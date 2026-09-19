# Contributing to SparkArc

SparkArc is a large multi-agent creative platform, not a small demo repository. Contributions are welcome, but reviewers will expect changes to respect the existing execution model, streaming boundaries, and shared infrastructure. Please read this guide before opening a pull request.

## Start here

Read these documents first:

- [AGENTS.md](../AGENTS.md) for the authoritative engineering rules
- [docs/project/architecture.md](../docs/project/architecture.md) for the current runtime and agent model
- [docs/project/database-migration.md](../docs/project/database-migration.md) if you touch persistence
- [docs/project/cicd-deployment.md](../docs/project/cicd-deployment.md) if you touch CI, packaging, or deployment behavior

The core rule is simple: **extend existing integration points instead of creating parallel pipelines**.

## Branch strategy

SparkArc uses two long-lived branches with different responsibilities:

- `dev`: day-to-day development, contributor PR integration, and fast iteration
- `main`: stable production line, official deployment source, and formal release base

If you are opening a normal feature or fix PR, target **`dev`** unless the maintainer explicitly asks for `main`.

## Architecture snapshot

SparkArc currently has two distinct collaboration systems:

- **Director orchestration**: the production path used today for task delegation and multi-agent coordination
- **Beacon / horn / flag semantics**: implemented as infrastructure, but horizontal autonomous agent-to-agent communication remains a reserved capability rather than the main execution path

If you are changing agent collaboration behavior, do not assume these two systems are interchangeable.
(The underlying Beacon Bus capability is already implemented end to end and may be enabled more broadly as model reliability and coordination quality continue to improve.)

## Two streaming pipelines you must not mix

SparkArc uses two separate stream protocols with different consumers and recovery rules.

### Chat pipeline (NDJSON)

- Frontend entry: `client/src/components/stores/chatStore.ts`
- Backend entry: `server/agents/routes/chat.py`
- Runtime base: `server/agents/communication.py`
- Event shape: `task_snapshot`, `assistant_delta`, `reasoning_delta`, `tool_*`, `task_done`, and related chat events

Important constraints:

- Tool events and assistant text may interleave
- Recovery uses the persisted event log plus `task_snapshot`
- Chat replay must not depend on destructive queue reads
- The in-progress assistant message must be updated in place rather than appended as a second assistant message

### Business task pipeline (SSE / semantic frames)

- Frontend entry: `client/src/utils/streamingRuntime.ts`
- Backend bridge: `server/agents/routes/streaming_utils.py`
- Semantic layer: `server/agents/stream_semantics.py` and `execution_core.py` (`server/agents/routes/stream_semantics.py` is a compatibility re-export only)

Important constraints:

- Long-running product tasks should flow through `createStreamingTask`
- Use the existing SSE readers and task lifecycle management
- Do not reuse chat delta semantics inside business-task consumers

## Shared infrastructure you are expected to reuse

SparkArc already has centralized bases for common operations. If your change overlaps with any of these, reviewers will expect reuse rather than reinvention.

- **Tool facade and registry**:
  - `server/agents/agent_tools.py`
  - `server/agents/tools/*`
  - `server/agents/tools/registry.py`
- **Patch / localized replacement**:
  - `server/agents/tools/common.py::_apply_patch`
- **Token chunking**:
  - `server/core/file_ingest/chunking.py::TokenTextSplitter`
- **Semantic chunking**:
  - `server/story/semantic_chunker/`
- **Streaming task hosting**:
  - `client/src/utils/streamingRuntime.ts`
- **Chat stream consumption**:
  - `client/src/components/stores/chatStore.ts`

Common review failures in this repository:

- Rebuilding stream readers or loading state logic inside page components
- Adding route-local business logic that should live in an agent, service, or shared helper
- Writing ad hoc regex or `.replace()` logic instead of using `_apply_patch`
- Re-implementing chunking behavior instead of using the shared chunking infrastructure
- Creating a second tool registry, facade, or agent-to-tool mapping source

## If you add or change an agent

Start by assuming the existing bases should still apply:

- `SparkBaseAgent`
- `SparkAgentExecutor`

### Registration and integration checklist

When an agent changes, confirm the affected registration points rather than adding hidden entry paths:

1. `server/agents/registry.py`
2. `server/agents/routes/runtime.py` when runtime lock or signal behavior changes
3. `server/agents/agent_tools.py` and `server/agents/tools/registry.py`
4. `server/agents/director_graph.py` when Director delegation behavior changes

### Prompt modality contract

Specialist agents are expected to keep three prompt modalities aligned:

