# 安装与部署阶段自动准备 Ego Lite

范围：`capability-increment`，只改变动态网页浏览器的准备时机。
基线 `e8c50271`，独立分支 `feat/dynamic-discovery-sources`；主工作区已有
`outputs/`、`promo/` 未跟踪内容保持原样，不修改生产配置或数据。

状态：部署接线、回归测试及真实服务自动触发已通过；本轮全新部署尚未到达
`ready`，不能作为完整安装验收通过或发布依据。改动仍在功能分支，未合入 main 或发布。

后续复验：[真实部署与聊天端到端记录](2026-10-23-ego-live-deployment-e2e.md)
已记录新的官方联网下载、首次设置失败恢复及真实业务结果。本页保持原轮次的
FAIL / NOT_RUN，不将后续成功回填成首次通过。

## 行为契约

- macOS 源码 bootstrap 在依赖安装后自动调用安装目标运行时的 `sources prepare-ego`，
  `--skip-start` 不跳过该依赖准备；实际数据目录由目标运行时配置解析。
  重跑源码安装器仍验证原生握手，可能启动已准备但离线的宿主。
- `start` / `serve-api` 和桌面安装后首次启动显式启用主 API 的部署准备。
  下载、签名校验、官方首次设置和原生握手使用同一个 `EgoSetupManager`，
  在 API 拥有的后台任务中进行，HTTP 与设置页不等待下载。
- macOS 正式 DMG 是拖拽应用，没有安装后执行钩子；自动准备发生在安装后首次启动，
  不在构建机器、挂载 DMG 时或签名 bundle 内执行。
- 服务启动检查发现已准备且应用仍存在时直接跳过，离线宿主不会仅因此被打开。
  失败后下次服务启动自动重试，同进程不自动循环；设置按钮和 CLI 可显式重试。
- app factory 默认关闭准备，只有实际部署入口启用；自检、重复实例、worker 和只读 GET
  不会开始安装。退出 API 会取消并等待其尚未完成的准备任务。
- 不同安装渠道保持同一用户与配置/数据根；外部 `OPENBILICLAW_EGO_*` 宿主不被修改。
  当前原生适配仅支持 macOS；Linux Docker 和 Windows 跳过并报告限制，Feed 仍可使用。

## 验证记录

部署接线相关 Python 回归 **460 passed / 2 skipped / 26 warnings**，
`/tmp/ego-deployment-combined-regression.log`。其中两项跳过为 Windows 专用桌面测试；
不据此宣称 Windows 原生 Ego 通过。源码 bootstrap 和安装文档契约另有
**115 passed**，`/tmp/ego-install-deployment-regression.log`。
全局 Ruff、格式检查通过；注册审计 31 PASS / 17 N/A / 11 MANUAL / 0 MISSING，
它只证明接线，不代表网站或安装环境真实通过。
MyPy 352 个源文件通过。关闭顺序及端口判定修复的最终定向回归为
**56 passed**，`/tmp/ego-stop-port-recovery-green.log`；覆盖主 PID 先退出、
已消失 PID 的恢复、端口持续开放、连接超时/未知错误和进程拒绝退出，
只有明确的连接拒绝才确认端口关闭。这些测试与前述范围有重叠，不能相加当作全量测试。

真实首次部署暴露一个关闭顺序问题：Ego 主 PID 消失后，旧实现只检查一次调试端口，
而没有继续使用剩余退出预算，报 `ego_setup_debug_port_still_open`。首轮约 4.75 秒
健康接口即返回 200，约 48.80 秒进入官方 onboarding，约 71.55 秒在 restart 阶段失败；
随后只读检查显示端口已关闭且本轮宿主进程均已消失。原始记录保留在
`/tmp/openbiliclaw-ego-startup-20261023/first-observations.jsonl` 和
`failure-native-state.json`；不把原失败记录覆盖成成功。API 始终可用。
随后失败 profile 的正常服务重启在系统信任校验阶段遇到约 90 秒 `spctl` 超时，
记录为 `ego_setup_timeout`，没有绕过或放宽系统校验；这轮不作为关闭顺序修复通过的证据。

最终冻结源码另从不存在应用、profile、setup 状态或数据库的隔离目录启动真实 CLI。
部署准备自动触发，HTTP 健康接口保持 200；应用复制命令 `ditto` 达到既有 120 秒预算，
最终约 228.67 秒记录 `phase=install / ego_setup_timeout / installed=false`。
当时磁盘剩余约 127 GiB，系统 load average 为 267.79 / 292.13 / 161.28；
高负载是观察到的条件，尚未证明是超时的唯一原因。没有延长预算、移除 quarantine、
绕过系统信任检查或手工写入成功状态，也没有继续重复尝试。

| 验收项 | 结果 | 证据边界 |
| --- | --- | --- |
| 源码安装、API、桌面部署接线 | PASS | 上述自动化回归覆盖；桌面入口测试不等同实际冻结包 |
| 无聊天、无 prepare 请求的真实服务自动触发 | PASS | 实际 `serve-api --host 127.0.0.1 --port 18511`，零模型调用、零业务网站请求 |
| 准备期间 API 可用及失败状态可见 | PASS | 三轮健康接口均持续返回 200；真实错误投影保留 |
| 新环境自动准备至原生握手 ready | FAIL | 首轮关闭时序问题已修复并定向验证；后两轮分别在信任校验、复制阶段超时 |
| 已 ready 且离线的真实部署重启复用 | NOT_RUN | 本轮缺少成功 ready 前置；仅有自动化测试覆盖跳过准备分支 |
| 最终 wheel 构建与源码一致性 | PASS | 离线构建成功，9 个关键模块/原生脚本逐字节匹配 |
| macOS 冻结桌面包的完整安装运行 | NOT_RUN | 未执行完整 PyInstaller/签名 DMG 构建与安装验收 |
| Windows / Linux 原生 Ego | UNSUPPORTED | 当前适配明确跳过，不用 Feed、Docker 或 macOS 结果替代 |

真实试验汇总为
`/tmp/openbiliclaw-ego-startup-fresh-20261023/startup-acceptance-summary.json`。
最终试验只复用官方完整 DMG 缓存（164216514 字节，SHA-256
`4a91f88ddcbb5bf76ed77682b2059f02a96b24c8e3fbef95434c794692656a91`），
没有复制现成应用、profile 或就绪标记；本轮不证明重新联网下载成功。
第二、三轮记录正常服务关闭，18511 端口、本轮拥有的 Ego 进程与挂载均已清理；
首轮观察进程退出一并终止 CLI，不宣称首轮正常关闭。其他用户进程未修改。

最终 wheel 为
`/tmp/openbiliclaw-ego-deployment-wheel-final/openbiliclaw-0.3.231-py3-none-any.whl`，
SHA-256 `a62528e2920e28918a94b0168e25d65c6e2b745811daf101dc00a87371174250`。
逐文件比对与独立安装入口源码哈希见
`/tmp/ego-deployment-wheel-final-verification.json`；源码安装脚本及桌面入口不在 wheel 内，
其测试和哈希单列，不能把 wheel 成功称为桌面安装包成功。

此前网站聊天、10 条发现/9 条入池、定时去重及完整原生链路见
[上一轮验收](2026-10-22-ego-lite-discovery-e2e.md)。
本轮不把上一轮聊天准备误称为安装阶段自动准备。
