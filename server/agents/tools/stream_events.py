import json
import re
from copy import deepcopy
from typing import Any, Dict


_DETAIL_MAX_STRING = 2000
_DETAIL_MAX_RESULT = 4000
_DETAIL_MAX_ITEMS = 24
_DETAIL_MAX_KEYS = 40
_SENSITIVE_KEY_NAMES = {
    "apikey",
    "authorization",
    "accesstoken",
    "clientsecret",
    "cookie",
    "credential",
    "password",
    "privatekey",
    "secret",
    "token",
}
_SENSITIVE_TEXT_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|access[_-]?token|token|password|secret|cookie)\s*[:=]\s*([^\s,;]+)"
)
_SENSITIVE_STRUCTURED_TEXT_RE = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|authorization|bearer|access[_-]?token|token|password|secret|cookie)[\"']?\s*:\s*[\"']?)([^,\"'\s}]+)"
)


# 仅输入白名单工具允许在聊天轨迹中展示传入参数；返回结果默认不展示。
# （历史行为：tool_result 曾按同一白名单展示。滑窗读窗/检索命中正文可达
# 64K，一旦进 segments/tool_traces 就会落盘 DB 并撑爆历史接口，因此返回
# 结果一律不进面板；模型侧正文走内存 ToolMessage，不走这里。）
# 滑窗读窗工具的 policy 是刻意最小化的：只存指针（source_id/chunk_index）。
# 检索工具（search_project/semantic_search）存“简要调用内容”：pattern/query +
# scope + k，供面板展开显示“搜了什么”；命中正文不存（走读窗按需取）。
# 记账工具（note_window_clues）是唯一的例外：clues 是 Agent 写给用户看的
# 线索结论，全量展示（只脱敏、不截断），否则面板只有 source_id/窗口号这类
# 机器指针，用户完全看不到记了什么。
TOOL_DETAIL_POLICIES: Dict[str, tuple[str, ...]] = {
    "describe_longread_source": ("source_id",),
    "read_longread_window": ("source_id", "chunk_index"),
    "read_worldview_window": ("chunk_index",),
    "read_attachment_chunk": ("attachment_id", "chunk_index"),
    "note_window_clues": ("source_id", "chunk_index", "clue_type", "importance", "clues"),
    "search_project": ("pattern", "scope", "max_results"),
    "semantic_search": ("query", "scope", "k"),
    "delegate_task": (
        "target_agent", "task_description", "completion_mode", "chapter_name",
        "scene_name", "scene_file_path", "scene_guidance", "scene_characters",
    ),
    "replace_from_search": ("indices", "replacement"),
    "patch_script": ("search_text", "replace_text"),
    "patch_worldview": ("search_text", "replace_text"),
    "patch_synopsis": ("search_text", "replace_text"),
    "patch_beat_sheet": ("search_text", "replace_text"),
    "patch_outline": ("search_text", "replace_text"),
    "web_search": ("provider", "query", "num_results", "exa_options", "tavily_options"),
    "work_tracker": ("overwrite", "items", "operations", "summary", "contract"),
    "rewrite_inspiration": ("content", "title", "tags"),
    "rewrite_worldview": ("content",),
    "rewrite_all_characters": ("characters",),
    "rewrite_outline": ("outline",),
    "rewrite_synopsis": ("synopsis",),
    "rewrite_beat_sheet": ("beat_sheet",),
    "update_character": ("character_id", "name", "content"),
    "create_character_relation": ("source_character", "target_character", "relation", "description"),
    "update_project_story_tags": (
        "workspace_mode", "style", "genres", "tones", "worldviews", "pov",
        "length_hint", "scene_length_hint", "scene_target_chars", "active_inspiration_id",
    ),
    "create_or_rewrite_script": ("chapter_name", "work_name", "file_path"),
    "create_chapter": ("chapter_name",),
    "organize_scenes_to_chapter": ("scene_paths", "new_chapter_name", "chapter_num", "preserve_originals"),
    "rename_chapter": ("chapter_path", "new_chapter_name"),
    "rename_scene": ("scene_path", "new_scene_name"),
    "batch_rename_chapters": ("renames",),
    "batch_rename_scenes": ("renames",),
    "batch_update_story_metadata": ("updates",),
    "reorder_chapters": ("chapter_paths",),
    "reorder_scenes": ("chapter_path", "scene_paths"),
}


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _is_sensitive_key(value: Any) -> bool:
    """识别常见密钥字段的变体，避免仅依赖精确字段名。"""
    normalized = _normalized_key(value)
    if normalized in _SENSITIVE_KEY_NAMES:
        return True
    return normalized.endswith((
        "apikey",
        "accesstoken",
        "refreshtoken",
        "token",
        "secret",
        "password",
        "privatekey",
        "credential",
        "authorization",
        "cookie",
    ))


