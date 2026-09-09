"""受控的 SparkArc 运行态取证。

该模块只在显式设置 ``SPARKARC_CAPTURE_DIR`` 时工作。它不参与正常业务逻辑，
也不把取证内容写入普通日志；每次请求完成后生成一份独立 JSON，供 SparkPen
benchmark 做真实上下文和工具闭环审计。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


CAPTURE_SCHEMA_VERSION = "sparkarc-runtime-capture-v1"
CAPTURE_DIR_ENV = "SPARKARC_CAPTURE_DIR"
CAPTURE_AGENT_ENV = "SPARKARC_CAPTURE_AGENT_ID"


def _jsonable(value: Any) -> Any:
    """把 LangChain/Pydantic 对象转换为可持久化的 JSON 值。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    for method_name in ("model_dump", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                dumped = method()
            except Exception:
                continue
            return _jsonable(dumped)
    try:
        return _jsonable(vars(value))
    except Exception:
        return str(value)


def serialize_message(message: Any) -> dict[str, Any]:
    """保留消息角色、正文、工具调用和响应元数据。"""
    try:
        from langchain_core.messages import message_to_dict

        payload = message_to_dict(message)
        if isinstance(payload, dict):
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            message_type = str(payload.get("type") or data.get("type") or "")
            role = {
                "human": "user",
                "ai": "assistant",
                "tool": "tool",
                "system": "system",
                "function": "tool",
            }.get(message_type, message_type or None)
            normalized = {
                "role": role,
                "content": data.get("content"),
                "type": message_type,
            }
            for key in (
                "id",
                "name",
                "tool_call_id",
                "tool_calls",
                "invalid_tool_calls",
                "additional_kwargs",
                "response_metadata",
            ):
                if key in data:
                    normalized[key] = data[key]
            normalized["langchain"] = payload
            return _jsonable(normalized)
    except Exception:
        pass

    data: dict[str, Any] = {
        "type": type(message).__name__,
        "role": getattr(message, "type", None) or getattr(message, "role", None),
        "content": getattr(message, "content", None),
    }
    for key in ("id", "name", "tool_call_id", "tool_calls", "invalid_tool_calls", "additional_kwargs", "response_metadata"):
        value = getattr(message, key, None)
        if value not in (None, "", [], {}):
            data[key] = value
    return _jsonable(data)


def serialize_messages(messages: Iterable[Any]) -> list[dict[str, Any]]:
    return [serialize_message(message) for message in messages]


def _tool_schema(tool: Any) -> dict[str, Any]:
    """优先使用 LangChain 的 OpenAI function schema 转换器。"""
    try:
        from langchain_core.utils.function_calling import convert_to_openai_tool

        result = convert_to_openai_tool(tool)
        if isinstance(result, dict):
            return _jsonable(result)
    except Exception:
        pass

    name = str(getattr(tool, "name", "") or "")
    description = str(getattr(tool, "description", "") or "")
    schema_model = getattr(tool, "args_schema", None)
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    for method_name in ("model_json_schema", "schema"):
        method = getattr(schema_model, method_name, None)
        if callable(method):
            try:
                candidate = method()
            except Exception:
                continue
            if isinstance(candidate, dict):
                parameters = candidate
                break
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": _jsonable(parameters),
        },
    }


def serialize_tools(tools: Iterable[Any]) -> list[dict[str, Any]]:
    return [_tool_schema(tool) for tool in tools]


