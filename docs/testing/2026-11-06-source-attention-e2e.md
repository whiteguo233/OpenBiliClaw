# 来源登录／验证提醒与替代网站真实验收（2026-11-06）

## 范围和环境

后端分支 `fix/source-attention-20261106`，基线 `c1aa7e949b9f5e2b629f9c952ce6876ef104c1ed`；
原生分支 `fix/native-source-attention-20261106`，基线 `379cfcc92aea0ae7fe6b8176f096d19d057b2dc0`。
两者在新建独立 worktree 中继续工作，并保留前轮验证域授权改动；没有合并、推送、发版。

本机私有证据目录 `/tmp/obc-source-attention-20261106-s0t4jjj9`。
新数据库、新 Ego profile；只复用官方安装包缓存，准备耗时 74.6 秒并成功握手。
这轮是缓存安装包的安装／配置验收，不是重新下载验收。来源执行使用真实 Ego，未使用 Playwright 代替。
聊天与评估使用此前授权的官方 GPT-6 Luna CLI 兼容 API 桥；embedding 为真实 Ollama
`nomic-embed-text`。不回放模型响应，不写入候选或评估分数，准入阈值保持 0.6。

隔离测试画像为预设的软件开发兴趣（Rust、Python/Django、开源资讯），不是用户真实画像。
初始脚本漏掉画像保存，导致 Django 激活后的首次等待窗口里后台仍按未初始化暂停；
保留 `django-live.log` 的 `FAIL_NO_SUPPLY` 原始结果。保存正确的画像后，后台自然发现就绪并采集，
没有手动改写到期时间或直接插入候选。后续以正式 run、candidate、cache 证据判定。
日常数据与既有哔哩轻小说精确直连规则未改变；前轮交给用户的 Ego 验证窗口不在本次控制范围。

## 行为变化

- 部分页面成功后遇到验证，即使候选队列容量不足或交接抛错，也保留原验证暂停；两个回归先失败再修复。
- 执行状态继续使用 `login_required`；明确网站安全验证用 `verification_required` 原因区分。
- 带租约完成成功后才记录 discovery 告警；过期结果不能覆盖新版本、不能发误报。
- 来源与份额保留，暂停流程不显示下次到期时间；强制刷新、停用后再启用都不能绕过验证。
- `GET /api/custom-sources/attention` 从数据库当前状态投影提醒，已启用来源按 source_id 合并；
  未发布草稿按 draft_id 展示，同一来源的恢复草稿不增加重复提醒。接口不启动浏览器。
- Web 消息铃铛、原生 App 消息页和来源设置显示原因与处理入口。前台 30 秒同步，打开消息也刷新；
  暂时断线保留已有提醒。原生进入后台停止定时器。
- 打开或退出接管不解除暂停。成功试采、发布恢复后才移除；无需用户重新设置来源份额。
- 这是应用内待处理提醒与诊断日志，没有新增系统推送或邮件服务。

## 真实来源证据

Rust 官方博客 `https://blog.rust-lang.org/`、Django 新闻页
`https://www.djangoproject.com/weblog/` 均在真实 Ego 中返回文章列表，无资源失败。
后续正文按原批准选择器、3 条／1 页预算采集。

首轮 iOS 原生流程实际完成 Rust 聊天建源、审批、份额改为 2、后台采集、截图接管、继续试跑、
返回聊天，再通过聊天新增 Django RSS `https://www.djangoproject.com/rss/weblog/`。
但最终异常检查发现 `SourceAttentionProvider` 在 Home 构建过程中同步通知，故整轮记为失败。
该问题以单元用例复现失败后，改为 microtask 启动首轮刷新；修复后的原生整轮另行复验。
原始记录保存在 `native-live-before-fix.log`，不以中途 PASS 日志覆盖最终失败。
下一次复跑正确要求澄清：首页不提供摘要，且测试提示“作者没有留空”有歧义。
保留 `native-live-clarification.log` 和该 `needs_details` 草稿；后续提示明确仅要求标题、链接，
作者／摘要／时间均可为空。没有把模型的追问当作已完成建源。

初次验收的有效数据：Rust 网页 3 条、Django 网页 3 条进入评估；Rust 3 条和 Django 2 条
入池，Django 1 条低分拒绝。Django RSS 实际取得 3 条，与网页的文章 URL 相同，正常去重后
新增候选 0 条；不能把它写成新增 3 条候选或入池 3 条。
真实 `GET /api/recommendations?limit=10` 返回 Rust 与 Django 各一张文章卡，
`source_platform=web`、独立 `source_id/source_name` 和 `content_type=article` 正确；
保留 `recommendations-live.json`。这是已生成推荐的消费证据，不是额外两条入池。

## 修复后原生完整复验

在 iPhone 17 Pro / iOS 26.5 模拟器从当前代码重新构建，实际聊天和 Ego 请求整轮
9 分 29 秒通过：创建 Rust 来源、批准、修改份额为 2、后台采集、原生截图接管、
继续真实试采、返回聊天、再通过聊天新增 Django RSS 并实际采集。

Rust 来源 `custom_3b2c49b4b90d4c52a76423a7448e3f02` 的当前 version 2 两轮自然采集
均 3 条；上轮完成到下一轮开始 **315.53 秒**，满足配置的 5 分钟周期。
这些文章已在首轮验收进入数据库，因此再次采集入队 0 条，符合去重。
不是将 version 1 的运行与重新登录后 version 2 的首轮混算。
Django RSS `custom_721888ae0fc24acaaaefa4f08b397a47` 取得 3 条、重复入队 0 条。
正常结束后用例只停用本次创建的两个来源，保留历史；没有操作用户来源。

