# 长文档滑窗阈值总览（longread-thresholds）

> 给维护者的一页纸：所有与“长文本切分 / 滑窗读取 / 注入降级”有关的阈值、
> 定义位置、默认值、作用范围。改阈值前先读本页，改完同步更新本页。

## 阈值表

| 阈值 | 定义位置 | 默认值 | 作用范围 |
|---|---|---|---|
| `ATTACHMENT_CHUNK_TOKENS_MIN` | `server/core/project_settings.py` | 1000 | 附件分片 token 下限。`_coerce_attachment_chunk_tokens` 钳制入参，前端面板输入框 `min` 也读它。 |
| `ATTACHMENT_CHUNK_TOKENS_MAX` | `server/core/project_settings.py` | 120000 | 附件分片 token 上限。同上，钳制入参与面板 `max`。单窗口工具侧还会再被 `LONGREAD_MAX_WINDOW_TOKENS` 兜底。 |
| `ATTACHMENT_CHUNK_TOKENS_DEFAULT` | `server/core/project_settings.py` | 64000 | 项目未配置 `attachment_chunk_tokens` 时的附件切分窗口（`TokenTextSplitter(chunk_tokens=…)`，`tail_merge 0.5/1.5`）。同时是世界观逻辑切片的窗口大小。 |
| `CHAT_ATTACHMENT_DIRECT_INJECTION_MAX_TOKENS` | `server/core/project_settings.py` | 64000 | 单附件全文直接注入上限。`prepare_chat_attachment` 与 `chat_attachment._normalize_attachment_meta` 共用：超过即 `isPartial=True`，首轮只注入首片 + 分片说明。注意它是写死的 64K，不跟随面板可调的 chunk 大小。 |
| `LONGREAD_WORLDVIEW_SLIDING_THRESHOLD_TOKENS` | `server/core/project_settings.py` | 64000 | 世界观转滑窗阈值。`worldview_source.is_worldview_oversized` 判定；超阈后 `context_provider.get_worldview_context` 只注入地图 + 首片，其余走 `read_worldview_window`。影响 showrunner / scriptwriter / critic / director 四个 Agent 的世界观注入分支。 |
| `LONGREAD_MAX_WINDOW_TOKENS` | `server/core/project_settings.py` | 64000 | 单窗口正文上限。`tools/longread._read_attachment_window_text` 兜底：必须用切分时的同一口径（`meta.estimate_model`）实测，禁止跨口径比较——切分器是“≤ chunk_tokens 即合法”的装箱，口径一致时装箱承诺与本上限是同一把尺子。口径漂移的旧附件（无口径记录）走读时自愈细分；真正同口径超窗才提示调小分片重传。根因见国家意志 e2e（切时无模型回退 64K vs 读时无模型回退 100K+ 误杀 32 窗）。 |
| `LONGREAD_LEDGER_SNAPSHOT_MAX_CHARS` | `server/core/project_settings.py` | 8000 | 上轮账本快照注入本轮尾部的字符上限。`routes/chat.py::_append_longread_ledger_snapshot` 在四个聊天入口（send/send-stream/edit/edit-stream）共用；超过时只保留最新条目并注明省略，避免账本自身成为新的爆点。 |
| `LONGREAD_LEDGER_MAX_ENTRIES` | `server/core/project_settings.py` | 64 | 单房间线索账本上限（条）。`ClueLedger` / `LedgerStore.load` / `longread_store.init_task_ledger` 共用；超了只保留最新 N 条。旧线索被丢弃前应已沉淀为正文结论或 checkpoint 摘要。 |
| 当前模型 `max_context_tokens` | Matchbox 模型配置（上传时 `_resolve_import_model_limits` 读取） | 256000（`DEFAULT_MAX_CONTEXT_TOKENS`） | 附件超窗判定：`全文 > max_context_tokens` 即 `is_oversized=True`。超窗附件照常切分落盘，但首轮只注入清单、不预注入任何正文（`expand_active_context_with_attachments` 清单分支）。 |

## 配套上限（非本页阈值，但经常一起被问到）

- 聊天历史预算：`server/agents/context_budget.py`（`hard_budget = max_context − 预留输出 − 安全边际`），与滑窗正交：滑窗管“长文档怎么读”，预算管“整轮请求塞不塞得下”。
- 压缩工作集比例：`COMPACTION_TARGET_RATIOS`（128K/28%、256K/24%、512K/18%、1M/14%、2M/12%），见 `docs/project/context-management.zh-CN.md`。
- GraphRAG 取数上限：`SPARKARC_GRAPHRAG_MAX_SOURCE_CHARS`（默认 24 万字符），超了静默截断，大尾巴不在图里——这正是滑窗要补的缺口。

## 前缀缓存布局（改动本页任何阈值都要复核）

`system（稳定）+ manifest（稳定）+ ledger（只追加）+ 当前窗口（一片，尾部）+ 本轮用户请求（最尾）`。

- 地图与账本一经注入只追加不改写；
- 旧窗口折叠只在“尾部变前缀”（任务终态落盘 / 持久化前）发生一次；
- 任务进行中只追加新工具结果，不反复改写中间历史。

违反会从第一个被改的 `ToolMessage` 起让后续前缀缓存全部失效，详见 AGENTS.md §5.2.1 第 4 条。
