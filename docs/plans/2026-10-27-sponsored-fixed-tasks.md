# Sponsored 固定任务 IPC v2（未发布）

本阶段方案已由[全项目 SDK](2026-10-27-sponsored-all-tasks.md)取代；下文保留单任务阶段的历史范围。

日期：2026-10-27。源码默认关闭，只有配套私有 Runtime/签名 Policy 的可选发行版可使用。当前只有已接通的画像合并，不自动把其他模型调用迁入赞助通道。

## 数据流与类型

```text
ProfileConsolidator -> execute_sponsored_or_structured(payload)
 -> SponsoredProvider: local typed input check -> IPC v2 input (no messages)
 -> private Runtime: fixed task + signed Policy + schema
 -> canonical system + fixed legacy user renderer -> SiliconFlow HTTPS
 -> standardized usage/result -> Core
```

execute 只允许 `type`、`id`、`task_id`、`contract_version`、`input` 五个字段；类型固定 `soul.consolidation`、contract_version=2。Runtime 拒绝旧 messages/requested、任意模型/温度/URL/Prompt 覆盖。Policy 只为编译期支持的类型启用权限，签名文件不能自行创建通用聊天类型。

input 仅包含 likes_clusters/dislikes_clusters，总共最多32簇；每簇字段固定 cluster_id、known_distinct_pairs、members。likes member 为 name/weight/category，dislikes member 为字符串；嵌套字段和长度/数量均有限制。上限与旧批处理32、旧输出16384一致；字段长度/数量安全上限是该接口的验收边界，新增任务或扩大限制需更新两端测试与策略。

模型、温度、输出限额由签名 Policy 提供。握手包含任务 system hash contracts；Core 比对，策略/Prompt 不一致失败而非悄悄用旧 Prompt。system Prompt 文本及 hash 由公共 builder 同源生成；Runtime 按固定标签和 deterministic pretty JSON 渲染 user Prompt。共享 tests/fixtures/sponsored-consolidation-v2.json 钉住旧 BYOK Prompt 的完整字节，不引入专用业务 Prompt。

## Core API

`SponsoredProvider.execute_task(caller=..., user_payload=...)` 只发送结构化 input。保留的 system_prompt/user_text 参数仅作本地字节漂移校验，不传到 Runtime，也不允许改变业务 Prompt。额外字段、非法嵌套或不一致文本为 CONTRACT_MISMATCH，不静默消费用户自己的模型预算。可用性、额度、过期策略等按已有 fallback 配置处理。

`SponsoredTaskContract.to_policy_task()` 增加 system_prompt 字段；默认 registry 升级为 contract2。mock runtime 与真实 runtime 使用相同 IPC 版本。旧 Runtime 握手失败，不能自动退回宽松 v1 入口；需同时更新 Core/Runtime 并重新生成签名策略与包装材料。

## 模式与界面范围

- git / AI 安装：保持 enabled=false 和 BYOK/Ollama，不引入私有源码、Rust 编译依赖或赞助 Key。
- Docker：仍为源码路径，不自动下载/启用私有二进制；显式配置需自行提供配套的正式 Runtime bundle。
- 桌面：安装包包含并核验 bundle 的步骤尚待正式发行验证。本分支不改变默认开启状态，不声称已进入已发布安装包。
- 插件 popup、桌面 web、手机 web、CLI 继续共用后端配置和现有错误/回退行为，本轮没有新增 UI 设置或交互。

## 轻量启动依赖

`consolidation_prompt.py` 是静态 Prompt/纯 user renderer 的单一来源，业务 builder 和契约生成共用。`llm` 包保留既有公开导出并按访问加载 SDK，mock/协议工具不再为握手加载所有远端模型 SDK；生产握手时限不变。测试在独立子进程检查 mock 不加载 OpenAI/Anthropic/Google GenAI SDK。

## 安全与验收

生产 Runtime 固定信任根和上游目标，拒绝开发开关；密钥与请求限制保留在私有模块。用户仍可模拟合法簇数据，文本字段仍有 prompt injection 风险；本地校验、配额与混淆不能代替供应商实际执行的权限和撤销。

公共侧运行 pytest、mypy src/、ruff check src/ tests/；私有侧运行默认/开发构建测试、Prompt golden 与假材料发布验收。实际模型质量、签名公证、四平台安装/更新和上游限额需单独记录，不以 mock 测试代替。

本次执行结果、配套 Runtime 提交及未完成的全量测试详见[验收记录](../testing/2026-10-27-sponsored-fixed-tasks.md)。
