# Browser discovery workflows

浏览器发现由真实 Ego Lite 宿主执行声明式流程；聊天负责建立和修改来源，
调度、去重、评估和入池沿用统一发现链路。新建来源的 `auto` 只对实际解析成功的
RSS / Atom / JSON Feed 选择 HTTP，普通网页选择 `ego`。旧 HTTP Sitemap / 静态列表
与已保存的 Playwright 流程保留兼容；更新流程默认沿用原执行器，不自动迁移。

首次添加网页自动准备专用 Ego Lite，后续采集按需启动已准备的宿主。
真实验收及平台限制见[本轮记录](../testing/2026-10-22-ego-lite-discovery-e2e.md)；
安装检测和历史 Playwright 记录不计作 Ego 网站执行证据。

## 已实现能力

| 能力 | 行为 |
| --- | --- |
| 流程编译 | `version=1`；只接受导航、搜索、等待、链接和最终采集；拒绝代码、秘密字段、临时 snapshot ref 和越界预算 |
| 动态参数 | `query`、`date_from`、`date_to` 每轮替换；URL 参数进行编码 |
| 标准提取 | 标题、URL、作者、发布时间、摘要；支持下一页、循环检测、条数和页数限制 |
| 登录会话 | 不透明 session ID 映射到应用拥有的 Ego task space 和固定 profile ID；同一 Ego profile 的 Cookie 可共享，task space 仅隔离页面 |
| 人工接管 | 同一会话提供 viewport PNG、脱敏 DOM snapshot、点击／输入／按键；只允许在显式 interactive 模式调用 |
| 空结果 | 必须匹配明确的空结果元素；找不到内容且没有空结果证据会报告提取失败 |
| Ego Lite | 官方 `ego-browser nodejs` 加载仓库固定脚本，通过 `ego.sendCDPMessage` 执行；没有 Playwright 中转或静默回退 |
| 托管准备 | 首次网页建源自动下载官方应用、校验签名、完成专用 profile 首次设置并验证原生连接；设置页和 CLI 可提前准备或重试 |
| 环境边界 | 当前适配器连接本机 macOS Ego 宿主；未验收 Windows，不提供 Linux/Docker 远程桥接 |
| 历史兼容 | 已保存的 Playwright 流程继续使用原完整 Chromium 执行器；新建工具与 CLI 不再提供该选择 |

## 公共接口

- `compile_browser_workflow(payload) -> BrowserWorkflowPlan`：保存前、运行前均校验。
- `BrowserWorkflowPlan.to_dict()`：持久化格式，不包含账号密码或 Cookie。
- `WorkflowStore.pending_draft_summaries()` 返回最近 10 条未应用草稿的有界名称、网址、
  目标、状态与 ID；聊天 `list_sources` 可在重启后恢复草稿，不要求用户记住内部 ID。
- `EgoLiteWorkflowExecutor(profile_root).run(plan, session_id=..., parameters=...)`
  返回 `BrowserWorkflowResult`，明确区分成功、已证实为空、需要登录、降级和失败。
- `EgoSetupManager(data_dir).prepare()` 安装与初始化；`status()` 只读，
  `connection()` 返回专用 CLI／宿主／profile，`ensure_running()` 只启动已初始化的宿主。
  data_dir 为当前数据库父目录；网页首次准备在页面运行预算以外执行，Feed 不进入此路径。
- `open_session(plan, session_id=..., interactive=False, observation_only=False)`
  打开入口但不执行采集。`observation_only` 可以在跨域跳转被阻止后保留页面供观察；
  不会授权该跳转。
