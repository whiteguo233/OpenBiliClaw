# 已申请模型经 Rust SDK 的真实调用记录（2026-10-28）

使用用户在 Codex Session「调研可用免费模型及新增模型」提供、并在本轮授权测试的 Key，完成 Qwen/Qwen3-8B、Qwen/Qwen3.5-4B、BAAI/bge-m3 的真实调用。16 个用例中，13 项取得有效协议响应或向量，3 项失败；成功响应仍有质量缺陷。本次小样本中，8B 的文本任务更稳定，4B 延迟高且出现超时和 503，BGE-M3 的文本向量验证通过。

脱敏逐项结果、usage、生成内容、二进制及关键源文件 SHA-256 见[机器记录](2026-10-28-sponsored-granted-models.json)。测试时间为北京时间 2026-10-28 12:15—12:26。没有改变项目默认模型、本机配置或现有向量缓存，也没有发版或部署。

## 调用方法

```text
项目现有提示词 builder + 合成输入
  -> SponsoredProvider（独立测试注册表）
  -> IPC2 + 临时签名策略
  -> release Rust 子进程（嵌入封装 Key 与验证公钥）
  -> 编译期固定的 SiliconFlow chat / embeddings 地址
```

- 公共 Core 基线为 `2538256d2a54290820e46d228808aac3446f9525`，私有 Runtime 基线为 `a48bb1642aded2497e71e1bb0aa6148ab854bb5b`，均叠加本次 worktree 改动。机器记录同时保存关键源文件哈希，避免把工作树构建误记为干净提交的发行包。
- 私有 `tools/live_sdk_probe.py` 生成一次性 Ed25519 签名材料、短有效期策略和 vault，执行 `cargo build --locked --release --bin obc-sponsored-runtime`。没有启用 development、mock、unsigned 或运行时外部公钥覆盖；真实模型 HTTP 请求全部由 Rust 发出。
- 两款 Qwen 使用相同合成输入及现有画像、批量内容评分、推荐文案提示词；明确设置 `enable_thinking=false`。本次测试注册表的输出上限为 2048 token，各用例进一步限制到 64—1536 token，单次等待上限 100 秒。
- 同一测试 Key 的签名总预算为每日 30 次、每日及每月 150,000 token、每分钟 20 次；策略有效期 3 小时。余额保护下限设为 0，以测试账号申请的模型额度。未把应用实际扣费或长期免费资格作为本次已验证事实。
- 8B 的测试策略不注册视觉；4B 增加一项内联图片识别。两份策略都将文本向量绑定到 BGE-M3，不注册图片向量。

这是直接 Provider 的受限测试配置。它不等于用同一配置启动完整 LLMService；例如生产结构化任务可能要求至少 4096 token，仍需另行确定正式上限及能力组合。此前的 [108 处调用点覆盖审计](2026-10-27-sponsored-all-tasks.md)也不等于本次逐一执行了 108 处真实业务。

## Qwen 结果

耗时包含 SDK、IPC、网络和模型生成，单位为秒。503 保留原错误，未切换模型或付费通道来补齐结果。

| 用例 | Qwen3-8B | Qwen3.5-4B |
| --- | --- | --- |
| 连通性标记 | 成功，1.058 | 成功，42.429 |
| 项目画像提示词 | JSON 可解析，17.216 | JSON 可解析，61.294 |
| 项目批量内容评分提示词 | JSON 可解析，13.428 | JSON 可解析，90.811 |
| 项目推荐文案提示词 | JSON 可解析，13.990 | 100.029，`UNAVAILABLE` 超时 |
| 项目工具接口 | 正确返回 search 及 Rust 查询参数，1.501 | 2.056，`UPSTREAM_ERROR` / HTTP 503 |
| SDK 缓冲 stream 接口 | 成功，6.774；正文首块 6.773，收到 terminal | 100.004，`UNAVAILABLE` 超时 |
| 96×96 纯红 PNG 识别 | 未开放此测试能力 | 正确返回 `{"color":"red"}`，35.890 |

