# 动态 Discovery 来源续建验收（2026-10-22）

本轮目标为 discovery-only：通过聊一聊动态添加网站与 RSS/Atom/JSON Feed 来源，
在同一 Discovery 管线定时采集、评估入池，并在配置页管理独立份额、频率与启停。
继续使用 `feat/dynamic-discovery-sources` 独立 worktree；起点为 `e88965bb`，
整合当前 main `8b99ed46`（v0.3.231）。主工作区的 `outputs/`、`promo/` 未跟踪文件不属于本任务。

## 主线整合

合并保留 TikTok/Instagram、国际化、蓝鱼及运行时恢复改动，同时保留 Web/custom
注册、独立份额与三端展示。API canonical 来源顺序为 main 原序加末尾 Web；UI 展示顺序
可以不同，初始化 roster 继续排除 Web。HTTP 依赖取双方约束交集 `httpx[socks]>=0.28,<1`，
保留默认 Playwright driver 和 main macOS 签名/版本构建规则；`uv lock --offline` 成功。

所有 Python 检查以当前 worktree `src` 导入。需要配置的回归使用临时 cwd/config/data，
不加载生产账号或画像。合并阶段未启动生产服务、浏览器账号任务或发布。

| 验证 | 结果与边界 |
| --- | --- |
| MyPy / Ruff | 全局 349 个源码文件类型检查通过，Ruff 通过 |
| 文档与打包 | 99 passed；`/tmp/dynamic-source-merge-20261022-docs-packaging.log` |
| 前端 | 扩展全套 1833 passed，Web JS 142 passed，typecheck、Chrome/Firefox 构建与资产 preflight 通过；`/tmp/dynamic-ui-merge-*` |
| 配置、认证、CLI、OpenClaw、custom | 原始 1022 passed / 40 skipped / 1 failed；失败为精确 dispatcher 集合漏 main 新平台，更新联合契约后整文件 12 passed；`/tmp/dynamic-merge-backend-tests.log`、`custom-contract.log` |
| API/平台兼容 | 原始 269 passed / 4 failed；旧顺序和 CLI 文案断言更新联合契约后两整文件 67 passed；`/tmp/dynamic-merge-backend-compat.log`、`adjusted-contracts.log` |
| 运行时与配额 | 原始 374 passed / 1 failed，核心 refresh 另 205 passed；API roster 误用 UI 顺序，恢复 main 原序加 Web 后相关两整文件 21 passed；`/tmp/dynamic-source-merge-runtime-result.json` |
| Python 前端契约 | 原始 75 passed / 1 failed，同为 API roster 问题，上述 21 项复跑覆盖；原失败日志保留 |

各组有重叠，不能把通过数相加当作全量测试数；上述均为合并定向验证，新增功能及最终全量结果另记。

## 本轮补齐的产品链路

- 新聊天网站/RSS/Atom/JSON Feed 统一 prepare → trial/ready → activate；在旧 create_source 新审批登记前给出纠正指引，保留既有审批回放和平台关键词订阅。
- 关闭自动调度时手动刷新仍执行自定义来源；interest 先准备统一关键词，候选有界 flush 并等待推荐文案，保留登录、配额、租约和错误/限流退避。待评估原料占满配额时停止继续抓取但仍推进评估。
- 来源 A 的保存/启停/刷新保留 B 未保存输入；失败保留编辑。人工登录操作与截图轮询串行，会话失效显示重开入口，三端与三种语言补齐订阅类型。
- 支持直接 Feed、网页中观察到的 alternate/明确订阅链接、正文生成缺失标题；请求域与文章链接域分别确认，文章仅作引用不自动读取。新域降级不提交完整检查点；专用平台 HTML 不回退浏览器，明确格式并试跑成功的 Feed 可作为独立来源。

此前验收不能代替本轮修改后的验证，尤其是真实聊天、实际定时采集、模型评估与安装产物。
历史记录见[动态来源验收](../platform-source-acceptance.dynamic-web.md)。

## 新增行为的定向验证

