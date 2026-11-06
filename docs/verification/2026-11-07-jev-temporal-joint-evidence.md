# Jev 时效矛盾修复与正式隔离复验

日期：2026-11-07。基线 `5723dc1b`，独立分支 `fix/jev-temporal-joint-evidence`。
没有部署或重启日常服务，没有修改源画像／原候选数据库，没有合并或推送。

## 问题与修复

上一轮 5 条唯一真实视频中 4 条因独立时效题互相矛盾而停放，不能以 HTTP 200
代替质量验收。最小回归只选择各题允许的选项，即复现
`Temporal evaluation class/mode pair is invalid`，不需要长画像或真实网络。

根因是独立问题却有隐藏的联合约束。例如模型认为内容依赖迭代工具，另一个问题
认为“没有直接过期声明”，于是组合成不合法的 `versioned / none`。
扩大窗口或多次付费重问不能解决这个合同问题。

最终合同 `jev-full-evaluation-v5-joint-outcomes`：

- 每条候选 **8 题**：相关性、理由、主题、IP、风格、时效结论、代表原句、时效置信度。
- 普通时效结论是 6 类和 2 个 durable hook 变体。时间敏感类默认表示 freshness_only，
  不是“已经过期”。原文摘录只从可见源片段中选取，不产生硬过期断言。
- deadline/event/version 硬状态的类别、范围、状态、原句、时间属于同一选项，
  发送前与返回后都保留原 grounding／原子校验，不放宽条件句、时区或准入规则。
- 超过 255 备选时重新预算显式源视图；必需内容仍不合适就本地停放。
- native INVALID（缺题、非法选项、错模型等）也停放，不交给候选重试反复计费。
  503 等仍是暂态错误。正式 CLI／runtime／probe 的身份包含端点但不含凭据，
  修正网关允许恢复，换 Token、重启或同配置保存不触发重评。
- 三端共享文案及探测结果同步为 8 题；既有题数上限原值保留，合法范围为 0 或 8–640。

首版曾把类别与所有证据句都做笛卡尔组合（7 题）。6 次真实请求虽不再矛盾，
却有 3 条选成 unknown；普通选项描述重复，且占用了更多单题预算。因此没有发布这版。
修正后的方案不重复普通类别，只给硬断言枚举绑定证据；没有把已返回的 unknown 改写为已知。
初版 harness 还错误地要求低分拒绝的 `eval_error` 为空；修正为检查真实终态与
`temporal_evidence_complete`，保留首次报告，没有将低分拒绝算作技术失败。

## 真请求范围与结果

正式 `openbiliclaw.api.app.create_app` / Uvicorn / 真实候选 worker / SQLite，
仅增加不改响应的脱敏观测。复用上一轮冻结的画像副本、5 条真实视频和 1 条
454 段的合成长文。真实网关 `https://openrouter.ai/api/v1/systemone`，固定
`typesafe/jev-1.14-20261014`。传输尝试次数 1；每场景每候选一次评估，跨容量重放
是隔离验收费用，不是线上自动重试。没有模型替换或 LLM 评估兜底。

| 场景 | 真实 HTTP / 题数 | 结果 |
|---|---|---|
| 64,000 / 32,000，5 视频＋长文 | 5 / 48 | 2 候选合批一次 16 题，其余各 8；6 条均为完整有效结果，0 矛盾、0 unknown |
| 同设置草稿探测 | 1 / 8 | 有效结果，约 1.91 秒；配置文件未变 |
| 48,000 / 24,000，2 视频＋长文 | 3 / 24 | 3 条有效，0 矛盾、0 unknown；最长单题包 23,887 bytes |
| 12,000 / 12,000，长文＋真实画像 | 0 / 0 | 必需输入装不下，本地停放，attempts=0，无自动重试 |

最终 **9 次真实请求 / 80 题**有效。默认窗口最长单题包 31,885 bytes；实际
双候选包 42,256 bytes、最长单题包 26,719 bytes。双限制均包含完整 JSON 和题义。
这仍是保守 UTF-8 工程护栏，不是 Jev 精确 tokenizer 或“正好用满 token”。

