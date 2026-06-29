# 📖 OpenBiliClaw 文档导航

> 本页面是项目文档的一站式入口。

## 项目概览

- [项目主页](index.html) — GitHub Pages 首页，桌面安装包 / 一句话安装、插件下载、GitHub 入口和产品卖点概览
- [主页 SEO 维护指南](seo.md) — Search Console / Bing 提交清单、sitemap / OG / JSON-LD 长期维护要点
- [项目规格说明书 (SPEC)](spec.md) — 完整的项目设计与规划
- [隐私权政策](privacy.md) — Chrome Web Store / 插件数据收集披露与本地优先数据流说明
- [Chrome Web Store 商店页文案](chrome-webstore-listing.md) — 可直接复制到商店后台的项目入口、安装使用说明和隐私引导
- [v0.1 开发任务清单](v0.1-todolist.md) — 当前版本的开发主线
- [技术债清单](technical-debt.md) — 已确认技术债、风险解析、建议治理方向和待确认 TODO 线索
- [架构设计](architecture.md) — 系统架构与模块关系
- [记忆系统设计](memory-design.md) — 多层网状记忆架构详解
- [变更日志](changelog.md) — 各里程碑交付记录
- [GitHub Releases](https://github.com/whiteguo233/OpenBiliClaw/releases/latest) — 普通用户看 Latest Release 的 `openbiliclaw-v*` 聚合页，同时下载浏览器插件 zip、实验性桌面安装包并查看后端源码 tag；维护者通道仍保留 `extension-v*` / `desktop-v*` / `backend-v*`
- [手动端到端联调](manual-e2e.md) — CLI、插件与 SQLite 的真实联调步骤
- [新平台来源接入指南](platform-source-integration.md) — 事件抓取、插件任务、discover、配置页、推荐卡、真实 E2E 和发布文档的标准接入流程
- [OpenClaw 接入最短指南](openclaw-quickstart.md) — Docker 优先、本地兜底的安装、初始化、skill 发现与 CLI bridge 自检
- [Agent 机器契约 (短)](agent-install.md) — 给 AI 智能体 WebFetch 的短契约,配合 README 的短粘贴语句
- [Agent 部署详细说明](agent-deployment.md) — 给人看的详细版本 + 所有 JSON 事件/错误码/排查表
- [Docker 部署指南](docker-deployment.md) — 手动 Docker / docker compose 部署步骤
- [后端自动更新 SPEC](specs/auto-update.md) — 后端源码自动应用、默认关闭的更新开关、git 安全边界与插件商店原生更新边界
- [新平台来源接入指南](platform-source-integration.md) — 按知乎接入经验沉淀的端到端来源开发、验证和发布检查清单

## 可视化架构图

- [Soul 模块架构与流程图](diagrams/soul-architecture.html) — Soul 真实写回口、pipeline 输入边界、完整 rebuild 与局部写回路径
- [Soul 更新变化流程图](diagrams/soul-update-flow.html) — 事件来源矩阵、分层路由、典型场景和专属名词注释
- [Recommendation 模块架构与流程图](diagrams/recommendation-architecture.html) — 候选池 readiness、serve 热路径、PoolCurator、MMR 和反馈回流
- [Web HTML 模块架构与流程图](diagrams/web-architecture.html) — `/web` 桌面端、`/m` 移动端、REST hydration、runtime-stream 和用户动作边界
- [Discovery 模块架构图](diagrams/discovery-architecture.html) — 多源发现、刷新调度、评估优化和模块协议边界

## 模块文档

| 模块 | 文档 | 对应代码 | 状态 |
|------|------|----------|------|
| LLM 多模型支持 | [modules/llm.md](modules/llm.md) | `src/openbiliclaw/llm/` | ✅ v0.3.74 统一结构化 JSON 容错 + Ollama embedding 空凭据静默 |
| B 站接入层 | [modules/bilibili.md](modules/bilibili.md) | `src/openbiliclaw/bilibili/` | ✅ M3 完成 |
| 多源适配层 | [modules/discovery.md](modules/discovery.md#多源适配层) | `src/openbiliclaw/sources/` | ✅ v0.3.x 落地 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / 通用 Web 多源 discovery |
| YouTube 接入 | [modules/youtube.md](modules/youtube.md) | `src/openbiliclaw/youtube/` + `src/openbiliclaw/sources/yt_tasks.py` | ✅ init / fetch smoke / Google Takeout 导入 |
| 记忆系统 | [modules/memory.md](modules/memory.md) | `src/openbiliclaw/memory/` | ✅ 完成 |
| 灵魂引擎 | [modules/soul.md](modules/soul.md) | `src/openbiliclaw/soul/` | ✅ 完成 |
| 内容发现引擎 | [modules/discovery.md](modules/discovery.md) | `src/openbiliclaw/discovery/` | ✅ v0.3.x 多源 + 统一待评估池 + 跨源跨轮 topic 配额 |
| 推荐引擎 | [modules/recommendation.md](modules/recommendation.md) | `src/openbiliclaw/recommendation/` | ✅ v0.3.x 双轴 fatigue + per-group 候选窗口 + reshuffle 0.6s |
| 存储层 | [modules/storage.md](modules/storage.md) | `src/openbiliclaw/storage/` | ✅ SQLite schema + discovery_candidates 待评估池 + pool readiness 计数 |
| 灵魂管线架构 | [modules/soul-pipeline-architecture.md](modules/soul-pipeline-architecture.md) | `src/openbiliclaw/soul/` | ✅ 完成 |
| 浏览器插件 | [modules/extension.md](modules/extension.md) | `extension/` | ✅ 支持 B 站 + 小红书 + 抖音 + YouTube + X + 知乎 + Reddit 任务桥、跨平台行为采集、扩展驱动 E2E 捕捉自检、Cookie 同步、自启动开关和降级配置修复 |
| CLI 命令参考 | [modules/cli.md](modules/cli.md) | `src/openbiliclaw/cli.py` | ✅ 持续更新 (含 `autostart` / `setup-embedding` / `discover-douyin` / `fetch-youtube` / `fetch-reddit` / `discover-reddit*`) |
| 配置参考 | [modules/config.md](modules/config.md) | `config.example.toml` | ✅ 持续更新 (含 `[sources.reddit]`、`[autostart]`、`/api/config` 回滚与 `reset_fields`) |
| 局域网密码门禁 | [modules/api-auth.md](modules/api-auth.md) | `src/openbiliclaw/auth_core.py` + `src/openbiliclaw/api/auth.py` | ✅ 可选 `[api.auth]` 密码门禁 + `/api/auth/*` + `set-password` |
| 集成适配层 | [modules/integrations.md](modules/integrations.md) | `src/openbiliclaw/integrations/` | ✅ OpenClaw adapter 已接入 |
| 运行时服务 | [modules/runtime.md](modules/runtime.md) | `src/openbiliclaw/runtime/` | ✅ refresh / candidate pipeline / presence gate / autostart / Ollama preflight / degraded boot / runtime-stream / 扩展 E2E 控制事件 / backend tag auto-update |
| 引导初始化 | [modules/init.md](modules/init.md) | `src/openbiliclaw/cli.py`（`run_guided_init`）+ `runtime/init_coordinator.py` + `runtime/init_prereqs.py` | ✅ v0.3.102 共享流水线 + `InitCoordinator` 状态机 + `/api/init*` + 写者门控 + 插件推荐 tab CTA |

## 开发指南

- [贡献指南](contributing.md) — 环境搭建、代码规范、文档更新要求
- [AGENTS.md](../AGENTS.md) — AI 代理开发规则（含文档更新强制要求）
