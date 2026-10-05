# 推荐页惊喜队列读取阻塞（2026-09-08）

## 现场与定位

用户反馈合并提速后手机刷一屏仍慢。检查端口 8420 的 API 进程，运行目录为 main，版本 `ca97e41b`，包含优化提交 `0ae807ce`。局域网客户端最近一次请求附近记录多样性排序 45 候选、51.1ms，事件循环恢复延迟 0.3ms。没有该次请求的完整历史耗时，不能将后续复现等同于用户那一次。

在同一真实实例测试，独立换批返回 10 条为 554.6ms，追加 10 条为 377.5ms，HTTP / WebSocket / SQLite 已提交库存一致。并发 `/api/health` 用了 8081.6ms；该端点包含外部 embedding 就绪探测，不能据此判断主事件循环阻塞，后续改用无数据库、无服务商请求的 `/api/ping`。

同时请求手机页面的 runtime-status、activity-feed、sources/status、delight/pending-batch、platform-availability 时：runtime-status 3532.8ms、activity-feed 3613.9ms，ping 首次 1198.1ms，后续 3.9–62.0ms。

逐项隔离：

| 单独发起的接口 | 接口耗时 | 发起后 30ms 并发 ping 耗时 |
| --- | ---: | ---: |
| ping 基准 | 8.8ms | 3.1ms |
| delight/pending-batch 第一次 | 232.4ms | 203.6ms |
| runtime-status | 1418.3ms | 4.3ms |
| activity-feed | 1310.1ms | 6.0ms |
| delight/pending-batch 第二次 | 270.6ms | 240.8ms |

`pending_delight_batch` 原为 async 路由，内部没有 await，却同步计算动态阈值、查询历史和读取不喜欢主题。这会占住主 API 事件循环，拖住正在进出的推荐代理请求。runtime-status 和 activity-feed 本次隔离测试虽耗时更长，但读取已在线程中执行，未同样阻塞 ping。

## 修复与验证

将该接口声明为同步路由，交给 FastAPI 线程池执行完整函数。推荐引擎、候选数量、动态阈值、过滤顺序、liked/delivered 规则、文案和返回字段均不改；不引入库存或队列结果缓存。

新增回归分别把阈值读取和候选查询卡住，验证它们在线程中执行、ping 可先完成、查询完成后队列过滤及 liked 状态保留。旧代码两种场景均失败，修复后通过；包含现有队列与活动动态测试的定向验证共 9 项通过。


完整 API 与移动端推荐一致性回归：**607 passed**（161.26s），Ruff 与 diff check 通过；MyPy 保留基线已有的 10 项错误（3 个文件），无新增错误。

同配置、同数据库、完整后台进程加载修复后，三次惊喜队列读取为 386.6 / 403.7 / 361.8ms；并发 ping 为 **8.6 / 5.8 / 5.8ms**。测试期间仍有回归与后台负载，队列自身绝对耗时不与之前直接做速度比较；确认的是其读取不再阻塞主 API。真实换批返回 10 条耗时 858.0ms，HTTP 库存、同版本 WebSocket 与后续 GET 一致，推荐 ID / shown 均已落库，验证失败列表为空。这些仍是本机请求样本，不是手机网络和渲染时间。

原始聚合结果：[隔离读取](results/delight-isolation-after.json)、[页面并发读取](results/delight-isolation-concurrent-after.json)、[真实换批](results/delight-isolation-serve.json)。上述验收在 `fix/delight-read-isolation` 分支完成，代码修复提交为 `db9667ab`。合并收尾将此修复完整纳入 main，并从 main 以原配置、原数据库启动完整服务；确认 ping、惊喜队列与库存接口正常后清理本次分支及 worktree。此前推荐排序优化完整保留。