默认结果：4 个工具／Agent 类视频为 versioned，演出合集为 historical，通用算法长文
为 evergreen；收紧窗口的相应分类一致。全部 9 次候选评估按原相关性门槛低分拒绝，
并未入池。因此本轮证明时效合同和失败处理得到修复，**不证明相关性已校准或推荐效果优秀**。

长文默认保留 94/454 段，24K 保留 53/454 段，均显式标记 partial；生产前备份／
否则不适用的条件句及邻句保留。5 条真实视频的可见源文本在默认窗口全量保留。
所有原文字符范围均可回溯，负偏好／风格／场景必需字段逐项相等，正向兴趣均来自
原目录且非空。源画像哈希未变。不能称为任意长文本“语义无损压缩”。

最终返回用量费用合计约 **$0.004534152**；含未采用的首版，本轮共 15 次真请求、
约 $0.007875966。费用是网关返回的 usage 汇总，不是对未来账单的保证。
所有隔离服务均停止，测试生成的含凭据配置已删除；脱敏计数报告保留在本机私有
`/tmp/obc-jev-joint-real-20261107.Y8PByZ/`，未提交画像、原文或密钥。

## 自动化回归与边界

首轮合并回归有 3 个验收 CLI 子进程在 30 秒 watchdog 处超时；单独重跑也复现了
冷启动超时。栈采样位于 Pydantic 加载、本地 JSON 打包及 SQLite schema 初始化，
不是 Jev 网络等待。外层测试限时改为 150 秒，匹配脚本两场景各最多 60 秒再加
30 秒启动余量；生产请求超时、次数、模型和所有成功断言未放宽。未采用超时那轮
作为通过证据。

扩大回归又发现 400 段条件邻句用例在高负载下耗尽 30 秒本地预算。隔离复现耗时
约 11.30 秒；栈采样显示候选视图反复序列化。将 `candidate_text_view()` 的 greedy
试装大小改为精确累加 canonical JSON 条目（含逗号、计数位宽、布尔长度），
完成后仍有最终实际序列化校验。18KB 样例重复序列化量由约 5.98 MB 降到不足 0.1 MB；
单次视图约 1.04→0.11 秒，完整用例约 11.30→4.98 秒。仅本机观测，不是跨设备性能保证。
3K／12K／18K／24K 的输出 golden 哈希完全一致，13 项源视图／邻句回归通过；
原 5 视频＋长文 × 4 档的 24 组源视图与旧实现逐字节相等。该优化在上述真请求后完成，
没有再向模型重复收费，也没有修改源文本选择政策或生产时间预算。

之后组合回归在 236 项通过后出现浏览器入池请求 5 秒超时；隔离重跑又在正式服务
40 秒启动等待处失败，宿主机当时 load average 从约 178 上升到 691。两轮都不计通过。
另用不依赖网络的确定性回归确认：纯本地打包确实阻塞共享事件循环，排队的回调不能
运行。修复将已快照输入的单候选准备放到线程中顺序执行，保持原截止时间和 payload，
并增加打包期间取消后零 provider 请求的回归；不通过扩大 HTTP 等待掩盖这个问题。

修复后的最终自动化结果：

| 验证 | 结果 |
|---|---|
| Jev／decision／候选队列／配置探测／画像／时效／导演接线扩大回归 | **730 passed**，625.66 秒；432 条 warning，0 失败 |
| 正式 Uvicorn 工厂＋浏览器＋热重载／候选入队／进程重启 | **3 passed**，164.07 秒；Base URL 与完整 endpoint 均通过，Jev 推理用本地协议后端；未放宽原 5 秒 HTTP 等待或 40 秒启动等待 |
| 共享配置表单：4 个宽度 × 2 个主题＋草稿探测交互 | **9 passed**，262.98 秒；375px 深浅色截图人工检查，无横向溢出 |
| `tests/test_cli.py -k config_show` | **6 passed**，264 deselected |
| 扩展构建／完整 JS 测试 | 22 个 manifest/WAR 资源检查通过；**1857 passed**，0 失败 |
| Ruff / MyPy / i18n | 全部通过；MyPy 364 个源文件，2928 条目录无漂移；20 个变更 Python 文件格式检查通过 |

