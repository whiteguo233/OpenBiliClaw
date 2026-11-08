# 推荐导演实施进度

更新：2026-10-22。导演与后续 Jev 评估实现已统一整合进 main，默认关闭，尚未发版，未改用户配置。历史真实验收修复为 `a94c1401`，覆盖优化 `a1ff1797`，CLI 评测 `5419f168`，屏策略 `4ead2946`；下文保留各轮当时的结果，不把历史未验收项改写为已通过。最新集成验证见 [Jev 实施记录](2026-10-14-jev-single-pass-context.md)。

## 下一阶段：编排产品与真实效果收口

已有代码：`agent_only` 的多屏预排、`jev_recurrent` 的二卡递推、`agent_jev` 的审查/中间换卡，及画像、最近三屏真实曝光/反馈、原子提交、恢复和热重载失效合同。

尚需完成：

- 导演模式本身的图形配置与可观察运行状态；当前 Jev 连接/完整过滤配置并不是导演模式开关，导演仍通过 TOML 控制。
- Agent 与官方 Jev 的真实联合编排、局部修补和反馈后重排验收；目前无 Jev Token，不能以合成服务代替。
- 同一场景下纯 Agent / 混合 / Jev 逐对的完整屏与短尾质量、等待时延、调用次数和失败回退评测。既有 Luna 诊断预算结果不等于默认预算、用户满意度或线上 SLA。

下一切片先明确模式配置、状态与验收入口，再在隔离数据中跑实请求；不重做已存在的编排内核，也不自动开启导演或切换模型。

架构依据：[三模式方案](2026-09-26-director-jev-modes.md)、[基础规格](2026-08-31-recommendation-director-spec.md)。

## 真实请求与隔离数据端到端验收（2026-10-02）

**结论：结构修复已落地，真实请求已执行，但不能签署完整 Agent / Jev 端到端通过。** 合成三场景通过生产结构校验，真实库存可产出有效 Agent 首屏；稳定的多屏供给、默认时限、语义去重仍未验收。以下保留所有失败，不用确定性回退、离线 fixture 或调大预算替代真实成功。

### 修复内容

- 按 diagnosing-bugs 先重现实际非法输出：重复选卡、把“不选”卡放进 pairs、选中与遗漏交叉，以及 6＋2 漏掉仍可用的一卡。v3 改为先判断有用供给、再排完整屏与合法短尾；当前明确意图贯穿未来屏，独立入口不等于无关内容。
- Agent 模型边界改用 `c0..` 请求内短编号，严格映射回 canonical 身份；只翻译所选／遗漏，不改证据、文本、顺序或重复。未知编号仍拒绝，没有按猜测找相似卡或本地补卡。
- `HorizonPlanner.plan(..., max_validation_retries=1)` 仅对非法 JSON／结构／引用／覆盖尝试一次模型纠正。原输入冻结、固定诊断与已知短编号回馈、总共至多两次调用，共用原 deadline；网络失败、超时、晚到和超大输出不重试。成功用量累加，默认 20 秒未改。按 codebase-design 将这些细节封装在 planner seam，不传播到调用方或存储协议。
- 新增 `run_director_live_e2e.py`：源 SQLite 只读 online backup、白名单真实 memory 副本、生产 HTTP handler / SQLite / Soul / DirectorRuntime、真实 loopback TCP 与已登录 Luna CLI。仅临时进程启用 `agent_only/enforce`；反馈只写私有副本。关闭 ASGI lifespan，明确不验 discovery、长期学习、启动恢复或 Jev；报告不保存真实画像、候选、模型正文或异常正文。
- 验收器也建立红测试并修正：无 future／空 tombstone 不能声称持久化成功；必须重启后真正消费 cached NEXT；HTTP 实际交付须与提交批次、卡序和推荐 ID 一致；STAY 对全部前屏去重。首屏没有 future 时单列 `first_plan_has_no_future_screen`，不能误怪弱点击，更不能算完整验收通过。

### 真实 Luna 合成场景

固定 `gpt-6-luna / low`、24 张合成候选、目标每屏 6 卡／3 屏、60 秒诊断预算。输入和模型均未替换成 mock，但内容／画像为合成案例，不是线上满意度实验。

| 场景 | 最终屏形 | 规划总时长 / 调用数 | 结论 |
|---|---|---|---|
| 明确工程需求 | 6＋3 | 43.483 秒 / 2 | 首答漏三卡，模型纠正后选齐 9 张实践内容，没有纯户外填充 |
| 局部反感夸张标题 | 6＋6＋4 | 19.538 秒 / 1 | 避开三张夸张卡，仍保留 AI；但屏目标与部分户外 pair 角色不完全吻合，note 错写总数 |
| 一次弱点击 | 6＋6＋6 | 29.867 秒 / 1 | 没把点击写成喜欢／读完，保留户外；第二屏仍有一组预注册 RAG 同质重复 |

[最终单臂矩阵](../../artifacts/director-effect-eval/luna-e2e-1002-final-matrix.json)三次均通过原生产 validator，但不是语义全绿。另一次[短编号首测](../../artifacts/director-effect-eval/luna-e2e-1002-short-refs-repair.json)在 19.020 秒一次生成合法 6＋3，note 却写 7 张／6＋1，说明自然语言计数仍不能作为权威结果。逐屏旧 `valid_selection` 包含满 target 长度，合法短尾可能为 false；结构结论看 `plan_valid` 和 `planning_completion`，新报告已加说明，不回改历史指标。

中间探针也保留：[v2 基线](../../artifacts/director-effect-eval/luna-e2e-1002-baseline.json)、[早期 v3](../../artifacts/director-effect-eval/luna-e2e-1002-policy-v3.json)、[medium 诊断](../../artifacts/director-effect-eval/luna-e2e-1002-medium.json)、[供给先行首测](../../artifacts/director-effect-eval/luna-e2e-1002-supply-first.json)。medium 没解决结构问题，默认仍为 low；这些跨时间、部分不同 wrapper 的探针不是严格因果 A/B。

