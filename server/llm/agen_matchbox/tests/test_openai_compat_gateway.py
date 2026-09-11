"""OpenAI 兼容网关的离线协议契约（网关自包含版）。

从主项目 ``test/llm/test_openai_compat_gateway.py`` 迁移网关通用部分；
依赖宿主 ``core.request_context`` / ``agents.tools`` 的用例保留在主项目，
此处只覆盖：请求头握手、工具 Schema 规范化、工具历史校验、用量回调。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def test_upstream_handshake_is_opt_in_and_outside_llm_payload() -> None:
    from ..gateway import (
        ChatUniversal,
        apply_sdk_request_compat,
        build_sdk_compat_headers,
    )
    from ..request_headers import build_upstream_request_headers, set_upstream_handshake

    try:
        # 默认不注入任何宿主标识。
        headers = build_upstream_request_headers({"User-Agent": "Agent-Matchbox/1.0"})
        assert headers == {"User-Agent": "Agent-Matchbox/1.0"}

        # 宿主显式配置后才注入，且不进入 LLM payload。
        set_upstream_handshake("X-Matchbox-Test", "matchbox-test")
        marked_headers = build_sdk_compat_headers({"User-Agent": "Agent-Matchbox/1.0"})
        assert marked_headers["X-Matchbox-Test"] == "matchbox-test"

        plain = ChatUniversal(
            model="deepseek-chat",
            api_key="offline-key",
            base_url="https://example.invalid/v1",
        )
        marked = ChatUniversal(
            model="deepseek-chat",
            api_key="offline-key",
            base_url="https://example.invalid/v1",
            default_headers=dict(marked_headers),
        )
        messages = [HumanMessage(content="固定消息")]
        assert marked._get_request_payload(messages) == plain._get_request_payload(messages)
        assert "X-Matchbox-Test" not in marked._get_request_payload(messages)
    finally:
        set_upstream_handshake(None, None)


def test_handshake_header_replacement_is_case_insensitive() -> None:
    from ..request_headers import build_upstream_request_headers, set_upstream_handshake

    try:
        set_upstream_handshake("X-Matchbox-Test", "matchbox-test")
        headers = build_upstream_request_headers({"x-matchbox-test": "incorrect"})
        assert headers == {"X-Matchbox-Test": "matchbox-test"}
    finally:
        set_upstream_handshake(None, None)


def test_prompt_cache_key_requires_injected_context_reader() -> None:
    """未注入上下文读取器时不生成路由键，也绝不导入宿主模块。"""
    import sys

    from ..gateway import ChatUniversal

    llm = ChatUniversal(
        model="gpt-5.6-offline",
        api_key="offline-key",
        base_url="https://example.invalid/v1",
    )
    payload = llm._get_request_payload([HumanMessage(content="测试")])
    assert "prompt_cache_key" not in payload
    assert "core" not in sys.modules


def test_prompt_cache_key_uses_injected_reader_and_prefix() -> None:
    from types import SimpleNamespace

    from .. import integrations as integrations_module
    from ..gateway import build_prompt_cache_routing_key

    reader = SimpleNamespace(
        user_id=lambda: "user-1",
        project_name=lambda: "project-1",
        chat_session=lambda: ("room-a", "ctx-a"),
    )
    old = integrations_module.get_prompt_cache_context_reader()
    integrations_module.set_prompt_cache_context_reader(lambda: reader)
    try:
        from langchain_core.callbacks import BaseCallbackHandler

        class AgentCallback(BaseCallbackHandler):
            agent_name = "agent_director"

        llm = SimpleNamespace(model_name="gpt-5.6-offline", callbacks=[AgentCallback()])
        first = build_prompt_cache_routing_key(llm)
        assert first is not None
        assert first.startswith("matchbox:v1:")
        assert "user-1" not in first
        assert "project-1" not in first
        # 同一会话稳定，不同会话隔离。
        assert build_prompt_cache_routing_key(llm) == first
        other = SimpleNamespace(
            user_id=lambda: "user-1",
            project_name=lambda: "project-1",
            chat_session=lambda: ("room-b", "ctx-b"),
        )
        integrations_module.set_prompt_cache_context_reader(lambda: other)
        assert build_prompt_cache_routing_key(llm) != first
        # 非目标模型不注入。
        assert build_prompt_cache_routing_key(SimpleNamespace(model_name="other", callbacks=[])) is None
    finally:
        integrations_module.set_prompt_cache_context_reader(old)


def test_tool_schema_required_is_completed_recursively_without_mutating_input() -> None:
    from ..gateway import normalize_openai_tool_schemas

    tools = [
        {
            "type": "function",
            "function": {
                "name": "inspect_scene",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filters": {
                            "type": "object",
                            "properties": {
                                "keyword": {"type": "string"},
                            },
                        }
                    },
                },
            },
        }
    ]

    normalized = normalize_openai_tool_schemas(tools)

    assert "required" not in tools[0]["function"]["parameters"]
    parameters = normalized[0]["function"]["parameters"]
    assert parameters["required"] == []
    assert parameters["properties"]["filters"]["required"] == []


def test_tool_schema_removes_plural_examples_for_gemini_compatibility() -> None:
    from ..gateway import normalize_openai_tool_schemas

    tools = [
        {
            "type": "function",
            "function": {
                "name": "update_tasks",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "operations": {
                            "type": "array",
                            "examples": [[{"operation": "complete"}]],
                            "items": {
                                "type": "object",
                                "example": {"operation": "complete"},
                                "examples": [{"operation": "complete"}],
                                "properties": {
                                    "operation": {"type": "string"},
                                },
                            },
                        },
                        "examples": {
                            "type": "string",
                            "description": "名为 examples 的真实工具参数",
                        },
                    },
                },
            },
        }
    ]

    normalized = normalize_openai_tool_schemas(tools)
    operations = normalized[0]["function"]["parameters"]["properties"]["operations"]

    assert "examples" in tools[0]["function"]["parameters"]["properties"]["operations"]
    assert "examples" not in operations
    assert "examples" not in operations["items"]
    assert operations["items"]["example"] == {"operation": "complete"}
    assert normalized[0]["function"]["parameters"]["properties"]["examples"] == {
        "type": "string",
        "description": "名为 examples 的真实工具参数",
    }


def test_gateway_rejects_incomplete_tool_history_before_upstream_request() -> None:
    from ..gateway import ChatUniversal
    from ..tool_protocol import ToolMessageProtocolError

    llm = ChatUniversal(
        model="offline-tool-protocol-check",
        api_key="offline-key",
        base_url="https://example.invalid/v1",
    )
    messages = [
        HumanMessage(content="继续任务"),
        AIMessage(content="", tool_calls=[{
            "id": "call_first",
            "name": "first_tool",
            "args": {},
            "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "id": "call_second",
            "name": "second_tool",
            "args": {},
            "type": "tool_call",
        }]),
        ToolMessage(content="已更新", tool_call_id="call_second", name="second_tool"),
    ]

    with pytest.raises(ToolMessageProtocolError, match="call_first"):
        llm._get_request_payload(messages)


def test_usage_callback_records_offline_without_blocking(monkeypatch) -> None:
    """用量回调只投递后台任务：调用返回时 DB 为空，后台落库后才可见。"""
    from ..tracked_model import UsageTrackingCallback
    from ..usage_writer import BackgroundUsageWriter, set_default_usage_writer

    writer = BackgroundUsageWriter()
    old_writer = None
    try:
        from .. import usage_writer as usage_writer_module

        old_writer = usage_writer_module._default_writer
        usage_writer_module._default_writer = writer
        notifications: list = []
        monkeypatch.setattr(
            "agen_matchbox.tracked_model.settle_usage_entry_credit",
            lambda *_args, **_kwargs: None,
        )

        class FakeSession:
            committed: list = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def add(self, entry):
                FakeSession.committed.append(entry)

            def flush(self):
                return None

            def commit(self):
                return None

        callback = UsageTrackingCallback(
            user_id="u",
            model_id=1,
            platform_id=2,
            model_name="offline-model",
            platform_name="offline-provider",
            session_maker=FakeSession,
            agent_name="agent_director",
            usage_recorded_handler=notifications.append,
        )

        callback._record_usage(
            prompt_tokens=100,
            completion_tokens=20,
            cached_prompt_tokens=60,
            cache_miss_prompt_tokens=40,
            usage_source="upstream",
            cache_source="provider",
        )
        # 投递是非阻塞的：函数返回不代表落库完成，最终由后台线程收口。
        writer._tasks.join()

        assert notifications == [{
            "agent_name": "agent_director",
            "model_name": "offline-model",
            "platform_name": "offline-provider",
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cached_prompt_tokens": 60,
            "cache_miss_prompt_tokens": 40,
            "usage_source": "upstream",
            "cache_source": "provider",
            "success": True,
            "context_key": None,
        }]
        assert FakeSession.committed
        assert writer.stats()["failed_tasks"] == 0
    finally:
        from .. import usage_writer as usage_writer_module

        usage_writer_module._default_writer = old_writer
        writer.shutdown()


def test_usage_callback_falls_back_to_context_captured_at_creation(monkeypatch) -> None:
    from ..tracked_model import UsageTrackingCallback

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def add(self, entry):
            self.entry = entry

        def flush(self):
            return None

        def commit(self):
            return None

    current_context = ["batch-context"]
    events = []
    monkeypatch.setattr(
        "agen_matchbox.tracked_model.settle_usage_entry_credit",
        lambda *_args, **_kwargs: None,
    )
    callback = UsageTrackingCallback(
        user_id="u",
        model_id=1,
        platform_id=2,
        model_name="offline-model",
        platform_name="offline-provider",
        session_maker=FakeSession,
        usage_context_provider=lambda: current_context[0],
        usage_recorded_handler=events.append,
    )

    current_context[0] = None
    callback._write_usage_snapshot({
        "user_id": "u",
        "model_id": 1,
        "prompt_tokens": 3,
        "completion_tokens": 1,
        "cached_prompt_tokens": 0,
        "cache_miss_prompt_tokens": None,
        "usage_source": None,
        "cache_source": None,
        "success": True,
        "agent_name": None,
        "context_key": None,
        "quota_scope": None,
    })

    assert events[0]["context_key"] == "batch-context"
