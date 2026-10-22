# 通用 Web 来源

## 动态网站来源与兼容订阅

新增网站、RSS、Atom 或 JSON Feed 从「聊一聊」开始：用户说明网址与内容意图，Agent 建立草稿、试跑并展示样例；
需要登录时引导到独立的浏览器接管页。确认启用后，每个网站有稳定 `source_id`，按自己的
间隔运行发现任务，并以独立 `target_share` 参与候选池分配。一个网站可以包含多个
workflow；不把这些网站当成旧版 Web 总份额下的子权重。

例如在「聊一聊」输入「把 `https://example.com/feed.xml` 加为来源，关注它的全部更新，
每 30 分钟采集一次，份额设为 2」。Agent 补问缺少的信息，将自然语言意图整理为可复跑
流程，试跑后展示样例与启用确认卡；用户批准才进入调度。之后可在设置的「自定义来源」
调整名称、份额、频率或停用，也可继续通过聊一聊管理。份额影响推荐池分配；内容仍须通过
统一评估，采集成功不保证每一条内容都入池。

Feed 可以直接提交订阅地址，也可从网页中已观察到的 alternate 或明确订阅链接发现。
订阅托管域与文章域分开：`allowed_origins` 仅授权实际订阅请求；`content_origins`
记录最多 8 个已观察到的公共文章 origin，只允许接收 Feed 内的正文和链接，不自动请求
文章页、图片或附件。确认卡展示这两种范围。运行时出现新文章域会保留已授权候选并报告
`degraded`，不提交完整检查点。通过聊一聊指定原来源与流程重新准备、试跑并批准后，
以新版本替换该流程并保留 seen/运行历史，不新增重复调度；仅指定来源仍表示追加流程。
审批时展示旧/新范围，版本已变化的旧草稿需重新准备。无标题但有正文的条目
从清理后的实际正文生成短标题；只读取有界当前 Feed，不保证补齐历史或所有分页。
格式依据：[RSS 2.0](https://www.rssboard.org/rss-specification) 允许 item 仅有 description，
[JSON Feed 1.1](https://www.jsonfeed.org/version/1.1/) 的 title 与 next_url 均为可选字段。
专用平台域仅在明确解析并成功试跑 Feed 后可建为自定义订阅；平台 HTML 仍用原生适配器，
不会继承平台凭据。旧匿名 recipe 的默认专用域拒绝规则保留。

动态工具为 `prepare_discovery_source`（建立持久草稿并试跑）和
`trial_discovery_source`（继续试跑），两者是 `soft_write`；
`activate_discovery_source`（启用）与 `manage_discovery_source`（修改来源配置）为
`hard_write`，均须聊天确认卡批准。设置页的保存、启停是用户显式操作，直接调用
来源 API；归档另有界面确认。完整工具边界见 [Agent 模块](agent.md#动态网站来源的对话与管理)。

三端设置共用 `shared/custom-sources.js/.css`，管理名称、份额、频率、启停、归档、
最近运行和重新登录；类型显示为订阅源、站点地图、网页列表或浏览器网页。
操作某来源后保留其他来源未保存的编辑；操作失败保留当前输入。登录截图轮询与用户输入串行，
会话失效显示重新打开入口。推荐与惊喜 DTO 保留 `source_id/source_name`，网站名用于显示，
内容平台仍是 `web`。API 草稿、试跑、启用和登录接管协议见 [API 模块](api.md#动态网站来源)。
运行记录的 `enqueued_count` 显示为「入队待评估」，仅表示已交给候选评估队列；
不表示已通过评分或进入可推荐库存。旧版 HTTP recipe 的运行计数遵守同一语义。
用户不向聊天或设置粘贴网站密码、Cookie。执行器能力随运行环境报告，不能仅安装
Ego Skill 就宣称具备服务器或 Docker 浏览器能力。

当前浏览器的内容导航与提取严格限定原站 `allowed_origins`。页面中实际观察到的公共
静态资源 origin 可单独加入 `resource_origins`；跨站登录 origin 必须由用户在接管页
明确选择，只允许人工登录期间使用，不扩大后台内容发现范围。普通新网页使用官方 Ego
原生执行器，实际解析成功的 RSS / Atom / JSON Feed 使用 HTTP；不会回退到 Playwright。
Ego 实施 origin、方法与公共 DNS 预检，宿主负责 socket 解析，不能声称具备旧 Chromium
执行器的 DNS pin 保证。真实网站能力以各次验收证据为准。重新登录按钮只为包含
Playwright/Ego workflow 的来源显示，HTTP 公开流程没有登录按钮。

macOS 在部署阶段自动准备 Ego：本机源码安装在依赖完成后、`--skip-start` 返回前执行，
桌面包安装后首次启动及实际 CLI `start` / `serve-api` 在后台补齐未完成环境，不等待聊天，
也不阻塞 HTTP/UI。准备在实际数据库目录的 `ego-managed` 中安装验证过的官方应用、
初始化私有 profile 和 named host、完成原生握手。API、桌面和 CLI 服务启动检查会跳过
已安装且 ready 的环境，即使 offline 也不重新下载或启动；源码安装重跑仍显式准备并
验证握手，必要时启动宿主。失败在同进程不循环，下次服务启动会重试。默认 `create_app()`、
自检和 worker 子进程不启用部署准备，GET 仍只读。

聊天、CLI 或草稿 API 保留未完成环境的补救路径，下载和初始化不占用页面观察的 60 秒
预算。已准备宿主可由实际网页会话或源码安装的显式握手按需启动。准备跳过个人浏览数据导入，不设默认
浏览器、不添加 Dock、不启用崩溃报告；网站账号仍由用户在接管页登录，HTTP Feed 不依赖 Ego。

共享设置中的「准备 Ego Lite」用于显式准备或失败后重试，显示安装、初始化与握手阶段。
按钮调用 `POST /api/custom-sources/executors/ego/prepare`；只在组件可见且 preparing 时
每 3 秒轮询状态，不覆盖来源未保存的编辑。`GET /api/custom-sources` 纯只读，不安装、
不启动应用或首次设置。`setup_state=ready` 与 `available` 分开：已准备但离线可在实际
任务时重新启动；`connection_state=unverified` 需通过实际试跑验证。聊天入口始终可用。
任一显式 `OPENBILICLAW_EGO_BROWSER`、`OPENBILICLAW_EGO_SERVER_NAME`、`OPENBILICLAW_EGO_PROFILE` 非空配置退出托管，界面不显示
托管准备按钮，也不修改该外部宿主。当前只支持 macOS；不支持的环境明确报告，
不会推荐或暗退到 Playwright。旧 Playwright 流程以兼容标签显示，原安装 API 留作维护。

以下章节描述**兼容的匿名 HTTP recipe 路径**。其中「不支持登录/JavaScript」和
旧版 CLI chat 排除仅约束该路径，不代表动态网站来源的产品边界。

动态增量契约位于 [platform-source-contract.dynamic-web.toml](../platform-source-contract.dynamic-web.toml)。
来源族的 `discover=optional-credential` 表示匿名 workflow 仍可工作；某个流程声明
`auth.required=true` 后，登录即为该流程的硬前置，过期必须暂停到人工接管后继续。
注册审计按契约声明的 `workflow_service.py/custom_workflow_producer.py` 识别动态实例路径，
检查实例健康、trial/resume、planner 两轨、实例 claim 和 provenance 测试，不用旧 `web`
固定字典项替代。审计只确认接线与测试节点存在，不执行这些测试或证明真实网站可用。
契约中的 `smoke.sinks` 专指不产生派生投影的 `smoke_only` 预览；正式隔离生命周期/评估
验收会写 draft/run/seen/schedule，并可使用合成画像与 memory，实际写入须记入验收账本，
不导入真实用户历史或账号画像。

历史 Playwright 回归的 CI `web-guided-init-e2e` job 在安装 Chromium 后同时执行
`tests/test_web_guided_init_e2e.py` 与 `tests/test_workflow_executors.py`，覆盖真实浏览器
登录会话持久化、分页、请求边界和结构观察。该受控网站回归不替代外站、Docker 或
桌面安装态验收；真实网站与运行环境的结果单列在[验收记录](../platform-source-acceptance.dynamic-web.md)。

## 旧版公开 HTTP recipe

> Web 是一个 **discovery-only** 的匿名公开来源。用户可以粘贴一个公开 URL，让只读
> authoring probe 识别 Feed、Sitemap 或保守的静态列表结构；探测结果只是待检查的 recipe
> 草稿。正式运行仍只跟随用户显式确认并保存的 `feed`、`sitemap` 或 `listing` recipe，且
> recipe 同时也是网络权限清单。它不是搜索引擎或递归浏览器爬虫，也不读取登录态页面或执行
> JavaScript。

## 旧版 HTTP 路径已实现功能

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| RSS / Atom / JSON Feed | ✅ | 确定性解析公开 feed，保留标题、作者、摘要、链接和上游明确给出的发布时间 |
| Sitemap / Sitemap Index | ✅ | 有界轮转子 sitemap，并按 `detail_fetch_limit` 读取公开 HTML 详情；`lastmod` 只用于 sitemap 元数据，不冒充发布时间 |
| 静态 HTML Listing | ✅ | 使用受限、可审计的 selector plan 解析列表，可有界补取详情页；不运行脚本 |
| Typed recipe 与图形编辑器 | ✅ | 后端只接受固定字段和 `feed/sitemap/listing`，拒绝客户端写入 ID、状态、游标、验证器和其它未知字段；桌面 Web 与插件设置页提供同一安全字段集的创建、启停和删除界面 |
| 旧聊天审批兼容 | ✅ | 既有 `create_source` 审批仍可执行；新聊天网站 / Feed 统一走动态来源 prepare → activate，避免落入旧 Web 聚合份额。已有平台关键词订阅继续使用 create_source |
| 粘贴 URL 自动探测 | ✅ | 桌面 Web、插件与 `probe-web` CLI 只读识别直接 Feed/Sitemap、HTML alternate Feed、同源 `/sitemap.xml` 和保守的重复静态列表；展示样例后由用户采用并再次显式保存，不自动创建或启用 recipe |
| 匿名安全传输 | ✅ | 精确 origin allowlist、逐请求/逐跳公共 DNS 校验、socket DNS pin、robots fail-closed、无环境代理和无 Cookie |
| 后台调度 | ✅ | `WebDiscoveryProducer` 原子领取到期 recipe，按 recipe cadence、权重和有界请求预算补充统一候选池 |
| 状态与去重账本 | ✅ | 每个 recipe 独立保存 lease、健康、HTTP validators、sitemap cursor 和 seen provenance；候选身份仍按 canonical URL 全局去重 |
| CLI 正式补池 | ✅ | `openbiliclaw discover --source web` 复用真实 adapter、producer 与 candidate pipeline，提交到期 recipe 的候选、seen 与完整 checkpoint |
| CLI 只读预览 | ✅ | `openbiliclaw discover-web --recipe-id <id>` 重新校验单条 recipe 后执行，不写候选、seen、cursor 或 memory |
| 来源状态 | ✅ | `/api/sources/status` 聚合全局开关、启用 recipe 数和最近运行健康；从未运行时不宣称 ready |
| 账号能力 | 不支持 | 不接收 Cookie、令牌、登录心跳或账号身份；`state="no_auth"` 只表示无需凭据，不代表取数已成功 |
| 自动保存 / 任意 crawl | 不支持 | probe 不自动建 recipe；没有站内搜索、跨域扩权、链接递归、任意翻页或全站 crawl |
| 登录态 / JS 页面 | 不支持 | 不使用 CDP、Playwright、浏览器扩展或 Ego Lite，不执行 JavaScript，也不绕过登录墙或 challenge |
| 画像初始化 / 站内写入 | 不支持 | Web 内容只进入 discovery candidate；不生成账号行为事件，不修改上游收藏、书签或订阅 |

### 历史聊天审批兼容

以下是已有匿名 HTTP recipe 的历史审批路径。桌面 Web、移动 Web 与插件的新聊天请求
统一走本页开头的 `prepare_discovery_source` → `activate_discovery_source`；新建旧路径审批
会收到纠正指引。CLI `chat` 未接入动态来源 Agent 工具，CLI 用户仍可使用独立的
`probe-web`、`discover-web` 与 `discover --source web` 命令管理旧 HTTP recipe 路径。

```text
历史持久审批卡 → 用户批准 → create_source(source_type="web", ...)
                → build_web_recipe → source_recipes
                → 到期 WebDiscoveryProducer → 统一候选评估 / 推荐
```

历史审批的拒绝与待审批状态不保存来源或开始调度；批准后的执行仍经过同一 compiler、
exact-origin / robots / public DNS + socket pin 边界。同样的 recipe 重复创建会返回已有项，
不会借此重新启用用户已停用的来源。Web 仍为匿名公开 discovery-only；不读取登录态、
不执行 JavaScript，也不生成账号画像初始化事件。

## 数据流

```text
粘贴公开 URL → POST /api/sources/web/probe → exact-origin SafeWebTransport
     │             └→ direct feed/sitemap · HTML alternate · /sitemap.xml · static structure
     │                                      ↓
     └────────────── preview samples + compiler-ready recipe draft
                                            ↓
                                用户“采用”只回填表单（仍未保存）
                                            ↓ 显式点击“添加网站”
POST /api/sources (source_type=web)
        │
        ▼
build_web_recipe() ──字段/URL/origin/预算/selector 校验──→ source_recipes
                                                               │
runtime deficit / cadence ──────────────────────────────────────┘
        │
        ▼
WebDiscoveryProducer ──原子 claim + token-fenced lease──→ WebSourceAdapter.fetch_result()
                                                               │
                                                               ▼
                                                     SafeWebTransport
                                      origin allowlist → public DNS → socket pin
                                      robots.txt → bounded anonymous GET → MIME gate
                                                               │
                                     ┌─────────────────────────┼─────────────────────────┐
                                     ▼                         ▼                         ▼
                              RSS/Atom/JSON Feed          Sitemap + details       Static listing + details
                                     └─────────────────────────┬─────────────────────────┘
                                                               ▼
                           canonical URL → DiscoveredContent(source_platform="web")
                                                               │
                          recipe seen ledger ──new only─────────┤
                                                               ▼
                       discovery_candidates → shared evaluator/admission → content_cache
                                                               │
                                                               ▼
                                      desktop / mobile / extension recommendation cards

每轮结果 ──lease token 校验──→ web_source_state
                               （status / validators / cursor / cadence / counts）
```

正式 Web 取数本身不调用 LLM。LLM 只在候选进入全来源共享 evaluator 后参与相关性评估和
推荐文案；因此模型不能扩大 recipe 的网络权限，也不能替 selector 猜测页面结构。

## URL 自动探测与确认边界

桌面 Web 与插件「设置 → 平台源 → 公开 Web」顶部可粘贴一个网站 URL。CLI 的等价只读入口为：

```bash
openbiliclaw probe-web https://engineering.example.com/
```

probe 固定按以下顺序收集最多 4 个方案、每个最多 3 条内容样例：

1. 直接把所给 URL 作为 RSS / Atom / JSON Feed 或 Sitemap 解析；
2. 若是 HTML，只检查页面内同源 `link rel="alternate"` 声明的前两个 Feed；
3. 有预算时检查同一 exact origin 的 `/sitemap.xml`；
4. 对已取得的静态 HTML 做确定性结构推断。只有至少两个不同的同源内容链接、重复的
   `article` 或带明确内容 token 的容器，且现有 listing parser 能实际重放该 selector 时才给出
   中置信方案。`nav/header/footer/aside` 及 menu、breadcrumb、pagination、sidebar 等导航结构
   明确排除，防止把站点菜单当内容流。

整个 probe 最多 4 次目标资源读取，并共享一次总 wall-clock deadline；每个目标仍经 robots、
exact-origin、逐跳公共 DNS、socket pin、MIME、响应体和 parser 上限。跨 origin redirect 不会被
自动加入权限，Cookie、Authorization、环境代理、LLM、JavaScript 与浏览器登录态都不参与。
错误只返回稳定、截断的安全说明，不回传上游 body、解析地址或其它诊断秘密。
API 进程最多同时执行 2 个 probe；容量已满时立即返回 429 `web_probe_busy` 与
`Retry-After: 1`，不建立无界等待队列。其它安全取数失败收敛为 200 + `no_match`；只有输入契约
错误返回 422。桌面 Web 与 Popup 都用 65 秒客户端 deadline，覆盖后端配置允许的最高 60 秒
probe 预算并为响应传输留出余量。

结果中的 `recipe` 是通过当前 compiler 自检过的可编辑草稿，但不是已保存的权威计划。图形端
点击「采用此方案」只会回填原 recipe 表单并明确提示“尚未保存”；用户仍需检查 origin、预算和
selector，再点击「添加网站」。真正的 `POST /api/sources` 会再次运行 typed compiler。修改 probe
输入会立即废弃旧候选，探测也不写 `source_recipes`、Web state/seen、candidate、memory 或 profile。
因此 probe 在 guided init 和 LLM degraded recovery 中保持可用，而真正的 recipe CRUD 仍受原有
写保护。

## Recipe 契约

Web recipe 通过平台中立的 `POST /api/sources` 创建。例如：

```json
{
  "source_type": "web",
  "name": "Example Engineering Feed",
  "strategy": "feed",
  "config": {
    "entry_url": "https://engineering.example.com/feed.xml",
    "allowed_origins": ["https://engineering.example.com"],
    "interval_minutes": 30,
    "max_items": 20,
    "max_pages": 1,
    "detail_fetch_limit": 10
  },
  "target_share": 4,
  "enabled": true
}
```

### 可编辑字段

| 字段 | 默认值 / 范围 | 语义 |
| --- | --- | --- |
| `name` | 必填，1..120 字符 | 本地显示名 |
| `strategy` | `feed` / `sitemap` / `listing` | 必须显式选择；没有 `auto` |
| `config.entry_url` | 必填 | 公开 `http(s)` URL；只允许标准 80/443 端口，无 userinfo、IP literal 或保留内网主机名；已有专用 adapter 的平台 host 不得借通用 Web 重复接入 |
| `config.allowed_origins` | 输入最多 8 项 | 精确 scheme + host origin；entry origin 会自动加入。重定向、feed link、sitemap child、listing link 和详情页都不能越界 |
| `config.interval_minutes` | `30`，范围 `5..10080` | recipe 自己的最小调度间隔 |
| `config.max_items` | `20`，范围 `1..50` | 单轮候选上限 |
| `config.max_pages` | `1`，范围 `1..5` | 当前用于 sitemap index 单轮子 sitemap 上限，不授权任意翻页或递归 crawl |
| `config.detail_fetch_limit` | `min(max_items, 10)`，范围 `0..20` | 列表/站点地图详情页预算；不得大于 `max_items`，sitemap 至少为 1 |
| `config.listing` | 仅 `listing` 使用 | 确定性静态列表 selector plan，见下节 |
| `target_share` | `4`，范围 `1..20` | 多个 Web recipe 之间的有界公平分配权重，不是全平台 pool share |
| `enabled` | `true` | 单 recipe 调度开关 |

`id`、`source_type`、`created_by`、`created_at`、`last_fetched_at`、运行状态、HTTP
validator、cursor 和 lease 都由后端拥有；客户端提交这些字段或未知字段会收到 HTTP 422。
后端还会写入 `plan_version`，客户端不能覆盖。

更新 recipe 使用 `PUT /api/sources/{recipe_id}`，只需提交要修改的可编辑顶层字段；但如果提交
`config`，它必须是一份能独立通过 compiler 的完整 config，而不是 nested patch。改变
strategy、入口、allowlist、取数上限或 selector 等执行计划时，后端会清除该 recipe 的旧
validator/cursor/seen 状态，防止把旧页面的 checkpoint 套到新权限或新解析计划上。只改名称、
调度间隔、权重或启用状态不会伪装成执行计划变化。删除 recipe 会在同一持久化流程中清理其
Web state 和 seen ledger。

### Listing selector plan

```json
{
  "item_selector": "main article.card",
  "link_selector": "h2 a",
  "title_selector": "h2",
  "summary_selector": "p.summary",
  "author_selector": ".author",
  "published_selector": "time",
  "published_attribute": "datetime",
  "empty_selector": ".empty-state"
}
```

`item_selector` 必填，`link_selector` 默认 `a`。selector 只支持 descendant combinator 与简单
`tag`、`.class`、`#id` 组合，最多 8 段；attribute selector、pseudo-class 和脚本选择器均
拒绝。`empty_selector` 是唯一可声明“列表确实为空”的 HTML 证据；item selector 无匹配且
没有 empty marker 时返回 `schema_changed`，不会把页面改版误记成正常空结果。

## 三种发现模式

### `feed`

接受 RSS、Atom 和 JSON Feed。条目 URL 必须在 recipe 的 `content_origins` 内（旧数据默认使用请求 allowlist）；越界或无可用 URL 的
条目不会获得权限。HTTP `ETag` / `Last-Modified` 在完整成功后进入状态账本，后续可发送条件
请求；`304` 记为 `not_modified`。真正零条目的 envelope 才能确认 `empty`：只要 RSS/Atom/JSON
Feed envelope 非空，却没有任何 usable authorized-origin row（包括 JSON Feed `items` 中全是非 object
值），就返回 `schema_changed` / degraded，不得伪装 affirmative empty 或推进 validator。

每轮最多扫描 `max_items` 个 feed row，但 candidate allocation 可能更小；producer 按 allocation
逐批 handoff。只要本次仍有未交付的 bounded row，`scope_complete=false`，新的 ETag /
Last-Modified 就不能提交；下一轮重取同一有界 window，再由 recipe-scoped seen ledger 暴露下一批。
这样小份额 recipe 也会逐轮越过稳定的 feed 前缀，不会长期饿死排在后面的新条目。

### `sitemap`

接受 `urlset` 或一层 `sitemapindex`。index 每轮最多读取 `max_pages` 个 child sitemap，并用
backend-owned offset 在后续轮次继续；嵌套 sitemap index 明确降级，不递归展开。URL 行本身
不是推荐内容，adapter 只在 `detail_fetch_limit` 内读取静态 HTML 详情并提取文章元数据。
`<lastmod>` 是 sitemap 调度元数据，绝不写成 `published_at`。非空 sitemap envelope 如果没有
任何 usable same-origin URL，同样是 `schema_changed` / degraded，而不是成功空结果；不得推进
cursor 或 HTTP validator。

某个 child 或详情永久失败时，adapter 会继续本轮其它独立分支，并把 backend-owned recovery
frontier 写入状态账本；canonical cursor 与 ETag 仍保持不动。后续轮次从 frontier 继续，但只有
当前 root topology 的全部 URL / child 单元都完成一次无错误清扫，才清除 recovery 状态并提交
validator；root 或 child URL/`lastmod` topology 变化会重置对应 clean-sweep 证明，并从稳定边界
重新开始。空 child、单轮 allocation 改变或 sitemap 条目增删都不能提前把这次恢复伪装成完整
成功。`urlset` 在完整清扫后可以使用 root 的条件请求；`sitemapindex` 每轮仍无条件读取 root 和
有界 child window，因为稳定的 root ETag 不能证明 child sitemap 没有新增 URL。详情或
child 返回 `429` 时立即停止该 recipe，并将 `Retry-After` 交给 scheduler，而不是继续请求同站点。

正常（非 recovery）游标也携带 URL 顺序的 topology fingerprint，不能把上一份快照里的位置数字
直接套到新快照：root `urlset` 的 URL 顺序改变时，当前 entry offset 安全回到 0；`sitemapindex`
中当前 child 的 URL 顺序改变时，只重置该 child 的 entry offset，保留仍然有效的 root child
轮转位置；root index 自身的 child URL 顺序改变时则从 root 的稳定边界重新开始。这样 newest-first
sitemap 在头部插入新 URL 后不会因为旧 positional cursor 跳过新内容，也不会为一个 child 的变化
无谓重放其它 child。normal cursor fingerprint 只跟踪 URL 顺序；recovery clean-sweep 的证明另行
绑定 URL + `lastmod`，两者不能互相替代。

### `listing`

读取一个静态 HTML 列表，按 selector plan 产出链接、标题、摘要、作者和时间；再在
`detail_fetch_limit` 内读取同 allowlist 的详情页补全元数据。登录表单、Cloudflare challenge、
CAPTCHA 或“需要启用 JavaScript/cookie”的页面分别记为 `login_required` / degraded，不会被
解释成空列表，也不会启动浏览器绕过。listing 每轮最多扫描 `max_items` 个匹配 row，并像 feed
一样按 allocation 分批 handoff；backlog 清空前 `scope_complete=false`，防止过早提交 validator
后让稳定前缀长期遮住后续条目。

所有 HTML parser 都有结构复杂度上限：整篇最多 25,000 个 nodes、嵌套深度最多 256，listing
最多保留 512 个 matched item scopes。selector 先构建一次 preorder interval 索引，字段文本提取
另有整次 parse 共享的 node/character 工作预算，避免嵌套 scopes 把有界文档放大成二次 CPU 工作。
任一结构或工作量上限超出都返回 `document_too_complex` 并 fail closed，不会截断成看似完整的空
结果或 partial 成功。

## 传输与安全边界

`SafeWebTransport` 的默认生产路径遵守以下约束：

- 只发匿名 `GET`；请求只带内容协商、项目 User-Agent 和已提交的条件请求 validator。
  `httpx` 使用 `trust_env=false`，不会继承环境代理；cookie jar 在每次请求前后清空，也不发送
  Authorization、页面 Referer 或 URL userinfo。
- URL 只允许 `http` / `https` 与标准端口，origin 必须精确命中 recipe allowlist。每个 redirect
  hop 都重新校验 URL、origin、DNS、robots 和响应上限；越界 redirect 直接拒绝。
- 每次请求前解析 DNS，全部地址都必须是公网地址。生产 transport 随后把这些已验证的 literal
  address pin 给 socket backend，同时保留原 hostname 作为 HTTP Host 和 TLS SNI，关闭“策略
  校验一个地址、实际连接另一个地址”的 DNS rebinding / TOCTOU 窗口。
- 内容请求前读取同 origin 的 `/robots.txt`。404/410 表示没有声明限制；401/403 表示拒绝；
  网络错误、5xx、异常 MIME、redirect loop 或无法安全读取都返回 `robots_unavailable` 并
  fail closed。明确的 Disallow 返回 `robots_denied`。规则采用 longest-match、Allow 同长度优先
  和有界的 `*` / terminal `$` matcher；单规则 wildcard 以及整个文件的规则数、literal octets
  与 wildcard 总量都有复杂度上限，超限 fail closed，不执行可灾难回溯的正则。
- 单资源全流程 deadline（含 DNS、robots、redirect 与 body）、响应字节、redirect 数、
  feed/sitemap 条目数和 HTML node/depth/matched-scope 数都有上限；只接受
  与策略相符的 MIME family。`text/plain` / octet-stream 等通用 MIME 也必须通过小范围内容
  sniff，不能把任意二进制或登录 HTML 喂给错误 parser。
- 错误对外只暴露稳定 code 和截断 detail，不回传上游 body。`429` 保留合法 `Retry-After` 供
  recipe cadence 使用；其它 HTTP、网络、schema 和 access-wall 结果保持可区分状态。

这条策略比普通“先解析 DNS 再让 HTTP client 自己连 hostname”更严格。测试中注入的自定义
transport 是 fixture seam，自行负责其连接行为；上述 socket pin 描述的是默认生产 transport。

默认 transport 使用 `httpx[socks]>=0.28,<1` 构造有界请求，并直接使用
`httpcore>=1.0,<2` 的 network backend primitive 将已验证地址交给 socket 层。`httpcore` 因此是
声明并锁定的生产依赖，不依靠 `httpx` 的传递依赖偶然存在。普通 `pip install .`、`uv sync`、
Docker build 与桌面打包都会自动安装两者；这条匿名 HTTP 路径不需要 Node、浏览器、
Ego Lite 或 crawl 服务。上游 major 版本不在当前兼容范围内，升级必须重跑 DNS pin / TLS SNI / redirect 回归。

## 身份、时间与候选字段

Web item 的 canonical URL 会移除 fragment 和常见 tracking query，排序剩余 query，并规范化
hostname。稳定内容 ID 为 canonical URL 的 SHA-256 前 24 位：`url-<24 hex>`；候选 key 为
`web:url-<24 hex>`。因此相同 canonical URL 即使由多个 recipe 发现，也会在统一候选池按同一
内容去重；recipe ID 只作为 provenance、cadence 和 seen scope，不进入内容身份。

正式候选固定为：

- `source_platform="web"`
- `source_strategy="web-feed" | "web-sitemap" | "web-listing"`
- `content_type="article"`
- `source_recipe_id=<recipe id>` 与 `source_site=<canonical hostname>`

Feed 或 HTML 明确给出的发布时间会经统一 UTC normalizer 写入；缺失就保持缺失，不使用抓取
时间、HTTP Date 或 sitemap `lastmod` 猜测。Phase A 不抓取任意图片，也没有可移植的 view、
like、favorite、comment、share 或 danmaku 指标，这些字段不能由页面文案或 LLM 伪造。

## 调度与状态账本

`WebDiscoveryProducer` 只在 `[sources.web].enabled=true` 且数据库至少有一个 enabled Web
recipe 时参与 pool share。没有 recipe 时，全局开关默认开启也不会预留 Web quota。多个 recipe
先轮换起点，再按 `target_share` 做有上限的公平分配；每条到期 recipe 在执行前单独原子 claim，
避免一批中排在后面的 recipe 提前消耗 lease。

三个表分工如下：

| 表 | 所有者 | 内容 |
| --- | --- | --- |
| `source_recipes` | API / recipe compiler | 用户可编辑计划、开关、权重、创建信息与最近成功 fetch 时间 |
| `web_source_state` | runtime | status、error、ETag、Last-Modified、带 topology fingerprint 的 sitemap cursor / recovery frontier、尝试/成功/产出时间、next fetch、失败次数、完整性、lease 和计数 |
| `web_source_seen` | runtime | recipe-scoped item key、canonical URL、内容 hash 和首次/最近观察时间；默认保留最近 10,000 行 |

网络 I/O 后 producer 会再次续租并核对 token；lease 已过期或被替换的 worker 不能 enqueue，也
不能提交状态。只有 `ok / empty / not_modified` 且 `scope_complete=true` 才推进 validator 和
cursor。partial/degraded 轮次已经安全解析出的行仍可进入候选池，但 checkpoint 保持在上一次
完整状态；候选成功交给 shared pipeline 后才记 seen，enqueue 失败不会烧掉重试机会。

Feed/listing 的 `scope_complete` 还要求本轮 bounded scan backlog 已清空；单轮 candidate allocation
小于 `max_items` 时会重取同一有界 window，并由 seen ledger 分批 handoff，不能仅因这一批 enqueue
成功就提交新的 HTTP validator。非空 feed/sitemap envelope 零 usable row 与 HTML
`document_too_complex` 都属于 degraded/schema failure，也不能推进任何 checkpoint。

## API 与来源状态

| API | 说明 |
| --- | --- |
| `GET /api/sources` | 列出 recipe；Web 行附只读 `runtime` 摘要，但不暴露 validator、cursor 或 lease token |
| `POST /api/sources` | 创建并编译 typed Web recipe；非法权限或字段返回 422 |
| `PUT /api/sources/{recipe_id}` | 在保留后端 ID/owner 的前提下更新可编辑字段 |
| `DELETE /api/sources/{recipe_id}` | 删除用户 recipe 及其 Web state/seen；system recipe 保持不可删 |
| `POST /api/sources/web/probe` | 只读探测一个公开 URL，返回 `ready/no_match`、页面摘要、候选方案和有界样例；不保存任何状态，未知请求字段或非法 URL 返回 422 |
| `GET /api/sources/status` | 纯本地聚合 Web 全局开关、启用 recipe 和最近 observed health；不出网 |
| `GET /api/sources/credentials` | 返回 `form.kind="none"` 的匿名说明，不返回或请求凭据 |
| `POST /api/sources/web/verify` | 固定为 `verify_method="none"`；不会借“测试连接”偷偷访问任意 recipe |
| `POST /api/sources/web/credential` | Web 没有合法 credential kind，不能写 Cookie、token 或 login state |

Web status 的 `state="no_auth"` / `logged_in=true` 是旧客户端的“无需登录”兼容形状；发现健康
必须读独立 `discovery_state`：

- 全局关闭 → `disabled`
- 全局开启但无 enabled recipe，或所有 recipe 尚未得到成功证据 → `unverified`
- 成功与 pending/partial 混合 → `partial`
- 任一 rate limit / failed 按优先级聚合为 `rate_limited` / `error`
- 所有 enabled recipe 均有 `ok/empty/not_modified` 成功证据 → `ready`

状态页轮询只读本地账本，不会触发 robots、DNS 或上游 HTTP 请求。

## 公开 Python API

| API | 契约 |
| --- | --- |
| `build_web_recipe(payload, *, recipe_id=None, created_by="user")` | 校验并编译唯一受支持的 typed Web recipe；`recipe_id` / `created_by` 只能由受信调用方传入 |
| `web_recipe_payload_from_existing(existing, changes)` | 合并可编辑顶层字段，拒绝身份与状态写入；合并结果仍必须再经 `build_web_recipe()` |
| `HTMLListingPlan` / `validate_html_listing_plan(plan)` | 表达并静态校验受限 listing selector DSL，不发网络请求 |
| `canonicalize_web_url(url, *, allowed_origins=None)` / `web_content_id(url)` | 产生 Web canonical URL 与跨 recipe 稳定内容 ID；可选强制 exact-origin allowlist |
| `SafeWebTransport(...).fetch(url, *, expected=..., ...)` | 执行匿名有界读取，返回 `WebFetchArtifact`；安全失败统一为不含 body 的 `WebFetchError` |
| `parse_web_feed(...)` / `parse_sitemap(...)` / `parse_html_listing(...)` / `parse_html_detail(...)` | 对已取得的有界 bytes 做确定性解析；解析或 schema 边界失败为 `WebParseError` |
| `infer_html_listing_plan(...)` | 从已取得的 HTML 保守推断一个可由正式 listing parser 重放的 selector plan；导航 chrome 与不可靠单链接结构返回 `None` |
| `probe_public_web_url(url, ...)` | 在 exact-origin 安全传输和共享 deadline 内生成有界、compiler-ready 的 recipe 草稿；只读且不持久化 |
| `WebSourceAdapter.fetch_result(recipe, profile, limit=20, *, state=None)` | 执行一条 formal recipe，返回含 status、scope completeness、validator/cursor 候选值与 items 的 `SourceFetchResult` |
| `WebDiscoveryProducer.produce_if_due(*, limit=None, force=False)` | 后台 claim / lease / seen / candidate handoff 入口；`force` 只绕过 cadence，不绕过 recipe 开关、权限或 lease fencing |
| `web_source_status(database, *, enabled)` | 读本地 Web 账本的 runtime 摘要，不出网；对外 HTTP status 仍以 `/api/sources/status` 的能力矩阵与 `discovery_state` 为准 |

`discover_feed_urls(document, page_url=..., allowed_origins=...)` 是一个只检查已取得 HTML
`<link rel="alternate">` 的确定性 parser helper；URL probe 会在受限请求预算内消费它，formal
producer 仍不会自动扩展 recipe。helper 本身不出网、不持久化，且产品仍要求用户显式确认保存。

## CLI 正式补池与只读预览

正式手动运行所有到期 Web recipe：

```bash
openbiliclaw discover --source web --limit 30
openbiliclaw discover --source web --limit 30 --force
```

这条命令复用后台的真实 `WebSourceAdapter → WebDiscoveryProducer →
DiscoveryCandidatePipeline`，所以会写 `discovery_candidates(pending_eval)`、recipe-scoped seen、
运行健康，以及完整成功后的 HTTP validator / sitemap cursor。`--limit` 是本轮总上限，各 recipe
预算继续生效；`--force` 只绕过普通 cadence，不绕过全局/recipe 开关、活动 lease 或任何网络
安全门。`ok / valid_empty / throttled` 退出 0；`partial / disabled / no_recipes / error` 退出 1。
通用 runtime 配置必须先通过 CLI 启动门，但 Web HTTP fetch 本身仍不调用 LLM，入池 candidate
由共享 evaluator 后续异步处理。

如只想诊断一条 recipe 而不改任何账本，使用：

```bash
openbiliclaw discover-web --recipe-id <recipe-id> --limit 20
```

命令要求全局 Web 开关和目标 recipe 都已启用，并用当前 compiler 重新校验数据库中的 recipe，
防止旧版或手工写库计划绕过新安全约束。它从空 state 执行一次网络预览，所以不会消费或更新
scheduler 的 ETag、Last-Modified 或 sitemap cursor；也不会写 `discovery_candidates`、
`web_source_seen`、`content_cache`、memory 或 profile。`--limit` 范围为 1..50。

如尚未有 recipe，可先用 `probe-web <URL>` 输出候选，或在桌面 Web / 插件里自动探测并采用草稿；
CLI 不自动保存，图形端采用后也必须显式点击「添加网站」。后台 producer 会按 cadence 补池，
`discover --source web` 可显式运行正式 producer，而 `discover-web --recipe-id ...` 只负责已保存
单 recipe 的只读诊断预览。

## 配置

```toml
[sources.web]
enabled = true
request_timeout_seconds = 15
max_response_bytes = 2097152
max_redirects = 5
lease_seconds = 300

[scheduler.pool_source_shares]
web = 1
```

`enabled` 是紧急总开关；显式创建 recipe 才是实际 opt-in。四个 transport/runtime 数值由配置
loader 和 API 同时收敛：timeout `1..60` 秒、response `65536..5000000` bytes、redirect
`0..10`、lease `30..3600` 秒。`pool_source_shares.web` 只在全局开关与 enabled recipe 同时
存在时生效。

桌面 Web、移动 Web 和 extension recommendation 均能显示 Web 文字卡及策略标签；desktop /
popup 的共享来源 roster 能显示匿名和 discovery health。桌面 Web 与插件 Popup/side panel 的
「设置 → 平台源 → 公开 Web」提供 recipe 编辑器：可保存全局 transport ceilings 与 Web share，
先粘贴 URL 探测并预览候选/样例，再把选中方案回填到表单；也可直接手填。编辑器列出
recipe/runtime 状态，创建 `feed / sitemap / listing` recipe（含 exact origins、cadence、上限与受限
selector），并单条启停或删除；后端 422 原因原样展示。移动 Web 没有来源设置页，按四表面契约
有意排除设置页 authoring probe，不提供独立 probe / recipe CRUD 表单；「聊一聊」仍可通过共享 Agent probe 与审批卡新增来源。CLI `probe-web` 与单 recipe preview 均
保持只读，formal discover 才可写入候选/状态；
`/api/sources` 是所有界面的权威 CRUD 契约。

## 旧版 HTTP 路径排除项与历史 Ego Lite 决策

旧版 typed HTTP recipe 路径明确排除以下能力：

- 探测后自动保存、自动启用或无人确认地调度 recipe；
- 全站搜索、KeywordPlanner 关键词生成、链接递归、无限翻页或通用 crawl；
- 登录态网页、Cookie/token、CDP、浏览器扩展任务、JavaScript 渲染或 challenge 绕过；
- 从网页推导用户画像、账号身份或站内互动数字；
- 任意页面图片代理、原生 deep link 或站内收藏/书签写回。

仓库仍保留历史 `web_extract` 浏览器 + LLM 兼容实现，但 typed Web API 不接受该 strategy，正式
producer 也不调度它；它不是本模块的 canonical Web source 或安全 fallback。

历史 Phase A/B 草稿将 Ego Lite 列为 deferred；该结论仅记录旧版匿名 HTTP 路径的范围。
动态来源将执行器独立建模，HTTP 路径仍不隐式升级权限或回退到登录浏览器。
[Ego Lite 官方下载说明](https://lite.ego.app/download)提供 macOS 与 Windows beta；本项目当前原生适配器只支持 macOS，尚未完成 Windows 验收。
[官方宿主桥接指南](https://lite.ego.app/document/en/docs/custom-agent-harness)要求在应用所在电脑
执行命令。OpenBiliClaw 的 macOS 托管准备负责安装和初始化该本机宿主，不提供远程桥接，
也不把 Linux/Docker 变成原生 Ego 环境；具体可用性仍依据运行环境与实际执行证据。

## 关键文件与测试

- `src/openbiliclaw/sources/web_recipe.py` — typed recipe compiler 与权限/预算校验
- `src/openbiliclaw/sources/web_probe.py` — URL authoring probe、结构推断、候选样例与草稿编译
- `src/openbiliclaw/sources/web_public.py` — 安全 HTTP transport、DNS pin、robots 与确定性 parser
- `src/openbiliclaw/sources/web_adapter.py` — 三策略执行与结构化结果
- `src/openbiliclaw/runtime/web_producer.py` — cadence、lease、公平分配、seen 与候选 handoff
- `src/openbiliclaw/storage/database.py` — recipe/state/seen 持久化与 fencing
- `tests/fixtures/web/` — feed、sitemap、listing 等脱敏 fixture
- `tests/test_web_public.py` / `tests/test_web_adapter_public.py` — transport/parser/strategy 语义
- `tests/test_web_recipe.py` / `tests/test_web_source_state.py` / `tests/test_web_producer.py` — recipe、账本和 producer
- `tests/test_web_source_api.py` / `tests/test_web_source_auth_cli.py` — API/status/CLI 边界
- `tests/test_web_probe.py` / `tests/test_desktop_web_probe.py` / `extension/tests/web-recipe-settings.test.ts` — probe、安全预算、桌面与插件确认边界
- `docs/platform-source-contract.web.toml` / `docs/platform-source-acceptance.web.md` — Web discovery-only 合同与旧 Phase A / Phase B 历史验收台账；不代表当前聊天增量已验收


### 聊天入口的兼容边界与回归

异步 `probe_web_source` 只向 AgentLoop 暴露，输出按当前聊天字符预算保留一个完整创建方案。
旧版聊天的同步 dispatcher 拒绝 Web 创建/启停，明确引导到确认卡或设置页；不会在 Agent
不可用时退回静默订阅。`tests/test_agent_web_sources.py` 验证默认角色、确认前零写入、
批准后的 typed recipe 与候选池、拒绝、非法配置、重复请求和小字符预算。
本轮接手证据见 [聊天 Web 来源验收](../platform-source-acceptance.chat-web.md)。
