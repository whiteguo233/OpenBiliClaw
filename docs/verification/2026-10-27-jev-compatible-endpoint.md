# Jev 可配置兼容端点验收（2026-10-27）

本记录按阶段追加，保留历史失败。最新结果见[后续修复与真实恢复验收](#后续修复与真实恢复验收2026-10-27)：
正向兴趣分配及本地超限空重试已修复，并以正式隔离进程＋真实请求验证容量恢复；未部署日常实例。

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

## 后续补测：正式进程、真实画像与真实视频副本

**结论：传输链路通过，但动态适配整体未通过，发现两处缺口。** 此节不能被上面的合成画像验证结果覆盖。

### 运行范围

当前仓库 main 为 `23de203a`，本分支被测提交 `04a62a6c`，修复未部署到日常服务。
只读检查仓库日常配置时，`discovery.eval_scorer=llm`、Jev disabled、导演 off；未改该配置。
因此本轮不是“用户日常实例已开启 Jev”的证明。

新起正式 `openbiliclaw.api.app:create_app` / lifespan / RuntimeContext / coordinator 的独立 Uvicorn
进程，使用真实画像和两条真实 Bilibili 视频的只读快照，以及一条明确标记的合成长文本压力样本。
正常配置 API 保存 → apply revision 完成 → 候选队列 → 正式后台评估器 → OpenRouter → 原门槛判定落库，
没有替换模型响应、evaluator、引擎、调度器或配置 rebuild。候选通过公开 enqueue 接口放入隔离
SQLite，本轮没有重新抓取源站视频或验证源站采集/扩展入口。旁路 HTTP event hooks 仅核对长度、
原文 offsets、画像来源/必需约束和响应元数据，不改 URL、请求内容或远端答案；观察断言失败会停止发出该请求。

画像源文件 58,850 bytes；原始本地画像目录 24,570 bytes / 114 个正向兴趣；包含有效覆盖编辑和
运行时合并后的目录为 24,686 bytes。必需共享画像（避雷、风格、情境）7,375 bytes，带投影元数据的
最低画像视图 7,484 bytes。保留画像/偏好/覆盖编辑/推测状态的副本，以正式 SoulEngine 得到有效画像。
原画像文件哈希前后相同；原数据库仅 mode=ro 查询，未写入，Cookie 和真实 key 未复制到测试文件。

没有给其它聊天/embedding 链路真实服务凭据；本轮验证的是 Jev 完整评估，不是文案生成、源站采集、
正向入池推荐、多模态或导演质量。本次未补做配置页 UI：正式进程经同一配置 HTTP API 保存，三端
表单和 Token 的 UI 测试仍以前节为准。模型固定 `typesafe/jev-1.14-20261014`，通过收紧所选模型的
容量登记模拟小窗口，不更换或自动选择模型。

### 出站请求实测

| 档位 | 配置总量 / state＋单题 token | 生效双字节护栏 | 真实请求 | 每候选正向兴趣 | 长文片段 |
|---|---|---|---:|---|---|
| 正常 | 64,000 / 32,000 | 48,000 / 24,000 | 2，均 200 | 4 / 4 / 1 | 65 / 454 |
| 收紧 | 48,000 / 24,000 | 36,000 / 18,000 | 2，均 200 | **0 / 0 / 0** | 34 / 454 |
| 更小 | 32,000 / 16,000 | 24,000 / 12,000 | 0 | 未能构造请求 | 未发出 |

正常档两个请求分别 34,032 / 32,652 bytes，最大 state＋单题分别 15,990 / 23,904 bytes；
收紧档分别 33,006 / 26,469 bytes，最大 state＋单题分别 14,964 / 17,721 bytes。
每档均按 2 候选/20 题 + 1 候选/10 题执行，无候选重复远端评估。实际容量指纹随保存配置改变；
观测值均在当档限制内，排除了热更新仍沿用旧预算这一假设。

全部已发送请求都保留完整必需画像，兴趣记录均来自原画像；未发送整个目录或人物长画像。
两条真实视频保留全部 8/8、5/5 个原文片段；压力样本为显式 partial，选中片段全部逐段核对原文
字段/offset，条件句“如果用于生产，必须先备份；否则不适用。”两档均保留。
Jev 只接收候选标题/描述/正文等文本及元数据，不包含视频帧、封面像素、音频或视频文件。

四次响应分别耗时 1,783.06 / 1,360.94 / 2,378.02 / 3,031.52 ms，均为 TypeSafe、实际固定模型
完全匹配；API 报告费用合计 **$0.002059302**。两档各三条均获得完整评估并按现有 0.60 门槛进入
`rejected_low_score`，没有伪造缓存成功或降低门槛；这不证明模型质量或正向推荐效果。

### 缺口一：软预算把可容纳的正向兴趣排空

`JevContentEvaluator._load_profile` 的画像额度为
`max(mandatory_bytes, min(available_bytes, single_guard // 3))`。
18,000-byte 护栏的 1/3 为 6,000，小于必需视图 7,484，额度刚好卡在 mandatory floor，
因此正向兴趣全部消失，即使实际请求仍有余量。避雷/风格/情境仍在，不是整个画像丢失。

同一真实样本的离线公开请求探针给两条真实候选各补回一个正常档选中的原有相关兴趣：
请求 32,512 bytes、state＋单题 14,470 bytes，仍在 36,000 / 18,000 护栏内。
该探针未调用模型，不能当成补回兴趣后的真实效果验证。

已将问题缩到不含私人信息的 0.51 秒离线复现：一个“算法”兴趣、一份超过 1/3 软额度但仍可容纳
的必需约束、短算法候选。自动选择 0 条，补回该兴趣后仅 18,247 / 10,256 bytes（护栏 36,000 / 18,000），
断言以 `fitting_relevant_interest_was_dropped` 失败。复现只在 provider 公开边界捕获请求并返回
明确的本地不可用状态，不发 HTTP。应修的是必需基线之外的可用画像预算分配，而不是删避雷或自动换模型。

### 缺口二：确定性的输入超限丢失原因并空重试

12,000-byte 档的三条候选均本地判定 `input_budget_exhausted`，未发出 Jev 请求、未增加远端费用。
但完整 evaluator 仅返回 `None`，引擎/管线将其归成 `evaluation_response_missing`，重新入队后连续
走满 **50 次 batch_eval_attempts**，三条最终都是 `failed_eval`。这不是成功适配，也不是服务端 400/429。

根因链：本地确定性装不下 → 无类型失败的 `None` → 通用“响应缺失” → 批次重试预算耗尽。
应保留输入预算不足这一失败类别，避免同一模型/容量/画像/内容未变化时立即重试；容量或画像改变后
才重新判断，并给配置页/运行状态清楚的可操作原因。仅增加重试次数不会解决。

离线零网络探针还确认 6,000-byte 护栏不会发请求。一个只有最短候选和必需画像的完整请求本身
约需 11,610-byte state＋单题：因此不能承诺任意小窗口都能无损装下；真实候选的元数据/完整条件句
还需额外空间。单独这个下界不证明所有 12KB 输入都不可能，也不能掩盖上述重试缺陷。

本次只测试和定位，**尚未修复这两处问题**，已询问是否继续修复。相关原有画像/预算单测
65 项通过，但没有覆盖本轮的真实 mandatory-to-soft-budget 比例，不能据此覆盖失败结论。
两个临时正式服务已停止；没有合并/推送、部署或重启日常实例。私密副本和完整现场仅保存在
权限受限的本机 `/tmp/obc-jev-formal-*` 诊断目录，未提交；本节只保存脱敏指标与代码归因。

## 后续修复与真实恢复验收（2026-10-27）

本节为用户批准修复后的独立结果，不覆盖上节的失败记录。仍在独立 worktree，
不修改或重启日常实例；固定 OpenRouter `typesafe/jev-1.14-20261014`，不切模型，
Token 仅通过关闭终端回显的临时环境变量传递，不落配置、报告或仓库。

### 修复合同

1. `JevContentEvaluator._load_profile()` 的必需画像作为基线，可选兴趣按剩余窗口的
   三分之一分配软额度；不足时借用实际剩余空间加载完整兴趣事实。源画像存在兴趣但
   一条也装不下时继续本地减少可选原文，不以“只有避雷”冒充完整画像。
   分配策略版本进入缓存身份；不使用摘要 LLM，不改原画像。
2. 本地确定性预算失败使用程序内 `InputBudgetFailure`，不再折叠成远端 missing。
   engine 绑定有效画像目录、负例、scorer、精确模型/容量/打包契约的 SHA-256 身份，
   pipeline 按原 claim token CAS 停放 `failed_eval` + `input_budget_exhausted:<fingerprint>`。
   不写成功评分/缓存，不增加 eval/batch attempts，不牵连混合批次中的有效项。
3. 通用 transient 死信复活排除预算停放项；同画像/容量下手动刷新、重复保存、重启均不空跑。
   下一次有效唤醒/安全扫描发现上下文身份改变才有界重新入队。
   容量或画像改变可以恢复；只换 Token、时间流逝不表示原输入会变小。
4. `/api/runtime-status` 增加只读 `candidate_eval_input_budget_blocked`，
   沿用 `candidate_eval_last_error` 显示可操作原因；coordinator 重建仍可从 durable 行恢复诊断。

### 红绿回归

最小公开边界复现修复前为 `automatic_positive_interests=0`，而手动放回一条原始兴趣仍能通过双护栏；
修复后为 1。新增短内容/长内容、大块必需画像、只停放一次、同配置不复活、画像缩小恢复、
容量放宽恢复、混合批次有效项保留、重建状态读数，以及旧 claim 无权停放新 owner 等回归。
相关 300 项测试通过；Ruff format/check 与 MyPy 363 个源文件通过，UI catalog 2920 条一致。

扩大回归执行配置/API/画像、所有 Jev/decision 单元模块和候选管线/coordinator：
初跑为 **1069 passed、3 failed、3 deselected**；三处失败均为协议假后端的旧
`pending_eval` 断言，现改为 `failed_eval` + 明确预算原因、`blocked=1`、零请求/零 attempts，
定向三项复测通过；随后整个 `test_jev_fake_backend.py` 加该 API 状态快照复测 **20 passed**。
不是把远端 422 或暂态 missing 改成预算失败，它们仍检查 pending。
新增状态字段还使全仓尝试中的一个严格 API 响应快照先失败；补上默认值 0 后已在上述扩大选择中通过。
该全仓尝试在定位此失败后主动中断（847 passed、1 failed、178 deselected），
不宣称全仓 pytest 全绿。真实凭据 integration 和 `login_codex_import` 未运行。

```bash
PYTHONPATH=src pytest -q tests/test_api_app.py::TestBackendAPI::test_runtime_status_endpoint_returns_runtime_summary tests/test_jev*.py tests/test_decision*.py tests/test_api_config_guards.py tests/test_api_config_probe.py tests/test_api_config_transactional.py tests/test_config.py tests/test_profile_views.py tests/test_profile_views_guards.py tests/test_on_demand_profile_views.py tests/test_discovery_candidate_store.py tests/test_discovery_candidate_pipeline.py tests/test_candidate_pipeline_admission.py tests/test_candidate_eval_coordinator.py -m 'not integration' --tb=short --disable-warnings
PYTHONPATH=src pytest -q tests/test_jev_fake_backend.py::test_local_overflow_does_not_reach_the_real_socket
```

### 正式进程与真实请求

使用正式 `create_app()`、lifespan、配置保存/应用、候选 coordinator、HTTP adapter 和 SQLite admission，
未注入假的业务组件或推理响应。仅在 HTTP 边界被动检查请求预算、画像事实来源、必需项和片段出处。
同一真实画像的 58,850-byte 文件副本，有效请求目录 24,686 bytes / 114 个兴趣；
共享必需画像 7,375 bytes。两条真实视频快照来自日常数据库只读查询，另加一条明确标注的
454 片段合成长文，保留“必须先备份；否则不适用”条件句。不是当天重新抓取的视频正文。

| 阶段 | 双 UTF-8 护栏：请求 / state＋最长题 | 真实 POST | 实际请求 bytes（三条） | 实际最长单题 bytes（三条） | 所选兴趣数（三条） | 结果 |
|---|---|---:|---|---|---|---|
| 正常窗口候选 | 48,000 / 24,000 | 3 | 26,895 / 26,820 / 32,652 | 18,526 / 18,613 / 23,904 | 11 / 11 / 1 | 三条均有效落库；1 cached、2 rejected_low_score |
| 收紧窗口 | 36,000 / 18,000 | 3 | 24,896 / 24,826 / 26,651 | 16,527 / 16,619 / 17,903 | 11 / 12 / 2 | 三条有效评估，均 rejected_low_score；不再兴趣归零 |
| 必需输入装不下 | 24,000 / 12,000 | 0 | — | — | — | 三条预算停放，eval/batch attempts 均为 0 |
| 同容量重复保存/唤醒 | 24,000 / 12,000 | 0 | — | — | — | 原三条仍停放，同 fingerprint，无重试；状态显示 blocked=3 |
| 重启后保存放宽容量 | 48,000 / 24,000 | 3 | 26,895 / 26,820 / 32,652 | 18,526 / 18,613 / 23,904 | 11 / 11 / 1 | 同三条自动恢复，blocked 3→0；1 cached、2 rejected_low_score |

九次真实响应均为固定模型、HTTP 200、每次十道完整问题；从未把同一候选拆成多轮付费评估。
各请求完整保留必需画像，所选兴趣均来自源目录，原文 span 的字段/字符位置逐项匹配。
长文在正常/收紧窗口分别选择 65/454、34/454 个完整片段，条件句均保留；这不是语义无损证明。
本轮远端 usage 合计 **$0.004209072**（不含上节修复前费用），HTTP 延迟约 1.14–3.83 秒。
三条低分/准入的差异不构成质量提升、阈值标定或确定性输出认证。

### 验收脚本自身的失败与范围

- 首次修复后正式运行取得三次有效响应且原三条都落库，但脚本按“全表恰好三条”等待；
  正常后台又发现了 40 条内容，导致脚本整体超时。被动出处检查阻止了这些范围外候选的付费请求；
  该次整体 `passed=false` 保留，不改成全流程通过。表中正常窗口只引用已核实的原三条结果。
- 随后修正为按验收 cohort 等待，并通过现有 B 站专用代理配置阻止隔离环境无关站点抓取。
  收紧/停放/同配置三个阶段通过；容量保存阶段超过脚本 8 秒读取期限，原报告仍为 `passed=false`。
- 最后单独恢复原有停放数据库，**未重放前三阶段的付费评估**。延长验收客户端读取期限后，
  先证实重启仍 blocked=3，再通过正式 `PUT /api/config` 保存放宽容量、等实际 applied，
  自动恢复相同三条并确认 blocked=0；独立 `recovery-report.json` 为 `passed=true`。

私密完整现场位于 `/tmp/obc-jev-fixed-20261027.oAOS3x` 与
`/tmp/obc-jev-recovery-20261027.bHEfur`，未提交；文件扫描未发现 OpenRouter Token。
源画像文件 hash 前后一致，源数据库只读，临时服务已结束。未验证真实安装版插件 MV3 操作、
日常部署、推荐表达 LLM 或导演编排质量；没有为了让测试入池降低 0.60 阈值。
双字节护栏仍是配置容量的保守工程映射，不是精确 tokenizer 或官方窗口上限探测。
