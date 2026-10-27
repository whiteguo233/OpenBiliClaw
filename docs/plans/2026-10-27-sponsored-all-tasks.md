# Sponsored SDK：全项目 LLM 任务（未发布）

本方案取代只覆盖画像合并的阶段性实现。用户要求是支持本项目所有需要 LLM 的任务；固定的是 SDK 操作和传输权限，不能把一个具体业务当作全部 SDK。源码默认仍关闭，没有自动部署或调用真实模型。

## 覆盖范围

[audit_sponsored_coverage.py](../../scripts/audit_sponsored_coverage.py) 扫描项目调用点并生成[覆盖清单](../testing/sponsored-task-inventory.json)。它覆盖画像初始化/偏好/觉察/洞察/门控/分层更新/合并/分类迁移/推测、搜索词和探索/来源内容抽取、发现与推荐评估/推荐文案/情绪判断、普通和工具对话、封面视觉评估、文本/文档/图片向量，以及模拟人物/事件、质量评测和优化器。清单测试防止新入口未经审阅加入。

| 固定任务 | 版本 | 应用任务 | Runtime 请求 |
| --- | --- | --- | --- |
| llm.structured | 3 | 所有 JSON 分析、抽取、生成、评分 | 固定 chat/completions，JSON object |
| llm.text | 3 | 普通对话、非 JSON 文本 | 固定 chat/completions |
| llm.tools | 3 | 项目已有 JSON 工具协议、评测只读工具循环 | 固定 chat/completions；工具在 Core 执行 |
| llm.vision | 3 | 文本和封面图的结构化评估 | 固定 chat/completions；图片仅 inline data URL |
| llm.embedding_text | 3 | 短文本、全文档、探测向量 | 固定 embeddings，float 向量 |
| llm.embedding_image | 3 | 封面图向量 | 固定 embeddings；与文本同模型、同维度 |
| soul.consolidation | 2 | 旧画像合并兼容入口 | 保留有界簇数据和固定 renderer |

长度帧仍为 IPC 2；新增任务 contract3。Core/Runtime/Policy 成套升级，握手要求全套任务契约，不能用旧的单任务 Runtime 假装支持全项目。

## 统一路由

```text
LLMService text/JSON/tools/vision ─┐
Registry.complete / bare eval ────┼─ shared SponsoredProvider ─ IPC ─ private Runtime
Evaluation agent + read tools ───┤                              ├─ chat/completions
EmbeddingService + own cache ────┘                              └─ embeddings
```

LLMService 在既有并发 gate 内路由；保留 core memory、history、原 Prompt 和工具输出解析。Registry 原始调用和可能收到裸 provider 的 eval 走同一入口。启用 Sponsored 后 registry 将其注册为有效默认，未配置 BYOK 也能构建；原用户链保留为显式允许的可用性回退。模块 override 继续作用于用户回退路径。

同一事件循环共享一个 Runtime 子进程，避免每个业务重复启动。关闭等待正在执行的请求。不同应用工作进程允许各自持有子进程：安装账本只在 reserve/commit 事务持锁并重读/验 HMAC，HTTP 期间不持锁。全局 Key cap 和各任务额度分别计算；一个任务不能挤占另一个任务的专属请求额度。失败/未知 usage 保留预留，跨日完成不减掉新一天其他请求的预留。

完成请求只允许 messages、caller、temperature、max_tokens、reasoning_effort；温度 0..2，输出不超过签名上限（编译上限32768），最多128条消息。角色仅 system（首条）、user、assistant；禁止请求自定义模型、目标、Header、Key 和 HTTP 参数。视觉最多32张图、总输入12MiB，每张 data URL 最多8MiB；仅 JPEG/PNG/WebP/GIF base64，不接受远程图片地址。文本任务总输入2MiB。对话本身允许自由文本；这些限制不能认证请求一定由原项目发起。

reasoning_effort 的显式值映射为 SiliconFlow enable_thinking / thinking_budget；None 保持上游默认，空串关闭。能力和计量以最终获批模型为准，不宣称所有模型都支持思考预算。

## 向量空间与评测工具

启用 Sponsored 后 EmbeddingService 使用专属 adapter 和 provenance 缓存；默认文本/图像契约同为 Qwen/Qwen3-VL-Embedding-8B、1024维。响应必须匹配契约模型、维度，所有值有限且不能是零向量。模型/维度/来源不匹配不写缓存。Embedding 不在同一 namespace 下回退另一家模型；不可用时沿现有空向量/熔断降级，切回 BYOK 后恢复原来的独立 namespace。`embedding.multimodal_enabled` 仍控制是否实际使用图片向量。

这次接线改变可选赞助模式下的 embedding 来源，原相似度/准入阈值的校准必须重开；没有用 mock 代替真实质量评测，也没有自动调低阈值。

评测 agent 在赞助可用时无需 Claude SDK。文本/JSON 和 Read/Grep/Glob 经 SDK 路由；文件工具仅能读评测器原有可修改源码白名单，校验 resolve 后路径、拒绝越界/符号链接逃逸，结果与轮数有上限，不执行 shell 或文件写入。关闭赞助或允许的可用性回退时，保留 Claude SDK 的既有行为。指定某个外部 provider 的连通性检查和专门供应商对照实验继续测试目标，不能被赞助替换成一次假阳性。

## 免费能力与正式发布

接口能表达一种能力，不代表当前免费账号获准调用相应模型。文本沿用历史合并 pilot 的 GLM-4；视觉参考官方示例 Qwen2.5-VL-72B；多模态向量参考官方 Qwen3-VL-Embedding-8B。后两项**未确认免费、未实测账号权限、未实测质量**。

私有发布 job 必须配置审阅后的 SPONSORED_APPROVED_MODELS_JSON（项目免费/赠送额度实际覆盖的模型）和 SPONSORED_RELEASE_CAPS_JSON（有限的 daily_requests/daily_tokens/monthly_tokens/rpm）。没有完整清单与预算，或任务模型不在清单内，发布失败。没有在本次工作中配置这些变量、读取真实 Key 或运行发布；不能把 SDK 覆盖完整写成所有能力永久免费。

正式接口依据：SiliconFlow [视觉输入](https://docs.siliconflow.cn/docs/userguide/capabilities/multimodal-vision)、[统一文本/图像 embeddings](https://docs.siliconflow.cn/en/api-reference/embeddings/create-embeddings)。2026-10-27 读取官方公开资料，未请求带凭据 API。发行前仍需用获批免费额度逐类型验可用性、质量和限额。

具体命令、数量、基线失败和配套提交见[最终验收](../testing/2026-10-27-sponsored-all-tasks.md)。
