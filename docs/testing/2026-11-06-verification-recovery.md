# Ego 验证恢复与重复采集验收（2026-11-06）

后续用户已同意临时代理对照，结果和新发现的验证域授权修复见
[哔哩轻小说最新记录](2026-11-06-bilinovel-proxy-verification.md)。下文“待确认”为当时状态。

Integration level：**discovery-only repair**。承接[上一轮剩余站点修复](2026-11-06-remaining-sites-fixes.md)，
继续处理 V2EX 验证与哔哩轻小说直连阻断。契约沿用
[dynamic-web](../platform-source-contract.dynamic-web.toml)。这不是全部网站、真实账号登录或安装包的重新认证。

## 环境与代码

- 独立 worktree `ego-verification-recovery-20261106`，分支
  `fix/ego-verification-recovery-20261106`，基线 `0be53fc96c9645482cf6e5f4602dbafec7921e08`。
  主工作区本轮起点为 `4c2c2267`，既有未跟踪目录及其他任务不改动；不合并、推送或发布。
- 复用隔离目录 `/tmp/obc-multisite-20261106`；证据保存在私有 `followup/`。
  起点是 14 个停用来源、47 次运行、39 个候选、15 条缓存，不能算作从零安装。
  API 18612、所属 worker 和测试导入本 worktree；Ego 子进程每次加载此处固定宿主脚本。
  新编制提示上线时只重启测试 API 以载入 Python 代码，不是用户运行中必须重启的恢复步骤。
- 官方 Ego Lite 0.6.2.7，专用 profile，实际网页请求全部走 Ego。
  Luna 使用已授权的官方 GPT-6 Luna CLI 兼容桥（18613），embedding 使用实际
  Ollama `nomic-embed-text`；没有模型答案回放、直接注入候选或降低准入门槛。
- 原生 App 产品代码保持 `OpenBiliClaw-mobile-remaining-sites-20261106` 的 `379cfcc`。
  测试目标为 iPhone 17 Pro / iOS 26.5 模拟器，真实 Flutter 原生构建；不是 PWA 或物理手机。
  live harness 与产物仅保存在私有目录，不把设备设置、原始协议或截图提交到仓库。
- 保留用户指定的 Ego 精确直连域 `bilinovel.com`、`www.bilinovel.com`；其他网站仍走
  系统代理。OS 网络、个人浏览器和其他 Ego 环境均不更改。

## 复现、修复与边界

| 问题 | 真实证据与修复 | 保留边界 |
| --- | --- | --- |
| V2EX 验证始终停留 | 原页面为 403 Document、标题“请稍候…”、`unsupported_embedded_pages=1`；人工模式同样阻止所有内嵌页。修复后实际 Ego 自动完成网站自身检查，显示热门主题，继续试跑 `ready/true`、3 条、18.90 秒 | 没有自动解题、点击验证框或输入账号。仅人工模式允许自有 iframe，仍走精确内容／已授权登录域及公共 DNS 守卫；后台 iframe 仍拒绝 |
| 子页面权限与预算 | 自有 iframe 子会话保持暂停，安装请求／响应守卫后才恢复；未暂停的附加目标拒绝；父会话脱离会递归撤销子会话。最多八个附加子会话，共用 250 请求、4 MiB 单响应和 16 MiB 总观察预算 | 弹窗、worker、其他任务事件不取得权限；静态资源授权不升级为文档授权。流量是已观察阈值，不宣称 socket 硬配额 |
| 广告被拒误作登录跳转 | 实际验证成功后 Google 广告内嵌页仍被原边界拒绝；现使用有界 `blocked_frame_origins`，不混入顶层导航。子文档 200 不再清掉顶层 403 | 不给广告或未知登录域自动授权；真实跨域账号 SSO 未验收 |
| 只采当前一个帖子 | 第一次原生建源虽然跑通，却保存了 `div.cell.hot_t_1254622`，只能采一个当前主题。新增有界同容器重复结构证据与编制约束；用完全相同的普通用户请求重新建源，真实模型改用 `#TopicsHot > div.cell > table > tbody > tr:has(...)`，取到 3 个不同主题 | 原始路径证据、链接过滤与预算保留；证据只用于编制，既有计划不自动扩范围或迁移；单条、超限、越容器或无效 selector 不提供建议 |

