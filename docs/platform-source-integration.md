# 新平台来源接入指南

> 这份指南沉淀自接入知乎来源的完整经历。目标是让后续新增任意平台时，都按同一套路径完成：事件抓取、初始化画像、discover、配置页、推荐卡、真实端到端测试、文档和发布。

## 核心原则

新增平台不是“加一个爬虫”，而是新增一个完整来源契约。只有下面链路都打通，才算功能完备：

- 后端事件 / 候选转换
- 浏览器插件或服务端取数路径
- CLI smoke 命令
- guided init 和画像初始化
- formal discover 调度
- 配置页和来源比例
- 桌面 Web / 移动 Web / 插件推荐卡
- LLM eval / 推荐链路中的候选兼容
- 单元测试和真实登录态 E2E
- 文档、版本和发布资产

优先复用现有平台模式，不要发明孤立路径。

| 目标 | 优先参考 |
| --- | --- |
| 登录态浏览器取数 | `extension/src/background/*-task-dispatcher.ts`、`extension/src/content/*/task-executor.ts`、`src/openbiliclaw/sources/*_tasks.py` |
| 服务端 / 直连 discover | `src/openbiliclaw/discovery/strategies/x.py`、`douyin_direct.py`、`bilibili_producer.py` |
| 初始化画像 | `src/openbiliclaw/cli.py` 中 B 站 / XHS / 抖音 / YouTube / X / 知乎路径 |
| 配置页 | `src/openbiliclaw/config.py`、`src/openbiliclaw/api/app.py`、`extension/popup/*`、`src/openbiliclaw/web/desktop/assets/js/app.js` |
| 纯文本推荐卡 | X / 知乎三端推荐卡处理 |

## 0. 定义来源契约

动代码前先写清楚：

- `slug`：平台全局 key，如 `zhihu`，必须在配置、事件、候选、UI 中一致。
- 内容单元：视频、笔记、推文、回答、文章、问题、帖子等。
- 事件类型：`view`、`like`、`favorite`、`follow`、`comment`、`share`、`dislike` 等。
- discover 模式：`search`、`hot`、`feed`、`creator`、`related` 或平台等价模式。
- 取数方式：官方 API、服务端 cookie replay、浏览器插件登录态、导入文件或混合方案。
- 是否只读：默认只读，不主动改变用户平台状态。
- 每个分支额度：按真实来源分支独立定义，不要因为多个分支最后都映射成 `favorite` 就共享上限。
- 状态变更边界：哪些 E2E 动作只读 / 安全，哪些会改变账号状态，必须提前写清楚。
- 三端内容卡形态：有封面、无封面文字、长文本、外链、评论 / 帖子 / 回答等特殊类型。

如果平台依赖登录态，优先走浏览器插件任务链路。真实 E2E 要使用安装了插件且已有登录态的浏览器，不要用 MCP/CDP 临时浏览器代替，除非用户明确要求只做普通 UI 自动化。

## 1. 调研和架构选择

1. 查是否有稳定官方 API 能拿到目标信号。需要联网时优先官方文档 / 一手资料。
2. 没有稳定 API 时，参考 XHS / 抖音 / YouTube / 知乎的浏览器插件任务模式：
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
4. 先做最小 smoke：
   - `fetch-<slug>` 或 `discover-<slug> <keyword>`；
   - 默认不写 memory、不触发画像；
   - 终端打印分支计数和失败原因；
   - 后端持久化任务结果，方便状态页和 debug。

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

必须满足：

