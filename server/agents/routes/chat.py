"""
Chat / Session History API - 通用会话机制
"""

# ─────────────────────────────────────────────────────────────────────────────
# 关于Segment 时序记录
#
# 【背景与问题】
#   聊天流（chat_stream）本质上是 AI 在时间线上交替输出多种内容的序列，例如：
#     推理(reasoning_delta) → 正文(assistant_delta) → 工具(tool_*) → 推理 → 正文
#   ChatTaskEntry + ChatStreamAccumulator 是聊天流恢复的唯一状态源：
#   - 每个事件先写入 append-only event_log，并同步进入 accumulator；
#   - accumulator 维护 content / reasoning / tool_traces / segments / stream_seq；
#   - 运行中 checkpoint 到同一条 assistant 消息，完成时写最终 metadata；
#   - 前端 chatStore.ts 的历史归一化会优先读取 metadata.segments，
#     从而在刷新后完整还原交错时序的 UI 渲染效果。
#
# 【Segment 数据规范（供开发者遵循）】
#   segments 是一个有序列表，每个元素为 Dict：
#
#   推理段落：
#     { "type": "reasoning", "text": "AI 的思考过程..." }
#
#   正文段落：
#     { "type": "text", "text": "AI 对用户说的话..." }
#
#   工具调用段落：
#     { "type": "tool_trace", "tool_name": "update_character",
#       "status": "finished",        # started | running | finished | failed | cancelled
#       "started_at": 1710450001.0,  # Unix 时间戳（秒，三位小数）
#       "finished_at": 1710450003.5,
#       "duration": 2.5,             # 秒
#       "source_agent": "",          # 嵌套调用时为子 Agent ID，直接调用为空
#       "nested": False,             # 是否为委派后的嵌套调用
#       "_seg_id": "update_character::agent_lorebook:1"  # 跨事件精确匹配的唯一标识
#     }
#
#   规则：
#   - reasoning / text 类型：相邻同类 segment 会合并，避免碎片化。
#   - tool_trace 类型：以 _seg_id 唯一标识同一次调用，finished/failed 事件
#     会原地更新对应项，而非追加新项（精确还原单次工具调用完整生命周期）。
#   - 这份数据只用于 UI 渲染时序还原，tool_traces 字段仍保留用于聚合统计。
#
# 【新增聊天事件接入规范】
#   若将来新增 chat_stream 事件类型，优先扩展 chat_persistence.py 的
#   ChatStreamAccumulator / _collect_segment_from_event / _collect_tool_trace_from_event，
#   不要在路由层重新维护一套 buf / segments / tool_trace_map。
# ─────────────────────────────────────────────────────────────────────────────
from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from typing import Any, Callable, Dict
import asyncio
import threading
import time
import json
from sqlalchemy import func

from core.auth import get_current_user
from core.models import UserInfoSession, ChatMessage
from core.request_context import (
    current_export_format,
    get_current_export_format,
    get_current_project_name,
    get_current_locale,
    resolve_project_name,
    reset_current_locale,
    set_current_inspiration_context,
    set_current_locale,
    set_current_export_format,
    current_llm_usage_context,
    current_llm_usage_reporter,
    reset_current_chat_session,
    set_current_chat_session,
)

from agents.agent_factory import create_agent_instance
from agents.context_budget import (
    CHAT_HISTORY_FETCH_LIMIT,
    CONTEXT_CHECKPOINT_READY_EVENT,
    NonRetryableChatError,
)
from agents.chat_manager import ChatManager

from .schemas import (
    ChatSendRequest, ChatMessageEditRequest, ChatTaskCancelRequest,
    ChatMessageAttachmentRemoveRequest,
    ChatContextCompactRequest,
    _resolve_effective_active_context,
)
from .chat_attachment import (
    build_imported_file_context_label as _build_imported_file_context_label,
    build_user_message_metadata as _build_user_message_metadata,
    expand_active_context_with_attachments as _expand_active_context_with_attachments,
    extract_imported_files_meta as _extract_imported_files_meta,
)
from .streaming_utils import iterate_sync_iterable_in_thread
from .chat_task import (
    ChatTaskEntry,
    _make_task_key,
    register_task,
    get_task_by_parts,
    cancel_task,
    update_task_status,
    cleanup_task,
    wait_for_task_exit,
    list_recent_tasks,
    purge_project_tasks,
    build_task_status_payload,
)
chat_router = APIRouter()


def _apply_request_runtime_meta(
    active_meta: Dict[str, Any] | None,
    *,
    user_id: str | None = None,
    project_name: str | None = None,
) -> None:
    inspiration_id = None
    export_format = None
    if isinstance(active_meta, dict):
        inspiration_id = active_meta.get('inspirationId') or active_meta.get('inspiration_id')
    if user_id and project_name:
        try:
            from core.project_settings import get_workspace_mode

            export_format = "novel" if get_workspace_mode(str(user_id), str(project_name)) == "novel" else "arc"
        except Exception:
            export_format = None
    set_current_inspiration_context(str(inspiration_id) if inspiration_id else None)
    set_current_export_format(export_format)


def _run_chat_background_context(
    *,
    user_id: str,
    project_name: str,
    is_admin: bool,
    locale: str,
    llm_usage_context: str,
    llm_usage_reporter: Callable[[Dict[str, Any]], None] | None = None,
    chat_agent_id: str,
    chat_context_key: str,
    export_format: str,
    callback: Any,
) -> Any:
    """在聊天后台线程中恢复请求级上下文。"""
    from core.request_context import (
        current_project_name,
        current_scriptwriter_prewrite_receipt,
        current_user_id,
        current_user_is_admin,
    )

    current_user_id.set(str(user_id))
    # 后台线程不能只恢复 user_id：管理员是否可使用系统托管 Key
    # 取决于这个标记。漏掉它会让站长在关闭共享后被误判为普通用户。
    current_user_is_admin.set(bool(is_admin))
    current_project_name.set(project_name)
    export_format_token = current_export_format.set(
        "novel" if str(export_format or "").strip().lower() == "novel" else "arc"
    )
    prewrite_receipt_token = current_scriptwriter_prewrite_receipt.set({})
    locale_token = set_current_locale(locale)
    usage_token = current_llm_usage_context.set(llm_usage_context)
    usage_reporter_token = current_llm_usage_reporter.set(llm_usage_reporter)
    chat_tokens = set_current_chat_session(chat_agent_id, chat_context_key)
    try:
        return callback()
    finally:
        reset_current_chat_session(chat_tokens)
        current_llm_usage_reporter.reset(usage_reporter_token)
        current_llm_usage_context.reset(usage_token)
        reset_current_locale(locale_token)
        current_scriptwriter_prewrite_receipt.reset(prewrite_receipt_token)
        current_export_format.reset(export_format_token)


def _as_stream_event(delta) -> dict:
    if isinstance(delta, dict):
        return delta
    if isinstance(delta, str):
        return {"event": "assistant_delta", "text": delta}
    return {"event": "assistant_delta", "text": str(delta)}


def _serialize_stream_event(delta) -> str:
    event = _as_stream_event(delta)
    return json.dumps(event, ensure_ascii=False) + "\n"


def _coerce_stream_error_text(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ('message', 'error', 'data', 'text'):
            text = _coerce_stream_error_text(value.get(key))
            if text:
                return text
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value).strip()
    if isinstance(value, (list, tuple)):
        parts = [_coerce_stream_error_text(item) for item in value]
        return ''.join(part for part in parts if part).strip()
    return str(value).strip()


def _mark_chat_task_error(
    cm: ChatManager,
    entry: ChatTaskEntry,
    task_key: str,
    error_payload: Any,
    *,
    retry_count: int = 0,
) -> str:
    error_message = _coerce_stream_error_text(error_payload) or '聊天生成失败'
    if entry.is_terminalized():
        return entry.error_message
    entry.error_message = error_message
    # 终态只能由 _finalize_chat_task 发布。若先把 status 改为 error，观察器会
    # 在 task_done 写入事件日志前退出，前端无法可靠收口。
    return error_message


def _persist_context_checkpoint_safely(
    cm: ChatManager,
    *,
    agent_id: str,
    context_key: str,
    checkpoint: Dict[str, Any] | None,
) -> Dict[str, Any] | None:
    """checkpoint 是优化层；落盘故障不能重放已经成功的模型或工具调用。"""
    if not checkpoint:
        return None
    try:
        return cm.persist_context_checkpoint(
            agent_id=agent_id,
            context_key=context_key,
            checkpoint=checkpoint,
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "持久化聊天上下文 checkpoint 失败: agent=%s context=%s",
            agent_id,
            context_key,
        )
        return None


