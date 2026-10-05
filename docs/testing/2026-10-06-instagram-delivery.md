# Instagram main 集成交付（2026-10-06）

## 范围

本轮按用户要求提交、合并 main、推送并更新 README / 项目首页，不 bump 版本、不打 tag、不上传商店、不切换生产服务。来源整体仍为 **incremental only**，不是完整验收通过。

- Instagram 修复提交：`c642ec58`，首次接入为 `6975e9e9`。
- main 整合基线：`fcb170bc`（v0.3.226），相对原分支增加 471 个提交；保留 GitHub 来源、时效准入、Safari 守卫、聊天与 Tailnet 等主线改动。
- 解决共享注册表、任务恢复、失败提示、图片网络边界与文档冲突；Instagram 补接主线共享发布日期偏好（TOML → API → 桌面/插件 → 候选准入）。
- 手机仓库 `485269f` 已合并并推送 main：显式启用的消费测试、README 和验收说明；临时测试包名没有提交。主工作区原有改动的二进制 diff 哈希在合并前后相同，未混入提交。

## 检查与证据

Python 使用独立 worktree 的 `PYTHONPATH=src`，已核对 `openbiliclaw.__file__`；不拿共享 editable venv 的 main 导入结果作为分支证据。

| 检查 | 结果 |
| --- | --- |
| Ruff / MyPy | PASS，309 个 Python 源文件类型检查 |
| 来源注册审计 | 34 PASS / 14 MANUAL / 10 N/A / 0 MISSING；不等于全部真实门禁通过 |
| 共享来源指标 | 6/6 PASS |
| 版本一致性 | 后端/插件保持 0.3.226，无新版本发布 |
| 合并定向回归 | 183 PASS（来源契约、日期准入、CLI、图片代理安全边界） |
| 后端全量首轮 | 10,093 PASS / 65 SKIP / 13 FAIL，44 分 35 秒；不是一次全绿 |
| 首轮失败最终复测 | 13/13 PASS；CLI 输入、状态缓存隔离、共享来源数量、初始化请求字段与首页断言已修正 |
| 最终增量检查 | 184 PASS（含新增日期 API / UI / 准入检查）；配置模块 361 PASS；首页最终 5 PASS |
| 扩展全量 | 1,671 PASS；原先一项依赖固定源码截取长度的断言已改按函数边界截取 |
| Chrome / Firefox | TypeScript、构建与各 21 项 manifest 资源检查 PASS |
| 原生 mobile | `flutter analyze --no-pub` 无问题；`flutter test --no-pub` 139 PASS |
| 首页 | 真实浏览器中英切换、Instagram 卡片及验收链接可见；390px 中英文无横向溢出 |

首轮启动后发生过源码/测试修订，因此收集的是旧测试对象；其中源码反射检查也因运行期间行号变化失败，最终独立复测和配置全模块均通过。以上分开记录首轮与最终复测，不将增量复测描述为重新完成一次完整全绿运行。

原始日志位于本机 `/tmp/openbiliclaw-instagram-delivery-20261005.8UTOz8`。私有归档目录 `/Users/white/workspace/.task-archives/instagram-delivery-20261006.PRs9nP` 已保存任务配置、数据、截图和历史真实请求证据，权限为仅当前用户可访问；不提交这些原始私有目录。README 中英版本与项目首页均明确标注 main 源码增量、实验性、默认关闭与未发布状态。

## 不随合并消失的限制

真实上游测试的安装产物与计数以 [Instagram 台账](../platform-source-acceptance.instagram.md) 各日期记录为准。本轮合并后的构建、单测和本地 UI 检查不是又一次 Instagram / LLM 真站全链路证明，也不宣称现有安装包包含此 main 增量。

- Likes 非空末页缺少可信终止信号，保留 `partial/progress_stalled`。
- 第二账号和 Firefox 登录态没有完成验收；Firefox 匿名路径已有单独证据。
- iOS 仅模拟器内生产推荐组件的集成 harness，未覆盖完整 App 启动设置、Android/手机真机、Instagram App 唤起及真机视频播放。
- 跨端本地保存落库与刷新后状态一致；已打开 PC 页面不会立即响应其它端的取消操作。
- 生产使用仍须确认 Meta 自动采集许可；网络波动仍可能导致真实任务失败。

Clash Verge、共享代理节点、日常后端和其他任务的 worktree 均不在本轮清理范围。
