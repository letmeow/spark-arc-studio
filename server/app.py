import os
import json
import warnings
import logging

from llm.matchbox_adapter import configure_sparkarc_matchbox_environment

configure_sparkarc_matchbox_environment()

# ═══════════════════════════════════════════════════════════════════════════
# 第三方库警告 / 日志抑制（必须在所有第三方库导入之前）
# ═══════════════════════════════════════════════════════════════════════════

# ── 1. 环境变量 ──────────────────────────────────────────────────────────
# TRANSFORMERS_NO_ADVISORY_WARNINGS: 抑制 "PyTorch was not found" 等 print
# PYTHONWARNINGS: 确保子进程（uvicorn --reload）也继承 DeprecationWarning 抑制
# HF_HUB_VERBOSITY: 让 huggingface_hub 自身初始化时直接只放行 ERROR
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
os.environ.setdefault("PYTHONWARNINGS", "ignore::DeprecationWarning")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

from core.runtime_cache import configure_runtime_cache_environment
from core.server_runtime import build_local_server_urls, resolve_server_binding

configure_runtime_cache_environment()

# ── 2. warnings 过滤 ────────────────────────────────────────────────────
# SWIG 生成的 C 扩展在 Python 3.12+ 触发 DeprecationWarning（SwigPyPacked /
# SwigPyObject / swigvarlink 无 __module__ 属性），属于上游问题，静默处理
warnings.filterwarnings("ignore", message=".*SwigPy.*", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*swigvarlink.*", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*builtin type.*has no __module__.*", category=DeprecationWarning)
# transformers 的 FutureWarning / UserWarning（模型类型不匹配等），在
# estimate_tokens.py 的 catch_warnings 中也有局部处理
warnings.filterwarnings("ignore", category=FutureWarning, module="transformers")
warnings.filterwarnings("ignore", message=".*You are using a model of type.*", module="transformers")

# ── 3. logging 过滤 ──────────────────────────────────────────────────────
# transformers / huggingface_hub / torch 的 INFO/WARNING 日志噪音极大，
# 只保留 ERROR 级别。使用 root Filter 而非 setLevel，因为子 logger
# 可能在导入后才创建，setLevel 无法覆盖后创建的子 logger
class _ThirdPartyLogFilter(logging.Filter):
    _SUPRESSED_PREFIXES = ("transformers", "transformers_modules", "huggingface_hub", "torch")
    def filter(self, record):
        for p in self._SUPRESSED_PREFIXES:
            if record.name == p or record.name.startswith(p + "."):
                return record.levelno >= logging.ERROR
        return True
logging.getLogger().addFilter(_ThirdPartyLogFilter())

# ═══════════════════════════════════════════════════════════════════════════
# 以下为正常业务导入
# ═══════════════════════════════════════════════════════════════════════════
import wsproto
import asyncio
import httpx
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from starlette.datastructures import Headers
from starlette.staticfiles import NotModifiedResponse

# 自定义 uvicorn 日志配置，为 INFO 日志添加时间戳（精确到秒）
import copy
import logging.config
from uvicorn.config import LOGGING_CONFIG
UVICORN_LOG_CONFIG = copy.deepcopy(LOGGING_CONFIG)
UVICORN_LOG_CONFIG["formatters"]["default"]["fmt"] = "%(asctime)s - %(levelprefix)s %(message)s"
UVICORN_LOG_CONFIG["formatters"]["default"]["datefmt"] = "%Y-%m-%d %H:%M:%S"
UVICORN_LOG_CONFIG["formatters"]["access"]["fmt"] = '%(asctime)s - %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
UVICORN_LOG_CONFIG["formatters"]["access"]["datefmt"] = "%Y-%m-%d %H:%M:%S"
# 立即应用配置，确保 CLI 模式 (uvicorn app:app) 也能生效
logging.config.dictConfig(UVICORN_LOG_CONFIG)