# ── 自动重试统一收口 ─────────────────────────────────────────────
#
# 设计原则：
# 1. chat_stream 的两类失败都必须触发重试：
#    a) generator 抛 Python 异常（罕见，多是协程中断 / 强制结束）
#    b) generator yield 出 ``{"event": "error", ...}``（常见，上游 LLM/工具
#       异常被捕获后通过事件传递）
# 2. 重试期间静默吃掉中间 error：仅向前端推 ``retry_attempt`` 事件，禁止
#    把中间错误事件落到 ``entry.event_log``，否则前端会立即清零 ``sending``
#    并在 observer 重连回放时再次"假完结"。
# 3. 仅最后一次失败才正式落盘 error 事件 + 触发 ``_mark_chat_task_error``。
# 4. ``send_chat_message_stream`` 与 ``edit_chat_message_stream`` 必须共用
#    本函数，保证两条入口的容错 / 重试语义完全一致。
_CHAT_MAX_RETRIES = 3
_CHAT_RETRY_DELAY = 5.0

_TOOL_EXECUTION_EVENTS = {
    'tool_exec_started',
    'tool_exec_finished',
    'tool_exec_failed',
}


def _attempt_has_tool_execution(entry: ChatTaskEntry, after_seq: int) -> bool:
    """判断本次尝试是否已进入可能产生副作用的工具执行阶段。"""
    return any(
        event.get('event') in _TOOL_EXECUTION_EVENTS
        for event in entry.get_events_after(after_seq)
    )


def _run_chat_stream_with_retry(
    *,
    agent_inst: Any,
    message: str,
    history: list,
    active_context: Any,
    cm: ChatManager,
    entry: ChatTaskEntry,
    task_key: str,
    stop_event: threading.Event,
    max_retries: int = _CHAT_MAX_RETRIES,
    retry_delay: float = _CHAT_RETRY_DELAY,
) -> tuple[bool, str, int]:
    """统一驱动 ``agent.chat_stream`` 的执行 + 自动重试。

    返回 ``(terminated_early, final_error_message, retry_count)``：

    - ``terminated_early``: 因 ``stop_event`` 被触发而提前退出。
    - ``final_error_message``: 重试全部失败后的最终错误摘要；正常完成时为 ``''``。
    - ``retry_count``: 实际发生过的重试次数（0 表示一次成功）。
    """
    from .schemas import format_ai_error  # 局部导入避免循环依赖

    terminated_early = False
    final_error_message = ''
    retry_count = 0
    checkpoint_candidate: Dict[str, Any] | None = None

    for attempt in range(1, max_retries + 1):
        if stop_event.is_set() or entry.is_terminalized():
            terminated_early = True
            break
        # 重试前清空上一轮残留：accumulator 重置 + 推 snapshot 让前端 UI 复位
        if attempt > 1:
            entry.reset_for_retry()
            _checkpoint_chat_task(cm, entry, force=True, stream_status='running')
            entry.append_control_event(entry.build_snapshot())
        if stop_event.is_set() or entry.is_terminalized():
            terminated_early = True
            break

        last_error_summary = ''
        encountered_error = False
        attempt_start_seq = entry.next_seq

        try:
            for delta in agent_inst.chat_stream(
                message,
                history=history,
                active_context=active_context,
                stop_event=stop_event,
            ):
                if stop_event.is_set() or entry.is_terminalized():
                    terminated_early = True
                    break
                if not delta:
                    continue

                if isinstance(delta, dict) and delta.get('event') == CONTEXT_CHECKPOINT_READY_EVENT:
                    candidate = delta.get('checkpoint')
                    if isinstance(candidate, dict):
                        checkpoint_candidate = dict(candidate)
                    continue

                # ⚠️ 拦截 yield 出来的 error 事件：暂不落盘，由重试逻辑统一裁决
                #   这是状态唯一性的关键 —— 中间错误若被 append 进 event_log，
                #   observer 重连回放时会让前端误以为任务已终结。
                if isinstance(delta, dict) and delta.get('event') == 'error':
                    last_error_summary = _coerce_stream_error_text(delta) or '聊天生成失败'
                    encountered_error = True
                    if delta.get('retryable') is False:
                        entry.append_event(delta)
                        final_error_message = _mark_chat_task_error(
                            cm,
                            entry,
                            task_key,
                            delta,
                            retry_count=retry_count,
                        )
                        return terminated_early, final_error_message, retry_count
                    break

                event = entry.append_event(delta)
                if event.get('ignored_after_terminal'):
                    terminated_early = True
                    break
                event_type = event.get('event')
                _checkpoint_chat_task(
                    cm,
                    entry,
                    force=event_type in {
                        'tool_intent_started',
                        'tool_exec_started',
                        'tool_exec_finished',
                        'tool_exec_failed',
                    },
                    stream_status='running',
                )

            if stop_event.is_set() or entry.is_terminalized():
                terminated_early = True
                break

            if not encountered_error:
                # chat_stream 正常结束，跳出重试循环
                if checkpoint_candidate is None:
                    consume_candidate = getattr(agent_inst, 'consume_context_checkpoint_candidate', None)
                    if callable(consume_candidate):
                        checkpoint_candidate = consume_candidate()
                if checkpoint_candidate and hasattr(cm, 'persist_context_checkpoint'):
                    _persist_context_checkpoint_safely(
                        cm,
                        agent_id=entry.agent_id,
                        context_key=entry.context_key,
                        checkpoint=checkpoint_candidate,
                    )
                return terminated_early, final_error_message, retry_count

        except Exception as e:
            if stop_event.is_set() or entry.is_terminalized():
                terminated_early = True
                break
            if isinstance(e, NonRetryableChatError):
                error_event = e.to_event()
                entry.append_event(error_event)
                final_error_message = _mark_chat_task_error(
                    cm,
                    entry,
                    task_key,
                    error_event,
                    retry_count=retry_count,
                )
                return terminated_early, final_error_message, retry_count
            last_error_summary = format_ai_error(e)
            encountered_error = True

        if entry.is_terminalized():
            terminated_early = True
            break

        # 工具一旦开始执行，其文件写入等副作用无法由 accumulator.reset_for_retry
        # 回滚。此时禁止整轮重放，避免同一工具被重复执行。
        if encountered_error and _attempt_has_tool_execution(entry, attempt_start_seq):
            entry.append_event({'event': 'error', 'message': last_error_summary})
            final_error_message = _mark_chat_task_error(
                cm,
                entry,
                task_key,
                last_error_summary,
                retry_count=retry_count,
            )
            break

        # 走到这里说明该 attempt 触发了错误：要么走重试，要么落盘最终错误
        retry_count = attempt
        if attempt < max_retries:
            entry.append_event({
                'event': 'retry_attempt',
                'attempt': attempt,
                'max_retries': max_retries,
                'error_summary': last_error_summary,
            }, accumulate=False)
            update_task_status(
                task_key,
                'running',
                expected_task_id=entry.task_id,
                retry_count=attempt,
            )
            if stop_event.wait(retry_delay) or entry.is_terminalized():
                terminated_early = True
                break
        else:
            # 最后一次失败：正式落盘 error 事件 + 标记任务为 error
            if entry.is_terminalized():
                terminated_early = True
                break
            entry.append_event({'event': 'error', 'message': last_error_summary})
            final_error_message = _mark_chat_task_error(
                cm,
                entry,
                task_key,
                last_error_summary,
                retry_count=attempt,
            )

    return terminated_early, final_error_message, retry_count


_NDJSON_MEDIA_TYPE = 'application/x-ndjson; charset=utf-8'


def _init_chat_task_ledger(user_id: str, project_name: str, agent_id: str, context_key: str) -> None:
    """任务开始时从落盘恢复线索账本（内存态，热路径零 IO）。"""
    try:
        from agents.longread_store import init_task_ledger

        init_task_ledger(str(user_id), str(project_name), str(agent_id), str(context_key))
    except Exception:
        pass


def _persist_chat_task_ledger(user_id: str, project_name: str) -> None:
    """任务终态时一次性落盘线索账本；失败不影响任务收口。"""
    try:
        from agents.longread import LedgerStore
        from agents.longread_store import take_task_ledger

        ledger, key = take_task_ledger()
        if ledger is not None and key and ledger.entries:
            LedgerStore.save(str(user_id), str(project_name), key, ledger)
    except Exception:
        pass


