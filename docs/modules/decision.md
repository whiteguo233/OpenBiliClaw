# 独立决策 Provider

> `src/openbiliclaw/decision/` 提供 typed decision 的基础契约和 Jev 适配器。
> 默认关闭，已由导演运行时、独立 discovery 语义过滤和可选完整评估器调用。
> 与聊天 LLM 路由和凭据隔离；模型只产出证据，不拥有数据库提交权限。

## 模块边界

```text
导演 / 独立过滤 / 完整候选评估（EVALUATE）/ 显式开发调用
  → DecisionRequest（状态、具名问题、purpose、schema 版本、deadline）
  → DecisionProvider.evaluate
  → Jev HTTP adapter
  → 严格解析 / 请求绑定校验
  → DecisionEvidence（答案或不可用原因、模型 / 输入归因、用量）

runtime ── 共享有界准入、总截止时间、熔断与取消回收
```

导演调用的 `session_context` 包含按需推荐画像快视图、最近三屏真实经历和语义反馈。
pair / audit / repair 使用 `context_budget.py` 的模型绑定双护栏；不再把所有模型都当作
64,000 字节。完整评估共用该预算，独立预过滤保留原来的 24,000 字节上限与合同。
超限有因回退，不静默删掉避雷项。画像／历史卡片不能扩大程序提供的合法选项集合，
详见[导演上下文](director.md)。

Jev 是与生成式聊天 LLM、embedding 分开的 provider，不实现 `LLMProvider.complete()`，
也不加入 `[llm]` 的全局或模块 fallback 链。它返回判断证据，不直接改候选池、曝光历史、
推荐草稿或用户画像。调用方拥有业务阈值、最终约束校验、降级与提交权限。

同一请求中的问题独立求值；第二题不会自动看到第一题的输出。[离线导演内核](director.md)
已按“两张一组”递推编排：在同一个 Choice 中联合选择合法 pair 和软角色，由应用更新已选前缀后再调用下一步，不能把
独立的两个单卡 Choice 当作有条件依赖的二卡选择。模型不持有本项目的跨请求会话状态。

三策略共用 `recommendation/director/policy.py` 的屏目标／证据／角色指令与 `screen_policy`
状态。纯 Jev 从保守 brief 开始：明确意图存在时 `follow_intent`，否则 `browse`，不推断心理状态。
最多 16 个合法 pair 展开为 64 个 pair / role 选项，再加 `none_suitable`，仍是每步一次调用；
角色为 `anchor / complement / exploration / open`，不新增硬配额。混合模式审查／修补同时携带
当前 pair 的 intent / role、剩余草稿与失效引用，以判断对整屏的增量价值。指令内整屏自检没有
新增模型调用；本地 `screen_review` 只检查规范化标题完全重复，不能认证语义质量。调用数、
截止时间和输入字节预算不变；拒选继续走有因回退。独立过滤不使用该编排策略，合同不变。

## 已实现功能

| 能力 | 状态 | 范围 |
|---|---|---|
| Provider-neutral 请求与证据合同 | 已实现 | 区分请求目的、问题 schema、输入摘要与模型归因 |
| Jev Choice / Score / Noul | 已实现 | 原生 typed 问题与答案、严格结果绑定；不解析自由文本回答 |
| 有界 HTTP 请求 | 已实现 | 总 deadline、有限重试、明确错误原因、取消传播 |
| 独立配置 | 已实现 | 默认关闭；本机保存 Token 优先，空值沿用独立环境变量，不借用聊天 key；支持显式原生 URL |
| 三策略导演调用者 | 已实现 | `PairComposer` 逐对决策，混合模式审查／修补；Agent-only 编排不调用 Jev |
| 共享屏目标与角色 | 已实现 | `screen_policy` 区分事实／假设／未知；一次 pair / role 联合选择或保留职责的局部修补，不新增语义质量保证 |
| 独立过滤与运行时 | 已实现 | 独立 shadow / enforce、真实标签审计与延期重审；运行时共享并发闸与熔断 |
| 完整候选评估 | 已实现，实验性 | purpose=`EVALUATE`，纯文本受控分类；不隐式调用 LLM，详见 discovery |
| 固定模型自适应预算 | 已实现 | 精确 ID 的双 token 容量 → 双 UTF-8 工程护栏；模型不自动切换，未知容量必须显式登记；完整评估与导演共用 |
| 完整题组多候选合批 | 已实现，待实测 | 原文片段只传一次，公共规则/相同必需画像共享；配置题数、候选数、单题字节护栏。长内容本地摘录，不增加业务评估轮次 |
| 本机 HTTP 协议假后端 | 开发测试工具 | `scripts/jev_fake_backend.py` 固定合成答案/usage、独立字节护栏和故障注入；不改变生产 adapter 或端点，不模拟模型推理质量 |
| 图形界面设置与服务探测 | 设置已实现，探测未提供 | 三端共用连接表单，URL/Token/固定模型/容量与完整评估可同次保存；Token 不回显，不提供付费探测 |
| 线上质量 / 中文效果 / 时延保证 | 未验证 | 离线 fixture 不等于真实模型验收；本轮未调用付费 API |

