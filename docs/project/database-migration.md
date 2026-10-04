# 数据库自动迁移——完整指南

本文档介绍表结构升级、模型变更工作流和已有实例的数据迁移。首次部署请先阅读 [数据库部署与配置](database-deployment.md)。

---

## 1. 自动迁移特性

1. **多数据库分支**：`users.db` 与 `llm_config.db` 采用独立 `version_locations`，互不干扰
2. **启动自动升级**：启动时使用 Alembic API 直接升级
3. **临时库生成迁移**：`gen_migration.py` 先用已提交迁移链构造临时 head DB，再与当前 Models 对比，避免开发机真实 DB 污染迁移结果
4. **自动跳过**：启动时先读取 `alembic_version` 与脚本 head，已是最新且无缺失结构时直接跳过
5. **最早阶段执行**：迁移在 `lifespan` 最前面完成，避免业务初始化占用 SQLite 锁
6. **智能重命名检测**：当开发者在代码中重命名数据库字段时，迁移工具会自动识别并询问确认，避免了传统工具"先删除再新增"导致的数据丢失风险
7. **危险操作拦截**：任何涉及 `DROP COLUMN`（删除列）或 `DROP TABLE`（删除表）的修改，在生成迁移脚本阶段都会被强制拦截并要求开发者交互确认
8. **孤儿版本自愈**：当底层迁移链被上游仓库重置打断时，启动期自愈机制会自动补缺失表/列并 stamp 到 head；默认保留额外表/列，不做破坏性删除
9. **head 漂移保护**：如果版本号已是 head 但 DB 缺少当前模型字段，启动期默认报错，防止悄悄修库吞掉应提交给下游的 migration

---

## 2. 开发者工作流（改表 → 迁移 → 审核 → 发布）

1. **修改模型**（`server/core/models.py` 或 `server/llm/agen_matchbox/models.py`）
2. **生成迁移**：

    ```bash
    cd server
    python gen_migration.py
    ```

3. **处理冲突**：如有重命名/删除等危险操作，按提示确认或取消；不要手写迁移脚本
4. **提交迁移**：将生成的迁移文件提交到仓库
5. **用户拉取代码**：无需手动迁移，启动服务会自动执行升级

> 💡 **开发者注意**：
>
> - 🚫警告：禁止手写迁移文件和修改现有迁移文件，这会造成冲突
> - 修改 `core/models.py` (Users DB) 后，运行 `python gen_migration.py users "说明"`
> - 修改 `llm/agen_matchbox/models.py` (LLM DB) 后，运行 `python gen_migration.py llm "说明"`
> - 如果不指定数据库名，默认会对所有数据库生成迁移：`python gen_migration.py "说明"`
> - 生成脚本不会读取真实运行库作为 autogenerate 基准；如果临时库升级后仍与 Models 不一致，脚本会失败并指出缺失/多余结构。

### 2.1 救急开关

- `SPARKARC_AUTO_MIGRATE_REPAIR_HEAD_DRIFT=1`：当 `alembic_version` 已是 head 但 DB 缺表/缺列时，允许启动期按 Models 补缺失对象。默认关闭，避免开发机悄悄修库后吞掉 migration。
- `SPARKARC_AUTO_MIGRATE_ALLOW_DROPS=1`：孤儿版本自愈时允许删除 Models 未定义的额外列/表。默认关闭；除非已备份数据库且确认这些结构无用，否则不要开启。
- `SPARKARC_ALEMBIC_USERS_DB` / `SPARKARC_ALEMBIC_LLM_DB`：覆盖 Alembic 目标 DB 路径。未设置时，LLM DB 会跟随 `AGENT_MATCHBOX_HOME`，保证迁移目标和运行时 manager 使用同一个文件。
- `SPARKARC_USERS_DATABASE_URL`：覆盖 SparkArc 用户主库连接串。未设置时使用 `server/data/users.db`。
- `AGENT_MATCHBOX_DATABASE_URL`：覆盖 Agent Matchbox 组件数据库连接串。未设置时使用组件目录下的 `llm_config.db`。

共用部署可填写 PostgreSQL 地址、端口、账号和原密码，由程序为两个分支派生连接；需要特殊参数时可使用高级 `SPARKARC_POSTGRES_URL`。上述单库变量优先于共用配置，地址与高级 URL 均为空时使用 SQLite。首次部署配置见 [数据库部署与配置](database-deployment.md)。

---

## 3. 将自动迁移基础设施接入你的应用

如果你想将这套自动数据库迁移逻辑（自动升级、多库支持、重命名检测）复用到其他 FastAPI 项目，请务必改清楚以下"必改项"，做到开箱即用：

### 3.1 复制核心文件

- `server/alembic/` (目录)：包含环境配置 `env.py` 和脚本模板
- `server/alembic.ini`：配置文件
- `server/gen_migration.py`：生成迁移的 CLI 工具
- `server/core/auto_migrate.py`：负责运行时自动升级的逻辑

