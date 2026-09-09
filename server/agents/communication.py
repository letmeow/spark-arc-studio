"""
Agent 通讯基础设施

本文件定义的是 Spark 项目里的“通讯层底座”，核心角色是 `SparkBaseAgent`。

`SparkBaseAgent` 的职责：
- 提供 Agent 身份、注册信息、用户作用域
- 提供信标 / 号角 / 旗帜 三件套运行态
- 提供消息总线绑定、同步消息收发
- 提供聊天模式、工具调用模式的公共实现

它解决的是“Agent 如何作为系统内的一个协作节点存在”。

它不解决的是“不同业务入口如何收敛到同一套执行逻辑”。
这部分由 `agent_utils.py` 中的 `SparkAgentExecutor` 负责。

因此在当前架构里，两层职责明确分开：
- `SparkBaseAgent`：通讯层 / Agent 行为底座
- `SparkAgentExecutor`：执行层 / 统一业务协议底座

典型业务 Agent（如 Muse / Lorebook / Showrunner / ScriptWriter）
可以同时继承这两个类：
- 既拥有 Agent 通讯与聊天能力
- 又拥有统一的 `build_context() -> execute() -> write_result()` 执行链

实现同步通讯总线（Bus）以及 Agent 基础基类。
"""
import contextvars
import dataclasses
import json
import os
import queue
import re
import threading
import time
import uuid
from typing import Dict, Any, Callable, Optional, List
from .registry import get_agent_registry, _resolve_i18n_field
from .language_policy import prepend_prompt_language_policy
from .attachment.chunk_history import (
    ATTACHMENT_CHUNK_COLLAPSED_PLACEHOLDER,
    ATTACHMENT_CHUNK_TOOL_NAME,
    collapse_attachment_chunk_history,
)
from .tools.stream_events import (
    build_tool_stream_event,
    get_tool_result_failure_message,
    get_tool_ui_binding,
    is_tool_result_failure,
    normalize_tool_name,
)
from .tools.pipeline import (
    PIPELINE_COMPLETION_MARKER,
    PIPELINE_COMPLETION_TOOL_NAME,
)
from llm.agen_matchbox.tool_protocol import (
    build_tool_history_message,
    build_tool_result_messages,
    dedupe_tool_specs,
    extract_tool_call_id,
    normalize_tool_args,
    prepare_tool_specs_for_execution,
)


# ── ToolEventSink: 嵌套工具事件广播 ──────────────────────────────
# 当 chat_stream() 执行 delegate_task 这类会触发嵌套 agent 工具调用的工具时，
# 嵌套 agent 的 _execute_tool_calls 会把 started/finished 事件推送到这个 sink。
# 外层 chat_stream() 在工具执行完毕后从 sink 读取这些事件并 yield 给前端。

_tool_event_sink: contextvars.ContextVar[Optional[queue.Queue]] = contextvars.ContextVar(
    "_tool_event_sink", default=None
)


def get_tool_event_sink() -> Optional[queue.Queue]:
    return _tool_event_sink.get(None)


def set_tool_event_sink(q: Optional[queue.Queue]) -> contextvars.Token:
    return _tool_event_sink.set(q)


def reset_tool_event_sink(token: contextvars.Token) -> None:
    """按 ContextVar token 恢复外层工具事件接收器。"""
    _tool_event_sink.reset(token)


def is_stop_event_set(stop_event: Any = None) -> bool:
    """Duck-typed cancellation check for chat/director stream stop events."""
    if stop_event is None:
        return False
    is_set = getattr(stop_event, "is_set", None)
    if not callable(is_set):
        return False
    try:
        return bool(is_set())
    except Exception:
        return False


def _current_longread_ledger() -> Any:
    """返回当前任务的线索账本（内存态）；任务外返回 None，折叠退化不断链。"""
    try:
        from agents.longread_store import load_task_ledger
        from core.request_context import current_user_id, get_current_project_name

        return load_task_ledger(
            str(current_user_id.get() or ""),
            str(get_current_project_name() or ""),
        )
    except Exception:
        return None


def is_pipeline_tool_result_failure(tool_name: str, result: Any) -> bool:
    """对落盘回执做保守失败识别，避免把文本型失败当成流水线完成。"""
    if is_tool_result_failure(tool_name, result):
        return True
    from agents.tools.registry import PIPELINE_PERSIST_TOOL_NAMES

    if tool_name not in PIPELINE_PERSIST_TOOL_NAMES:
        return False
    first_line = str(result or "").strip().splitlines()[:1]
    return bool(first_line) and any(
        marker in first_line[0]
        for marker in ("失败", "未找到", "未知工具")
    )


def _format_pipeline_receipt_result(result: Any) -> str:
    """从真实工具返回中提取短回执，不把正文或完整工具参数带回导演。"""
    text = str(result or "").strip()
    if not text:
        return "执行成功"
    if isinstance(result, dict):
        payload = result
    else:
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
    if isinstance(payload, dict):
        parts = []
        message = str(payload.get("message") or payload.get("status") or "执行成功").strip()
        if message:
            parts.append(message)
        path = str(payload.get("path") or "").strip()
        if path and path not in message:
            parts.append(f"路径：{path}")
        written_chars = payload.get("written_chars")
        if isinstance(written_chars, int):
            parts.append(f"正文 {written_chars} 字")
        return "；".join(parts)[:300] or "执行成功"
    return text.splitlines()[0][:300]


def build_pipeline_completion_receipt(
    agent_id: str,
    write_receipts: List[tuple[str, Any]],
) -> str:
    """根据已成功执行的落盘工具生成给导演的确定性短回执。"""
    lines = [f"[{agent_id}] 流水线步骤已完成，项目真相源已更新。"]
    seen: set[tuple[str, str]] = set()
    for tool_name, result in write_receipts:
        summary = _format_pipeline_receipt_result(result)
        key = (tool_name, summary)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {tool_name}：{summary}")
    return "\n".join(lines)


def resolve_pipeline_completion(
    agent_id: str,
    tool_results: List[tuple[str, str, Any]],
    write_receipts: List[tuple[str, Any]],
) -> Optional[str]:
    """累计真实落盘回执；仅在最终控制工具确认且本批无失败时返回完成回执。"""
    from agents.tools.registry import PIPELINE_PERSIST_TOOL_NAMES

    current_batch_failed = False
    for _, tool_name, tool_result in tool_results:
        if tool_name == PIPELINE_COMPLETION_TOOL_NAME:
            continue
        failed = is_pipeline_tool_result_failure(tool_name, tool_result)
        if failed:
            current_batch_failed = True
        elif tool_name in PIPELINE_PERSIST_TOOL_NAMES:
            write_receipts.append((tool_name, tool_result))

    completion_is_last = bool(tool_results) and (
        tool_results[-1][1] == PIPELINE_COMPLETION_TOOL_NAME
    )
    completion_acknowledged = completion_is_last and str(
        tool_results[-1][2] or ""
    ).strip() == PIPELINE_COMPLETION_MARKER
    if completion_acknowledged and not current_batch_failed and write_receipts:
        return build_pipeline_completion_receipt(agent_id, write_receipts)
    return None


from llm.agen_matchbox.reasoning_compat import (
    extract_reasoning_text_from_message,
    extract_reasoning_text_from_plain_text,
    extract_text_content_from_message,
    extract_visible_text_from_plain_text,
    MessageEventStreamReasoningAdapter,
)


class ModelStreamRetryExhaustedError(RuntimeError):
    """当前模型轮次已完成内部续跑尝试，不允许再从用户消息重放整项任务。"""


class ModelStreamIdleTimeoutError(TimeoutError):
    """模型流在指定时间内没有收到推理或正文内容。"""

    def __init__(self, timeout: float) -> None:
        self.timeout = float(timeout)
        super().__init__(f"模型流连续 {self.timeout:g} 秒未收到推理或正文内容")


MODEL_STREAM_IDLE_TIMEOUT_SECONDS = 120.0
_MODEL_STREAM_POLL_INTERVAL_SECONDS = 0.05


@dataclasses.dataclass(frozen=True)
class ModelTurnRetryNotice:
    """通知调用方丢弃当前失败轮次的临时聚合状态。"""

    attempt: int
    max_attempts: int
    error: str


def _model_chunk_has_content(chunk: Any) -> bool:
    """判断模型增量是否包含会刷新空闲计时的推理或正文文本。"""
    try:
        reasoning = extract_reasoning_text_from_message(chunk)
    except Exception:
        reasoning = ""
    try:
        content = extract_text_content_from_message(chunk)
    except Exception:
        content = ""

    if str(reasoning or "").strip() or str(content or "").strip():
        return True
    if isinstance(chunk, str):
        try:
            return bool(
                extract_reasoning_text_from_plain_text(chunk).strip()
                or extract_visible_text_from_plain_text(chunk).strip()
            )
        except Exception:
            return bool(chunk.strip())
    return False


def _model_chunk_has_activity(chunk: Any) -> bool:
    """判断模型增量是否已经离开前端的“思考中”占位阶段。"""
    # 用户约定只以 reasoning/content 文本作为首个有效活动。工具参数碎片可能
    # 在上游半途卡死，不能借此永久解除 120 秒截止时间。
    return _model_chunk_has_content(chunk)


def _stream_model_turn_with_idle_watchdog(
    llm: Any,
    messages: List[Any],
    *,
    stop_event: Any = None,
    idle_timeout: float | None = MODEL_STREAM_IDLE_TIMEOUT_SECONDS,
):
    """在守护线程里读取同步模型流，使取消和内容空闲超时不被上游阻塞。"""
    events: queue.Queue[tuple[str, Any]] = queue.Queue()
    accept_results = threading.Event()
    accept_results.set()

    def _consume_upstream() -> None:
        def _publish(event_type: str, payload: Any) -> None:
            if accept_results.is_set():
                events.put((event_type, (time.monotonic(), payload)))

        try:
            iterator = iter(llm.stream(messages))
            while accept_results.is_set() and not is_stop_event_set(stop_event):
                try:
                    item = next(iterator)
                except StopIteration:
                    _publish("done", None)
                    return
                except Exception as exc:
                    _publish("error", exc)
                    return
                _publish("item", item)
        except Exception as exc:
            _publish("error", exc)

    worker_context = contextvars.copy_context()
    worker = threading.Thread(
        target=worker_context.run,
        args=(_consume_upstream,),
        daemon=True,
        name="model_stream_watchdog",
    )
    worker.start()

    timeout_value = None if idle_timeout is None else max(float(idle_timeout), 0.0)
    deadline = (
        None
        if timeout_value is None
        else time.monotonic() + timeout_value
    )
    first_activity_received = False
    try:
        while True:
            if is_stop_event_set(stop_event):
                return

            wait_timeout = _MODEL_STREAM_POLL_INTERVAL_SECONDS
            if deadline is not None:
                remaining = deadline - time.monotonic()
                wait_timeout = min(wait_timeout, max(remaining, 0.0))

            try:
                event_type, payload = events.get(timeout=wait_timeout)
            except queue.Empty:
                if deadline is not None and time.monotonic() >= deadline:
                    raise ModelStreamIdleTimeoutError(timeout_value or 0.0)
                continue

            event_at, payload = payload
            if deadline is not None and event_at > deadline:
                raise ModelStreamIdleTimeoutError(timeout_value or 0.0)

            if event_type == "done":
                return
            if event_type == "error":
                raise payload
            if event_type != "item":
                continue
            if is_stop_event_set(stop_event):
                return
            if not first_activity_received and _model_chunk_has_activity(payload):
                first_activity_received = True
                # 120 秒只保护前端“思考中”占位阶段。首个有效事件到达后，
                # 后续正文间隔、工具参数生成和工具执行时长均不由此 watchdog 限制。
                deadline = None
            yield payload
    finally:
        # 超时或取消后不再接收这个轮次的迟到结果；上游 SDK 若仍阻塞，
        # 守护线程会随进程退出，不得把结果转入后续聊天轮次。
        accept_results.clear()


