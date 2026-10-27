# 聊天三端逐项真实复验（2026-09-30）

## 结论与边界

按桌面 Web、真实加载的 Chrome 插件 popup、移动 Web 分别操作界面，发现并修复 4 项问题。
三端都完成真实模型简答、笔记保存/更正、网页读取，以及角色/风格、会话草稿、断网恢复、
任务详情/取消和尺寸检查。没有把 API 返回成功代替界面验收。

**本轮不能判为全部通过**：模型后续持续返回 429，桌面和插件的删除审批链未走通；
Exa 搜索偶发或持续 ConnectError；移动/插件浏览器点击官方来源链接时出现
`net::ERR_CONNECTION_CLOSED`。这些均有失败记录，未替换为模拟成功。

移动端是 Chromium 的移动/触摸上下文与不同视口检查，**不是 iOS/Android 真机验收**。
键盘场景通过压缩视口覆盖布局，不代表系统输入法、软键盘或 Safari 已验证。

## 基线与环境

- 独立 worktree：`OpenBiliClaw-chat-comprehensive`，分支 `fix/chat-comprehensive-e2e`。
  本次合入 `main@ec94154b`，集成提交 `6c9b199a`；包含最新 length/reasoning 重试和运行状态修复。
  上轮报告中的 `main@50bb906b` 不再是本轮基线。
- 后端 `127.0.0.1:18439`；主数据的在线 SQLite 备份、memory 和状态文件放到新副本
  `data/surface-e2e-0930`，修改 data_dir 并关闭该副本 scheduler/saved_sync 自动同步。
  后台学习仍会运行；不操作主数据或已有用户任务。
- 保留真实模型路由、并发与 `network.mode=system`：主模型 `deepseek-v4-flash`，
  回退模型 `sensenova-6.8-flash-lite`。使用真实 Exa public MCP 和外网网页请求。
- 桌面 `/web/`，移动 `/m/#/chat`；插件通过 Chrome for Testing 加载当前 worktree 的
  unpacked 扩展，真实 service worker 与本地后端通信，没有把 popup 当普通网页代替。
- 主工作区保持干净，18420/18421 原有服务未停止；修复只在上述分支，尚未合回 main 或发布。

## 本次 4 项修复

| 缺陷 | 实际复现 | 修复与验证 |
|---|---|---|
| 桌面提示条挡住发送 | 长请求后通知停在右下角；点击发送时命中 `.toast-item`，表单没有 submit。鼠标停留又暂停通知消失，阻挡持续存在 | 聊天页通知移到输入区上方；375/768/1440px 三项浏览器回归由失败转通过。真实页显示通知时发送请求成功发出，按钮上沿 917px、通知下沿 904px；该模型请求随后因 429 失败，不能把发出请求等同于回复成功 |
| 通道忙误报配置重载 | 三端同时执行时，前一个回复占用全局 dialogue lease，后一个准入超时却显示“系统正在重载配置” | 按 paused/active 区分提示；忙时显示“对话通道正忙，请稍后查看回复”。保留 30 秒准入及 durable worker 接续，不修改串行写入约束；busy/paused 与恢复测试通过 |
| 插件重连后角色列表空白 | 刷新后很快打开对话，连接尚未就绪，角色/风格读取跳过；恢复在线后历史能加载，角色仍 0 项、风格错显自然朋友；重新切 Tab 才恢复 | 离线转可达且正在聊天时补读角色、风格、会话。真实离线刷新→打开对话→联网，无需切 Tab，选项从 0 恢复为 4，风格恢复简洁直接；回归先红后绿 |
| 移动端改名被后台刷新清空 | 编辑名称时从另一客户端修改同一测试会话风格；下一次历史刷新使编辑框退回旧名称并丢焦点 | 重绘前按会话捕获名称和选区，重绘后只恢复同一会话，改名按钮聚焦新节点。真实跨端请求回测保留名称/焦点；浏览器回归另验证选区、保存值和换会话不串草稿 |

## 三端操作矩阵

