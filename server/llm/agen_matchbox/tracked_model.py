"""
LLM 用量追踪模块

架构说明
--------
本模块提供三个核心类：

1. UsageTrackingCallback（BaseCallbackHandler）
   - 通过 LangChain 官方 Callback 机制截获所有 LLM 调用事件
   - 自动覆盖全部 9 种调用方式（invoke/stream/batch/generate 及其异步变体）
   - 无需自定义 BaseChatModel，直接使用原生 ChatOpenAI，完全兼容 OpenAI 协议
   - Token 统计策略：
     * 优先读取 API 返回的真实 usage 字段（标准 OpenAI 协议）
     * 若 API 不返回 usage（国产模型、截断输出等），自动降级为本地 estimate_tokens 估算
   - 同时支持同步和异步（on_llm_end / on_llm_error 均有 async 版本）

2. LLMClient（具名返回对象）
    - get_user_llm() 的返回类型，包含 llm、usage 以及模型上限字段

3. LLMUsage（轻量句柄）
    - 随 ChatOpenAI 实例一同返回，携带用量查询方法
    - 精确到 user_id + model_id 维度，支持未来限额、限次、计费扩展
    - 用法：client = manager.get_user_llm(user_id)
              result = client.invoke(messages)
              usage = client.usage.get_usage_last_24h()

关于 streaming 参数
-------------------
⚠️ 不要向 get_user_llm() 传入 streaming 参数。
流式/非流式由调用方式决定，不由构造参数控制：
  - 非流式：llm.invoke() / llm.ainvoke()
  - 流式：  llm.stream() / llm.astream() / llm.astream_events()
streaming 参数（若传入）会被静默忽略，不会透传到底层 SDK。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult, ChatGenerationChunk

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from .models import UsageLogEntry, DEFAULT_MAX_CONTEXT_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS
from .credit_services import settle_usage_entry_credit
from .estimate_tokens import estimate_tokens
from .reasoning_compat import extract_reasoning_text_from_message, extract_text_content_from_message
from .usage_compat import extract_usage_from_llm_result, normalize_usage_payload


@dataclass(frozen=True)
class LLMClient:
    """
    get_user_llm() 的具名返回对象。

    属性：
    - llm: 已注入 UsageTrackingCallback 的 ChatOpenAI 实例
    - usage: 用量查询句柄（LLMUsage）
    - max_context_tokens: 当前模型上下文上限
    - max_output_tokens: 当前模型单次输出上限

    调用方式：
    - 默认当作 LLM 使用：client.invoke(...) / client.stream(...)
    - 查询用量走子对象：client.usage.get_usage_last_24h()
    """

    llm: Any
    usage: "LLMUsage"
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS

    def get_model_limits(self) -> Dict[str, int]:
        """返回当前模型的上下文与输出上限。"""
        return {
            "max_context_tokens": int(self.max_context_tokens),
            "max_output_tokens": int(self.max_output_tokens),
        }

    def __getattr__(self, name: str) -> Any:
        """将未知属性/方法透传给内部 llm，实现 get_user_llm(...).invoke() 直调。"""
        return getattr(self.llm, name)

    def __dir__(self):
        """合并 llm 的可见属性，便于 IDE 自动补全。"""
        return sorted(set(super().__dir__()) | set(dir(self.llm)))


class UsageTrackingCallback(BaseCallbackHandler):
    """
    LLM 用量追踪 Callback。

    通过 LangChain Callback 机制自动截获所有调用事件，
    无需自定义 BaseChatModel，自动覆盖全部 9 种调用方式。

    Token 统计优先级：
    1. API 返回的真实 usage 字段（标准 OpenAI 协议）
    2. 本地 estimate_tokens 估算（兜底，适用于国产模型/截断输出）

    性能说明（重要）：
    - 流式中断/失败的记账必须保留，因此用量写入设计为“后台线程异步落库”；
    - ``on_llm_end``（含异步版本）只做内存计算 + 任务投递，不做 DB 同步写入；
    - 同步调用路径（``invoke``）回调本身是同步的，只能做到“快速投递”，
      真正耗时的 SQLite 写入与点数结算发生在 ``usage_writer`` 后台单线程；
    - ``estimate_tokens`` 在有真实 usage 时完全不会触发，只有缺失 usage
      （国产中转/中断/失败）时才做本地估算，且默认只用 tiktoken 本地编码器，
      不触发任何 tokenizer 下载（除非宿主显式开启预热）。
    """

    def __init__(
        self,
        user_id: str,
        model_id: int,
        platform_id: int,
        model_name: str,
        platform_name: str,
        session_maker: sessionmaker,
        agent_name: Optional[str] = None,
        quota_scope: Optional[str] = None,
        billing_enabled: bool = False,
        usage_context_provider=None,
        usage_recorded_handler=None,
        usage_writer=None,
    ):
        super().__init__()
        self.user_id = user_id
        self.model_id = model_id
        self.platform_id = platform_id
        self.model_name = model_name
        self.platform_name = platform_name
        self.agent_name = agent_name
        self.quota_scope = quota_scope
        self.billing_enabled = bool(billing_enabled)
        self._session_maker = session_maker
        self._usage_context_provider = usage_context_provider
        self._usage_recorded_handler = usage_recorded_handler
        # 用量落库写入器：默认使用进程级后台单线程，保证“中断也记账”
        # 的同时不阻塞模型响应返回。宿主可注入自己的写入器（例如复用
        # 应用级线程池），但禁止在回调线程做同步 DB 写入之外的重操作。
        from .usage_writer import get_default_usage_writer

        self._usage_writer = usage_writer or get_default_usage_writer()
        self._usage_context_snapshot = None
        if usage_context_provider is not None:
            try:
                self._usage_context_snapshot = usage_context_provider()
            except Exception:
                # 回调创建阶段没有上下文时，仍允许后续调用阶段重新读取。
                self._usage_context_snapshot = None

        # 流式累积缓冲区（按 run_id 隔离，支持并发）
        self._stream_buffers: Dict[str, List[str]] = {}
        # 输入 token 缓存（按 run_id）
        self._prompt_tokens_cache: Dict[str, int] = {}
        # 输入 prompt 文本缓存（按 run_id，用于本地 token 兜底）
        self._prompt_text_cache: Dict[str, str] = {}
        # 流式响应的最终 usage 往往只挂在最后一个 chunk 上，按 run_id 暂存真实上游统计。
        self._stream_usage_cache: Dict[str, Dict[str, Any]] = {}

    # ==================== 内部工具方法 ====================

    def _messages_to_text(self, messages: List[BaseMessage]) -> str:
        """将消息列表转换为文本，用于估算 Token"""
        parts = []
        for msg in messages:
            content = msg.content
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
        return "\n".join(parts)

    @staticmethod
    def _clamp_cached_prompt_tokens(cached_prompt_tokens: int, prompt_tokens: int) -> int:
        """缓存命中 token 不能超过本次输入 token。"""
        cached = max(int(cached_prompt_tokens or 0), 0)
        prompt = max(int(prompt_tokens or 0), 0)
        return min(cached, prompt) if prompt > 0 else cached

    def _extract_token_usage(self, response: LLMResult) -> Optional[Dict[str, int]]:
        """
        尝试从 API 响应中提取真实 token 用量。
        优先读取 OpenAI 兼容协议中的真实 usage，并兼容各家缓存字段。
        
        注意：不尝试从 completion_tokens_details 等非通用扩展字段中提取推理 token，
        因为各家 API 对这些字段的返回格式不一致。推理 token 的估算已通过
        on_llm_new_token 中对 reasoning_content 文本的本地累积实现。
        
        返回 None 表示 API 未提供 usage，需要降级为本地估算。
        """
        normalized = extract_usage_from_llm_result(response)
        if normalized is not None:
            return normalized.to_dict()

        return None  # API 未返回 usage，触发本地估算

    @staticmethod
    def _usage_has_cache_stats(usage: Optional[Dict[str, Any]]) -> bool:
        """判断 usage 是否包含真实缓存统计字段。"""
        if not usage:
            return False
        return (
            int(usage.get("cached_prompt_tokens") or 0) > 0
            or usage.get("cache_miss_prompt_tokens") is not None
            or usage.get("cache_source") == "upstream"
        )

    def _select_best_api_usage(self, run_key: str, response: LLMResult) -> Optional[Dict[str, Any]]:
        """优先选择包含缓存统计的真实 usage，避免流式兜底覆盖更完整的最终结果。"""
        stream_usage = self._stream_usage_cache.pop(run_key, None)
        result_usage = self._extract_token_usage(response)

        if self._usage_has_cache_stats(result_usage):
            return result_usage
        if self._usage_has_cache_stats(stream_usage):
            return stream_usage
        return result_usage or stream_usage

    def _remember_stream_usage_from_chunk(self, run_key: str, chunk: Any) -> None:
        """从流式 chunk 中暂存真实上游 usage，避免 LangChain 聚合时丢扩展字段。"""
        if not chunk:
            return

        message = getattr(chunk, "message", None)
        if message is None:
            return

        response_metadata = getattr(message, "response_metadata", None) or {}
        if not isinstance(response_metadata, dict):
            return

        for key in ("usage", "token_usage", "usage_metadata"):
            raw_usage = response_metadata.get(key)
            normalized = normalize_usage_payload(raw_usage)
            if normalized is not None:
                self._stream_usage_cache[run_key] = normalized.to_dict()
                return

    def _extract_completion_text(self, response: LLMResult) -> str:
        """从响应中提取 completion 文本，用于本地估算"""
        parts = []
        for gen_list in response.generations:
            for gen in gen_list:
                # ChatGeneration
                msg = getattr(gen, "message", None)
                if msg is not None:
                    visible_text = extract_text_content_from_message(msg)
                    if visible_text:
                        parts.append(visible_text)

                    reasoning_text = extract_reasoning_text_from_message(msg)
                    if reasoning_text:
                        parts.append(reasoning_text)
                            
                    # tool_calls 也计入 completion
                    tool_calls = getattr(msg, "tool_calls", None)
                    if tool_calls:
                        try:
                            parts.append(json.dumps(tool_calls, ensure_ascii=False))
                        except Exception:
                            pass
                else:
                    # 普通 Generation（text）
                    text = getattr(gen, "text", "")
                    if text:
                        parts.append(text)
        return "\n".join(p for p in parts if p)

    def _record_usage(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        cached_prompt_tokens: int = 0,
        cache_miss_prompt_tokens: Optional[int] = None,
        usage_source: Optional[str] = None,
        cache_source: Optional[str] = None,
        success: bool = True,
    ) -> None:
        """把用量写入投递到后台线程，调用方永不阻塞。

        时序保证：``usage_recorded_handler`` 只在 DB 提交成功后由后台线程
        调用，因此宿主收到的永远是“已落库”事件，可直接用于实时推送；
        调用线程返回时落库可能尚未完成，需要强一致读的场景请直接查 DB。
        """
        if self._session_maker is None:
            return
        usage_context = None
        if self._usage_context_provider is not None:
            try:
                usage_context = self._usage_context_provider()
            except Exception:
                usage_context = None
        if not usage_context:
            usage_context = self._usage_context_snapshot

        snapshot = {
            "user_id": self.user_id,
            "model_id": self.model_id,
            "prompt_tokens": max(int(prompt_tokens or 0), 0),
            "completion_tokens": max(int(completion_tokens or 0), 0),
            "cached_prompt_tokens": max(int(cached_prompt_tokens or 0), 0),
            "cache_miss_prompt_tokens": (
                max(int(cache_miss_prompt_tokens), 0)
                if cache_miss_prompt_tokens is not None
                else None
            ),
            "usage_source": str(usage_source) if usage_source else None,
            "cache_source": str(cache_source) if cache_source else None,
            "success": bool(success),
            "agent_name": self.agent_name,
            # 快照优先级：调用时刻的 provider > 创建时刻的快照 > 显式传入。
            # 注意：调用方可在 snapshot 中预置 context_key（例如单测），
            # 此时不再用 provider 覆盖，保证可预测性。
            "context_key": None,
            "quota_scope": self.quota_scope,
        }
        if usage_context:
            snapshot["context_key"] = str(usage_context)

        def _do_record() -> Optional[Dict[str, Any]]:
            return self._write_usage_snapshot(snapshot)

        from .usage_writer import UsageWriteTask

        self._usage_writer.submit(UsageWriteTask(record=_do_record))

    def _write_usage_snapshot(self, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """在后台线程执行真正的 DB 写入 + 点数结算 + 宿主通知。"""
        total_tokens = int(snapshot["prompt_tokens"]) + int(snapshot["completion_tokens"])
        # 允许调用方预置 context_key（例如单测）；否则按快照链解析。
        context_key = snapshot.get("context_key")
        if not context_key:
            context_key = self._usage_context_snapshot
        with self._session_maker() as session:
            entry = UsageLogEntry(
                user_id=snapshot["user_id"],
                model_id=snapshot["model_id"],
                prompt_tokens=snapshot["prompt_tokens"],
                completion_tokens=snapshot["completion_tokens"],
                total_tokens=total_tokens,
                cached_prompt_tokens=snapshot["cached_prompt_tokens"],
                cache_miss_prompt_tokens=snapshot["cache_miss_prompt_tokens"],
                usage_source=snapshot["usage_source"],
                cache_source=snapshot["cache_source"],
                success=1 if snapshot["success"] else 0,
                agent_name=snapshot["agent_name"],
                context_key=str(context_key) if context_key else None,
                quota_scope=snapshot["quota_scope"],
            )
            session.add(entry)
            session.flush()
            settle_usage_entry_credit(session, entry, billing_enabled=self.billing_enabled)
            session.commit()
        summary = {
            "agent_name": snapshot["agent_name"] or "unknown",
            "model_name": self.model_name,
            "platform_name": self.platform_name,
            "prompt_tokens": snapshot["prompt_tokens"],
            "completion_tokens": snapshot["completion_tokens"],
            "total_tokens": total_tokens,
            "cached_prompt_tokens": snapshot["cached_prompt_tokens"],
            "cache_miss_prompt_tokens": snapshot["cache_miss_prompt_tokens"],
            "usage_source": snapshot["usage_source"],
            "cache_source": snapshot["cache_source"],
            "success": snapshot["success"],
            "context_key": str(context_key) if context_key else None,
        }
        if self._usage_recorded_handler is not None:
            try:
                self._usage_recorded_handler(dict(summary))
            except Exception:
                # 宿主通知属于观测能力，失败不能影响已经成功提交的用量与模型调用。
                pass
        return summary

    async def _arecord_usage(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        cached_prompt_tokens: int = 0,
        cache_miss_prompt_tokens: Optional[int] = None,
        usage_source: Optional[str] = None,
        cache_source: Optional[str] = None,
        success: bool = True,
    ) -> None:
        """异步上下文同样只投递后台任务，不在事件循环里做同步 DB 写入。"""
        # 后台写入器是线程安全的，事件循环线程只做 submit()（非阻塞）。
        self._record_usage(
            prompt_tokens,
            completion_tokens,
            cached_prompt_tokens,
            cache_miss_prompt_tokens,
            usage_source,
            cache_source,
            success,
        )

    # ==================== 同步 Callback 事件 ====================

    def on_chat_model_start(
        self,
        serialized: Dict[str, Any],
        messages: List[List[BaseMessage]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """调用开始：预估并缓存 prompt token 数"""
        all_messages = [msg for msg_list in messages for msg in msg_list]
        prompt_text = self._messages_to_text(all_messages)
        self._prompt_tokens_cache[str(run_id)] = estimate_tokens(prompt_text, self.model_name)
        self._prompt_text_cache[str(run_id)] = prompt_text
        self._stream_buffers[str(run_id)] = []

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """
        调用结束（invoke/batch/generate 路径）：记录用量。
        优先使用 API 返回的真实 usage，否则降级为本地估算。
        """
        run_key = str(run_id)
        prompt_tokens = self._prompt_tokens_cache.pop(run_key, 0)
        prompt_text = self._prompt_text_cache.pop(run_key, "")
        cached_prompt_tokens = 0
        cache_miss_prompt_tokens = None
        usage_source = None
        cache_source = None

        # 优先读取 API 真实 usage
        api_usage = self._select_best_api_usage(run_key, response)
        if api_usage:
            prompt_tokens = api_usage["prompt_tokens"] or prompt_tokens
            completion_tokens = api_usage["completion_tokens"]
            cached_prompt_tokens = int(api_usage.get("cached_prompt_tokens") or 0)
            cache_miss_prompt_tokens = api_usage.get("cache_miss_prompt_tokens")
            usage_source = api_usage.get("usage_source") or "upstream"
            cache_source = api_usage.get("cache_source")
            cached_prompt_tokens = self._clamp_cached_prompt_tokens(cached_prompt_tokens, prompt_tokens)
        else:
            # 降级：本地估算 completion
            completion_text = self._extract_completion_text(response)

            # 流式路径：completion 已在 on_llm_new_token 中累积
            stream_buf = self._stream_buffers.pop(run_key, [])
            if stream_buf:
                completion_text = "".join(stream_buf)

            completion_tokens = estimate_tokens(completion_text, self.model_name)
            usage_source = "estimated"

        self._stream_buffers.pop(run_key, None)
        self._stream_usage_cache.pop(run_key, None)
        self._record_usage(
            prompt_tokens,
            completion_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
            cache_miss_prompt_tokens=cache_miss_prompt_tokens,
            usage_source=usage_source,
            cache_source=cache_source,
            success=True,
        )

    def on_llm_new_token(
        self,
        token: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """流式路径：累积每个 token chunk，用于本地估算兜底（包含 reasoning）"""
        run_key = str(run_id)
        if run_key not in self._stream_buffers:
            self._stream_buffers[run_key] = []
            
        chunk = kwargs.get("chunk")
        reasoning_text = ""
        
        if chunk and hasattr(chunk, "message"):
            self._remember_stream_usage_from_chunk(run_key, chunk)
            msg = chunk.message
            reasoning_text = extract_reasoning_text_from_message(msg)

        if reasoning_text:
            self._stream_buffers[run_key].append(reasoning_text)
        elif token:
            self._stream_buffers[run_key].append(token)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """调用失败：记录失败用量（若已产生流式输出则按已输出内容估算 completion）"""
        run_key = str(run_id)
        prompt_tokens = self._prompt_tokens_cache.pop(run_key, 0)
        self._prompt_text_cache.pop(run_key, None)
        self._stream_usage_cache.pop(run_key, None)
        stream_buf = self._stream_buffers.pop(run_key, None) or []
        completion_tokens = 0
        if stream_buf:
            completion_text = "".join(stream_buf)
            completion_tokens = estimate_tokens(completion_text, self.model_name)
        self._record_usage(
            prompt_tokens,
            completion_tokens=completion_tokens,
            cached_prompt_tokens=0,
            success=False,
        )

    # ==================== 异步 Callback 事件（真异步，不阻塞事件循环）====================

    async def on_chat_model_start(  # type: ignore[override]
        self,
        serialized: Dict[str, Any],
        messages: List[List[BaseMessage]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """异步版本：调用开始，预估 prompt token"""
        all_messages = [msg for msg_list in messages for msg in msg_list]
        prompt_text = self._messages_to_text(all_messages)
        self._prompt_tokens_cache[str(run_id)] = estimate_tokens(prompt_text, self.model_name)
        self._prompt_text_cache[str(run_id)] = prompt_text
        self._stream_buffers[str(run_id)] = []

    async def on_llm_end(  # type: ignore[override]
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """异步版本：调用结束，记录用量"""
        run_key = str(run_id)
        prompt_tokens = self._prompt_tokens_cache.pop(run_key, 0)
        prompt_text = self._prompt_text_cache.pop(run_key, "")
        cached_prompt_tokens = 0
        cache_miss_prompt_tokens = None
        usage_source = None
        cache_source = None

        api_usage = self._select_best_api_usage(run_key, response)
        if api_usage:
            prompt_tokens = api_usage["prompt_tokens"] or prompt_tokens
            completion_tokens = api_usage["completion_tokens"]
            cached_prompt_tokens = int(api_usage.get("cached_prompt_tokens") or 0)
            cache_miss_prompt_tokens = api_usage.get("cache_miss_prompt_tokens")
            usage_source = api_usage.get("usage_source") or "upstream"
            cache_source = api_usage.get("cache_source")
            cached_prompt_tokens = self._clamp_cached_prompt_tokens(cached_prompt_tokens, prompt_tokens)
        else:
            stream_buf = self._stream_buffers.pop(run_key, [])
            if stream_buf:
                completion_text = "".join(stream_buf)
            else:
                completion_text = self._extract_completion_text(response)
            completion_tokens = estimate_tokens(completion_text, self.model_name)
            usage_source = "estimated"

        self._stream_buffers.pop(run_key, None)
        self._stream_usage_cache.pop(run_key, None)
        await self._arecord_usage(
            prompt_tokens,
            completion_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
            cache_miss_prompt_tokens=cache_miss_prompt_tokens,
            usage_source=usage_source,
            cache_source=cache_source,
            success=True,
        )

    async def on_llm_new_token(  # type: ignore[override]
        self,
        token: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """异步版本：流式 token 累积（包含 reasoning）"""
        run_key = str(run_id)
        if run_key not in self._stream_buffers:
            self._stream_buffers[run_key] = []
            
        chunk = kwargs.get("chunk")
        reasoning_text = ""
        
        if chunk and hasattr(chunk, "message"):
            self._remember_stream_usage_from_chunk(run_key, chunk)
            msg = chunk.message
            reasoning_text = extract_reasoning_text_from_message(msg)

        if reasoning_text:
            self._stream_buffers[run_key].append(reasoning_text)
        elif token:
            self._stream_buffers[run_key].append(token)

    async def on_llm_error(  # type: ignore[override]
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """异步版本：调用失败，记录失败用量（若有已输出 token 则按已输出估算）"""
        run_key = str(run_id)
        prompt_tokens = self._prompt_tokens_cache.pop(run_key, 0)
        self._prompt_text_cache.pop(run_key, None)
        self._stream_usage_cache.pop(run_key, None)
        stream_buf = self._stream_buffers.pop(run_key, None) or []
        completion_tokens = 0
        if stream_buf:
            completion_text = "".join(stream_buf)
            completion_tokens = estimate_tokens(completion_text, self.model_name)
        await self._arecord_usage(
            prompt_tokens,
            completion_tokens=completion_tokens,
            cached_prompt_tokens=0,
            success=False,
        )


class LLMUsage:
    """
    LLM 用量查询句柄。

    随 ChatOpenAI 实例一同由 get_user_llm() 返回，
    提供精确到 user_id + model_id 维度的用量查询接口，
    支持未来限额、限次、计费等扩展。

    用法：
        client = manager.get_user_llm(user_id)
        result = client.invoke(messages)
        usage = client.usage.get_usage_last_24h()
        print(f"Last 24h: {usage['total_tokens']} tokens, {usage['requests']} requests")
    """

    def __init__(
        self,
        user_id: str,
        model_id: int,
        platform_id: int,
        model_name: str,
        platform_name: str,
        session_maker: sessionmaker,
        agent_name: Optional[str] = None,
        quota_scope: Optional[str] = None,
    ):
        self.user_id = user_id
        self.model_id = model_id
        self.platform_id = platform_id
        self.model_name = model_name
        self.platform_name = platform_name
        self.agent_name = agent_name
        self.quota_scope = quota_scope
        self._session_maker = session_maker

    def get_usage_last_24h(self) -> Dict[str, Any]:
        """获取过去 24 小时的用量"""
        return self._get_usage_since(timedelta(hours=24))

    def get_usage_last_week(self) -> Dict[str, Any]:
        """获取过去 7 天的用量"""
        return self._get_usage_since(timedelta(days=7))

    def get_usage_last_month(self) -> Dict[str, Any]:
        """获取过去 30 天的用量"""
        return self._get_usage_since(timedelta(days=30))

    def get_usage_total(self) -> Dict[str, Any]:
        """获取所有时间的总用量"""
        return self._get_usage_since(None)

    def get_sys_paid_usage_last_24h(self) -> Dict[str, Any]:
        """获取过去 24 小时内消耗站长额度的用量"""
        return self._get_usage_since(timedelta(hours=24), quota_scope="sys_paid")

    def get_self_paid_usage_last_24h(self) -> Dict[str, Any]:
        """获取过去 24 小时内消耗用户自有密钥的用量"""
        return self._get_usage_since(timedelta(hours=24), quota_scope="self_paid")

    def get_sys_paid_usage_total(self) -> Dict[str, Any]:
        """获取所有时间内消耗站长额度的用量"""
        return self._get_usage_since(None, quota_scope="sys_paid")

    def get_self_paid_usage_total(self) -> Dict[str, Any]:
        """获取所有时间内消耗用户自有密钥的用量"""
        return self._get_usage_since(None, quota_scope="self_paid")

    def get_usage_by_range(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        quota_scope: Optional[str] = None,
    ) -> Dict[str, Any]:
        """获取指定时间范围的用量"""
        with self._session_maker() as session:
            query = session.query(
                func.coalesce(func.sum(UsageLogEntry.total_tokens), 0).label("total_tokens"),
                func.coalesce(func.sum(UsageLogEntry.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(UsageLogEntry.completion_tokens), 0).label("completion_tokens"),
                func.coalesce(func.sum(UsageLogEntry.cached_prompt_tokens), 0).label("cached_prompt_tokens"),
                func.coalesce(func.sum(UsageLogEntry.cache_miss_prompt_tokens), 0).label("cache_miss_prompt_tokens"),
                func.count(UsageLogEntry.id).label("requests"),
                func.coalesce(func.sum(1 - UsageLogEntry.success), 0).label("errors"),
            ).filter(
                UsageLogEntry.user_id == self.user_id,
                UsageLogEntry.model_id == self.model_id,
            )
            if start_time is not None:
                query = query.filter(UsageLogEntry.created_at >= start_time)
            if end_time is not None:
                query = query.filter(UsageLogEntry.created_at <= end_time)
            if quota_scope is not None:
                query = query.filter(UsageLogEntry.quota_scope == quota_scope)
            result = query.first()
            return self._format_result(result)

    def _get_usage_since(self, delta: Optional[timedelta], quota_scope: Optional[str] = None) -> Dict[str, Any]:
        """内部方法：查询指定时间范围的用量"""
        with self._session_maker() as session:
            query = session.query(
                func.coalesce(func.sum(UsageLogEntry.total_tokens), 0).label("total_tokens"),
                func.coalesce(func.sum(UsageLogEntry.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(UsageLogEntry.completion_tokens), 0).label("completion_tokens"),
                func.coalesce(func.sum(UsageLogEntry.cached_prompt_tokens), 0).label("cached_prompt_tokens"),
                func.coalesce(func.sum(UsageLogEntry.cache_miss_prompt_tokens), 0).label("cache_miss_prompt_tokens"),
                func.count(UsageLogEntry.id).label("requests"),
                func.coalesce(func.sum(1 - UsageLogEntry.success), 0).label("errors"),
            ).filter(
                UsageLogEntry.user_id == self.user_id,
                UsageLogEntry.model_id == self.model_id,
            )
            if delta is not None:
                cutoff = datetime.now(UTC) - delta
                query = query.filter(UsageLogEntry.created_at >= cutoff)
            if quota_scope is not None:
                query = query.filter(UsageLogEntry.quota_scope == quota_scope)
            result = query.first()
            return self._format_result(result)

    @staticmethod
    def _format_result(result) -> Dict[str, Any]:
        return {
            "total_tokens": int(result.total_tokens or 0),
            "prompt_tokens": int(result.prompt_tokens or 0),
            "completion_tokens": int(result.completion_tokens or 0),
            "cached_prompt_tokens": int(result.cached_prompt_tokens or 0),
            "cache_miss_prompt_tokens": int(result.cache_miss_prompt_tokens or 0),
            "requests": int(result.requests or 0),
            "errors": int(result.errors or 0),
        }
