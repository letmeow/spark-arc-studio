# 数据库部署与配置

首次部署先选择数据库方式。默认选择是 SQLite；如果明确要用 PostgreSQL，再在“宿主机 PostgreSQL”和“Compose 内置 PostgreSQL”之间选择。SparkArc 不扫描宿主机的数据库服务，也不自动选择或切换数据库。

需要配置环境变量时，先在项目根目录复制模板，再编辑 `.env`：

```bash
# Linux / macOS
cp .env.example .env
```

Windows PowerShell 使用 `Copy-Item .env.example .env`。复制步骤仅用于首次创建；已有 `.env` 时直接编辑，避免覆盖站点配置。模板中的值默认为 SQLite，按下述部署方式填写相应变量。根目录 `.env` 由 Compose 读取；`server/llm/agen_matchbox/.env` 是组件的运行时密钥配置，不是部署模板。

| 方式 | 适用场景 | 配置入口 |
|---|---|---|
| SQLite | 本地使用、轻量自部署 | 不设置数据库连接串 |
| Compose PostgreSQL | 希望随项目部署和管理数据库 | `.env` 的 `POSTGRES_PASSWORD` + PostgreSQL 覆盖文件 |
| 外部 PostgreSQL | 已有宿主机、独立服务器或托管数据库 | `.env` 的数据库地址、账号和密码 |

## 先做选择

| 你的情况 | 应选择 | 启动方式 |
|---|---|---|
| 只是本地开发、试用，或不想额外管理数据库 | **默认 SQLite** | `docker compose up -d --build` |
| 服务器已经运行 PostgreSQL，希望复用现有备份、监控和账号 | **外部 PostgreSQL（宿主机或云数据库）** | 填写 `SPARKARC_POSTGRES_HOST` 等分项，并使用默认 Compose 文件 |
| 没有可复用的 PostgreSQL，希望数据库跟项目一起部署 | **Compose 内置 PostgreSQL** | 只填写 `POSTGRES_PASSWORD`，使用 PostgreSQL Compose 覆盖文件 |

三种方式不会自动互相切换。默认 Compose 文件只启动 `sparkarc` 和 SQLite，但始终提供 `host.docker.internal` 到宿主机网关的映射；这只是网络地址映射，不会启动或连接任何数据库。`docker-compose.postgres.yml` 是可选覆盖文件，加入它才会启动 `postgres` 容器。两种 PostgreSQL 方式不要同时使用。

宿主机 PostgreSQL 和云数据库在 SparkArc 中属于同一种“外部 PostgreSQL”，使用同一套配置和同一条启动命令。只有地址写法不同：宿主机通常写 `host.docker.internal`，云数据库写服务商提供的域名；云数据库不需要任何额外 Compose 覆盖文件。

选择 PostgreSQL 后，应用会派生并使用两个业务数据库：`sparkarc_users` 和 `sparkarc_llm`。数据库账号和密码的处理方式如下：

- 宿主机／外部 PostgreSQL：使用你填写的已有账号和密码；账号需要能访问维护库，首次缺库时还需要 `CREATEDB`，或者由管理员提前创建两个业务库。
- Compose 内置 PostgreSQL：PostgreSQL 官方镜像首次初始化时自动创建账号 `sparkarc`；密码就是 `.env` 中的 `POSTGRES_PASSWORD`。SparkArc 不随机生成密码，也不会替用户创建第二个账号，只负责创建两个业务数据库和表。

内置数据库的账号、密码和数据都保存在 `postgres_data` 命名卷中。首次初始化后修改 `.env` 中的 `POSTGRES_PASSWORD` 不会修改已有数据库密码；如需改密码，应在 PostgreSQL 中执行密码变更并同步应用配置。

PostgreSQL 使用两个独立数据库：`sparkarc_users` 保存用户、会话、聊天、版本关联等平台数据；`sparkarc_llm` 保存模型配置、密钥、额度与用量等组件数据。默认只需填写一份服务连接，由程序派生两个库名。两套 Alembic 都使用 `alembic_version`，不能共用一个数据库的同一 schema。

## 1. 默认 SQLite

在项目根目录执行：

```bash
docker compose up -d --build
```

保留 `SPARKARC_POSTGRES_HOST` 为空，也不设置高级 URL，应用使用 `server/data/users.db` 和 `server/llm/agen_matchbox/llm_config.db`。访问 `http://localhost:7788`。

默认 SQLite 不要求创建 `.env`；复制模板后保留地址与高级 URL 为空也可直接启动。