## 公共 API

`BoundedDecisionProvider` 默认并发 2、总 admitted（运行+排队）上限 32、连续 5 次 adapter 失败后熔断 30 秒；排队计入 deadline，关闭取消并回收任务。排队超时与调用者取消不算服务失败。这些为工程边界。`LoopLocalDecisionProvider` 为独立 CLI discovery 管理同 loop 共享闸门、每请求独立 transport，不跨多个 `asyncio.run()` 复用 HTTP client。`validate_decision_provider_credentials()` 只验配置和凭据，不创建 transport，off 不读取 key。

| 接口 | 合同 |
|---|---|
| `resolve_jev_context(model, overrides=None)` | 返回不可变 `JevContextBudget`；仅解析所选模型，未登记抛 `JevContextError`；不发网络请求 |
| `JevContextBudget.measure(state, questions)` / `fits_request(request)` | 包含原生 JSON 包装的总字节与最长单题字节；不是 tokenizer；`fingerprint` 用于缓存/运行时身份 |
| `JevContextBudget.with_byte_guards(...)` | 0=自动，非零只允许更严的人工护栏；不得超过模型护栏；`JevContextLimits` 为精确模型的两项 token 容量 |
| `DecisionRequest` | `request_id`、`purpose`、`question_schema_version`、JSON `state`、具名 `questions` 和可选 `deadline_monotonic` |
| `DecisionPurpose` | `FILTER` / `CHOOSE_PAIR` / `AUDIT_DRAFT` / `REPAIR_PAIR`，仅用于归因，不自动实现对应业务流程或阈值 |
| 导演 `DecisionRequest.state.screen_policy` | 带 brief、证据强度、已选／剩余角色和可用主题摘要；是调用方的软编排合同，不授予 provider 修改准入、计划或数据库的权限 |
| `DecisionRequest.input_digest` | 对 purpose / schema / state / questions 做 canonical JSON SHA-256；不包含请求 ID 和 deadline。模型归因独立保存，不可仅凭此摘要跨模型复用结果 |
| `DecisionProvider.evaluate(request)` | 异步返回 `DecisionEvidence`；provider 不拥有业务副作用 |
| `ChoiceQuestion` | `instructions` + `criteria`（选项 ID → 描述）；答案必须属于本次合法选项，最多 255 项 |
| `ScoreQuestion` | `instructions` + 有序 `criteria`；2–10 档评分，不是任意浮点排序器 |
| `NoulQuestion` / `NoulCriteria` | 二元判断，可明确 true / false 定义；答案是 `noul`，没有独立 `confidence` 字段 |
| `JevDecisionProvider(...)` | 接收 `api_key`、固定模型、超时、最大尝试次数、可选 `httpx.AsyncClient` 及完整 `endpoint_url`；默认官方 URL |
| `validate_jev_endpoint_url(value)` | 只允许完整 HTTPS 原生接口，回环测试可 HTTP；拒绝 userinfo/query/fragment/空白，异常不回显输入 |
| `JevDecisionProvider.aclose()` | 只关闭适配器自己创建的 HTTP client；借用 client 仍由调用方关闭 |
| `parse_jev_response(payload, request)` | 校验并绑定本次请求，返回实际模型、答案映射、用量；非法响应抛出不含敏感正文的 `ValueError` |
| `build_decision_provider(config, *, provider_name="jev", client=None, environ=None)` | 接受 `DecisionConfig`；关闭时返回 `None`，不读取凭据、不创建 client；启用但缺凭据或 provider 未知时抛 `DecisionConfigurationError`（`ConfigError` 子类），不会借用 LLM key |
| `ScriptedDecisionProvider(payloads, *, model="jev-fixture")` | 依次消费离线 fixture，复用真实响应校验器；`requests` 保存请求快照供状态传递断言，不请求网络 |

