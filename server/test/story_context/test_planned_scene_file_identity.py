from __future__ import annotations

from pathlib import Path

import pytest


def test_find_scene_file_by_identity_uses_filename_meta_across_project(tmp_path: Path) -> None:
    from story.file_naming import find_scene_file_by_identity

    stories_path = tmp_path / "stories"
    old_dir = stories_path / "旧章节名"
    old_dir.mkdir(parents=True)
    existing = old_dir / "旧标题.__spark__chap=001.scene=003.order=001003.arc"
    existing.write_text("# 1-3 旧标题\n[-1]\n旧正文", encoding="utf-8")

    found, parsed = find_scene_file_by_identity(
        str(stories_path),
        1,
        3,
        file_format="arc",
    )

    assert found == str(existing)
    assert parsed is not None
    assert parsed["chapter_num"] == 1
    assert parsed["scene_num"] == 3


def test_scene_identity_parser_accepts_legacy_prefixes_and_chinese_numbers() -> None:
    from story.file_naming import canonical_scene_display_name, parse_scene_identity_from_title

    assert parse_scene_identity_from_title("场景 3-4：旧标题") == (3, 4)
    assert parse_scene_identity_from_title("第三-四 旧标题") == (3, 4)
    assert parse_scene_identity_from_title("1-0 错误编号") == (1, None)
    assert canonical_scene_display_name("场景 第三-四：旧标题", 3, 4) == "3-4 旧标题"


def test_outline_parser_separates_protocol_numbers_from_reader_facing_titles() -> None:
    from story.outline_parser import parse_outline_markup

    outline = parse_outline_markup(
        "## Chapter 3: 三 · 深水\n\n### 场景 3-4：旧船返航\n\n正文说明"
    )

    chapter = outline["nodes"][0]
    scene = chapter["children"][0]
    assert chapter["chapter"] == 3
    assert chapter["title"] == "三 · 深水"
    assert scene["chapter_num"] == 3
    assert scene["scene_num"] == 4
    assert scene["title"] == "旧船返航"


def test_auto_write_state_hides_physical_filename_metadata(monkeypatch, tmp_path: Path) -> None:
    from agents.routes.auto_write_state import load_auto_write_state, save_auto_write_state

    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    physical = "一 · 开端/1-1 初遇.__spark__chap=001.scene=001.order=001001.arc"
    saved = save_auto_write_state("7", "demo", {
        "lastSavedFilename": physical,
        "generatedFiles": [physical],
        "generatedSceneFiles": [physical],
    })
    loaded = load_auto_write_state("7", "demo")

    for state in (saved, loaded):
        assert state["lastSavedFilename"] == "一 · 开端/1-1 初遇.arc"
        assert state["generatedFiles"] == ["一 · 开端/1-1 初遇.arc"]
        assert state["generatedSceneFiles"] == ["一 · 开端/1-1 初遇.arc"]


def test_find_scene_file_by_identity_reuses_legacy_filename_without_metadata(tmp_path: Path) -> None:
    from story.file_naming import find_scene_file_by_identity

    stories_path = tmp_path / "stories"
    stories_path.mkdir()
    existing = stories_path / "旧章节" / "第三-四 旧标题.arc"
    existing.parent.mkdir()
    existing.write_text("# 第三-四 旧标题\n旧正文", encoding="utf-8")

    found, parsed = find_scene_file_by_identity(str(stories_path), 3, 4, file_format="arc")

    assert found == str(existing)
    assert parsed and parsed["display_name"] == "第三-四 旧标题"


def test_resolve_planned_scene_file_path_overwrites_existing_identity(tmp_path: Path) -> None:
    from story.file_naming import resolve_planned_scene_file_path

    stories_path = tmp_path / "stories"
    old_dir = stories_path / "一 · 开端"
    old_dir.mkdir(parents=True)
    existing = old_dir / "1-1 初遇.__spark__chap=001.scene=001.order=001001.arc"
    existing.write_text("# 1-1 初遇\n[-1]\n旧正文", encoding="utf-8")

    resolved, existed, _ = resolve_planned_scene_file_path(
        str(stories_path),
        1,
        1,
        "1-1 重写后的标题",
        chapter_dir_name="一 · 开端",
        file_format="arc",
    )

    assert resolved == str(existing)
    assert existed is True


