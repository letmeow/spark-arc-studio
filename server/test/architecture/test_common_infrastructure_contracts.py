"""守护公共 Patch、Token 分块与数据库迁移的统一基础设施。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.tools.common import _apply_patch
from core.file_ingest.chunking import TokenTextSplitter
from core.migration_specs import get_db_path, get_db_spec, get_version_dir, iter_db_names, sqlite_url
from story.text_metrics import count_story_body_chars


def test_apply_patch_exact_append_whitespace_and_json_validation(tmp_path: Path) -> None:
    target = tmp_path / "story.json"
    target.write_text('{"name": "旧名", "items": [1]}\n', encoding="utf-8")

    result = _apply_patch(str(target), "旧名", "新名", validate_json=True, file_label="story.json")
    assert "已成功局部更新" in result
    assert json.loads(target.read_text(encoding="utf-8"))["name"] == "新名"

    bad_result = _apply_patch(str(target), '"items": [1]', '"items": [', validate_json=True, file_label="story.json")
    assert bad_result.startswith("局部修改失败")
    assert json.loads(target.read_text(encoding="utf-8"))["items"] == [1]

    loose = tmp_path / "loose.txt"
    loose.write_text("第一行   很多空格\n\n\n第二行\n", encoding="utf-8")
    loose_result = _apply_patch(str(loose), "第一行 很多空格\n\n第二行", "替换后", file_label="loose.txt")
    assert "已成功局部更新" in loose_result
    assert loose.read_text(encoding="utf-8") == "替换后\n第二行\n"

    append_result = _apply_patch(str(loose), "", "追加", file_label="loose.txt")
    assert "末尾追加" in append_result
    assert loose.read_text(encoding="utf-8").endswith("\n追加")

    before_validation = loose.read_text(encoding="utf-8")

    def reject_content(_original: str, _candidate: str) -> None:
        raise ValueError("测试拒绝")

    rejected = _apply_patch(
        str(loose),
        "追加",
        "不应写入",
        validate_content=reject_content,
        file_label="loose.txt",
    )
    assert rejected.startswith("局部修改失败")
    assert loose.read_text(encoding="utf-8") == before_validation


def test_story_body_char_count_excludes_format_and_conception() -> None:
    arc = """# 1-1 初遇
<conception>这里不计入正文</conception>
[旁白]
雨落旧站台。
[林夏]
你来了。
"""
    assert count_story_body_chars(arc, "arc") == len("雨落旧站台你来了")

    metadata_only_arc = "# 1-2 空场景\n<conception>这里仍然不计入正文</conception>"
    assert count_story_body_chars(metadata_only_arc, "arc") == 0

    novel = "# 标题\n<conception>隐藏构思</conception>雨落在旧站台。"
    assert count_story_body_chars(novel, "novel") == len("标题雨落在旧站台")


def test_token_text_splitter_keeps_stable_chunk_metadata(monkeypatch) -> None:
    # 统一入口 stub：estimate_text_tokens 及其底层 _estimate_tokens；
    # 旧 estimate_tokens 别名已不再被切分器使用。
    stub = lambda text, model=None: len(text)
    monkeypatch.setattr("core.file_ingest.chunking.estimate_text_tokens", stub)
    monkeypatch.setattr("core.file_ingest.chunking._estimate_tokens", stub)

    text = "甲" * 8 + "。" + "乙" * 8 + "。" + "丙" * 3 + "。"
    splitter = TokenTextSplitter(
        chunk_tokens=10,
        min_tokens=1,
        tail_merge_threshold_ratio=0.5,
        tail_merge_cap_ratio=1.4,
    )
    chunks = splitter.split(text)

    # 尾部合并的承诺是“合完仍是合法单窗”：9 + 1 + 4 = 14 > 10 不合，
    # 保留 3 片；合完超窗的合并一律不做（读窗不可用比多一片更严重）。
    assert len(chunks) == 3
    assert [chunk.index for chunk in chunks] == [0, 1, 2]
    assert all(chunk.total == 3 for chunk in chunks)
    assert chunks[1].previous_tail == chunks[0].text[-splitter.TAIL_CHARS:]

    _chunks, info = splitter.split_with_info(text)
    assert info["chunk_count"] == 3
    assert info["chunk_tokens_target"] == 10


def test_migration_specs_keep_known_database_branches(monkeypatch, tmp_path: Path) -> None:
    users_db = tmp_path / "users.sqlite"
    monkeypatch.setenv("SPARKARC_ALEMBIC_USERS_DB", str(users_db))

    assert set(iter_db_names()) == {"users", "llm"}
    assert get_db_spec("users").version_subdir == "users"
    assert get_db_path("users") == users_db.resolve()
    assert get_version_dir("users", base_dir=tmp_path) == tmp_path.resolve() / "alembic" / "versions" / "users"
    assert sqlite_url(users_db).startswith("sqlite:///")


@pytest.mark.parametrize(
    ("missing_columns", "expected_action"),
    [([], "stamp"), (["llm_platforms.recharge_url"], "heal")],
)
def test_auto_migrate_recovers_duplicate_schema_objects(
    monkeypatch,
    tmp_path: Path,
    missing_columns: list[str],
    expected_action: str,
) -> None:
    from core import auto_migrate

    db_path = tmp_path / "llm.sqlite"
    actions: list[str] = []

    monkeypatch.setattr(auto_migrate, "get_database_url", lambda db_name: "sqlite:///fake")
    monkeypatch.setattr(auto_migrate, "_get_db_path", lambda db_name: str(db_path))
    monkeypatch.setattr(auto_migrate, "_get_current_db_revision", lambda path: "old")
    monkeypatch.setattr(auto_migrate, "_get_head_revision", lambda base_dir, db_name: "head")
    monkeypatch.setattr(auto_migrate, "_build_alembic_config", lambda base_dir, db_name: object())
    monkeypatch.setattr(
        auto_migrate.command,
        "upgrade",
        lambda cfg, target: (_ for _ in ()).throw(
            RuntimeError("(sqlite3.OperationalError) duplicate column name: recharge_url")
        ),
    )
    monkeypatch.setattr(
        auto_migrate,
        "_describe_schema_drift",
        lambda db_name, db_path: {
            "missing_tables": [],
            "missing_columns": missing_columns,
            "extra_tables": [],
            "extra_columns": [],
        },
    )
    monkeypatch.setattr(auto_migrate, "_stamp_head", lambda *args: actions.append("stamp"))
    monkeypatch.setattr(auto_migrate, "_heal_orphan_revision", lambda *args: actions.append("heal"))

    auto_migrate.run_db_upgrade("llm", str(tmp_path))

    assert actions == [expected_action]