- 平台原始 row 转成统一事件时带 `source_platform=<slug>`。
- metadata 保留平台稳定 ID、URL、作者、来源分支、原始互动动作等可解释字段。
- `signal_strength` 语义和其他平台一致；平台自带强度优先，缺失时用统一兜底。
- smoke 任务默认不写 memory、不触发画像。
- init / profile 任务必须显式带当前 init ownership 或 `profile_update=true` 等语义，避免普通 smoke 污染画像。
- `/api/sources/status` 基于最近任务结果给出 `ready`、`missing`、`partial`、`unverified`、`login_required` 等真实状态，不要硬编码 `no_auth`。
- 插件任务平台通常需要 `/api/sources/<slug>/next-task`、`/task-result`、`/kick`。

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
- 普通行为采集和显式任务执行隔离。
- 任务 tab 用 hash/query 标记，content script 在任务模式下只跑 executor，不上报普通浏览事件。
- 任务必须有超时和结构化错误，不要长期 pending。
- content executor 只做同源 DOM/JSON 归一化，最终事件权重、画像写入由后端决定。
- 现代站点常把按钮放在 Web Components / open shadow root 里；点击 / 分享 / 收藏等 E2E selector 要能处理 open shadow DOM、slot、icon-only button 和动态 aria/title/data-testid。
- 默认 E2E 只跑不改变账号状态的动作。`like`、`favorite`、`follow`、`save`、`upvote`、`subscribe` 等会改真实账号状态的动作必须有显式 `allow_state_changing` / 测试号 / 用户授权。
- 行为事件采集要验证 DB 里的统一事件：`source_platform`、稳定内容 ID、URL、作者 / subreddit / topic、target metadata 和 dedupe key 都要能追溯。
- 测试覆盖 URL 分类、任务校验、timeout、登录失败、分支 cap、normalizer、dispatcher 回传。

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
- `/api/sources/status` 要支持该来源真实状态枚举。插件任务源常见 `unverified`：尚无任务证明不是失败，测试不能只允许 `ready/missing`。
- 开关关闭时保留用户填写的 share / budget，但 runtime 有效配比必须剔除该平台。

## 5. Guided Init 和画像初始化

所有初始化入口都要补：

- CLI：`--yes-<slug>` / `--no-<slug>`，必要时加分支上限参数。
- Desktop `/setup/` 来源选择。
- 插件 guided-init checklist。
- API init models、init status 和进度展示。

规则：

- 新可选平台默认 opt-in 提示，不阻塞 B 站或其他已选平台初始化。
- 如果平台能在已登录浏览器内稳定读取个人行为信号，应优先实现 `bootstrap_events` / `bootstrap_profile`：明确每个 scope、默认上限（当前强信号平台通常每 scope 300）、事件映射（例如 saved → `favorite`、upvoted/liked → `like`、subscribed/following → `follow`），并允许该平台作为唯一初始化来源，只要真实拉到至少一条信号。
- 如果平台只启用后续 discovery、不在 init 阶段产生个人行为信号，必须在 CLI / API / 插件 / Web UI 中标成 discovery-only，且不能作为唯一画像初始化来源；只选择这类来源时应给出明确错误（例如 `no_profile_signal_sources`），不要等到最后落成 `empty_signals`。
- 平台登录缺失只影响该平台，不应让其他来源无法初始化。
- init 任务结果必须绑定当前 init run，避免扩展延迟结果误写 memory。
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
  - **生成侧**：把 `<slug>` 加进 `KeywordPlanner` 的平台集合，更新 `build_merged_keywords_prompt` 的静态 `<supply_advantage>` / 允许 key / schema 示例，并补测试证明 `<slug>` 缺口会触发一次 merged LLM 生成。
  - **抓取侧**：producer 使用 `KeywordFetchCoordinator.claim(<slug>)` 领取关键词，把 `source_keyword_id` 透传到候选；关键词池为空时回退画像关键词，抓取失败时标 `failed`，成功交付候选后标 `used`。
  - 只做 claim/fetch、不进 planner generation，会导致正式 discover 长期只能吃画像 fallback 或旧词库，不算接入完成。
  - 补齐某个平台时顺手审计所有已接入 search 型来源；文档写着“使用统一关键词”的来源必须都有 generation 测试覆盖，不能只在 producer 里 claim。
- creator / related 需要 seed；冷启动时可用同轮 search / hot / feed 结果兜底。
- 停止时给明确 reason：`pool_full`、`source_disabled`、`mode_disabled`、`budget_exhausted`、`login_required` 等。
- 候选入池必须尊重 `[scheduler.pool_source_shares]`。
- 正式 producer 与 smoke 命令的终端文案要描述真实后端；默认走插件时不要残留“命令后端”之类旧提示。
- discovery 入池后至少抽查 DB：`source_platform`、`source_strategy`、`source_keyword_id`、内容 URL、body_text / content_type 等字段能被 evaluator 和推荐卡消费。

## 6.5 Eval / 推荐链路接入

