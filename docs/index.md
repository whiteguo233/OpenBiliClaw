# 📖 OpenBiliClaw 文档导航

> 本页面是项目文档的一站式入口。用户看第一区块就够了；第二区块起面向开发者和贡献者。

## 👤 我是用户

- [项目主页](index.html) — GitHub Pages 首页，桌面安装包 / 一句话安装、插件下载和产品卖点概览
- [DSH 客户端插件](https://github.com/whiteguo233/dsh-openbiliclaw) — 把 OpenBiliClaw 装进 DeepSeek Harness：DSH 界面常驻第四栏（推荐 / 内容库 / 对话 / 画像 / 设置）+ 22 个 Agent Bridge 工具，Agent 也能读推荐、答探测、闭环学习
- [Flutter 移动客户端](https://github.com/whiteguo233/OpenBiliClaw-mobile) — 独立仓库的原生 App（Android / iOS / Web / 桌面），[Latest Release](https://github.com/whiteguo233/OpenBiliClaw-mobile/releases/latest) 提供 Android 签名 APK 与 iOS 自签名 IPA，连接同一本地后端
- [常见问题 FAQ](faq.md) — macOS 安全阻挡、插件连不上后端、embedding 配置、跨机器迁移、手机访问等高频问题
- [GitHub Releases](https://github.com/whiteguo233/OpenBiliClaw/releases/latest) — Latest Release 的 `openbiliclaw-v*` 聚合页，下载浏览器插件 zip / Safari dmg 和桌面安装包；维护者通道仍保留 `extension-v*` / `desktop-v*` / `backend-v*`
- [国内下载（123 云盘）](https://4001474255.share.123pan.cn/123pan/IxbZMh-90KO3) — 当前 v0.3.221 的 7 个包，分享永久有效并可直接打开下载；[Gitee v0.3.221 发行版](https://gitee.com/whiteguo233/openbiliclaw/releases/tag/openbiliclaw-v0.3.221) 提供国内镜像附件与源码入口
- [隐私权政策](privacy.md) — 插件数据收集披露、本地优先数据流与明文迁移包说明
- [变更日志](changelog.md) — 各版本交付记录
- [Docker 部署指南](docker-deployment.md) — 手动 Docker / docker compose 部署步骤
- [Safari Web Extension 构建](safari-extension-build.md) — macOS 上 `build:safari` / `package:safari`、`safari-web-extension-converter` 转 Xcode 工程、Developer ID 签名 + notarization、`extension-v*` 发版链与限制矩阵
- [可选 HTTPS 部署](https-deployment.md) — 公网域名 Caddy 自动证书，以及可信 LAN 的 TLS profile / 本地 CA 流程
- [应用内 Tailnet](modules/tailnet.md) — 电脑端内嵌 tsnet helper，与 `OpenBiliClaw-mobile` Android / iOS 原生 App 组成私网远程链路；不覆盖其 Web / 桌面 Flutter 构建，无需系统 Tailscale
- [OpenClaw 接入最短指南](openclaw-quickstart.md) — 把 OpenBiliClaw 接进 OpenClaw / AI 编码助手
- [Agent Bridge 能力契约](agent-integration.md) — `agent-bridge/v2` 能力协商、宿主别名与新功能同步清单

## 🛠️ 我是开发者 / 贡献者

- [项目规格说明书 (SPEC)](spec.md) — 完整的项目设计与规划
- [架构设计](architecture.md) — 系统架构与模块关系
- [推荐导演三模式设计](plans/2026-09-26-director-jev-modes.md) / [Jev 官方研究](plans/2026-09-26-jev-provider-research.md) / [基础规格](plans/2026-08-31-recommendation-director-spec.md) — 逐对编排、Agent+Jev 局部修正、纯 Agent 与独立过滤 provider；设计阶段，尚未实现
- [架构总览图](architecture-overview.md) / [English](architecture-overview.en.md) — 从 README 拆出的 ASCII 架构总览：runtime 并发闸门、Agent 编排层、多源适配与发现/推荐/保存链路
- [记忆系统设计](memory-design.md) — 多层网状记忆架构详解
- [v0.1 开发任务清单](v0.1-todolist.md) — 当前版本的开发主线
- [技术债清单](technical-debt.md) — 已确认技术债、风险解析、建议治理方向和待确认 TODO 线索
- [新平台来源接入指南](platform-source-integration.md) / [来源契约模板](platform-source-contract.example.toml) / [验收报告模板](platform-source-acceptance.example.md) / [本次 YouTube/知乎 native-save 验收账本](platform-source-acceptance.native-save-confirmation.md) / [历史失败教训索引](platform-source-history-lessons.md) — 从既有多平台首版与后续修复提炼的契约式接入流程，含 capability-aware audit、任务/增量状态机、双浏览器构建与真实 E2E 完成门禁
- [Bangumi 来源文档](modules/bangumi.md) / [接入 Spec](plans/2026-07-17-bangumi-source-spec.md) / [实施计划](plans/2026-07-17-bangumi-source-plan.md) — 官方只读 API、公开收藏初始化、统一 discover、三端体验与验收边界
- [GitHub 来源文档](modules/github.md) / [冻结契约](platform-source-contract.github.toml) / [验收记录](platform-source-acceptance.github.md) — 官方只读 REST API、公开 repository discovery、starred repositories 初始化与三端文字卡
- [Linux.do 来源文档](modules/linuxdo.md) — 扩展同源只读 GET、五路 discovery、三类个人 bootstrap、布尔登录态与隐私边界
- [知乎来源文档](modules/zhihu.md) — 浏览器任务、布尔登录态、全局 `知乎收藏` 开关与新文档零点击确认边界
- [网页工具与聊天笔记验收](testing/2026-09-26-chat-web-memory.md) — 搜索、公开链接阅读、笔记更正/审批删除与三端真实请求
- [聊天真实请求复验（9月28日）](testing/2026-09-28-chat-real-request-recheck.md) — 19 次真实模型请求、三端 UI、延迟样本与仍存在的限制
- [聊天完整复查（9月30日）](testing/2026-09-30-chat-comprehensive-e2e.md) — 最新 main 合并验收、三端草稿修复、审批冲突/任务恢复、真实限流与功能流程优化优先级
- [聊天三端逐项复验（9月30日）](testing/2026-09-30-chat-three-surfaces-e2e.md) — 插件/Web/移动端真实操作矩阵、4 项修复、限流与外网失败边界
- [聊天最新链路真实请求复验](testing/2026-10-02-chat-live-recheck.md) — 最新 main 整合、插件逐字与卡片布局修复、旧路径并发幂等
- [假设与疑惑 Luna 实测](testing/2026-10-01-chat-cards-codex-luna.md) — 三端卡片、真实 CLI 回复、疑惑数据库结算与提示遮挡修复
- [聊天风格模板验收](testing/2026-09-26-chat-personas.md) — 六种会话风格、三端真实请求与跨设备保存、移动布局和验证边界
- [手动端到端联调](manual-e2e.md) — CLI、插件与 SQLite 的真实联调步骤
- [Agent 机器契约 (短)](agent-install.md) — 给 AI 智能体读取的短部署契约,配合 README 的短粘贴语句
- [Agent 部署详细说明](agent-deployment.md) — 给人看的详细版本 + 所有 JSON 事件/错误码/排查表
- [后端自动更新 SPEC](specs/auto-update.md) — 后端源码自动应用、默认关闭的更新开关、git 安全边界与插件商店原生更新边界
- [Chrome Web Store 商店页文案](chrome-webstore-listing.md) — 可直接复制到商店后台的项目入口、安装使用说明和隐私引导
- [主页 SEO 维护指南](seo.md) — Search Console / Bing 提交清单、sitemap / OG / JSON-LD 长期维护要点

## 可视化架构图

- [Soul 模块架构与流程图](diagrams/soul-architecture.html) — Soul 真实写回口、pipeline 输入边界、完整 rebuild 与局部写回路径
- [Soul 更新变化流程图](diagrams/soul-update-flow.html) — 事件来源矩阵、分层路由、典型场景和专属名词注释
- [Recommendation 模块架构与流程图](diagrams/recommendation-architecture.html) — 候选池 readiness、serve 热路径、PoolCurator、MMR 和反馈回流
- [Web HTML 模块架构与流程图](diagrams/web-architecture.html) — `/web` 桌面端、`/m` 移动端、REST hydration、runtime-stream、配置页迁移和用户动作边界
- [Discovery 模块架构图](diagrams/discovery-architecture.html) — 多源发现、刷新调度、评估优化和模块协议边界

## 模块文档

| 模块 | 文档 | 对应代码 | 状态 |
|------|------|----------|------|
| 后端 API | [modules/api.md](modules/api.md) | `src/openbiliclaw/api/` | ✅ durable 对话 + 配置后台应用 + 本机-only 迁移四 API；GitHub 配置 / 状态 / init / repository 推荐 DTO 已记录 |
| LLM 多模型支持 | [modules/llm.md](modules/llm.md) | `src/openbiliclaw/llm/` | ✅ 统一结构化 JSON 容错 + Ollama embedding 空凭据静默；十三来源 planner 与 GitHub repository 查询约束已记录 |
| B 站接入层 | [modules/bilibili.md](modules/bilibili.md) | `src/openbiliclaw/bilibili/` | ✅ M3 完成 |
| 多源适配层 | [modules/discovery.md](modules/discovery.md#多源适配层) | `src/openbiliclaw/sources/` | ✅ 既有多源 discovery；GitHub repository source 为本分支 🧪 接线，验收见专用 ledger |
| Bangumi 接入 | [modules/bangumi.md](modules/bangumi.md) | `src/openbiliclaw/sources/bangumi*.py` + `runtime/bangumi_producer.py` | ✅ 官方匿名只读 API + 公开收藏 init + search/ranked/latest discovery |
| GitHub 接入 | [modules/github.md](modules/github.md) | `src/openbiliclaw/sources/github*.py` + `runtime/github_producer.py` | 🧪 官方匿名/可选 PAT API + public starred init + repository search/ranked/latest 已接线；真实门禁以 [acceptance ledger](platform-source-acceptance.github.md) 为准 |
| Linux.do 接入 | [modules/linuxdo.md](modules/linuxdo.md) | `src/openbiliclaw/sources/linuxdo_tasks.py` + `runtime/linuxdo_producer.py` + `extension/src/**/linuxdo*` | ✅ 只读实现、fixture、双浏览器构建与真实已登录 Chrome E2E 已完成 |
| V2EX 接入 | [modules/v2ex.md](modules/v2ex.md) | `src/openbiliclaw/sources/v2ex*.py` + `runtime/v2ex_producer.py` + 扩展任务桥 | ✅ 匿名 API/Feed + 可选 PAT + 四个只读 bootstrap scope + Topic 回复聚合 |
| 微博接入 | [modules/weibo.md](modules/weibo.md) | `src/openbiliclaw/sources/weibo*.py` + `runtime/weibo_producer.py` + `extension/src/**/weibo*` | ✅ 匿名公开 search/hot/creator discovery + 登录态 init-only 收藏 / 关注 / mentions 任务桥；后端不接收 Cookie |
| 平台来源接入契约 | [modules/source-auth.md](modules/source-auth.md) | `src/openbiliclaw/api/source_auth/` | ✅ 十三来源契约正交化 + `verify_method` 证据强度 + 一键验证；GitHub 为匿名可用、可选 PAT，移动端凭据管理仍为有意排除 |
| YouTube 接入 | [modules/youtube.md](modules/youtube.md) | `src/openbiliclaw/youtube/` + `src/openbiliclaw/sources/yt_tasks.py` | ✅ init / fetch smoke / Google Takeout 导入 |
| TikTok 接入（实验性） | [modules/tiktok.md](modules/tiktok.md) | `src/openbiliclaw/sources/tiktok.py` + `src/openbiliclaw/discovery/strategies/tiktok.py` + `runtime/tiktok_producer.py` | ✅ yt-dlp 匿名 hashtag / creator discovery（issue #88）；无登录态、无扩展依赖 |
| 知乎接入 | [modules/zhihu.md](modules/zhihu.md) | `src/openbiliclaw/sources/zhihu_tasks.py` + `runtime/zhihu_producer.py` + `extension/src/**/zhihu*` | ✅ 登录态任务桥、五路 discovery、账号信号与原生保存确认 |
| 记忆系统 | [modules/memory.md](modules/memory.md) | `src/openbiliclaw/memory/` | ✅ 完成 |
| 灵魂引擎 | [modules/soul.md](modules/soul.md) | `src/openbiliclaw/soul/` | ✅ 完成 |
| 聊天 Agent Loop | [modules/agent.md](modules/agent.md) | `src/openbiliclaw/agent/` | 🧪 M1–M7 后端完成（工具注册表 / 原生 FC / 多跳 loop / SSE 真流式 / v1 工具集 / skill / 会话 / 任务中心 / 审批门）；M8/M9 三端前端已交付 |
| 桌面 Web | [modules/desktop-web.md](modules/desktop-web.md) | `src/openbiliclaw/web/desktop/` | 🧪 M8 聊一聊 agent loop 前端：流式过程折叠展示、会话侧栏、skill 切换、审批卡、任务中心 |
| 内容发现引擎 | [modules/discovery.md](modules/discovery.md) | `src/openbiliclaw/discovery/` | ✅ v0.3.x 多源 + 统一待评估池 + 跨源跨轮 topic 配额 |
| 推荐引擎 | [modules/recommendation.md](modules/recommendation.md) | `src/openbiliclaw/recommendation/` | ✅ v0.3.x 双轴 fatigue + per-group 候选窗口 + reshuffle 0.6s |
| 存储层 | [modules/storage.md](modules/storage.md) | `src/openbiliclaw/storage/` | ✅ SQLite schema + discovery candidates / pool readiness + 去除 API auth 的 `.obcbackup` 快照、暂存 / 取消、重启应用与回滚 |
| 原生保存同步 | [modules/saved-sync.md](modules/saved-sync.md) | `src/openbiliclaw/saved_sync/` | ✅ canonical API + runtime + B 站 direct adapter + 六平台 extension adapter/executor + 三端后端状态驱动保存界面；GitHub repository 与微博保持 typed identity 的 local-only 保存 |
| 灵魂管线架构 | [modules/soul-pipeline-architecture.md](modules/soul-pipeline-architecture.md) | `src/openbiliclaw/soul/` | ✅ 完成 |
| 浏览器插件 | [modules/extension.md](modules/extension.md) | `extension/` | ✅ 支持既有来源任务桥与跨平台行为采集；GitHub 只新增 setup/status/config/repository 卡片 UI，明确不新增 host permission、content script、Cookie、任务或 native-save |
| CLI 命令参考 | [modules/cli.md](modules/cli.md) | `src/openbiliclaw/cli.py` | 🧪 已记录 GitHub `fetch-github`、三条 discover smoke 与正式 `discover --source github`；验收状态见 ledger |
| 配置参考 | [modules/config.md](modules/config.md) | `config.example.toml` | ✅ 持续更新（含 `[sources.github]`、固定 PAT 环境变量、三分支预算、节流、bootstrap 上限与来源占比） |
| 局域网密码门禁 | [modules/api-auth.md](modules/api-auth.md) | `src/openbiliclaw/auth_core.py` + `src/openbiliclaw/api/auth.py` | ✅ 可选 `[api.auth]` 密码门禁 + `/api/auth/*` + `set-password` |
| 应用内 Tailnet | [modules/tailnet.md](modules/tailnet.md) | `runtime/tailnet_supervisor.py` + `cmd/openbiliclaw-tailnet/` | ✅ 默认关闭的 Android / iOS tsnet 私网入口；桌面包内置，源码可构建，Docker 首版不内置；macOS helper 需 12+，不改变主应用 10.15+ 目标 |
| 公网 HTTPS 网关 | [HTTPS 部署](https-deployment.md) | `docker-compose.https.yml` | ✅ 默认关闭的 Caddy 自动证书 + shared-loopback upstream + REST/WebSocket |
| TLS 反向代理 | [modules/tls-proxy.md](modules/tls-proxy.md) | `src/openbiliclaw/tls_proxy.py` | ✅ 默认关闭的 LAN/self-managed HTTPS + 精确 Origin/Host + WebSocket + SAN 检测 |
| 集成适配层 | [modules/integrations.md](modules/integrations.md) · [agent-integration.md](agent-integration.md) | `src/openbiliclaw/integrations/` | ✅ Agent Bridge v2；OpenClaw 兼容，Hermes / WorkBuddy 共用能力清单 |
| 运行时服务 | [modules/runtime.md](modules/runtime.md) | `src/openbiliclaw/runtime/` | ✅ refresh / candidate pipeline / presence gate / autostart / Ollama preflight / degraded boot / runtime-stream / 扩展 E2E 控制事件 / backend tag auto-update |
| 原生保存授权 E2E | [native-save-e2e.md](native-save-e2e.md) | 手动验证 runbook | ⚠️ 仅在明确授权命名 BV 号 / 测试账号后执行平台写入 |
| 六平台原生保存安全 E2E | [testing/six-platform-native-save-e2e.md](testing/six-platform-native-save-e2e.md) | 精确授权、安全结果与手动验证矩阵 | ⚠️ 默认只做 local-only；六平台真实写入必须逐项获得当前授权 |
| 引导初始化 | [modules/init.md](modules/init.md) | `src/openbiliclaw/cli.py`（`run_guided_init`）+ `runtime/init_coordinator.py` + `runtime/init_prereqs.py` | ✅ v0.3.102 共享流水线 + `InitCoordinator` 状态机 + `/api/init*` + 写者门控 + 插件推荐 tab CTA |

## 开发指南

- [贡献指南](contributing.md) — 环境搭建、代码规范、文档更新要求
- [AGENTS.md](../AGENTS.md) — AI 代理开发规则（含文档更新强制要求）
