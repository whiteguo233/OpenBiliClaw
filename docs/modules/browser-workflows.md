# Browser discovery workflows

浏览器发现使用独立的持久登录会话执行声明式流程；聊天负责建立和修改来源，
调度、去重、评估和入池沿用统一发现链路。公共 Feed / Sitemap / 静态列表继续使用
原有匿名 HTTP 适配器。

## 已实现能力

| 能力 | 行为 |
| --- | --- |
| 流程编译 | `version=1`；只接受导航、搜索、等待、链接和最终采集；拒绝代码、秘密字段、临时 snapshot ref 和越界预算 |
| 动态参数 | `query`、`date_from`、`date_to` 每轮替换；URL 参数进行编码 |
| 标准提取 | 标题、URL、作者、发布时间、摘要；支持下一页、循环检测、条数和页数限制 |
| 登录会话 | 后端生成的不透明 session ID 对应独立 profile；退出后保存 Cookie，下次执行复用 |
| 人工接管 | 同一会话提供 viewport PNG、脱敏 DOM snapshot、点击／输入／按键；只允许在显式 interactive 模式调用 |
| 空结果 | 必须匹配明确的空结果元素；找不到内容且没有空结果证据会报告提取失败 |
| 可移植执行 | Playwright 完整 Chromium 支持桌面、Python CLI、Linux 服务器和 amd64/arm64 Docker；就绪检查与实际启动使用同一可执行文件 |
| Ego Lite | 检测宿主 CLI 并返回明确不可用原因；尚无满足隔离与网络约束的 Ego Lite 执行适配器，不声称已完成该能力 |

## 公共接口

- `compile_browser_workflow(payload) -> BrowserWorkflowPlan`：保存前、运行前均校验。
- `BrowserWorkflowPlan.to_dict()`：持久化格式，不包含账号密码或 Cookie。
- `PlaywrightWorkflowExecutor(profile_root).run(plan, session_id=..., parameters=...)`
  返回 `BrowserWorkflowResult`，明确区分成功、已证实为空、需要登录、降级和失败。
- `open_session(plan, session_id=..., interactive=False, observation_only=False)`
  打开入口但不执行采集。`observation_only` 可以在跨域跳转被阻止后保留页面供观察；
  不会授权该跳转。
- `BrowserWorkflowSession.snapshot()` 返回有限的 tag、selector、文字和链接信息，
  不包含输入框值或 URL query；优先保留主内容及搜索／登录控件，并提供实际
  parent、ancestor、前后兄弟及相对 child-link selector。数组顺序不是 DOM 邻接证据。
  同时返回 `blocked_resource_origins` 和
  `blocked_navigation_origins`，供用户判断是否增加授权。
  `resource_failures` 最多保留 12 项资源 origin、类型、HTTP 失败状态及固定网络
  错误码；不包含 URL query、响应正文或 Cookie。核心资源失败导致页面完全没有
  可见文字／控件时，建源直接报告访问失败，不把空白页面误判为需要更多用户信息。
  明确的登录墙或待授权跳转证据优先进入人工接管；先前可选资源失败不能遮蔽这些证据。
- `BrowserWorkflowSession.act()` 仅供已授权的人工接管 API 使用，不能作为模型采集工具。
- `browser_runtime_status()` 检查匹配的 Chromium 文件是否存在；仅装 Python 模块不算就绪。
  `PlaywrightWorkflowExecutor` 默认启动该完整 Chromium；显式 `executable_path` 仍优先。
  无界面模式不再隐式选择另一份 headless shell，避免就绪检测与执行不一致及站点兼容差异。
- `install_browser_runtime(with_system_deps=False)` 调用随 Playwright 打包的 Node driver，
  适用于冻结桌面程序，不依赖 `sys.executable -m playwright`。

## 流程权限

`allowed_origins` 是精确的内容导航和提取范围。`resource_origins` 仅允许相应域的
只读子资源，不能用来扩大来源的内容范围。`auth.login_origins` 只在用户开启人工
接管时允许登录导航和 POST；周期发现遇到该跳转会报告需要登录。

计划只接受稳定 CSS selector。`click_link` 读取链接 href 再导航；`submit_search`
只支持无密码字段的 GET 表单。后台不能提交 POST；需要 POST 搜索、复杂应用内操作
或未支持登录流程的网站会明确失败，不能偷偷扩展权限。第一版流程针对顶层文档，
iframe 和弹窗请求会被阻止；强制依赖嵌入式验证码的网站目前不能完整接管，
不能将顶层页面登录测试推断成所有网站／验证码都已支持。

