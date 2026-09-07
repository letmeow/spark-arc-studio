from __future__ import annotations

from agents.agent_tools import TOOLS_BY_NAME as FACADE_TOOLS_BY_NAME
from agents.agent_tools import get_tools_for_agent as facade_get_tools_for_agent
from agents.tools.registry import ALL_TOOLS, TOOLS_BY_NAME, get_tools_for_agent
from agents.tools.stream_events import (
    build_tool_stream_event,
    get_tool_result_failure_message,
    get_tool_ui_binding,
    is_tool_result_failure,
    normalize_tool_name,
)
from core.request_context import current_project_name, current_user_id


CORE_AGENT_IDS = {
    "agent_director",
    "agent_muse",
    "agent_lorebook",
    "agent_showrunner",
    "agent_scriptwriter",
    "agent_critic",
    "agent_style",
}


def tool_names(agent_id: str, user_id: str | None = None) -> set[str]:
    return {tool.name for tool in get_tools_for_agent(agent_id, user_id=user_id)}


def test_tool_registry_has_stable_unique_truth_source() -> None:
    all_names = [tool.name for tool in ALL_TOOLS]
    assert all_names
    assert set(all_names) == set(TOOLS_BY_NAME)
    assert FACADE_TOOLS_BY_NAME is TOOLS_BY_NAME
    assert facade_get_tools_for_agent is get_tools_for_agent


def test_core_agent_tool_boundaries() -> None:
    assert "delegate_task" in tool_names("agent_director")
    assert "rewrite_inspiration" in tool_names("agent_muse")
    assert {"rewrite_worldview", "rewrite_all_characters", "update_character", "create_character_relation", "web_search"} <= tool_names("agent_lorebook")
    assert {"rewrite_synopsis", "rewrite_beat_sheet", "rewrite_outline"} <= tool_names("agent_showrunner")
    assert {
        "prepare_script_creation",
        "create_chapter",
        "create_or_rewrite_script",
        "batch_rename_chapters",
        "batch_rename_scenes",
        "batch_update_story_metadata",
        "patch_script",
        "update_project_story_tags",
    } <= tool_names("agent_scriptwriter")
    assert "delegate_task" not in tool_names("agent_scriptwriter")
    assert "delegate_task" not in tool_names("agent_critic")
    assert tool_names("agent_style") == set()


def test_story_batch_tools_expose_single_purpose_schemas() -> None:
    expected_fields = {
        "batch_rename_chapters": {"renames"},
        "batch_rename_scenes": {"renames"},
        "batch_update_story_metadata": {"updates"},
    }
    for tool_name, fields in expected_fields.items():
        tool = TOOLS_BY_NAME[tool_name]
        assert set(tool.args_schema.model_fields) == fields


def test_pipeline_persist_registry_includes_all_scriptwriter_writes() -> None:
    from agents.tools.registry import PIPELINE_PERSIST_TOOL_NAMES

    assert {
        "create_chapter",
        "organize_scenes_to_chapter",
        "rename_chapter",
        "rename_scene",
        "reorder_chapters",
        "reorder_scenes",
        "batch_rename_chapters",
        "batch_rename_scenes",
        "batch_update_story_metadata",
    } <= PIPELINE_PERSIST_TOOL_NAMES