- 聊天路由、可信范围审批、技能与平台操作回归：最终 74 passed，`/tmp/dynamic-source-chat-routing-final.log`（补充平台名伪装 Feed 的路由回归）。
- 新 Feed 全纵向和旧 parser/recipe/probe/adapter/API/agent 兼容：230 passed，`/tmp/dynamic-feed-focused.log`。
- 前端最终：扩展 1833 passed、Web JS 150 passed；三语目录/i18n+contract 45 passed，最后登录提示改动后 i18n 33 passed，Chrome/Firefox 重新构建各 22 项资产检查通过。最后一次全 Web JS 原 149 passed / 1 failed 为旧聊天标签断言，修正后 150 passed，原失败日志保留在 `/tmp/dynamic-ui-login-activation-*`。登录更新标记用例 red → green，登录页共 8 passed。隔离 UI fixture 的 desktop/375px 以及英文登录失效→重开验证见 `output/playwright/dynamic-source-fixes/`；这些不能替代真实服务证据。
- 独立协议调查使用临时 feedparser 环境，公开 Django RSS、GitHub Requests Atom、JSON Feed 官网、BBC RSS 均 HTTP 200；调查不是产品传输成功证据。脱敏摘要 `/tmp/dynamic-source-feed-upstream-20261022.json`。格式规则引用见 Web 模块文档。

- 手动刷新/关键词/候选预算/租约/来源契约 10 个文件：456 passed / 3 warnings，`/tmp/dynamic-custom-manual-regression-verified.log`。新测试证实并修复 `flush` 总预算被并发再次放大的问题；原失败和修复日志保留。
- 全局 Ruff、839 文件格式检查、MyPy 349 文件通过。注册审计 31 PASS、0 required missing，11 项人工证据待另行核对，不把注册审计写成完整 E2E。
- 最终 wheel 构建成功，26 个本轮关键产品/资源文件与工作树及全新安装副本逐字节一致：`/tmp/dynamic-source-final-wheel-provenance.json`。SHA-256 为 `683cbd5dd9c845459166adb8ab4444454b8884bd87d31353fb3fc86dc6702b06`，新安装目标 `/tmp/dynamic-source-final-wheel-install-k6ko7irz`。

## 全量回归与失败归因

隔离运行根执行全量 `pytest`：**11169 passed / 72 skipped / 5 failed**，耗时 2443 秒，
原始日志 `/tmp/dynamic-source-final-pytest-full.log` 保留。没有把它表述为最终版本的一次全绿运行：

- 一项桌面卡片结构断言同时保留了“仅截取 Linux.do/V2EX”与“应该有三个来源”的合并残留。
  改为检查所有卡片为兄弟节点以及各自的份额输入，不固定中间平台数量。
- 一项 Web 日期默认配置缺失，已按上节接入既有日期配置与准入。
- Instagram、Web、微博的三个刷新接线断言使用 `inspect.getsource`；全量进程加载旧版本后，
  运行中为真实验收修复刷新逻辑，源码行号发生变化，导致它们读到了相邻函数。
  新进程重跑无需修改这三个测试或生产接线。

上述五个失败所在完整文件在最终对应源码上合并重跑：**101 passed**，
`/tmp/dynamic-source-final-full-failures-rerun.log`。日期配置、配置 API 与事务保护四文件回归
原 **447 passed / 1 failed**；唯一失败为新测试漏注册其自定义来源，修正测试数据后日期文件
**12 passed**，并已包含在上述 101 项中。日志 `/tmp/dynamic-source-final-web-date*.log`。
文档、安装契约及打包相关七文件最终 **152 passed / 2 skipped**，
`/tmp/dynamic-source-final-docs-packaging.log`。各组测试有重叠，不累加为总测试数。

## 真实公开订阅到候选队列

默认 `SafeWebTransport`，真实 DNS/socket pin/robots，无生产配置、账号或模型替身，临时数据库。
调用实际 prepare(http) → trial → activate → CustomWorkflowProducer；这些数据仅证明采集和入队，尚不代表模型评估。

