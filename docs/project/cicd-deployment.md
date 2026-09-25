# CI/CD 自动化部署——完整指南

本文档包含 CI/CD 自动化部署的深度技术细节。README 中仅保留概述。

---

## 1. 支持的 Git 平台

| 平台 | 配置文件 | Runner | 触发条件 |
| :--- | :--- | :--- | :--- |
| **Gitea** | `.gitea/workflows/deploy.yml` | 自建 `act_runner`（Docker 模式） | push 到 `main` 分支 |
| **GitLab** | 需自行移植（参考 `.gitea/workflows/deploy.yml`） | 自建 GitLab Runner（Docker 模式） | push 到任意分支 |
| **GitHub** | `.github/workflows/pr-checks.yml` / `release-desktop.yml` / `release-android-apk.yml` / `release-all.yml` / `sync-gitee-release.yml` | GitHub 托管 Runner | PR / push 到 `main`（检查）；手动触发（发布） |

> ⚠️ **GitLab CI 说明**：仓库当前**未附带** GitLab CI 配置文件。如需在 GitLab 上运行，请参考 `.gitea/workflows/deploy.yml` 的五阶段逻辑自行移植，并在项目 **Settings → CI/CD → General pipelines → CI/CD configuration file** 中指向你创建的配置文件路径。

> 💡 **关于 GitHub Actions**：本项目已在 GitHub 上配置了完整的工作流——`pr-checks.yml` 负责 PR 质量门禁（前端构建/类型检查/单元测试 + 后端回归测试 + Docker 构建），`release-desktop.yml` 和 `release-android-apk.yml` 负责桌面端与 Android 端的发布构建。Gitea Actions 的语法设计与 GitHub Actions 高度相似，但**并非直接兼容**，移植时需注意以下差异：
>
> - **Token 变量名**：Gitea 使用 `${{ gitea.token }}`，GitHub 使用 `${{ github.token }}`。本项目的工作流已同时读取两者并以非空者优先，因此迁移到 GitHub 后无需修改 Token 部分。
> - **托管 Runner**：GitHub 提供开箱即用的托管 Runner（`ubuntu-latest` 直接可用）；Gitea 需要在服务器上**自行部署 `act_runner`** 并以 Docker 模式运行。
> - **代码检出 Action**：标准的 `actions/checkout` 在 Gitea 上存在兼容问题。本项目绕过了这一点——检出步骤直接使用裸 `git` 命令实现，同时兼容两个平台。

---

## 2. 流水线阶段

三个阶段顺序执行，任意阶段失败则终止：

```text
📥 检出代码  →  🔨 构建镜像  →  🧪 测试（预留）  →  🚀 部署  →  🧹 清理
```

1. **构建**：执行 `docker build`，利用 BuildKit 的 `--mount=type=cache` 缓存 npm/pip 包，非首次构建可大幅提速
2. **测试**：Gitea / GitLab 当前为预留阶段；GitHub Actions 已集成前端类型检查 + 单元测试 + 后端 `pytest` 回归测试 + Docker 构建验证
3. **部署**：
    - 自动创建五个持久化 Docker Volume（`sparkarc_data`、`sparkarc_userdata`、`sparkarc_shares`、`sparkarc_llm_config`、`sparkarc_runtime_cache`），已存在则跳过
    - 若在 CI Secret 中配置了 `LLM_KEY`，自动写入容器的 `.env` 文件；未配置则启动后可通过前端设置
    - 若在 CI Secret / Variable 中配置了注册验证相关变量，会通过容器环境变量传入运行时
    - 若在管理员后台保存注册验证配置，会写入持久化数据卷中的运行时 `.env`，不会随容器重建丢失
    - 本地嵌入相关的 GGUF 模型、llama.cpp 预编译包、Hugging Face / transformers tokenizer 缓存会写入 `sparkarc_runtime_cache`，Docker 重建后继续复用
    - 原子替换：先删除旧容器，再以相同 Volume 启动新容器，数据零丢失
    - 启动阶段自动执行"受管文件同步"：将镜像中的 Git 受管文件覆盖回挂载目录，并清理已下线的旧受管文件；`*.db`、`.env` 等运行时数据不覆盖
4. **清理**：自动执行 `docker image prune` 清理构建过程中产生的悬空镜像

---

## 3. 配置 Gitea Runner（快速上手）

在你的服务器上，以 Docker 模式运行 `act_runner`，并将其注册到 Gitea 实例：

```bash
# 1. 获取注册 Token（Gitea 仓库 → 设置 → Actions → Runner）
# 2. 注册并启动 Runner
docker run -d \
  --name gitea-runner \
  --restart unless-stopped \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e GITEA_INSTANCE_URL=http://your-gitea-instance \
  -e GITEA_RUNNER_REGISTRATION_TOKEN=your_token \
  gitea/act_runner:latest
```

Runner 启动后，向 `main` 分支推送代码即可自动触发完整的构建和部署流程。

---

## 4. 配置 CI Secret

在你的 Git 平台仓库的 **Settings → Secrets** 中添加以下变量（可选）：

| 变量名 | 说明 |
| :--- | :--- |
| `LLM_KEY` | 大模型主密钥。配置后自动写入容器；未配置则首次启动后通过前端设置 |
| `SPARKARC_REGISTRATION_VERIFICATION_ENABLED` | 可选。设为 `1` 开启注册人机验证；缺少 Turnstile site key 或 secret key 时仍会自动关闭 |
| `SPARKARC_REGISTRATION_VERIFICATION_PROVIDER` | 可选。当前支持 `turnstile`；未配置时默认按 `turnstile` 处理 |
| `SPARKARC_TURNSTILE_SITE_KEY` | 可选。Cloudflare Turnstile 站点密钥，可公开给前端 |
| `SPARKARC_TURNSTILE_SECRET_KEY` | 可选。Cloudflare Turnstile 私钥，只传给后端容器，严禁提交到仓库 |

### 注册验证变量说明

注册验证默认关闭。只有同时满足以下条件时才会启用：

1. `SPARKARC_REGISTRATION_VERIFICATION_ENABLED=1`
2. `SPARKARC_REGISTRATION_VERIFICATION_PROVIDER=turnstile`（或留空使用默认值）
3. `SPARKARC_TURNSTILE_SITE_KEY` 已配置
4. `SPARKARC_TURNSTILE_SECRET_KEY` 已配置

如果缺少 site key 或 secret key，后端会把注册验证视为未启用，不会阻塞首次部署时的管理员注册。

Turnstile 的安全边界是：前端只接收 site key 并提交 token；后端在 `/api/register` 创建用户前调用 Cloudflare `siteverify` 校验 token。后续如果迁移到 Google、腾讯云等验证平台，应扩展 `server/core/verification.py` 的 provider，而不是在注册路由或前端页面中复制一套验证逻辑。
