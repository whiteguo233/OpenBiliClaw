# 哔哩轻小说聊天建源验收（2026-10-22）

目标为 discovery-only：从聊一聊自然语言请求开始，为 `https://www.bilinovel.com/`
创建独立动态来源，验证试跑、用户确认、定时采集、真实模型评估入池与推荐卡、配置管理。
仅采集公开书目元数据（标题、作者、简介、书目链接），不读取章节正文。

## 环境与证据边界

- 独立 worktree `feat/dynamic-discovery-sources`；起点 `c05c1a9f`，浏览器修复 `4acb6f43`。
- 隔离服务 `127.0.0.1:18496`，独立配置、数据库与合成轻小说兴趣画像；起始来源和候选均为零。
  未导入生产账号、浏览器 Cookie、历史或私有画像，未更改生产服务。
- 用户授权使用真实 Codex GPT-6 Luna。临时 `127.0.0.1:18497/v1` 转接官方本机 CLI；
  所有模型请求指定 `gpt-6-luna`，不提供固定工具调用、候选、分数或推荐文案。
- 运行证据根 `/tmp/dynamic-source-bilinovel-luna-20261022/`；
  模型桥证据根 `/tmp/dynamic-source-luna-bridge-20261022/`；
  UI 证据 `output/playwright/bilinovel-luna-live/`（本机忽略目录，不提交浏览器资料）。
- 前轮 RSS/Atom/JSON Feed 验证见[动态来源续建验收](2026-10-22-dynamic-sources-completion.md)。
  本轮不将那次入队成功、真实评分拒绝或预算耗尽表述为最终推荐成功。

## 真实故障与修复

默认浏览器在独立只读探测中先出现超时，再返回 Cloudflare 拦截页；普通完整 Chromium
可以读取公开书目。只改变执行器的浏览器文件路径，保留全部 DNS pin、代理禁用、精确
origin、只读请求及资源预算后，同一产品观察路径返回正常站点标题和 100 条 DOM 事实。

根因是就绪检查检查完整 Chromium，默认无界面启动却由 Playwright 另选 headless shell。
修复让实际启动使用就绪检查返回的文件；显式路径继续优先，不放宽网络或页面权限。
修复后的默认产品代码已独立读取公开页面，此探测不是聊天建源或推荐成功的证据。

| 验证 | 结果 |
| --- | --- |
| 新增“完整 Chromium 就绪、另一个 headless shell 不存在”回归 | 修复前 1 failed |
| 执行器与安装器两完整文件 | 24 passed |
| 编译、生命周期、更新、并发四完整文件 | 74 passed |
| 全局 Ruff、修改文件格式 | 通过 |
| 全局 MyPy | 349 个源码文件通过 |

日志 `/tmp/bilinovel-browser-selection-red.log`、`/tmp/bilinovel-browser-selection-regression.log`、
`/tmp/bilinovel-workflow-boundary-regression.log`、`/tmp/bilinovel-browser-selection-static.log`；
详细探测索引 `browser-runtime-diagnosis.json`。没有重复宣称本次全量 pytest 一次全绿。

临时 Luna 文本工具桥的前两次真实 UI 请求都包含完整 23 个工具，但模型回复无可用工具，
没有 prepare 调用、来源或候选。原失败分别保留在 `first-chat-failure.json`、
`second-chat-failure.json` 和 UI 截图中。通用单工具协议探测成功不能替代业务聊天成功。

临时桥改为官方 `app-server` 原生 `dynamicTools`：注册应用原有工具 schema，
收到 `item/tool/call` 后交给产品执行，保留待响应的原 JSON-RPC ID；只在产品返回真实
工具结果后续同一个模型 thread/turn。父工具等待期间释放模型请求槽，允许试跑内部
嵌套调用模型。真实随机值读取工具往返、嵌套模型请求、结束后 pending/session 归零已
通过，证据 `native-roundtrip-probe.json`。该通用探测也不替代下面的实际产品链路。

## 端到端结果

真实聊天创建来源并审批后，完整产品管线完成了阳性推荐验证。没有预放内容或评分，也没有修改原有准入门槛。

