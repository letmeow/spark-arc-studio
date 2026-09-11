# Matchbox Agent Gateway: Full-Featured LLM Gateway Built for Agents
[简体中文](README.md) | [English](README.en.md)

![MatchGateway App](./app.png)
![MatchGateway App](./app.png)

Matchbox Agent Gateway is built for Agent development. It is a powerful and extremely flexible LLM routing and quota control center. **It is lightweight, requires no deployment, and is deeply integrated into agent development and management**. While targeted at today's most professional and versatile agent orchestration frameworks like **LangChain/LangGraph**, it **can be very easily migrated to AutoGen, CrewAI, and other powerful agent frameworks**—just ask your Coding Assistant to adapt it for your framework.

Designed to support scenarios ranging from personal development and debugging to multi-user production environments, this project also provides a graphical user interface (GUI) to simplify core configuration management.

> 💡 **Why "Matchbox"?**
> Matches are the raw material to ignite the fire of intelligence. In this era of AI democratization, site owners building AI applications are like "The Little Match Girl" (~~Tokens aren't selling, please help us~~).
![MatchGateway Slogan](./slogan.jpg)

### 🔥 Why Choose an Embedded Gateway?

Although specialized external gateways (such as NewAPI, LiteLLM, etc.) are powerful, they often introduce an integration gap when coupled with complex applications. As an **embedded gateway**, this manager offers the following unique advantages:

1. **Seamless Integration with the Agent Orchestration Ecosystem**:
   The embedded gateway operates directly at the application code layer. It seamlessly propagates context, function calling definitions, and even special structured output formats throughout the agent orchestration process. This effectively avoids the network latency and connection drops caused by multi-hop external routing, and completely eliminates protocol compatibility issues when external gateways proxy streaming responses.
2. **Flexible Support for "System Hosting" & "User Customization (BYOK)"**:
   Perfectly supports commercial and multi-tenant user scenarios. It can either be **system-hosted** (administrators configure a global LLM pool for users to use out-of-the-box) or support **BYOK (Bring Your Own Key)**. Encrypted isolation of user data is performed directly within the application, eliminating the need to sync accounts and generate tokens in a separate external gateway.
3. **Native Multi-Metric Quota Management**:
   Features a built-in quota and rate-limiting mechanism. The system binds directly to the application's native User ID, clearly and automatically distinguishing between "System Paid" (consuming the administrator's hosted keys) and "Self Paid" (BYOK). These two quota metrics are managed independently, allowing the application layer to block/verify requests in a fraction of a second before they are forwarded to the provider.
4. **Unified & Extremely Simplified Operations**:
   No need to deploy Redis or configure complex OneAPI Docker containers. Billing, auditing, model management, and usage statistics are all elegantly handled within this service via built-in SQLite / SQLAlchemy, dramatically reducing the operational cognitive load of local development, debugging, and private deployments.

## ✨ Core Features

- **Multiple Operation Modes**:
  - **No-User / Global Single-User Mode**: Suitable for backend services, personal tools, or local development and debugging. All requests share a system-level LLM configured via environment variables.
  - **Multi-User Fixed-Platform Mode**: Suitable for scenarios where model quality and source must be strictly controlled. All users share the system's pre-configured platforms but can use their own API keys.
  - **Multi-User Custom-Platform Mode**: Provides maximum flexibility, allowing each user to freely add and manage their own LLM platforms and models.
- **Unified Interface**: Regardless of how the backend configuration changes, developers can retrieve the LLM instance for a specific user/purpose via `matchbox().get_user_llm(user_id, usage_key="fast")`.
- **Intelligent Reasoning Stream Adaptation (No Blank Waiting)**: The gateway is **OpenAI-compatible** and supports dynamic detection and auto-conversion of common reasoning fields (e.g., `reasoning_content` and `<think>` tags) to **unify them into a continuous reasoning stream**. This ensures that the frontend maintains a superior pure-streaming experience when running deep thinking models.
- **Strict Tool Schema Compatibility**: Function tools are normalized to valid JSON Schema at the request boundary. Object schemas with no required parameters explicitly receive `required: []` for strict implementations such as Grok. This is a general OpenAI-compatible protocol rule: it does not branch on domains or model names, and it does not consume or rewrite `extra_body`.
- **Multi-Purpose Model Binding Slots**: Maintains multiple purpose slots per user (e.g., `main`, `fast`, `reason`) and allows users to define custom slots and bind them to different models as needed.
- **System and User Isolation**: Clearly distinguishes between "System Platforms" and "User Private Platforms". System platforms are managed by the configuration file (`matchbox_cfg.yaml`), while user platform data is stored in the database.
- **Flexible Key Management**:
  - It is highly recommended to manage API keys using **environment variables** to avoid hardcoded keys and improve security.
  - Users can provide their own API keys for shared system platforms, helping distribute costs.
  - Provides the `LLM_AUTO_KEY` option, allowing the system to automatically fall back to server keys when no user key is provided (use with caution).
