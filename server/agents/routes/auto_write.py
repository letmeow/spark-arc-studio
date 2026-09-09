"""
Auto-Write API - 自动化剧本撰写的异步批处理引擎。

════════════════════════════════════════════════════════════════════════
【架构定位：无人值守的长连接连续生成管道】

本文件不仅实现了与 `production.py` 类似的【业务语义流 (Stream Semantics)】标准 SSE 通信，
更进一步实现了一个复杂的状态机，用于控制**跨章节、多场景**的连续无人值守生成。

【工作流核心机制】
1. 状态落地：通过 `auto_write_state.py` 将当前运行游标（Chapter / Scene Index）实时落盘。
2. 线程隔离：长耗时的 AI 调用通过 `asyncio.to_thread` 执行，避免阻塞事件循环。
3. 状态帧约定：推送极为精细的语义帧：
   - chapter_start / prewrite / prewrite_tool / model_request_* / tool_*（调研与落盘共用循环）
   - writing_scene / scene_completed / scene_saved / chapter_saved
   - paused / cancelled / complete / error
   这些帧使得前端组件能在不直接介入生成逻辑的前提下，完美渲染复杂的嵌套进度环。

【单循环语义（必须遵守）】
- 每个场景 exactly 一次 `run_autonomous_scriptwriter_creation` 工具循环：
  调研（只读工具）与落盘（create_chapter + create_or_rewrite_script）在同一循环完成。
- `model_request` 轮次计数（attempt/max_attempts，最多 4 次）只属于“调研请求”，
  落盘本身是单次原子写入，不存在“正文分段”，不得把轮次显示为“第 x/4 段正文”。
- `write_started` 置位前一切都是 prewrite：只有落盘工具被调用后 phase 才翻为 writing。
- 工具调用是非流式的（`llm.invoke` 一次性返回完整工具结果）：不存在逐字测速。
  `scene_completed` 的 total_chars/elapsed/avg_speed 是落盘瞬间的事后统计。
  不要恢复“逐字播报工具参数”的旧 SSE 正文流，那会把未提交的草稿当成已完成内容展示。

作为项目内【工程化成熟度最高】的标准链路之一，本文件为未来添加其他后台自动批处理任务（如
自动大纲扩写、长篇风格修订）提供了最可靠的并发模板。
════════════════════════════════════════════════════════════════════════
"""

import json
import os
import asyncio
import time
import threading
from typing import Optional, List, Dict, Any, Callable
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from core.auth import get_current_user
from core.utils import get_project_path
from core.project_settings import get_project_story_tags, get_workspace_mode
from agents.story_terminology import get_story_terminology
from agents.agent_scriptwriter import ScriptwriterAgent
from agents.scriptwriter_prewrite import (
    PREWRITE_MAX_REQUESTS,
    PREWRITE_STATUS_MESSAGE,
    SCRIPTWRITER_CONTINUITY_MAX_TURNS,
    ScriptwriterPreWriteRequest,
    run_autonomous_scriptwriter_creation,
)
from agents.prompt_layout import CompletedPromptTurn
from agents.agent_critic import CriticAgent
from agents.stream_semantics import (
    semantic_sse_data,
    merge_semantics,
    on_cancelled,
    on_done,
    on_error,
    on_progress,
    on_start,
    on_stats,
)
from agents.auto_write_state import (
    begin_auto_write_run,
    build_auto_write_state_payload,
    build_scene_output_filename,
    patch_auto_write_state,
)
from story.file_naming import (
    DuplicateSceneIdentityError,
    canonical_chapter_display_name,
    canonical_scene_display_name,
    resolve_planned_scene_file_path,
    strip_story_filename_meta,
)
from agents.project_context import (
    load_worldview,
    load_all_roles,
    load_full_outline,
    build_scene_context,
    build_story_tags_hint,
)
from agents.agent_style.utils import (
    format_style_profile_for_prompt,
    load_project_style_profile,
)

auto_write_router = APIRouter()


def record_auto_write_scene_review(
    *,
    user_id: str,
    project_name: str,
    critic: Any,
    scene_text: str,
    context_text: str,
    guidance_text: str,
    scene_title: str,
    source_rel_path: str,
    worldview: str,
    roles: str,
    style_profile: Any,
    story_tags_block: str,
) -> Dict[str, Any] | None:
    """自动写作场景保存后执行 Critic 评审，并把修订工单写入 StoryMemory。"""
    if critic is None:
        return None
    try:
        review_target = source_rel_path or scene_title or "自动写作场景"
        review = critic.evaluate(
            script_text=scene_text,
            context=context_text,
            guidance=guidance_text,
            worldview=worldview,
            roles=roles,
            style_profile=style_profile,
            review_target=review_target,
            story_tags=story_tags_block,
        )

        from agents.story_memory import StoryMemoryFacade

        tickets = StoryMemoryFacade(user_id, project_name).record_quality_review(
            review=review,
            review_target=review_target,
            scene_name=scene_title,
            source_path=source_rel_path,
        )
        patch_auto_write_state(
            user_id,
            project_name,
            lastReviewDecision=review.get("decision") or "",
            lastReviewGrade=review.get("overall_grade") or "",
            lastReviewTarget=review_target,
            lastReviewTicketCount=len(tickets or []),
            lastReviewError="",
        )
        return review
    except Exception as e:
        message = str(e)
        print(f"[AutoWrite] 自动审稿失败（不影响写作保存）：{message}")
        patch_auto_write_state(
            user_id,
            project_name,
            lastReviewError=message,
            lastReviewTarget=source_rel_path or scene_title or "",
        )
        return None


