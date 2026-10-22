# Ego 自动部署与真实聊天建源复验

范围：`capability-increment`，真实部署恢复、聊天添加网页/Feed、定时采集、真实模型评估、
入池及来源管理。基线 `fe2ef1f5`，继续使用 `feat/dynamic-discovery-sources` 独立 worktree。
本记录保留失败与修后结果，不用之前的 Playwright 或 Ego 验收替代本轮。

## 运行与隔离

- 根目录 `/tmp/obc-ego-e2e-20261023-0315`；真实项目 CLI `serve-api` 监听 `18512`，
  `PYTHONPATH` 明确指向当前 worktree。业务库、配置、画像、应用与 profile 均为本轮新建。
  画像为合成轻小说兴趣，来源、候选、推荐和事件的起始计数均为 0。
- 未设置任何 `OPENBILICLAW_EGO_*`，没有复制应用、浏览器 profile、来源流程或成功标记。
  用户授权的真实 `gpt-6-luna / medium` 经官方 app-server 协议桥 `18513` 调用，
  只转换产品请求与工具协议，不生成固定响应、计划或评分。
  embedding 保持此前验收配置的 Ollama `nomic-embed-text`。
- 正式下载器从官方 CDN 下载 164216514 字节，SHA-256 为
  `4a91f88ddcbb5bf76ed77682b2059f02a96b24c8e3fbef95434c794692656a91`。
  `baseline.json` 证明开始时没有 installer 缓存，`download-evidence.json` 保存下载结果。
- 普通网页与产品 UI 均使用本轮托管 Ego 0.6.0.16；没有 Playwright 浏览器执行或替换。
  公开网站只读作品/文章元数据，不读取小说章节、不登录、不向网站写入。
  所有审批、配置和内容写入只作用于隔离实例。

## 部署失败、诊断与恢复

首次实际服务启动无需聊天或 prepare API 即进入下载，约 32.73 秒健康接口返回 200。
复制与代码签名检查完成后，系统信任检查达到既有预算，约 391.11 秒观测到
`phase=verify / ego_setup_timeout`。失败没有被覆盖；此前已验证的独立应用再次通过
`spctl`，但也耗时约 80.49 秒。这支持“系统信任校验很慢”的判断，不证明唯一根因。

聊天首次添加哔哩轻小说时，模型真实调用 prepare，返回安装中；没有提前建成来源。
后续 Django Feed 探测失败，`auto` 流程进入网页准备并重试安装：这轮通过系统信任检查，
但首次启动的监听检查超时。阶段与调用路径把该超时缩小到 `_launch()` 的 OS 探测。
独立对运行中服务的三次 `lsof` 探测为 0.28–0.30 秒，未稳定重现负载下的瞬态超时。

修复监听检查后，正常服务重启自动继续准备；首次设置的只读页面读取仍超时。
补齐只读 snapshot 的有界重试后，下一次真实服务启动日志捕获连续 snapshot 超时、
随后依次进入官方步骤 0–8，关闭临时调试端口并正常重启，通过原生握手。
`snapshot-restart-ready.json` 记录重启后约 148.23 秒 `ready / available=true`，
`debug-ports-after-ready.json` 记录旧调试端口已连接拒绝。

两项修复均没有增加原有外层预算：监听探测在已有有限循环内重试，只有验证本机
监听后才能继续；snapshot 是只读请求，仍受首次设置总预算约束。
点击操作响应丢失时报告 `ego_setup_action_timeout`，不重放动作。日志只记录步骤号
或命令名/预算，不记录设置字段或浏览器数据。

## 真实业务证据

- **RSS/Atom**：Django URL 的正式公开探测返回 safety-policy `no_match`，保留失败；
  不把后续 Ego 重试误记为 Feed 成功。阮一峰 Atom 在同一真实聊天中准备、试采成功，
  经正式审批 API 启用独立来源，份额 1、5 分钟。自然首轮 3 发现/3 入队，下一轮
  `not_modified`、0 入队；真实模型评估三条均因与轻小说画像相关度低而拒绝，未伪造入池。
