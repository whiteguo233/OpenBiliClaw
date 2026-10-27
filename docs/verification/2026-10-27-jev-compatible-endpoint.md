# Jev 可配置兼容端点验收（2026-10-27）

## 交付与数据边界

- 桌面 Web / 插件 popup / 移动 Web 共用 Jev 连接表单，支持 Base URL 或完整原生接口、Token、固定模型与双上下文容量。选择候选评估器 `jev` 才替换完整评估；连接开关不擅自开启业务路由。
- Base 根/代理前缀补 `/v1/systemone`，已有 `/v1` 仅补 `/systemone`；配置 API 接受互斥 `base_url` 输入，TOML 保持 canonical `endpoint_url`。不锁官方域名，不自动切模型，自定义完整路径保持原样。
- HTTP 仅允许回环测试，外部 HTTPS；不跟随重定向，拒绝 URL 内凭据/query/fragment。换目标必须新 Token，空 Token 保留、显式清除；读取和错误不回显，私有配置权限 0600。
- 本轮在独立 worktree 测试，未改日常配置/数据库，未合并、推送或重启用户服务。真实 key 只由测试进程内存/环境提供，未写入测试文件、报告或临时 TOML。
- 架构接线、CLI 命令、依赖与安装流程不变；修正在既有配置更新/共享表单/原生 adapter 边界，不新增聊天 fallback 或独立云端业务。

## 修复前证据与协议修正

此前真实 OpenRouter 请求 HTTP 200，但 native parser 拒绝，完整评估 0/3、全部仍待评估。离线回放同一真实响应可稳定复现：

1. 顶层 `id/provider` 与 `usage.cost` 被 extra-forbid 拒绝；现在仅接受这些已知元数据的严格类型，不忽略任意未知字段。
2. 十级 Score `7.48` 与已舍入概率的加权值 `7.52` 不满足 1e-6 等式；五级 Score `3.31` 的概率和为 `0.99`。

现在保留原始 score/概率，针对全部处于两位小数网格的返回，以每项 ±0.005 的量化区间验证存在合法未舍入分布及期望值；不静默归一化/重算 score。高精度返回仍严格验证，非有限/越界/错模型/题目或选项不符/篡改 rubric 继续拒绝。完整评估缓存绑定 `JEV_WIRE_VERSION`。

