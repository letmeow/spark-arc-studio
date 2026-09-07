from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from typing import Optional
import io
import json
import os
import re
import shutil
import time
import zipfile
from datetime import datetime
from urllib.parse import quote

from core.auth import get_current_user, get_optional_user
from core.character_store import CHARACTER_STORE_FILENAME
from core.request_context import normalize_project_name
from core.utils import (
    ensure_project_directory,
    ensure_project_stories_directory,
    ensure_project_worldview_and_character_settings,
    get_project_path,
    get_project_stories_path,
    get_user_projects_root,
)
from core.models import UserInfoSession, ProjectVersion, Share
from agents.chat_manager import ChatManager
from agents.routes.chat_task import purge_project_tasks
from agents.project_background_builds import cancel_project_background_builds

project_router = APIRouter()

_STYLE_EXPORT_SNAPSHOT_PATH = ".sparkarc/exported_style_profile.json"
_STYLE_EXPORT_SNAPSHOT_KIND = "sparkarc.project_style_snapshot"
_STYLE_EXPORT_SNAPSHOT_VERSION = 2


def _cancel_project_background_builds(
    user_id: str,
    project_name: str,
    *,
    vector_wait_timeout: float = 4.0,
    graph_wait_timeout: float = 2.0,
    auto_write_wait_timeout: float = 4.0,
) -> list[str]:
    """项目目录变更前，统一停止所有可能读写该项目的后台任务。"""
    return cancel_project_background_builds(
        user_id,
        project_name,
        vector_wait_timeout=vector_wait_timeout,
        graph_wait_timeout=graph_wait_timeout,
        auto_write_wait_timeout=auto_write_wait_timeout,
    )


def _remove_project_directory_with_retries(user_id: str, project_name: str, project_path: str) -> None:
    """Windows 下索引文件句柄释放可能略有延迟，删除时做短重试。"""
    last_exc: Exception | None = None
    for attempt in range(8):
        if not os.path.exists(project_path):
            return
        try:
            shutil.rmtree(project_path)
            return
        except (PermissionError, OSError) as exc:
            last_exc = exc
            _cancel_project_background_builds(
                user_id,
                project_name,
                vector_wait_timeout=0.5,
                graph_wait_timeout=0.0,
                auto_write_wait_timeout=0.0,
            )
            if attempt < 7:
                time.sleep(0.25 * (attempt + 1))
    if last_exc:
        raise last_exc

class ProjectCreate(BaseModel):
    projectName: str
    workspaceMode: Optional[str] = None

class ProjectRename(BaseModel):
    newName: str

@project_router.get('/api/projects')
async def get_projects(user: Optional[dict] = Depends(get_optional_user)):
    """列出当前用户的所有项目"""
    try:
        if not user:
            return []
        user_id = str(user['user_id'])
        projects_root = get_user_projects_root(user_id)
        if not os.path.exists(projects_root):
            os.makedirs(projects_root)
            return []
        projects = [
            name for name in os.listdir(projects_root)
            if os.path.isdir(os.path.join(projects_root, name))
            # 防御：过滤掉被 ensure_project_* 意外创建的不完整目录
            # 合法项目至少有单文件角色仓库（注册/创建时初始化）
            and os.path.isfile(os.path.join(projects_root, name, 'chr', CHARACTER_STORE_FILENAME))
        ]
        return sorted(projects)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "message": f"获取项目列表失败: {exc}"})


