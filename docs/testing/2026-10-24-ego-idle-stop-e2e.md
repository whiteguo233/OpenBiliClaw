# Ego 托管宿主空闲回收与真实端到端验收（2026-10-24）

本补丁修复一个真实事故：一次 Ego 端到端 spike 结束后，`ego lite` 浏览器与一个
100% CPU 的渲染进程继续驻留 **46 核小时**，直到人工发现。根因不是页面脚本死循环，
而是集成只关闭任务空间里的 tab，从不停止托管浏览器本体；异常路径下 tab 关闭也是
尽力而为，孤儿 tab 会随常驻浏览器一直渲染。

## 行为变更

- **跨进程会话租约**：每个工作流会话在启动宿主前持有 `sessions.lock` 的共享
  `flock`，最后一个会话结束时释放。空闲回收必须拿到排他租约才能停止宿主，因此
  不会误停另一个 API/worker 进程正在使用的会话。
- **空闲回收**：最后一个会话关闭后保留默认 120 秒保温窗口
  （`OPENBILICLAW_EGO_IDLE_STOP_SECONDS` 可覆盖），随后停止托管宿主并清理
  `setup.json` 中的 `pid`。下一次工作流按既有 `ensure_running()` 冷启动，任务空间
  与登录 profile 保留在磁盘上。
- **关闭加固**：`Target.closeTarget` 未确认时缩短保温窗口并立即回收；SIGTERM 在
  6 秒内未退出时，仅对已通过可执行文件、server name、user-data-dir 三重校验的
  PID 升级 SIGKILL，绝不按过期 PID 猜测。解释器退出时以同样的排他租约兜底停止。
- **宿主 close 回执**：修复 `ego_host.mjs` 在回包前先关闭 socket 的顺序问题。此前
  每次关闭都会被判为失败，导致保温窗口失效并产生固定告警。
- **边界**：显式 `OPENBILICLAW_EGO_*` 外部宿主仍完全由用户管理，不安装、不停止、
  不回收；只有本应用安装的托管宿主适用上述策略。

## 验证证据

### 单元/回归

- `pytest tests/test_ego_native_executor.py tests/test_ego_host_lifecycle.py
  tests/test_ego_workflow_wiring.py tests/test_ego_packaging.py
  tests/test_ego_collect_dom.py tests/test_ego_managed_wiring.py
  tests/test_ego_setup.py tests/test_ego_setup_surfaces.py
  tests/test_custom_workflow_lifecycle.py -q`：**190 passed**。
- 新增 `tests/test_ego_host_lifecycle.py` 7 项：共享租约互斥、末次会话触发回收、
  并发会话阻止回收、外部宿主永不回收、SIGKILL 升级、未初始化/外部停止为空操作、
  关闭失败时保温窗口归零。
- 原“宿主不退出也不升级”测试按新策略改为“先 SIGTERM、超时后对同一已验证 PID
  升级 SIGKILL，仍失败则保留状态并报错”。
- `ruff check` 与 `mypy`（改动模块）通过。

### 真实环境端到端（真实请求）

- 官方 `egolite-fHoqgZ74bOEM.dmg`（x64，164,369,902 字节）完整下载、签名/系统信任
  校验、安装并通过既有 onboarding 自动化走完 9 步首次设置；真实版本 **0.6.0.20**。
  安装根 `/tmp/obc-ego-e2e-20261024`，未触碰生产配置与数据。
- 目标站点 `https://www.solidot.org/`（真实公网 GET）。工作流：
  `ul.comment_on > li` 取标题与链接，`max_items=5`、单页。结果 **5 条真实条目**，
  耗时 4.5 秒，首条为 `https://www.solidot.org/story?sid=85692`
  （“浏览器 Arc 将于 12 月关闭”）。
- 采集结束后宿主保持存活（保温窗口生效），4 秒空闲后进程与全部渲染进程退出，
  `setup.json` 中 `pid` 清空而 `initialized` 保留；随后 `ensure_running()` 再次拉起
  成功，最终 `stop()` 后无残留 `--ego-server-name=obc-*` 进程。
- 复现脚本 `/tmp/obc-ego-e2e-20261024/e2e.py`，原始日志同目录。

### 静默后台运行（真实窗口检查）

- 托管宿主正常启动改为 `--no-startup-window`，首次设置仍用可见的调试窗口；
  启动后 WindowServer 中没有任何 ego 窗口。
- 非交互会话创建 tab 后立即通过 CDP `Browser.setWindowBounds(..., minimized)`
  收回窗口，关闭 tab 前再收回一次，避免最后一个 tab 关闭后留下空窗口。
- 交互式登录接管不最小化：真实检查中窗口可见，截图 305,201 字节成功；关闭后窗口
  立即消失。最小化窗口下 `Page.captureScreenshot` 会报 "Cannot capture a
  screenshot while the window is minimized"，因此该策略只用于非交互路径，登录
  截图链路不受影响。
- 真实采集全程以 10 Hz 采样 WindowServer 可见窗口：0 次命中；采集、空闲回收、
  重启复用均正常。另以 50 Hz 采样系统前台应用，全程仍是 Chrome，后台采集不抢焦点。
  交互窗口可见但不主动激活，登录由产品截图/操作接口驱动。
- 同一会话内页面导航、`document.readyState`、DOM 采集在最小化窗口中工作正常。

### 边界

- 本轮只验证一个真实站点与托管宿主生命周期，未重跑完整产品聊天建源、模型评估与
  三端展示；不据此声明整条业务链路零故障。
- 外部宿主模式下的孤儿 tab 仍可能残留（按契约由用户管理），本补丁只保证托管宿主
  不再长期驻留。