### 真实画像／库存与 HTTP

源库存本轮 shortlist 40 张，命名画像包含 32 个兴趣／131 个明确避雷。生产配置仍是 director off；API 写操作全在只读备份后的临时数据库，不修改真实库存、曝光或画像。

- 修复前 [20 秒](../../artifacts/director-effect-eval/live-e2e-1002-20s.json)超时，回退被判失败；[60 秒](../../artifacts/director-effect-eval/live-e2e-1002-60s.json)返回未知编号与选中／遗漏交叉，仍被严格拒绝。
- 修复后 [60 秒六卡屏](../../artifacts/director-effect-eval/live-e2e-1002-final-60s.json)两次规划各得到合法单屏（4 张、6 张），完成实际交付、精确恢复／幂等重放、子集 ACK 和点击幂等。第一份计划本来就没有未来屏，因此无法验收 cached NEXT；当时脚本误报弱点击丢计划，已用上述固定原因修正，原报告不重写。
- 修复后 [默认 20 秒](../../artifacts/director-effect-eval/live-e2e-1002-final-20s.json)首答 13.486 秒生成有效 5 卡屏；下一轮模型先给空屏，再在共享 20 秒预算内纠正超时，明确回退。后台预排调用在报告单独标记，不藏进前台延迟或当成额外纠错。
- [每屏两卡探针](../../artifacts/director-effect-eval/live-e2e-1002-pairs-60s.json)首轮遇 CLI 重连／未知错误，适配器拒绝而不是采纳可能的部分答案。没有因此放宽 CLI 成功合同。
- [最终两卡屏复测](../../artifacts/director-effect-eval/live-e2e-1002-final-pairs-60s.json)首次漏解释三张候选；模型纠正后给出 4＋2，而目标是每屏 2 张，仍被严格拒绝。两次分别 13.037／18.132 秒，不是传输超时；保留失败，未增加第三次纠正或事后拆屏。
- Playwright 使用真实桌面 `/web/` 和移动 `/m/`、无路由 mock：显示同一批十张、刷新恢复、实际可见 ACK 与一次点赞已在隔离 SQLite 查证。该批真实归属是 `deterministic`，原因为 `planner_timeout`（2 次调用），**只是回退路径及页面接线验证，不是 Agent 多屏成功**。首次生成未结束时手点“加载更多”得到 409，原生成完成后正常显示。截图只保存在忽略目录 `output/playwright/`，不提交真实推荐文字；[浏览器服务报告](../../artifacts/director-effect-eval/live-e2e-1002-browser.json) `passed=null` 不代表自动通过。

完整严格脚本要求：NEXT1→可见 ACK／点击→cached NEXT2→明确反馈→fresh NEXT3→非空未来计划持久化→重启精确恢复→cached NEXT4→STAY，并要求五屏均为 Agent、无重复。没有走完的步骤不能用离线测试补签。Jev 凭据仍未配置，纯 Jev、混合修补与独立过滤的真实服务验收均未执行。

浏览器追加点了移动端“换一批”：真实提交 `STAY`，体验序号保持 1，但仍为超时回退的十卡屏；[脱敏观察记录](../../artifacts/director-effect-eval/live-e2e-1002-browser-observations.json)与服务模型调用报告分开，不伪造自动 pass。浏览器及临时服务均已结束，私有副本按生命周期清理。

### 代码验证与交付边界

- 最终相关集 **1018 passed / 48 warnings**（105.03 秒）：`PYTHONPATH=src pytest tests/test_director_*.py tests/test_pair_*.py tests/test_decision_*.py tests/test_jev_*.py tests/test_semantic_filter.py -q --disable-warnings --maxfail=3`。包括真实 loopback HTTP／SQLite 加明确 fake 模型的验收器红绿回归，不把这 1018 项当成真实模型质量得分。
- 全仓 `ruff check src/ tests/` 加三份评测脚本通过；`mypy src/` 加三脚本共 321 个源文件通过，变更 Python 格式检查通过。没有新增配置字段、依赖、数据库列、安装流程或用户界面实现。
- 完整仓库 pytest 运行 856.33 秒后主动停止以收束到相关回归：当时 **3037 passed / 2 skipped**，约 28%，不是全仓通过，不与相关集叠加报数。
- 真正未通过的是模型供给／结构稳定性与时延，不是把 HTTP 200 或有卡片当作成功就能消除的问题。保留原 20 秒及严格合同、没有默认启用导演、没有改真实偏好以凑通过。需要后续更稳定的模型输出与 Jev 本机凭据才能完成剩余真实模式验收。

## 临时 CLI 的重连恢复修复（2026-10-01）

上轮只能观察到错误计数，缺少时序，故没有接受那些输出。本轮按 diagnosing-bugs 建立秒级服务边界回归，再作一次真实脱敏探针：

- 最小测试 `test_reconnection_notice_before_valid_final_does_not_discard_completed_turn` 原实现明确失败：即使通知后有新答案、完成事件及匹配文件，仍抛 `cli_turn_failed`。移除通知则通过。
- [真实探针事件序列](../../artifacts/director-effect-eval/luna-stream-recovery-probe.json) 为 thread → 初始化诊断 item → turn → **重连 2/5** → JSON 答案 → completed。该答案屏形为 6＋4＋2，说明有最终 JSON，但也说明它不能直接通过覆盖校验。对应 [未修改成功规则的调用记录](../../artifacts/director-effect-eval/luna-stream-recovery-probe-run.json) 在 24.880 秒被旧规则拒收。
- 已确认的主因是适配器把**任何顶层 error** 都当作整轮失败。现在只识别有界编号重连通知与显式 WebSocket→HTTPS 回退通知，要求通知后出现新最终答案、单个生命周期、终态位于最后、退出码为 0 且新临时文件完全匹配。未知错误、`turn.failed`、陈旧答案、工具事件、重复轮次和不完整结果继续拒绝。CR/LF 注入与格式边界均有回归。
- [第一次复测](../../artifacts/director-effect-eval/luna-stream-recovery-matrix.json) 的工程场景在 17.837 秒通过通道、被 planner 正确拒绝：`c02/c03` 同时列在选中 pairs 和 omissions，pair intent 还写着“不选”。风格场景在 37.188 秒仍有一类未识别的通知（5 个 error 中 4 个为编号重连）；根据本机 CLI 二进制确认的通知文本，后续补齐显式 HTTPS 回退识别，但没有该次第五条通知的正文，不能断言其身份。该轮弱点击未运行。失败／非法计划记录没有删除或改成成功。
- [最终复测](../../artifacts/director-effect-eval/luna-stream-recovery-final.json) 保持同模型、同输入与 60 秒预算，首个工程场景在 **60.013 秒**触发 `planner_timeout`，CLI 进程已取消并回收；后两个场景未调用。这是实际截止时间到达，不是解析器拒收，也不能据此确定等待发生在哪个网络阶段。本轮没有通过生产校验的真实计划，不能宣称三场景、模型质量或时延已经验收。

