from __future__ import annotations

import uuid
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from agents.prompt_layout import CompletedPromptTurn, build_append_only_task_messages
from langchain_core.messages import BaseMessage

from core.request_context import (
    current_agent_id,
    current_export_format,
    current_project_name,
    current_scriptwriter_prewrite_receipt,
    current_user_id,
    get_scriptwriter_prewrite_receipt,
    set_scriptwriter_prewrite_receipt,
)
from llm.agen_matchbox.tool_protocol import (
    build_tool_history_message,
    build_tool_result_messages,
    extract_tool_specs_from_message,
    prepare_tool_specs_for_execution,
)
from agents.tools.stream_events import (
    build_tool_display_details,
    is_tool_result_failure,
    normalize_tool_name,
)


PREWRITE_STATUS_MESSAGE = "编剧调研"
PREWRITE_TOOL_NAME = "prepare_script_creation"
# 单循环语义：每个场景最多 4 次模型请求（attempt/max_attempts），调研与落盘共用
# 同一个预算。4 是“调研请求的上限”，不是“正文分段数”：落盘（create_chapter +
# create_or_rewrite_script）是单次原子写入，成功即结束本场；write_started 置位前
# 一切都属于 prewrite。工具调用是非流式的，不存在逐字测速。
PREWRITE_MAX_REQUESTS = 4
# 兼容旧调用名；限制的是模型请求次数，不限制单轮或累计工具数量。
PREWRITE_MAX_TOOL_ROUNDS = PREWRITE_MAX_REQUESTS
PREWRITE_MAX_TOOL_CALLS = None
SCRIPTWRITER_CONTINUITY_MAX_TURNS = 4


@dataclass(frozen=True)
class ScriptwriterPreWriteRequest:
    user_id: str
    project_name: str
    task_description: str
    chapter_name: str = ""
    scene_name: str = ""
    scene_guidance: str = ""
    scene_characters: list[str] = field(default_factory=list)
    full_outline: str = ""
    available_context: str = ""
    worldview: str = ""
    roles: str = ""
    style_profile: str = ""
    story_tags: str = ""
    chr_reference: str = ""
    export_format: str = "arc"
    target_chars: int | None = None


@dataclass(frozen=True)
class ScriptwriterPreWriteResult:
    receipt_id: str
    brief: str
    research_context: str
    planning_note: str
    tools_used: tuple[str, ...]
    request_count: int = 0
    saved_payload: dict[str, Any] | None = None
    written_content: str = ""
    blocked_reason: str = ""
    continuity_turn: CompletedPromptTurn | None = None
    capture_path: str = ""

    @property
    def saved(self) -> bool:
        return bool(self.saved_payload and self.saved_payload.get("status") == "saved")

    @property
    def context_addition(self) -> str:
        parts = []
        if self.research_context.strip():
            parts.append("### PreWrite 调研所得\n" + self.research_context.strip())
        return "\n\n".join(parts)