def test_showrunner_keeps_continuity_tool_schema_stable_when_story_appears(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    user_token = current_user_id.set("88")
    project_token = current_project_name.set("demo")
    continuity_tools = {
        "story_memory_tool",
        "graph_rag_tool",
        "list_chapters",
        "read_chapter_scene",
        "read_chapter_outline_raw",
        "search_project",
        "semantic_search",
    }
    try:
        fresh_tools = tool_names("agent_showrunner", user_id="88")
        assert continuity_tools <= fresh_tools
        assert {"rewrite_synopsis", "rewrite_beat_sheet", "rewrite_outline"} <= fresh_tools

        story_path = tmp_path / "uid_88" / "projects" / "demo" / "stories" / "一 · 开端" / "1.1.arc"
        story_path.parent.mkdir(parents=True)
        story_path.write_text("---scene 1-1\n[旁白] 已写正文", encoding="utf-8")

        existing_tools = tool_names("agent_showrunner", user_id="88")
        assert continuity_tools <= existing_tools
        assert existing_tools == fresh_tools
    finally:
        current_project_name.reset(project_token)
        current_user_id.reset(user_token)


def test_long_write_tools_expose_batching_and_append_guidance() -> None:
    character_schema = TOOLS_BY_NAME["rewrite_all_characters"].args_schema.model_fields
    assert "最多约 5 个角色" in character_schema["overwrite_content"].description
    assert "append" in character_schema
    assert character_schema["append"].default is True
    assert "整体替换" in character_schema["append"].description
    assert "显式传 false" in character_schema["append"].description

    outline_schema = TOOLS_BY_NAME["rewrite_outline"].args_schema.model_fields
    assert "每批约 10 个场景" in outline_schema["overwrite_content"].description
    assert "patch_outline" in outline_schema["overwrite_content"].description

    patch_schema = TOOLS_BY_NAME["patch_outline"].args_schema.model_fields
    assert "追加到大纲末尾" in patch_schema["search_text"].description


def test_get_tools_for_agent_is_defined_for_every_core_agent() -> None:
    for agent_id in CORE_AGENT_IDS:
        tools = get_tools_for_agent(agent_id)
        assert isinstance(tools, list)
        assert all(getattr(tool, "name", "") for tool in tools)


def test_director_longread_tools_come_from_single_facade() -> None:
    """滑窗工具必须走统一底座：registry 与门面同源，导演一次性配齐。"""
    names = tool_names("agent_director")
    assert {
        "read_attachment_chunk",
        "read_longread_window",
        "describe_longread_source",
        "note_window_clues",
        "read_worldview_window",
    } <= names
    assert facade_get_tools_for_agent is get_tools_for_agent


def test_longread_and_search_tool_details_expose_pointer_only() -> None:
    """面板展开只允许指针/搜了什么：读窗不暴露正文，检索不暴露命中正文。

    唯一的例外是 note_window_clues：clues 是 Agent 写给用户看的线索结论，
    必须展示，否则面板只有机器指针。
    """
    read_evt = build_tool_stream_event(
        "tool_exec_started",
        "read_longread_window",
        source_agent="agent_director",
        tool_call_key="call-1",
        tool_input={"source_id": "abc", "chunk_index": 2, "secret": "x"},
        tool_result="窗口正文不应展示",
    )
    assert read_evt["tool_input"] == {"source_id": "abc", "chunk_index": 2}
    assert "tool_result" not in read_evt

    search_evt = build_tool_stream_event(
        "tool_exec_started",
        "search_project",
        source_agent="agent_director",
        tool_call_key="call-2",
        tool_input={"pattern": "玉佩", "scope": ["attachment"], "max_results": 20},
        tool_result="命中正文不应展示",
    )
    assert search_evt["tool_input"] == {"pattern": "玉佩", "scope": ["attachment"], "max_results": 20}
    assert "tool_result" not in search_evt

    note_evt = build_tool_stream_event(
        "tool_exec_started",
        "note_window_clues",
        source_agent="agent_director",
        tool_call_key="call-3",
        tool_input={
            "source_id": "abc",
            "chunk_index": 30,
            "clue_type": "矛盾",
            "importance": 5,
            "clues": ["林淮生在窗口30现身"],
            "secret": "x",
        },
        tool_result="已记录 1 条线索",
    )
    assert note_evt["tool_input"] == {
        "source_id": "abc",
        "chunk_index": 30,
        "clue_type": "矛盾",
        "importance": 5,
        "clues": ["林淮生在窗口30现身"],
    }
    assert "tool_result" not in note_evt


def test_note_window_clues_clues_are_never_truncated() -> None:
    """记账 clues 全量展示：3000+ 字符不断尾，不出现省略标记。"""
    long_clue = "林" * 3000
    many_clues = [f"线索{i:02d}：" + "淮" * 100 for i in range(30)]
    evt = build_tool_stream_event(
        "tool_exec_started",
        "note_window_clues",
        source_agent="agent_director",
        tool_call_key="call-4",
        tool_input={
            "source_id": "abc",
            "chunk_index": 30,
            "clues": [long_clue, *many_clues],
        },
    )
    clues = evt["tool_input"]["clues"]
    assert clues[0] == long_clue
    assert len(clues) == 31
    assert not any("省略" in str(item) for item in clues)


def test_tool_stream_event_injects_ui_metadata_from_backend_binding() -> None:
    evt = build_tool_stream_event(
        "tool_exec_started",
        "rewrite_worldview",
        source_agent="agent_lorebook",
        tool_call_key="call-1",
    )

    assert evt["tool_name"] == "rewrite_worldview"
    assert evt["ui_scope"] == "world"
    assert evt["ui_target"] == "worldview"
    assert "lorebook-refresh-worldview" in evt["ui_refresh_events"]

    tags_evt = build_tool_stream_event("tool_exec_started", "update story tags")
    assert tags_evt["tool_name"] == "update_project_story_tags"
    assert tags_evt["ui_scope"] == "story-tags"
    assert "story-tags-refresh" in tags_evt["ui_refresh_events"]


def test_tool_name_aliases_match_ui_binding_normalization() -> None:
    assert normalize_tool_name("rewrite-worldview") == "rewrite_worldview"
    assert normalize_tool_name("rewrite characters") == "rewrite_all_characters"
    assert normalize_tool_name("update story tags") == "update_project_story_tags"
    assert get_tool_ui_binding("rewrite characters")["target"] == "characters"


def test_character_relation_tool_refreshes_character_surface() -> None:
    binding = get_tool_ui_binding("create_character_relation")
    assert binding == {
        "scope": "world",
        "target": "characters",
        "refresh_events": ["lorebook-refresh-characters", "lorebook-refresh"],
    }
    evt = build_tool_stream_event("tool_exec_started", "create_character_relation")
    assert evt["ui_target"] == "characters"
    assert "lorebook-refresh-characters" in evt["ui_refresh_events"]


def test_web_search_degraded_result_is_not_reported_as_success() -> None:
    assert is_tool_result_failure(
        "web_search",
        "联网搜索暂时不可用（exa）：上游仍未恢复。",
    ) is True
    assert is_tool_result_failure("web_search", "使用 exa 搜索的外部资料如下。") is False
    assert "上游暂不可用" in get_tool_result_failure_message(
        "web_search",
        "联网搜索暂时不可用（exa）：上游仍未恢复。",
    )
    assert "未能完成" in get_tool_result_failure_message(
        "web_search",
        "联网搜索失败（exa）：鉴权失败。",
    )


def test_character_relation_failures_are_not_reported_as_success() -> None:
    result = "创建角色关系失败：未找到终点角色‘乙’。请先落盘角色设定。"
    assert is_tool_result_failure("create_character_relation", result) is True
    assert "创建角色关系失败" in get_tool_result_failure_message("create_character_relation", result)


def test_character_relation_tool_persists_only_existing_characters_and_rejects_duplicates(
    monkeypatch,
    tmp_path,
) -> None:
    from agents.tools.lorebook import create_character_relation
    from core.character_relations import read_character_relations
    from core.character_store import upsert_character

    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    user_token = current_user_id.set("91")
    project_token = current_project_name.set("demo")
    try:
        upsert_character("91", "demo", "0", name="甲", content="")
        upsert_character("91", "demo", "1", name="乙", content="")

        created = create_character_relation.invoke({
            "source_character": "甲",
            "target_character": "乙",
            "relation": "盟友",
            "note": "共同目标",
        })
        assert created.startswith("已创建角色关系")
        assert read_character_relations("91", "demo")[0]["relation"] == "盟友"

        duplicate = create_character_relation.invoke({
            "source_character": "甲",
            "target_character": "乙",
            "relation": "盟友",
        })
        assert duplicate.startswith("创建角色关系失败")
        missing = create_character_relation.invoke({
            "source_character": "甲",
            "target_character": "丙",
            "relation": "亲属",
        })
        assert missing.startswith("创建角色关系失败")
        assert len(read_character_relations("91", "demo")) == 1
    finally:
        current_project_name.reset(project_token)
        current_user_id.reset(user_token)


def test_scriptwriter_domain_failures_are_not_reported_as_success() -> None:
    prewrite_failure = "PreWrite 失败：chapter_name 与 scene_name 均不能为空。"
    write_failure = "创建/重写剧本失败：当前完整场景尚未完成匹配的 PreWrite。"

    assert is_tool_result_failure("prepare_script_creation", prewrite_failure) is True
    assert is_tool_result_failure("create_or_rewrite_script", write_failure) is True
    assert get_tool_result_failure_message("prepare_script_creation", prewrite_failure) == prewrite_failure