def _resolve_export_format(user_id: str, project_name: str) -> str:
    """根据项目 story tags 解析自动写作输出格式。"""
    return "novel" if get_workspace_mode(user_id, project_name) == "novel" else "arc"


def _require_nonempty_scene_body(content: str, export_format: str) -> int:
    """按最终正文口径校验落盘结果，拒绝只有元数据的空场景。"""
    from story.text_metrics import count_story_body_chars

    written_chars = count_story_body_chars(content, export_format)
    if written_chars <= 0:
        raise RuntimeError("编剧落盘结果没有可见正文，自动写作未完成当前场景。")
    return written_chars


def _auto_write_terms(user_id: str, project_name: str) -> dict[str, str]:
    """返回自动写作状态和任务提示使用的模式化结构术语。"""
    return get_story_terminology(get_workspace_mode(user_id, project_name))


async def generate_script_stream(
    user_id: str,
    project_name: str,
    outline: Dict[str, Any],
    request: Request | None = None,
    mode: str = "chapter_by_chapter",  # "all" or "chapter_by_chapter"
    start_chapter_index: int = 0,
    start_scene_index: int = 0,  # 从该章内第 N 个场景开始（仅对起始章有效）
    context_strategy: str = "accumulate",
    export_format: str = "arc",
    auto_review: bool = False,
    from_director: bool = False,
    stop_event: threading.Event | None = None,
    prewrite_tool_callback: Callable[[Dict[str, Any]], None] | None = None,
):
    """
    Generator function for SSE streaming of script generation progress.
    """

    stop_event = stop_event or threading.Event()

    # 1. Initialize
    nodes = outline.get("nodes", [])
    stories_path = os.path.join(get_project_path(user_id, project_name), "stories")
    os.makedirs(stories_path, exist_ok=True)

    # Filter chapters (skip those before start_chapter_index)
    # Note: nodes can contain non-chapter items if the structure is complex,
    # but usually top-level nodes are chapters.
    chapter_nodes = [n for n in nodes if n.get("type") == "chapter"]
    current_chapter_index: int | None = None
    current_chapter_title = ""
    current_scene_index: int | None = None
    current_scene_title = ""
    generated_files: list[str] = []
    generated_scene_files: list[str] = []
    # 当前 story_unit 的生命周期标记：只有落盘工具被调用后才置为 True。
    write_started = False
    total_scenes_count = sum(len(ch.get("children") or []) for ch in chapter_nodes)

    state = begin_auto_write_run(
        user_id,
        project_name,
        mode=mode,
        export_format=export_format,
        start_chapter_index=start_chapter_index,
        start_scene_index=start_scene_index,
        total_chapters=len(chapter_nodes),
        total_scenes=total_scenes_count,
        from_director=from_director,
    )

    def update_state(status: str, **extra: Any) -> Dict[str, Any]:
        payload = {
            "status": status,
            "mode": mode,
            "exportFormat": export_format,
            "autoReviewEnabled": auto_review,
            "fromDirector": from_director,
            "currentChapterIndex": current_chapter_index,
            "currentChapterTitle": current_chapter_title,
            "currentSceneIndex": current_scene_index,
            "currentSceneTitle": current_scene_title,
            "generatedFiles": generated_files,
            "generatedSceneFiles": generated_scene_files,
            "writeStarted": bool(write_started),
        }
        payload.update(extra)
        return patch_auto_write_state(
            user_id,
            project_name,
            **payload,
        )

    if start_chapter_index >= len(chapter_nodes):
        update_state(
            "complete",
            nextChapterIndex=len(chapter_nodes),
            availableResumeChapterIndex=None,
            availableResumeSceneIndex=None,
            availableRestartChapterIndex=None,
            completedAt=patch_auto_write_state(user_id, project_name).get("updatedAt", ""),
            lastError="",
        )
        yield semantic_sse_data(
            "complete",
            message="No more chapters to write.",
            completedScenes=total_scenes_count,
            totalScenes=total_scenes_count,
            **on_done("没有更多章节需要生成"),
        )
        return

    try:
        writer = ScriptwriterAgent(user_id)
    except ValueError as e:
        message = str(e)
        update_state(
            "error",
            nextChapterIndex=start_chapter_index,
            availableResumeChapterIndex=start_chapter_index,
            availableResumeSceneIndex=start_scene_index,
            availableRestartChapterIndex=start_chapter_index,
            lastError=message,
        )
        yield semantic_sse_data("error", message=message, **on_error(message))
        return
    except Exception as e:
        from .schemas import format_ai_error
        message = format_ai_error(e)
        update_state(
            "error",
            nextChapterIndex=start_chapter_index,
            availableResumeChapterIndex=start_chapter_index,
            availableResumeSceneIndex=start_scene_index,
            availableRestartChapterIndex=start_chapter_index,
            lastError=message,
        )
        yield semantic_sse_data("error", message=message, **on_error(message))
        return

    critic: CriticAgent | None = None
    if auto_review:
        try:
            critic = CriticAgent(user_id)
        except Exception as e:
            critic = None
            print(f"[AutoWrite] Critic 初始化失败，自动审稿降级跳过：{e}")

    terms = _auto_write_terms(user_id, project_name)
    yield semantic_sse_data(
        "started",
        **merge_semantics(
            on_start("自动撰写任务已启动"),
            on_progress(f"正在准备{terms['group']}任务...", stage="prepare"),
        ),
    )

    # ── 预加载全量项目数据（一次性，无需在每个场景重复 IO）─────────────────
    worldview = load_worldview(user_id, project_name)
    roles, chr_map = load_all_roles(user_id, project_name)
    full_outline = load_full_outline(user_id, project_name)
    style_profile = load_project_style_profile(user_id=user_id, project_name=project_name)
    
    # ── 加载项目级故事主题参数（story_tags）────────────────────────────────
    # 这些参数是“项目宪法”，包含 POV、风格、题材、基调、世界观、作品规模等。
    story_tags = get_project_story_tags(user_id, project_name)
    
    # 构建 story_tags 注入块（与 context_provider._build_story_tags_block 保持一致）
    story_tags_block = build_story_tags_hint(story_tags)

    # Context accumulation (简单片段积累，三圈记忆策略会在 build_scene_context 里处理跨章前文)
    chapters_processed = 0
    scriptwriter_continuity: list[CompletedPromptTurn] = []

    for i in range(start_chapter_index, len(chapter_nodes)):
        if request is not None and await request.is_disconnected():
            stop_event.set()
            update_state(
                "interrupted",
                nextChapterIndex=current_chapter_index if current_chapter_index is not None else start_chapter_index,
                availableResumeChapterIndex=current_chapter_index if current_chapter_index is not None else start_chapter_index,
                availableResumeSceneIndex=current_scene_index if current_scene_index is not None else start_scene_index,
                availableRestartChapterIndex=current_chapter_index if current_chapter_index is not None else start_chapter_index,
                lastError="",
            )
            yield semantic_sse_data(
                "cancelled",
                message="自动撰写任务已取消",
                **on_cancelled("自动撰写任务已取消"),
            )
            return

        chapter = chapter_nodes[i]
        chapter_num = chapter.get("chapter", i + 1)
        raw_chapter_title = chapter.get("title", f"Chapter {chapter_num}")
        try:
            chapter_title = canonical_chapter_display_name(raw_chapter_title, int(chapter_num))
        except (TypeError, ValueError) as exc:
            message = f"自动写作已停止：大纲中的章节命名无效（{exc}）"
            update_state(
                "error",
                nextChapterIndex=i,
                availableResumeChapterIndex=i,
                availableResumeSceneIndex=0,
                availableRestartChapterIndex=i,
                lastError=message,
            )
            yield semantic_sse_data("error", message=message, **on_error(message))
            return
        scenes = chapter.get("children", [])
        current_chapter_index = i
        current_chapter_title = chapter_title
        current_scene_index = None
        current_scene_title = ""
        # 第一章：如果有 start_scene_index，跳过小于它的场景
        effective_start_scene = start_scene_index if i == start_chapter_index else 0

        update_state(
            "running",
            nextChapterIndex=i,
            availableResumeChapterIndex=i,
            availableResumeSceneIndex=effective_start_scene,
            availableRestartChapterIndex=i,
            lastError="",
        )

        yield semantic_sse_data(
            "chapter_start",
            chapter_index=i,
            chapter_title=chapter_title,
            **on_progress(
                f"开始章节：{chapter_title}", stage="chapter_start", chapterIndex=i
            ),
        )

        # Determine existing content or start fresh?
        # For auto-write, we generally assume we are writing fresh or overwriting.

        for scene_idx, scene in enumerate(scenes):
            if scene_idx < effective_start_scene:
                continue

            # 每个 story_unit 都从调研阶段开始；上一场的写入状态不能泄漏到下一场。
            write_started = False
            if request is not None and await request.is_disconnected():
                stop_event.set()
                update_state(
                    "interrupted",
                    nextChapterIndex=i,
                    availableResumeChapterIndex=i,
                    availableResumeSceneIndex=scene_idx,
                    availableRestartChapterIndex=i,
                    lastError="",
                )
                yield semantic_sse_data(
                    "cancelled",
                    message="自动撰写任务已取消",
                    **on_cancelled("自动撰写任务已取消"),
                )
                return

            raw_scene_title = scene.get("title", f"场景 {scene_idx + 1}")
            try:
                scene_title = canonical_scene_display_name(
                    raw_scene_title,
                    int(chapter_num),
                    int(scene_idx) + 1,
                )
            except (TypeError, ValueError) as exc:
                message = f"自动写作已停止：大纲中的章节或场景命名无效（{exc}）"
                update_state(
                    "error",
                    nextChapterIndex=i,
                    availableResumeChapterIndex=i,
                    availableResumeSceneIndex=scene_idx,
                    availableRestartChapterIndex=i,
                    lastError=message,
                )
                yield semantic_sse_data("error", message=message, **on_error(message))
                return
            scene_desc = scene.get("description", "")
            key_dialogues = scene.get("key_dialogues", [])
            current_scene_index = scene_idx
            current_scene_title = scene_title
            
            # Prepare file path for this specific scene
            filename = build_scene_output_filename(chapter_num, chapter_title, scene_idx, scene_title, export_format)
            try:
                filepath, _, _ = resolve_planned_scene_file_path(
                    stories_path,
                    int(chapter_num),
                    int(scene_idx) + 1,
                    scene_title,
                    chapter_dir_name=chapter_title,
                    file_format=export_format,
                )
            except DuplicateSceneIdentityError as exc:
                message = f"自动写作已停止：{exc}。请先在作品管理器中确认并整理重复文件。"
                update_state(
                    "error",
                    nextChapterIndex=i,
                    availableResumeChapterIndex=i,
                    availableResumeSceneIndex=scene_idx,
                    availableRestartChapterIndex=i,
                    lastError=message,
                )
                yield semantic_sse_data("error", message=message, **on_error(message))
                return
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            filename = os.path.basename(filepath)
            display_filename = strip_story_filename_meta(filename)
            
            update_state(
                "running",
                nextChapterIndex=i,
                availableResumeChapterIndex=i,
                availableResumeSceneIndex=scene_idx,
                availableRestartChapterIndex=i,
                lastSavedFilename=display_filename if os.path.exists(filepath) else "",
            )
            dialogues_str = ""
            if key_dialogues:
                dialogues_str = "\n\n【关键对话/剧情方向】\n" + "\n".join(
                    [f"- {d}" for d in key_dialogues]
                )

            # Construct Prompt Context
            # We provide:
            # 1. Overall Story Context (from Outline Summary + Accumulation)
            # 2. Current Chapter Goal
            # 3. Current Scene Goal

            # ── 用统一组装器构建三圈记忆前文 ───────────────────────────────────
            context_str = build_scene_context(
                user_id,
                project_name,
                current_chapter_index=i,
                current_scene_index=scene_idx,
                chapter_meta=chapter,
                scene_meta=scene,
                chr_map=chr_map,
            )
            
            # ── 注入项目级故事主题参数（POV 等）──────────────────────────────
            # 将 story_tags_block 前置到 context_str，确保 POV 等关键参数在上下文最前面
            if story_tags_block:
                context_str = story_tags_block + "\n\n" + context_str

            # 场景元数据（从大纲 > 行解析）
            scene_meta_parts = []
            scene_mood = scene.get("mood", "")
            if scene_mood:
                scene_meta_parts.append(f"情绪：{scene_mood}")
            scene_tension = scene.get("tension", "")
            if scene_tension:
                scene_meta_parts.append(f"张力：{scene_tension}")
            scene_characters = scene.get("characters", [])
            if scene_characters:
                scene_meta_parts.append(f"登场：{', '.join(scene_characters)}")
            scene_meta_str = (" | " + " | ".join(scene_meta_parts)) if scene_meta_parts else ""

            scene_goal = f"""【当前{terms['unit']}任务】
{terms['unit']}名：{scene_title}{scene_meta_str}
{terms['unit']}描述：{scene_desc}{dialogues_str}
当前{terms['group']}目标：{chapter_title} — {chapter.get('description', '')}
请撰写本{terms['unit']}的完整正文。
"""

            update_state(
                "running",
                phase="prewrite",
                phaseMessage=PREWRITE_STATUS_MESSAGE,
                nextChapterIndex=i,
                availableResumeChapterIndex=i,
                availableResumeSceneIndex=scene_idx,
            )
            yield semantic_sse_data(
                "prewrite",
                chapter_index=i,
                chapter_title=chapter_title,
                scene_index=scene_idx,
                scene_title=scene_title,
                **on_progress(
                    PREWRITE_STATUS_MESSAGE,
                    stage="prewrite",
                    chapterIndex=i,
                    sceneIndex=scene_idx,
                ),
            )

            write_attempt = 0

            def report_prewrite_tool(tool_name: str) -> None:
                """更新可恢复状态，并把当前调查或落盘工具送入观察者日志。

                落盘工具（create_chapter / create_or_rewrite_script）是本场首次
                被调用时，才把 phase 从 prewrite 翻转为 writing：此前的所有
                model_request 轮次都属于调研，绝不能显示为“正在生成正文”。
                """
                nonlocal write_started
                normalized_tool_name = str(tool_name or "").strip()
                is_writing_tool = normalized_tool_name in {
                    "create_chapter",
                    "create_or_rewrite_script",
                }
                if is_writing_tool:
                    write_started = True
                phase_message = (
                    f"正在撰写：{chapter_title} - {scene_title}"
                    if is_writing_tool
                    else PREWRITE_STATUS_MESSAGE
                )
                update_state(
                    "running",
                    phase="writing" if write_started else "prewrite",
                    phaseMessage=phase_message,
                    phaseEvent="tool_started",
                    phaseToolName=normalized_tool_name,
                    phaseResult="",
                )
                if prewrite_tool_callback is not None:
                    prewrite_tool_callback({
                        "tool_name": normalized_tool_name,
                        "attempt": write_attempt,
                        "max_attempts": PREWRITE_MAX_REQUESTS,
                        "write_started": write_started,
                        "chapter_index": i,
                        "scene_index": scene_idx,
                        "chapter_title": chapter_title,
                        "scene_title": scene_title,
                    })

            def report_creation_lifecycle(payload: Dict[str, Any]) -> None:
                """把 invoke 与工具执行状态收口到持久化快照和 SSE 回放日志。

                调研 / 落盘的判定只看落盘工具是否已被调用（write_started），
                而不看事件名：model_request_* 在调研轮次同样会触发，把它们
                直接标为 writing 是旧的误导根因。
                """
                nonlocal write_started, write_attempt
                event = str(payload.get("event") or "").strip()
                tool_name = str(payload.get("tool_name") or "").strip()
                error = str(payload.get("error") or "").strip()
                backend_reason = str(payload.get("backend_reason") or "").strip()
                backend_code = str(payload.get("backend_code") or "").strip()
                if event == "model_request_failed" and error:
                    from agents.error_formatting import format_ai_error

                    error = format_ai_error(RuntimeError(error))
                    payload = {**payload, "error": error}
                result = str(payload.get("result") or "").strip()
                attempt = int(payload.get("attempt") or 0)
                max_attempts = int(payload.get("max_attempts") or 0)
                if event == "model_request_started":
                    write_attempt = max(write_attempt, attempt)
                # 工具开始事件继续由既有 on_tool_progress 管线发送，避免重复日志帧。
                if event == "tool_started":
                    return
                if tool_name in {
                    "create_chapter",
                    "create_or_rewrite_script",
                }:
                    write_started = True
                if event == "model_request_failed" and not backend_reason:
                    backend_reason = "llm_error"
                if event == "tool_failed" and not backend_reason:
                    backend_reason = "tool_failure"
                # 落盘动作是单次原子写入：成功即结束本场，不存在“正文分段”。
                # 调研轮次上限（max_attempts）只约束“第几次调研请求”，
                # 落盘行永远不带轮次计数。
                if tool_name in {
                    "create_chapter",
                    "create_or_rewrite_script",
                }:
                    is_writing_event = True
                else:
                    is_writing_event = write_started
                state_fields: Dict[str, Any] = {
                    "phase": "writing" if is_writing_event else "prewrite",
                    "phaseEvent": event,
                    "phaseToolName": tool_name,
                    "phaseResult": result,
                    "phaseAttempt": attempt,
                    "phaseMaxAttempts": max_attempts,
                    "backendReason": backend_reason,
                    "backendCode": backend_code,
                }
                if event in {"model_request_started", "model_request_failed", "tool_failed"}:
                    state_fields["phaseError"] = error
                update_state(
                    "running",
                    **state_fields,
                )
                if prewrite_tool_callback is not None:
                    prewrite_tool_callback({
                        **payload,
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "write_started": write_started,
                        "backend_reason": backend_reason,
                        "backend_code": backend_code,
                        "chapter_index": i,
                        "scene_index": scene_idx,
                        "chapter_title": chapter_title,
                        "scene_title": scene_title,
                    })

            scene_started_at = time.time()
            try:
                prewrite_result = await asyncio.to_thread(
                    run_autonomous_scriptwriter_creation,
                    ScriptwriterPreWriteRequest(
                        user_id=user_id,
                        project_name=project_name,
                        task_description=scene_goal,
                        chapter_name=str(raw_chapter_title or "").strip(),
                        scene_name=str(raw_scene_title or "").strip(),
                        scene_guidance=scene_desc,
                        scene_characters=[str(name) for name in scene_characters],
                        full_outline=full_outline,
                        available_context=context_str,
                        worldview=worldview,
                        roles=roles,
                        style_profile=format_style_profile_for_prompt(style_profile),
                        story_tags=story_tags_block,
                        chr_reference=writer._build_chr_reference(chr_map),
                        export_format=export_format,
                        target_chars=(
                            story_tags.get("scene_target_chars")
                            if isinstance(story_tags.get("scene_target_chars"), int)
                            else None
                        ),
                    ),
                    agent=writer,
                    on_tool_progress=report_prewrite_tool,
                    on_lifecycle_event=report_creation_lifecycle,
                    completed_turns=tuple(scriptwriter_continuity),
                )
                if not prewrite_result.saved:
                    raise RuntimeError(
                        prewrite_result.blocked_reason
                        or "编剧在 4 次请求内未完成当前场景落盘。"
                    )
            except Exception as e:
                from agents.error_formatting import format_ai_error

                message = format_ai_error(e)
                update_state(
                    "error",
                    phase="",
                    phaseEvent="failed",
                    phaseError=message,
                    nextChapterIndex=i,
                    availableResumeChapterIndex=i,
                    availableResumeSceneIndex=scene_idx,
                    availableRestartChapterIndex=i,
                    lastError=message,
                )
                yield semantic_sse_data("error", message=message, **on_error(message))
                return
            if prewrite_result.continuity_turn is not None:
                scriptwriter_continuity.append(prewrite_result.continuity_turn)
                del scriptwriter_continuity[:-SCRIPTWRITER_CONTINUITY_MAX_TURNS]

            update_state(
                "running",
                phase="writing",
                phaseMessage=f"正在撰写：{chapter_title} - {scene_title}",
            )
            yield semantic_sse_data(
                "writing_scene",
                chapter_index=i,
                chapter_title=chapter_title,
                scene_index=scene_idx,
                scene_title=scene_title,
                **on_progress(
                    f"正在撰写：{chapter_title} - {scene_title}",
                    stage="scene_start",
                    chapterIndex=i,
                    sceneIndex=scene_idx,
                ),
            )

            try:
                saved_payload = prewrite_result.saved_payload or {}
                saved_relative_path = str(saved_payload.get("path") or "").strip()
                if not saved_relative_path:
                    raise RuntimeError(f"编剧已返回落盘成功，但没有提供{terms['unit']}文件路径。")

                stories_root = os.path.abspath(stories_path)
                saved_absolute_path = os.path.abspath(
                    os.path.join(stories_root, saved_relative_path)
                )
                if os.path.commonpath((stories_root, saved_absolute_path)) != stories_root:
                    raise RuntimeError(f"编剧返回的{terms['unit']}文件路径超出项目故事目录。")
                if not os.path.isfile(saved_absolute_path):
                    raise RuntimeError(
                        f"编剧报告已落盘，但找不到{terms['unit']}文件：{saved_relative_path}"
                    )
                with open(saved_absolute_path, "r", encoding="utf-8") as saved_file:
                    current_scene_full_text = saved_file.read().strip()

                filepath = saved_absolute_path
                filename = os.path.basename(filepath)
                display_filename = strip_story_filename_meta(filename)
                arc_text = prewrite_result.written_content.strip() or current_scene_full_text
                total_chars = _require_nonempty_scene_body(
                    current_scene_full_text,
                    export_format,
                )
                elapsed = max(time.time() - scene_started_at, 0.0)
                avg_speed = total_chars / elapsed if elapsed > 0 else 0

                if request is not None and await request.is_disconnected():
                    stop_event.set()
                if stop_event.is_set():
                    update_state(
                        "interrupted",
                        nextChapterIndex=i,
                        availableResumeChapterIndex=i,
                        availableResumeSceneIndex=scene_idx,
                        availableRestartChapterIndex=i,
                        lastSavedFilename=display_filename,
                        lastError="",
                    )
                    yield semantic_sse_data(
                        "cancelled",
                        message="自动撰写任务已取消",
                        **on_cancelled("自动撰写任务已取消"),
                    )
                    return

                # 落盘工具已经保存了最终正文，这里只发送完成事件，不再次生成或覆盖文件。
                # 注意 avg_speed 是落盘瞬间的“本场字数/耗时”事后统计，不是流式测速：
                # 工具调用是非流式的，invoke 一次性返回完整工具结果。
                update_state(
                    "running",
                    nextChapterIndex=i,
                    availableResumeChapterIndex=i,
                    availableResumeSceneIndex=scene_idx,
                    availableRestartChapterIndex=i,
                    lastSavedFilename=display_filename if os.path.exists(filepath) else "",
                    lastError="",
                    lastSceneChars=total_chars,
                    lastSceneElapsed=round(elapsed, 1),
                    lastSceneSpeed=round(avg_speed, 1),
                    lastScenePreview=arc_text[:100] + "..." if len(arc_text) > 100 else arc_text,
                )
                yield semantic_sse_data(
                    "scene_completed",
                    scene_title=scene_title,
                    preview=arc_text[:100] + "..." if len(arc_text) > 100 else arc_text,
                    total_chars=total_chars,
                    elapsed=round(elapsed, 1),
                    avg_speed=round(avg_speed, 1),
                    **merge_semantics(
                        on_progress(
                            f"场景完成：{scene_title}", stage="scene_completed"
                        ),
                        on_stats(
                            chars=total_chars,
                            speed=round(avg_speed, 1),
                            elapsed=round(elapsed, 1),
                            label=f"场景完成 · {total_chars} 字 · 平均 {round(avg_speed, 1)} 字/秒",
                        ),
                    ),
                )

            except Exception as e:
                print(f"Error writing scene {scene_title}: {e}")
                message = str(e)
                update_state(
                    "error",
                    nextChapterIndex=i,
                    availableResumeChapterIndex=i,
                    availableResumeSceneIndex=scene_idx,
                    availableRestartChapterIndex=i,
                    lastSavedFilename=display_filename,
                    lastError=message,
                )
                yield semantic_sse_data("error", message=message, **on_error(message))
                return

            # Save file after each scene (progressive save)
            if stop_event.is_set():
                update_state(
                    "interrupted",
                    nextChapterIndex=i,
                    availableResumeChapterIndex=i,
                    availableResumeSceneIndex=scene_idx,
                    availableRestartChapterIndex=i,
                    lastSavedFilename=display_filename if os.path.exists(filepath) else "",
                    lastError="",
                )
                yield semantic_sse_data(
                    "cancelled",
                    message="自动撰写任务已取消",
                    **on_cancelled("自动撰写任务已取消"),
                )
                return
            source_rel_path = saved_relative_path

            review = record_auto_write_scene_review(
                user_id=user_id,
                project_name=project_name,
                critic=critic if auto_review else None,
                scene_text=current_scene_full_text,
                context_text=context_str,
                guidance_text=scene_goal,
                scene_title=scene_title,
                source_rel_path=source_rel_path,
                worldview=worldview,
                roles=roles,
                style_profile=style_profile,
                story_tags_block=story_tags_block,
            )
            if review:
                yield semantic_sse_data(
                    "scene_reviewed",
                    scene_title=scene_title,
                    filename=display_filename,
                    decision=review.get("decision") or "",
                    overallGrade=review.get("overall_grade") or "",
                    rewriteRequired=bool(review.get("rewrite_required")),
                    fixTicketCount=len(review.get("fix_tickets") or []),
                    **on_progress(
                        f"场景已审稿：{scene_title}",
                        stage="scene_reviewed",
                    ),
                )

            if display_filename not in generated_scene_files:
                generated_scene_files.append(display_filename)

            next_resume_chapter_index = i
            next_resume_scene_index = scene_idx + 1
            if next_resume_scene_index >= len(scenes):
                next_resume_chapter_index = i + 1
                next_resume_scene_index = 0
            
            update_state(
                "running",
                nextChapterIndex=next_resume_chapter_index,
                availableResumeChapterIndex=next_resume_chapter_index,
                availableResumeSceneIndex=next_resume_scene_index,
                availableRestartChapterIndex=i,
                lastSavedFilename=display_filename,
                generatedFiles=generated_scene_files,
                lastError="",
            )
            
            # Send progressive scene saved event to inform frontend about new scene file
            yield semantic_sse_data(
                "scene_saved",
                filename=display_filename,
                completedScenes=len(generated_scene_files),
                totalScenes=total_scenes_count,
                **on_progress(f"场景已保存：{display_filename}", stage="scene_saved"),
            )

        # Notify chapter saved (all scenes done)
        update_state(
            "running",
            lastCompletedChapterIndex=i,
            lastCompletedChapterTitle=chapter_title,
            nextChapterIndex=i + 1,
            availableResumeChapterIndex=i + 1,
            availableResumeSceneIndex=0,
            availableRestartChapterIndex=i,
            currentSceneIndex=None,
            currentSceneTitle="",
            lastError="",
        )
        yield semantic_sse_data(
            "chapter_saved",
            **on_progress(f"所有场景已完成：{chapter_title}", stage="chapter_saved"),
        )

        chapters_processed += 1

        # Check Mode
        if mode == "chapter_by_chapter":
            update_state(
                "chapter_paused",
                nextChapterIndex=i + 1,
                availableResumeChapterIndex=i + 1,
                availableResumeSceneIndex=0,
                availableRestartChapterIndex=i,
                currentSceneIndex=None,
                currentSceneTitle="",
                lastError="",
            )
            yield semantic_sse_data(
                "paused",
                next_chapter_index=i + 1,
                restart_chapter_index=i,
                **on_progress("当前章节已完成，任务暂停", stage="paused"),
            )
            return

    update_state(
        "complete",
        nextChapterIndex=len(chapter_nodes),
        availableResumeChapterIndex=None,
        availableResumeSceneIndex=None,
        availableRestartChapterIndex=None,
        currentSceneIndex=None,
        currentSceneTitle="",
        lastError="",
        completedAt=patch_auto_write_state(user_id, project_name).get("updatedAt", ""),
    )
    yield semantic_sse_data(
        "complete",
        completedScenes=total_scenes_count,
        totalScenes=total_scenes_count,
        **on_done("全部自动撰写任务已完成"),
    )


