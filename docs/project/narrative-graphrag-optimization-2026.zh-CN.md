# SparkArc 长篇叙事 GraphRAG 定位与整改方案（2026）

## 1. 结论摘要

SparkArc 当前的 GraphRAG 确实偏鸡肋。问题不在于它被封装成工具后不能由 Agent 自主调用，而在于以下三层能力没有对齐：

1. **工具绑定错位**（正在整改）：`graph_rag_tool` 现绑定给 Director、Scriptwriter、Critic、Showrunner；承担创意前期开发的 Muse、Lorebook 仍没有该工具。
2. **工具协议过窄**：AI 端只有 `status` 和通用自然语言 `query`，输出只有普通回答或写作约束，缺少结构诊断、路径追踪、缺口发现、方案对比等前期开发操作。
3. **图谱表达力不足**：底层是静态无向实体三元组图。同一实体对的多种关系会被压进一条边，无法可靠表达事件、目标、冲突、因果、时间、知情边界、关系变化、伏笔状态和揭示顺序。

因此，当前能力本质上是一个成本较高的“跨文件事实核对器”，不是“编剧前期结构分析器”。Agent 可以自由决定是否调用工具，但工具只能返回它已经具备的数据和操作；自由调用不会自动产生 schema 中不存在的叙事信息。

GraphRAG 也不只适合处理长篇人物关系。对长篇小说和剧本而言，它最有价值的范围是：

- 跨章节、跨文档的多跳关系与因果追踪；
- 人物目标、阵营、地点、物件、事件之间的约束传播；
- 角色状态、关系状态、秘密知情状态随故事时间的变化；
- 伏笔的埋设、推进、误导、揭示与回收链；
- 主线与支线的交汇、长期悬置和结构空洞；
- 在指定章节或场景时间点回答“当时成立什么”，避免未来剧情泄漏。

人物关系只是其中一个子集。

## 2. 适用边界：GraphRAG 何时值得使用

### 2.1 从零创意阶段

当项目只有一句灵感、没有角色档案、世界观、梗概或大纲时，GraphRAG 没有足够的项目事实可以建图。此时它基本不能提供独有价值，应该由 Muse、外部调研和普通模型发散承担。

### 2.2 编剧前期开发阶段

当项目已经有角色、世界观、梗概、节拍或部分大纲后，GraphRAG 开始有价值。它不应替代 Showrunner 创作剧情，而应给 Showrunner 提供结构证据和诊断，例如：

- 哪些角色目标没有与主冲突发生连接；
- 哪条因果链存在跳步或只有结果没有诱因；
- 哪些支线长期没有与主线交汇；
- 哪个角色掌握的信息不足以支撑其后续行动；
- 哪个关键物件或地点只出现一次，没有承担结构功能；
- 哪组人物关系长期静止，没有转折或代价；
- 哪些候选场景能同时推进两条以上既有叙事线程。

这才是 GraphRAG 在 pre-production 阶段最值得发展的方向。

### 2.3 正文生产与修订阶段

- StoryMemory 负责最近保存正文形成的实时状态、开放线索和修订工单。
- 语义检索负责定位原文、台词、场景和局部事实。
- Narrative GraphRAG 负责跨章节多跳、时序、因果、知情边界和长期结构。
- 最终判断应回到原文证据；图谱负责导航、聚合和约束，不应成为脱离原文的第二真相源。

简单事实问题不应默认走 GraphRAG。只有复杂多跳问题，或离线建图成本可以被大量后续查询摊销时，GraphRAG 才更有优势。

## 3. 当前实现的事实审计

### 3.1 工具封装与绑定

统一门面和注册方式符合项目架构：

- 对外门面：`server/agents/agent_tools.py`
- 工具实现：`server/agents/tools/research.py`
- 唯一注册表：`server/agents/tools/registry.py`
- 图谱服务：`server/agents/graphrag/service.py`

`graph_rag_tool` 当前只有以下操作：

| 操作 | 能力 |
| --- | --- |
| `status` | 查询索引是否存在、是否过期、是否正在构建 |
| `query` + `local` | 对命中实体做 1 至 4 跳邻域遍历 |
| `query` + `global` | 返回节点数、边数、高度数实体和连通分量摘要 |
| `query` + `drift` | 拼接 local 与 global 上下文 |
| `response_mode=answer` | 再调用一次 LLM 生成自然语言回答 |
| `response_mode=writing_guardrails` | 将邻边机械整理为“必须保持 / 避免冲突 / 待补充” |

工具目前绑定给：

- Director
- Scriptwriter
- Critic
- Showrunner（连续性工具组）

工具目前没有绑定给：

- Muse
- Lorebook
- Style

