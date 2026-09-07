"""超长世界观的逻辑切片视图（longread 底座的第二个接入方）。

设计约束（防止后续维护者困惑）：
- 世界观真相源永远是 ``世界观.txt`` 全文；本模块不复制、不双写，
  只在内存里按 ``TokenTextSplitter`` 做“逻辑切片视图”，每次按需重算。
- 切片参数与附件共用同一套 ``TokenTextSplitter``，窗口大小取
  ``attachment_chunk_tokens`` 项目配置（默认 64K），与附件滑窗对齐；
  是否转滑窗由 ``LONGREAD_WORLDVIEW_SLIDING_THRESHOLD_TOKENS`` 决定。
- 世界观未超阈时返回 None：调用方保持全文注入，不走滑窗。
"""

from __future__ import annotations

from core.file_ingest.chunking import TokenTextSplitter, estimate_text_tokens as estimate_tokens
from core.project_settings import (
    LONGREAD_WORLDVIEW_SLIDING_THRESHOLD_TOKENS,
    get_attachment_chunk_tokens,
)

from agents.longread import SourceManifest


def _load_worldview_text(user_id: str, project_name: str) -> str:
    from agents.project_content import load_worldview

    return str(load_worldview(str(user_id), str(project_name)) or "")


def _split_worldview(user_id: str, project_name: str, text: str) -> list:
    chunk_tokens = get_attachment_chunk_tokens(str(user_id), str(project_name))
    splitter = TokenTextSplitter(
        chunk_tokens=chunk_tokens,
        tail_merge_threshold_ratio=0.5,
        tail_merge_cap_ratio=1.5,
    )
    return splitter.split(text)


def is_worldview_oversized(user_id: str, project_name: str, text: str | None = None) -> bool:
    """世界观是否超过转滑窗阈值。"""
    content = text if text is not None else _load_worldview_text(user_id, project_name)
    if not (content or "").strip():
        return False
    return estimate_tokens(content) > LONGREAD_WORLDVIEW_SLIDING_THRESHOLD_TOKENS


def describe_worldview_source(user_id: str, project_name: str) -> SourceManifest | None:
    """超阈时返回世界观地图；未超阈/为空返回 None（调用方走全文注入）。"""
    content = _load_worldview_text(user_id, project_name)
    if not content.strip() or not is_worldview_oversized(user_id, project_name, content):
        return None
    chunks = _split_worldview(user_id, project_name, content)
    if not chunks:
        return None
    entries = tuple(
        (chunk.text or "").strip().splitlines()[0][:120] if (chunk.text or "").strip() else "(空窗口)"
        for chunk in chunks
    )
    return SourceManifest(
        source_id="worldview",
        filename="世界观",
        chunk_count=len(chunks),
        total_tokens=estimate_tokens(content),
        entries=entries,
    )


def read_worldview_window_text(user_id: str, project_name: str, chunk_index: int) -> str:
    """读取世界观第 N 个逻辑窗口正文。"""
    content = _load_worldview_text(user_id, project_name)
    if not content.strip():
        return ""
    chunks = _split_worldview(user_id, project_name, content)
    if chunk_index < 0 or chunk_index >= len(chunks):
        return ""
    return (chunks[chunk_index].text or "").strip()


__all__ = [
    "describe_worldview_source",
    "is_worldview_oversized",
    "read_worldview_window_text",
]