@project_router.post('/api/projects')
async def create_project(data: ProjectCreate, user: dict = Depends(get_current_user)):
    """创建新项目并初始化目录结构"""
    try:
        user_id = str(user['user_id'])
        project_name = normalize_project_name(data.projectName)
        if not project_name:
            return JSONResponse(status_code=400, content={"success": False, "message": "项目名称不能为空"})
        workspace_mode = "novel" if data.workspaceMode == "novel" else "script"

        project_path = get_project_path(user_id, project_name)
        if os.path.exists(project_path):
            return JSONResponse(status_code=409, content={"success": False, "message": "项目已存在"})

        ensure_project_directory(user_id, project_name)
        ensure_project_stories_directory(user_id, project_name)
        ensure_project_worldview_and_character_settings(user_id, project_name)

        # 同名重建兜底：目录是新目录，但 DB（聊天/版本/分享）与内存任务都以
        # (user_id, project_name) 为键。若旧项目删除时某类清理静默失败，
        # 新项目会直接继承旧聊天与旧分享。这里显式清零，保证新项目从空开始。
        try:
            cm = ChatManager(user_id=user_id, project_name=project_name)
            cm.clear_project_sessions()
        except Exception as e:
            print(f"Failed to clear stale chat history on project create: {e}")
        try:
            with UserInfoSession() as session:
                session.query(ProjectVersion).filter_by(
                    user_id=int(user_id),
                    project_name=project_name,
                ).delete()
                session.query(Share).filter_by(
                    user_id=int(user_id),
                    project_name=project_name,
                ).delete()
                session.commit()
        except Exception as e:
            print(f"Failed to clear stale version/share history on project create: {e}")
        try:
            purge_project_tasks(user_id, project_name)
        except Exception as e:
            print(f"Failed to purge stale chat tasks on project create: {e}")

        try:
            from core.project_settings import initialize_project_workspace_mode
            initialize_project_workspace_mode(user_id, project_name, workspace_mode)
        except Exception as e:
            print(f"Failed to initialize project workspace mode: {e}")

        # 按用户级默认配置初始化语义搜索开关
        try:
            from core.project_settings import get_default_semantic_enabled, set_project_setting
            if get_default_semantic_enabled(user_id):
                set_project_setting(user_id, project_name, "semantic_search_enabled", True)
        except Exception as e:
            print(f"Failed to initialize semantic search config: {e}")

        return {"success": True, "message": "项目创建成功", "workspaceMode": workspace_mode}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "message": f"项目创建失败: {exc}"})


@project_router.delete('/api/projects/{project_name}')
async def delete_project(project_name: str, user: dict = Depends(get_current_user)):
    """删除指定项目（含聊天记录、版本/分享记录与其快照文件）"""
    try:
        user_id = str(user['user_id'])
        project_path = get_project_path(user_id, project_name)
        if not os.path.exists(project_path):
            return JSONResponse(status_code=404, content={"success": False, "message": "项目不存在"})

        # 1. 删除项目文件目录前，优先中断可能持有文件句柄的后台构建。
        cancel_warnings = _cancel_project_background_builds(user_id, project_name)
        if cancel_warnings:
            return JSONResponse(
                status_code=409,
                content={"success": False, "message": "项目仍有后台任务未停止", "details": cancel_warnings},
            )
        _remove_project_directory_with_retries(user_id, project_name, project_path)

        # 2. 中断该项目的内存聊天任务，避免同名重建后被 recent-tasks/task-stream 误恢复。
        try:
            purge_project_tasks(user_id, project_name)
        except Exception as e:
            print(f"Failed to purge project chat tasks: {e}")

        # 3. 清除该项目所有聊天记录
        try:
            cm = ChatManager(user_id=user_id, project_name=project_name)
            cm.clear_project_sessions()
        except Exception as e:
            print(f"Failed to clear project chat history: {e}")

        # 4. 清除该项目所有版本记录及其快照文件
        try:
            with UserInfoSession() as session:
                versions = session.query(ProjectVersion).filter_by(
                    user_id=int(user_id),
                    project_name=project_name,
                ).all()
                snapshot_paths = [v.snapshot_path for v in versions if getattr(v, "snapshot_path", None)]
                for version in versions:
                    session.delete(version)
                shares = session.query(Share).filter_by(
                    user_id=int(user_id),
                    project_name=project_name,
                ).all()
                snapshot_paths.extend(s.snapshot_path for s in shares if getattr(s, "snapshot_path", None))
                for share in shares:
                    session.delete(share)
                session.commit()
            from story.presentation_manifest import remove_presentation_snapshot
            for snapshot_path in snapshot_paths:
                try:
                    if snapshot_path and os.path.exists(snapshot_path):
                        os.remove(snapshot_path)
                except OSError:
                    pass
                try:
                    remove_presentation_snapshot(snapshot_path)
                except Exception:
                    pass
        except Exception as e:
            print(f"Failed to clear project version history: {e}")

        # 4. 清理所有灵感的 project_links 引用，避免出现指向已删除项目的“鬼绑定”
        try:
            from mcp_server.spark_inspiration.logic import cleanup_project_from_all_inspirations
            cleanup_project_from_all_inspirations(user_id, project_name)
        except Exception as e:
            print(f"Failed to clear project inspiration bindings: {e}")

        return {"success": True, "message": "项目删除成功"}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "message": f"项目删除失败: {exc}"})