730 项包括新增的事件循环响应、准备时取消保护、source golden 与字节边界测试；
单独跑过的 81 项核心回归不重复计入。上述为本改动相关范围，不冒充全仓库 pytest。
覆盖完整 evaluator、真实 native 回环、正式服务入口、配置表单、候选停放／恢复、
时效严格校验及无 LLM 兜底。最终 XML 位于本机私有
`/tmp/obc-jev-final-regression-20261107.mezTEG/results.xml`，正式进程日志位于
`/tmp/obc-jev-runtime-fixed-20261107.dW49EV/`；测试进程均已退出。

主要复验命令（均使用本工作树的 `PYTHONPATH=src` 与项目 `.venv`）：

```sh
pytest -q tests/test_jev_acceptance_cli.py tests/test_jev_context_budget.py \
  tests/test_jev_evaluation_config.py tests/test_jev_evaluator_http_pipeline.py \
  tests/test_jev_evaluator_wiring.py tests/test_jev_fake_backend.py \
  tests/test_jev_full_evaluator.py tests/test_jev_on_demand_profile.py \
  tests/test_jev_probe.py tests/test_jev_provider.py tests/test_jev_settings_api.py \
  tests/test_jev_settings_mobile.py tests/test_jev_settings_popup.py \
  tests/test_jev_source_context.py tests/test_decision*.py \
  tests/test_discovery_candidate_store.py tests/test_candidate_eval_coordinator.py \
  tests/test_api_config_probe.py tests/test_desktop_web_config_probe.py \
  tests/test_profile_views_guards.py tests/test_i18n.py tests/test_temporal_evaluation.py \
  tests/test_director_runtime.py tests/test_director_runtime_boundary_audit.py
pytest -vv tests/test_jev_runtime_e2e.py
pytest -q tests/test_jev_connection_form.py
pytest -q tests/test_cli.py -k config_show
npm --prefix extension run build
npm --prefix extension test
ruff check src/ tests/
mypy src/
python scripts/build_i18n_catalogs.py --check
```

上轮真实 Desktop／MV3／移动 Web 配置链路证据仍保留；本轮不重做无关部署。
实体 iPhone 仍未获得可运行的 Safari 自动化会话，不计通过。
没有新增依赖、数据库迁移、CLI 命令或安装步骤；未对导演屏编排质量作新增认证。

## 当前提交的三端正式服务与真实请求复验

应用户再次要求，对代码提交 `61d4164f8ed29736954b286a3c7adc51641d8833` 完整复验。
这次真实请求发生在本地打包性能优化与工作线程修复之后，不仅是之前请求的离线等价比较。
正式 app factory、配置 API、候选 worker、SQLite 和模型响应均为真实实现；观测器仅增加
脱敏计量、容量／来源校验和重复请求费用闸门，不替换 evaluator、provider 或模型响应。
仍按用户先前确认的范围运行在隔离实例，不切换、部署或重启日常服务。

- 固定 `typesafe/jev-1.14-20261014`，真实 OpenRouter 原生 endpoint；无自动换模型。
- 保留默认 **30 秒**完整评估预算；传输最多 15 秒、尝试 1 次。本轮真实请求硬上限 12 次。
- 使用冻结的真实画像副本、原 5 条不同视频和 1 条明确标记的 454 段合成压力文本。
- 所有地址、Token、模型与容量均由页面填写后保存，非把已配置成功当作页面验收。

