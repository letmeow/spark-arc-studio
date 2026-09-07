import os
import shutil
import tempfile

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from core.auth import get_current_user
from core.file_ingest.service import (
    ImportTextEmptyError,
    UnsupportedImportFormatError,
    get_capabilities_payload,
)
from core.project_settings import (
    ATTACHMENT_CHUNK_TOKENS_DEFAULT,
    ATTACHMENT_CHUNK_TOKENS_MAX,
    ATTACHMENT_CHUNK_TOKENS_MIN,
    get_attachment_chunk_tokens,
    set_attachment_chunk_tokens,
)
from core.request_context import get_current_project_name, resolve_project_name


import_router = APIRouter()


def _resolve_import_model_limits(user_id: str) -> tuple[str | None, int]:
    """复用当前主用途模型，统一附件 token 估算口径与总量上限。"""
    try:
        from llm.agen_matchbox import DEFAULT_USAGE_KEY, matchbox
        from llm.agen_matchbox.models import DEFAULT_MAX_CONTEXT_TOKENS

        detail = matchbox().get_user_selection_detail(str(user_id), usage_key=DEFAULT_USAGE_KEY)
        current = detail.get("current") if isinstance(detail, dict) else None
        model_name = current.get("model_name") if isinstance(current, dict) else None
        normalized = str(model_name or "").strip()
        raw_limit = current.get("max_context_tokens") if isinstance(current, dict) else None
        max_context_tokens = int(raw_limit or DEFAULT_MAX_CONTEXT_TOKENS)
        if max_context_tokens <= 0:
            max_context_tokens = DEFAULT_MAX_CONTEXT_TOKENS
        return normalized or None, max_context_tokens
    except Exception:
        from llm.agen_matchbox.models import DEFAULT_MAX_CONTEXT_TOKENS

        return None, DEFAULT_MAX_CONTEXT_TOKENS


@import_router.get("/api/import/capabilities")
async def get_import_capabilities(user: dict = Depends(get_current_user)):
    return get_capabilities_payload()


# ==================== 附件分片大小（滑动窗口）配置 ====================

class _AttachmentChunkTokensUpdate(BaseModel):
    projectName: str | None = None
    chunkTokens: int


@import_router.get("/api/import/chunk-tokens")
async def get_chunk_tokens_setting(
    projectName: str | None = None,
    user: dict = Depends(get_current_user),
):
    """读取项目级"附件分片 token 上限"。

    前端在打开附件面板时调用，用于在滑动窗口大小输入框中回填当前值。
    无项目上下文时返回默认值（不视为错误，避免新建项目卡住 UI）。
    """
    user_id = str(user['user_id'])
    project_name = resolve_project_name(get_current_project_name(), projectName)
    if not project_name:
        return {
            "success": True,
            "chunkTokens": ATTACHMENT_CHUNK_TOKENS_DEFAULT,
            "min": ATTACHMENT_CHUNK_TOKENS_MIN,
            "max": ATTACHMENT_CHUNK_TOKENS_MAX,
            "default": ATTACHMENT_CHUNK_TOKENS_DEFAULT,
        }
    return {
        "success": True,
        "chunkTokens": get_attachment_chunk_tokens(user_id, project_name),
        "min": ATTACHMENT_CHUNK_TOKENS_MIN,
        "max": ATTACHMENT_CHUNK_TOKENS_MAX,
        "default": ATTACHMENT_CHUNK_TOKENS_DEFAULT,
    }


@import_router.post("/api/import/chunk-tokens")
async def update_chunk_tokens_setting(
    data: _AttachmentChunkTokensUpdate,
    user: dict = Depends(get_current_user),
):
    """更新项目级"附件分片 token 上限"。

    入参超出 [MIN, MAX] 区间会被自动 clamp 到边界，返回的 chunkTokens
    永远是最终落盘的合法值。
    """
    user_id = str(user['user_id'])
    project_name = resolve_project_name(get_current_project_name(), data.projectName)
    if not project_name:
        return JSONResponse(status_code=400, content={"error": "缺少项目名称"})
    persisted = set_attachment_chunk_tokens(user_id, project_name, data.chunkTokens)
    return {
        "success": True,
        "chunkTokens": persisted,
        "min": ATTACHMENT_CHUNK_TOKENS_MIN,
        "max": ATTACHMENT_CHUNK_TOKENS_MAX,
        "default": ATTACHMENT_CHUNK_TOKENS_DEFAULT,
    }