| 阶段 | 真实结果 |
| --- | --- |
| 自然聊天与工具 | 原生动态工具由 Luna 自主选择；`prepare_discovery_source` 生成持久流程并真实试跑，`activate_discovery_source` 产生确认卡 |
| 人工确认 | 13:16:28.113 UTC 在桌面点击批准；0 → 1 来源，独立 1 份、5 分钟，匿名 Playwright、固定发现 |
| 首轮正式采集 | 13:16:29–13:16:35，12 条发现 / 12 条入队 / 12 个 seen 键 |
| 正常评分 | 原门槛 0.60，原聚合等待 90 秒；12 条实际评分后 7 条 cached / 5 条 rejected，未强制通过 |
| 推荐文案 | 7 条真实模型文案落库，库存 0 → 7 |
| 正常展示 | 13:20:18 单次“加载更多推荐”返回 7 张卡；UI 视觉核对来源角标“哔哩轻小说”和真实作品链接 |
| 追加后补货 | append 自动唤醒既有补货：12 条发现 / 0 入队；这是手动消费触发，不能记作自然周期 |
| 自然到期重复执行 | 13:25:59–13:26:07，12 条发现 / 0 入队；seen 仍 12，没有重复库存或追加评估 |
| 重启持久化 | 暂停调度、正常关闭并加载平台筛选修复后，来源、流程、版本、运行、状态、seen、候选身份/状态和 cache 九类哈希全部一致 |
| 三端显示 | 桌面 7 张普通卡；375px 移动端、420px 临时安装扩展各显示同 7 张普通卡及 1 个惊喜展示，来源名正确、没有横向溢出；惊喜展示不另计为第 8 条入池内容 |
| 管理与停用 | UI 实际保存份额 1 → 2 → 1，间隔始终 5 分钟；最后停用，三次 PATCH 200 及 fresh GET 确认 |
| 最终重启 | 调度关闭、来源停用状态下正常重启，九类持久状态再次全部一致，未重新采集或评分 |

来源为 `custom_fba49ce64be147a1afb2f9fe9a97e26d`，流程为
`d1b33ad4-847f-4017-9670-def837c322b0` v1，审批为 `ap_482c8fb1aecc4eb4`。
模型实际生成选择器后由产品执行；无人手写或替换采集规则。12 条内容均为书名、52–55 字
公开简介和作品链接，没有章节正文。原始评分为
`0.25, 0.64, 0.56, 0.81, 0.59, 0.43, 0.74, 0.77, 0.35, 0.78, 0.73, 0.70`；
现有题材多样性上限另将本已低于门槛的 0.56 条目置零，不能把原始模型数组等同最终持久评分。

真实 evaluator 请求为 `chatcmpl-luna-580c1452049047248d99f7420dc8bde8`（12.752 秒），
copy 为 `chatcmpl-luna-3d4ff5190a0946ef90497a21c0e7c9b9`（13.149 秒）。两者原生响应
均回显 `gpt-6-luna` / `openai`，实际输入包含轻小说、异世界、校园青春兴趣。
`bilinovel-real-eval-copy-evidence.json` 保存原始结构化返回、请求哈希与用量。
这证明官方登录通道实际完成模型调用，不主张独立验证远端模型实现。

## 元数据与更新边界

首版生成流程的 `description_selector` 有效，但 `author_selector` 为空，12 条作者均为空。
试跑摘要 DTO 不包含完整简介字段，因此模型在确认前也明确提示不能确认作者/简介。
真实数据库证明简介存在，作者确实未采集，不能把“作者不详”标题或缺省值当作者成功。

13:31:59 通过原聊天自然要求补齐真实作者，并明确更新原流程、保持 1 份/5 分钟。
模型先读取来源，再以正确 `source_id`、`workflow_id` 准备更新；服务端将草稿绑定到 `base_version=1`。
实际页面在原浏览器预算内超时，草稿 `6b4626f9-9bef-42ee-bc75-aaae0025abc7`
返回 `failed / Website did not load before the browser timeout`，零样例、无审批。
旧来源与 v1 完整保留；未盲重试、未自动发布失败流程、未补造作者。作者完整性仍是此站验收边界。

## 平台库存与筛选修复

真实 UI 暴露了库存 Tab 显示裸 `custom_*` 的问题。后端同样把该实例归属用于公开平台
筛选，而 API 仅接受 canonical 平台，导致即使显示 Web 也无法读取动态来源内容。
修复仅将公开库存聚合和平台筛选统一到内容的 canonical 族；Web 包含旧 recipe 和动态实例。
内部配额、实例精确读取、来源名、canonical 内容键及跨来源 URL 去重保持不变。

重启后真实页面为“全部 0 / Web 0”，已加载 7 张卡仍能按 Web 筛选，裸实例 ID 消失。
这里的 0 是七张卡已经被正常展示消费的真实状态；修复后的正数聚合与 API Web 续页由下列
真实存储/API 集成回归验证，没有把已展示卡片重新置回库存。

