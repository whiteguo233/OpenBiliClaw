# 哔哩轻小说精确域名直连与原生复验（2026-10-27）

范围为 `discovery-only` 修复，沿用 [dynamic-web 契约](../platform-source-contract.dynamic-web.toml)。
用户明确选择仅哔哩轻小说直连，其他网站继续使用系统代理。承接
[网络阻断排查](2026-10-27-bilinovel-network-blocker.md)，本轮没有修改全局代理。

## 实现与环境

- 后端独立 worktree `ego-bilinovel-direct`，分支 `fix/ego-bilinovel-direct`，基线 `498bb0a6`。
  CLI、Python import、API 启动脚本均指向该 worktree；日常服务和主工作区未切换。
- 复用隔离目录 `/tmp/obc-native-e2e-20261027`，API 18612；新证据保存于私有目录
  `/tmp/obc-bilinovel-direct-20261027`。已有 16 候选、6 缓存作为本轮基线，没有伪称空库。
- 网站为真实托管 Ego Lite 0.6.1.13 原生通道，Feed 走 HTTP，没有 Playwright 回退。
  模型仍是获准的官方 Codex GPT-6 Luna 客户端兼容桥接，embedding 仍为真实 Ollama
  `nomic-embed-text`。未替换模型、注入回复或降低入池门槛。
- 移动端 checkout `OpenBiliClaw-mobile-e2e-20261027` / `7baf731`，产品代码沿用
  `3e66d85`；iPhone 17 Pro / iOS 26.5 原生模拟器，没有修改 App 产品代码。

通过真实 `PUT /api/custom-sources/executors/ego/network` 保存并回读
`direct_hosts=["bilinovel.com","www.bilinovel.com"]`，偏好位于私有
`ego-managed/network.json`，默认空列表。API 无需重启，冷启动读取当前系统手动代理及
原排除项，再追加这两个精确域名。仅 bypass 参数的实验仍失败；配合当前系统手动代理
映射才生效，行为依据 [Chromium 代理文档](https://chromium.googlesource.com/chromium/src/+/HEAD/net/docs/proxy.md)。
不扩大到其他子域或任意站点，不自动修改证书／代理，不将 PAC/WPAD 静默转换成直连。

## 本轮同时补齐的运行问题

1. Ego 冷启动后数字空间 ID 会被另一个来源复用。只在宿主代次变化、原逻辑名称缺失，
   且当前 ID 的 agent/profile/name 与另一个本应用当前代次私有会话记录一致时恢复原空间。
   未知占用、用户所有、改名、同名和跨 profile 仍拒绝。旧协议回归先失败，修复后通过。
2. 接管重开遇到原生初始化超时，旧重试只重建连接。现在释放失败会话后以独占租约确认
   无其他活动，才能停止闲置托管宿主并由既有一次重试自动启动；取消仍传播，总预算不延长。
3. 继续试跑曾因导航等待超时失败。真实诊断为文档已提交且 DOM `interactive`，没有
   权限拦截；固定读取无需等待外围资源全部结束。保留 `complete` 稳定 1.5 秒的原规则，
   另允许相同 loader／URL 的已解析文档保持 `interactive` 8 秒后进入原提取校验。
   这不是 DOM 内容不再变化的保证；`loading`、未提交／旧文档仍拒绝，
   最终 URL 仍授权，选择器／空结果／scope／审批仍执行，不把 DOM 就绪当作采集成功。

## 真实结果

| 门槛 | 适用性 | 结果与证据 |
| --- | --- | --- |
| 显式设置、持久化与活动会话保护 | required | PASS；真实 API 保存并回读成功；原生接管期间同一 PUT 返回 409 `ego_setup_sessions_busy`，规则未改，见 `route-configured.json`、`active-native-route-update.json` |
| 两站实际请求路径 | required | PASS；同一配置下 NGA Document 连接仍为系统 loopback 代理，哔哩轻小说为公网直连，两站各采 3 条。仅私有固定宿主副本记录连接元数据，未改变请求策略；`route-verification.json` |
| 原生聊天新增哔哩轻小说＋Atom | required | PASS，11 分 08 秒；Ego 真试采、App 审批、份额 1→2、后台 3 条采集、接管／继续试跑／返回聊天，再聊天新增并审批 Atom；`native-full.log`。本阶段在空间 ID／等待恢复补充前运行，不能单独充作最终代码全部验收 |
| 旧哔哩轻小说五分钟持续采集 | required | PASS；发布版本 2 连续三轮 `ok`，各 3 条；前轮完成到下轮开始为 313.884、313.877 秒。9 发现、1 新入队、8 重复，`cadence.json`；阶段边界同上 |
| 最终代码原生严格截图与恢复 | required | PASS，42 秒；真实 RawImage 为 370×260.2，原始 PNG 已人工核看；Tab、退出后 409、不同草稿重新接管、真实继续试跑均通过，`native-visual-dom.log`、`native-visual-dom-driver.json` |
| 最终代码正常调度 | required | PASS；NGA、哔哩轻小说发布版本 3 各运行一次，均 `executor=ego/status=ok/3 discovered`；NGA 2 新入队，哔哩轻小说 0 新入队（重复），`formal-dom.json`；没有绕过失败退避 |
| 真实评估与原规则准入 | required | PASS；本轮新增候选 17–19 完成真实评估，分数 0.48、0.28、0.58，均低分拒绝。总库 19 候选、缓存仍为原 6 条；没有把去重或低分结果算成新入池 |
| 最终关联检查与资源回收 | required | PASS；27 个关联文件 464 passed / 129.31 秒，Ruff 与 MyPy（352 源文件）通过；最后采集完成后 188.39 秒检查时宿主已离线而 API 仍健康，规则仍持久保留 |

原生首次严格截图在重开时失败，第二次重开通过但继续试跑因加载超时失败；两份 driver
和日志均保留，未用后续通过覆盖失败。私有 readiness 诊断记录了已提交的 `interactive`
文档；未将其描述成网站离线或必须登录。最终 DOM 等待修复后严格用例完整通过。
Luna 本轮曾有上游 routing 错误并恢复，所有成功结果来自实际请求；没有录制回复回放。
静态契约审计为 31 PASS / 11 MANUAL / 17 N/A，required missing=0，仅代表注册。
没有重跑全仓所有 pytest；原生侧只构建／运行上述真实测试，没有新的 App 产品变更。

## 交付与边界

**本轮所声明修复范围 verdict：complete。** 6 个隔离测试来源均停用、18 个草稿会话已
显式关闭；候选／缓存计数与得分见 `final-data.json`，退出核对见 `cleanup.json`。
测试 API 与兼容桥接已退出，18612／18613 端口关闭，测试托管 Ego 进程数为 0；
两个精确域名偏好仍保存在隔离数据目录中。

这是上述网络与会话修复的验收，不是整个平台全能力或所有安装平台的认证。
原生普通聊天／假设操作未在本轮重复，见[此前真实验收](2026-10-27-native-real-e2e.md)。
本轮没有真实账号登录、真机、Android、Windows、Linux/Docker Ego 或正式安装包验收。
配置只应用到隔离实例，代码未合 main、未推送或发版，日常服务保持原部署。
原始请求、截图、数据库和代理连接元数据只保存在本机私有证据目录，没有提交凭据。