> 📌 整改进展：Showrunner 已接入 `graph_rag_tool`（随连续性工具组绑定），"大纲主要生产者无法直接使用 GraphRAG"的首要障碍已解除；Muse、Lorebook 的接入仍在规划中。

### 3.2 PreWrite 的真实能力

Scriptwriter 的自主 PreWrite 会绑定 GraphRAG，但系统提示明确限定其用途为“跨文件关系与更大范围事实约束”，并要求最终输出不超过 200 字的单场创作规划。它适合写前核对，不承担剧集结构、人物弧线、支线交汇或揭示策略分析。

所以“已经封装成工具”这一点是成立的，但当前工具是只读事实问答工具，不是结构分析工具。

### 3.3 建图与查询模型

当前实现：

- 复用 `SemanticChunker` 处理 `worldview/synopsis/outline/character/arc/novel`；
- 明确排除了节拍表，理由是避免与大纲重复；
- 每个分块调用 Matchbox `fast` 模型抽取 `subject/relation/object`；
- 使用 `nx.Graph` 无向图；
- 相同实体对的关系、来源和证据样本被合并到单边字符串；
- 查询先调用 LLM 抽实体，再调用 LLM 根据图上下文回答；
- global 查询只计算高度数节点和连通分量，不是真正的叙事全局分析。

这套模型可以回答“甲与乙有什么关系”，但很难可靠回答：

- 甲在第 30 场时是否已经知道秘密；
- 甲为什么在第 42 场背叛乙；
- 两人的关系经历了哪些阶段；
- 某伏笔何时埋设、何时被误读、何时回收；
- 哪个未解决目标可以自然驱动下一场；
- 当前大纲哪条支线缺少与主线的交汇点。

## 4. 已确认需要整改的问题

### P0：正确性、安全和协议一致性

1. Director 提示词错误声称首次 `semantic_search` 会自动建索引，实际只会降级关键词搜索。
2. GraphRAG 工具查询前未检查项目 `graphrag_enabled`，关闭后仍可能读取旧图。
3. `replace_from_search` 可能修改 `.attachments/.../full.txt` 附件抽取缓存。
4. 搜索结果缓存键只有用户和项目，并发聊天或 Agent 可能互相覆盖。
5. 语义索引过期时仍查询旧索引，GraphRAG 则拒绝查询，两者策略不一致。
6. `semantic_search.k` 没有合理上限。
7. LanceDB `_distance` 被展示为“相似度”，含义可能相反。

### P1：GraphRAG 核心能力

1. 静态无向图无法表达叙事方向和状态演化。
2. 同一实体对的多条关系被压成一条字符串，关系历史丢失。
3. 缺少事件、目标、冲突、秘密、线索和故事线程等叙事节点。
4. 缺少章节/场次顺序、有效区间、置信度和可回溯证据。
5. 任意源文件变化都会全量重抽取，长篇项目成本不可持续。
6. 查询固定消耗两次 LLM，简单查询也承担不必要延迟。
7. global 只是图统计，不足以支撑前期结构判断。

## 5. 2026 年论文依据

本节严格只采用 2025 年之后公开的论文，即 2026 年论文或预印本。预印本结论用于设计参考，不视为已经被长期复现的定论。

### 5.1 Narrative Knowledge Weaver（arXiv:2606.05724）

该工作把长篇叙事拆分为角色状态、时间、因果、互动、事件、episode 和 storyline，并将文本、图和叙事检索通道分开。其结果支持两个判断：

- 长篇叙事图谱远不止人物关系；
- 普通段落问题仍可能更适合 Hybrid RAG，不应让所有问题都走图谱。

### 5.2 Narrative World Model（arXiv:2607.05577，预印本、审稿中）

该工作面向长篇小说 writer memory，重点表达知情状态、关系变化、物体位置、伏笔开闭、事件因果、揭示顺序与事件顺序。每条事实关联来源章节、证据、有效区间和置信度，查询可以按章节截断，防止使用未来剧情。

其检索采用 BM25、dense vector 与 RRF 融合，命中后只扩展一跳类型化邻域。论文报告的结果支持：效果主要来自叙事分解和按问题检索，而不是更大的图或更昂贵的抽取模型。

### 5.3 STAGE（arXiv:2601.08510）

STAGE 面向 150 部中英文电影剧本，任务覆盖知识图谱构建、场景事件摘要、长上下文问答和角色一致性生成。它进一步说明剧本图谱的目标不是只记录人物关系，而是建立可用于理解和生成的叙事世界模型。

### 5.4 GroundedKG-RAG（arXiv:2604.04359）

该工作强调将实体、动作、时间和语义边直接绑定原文句子，先检索节点，再返回对应原文证据。它还报告复杂邻居嵌入未稳定优于基础节点嵌入，严格向量阈值可能误删正确证据。