def test_resolve_planned_scene_file_path_blocks_ambiguous_duplicates(tmp_path: Path) -> None:
    import pytest

    from story.file_naming import DuplicateSceneIdentityError, resolve_planned_scene_file_path

    stories_path = tmp_path / "stories"
    for folder, title in (("旧章节", "第三-四 旧标题"), ("新章节", "3-4 新标题")):
        target = stories_path / folder / f"{title}.arc"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {title}\n正文", encoding="utf-8")

    with pytest.raises(DuplicateSceneIdentityError) as exc_info:
        resolve_planned_scene_file_path(
            str(stories_path),
            3,
            4,
            "3-4 再次生成",
            chapter_dir_name="三 · 新章节名",
            file_format="arc",
        )

    assert sorted(exc_info.value.paths) == ["新章节/3-4 新标题.arc", "旧章节/第三-四 旧标题.arc"]


def test_create_or_rewrite_script_overwrites_existing_planned_scene(monkeypatch, tmp_path: Path) -> None:
    from core.request_context import current_export_format, current_project_name, current_user_id
    from agents.tools.scriptwriter import create_or_rewrite_script

    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    stories_path = tmp_path / "uid_7" / "projects" / "demo" / "stories" / "一 · 开端"
    stories_path.mkdir(parents=True)
    existing = stories_path / "1-1 初遇.__spark__chap=001.scene=001.order=001001.arc"
    existing.write_text("# 1-1 初遇\n[-1]\n旧正文", encoding="utf-8")

    user_token = current_user_id.set("7")
    project_token = current_project_name.set("demo")
    format_token = current_export_format.set("arc")
    try:
        result = create_or_rewrite_script.invoke(
            {
                "chapter_name": "一 · 开端",
                "work_name": "1-1 初遇",
                "overwrite_content": "[-1]\n新正文",
            }
        )
    finally:
        current_export_format.reset(format_token)
        current_project_name.reset(project_token)
        current_user_id.reset(user_token)

    files = list(stories_path.glob("*.arc"))
    assert files == [existing]
    assert "已覆盖" in result
    assert "新正文" in existing.read_text(encoding="utf-8")
    assert "旧正文" not in existing.read_text(encoding="utf-8")


def test_create_or_rewrite_script_reuses_legacy_scene_and_rejects_zero_scene(monkeypatch, tmp_path: Path) -> None:
    from core.request_context import current_export_format, current_project_name, current_user_id
    from agents.tools.scriptwriter import create_or_rewrite_script

    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    monkeypatch.setattr("agents.story_memory.enqueue_scene_memory_write", lambda **_kwargs: None)
    stories_path = tmp_path / "uid_7" / "projects" / "demo" / "stories" / "一 · 开端"
    stories_path.mkdir(parents=True)
    existing = stories_path / "第三-四 旧标题.arc"
    existing.write_text("# 第三-四 旧标题\n旧正文", encoding="utf-8")

    user_token = current_user_id.set("7")
    project_token = current_project_name.set("demo")
    format_token = current_export_format.set("arc")
    try:
        reused = create_or_rewrite_script.invoke({
            "chapter_name": "三 · 改名后的章节",
            "work_name": "场景 第三-四：改名后的场景",
            "overwrite_content": "新正文",
        })
        rejected = create_or_rewrite_script.invoke({
            "chapter_name": "一 · 开端",
            "work_name": "1-0 错误场景",
            "overwrite_content": "不应落盘",
        })
    finally:
        current_export_format.reset(format_token)
        current_project_name.reset(project_token)
        current_user_id.reset(user_token)

    assert "已覆盖" in reused
    assert existing.read_text(encoding="utf-8").endswith("新正文")
    assert "必须是大于 0" in rejected
    assert len(list(stories_path.glob("*.arc"))) == 1


