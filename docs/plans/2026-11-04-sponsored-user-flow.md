# 免费双模型完整用户流程

## 已确认的产品边界

- 新用户初始化推荐 `Qwen/Qwen3-8B`，可以选择 `Qwen/Qwen3.5-4B` 或自有渠道；已有配置保留。
- 插件、PC、手机与CLI共用同一份本机配置，保存应用后新调用使用所选模型，重启保留；在途请求允许完成。
- 默认只在两个免费模型间进行有界可用性回退。自有渠道回退必须用户明确开启，契约错误不回退；流已输出后不重复执行。
- 日常画像、发现、评估、推荐解释、记忆与对话/Agent工具走文本能力。图片理解是可选能力，调用时交给4B；BGE-M3只提供1024维文本向量，不提供图片向量。独立Jev/导演等原有可选增量能力仍有自己的配置。
- Git/AI安装、Docker、桌面通过同一套已验签组件安装逻辑接入；普通用户不输入共享Key、不安装Rust。

## 实现

```text
共享设置 → PUT /api/config → 保存 + runtime apply + 子进程重建
                          → model → 已签名 task ID
LLMService / Registry / eval → SponsoredProvider → framed IPC2 → Rust → SiliconFlow
EmbeddingService            → BGE-M3 签名任务                 → /embeddings
业务结果                    ← Core结构/证据校验 ← delta + terminal/usage

Core固定目录/公钥 → HTTPS归档摘要 → manifest/策略/文件验签 → 离线自检
                 → .sponsored/bundles/<manifest hash> → 原子active指针
```

`llm/sponsored_profiles.py` 为两套聊天操作、旧合并、可选视觉和文本向量生成十项契约。选择字段不变成任意模型覆盖。业务Prompt保持Core侧维护，Rust验证操作、载荷、固定出口、有效期与预算。

文本/JSON传输使用真实SSE；工具沿用Core的prompt模拟协议，执行权不进入Rust。JSON对象约束会破坏根数组输出，structured/vision的签名传输格式已改为text，JSON指令和解析校验继续保留。上下文采用硅基流动部署的8B 128K、4B 256K档；当前用保守UTF-8字节估计预留输出，不宣称精确tokenizer，不裁掉业务内容。超限可在显式免费互备开启时试更大窗口，否则报错。BGE采用8192字节保守边界。

向量缓存按provider/endpoint/model/维度隔离，换聊天模型不变更BGE空间。空向量、失败和错误维度不缓存。关闭免费方案后恢复原有自有配置。

## 安装、更新与信任

Core中的 `runtime/sponsored_release.json` 是独立信任根；不信任下载包自带公钥，也不接受浏览器提供可执行文件/URL。签名归档只允许固定文件，拒绝链接、路径穿越、重复与超大成员。执行前完成验签和兼容性检查，离线自检通过才激活。更新失败保留旧目录/指针。

源码bootstrap预备组件；源码updater在新依赖安装后、重启前协调组件。Docker构建内置匹配平台的包，桌面打包放入Resources。macOS整体签名不能改写已签名SDK，签后再次检查manifest。加密/混淆降低直接取Key的难度，客户端持有的共享凭据仍不能保证不可提取；本机额度账本也不替代供应商账户限额。

## 当前交付边界

本分支已具备完整代码、四端真实后端浏览器测试、Rust真实API流式测试和签名安装测试。五平台SDK组件已[发布](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/sponsored-runtime-2026-11-04.1)，Core目录包含固定下载URL、独立长期公钥和文件摘要；主程序改动仍通过[PR336](https://github.com/whiteguo233/OpenBiliClaw/pull/336)交付，不能把SDK发布等同于已向现有用户发布新版应用。

本次已配置私有仓main-only手动发布环境、长期Ed25519密钥和有限预算，固定已审阅的公私仓SHA。当前私有仓套餐不支持required reviewer，不能声称有人工审批强制门禁。Mac两架构完成Developer ID签名和Apple Accepted公证；Windows当前采用显式manifest-only模式，由Core独立验签，不宣称Authenticode签名。策略有效90天，具体到期时间、公证ID和摘要见[发行审计](../testing/assets/2026-11-04-sponsored-release-audit.json)。

单安装公平使用上限为20,000次/天、20M tokens/天、300M tokens/月、300 RPM；这是客户端有限预算，不是供应商账户获批额度。轮换凭据、续期或变更Core契约后，需要重新生成并验证组件和固定目录。在线原生安装、损坏升级保护和双架构Docker由专门CI执行；应用整包另走安装器构建验收。

[测试与质量记录](../testing/2026-11-04-sponsored-user-flow.md)区分连接成功、格式完整和事实质量。两模型仍可能过度推断画像或补造时间依据，免费接入不改变这项质量边界。

后续[全系统业务验收](../testing/2026-11-04-sponsored-system-acceptance.md)确认主要基础链路可以执行，但尚未达到全部稳定可用：完整后台存在候选评估超时、聊天排队和繁忙时配置应用等待，工具指令与内容忠实性也有漏项。Core 已补齐通用操作的隐式思考预算及 Agent 真流式，旧归并契约与私有 SDK 本轮未改。后续应分别验收长批次、前后台请求竞争、配置应用时限和严格多步工具执行，不能用短连接探测替代整体验收。
