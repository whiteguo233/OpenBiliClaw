# Ego Lite 动态来源原生接入与真实验收

状态：**进行中，真实网站端到端尚未通过**。

用户要求普通网页必须使用 Ego Lite。此前
[哔哩轻小说验收](2026-10-22-bilinovel-chat-e2e.md)使用 Playwright，其结果保留为历史证据，
不能代表本次 Ego 验收通过。

## 本轮边界

- 复用 `feat/dynamic-discovery-sources` 独立 worktree；不修改生产配置或数据。
- 新普通网页 `auto/ego` 使用官方 `ego-browser nodejs` 与原生 `ego.sendCDPMessage`。
  不使用 Playwright 连接、浏览器替身或 `executor_factory` 注入。
- RSS / Atom / JSON Feed 经实际解析后可使用 HTTP；旧持久流程更新默认保留原执行器。
- 哔哩轻小说只读公开书籍元数据，不下载小说章节、输入账号凭据或导入个人浏览器数据。
- 新运行根 `/tmp/dynamic-source-bilinovel-ego-20261022`、后端端口 `18506`；
  原生 Luna 请求桥 `/tmp/dynamic-source-ego-luna-bridge-20261022`、端口 `18507`。
  不复用上轮数据库、来源、选择器、候选、评分或推荐文案。
- 用户已授权的真实 `gpt-6-luna / medium` 通过官方 Codex app-server 的 native dynamicTools
  接收产品工具；临时桥仅做协议适配，不生成固定计划、工具结果或评分。

## 当前证据

| 检查 | 结果 | 证据与限制 |
| --- | --- | --- |
| 官方宿主 | 已安装并启动，未完成首次设置 | 官方 `citrolabs/ego-lite`、macOS Intel 0.6.0.16，独立 user-data-dir；CLI 返回需要完成首次设置，不能操作网页 |
| 人工首次设置 | 待用户完成 | 官方安装指南要求等待用户确认完成 onboarding；已请求用户跳过个人浏览器数据导入和默认浏览器设置，没有修改隐藏偏好绕过 |
| 模型桥就绪 | PASS，无业务调用 | `initial-readiness.json`：protocol 5 / native dynamicTools / gpt-6-luna / medium，各调用计数为 0 |
| 工具/CLI 路由与兼容 | PASS | 初轮 7 个完整文件 102 passed；收紧新建显式 HTTP 仅 Feed 后，4 个受影响完整文件 87 passed。日志 `/tmp/ego-wiring-native-class-regression.log` 与 `/tmp/ego-wiring-explicit-http-regression.log`；旧 HTTP/PW 更新保留执行器，缺失 Ego 不回退 |
| 设置界面 | PASS（自动化） | 158 WebJS、33 i18n 测试；三语状态与入口，Chrome/Firefox 构建及 22 项资源检查通过；不是实际 Ego UI 验收 |
| 脚本打包 | PASS（wheel / 冻结收集契约） | 真实离线 wheel 包含 ego_executor.py 与两个原生 .mjs，内容和构建时源码一致；67 项打包回归通过。未完整构建桌面安装包，当前没有 PyInstaller |
| 安装说明与契约 | PASS | 31 项文档/安装器测试；注册审计 31 PASS / 17 N/A / 11 MANUAL / 0 MISSING，不等同真实网站验收 |
| 原生 CDP 协议 | 未验收 | 必须真实验证 task space、页面、request/response 拦截、DOM、截图、输入及关闭恢复 |
| 真实聊一聊建源 → 启用 → 周期采集 → 入池 | 未验收 | 必须在全新隔离服务中操作，保存 workflow.executor=ego 与真实模型/原生执行证据 |

原生执行器定向回归为 **71 passed / 16 deselected**，日志
`/tmp/ego-executor-regression-20261022.log`；排除的 16 项是旧 Playwright 实机测试，
本轮没有通过它们替代 Ego 验收。测试覆盖请求与响应阶段、任务空间身份、私有状态、
超时清理、人工输入和分页登录判断；Node 原生 binding fixture 仍属于受控协议模拟。
独立代码复查发现的登录观察丢失、分页登录检查、填写假成功、关闭连接、输入焦点和
隐藏元素可见性问题均已修复。全局 Ruff 通过，MyPy 350 个源文件通过。
最终源码冻结后重新执行 `uv build --wheel --offline`，在
`/tmp/openbiliclaw-ego-final-wheel/evidence.json` 核对原生执行器、两个脚本、接线服务与
共享设置脚本共 5 个包内文件和最终源码一致。未运行全量 pytest；本轮结果限定为上述
受影响测试、原生协议 fixture、UI/打包/安装契约和全局静态检查。

## 执行与数据隔离

命名宿主 `obc-discovery-spike-20261022` 使用独立
`/tmp/openbiliclaw-ego-host-20261022/profile`。应用只复用自己创建的 task space，
核对 ID、随机名称和 agent ownership；task space 是页面隔离，Cookie 属于选定 Ego
profile。状态 GET 不调用会打开首次设置的 CLI，也不把 `installed=true` 当作已连接。

原生桥实施精确 origin、允许方法与公共 DNS 预检；Ego 宿主负责最终 socket 解析，
因此明确 `dns_pinned=false`。不继承历史 Playwright 的 socket pin 声明。
首次设置、真实协议或网站失败均如实记录，不回退后再报告 Ego 成功。

收尾时未启动产品 `18506`，运行根仍没有来源或候选数据库。模型桥 `18507` 在零请求
状态下正常关闭，`idle-shutdown.json` 记录所有计数为 0、进程退出且端口关闭；不计入
真实模型业务验收。保留隔离 Ego 宿主及设置窗口，等待用户完成首次设置。

恢复入口为 `/tmp/openbiliclaw-ego-host-20261022/public-spike.py`：只做公开页面的真实
Ego DOM/截图探测，且先完成首次设置；成功后再启动全新产品与 Luna 桥跑聊天建源。
`implementation-evidence.json` 保存原生实现冻结时的文件 SHA 与官方版本信息。

## 待完成

完成官方首次设置后，先运行真实原生协议探测，再通过产品聊天添加
`https://www.bilinovel.com/`、审查试跑样例并批准；随后验证独立份额、定期发现、
去重、真实评估与文案入池、三端显示和设置管理。最终补充实际计数、运行版本、
测试结果、重启复用与清理情况，再判断本轮是否通过。