该两位精度来自本次真实返回的观测，不冒称所有服务商承诺。元数据和 Base 协议依据：[OpenRouter 官方 SDK 文档](https://openrouter.ai/docs/guides/community/typesafe-sdk)。

## 三端与真实进程（远端推理为合成）

- Popup：真实源码表单 → 正式配置 API → 隔离 TOML → 重新读取地址/评估器，Token 不回显；仅 Chrome API 边界 stub。
- Mobile：同上，关闭重开设置后恢复 Base URL / 固定模型 / Jev 评估，Token 输入清空。
- Desktop：两种地址模式分别从正式 app factory / lifespan 启动独立 Uvicorn，经表单保存，等待真实 apply revision 完成，再导入 3 条合成候选。后台使用保存的 URL/Token/模型完成评估并入库；每候选 10 题未拆分，长文原件/条件句保留，真实回环 HTTP 独立校验 12KB 护栏。
- Headless Chromium 共享表单额外覆盖 375/768/1024/1440 宽度、浅/深色、等价地址切换不改目标、新目标须新 Token、无横向溢出。
- Ego 人工可视检查被首次 onboarding 阻挡，未绕过或改用户浏览器。上述自动化浏览器验收不是扩展真实 MV3 安装、Safari 宿主或人工体验认证。

## OpenRouter 真实请求

先用正式配置 API 保存并加载隔离配置，再经正式 `build_decision_provider`、完整 evaluator、candidate pipeline 和临时 SQLite 执行；非合成模型响应。配置 API 的 runtime rebuild 在该测试中隔离；真实运行中热重建与 Token 持久化由上节独立进程/回环测试覆盖，不能将两项拼称为用户日常服务的付费模型在线验收。

- Base URL：`https://openrouter.ai/api`，保存/实际请求地址：`https://openrouter.ai/api/v1/systemone`。
- 请求/返回模型均固定 `typesafe/jev-1.14-20261014`，实际 provider `TypeSafe`，不使用 Jev Router。
- 显式登记保守 64,000 / 32,000 token 容量，工程护栏 48,000 / 24,000 UTF-8 字节；不是测出模型最大容量或精确 tokenizer。
- 仅合成画像/长短候选。两组独立样本用于比较预算，不是生产中多轮重复评估；每次仅 1 次传输尝试，最多 6 请求，实际 4 次全部 HTTP 200 / evidence OK；没有摘要模型调用或 LLM fallback。

| 组 | state＋单题护栏 | 候选/题数组合 | 实际最大 state＋单题 | 完成 | 入池/拒绝 | 遗留待评估 |
|---|---:|---|---:|---:|---|---:|
| 标准 | 24,000 bytes | 2/20 + 1/10 | 23,877 bytes | 3/3 | 2 / 1 | 0 |
| 收紧 | 12,000 bytes | 2/20 + 1/10 | 11,908 bytes | 3/3 | 3 / 0 | 0 |

请求 JSON 字节数分别为 23,803、32,625、23,803、20,656；服务端 input tokens 分别为 7,563、14,239、7,563、8,022，output tokens 为 1,419、1,668、1,419、1,073。API `usage.cost` 合计 **$0.001570254**；不是免费额度或完整账户账单证明。

两组原始长文均保留；选中原文片段分别为 108/321、43/321，有显式 partial 覆盖信息。准入结果不同，因此只认证配置/传输/解析/预算适配/完整评估落库链路，**不认证语义无损、中文质量或评分阈值校准**。本次未测试真实图片、导演模型效果、其它网关或其它上下文规格。

## 自动回归命令

```bash
PYTHONPATH=src pytest -q tests/test_decision_models.py tests/test_jev_provider.py tests/test_jev_full_evaluator.py tests/test_jev_settings_api.py
PYTHONPATH=src pytest -q tests/test_decision_config.py tests/test_jev_context_budget.py tests/test_jev_on_demand_profile.py tests/test_jev_evaluation_config.py tests/test_jev_evaluator_http_pipeline.py tests/test_jev_acceptance_cli.py tests/test_desktop_web_settings_save_e2e.py
PYTHONPATH=src pytest -q tests/test_jev_runtime_e2e.py tests/test_jev_settings_popup.py tests/test_jev_settings_mobile.py tests/test_jev_connection_form.py
PYTHONPATH=src pytest -q tests/test_decision_registry.py tests/test_decision_runtime.py tests/test_decision_fake.py tests/test_pair_composer.py tests/test_pair_proposer.py tests/test_pair_composition_models.py tests/test_pair_replay.py tests/test_semantic_filter.py tests/test_i18n.py tests/test_cli.py -k 'not login_codex_import'
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/
python scripts/build_i18n_catalogs.py --check
cd extension
npm test
npm run typecheck
node scripts/build.mjs
TARGET=firefox node scripts/build.mjs
TARGET=safari node scripts/build.mjs
```

上述后端相关选择合计 **942 passed、1 deselected**（183 + 136 + 5 + 610 + 8）；扩展 **1857 passed**。
Ruff lint/format（891 个 Python 文件）、MyPy（363 个源文件）、i18n（2920 字符串/210 模式）通过。
扩展 typecheck 与 Chrome / Firefox / Safari 构建通过，资产预检分别 22/22/21。

不是全仓库 pytest。特意排除会探测本机真实 OAuth 的 `test_login_codex_import_uses_source_path`；CLI 其余用例包含 config-show。测试不把被排除项冒称通过。
