# 固定 Provider 的真实初始化与调用验收（2026-11-07）

本次在 `feat/unified-sponsored-providers` 的 `f5eb53f4` 上验证固定实例的实际使用路径。使用 macOS Intel、正式验签 SDK `2026-11-06.2`、Core `0.3.234`、真实 Uvicorn HTTP/SSE 后端与 Chromium。模型和向量请求实际经过 Rust 组件访问硅基流动；没有使用模型替身、注入向量或用户自己的 Provider 兜底。

两个全新临时目录分别从 `default_chain=[]`、Embedding 未选择、旧赞助开关关闭开始。安装正式签名 SDK 后保持 `runtime_path` 为空，通过产品正常组件发现与准备流程连接。全部输入都是合成测试数据，未导入实际用户的配置、Key、Cookie 或历史。每轮结束均关闭测试后端、清理临时目录及该目录专属的 Keychain 账本。

## 初始化、切换与持久化

| 场景 | 实际结果 |
| --- | --- |
| 初始化选择 Qwen3-8B 与免费 BGE-M3 | 保存后默认链为 `[siliconflow-free]`；LLM 探测返回 `Qwen/Qwen3-8B`，Embedding 探测返回 `BAAI/bge-m3` |
| 初始化选择 Qwen3.5-4B 与免费 BGE-M3 | 保存后默认链为 `[siliconflow-free-qwen35-4b]`；LLM 探测返回 `Qwen/Qwen3.5-4B`，Embedding 探测成功 |
| PC 配置页把另一免费模型加入并移到首位 | 两个方向均成功；新选择位于第一项，原选择保留在第二项，真实整链探测使用第一项模型 |
| 后端完全退出再启动 | 两个方向的默认链顺序均保留；LLM 与 Embedding 真实探测再次成功 |
| 手机网页实际发送聊天 | 8B、4B 均收到多个增量事件与最终完成事件；数据库用量记录的实际模型与选择一致 |
| 扩展配置页逐项点击两个 LLM 的测试按钮及 Embedding 测试按钮 | 三项均成功，返回相应的两个 Qwen 模型及 BGE-M3；内置项没有编辑或删除按钮 |

初始化只把选中的 LLM 放入默认链。另一个实例仍在固定目录里，可由用户加入备用位置。BGE-M3 属于独立 Embedding Provider，选择后直接启用，不进入 LLM 调用链；切换聊天模型不会改变向量配置。

扩展检查使用实际 popup HTML/JS 与真实后端、SDK、上游服务，但 `chrome.storage/runtime` 宿主接口由测试夹具提供。这一项不代表重新验收扩展商店安装、后台采集或浏览器权限流程。PC、初始化及手机网页直接访问实际后端。

## LLM 与 Embedding 请求

- 两个模型均完成实际 Agent 多跳流程：读取 `read_memory` → 工具结果 → 流式正文 → 最终回复。后端数据库记录了两个模型的 `agent.chat` 用量；部分请求还触发真实的 `soul.dialogue_insight` 调用。
- 手机页面的流式正文分别取得 21～45 个增量事件（含首轮与复测样本）。
- BGE-M3 通过初始化、模型切换后、后端重启后和扩展页面的真实探测。探测绕过向量缓存。
- 额外用保存后的真实配置构建 Embedding 服务，把 LLM 默认链清空后同时发出 3 个不同文本请求。1.074 秒内全部得到非空 1024 维向量；两句 Rust 所有权释义的余弦相似度为 0.9402，与烘焙文本的相似度为 0.2948。此项使用真实 SDK 服务接口，未经过浏览器。

### 真实发现：4B 的格式异常与耗时波动

Qwen3.5-4B 的首轮工具对话在完成记忆工具流程后，最终正文又附加了 `{"tool_call":{"name":"get_profile","arguments":{}}}`。这段 JSON 是正文，没有作为原生工具调用执行。连接、实例选择和流式协议都成功，但这次正文质量不能算完全通过。

同一配置与提示追加 3 次真实复测，均得到成功的工具结果与正常正文。合计 4 次该场景中观察到 1 次格式异常；四次耗时分别为 4.404、28.211、2.580、3.306 秒。一轮连接探测也曾用时 14.439 秒，其他样本约 1 秒。样本不足以推算稳定性比例或吞吐指标，应保留原异常，不能因重试成功就删除它。

本轮确认的是 Provider 接入、初始化选路、真实请求和持久化可用；不据此宣称模型在所有长任务、格式约束或高并发场景下都可靠。本轮结束时 4B 的正文格式问题尚未解决；后续增加正文协议检查与真实回归，见[工具协议正文修复验收](2026-11-07-tool-text-guard.md)。此处保留首次失败及原始样本。

## 自动回归与证据

加强了 `tests/test_sponsored_runtime_e2e.py` 的初始化回归：原夹具通过旧赞助开关迁移预先激活了默认链，本次改为从真正空链、未启用向量开始，参数化两个模型，断言初始化后的第一项、实际整链返回模型、独立向量、双向切换后的备用位置和重启持久化。

```bash
PYTHONPATH=src pytest tests/test_sponsored_runtime_e2e.py::test_setup_without_a_key_then_desktop_switch_and_restart -q
```

结果为 2 通过（181.28 秒）；该自动回归沿用确定性 IPC 测试进程，真实上游证据单独保存在[实测数据](2026-11-07-fixed-providers-live.json)。Ruff 与差异检查通过。本轮只增加测试及文档，生产代码未改；未重跑全量后端测试或跨平台发行构建。

测试脚本首轮的向量追加步骤误在 Playwright 同步 API 的事件循环里调用 `asyncio.run`，修正为独立线程执行后完成复测。浏览器退出时有 Playwright 未完成 response future 的 `Target closed` 警告；最终两份验收进程均以 0 退出，后端和对应 Keychain 条目均完成清理。此类夹具问题与模型输出异常分别记录。

本机原始日志：`/tmp/obc-fixed-live-e2e.log`、`/tmp/obc-fixed-live-followup.log`、`/tmp/obc-fresh-provider-regression.log`。截图：`output/playwright/live-fixed-providers-20261107/` 与 `output/playwright/live-fixed-followup-20261107/`。当前分支尚未合入 main 或发布安装包。