对 SparkArc 的直接启示是：第一阶段不必投入复杂图嵌入，先做好叙事 schema、证据回链和混合召回。

### 5.5 Core-based Hierarchies for Efficient GraphRAG（arXiv:2603.05207，KDD 2026）

该工作用确定性的 k-core 层次和 token budget aware sampling 优化全局检索。SparkArc 后续可以直接利用 NetworkX 的 `core_number` 构造全局结构摘要，无需立即引入图数据库或复杂社区管线。

### 5.6 GraphRAG-Router（arXiv:2604.16401）与 Do We Still Need GraphRAG?（arXiv:2604.09666）

两项工作共同支持按问题复杂度路由：简单问题优先 dense/hybrid RAG，复杂多跳问题才进入 GraphRAG。SparkArc 无需照搬强化学习路由器，先实现确定性的规则路由即可获得主要成本收益。

## 6. 前期开发应该提供哪些 GraphRAG 操作

不建议让 Agent 用一个无限泛化的 `question` 字段承担所有任务。保留现有 `status/query`，新增一个只读 `analyze` 操作和明确的分析类型即可：

| 分析类型 | 主要使用者 | 输出 |
| --- | --- | --- |
| `character_pressure` | Showrunner、Lorebook | 角色目标、阻力、盟友、代价和未连接目标 |
| `causal_chain` | Showrunner、Critic | 事件原因、结果、缺失桥接和循环因果 |
| `subplot_intersections` | Showrunner | 主支线交汇点、长期孤立支线、可合并推进机会 |
| `knowledge_boundary` | Showrunner、Scriptwriter、Critic | 谁在何时知道什么、信息来源和越界风险 |
| `relationship_arc` | Lorebook、Showrunner | 关系阶段、变化触发事件、当前状态和缺失转折 |
| `thread_lifecycle` | Showrunner、Scriptwriter | 目标、秘密、伏笔等线程的开启、推进、悬置、回收 |
| `structure_gaps` | Showrunner、Critic | 孤立节点、无结果事件、无诱因结果、长期未推进线程 |
| `evidence_trace` | 全部相关 Agent | 图事实对应的原文位置和证据片段 |

这些操作应返回结构化“证据包”，由正在工作的 Agent 做最终创作判断，而不是由 GraphRAG 内部再固定调用一次回答模型。这样既减少延迟，也避免 GraphRAG 的回答模型抢走 Showrunner、Scriptwriter 或 Critic 的专业职责。

## 7. 不大改架构的实施路线

### P0：先修现有边界

- 修复第 4 节的七项正确性与安全问题。
- GraphRAG 工具在 `status/query` 时统一检查项目启用状态。
- 明确 stale 策略：默认拒绝旧图，必要时允许显式 `allow_stale=true` 并标注风险，不静默混用。

### P1：继续使用 NetworkX，升级为叙事图

不引入图数据库，先将 `nx.Graph` 改为 `nx.MultiDiGraph`，保留现有持久化目录和工具门面。

最小节点/记录类型：

- `entity`：人物、组织、地点、物件；
- `event`：发生的事件或计划中的剧情事件；
- `state_delta`：人物、物件、地点状态变化；
- `knowledge`：秘密或可传播信息；
- `relationship_delta`：关系变化，而非静态关系字符串；
- `thread`：目标、冲突、伏笔、承诺、悬念等长期线程。

最小公共字段：

- `source`
- `chapter_order`
- `scene_order`
- `evidence_span`
- `valid_from`
- `valid_to`
- `confidence`
- `polarity`

不要继续把相同实体对的历史关系压到一条边。多边和时间字段是关系弧、知情边界与因果追踪的前提。

### P1：增量构建

在 metadata 中持久化：

`source -> chunk_id -> content_hash -> extracted_records`

构建时只重抽取新增或修改的 chunk；删除源文件时只撤销对应来源记录，再局部重算受影响实体。该方案可以复用现有文件哈希、`SemanticChunker` 和后台构建状态，不改变路由层与前端主流程。

节拍表不应再被一刀切排除。应通过稳定的叙事单元 ID 和来源优先级去重，让节拍、梗概和大纲可以表达“同一计划事件的不同粒度”，而不是因为可能重复就放弃最适合前期结构分析的数据源。

### P1：把工具给正确的 Agent

- Showrunner：必须绑定只读 `query/analyze`，这是前期结构分析的主使用者。
- Lorebook：绑定关系弧、世界约束和阵营结构分析。
- Muse：仅在已有项目基础上寻找空白、变体或冲突机会时按需绑定；从零灵感阶段不应强制用图。
- Director：负责判断是否需要 GraphRAG，并把证据包交给下游 Agent。
- Scriptwriter、Critic：继续用于写前约束与一致性检查。

所有新增能力继续经 `server/agents/tools/registry.py` 注册和 `server/agents/agent_tools.py` 导出，不创建第二套工具协议。

