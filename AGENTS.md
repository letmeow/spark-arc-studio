# SparkArc AGENTS 指南（给 AI 助手与贡献者）

## 1. 文档目标

本文件是 SparkArc 项目的强约束指南。

首先是几条铁律：

- 这是一个庞大的项目，在任何更改前，你必须确保拿到足够的信息，对该链路足够了解，防止堆屎山。
- 任何改动都要优先接入现有统一管线，避免同一能力在多处重复实现。
- 任何新增能力都要做到“改一处，全链路受益”。
- 任何短平快修补都不能以破坏长期可维护性为代价。

### 1.1 Python 环境边界

`server/.runtime/python/` 是 Windows 一键启动脚本生成的便携运行时环境，仅服务于 `start.bat` 这类免配置启动链路。它不是开发者默认 Python 环境，也不是 AI 运行开发测试时的首选解释器。

开发 / 测试应优先使用 VSCode 当前选中的解释器、用户显式指定的 conda / venv / uv 环境（根目录找不到环境就考虑诸如conda env list寻找，需要灵活判断用户当前使用的是哪种包管理器）。

### 1.2 后端启动路径边界（用户部署 vs 开发者调试）

`start.bat` / `start.sh` 是**用户端部署入口**，不是开发者入口：
- 配合 Launcher 受管目录（`~/.sparkarc/sparkarc-server`）使用**用户专有的数据目录**，与开发仓库的 `server/_userdata` 不是同一份数据。

开发者 / AI 在开发测试时**必须直接启动代码**，严禁使用 `start.bat` / `start.sh`：

- 解释器：VSCode 当前选中的解释器（或用户显式指定的 conda / venv / uv 环境）；
- 入口：`server/app.py`，cwd=`server/`；
- 环境：`SPARKARC_SERVER_TRAY=0`，保持热重载开启（非便携 Python 下默认即开，或显式 `SPARKARC_SERVER_RELOAD=1`；对标 `.vscode/launch.json` 的 "Server" 配置）。

用错入口的后果：开发测试数据与用户数据目录错乱、改代码不生效（无热重载）、解释器缺依赖或版本不对。

## 2. 统一收口，不复制实现