每端使用各自新建会话和 `E2E3-<端>` 测试笔记。六种风格均逐项通过界面保存并核对服务端：
自然朋友、简洁直接、温柔倾听、轻松幽默、理性分析、循循善诱；
稳定标识为 natural/concise/warm/playful/analytical/socratic。四个功能角色均实际切换，
最后回到口味伙伴，确认不会改掉简洁风格。

| 功能 | 桌面 Web | 移动 Web | 插件 popup |
|---|---|---|---|
| 9×6，只答数字 | `54`，无工具 | `54`，无工具 | `54`，无工具 |
| 6 种风格保存、4 种角色切换 | 通过 | 通过 | 通过；重连目录问题修复后复验 |
| 保存乌龙茶笔记→更正温水 | 真实工具成功，磁盘核对 | 真实工具成功，磁盘核对 | 真实工具成功，磁盘核对 |
| 搜索 Python TaskGroup | ConnectError | 第一次失败，再搜索成功 | ConnectError |
| 阅读官方文档→一句话附来源 | 成功读取并回复 | 成功读取并回复 | 成功读取并回复 |
| 删除笔记审批 | 修复发送遮挡后请求能发出，但模型 429，未产生审批 | 生成审批，UI 批准后 executed，磁盘笔记消失 | 模型 429，未产生审批 |
| 过程展开、查看工具记录 | 通过 | 通过 | 通过 |
| 点击官方来源外链 | 新页成功加载 | 地址正确，但连接被关闭 | 地址正确，但连接被关闭 |
| 新会话空输入、A/B 各自草稿、清空不复活 | 通过 | 通过 | 通过 |
| 改名、归档、归档后恢复默认会话草稿 | 通过 | 修复后通过 | 归档和草稿通过；popup 当前没有改名入口 |
| 断网输入保留、联网后同一条消息仅提交一次 | 通过 | 通过 | 通过 |
| 刷新恢复历史/真实失败状态 | 刷新前 pending，之后恢复 429 failed | 刷新前已 failed，恢复失败历史 | 刷新前已 failed，恢复失败历史 |
| 任务列表→详情→取消 | cancelled | cancelled | cancelled |
| 输入、长文字与发送命中检查 | 768×720 / 1440×1000 通过 | 320×667 / 390×844 / 390×420 / 768×1024 通过 | 360×650 / 430×750 通过 |

任务测试通过真实 `POST /api/chat/tasks` 创建独立测试任务，模型 runner 真实启动，
随后从对应界面打开详情并点击取消，核对持久状态。**这是 API 准备任务加 UI 取消，
不是三端“自然语言提议→确认→后台完成”的完整通过结论**。未取消副本中既有任务。

网页工具返回的链接为 `https://docs.python.org/3/library/asyncio-task.html#asyncio.TaskGroup`。
桌面和插件在搜索失败后直接读取已知官方链接成功；它们的最终答案成功不代表搜索成功。
移动/插件外链各重试一次仍连接关闭，未通过关闭 TLS 校验或改变代理绕过。

断网测试先让页面实际失联，再输入/点击、检查草稿，联网后发送并刷新；三端历史中同一
测试消息均只有一条。移动和插件该次模型失败过快，刷新前已终态，不能计作“成功在途回复恢复”。
本轮三个会话最终 19 条用户测试回合：13 completed、6 failed、0 pending；失败均因模型 429。

## 延迟样本与仍未解决的问题

下表是从界面发送操作到终态的墙钟样本，含前端与轮询开销。相同场景曾让三端同时发起，
因此包含服务端串行 dialogue lease 排队；它不是单用户基准、P95 或“耗时都在模型里”的证据。

| 请求 | 桌面（秒） | 移动（秒） | 插件（秒） |
|---|---:|---:|---:|
| 简短算术 | 7.078 | 9.625 | 2.308 |
| 保存笔记 | 26.956 | 30.652 | 36.688 |
| 更正笔记 | 20.293 | 12.972 | 74.344 |
| 搜索/阅读/回复整轮 | 122.734 | 40.590 | 39.308 |

