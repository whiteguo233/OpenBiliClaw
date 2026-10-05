# 聊天真实请求复验（2026-09-28）

## 环境与范围

- 代码：`feat/chat-web-memory`，功能提交 `37605537`。这些功能尚未合入 main，
  本次结果不代表 main 或已安装版本的验收。
- macOS 本机真实 FastAPI（18439）、真实模型服务、真实 Exa MCP / IANA 网络请求，
  沿用前轮隔离配置和数据副本；用户运行中的 18420/18421 服务和主工作区不变。
- Playwright 控制真实 Chromium 与加载 unpacked 扩展的 Chrome for Testing。
  移动 Web 检查 390×844、320×740 视口；属于移动布局模拟，不是 iPhone/Android 真机验收。
- 搜索沿用副本的显式 direct 网络策略；没有 mock 模型、工具结果或审批执行。

## 19 次真实模型请求

| 场景 | 次数 | 结果 / 耗时 |
|---|---:|---|
| 简单算术 | 1 | 回复 5，无工具；1.809 秒 |
| 笔记保存、跨会话回忆、更正、再回忆、提交删除 | 5 | 全部正确；3.772 / 1.003 / 3.948 / 1.500 / 4.281 秒 |
| 六种聊天风格各发一次 | 6 | 均 completed，turn 冻结的 persona 与会话一致；无工具；1.237–2.119 秒 |
| 桌面 UI 网页搜索 | 1 | search_web 成功，两个官方来源；创建至完成约 6 秒 |
| 手机布局 UI 读 IANA 网页 | 1 | read_webpage 成功、正文与来源完整；约 4 秒 |
| 插件 UI 删除前/后查笔记 | 2 | 先“温水”，后“不存在”；约 4 / 13 秒 |
| 延迟补测：算术、查已删笔记、问候 | 3 | 1.264 / 2.556 / 1.241 秒；仅查笔记调用一次工具 |

API 脚本的耗时为本地计时；浏览器场景的近似秒数来自 durable turn 的创建/完成时间。
一次插件查询约 13 秒，随后同类读取未复现；尚不能确认原因或承诺稳定延迟。
风格验证覆盖配置注入与简短回复，不声称简单题能充分区分六种人格表现。

## 浏览器与存储核对

- 桌面通过 UI 新建会话、选择“简洁直接”并发送搜索；刷新后风格和回复保留。
  两个来源是有效 anchor，实际点击打开 Python 官方文档成功。
- 移动网页读取的来源可点击，320/390px 无横向溢出。
- 从手机点击测试笔记的“批准执行”；审批从 executing 进入 executed，展开过程卡
  显示“已批准并执行”，磁盘中的目标笔记确已删除。只批准本轮测试审批
  `ap_40166a00afaa42d3`，不处理其他记录。
- 插件再次调用 read_memory 返回完整空列表，回复“不存在”；刷新后回放一致，
  430px 视口无横向溢出。所有观察到的工具结果均 ok=true、未被循环截断。

## 仍存在的限制

- 后台画像学习仍出现 provider fallback 全部失败、HTTP 400 `inference request is invalid`。
  与前轮已知问题一致，本轮没有修复；前台聊天成功不能证明后台画像学习成功。
- 推荐封面仍有 image-proxy 502/504，未影响上述聊天流程，本轮没有修复图片来源问题。
- 一次 13 秒延迟没有稳定复现。没有据此猜测根因或修改代码，也未重复全仓自动化测试。
- 本轮未发现所测新增聊天功能的阻断性回归；未覆盖全部 Agent 工具、CLI、真机浏览器、
  当前 main 合并兼容性或生产发布。

## 本机证据

原始证据可能包含测试会话和副本数据，仅留本机，不提交：

- `/tmp/chat-e2e-0928-{memory,personas,latency}.json`
- `/tmp/chat-e2e-0928-{desktop,mobile,extension,extension-after-delete,approvals}.json`
- `output/playwright/0928-desktop-search.png`
- `output/playwright/0928-mobile-read-{320,390}.png`
- `output/playwright/0928-mobile-delete-executed.png`
- `output/playwright/0928-extension-{read,after-delete}.png`

结束后关闭本轮三个浏览器会话及 18439 测试服务；用户原服务保持运行。
