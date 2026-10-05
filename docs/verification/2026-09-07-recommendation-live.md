# 真实环境推荐链路验收（2026-09-07）

这是对[隔离测试报告](2026-09-07-recommendation-consistency.md)的后续真实验收。
使用原配置、原 SQLite 数据库、真实后台 discovery / full worker / recommendation / image 进程、真实 HTTP 和 WebSocket。没有假 HTTP、合成候选或手工修改库存。
运行源码来自 `fix/recommendation-consistency` worktree；`OPENBILICLAW_PROJECT_ROOT` 显式指向原工作区，确保配置、凭据和数据库仍属于原实例。main 源码未修改，微信项目的服务也未操作。

## 真测发现与补修

初版修复并没有保证真实响应速度。第一轮真实请求只有前两笔完成（约 1.68 / 3.10 秒），随后换批和健康检查出现 15 秒读取超时。
Python 栈采样确认 `/api/activity-feed` 在主 API 事件循环直接执行 `get_runtime_status()`，反复扫描真实候选历史与 temporal eligibility，阻塞了换批代理和其它请求。

补修包括：

1. 活动动态计算整体移入工作线程，并使用异步锁合并并发相同缓存请求。回归测试在诊断函数被阻塞时实际请求健康接口，验证事件循环仍响应，并确认两个动态请求只计算一次。
2. 同一次隔离 SQLite 读取事务中的推荐阈值仅计算一次。真实数据约有 5,500 条已评分历史参与边界计算；候选、库存与平台补位原来会重复扫描。缓存仅属于当前事务，关闭时清空，下一次请求仍读取最新提交；提交阶段仍重查资格。没有恢复跨请求陈旧候选快照。

## HTTP / WebSocket / 数据库结果

| 场景 | 结果与耗时 |
| --- | --- |
| 初版真实请求 | 出现读取超时，不能验收为稳定。见[原始结果](results/recommendation-live-initial.json)。 |
| 活动动态移出主线程后，编译及测试并行负载 | 连续 12 次换批/追加全部成功，120 条唯一；中位数 6.10 秒，P95/最大 8.42 秒。健康探测 P95 71.9ms，最大 2.19 秒。见[结果](results/recommendation-live-busy.json)。 |
| 加上事务内去重计算，停止全量 API 测试和设备编译后的常规批次 | 连续 6 次全部成功，60 条唯一；单次 1.17–1.92 秒，中位数 1.40 秒，P95/最大 1.92 秒。健康探测 53 次，最大 69.3ms。见[结果](results/recommendation-live-final.json)。 |
| X 平台定向换批及追加 | 先返回 9 条、再追加 1 条，约 609 / 521ms；只返回 X，来源余量最终归零，总量与来源图一致。此轮只有一次健康探测，耗时约 2.99 秒，不能把上一轮的 69ms 当作全程健康探测上限。见[结果](results/recommendation-live-scoped.json)。 |

每个成功批次检查：真实 ID > 0、批内和该轮批间无重复、响应前 recommendation/shown 已写入原数据库、总量等于各来源余量之和、随后真实库存查询一致，以及主 API WebSocket 收到与响应版本/来源图完全匹配的库存事件。

后台一直在真实补货，库存不会严格只减不增。例如高负载轮中发生新入池；X 清空后再次请求又取到 3 条新补内容（约 3.40 秒），所以不能声称本轮完成了真实空响应测试。空响应暂停/恢复仍由既有单元回归覆盖。

两轮使用的库存规模和机器负载不同，不是严格 A/B 性能实验。常规样本只有 6 笔，不能据此保证任何网络、长期运行或高负载下都小于 2 秒。

## 客户端真实交互