- 算术三端都只答数字，没有额外追问或工具，但多端并发下仍不保证秒回。
- 最后再次从桌面申请删除，22.111 秒后仍为 429；不继续反复调用同一额度受限服务。
  本次没有修复上游额度/限流，桌面和插件两条测试笔记仍保留在隔离副本中。
- 保持相同 `system` 出站策略，独立调用真实 search_web 两次，分别 0.428/0.154 秒
  ConnectError。没有足够证据证明改模型、加并发或盲目重试能解决该网络问题。
- 本轮确认的是**全局 dialogue lease**占用与上游限流都可能带来等待。上一轮采样的
  LLM provider slot 等待 0 秒不能排除进入该 slot 之前的 dialogue lease 排队。
- 上轮记录的后台学习上下文膨胀不在本次修复范围内；前台回复成功仍不等于后台学习完成。

后续优先级：先将“等待前一条回复 / 等待模型 / 工具已完成”分别呈现，保留真实 durable 状态；
给失败任务提供沿用原参数的一键重试；诊断 Exa/外链的实际网络链路；再设计有明确取消语义的
“停止生成”和有界学习上下文。不能为了速度直接放开带写副作用的全局执行锁。

## 自动化验证

以下为相关集合，数量存在重叠，不是全仓 pytest 通过声明。

- 合入最新 main 后，模型/运行状态/聊天相关集合：540 passed。
- 最终 Python：148 passed，覆盖 chat stream/sessions、dialogue lease、approvals、tasks、
  memory notes、移动静态合同及移动改名/桌面布局真实浏览器测试。
- `node --test tests/js/*chat*.test.mjs`：92 passed。
- 扩展 `npm test`：1,518 passed；`npm run typecheck`、`npm run build:bundle` 与资源预检通过。
- `mypy src/`：300 文件通过；`ruff check src/ tests/` 通过；修改的 Python 文件格式通过。
- 新回归均有 red→green：toast 3 项、busy lease 1 项、popup 重连目录 1 项、mobile 改名 1 项。
- FastAPI on_event 弃用警告仍存在，未把警告当作功能失败。

最终 Python 命令：

```bash
PYTHONPATH=src python -m pytest \
  tests/test_chat_agent_stream_api.py tests/test_chat_sessions.py \
  tests/test_dialogue_reply_scheduler.py tests/test_agent_approvals.py \
  tests/test_agent_tasks.py tests/test_agent_memory_notes.py \
  tests/test_mobile_web_agent_chat.py tests/test_mobile_chat_session_controls_e2e.py \
  tests/test_desktop_dialogue_layout_e2e.py -q
```

浏览器复现入口：`playwright_cli.sh -s=surface-{desktop,mobile,popup}`，在实际页面执行
输入、点击、断网/联网、刷新和尺寸变化；真实请求没有网络响应替身。新自动化回归中的
DOM fixture/VM 单元桩只用于稳定复现控件行为，与上面的真实服务验收分开记录。

## 本机证据与清理

测试会话：

- desktop：`chat-3aa03edc3d0e4bf0bdb1720f64fe1691`
- mobile：`chat-7511cc8a4bcf4a1395c5d8d83364e590`
- popup：`chat-d9602f6a005c4adf82c171db6892bfd7`
- 移动删除审批：`ap_23404b68975c4ff6`，executed。
- 三端取消任务：`agent-task-3f36eba767d94f02a420c565a7426043`、
  `agent-task-6e0d36aa6f044c5b9b0148290aa7b745`、`agent-task-4d23d5407c3e4888a334da682f3d2c4c`。

本机 `/tmp/chat-surfaces-*` 保存逐项 JSON、工具输出、red/green 和请求日志；
`output/playwright/surfaces-*` 保存三端实际页面截图。包含副本画像的截图与运行日志
留在本机忽略目录，不提交用户数据。测试副本、配置不纳入 git。

测试结束关闭本任务的三个浏览器会话和 18439 测试服务，移除临时 node_modules 链接。
原有主工作区与服务保留。本次没有新增接口、配置项、模块边界或数据流，架构图/安装流程不变。
