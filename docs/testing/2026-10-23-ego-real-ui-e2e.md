# Ego Lite 真实页面建源与正式 Discovery 复验

本轮从实际桌面 Web 输入框发送自然语言，补齐此前主要通过聊天 API 发起的证据。
范围为已有动态来源能力的 `capability-increment`：真实验收及列表刷新保护修复。
基线 `e1ed0ad661ad7985103bbb647324189bdd5e243e`，分支
`feat/dynamic-discovery-sources`，复用契约 `docs/platform-source-contract.dynamic-web.toml`。

## 环境与产物

- 隔离目录 `/tmp/obc-ego-ui-e2e-20261023-r2`，后端 `127.0.0.1:18612`，
  模型协议桥 `18613`。所有业务表初始为空，画像为合成轻小说偏好。
- 真实 CLI `/Users/white/workspace/OpenBiliClaw/.venv/bin/openbiliclaw serve-api`，
  实际 import 为隔离目录 `runtime/openbiliclaw/__init__.py`，来自解包 wheel。
  后端 PID 96065，所有本轮操作期间未重启。无 `OPENBILICLAW_EGO_*` 覆盖。
- 初始 wheel SHA-256 `e6700cc99d0cabf04d0ede80268f8843f22e6106f92e43354ba1257685c23a42`；
  386 个 Python/JS/CSS/HTML 文件与工作分支逐字节一致。
- 全新托管应用和 profile；仅复用此前官方下载且重新校验的安装包缓存。
  安装包 SHA-256 `4a91f88ddcbb5bf76ed77682b2059f02a96b24c8e3fbef95434c794692656a91`。
  HTTP 首次就绪 17.08 秒，自动安装、验证、首次设置与原生握手共 175.28 秒。
  本轮不是重新联网冷下载；此前完整冷下载证据见[安装修复复验](2026-10-23-ego-reliability-fixes.md)。
- 页面操作、网页观察与采集均使用 Ego Lite。未使用 Playwright。
  状态 API 会列举机器上的兼容执行器，但实际网页 workflow 始终为 `ego`，Feed 为 `http`。
- 按用户既有授权，经官方 Codex app-server 调用真实 `gpt-6-luna / medium`。
  协议桥不预设工具参数、采集计划、评分或内容；24 次已完成模型请求均为真实 Luna 成功响应。
  embedding 为本机 Ollama `nomic-embed-text`，正式评估执行 embedding prefilter。

## 聊天、审批、采集与入池

两次请求均由 Ego 操作页面输入框和发送按钮，审批也在页面「待审批」弹窗完成。
没有直接创建来源、注入候选、修改数据库或强制通过评分。

| 来源 | 聊天要求 | 试采 | 首轮正式发现 / 入队 | 真实评估与展示 |
| --- | --- | --- | --- | --- |
| 哔哩轻小说 | 首页最新更新，最多 3 部、1 页、不翻页，标题/作品链接/简介，不读章节，无需登录，每 5 分钟、1 份 | 3 | 3 / 3，Ego | 2 cached 并生成文案，1 rejected_low_score；首页显示两张带独立来源名的作品卡 |
| 阮一峰周刊 | Atom 最新文章标题/链接/摘要，最多 2 篇、不翻页，每 5 分钟、1 份 | 2 | 2 / 2，HTTP | 2 rejected_low_score；正常过滤，不冒充推荐入池 |

哔哩轻小说来源 `custom_a9c41359d0d7479091c08a88ae517c9b`，workflow
`dd0ce181-fb01-4595-bf88-f6e8e13a9670`，审批 `ap_e5b803f6fcc24fca`。
试采与发布计划 SHA-256 同为
`cfcf04353509aa3b87dc036f45cd29a9cec3e8801c68897863395d1f9fe5168e`，预算持久化为 3/1。
《亚妖精物语》0.63、《黄金神威 鹤见笃四郎的宿愿》0.65 进入推荐缓存；
《作者不详：推理作家的读本》0.58 被过滤。首页实际链接分别指向作品 5484、5482，
带「哔哩轻小说」标签和真实生成文案。已展示状态为 shown，可换库存为 0 不表示未曾入池。

RSS 来源 `custom_9b2af66e9ede4ed5baf7b079e1d1e519`，workflow
`17f0274b-af48-447f-afb2-09401de55cd5`，审批 `ap_3c91bf84b2c74c1a`。
试采与发布计划 SHA-256 同为
`177c0d7a6b19ecf1ba4bc903a1a83d0c9a4f376e8f584bdb79128543747f001b`。
真实读取科技爱好者周刊 416、415 期；订阅及其内容链接范围在审批中分别显示。

## 周期运行的真实失败与恢复

首轮 Ego 正式运行 `9246d8cb-cf39-409e-a3af-04a068de1954` 成功。
约五分钟后的 `1d893e1d-33c8-478f-9148-d957572c2d61` 失败，错误为
`ego_connection_timeout`，0 发现 / 0 入队；该次失败不能计作去重成功。
下一次自然调度 `c12b7221-192c-45cc-869a-b6c892a1288e` 自动恢复为成功，
3 发现 / 0 入队；seen 保持 3，缓存保持 2。没有重启或手工恢复宿主、后端及工作流。
RSS 后续运行返回 `not_modified`、0 入队，seen 保持 2。
再下一轮 Ego `44114670-50d8-4d1f-bee7-a93781d83a34` 也成功，3 发现 / 0 入队。
本轮累计 Ego 4 次正式运行（3 成功、1 连接超时），RSS 3 次（1 成功、2 not_modified）。

