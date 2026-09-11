"""OpenAI 兼容网关的离线协议契约。"""

from __future__ import annotations

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def test_sparkarc_handshake_header_is_fixed_and_outside_llm_payload(monkeypatch) -> None:
    from llm import matchbox_adapter
    from llm.agen_matchbox.gateway import (
        ChatUniversal,
        apply_sdk_request_compat,
        build_sdk_compat_headers,
    )
    from llm.agen_matchbox.request_headers import (
        SPARKARC_HANDSHAKE_HEADER,
        SPARKARC_HANDSHAKE_VALUE,
        build_upstream_request_headers,
        set_upstream_handshake,
    )

    # 宿主行为：通过适配层显式配置握手标识（独立网关默认不注入）。
    matchbox_adapter.configure_sparkarc_matchbox_environment()
    set_upstream_handshake(SPARKARC_HANDSHAKE_HEADER, SPARKARC_HANDSHAKE_VALUE)

    original_headers = {
        "User-Agent": "SparkArc/1.0",
        "x-sparkarc-client": "incorrect",
    }
    headers = build_upstream_request_headers(original_headers)

    assert headers == {
        "User-Agent": "SparkArc/1.0",
        SPARKARC_HANDSHAKE_HEADER: SPARKARC_HANDSHAKE_VALUE,
    }
    assert original_headers["x-sparkarc-client"] == "incorrect"

    compat_kwargs = {"default_headers": {"User-Agent": "SparkArc/1.0"}}
    apply_sdk_request_compat(compat_kwargs, include_stream_usage=False)
    assert compat_kwargs["default_headers"][SPARKARC_HANDSHAKE_HEADER] == SPARKARC_HANDSHAKE_VALUE

    plain = ChatUniversal(
        model="deepseek-chat",
        api_key="offline-key",
        base_url="https://example.invalid/v1",
    )
    marked = ChatUniversal(
        model="deepseek-chat",
        api_key="offline-key",
        base_url="https://example.invalid/v1",
        default_headers=build_sdk_compat_headers({"User-Agent": "SparkArc/1.0"}),
    )
    messages = [HumanMessage(content="固定消息")]

    assert marked._get_request_payload(messages) == plain._get_request_payload(messages)
    assert SPARKARC_HANDSHAKE_HEADER not in marked._get_request_payload(messages)


def test_gpt56_prompt_cache_key_is_stable_and_session_isolated() -> None:
    from llm import matchbox_adapter
    from core.request_context import (
        current_project_name,
        current_user_id,
        reset_current_chat_session,
        set_current_chat_session,
    )
    from llm.agen_matchbox.gateway import ChatUniversal

    # 宿主行为：构造注入配置并初始化管理器，注册提示词缓存上下文读取器。
    # （独立网关不自动注册，显式初始化后才生效。）
    integrations, _engine_factory, _database_url = matchbox_adapter.build_sparkarc_matchbox_integrations()
    from llm.agen_matchbox.integrations import set_prompt_cache_context_reader
    set_prompt_cache_context_reader(integrations.prompt_cache_context_reader)

    class AgentCallback(BaseCallbackHandler):
        agent_name = "agent_director"

    user_token = current_user_id.set("user-1")
    project_token = current_project_name.set("project-1")
    chat_tokens = set_current_chat_session("agent_director", "room-a")
    try:
        llm = ChatUniversal(
            model="gpt-5.6-luna",
            api_key="offline-key",
            base_url="https://example.invalid/v1",
            callbacks=[AgentCallback()],
        )
        first = llm._get_request_payload([HumanMessage(content="第一次")])
        second = llm._get_request_payload([HumanMessage(content="第二次")])
        assert first["prompt_cache_key"] == second["prompt_cache_key"]
        assert first["prompt_cache_key"].startswith("sparkarc:v1:")
        assert "user-1" not in first["prompt_cache_key"]
        assert "project-1" not in first["prompt_cache_key"]
    finally:
        reset_current_chat_session(chat_tokens)

    other_chat_tokens = set_current_chat_session("agent_director", "room-b")
    try:
        isolated = llm._get_request_payload([HumanMessage(content="第三次")])
        assert isolated["prompt_cache_key"] != first["prompt_cache_key"]
    finally:
        reset_current_chat_session(other_chat_tokens)
        current_project_name.reset(project_token)
        current_user_id.reset(user_token)

    # 断言前缀由宿主环境变量保持 SparkArc 行为（独立网关默认 matchbox:v1）。
    assert first["prompt_cache_key"].startswith("sparkarc:v1:")


def test_prompt_cache_key_is_not_injected_into_other_compatible_models() -> None:
    from core.request_context import current_project_name, current_user_id
    from llm.agen_matchbox.gateway import ChatUniversal

    class AgentCallback(BaseCallbackHandler):
        agent_name = "agent_director"

    user_token = current_user_id.set("user-1")
    project_token = current_project_name.set("project-1")
    try:
        llm = ChatUniversal(
            model="deepseek-chat",
            api_key="offline-key",
            base_url="https://example.invalid/v1",
            callbacks=[AgentCallback()],
        )
        payload = llm._get_request_payload([HumanMessage(content="测试")])
        assert "prompt_cache_key" not in payload
    finally:
        current_project_name.reset(project_token)
        current_user_id.reset(user_token)


