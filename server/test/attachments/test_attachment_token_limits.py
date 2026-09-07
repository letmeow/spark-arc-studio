from types import SimpleNamespace

import pytest

from agents.utility_agent import (
    UtilityAgent,
)
from core.project_settings import CHAT_ATTACHMENT_DIRECT_INJECTION_MAX_TOKENS


def _parsed_document(token_count: int) -> SimpleNamespace:
    return SimpleNamespace(
        filename="附件.txt",
        source_format=".txt",
        full_text="字" * token_count,
        sections=[],
        warnings=[],
        metadata={},
    )


def _stub_chunking(monkeypatch, total_tokens: int) -> None:
    """冻结切分器的 token 口径，避免真实分词器抖动。

    注意口径统一：prepare_chat_attachment 全程使用切分器 pack 累加，
    estimate_text_tokens(len) 按字符计；total_tokens 必须等于字符数。
    统一入口是 core.file_ingest.chunking.estimate_text_tokens，
    必须 stub 它（及其底层 _estimate_tokens），stub 旧的 estimate_tokens
    别名无效——切分器内部已不再走别名。
    """
    stub = lambda text, model=None: len(text)
    monkeypatch.setattr(
        "core.file_ingest.chunking.estimate_text_tokens",
        stub,
    )
    monkeypatch.setattr(
        "core.file_ingest.chunking._estimate_tokens",
        stub,
    )


def test_prepare_chat_attachment_saves_oversized_file_as_manifest_only(monkeypatch) -> None:
    """超窗附件不再 413 拒绝：照常切分落盘，标记 oversized + partial。"""
    parsed = _parsed_document(101)
    saved: dict = {}

    monkeypatch.setattr("core.file_ingest.service.parse_uploaded_file", lambda *_args, **_kwargs: parsed)
    _stub_chunking(monkeypatch, 101)

    def _fake_save(**kwargs):
        saved.update(kwargs)
        return SimpleNamespace(attachment_id="attachment-id")

    monkeypatch.setattr("agents.attachment.save_attachment", _fake_save)

    prepared = UtilityAgent.prepare_chat_attachment(
        user_id="1",
        project_name="demo",
        file_path="unused.txt",
        filename="附件.txt",
        chunk_tokens=64_000,
        max_context_tokens=100,
    )

    assert saved, "超窗附件必须落盘"
    assert prepared.attachment_id == "attachment-id"
    assert prepared.is_oversized is True
    assert prepared.is_partial is True
    assert prepared.total_tokens_estimated == 101


@pytest.mark.parametrize(
    ("total_chars", "expected_partial"),
    [
        # 64K 字符恰好等于边界 → 单片 → 非 partial。
        (CHAT_ATTACHMENT_DIRECT_INJECTION_MAX_TOKENS, False),
        # 远超边界 → 多片 → partial。total 取 pack 累加（含片间 1 token 连接），
        # 因此不断言精确值，只断言方向与分片数。
        (CHAT_ATTACHMENT_DIRECT_INJECTION_MAX_TOKENS * 3, True),
    ],
)
def test_prepare_chat_attachment_uses_64k_direct_injection_boundary(
    monkeypatch,
    total_chars: int,
    expected_partial: bool,
) -> None:
    # 口径统一后 total_tokens == pack 累加；边界用例传恰好等于边界的字符数。
    parsed = _parsed_document(total_chars)
    monkeypatch.setattr("core.file_ingest.service.parse_uploaded_file", lambda *_args, **_kwargs: parsed)
    _stub_chunking(monkeypatch, total_chars)
    monkeypatch.setattr(
        "agents.attachment.save_attachment",
        lambda **_kwargs: SimpleNamespace(attachment_id="attachment-id"),
    )

    prepared = UtilityAgent.prepare_chat_attachment(
        user_id="1",
        project_name="demo",
        file_path="unused.txt",
        filename="附件.txt",
        chunk_tokens=64_000,
        max_context_tokens=256_000,
    )

    if expected_partial:
        assert prepared.total_tokens_estimated > CHAT_ATTACHMENT_DIRECT_INJECTION_MAX_TOKENS
        assert prepared.chunk_count > 1
    else:
        assert prepared.total_tokens_estimated == total_chars
        assert prepared.chunk_count == 1
    assert prepared.is_partial is expected_partial


def test_oversized_single_attachment_injects_manifest_only() -> None:
    """超窗单附件不预注入任何正文，只能走清单 + 滑窗。"""
    from agents.routes.chat_attachment import expand_active_context_with_attachments

    result = expand_active_context_with_attachments(
        "1",
        "demo",
        "基础上下文",
        [
            {
                "attachmentId": "oversized-id",
                "filename": "超长.txt",
                "totalTokens": 999999,
                "isPartial": True,
                "isOversized": True,
            }
        ],
    )

    assert "基础上下文" in result
    assert "已上传" in result or "附件" in result
    assert "【已上传文件首个分片" not in result


def test_attachment_metadata_cannot_bypass_64k_partial_mode() -> None:
    from agents.routes.chat_attachment import _normalize_attachment_meta

    normalized = _normalize_attachment_meta(
        {
            "attachmentId": "attachment-id",
            "filename": "附件.txt",
            "totalTokens": CHAT_ATTACHMENT_DIRECT_INJECTION_MAX_TOKENS + 1,
            "isPartial": False,
        }
    )

    assert normalized is not None
    assert normalized["isPartial"] is True
