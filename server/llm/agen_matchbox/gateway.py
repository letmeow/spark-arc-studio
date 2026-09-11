"""轻量 LLM 客户端网关，不依赖 manager/数据库。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Dict, Mapping, Optional

from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from .env_utils import get_env_var
from .reasoning_compat import (
    extract_metadata_reasoning_text_from_message,
    extract_reasoning_text_from_chat_delta,
    extract_reasoning_text_from_message,
)
from .request_headers import build_upstream_request_headers
from .tool_protocol import validate_tool_message_history


def _prompt_cache_agent_name(callbacks: Any) -> str:
    for callback in list(callbacks or []):
        agent_name = str(getattr(callback, "agent_name", "") or "").strip()
        if agent_name:
            return agent_name
    return ""


def build_prompt_cache_routing_key(
    llm: Any,
    *,
    context_reader: Any = None,
) -> str | None:
    """为支持改进匹配的 OpenAI 模型生成隔离、稳定且不泄露身份的路由键。

    ``context_reader`` 是宿主注入的上下文读取器，需提供 ``user_id``、
    ``project_name``、``chat_session`` 三个无参可调用属性（缺失时按空处理）。
    未注入时返回 ``None``，网关不会尝试导入任何宿主模块。
    """
    model_name = str(
        getattr(llm, "model_name", "")
        or getattr(llm, "model", "")
        or ""
    ).strip().lower()
    if not model_name.startswith("gpt-5.6"):
        return None

    try:
        from . import integrations as integrations_module

        reader = context_reader
        if reader is None:
            provider = integrations_module.get_prompt_cache_context_reader()
            reader = provider() if callable(provider) else None
        if reader is None:
            return None

        user_id = str(reader.user_id() or "").strip() if hasattr(reader, "user_id") else ""
        project_name = str(reader.project_name() or "").strip() if hasattr(reader, "project_name") else ""
        session = reader.chat_session() if hasattr(reader, "chat_session") else (None, None)
        room_agent_id, context_key = session if isinstance(session, (tuple, list)) else (None, None)
    except Exception:
        return None

    agent_name = _prompt_cache_agent_name(getattr(llm, "callbacks", None))
    if not user_id or not agent_name:
        return None

    identity = json.dumps(
        {
            "version": 1,
            "user_id": user_id,
            "project_name": project_name,
            "room_agent_id": room_agent_id or "",
            "context_key": context_key or "",
            "agent_name": agent_name,
            "model_name": model_name,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:40]
    key_prefix = get_env_var("AGENT_MATCHBOX_PROMPT_CACHE_KEY_PREFIX", "matchbox:v1") or "matchbox:v1"
    return f"{key_prefix}:{digest}"


def _normalize_openai_compat_json_schema(schema: Any) -> Any:
    """递归收敛工具 Schema，并补齐对象的 ``required`` 数组。

    ``examples`` 是标准 JSON Schema 注释，但不属于 Gemini
    ``FunctionDeclaration.parameters`` 接受的 Schema 字段。部分 OpenAI 兼容
    代理会把工具声明直接转换成 Gemini Schema，因而会让整个请求在生成前以
    ``INVALID_ARGUMENT`` 失败。单数 ``example`` 属于 Gemini Schema，继续保留。
    """
    if not isinstance(schema, dict):
        return deepcopy(schema)

    normalized = {}
    schema_maps = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
    schema_values = {"items", "contains", "additionalProperties", "propertyNames", "not", "if", "then", "else"}
    schema_lists = {"anyOf", "oneOf", "allOf", "prefixItems"}
    for key, value in schema.items():
        if key == "examples":
            continue
        if key in schema_maps and isinstance(value, dict):
            # 映射键是参数名或定义名，必须原样保留，即使恰好叫 examples。
            normalized[key] = {
                name: _normalize_openai_compat_json_schema(child)
                for name, child in value.items()
            }
        elif key in schema_values and isinstance(value, dict):
            normalized[key] = _normalize_openai_compat_json_schema(value)
        elif key in schema_lists and isinstance(value, list):
            normalized[key] = [
                _normalize_openai_compat_json_schema(child)
                for child in value
            ]
        else:
            normalized[key] = deepcopy(value)
    is_object_schema = (
        normalized.get("type") == "object"
        or isinstance(normalized.get("properties"), dict)
    )
    if is_object_schema and normalized.get("required") is None:
        normalized["required"] = []
    return normalized


def normalize_openai_tool_schemas(tools: Any) -> Any:
    """将函数工具参数规范化为严格提供商也接受的合法 JSON Schema。

    OpenAI 兼容实现通常允许对象 Schema 省略 ``required``，但部分提供商会把
    缺失值按 ``null`` 校验并拒绝请求。空数组仍属于标准 JSON Schema，因此可在
    统一协议层安全补齐。纯注释字段 ``examples`` 会被部分 Gemini 代理当作未知
    字段拒绝，统一移除也不会改变参数校验语义。这里不按模型名称或端点域名分支。
    """
    if not isinstance(tools, list):
        return tools

    normalized_tools = []
    for tool in tools:
        if not isinstance(tool, dict):
            normalized_tools.append(tool)
            continue

        normalized_tool = dict(tool)
        function = tool.get("function")
        if isinstance(function, dict):
            normalized_function = dict(function)
            parameters = function.get("parameters")
            if isinstance(parameters, dict):
                normalized_function["parameters"] = _normalize_openai_compat_json_schema(parameters)
            normalized_tool["function"] = normalized_function
        normalized_tools.append(normalized_tool)
    return normalized_tools


def _env_flag_enabled(name: str, default: bool) -> bool:
    """读取布尔环境变量，支持 1/0、true/false、yes/no、on/off。"""
    raw = get_env_var(name)
    if raw is None:
        return default

    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def build_sdk_compat_headers(
    existing_headers: Optional[Mapping[str, str]] = None,
) -> Optional[Dict[str, str]]:
    """为 OpenAI 兼容网关构建请求头。"""
    headers = build_upstream_request_headers(existing_headers)

    if not _env_flag_enabled("AGENT_MATCHBOX_OPENAI_COMPAT_OVERRIDE_UA", default=True):
        return headers or None

    for key in headers.keys():
        if str(key).lower() == "user-agent":
            return headers or None

    compat_ua = get_env_var("AGENT_MATCHBOX_OPENAI_COMPAT_USER_AGENT", "Agent-Matchbox/1.0")
    compat_ua = (compat_ua or "Agent-Matchbox/1.0").strip() or "Agent-Matchbox/1.0"
    headers["User-Agent"] = compat_ua
    return headers


def apply_sdk_request_compat(kwargs: Dict[str, Any], *, include_stream_usage: bool = True) -> Dict[str, Any]:
    """统一注入 SDK 兼容参数。

    Args:
        kwargs: 待注入的 SDK 参数。
        include_stream_usage: 是否注入 ``stream_usage``。
            Embedding 接口（如 ``OpenAIEmbeddings.embed_documents``）不支持该参数，
            需要显式传 ``False`` 避免 ``Embeddings.create()`` 报错。
    """
    compat_headers = build_sdk_compat_headers(kwargs.get("default_headers"))
    if compat_headers is not None:
        kwargs["default_headers"] = compat_headers
    if include_stream_usage:
        stream_usage_mode = str(get_env_var("AGENT_MATCHBOX_OPENAI_COMPAT_STREAM_USAGE", "auto") or "auto").strip().lower()
        if stream_usage_mode in {"1", "true", "yes", "on", "auto"}:
            kwargs.setdefault("stream_usage", True)
    return kwargs


class ChatUniversal(ChatOpenAI):
    """
    ChatOpenAI 子类：尽量保留各类 OpenAI 兼容网关返回的 reasoning 文本。
    
    背景：
        LangChain 1.x 的 ChatOpenAI 对 OpenAI 官方 content blocks 支持较好，
        但对很多“OpenAI 兼容”网关附加在 delta 里的非标准 reasoning 字段
        （如 `reasoning_content`、`reasoning`、`analysis`、`thinking`）会直接丢弃。
    
    方案：
        覆盖 _convert_chunk_to_generation_chunk 方法，在父类处理完毕后检查原始 delta
        中是否包含上述非标准 reasoning 字段。如有则统一注入到
        `AIMessageChunk.additional_kwargs["reasoning_content"]`。

        这样上层业务与用量统计都只依赖一个统一入口，无需关心不同中转站的命名差异。
    
    稳定性：
        相比 monkey-patch（运行时替换模块级函数），子类继承更稳健：
        - 不修改 LangChain 的任何源码
        - 如果 LangChain 升级重命名了方法，Python 会正常报错而非静默失效
        - _convert_chunk_to_generation_chunk 是实例方法，LangChain 不太可能在 1.x 内改名
    """

    def _get_request_payload(
        self,
        input_,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "prompt_cache_key" not in payload:
            prompt_cache_key = build_prompt_cache_routing_key(self)
            if prompt_cache_key:
                payload["prompt_cache_key"] = prompt_cache_key
        if "tools" in payload:
            payload["tools"] = normalize_openai_tool_schemas(payload.get("tools"))

        payload_messages = payload.get("messages")
        if not isinstance(payload_messages, list):
            return payload

        source_messages = self._convert_input(input_).to_messages()
        for source_message, payload_message in zip(source_messages, payload_messages):
            if not isinstance(payload_message, dict):
                continue
            if payload_message.get("role") != "assistant":
                continue

            reasoning = extract_metadata_reasoning_text_from_message(source_message)
            if reasoning:
                payload_message["reasoning_content"] = reasoning

        validate_tool_message_history(payload_messages)

        return payload

    def _create_chat_result(self, response, generation_info: dict | None = None):
        result = super()._create_chat_result(response, generation_info=generation_info)
        response_dict = response if isinstance(response, dict) else response.model_dump()
        raw_usage = response_dict.get("usage")
        if raw_usage:
            llm_output = dict(result.llm_output or {})
            llm_output["usage"] = raw_usage
            result.llm_output = llm_output
        choices = response_dict.get("choices") or []

        for generation, raw_choice in zip(result.generations, choices):
            raw_message = raw_choice.get("message") if isinstance(raw_choice, dict) else None
            reasoning = extract_reasoning_text_from_message(raw_message)
            if reasoning and hasattr(generation.message, "additional_kwargs"):
                generation.message.additional_kwargs["reasoning_content"] = reasoning

        return result

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        result = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if result is None:
            return None

        raw_usage = chunk.get("usage")
        if raw_usage and hasattr(result.message, "response_metadata"):
            result.message.response_metadata["usage"] = raw_usage

        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
        if choices:
            delta = choices[0].get("delta") or {}
            reasoning = extract_reasoning_text_from_chat_delta(delta)
            if reasoning and isinstance(reasoning, str):
                msg = result.message
                if hasattr(msg, "additional_kwargs"):
                    msg.additional_kwargs["reasoning_content"] = reasoning

        return result


def create_quick_llm(
    *,
    base_url: str,
    api_key: str,
    model_name: str,
    **kwargs: Any,
) -> ChatUniversal:
    """创建轻量 Chat 客户端，不触发 AIManager/数据库逻辑。"""
    payload = dict(kwargs)
    payload.pop("streaming", None)
    payload = apply_sdk_request_compat(payload)
    return ChatUniversal(
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        **payload,
    )


def create_quick_embedding(
    *,
    base_url: str,
    api_key: str,
    model_name: str,
    **kwargs: Any,
) -> OpenAIEmbeddings:
    """创建轻量 Embedding 客户端，不触发 AIManager/数据库逻辑。"""
    payload = dict(kwargs)
    payload = apply_sdk_request_compat(payload, include_stream_usage=False)
    return OpenAIEmbeddings(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        check_embedding_ctx_length=False,
        **payload,
    )