| 配置表面 | 真实草稿探测 | 保存与真实使用 |
|---|---|---|
| 桌面 Web `/web/` | 1 次 / 8 题，2.964 秒，配置文件未变 | Base URL `https://openrouter.ai/api`、64K / 32K 保存 → applied；6 条候选用 5 请求 / 48 题完成，其中一次 2 候选 / 16 题 |
| 实际 MV3 插件 | 1 次 / 8 题，1.771 秒，配置文件未变 | 独立有头 Chromium 真 service worker／`chrome.storage`，没有 Chrome API stub；完整 endpoint、48K / 24K 保存后，1 视频＋长文用 2 请求 / 16 题完成 |
| 移动 Web `/m/` | 1 次 / 8 题，2.156 秒，配置文件未变 | 12K 停放 → 原设置保存仍停放 → 56K / 28K 保存恢复；原长文一次真实请求 / 8 题完成 |

每次保存均验证 PUT 202 → apply-status applied → GET 正确显示 endpoint／固定模型／容量；
`reveal_keys=true` 不回显 Token，表单保存后清空 Token 输入；真实扩展本地存储也不含 Token。
关闭重开移动设置能恢复所选模型。重启正式进程后沿用已保存 Token 和 28K 容量，
没有再次评估或增加远端请求。

| 容量场景 | 最长 state＋单题原生包 | 长文选中片段 | 结果 |
|---|---|---|---|
| 总 64,000 / 单题 32,000 | 31,885 bytes | 94 / 454 | 6 条完整有效结果；双候选整包 42,256 bytes |
| 总 48,000 / 单题 24,000 | 23,887 bytes | 53 / 454 | 2 条完整有效结果 |
| 总 12,000 / 单题 12,000 | 不发送 | 不发送 | 必需信息装不下；`input_budget_exhausted`、两类 attempts 均 0 |
| 相同 12K 配置再次保存 | 不发送 | 不发送 | 原候选保持停放，零请求／零重评 |
| 总 56,000 / 单题 28,000 | 27,887 bytes | 73 / 454 | 原停放长文恢复，一次完整有效评估 |

所有实际请求均通过双字节护栏；这是包含 envelope 的保守 UTF-8 工程限制，不是精确
tokenizer，也不声称精确 token 满载。每条实际候选都保留来自原目录的正向兴趣，
必需画像字段逐项一致；全部摘录与原文字符偏移相等，长文的条件／否定及邻句保留。
长文仍明确标记 partial，不能称任意长文本语义无损压缩；源画像文件哈希前后不变。

本轮 **11 次真实请求 / 96 题**，全部返回有效完整结果；包括 3 次草稿探测和 9 次
候选评估（其中双候选共用一个请求）。9 次候选评估均无矛盾、无 unknown 分类，
但仍按原相关性门槛低分拒绝，未入推荐池。因此结论是当前代码的工程链路与上下文
安全适配通过，**不是相关性标定、正向入池或推荐满意度认证**。
不同容量下重放同一来源是本轮显式验收，不是线上自动补评。

OpenRouter 返回 usage 合计约 **US$0.00507885**，无额外诊断或工具重试付费请求；
该值不是未来账单保证。所有隔离进程已停止，测试生成的 `config.toml` 及两份备份已删除，
临时扩展浏览器资料已删除；日常配置、原画像和原候选数据库未更改。

本机私有计量与日志：`/tmp/obc-jev-current-live-20261107.NiHw4L/report.json`、
`trace.jsonl`、`server.log`。可复查脚本也位于该目录，不含真实 Token。
截图 `output/playwright/jev-current-live-{desktop,mv3,mobile}.png` 均在保存并清空 Token 后拍摄，
已逐张查看。移动 Web 使用 Chromium 390×844 触屏模拟，**不等同于实体 iPhone／Safari**。
本轮未新增 CLI 远端调用，沿用上一节 6 项 `config_show` 回归；未覆盖新源站抓取、
安装器、Docker 全新安装、移动原生壳、封面多模态或推荐导演屏编排质量。
本次只补验收记录，没有修改产品代码，也没有合并或推送。