证据：`native-live-final.log` 的最终 `All tests passed`（包含异常检查），
`native-rust.json` 保存本轮明确允许缺失字段的输入要求。此为安装到 iOS 模拟器的原生 App，
不是移动网页，也不等于物理手机或 Android 验收。

## 真实验证提醒

新的哔哩轻小说草稿 `a08c3243-09b1-4088-a0e8-2d3a9079f39d` 由真实 Ego 观察返回
`needs_login / verification_required / trial_success=false`，待处理接口返回一条对应提醒。
没有批准、启用该草稿，也没有操作网站验证码。真实网站验证尚未通过。
完整停止并重启本轮 API 与后台 workers 后，待处理接口与重启前内容完全相同；
再次尝试启用仍返回 422。证据为 `attention-before-restart.json`、
`attention-after-restart.json`、`verification-activation-blocked.json`。

重新构建原生 App 后，真实提醒专项整轮 56 秒通过：消息铃铛打开待处理卡片，点击
“处理验证”进入 Ego 截图页，退出后原提醒仍存在且 `trial_success=false`。
`native-attention-live.log` 最终为 `All tests passed`；两张原始截图已提取并人工核看，
卡片与处理入口可读，接管页显示实际网站验证画面：

- `source-attention-inbox.png`：SHA-256 `81c72a0a213431d63fc809f219edf433d2121a7ec26189ec25f7296a906b0055`。
- `source-attention-recovery.png`：SHA-256 `dc35720badce0b483c8d377a145551d9725a259a1ef61ba7e0c7abaf2416b7c0`。

已发布任务的账号过期／验证暂停、重复轮询、重启投影、强制刷新门控、恢复成功清除，
由真实存储／producer 生命周期测试和受控上游结果覆盖；这不等于真实账号过期验收。

## 运行收尾

截至停用时，5 个测试来源共完成 15 次正式成功采集，正式运行没有失败；
建源草稿的验证失败和前述测试脚本失败另行保留，不混入正式运行统计。
`sources-final.json` 确认 5 个来源全部停用；历史与未通过验证的草稿保留。
原生退出接管后，21:58:02 日志确认 managed Ego 因空闲自动停止。
随后读取提醒和来源列表仍返回 Ego `ready / offline`，没有因查询提醒重新启动浏览器。
本轮 API 和 Luna 桥已发送正常退出信号，API 日志确认 application shutdown complete。
API、worker 和桥进程均已退出，18612／18613 端口关闭，模拟器恢复到最初的关机状态。
既有哔哩轻小说路由文件 SHA-256 仍为
`d99301d70eccef0d4556d0af4b33277d5b36107c9082ef3118c2013a4a0e2c92`。

## 验证状态

- 最终暂停／入队／版本并发／运行历史／手动刷新相关后端测试：128 项通过；提醒专项包含 7 个案例。
- 全量 Web JS 行为测试：171 项通过，其中本轮相关 41 项覆盖消息保留／恢复、精确接管路由、跨站链接拒绝和来源卡提示。
- 原生全量单元／Widget 测试：175 项通过；真实启动异常回归先失败再通过。
- Ruff 全仓通过；MyPy 检查 353 个源文件通过；原生 analyze 无问题。
- 真实 iOS 原生聊天建源／两个自然周期整轮通过；真实原生验证提醒专项通过。
- 全量 Python 首轮：**11631 passed、6 failed、72 skipped**，耗时 2468.87 秒。
  保留 `pytest-parallel.log`，不能写成全量一次通过。命令为
  `PYTHONPATH=<隔离 test-tools>:src python -m pytest -n 4 --dist=load`；
  xdist/execnet 只装到本轮私有目录，没有修改产品依赖。
  此轮收集后新增的两个部分采集／队列异常用例由最终 128 项相关回归覆盖。

全量失败项复核：

| 首轮失败 | 原始失败点 | 同一份最终代码复核 |
|---|---|---|
| 推荐封面请求有界 | 5 秒内首屏卡片数量仍为 0 | 首次串行仍失败；与未修改基线分别单独运行后均通过，30.07／30.49 秒 |
| 50 路数据库 settlement 竞争 | `Barrier.wait(timeout=5)` 破坏 | 串行通过，未改数据库或断言 |
| 聊天 toast 不遮挡输入框 | 点击按钮的 1 秒等待超时 | 串行通过，未改页面或断言 |
| 迁移 request ID 最终 fallback | 设置按钮稳定性等待超时 | 串行通过，未改迁移代码或断言 |
| 移动网页探测处理中保持 busy | 初始化徽标的 5 秒等待超时 | 串行通过，未改移动网页或断言 |
| refresh durable claims 所有权 | 固定 sleep 20 毫秒后 task 尚未 done | 串行通过，未改调度器或断言 |

完整节点名与结果保留在 `pytest-failed-serial.log`（5 通过、1 失败），
最后一项独立复核的日志为 `pytest-current-cover-debug.log`（1 通过）。
基线从 `c1aa7e949b9f5e2b629f9c952ce6876ef104c1ed` 只读导出到私有目录，
对应 `pytest-baseline-cover.log`（1 通过）。没有放宽超时、移除断言或通过重跑覆盖原始失败。
观察结果符合负载／时序敏感，但没有据此断言所有波动的根因已解决。
这些现有前端回归使用其本地测试驱动与合成接口，不计入真实网站 Ego 验收证据。