8B 为 6/6 项请求成功，4B 为 4/7 项成功。503 与超时反映当时的服务可用性和本次等待预算，不能据此认定 4B 永久不支持工具或流式。

这里的工具接口沿用 Core 的 prompt 模拟与解析，未测试供应商原生 HTTP function calling。stream 是完整响应后的缓冲适配，未实现或测试上游逐 token SSE。

## BGE-M3 结果

修正了 SDK 原先面向 Qwen-VL 的 embedding 请求体：BGE-M3 使用经典字符串 `input`，不发送 `dimensions` 覆盖。官方请求格式见 [SiliconFlow embeddings API](https://docs.siliconflow.cn/docs/api/embeddings-post)。响应模型和固定 1024 维向量按签名契约校验，不能因模型切换复用原来的向量空间假设。

| 文本 | 耗时（秒） | 检查 |
| --- | --- | --- |
| 我想学习 Rust 的所有权和借用机制 | 0.294 | 1024 维、有限值、非零、范数约 1 |
| Rust 内存安全教程：所有权、借用和生命周期 | 0.156 | 同上 |
| 今天天气预报说明天会下雨 | 0.188 | 同上 |

第一句与相关 Rust 教程的余弦相似度为 **0.844749**，与天气文本为 **0.368871**，顺序符合预期。这只验证一个语义样例，不能直接沿用或认证现有推荐阈值。BGE-M3 仅承担文本向量，图片向量策略及请求由本地校验拒绝，没有将图片发送给它。

## 内容质量复核

- 两款 Qwen 的评分结果均覆盖两个输入 ID，并正确降低用户明确不喜欢的博彩内容：8B 的 Rust / 博彩分数为 0.95 / 0.15，4B 为 0.92 / 0.00。
- 两款模型都将博彩样本标记为 `current`，并写出“近期讨论”作为时间证据，但输入没有提供发布时间。这属于无输入依据的时间推断，不能因 JSON 有效就视为业务质量通过。
- 8B 的推荐说明为 60 字，符合该提示词要求的 50—150 字，并提及所有权、借用和编译器示例。4B 此项没有成功响应。
- 画像只用了极少量合成历史。JSON 可解析不代表人格、生活阶段等推断正确；本轮未做真实用户画像、持久化结果或推荐全链路验收。

## 凭据和清理

Key 从已授权的原 Session 在进程内读取，经 stdin 传入封装工具，没有放入 shell 命令、argv、环境变量、项目配置或报告。临时目录权限为 0700，密钥材料文件为 0600。两份策略均通过真实签名及解封检查；release 二进制中未发现连续明文 Key。该扫描不能证明客户端二进制具有不可逆的密钥保密性。

测试结束已清理临时二进制、构建目录、vault、签名材料和本次临时数据目录对应的唯一账本 Keychain 条目。报告中的 `signed_policy_and_unwrap_check`、`plaintext_key_scan`、`temporary_ledger_keychain_cleanup`、`temporary_materials_removed` 均为 true。改动文件和本地结果文件也通过对原 Key 的精确匹配扫描。

## 本轮本地检查

| 检查 | 结果 |
| --- | --- |
| Core：`pytest tests/test_sponsored_*.py tests/test_llm_sponsored_routing.py tests/test_embedding_service.py tests/test_llm_registry.py -q --tb=short` | 248 passed，1 skipped |
| Core：`ruff check src/ tests/`、`mypy src/` | 通过，MyPy 检查 379 个源文件 |
| Runtime：`cargo test --locked` | 62 passed，1 ignored |
| Runtime：`cargo test --locked --features development` | 61 passed，1 ignored |
| Runtime：`cargo clippy --locked --all-targets -- -D warnings`、`cargo fmt --all --check` | 通过 |

Core 跳过项是需要额外环境变量的历史可选 Rust bridge；Runtime 忽略项是系统 Keychain 单元测试。本次真实 release 调用独立验证了实际 IPC、网络和临时账本清理，不能把未执行的单元项记为通过。此次没有重复跑完整项目测试集，也没有测试最大上下文、并发吞吐、长期稳定性或系统签名安装包。