def stream_model_turn_with_retry(
    llm: Any,
    messages: List[Any],
    *,
    stop_event: Any = None,
    max_attempts: int = 3,
    retry_delay: float = 1.0,
    idle_timeout: float | None = MODEL_STREAM_IDLE_TIMEOUT_SECONDS,
):
    """只重试当前模型轮次，并按内容空闲时间保护同步上游流。"""
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            yield from _stream_model_turn_with_idle_watchdog(
                llm,
                messages,
                stop_event=stop_event,
                idle_timeout=idle_timeout,
            )
            return
        except ModelStreamIdleTimeoutError as exc:
            # 首次有效活动超时代表本次上游请求已经失去响应。继续自动重试会把
            # 用户指定的 120 秒请求上限放大为多轮等待，因此直接结束当前轮次。
            last_error = exc
            break
        except Exception as exc:
            if is_stop_event_set(stop_event):
                return
            last_error = exc
            if attempt >= max_attempts:
                break
            yield ModelTurnRetryNotice(attempt=attempt, max_attempts=max_attempts, error=str(exc))
            if retry_delay > 0:
                waiter = getattr(stop_event, "wait", None)
                if callable(waiter):
                    if waiter(retry_delay):
                        return
                else:
                    threading.Event().wait(retry_delay)
    raise ModelStreamRetryExhaustedError(str(last_error or "模型流异常中断")) from last_error

@dataclasses.dataclass
class AgentMessage:
    """
    Agent 之间传递的消息数据结构。
    """
    sender: str        # 发送方 Agent 的唯一标识符
    receiver: str      # 接收方 Agent 的唯一标识符
    intent: str        # 消息意图（例如：'query', 'task_assign', 'status_update'）
    content: Any       # 消息主体内容，可以是字符串、字典或其他任意对象
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict) # 附加的元数据


HANDOFF_DELIVERY_DIRECT_TO_USER = "direct_to_user"
HANDOFF_DELIVERY_RETURN_TO_DIRECTOR = "return_to_director"
HANDOFF_COMPLETION_REPORT_TO_USER = "report_to_user"
HANDOFF_COMPLETION_RETURN_TO_DIRECTOR = "return_to_director"
HANDOFF_COMPLETION_SILENT_CONTINUE = "silent_continue"
HANDOFF_CONFIRMATION_PENDING = "needs_confirmation"
HANDOFF_CONFIRMATION_CONFIRMED = "already_confirmed"
HANDOFF_CONFIRMATION_NOT_REQUIRED = "not_required"
VALID_HANDOFF_DELIVERY_MODES = {
    HANDOFF_DELIVERY_DIRECT_TO_USER,
    HANDOFF_DELIVERY_RETURN_TO_DIRECTOR,
}
VALID_HANDOFF_COMPLETION_MODES = {
    HANDOFF_COMPLETION_REPORT_TO_USER,
    HANDOFF_COMPLETION_RETURN_TO_DIRECTOR,
    HANDOFF_COMPLETION_SILENT_CONTINUE,
}
VALID_HANDOFF_CONFIRMATION_STATES = {
    HANDOFF_CONFIRMATION_PENDING,
    HANDOFF_CONFIRMATION_CONFIRMED,
    HANDOFF_CONFIRMATION_NOT_REQUIRED,
}


def normalize_handoff_payload(
    payload: Optional[Dict[str, Any]],
    *,
    sender_id: str = "agent_director",
) -> Dict[str, Any]:
    raw = dict(payload or {})

    target_agent = str(raw.get("target_agent") or "").strip()
    task_description = str(raw.get("task_description") or raw.get("content") or "").strip()
    delivery_mode = str(raw.get("delivery_mode") or HANDOFF_DELIVERY_DIRECT_TO_USER).strip() or HANDOFF_DELIVERY_DIRECT_TO_USER
    if delivery_mode not in VALID_HANDOFF_DELIVERY_MODES:
        delivery_mode = HANDOFF_DELIVERY_DIRECT_TO_USER

    completion_mode = str(raw.get("completion_mode") or "").strip()
    if completion_mode not in VALID_HANDOFF_COMPLETION_MODES:
        completion_mode = (
            HANDOFF_COMPLETION_RETURN_TO_DIRECTOR
            if delivery_mode == HANDOFF_DELIVERY_RETURN_TO_DIRECTOR
            else HANDOFF_COMPLETION_REPORT_TO_USER
        )

    requires_review = bool(raw.get("requires_review"))
    if requires_review:
        delivery_mode = HANDOFF_DELIVERY_RETURN_TO_DIRECTOR
        completion_mode = HANDOFF_COMPLETION_RETURN_TO_DIRECTOR

    if completion_mode in {HANDOFF_COMPLETION_RETURN_TO_DIRECTOR, HANDOFF_COMPLETION_SILENT_CONTINUE}:
        delivery_mode = HANDOFF_DELIVERY_RETURN_TO_DIRECTOR
    elif delivery_mode == HANDOFF_DELIVERY_RETURN_TO_DIRECTOR:
        completion_mode = HANDOFF_COMPLETION_RETURN_TO_DIRECTOR

    delegated_by = str(raw.get("delegated_by") or sender_id or "agent_director").strip() or "agent_director"
    if delegated_by == "agent_director":
        user_confirmation_state = HANDOFF_CONFIRMATION_NOT_REQUIRED
    else:
        user_confirmation_state = str(
            raw.get("user_confirmation_state")
            or (HANDOFF_CONFIRMATION_CONFIRMED if raw.get("skip_tool_confirmation") else "")
            or HANDOFF_CONFIRMATION_PENDING
        ).strip() or HANDOFF_CONFIRMATION_PENDING
        if user_confirmation_state not in VALID_HANDOFF_CONFIRMATION_STATES:
            user_confirmation_state = HANDOFF_CONFIRMATION_PENDING

    return_to = str(raw.get("return_to") or sender_id or "agent_director").strip() or (sender_id or "agent_director")
    grant_baton_to = str(raw.get("grant_baton_to") or target_agent).strip() or target_agent
    task_id = str(raw.get("task_id") or uuid.uuid4().hex).strip() or uuid.uuid4().hex
    export_format = str(raw.get("export_format") or "").strip().lower()
    if export_format not in {"arc", "novel"}:
        export_format = ""

    scene_characters = raw.get("scene_characters") or raw.get("characters") or []
    if isinstance(scene_characters, str):
        scene_characters = [
            item.strip()
            for item in re.split(r"[,，、\n]", scene_characters)
            if item.strip()
        ]
    elif isinstance(scene_characters, list):
        scene_characters = [str(item).strip() for item in scene_characters if str(item).strip()]
    else:
        scene_characters = []

    return {
        "task_id": task_id,
        "target_agent": target_agent,
        "task_description": task_description,
        "delivery_mode": delivery_mode,
        "completion_mode": completion_mode,
        "requires_review": requires_review,
        "user_confirmation_state": user_confirmation_state,
        "skip_tool_confirmation": user_confirmation_state in {HANDOFF_CONFIRMATION_CONFIRMED, HANDOFF_CONFIRMATION_NOT_REQUIRED},
        "return_to": return_to,
        "grant_baton_to": grant_baton_to,
        "delegated_by": delegated_by,
        "project_name": str(raw.get("project_name") or "").strip(),
        "export_format": export_format,
        "chapter_name": str(raw.get("chapter_name") or "").strip(),
        "scene_name": str(raw.get("scene_name") or "").strip(),
        "scene_file_path": str(raw.get("scene_file_path") or raw.get("file_path") or "").strip(),
        "scene_guidance": str(raw.get("scene_guidance") or "").strip(),
        "scene_characters": scene_characters,
        "tracker_item_id": str(raw.get("tracker_item_id") or "").strip(),
    }


def transfer_baton(
    context: "CommunicationContext",
    user_id: str,
    *,
    to_agent_id: str,
    from_agent_id: Optional[str] = None,
    auto_open_beacon: bool = True,
) -> Dict[str, Any]:
    namespace = context._user_namespaces.get(str(user_id)) or {}
    target = namespace.get(to_agent_id)
    if not target:
        return {
            "status": "error",
            "message": f"在用户 '{user_id}' 的空间内未找到可接棒 Agent: '{to_agent_id}'",
        }

    if from_agent_id and from_agent_id != to_agent_id:
        sender = namespace.get(from_agent_id)
        if sender:
            sender.return_baton()

    if auto_open_beacon:
        target.open_beacon()
    target.take_baton()
    return {
        "status": "ok",
        "baton_holder": target.agent_id,
        "isBeaconOpen": target.signals.is_beacon_open,
        "hasHorn": target.signals.has_horn,
        "hasBaton": target.signals.has_baton,
    }


@dataclasses.dataclass
class AgentSignalState:
    """
    Agent 三件套运行态。

    - 信标（is_beacon_open）：该 Agent 是否对外可见、可被触达、可接收外部消息。
    - 号角（has_horn）：该 Agent 是否具备主动向其他 Agent 发话、发起协作的资格。
    - 旗帜（has_baton）：当前这条任务链的接力棒是否在该 Agent 手里。
    """
    is_beacon_open: bool = False
    has_horn: bool = False
    has_baton: bool = False