def _append_longread_ledger_snapshot(
    user_id: str,
    project_name: str,
    agent_id: str,
    context_key: str,
    active_context: Any,
) -> Any:
    """把上轮沉淀的线索账本追加到本轮尾部（只追加，不改写历史）。

    位置固定在“附件/世界观注入块之后、本轮用户请求之前”，字节稳定；
    超过约 2K token 时只保留最新条目并注明省略，避免账本自身成为爆点。
    无账本时原样返回，零干扰。
    """
    try:
        from agents.longread import LedgerStore, ledger_key
        from core.project_settings import LONGREAD_LEDGER_SNAPSHOT_MAX_CHARS

        key = ledger_key(str(user_id), str(project_name), str(agent_id), str(context_key))
        ledger = LedgerStore.load(str(user_id), str(project_name), key)
        if not ledger.entries:
            return active_context
        rendered = ledger.render()
        snapshot_limit = max(2000, int(LONGREAD_LEDGER_SNAPSHOT_MAX_CHARS or 8000))
        if len(rendered) > snapshot_limit:
            kept: list[str] = []
            total = 0
            for item in reversed(ledger.entries):
                line = f"[{item.source_id} 窗口{item.chunk_index}] {item.clue}"
                if total + len(line) > snapshot_limit - 500:
                    break
                kept.append(line)
                total += len(line)
            kept.reverse()
            omitted = len(ledger.entries) - len(kept)
            rendered = "【线索账本（上轮沉淀，只追加）】\n" + "\n".join(kept)
            if omitted > 0:
                rendered += f"\n（另有更早 {omitted} 条已省略，需要时可按窗口号重读原文）"
        else:
            rendered = rendered.replace("【线索账本】", "【线索账本（上轮沉淀，只追加）】", 1)
        base = str(active_context or "").strip()
        return "\n\n".join(seg for seg in [base, rendered] if seg)
    except Exception:
        return active_context

# 旁路标记前缀：用于从工具返回文本中提取导演触发 Auto-Write 的结构化元数据
_SIDEBAND_PREFIX = "__director_auto_write_started__:"


def _extract_director_sideband(text: str):
    """从工具返回文本中提取导演触发 Auto-Write 的旁路元数据。

    若文本首行包含 __director_auto_write_started__:{json}，则：
    - 返回 (json_str, 去除首行后的剩余文本)
    否则返回 (None, 原文本)。
    """
    if not isinstance(text, str) or not text.startswith(_SIDEBAND_PREFIX):
        return None, text
    newline_pos = text.find("\n")
    if newline_pos == -1:
        meta_str = text[len(_SIDEBAND_PREFIX):]
        rest = ""
    else:
        meta_str = text[len(_SIDEBAND_PREFIX):newline_pos]
        rest = text[newline_pos + 1:]
    return meta_str.strip(), rest


_CHAT_CHECKPOINT_INTERVAL = 3.0


def _make_llm_usage_context(task_id: str) -> str:
    return f"chat_task:{task_id}"


def _publish_chat_task_llm_usage(entry: ChatTaskEntry, usage: Dict[str, Any]) -> None:
    """把单次真实用量累加到任务，并写入可重放 NDJSON 事件。"""
    if entry.is_terminalized():
        return
    snapshot = entry.record_llm_usage(usage)
    if entry.is_terminalized():
        return
    entry.append_control_event({
        "event": "llm_usage",
        "llm_usage": snapshot,
    })


