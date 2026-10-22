# 推荐候选池持续为零：诊断与恢复验证

## 日志证据与结论边界

用户提供的 Windows 主日志覆盖 2026-07-05 至 2026-10-19。直接对应症状的片段：

- 2026-09-27 22:55:01：`no servable pool candidates (raw=126 pending=126)`，维护记录 `available=0->0`。
- 2026-09-28 17:39:56：供给队列已有 `pending=426 evaluating=60`，不能把空池简单归因于完全抓不到来源内容。
- 同期存在模型仅输出 reasoning、限流，以及 `Background LLM calls reached ... (max 120)`。这些会阻止评估/文案完成，raw 数量不等于可推荐库存。
- 2026-10-18 多次 `Config hot-reload succeeded` 与模型切换只出现在主 API；正在运行的后台进程没有配置重载或重启通道。

已复现的代码缺陷是配置修复不传播到子进程：主 API 换为可用模型后，discovery worker 的 provider、预算与暂停状态仍来自启动快照。这能让候选长期停在评估/文案前。主日志没有 10 月后台子进程的完整错误，故不能断言这一个缺陷解释用户机器上所有空池时段；历史限流、预算耗尽与模型配置错误仍须按实际状态处理。

## 修复

完整 API handoff 成功后递增 app-owned generation；父 supervisor 在配置应用队列结束后停止并重启后台子进程。仅聊天 loop 局部更新不重启。所有退出必须由 terminate/wait 或 kill/wait 确认，避免两个 evaluator claim owner 并发；停止失败保持原进程并重试。旧候选不清库，复用现有持久队列与启动恢复。

桌面、源码 `start` 与 Docker `serve-api` 使用同一 supervisor。子进程通过内部 `OPENBILICLAW_RUNTIME_DATA_DIR` 保留父进程已锁定的数据目录，磁盘待生效目录仍需完整退出重开。没有新增依赖、安装器步骤、CLI 命令或用户 TOML 字段。

## 验证

先写并运行 `test_supervisor_restarts_running_workers_after_applied_config_changes`：修复前断言失败，子进程仍只加载 `broken-model`；修复后加载序列为 `broken-model → working-model`，旧 PID 已退出。

`tests/test_delegated_pool_recovery.py` 启动真实 Python 子进程，使用真实 SQLite、`DiscoveryCandidatePipeline`、`ContentDiscoveryEngine` 和推荐文案 drain，仅模型 I/O 使用确定性替身：

1. 故障模型：一条候选保持 `pending_eval`，canonical `available=0`。
2. 通过真实 `PUT /api/config` 保存可用模型，等待 apply-status 成功；supervisor 读取 API 发布的 generation，确认旧进程退出后启动新进程。
3. 同一持久候选变成 `cached`，补齐推荐文案后 canonical `available=1`。

另覆盖配置排队时不重启、合并修订只切换一次、不能结束旧 owner 时禁止启动新 owner、数据目录仍绑定父进程锁、API 成功发布 generation / 构造失败不发布。

检查结果：

- 全量 `pytest -q --tb=short`：10,519 passed，65 skipped，1 failed，耗时 2,210.98 秒。唯一失败为 `test_api_auth_env_vars_matches_loader_read_surface`：全量进程已导入旧行号的函数后，本轮源码新增环境变量分支，`inspect.getsource()` 按旧 code-object 行号读取了更新后的文件，得到错误的源码片段。鉴权函数与白名单本身未修改；不将此轮描述为单次全绿。
- 停止修改源码后，用新进程执行 `pytest tests/test_config.py::test_api_auth_env_vars_matches_loader_read_surface tests/test_config.py tests/test_proc_supervisor.py tests/test_delegated_pool_recovery.py tests/test_api_config_transactional.py -q`：401 passed，包含上述失败项、完整配置组及实际 API → 子进程 → 可推荐池恢复；鉴权读取面检查正常。
- `pytest tests/test_delegated_pool_recovery.py tests/test_proc_supervisor.py tests/test_api_config_transactional.py -q`：31 passed（随后新增的 CLI 两模式接线也包含在下一组）。
- `pytest tests/test_proc_supervisor.py tests/test_config.py tests/test_cli_sigterm.py tests/test_packaging_entry.py tests/test_cli_child_stdio.py -q`：449 passed，2 skipped。
- `ruff check src/ tests/ packaging/entry.py`、修改文件的 `ruff format --check`：通过。
- `mypy src/`：324 个 source files 通过。
- 使用隔离临时配置执行 `openbiliclaw config-show`：exit 0，Provider 和数据目录显示正常，测试凭据未明文输出。

后续已补充本机真实 B 站、云端模型、Ollama 与多进程 HTTP 验收：原主备模型出现 429/500；隔离实例切到实际可用模型后完成可推荐数 `0 → 3 → 消费后 0 → 自动补回 1`。完整失败与成功条件见 [真实请求验收](2026-10-21-empty-pool-live-requests.md)。真实用户 Windows 安装包和其外部模型账号仍未在本机复测；配置切换会短暂重启后台服务，主 API 保持运行。

## 旧版本临时恢复

确认评估与推荐文案使用的模型可调用，保存配置后从系统托盘完整退出应用并重新打开，使后台进程重新读配置；只刷新网页不能更新旧 worker。若日志仍提示限流/预算耗尽，先恢复 provider 或等待预算窗口，不清空候选数据库、不绕过评分和文案门槛。需要进一步定位时收集 `desktop.log`（源码/Docker 为 `child-discovery-worker.log`）及故障当时的 runtime-status。
