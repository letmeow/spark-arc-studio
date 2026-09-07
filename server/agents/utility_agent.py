"""系统工具 Agent。

该模块承载不会直接面对用户的系统级任务，例如长上下文压缩。
它不注册到信标总线，也不参与导演委派；需要 LLM 的操作必须由调用方显式
传入当前 Agent 的客户端，不能在这里自行选择或切换模型。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from agents.agent_utils import load_prompt
from core.file_ingest.chunking import TokenTextSplitter
from llm.agen_matchbox.estimate_tokens import estimate_tokens


UTILITY_AGENT_ID = "agent_utility"


@dataclass(slots=True)
class ChatAttachmentPreparation:
    """聊天附件解析、切分与落盘后的结构化结果。

    ``is_oversized`` 表示全文超过当前模型窗口：附件照常切分落盘，
    但调用方不得预注入正文，只能走清单 + 滑窗按需读取。
    """

    parsed: Any
    chunks: list[Any]
    chunk_info: dict
    attachment_id: str
    chunk_count: int
    total_tokens_estimated: int
    is_partial: bool
    is_oversized: bool = False


class AttachmentContextWindowExceededError(ValueError):
    """附件全文 token 数超过当前模型上下文窗口。

    保留该异常类型仅为兼容历史调用方；当前策略不再拒绝落盘，
    ``prepare_chat_attachment`` 改为标记 ``is_oversized`` 并照常返回。
    """

    def __init__(self, *, total_tokens: int, max_context_tokens: int):
        self.total_tokens = max(int(total_tokens or 0), 0)
        self.max_context_tokens = max(int(max_context_tokens or 0), 0)
        super().__init__(
            f"附件约 {self.total_tokens} tokens，超过当前模型 {self.max_context_tokens} tokens 的上下文窗口"
        )


class ContextCompressionSourceRequiredError(ValueError):
    """上下文压缩没有收到调用方当前 Agent 的 LLM。"""

    def __init__(self) -> None:
        super().__init__("上下文压缩必须复用调用方当前 Agent 的 LLM，禁止切换到其他 Agent。")


class UtilityAgent:
    """系统内部工具 Agent。"""

    agent_id = UTILITY_AGENT_ID

    def __init__(self, user_id: str, project_name: str | None = None):
        self.user_id = str(user_id)
        self.project_name = project_name or ""

    @staticmethod
    def _json_dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    @staticmethod
    def _bind_tools_for_compaction(llm: Any, tools: Sequence[Any]) -> Any:
        """保留工具 schema 以复用缓存前缀，同时关闭压缩请求的工具选择。"""
        bind_tools = getattr(llm, "bind_tools", None)
        if not callable(bind_tools):
            return llm
        try:
            return bind_tools(list(tools), tool_choice="none")
        except (TypeError, ValueError):
            # 兼容旧网关/测试替身；即使网关不接受 none，响应仍会在下方拒绝工具调用。
            return bind_tools(list(tools))

    @staticmethod
    def _parse_json_object(raw: Any) -> Dict[str, Any]:
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
        try:
            value = json.loads(text)
            if isinstance(value, dict):
                return value
        except Exception:
            pass
        return {
            "summary": text[:4000],
            "user_intent_anchors": [],
            "creative_state": {},
            "author_preferences": {},
            "current_progress": [],
            "important_facts": [],
            "decisions": [],
            "rejected_options": [],
            "conflicts_and_uncertainties": [],
            "open_tasks": [],
            "recent_turns": [],
            "tool_results": [],
            "retrieval_anchors": [],
            "handoff_notes": ["压缩模型未返回严格 JSON，已保留原始摘要文本。"],
        }

    def _compress_once(
        self,
        *,
        history_text: str,
        agent_id: str,
        model_name: str,
        target_tokens: int,
        current_user_message: str,
        agent_profile: str = "",
        prefix_messages: Sequence[BaseMessage] | None = None,
        llm_client: Any = None,
        tools: Sequence[Any] | None = None,
    ) -> Dict[str, Any]:
        prompt = load_prompt(
            "utility",
            "compress_context",
            agent_id=agent_id,
            model_name=model_name or "unknown",
            target_tokens=str(max(int(target_tokens or 0), 1)),
            current_user_message=current_user_message or "",
            history_text=history_text or "",
            agent_profile=agent_profile or "按通用创作协作上下文处理。",
        )
        if llm_client is None:
            raise ContextCompressionSourceRequiredError()
        llm = llm_client
        if tools:
            llm = self._bind_tools_for_compaction(llm, tools)
        if prefix_messages:
            # 压缩指令放在原始前缀之后，避免在稳定 system 与历史之间插入新角色。
            messages = [
                *list(prefix_messages),
                HumanMessage(content=(
                    f"{prompt.get('system', '')}\n\n{prompt.get('user', '')}"
                )),
            ]
        else:
            messages = [
                SystemMessage(content=prompt.get("system", "")),
                HumanMessage(content=prompt.get("user", "")),
            ]
        response = llm.invoke(messages)
        if getattr(response, "tool_calls", None):
            raise ValueError("上下文压缩模型返回了工具调用，已拒绝执行")
        content = getattr(response, "content", response)
        return self._parse_json_object(content)

    @staticmethod
    def _summary_token_count(
        summary: Dict[str, Any],
        *,
        model_name: str,
    ) -> int:
        return int(estimate_tokens(
            json.dumps(summary, ensure_ascii=False),
            model=model_name or None,
        ))

    def _enforce_summary_budget(
        self,
        *,
        summary: Dict[str, Any],
        agent_id: str,
        model_name: str,
        target_tokens: int,
        current_user_message: str,
        agent_profile: str = "",
        llm_client: Any,
        tools: Sequence[Any] | None = None,
        prefix_messages: Sequence[BaseMessage] | None = None,
    ) -> Dict[str, Any]:
        """超出目标时再做一次结构化收敛，仍超限则显式失败。"""
        safe_target = max(int(target_tokens or 0), 256)
        if self._summary_token_count(summary, model_name=model_name) <= safe_target:
            return summary

        compacted_again = self._compress_once(
            history_text=self._json_dumps(summary),
            agent_id=agent_id,
            model_name=model_name,
            target_tokens=safe_target,
            current_user_message=current_user_message,
            agent_profile=agent_profile,
            prefix_messages=prefix_messages,
            llm_client=llm_client,
            tools=tools,
        )
        actual_tokens = self._summary_token_count(
            compacted_again,
            model_name=model_name,
        )
        if actual_tokens > safe_target:
            raise ValueError(
                f"上下文摘要超过目标预算：{actual_tokens} > {safe_target} tokens"
            )
        return compacted_again

    def compress_chat_history(
        self,
        *,
        history_items: List[Dict[str, Any]],
        agent_id: str,
        model_name: str,
        target_tokens: int = 8000,
        current_user_message: str = "",
        agent_profile: str = "",
        source_prefix_messages: Sequence[BaseMessage] | None = None,
        source_llm_client: Any = None,
        source_tools: Sequence[Any] | None = None,
    ) -> Dict[str, Any]:
        """压缩聊天历史，必要时先分块摘要再合并。"""
        if source_llm_client is None:
            raise ContextCompressionSourceRequiredError()

        material = self._json_dumps(history_items)
        # 压缩器始终复用调用方当前 Agent 的客户端；不能因为工具绑定能力差异而
        # 静默切换到另一个 Agent 的模型。
        llm = source_llm_client
        source_model_name = str(getattr(getattr(llm, "usage", None), "model_name", "") or model_name or "")
        max_context = int(getattr(llm, "max_context_tokens", 0) or 100000)
        safe_target = max(int(target_tokens or 0), 256)
        safety_tokens = max(2000, int(max_context * 0.05))
        max_input_tokens = max(4000, max_context - safe_target - safety_tokens)

        prefix_messages = list(source_prefix_messages or [])
        stable_prefix_messages = (
            [prefix_messages[0]]
            if prefix_messages and getattr(prefix_messages[0], "type", "") == "system"
            else []
        )
        prefix_text = "\n\n".join(
            f"{getattr(message, 'type', '')}: {getattr(message, 'content', '')}"
            for message in prefix_messages
        )
        prefix_tokens = estimate_tokens(prefix_text, model=source_model_name or model_name) if prefix_messages else 0

        if prefix_messages and prefix_tokens <= max_input_tokens:
            summary = self._compress_once(
                history_text="（待压缩历史已作为本请求前缀中的原始消息提供，请以这些原始消息为准。）",
                agent_id=agent_id,
                model_name=model_name,
                target_tokens=safe_target,
                current_user_message=current_user_message,
                agent_profile=agent_profile,
                prefix_messages=prefix_messages,
                llm_client=llm,
                tools=source_tools,
            )
            return self._enforce_summary_budget(
                summary=summary,
                agent_id=agent_id,
                model_name=source_model_name or model_name,
                target_tokens=safe_target,
                current_user_message=current_user_message,
                agent_profile=agent_profile,
                llm_client=llm,
                tools=source_tools,
                prefix_messages=stable_prefix_messages,
            )

        if estimate_tokens(material, model=source_model_name or model_name) <= max_input_tokens:
            summary = self._compress_once(
                history_text=material,
                agent_id=agent_id,
                model_name=model_name,
                target_tokens=target_tokens,
                current_user_message=current_user_message,
                agent_profile=agent_profile,
                llm_client=llm,
            )
            return self._enforce_summary_budget(
                summary=summary,
                agent_id=agent_id,
                model_name=source_model_name or model_name,
                target_tokens=target_tokens,
                current_user_message=current_user_message,
                agent_profile=agent_profile,
                llm_client=llm,
                tools=source_tools,
                prefix_messages=stable_prefix_messages,
            )

        splitter = TokenTextSplitter(
            chunk_tokens=max_input_tokens,
            min_tokens=1000,
            max_tokens=max(2000, max_input_tokens),
            estimate_model=source_model_name or model_name or None,
        )
        partials: list[Dict[str, Any]] = []
        for chunk in splitter.split(material):
            partials.append(self._compress_once(
                history_text=chunk.text,
                agent_id=agent_id,
                model_name=model_name,
                target_tokens=max(1200, int(target_tokens / 2)),
                current_user_message=current_user_message,
                agent_profile=agent_profile,
                llm_client=llm,
            ))

        summary = self._compress_once(
            history_text=self._json_dumps(partials),
            agent_id=agent_id,
            model_name=model_name,
            target_tokens=target_tokens,
            current_user_message=current_user_message,
            agent_profile=agent_profile,
            llm_client=llm,
        )
        return self._enforce_summary_budget(
            summary=summary,
            agent_id=agent_id,
            model_name=source_model_name or model_name,
            target_tokens=target_tokens,
            current_user_message=current_user_message,
            agent_profile=agent_profile,
            llm_client=llm,
            tools=source_tools,
            prefix_messages=stable_prefix_messages,
        )

    @staticmethod
    def prepare_chat_attachment(
        *,
        user_id: str,
        project_name: str,
        file_path: str,
        filename: str,
        chunk_tokens: int,
        estimate_model: str | None = None,
        max_context_tokens: int | None = None,
    ) -> ChatAttachmentPreparation:
        """聊天附件解析、Token 切分与附件缓存落盘的统一 facade。"""
        from agents.attachment import save_attachment
        from core.file_ingest.service import parse_uploaded_file

        parsed = parse_uploaded_file(file_path, filename, estimate_model)
        normalized_context_limit = max(int(max_context_tokens or 0), 0)

        # 同口径装箱：切分器是“≤ chunk_tokens 即合法”的装箱，窗口会顶着上限
        # 装满；读窗侧用同一口径（estimate_model）实测兜底。口径一致时装箱
        # 承诺与读窗上限是同一把尺子，不存在系统性误杀（国家意志 e2e 的根因
        # 恰是跨口径比较：切时无模型回退 64K、读时无模型回退 100K+）。
        # 模型窗口只决定 is_oversized 清单降级，不参与切分窗口计算。
        splitter = TokenTextSplitter(
            chunk_tokens=chunk_tokens,
            tail_merge_threshold_ratio=0.5,
            tail_merge_cap_ratio=1.5,
            estimate_model=estimate_model,
        )
        chunks, chunk_info = splitter.split_with_info(parsed.full_text)
        # 全程使用切分器口径（pack 累加），不混用整体一次性估算：
        # 两者在部分 tokenizer 下存在系统性偏差，混用会导致单片超窗。
        total_tokens_estimated = int(chunk_info.get("total_tokens_estimated") or 0)
        # 超窗不再拒绝落盘：照常切分并落盘，仅标记 oversized。
        # 调用方（chat_attachment 注入、前端 toast）看到该标记后走
        # “清单 + 滑窗按需读取”，不预注入任何正文。
        is_oversized = bool(
            normalized_context_limit
            and total_tokens_estimated > normalized_context_limit
        )
        from core.project_settings import CHAT_ATTACHMENT_DIRECT_INJECTION_MAX_TOKENS

        is_partial = (
            total_tokens_estimated > CHAT_ATTACHMENT_DIRECT_INJECTION_MAX_TOKENS
            or is_oversized
        )
        meta = save_attachment(
            user_id=user_id,
            project_name=project_name,
            filename=parsed.filename or filename,
            source_format=parsed.source_format,
            full_text=parsed.full_text,
            chunks=[c.text for c in chunks],
            total_tokens=total_tokens_estimated,
            estimate_model=estimate_model,
        )
        return ChatAttachmentPreparation(
            parsed=parsed,
            chunks=chunks,
            chunk_info=chunk_info,
            attachment_id=meta.attachment_id,
            chunk_count=len(chunks),
            total_tokens_estimated=total_tokens_estimated,
            is_partial=is_partial,
            is_oversized=is_oversized,
        )
