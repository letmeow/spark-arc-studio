"""
聊天后台任务管理器。

核心职责：
- 维护活跃聊天任务的注册表、可重放事件日志与运行时快照
- 提供注册 / 查询 / 取消 / 清理操作
- 前端断连后任务继续运行，只有显式 cancel 才会停止
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .chat_persistence import ChatStreamAccumulator


@dataclass
class ChatTaskEntry:
    """单个聊天后台任务的元数据与运行时状态。"""

    task_key: str  # user_id:project_name:agent_id:context_key
    user_id: str
    project_name: str
    agent_id: str
    context_key: str
    stop_event: threading.Event
    status: str  # running | completed | cancelled | error
    started_at: float

    # 完成后的元数据（供 task-status 查询）
    result_message_id: Optional[int] = None
    result_content: str = ''
    result_metadata: Dict[str, Any] = field(default_factory=dict)
    llm_usage: Optional[Dict[str, Any]] = None
    error_message: str = ''

    # 重试次数（0 表示未重试，1-3 表示已重试次数）
    retry_count: int = 0

    # 事件类型标记：send 或 edit
    channel: str = 'direct_reply_stream'

    # 稳定任务 ID 与可重放事件日志
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    user_message_id: Optional[int] = None
    assistant_message_id: Optional[int] = None
    finished_event: threading.Event = field(default_factory=threading.Event)
    cancel_requested: bool = False
    # 终态声明与事件日志写入分离，防止取消接口和后台 finally 重复收尾。
    terminalized: bool = False
    terminal_status: Optional[str] = None
    event_log: List[Dict[str, Any]] = field(default_factory=list)
    next_seq: int = 0
    accumulator: Optional[ChatStreamAccumulator] = None
    log_lock: threading.RLock = field(default_factory=threading.RLock)
    checkpoint_lock: threading.RLock = field(default_factory=threading.RLock)
    last_checkpoint_seq: int = 0
    last_checkpoint_at: float = 0.0
    observer_signals: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.accumulator is None:
            self.accumulator = ChatStreamAccumulator(channel=self.channel, task_id=self.task_id)

    def append_event(
        self,
        event: Any,
        *,
        accumulate: bool = True,
        allow_terminal: bool = False,
    ) -> Dict[str, Any]:
        """Append one NDJSON event to the replay log and update the accumulator."""
        if isinstance(event, dict):
            payload = dict(event)
        elif isinstance(event, str):
            payload = {"event": "assistant_delta", "text": event}
        else:
            payload = {"event": "assistant_delta", "text": str(event)}

        with self.log_lock:
            if self.terminalized and not allow_terminal:
                return {
                    "event": payload.get("event") or "ignored",
                    "task_id": self.task_id,
                    "seq": self.next_seq,
                    "ignored_after_terminal": True,
                }
            self.next_seq += 1
            payload["seq"] = self.next_seq
            payload["task_id"] = self.task_id
            if self.assistant_message_id is not None and "assistant_message_id" not in payload:
                payload["assistant_message_id"] = self.assistant_message_id
            self.event_log.append(payload)
            if accumulate and self.accumulator is not None:
                self.accumulator.append_event(payload, seq=self.next_seq)
            result = dict(payload)
        self.notify_observers()
        return result

    def append_control_event(
        self,
        event: Dict[str, Any],
        *,
        allow_terminal: bool = False,
    ) -> Dict[str, Any]:
        return self.append_event(event, accumulate=False, allow_terminal=allow_terminal)

    def record_llm_usage(self, usage: Dict[str, Any]) -> Dict[str, Any]:
        """累加一次已落库的 LLM 用量，并返回当前任务快照。"""
        with self.log_lock:
            if self.terminalized:
                return dict(self.llm_usage or {})
            current = dict(self.llm_usage or {})
            by_agent = {
                str(key): dict(value)
                for key, value in (current.get("by_agent") or {}).items()
                if isinstance(value, dict)
            }
            agent_name = str(usage.get("agent_name") or "").strip() or "unknown"
            agent_usage = dict(by_agent.get(agent_name) or {})

            prompt_tokens = max(int(usage.get("prompt_tokens") or 0), 0)
            completion_tokens = max(int(usage.get("completion_tokens") or 0), 0)
            total_tokens = max(int(usage.get("total_tokens") or prompt_tokens + completion_tokens), 0)
            cached_tokens = max(int(usage.get("cached_prompt_tokens") or 0), 0)
            cache_miss_raw = usage.get("cache_miss_prompt_tokens")
            cache_stats_available = bool(
                agent_usage.get("cache_stats_available")
                or cached_tokens > 0
                or cache_miss_raw is not None
            )

            for target in (current, agent_usage):
                target["prompt_tokens"] = int(target.get("prompt_tokens") or 0) + prompt_tokens
                target["completion_tokens"] = int(target.get("completion_tokens") or 0) + completion_tokens
                target["total_tokens"] = int(target.get("total_tokens") or 0) + total_tokens
                target["cached_prompt_tokens"] = int(target.get("cached_prompt_tokens") or 0) + cached_tokens
                target["cache_miss_prompt_tokens"] = int(target.get("cache_miss_prompt_tokens") or 0) + max(
                    int(cache_miss_raw or 0),
                    0,
                )
                target["requests"] = int(target.get("requests") or 0) + 1
                target["errors"] = int(target.get("errors") or 0) + (0 if usage.get("success", True) else 1)

            current_cache_available = bool(
                current.get("cache_stats_available")
                or cached_tokens > 0
                or cache_miss_raw is not None
            )
            agent_usage["cache_stats_available"] = cache_stats_available
            agent_usage["cache_hit_rate"] = (
                agent_usage["cached_prompt_tokens"] / agent_usage["prompt_tokens"]
                if cache_stats_available and agent_usage["prompt_tokens"] > 0
                else None
            )
            by_agent[agent_name] = agent_usage
            current["cache_stats_available"] = current_cache_available
            current["cache_hit_rate"] = (
                current["cached_prompt_tokens"] / current["prompt_tokens"]
                if current_cache_available and current["prompt_tokens"] > 0
                else None
            )
            current["by_agent"] = by_agent
            current["source"] = "usage_callback"
            self.llm_usage = current
            return dict(current)

    def get_events_after(self, after_seq: int = 0) -> List[Dict[str, Any]]:
        cursor = int(after_seq or 0)
        with self.log_lock:
            return [dict(evt) for evt in self.event_log if int(evt.get("seq") or 0) > cursor]

    def build_snapshot(self) -> Dict[str, Any]:
        with self.log_lock:
            seq = self.next_seq
            if self.accumulator is None:
                self.accumulator = ChatStreamAccumulator(channel=self.channel, task_id=self.task_id)
            snapshot = self.accumulator.build_snapshot(
                status=self.status,
                assistant_message_id=self.assistant_message_id,
                seq=seq,
                error_message=self.error_message,
            )
            if self.llm_usage:
                snapshot["metadata"]["llm_usage"] = dict(self.llm_usage)
                snapshot["llm_usage"] = dict(self.llm_usage)
            if self.user_message_id is not None:
                snapshot["user_message_id"] = self.user_message_id
            return snapshot

    def build_metadata(self, *, stream_status: str | None = None) -> Dict[str, Any]:
        with self.log_lock:
            if self.accumulator is None:
                self.accumulator = ChatStreamAccumulator(channel=self.channel, task_id=self.task_id)
            resolved_status = stream_status or self.status
            metadata = self.accumulator.build_metadata(
                stream_status=resolved_status,
                assistant_message_id=self.assistant_message_id,
            )
            if self.llm_usage:
                metadata["llm_usage"] = dict(self.llm_usage)
            if resolved_status == 'error':
                error_message = str(self.error_message or '').strip()
                if error_message:
                    metadata['error'] = error_message
                metadata['retry_count'] = max(int(self.retry_count or 0), 0)
            return metadata

    def reset_for_retry(self) -> None:
        with self.log_lock:
            if self.terminalized:
                return
            if self.accumulator is None:
                self.accumulator = ChatStreamAccumulator(channel=self.channel, task_id=self.task_id)
            self.accumulator.reset_for_retry()

    def is_terminalized(self) -> bool:
        """返回任务是否已经被某个线程抢先声明终态。"""
        with self.log_lock:
            return self.terminalized

    def claim_terminal_status(self, status: str) -> bool:
        """原子声明一次终态；成功后迟到的后台事件将被丢弃。"""
        with self.log_lock:
            if self.terminalized or self.finished_event.is_set():
                return False
            self.terminalized = True
            self.terminal_status = str(status)
            self.status = str(status)
            return True

    def subscribe(self, loop: asyncio.AbstractEventLoop, signal: asyncio.Event) -> None:
        with self.log_lock:
            self.observer_signals.add((loop, signal))

    def unsubscribe(self, loop: asyncio.AbstractEventLoop, signal: asyncio.Event) -> None:
        with self.log_lock:
            self.observer_signals.discard((loop, signal))

    def notify_observers(self) -> None:
        """从后台线程安全唤醒所有异步观察者。"""
        with self.log_lock:
            observers = list(self.observer_signals)
        for loop, signal in observers:
            try:
                loop.call_soon_threadsafe(signal.set)
            except RuntimeError:
                self.unsubscribe(loop, signal)


# ─────────────────────────────────────────────────────────────────────────────
# 全局注册表
# ─────────────────────────────────────────────────────────────────────────────

_active_chat_tasks: Dict[str, ChatTaskEntry] = {}
_registry_lock = threading.Lock()


def _make_task_key(user_id: str, project_name: str, agent_id: str, context_key: str) -> str:
    return f"{user_id}:{project_name}:{agent_id}:{context_key}"


def register_task(entry: ChatTaskEntry) -> None:
    """注册新的聊天后台任务，禁止覆盖尚未真正退出的旧代任务。"""
    with _registry_lock:
        existing = _active_chat_tasks.get(entry.task_key)
        if existing and not existing.finished_event.is_set():
            raise ValueError(f'聊天任务 {entry.task_key} 已在运行中')
        _active_chat_tasks[entry.task_key] = entry


def get_task(task_key: str) -> Optional[ChatTaskEntry]:
    """查询指定 key 的任务。"""
    with _registry_lock:
        return _active_chat_tasks.get(task_key)


def get_task_by_parts(user_id: str, project_name: str, agent_id: str, context_key: str) -> Optional[ChatTaskEntry]:
    """按组成部分查询任务。"""
    return get_task(_make_task_key(user_id, project_name, agent_id, context_key))


def cancel_task(task_key: str) -> bool:
    """请求取消指定任务，并由调用方在必要时强制收口终态。"""
    with _registry_lock:
        entry = _active_chat_tasks.get(task_key)
    if not entry:
        return False
    with entry.log_lock:
        if entry.finished_event.is_set() or entry.terminalized or entry.status != 'running':
            return False
        entry.stop_event.set()
        entry.cancel_requested = True
    entry.append_control_event({"event": "task_cancel_requested", "status": "cancelled"})
    return True


def update_task_status(
    task_key: str,
    status: str,
    *,
    expected_task_id: str | None = None,
    allow_terminal: bool = False,
    **fields: Any,
) -> None:
    """更新任务状态和字段。"""
    with _registry_lock:
        entry = _active_chat_tasks.get(task_key)
    if entry and (expected_task_id is None or entry.task_id == expected_task_id):
        with entry.log_lock:
            if entry.terminalized and not allow_terminal:
                return
            entry.status = status
            for k, v in fields.items():
                if hasattr(entry, k):
                    setattr(entry, k, v)
        entry.notify_observers()


def cleanup_task(task_key: str, delay: float = 60.0, *, task_id: str | None = None) -> None:
    """延迟清理已结束的任务，并校验任务代次避免误删新任务。"""
    if task_id is None:
        current = get_task(task_key)
        task_id = current.task_id if current else None

    def _do_cleanup():
        with _registry_lock:
            current = _active_chat_tasks.get(task_key)
            if current and (task_id is None or current.task_id == task_id):
                _active_chat_tasks.pop(task_key, None)
    threading.Timer(delay, _do_cleanup).start()


def wait_for_task_exit(task_key: str, timeout: float = 10.0) -> bool:
    """等待任务后台线程退出，返回是否已退出。"""
    entry = get_task(task_key)
    if not entry:
        return True
    if entry.finished_event.is_set():
        return True
    return entry.finished_event.wait(max(float(timeout or 0), 0.0))


def purge_project_tasks(user_id: str, project_name: str) -> int:
    """移除指定用户+项目在内存任务注册表中的全部残留条目。

    项目删除后，目录与 DB 聊天都会被清除；若同名项目被重建，残留的内存任务
    （running 会被重连流，completed 会触发刷新历史）会被误判为新项目的任务。
    只有 running 任务需要显式请求停止（写 stop_event），已终态任务直接移除，
    避免等待其延迟 cleanup 计时器。
    """
    removed = 0
    with _registry_lock:
        keys = [
            key
            for key, entry in _active_chat_tasks.items()
            if entry.user_id == user_id and entry.project_name == project_name
        ]
        for key in keys:
            entry = _active_chat_tasks.get(key)
            if entry is not None and entry.status == 'running' and not entry.finished_event.is_set():
                entry.stop_event.set()
                entry.cancel_requested = True
            _active_chat_tasks.pop(key, None)
            removed += 1
    return removed


def list_recent_tasks(user_id: str, project_name: str) -> list[ChatTaskEntry]:
    """列出指定用户+项目下所有未清理的任务（running + completed/cancelled/error 尚未被 cleanup）。
    供前端恢复场景使用：running → 重连流；completed → 刷新历史获取结果。
    """
    with _registry_lock:
        return [
            entry for entry in _active_chat_tasks.values()
            if entry.user_id == user_id and entry.project_name == project_name
        ]


def build_task_status_payload(entry: ChatTaskEntry) -> Dict[str, Any]:
    """构建 task-status API 的返回载荷。"""
    payload: Dict[str, Any] = {
        'hasTask': True,
        'status': entry.terminal_status if entry.terminalized else entry.status,
        'agentId': entry.agent_id,
        'contextKey': entry.context_key,
        'channel': entry.channel,
        'startedAt': entry.started_at,
        'error': entry.error_message,
        'retryCount': entry.retry_count,
        'taskId': entry.task_id,
        'assistantMessageId': entry.assistant_message_id,
        'userMessageId': entry.user_message_id,
        'lastSeq': entry.next_seq,
    }
    if entry.result_message_id is not None:
        payload['resultMessageId'] = entry.result_message_id
    if entry.result_content:
        payload['resultContent'] = entry.result_content
    if entry.llm_usage:
        payload['llmUsage'] = dict(entry.llm_usage)
    context_window_stats = None
    if isinstance(entry.result_metadata, dict):
        result_stats = entry.result_metadata.get('context_window_stats') or entry.result_metadata.get('contextWindowStats')
        if isinstance(result_stats, dict):
            context_window_stats = dict(result_stats)
    if context_window_stats is None and entry.accumulator is not None and isinstance(entry.accumulator.context_window_stats, dict):
        context_window_stats = dict(entry.accumulator.context_window_stats)
    if context_window_stats:
        payload['contextWindowStats'] = context_window_stats
    return payload
