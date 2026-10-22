# 空候选池：真实请求验收

测试时间：2026-10-20 深夜至 2026-10-21（Asia/Shanghai）。代码基线：`d01eace1`，分支 `fix/empty-candidate-pool`。

结论：真实配置传播、候选评估、推荐文案、推荐返回和消费后自动补池均已通过，观察到 **0 → 3 → 消费后 0 → 自动补回 1**。但本机原主备模型链的批量请求没有通过；成功轮次在隔离实例中切换到了同一网关实际提供的 `deepseek-flash`。修复子进程配置同步不能替代恢复模型配额或服务可用性。

## 环境与范围

- macOS 源码环境，共享已安装的 Python 3.12 虚拟环境，通过正式 `openbiliclaw serve-api` 启动主 API，以及 full worker、discovery worker、recommendation、image service 四个子进程。
- 使用本机实际配置的 SenseNova 网关账号、B 站登录信息、画像副本，以及运行中的 Ollama `nomic-embed-text`。没有模型响应替身、虚构 BV 号或手工指定的评分。
- 配置、数据库、画像、日志、API/推荐/图片端口全部隔离；生产配置与生产数据库不写入。候选由正式 worker 的真实 B 站搜索、榜单和相关推荐请求自动入队，没有直接向队列塞入测试候选。
- 为限制测试量，目标池与文案水位均为 6，发现上限为 8，关闭无关来源、自动更新、账号增量、画像合并及搜索灵感。评分阈值保持 0.6，时间有效性和文案就绪门槛保持正式实现。
- 这是本机真实服务验收，不等于用户 Windows 安装包或用户外部模型账号已经验证。

## 已验证的配置恢复链路

| 阶段 | 真实结果 |
| --- | --- |
| 独立连通探针 | `deepseek-v4-flash` 回复成功，耗时 2.01 秒；B 站科技榜返回 97 条，耗时 0.17 秒。这 97 条是探针结果，不是正式候选入队数。 |
| 故障注入 | 测试实例使用不存在的模型名，真实网关返回 HTTP 404 `model is not found`；正式 worker 自动入队 20 条真实内容，最终进入 `failed_eval`，可推荐数为 0。 |
| 在线修正 | 实际 TCP HTTP `PUT /api/config` 返回 202，apply-status 的 requested/applied revision 均为 1。 |
| 子进程替换 | `[54254, 54255, 54256, 54257] → [54641, 54642, 54643, 54644]`，约 3.22 秒完成；四个旧 PID 均已退出，主 API PID 保持 54252。 |
| 原候选恢复 | 新 discovery worker 记录 `revived 20 failed_eval candidate(s) for re-evaluation on startup`；复用同一数据库，20 条原候选回到 `pending_eval`，随后重新被 claim。 |

这部分证明修复确实把正确配置送到了真实后台子进程，能够恢复旧候选。它不能证明外部模型已经完成评估或可推荐池已经补满。

## 首轮：原主备模型链的失败证据

真实请求还暴露了本机模型服务的问题：

1. 主模型 `deepseek-v4-flash` 返回 HTTP 429，SDK 原始响应为 `429003 / inference exceeds tpm/rpm limit`。同一模型的 64、8192 token 输出预算探活成功，16384 token 输出预算探活被拒；正式文本批量评估使用 16384。不能仅凭这个对照断言账号的具体 TPM 上限，但可以确认简单探活不代表批量请求可用。
2. 恢复本机原有主备链 `deepseek-v4-flash → sensenova-6.8-flash-lite` 后，日志确认主模型失败时尝试备用模型。运行中还出现 `returned empty content with response_format=json_object`，之后的兼容重试遭遇限流。
3. 使用相同 20 条真实候选、真实画像和正式 `ContentDiscoveryEngine._evaluate_batch_once()` 单独复查备用模型。没有实验参数的原始生产请求约 30,161 字符、16384 token 输出预算、`json_object` 格式；连续三次实际返回 HTTP 500，原始响应为 `engine is not available temporarily / inference_error / 500`，总耗时 7.94 秒。临时尝试两种关闭思考参数也收到服务端 500，不能据此认定参数受支持，未修改生产 adapter。

首轮驱动在启动后 704.88 秒以 `AssertionError: real model did not produce an available pool` 失败：20 条候选仍在评估链路，可推荐数为 0，没有进入推荐文案或推荐返回验收。期间执行过恢复主备链和超时配置的受控重载，因此不能把这段时间写成某个固定配置连续运行 10 分钟的结果：测试最初把请求超时限制为 60 秒，在启动后 499.86 秒恢复本机原有的 1200 秒；总测试使用独立的外层截止时间。

首轮未完成候选入池、推荐 API 返回或消费后的自动补池，不能把进程重启成功、raw=20 或简单模型探活成功写成端到端通过。数据库中只有一笔成功的关键词规划调用（11,935 tokens），没有成功的正式评估或推荐文案调用。

## 二轮：可用模型完成真实闭环

