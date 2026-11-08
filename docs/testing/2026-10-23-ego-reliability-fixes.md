# Ego 同次安装恢复与聊天采集上限修复

范围为 `capability-increment`：修复部署准备需要人工重启、以及对话中的采集上限未落实。
本记录接续[此前失败与恢复记录](2026-10-23-ego-live-deployment-e2e.md)，不改写历史失败。
基线 `eec66afb949e2e6269f4521c29028c52e45a5971`，分支 `feat/dynamic-discovery-sources`。

## 实现边界

- 安装下载失败自动重试；官方 CDN 返回强 ETag 和范围支持时四段并行下载，严格验证
  响应范围、实体身份与长度。只有完整文件才能缓存，不接受预分配文件长度作为完成证据。
- 仅对幂等系统命令的超时重试，预算逐次为基础值的 1/2/3 倍；实际签名或信任拒绝仍失败。
  设置动作回执超时先验证关闭自己的宿主，再观察当前步骤续接，不盲目重放点击。
  每个恢复点最多三次尝试、间隔 1/3 秒，安装最多 1200 秒，首次设置与连接另有独立 300 秒；取消清理不吞掉。
- 聊天 dispatch 独立绑定用户原话，提取常见中英文明确条数／页数上限，与模型 intent
  合并后持久化 `scope_limits`。编译后收紧计划，试采、审批、正式运行使用同一预算；
  更新、登录恢复、后台重编制保留约束。Feed 使用相同规则，确认卡显示真实预算。
- 未增加配置开关、依赖或网站权限。Linux/Docker、Windows 原生 Ego 能力未扩大。
  不声称任意自然语言都能准确解析，不回写旧版本预算。

## 诊断与回归证据

临时证据目录 `/tmp/obc-ego-reliability-20261023`，仅本机保留，不提交凭据或原始模型记录。
最小红灯为安装信任检查超时后没有同次恢复、用户 10 条而模型生成 20 条，共 2 项失败。
第一组组合回归 190 项通过后，真实 RSS 聊天暴露模型把上限从工具 intent 省略的问题：
`live` 诊断实例的草稿仍为 20，因此未批准；随后新增原话 ContextVar 约束及对应红绿测试。
旧单流下载的诊断运行在 711 秒约 58 MB 时主动取消，明确不作为最终安装失败或成功。

最终相关回归覆盖安装/部署接线、范围下载错误与取消、聊天隔离、Feed、工作流更新和生命周期：
初版 367 项通过（133.45 秒）；加入 producer 修复预算和分阶段超时后，最终 370 项通过（122.69 秒，`verified-regression.log`）。
MyPy 检查 352 个源文件通过，Ruff 与 `git diff --check` 通过。

## 真实环境

第一轮完整冷安装实例 `/tmp/obc-ego-reliability-20261023/final`，真实 CLI `serve-api` 监听 18512。
配置、数据库、应用、installer 缓存和 profile 全新，无 `OPENBILICLAW_EGO_*`，没有复制
应用或写成功标记。首次健康 HTTP 200 在 42.26 秒返回，下载在后台继续。
模型由已授权官方 Codex app-server 的真实 `gpt-6-luna / medium` 经协议桥 18513 调用；
不固定工具参数、采集计划或评分。embedding 为本机 Ollama `nomic-embed-text`。
画像为合成轻小说偏好，所有来源写入、审批和配置只作用于隔离实例。

真实 RSS 聊天请求阮一峰 Atom，每轮最多 2 条、每 5 分钟、份额 1。
草稿 `c770718f-f64a-4a72-8fc5-b1dc6142cc80` 试采成功，审批
`ap_555a587f16e14f6a` 摘要显示 `2 条，1 页`，经正式审批 API 启用。
来源 `custom_027a367a4cf74038b78a08928d48cc33` 首轮自然调度
`9cd0f63a-ddb5-4460-919e-949e7f8083e2` 为 2 发现 / 2 入队，执行器 http。

## 验收台账

| 检查 | 适用性 | 状态 | 证据 |
| --- | --- | --- | --- |
| 安装瞬态恢复、预算、取消、签名拒绝 | required | PASS | `verified-regression.log` |
| 用户上限、模型遗漏、并发隔离、Feed、修复继承 | required | PASS | `verified-regression.log` |
| 全新官方下载至原生 ready，无人工重启 | required | PASS | `verified/fresh-ready.json`、`verified/cold-install-proof.json` |
| 真实 RSS 聊天、审批与定时采集 2 条 | required | PASS | `final/rss-cap-events.jsonl`、`final/observations.jsonl` |
| 真实 Ego 聊天、审批与受限定时采集、评估入池 | required | PASS | `verified/trial-proof.json`、`published-proof.json`、`business-pool-proof.json` |
| 当前源码 wheel 打包完整性 | required | PASS | `verified/wheel-proof.json`，六个改动源模块逐字节一致 |
| 冻结桌面安装包分发、Linux 原生 Ego | N/A | NOT_RUN | 本次不构建或发布桌面安装包，不新增平台支持 |

本轮尚未合并、安装到生产实例或发布。

