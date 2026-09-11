"""Matchbox 与宿主应用之间的可选集成回调。

Matchbox 的默认运行路径不需要这些回调。服务型宿主可以按需注入业务默认用途、
请求身份、用量上下文和外部密钥轮换处理器，而无需让 Matchbox 导入宿主模块。

``secret_rotation_handler`` 在 Matchbox 已经扫描通用密钥、但尚未提交事务时
被调用。处理器可以通过回调登记自己的密钥迁移任务，从而与通用密钥迁移保持
同一事务边界。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


DefaultUsageKeyResolver = Callable[[Optional[str]], str]
CallerContextProvider = Callable[[], tuple[Optional[str], bool]]
UsageContextProvider = Callable[[], Optional[str]]
UsageRecordedHandler = Callable[[dict[str, Any]], None]
SecretRotationHandler = Callable[..., None]
# 提示词缓存上下文读取器：宿主按需返回 ``user_id`` / ``project_name`` /
# ``chat_session`` 三个无参可调用对象；网关只读快照，不持有宿主模块引用。
PromptCacheContextReader = Callable[[], Any]


@dataclass(frozen=True)
class MatchboxIntegrations:

    default_usage_key_resolver: Optional[DefaultUsageKeyResolver] = None
    caller_context_provider: Optional[CallerContextProvider] = None
    usage_context_provider: Optional[UsageContextProvider] = None
    usage_recorded_handler: Optional[UsageRecordedHandler] = None
    secret_rotation_handler: Optional[SecretRotationHandler] = None
    prompt_cache_context_reader: Optional[PromptCacheContextReader] = None


# 网关注入点：宿主通过 MatchboxIntegrations 传入读取器后，网关层
# ``build_prompt_cache_routing_key`` 可在不导入宿主模块的情况下读取
# 请求上下文。模块级变量只保存“如何读取”，不保存宿主对象本身。
_prompt_cache_context_reader: Optional[PromptCacheContextReader] = None


def set_prompt_cache_context_reader(reader: Optional[PromptCacheContextReader]) -> None:
    """注册/清空提示词缓存上下文读取器（供 AIManager 初始化调用）。"""
    global _prompt_cache_context_reader
    _prompt_cache_context_reader = reader


def get_prompt_cache_context_reader() -> Optional[PromptCacheContextReader]:
    """返回当前注册的提示词缓存上下文读取器（可为 None）。"""
    return _prompt_cache_context_reader
