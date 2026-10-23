# 原生来源登录会话验收（2026-10-24）

配套 Flutter 分支 `feat/native-dynamic-sources`。原生聊天、审批、来源设置和 Ego 画面均由 Flutter 页面完成；网页执行器仍是后端托管 Ego Lite，手机不安装执行器。本补丁基于 `9634b6e2`，仅增加人工窗口释放及同来源活跃登录草稿复用，不修改调度、份额、登录凭据或审批语义。

## 接口行为

- `POST /api/custom-sources/{id}/login`：同来源已有活跃的既有流程登录窗口时返回同一草稿。不会复用尚待审批的流程修改草稿。
- `POST /api/custom-sources/drafts/{id}/close`：幂等释放窗口和到期计时器，未知或已关闭窗口同样成功；不试跑、不发布、不注销网站。
- Flutter 页面退出主动调用关闭；退出与接管请求并发时，接管返回后再次关闭，避免晚到请求遗留窗口。强制终止或断网由已有 15 分钟到期机制兜底，重新登录可复用活跃窗口。

这是现有路由内部资源生命周期变更，没有新增模块、依赖、CLI、配置字段或安装步骤。公开契约见 [API 文档](../modules/api.md)。

## 验证证据

- 真实 iPhone 17 Pro / iOS 26.5 模拟器，隔离后端 `localhost:18612`，NGA 来源使用 Ego。原生完整聊天流程此前已完成 NGA 与 Atom 建源、审批、份额修改、调度采集；Atom 候选真实评分后入池。
- 原生登录补验：同来源重复登录返回同一活跃草稿；页面退出后原截图请求返回 409；重新进入得到新草稿并正常打开 Ego，继续试跑成功。没有使用真实 NGA 账号，不将公开页试跑算作账号登录验收。
- 相关后端回归 112 项通过：workflow lifecycle/concurrency/update、source approvals、Ego native executor/wiring。
- `ruff check src/ tests/` 通过；`mypy src/` 352 文件通过。
- 后端全量 `pytest -q` 运行 6 分 5 秒、975 项通过后主动中止，以完成本次接口相关回归；**全量套件未跑完，不声明全量通过**。
- Flutter 全部 159 项测试通过，静态检查 0 问题，Android Debug APK 构建通过。Android/iPhone 真机及真实 Tailnet 网络未进行本轮端到端测试。

本机原始日志 `/tmp/obc-native-live-20261024/`；Flutter 详细记录和截图位于客户端仓库 `docs/native-custom-sources-live-20261024.md`。原始模型请求含测试画像，不提交。使用隔离测试数据，测试来源完成后停用。