SparkArc 现有架构已经有清晰收口层。新增功能必须先判断是否能接入现有收口点，而不是新开平行管线。

 后端收口重点：

 - 通讯层底座：server/agents/communication.py
 - 执行协议层：server/agents/agent_utils.py
 - 工具门面层：server/agents/agent_tools.py（统一门面） + server/agents/tools/*（内部实现）
 - 公共工厂 / 服务层：server/agents/agent_factory.py + server/agents/project_content.py + server/agents/auto_write_service.py
 - 多 Agent 调度层：server/agents/director_graph.py
 - 流式桥接层：server/agents/routes/streaming_utils.py
 - 业务语义层：server/agents/routes/stream_semantics.py + server/agents/routes/execution_core.py
 - 路由聚合层：server/agents/routes/__init__.py
 - 上下文布局层：server/agents/prompt_layout.py + server/agents/context_budget.py
 - **大统一工具性底层（大统一基建）**：
   - **局部替换与增量修改（Patch）**：统一收口在 `server/agents/tools/common.py` 的 `_apply_patch`。无论是剧本复写、大纲局部修改还是设定更新，凡是涉及“在已有文本中定位并替换”的逻辑，必须复用此底层，严禁各 Agent 自行实现正则或字符串替换。
   - **智能文本切分（Token Chunking）**：统一收口在 `server/core/file_ingest/chunking.py` 的 `TokenTextSplitter`（或通过 `server/agents/agent_style/text_splitter.py` 兼容重导出）。无论是上传附件、评审专家审稿、还是文风克隆分析，凡是涉及按 Token 数量切分文本的逻辑，必须复用此底层，避免 3 次以上重复实现。
   - **语义分块器（Semantic Chunker）**：统一收口在 `server/story/semantic_chunker/` 的 `SemanticChunker`。凡是涉及项目文件、知识图谱、向量索引的语义分块，必须复用此底层。
   - **长文档滑窗（Longread）**：统一收口在 `server/agents/longread/`（地图 + 线索账本 + 带线索折叠）+ `server/agents/tools/longread.py`（工具面）+ `server/agents/longread_store.py`（任务内存流转）+ `server/agents/worldview_source.py`（世界观逻辑切片视图）。凡是“全文可能超预算、需分片 + 按需读取”的长文本（附件、超长世界观，后续可扩展到大纲/角色聚合），必须接入此底座：开局看地图、读一片记一笔、折叠留线索 + 回跳指针。严禁各 Agent 自建第二套滑窗/占位符/账本。阈值与作用范围见 `docs/project/longread-thresholds.zh-CN.md`。
   - **基建扩展原则**：上述四项仅为当前最典型的工具性基建示例。**后续任何新增的、可能被多处复用的底层基础设施（如向量检索、缓存控制、文件解析等），必须遵循相似的“大统一”原则，先下沉至公共工具层或核心服务层，严禁在各业务线或 Agent 内部重复造轮子。**

前端收口重点：

- 流式任务入口：client/src/utils/streamingRuntime.ts（createStreamingTask）
- 全局遮罩统计：client/src/utils/loadingStats.ts
- 事件总线：client/src/eventBus.ts
- 全局加载 UI：client/src/components/share/GlobalLoading.vue
- 聊天流消费收口：client/src/components/stores/chatStore.ts

## 3. 两条主链路（必须分清）

### 3.1 聊天主链路（Chat NDJSON）

用途：自由对话、Director 调度、工具调用可视化。

标准链路：

1. 前端通过 chatStore/chatService 发起聊天流。
2. 后端路由在 server/agents/routes/chat.py。
3. Agent 侧通过 SparkBaseAgent.chat_stream 推送事件。
4. chat.py 为每个运行中任务创建 assistant 占位消息，并把事件写入 ChatTaskEntry 的 append-only event_log。
5. chat.py 输出 NDJSON 事件（task_snapshot、assistant_delta、reasoning_delta、tool_*、task_done 等）。
6. chatStore._consumeStream 统一消费并维护消息、segments、tool_traces。
7. chat.py 运行中持续 checkpoint 到同一条 assistant 消息，落盘 metadata.segments / metadata.tool_traces / stream_seq，保证刷新后时序可恢复。

关键事实：

- 聊天链路是 NDJSON，不是业务语义 onStart/onDelta 协议。
- 工具事件与正文可以交错出现，不能假设固定顺序。
- 前端刷新/重连恢复必须走 task_snapshot + afterSeq 游标回放；聊天链路不保留 progress_queue，禁止把 Queue 当 replay log 使用，也禁止用 get_nowait 这类破坏性读取作为恢复链路。
- 运行中的 assistant 消息必须复用同一条 DB 记录增量更新，完成后不可再 append 第二条助手消息。

### 3.2 业务任务主链路（SSE/语义流）

用途：长耗时业务任务，例如 production、style、auto_write、structure、lorebook、muse。

标准链路：

1. 前端创建 createStreamingTask(scope, target)。
2. 前端使用 consumeSSEReader/consumeTextReader/consumeNdjsonReader 消费流。
3. 后端路由通过 iterate_sync_iterable_in_thread 桥接同步生成器到异步响应。
4. 业务事件统一附加 onStart/onProgress/onDelta/onStats/onDone/onError/onCancelled。
5. 全局遮罩统一走 global-loading/cancel-loading 事件。

关键事实：

- 业务流由 streamingRuntime 统一托管，不要在页面里重复写一套“读取器 + 取消 + 统计”状态机。
- SSE 心跳、取消、统计逻辑已在主链路中沉淀，优先复用。

## 4. 后端扩展规则

### 4.1 新增 Agent：先复用双基座

新 Agent 默认应复用：

- SparkBaseAgent（通讯与聊天能力）
- SparkAgentExecutor（build_context -> execute -> write_result 执行协议）

参考文件：

- server/agents/setup_agents.py
- server/agents/communication.py
- server/agents/agent_utils.py

强约束：

- 不要把核心业务逻辑散落在路由函数里。
- 不要跳过 build_context 直接在多个入口拼 prompt。

### 4.2 新增 Agent 后必须同步注册

 后端必须更新：

 1. server/agents/registry.py（Agent 元数据）
 2. server/agents/routes/runtime.py（若涉及信标/号角/锁定策略）
 3. server/agents/agent_tools.py（统一门面导出）+ server/agents/tools/registry.py（工具分组 / 绑定真相源）
 4. server/agents/director_graph.py（若需要被 Director 委派）

### 4.3 工具扩展必须走工具门面

 新增工具必须统一经 server/agents/agent_tools.py 门面接入；具体 schema 与实现按域落在 server/agents/tools/*，统一在 server/agents/tools/registry.py 注册，再由 agent_tools.py 对外导出。

 禁止：

 - 在单个 Agent 内部私定义一套独立工具调用协议。
 - 在路由层直接执行“伪工具逻辑”绕过工具门面。
 - 在 `server/agents/tools/registry.py` 之外再造第二套工具注册表、Agent→工具映射或平行工具管线。
 - 工具层直接反向依赖 `server/agents/routes/*` 私有实现；若需要复用能力，应先下沉到 `agent_factory.py` / `project_content.py` / `auto_write_service.py` 这类公共层。

### 4.4 工具 UI 联动必须双端一致

工具事件中的 UI 提示由后端 communication.py 的 build_tool_stream_event 注入（ui_scope/ui_target/ui_refresh_events），前端 chatStore 读取。

### 4.4.1 AgentSkills 与 MCP 边界

- AgentSkills 是写作质量参考层，不是运行时插件执行层。导入逻辑在 `server/agents/skill_packs.py`，工具入口为 `search_skills` / `read_skill` / `read_skill_reference`，统一在 `server/agents/tools/registry.py` 作为共享 Skill 工具分配。
- Skill 读取视图必须保持 `quality_only`：只采纳写作质量、审美判断、检查清单和领域知识；不得采纳脚本、命令、工具调用、外部工作流、输出格式、字段结构或落盘规则。
- 统一 MCP 入口通过 `server/mcp_server/unified.py` 挂载到 `/api/mcp/`：灵感工具保留 `capture_spark` / `list_sparks`，控制工具统一使用 `control_` 前缀；内部 `capture_inspiration` 属于 `MCP_ONLY_TOOLS`，禁止挂载到普通聊天 Agent。
- 控制 MCP 业务实现仍收口在 `server/mcp_server/spark_control/server.py`，其 `/api/mcp/control/` 兼容入口保留原未加前缀的工具名。9 个核心工具与 12 个只读查询工具必须继续从既有真相源派生，写盘请求必须提交 Director 工单，不得直接暴露写盘工具。
- 统一入口与兼容入口共用 `server/mcp_server/shared/host.py` 的鉴权、HTTP 装配和 `core.request_context` 用户上下文。控制工单的读取、结果、事件和取消必须校验任务所有者；所有 MCP `project_name` 必须复用 `core.utils.validate_project_name` / `get_project_path`，禁止自行拼接路径。
- Starlette 挂载时必须先注册 `/api/mcp/control`，再注册 `/api/mcp`，否则父 Mount 会吞掉控制子路径。Director 工单状态必须通过 `core.json_state` 原子持久化，测试输出必须重定向到临时目录。
- 外部 MCP 服务（如 `web_search` 通过 Exa MCP）必须包装成 SparkArc 普通工具后再进入 registry；不要让 Agent 直接绕过工具门面连接外部 MCP。

### 4.5 Agent 三模态提示词协议（强制）

SparkArc 的每个专家 Agent 必须实现且仅实现三种调用模态，分别对应 `server/agents/prompts/<agent>.yaml` 的三个顶层字段。三种模态的运行态已由统一管线固定，贡献者只需保证 yaml 字段语义对齐。

| 模态 | 何时触发 | 使用字段 | 受众 | 行为约束 |
| :--- | :--- | :--- | :--- | :--- |
| **专有工作模式（Specialized Work）** | 业务路由 / 面板按钮 → `agent.execute()` / 具名方法（如 `expand_inspiration`、`generate_outline`）| `system` + `user` | 机器解析器 / 直接落盘 | 输出格式严格、可被解析器还原、禁止寒暄 |
| **用户交互模式（Chat Mode）** | 聊天路由 → `SparkBaseAgent.chat_stream(skip_tool_confirmation=False)` | `chat_system` | 真人用户 | 自然对话、可发散建议、不强制输出结构化格式 |
| **导演委派模式（Pipeline Mode）** | 导演 → `delegate_task` → `sub_agent_node` → `chat_stream(skip_tool_confirmation=True)` | `pipeline_system` | 导演（上游 Agent）| 按任务描述一次性产出 + 工具落盘 + 向导演简报，**产出规范与专有工作模式等价** |

运行态逻辑（禁止绕过）：

- 模式选择收口在 `server/agents/communication.py` 的 `chat_stream()` / `chat()` 里：`skip_tool_confirmation=True` 时优先取 `pipeline_system`；为 `False` 时优先取 `chat_system`；两者都缺才回落到 `system`。
- 导演委派时 `normalize_handoff_payload` 会强制把 `user_confirmation_state` 提升为 `not_required`，从而保证子 Agent 一定走 `pipeline_system`。
- 对应测试：`server/test/test_director_skip_confirmation.py`、`server/test/test_director_handoff_protocol.py`。

**`pipeline_system` 写法硬约束**：

1. **受众声明**：第一句必须明确"你的受众是导演，不是用户"，避免 LLM 代入头脑风暴/对话模式。
2. **三件套主干**：正文只写「调工具 + 一步到位 + 向导演简报」三件套，外加必要的反注入/反占位符提示。
3. **格式规范走 tool reference，不要复述**：详见下一节 §4.5.1。结构化产出规范（字段列表、Markup schema、禁止事项、结尾边界）应该通过 `_get_tool_prompt_references` 绑定到对应落盘工具，而**不是**把 `system` 里的规范复制粘贴到 `pipeline_system` 里——那样会双份维护、容易漂移。
4. **严禁无效引用**：禁止使用"与正常生成相同"、"格式同 system"、"参照默认模板"这类表述——两段 system 在代码里是**互斥选择**而非叠加，LLM 看不到另一个字段的内容。
5. **禁止头脑风暴式软约束**：`pipeline_system` 里不要出现"发散思维 / 打破常规 / 热情洋溢"这类与结构化产出冲突的语气修饰。

**`chat_system` 写法约束**：

1. 限定"对话模式下"的人设与语气，不要求任何严格输出格式。
2. 可以保留发散、建议、反问等对话风格。
3. 不要在这里重复结构化格式定义——防止用户只想聊天时反被套死。

**`system` 写法约束**：

1. 这是最严格的模式，所有结构化格式、字段定义、示例都应该放在这里。
2. 要配合 `user` 模板使用，由 `agent.execute()` 或具名方法直接传入。

违反以上任一项都会导致类似"导演委派灵感 Agent 时跑去构建世界观"这种模态串味问题（历史真实 Bug：Muse 未注册 tool reference，导致 pipeline 模式下 LLM 丢失 7 条格式规范）。

### 4.5.1 格式规范的唯一真相源：`_get_tool_prompt_references`

SparkArc 用「工具 reference 自动注入」机制避免在 `system` 与 `pipeline_system` 之间重复书写产出规范。

**运行态机制**：

- `server/agents/communication.py` 的 `_build_tool_prompt_reference_block()` 会在 LLM 被绑定工具时（无论 chat 还是 pipeline 模式），把 Agent 注册的「工具 → yaml 字段」映射展开为「当你决定调用工具 `rewrite_xxx` 时，必须复用以下既有生成规范：...」拼接到 system prompt 末尾。
- 注册点：每个 Agent 子类重写 `_get_tool_prompt_references()` 返回 `{tool_name: [{"prompt_key": ..., "field": "system"}]}`，并可用 `_get_tool_prompt_reference_values()` 为占位符提供默认填充（避免 LLM 看到字面 `{worldview}` 这类占位符）。

**最佳实践分类**：

| Agent 类型 | 示例 | 如何承载产出规范 |
| :--- | :--- | :--- |
| **有落盘工具** | muse / lorebook / showrunner / scriptwriter | ✅ 必须注册 `_get_tool_prompt_references`，把格式规范挂到对应工具的 yaml `system` 字段。`pipeline_system` 保持极简三件套。 |
| **无落盘工具**（产出直接给导演）| critic | ⚠️ 例外情况：tool reference 无处可挂。`pipeline_system` 必须内嵌 JSON schema / 产出字段清单的关键摘要。 |

**现状参考实现**（方便对照）：

- `MuseAgent._get_tool_prompt_references` → `rewrite_inspiration` 指向 yaml 顶层 `system`（7 条灵感规范）
- `WorldviewAgent._get_tool_prompt_references` → `rewrite_worldview` 指向 `rewrite_worldview.system`，`rewrite_all_characters` 指向 `generate_characters.system`
- `ShowrunnerAgent._get_tool_prompt_references` → 三个 rewrite_* 分别指向 `generate_synopsis.system` / `generate_beat_sheet.system` / `generate_outline.system`
- `ScriptwriterAgent._get_tool_prompt_references` → `create_or_rewrite_script` 指向顶层 `system`（含 `.arc` 规范 + `{arc_example}` 占位符）
- `CriticAgent`：**无落盘工具**，故不注册 tool reference；`critic.yaml/pipeline_system` 内嵌了五维审核 + 等级映射 + JSON 必填字段清单。

**贡献者常见错误**：

- ❌ 在 `pipeline_system` 里重复书写 `system` 里已有的格式规范，造成双份维护漂移。
- ❌ Agent 有落盘工具但忘记注册 `_get_tool_prompt_references`，LLM 调工具时看不到规范——这就是 Muse 历史 Bug 的本质。
- ❌ 把 Agent 专属工具的占位符（如 `{worldview}`）忘在 `_get_tool_prompt_reference_values` 里没提供默认填充，LLM 会看到字面 `{worldview}`。

### 4.5.2 通用基底：`base` 字段与 `{base.xxx}` 占位符

YAML 顶层 `base` 字段用于提取多模态共享的提示词片段（如身份声明、核心要求、审核维度等），避免在 `system` / `chat_system` / `pipeline_system` 之间重复书写。

**运行态机制**：

- `server/agents/agent_utils.py` 的 `load_prompt` 在加载 YAML 后，将 `base` 字典递归展平为 `base.xxx` 键值对，注入占位符替换的 kwargs（不覆盖用户显式传入值）。
- 子 prompt（如 `generate_synopsis`）加载时，`load_prompt` 会先加载完整 YAML 以访问顶层 `base`，再展平注入。
- 各模态字段通过 `{base.identity}`、`{base.core_requirements}` 等占位符引用共享内容，`_replace_placeholders` 自动替换。

**最佳实践**：

| 提取内容 | 示例 | base 键名 |
| :--- | :--- | :--- |
| 身份声明 | "你是一位**资深主编**" | `base.identity` |
| 核心要求 | "禁止废话 / 证据化审核" | `base.core_requirements` |
| 审核维度 | 五维 AI 味检测 | `base.review_dimensions` |
| 等级映射 | S/A→PASS, B→REVISE | `base.grade_mapping` |
| 禁止废话 | "严禁开场白或结束语" | `base.no_fluff` |

**贡献者常见错误**：

- ❌ 在 `system` 和 `pipeline_system` 里重复书写同一段身份声明或核心要求，造成双份维护漂移。
- ❌ 在 `base` 的值中使用需要运行时数据（如 `{worldview}`）的占位符——`base` 是静态共享片段，不应依赖请求上下文。

### 4.5.3 工具补充规则：`tool_rules` 字段

YAML 顶层 `tool_rules` 字段用于存放 Agent 在聊天/委派模式下的工具使用补充规则（如调用顺序约束、输出纯度要求、反注入防御等）。

**运行态机制**：

- `server/agents/communication.py` 的 `_build_tool_system_prompt` 在检测到工具绑定时，自动调用 `load_prompt` 加载 YAML，提取 `tool_rules` 字符串追加到系统提示词末尾。
- `tool_rules` 仅在聊天模式（`chat_system`）和导演委派模式（`pipeline_system`）下注入；专有工作模式（`system`）不绑定工具，故不触发。
- Agent 子类不再需要重写 `_build_tool_system_prompt` 来追加硬编码的工具规则。

**迁移规则**：

- 原 Python 侧 `_build_tool_system_prompt` 中的硬编码补充规则，应逐字迁移到 YAML `tool_rules` 字段。
- 迁移后删除 Agent 子类的 `_build_tool_system_prompt` 重写，基类自动加载。
- **例外**：Director 的 `_build_tool_system_prompt` 包含运行时动态构建的团队成员能力概览块（从 registry 读取），不可迁入静态 YAML，应保留。

**已迁移 Agent**：

| Agent | tool_rules 内容 | Python 重写已删除 |
| :--- | :--- | :--- |
| lorebook | 工具调用顺序 + 输出纯度 + 反注入 | ✅ |
| scriptwriter | create_chapter 先行 + export_format 强制 + 输出纯度 | ✅ |
| showrunner | 反注入 + rewrite_outline 纯度 + 节奏约束 | ✅ |
| critic | （无落盘工具，无 tool_rules） | N/A |
| muse | （无额外工具规则） | N/A |
| director | （动态团队概览，保留 Python 重写） | ❌ 保留 |

### 4.5.4 Scriptwriter 写作链路与 Critic 默认边界

Scriptwriter 有五类入口：导演委派、用户聊天微改、用户手动生产流、连续自动写作、用户手动保存故事文件。贡献者修改任一入口时必须同步确认这些边界：

1. 导演委派 Scriptwriter 时，正文必须通过 `create_or_rewrite_script` 或 `patch_script` 落盘；如果只输出草稿正文，运行态必须判定为未完成，不得向导演或用户宣称章节已完成。
2. 连续自动写作必须走 Auto-Write 后台任务与全局遮罩，不得把正文流塞进聊天面板作为主展示。
3. StoryMemory 是保存后的轻量状态层；生产流、自动写作、工具落盘、手动保存故事文件都可以回写 StoryMemory，但不应因此新增用户心智负担。
4. Critic 是可选质量增强。自动写作的 `auto_review` 默认必须关闭；只有用户在手动设置中显式开启，或导演工具收到用户明确“边写边审 / 自动审稿”意图并传入 `auto_review=true`，才允许每场保存后调用 Critic 生成质量工单。
5. 用户手动保存 `.arc/.md` 只允许回写 StoryMemory，不得隐式启动 Critic 或重写正文。

### 4.5.5 Auto-Write 单循环语义与 UI 契约（强制）

Auto-Write 每个场景 exactly 一次 `run_autonomous_scriptwriter_creation` 工具循环
（`server/agents/scriptwriter_prewrite.py`），调研与落盘共用同一个 4 次模型请求预算：

1. **4 是调研请求上限，不是正文分段数**：`PREWRITE_MAX_REQUESTS=4` 限制的是 `invoke`
   次数。落盘（`create_chapter` + `create_or_rewrite_script`）是单次原子写入，成功即
   结束本场。前端 `attempt/max_attempts` 永远读作“第几次调研请求”，落盘行不带计数。
2. **`write_started` 是调研/落盘的唯一分界**：后端 `report_creation_lifecycle`
   只看落盘工具是否已被调用，不看事件名。`model_request_*` 在调研轮次同样触发，
   禁止把它直接映射为 `phase=writing` 或“正在生成正文”。
3. **工具调用是非流式的，不存在逐字测速**：`llm.invoke` 一次性返回完整工具结果，
   正文藏在 `overwrite_content` 参数里。`scene_completed` 的
   `total_chars/elapsed/avg_speed` 是落盘瞬间的事后统计（`lastSceneChars` /
   `lastSceneSpeed` / `lastSceneElapsed` / `lastScenePreview`），前端展示为
   “本场落盘统计”，不得渲染成打字机进度。禁止恢复“逐字播报工具参数”的旧 SSE
   正文流——那会把未提交的草稿当成已完成内容展示，违反 §4.5.4 第 1 条。
4. **报错必须可见**：`model_request_failed` / `tool_failed` / 终端 `error` 在手动与
   Director 两种触发下走同一条 `generate_script_stream`，必须都落到遮罩。
   遮罩在 `error` 状态不得直接卸载；手动 `startManualWrite` 返回 `success:false`
   时 setup 面板必须展示 `result.error`，禁止静默吞掉。
5. **两种触发是同一引擎**：手动（`auto-write-start`，`from_director=False`）与
   Director（`trigger_auto_write`，`from_director=True`，经
   `__director_auto_write_started__` 旁路串通知前端）只差入口参数与 UI 标记，
   生成逻辑、状态机、恢复游标完全一致。`fromDirector` 只影响展示，不分支生成。

### 4.6 新增 Agent 的三模态自检清单

新增 Agent 时，以下所有项必须同时满足：

 1. `server/agents/prompts/<agent>.yaml` 同时定义 `system`、`chat_system`、`pipeline_system` 三个顶层字段。
 2. 若该 Agent 有落盘工具：必须在 Agent 子类重写 `_get_tool_prompt_references()`，把 yaml `system`（或对应子 prompt `system`）绑定 to 落盘工具；对应 Agent 的 `pipeline_system` 保持极简三件套（受众 / 调工具 / 简报）。
 3. 若该 Agent 没有落盘工具（产出直接给导演，如 critic）：必须在 `pipeline_system` 里直接内嵌产出规范的关键摘要（字段清单、等级标准等），不得引用式指向 `system`。
 4. 多模态共享的提示词片段（身份声明、核心要求等）必须提取到 YAML 顶层 `base` 字段，各模态通过 `{base.xxx}` 占位符引用，禁止在 `system` / `chat_system` / `pipeline_system` 之间重复书写。
 5. 若该 Agent 有工具使用补充规则（调用顺序、输出纯度、反注入等），必须写入 YAML 顶层 `tool_rules` 字段，由基类 `_build_tool_system_prompt` 自动加载；禁止在 Python 侧重写 `_build_tool_system_prompt` 追加硬编码规则。
 6. 对应 `SparkAgentExecutor` 的 `build_context` / `execute` / `write_result` 协议完整实现。
 7. `server/agents/tools/*` 中，该 Agent 落盘相关工具（如 `rewrite_xxx`）已按域实现，并在 `server/agents/tools/registry.py` 注册；`server/agents/agent_tools.py` 继续作为唯一公共导出与 `get_tools_for_agent` 门面。
 8. 若希望被导演委派，需在 `server/agents/prompts/director.yaml` 的"专家分工"速查表中列入。
 9. 新增测试覆盖三模态分别命中，对齐 `server/test/test_director_skip_confirmation.py` 的做法。

## 5. 前端扩展规则

### 5.1 不要绕过 createStreamingTask

所有需要遮罩、统计、可取消的流式任务必须通过：

- client/src/utils/streamingRuntime.ts

不要直接调用 loadingStats 或直接 emit global-loading 作为主方案。

### 5.2 聊天链路唯一收口是 chatStore

聊天流解析、tool event 桥接、segments/tool_traces 管理统一在：

- client/src/components/stores/chatStore.ts

禁止在组件里直接解析聊天 NDJSON 并自行维护状态。

### 5.2.1 聊天上下文布局与缓存前缀（强制）

聊天上下文必须维持“稳定前缀 + 动态尾部”的布局：

1. 固定/低频变化内容放在 `SystemMessage`：Agent 模态 prompt、语言策略、工具清单、确认规则、tool reference、tool_rules。
2. 当前编辑区、附件现场、用户本轮请求必须通过 `server/agents/prompt_layout.py` 的 `build_current_user_message()` 放入最后一条 user message，禁止重新塞回 system prompt。
3. 历史消息、压缩摘要与工具结果必须交给 `server/agents/context_budget.py` 管理预算；工具循环后必须继续使用 `rebudget_existing_messages()`，不要手写裁剪逻辑。
4. 长文档滑窗必须维持 `system（稳定）+ manifest（稳定）+ ledger（只追加）+ 当前窗口（一片，尾部）+ 本轮用户请求（最尾）` 布局：地图与账本一经注入只追加不改写；旧窗口折叠只在“尾部变前缀”（任务终态落盘 / 持久化前）发生一次，任务进行中只追加新工具结果、不反复改写中间历史。违反此条会从第一个被改的 ToolMessage 起让后续前缀缓存全部失效。收口：`server/agents/longread/` + `server/agents/tools/longread.py` + `server/agents/longread_store.py`。
5. AgentSkills 只能通过 `search_skills` / `read_skill` / `read_skill_reference` 按需读取；Skill 内容是动态工具结果，不得自动拼入 system 前缀，也不得覆盖输出格式、字段结构、工具协议或落盘规则。
6. 新增动态系统规则前必须评估是否会破坏 prompt cache 稳定前缀；能放到最后 user 的任务现场内容，不要放进 system。
7. 更换模型 / 平台、修改专家 prompt / `pipeline_system` / `tool_rules`、改变工具绑定、语言策略或部分全局参数，都会改变稳定前缀并导致上游缓存重新建立。文档和 UI 不得暗示缓存跨这些变更仍稳定命中。
8. 前端展示的窗口 token 与缓存命中来自后端 `context_window_stats`，完成时只从 `llm_usage.by_agent[当前窗口 agent_id]` 合并当前 Agent 的缓存命中；缓存命中为 0 时不显示，不要在前端自行估算。`llm_usage` 顶层是整个 chat task 的全链路汇总，可能包含导演委派的子 Agent，只能用于后台成本诊断，不得混入当前窗口命中率展示。

### 5.2.2 内容产出链路变更的缓存判断协议

新增工具、工具字段、数据格式、StoryMemory 状态、Agent 操作步骤或产出协议时，先判断它会不会改变 LLM 请求的消息顺序、消息角色、system 内容、工具集合、工具 reference、历史恢复方式或动态上下文注入位置。这里的判断不能只看“代码能否运行”：稳定前缀一旦在较早位置发生变化，后续内容即使完全相同也无法继续复用，可能同时放大 token 成本、延迟和长链路中每个后续请求的损失。

这不是禁止扩展结构，而是要求扩展时主动设计上下文边界：

1. 把静态协议、字段定义、工具 schema 和低频规则保持为确定性的稳定块；把本轮数据、当前状态、附件现场、用户意图和运行结果放在动态尾部。若新数据格式既包含 schema 又包含实例数据，通常应将 schema 固定在稳定块，将实例数据放在最后一条 user、工具结果或专用状态查询中。
2. 新增能力应尽量接入现有的消息布局、上下文预算、工具门面、StoryMemory 和委派历史收口点。不要为了缓存直接丢掉 Agent 完成任务所需的全局事实，也不要通过重复发送无效内容制造表面命中率；需要在动态认知、输出质量、总输入量和可复用前缀之间做有证据的取舍。
3. 如果确实需要改变稳定协议，应把它视为一次缓存版本变化，检查所有调用模态、所有受影响 Agent、工具调用闭合协议、历史恢复和落盘结果，并接受首次请求重新建立缓存。不要只检查新增功能本身而忽略同一 Agent 后续请求及被委派的子 Agent。
4. 完成后用请求原始日志或等价的结构化测试检查相邻请求：system、工具集合和 `prompt_cache_key` 是否按预期稳定，动态内容是否只在预期位置变化，消息历史是否保持合法且尽量追加，新增 token 是否带来实际能力而不是无效重复。上游未返回缓存字段时，只能报告本地前缀分析，不能把未报告当作命中或未命中。

AI 在修改内容产出链路时，应根据任务质量和协议需要自行判断采用哪种布局；但必须说明所识别的稳定块、动态块、潜在前缀断点、质量影响和验证结果。任何“看似只是增加一个字段或工具”的改动，都应先完成这项判断再进入实现。

### 5.3 新增 Agent 的前端映射检查清单

新增 Agent 时，除了后端注册，还需要检查以下前端映射点是否需要更新：

1. 视图默认 Agent 分配：client/src/components/share/GlobalChatFloat.vue（viewAgentMap）
2. 聊天气泡显示名/颜色/图标：后端 `server/agents/registry.py` 的 `name` / `icon` / `color` 是真相源，前端 `client/src/composables/useAgentRegistry.ts` 只负责读取与兜底。
3. Agent 流程蓝图布局与默认连线：client/src/components/lorebook/AgentFlowBlueprint.vue
4. 运行态 mock 数据（如保留）：client/src/components/stores/agentRuntimeStore.ts
5. 页面级快捷模型选择入口（如需要）：client/src/components/lorebook/AiSettingsPanel.vue 与对应视图

说明：并非每次都必须改全部文件，但必须逐项确认。

### 5.4 前端文案与国际化（强制）

前端新增或修改界面时，必须遵守以下约束：

1. **禁止硬编码用户可见文本**（按钮、标题、提示、占位符、错误文案等）。
2. 所有用户可见文本必须通过 Vue I18n 管理（`t(...)` 或等价封装）。
3. 新功能上线前需同步补齐四语词条：`zh-CN` / `en-US` / `ja-JP` / `ko-KR`。
4. 若历史代码存在硬编码，改动触及该区域时应顺手迁移到 i18n，避免债务继续扩散。

## 6. 协议边界与兼容要求

### 6.1 Chat NDJSON 与业务语义流不可混用

- 聊天侧消费器：chatStore._consumeStream
- 业务侧消费器：streamingRuntime 的 SSE/Text/NDJSON 读取器

不要把 onStart/onDelta 直接塞到 chatStore，也不要把 assistant_delta 套到业务页面语义消费器。

### 6.2 reasoning/think 兼容必须走既有解析器

后端：

- server/llm/agen_matchbox/reasoning_compat.py

前端：

- client/src/utils/streamingRuntime.ts（createThinkStreamParser）

禁止各业务线重复实现一版 think 标签解析器。

## 7. 迁移与数据红线（强制）

数据库结构变更必须遵循：

1. 修改模型定义：server/core/models.py
2. 生成迁移：server/gen_migration.py
3. 启动时自动迁移：server/core/auto_migrate.py + server/app.py 生命周期

严禁：

- 手工创建 Alembic 迁移文件
- 手工修改 Alembic 迁移文件
- 直接在运行数据库上手写 DDL 绕过迁移体系

参考禁令文档：

- server/alembic/DO NOT MANUALLY EDIT MIGRATION FILES!.md

## 8. 新增流程的推荐模板

### 8.1 若是“聊天内能力”

优先做法：

1. 先判断能否作为已有 Agent 的新工具。
2. 在 agent_tools.py 增加工具 schema + 实现。
3. 让 Director 通过 delegate_task 或工具调用触发该能力。
4. 在 communication/chatStore 保持工具事件可视化一致。

### 8.2 若是“独立业务流”

优先做法：

1. 在 server/agents/routes 下新增或复用业务路由模块。
2. 使用 iterate_sync_iterable_in_thread 桥接同步生成器。
3. 统一发送 onXxx 语义帧与 cancelled/error 终态。
4. 前端通过 createStreamingTask + consumeSSEReader 接入。

## 9. 反模式清单（禁止堆屎山与技术债）

为了确保代码的卓越质量，以下行为默认视为架构违规。贡献者在开发前必须熟读并严格避免：

1. 在多个路由复制同一段流式桥接逻辑，不抽到 streaming_utils。正确方式：统一在 `server/agents/routes/streaming_utils.py` 中使用 `iterate_sync_iterable_in_thread` 桥接同步生成器。
2. 在组件里手写全局遮罩协议，不走 createStreamingTask。正确方式：前端长耗时任务必须通过 `client/src/utils/streamingRuntime.ts` 中的 `createStreamingTask` 统一托管。
3. 在多个地方重复维护工具到 UI 的映射，且不同步后端 binding。正确方式：工具 UI 联动事件必须在后端由 `communication.py` 的 `build_tool_stream_event` 注入元数据，前端 `chatStore` 统一读取。
4. 在聊天与业务流之间混用事件协议，导致消费器耦合。正确方式：聊天流和独立业务流的协议边界隔离，聊天侧统一用 `chatStore` 消费 NDJSON，业务侧由 `streamingRuntime` 的 SSE 读取器消费语义帧。
5. 在 Agent 内直接写文件路径与 IO 细节，绕过 write_result 统一出口。正确方式：Agent 执行协议必须完整实现 `build_context` -> `execute` -> `write_result` 并统一进行文件落盘。
6. 为赶进度创建“临时入口”而不接入 registry/director_graph/tools 门面。正确方式：新增 Agent/流程/工具后，必须同步在 `registry.py` 等四大收口点完成注册并走门面导出。
7. 修改数据模型后不走迁移生成流程。正确方式：修改模型定义后必须通过 `python server/gen_migration.py` 自动派生 Alembic 迁移脚本，由系统启动生命周期自动执行升级。
8. 把测试运行过程中生成的缓存、索引、向量库、图谱、中间文件、导出结果直接写入被 Git 跟踪的测试目录（如 `server/test/`、`client/**/__tests__/` 或人工维护的 fixture / baseline 目录），导致版本库被运行产物污染。正确方式：测试或调试产生的中间临时产物必须强制写入根目录下的 `/.tmp/`，禁止污染 Git 库。
9. 在实现临时测试、调试脚本或一次性验证脚本时，默认把脚本或输出放入正式测试目录，验证后又遗留在仓库中。正确方式：临时脚本及其输出统一放入项目根 `/.tmp/tests/<本次任务>/`，验证完成后在当前任务结束前全部删除；值得长期保留的场景应重写为所属领域的正式回归测试。
10. 自行编写正则表达式或 `.replace()` 方式进行文本的定位和局部替换。正确方式：凡涉及在已有文本中定位并替换的逻辑，必须复用 `server/agents/tools/common.py` 的 `_apply_patch` 统一底层。
11. 在业务层自写字符或段落 `split()` 等简陋方法来切分长文本。正确方式：涉及分块的逻辑，必须复用 `TokenTextSplitter`（按 Token 切分）或 `SemanticChunker`（语义分块）基建底座。

以上内容不代表全部，实际工程中还有许多类似逻辑，请灵活运用
---

## 10. 最小回归测试清单

涉及聊天链路、工具事件、多 Agent 委派、流式语义时，必须执行回归测试。测试文件可能会随架构演进而动态变化，贡献者应遵循以下测试指导原则：

### 10.0 基础建筑测试（长期护栏）

项目已建立一组“基础建筑测试”，专门覆盖稳定协议与统一收口层，而不是覆盖大模型输出质量或具体业务文案。
这组测试只是后端测试体系中的一个长期子集，不是“后端测试”的同义词；普通功能回归、短期 bug 回归、模块级验证应放在更贴近业务语义的测试位置，如没有测试位置，可以新建目录。

**强制原则**：

- 基础建筑测试禁止调用真实大模型、联网搜索、远程 API、真实 token 鉴权或计费型上游服务。
- 需要模型、流、网络或数据库行为时，必须使用 fake / monkeypatch / 内存对象 / 临时目录。
- 测试目标是“统一管线是否仍成立”：Agent 三模态、工具注册真相源、Chat NDJSON 时序、业务流桥接、前端 reader、工具 UI 绑定、公共 patch / chunk / migration 基建。
- 这类测试应保持低维护成本。新增 Agent 或工具时可以小幅扩展白名单/断言；不得把易变 prompt 文案或真实生成内容写成脆弱快照。

**当前基础建筑测试位置**：

- 后端：`server/test/architecture/`
  - `test_agent_prompt_contracts.py`：Agent 三模态、pipeline 受众声明、tool reference 契约。
  - `test_tool_registry_contracts.py`：工具注册表、工具门面、后端工具 UI 元数据。
  - `test_chat_stream_contracts.py`：ChatTaskEntry / accumulator / observer / retry 契约。
  - `test_streaming_bridge_contracts.py`：同步生成器到异步流桥接、业务语义帧。
  - `test_common_infrastructure_contracts.py`：`_apply_patch`、`TokenTextSplitter`、迁移路径规格。
  - `test_matchbox_startup_contracts.py`：火柴 Agent 网关启动期懒加载契约。
- 其他后端业务/功能回归目录：
  - `server/test/agents/`、`server/test/agent_skills/`：Agent 默认行为与 AgentSkills 功能回归。
  - `server/test/auth/`、`server/test/projects/`：账号、权限、数据归属与项目生命周期回归。
  - `server/test/chat/`、`server/test/context_budget/`：聊天功能与上下文预算策略回归。
  - `server/test/attachments/`：聊天附件切分、超窗清单降级、附件 GC 回归。
  - `server/test/longread/`：长文档滑窗底座（地图稳定、线索账本、带线索折叠、世界观转滑窗）回归。
  - `server/test/director/`：导演调度、委派边界、自动写作触发等业务护栏。
  - `server/test/graphrag/`：知识图谱、语义分块、项目文件收集等检索能力回归。
  - `server/test/image_generation/`、`server/test/web_search/`：外部能力适配器与供应商配置回归。
  - `server/test/matchbox/`、`server/test/llm/`：模型配置、模态与兼容网关回归。
  - `server/test/mcp/`：MCP 服务、工具暴露与工单行为回归。
  - `server/test/story_context/`：大纲场景契约、生产上下文包、Scriptwriter 交接上下文。
  - `server/test/story_memory/`：StoryMemory 状态吸收、任务包、显式吸收接口、自动写作记忆回写。
  - `server/test/style/`：文风档案格式化、风格注入、风格相关生成路径。
- 说明：`server/test/architecture/` 只放长期契约与基础建筑护栏测试；普通业务回归、页面/接口功能回归或具体 bug 回归必须放入所属领域目录。只为当前任务提供证据的一次性验证属于临时测试，应放入 `/.tmp/tests/` 并用完删除。测试名称里已经出现明确业务对象（如 StoryMemory、GraphRAG、风格、导演委派）时，优先放入对应业务目录，除非它真的在守护全局基础协议。

**测试放置决策（新增测试前必须逐项执行）**：

1. 先定位被测生产模块或业务能力，并在 `server/test/` 下寻找同名或同领域目录；默认放在该领域目录。
2. 不存在合适目录时，创建职责单一、可长期复用的领域目录；禁止因为“不知道放哪”而塞入 `architecture/`。
3. 只有同时满足以下三项才允许进入 `architecture/`：守护跨模块稳定不变量；直接覆盖统一 registry / facade / pipeline / protocol / 公共基建；普通产品迭代不会频繁改动断言。
4. 单次 bug 的严重程度、修复紧迫度或复现难度都不是架构测试的判据。决定长期保留的回归测试必须放到缺陷所属领域，只有能抽象为稳定的跨模块协议时才可升格。
5. 主要断言具体提示词措辞、供应商参数、单个接口结果、README / Dockerfile / 源码字符串或某次补丁实现细节的测试，禁止放入 `architecture/`；应改为行为测试、放入领域目录，或在价值不足时删除。
6. 文件命名使用 `test_<能力>.py`；不要用 `contracts`、`architecture` 等后缀给普通功能测试伪装层级。测试函数命名应表达“条件 + 预期行为”，不要使用缺陷编号或“临时测试”。
7. 新增目录或无法判断层级时，先阅读 `server/test/README.md`；评审时必须把测试位置本身作为审查项。
- 前端：
  - `client/src/utils/__tests__/streamingRuntime.architecture.spec.ts`
  - `client/src/components/stores/chat/__tests__/toolUi.architecture.spec.ts`
  - `client/src/components/stores/__tests__/chatStore.stream.architecture.spec.ts`

### 10.0.1 临时测试生命周期（强制）

**临时测试**是只为本次调查或实现提供证据、验证结束后不承担防回归责任的一次性代码。例如：验证一个小函数当前输出、探测某个假设、打印中间状态、复现一次环境问题、确认某段迁移数据是否正常。

临时测试不是“较小的正式测试”，也不是“先随手放进测试目录以后再说”。仓库只允许两种测试状态：

- **临时验证**：不进入正式测试树，用完删除。
- **正式回归测试**：有明确长期守护价值，放入所属领域目录并持续维护。

AI 创建临时测试时必须遵守：

1. **固定位置**：统一放在仓库根 `/.tmp/tests/<本次任务>/`。禁止放入 `server/test/`、`client/**/__tests__/`、`tests/` 或任何可能被正式测试命令自动收集的目录。
2. **明确命名**：使用 `verify_*.py`、`probe_*.py`、`inspect_*.ts` 等一次性名称，不使用 `test_*.py` / `*.spec.ts`，避免伪装成正式测试或被默认收集。
3. **显式运行**：临时脚本只能按明确路径单独执行；不得把 `/.tmp/tests/` 加入 pytest、Vitest、CI、coverage 或项目测试配置。
4. **用完即删**：得到验证结论后，在当前任务结束前删除临时脚本及其输出。即使 `/.tmp/` 已被 `.gitignore` 忽略，也不得把清理责任留给用户。
5. **先判定再升格**：如果测试揭示了可复发的生产缺陷，并且断言稳定、有长期防回归价值，应将核心场景**重新编写**为正式测试，放入对应领域目录；不要把临时探针原样移动过去。
6. **架构升格更严格**：只有正式领域回归还能进一步抽象成跨模块稳定协议时，才允许写入 `architecture/`。
7. **结束前检查**：汇报完成前检查 `/.tmp/tests/<本次任务>/` 已清理，并检查 Git 状态确认临时文件没有进入跟踪范围。

判断口诀：**只回答“现在是否正常”就用临时验证并删除；要防止“以后再次坏掉”才写正式回归测试。** 不允许把“短期测试”提交进仓库等待未来清理。

### 10.0.2 AI 维护测试站协议（强制）

长期测试不是业务实现的影子副本，而是统一协议和架构不变量的护栏。测试过时优先重审测试层级，禁止为了变绿而无解释地削弱契约。

AI 新增或修改测试时必须遵守：

1. **先说明守护对象**：每个长期测试文件顶部应能看出它守护的协议或收口层。新增正式测试前先判断它属于基础建筑测试、烟雾集成测试还是领域业务回归测试；仅服务当前任务的验证必须按上一节作为临时测试处理。
2. **测协议，不测实现细节**：优先断言事件名、事件形状、状态机终态、注册表一致性、恢复/重连/回放能力、工具 UI 元数据、统一入口是否被使用。禁止把 prompt 完整文案、DOM 细碎层级、CSS class、临时变量名、LLM 生成正文写成长期断言。
3. **测收口，不测每个使用点**：优先测试 `streamingRuntime.ts`、`chatStore.ts`、`streaming_utils.py`、`stream_semantics.py`、工具 registry / facade、`_apply_patch`、`TokenTextSplitter` 等统一底座。页面级测试只做少量烟雾覆盖。
4. **测不变量，不滥用快照**：长期测试应断言“必须存在/必须完成/必须回放/必须走统一门面”这类不变量。除非用户明确要求，禁止新增整段 HTML、整段 prompt、整段生成结果的脆弱快照。
5. **禁止真实上游依赖**：基础建筑与常规回归测试不得调用真实 LLM、消耗 token、依赖 API key、联网搜索、访问远程服务或读取用户真实项目数据。需要外部行为时使用 fake / monkeypatch / 临时目录 / 内存流。
6. **失败先判因，再改测试**：测试失败时，AI 禁止直接改断言变绿。必须先判断是代码回归、架构契约有意变化、测试层级错误、fixture 过时，还是环境依赖问题。只有确认是“契约有意变化”或“测试测错层级”时，才允许修改测试；否则应修代码。
7. **新增 bug 回归要下沉**：需要长期防止复发的 bug 测试应放入所属领域目录；仅用于定位和确认修复的探针属于临时测试，必须放在 `/.tmp/tests/` 并在任务结束前删除。不得在正式测试树中保留含糊的“短期回归”。
8. **维护成本红线**：如果某个测试在普通业务迭代中频繁大改，优先重构测试到更稳定的协议边界，或拆成“基础建筑测试 + 少量业务烟雾测试”。不要把大段业务规格复制进测试。

推荐在长期测试文件顶部写明：

```python
"""
守护对象：
- Chat NDJSON 事件可重放
- segments/tool_traces 时序不丢失
- 中间错误不会污染最终 event_log

本测试禁止：
- 调用真实 LLM
- 连接真实外部服务
- 依赖具体 prompt 文案
"""
```

### 10.1 测试指导原则
1. **后端测试原则**：
   - 任何涉及聊天流（Chat Stream）或 NDJSON 事件的改动，必须回归聊天事件流、历史时序分段（Segments）以及工具 UI 元数据的测试。
   - 任何涉及导演（Director）调度、委派协议（Handoff）或免确认策略的改动，必须回归多 Agent 调度图与委派协议测试。
   - 任何涉及业务语义流（SSE）的改动，必须回归流式语义运行态测试。
   - 测试输入样例、人工维护的 fixture / baseline，与测试运行时生成的缓存、索引、临时图谱、序列化产物必须严格分离；只有前者允许进入版本库。
   - 若测试需要生成 FAISS / pickle / GraphML / JSON 索引、缓存文件或其他中间产物，统一写入项目根 `/.tmp/` 下按用途分组的子目录；**禁止**直接写回 `server/test/` 及其已跟踪子目录。
   - AI 助手在新增或修改测试时，若需要落盘中间结果，必须先检查目标目录是否受 Git 跟踪；拿不准时默认写入项目根 `/.tmp/`，而不是把输出塞进现有测试目录。
2. **前端测试原则**：
   - 任何涉及聊天流消费、工具事件桥接的改动，必须回归 `chatStore` 单元测试。
   - 任何涉及流式任务托管、取消、统计的改动，必须回归 `streamingRuntime` 单元测试。
   - 任何涉及全局加载遮罩、聊天气泡渲染的改动，必须回归对应组件的挂载与事件测试。

### 10.2 推荐测试命令（按需裁剪）
- **后端测试**：进入 `server` 目录，使用 `pytest` 运行 `test/` 目录下对应的测试脚本。例如：
  ```bash
  cd server
  # 运行基础建筑测试（不调用真实大模型或外部鉴权）
  pytest test/architecture

  # 运行聊天与工具事件相关测试
  pytest test/architecture/test_chat_stream_contracts.py test/architecture/test_tool_registry_contracts.py test/chat
  # 运行导演调度与委派协议相关测试
  pytest test/director
  # 运行流式语义相关测试
  pytest test/architecture/test_streaming_bridge_contracts.py
  ```
- **前端测试**：进入 `client` 目录，使用 `npm run test` 运行对应的 `.spec.ts` 测试。例如：
  ```bash
  cd client
  # 运行基础建筑测试（reader / toolUi / chatStore 最小流消费）
  npm run test -- src/utils/__tests__/streamingRuntime.architecture.spec.ts src/components/stores/chat/__tests__/toolUi.architecture.spec.ts src/components/stores/__tests__/chatStore.stream.architecture.spec.ts

  # 运行 Store 与工具类测试
  npm run test -- src/components/stores/__tests__/chatStore.spec.ts src/utils/__tests__/streamingRuntime.spec.ts
  # 运行全局 UI 组件测试
  npm run test -- src/components/share/__tests__/GlobalLoading.spec.ts src/components/share/__tests__/ChatMessageList.spec.ts
  ```

---

## 11. 提交前自检

提交前请逐项确认：

1. 新能力是否接入了既有统一收口层。
2. 是否避免了页面层/路由层重复状态机。
3. 后端与前端的工具 UI 映射是否双端一致。
4. 数据变更是否遵守迁移流程。
5. 是否补齐了对应回归测试并确保通过。
6. 新增临时测试、调试脚本或一次性验证逻辑时，是否将脚本和输出都放入 `/.tmp/tests/<本次任务>/`，并已在汇报完成前全部删除；需要长期保留的场景是否已重写为所属领域的正式测试。
7. 新增的长耗时物理任务是否显式配备了并发写锁保护，且前端消费重连流时是否遵循了 clientId 校验规约。
8. 公共服务或工具类是否完全独立，无任何指向路由层的反向依赖。

如果以上任一项答案为“否”，先修正架构再提交。

## 12.AI权限安全红线
【FORBIDDEN】未经用户明确语言要求的情况下，AI助手仅允许使用【只读型】git命令！
考虑到相当一部分**用户默认开启了自动批准请求**，你必须小心再小心，对于git写入操作不能依赖于自动批准。你要明确用户的主观意图。**禁止把自动批准当成用户的主观行为**。
严禁执行任何git的提交、推送或者其他可能导致写入的行为！！！
未经许可使用GitHubCLI等工具操作远程是绝对禁止的行为！！！
 