运行前检查所有允许域的 DNS 地址均为公网，并给 Chromium 配置固定解析地址。
未知域解析失败；每个请求还受精确 origin、方法和总请求数约束。显式 browser-context
proxy bypass 防止系统代理代替浏览器解析域名，破坏 DNS pin；未使用的本机代理端口
只作为 fail-closed 兜底。服务工作线程、WebSocket、下载和额外页面被禁用或关闭。
重定向另用 CDP 响应阶段检查，因为 Playwright `route` 不会重新回调同一重定向链。
单次导航最多等待 30 秒，整个发现运行仍受计划的总时间预算约束；总预算取消后
会释放 profile，允许后续重新打开。

当前浏览器执行不继承 `[network]` 的系统／自定义代理。把普通代理直接传给
Chromium 会让代理重新解析目标域名，失去已校验 IP 与实际连接目标一致的保证。
需要代理才能访问的网站当前可能超时；HTTP 客户端可通过代理访问，并不意味着
浏览器工作流也已支持该路由。

本地浏览器测试仅通过 Python 依赖注入，允许受控 fixture DNS 名解析到 `127.0.0.1`。
该开关不属于计划、配置或 API 输入，不能通过聊天启用。

## 安装与维护

默认 Python 安装已包含 Playwright driver；首次使用运行 `openbiliclaw browser install`，
或使用设置页的安装按钮。`[browser]` extra 继续作为兼容别名。Linux
缺少系统库时由管理员使用 `--with-system-deps`。桌面构建包含匹配的 Node driver，
Chromium 按用户安装；Docker 在构建时安装浏览器和系统库。
只需安装匹配的完整 Chromium；工作流不依赖单独的 headless shell 文件。

浏览器 profile 是登录凭据存储，应与应用数据一起保留并限制本机访问权限。不要把
profile、Cookie、截图或接管输入写入流程版本、模型提示、公共日志或代码仓库。

## 验证记录

2026-10-03，使用真实 Chromium、公开 Django 官方博客和当前配置的真实模型，
以“跟踪最新官方公告，提取标题和官方文章链接”为目标完成自然语言建源。
模型基于脱敏 DOM 层级生成采集选择器，通过严格编译后试跑取得 5 条公告。
隔离数据库内启用来源后，首次执行和关闭数据库再重开的执行均成功取得 5 条；
重开执行不配置模型，保存的流程版本及内容散列保持一致。该验证未修改生产数据，
未使用登录账号；它证明公开列表的完整建源与持久执行路径，不代表所有动态网站
或登录方式均已验收。

本地真实浏览器 fixture 覆盖分页、登录持久化与失效、跨域登录授权、私网与多跳
重定向阻断、资源域与内容域分离，以及正文优先观察和选择器跨会话复用。
Linux/amd64 Docker 已完成镜像构建、默认入口与健康检查、匹配 Chromium 的连续分页
回放、总预算取消后 profile 复用和资源失败诊断；采用容器内匿名 fixture，外网关闭。
这不替代外站账号、Linux/arm64、Ego Lite 或 macOS/Windows 冻结程序验收。

同日真实豆瓣验证中，`/group/` 未登录时转到公开讨论精选；其实际登录入口的
核心脚本对 Chromium 返回 HTTP 418，并被浏览器 ORB 阻止。此时尚无登录表单
或 iframe，已验证诊断会明确报告资源访问失败。没有向用户交接空白页面，也没有
输入凭据、导入 Cookie 或绕过网站限制。Hugging Face Papers 的当前网络则表现为
直接连接超时、既有 HTTP 代理路由可响应；尚未通过浏览器建源验收。

真实 Django 5.2 文档搜索另完成“按当前关键词，每轮第一页前 5 条”的自然语言
建源、试跑和启用。模型生成 `{query}` 导航参数与 5 条／1 页预算；同一流程从
`database` 改为 `authentication` 后取得两组不同的文档 URL。关闭数据库再重开且
不配置模型，原查询仍返回相同 5 条，流程版本与内容散列一致。
此前未限制分页的尝试在第二页遇到另一个 origin 的官方博客链接，严格报告降级，
未扩大允许域；Python 文档的 JavaScript 搜索虽曾成功呈现，但后续索引加载反复
超时，未通过持久执行验收。查询参数验收不等于复杂 JavaScript 页面全部已支持。
