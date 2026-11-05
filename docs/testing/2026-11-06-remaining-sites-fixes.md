# 多站真实验收后的阻断修复（2026-11-06）

范围：`discovery-only` 修复；延续 [12 站真实测试](2026-11-06-multisite-real-e2e.md)，
用户要求发现问题就修复。契约为 [dynamic-web](../platform-source-contract.dynamic-web.toml)
和兼容 [web](../platform-source-contract.web.toml)。本轮不是再次测试全部 12 站。

## 环境与代码边界

- 独立 worktree `ego-remaining-sites-20261106`，分支 `fix/ego-remaining-sites-20261106`，
  基线 `683e157b168ebd9c0282cba267fbc58879e86b66`。主工作区的 `outputs/`、`promo/`、
  `tmp/` 保留；不合并、推送、发布或切换日常部署。
- 复用上轮隔离目录 `/tmp/obc-multisite-20261106`，本轮证据单独存 `remediation/`。
  起点为 12 个停用来源、41 次运行、33 个候选、12 条缓存，不声称从零开始。
  API 18612、真实模型桥 18613 以及所属 worker 均导入本 worktree。
- 复用已安装的官方 Ego Lite 0.6.2.7 与专用 profile，不计作新安装验收；网页使用真实
  原生 Ego，Feed 使用实际 HTTP。Luna 为用户已授权的官方 GPT-6 Luna CLI 兼容桥，
  沿用上轮原模型限流后的隔离配置；没有模型答案回放、候选注入或阈值修改。
- 网络保留 `bilinovel.com` / `www.bilinovel.com` 的 Ego 精确直连偏好；其他 Ego 站点
  跟随原系统代理，不修改 OS 网络。HTTP 新修复明确消费已有 `[network].mode=system`。
- 原生 App 首轮复用 `OpenBiliClaw-mobile-multisite-20261106` 的原产品代码与 iPhone 17 Pro /
  iOS 26.5 模拟器。发现聊天接管入口缺失后，从 `7baf731` 建立独立 worktree
  `OpenBiliClaw-mobile-remaining-sites-20261106`、分支 `fix/native-verification-handoff-20261106`
  修复客户端，提交 `379cfcc`；live harness、模型协议与截图仅留在私有证据目录。

## 修复与因果证据

| 问题 | 复现与修复 | 回归边界 |
| --- | --- | --- |
| BBC RSS 固定直连 | 原正式传输 `robots_unavailable → timeout → ConnectTimeout`；本机 DNS 给出 `31.13.70.33`，代理上的受校验 DNS 给出 `23.55.208.178`，同代理 robots 与 RSS 各真实 200。HTTP 传输改为逐跳消费现有 direct/system/custom 网络策略 | 不跳过 robots、不关闭 TLS、不使用未校验远程域名连接；复用有界 DoH 和数字 IP CONNECT/SOCKS，HTTP 80 / HTTPS 443 均测试；直连与系统排除项保留 |
| 聚合订阅只能接收 8 个文章域 | 原 Planet Python 解析有 18 个不同文章 origin，试跑报 `content_scope_changed`。将文章引用独立上限改为 128，实际请求范围仍最多 8 个 origin | 没有文章页／图片自动抓取；私有引用拒绝；已批准集合在正式运行中冻结，新域仍 degraded 且不提交完整检查点 |
| V2EX HTML 被专用平台名单挡住 | 原 `prepare(executor=ego)` 在网络前返回 `needs_details`。允许已知平台独立创建 Ego 来源，试跑成功后才能审批激活 | 独立 `custom_` 来源、`web` 内容身份、份额、会话和正常候选准入；不继承内置适配器凭据；旧静态 recipe 默认拒绝规则保留 |
| 网站验证页误问新网址 | 放开 V2EX 后真实页面为 403 Document、标题“请稍候…”、可见“正在进行安全验证”；原模型返回 needs_details。用明确有界观察识别验证页，返回 needs_login 与接管链接，运行时也暂停 | 验证本身不等于账号登录要求；普通文章、普通拒绝、图片 403 不误判；没有自动解题、点击验证框或伪报成功 |
| 原生聊天缺少接管入口 | 实际持久化工具事件使用顶层 `text`，`ChatTurn.sourceLoginLinks` 只读旧 `data.text`，导致后端已经返回接管链接而按钮缺失。现在兼容两种事件格式 | 保留去重、无效 JSON 忽略、自身后端 URL 校验；不依赖模型将链接写成 Markdown |

最初 7 个新回归先失败；前三项修复后通过。验证页修复另有真实观察驱动的回归先失败，
修复后通过，并覆盖后台执行不得采集验证文字。
App 真实聊天卡住后，根据同一持久化事件补的回归先返回空链接而失败；修复后通过。

## 真实流程结果

- **Planet Python PASS**：真实聊天 → prepare → 真实试跑 → 激活审批 → 正式 worker →
  设置份额 1→2、间隔 5 分钟。三轮为 `ok(3)`、实际 `304`、实际 `304`；3 条新候选，
  真实 Luna 评分 0.72–0.78，全部入池。没有把 304 算成新内容。