- **Ego 草稿恢复**：模型通过 `list_sources` 自行找回同一哔哩轻小说草稿并调用 trial，
  取得 5 条真实样例、提交启用审批。首个规则把卡片简介/作者/标签混入书名，
  因此在真实 Ego 产品 UI 中拒绝该审批，并通过同一聊天反馈字段问题。
  规则由真实模型重新生成，未手工修改选择器。

- **模型重试与审批**：首次字段修正产生的流程将 `max_items` 放入 step，被 schema
  以 `step has unknown fields or is not an object` 拒绝，没有发布。再次自然语言反馈后，
  模型生成干净的书名与独立简介，未可靠提取的作者为空；真实 Ego UI 批准
  `ap_9ccc829e9321447c`，建成来源 `custom_f074e7b7cca149389a9cf5a564815793`。
- **计划固化**：workflow `e2fb919b-aadc-4c3e-b33c-aa237cf52180`，version 1，
  executor `ego`。试采至发布的计划 SHA-256 始终为
  `c6489001a0969f167afbc619e71544e9e6a251c7518334b947f19cf01b09445f`。
  `published-plan-integrity.json` 保存一致性；没有手工改计划或注入样例。
- **定时采集与入池**：自然首轮 run `19f82190-edcf-41b2-a510-497c3e822781`，
  10 发现 / 10 入队；真实 Luna 评估后 8 cached、2 rejected_low_score，8 条均已生成
  推荐文案。自然约五分钟后 run `c5385380-3c2e-49fe-8dc8-b241d3f6ee5d` 为
  10 发现 / 0 入队，seen 仍为 10；没有调整时钟或强制调用采集。
- **设置**：真实 Ego UI 在“平台源”下显示两个独立来源。哔哩轻小说占比由 1 改 2，
  点击“保存来源设置”后正式 API 读回为 2，截图 `ui/settings-share2.png` 已人工查看。
  页面显示来源类型“网页（Ego Lite）”、五分钟间隔及正常运行时间。随后通过同一页面
  恢复为 1 份，`settings-restored.json` 确认生效。部分 Ego UI 操作遇到瞬态 DOM 解析超时
  或刷新后 stale ref，保留各轮错误，再观察当前页面、使用已观测控件完成；不计作产品保存失败。
- **首页展示**：真实 Ego 页面 `ui/round-21.txt` 显示 8 张推荐卡，每张都有作品名、
  真实链接、“哔哩轻小说”来源名及模型文案。`ui/round-23.txt` 提取 8 个对应链接，
  `ui/recommendations.png` 已实际查看。页面读取后库存变为 shown / 可换 0，
  是展示消费，不是推荐丢失；此前 fresh 8 的时间序列保留。

当前模型计划仍有一项明确限制：对话要求每轮最多 10 条，但生成的 `budgets.max_items`
为默认 20（max_pages 3）。本次首页实际只有 10 条，不能证明自然语言上限被严格固化；
`published-plan-integrity.json` 的 `requested_limit_enforced=false` 保留此项失败。
本轮未修复该语义问题，也不将干净元数据或正常去重当成该项通过。

观察脚本最初错误地把 HTTP Feed 也纳入“所有 workflow 必须 ego”的断言，导致原
`observations.jsonl` 中 `ego_identity_violation=true`。原日志保留；
`business-final-evidence.json` 按具体来源分别验证网页为 ego、Feed 为 http，两者均正确。

## 重启、恢复与收尾

实际设置页恢复占比后，通过正式 PATCH 暂停两个测试来源，正常 SIGTERM 关闭服务，
再关闭该数据目录拥有的 Ego 宿主。CLI 重新启动，约 14.96 秒后健康接口为 200，
两个来源仍在，Ego 为 `ready / installed=true / available=false / offline`。
`setup.json` 的字节 SHA 和 mtime 均未改变，证明该次服务启动跳过准备且未打开浏览器。
`offline-restart-verification.json` 中来源、workflow、版本、运行、调度、seen、
候选身份/状态和推荐缓存九组读库结果全部相同。