### P2：混合召回和查询路由

复用现有 LanceDB，为叙事记录生成可检索文本：

- BM25/关键词召回精确名称、场次和专有词；
- dense vector 召回语义相近的事件、目标和线索；
- RRF 合并排序；
- 只对命中种子扩展一跳类型化邻域；
- 返回原文证据片段，而不是只返回图边。

规则路由：

| 问题 | 首选通道 |
| --- | --- |
| 找台词、场景、原文措辞、局部事实 | `semantic_search` |
| 最近状态、开放线索、修订工单 | `story_memory_tool` |
| 跨章因果、关系演变、知情边界、长期线程 | Narrative GraphRAG |
| 势力结构、核心冲突、全局支线结构 | Graph global/k-core 摘要 |

实体识别先使用角色别名表、关键词和混合召回；只有无法确定种子时才调用 LLM。GraphRAG 默认返回证据包，让上层 Agent 推理；只有独立问答入口才调用专用回答模型。

## 8. 成本、速度与效果预期

| 改动 | 成本 | 速度 | 效果 |
| --- | --- | --- | --- |
| 规则路由，简单问题不走图 | 显著下降 | 查询更快 | 简单问题不降质 |
| chunk 级增量抽取 | 单次修改成本显著下降 | 长篇刷新大幅加快 | 与全量图一致时不降质 |
| 取消固定回答 LLM | 每次图查询少一次模型调用 | 明显降低延迟 | 专业 Agent 保留最终判断权 |
| `MultiDiGraph` + 时间/证据字段 | 存储小幅增加 | 图遍历变化有限 | 多跳、状态和一致性显著增强 |
| BM25 + dense + RRF + 一跳扩展 | 增加轻量检索计算 | 控制上下文后通常更快 | 召回和证据落地更稳 |
| k-core 全局摘要 | 无需额外 LLM 社区构建 | 全局查询更稳定 | 更适合结构中心性分析 |

以上是设计预期，不应在基准完成前写成确定提升比例。

## 9. 验证指标与回归测试

### 9.1 构建成本

- 全量构建 LLM 调用数、输入 token、P50/P95 耗时；
- 修改一个场景后的增量调用数和耗时；
- 新增、修改、删除源文件后的记录一致性；
- 增量图与同版本全量重建图的等价性。

### 9.2 查询效果

- 原文 evidence precision/recall；
- 跨章节多跳问答；
- 指定时间点人物状态；
- 秘密知情边界；
- 关系变化与触发事件；
- 伏笔/目标/冲突线程生命周期；
- 因果链缺口；
- 主支线交汇与孤立支线识别。

### 9.3 性能与安全

- query P50/P95、LLM 调用数和 token；
- GraphRAG 关闭后不得查询旧图；
- stale 图不得被静默使用；
- 附件缓存不得被替换工具修改；
- 并发聊天与 Agent 的搜索结果缓存不得串线；
- Showrunner/Lorebook 的工具绑定和提示词契约测试；
- 工具事件仍通过现有 communication/chatStore 链路展示。

## 10. 推荐实施顺序

1. 修复 P0 正确性与安全问题。
2. 给 Showrunner 增加 GraphRAG 只读能力，同时先实现 `analyze=structure_gaps/causal_chain/subplot_intersections` 三类前期操作。
3. 将图升级为 `MultiDiGraph`，补齐事件、线程、时间与证据字段。
4. 实现 chunk 级增量构建。
5. 增加混合召回、规则路由和证据包输出，取消常态化第二次回答 LLM。
6. 基准达标后，再考虑 k-core 全局层次；暂不投入复杂图嵌入或外部图数据库。

这条路线保持现有 UI/API 构建边界、工具门面、SemanticChunker、NetworkX、LanceDB 和 Agent 工具循环不变，主要升级 GraphRAG 服务内部的数据 schema、增量缓存和只读查询操作，属于可控的演进而非架构重写。

## 11. 论文链接

- [Narrative Knowledge Weaver（arXiv:2606.05724）](https://arxiv.org/abs/2606.05724)
- [Narrative World Model（arXiv:2607.05577）](https://arxiv.org/abs/2607.05577)
- [STAGE（arXiv:2601.08510）](https://arxiv.org/abs/2601.08510)
- [GroundedKG-RAG（arXiv:2604.04359）](https://arxiv.org/abs/2604.04359)
- [Core-based Hierarchies for Efficient GraphRAG（arXiv:2603.05207）](https://arxiv.org/abs/2603.05207)
- [GraphRAG-Router（arXiv:2604.16401）](https://arxiv.org/abs/2604.16401)
- [Do We Still Need GraphRAG?（arXiv:2604.09666）](https://arxiv.org/abs/2604.09666)