内嵌页、广告诊断、父子撤销和重复行均有修前失败记录，再由实际产品代码修复后通过。
协议 fixture 验证守卫边界；V2EX 实网验证提供独立成功证据，二者不互相替代。
实际模型请求还核对到 `#TopicsHot > div.cell > table > tbody > tr` 的重复结构证据，
匹配数为 10；模型保存了该范围和 `/t/` 链接过滤。不是测试脚本直接替换了 selector。

## 原生 App 与真实采集

- 第一次原生整轮 149 秒通过：聊天 → 真实编制 → 审批 → 设置份额 1→2 → 首次调度 →
  Ego PNG 实际绘制 → 继续试跑 → 返回聊天；另加 Atom 并采到 3 条。
  复查持久计划发现单帖编号缺陷，因此该轮不作为可持续列表采集的最终通过证据。
- 修复重复行证据后，相同普通请求再次从原生聊天新建独立来源，237 秒完成上述整轮；
  本次网页每次试采为 3 条、上限 1 页、间隔 5 分钟，原生设置份额改为 2。
  原生 PNG 人工核看为真实 V2EX 热榜，SHA-256：
  `a0c3ef0636c7369a0fb1f3c561b5451a68dd2b7521eaf1c2ace34683e987d04f`。
  本轮复用已完成验证的专用 profile，不宣称重新通过了一次全新 profile 挑战。
- 最终来源 `custom_560cae8a80d04c78842475b98ded58a3`，首轮正式 worker 读取 3 条，
  其中 1 条已被首次测试发现，新增 2 条。实际评估 0.64 的 SSH 工具主题入池并生成文案，
  0.28 的生活问题因低相关性拒绝；首次来源的另一条为 0.57，同样正常拒绝。
  新缓存保持 `source_platform=web`、`content_type=article` 及独立来源归属。
- 两次原生 Atom 流程均真实解析 3 条；内容已存在，新增候选为 0。不能把重复解析算新入池。
- 最终固定代码另复测原有 NGA、Hacker News、少数派的 Ego 工作流，分别 10.35、7.00、
  8.17 秒，均为 `ready/true`、3 个样例；关闭试跑草稿，原来源保持停用。
  这是传输／选择器兼容复测，没有重复计算为三条新聊天建源或正式调度。

最终来源共四次正式运行：激活版本 1 一次，原生继续试跑发布版本 2 后三次。
版本 2 在本地时间 04:50:03、04:55:13、05:00:21 自然到期执行，分别用时
9.91、7.59、8.05 秒，均读取 3 条、重复入队 0 条。没有 force、改时钟或重置 due。
04:53:29 记录到托管 Ego 空闲退出，后续周期仍成功。版本 1／2 的实际计划 SHA-256 相同：
`26e8ae73052c7bc83cba0af0f626963227dedea758a1276239aec400a562a2d8`。

三次最终版本成功后，测试监视器通过正常管理 API 停用该来源。确认全部测试来源均停用后，
调用真实 `POST /api/recommendations/append`，返回 1 张该 V2EX 来源的文章卡，名称、
`source_id`、`web/article` 与实际 `/t/1254583` 链接一致；随后的 GET 也包含它。
先前 GET 仅返回已有的推荐快照，不把“入池”和“已经在当前推荐页展示”混作一步。
推荐投影在周期证明之后进行，未增加正式采集次数。

## 哔哩轻小说网络边界

当前真实 Ego 直连再次返回 `net::ERR_CONNECTION_RESET`，独立 HTTPS 直连也被重置。
为了区分 DNS、TLS 与浏览器因素，对 www 域本机解析／公共解析的四个 IPv4 和裸域的
两个 IPv4，分别测试默认 TLS 与 TLS 1.2，12 次直接握手全部重置；裸域另四次 IPv6
握手无路由。保留实际域名 SNI 和证书验证，公共 DNS 查询可经过现有代理，但所有目标
握手都是直连；没有把代理请求冒充直连。

这证明当前问题不只发生在 Ego 或某一个解析地址，尚不能定位到具体网络设备或上游方。
用户此前明确选择此站直连；本轮已经询问是否允许临时代理对照，未收到答复前不改变路由。
旧月份成功不算本次通过，也不靠重启或关闭证书验证掩盖失败。

