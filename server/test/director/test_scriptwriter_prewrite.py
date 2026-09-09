from __future__ import annotations

import json
import asyncio
import contextvars
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import ValidationError

from agents.scriptwriter_prewrite import (
    PREWRITE_MAX_TOOL_CALLS,
    PREWRITE_MAX_TOOL_ROUNDS,
    ScriptwriterPreWriteRequest,
    _prewrite_read_tools,
    run_autonomous_scriptwriter_creation,
    run_autonomous_scriptwriter_prewrite,
)
from agents.routes.chat import _run_chat_background_context
from core.request_context import (
    clear_scriptwriter_prewrite_receipt,
    current_agent_id,
    current_project_name,
    current_scriptwriter_prewrite_receipt,
    current_user_id,
    get_scriptwriter_prewrite_receipt,
    set_current_export_format,
    set_scriptwriter_prewrite_receipt,
)
from llm.agen_matchbox.tool_protocol import validate_tool_message_history


@tool
def prewrite_read_probe() -> str:
    """返回只读测试资料。"""
    return "只读事实"


class _FakeBoundLlm:
    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = list(responses)
        self.invoke_count = 0
        self.message_snapshots: list[list] = []

    def invoke(self, messages):
        self.invoke_count += 1
        self.message_snapshots.append(list(messages))
        return self.responses.pop(0)


class _FakeLlm:
    def __init__(self, bound_responses: list[AIMessage]) -> None:
        self.bound = _FakeBoundLlm(bound_responses)
        self.bound_tool_names: list[str] = []
        self.final_invoke_count = 0

    def bind_tools(self, tools):
        self.bound_tool_names = [item.name for item in tools]
        return self.bound

    def invoke(self, _messages):
        self.final_invoke_count += 1
        return AIMessage(content="围绕冲突推进，保持人物知情边界，结尾落到关键选择。")


def _tool_call(call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{
            "name": "prewrite_read_probe",
            "args": {},
            "id": call_id,
            "type": "tool_call",
        }],
    )


def test_autonomous_prewrite_only_binds_read_tools_and_limits_rounds(monkeypatch) -> None:
    monkeypatch.setattr("agents.scriptwriter_prewrite._build_prewrite_brief", lambda _request: "场景任务包")
    monkeypatch.setattr("agents.scriptwriter_prewrite._prewrite_read_tools", lambda: [prewrite_read_probe])
    llm = _FakeLlm([_tool_call("call-1"), _tool_call("call-2")])
    progress_tools = []

    result = run_autonomous_scriptwriter_prewrite(
        ScriptwriterPreWriteRequest(
            user_id="u1",
            project_name="p1",
            task_description="写第一场",
            chapter_name="一 · 开端",
            scene_name="1-1 初遇",
        ),
        llm=llm,
        on_tool_progress=progress_tools.append,
        max_tool_rounds=2,
    )

    assert llm.bound_tool_names == ["prewrite_read_probe"]
    assert llm.bound.invoke_count == 2
    assert llm.final_invoke_count == 0
    assert result.tools_used == ("prewrite_read_probe", "prewrite_read_probe")
    assert progress_tools == ["prewrite_read_probe", "prewrite_read_probe"]
    assert "只读事实" in result.research_context
    assert result.planning_note == ""


def test_autonomous_prewrite_allows_same_tool_retry_after_failure(monkeypatch) -> None:
    attempts = {"count": 0}

    @tool
    def retry_probe() -> str:
        """读取可重试的测试资料。"""
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("临时读取失败")
        return "重试后取得的事实"

    monkeypatch.setattr("agents.scriptwriter_prewrite._build_prewrite_brief", lambda _request: "场景任务包")
    monkeypatch.setattr("agents.scriptwriter_prewrite._prewrite_read_tools", lambda: [retry_probe])
    llm = _FakeLlm([
        AIMessage(content="", tool_calls=[{
            "name": "retry_probe",
            "args": {},
            "id": "retry-1",
            "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "retry_probe",
            "args": {},
            "id": "retry-2",
            "type": "tool_call",
        }]),
    ])

    lifecycle_events: list[dict] = []
    result = run_autonomous_scriptwriter_prewrite(
        ScriptwriterPreWriteRequest(
            user_id="u-retry",
            project_name="p-retry",
            task_description="核对当前场景",
        ),
        llm=llm,
        on_lifecycle_event=lifecycle_events.append,
        max_tool_rounds=2,
    )

    assert attempts["count"] == 2
    assert result.tools_used == ("retry_probe", "retry_probe")
    assert "重试后取得的事实" in result.research_context
    failed = next(item for item in lifecycle_events if item["event"] == "tool_failed")
    assert failed["error"] == "工具 retry_probe 执行失败：临时读取失败"
    assert failed["will_retry"] is True
    assert failed["attempt"] == 1
    assert failed["max_attempts"] == 2
    assert any(
        item["event"] == "tool_succeeded" and item["tool_name"] == "retry_probe"
        for item in lifecycle_events
    )


