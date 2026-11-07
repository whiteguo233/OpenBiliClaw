# 来源持续故障与现有异常事件流（2026-11-06）

## 范围与分支

Discovery-only 增量：动态网页和 HTTP Feed 的持续采集故障接入现有异常展示，不新增平台账号能力。
后端 `feat/source-failure-alerts-20261106`，基线 `c1aa7e949b9f5e2b629f9c952ce6876ef104c1ed`；
原生 `feat/native-source-failure-alerts-20261106`，基线 `379cfcc92aea0ae7fe6b8176f096d19d057b2dc0`。
独立 worktree 携带上一轮来源提醒／验证授权的已核对改动；主工作区与上一轮 worktree 不变。
未合并、推送或发布。前轮完整聊天／Ego／定时入池验收继续以
[来源提醒 E2E](2026-11-06-source-attention-e2e.md) 为准。

私有证据目录：`/var/folders/qt/cvqn590d6sxgwt2mv4yf7hzr0000gn/T/obc-source-failure-alerts-20261106-q18r351r`。
原始日志、截图和故障控制脚本仅保存在本机。

## 行为和数据流

- 第 1–2 次普通失败在来源卡显示安全原因；连续第 3 次进入当前提醒和异常历史，继续原退避。
- 登录／安全验证立即提醒，保留原暂停门控。强制刷新不绕过暂停或退避。
- 固定原因覆盖超时、网络、限流、解析、执行环境、访问权限、地址不存在和候选交接失败。
- 成功采集（包括合法 empty/not_modified）或成功试采后发布恢复版本清除该任务故障并写恢复事件。
  同一来源多任务仍分别结算；当前提醒合并，某个任务恢复不清除其他任务的问题。
- `WorkflowStore` 的带租约事务写入 `custom_source_alert_events`，唯一 cause 去重并保留最近 500 条。
  同一普通故障期间不重复写历史。过期 worker 不能追加误报或伪恢复。
- API 每 2 秒读取已提交事件并转发现有 `diagnostics.alert`。启动幂等、关闭取消；REST 读取历史，
  不在重启时重放历史推送。原 LLM／Embedding 内存缓冲及其重启清空行为不变。
- Web／扩展沿用「设置 → 日志 → 异常报警」；Web 消息可定位对应来源。
  原生 App 新增「设置 → 异常记录」及来源页历史入口；前台历史 15 秒、提醒 30 秒同步。
  后台暂停轮询，历史页打开来源时也暂停；断线或 DB 暂时不可用保留已显示记录。
- 普通故障点击“查看来源”，登录／验证仍走人工接管。读取提醒／历史不启动 Ego。
  无新增系统推送、邮件、配置、CLI、依赖或安装步骤。

## 回归发现与修复

独立子进程写入故障的测试暴露 FastAPI router 生命周期重复调用可启动两个转发任务，
导致同一事件推送两次；已加入锁和现存 task 检查。修复后独立 worker → API 转发专项通过。
原生前后台测试初次失败来自 MockClient 回调中使用异步测试的 expect 与 pump 冲突；
保留日志，改用同步 expectSync 后，暂停无请求、恢复立即刷新与历史保留均通过。
旧诊断结构测试仍匹配旧空态文案和未合并历史的 snapshot 调用；按新契约更新后单独复验。

一次新增 Python 测试命令漏设 `PYTHONPATH=src`，导入了主工作区的 editable 安装而收集失败；
原日志保留，后续所有 Python 命令显式从当前 worktree 导入。

## 原生安装和真实请求验证

使用 iPhone 17 Pro / iOS 26.5 模拟器，在新 worktree 重新构建原生包。
独立 API 18616、独立控制进程 18617、新数据目录，Django 官方 RSS 先真实准备／试采后启用。
故障由私有脚本经正式 WorkflowStore claim/complete 注入三次 timeout，且测试专用重置到期时间；
恢复从同一正式 HTTP Feed 执行路径真实请求 Django RSS，提交实际结果。
原生连接真实 API；不伪造 API 响应，不直接插入提醒行，不使用 Playwright 替代网页执行器。