@project_router.put('/api/projects/{project_name}')
async def rename_project(project_name: str, data: ProjectRename, user: dict = Depends(get_current_user)):
    """重命名项目"""
    try:
        user_id = str(user['user_id'])
        new_name = normalize_project_name(data.newName)
        if not new_name:
            return JSONResponse(status_code=400, content={"success": False, "message": "项目名称不能为空"})

        old_path = get_project_path(user_id, project_name)
        if not os.path.exists(old_path):
            return JSONResponse(status_code=404, content={"success": False, "message": "项目不存在"})

        new_path = get_project_path(user_id, new_name)
        if os.path.exists(new_path):
            return JSONResponse(status_code=409, content={"success": False, "message": "目标项目名已存在"})

        cancel_warnings = _cancel_project_background_builds(user_id, project_name)
        if cancel_warnings:
            return JSONResponse(
                status_code=409,
                content={"success": False, "message": "项目仍有后台任务未停止", "details": cancel_warnings},
            )

        os.rename(old_path, new_path)

        # 更新版本记录中的项目名
        try:
            with UserInfoSession() as session:
                session.query(ProjectVersion).filter_by(
                    user_id=int(user_id),
                    project_name=project_name,
                ).update({"project_name": new_name})
                session.commit()
        except Exception as e:
            print(f"Failed to update version history: {e}")

        # 更新聊天记录中的项目名
        try:
            ChatManager.rename_project(user_id=user_id, old_name=project_name, new_name=new_name)
        except Exception as e:
            print(f"Failed to update chat history: {e}")

        # 同步更新灵感的 project_links，保证重命名后绑定关系不丢失
        try:
            from mcp_server.spark_inspiration.logic import rename_project_in_all_inspirations
            rename_project_in_all_inspirations(user_id, project_name, new_name)
        except Exception as e:
            print(f"Failed to update inspiration binding relation: {e}")

        return {"success": True, "message": "项目重命名成功", "newName": new_name}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "message": f"项目重命名失败: {exc}"})


# ── XOR 简易加密/解密（防随手打开，非安全加密） ──

_SPARK_XOR_KEY_LEN = 16  # 头部明文密钥长度（ASCII 时间戳）

def _xor_transform(data: bytes, key: bytes) -> bytes:
    """用 key 循环 XOR data"""
    kl = len(key)
    return bytes(b ^ key[i % kl] for i, b in enumerate(data))


def _make_timestamp_key() -> bytes:
    """生成当前时间戳密钥（精确到秒，16 字节右补零）"""
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    return ts.encode("ascii").ljust(_SPARK_XOR_KEY_LEN, b"\x00")


def _build_project_style_snapshot(user_id: str, project_name: str) -> dict | None:
    """导出项目当前生效风格的快照；只写进导出包，不写回项目目录。"""
    try:
        from agents.agent_style.utils import (
            load_style_profile_from_file,
            resolve_project_style_binding,
        )

        binding = resolve_project_style_binding(user_id, project_name)
        if not binding:
            return None
        profile = load_style_profile_from_file(binding["style_id"], user_id=user_id)
        if not isinstance(profile, str) or not profile.strip():
            return None

        return {
            "kind": _STYLE_EXPORT_SNAPSHOT_KIND,
            "version": _STYLE_EXPORT_SNAPSHOT_VERSION,
            "source_project_name": project_name,
            "style_id": binding["style_id"],
            "style_name": binding["style_name"],
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "style_profile": profile,
        }
    except Exception as exc:
        print(f"Failed to build project style snapshot: {exc}")
        return None