@pytest.mark.parametrize(
    ("export_format", "content", "expected_reason"),
    [
        ("arc", "<conception>只有构思，没有 ARC 正文。</conception>", "body_empty"),
        ("arc", "<conception>未闭合的构思", "markup_tag_unbalanced:conception"),
        ("novel", "<conception>未闭合的构思", "markup_tag_unbalanced:conception"),
    ],
)
def test_create_or_rewrite_script_rejects_metadata_only_or_unbalanced_content(
    monkeypatch,
    tmp_path: Path,
    export_format: str,
    content: str,
    expected_reason: str,
) -> None:
    from core.request_context import current_export_format, current_project_name, current_user_id
    from agents.tools.scriptwriter import create_or_rewrite_script

    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    monkeypatch.setattr("agents.story_memory.enqueue_scene_memory_write", lambda **_kwargs: None)
    stories_path = tmp_path / "uid_7" / "projects" / "demo" / "stories" / "一 · 开端"
    stories_path.mkdir(parents=True)
    existing = stories_path / (
        "1-1 初遇.__spark__chap=001.scene=001.order=001001."
        + ("arc" if export_format == "arc" else "md")
    )
    existing.write_text("旧正文", encoding="utf-8")

    user_token = current_user_id.set("7")
    project_token = current_project_name.set("demo")
    format_token = current_export_format.set(export_format)
    try:
        result = create_or_rewrite_script.invoke(
            {
                "chapter_name": "一 · 开端",
                "work_name": "1-1 初遇",
                "overwrite_content": content,
            }
        )
    finally:
        current_export_format.reset(format_token)
        current_project_name.reset(project_token)
        current_user_id.reset(user_token)

    assert result.startswith((
        "创建/重写剧本失败：正文落盘校验未通过",
        "创建/重写剧本失败：正文没有可见内容",
    ))
    assert expected_reason in result
    assert existing.read_text(encoding="utf-8") == "旧正文"


def test_auto_write_scene_plan_uses_outline_position_instead_of_bad_title_number(monkeypatch, tmp_path: Path) -> None:
    from agents.routes.auto_write_state import build_auto_write_scene_plan

    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    outline = {
        "nodes": [{
            "type": "chapter",
            "title": "一 · 开端",
            "children": [{"type": "scene", "title": "场景 1-0：错误编号"}],
        }],
    }

    plan = build_auto_write_scene_plan("7", "demo", outline, export_format="arc")

    assert plan[0]["sceneTitle"] == "1-1 错误编号"
    assert plan[0]["filename"] == "1-1 错误编号.arc"


def test_read_chapter_scene_reads_nested_persisted_arc(monkeypatch, tmp_path: Path) -> None:
    from agents.tools.shared_read import read_chapter_scene
    from core.request_context import current_project_name, current_user_id

    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    stories_path = tmp_path / "uid_7" / "projects" / "demo" / "stories" / "一 · 开端"
    stories_path.mkdir(parents=True)
    story_path = stories_path / "1-1 初遇.__spark__chap=001.scene=001.order=001001.arc"
    story_path.write_text("# 1-1 初遇\n[旁白]\n嵌套目录中的正文。", encoding="utf-8")

    user_token = current_user_id.set("7")
    project_token = current_project_name.set("demo")
    try:
        result = read_chapter_scene.invoke({"chapter_index": 0, "scene_index": 0})
    finally:
        current_project_name.reset(project_token)
        current_user_id.reset(user_token)

    assert "已落盘剧本" in result
    assert "一 · 开端/1-1 初遇" in result
    assert "嵌套目录中的正文" in result


def test_agent_context_provider_lists_and_reads_novel_files(monkeypatch, tmp_path: Path) -> None:
    from agents.context_provider import AgentContextProvider

    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    stories_path = tmp_path / "uid_7" / "projects" / "demo" / "stories" / "第一卷"
    stories_path.mkdir(parents=True)
    novel_path = stories_path / "第一章.md"
    arc_path = stories_path / "第一场.arc"
    novel_path.write_text("这是小说正文。", encoding="utf-8")
    arc_path.write_text("# 第一场\n[-1]\n这是剧本正文。", encoding="utf-8")

    provider = AgentContextProvider("7", "demo")
    scene_list = provider.get_scene_list()

    assert "第一章" in scene_list
    assert "第一场" in scene_list
    novel_content = provider.get_scene_content("第一卷/第一章.md")
    assert "```markdown" in novel_content
    assert "这是小说正文。" in novel_content