开发测试接口 `scripts.jev_fake_backend.FakeJevBackend(model=..., mode="ok",
max_request_bytes=48000, max_state_question_bytes=24000)` 是 context manager，
只绑定 `127.0.0.1` 临时端口；`client()` 返回由调用方关闭的 `httpx.AsyncClient`。
其测试 transport 只允许官方原生 URL + `synthetic-test-key`，然后通过真正的
`AsyncHTTPTransport` 连接回环服务，禁用环境代理，不提供外网转发或真实密钥入口。
`requests` 仅保留本轮合成请求内存快照，`records` 给出独立计量的字节数/题数/HTTP
状态。支持 Choice / Score / Noul；`mode` 包含 `ok`、`low_score`、`server_error`、
`missing_answer`、`extra_answer`、`wrong_model`、`unknown_choice`、`bad_distribution`、
`bad_score_legend`。这些故障是测试输入，不是官方错误响应形态的完整模拟。
重复使用的演示入口见 [CLI 开发验证](cli.md#jev-本机开发验证脚本)。

`DecisionEvidence` 区分成功、服务不可用和响应无效，记录请求 / 输入 / schema 归因、
实际模型、用量、时延、尝试次数、HTTP 状态和稳定 `reason_code`。失败不是全零分、
空合法选择或“用户不感兴趣”。Choice / Score 的 `confidence` 与 Noul 值均不等于
经本项目校准的点击率或满意度概率；调用方不得伪造 Noul 的 confidence。

`usage` 仅表示当前返回响应中的 token 用量；重试前失败的远端尝试也可能产生费用，
因此不能把它误称为整个逻辑请求的完整账单。

本地请求构造错误由类型校验拒绝；若调用者事后破坏了嵌套数据，`evaluate()` 会重新
冻结并校验输入，抛出不含原文的 `ValueError("invalid decision request")`，不发 HTTP。
这类调用者错误不同于远端返回 `INVALID`。第一版问题指令与选项描述接受字符串；
state 接受有限数值组成的 JSON 文本 / 对象 / 数组，不支持多模态。Choice / Score
数量上限已校验；调用层仍须控制总上下文 token 预算，不能视为已实现官方 tokenizer。

## 配置与调用生命周期

```toml
[decision.providers.jev]
enabled = false
model = "jev-1.13.0"
api_key_env = "TYPESAFE_API_KEY"
timeout_seconds = 2.0
max_attempts = 2
```

完整字段校验见[配置参考](config.md#decisionprovidersjev)。启用配置只表示允许显式
构建 provider，并不让 daemon 开始请求 Jev，也不自动启用导演或过滤。调用进程必须
提供本机保存的 Token 或 `api_key_env` 指向的环境变量；不要把真实 key 写进配置样例、命令行参数或日志。
固定模型 ID 是可复现实验的起点，换模型后应重新校准业务阈值。

固定模型请求若返回不同的实际模型，结果为 `INVALID / model_mismatch`，保留可用的
实际模型 / 用量归因但不放出答案；`jev-latest` / `jev-preview` 两个已知 alias 允许
解析到实际版本。离线 fake 的模型名是 fixture 标签，不施加远端固定版本匹配规则。

`max_attempts` 包括第一次请求。重试只用于可重试的 HTTP 限流 / 服务错误
（429、529、5xx），受同一个总 deadline 约束，等待时间和所有尝试都计入预算；
认证、非法请求和无效响应不会自动变成无限重试。调用方提供的更早 deadline 可以
进一步收紧预算；取消请求直接传播，不被折叠为普通失败证据。

适配器默认 Jev 官方端点，显式 `endpoint_url` 可指定兼容原生协议的自建/中转；禁止 HTTP 重定向。
URL 改动的配置 API 必须同时提交新 Token，不向另一个地址自动沿用旧凭据。自建 client 遵循项目
`network.outbound_httpx_kwargs()` 的海外网络路由；借用 client 的代理设置由其 owner
负责；回环 client 强制 `trust_env=false`。适配器不负责整屏预算；运行时准入与熔断由 `BoundedDecisionProvider` 负责，
整屏预算由调用者控制，不能把聊天 LLM 的预算视为已经覆盖 Jev。

## 安全与四表面边界

- 请求状态与候选描述会发往配置的外部决策服务；后续调用者应只提供完成判断所需的最小摘要。
- 候选文本是不可信数据，不能因为 typed 输出就认为模型不受内容诱导；provider 本身没有工具或数据库写权限。
- 不记录 API key、state 或远端错误响应正文；错误以类型和稳定原因码呈现。
- CLI：`config-show` 展示开关、模型、凭据变量名和预算，不探测网络，也不读取或打印实际 key。
- 插件 popup、桌面 Web、移动 Web：共用 Jev 连接表单与后端配置 API，可配置连接/容量并切换完整评估；不增加付费探测按钮。Token 不回显/不进浏览器存储，仅后端本机 TOML 保存，非加密密钥库。
- Git / Docker / 桌面安装的依赖与启动流程不变；复用现有 `httpx`，未安装 Jev SDK，也没有自动下载模型。

## 后续接线的验收边界

三策略和独立过滤已接线；离线测试覆盖逐对状态、局部修改后缀复核、不可变屏、deadline 和回退。
中文质量、实际误拒率、探索效果与费用／时延仍需授权实测。现有内容分类仍负责 topic / style / 时效等 metadata，
不能以相关性判断直接替代。具体模式和提交合同见
[导演设计](../plans/2026-09-26-director-jev-modes.md)，原生能力与限制见
[官方研究](../plans/2026-09-26-jev-provider-research.md)。