def _restore_project_style_snapshot(user_id: str, project_name: str, project_path: str) -> dict | None:
    """导入项目后，把导出包里的风格快照复制到当前用户风格库并重绑定项目。"""
    snapshot_path = os.path.join(project_path, _STYLE_EXPORT_SNAPSHOT_PATH)
    if not os.path.exists(snapshot_path):
        return None

    try:
        from agents.agent_style.utils import (
            find_style_profile_by_name,
            load_style_profile_record,
            make_unique_style_name,
            normalize_style_name,
            save_project_style_binding,
            save_style_profile_to_file,
        )

        with open(snapshot_path, "r", encoding="utf-8") as f:
            payload = json.load(f) or {}
        profile = payload.get("style_profile")
        if not isinstance(profile, str) or not profile.strip():
            return None

        source_style_id = str(payload.get("style_id") or "").strip()
        if not source_style_id:
            raise ValueError("项目导出包缺少 style_id")
        existing_record = (
            load_style_profile_record(source_style_id, user_id=user_id)
        )
        if existing_record:
            imported_record = existing_record
        else:
            source_name = normalize_style_name(payload.get("style_name"), fallback="风格") or "风格"
            imported_style_name = make_unique_style_name(user_id, f"{project_name}-{source_name}")
            save_style_profile_to_file(
                imported_style_name,
                profile,
                user_id=user_id,
                style_id=source_style_id,
            )
            imported_record = find_style_profile_by_name(imported_style_name, user_id=user_id)

        if not imported_record:
            raise ValueError("导入风格档案后无法解析身份")
        save_project_style_binding(user_id, project_name, imported_record["style_id"])
        try:
            os.remove(snapshot_path)
        except Exception:
            pass

        return {
            "styleName": imported_record["style_name"],
            "styleId": imported_record["style_id"],
            "sourceStyleName": payload.get("style_name") or "",
        }
    except Exception as exc:
        print(f"Failed to restore project style snapshot: {exc}")
        return {"warning": str(exc)}


def _safe_extract_project_zip(zf: zipfile.ZipFile, target_dir: str) -> None:
    """安全解压项目包，禁止 ZIP 内路径逃逸到目标目录外。"""
    target_root = os.path.abspath(target_dir)
    for member in zf.infolist():
        destination = os.path.abspath(os.path.join(target_root, member.filename))
        if destination != target_root and not destination.startswith(target_root + os.sep):
            raise ValueError(f"项目包包含非法路径: {member.filename}")
    zf.extractall(target_dir)


# ── 导出项目 ──

@project_router.get("/api/project/{project_name}/export")
async def export_project(project_name: str, user: dict = Depends(get_current_user)):
    """将项目目录打包为 .spark 文件（ZIP + XOR 加密）"""
    user_id = str(user["user_id"])
    project_path = get_project_path(user_id, project_name)

    if not os.path.exists(project_path):
        return JSONResponse(status_code=404, content={"success": False, "message": "项目不存在"})

    # 在内存中创建 ZIP（排除派生/缓存文件）
    _EXPORT_SKIP_DIRS = {"exports", "__pycache__", ".vector_index_lancedb", ".graphrag", ".story_memory"}
    _EXPORT_SKIP_FILES = {"stories.db"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(project_path):
            # 跳过排除的目录（原地修改 dirs 影响 os.walk 后续遍历）
            dirs[:] = [d for d in dirs if d not in _EXPORT_SKIP_DIRS]
            for fname in files:
                if fname in _EXPORT_SKIP_FILES:
                    continue
                full_path = os.path.join(root, fname)
                arcname = os.path.relpath(full_path, project_path).replace(os.sep, "/")
                if arcname == _STYLE_EXPORT_SNAPSHOT_PATH:
                    continue
                zf.write(full_path, arcname)

        style_snapshot = _build_project_style_snapshot(user_id, project_name)
        if style_snapshot:
            zf.writestr(
                _STYLE_EXPORT_SNAPSHOT_PATH,
                json.dumps(style_snapshot, ensure_ascii=False, indent=2),
            )

    zip_bytes = buf.getvalue()

    # XOR 加密
    key = _make_timestamp_key()
    encrypted = _xor_transform(zip_bytes, key)

    # 拼接：头部明文密钥 + 加密后的 ZIP
    payload = key + encrypted

    ts_short = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{project_name}_{ts_short}.spark"
    # RFC 5987: 中文文件名需用 filename*=UTF-8'' 编码，同时提供 ASCII 兜底
    filename_ascii = f"project_{ts_short}.spark"
    filename_utf8 = quote(filename, safe="")

    return Response(
        content=payload,
        media_type="application/x-sparkarc-project",
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename_ascii}\"; filename*=UTF-8''{filename_utf8}",
        },
    )