本修复验收：最终相关回归集 **257 passed**（7.00 秒），覆盖适配器、评测入口、多屏规划／覆盖与运行时；包含真实 planner 加 mock CLI 的恢复边界，不是模型质量分。`ruff check src/ tests/`（另含适配器和评测入口）、变更 Python 格式检查、`mypy src/`（317 文件）及 `git diff --check` 通过；未重跑全仓 pytest。编号重连与 HTTPS 回退均先建立失败测试再修复，未知错误／不完整成功／覆盖不合法继续拒绝。

该修复只改开发者评测适配器与测试，**未改生产 planner、提示词、策略版本或覆盖合同**；不替模型删掉矛盾卡片，不把程序修正后的输出当作原模型效果。临时探针脚本已清理，只保留不含错误正文／推理的诊断证据。官方文档只确认[事件类型和最终文件机制](https://learn.chatgpt.com/docs/non-interactive-mode)，通知识别是本机版本兼容规则，不能推广为“所有 error 都可忽略”。

## 多屏覆盖与短尾保护优化（2026-10-01）

本次针对上一轮三个新策略调用都只排一屏的问题，统一修正规划合同与执行边界，不仅改一句 prompt：

1. 将请求屏数表达为**目标**而不只是上限；单屏 `screen_policy` 是逐屏复用的模板，避免其中的 `remaining_count=6` 被误当整个规划预算。
2. Agent 一次确定适用内容，再编排完整屏＋至多一个短尾；明确意图约束全部未来屏，不为凑数改推其他稳定兴趣，也不虚构用户读完前屏。
3. 新输出必带完成／缺货声明，并按已有 refs 说明语义未用候选。程序独立校验屏形、唯一性、硬配额及剩余可扩展容量。语义判断仍为未认证意见；硬可行性只针对已选前缀，不证明全局最优。
4. 初始 `planning_completion` 随计划保存，消费／重绑定后不拿历史计数与剩余屏数比较；每屏保存 `initial_card_count` 区分有意短尾与后续硬修补缩水，纯 Agent／混合修补不机械补满语义短尾。遗漏不写入长期偏好或全局屏蔽。
5. 规划原因进入安全 trace；策略版本升为 v2，旧持久化格式可读、旧未来计划不复用。默认预算和调用次数不变。

采用 codebase-design 的模块边界原则，将覆盖／提前结束规则收进统一的 `horizon_coverage.py`，规划与执行使用同一份有界历史合同。没有新增数据库列、UI、配置、安装依赖或远端 provider；四表面仍共用后端。CLI 临时测试通道不是上线方案。

### 同场景再测与限制

使用同一份三场景合成 fixture、24 张候选、每屏 6 卡、目标 3 屏，固定 Codex CLI / `gpt-6-luna` / `low` 与 60 秒诊断预算。v2 严格完成合同与旧 prompt 不兼容；评测入口现在仅运行当前策略单臂，与冻结的 [v1 报告](../../artifacts/director-effect-eval/luna-matrix-60s.json)做描述性前后对照，不是同时 A/B。历史 artifacts 不重写。

首轮 [v2 原始记录](../../artifacts/director-effect-eval/luna-horizon-v2-60s.json) 在工程需求场景 34.725 秒返回 `planner_unavailable`：CLI 退出码为 0，但 JSONL 含致命失败事件，适配器按 `cli_turn_failed` 拒绝，后两场景未调用。该轮无有效计划，不可判定新策略好坏，也不把可能出现的部分正文计为成功。成功判定继续要求唯一 `turn.completed`、无致命 error／turn.failed、最终消息与文件一致；参照 [OpenAI 官方非交互事件文档](https://learn.chatgpt.com/docs/non-interactive-mode)，不为跑通评测放宽失败语义。

只增加错误诊断白名单后做了一次 [诊断重试](../../artifacts/director-effect-eval/luna-horizon-v2-diagnostic.json)：38.721 秒，同样被适配器拒收。退出码 0；事件计数为 5 个顶层 `error`、1 个 `turn.completed`、3 个 completed item；无 `turn.failed` 事件。脱敏标记为 `connection`、`stream disconnected`。**只能确认顶层错误触发严格合同拒收，不能断言没有模型回答或编排质量失败。** 未继续第三次调用；两轮后续场景均未跑，暂不能给出 v1→v2 的模型效果或延迟改善结论。

新增诊断只从 stderr 与真实错误事件字段取有限词表，不保存错误正文、候选、最终文案或推理；成功规则不变。不绕过限额／网络故障、不改模型，不使用 mock 输出补齐真实效果表。硬失效情况下的确定性替换仍可能用到原遗漏内容（有明确归因并要求重排）；短尾容量保护不等于运行期语义强保证。

### 本增量验收

- 最终代码树相关集 **1,572 passed / 78 warnings**（104.98 秒）：全部 `test_director_*`、`test_pair_*`、`test_decision_*`、`test_jev_*`，以及 semantic filter、config、LLM concurrency、OpenClaw、recommendation engine／proxy headers、profile views 和 soul profile。包含真实 Chromium、SQLite 和明确 mock 的模型边界；不是模型质量得分。
- 覆盖未解释停排、6＋3 语义短尾、6＋6＋6 满目标、硬配额、无效遗漏 refs、初始快照消费／重启／STAY、缓存 trace、模型故障归因、短尾不反复重排、失效卡明确修补，以及原满屏硬缩水后恢复。共享版本变化的 Jev golden 已核验：只把版本改回 v1，即精确恢复旧 digest，问题指令／选项未漂移。
- 全仓 `ruff check src/ tests/`（另含两评测脚本）、变更 Python 格式检查、`mypy src/`（317 文件）和 `git diff --check` 通过。
- 曾启动全仓 `pytest -q -m 'not integration' --maxfail=3`，运行 423.15 秒后主动中止以收束到改动相关集；当时 **1,627 passed / 121 deselected**，约 15%，不是完整全仓通过。最终采用上述完整相关集验收，不合并重复计数。
- 两次真实 CLI 调用均无被接受的计划，未测试真实 Jev；未修改用户配置、真实画像／库存／曝光，未合回 main、推送或发布。

## Codex CLI / GPT-6 Luna 合成场景实测（2026-09-30）

**结论：临时 CLI 通道已跑通，三场景新旧 prompt 共六次均通过真实生产 planner 校验；首屏行为符合预期，但多屏预排与默认时延预算尚未达标。** 这不是线上发布、真实用户实验或 Jev 验收。

### 运行条件与原始记录

- 用户明确要求临时使用 Codex CLI / `gpt-6-luna`。最终使用本机应用自带 `codex-cli 0.155.0-alpha.16.3`，每次显式 `--model gpt-6-luna` / `low`，不更改模型；JSONL 未回传服务端模型标识，记录注明模型归因来自 CLI flag。
- 沿用上一轮预注册画像、24 张合成中文候选与三类反馈；每屏目标 6 卡、最多预排 3 屏、单次 60 秒诊断预算。每条件每场景一次，调用顺序跨场景交错，不声称统计显著性。
- 新旧两条件都经当前 HorizonPlanner 的引用／结构／约束验证。旧条件只替换旧静态 prompt 并移除 `screen_policy` 块，仍不是完整旧生产版本。旧结果的 `source=conservative` brief 和 `role=open` 是兼容归一化，不能当成旧模型自行给出的证据判断。
- 结果：[六次对照原始 JSON](../../artifacts/director-effect-eval/luna-matrix-60s.json)。时延包含 CLI 启动与附加上下文；temperature／max_tokens 未在 CLI 上强制，CLI token 用量不换算 API 费用，也不与上一轮两家 HTTP 供应商混算。

| 场景 | 旧 prompt 首屏 / 后续 | 新策略首屏 / 后续 | 旧 / 新总耗时 |
|---|---|---|---|
| 明确要 Agent 工程落地 | 6/6 工程内容；共 3 屏（6/6/2 卡），第三屏全是户外装备，与本次方向不符 | 6/6 工程内容，含徒步笔记助手的关联探索；只生成 1 屏 | 13.379 / 22.138 秒 |
| 只反感夸张标题 | 0 张夸张卡、6 张 AI 内容；共 3 屏（6/6/2 卡），以后续屏覆盖户外 | 0 张夸张卡，4 张纯 AI＋1 张 AI/户外桥接＋1 张户外；只生成 1 屏 | 11.908 / 11.488 秒 |
| 单次点击／无操作曝光 | 首屏 6 张 AI 内容，后续保留户外；共 3 屏（6/6/2 卡） | 4 张纯 AI＋1 张桥接＋1 张户外；brief 明确点击不等于喜欢／读完、未操作不等于不喜欢；只生成 1 屏 | 17.428 / 13.637 秒 |

所有六次有效计划都没有未知／重复引用，按预注册的两个入门语义重复组也没有重复。新策略三次均有真实生成的 brief／角色，显式指出未知点。旧策略同样通过了首屏贴题和局部风格避让检查，不能据此说新策略带来全面提升。两个兴趣同屏呈现是产品差异，不自动等于用户更满意；桥接卡不算两个独立兴趣入口。

### 一屏具体长什么样

明确要求工程落地时，新策略实际输出：

| 配对角色 | 两张合成卡 | 本对职责 |
|---|---|---|
| anchor | `c07` 工单助手实现笔记 + `c08` 工具调用重复执行复盘 | 实现与可靠性 |
| complement | `c11` 本地助手容器部署 + `c13` trace 定位失败步骤 | 运行与排查 |
| exploration | `c14` 修改数据前确认 + `c20` 徒步笔记可追问助手 | 权限边界与关联应用；角色是软描述，不是硬配额 |

### 需要继续改进，但本轮未擅自改生产逻辑

1. **预排长度不足。** 新策略三次都只给 6 卡一屏，虽满足“最多三屏”的当前合同，却没有验证用户期望的提前多屏编排／摊薄调用成本。应明确期望屏数、何时合法缩短以及供给不足说明，不能只靠上限暗示模型多排；也不能机械凑满导致旧方案中的跑题。
2. **慢路径预算仍需标定。** 60 秒诊断下新策略为 11.488–22.138 秒；其中一次超过现有 20 秒默认预算，且另一次 20 秒 smoke 确实超时。不能将 CLI 启动时延当线上模型 SLA，也不能据此直接提高生产预算。
3. **证据还很小。** 每格仅一次、候选与历史是合成且风格元数据可见；没有真实历史的反事实回放、真实连续消费、线上满意度或 Jev 修补／过滤结果。后续应扩充真实形态的候选样本与重复轮次，再讨论效果收益。

### 失败与兼容记录不隐藏

- PATH 首选独立 CLI `0.146.0`：[首次失败](../../artifacts/director-effect-eval/luna-smoke-20s.json) 6.449 秒；[诊断](../../artifacts/director-effect-eval/luna-smoke-diagnostic.json) 12.465 秒，安全标记为 `not supported` / `stream disconnected`，当时模型缓存无 GPT-6 Luna。没有静默换模型；选择现有应用自带版本后，其模型缓存包含 GPT-6 Luna。
- 新版 CLI [20 秒 smoke](../../artifacts/director-effect-eval/luna-bundled-smoke-20s.json) 超时且进程组已回收。两轮 [60 秒 smoke](../../artifacts/director-effect-eval/luna-bundled-smoke-60s.json)／[事件诊断](../../artifacts/director-effect-eval/luna-bundled-event-diagnostic.json) 分别在 15.617／14.602 秒 exit 0，但因夹带恢复性 `item.type=error` 被早期适配器拒绝，不能当模型失败或成功效果样本。
- 修正临时适配器后，恢复性 error item 只记诊断、不保存原文；仍须 exit 0、唯一 turn.completed、最终文件与最终消息一致、没有工具事件，才进入生产 planner 校验。最终六次都有恢复性诊断标记，均保留在原始记录。
- 未修改 OpenBiliClaw 用户配置、真实画像／库存／曝光，未切换生产路由、合入 main／发布；CLI 自行正常维护认证／模型缓存。新增入口与复跑命令见[模块文档](../modules/director.md#验证与记录)。
- 最终离线定向集 **153 passed**（14.81 秒）：CLI transport、effect cases／runner、horizon modes、screen policy、policy planner。全仓 `ruff check src/ tests/`（含评测脚本）、变更文件格式与 `git diff --check` 通过。未重跑全仓 pytest；这些测试数字不是模型质量分。仅开发者评测脚本／测试／记录改变，没有生产接口、四表面、配置字段、依赖或安装器变化。

## Agent 真实调用试跑（2026-09-30）

以下是前轮配置模型的历史记录，后续 CLI 结果见上节。**该轮没有获得有效 Agent 计划，不能下编排质量结论。** 用户明确选择先看 Agent，未测试缺少 `TYPESAFE_API_KEY` 的 Jev。没有修改生产实现、真实配置、画像、库存或曝光；默认导演仍关闭，未合入 main／发布。

### 试跑记录

只使用既有推荐链里的模型，在测试进程内分别固定实例；两模型结果不能混作同模型 A/B。每轮输入都是合成的同一画像、24 张中文候选、6 卡目标，先验证明确工程需求场景。下列数字是 service／planner 层经过时间，不是首 token 延迟或线上 SLA。

| 模型与条件 | 屏数 / 预算 | 结果 | 用时 |
|---|---|---|---|
| deepseek-v4-flash，新策略首次 | 3 / 20 秒 | provider unavailable；此轮只记录顶层错误类，不能追认具体 HTTP 原因 | 0.555 秒 |
| deepseek-v4-flash，新策略诊断 | 3 / 20 秒 | HTTP 429 | 0.688 秒 |
| sensenova-6.8-flash-lite，新策略 | 3 / 20 秒 | planner timeout，没有结果 | 20.003 秒 |
| sensenova-6.8-flash-lite，新策略长预算诊断 | 3 / 60 秒 | LLMResponseError，没有有效正文 | 51.656 秒 |
| sensenova-6.8-flash-lite，旧 prompt 单屏诊断 | 1 / 60 秒 | HTTP 429 | 31.898 秒 |
| 同进程随后新策略单屏 | 1 / 60 秒 | 上次 429 后 registry 本地冷却短路，**不是一次新远端模型结果** | 0.005 秒 |

原始、未重写的合成输入与结果：

- [主模型首次](../../artifacts/director-effect-eval/agent-policy-smoke-20s.json) 与 [429 诊断](../../artifacts/director-effect-eval/agent-policy-smoke-diagnostic.json)。
- [备用模型 20 秒](../../artifacts/director-effect-eval/sensenova-smoke-20s.json) 与 [60 秒](../../artifacts/director-effect-eval/sensenova-smoke-60s.json)。
- [单屏新旧条件诊断](../../artifacts/director-effect-eval/sensenova-single-screen-60s.json)，其中新条件的本地短路必须排除在远端模型质量比较之外。

`LLMResponseError` 在适配器里既可能表示纯空正文，也可能是仅有 reasoning 而没有最终正文；这次该轮未记录二者区别，**不能断言推理耗尽 token 或具体 finish reason**。适配器可能执行格式兼容／瞬时错误重试，故 service 调用数不等于 HTTP 请求数；没有完整失败用量与费用记录。没有任何 `plan_valid=true`，也没有把 deterministic baseline 的卡片算成 Agent 输出。

### 已留下的评测合同与下一次入口

- `scripts/director_effect_cases.py`：预注册三类场景——明确要求工程落地；“标题太夸张”的局部负反馈；单次点击与无操作曝光。使用生产命名画像视图，24 张候选固定，标签不进入模型输入。
- 原始指标为任务支持、语义重复、夸张风格、已有兴趣覆盖和有依据探索；不合成“质量总分”。弱点击是否引发虚构疲劳／掌握程度仍需检查简短 brief，不能从数量推断。
- 旧条件仅替换 `e86105b1` 的 planner 静态 prompt，并删除新的 `screen_policy` 输入块；当前校验器、其他画像／历史／候选相同。此为 prompt 对照，**不是旧二进制／完整旧推荐算法对照**；deterministic pair baseline 也不是原生产 MMR。
- `scripts/run_director_effect_eval.py` 显式联网 opt-in、限定现有模型路由、输出文件不可覆盖、最多两次重复／60 秒预算、临时 memory；checkpoint 保留失败。发现冷却污染后，已把脚本改为服务故障立即停止余下比较；保留上面的原始失败记录，不重新打接口绕过限流。
- 新增离线评测保护测试 19 项；连同 horizon modes、screen policy、policy planner 定向集共 **111 passed**（8.67 秒）。全仓 `ruff check src/ tests/`（另含新脚本）、新文件格式检查与 `git diff --check` 通过；未重跑全仓 pytest，不将 mock 回归当成效果证明。脚本不自动改模型、关闭思考或提高生产预算。四表面、配置、架构接线、依赖和安装流程均不变。

服务恢复后按[模块文档的显式命令](../modules/director.md#验证与记录)重新跑三场景同模型配对比较。局部负反馈、弱点击与后续屏质量这次**尚未获得真实模型结果**；不能用前面 1,436 项 mock／协议测试替代。先恢复可用模型服务并核验可用正文，再评价策略；当前失败不足以归因于导演策略本身。

## 当前增量：共享屏幕编排策略（2026-09-30）

- 三模式共享 `policy.py`：证据分层 → 屏幕任务 → 软角色 → 两卡增量价值 → 整屏自检 → 可撤销计划。每屏 brief 记录目标、已有证据引用、未知点和描述性失效条件；每对记录 role / intent。不是模型长篇推理日志。
- Agent 一次调用输出具体多屏计划和上述决策结果；验证枚举、来源引用和候选约束。旧格式缺 brief 时保守补齐且明确 `source=conservative`，不伪称模型判断完整。
- 纯 Jev 用明确意图／浏览建立保守目标，一次 Choice 联合选择 pair＋role（≤64 个选项加拒选），不增加调用次数。Jev 修补看到当前职责、已选角色和剩余草稿，保留局部意图；无适当替代则请求重新规划。
- 实际可完成数量用于短屏最后一对检查。程序仅诊断规范化后完全相同的标题，固定 `semantic_quality=not_certified`；模型整屏自检仍是同一次调用中的指令，不是独立质量认证。
- 纯 Agent 以同事务、完整历史的偏好 revision 水位区分普通曝光／点击与需重排的反馈，避免 4+4 窗口截断引起误判。最多四个动作槽同时保护最新非点击反馈，保证 Agent 能看到重排原因；skip 仍是模糊反馈。新增部分表达式索引，不新增表／列、不改写历史。
- 策略版本写入计划和 runtime context digest，旧策略的未来计划及在飞写入失效；已提交屏不改。三端、CLI 和 OpenClaw 共用后端，无新配置／UI／安装依赖，默认仍关闭，未修改真实配置或调用付费模型。

### 本增量最终验收

- 最终代码树相关回归集 **1,436 passed / 78 warnings**（143.64 秒）：`tests/test_director_*.py`、`test_pair_*.py`、`test_decision_*.py`、`test_jev_*.py`、`test_semantic_filter.py`、`test_config.py`、`test_llm_concurrency.py`、`test_openclaw_*.py`、`test_recommendation_engine.py`、`test_recommendation_proxy_headers.py`、`test_profile_views*.py`、`test_soul_profile.py`。包含真实 Chromium 的导演桌面／移动协议测试和临时 SQLite；模型调用均为离线 fixture / mock，不是实际推荐效果验收。
- 新增验收覆盖联合 pair＋role 精确归因、三模式落库与消费后保留角色、未知证据拒绝、保守兼容输出、局部修补职责及后缀语义、缺货尾对检查、连续 ACK／点击与显式反馈水位、同卡密集点击后保留明确要求、旧策略在飞提交隔离。
- 全仓 `ruff check src/ tests/`、变更 Python 文件格式检查、`mypy src/`（316 文件）、`git diff --check` 通过。`python scripts/replay_director_pairs.py` 离线回放成功，报告 0 次网络调用。
- 未运行全仓所有 pytest 文件，未合入 main 或发版；不能把相关集通过写成全仓全绿。真实中文推荐质量、满意度、成本与时延仍需另行授权评测。

## 上一轮增量：画像与短期会话上下文（2026-09-30）

- 三种模式统一接入命名推荐画像视图：稳定偏好、近期关注、全部明确避雷；Agent 完整预算、Jev 精简预算，不使用文学人格画像或聊天记忆。
- 最近三屏真实经历按 stream 隔离，从不可变屏快照补齐标题／主题／来源与反馈语义；保留真实曝光时间、每卡最近四条动作。生成和预加载不算曝光，点击不等于喜欢，没有操作不等于不喜欢。
- 画像摘要变化使后续 Agent 预排重新规划；历史提交不改写。Agent 稳定画像前置、动态屏幕状态后置，共同上下文投影避免三种模式各自维护输入合同。
- 最终定向测试 **427 passed / 4 warnings**（38.55 秒）：context、runtime、store、horizon、API、CLI、OpenClaw、全部 pair 与 profile view、Soul profile 用例。全仓 Ruff、MyPy（315 文件）和 `git diff --check` 通过。
- 导演真实 Chromium 四项最终复跑 **4 passed**（51.25 秒）。此前宽集出现两条桌面曝光断言失败；其中初始生成断言在未修改父提交也复现，另一条未证明基线必现。最终复跑通过不抹掉这次不稳定记录。
- 全仓 pytest 排除既有联网登录测试后启动，但未完成；约 29% 时已出现失败，结束该轮广泛运行，不作为完整全仓验收或通过统计。本增量以以上最终定向结果为准，未修改前端、库存或登录探测行为。
- 模块、画像使用规范、架构、规格、双语 README 与变更日志同步。配置／CLI／依赖／安装器未改；默认关闭，未改真实配置、调用付费服务、合回 main 或发布。实际推荐质量与延迟仍需另行实测。

## 上一轮交付：三策略、独立过滤与整屏运行时（2026-09-26）

已完成本轮要求的可运行功能：Jev 两卡递推、Agent 多屏具体预排 + Jev 中间单卡／整对修补、纯 Agent 预排，以及独立 Jev 语义过滤。默认关闭，配置采用 TOML；更改后完整重启后端（主 API、推荐进程、发现 worker）。没有改用户真实配置、调用付费 API、合入 main 或发版。

- 同一 `DirectorRuntime` / `DirectorStore` 承担候选准备、状态版本、幂等、TTL / CAS、原子屏提交与不可变历史；配置换代拒绝旧进程提案，同配置重建保留未来计划。
- `NEXT` 推进屏，`STAY` 保留根方向；真实可见 ACK / click / 明确反馈与生成、预加载分离。复用 canonical event ID，重试不重复写学习事件。
- 三端（插件、桌面、移动）共用客户端协议；CLI 和 OpenClaw 接同一运行时，宿主工具返回不当曝光，需实际展示后明确 ACK。
- 独立过滤只判断明确稳定避雷并保留探索；拒绝仅延期一轮，下轮强制完整 LLM 评估，不承诺永久省掉评估成本。enforce 的人工校准标签不是效果证明。
- 预算覆盖排队／调用，provider 共用限流／熔断，取消与关闭回收资源；Agent-only 关过滤时不创建 Jev client、不读 key。
- `shadow` 展示同 shortlist 的确定性 pair baseline，不是旧 MMR 的严格不干预对照；真实中文效果、误拒率、长期满意度、费用和时延仍需授权实测。

完整使用合同见 [导演模块](../modules/director.md)、[配置](../modules/config.md)、[API](../modules/api.md)。

### 本轮验收记录

本功能验收使用临时 SQLite、合成画像、Fake Agent/Jev 或 MockTransport；浏览器测试使用真实 Chromium，没有真实 Jev / Agent 效果验收。全仓测试开始后的审计修复另跑最终定向集，两个统计不混加。

- 最终树定向 Python 集 **1,340 passed**（85.53 秒）：`tests/test_director_*.py`、`test_pair_*.py`、`test_decision_*.py`、`test_jev_*.py`、`test_semantic_filter.py`、`test_config.py`、`test_llm_concurrency.py`、`test_openclaw_*.py`、`test_recommendation_engine.py`、`test_recommendation_proxy_headers.py`。包含真实 SQLite / API / CLI / OpenClaw 和三端 Chromium 协议用例。
- 安装合同与安装器测试 **21 passed**；`bash -n scripts/install.sh` 通过。安装完成提示仅链接默认关闭的可选配置，不读取 Key 或探测服务。
- `node --test tests/js/*.test.mjs`：**77 passed**，其中共享导演客户端 16 项。扩展全套 `npm test`：**1,497 passed / 1 failed**；唯一失败是下述已复现的聊天旧断言。
- 扩展 `npm run typecheck`、`npm run build:bundle` 通过，Chrome 19 项 manifest / WAR 资源预检通过；构建未发布。
- 全仓 `ruff check src/ tests/`、新文件格式检查、`mypy src/`（**315 source files**）、`git diff --check` 通过。

已知基线例外均在干净 detached worktree **1253a0a8** 再次复现：

1. Python：`tests/test_desktop_web_list_stability_e2e.py::test_pool_refill_event_keeps_loaded_cards_and_scroll_position`，库存数字期望 `70`、实际 `40`（第 331 行）。
2. 扩展：`tests/popup-api.test.ts` 的 `startChatTurn posts durable chat turn metadata`，实现已发送 `streaming: false`，旧断言仍期望该字段不存在。

全仓 Python `pytest -q --disable-warnings --maxfail=12` 完整跑完：**10,088 passed / 65 skipped / 6 failed**，1,368.15 秒。该进程在最终审计修复前启动，不是最终树的一次性全绿结果：其中旧 CLI stub 的两个失败、OpenClaw 反馈 / CLI 各一个失败、Weibo 源码检查失败，已在最终树连同相关用例复跑 **32 passed**。补齐了 JSON CLI 测试对夹杂的 fixture 启动日志的隔离；源码检查在文件冻结后的新进程中通过。剩下的桌面库存断言为上述干净基线问题。

补跑既有 CLI 全文件时，另遇 `test_login_codex_import_uses_source_path` 的 `ConnectError`：该测试仅 mock 凭据导入，未 mock 登录后的联网探测；测试及登录探测路径均未在本分支修改。这项作为额外的非隔离测试失败记录，不将其算成通过。显式排除该项后，`test_cli.py` + `test_director_openclaw.py` + `test_weibo_wiring.py` 为 **289 passed / 1 deselected**。默认功能测试不依赖这条登录路径。

这些统计不是“全仓全绿”；未在导演分支夹带修改桌面库存、聊天协议或登录探测行为。

## 历史切片：离线逐对编排内核（e364b4e2）

以下是该次提交时的历史边界，不代表当前能力；后续待办已由上面的运行时交付覆盖。

已在同一独立 worktree 实现 `PairComposer.prepare_next()`；这次推进的是可回放内核，
**不是生产 shadow 或导演上线**。只有小 Interface 返回 tentative 提案，复杂的可行性、
递推、证据与故障处理留在内核里；没有直接接入旧 `serve()` 或混合历史列表。

- 冻结且版本化的 shortlist、session / state / feedback cursor、方向和约束合同。
  当前合同是离线输入，不冒充完整 SessionStepPolicy、请求幂等或 stream fence。
- 每步一个 Choice 选有序双卡组合，下一步携带已选 prefix；真实反馈不会随草稿增长。
- 精确处理互斥 topic minimum / maximum、canonical 去重、排除和 eligibility 快照。
  每个选项都必须保留整屏可完成性；保留确定性完整 witness，有界提供语义备选。
- 模型拒选、错绑 / 非法证据、超时、迟到、预算耗尽都带原因回退；调用者取消传播，
  包括 adapter 捕捉取消后仍试图返回成功的情况。
- 支持奇数尾部和最大合法短缺数量；硬 minimum 无法满足时明确 UNSATISFIABLE，
  不放松规则、不重复凑数。内部不变量失败抛出错误，不伪装成证明无解。
- 可执行合成回放：`python scripts/replay_director_pairs.py`，演示三次顺序选择与
  中间拒选后的确定性补完；不读真实画像 / 数据库 / 配置，不使用真实凭据或网络。
- shortlist 40、目标 20、每步最多 16 个候选组合、256 次组合探测、24,000 UTF-8
  字节、默认 2 秒整屏模型决策预算均为工程上限，不是质量或性能标定。

详见[内核合同与示例](../modules/director.md)。Agent 多屏草稿、混合修补、独立过滤、
屏提交 / 实际曝光 / 反馈桥和真实中文质量验收仍未完成。四表面推荐行为、配置字段、
依赖和安装流程都不变；未合入 main、未发布。

### 本切片验收

- 四个新增 `test_pair_*` 文件：**205 passed**；director 模块 **99%** 覆盖率
  （384 statements，5 未覆盖）。包括 120 组固定种子的穷举对照、prompt / 输入摘要
  golden、真实 Jev adapter 的 mock HTTP 串联、故障、取消和可执行回放。
- 上述用例与独立 decision / Jev、推荐引擎、PoolCurator、画像边界和 style 回归合跑：
  **578 passed**，32.88 秒。两次统计是重叠集合，不相加。
- 全仓 `ruff check src/ tests/`（另含新脚本）通过；`mypy src/` **306 files** 通过；
  新文件格式检查与 `git diff --check` 通过。
- 本切片未重跑耗时全仓 pytest。下方全仓 9517 / 65 / 1 属于前一 provider 切片，
  不能当作本次最终树的全量结果；已知桌面库存失败未在本切片修改或重新验证。
- 交叉审计发现并修复了“provider 吞掉外部取消仍返回成功”和超大 revision 整数
  序列化失败两个边界，分别加入回归用例。未调用真实 / 付费服务。

## 前一切片：独立 Jev provider（24f34424）

代码与模块验收已完成；全仓回归有一项已在干净基线复现的既有失败。本切片没有启用
任何用户推荐行为，未合入 main、未发布。

- `decision/`：类型化请求 / 证据、原生 HTTP adapter、独立工厂、可回放 fake。
- Choice / Score / Noul 的问题绑定、合法选项、概率、rubric、模型与输入归因。
- 总 deadline、有限重试、取消传播、固定模型漂移拒绝、错误与凭据脱敏。
- `[decision.providers.jev]`：默认关闭、环境变量凭据引用、保存 / 重载和 CLI 展示。
- 不新增依赖，不读取真实 key，不调用付费模型，不写候选、曝光或画像。
- 未实现全局并发 / 熔断、中文质量门槛、过滤业务策略或导演运行时接线。

这是可独立测试的 provider 底座，不表示基础规格的 Phase 0A 已完成，也不表示导演
三模式已可用。主工作区的设计稿保留；实现、配置、测试和文档变更均在独立 worktree。

## 独立 provider 验收记录（前一切片）

所有 Python 命令均在独立 worktree 使用 `PYTHONPATH=src` 与项目现有 `.venv`；没有
安装新依赖，也没有调用真实 Jev 服务。

- 新增五个 decision / Jev 测试文件：**144 passed**，`openbiliclaw.decision` 覆盖率
  **98%**（330 statements，7 未覆盖）。
- 上述五文件加既有 `tests/test_config.py`：**487 passed**。
- 既有 config-show 子集：6 passed；另行检查的桌面测试子集：19 passed。
- `ruff check src/ tests/`：通过；新增模块与测试 `ruff format --check`：通过。
- `mypy src/`：302 source files 通过；`git diff --check` 通过。
- `pytest -q --disable-warnings --maxfail=10` 全仓回归：**9517 passed, 65 skipped,
  1 failed**，耗时约 21 分 44 秒。运行期间补齐的新增用例已由最终定向集覆盖，两个
  统计口径分开记录，不将它们相加称为一次全量运行。

唯一失败：
`tests/test_desktop_web_list_stability_e2e.py::test_pool_refill_event_keeps_loaded_cards_and_scroll_position`，
第 331 行库存数字预期 `70`、实际 `40`。在功能 worktree 单独重跑失败；另建干净的
detached worktree，基于未改动的 **1253a0a8** 单独运行，同一断言也失败。该桌面
HTML/JS 与测试均不在本次改动中；作为既有回归例外留痕，不在 Jev 分支夹带修复。

## 历史规划：后续切片与先后依赖（已按上文推进）

1. **屏与反馈底座**：BatchInstance 身份、生成请求幂等、真实曝光确认、反馈归因与提交
   fencing。现有 GET top-up 混合历史行的问题必须先解决，才能上线“每屏”导演。
2. **过滤 shadow**：在模型预过滤之前冻结对照 cohort，复用 Jev provider；保留现有
   metadata 评估，排除现有 embedding filter 与 Jev 同时 enforce 的混杂。
   这是可与屏底座并行开发的独立接线，但需要自己的证据持久化与样本覆盖门槛。
3. **逐对编排接线**：离线合法 pair 提案、递归 prefix、两卡追加和整屏 topic 配额验证
   已实现；仍需真实供给映射、中文评测与生产 shadow，以及完整无 Plan 的
   SESSION_STEP / SESSION_STAY 合同。先完成屏底座，再接线上提交。
4. **Agent 多屏草稿**：有限候选引用、具体配对与语义意图分离、TTL / 失效、纯 Agent
   路径；模型输出只能作为提案，最终提交仍由程序校验。
5. **混合修补**：草稿批量审查、单卡 / 单对替换、中间修改后的后缀复核、修改预算、
   CAS 和整体重规划；禁止修改已 COMMITTED 的批次。
6. **效果验收与界面**：中文标注与受控实验；明确插件、桌面 Web、移动 Web、CLI 的
   配置与交互合同。未验证前不把 vendor 时延或概率当作产品 SLA / 满意度。

后续实现需要继续版本化 schema 与 fixture，不能把旧规格的单模式 JSON 原样用于
新无 Plan 路径，也不能用 provider 的成功测试代替候选过滤或三模式产品验收。