- 会话的 `snapshot()` 返回有限的 tag、selector、文字和链接信息，
  不包含输入框值或 URL query；优先保留主内容及搜索／登录控件，并提供实际
  parent、ancestor、前后兄弟及相对 child-link selector。数组顺序不是 DOM 邻接证据。
  同时返回 `blocked_resource_origins` 和
  `blocked_navigation_origins`，供用户判断是否增加授权。
  `resource_failures` 有界保留资源 origin、类型、HTTP 失败状态及固定网络
  错误码；不包含 URL query、响应正文或 Cookie。核心资源失败导致页面完全没有
  可见文字／控件时，建源直接报告访问失败，不把空白页面误判为需要更多用户信息。
  明确的登录墙或待授权跳转证据优先进入人工接管；先前可选资源失败不能遮蔽这些证据。
- 会话的 `act()` 仅供已授权的人工接管 API 使用，不能作为模型采集工具。
- Ego `availability()` 只读本机安装与匹配宿主进程，不启动 CLI 或首次设置。
  `connection_state=unverified` 表示已安装、待实际试跑握手；不能用 `installed=true`
  宣称已连接。托管宿主使用持久握手记录与当前专用进程身份核对，跨后端进程仍可读；
  外部宿主使用本进程的原生握手记录。实际打开托管会话会按需启动已初始化宿主。
- 以下两个接口只维护历史 Playwright 流程：`browser_runtime_status()` 检查匹配的
  Chromium 文件是否存在；仅装 Python 模块不算就绪。
  `PlaywrightWorkflowExecutor` 默认启动该完整 Chromium；显式 `executable_path` 仍优先。
  无界面模式不再隐式选择另一份 headless shell，避免就绪检测与执行不一致及站点兼容差异。
- `install_browser_runtime(with_system_deps=False)` 调用随 Playwright 打包的 Node driver，
  适用于冻结桌面程序，不依赖 `sys.executable -m playwright`。

## 流程权限

`allowed_origins` 是精确的内容导航和提取范围。`resource_origins` 仅允许相应域的
只读子资源，不能用来扩大来源的内容范围。`auth.login_origins` 只在用户开启人工
接管时允许登录导航和 POST；周期发现遇到该跳转会报告需要登录。

计划只接受稳定 CSS selector。原生采集将条目字段的相对选择器（如 `> a`）按
`:scope` 解析，`:scope` 自身也可作为链接条目；不改写模型保存的计划。
`click_link` 读取链接 href 再导航；`submit_search`
只支持无密码字段的 GET 表单。后台不能提交 POST；需要 POST 搜索、复杂应用内操作
或未支持登录流程的网站会明确失败，不能偷偷扩展权限。第一版流程针对顶层文档，
iframe 和弹窗请求会被阻止；强制依赖嵌入式验证码的网站目前不能完整接管，
不能将顶层页面登录测试推断成所有网站／验证码都已支持。

Ego 原生桥在导航前、CDP 请求和重定向响应阶段校验精确 origin、允许方法与公共 DNS，
并限制请求数量和运行时间。脚本只使用仓库固定的观察、表单和提取逻辑，不能执行模型
提供的 JavaScript。工作流任务只复用 ID、随机名称与 agent ownership 均匹配的
task space，不接管个人标签页；关闭采集页后保留会话映射供定期执行。
官方宿主重启会丢弃空 task space。仅对托管宿主，在已记录的宿主进程确实改变、
旧 ID 与随机名称都不存在时，才在同一固定 profile 重建任务空间；同进程丢失、
名称／ID 冲突、用户接管、profile 改变及缺少历史进程记录均继续失败。
外部宿主不自动重建。该恢复证明公开采集可续跑，不等于真实账号登录持久已经验收。
响应长度与已接收流量超过阈值会终止加载并报告资源预算错误；在途数据可能超过阈值，
这不是宿主网络层的严格字节配额。后台连接前检查命名宿主仍在运行；官方 CLI 没有
原子 no-autolaunch 开关，检查后宿主立刻退出的竞态尚不能完全消除。