def test_autonomous_prewrite_normalizes_missing_tool_call_id(monkeypatch) -> None:
    monkeypatch.setattr("agents.scriptwriter_prewrite._build_prewrite_brief", lambda _request: "场景任务包")
    monkeypatch.setattr("agents.scriptwriter_prewrite._prewrite_read_tools", lambda: [prewrite_read_probe])

    class CapturingLlm(_FakeLlm):
        final_messages: list | None = None

        def invoke(self, messages):
            self.final_messages = list(messages)
            return super().invoke(messages)

    llm = CapturingLlm([_tool_call(""), AIMessage(content="PREWRITE_READY")])
    run_autonomous_scriptwriter_prewrite(
        ScriptwriterPreWriteRequest(
            user_id="u-missing-id",
            project_name="p-missing-id",
            task_description="核对当前场景",
        ),
        llm=llm,
        max_tool_rounds=2,
    )

    assert llm.bound.message_snapshots
    final_messages = llm.bound.message_snapshots[-1]
    validate_tool_message_history(final_messages)
    assistant = next(message for message in final_messages if isinstance(message, AIMessage))
    tool_message = final_messages[final_messages.index(assistant) + 1]
    assert assistant.tool_calls[0]["id"]
    assert assistant.tool_calls[0]["id"] == tool_message.tool_call_id


def test_prewrite_default_research_budget_and_search_tools() -> None:
    tool_names = {tool.name for tool in _prewrite_read_tools()}

    assert PREWRITE_MAX_TOOL_ROUNDS == 4
    assert PREWRITE_MAX_TOOL_CALLS is None
    assert {"search_project", "semantic_search"} <= tool_names


def test_autonomous_prewrite_uses_model_budget_without_fixed_character_truncation(monkeypatch) -> None:
    long_tool_result = "工具结果开头" + ("证据" * 5000) + "工具结果结尾"

    @tool
    def long_prewrite_probe() -> str:
        """返回超过旧版固定上限的测试资料。"""
        return long_tool_result

    monkeypatch.setattr("agents.scriptwriter_prewrite._build_prewrite_brief", lambda _request: "场景任务包")
    monkeypatch.setattr("agents.scriptwriter_prewrite._prewrite_read_tools", lambda: [long_prewrite_probe])
    llm = _FakeLlm([
        AIMessage(content="", tool_calls=[{
            "name": "long_prewrite_probe",
            "args": {},
            "id": "call-long",
            "type": "tool_call",
        }]),
        AIMessage(content="连续性简报"),
    ])
    full_outline = "大纲开头" + ("章节" * 6000) + "大纲结尾"
    available_context = "上下文开头" + ("状态" * 5000) + "上下文结尾"

    result = run_autonomous_scriptwriter_prewrite(
        ScriptwriterPreWriteRequest(
            user_id="u-long",
            project_name="p-long",
            task_description="写当前场景",
            chapter_name="十 · 返家",
            scene_name="10-3 开门",
            full_outline=full_outline,
            available_context=available_context,
        ),
        llm=llm,
        max_tool_rounds=2,
    )

    first_system_message = str(llm.bound.message_snapshots[0][0].content)
    first_user_message = str(llm.bound.message_snapshots[0][1].content)
    assert "大纲开头" in first_system_message and "大纲结尾" in first_system_message
    assert "大纲开头" not in first_user_message and "大纲结尾" not in first_user_message
    assert "上下文开头" in first_user_message and "上下文结尾" in first_user_message
    assert "工具结果开头" in result.research_context
    assert "工具结果结尾" in result.research_context
    assert "按 PreWrite 上下文预算截断" not in result.research_context