from agents.auto_write_service import configure_auto_write_runner

configure_auto_write_runner(generate_script_stream)


@auto_write_router.post("/api/outline/{project_name}/auto-write-start")
async def auto_write_start(
    project_name: str, request: Request, user: dict = Depends(get_current_user),
):
    """手动触发 Auto-Write，以后台线程执行（不受前端断连影响），
    同时创建进度队列供 SSE 观察者读取实时进度。"""
    user_id = str(user["user_id"])
    data = await request.json() or {}
    mode = data.get("mode", "chapter_by_chapter")
    start_chapter_index = data.get("start_chapter_index", 0)
    start_scene_index = data.get("start_scene_index", 0)
    export_format = _resolve_export_format(user_id, project_name)
    auto_review = bool(data.get("auto_review", False))

    # 加载大纲
    from story.outline_parser import parse_outline_markup
    outline_path = os.path.join(get_project_path(user_id, project_name), "大纲.txt")
    if not os.path.exists(outline_path):
        return {"success": False, "error": "Outline not found"}

    with open(outline_path, "r", encoding="utf-8") as f:
        outline = parse_outline_markup(f.read())

    from agents.auto_write_service import start_auto_write_background

    result = start_auto_write_background(
        user_id=user_id,
        project_name=project_name,
        outline=outline,
        mode=mode,
        start_chapter_index=start_chapter_index,
        start_scene_index=start_scene_index,
        export_format=export_format,
        context_strategy="accumulate",
        auto_review=auto_review,
        from_director=False,
    )
    if not result.started:
        return {"success": False, "error": result.error}

    return {"success": True, "export_format": export_format}