## 2. 随 Compose 部署 PostgreSQL

在根目录 `.env` 添加：

```dotenv
POSTGRES_PASSWORD=替换为随机十六进制密码
```

可用 `openssl rand -hex 32` 生成密码，也可填写自选密码。建议用单引号包住密码，避免 Compose 把 `$` 当作变量，例如 `POSTGRES_PASSWORD='my@pass$word'`。不要将 `.env` 提交到仓库。

```bash
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --build
```

默认的 `docker compose up -d --build` 仍然使用 SQLite；只有显式加入 `docker-compose.postgres.yml` 才会启动内置 PostgreSQL。

Compose 创建 PostgreSQL 服务并配置共用连接，等待数据库健康后由应用创建两个业务数据库并执行表结构升级。PostgreSQL 数据存放在命名卷中，5432 不发布到宿主机，应用访问地址仍为 `http://localhost:7788`。

启动、更新及停止都使用相同的两个 `-f` 参数。修改 `.env` 不会修改已经初始化的数据库角色密码；更新服务不要使用 `down -v`，该命令会删除命名数据卷。

## 3. 使用外部 PostgreSQL

建议为 SparkArc 创建专用角色。需要由程序首次建库时，数据库管理员可在 `psql` 中执行：

```sql
CREATE ROLE sparkarc LOGIN CREATEDB;
\password sparkarc
```

`\password` 会交互式设置密码。程序只在目标数据库不存在时建库；已有数据库直接连接，不重复创建、不清空数据。首次建库后可执行 `ALTER ROLE sparkarc NOCREATEDB;` 收回建库权限，保留业务库所有权用于表结构升级。无需授予超级用户权限。

根目录 `.env` 配置：

```dotenv
SPARKARC_POSTGRES_HOST=数据库地址
SPARKARC_POSTGRES_PORT=5432
SPARKARC_POSTGRES_USER=sparkarc
SPARKARC_POSTGRES_PASSWORD='填写数据库账号的原密码'
SPARKARC_DATABASE_PREFIX=sparkarc
```

通常只需要填写地址和密码：端口默认 `5432`，账号默认 `sparkarc`。该账号需由数据库管理员创建；使用已有专用账号时修改 `SPARKARC_POSTGRES_USER`。直接填写原密码即可，密码里的 `@`、`:`、`/` 等字符由程序处理。建议用单引号包住密码，避免 `.env` 中的 `$` 被 Compose 当作变量；密码本身含单引号时写成 `\'`，例如 `SPARKARC_POSTGRES_PASSWORD='it\'s@secret'`。

程序使用固定名称 `<前缀>_users` 和 `<前缀>_llm`，无需填写库名。前缀默认 `sparkarc`，只允许小写字母、数字和下划线，以字母开头，最长 57 字符。修改前缀会选择另一组数据库，不会自动迁移已有数据。

地址应是应用容器能够访问的数据库地址。需要 TLS 参数或自定义维护库的托管数据库，可使用下面的高级 URL 配置，并按服务提供商要求设置。然后启动：

```bash
docker compose up -d --build
```

此方式不会创建 PostgreSQL 容器。已指定 PostgreSQL 的连接失败会导致启动失败，不会回退到 SQLite 或创建替代数据库。

托管服务不允许建库或希望应用使用最小权限时，管理员可以预先创建两个业务库并授权给应用账号。目标库已存在时无需 `CREATEDB`，也无需连接维护库。缺库且没有建库权限时会明确报错。

高级配置 `SPARKARC_POSTGRES_URL` 优先于地址、端口、账号、密码分项，可附加 TLS 参数，例如 `?sslmode=require`。URL 末尾的库名用于缺库初始化时连接维护库，通常为 `postgres`，也可填写服务商允许连接的维护数据库。只有手写 URL 时才需要“URL 编码”：将密码里的特殊字符转换为连接串格式，例如 `@` 写成 `%40`，否则它可能被误认为密码与地址的分隔符。使用分项配置无需自行转换。

两库需要不同地址、账号或已有库名时，可分别填写 `SPARKARC_USERS_DATABASE_URL` 和 `AGENT_MATCHBOX_DATABASE_URL`，对应单库配置优先于高级共用 URL 和分项配置。不设置任何共用配置而仅使用单库 URL 时，需预先建库。Matchbox 独立使用时继续使用自己的 `AGENT_MATCHBOX_DATABASE_URL`；共用配置的适配由 SparkArc 宿主层完成。

