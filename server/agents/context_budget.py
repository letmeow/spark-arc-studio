"""聊天上下文 token 预算与自动压缩。"""

from __future__ import annotations

import json
import contextvars
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Generator, List, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from core.file_ingest.chunking import estimate_text_tokens as estimate_tokens


CHAT_HISTORY_FETCH_LIMIT = 200
DEFAULT_CONTEXT_WINDOW_FALLBACK_TOKENS = 256_000
MAX_OUTPUT_RESERVE_TOKENS = 20_000
CONTEXT_SAFETY_FLOOR_TOKENS = 16_000
CONTEXT_SAFETY_RATIO = 0.0625
SMALL_CONTEXT_RESERVE_RATIO = 0.10
MIN_CONTEXT_BUDGET_TOKENS = 256
MIN_COMPACTION_SUMMARY_TOKENS = 256
MIN_COMPACTION_RECENT_TOKENS = 2_000
CONTEXT_CHECKPOINT_KIND = "context_checkpoint"
LEGACY_CONTEXT_SUMMARY_KIND = "context_summary"
CONTEXT_CHECKPOINT_READY_EVENT = "context_checkpoint_ready"

# 压缩后的工作集随窗口增大而降低占比，避免百万级窗口仍保留大部分旧原文。
# Agent 偏移体现的是“近期原文”和“结构化摘要”之间的取舍，不改变原始信息的来源。
COMPACTION_TARGET_RATIOS = (
    (128_000, 0.28),
    (256_000, 0.24),
    (512_000, 0.18),
    (1_000_000, 0.14),
    (2_000_000, 0.12),
)
COMPACTION_AGENT_RATIO_OFFSETS = {
    "agent_director": -0.02,
    "agent_muse": -0.02,
    "agent_style": 0.00,
    "agent_lorebook": 0.01,
    "agent_showrunner": 0.01,
    "agent_critic": 0.01,
    "agent_scriptwriter": 0.02,
}
COMPACTION_AGENT_SUMMARY_RATIOS = {
    "agent_director": 0.62,
    "agent_muse": 0.60,
    "agent_style": 0.52,
    "agent_lorebook": 0.56,
    "agent_showrunner": 0.54,
    "agent_critic": 0.56,
    "agent_scriptwriter": 0.42,
}
COMPACTION_AGENT_PROFILES = {
    "agent_director": "优先保留用户目标与硬约束、已完成/未完成任务、委派结果、工具结论、关键决策及下一步；删除重复寒暄和过程性转述。",
    "agent_muse": "优先保留用户明确偏好、采用与否决的创意、创作方向、灵感种子及其约束；合并重复脑暴，只保留可继续发展的差异点。",
    "agent_lorebook": "优先保留世界观硬事实、角色身份与关系、时间线、已落盘设定、冲突与覆盖关系；不要把推测写成 canon。",
    "agent_showrunner": "优先保留故事目标、结构决策、梗概/节拍/大纲的当前版本、节奏约束、否决方案和待解决的结构问题。",
    "agent_scriptwriter": "优先保留当前场景、角色声音、连续性事实、用户改写要求、已确认文本约束、工具落盘结果和最近创作上下文；近期原文比旧过程更重要。",
    "agent_critic": "优先保留审阅对象、逐项证据、问题严重度、修改建议、已采纳/拒绝的修正和未关闭风险；不要只保留笼统评价。",
    "agent_style": "优先保留稳定文风规则、作者偏好与禁区、正反例、当前迁移目标和已确认的表达取舍；合并同义规则。",
}


class NonRetryableChatError(RuntimeError):
    """不应通过重复调用上游模型来重试的聊天错误。"""

    code = "non_retryable_chat_error"

    def __init__(self, message: str, *, reason: str, details: Dict[str, Any] | None = None):
        super().__init__(message)
        self.reason = str(reason or "unknown")
        self.details = dict(details or {})

    def to_event(self) -> Dict[str, Any]:
        return {
            "event": "error",
            "code": self.code,
            "message": str(self),
            "reason": self.reason,
            "retryable": False,
            **self.details,
        }


class ContextWindowIncompatibleError(NonRetryableChatError):
    """当前模型窗口无法容纳不可丢弃的最小上下文。"""

    code = "context_window_incompatible"


class ContextCompactionFailedError(NonRetryableChatError):
    """压缩失败；为避免静默丢失历史而终止本轮请求。"""

    code = "context_compaction_failed"


class ContextBudgetCancelledError(RuntimeError):
    """聊天任务取消后停止等待后台预算操作。"""


def stream_context_budget_events(
    operation: Callable[..., "ContextBudgetResult"],
    /,
    *,
    stop_event: Any = None,
    **kwargs: Any,
) -> Generator[Dict[str, Any], None, "ContextBudgetResult"]:
    """在独立线程执行预算操作，让压缩事件能覆盖真实等待时间。"""
    event_queue: queue.Queue[Any] = queue.Queue()
    done_marker = object()
    result_holder: Dict[str, Any] = {}
    caller_context = contextvars.copy_context()

    def run() -> None:
        try:
            result_holder["result"] = operation(**kwargs, emit_event=event_queue.put)
        except BaseException as exc:
            result_holder["error"] = exc
        finally:
            event_queue.put(done_marker)

    worker = threading.Thread(
        target=lambda: caller_context.run(run),
        daemon=True,
        name="chat_context_budget",
    )
    worker.start()

    while True:
        if stop_event is not None and stop_event.is_set():
            raise ContextBudgetCancelledError("聊天上下文预算已取消")
        try:
            item = event_queue.get(timeout=0.05)
        except queue.Empty:
            continue
        if item is done_marker:
            break
        if isinstance(item, dict):
            yield item

    worker.join()
    error = result_holder.get("error")
    if isinstance(error, BaseException):
        raise error
    result = result_holder.get("result")
    if not isinstance(result, ContextBudgetResult):
        raise RuntimeError("上下文预算操作未返回有效结果")
    return result


@dataclass(slots=True)
class ContextBudgetResult:
    messages: List[BaseMessage]
    compacted: bool = False
    original_tokens: int = 0
    compacted_tokens: int = 0
    retained_messages: int = 0
    checkpoint: Dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PromptSectionBudget:
    """专有工作模式 user prompt 的可裁剪区块规则。"""

    heading: str
    min_chars: int = 800
    floor_ratio: float = 0.25
    protected: bool = False


@dataclass(frozen=True, slots=True)
class ContextBudgetPolicy:
    """模型上下文窗口的自适应预算策略。"""

    hard_budget: int
    trigger_budget: int
    reserved_context: int
    reserved_output: int
    safety_margin: int
    trigger_ratio: float


