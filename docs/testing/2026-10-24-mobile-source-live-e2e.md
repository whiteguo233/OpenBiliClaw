# 手机来源登录返回与聊天建源真实验收

## 范围与环境

- 范围：`capability-increment`，验证 `e8713708` 手机登录返回修复及其相邻聊天建源链路；复用 `docs/platform-source-contract.dynamic-web.toml`。本报告不认证独立 Flutter 原生 APP。
- 分支：`fix/mobile-source-login-return`，独立 worktree；生产配置、数据和原生客户端未改动。
- 实际 CLI：`openbiliclaw serve-api --host 127.0.0.1 --port 18612`。
  隔离 runtime/data/config 在 `/tmp/obc-ego-ui-e2e-20261023-r2`；运行前比对 Python、JS、MJS、HTML、CSS 与该提交，无差异。复用已有安装，不是首次安装测试。
- 浏览器与网站采集均为真实 Ego Lite，没有 Playwright。UI 使用 `/m/`，最终截图验证 390×844，页面宽度与视口均为 390，无横向溢出。
- 模型沿用用户允许的官方 Codex app-server 协议桥，真实 `gpt-6-luna / medium`、provider `openai`。14 次已完成模型请求均为 `ok`；桥只转协议，不预设工具调用、采集结果、评分或推荐文案。
- 本轮证据目录：`/tmp/obc-mobile-live-e2e-20261024`，包含脱敏汇总、请求日志、数据库证据、UI 脚本和截图；原始模型会话不入库提交。

## 实际操作与结果

| 门 | 适用性 | 结果 | 证据 |
| --- | --- | --- | --- |
| 运行代码与隔离环境 | required | PASS | `baseline.json`，进程通过隔离 CLI wrapper 启动 |
| 手机设置进入真实 NGA 接管 | required | PASS | `/login`、`/takeover` 均 HTTP 200；接管页为 `/m/desktop/source-login.html`，实际显示 NGA 网站截图 |
| 接管后继续试跑 | required | PASS | 点击“登录完成，继续试跑”，`/resume` HTTP 200，`trial_success=true`、`executor=ego`，3 条真实样例 |
| 返回手机对话 | required | PASS | 点击“返回手机对话”进入 `/m/#/chat`，对话输入框可操作，并从这里发送下一条建源消息 |
| 手机聊天新增并批准 | required | PASS | 实际聊天发送 URL、范围、3 条/1 页、5 分钟、1 份要求；模型试采后生成确认卡；UI 点击批准 |
| 后台发现、去重与评估 | required | PASS | 两轮正式 Ego 运行均 `ok`；累计 6 条发现、3 条去重后入队，真实模型评分后 1 条入池、2 条低分过滤 |
| 手机推荐显示独立来源 | required | PASS | `new-source-phone.png`：显示“NGA 手机验收 1024”、标题、作者与推荐文案；数据库同条为 `shown`，文案长 102 字符 |
| 手机来源管理 | required | PASS | 设置页显示名称、份额、5 分钟间隔及下次发现；实际保存份额 1→2，读库确认，随后恢复 1 |
| 清理 | required | PASS | UI 停用新增来源；所有测试来源均停用、份额为 1；测试后端、协议桥、Ego 均关闭 |
| NGA 账号登录、验证码 | N/A | NOT_RUN | 本次公开列表无需登录；接管和恢复试采成功不等于账号登录成功 |
| 满 5 分钟的下一轮自然周期 | required（完整周期验收） | NOT_RUN | 本轮验证首次后台执行和手动换批补货，不把后者计作 5 分钟周期；此前独立周期证据见 2026-10-23 NGA 报告 |
| 手机真机安装、Flutter 原生 APP | required（原生 APP 验收） | NOT_RUN | 本次为 Ego 手机尺寸的移动网页/PWA，未运行 APK/IPA |

### 标识与计数

- 重新登录草稿：`d1dd2b94-9216-477f-bb22-f78017344894`，复用旧 NGA 已批准流程；公开采集 3 条，成功发布新版本，原来源仍保持停用。
- 手机聊天 turn：`m-1792778436389-4xuro5`，`completed`。
- 新建草稿：`d3bf6477-b946-4967-9289-fa0851b38e7a`；审批：`ap_6347d038b5964f5d`。
- 新来源：`custom_afb983bf132d41bf8f764e3002713c8e`，名称“NGA 手机验收 1024”。
- workflow：`185c4348-eb33-4392-9f08-52f92a78edba`，版本 1，执行器 `ego`。
- 首轮：`4d390310-b8af-4072-b619-d52ccdf8e0b4`，发现 3、入队 2。
- 换批触发补货：`16784f8b-49c5-4d6c-99bd-868dd360ea47`，发现 3、入队 1。
- 候选 13：0.16，`rejected_low_score`；候选 14：0.72，`cached` 且对应内容已 `shown`；候选 15：0.38，`rejected_low_score`。
- 实际展示的是《少女乐队的呐喊/GIRLS BAND CRY》专楼，原始链接 `https://bbs.nga.cn/read.php?tid=39453883`。没有把此前其他 NGA 或轻小说缓存当作新来源结果。

## 测试过程中的限制

首次打开页面时测试后端尚在启动，页面空白；确认服务启动完成后重新加载，同一进程内完成后续流程。一次输入定位使用了会随角色变化的 placeholder，未匹配且未发请求；读取实际 DOM 后改用稳定的 `#chat-input`，只发送了一次建源消息。这些记录不描述为“全程零重试”。

本地独立原生仓库 `OpenBiliClaw-mobile` 在 `dfe340a` 加现有未提交改动的状态下，`lib/` 未找到 `custom-sources` 管理接线及 `source_name`/`sourceName` 字段，推荐模型只有平台级字段。本轮只读检查，未修改该仓库，也不把移动网页通过推断成原生支持。

本轮 verdict：手机登录返回修复及聊天→审批→实际发现→评估→推荐展示链路通过；整体移动产品仍为 `incremental only`，原生 APP 和本轮完整周期未验收。修复已提交、隔离服务已加载验证；未合入 main、未发布、未更新用户正式安装版。