def test_tool_schema_required_is_always_an_array() -> None:
    from agents.tools.registry import TOOLS_BY_NAME
    from llm.agen_matchbox.gateway import ChatUniversal

    llm = ChatUniversal(
        model="offline-schema-check",
        api_key="offline-key",
        base_url="https://example.invalid/v1",
    )
    bound = llm.bind_tools(list(TOOLS_BY_NAME.values()))
    payload = llm._get_request_payload(
        [HumanMessage(content="离线检查")],
        **bound.kwargs,
    )

    parameters_by_name = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in payload["tools"]
    }
    assert set(parameters_by_name) == set(TOOLS_BY_NAME)
    assert all(
        isinstance(parameters.get("required"), list)
        for parameters in parameters_by_name.values()
    )
    assert parameters_by_name["list_chapters"]["required"] == []
    assert parameters_by_name["delegate_task"]["required"] == [
        "target_agent",
        "task_description",
    ]
    delegate_properties = parameters_by_name["delegate_task"]["properties"]
    assert set(delegate_properties) == {
            "target_agent",
            "task_description",
            "tracker_item_id",
            "completion_mode",
        "chapter_name",
        "scene_name",
        "scene_file_path",
        "scene_guidance",
        "scene_characters",
    }
    assert delegate_properties["target_agent"]["enum"] == [
        "agent_scriptwriter",
        "agent_showrunner",
        "agent_lorebook",
        "agent_muse",
        "agent_critic",
    ]


def test_tool_schema_required_is_completed_recursively_without_mutating_input() -> None:
    from llm.agen_matchbox.gateway import normalize_openai_tool_schemas

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
    from llm.agen_matchbox.gateway import normalize_openai_tool_schemas

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


def test_work_tracker_schema_sent_upstream_excludes_plural_examples() -> None:
    from agents.tools.automation import work_tracker
    from llm.agen_matchbox.gateway import ChatUniversal

    llm = ChatUniversal(
        model="offline-gemini-schema-check",
        api_key="offline-key",
        base_url="https://example.invalid/v1",
    )
    bound = llm.bind_tools([work_tracker])
    payload = llm._get_request_payload(
        [HumanMessage(content="离线检查")],
        **bound.kwargs,
    )

    serialized_schema = str(payload["tools"][0]["function"]["parameters"])
    assert "'examples':" not in serialized_schema


def test_schema_validation_400_is_not_reported_as_content_moderation(monkeypatch) -> None:
    from agents.routes.schemas import format_ai_error

    monkeypatch.setattr("core.request_context.get_current_locale", lambda: "zh-CN")
    raw = (
        "Error code: 400 - Invalid request content: Schema validation failed: "
        '[standard_violation] /required: null is not of type "array"'
    )

    message = format_ai_error(RuntimeError(raw))

    assert "Schema 校验" in message
    assert "内容安全" not in message
    assert raw in message


def test_explicit_content_filter_error_keeps_content_safety_guidance(monkeypatch) -> None:
    from agents.routes.schemas import format_ai_error

    monkeypatch.setattr("core.request_context.get_current_locale", lambda: "zh-CN")

    message = format_ai_error(RuntimeError("400 content_filter policy violation"))

    assert "内容安全策略" in message


def test_generic_400_uses_neutral_invalid_request_guidance(monkeypatch) -> None:
    from agents.routes.schemas import format_ai_error

    monkeypatch.setattr("core.request_context.get_current_locale", lambda: "zh-CN")

    message = format_ai_error(RuntimeError("Error code: 400 - unsupported parameter"))

    assert "请求无效" in message
    assert "内容安全" not in message


def test_gateway_rejects_incomplete_tool_history_before_upstream_request() -> None:
    from llm.agen_matchbox.gateway import ChatUniversal
    from llm.agen_matchbox.tool_protocol import ToolMessageProtocolError

    llm = ChatUniversal(
        model="offline-tool-protocol-check",
        api_key="offline-key",
        base_url="https://example.invalid/v1",
    )
    messages = [
        HumanMessage(content="继续任务"),
        AIMessage(content="", tool_calls=[{
            "id": "call_delegate",
            "name": "delegate_task",
            "args": {},
            "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "id": "call_tracker",
            "name": "work_tracker",
            "args": {},
            "type": "tool_call",
        }]),
        ToolMessage(content="已更新", tool_call_id="call_tracker", name="work_tracker"),
    ]

    with pytest.raises(ToolMessageProtocolError, match="call_delegate"):
        llm._get_request_payload(messages)


def test_usage_callback_notifies_host_after_usage_commit(monkeypatch) -> None:
    """用量写入改为后台异步：调用返回后由后台线程收口，语义与原来一致。"""
    from llm.agen_matchbox.tracked_model import UsageTrackingCallback
    from llm.agen_matchbox.usage_writer import BackgroundUsageWriter

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

    events = []
    monkeypatch.setattr(
        "llm.agen_matchbox.tracked_model.settle_usage_entry_credit",
        lambda *_args, **_kwargs: None,
    )
    writer = BackgroundUsageWriter()
    try:
        callback = UsageTrackingCallback(
            user_id="u",
            model_id=1,
            platform_id=2,
            model_name="offline-model",
            platform_name="offline-provider",
            session_maker=FakeSession,
            agent_name="agent_director",
            usage_recorded_handler=events.append,
            usage_writer=writer,
        )

        callback._record_usage(
            prompt_tokens=100,
            completion_tokens=20,
            cached_prompt_tokens=60,
            cache_miss_prompt_tokens=40,
            usage_source="upstream",
            cache_source="provider",
        )
        writer._tasks.join()
    finally:
        writer.shutdown()

    assert events == [{
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


def test_usage_callback_falls_back_to_context_captured_at_creation(monkeypatch) -> None:
    from llm.agen_matchbox.tracked_model import UsageTrackingCallback

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
        "llm.agen_matchbox.tracked_model.settle_usage_entry_credit",
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
    # 后台写入路径下，创建时刻的快照在后台线程解析；此处直接验证快照写法。
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