def _suppress_noisy_library_logger(name: str) -> None:
    """
    对噪音较大的第三方 logger 做强制降噪。

    说明：
    1. 部分库会在导入时自己挂 StreamHandler，绕过 root logger filter。
    2. 这里直接把命名空间 logger 降到 ERROR，并清掉自带 handler，
       让后续子 logger 继承这一层级，避免 INFO/WARNING 污染控制台。
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.ERROR)
    logger.handlers.clear()


for _logger_name in ("transformers", "transformers_modules", "huggingface_hub", "torch"):
    _suppress_noisy_library_logger(_logger_name)

# 降噪第三方日志
logging.getLogger("docket.worker").setLevel(logging.WARNING)
logging.getLogger("mcp.server.streamable_http_manager").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

SERVER_HOST, SERVER_PORT = resolve_server_binding()
SERVER_URL, SERVER_HEALTH_URL = build_local_server_urls(SERVER_HOST, SERVER_PORT)


def _run_startup_migrations() -> None:
    from core.auto_migrate import run_auto_migrations
    try:
        run_auto_migrations()
    except Exception as e:
        print(f"❌ Database migration failed. Do not modify the database schema externally. Error: {e}")
        raise e


def _repair_stale_auto_write_states() -> None:
    """启动时修复上次进程退出遗留的自动写作运行态。"""
    from agents.auto_write_state import repair_stale_auto_write_states

    repaired = repair_stale_auto_write_states()
    if repaired:
        print(f"  [auto-write] 已修复 {repaired} 个中断任务状态", flush=True)



def _has_branch_migrations(branch_label: str) -> bool:
    from core.migration_specs import get_version_dir

    versions_dir = get_version_dir(branch_label)
    if not versions_dir.is_dir():
        return False
    for file_path in versions_dir.glob("*.py"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            if "revision" in content:
                return True
        except Exception:
            continue
    return False


def _ensure_migration_history() -> None:
    missing = []
    for label in ("users", "llm"):
        if not _has_branch_migrations(label):
            missing.append(label)
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            "未检测到迁移历史: "
            f"{joined}. 请先运行 gen_migration.py 生成 base 迁移，然后再启动服务。"
        )


# _ensure_migration_history()
# _run_startup_migrations() # Moved to lifespan to avoid double execution and allow proper logging

# 导入所有 APIRouter
from core.auth import auth_router
from core.routes_admin import admin_router
from core.routes_admin_config import admin_config_router
from core.routes_import import import_router
from core.routes_tags import tags_router
from core.routes_tos import tos_router
from core.routes_redeem import redeem_router
from core.routes_feedback import feedback_router
from core.request_context import reset_current_locale, set_current_locale
from story.routes_story import story_router
from agents.routes import agents_router  # 使用拆分后的新模块
from agents.routes.auto_write import auto_write_router
from agents.routes.semantic_search_routes import semantic_search_router
from agents.routes.graphrag_routes import graphrag_router
from llm.routes_llm import llm_router
from llm.agen_matchbox import QuotaExceededError, CreditBalanceExceededError

# MCP 服务器：统一入口与控制兼容入口
from mcp_server.unified import mcp as mcp_unified_inst
from mcp_server.spark_control.server import mcp as mcp_control_inst
from mcp_server.shared.host import McpAuthMiddleware, create_mcp_http_app

# ============================================
# MCP 应用配置（使用 HTTP 传输）
# ============================================
# 创建统一入口和旧控制入口的 Streamable HTTP 应用。
_mcp_app = create_mcp_http_app(mcp_unified_inst)
_mcp_control_app = create_mcp_http_app(mcp_control_inst)

# 两个入口都使用同一个共享鉴权中间件；旧控制入口仅用于兼容已有客户端。
_mcp_app_with_auth = McpAuthMiddleware(_mcp_app)
_mcp_control_app_with_auth = McpAuthMiddleware(_mcp_control_app)


# ============================================
# 生命周期管理（合并 FastAPI 和 MCP 的 lifespan）
# ============================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理。
    
    合并 FastAPI 和 MCP 的生命周期：
    - FastAPI 的启动检查和预热
    - MCP 的 session manager 初始化
    """
    # ========== 启动阶段 ==========
    # 启动即完成数据库迁移，避免后续逻辑持锁或延迟初始化
    print("🛠️  Checking and running database migrations...", flush=True)
    _run_startup_migrations()
    print("✅ Database migration complete", flush=True)

    # 清理因进程意外退出而遗留的孤儿 running 状态
    # 若 auto_write_state.json 中记录 status=running/chapter_paused，
    # 说明上次是被强行杀进程，此时实际上没有任何写作线程存在，
    # 需将状态修正为 interrupted，防止前端误判为写作中并弹出全局遮罩。
    try:
        _repair_stale_auto_write_states()
    except Exception as _e:
        print(f"⚠️ Failed to clean up stale auto-write states (non-fatal): {_e}", flush=True)

    try:
        from agents.chat_manager import ChatManager

        repaired_chat_streams = ChatManager.mark_interrupted_stream_messages()
        if repaired_chat_streams:
            print(f"⚠️ Marked {repaired_chat_streams} interrupted chat stream(s)", flush=True)
    except Exception as _e:
        print(f"⚠️ Failed to repair stale chat streams (non-fatal): {_e}", flush=True)

    # 检查必要组件
    server_root = os.path.dirname(os.path.abspath(__file__))
    arc_template_path = os.path.join(server_root, 'ARC_AI_Format.arc')
    
    if not os.path.exists(arc_template_path):
        error_msg = f"\n❌ 关键文件缺失: {arc_template_path}\n此文件是系统的核心 AI 剧本格式样板，必须存在于 server 目录下。\n请恢复该文件后重新启动。"
        print(error_msg)
        raise FileNotFoundError(error_msg)

    # 同时管理统一入口和兼容入口的 MCP session manager。
    # 统一入口内部还会管理两个业务子服务的生命周期。
    from contextlib import AsyncExitStack
    async with AsyncExitStack() as _mcp_stack:
        await _mcp_stack.enter_async_context(_mcp_app.lifespan(app))
        print("✅ MCP Server initialized", flush=True)
        await _mcp_stack.enter_async_context(_mcp_control_app.lifespan(app))
        print("✅ Spark Control MCP Server initialized", flush=True)
        # 显式初始化 LLM Manager（确保 migration 已完成且释放了 DB 锁）
        # 关键说明：
        # 1. 这里必须只做 Matchbox 的“轻启动”硬依赖初始化，目标是尽快放行 /health 与 startup complete。
        # 2. Matchbox 的重运行时（gateway / tracked_model / langchain_openai）不应再阻塞这个同步阶段。
        # 3. 这些重依赖会在 initialize_matchbox() 返回后立刻提交后台预热，也就是“启动同时异步预热”，
        #    而不是等首个用户请求到了才开始加载。
        try:
            from llm.agen_matchbox import warmup_matchbox_runtime
            from llm.matchbox_adapter import initialize_sparkarc_matchbox
            print("📦 Initializing Matchbox gateway...", flush=True)
            initialize_sparkarc_matchbox(ensure_defaults=True)
            warmup_matchbox_runtime(blocking=False)
            print("⚙️ Matchbox runtime warm-up submitted in background", flush=True)
        except Exception as e:
            print(f"⚠️ Matchbox gateway init notice: {e}", flush=True)

        # 异步预热分词器（后台线程，不阻塞启动）
        try:
            from llm.agen_matchbox.estimate_tokens import warmup_tokenizers
            warmup_tokenizers(blocking=False)
            print("⚙️ Tokenizer warm-up submitted in background", flush=True)
        except Exception as e:
            print(f"⚠️ Tokenizer warm-up submission failed (non-fatal): {e}", flush=True)

        # 恢复上次已持久化的本地嵌入开关，避免每次重启都需要手动重新开启。
        try:
            from agents.vector_index.local_embedding import restore_local_embedding_service_from_settings

            if restore_local_embedding_service_from_settings():
                print("⚙️ 已根据持久化设置提交本地嵌入服务启动", flush=True)
        except Exception as e:
            print(f"⚠️ 本地嵌入服务持久化恢复失败（不影响主服务启动）：{e}", flush=True)

        # 应用启动后预热
        asyncio.create_task(warm_up())
        try:
            from core.system_tray import launch_tray_helper_after_health_check

            server_root = os.path.dirname(os.path.abspath(__file__))
            asyncio.create_task(
                launch_tray_helper_after_health_check(
                    server_root=server_root,
                    server_url=SERVER_URL,
                    health_url=SERVER_HEALTH_URL,
                )
            )
        except Exception as e:
            print(f"⚠️ System tray assistant launch failed (non-fatal): {e}", flush=True)
        print("🚀 Server started successfully!", flush=True)
        
        yield  # ========== 应用运行中 ==========
    
    # ========== 关闭阶段 ==========
    try:
        from agents.vector_index.local_embedding import stop_local_embedding_service

        stop_local_embedding_service()
    except Exception as e:
        print(f"⚠️ Local embedding shutdown failed (non-fatal): {e}", flush=True)
    try:
        from llm.agen_matchbox import reset_matchbo
        reset_matchbo()
    except Exception:
        pass
    print("🛑 Shutting down server...", flush=True)