- 手机 Web：Chromium 390×844 访问真实 `/m/`，点击换批、滚轮触发底部追加。观察到 loading 按钮后恢复，卡片 10→20，总库存更新；换批与追加按钮均可继续操作。没有前端脚本异常。控制台另有服务重启期间的 WebSocket 断连，以及过期小红书封面/非图片 X 视频 URL 的图片代理失败，未阻塞卡片和按钮恢复。
- iOS 26.5 的 iPhone 17 Pro 模拟器：运行生产 `RecommendView`、真实 providers、真实 ApiClient、真实 HTTP/WebSocket，两次换批和实际 fling 追加通过；没有 mock transport，不修改已保存的连接设置，不执行点赞/收藏/发消息。最终轮 UI 完成时间约 5.67 / 2.64 秒，fling 到新增卡片约 8.45 秒（包含手势滚动、布局和调试运行开销，不能等同于单次 HTTP 耗时）。两轮原生测试均验证了总数/来源图一致、真实 ID、无重复和追加后 10→20。
- 实体 iPhone（iOS 18.5）：签名与设备编译成功，但无线 Dart VM 调试连接未建立。`flutter test` 提示无线设备需发布调试端口；切换 `flutter drive --publish-port` 后仍无法发现调试服务。已提示用户解锁并允许本地网络或连接 USB。本项没有端到端通过证据。

## 可重复执行

以下命令会实际消费推荐池，需显式选择运行。JSON 结果只保留计数、耗时与库存事件，不导出推荐正文或凭据。

```bash
python scripts/verify_recommendation_live.py --requests 6 \
  --database /absolute/path/to/data/openbiliclaw.db \
  --output /tmp/recommendation-live.json
```

可加 `--source-platform twitter` 验证平台作用域；`--database` 仅使用 SQLite 只读连接检查已提交记录。
原生入口为配套 mobile 仓库 `integration_test/recommendation_consistency_live_test.dart`；无线实体设备使用 `test_driver/recommendation_live.dart` 与 `flutter drive --publish-port --dart-define=LIVE_BACKEND_HOST=<电脑局域网地址>`。

## 补修回归

- `tests/test_api_app.py`：604 passed，包含活动动态阻塞/合并回归。
- 存储阈值、平台快照等专项：9 passed。
- 存储 + 推荐引擎 + Web 回归：336 passed、1 failed；失败的 expression 部分结果测试单独复跑通过（1 passed）。没有将此组合执行写成一次全绿。
- `ruff check src/ tests/ scripts/verify_recommendation_live.py` 及 `git diff --check` 通过。
- `mypy src/` 仍是原有 10 个错误、3 个文件，没有新增。
- mobile `flutter analyze` 无问题；原有 82 项单元测试结果见上一报告。

## 现场状态

测试后本机 `8420` 服务保留运行修复分支，配置和数据目录仍是原实例；真实 `/m/` 资源逐字节校验与修复分支相符，健康状态为 ok。两个 main 工作区保持干净，尚未合并 main；以后从旧 main 手动启动仍会加载旧代码。
实体 iPhone 已成功安装以 `lib/main.dart` 为入口的正常 Release App（66.4MB），替换临时测试入口，保留应用数据；安装成功不等于实体交互测试通过。


## 合并验证（2026-09-08）

在修复分支合入 main 的 `9adf48b1` 评论字段修复后，重新运行 `pytest tests/test_bilibili_api.py tests/test_api_app.py -q`：638 项通过，2148 个警告，约 158.5 秒；退出时另有 pytest 历史临时目录清理警告。`ruff check src/ tests/ scripts/verify_recommendation_live.py` 和 `git diff --check` 通过。`mypy src/` 仍为既有 3 个文件中的 10 个基线错误，无新增类型错误。

移动端 `64d859f` 包含此前推荐一致性修复与评论时间插值修复，已快进合入并推送该仓库 main；其静态检查无问题，82 项测试通过，正常入口 iOS release 包已安装到配对手机。评论页面尚未完成实体机截图复核。

原工作树的本地配置、测试数据、日志及截图保留在主工作区忽略目录 `output/verification/2026-09-08-recommendation-cleanup/`，不会提交或推送；原测试过程的分支运行描述保留为历史记录。
