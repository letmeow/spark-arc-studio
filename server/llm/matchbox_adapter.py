"""SparkArc 对 Agent Matchbox 的宿主适配层。

Matchbox 不知道 SparkArc 的 Agent、请求上下文和搜索业务；本模块负责把这些
项目语义注入通用组件，并确保 SparkArc 继续使用既有迁移与数据库路径。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


_adapter_defaulted_env_names: set[str] = set()
_LEGACY_ENV_MAP = {
    "SPARKARC_SKIP_LLM_MANAGER": "AGENT_MATCHBOX_DISABLED",
    "SPARKARC_OPENAI_COMPAT_OVERRIDE_UA": "AGENT_MATCHBOX_OPENAI_COMPAT_OVERRIDE_UA",
    "SPARKARC_OPENAI_COMPAT_USER_AGENT": "AGENT_MATCHBOX_OPENAI_COMPAT_USER_AGENT",
    "SPARKARC_OPENAI_COMPAT_STREAM_USAGE": "AGENT_MATCHBOX_OPENAI_COMPAT_STREAM_USAGE",
    "SPARKARC_UPSTREAM_HANDSHAKE_HEADER": "AGENT_MATCHBOX_UPSTREAM_HANDSHAKE_HEADER",
    "SPARKARC_UPSTREAM_HANDSHAKE_VALUE": "AGENT_MATCHBOX_UPSTREAM_HANDSHAKE_VALUE",
}


def configure_sparkarc_matchbox_environment() -> Path:
    """设置 SparkArc 的 Matchbox 运行目录，并兼容历史环境变量。"""
    component_home = Path(__file__).resolve().parent / "agen_matchbox"
    from llm.agen_matchbox.paths import set_default_mgr_home

    set_default_mgr_home(component_home)

    for legacy_name, matchbox_name in _LEGACY_ENV_MAP.items():
        if matchbox_name not in os.environ and legacy_name in os.environ:
            os.environ[matchbox_name] = os.environ[legacy_name]

    if "AGENT_MATCHBOX_DISABLED" not in os.environ and any(
        "alembic" in argument or "gen_migration.py" in argument
        for argument in sys.argv
    ):
        os.environ["AGENT_MATCHBOX_DISABLED"] = "1"

    # 保持 SparkArc 现有的请求头标识；独立 Matchbox 默认不注入任何标识。
    user_agent_name = "AGENT_MATCHBOX_OPENAI_COMPAT_USER_AGENT"
    if user_agent_name not in os.environ:
        os.environ[user_agent_name] = "SparkArc/1.0"
        _adapter_defaulted_env_names.add(user_agent_name)
    handshake_header = "AGENT_MATCHBOX_UPSTREAM_HANDSHAKE_HEADER"
    handshake_value = "AGENT_MATCHBOX_UPSTREAM_HANDSHAKE_VALUE"
    if handshake_header not in os.environ:
        os.environ[handshake_header] = "X-SparkArc-Client"
        _adapter_defaulted_env_names.add(handshake_header)
    if handshake_value not in os.environ:
        os.environ[handshake_value] = "sparkarc"
        _adapter_defaulted_env_names.add(handshake_value)
    cache_prefix = "AGENT_MATCHBOX_PROMPT_CACHE_KEY_PREFIX"
    if cache_prefix not in os.environ:
        os.environ[cache_prefix] = "sparkarc:v1"
        _adapter_defaulted_env_names.add(cache_prefix)
    return component_home


def _apply_legacy_env_file_aliases() -> None:
    """把 Matchbox 旧 `.env` 中的 SparkArc 变量映射到通用变量。"""
    from llm.agen_matchbox.env_utils import get_env_file_var

    for legacy_name, matchbox_name in _LEGACY_ENV_MAP.items():
        if get_env_file_var(matchbox_name):
            continue
        legacy_value = get_env_file_var(legacy_name)
        if not legacy_value:
            continue
        if matchbox_name not in os.environ or matchbox_name in _adapter_defaulted_env_names:
            os.environ[matchbox_name] = legacy_value
            _adapter_defaulted_env_names.discard(matchbox_name)


def _default_usage_key(agent_name: Optional[str]) -> str:
    """返回 SparkArc 各类 Agent 的默认用途槽位。"""
    if agent_name in {"agent_story_memory"}:
        return "fast"
    return "reason" if agent_name == "agent_director" else "main"


def _caller_context() -> tuple[Optional[str], bool]:
    """从 SparkArc 请求上下文读取调用者身份。"""
    from core.request_context import current_user_id, current_user_is_admin

    caller_user_id = current_user_id.get()
    return (
        str(caller_user_id) if caller_user_id is not None else None,
        bool(current_user_is_admin.get()),
    )


def _usage_context() -> Optional[str]:
    """读取 SparkArc 聊天任务的用量归属标记。"""
    from core.request_context import current_llm_usage_context

    return current_llm_usage_context.get()


def _usage_recorded(payload: dict) -> None:
    """把已提交的单次用量通知交给当前 SparkArc 请求。"""
    from core.request_context import current_llm_usage_reporter

    reporter = current_llm_usage_reporter.get()
    if reporter is not None:
        reporter(dict(payload))


def _prompt_cache_context():
    """为网关提供提示词缓存路由键所需的请求上下文快照。"""
    from core.request_context import (
        current_user_id,
        get_current_chat_session,
        get_current_project_name,
    )

    class _Reader:
        @staticmethod
        def user_id():
            return current_user_id.get()

        @staticmethod
        def project_name():
            return get_current_project_name()

        @staticmethod
        def chat_session():
            return get_current_chat_session()

    return _Reader()


def build_sparkarc_matchbox_integrations():
    """构造 SparkArc 的 Matchbox 注入配置。"""
    configure_sparkarc_matchbox_environment()
    _apply_legacy_env_file_aliases()

    from core.db_engine import create_configured_engine
    from core.migration_specs import get_database_url
    from core.search_provider_settings import matchbox_secret_rotation_handler
    from llm.agen_matchbox.integrations import MatchboxIntegrations

    return MatchboxIntegrations(
        default_usage_key_resolver=_default_usage_key,
        caller_context_provider=_caller_context,
        usage_context_provider=_usage_context,
        usage_recorded_handler=_usage_recorded,
        secret_rotation_handler=matchbox_secret_rotation_handler,
        prompt_cache_context_reader=_prompt_cache_context,
    ), create_configured_engine, get_database_url("llm")


def prepare_sparkarc_matchbox_gui(_manager=None) -> None:
    """为 SparkArc 内置 Matchbox GUI 执行原有 LLM 数据库迁移。"""
    configure_sparkarc_matchbox_environment()
    _apply_legacy_env_file_aliases()

    from core.auto_migrate import BASE_DIR, run_db_upgrade

    run_db_upgrade("llm", BASE_DIR)


def initialize_sparkarc_matchbox(*, ensure_defaults: bool = True, force: bool = False):
    """在 SparkArc 的迁移完成后初始化 Matchbox。"""
    configure_sparkarc_matchbox_environment()
    integrations, engine_factory, database_url = build_sparkarc_matchbox_integrations()

    from core.migration_specs import get_db_path
    from llm.agen_matchbox import initialize_matchbox

    return initialize_matchbox(
        db_name=str(get_db_path("llm")),
        ensure_defaults=ensure_defaults,
        force=force,
        ensure_schema=False,
        database_url=database_url,
        engine_factory=engine_factory,
        integrations=integrations,
    )