随后通过正式 PATCH 重新启用网页来源，等待真实定时到期；产品自行启动新宿主
PID 30073（旧宿主为 28048），原生 run `c8eb7e57-a4e7-4884-b8a6-649256ec4673`
成功发现 10 条、0 新入队，没有重新生成流程。`after-restart-native-run.json` 保存
该次记录及 `debug_port=0` 的新宿主状态。Feed 保持停用，没有把它的结果当作浏览器恢复。

UI TaskSpace 2 已按技能要求完成且未保留页面。最后通过产品接口停用网页来源，
正常关闭本次 API、模型桥、观察进程和专用 Ego；保留应用、profile、数据库及证据。
未停止生产服务或其他用户浏览器。源码与临时实例隔离，不把测试来源添加到生产配置。

| 本轮门槛 | 结果 | 证据边界 |
| --- | --- | --- |
| 空缓存官方下载 | PASS | 真实 164216514 字节，没有借用上一轮 installer |
| 第一次部署即 ready | FAIL | 系统信任检查超时，原始记录保留 |
| 修后正常重启自动完成首次设置和握手 | PASS | 无手工设置、无放宽校验；属于失败后恢复 |
| 真实聊天 → 试采 → 人工审批 → 独立 Ego 来源 | PASS | 经字段反馈及模型重试后完成，非一次零干预成功 |
| 自然定时发现 → 真实评估 → 入池 → 首页 | PASS | 10 发现、8 入池和 8 个来源名推荐卡 |
| 自然周期去重 | PASS | 第二轮 10 发现、0 入队，seen 10 |
| Atom 采集与去重 | PASS | 首轮 3，后续 not_modified；本画像下三条均低分拒绝 |
| 配置页独立占比保存与恢复 | PASS | 真实页面 1 → 2 → 1，正式 API 读回 |
| 服务重启持久化、已准备离线不重装 | PASS | 九组持久数据及 setup 内容/mtime 相同 |
| 后续定时任务按需重启 Ego | PASS | 新原生宿主，10 发现、0 入队 |
| 自然语言“最多 10 条”严格固化 | FAIL | 模型计划 max_items 20，尚未修复 |
| 最终源码单次全新安装 / 冻结 DMG 安装 | NOT_RUN | 不把恢复成功或 wheel 构建提升为这两项通过 |
| 登录、验证码、Windows / Linux 原生 Ego | NOT_RUN / UNSUPPORTED | 仅 macOS Intel 匿名网站与 Atom |

截至业务与恢复验证完成，`model-audit-summary.json` 记录 53 次真实请求，均为
`gpt-6-luna / openai`，没有模型 mock。该数含聊天、规划、评估及其他产品后台任务，
不作为独立评分次数。原始协议记录保留本轮目录内，不提交请求正文到仓库。

## 自动化与构建边界

监听检查回归先有 3 项失败；只读 snapshot 故障用例先有 1 failed / 1 passed。
修后相关四文件组合为 80 passed / 24 warnings；补充丢失点击回执的负例后，
原生设置控制三项定向回归均通过，确认动作只执行一次。这些范围重叠，不能相加。
日志位于本轮根目录的 `listener-red.log`、`onboarding-read-red.log`、
`onboarding-read-green.log`、`onboarding-final-controls.log`。

全局 Ruff 通过，MyPy 352 个源文件通过，日志分别为 `ruff-final.log`、`mypy-final.log`。
离线 wheel 构建成功，SHA-256
`91d52d5da97df2b9cda1e778ed8ce106574fcf599050e2a1e0d50dde400ab2a2`；
两项修改模块、Ego executor 和 CLI 四个 wheel 成员与工作树逐字节一致，
记录 `wheel-verification.json`。没有以 wheel 构建替代桌面打包安装。

本轮为 macOS Intel 真实源码部署；不宣称冻结桌面安装包、Apple Silicon、Windows、
Linux/Docker、外站账号登录或任意网站已验收。未合入 main、推送或发布。