## 检查与验收门

| 门 | 适用性 | 本轮执行 |
| --- | --- | --- |
| Ego 验证恢复与原生聊天／审批／管理／返回 | required | PASS，真实 V2EX 与真实模型；最终新建试跑 3 条 |
| 固化流程正常周期与去重 | required | PASS，最终版本连续三轮各 3 条、0 重复入队；版本切换前另有首轮 |
| 实际评估与入池 | required | PASS，最终来源 2 个新候选中 1 入池、1 拒绝；首次来源另 1 拒绝 |
| 推荐接口来源投影 | required | PASS，真实 append 与后续 GET 返回新增 V2EX 文章，独立来源归属正确 |
| 原生 Atom 接入兼容 | required | PASS，实际解析与调度；已见内容不重复入队 |
| NGA / HN / 少数派 Ego 兼容 | required | PASS，三站各 3 个真实试跑样例 |
| 哔哩轻小说当前真实可达性 | required | BLOCKED，保留指定直连，目标 TLS 重置 |
| iframe 权限与重复结构回归 | required | PASS，实际仓库脚本的协议／DOM fixture；不代替实网登录 |
| 真实账号 SSO、弹窗登录、Windows/Linux、安装包重装 | 本轮未改动／未认证 | NOT_RUN；不由 macOS 公开页面证据推导 |

- 最终 32 个关联测试文件 `548 passed`，296.88 秒；精确文件列表为私有
  `regression-final-files.json`，覆盖 custom、Ego、编译和结构修复。运行前打印导入路径为本 worktree。
- 最后对子会话暂停状态和可选选择器异常边界的补充专项 `54 passed`，64.45 秒；
  与上述集合重叠，不相加。`pytest -q tests/test_ego_embedded_login.py
  tests/test_ego_repeated_rows.py tests/test_ego_collect_dom.py tests/test_ego_native_executor.py`。
- 较早的 iframe 修复关联组合为 `567 passed`，501.18 秒；该轮未包含后来新增的重复行
  修复，不能代替最终组合。原生场景中的外站请求不使用 Playwright；该早期组合包含旧执行器
  的受控本地 fixture，只验证兼容性，不作为 Ego 实网证据。
- `ruff check src/ tests/`、改动 Python 文件 `ruff format --check`、`mypy src/`
  （352 个源文件）及两个产品 `.mjs` 的 `node --check` 均通过。
- `scripts/audit_platform_source.py --contract docs/platform-source-contract.dynamic-web.toml
  --check --json` 为 31 PASS / 11 MANUAL / 17 N/A / 0 MISSING，required missing=0。
  仅证明注册；能力排除项另包含在实际执行的 `test_custom_source_contract.py` 中。
- 原生 Xcode 构建成功（87.1 秒），两轮通过 `flutter drive --driver=test_driver/native_live.dart
  --target=integration_test/native_v2ex_recovery_live_test.dart --use-application-binary=<私有 Runner.app>
  --dart-define-from-file=<私有测试配置> -d <本轮模拟器> --no-pub` 实际运行。
  App 产品代码未改动，本轮没有再把上一轮 168 项常规测试声称为重跑。
- `provenance-final.json` 固定三个后端产品文件、原生二进制与 harness 的 SHA-256；
  API 和四个所属 worker 的实际 cwd 均核对为本 worktree。未重新认证安装器或全仓测试。

## 收尾

最终隔离库保留 18 个来源（全部停用）、55 次运行、42 个候选、16 条缓存；相比起点净新增
4 个来源、8 次正常运行、3 个候选、1 条缓存。全部候选已处理，16 cached / 26 low-score rejected。
11 个本轮使用或创建的草稿控制显式关闭；测试 API、模型桥、监视器及四个 worker 均停止，
18612 / 18613 无监听，专用 Ego 没有遗留进程。测试模拟器恢复 Shutdown，临时 native harness
已移出移动端 worktree，该 worktree 保持 clean。其他环境与主工作区既有未跟踪目录保留。
数据库、配置、截图、模型协议与构建产物只留在私有证据目录；没有上传或发布。
整体仍为 **incremental only**：可用修复已经真实验证，哔哩轻小说的当前网络阻断未解除。