### 3.2 必改项清单（迁移到新项目一定要改）

- **数据库路径**：
    - `server/core/migration_specs.py` 中的 `DB_SPECS` / `get_db_path`
- **Metadata 入口**：
    - `server/core/migration_specs.py` 中 `load_metadata`
- **多库分支命名**：
    - `server/alembic.ini` 中的 `[users]` / `[llm]` 段落名称
    - `server/core/migration_specs.py` 中的 `DB_SPECS`
- **自定义类型渲染**：
    - 如果你有自定义类型（如 `SqliteJSONB`），必须在 `env.py` 里加 `render_item` 规则
    - `render_as_batch_mode` 是为 SQLite 设计，Postgres/MySQL 应关闭
- **业务启动入口**：
    - `app.py` 中 `lifespan` 里调用 `run_auto_migrations()` 的位置要靠前

### 3.3 配置多数据库（可选）

- 修改 `server/core/migration_specs.py` 中的 `DB_SPECS`、`get_db_path` 和 `load_metadata`
- 在 `server/alembic.ini` 中补充对应 section 与 `version_locations`

### 3.4 接入应用生命周期

在你的 `app.py` 或 `main.py` 的 lifespan 中调用 `run_auto_migrations`：

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from core.auto_migrate import run_auto_migrations

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. 启动时自动迁移
    try:
        run_auto_migrations()
    except Exception as e:
        print(f"Migration failed: {e}")
        raise e
    
    yield
    
app = FastAPI(lifespan=lifespan)
```

---

## 4. 清理迁移历史（⚠️ 高风险操作）

⚠️ 警告：如果你需要用 Git 在多个地方同步仓库，那么**禁止执行清理历史**。这会导致拉取者出现数据库版本错误。除非你确定你的操作只涉及最简单的增删。

⚠️ 自愈机制只是兜底，处理掉简单的增删。如果清理掉了涉及重命名和修改字段类型或约束，而下游拉取者又没有来得及同步之前的迁移历史，会导致拉取者出现错误！

⚠️ 只有你在本地独自开发的时候才能使用这个脚本！

`gen_migration.py` 使用临时数据库作为生成迁移的基准，不需要通过清理历史来“修正 autogenerate”。清历史只适合私有开发阶段压缩历史；公开分支应只追加迁移。

```bash
cd server
python clear_migration.py --yes
```

该脚本会：

1. 先升级到最新 head
2. 备份/删除旧迁移
3. 使用空数据库生成新的基线迁移
4. 将真实数据库 stamp 到新 head
5. 再用临时库隔离模式验证新迁移链能从零升级到当前 Models

---

## 5. SQLite / PostgreSQL 双模式

SparkArc 默认保持 SQLite 零部署；生产部署可把平台主库切到 PostgreSQL。

### 5.1 共用连接与单库覆盖

连接同一 PostgreSQL 服务时，只需配置：

```dotenv
SPARKARC_POSTGRES_HOST=数据库地址
SPARKARC_POSTGRES_PORT=5432
SPARKARC_POSTGRES_USER=sparkarc
SPARKARC_POSTGRES_PASSWORD='填写数据库账号的原密码'
SPARKARC_DATABASE_PREFIX=sparkarc
```

程序使用 `sparkarc_users` / `sparkarc_llm` 两个数据库，缺库时尝试创建，已有库直接连接。缺库初始化需要维护库连接权限和 `CREATEDB`；预先建库时无需建库权限。

不同服务、账号或已有数据库名称可使用高级单库覆盖：

```bash
# SparkArc 用户、聊天、分享、反馈等平台主库
SPARKARC_USERS_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/sparkarc_users

# Agent Matchbox 独立组件库：平台、模型、密钥、用量日志、兑换码等
AGENT_MATCHBOX_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/sparkarc_llm
```

两条单库 URL 优先于高级共用 URL `SPARKARC_POSTGRES_URL`，高级共用 URL 优先于分项配置；地址和高级 URL 均为空时使用 SQLite。分项密码无需 URL 编码。独立 Agent Matchbox 使用组件级 `AGENT_MATCHBOX_DATABASE_URL`，共用连接由 SparkArc 宿主适配层传入组件。

**必须使用两个独立数据库**：两套 Alembic 分支都维护 `alembic_version`，不能将两条连接串指向同一个数据库的同一个 schema。连接串只改变目标，启动自动升级只负责表结构，不会自动搬运 SQLite 数据。

首次部署的 SQLite、Compose PostgreSQL、外部 PostgreSQL 与宿主连接配置，统一见 [数据库部署与配置](database-deployment.md)。

### 5.2 SQLite 转 PostgreSQL

以下为 Linux shell 示例，须使用与来源数据库相同且已升级到 head 的应用代码/镜像。计划短暂停写，保留原始数据库、主密钥和全部业务卷。

```bash
dc() { docker compose -f docker-compose.yml -f docker-compose.postgres.yml "$@"; }