- **BBC 技术 RSS PASS**：同一真实聊天／审批／管理链，三轮各读取 3 条；首轮入队 3 条，
  后两轮均去重。真实评分 0.33–0.36，3 条因相关性低被拒绝，采集没有失败；不把候选数
  当成入池数。
- **V2EX 保留站外验证阻断**：真实原生聊天首次已经进入 Ego，但安全验证页使正向审批
  测试无法继续，已保存失败证据并中止等待。修复后原草稿真实复验返回 `needs_login`，
  没有激活。真实聊天续查也返回原接管链接且没有提交审批。
- **V2EX 原生接管 PASS（限接管边界）**：真实 App 打开 Ego 画面、实际 PNG 绘制
  370×260.2、点击继续试跑后仍正确保留 needs_login/false，退出释放；27 秒通过。
  截图人工核看为 V2EX 验证页面，SHA-256
  `9f5e87aaa418d2a1ed37efcbfa7f008a283b16098fa353e40d05f7657af9944b`。
  这不等于通过了网站验证、成功采集或真实账号登录。
- **V2EX 原生聊天接管整轮 PASS**：单独接管页通过后，又从真实聊天发现按钮缺失；
  在新的 App worktree 修复事件解析并重新构建。实际输入新增来源请求 → 真实模型调用
  prepare → 显示“在 APP 中登录来源”按钮 → 点击打开真实 Ego 验证画面 → 继续试跑
  仍为 needs_login/false、没有激活来源 → 返回对话，63 秒通过。截图人工核看，SHA-256
  `4b23ab5608c8780d47a42550df01da43caec0dd79810e5ff977231c76ebec7f2`。
- **哔哩轻小说仍 BLOCKED**：保留用户指定直连下，当前官方 Ego 复验仍是
  `net::ERR_CONNECTION_RESET`（1.89 秒）。独立直接 HTTPS 与其他当前解析地址也被
  重置；没有改为代理、硬编码 IP、关闭证书或靠重启声称修复。旧月份的成功不算本次通过。

本轮新增 2 个成功来源、6 次正常调度、6 个新候选，其中 3 入池、3 低分拒绝。
V2EX 和哔哩轻小说没有创建激活来源。结论为 **incremental only**：两项实际接入阻断已
打通，专用域限制与验证接管缺陷已修复；两个网站的外部访问／验证条件仍未满足。

## 检查与证据

- 核心修复后的 48 个关联测试文件：`915 passed`，244.86 秒；覆盖 Ego、custom、workflow、
  Web、公共网络及图片网络隔离。排除旧 `test_web_guided_init_e2e.py`，没有用 Playwright
  替代 Ego，也不是全仓 pytest。准确文件集保存在私有 `regression-files.json`。
- 随后同步 Web 网络注册声明：`routed_by_network_mode=true`，地域要求仍为 false。
  相关 5 个文件另跑 `131 passed`（与前述集合有重叠，不相加）；运行中的 API 在这项
  元数据修改前启动，BBC / Planet 的传输与本轮所有运行代码均已加载，声明另由回归和契约核对。
- App 全部常规测试 `168 passed`，`flutter analyze --no-pub` 无问题；新增真实事件格式与旧格式兼容回归。
- Ruff `src/ tests/` 通过；MyPy 352 个源文件通过；隔离配置 `config-show` 检查。
- source contract audit：31 PASS / 11 MANUAL / 17 N/A，required missing=0；只证明注册，
  不替代真实采集结论。请求、模型协议、配置、数据库及原始截图仅保存在私有证据目录。
- 对既有图片隧道的改动只增加显式 80/443 端口参数，封面仍用原默认 443；边界测试覆盖
  HTTP/SOCKS 目的 IP、Host/TLS、robots、私有／混合地址失败、无凭据和无直连降级。
- API 重启用于载入本次代码；一次过早的测试脚本连接与短时 health 探测失败单独保留，
  正常来源 API 与 worker 就绪后才进行实际重试。不是安装或用户操作的重启要求。
- 为覆盖公共网络策略对已有来源的影响，最终代码另用原 `system` 设置真实读取阮一峰
  Atom、Solidot RSS、Daring Fireball JSON Feed：三者均 HTTP 200，并成功解析 3 条。
  这项是传输／解析兼容复查，没有重新审批、启用或写候选，不计作三次新增完整 E2E。

## 收尾

测试库保留 14 个来源（全部停用）、47 次运行、39 个候选、15 条缓存；其中本轮净新增
2 个来源、6 次运行、6 个候选、3 条缓存。四个测试草稿的交互控制显式关闭，专用 Ego
已自动退出。API、模型桥及其 worker 均停止，18612 / 18613 无监听，模拟器恢复 Shutdown。
未停止其他环境的进程。临时原生 harness 已从两个移动端 worktree 移除，私有证据继续保留；
主工作区既有未跟踪目录未修改，修复分支没有合并、推送或发布。