def _tool_call_record(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {"name": "", "call_id": "", "args": {}}
    args = spec.get("args")
    if not isinstance(args, dict):
        args = {}
    return _jsonable({
        "name": str(spec.get("name") or ""),
        "call_id": str(spec.get("call_id") or ""),
        "index": spec.get("index"),
        "args": args,
    })


def serialize_tool_specs(specs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_tool_call_record(spec) for spec in specs]


def serialize_tool_results(results: Iterable[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in results:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            call_id, name, result = item[0], item[1], item[2]
            records.append(_jsonable({
                "call_id": str(call_id or ""),
                "name": str(name or ""),
                "result": result,
            }))
        elif isinstance(item, dict):
            records.append(_jsonable(item))
        else:
            records.append({"result": _jsonable(item)})
    return records


def _message_roles(messages: Iterable[Any]) -> list[str]:
    roles: list[str] = []
    for message in messages:
        role = getattr(message, "type", None) or getattr(message, "role", None)
        if role:
            roles.append(str(role))
    return roles


def _safe_relative_file(root: Path, relative_path: str) -> Path | None:
    raw = str(relative_path or "").replace("\\", "/").strip()
    if not raw or Path(raw).is_absolute():
        return None
    candidate = (root / Path(raw)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _capture_saved_artifacts(
    *,
    user_id: str,
    project_name: str,
    saved_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """读取真实文件和当前 StoryMemory 快照，不相信工具返回的正文统计。"""
    if not saved_payload:
        return {"saved_file": None, "story_memory": None}

    result: dict[str, Any] = {"saved_file": None, "story_memory": None}
    try:
        from core.utils import get_project_stories_path

        stories_root = Path(get_project_stories_path(str(user_id), str(project_name))).resolve()
        relative_path = str(saved_payload.get("path") or "")
        file_path = _safe_relative_file(stories_root, relative_path)
        if file_path is not None:
            file_record: dict[str, Any] = {
                "relative_path": relative_path.replace("\\", "/"),
                "absolute_path": str(file_path),
                "exists": file_path.is_file(),
            }
            if file_path.is_file():
                content = file_path.read_text(encoding="utf-8")
                file_record.update({
                    "content": content,
                    "content_chars": len(content),
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                })
            result["saved_file"] = file_record
    except Exception as exc:
        result["saved_file_error"] = f"{type(exc).__name__}: {exc}"

    try:
        from agents.story_memory import StoryMemoryFacade

        state = StoryMemoryFacade(str(user_id), str(project_name)).load_state()
        relative_path = str(saved_payload.get("path") or "").replace("\\", "/")
        matching = [
            item for item in state.get("scenes", [])
            if isinstance(item, dict)
            and str(item.get("source_path") or "").replace("\\", "/") == relative_path
        ]
        result["story_memory"] = {
            "state_path": StoryMemoryFacade(str(user_id), str(project_name)).state_path,
            "scene_receipts": _jsonable(matching),
            "state": _jsonable(state),
        }
    except Exception as exc:
        result["story_memory_error"] = f"{type(exc).__name__}: {exc}"
    return result


class RuntimeCapture:
    """单个模型请求的取证会话。"""

    def __init__(self, *, agent_id: str, user_id: str, project_name: str, metadata: dict[str, Any] | None = None):
        capture_dir = str(os.environ.get(CAPTURE_DIR_ENV) or "").strip()
        self.enabled = bool(capture_dir)
        self.path: Path | None = None
        self.output_path: Path | None = None
        self._last_messages: list[Any] = []
        self.payload: dict[str, Any] = {
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "capture_id": uuid.uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": _jsonable({
                "agent_id": str(agent_id),
                "user_id": str(user_id),
                "project_name": str(project_name),
                **(metadata or {}),
            }),
            "tools": [],
            "turns": [],
        }
        if self.enabled:
            self.path = Path(capture_dir).expanduser().resolve()
            self.path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def create(
        cls,
        *,
        agent_id: str,
        user_id: str,
        project_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> "RuntimeCapture | None":
        expected_agent = str(os.environ.get(CAPTURE_AGENT_ENV) or "").strip()
        if expected_agent and expected_agent != str(agent_id):
            return None
        capture = cls(
            agent_id=agent_id,
            user_id=user_id,
            project_name=project_name,
            metadata=metadata,
        )
        return capture if capture.enabled else None

    def start(self, *, messages: Iterable[Any], tools: Iterable[Any]) -> None:
        if not self.enabled:
            return
        message_list = list(messages)
        tool_list = list(tools)
        self._last_messages = message_list
        self.payload["messages_initial"] = serialize_messages(message_list)
        self.payload["roles_initial"] = _message_roles(message_list)
        self.payload["tools"] = serialize_tools(tool_list)
        metadata = self.payload.get("metadata")
        if isinstance(metadata, dict):
            metadata.setdefault(
                "captured_tool_names",
                [str(getattr(tool, "name", "") or "") for tool in tool_list],
            )

    def record_turn(
        self,
        *,
        ordinal: int,
        messages_before: Iterable[Any],
        response: Any,
        tool_specs: Iterable[dict[str, Any]],
        tool_results: Iterable[Any],
        messages_after: Iterable[Any],
    ) -> None:
        if not self.enabled:
            return
        message_list = list(messages_after)
        self._last_messages = message_list
        self.payload["turns"].append(_jsonable({
            "ordinal": int(ordinal),
            "messages_before": serialize_messages(messages_before),
            "assistant_response": serialize_message(response) if response is not None else None,
            "tool_calls": serialize_tool_specs(tool_specs),
            "tool_results": serialize_tool_results(tool_results),
            "messages_after": serialize_messages(message_list),
        }))

    def _infer_saved_payload(self) -> dict[str, Any] | None:
        """从真实工具返回值恢复保存回执，避免展示层摘要成为真值。"""
        for turn in reversed(self.payload.get("turns") or []):
            for item in reversed(turn.get("tool_results") or []):
                raw = item.get("result") if isinstance(item, dict) else None
                try:
                    value = json.loads(str(raw or ""))
                except Exception:
                    continue
                if isinstance(value, dict) and value.get("status") == "saved":
                    return value
        return None

    def finalize(
        self,
        *,
        messages: Iterable[Any],
        status: str = "completed",
        error: str = "",
        saved_payload: dict[str, Any] | None = None,
        written_content: str = "",
    ) -> None:
        if not self.enabled or self.path is None:
            return
        self.payload["status"] = str(status or "completed")
        if error:
            self.payload["error"] = str(error)
        final_messages = list(messages) if messages else list(self._last_messages)
        self.payload["messages_final"] = serialize_messages(final_messages)
        saved_payload = saved_payload or self._infer_saved_payload()
        self.payload["saved_payload"] = _jsonable(saved_payload) if saved_payload else None
        if written_content:
            self.payload["written_content_from_tool_args"] = str(written_content)
        metadata = self.payload.get("metadata") or {}
        self.payload["artifacts"] = _capture_saved_artifacts(
            user_id=str(metadata.get("user_id") or ""),
            project_name=str(metadata.get("project_name") or ""),
            saved_payload=saved_payload,
        )
        filename = f"{self.payload['created_at'].replace(':', '').replace('+00:00', 'Z')}-{self.payload['capture_id']}.json"
        target = self.path / filename
        fd, temp_name = tempfile.mkstemp(prefix=f".{filename}.", suffix=".tmp", dir=str(self.path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temp_name, target)
            self.output_path = target
        finally:
            try:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            except OSError:
                pass