# 配置 POSTGRES_PASSWORD 后先启动数据库，现有应用保持运行
dc up -d --wait postgres
docker compose stop sparkarc

# 停写后归档全部业务目录；包含 SQLite WAL、.env、密钥、项目与分享
mkdir -p backups/pre-postgres
docker compose cp sparkarc:/app/server/data backups/pre-postgres/data
docker compose cp sparkarc:/app/server/llm/agen_matchbox backups/pre-postgres/agen_matchbox
docker compose cp sparkarc:/app/server/_userdata backups/pre-postgres/_userdata
docker compose cp sparkarc:/app/server/shares_data backups/pre-postgres/shares_data

# 利用 SQLite backup API 生成独立副本，退出 WAL 模式以支持只读校验
dc run --rm -T --no-deps --entrypoint python sparkarc - <<'PY'
import sqlite3
from pathlib import Path
backup = Path('/app/server/data/pg-migration-backup')
backup.mkdir(exist_ok=False)
for name, path in [('users', '/app/server/data/users.db'), ('llm', '/app/server/llm/agen_matchbox/llm_config.db')]:
    with sqlite3.connect(path) as source, sqlite3.connect(backup / f'{name}.db') as target:
        source.backup(target)
        target.execute('PRAGMA journal_mode=DELETE')
PY

# 目标必须为空；脚本复用 Alembic 建表、模型解码和外键顺序
dc run --rm --no-deps --entrypoint python sparkarc migrate_sqlite_to_postgres.py users data/pg-migration-backup/users.db
dc run --rm --no-deps --entrypoint python sparkarc migrate_sqlite_to_postgres.py llm data/pg-migration-backup/llm.db
dc run --rm --no-deps --entrypoint python sparkarc migrate_sqlite_to_postgres.py users data/pg-migration-backup/users.db --verify-only
dc run --rm --no-deps --entrypoint python sparkarc migrate_sqlite_to_postgres.py llm data/pg-migration-backup/llm.db --verify-only

# 两库全部校验成功后切换
dc up -d sparkarc
curl -fsS http://localhost:7788/health
```

迁移命令按表比较记录数及全部字段 SHA-256，并修复自增序列；JSON 按业务值比较，密钥密文原样保留。非空目标、来源版本不符、额外表/列、约束冲突或内容差异都会拒绝迁移，每库导入在一个事务内完成。两库并非跨库原子事务：如果第二库失败，保持应用停写，处理失败库后重试，已成功的库只执行 `--verify-only`。

切换前失败时可启动原 SQLite 配置恢复服务。**切换后 PostgreSQL 已有新写入时，不能直接回退到旧 SQLite**，需要先停写并迁回增量数据。创作文件、项目 `stories.db`、向量索引和分享快照保持原卷，不需要搬进 PostgreSQL。

CI 管理的实例应在切换前配置数据库连接及应用网络，配置入口见 [数据库部署与配置](database-deployment.md#5-ci-部署)。

### 5.3 项目级存储边界

每个项目的 `stories.db` 继续使用 SQLite。它是用户私有、轻量、几乎无并发的项目快照/导出格式，仍服务于版本、分享、试玩和下载链路，不纳入 PostgreSQL 主线。

项目级语义索引使用每项目本地 LanceDB 目录 `.vector_index_lancedb`，不使用 PostgreSQL/pgvector，也不纳入主库迁移范围。

### 5.4 跨数据库类型映射

#### `SqliteJSONB` 自定义类型

`core/models.py` 中的 `SqliteJSONB` 是一个 dialect-aware `TypeDecorator`：

- SQLite：使用 BLOB 保存 UTF-8 编码的 JSON 文本。
- PostgreSQL：映射为原生 `JSONB`。

SQLite 存储方式为：

```
Python dict → json.dumps → UTF-8 bytes → BLOB 列
```

这不是 SQLite 3.45+ 的原生 JSONB 格式，只是在 BLOB 里存了 UTF-8 编码的 JSON 文本。

#### 受影响范围

| 数据库 | 表 | JSON 字段 |
|---|---|---|
| users.db | `chat_messages` | `content`, `metadata_json` |

`stories.db` 中也存在 JSON 字段，但它继续作为项目级 SQLite 文件保留，不属于 PostgreSQL 双模式主线。其余平台表（`users`、`user_sessions`、`shares`、`project_versions`、`system_platform_quotas`、`user_feedbacks` 等）均为纯标量字段，无需任何改动。

#### 额外注意

- SQLite 的 `BOOLEAN` 实际存储为 `INTEGER`，PG 有原生 `BOOLEAN`，SQLAlchemy 会自动处理差异
- PostgreSQL 模式下启动期直接运行 Alembic upgrade，不执行 SQLite 专属的文件级自愈逻辑。