def _normalize_target(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").casefold()


def _resolve_request_scene_identity(request: ScriptwriterPreWriteRequest) -> tuple[int, int]:
    """由大纲或现有文件元数据签发身份，禁止从模型标题编号推断。"""
    import os

    from agents.project_context import (
        load_project_context_bundle,
        resolve_outline_scene_contract_for_task,
    )
    from core.utils import get_project_stories_path
    from story.file_naming import list_story_files, sanitize_story_display_name

    bundle = load_project_context_bundle(request.user_id, request.project_name)
    contract = resolve_outline_scene_contract_for_task(
        bundle.get("outline_data") or {},
        task_description=request.task_description,
        chapter_title=request.chapter_name,
        scene_name=request.scene_name,
        file_path="",
    )
    chapter_index = contract.get("chapter_index")
    scene_index = contract.get("scene_index")
    if isinstance(chapter_index, int) and isinstance(scene_index, int):
        return chapter_index + 1, scene_index + 1

    stories_path = get_project_stories_path(request.user_id, request.project_name)
    story_files = list_story_files(stories_path)
    desired_display = sanitize_story_display_name(request.scene_name, "")
    for _, _, parsed in story_files:
        if not parsed or parsed.get("free"):
            continue
        if parsed.get("display_name") != desired_display:
            continue
        if isinstance(parsed.get("chapter_num"), int) and isinstance(parsed.get("scene_num"), int):
            return int(parsed["chapter_num"]), int(parsed["scene_num"])

    safe_chapter = str(request.chapter_name or "").strip().replace("\\", "_").replace("/", "_")
    chapter_dir = os.path.join(stories_path, safe_chapter)
    chapter_numbers: set[int] = set()
    scene_numbers: list[int] = []
    for _, absolute_path, parsed in story_files:
        if not parsed or parsed.get("free"):
            continue
        chapter_num = parsed.get("chapter_num")
        scene_num = parsed.get("scene_num")
        if isinstance(chapter_num, int):
            chapter_numbers.add(chapter_num)
        if os.path.dirname(absolute_path) == chapter_dir and isinstance(chapter_num, int):
            if isinstance(scene_num, int):
                scene_numbers.append(scene_num)

    if scene_numbers:
        target_chapter = next(
            int(parsed["chapter_num"])
            for _, absolute_path, parsed in story_files
            if parsed
            and os.path.dirname(absolute_path) == chapter_dir
            and isinstance(parsed.get("chapter_num"), int)
        )
        return target_chapter, max(scene_numbers) + 1

    return max(chapter_numbers, default=0) + 1, 1


def _issue_receipt(request: ScriptwriterPreWriteRequest, *, persist: bool = True) -> str:
    receipt_id = uuid.uuid4().hex
    if persist:
        chapter_num, scene_num = _resolve_request_scene_identity(request)
        set_scriptwriter_prewrite_receipt({
            "receipt_id": receipt_id,
            "user_id": str(request.user_id),
            "project_name": request.project_name,
            "chapter_name": request.chapter_name.strip(),
            "scene_name": request.scene_name.strip(),
            "task_description": request.task_description.strip(),
            "chapter_num": chapter_num,
            "scene_num": scene_num,
        })
    return receipt_id


def has_matching_prewrite_receipt(
    *,
    user_id: str,
    project_name: str,
    chapter_name: str | None,
    scene_name: str | None,
) -> bool:
    receipt = get_scriptwriter_prewrite_receipt()
    if not receipt:
        return False
    if str(receipt.get("user_id") or "") != str(user_id):
        return False
    if _normalize_target(str(receipt.get("project_name") or "")) != _normalize_target(project_name):
        return False

    expected_chapter = _normalize_target(chapter_name or "")
    actual_chapter = _normalize_target(str(receipt.get("chapter_name") or ""))
    expected_scene = _normalize_target(scene_name or "")
    actual_scene = _normalize_target(str(receipt.get("scene_name") or ""))
    invalid_targets = {"", "null", "none", "undefined", "nil"}
    if expected_chapter in invalid_targets or actual_chapter in invalid_targets:
        return False
    if expected_scene in invalid_targets or actual_scene in invalid_targets:
        return False
    if expected_chapter != actual_chapter or expected_scene != actual_scene:
        return False
    return True


def _build_prewrite_brief(request: ScriptwriterPreWriteRequest) -> str:
    from agents.project_context import build_scriptwriter_handoff_context

    return build_scriptwriter_handoff_context(
        request.user_id,
        request.project_name,
        task_description=request.task_description,
        chapter_name=request.chapter_name,
        scene_name=request.scene_name,
        scene_guidance=request.scene_guidance,
        scene_characters=request.scene_characters,
    )


def prepare_interactive_scriptwriter_prewrite(
    request: ScriptwriterPreWriteRequest,
) -> ScriptwriterPreWriteResult:
    """聊天/导演委派模式：生成确定性任务包，后续调查继续走当前 Agent 工具循环。"""
    brief = _build_prewrite_brief(request)
    receipt_id = _issue_receipt(request)
    return ScriptwriterPreWriteResult(
        receipt_id=receipt_id,
        brief=brief,
        research_context="",
        planning_note="",
        tools_used=(),
    )


def _prewrite_read_tools() -> list[Any]:
    from agents.tools.research import graph_rag_tool
    from agents.tools.search import search_project, semantic_search
    from agents.tools.scriptwriter import read_beat_sheet, read_character, read_synopsis, read_worldview
    from agents.tools.shared_read import list_chapters, read_chapter_outline_raw, read_chapter_scene
    from agents.tools.story_memory import story_memory_tool

    return [
        story_memory_tool,
        graph_rag_tool,
        list_chapters,
        read_chapter_scene,
        read_chapter_outline_raw,
        read_worldview,
        read_character,
        read_synopsis,
        read_beat_sheet,
        search_project,
        semantic_search,
    ]


def _autonomous_creation_tools() -> list[Any]:
    """自动写作固定工具集：调查与最终落盘共用同一份 Schema。"""
    from agents.tools.scriptwriter import create_chapter, create_or_rewrite_script

    return [*_prewrite_read_tools(), create_chapter, create_or_rewrite_script]


def _append_stable_project_context(system_prompt: str, request: ScriptwriterPreWriteRequest) -> str:
    """按稳定性排列项目级材料，把逐场变化内容留在最后一条 user。"""
    sections = [
        ("项目故事参数", request.story_tags),
        ("完整世界观", request.worldview),
        ("稳定完整大纲", request.full_outline),
        ("完整角色档案", request.roles),
        ("可用说话人标记", request.chr_reference),
        ("作者文风档案", request.style_profile),
    ]
    blocks = [system_prompt.rstrip()]
    for title, value in sections:
        text = str(value or "").strip()
        if text:
            blocks.append(f"### {title}\n{text}")
    return "\n\n".join(blocks)


def _build_research_system_prompt(request: ScriptwriterPreWriteRequest) -> str:
    from agents.language_policy import prepend_prompt_language_policy

    prompt = prepend_prompt_language_policy(
        """你是 Scriptwriter 的写前事实核对器。你只负责判断当前材料是否足以安全写作，不生成正文，也不输出自然语言调查总结。

系统已经提供确定性场景任务包。不要重复查询其中已有的 StoryMemory 条目。缺少关键原文证据时，优先在同一次响应中批量调用所有相互独立的只读工具；获得结果后只针对仍然存在的关键缺口深入查询。

你最多获得 4 次模型请求，但每次可调用任意数量的只读工具。资料足够时停止调用工具，仅返回 `PREWRITE_READY`。非关键细节缺少确定证据时，将边界理解为“当前材料不足以形成确定结论，本场不得作确定性描写”，不把它误判为可以自由编造。只有关键依据冲突、任何写法都会破坏既有事实时，才返回一段明确的冲突说明。

本轮任务描述与场景指导给出的开始/结束边界，高于完整大纲中属于后续场景的动作；不得把边界后的行为提前到当前场景。"""
    )
    return _append_stable_project_context(prompt, request)


def _build_autonomous_creation_system_prompt(
    request: ScriptwriterPreWriteRequest,
    *,
    agent: Any,
    tools: list[Any],
) -> str:
    from agents.agent_utils import load_prompt

    prompts = load_prompt("scriptwriter")
    base_prompt = prompts.get("pipeline_system") or prompts.get("system") or "你是专业执笔编剧。"
    prompt = agent._build_tool_system_prompt(
        base_prompt,
        skip_tool_confirmation=True,
        tools_override=tools,
        tool_rules_key="autonomous_tool_rules",
    )
    return _append_stable_project_context(prompt, request)


def _parse_saved_payload(value: Any) -> dict[str, Any] | None:
    try:
        payload = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict) and payload.get("status") == "saved":
        return payload
    return None