Ego 自己负责底层 DNS、socket 与网络设置，本适配器**没有 socket DNS pin**，
`network_guarantee=origin_and_public_dns_preflight`、`dns_pinned=false` 如实报告。
公共 DNS 预检不等同于实际连接地址固定；不把旧执行器的网络保证套用到 Ego。
真实公共网页的 DOM、截图与采集已经过官方宿主验证；人工登录和复杂站点的适用范围
仍按各项实测结果报告，不能由公共列表采集推导。

历史 Playwright 执行器运行前检查所有允许域的 DNS 地址均为公网，并给 Chromium 配置固定解析地址。
未知域解析失败；每个请求还受精确 origin、方法和总请求数约束。显式 browser-context
proxy bypass 防止系统代理代替浏览器解析域名，破坏 DNS pin；未使用的本机代理端口
只作为 fail-closed 兜底。服务工作线程、WebSocket、下载和额外页面被禁用或关闭。
重定向另用 CDP 响应阶段检查，因为 Playwright `route` 不会重新回调同一重定向链。
单次导航最多等待 30 秒，整个发现运行仍受计划的总时间预算约束；总预算取消后
会释放 profile，允许后续重新打开。

历史 Playwright 执行不继承 `[network]` 的系统／自定义代理。把普通代理直接传给
Chromium 会让代理重新解析目标域名，失去已校验 IP 与实际连接目标一致的保证。
需要代理才能访问的网站当前可能超时；HTTP 客户端可通过代理访问，并不意味着
浏览器工作流也已支持该路由。

本地浏览器测试仅通过 Python 依赖注入，允许受控 fixture DNS 名解析到 `127.0.0.1`。
该开关不属于计划、配置或 API 输入，不能通过聊天启用。

## 安装与维护

新网站首次建源在运行后端的同一台 Mac 自动准备[官方 Ego Lite](https://lite.ego.app/download)。
专用应用和 profile 位于数据库旁的 `ego-managed/`，无需填写 CLI 路径、服务名或启动参数。
设置页“准备 Ego Lite”和 `openbiliclaw sources prepare-ego` 可提前准备／重试。
完整安装包缓存于专用目录。镜像内缺应用/CLI 或明确签名不符时清除自身常规缓存，
下一次准备会重新下载；系统信任拒绝、超时、取消或无法确定原因的挂载失败保留缓存，
不自动循环下载。准备流程验证官方签名与系统信任，在专用首次设置页面跳过数据导入和可选订阅，
关闭默认浏览器、Dock 和崩溃报告选项。仅准备期间开放已验证的 loopback CDP，
完成后关闭调试端口、重启并核验真实原生连接；正常采集使用原生接口。
遇到未知设置页面或校验失败明确报告，不通过隐藏偏好跳过设置或取消系统隔离属性。
官方另有 Windows beta，但当前 Python 适配器尚未支持它；Linux/Docker 不运行托管准备。

任一 `OPENBILICLAW_EGO_*` 环境变量表示显式使用外部宿主，托管流程不会修改它。
此时 `OPENBILICLAW_EGO_BROWSER` 指定官方 CLI 路径（未指定时从 PATH 查找 `ego-browser`）；
`OPENBILICLAW_EGO_SERVER_NAME` 选择命名宿主；`OPENBILICLAW_EGO_PROFILE` 指定 Ego
profile ID（默认 `Default`）。环境变量由运行后端的进程继承，见[配置模块](config.md)。
真实测试使用官方 `--user-data-dir` 隔离数据，不导入日常浏览器账号。

历史 Playwright 流程可继续用 `openbiliclaw browser install` 维护完整 Chromium；
`[browser]` extra 仍为兼容别名。桌面包含匹配 Node driver，Docker 保留旧执行环境。
这些依赖可用不代表 Ego 可用，也不触发新网站自动回退。

Ego profile 是登录凭据存储，由官方宿主管理；应用数据只保存私有会话映射。不要把
profile、Cookie、截图或接管输入写入流程版本、模型提示、公共日志或代码仓库。

## 历史 Playwright 验证记录

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