def test_autonomous_creation_batches_research_and_saves_without_summary_request(monkeypatch) -> None:
    calls = []

    @tool
    def first_research_probe() -> str:
        """读取第一份事实。"""
        calls.append("first_research_probe")
        return "事实一"

    @tool
    def second_research_probe() -> str:
        """读取第二份事实。"""
        calls.append("second_research_probe")
        return "事实二"

    @tool
    def create_chapter(chapter_name: str) -> str:
        """创建章节。"""
        calls.append(f"create_chapter:{chapter_name}")
        return "章节已存在"

    @tool
    def create_or_rewrite_script(
        overwrite_content: str,
        chapter_name: str,
        work_name: str,
    ) -> str:
        """保存正文。"""
        calls.append(f"create_or_rewrite_script:{chapter_name}:{work_name}")
        return json.dumps({
            "status": "saved",
            "path": "一 · 开端/1-1 初遇.arc",
            "written_chars": 8,
        }, ensure_ascii=False)

    tools = [
        first_research_probe,
        second_research_probe,
        create_chapter,
        create_or_rewrite_script,
    ]
    response = AIMessage(content="", tool_calls=[
        {"name": "first_research_probe", "args": {}, "id": "read-1", "type": "tool_call"},
        {"name": "second_research_probe", "args": {}, "id": "read-2", "type": "tool_call"},
        {
            "name": "create_chapter",
            "args": {"chapter_name": "一 · 开端"},
            "id": "chapter-1",
            "type": "tool_call",
        },
        {
            "name": "create_or_rewrite_script",
            "args": {
                "overwrite_content": "<conception>保持知情边界</conception>\n[旁白] 雨落。",
                "chapter_name": "一 · 开端",
                "work_name": "1-1 初遇",
            },
            "id": "write-1",
            "type": "tool_call",
        },
    ])
    llm = _FakeLlm([response])

    class FakeAgent:
        def __init__(self):
            self.llm = llm

        @staticmethod
        def _clean_model_visible_arc_text(value) -> str:
            return str(value or "")

        @staticmethod
        def _build_tool_system_prompt(*_args, **_kwargs) -> str:
            return "固定系统提示"

    monkeypatch.setattr("agents.scriptwriter_prewrite._build_prewrite_brief", lambda _request: "场景任务包")
    monkeypatch.setattr("agents.scriptwriter_prewrite._prewrite_read_tools", lambda: tools[:2])
    monkeypatch.setattr("agents.scriptwriter_prewrite._autonomous_creation_tools", lambda: tools)
    monkeypatch.setattr("agents.scriptwriter_prewrite._issue_receipt", lambda *_args, **_kwargs: "receipt")

    result = run_autonomous_scriptwriter_creation(
        ScriptwriterPreWriteRequest(
            user_id="u-batch",
            project_name="p-batch",
            task_description="写第一场",
            chapter_name="一 · 开端",
            scene_name="1-1 初遇",
        ),
        agent=FakeAgent(),
    )

    assert result.saved is True
    assert result.request_count == 1
    assert result.planning_note == ""
    assert "<conception>" in result.written_content
    assert calls == [
        "first_research_probe",
        "second_research_probe",
        "create_chapter:一 · 开端",
        "create_or_rewrite_script:一 · 开端:1-1 初遇",
    ]
    assert llm.bound.invoke_count == 1
    assert llm.final_invoke_count == 0