def _build_continuity_turn(
    request: ScriptwriterPreWriteRequest,
    *,
    user_prompt: str,
    saved_payload: dict[str, Any] | None,
    written_content: str,
    preserved_messages: Sequence[BaseMessage] = (),
) -> CompletedPromptTurn | None:
    """从实际落盘结果生成短回执，不复制正文，也不替代 StoryMemory。"""
    if not saved_payload:
        return None
    conception_match = re.search(
        r"<conception>\s*([\s\S]*?)\s*</conception>",
        str(written_content or ""),
        flags=re.IGNORECASE,
    )
    conception = re.sub(
        r"\s+",
        " ",
        conception_match.group(1) if conception_match else "",
    ).strip()
    if len(conception) > 1200:
        conception = conception[:1200].rstrip() + "…"
    receipt_parts = [
        "当前场景已经完成并成功落盘。",
        f"章节：{request.chapter_name.strip() or '（未提供）'}",
        f"场景：{request.scene_name.strip() or '（未提供）'}",
        f"保存路径：{str(saved_payload.get('path') or '').strip() or '（未提供）'}",
    ]
    if conception:
        receipt_parts.append(f"已落盘的连续性设计：{conception}")
    receipt_parts.append(
        "后续场景必须以已保存文件和最新 StoryMemory 事实包为准；不要复写本场正文。"
    )
    return CompletedPromptTurn(
        user_prompt=str(user_prompt or "").strip(),
        assistant_receipt="\n".join(receipt_parts),
        preserved_messages=tuple(preserved_messages),
    )