| 实际入口 | 试跑与运行 | 发现 / 入队 |
| --- | --- | --- |
| `https://www.djangoproject.com/rss/weblog/` | RSS ready / ok | 10 / 5 |
| `https://simonwillison.net/atom/everything/` | Atom ready / ok | 20 / 5 |
| `https://www.jsonfeed.org/feed.json` | JSON Feed ready / ok | 2 / 2 |
| GitHub Requests releases.atom | robots_denied，未启用 | 0 / 0 |
| BBC Technology RSS | robots_unavailable，未启用 | 0 / 0 |

成功入口和不可用入口均保留：`/tmp/dynamic-source-feed-live-service.json`、
`/tmp/dynamic-source-feed-transport-check.json`。未绕过 robots，不能由普通 HTTP 200 推导为产品采集可用。

## 真实验收暴露问题后的修复

- 首轮空池聊天中，`sources.web.workflow` 被归为 maintenance；其 120 秒调用超时早于空池维护任务 300 秒豁免，默认 auto 连续失败。模型随后主动选 HTTP，Django 才完成试跑和审批。保留原始失败，不把这轮写成 auto 正常。
- 用户 prepare/trial 改用精确 interactive caller；后台自动 repair 仍用 maintenance。新增真实 gate 行为测试原 4 failed / 1 passed → 5 passed；10 个相关完整文件最终 186 passed，`/tmp/dynamic-workflow-concurrency-regression.log`。
- 独立复核发现 Instagram 刷新/文案失败会中断后续自定义来源。聚合层按来源波隔离异常，保留失败提示，让其余来源继续；三文件 219 passed，`/tmp/dynamic-source-manual-wave-isolation.log`。
- 全量回归发现主线日期契约已覆盖所有 canonical 来源，但 Web 配置漏接公共日期字段。
  补齐 TOML、配置 API 和候选准入映射，默认 `all`；不新增实例日期编辑器或改变默认筛选规则。
- 最后复核复现新文章域恢复时会追加 workflow、留下旧 degraded 流程。新增显式
  `source_id + workflow_id` 更新路径，服务端固定 `base_version`，试跑及审批后原子替换，
  保留 seen/历史并使旧 lease 失效；跨来源、未知 ID、过期版本均拒绝。聊天只读列表提供流程
  ID/版本/目标，确认卡展示原流程与范围变化。更新的登录恢复只试跑，仍须返回聊天确认；
  旧已批准流程重新登录保持兼容。16 个相关完整文件最终 **208 passed**，
  `/tmp/dynamic-workflow-update-regression.log`。
- 最终 wheel 实际安装到新临时目标，JSON Feed prepare → trial → activate → fetch 为 ready/ok/2 条；配置展示命令 exit 0，运行根为仅含样例的独立目录。`--no-deps` 借用已存在的依赖，不冒充全新系统依赖安装；本 smoke 没有模型评估。证据见 `/tmp/dynamic-source-final-wheel-provenance.json`。

## 实际服务与三端操作

使用独立运行根 `/tmp/dynamic-source-live-20261022` 和端口 `18495` 启动完整 `create_app`。
加载实际配置的模型与 Ollama embedding，模型凭据只保留在内存，落盘配置清空 key；未导入
生产平台账号、历史或画像，使用明确标记的合成兴趣画像。三端均操作真实 SSE、Agent 工具和
审批接口，无伪造工具响应。插件为本分支 Chrome MV3 构建在临时 profile 中的 unpacked 安装。

- 桌面批准 Django RSS、移动端批准 Simon Willison Atom、插件批准 JSON Feed 官网来源；
  实际来源数由 0 → 1 → 2 → 3。修复交互优先级后，Atom 与 JSON Feed 均首次默认 auto 成功。
- 三源份额 2:1:1 时，300 条目标的独立配额为 150/75/75。插件把 Atom 份额 1 → 3 → 1，
  两次实际保存均由读取接口确认；另一个来源未保存的名称保留，数据库中的该名称没有被误写。
- Atom 首轮自然调度发现 20 / 入队 20，下一自然轮返回 `not_modified`、0 / 0。
  JSON Feed 首轮发现 2 / 入队 2，第二轮重新读取 2 条但 seen 去重后入队 0。
