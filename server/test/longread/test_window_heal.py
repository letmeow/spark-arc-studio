"""新口径自愈回归：旧附件（无 estimate_model）超窗读时自动细分并可读。"""

from __future__ import annotations


def _stub_unified_estimator(monkeypatch) -> None:
    """冻结统一估算入口。统一入口是 estimate_text_tokens 及其底层
    _estimate_tokens；stub 旧 estimate_tokens 别名无效。"""
    stub = lambda text, model=None: len(text)
    monkeypatch.setattr(
        "core.file_ingest.chunking.estimate_text_tokens",
        stub,
    )
    monkeypatch.setattr(
        "core.file_ingest.chunking._estimate_tokens",
        stub,
    )


def test_heal_oversized_window_splits_and_persists(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    _stub_unified_estimator(monkeypatch)
    import agents.tools.longread as longread_mod
    import core.project_settings as settings

    monkeypatch.setattr(settings, "LONGREAD_MAX_WINDOW_TOKENS", 10, raising=False)
    monkeypatch.setattr(longread_mod, "LONGREAD_MAX_WINDOW_TOKENS", 10, raising=False)

    from agents.attachment import get_attachment_meta, load_chunks, save_attachment
    from agents.tools.longread import _read_attachment_window_text
    from core.request_context import current_project_name, current_user_id

    meta = save_attachment(
        "90", "demo", "旧.txt", "txt",
        "甲" * 8 + "乙" * 8 + "丙" * 8,
        ["甲" * 8, "乙" * 8 + "丙" * 8],
        24,
    )
    assert meta.estimate_model == ""

    user_token = current_user_id.set("90")
    project_token = current_project_name.set("demo")
    try:
        text, error = _read_attachment_window_text("90", "demo", meta.attachment_id, 1)
    finally:
        current_project_name.reset(project_token)
        current_user_id.reset(user_token)

    assert error == ""
    assert "自愈说明" in text
    assert "乙" in text
    assert get_attachment_meta("90", "demo", meta.attachment_id).chunk_count == 3
    assert len(load_chunks("90", "demo", meta.attachment_id)) == 3


def test_same_caliber_oversized_window_still_rejected(monkeypatch, tmp_path) -> None:
    """新附件（有口径记录）同口径超窗：不自愈，走重传提示。"""
    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    _stub_unified_estimator(monkeypatch)
    import agents.tools.longread as longread_mod
    import core.project_settings as settings

    monkeypatch.setattr(settings, "LONGREAD_MAX_WINDOW_TOKENS", 10, raising=False)
    monkeypatch.setattr(longread_mod, "LONGREAD_MAX_WINDOW_TOKENS", 10, raising=False)

    from agents.attachment import save_attachment
    from agents.tools.longread import _read_attachment_window_text
    from core.request_context import current_project_name, current_user_id

    meta = save_attachment(
        "91", "demo", "新.txt", "txt",
        "甲" * 8 + "乙" * 8 + "丙" * 8,
        ["甲" * 8, "乙" * 8 + "丙" * 8],
        24,
        estimate_model="qwen3.8-flash",
    )
    assert meta.estimate_model == "qwen3.8-flash"

    user_token = current_user_id.set("91")
    project_token = current_project_name.set("demo")
    try:
        _text, error = _read_attachment_window_text("91", "demo", meta.attachment_id, 1)
    finally:
        current_project_name.reset(project_token)
        current_user_id.reset(user_token)

    assert "超过单窗口上限" in error