def _run_prewrite_tool_loop(
    request: ScriptwriterPreWriteRequest,
    *,
    llm: Any,
    tools: list[Any],
    system_prompt: str,
    user_prompt: str,
    clean_text: Callable[[Any], str] | None,
    on_tool_progress: Callable[[str], None] | None,
    on_lifecycle_event: Callable[[dict[str, Any]], None] | None,
    max_requests: int,
    require_save: bool,
    completed_turns: Sequence[CompletedPromptTurn] | None = None,
) -> ScriptwriterPreWriteResult:
    from agents.context_budget import (
        prepare_specialized_prompt_messages_with_budget,
        rebudget_existing_messages,
    )
    from langchain_core.messages import AIMessage, HumanMessage
    from llm.agen_matchbox.reasoning_compat import extract_text_content_from_message
    from agents.runtime_capture import RuntimeCapture

    clean = clean_text or (lambda value: str(value or ""))
    brief = _build_prewrite_brief(request)
    allowed_tools = {str(getattr(tool, "name", "")): tool for tool in tools}
    read_tool_names = set(allowed_tools) - {
        "create_chapter",
        "create_or_rewrite_script",
    }
    llm_with_tools = llm.bind_tools(tools)
    prepared_messages = prepare_specialized_prompt_messages_with_budget(
        agent_id="agent_scriptwriter",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm_client=llm,
    ).messages
    messages = build_append_only_task_messages(
        system_prompt=str(prepared_messages[0].content),
        completed_turns=completed_turns,
        current_user_prompt=str(prepared_messages[-1].content),
    )
    messages = rebudget_existing_messages(
        user_id=str(request.user_id),
        project_name=request.project_name,
        agent_id="agent_scriptwriter",
        messages=messages,
        llm_client=llm,
        tools=tools,
        current_user_message=user_prompt,
    ).messages

    capture = None
    if require_save:
        capture = RuntimeCapture.create(
            agent_id="agent_scriptwriter",
            user_id=str(request.user_id),
            project_name=request.project_name,
            metadata={
                "capture_kind": "auto_write_prewrite",
                "export_format": request.export_format or "arc",
                "chapter_name": request.chapter_name,
                "scene_name": request.scene_name,
                "task_description": request.task_description,
                "scene_guidance": request.scene_guidance,
                "scene_characters": request.scene_characters,
                "available_context": request.available_context,
                "story_tags": request.story_tags,
                "worldview": request.worldview,
                "roles": request.roles,
                "full_outline": request.full_outline,
                "style_profile": request.style_profile,
                "chr_reference": request.chr_reference,
                "target_chars": request.target_chars,
            },
        )
        if capture is not None:
            capture.start(messages=messages, tools=tools)

    user_token = current_user_id.set(str(request.user_id))
    project_token = current_project_name.set(request.project_name)
    agent_token = current_agent_id.set("agent_scriptwriter")
    format_token = current_export_format.set(request.export_format or "arc")
    receipt_state_token = current_scriptwriter_prewrite_receipt.set({})
    receipt_id = _issue_receipt(request, persist=require_save)
    gathered: list[str] = []
    tools_used: list[str] = []
    saved_payload: dict[str, Any] | None = None
    written_content = ""
    preserved_messages: tuple[BaseMessage, ...] = ()
    blocked_reason = ""
    last_tool_failure = ""
    request_count = 0
    capture_error = ""

    def emit_lifecycle(event: str, **payload: Any) -> None:
        """上报自动写作生命周期；观察回调失败不得影响正文任务。"""
        if on_lifecycle_event is None:
            return
        try:
            on_lifecycle_event({"event": event, **payload})
        except Exception:
            return

    try:
        request_limit = max(1, min(int(max_requests), PREWRITE_MAX_REQUESTS))
        for round_index in range(request_limit):
            attempt = round_index + 1
            if require_save and round_index == request_limit - 1:
                messages.append(HumanMessage(content=(
                    "这是本场允许的最后一次模型请求。现在必须作出最终决定：资料足够或只有非关键缺口时，"
                    "直接调用 create_chapter 与 create_or_rewrite_script 完成落盘；不得再调用任何只读调查工具。"
                    "只有关键依据冲突时才停止并明确说明冲突。"
                )))
            messages_before = list(messages)
            emit_lifecycle(
                "model_request_started",
                attempt=attempt,
                max_attempts=request_limit,
            )
            try:
                response = llm_with_tools.invoke(messages)
                request_count += 1
            except Exception as exc:
                backend_reason = "llm_error"
                backend_code = getattr(exc, "code", "") or type(exc).__name__
                emit_lifecycle(
                    "model_request_failed",
                    attempt=attempt,
                    max_attempts=request_limit,
                    error=str(exc),
                    backend_reason=backend_reason,
                    backend_code=str(backend_code),
                )
                raise
            tool_specs = prepare_tool_specs_for_execution(
                extract_tool_specs_from_message(response),
                normalize_name=normalize_tool_name,
                tool_lookup=allowed_tools,
            )
            emit_lifecycle(
                "model_request_succeeded",
                attempt=attempt,
                max_attempts=request_limit,
                tool_count=len(tool_specs),
            )
            if not tool_specs:
                response_text = clean(extract_text_content_from_message(response)).strip()
                if not require_save:
                    if response_text and response_text != "PREWRITE_READY":
                        blocked_reason = response_text
                    if capture is not None:
                        capture.record_turn(
                            ordinal=attempt,
                            messages_before=messages_before,
                            response=response,
                            tool_specs=(),
                            tool_results=(),
                            messages_after=messages,
                        )
                    break
                if round_index == request_limit - 1:
                    blocked_reason = response_text
                    if capture is not None:
                        capture.record_turn(
                            ordinal=attempt,
                            messages_before=messages_before,
                            response=response,
                            tool_specs=(),
                            tool_results=(),
                            messages_after=messages,
                        )
                    break
                messages.append(AIMessage(content=response_text or "未调用工具。"))
                messages.append(HumanMessage(content=(
                    "不要输出调查总结。若不存在关键事实冲突，请继续调查或直接调用正文落盘工具；"
                    "若存在关键冲突，请明确指出冲突材料与需要裁决的问题。"
                )))
                if capture is not None:
                    capture.record_turn(
                        ordinal=attempt,
                        messages_before=messages_before,
                        response=response,
                        tool_specs=(),
                        tool_results=(),
                        messages_after=messages,
                    )
                continue

            messages.append(build_tool_history_message(response, tool_specs))
            tool_results: list[tuple[str, str, Any]] = []
            for tool_call in tool_specs:
                tool_name = str(tool_call.get("name") or "").strip()
                tool_args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
                call_id = str(tool_call["call_id"])
                tool = allowed_tools.get(tool_name)
                if tool is None:
                    result = f"PreWrite 拒绝未绑定工具：{tool_name}"
                    backend_reason = "unknown_tool"
                elif (
                    require_save
                    and tool_name == "create_or_rewrite_script"
                    and "<conception>" not in str(tool_args.get("overwrite_content") or "")
                ):
                    result = (
                        "创建/重写剧本失败：自动写作正文必须包含一个 <conception>...</conception>，"
                        "记录最终场景设计与连续性约束；请补齐后重新调用。"
                    )
                    backend_reason = "missing_conception"
                else:
                    backend_reason = ""
                    try:
                        if on_tool_progress is not None:
                            on_tool_progress(tool_name)
                        emit_lifecycle(
                            "tool_started",
                            tool_name=tool_name,
                            attempt=attempt,
                            max_attempts=request_limit,
                        )
                        result = tool.invoke(tool_args)
                    except Exception as exc:
                        result = f"工具 {tool_name} 执行失败：{exc}"
                        backend_reason = "tool_exception"

                cleaned = clean(result).strip()
                tool_failed = is_tool_result_failure(tool_name, result)
                if backend_reason in {"unknown_tool", "missing_conception"}:
                    tool_failed = True
                if tool_failed:
                    if not backend_reason:
                        backend_reason = "tool_failure"
                    last_tool_failure = str(result).strip()
                    emit_lifecycle(
                        "tool_failed",
                        tool_name=tool_name,
                        attempt=attempt,
                        max_attempts=request_limit,
                        will_retry=round_index < request_limit - 1,
                        error=cleaned[:1000],
                        backend_reason=backend_reason,
                    )
                else:
                    display_details = build_tool_display_details(
                        tool_name,
                        tool_result=result,
                    )
                    display_result = display_details.get("tool_result", "")
                    if not isinstance(display_result, str):
                        display_result = json.dumps(display_result, ensure_ascii=False)
                    emit_lifecycle(
                        "tool_succeeded",
                        tool_name=tool_name,
                        attempt=attempt,
                        max_attempts=request_limit,
                        result=str(display_result)[:500],
                    )
                tools_used.append(tool_name)
                if tool_name in read_tool_names:
                    gathered.append(f"[{tool_name}]\n{cleaned}")
                if tool_name == "create_or_rewrite_script":
                    payload = _parse_saved_payload(result)
                    if payload is not None:
                        saved_payload = payload
                        written_content = str(tool_args.get("overwrite_content") or "").strip()
                tool_results.append((call_id, tool_name or "unknown_tool", cleaned))
                if saved_payload is not None:
                    break

            messages.extend(build_tool_result_messages(tool_results))
            if capture is not None:
                capture.record_turn(
                    ordinal=attempt,
                    messages_before=messages_before,
                    response=response,
                    tool_specs=tool_specs,
                    tool_results=tool_results,
                    messages_after=messages,
                )
            if saved_payload is not None:
                chapter_specs = [
                    spec for spec in tool_specs
                    if str(spec.get("name") or "") == "create_chapter"
                ]
                chapter_results = [
                    item for item in tool_results
                    if item[1] == "create_chapter"
                ]
                if chapter_specs and chapter_results:
                    preserved_messages = (
                        build_tool_history_message(response, chapter_specs),
                        *build_tool_result_messages(chapter_results),
                    )
            if saved_payload is not None:
                break
            messages = rebudget_existing_messages(
                user_id=str(request.user_id),
                project_name=request.project_name,
                agent_id="agent_scriptwriter",
                messages=messages,
                llm_client=llm,
                tools=tools,
                current_user_message=user_prompt,
            ).messages
    except Exception as exc:
        capture_error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if capture is not None:
            capture.finalize(
                messages=messages,
                status="saved" if saved_payload is not None else "failed" if capture_error else "blocked",
                error=capture_error,
                saved_payload=saved_payload,
                written_content=written_content,
            )
        current_scriptwriter_prewrite_receipt.reset(receipt_state_token)
        current_export_format.reset(format_token)
        current_agent_id.reset(agent_token)
        current_project_name.reset(project_token)
        current_user_id.reset(user_token)

    if require_save and saved_payload is None and not blocked_reason:
        blocked_reason = last_tool_failure or "在 4 次模型请求内未完成正文落盘，现有结果不足以安全继续。"
    return ScriptwriterPreWriteResult(
        receipt_id=receipt_id,
        brief=brief,
        research_context="\n\n".join(gathered),
        planning_note="",
        tools_used=tuple(tools_used),
        request_count=request_count,
        saved_payload=saved_payload,
        written_content=written_content,
        blocked_reason=blocked_reason,
        continuity_turn=_build_continuity_turn(
            request,
            user_prompt=user_prompt,
            saved_payload=saved_payload,
            written_content=written_content,
            preserved_messages=preserved_messages,
        ) if require_save else None,
        capture_path=str(capture.output_path) if capture is not None and capture.output_path else "",
    )