class SparkBaseAgent:
    """
    所有参与通讯系统的 Agent 基类。
    封装了身份管理、信标/号角/旗帜控制以及消息收发的核心逻辑。
    """
    def __init__(self, agent_id: str, user_id: str, project_name: str = ""):
        self.agent_id = agent_id  # Agent 的功能 ID (如 agent_showrunner)
        self.user_id = str(user_id)    # 所属用户的 ID
        self.project_name = str(project_name or "")
        self.context: Optional['CommunicationContext'] = None # 绑定的通讯总线上下文
        self.signals = AgentSignalState() # 初始化信标 / 号角 / 旗帜 三件套
        
        # 从注册中心加载元数据
        self.name = agent_id
        self.intro = ""
        self._load_registry_info()
        
        # 延迟加载 LLM，避免基类初始化时产生开销
        self._llm = None
        self._pending_context_checkpoint: Optional[Dict[str, Any]] = None

    def _set_context_checkpoint_candidate(self, checkpoint: Optional[Dict[str, Any]]) -> None:
        self._pending_context_checkpoint = dict(checkpoint) if isinstance(checkpoint, dict) else None

    def consume_context_checkpoint_candidate(self) -> Optional[Dict[str, Any]]:
        """取出本次成功请求产生的 checkpoint 候选，并清空单次状态。"""
        checkpoint = self._pending_context_checkpoint
        self._pending_context_checkpoint = None
        return checkpoint

    @property
    def llm(self):
        if self._llm is None:
            from llm.agen_matchbox import matchbox
            self._llm = matchbox().get_user_llm(
                self.user_id,
                agent_name=self.agent_id,
            )
        return self._llm

    @llm.setter
    def llm(self, value):
        self._llm = value

    def _load_registry_info(self):
        """
        从全局 Agent 注册中心加载 Agent 的名称 and 简介。
        """
        from .language_policy import get_current_locale
        locale = get_current_locale()
        registry = get_agent_registry(locale)
        for info in registry:
            if info.get('key') == self.agent_id:
                self.name = info.get('name', self.agent_id)
                self.intro = info.get('description', "")
                break

    def bind_context(self, context: 'CommunicationContext'):
        """
        将当前 Agent 绑定到一个通讯上下文（通讯总线）中。
        """
        self.context = context
        context.register(self)

    def open_beacon(self):
        """
        开启信标：允许该 Agent 被其他 Agent 看见并接收外部消息。
        """
        self.signals.is_beacon_open = True

    def close_beacon(self):
        """
        关闭信标：该 Agent 从协作视野中隐身，不再接收外部消息。
        """
        self.signals.is_beacon_open = False

    def raise_horn(self):
        """
        吹响号角：授予该 Agent 主动向其他 Agent 发话、发起跨 Agent 协作的资格。
        """
        self.signals.has_horn = True
        self.open_beacon()

    def lower_horn(self):
        """
        放下号角：取消该 Agent 主动向其他 Agent 发话的资格。
        """
        self.signals.has_horn = False

    def take_baton(self):
        """
        接过旗帜：表示当前这条任务链的推进责任来到该 Agent 手里。
        旗帜在 SparkArc 中代表“接力棒”，不是长期权限。
        """
        self.signals.has_baton = True
        self.open_beacon()

    def return_baton(self):
        """
        交还旗帜：表示当前这条任务链的推进责任已不在该 Agent 手里。
        """
        self.signals.has_baton = False

    def send_message(self, target_id: str, intent: str, content: Any, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        向同一个用户下的另一个 Agent 发送同步消息。
        :param target_id: 目标 Agent 的功能 ID。
        :param intent: 消息意图。
        :param content: 消息内容。
        :param metadata: 附加元数据。
        :return: 目标 Agent 处理后的响应字典。
        """
        if not self.context:
            raise RuntimeError(f"Agent {self.agent_id} 尚未绑定到 CommunicationContext，无法发送消息")
        
        # 检查号角（主动通信权）
        if not self.signals.has_horn:
             return {"status": "rejected", "message": f"Agent {self.agent_id} 未持有号角（无主动通信权），无法发送消息"}

        # 自动注入发送者的身份信息，便于接收方识别
        msg_metadata = metadata or {}
        if "_sender" not in msg_metadata:
            msg_metadata["_sender"] = {
                "id": self.agent_id,
                "user_id": self.user_id,
                "name": self.name,
                "intro": self.intro
            }
        
        # 封装成通讯载荷
        payload = {
            "intent": intent,
            "content": content,
            "metadata": msg_metadata
        }
        
        # 通过上下文总线进行分发（强制限定在当前用户的作用域内）
        return self.context.dispatch(self.user_id, self.agent_id, target_id, payload)

    def get_available_agents(self) -> List[Dict[str, Any]]:
        """
        获取当前通讯上下文中，属于当前用户的可用 Agent 列表。
        """
        if not self.context:
            return []
        return self.context.list_available_agents(user_id=self.user_id)

    def receive_message(self, sender_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        接收消息的入口方法。负责前置的安全检查（信标状态）。
        """
        # 1. 检查信标是否开启
        if not self.signals.is_beacon_open:
             return {"status": "rejected", "message": f"Agent {self.agent_id} 的信标已关闭，拒绝接收消息"}
        
        # 2. 校验通过，进入业务逻辑处理
        return self.on_message(sender_id, payload)

    def on_message(self, sender_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        业务逻辑回调方法。子类应重写此方法以实现具体的响应逻辑。
        """
        return {
            "status": "received",
            "agent": self.agent_id,
            "echo_intent": payload.get('intent'),
            "message": "基础 Agent 已收到消息"
        }

    def _build_tool_system_prompt(
        self,
        base_prompt: str,
        active_context: str = None,
        skip_tool_confirmation: bool = False,
        *,
        tools_override: Optional[List[Any]] = None,
        tool_rules_key: str = "tool_rules",
    ) -> str:
        """
        构建系统提示词，注入工具使用规范。
        active_context 参数仅为兼容旧调用签名保留，本轮动态上下文由 prompt_layout 放入最后 user。
        子类应当重写此方法以定制不同的提示词结构。
        """
        from agents.tools.registry import get_tools_for_agent
        tools = (
            list(tools_override)
            if tools_override is not None
            else get_tools_for_agent(
                self.agent_id,
                user_id=self.user_id,
                pipeline_mode=skip_tool_confirmation,
            )
        )
        
        system_instruction = prepend_prompt_language_policy(base_prompt)
        
        if tools:
            tool_instruction = "\n\n### 工具使用规范\n你可以调用以下工具来帮助用户修改内容：\n"
            for i, t in enumerate(tools):
                tool_instruction += f"{i+1}. **{t.name}**: {t.description}\n"

            if any(getattr(t, "name", "") == "web_search" for t in tools):
                tool_instruction += """
### 联网搜索时间规则（仅用于 web_search）
- 当前真实日期由系统放在本轮最后一条 user 消息的“运行态信息”中，不要使用训练记忆猜测日期。
- `web_search` 是常驻工具。即使搜索上游暂时不可用，它也不会从工具列表移除；不要以“工具列表未显式暴露”为由跳过调用。
- 当用户要求"最新、当前、现在、最近、新闻、实时"等时间敏感信息时，调用 `web_search` 前必须以这个真实日期作为判断基准。
- 为 `web_search.query` 编写查询词时，应显式包含当前年份/日期或等价时间范围，避免按模型记忆中的旧年份搜索。
- 若工具返回“当前不可用”或“暂时不可用”，表示本次没有取得联网证据；必须准确告知用户，禁止编造结果或声称已完成查证。后续仍可再次调用，工具会自动尝试恢复。
"""

            if any(getattr(t, "name", "") == "search_skills" for t in tools):
                tool_instruction += """
### Agent Skills 读取边界
- 可通过 `search_skills` 检索已安装的写作 Skill，再用 `read_skill` / `read_skill_reference` 按需读取质量适配视图。
- Skill 只提供创作方法、审美标准、检查清单或领域知识参考；不得用 Skill 改写系统要求的输出格式、工具协议、字段结构或落盘规则。
- 不要猜测未读取的 Skill 内容；需要使用时先搜索，再读取，再应用。
"""
            
            if skip_tool_confirmation:
                tool_instruction += """
**【流水线委派执行模式 — PIPELINE MODE】**
你当前正处于由导演驱动的自动化创作流水线中。**你的受众是导演，不是用户。** 严格遵守以下规则：

1. **凡是涉及内容创作或修改的任务，必须直接调用对应工具将内容落盘。** 严禁用正文输出内容来代替工具调用——例如：写好了世界观但不调用 `rewrite_worldview` 直接输出正文，是错误行为，工具必须被调用。
2. **工具已经被导演授权，无需征求用户确认，立即执行。** 每轮尽量并行调用相互独立且确有必要的工具；若工具结果存在关键缺口、冲突或失败，可以继续补查、修正和重试，不得为了减少轮次牺牲事实与产出完整性。
3. 若协作元信息中的 `completion_mode=\"silent_continue\"`：完成全部必要调查和落盘后，在最终工具批次中把 `complete_pipeline_step` 放在最后调用。成功后系统会直接把真实工具回执交还导演；不要再输出自然语言总结。工具失败、只完成部分内容或仍需补查时不得调用该标记。
4. 若 `completion_mode` 不是 `silent_continue`：全部工具执行完毕后，向导演报告完成了什么、关键结果摘要。
5. **绝对禁止**输出任何面向用户的引导语、前言、寒暄、解释说明或"如果你想要..."等话术。只有行动和必要报告。
"""
            else:
                tool_instruction += """
**工具确认边界**：
- **无副作用操作直接执行**：读取、搜索、检索、核对、查看状态等不会修改项目内容，也不会启动写入任务的操作，无需用户确认。需要时直接调用工具，禁止先询问“是否继续”。
- **有副作用操作确认一次**：写入或创建内容、完整重写、局部替换、移动或删除项目内容、启动后台/自动写作任务，以及委派会执行这些操作的创作任务，在用户尚未批准本次具体执行时，先简要说明影响范围并请求确认。
- 用户明确回复同意，或明确说“直接执行 / 无需确认”后，立即执行，不要重复确认。同一条执行链路只能确认一次；上游已确认后，后续步骤和被委派的 Agent 不得再次向用户确认。
- Director 委派属于已由上游处理确认的内部执行链路。子 Agent 收到 Director 委派后必须直接执行，不得再次申请批准。
- 如果用户只是询问或讨论，且无需查询事实，不要调用工具，正常对话即可。
"""
            system_instruction += tool_instruction

            tool_reference_block = self._build_tool_prompt_reference_block(tools_override=tools)
            if tool_reference_block:
                system_instruction += tool_reference_block

        # 自动加载 yaml 中的 tool_rules（Agent 特定的工具使用补充规则）
        if tools:
            try:
                from .agent_utils import load_prompt as _load_prompt
                _prompt_name = self.agent_id.replace("agent_", "")
                _prompts = _load_prompt(_prompt_name)
                _tool_rules = _prompts.get(tool_rules_key)
                if isinstance(_tool_rules, str) and _tool_rules.strip():
                    system_instruction += "\n\n" + _tool_rules.strip()
            except Exception:
                pass

        return system_instruction

    def _build_runtime_tail(self, *, tools_override: Optional[List[Any]] = None) -> str:
        """构建仅属于本轮请求的运行态尾部，避免污染可缓存的系统前缀。"""
        from agents.tools.registry import get_tools_for_agent

        tools = (
            list(tools_override)
            if tools_override is not None
            else get_tools_for_agent(self.agent_id, user_id=self.user_id)
        )
        parts: List[str] = []

        if any(getattr(tool, "name", "") == "web_search" for tool in tools):
            try:
                from datetime import datetime
                from zoneinfo import ZoneInfo

                search_date = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
            except Exception:
                search_date = time.strftime("%Y-%m-%d")
            parts.append(
                "### 本轮运行态信息\n"
                f"- 当前真实日期（UTC+8）：{search_date}"
            )

        if any(getattr(tool, "name", "") == "search_skills" for tool in tools):
            skill_catalog = self._build_skill_catalog_prompt_block().strip()
            if skill_catalog:
                parts.append(skill_catalog)

        if not self.project_name:
            return "\n\n".join(parts)
        try:
            from agents.work_tracker import build_work_tracker_prompt_context

            if any(getattr(tool, "name", "") == "work_tracker" for tool in tools):
                tracker_context = build_work_tracker_prompt_context(
                    self.user_id,
                    self.project_name,
                    self.agent_id,
                )
                if tracker_context:
                    parts.append(tracker_context)
        except Exception:
            pass
        return "\n\n".join(parts)

    def _build_skill_catalog_prompt_block(self) -> str:
        """注入已安装 Skill 的最小索引，帮助模型选择要读取的 Skill。"""
        try:
            from agents.skill_packs import list_effective_skills

            skills = list_effective_skills(self.user_id)
        except Exception:
            return ""

        if not skills:
            return ""

        lines = ["\n### 当前可用 Agent Skills（最小索引）"]
        for item in skills:
            skill_id = str(item.get("skill_id") or "").strip()
            name = str(item.get("name") or item.get("normalized_name") or "").strip()
            description = str(item.get("description") or "").strip()
            domain = str(item.get("domain") or "").strip()
            parts = [f"skill_id={skill_id}"]
            if name:
                parts.append(f"name={name}")
            if domain:
                parts.append(f"domain={domain}")
            if description:
                parts.append(f"description={description}")
            lines.append("- " + "；".join(parts))
        lines.append("需要采用某个 Skill 时，先按 skill_id 调用 `read_skill`；只有需要额外参考文件时再调用 `read_skill_reference`。")
        return "\n".join(lines) + "\n"

    def _get_tool_prompt_references(self) -> Dict[str, list[dict]]:
        """返回工具名到 YAML 提示词片段的映射。子类可覆盖。"""
        return {}

    def _get_tool_prompt_reference_values(self) -> Dict[str, Dict[str, Any]]:
        """返回加载工具参考提示词时使用的占位符默认值。"""
        return {}

    def _build_tool_prompt_reference_block(
        self,
        *,
        tools_override: Optional[List[Any]] = None,
    ) -> str:
        from agents.tools.registry import get_tools_for_agent
        from .agent_utils import load_prompt

        references = self._get_tool_prompt_references() or {}
        if not references:
            return ""

        prompt_name = self.agent_id.replace("agent_", "")
        tools = (
            list(tools_override)
            if tools_override is not None
            else get_tools_for_agent(self.agent_id, user_id=self.user_id)
        )
        tool_names = {tool.name for tool in tools}
        reference_values = self._get_tool_prompt_reference_values() or {}
        blocks: list[str] = []

        for tool_name, ref_items in references.items():
            if tool_name not in tool_names or not ref_items:
                continue

            snippets: list[str] = []
            for item in ref_items:
                if not isinstance(item, dict):
                    continue

                prompt_key = item.get("prompt_key")
                field = item.get("field", "system")
                values = reference_values.get(prompt_key or "__root__", {})

                try:
                    prompt_payload = load_prompt(prompt_name, prompt_key, **values) if prompt_key else load_prompt(prompt_name, **values)
                except Exception:
                    continue

                if not isinstance(prompt_payload, dict):
                    continue

                content = prompt_payload.get(field)
                if isinstance(content, str) and content.strip():
                    snippets.append(content.strip())

            if snippets:
                blocks.append(
                    f"### 当你决定调用工具 `{tool_name}` 时，必须复用以下既有生成规范（这些规范与手动触发生成使用的是同一套来源）：\n\n"
                    + "\n\n".join(snippets)
                )

        if not blocks:
            return ""

        return "\n\n### 工具执行时必须复用的既有生成提示词\n" + "\n\n".join(blocks)

    def _execute_tool_calls(self, tool_calls: list) -> str:
        from agents.tools.registry import TOOLS_BY_NAME
        from core.request_context import current_agent_id
        import traceback
        
        sink = get_tool_event_sink()

        results = []
        for tool_call in tool_calls:
            if isinstance(tool_call, dict) and "raw" in tool_call:
                raw_tool_call = tool_call.get("raw")
                tool_name = str(tool_call.get("name") or self._extract_tool_name(raw_tool_call))
                tool_args = tool_call.get("args")
                if not isinstance(tool_args, dict) or not tool_args:
                    tool_args = self._extract_tool_args(raw_tool_call)
            else:
                raw_tool_call = tool_call
                tool_name = self._extract_tool_name(raw_tool_call)
                tool_args = self._extract_tool_args(raw_tool_call)

            tool_name = normalize_tool_name(tool_name)
            tool = TOOLS_BY_NAME.get(tool_name)
            tool_args = normalize_tool_args(tool_args, tool=tool)
            if isinstance(tool_call, dict):
                tool_call["name"] = tool_name
                tool_call["args"] = tool_args
                raw = tool_call.get("raw")
                if isinstance(raw, dict):
                    raw["name"] = tool_name
                    raw["args"] = tool_args
            tool_call_key = self._extract_tool_call_id(raw_tool_call) or f"{self.agent_id}:{tool_name}:{uuid.uuid4().hex}"

            self._debug_tool_event(
                "tool_invoke",
                tool_name=tool_name,
                arg_keys=list(tool_args.keys()) if isinstance(tool_args, dict) else [],
                has_args=bool(tool_args),
                raw_type=type(raw_tool_call).__name__,
            )

            # 向 sink 推送工具开始事件（供外层 chat_stream 转发给前端）
            if sink is not None:
                _extra_exec: dict = {}
                sink.put(build_tool_stream_event(
                    "tool_exec_started",
                    tool_name,
                    source_agent=self.agent_id,
                    tool_call_key=tool_call_key,
                    tool_input=tool_args,
                    **_extra_exec,
                ))

            if tool:
                try:
                    agent_token = current_agent_id.set(self.agent_id)
                    try:
                        tool_result_text = tool.invoke(tool_args)
                        results.append(tool_result_text)
                    finally:
                        current_agent_id.reset(agent_token)
                    tool_failed = is_tool_result_failure(tool_name, tool_result_text)
                    if sink is not None and not tool_failed:
                        sink.put(build_tool_stream_event(
                            "tool_exec_finished",
                            tool_name,
                            source_agent=self.agent_id,
                            tool_call_key=tool_call_key,
                            tool_input=tool_args,
                            tool_result=tool_result_text,
                        ))
                    elif sink is not None:
                        sink.put(build_tool_stream_event(
                            "tool_exec_failed",
                            tool_name,
                            source_agent=self.agent_id,
                            tool_call_key=tool_call_key,
                            message=get_tool_result_failure_message(tool_name, tool_result_text),
                            tool_input=tool_args,
                            tool_error=tool_result_text,
                        ))
                except Exception as e:
                    tb = traceback.format_exc()
                    self._debug_tool_event(
                        "tool_invoke_error",
                        tool_name=tool_name,
                        has_args=bool(tool_args),
                        error=str(e),
                    )
                    if tool_name == "work_tracker":
                        from agents.tools.automation import format_work_tracker_validation_error

                        tool_error_text = format_work_tracker_validation_error(tool_args, e)
                    else:
                        tool_error_text = f"工具 {tool_name} 执行失败: {e}\n请检查参数格式后重新调用。"
                    results.append(tool_error_text)
                    if sink is not None:
                        sink.put(build_tool_stream_event(
                            "tool_exec_failed",
                            tool_name,
                            source_agent=self.agent_id,
                            tool_call_key=tool_call_key,
                            message=get_tool_result_failure_message(tool_name, tool_error_text),
                            tool_input=tool_args,
                            tool_error=tool_error_text,
                        ))
            else:
                results.append(f"未知工具: {tool_name}")
                if sink is not None:
                    sink.put(build_tool_stream_event(
                        "tool_exec_failed",
                        tool_name,
                        source_agent=self.agent_id,
                        tool_call_key=tool_call_key,
                        message="模型调用了不存在的工具，正在尝试修正",
                        tool_input=tool_args,
                        tool_error=f"未知工具: {tool_name}",
                    ))
        
        return "\n".join(results)

    def _tool_call_as_dict(self, value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if value is None:
            return {}
        if hasattr(value, "model_dump"):
            try:
                dumped = value.model_dump()
                return dumped if isinstance(dumped, dict) else {}
            except Exception:
                pass
        if hasattr(value, "dict"):
            try:
                dumped = value.dict()
                return dumped if isinstance(dumped, dict) else {}
            except Exception:
                pass
        try:
            return dict(value)
        except Exception:
            return {}

    @staticmethod
    def _tool_debug_enabled() -> bool:
        """是否输出工具调用调试日志。
        默认关闭，避免污染正式日志。
        SPARKARC_DEBUG_TOOL_ARGS=1
        """
        raw = (os.getenv("SPARKARC_DEBUG_TOOL_ARGS") or "").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def _debug_tool_event(self, stage: str, **payload: Any) -> None:
        if not self._tool_debug_enabled():
            return
        try:
            body = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            body = str(payload)
        print(f"[tool-debug][{self.agent_id}][{stage}] {body}")

    @staticmethod
    def _extract_json_object_text(text: str) -> str:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end >= start:
            return text[start:end + 1]
        return text

    def _parse_tool_args_value(self, value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if value is None:
            return {}
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            candidates = [text]
            extracted = self._extract_json_object_text(text)
            if extracted != text:
                candidates.append(extracted)
            for candidate in candidates:
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    continue
            return {}
        return {}

    @staticmethod
    def _tool_spec_has_args(spec: Dict[str, Any]) -> bool:
        args = spec.get("args")
        return isinstance(args, dict) and any(value is not None for value in args.values())

    def _extract_tool_call_id(self, tool_call: Any) -> str:
        return extract_tool_call_id(tool_call)

    def _dedupe_tool_specs(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按 tool id / name 去重，优先保留带参数的版本。

        某些模型 / SDK 会同时在 `tool_calls`、`invalid_tool_calls`、
        `additional_kwargs.tool_calls` 中保留同一条调用。如果不做去重，可能造成
        同一个工具被执行两次。
        """
        return dedupe_tool_specs(items)

    def _prepare_tool_specs_for_execution(self, tool_specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """为工具执行和下一轮消息历史建立同一份规范调用。"""
        from agents.tools.registry import TOOLS_BY_NAME

        return prepare_tool_specs_for_execution(
            tool_specs,
            normalize_name=normalize_tool_name,
            tool_lookup=TOOLS_BY_NAME,
        )

    @staticmethod
    def _build_tool_history_message(message: Any, tool_specs: List[Dict[str, Any]]) -> Any:
        """重建协议合法的 assistant 工具消息，只声明实际进入执行链的调用。"""
        return build_tool_history_message(message, tool_specs)

    def _merge_tool_args_into_raw(
        self,
        raw: Any,
        *,
        tool_name: Optional[str],
        args: Dict[str, Any],
        raw_args_text: str = "",
    ) -> Dict[str, Any]:
        raw_dict = dict(self._tool_call_as_dict(raw))
        function_dict = dict(self._tool_call_as_dict(raw_dict.get("function")))

        resolved_name = tool_name or raw_dict.get("name") or function_dict.get("name")
        args_text = (raw_args_text or json.dumps(args, ensure_ascii=False)).strip() or "{}"

        raw_dict["type"] = raw_dict.get("type") or "tool_call"
        if resolved_name:
            raw_dict["name"] = resolved_name
            function_dict["name"] = resolved_name

        raw_dict["args"] = args
        raw_dict["arguments"] = args_text

        if function_dict or resolved_name:
            function_dict["arguments"] = args_text
            raw_dict["function"] = function_dict

        return raw_dict

    def _append_tool_call_chunk_buffer(
        self,
        chunk_buffers: Dict[int, Dict[str, Any]],
        tool_call_chunk: Any,
    ) -> int:
        """累积流式工具调用碎片。

        某些模型会把 JSON 参数拆成很多小段返回；如果只看最终聚合结果，
        偶发情况下 LangChain 可能给出空 `args`。这里把碎片按 index 暂存，
        在流式结束后做一次兜底恢复。
        """
        chunk_dict = self._tool_call_as_dict(tool_call_chunk)

        function_obj = chunk_dict.get("function") or getattr(tool_call_chunk, "function", None)
        function_dict = self._tool_call_as_dict(function_obj)

        tool_name = (
            chunk_dict.get("name")
            or getattr(tool_call_chunk, "name", None)
            or function_dict.get("name")
            or getattr(function_obj, "name", None)
        )
        call_id = chunk_dict.get("id")
        if call_id is None:
            call_id = getattr(tool_call_chunk, "id", None)

        index = chunk_dict.get("index")
        if index is None:
            index = getattr(tool_call_chunk, "index", None)
        if index is None and call_id:
            for existing_index, existing in chunk_buffers.items():
                if existing.get("id") == str(call_id):
                    index = existing_index
                    break
        if index is None and tool_name:
            normalized_tool_name = normalize_tool_name(str(tool_name))
            for existing_index, existing in chunk_buffers.items():
                if normalize_tool_name(str(existing.get("name") or "")) == normalized_tool_name:
                    index = existing_index
                    break
        if index is None and len(chunk_buffers) == 1:
            index = next(iter(chunk_buffers.keys()))
        if index is None:
            index = len(chunk_buffers)
        index = int(index)

        buf = chunk_buffers.setdefault(index, {
            "index": index,
            "id": "",
            "name": None,
            "args_parts": [],
            "raw": [],
        })

        if tool_name and not buf["name"]:
            buf["name"] = str(tool_name)
        if call_id and not buf["id"]:
            buf["id"] = str(call_id)

        args_piece = chunk_dict.get("args")
        if args_piece is None:
            args_piece = getattr(tool_call_chunk, "args", None)
        if args_piece is None:
            args_piece = function_dict.get("arguments") or getattr(function_obj, "arguments", None)

        if isinstance(args_piece, dict):
            buf["args_parts"].append(json.dumps(args_piece, ensure_ascii=False))
        elif isinstance(args_piece, str):
            buf["args_parts"].append(args_piece)

        buf["raw"].append(chunk_dict or str(tool_call_chunk))
        return index

    def _tool_call_event_key(
        self,
        tool_name: str,
        raw_tool_call: Any = None,
        tool_index: Any = None,
        fallback_index: int = 0,
    ) -> str:
        normalized_tool_name = normalize_tool_name(tool_name)
        if tool_index is not None:
            return f"{self.agent_id}:{normalized_tool_name}:{tool_index}"
        raw_call_id = self._extract_tool_call_id(raw_tool_call)
        if raw_call_id:
            return raw_call_id
        return f"{self.agent_id}:{normalized_tool_name}:{fallback_index}"

    def _build_tool_specs_from_chunk_buffers(self, chunk_buffers: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
        specs: List[Dict[str, Any]] = []
        for index in sorted(chunk_buffers.keys()):
            item = chunk_buffers[index]
            name = (item.get("name") or "").strip() or "unknown_tool"
            args_text = "".join(item.get("args_parts") or []).strip()
            args = self._parse_tool_args_value(args_text)
            raw = self._merge_tool_args_into_raw(
                {
                    "id": item.get("id") or "",
                    "type": "tool_call",
                },
                tool_name=name if name != "unknown_tool" else None,
                args=args,
                raw_args_text=args_text,
            )
            specs.append({
                "raw": raw,
                "name": name,
                "args": args,
                "index": index,
            })
        return specs

    def _hydrate_tool_specs_from_chunk_buffers(
        self,
        specs: List[Dict[str, Any]],
        chunk_buffers: Dict[int, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        chunk_specs = self._build_tool_specs_from_chunk_buffers(chunk_buffers)
        if not chunk_specs:
            return specs

        if not specs:
            self._debug_tool_event("tool_specs_from_chunks", count=len(chunk_specs))
            return self._dedupe_tool_specs(chunk_specs)

        merged: List[Dict[str, Any]] = []
        for index, spec in enumerate(specs):
            current = dict(spec)
            current_args = current.get("args") if isinstance(current.get("args"), dict) else {}
            needs_hydration = not current_args or any(value is None for value in current_args.values())
            if not needs_hydration:
                merged.append(current)
                continue

            fallback = next(
                (
                    item for item in chunk_specs
                    if item.get("index") == index and self._tool_spec_has_args(item)
                ),
                None,
            )
            if fallback is None:
                fallback = next(
                    (
                        item for item in chunk_specs
                        if item.get("name") == current.get("name") and self._tool_spec_has_args(item)
                    ),
                    None,
                )

            if fallback is not None:
                fallback_args = fallback.get("args") if isinstance(fallback.get("args"), dict) else {}
                merged_args = dict(fallback_args)
                merged_args.update({key: value for key, value in current_args.items() if value is not None})
                current["args"] = merged_args
                if current.get("index") is None:
                    current["index"] = fallback.get("index")
                fallback_raw = fallback.get("raw") or {}
                current["raw"] = self._merge_tool_args_into_raw(
                    current.get("raw"),
                    tool_name=current.get("name") or fallback.get("name"),
                    args=current["args"],
                    raw_args_text=fallback_raw.get("arguments", ""),
                )
                self._debug_tool_event(
                    "tool_args_hydrated_from_chunks",
                    tool_name=current.get("name"),
                    arg_keys=list(current["args"].keys()),
                )
            merged.append(current)

        if any(self._tool_spec_has_args(item) for item in merged):
            return self._dedupe_tool_specs(merged)

        resolved_chunk_specs = [
            item for item in chunk_specs
            if (item.get("name") or "") not in {"", "unknown_tool"}
        ]
        if resolved_chunk_specs:
            self._debug_tool_event("tool_specs_fallback_to_chunks", count=len(resolved_chunk_specs))
            return self._dedupe_tool_specs(resolved_chunk_specs)

        return self._dedupe_tool_specs(merged)

    def _extract_tool_args(self, tool_call: Any) -> Dict[str, Any]:
        tool_call_dict = self._tool_call_as_dict(tool_call)
        function_obj = tool_call_dict.get("function") or getattr(tool_call, "function", None)
        function_dict = self._tool_call_as_dict(function_obj)

        tool_args = tool_call_dict.get("args")
        if tool_args is None:
            tool_args = getattr(tool_call, "args", None)
        parsed_args = self._parse_tool_args_value(tool_args)
        if parsed_args:
            return parsed_args

        args_str = (
            tool_call_dict.get("arguments")
            or function_dict.get("arguments")
            or getattr(tool_call, "arguments", None)
            or getattr(function_obj, "arguments", None)
            or "{}"
        )
        parsed_args = self._parse_tool_args_value(args_str)
        if parsed_args:
            return parsed_args

        additional = tool_call_dict.get("additional_kwargs") or getattr(tool_call, "additional_kwargs", None) or {}
        raw_tool_calls = additional.get("tool_calls") or []
        if isinstance(raw_tool_calls, list):
            for raw_call in raw_tool_calls:
                raw_dict = self._tool_call_as_dict(raw_call)
                parsed_args = self._parse_tool_args_value(
                    raw_dict.get("args")
                    or raw_dict.get("arguments")
                    or raw_dict.get("function", {}).get("arguments")
                )
                if parsed_args:
                    return parsed_args

        invalid_tool_calls = tool_call_dict.get("invalid_tool_calls") or getattr(tool_call, "invalid_tool_calls", None) or []
        if isinstance(invalid_tool_calls, list):
            for invalid_call in invalid_tool_calls:
                invalid_dict = self._tool_call_as_dict(invalid_call)
                parsed_args = self._parse_tool_args_value(
                    invalid_dict.get("args")
                    or invalid_dict.get("arguments")
                    or invalid_dict.get("function", {}).get("arguments")
                )
                if parsed_args:
                    return parsed_args

        return {}

    def _extract_tool_call_specs_from_message(self, message: Any) -> list[dict]:
        specs: list[dict] = []

        def _has_resolved_name(items: list[dict]) -> bool:
            return any((item.get("name") or "") not in {"", "unknown_tool"} for item in items)

        def _has_resolved_args(items: list[dict]) -> bool:
            return any(
                (item.get("name") or "") not in {"", "unknown_tool"}
                and self._tool_spec_has_args(item)
                for item in items
            )

        def _resolved_only(items: list[dict]) -> list[dict]:
            resolved = [item for item in items if (item.get("name") or "") not in {"", "unknown_tool"}]
            return self._dedupe_tool_specs(resolved or items)

        tool_calls = getattr(message, "tool_calls", None) or []
        for index, tool_call in enumerate(tool_calls):
            specs.append({
                "raw": tool_call,
                "name": self._extract_tool_name(tool_call),
                "args": self._extract_tool_args(tool_call),
                "index": index,
            })

        if _has_resolved_args(specs):
            return _resolved_only(specs)

        invalid_tool_calls = getattr(message, "invalid_tool_calls", None) or []
        for index, tool_call in enumerate(invalid_tool_calls):
            specs.append({
                "raw": tool_call,
                "name": self._extract_tool_name(tool_call),
                "args": self._extract_tool_args(tool_call),
                "index": index,
            })

        if _has_resolved_args(specs):
            return _resolved_only(specs)

        additional = getattr(message, "additional_kwargs", None) or {}
        raw_tool_calls = additional.get("tool_calls") or []
        if isinstance(raw_tool_calls, list):
            for index, tool_call in enumerate(raw_tool_calls):
                specs.append({
                    "raw": tool_call,
                    "name": self._extract_tool_name(tool_call),
                    "args": self._extract_tool_args(tool_call),
                    "index": index,
                })

        if _has_resolved_args(specs):
            return _resolved_only(specs)

        function_call = additional.get("function_call")
        if function_call:
            specs.append({
                "raw": {"function": function_call, "type": "tool_call"},
                "name": self._extract_tool_name({"function": function_call}),
                "args": self._extract_tool_args({"function": function_call}),
            })

        return _resolved_only(specs)

    def _extract_tool_name(self, tool_call: dict) -> str:
        tool_call_dict = self._tool_call_as_dict(tool_call)
        function_obj = tool_call_dict.get("function") or getattr(tool_call, "function", None)
        function_dict = self._tool_call_as_dict(function_obj)

        name = (
            tool_call_dict.get("name")
            or function_dict.get("name")
            or getattr(tool_call, "name", None)
            or getattr(function_obj, "name", None)
        )
        if name:
            return name

        additional = tool_call_dict.get("additional_kwargs") or getattr(tool_call, "additional_kwargs", None) or {}
        raw_tool_calls = additional.get("tool_calls") or []
        if isinstance(raw_tool_calls, list):
            for raw_call in raw_tool_calls:
                raw_name = (raw_call.get("name") or raw_call.get("function", {}).get("name")) if isinstance(raw_call, dict) else None
                if raw_name:
                    return raw_name

        function_call = additional.get("function_call")
        if isinstance(function_call, dict) and function_call.get("name"):
            return function_call.get("name")

        return "unknown_tool"

    def _tool_progress_text(self, tool_name: str) -> str:
        mapping = {
            "rewrite_inspiration": "正在重写当前灵感...",
            "rewrite_worldview": "正在重写世界观设定...",
            "rewrite_all_characters": "正在重写所有角色设定...",
            "update_character": "正在更新角色设定...",
            "create_character_relation": "正在记录角色关系...",
            "patch_worldview": "正在局部更新世界观...",
            "rewrite_synopsis": "正在重写故事梗概...",
            "patch_synopsis": "正在局部更新故事梗概...",
            "rewrite_beat_sheet": "正在重写节拍表...",
            "patch_beat_sheet": "正在局部更新节拍表...",
            "rewrite_outline": "正在重写剧情大纲...",
            "patch_outline": "正在局部更新剧情大纲...",
            "create_chapter": "正在创建章节...",
            "prepare_script_creation": "编剧调研",
            "create_or_rewrite_script": "正在新建/重写剧本文本...",
            "patch_script": "正在局部更新剧本文本...",
            "list_chapters": "正在查阅章节结构...",
            "read_chapter_scene": "正在读取章节内容...",
            "read_chapter_outline_raw": "正在读取章节大纲原文...",
            "read_attachment_chunk": "正在读取附件分片...",
            "read_longread_window": "正在读取长文档窗口...",
            "describe_longread_source": "正在查看长文档地图...",
            "note_window_clues": "正在记录窗口线索...",
            "read_worldview_window": "正在读取世界观窗口...",
            "search_skills": "正在检索 Agent Skills...",
            "read_skill": "正在读取 Skill 质量视图...",
            "read_skill_reference": "正在读取 Skill 参考文本...",
            "delegate_task": "正在委派任务...",
            "web_search": "正在联网搜索外部资料...",
        }
        return mapping.get(tool_name, f"正在执行工具 {tool_name} ...")

    def _tool_event_metadata(self, tool_name: str, tool_args: Any = None) -> Dict[str, Any]:
        """提取可安全展示、持久化的工具调用元数据。"""
        if normalize_tool_name(tool_name) != "web_search" or not isinstance(tool_args, dict):
            return {}
        provider = str(tool_args.get("provider") or "").strip().lower()
        if provider not in {"exa", "tavily"}:
            return {}
        return {"tool_provider": provider}

    def _extract_active_context_from_history(self, history: List[Dict[str, Any]] | None) -> Optional[str]:
        if not history:
            return None
        # Prefer most recent active_context stored in metadata
        for msg in reversed(history):
            meta = msg.get("metadata") or {}
            ctx = meta.get("active_context") or meta.get("activeContext")
            if isinstance(ctx, str) and ctx.strip():
                return ctx
        return None

    def chat(
        self,
        user_message: str,
        history: List[Dict[str, Any]] = None,
        active_context: str = None,
        skip_tool_confirmation: bool = False,
        stop_after_pipeline_completion: bool = False,
    ) -> str:
        """
        通用的直接对话入口。
        """
        from .agent_utils import load_prompt
        self._set_context_checkpoint_candidate(None)

        if not active_context:
            active_context = self._extract_active_context_from_history(history)
        
        # 1. 加载提示词
        # 假设 YAML 中有名为 'system' 的顶级键作为系统提示词
        # 如果没有对应的 yaml，则使用基础提示词
        try:
            # 去掉 agent_ 前缀
            prompt_name = self.agent_id.replace("agent_", "")
            prompts = load_prompt(prompt_name)
            # 流水线委派模式：优先 pipeline_system；普通对话模式：优先 chat_system
            if skip_tool_confirmation:
                system_prompt = prompts.get('pipeline_system') or prompts.get('chat_system') or prompts.get('system', f"你是一个专业的助手：{self.name}")
            else:
                system_prompt = prompts.get('chat_system') or prompts.get('system', f"你是一个专业的助手：{self.name}")
        except Exception:
            system_prompt = f"你是一个专业的助手：{self.name}。你的职责是：{self.intro}"

        from agents.tools.registry import get_tools_for_agent

        tools = get_tools_for_agent(
            self.agent_id,
            user_id=self.user_id,
            pipeline_mode=skip_tool_confirmation,
        )

        # 1.1 注入互动模式与工具说明；动态上下文由 PromptLayout 放入最后 user
        system_instruction = self._build_tool_system_prompt(
            system_prompt,
            active_context,
            skip_tool_confirmation=skip_tool_confirmation,
            tools_override=tools,
        )
        from agents.prompt_layout import build_chat_prompt_layout
        runtime_tail = self._build_runtime_tail(tools_override=tools)
        prompt_layout = build_chat_prompt_layout(
            system_instruction=system_instruction,
            user_message=user_message,
            active_context=active_context,
            runtime_tail=runtime_tail,
        )

        # 2. 调用 LLM（支持多轮工具调用）
        try:
            from llm.agen_matchbox import matchbox
            from agents.context_budget import prepare_chat_messages_with_budget, rebudget_existing_messages

            invoke_llm = matchbox().get_user_llm(
                self.user_id,
                agent_name=self.agent_id,
            )
            base_llm_client = invoke_llm
            budget_result = prepare_chat_messages_with_budget(
                user_id=self.user_id,
                project_name=self.project_name,
                agent_id=self.agent_id,
                system_instruction=prompt_layout.system_instruction,
                history=history,
                user_message=prompt_layout.user_message,
                llm_client=base_llm_client,
                tools=tools,
            )
            self._set_context_checkpoint_candidate(budget_result.checkpoint)
            messages = budget_result.messages
            if tools:
                invoke_llm = invoke_llm.bind_tools(tools)

            import logging
            _chat_logger = logging.getLogger("chat_debug")

            _chat_logger.warning(
                "[chat] agent=%s tools_bound=%s tool_names=%s",
                self.agent_id,
                bool(tools),
                [t.name for t in tools] if tools else [],
            )

            pipeline_write_receipts: List[tuple[str, Any]] = []
            while True:
                response = invoke_llm.invoke(messages)

                _chat_logger.warning(
                    "[chat] agent=%s response_type=%s has_tool_calls=%s tool_calls=%s content_preview=%s",
                    self.agent_id,
                    type(response).__name__,
                    bool(getattr(response, "tool_calls", None)),
                    getattr(response, "tool_calls", None),
                    (response.content[:200] if isinstance(response.content, str) else str(response.content)[:200]),
                )

                tool_specs = self._prepare_tool_specs_for_execution(
                    self._extract_tool_call_specs_from_message(response)
                )
                self._debug_tool_event(
                    "chat_invoke_tool_specs",
                    count=len(tool_specs),
                    names=[spec.get("name") for spec in tool_specs],
                    has_args=[self._tool_spec_has_args(spec) for spec in tool_specs],
                )

                if not tool_specs:
                    response_text = extract_text_content_from_message(response)
                    if response_text:
                        return response_text
                    return extract_visible_text_from_plain_text(
                        response.content if isinstance(response.content, str) else str(response.content)
                    )

                # 执行工具并收集结果，准备下一轮
                tool_results = []
                for tool_spec in tool_specs:
                    tool_call_id = tool_spec["call_id"]
                    tool_name = normalize_tool_name(str(
                        tool_spec.get("name")
                        or self._extract_tool_name(tool_spec.get("raw"))
                    ))
                    result = self._execute_tool_calls([tool_spec])
                    tool_results.append((tool_call_id, tool_name, result))

                if stop_after_pipeline_completion:
                    completion_receipt = resolve_pipeline_completion(
                        self.agent_id,
                        tool_results,
                        pipeline_write_receipts,
                    )
                    if completion_receipt:
                        return completion_receipt

                # 将 AI 消息（含 tool_calls）和工具结果追加到消息历史
                # 清洗 think 标签，避免下一轮 LLM 把推理内容当正文回显
                if isinstance(response.content, str) and response.content:
                    response.content = extract_visible_text_from_plain_text(response.content)
                messages.append(self._build_tool_history_message(response, tool_specs))
                fresh_call_ids = {cid for cid, _, _ in tool_results}
                messages.extend(build_tool_result_messages(tool_results))
                # 长文档滑窗折叠（longread 统一收口）：只折旧 user 轮次窗口，
                # 本轮新读原文完整保留；新结果追加后调用一次，不反复改写中间。
                collapse_attachment_chunk_history(
                    messages,
                    fresh_call_ids=fresh_call_ids,
                    ledger=_current_longread_ledger(),
                )
                messages = rebudget_existing_messages(
                    user_id=self.user_id,
                    project_name=self.project_name,
                    agent_id=self.agent_id,
                    messages=messages,
                    llm_client=base_llm_client,
                    tools=tools,
                    current_user_message=user_message,
                ).messages

        except Exception as e:
            import traceback
            traceback.print_exc()
            from agents.context_budget import NonRetryableChatError
            if isinstance(e, NonRetryableChatError):
                raise
            raise

    def chat_stream(
        self,
        user_message: str,
        history: List[Dict[str, Any]] = None,
        active_context: str = None,
        skip_tool_confirmation: bool = False,
        stop_event: Any = None,
        stop_after_pipeline_completion: bool = False,
        tools_override: Optional[List[Any]] = None,
        prepared_history_messages: Optional[List[Any]] = None,
        conversation_recorder: Optional[Callable[[List[Any]], None]] = None,
    ):
        """通用流式对话入口。逐段 yield 文本增量。"""
        from .agent_utils import load_prompt
        self._set_context_checkpoint_candidate(None)

        if is_stop_event_set(stop_event):
            return

        if not active_context:
            active_context = self._extract_active_context_from_history(history)

        try:
            prompt_name = self.agent_id.replace("agent_", "")
            prompts = load_prompt(prompt_name)
            # 流水线委派模式：优先 pipeline_system；普通对话模式：优先 chat_system
            if skip_tool_confirmation:
                system_prompt = prompts.get('pipeline_system') or prompts.get('chat_system') or prompts.get('system', f"你是一个专业的助手：{self.name}")
            else:
                system_prompt = prompts.get('chat_system') or prompts.get('system', f"你是一个专业的助手：{self.name}")
        except Exception:
            system_prompt = f"你是一个专业的助手：{self.name}。你的职责是：{self.intro}"

        from agents.tools.registry import get_tools_for_agent

        tools = (
            list(tools_override)
            if tools_override is not None
            else get_tools_for_agent(
                self.agent_id,
                user_id=self.user_id,
                pipeline_mode=skip_tool_confirmation,
            )
        )
        system_instruction = self._build_tool_system_prompt(
            system_prompt,
            active_context,
            skip_tool_confirmation=skip_tool_confirmation,
            tools_override=tools,
        )
        from agents.prompt_layout import build_chat_prompt_layout
        runtime_tail = self._build_runtime_tail(tools_override=tools)
        prompt_layout = build_chat_prompt_layout(
            system_instruction=system_instruction,
            user_message=user_message,
            active_context=active_context,
            runtime_tail=runtime_tail,
        )

        from llm.agen_matchbox import matchbox
        from agents.context_budget import (
            prepare_chat_messages_with_budget,
            rebudget_existing_messages,
            stream_context_budget_events,
        )
        stream_llm = matchbox().get_user_llm(
            self.user_id,
            agent_name=self.agent_id,
        )
        base_stream_llm = stream_llm
        if prepared_history_messages is None:
            budget_result = yield from stream_context_budget_events(
                prepare_chat_messages_with_budget,
                stop_event=stop_event,
                user_id=self.user_id,
                project_name=self.project_name,
                agent_id=self.agent_id,
                system_instruction=prompt_layout.system_instruction,
                history=history,
                user_message=prompt_layout.user_message,
                llm_client=base_stream_llm,
                tools=tools,
            )
        else:
            from langchain_core.messages import HumanMessage, SystemMessage

            prepared_messages = [
                SystemMessage(content=prompt_layout.system_instruction),
                *list(prepared_history_messages),
                HumanMessage(content=prompt_layout.user_message),
            ]
            budget_result = yield from stream_context_budget_events(
                rebudget_existing_messages,
                stop_event=stop_event,
                user_id=self.user_id,
                project_name=self.project_name,
                agent_id=self.agent_id,
                messages=prepared_messages,
                llm_client=base_stream_llm,
                tools=tools,
                current_user_message=user_message,
            )
        self._set_context_checkpoint_candidate(budget_result.checkpoint)
        messages = budget_result.messages
        if tools:
            stream_llm = stream_llm.bind_tools(tools)

        from agents.runtime_capture import RuntimeCapture

        capture = None
        if self.agent_id == "agent_scriptwriter":
            from core.request_context import get_current_export_format

            visual_enabled = None
            visual_settings_fn = getattr(self, "_visual_illustration_settings", None)
            if callable(visual_settings_fn):
                try:
                    visual_enabled = bool((visual_settings_fn() or {}).get("enabled"))
                except Exception:
                    visual_enabled = None
            capture = RuntimeCapture.create(
                agent_id=self.agent_id,
                user_id=str(self.user_id),
                project_name=str(self.project_name),
                metadata={
                    "capture_kind": "chat_stream",
                    "modality": "pipeline" if skip_tool_confirmation else "chat",
                    "skip_tool_confirmation": bool(skip_tool_confirmation),
                    "stop_after_pipeline_completion": bool(stop_after_pipeline_completion),
                    "user_message": str(user_message or ""),
                    "active_context": str(active_context or ""),
                    "runtime_tail": str(runtime_tail or ""),
                    "export_format": str(get_current_export_format() or "arc"),
                    "visual_illustration_enabled": visual_enabled,
                },
            )
        if capture is not None:
            capture.start(messages=messages, tools=tools)
        capture_error = ""

        try:
            pipeline_write_receipts: List[tuple[str, Any]] = []
            while True:
                if is_stop_event_set(stop_event):
                    return

                aggregated_chunk = None
                started_tools = set()
                tool_intent_keys: Dict[str, str] = {}
                tool_chunk_buffers: Dict[int, Dict[str, Any]] = {}
                stream_reasoning_adapter = MessageEventStreamReasoningAdapter()
                messages_before = list(messages)

                for chunk in stream_model_turn_with_retry(
                    stream_llm,
                    messages,
                    stop_event=stop_event,
                ):
                    if isinstance(chunk, ModelTurnRetryNotice):
                        aggregated_chunk = None
                        started_tools = set()
                        tool_intent_keys = {}
                        tool_chunk_buffers = {}
                        stream_reasoning_adapter = MessageEventStreamReasoningAdapter()
                        yield {
                            "event": "retry_attempt",
                            "attempt": chunk.attempt,
                            "max_retries": chunk.max_attempts,
                            "error_summary": chunk.error,
                            "retry_scope": "model_turn",
                            "source_agent": self.agent_id,
                        }
                        continue
                    if is_stop_event_set(stop_event):
                        return

                    if aggregated_chunk is None:
                        aggregated_chunk = chunk
                    else:
                        try:
                            aggregated_chunk = aggregated_chunk + chunk
                        except Exception:
                            pass

                    # 流式事件分发
                    tool_call_chunks = getattr(chunk, 'tool_call_chunks', None) or []
                    for tcc in tool_call_chunks:
                        buffer_index = self._append_tool_call_chunk_buffer(tool_chunk_buffers, tcc)

                        tcc_dict = self._tool_call_as_dict(tcc)
                        tool_index = tcc_dict.get('index')
                        if tool_index is None:
                            tool_index = getattr(tcc, 'index', None)
                        if tool_index is None:
                            tool_index = buffer_index

                        tool_name = tcc_dict.get('name') or getattr(tcc, 'name', None)
                        if not tool_name and tool_index in tool_chunk_buffers:
                            tool_name = tool_chunk_buffers[tool_index].get('name')

                        if not tool_name:
                            continue
                        tool_name = normalize_tool_name(tool_name)
                        if tool_name == PIPELINE_COMPLETION_TOOL_NAME:
                            continue
                        tool_call_key = self._tool_call_event_key(tool_name, tcc, tool_index, len(started_tools))
                        if tool_call_key in started_tools:
                            continue
                        started_tools.add(tool_call_key)
                        tool_intent_keys[tool_call_key] = tool_call_key
                        raw_call_id = self._extract_tool_call_id(tcc)
                        if raw_call_id:
                            tool_intent_keys[raw_call_id] = tool_call_key
                        tool_intent_keys.setdefault(tool_name, tool_call_key)
                        progress_text = self._tool_progress_text(tool_name)
                        yield build_tool_stream_event(
                            "tool_intent_started",
                            tool_name,
                            source_agent=self.agent_id,
                            message=progress_text,
                            tool_call_key=tool_call_key,
                        )

                    reasoning, content = stream_reasoning_adapter.push_message(chunk)
                    if reasoning:
                        yield {"event": "reasoning_delta", "text": reasoning, "source_agent": self.agent_id}
                    if content:
                        yield {"event": "assistant_delta", "text": content, "source_agent": self.agent_id}

                trailing_reasoning, trailing_content = stream_reasoning_adapter.flush()
                if is_stop_event_set(stop_event):
                    return
                if trailing_reasoning:
                    yield {"event": "reasoning_delta", "text": trailing_reasoning, "source_agent": self.agent_id}
                if trailing_content:
                    yield {"event": "assistant_delta", "text": trailing_content, "source_agent": self.agent_id}

                tool_specs: List[Dict[str, Any]] = []
                if aggregated_chunk is not None:
                    tool_specs = self._extract_tool_call_specs_from_message(aggregated_chunk)
                tool_specs = self._hydrate_tool_specs_from_chunk_buffers(tool_specs, tool_chunk_buffers)
                tool_specs = self._prepare_tool_specs_for_execution(tool_specs)

                self._debug_tool_event(
                    "chat_stream_tool_specs",
                    count=len(tool_specs),
                    names=[spec.get("name") for spec in tool_specs],
                    has_args=[self._tool_spec_has_args(spec) for spec in tool_specs],
                    chunk_buffer_count=len(tool_chunk_buffers),
                )

                if not tool_specs:
                    if aggregated_chunk is not None:
                        if isinstance(aggregated_chunk.content, str) and aggregated_chunk.content:
                            aggregated_chunk.content = extract_visible_text_from_plain_text(aggregated_chunk.content)
                        messages.append(aggregated_chunk)
                    if capture is not None:
                        capture.record_turn(
                            ordinal=len(capture.payload.get("turns") or []) + 1,
                            messages_before=messages_before,
                            response=aggregated_chunk,
                            tool_specs=(),
                            tool_results=(),
                            messages_after=messages,
                        )
                    if conversation_recorder is not None:
                        conversation_recorder(list(messages[1:]))
                    break  # 没有工具调用，对话结束

                # 执行工具并收集结果（设置 sink 捕获嵌套工具事件）
                event_sink = queue.Queue()
                sink_token = set_tool_event_sink(event_sink)
                tool_results: List[tuple] = []
                cancelled_during_tools = False
                try:
                    for tool_spec in tool_specs:
                        if is_stop_event_set(stop_event):
                            cancelled_during_tools = True
                            break

                        tool_name = normalize_tool_name(str(tool_spec.get("name") or self._extract_tool_name(tool_spec.get("raw"))))
                        is_pipeline_control = tool_name == PIPELINE_COMPLETION_TOOL_NAME
                        spec_index = tool_spec.get("index")
                        raw_call_id = tool_spec["call_id"]
                        indexed_tool_call_key = self._tool_call_event_key(tool_name, tool_spec.get("raw"), spec_index, len(tool_results)) if spec_index is not None else ""
                        tool_call_key = (
                            (tool_intent_keys.get(raw_call_id) if raw_call_id else "")
                            or (tool_intent_keys.get(indexed_tool_call_key) if indexed_tool_call_key else "")
                            or indexed_tool_call_key
                            or (tool_intent_keys.get(tool_name) if len(tool_specs) == 1 else "")
                            or self._tool_call_event_key(tool_name, tool_spec.get("raw"), spec_index, len(tool_results))
                        )
                        progress_text = self._tool_progress_text(tool_name)
                        tool_event_metadata = self._tool_event_metadata(tool_name, tool_spec.get("args"))
                        if tool_event_metadata.get("tool_provider"):
                            progress_text = f"正在使用 {tool_event_metadata['tool_provider'].title()} 搜索..."

                        if not is_pipeline_control and tool_call_key not in started_tools:
                            yield build_tool_stream_event(
                                "tool_intent_started",
                                tool_name,
                                source_agent=self.agent_id,
                                message=progress_text,
                                tool_call_key=tool_call_key,
                                tool_input=tool_spec.get("args"),
                                **tool_event_metadata,
                            )
                            if is_stop_event_set(stop_event):
                                cancelled_during_tools = True
                                break
                            started_tools.add(tool_call_key)

                        if not is_pipeline_control:
                            yield build_tool_stream_event(
                                "tool_exec_started",
                                tool_name,
                                source_agent=self.agent_id,
                                message=progress_text,
                                tool_call_key=tool_call_key,
                                tool_input=tool_spec.get("args"),
                                **tool_event_metadata,
                            )
                        if is_stop_event_set(stop_event):
                            cancelled_during_tools = True
                            break

                        tool_result = self._execute_tool_calls([tool_spec])

                        # 排空 sink 中的嵌套工具事件并转发给前端
                        # 过滤掉与当前主工具同名的事件（外层已显式 yield）
                        while not event_sink.empty():
                            try:
                                nested_evt = event_sink.get_nowait()
                                if isinstance(nested_evt, dict):
                                    evt_tool = nested_evt.get("tool_name", "")
                                    if evt_tool == tool_name:
                                        continue  # 跳过重复的主工具事件
                                    nested_evt["nested"] = True
                                    nested_evt["parent_tool"] = tool_name
                                    yield nested_evt
                            except queue.Empty:
                                break

                        _is_tool_failure = is_pipeline_tool_result_failure(tool_name, tool_result)
                        if _is_tool_failure and not is_pipeline_control:
                            yield build_tool_stream_event(
                                "tool_exec_failed",
                                tool_name,
                                source_agent=self.agent_id,
                                tool_call_key=tool_call_key,
                                message=get_tool_result_failure_message(tool_name, tool_result),
                                tool_input=tool_spec.get("args"),
                                tool_error=tool_result,
                                **tool_event_metadata,
                            )
                        elif not is_pipeline_control:
                            yield build_tool_stream_event(
                                "tool_exec_finished",
                                tool_name,
                                source_agent=self.agent_id,
                                tool_call_key=tool_call_key,
                                tool_input=tool_spec.get("args"),
                                tool_result=tool_result,
                                **tool_event_metadata,
                            )

                        if is_stop_event_set(stop_event):
                            cancelled_during_tools = True
                            break

                        # 旁路检测：若工具返回文本携带 Auto-Write 触发标记，立即推送语义事件帧
                        _SIDEBAND_MARKER = "__director_auto_write_started__:"
                        if isinstance(tool_result, str) and tool_result.startswith(_SIDEBAND_MARKER):
                            _nl = tool_result.find("\n")
                            _meta_str = tool_result[len(_SIDEBAND_MARKER):_nl] if _nl != -1 else tool_result[len(_SIDEBAND_MARKER):]
                            try:
                                import json as _json
                                _meta = _json.loads(_meta_str.strip())
                                yield {"event": "director_auto_write_started", **_meta}
                            except Exception:
                                pass

                        tool_call_id = tool_spec["call_id"]
                        tool_results.append((tool_call_id, tool_name, tool_result))
                finally:
                    reset_tool_event_sink(sink_token)

                if cancelled_during_tools or is_stop_event_set(stop_event):
                    return

                # 将 AI 消息（含 tool_calls）和工具结果追加到消息历史，进入下一轮
                # 清洗 think 标签，避免下一轮 LLM 把推理内容当正文回显
                if aggregated_chunk is not None:
                    if isinstance(aggregated_chunk.content, str) and aggregated_chunk.content:
                        aggregated_chunk.content = extract_visible_text_from_plain_text(aggregated_chunk.content)
                    messages.append(self._build_tool_history_message(aggregated_chunk, tool_specs))
                fresh_call_ids = {cid for cid, _, _ in tool_results}
                messages.extend(build_tool_result_messages(tool_results))

                if capture is not None:
                    capture.record_turn(
                        ordinal=len(capture.payload.get("turns") or []) + 1,
                        messages_before=messages_before,
                        response=aggregated_chunk,
                        tool_specs=tool_specs,
                        tool_results=tool_results,
                        messages_after=messages,
                    )

                if stop_after_pipeline_completion:
                    completion_receipt = resolve_pipeline_completion(
                        self.agent_id,
                        tool_results,
                        pipeline_write_receipts,
                    )
                    if completion_receipt:
                        if conversation_recorder is not None:
                            conversation_recorder(list(messages[1:]))
                        yield {
                            "event": "pipeline_step_completed",
                            "source_agent": self.agent_id,
                            "receipt": completion_receipt,
                        }
                        return

                # 长文档滑窗折叠（longread 统一收口）：
                # - 任务进行中只追加、不改写中间历史；折叠只折“旧 user 轮次”的
                #   窗口，本轮新读的窗口原文完整保留；
                # - 新工具结果追加后调用一次，不在同一轮内反复改写，避免前缀
                #   缓存从第一个被改的 ToolMessage 起全部失效。
                # - 账本由 note_window_clues 在任务内存里维护，这里按需透传，
                #   缺账本时退化为通用占位符（不断链）。
                collapse_attachment_chunk_history(
                    messages,
                    fresh_call_ids=fresh_call_ids,
                    ledger=_current_longread_ledger(),
                )
                tool_budget_result = yield from stream_context_budget_events(
                    rebudget_existing_messages,
                    stop_event=stop_event,
                    user_id=self.user_id,
                    project_name=self.project_name,
                    agent_id=self.agent_id,
                    messages=messages,
                    llm_client=base_stream_llm,
                    tools=tools,
                    current_user_message=user_message,
                )
                messages = tool_budget_result.messages

        except Exception as e:
            capture_error = f"{type(e).__name__}: {e}"
            if is_stop_event_set(stop_event):
                return
            import traceback
            traceback.print_exc()
            from agents.context_budget import NonRetryableChatError
            from agents.error_formatting import format_ai_error
            if isinstance(e, NonRetryableChatError):
                yield e.to_event()
                return
            yield {
                "event": "error",
                "data": format_ai_error(e),
                "retryable": not isinstance(e, ModelStreamRetryExhaustedError),
            }
        finally:
            if capture is not None:
                capture.finalize(
                    messages=messages,
                    status="failed" if capture_error else "completed",
                    error=capture_error,
                )


class CommunicationContext:
    """
    通讯上下文管理器，负责在同一用户的不同 Agent 之间分发消息。
    """
    def __init__(self):
        # 存储结构：{ user_id: { agent_id: SparkBaseAgent } }
        self._user_namespaces: Dict[str, Dict[str, SparkBaseAgent]] = {}

    def register(self, agent: SparkBaseAgent):
        """
        将一个 Agent 实例注册到其所属用户的命名空间中。
        """
        uid = agent.user_id
        if uid not in self._user_namespaces:
            self._user_namespaces[uid] = {}
        
        self._user_namespaces[uid][agent.agent_id] = agent

    def dispatch(self, user_id: str, sender_id: str, target_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        在指定用户的命名空间内分发消息。
        这是一个同步调用过程，确保了用户间的数据隔离。
        """
        namespace = self._user_namespaces.get(user_id)
        if not namespace:
            return {
                "status": "error", 
                "message": f"未找到用户 '{user_id}' 的通讯命名空间"
            }
            
        target = namespace.get(target_id)
        if not target:
            return {
                "status": "error", 
                "message": f"在用户 '{user_id}' 的空间内未找到目标 Agent: '{target_id}'"
            }
        
        # 直接触发目标 Agent 的接收逻辑
        return target.receive_message(sender_id, payload)

    def list_available_agents(self, user_id: str) -> List[Dict[str, Any]]:
        """
        列出指定用户下所有已注册且开启了信标的 Agent。
        """
        namespace = self._user_namespaces.get(user_id, {})
        return [
            {
                "id": agent.agent_id,
                "name": agent.name,
                "intro": agent.intro
            }
            for agent in namespace.values()
            if agent.signals.is_beacon_open
        ]

# 全局通讯总线实例（单例模式）
_global_context = CommunicationContext()

def get_global_context() -> CommunicationContext:
    return _global_context