通过正式 adapter 的 `list_models()` 从同一网关实际读取模型列表，选择其中的 `deepseek-flash`。先以相同 20 条内容和正式评估器作真实请求：约 34.68 秒返回完整正文，20 条全部可解析；正式多进程轮次中，这批旧候选实际得分为 0.05–0.45，按原阈值正常拒绝。随后由正式供给链路自动寻找新内容，全程没有修改画像、手工插入候选或绕过门槛。

下表时间相对于第二次实例启动：

| 时间 | 实际行为与断言 |
| --- | --- |
| +38.47 秒 | 实际 `PUT /api/config` 把模型切为 `deepseek-flash`，HTTP 202；requested/applied revision 均为 1。 |
| +39.59 秒 | 四个子进程 `[56890, 56891, 56892, 56893] → [56936, 56937, 56938, 56939]`，旧进程全部退出，主 API 保持 PID 56885。 |
| +84.62 秒 | 20 条旧候选全部低分拒绝，canonical available 仍为 0。 |
| +129.65 秒 | 自动补入 18 条新候选（12 条 search、6 条 related_chain），继续真实评估。 |
| +258.27 秒 | 3 条合格内容正式 admission，等待推荐文案，其余 35 条候选已因低分拒绝。 |
| +273.28 秒 | 推荐文案完成，canonical available=3、copy_ready=3、pending=0。 |
| +273.59 秒 | 实际 `POST /api/recommendations/append` 返回 HTTP 200 和 3 条真实视频及文案；数据库确认消费后 available=0。 |
| +288.61 秒 | 没有再修改配置或手工加候选，后台自动补入 15 条新候选。 |
| +378.65 秒 | 新内容评估、admission 和文案全部完成，available=1、copy_ready=1、pending=0；闭环断言通过。 |

第一批实际返回的内容标识为 `BV1toJR6HEVy`、`BV1rSpk6EEmr`、`BV1LJX3B9EP7`，分别带有非空推荐文案。通过时累计 53 条真实候选：4 条 cached，49 条 rejected_low_score；已 admission 内容得分为 0.62–0.84。低分内容没有为了测试通过而放行。

随后再次实际调用推荐 append，HTTP 200 返回补回的 `BV1koBjYpEpM`，文案长度为 158 个字符，与前三条不重复。停机前后台已经开始评估下一轮 11 条候选，所以停机快照的累计候选数为 64；53 是闭环断言通过当时的数量。

用量表确认 `deepseek-flash` 实际完成了 3 次 `discovery.evaluate_batch`（59,744 tokens）与 2 次 `recommendation.write_expression`（18,900 tokens）；关键词规划、搜索词生成和本地 embedding 也通过实际配置执行。这些是调用账本中的 token 计数，不代表供应商实际计费金额。

相同代码再次执行 `pytest tests/test_delegated_pool_recovery.py tests/test_proc_supervisor.py tests/test_api_config_transactional.py -q --tb=short`：**33 passed，32.56 秒**。没有为了真实测试修改生产代码。

## 修复与部署含义

- 代码修复解决“保存了正确配置，后台仍用旧配置”的进程边界问题；实际 HTTP 保存后，四个旧进程均退出，新进程重新消费持久候选。
- 模型必须能完成真实批量结构化评估和推荐文案，简单 `OK` 探活不足以验收。本机原主模型的 429 和备用模型的 500 是独立的服务端阻塞；本轮通过以实际可用模型替换测试实例中的路由解决。
- `deepseek-flash` 只在隔离实例中切换，生产配置未改写；其可用性是本账号、本次请求的实测结果，不保证用户其他账号或未来时刻相同。
- 小目标池验证的是从空池恢复和消费后补货，并未验收到达默认 300 条库存，也未验证 Windows 打包运行时。

## 本地证据

脱敏事件、请求元数据和运行脚本保存在忽略目录 `output/empty-pool-live-20261020/`。请求元数据只保留模型、输出预算、输入字符数、状态码、finish reason、响应长度及 token 计数，不保存或输出 Key、Cookie、画像或完整提示词。凭据只在私有临时运行目录中使用。

已确认测试主 API 和四个子进程全部退出，删除包含配置凭据、画像副本及原始日志的私有临时目录。保留的脱敏产物通过实际凭据字符串扫描，匹配数为 0；生产 `config.toml` 的修改时间早于首轮启动，没有被本轮测试改写。

## 合回 main 前的集成验证

与 `origin/main`（`cd928628`）合并时，仅变更日志顶部的并行新增条目冲突，保留双方记录；API 代码自动合并。合并后在独立 worktree 再跑配置、事务重载、CLI 子进程退出、打包入口、worker 降级/心跳及蓝鱼功能的定向回归：**639 passed、6 skipped，63.56 秒**。`ruff check src/ tests/ packaging/entry.py` 通过，`mypy src/` 的 **335 个源文件**通过。这里是针对合并交集的验证，不替代前文记录的全量测试与真实服务条件。

清理 worktree 前，已将 32 份脱敏证据及历史运行脚本复制到主工作区同名 `output/empty-pool-live-20261020/`，逐文件 SHA-256 校验一致；没有将这些本地产物加入 Git。
