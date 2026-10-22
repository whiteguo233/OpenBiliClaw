# Ego Lite 动态来源原生接入与真实验收

状态：**真实 Ego 聊天建源、定时采集、模型评估与入池通过；托管首次设置、无环境配置聊天和宿主重启恢复通过**。

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
| 官方宿主与首次设置 | PASS | 官方 macOS Intel 0.6.0.16；用户要求代为设置后，通过专用宿主的真实 `chrome://onboarding/` 页面逐步完成。跳过数据导入、newsletter/referral，取消默认浏览器、Dock 与崩溃报告；没有修改隐藏偏好或调用完成设置的内部快捷 API |
| 原生公开网页探测 | PASS | `/tmp/openbiliclaw-ego-host-20261022/public-spike-result.json`：真实 executor=ego、标题“哔哩轻小说_日本动漫轻小说在线阅读”、100 个 DOM 元素、PNG 截图。设置后调试端口 18508 关闭，正常原生宿主重启后仍成功 |
| 模型桥 | PASS，真实业务调用 | protocol 5 / native dynamicTools / gpt-6-luna / medium；初始零调用，后续真实作者、聊天工具、评估与文案调用另存 audit，不使用固定计划或评分 |
| 工具/CLI 路由与兼容 | PASS | 初轮 7 个完整文件 102 passed；收紧新建显式 HTTP 仅 Feed 后，4 个受影响完整文件 87 passed。日志 `/tmp/ego-wiring-native-class-regression.log` 与 `/tmp/ego-wiring-explicit-http-regression.log`；旧 HTTP/PW 更新保留执行器，缺失 Ego 不回退 |
| 设置界面 | PASS（自动化） | 本轮 162 WebJS、33 i18n / 3127 字串，Chrome/Firefox 构建及 22 项资源检查通过；prepare API/CLI 与相关生命周期 68 tests 通过。不是全部安装平台的 UI 验收 |
| 脚本打包 | PASS（wheel / 冻结收集契约） | 真实离线 wheel 的 11 个关键文件与本轮最终源码一致，包含原生脚本、托管安装/首次设置、服务、API/CLI 及共享设置界面；历史 67 项打包回归保留，本轮打包契约也在最终相关回归中通过。未完整构建桌面安装包，当前没有 PyInstaller |
| 安装说明与契约 | PASS | 31 项文档/安装器测试；注册审计 31 PASS / 17 N/A / 11 MANUAL / 0 MISSING，不等同真实网站验收 |
| 真实聊一聊建源 → 审批 | PASS | 全新 18506 服务，模型自主 prepare(executor=ego)→trial→真实审批卡；一次 approve 后创建独立来源。初次试跑的简介 DTO 漏投影已修，实际缺失的作者没有伪造 |
| 自然定时采集与去重 | PASS | 15:12:55–15:12:57 UTC 首轮真实 workflow v1 / executor=ego，10 discovered / 10 enqueued / 10 seen。15:17:57–15:17:59 第二轮，间隔 300.027 秒，10 发现/0 入队/seen 仍 10；未改时钟或提前 force，`natural-repeat-dedup.json` |
| 真实评估与入池 | PASS | 10 实际候选经真实 Luna 评估与文案，9 cached 且持久化 copy，1 条经既有 style cap 拒绝；admission_min_score 仍为 0.6。`real-model-eval-copy.json`、`real-candidate-scores.json`；平台库存 `{web:9,total:9}` |
| 推荐 API 与管理 | PASS | 正常 Web 范围 append 返回 9 张实际推荐，均保留同一 source_id、source_name=哔哩轻小说、source_platform=web；真实管理 API 修改份额 1→2→1、间隔 5 分钟并最终停用。`recommendations-web-append.json`、`management-real-api.json` |
| 托管安装与首次设置 | PASS（全新 profile，复用已下载官方 DMG） | 首次完整下载后 strict 校验发现官方包自带 FinderInfo；只清新副本两项元数据后 strict 与 Gatekeeper 均通过，958 个文件/链接内容与官方 DMG 相同。第二次网络重下载过慢被正常取消；将已有完整官方 DMG 放正式 installer.dmg 缓存，产品正常执行挂载、复制、签名检查、新 profile 官方 UI 设置、关闭调试口、正常重启与 native handshake，setup_state=ready/available=true。没有预填 profile 或成功标记、没有 mock 下载器/执行器。日志 `/tmp/openbiliclaw-managed-setup-20261022-cached.log`，缓存来源/hash 见该根 `installer-cache-evidence.json` |
| 托管按需启动与空间恢复 | PASS | 真实模型生成计划在首次运行和正常关闭宿主后的运行均 `ok / 10`，URL 集合相同；停机 `available=false`，自动重启后 true，`managed-native-restart.json`。初次复验曾因空空间丢失失败，原始失败保留为 `managed-native-restart-initial-failure.json`；修复只允许已确认托管宿主重启且旧 ID/名称均不存在时，在固定 profile 重建，不 claim 用户空间 |
| 无环境配置聊天、草稿恢复与审批 | PASS | 新产品实例 18509 未设置任何 `OPENBILICLAW_EGO_*`，初始来源/候选/推荐均为 0；真实 Luna 自主建草稿。首次试跑暴露原生 DOM reader 不支持相对子选择器，通用修复后正常重启；自然续聊未提供内部 ID，模型通过 `list_sources` 找回同一草稿并试跑成功，5 条真实样例均有简介，一次审批后新增 1 个独立 Ego 来源（1 份、5 分钟）。模型计划从失败到发布 SHA 始终为 `c2507cf43f5d683b889802aa625baca10d114b8a323c39788a9d4c32ea7ec366`，未手改选择器。此实例调度关闭，完整后续入池证据属于 18506 |
| 真实界面与持久化 | PASS（限定范围） | 官方 Ego 展示 9 张桌面推荐卡及来源管理；375px 移动推荐页无横向溢出且 9 张卡保留来源名。移动设置实际截图宽为 1200 CSS px，不宣称其 375px 响应式通过。服务正常重启后 sources/workflows/versions/runs/states/seen/candidate/cache 哈希全部一致，见 `ui/evidence.json`、`final-persistence-restart.json` |
| 测试资源关闭 | PASS | 18506/18507/18509、观察器和 native 模型桥正常退出；仅本轮两个独立 Ego 宿主发送正常终止信号，应用、profile 与证据保留。托管状态为 prepared/offline，下一次实际工作会启动。`managed-chat-runtime/cleanup-final.json`、托管根 `host-cleanup-final.json` |

