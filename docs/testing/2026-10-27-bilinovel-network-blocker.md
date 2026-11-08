# 哔哩轻小说持续采集阻断排查（2026-10-27）

后续用户明确选择仅该站直连，修复与最终原生／调度复验见
[精确域名直连验收](2026-10-27-bilinovel-direct-e2e.md)。下文保留当时默认代理路径的阻断结论。

范围：`discovery-only` 修复，沿用 [dynamic-web 契约](../platform-source-contract.dynamic-web.toml)。
承接[原生真实验收](2026-10-27-native-real-e2e.md)中持续采集、后期接管未通过的问题。

**结论：默认网络路径仍 `blocked`，诊断传递已修复。** 失败与当前系统代理路径相关；
同一 Ego 宿主、profile、已发布流程和请求权限下，仅测试宿主直连连续四轮成功。
不能从此推导网站全球离线、必须登录、代理内部哪条规则有误，或任意网络都可直连。

## 环境与复现

- 独立 worktree `ego-bilinovel-connection`，分支 `fix/ego-bilinovel-connection`，基线 `428e1305`。
  主工作区原有 `outputs/`、`promo/` 未触碰。实验起点无代码修改，诊断修复随后落地。
- 复用本轮隔离目录 `/tmp/obc-native-e2e-20261027`，候选和来源均为前次测试数据，
  没有访问日常实例。新证据保存在私有目录 `/tmp/obc-bilinovel-blocker-20261027`。
- 已发布哔哩轻小说版本 2：入口 `https://www.bilinovel.com/`，首页“新书抢鲜”，
  `max_items=3/max_pages=1/timeout_seconds=120`。与版本 1 入口、预算和动作一致。
- 真实托管 Ego Lite 0.6.1.13、原生 Node/CDP；Python import 与 CLI 指向本 worktree。
  未使用 Playwright、HTTP 内容回填、伪造模型回复或候选注入。
- 快速复现：真实 `EgoLiteWorkflowExecutor.run()` 运行同一发布版本，6.1 秒返回
  `ego_navigation_failed`；导航授权均通过，`blocked_requests=0`，安全诊断为
  `net::ERR_CONNECTION_CLOSED`。

## 受控网络对照

以下属于 `live-transport` 诊断，非正式调度或原生 App 端到端通过证据。
使用私有固定宿主副本仅记录错误枚举；网络对照每次只变一项，未修改全局代理。

| 实验 | 结果 | 证据 |
| --- | --- | --- |
| 原网络、原权限，连续四轮 | 4/4 失败，均连接关闭 | `normal.log` |
| 移除 Fetch 拦截，普通 Ego 导航 | 两轮仍连接关闭，排除采集拦截为必要原因 | `no-fetch-result.json`；首次探针的退出清理调用写错，浏览器由既有 atexit 清理；导航结果与该脚本收尾异常分开记录 |
| 每轮同 URL 最多三次导航 | 四轮均失败，12 次请求均未恢复 | `retry-result.json`；此重试未加入产品 |
| 仅测试宿主带 `--no-proxy-server` | 4/4 成功，每轮 3 本，正式权限仍开启 | `direct-repeat.log`、`no-proxy-result.json` |
| 恢复默认系统代理路径 | 2/2 失败，均连接关闭 | `system-reverse.log` |
| 显式指定系统的同一 HTTPS 代理 | 3/3 失败，均连接关闭 | `explicit-proxy-result.json` |

早期两轮原路径样本曾出现“一轮失败、一轮成功”；早期直连样本第一轮原生连接初始化
超时、第二轮采集成功。它们保留在原始日志，不替代后续四轮与反向对照，也不证明绝对
确定性。对照证明当前代理路径是可重复影响因素，尚未审计代理内部路由／远端出口。
测试结束撤掉私有启动参数；系统 HTTP/HTTPS/SOCKS 设置和产品默认路由均未改。

## 产品修复与正式验证

旧实现两处丢失原因：固定 Node 宿主把 Chromium `errorText` 丢弃，之后 producer 又以
单独的 `error_code` 覆盖 `detail`。因此正式记录只有“导航失败”，难以判断网络原因。

现在只接受长度受限的 `net::ERR_[A-Z_]+` 枚举：宿主每次导航先清除旧原因，Python 再
校验一次，producer 仅从合法枚举构建 `ego_navigation_failed (net::ERR_...)`。
浏览器原始错误文本、URL、query 不会复制到运行历史；登录失败与其他异常保持原分类。
API 字段、审批、源计划、预算、退避、证书和代理选择均不改变。

- 回归先红后绿：旧宿主不能返回网络枚举；Python 丢失合法枚举；旧 producer 丢失运行
  详情。新测试同时覆盖恶意／超长文本、非字符串、旧原因清除和登录错误不混用。
- 初次正式调度复验发现 producer 仍丢详情，保留失败探针 `formal-system-error.json`；
  修复后第一段 150 秒探针没有等到既有退避结束，`runs=[]`，保留该测试预算失败。
  随后等待正常到期，最终正式调度运行发布版本 2，4.73 秒后返回
  `ego_navigation_failed (net::ERR_CONNECTION_CLOSED)`，0 发现／0 入队；运行历史及
  `last_error` 均核对到该枚举。`formal-system-verified.json` 记录验证 PASS，未重置退避。
- 本轮没有新增候选入池、调用模型补分或复测所有原生界面；前轮 6 条入池仍属于前轮。
  哔哩轻小说后期接管截图仍未认证，不能以 NGA 的既有通过替代。

## 验收与交付边界

| 门槛 | 适用性 | 结果 |
| --- | --- | --- |
| 原阻断真实复现与网络对照 | required | PASS（诊断层） |
| 安全网络枚举传递、持久化回归 | required | PASS（单元／原生协议层） |
| 最终代码正式失败原因持久化 | required | PASS；正常调度运行 34.19 秒探针内完成并保留真实网络错误，随后停用来源 |
| 默认路由持续采集 | required | BLOCKED（代理路径） |
| 哔哩轻小说后期原生截图 | required | BLOCKED，未重复运行 |
| 关联回归／静态检查 | required | PASS；24 个关联测试文件共 433 passed / 93.76 秒；Ruff 通过，MyPy 352 源文件通过 |

契约注册审计仍为 31 PASS / 11 MANUAL / 17 N/A，required missing=0；仅证明注册。
没有把注册检查或直连诊断当成默认配置下的持续采集通过。
本轮未跑全仓所有 pytest，也未重复前轮 App 普通聊天／假设验收。

未合并、推送、发版或修改全局代理；站点单独直连属于网络路径偏好，保留用户选择，
没有为了通过验收静默切换出口。最终退出和测试来源停用核对保存在本轮私有证据目录。
运行历史 API 已回读到安全错误码；4 个测试来源均停用，隔离 API／兼容桥接端口关闭，
本轮托管 Ego 进程数为 0。库内仍为 16 个候选、6 条缓存，没有新增入池数据。