| 回归 | 结果 |
| --- | --- |
| 新平台范围三项（两来源、重复 URL、API） | 修复前 3 failed，修复后 3 passed |
| 存储/引擎/custom 五完整文件 | 224 passed |
| 平台 API 子集 | 11 passed / 617 deselected |
| Web JavaScript 全套 | 154 passed，含新增 4 项自定义网站筛选行为 |
| 桌面 load-more / pool 合约 | 42 passed |
| scope、文档、安装/打包契约组合 | 161 passed / 2 skipped（Windows-only named mutex） |
| 最终全局 Ruff / 格式 / MyPy | 通过 / 840 文件格式通过 / 349 源码文件类型检查通过 |
| 独立语义复核 | 未发现可操作问题；核对 canonical 平台范围、内部实例读取、旧策略前缀与配额不变 |

各组有重叠，不相加为全量数。前轮全量的 11169 passed / 72 skipped / 5 failed 及最终对应文件
复跑结果仍在[续建验收](2026-10-22-dynamic-sources-completion.md#全量回归与失败归因)单列；
本轮没有另跑或声称最终整库一次全绿。新修复不增加配置、CLI、依赖或运行模块，也不改变架构接线。

## 临时模型桥的限制与诊断

实际成功路径使用官方 CLI `app-server` 原生动态工具，固定 `gpt-6-luna / medium`。
`thread/start` 回显 read-only sandbox、`networkAccess=false`、空 instruction sources；
应用工具通过宿主执行，模型侧不能运行本机命令或自行访问网站。app-server 仍读取官方 CLI
登录及部分用户配置，因此不宣称完全忽略用户配置。此临时桥不是仓库依赖或正式安装组件，
也不主张与 Chat Completions 的每个温度/输出预算参数完全等价。

原生桥首次业务调用曾因默认 64 KiB stdout 行上限拒绝大 DOM 通知，产品保留真实 502。
修复为有界 8 MiB 并显式记录 reader 错误，确定性 200,086 字节通知复现旧失败/新成功；
同一实际 author 输入 85,467 字节、通知 96,022 字节随后成功。完整输入未截断，工具和业务
结果没有重写。`native-line-limit-product-evidence.json` 保留失败与成功请求的对应关系。

## 证据索引

运行目录内：`first-producer-run.json`、`first-candidate-metadata.json`、
`real-evaluation-and-copy.json`、`real-evaluation-scores.json`、
`append-triggered-repeat-dedup.json`、`natural-repeat-dedup.json`、
`platform-fix-restart.json`、`author-update-trial-failure.json` 和 `author-update-chat.json`。
UI 截图与完整 append 响应位于前述本机证据目录。
回归日志为 `/tmp/dynamic-source-platform-*-regression.log`、
`/tmp/bilinovel-webjs-final.log`、`/tmp/bilinovel-desktop-filter-tests.log`、
`/tmp/bilinovel-final-scope-docs-packaging.log`、`/tmp/bilinovel-final-global-*.log`。

本轮证明匿名书目发现和推荐，不覆盖真实账号登录、网站内写入、Ego Lite 宿主桥、Windows
安装态或任意网站可用性；正常网页访问仍可能因站点挑战或超时失败。

## 构建与清理

`uv build --wheel --offline` 成功；新 wheel 中本轮浏览器执行器、存储与引擎三个关键文件
和最终源码逐字节一致，证据 `final-wheel-provenance.json`。wheel SHA-256 为
`c46a47525d30a8048b7453c72717bf7d31983d064427d8bf89f250f8c798ae3e`。
先前 `python -m build` 因环境未装 build 模块退出，随后使用现有 uv 缓存离线构建，未安装依赖。
这不是新一轮 Docker/Windows 安装态验证。

管理结束后，`final-restart-persistence.json` 确认停用状态与九类哈希保留。第一次清理重启
脚本遇到临时模型桥已退出，随后一次重复监听尝试正常退出；核对实际监听 PID 后再正常
关闭和重启，未修改任何业务数据。该脚本失误记录于 `final-restart-harness-attempt.json`，
不当作产品缺陷或成功启动证据。

13:40 UTC 完成隔离服务的正常 application shutdown，停止本任务 observer；三个临时
浏览器此前均正常关闭。18496/18497/18498 已无监听，桥无残余 app-server 子进程。
桥退出原因不可恢复，不宣称桥本身优雅退出。日志已脱敏并替换原日志，配置中非空模型密钥、
Cookie、令牌或密码字段为零；保留合成数据库和证据供复核。详情 `cleanup-final.json` 及
模型桥同名清理记录。未启动、停止或修改用户生产服务，也没有推送、合并或发版。