这是故障展示／恢复验收，不是自然网站故障、自然定时周期、聊天模型或候选评估入池验收。
测试初始空模型配置使业务 API 进入降级模式并返回 503；随后仅在私有配置中选择本机已有
`qwen2.5:0.5b`，正常启动实际 runtime。本场景不调用聊天模型或 embedding，不声称验证模型可用性。

最终重新构建后的整轮原生验收 **58 秒通过**（native-failure-live-verified.log，含最终异常检查）：
铃铛 → 对应来源故障卡 → 来源异常历史 → 真实 RSS 恢复 → 保留失败和恢复两类记录 →
当前提醒消失 → 关闭消息 → 设置页独立异常入口。是安装到 iOS 模拟器的原生 App，
不等同于物理手机、Android 或本轮聊天建源验收。

live-stream-verified.log 收到 source:5/source_timeout 与 source:6/source_recovered 各一次；
全部三次故障过程共 6 条持久转移，也在跨进程 WebSocket 中各收到一次。
最终恢复实际发现 3 条、因此前真实入队而新增 0 条，状态 ok；份额仍为 2。

四张截图已提取，来源故障卡与恢复历史已人工核看；有正常可读的来源名、原因和操作入口。
关键原图 SHA-256：

- source-failure-settings-verified.png：59c0aa6ae514f09ea45db76fdffde8db9bc23397cb500746527ce5e715fe7e32。
- source-recovered-history-verified.png：168f49edb9c6075d79222e9fe1888e4e506dd002494f7d22f9de2c7d9297d22a。

首轮原生恢复未通过：测试脚本的 enqueue 回调返回 0 且没有候选落库，真实 RSS 虽取到 3 条，
正式完成逻辑正确返回 degraded/candidate_queue_full，继续保留提醒，未伪报恢复。
原始结果在 live-recover-queue-rejected.json。脚本改为真实候选入队后先成功入队 3 条，
后续同文重采为 3 条发现／0 新增，状态 ok，按既有候选和 workflow seen 去重；未评估为入池。
第二轮已经验证失败／恢复历史和当前提醒消失，但测试错误地对消息 bottom sheet 使用 pageBack，
因此整体仍记失败。改为点击已有“关闭”按钮，同时等待来源卡实际加载和页面转场完成后截图。
native-failure-live.log 与 native-failure-live-final.log 均保留，不将中途成功当作整轮通过。

实际停止并重启隔离 API 后，history-before-restart.json 与 history-after-restart.json 的
4 条 alert 完全一致，attention 仍为空，证明恢复后的历史保留和当前提醒语义独立。

## 常规验证

- 来源、租约、入队、API 跨进程提醒新增／关联专项 24 项通过（backend-added-rerun.log）。
- 扩大相关 Python 回归为 204 通过、1 个旧结构断言失败；该断言更新后，所在文件 4 项通过。
  原始 backend-final.log 和 desktop-diagnostics-final.log 均保留。不是重跑全仓 1 万余项。
- Web JS 174 项通过；扩展诊断 3 项通过。
- 原生全部单元／Widget 测试 178 项通过；flutter analyze 无问题。
- Ruff 全仓通过；MyPy 354 个源文件通过；两个 worktree 的 diff whitespace 检查通过。

## 收尾

测试来源已通过正式 API 停用，保留运行、候选和 6 条历史；当前提醒为空。
隔离 API／故障控制进程已关闭，模拟器恢复到原先关机状态。未启动新的 Ego 宿主，
没有操作前轮交给用户的 Ego 验证窗口。原哔哩轻小说精确路由文件 SHA-256 仍为
d99301d70eccef0d4556d0af4b33277d5b36107c9082ef3118c2013a4a0e2c92。
主工作区只有开始前已有的 outputs/promo/tmp 未跟踪目录；未将本轮改动合入 main 或发布安装包。