def test_scene_context_groups_nested_scene_files_by_chapter_identity(monkeypatch, tmp_path: Path) -> None:
    from agents.routes.context_builder import build_scene_context
    from story.file_naming import build_scene_story_filename

    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    stories_path = tmp_path / "uid_32" / "projects" / "demo" / "stories"
    chapter_one = stories_path / "第一章"
    chapter_two = stories_path / "第二章"
    chapter_one.mkdir(parents=True)
    chapter_two.mkdir(parents=True)

    files = [
        (chapter_one, build_scene_story_filename(1, 1, "开端"), "# 开端\n[-1]\n第一章第一场。"),
        (chapter_one, build_scene_story_filename(1, 2, "尾声"), "# 尾声\n[-1]\n第一章最后一场。"),
        (chapter_two, build_scene_story_filename(2, 1, "承接"), "# 承接\n[-1]\n第二章已完成场景。"),
        (chapter_two, build_scene_story_filename(2, 2, "当前"), "# 当前\n[-1]\n不应进入前文。"),
    ]
    for directory, filename, content in files:
        (directory / filename).write_text(content, encoding="utf-8")

    context = build_scene_context(
        "32",
        "demo",
        1,
        current_scene_index=1,
    )

    assert "第一章最后一场" in context
    assert "第二章已完成场景" in context
    assert "第一章第一场" not in context
    assert "不应进入前文" not in context


def test_scene_context_keeps_only_recent_chapter_tails_and_current_scenes(monkeypatch, tmp_path: Path) -> None:
    from agents.routes.context_builder import build_scene_context
    from story.file_naming import build_scene_story_filename

    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    stories_path = tmp_path / "uid_44" / "projects" / "demo" / "stories"
    for chapter_number in range(1, 13):
        chapter_dir = stories_path / f"第{chapter_number}章"
        chapter_dir.mkdir(parents=True)
        scene_total = 5 if chapter_number == 12 else 1
        for scene_number in range(1, scene_total + 1):
            filename = build_scene_story_filename(
                chapter_number,
                scene_number,
                f"第{chapter_number}章第{scene_number}场",
            )
            (chapter_dir / filename).write_text(
                f"# 第{chapter_number}章第{scene_number}场\n[旁白]\n正文-{chapter_number}-{scene_number}。",
                encoding="utf-8",
            )

    context = build_scene_context(
        "44",
        "demo",
        11,
        current_scene_index=5,
    )

    assert "正文-10-1" in context
    assert "正文-11-1" in context
    assert "正文-9-1" not in context
    assert "正文-12-3" in context
    assert "正文-12-4" in context
    assert "正文-12-5" in context
    assert "正文-12-1" not in context
    assert "正文-12-2" not in context


def test_patch_script_updates_nested_arc(monkeypatch, tmp_path: Path) -> None:
    from agents.tools.scriptwriter import patch_script
    from core.request_context import current_project_name, current_user_id

    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "core.project_settings.get_visual_illustration_settings",
        lambda user_id, project_name: {"max_per_scene": 2, "min_node_gap": 1},
    )
    monkeypatch.setattr(
        "core.project_settings.is_visual_illustration_enabled",
        lambda user_id, project_name: False,
    )
    stories_path = tmp_path / "uid_7" / "projects" / "demo" / "stories" / "一 · 开端"
    stories_path.mkdir(parents=True)
    story_path = stories_path / "1-1 初遇.__spark__chap=001.scene=001.order=001001.arc"
    story_path.write_text("# 1-1 初遇\n[旁白]\n修改前正文。", encoding="utf-8")

    user_token = current_user_id.set("7")
    project_token = current_project_name.set("demo")
    try:
        result = patch_script.invoke({
            "search_text": "修改前正文。",
            "replace_text": "修改后正文。",
        })
    finally:
        current_project_name.reset(project_token)
        current_user_id.reset(user_token)

    assert "已成功局部更新" in result
    assert "修改后正文" in story_path.read_text(encoding="utf-8")