def test_autonomous_creation_appends_completed_scene_before_current_scene(monkeypatch) -> None:
    @tool
    def create_chapter(chapter_name: str) -> str:
        """创建章节。"""
        return f"章节可用：{chapter_name}"

    @tool
    def create_or_rewrite_script(
        overwrite_content: str,
        chapter_name: str,
        work_name: str,
    ) -> str:
        """保存正文。"""
        return json.dumps({
            "status": "saved",
            "path": f"{chapter_name}/{work_name}.arc",
            "written_chars": len(overwrite_content),
        }, ensure_ascii=False)

    def saved_response(scene_name: str) -> AIMessage:
        return AIMessage(content="", tool_calls=[
            {
                "name": "create_chapter",
                "args": {"chapter_name": "一 · 开端"},
                "id": f"chapter-{scene_name}",
                "type": "tool_call",
            },
            {
                "name": "create_or_rewrite_script",
                "args": {
                    "overwrite_content": f"<conception>{scene_name}连续性</conception>\n[旁白] {scene_name}正文。",
                    "chapter_name": "一 · 开端",
                    "work_name": scene_name,
                },
                "id": f"write-{scene_name}",
                "type": "tool_call",
            },
        ])

    llm = _FakeLlm([saved_response("1-1 初遇"), saved_response("1-2 追踪")])

    class FakeAgent:
        def __init__(self):
            self.llm = llm

        @staticmethod
        def _clean_model_visible_arc_text(value) -> str:
            return str(value or "")

        @staticmethod
        def _build_tool_system_prompt(*_args, **_kwargs) -> str:
            return "固定系统提示"

    monkeypatch.setattr(
        "agents.scriptwriter_prewrite._build_prewrite_brief",
        lambda request: f"完整任务包：{request.scene_name}",
    )
    monkeypatch.setattr(
        "agents.scriptwriter_prewrite._autonomous_creation_tools",
        lambda: [create_chapter, create_or_rewrite_script],
    )
    monkeypatch.setattr("agents.scriptwriter_prewrite._issue_receipt", lambda *_args, **_kwargs: "receipt")
    agent = FakeAgent()
    first = run_autonomous_scriptwriter_creation(
        ScriptwriterPreWriteRequest(
            user_id="u-cache",
            project_name="p-cache",
            task_description="写第一场",
            chapter_name="一 · 开端",
            scene_name="1-1 初遇",
            available_context="第一场完整动态上下文",
        ),
        agent=agent,
    )
    second = run_autonomous_scriptwriter_creation(
        ScriptwriterPreWriteRequest(
            user_id="u-cache",
            project_name="p-cache",
            task_description="写第二场",
            chapter_name="一 · 开端",
            scene_name="1-2 追踪",
            available_context="第二场完整动态上下文",
        ),
        agent=agent,
        completed_turns=(first.continuity_turn,),
    )

    first_messages, second_messages = llm.bound.message_snapshots
    assert len(first_messages) == 2
    assert len(second_messages) == 6
    assert isinstance(second_messages[0], SystemMessage)
    assert isinstance(second_messages[1], HumanMessage)
    assert isinstance(second_messages[2], AIMessage)
    assert isinstance(second_messages[3], ToolMessage)
    assert isinstance(second_messages[4], AIMessage)
    assert isinstance(second_messages[5], HumanMessage)
    assert second_messages[0].content == first_messages[0].content
    assert second_messages[1].content == first_messages[1].content
    assert second_messages[2].tool_calls[0]["name"] == "create_chapter"
    assert second_messages[3].tool_call_id == second_messages[2].tool_calls[0]["id"]
    assert "1-1 初遇连续性" in second_messages[4].content
    assert "第一场正文" not in str(second_messages[2].tool_calls)
    assert "第二场完整动态上下文" in second_messages[5].content
    validate_tool_message_history(second_messages[:4])
    assert second.continuity_turn is not None


def test_prepare_tool_issues_matching_receipt(monkeypatch) -> None:
    from agents.tools.scriptwriter import prepare_script_creation

    monkeypatch.setattr("agents.scriptwriter_prewrite._build_prewrite_brief", lambda _request: "确定性任务包")
    user_token = current_user_id.set("u2")
    project_token = current_project_name.set("p2")
    agent_token = current_agent_id.set("agent_scriptwriter")
    receipt_token = current_scriptwriter_prewrite_receipt.set({})
    clear_scriptwriter_prewrite_receipt()
    try:
        payload = json.loads(prepare_script_creation.invoke({
            "task_description": "完整创作第一场",
            "chapter_name": "一 · 开端",
            "scene_name": "1-1 初遇",
            "scene_guidance": "建立冲突",
            "scene_characters": ["林舟"],
        }))
        receipt = get_scriptwriter_prewrite_receipt()
        assert payload["status"] == "ready"
        assert payload["task_pack"] == "确定性任务包"
        assert receipt is not None
        assert receipt["chapter_name"] == "一 · 开端"
        assert receipt["scene_name"] == "1-1 初遇"
        assert receipt["chapter_num"] == 1
        assert receipt["scene_num"] == 1
    finally:
        clear_scriptwriter_prewrite_receipt()
        current_scriptwriter_prewrite_receipt.reset(receipt_token)
        current_agent_id.reset(agent_token)
        current_project_name.reset(project_token)
        current_user_id.reset(user_token)


