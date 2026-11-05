# 免费模型断线、重试、互备和任务恢复验收（2026-11-05）

基线 Core `7a74099d`，修复分支 `fix/sponsored-resilience`，后续并入 PR336。使用已发布的 `sponsored-runtime-2026-11-05.1` Mac Intel 组件，manifest SHA256 `9c4f6dad43996b9882d54da4eb82412335b4eaf55c5bab5a4fca4a27478ec3de`。本轮不修改或重新发行 Rust 二进制。机器证据见 [results.json](assets/sponsored-resilience/results.json)。

## 发现并修复的问题

| 实际缺口 | 修复与边界 |
| --- | --- |
| 交互总超时在互备循环外触发，首个模型一直无响应时备用模型永远轮不到 | 仍保留30秒首段总预算，两个候选共享，首个约15秒；同模型重试也共用该时间片，及时终止旧进程并切到备用。显式思考沿用请求级时限。 |
| 免费互备与自有渠道回退用了同一个错误规则，共享Key失效后仍试另一个模型 | 区分两个策略；AUTH_FAILED、POLICY_INVALID、QUOTA_EXCEEDED、BALANCE_LOW、LEDGER_TAMPERED不做免费互备。显式允许的自有渠道回退仍有效。 |
| 前台临时超时直接把消息标failed，后台却可能对失效Key一直重试 | 尚无事件/工具的临时免费故障保留pending，最多两次附加持久重试；计数持久化，重启不清零；永久故障直接终止。 |
| 持久恢复可能重新执行已经开始的Agent轮次 | 首个delta保存不含内容的执行标记，工具/语义事件照常保存；恢复时发现已开始则明确终止，不从头重跑。HTTP订阅断开仍让原执行继续，完成消息重连只回放结果。 |
| 插件/手机把后台重试的SSE提示当终态失败 | SSE返回 `retrying: true`，客户端保持pending并继续轮询同一个turn；桌面通过既有历史刷新读取最终状态。 |

先失败后修复：最小SDK回归最初3项失败（首段互备、两种接口的失效Key重复调用）；API最初6项失败（前台临时错误无法恢复、后台永久错误/部分输出/工具及重启场景重复尝试）。新回归保留这些触发条件，不通过放松终态或输出完整性断言取得通过。

## 真实网络与进程故障

代理只接受 `CONNECT api.siliconflow.cn:443`，转发未解密TLS字节，主动暂停或关闭测试进程的连接。模型答案均来自真实硅基流动；没有替换模型响应、暴露密钥、改全机网络或撤销生产Key。所有数据库/账本是新建隔离目录，免费互备开启，自有付费回退关闭。

| 验收 | 8B为首选 | 4B为首选 |
| --- | --- | --- |
| 9项SDK矩阵 | 9/9符合预期 | 9/9符合预期 |
| 首段前断网 | 同模型第2次成功，1.58秒 | 同模型第2次成功，13.43秒 |
| 首个模型连接持续暂停 | 约15秒切4B，16.32秒首段，17.78秒完成 | 约15秒切8B，15.38秒首段，17.03秒完成 |
| 杀死本测试拥有的Rust子进程 | 切4B，2.79秒完成 | 切8B，2.00秒完成 |
| 收到真实首段后断网 | 明确UPSTREAM_ERROR，无终帧、不重放 | 明确UPSTREAM_ERROR，无终帧、不重放 |
| 故障后的聊天与BGE | 聊天完成，BGE1024维有效 | 聊天完成，BGE1024维有效 |
| BGE连接中断 | 同一个IPC内重建连接后成功，0.57秒 | 同一个IPC内重建连接后成功，0.56秒 |

9项包括预热BGE、首段前断线、静默主模型、子进程崩溃、流中断、恢复聊天、恢复向量、向量断线、最后聊天。预期的流中断被明确判为错误，不能当成模型完成。连接层与Rust重试结合的证据是：BGE只收到一次execute，同时创建了两条TLS连接，返回有效向量。

## 完整HTTP、并发及重启

| 验收 | 8B首选 | 4B首选 |
| --- | --- | --- |
| 聊天×2、评估×2、向量×2同时提交 | 6/6，22.78秒 | 6/6，13.68秒 |
| 三个Rust worker运行时同时断开测试连接，6项混合任务 | 6/6，23.09秒，峰值3 | 6/6，10.73秒，峰值3 |
| 两个候选都静默到30秒后恢复网络 | 原消息保持pending，持久重试后32.06秒完成 | 原消息保持pending，持久重试后32.53秒完成 |
| 浏览器在首段后主动断开HTTP | 后台完成，重连无新生成 | 后台完成，重连无新生成 |
| 真正上游在首段后断线 | 数据库failed，不保存半截为成功，重连不执行 | 同左 |
| 未开始的pending消息，关闭/重建API应用并重读同一数据库 | 完成，恢复观察1.79秒 | 完成，恢复观察1.55秒 |

72/72并发期间的 `/api/ping` 探测成功。并发峰值指真实IPC在途请求，不能推导云端GPU吞吐或九千用户容量。服务重启测试重建了Uvicorn应用和SDK进程，并复用持久数据库；不宣称测试了断电、内核崩溃或文件系统损坏。工具执行边界及重启禁止重放由API回归覆盖；本轮真人服务请求使用简短文本/候选评估/BGE，不据此宣称模型能准确完成所有长任务或工具选择。

初轮 `http-fault4b` 的partial断言曾失败：4B自然首段超时切8B，加上并行学习调用，导致脚本总IPC数为3，脚本误以为必须等于1。数据库实际failed，出字后的中断没有重放。保留原记录，脚本改为核对“注入故障时的聊天尝试数”和“此后不增加”，两模型各4项重新通过。不能把这次自然慢响应从结果里删除。

另用先前保存的、生产模式编译且独立签名的测试组件（仅含合成无效凭据）复测：真实服务返回AUTH_FAILED，只发一个execute；过期签名策略返回POLICY_INVALID，发出零个execute。生产凭据未撤销。500重试与429错误分类来自Rust本地HTTP/单元回归，Python补测429先重试一次再切免费模型；未主动耗尽共享账户以制造真实云端429、余额不足或日额度耗尽。

## 验证与复验

完整pytest正在固定代码快照上运行，当前不作为全量通过的证据。已完成：定向Python114项、Rust上游18项、插件12项、Web JS 54项，Ruff、MyPy（386源文件）、Chrome插件构建和22项资源预检通过。原始报告与后续重复结果均保留。

在新目录运行，`BUNDLE` 指向已验签组件目录，不填API Key：

```bash
PYTHONPATH=src python scripts/sponsored_resilience_acceptance.py --root output/sdk-faults --model Qwen/Qwen3-8B --bundle "$BUNDLE"
PYTHONPATH=src python scripts/sponsored_http_resilience_acceptance.py --root output/http-faults --model Qwen/Qwen3-8B --bundle "$BUNDLE"
PYTHONPATH=src python scripts/sponsored_mixed_resilience_acceptance.py --root output/mixed-faults --model Qwen/Qwen3-8B --bundle "$BUNDLE"
PYTHONPATH=src python scripts/sponsored_http_concurrency_acceptance.py --root output/http-burst --model Qwen/Qwen3-8B --bundle "$BUNDLE" --free-fallback
```

将模型换为 `Qwen/Qwen3.5-4B` 并换一个新root复测。真实服务仍可能慢、限流或两模型同时失败；兜底的目标是有界恢复、清晰报错和不重复执行，不能保证所有请求必定成功。组件仍使用已发布版本；本轮Core修复进入PR336，主程序合并/发版前不会自动生效到既有安装。