全量 `pytest -q` 扩展检查在 656 通过、1 失败时中断定位；失败为
`test_put_config_does_not_block_on_speculator` 的 0.5 秒超时断言。
单测复跑失败；使用 `git archive HEAD src` 提取的未修改基线代码再次复跑同样失败，
堆栈确认来自 `baseline-source/src`。不改动无关接口或放宽测试，不宣称全量通过。
日志为 `full-pytest.log`、`config-latency-rerun.log`、`config-latency-baseline.log`。

`uv build --wheel --offline` 成功；wheel SHA-256
`e6700cc99d0cabf04d0ede80268f8843f22e6106f92e43354ba1257685c23a42`（`verified/wheel-proof.json`）。
此项为源码 wheel 构建／内容校验，不冒充冻结桌面 DMG 的安装验收。
`openbiliclaw config-show` 使用隔离实例配置执行通过。

第一轮冷安装真实捕获新的红灯：官方下载完成且 hash 正确，应用签名／系统信任通过，
首次设置依次完成步骤 0–8，但共享 1200 秒预算在原生握手阶段耗尽。服务从头至尾未重启，
最终失败记录为 `final/cold-install-failed.json`。这不是通过。
据此改为安装 1200 秒、初始化／连接另有独立 300 秒，防止慢下载挤掉收尾时间；
新增预算隔离和连接超时清理测试，安装测试共 71 项通过。

最终修正版在全新 `/tmp/obc-ego-reliability-20261023/verified` 再次从官方下载，
`verified/baseline.json` 证明应用、profile、安装缓存均不存在。以该实例终态为最终冷安装证据。

最终修正版冷安装 **PASS**：启动至 `ready/available=true` 为 625.39 秒，首次 HTTP
就绪为 26.57 秒。官方 164216514 字节包 SHA-256 为
`4a91f88ddcbb5bf76ed77682b2059f02a96b24c8e3fbef95434c794692656a91`。
首次设置实际遇到两次瞬态响应超时，16:26:54 和 16:27:04 自动停止自有宿主并重新观察；
第三次完整经过步骤 0–8，16:27:59 原生握手成功，Ego 0.6.0.16。
后端 PID 与最初启动记录一致，未调用 prepare API、未人工重启、未重写成功标记。
最终 `debug_port=0`，宿主参数没有调试开关，已记录的两个临时端口均拒绝连接。

最终 Ego 聊天试采：真实模型直接调用 `prepare_discovery_source(executor=ego)`，
草稿 `67a7f519-a042-4431-8885-b8b47107bd24` 保存 `scope_limits={max_items:3,max_pages:1}`，
真实试采取得《亚妖精物语》《作者不详：推理作家的读本》《黄金神威 鹤见笃四郎的宿愿》，
书名、作品链接和简介分开，未读取章节。计划预算同为 3/1，SHA-256
`9070f57bb355085726d9d30731e9bc558256fd7d66adbb2b345e7f839830cf84`。
工具有界展示两个样例，持久草稿有三条；不以展示样例数替代正式预算。
审批 `ap_cd85a7d36f8d4a4a` 显示真实上限、五分钟周期和份额 1。

真实 Ego 产品 UI 的“待审批”窗口批准后，来源
`custom_4933834e053b405ba68899cb805558bd` 启用，workflow
`323c1ffa-db70-4b51-895e-063a2ce70596` version 1 的计划哈希与试采完全相同。
首轮自然调度 `d75c3099-0d26-4f28-ae81-b31703200a21`：3 发现 / 3 入队，executor=ego。
真实 Luna 评估后 3 cached，推荐缓存 3 fresh，3 条文案全部生成，来源 attribution 正确。
没有注入候选、预设评分、强制采集或修改时钟。

设置页 `verified/ui/round-12.txt` 显示独立来源、Ego Lite 已就绪、占比 1、周期 5 分钟、
最近成功和下次发现时间，以及名称／占比／周期编辑、启停、运行记录和归档入口。
本次 UI 验证没有再次修改比例；该操作的既有真实验收见前一记录。
部分自动化选择器命中折叠或带徽标的控件失败，重新观察后经可见“待审批”弹窗完成；
后续试图聚焦来源名称截图时 Ego 的 `Page.getFrameTree` 超时，未取得截图，未计作截图通过。
保留各轮原始错误，以已完成的 UI 审批、设置 snapshot 和独立数据库证据作结论。

第二轮自然调度 `d983f9d7-f732-412b-abea-3f504d6fcab8` 于首轮完成约 300 秒后启动，
仍为 3 发现、0 入队；seen 与缓存均保持 3，证明持久预算与去重正常。
最终证据为 `verified/business-final-evidence.json`。
RSS 的后续两轮均为 `not_modified`、0 入队，2 条候选经真实模型低分拒绝，未冒充入池。

收尾：UI task space 2 已 `finish({keep:[]})` 成功。测试来源通过正式管理 API 暂停，
隔离后端、模型桥、观察器正常停止，测试目录与证据保留；专用 Ego 宿主也已关闭，状态为 ready/offline，不改动生产配置或日常浏览器。
本次声明为上述修复范围完成，不扩大到冻结安装包分发或所有网站可采集。
