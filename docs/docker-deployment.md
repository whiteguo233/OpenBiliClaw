# Docker 部署指南

[← 返回 README](../README.md)

## 前置条件

- [Docker](https://docs.docker.com/get-docker/) 20.10+
- [Docker Compose](https://docs.docker.com/compose/install/) V2（`docker compose` 命令）
- 至少一个 LLM API Key（OpenAI / Claude / Gemini / DeepSeek / OpenRouter），或本地运行 Ollama

## 快速开始

```bash
# 1. 克隆项目
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw

# 2. 启动容器
docker compose up -d --build

# 3. 一键初始化（交互式引导配置 + B 站认证 + 画像生成 + 首轮发现）
docker exec -it openbiliclaw-backend openbiliclaw init
```

`init` 命令会引导你完成所有必要的配置，包括设置 LLM API Key 和 B 站认证。

## 配置

容器首次启动时会基于 `config.example.toml` 自动生成配置模板到 Docker volume 中。你可以通过以下方式编辑：

```bash
# 方式一：通过 init 命令交互式配置（推荐）
docker exec -it openbiliclaw-backend openbiliclaw init

# 方式二：直接编辑容器内的配置文件
docker exec -it openbiliclaw-backend vi /app/runtime/config.toml
```

### 环境变量

可通过环境变量覆盖部分配置，在 `docker-compose.yml` 的 `environment` 中设置或启动时传入：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENBILICLAW_PROXY_HOST` | `host.docker.internal` | 代理主机地址 |
| `OPENBILICLAW_PROXY_PORT` | `7897` | 代理端口 |
| `OPENBILICLAW_PROXY_TIMEOUT` | `1.0` | 代理探测超时（秒） |

### LLM 配置

支持多种 LLM 提供商，在配置文件中设置 `default_provider` 和对应的 `api_key`：

- **openai** — GPT-4o（默认）
- **claude** — Claude Sonnet
- **gemini** — Gemini 2.5 Flash
- **deepseek** — DeepSeek Chat
- **ollama** — 本地模型（需确保容器能访问宿主机的 Ollama 服务）
- **openrouter** — 通过 OpenRouter 访问多种模型

还可为不同模块（soul / discovery / recommendation / evaluation）指定不同的模型，例如用便宜的模型做内容发现评估，用高质量模型做灵魂画像生成。

## 日常命令

所有 CLI 命令通过 `docker exec` 在容器内执行：

```bash
# B 站认证登录
docker exec -it openbiliclaw-backend openbiliclaw auth login

# 手动触发内容发现
docker exec -it openbiliclaw-backend openbiliclaw discover

# 查看推荐
docker exec -it openbiliclaw-backend openbiliclaw recommend

# 查看用户画像
docker exec -it openbiliclaw-backend openbiliclaw profile
```

### 生命周期管理

```bash
# 启动（需要在项目目录）
docker compose up -d

# 停止
docker compose down

# 重新构建（代码更新后）
docker compose up -d --build

# 查看容器日志
docker compose logs -f openbiliclaw-backend
```

> **注意**：Docker 镜像在构建时打包代码，`git pull` 后必须加 `--build` 重新构建，否则容器内运行的仍是旧版代码。
> 如果发现画像内容缺失或功能不符合预期，首先尝试 `docker compose up -d --build` 重建镜像。

## 默认行为

- 后端对外监听 **`8420`** 端口
- 配置、数据、日志存放在 Docker named volumes 中：
  - `openbiliclaw_config` → `/app/runtime`（配置文件）
  - `openbiliclaw_data` → `/app/runtime/data`（SQLite 数据库等）
  - `openbiliclaw_logs` → `/app/runtime/logs`（日志文件）
- 健康检查地址：`http://127.0.0.1:8420/api/health`
- 容器设置为 `restart: unless-stopped`，异常退出后自动重启

## 数据与存储

Docker 部署默认与宿主机项目目录**完全隔离**，所有数据保存在 Docker named volumes 中。

### 查看日志

```bash
# 查看容器标准输出
docker compose logs -f

# 查看应用日志文件
docker exec -it openbiliclaw-backend cat /app/runtime/logs/openbiliclaw.log
```

### 备份数据

```bash
# 备份数据库
docker cp openbiliclaw-backend:/app/runtime/data ./backup-data

# 备份配置
docker cp openbiliclaw-backend:/app/runtime/config.toml ./config-backup.toml
```

### 彻底重置

删除所有 volumes 并重建，将清除所有数据（配置、画像、历史记录）：

```bash
docker compose down -v
docker compose up -d --build
```

## 网络与代理

### Clash 代理

容器启动时自动探测宿主机 Clash 代理（默认 `host.docker.internal:7897`）。

自定义代理端口：

```bash
export OPENBILICLAW_PROXY_PORT=7890
docker compose up -d --build
```

自定义代理主机：

```bash
export OPENBILICLAW_PROXY_HOST=192.168.1.100
docker compose up -d --build
```

### Ollama 本地模型

如使用宿主机上的 Ollama，需确保 Ollama 监听 `0.0.0.0`，并在配置中设置：

```toml
[llm.ollama]
model = "llama3"
base_url = "http://host.docker.internal:11434"
```

### 本地 embedding 兜底（Ollama + bge-m3）

不想再多一份 embedding API Key、或想让系统在断网时仍能跑相似度计算，可以让 Ollama 同时承担 embedding 服务：

```bash
# 1. 在宿主机拉取 bge-m3（首次 ~568MB，CPU 即可跑）
ollama pull bge-m3

# 2. 在容器里写入 embedding 配置（推荐用 setup-embedding 命令）
docker exec -it openbiliclaw-backend uv run openbiliclaw setup-embedding
```

或直接编辑 `config.toml` 的 `[llm.embedding]` 段：

```toml
[llm.embedding]
provider = "ollama"
model = "bge-m3"
```

注意：容器需要能访问宿主机的 Ollama，确认 `[llm.ollama] base_url` 已经设到 `http://host.docker.internal:11434`，embedding 会自动复用同一连接。

## 常见问题

**Q: 容器启动后如何确认服务正常？**

```bash
curl http://127.0.0.1:8420/api/health
```

**Q: 如何更新到最新版本？**

```bash
git pull
docker compose up -d --build
```

**Q: 端口 8420 被占用怎么办？**

修改 `docker-compose.yml` 中的端口映射：

```yaml
ports:
  - "9090:8420"  # 宿主机 9090 → 容器 8420
```

**Q: 数据库出现问题怎么修复？**

如果数据库出现问题，可以在容器内运行 `docker exec openbiliclaw-backend openbiliclaw db-repair` 进行检查和修复。
