# Jev 设置说明精简（2026-11-07）

基于 `9a0a2944`，仅调整桌面 Web、插件和移动 Web 的文案与折叠展示，不改变模型、配置字段、评估路由或 CLI。

## 用户说明

> 选择 Jev 后，只有候选内容评估改用 Jev；聊天、画像等仍沿用原来的 LLM 配置。

服务开关注明“不切换评估器”。模式限制与地址 / Token 说明默认折叠；数据发送、测试计费及后端失败原因仍可见。
简体、繁体、英文目录同步。文案修改阶段未部署日常服务、未调用真实付费模型；后续真实请求验收见下文。

## 验证记录

- `pytest tests/test_i18n.py tests/test_jev_evaluator_wiring.py tests/test_jev_evaluation_config.py tests/test_jev_settings_api.py tests/test_jev_probe.py -q`：首次 113 passed / 2 failed。英文目录排序已修正；环境变量说明已由三端重复状态行改为共享表单展示，相应静态断言同步。两处失败及同组目录检查复跑 3 passed。
- `pytest tests/test_jev_connection_form.py tests/test_jev_settings_popup.py tests/test_jev_settings_mobile.py tests/test_desktop_web_settings_save_e2e.py tests/test_desktop_web_multimodal_settings.py -q`：首次 32 passed / 2 failed。Jev 重载测试原本要求长说明始终可见，已改为检查默认隐藏、展开后可见，单独复跑 1 passed。
- 另一项为 `test_external_runtime_config_event_rehydrates_settings`：一次未在 3 秒内刷新通用平台占比；本分支和原 `9a0a2944` 分支单独复跑各 1 passed。未修改通用刷新实现，也不将一次复跑成功视为消除了该测试的间歇性风险。
- 共享表单覆盖 375 / 768 / 1024 / 1440 宽度与明暗主题、Token 目标绑定、草稿测试状态失效；新增连接启用后仍保持原评估器、不会产生 LLM 配置更新，以及键盘展开 / 收起说明的检查。
- 插件 `npm run build`、`npm run build:bundle`、`npm run verify:assets` 通过；i18n 运行时 10 项通过。共享脚本和英文目录的构建副本与源文件一致。
- i18n 目录检查通过（2931 条源文），修改测试的 Ruff 检查 / 格式检查、`git diff --check` 通过。

## 页面复核

从正式 FastAPI factory 启动无真实密钥、禁用调度与来源的隔离实例，实际打开桌面 Web 和已加载的 MV3 插件。
路径：Web 左侧“设置”，或插件右上角设置按钮 → “高级功能” → “候选评分模式”。
检查一句话说明、连接开关、折叠项以及 URL / 模型 / Token 字段；没有点击付费测试按钮或写入日常配置。
本地截图位于本分支 `output/playwright/jev-settings-location-pc.png` 和 `jev-settings-location-extension.png`。
临时服务和浏览器已关闭；截图不含 Token。未重新认证模型质量、实体手机或完整安装流程。

## 当前提交真实请求复验：部分通过，保留失败

测试代码为 `b219d49c2d30facb06789d6f0e37e76e3f9959e8`。使用正式 FastAPI factory、独立数据库 / 配置、真实 Chromium 和实际加载的 MV3 插件，未替换业务 evaluator 或远端响应。沿用用户授权的 OpenRouter 凭据，固定模型 `typesafe/jev-1.14-20261014`，原生接口 `https://openrouter.ai/api/v1/systemone`。日常服务与主工作区未变。

### 已通过范围