此前提交 `a4dc8fe8` 的原生执行器定向回归为 **71 passed / 16 deselected**，日志
`/tmp/ego-executor-regression-20261022.log`；排除的 16 项是旧 Playwright 实机测试，
本轮没有通过它们替代 Ego 验收。测试覆盖请求与响应阶段、任务空间身份、私有状态、
超时清理、人工输入和分页登录判断；Node 原生 binding fixture 仍属于受控协议模拟。
独立代码复查发现的登录观察丢失、分页登录检查、填写假成功、关闭连接、输入焦点和
隐藏元素可见性问题均已修复。全局 Ruff 通过，MyPy 350 个源文件通过。
最终源码冻结后重新执行 `uv build --wheel --offline`，在
`/tmp/openbiliclaw-ego-final-wheel/evidence.json` 核对原生执行器、两个脚本、接线服务与
共享设置脚本共 5 个包内文件和当时源码一致。这些历史源码哈希不代表本轮托管实现。

本轮最终相关回归 **217 passed / 39 warnings**，覆盖原生协议、DOM 读取、托管安装、
管理 API/CLI、草稿恢复、Feed、来源生命周期、安装器与打包契约；
`/tmp/ego-managed-final-regression.log`。Ruff 通过，MyPy 352 个源文件通过，
851 个 Python 文件格式检查通过。另有 162 WebJS、33 i18n / 3127 字串、
Chrome/Firefox 构建和 22 项资源检查。实际 CLI `sources ego-status` 返回 ready，
`config-show` 正常退出；测试配置磁盘未保存临时模型桥密钥，因此该 CLI 的模型未配置提示
不代表实际注入模型的服务不可用。未运行全量 pytest，不把协议 fixture 当作真实网站证据。

最终离线 wheel：`/tmp/openbiliclaw-ego-managed-wheel-final/openbiliclaw-0.3.231-py3-none-any.whl`，
SHA-256 `43c943859b979dda14c0a1797f573d8410ec7de4fb8278a45dc9318c09894ce4`。
同目录 `evidence.json` 逐字节核对 11 个关键文件。安装失败缓存恢复另有 50 项定向回归
（含 6 项无窗口子进程检查，包含在上述最终回归范围内）；使用可控操作系统/下载 I/O，
验证坏签名与缺应用/CLI 后再次准备可重新下载，信任拒绝与临时错误不误报成功。

## 执行与数据隔离

命名宿主 `obc-discovery-spike-20261022` 使用独立
`/tmp/openbiliclaw-ego-host-20261022/profile`。应用只复用自己创建的 task space，
核对 ID、随机名称和 agent ownership；task space 是页面隔离，Cookie 属于选定 Ego
profile。状态 GET 不调用会打开首次设置的 CLI，也不把 `installed=true` 当作已连接。

原生桥实施精确 origin、允许方法与公共 DNS 预检；Ego 宿主负责最终 socket 解析，
因此明确 `dns_pinned=false`。不继承历史 Playwright 的 socket pin 声明。
首次设置、真实协议或网站失败均如实记录，不回退后再报告 Ego 成功。

此前阶段结束时，18506 尚未启动，模型桥曾在零请求下关闭；该历史记录保留在
`idle-shutdown.json`。本次在用户明确授权代为设置后完成首次设置，重启模型桥，
从全新业务数据库开始真正的 Ego 验收。`implementation-evidence.json` 属于此前实现快照，
后续改动与验证不能沿用它的源码 SHA。

实测还发现重启后模型无法从前轮工具结果恢复草稿 ID。新增
`WorkflowStore.pending_draft_summaries()`，由现有 `list_sources` 提供最近 10 条未应用草稿的
名称、网址、目标、状态与 ID，不暴露计划、会话或观察快照。数据库重开回归验证恢复，
安装失败草稿的 `trial` 也会重新执行前台托管准备；后台修复不会首次安装应用。
18506 真实验收的第三轮续聊曾提供前轮真实 draft ID 才继续，这一事实保留。
随后 18509 的独立无环境配置验收才验证了模型自主查找草稿，证据位于
`/tmp/openbiliclaw-managed-setup-20261022/managed-chat-runtime/managed-e2e-summary.json`，
以及同目录 `managed-chat-resume-02-events.jsonl`、`managed-real-approval.json`。
相对子选择器修复只改变固定 DOM reader：条目内的 `> ...` 加入 `:scope`，
`:scope` 自身可作为链接条目；所有真实模型规则保持原样。

## 验收边界

托管自动安装目前仅支持 macOS，本次实机为 Intel；没有验证 Apple Silicon 实机、
Windows/Linux 原生执行、完整冻结桌面安装包、外站真实账号登录或任意网站兼容性。
公开规则实际未采到作者，保留空值。模型桥 44 次成功传输不等于 44 次业务成功；
简介投影、空间重启和相对子选择器的原始失败均保留，没有合并成一次从未失败的记录。
