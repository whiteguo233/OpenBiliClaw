# Jev 完整评估的单轮上下文适配

## 已确认的范围

本次只完善 `discovery.eval_scorer="jev"` 的完整候选评估，不扩展推荐导演或
独立 semantic prefilter。保留用户选择的精确模型，不自动切换，不额外调用摘要
LLM。源画像和候选原文完整保留；输入允许使用可追溯的按需画像和原文摘录，
不承诺摘录后的判断与全文一次输入完全等价。

一条候选的完整题组属于同一个请求。多个候选可以合批，问题并行求值；不能为
适配上下文而拆题多轮求值或分段反复评估同一候选。批次超限时优先减少候选数，
不为凑大批删除关键证据。最小必需信息仍装不下时留为待评估，不伪造零分淘汰。
传输层有限重试与业务多轮评估不同；返回的 usage 不代表失败尝试的完整账单。

## 官方能力与约束

以下事实核实于 2026-10-13。

`POST /v1/systemone` 接受 `model`、`state` 和 `questions`。所有问题共享 state，
彼此独立、并行；一道题的答案不会成为另一题的输入。question ID 仅用于结果
关联，不送入底层模型，所以 instructions 必须明确目标候选和画像路径。
[API](https://docs.typesafe.ai/api) 与 [State](https://docs.typesafe.ai/concepts/state)。

| 限制 | 官方 Jev 1.13 |
| --- | --- |
| 整批输入 | state 加全部 questions 不超过 64k tokens |
| 单题窗口 | 整份 state 加最长 question 不超过 32k tokens |
| Choice | 每题最多 255 个选项 |
| Score | 最多 10 档，至少使用 2 档 |
| question 总数和独立长度 | 未公布独立最大值，仍受组合窗口约束 |
| 输入模态 | 文字和 JSON，不支持原生图像、音频、视频 |

容量来自 [Models](https://docs.typesafe.ai/models)，题型限制来自
[API](https://docs.typesafe.ai/api)。本项目可设置更小运行预算和题数/题长工程护栏，
但不能靠配置扩大官方服务的真实容量。其它精确模型 ID 必须显式登记容量。
公开模型列表不提供可直接用于自动容量发现的字段；未见官方 tokenizer 或
count-tokens 接口。[OpenAPI](https://api.typesafe.ai/openapi.json)。

共享 state 在一个请求中只摄入一次，可以减少重复输入费用。官方 13 题示例是
同一文档、Jev 1.12 的实验，不能把它的节省比例或答案一致性推广为“增加不同
候选仍不影响结果”的保证。[Parallel questions](https://docs.typesafe.ai/cookbooks/parallel_questions)。

官方支持结构化 instructions 和明确的嵌套路径引用；并行结果由代码组合。
路径约束不是候选之间的硬隔离，无关 state 过多可能降低准确率。
[Advanced structure](https://docs.typesafe.ai/primitives/advanced)、
[Fan-out](https://docs.typesafe.ai/patterns/fan-out)、
[已知局限](https://docs.typesafe.ai/model-jaggedness/jev-1.13)。

## 实现契约

1. 配置解析集中维护双容量和工程护栏，不把 UTF-8 字节计量标为精确 token 数。
   请求快照冻结容量与打包策略；配置重载后的新任务使用新设置，不混合新旧结果。
2. 本地整理保留显式负偏好、限定条件、负例和必要来源语境。画像只通过
   `soul/profile_views.py` 的具名视图选择，不新增调用端画像序列化分叉。
3. 单条内容先去重、避免正文在各题反复出现。只有单条请求超限时才按完整原文
   片段选取证据，保留必要的邻接条件、否定和转折；不可安全拆开的片段不截字。
   摘录标识、源摘要和覆盖情况必须可追溯，不能声称没有遗漏语义。
4. 每条候选完整题组作为不可拆的合批单元。所有批次都验证完整请求及最长单题
   窗口，题型选项和人工题数/题长护栏。合批数量同时受质量保护上限约束，不能
   只追求填满窗口。候选选择不依赖同批同行，避免批次变化改写缓存身份。
5. 返回结果必须通过模型、schema、输入摘要、完整题集及原文证据校验后才能
   入池和缓存。超限、缺题、证据冲突、不可确定的压缩结果保留待评估。失败不能
   调用生成式 LLM 补全。容量、词表或压缩策略变化使相关旧缓存失效。

## 验收范围

测试入口覆盖配置保存/读取、`JevContentEvaluator.evaluate()` 和原生 HTTP
请求到候选入池的端到端链路；不增加面向私有函数的测试。

需要覆盖中文长内容、短内容原样通过、否定及例外保留、超长单段待评估、
显式禁忌不可删除、来源不变、每候选只有一套完整题、合批与单条上下文一致、
不同配置预算和模型隔离、题数和题长护栏、无隐式 LLM 调用、失败不入池/缓存。

模拟 HTTP 只能证明协议和链路行为。真实 Jev 验收需要本机环境变量凭据，使用
独立测试数据和临时数据库，不改生产配置或候选池。需分别记录请求是否成功、
实际输入 token、延迟、中文判断和合批串扰；高 confidence 不证明被摘掉的
证据无关，也不能替代质量验收。

## 2026 年 10 月 18 日实现和实测入口

实现位于 `feat/jev-single-pass-context`，基于原有按需画像分支；尚未合并 main。
原生输入合同升为 `jev-full-evaluation-v3`，源片段策略为 `jev-source-spans-v1`，
使旧缓存失效。`candidate_text_view()` 保存原句和源字段/字符位置，按完整片段选择，
不会把摘录写回源正文。公共规则、相同负偏好/场景只传一次。

2026-10-18 官方模型文档新增 Jev 1.14.0，默认仍是 64k/32k；本分支增加该精确 ID
登记，不改变选定模型。官方现在明确无独立题数上限。实验长上下文 header 可扩大
窗口，但会更慢且影响准确率，本实现不发送它，亦不允许仅改数字扩大官方标准容量。
[Models](https://docs.typesafe.ai/models)。

免费额度核查：官方协议支持酌情发放 Promotional Credits，未公开保证新注册赠送
固定额度；Playground 需登录。当前进程和项目配置没有可用 Jev key，无法完成原生
真实请求验证。未注册账号、申请额度或充值。
[官方协议 8.2](https://typesafe.ai/legal/mca)、
[官方余额说明](https://help.typesafe.ai/articles/5505198974-what-s-a-402-error-and-how-can-i-add-more-credits)。

本地预览运行：

```bash
PYTHONPATH=src python scripts/validate_jev_single_pass.py
```

真实运行前在官方控制台确认所在组织的免费余额、有效期和自动充值关闭，再仅在本机
设置 `TYPESAFE_API_KEY`。运行：

```bash
PYTHONPATH=src python scripts/validate_jev_single_pass.py --live --confirm-free-credits
```

脚本只用合成画像/候选和临时 SQLite；比较标准与更小运行预算，每条候选每个测试场景
只属于一个请求，总计最多 6 次 POST，transport 不自动重试，第一次 provider 失败后
停止远端调用。输出安全计数、原文摘要/覆盖、实际模型和 usage；缺 key 或未确认免费
余额时返回 `not_attempted`，不发送网络请求。`preview` 的 UNAVAILABLE 是主动阻止
远端调用，不代表真实服务失败。只有两组真实链路完整完成、原文保留且无 pending 才
报告 `passed`，仍需人工判断中文评分与误拒。

验证记录：2026-10-18，Jev / decision / director / profile 相关回归 1016 项通过，
`test_cli.py -k config_show` 6 项通过；全量 `ruff check src/ tests/` 和 `mypy src/`
通过（322 个源文件）。另补压缩结果不随请求内 ref 长度变化、重复句按原始位置
保留实际邻句的公共入口回归；最终评估/画像/HTTP 核心 83 项复验通过。
本地 runner 两种预算均生成完整题组，源正文保留；显式 live 命令因缺少本机 key
返回 `not_attempted`，没有进行远端推理。未运行整个仓库的 pytest、浏览器交互或
桌面/安装器测试；三端 UI 继续沿用既有评估模式切换，新增细粒度字段仅 TOML/API。

## 2026 年 10 月 19 日：本机 Jev 假后端实测

按用户要求在独立 `test/jev-fake-backend` worktree，基于 `b2d63a93` 增加测试工具。
生产 `src/` 和官方端点均不修改，不把假服务注册成可选生产 provider。
TDD 的测试边界是原生 HTTP 客户端与候选 claim / evaluate / complete：先测失败，
再实现真正监听回环端口的服务，最后沿公共数据库查询接口验证持久状态。

`scripts/jev_fake_backend.py` 用标准库 HTTP server，不增加依赖；监听地址固定为
`127.0.0.1`，端口由系统分配。严格测试 transport 只接受官方原生地址与合成 key，
再用实际 socket HTTP 转到本地；不使用 MockTransport，不允许真实 key 或其它目的地。
服务不调用生产响应 parser 或预算计量器来生成预期结果，独立检查原生 envelope、
Score 2–10 档和 Choice ≤255，并分别计量完整请求、state＋最长题的 canonical JSON
字节数。容量是测试工程护栏，不是官方 token 计数实现。

可重复运行：

```bash
PYTHONPATH=src python scripts/validate_jev_single_pass.py --fake
PYTHONPATH=src python scripts/validate_jev_single_pass.py --fake --model jev-1.14.0
PYTHONPATH=src pytest -q tests/test_jev_fake_backend.py
```

两组临时 SQLite、合成画像/候选，Jev 1.13 演示结果：

| 场景 | 完整评估 / 入池 | 实际 HTTP | 长文已选片段 | 长文 state＋最长题字节 |
| --- | --- | --- | --- | --- |
| 标准预算 | 3 / 3，pending=0 | 2 次，20 题＋10 题 | 108 / 321 | 23,899 ≤24,000 |
| 收紧至 12,000 字节 | 3 / 3，pending=0 | 2 次，20 题＋10 题 | 43 / 321 | 11,892 ≤12,000 |

每个场景中每条候选只有一次完整 10 题评估；同一合成数据在两个隔离场景重复是测试
对比，不是生产多轮评估。全量原文均保留，返回答案和 `usage=123/10` 固定为合成
fixture。报告显式标记 `backend=local_fake`、`live=false`、`usage_is_synthetic=true`。

自动化覆盖：原生三种 primitive、1.13/1.14 精确模型与 12KB/18KB 工程预算、长中文
和显式条件保留、完整负偏好、一次完整题组、低分拒绝；缺题、多题、错模型、非法选项、
概率分布错误、rubric 被改、HTTP 503、远端 422；不可拆正文、单题长度、必需画像
超限时零 HTTP。技术失败留 pending，不缓存失败、不调生成/视觉 LLM；合法低分
则进入 `rejected_low_score`。测试均使用临时数据库，不触碰真实用户信息。

验收边界：证明本机协议、预算配置/打包和数据库状态流转，不证明真实模型中文效果、
摘录语义无损、官方 tokenizer 上限、服务端错误正文形状、费用或线上时延。没有调用
官方 API、读取真实 key、改生产配置或运行浏览器/安装器测试；运行时架构图不变。

最终验证（本机 `.venv`，`PYTHONPATH=src`）：

- 新增回环 HTTP 验收 18 项，纳入
  `pytest -q tests/test_jev* tests/test_decision* tests/test_on_demand_profile_views.py tests/test_profile_views.py tests/test_profile_views_guards.py`：**420 passed**。
  首轮为 419 passed / 1 failed，唯一失败是未修改的
  `test_cli_per_call_transport_closed_on_exception_timeout_and_cancel[raise]`
  （10ms 真实时钟预算）；该参数组隔离复验 3 passed，整组复跑 420 passed。
  记录为时序敏感现象，不隐藏首轮失败，不据此改动生产 runtime。
- `ruff check src/ tests/ scripts/jev_fake_backend.py scripts/validate_jev_single_pass.py` 通过。
- `MYPYPATH=src:. mypy --explicit-package-bases src/ scripts/jev_fake_backend.py scripts/validate_jev_single_pass.py`：324 个文件通过。
- `--fake` 的 1.13/1.14 两个显式模型均 `passed`，各 4 次本机 POST；默认模式仍
  `preview`，去掉本机 key 的 `--live --confirm-free-credits` 返回 `not_attempted`
  和退出码 2，没有远端 HTTP。
- 没有运行全仓 pytest 或涉及真实 OAuth 凭据探测的 CLI 测试。

## 2026 年 10 月 20 日：三端 Jev 连接配置闭环

独立 `feat/jev-settings-ui` worktree，基于已验收假后端分支 `3a3c65ef`，尚未合入 main。
桌面 Web/插件「设置 → 高级功能 → Jev 连接配置」，移动设置同名区，共用
`web/shared/jev-settings.js/.css`，构建按既有机制复制到插件。UI 采用既有样式、可访问
标签/密码框/状态提示/单列窄屏布局，基本连接字段与折叠容量/请求预算分开。

- 配置服务开关、完整原生 API URL、Token、固定模型、双 token 上限、每批候选数、超时/重试，
  与 `eval_scorer=jev` 可同次保存。Jev 是替换完整评估，不是又叠一层语义预过滤。
- `PUT /api/config` 严格部分更新 `decision.providers.jev`，验证后沿原 apply revision
  后台重建；202 只表示已保存/排队。本机保存 Token 优先，空值沿用 env；省略/空串不覆盖，
  显式 `clear_api_key` 清除。Token 不进 GET/错误响应/repr/CLI/浏览器缓存，保存后输入框清空。
  本机 TOML 未加密，POSIX 写入权限 0600；配置备份/迁移可能含 Token，清除不擦历史备份。
- URL 默认官方接口；自建/中转必须兼容 native systemone 协议。外部仅 HTTPS，回环测试可 HTTP，
  禁止 userinfo/query/fragment、重定向与自动沿用到新目的地的旧 Token；回环禁环境代理。
- 更换模型 ID 不继承上一模型容量；容量完整表可恢复自动，并保留其它型号登记，不自动选模型。
  老后端缺少新凭据状态字段时禁止连接编辑，只保留原有模式切换，避免旧 API 忽略密钥写入却报成功。

验证与明确边界：

- TDD 新增 20 项验收（配置 API 16，移动/插件各 1，桌面 2）；最终
  `pytest -q tests/test_jev_evaluation_config.py tests/test_jev_settings_api.py tests/test_jev_settings_mobile.py tests/test_jev_settings_popup.py tests/test_desktop_web_settings_save_e2e.py -k 'jev or switching_jev' --tb=short`：**56 passed**。
  覆盖同次保存/刷新、Token 保留/清除/脱敏、坏 URL/字段/容量不落盘、旧后端保护、跨型号容量隔离。
- 扩展相关回归命令
  `pytest -q tests/test_jev* tests/test_decision* tests/test_api_config* tests/test_config.py tests/test_desktop_web_settings_save_e2e.py tests/test_mobile_web_probe_delight_e2e.py --tb=short`
  为 **838 passed / 1 failed**；唯一失败是容量未知提示丢失“精确模型 ID”的断言，已修复提示。
  修复后 `test_jev_evaluation_config.py` 34 项及上述最终 56 项复验通过；不把重叠组相加。
- `test_cli.py -k config_show`：6 passed；插件 Node settings/layout：58 passed；
  `npm run build:bundle`、`npm run verify:assets`、全仓 Ruff、MyPy（322 源文件）及 JS syntax 检查通过。
- 从 API 保存的配置构建实际 client，对本机 fake 的**显式 URL**发出一次完整 10 题请求并成功；
  没有 URL 重写桥，错误环境 key/HTTP_PROXY 不会覆盖保存的 Token 或回环直连策略。
- Web/移动/插件页面资产均在真实 Chromium 运行；移动/插件调用真实 FastAPI 配置逻辑，
  Chrome API 与底层 transport 为测试边界，runtime 后台重建在配置测试中用 stub 防止模型后台请求。
  不是实际 MV3 安装/host permission、完整 daemon 生命周期或官方 Jev 质量验收。
  Playwright CLI 的本机可视核对截图在 `output/playwright/`；简单 fixture 缺失非本功能路由的
  404/流重连日志不能当作产品端到端健康证明。
- 没有运行全仓 pytest、官方付费探测、真实来源账号操作或安装器测试；未改生产配置/数据库。
  无新依赖/安装流程变更，未修改外部 Flutter 客户端。README/架构/spec/模块/配置/CLI 文档同步。

## 2026-10-21 真实 API 进程与热重载验收

在独立 `test/jev-runtime-e2e` worktree（基于 `64901843`）补足上一轮 stub 掉的
runtime 重建；未合入 main 或部署到日常服务。本机项目配置及所选环境变量均没有
Jev Token，所以**没有向官方 Jev 发出真实推理请求**，不认定官方容量/质量已验收。

新增 `tests/test_jev_runtime_e2e.py` 启动原生产 `create_app` factory 的独立 uvicorn
进程。真实 Chromium 操作桌面设置页；HTTP config API 写临时 TOML，等待真实
apply revision 成功，再通过实际来源 ingest API 投递合成候选。运行真实候选
coordinator、Jev adapter 与 SQLite 准入，不替换 runtime/provider/config 内部逻辑。
只有外侧 Jev 使用本机合成协议服务；通用 LLM/embedding 指向不可用回环地址，
未传入真实服务凭据、操作来源账号或修改生产配置/画像/候选池。测试进程结束后回收。
该测试不是安装到浏览器中的 MV3 插件验收，也不认证通用 LLM 健康、推荐文案生成或
最终卡片展示链路；另外两端的设置回归仍沿用上一轮的边界。

本轮发现并修复：

- Jev 原本复用了普通 LLM 的批输入投影，`description` 在预算摘录前就被截到
  400 字。实际来源入口保留原件，但 Jev 看不到尾部条件。现在仅 Jev 使用完整描述，
  交由 `jev_context` 按所选模型预算摘录；普通 LLM 的 400 字兼容行为不变。
- 缓存身份同样绑定完整描述。新增回环 HTTP 回归证明：同一原件重用结果，而只改变
  400 字之后的适用条件必须重新请求；旧投影复现为仅 1 请求，修复后正确为 2 请求。

真实进程最终连续两次通过（8.32 秒、7.57 秒，计入正常测试组的另一次亦通过）：

- 页面保存 URL、Token、固定 `jev-1.14.0`、64k/16k 双容量及最大尝试 1；
  实际热重载 `applied`，刷新仍为 Jev，GET（含 reveal_keys）及密码框不回显 Token。
- 3 条候选（含 350 段长描述）全部准入，待评估为 0，原始描述不变。
- 2 次真实回环 POST，各 20 / 10 题；每条候选完整 10 题，既不拆评估也不重复候选。
- 请求包分别 24,056 / 20,642 字节；state＋最长单题分别 6,500 / 11,894 字节，
  均低于所配 12,000 字节护栏。长文出现明确 partial coverage；中后部条件和完整
  避雷保留。这里是**工程字节护栏，不是官方 tokenizer 实测**。
- 可视截图：本 worktree 的 `output/playwright/jev-runtime-settings.png`（忽略文件）。

最终验证共 **458 项不重叠测试通过**，重复实跑不额外计数：

```text
pytest -q tests/test_jev_fake_backend.py tests/test_jev_runtime_e2e.py --tb=short
  20 passed
pytest -q tests/test_jev_evaluator_wiring.py tests/test_discovery_engine.py
  tests/test_discovery_candidate_pipeline.py tests/test_discovery_multimodal.py --tb=short
  230 passed
pytest -q tests/test_jev_full_evaluator.py tests/test_jev_on_demand_profile.py
  tests/test_jev_evaluator_http_pipeline.py tests/test_jev_context_budget.py
  tests/test_jev_provider.py --tb=short
  152 passed
pytest -q tests/test_jev_evaluation_config.py tests/test_jev_settings_api.py
  tests/test_jev_settings_mobile.py tests/test_jev_settings_popup.py
  tests/test_desktop_web_settings_save_e2e.py -k 'jev or switching_jev' --tb=short
  56 passed, 9 deselected（既有 FastAPI on_event 弃用警告）
```

全部命令使用共享 `.venv`，并在该 worktree 设 `PYTHONPATH=src`。全仓 Ruff、
MyPy（322 源文件）、修改文件的 format check 与 `git diff --check` 通过；未跑全仓
pytest。无新依赖、配置字段、CLI、安装或对外协议变更；模块文档、changelog 和
四处架构数据路径已同步。

## 2026-10-22 配置驱动的验收入口与重启验证

独立 `test/jev-configured-acceptance` worktree，基于 `283eb93c`。本机正式配置
仍未启用 Jev、没有保存 Token，所选环境变量也无 key；**本轮没有官方真实推理请求**。
真实项目 TOML 的显式 preview 成功，实请求未带额度确认时返回 `not_attempted`，零 HTTP。
未改正式配置/画像/候选池、main、发布或日常运行服务。

验收入口收口：

- `validate_jev_single_pass.py --config <file>` 只读该文件，读取保存的原生 URL、
  Token/命名环境凭据、精确模型及上下文容量/评估参数。不把其它 TOML/环境覆盖层并入模型
  选择；`--model` 若与配置不同直接拒绝，不自动切换模型。未知模型必须显式登记双容量。
- 默认 preview 不构造客户端或解析 Token 环境值；`--fake` 禁止与配置同用。
  live 仍要求先自行确认可用促销额度，缺 key/关闭连接/无容量/冲突设置都拒绝发请求。
- 配置路径经原生产 `build_decision_provider`，保存 Token 优先、空值才用所命名 env。
  脚本把尝试数收紧为 1，超时不超过保存值和 15 秒；全次最多 6 POST，首次远端失败
  即不再发请求。不打开配置里的生产 data_dir，也不更改文件或把 env 密钥保存下来。
- 同一 runner 的两种预算各用独立合成三候选与临时 SQLite，用于验证预算差异；
  不改变生产每候选单次完整评估的约定。所配服务报告 `backend=configured`、
  `usage_is_synthetic=null`：成功响应不等于认证某中转/自建服务的模型和用量真实性。
  完整用法见 [CLI 开发验证](../modules/cli.md#jev-本机开发验证脚本)。

新增覆盖：

- CLI 子进程→保存配置→真实回环 HTTP→原生评估→临时 SQLite：自定义小窗口模型，
  请求容量按登记值；保存 Token 胜过错误默认 env。单独验证命名 env 的无保存 Token 路径。
- 缺额度确认、连接关闭、缺 key、模型冲突、未知容量、fake/config 冲突均无请求且不泄密；
  所配服务 503 时只请求一次、无准入、3 条保持 pending，不使用文件中的 3 次重试。
- 实际生产 factory 的进程关闭后，用相同临时配置和数据启动**新的进程**；原始保存的
  自定义模型/URL/Token/双容量仍生效，来源 ingest 触发完整 10 题评估并准入 1 条。
  推理边界仍为本机合成服务；不是官方模型或精确 tokenizer 验收。

验证命令（共享 `.venv`，`PYTHONPATH=src`）：

```text
pytest -q tests/test_jev_acceptance_cli.py tests/test_jev_fake_backend.py
  tests/test_jev_runtime_e2e.py --tb=short
  31 passed
ruff check src/ tests/ scripts/validate_jev_single_pass.py scripts/jev_fake_backend.py
  passed
MYPYPATH=src:. mypy --explicit-package-bases src/
  scripts/validate_jev_single_pass.py scripts/jev_fake_backend.py
  324 source files passed
```

未新增运行依赖、产品配置字段或安装步骤。文档更新限开发 CLI、发现模块、changelog
和本记录；生产架构/接口接线未变。

合入预检：对当时 main `8b99ed46` 做 `git merge-tree --write-tree main HEAD`，
发现 **17 个文件冲突**（没有真正开始 merge，也没有改变任何分支/工作文件）：
README CN/EN、architecture/changelog/index、API/discovery/extension/runtime 模块文档、
两份导演设计文档，以及 API app、CLI、config、recommendation engine、desktop index、
mobile app。整批历史仍包含 16 个未合入 main 的导演/Jev 提交，不只是过滤的一处修复；
在获得合入范围选择前不把这批变更直接落到 main。不得把旧分支通过视作最新版 main 已验收。

## 2026-10-22 main 整合与推送收尾

用户明确要求 merge main、push 与清理。以 main `8b99ed46` 创建独立
`integrate/jev-main-20261022`，合并 `c4d16755` 的完整历史，不覆盖主工作区的
`outputs/`、`promo/` 或本机配置。处理 17 个冲突文件，保留最新主线的来源接入、
聊天流关闭、多语言、输出语言和配置子进程重载；导演/评估默认开关未改变。

整合完成后 main 并发前进到 `0aba8ec2`（国内镜像发布文档更新）。首次本地快进因
未提交文档被 Git 拒绝、未覆盖文件，首次推送也被非快进保护拒绝；待该文档提交后将其
完整合入，六个文档文件自动合并、无源代码变化，再复验安装契约和 i18n。主工作区原有
`outputs/`、`promo/` 保留，不采用强推、reset 或自动 stash。

整合修复：

- API 同时关闭聊天流任务与旧导演 runtime；配置导入同时保留新来源与 Jev 模型。
- 推荐引擎保留 `output_language` 与注入式 `content_evaluator`，普通推荐保持原路由。
- Web 移动设置一次保存语言、保存同步与 Jev 更新，读取成功后按选定语言应用；
  桌面保留 main 的共享聊天/i18n 脚本并加入导演与 Jev 资源。
- Jev CLI 标签走现有语义翻译目录；共用表单及新屏协议提示补齐英文与生成繁体，
  全清单为 2,916 文案 / 210 插值模式。没有翻译 Token、模型 ID、URL 或用户内容。
- 旧 Jev TestClient fixture 改为真正的 loopback peer + Host，匹配 main 的本机配置安全合同；
  没有放松生产鉴权。保留主线发布历史与历史失败证据，当前文档标记已整合、尚未发版。

最终树验证（命令均为共享 `.venv` 与 `PYTHONPATH=src`）：

```text
pytest -q tests/test_director_*.py tests/test_pair_*.py tests/test_decision_*.py
  tests/test_jev_*.py tests/test_semantic_filter.py tests/test_on_demand_profile_views.py
  tests/test_i18n.py --maxfail=10 --tb=short
  1307 passed
pytest -q tests/test_install_contract_docs.py tests/test_docker_runtime.py
  tests/test_openclaw_cli.py tests/test_openclaw_skill.py tests/test_recommendation_engine.py
  tests/test_discovery_engine.py tests/test_discovery_candidate_pipeline.py --maxfail=5 --tb=short
  443 passed
pytest -q tests/test_desktop_web_settings_save_e2e.py tests/test_mobile_web_probe_delight_e2e.py
  tests/test_api_config_*.py tests/test_api_auth.py tests/test_auth_core.py
  tests/test_jev_settings_mobile.py tests/test_jev_settings_popup.py --maxfail=5 --tb=short
  196 passed
pytest -q tests/test_api_config_guards.py tests/test_proc_supervisor.py
  tests/test_worker_status.py tests/test_worker_degraded_boot.py --maxfail=5 --tb=short
  66 passed
pytest -q tests/test_cli.py -k config_show --tb=short
  6 passed
extension: npm test
  1821 passed
extension: npm run typecheck; Chrome / Firefox / Safari build:bundle + verify:assets
  all passed（构建/资产检查，不是已安装 MV3 或真机验收）
ruff check src/ tests/（另含 Jev 验收工具）；mypy src/；build_i18n_catalogs.py --check
  all passed；361 个 Python 源文件；git diff --check passed
```

以上测试组有交集，不把数字相加当作唯一用例总数。早期全仓 pytest 排除了已知会
触碰本机 OAuth 的登录测试，在修正旧测试夹具前启动；约 10% 时停止该旧进程，
**没有全仓完成结果**，最终采用上述相关集，不声称全仓全绿。初期 i18n 目录残留/
排序与旧非本机 fixture 的失败均在最终对应组复验通过；构建开始时的依赖软链路径
错误已纠正后重新完整构建。未调用官方 Jev、操作来源账号、修改真实画像/库存、发布
Release/商店包或重启日常服务。

收尾只删除已合入且干净的本轮临时分支/worktree。截图及临时配置/测试数据先备份到
主工作区忽略的 `output/` 专用归档目录；有未提交研究笔记和本地数据的旧
`feat/jev-full-evaluator` 工作区保留，不强制删除。推荐导演的下一阶段范围见
[实施进度顶部](2026-09-26-director-implementation-progress.md)：图形模式配置、运行状态与
真实 Agent/Jev 联合编排和质量/时延验收；不是从零重做三策略内核。