# ============================================
# 创建 FastAPI 应用
# ============================================
app = FastAPI(
    title="SparkArc API",
    description="SparkArc 后端服务",
    version="2.0.0",
    lifespan=lifespan,  # 使用合并后的 lifespan
    docs_url=None,
    redoc_url=None,
    openapi_url=None
)


SPA_HTML_CACHE_CONTROL = "no-cache"
FINGERPRINTED_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
ROOT_STATIC_CACHE_CONTROL = "public, max-age=604800"


def _normalize_dist_relative_path(relative_path: str) -> str:
    return str(relative_path or "").replace("\\", "/").lstrip("/")


def _get_dist_cache_control(relative_path: str) -> str:
    normalized = _normalize_dist_relative_path(relative_path)
    if not normalized or normalized == "index.html":
        return SPA_HTML_CACHE_CONTROL
    if normalized.startswith("assets/"):
        return FINGERPRINTED_ASSET_CACHE_CONTROL
    return ROOT_STATIC_CACHE_CONTROL


def _build_cached_file_response(
    request: Request,
    file_path: str,
    *,
    cache_path: str,
):
    stat_result = os.stat(file_path)
    response = FileResponse(file_path, stat_result=stat_result)
    response.headers["Cache-Control"] = _get_dist_cache_control(cache_path)
    if StaticFiles.is_not_modified(None, response.headers, request.headers):
        return NotModifiedResponse(response.headers)
    return response