def test_scriptwriter_tools_reject_null_and_placeholder_target_names() -> None:
    from agents.tools.scriptwriter import (
        CreateOrRewriteScriptInput,
        PrepareScriptCreationInput,
    )

    invalid_values = (None, "", "   ", "null", "NULL", "undefined", "None")
    for invalid in invalid_values:
        with pytest.raises(ValidationError):
            PrepareScriptCreationInput.model_validate({
                "task_description": "写第一场",
                "chapter_name": invalid,
                "scene_name": "1-1 初遇",
            })
        with pytest.raises(ValidationError):
            CreateOrRewriteScriptInput.model_validate({
                "overwrite_content": "正文",
                "chapter_name": "一 · 开端",
                "work_name": invalid,
            })

    with pytest.raises(ValidationError):
        CreateOrRewriteScriptInput.model_validate({"overwrite_content": "正文"})


def test_prewrite_receipt_never_matches_missing_or_placeholder_targets() -> None:
    from agents.scriptwriter_prewrite import has_matching_prewrite_receipt

    receipt_token = current_scriptwriter_prewrite_receipt.set({})
    try:
        set_scriptwriter_prewrite_receipt({
            "receipt_id": "receipt-invalid",
            "user_id": "u-invalid",
            "project_name": "p-invalid",
            "chapter_name": "null",
            "scene_name": "null",
        })
        assert has_matching_prewrite_receipt(
            user_id="u-invalid",
            project_name="p-invalid",
            chapter_name=None,
            scene_name=None,
        ) is False
        assert has_matching_prewrite_receipt(
            user_id="u-invalid",
            project_name="p-invalid",
            chapter_name="null",
            scene_name="null",
        ) is False
    finally:
        clear_scriptwriter_prewrite_receipt()
        current_scriptwriter_prewrite_receipt.reset(receipt_token)


def test_full_script_write_requires_and_consumes_matching_receipt(monkeypatch, tmp_path) -> None:
    from agents.tools.scriptwriter import create_or_rewrite_script

    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    monkeypatch.setattr("agents.story_memory.enqueue_scene_memory_write", lambda **_kwargs: None)
    user_token = current_user_id.set("u3")
    project_token = current_project_name.set("p3")
    agent_token = current_agent_id.set("agent_scriptwriter")
    receipt_token = current_scriptwriter_prewrite_receipt.set({})
    set_current_export_format("novel")
    clear_scriptwriter_prewrite_receipt()
    args = {
        "overwrite_content": "雨落在旧站台上。",
        "chapter_name": "一 · 开端",
        "work_name": "1-1 初遇",
        "target_chars": 800,
    }
    try:
        rejected = create_or_rewrite_script.invoke(args)
        assert "尚未完成匹配的 PreWrite" in rejected

        set_scriptwriter_prewrite_receipt({
            "receipt_id": "receipt-1",
            "user_id": "u3",
            "project_name": "p3",
            "chapter_name": "一 · 开端",
            "scene_name": "1-2 错误场景",
        })
        mismatch = create_or_rewrite_script.invoke(args)
        assert "尚未完成匹配的 PreWrite" in mismatch

        extension_mismatch = create_or_rewrite_script.invoke({
            **args,
            "work_name": "1-1 初遇.md",
        })
        assert "work_name 必须是不含扩展名" in extension_mismatch

        set_scriptwriter_prewrite_receipt({
            "receipt_id": "receipt-2",
            "user_id": "u3",
            "project_name": "p3",
            "chapter_name": "一 · 开端",
            "scene_name": "1-1 初遇",
        })
        saved = create_or_rewrite_script.invoke(args)
        saved_payload = json.loads(saved)
        assert saved_payload["status"] == "saved"
        assert "已保存" in saved_payload["message"]
        assert saved_payload["written_chars"] == len("雨落在旧站台上")
        assert saved_payload["target_chars"] == 800
        assert saved_payload["target_source"] == "current_task"
        assert saved_payload["deviation_chars"] == len("雨落在旧站台上") - 800
        assert "自行判断" in saved_payload["length_policy"]
        assert get_scriptwriter_prewrite_receipt() is None
    finally:
        clear_scriptwriter_prewrite_receipt()
        set_current_export_format(None)
        current_scriptwriter_prewrite_receipt.reset(receipt_token)
        current_agent_id.reset(agent_token)
        current_project_name.reset(project_token)
        current_user_id.reset(user_token)


