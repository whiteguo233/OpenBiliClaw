# 换批、库存与移动端续页修复验收

日期：2026-09-07。后端基线 `44f4736e`，原生 Flutter 基线 `ff37675`。
两个仓库均使用独立 `fix/recommendation-consistency` 分支；原生提交 `40452bc`。
本次没有合入 main、发布安装包或重启用户正在运行的服务。

## 已修复行为

- 推荐进程从当前 SQLite 快照取候选，完整排序后原子提交推荐历史与 shown，再返回真实推荐 ID；不再依赖延迟 worker 快照和异步 outbox 消费。
- 换批/追加响应携带同一次精确查询的总量、各来源余量及读取版本。主 API 转发独立推荐进程的库存事件，并在有订阅者时每两秒检查后台补货变化。
- 手机 Web、桌面 Web、扩展和 Flutter 直接应用响应库存；版本/查询代次防止旧库存响应覆盖新值。桌面顶部总数与来源标签共用快照。
- 手机 Web 请求正文也受超时保护；换批失败保留卡片并恢复按钮。追加后即使库存事件重建了头部，换批按钮也恢复可用。
- 自动续页重新检查已可见的哨兵，空批暂停、实际库存增加后恢复，重复相同正库存不触发空页循环。
- Flutter 保留手动重试，过期零库存不再阻断刷新/追加；加载失败解除 busy。冷却结束、补货和组件重建重新检查续页条件，手指松开后的惯性滚动继续保留手势意图。

接口与数据流见 [API 模块](../modules/api.md)、[推荐模块](../modules/recommendation.md)、[运行时模块](../modules/runtime.md)、[手机 Web](../mobile-web-spec.md)。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 后端全量 `pytest -q --disable-warnings --tb=short` | 初次 8927 passed、102 skipped、11 failed，耗时 21 分钟。失败逐项在 main 复跑，其中 10 项原已失败；3 项响应契约断言（含 2 项原有失败）同步后通过，剩余 8 项属于基线问题，见下表。未将局部复跑冒称为第二次全量通过。 |
| 最终引擎、outbox、快照、worker status、Web、API 精确库存及跨进程代理回归 | 199 passed、1 deselected；排除的是下表已有失败的桌面对话静态测试。 |
| API 空池/定向短批/代理/库存相关补充复跑 | 7 passed、596 deselected。 |
| `node --test tests/js/mobile-recommend-consistency.test.mjs` | 8 passed，涵盖正文超时、底部意图、库存乱序、重复库存、空页暂停和按钮恢复。 |
| `ruff check src/ tests/` / `git diff --check` | 通过。 |
| `mypy src/` | 10 个错误、3 个文件；全部可在原 main 的 15 个错误中找到，无新增类型错误。本次修正了时钟 Callable 注解等 5 个原有错误。 |
| 扩展 `npm test` | 1458 passed、1 failed；同一个既有 durable chat 请求形状断言在 main 复现。 |
| 扩展 `npm run typecheck` / `npm run build` | 通过，构建 19 个入口资产。 |
| Flutter `flutter analyze` / `flutter test --no-pub --reporter expanded` | 静态检查无问题，82 passed。 |

全量测试剩余基线失败：

| 测试位置 | 原有失败 |
| --- | --- |
| `test_api_weibo.py::test_weibo_share_count_survives_recommendation_and_delight_http_serialization` | FakeDatabase 不接受 `include_delivered`。 |
| `test_desktop_web_pool_status.py::test_desktop_inline_poll_checks_failed_before_stale_reply` | 静态断言仍寻找旧聊天轮询逻辑。 |
| `test_dialogue_reply_scheduler.py::test_every_production_dialogue_respond_call_is_behind_stable_lease` | 对话调用静态检查失败。 |
| `test_discovery_engine.py::test_text_batch_evaluation_bounds_declared_output_tokens` | 输出 token 上限断言失败。 |
| `test_image_fetch.py` 中 4 项并发/优先级/关闭测试 | 等待条件未到达；原 main 的同组复跑同样失败。 |

浏览器使用 Chromium 加载本分支真实 `/m/` 与 `/web/` 资产，配套隔离的内存 API stub，未消费用户的真实推荐池。手机视口 390×844 下，换批/追加后数量依次 30→26→22，追加后由 4 张增至 8 张卡片；桌面换批后总数与 GitHub 来源标签同时为 14。stub 未提供 WebSocket，连接失败日志属于测试环境限制；真实事件桥接由 ASGI 回归验证。Flutter 尚未安装到实体 Android/iOS 手机。

## 性能与限制

300 条合成候选、真实 SQLite、完整排序、连续 20 次取 10 条并查询精确库存：200 条返回内容全部唯一，剩余库存准确为 100。单次耗时中位数 97.7ms，P95 214.2ms，最大 268.9ms。使用测试画像/模型替身，没有接入外部 embedding 服务；该结果不能替代真实手机网络、生产候选分布和运行时负载下的验收。推荐热路径使用预热文案和缓存 embedding，缓存缺失不会在线请求 embedding 提供商。

12 秒客户端截止时间用于退出失败请求并恢复可操作状态，并不承诺所有网络环境下都能在该时间内成功。客户端断开时，已开始的后端数据库提交仍可能完成；本次不新增请求幂等重放协议。

遗留 outbox 以不可变批文件逐批确认，新批次不会被旧批次清理误删，持久化失败保留重试。升级须将旧 API 与 worker 一起重启，避免混用仍往旧单文件追加的进程。后端库存协议为 additive，旧客户端仍可读取推荐；原生端完整体验需要更新配套客户端。