class CachedStaticFiles(StaticFiles):
    def __init__(self, *args, cache_path_prefix: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.cache_path_prefix = _normalize_dist_relative_path(cache_path_prefix)

    def file_response(self, full_path, stat_result, scope, status_code: int = 200):
        request_headers = Headers(scope=scope)
        response = FileResponse(full_path, status_code=status_code, stat_result=stat_result)
        relative_path = os.path.relpath(str(full_path), str(self.directory or ""))
        cache_path = _normalize_dist_relative_path(
            os.path.join(self.cache_path_prefix, relative_path)
            if self.cache_path_prefix
            else relative_path
        )
        response.headers["Cache-Control"] = _get_dist_cache_control(cache_path)
        if self.is_not_modified(response.headers, request_headers):
            return NotModifiedResponse(response.headers)
        return response


@app.middleware("http")
async def locale_context_middleware(request: Request, call_next):
    locale = request.headers.get("X-Spark-Locale") or request.headers.get("Accept-Language")
    token = set_current_locale(locale)
    try:
        return await call_next(request)
    finally:
        reset_current_locale(token)


@app.exception_handler(QuotaExceededError)
async def handle_quota_exceeded(_: Request, exc: QuotaExceededError):
    return JSONResponse(
        status_code=429,
        content={
            "success": False,
            "message": str(exc),
            "error": "quota_exceeded",
        },
    )


@app.exception_handler(CreditBalanceExceededError)
async def handle_credit_balance_exceeded(_: Request, exc: CreditBalanceExceededError):
    return JSONResponse(
        status_code=429,
        content={
            "success": False,
            "message": str(exc),
            "error": "credit_balance_exceeded",
        },
    )

# ⚠️ 警示与性能避坑指南：
# 1. 严禁在此处引入或使用 FastAPI 的 GZipMiddleware，这会在应用层拦截并缓冲（Buffering）所有流式响应，
#    导致项目核心的大模型流式生成（Chat NDJSON、SSE 语义流等）出现卡顿或无法实时输出的致命异常。
# 2. 生产环境中，在网络的任意一层（如 Nginx、CDN/Cloudflare）如果误对流式接口启用了 Gzip 压缩，
#    都会因为压缩算法的块缓冲机制（缺乏 Z_SYNC_FLUSH）或代理缓存而破坏流式输出。
# 3. 特别注意：本项目 Chat 主链路使用的 'application/x-ndjson' 属于非标准流式媒体类型，极易被 CDN/代理 误认为普通 JSON 响应而强行执行 Gzip 压缩。
# 4. 推荐方案：生产环境推荐前置 Nginx 等反代服务，对静态资源配置 Gzip，但对 API 流式通道配置 gzip off; proxy_buffering off;
#    且在后端的 StreamingResponse 中统一追加 "X-Accel-Buffering: no" 响应头，强制避开所有中介代理的缓冲拦截。
# 5. 若直接裸 Uvicorn 暴露后端端口：Uvicorn/FastAPI 不会自动压缩静态资源；构建报告里的 gzip 体积仅代表“如果开启静态压缩后的估算传输量”。
#    如需让裸 Uvicorn 用户也享受静态压缩，应只对 client/dist 静态文件做预压缩/编码协商，严禁复用到 /api 流式接口。

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册所有业务路由
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(admin_config_router)
app.include_router(import_router)
app.include_router(tags_router)
app.include_router(tos_router)
app.include_router(redeem_router)
app.include_router(feedback_router)
app.include_router(story_router)
app.include_router(agents_router)
app.include_router(auto_write_router)
app.include_router(semantic_search_router)
app.include_router(graphrag_router)
app.include_router(llm_router)

# 系统相关路由
from core.notice_mgr import get_latest_notice, get_notices, add_notice, update_notice, delete_notice
from pydantic import BaseModel
from core.auth import require_admin, get_optional_user, get_current_user, user_db
from sqlalchemy import select
from core.models import User

class NoticeCreateRequest(BaseModel):
    title: str
    content: str

class NoticeUpdateRequest(BaseModel):
    notice_id: str
    title: str
    content: str

@app.get("/api/system/notice")
async def get_notice(current_user: dict = Depends(get_optional_user)):
    """获取最新系统公告，附带当前用户的已读状态和首次登录标记"""
    notice = get_latest_notice()
    is_read = True
    is_first_login = False

    if notice and current_user:
        try:
            with user_db._session() as s:
                user = s.execute(select(User).where(User.id == current_user['user_id'])).scalar_one_or_none()
                if user:
                    is_read = (user.last_read_notice_id == notice['id'])
                    is_first_login = (user.first_login is None or user.first_login != 0)
        except Exception:
            pass
    elif current_user and not notice:
        # 无公告时视为已读
        is_read = True
        try:
            with user_db._session() as s:
                user = s.execute(select(User).where(User.id == current_user['user_id'])).scalar_one_or_none()
                if user:
                    is_first_login = (user.first_login is None or user.first_login != 0)
        except Exception:
            pass

    result = {"success": True, "notice": notice, "is_read": is_read, "is_first_login": is_first_login}
    return result

@app.get("/api/system/notice/history")
async def get_notice_history():
    """获取公告历史"""
    notices = get_notices()
    return {"success": True, "notices": notices}

@app.post("/api/admin/notice")
async def create_new_notice(request: NoticeCreateRequest, admin_user: dict = Depends(require_admin)):
    """创建公告（管理员功能）"""
    try:
        notice = add_notice(request.title, request.content)
        return {"success": True, "notice": notice}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/notice")
async def update_existing_notice(request: NoticeUpdateRequest, admin_user: dict = Depends(require_admin)):
    """更新公告（管理员功能）"""
    try:
        success = update_notice(request.notice_id, request.title, request.content)
        if success:
            return {"success": True}
        raise HTTPException(status_code=404, detail="公告不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class NoticeReadRequest(BaseModel):
    notice_id: str

@app.post("/api/user/notice-read")
async def mark_notice_read(request: NoticeReadRequest, current_user: dict = Depends(get_current_user)):
    """标记公告为已读（不校验公告是否存在，静默处理已删除公告）"""
    user_id = current_user['user_id']
    try:
        with user_db._session() as s:
            user = s.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
            if not user:
                return JSONResponse(status_code=404, content={"success": False, "message": "用户不存在"})
            user.last_read_notice_id = request.notice_id
            s.add(user)
            s.commit()
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})

