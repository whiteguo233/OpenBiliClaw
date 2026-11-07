# Evaluation 模块

## Provider 结构统一（2026-11-06，应用待发布）

| 已实现能力 | 公共接口与行为 |
| --- | --- |
| 评估 harness 遵循模块链 | `evaluation_uses_sponsored()` 读取有效 evaluation 链；`sponsored_agent_text()` 通过普通 LLMService 执行，保留原 Read/Grep/Glob 白名单与只读边界。自有 evaluation 模块链不被免费默认实例截获。 |

配置见 [Config](config.md#硅基流动作为普通-provider)，验证见 [本次验收](../testing/2026-11-06-unified-providers.md)。

| 功能 | 状态 | 接口与说明 |
| --- | --- | --- |
| 裸 provider/Registry 模拟与评分 | 已实现 | PersonaGenerator、EventSimulator、PromptOptimizer 的 complete 调用通过 complete_project_task；保持其消息与解析流程 |
| 结构化质量评估 | 已实现 | DiscoveryEvaluator / 各 LLMService.complete_structured_task 走统一 SDK |
| Claude agent 旁路的赞助接线 | 已实现，未发布 | sponsored_agents.agent_options / sponsored_agent_text；关闭时保留 Claude SDK，可用时不要求安装 Claude SDK |
| 优化器的只读工具 | 已实现，未发布 | Read/Grep/Glob 只读取原优化源码白名单，路径 resolve 校验、限制结果长度/轮数，不执行 shell/写入 |

`agents.collect_json()`、`run_*_agent()`、persona_judge、evaluator 和 speculation_evaluator 的业务入口与结果类型不变；赞助调用返回后继续使用原 JSON 提取、对齐与评分逻辑。模型不可用时按统一配置回退；源文件工具属于 Core，不进入 Rust 或项目方服务。模型质量、免费额度和相似度阈值校准尚需实际发行验证。

覆盖清单由 [audit_sponsored_coverage.py](../../scripts/audit_sponsored_coverage.py) 生成。新增直接 SDK 调用必须接入统一路由并更新清单，不能只覆盖主服务却遗漏评测路径。详见[全项目设计](../plans/2026-10-27-sponsored-all-tasks.md)。

### 授权真实模型验收（2026-10-28）

配套私有 `tools/live_sdk_probe.py` 使用项目现有提示词 builder 与公开 SDK，经临时签名策略、封装 Key 和 release Rust 子进程访问硅基流动。API Key 只经封装工具 stdin 传递，模型请求全部由 Rust 发出。两款 Qwen 使用相同画像、内容评分、推荐文案及工具用例，关闭思考；stream 验证只代表当前 SDK 的缓冲接口。BGE-M3 验证文本向量及相似度，不声明图片向量。真实结果及质量缺陷见[记录](../testing/2026-10-28-sponsored-granted-models.md)。
