"""
附件落盘存储
============

落盘位置：``{project_path}/.attachments/{attachment_id}/``

attachment_id = sha256(content)[:16]，天然去重：同一 user/project 下
多个 session 上传同一文件只会得到同一份缓存。

"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from core.utils import get_project_path
from core.json_state import load_json_file, save_json_file_atomic


ATTACHMENTS_DIR_NAME = ".attachments"
META_FILENAME = "meta.json"
FULL_TEXT_FILENAME = "full.txt"
CHUNKS_DIR_NAME = "chunks"
CHUNK_FILENAME_FMT = "chunk_{index}.txt"


# ==================== 数据类 / 异常 ====================

@dataclass
class AttachmentMeta:
    """附件元信息（落盘于 meta.json）"""
    attachment_id: str
    filename: str
    source_format: str
    total_tokens: int
    chunk_count: int
    content_hash: str
    uploaded_at: str
    # 切分口径：estimate_model 为空表示无模型回退口径。同一内容 + 同一口径
    # 才复用旧分片；任一变化都必须重切（国家意志 e2e：qwen 回退 64K 切出的
    # 32 片在当前 500K 模型下实测 100K+，直接读窗必然被 LONGREAD 上限拒绝）。
    estimate_model: str = ""
    # 可选字段：摘要状态、被引用时间等
    last_referenced_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "attachment_id": self.attachment_id,
            "filename": self.filename,
            "source_format": self.source_format,
            "total_tokens": int(self.total_tokens),
            "chunk_count": int(self.chunk_count),
            "content_hash": self.content_hash,
            "uploaded_at": self.uploaded_at,
            "estimate_model": str(self.estimate_model or ""),
        }
        if self.last_referenced_at:
            data["last_referenced_at"] = self.last_referenced_at
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AttachmentMeta":
        return cls(
            attachment_id=str(data.get("attachment_id") or ""),
            filename=str(data.get("filename") or ""),
            source_format=str(data.get("source_format") or ""),
            total_tokens=int(data.get("total_tokens") or 0),
            chunk_count=int(data.get("chunk_count") or 0),
            content_hash=str(data.get("content_hash") or ""),
            uploaded_at=str(data.get("uploaded_at") or ""),
            estimate_model=str(data.get("estimate_model") or ""),
            last_referenced_at=(str(data["last_referenced_at"]) if data.get("last_referenced_at") else None),
        )


class AttachmentNotFoundError(LookupError):
    """附件缓存不存在或已被清理"""
    def __init__(self, attachment_id: str):
        super().__init__(f"附件缓存不存在: {attachment_id}")
        self.attachment_id = attachment_id


# ==================== 路径解析 ====================

def _attachments_root(user_id: str, project_name: str) -> str:
    project_path = get_project_path(user_id, project_name)
    return os.path.join(project_path, ATTACHMENTS_DIR_NAME)


def ensure_attachments_root(user_id: str, project_name: str) -> str:
    root = _attachments_root(user_id, project_name)
    os.makedirs(root, exist_ok=True)
    return root


def get_attachment_root(user_id: str, project_name: str, attachment_id: str) -> str:
    """返回附件目录绝对路径（不保证存在）。"""
    return os.path.join(_attachments_root(user_id, project_name), str(attachment_id))


def _meta_path(user_id: str, project_name: str, attachment_id: str) -> str:
    return os.path.join(get_attachment_root(user_id, project_name, attachment_id), META_FILENAME)


def _full_text_path(user_id: str, project_name: str, attachment_id: str) -> str:
    return os.path.join(get_attachment_root(user_id, project_name, attachment_id), FULL_TEXT_FILENAME)


def _chunks_dir(user_id: str, project_name: str, attachment_id: str) -> str:
    return os.path.join(get_attachment_root(user_id, project_name, attachment_id), CHUNKS_DIR_NAME)


def get_chunk_paths(user_id: str, project_name: str, attachment_id: str) -> list[str]:
    """返回附件所有 chunk 文件的绝对路径列表（按索引排序）。"""
    directory = _chunks_dir(user_id, project_name, attachment_id)
    if not os.path.isdir(directory):
        return []
    paths = []
    for name in sorted(os.listdir(directory)):
        if name.startswith("chunk_") and name.endswith(".txt"):
            paths.append(os.path.join(directory, name))
    return paths


# ==================== 写入 ====================

def compute_attachment_id(content: str) -> str:
    """基于文本内容的 sha256 前 16 位作为 attachment_id。同一内容天然去重。"""
    return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()[:16]


def save_attachment(
    user_id: str,
    project_name: str,
    filename: str,
    source_format: str,
    full_text: str,
    chunks: Iterable[str],
    total_tokens: int,
    estimate_model: str | None = None,
) -> AttachmentMeta:
    """
    落盘附件到项目 .attachments 目录。

    返回 AttachmentMeta；若相同 content_hash 的附件已存在，会覆盖 meta.json
    的 filename/uploaded_at 但不重写正文（节省 IO）。

    分片一致性（关键不变量）：meta.chunk_count 必须等于磁盘 chunk 文件数。
    旧版本在“同内容重传 + 切分参数变化”时跳过 chunk 重写，导致 meta 与磁盘
    不一致（七堇年 e2e：meta=2、磁盘=1）。本次修复后按“期望文件集合”对账：
    多余的旧分片删除、缺失的补写；任何不一致都以本次切分结果为准。
    """
    normalized_text = str(full_text or "")
    attachment_id = compute_attachment_id(normalized_text)
    content_hash = hashlib.sha256(normalized_text.encode("utf-8", errors="ignore")).hexdigest()

    root = get_attachment_root(user_id, project_name, attachment_id)
    os.makedirs(root, exist_ok=True)
    chunks_dir = _chunks_dir(user_id, project_name, attachment_id)
    os.makedirs(chunks_dir, exist_ok=True)

    # 正文只在首次写入或缺失时写，节约 IO
    full_path = _full_text_path(user_id, project_name, attachment_id)
    if not os.path.exists(full_path):
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(normalized_text)

    # 分片对账：期望集合 = 本次切分结果；删除多余旧分片、补写缺失分片、
    # 重写内容变化的分片。同 id 只保证同内容，不同切分参数下同位置分片
    # 内容可能变化，必须以本次为准（否则 chunk_0 是旧切分残留）。
    chunk_list = [str(chunk_text or "") for chunk_text in (chunks or [])]
    expected_names = {
        CHUNK_FILENAME_FMT.format(index=idx) for idx in range(len(chunk_list))
    }
    try:
        existing_names = {
            name
            for name in os.listdir(chunks_dir)
            if name.startswith("chunk_") and name.endswith(".txt")
        }
    except OSError:
        existing_names = set()
    for stale in sorted(existing_names - expected_names):
        try:
            os.remove(os.path.join(chunks_dir, stale))
        except OSError:
            pass
    for idx, chunk_text in enumerate(chunk_list):
        chunk_path = os.path.join(chunks_dir, CHUNK_FILENAME_FMT.format(index=idx))
        needs_write = True
        if os.path.exists(chunk_path):
            try:
                with open(chunk_path, "r", encoding="utf-8") as f:
                    needs_write = f.read() != chunk_text
            except OSError:
                needs_write = True
        if not needs_write:
            continue
        with open(chunk_path, "w", encoding="utf-8") as f:
            f.write(chunk_text)

    now_iso = datetime.now(timezone.utc).isoformat()
    meta = AttachmentMeta(
        attachment_id=attachment_id,
        filename=str(filename or "").strip() or "unknown",
        source_format=str(source_format or "").strip() or "txt",
        total_tokens=int(total_tokens or 0),
        chunk_count=len(chunk_list) if chunk_list else (1 if normalized_text else 0),
        content_hash=content_hash,
        uploaded_at=now_iso,
        estimate_model=str(estimate_model or "").strip(),
        last_referenced_at=now_iso,
    )

    # 覆盖 meta.json（即便是旧 id 也更新 filename/uploaded_at，以反映最近一次上传）
    save_json_file_atomic(_meta_path(user_id, project_name, attachment_id), meta.to_dict())

    return meta


# ==================== 读取 ====================

def get_attachment_meta(user_id: str, project_name: str, attachment_id: str) -> Optional[AttachmentMeta]:
    """附件 meta。不存在时返回 None，不抛异常。"""
    path = _meta_path(user_id, project_name, attachment_id)
    if not os.path.exists(path):
        return None
    data = load_json_file(path, dict)
    return AttachmentMeta.from_dict(data) if isinstance(data, dict) and data else None


def load_attachment_text(user_id: str, project_name: str, attachment_id: str) -> str:
    """读取附件全文。缓存缺失时抛 AttachmentNotFoundError。"""
    path = _full_text_path(user_id, project_name, attachment_id)
    if not os.path.exists(path):
        raise AttachmentNotFoundError(attachment_id)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_chunks(user_id: str, project_name: str, attachment_id: str) -> list[str]:
    """读取附件所有 chunk（按索引顺序）。缓存缺失时抛 AttachmentNotFoundError。"""
    paths = get_chunk_paths(user_id, project_name, attachment_id)
    if not paths:
        # 兜底：没有 chunk 文件但 full.txt 存在，视为单片
        try:
            full = load_attachment_text(user_id, project_name, attachment_id)
            return [full] if full else []
        except AttachmentNotFoundError:
            raise
    chunks: list[str] = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                chunks.append(f.read())
        except Exception:
            chunks.append("")
    return chunks


def touch_last_referenced(user_id: str, project_name: str, attachment_id: str) -> None:
    """更新 last_referenced_at 时间戳（用于 GC）。静默失败。"""
    meta = get_attachment_meta(user_id, project_name, attachment_id)
    if not meta:
        return
    meta.last_referenced_at = datetime.now(timezone.utc).isoformat()
    try:
        save_json_file_atomic(_meta_path(user_id, project_name, attachment_id), meta.to_dict())
    except Exception:
        pass


# ==================== 删除 ====================

def delete_attachment(user_id: str, project_name: str, attachment_id: str) -> bool:
    """删除附件及其所有缓存。返回是否真的删了。"""
    root = get_attachment_root(user_id, project_name, attachment_id)
    if not os.path.isdir(root):
        return False
    try:
        shutil.rmtree(root)
        return True
    except Exception:
        return False