| 入口 / 场景 | 真实结果 |
| --- | --- |
| PC Web 只启用连接并保存 | 评估器仍为 `llm`；必须手动选择 `jev` 才切换。两次保存均不改变实际生效的默认 LLM 配置 |
| PC Web 64K / 32K | 草稿探测 1 请求 / 8 题通过且不保存；5 条冻结真实视频记录＋1 条合成长文，经正式候选入队 / refresh / consumer 完成 5 请求 / 48 题 |
| 实际 MV3 48K / 24K | 直接填写完整原生 URL，探测 1 请求 / 8 题通过；保存应用后，1 条真实记录＋同一长文完成 2 请求 / 16 题；Token 不在扩展 storage 中 |
| 移动 Web 12K / 12K | 保存并应用成功；长文无法容纳必需输入时，以 `input_budget_exhausted` 停放，真实请求数、评估尝试数均为 0 |
| 移动 Web 相同配置再次保存 | 仍停放，未产生重复 Jev 请求 |

每次成功保存都检查生效模型实例（含 URL / Token / 模型参数）、默认链、四个模块路由、Embedding、并发和超时与基线一致。Jev Token 保存后输入框清空，配置读取接口即使带 `reveal_keys=true` 也不返回 Token。没有执行真实聊天 / 画像生成，不能将配置不变解释成这些功能重新完成了联网验收。

容量为本轮显式填写的总请求 / state＋单题 token 上限；当前保守估计对应同数值字节护栏，不代表精确 tokenizer 或 token 已用满。PC 最大 wire 请求 42,256 bytes、最大 state＋单题 31,885 bytes；插件分别为 31,281 / 23,887 bytes。长文从 454 段选择 94 段（32K）或 53 段（24K），均标记 `is_partial=true`；原句来源、条件邻句、必需画像保持，兴趣来自原画像且非空。此为按预算摘录，不是无损压缩。冻结画像源文件摘要未变。

8 次候选评估均形成完整时效证据，最终全部按原门槛低分拒绝；没有调低准入阈值，也未认证推荐质量或正向出卡。

### 未通过与未执行

移动 Web 将草稿改为 56K / 28K 后，点击“测试连接与评估”收到远端 HTTP 200、匹配模型和 8 个答案，但生产 adapter 返回 `INVALID / invalid_response`，页面探测结果为 `ok=false`。探测没有保存草稿，没有自动重试。

该请求为 13,633 bytes，state＋单题最大 7,164 bytes；上游 usage 为 input 4,114 / output 563 tokens，未达到配置的容量上限。定位到 `JevDecisionProvider` 的 JSON / `parse_jev_response` 校验入口，不是本地输入容量不足，也尚未到联合时效证据判定。此次仅记录脱敏数量、选项和用量，未保留完整响应，因此无法确认具体失败字段，更不能据此认定是上游答案错误还是本地兼容性问题。

测试在此停止：**本轮调大容量后的真实恢复、重启持久化没有执行，整体不能标记全通过**。此前提交的成功记录不替代本轮失败。后续需在不泄露输入和凭据的前提下采集具体校验原因，再决定是否修改兼容处理；不得为过测放宽证据校验或重复请求直到偶然成功。

### 测试脚本诊断与费用

- 首次尝试在 PC 探测通过后，新增的原始 `[llm]` 字典相等断言失败。两轮不联网复核确认：第一次从设置页保存会将旧 provider 表转换为路由 v2；只是存储格式迁移，实际模型 / 凭据 / 路由不变。调整验收口径为比较正式 `effective_llm_*` 投影，并用修改模型的负对照确认仍能检出模型变更。未修改产品实现。
- 独立搜索词生成仍会尝试原 LLM；隔离配置将其指向不可用的本机端口，因此有非 Jev 后台连接失败。它们不构成候选评估回退，也不应加入 Jev 重评计数或断言整个应用没有任何 LLM 调用。搜索抓取与聊天不在本轮成功范围。
- 包含首次中止的探测，本轮共 **11 次真实 Jev HTTP 请求 / 96 题**，均返回 HTTP 200，其中一次探测未通过协议校验。上游返回的 usage cost 合计 **$0.004626678**；不是独立账单审计。没有为失败追加付费请求。
- 相关回归：`pytest tests/test_jev_probe.py tests/test_jev_evaluator_wiring.py tests/test_jev_evaluation_config.py -q`：**57 passed**，104 条既有 FastAPI `on_event` 弃用警告，51.10 秒。共享脚本 / 英文目录构建副本 `cmp`、`git diff --check` 通过。