def _collect_chat_task_llm_usage(entry: ChatTaskEntry) -> Dict[str, Any] | None:
    """Aggregate real LLM usage rows produced by this chat task."""
    usage_context = _make_llm_usage_context(entry.task_id)
    try:
        from llm.agen_matchbox import matchbox
        from llm.agen_matchbox.models import UsageLogEntry

        with matchbox().Session() as session:
            result = session.query(
                func.coalesce(func.sum(UsageLogEntry.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(UsageLogEntry.completion_tokens), 0).label("completion_tokens"),
                func.coalesce(func.sum(UsageLogEntry.total_tokens), 0).label("total_tokens"),
                func.coalesce(func.sum(UsageLogEntry.cached_prompt_tokens), 0).label("cached_prompt_tokens"),
                func.coalesce(func.sum(UsageLogEntry.cache_miss_prompt_tokens), 0).label("cache_miss_prompt_tokens"),
                func.count(UsageLogEntry.cache_miss_prompt_tokens).label("cache_stats_requests"),
                func.count(UsageLogEntry.id).label("requests"),
                func.coalesce(func.sum(1 - UsageLogEntry.success), 0).label("errors"),
            ).filter(
                UsageLogEntry.user_id == str(entry.user_id),
                UsageLogEntry.context_key == usage_context,
            ).first()
            agent_rows = session.query(
                UsageLogEntry.agent_name.label("agent_name"),
                func.coalesce(func.sum(UsageLogEntry.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(UsageLogEntry.completion_tokens), 0).label("completion_tokens"),
                func.coalesce(func.sum(UsageLogEntry.total_tokens), 0).label("total_tokens"),
                func.coalesce(func.sum(UsageLogEntry.cached_prompt_tokens), 0).label("cached_prompt_tokens"),
                func.coalesce(func.sum(UsageLogEntry.cache_miss_prompt_tokens), 0).label("cache_miss_prompt_tokens"),
                func.count(UsageLogEntry.cache_miss_prompt_tokens).label("cache_stats_requests"),
                func.count(UsageLogEntry.id).label("requests"),
                func.coalesce(func.sum(1 - UsageLogEntry.success), 0).label("errors"),
            ).filter(
                UsageLogEntry.user_id == str(entry.user_id),
                UsageLogEntry.context_key == usage_context,
            ).group_by(UsageLogEntry.agent_name).all()
    except Exception:
        return None

    requests = int(result.requests or 0) if result is not None else 0
    if requests <= 0:
        return None
    by_agent = {}
    for row in agent_rows or []:
        agent_name = str(row.agent_name or "").strip() or "unknown"
        cache_stats_available = bool(
            int(row.cached_prompt_tokens or 0) > 0
            or int(row.cache_stats_requests or 0) > 0
        )
        by_agent[agent_name] = {
            "prompt_tokens": int(row.prompt_tokens or 0),
            "completion_tokens": int(row.completion_tokens or 0),
            "total_tokens": int(row.total_tokens or 0),
            "cached_prompt_tokens": int(row.cached_prompt_tokens or 0),
            "cache_miss_prompt_tokens": int(row.cache_miss_prompt_tokens or 0),
            "cache_hit_rate": (
                (int(row.cached_prompt_tokens or 0) / int(row.prompt_tokens or 1))
                if cache_stats_available and int(row.prompt_tokens or 0) > 0
                else None
            ),
            "cache_stats_available": cache_stats_available,
            "requests": int(row.requests or 0),
            "errors": int(row.errors or 0),
        }

    cache_stats_available = bool(
        int(result.cached_prompt_tokens or 0) > 0
        or int(result.cache_stats_requests or 0) > 0
    )
    return {
        "prompt_tokens": int(result.prompt_tokens or 0),
        "completion_tokens": int(result.completion_tokens or 0),
        "total_tokens": int(result.total_tokens or 0),
        "cached_prompt_tokens": int(result.cached_prompt_tokens or 0),
        "cache_miss_prompt_tokens": int(result.cache_miss_prompt_tokens or 0),
        "cache_hit_rate": (
            (int(result.cached_prompt_tokens or 0) / int(result.prompt_tokens or 1))
            if cache_stats_available and int(result.prompt_tokens or 0) > 0
            else None
        ),
        "cache_stats_available": cache_stats_available,
        "requests": requests,
        "errors": int(result.errors or 0),
        "by_agent": by_agent,
        "source": "usage_log",
    }


def _merge_context_window_stats_with_usage(
    context_window_stats: Dict[str, Any] | None,
    llm_usage: Dict[str, Any] | None,
) -> Dict[str, Any] | None:
    if not isinstance(context_window_stats, dict):
        return None

    merged = dict(context_window_stats)
    if not isinstance(llm_usage, dict):
        return merged

    agent_id = str(
        merged.get("agent_id")
        or merged.get("agentId")
        or merged.get("source_agent")
        or merged.get("sourceAgent")
        or ""
    ).strip()
    if not agent_id:
        return merged

    by_agent = llm_usage.get("by_agent") or llm_usage.get("byAgent")
    if not isinstance(by_agent, dict):
        return merged

    agent_usage = by_agent.get(agent_id)
    if not isinstance(agent_usage, dict):
        return merged

    completion_tokens = agent_usage.get("completion_tokens")
    if completion_tokens is None:
        completion_tokens = agent_usage.get("completionTokens")
    if completion_tokens is None:
        return merged

    try:
        merged["output_tokens"] = max(int(completion_tokens), 0)
    except Exception:
        return merged

    cached_prompt_tokens = agent_usage.get("cached_prompt_tokens")
    if cached_prompt_tokens is None:
        cached_prompt_tokens = agent_usage.get("cachedPromptTokens")
    cache_miss_prompt_tokens = agent_usage.get("cache_miss_prompt_tokens")
    if cache_miss_prompt_tokens is None:
        cache_miss_prompt_tokens = agent_usage.get("cacheMissPromptTokens")
    cache_hit_rate = agent_usage.get("cache_hit_rate")
    if cache_hit_rate is None:
        cache_hit_rate = agent_usage.get("cacheHitRate")

    try:
        merged["cached_prompt_tokens"] = max(int(cached_prompt_tokens or 0), 0)
    except Exception:
        merged["cached_prompt_tokens"] = 0
    if cache_miss_prompt_tokens is not None:
        try:
            merged["cache_miss_prompt_tokens"] = max(int(cache_miss_prompt_tokens or 0), 0)
        except Exception:
            pass
    if cache_hit_rate is not None:
        try:
            merged["cache_hit_rate"] = max(0.0, min(1.0, float(cache_hit_rate)))
        except Exception:
            pass
    return merged


def _start_chat_stream_task(
    *,
    user: dict,
    user_id: str,
    project_name: str,
    agent_id: str,
    context_key: str,
    channel: str,
    message: str,
    active_context: Any,
    cm: ChatManager,
    prepare_history: Callable[[], tuple[list, int | None]],
) -> ChatTaskEntry:
    """注册并启动统一聊天后台任务，入口差异仅由历史准备回调承载。"""
    task_key = _make_task_key(user_id, project_name, agent_id, context_key)
    stop_event = threading.Event()
    entry = ChatTaskEntry(
        task_key=task_key,
        user_id=user_id,
        project_name=project_name,
        agent_id=agent_id,
        context_key=context_key,
        stop_event=stop_event,
        status='running',
        started_at=time.time(),
        channel=channel,
    )
    try:
        register_task(entry)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail='该会话已有任务在执行') from exc

    try:
        history, user_message_id = prepare_history()
        entry.user_message_id = user_message_id
        assistant_msg = cm.append_message(
            agent_id=agent_id,
            context_key=context_key,
            role='assistant',
            content='',
            metadata={
                'channel': channel,
                'stream_status': 'running',
                'stream_seq': 0,
                'task_id': entry.task_id,
            },
        )
        entry.assistant_message_id = assistant_msg.id
        entry.result_message_id = assistant_msg.id
        _checkpoint_chat_task(cm, entry, force=True, stream_status='running')
        agent_inst = create_agent_instance(agent_id, user_id, project_name)
    except Exception as exc:
        from agents.error_formatting import format_ai_error

        _finalize_chat_task(
            cm,
            entry,
            task_key,
            final_status='error',
            final_error_message=format_ai_error(exc),
            collect_usage=False,
        )
        raise
    request_locale = get_current_locale()
    request_export_format = get_current_export_format()

    def _run_chat_background() -> None:
        import contextvars

        ctx = contextvars.copy_context()

        def _in_context() -> None:
            terminated_early = False
            final_error_message = ''
            retry_count = 0
            _init_chat_task_ledger(user_id, project_name, agent_id, context_key)
            try:
                terminated_early, final_error_message, retry_count = _run_chat_stream_with_retry(
                    agent_inst=agent_inst,
                    message=message,
                    history=history,
                    active_context=active_context,
                    cm=cm,
                    entry=entry,
                    task_key=task_key,
                    stop_event=stop_event,
                )
            finally:
                _persist_chat_task_ledger(user_id, project_name)
                final_status = (
                    'cancelled'
                    if terminated_early
                    else 'error'
                    if final_error_message
                    else 'completed'
                )
                _finalize_chat_task(
                    cm,
                    entry,
                    task_key,
                    final_status=final_status,
                    final_error_message=final_error_message,
                    retry_count=retry_count,
                )

        ctx.run(
            _run_chat_background_context,
            user_id=str(user_id),
            project_name=project_name,
            is_admin=bool(user.get('is_admin')),
            locale=request_locale,
            llm_usage_context=_make_llm_usage_context(entry.task_id),
            llm_usage_reporter=lambda usage: _publish_chat_task_llm_usage(entry, usage),
            chat_agent_id=agent_id,
            chat_context_key=context_key,
            export_format=request_export_format,
            callback=_in_context,
        )

    thread = threading.Thread(
        target=_run_chat_background,
        daemon=True,
        name=f"chat_{channel}_{task_key}",
    )
    thread.start()
    return entry


def _visible_chat_history(history: list[dict]) -> list[dict]:
    return [item for item in history if item.get("role") != "system"]


def _context_summary_plain_text(summary: Dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return str(summary or "").strip()
    lines: list[str] = []
    title_map = {
        "summary": "摘要",
        "user_goal": "用户目标",
        "user_intent_anchors": "用户意图与原话锚点",
        "creative_state": "创作状态",
        "author_preferences": "作者偏好与禁区",
        "current_progress": "当前进度",
        "important_facts": "重要事实",
        "decisions": "已定决策",
        "rejected_options": "明确否决项",
        "conflicts_and_uncertainties": "冲突与未确认项",
        "open_tasks": "待办事项",
        "recent_turns": "近期上下文",
        "tool_results": "工具结果",
        "handoff_notes": "交接提醒",
    }
    for key, title in title_map.items():
        value = summary.get(key)
        if value in (None, "", [], {}):
            continue
        lines.append(f"{title}：")
        if isinstance(value, list):
            lines.extend(f"- {str(item).strip()}" for item in value if str(item).strip())
        elif isinstance(value, dict):
            lines.append(json.dumps(value, ensure_ascii=False, indent=2))
        else:
            lines.append(str(value).strip())
        lines.append("")
    return "\n".join(lines).strip()


def _checkpoint_chat_task(
    cm: ChatManager,
    entry: ChatTaskEntry,
    *,
    force: bool = False,
    stream_status: str | None = None,
) -> None:
    """把当前助手快照持久化到占位消息；已终态任务不再写入运行中快照。"""
    if entry.assistant_message_id is None:
        return
    with entry.checkpoint_lock:
        if entry.is_terminalized() and stream_status != entry.terminal_status:
            return
        now = time.time()
        if not force:
            if entry.last_checkpoint_seq == entry.next_seq:
                return
            if now - float(entry.last_checkpoint_at or 0) < _CHAT_CHECKPOINT_INTERVAL:
                return

        status = stream_status or entry.status or 'running'
        metadata = entry.build_metadata(stream_status=status)
        content = entry.accumulator.content if entry.accumulator is not None else ''
        cm.update_message_content_metadata(entry.assistant_message_id, content, metadata)
        entry.last_checkpoint_seq = entry.next_seq
        entry.last_checkpoint_at = now


def _finalize_chat_task(
    cm: ChatManager,
    entry: ChatTaskEntry,
    task_key: str,
    *,
    final_status: str,
    final_error_message: str = '',
    retry_count: int = 0,
    collect_usage: bool = True,
) -> bool:
    """一次性收口聊天任务，并发布唯一的 ``task_done`` 终态事件。"""
    if not entry.claim_terminal_status(final_status):
        return False
    import logging

    logger = logging.getLogger(__name__)
    reply = ''
    metadata: Dict[str, Any] = {}
    terminal_error_message = _coerce_stream_error_text(final_error_message)
    if final_status == 'error' and not terminal_error_message:
        terminal_error_message = '聊天生成失败'
    elif final_status != 'error':
        terminal_error_message = ''
    try:
        # 先固定终态诊断，再构建并持久化助手消息 metadata。
        # 这样任务内存注册表清理后，历史恢复仍能展示同一条最终错误。
        with entry.log_lock:
            entry.error_message = terminal_error_message
            entry.retry_count = retry_count
        if collect_usage:
            collected_usage = _collect_chat_task_llm_usage(entry)
            if collected_usage is not None:
                entry.llm_usage = collected_usage
        if entry.accumulator is not None:
            entry.accumulator.context_window_stats = _merge_context_window_stats_with_usage(
                entry.accumulator.context_window_stats,
                entry.llm_usage,
            )
        reply = entry.accumulator.content if entry.accumulator is not None else ''
        try:
            metadata = entry.build_metadata(stream_status=final_status)
        except Exception:
            logger.exception("构建聊天终态元数据失败: task=%s", entry.task_id)
            metadata = {}

        try:
            entry.append_control_event(
                {
                    'event': 'task_done',
                    'status': final_status,
                    'assistant_message_id': entry.assistant_message_id,
                    'result_message_id': entry.assistant_message_id,
                    **({'llm_usage': entry.llm_usage} if entry.llm_usage else {}),
                    **({
                        'context_window_stats': entry.accumulator.context_window_stats,
                    } if entry.accumulator is not None and entry.accumulator.context_window_stats else {}),
                    **({'error': terminal_error_message} if terminal_error_message else {}),
                },
                allow_terminal=True,
            )
        except Exception:
            logger.exception("发布聊天 task_done 事件失败: task=%s", entry.task_id)

        with entry.log_lock:
            entry.status = final_status
            entry.result_message_id = entry.assistant_message_id
            entry.result_content = reply
            entry.result_metadata = metadata
            entry.error_message = terminal_error_message
            entry.retry_count = retry_count
        # 内存终态与前端停止按钮不能等待数据库；持久化在此后尽力完成。
        entry.finished_event.set()
        entry.notify_observers()
        cleanup_task(task_key, task_id=entry.task_id)

        try:
            _checkpoint_chat_task(cm, entry, force=True, stream_status=final_status)
        except Exception:
            logger.exception("写入聊天终态 checkpoint 失败: task=%s", entry.task_id)

        try:
            update_task_status(
                task_key,
                final_status,
                expected_task_id=entry.task_id,
                allow_terminal=True,
                result_message_id=entry.assistant_message_id,
                result_content=reply,
                result_metadata=metadata,
                error_message=terminal_error_message,
                retry_count=retry_count,
            )
        except Exception:
            logger.exception("更新聊天终态状态失败: task=%s", entry.task_id)
    finally:
        with entry.log_lock:
            entry.status = final_status
            entry.result_message_id = entry.assistant_message_id
            entry.result_content = reply
            entry.result_metadata = metadata
            entry.error_message = terminal_error_message
            entry.retry_count = retry_count
        entry.finished_event.set()
        entry.notify_observers()
        cleanup_task(task_key, task_id=entry.task_id)
    return True


async def _observe_chat_task_events(request: Request, entry: ChatTaskEntry, *, after_seq: int = 0, include_snapshot: bool = True):
    """Yield replayable NDJSON events for one observer without consuming the task log."""
    cursor = max(0, int(after_seq or 0))
    loop = asyncio.get_running_loop()
    signal = asyncio.Event()
    entry.subscribe(loop, signal)
    try:
        if include_snapshot:
            snapshot = entry.build_snapshot()
            yield _serialize_stream_event(snapshot)
            cursor = max(cursor, int(snapshot.get("seq") or 0))

        heartbeat_interval = 5.0
        last_heartbeat = time.time()
        while True:
            events = entry.get_events_after(cursor)
            if events:
                for event in events:
                    cursor = max(cursor, int(event.get("seq") or 0))
                    yield _serialize_stream_event(event)
                continue

            # claim_terminal_status 会先阻止迟到事件，但观察器必须等到 task_done
            # 已写入日志且 finished_event 置位后才能退出。
            if entry.finished_event.is_set():
                break
            if request and await request.is_disconnected():
                break

            signal.clear()
            # 清除信号后重新检查，避免事件恰好落在查询与 clear 之间造成漏唤醒。
            if entry.get_events_after(cursor) or entry.finished_event.is_set():
                continue

            remaining = max(0.05, heartbeat_interval - (time.time() - last_heartbeat))
            try:
                await asyncio.wait_for(signal.wait(), timeout=min(remaining, 1.0))
            except asyncio.TimeoutError:
                pass

            current = time.time()
            if current - last_heartbeat >= heartbeat_interval:
                yield _serialize_stream_event({"event": "heartbeat"})
                last_heartbeat = current
    finally:
        entry.unsubscribe(loop, signal)


@chat_router.get('/api/chat/history')
async def get_chat_history(
    request: Request,
    agentId: str = Query(..., alias='agentId'),
    contextKey: str = Query('global', alias='contextKey'),
    limit: int = Query(50),
    user: dict = Depends(get_current_user),
):
    """获取指定 Agent + contextKey 的历史记录。"""
    user_id = str(user['user_id'])
    project_name = resolve_project_name(get_current_project_name(), request.query_params.get('projectName'))
    if not project_name:
        return JSONResponse(status_code=400, content={'error': '缺少项目名称'})

    cm = ChatManager(user_id=user_id, project_name=project_name)
    history = cm.get_history(agent_id=agentId, context_key=contextKey, limit=limit)
    return {'success': True, 'history': _visible_chat_history(history)}


@chat_router.delete('/api/chat/history')
async def clear_chat_history(
    request: Request,
    agentId: str = Query(..., alias='agentId'),
    contextKey: str = Query('global', alias='contextKey'),
    user: dict = Depends(get_current_user),
):
    """清空指定 Agent + contextKey 的会话。"""
    user_id = str(user['user_id'])
    project_name = resolve_project_name(get_current_project_name(), request.query_params.get('projectName'))
    if not project_name:
        return JSONResponse(status_code=400, content={'error': '缺少项目名称'})

    cm = ChatManager(user_id=user_id, project_name=project_name)
    ok = cm.clear_session(agent_id=agentId, context_key=contextKey)
    if ok:
        from agents.attachment import collect_orphan_attachments

        collect_orphan_attachments(user_id, project_name)
    return {'success': True, 'cleared': ok}


@chat_router.post('/api/chat/context/compact')
async def compact_chat_context(data: ChatContextCompactRequest, user: dict = Depends(get_current_user)):
    """手动压缩当前 Agent + contextKey 的聊天上下文为内部摘要。"""
    user_id = str(user['user_id'])
    project_name = resolve_project_name(get_current_project_name(), data.projectName)
    if not project_name:
        return JSONResponse(status_code=400, content={'error': '缺少项目名称'})

    cm = ChatManager(user_id=user_id, project_name=project_name)
    history = cm.get_context_history(agent_id=data.agentId, context_key=data.contextKey)
    raw_history = [
        item
        for item in history
        if item.get("role") in {"user", "assistant"} and str(item.get("content") or "").strip()
    ]
    if not raw_history:
        return {'success': True, 'compacted': False, 'message': '当前会话没有新增的可压缩上下文'}
    from agents.context_budget import (
        COMPACTION_AGENT_PROFILES,
        _compaction_budget,
        partition_history_for_manual_compaction,
    )

    from llm.agen_matchbox import matchbox

    llm = matchbox().get_user_llm(user_id, agent_name=data.agentId)
    model_name = str(
        getattr(getattr(llm, "usage", None), "model_name", "")
        or getattr(llm, "model_name", "")
        or ""
    )
    compaction_budget = _compaction_budget(
        int(getattr(llm, "max_context_tokens", 0) or 256_000),
        int(getattr(llm, "max_output_tokens", 0) or 4_096),
        data.agentId,
    )

    compactible_source, retained_history = partition_history_for_manual_compaction(
        history,
        recent_token_budget=compaction_budget.recent_tokens,
        model_name=model_name,
    )
    compactible_history = [
        {
            "role": item.get("role"),
            "content": item.get("content"),
        }
        for item in compactible_source
        if item.get("role") in {"system", "user", "assistant"} and str(item.get("content") or "").strip()
    ]
    new_source_messages = [
        item
        for item in compactible_source
        if item.get("role") in {"user", "assistant"}
    ]
    if not compactible_history or not new_source_messages:
        return {
            'success': True,
            'compacted': False,
            'message': '当前会话尚未积累超过动态近期预算的可压缩上下文',
        }

    try:
        from agents.utility_agent import UtilityAgent
        from agents.context_budget import build_context_checkpoint_payload_from_history
        requested_target = int(data.targetTokens or 0)
        target_tokens = max(
            256,
            min(
                compaction_budget.summary_tokens,
                requested_target if requested_target > 0 else compaction_budget.summary_tokens,
            ),
        )

        utility = UtilityAgent(user_id=user_id, project_name=project_name)
        summary = await run_in_threadpool(
            utility.compress_chat_history,
            history_items=compactible_history,
            agent_id=data.agentId,
            model_name=model_name,
            source_llm_client=llm,
            target_tokens=target_tokens,
            current_user_message="用户手动触发上下文压缩。",
            agent_profile=COMPACTION_AGENT_PROFILES.get(data.agentId, "优先保留用户目标、硬约束、关键事实、决策、进度、工具结论和开放任务；删除重复与无后续价值的过程内容。"),
        )
        summary_text = _context_summary_plain_text(summary)
        summary_json = json.dumps(summary, ensure_ascii=False, indent=2)
        try:
            from llm.agen_matchbox.estimate_tokens import estimate_tokens
            summary_tokens = int(estimate_tokens(summary_json, model=model_name))
            retained_tokens = int(estimate_tokens(
                json.dumps(
                    [
                        {"role": item.get("role"), "content": item.get("content")}
                        for item in retained_history
                    ],
                    ensure_ascii=False,
                ),
                model=model_name,
            ))
            original_tokens = int(estimate_tokens(
                json.dumps(
                    [
                        {"role": item.get("role"), "content": item.get("content")}
                        for item in history
                    ],
                    ensure_ascii=False,
                ),
                model=model_name,
            ))
            compacted_runtime_tokens = summary_tokens + retained_tokens
        except Exception:
            summary_tokens = 0
            original_tokens = 0
            compacted_runtime_tokens = 0
        checkpoint_payload = build_context_checkpoint_payload_from_history(
            summary=summary,
            history=compactible_source,
            source="manual_compaction",
            agent_id=data.agentId,
            model_name=model_name,
            target_tokens=target_tokens,
            original_tokens=original_tokens,
            compacted_tokens=summary_tokens,
            retained_messages=len(retained_history),
        )
        if checkpoint_payload is None:
            return JSONResponse(status_code=409, content={
                'error': '无法确定上下文压缩边界，请刷新会话后重试',
                'code': 'context_checkpoint_boundary_missing',
            })
        msg = cm.persist_context_checkpoint(
            agent_id=data.agentId,
            context_key=data.contextKey,
            checkpoint=checkpoint_payload,
        )
        if msg is None:
            return JSONResponse(status_code=409, content={
                'error': '上下文在压缩期间发生变化，请重试',
                'code': 'context_checkpoint_conflict',
            })
        checkpoint_metadata = dict(msg.get("metadata") or {})
        original_message_count = int(checkpoint_metadata.get("original_messages") or len(raw_history))
        notice = cm.append_message(
            agent_id=data.agentId,
            context_key=data.contextKey,
            role='assistant',
            content='',
            metadata={
                "kind": "context_compaction_notice",
                "channel": "manual_compaction",
                "context_window_stats": {
                    "agent_id": data.agentId,
                    "input_tokens": compacted_runtime_tokens,
                    "output_tokens": 0,
                    "original_tokens": original_tokens,
                    "retained_messages": len(retained_history),
                    "model": model_name,
                    "compacted": True,
                    "reason": "manual_context_compacted",
                    "compaction_target_tokens": compaction_budget.target_context_tokens,
                    "summary_budget_tokens": compaction_budget.summary_tokens,
                    "recent_budget_tokens": compaction_budget.recent_tokens,
                    "compaction_target_ratio": compaction_budget.target_ratio,
                },
                "segments": [
                    {
                        "type": "context_compaction_summary",
                        "status": "finished",
                        "summary_text": summary_text,
                        "summary_message_id": msg["id"],
                        "original_messages": original_message_count,
                        "compacted_tokens": summary_tokens,
                        "model": model_name,
                    }
                ],
            },
        )
        return {
            'success': True,
            'compacted': True,
            'summaryMessageId': msg["id"],
            'noticeMessageId': notice.id,
            'originalMessages': original_message_count,
            'targetTokens': target_tokens,
            'compactionTargetTokens': compaction_budget.target_context_tokens,
            'summaryText': summary_text,
            'contextWindowStats': {
                'agent_id': data.agentId,
                'input_tokens': compacted_runtime_tokens,
                'original_tokens': original_tokens,
                'retained_messages': len(retained_history),
                'model': model_name,
                'compacted': True,
                'reason': 'manual_context_compacted',
                'compaction_target_tokens': compaction_budget.target_context_tokens,
                'summary_budget_tokens': compaction_budget.summary_tokens,
                'recent_budget_tokens': compaction_budget.recent_tokens,
                'compaction_target_ratio': compaction_budget.target_ratio,
            },
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        from .schemas import format_ai_error
        return JSONResponse(status_code=500, content={'error': format_ai_error(e)})


@chat_router.delete('/api/chat/message')
async def delete_chat_message(
    request: Request,
    messageId: int = Query(..., alias='messageId'),
    user: dict = Depends(get_current_user),
):
    """删除单条消息。"""
    user_id = str(user['user_id'])
    project_name = resolve_project_name(get_current_project_name(), request.query_params.get('projectName'))
    if not project_name:
        return JSONResponse(status_code=400, content={'error': '缺少项目名称'})

    with UserInfoSession() as session:
        msg = session.get(ChatMessage, messageId)
        if not msg or str(msg.user_id) != user_id:
            return JSONResponse(status_code=404, content={'error': '消息不存在'})
        if msg.project_name != project_name:
            return JSONResponse(status_code=403, content={'error': '无权操作此项目的消息'})
        msg_role = msg.role
        agent_id = msg.agent_id
        context_key = msg.context_key
        msg_id = msg.id

    cm = ChatManager(user_id=user_id, project_name=project_name)
    ok = cm.delete_message(messageId)
    if ok:
        from agents.attachment import collect_orphan_attachments

        collect_orphan_attachments(user_id, project_name)
    return {'success': True, 'deleted': bool(ok)}


@chat_router.post('/api/chat/message/attachment')
async def remove_chat_message_attachment(data: ChatMessageAttachmentRemoveRequest, user: dict = Depends(get_current_user)):
    """移除会话中的附件上下文（不删除消息本身）。

    语义：
    - 以 ``messageId`` 对应的附件作为锚点；同一 agent / contextKey 下所有引用
      同一附件的用户消息都会被同步标记为 deleted。
    - 多附件场景：``data.attachmentId`` 用于精确指定要移除的那一个附件；
      不传时移除列表中的首个附件。
    - ``importedFiles`` 是附件引用的唯一真相源，匹配项会被标记为 deleted。
    - 不删除消息，不删除后续回复。
    """
    user_id = str(user['user_id'])
    project_name = resolve_project_name(get_current_project_name(), data.projectName)
    if not project_name:
        return JSONResponse(status_code=400, content={'error': '缺少项目名称'})

    requested_attachment_id = (data.attachmentId or '').strip()

    def _collect_files(meta: dict) -> list[dict]:
        """从 metadata 的唯一附件真相源抽取引用列表。"""
        files = meta.get('importedFiles')
        if isinstance(files, list):
            return [dict(item) for item in files if isinstance(item, dict)]
        return []

    with UserInfoSession() as session:
        msg = session.get(ChatMessage, data.messageId)
        if not msg or str(msg.user_id) != user_id:
            return JSONResponse(status_code=404, content={'error': '消息不存在'})
        if msg.project_name != project_name:
            return JSONResponse(status_code=403, content={'error': '无权操作此项目的消息'})
        if msg.role != 'user':
            return JSONResponse(status_code=400, content={'error': '仅用户消息支持移除附件'})
        agent_id = msg.agent_id
        context_key = msg.context_key
        target_meta = dict(msg.metadata_json or {})
        target_files = _collect_files(target_meta)
        if not target_files:
            return JSONResponse(status_code=400, content={'error': '该消息没有可移除的附件'})

        target_entry: dict | None = None
        if requested_attachment_id:
            for entry in target_files:
                if str(entry.get('attachmentId') or '').strip() == requested_attachment_id:
                    target_entry = entry
                    break
            if target_entry is None:
                return JSONResponse(status_code=404, content={'error': '消息中未找到指定 attachmentId'})
        else:
            target_entry = target_files[0]

        target_filename = str(target_entry.get('filename') or '').strip()
        target_attachment_id = str(target_entry.get('attachmentId') or '').strip()
        target_uploaded_at = target_entry.get('uploadedAt') or 0

    def _same_attachment(entry: Any) -> bool:
        """优先用 attachmentId 精确匹配；缺失时回落到 filename + uploadedAt。"""
        if not isinstance(entry, dict):
            return False
        entry_id = str(entry.get('attachmentId') or '').strip()
        if target_attachment_id and entry_id:
            return entry_id == target_attachment_id
        filename = str(entry.get('filename') or '').strip()
        if not filename or filename != target_filename:
            return False
        uploaded_at = entry.get('uploadedAt') or 0
        if target_uploaded_at and uploaded_at:
            return str(uploaded_at) == str(target_uploaded_at)
        return True

    deleted_at = int(time.time())
    updated_count = 0

    with UserInfoSession() as session:
        messages = (
            session.query(ChatMessage)
            .filter(
                ChatMessage.user_id == int(user_id),
                ChatMessage.project_name == project_name,
                ChatMessage.agent_id == agent_id,
                ChatMessage.context_key == context_key,
                ChatMessage.role == 'user',
            )
            .all()
        )
        for item in messages:
            meta = dict(item.metadata_json or {})
            entries = _collect_files(meta)
            if not any(_same_attachment(e) for e in entries):
                continue

            # 标记列表中匹配到的项；同一消息可能历史上挂多份，全部一并 deleted。
            updated_entries: list[dict] = []
            for entry in entries:
                if _same_attachment(entry):
                    new_entry = dict(entry)
                    new_entry['deleted'] = True
                    new_entry['deletedAt'] = deleted_at
                    updated_entries.append(new_entry)
                else:
                    updated_entries.append(dict(entry))
            meta['importedFiles'] = updated_entries

            active_ctx = meta.get('active_context')
            if isinstance(active_ctx, str) and active_ctx.strip():
                fallback_label = target_filename or target_attachment_id or '未知附件'
                meta['active_context'] = f'[附件 "{fallback_label}" 已被删除]'

            item.metadata_json = meta
            updated_count += 1

        session.commit()

    from agents.attachment import collect_orphan_attachments

    collect_orphan_attachments(user_id, project_name)

    return {'success': True, 'updated': updated_count}


@chat_router.post('/api/chat/edit')
async def edit_chat_message(data: ChatMessageEditRequest, user: dict = Depends(get_current_user)):
    """编辑消息并重新开始对话。
    
    逻辑：
    1. 找到该消息，更新其内容。
    2. 删除该消息之后的所有消息。
    3. 如果是用户消息，则触发 Agent 重新回复。
    """
    user_id = str(user['user_id'])
    project_name = resolve_project_name(get_current_project_name(), data.projectName)
    if not project_name:
        return JSONResponse(status_code=400, content={'error': '缺少项目名称'})

    cm = ChatManager(user_id=user_id, project_name=project_name)
    
    # 获取消息详情以获得时间戳
    with UserInfoSession() as session:
        msg = session.get(ChatMessage, data.messageId)
        if not msg or str(msg.user_id) != user_id:
            return JSONResponse(status_code=404, content={'error': '消息不存在'})
        
        # 安全检查
        if msg.project_name != project_name:
            return JSONResponse(status_code=403, content={'error': '无权操作此项目的消息'})
        
        # 信赖数据库中真实的房间归属
        data.agentId = msg.agent_id
        data.contextKey = msg.context_key
        
        timestamp = msg.timestamp.timestamp()
        role = msg.role
        msg_id = msg.id

    # 1. 更新内容
    cm.update_message(data.messageId, data.content)

    # 2. 删除之后的消息（使用 message_id 更可靠）
    cm.delete_after(agent_id=data.agentId, context_key=data.contextKey, message_id=msg_id)

    # 3. 如果是用户消息，则重新触发回复
    if role == 'user':
        effective_active_context = _resolve_effective_active_context(user_id, project_name, data.agentId, data.activeContext)
        _apply_request_runtime_meta(data.activeMeta, user_id=user_id, project_name=project_name)
        imported_files_meta = _extract_imported_files_meta(data.activeMeta)
        effective_active_context = _expand_active_context_with_attachments(
            user_id, project_name, effective_active_context, imported_files_meta,
        )
        effective_active_context = _append_longread_ledger_snapshot(
            user_id, project_name, data.agentId, data.contextKey, effective_active_context,
        )

        # 统一实例化 Agent（包括导演）并获取回复
        # get_history 返回的历史已含编辑后的用户消息，需移除以避免与 data.content 双喂
        history = cm.get_context_history(agent_id=data.agentId, context_key=data.contextKey)
        if history and history[-1].get('role') == 'user':
            history = history[:-1]

        try:
            print(f"[EditChat] Triggering reply for expert agent: {data.agentId}")
            agent_inst = create_agent_instance(data.agentId, user_id, project_name)

            chat_tokens = set_current_chat_session(data.agentId, data.contextKey)
            try:
                reply = await run_in_threadpool(
                    agent_inst.chat,
                    data.content,
                    history=history,
                    active_context=effective_active_context,
                )
            finally:
                reset_current_chat_session(chat_tokens)
            print(f"[EditChat] Agent reply length: {len(reply) if reply else 0}")
            
            cm.append_message(
                agent_id=data.agentId,
                context_key=data.contextKey,
                role='assistant',
                content=reply,
                metadata={'channel': 'edit_reply'},
            )
            checkpoint = getattr(agent_inst, 'consume_context_checkpoint_candidate', None)
            if callable(checkpoint):
                checkpoint = checkpoint()
            else:
                checkpoint = None
            if checkpoint:
                _persist_context_checkpoint_safely(
                    cm,
                    agent_id=data.agentId,
                    context_key=data.contextKey,
                    checkpoint=checkpoint,
                )
            return {'success': True, 'reply': reply}
        except Exception as e:
            import traceback
            traceback.print_exc()
            from .schemas import format_ai_error
            return JSONResponse(status_code=500, content={'error': format_ai_error(e)})

    return {'success': True}


@chat_router.post('/api/chat/edit/stream')
async def edit_chat_message_stream(request: Request, data: ChatMessageEditRequest, user: dict = Depends(get_current_user)):
    """编辑消息并重新开始对话（流式输出）。

    后台线程模式：前端断连后 AI 继续执行，直到任务自然完成或被显式取消。
    """
    user_id = str(user['user_id'])
    project_name = resolve_project_name(get_current_project_name(), data.projectName)
    if not project_name:
        raise HTTPException(status_code=400, detail='缺少项目名称')

    cm = ChatManager(user_id=user_id, project_name=project_name)

    with UserInfoSession() as session:
        msg = session.get(ChatMessage, data.messageId)
        if not msg or str(msg.user_id) != user_id:
            raise HTTPException(status_code=404, detail='消息不存在')

        if msg.project_name != project_name:
            raise HTTPException(status_code=403, detail='无权操作此项目的消息')
        
        # 信赖数据库中真实的房间归属
        data.agentId = msg.agent_id
        data.contextKey = msg.context_key

        role = msg.role
        msg_id = msg.id

    if role != 'user':
        cm.update_message(data.messageId, data.content)
        cm.delete_after(agent_id=data.agentId, context_key=data.contextKey, message_id=msg_id)
        return StreamingResponse(iter(['']), media_type='text/plain')

    # 编辑是“替换当前任务”，必须先让同一会话的旧后台线程真实退出。
    task_key = _make_task_key(user_id, project_name, data.agentId, data.contextKey)
    existing_task = get_task_by_parts(user_id, project_name, data.agentId, data.contextKey)
    if existing_task and not existing_task.finished_event.is_set():
        if existing_task.status == 'running':
            cancel_task(task_key)
        stopped = await asyncio.to_thread(wait_for_task_exit, task_key, 10.0)
        if not stopped:
            raise HTTPException(status_code=409, detail='上一条聊天任务仍在退出，请稍后重试')

    effective_active_context = _resolve_effective_active_context(user_id, project_name, data.agentId, data.activeContext)
    _apply_request_runtime_meta(data.activeMeta, user_id=user_id, project_name=project_name)
    imported_files_meta = _extract_imported_files_meta(data.activeMeta)
    effective_active_context = _expand_active_context_with_attachments(
        user_id, project_name, effective_active_context, imported_files_meta,
    )
    effective_active_context = _append_longread_ledger_snapshot(
        user_id, project_name, data.agentId, data.contextKey, effective_active_context,
    )

    def prepare_history() -> tuple[list, int]:
        cm.update_message(data.messageId, data.content)
        cm.delete_after(agent_id=data.agentId, context_key=data.contextKey, message_id=msg_id)
        history = cm.get_context_history(agent_id=data.agentId, context_key=data.contextKey)
        if history and history[-1].get('role') == 'user':
            history = history[:-1]
        return history, msg_id

    entry = _start_chat_stream_task(
        user=user,
        user_id=user_id,
        project_name=project_name,
        agent_id=data.agentId,
        context_key=data.contextKey,
        channel='edit_reply_stream',
        message=data.content,
        active_context=effective_active_context,
        cm=cm,
        prepare_history=prepare_history,
    )

    return StreamingResponse(_observe_chat_task_events(request, entry, include_snapshot=True), media_type=_NDJSON_MEDIA_TYPE)


@chat_router.post('/api/chat/send')
async def send_chat_message(data: ChatSendRequest, user: dict = Depends(get_current_user)):
    """发送消息。

规则：
- 对导演(agent_director)说：执行路由，并把消息"静默写入"多个目标 Agent 的会话
- 对具体 Agent 说：仅写入该 Agent 的会话（不重复写到导演）
"""
    user_id = str(user['user_id'])
    project_name = resolve_project_name(get_current_project_name(), data.projectName)
    if not project_name:
        return JSONResponse(status_code=400, content={'error': '缺少项目名称'})

    agent_id = (data.agentId or '').strip()
    if not agent_id:
        return JSONResponse(status_code=400, content={'error': '缺少 agentId'})

    context_key = (data.contextKey or 'global').strip() or 'global'
    message = (data.message or '').strip()
    if not message:
        return JSONResponse(status_code=400, content={'error': '消息为空'})

    effective_active_context = _resolve_effective_active_context(user_id, project_name, agent_id, data.activeContext)
    _apply_request_runtime_meta(data.activeMeta, user_id=user_id, project_name=project_name)
    imported_files_meta = _extract_imported_files_meta(data.activeMeta)
    effective_active_context = _expand_active_context_with_attachments(
        user_id, project_name, effective_active_context, imported_files_meta,
    )
    effective_active_context = _append_longread_ledger_snapshot(
        user_id, project_name, agent_id, context_key, effective_active_context,
    )

    # 统一处理所有 Agent（包括导演）
    cm = ChatManager(user_id=user_id, project_name=project_name)

    # 1. 先取历史（不含当前消息），避免双喂
    history = cm.get_context_history(agent_id=agent_id, context_key=context_key)

    # 2. 保存用户消息到 DB
    cm.append_message(
        agent_id=agent_id,
        context_key=context_key,
        role='user',
        content=message,
        metadata=_build_user_message_metadata('direct', data.activeContext, imported_files_meta),
    )

    try:
        agent_inst = create_agent_instance(agent_id, user_id, project_name)

        chat_tokens = set_current_chat_session(agent_id, context_key)
        try:
            reply = await run_in_threadpool(
                agent_inst.chat,
                message,
                history=history,
                active_context=effective_active_context,
            )
        finally:
            reset_current_chat_session(chat_tokens)

        # 3. Record AI reply
        cm.append_message(
            agent_id=agent_id,
            context_key=context_key,
            role='assistant',
            content=reply,
            metadata={'channel': 'direct_reply'},
        )
        checkpoint = getattr(agent_inst, 'consume_context_checkpoint_candidate', None)
        if callable(checkpoint):
            checkpoint = checkpoint()
        else:
            checkpoint = None
        if checkpoint:
            _persist_context_checkpoint_safely(
                cm,
                agent_id=agent_id,
                context_key=context_key,
                checkpoint=checkpoint,
            )
        
        return {'success': True, 'mode': 'direct', 'reply': reply}
    except Exception as e:
        print(f"[Direct Chat] Failed for {agent_id}: {e}")
        from .schemas import format_ai_error
        return JSONResponse(status_code=500, content={'error': format_ai_error(e)})


@chat_router.post('/api/chat/send/stream')
async def send_chat_message_stream(request: Request, data: ChatSendRequest, user: dict = Depends(get_current_user)):
    """发送消息（流式输出，NDJSON）。

    与 /api/chat/send 规则一致，但 AI 回复以流式文本返回。
    后台线程模式：前端断连后 AI 继续执行，直到任务自然完成或被显式取消。
    """
    user_id = str(user['user_id'])
    project_name = resolve_project_name(get_current_project_name(), data.projectName)
    if not project_name:
        raise HTTPException(status_code=400, detail='缺少项目名称')

    agent_id = (data.agentId or '').strip()
    if not agent_id:
        raise HTTPException(status_code=400, detail='缺少 agentId')

    context_key = (data.contextKey or 'global').strip() or 'global'
    message = (data.message or '').strip()
    if not message:
        raise HTTPException(status_code=400, detail='消息为空')

    effective_active_context = _resolve_effective_active_context(user_id, project_name, agent_id, data.activeContext)
    _apply_request_runtime_meta(data.activeMeta, user_id=user_id, project_name=project_name)
    imported_files_meta = _extract_imported_files_meta(data.activeMeta)
    effective_active_context = _expand_active_context_with_attachments(
        user_id, project_name, effective_active_context, imported_files_meta,
    )
    effective_active_context = _append_longread_ledger_snapshot(
        user_id, project_name, agent_id, context_key, effective_active_context,
    )

    cm = ChatManager(user_id=user_id, project_name=project_name)

    def prepare_history() -> tuple[list, int]:
        history = cm.get_context_history(agent_id=agent_id, context_key=context_key)
        user_message = cm.append_message(
            agent_id=agent_id,
            context_key=context_key,
            role='user',
            content=message,
            metadata=_build_user_message_metadata('direct', data.activeContext, imported_files_meta),
        )
        return history, user_message.id

    entry = _start_chat_stream_task(
        user=user,
        user_id=user_id,
        project_name=project_name,
        agent_id=agent_id,
        context_key=context_key,
        channel='direct_reply_stream',
        message=message,
        active_context=effective_active_context,
        cm=cm,
        prepare_history=prepare_history,
    )

    return StreamingResponse(_observe_chat_task_events(request, entry, include_snapshot=True), media_type=_NDJSON_MEDIA_TYPE)


# ─────────────────────────────────────────────────────────────────────────────
# 聊天后台任务管理 API
# ─────────────────────────────────────────────────────────────────────────────


@chat_router.get('/api/chat/task-status')
async def get_chat_task_status(
    request: Request,
    agentId: str = Query(..., alias='agentId'),
    contextKey: str = Query('global', alias='contextKey'),
    user: dict = Depends(get_current_user),
):
    """查询指定会话是否有后台聊天任务在运行。

    返回格式：
    - hasTask: bool — 是否存在任务（包括已完成/取消但尚未清理的）
    - status: str — running | completed | cancelled | error
    - agentId / contextKey — 任务标识
    - resultMessageId — 完成后的消息 ID（用于前端刷新历史）
    - error — 错误信息（仅 error 状态）
    """
    user_id = str(user['user_id'])
    project_name = resolve_project_name(get_current_project_name(), request.query_params.get('projectName'))
    if not project_name:
        return JSONResponse(status_code=400, content={'error': '缺少项目名称'})

    entry = get_task_by_parts(user_id, project_name, agentId, contextKey)
    if not entry:
        return {'hasTask': False}

    return build_task_status_payload(entry)


@chat_router.get('/api/chat/recent-tasks')
async def get_chat_recent_tasks(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """列出当前用户在当前项目下尚未清理的聊天任务，用于刷新恢复。"""
    user_id = str(user['user_id'])
    project_name = resolve_project_name(get_current_project_name(), request.query_params.get('projectName'))
    if not project_name:
        return JSONResponse(status_code=400, content={'error': '缺少项目名称'})

    tasks = list_recent_tasks(user_id, project_name)
    return {
        'tasks': [build_task_status_payload(t) for t in tasks],
        'count': len(tasks),
    }


@chat_router.post('/api/chat/task-cancel')
async def cancel_chat_task(request: Request, data: ChatTaskCancelRequest, user: dict = Depends(get_current_user)):
    """手动取消指定会话的后台聊天任务（对应前端"停止"按钮）。"""
    user_id = str(user['user_id'])
    project_name = resolve_project_name(get_current_project_name(), data.projectName)
    if not project_name:
        raise HTTPException(status_code=400, detail='缺少项目名称')

    agent_id = (data.agentId or '').strip()
    if not agent_id:
        raise HTTPException(status_code=400, detail='缺少 agentId')

    context_key = (data.contextKey or 'global').strip() or 'global'

    task_key = _make_task_key(user_id, project_name, agent_id, context_key)
    ok = cancel_task(task_key)
    if not ok:
        return {'success': False, 'reason': '任务不存在或已结束'}
    # 取消接口只负责请求停止。task_done 与 finished_event 必须由真正运行聊天
    # 后台逻辑的线程在退出后发布，避免旧工具尚未结束时新任务抢占同一会话。
    return {'success': True}


@chat_router.get('/api/chat/task-stream')
async def reconnect_chat_task_stream(
    request: Request,
    agentId: str = Query(..., alias='agentId'),
    contextKey: str = Query('global', alias='contextKey'),
    afterSeq: int = Query(0, alias='afterSeq'),
    user: dict = Depends(get_current_user),
):
    """重连到正在运行的后台聊天任务，按 cursor 回放事件。

    前端关闭/刷新后重新进入时调用此端点：
    - running → 新 NDJSON 观察者读取 task_snapshot，并按 afterSeq 回放 event_log 后续事件
    - completed/cancelled/error → 返回最终状态 JSON（含 resultMessageId）
    - 不存在 → 返回 {hasTask: false}
    """
    user_id = str(user['user_id'])
    project_name = resolve_project_name(get_current_project_name(), request.query_params.get('projectName'))
    if not project_name:
        return JSONResponse(status_code=400, content={'error': '缺少项目名称'})

    entry = get_task_by_parts(user_id, project_name, agentId, contextKey)

    # 任务不存在
    if not entry:
        return {'hasTask': False}

    # 任务已结束：返回最终状态
    if entry.status != 'running':
        return build_task_status_payload(entry)

    return StreamingResponse(
        _observe_chat_task_events(request, entry, after_seq=afterSeq, include_snapshot=True),
        media_type=_NDJSON_MEDIA_TYPE,
    )