def run_autonomous_scriptwriter_prewrite(
    request: ScriptwriterPreWriteRequest,
    *,
    llm: Any,
    clean_text: Callable[[Any], str] | None = None,
    on_tool_progress: Callable[[str], None] | None = None,
    on_lifecycle_event: Callable[[dict[str, Any]], None] | None = None,
    max_tool_rounds: int = PREWRITE_MAX_TOOL_ROUNDS,
) -> ScriptwriterPreWriteResult:
    """局部编辑兼容模式：最多四次请求完成只读调查，不再生成独立总结。"""
    brief = _build_prewrite_brief(request)
    tools = _prewrite_read_tools()
    user_prompt = "\n\n".join(part for part in [
        brief,
        f"### 当前已经准备的动态上下文\n{request.available_context.strip()}" if request.available_context.strip() else "",
        "请核对当前材料是否足够安全写作。",
    ] if part)
    return _run_prewrite_tool_loop(
        request,
        llm=llm,
        tools=tools,
        system_prompt=_build_research_system_prompt(request),
        user_prompt=user_prompt,
        clean_text=clean_text,
        on_tool_progress=on_tool_progress,
        on_lifecycle_event=on_lifecycle_event,
        max_requests=max_tool_rounds,
        require_save=False,
    )


def run_autonomous_scriptwriter_creation(
    request: ScriptwriterPreWriteRequest,
    *,
    agent: Any,
    on_tool_progress: Callable[[str], None] | None = None,
    on_lifecycle_event: Callable[[dict[str, Any]], None] | None = None,
    max_requests: int = PREWRITE_MAX_REQUESTS,
    completed_turns: Sequence[CompletedPromptTurn] | None = None,
) -> ScriptwriterPreWriteResult:
    """Auto-Write 模式：调查、判断、正文生成和落盘都在同一工具循环完成。"""
    brief = _build_prewrite_brief(request)
    tools = _autonomous_creation_tools()
    user_prompt = "\n\n".join(part for part in [
        brief,
        f"### 当前场景动态上下文\n{request.available_context.strip()}" if request.available_context.strip() else "",
        (
            "### 本次落盘必须逐字复用的可读名称\n"
            f"chapter_name：{request.chapter_name.strip()}\n"
            f"scene_name/work_name：{request.scene_name.strip()}\n"
            "注意：以上是可读标题，不是文件名；work_name 绝对不要附加 .arc 或 .md 扩展名，"
            "也不要自行添加 1-1 等编号。"
        ),
        f"### 当前创作任务\n{request.task_description.strip()}",
        "请按需调查；材料足够后直接创建章节并调用正文工具落盘。不要输出独立的 PreWrite 总结。",
    ] if part)
    return _run_prewrite_tool_loop(
        request,
        llm=agent.llm,
        tools=tools,
        system_prompt=_build_autonomous_creation_system_prompt(request, agent=agent, tools=tools),
        user_prompt=user_prompt,
        clean_text=agent._clean_model_visible_arc_text,
        on_tool_progress=on_tool_progress,
        on_lifecycle_event=on_lifecycle_event,
        max_requests=max_requests,
        require_save=True,
        completed_turns=completed_turns,
    )