- Django 已通过早先同一产品传输的试跑与采集，但完整服务中的五次自然轮均遇到
  `robots_unavailable` 并保留退避；不把早先的成功挪用为本次自然轮成功。
- JSON Feed 的两条候选完成真实评估，均按现有规则拒绝；未调低阈值或强行入池。
  Atom 的 20 条评估遇到实际 provider 返回空内容后的 SDK 重试，维持原 1200 秒超时。
  原批在 11:59:15 UTC 以 `output budget exhausted` 结束，现有引擎随后自动拆批重试。
  观测到 `refill_active=1`、`maintenance_waiting=0`，不是交互优先级再次阻塞。
- 11:57:15 UTC 从插件停用 JSON Feed，另两源保持启用；跨卡未保存编辑继续保留。
  12:02:03 UTC 已超过其原到期时间 90 秒，运行次数仍为 3，另两源期间继续运行两次；
  seen 与既有记录保留，停用来源退出有效配额，剩余配额为 200:100。
- 为有界结束本轮外部模型验收，12:06:18 UTC 正常提交关闭调度的配置并等待 applied。
  原模型批失败后的在途拆分重试由现有生命周期取消，20 条候选回到 pending，未伪造评分或推荐。
- 12:07:57 UTC 在真实首页点击一次「换一批」，`POST /api/recommendations/reshuffle` 返回
  HTTP 200 / 空列表。关闭调度后仍创建并执行 Atom 的手动 run（`not_modified`），
  Django 在错误退避内不强制采集，已停用 JSON Feed 不运行。手动评估实际领取 20 条 pending
  并发起模型请求，随后再次限流；目标观测 120 秒，工具调度后于 136.9 秒正常关闭服务，
  manual job 仍未完成，落盘候选仍为 evaluating，不能写成已评估或已回到 pending。
  不把 HTTP 200 算成最终入池完成。

页面、审批卡、比例保存及停用截图与操作记录：`output/playwright/dynamic-source-live/`。
服务事件、持久状态快照与模型长尾观测：`/tmp/dynamic-source-live-20261022/`。
本轮真实服务证明了三端建源/管理、自然与手动采集、去重和评估触发；真实 JSON Feed 评估两条
均拒绝，Atom 受 provider 输出预算和限流影响，未产出可推荐卡。因此三端真实推荐卡
`source_name` 展示记为 `NOT_RUN_NO_REAL_CARD`，不能宣称这次真实入池 E2E 全通过。
评分通过后的入池、文案、归属 DTO 与三端显示由定向单元/集成及受控 UI 测试覆盖，
它们与真实模型阳性证据分列。

最终 Python 源码启动同一验收数据库，12:13:43 UTC 健康检查通过，调度仍关闭。来源、流程、
版本内容、运行历史、游标及计划时间、seen、22 个候选身份、缓存共 8 组哈希保持一致；
20 个中断的 evaluating 由正常启动恢复为 pending_eval，两条 rejected_low_score 及 JSON Feed
停用状态保留。证据 `final-restart-persistence.json`、`final-python-source-sha256.json`；
完整分层结论见同目录 `acceptance-summary.json`。

## 交付边界

- 动态来源入口为桌面 Web、移动 Web 和插件的「聊一聊」及设置；CLI `chat` 暂未接入
  动态来源工具，旧 Web recipe CLI 保持兼容。
- 浏览器执行使用现有 Playwright 隔离实现，HTTP 覆盖公开 Feed、Sitemap 和静态列表。
  Ego Lite 宿主 bridge 仍未接入，执行器明确报告不可用，不能称为 Ego 已验证。
- 本轮不增加账号历史初始化或站内写入；需要登录的网站经人工接管，具体站点仍可能受
  robots、登录方式或页面变化限制。Feed 仅有界读取当前内容，不承诺抓全历史。
- 安装证据为当前 macOS 上 wheel 的新目录安装、真实公开 JSON Feed smoke、三端构建与
  unpacked Chrome 插件；没有将这些写成 Docker、Windows 或桌面安装器的完整复验。
- 代码在独立 `feat/dynamic-discovery-sources` worktree；本轮未合回主线、推送或部署生产服务。