# ── 导入项目 ──

@project_router.post("/api/project/import")
async def import_project(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """从 .spark 文件导入项目（解密 → 解压 → 创建同名项目）"""
    user_id = str(user["user_id"])

    raw = await file.read()
    if len(raw) < _SPARK_XOR_KEY_LEN + 1:
        return JSONResponse(status_code=400, content={"success": False, "message": "无效的 .spark 文件"})

    # 提取头部密钥并解密
    key = raw[:_SPARK_XOR_KEY_LEN]
    encrypted_zip = raw[_SPARK_XOR_KEY_LEN:]
    zip_bytes = _xor_transform(encrypted_zip, key)

    # 解压到临时目录
    tmp_dir = os.path.join(get_user_projects_root(user_id), "_spark_import_tmp")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        buf = io.BytesIO(zip_bytes)
        with zipfile.ZipFile(buf, "r") as zf:
            _safe_extract_project_zip(zf, tmp_dir)
    except (zipfile.BadZipFile, Exception) as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return JSONResponse(status_code=400, content={"success": False, "message": f"解压失败: {e}"})

    # 确定项目名：优先从上传文件名推断，去掉时间戳后缀和 .spark 扩展名
    upload_name = file.filename or "imported_project"
    base_name = upload_name.rsplit(".", 1)[0] if "." in upload_name else upload_name
    # 去掉可能的时间戳后缀（格式：项目名_20260413_004000）
    clean_name = re.sub(r"[_\-]\d{8}[_\-]?\d{0,6}$", "", base_name) or base_name
    clean_name = normalize_project_name(clean_name)
    if not clean_name:
        clean_name = "imported_project"

    # 同名冲突时追加后缀
    final_name = clean_name
    projects_root = get_user_projects_root(user_id)
    if os.path.exists(os.path.join(projects_root, final_name)):
        suffix = 1
        while os.path.exists(os.path.join(projects_root, f"{clean_name}_{suffix}")):
            suffix += 1
        final_name = f"{clean_name}_{suffix}"

    # 创建目标项目目录并复制文件
    target_path = os.path.join(projects_root, final_name)
    shutil.move(tmp_dir, target_path)

    # 确保目录结构完整
    ensure_project_worldview_and_character_settings(user_id, final_name)
    ensure_project_stories_directory(user_id, final_name)

    imported_style = _restore_project_style_snapshot(user_id, final_name, target_path)
    warnings = []
    if isinstance(imported_style, dict) and imported_style.get("warning"):
        warnings.append(f"风格导入失败: {imported_style.get('warning')}")
        imported_style = None

    sqlite_result = None
    try:
        from story.importer import import_project_stories_to_db

        sqlite_result = import_project_stories_to_db(user_id, final_name, reset=True)
    except Exception as exc:
        warnings.append(f"运行时数据库重建失败: {exc}")

    return {
        "success": True,
        "message": "项目导入成功",
        "projectName": final_name,
        "importedStyle": imported_style,
        "runtimeDb": sqlite_result,
        "warnings": warnings,
    }