@auto_write_router.get("/api/outline/{project_name}/auto-write-progress-stream")
async def auto_write_progress_stream(
    project_name: str,
    afterSeq: int = 0,
    user: dict = Depends(get_current_user),
):
    """SSE 观察者端点：只读取进度队列，不控制任务生命周期。
    前端断连后任务继续运行，可重新连接此端点恢复实时流。"""
    from agents.auto_write_service import observe_auto_write_progress

    return StreamingResponse(
        observe_auto_write_progress(str(user["user_id"]), project_name, after_seq=afterSeq),
        media_type="text/event-stream",
    )


@auto_write_router.get("/api/outline/{project_name}/auto-write-state")
async def get_auto_write_state(
    project_name: str,
    user: dict = Depends(get_current_user),
):
    user_id = str(user["user_id"])
    resolved_export_format = _resolve_export_format(user_id, project_name)
    from story.outline_parser import parse_outline_markup
    outline_path = os.path.join(get_project_path(user_id, project_name), "大纲.txt")
    if not os.path.exists(outline_path):
        return {
            **build_auto_write_state_payload(user_id, project_name, {"nodes": []}, export_format=resolved_export_format),
            "outlineExists": False,
        }

    with open(outline_path, "r", encoding="utf-8") as f:
        outline = parse_outline_markup(f.read())

    return {
        **build_auto_write_state_payload(
            user_id,
            project_name,
            outline,
            export_format=resolved_export_format,
        ),
        "outlineExists": True,
    }


@auto_write_router.post("/api/outline/{project_name}/auto-write-pause")
async def auto_write_pause(
    project_name: str, user: dict = Depends(get_current_user)
):
    """
    前端点击终止/暂停触发，向正在运行的该项目生成引擎发送停止信号。
    """
    user_id = str(user["user_id"])
    
    from agents.auto_write_service import stop_auto_write

    stop_auto_write(user_id, project_name)
    
    # 兜底：修改状态为中断（防止孤儿线程无法更新或早已消失）
    patch_auto_write_state(
        user_id, project_name, 
        status="interrupted", 
        lastError="用户已中断写作",
        acknowledged=True,  # 用户手动中断，下次不再弹出提示
    )

    return {"success": True}


@auto_write_router.post("/api/outline/{project_name}/auto-write-acknowledge")
async def auto_write_acknowledge(
    project_name: str, user: dict = Depends(get_current_user)
):
    """
    前端关闭遮罩时调用，标记 acknowledged=True，下次进入页面不再弹出提示。
    """
    user_id = str(user["user_id"])
    patch_auto_write_state(user_id, project_name, acknowledged=True)
    return {"success": True}
