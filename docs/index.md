# 📖 OpenBiliClaw 文档导航

> 本页面是项目文档的一站式入口。

## 项目概览

- [项目主页](index.html) — GitHub Pages 首页，一句话安装、插件下载、GitHub 入口和产品卖点概览
- [项目规格说明书 (SPEC)](spec.md) — 完整的项目设计与规划
- [v0.1 开发任务清单](v0.1-todolist.md) — 当前版本的开发主线
- [架构设计](architecture.md) — 系统架构与模块关系
- [记忆系统设计](memory-design.md) — 多层网状记忆架构详解
- [变更日志](changelog.md) — 各里程碑交付记录
- [GitHub Releases](https://github.com/whiteguo233/OpenBiliClaw/releases) — 从 `extension-v*` 下载插件；后端源码更新看 `backend-v*` tag，不发布后端桌面包
- [手动端到端联调](manual-e2e.md) — CLI、插件与 SQLite 的真实联调步骤
- [OpenClaw 接入最短指南](openclaw-quickstart.md) — Docker 优先、本地兜底的安装、初始化、skill 发现与 CLI bridge 自检
- [Agent 机器契约 (短)](agent-install.md) — 给 AI 智能体 WebFetch 的短契约,配合 README 的短粘贴语句
- [Agent 部署详细说明](agent-deployment.md) — 给人看的详细版本 + 所有 JSON 事件/错误码/排查表
- [Docker 部署指南](docker-deployment.md) — 手动 Docker / docker compose 部署步骤

## 模块文档

| 模块 | 文档 | 对应代码 | 状态 |
|------|------|----------|------|
| LLM 多模型支持 | [modules/llm.md](modules/llm.md) | `src/openbiliclaw/llm/` | ✅ v0.3.74 统一结构化 JSON 容错 + Ollama embedding 空凭据静默 |
| B 站接入层 | [modules/bilibili.md](modules/bilibili.md) | `src/openbiliclaw/bilibili/` | ✅ M3 完成 |
| 多源适配层 | [modules/discovery.md](modules/discovery.md#多源适配层) | `src/openbiliclaw/sources/` | ✅ v0.3.0 落地 B 站 / 小红书 / 通用 Web；v0.3.69 接入抖音插件签名 search / hot / feed discovery 和 YouTube 初始化画像 |
| YouTube 接入 | [modules/youtube.md](modules/youtube.md) | `src/openbiliclaw/youtube/` + `src/openbiliclaw/sources/yt_tasks.py` | ✅ init / fetch smoke / Google Takeout 导入 |
| 记忆系统 | [modules/memory.md](modules/memory.md) | `src/openbiliclaw/memory/` | ✅ 完成 |
| 灵魂引擎 | [modules/soul.md](modules/soul.md) | `src/openbiliclaw/soul/` | ✅ 完成 |
| 内容发现引擎 | [modules/discovery.md](modules/discovery.md) | `src/openbiliclaw/discovery/` | ✅ v0.3.x 多源 + 跨源跨轮 topic 配额 + 抖音 `DouyinDiscoveryService` |
| 推荐引擎 | [modules/recommendation.md](modules/recommendation.md) | `src/openbiliclaw/recommendation/` | ✅ v0.3.x 双轴 fatigue + per-group 候选窗口 + reshuffle 0.6s |
| 灵魂管线架构 | [modules/soul-pipeline-architecture.md](modules/soul-pipeline-architecture.md) | `src/openbiliclaw/soul/` | ✅ 完成 |
| 浏览器插件 | [modules/extension.md](modules/extension.md) | `extension/` | ✅ 支持 B 站 + 小红书 + 抖音 + YouTube 任务桥 / 行为采集 / Cookie 同步 / 降级配置修复 |
| CLI 命令参考 | [modules/cli.md](modules/cli.md) | `src/openbiliclaw/cli.py` | ✅ 持续更新 (含 `setup-embedding` / `discover-douyin` / `fetch-youtube` / `import-youtube`) |
| 配置参考 | [modules/config.md](modules/config.md) | `config.example.toml` | ✅ 持续更新 (含 `/api/config` 回滚与 `reset_fields`) |
| 集成适配层 | [modules/integrations.md](modules/integrations.md) | `src/openbiliclaw/integrations/` | ✅ OpenClaw adapter 已接入 |
| 运行时服务 | [modules/runtime.md](modules/runtime.md) | `src/openbiliclaw/runtime/` | ✅ refresh / presence gate / degraded boot / runtime-stream / backend tag auto-update |

## 开发指南

- [贡献指南](contributing.md) — 环境搭建、代码规范、文档更新要求
- [AGENTS.md](../AGENTS.md) — AI 代理开发规则（含文档更新强制要求）