def _redact_text(value: str, limit: int) -> str:
    text = str(value or "")
    text = _SENSITIVE_TEXT_RE.sub(lambda match: f"{match.group(1)}=[已隐藏]", text)
    text = _SENSITIVE_STRUCTURED_TEXT_RE.sub(lambda match: f"{match.group(1)}[已隐藏]", text)
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _safe_detail(value: Any, *, limit: int = _DETAIL_MAX_STRING, depth: int = 0) -> Any:
    """递归脱敏并限制事件详情大小，避免把密钥或超长正文发到前端。"""
    if depth > 5:
        return "[嵌套层级过深]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_text(value, limit)
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _DETAIL_MAX_KEYS:
                result["…"] = "其余字段已省略"
                break
            key_text = str(key)
            if _is_sensitive_key(key_text):
                result[key_text] = "[已隐藏]"
                continue
            result[key_text] = _safe_detail(item, limit=limit, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [_safe_detail(item, limit=limit, depth=depth + 1) for item in items[:_DETAIL_MAX_ITEMS]]
        if len(items) > _DETAIL_MAX_ITEMS:
            result.append("其余项目已省略")
        return result
    return _redact_text(value, limit)


def _redact_text_no_limit(value: str) -> str:
    """只做敏感信息脱敏，不做任何长度截断（记账 clues 展示专用）。"""
    text = str(value or "")
    text = _SENSITIVE_TEXT_RE.sub(lambda match: f"{match.group(1)}=[已隐藏]", text)
    text = _SENSITIVE_STRUCTURED_TEXT_RE.sub(lambda match: f"{match.group(1)}[已隐藏]", text)
    return text


def _safe_detail_no_truncate(value: Any, *, depth: int = 0) -> Any:
    """递归脱敏但不限长度/条数（仅用于 note_window_clues.clues 全量展示）。

    保留深度 guard 防递归爆炸、保留敏感 key 隐藏；去掉 _DETAIL_MAX_STRING、
    _DETAIL_MAX_ITEMS、`…其余已省略` 这类截断。调用方已明确接受超长线索
    进落盘 segments 的代价。
    """
    if depth > 5:
        return "[嵌套层级过深]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_text_no_limit(value)
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                result[key_text] = "[已隐藏]"
                continue
            result[key_text] = _safe_detail_no_truncate(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_safe_detail_no_truncate(item, depth=depth + 1) for item in list(value)]
    return _redact_text_no_limit(value)


def _clip_detail_payload(value: Any, *, limit: int) -> Any:
    """对复杂对象再做一次整体长度限制，保留合法 JSON 结构。"""
    safe = _safe_detail(value, limit=_DETAIL_MAX_STRING)
    try:
        encoded = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return _redact_text(safe, limit)
    if len(encoded) <= limit:
        return safe
    if isinstance(safe, str):
        return _redact_text(safe, limit)
    if isinstance(safe, dict):
        compact: Dict[str, Any] = {}
        for key, item in safe.items():
            candidate = dict(compact)
            candidate[key] = item
            try:
                candidate_length = len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")))
            except Exception:
                candidate_length = limit + 1
            if candidate_length > limit:
                compact["…"] = "其余字段已省略"
                break
            compact = candidate
        return compact
    if isinstance(safe, list):
        compact_list: list[Any] = []
        for item in safe:
            candidate = compact_list + [item]
            try:
                candidate_length = len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")))
            except Exception:
                candidate_length = limit + 1
            if candidate_length > limit:
                compact_list.append("其余项目已省略")
                break
            compact_list = candidate
        return compact_list
    return _redact_text(encoded, limit)


def build_tool_display_details(
    tool_name: str,
    *,
    tool_input: Any = None,
    tool_result: Any = None,
    tool_error: Any = None,
) -> Dict[str, Any]:
    """生成前端可展开的受控工具详情，不改变模型请求参数。

    约束：只展示输入白名单字段；返回结果一律不展示（见 TOOL_DETAIL_POLICIES
    顶部注释）。tool_result 参数保留仅为兼容旧调用签名，传入也会被丢弃。
    """
    normalized = normalize_tool_name(tool_name)
    fields = TOOL_DETAIL_POLICIES.get(normalized)
    details: Dict[str, Any] = {}
    if fields is not None and tool_input is not None:
        if isinstance(tool_input, dict):
            selected = {field: deepcopy(tool_input[field]) for field in fields if field in tool_input}
            if normalized == "note_window_clues" and "clues" in selected:
                # 记账 clues 全量展示：只脱敏、不截断。账本单条上限 500 字符，
                # 全量即账本原样；调用方已明确接受超长线索进落盘 segments 的代价。
                selected = dict(selected)
                selected["clues"] = _safe_detail_no_truncate(selected["clues"])
                rest = {k: v for k, v in selected.items() if k != "clues"}
                details["tool_input"] = {
                    **_clip_detail_payload(rest, limit=_DETAIL_MAX_RESULT),
                    "clues": selected["clues"],
                }
            else:
                details["tool_input"] = _clip_detail_payload(selected, limit=_DETAIL_MAX_RESULT)
        else:
            details["tool_input"] = _clip_detail_payload(tool_input, limit=_DETAIL_MAX_RESULT)
    if tool_error is not None:
        details["tool_error"] = _clip_detail_payload(tool_error, limit=_DETAIL_MAX_RESULT)
    return details


def normalize_tool_name(raw_tool_name: str = "") -> str:
    normalized = str(raw_tool_name or "").strip().lower()
    if not normalized:
        return ""
    key = normalized.replace(" ", "").replace("_", "").replace("-", "")
    aliases = {
        "rewriteworldview": "rewrite_worldview",
        "rewriteallcharacters": "rewrite_all_characters",
        "rewritecharacters": "rewrite_all_characters",
        "rewritecharacter": "update_character",
        "updatecharacter": "update_character",
        "updatestorytags": "update_project_story_tags",
        "updateprojectstorytags": "update_project_story_tags",
        "updateprojectstorytag": "update_project_story_tags",
        "updatestorytag": "update_project_story_tags",
    }
    return aliases.get(key, normalized)


def is_tool_result_failure(tool_name: str, result: Any) -> bool:
    """识别工具以文本返回的可恢复失败，避免把降级结果误标为成功。"""
    if not isinstance(result, str):
        return False
    normalized = normalize_tool_name(tool_name)
    if "执行失败" in result:
        return True
    stripped = result.strip()
    domain_failure_prefixes = {
        "prepare_script_creation": ("PreWrite 失败",),
        "create_or_rewrite_script": ("创建/重写剧本失败",),
        "create_chapter": ("创建章节失败",),
        "organize_scenes_to_chapter": ("整理失败",),
        "rename_chapter": ("重命名章节失败",),
        "rename_scene": ("重命名场景失败",),
        "reorder_chapters": ("重排章节失败",),
        "reorder_scenes": ("重排场景失败",),
        "batch_rename_chapters": ("批量重命名章节失败", "批量结构更新失败"),
        "batch_rename_scenes": ("批量重命名场景失败", "批量结构更新失败"),
        "batch_update_story_metadata": ("批量更新故事元数据失败", "批量结构更新失败"),
        "batch_update_story_structure": ("批量结构更新失败",),
        "create_character_relation": ("创建角色关系失败",),
        "work_tracker": ("任务板更新失败", "读取任务板失败"),
    }
    if stripped.startswith(domain_failure_prefixes.get(normalized, ())):
        return True
    if normalized == "web_search":
        return result.startswith((
            "联网搜索当前不可用",
            "联网搜索暂时不可用",
            "联网搜索失败",
        ))
    return False


def get_tool_result_failure_message(tool_name: str, result: Any) -> str:
    """为前端事件生成与实际失败类型一致的简短说明。"""
    normalized = normalize_tool_name(tool_name)
    text = result if isinstance(result, str) else ""
    if normalized == "web_search":
        if text.startswith(("联网搜索当前不可用", "联网搜索暂时不可用")):
            return "联网搜索上游暂不可用，AI 将基于失败状态继续回应"
        return "联网搜索未能完成，AI 将基于失败状态继续回应"
    if text.strip().startswith((
        "PreWrite 失败", "创建/重写剧本失败", "创建章节失败", "整理失败",
        "重命名章节失败", "重命名场景失败", "重排章节失败", "重排场景失败",
        "批量重命名章节失败", "批量重命名场景失败", "批量更新故事元数据失败",
        "批量结构更新失败", "创建角色关系失败", "任务板更新失败", "读取任务板失败",
    )):
        return text.strip().splitlines()[0][:200]
    if normalized == "work_tracker" and "operations[" in text:
        return text.strip().splitlines()[0][:200]
    return "模型使用了错误的调用格式，正在尝试修正"


def get_tool_ui_binding(tool_name: str) -> Dict[str, Any]:
    normalized = normalize_tool_name(tool_name)
    if normalized == "rewrite_inspiration":
        return {
            "scope": "muse",
            "target": "",
            "refresh_events": ["muse-refresh"],
        }

    if normalized in {"rewrite_worldview", "rewrite_all_characters", "update_character", "create_character_relation"}:
        target = "worldview" if normalized == "rewrite_worldview" else "characters"
        refresh_events = ["lorebook-refresh"]
        if target == "worldview":
            refresh_events.insert(0, "lorebook-refresh-worldview")
        if target == "characters":
            refresh_events.insert(0, "lorebook-refresh-characters")
        return {
            "scope": "world",
            "target": target,
            "refresh_events": refresh_events,
        }

    if normalized in {"rewrite_outline", "patch_outline"}:
        return {
            "scope": "outline",
            "target": "",
            "refresh_events": ["outline-refresh"],
        }

    if normalized == "read_chapter_outline_raw":
        return {
            "scope": "outline",
            "target": "",
            "refresh_events": [],
        }

    if normalized in {"rewrite_synopsis", "patch_synopsis"}:
        return {
            "scope": "synopsis",
            "target": "content",
            "refresh_events": ["synopsis-refresh"],
        }

    if normalized in {"rewrite_beat_sheet", "patch_beat_sheet"}:
        return {
            "scope": "synopsis",
            "target": "beats",
            "refresh_events": ["synopsis-refresh"],
        }

    if normalized == "update_project_story_tags":
        return {
            "scope": "story-tags",
            "target": "",
            "refresh_events": ["story-tags-refresh"],
        }

    if normalized in {"search_project", "semantic_search", "web_search"}:
        return {
            "scope": "",
            "target": "",
            "refresh_events": [],
        }

    if normalized == "replace_from_search":
        return {
            "scope": "",
            "target": "",
            "refresh_events": [
                "outline-refresh",
                "synopsis-refresh",
                "lorebook-refresh",
                "lorebook-refresh-worldview",
                "lorebook-refresh-characters",
            ],
        }

    return {
        "scope": "",
        "target": "",
        "refresh_events": [],
    }


def build_tool_stream_event(
    event_name: str,
    tool_name: str,
    *,
    source_agent: str = "",
    message: str = "",
    tool_call_key: str = "",
    tool_input: Any = None,
    tool_result: Any = None,
    tool_error: Any = None,
    **extra: Any,
) -> Dict[str, Any]:
    normalized_tool_name = normalize_tool_name(tool_name)
    payload: Dict[str, Any] = {
        "event": str(event_name or "").strip(),
        "tool_name": normalized_tool_name,
    }
    if source_agent:
        payload["source_agent"] = source_agent
    if message:
        payload["message"] = message
    if tool_call_key:
        payload["tool_call_key"] = tool_call_key

    binding = get_tool_ui_binding(normalized_tool_name)
    if binding.get("scope"):
        payload["ui_scope"] = binding["scope"]
    if binding.get("target"):
        payload["ui_target"] = binding["target"]
    if binding.get("refresh_events"):
        payload["ui_refresh_events"] = list(binding["refresh_events"])

    # 调用方附加元数据不能覆盖受控详情，避免误把原始参数绕过脱敏层发给前端。
    payload.update({
        key: value
        for key, value in extra.items()
        if key not in {"tool_input", "tool_result", "tool_error"}
    })
    payload.update(build_tool_display_details(
        normalized_tool_name,
        tool_input=tool_input,
        tool_result=tool_result,
        tool_error=tool_error,
    ))
    return payload