@import_router.post("/api/import/parse")
async def parse_import_file(
    file: UploadFile = File(...),
    chunkTokens: int | None = Form(None),
    projectName: str | None = Form(None),
    user: dict = Depends(get_current_user),
):
    user_id = str(user['user_id'])
    project_name = resolve_project_name(get_current_project_name(), projectName)
    if not project_name:
        return JSONResponse(status_code=400, content={"error": "当前未选择项目，无法上传聊天附件"})

    suffix = os.path.splitext(file.filename or "")[1].lower()
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)

    try:
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        estimate_model, max_context_tokens = _resolve_import_model_limits(user_id)
        # chunk_tokens 优先级：前端显式传值 > 项目级配置 > 默认值。
        # 不再硬编码 30000——项目可在附件面板里调整滑动窗口大小并持久化。
        from core.project_settings import _coerce_attachment_chunk_tokens
        if chunkTokens is None:
            chunk_tokens = get_attachment_chunk_tokens(user_id, project_name)
        else:
            chunk_tokens = _coerce_attachment_chunk_tokens(chunkTokens)

        from agents.utility_agent import UtilityAgent

        prepared = await run_in_threadpool(
            UtilityAgent.prepare_chat_attachment,
            user_id=user_id,
            project_name=project_name,
            file_path=tmp_path,
            filename=file.filename or "",
            chunk_tokens=chunk_tokens,
            estimate_model=estimate_model,
            max_context_tokens=max_context_tokens,
        )

        if not prepared.attachment_id:
            return JSONResponse(status_code=500, content={"error": "聊天附件保存失败：attachment_id 为空"})

        # 附件落盘成功后，按统一策略后台异步补一轮语义增量更新。
        # 仅在“项目语义检索开关 + 附件入索开关”同时开启时触发；
        # 触发本身是非阻塞的：内部会创建 daemon 线程，构建中再来一次会自动排队补刷。
        try:
            from core.project_settings import (
                is_attachment_index_enabled,
                is_semantic_search_enabled,
            )

            if (
                is_semantic_search_enabled(user_id, project_name)
                and is_attachment_index_enabled(user_id, project_name)
            ):
                from agents.vector_index import VectorIndexService

                def _kick_off_attachment_refresh() -> None:
                    try:
                        VectorIndexService(user_id, project_name).ensure_background_build_started(
                            check_freshness=True
                        )
                    except Exception as exc:
                        print(f"[import] Background attachment indexing failed: {exc}")

                await run_in_threadpool(_kick_off_attachment_refresh)
        except Exception as exc:
            # 任何意外都不影响附件上传成功的响应。
            print(f"[import] Exception triggering attachment semantic index: {exc}")

        return {
            "success": True,
            "attachment_id": prepared.attachment_id,
            "filename": prepared.parsed.filename,
            "source_format": prepared.parsed.source_format,
            # 响应瘦身：全文/全分片正文不再回传前端。前端只持有 attachment_id
            # 引用（见 fileImportService.ts），正文由后端按 id 从磁盘按需注入；
            # 全量回传是上传链路最大的下行浪费（七堇年实测 750KB，占总耗时大头）。
            "sections": [
                {
                    "section_type": section.section_type,
                    "title": section.title,
                    "estimated_tokens": section.estimated_tokens,
                }
                for section in prepared.parsed.sections
            ],
            "warnings": [
                {
                    "code": warning.code,
                    "message": warning.message,
                }
                for warning in prepared.parsed.warnings
            ],
            "metadata": prepared.parsed.metadata,
            # chunks 只回传元信息（窗口号/字符数/token 数/上一片尾），不回传 text：
            # 正文已落盘，读窗/注入一律按 id 从磁盘取；回传 text 是第二大浪费。
            "chunks": [
                {
                    "index": chunk.index,
                    "total": chunk.total,
                    "char_count": chunk.char_count,
                    "estimated_tokens": chunk.estimated_tokens,
                    "previous_tail": chunk.previous_tail,
                }
                for chunk in prepared.chunks
            ],
            "chunk_info": prepared.chunk_info,
            "chunk_count": prepared.chunk_count,
            "is_partial": prepared.is_partial,
            "is_oversized": prepared.is_oversized,
            "max_context_tokens": max_context_tokens,
        }
    except UnsupportedImportFormatError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except ImportTextEmptyError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