@app.delete("/api/admin/notice/{notice_id}")
async def delete_existing_notice(notice_id: str, admin_user: dict = Depends(require_admin)):
    """删除公告（管理员功能）"""
    try:
        success = delete_notice(notice_id)
        if success:
            return {"success": True}
        raise HTTPException(status_code=404, detail="公告不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 健康检查
@app.get("/health", response_class=PlainTextResponse)
async def health_check():
    return "sparkarc-ok"

# 挂载 spark_control MCP Server（远程操控：聊天链路 + 导演调度 + 查询工具）
# 子路径必须先于 /api/mcp 父路径挂载，否则会被父 Mount 提前匹配。
app.mount("/api/mcp/control", _mcp_control_app_with_auth)

# 挂载 MCP Server（带鉴权中间件）
# 挂载到 /api/mcp/，确保尾部斜杠正确处理
# 注意：Starlette mount 要求挂载路径不带尾部斜杠，但 MCP 端点需要尾部斜杠
app.mount("/api/mcp", _mcp_app_with_auth)


# 处理不带尾部斜杠的 MCP 请求，重定向或代理到正确的端点
from starlette.responses import RedirectResponse

@app.api_route("/api/mcp", methods=["GET", "POST", "DELETE", "OPTIONS"])
async def mcp_redirect(request: Request):
    """将 /api/mcp 重定向到 /api/mcp/ 以确保 MCP 客户端兼容性"""
    # 构建带尾部斜杠的 URL
    url = request.url.replace(path="/api/mcp/")
    return RedirectResponse(url=str(url), status_code=307)

@app.api_route("/api/mcp/control", methods=["GET", "POST", "DELETE", "OPTIONS"])
async def mcp_control_redirect(request: Request):
    """将 /api/mcp/control 重定向到 /api/mcp/control/ 以确保 MCP 客户端兼容性"""
    url = request.url.replace(path="/api/mcp/control/")
    return RedirectResponse(url=str(url), status_code=307)

async def warm_up():
    """启动后预热，确保重依赖在后台预热完毕"""
    # 延迟 0.1s 确保 lifespan complete
    await asyncio.sleep(0.1)

    # 1. 预热数据库
    try:
        from sqlalchemy import select
        from core.auth import user_db
        with user_db._session() as s:
            s.execute(select(1)).scalar()
        print("✅ Database warm-up complete (connection pool established)", flush=True)
    except Exception as e:
        print(f"⚠️ Database warm-up failed (non-fatal): {e}", flush=True)

    # 2. 预热分词器和模型估算
    try:
        from core.file_ingest.chunking import estimate_text_tokens as estimate_tokens
        estimate_tokens("warmup ping")
        print("✅ Tokenizer warm-up complete", flush=True)
    except Exception as e:
        print(f"⚠️ Tokenizer warm-up failed (non-fatal): {e}", flush=True)

# 获取前端静态文件目录
current_dir = os.path.dirname(os.path.abspath(__file__))
dist_dir = os.path.join(os.path.dirname(current_dir), 'client', 'dist')

# 静态文件服务
if os.path.exists(dist_dir):
    assets_dir = os.path.join(dist_dir, "assets")
    if os.path.exists(assets_dir):
        app.mount(
            "/assets",
            CachedStaticFiles(directory=assets_dir, cache_path_prefix="assets"),
            name="assets",
        )
    
    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        """服务单页应用 (SPA)"""
        # 跳过 API 和健康检查路由
        if full_path.startswith(("api/", "health")):
            return {"error": "Not found"}, 404
        
        # 检查请求的路径是否是静态文件
        file_path = os.path.join(dist_dir, full_path)
        if full_path and os.path.isfile(file_path):
            return _build_cached_file_response(
                request,
                file_path,
                cache_path=full_path,
            )

        # 提供 SPA 的主入口 index.html
        index_path = os.path.join(dist_dir, 'index.html')
        if os.path.exists(index_path):
            return _build_cached_file_response(
                request,
                index_path,
                cache_path='index.html',
            )
        return {"message": "SPA not found. Please build the client first."}
else:
    @app.get("/")
    async def root():
        return {
            "message": "SparkArc API",
            "version": "2.0.0",
            "docs": "/docs",
            "redoc": "/redoc",
            "status": "running",
            "note": "前端文件未找到，请先构建 client"
        }


if __name__ == '__main__':
    import uvicorn
    from core.system_tray import read_bool_env, running_in_embedded_python

    def _run_uvicorn_server(*, enable_reload: bool) -> None:
        uvicorn.run(
            "app:app",
            host=SERVER_HOST,
            port=SERVER_PORT,
            reload=enable_reload,
            log_config=UVICORN_LOG_CONFIG,
            reload_excludes=[
                "test",
                "test/*",
                "*.py[co]",
                "__pycache__",
                ".git",
                "*.db",
                "*.db-journal",
                "*.db-wal",
                "data/*",
                "llm/agen_matchbox/*.db*",
                "alembic/versions/*",
                "alembic/versions/**",
            ],
            access_log=True,
            log_level="info",
            ws='wsproto'  # 切换到 wsproto 以避开 websockets 14.0+ 的弃用警告
        )

    default_reload = not running_in_embedded_python()
    enable_reload = read_bool_env("SPARKARC_SERVER_RELOAD", default=default_reload)
    os.environ["SPARKARC_SERVER_RELOAD_ACTIVE"] = "1" if enable_reload else "0"
    _run_uvicorn_server(enable_reload=enable_reload)