### 连接宿主 Linux 的 PostgreSQL

将 `SPARKARC_POSTGRES_HOST` 设为 `host.docker.internal`，使用默认 Compose 文件：

```bash
docker compose up -d --build
```

默认 Compose 已提供宿主地址映射，不创建数据库服务。容器中的 `localhost` 指容器自身，不能用它连接宿主 PostgreSQL。

宿主 PostgreSQL 必须监听 Docker 可达的宿主地址；仅监听 `127.0.0.1` 时无法从应用容器连接。`pg_hba.conf` 应只放行实际应用网络、专用角色和这两个数据库，使用 `scram-sha-256`；防火墙限制访问来源，无需向公网开放 5432。

已有统一备份和监控的 PostgreSQL 时可复用它；需要独立版本或资源隔离时可使用 Compose 专用服务。

## 4. 启动与验证

首次启动会自动创建表并升级到对应 Alembic head，不需要手工建表。先检查启动日志，再检查健康状态：

```bash
docker compose logs --tail=100 sparkarc
curl -fsS http://localhost:7788/health
```

正常响应为 `sparkarc-ok`。涉及覆盖文件时，查看日志也应使用部署时相同的 `-f` 参数。数据库地址、认证或权限错误应修正配置后重新创建应用容器；仅修改根目录 `.env` 不会更新已经运行的容器环境。

源码开发时，在启动 `server/app.py` 的解释器进程中设置同样的数据库环境变量。根目录 `.env` 是 Compose 的配置入口，源码启动不会自动加载它；地址应使用开发机能够访问的数据库地址。

## 5. CI 部署

Gitea 部署工作流通过仓库 Secrets 接收相同的地址、端口、账号和密码分项，以及可选的库名前缀、高级 URL 和 `SPARKARC_DATABASE_NETWORK`。首次部署 PostgreSQL 时先准备服务、账号和网络，再配置 Secrets；未设置地址或高级 URL 时使用 SQLite。

`SPARKARC_DATABASE_NETWORK` 指宿主 Docker 中已存在的应用网络，默认 `bridge`。数据库为容器时，可将应用与数据库加入同一用户定义网络，并在连接串中使用数据库服务的网络名称。CI 与默认 Compose 都提供 `host.docker.internal` 宿主地址映射；连接宿主 PostgreSQL 时可填写该名称或容器可达的宿主 IP。CI 部署会等待应用健康检查通过。

重建时，显式配置优先；未指定共用地址或 URL 时，从现有应用容器继承数据库配置。明确指定共用地址或 URL 时，不继承容器中的单库覆盖；使用分项配置时请将密码等必要字段一起保存在 Secrets 中。网络未配置时从现有容器继承。保存完整配置，使应用容器不存在时也能按相同配置部署。数据库生命周期独立于应用镜像更新。详细流程见 [CI/CD 部署指南](cicd-deployment.md)。

## 6. 数据与备份边界

连接串只选择目标数据库，表结构自动升级不会搬运现有 SQLite 数据。已有实例切换数据库前，按 [SQLite 转 PostgreSQL 操作指南](database-migration.md#52-sqlite-转-postgresql)停写、备份、导入和校验。

项目正文、资源、分享快照、项目 `stories.db` 和 LanceDB 索引保留在文件目录中，不随主库切换搬进 PostgreSQL。完整备份需同时包含两个主库、主密钥配置和业务文件目录。

## 7. 主密钥与其它部署配置

`LLM_KEY` 是 API Key 的加密主密钥。推荐通过管理员后台设置，它会持久化到 `server/llm/agen_matchbox/.env`，换密时同步迁移数据库与密钥配置中的密文。

根目录 `.env` 的 `LLM_KEY` 或 CI Secret 只作为首次初始化输入：组件尚未保存主密钥时写入组件配置，并保留文件内其它设置；组件已有密钥时使用该持久化值，不被部署输入覆盖。因此管理员后台换密后，重新部署仍使用换密结果。根目录 `.env` 不由后台回写，更换它不能代替主密钥轮换；更换后可清空首次初始化输入，保留组件持久化配置。

`.env.example` 覆盖 Compose 对外开放的部署变量，包括数据库、首次主密钥、注册验证和 Exa/Tavily 联网搜索。联网搜索的系统配置也可在管理员后台保存；根目录填写的非空搜索或注册验证变量属于静态部署覆盖，重建后仍以它们为准。组件内部运行参数、调试变量和模型性能调优不属于这个部署模板，按对应组件文档配置。
