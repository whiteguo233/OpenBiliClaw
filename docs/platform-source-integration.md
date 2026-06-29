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

如果平台依赖登录态，优先走浏览器插件任务链路。真实 E2E 要使用安装了插件且已有登录态的浏览器，不要用 MCP/CDP 临时浏览器代替，除非用户明确要求只做普通 UI 自动化。

## 1. 调研和架构选择

1. 查是否有稳定官方 API 能拿到目标信号。需要联网时优先官方文档 / 一手资料。
2. 没有稳定 API 时，参考 XHS / 抖音 / YouTube / 知乎的浏览器插件任务模式：
   - 后端入队任务；
   - 插件打开或复用真实平台 tab；
   - content script 读取 DOM 或同源 JSON endpoint；
   - 插件把规范化结果 POST 回后端；
   - 后端再转换为统一事件或 discover 候选。
3. 先做最小 smoke：
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

## 5. Guided Init 和画像初始化

所有初始化入口都要补：

- CLI：`--yes-<slug>` / `--no-<slug>`，必要时加分支上限参数。
- Desktop `/setup/` 来源选择。
- 插件 guided-init checklist。
- API init models、init status 和进度展示。

规则：

- 新可选平台默认 opt-in 提示，不阻塞 B 站或其他已选平台初始化。
- 平台登录缺失只影响该平台，不应让其他来源无法初始化。
- init 任务结果必须绑定当前 init run，避免扩展延迟结果误写 memory。
- smoke 后若需要写 memory，必须用显式 flag，例如 `--write-memory`。
- 画像重建必须显式，例如 `--rebuild-profile`，且应隐含写 memory。
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
- creator / related 需要 seed；冷启动时可用同轮 search / hot / feed 结果兜底。
- 停止时给明确 reason：`pool_full`、`source_disabled`、`mode_disabled`、`budget_exhausted`、`login_required` 等。
- 候选入池必须尊重 `[scheduler.pool_source_shares]`。

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
- 推 tag 前先查远端是否已存在同名 tag
- 常规 tag：
  - `backend-vX.Y.Z`
  - `extension-vA.B.C`
  - `desktop-vX.Y.Z`
- 确认 GitHub Actions 成功。
- 确认聚合 release `openbiliclaw-vX.Y.Z` 只包含当前版本资产。

## 常见失败模式

- 只加了爬取命令，没有接 formal discover。
- 只加后端，没有插件登录态任务。
- 用临时浏览器自动化替代真实安装插件的登录态浏览器。
- smoke 默认写 memory 或触发画像。
- 多个来源分支因为映射到同一 event type 而错误共享额度。
- 配置页能保存，但 runtime source policy 没有使用。
- 旧 `config.toml` 缺新字段时崩溃或默默禁用。
- `/api/sources/status` 永远显示固定状态。
- 推荐卡只适配一端，移动 Web 或插件侧栏破版。
- 只跑单元测试，不跑真实 E2E。
- 把根目录截图、`dist/`、zip 包等临时产物提交进仓库。
- 发布时复用已存在的版本号或 tag。