新来源不只是能抓到候选，还要能走完推荐闭环。

检查项：

- `DiscoveredContent` 字段足够 evaluator 判断：标题、作者、正文 / 摘要、标签、URL、内容类型。
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
- 打开链接正确。
- 无封面来源有 text-card fallback。
- 非 B 站内容不会误构造 B 站 URL。
- 稍后再看、收藏、忽略、不感兴趣、聊一聊等动作仍可用。
- 长标题、长摘要、无封面卡片不会遮挡按钮。
- 桌面、移动、插件侧栏都做截图或视觉检查。
- 推荐页平台过滤 / source badge / source label 要包含新平台。
- 如果源主要是文字内容，要确认 text-card 在 PC、移动、插件三端都不是断图 fallback，按钮不会被正文遮挡。

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

插件常见测试：

- `extension/tests/<slug>-adapter.test.ts`
- `extension/tests/<slug>-task-dispatcher.test.ts`
- `extension/tests/<slug>-task-executor.test.ts`
- popup/settings/init 相关测试

完成前至少跑：

```bash
.venv/bin/ruff check src tests
.venv/bin/mypy src
.venv/bin/pytest -q --tb=short
cd extension && npm test && npm run typecheck && npm run build
```

对大改动，最终还要跑一次全量 `.venv/bin/pytest -q` 和 `npm test`。如果全量检查发现旧测试断言漏了新合法状态（如插件源 `unverified`），修测试；如果 `ruff format --check src tests` 命中历史无关文件，不要顺手格式化，先用 `origin/main:<path>` 验证是否已在 main 修复，并在交付说明里说明。

发布前插件包验证：

```bash
cd extension
npm run package:only -- --archive-version <extension-version>
npm run build:firefox
npm run package:firefox:only -- --archive-version <extension-version>
```

全仓 `ruff format --check src tests` 如果命中历史无关文件，不要顺手大规模格式化；只格式化本次改动文件。

## 9. 真实端到端验证

登录态相关来源必须用真实扩展浏览器验证。

验证阶梯：

1. 启动后端，确认使用的 data/config root 和扩展连接的是同一套环境。
2. 重新加载本地构建的插件；若项目已有热更新，可按现有机制使用。
3. 打开平台页面，确认当前浏览器已登录。
4. 跑 `fetch-<slug>` 或 discover smoke，看分支计数、cap、错误原因。
5. 每个 discover mode 跑一次，确认候选入 `discovery_candidates`，或因合理 reason 停止。
6. 跑 `openbiliclaw discover --source <slug>`，确认正式 producer 通。
7. 在插件配置页和桌面 Web 配置页保存 source modes / source share，回读 `/api/config`。
8. 桌面 Web、移动 Web、插件 side panel 都看推荐卡样式。
9. 如用户要求，跑 `--write-memory` / `--rebuild-profile`，确认 memory/profile 真的变化。

真实 E2E 的终端输出、任务 result、数据库计数比截图更有价值；截图只作为临时视觉证据。

真实 E2E 要分层报告：

- 安全动作：snapshot / scroll / click / share / search / hot / related 等默认可以跑。
- 状态变更动作：like / favorite / follow / save / upvote / subscribe 只在用户明确允许或测试号中跑。
- 配置动作：插件页和 PC Web 保存后必须回读 `/api/config`，再确认 runtime source policy / pool share 生效。
- 推荐动作：三端截图或像素/DOM 检查要覆盖长标题、无封面、文字卡和按钮区域。
- 画像 / eval：使用真实本地配置的 LLM provider，记录 provider、命令、候选 / 事件计数和最终 profile / candidate 状态。
- 混合后端动作：如果默认后端会 fallback 到插件，报告时要把“默认后端成功”和“fallback 成功”拆开说；例如 CLI / SDK credential 未就绪但插件 fallback 完成 discovery，不能表述成默认后端已通。
- Cookie / credential 同步动作：如果实现了插件同步第三方 CLI credential，要同时验证后端 endpoint、插件 runtime-stream / hot reload、浏览器 cookie 可读性和最终 credential 文件；若真实浏览器缺必要 cookie 名，要记录“不阻塞 fallback，但默认命令后端仍 login_required”。

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

发布检查：