@dataclass(frozen=True, slots=True)
class ContextCompactionBudget:
    """一次历史压缩的目标工作集和摘要/近期原文分配。"""

    target_ratio: float
    target_context_tokens: int
    summary_tokens: int
    recent_tokens: int
    required_tokens: int


def _compaction_target_ratio(max_context: int, agent_id: str) -> float:
    context = max(int(max_context or 0), 1)
    first_context, first_ratio = COMPACTION_TARGET_RATIOS[0]
    base_ratio = first_ratio
    if context > first_context:
        base_ratio = COMPACTION_TARGET_RATIOS[-1][1]
        previous_context, previous_ratio = COMPACTION_TARGET_RATIOS[0]
        for upper_bound, ratio in COMPACTION_TARGET_RATIOS[1:]:
            if context <= upper_bound:
                progress = (context - previous_context) / max(1, upper_bound - previous_context)
                base_ratio = previous_ratio + (ratio - previous_ratio) * progress
                break
            previous_context, previous_ratio = upper_bound, ratio
    adjusted = base_ratio + float(COMPACTION_AGENT_RATIO_OFFSETS.get(str(agent_id or ""), 0.0))
    return min(max(adjusted, 0.10), 0.30)


def _compaction_budget(
    max_context: int,
    max_output: int,
    agent_id: str,
    required_tokens: int = 0,
) -> ContextCompactionBudget:
    """按模型窗口和 Agent 特征计算压缩后工作集，不把窗口剩余空间全部回填给旧历史。"""
    context = max(int(max_context or 0), 1_024)
    hard_budget, _ = _budget_limits(context, max_output)
    required = max(int(required_tokens or 0), 0)
    ratio = _compaction_target_ratio(context, agent_id)
    desired = max(MIN_COMPACTION_RECENT_TOKENS, int(context * ratio))
    # 当前请求及工具闭合单元不可丢弃；必要时允许它们抬高目标，但不超过 hard_budget。
    target_context = min(hard_budget, max(desired, required + MIN_COMPACTION_SUMMARY_TOKENS))
    available_history = max(0, target_context - required)
    summary_ratio = min(
        max(float(COMPACTION_AGENT_SUMMARY_RATIOS.get(str(agent_id or ""), 0.52)), 0.35),
        0.70,
    )
    if available_history <= MIN_COMPACTION_SUMMARY_TOKENS:
        summary_tokens = available_history
        recent_tokens = 0
    else:
        output_limit = max(int(max_output or 0), MIN_COMPACTION_SUMMARY_TOKENS)
        summary_output_capacity = max(
            MIN_COMPACTION_SUMMARY_TOKENS,
            output_limit - max(256, int(output_limit * 0.05)),
        )
        summary_tokens = max(
            MIN_COMPACTION_SUMMARY_TOKENS,
            int(available_history * summary_ratio),
        )
        summary_tokens = min(summary_tokens, available_history, summary_output_capacity)
        recent_tokens = available_history - summary_tokens
    return ContextCompactionBudget(
        target_ratio=ratio,
        target_context_tokens=target_context,
        summary_tokens=summary_tokens,
        recent_tokens=recent_tokens,
        required_tokens=required,
    )


def _coerce_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def _history_source_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return {
        "message_id": item.get("id"),
        "role": str(item.get("role") or "").strip(),
        "metadata": dict(metadata),
    }


def _history_message_kwargs(item: Dict[str, Any]) -> Dict[str, Any]:
    # response_metadata 不会作为 OpenAI 消息字段发送给上游，只用于本地追踪压缩边界。
    return {"response_metadata": {"spark_history": _history_source_metadata(item)}}


def _history_to_messages(history: List[Dict[str, Any]] | None) -> List[BaseMessage]:
    messages: List[BaseMessage] = []
    for msg in history or []:
        role = str(msg.get("role") or "").strip()
        content = _coerce_content(msg.get("content")).strip()
        if not content:
            continue
        metadata = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
        if role == "system" and metadata.get("kind") in {CONTEXT_CHECKPOINT_KIND, LEGACY_CONTEXT_SUMMARY_KIND}:
            messages = [SystemMessage(
                content=(
                    "【已压缩的早期上下文】\n"
                    "以下内容是系统生成的内部创作交接摘要，请把它视为此前对话事实、"
                    "用户意图与工作进度，不要向用户解释压缩过程。\n"
                    f"{content}"
                ),
                **_history_message_kwargs(msg),
            )]
            continue
        if role == "user":
            messages.append(HumanMessage(content=content, **_history_message_kwargs(msg)))
        elif role == "assistant":
            messages.append(AIMessage(content=content, **_history_message_kwargs(msg)))
    return messages


def _message_text(message: BaseMessage) -> str:
    role = getattr(message, "type", "") or message.__class__.__name__
    return f"{role}: {_coerce_content(getattr(message, 'content', ''))}"


def _message_type(message: BaseMessage) -> str:
    return str(getattr(message, "type", "") or "").strip().lower()


def _has_tool_calls(message: BaseMessage) -> bool:
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        return True
    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict) and additional_kwargs.get("tool_calls"):
        return True
    return False


def _repair_tool_boundary(body_messages: List[BaseMessage], retained_messages: List[BaseMessage]) -> List[BaseMessage]:
    """避免预算裁剪把 AI 工具调用与 ToolMessage 响应切开。"""
    if not retained_messages:
        return retained_messages

    start = max(0, len(body_messages) - len(retained_messages))
    while start > 0 and _message_type(body_messages[start]) == "tool":
        start -= 1
    if start > 0 and _message_type(body_messages[start - 1]) in {"ai", "assistant"} and _has_tool_calls(body_messages[start - 1]):
        start -= 1
    return list(body_messages[start:])


def _drop_oldest_message_unit(messages: List[BaseMessage]) -> None:
    """按工具调用单元丢弃最老消息，避免留下孤立 ToolMessage。"""
    if not messages:
        return
    first_type = _message_type(messages[0])
    if first_type == "tool":
        while messages and _message_type(messages[0]) == "tool":
            messages.pop(0)
        return
    if first_type in {"ai", "assistant"} and _has_tool_calls(messages[0]):
        messages.pop(0)
        while messages and _message_type(messages[0]) == "tool":
            messages.pop(0)
        return
    messages.pop(0)


def _messages_tokens(messages: List[BaseMessage], model_name: str | None) -> int:
    if not messages:
        return 0
    return estimate_tokens("\n\n".join(_message_text(m) for m in messages), model=model_name)


def _message_turn_units(messages: Sequence[BaseMessage]) -> List[List[BaseMessage]]:
    """按 user turn 形成不可拆分单元，工具调用和结果自然留在同一轮。"""
    units: List[List[BaseMessage]] = []
    current: List[BaseMessage] = []
    for message in messages:
        if _message_type(message) in {"human", "user"} and current:
            units.append(current)
            current = []
        current.append(message)
    if current:
        units.append(current)
    return units