- **Split Quota Mechanism by Funding Source**:
  - Calls are automatically categorized into `sys_paid` (consuming system-hosted keys) and `self_paid` (consuming the user's own keys).
  - Both scopes support "Every N hours quota window" and "Total quota", and perform interception before forwarding actual LLM requests.
- **Redeem Code System**:
  - Administrators can batch create, revoke, and delete redeem codes, setting the amount of redeemable points.
  - Supports two types: `single` (one-time use, immediately invalidated after redemption) and `per_user` (redeemable once per user, like a global server benefit).
  - Redeem codes are randomly generated with a 20-character string (alphanumeric, excluding ambiguous letters like I/O/l/o) by default, and also support custom inputs.
  - Users can recharge system points using redeem codes, with fully traceable transaction records.
- **Dynamic Model Probing**: Built-in model detection tool (`probe_platform_models`) capable of fetching model lists from any OpenAI-compatible provider.
  - **Reasoning Content / Billing Fields Visualization (Platform Test)**: The GUI "Test Model" displays the raw JSON response. Some platforms return `reasoning_content`, `usage`, or `billing`-related fields, which can be viewed directly in the logs.
  - **Graphical Configuration Tool**: Features a `Flet (0.28.3)` based modern adaptive GUI tool (`matchbox_cfg_gui.pyw`, supports double-click auto-launch without a console window on Windows) that operates **directly on the database** without any frontend dependency. It supports adding, editing, and soft-disabling platforms/models, drag-and-drop reordering, encrypting and storing API keys, probing/testing models, resetting the DB from local YAML, or exporting the DB back to `matchbox_cfg.yaml` + `matchbox_key.yaml`.
- **Database Persistence**: Defaults to SQLite for storing user configurations, platforms, and models. Production environments can switch to PostgreSQL via `AGENT_MATCHBOX_DATABASE_URL`.
- **Automatic Configuration Correction**: When a user's configuration becomes invalid (e.g., a model or platform is deleted), the system automatically rolls back to the first available default platform to guarantee service uptime.

## 📂 File Structure

```
.
├── __init__.py            # Package entry point, exporting initialize_matchbox / matchbox / create_matchbox
├── database.py            # Standalone database Engine factory
├── integrations.py        # Host integration callback contracts (usage slots/caller identity/usage context/key rotation/prompt-cache reader)
├── manager.py             # Core AIManager class (composing all Mixins; seed-sync algorithm lives in seed_sync.py)
├── seed_sync.py           # YAML seed→DB sync algorithm (spec expansion / 4-stage matching / write-back, unit-testable)
├── config.py              # Configuration loading and global constants (USE_SYS_LLM_CONFIG, LLM_AUTO_KEY, etc.; lazy DEFAULT_PLATFORM_CONFIGS)
├── models.py              # SQLAlchemy database models
├── security.py            # Security and encryption (SecurityManager)
├── admin.py               # Platform and model management Mixin (AdminMixin)
├── builder.py             # LLM instance construction Mixin (LLMBuilderMixin)
├── user_services.py       # User services Mixin (UserServicesMixin)
├── quota_services.py      # Quota config/statistics/interception Mixin (QuotaServicesMixin)
├── usage_services.py      # Usage statistics Mixin (UsageServicesMixin)
├── usage_writer.py        # Background usage writer (single-thread async persistence, interrupted streams still billed)
├── redeem_code_services.py # Redeem code management Mixin (RedeemCodeServicesMixin)
├── retrying.py            # tenacity retry policy for probe/test requests (ops-side only, never wraps billed calls)
├── tracked_model.py       # LLMClient/LLMUsage/UsageTrackingCallback
├── estimate_tokens.py     # Token usage estimation utility
├── hf_mirror.py           # Hugging Face mirror discovery, region detection, and reachability probing
├── utils.py               # Utility functions (probe_platform_models, parse_extra_body, etc.)
├── tests/                 # Standalone offline test suite (pytest tests -q)
│   ├── conftest.py        # In-memory DB + isolated home fixtures
│   ├── test_startup_contracts.py  # Light init / lazy loading / independence (subprocess probes)
│   ├── test_openai_compat_gateway.py  # Handshake headers / tool schemas / usage callbacks
│   ├── test_platform_identity.py  # Platform identity and duplicate URLs
│   ├── test_sort_order.py # Sort persistence and fallback
│   ├── test_redeem_and_credit_grants.py  # Redeem codes / grants / concurrent settlement
│   ├── test_seed_sync.py  # Seed matching algorithm
│   ├── test_retrying.py   # Probe retry policy
│   └── ...                # paths/database/modalities/hf_mirror
├── matchbox_cfg.yaml       # System platform structure configuration (used for initialization/export only, runtime uses database)
├── matchbox_key.yaml       # System platform API keys (should be gitignored, do not commit)
├── matchbox_cfg_gui.pyw    # Graphical configuration management tool (double-clickable entry point, actual code is in gui/ subdirectory)
├── gui/                   # GUI modules (split from matchbox_cfg_gui)
│   ├── __init__.py
│   ├── main_window.py     # Main window LLMConfigGUI class (platform config, user overview, model management)
│   ├── platform_panel.py  # Platform management Mixin
│   ├── model_panel.py     # Model management Mixin
│   ├── dialogs.py         # Dialog Mixin (add/edit models, system usage slots, user quotas)
│   ├── key_manager.py     # Key management Mixin
│   ├── probe.py           # Model probing Mixin
│   ├── dpi.py             # High-DPI scaling and window size strategy
│   └── theme.py           # GUI theme, color palette, and cross-platform font compatibility
└── README.md              # This document
```

- **`manager.py`**: Contains the `AIManager` class, which uses the Mixin pattern to combine `AdminMixin`, `LLMBuilderMixin`, `UserServicesMixin`, `QuotaServicesMixin`, `UsageServicesMixin`, and other modules. This is the main entry point to interact with the library.
- **`quota_services.py`**: Quota services module, handling configuration, periodic usage statistics, total usage statistics, and pre-invocation interception for the two billing scopes (`sys_paid`/`self_paid`).
- **`usage_services.py`**: Usage statistics module. In addition to single-user aggregation, it also provides user-wide usage overview aggregation for the GUI.
- **`matchbox_cfg.yaml`**: **Initialization configuration file**. Used to define initial "System Platforms". On first startup, the manager syncs the platforms in this file to the database. Subsequent startups will only incrementally add new platforms without overwriting existing configurations. **The database, not this file, is the authority source at runtime.**
- **`matchbox_cfg_gui.pyw`**: GUI entry file, with actual logic split into the `gui/` subdirectory. **Operates directly on the database**, supporting adding/deleting/modifying platforms and models (deleting is soft-disabling), encrypting/storing API keys, probing/testing models, user usage overview, double-clicking users to view details, and resetting the DB from local YAML or exporting the DB to `matchbox_cfg.yaml` + `matchbox_key.yaml`.

## 🛠️ First-Time Configuration Flow (Newbies Must Read)

**Note:** The default configuration file (`matchbox_cfg.yaml`) is suitable for quick migration or sharing model configurations. `platform_key` is the stable identity for configuration and key mapping, while runtime database references use `platform_id`; `base_url` is only an upstream endpoint and may be shared by multiple platforms. API keys are stored separately in `matchbox_key.yaml` and indexed by `platform_key`. Legacy URL-indexed keys are read only when that URL identifies exactly one platform. This file is ignored by `.gitignore` and must not be committed to version control.

When using the manager for the first time, you need to run the configuration tool and fill in your own API keys.

1. **Set the Master Encryption Key (LLM_KEY)**:
    - The system uses `LLM_KEY` to encrypt your API keys and all user-defined API keys. You can set this as an environment variable or run the GUI tool directly; it will prompt you for input and automatically save it.
    - **Do not panic if you see a warning saying "Historical keys exist and cannot be decrypted" on your first deployment.** This usually means that `matchbox_key.yaml` contains encrypted keys generated by the repository author or another environment, which are naturally invalid on your machine. You only need to set your own `LLM_KEY` and follow the prompt to clean up these unrecoverable keys. **Cleaning up keys will not delete platform or model structures, it only clears those invalid hosted keys.**

2. **Launch the Configuration Tool**:
    - Install the dependencies listed below, then run `python matchbox_cfg_gui.pyw` or double-click `matchbox_cfg_gui.pyw` on Windows.
    - You will see pre-configured platforms (e.g., DeepSeek, OpenRouter), but their keys are currently unusable.

3. **Replace and Activate Platforms**:
    - Select the platform you intend to use, fill in your actual **API Key** on the right, and click save.
    - It is recommended to delete platforms you do not need.

4. **Verify Models**:
    - Click **"Probe Available Models"**. If configured correctly, the right-hand panel will list all models supported by the platform.
    - Select a model on the left, click **"Test Model"**, and seeing "Test Successful" indicates that the configuration is complete.

5. **Check Usage Binds**:
    - Click **"System Usage Management"**.
    - Ensure that `main` (primary model), `fast` (fast model), and `reason` (reasoning model) are bound to valid models that you just configured keys for.

6. **Final Testing**:
    - Select a model on the left and click **"Test Model"**.
    - If the "Test Successful" log appears, your configuration is successfully finalized!

## ⚙️ Core Concepts & Runtime Modes

Understanding the runtime modes of this project is crucial, as they directly affect functionality behavior and secondary development.

### Standard Design (Recommended)

To balance stability, maintainability, and extensibility, the Matchbox gateway adopts a **two-phase initialization + dual-channel** standard design:

1. **Management Channel (Default)**:
  - **Phase 1 (Light Startup)**: At startup, explicitly invoke `initialize_matchbox(ensure_defaults=True)`. This only completes database engine initialization and default configuration synchronization. This phase **does not** load heavy runtime dependencies like `langchain_openai`, nor does it touch YAML/decryption (`import config` has no filesystem side effects; configs are lazily loaded on first access).
  - **Phase 2 (Asynchronous Warmup)**: Immediately follow up by calling `warmup_matchbox_runtime(blocking=False)` to preload runtime modules like `ChatUniversal` and `LLMClient` in a background thread. This executes in parallel with application startup to avoid blocking the first incoming request.
  - During request-time, get the manager via `matchbox()` and then call `get_user_llm(...)` / `get_user_embedding(...)`.
  - Automatically handles user model selection, key priority, quota interception, and usage accounting.
  - **Usage writes are asynchronous**: `on_llm_end` only computes in memory and submits to the `usage_writer` background thread; SQLite writes + credit settlement never block the model response. Interrupted/failed streams are still accounted.
2. **Lightweight Channel (Bypass)**:
  - Quickly create clients via `create_quick_llm(...)` / `create_quick_embedding(...)`.
  - Bypasses the database, making it ideal for scripts, toolchains, temporary tasks, and external integrations.
3. **Lifecycle Constraints**:
  - Initialize + warmup at startup, and call `reset_matchbo()` during shutdown to avoid side effects of import-time initialization.
4. **Runtime Directory Governance**:
  - DB/.env/YAML/state default to the Matchbox component root. A host can call `set_default_mgr_home(path)` before initialization to choose its own default, while the higher-priority `AGENT_MATCHBOX_HOME` environment variable remains available for deployment overrides.

### Recommended Flow (Developer Practice)

```python
from agen_matchbox import initialize_matchbox, warmup_matchbox_runtime, matchbox

# 1) Light Startup: Database engine + default configuration sync (millisecond-level)
initialize_matchbox(ensure_defaults=True)

# 2) Async Warmup: Preload heavy runtime dependencies like langchain_openai in a background thread
#    blocking=False (default) returns immediately to run in parallel with app startup;
#    modules are ready when the first request arrives.
warmup_matchbox_runtime(blocking=False)

# 3) Get client on-demand in business requests (required=True by default)
client = matchbox().get_user_llm(user_id="user_123", usage_key="main", agent_name="your_agent")

# 4) Use it like a normal LLM client
result = client.invoke("Please provide a cyberpunk worldview seed")

# 5) Streaming works out-of-the-box, with usage archived automatically
for chunk in client.stream("Continue expanding into a three-act structure"):
    print(chunk.content, end="")
```

### 1. System User (`SYSTEM_USER_ID = "-1"`)

This is a special virtual user ID. When calling `matchbox().get_user_llm()` (without `user_id`) or `matchbox().get_user_llm(user_id="-1")` in code, the manager enters **System Mode**.

- **Purpose**: Provides a unified LLM instance for application backends, global services, or local development and debugging.
- **Key Source**: In system mode, priority is given to the user's custom-configured key for the system platform. If not configured, it falls back to the system backup key based on the `LLM_AUTO_KEY` rule. In the current implementation, the system backup key is parsed from `DEFAULT_PLATFORM_CONFIGS` (provided by `matchbox_cfg.yaml` for structure, `matchbox_key.yaml` for API keys, and environment variables placeholders).

### 2. Global Mode Switches

There are two important global configuration switches in [`config.py`](config.py):

- **`USE_SYS_LLM_CONFIG = True` (Multi-User Fixed-Platform Mode)**:
  - All users can only see and use the system platforms defined in `matchbox_cfg.yaml`.
  - Users **cannot** create, modify, or delete their own platforms and models.
  - Users **can** provide their own API keys for these system platforms, which are securely stored in the database's `llm_sys_platform_keys` table and bound to their user ID.
  - This mode balances unified model management and cost distribution.

- **`USE_SYS_LLM_CONFIG = False` (Multi-User Custom-Platform Mode)**:
  - This is the **default** mode.
  - Users have maximum permissions in this mode.
  - **System platforms remain visible and usable**, but users gain "write permissions."
  - Users can create their private platforms and models by calling `AIManager` methods like `add_platform`, `add_model`, etc.
  - Best suited for scenarios requiring high customization.

### 3. Automatic Key Fallback & Priority (`LLM_AUTO_KEY`)

When retrieving API keys, the system adheres to the **"User Private > System Backup"** priority rule:

1. **User Private Key**: Used with priority if the user has configured an override key for a system platform (stored in the database).
2. **System Backup Key**: Checked only if the user hasn't set their own key and `LLM_AUTO_KEY` is checked.

- **`LLM_AUTO_KEY = True`**:
  - **⚠️ Note this important option!**
    - If a regular user calls a **system platform** but hasn't provided their own API key, and this option is `True`, the manager will fall back to using the system backup key (parsed from `DEFAULT_PLATFORM_CONFIGS`, derived from `matchbox_cfg.yaml` + `matchbox_key.yaml`) as the backend API key.
  - **Pros**: Delivers a seamless trial experience for guest or unconfigured users.
  - **Risk**: **Could lead to unexpected server costs!** If you do not wish to provide free services to users, make sure to set this to `False`.

- **`LLM_AUTO_KEY = False`**:
  - The safer option.
  - If a user has not provided their own API key for a system platform, calling the LLM will directly raise a `ValueError`, prompting the user to configure their API key.

**Recommended Settings**:

- If you want the **server to provide unified services and cover the cost** (i.e., "I lock down all models and provide API services to all users"), set `LLM_AUTO_KEY = True` and configure API keys for default system platforms via the GUI (paid by the administrator).
- If you want **users to use their own keys and pay** (i.e., "I lock down all models but users pay for their own APIs"), set `LLM_AUTO_KEY = False` and require users to fill in their API keys in the frontend or settings.

### 4. Quota Metric & Interception

In the current version, all calls are split into two billing/quota scopes based on the **actual source of the key used**:

- **`sys_paid`**: System platforms using hosted keys.
- **`self_paid`**: Users using their own keys.
  - Includes user override keys on system platforms.
  - Includes user-defined keys on private custom platforms.

This split serves a clear purpose:

- When site owners want to cap the costs they incur, they only limit `sys_paid`.
- Even if a user exhausts their `sys_paid` quota, they can switch to their own key and continue utilizing the `self_paid` channel.
- Ensures that hosted budget exhaustion does not accidentally lock out users' self-paid paths.

Quota configurations are stored in the `user_quota_policies` table, supporting independent configurations for both scopes:

- **Every N-Hour Window Quota**:
  - `*_window_hours`
  - `*_window_token_limit`
  - `*_window_request_limit`
- **Total Quota**:
  - `*_total_token_limit`
  - `*_total_request_limit`

All fields are nullable; null indicates that no limit is enforced for that specific metric.

The runtime interception logic is as follows:

1. `matchbox().get_user_llm(...)` / `matchbox().get_spec_sys_llm(...)` first resolves the actual key used for the call.
2. The system maps the call to either `sys_paid` or `self_paid`.
3. Evaluates limits only for the matching scope.
4. If exceeded, raises a `QuotaExceededError`.

### 5. Multi-Purpose Model Slots

- **Default Slots**: The system automatically initializes `main` (primary), `fast` (fast), and `reason` (reasoning) slots for each user, binding them to default platform/model selections upon registration.
- **Custom Slots**: New usage slots can be added with custom keys and initial models via `POST /api/ai/user-selection/usage` or `AIManager.create_user_usage_slot(...)`.
- **Query & Update**:
  - `GET /api/ai/user-selection?usage_key=fast` queries a specific slot; the response also includes a `usage_selections` list to show current bindings for all slots.
  - `POST /api/ai/user-selection` accepts a `usage_key` parameter to update the model bound to that specific slot.
- **Runtime Resolution**: `matchbox().get_user_llm(user_id, usage_key="reason")` directly returns the model instance bound to that slot; defaults to `main` if the parameter is omitted.

## 🚀 Quick Start

### 1. Install Dependencies

This repository currently provides Matchbox as a source component and does not include a `pyproject.toml`. Put the `agen_matchbox` directory on the host application's Python import path and declare versions in the host dependency file. For direct development, install the required dependencies explicitly:

```bash
pip install langchain-core langchain-openai sqlalchemy tiktoken cryptography pyyaml requests python-dotenv tenacity
# Optional: PostgreSQL / GUI / host-side Alembic (the gateway itself has no migration dependency)
pip install "psycopg[binary]" "flet>=0.28.3,<0.29.0" alembic
```

### 1.1 Standalone Tests

The gateway ships its own offline test suite (no host dependency, no real LLM calls, no external network):

```bash
cd server/llm/agen_matchbox && pytest tests -q
# or from the host server directory
pytest llm/agen_matchbox/tests -q
```

### 2. Configure Platforms and Models via GUI

**Recommended**: Launch directly by double-clicking `matchbox_cfg_gui.pyw` on Windows, or via command line to interact with the database without manually editing YAML.

```bash
python matchbox_cfg_gui.pyw
```

> **Description**: `matchbox_cfg.yaml` only syncs pre-configured platforms to the database on **first startup** (incremental sync, no overwrite).
> At runtime, model/platform choices are retrieved from the database. System backup keys are still resolved from `DEFAULT_PLATFORM_CONFIGS` (coming from `matchbox_key.yaml` / environment variables placeholders).
> If the distributed key file contains `ENC:` keys encrypted in another environment that cannot be decrypted in your current deployment, the system will **skip importing these invalid keys** but still sync the platform and model structures. The admin can then enter their own keys in the local GUI.

**GUI Setup Steps**:

1. Select the platform you want to use from the left panel (e.g., DeepSeek, OpenRouter).
2. Fill in your actual **API Key** on the right and click "Save Key" (encrypted and stored in the DB).
3. Click "Probe Models" to fetch all models supported by the platform.
4. Select target models from the probed list, and click "Add Selected" to add them to the platform.
5. For platforms you do not need, click "Disable Platform" to soft-disable and hide them from default listings without deleting database records.

#### 2.1. Model Modalities and Image Protocols

Model types are not persisted through `is_embedding` or an expanding set of category flags. The database, YAML, and API use two factual fields only:

- `input_modalities`: currently `text` and `image`.
- `output_modalities`: currently `text`, `image`, and `embedding`; `embedding` is exclusive with other outputs.

Common combinations:

| Use case | `input_modalities` | `output_modalities` |
|---|---|---|
| Text model | `[text]` | `[text]` |
| Vision text model | `[text, image]` | `[text]` |
| Text-to-image | `[text]` | `[image]` |
| Image-to-image / editing | `[text, image]` | `[image]` |
| Unified text and image output | `[text, image]` | `[text, image]` |
| Embedding model | `[text]` | `[embedding]` |

The Web manager and CustomTkinter GUI expose only three checkboxes: Vision, Image Generation, and Embedding. Text input is implicit. Selecting Embedding clears the other choices. Compact model tags use `T` for text output, `I` for image output, `V` for image input, and `E` for embedding output; the Web UI explains each tag through tooltips.

```yaml
models:
  Editable Image Model:
    model_name: provider-image-model
    input_modalities: [text, image]
    output_modalities: [image]
    image_generation_adapter: openai_images
    extra_body:
      quality: high
```

`image_generation_adapter` is the sole source of truth for the gateway's image protocol selection. The user selects it explicitly; it is never inferred from the `base_url` domain, model name, or `extra_body`, and is never written into or forwarded to the upstream service. `extra_body` remains reserved for provider-specific parameters. The adapter strips only the gateway's internal control keys and forwards other parameters on a best-effort basis. Image models without an explicit selection use the `openai_images` default; no legacy nested adapter is read.

| Value | Upstream protocol | Configured `model_name` | Reference-image transport |
|---|---|---|---|
| `openai_images` | `/images/generations` and `/images/edits` | GPT Image or a compatible image model | multipart `image[]` |
| `openai_responses_image` | `/responses` with the `image_generation` tool | A mainline text model that supports the tool | `input_image` data URLs in the one request; no persistent Files API upload |
| `openai_chat_image` | A compatible `/chat/completions` gateway | The image-model name exposed by that gateway | data URLs in multimodal message content; parses Markdown/data-URI image results |
| `gemini_generate_content` | Gemini `models/*:generateContent` | A Gemini / Nano Banana model | `inline_data` |
| `gemini_interactions` | Gemini Interactions | A Gemini / Nano Banana model | image parts in `input` |
| `xai_images` | xAI `/images/generations` and `/images/edits` | A Grok Image model | JSON data URLs, with at most three references for editing |

`openai_images` remains the default because it is the closest thing to a common minimum image API. `openai_responses_image` is not a direct GPT Image model protocol: the configured mainline model calls the image-generation tool, so mainline-model token usage also applies. The gateway never uploads host-project references to a provider Files API automatically. Project images remain persisted locally and are attached only to the selected generation request.

#### 2.2. Manually Editing YAML (Bootstrap/Distribution Only)

Directly edit the [`matchbox_cfg.yaml`](matchbox_cfg.yaml:1) file. On next startup, new platforms will be incrementally synced to the database.

- **`api_key`**: Can use placeholders (e.g., `{OPENAI_API_KEY}`), which will be resolved from environment variables at startup.
  Alternatively, leave it blank and fill in the encrypted key via the GUI later.

### 3. Set Environment Variables

Before running your main application, make sure you have set the environment variables referenced in `matchbox_key.yaml`.

For example, if your configuration is `api_key: '{GEMINIX_API_KEY}'`, you need to:

- **Windows**:

  ```powershell
  $Env:GEMINIX_API_KEY="your_real_api_key"
  ```

  (Set in System Properties to make it persistent)
- **Linux/macOS**:

  ```bash
  export GEMINIX_API_KEY="your_real_api_key"
  ```

  (Add to `.bashrc` or `.zshrc` to make it persistent)

**Tip**: The GUI tool's "Save API Key" **encrypts and writes the key directly to the database** (not to the YAML). If you wish to dump the current database configuration to local files, use the "Export DB to YAML" option in the toolbar. This will generate both `matchbox_cfg.yaml` (structures) and `matchbox_key.yaml` (keys).

### 4. Use in Code

The model manager has been refactored into a component-based structure, integrating management, building, and statistics capabilities via the Mixin pattern.

The recommended approach is to import `initialize_matchbox`, `warmup_matchbox_runtime`, and `matchbox`, and explicitly perform the two-phase initialization during application startup (typically in lifespan or startup hooks).

```python
from agen_matchbox import initialize_matchbox, warmup_matchbox_runtime, matchbox

# Recommended during application startup (process-level, two-phase)
# Phase 1: Light Startup — Database engine, schemas, default config sync (no langchain_openai loaded)
initialize_matchbox(ensure_defaults=True)

# Phase 2: Asynchronous Warmup — preload ChatUniversal / LLMClient runtime modules in background
# blocking=False (default) returns immediately without blocking app startup
warmup_matchbox_runtime(blocking=False)

# --- Scenario 1: Retrieve LLM for a specific user ---
# The manager automatically resolves model selections and API keys for the user
try:
    user_llm = matchbox().get_user_llm(user_id="user_123")
    fast_llm = matchbox().get_user_llm(user_id="user_123", usage_key="fast")
    # response = user_llm.invoke("Hello")
    # for chunk in user_llm.stream("Hello"):
    #     print(chunk.content, end="")
except ValueError as e:
    # Occurs if the API key is unconfigured, etc.
    print(f"Failed to retrieve LLM: {e}")


# --- Scenario 2: Use in backend services or no-user scenarios ---
# Uses the special SYSTEM_USER_ID, with keys retrieved from matchbox_key.yaml
try:
    system_llm = matchbox().get_user_llm() # user_id=None defaults to system user
    # response = system_llm.invoke("Write a Python Hello World")
except ValueError as e:
    print(f"Failed to retrieve system LLM: {e}")


# --- Scenario 3: Lightweight entry, specify system model directly (suitable for scripts) ---
# The display name must match the configuration exactly; configuration is immutable during calls
try:
    qwen_llm = matchbox().get_spec_sys_llm(
        platform_name="阿里云百炼",
        model_display_name="通义flash"
    )
    # response = qwen_llm.invoke("Introduce Tongyi Qwen")
except ValueError as e:
    print(f"Failed to retrieve specified LLM: {e}")
```

## 📦 Dual Data Source Architecture: Database vs YAML

### Core Concepts

System platform configurations support two types of data sources, suited for different use cases:

| Data Source | Storage Location | Effect Mechanism | Use Case |
|-------------|------------------|------------------|----------|
| **Database** (Recommended) | `llm_config.db` | Instant effect upon modification | Production environments, web admin, dynamic changes |
| **YAML** | `matchbox_cfg.yaml` (Structures)<br>`matchbox_key.yaml` (Keys) | Requires service restart | Initial deployment, sharing config, version control |

### Sync Strategies (Three Trigger Events)

1. **First Initialization**:
    - **Trigger**: Database is empty.
    - **Behavior**: YAML configuration is fully loaded into the database.
    - **Purpose**: Provides an out-of-the-box configuration for new deployments.

2. **Incremental Sync**:
    - **Trigger**: Subsequent startups (Default).
    - **Behavior**: Adds only new platforms and models from YAML, **without overwriting or deleting** existing database records.
    - **Purpose**: Distributes new models via YAML while **protecting** custom administrative changes made directly in the database.

3. **Force Reset**:
    - **Trigger**: GUI "Reset from Config File" button or API invocation.
    - **Behavior**: **Resets** the database system platform config based on YAML: updates platforms/models, soft-disables platforms missing in YAML, and retains user API keys.
    - **Purpose**: Restores standard configurations if the database state becomes disorganized.

### GUI Tool

The GUI configuration tool (`matchbox_cfg_gui.pyw`) **directly operates on the database**. Changes take effect immediately without requiring service restarts.

- **📦 Database (Sole Mode)**: All platform/model modifications write directly to the database; API keys are stored encrypted.
- **📥 Reset DB from YAML**: Rebuilds database platforms based on local `matchbox_cfg.yaml` + `matchbox_key.yaml`. Platforms missing in YAML are soft-disabled, while user-override keys are preserved.
- **📤 Export DB to YAML**: Dumps database configurations to local files (`matchbox_cfg.yaml` and `matchbox_key.yaml`) for version control or sharing.

### Frontend Admin API

Administrators can manage database system platforms directly via REST APIs:

```
GET    /api/ai/admin/sys-platforms          # Get all system platforms
POST   /api/ai/admin/sys-platform           # Add system platform
PUT    /api/ai/admin/sys-platform           # Update system platform
DELETE /api/ai/admin/sys-platform           # Soft-disable system platform
POST   /api/ai/admin/sys-platform/api-key   # Update platform API key
POST   /api/ai/admin/reload-from-yaml       # Force reload database from YAML
```

## ⚠️ Important Notices & FAQ

1. **Database is the Runtime Source of Truth**:
    - During service execution, all model configurations are read from the **database**, not the YAML files.
    - Changes made via the web frontend or GUI database mode take effect instantly.
    - YAML is only used for initialization during startup and will not overwrite database edits.

2. **API Key Security**:
    - **⚠️ CRITICAL WARNING: NEVER** commit raw API keys or files like `matchbox_key.yaml` or `.env` to public code repositories (such as GitHub).
    - **Always use `.gitignore`**: Ensure that your project's root `.gitignore` file includes `*.env` and `matchbox_key.yaml` to prevent accidental exposure.
    - **Best Practice**: Always prefer environment variables. The GUI tool can help you achieve this effortlessly.
    - **💡 Why Key Encryption at Rest is Necessary? (Design Rationale)**:
      Some developers might ask: "If my keys are already ignored by `.gitignore` in the local environment, why do I still need encryption and decryption? Isn't this redundant?"
      This design is primarily aimed at **preventing accidental leaks and significantly increasing the cost of cracking**. In reality, 99.9% of API key leaks are caused by automated bots using regular expressions to search for plaintext keys.
      While this does not prevent a motivated human or AI from decrypting the keys if both the master key (`LLM_KEY`) and the encrypted key file are leaked, it does successfully evade the vast majority of scenarios where a credential is accidentally committed, exposed in logs, or leaked as raw files, preventing automated bots from scanning the plaintext key.

3. **Database Files**:
    - By default, `llm_config.db` is generated in the Matchbox component root. A host can call `set_default_mgr_home(path)` before initialization to choose its default directory, or use the higher-priority `AGENT_MATCHBOX_HOME` environment variable as an explicit deployment override. This SQLite file contains all user data and synced system platform data. Protect it carefully.
    - To use PostgreSQL, set `AGENT_MATCHBOX_DATABASE_URL=postgresql+psycopg://user:password@host:5432/dbname`. This variable belongs to the Matchbox component itself, making it easy to reuse across different projects.

4. **Model Probing Failure?**:
    - **Check `base_url`**: Ensure the URL is correct, and verify if it needs a `/v1` suffix.
    - **Check API Key**: Confirm the key is correct, active, and has sufficient credits.
    - **Check Network**: Ensure the server has outbound internet access to reach the `base_url`.

5. **Usage of `extra_body`**:
    - `extra_body` provides a powerful mechanism to pass provider-specific parameters.
    - In the GUI, it must be valid JSON. In YAML, it is represented as a dictionary.
    - These parameters are merged automatically into `ChatOpenAI`'s `extra_body` or `model_kwargs`.

## 📊 Usage Tracking Capabilities

`matchbox().get_user_llm()` returns an `LLMClient`:

- Can be directly used as an LLM instance (supporting `invoke`, `stream`, etc.).
- Query usage statistics via the `.usage` sub-object (e.g., `client.usage.get_usage_last_24h()`).
- Token consumption and requests are automatically logged to the database during each call.

### Automatic Logging

```python
from agen_matchbox import matchbox

# Get client (can be used directly as an LLM client)
client = matchbox().get_user_llm(user_id="user_123", agent_name="agent_muse")

# Use normally; usage is auto-recorded to the database
result = client.invoke(messages)

# Streaming outputs are also logged automatically upon completion
for chunk in client.stream(messages):
    print(chunk.content, end="")

# If the stream is interrupted (client disconnect / cancellation),
# the system estimates completion_tokens based on generated chunks and logs it (success=0)

# Query usage via the .usage sub-object
usage_24h = client.usage.get_usage_last_24h()
print(usage_24h)

# Query system-paid usage within the last 24 hours
sys_paid_24h = client.usage.get_sys_paid_usage_last_24h()

# Query user self-paid usage within the last 24 hours
self_paid_24h = client.usage.get_self_paid_usage_last_24h()
```

### Querying Usage (via usage sub-object)

Use `client.usage` to query usage stats under the current user and model:

```python
# client = matchbox().get_user_llm(user_id="user_123")

# Get usage for the last 24 hours
usage_24h = client.usage.get_usage_last_24h()
print(f"Last 24h: {usage_24h['total_tokens']} tokens, {usage_24h['requests']} requests")

# Get usage for the last 7 days
usage_week = client.usage.get_usage_last_week()

# Get usage for the last 30 days
usage_month = client.usage.get_usage_last_month()

# Get all-time usage
usage_total = client.usage.get_usage_total()

# Get all-time system-paid usage
sys_paid_total = client.usage.get_sys_paid_usage_total()

# Get all-time user self-paid usage
self_paid_total = client.usage.get_self_paid_usage_total()

# Get usage within a custom datetime range
from datetime import datetime
usage = client.usage.get_usage_by_range(
    start_time=datetime(2026, 1, 1),
    end_time=datetime(2026, 1, 31)
)
```

The returned dictionary format:

```python
{
    "total_tokens": 12345,     # Total tokens consumed
    "prompt_tokens": 8000,     # Prompt tokens
    "completion_tokens": 4345, # Completion tokens
    "requests": 50,            # Total requests
    "errors": 2,               # Failed requests
}
```

### Manager-Level Usage Querying

The manager returned by `matchbox()` provides broader usage querying interfaces:

```python
from datetime import timedelta
from agen_matchbox import matchbox

mgr = matchbox()

# Get total usage for a user in the last 24 hours
usage = mgr.get_user_usage_last_24h(user_id="user_123")

# Get system-paid usage for a user in the last 24 hours
usage = mgr.get_user_sys_paid_usage_last_24h(user_id="user_123")

# Get self-paid usage for a user in the last 24 hours
usage = mgr.get_user_self_paid_usage_last_24h(user_id="user_123")

# Get total usage for a user in the last 7 days
usage = mgr.get_user_usage_last_week(user_id="user_123")

# Get all-time system-paid usage for a user
usage = mgr.get_user_sys_paid_usage_total(user_id="user_123")

# Get all-time self-paid usage for a user
usage = mgr.get_user_self_paid_usage_total(user_id="user_123")

# Query usage by quota scope (sys_paid / self_paid / total)
usage = mgr.get_user_usage_by_scope(
    user_id="user_123",
    quota_scope="sys_paid",
)

# Get user model usage statistics (grouped by model)
stats = mgr.get_user_usage_stats(
    user_id="user_123",
    since=timedelta(days=7)  # Optional, limits timeframe
)
# Returns: [{"model_name": "gpt-4", "tokens": 5000, ...}, ...]

# Get usage grouped by agent name
by_agent = mgr.get_usage_by_agent(
    user_id="user_123",
    since=timedelta(hours=24)
)
# Returns: [{"agent_name": "agent_muse", "tokens": 1234, "requests": 10}, ...]

# Get usage timeline data (useful for charts)
timeline = mgr.get_usage_timeline(
    user_id="user_123",
    granularity="hour",  # Or "day"
    since=timedelta(hours=24)
)
# Returns: [{"time": "2026-01-01 10:00", "tokens": 500, "requests": 5}, ...]

# Purge old logs (recommended to run periodically)
deleted = mgr.purge_old_usage_logs(older_than=timedelta(days=90))
print(f"Purged {deleted} obsolete log entries")
```

### Data Storage

Usage records are persisted in the `usage_log_entries` table. Each LLM call creates a record containing:

- `user_id` and `model_id`
- `quota_scope` (`sys_paid` or `self_paid`)
- `prompt_tokens`, `completion_tokens`, `total_tokens`
- `success` (1=success, 0=failure)
- `agent_name` (name of calling Agent)
- `created_at` (timestamp for time-range queries)

> **Note**: The legacy `ModelUsageStats` table is deprecated and no longer updated. Use the new time-series log table for aggregation queries.

### Quota Settings & Status Queries

In addition to usage logs, the manager provides user quota policy administration and status summaries:

```python
from agen_matchbox import matchbox

# Get current user's quota policy
policy = matchbox().get_user_quota_policy(user_id="user_123")

# Save/update quota policy
policy = matchbox().save_user_quota_policy(
    user_id="user_123",
    sys_paid_window_hours=24,
    sys_paid_window_token_limit=100000,
    sys_paid_window_request_limit=200,
    sys_paid_total_token_limit=None,
    sys_paid_total_request_limit=None,
)

# Fetch policy + usage status + remaining balance summary
status = matchbox().get_user_quota_status(user_id="user_123")
```

Among these:

- `sys_paid_*` limits system-hosted API key usage.
- `self_paid_*` limits user self-paid API key usage.
- A value of `None` indicates that the specific limit is disabled.

## 🧪 Inference Content & Billing Fields in Platform Tests

Clicking "Test Model" in the GUI executes `test_platform_chat(..., return_json=True)` internally and logs the response JSON (truncated if too long).

- **Reasoning Content Display**: If the platform returns `reasoning_content` (or compatible fields) in the response, it is visible directly in the raw JSON log.
- **Billing/Usage Fields**: If the platform returns `usage`, `token_usage`, or `billing` fields, they are displayed verbatim in the logged response.
- **Provider Divergence is Normal**: Different platforms have distinct schemas for reasoning content and billing fields; missing fields do not indicate failure.
- **Purpose**: This acts as a "raw response visualization" tool for debugging and platform alignment. Consolidated billing calculations must be implemented at the business logic layer based on platform unit prices.

### Token Estimation & Billing

To ensure cross-platform compatibility and statistical consistency, the manager adopts a hybrid "**prefer API usage, fall back to local estimation**" strategy:

1. **Prioritize API Usage**: If the response contains standard usage fields (`prompt_tokens`/`completion_tokens` or `input_tokens`/`output_tokens`), these values are preferred. Local estimation never runs in this path (zero extra cost).
2. **Fallback to Local Estimation**: If the platform returns no usage details (common in some streaming or non-standard APIs), `estimate_tokens` is used to estimate input and output texts. By default only local tiktoken encoders are used; no tokenizer downloads are triggered unless the host explicitly calls `warmup_tokenizers()`.
3. **Reasoning Content Inclusion**: Accumulated `reasoning_content` (including third-party extensions) is included in completion estimation when no raw usage is returned.
4. **Recording Counts & Success State**: Every call is stored with a `success=1/0` flag and token fields. Interrupted streams log the estimated output produced so far and are flagged as failed. Persistence runs on the `usage_writer` background thread and never blocks the model response.

### Interception Order & Database Migrations

- **Pre-call interception order**: quota (`quota_services.enforce_user_quota`) → credit (`credit_services.enforce_user_credit`), executed sequentially in the same transaction by `builder.get_user_llm`.
- **Upstream probe retries**: `probe_platform_models` / `test_platform_chat` retry network-layer errors (connection failures/timeouts) up to 3 times with exponential backoff; business errors like 401/400 are never retried. Disable via `AGENT_MATCHBOX_PROBE_RETRY_ENABLED=0`. Billed model calls are never auto-retried (retrying a billed request is a host orchestration decision).
- **Database migrations**: the gateway itself only provides `ensure_schema()` (`Base.metadata.create_all`, no Alembic dependency, keeping zero external deps). Hosts referencing this gateway **should introduce migrations on the host side** (e.g. a host Alembic branch whose `load_metadata` reuses `agen_matchbox.models.Base.metadata`); do not nest another migration system inside the gateway.

> Note: The built-in counters focus on tokens/requests/errors and do not output financial costs. If financial billing is required, convert tokens to currency at the application layer.

## 🔄 Migrating to Other Frameworks

While this component is tightly integrated with `LangChain`, its core layers (DB management, security encryption, usage accounting) are designed to be completely independent. To migrate `matchbox` to other mainstream Agent frameworks, follow these steps:

### 1. Migrate to AutoGen (Microsoft)

AutoGen v0.4+ (python-v0.7+) introduces the `model_client` pattern, removing the mandatory dependency on `llm_config` dicts.

- **Migration Core**: Add a helper method in `LLMBuilderMixin` that returns a `model_client`.
- **Sample Code**:

  ```python
  from autogen_ext.models.openai import OpenAIChatCompletionClient

  def get_autogen_client(self, user_id, usage_key="main"):
      # 1. Retrieve the underlying base_url and api_key
      resolved = self._resolve_user_choice(...) 
      
      # 2. Return an AutoGen-compatible client
      return OpenAIChatCompletionClient(
          model=resolved["model"].model_name,
          api_key=resolved["api_key"],
          base_url=resolved["base_url"]
      )
  ```

### 2. Migrate to CrewAI

CrewAI maintains high compatibility with LangChain objects, but it also provides a native `LLM` class for standard OpenAI-compatible endpoints.

- **Quick Integration**: The `LLMClient` returned by `get_user_llm()` proxies common LangChain methods and can be passed directly to `Agent(llm=client)` or used via `client.invoke()`/`client.stream()`.
- **Native Integration**: To completely strip LangChain, configure `CrewAI`'s `LLM` class as follows:

  ```python
  from crewai import LLM

  # Fetch configuration from matchbox and instantiate
  crew_llm = LLM(
      model=f"openai/{model_name}", # CrewAI uses provider/model format
      base_url=base_url,
      api_key=api_key
  )
  ```

### 3. Complete De-LangChainization

To build a universal backend completely independent of LangChain, we recommend using **LiteLLM** as middleware:

1. **Update Dependencies**: Replace `langchain-openai` with `litellm` in your requirements.
2. **Refactor Adapter Layer**: Modify `LLMClient/UsageTrackingCallback` in [`tracked_model.py`](tracked_model.py) to directly wrap `litellm.completion` while retaining `.usage` query capabilities.
3. **Reuse Core Modules**: Keep [`manager.py`](manager.py) and [`usage_services.py`](usage_services.py) intact, as the database operations and usage tracking logic are 100% framework-agnostic.

With this dual-layer architecture (management layer + adapter layer), you can easily integrate `matchbox` into any emerging AI ecosystem.

## 📄 License

Matchbox Agent Gateway is separately licensed under Apache License 2.0 according to the `LICENSE` file in this directory and may be reused as an independent component.