def test_full_script_write_rejects_metadata_only_zero_body(monkeypatch, tmp_path) -> None:
    from agents.tools.scriptwriter import create_or_rewrite_script

    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    user_token = current_user_id.set("u-empty")
    project_token = current_project_name.set("p-empty")
    agent_token = current_agent_id.set("agent_scriptwriter")
    receipt_token = current_scriptwriter_prewrite_receipt.set({})
    set_current_export_format("arc")
    try:
        set_scriptwriter_prewrite_receipt({
            "receipt_id": "receipt-empty",
            "user_id": "u-empty",
            "project_name": "p-empty",
            "chapter_name": "一 · 开端",
            "scene_name": "1-1 空场景",
            "chapter_num": 1,
            "scene_num": 1,
        })
        result = create_or_rewrite_script.invoke({
            "overwrite_content": "# 1-1 空场景\n<conception>只有构思，没有正文。</conception>",
            "chapter_name": "一 · 开端",
            "work_name": "1-1 空场景",
        })
    finally:
        clear_scriptwriter_prewrite_receipt()
        set_current_export_format(None)
        current_scriptwriter_prewrite_receipt.reset(receipt_token)
        current_agent_id.reset(agent_token)
        current_project_name.reset(project_token)
        current_user_id.reset(user_token)

    assert "正文没有可见内容" in result
    assert not list(tmp_path.rglob("*.arc"))


def test_auto_write_completion_guard_rejects_zero_body_scene() -> None:
    from agents.routes.auto_write import _require_nonempty_scene_body

    with pytest.raises(RuntimeError, match="没有可见正文"):
        _require_nonempty_scene_body(
            "# 1-1 空场景\n<conception>只有构思，没有正文。</conception>",
            "arc",
        )

    assert _require_nonempty_scene_body("# 1-1 正常场景\n[-1]\n雨落在旧站台上。", "arc") > 0


def test_chat_background_preserves_receipt_and_format_across_tool_contexts(monkeypatch, tmp_path) -> None:
    from agents.tools.scriptwriter import create_chapter, create_or_rewrite_script, prepare_script_creation

    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    monkeypatch.setattr("agents.scriptwriter_prewrite._build_prewrite_brief", lambda _request: "确定性任务包")
    monkeypatch.setattr("agents.story_memory.enqueue_scene_memory_write", lambda **_kwargs: None)

    saved_result = ""

    def callback() -> None:
        nonlocal saved_result
        agent_token = current_agent_id.set("agent_scriptwriter")
        try:
            prepare_context = contextvars.copy_context()
            prepare_payload = json.loads(prepare_context.run(
                prepare_script_creation.invoke,
                {
                    "task_description": "完整创作第一场",
                    "chapter_name": "一 · 开端",
                    "scene_name": "1-1 初遇",
                },
            ))
            assert prepare_payload["status"] == "ready"
            create_context = contextvars.copy_context()
            assert "已创建" in create_context.run(
                create_chapter.invoke,
                {"chapter_name": "一 · 开端"},
            )

            write_context = contextvars.copy_context()
            saved_result = write_context.run(
                create_or_rewrite_script.invoke,
                {
                    "overwrite_content": "雨落在旧站台上。",
                    "chapter_name": "一 · 开端",
                    "work_name": "1-1 初遇",
                },
            )
        finally:
            current_agent_id.reset(agent_token)

    _run_chat_background_context(
        user_id="u-chat",
        project_name="p-chat",
        is_admin=False,
        locale="zh-CN",
        llm_usage_context="task:prewrite-regression",
        chat_agent_id="agent_director",
        chat_context_key="global",
        export_format="novel",
        callback=callback,
    )

    saved_payload = json.loads(saved_result)
    assert "已保存" in saved_payload["message"]
    from core.utils import get_project_stories_path

    saved_relative_path = saved_payload["path"]
    saved_path = Path(get_project_stories_path("u-chat", "p-chat")) / saved_relative_path
    assert saved_path.suffix == ".md"
    assert saved_path.read_text(encoding="utf-8") == "雨落在旧站台上。"

    from story import routes_files

    stories_path = Path(get_project_stories_path("u-chat", "p-chat"))
    project_path = stories_path.parent
    monkeypatch.setattr(routes_files, "ensure_project_stories_directory", lambda *_args, **_kwargs: str(stories_path))
    monkeypatch.setattr(routes_files, "get_project_path", lambda *_args, **_kwargs: str(project_path))
    file_tree = asyncio.run(routes_files.get_story_files(
        "p-chat",
        format="novel",
        user={"user_id": "u-chat"},
    ))
    children = file_tree[0]["children"]
    assert len(children) == 1
    assert children[0]["path"] == "一 · 开端/1-1 初遇.md"
    assert children[0]["format"] == "novel"
    assert children[0]["meta"] == {"chap": "001", "scene": "001", "order": "001001"}


