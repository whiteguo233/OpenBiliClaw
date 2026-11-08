# 模型超时对齐验收（2026-11-08，未发布）

原因：普通 Provider 工厂已使用全局 1200 秒；固定免费实例仍取旧 sponsored 180 秒，Rust HTTP 和 relay 又独立硬编码 180 秒。原初始化外层预算也没有覆盖长请求超时后的一层恢复。

修复：原生免费实例继承全局值；协商式 IPC `timeout_ms` 将预算送至 Rust，HTTP 重试共用剩余时间；Embedding 单独限 180 秒，Rust 连接 10 秒、余额查询 30 秒；交互无显式思考首个有效输出 300 秒以内。初始化按注册表请求预算、实际批数和并发波次扩展默认阶段上限，显式 override 保持不变。

验证记录将在本轮实际执行完成后补全。旧 SDK 能兼容新 Core，但不能获得新版 Rust 的长请求能力；本分支不代表安装包已更新。其他供应商只验证本地接入与超时传递，不声称已逐一调用其付费服务。上下文长度及前一轮真实初始化数据见 [初始化实测](2026-11-08-free-model-init-timeouts.md)。

## 已完成的受控和真实传输验证

- 新增回归先复现免费实例继承旧 180 秒配置、初始化缺少请求预算参数（5 项失败），修复后通过。
- Core 免费 Provider / 配置 / relay / IPC / Web 初始化相关回归：850 passed，1 skipped；其他模型接入回归：165 passed。Ruff 通过，MyPy 413 源文件通过。
- Rust `cargo test --locked`：81 passed，3 个手动验收项 ignored；另外显式执行 190 秒延迟 HTTP 测试成功（190.01 秒），证明真实 Rust HTTP 读取超过旧 180 秒边界。此项使用受控本地服务，不冒充上游耗时。
- Rust 200ms 请求预算 + 700ms 响应延迟验证普通请求及流式 body 超时，普通重试不累计三份预算。Core 新旧协议、取消、流式、Embedding 预算测试通过。
- 本地重新编译、签名策略验证的 Rust 候选真实调用 SiliconFlow：两模型流式首字 1.84 / 0.81 秒；BGE-M3 两次均为 1024 个有限数值。确认运行中的 IPC 协商 `request_timeouts=true`，聊天 1200 秒、Embedding 180 秒。见 [流式记录](assets/model-timeouts-20261108/live-stream-and-embedding.json)。

## 真实初始化发现的额外断连

8B 第一轮 200 条数据，7/8 批完成后因上游 `transport error ... peer closed connection without sending TLS close_notify` 失败，耗时 405.65 秒；失败请求 89 秒，已用尽 Rust 有限传输重试，**并非 1200 秒超时**。保留 [失败记录](assets/model-timeouts-20261108/qwen8b-before-disconnect-recovery.json)，不以随后成功覆盖它。

该失败暴露偏好层只恢复超时而不恢复明确的连接断开。补充与超时共享的一层缩批恢复，TLS peer closed / reset / aborted / unexpected EOF / broken pipe 只在带类型的免费上游 transport error 下触发；持续故障、证书验证、鉴权、额度、策略、格式和模型不符仍失败。实际异常通过 Adapter 包裹的调用点回归先 4 failed，修复后相关测试 53 passed。没有放宽 TLS 校验。

真实初始化结果如下。测试只做合成历史入库、偏好分析、画像生成并持久化，不包括平台在线抓取和初始推荐池生成；没有更改用户现有配置或画像。候选未做 OS 公证或公开发行。密钥只在私有构建环境使用，临时签名种子、Key vault、布局种子与编译缓存已清理。


## 最终真实初始化结果

| 模型 | 合成历史 / 完成计划批次 | 偏好分析 | 含画像持久化总耗时 | 结果 |
| --- | --- | --- | --- | --- |
| Qwen/Qwen3-8B（断连修复后重跑） | 200 条 / 8 of 8 | 238.53s | 244.98s | 成功 |
| Qwen/Qwen3.5-4B | 200 条 / 8 of 8 | 217.50s | 223.72s | 成功 |

见 [8B 报告](assets/model-timeouts-20261108/qwen8b-final.json) 与 [4B 报告](assets/model-timeouts-20261108/qwen35-4b-final.json)。每个成功请求均确认新的 Rust 预算协商能力，1200 秒；没有跨模型兜底。4B 本轮在补充断连恢复前已开始且顺利完成；8B 修复后重跑没有再次遇到断连，因此本轮真实成功本身不证明曾在网络中触发恢复，恢复次数和失败边界另由上述回归测试验证。

偏好 / Soul 最终回归：234 passed；Ruff 与 MyPy（413 源文件）通过。本轮没有重跑 1.4 万项全套，前轮全套状态见前轮报告。真实验收过程曾出现脚本把 frozen settings 当可变对象赋值的启动错误，在任何网络请求前即失败；已改用 dataclasses.replace，再运行上述两模型测试。


## 扩展初始化 / API 回归限制

扩展运行 CLI、GitHub/TikTok 初始化、Web 引导、桌面进度及整个 `test_api_app.py`：1009 passed、6 failed（658.36 秒）。五项对话卡片测试在异步任务尚未完成时得到 202，与测试期待的同步终态不一致；另一个健康检查测试耗时 2.92 秒，超过其 1 秒断言。六项单独复测全部通过（7.01 秒）；没有修改这些测试断言或相关业务代码，也未再重跑完整组合，不能把该组合记录为零失败。最终核心偏好/超时回归仍以 234 passed、53 passed 两组记录为准，存在部分重叠，不相加计总数。

本次只提交隔离修复分支，没有合并 main、推送或发布安装包。旧 SDK 的 180 秒限制不会因为这份验收记录自动消失；下一次发布需要同时携带新版 Core 与 Rust SDK。