脱敏原始证据：首次中止 `/tmp/obc-jev-copy-live-20261107.FMZol0/report.json`；语义比较离线复核 `/tmp/obc-jev-copy-offline-20261107.DRnpsy/report.json`；主复验 `/tmp/obc-jev-copy-verified-20261107.kI6xxP/report.json` 和 `trace.jsonl`。不要将这些目录中的私有画像 / 原文副本提交到仓库。新截图为本分支 `output/playwright/jev-copy-live-desktop.png`、`jev-copy-live-mv3.png`；移动失败发生在截图步骤之前。

所有本轮临时服务和浏览器均已停止；临时配置、配置备份和持有 Token 的浏览器 profile 已删除，日常凭据未写入或改动。仅测试源代码 worktree 的正式应用入口，不是日常实例部署、商店更新、Docker / 安装器验收或实体手机验收。

## 合回 main 验证与收尾

按用户明确的 commit / merge main / push / 清理要求，将 `aa8c4d55` 及其 9 个 Jev 前序提交整合到 `e080fbe3`。使用独立 `merge/jev-evaluator-main` worktree，不在主工作区编码。7 个冲突文件按双方意图合并：API / runtime 文档保留免费方案与 Jev 两部分；三语言词条取并集；手机同时挂载免费模型与 Jev 的独立测试入口；Jev 重载回归同时等待持久化状态和异步值回填。未放宽鉴权、答案校验或覆盖 main 的新模型路由。

合并结果的验证（无新付费请求）：

- 20 个 Jev / decision / 候选存储 / 画像 / 元信息 / i18n / 本机访问专项文件：477 passed。
- `test_sponsored_settings_api.py`、`test_sponsored_user_flow.py`、`test_sponsored_renewal.py`、`test_profile_views.py`：64 passed。
- `test_cli.py -k config_show`：6 passed / 269 deselected。
- `test_discovery_candidate_pipeline.py`、`test_candidate_pipeline_admission.py`、`test_profile_views_guards.py`：85 passed。最初误写不存在的 `test_candidate_pipeline.py`，该次未收集任何测试；改用实际文件名后运行通过。
- 共享 Jev 表单、移动页、插件页、正式进程保存 / 热重载 / 候选入队 / 重启回归：15 passed；推理由本地 fake Jev 提供，不冒充真实模型。
- `test_sponsored_runtime_e2e.py::test_popup_and_mobile_share_saved_model_with_real_backend`：1 passed，保留 main 的免费模型手机 / 插件入口；模型为 synthetic IPC peer。
- 合计 648 项 Python 检查通过；不是全仓全量 pytest。FastAPI `on_event` 弃用警告仍存在。
- `ruff check src/ tests/`、`mypy src/`（392 个源文件）通过；UI 目录检查 3000 条通过。
- Chrome 插件完整构建 / 22 项资源 preflight 通过，插件测试 1884 passed；移动反代路径 JS 回归 4 passed。

这些结果只证明当前合并兼容性。上文真实 OpenRouter 的 `invalid_response` 仍保留为未解决项，不能据此宣称已修复；没有新增真实恢复 / 重启认证、版本发布或日常服务重启。

清理前检查旧 worktree 没有未提交源码，配置 / 数据库无打开句柄。五个 Jev worktree 的本地配置、数据库、日志及截图已备份到权限受限的本机归档目录，并逐文件校验内容摘要；缓存和可重建的插件产物不作为源代码保存。归档未进入 Git。主工作区原有 `outputs/`、`promo/` 以及无关 worktree 保持原样。历史截图路径在 worktree 删除后请从本机归档恢复；源码与本报告由 main 的提交历史保留。

收尾时再次检查，上述 `/tmp/obc-jev-copy-verified-20261107.kI6xxP` 已不在本机临时目录中，无法追加归档其原始报告与 trace；没有据此更改历史测试结论或补造原始响应。后续排查需重新采集脱敏校验原因，不能依赖该临时路径。