def test_auto_write_emits_prewrite_before_writing_scene(monkeypatch, tmp_path: Path) -> None:
    from agents.routes import auto_write
    from agents.scriptwriter_prewrite import ScriptwriterPreWriteResult

    class FakeWriter:
        def __init__(self, _user_id: str) -> None:
            self.llm = object()

        @staticmethod
        def _build_chr_reference(_chr_map) -> str:
            return "[旁白] = 旁白叙述"

        @staticmethod
        def _clean_model_visible_arc_text(value) -> str:
            return str(value or "")

    state_updates: list[dict] = []
    prewrite_tool_payloads: list[dict] = []
    monkeypatch.setattr(auto_write, "ScriptwriterAgent", FakeWriter)
    monkeypatch.setattr(auto_write, "begin_auto_write_run", lambda *_args, **_kwargs: {})

    def fake_patch_state(*_args, **fields):
        state_updates.append(dict(fields))
        return {"updatedAt": "now", **fields}

    monkeypatch.setattr(auto_write, "patch_auto_write_state", fake_patch_state)
    monkeypatch.setattr(auto_write, "get_project_path", lambda *_args: str(tmp_path))
    monkeypatch.setattr(auto_write, "load_worldview", lambda *_args: "世界观")
    monkeypatch.setattr(auto_write, "load_all_roles", lambda *_args: ("角色", {1: "林舟"}))
    monkeypatch.setattr(auto_write, "load_full_outline", lambda *_args: "完整大纲")
    monkeypatch.setattr(auto_write, "load_project_style_profile", lambda **_kwargs: None)
    monkeypatch.setattr(auto_write, "get_project_story_tags", lambda *_args: {})
    monkeypatch.setattr(auto_write, "build_story_tags_hint", lambda _tags: "")
    monkeypatch.setattr(auto_write, "build_scene_context", lambda *_args, **_kwargs: "前文上下文")
    scene_path = tmp_path / "stories" / "一 · 开端" / "1-1 初遇.arc"
    monkeypatch.setattr(
        auto_write,
        "resolve_planned_scene_file_path",
        lambda *_args, **_kwargs: (str(scene_path), False, None),
    )
    def fake_prewrite(*_args, **kwargs):
        callback = kwargs.get("on_tool_progress")
        lifecycle_callback = kwargs.get("on_lifecycle_event")
        if lifecycle_callback is not None:
            lifecycle_callback({"event": "model_request_started", "attempt": 1, "max_attempts": 4})
        if callback is not None:
            callback("story_memory_tool")
        if lifecycle_callback is not None:
            lifecycle_callback({
                "event": "tool_succeeded",
                "tool_name": "story_memory_tool",
                "attempt": 1,
                "max_attempts": 4,
                "result": "已读取事实",
            })
        if callback is not None:
            callback("create_chapter")
            callback("create_or_rewrite_script")
        scene_path.parent.mkdir(parents=True, exist_ok=True)
        scene_path.write_text(
            "# 初遇\n<conception>\n本场建立雨夜悬念。\n</conception>\n[旁白]\n已保存正文可见内容足够长可以落盘",
            encoding="utf-8",
        )
        return ScriptwriterPreWriteResult(
            receipt_id="autonomous",
            brief="任务包",
            research_context="事实",
            planning_note="",
            tools_used=(),
            saved_payload={
                "status": "saved",
                "path": "一 · 开端/1-1 初遇.arc",
                "written_chars": 12,
            },
            written_content="[旁白]\n已保存正文可见内容足够长可以落盘",
        )

    monkeypatch.setattr(auto_write, "run_autonomous_scriptwriter_creation", fake_prewrite)

    outline = {
        "nodes": [{
            "type": "chapter",
            "title": "一 · 开端",
            "chapter": 1,
            "description": "建立悬念",
            "children": [{
                "title": "1-1 初遇",
                "description": "两人在雨夜相遇",
                "characters": ["林舟"],
            }],
        }],
    }
    async def collect_statuses() -> list[str]:
        stream = auto_write.generate_script_stream(
            "u4",
            "p4",
            outline,
            mode="continuous_write",
            export_format="arc",
            prewrite_tool_callback=prewrite_tool_payloads.append,
        )
        statuses: list[str] = []
        try:
            async for raw_event in stream:
                payload = json.loads(raw_event.removeprefix("data: ").strip())
                statuses.append(str(payload.get("status") or ""))
        finally:
            await stream.aclose()
        return statuses

    statuses = asyncio.run(collect_statuses())

    assert statuses.index("prewrite") < statuses.index("writing_scene")
    assert statuses.index("writing_scene") < statuses.index("scene_completed")
    assert statuses.index("scene_completed") < statuses.index("scene_saved")
    assert statuses[-1] == "complete"
    assert scene_path.read_text(encoding="utf-8").count("<conception>") == 1
    tool_started_payload = next(
        item for item in prewrite_tool_payloads
        if item.get("tool_name") == "story_memory_tool"
    )
    from agents.auto_write_service import _prewrite_tool_event

    replay_payload = json.loads(
        _prewrite_tool_event(tool_started_payload).removeprefix("data: ").strip()
    )
    assert replay_payload["status"] == "prewrite_tool"
    assert replay_payload["tool_name"] == "story_memory_tool"
    phase_sequence = [item.get("phase") for item in state_updates if item.get("phase")]
    assert phase_sequence[0] == "prewrite"
    assert "writing" in phase_sequence
    assert any(item.get("phaseToolName") == "story_memory_tool" for item in state_updates)
    assert any(item.get("phaseEvent") == "model_request_started" for item in state_updates)
    assert any(item.get("phaseEvent") == "tool_succeeded" for item in state_updates)
    assert state_updates[-1]["writeStarted"] is True

    def failing_creation(*_args, **kwargs):
        lifecycle_callback = kwargs.get("on_lifecycle_event")
        if lifecycle_callback is not None:
            lifecycle_callback({
                "event": "model_request_failed",
                "attempt": 1,
                "max_attempts": 4,
                "error": "上游连接被迫停止",
            })
        raise RuntimeError("上游连接被迫停止")

    monkeypatch.setattr(auto_write, "run_autonomous_scriptwriter_creation", failing_creation)
    state_updates.clear()

    async def collect_failure_statuses() -> list[dict]:
        stream = auto_write.generate_script_stream(
            "u4",
            "p4",
            outline,
            mode="continuous_write",
            export_format="arc",
        )
        return [
            json.loads(raw_event.removeprefix("data: ").strip())
            async for raw_event in stream
        ]

    failure_events = asyncio.run(collect_failure_statuses())
    assert failure_events[-1]["status"] == "error"
    assert "上游连接被迫停止" in failure_events[-1]["message"]
    terminal_state = state_updates[-1]
    assert terminal_state["status"] == "error"
    assert terminal_state["availableResumeChapterIndex"] == 0
    assert terminal_state["availableResumeSceneIndex"] == 0
    assert "上游连接被迫停止" in terminal_state["lastError"]
