# 新平台来源接入指南

> 这份指南从 B 站、小红书、抖音、YouTube、X、知乎、Reddit、Bangumi 等首次接入及其后续修复中提炼而来，并持续吸收 Linux.do、V2EX、微博、GitHub 的后续修复，以及 Instagram、微信公众号在途分支的反例。历史分支不等于 main 已支持的能力。目标不是复制某个平台，而是让后续新增来源按能力契约完整交付，并在首版前挡住历史上反复出现的任务竞态、身份误判、假空结果、注册漂移和构建产物错位。

## 导航

- [核心原则](#核心原则)
- [Skill 执行协议](#skill-执行协议)
- [0. 定义来源契约](#0-定义来源契约)
- [1. 调研和架构选择](#1-调研和架构选择)
- [2. 后端事件和任务链路](#2-后端事件和任务链路)
- [3. 浏览器插件接入](#3-浏览器插件接入)
- [4. 配置和设置页](#4-配置和设置页)
- [5. Guided Init 和画像初始化](#5-guided-init-和画像初始化)
- [6. Discover 接入](#6-discover-接入)
- [6.5 Eval / 推荐链路接入](#65-eval--推荐链路接入)
- [7. 推荐卡三端适配](#7-推荐卡三端适配)
- [8. 测试清单](#8-测试清单)
- [9. 真实端到端验证](#9-真实端到端验证)
- [10. 文档和发布](#10-文档和发布)
- [11. 完成判定](#11-完成判定)
- [常见失败模式](#常见失败模式)
- [历史失败教训索引](platform-source-history-lessons.md)

## 核心原则

新增平台不是“加一个爬虫”，而是新增一个完整来源契约。`full` 接入只有下面适用链路都打通才算功能完备；能力在契约中确实不存在时可以写 `N/A`，但必须有理由和测试，不能用 `deferred` 代替：

- 后端事件 / 候选转换
- 浏览器插件或服务端取数路径
- CLI smoke 命令
- guided init 和画像初始化
- formal discover 调度
- 配置页和来源比例
- 桌面 Web / 移动 Web / 插件推荐卡
- LLM eval / 推荐链路中的候选兼容
- 单元测试和真实登录态 E2E
- 文档与 release-readiness；版本、tag、发布和上游写动作另需明确授权

优先复用现有平台模式，不要发明孤立路径。

| 目标 | 优先参考 |
| --- | --- |
| 登录态浏览器取数 | `extension/src/background/*-task-dispatcher.ts`、`extension/src/content/*/task-executor.ts`、`src/openbiliclaw/sources/*_tasks.py` |
| 服务端 / 直连 discover | `src/openbiliclaw/discovery/strategies/x.py`、`src/openbiliclaw/discovery/strategies/douyin_direct.py`、`src/openbiliclaw/runtime/bilibili_producer.py` |
| 初始化画像 | `src/openbiliclaw/cli.py` 中 B 站 / XHS / 抖音 / YouTube / X / 知乎路径 |
| 配置页 | `src/openbiliclaw/config.py`、`src/openbiliclaw/api/app.py`、`extension/popup/*`、`src/openbiliclaw/web/desktop/assets/js/app.js` |
| 纯文本推荐卡 | X / 知乎三端推荐卡处理 |

## Skill 执行协议

先按本轮请求选择适用阶段：新来源走完整契约；已有来源修复复用契约，只更新受影响门及跨层依赖；只读调研/审计不创建实现分支、不执行生产变更；只优化 skill/文档不新建平台契约、不跑真实账号 E2E。全量接入扫描本指南所有能力标题查漏，小修复按能力阅读，不强制重新完成无关链路。按适用阶段门推进；每一门都记录 `PASS / FAIL / NOT_RUN / BLOCKED`，适用性另记为 `required / N/A`。能力型 `N/A` 必须写明产品/架构证据并有契约测试；未获请求的 commit、发布或上游写操作用 scope/authorization 证据证明 `N/A`，不伪造测试。`deferred`、`暂未真测` 和 `以后再做` 都是 `NOT_RUN`，不是 `N/A`。

1. **范围与隔离门**：先声明 `full`、`discovery-only`、`capability-increment` 或 `audit-only`。用户只说“新增一个来源”时默认是 `full`；不能把少做的链路改名成“完整”。实现遵循仓库独立 worktree 规则，记录 HEAD、既有 diff 和相关测试基线；运行测试或 E2E 前再核对 Python import、CLI、后端 config/data root 与实际安装的扩展 build。只读审计记录所审 checkout 即可。
2. **历史取证门**：先查 [历史索引](platform-source-history-lessons.md) 中相关能力的首次接入和后续修复，再核对当前代码/测试；已有可移植证据足够时，不必每次遍历全部 session/PR。有具体缺口时补查 `git log --all`、PR/issue、可用的项目 Codex/Claude session；找不到前例就记录检索范围。session 只作线索，区分主会话请求、子代理审查、main 已合入与在途分支；历史授权不延续到新任务。不得复制 Cookie、token、账号标识或原始 transcript。
3. **来源契约门**：新来源动代码前填写 `docs/platform-source-contract.example.toml` 的副本；已有来源复用并更新原契约，不为小修复复制整份台账。冻结 slug/alias、身份、schema、transport、auth、discover、init、incremental、surface、E2E 和排除项，并从 `docs/platform-source-acceptance.example.md` 建立验收 ledger。用项目 Python 3.11+（下文 `$SOURCE_SKILL_PYTHON`）运行 audit 生成注册缺口基线；一个尚未写入 canonical registry 的全新 slug 应得到 JSON `MISSING`，不是 contract error。它只证明接线痕迹，不证明语义或 E2E。audit 的普通实现 `MISSING` 映射 ledger `FAIL`；明确指出“必须先扩共享契约才能安全开始来源实现”的 prerequisite `MISSING` 映射 `BLOCKED`。`MANUAL` 在补证据前映射 `NOT_RUN`，`N/A` 仍需 contract exclusion 与测试，不能把 audit `PASS` 直接当整门完成。audit 只确认 exclusion nodeid 可解析；验收报告还必须记录这些 nodeid 实际执行为 PASS，skip/xfail/未执行都不是 N/A 证据。
4. **上游 spike 门**：至少保存一组脱敏真实 envelope 和一个反例，验证内容类型、ID、分页/cursor、终止证据、限流和错误体。登录凭据做完整态 / 剥登录字段游客态对照；服务端、子进程和浏览器 fallback 分开证明。拿不到真实只读路径时，该能力记 `BLOCKED`，不要用臆造 fixture 证明它；继续完成其它不依赖该前提且已授权的工作。不要因缺可选凭据而阻塞已可证明的公开路径。
5. **纵向实现门**：按「canonical registry / contract → transport / normalizer → task / event / bootstrap → formal discover / eval → config / status / init → 三端 surface → docs / release-readiness」逐层落地，用失败复现或行为测试锁住每个改变的契约，再验证相邻链路。
6. **漏项审查门**：重新运行 audit，按参考平台能力做 `rg -l '<reference-slug>'` 与 `<slug>` 的差集分类，做遗漏审查。用户要求独立审查，或完整接入/高风险改动适合且允许独立审查时，让 reviewer 只看原始契约、diff、测试和真实 artifact；不要喂预期答案。小修复不自动扩成多轮对抗审查。
7. **验收门**：交付表逐项给出代码、单测、构建产物、安全真机 E2E、需要额外授权的状态变更 E2E、明确排除和阻塞。只有所声明范围的所有 `required` 行都是 `PASS` 才能写 `complete`，修复/审计完成不代表整个来源完成；已有界、安全、可用的切片但 required 行仍有 `FAIL/NOT_RUN` 时写 `incremental only`；因缺真实上游、账号/浏览器、必要授权、共享架构前置能力或其它安全前提而无法继续时写 `blocked`。文档与 release-readiness 必查，但 version/tag/push/marketplace 等发布动作只有用户明确要求时才执行。

### 能力矩阵与跨链路注册地图

不要复制“最像的平台”整套实现。一个新来源往往同时借鉴多种前例：匿名 API 可参考 Bangumi/YouTube，optional credential 可参考 Bangumi，服务端 CLI 可参考 X/Reddit，浏览器 bootstrap 可参考 XHS/抖音/YouTube/知乎/Reddit，纯文本卡可参考 X/知乎。契约中每项能力独立选择前例。

术语要分清：本指南的“四个产品面”只指 setup、桌面、移动、extension popup/side panel；contract 的八项 `surfaces` 还把 CLI、source status、credentials 与 recommendation 作为横切交付能力分别审计。`credentials=true` 表示项目存在该来源的凭据能力，不代表每个产品面都有编辑表单；移动端凭据管理当前是全局有意排除，必须在验收 ledger 中单列，不能与 mobile recommendation/status 混为一个布尔值。

新来源至少逐项审计这些中央注册点；能从 canonical registry 派生的不要再手抄，不能派生的要补集合相等或参数化测试：

- `src/openbiliclaw/sources/platforms.py`：canonical family、alias、strategy prefix、host、网络分类；跨平台 `item_key` 必须 namespaced。
- `src/openbiliclaw/api/source_auth/{providers,verify,write}.py`、`src/openbiliclaw/api/models.py`、`GET /api/sources/credentials`：provider、动作、写入/表单、status/credential/config response 的硬编码字段都要覆盖。模板路由存在不等于新 slug 已接入。
- `src/openbiliclaw/web/shared/source-status.js` 的 `SOURCE_KEYS`、`src/openbiliclaw/runtime/init_prereqs.py`、`source_policy.py`、`refresh.py`、CLI dispatch/help、config load/save/API round-trip。
- 有 search 时：`runtime/keyword_planner.py`、`llm/prompts.py`、`runtime/inspiration_pipeline.py`/allocation targets、`runtime/keyword_fetch.py` 与对应生成/claim 测试。
- 有账号 bootstrap 时：`runtime/init_prereqs.py::_PLATFORM_SOURCE_FIELDS`、CLI 精确 `--yes/--no` 选项、init callback allowlist、`sources/source_bootstrap.py` 的 task table/enqueue/seed 规则、`sources/bootstrap_state.py`、`sources/task_result_protocol.py` 以及 source task transaction/staged-completion 参数化测试。
- 有周期回拉时：`runtime/source_incremental_sync.py` 的 source order/task spec/config alias/interval、热重载与文档契约测试。
- 有浏览器任务时：Chrome/Firefox manifest、build entry/WAR、service worker、dispatcher mutex、cookie/login sync、init write allowlist、隐私与商店 listing。
- 有 native save、封面代理或移动 deep link 时，分别审计 `saved_sync` identity/runner、`runtime/image_cache.py` host allowlist/direct-fetch、`web/js/app-launch.js`。
- 审计开发/营销快照：`scripts/source_contract_metrics.py` 只量化 auth 重构，不能替代总审计；README、首页、架构图、商店资产中的“几平台”文案也必须与真实能力一致。

## 0. 定义来源契约

动代码前先写清楚：

- 接入等级：`full / discovery-only / capability-increment / audit-only`；默认“新增来源”是 `full`。
- `slug`：平台全局 key，如 `zhihu`，必须在配置、事件、候选、UI 中一致。
- canonical 映射：列出 alias、host、strategy prefix，以及历史兼容所需的 transport route alias；每个 alias/host/prefix 的 exact owner 必须全局唯一，否则 registry 的先后顺序会把本源归给别的平台。route alias 还必须是同一 `SourceFamilyRule` 已拥有且全局唯一的安全 ASCII route key，不能借别的平台 alias，也不能含路径段。写库一律只存 canonical family，不能假设 slug 在所有旧 URL 中都字面一致。
- 内容单元：视频、笔记、推文、回答、文章、问题、帖子等。
- 内容身份：平台 item ID、canonical URL、跨 scope 去重键、账号身份边界和 `item_key` namespacing；comment/reply 不得退化成 parent post 身份。
- 事件类型：`view`、`like`、`favorite`、`follow`、`comment`、`share`、`dislike` 等。
- discover 模式：`search`、`hot`、`feed`、`creator`、`related` 或平台等价模式。
- 取数方式：官方 API、服务端 cookie replay、浏览器插件登录态、导入文件或混合方案。
- 上游响应：成功的 content-type/envelope、分页字段与终止证据，以及 401、429、200 HTML challenge、错误 JSON、删帖和字段缺失的分类。
- 鉴权形态：必须明确是「匿名即可」「必须登录」「匿名可用 + 可选凭据增强」还是「逐能力不同」。最后一种用于 public discover 匿名、private bootstrap/incremental 登录之类的 hybrid 来源，不能被粗暴压进一个全源布尔值。
- 账号解析阶梯：如果同一账号可从令牌、显式用户名、已保存配置和扩展身份获得，先定义唯一优先级并由后端执行；客户端不得各复制一份准入判断。
- 身份证据：明确哪些字段只是页面观察值，哪些经过上游正面确认；`observed`、`accepted`、`verified` 不得混用。
- 是否只读：默认只读，不主动改变用户平台状态。
- 时间语义：平台原始发布时间如何转为 UTC `published_at`，来源和精度如何保留；`discovered_at` 不能冒充发布时间。
- 每个分支额度：按真实来源分支独立定义，不要因为多个分支最后都映射成 `favorite` 就共享上限。
- 状态变更边界：哪些 E2E 动作只读 / 安全，哪些会改变账号状态，必须提前写清楚。
- 安全动作命名只能采用 audit 的精确只读语法：`snapshot / scroll / search / hot / related / feed / ranked / latest`，`read-identity`，`read-public-{collection,feed,profile,item,page}`，`fetch-public-{collection,feed,profile,item,page,search,ranked,latest}`，`navigate-public-{item,page,profile}`，`open-public-link / copy-public-link / open-share-panel / close-share-panel`。每项必须在 `[e2e.safe_assertions]` 精确声明 `upstream-state-unchanged`，自由文本 postcondition 只记录可观察证据、不能作为机器授权。未知、非 ASCII 或平台专属动作默认归入 mutating；`watch-later`、星标、转发、中文「点赞」等不能靠避开英文 denylist 获得默认授权。
- 三端内容卡形态：有封面、无封面文字、长文本、外链、评论 / 帖子 / 回答等特殊类型。
- 统一字段和文案：跨平台作者写 `author_name`，稳定 ID 写通用内容 ID；`UP主`、`BV号` 等平台专属术语只能在对应平台展示。
- 网络所有权：注明请求实际由项目 HTTP client、第三方子进程还是浏览器发出；配置提示只能推荐真正影响这条传输路径的代理设置。
- 完整性与终态：分别定义 `ok / empty / degraded / login_required / rate_limited / failed`，`scope_complete` 的证明，以及 partial rows、cursor、retraction 如何处理。只有确实观察到合法空响应才可写 `empty`。
- 画像刷新生命周期：`incremental / init-and-on-demand / on-demand / init-only / none`；`discovery-only` 是 integration level，不是把未实现刷新换个名字。账号 bootstrap 信号若会进入画像，必须选择并测试刷新生命周期；`init-and-on-demand` 等明确生命周期是有效产品选择。实现 incremental 能力不代表默认启用周期任务，须保持 `source_incremental_enabled=false`。
- smoke 写入预算：`[smoke] storage_scope` 必须是 `isolated-only`，并在 `[smoke.sinks]` 对 `task / task_result / seen / affinity / snapshot / schedule / event_ingress / memory / profile` 九项逐一写 `allowed / forbidden`；“不写 memory”不等于无副作用，笼统的“临时数据库可写”也不能替代逐 sink 冻结。
- 浏览器执行模型：是否支持 inactive/hidden tab、是否依赖渲染、SPA/full-navigation 恢复、任务 marker 的存活范围和必要的用户前台切换。
- engagement 计数逐项声明：`view / like / favorite / comment / share / danmaku` 六项，逐项写清「平台可提供并映射」还是「平台结构性缺失」。结构性缺失（如非 B 站的 `danmaku`、Reddit 的 `view`）合法留 0，前端不渲染、不做占位，不当 bug 修。可映射的计数必须在该平台**所有** fetch 子路径（feed / search / bootstrap / creator …）一致填充——同一内容在 A 路径有计数、B 路径全 0 是真实缺陷（参考 `docs/plans/2026-07-07-engagement-stats-completeness-spec.md` 的跨平台矩阵和三类成因）。
- 登录判定 cookie：声明哪个 cookie 名代表真实登录（例如 XHS `web_session`、知乎 `z_c0`、Reddit `reddit_session`），游客 cookie（`a1` / `_xsrf` 之类）不算。cookie 存在最多是新鲜的 `observed` 正向提示；权威当前 API 的 401、login wall 或 session-current 否定必须覆盖旧心跳。只有没有当前权威信号时，任务历史等间接证据才可兜底。

如果平台依赖登录态，优先走浏览器插件任务链路。真实 E2E 要使用安装了插件且已有登录态的浏览器，不要用 MCP/CDP 临时浏览器代替，除非用户明确要求只做普通 UI 自动化。

### 0.1 接入契约必填项（有强制点，不是建议）

前面的来源契约长期只有原则、没有统一强制机制——早期平台各写各的，最后靠 `api/app.py` 里一个 400 多行的 if/elif 手工拍平，新平台全靠手抄上一个。所以新增平台时，下面这组字段**必须**在 `src/openbiliclaw/api/source_auth/providers.py` 里显式填写，并由 contract audit 与 canonical-set 测试证明覆盖。注意：既有 `tests/test_source_auth_contract.py` 主要遍历已经登记的 registry；如果 provider、verify action 和 credential spec 同时漏掉一个新平台，旧参数化测试可能全部通过，不能把绿灯当成注册完整性的证据。

先判定全源 `auth_required` 是否真的足够表达产品语义；`[auth.capability_required]` 要把必交付能力与可选 fallback 分开：

- 全部 required capability 都匿名，写 `auth.mode="anonymous"`，运行时 `auth_required=false`。
- 同一批公开能力匿名可用、凭据只增强配额或私有字段，写 `auth.mode="anonymous-with-optional-credentials"`，运行时仍是 `auth_required=false`，但 optional credential 的验证结论必须常驻展示。
- 全部 required capability 都必须登录，写 `auth.mode="login-required"`，运行时 `auth_required=true`。
- public discover 匿名、browser bootstrap/incremental 必须登录等混合情形，写 `auth.mode="capability-specific"`，并在 `[auth.capability_modes]` 对每项 active capability 使用 `anonymous / optional-credential / login-required`。共享后端现已提供 `SourceCapabilityAuth` 与 `SourceAuthContract.capabilities`，V2EX、Linux.do、微博已有逐能力实现。复用 `mode / required / ready / state`，让 status、setup、init 读取本次能力；旧 `auth_required` 只作兼容投影。**只有当前 checkout 的共享契约或消费者确实无法表达目标能力时，本门是 `BLOCKED`；先补共享前置能力，不要把历史的“共享模型尚不存在”当作今天必须重建模型的理由，也不得让前端各自补 guard**。新来源自身的 provider/roster/投影漏项是本次实现待办，不构成外部阻塞。

`capability_modes` 与 `capability_required` 的键必须覆盖契约实际启用的 `discover / profile / bootstrap / incremental / identity` 等能力；自由文案只能放在说明字段里，不能用一段看似完整的字符串代替机器语义。仅作为另一条账号解析 fallback 的登录 identity 可以标 `required=false`，不会单独把整个来源升级成 capability-specific；匿名 discover 与必交付的登录 bootstrap/incremental 则必须升级。

三张 capability 表的 key 必须与启用能力**精确相等**，不能多声明一个实际关闭的 `incremental` 再把实现审成 N/A；`discover`、`profile/bootstrap`、已选择的 `incremental` 和 `native-save` 也不能靠把 `capability_required=false` 自行降级。transport route 若声称由 `browser-task` 拥有，`extension.task` 必须真是 `browser-task`，反向也必须至少有一条能力路由说明这个任务执行器服务谁。

| 字段 | 必须回答的问题 | 允许留白吗 |
| --- | --- | --- |
| `auth_required` | 这个源到底需不需要凭据 | 否 |
| `credential` | 凭据在不在（`none` / `present` / `invalid`） | 否 |
| `credential_origin` | 凭据存在哪（config / env / data_file / extension / external_cli） | 否 |
| `verification` | 最近一次验证的结论 | 否 |
| `verify_method` | **这个结论有多硬** | 否 |
| `verify_ttl_seconds` | 结论多久过期，`None` = 不过期 | 可 `None`，但要有理由 |

`verify_method` 是其中最关键的一个，取值即证据强度：`live_probe`（真出网）> `passive_health`（由真实流量的错误反推）> `browser_heartbeat`（插件报登录 cookie 存在）> `local_file`（只读本地文件）> `task_history`（由历史任务反推）> `none`（无验证能力或不需要）。

**填 `none` 之前必须先做剥离对照实验**：实验组 = 完整凭据，对照组 = 剥掉登录 cookie 后的游客态，同一签名器 / UA / 时刻，唯一变量是登录 cookie。拿不出对照数据就不许在代码或 docstring 里声称"该平台无法验证"。

这条规则是有代价换来的：抖音的 `api/app.py` docstring 曾断言它"没有稳定的 nav 端点能区分未登录和软风控"，于是整个平台的登录态显示误报了很久，而且这句话还成了后续所有人不去修的理由。2026-07-18 的对照实验只花了几分钟就推翻了它——`/aweme/v1/web/user/profile/self/` 已登录返回 `status_code=0` + 非空 uid，游客返回 `status_code=8` "用户未登录"，干净得很。

对照实验本身也要防伪：同一次实验里 `/aweme/v1/web/query/user/` 在两组返回**完全相同**的 12 位 uid（那是设备级标识，由 `ttwid` / `odin_tt` 驱动）。只看"有没有返回 uid"会得出"可以验证"的错误结论。**判据必须是两组之间有差异，而不是单组看起来正常。**

**第三种形态：匿名可用 + 可选可验证凭据（Bangumi）。** `auth_required` 是布尔，但有的源既不「必须登录」也不「无凭据可验」。Bangumi 公开收藏 / 排行匿名即可发现（`auth_required=false`），但配了个人令牌就能验证令牌——`GET /v0/me` 有效令牌返回账号、无效令牌返回 `unauthorized`。接法：`auth_required` 恒 `false`，配了令牌才 `credential=present` + `verify_method=live_probe`；`verify_method` 因此**随状态在 `none`/`live_probe` 间变**，而 `VERIFY_ACTIONS` 里的动作固定为 `live_probe`。共享 renderer 现在会通过 `hasVerifiableCredential()`、`describeAuthVerdict()` 和 `describeEvidence()` 展示 optional credential 的常驻结论与证据；新增同类来源必须覆盖无凭据、有效、失效、网络不确定和 disabled-with-stored-credential，不能复活“`auth_required=false` 就隐藏证据”的旧行为。详见 `docs/modules/source-auth.md`「Bangumi 的接入」。

历史设计与诊断背景见 `docs/plans/2026-07-18-source-auth-contract-spec.md`；plan 记录当时方案，不是当前规范。与本指南或当前代码/测试冲突时，以本指南的阶段门和当前实现为准。

### 0.2 验证动作：`POST /api/sources/{slug}/verify`

新平台除了在 `providers.py` 填契约字段，还必须在 `src/openbiliclaw/api/source_auth/verify.py` 的 `VERIFY_ACTIONS` 里登记一个动作，否则第一次点「测试连接」就是 KeyError。`tests/test_source_auth_contract.py::test_verify_action_table_covers_every_platform` 断言两张表键集合相等。

| 动作 | 语义 | 当前平台 |
| --- | --- | --- |
| `live_probe` | 当场出网探测 | bilibili、douyin、twitter、bangumi（有可选令牌时） |
| `passive_health` | 汇报真实流量已经得出的结论，不出网 | 暂无（保留给确实没有主动探针的来源） |
| `browser_heartbeat` | 经 WS 发 `*_login_state_sync_requested`，等插件回报（至多 5s） | xiaohongshu、zhihu |
| `local_file` | 重读本地凭据文件，不跑子进程 | reddit |
| `none` | 没有可验证的东西 | youtube |

`browser_heartbeat` 不是“除了小红书就当知乎”的通用 fallback。除了 `VERIFY_ACTIONS`，还必须把 source → event/database prefix 登记到 `verify.py::_BROWSER_HEARTBEAT_PREFIXES`，实现对应 `get_<prefix>_login_state()`、扩展 runtime-stream `<prefix>_login_state_sync_requested` handler 和来源专属往返测试。审计必须逐项确认这些接线；缺任何一项时不能因为动作枚举存在就 PASS。

X 的 `live_probe` 只调用 `twitter-cli` 的只读 `fetch_me()` 账户状态接口，不执行点赞、收藏或其它写操作；发现链路仍会把真实请求结果写入 `XSourceHealthStore`，所以状态页既能反映后台流量，也能被「测试连接」主动刷新。

三个容易写错的地方：

1. **动作是平台的固定属性，不等于契约里的 `verify_method`。** 后者描述「当前这个结论是怎么来的」，会随状态变化——知乎有插件心跳时是 `browser_heartbeat`，没有时回落 `task_history`。但点按钮要做的事永远是「请插件重新上报」。按 `verify_method` 分派会让知乎在最需要验证的时候反而没有可执行动作，还会凭空造出一个「重跑历史」这种不存在的操作。
2. **响应里 `outcome` 和 `auth.verification` 回答的是两个问题。** 前者是「这次点击验证到了什么」，后者是「我们现在相信什么」。插件没连时，一小时前的心跳仍然让 `verification=verified`，但这次点击什么都没验证到，所以 `outcome=indeterminate`。把两者合并会渲染出绿色「已验证」配上「插件未连接」的文案。
3. **三态，不是两态。** 探测超时、插件没回、平台限流、YouTube 无需登录——全部是 `indeterminate`，不是 `failed`。把「判定不了」显示成「凭据失效」，会让用户去删一份好好的 cookie。

去抖：每平台 10 秒，窗口内重复调用重放上次结果且不产生任何出网请求；并发点击另有 in-flight 标记拦截。用户能按住的按钮如果不挡，就是自己给自己造风控。

重放必须能被前端认出来，所以响应带两个字段：`replayed`（这次调用有没有真的干活）和 `retry_after_seconds`（还要等多久才会重新探测）。少了它们，一次被去抖的点击和一次真探测在响应里逐字节相同 —— 刚修好 Cookie 的用户再点一次，拿回缓存里的旧失败，会以为没修好。`retry_after_seconds` 由后端下发而不是前端各写一个 `10`，理由同 I4：两端各存一份常量，就是下一次漂移。

### 0.3 桌面与插件「测试连接」按钮（移动端有意排除）

桌面 Web（`web/desktop/index.html` 的 `.source-status-row`）与插件 popup（`popup/popup.html` 的 `.settings-source-card`）每个平台各一个按钮，走同一套 DOM 约定：`renderProbePending()` → await → `renderVerifyResult()`，tone 写在 `dataset.tone`、文案写在 `textContent`，与「模型」tab 的 LLM 探测共用 `setProbeStatus()`。移动端当前有意排除凭据管理与「测试连接」；该排除必须写进规格和契约测试，saved-sync 等消费面仍要能展示真实登录需求。

三条硬规矩：

1. **tone 三态**：`verified` → `success`（绿）、`failed` → `error`（红）、`indeterminate` → `neutral`（蓝）。`neutral` 是本次新增的 CSS tone，**不能复用 `pending`** —— 那是「探测中」的灰，让终态和加载态长得一样。
2. **文案 100% 来自后端 `message`**，前端不得出现平台专属字符串（I4，指标脚本第 2 项会抓）。前端唯一自造的文案是「连不上我们自己的后端」那一条，且同样按 `indeterminate` 渲染 —— 后端连不上不能说明平台凭据坏了。
3. **状态标签与本次结论分开渲染**：`auth.verification` 驱动上方的「接入：…」badge，`outcome` 只驱动按钮旁那行字。小红书 / 知乎在插件没连时就是这个样子 —— badge 保持「状态待验证」，按钮旁是中性的「5 秒内没有收到回报」，绝不是绿色「已验证」配「插件未连接」。

按钮点完进入可见倒计时（`测试连接（10s）`）并 disable，长度取自 `retry_after_seconds`；真落进去抖窗口的点击（另一端点的、或刷新页面后的）文案会追加「沿用刚才的结果，本次未重新探测」。

### 0.4 凭据写入：`POST /api/sources/{slug}/credential`

新平台**不要**再新开一条写入路由。统一入口一条：

```
POST /api/sources/{slug}/credential
body  {kind: "cookie" | "token" | "login_state", value, source}
→ 200 {accepted, error_code, message, persisted, checked, unverified_reason, cookie_names, auth}
```

流程固定为：**结构校验 → 活体校验（有能力的平台）→ 落盘 → 广播 → 重算契约一并返回**。最后一步就是「保存后零回执」的解法——`auth` 是写完之后重新算出来的契约，所以保存页和下一次状态轮询不可能给出两种说法。

新平台要在 `src/openbiliclaw/api/source_auth/write.py` 的 `CREDENTIAL_SPECS` 里登记一条，字段含义：

| 字段 | 回答什么 |
| --- | --- |
| `kinds` | 统一 `/credential` 接受哪几种写入；空元组 = 此端点不接收（youtube 无凭据，Bangumi 是需显式说明的 config-only 兼容例外） |
| `required_keys` / `any_of_keys` | 结构校验用的 cookie 名，**必须与 `providers.py` 里数的那组一致** |
| `opaque_credential` | 凭据是否是一整串不可拆字段的 secret；用于按凭据生成非明文指纹 |
| `invalid_error_code` | 结构不合格时的机器可读码 |
| `live_gate` | 落盘前要不要真出网探一次 |
| `unverified_reason` | `live_gate=False` 时**必填**：说明为什么这个平台的写入无法确认 |

当前强度对照：

| 平台 | kind | 结构校验 | 活体校验 | 落到哪 |
| --- | --- | --- | --- | --- |
| bilibili | cookie | SESSDATA + bili_jct + DedeUserID | nav 探针 | `data/bilibili_cookie.json` + config.toml 镜像 |
| douyin | cookie | sessionid / sessionid_ss / sid_tt 至少一个 | profile/self 探针（D11） | `data/douyin_cookie.json` |
| twitter | cookie | auth_token + ct0 | `fetch_me()` 只读探针 | `data/x_cookie.json` |
| reddit | cookie | reddit_session | 无（只读本地文件） | rdt-cli 凭据库 |
| xiaohongshu | login_state / token | 类型即全部 | **架构上不可能** | DB 布尔位 / 内容令牌 |
| zhihu | login_state | 类型即全部 | **架构上不可能** | DB 布尔位 |
| youtube | — | 不接受写入 | — | — |
| bangumi | token（config / init 专用） | 非空、单行 ASCII、长度上限 | `/v0/me` | config.toml；现有兼容路径不经统一 `/credential` |

四条容易写错的地方：

1. **拒绝必须有证据。** 结构不合格是证据，平台明说「未登录」是证据。传输失败**不是**关于凭据的证据——但在能验证的平台上它仍然拒绝落盘，返回 `validation_network`（插件本来就对这个码做退避重试）。一份我们没能验证的凭据静默落盘，正是这个模块要防的事。
2. **`checked` 字段必须诚实。** `live_probe` / `structural` / `none` 三档，加上 `unverified_reason`。小红书和知乎后端只存一个布尔位，一个字节的 cookie 都没有，所以它们的写入永远是 `checked="none"` 并显式说明原因——不许因为「返回 200 好看」就假装校验过（I3/I5）。
3. **`kind="token"` 不是凭据。** 小红书的 `xsec_token` 是单篇笔记的内容访问令牌，跟账号登没登录毫无关系（spec D5）。单独设一个 kind 就是为了不让它继承登录凭据的那套承诺。
4. **`PUT /api/config` 也是凭据写入端点。** 它的路径叶子是 `config`，按词根扫描一个都看不见，但它会写入多个平台凭据，而且设置页手工粘贴走的正是这条路。与统一端点重叠的来源委托同一个 `validate_credential()`，所以两条路径对同一份凭据给出相同的 `error_code`（`tests/test_source_auth_contract.py::test_write_paths_have_equal_validation` 锁死）；Bangumi 的既有 config-only 令牌也必须在保存前经 `/v0/me` 活体校验。判断「这是不是凭据写入端点」的依据永远是**它会不会把用户凭据落盘**，不是路由名（I7）。

`PUT /api/config` 保留自己的落盘代码，因为它正处在 config.toml 的事务中间，不能让共享写入器把它没保存完的编辑刷出去。它必须为每个字段明确**局部更新协议**：省略或掩码表示「没编辑」；允许清除的字段用显式空字符串表示「删除」（Bangumi `access_token` 已采用）；仍沿用「空值不覆盖」的旧 cookie 字段要在表单与测试里明确。不能用一个全局 truthy 判断混掉三种语义。

可选凭据还要有完整失效生命周期：运行期收到权威 401/403 时，可匿名降级的来源应移除本轮 Authorization 后继续公开路径，同时把 `token_state` 标成 rejected，而不是每次重启都拿同一死令牌撞上游。持久化时只保存不可逆的非秘密指纹，用来识别「还是同一份死凭据」；令牌轮换、验证成功或显式清除都要清掉旧标记。网络失败和限流是 indeterminate，不能写成 rejected。任何日志、状态和测试 fixture 都不得泄漏明文凭据。

探针缓存和成功证据同样属于**某一份凭据**，不属于平台 slug。Cookie 指纹只覆盖 `CREDENTIAL_SPECS` 声明的登录字段（避免无关设备 token 轮换导致每次重探），opaque token 则覆盖整串 secret 的哈希；凭据变化必须立即失效旧去抖结果、旧成功和 in-flight 状态。缓存命中不能刷新 `verified_at`，否则每次插件重发都会让一份从未复验的凭据永久显示「刚刚验证」。

旧端点（`/api/bilibili/cookie`、`/api/sources/{dy,x,reddit}/cookie`、`/api/sources/xhs/tokens`、`/api/sources/{xhs,zhihu}/login-state`）全部保留为内部转发，响应逐字段不变（装着的插件在解析它们），并标 `deprecated=True`。这个标记不是文档修饰：`scripts/source_contract_metrics.py` 第 5 项只数**未标 deprecated** 的凭据写入形态，不标就永远显示「4 种命名」。

### 0.5 表单描述符：`GET /api/sources/credentials` 的 `form`

前端不该知道「小红书能不能粘贴 cookie」。新平台在 `CREDENTIAL_SPECS` 里补 `form_*` 字段，端点会把它投影成 `form` 描述符下发，三端照着渲染：

| 字段 | 回答什么 |
| --- | --- |
| `kind` | `cookie_textarea` / `token_input` / `extension_only` / `none`。**这是能力声明，不是布局偏好** |
| `label` / `placeholder` / `help_text` | 表单文案，全部来自后端 |
| `env_var` | 这个平台认哪个环境变量；`null` = 不认（B 站今天就是 `null`） |
| `required_keys` + `required_keys_mode` | 结构校验要哪些 cookie 名，`all` 还是 `any` |
| `actions` | 这个平台**真正做得到**的按钮 |

四条硬规矩：

1. **描述符是派生的，不是另写一份。** `required_keys` 直接来自 `CREDENTIAL_SPECS` 的 `required_keys` / `any_of_keys`，`build_credential_form()` 只做投影。表单说要三个 cookie、校验器只要一个，就是 D6 的漂移换个楼层重演一遍。
2. **`required_keys_mode` 不能省。** 抖音三个 session cookie 是**任选其一**，把它们平铺成 `required_keys` 会让 UI 声称校验器要求它其实不要的东西。spec 初稿的字段表里只有 `required_keys`，实现时发现表达不了，所以补了 mode——描述符宁可多一个字段，也不许说谎。
3. **`extension_only` 是绑定的。** 后端一个字节的小红书 / 知乎 cookie 都不存，所以这两个平台**不许渲染出可粘贴的输入框**。给一个能填的框，是在骗用户往虚空里打字。它们仍然有 `verify` 和 `open_login_window` 两个动作——「去浏览器登录」才是这两个平台唯一有效的修法。
4. **动作即能力，没有的不许挂。** 通用描述符目前只有 `verify` / `copy` / `open_login_window`。`clear` 只有在后端存在明确删除语义后才能下发；Bangumi 已通过 `PUT /api/config` 的 `access_token: ""` 和三端清除控件实现，但尚未成为通用 descriptor action。新来源若承诺清除，必须先实现端点、清理验证 / rejected 缓存并补跨端契约测试；否则不显示按钮。

`summary`（凭据行那句话）同样由后端下发。小红书那句「已保存，但不代表账号登录」以前是桌面页里的一个平台特判，于是只有桌面页说得出这句话，插件和引导页都不知道。

### 0.6 三端共享渲染模块 `web/shared/source-status.js`

状态 → 文案/色调的表**只有一份**，在 `src/openbiliclaw/web/shared/source-status.js`。三个前端都加载它：

| 前端 | 加载方式 |
| --- | --- |
| 桌面 Web | `<script src="/shared/source-status.js" defer>`，在 `app.js` 之前 |
| 插件 side panel | `<script src="shared/source-status.js">`（classic，在 `popup.js` 模块之前）；文件由 `extension/scripts/build.mjs` 在每次 build 时复制进包 |
| setup 引导页 | `<script src="/shared/source-status.js">`，在内联脚本之前 |

几个要点：

- **`/shared` 是独立 mount。** `/web` 挂的是 `web/desktop/`，所以 `web/shared/` 下的文件从 `/web/shared/…` 取不到（只能从 `/m/shared/…`，那是移动端 mount）。跨端共享的资源不该走某一端专属的 URL。
- **插件必须用复制，不能用 HTTP 拉。** MV3 默认 CSP `script-src 'self'` 禁止从后端加载脚本，所以同一个源文件有两条投递路径：HTTP（桌面 / 引导页）和 build 期复制（插件）。复制产物 `extension/popup/shared/` 已 gitignore——**不要提交它**，一提交它就变成第四份手抄副本。
- **共享的是本枚举，不是所有相邻枚举。** 判据是「这张表的键是不是 `/api/sources/*` 发出来的字段值」。saved-sync 的任务状态表（`saved-sync-core.js` 等 6 处）跟本枚举共用 `login_required` / `rate_limited` 两个**拼写**，但回答的是「这一条收藏同步成功没有」，不是「这个源接不接得上」，所以不合并。引导页的 `INIT_REASON_TEXT` 同理（那是初始化前置条件枚举）。
- **依赖是硬的。** 模块缺失会让整个页面挂掉（与既有的 `saved-sync-core.js` 一致）。所以任何自建 HTTP stub 的 E2E 测试都必须加 `/shared/` 路由，否则 404 会以「某个不相关的测试超时」的形式暴露出来——`tests/test_desktop_web_autoload_margin_e2e.py` 等三个 stub 已加。

### 0.7 身份主张与验证证据

Bangumi 暴露了一个容易被忽略的边界：页面上看到 uid / 用户名，只能说明扩展**观察到了一个主张**；只有权威上游正面确认两者属于同一账号，才能写 `verified=true`。

实现身份通道时遵守下面规则：

- `verified=true` 只来自正面匹配。404、uid 不一致、空用户名、限流和网络失败分别是「否定」或「无法判断」，都不是验证成功。
- 匿名可用来源可以 fail-open 保持可用，但必须持久化为 `verified=false`、返回 warning，并给出显式用户名 / 令牌等纠正路径；不能为了可用性把未确认身份涂绿。
- 已验证证据只对**完全相同且合法的身份主张** sticky：瞬时网络失败不能把它降级；uid 或 username 改变则旧证据失效。升级前的缺字段、空用户名却 verified 等非法记录要在读取时归一为未验证，不能盲目继承。
- 合并、持久化和响应必须基于锁内重读的权威状态；写盘失败就返回失败，不能 200 回一个磁盘上不存在的状态。
- DOM 身份只能从「当前用户本人」专属区域取值。通用头像 / 用户链接 selector 会把时间线里的陌生人当本人；页面全局变量若只存在 MAIN world，应使用最小桥接并严格校验消息来源与字段。
- 浏览器 Cookie 登录、公开用户名、PAT / OAuth 是三条不同能力路径。浏览器已登录不代表服务端 API 会接受 Cookie；反过来，公开 API 匿名可用也不代表可选令牌无需验证。
- 证据有方向和时效：cookie 名存在、旧任务成功和旧心跳只是正向提示；本轮权威端点返回 401、当前 login wall 或 session-current 否定时必须立即压过旧提示。瞬时网络错误是 `indeterminate`，不能把好凭据抹掉，也不能沿用旧绿灯冒充本轮验证。
- 匿名传输必须是负能力测试：显式剥离 `Cookie`、`Authorization` 和第三方环境注入的 credential，必要时关闭 `trust_env`；证明公开来源不会因为开发机恰好登录而偷偷变成私有路径。

若来源支持多条账号路径，把优先级写成单一后端函数，例如：`token /v0/me > 本轮显式值或配置值 > 扩展身份 > 可操作错误`。所有 CLI、setup、桌面和插件入口只负责传递用户输入并展示后端结论。

## 1. 调研和架构选择

1. 查是否有稳定官方 API 能拿到目标信号。需要联网时优先官方文档 / 一手资料。区分产品想读取的主体：当前读者、公开账号、内容发布者；发布者 AppID/AppSecret 能读取的自有内容，不等于当前读者的收藏或任意账号历史。缺凭据时先完成不依赖它的路径，不擅自将用户要求的完整来源缩成官方 API 子集。
2. 没有稳定 API 时，参考 XHS / 抖音 / YouTube / 知乎 / Reddit / V2EX 的浏览器插件任务模式：
   - 后端入队任务；
   - 插件打开或复用真实平台 tab；
   - content script 读取 DOM 或同源 JSON endpoint；
   - 插件把规范化结果 POST 回后端；
   - 后端再转换为统一事件或 discover 候选。
3. 如果选择第三方 CLI / SDK 作为默认后端，默认安装必须真正带上它：
   - 把依赖加到 `pyproject.toml` 默认 `dependencies`，更新 lockfile，并用项目虚拟环境实际安装验证；
   - 一键 AI 安装、本地脚本安装、Docker 构建若走 `pip install .` / `uv sync` 会自动吃默认依赖；如果某个安装入口绕过 `pyproject.toml`，要同步补清单；
   - 桌面 / PyInstaller 安装包还要显式收集 lazy / subprocess 依赖；如果冻结包没有 console script，要提供 in-process fallback 或把可执行文件打进包里；
   - 如果第三方 CLI / SDK 需要浏览器 Cookie，优先复用已连接 OpenBiliClaw 插件的 `chrome.cookies` 同步能力，把必要 Cookie 写入该工具的本地 credential store；手动 `login` 命令只能作为 fallback，不能成为有插件登录态时的唯一入口；
   - 用真实 `--help` / 源码确认命令、参数、结构化输出格式，不要凭 README 或记忆猜子命令；
   - smoke / producer 要输出 JSON/YAML 等机器可解析格式，并补单测锁定真实参数；
   - 状态探测不能隐式触发登录、浏览器 Cookie 提取或其他长耗时副作用；缺本地凭据时应返回 `login_required` 并提示显式登录命令；
   - 命令脚本通常装在虚拟环境 `bin/` / `Scripts/`，用户可能直接运行 `.venv/bin/openbiliclaw` 而没有激活 venv，`shutil.which()` 之外还要查当前 Python 环境的脚本目录。
4. 按**真实传输层**选择网络策略：
   - 项目 HTTP client 才能直接复用 `[network]` 的 `system` / `custom` route；
   - 第三方 CLI / 子进程是否继承代理，要按它实际读取的环境变量或参数验证；
   - 浏览器请求由浏览器网络栈负责，后端代理开关不一定生效；
   - 错误提示应在最低层网络失败处附上真正可操作的配置项，使 CLI、producer、init 都继承同一提示；分别测试 `system`、`custom` 和不应继承代理的国内 client。
5. 先做最小 smoke：
   - `fetch-<slug>` 或 `discover-<slug> <keyword>`；
   - 默认不写 memory、不触发画像；
   - 终端打印分支计数和失败原因；
   - 后端持久化任务结果，方便状态页和 debug。

上游 spike 必须先于完整 adapter，并留下可复查的脱敏 fixture：

- 保存真实成功 envelope、至少一个错误/空/字段漂移反例；分页来源至少有第一页、后一页和终止页。fixture 要保留 HTTP status、content-type、外层 shape、cursor/has_more 与字段类型，删除 Cookie、token、账号 ID、签名 URL 和非必要正文。
- 解析前先判 status、content-type 和 magic bytes。`200 text/html` 的登录页/验证码不能交给 JSON normalizer，更不能转成 `ok + 0`；JSON 外层 error/status 也不能只因为 HTTP 200 就视为成功。
- JSONP 只接受严格 callback 包裹并用 JSON parser，禁止 `eval`；XML/RSS 要限制大小并禁 DTD/外部实体。任何 parser fallback 都要有明确边界和反例。
- fixture 只能锁住已经观察到的协议，不能自己造一个 client 和一个 fixture 相互证明。至少一次真站采样要断言真实 envelope、分页/终止和 normalized row 同时成立；上游变更后先更新取证，再更新 fixture。
- 对 login-required/optional-credential 路径做同条件完整凭据与剥离凭据对照；对 public anonymous 路径反向证明没有隐式携带开发机凭据。项目专用凭据与开发工具凭据分开；例如 GitHub 来源不能顺手继承 `GH_TOKEN`、`GITHUB_TOKEN` 或 `gh` 登录来掩盖未配置状态。
- 分页目的地按实际 auth 模式验证：GitHub PAT 的 starred Link 曾从 `/users/<username>/starred` 变成 `/user/<id>/starred`。既不能因首个路径固定而拒绝同资源合法别名，也不能为兼容放开任意 host/path；回归覆盖精确安全变体、跨源跳转、非单调 cursor 和循环 Link。
- 用户可配置 Feed/URL 时，网络边界覆盖 Feed 本身、redirect 和 enclosure/image；沿每一跳校验地址并明确 DNS rebinding 策略。XML 安全检查要覆盖实际编码（包括 UTF-16）、DTD/实体、大小与解压上限。静态字符串扫描不是安全 parser；微信在途分支的反例见历史索引，先核对当前实现再复用。

## 2. 后端事件和任务链路

常见文件：

- `src/openbiliclaw/sources/<slug>_tasks.py`
- `src/openbiliclaw/runtime/<slug>_producer.py`
- `src/openbiliclaw/sources/event_format.py`
- `src/openbiliclaw/sources/bootstrap_state.py`
- `src/openbiliclaw/api/app.py`
- `src/openbiliclaw/api/models.py`
- `src/openbiliclaw/api/runtime_context.py`
- `src/openbiliclaw/cli.py`

### 2.1 Canonical row、事件与持久结果

必须满足：

- 平台原始 row 转成统一事件时带 `source_platform=<slug>`。
- metadata 保留平台稳定 ID、URL、作者、来源分支、原始互动动作等可解释字段。
- 外部 schema 必须逐层校验容器类型：字符串不是列表，字典不是作者名。不要对任意嵌套值直接 `str()`；否则会把字符逐个迭代，或把 `["作者"]` / `None` / `{...}` 固化成脏数据。
- 占位值过滤只覆盖本栈确实可能产生的伪值，并用真实反例锁住。`nil`、单字名、标点名、非拉丁字符都可能是真实作者；「看起来奇怪」不能作为删除规则。
- 先抽样真实响应确定字段优先级，优先复用列表行已有的 inline infobox / credit 字段；如果 detail endpoint 没增加有效信息，不要为每行制造 N+1。
- 统一作者字段用 `author_name`；旧存储列或兼容别名可以保留，但 CLI、API 和 UI 不得把非 B 站作者称为 `UP主`。
- `signal_strength` 语义和其他平台一致；平台自带强度优先，缺失时用统一兜底。
- 收藏 / 行为 API 如果是 `状态 × 内容类型` 多 lane，分页必须公平轮转：遵守上游单页上限、缓存每 lane 富余行、对稀疏与短页独立终止；全局去重和最终 limit 在预算计数之前执行，避免一个密集 lane 吞光配额。
- smoke 任务默认不写 memory、不触发画像。
- init / profile 任务必须显式带当前 init ownership 或 `profile_update=true` 等语义，避免普通 smoke 污染画像。
- extension source task 的最终结果必须使用两阶段完成：先在单一写事务冻结第一份 canonical payload，并用 staged marker 把非终态 DB row 变成业务 mutation 的逻辑终态；再从该持久 payload 重放 event ingress / seen-key / 来源投影；最后只翻 terminal status，不替换 `result_json`。所有 merge/fail/rate-limit 路径都要在同一锁内检查 marker，但 claim 必须保留普通 lease 的 stale reclaim，避免 dispatcher 丢失非 2xx result 响应后任务永久卡住。验收至少注入 merge→ingress、ingress→marker、marker→terminal 三个崩溃窗口，并证明原 caller 不主动重发时，lease 过期后的重新领取仍用第一份 payload 修复；stable event duplicate 能补 marker、seen-key 写失败不会提前完成。当前 XHS / 抖音 / YouTube / 知乎 / Reddit / V2EX 六个账号 bootstrap 队列都已迁移；新插件任务来源必须从接入首版就遵守同一 staged completion 契约。
- 会根据“本次列表缺失”生成取消收藏 / 取消关注等负向事件时，必须由插件明确证明 scope 完整；触顶、限流、网络失败、解析失败和 partial 都不得推进缺失计数。至少连续两次完整快照确认缺失后才生成 retraction，重新出现要生成 restore；两者都必须先落持久 outbox，再进入事件 ingress，成功后才 ack，确保崩溃重放不丢不重。
- 所有账号行为投影、seen-key、快照和亲和度表必须按后端解析出的 canonical identity 隔离。身份优先级和证据等级由后端统一决定；多个非空身份声明冲突时，账号初始化与投影暂停，但匿名/公开 discovery 不应被连带关闭。
- `/api/sources/status` 基于最近任务结果给出 `ready`、`missing`、`partial`、`unverified`、`login_required` 等真实状态，不要硬编码 `no_auth`。
- 登录态平台要接真实登录指示链路：插件 `extension/src/background/cookie-sync.ts` 监控该平台登录 cookie（只上报 `logged_in` 布尔，绝不传 cookie 值）→ `POST /api/sources/<slug>/login-state` → 后端存 auth_state kv + 时间戳。`/api/sources/status` 优先按登录 cookie 状态判定，任务历史只作无 cookie 信号时的兜底。
- `extension.task="browser-task"` 必须分别提供 `/api/sources/<route-key>/next-task`、`/task-result`、`/kick` 三条精确路由；一个 alternation 命中其中任意一条不算完整。它们必须严格用这个路径形状：init 写保护中间件按 URL 段精确放行 `/api/sources/<route-key>/{kick,task-result}` 的 POST（`api/app.py` 的 `_init_write_allowed`），自造别的端点形状会在 init 期间被 409 拦掉（或反过来意外绕过 init 保护）。`identity`、`login-state` 等会影响本轮初始化的 callback 也必须逐条加入精确 allowlist，不能只放行任务结果。
- 三个 browser-task endpoint 必须是**同一个 route key 上真实注册的 GET/POST decorator**；散落在 canonical slug 与旧 alias 的三个字符串、注释或死分支不构成一个可工作的队列。
- 插件 background 对后端的调用一律走带鉴权的共享 API client（device-key / session，见 PR #99），dispatcher 不要自己裸 `fetch`。
- 如果平台要支持「自定义来源 recipe」（`SourceRecipe`）取数，还需提供 `src/openbiliclaw/sources/<slug>_adapter.py` 实现 `SourceAdapter` 协议并注册进 `AdapterRegistry`。`sources/registry.py` 的 `AdapterRegistry.resolve(recipe)` 按 `recipe.source_type` 查找；当前 `DiscoveryEngine` 只提供 `register_adapter()` / `adapter_registry`，尚未在 discover 运行时调用 `resolve()`，所以只注册不能宣称 recipe 取数已接通，还要补运行时解析与调用。只走平台原生任务 / producer 链路则不需要 adapter。

### 2.2 浏览器任务准入、恢复与终态

任务队列的正确顺序是硬契约，不是实现偏好：

1. MV3 冷启动先从持久 task/tab/deadline 恢复或收尾旧执行，并建立 recovery barrier；结果消息可以唤醒恢复。恢复完成前不得领取新任务。
2. 检查来源 enabled、扩展/后端在线、必要 tab/API capability 和安装包资产。
3. 先拿扩展内跨来源 mutex，再调用后端 claim。mutex 放在 claim 之后会制造无人执行的 `in_progress` 行。
4. 后端在 `BEGIN IMMEDIATE` 中同时做 stale-lease reclaim、全局 active-task 检查与 claim；不同 extension ID、浏览器 profile、CLI、guided-init、scheduler 必须共享同一 SQLite admission。`force` 只能绕终态去重，不能绕过 active-task fence。
5. task-result 只有 2xx 才算 ACK；非 2xx/断线对完全相同 payload 做有界幂等重试。claim lease 要覆盖合法长任务，并与 idle deadline、absolute deadline 分开；心跳不能伪装成业务进展。

终态必须保存可诊断且可恢复的真实语义：

- `empty` 需要 affirmative evidence：确实观察到合法成功响应，且它明确表示零项。`response_observed=false`、content tap 未安装、bundle 缺失、login wall、HTML challenge、错误 JSON、parse failure、timeout、429 都不是空结果。
- 一个 scope/page 失败时保留已经规范化并接纳的 rows，返回 `degraded` 和 completeness vector；不得把 partial 当完整 success，也不得因重试覆盖第一份 canonical payload。
- scope、item budget、allowed branches 和 profile-update 权限冻结在 task payload。partial/final/direct/risk merge 只能做安全 enrichment，不能扩大后端预算；第一份 canonical identity/token 获胜。
- dispatcher 报告的 claim 数必须等于实际执行数；未执行 keyword/task 要回滚，不能因为批量 claim 就全部标 `used`。
- staged completion 需要覆盖 merge→ingress、ingress→marker、marker→terminal 三个崩溃窗口；重复 ACK、lease reclaim、seen/outbox 失败都不能替换 payload 或提前 terminal。

### 2.3 Bootstrap 与周期增量同步

只要账号信号会进入 event/profile，首版就要为刷新生命周期做明确选择。当前周期账号同步总开关 `source_incremental_enabled` 默认 `false`；旧配置缺该字段时也关闭。`054cd16a` 是用户在真实使用中反馈抢焦点后形成的产品决定，不能因为“完整接入”重新默认开启。

区分四条调用链：手动 init/fetch、formal discover、scheduler 周期 bootstrap、native-save。`asyncio` 后台协程不意味着浏览器后台 tab，discover 后台成功也不能证明周期 bootstrap 不抢焦点。选择 `incremental` 只表示能力存在；必须验证总开关关闭时不检测/唤醒扩展、不新建任务，并在 claim 前终止升级遗留的周期队列；手动任务和正常 discover 仍可用。验证前台行为时，记录任务目的、触发者、tab 是否激活和 focus 变化；无人值守路径不可从“手动 init 可前台”继承权限。任务结束只在用户未自行切换的情况下恢复原焦点，避免收尾再次抢焦点。

选择 `incremental` 时必须同步注册：

- `sources/source_bootstrap.py` 的 `_BOOTSTRAP_TASK_TABLES`、enqueue/seed evidence；`sources/bootstrap_state.py` 的 state key/default；`sources/task_result_protocol.py` 的 staged cases。
- `runtime/source_incremental_sync.py` 的 `SOURCE_ORDER`、`_TASK_SPECS`、`_SOURCE_CONFIG_ALIASES`、`_SOURCE_INTERVAL_FIELDS`，以及 config/API/docs 的 interval 字段。
- scheduler/profile-ready/init-fence/source-enabled/extension-online/period 六个准入条件。guided init reserve 之前 runtime loop 不得抢跑；offline/disabled 不入队也不推进成功时钟。
- CLI、guided init、daemon 与热重载共用数据库原子 admission；五源或未来多源默认全局串行。scan→insert 不能拆成两个事务，`force` 也不得建立第二个 active task。
- crash adoption 继承原 `created_at`、cursor、deadline 和 canonical task id；不能因为只抄 task id 导致完成后立即再排。坏时间/未来时间要自愈，stale lease 可恢复。
- seen-key/identity 有界、原子、按接收顺序保存；不要用 set/sort 改变顺序，也不要把 child comment 回落成 parent post。完整 init 成功可以 seed incremental checkpoint，失败/partial 不能伪装成功推进时钟。

增量投影还需要完整性协议：只有 `scope_complete=true` 的完整 snapshot 才能从“缺失”推导 retraction；page cap、429、网络/解析失败、partial 都不推进 missing。需要撤回语义时，至少连续两次完整 snapshot 缺失才 retract，重现时 restore；durable outbox 先于 event ingress，成功后再 ACK。seen/snapshot/affinity 按 canonical account identity 分区，identity conflict 暂停私有投影但不应阻断公开 discovery。

`smoke_only` 必须对所有派生 sink 保持纯净。`profile_update=false` 仍可能误写 affinity、snapshot、seen 或 schedule；测试要对 task 前后的每张适用表和普通行为事件计数做差，而不是只断言 memory/profile 不变。

## 3. 浏览器插件接入

登录态平台通常需要这些文件：

- `extension/src/shared/platforms/<slug>.ts`
- `extension/src/content/<slug>.ts`
- `extension/src/content/<slug>/task-executor.ts`
- `extension/src/content/<slug>/task-mode.ts`（需要任务 tab 标记时）
- `extension/src/background/<slug>-task-dispatcher.ts`
- `extension/src/background/service-worker.ts`
- `extension/manifest.json`
- `extension/manifest.firefox.json`
- `extension/scripts/build.mjs`
- `extension/tests/<slug>-*.test.ts`

插件要求：

- host permission 只加必要域名。
- 新增 host permission、页面身份读取或 MAIN-world bridge 时，同步更新隐私说明和商店 listing；明确不读取 Cookie 值、不采集浏览行为，只上报实现契约所需的最小公开字段。
- 普通行为采集和显式任务执行隔离。
- 任务 tab marker 选择必须经过真站 redirect/SPA 验证，不能笼统规定用 hash/query：有的 SPA 会清 hash，有的登录跳转会丢 query。content script 在任务模式下只跑 executor，不上报普通浏览事件；真实 E2E 断言任务前后普通行为事件增量为 0，避免任务页污染画像。
- 任务必须有超时和结构化错误，不要长期 pending。
- content executor 只做同源 DOM/JSON 归一化，最终事件权重、画像写入由后端决定。
- 需要 MAIN-world tap 时，在 manifest/build 中以 `document_start` 安装；isolated listener 必须先就绪，再注入 tap，再导航/触发动作。页面响应可能早于 collector，需用有界、按 task/scope 隔离、一次性消费的 early-response buffer；不得把响应正文、header、Cookie 或 token 跨 world 暴露。
- 同一任务要覆盖 SPA 与 full navigation：用持久 execution key 去重，并在新 document 恢复采集阶段。service worker 休眠后先恢复持久 task/tab/deadline 再 claim；tab close、浏览器重启、extension reload、alarm 与 WebSocket 同时触发都要 singleflight。
- inactive/background tab 是默认目标，但不能假设 hidden page 会渲染虚拟列表。优先从同源网络响应提取公开字段，DOM 作为 schema/无响应 fallback；若平台只能前台渲染，契约明确这一限制，受控切换并恢复原 active tab。
- 一次 reload 只重启当前已安装 build，不会自动部署 worktree 新产物。真机前记录 extension version/ID、build 绝对路径和 hash；Chrome/Firefox 输出目录要隔离清理，按各自 manifest 逐一验证 JS/CSS/icon/WAR 存在，避免后构建的 Firefox 清掉 Chrome 资产。
- 现代站点常把按钮放在 Web Components / open shadow root 里；点击 / 分享 / 收藏等 E2E selector 要能处理 open shadow DOM、slot、icon-only button 和动态 aria/title/data-testid。
- 默认 E2E 只跑不改变账号状态的动作。`like`、`favorite`、`follow`、`save`、`upvote`、`subscribe` 等会改真实账号状态的动作必须有显式 `allow_state_changing` / 测试号 / 用户授权。
- 行为事件采集要验证 DB 里的统一事件：`source_platform`、稳定内容 ID、URL、作者 / subreddit / topic、target metadata 和 dedupe key 都要能追溯。
- 测试覆盖 URL 分类、任务校验、timeout、登录失败、分支 cap、normalizer、dispatcher 回传，以及 active/background/hidden × SPA/full-nav × response-before/after-listener × DOM/API 的适用矩阵。真空、未观察、登录墙、限流和解析错误必须分别断言。

## 4. 配置和设置页

一个来源不支持 UI 配置，就还没有产品化。

需要更新：

- `src/openbiliclaw/config.py`
- `config.example.toml`
- `src/openbiliclaw/api/app.py` 的 `/api/config` GET/PUT
- `extension/popup/popup.html`
- `extension/popup/popup.js`
- `extension/popup/popup-helpers.js`
- `src/openbiliclaw/web/desktop/index.html`
- `src/openbiliclaw/web/desktop/assets/js/app.js`
- `/setup/` 和移动端 view-model 中的初始化来源列表
- `docs/modules/config.md`

配置项建议：

- `[sources.<slug>].enabled`
- `[sources.<slug>].source_modes`
- 每个 discover mode 独立 daily budget / cooldown
- `[scheduler.pool_source_shares].<slug>` 默认值
- 旧 `config.toml` 缺 `<slug>` 时自动补默认值
- 关闭平台时保留配置值，但 runtime quota 不应被它占用

特别注意：来源比例保存到配置页以后，必须真的进入 runtime source policy 和 candidate pool 配额，不只是 UI 上能看到。

配置页验收不要只看一个端：

- 插件 side panel 和 PC Web 都要能保存平台开关、source modes、每个分支预算、候选池 share。
- `/api/config` GET/PUT 要 round-trip 新字段，旧 `config.toml` 缺字段时按默认值回填。
- 不要假设 provider registry 会自动生成所有 API 字段。`SourcesStatusResponse`、`SourcesCredentialsResponse`、`SourcesConfigOut` 与 `GET /api/sources/credentials` 当前都可能有手写平台字段/组装；contract audit 必须比较 canonical family 与 provider/verify/spec/model/endpoint/shared `SOURCE_KEYS`/init roster/source policy 的集合，能力例外只来自契约。
- 局部更新语义必须一致：字段省略 = 保留已存值，空字符串 = 用户显式清除，掩码回显 = 不覆盖。表单需要分别记录「用户是否触碰」和「prefill 是否成功」；pending / failed prefill 留下的空框绝不能清掉配置。
- 保存成功也可能带 warning（常见为 HTTP 202）。setup、桌面和插件必须解析并展示 2xx 响应里的 `warnings`，不能只渲染 4xx/5xx。
- `/api/sources/status` 要支持该来源真实状态枚举。插件任务源常见 `unverified`：尚无任务证明不是失败，测试不能只允许 `ready/missing`。
- 保存 credential、插件 login sync、身份切换、source toggle 和 runtime config apply 后，setup、桌面与 popup 都要在无需整页刷新时收敛到后端最新状态；旧 heartbeat/缓存响应不得覆盖后到的新 authoritative verdict。
- 来源 enabled 轴与 credential/auth 轴分开计算。即使来源关闭，也要展示已存凭据、被拒状态和可执行下一步；不能在 `enabled=false` 时提前 return，把凭据状态藏掉。
- 开关关闭时保留用户填写的 share / budget，但 runtime 有效配比必须剔除该平台。

## 5. Guided Init 和画像初始化

所有初始化入口都要补：

- CLI：`--yes-<slug>` / `--no-<slug>`，必要时加分支上限参数。
- Desktop `/setup/` 来源选择。
- 插件 guided-init checklist。
- API init models、init status 和进度展示。

规则：

- 新可选平台默认 opt-in 提示，不阻塞 B 站或其他已选平台初始化。
- 来源准入和账号解析只由后端拥有。CLI / setup / 桌面 / 插件不得在发请求前复制「有无用户名 / 令牌 / 登录」判断；这种前端 guard 看不到扩展身份、已保存配置等后端 fallback，会让合法 payload 的实际请求数变成 0。
- 如果平台能在已登录浏览器内稳定读取个人行为信号，应优先实现 `bootstrap_events` / `bootstrap_profile`：明确每个 scope、默认上限（当前强信号平台通常每 scope 300）、事件映射（例如 saved → `favorite`、upvoted/liked → `like`、subscribed/following → `follow`），并允许该平台作为唯一初始化来源，只要真实拉到至少一条信号。
- 同时声明 init 之后如何更新：`incremental` 必须接第 2.3 节的共享 scheduler/admission/staged ingestion；`init-and-on-demand` / `on-demand` / `init-only` 要在配置、状态和文档中可见并有不会后台入队的测试。不能首版只做 init，后来才发现画像永远不刷新。
- 如果平台只启用后续 discovery、不在 init 阶段产生个人行为信号，必须在 CLI / API / 插件 / Web UI 中标成 discovery-only，且不能作为唯一画像初始化来源；只选择这类来源时应给出明确错误（例如 `no_profile_signal_sources`），不要等到最后落成 `empty_signals`。
- 平台登录缺失只影响该平台，不应让其他来源无法初始化。
- 新增来源后审计既有 B 站专属 gate：画像重建、CLI 输出和初始化校验不得因为历史上只有 B 站就继续要求 B 站登录。
- init 任务结果必须绑定当前 init run，避免扩展延迟结果误写 memory。
- 长收藏或慢上游要发布真实 item/chunk 进度，并把「长时间无新结果」的 idle deadline 与防无限运行的 absolute deadline 分开；固定墙钟无法区分卡死与持续变慢。心跳只证明连接还在，不算业务进展，硬上限也不得被 UI 当作耗时预估。
- smoke 后若需要写 memory，必须用显式 flag，例如 `--write-memory`。
- 画像重建必须显式，例如 `--rebuild-profile`，且应隐含写 memory。
- 真实画像 E2E 必须使用本地实际 LLM / embedding 配置；不要擅自换成本地默认模型或 mock provider。若用户指定了本地配置中的某个 provider，要按配置里的 provider/model/base_url 跑。
- 测试要证明：普通 smoke 不写 memory/profile；init/profile 任务会写。

## 6. Discover 接入

同时要有 smoke 命令和正式 discover。

后端：

- `src/openbiliclaw/runtime/<slug>_producer.py`
- refresh/runtime controller 调度入口
- 转成 `DiscoveredContent(source_platform=<slug>, source_strategy=<slug>-<mode>)`
- candidate pool 和 source policy 识别该来源
- `/api/sources/status` 能反映 discover 任务结果
- strategy/host/alias 都通过 `sources/platforms.py` 归一为 canonical family；新写入不得存 transport alias。source-scoped backfill/reclaim/统计 SQL 必须先 `WHERE source_platform = ?` 再 `LIMIT`，不能先限行后在 Python 过滤，否则其它平台缓存会泄入本源。

CLI：

- `discover-<slug>`：search smoke
- 可选 `discover-<slug>-hot`
- 可选 `discover-<slug>-feed`
- 可选 `discover-<slug>-creator`
- 可选 `discover-<slug>-related`
- `openbiliclaw discover --source <slug>` 必须走正式 producer，不能只提示去跑 smoke 命令。

质量要求：

- 没有显式关键词时用画像关键词 fallback。
- 只要该来源有 `search` 类 discover，就必须同时接入统一关键词链路的两半：
  - **生成侧（双轨，两条都要覆盖新平台）**：
    - merged prompt 轨：`runtime/keyword_planner.py` 的 `_PLANNER_PLATFORMS` 平台元组、`_PLATFORM_QUERY_STYLES` 平台 query 风格字典，以及 `llm/prompts.py` 的静态 `PLATFORM_SUPPLY_ADVANTAGES`（`<supply_advantage>` 表）/ 允许 key / schema 示例，都要加 `<slug>`；补测试证明 `<slug>` 缺口会触发一次 merged LLM 生成。
    - keyword inspiration axis 轨：`runtime/inspiration_pipeline.py` + `build_inspiration_axis_keyword_prompt`（axis+keyword 单次 LLM 调用，cross-domain explore 也从 axis 库取词）。它按 allocation targets 的 `platforms` 分配产词——确认新平台会出现在 allocation targets 里，否则 axis 轨永远不为该平台产词。
  - **抓取侧**：producer 使用 `KeywordFetchCoordinator.claim(<slug>)` 领取关键词，把 `source_keyword_id` 透传到候选；关键词池为空时回退画像关键词，抓取失败时标 `failed`，成功交付候选后标 `used`。
  - 只做 claim/fetch、不进 planner generation，会导致正式 discover 长期只能吃画像 fallback 或旧词库，不算接入完成；只接 merged 轨漏 axis 轨（或反之）同样是半截接入。
  - 补齐某个平台时顺手审计所有已接入 search 型来源；文档写着“使用统一关键词”的来源必须都有 generation 测试覆盖，不能只在 producer 里 claim。
- 候选入池阈值必须走统一 admission policy（`src/openbiliclaw/discovery/admission.py` 的 `effective_admission_threshold`）：策略 / producer 可以提供更严格的 requested threshold，但它只能抬高、不能压低或绕过 policy floor；exact `explore` 是唯一放宽语境。2026-07-10 的统一修复把候选自带的 `score_threshold` 作为 requested input 再与 policy floor 取 `max`，新来源不要恢复“直接采用候选阈值”的旧路径。
- creator / related 需要 seed；冷启动时可用同轮 search / hot / feed 结果兜底。
- 停止时给明确 reason：`pool_full`、`source_disabled`、`mode_disabled`、`budget_exhausted`、`login_required` 等。所有 mode 都耗尽预算时要汇总成 `budget_exhausted`，不能伪装成一次成功但空结果。
- 分支预算只计入最终全局去重、通过过滤且实际保留的候选；上游重复行、超出最终 limit 的富余行不能提前烧掉预算。
- 持久化 cursor 必须处理上游总量缩水或 offset 失效：允许一次有界 reset/retry，仍失败再给稳定 reason，不能永久卡在旧高位 cursor。
- 关键词 claim 是事务生命周期：429 / 瞬时失败只回滚当前及尚未执行的 claim，已经成功产出的候选保留；成功交付才标 `used`，确定失败才标 `failed`。
- 显式 `openbiliclaw discover --source <slug>` 受该来源 enabled/mode 约束，但不应被 daemon 的 scheduler master switch 拦住；总开关只控制后台调度。
- 候选入池必须尊重 `[scheduler.pool_source_shares]`。
- source share 要贯穿 producer、内部 pool、pool-full 判断、evaluation、disabled-source 回收和后续 refill；只在最终 candidate 表按比例不够。关闭来源的旧 raw/pending 行不能继续占坑，恢复时也不能挤掉启用来源。
- 调度结果分开记录 `ran`、`made_progress` 与 `productive_supply`。空跑、重复行、全部被 admission 拒绝不能重置“无产出”退避；offline、budget exhausted、rate limit、valid empty、infrastructure failure 使用不同 reason/backoff。
- page/scope 部分失败必须返回 `degraded` 与 completeness vector 并保留已入池候选；429 不能把前页结果抹掉，也不能推进 cursor/缺失推断到“完整”。
- 正式 producer 与 smoke 命令的终端文案要描述真实后端；默认走插件时不要残留“命令后端”之类旧提示。
- discovery 入池后至少抽查 DB：`source_platform`、`source_strategy`、`source_keyword_id`、内容 URL、body_text / content_type 等字段能被 evaluator 和推荐卡消费。

formal producer 与 inspiration/direct helper 如果共享同一上游配额，必须复用持久 cooldown 和公开 query sanitizer，不能各自重试绕过 429。状态只聚合当前启用 modes；credential 拒绝标记只作用于当前指纹，不能让已关闭模式或旧 token 的错误永久拖坏来源。分别记录 fetched、去重后 retained、enqueued、evaluated、admitted；`incomplete_results`、筛掉全部非法/私有行、page/item cap 和后页 timeout 都不能冒充完整空结果。参见 `tests/test_github_producer.py` 与 inspiration 测试中的相关回归。

## 6.5 Eval / 推荐链路接入

新来源不只是能抓到候选，还要能走完推荐闭环。

检查项：

- `DiscoveredContent` 字段足够 evaluator 判断：标题、作者、正文 / 摘要、标签、URL、内容类型。
- adapter 将平台原始发布时间映射为精确 UTC `published_at`，并保留原来源/精度；未知、非法、未来值 fail neutral，绝不拿 `discovered_at` 补。测试必须有 old-but-newly-discovered、unknown、时区边界和未来/非法时间。
- 所有 single/batch/reclassify/cache 路径传同一个精确 UTC `evaluated_at`。时效分类/bonus 只给时间精确且高置信的 breaking/current/versioned；evergreen/historical/unknown 保持中性。cache key 绑定 publication summary、评估 UTC hour 和 schema/model namespace，跨小时或发布时间修正会 miss。
- 平台评分、排名、收藏人数等 catalog 信号要放在明确字段或 metadata 中，不能硬塞成 `like_count` / `view_count`，也不能套用 B 站播放量质量门槛。
- LLM prompt builder / merged prompt schema 不应因为新平台 key 或 text-only 内容破坏静态 system prompt 约定。
- 候选进入 `discovery_candidates(pending_eval)` 后，真实本地 LLM eval 配置能跑通；不要用 mock 或错误 provider 代替用户配置。
- admission 后推荐 API 返回的 item 保留 `source_platform` / `content_url` / `body_text` / `content_type`。
- 推荐卡的「去看看 / 收藏 / 稍后再看 / 不感兴趣 / 聊一聊」仍能对非 B 站来源发正确 payload。

## 7. 推荐卡三端适配

三端都要补齐：

- 桌面 Web：`src/openbiliclaw/web/desktop/assets/js/app.js` 和 CSS。
- 移动 Web：`src/openbiliclaw/web/js/view-models.js` 和 CSS。
- 插件 side panel：`extension/popup/popup-helpers.js`、`popup.html`、`popup.js`。

检查项：

- 来源 badge 和文案正确。
- 作者和内容 ID 使用跨平台语义：所有来源读 `author_name`；只有 B 站显示「UP主」「BV号」，其它来源显示「作者 / 创作者」「内容 ID」或平台自己的准确称谓。
- 打开链接正确。
- 无封面来源有 text-card fallback。
- 非 B 站内容不会误构造 B 站 URL。
- 稍后再看、收藏、忽略、不感兴趣、聊一聊等动作仍可用。
- 长标题、长摘要、无封面卡片不会遮挡按钮。
- 桌面、移动、插件侧栏都做截图或视觉检查。
- 推荐页平台过滤 / source badge / source label 要包含新平台。
- 四个产品面（setup、桌面、移动、插件）逐一实现，或在规格中逐面写明有意排除、理由和契约测试；「这个场景大概用不到」不算完成。
- engagement 契约包含 `view / like / favorite / comment / share / danmaku` 六项。当前 `RecommendationOut` 已有 `share_count`，不要沿用旧 plan 的“DTO 缺失”结论；逐字段追踪 normalizer → storage → DTO → 桌面/移动/插件实际 renderer。契约里声明结构性缺失的字段不渲染、不占位；声明可映射的字段用真实候选验证。`source_metadata` 写库或通过 API 返回不代表前端已展示：GitHub 当前文字卡的 owner/name、description、stars 与仍只保存在后端的 topics/language/license 等须分开报告。
- 如果源主要是文字内容，要确认 text-card 在 PC、移动、插件三端都不是断图 fallback，按钮不会被正文遮挡。
- 封面链路要显式决定：走后端 `/api/image-proxy` 缓存代理，还是浏览器直连。走代理必须把封面 CDN 域名加进 `runtime/image_cache.py` 的 `ALLOWED_IMAGE_HOST_SUFFIXES`（否则一律 403 Domain not in whitelist）；CN CDN 域名还要同时加 `_DIRECT_FETCH_HOST_SUFFIXES` 绕过系统代理（风控会封代理出口 IP，抖音 / B 站 / XHS 都踩过）。这个 suffix 表同时是 SSRF 边界：contract 只接受具体 DNS host 并拒绝 wildcard/public-suffix/local/IP/已知 wildcard-DNS，但静态语法仍不能证明安全；每次请求和每个 redirect hop 还必须解析并拒绝 loopback/private/link-local/reserved 地址，记录 DNS rebinding/地址固定策略。没有这份 runtime 证据时 `media.image-network-boundary` 保持 `NOT_RUN/BLOCKED`，不能凭 allow-list 接线 PASS 宣称安全。浏览器直连则要先确认该 CDN 无防盗链 / referer 限制。
- 移动 Web 的「去看看」会尝试拉起平台原生 App：`src/openbiliclaw/web/js/app-launch.js` 的 `buildAppDeepLink(url)` 按内容 URL 的 host / path 分支解析并返回 URL scheme。新平台有可靠官方 scheme 就加对应解析分支；没有就返回空串，由 `openContentUrl()` 走浏览器 fallback，不要硬造 scheme。

临时 E2E 截图不要直接提交到根目录。只有迁移到 `docs/images/` 且被 README / 首页 / 文档引用时才提交。

## 8. 测试清单

后端常见测试：

- `tests/test_<slug>_tasks.py`
- `tests/test_<slug>_producer.py`
- `tests/test_api_<slug>_ingest.py`
- `tests/test_config.py`
- `tests/test_source_policy.py`
- `tests/test_cli.py`
- `tests/test_api_app.py`
- `tests/test_keyword_planner.py` / `tests/test_llm_prompts.py`（search 型来源必须证明统一 query generation 已接入）
- 推荐卡样式 / view-model 测试

全局契约测试不能只遍历“已经注册的来源”。至少断言 canonical families 与所有 required 中央 roster 的集合关系，并由 contract 驱动能力例外：auth provider/action/spec、status/credential/config response 字段、credentials endpoint、shared `SOURCE_KEYS`、source policy/API share/init order、CLI dispatch/help、search planner/inspiration、bootstrap/incremental table。还要枚举 alias/host/strategy prefix 在未显式给 `source_platform` 时也能归一，且写库只出现 canonical family。

Bangumi 复盘后新增的必测矩阵：

- schema normalizer：错误容器类型、`None`、嵌套列表 / 字典、占位串，以及真实但看似异常的短名 / 标点名反例。
- 多 lane 分页：密集 / 稀疏、满页 / 短页、上游 page cap、跨 lane 重复、全局 limit 和预算只计最终保留项。
- 凭据 patch：省略、显式空串、掩码、prefill pending/failed、轮换新值、清除；省略与掩码必须零网络。
- 身份状态机：正面匹配、404、uid mismatch、网络失败、已验证后瞬时失败、身份变化、旧版非法记录、并发上报和持久化失败。
- source status：enabled × credential present/rejected × verification 三轴组合，尤其关闭来源时不能隐藏已存或失效凭据。
- discover 状态：所有 mode 预算耗尽、cursor 缩水 reset、429 后关键词回滚、已产出 partial result 保留、显式 CLI 不受 scheduler 总开关影响。
- 跨端契约：前端必须真的发出初始化请求、展示 2xx warning，并且不包含后端准入判断或平台专属重复文案。
- upstream fixture：真实 envelope/分页/终止页、错误 content-type、200 HTML challenge、空数组与未观察响应分别覆盖；fixture 与 normalizer 之外另有一次脱敏真站 contract test。
- 任务准入与恢复：capability/mutex 前零 claim、两个 extension ID/profile 只有一个 active task、`force` 不绕 active fence、冷启动 recovery barrier、长 lease/stale reclaim、result 非 2xx 同 payload 重试。
- 任务隔离与完成：marker 经真实 SPA/redirect 仍在、普通行为事件 delta=0、first-final-wins 三个 crash window、scope cap 不可扩大、partial 保留、true-empty/no-observer/login/rate-limit/parse 各自终态。
- 增量同步：六个准入门、CLI/init/scheduler 原子单飞、crash adoption 继承时间/cursor、seen 顺序/边界、成功 seed/失败不推进、scope-complete 两轮 retraction/restore、smoke 对所有 projection sink 零增量。
- 时间语义：old-but-newly-discovered、unknown、非法/未来、时区边界、精确 evaluated-at、跨小时/cache invalidation。

插件常见测试：

- `extension/tests/<slug>-adapter.test.ts`
- `extension/tests/<slug>-task-dispatcher.test.ts`
- `extension/tests/<slug>-task-executor.test.ts`
- popup/settings/init 相关测试
- Chrome/Firefox 两份 manifest 的 referenced asset/WAR 存在性；active/background/hidden、SPA/full-nav、response-before/after-listener、DOM/API 按契约覆盖。

原生保存的不确定结果必须与重新执行分开：点击后没观察到选中态，不证明上游未写入。先停止并等待原 mutation sender，重载精确 item，等待新 `document_instance_id` READY，用独立 execution 的 `verification_only` 只读核对持久状态；只有正面证据才升级成功，不能重试 toggle 反向取消。容器/按钮语义先经真站核对，平台可能已从命名收藏夹改为全局收藏开关。复用 `c14f38f9` 的 runner/transport/平台 verifier 回归。

原生保存 executor 还必须覆盖 strict task/page/item/type 关联、full ancestor visibility、closest identity fence、hidden/related dialog、同名 ambiguity、checked idempotency，以及 directional action-local risk。需要命名容器的平台必须在创建后 close/reopen/re-query；创建失败或重查不一致不得 fallback 到其它容器。fixture 接线完成不等于真实账号验证，文档和 PR 必须分别报告两种状态。

实现变更完成前按仓库要求跑对应检查；完整接入用以下全量清单。纯 skill/文档更新运行 skill validator、现有 skill/文档契约测试与链接/镜像检查，不以平台 E2E 作为文档修改的完成条件：

```bash
SOURCE_SKILL_PYTHON=/absolute/path/to/project-venv/bin/python
# 完成真实来源时替换为已冻结的副本；本仓库模板/当前示例可直接运行。
SOURCE_CONTRACT=docs/platform-source-contract.example.toml
PYTHONPATH="$PWD/src" "$SOURCE_SKILL_PYTHON" -c 'import openbiliclaw; print(openbiliclaw.__file__)'
PYTHONPATH="$PWD/src" "$SOURCE_SKILL_PYTHON" -m ruff check src tests
PYTHONPATH="$PWD/src" "$SOURCE_SKILL_PYTHON" -m mypy src
PYTHONPATH="$PWD/src" "$SOURCE_SKILL_PYTHON" -m pytest -q --tb=short
PYTHONPATH="$PWD/src" "$SOURCE_SKILL_PYTHON" scripts/audit_platform_source.py --contract "$SOURCE_CONTRACT" --check --json
PYTHONPATH="$PWD/src" "$SOURCE_SKILL_PYTHON" scripts/source_contract_metrics.py --check
cd extension
npm test
npm run typecheck
npm run build
npm run verify:assets
npm run build:firefox
npm run verify:assets:firefox
npm run verify:assets
```

`SOURCE_SKILL_PYTHON` 必须是已有开发依赖的 Python 绝对路径；第一条输出必须位于当前 worktree。共享 editable venv 很可能仍指向 main，只有显式 `PYTHONPATH="$PWD/src"` 和 import 证明后才可复用；若不在，不得继续拿该 venv 的绿灯当本分支证据。全量门禁在最终树通过后，无新增修改或失败无需重复；若分批运行，要汇总全部批次退出码与用例数。超时/取消不是通过，进程存活也不是进度证据，按实际结果记 `NOT_RUN/FAIL`。如果全量检查发现旧测试断言漏了新合法状态（如插件源 `unverified`），修测试；如果 `ruff format --check src tests` 命中历史无关文件，不要顺手格式化，先用 `origin/main:<path>` 验证是否已在 main 修复，并在交付说明里说明。

发布前插件包验证：

```bash
cd extension
npm run package:only -- --archive-version <extension-version>
npm run package:firefox:only -- --archive-version <extension-version>
```

这里的 `package:only` 只能消费刚才已验证的对应 target build；不得在 Firefox build 后拿已被清理/替换的 Chrome 目录打包。记录两份 archive 的版本、hash 与来源 commit，并至少安装 package/store 形态跑一次关键 E2E，不能只测 workspace 文件。若 contract 宣称 Chrome 和 Firefox 都支持同一登录任务，关键 authenticated path 必须在两者的安装产物各跑一次；Firefox 只 build 不算双浏览器完成。

全仓 `ruff format --check src tests` 如果命中历史无关文件，不要顺手大规模格式化；只格式化本次改动文件。

## 9. 真实端到端验证

登录态相关来源必须用真实扩展浏览器验证。

**先做 E2E 预检，再启动昂贵流程**：核对运行代码/端口、浏览器安装目录/权限/登录态、隔离 DB/data/config、实际配置的 LLM 与 embedding provider/model/base URL（不输出 key）。配置读取应沿用项目 loader 和覆盖优先级，不要临时造一份默认配置，把用户的云模型换成 Ollama 后再因慢而判平台失败。将缺 PAT、未登录、扩展装错 checkout 等前提与代码缺陷分开；有 blocker 仍继续其它独立链路。对生产服务的切换按本轮授权处理；若临时接管已有端口，记录恢复方式，结束后核对原服务、配置、扩展连接、task tab 与本次临时数据，不能只说“测试通过”。

验证阶梯：

1. 在验收记录中写清 worktree/commit、`openbiliclaw.__file__`、CLI 路径、后端 bind/data/config root；使用隔离临时 SQLite，并先记录相关表基线。
2. 写清扩展 ID/version、浏览器、实际安装的 build/package 绝对路径和 hash，再重新加载。热重载或 `/api/extension/reload` 只会重启当前已安装包，不会把 worktree 新 build 自动部署进去。
3. 打开平台页面，确认当前浏览器已登录。
4. 跑 `fetch-<slug>` 或 discover smoke，看分支计数、cap、错误原因。
5. 每个 discover mode 跑一次，确认候选入 `discovery_candidates`，或因合理 reason 停止。
6. 跑 `openbiliclaw discover --source <slug>`，确认正式 producer 通。
7. 在插件配置页和桌面 Web 配置页保存 source modes / source share，回读 `/api/config`。
8. 桌面 Web、移动 Web、插件 side panel 都看推荐卡样式。
9. 如用户要求，跑 `--write-memory` / `--rebuild-profile`，确认 memory/profile 真的变化。

任务型来源还要证明：执行前后原 active tab 一致（契约明确需前台的除外）、普通浏览事件增量为 0、task/result 的 extension ID 与安装 artifact 一致、重复 result/lease reclaim 后 canonical payload 和业务行不变。

真实 E2E 的终端输出、任务 result、数据库计数比截图更有价值；截图只作为临时视觉证据。

**discover API smoke 不能代替初始化事件 smoke。** 如果来源宣称能贡献画像信号，还要单独证明 `真实收藏/行为 API → canonical row → event → init preparation`：

1. 分开报告「公开 demo 账号」和「当前登录用户」；前者只能证明公开路径与 schema，后者才证明账号解析和私密范围。
2. 在临时 project/data root 与临时 SQLite 中运行，避免污染真实 memory/profile；若只测事件提取，不调用 LLM。
3. 断言稳定 ID、URL、`author_name`、事件类型、scope/status、metadata 等 canonical 字段，而不只看数量大于 0。
4. 将同一批事件写两次，验证 `(inserted=N, duplicate=0) → (inserted=0, duplicate=N)`。
5. 原始浏览 / 收藏记录没有正负反馈证据时，`satisfaction` 保持 unknown；不要为了断言方便伪造成 positive/neutral/negative。

证据层级分别记 `unit/static`、`build/assets`、`live-transport`、`installed-app`、`full-pipeline`；PASS 只属于实际验证的那层。每条链都有独立终态和 artifact：公开 transport、当前账号 → event → init/profile、discover → real LLM → recommendation、安装版 UI。不允许在汇总行写 full backend PASS、附注却说 suite 超时；也不允许把手工直接调用浏览器 API 后 POST 结果算作已安装 dispatcher 的往返证明。partial init 后另跑推荐成功，不补齐原 init 终态。

真实 E2E 要分层报告：

- 安全动作：snapshot、scroll、只读导航、search、hot、related，以及已证明只打开/关闭分享面板或复制公开链接的精确动作默认可以跑。泛化的 `click` / `share` 名称本身不构成安全证明；contract 要写可观察的只读后置条件。
- `safe_actions` 是 fail-closed 机器入口，不是任意文案：只有受限只读动作语法、逐动作 `safe_assertions="upstream-state-unchanged"` 和 postcondition 才能列入；不认识的动词、非 ASCII 标签和平台自定义动作一律按状态变更处理，直到明确加入共享安全分类与回归测试。审计器不执行动作，也不替用户授权；真实 runner 仍必须核对请求方法/端点、安装产物和动作后的账号状态，不能用一段写着 `no/without/不改变` 的自由文案洗白实际 mutation。
- 状态变更动作：like / favorite / follow / save / upvote / subscribe 只在用户明确允许或测试号中跑。
- native-save 精确授权记录：仅有 `allow_state_changing=true` 不够；每次真实 favorite / watch-later 必须同时命名 exact platform、action、public `content_id` 与 `expected_target`，并按平台矩阵校验。trusted-local `/api/extension/e2e/run` dedicated 模式必须与 generic actions 互斥，只提交一个 canonical item 到 production `/api/saved/{action}/sync`，再按同一 durable task/item/resolved target 关联；通用 DOM E2E runner 禁止 native-save mutation。授权和结果都拒绝账号 ID、Cookie、token、HTML、响应正文和含秘密 URL；安全 callback 仅记录 `platform/action/content_id/expected_target/task_status/error_code`。自动同步默认关闭，手动两种 action 分开授权；duplicate 必须得到 `already_synced`，本地 cleanup 只删 membership 且确认平台记录保留。
- 配置动作：插件页和 PC Web 保存后必须回读 `/api/config`，再确认 runtime source policy / pool share 生效。
- 推荐动作：三端截图或像素/DOM 检查要覆盖长标题、无封面、文字卡和按钮区域。
- 画像 / eval：使用真实本地配置的 LLM provider，记录 provider、命令、候选 / 事件计数和最终 profile / candidate 状态。
- 混合后端动作：如果默认后端会 fallback 到插件，报告时要把“默认后端成功”和“fallback 成功”拆开说；例如 CLI / SDK credential 未就绪但插件 fallback 完成 discovery，不能表述成默认后端已通。
- Cookie / credential 同步动作：如果实现了插件同步第三方 CLI credential，要同时验证后端 endpoint、插件 runtime-stream / hot reload、浏览器 cookie 可读性和最终 credential 文件；若真实浏览器缺必要 cookie 名，要记录“不阻塞 fallback，但默认命令后端仍 login_required”。

不要只交 happy path。按契约至少覆盖 anonymous、authenticated、expired/rejected credential、transient network、identity mismatch/account switch、disabled-with-stored-credential、affirmative empty、partial、rate limit、duplicate/retry/crash；不适用项写 `N/A + 证据`。public discovery smoke 与 `account → canonical event → profile/init` smoke 是两条独立验收链，不能互相替代。

## 10. 文档和发布

接口、数据流、配置、CLI、新来源行为变化都要更新文档。

按范围更新：

- `docs/changelog.md`
- `docs/modules/cli.md`
- `docs/modules/config.md`
- `docs/modules/discovery.md`
- `docs/modules/extension.md`
- `docs/modules/soul.md` 或 memory/runtime 文档
- `docs/architecture.md`
- `docs/spec.md`
- `README.md`
- `README_EN.md`
- `docs/index.html`
- `docs/index.md`（新增文档时）
- 新增扩展 host permission / 身份通道时，还要更新隐私声明、Chrome/Firefox 商店 listing 与人工审核说明。
- 凭据获取步骤写到稳定文档锚点，所有 UI 只链接该锚点；不要在三端复制容易随上游变化的发令牌步骤。

新增来源检查完整 release-readiness；窄修复按影响范围检查，纯文档更新只做相应文档交付检查。commit、version bump、push、tag、GitHub Release、商店上传和生产服务切换依据当前任务的授权执行；用户已授权的动作不重复问，旧 session 中的授权不算本轮授权。用户未要求时，这些 mutation 行以 scope/authorization 证据记 `N/A (not requested)`，不要求虚构 contract test，也不阻塞代码接入 complete；用户已要求但尚未执行才是 `NOT_RUN`。不要为了“完整接入”擅自提交或发布。

Release-readiness 检查：

- 后端版本：`pyproject.toml`、`src/openbiliclaw/__init__.py`、`uv.lock`
- 插件版本：`extension/package.json`、`extension/package-lock.json`、`extension/manifest.json`
- 首页版本 / SEO：`docs/index.html` 的 `softwareVersion`、meta description、首页 source card、英文翻译都要包含新平台。
- README / README_EN 顶部定位、核心特性、安装登录说明、架构图中的来源列表都要同步。
- 如果新增 / 修改了本指南或 skill，确认不是未跟踪文件，并与 `origin/main` 已存在版本做 diff，避免合并时丢掉后补规则。skill 有两份入口（`.codex/skills/add-platform-source/SKILL.md` 和 `.claude/skills/add-platform-source/SKILL.md`），内容必须 byte-identical 并由 `tests/test_add_platform_source_skill.py` 锁住；实现细节只写在本指南，skill 只保留入口协议和关键约束。
- 推 tag 前先查远端是否已存在同名 tag；如果同名 tag 已经存在，不要改旧 release 对应的 changelog 语义，必须 bump 新版本并把新改动放进新的 changelog block。
- 常规 tag：
  - `backend-vX.Y.Z`
  - `extension-vA.B.C`
  - `desktop-vX.Y.Z`
- Docker 渠道：`.github/workflows/release-docker.yml` 分别发布 backend 镜像和独立的 `openbiliclaw-ollama` baked-embedding 镜像，也都在版本对齐范围内。新来源若给 `pyproject.toml` 加了默认依赖（第三方 CLI / SDK），要确认 backend Docker 镜像构建真的带上它。GHCR 新建的 package 默认 private，需要手动设 public，否则用户无法匿名拉取。
- 本地提交前跑 `git status --short --ignored` 看清楚：未跟踪设计稿、截图、`dist/`、zip/xpi/dmg/exe、临时 release 包不要误提交；只有文档引用的图片或明确要求入库的资产才纳入提交。
- 本地可先用 `uv build` 验证后端 sdist / wheel；当前项目 venv 可能没有 `python -m build`，不要因此把 `build` 加进运行时依赖。
- 插件本地包验证后，release 产物仍以 tag-triggered GitHub Actions 为准；本地 zip 只是验证，不提交。
- backend release 是 source tag 校验，不一定有 GitHub Release 资产；插件和桌面安装包由对应 workflow 发布，聚合 release 再收敛当前插件 / 桌面资产。
- 推 main 后再推 tag，确认 CI、backend source tag、extension package、desktop installers、pages build 都成功；聚合 release 只允许收录同版本资产，某个 channel 还没完成时应显示未发布，不能回填上一版桌面或插件包。
- 确认聚合 release `openbiliclaw-vX.Y.Z` 只包含当前版本资产，尤其不要混入旧 `.dmg` / `.exe`。
- 如果有 Chrome Web Store / Firefox AMO / 其他插件市场，按项目 workflow 触发上传或说明为什么不能发；Chrome Web Store 审核异步，成功上传不等于立刻对用户可见。
- 发布后把 release 链接、tag、commit、workflow 结果和本地残留未提交文件一起汇报。

## 11. 完成判定

验收报告至少包含：integration level、worktree/commit、contract 路径、每个 gate 的适用性与执行状态、primary/fallback transport、auth/account evidence、命令/exit code、build/package provenance、真实 E2E 计数与幂等样本、LLM provider/model、四端证据、实际执行的状态变更动作（默认 none）、risks/deferred，以及最终 verdict `complete / incremental only / blocked`。只有全部 required 行 `PASS` 才能写 `complete`。`required` 以本轮声明范围为准，不能通过缩小用户要求规避缺口；capability-increment 或 audit-only 的 complete 不等于 full source complete。代码存在、已提交、已合 main、当前进程加载、扩展已安装、已发布分别报告；任一状态都不能推出下一项。

## 常见失败模式

- 在共享 editable venv 中跑测试，却没有打印 import path；实际测试的是 main，不是当前 worktree。
- 只 reload 已安装扩展，误以为 worktree 新 build 已部署；或 Chrome/Firefox 共用输出目录，后一次 build 清掉前一份 manifest 资产。
- capability/online/mutex 检查放在 claim 之后，制造无人执行的 `in_progress`；或只用扩展内 mutex，两个 extension ID/profile 仍能并发领取。
- MV3 冷启动一边恢复旧 task/tab，一边领取新任务；alarm、WebSocket 和 reload 三路重复执行。
- 用 hash/query 做任务 marker 却没经过真站 SPA/redirect，marker 被清后任务页作为普通浏览写入画像。
- 把 `items=[]` 一律写 `ok`：其实可能没观察到响应、tap bundle 缺失、200 HTML challenge、login wall、解析失败或限流。
- 上游一部分 scope/page 成功、一部分 429/失败，却写完整 success、推进 cursor/缺失推断，或丢掉已经接纳的 partial rows。
- fixture 和 parser 自洽，但 fixture 的 envelope/cursor 来自猜测而不是真站；上游一变就静默 0。
- smoke 只断言 memory/profile 不变，却仍写 affinity、snapshot、seen、schedule 或普通行为事件。
- 账号 bootstrap 首版漏接 `_BOOTSTRAP_TASK_TABLES`、bootstrap state、incremental roster/spec/interval，导致 init 后永不刷新或 runtime 抢跑。
- crash adoption 只继承 task id，不继承 created_at/cursor/deadline；完成后立即再排。seen-key 用 set/sort 丢接收顺序，child identity 回落 parent。
- cookie 存在就永久判登录，让当前 401/login wall 输给旧 heartbeat；或 anonymous client 继承开发机 Cookie/Authorization，测试出假成功。
- 平台 alias/strategy/host 未进 canonical registry，写库出现多种 family；source-scoped SQL 先 LIMIT 后过滤，别的平台缓存混进本源。
- 用 `discovered_at` 冒充 `published_at`，让历史内容因为刚抓到而获得“新鲜”加分；评估路径没有统一精确 UTC `evaluated_at`。
- 新平台同时漏掉 provider/action/spec/model 字段，旧参数化测试仍绿；误以为 registry 自动生成 API response 和 UI roster。
- 只给 `VERIFY_ACTIONS` 加 `browser_heartbeat`，却没登记 source-specific prefix/getter/event handler；新来源被默认分派成另一个平台的登录态。
- full browser bootstrap 只有 fetch 命令和任务表，漏 `init_prereqs`、CLI `--yes/--no` 或 callback allowlist；setup 看不到来源，或 init 中 callback 被 409。
- 只加了爬取命令，没有接 formal discover。
- 把「匿名可用 + 可选凭据」误建模成「无需登录，所以没有凭据状态」，导致有效 / 失效令牌在 UI 中不可见。
- public discover 匿名、private bootstrap 登录，却硬选一个全源 `auth_required`；结果不是公开搜索被错误拦截，就是 setup/init 把未登录私有任务涂成可用。
- 把页面观察到的 uid / 用户名直接标成 verified；或把 404、uid mismatch、网络失败当作「上游答复过，所以已验证」。
- 后端已有账号 fallback，前端仍复制准入 guard，导致合法初始化请求根本没有发出。
- 配置保存把省略字段、空字符串和掩码当成同一件事，prefill 失败时静默清掉用户原配置。
- 只渲染错误响应，丢掉 202 成功响应里的 warning。
- Search 型来源只接 `KeywordFetchCoordinator.claim()`，漏掉 `KeywordPlanner` 平台集合和 merged prompt，导致 query generation 没有真正复用统一链路。
- 只接 merged prompt 轨，漏掉 keyword inspiration axis 轨的 `_PLATFORM_QUERY_STYLES` / allocation targets（或反之），新平台在其中一条生成轨上永远拿不到词。
- 策略 / producer 自设 admission min_score，绕过 `discovery/admission.py` 的统一入池阈值。
- engagement 计数只在某个 fetch 子路径映射，同一内容换个入口（bootstrap / activity / collection）计数全 0。
- 把外部 schema 的字符串当列表迭代，或 `str()` 嵌套值，产出逐字符标签、`"['作者']"`、`"None"` 等永久脏数据。
- 用「看起来不像人名」的宽泛过滤删除真实作者（短名、标点名、非拉丁文字）。
- 多 scope/type 收藏按单 lane 顺序抓取，密集 lane 吞光 limit；或在去重前就扣预算。
- 把平台结构性缺失的计数当 bug，硬造占位值或假数据。
- 封面走 `/api/image-proxy` 却没把 CDN 域加进 `ALLOWED_IMAGE_HOST_SUFFIXES`，卡片全部断图 403；CN CDN 没加 direct-fetch 后缀，被系统代理出口 IP 风控拦掉；或只校验 hostname suffix、未在请求和 redirect 时拒绝非 global 解析地址，把图片代理变成 SSRF 通道。
- 只加后端，没有插件登录态任务。
- 用临时浏览器自动化替代真实安装插件的登录态浏览器。
- 用错误 LLM provider / mock provider 跑 eval，和用户本地真实配置不一致。
- smoke 默认写 memory 或触发画像。
- 多个来源分支因为映射到同一 event type 而错误共享额度。
- 配置页能保存，但 runtime source policy 没有使用。
- 只做插件配置页，漏掉 PC Web；或只做平台开关，漏掉候选池 share。
- 旧 `config.toml` 缺新字段时崩溃或默默禁用。
- `/api/sources/status` 永远显示固定状态，或测试漏掉 `unverified` 等插件任务源合法状态。
- 来源关闭时提前返回「未启用」，把已存 / 已拒绝凭据和下一步一起藏掉。
- 推荐卡只适配一端，移动 Web 或插件侧栏破版。
- 推荐卡能显示但按钮 payload / source filter / 打开链接仍按 B 站假设工作。
- CLI / 卡片把所有作者叫「UP主」、把所有内容 ID 叫「BV号」。
- 只跑单元测试，不跑真实 E2E。
- 只 smoke discover 搜索接口，就宣称收藏→事件→初始化链路已验收。
- 网络提示推荐 `[network]` 设置，但真实请求由不读取该设置的子进程或浏览器发出。
- 真实 E2E 用了临时自动化浏览器，没有用已安装插件的登录态浏览器。
- 把根目录截图、`dist/`、zip 包等临时产物提交进仓库。
- 发布时复用已存在的版本号或 tag。
- 已经存在 release tag 后继续改旧版本 changelog / README，导致“旧 tag 说明包含新代码”。
- 只确认插件 / 后端 workflow 成功，没等桌面 workflow 更新聚合 release；或发现 Latest Release 里混入上一版插件 / 桌面资产却没有清理。
- Chrome Web Store workflow 没触发，或把“GitHub 插件包已发”误当成“插件市场已提交审核”。
- 混合后端 fallback 成功后，把默认 CLI / SDK credential 也说成已通。