- 后端版本：`pyproject.toml`、`src/openbiliclaw/__init__.py`、`uv.lock`
- 插件版本：`extension/package.json`、`extension/package-lock.json`、`extension/manifest.json`
- 首页版本 / SEO：`docs/index.html` 的 `softwareVersion`、meta description、首页 source card、英文翻译都要包含新平台。
- README / README_EN 顶部定位、核心特性、安装登录说明、架构图中的来源列表都要同步。
- 如果新增 / 修改了本指南或 skill，确认不是未跟踪文件，并与 `origin/main` 已存在版本做 diff，避免合并时丢掉后补规则。
- 推 tag 前先查远端是否已存在同名 tag；如果同名 tag 已经存在，不要改旧 release 对应的 changelog 语义，必须 bump 新版本并把新改动放进新的 changelog block。
- 常规 tag：
  - `backend-vX.Y.Z`
  - `extension-vA.B.C`
  - `desktop-vX.Y.Z`
- 本地提交前跑 `git status --short --ignored` 看清楚：未跟踪设计稿、截图、`dist/`、zip/xpi/dmg/exe、临时 release 包不要误提交；只有文档引用的图片或明确要求入库的资产才纳入提交。
- 本地可先用 `uv build` 验证后端 sdist / wheel；当前项目 venv 可能没有 `python -m build`，不要因此把 `build` 加进运行时依赖。
- 插件本地包验证后，release 产物仍以 tag-triggered GitHub Actions 为准；本地 zip 只是验证，不提交。
- backend release 是 source tag 校验，不一定有 GitHub Release 资产；插件和桌面安装包由对应 workflow 发布，聚合 release 再收敛当前插件 / 桌面资产。
- 推 main 后再推 tag，确认 CI、backend source tag、extension package、desktop installers、pages build 都成功；聚合 release 只允许收录同版本资产，某个 channel 还没完成时应显示未发布，不能回填上一版桌面或插件包。
- 确认聚合 release `openbiliclaw-vX.Y.Z` 只包含当前版本资产，尤其不要混入旧 `.dmg` / `.exe`。
- 如果有 Chrome Web Store / Firefox AMO / 其他插件市场，按项目 workflow 触发上传或说明为什么不能发；Chrome Web Store 审核异步，成功上传不等于立刻对用户可见。
- 发布后把 release 链接、tag、commit、workflow 结果和本地残留未提交文件一起汇报。

## 常见失败模式

- 只加了爬取命令，没有接 formal discover。
- Search 型来源只接 `KeywordFetchCoordinator.claim()`，漏掉 `KeywordPlanner` 平台集合和 merged prompt，导致 query generation 没有真正复用统一链路。
- 只加后端，没有插件登录态任务。
- 用临时浏览器自动化替代真实安装插件的登录态浏览器。
- 用错误 LLM provider / mock provider 跑 eval，和用户本地真实配置不一致。
- smoke 默认写 memory 或触发画像。
- 多个来源分支因为映射到同一 event type 而错误共享额度。
- 配置页能保存，但 runtime source policy 没有使用。
- 只做插件配置页，漏掉 PC Web；或只做平台开关，漏掉候选池 share。
- 旧 `config.toml` 缺新字段时崩溃或默默禁用。
- `/api/sources/status` 永远显示固定状态，或测试漏掉 `unverified` 等插件任务源合法状态。
- 推荐卡只适配一端，移动 Web 或插件侧栏破版。
- 推荐卡能显示但按钮 payload / source filter / 打开链接仍按 B 站假设工作。
- 只跑单元测试，不跑真实 E2E。
- 真实 E2E 用了临时自动化浏览器，没有用已安装插件的登录态浏览器。
- 把根目录截图、`dist/`、zip 包等临时产物提交进仓库。
- 发布时复用已存在的版本号或 tag。
- 已经存在 release tag 后继续改旧版本 changelog / README，导致“旧 tag 说明包含新代码”。
- 只确认插件 / 后端 workflow 成功，没等桌面 workflow 更新聚合 release；或发现 Latest Release 里混入上一版插件 / 桌面资产却没有清理。
- Chrome Web Store workflow 没触发，或把“GitHub 插件包已发”误当成“插件市场已提交审核”。
- 混合后端 fallback 成功后，把默认 CLI / SDK credential 也说成已通。
