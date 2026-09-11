# Matchbox Gateway Contributing Guide

## 1. Scope

This guide applies to the standalone `agen-matchbox` package. The goal is to keep multi-user routing, quota enforcement, key security, and agent compatibility stable across changes.

## 2. Core Principles

- **DB is the runtime authority.** `matchbox_cfg.yaml` (structures) + `matchbox_key.yaml` (keys, keyed by `platform_key`) are only used for bootstrap/incremental sync/export. All business reads go to `llm_config.db`.
- **Preserve the unified call chain:** `initialize_matchbox()` -> `matchbox()` -> `get_user_llm(...)` (production) / `get_spec_sys_llm(...)` (lightweight testing).
- **Light init, heavy warmup:** `initialize_matchbox()` must remain lightweight (DB schema + default config sync only). Heavy runtime deps (`langchain_openai`, `ChatUniversal`, `LLMClient`) are loaded via `warmup_matchbox_runtime()` or lazy-loaded inside `_load_chat_runtime()` at first use. `import config` must have no filesystem side effects (`DEFAULT_PLATFORM_CONFIGS` is lazy).
- **Keep quota scopes separated:** `sys_paid` (hosted key) and `self_paid` (user key) must remain independent tracks. Pre-call interception order is quota → credit.
- **Usage writes are async by design:** `UsageTrackingCallback` only submits to `usage_writer`; `usage_recorded_handler` fires after a successful commit. Never reintroduce synchronous DB writes on the model-return path.
- **No host imports inside the gateway:** host behavior (headers, prompt-cache context, migrations) goes through `integrations.py` / env vars / `matchbox_adapter`-style host adapters, never `from core...` / `from agents...`.
- **Never commit secrets:** No plaintext API keys, `matchbox_key.yaml`, `.env` files, or private config material in commits.

## 3. Initialization Architecture

The package exposes a two-phase startup model. Understanding this is essential before modifying init-related code:

### Phase 1: `initialize_matchbox()` (lightweight)

Creates the `AIManager` singleton, sets up DB engine/session, syncs YAML defaults to DB, and resolves default platform/model IDs. This phase intentionally does **not** import `langchain_openai`, `ChatUniversal`, or any heavy SDK modules.

### Phase 2: `warmup_matchbox_runtime()` (heavy, non-blocking)

Pre-imports `.gateway` and `.tracked_model` in a background thread so that the first `get_user_llm()` call does not block on module loading. Callers can pass `blocking=True` if synchronous warmup is needed (e.g., test scripts).

### Skip conditions

- `AGENT_MATCHBOX_DISABLED=1` disables the manager entirely (returns `None`).
- Migration runners should set `AGENT_MATCHBOX_DISABLED=1` before importing Matchbox. Host adapters may preserve legacy command detection, but the generic component does not inspect host-specific command names.

## 4. Recommended Change Patterns

- **Extend, don't duplicate:** Add new functionality via `manager.py` mixins (or new focused modules like `seed_sync.py` / `usage_writer.py` / `retrying.py`) rather than copying logic into route handlers.
- **Migrations live on the host side:** the gateway ships no Alembic dependency. Host Alembic branches should reuse `agen_matchbox.models.Base.metadata` via their own `load_metadata`. Keep `Base.metadata.create_all()` as a fallback for non-Alembic environments.
- **GUI and API semantics must align:** Platform, model, usage slot, and quota policy changes must be reflected in both `gui/` panels and REST API responses.
- **Lazy-load heavy deps:** Any new module that depends on `langchain_openai`, `tiktoken`, or similar heavy packages should follow the `_load_chat_runtime()` pattern in `builder.py` — load on first call, not at import time.
- **Tests live with the gateway:** gateway behavior changes must ship with offline tests under `tests/` (`pytest tests -q`: no host imports, no real LLM, no external network). Host repos keep only host-specific contract tests (auth, routes, tool registries).

## 5. Agent Ecosystem Compatibility

- This gateway is the runtime foundation for agents. Preserve LangChain/LangGraph compatibility.
- Do not break function-calling, streaming behavior, or reasoning field normalization (`ChatUniversal` in `gateway.py`).
- The `LLMClient` wrapper must remain directly usable as an LLM (`client.invoke()`, `client.stream()`).
- If default behavior changes, update README and usage examples in the same PR.

## 6. Key Files Reference

| File | Responsibility |
|------|---------------|
| `__init__.py` | Package entry: `initialize_matchbox`, `warmup_matchbox_runtime`, `matchbox`, lazy exports |
| `manager.py` | `AIManager` core class (all mixins composed) |
| `config.py` | Constants (`USE_SYS_LLM_CONFIG`, `LLM_AUTO_KEY`, `SYSTEM_USER_ID`), YAML/key file/env loading |
| `builder.py` | `LLMBuilderMixin` — resolves user choice, builds `LLMClient` |
| `gateway.py` | `ChatUniversal` (reasoning-aware ChatOpenAI subclass), `create_quick_llm/embedding` |
| `tracked_model.py` | `LLMClient`, `LLMUsage`, `UsageTrackingCallback` (async-submit, never blocks) |
| `usage_writer.py` | Background single-thread usage persistence |
| `seed_sync.py` | YAML seed→DB sync algorithm (unit-testable, no DB required) |
| `retrying.py` | tenacity probe/test retry policy (ops-side only) |
| `models.py` | SQLAlchemy schema (platforms, models, usage slots, quota policies) |
| `security.py` | `SecurityManager` — Fernet encryption for API keys |
| `quota_services.py` | `sys_paid`/`self_paid` quota enforcement |
| `usage_services.py` | Time-series usage logging and aggregation |
| `credit_services.py` | Credit balance management and enforcement |

## 7. Pre-PR Checklist

- [ ] No sensitive keys or private data committed.
- [ ] `initialize_matchbox()` remains lightweight — no heavy SDK imports at this stage, and `import config` has no filesystem side effects.
- [ ] `warmup_matchbox_runtime()` is called by host applications (not by the library itself).
- [ ] Gateway changes ship with offline tests under `tests/` and `pytest tests -q` passes.
- [ ] No new `from core...` / host-specific imports inside the gateway package.
- [ ] User model selection and usage-slot behavior remain compatible.
- [ ] Quota accounting and charging flows still work as expected.
- [ ] New code follows the lazy-loading pattern for heavy dependencies.
- [ ] Documentation is updated accordingly.