- `system`
- `chat_system`
- `pipeline_system`

The current architecture also relies on:

- `_get_tool_prompt_references()` for format-spec reuse
- YAML `base` for shared prompt fragments
- YAML `tool_rules` for tool-execution supplement rules

If you update an agent prompt stack, read the corresponding sections in [AGENTS.md](../AGENTS.md) before changing anything. Reviewers will treat those rules as authoritative.

### Frontend mapping impact

If an agent changes UI identity, default placement, or orchestration touchpoints, check whether you also need to update:

- `client/src/components/share/GlobalChatFloat.vue`
- `client/src/composables/useAgentRegistry.ts`
- `client/src/components/lorebook/AgentFlowBlueprint.vue`
- `client/src/components/stores/agentRuntimeStore.ts`
- `client/src/components/lorebook/AiSettingsPanel.vue`

## If you change frontend behavior

Please keep these conventions intact:

- User-visible strings must go through Vue I18n
- Long-running flows should use `createStreamingTask`
- Chat stream consumption should stay inside `chatStore`
- Tool event UI linkage should continue to rely on backend-provided metadata
- Do not build standalone global-loading behavior outside the shared runtime path

If the UI behavior is not obvious from code review alone, include screenshots or short recordings in the pull request.

## If you change backend, persistence, or file IO behavior

- Do not scatter core business logic into route handlers
- Reuse existing service, factory, and agent execution layers before introducing new ones
- If a schema change is required, update `server/core/models.py` first and follow the project migration flow
- Do not hand-edit generated migration files
- Keep runtime temporary files, caches, indexes, and debugging output out of tracked test directories
- Use `/.tmp/` for generated validation artifacts

## Testing expectations

Run the checks that match your change surface. For anything that touches streams, orchestration, tool UI events, or shared runtime behavior, focused regression coverage is expected.

### Architecture contract tests

SparkArc keeps a small set of architecture contract tests for stable infrastructure. They must not call real LLMs, consume tokens, require API keys, or depend on external services. Use fakes, monkeypatching, temporary directories, and in-memory streams instead.
This is a stable-contract subset of backend tests, not a catch-all backend test directory; ordinary feature regression or short-lived bug regression should live closer to the behavior being exercised.

Prefer tests that protect stable contracts: event shapes, state-machine outcomes, registry consistency, replay/reconnect behavior, and shared runtime entrypoints. Avoid long-lived tests that snapshot full prompts, generated model text, fragile DOM structure, or incidental implementation details. If a test repeatedly needs large edits during normal bug fixing, move it closer to the stable protocol boundary or keep it as a short-lived business regression test.

```bash
cd server
python -m pytest test/architecture
```

```bash
cd client
npm run test -- src/utils/__tests__ src/components/stores/chat/__tests__ src/components/stores/__tests__
```

### Frontend baseline

```bash
cd client
npm run i18n:check:strict
npm run typecheck
npm run test
```

### Backend baseline

```bash
cd server
python -m pytest -vv -s test/
```

### High-value targeted regression coverage

When your change affects stream semantics, delegation, tool UI metadata, or long-running runtime behavior, reviewers will usually expect targeted regression coverage in addition to the baseline commands above.

Use the current test inventory under these paths as your source of truth:

- `server/test/`
- frontend Vitest suites under `client/src/`

In practice, changes in these areas should usually update or re-run the relevant tests for:

- chat stream events and history replay
- Director delegation and skip-confirmation behavior
- tool event UI metadata
- semantic stream runtime behavior
- shared frontend runtime / store behavior when corresponding Vitest coverage exists

The exact filenames may evolve as the repository changes, so prefer the currently present tests in those areas instead of copying stale command snippets from older documentation.

If your change affects packaging or release behavior, run the relevant local build path when practical.

## Pull request expectations

Good SparkArc pull requests usually include:

- A short explanation of the user or maintainer problem being solved
- The architectural touchpoints involved
- Notes about protocol boundaries, registration points, migration impact, or operator-facing effects when relevant
- The exact tests you ran
- Screenshots, logs, or reproduction notes when they materially help review

Please do not attach generated junk from tracked test directories. Use `/.tmp/` for debugging output.

## Security and disclosure

If you found a security issue, do **not** open a public bug report with exploit details. Follow [SECURITY.md](SECURITY.md) instead.

## License and branding reminder

SparkArc is licensed under AGPL-3.0-only unless a subcomponent explicitly states otherwise. The project name, logo, and brand identity are not automatically granted for third-party white-label use. Review the repository license, legal notes, and operator-facing boundaries before proposing redistribution or hosted-service changes.