def _retain_recent_message_units(
    messages: Sequence[BaseMessage],
    *,
    token_budget: int,
    model_name: str | None,
) -> tuple[List[BaseMessage], List[BaseMessage]]:
    """从尾部保留完整 turn；边界轮过大时整轮进入摘要，避免半轮语义和工具协议断裂。"""
    units = _message_turn_units(messages)
    retained_reversed: List[List[BaseMessage]] = []
    used = 0
    budget = max(int(token_budget or 0), 0)
    for unit in reversed(units):
        cost = _messages_tokens(list(unit), model_name)
        if used + cost > budget:
            break
        retained_reversed.append(unit)
        used += cost
    retained_units = list(reversed(retained_reversed))
    retained = [message for unit in retained_units for message in unit]
    overflow_count = max(0, len(messages) - len(retained))
    return list(messages[:overflow_count]), retained


def _messages_to_history_items(messages: List[BaseMessage]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for message in messages:
        msg_type = getattr(message, "type", "") or ""
        if msg_type in {"ai", "assistant"}:
            role = "assistant"
        elif msg_type == "system":
            role = "system"
        elif msg_type == "tool":
            role = "tool"
        else:
            role = "user"
        item: Dict[str, Any] = {
            "role": role,
            "content": _coerce_content(getattr(message, "content", "")),
        }
        source = _message_history_metadata(message)
        message_id = _coerce_positive_int(source.get("message_id"))
        if message_id is not None:
            item["message_id"] = message_id
        metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        if str(metadata.get("kind") or "") in {
            CONTEXT_CHECKPOINT_KIND,
            LEGACY_CONTEXT_SUMMARY_KIND,
        }:
            item["source_message_id_start"] = metadata.get("source_message_id_start")
            item["source_message_id_end"] = (
                metadata.get("source_message_id_end")
                or metadata.get("compacted_through_message_id")
            )
        items.append(item)
    return items


def _message_history_metadata(message: BaseMessage) -> Dict[str, Any]:
    response_metadata = getattr(message, "response_metadata", None)
    if not isinstance(response_metadata, dict):
        return {}
    value = response_metadata.get("spark_history")
    return dict(value) if isinstance(value, dict) else {}


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _checkpoint_source_stats(messages: Sequence[BaseMessage]) -> Dict[str, Any]:
    """把既有 checkpoint 与新原文合并成一个连续、可审计的来源范围。"""
    first_ids: List[int] = []
    last_ids: List[int] = []
    original_messages = 0

    for message in messages:
        source = _message_history_metadata(message)
        metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        kind = str(metadata.get("kind") or "")
        if kind in {CONTEXT_CHECKPOINT_KIND, LEGACY_CONTEXT_SUMMARY_KIND}:
            first_id = _coerce_positive_int(
                metadata.get("source_message_id_start") or metadata.get("first_source_message_id")
            )
            last_id = _coerce_positive_int(
                metadata.get("compacted_through_message_id")
                or metadata.get("source_message_id_end")
                or metadata.get("last_source_message_id")
            )
            count = _coerce_positive_int(
                metadata.get("original_messages") or metadata.get("source_message_count")
            ) or 0
        else:
            message_id = _coerce_positive_int(source.get("message_id"))
            first_id = message_id
            last_id = message_id
            count = 1 if message_id is not None else 0

        if first_id is not None:
            first_ids.append(first_id)
        if last_id is not None:
            last_ids.append(last_id)
        original_messages += count

    return {
        "source_message_id_start": min(first_ids) if first_ids else None,
        "source_message_id_end": max(last_ids) if last_ids else None,
        "compacted_through_message_id": max(last_ids) if last_ids else None,
        "original_messages": original_messages,
    }


def build_context_checkpoint_payload(
    *,
    summary: Dict[str, Any],
    source_messages: Sequence[BaseMessage],
    source: str,
    agent_id: str,
    model_name: str,
    target_tokens: int,
    original_tokens: int,
    compacted_tokens: int,
    retained_messages: int,
) -> Dict[str, Any] | None:
    """构造可由持久化层幂等保存的 checkpoint 候选，不执行任何数据库操作。"""
    source_stats = _checkpoint_source_stats(source_messages)
    if source_stats.get("compacted_through_message_id") is None:
        return None
    return {
        "summary": dict(summary or {}),
        "metadata": {
            "kind": CONTEXT_CHECKPOINT_KIND,
            "schema_version": 1,
            "source": str(source or "automatic_compaction"),
            "agent_id": str(agent_id or ""),
            "model": str(model_name or ""),
            "target_tokens": max(int(target_tokens or 0), 0),
            "original_tokens": max(int(original_tokens or 0), 0),
            "compacted_tokens": max(int(compacted_tokens or 0), 0),
            "retained_messages": max(int(retained_messages or 0), 0),
            "created_at": int(time.time()),
            **source_stats,
        },
    }


def build_context_checkpoint_payload_from_history(
    *,
    summary: Dict[str, Any],
    history: Sequence[Dict[str, Any]],
    source: str,
    agent_id: str,
    model_name: str,
    target_tokens: int,
    original_tokens: int,
    compacted_tokens: int,
    retained_messages: int = 0,
) -> Dict[str, Any] | None:
    """供手动压缩复用的历史字典入口。"""
    return build_context_checkpoint_payload(
        summary=summary,
        source_messages=_history_to_messages(list(history)),
        source=source,
        agent_id=agent_id,
        model_name=model_name,
        target_tokens=target_tokens,
        original_tokens=original_tokens,
        compacted_tokens=compacted_tokens,
        retained_messages=retained_messages,
    )


def partition_history_for_manual_compaction(
    history: Sequence[Dict[str, Any]],
    *,
    keep_recent_turns: int = 2,
    recent_token_budget: int | None = None,
    model_name: str | None = None,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把运行时历史切成待摘要旧历史与保留原文的最近完整轮次。"""
    filtered = [
        dict(item)
        for item in history
        if str(item.get("role") or "") in {"system", "user", "assistant"}
        and str(item.get("content") or "").strip()
    ]
    if not filtered:
        return [], []

    user_indices = [index for index, item in enumerate(filtered) if str(item.get("role") or "") == "user"]
    if recent_token_budget is not None:
        turn_starts = user_indices or [0]
        turn_ranges = [
            (start, turn_starts[index + 1] if index + 1 < len(turn_starts) else len(filtered))
            for index, start in enumerate(turn_starts)
        ]
        used = 0
        split_at = len(filtered)
        for start, end in reversed(turn_ranges):
            cost = estimate_tokens(
                json.dumps(filtered[start:end], ensure_ascii=False),
                model=model_name or None,
            )
            if used + cost > max(int(recent_token_budget or 0), 0):
                break
            used += cost
            split_at = start
    elif not user_indices:
        split_at = max(0, len(filtered) - 1)
    else:
        turns = max(1, int(keep_recent_turns or 1))
        split_at = user_indices[max(0, len(user_indices) - turns)]

    return filtered[:split_at], filtered[split_at:]


def _get_model_name(llm_client: Any) -> str:
    usage = getattr(llm_client, "usage", None)
    return str(getattr(usage, "model_name", "") or getattr(llm_client, "model_name", "") or "").strip()


def _get_limits(llm_client: Any) -> tuple[int, int]:
    max_context = int(getattr(llm_client, "max_context_tokens", 0) or DEFAULT_CONTEXT_WINDOW_FALLBACK_TOKENS)
    max_output = int(getattr(llm_client, "max_output_tokens", 0) or 4096)
    return max(max_context, 1024), max(max_output, 256)


def _context_budget_policy(max_context: int, max_output: int) -> ContextBudgetPolicy:
    """分别预留模型输出空间与压缩安全缓冲，再推导输入预算。"""
    context = max(int(max_context or 0), 1024)
    output_limit = max(int(max_output or 0), 256)
    available_reserve = max(0, context - MIN_CONTEXT_BUDGET_TOKENS)
    reserved_output = min(
        output_limit,
        MAX_OUTPUT_RESERVE_TOKENS,
        available_reserve,
    )

    # 默认 256K 窗口保留 16K 安全缓冲；大窗口继续按 6.25% 连续增长，
    # 小窗口则按 10% 平滑缩放，避免固定缓冲吞掉全部输入预算。
    small_context_floor = min(
        CONTEXT_SAFETY_FLOOR_TOKENS,
        int(context * SMALL_CONTEXT_RESERVE_RATIO),
    )
    safety_margin = max(
        small_context_floor,
        int(context * CONTEXT_SAFETY_RATIO),
    )
    safety_margin = min(
        safety_margin,
        max(0, available_reserve - reserved_output),
    )
    reserved_context = reserved_output + safety_margin
    hard_budget = max(MIN_CONTEXT_BUDGET_TOKENS, context - reserved_context)
    trigger_budget = hard_budget
    trigger_ratio = trigger_budget / context if context else 0.0
    return ContextBudgetPolicy(
        hard_budget=hard_budget,
        trigger_budget=trigger_budget,
        reserved_context=reserved_context,
        reserved_output=reserved_output,
        safety_margin=safety_margin,
        trigger_ratio=trigger_ratio,
    )


def _budget_limits(max_context: int, max_output: int) -> tuple[int, int]:
    policy = _context_budget_policy(max_context, max_output)
    return policy.hard_budget, policy.trigger_budget


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return max(0.0, float(numerator or 0) / float(denominator))


def _append_event(emit_event: Callable[[Dict[str, Any]], None] | None, payload: Dict[str, Any]) -> None:
    if emit_event is None:
        return
    try:
        emit_event(payload)
    except Exception:
        pass


def _emit_started(
    emit_event: Callable[[Dict[str, Any]], None] | None,
    *,
    original_tokens: int,
    model_name: str,
    retained_messages: int,
    reason: str,
) -> None:
    _append_event(emit_event, {
        "event": "context_compaction_started",
        "original_tokens": original_tokens,
        "model": model_name,
        "retained_messages": retained_messages,
        "reason": reason,
    })


def _emit_finished(
    emit_event: Callable[[Dict[str, Any]], None] | None,
    *,
    original_tokens: int,
    compacted_tokens: int,
    retained_messages: int,
    model_name: str,
) -> None:
    _append_event(emit_event, {
        "event": "context_compaction_finished",
        "original_tokens": original_tokens,
        "compacted_tokens": compacted_tokens,
        "retained_messages": retained_messages,
        "model": model_name,
    })


def _emit_failed(
    emit_event: Callable[[Dict[str, Any]], None] | None,
    *,
    original_tokens: int,
    compacted_tokens: int,
    retained_messages: int,
    model_name: str,
    error: Exception,
) -> None:
    _append_event(emit_event, {
        "event": "context_compaction_failed",
        "original_tokens": original_tokens,
        "compacted_tokens": compacted_tokens,
        "retained_messages": retained_messages,
        "model": model_name,
        "message": str(error),
    })


def _emit_context_window_stats(
    emit_event: Callable[[Dict[str, Any]], None] | None,
    *,
    agent_id: str,
    original_tokens: int,
    input_tokens: int,
    retained_messages: int,
    model_name: str,
    max_context_tokens: int,
    max_output_tokens: int,
    hard_budget: int,
    trigger_budget: int,
    compacted: bool,
    reason: str,
) -> None:
    clean_input = max(int(input_tokens or 0), 0)
    clean_original = max(int(original_tokens or 0), 0)
    clean_context = max(int(max_context_tokens or 0), 1)
    clean_hard = max(int(hard_budget or 0), 1)
    clean_trigger = max(int(trigger_budget or 0), 1)
    policy = _context_budget_policy(max_context_tokens, max_output_tokens)
    compaction_budget = _compaction_budget(
        max_context_tokens,
        max_output_tokens,
        agent_id,
    )
    _append_event(emit_event, {
        "event": "context_window_stats",
        "agent_id": agent_id,
        "source_agent": agent_id,
        "input_tokens": clean_input,
        "original_tokens": clean_original,
        "retained_messages": max(int(retained_messages or 0), 0),
        "model": model_name,
        "max_context_tokens": max_context_tokens,
        "max_output_tokens": max_output_tokens,
        "hard_budget": hard_budget,
        "trigger_budget": trigger_budget,
        "reserved_context_tokens": policy.reserved_context,
        "reserved_output_tokens": policy.reserved_output,
        "safety_margin_tokens": policy.safety_margin,
        "trigger_ratio": round(policy.trigger_ratio, 4),
        "compaction_target_ratio": round(compaction_budget.target_ratio, 4),
        "compaction_target_tokens": compaction_budget.target_context_tokens,
        "summary_budget_tokens": compaction_budget.summary_tokens,
        "recent_budget_tokens": compaction_budget.recent_tokens,
        "usage_ratio": round(_safe_ratio(clean_input, clean_context), 4),
        "original_usage_ratio": round(_safe_ratio(clean_original, clean_context), 4),
        "hard_usage_ratio": round(_safe_ratio(clean_input, clean_hard), 4),
        "trigger_usage_ratio": round(_safe_ratio(clean_input, clean_trigger), 4),
        "compacted": bool(compacted),
        "reason": reason,
    })


def _clamp_ratio(value: float) -> float:
    try:
        ratio = float(value)
    except Exception:
        return 0.25
    return min(max(ratio, 0.0), 1.0)


def _truncate_middle(text: str, target_chars: int) -> str:
    """保留首尾，压缩中段；适合全局设定/大纲/旧前文这种可恢复材料。"""
    clean = str(text or "")
    target = max(int(target_chars or 0), 0)
    if target <= 0 or len(clean) <= target:
        return clean
    marker = "\n...（中间内容因上下文预算已截断；优先保留当前场景事实包、场景契约、创作指导与最近上下文）...\n"
    if target <= len(marker) + 80:
        return clean[:target].rstrip() + "\n...（内容因上下文预算已截断）"
    head = max(40, int((target - len(marker)) * 0.6))
    tail = max(40, target - len(marker) - head)
    return (clean[:head].rstrip() + marker + clean[-tail:].lstrip()).strip()


def _priority_prefix_end(text: str) -> int:
    """识别嵌在“前文/上下文”区块顶部的高优先级写作任务包。"""
    high_markers = ("=== 当前场景事实包", "【当前大纲场景契约】", "### Director→Scriptwriter 场景交接包")
    if not any(marker in text for marker in high_markers):
        return 0
    low_markers = (
        "\n=== 前序各章节末尾场景",
        "\n=== 当前章节前文",
        "\n=== 当前章节前文（已完成场景）",
        "\n# ",
        "\n## ",
    )
    starts = [text.find(marker) for marker in high_markers if text.find(marker) >= 0]
    if not starts:
        return 0
    first_high = min(starts)
    candidates = [text.find(marker, first_high + 1) for marker in low_markers]
    candidates = [idx for idx in candidates if idx > first_high]
    return min(candidates) if candidates else 0


def _truncate_section_text(text: str, target_chars: int) -> str:
    prefix_end = _priority_prefix_end(text)
    if prefix_end <= 0:
        return _truncate_middle(text, target_chars)

    protected_prefix = text[:prefix_end].rstrip()
    low_priority_tail = text[prefix_end:].lstrip()
    if len(text) <= target_chars:
        return text
    if len(protected_prefix) >= target_chars:
        return protected_prefix + "\n...（低优先级前文因上下文预算已省略；高优先级任务包已完整保留）"
    tail_target = max(400, target_chars - len(protected_prefix) - 80)
    return (
        protected_prefix
        + "\n\n...（以下低优先级历史前文因上下文预算已压缩；高优先级任务包已完整保留）...\n"
        + _truncate_middle(low_priority_tail, tail_target)
    ).strip()


def _section_budget_for_heading(
    heading: str,
    section_budgets: Sequence[PromptSectionBudget],
) -> PromptSectionBudget:
    clean_heading = str(heading or "").strip()
    for rule in section_budgets:
        if rule.heading and rule.heading in clean_heading:
            return rule
    return PromptSectionBudget(clean_heading, min_chars=600, floor_ratio=0.35)


def _split_markdown_heading_sections(text: str) -> List[Dict[str, str]]:
    """按二/三级标题拆分 prompt，保留标题前导文本。"""
    lines = str(text or "").splitlines(keepends=True)
    sections: List[Dict[str, str]] = []
    current_heading = ""
    current_lines: List[str] = []

    def flush() -> None:
        if current_lines or current_heading:
            sections.append({
                "heading": current_heading,
                "text": "".join(current_lines),
            })

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### "):
            flush()
            current_heading = stripped
            current_lines = [line]
        else:
            current_lines.append(line)
    flush()
    return sections


def _truncate_user_prompt_sections(
    *,
    user_prompt: str,
    target_tokens: int,
    model_name: str,
    section_budgets: Sequence[PromptSectionBudget],
) -> str:
    """在专有工作模式中保护高优先级区块，只裁剪可恢复的大段材料。"""
    sections = _split_markdown_heading_sections(user_prompt)
    if not sections:
        return user_prompt

    current_prompt = user_prompt
    while estimate_tokens(current_prompt, model=model_name) > target_tokens:
        candidates: List[tuple[int, int, PromptSectionBudget, Dict[str, str]]] = []
        for idx, section in enumerate(sections):
            text = section.get("text") or ""
            rule = _section_budget_for_heading(section.get("heading") or "", section_budgets)
            if rule.protected:
                continue
            floor = max(int(rule.min_chars), int(len(text) * _clamp_ratio(rule.floor_ratio)))
            if len(text) > floor + 120:
                candidates.append((len(text) - floor, idx, rule, section))
        if not candidates:
            break

        _saving, idx, rule, section = max(candidates, key=lambda item: item[0])
        text = section.get("text") or ""
        floor = max(int(rule.min_chars), int(len(text) * _clamp_ratio(rule.floor_ratio)))
        next_len = max(floor, int(len(text) * 0.72))
        if next_len >= len(text):
            next_len = floor
        sections[idx] = {
            "heading": section.get("heading") or "",
            "text": _truncate_section_text(text, next_len),
        }
        current_prompt = "".join(section.get("text") or "" for section in sections)

    return current_prompt


DEFAULT_SPECIALIZED_SECTION_BUDGETS: tuple[PromptSectionBudget, ...] = (
    PromptSectionBudget("当前场景事实包", protected=True),
    PromptSectionBudget("当前大纲场景契约", protected=True),
    PromptSectionBudget("当前场景的创作指导", protected=True),
    PromptSectionBudget("创作指导/章节目标", protected=True),
    PromptSectionBudget("修正意见", protected=True),
    PromptSectionBudget("审阅目标", protected=True),
    PromptSectionBudget("待审阅剧本", protected=True),
    PromptSectionBudget("写作指导", protected=True),
    PromptSectionBudget("作者想参考的文学风格档案", min_chars=2400, floor_ratio=0.5),
    PromptSectionBudget("作者风格档案", min_chars=2400, floor_ratio=0.5),
    PromptSectionBudget("世界观背景", min_chars=2200, floor_ratio=0.35),
    PromptSectionBudget("世界观", min_chars=2200, floor_ratio=0.35),
    PromptSectionBudget("全局大纲", min_chars=2600, floor_ratio=0.35),
    PromptSectionBudget("叙事记忆", min_chars=1600, floor_ratio=0.4),
    PromptSectionBudget("角色详细档案", min_chars=3600, floor_ratio=0.35),
    PromptSectionBudget("角色档案", min_chars=3600, floor_ratio=0.35),
    PromptSectionBudget("前文剧本", min_chars=5200, floor_ratio=0.45),
    PromptSectionBudget("当前上下文/前情", min_chars=5200, floor_ratio=0.45),
    PromptSectionBudget("前情提要", min_chars=5200, floor_ratio=0.45),
)


def prepare_specialized_prompt_messages_with_budget(
    *,
    agent_id: str,
    system_prompt: str,
    user_prompt: str,
    llm_client: Any,
    section_budgets: Sequence[PromptSectionBudget] | None = None,
    emit_event: Callable[[Dict[str, Any]], None] | None = None,
) -> ContextBudgetResult:
    """构造专有工作模式 messages，并在超预算时只裁动态 user 尾部材料。

    专有工作模式通常是固定 system 头 + 单条动态 user prompt。为维持上游
    prompt cache 稳定，预算保护只调整 user prompt 内的可恢复区块，不移动
    或改写 system prompt。
    """
    model_name = _get_model_name(llm_client)
    max_context, max_output = _get_limits(llm_client)
    policy = _context_budget_policy(max_context, max_output)
    hard_budget, trigger_budget = policy.hard_budget, policy.trigger_budget
    system_msg = SystemMessage(content=str(system_prompt or "").strip())
    user_msg = HumanMessage(content=str(user_prompt or "").strip())
    original_messages = [system_msg, user_msg]
    original_tokens = _messages_tokens(original_messages, model_name)
    if original_tokens <= hard_budget:
        _emit_context_window_stats(
            emit_event,
            agent_id=agent_id,
            original_tokens=original_tokens,
            input_tokens=original_tokens,
            retained_messages=1,
            model_name=model_name,
            max_context_tokens=max_context,
            max_output_tokens=max_output,
            hard_budget=hard_budget,
            trigger_budget=trigger_budget,
            compacted=False,
            reason="within_budget" if original_tokens <= trigger_budget else "within_hard_budget_high_usage",
        )
        return ContextBudgetResult(original_messages, False, original_tokens, original_tokens, 1)

    system_tokens = _messages_tokens([system_msg], model_name)
    user_budget = max(1024, hard_budget - system_tokens)
    truncated_user_prompt = _truncate_user_prompt_sections(
        user_prompt=str(user_prompt or "").strip(),
        target_tokens=user_budget,
        model_name=model_name,
        section_budgets=section_budgets or DEFAULT_SPECIALIZED_SECTION_BUDGETS,
    )
    compacted_messages = [system_msg, HumanMessage(content=truncated_user_prompt)]
    compacted_tokens = _messages_tokens(compacted_messages, model_name)
    _emit_context_window_stats(
        emit_event,
        agent_id=agent_id,
        original_tokens=original_tokens,
        input_tokens=compacted_tokens,
        retained_messages=1,
        model_name=model_name,
        max_context_tokens=max_context,
        max_output_tokens=max_output,
        hard_budget=hard_budget,
        trigger_budget=trigger_budget,
        compacted=compacted_tokens < original_tokens,
        reason="specialized_user_prompt_trimmed" if compacted_tokens < original_tokens else "specialized_prompt_over_budget_untrimmed",
    )
    return ContextBudgetResult(
        compacted_messages,
        compacted=compacted_tokens < original_tokens,
        original_tokens=original_tokens,
        compacted_tokens=compacted_tokens,
        retained_messages=1,
    )


def _compress_history_items(
    *,
    user_id: str,
    project_name: str,
    agent_id: str,
    model_name: str,
    target_tokens: int,
    current_user_message: str,
    overflow_messages: List[BaseMessage],
    source_system_message: SystemMessage | None = None,
    source_llm_client: Any = None,
    source_tools: Sequence[Any] | None = None,
) -> tuple[SystemMessage, Dict[str, Any]]:
    from agents.utility_agent import UtilityAgent

    utility = UtilityAgent(user_id=user_id, project_name=project_name)
    summary = utility.compress_chat_history(
        history_items=_messages_to_history_items(overflow_messages),
        agent_id=agent_id,
        model_name=model_name,
        target_tokens=target_tokens,
        current_user_message=current_user_message,
        agent_profile=COMPACTION_AGENT_PROFILES.get(
            str(agent_id or ""),
            "优先保留用户目标、硬约束、关键事实、决策、进度、工具结论和开放任务；删除重复与无后续价值的过程内容。",
        ),
        source_prefix_messages=[
            *([source_system_message] if source_system_message is not None else []),
            *overflow_messages,
        ],
        source_llm_client=source_llm_client,
        source_tools=source_tools,
    )
    return (
        SystemMessage(content=(
            "【已压缩的早期上下文】\n"
            "以下内容是系统为避免上下文窗口溢出而生成的内部创作交接摘要，"
            "请把它视为此前对话事实、用户意图与工作进度，不要向用户解释压缩过程。\n"
            f"{json.dumps(summary, ensure_ascii=False, indent=2)}"
        )),
        summary,
    )


def _context_window_error(
    *,
    reason: str,
    model_name: str,
    input_tokens: int,
    max_context: int,
    max_output: int,
    hard_budget: int,
) -> ContextWindowIncompatibleError:
    return ContextWindowIncompatibleError(
        "当前模型的上下文窗口无法容纳必要的系统指令和本轮内容。请改用上下文更大的模型，或减少当前附件与编辑区内容。",
        reason=reason,
        details={
            "model": model_name,
            "input_tokens": max(int(input_tokens or 0), 0),
            "max_context_tokens": max(int(max_context or 0), 0),
            "max_output_tokens": max(int(max_output or 0), 0),
            "hard_budget": max(int(hard_budget or 0), 0),
        },
    )


def prepare_chat_messages_with_budget(
    *,
    user_id: str,
    project_name: str,
    agent_id: str,
    system_instruction: str,
    history: List[Dict[str, Any]] | None,
    user_message: str,
    llm_client: Any,
    tools: Sequence[Any] | None = None,
    emit_event: Callable[[Dict[str, Any]], None] | None = None,
) -> ContextBudgetResult:
    """构造聊天 messages，并在超过模型预算时自动压缩早期历史。"""
    model_name = _get_model_name(llm_client)
    max_context, max_output = _get_limits(llm_client)
    hard_budget, trigger_budget = _budget_limits(max_context, max_output)

    system_msg = SystemMessage(content=system_instruction)
    current_msg = HumanMessage(content=user_message)
    history_messages = _history_to_messages(history)
    full_messages = [system_msg, *history_messages, current_msg]
    original_tokens = _messages_tokens(full_messages, model_name)
    if original_tokens <= hard_budget and original_tokens <= trigger_budget:
        _emit_context_window_stats(
            emit_event,
            agent_id=agent_id,
            original_tokens=original_tokens,
            input_tokens=original_tokens,
            retained_messages=len(history_messages),
            model_name=model_name,
            max_context_tokens=max_context,
            max_output_tokens=max_output,
            hard_budget=hard_budget,
            trigger_budget=trigger_budget,
            compacted=False,
            reason="within_budget",
        )
        return ContextBudgetResult(full_messages, False, original_tokens, original_tokens, len(history_messages))

    base_tokens = _messages_tokens([system_msg, current_msg], model_name)
    if base_tokens > hard_budget:
        _emit_context_window_stats(
            emit_event,
            agent_id=agent_id,
            original_tokens=original_tokens,
            input_tokens=base_tokens,
            retained_messages=0,
            model_name=model_name,
            max_context_tokens=max_context,
            max_output_tokens=max_output,
            hard_budget=hard_budget,
            trigger_budget=trigger_budget,
            compacted=False,
            reason="required_context_exceeds_model_window",
        )
        raise _context_window_error(
            reason="required_context_exceeds_model_window",
            model_name=model_name,
            input_tokens=base_tokens,
            max_context=max_context,
            max_output=max_output,
            hard_budget=hard_budget,
        )

    compaction_budget = _compaction_budget(
        max_context,
        max_output,
        agent_id,
        required_tokens=base_tokens,
    )
    summary_reserved = compaction_budget.summary_tokens
    recent_budget = compaction_budget.recent_tokens
    overflow_messages, retained_messages = _retain_recent_message_units(
        history_messages,
        token_budget=recent_budget,
        model_name=model_name,
    )

    if not overflow_messages:
        compacted_messages = [system_msg, *retained_messages, current_msg]
        compacted_tokens = _messages_tokens(compacted_messages, model_name)
        if compacted_tokens > hard_budget:
            raise _context_window_error(
                reason="minimum_history_unit_exceeds_model_window",
                model_name=model_name,
                input_tokens=compacted_tokens,
                max_context=max_context,
                max_output=max_output,
                hard_budget=hard_budget,
            )
        _emit_context_window_stats(
            emit_event,
            agent_id=agent_id,
            original_tokens=original_tokens,
            input_tokens=compacted_tokens,
            retained_messages=len(retained_messages),
            model_name=model_name,
            max_context_tokens=max_context,
            max_output_tokens=max_output,
            hard_budget=hard_budget,
            trigger_budget=trigger_budget,
            compacted=False,
            reason="within_hard_budget_high_usage",
        )
        return ContextBudgetResult(compacted_messages, False, original_tokens, compacted_tokens, len(retained_messages))

    _emit_started(
        emit_event,
        original_tokens=original_tokens,
        model_name=model_name,
        retained_messages=len(retained_messages),
        reason="context_budget_exceeded",
    )

    try:
        summary_msg, summary = _compress_history_items(
            user_id=user_id,
            project_name=project_name,
            agent_id=agent_id,
            model_name=model_name,
            target_tokens=summary_reserved,
            current_user_message=user_message,
            overflow_messages=overflow_messages,
            source_system_message=system_msg,
            source_llm_client=llm_client,
            source_tools=tools,
        )
        compacted_messages = [system_msg, summary_msg, *retained_messages, current_msg]
        compacted_tokens = _messages_tokens(compacted_messages, model_name)
        if compacted_tokens > hard_budget:
            raise _context_window_error(
                reason="compacted_context_exceeds_model_window",
                model_name=model_name,
                input_tokens=compacted_tokens,
                max_context=max_context,
                max_output=max_output,
                hard_budget=hard_budget,
            )
        checkpoint = build_context_checkpoint_payload(
            summary=summary,
            source_messages=overflow_messages,
            source="automatic_compaction",
            agent_id=agent_id,
            model_name=model_name,
            target_tokens=summary_reserved,
            original_tokens=original_tokens,
            compacted_tokens=compacted_tokens,
            retained_messages=len(retained_messages),
        )
        _emit_finished(
            emit_event,
            original_tokens=original_tokens,
            compacted_tokens=compacted_tokens,
            retained_messages=len(retained_messages),
            model_name=model_name,
        )
        if checkpoint is not None:
            _append_event(emit_event, {
                "event": CONTEXT_CHECKPOINT_READY_EVENT,
                "checkpoint": checkpoint,
            })
        _emit_context_window_stats(
            emit_event,
            agent_id=agent_id,
            original_tokens=original_tokens,
            input_tokens=compacted_tokens,
            retained_messages=len(retained_messages),
            model_name=model_name,
            max_context_tokens=max_context,
            max_output_tokens=max_output,
            hard_budget=hard_budget,
            trigger_budget=trigger_budget,
            compacted=True,
            reason="context_compacted",
        )
        return ContextBudgetResult(
            compacted_messages,
            True,
            original_tokens,
            compacted_tokens,
            len(retained_messages),
            checkpoint,
        )
    except NonRetryableChatError:
        raise
    except Exception as exc:
        compacted_tokens = _messages_tokens([system_msg, *retained_messages, current_msg], model_name)
        _emit_failed(
            emit_event,
            original_tokens=original_tokens,
            compacted_tokens=compacted_tokens,
            retained_messages=len(retained_messages),
            model_name=model_name,
            error=exc,
        )
        _emit_context_window_stats(
            emit_event,
            agent_id=agent_id,
            original_tokens=original_tokens,
            input_tokens=compacted_tokens,
            retained_messages=len(retained_messages),
            model_name=model_name,
            max_context_tokens=max_context,
            max_output_tokens=max_output,
            hard_budget=hard_budget,
            trigger_budget=trigger_budget,
            compacted=False,
            reason="context_compaction_failed",
        )
        raise ContextCompactionFailedError(
            "上下文压缩失败。为避免丢失早期对话，本次请求已停止，请稍后重试或检查当前 Agent 的模型配置。",
            reason="context_compaction_failed",
            details={
                "model": model_name,
                "original_tokens": original_tokens,
                "input_tokens": compacted_tokens,
                "max_context_tokens": max_context,
            },
        ) from exc


def rebudget_existing_messages(
    *,
    user_id: str,
    project_name: str,
    agent_id: str,
    messages: List[BaseMessage],
    llm_client: Any,
    tools: Sequence[Any] | None = None,
    emit_event: Callable[[Dict[str, Any]], None] | None = None,
    current_user_message: str = "",
) -> ContextBudgetResult:
    """对已经进入多轮工具循环的 LangChain messages 再做一次预算压缩。"""
    if len(messages) <= 2:
        model_name = _get_model_name(llm_client)
        max_context, max_output = _get_limits(llm_client)
        hard_budget, trigger_budget = _budget_limits(max_context, max_output)
        tokens = _messages_tokens(messages, model_name)
        if tokens > hard_budget:
            raise _context_window_error(
                reason="required_tool_loop_context_exceeds_model_window",
                model_name=model_name,
                input_tokens=tokens,
                max_context=max_context,
                max_output=max_output,
                hard_budget=hard_budget,
            )
        _emit_context_window_stats(
            emit_event,
            agent_id=agent_id,
            original_tokens=tokens,
            input_tokens=tokens,
            retained_messages=max(0, len(messages) - 1),
            model_name=model_name,
            max_context_tokens=max_context,
            max_output_tokens=max_output,
            hard_budget=hard_budget,
            trigger_budget=trigger_budget,
            compacted=False,
            reason="within_budget",
        )
        return ContextBudgetResult(messages, False, tokens, tokens, max(0, len(messages) - 1))

    model_name = _get_model_name(llm_client)
    max_context, max_output = _get_limits(llm_client)
    hard_budget, trigger_budget = _budget_limits(max_context, max_output)
    original_tokens = _messages_tokens(messages, model_name)
    if original_tokens <= hard_budget and original_tokens <= trigger_budget:
        _emit_context_window_stats(
            emit_event,
            agent_id=agent_id,
            original_tokens=original_tokens,
            input_tokens=original_tokens,
            retained_messages=max(0, len(messages) - 1),
            model_name=model_name,
            max_context_tokens=max_context,
            max_output_tokens=max_output,
            hard_budget=hard_budget,
            trigger_budget=trigger_budget,
            compacted=False,
            reason="within_budget",
        )
        return ContextBudgetResult(messages, False, original_tokens, original_tokens, max(0, len(messages) - 1))

    system_msg = messages[0]
    body_messages = messages[1:]

    # 当前用户消息及其后续工具循环不可压缩。否则模型可能在同一轮任务中忘掉
    # 用户原话，或把刚执行完的工具结果与其触发请求拆开。
    current_user_index = -1
    for index in range(len(body_messages) - 1, -1, -1):
        if _message_type(body_messages[index]) in {"human", "user"}:
            current_user_index = index
            break
    if current_user_index >= 0:
        compressible_body = body_messages[:current_user_index]
        required_tail = body_messages[current_user_index:]
    else:
        compressible_body = body_messages
        required_tail = []

    base_tokens = _messages_tokens([system_msg, *required_tail], model_name)
    if base_tokens > hard_budget:
        raise _context_window_error(
            reason="required_tool_loop_context_exceeds_model_window",
            model_name=model_name,
            input_tokens=base_tokens,
            max_context=max_context,
            max_output=max_output,
            hard_budget=hard_budget,
        )
    compaction_budget = _compaction_budget(
        max_context,
        max_output,
        agent_id,
        required_tokens=base_tokens,
    )
    summary_reserved = compaction_budget.summary_tokens
    recent_budget = compaction_budget.recent_tokens
    overflow_messages, retained_messages = _retain_recent_message_units(
        compressible_body,
        token_budget=recent_budget,
        model_name=model_name,
    )
    retained_count = len(retained_messages) + len(required_tail)
    if not overflow_messages:
        compacted = [system_msg, *retained_messages, *required_tail]
        compacted_tokens = _messages_tokens(compacted, model_name)
        if compacted_tokens > hard_budget:
            raise _context_window_error(
                reason="tool_loop_minimum_unit_exceeds_model_window",
                model_name=model_name,
                input_tokens=compacted_tokens,
                max_context=max_context,
                max_output=max_output,
                hard_budget=hard_budget,
            )
        _emit_context_window_stats(
            emit_event,
            agent_id=agent_id,
            original_tokens=original_tokens,
            input_tokens=compacted_tokens,
            retained_messages=retained_count,
            model_name=model_name,
            max_context_tokens=max_context,
            max_output_tokens=max_output,
            hard_budget=hard_budget,
            trigger_budget=trigger_budget,
            compacted=False,
            reason="within_hard_budget_high_usage",
        )
        return ContextBudgetResult(compacted, False, original_tokens, compacted_tokens, retained_count)

    _emit_started(
        emit_event,
        original_tokens=original_tokens,
        model_name=model_name,
        retained_messages=retained_count,
        reason="tool_loop_context_budget_exceeded",
    )

    try:
        summary_msg, _summary = _compress_history_items(
            user_id=user_id,
            project_name=project_name,
            agent_id=agent_id,
            model_name=model_name,
            target_tokens=summary_reserved,
            current_user_message=current_user_message,
            overflow_messages=overflow_messages,
            source_system_message=system_msg,
            source_llm_client=llm_client,
            source_tools=tools,
        )
        compacted = [system_msg, summary_msg, *retained_messages, *required_tail]
        compacted_tokens = _messages_tokens(compacted, model_name)
        if compacted_tokens > hard_budget:
            raise _context_window_error(
                reason="tool_loop_compacted_context_exceeds_model_window",
                model_name=model_name,
                input_tokens=compacted_tokens,
                max_context=max_context,
                max_output=max_output,
                hard_budget=hard_budget,
            )
        _emit_finished(
            emit_event,
            original_tokens=original_tokens,
            compacted_tokens=compacted_tokens,
            retained_messages=retained_count,
            model_name=model_name,
        )
        _emit_context_window_stats(
            emit_event,
            agent_id=agent_id,
            original_tokens=original_tokens,
            input_tokens=compacted_tokens,
            retained_messages=retained_count,
            model_name=model_name,
            max_context_tokens=max_context,
            max_output_tokens=max_output,
            hard_budget=hard_budget,
            trigger_budget=trigger_budget,
            compacted=True,
            reason="tool_loop_context_compacted",
        )
        return ContextBudgetResult(compacted, True, original_tokens, compacted_tokens, retained_count)
    except NonRetryableChatError:
        raise
    except Exception as exc:
        compacted_tokens = _messages_tokens(
            [system_msg, *retained_messages, *required_tail],
            model_name,
        )
        _emit_failed(
            emit_event,
            original_tokens=original_tokens,
            compacted_tokens=compacted_tokens,
            retained_messages=retained_count,
            model_name=model_name,
            error=exc,
        )
        _emit_context_window_stats(
            emit_event,
            agent_id=agent_id,
            original_tokens=original_tokens,
            input_tokens=compacted_tokens,
            retained_messages=retained_count,
            model_name=model_name,
            max_context_tokens=max_context,
            max_output_tokens=max_output,
            hard_budget=hard_budget,
            trigger_budget=trigger_budget,
            compacted=False,
            reason="tool_loop_context_compaction_failed",
        )
        raise ContextCompactionFailedError(
            "工具调用期间的上下文压缩失败。为避免丢失任务状态，本次请求已停止，请稍后重试。",
            reason="tool_loop_context_compaction_failed",
            details={
                "model": model_name,
                "original_tokens": original_tokens,
                "input_tokens": compacted_tokens,
                "max_context_tokens": max_context,
            },
        ) from exc
