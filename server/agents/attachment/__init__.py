"""
agents.attachment
=================

聊天附件的落盘/读取/清理服务。

附件文件不进 DB，而是以 content_hash 为 id 存到
``{project_path}/.attachments/{attachment_id}/``：

    meta.json        # filename, source_format, total_tokens, uploaded_at, chunk_count
    full.txt         # 完整文本（UTF-8）
    chunks/chunk_0.txt, chunk_1.txt, ...

聊天记录只保存 ``attachment_id`` 引用，发送消息时动态读盘拼到上下文；
附件文件缺失时注入 "[引用已失效]" 提示，不中断对话。

下游消费方：

- ``agents.routes.chat_attachment`` / chat 路由：聊天发送前按 attachmentId
  动态注入首片或全文，并在 partial 时附加分片说明。
- ``agents.tools.attachment.read_attachment_chunk``：导演 Agent 主动滑窗读取
  剩余分片的 LangChain 工具。
- ``agents.vector_index.VectorIndexService``：把附件分片接入项目语义检索
  （受 per-project 配置 ``attachment_index_enabled`` 控制，默认开）。
"""

from .storage import (
    AttachmentMeta,
    AttachmentNotFoundError,
    compute_attachment_id,
    delete_attachment,
    ensure_attachments_root,
    get_attachment_meta,
    get_attachment_root,
    get_chunk_paths,
    load_attachment_text,
    load_chunks,
    save_attachment,
    touch_last_referenced,
)
from .gc import collect_orphan_attachments

__all__ = [
    "AttachmentMeta",
    "AttachmentNotFoundError",
    "compute_attachment_id",
    "delete_attachment",
    "ensure_attachments_root",
    "get_attachment_meta",
    "get_attachment_root",
    "get_chunk_paths",
    "load_attachment_text",
    "load_chunks",
    "save_attachment",
    "touch_last_referenced",
    "collect_orphan_attachments",
]