Ego UI SDK 也间歇出现 `Accessibility.getFullAXTree`、`Page.getFrameTree`、
`Target.getTargets` 超时；重读页面后继续，未盲目重复已经保存的操作。
原生超时根因尚未定位，本轮没有改执行器，也不宣称连续运行零失败。

## 设置页发现的问题及修复

首次页面保存将哔哩轻小说 1 份改为 2 份，API 与数据库确认写入，scheduler 也报告
该来源目标 200、RSS 100。然而页面随后显示 0 张来源卡、Ego 状态未知、份额总览丢失。
真实失败断言 `ui/round-19.mjs` 返回 `cards=0, egoReady=false`，退出 1；
截图 `ui/settings-share-two.png` 保留现场。来源没有从数据库删除。

最初错误响应的原始 body 未被截获，不能断言其上游原因。追加只读响应观测后，
后续正常 GET 明确返回两条来源，页面恢复；正常保存 2→1 成功。
确定性回归复现了组件的错误处理缺口：请求 helper 解码失败可能返回 null，
组件将 null 或缺字段 envelope 当成成功空列表，清空来源与 Ego 状态。
新增测试在修复前失败（预期保留 3 个字段，实际 0 个）。

修复为原子验证列表 envelope：`items` 和 `executors` 必须均为数组，之后才替换
来源、份额总览和执行器状态；不完整响应保留当前卡片、编辑和状态，并提示刷新失败。
成功写入与后续刷新失败分别反馈；未自动重放写入。测试覆盖 null、HTML、空对象、
非法 items、缺 executors，以及恢复读取。

修复后重新构建 wheel，SHA-256
`cd0b366a21f9c0db2099642915dcb05afb914de06b9f83ff4624ad081f44ed48`。
352 个 Python 文件逐字节未变，部署该 wheel 的共享 JS 到本轮解包目录并刷新页面，
后端继续原 PID。HTTP 返回及页面已加载函数确认修复资产；共享 JS SHA-256
`fc6bf596bb3892082205e96b8d849f0fed3ba781b184ad70aecb64a93f4664df`。

修复版真实页面再次保存 1→2：两张卡保留，Ego ready，哔哩轻小说 2 份 / 50%，
RSS 1 份 / 25%，旧版空 recipe 配置 1 份 / 25%；API 回读一致。
再保存 2→1，卡片与数据仍正确。因 Ego 高层填充调用超时，最后两次保存使用
Ego `page.evaluate` 设置实际输入并点击实际保存按钮，仍执行页面生产事件处理器；
没有直接 PATCH 代替这两次 UI 保存。测试脚本记录此差异，不冒充全部为指针操作。

## 验收与边界

| 检查 | 适用性 | 结果 | 本机证据 |
| --- | --- | --- | --- |
| 包、配置、数据库、全新应用/profile、缓存来源 | required | PASS | baseline.json、business-baseline.json、wheel-source-comparison.json |
| 同次自动安装到 ready、无人工后端重启 | required | PASS | fresh-launch.json、fresh-ready.json、fresh-observations.jsonl |
| 页面自然语言建源与两次页面审批 | required | PASS | ui/round-03、06、09、14；pipeline-proof.json |
| 网站与 Feed 正式采集、真实评估及来源归属 | required | PASS | pipeline-proof.json、observations.jsonl、model-proof.json |
| 周期去重与失败后无需人工恢复 | required | PASS | 三次 Ego 运行与 RSS not_modified |
| 设置比例保存、独立总览、回读 | required | PASS | ui/round-27、28；fixed-share-*-readback.json |
| 页面暂停来源、持久状态及测试清理 | required | PASS | ui/round-30、31；paused-sources.json |
| 列表不完整响应不清空现有状态 | required | PASS | ui-refresh-red.log → ui-refresh-green.log |
| 本轮连续运行无瞬态失败 | required | FAIL | Ego 原生连接超时及 UI SDK 超时仍有真实记录 |
| 冻结桌面 DMG、Windows/Linux 原生 Ego、真实账号登录、插件安装 | N/A | NOT_RUN | 本轮公开站点、桌面 Web 和 wheel 范围，无该类交付或上游写入 |

结论为 **incremental only**：聊天到正式 Discovery 和推荐展示主链已真实跑通，
设置列表的错误处理缺口已修复；原生连接间歇超时仍需进一步定位，不以自动恢复
冒充完全没有故障。代码修改不扩大到其它来源或浏览器安装平台。

验证：`node --test tests/js/*.test.mjs` 163 通过；相关 Python 来源生命周期、
运行历史、runtime 共 51 通过；Ruff 与 `git diff --check` 通过，wheel 构建成功。
本次未重跑全量 Python 套件或 MyPy，未改 Python/接口/配置定义；此前全量 pytest
的既有失败记录仍见[上一轮](2026-10-23-ego-reliability-fixes.md)，不宣称全量通过。
文档同步 desktop-web、web、extension 模块及索引/变更日志；无架构、CLI、配置或
安装流程变化，因此不改对应图和安装文档。未合并 main、未部署生产或发布。

收尾经真实设置页分别停用两条测试来源，API 回读均为 enabled=false，卡片仍保留。
UI task space 1 已且仅调用一次 `finish({keep:[]})` 成功。证据保留在隔离目录；
测试服务、模型桥、观察器与专用 Ego 宿主在收尾停止，不更改生产配置或日常浏览器。
