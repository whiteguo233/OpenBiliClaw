# Instagram 平台来源验收台账

> **2026-10-06 交付补录**：本轮已获 commit / merge main / push 授权；整合最新 main 的结果见 [交付记录](testing/2026-10-06-instagram-delivery.md)。下文“未提交/未合并”仅表示各历史测试窗口的状态，不能推翻最新回执；合并也不会把此前缺失的真实账号/终止/手机验收自动变成 PASS。

> Integration level：`full`（目标，**完整验收尚未通过**）。2026-10-03 新空库真实初始化完成 5 事件 → 当前模型画像 → 10 候选 → 首批 2 推荐，后续换批补齐其余 3 条。最新最终包已通过 Firefox 匿名 topic/creator 各3条；2026-10-04 Chrome 网络恢复后，同一最终包在重启前后均通过 topic/creator 各2条，账号采集另取得2点赞/2收藏/1关注。结论仍为 **incremental only**：当前 Chrome 有界门禁通过，不等于永久解决代理不稳定或完成全部外部门禁。以下最新验收与 Gate ledger 优先于历史记录。

## 2026-10-05 消费端真实请求复验（含独立 Flutter App）

- **范围/数据**：本轮是消费端验收，复用同日真实初始化的2条Instagram推荐，不重新抓取/生成、不注入fixture。完整生产后端仍在隔离root `openbiliclaw-instagram-final-20261005.VBv1CQ` /28925运行，证据 `/tmp/openbiliclaw-ins-consumption-20261005.s6N23r`。原授权Chrome安装态8文件再次精确匹配最终ZIP；真实图片代理/API/SQLite请求，没有访问日常18420数据。
- **PC Web / 移动网页**：真实卡片封面、作者和推荐文案可见；PC收藏/稍后再看POST成功，390×844移动网页读到已选状态并通过remove请求取消。PC与移动网页打开对应Instagram链接，Chrome页面实际video readyState=4、currentTime>0且非paused；页面无登录墙。PC“聊一聊”真实提交feedback并提示聊天线索已保存，卡片保留，不冒称本轮有聊天模型回复。截图检查长标题与按钮、无横向溢出。移动网页为桌面Chrome窄视口，不是手机实机Safari。
- **插件页面 / 原生侧栏**：插件页面真实收藏/稍后再看及取消成功，“去看看”打开对应Reels并播放。`runtime.getContexts`明确证实SIDE_PANEL，360×870侧栏有真实卡片/已解码图片、无横溢；通过该view的真实按钮执行收藏POST、观察取消收藏状态并删除。侧栏操作使用实际DOM button.click，不等于物理鼠标命中测试，也不拿普通扩展TAB代替侧栏。最终8项安装文件hash匹配；业务请求证据见backend.log。
- **独立手机App**：找到 `/Users/white/workspace/OpenBiliClaw-mobile`，因其已有未提交改动另建 `OpenBiliClaw-mobile-ins-e2e` / `test/instagram-consumption`，基线f9aa02b。Flutter production RecommendView/providers/API/cover decoder消费真实后端，独立测试bundle运行于iPhone17Pro/iOS26.5模拟器，不替换已有App。集成测试1/1通过（构建后20秒）、analyzer零问题；推荐/封面解码、收藏/稍后再看写入回读取消均通过。OS接受外链后Safari实际显示目标Reels落地页；未证明视频播放或拉起Instagram原生App。不是完整App启动/设置流程或物理真机验收，Android无连接设备未测。
- **发现的跨端同步限制**：移动网页取消保存后，已打开的PC卡片仍保留pressed状态；刷新PC后恢复正确。持久化与重新加载一致性PASS，已打开页面的跨端即时同步不通过。本轮只测试，未修改共享同步逻辑，不把此问题藏在总体PASS中。
- **隔离/收尾**：最终saved_memberships=0、native_save_tasks=0、active Instagram task=0；3条正常内容click与1条聊天线索feedback属于本次本地消费写入，原5条bootstrap事件仍在。没有Instagram上游点赞/取消赞/收藏/关注。恢复测试插件原28420连接并关闭自己的Chrome/28925后端；Clash和日常18420不动。手机测试源码/说明保留独立worktree；未合并、部署、签名或发布。

## 2026-10-05 Likes 非空末页增补：上游证据门未通过

- 用户要求补齐非空末页判定；沿用独立 Instagram worktree，未修改生产逻辑或把无新增改成 complete。证据目录 `/tmp/openbiliclaw-liked-terminal-20261005.GFphDi`。
- 在原授权 Chrome profile 只读打开带任务隔离标记的 Likes 页面，被动观察原生 `/async/wbloks/fetch/`：`liked_media_screen` HTTP200。响应没有观察到 has_next_page/has_more/cursor/next_max_id 类字段或 liked_next 字样；页面树包含非空媒体绑定，但没有明确末页文案。Bloks data 中存在多个未命名布尔状态，不能将任意 false 当作终止证据。
- 原生鼠标滚动后仍只有同一个 screen 响应，没有 liked_next；main 子树没有溢出且可滚动的容器，document 高度等于 viewport。该证据排除了本小样本“明显漏滚内部容器”的解释，但不证明完整历史或一般分页协议。未点击选择、取消赞、筛选或任何互动操作，没有重构私有 POST 或执行 Bloks 动作。
- 现有 parser/executor 定向10项回归通过，覆盖明确空态、非空停滞、限流、触顶和身份变化；它们不补足真实非空末页证据。按上游证据门保持 partial，后续需要授权账号的真实有下一页/末页对照，才能确定终止状态与绑定关系；不能基于字段缺失、短列表或滚动静止新增完整性主张。本轮不升级该 gate。

## 2026-10-05 最终包完整初始化与较大公开样本复验

- **范围/产物**：继续既有 full 接入的验收，不新增生产功能。独立 root `/tmp/openbiliclaw-instagram-final-20261005.VBv1CQ`、空业务库、28925服务、worktree Python import；当前主项目有效 LLM/network 配置，其他来源及自动调度关闭。原授权 Chrome profile 的8项安装文件匹配最终 ZIP `73de4c2916e2494529b01689a6a61abb0d9898c692127d28d5d790c8ada05fdf`，permission/login=true。临时启动配置已被系统清理，重新建立启动配置，未复制 Cookie。
- **最终包四阶段 PASS（有界/partial）**：真实 popup 仅勾选 Instagram，UTC 10-04 16:30:54–16:33:44（本地10-05，170秒）：2 like / 2 favorite / 1 follow → 新画像 → topic7/creator3共10候选 → 2条推荐实际显示。7条低分、1条已看按正常策略拒绝。当前路由的 `deepseek-v4-flash` 偏好/画像和 `sensenova-6.8-flash-lite` 评估/推荐文案均成功；没有 fixture、旧库、阈值调整或模型替换。首次登录心跳未到时409拒绝，心跳到达后正常启动，初次失败保留。
- **账号终态**：bootstrap_limit=20；saved/following complete，Likes连续无新增后 `progress_stalled`，整体 completed/partial、`instagram_degraded`。不把“初始化有推荐”解释为完整历史采集成功，也不因本账号只有5条记录而宣称大样本账号分页通过。
- **较大公开样本与隔离**：topic/creator各30条，max_pages=3，均 observed/cursor_observed=true、partial/item_cap_reached。第一轮严格脚本 exit1，content_cache在窗口内变化，且一条推荐在同窗口被标记notified；原失败 `multipage.log`/`multipage.json`保留，未用推断升级通过。关闭popup后同限额有界复测 `multipage-quiet.log` exit0，仅instagram_tasks变化，全部业务/模型表hash不变。30条和游标证明较大非空样本，不单独证明真实后续游标请求次数或末页。
- **副作用子审计**：完整init窗口被动记录请求类别与错误码，没有保存认证头、账号ID或响应正文；未观察到明确like/save/follow写端点，但GraphQL/Bloks及站点分析请求未全部语义分类，故不能标全站无副作用PASS。discovery后再次真实smoke读取2/2/1，与初始采集的item身份逐项一致，saved/following complete、Likes仍partial；`account-after.log` exit0，业务表全不变。该子证据覆盖可见已采集集合，不覆盖未采到的Likes历史或平台内部状态。
- **自动化与限制**：本轮54个Instagram定向回归通过，source audit通过；无生产代码修改，不冒称重跑前轮8484后端/1535扩展全量。用户明确只有当前账号、先完成其他项，双账号与Firefox登录态仍待条件；Meta生产许可仍是外部门禁。未提交/合并/部署/发布，不改Clash或日常18420服务。

## 2026-10-04 Chrome 最终包冷启动复验

- **范围与环境**：仅处理 Chrome 既有阻塞；复用用户授权测试 profile，最终 v0.3.204 ZIP SHA-256 `73de4c2916e2494529b01689a6a61abb0d9898c692127d28d5d790c8ada05fdf`。重启前后8个安装态文件字节一致且匹配 ZIP，host permission/login 均 true。隔离 root `/tmp/openbiliclaw-chrome-repair-20261004.ei4kVb`，完整 worktree 生产服务监听28924，空业务库、当前主项目 LLM 路由、其余来源与自动调度关闭；未调用模型、未复制账号数据。
- **网络与测试准备**：开始时同一7897代理独立 curl 已 HTTP200/TLS verify0/CONNECT200；普通 Chrome 作者页 HTTP200。本轮未改 Clash、共享节点或 Chrome 代理设置，因此结论是连接恢复后验证通过，不是已定位并永久修复此前 TLS 断连。最初隔离配置缺模型导致503，补齐当前项目模型配置后健康检查正常；一次启动竞态 connection-refused 及原失败日志保留，不计为上游验收失败或成功。服务恢复间已有一个 topic 任务自动完成，后续显式 kick 再执行受控复验，不声称整轮均零人工唤醒。
- **真实 discovery 两轮**：`first-ready-retry.log` 与 `cold-restart.log` 均 exit0；完整关闭再开启原 Chrome 后仍 topic=2、creator=2，completed / partial / item_cap_reached、response_observed=true。保持 max_items=2、max_pages=1，无 fixture、绕过校验或共享节点切换。前轮只 auth_state/instagram_tasks 变化，冷启动轮仅 instagram_tasks 变化；业务表/模型记录为0、active_tasks=0。
- **账号采集补验**：`bootstrap.log` exit0，真实2点赞/2收藏/1关注，response_observed=true；following complete，liked/saved partial，有界 max_items_per_scope=2、max_pages_per_scope=2。任务 smoke_only=true/profile_update=false，仅任务表变化；不冒充新一轮画像、推荐或全量历史初始化。
- **结论与收尾**：Chrome 最新安装包的有界 discovery / 冷启动 / 账号采集当前通过；Oct3失败保留为历史证据，长期网络稳定性和其他 full 门禁不变。测试 endpoint 恢复28420，任务状态与任务页均清空，退出本轮 Chrome 和28924服务；Clash GUI/core及日常18420服务不动。无生产代码修改、提交、合并或发布。

## 2026-10-03 剩余实现、真实闭环与最终稳定性边界

- **范围与隔离**：沿用 `feat/instagram-source` / HEAD `6975e9e9` 独立 worktree，不改 main、日常 18420 服务、Clash GUI/core 或共享节点。证据 root `/tmp/openbiliclaw-instagram-finish-20261003.dINT3I`。新 `live/` 的配置来自当前有效模型/embedding/network 配置，不复制 DB、事件、画像或候选；分支仍不识别 main 新增的 embedding-cache 与 scheduler LLM-budget 字段，只承诺支持字段有效值一致。另建空 `anonymous/`，不复制 Cookie。
- **新鲜 guided-init**：UTC 06:06:46–06:12:00，314 秒。实际授权 Chrome popup 仅选 Instagram：2 like / 2 favorite / 1 follow → 5 canonical events；saved/following 取得完整末页，Likes 因缺可靠终态仍 partial。正式 topic/creator 按日预算 7/3 保留 10 候选；5 入池、4 低分拒绝、1 已看拒绝，首批展示 2 条。真实 `soul.preference`、`soul.profile_build`、`discovery.evaluate_batch`、`recommendation.write_expression` 均成功，实际模型为当前路由中的 `deepseek-v4-flash` / `sensenova-6.8-flash-lite`，没有改阈值或替换上游 fixture。见 `live/init-final-check-audit.json`；初轮仍用停滞修复前插件，不冒称最后包重新执行了全部初始化。
- **普通换批闭环**：`POST /api/recommendations/refresh` 现在接入 Instagram producer、显式小批 flush 评估和有界文案，不依赖 scheduler。保持预算、配额、共享锁与 admission；copy receipt 已有 owner 时不重复调用，评估/文案失败输出安全错误。本轮实际点击 popup 换批，在 7/3 预算耗尽时明确失败且无假 busy，同时完成原先 3 个 pending-copy 条目并实际展示。只新增 1 个本地 reshuffle 事件；这证明正式入口/预算失败/已有文案恢复，不冒称当次按钮又拿到新内容。9 个新增手动恢复回归及共享回归通过。
- **Likes 与时间边界**：实际小样本在 20 页预算下此前会重复身份读取直到 page cap；现连续 3 次非空无新增即 `progress_stalled`，保留 rows/partial，不推断移除或完整历史。身份/私有请求先等限速再启动 20 秒超时；bootstrap idle 按间隔调整到 90–185 秒，仅 accepted durable progress 续期，12 分钟 absolute 不变。虚拟时钟红绿复现 30 秒限速的过早 abort 和原 90 秒 idle 缺陷。最终执行器 Chrome `fetch-instagram --force --wait-seconds 180` 再次实际取得 2/2/1，错误精确保留为 `instagram_liked:progress_stalled`；前后全表 hash 仅 auth_state/instagram_tasks 变化，画像、事件、候选、推荐、LLM 均不变，见 `live/{before,after}-final-smoke-audit.json`。
- **Firefox 轮询争锁**：已授权 host permission、无 sessionid 的 retained Firefox 152.0.1 曾有 pending 任务、多轮周期 wake 却零 Instagram next-task；把既有 poll alarm 错开后即得到真实匿名 topic 3 条。诊断不作为产品修复：现争锁失败另设 30 秒 durable retry alarm，重走恢复、权限、mutex 与 claim；不借用结果 outbox，也不增加上游频率。回归先红后绿，独立 reviewer 另验证无权限零 claim、20 次重入仅一次请求、心跳不续 deadline、绝对截止只提交一次。最终 Firefox 包重新安装后，creator 自动被领取并终结，未再人工触发 alarm。
- **最后包真实结果（保留失败）**：Chrome 话题任务 `response_envelope_unobserved` / 0，creator partial / 2；Firefox 无登录 topic partial / 3（争锁修复前同一执行器），最后包 creator `response_envelope_unobserved` / 0。分别见 `live/discover-smoke-final.json` 与 `anonymous/smoke-results.json`。Chrome 正向双路径脚本 exit 1；Firefox终态/隔离脚本 exit 0 **不等于**两条匿名正向路径通过。两库均无 active task，业务/画像不变；Firefox业务表和模型记录始终为0。没有刷接口、复制登录态、模拟成功或把登录/挑战视为空。
- **封面功能与 SSRF**：新 `runtime/image_network.py` 对每跳 DNS 全量公网检查并固定数字 IP，直连/HTTP(S) CONNECT/SOCKS 保留原始 Host、TLS SNI 和证书校验。真实对照定位本机 DNS 返回错误公网地址使固定 IP TLS 失败；仅改为通过同一代理的固定 Cloudflare DoH 公网答案后，生产抓取恢复 200/image/jpeg、普通下载与加固抓取均 59,736 bytes，实际 popup 2/2 封面加载。103 个 image/API/security 回归覆盖混合内网、特殊地址、rebinding、跳转、代理、DoH 错误/超量/不匹配；安全攻击场景使用受控网络流，不冒称对真网内网攻击。新增第三方 DNS 数据流已披露于 privacy/config/安装文档。
- **独立检查与本地产物**：独立审查提出的两个计时缺陷均已修复；最终追加轮询复核 50 项 focused + 独立 deadline harness 1 项通过，未确认新缺陷。扩展最终 1496/1496、TypeScript、两浏览器 build、各 21 个资产与 ZIP CRC 通过；安装态 Chrome 8 文件、Firefox 4 文件 hash 精确匹配最终包。v0.3.204 Chrome SHA-256 `70eea31467d3df36aff96ed2eb352f1274737cb3aab0c211bfba782207075d68`；Firefox `a916084e4238700740cc7c284869f5592ec4cec21d3e8c10f97dea82db8aca85`。Python wheel 本地构建成功，不等于发布/部署。
- **自动化最终后端证据**：冻结后端 `pytest-final-frozen.log` **8484 passed / 60 skipped**，1518.81 秒、exit 0；早先完整轮次8483/60与失败码20项亦保留。旧滚动测试用真实 wheel 后有界等 scrollTop 变化替代固定80ms，未改生产CSS、滚动动作或断言。Ruff check/format 627文件、MyPy 263文件、source audit 34 PASS / 14 MANUAL / 10 N/A / 0 missing及来源指标6/6通过，manual行不伪装为自动验收通过。
- **首屏竞态继续修复**：最后有界Chrome topic从created到failed仅3秒；普通同路径页面稍后实际出现24个帖子链接，虽然另外有站点WebSocket连接关闭，不能据此把内容失败全归为代理。执行器单页仅等1.5秒、且末页仍滚动；现首次额外被动等待最多20秒，不续deadline、不请求新页，末页或始终无envelope停止滚动。迟到有效响应、无响应、迟到429、SPA登录/验证跳转5项红绿通过；已知页面失败优先于通用route mismatch，避免错误分类退化。保持原失败日志，修复后真实结果另补记。
- **未闭合边界**：最终真站双路径稳定性、大样本多页、真实第二账号切换、Firefox 登录账号链路、完整页面自有状态变更审计及 Meta production permission 仍不能由本次代码或合成测试替代。提交、合并、版本升级与发布未获明确请求，未执行。

### 首屏修复后的中间验收

- **Chrome 两路径闭环通过**：`live/discover-readiness-final.json` / 同名 log，最后包实际 topic=2、creator=2，均 `partial/item_cap_reached`、response_observed=true、脚本exit0。max_pages仍为1、max_items仍为2，未扩大限额；前后仅instagram_tasks变化，全部画像/事件/候选/模型/库存不变，无active任务。该结果取代上面的首屏竞态失败作为当前执行器证据；原失败文件保留。
- **Firefox 限制仍保留**：`anonymous/readiness-smoke.log` 在修复首屏等待后的包得到topic=3、creator仍failed/unobserved，后者领取后23秒终结，非超时遗留。最终迟到登录/验证分类小修的ZIP再次实装/hash匹配；未伪称有已登录Firefox或该分类小修又正向跑通creator。匿名数据root只有auth/task变化，业务与模型记录仍为0。
- **Recent Searches 子审计通过**：实际站内只打开搜索面板、不输入或提交；`recent-searches-before.log` 与 `recent-searches-after.log` 的标题/空历史/空输入三项均true，覆盖该窗口内的bootstrap与首次discover smoke。这个子证据不等于验证所有站内状态、分析日志或完整初始化窗口，完整upstream mutation门仍待补。
- **最终自动化/安装态**：扩展最终 **1501/1501**，五项readiness回归含迟到login/challenge均通过；独立审查再跑50项并确认21.5秒无响应终止、零等待progress、DOM-only空提示不升级empty、partial/login保留rows、3页最多2次scroll。双构建各21资产、ZIP CRC、Chrome安装态8项/Firefox4项hash通过。最终ZIP（v0.3.204）Chrome `d3b6dc5fa04d2bd5b4adf62cf2f0ee5edea73bec665c199175d46879fea04a35`，Firefox `0bf44456c0836dbc2472fdf5e2f1cb6c05bc192cd0a2231e4b9d2e18d8a43966`，取代本节中间包。924个src/tests/extension源码测试文件树hash `336381717d535fe2e0f05cbe59cfa919020961303fb7ed39a9400764763db3f9`，证据 `final-artifacts.json`。

### 匿名 SSR 身份修复后的最终安装验收

- **定位并修复真实 creator 误拒**：最终前一包的严格正向脚本 `anonymous/creator-delivery-smoke.log` 为 exit1 / `creator_not_public`，不再把它归为无响应。任务页结构证明公开 profile 与12条媒体的作者 `pk↔pk`、`id↔id` 各自全部一致，但两字段彼此不同；不保存原始响应或真实编号。SSR白名单与拆分carrier已接通，错误来自混淆两种身份字段。现 profile/author 共用严格用户键解析，优先有效pk、非法pk不回退，只比较同字段；两侧pk冲突不能靠相同备用id洗掉，已证实的id→pk关系归一为canonical author。新的未知/私密/无效或截断证明会撤销旧缓存，同payload所有matching profile必须一致公开。
- **回归和独立复核**：真实形状的MAIN初扫、21项身份/反例和强弱SSR证明递归均先红后绿。相同账号证据只合并增强；扫描禁止重入且最多初扫+一次补扫，早到媒体仍可接纳。最终六文件47/47、扩展全量 **1535/1535**（26.70秒、exit0），TypeScript及双构建通过；独立review确认所有已发现反例关闭、原递归复现只扫描2次。中间全量曾有未修改的Reddit native-save超时测试1533pass/1fail，日志 `extension-final-verified-tests.log` 保留；最终全量通过，不修改该测试或掩盖原失败。
- **Firefox 匿名双路径真实通过**：152.0.1、host permission=true、sessionid不存在；直接安装最终ZIP，运行manifest/worker/content/MAIN四项hash匹配。`anonymous/delivery-smoke.log` exit0，topic=3、creator=3，均completed/partial、item_cap_reached、response_observed=true。保持max_pages=1/max_items=3和原日预算；正常poll领取，无人工alarm唤醒、无注入上游fixture。前后仅instagram_tasks变化，active=0；所有事件、画像、候选、推荐、LLM记录仍为0。这是匿名独立profile门的PASS，不等于Firefox登录账号初始化或大样本通过。
- **Chrome 最新失败如实保留**：同一授权profile重启加载最终包，permission/login仍true，8项运行资产hash匹配；`live/discover-delivery.log` exit1，topic/creator均failed/0、send_message_failed_after_reload。普通作者页独立返回ERR_CONNECTION_CLOSED；仅为本次测试进程显式指定同一7897 HTTP代理后仍失败，未再入队重试。独立curl同代理CONNECT=200后TLS失败（exit35 / HTTP000），见 `chrome-network-curl-control.log`。因此不是匿名creator解析红灯，也不能拿Firefox成功或Chrome中间包成功当成最新Chrome通过。两任务只改变任务表，画像和所有业务数据不变、active=0；没有关闭证书验证、切换共享节点或重启Clash。
- **最终产物和静态门**：两包仍为v0.3.204，Chrome SHA-256 `73de4c2916e2494529b01689a6a61abb0d9898c692127d28d5d790c8ada05fdf`，Firefox `807cba9bdb2a1701edb822ba15c733464a84c7aecfe5ce4cfddc06562d1babb9`；ZIP CRC、各21资产、Chrome8/Firefox4安装文件全部匹配。927个源码/测试文件树hash `fd5958e2cdad347565ca034c8882a710efe427ef7707326ced6f04b7484aeb17`，见 `final-artifacts.json`。最终相关Python契约74/74、审计34 PASS/14 MANUAL/10 N/A/0missing、指标6/6通过；后端源码未再变化，8484pass/60skip全量仍是冻结后端证据。
- **仍需外部条件**：当前Chrome出口的新连接恢复与最终包复测；Firefox同浏览器登录；更大授权样本/多页末页；真实第二账号的隔离拒绝；完整上游状态审计；Meta生产使用许可。Recent Searches仅早先窗口的子审计通过，最后窗口未观察到面板标题，不能升级为完整审计。已请用户在保留Firefox窗口登录，不索取或复制Cookie；没有为了验收制造上游互动。提交/合并/bump/发布不在本次授权范围。
- **安全清理与最终数据核对**：Chrome/Firefox 测试扩展均恢复原 endpoint 28420，durable task state和任务tab均为0。正常关闭本轮Chrome，SIGTERM仅停止隔离后端71940/64688，28920/28922无监听；Clash GUI/core571/866、日常后端52548及原Firefox91887保持运行，未导航打断Firefox可能的登录操作。main仍干净且HEAD=5daad72c，任务worktree保留未提交改动。`live/after-delivery-final-audit.json` 对比较早bootstrap后审计，画像hash、事件、候选和推荐不变；长驻留窗口除auth/task外，llm_usage从12增至14（soul.speculate、soul.avoidance_speculate各一次成功），因此不将整个驻留窗口说成零模型调用。每次有界discovery自身的前后审计则只改变任务表，最后所有任务均已终结。

## 2026-10-02 页面资源故障与失败空态修复

- **本轮范围**：`capability-increment`，修复 discovery 资源连接问题的诊断/恢复和失败后的错误可见性；沿用 Instagram worktree，不关闭或重启 Clash Verge，不改上游互动、模型路由或七项暂缓外部门禁。本轮证据 root 为 `/tmp/openbiliclaw-instagram-fix-20261002.VawE7i`，原始请求、账号和配置仅留本机。
- **网络故障已独立复现**：从上轮失败日志提取同一公开 `static.cdninstagram.com` 资源，仅记录 URL 的 SHA-256。通过现有 7897 代理分别执行默认 HTTP、HTTP/1.1 与 SOCKS 远端 DNS，三次均 CONNECT=200 后 TLS 连接失败（curl exit 35），HTTP=0、下载=0；`network_probe.py` exit 1 / `network-before.json` 保留红灯。该反例不依赖浏览器、扩展或 LLM，且没有关闭证书验证。
- **当前出口仍有间歇故障**：收尾复查 `network-current-final.json` 的默认 HTTP 一次成功（200 / 1,132,378 bytes / 正常 TLS），HTTP/1.1 与 SOCKS 两次仍连接失败，脚本总判定 exit 1。不能再笼统描述为每个请求均失败，也不能凭一次成功标为稳定恢复。Clash 只读规则/选择/运行配置快照前后相同；未改共享路由。预先准备的 `live/` 仍仅有当前有效模型配置、没有创建业务库，不将准备阶段标作新鲜端到端已执行。
- **可用备用路径已验证，但尚未替用户切换**：本机代理 API 只读检查显示当前「日本直连Pro」在同一资源探测失败，「新加坡直连Pro 2」成功。独立临时 Mihomo 进程仅使用已有备用节点，两次实际 GET 均 HTTP 200、TLS verify=0、下载 1,132,378 字节且内容 hash 一致，见 `isolated-sg2-result.json`；临时子进程、端口、含节点凭据的配置与响应正文均已清理。实时选择和运行配置 hash 未变；Clash GUI/core 仍运行。该分组同时承载其他海外流量，已询问用户是否允许临时切换，未得到明确答复前不执行。备用节点成功不等于当前默认路径或完整 discovery 已恢复。
- **反馈修复**：`discovery_failure_message` 从最新 init 失败终态投影安全恢复提示，不透传账号、URL 或上游异常。待处理信号不再被当作运行状态；运行→失败实时事件重绘空态。锁占用/未执行的 success、仅规划关键词的时间戳不会清掉首轮故障。首次观察真实可用库存/有效推荐后，单条 `init_discovery_resolution.json` 凭据有界持久化 run_id，库存消费/重启不复活旧错误；新 init 不继承旧凭据，退休历史推荐与过期库存快照不能误清新失败。GET runtime-status 因此会在首次确认恢复时做一次本地 reconciliation 写，不改原 init 诊断。Instagram 的显式恢复入口仍为 `discover --source instagram --force`，本轮没有将普通“换一批”扩成 Instagram 重试编排。
- **实际失败 UI 与实时流**：从上轮真实失败数据作 SQLite backup，隔离运行完整生产 API 于 `127.0.0.1:28921`，禁止后端 outbound `socket.connect`；只访问本机三端 UI，不注入上游成功响应。实际安装 popup、desktop、mobile 均显示“内容发现未完成”及“画像已保存”，错误不再误标为“最近主题”或“现在在忙”。测试专用服务从真实 runtime 的手动刷新完成逻辑注入一个受控异常，经真实 WebSocket 在同一 document 里验证 running→failed；恢复原方法后的无执行 success 仍保留首轮错误。证据 `failure-ui/ws-{running,failed,noop-success}.log`、`runtime-noop-success.json`。这组是合成故障生命周期测试＋实际 UI/API，不是新空库、新 Instagram 或新模型的端到端成功。
- **首次订阅窗口另有真实反例**：最终 backend 重启后的快速 popup 加载测试，在 HTTP 读到 running、但末尾 WebSocket 尚未订阅时发生 failed，首次连接未补读，故主空态长期留在 running。`reviewed-lifecycle-stages.log` 为红灯、同刻 API 已 failed；这不是用更长等待就能修复的产品行为，也不误归因为已经证实的 HTTP 覆盖新事件。桌面后台页本来会暂停实时连接，最终前台生命周期测试必须逐页 foreground，不覆盖浏览器 visibility API。订阅补读修复与最终产物证据另在本节收尾记录。
- **渲染、构建与数据边界**：420px popup、1440px desktop、390px mobile 截图已检查，375/768/1024 等补充尺寸均无整页横向溢出，详见 `failure-ui/visual-checks.json.log` / `responsive-checks.log` 和忽略目录 `output/playwright/instagram-failure-fix-20261002/`。Chrome 安装态的 popup JS/helpers/HTML 三项 SHA-256 精确匹配新包；v0.3.204 Chrome ZIP `17f2192252554de664ddb3b0cf4f006b5a75677aff0c67c6bbd1dcfda2a5c261`、Firefox ZIP `92ae82a57db4eb2d2c0dbd9ddb970e04411a51b1499e366b73f6acf2485c76f2`，双包各 21 个 manifest 资产、CRC 与源码一致性通过。Firefox 本轮仅构建、不冒充重装真机；版本不变。`failure-ui/data-audit.json` 对照原始 62 张表，仅布尔登录心跳所在 auth_state 改变；事件5、画像 hash、任务3、原init、LLM usage2、0候选/推荐与native-save状态不变，无 active Instagram task。
- **完整后端回归保留失败**：最终后端源码的 `pytest-full-reviewed.log` 为 **8428 passed / 60 skipped / 1 failed**，872.56 秒、exit 1、5224 条 warning。唯一失败为未改的 `test_desktop_dialogue_layout_e2e.py::test_pending_inbox_is_bounded_and_independently_scrollable`，滚轮后固定等 80ms 的 `scrollTop` 仍为 0；该文件原样独立复测三轮各 **6/6 passed**（56.92 / 72.88 / 78.81 秒），未修改生产 CSS 或断言。支持时序波动，不把原始全仓运行改写为全绿。之前两次为等候审查修复而主动中断的全仓日志亦保留、不计最终通过。最终 Ruff check/format（624 文件）、MyPy（262 文件）、注册审计 34 PASS / 14 MANUAL / 10 N/A / 0 missing 均 exit 0。
- **首次订阅最终修复与实机闭环**：popup 的真实 stream client 在每次连接后补读一次权威 runtime-status；连接归属和生命周期 generation 阻止迟到终态倒灌，数量事件只保留固定字段覆盖层，不再丢掉尚缺的失败终态。公开 client 三项有效红灯、count-only 原始反例和真实 popup 回调回归均转绿，最终定向 111 passed。最后安装版使用同一个快速启动脚本（popup 不额外等待 WS ready），连续两轮在三端同一 document 中通过 running→failed→无执行 success 仍保留失败；没有“正在补货”残留。见 `failure-ui/ws-final-installed-lifecycle{,-repeat}.log`，两次 exit 0；是受控失败的真实 API/WS/UI 验证，不是新的 Instagram discovery 成功。
- **最终增量与产物**：最后前端增量完成后，扩展全量 **1490/1490**（38.07 秒）、14 个相关 Python 界面/结构文件 **256 passed**（28.05 秒）、TypeScript、Ruff、MyPy 和两套 build/21 资产/ZIP CRC 均通过。后端没有再次改动，不将其全仓那一次布局失败改成通过。最终 997 个源码/测试文件的树 hash 为 `1507784e457d6d83406b5c6692ea8ee4bd7e86d0456f52766cc4339e36132ba5`，验证前后无漂移。最终 Chrome ZIP `f48202e449ac984decc76647825916014afcb48d31ced6715d52d7472bf2c7c5`、Firefox ZIP `672673cbf519ef50ccf84906263542101072f8a97a79512b17d78829eb1e7533`（均 v0.3.204）取代上述中间包；`installed-ws-final-check.json` 确认安装中 manifest / worker / Instagram content / MAIN / popup 四文件共 8 项逐字节匹配最终 Chrome ZIP。`final-checks/validation-summary.md` 与 `artifacts-ws-final.json` 保存完整命令、退出码和产物证明。
- **收尾与判定**：最终三端截图另存为 `*-final.png`，均无横向溢出或假 running；已恢复本测试 Chrome profile 原 endpoint 28420，再正常关闭本轮 Chrome 与隔离后端 PID 73047，28921 无监听。`data-audit-cleanup.json` 再次确认原事件/任务/画像/LLM/推荐等业务证据不变，只有正常布尔心跳 auth_state 改变，无 active Instagram task。Clash GUI/core PID 571/866、日常后端 PID 52548、原 Firefox PID 91887 仍在运行。当前修复为 **incremental only**：失败反馈已修复并实机验收；网络切换与新鲜 discovery 复验仍 **BLOCKED（待共享分组切换确认）**，备用路径成功不能升级该门。未合并、提交、发布或部署到日常服务，七项暂缓门禁保持不变。

## 2026-10-02 10:58 UTC 再次真实请求：事件与画像通过，发现阻塞

- **隔离与当前配置**：新空 root `/tmp/openbiliclaw-instagram-live-repeat-20261002.m8wl5o`、完整生产后端 `127.0.0.1:28820`，沿用 Instagram worktree HEAD `6975e9e9` 及未提交修复。独立 `independent-baseline.json` 确认业务/任务表与 embedding cache 为 0，画像不存在，没有复制 DB/事件/画像/候选。模型与 embedding 的有效配置逐字段等于当前主项目；分支仍不识别 main 的新增 embedding 容量与 scheduler LLM-budget 字段，因此不是宣称两个运行时完全等价。保留 source cap=2、topic/creator 预算 7/3、请求间隔 3 秒，scheduler、incremental、native auto-sync 关闭。
- **已安装的真实登录环境**：复用此前用户授权的 Chrome profile，不复制 Cookie，不使用无登录临时浏览器。v0.3.204 扩展 ID `eikbajehgamillcimgogeobbngdemcla`，Instagram host permission=true，运行中 manifest/worker/content/MAIN 四项 hash 全与冻结 Chrome ZIP 字节一致，见 `installed-provenance.log`。Chrome ZIP 仍为 `124d307363ffdbbd470a44df10aa88222a680be06f2173778324b1f547376dc1`。仅调用扩展现有 endpoint 配置 helper 将本测试 profile 指向 28820，未保存旧缓存的整份配置；这不是本轮“界面保存配置”证据。预先存在的 Firefox 仍指向 28420，不参与本轮。
- **首次完整请求结果**：从实际 popup 取消 B 站、勾选 Instagram 并点击开始。新鲜登录心跳由扩展自动上报，任务重新验证同账号身份后，真实读取 2 like / 2 favorite / 1 follow，5 个 canonical event 与任务原始规范化 items 精确一致。stage 1 为 partial-cap warning；当前 `deepseek-v4-flash` 的 `soul.preference`、`soul.profile_build` 两次真实调用成功，stage 2/3 为 ok，新画像文件确实生成。没有 fallback 成功主张。两个 liked item 上游规范化结果未提供作者，不把“事件相等”表述为所有字段均完整。
- **真实失败，不沿用旧成功**：正式 topic `technology` 任务在 UTC 10:59:41→11:00:46 返回 failed / `response_envelope_unobserved`，response_observed=false，creator 没有获得种子而未执行；候选、cache、推荐均 0，未调用本轮 eval/推荐表达模型。init 返回 initialized=true、running=false，但 stage 4=warning / discovery_partial、partial_success=true；这只表示画像初始化完成，不表示首轮推荐成功。首次失败状态与独立审计分别保存在 `init-status-first-final.json`、`independent-first-final-audit.json`。
- **有界诊断复测**：保留原失败不改写；通过生产 InstagramTaskQueue 追加一次同话题 max_items=2 / max_pages=1 的只读诊断 task，经原扩展 dispatcher 执行，仍 failed / 同错误，0 items。该任务不归属于 formal producer，也不执行候选或画像入库，不能称为第二次完整 guided-init。`topic-probe-result.json` 与脚本 exit 1 保留失败证据。不存在独立 `discover-instagram` CLI（一次 help 探测已确认），正式入口仍为 `discover --source instagram`；没有为了复测添加新命令。
- **定位证据与边界**：被动观察不拦截/替换请求，只记录请求类型、状态、错误码和页面分类。诊断 task 与随后一次普通公开 topic 页累计有 20 个 script、2 个 stylesheet 请求报 `net::ERR_CONNECTION_CLOSED`；普通页面 document=200，但 bodyLength=0，未转登录页/challenge，也没有出现新的内容 GraphQL。同一脚本加载失败在非任务普通页面复现，支持浏览器到页面资源的连接故障，不支持“空数据已成功采集”，也不能据此断言具体代理节点内部原因。首次 bootstrap 则实际观察到 identity/saved/following GET 200、原生 Bloks POST 200、GraphQL POST 200。此为选定类别的被动观察，不是完整上游 mutation 审计；证据 `network-final-first.log`、`probe-runtime.json.log`、`public-page-diagnostic.log`。
- **本轮判定**：`audit-only` 的本次真实 E2E 已执行并发现外部资源加载阻塞；account→event→profile 通过，discover→eval→recommendation 不通过。未注入 fixture、旧响应或旧推荐，没有调低阈值、伪造成功或尝试站内互动。前轮 8421 passed / 60 skipped 与扩展 1477 项仍是自动化证据，不能替代本次失败链路；本轮没有生产代码改动，不重复全仓自动化来掩盖真站问题。七项原暂缓门禁保持原状态。
- **独立最终审计**：`independent-final-audit.json` / `independent-probe-delta.json` 确认复测仅新增 1 条诊断 task，画像文件 hash、事件、LLM usage 与关键业务状态不变，无 active task/init。诊断 task 的持久 smoke_only=false，隔离来自未交给 producer/candidate writer；不冒称已验证 smoke-only 门禁。embedding 配置和前置连通检查就绪，但 embedding cache 为 0，未到达本轮候选 embedding/eval 阶段。扩展 local/session outbox 为空、任务 tab 为 0，普通诊断页面已关闭并恢复原 active tab。
- **展示观察**：`popup-final.txt` 的推荐空态仍显示“阿B 正在补货”与“这轮还没补进”，没有在该卡片直接呈现本次 Instagram 失败原因；同时 init API 已明确 stage 4 warning。仅记录这处错误可见性不足，不把泛化补货文案当作后台任务仍在运行或重试已成功的证据。本轮未修改该共享空态。
- **收尾**：恢复 Chrome 测试 profile 原 endpoint 28420，再退出本轮 Chrome PID 35647 与隔离后端 PID 34887；28820 已无监听，证据保留。Clash Verge GUI/core PID 571/866、日常后端 PID 52548、原 Firefox PID 91887 均保持运行，没有修改代理/TLS/账号或主工作区，没有提交、合并或发布。验收文档 `git diff --check` 通过。

## 2026-10-02 暂缓外部门禁后的本地后续流程补验

- **范围与证据分层**：按用户要求暂缓下节七项外部门禁，寻找其他尚缺运行时证据的路径。本轮为 `audit-only` 补验及一处配置提示修复，结论为 **incremental only**。隔离 root `/tmp/openbiliclaw-instagram-local-acceptance-20261002.xPuU8T` 用 SQLite backup 复制上一轮真实数据，包含 5 事件、10 候选和 2 推荐；不是重新初始化，也没有新 Instagram 或 LLM 请求。使用 worktree 的完整生产 API/SQLite，端口仅 `127.0.0.1:28720`；浏览器只允许该本机地址，后端审计钩子拒绝所有 `socket.connect`，这不是系统级全协议防火墙。scheduler、incremental 与 native auto-sync 均关闭。
- **真实本地收藏闭环**：desktop 实际点击收藏/稍后再看 → API/SQLite 落库；重复添加各保持 1 条，删除收藏不影响稍后再看。返回 `sync_status=unsupported`、`error_code=local_only_source`、空 `sync_task_id`；显式请求平台同步返回 422，native task 仍为 0。完整后端进程重启后，两项 membership 和配置均保留。mobile 390×844 显示相同状态，逐项取消后各列表为 0，两个空态文案均实际可见且无横向溢出。证据：`saved-before-restart.json`、`after-restart.json`、`after-local-actions.json`、`browser-favorite-empty.log` 及对应快照。最终保留的 saved metadata 与 removal tombstones 属正常本地记录，不声称全库零写。
- **配置热更新与状态边界**：真实 PUT 返回 202，等待对应 revision 为 applied；停用来源仍保留已存 credential，enabled 与凭据存在性分离。`creator` 单独输入正规化为 topic+creator，预算 13/4、share=3 和 min interval=240 持久化；其他来源配置未被部分更新覆盖。桌面展开的平台源设置在同一 document 中看到 off→on，预算和占比不丢失，见 `config-live-off.log` / `config-live-on.log`。`config-disabled-policy.json` 证明公共 source policy 在持久配置停用时排除 Instagram，不冒充读取了某个不存在的 runtime API 字段。旧心跳只是复制的数据，不能据此宣称新登录成功；本轮不追加 popup/setup 收敛主张。
- **来源筛选和空新库存**：真实 API 的新库存为 0，但既有 2 张推荐仍可在 Instagram 与全部筛选之间切换；Instagram tab 正确选中，切换未新增 mutating request。见 `filter-evidence.log`。没有点击换一批或补库，因此不是混合来源补库测试。
- **跨进程发现冷却**：保持此前真实 attempt 时间不变，通过公开配置把最小间隔设为 240 分钟，在仍处于窗口内时由两个独立进程执行实际 console entry `discover --source instagram --limit 2`（不 force）。均 exit 0 / throttled，未尝试 DNS/connect/sendto；task、candidate、budget、attempt、LLM、events 的完整行 hash 与画像 hash 不变，没有用拒绝运行去延长冷却。证据为 `cooldown/result.json` 和两份原始 CLI 日志。
- **init-only 与离线 CLI 负向场景**：独立 root `/tmp/openbiliclaw-instagram-gap-local-20261002.Ziu3PJ/final-report.json`。真实 `SourceIncrementalSync.tick()` 在有效本地前置条件下，注入 0/1/24/720 小时均 not_due，41 张业务表、bootstrap/memory 不变，0 task/0 kick/0 network；这是时钟注入，不是假称真浏览器驻留一个月。真实 Typer app 子进程执行 `fetch-instagram --force --wait-seconds 1`，无扩展和后端时 exit 1 并明确超时，只留下 1 条 pending smoke-only / no-profile-update 诊断任务及 0 字节协调锁；业务/画像不变，唯一连接尝试为本机不可达 kick。保留的两个离线诊断 root 不得再连接后端或浏览器。
- **发现并修复**：Instagram 小预算告警原写“每日任务次数”，而 producer 实际按过滤、去重与最终 limit 后保留的候选条数计费。先通过真实 load_config 及 4 个公开 save/load 回归复现失败，再改为“每日保留候选条数”；0 的不限语义、预算值和其他平台提示均未改变。新增 14 项回归，完整配置测试 304 passed；独立代码/文档检查未发现新问题。配置模块、Instagram 模块与 changelog 同步更新，未改变架构、依赖、CLI 参数或安装流程。
- **最终自动化与数据审计**：本轮完整 Python 首次原跑 **8421 passed / 60 skipped**，575.76 秒、exit 0，另有 5192 条 warning；Ruff check、Ruff format（623 文件）、MyPy（262 文件）均通过，原始日志和退出码在 `final-checks/`。本轮未改扩展，1477 项扩展测试及两浏览器构建仍沿用前轮证据，不称为本轮重跑。`independent-final-data-audit.json` 比较原数据和隔离副本的 62 张表完整行 hash/计数，仅 saved_items、saved_item_removals、sqlite_sequence 有预期差异；业务/任务表及两个 soul 文件一致，活跃 Instagram task 为 0。
- **收尾**：最后再次启动完整后端，确认移除后的两个列表仍为 0、Instagram enabled 与预算/冷却配置保持，见 `final-restart-check.json`。只退出本轮隔离 Chrome 会话和 28720 后端，端口已无监听；预先存在的 Firefox 登录准备窗口保留，未操作账号。主工作区干净，日常 18420 PID 52548 与 Clash Verge GUI/core PID 571/866 仍运行；没有关闭、重启或修改 Clash，也没有 commit、merge、push 或发布。
- **边界与测试工具修正**：第一次手写 API payload 含标题换行，按公开校验正确返回 422；改用已保存的 canonical payload 后通过。最初工具误要求配置 PUT=200，已按真实异步 202/apply-status 断言；停用来源的 legacy `state=no_auth` 不等于凭据丢失。封面出站被本轮隔离策略阻止，图片失败不记作封面 PASS，也不将其当作新上游故障。七项暂缓门禁状态不变，没有扩大真实账号动作或发布范围。

## 2026-10-02 剩余门禁核对与 Firefox 登录准备

- 注册审计再次 exit 0：34 PASS / 10 N/A / 14 MANUAL，required_missing=0、fully_verified=false。独立只读核对仍剩共享封面 DNS/SSRF、匿名独立 profile、真实多页末页、双账号切换、Firefox 登录账号 E2E、上游状态前后审计及生产许可七项；没有将设计检查或现有小样本结果升级为这些门禁 PASS。
- 共享封面传输方案已核对 public HTTPX/HTTPCore 接口：需要逐跳校验全部 DNS 地址并固定实际连接 IP，保留原域名 Host/SNI 与 TLS 验证，不能以域名代理重试绕过检查。IP CONNECT 可能改变 Clash 域名分流，且代理若按 TLS 嗅探改写目标，单靠客户端不能宣称全链路固定。此轮尚未修改该生产代码或添加测试，测试入口（图片代理接口＋实际出站连接层）待用户确认，安全门禁继续 NOT_RUN。
- 全新可见 Firefox 152.0.1 profile 位于 `/tmp/openbiliclaw-firefox-login-20261002.gkrttB/profile`，最终 Firefox ZIP 的 SHA-256 为 `8731d37c53f4da714dcd9896c42d33d5dbf5e2326fd9c638826c4727a9c1a2cb`。通过正常临时安装装入原包，扩展 endpoint 固定到隔离 `127.0.0.1:28420`，该端口尚无后端；没有连接日常 18420。临时安装后 Instagram optional host permission 自动为 true，未调用权限授予/撤回 API，不能当作原生授权弹窗验收。
- Firefox 首次导航遇到 `nssFailure2` 连接重置；只读确认默认 system proxy 实际解析到 HTTP `127.0.0.1:7897`、默认 TLS 设置，同代理 curl 首页为 HTTP 200 / TLS verify=0。随后仅一次不改设置的普通刷新成功，真实可见密码输入框存在且不再是浏览器错误页。没有读取输入值、输入凭据、复制 Cookie 或开始采集。证据为该隔离 root 的 `network-readonly.json` 与 `navigation-retry-once.json`；窗口保留待用户本人登录，故账号 E2E 仍 BLOCKED，不能把登录页就绪记为通过。
- Clash Verge GUI/core PID 571/866 和日常后端 PID 52548 保持运行，主工作区干净；未修改 Clash、系统代理或真实 TLS 校验，没有新增站内互动、提交、合并或发布。

## 2026-10-02 最终构建真实请求复测

- **范围**：本轮为既有 full integration 的真实 E2E 复验，不新增协议、功能或账号互动；没有 mock / route fulfill 上游，不复制旧事件、画像或推荐，不调整评分门槛。使用既有授权 Chrome profile 和最终 worktree 扩展，独立 root `/tmp/openbiliclaw-instagram-real-20261002.WGpLKg`，后端仅监听 `127.0.0.1:28420`。保留此前 small-sample cap=2；daily scheduler 与 incremental 关闭。
- **新环境基线**：独立只读审查确认 13 项任务/业务 sink 为 0，3 张 producer lazy 表尚未创建，soul/profile 文件不存在。配置仅继承隔离来源设置并更新为当前主项目的有效 LLM 配置；同 worktree loader 逐字段比较相等。模型链为 `deepseek-v4-flash` → `sensenova-6.8-flash-lite`，embedding 为 Ollama `nomic-embed-text`。旧分支仍不识别主配置新增的 embedding 容量与 scheduler LLM-budget 字段，本轮只声明已识别的有效模型配置相等，不声明与更新后的 main 运行时完全等价。基线见 `audit-baseline.md`。
- **实际安装产物**：扩展 ID `eikbajehgamillcimgogeobbngdemcla`、版本 0.3.204、host permission=true。运行环境读取的 manifest/worker/content/MAIN 字节 hash 全与最终磁盘构建一致：worker `86a3f0a5b5188f3c63aa671e931146c8b4b7b87d8a0d57ab87af3da72feccb51`、content `d570335e5a5db56d694141d7246709d682d7da36946283a328d7bc6a5978441e`。这次真实请求已覆盖最后的数字身份分支修复版本，不再只沿用前一构建的正常路径证据。
- **初始化**：实际 popup 取消 B 站、仅选 Instagram 后点击开始。登录心跳自动上报，未手工 verify 或伪造 credential。bootstrap 经真实同源请求返回 2 like / 2 favorite / 1 follow，持久事件共 5；liked/saved incomplete、following complete，阶段 1 为 `warning/instagram_partial`。阶段 2、3 完成真实偏好分析和新画像构建，没有把已有画像复制进来。
- **正式发现和模型闭环**：当前 init 拥有的正式 topic/creator 任务分别回传 7 / 3 条，均为达到有界上限的 partial。10 个候选最终为 cached=2、rejected_low_score=7、rejected_recently_viewed=1；生成 2 条真实推荐。`soul.preference`、`soul.profile_build`、`discovery.evaluate_batch`、`recommendation.write_expression` 四类 usage 均记录 `deepseek-v4-flash` 成功，无本轮 fallback 成功主张。终态 `initialized=true`、`running=false`、stage 2/3/4=ok、`partial_success=true`，见 `init-status-final.json`。
- **独立持久化审查**：`audit-final.md` 确认单一哈希账号的 task/state/5 events 一致，topic/creator 任务结果与 10 个候选精确关联，2 个 cached 候选与 cache/recommendation 的双键、来源、文案和评分一致，无来源污染或活跃 claim 残留。本轮 2 条推荐均来自 topic，creator 的 3 条均低分淘汰，故只证明 creator→评估，不额外宣称该分支入推荐成功。首次入队尚无 expected account key；验证的是回传后的哈希账号一致性，不虚构首次请求前的绑定。usage 无逐候选外键、SQLite 无法独证上游响应/浏览器展示，均在审查中明确列出证据边界。
- **真实流量证据**：只附加被动响应状态观察器，未替换请求/响应、未记录 Cookie、headers、body、账号或完整 URL。观察到 identity/saved/following GET 各 1 次 200、native Bloks GET 1 次 200、原生 GraphQL POST 27 次 200。该观察器只覆盖选定 route class，不声称是完整网络审计；原生页面 POST 不等于扩展主动互动，也不据此宣称站内状态完全不变。Recent Searches 等前后审计仍单列未完成。
- **三端实际展示**：desktop 1440px、mobile 390px、已安装 popup 扩展页面 420px 均显示这 2 条 Instagram 推荐的封面、作者与文案，无横向溢出。桌面两张图片加载成功；移动端使用背景图而非 `<article><img>`，其封面由截图人工查看确认，不能把空 selector 计数误记为通过。截图在忽略目录 `output/playwright/instagram-real-20261002/`。本轮是 popup 页面，不新增原生 SIDE_PANEL 验收主张，也未执行卡片互动动作。
- **收尾**：3 个 Instagram 任务均 completed，local/session outbox 为空，任务 tab=0；采集前后的原 active tab ID 相同。普通浏览行为没有被任务污染成新事件，事件仍为 2 like / 2 favorite / 1 follow。自己的 Chrome 与 28420 后端正常退出；日常 18420 PID 52548、Clash Verge GUI/core PID 571/866 保持运行，没有关闭、重启或修改代理选择。
- **耗时与结论边界**：`init_runs` 记录 UTC 04:14:36→04:18:44，共 248 秒；bootstrap 为 49 秒，topic/creator 任务分别为 7 / 13 秒。最后一批候选采集完成 04:16:31 到 `discovery.evaluate_batch` 成功落库 04:18:34 相隔 123 秒，这是端到端阶段间隔，不当作精确 provider 请求耗时。本轮有界真实功能链通过，评分仍是主要等待点。共享封面 DNS/SSRF 防护、匿名独立 profile 授权、Firefox 真实登录、多页大样本、真实切号及平台许可等尚未补齐，不能宣布 full acceptance 或发布。

## 2026-10-01 代理修复后重新验收

- 用户明确要求保留 Clash Verge，并批准将当前 Instagram 所走的既有 selector 从新加坡直连 Pro 切换到日本直连 Pro。只修改运行时 selector；没有关闭或重启 GUI/内核、修改订阅/规则/YAML、切换系统代理或放宽真实请求的 TLS 验证。GUI/内核 PID 前后相同，配置文件摘要不变。不保证 GUI 重新加载后仍保持该运行时选择。
- 切换前 HTTP CONNECT 成功但 TLS EOF/`ERR_CONNECTION_CLOSED`，Instagram 静态资源加载失败；切换后实际 1,100,145 字节静态资源在默认 HTTP、HTTP/1.1、TLS 1.2、SOCKS 四种下载方式下均为 200/TLS verify=0。独立无登录 Chrome 的 Instagram document 为 200，正常显示登录表单且无请求失败；example.com 与 wikipedia.org 同路径 HTTPS 也恢复。对照支持旧路由/节点不稳定，不能据此断言其内部具体故障。私有脱敏诊断及恢复脚本位于 `/tmp/openbiliclaw-clash-repair-20261001.ySZXHB`。
- 新建 `/tmp/openbiliclaw-instagram-live-jp-20261001.Ol4Vbr`：仅复制配置，不复制 DB/事件/画像，12 张业务/任务表初始全零；Python、后端与安装态 Chrome 资源均核对 worktree。仅 Instagram 开启，scheduler/incremental 关闭，端口 28420，有效 LLM 路由与当前主项目相同。旧分支不识别主配置新增的三个 embedding 缓存容量字段，因此只声明有效模型配置相等，不声称原始 TOML 无损迁移。
- 第一次真实 popup 点击暴露两处问题：扩展先启动而后端尚不可达时，Instagram 配置探测返回 false 后未进入快速重试，仅留下小时周期，后端没有登录心跳；后端正确拒绝 Instagram 私人信号初始化，但 popup 把通用 `no_profile_signal_sources` 错译成 Bangumi 提示。通过正式 verify API 请求扩展重新读取本机 sessionid 存在性后，心跳正常；未伪造 credential，也未上传 Cookie 值。对应修复与复验见下文。
- **fresh guided-init 真实闭环通过（有界、部分采集）**：实际 popup 仅勾 Instagram，POST 后 bootstrap 取得 like=2、favorite=2、follow=1；liked/saved 达 cap 保留 incomplete，following complete，stage 1 为 `warning/instagram_partial`。阶段 2、3 使用主项目当前配置的模型完成分析与画像，阶段 4 正式 topic/creator 任务共取得 10 候选：2 条近期看过被拒、8 条进入模型评估，其中 4 条低分拒绝、4 条入 cache 并生成推荐表达。最终 run 为 completed，stage 2/3/4 全部 ok，`partial_success=true`，不是全量历史导入。
- 独立只读审查确认事件、候选、8 条 eval audit、4 条 cache 和 4 条推荐 join 全部属于 Instagram，无孤立推荐或 B 站污染；LLM default chain、module routes、embedding 与当前主配置有效值一致。真实 popup、desktop 和 390px mobile 已显示同批推荐，无横向溢出；截图位于忽略目录 `output/playwright/instagram-jp-fresh/`。此轮没有 mock 上游响应，没有追加站内互动，没有复制旧画像。
- **新发现问题修复**：自动心跳区分 enabled/disabled/unavailable，配置不可用不读 Cookie，1 分钟重试，成功后恢复小时周期。先红后绿的 48 项 cookie-sync 回归覆盖异常配置、权限与禁用；实际停掉隔离后端并重新加载最终扩展后，观测 alarm=1 分钟，恢复后端后自动 credential POST=200、登录提示 verified、alarm=60 分钟，没有手动 verify 或修改 Cookie。证据为同 root 的 `backend-reconnect.log` 与 `reconnect-evidence.md`。
- Popup、setup、desktop 首次及重新初始化优先显示后端平台详情，缺少/无效详情为平台中性提示；仅接受非空字符串，上限 2000，纯文本呈现。Node init-control/popup-api 98 项、真实 Chromium + 本地 HTTP stub 的 17 项、guided-init 45 项定向通过；这些故障注入测试不是 Instagram 上游证据。
- 心跳/UI 修复版本扩展全量为 1469 passed；执行器身份连续性修复后为 1476 passed，最后补上同用户名但数字账号不同的分支后，最终全量 **1477 passed / 0 failed**，日志 `extension-tests-identity-final.log`。TypeScript、Chrome/Firefox 各 21 项资源校验通过。最终两包重新本地构建，Chrome ZIP SHA-256 `124d307363ffdbbd470a44df10aa88222a680be06f2173778324b1f547376dc1`，Firefox ZIP `8731d37c53f4da714dcd9896c42d33d5dbf5e2326fd9c638826c4727a9c1a2cb`；未发版或签名。再次启动已授权 Chrome profile 后，从扩展运行环境读取资源字节，manifest / worker / content / MAIN 四项 hash 与磁盘一致：worker `86a3f0a5b5188f3c63aa671e931146c8b4b7b87d8a0d57ab87af3da72feccb51`、content `d570335e5a5db56d694141d7246709d682d7da36946283a328d7bc6a5978441e`；manifest 与 MAIN 未变化。此次仅核对安装资源和空 outbox，后端保持停止，不追加真站采集。
- 本轮首次 Python 全仓 **8405 passed、60 skipped、1 failed**（600.36 秒）：唯一失败是旧 Bangumi 静态测试仍要求通用错误 fallback 固定写 Bangumi 三档凭据。已将该断言同步为中性兜底，并保留实际浏览器/Node 对 Bangumi 后端详情三个路径的完整验证；三个定向复验通过，生产代码未为迁就旧断言回退。随后完整重跑 8406 passed、60 skipped；加入封面契约测试后的最终全仓为 **8407 passed、60 skipped**（583.19 秒、exit 0），日志 `full-pytest-complete.log`。机械格式化后的 source/auth 另有 261 passed/34 skipped，Ruff check/format 与 MyPy（262 文件）通过。旧 8393 通过记录仅为历史树。
- 最终冻结的 Firefox ZIP 在新空 profile 临时实装，MAIN document_start/bridge/replay/实际 creator executor 全通过，四项 installed asset hash 与包匹配，0 上游转发。证据 `/tmp/openbiliclaw-firefox-freeze-20261001.eaT9Do/result.json`，`checks_passed=true`；Firefox 和本地 fixture 代理均已退出。此前两版证据仍分别保留在 `/tmp/openbiliclaw-firefox-account-final-20261001.sYsNuM/result.json` 与 `/tmp/openbiliclaw-firefox-final-20261001.xI1eNE/result.json`。真实 Firefox 登录账号仍缺失，不能用该 fixture 替代。
- **匿名补测受原生授权阻塞**：心跳/UI 修复后的 Chrome ZIP 在全新无 Cookie profile 正常加载，运行中 worker/content/MAIN hash 与当时包内一致；popup 正常保存路径停在浏览器原生 Instagram 站点授权，权限最终仍 false。没有修改 manifest/profile 权限、伪造批准或复制 Cookie，故 topic `technology` / creator `technocraj` 两个计划任务均 NOT_RUN，上游 Instagram 请求和任务均 0，不称为网络失败。独立 root `/tmp/openbiliclaw-instagram-anonymous-20261001.oZNRid/acceptance.md` 记录所有 sink；事件/候选/seen/推荐/画像 ledger/LLM usage 均 0，设置读取产生 1 条 `x_source_health` 诊断记录，因此不声称全表零写。仅自己的新浏览器与 28620 后端已退出；恢复该项需人工确认这个独立 profile 的原生站点授权。后续账号隔离版本不复用这一包 hash 或冒称匿名任务通过。
- **账号加固后真实只读复验通过**：相同已授权登录 profile 运行 `fetch-instagram --force --wait-seconds 180`，实际冻结 smoke_only=true、cap=2、请求间隔 3000ms；结果 completed/partial、2 like/2 favorite/1 follow，只有诊断 task 由 3→4。事件5、候选10、cache4、推荐4、seen2、LLM usage4、画像 ledger6 均未变；soul/soul_profile JSON、画像 Markdown、bootstrap state 四项 hash 不变。runtime pipeline_state 的保存时间更新且文件 hash 变化，没有保留其运行前全文，故不声称全部文件零写。证据 `account-final-smoke.log` 与 `account-final-smoke-evidence.md`。该真站请求来自 content SHA `9dabc5f9219e9414ccd562cb96050114ab015fcdefb44dbae9d0573e31702a91` 的前一构建；最后仅增加“同用户名、不同数字 ID”的拒绝分支，未变正常路径，其差异由红绿回归验证，不冒称在最终字节上又做过一次真站请求。
- 本轮已登录 Chrome 测试会话与 28420 后端均正常退出；清理前 4 个 Instagram 任务全 completed、local/session outbox 为空、任务 tab=0。保留登录 profile 和私有证据；不触碰日常 18420、主配置/画像或原有授权样本。Clash Verge 与内核仍为原 PID 571/866、日本节点保持当前运行时选择。
- **独立遗漏审查**：封面实际由三端走 `/api/image-proxy`，原 contract 的 `image="direct"` 声明错误，漏列了必需的 `media.image-network-boundary` 手工安全门禁。已以先红后绿契约测试更正为 `proxy`，8 项契约测试通过，审计为 34 PASS / 10 N/A / 14 MANUAL / required_missing=0。CDN allow-list 接线不等于 SSRF 安全证明；共享代理尚缺每次 DNS 解析/逐跳地址检查与 rebinding 地址固定的运行时证据，该项明确 NOT_RUN，不能发布为完整验收。此轮未在 Clash/可信代理路由下仓促替换共享 HTTP 传输。
- **执行器账号连续性已修复**：红测复现了 saved 页中切号仍返回 ok，以及早期 Likes 缓冲属于 A、初始身份已是 B 时错误接纳两条路径。现在私人请求前后、最终提交前重新核对 fresh viewer，早期 Likes 绑定任务文档 PolarisViewer；确认切号返回 failed/0 items，并由既有 dispatcher 清空混合持久化进度。最终补测还复现“同用户名、不同数字 ID”被误降级为 partial 的分支，现直接拒绝并不追加 fallback 请求。所有身份/私人 GET 遵守冻结请求间隔；已知 429/challenge/login 立即停止，不追加确认探针。最终 executor 定向 30 项及扩展全量 1477 项通过。此项仅防可观测身份变化，不宣称平台提供原子快照，也不替代真实双账号验收。
- 封面网络边界的额外只读可行性检查使用本机 httpx 0.28.1/httpcore 1.0.9 与纯内存 socket 探针（0 外网请求）：直连可以固定已校验 IP 且保留原域名 TLS；代理路径仅做本地 DNS 预检无法约束代理的远程解析，直接改 IP CONNECT 又会改变域名分流，并在当前实现中忽略 `sni_hostname`。因此未放宽 TLS 或绕过 Clash，也未把预检当作完整修复；该共享传输层改造需单独明确兼容性边界，安全门禁保留 NOT_RUN。


## 2026-10-01 追加收口：安装态重放与界面边界

### 已完成

- **真实安装态 ACK 抑制与浏览器重启重放**：全新隔离 root `/tmp/openbiliclaw-instagram-completion-20261001.vekH1c`，显式使用 worktree import 和当前主项目 LLM 配置，scheduler/incremental 关闭。本项没有调用 LLM；Instagram 页面被本地合成 HTML 替换。真实已安装 Chrome 扩展经过 MAIN tap/executor、HTTP 和真实 API/SQLite；测试包装层在后端已提交结果后，将 ACK 替换为受控 503，不修改产品代码，不宣称 TCP 丢包。
- 分别覆盖失败 0 行和成功 1 行合成媒体两种终态；同一完整浏览器 profile 正常关闭再重启后继续重放原 JSON 字节，恢复 200 后清除 local/session outbox 与 task tab。两次 canonical result 重启前后完全相同，各有 10 / 16 次相同 body SHA-256 的 POST，首次正常写入、其余 `ignored=true`。两轮全浏览器 PID 变化为 16421→19629→20318；这是 graceful restart，不代替此前强制终止实验。验证无 pending/in-progress，10 张实际事件/候选/推荐/收藏等下游表均 0，无画像和 bootstrap state；不是合成账号 ingress 或真实 Instagram 内容验收。
- 证据为该 root 的 `ack-evidence.jsonl`、`ack-verification.json`、canonical 前后快照及 `browser-restart-provenance.md`；后者明确是实际工具输出转录。独立只读审查复核了必需表存在性、零副作用、重启边界及安装资源 hash。
- **无封面/文字卡矩阵**：3 种明确标记 SYNTHETIC 的 Instagram DTO（短卡、长标题正文、无断点长词）经过生产渲染器，覆盖 desktop 1440、mobile 375/390/768、已安装扩展 popup 页面和真实 `SIDE_PANEL`。逐卡检查作者、来源、文字卡 fallback、无横向溢出和全部关键按钮可命中；desktop/mobile 另实际提交并验证本地收藏请求的来源、ID、URL。其它动作仅证明可见可命中，不冒充业务结果。Chrome manifest 没有 `default_popup`，popup 证据是扩展页面 TAB，不虚报 `POPUP` context。
- 长列表补测发现移动端“回到顶部”遮住第三张卡的“聊一聊”。按 TDD 先复现失败，再只修改移动 shell：固定 3×3 点位检查、最多上移两步，无安全位置暂时隐藏；不扫描全量卡片、不改推荐/网络/动作协议。3 种宽度及 resize、触底、底栏间距、回顶均通过；新增 7 项加既有回顶/delight 回归共 **24 passed**。UI 验收规范只用于可点击区避让，保留原设计体系。
- **Chrome ZIP 新 profile 加载**：从现有 `0.3.204` ZIP 新解压并加载，7 项运行中资源 SHA-256 与包内一致，Instagram Cookie 数为 0；合成卡 popup 页面与原生侧栏通过。这是 ZIP 解压后的开发者加载，不是 CRX/商店安装，也不是登录任务 E2E。证据与截图位于忽略目录 `output/playwright/instagram-surface-matrix/`，包 hash 沿用下文。没有发布、签名或版本变更。
- **Firefox ZIP 安装执行**：Firefox 152.0.1 的全新隔离 profile 直接临时安装未修改的最终 ZIP，版本 0.3.204。首 inline script 执行时 `readyState=loading` 且 MAIN fetch hook 已在；合成 SSR `data-sjs` 与晚到的原生公开 profile 证明跨 world 归一化 1 行，replay 同样 1 行。实际 content script 接收 `INSTAGRAM_TASK_EXECUTE`、ACK、progress 和 `ok/items=1/scope_complete.discover=true` 终态；运行中 manifest/content/MAIN/background 的 SHA-256 与包内匹配。全部 HTTP/HTTPS 仅由本地拒绝转发代理提供 fixture，真实上游转发为 0，未接真实后端或登录账号；不等于商店签名或账号 E2E。证据 `/tmp/openbiliclaw-firefox-artifact-20261001.Rqxqu4/result.json`，浏览器正常退出。
- 冻结后的最终 Python 全仓 **8393 passed、60 skipped**，563.37 秒，exit 0；日志为本轮 root 的 `full-pytest-final.log`。扩展全量再次 **1459 passed / 0 failed**，TypeScript、两套各 21 项 asset verifier、Ruff 与 MyPy（262 source files）均通过；注册审计仍为 33 PASS / 10 N/A / 14 MANUAL、required_missing=0，不把注册完整当作真站验收。独立只读复核未发现新的必须修复项；未改扩展产物内容。

### 外部阻塞与安全收尾

- 最新已登录浏览器 document 返回 `net::ERR_CONNECTION_CLOSED` 后停止重试。独立无扩展/无登录的新 Chrome 和 curl 对照：Instagram、example.com、wikipedia.org 的 HTTPS 均在现有 `127.0.0.1:7897` 代理接受 CONNECT 后失败，而 example.com HTTP 返回 200。支持“通用代理/上游 TLS 传输故障”，不能细分代理进程或其上游，也不是登录失效、challenge 或证书验证错误的证明。脱敏报告为 `/tmp/openbiliclaw-instagram-network-20261001.UHzBqn/diagnosis.md`。
- 未切换系统代理、放宽真实上游 TLS 验证、复制 Cookie 或增加站内互动；Firefox 的本地 HTTPS fixture 使用隔离会话接受测试证书，不通外网。保留原先授权的 2 like/2 save/1 follow。下一步先由用户恢复同一路径的普通 HTTPS，再重跑 fresh guided-init；Firefox 登录、大样本、账号切换和 Recent Searches 对照仍需各自证据。
- 本轮 ACK 专用浏览器已关闭，隔离 28420 后端正常停止；界面专用 profile 与 stub 也已关闭。私有数据与日志保留，不删除登录 profile，不触碰日常 18420 或主配置/画像，无自动重试遗留。

## 2026-09-30 最终收口：初始化闭环与故障边界

### 修复与回归

- Creator transport 现在识别 profile 路由上的 401/403/429，不忽略终态错误、不在错误后继续滚动；已有合法 rows 按 partial 保留。Saved/following 非空但全部无效的 rows 返回 schema failure，混合有效/无效 rows 降级，不能伪装成成功空。
- 恢复的 bootstrap claim 冻结第一个已验证账号；后续 identity/progress/final 不同账号返回 `instagram_account_changed`，丢弃混合快照，不写任一账号的混合事件。后端接纳该终态与 `creator_not_public`，不会因错误码白名单漏项把 lease 卡住。
- Creator 媒体必须有明确公开作者，或匹配原生 `data.user` 的数字 ID 和 username；viewer、建议账号与缺失 privacy 不是证明。SSR 先到时等待原生公开 profile 并重新扫描 DOM，不存 raw response、不另发探测请求；private/unknown rows 不跨 bridge。
- **真实 fresh UI 暴露并修复阶段 4 错路由**：Instagram-only 原先仍调用 B 站四策略。API/CLI 现将本轮所选来源交给同一正式 Instagram producer；scheduler 可保持关闭，不改变后台 gate。新建和恢复的发现任务都在 kick 前登记到当前 init，旧后台任务仍不能领取。
- **小批量同步评估**：init 在 refresh lock 内调用 `drain_pending(flush=True)`，只跳过聚合等待，不提高调用方 batch cap，也不跳过评分、claim 或来源配额；随后同步生成推荐表达并检查 canonical pool。没有可浏览推荐仍失败/部分完成，不把 enqueue 当作成功。以上公共路径新增先红后绿测试，最终定向 **129 passed**；独立只读复核未发现新的必须修复项。
- 首轮全仓发现 saved-sync 测试存在终态 DB row 先于 watchdog done callback 的竞态；测试现在等待两者完成后作原断言，未修改生产 saved-sync 行为。相关复验 **54 passed**。修改代码期间的旧全仓另有两项 `inspect.getsource` 行号漂移失败，该轮 **8377 passed / 60 skipped / 2 failed** 不是最终树验收；最终全仓结果单列在 Gate ledger。

### 真实执行证据与未通过的重跑

- 沿用 nonempty 独立根 `/tmp/openbiliclaw-instagram-nonempty-20260930.IttQgF` 的真实 5 条授权事件及其新建画像，最新严格 privacy build 再次正式 topic/creator discover，经当前模型评估得到 0.74 分合格推荐。没有降低阈值、改模型路由或追加站内互动。
- 新 fresh guided 根 `/tmp/openbiliclaw-instagram-guided-20260930.FVhMMc` 从零数据启动 UI：真实 saved=2/following=1 入画像，Likes 因资源加载失败未观察到响应，保持降级。当前 `deepseek-v4-flash` 完成偏好分析、`sensenova-6.8-flash-lite` 完成画像；随后发现阶段 4 错走 B 站，已取消并修复。**这一轮不是 Instagram 四阶段成功证据。**
- 修复后的新 fresh 根 `/tmp/openbiliclaw-instagram-init-final-20260930.Jwaotg` 仍只复制当前配置，不复制 DB/事件/画像，显式设置 project root、worktree import、端口 28420，scheduler/incremental=false。刷新真实浏览器登录心跳后再点开始，bootstrap 终态 `failed/send_message_failed_after_reload`、0 items；init 为 `empty_signals`，无新画像，不宣称四阶段复验 PASS。
- 同时段普通浏览器 Likes 页面资源先出现 `static.cdninstagram.com / net::ERR_CONNECTION_CLOSED`，最终一次只读诊断连 document 也返回 `www.instagram.com / net::ERR_CONNECTION_CLOSED`。这是浏览器网络失败证据，不是未登录、challenge 或成功空；已停止重复 Instagram 请求。显式代理诊断未改善，已恢复原测试浏览器配置，未改用户系统代理、未绕过站点验证。脱敏证据为 fresh 根 `browser-network-final.log`。
- 真正终止本任务 Chrome 进程时，smoke task `f68c2376-2cb0-46f5-a016-7b04aea0ee3c` 处于 in-progress、持久 claim 存在；同 profile 重启后以原 claim `failed/recovery_tab_gone` 收尾，状态清空、无 stranded task。终止前尚无 accepted progress，此项证明 fail-closed 收尾，不证明非空进度续传。先前真实 extension reload 的 2/2/1 恢复证据仍独立有效；真实 ACK 丢包尚未验证。
- 跨源 mutex 使用实际 Linux.do 与 Instagram dispatcher/队列，并对 Linux.do 页面注入一次 **20 秒延迟后网络中止**：确认 Linux.do 持锁时 Instagram 保持 pending、无 claimed_at；锁释放后 Instagram 正常领取，最终保留 saved=2/following=1，Likes 为 `response_envelope_unobserved`。这是受控故障下实际调度/互斥验证，不是 Linux.do 上游数据 E2E。route 已撤销，临时 Linux.do enabled 已恢复 false。
- 实际 desktop、mobile 390×844、popup，以及通过 `chrome.sidePanel.open` 打开的原生 SIDE_PANEL 360×870 都观察到真实 Instagram 推荐标题/作者，无横向溢出；SIDE_PANEL context 已单独确认，不再以 popup URL 代替。无封面/文字卡全矩阵仍缺此来源安装态证据。

### 产物与边界

- 扩展最终全量 **1459 passed / 0 failed**；TypeScript、Chrome/Firefox build 与每套 21 项 asset verifier 均通过。Ruff 全仓、MyPy **262 source files** 通过。Firefox 未安装并登录实测，不复制 Chrome Cookie；尚未得到可用 Firefox 测试登录态。
- 当前已安装 Chrome unpacked 的四项 SHA-256 与工作树磁盘一致：manifest `eee447530d0147911debef2d591242016a81ad7ee70760eeb56c62d75a5fe5ec`；content `c37854f656ff58f245d8c4e3f0ea9a126739506c9306cfae4ac5a28b5cd8a495`；MAIN tap `d51d3e64074cf8884764484db06bc92f2f436f6532310e4325060801f17e14de`；worker `8bbf8cd894235a4e41cb998314ea1e38b109c43d304b5b075aa69bc3e4ee830c`。版本仍为 0.3.204。
- 仅本地构建两个忽略的 ZIP，不 bump/sign/upload/publish：Chrome `450d45cb0174c1b729e6944cb8d7afbd2e1b6b9f32246dca0e64d4fbc331c2fd`，Firefox `9db9fe4ffcbd32ef071a811a8b12966c9eb9430f52b78048389f35f9b5803a43`。产物构建不等于全新 profile 安装验收。
- 保留用户逐对象授权的 2 like/2 save/1 follow，不自动取消或扩大对象；主工作区、日常 18420、主配置与画像不变。真实账号 ID、Cookie、原始私人响应不进仓库。尚未请求提交/合并/发布，因此这些外部变更不执行。
- 当前结论为 **incremental only / full live acceptance blocked**：已修复已定位的实现问题，但网络恢复后的 fresh guided-init、Firefox 安装态、大样本分页、真实账号切换/ACK 丢失、站内状态前后对照及生产授权仍需各自证据，不能互相替代。
- 收尾只读检查：nonempty 根 events=5/candidates=5/cache=2/recommendations=1，guided 旧错误路由根 events=3/candidates=0/cache=20/recommendations=10（不得作为 Instagram 推荐验收），final fresh 根四者均 0、无画像；三个根 Instagram pending/in-progress 均 0、native-save tasks 均 0，scheduler/incremental/Linux.do 均 false。扩展 local/session 均无 Instagram task/outbox。已关闭本任务独立浏览器并正常停止 28420 后端，保留三份私有测试数据、日志和登录 profile；没有遗留自动重试。计数证据为 final fresh 根 `closure-counts.jsonl`。

## 2026-09-30：可选登录 topic 与正式模型链

### 同日追加：合成账号事件与真实 LLM（独立证据）

- 用户选择“先合成内部闭环，再手动准备少量真账号样本”。范围为 capability-increment 验证及所暴露入口修复，不授权自动点赞/收藏/关注；等待用户确认真实样本准备完成后才继续非空真站验收。
- 独立 root `/tmp/openbiliclaw-instagram-synthetic-20260930.zC8QXR`，全新数据库，未复制旧画像；配置保留当时主项目 LLM 路由，scheduler/incremental 关闭。旧 worktree 不识别的新 embedding-cache/scheduler-budget 配置字段照旧忽略，不宣称跨版本全配置迁移。
- `scripts/instagram_synthetic_e2e.py` 使用合成数字身份、合成规范化媒体与 following 结果，经过正式 next-task/task-result API、staged completion、event ingress、MemoryManager；无 Instagram 上游请求。2 like、2 favorite、1 follow 共 5 条；重复 callback 与第二个任务仍 5 条；追加自动化验证账号冲突 409 后仍 5 条、全新 smoke-only 样本仍 5 条，既有数据库拒绝注入。非空 Likes 保持 partial，不伪造全历史完整。
- 发现并先红后绿修复 `rebuild-profile --source instagram` 错误要求 B 站认证。现在所有本地事件重建跳过 B 站登录门禁，保留 runtime 配置校验，不改变抓取式 init 的认证规则。
- 正式 `rebuild-profile --source instagram --limit 20` 实际加载上述 5 条事件；当前 `deepseek-v4-flash` 完成 `soul.preference`、`soul.profile_build` 两次真实调用，生成新画像，合计约 ¥0.0137。这是合成输入 + 真实 LLM，不是用户真实兴趣画像。
- 新画像随后运行正式 `discover --source instagram --limit 4 --force`；仅浏览器回传以合成数据替代，topic/creator 各 2 条入候选，事件数保持 5。真实 evaluator 首选与 fallback 均报 rate_limited，4 条回到 pending_eval，推荐闭环 NOT_RUN/待模型可用；没有改变路由、降低阈值或继续重试。CLI exit 0/ok 仅表示本轮供给成功，不意味着评估完成。
- 来源任务、wiring、CLI 及新增合成回归合计 339 passed；MyPy 262 文件通过。上一次全仓结果单独保留，不宣称本次最终树又运行全仓。脚本和 CLI 变更未提交/合并/发布；没有启动监听端口或加载真实浏览器任务。
- 最终复验：新增两项 Python 用例再次通过（含既有数据库拒绝注入）；浏览器原生 Likes/任务 executor 的 23 项合成契约回归通过，Ruff 与 diff whitespace 检查通过。运行时 import 确认来自 worktree `src/openbiliclaw/__init__.py`，data root 确认上述独立目录，soul/preference 文件均已生成。测试进程全部退出，无后台重试；真实样本准备仍等待用户确认。

- 授权范围：用户明确允许直接 topic 页复用已有会话，被动读取页面自然返回的公开媒体；不提交 Search、不点赞/收藏/关注、不构造或重放私有搜索请求。`discover` 改为 `optional-credential`，未登录仍保留匿名路径，不把登录成功算作匿名验收。
- 原因与修复：真实 popular 页返回 `data.xdt_fbsearch__top_serp_graphql`，包含 header/account 单元与 MediaGrid 单元。parser 仅接纳直接 popular/hashtag 任务路由下 MediaGrid 的明确公开作者媒体，未知单元、隐私状态缺失和私密作者拒绝；保存 `authenticated_topic_observed` 布尔证据。解析超过 80 项撤销末页证据；上游错误立即结束循环，保留已接受内容，不继续滚动触发请求。
- 真实隔离环境：`/tmp/openbiliclaw-instagram-topic-20260930.liqt1m`，后端端口 28420、scheduler/incremental 均关闭；显式 `OPENBILICLAW_PROJECT_ROOT` 与 worktree `PYTHONPATH`。配置保留主项目当前 LLM 路由，沿用既有隔离测试画像副本；新 schema 的 embedding-cache / scheduler-budget 字段不由旧 worktree 支持，不宣称这些新字段迁移成功。此画像不是 Instagram 事件生成的画像。
- 正式 `discover --source instagram --limit 4 --force` 返回 `ok` / exit 0：topic 与 creator 各 2 条 `partial/item_cap_reached`，4 条写入统一候选池，作者、canonical URL、正文及发布时间均非空。真实模型链的首选 provider 失败后 fallback `deepseek-v4-flash` 完成 4 条评估，均 `rejected_low_score`，推荐 0；没有调整阈值或伪造事件。随后最终截断保护安装产物正式 `--limit 2 --force` 也返回 `ok`、发现与入池均 2 条。错误滚动停止保护在此复验之后追加，另以先红后绿回归验证，不把它冒充已遇到真实 429 的实测。
- 自动化：Python 来源/auth 定向 348 passed、34 skipped；MyPy 262 个文件通过，Ruff 通过。Chrome/Firefox 构建通过，两个 asset verifier 均确认 21 项。注册审计 33 PASS / 10 N/A / 14 MANUAL / required_missing=0。扩展最终测试及安装 hash 见本节后续记录，不把静态审计当作完整 E2E。
- 最终扩展全量 1447 passed（含先红后绿的错误后零滚动断言）；持久队列追加 provenance 断言后 19 passed。最终安装四项 SHA-256 与 worktree 磁盘逐项一致：manifest `eee447530d0147911debef2d591242016a81ad7ee70760eeb56c62d75a5fe5ec`；content `75a6f1bde05a050c99e8f57b4dcfa4b100e6d1d4b2c6db54bfa6457be9fd5a65`；MAIN tap `1eb680c2a67d3f6869d853d2cb07c530cae8b7350dc89c6765d85a3218efd5cf`；worker `b74f2cbe7001552681e47ecdebb754e2b81e43eec7b06ffb7bacb7481330e9be`。复验结束 DB 为 4 个 completed task、6 个 rejected_low_score 候选；扩展 local/session task state 均不存在。
- 留存边界：本轮未重新验证 logged-out browser discovery；此前空账号三个 scope 的真实结果仍有效，但没有非空 Likes 分页样本，也没有 Instagram 事件→画像→发现的真实闭环。页面自身 Recent Searches 前后对照、真实 in-flight 重启和跨来源 mutex 仍 NOT_RUN；不宣称所有 full-source gate 通过。没有提交、合并、发版或修改主工作区实现。
- 清理：本轮 topic 诊断 tab 与 popup 已关闭；确认无 pending/in-progress 和持久 task state 后停止隔离后端 PID 3939，28420 已无 listener。保留隔离 DB/日志用于复核，不清除登录 profile、不操作主后端 18420。
- 最终 Python 全仓：`pytest -q` 为 **8374 passed、60 skipped、1 failed**（1363.88 秒）。唯一失败为 `test_desktop_dialogue_layout_e2e.py::test_many_dialogue_cards_keep_natural_height_and_scroll[375-667]`：滚轮后固定等待 80ms，`scrollTop` 仍为 0；同一用例单独重跑 **1 passed**（7.84 秒），疑似浏览器时序不稳定。没有修改该无关 UI/测试，也不把原始全仓运行记为全绿。日志为隔离目录的 `full-pytest.log` 与 `layout-rerun.log`。

## Provenance

### 2026-09-30 晚：授权准备真实非空样本

- 用户逐对象确认 Instagram 两条内容 `DGTi0xYokFj`、`DVtTR4YgsKL` 的 like=true/save=true（默认个人收藏），以及公开账号 `nuvofindss` 的 follow=true。本次仅执行这 5 个操作；没有取消、追加其它对象或自动回滚。点赞按钮变为取消赞、收藏变为移除；第二条重新打开仍保持状态，账号主页出现已关注。最终后端回拉精确匹配两条 code 与目标账号，作为持久结果验证。此为用户授权样本准备，不是产品 native-save adapter 的验收。
- 新隔离 root `/tmp/openbiliclaw-instagram-nonempty-20260930.IttQgF`，端口 28420，未复制合成身份/事件或旧画像；当前主项目 LLM 路由保留，scheduler/incremental=false。import 路径确认为本 worktree；本轮日志、DB 和账号证据仅留本机，未将账号 ID/Cookie/原始 Bloks 写入仓库。
- 初次真实 smoke 返回 liked=0、saved=2、following=1，`instagram_liked:response_schema_degraded`。浏览器内被动捕获的非空 schema 显示：媒体 ID 为复合 ID、最后有全空占位格、visibility 表达式引用 media_id。脱敏合成回归先红（0 != 1）后绿，修复只解析数据，未执行任何 Bloks 动作。临时诊断 tab 已关闭，未保存原始响应；一次临时内联诊断脚本被 CSP 阻止，未绕过限制，最终验证使用重新构建并安装的扩展。
- 修复后两个真实 browser task 均 liked=2/saved=2/following=1，且精确匹配授权对象；第一次正式 ingress 后事件 0→5，第二次 5→5。每 scope 主动 cap=2，liked/saved 均 complete=false、following=true，终态 partial/item_cap_reached；不声称 Likes 非空末页或大样本分页通过。首次 smoke 无 event 副作用。
- 最终 installed manifest/content/worker hash 与上轮相同；MAIN tap 更新为 `12aab14114b20e58ea802f17f742a9e440d3b7f9c4f0f5c112fe623368fa2c43`，四项均与磁盘一致。Chrome/Firefox build、两个 21 项 asset preflight、TypeScript typecheck 通过。扩展全量 1448 passed；追加 affirmative-empty guard 后 6 项 Likes 定向再次通过；Python Instagram task/wiring/synthetic 回归 78 passed。
- 正式 `rebuild-profile --source instagram --limit 20` 加载 5 条真实事件，`deepseek-v4-flash` 完成 preference/profile_build 两次调用（约 ¥0.0148），生成新隔离画像，CLI exit 0。未使用合成账号或旧画像，也未覆盖用户主画像。这证明真实当前账号→规范化事件→画像构建的有界切片，不等于整套交互式 guided-init UI 已验收。
- 该真实新画像随后执行正式 `discover --source instagram --limit 4 --force`，真实 topic/creator 各 2 条、共 4 条候选；1 条最近已看过过滤，3 条由当前 `deepseek-v4-flash` 评估：1 cached（0.80 分）、2 rejected_low_score。正式 `recommend` 再由同一模型生成 expression 并展示 1 条推荐；`GET /api/recommendations` HTTP 200、items=1。所有 CLI exit 0，未降低阈值或修改模型路由。现在真实有界账号→事件→画像→发现→推荐链路 PASS；不扩大为全历史、全部 UI 或恢复场景 PASS。
- 清理前确认任务 pending/in-progress=0、事件仍 5、扩展 local/session task state 均不存在；诊断页和 popup 已关闭，隔离后端 PID 68810 正常停止。保留本地隔离数据用于复核，保留用户已授权的 2 like/2 save/1 follow，不自动取消。没有提交、合并、发布或覆盖主配置/画像。
- 本轮推荐呈现证据限于 CLI 与 API：清理前 popup 当前页面未观察到推荐标题/作者，未验证其推荐 tab 或四端渲染，不将接口有数据等同于全部界面通过。最终 28420 无 listener，5 个 task 均 completed，事件 5，候选状态为 cached=1/rejected_low_score=2/rejected_recently_viewed=1。

- Contract：[`docs/platform-source-contract.instagram.toml`](platform-source-contract.instagram.toml)
- Worktree：`feat/instagram-source`，测试 HEAD `6975e9e95e19d97bd4c5384b20f9810b3511e148`；2026-08-31 真站兼容修复及 2026-09-26 设置 / 发现 / 恢复修复尚未提交
- GitHub protocol research：instagrapi `2902a3bc`、Instaloader `54346929`、gallery-dl `86047cf6`、yt-dlp `5d6b8c8c`
- Real anonymous spike：2026-08-12、2026-08-31、2026-09-26，logged-out topic 和公开 creator 页面，只读；8 月任务证据与 9 月安装产物 / 公开浏览器 / 下游证据分别记录，不合并冒充同一次 E2E
- Real account boundary：2026-09-26 独立 Chrome for Testing profile 的 host permission 与 `sessionid` readiness 均为 true；网页端账户表单 GET 也确认了有效会话，但未取得可信数字账号 ID。旧客户端报 challenge 不再等同于真实上游验证；后续诊断已证实一个首页 HTML 误判。没有主动执行点赞 / 收藏 / 关注 / 搜索动作；页面自身曾出现私有 SERP 请求，因此缺少前后对照时不能声称已证明 Recent Searches 零变化。这不是对用户日常 Chrome 状态的判断。

## Gate ledger

### 2026-09-30 前轮收口记录（由上方最终收口补充）

- 继续使用上述 nonempty 隔离 root、28420 与同一已安装扩展，未改变用户主数据、模型路由或站内互动。打开实际 popup 推荐页、桌面 `/web/`、移动 `/m/`（390×844），三处均观察到真实推荐的标题 `Samsung screen technology` 与作者 `technocraj`；桌面/移动未出现横向溢出。此前 popup 未观察到内容的现象未复现。这里只证明该真实长标题卡，未覆盖无封面/文字卡全矩阵，也未将 popup 同路径视作原生 side panel 已验收。
- 新增一个真实、每 scope cap=2 的 smoke-only bootstrap；确认 local durable state 存在、已有任务 tab、尚无 pending final 后，通过 Chrome 扩展管理页重新加载扩展。原任务随后 completed，result=partial、liked=2/saved=2/following=1、error=`instagram_liked:item_cap_reached`，扩展 durable state 清除、任务页关闭。此为真实 in-flight **extension reload** 恢复，不冒充完整浏览器进程 kill 或网络 ACK 丢失验证；smoke-only 不写新画像。
- 本轮 Python 来源与对话布局定向 **107 passed**；扩展全量 **1448 passed**；Ruff、MyPy（262 文件）和 diff whitespace 通过。没有修改偶发滚动测试或宣称重新跑过全仓；原全仓失败仍保留。最终 verdict：**blocked（full acceptance）**，已通过真实有界端到端，不等于所有 required gate 完成。
- 收尾事件仍为 5；本轮三个界面 tab 已关闭，隔离后端 PID 74674 已发送正常退出信号，保留隔离 DB/日志与用户授权站内样本。
- 剩余：多页账号样本、完整 guided-init UI、原生 side panel/无封面卡矩阵、完整 browser kill、真实第二来源长任务并发、Recent Searches 前后审计，以及 Meta production permission。没有扩大点赞/收藏/关注样本；跨源验收需要明确第二来源及其可用登录环境。提交/合并/发布未请求，不执行。

| Gate | Applicability | Status | Evidence / remaining work |
| --- | --- | --- | --- |
| Frozen contract | required | PASS | Contract 已冻结 capability-specific auth、topic/creator discover、三 scope init、strict-empty 与 read-only boundary |
| GitHub implementation research | required | PASS | 固定 commit、license、endpoint/envelope/cursor 与近期 failure issue 已记录在模块文档；GPL/DMCA 代码未复制 |
| Canonical registration / audit | required | PASS | 修正封面proxy契约后审计34 PASS/10 N/A/14 MANUAL/required_missing=0；仅证明接线，封面DNS/SSRF手工安全验证单列，不被略过 |
| Normalization / stable identity | required | PASS | tests 覆盖 media/user numeric ID、canonical URL、类型、taken_at 与 unknown engagement；新增真站 `polaris_ordered_timeline_connection` 精确形状回归 |
| Capability-specific auth/status | required | PASS | Auth 组合回归证明 discover optional-credential（未登录 ready）、private heartbeat gating、首次 verify action；extension cookie tests 证明只上传 boolean |
| Durable task queue / first-final | required | PASS | Queue/protocol tests 覆盖 claim token、stage-first、duplicate/replay、machine-code-only diagnostics、failed+items 拒绝与 first-final |
| Guided init / event ingress | required | PASS | 自动化覆盖三类事件、账号分区、smoke/partial；新增 API/CLI 来源路由、fresh/recovered task 所属、flush 小批量且保留 hard cap，最终定向 129 passed |
| Formal discover / candidate pipeline | required | PASS | Producer tests 覆盖 topic/creator 正规化、预算、dedupe、owner lease、late-result adoption、绝对 cycle deadline、pool gate 与 handoff retry |
| Full repository regression | required | PASS | 2026-10-03最终后端8484 passed/60 skipped，1518.81秒、exit0。滚动测试改为真实wheel有界条件等待；Ruff 627文件/MyPy 263文件通过。旧失败日志仍保留 |
| Extension isolation / MV3 recovery | required | PASS | 最终1535/1535；停滞、限速/idle、争锁、首屏迟到/跳转、SSR公开证明/身份命名空间/缓存撤销及有界重扫回归。独立review原反例全部关闭，真站结果单列 |
| Chrome + Firefox build assets | required | PASS | Chrome 与 Firefox build 均完成，两个 asset verifier 均确认 21 个 manifest scripts/WAR assets |
| Anonymous upstream smoke | required | PASS | 最终Firefox ZIP真实topic/creator各3条，partial/item_cap_reached且response_observed=true，严格脚本exit0；host permission=true/sessionid不存在，正常poll领取，只有任务表变化、业务/LLM为0、active=0 |
| Current installed build provenance | required | PASS | 2026-10-03 final-artifacts.json：Chrome最终ZIP安装态8项文件hash匹配，Firefox最终ZIP重新实装4项匹配；CRC与21资产通过。版本仍0.3.204，无商店发布 |
| Failed initial discovery feedback | required | PASS | 真实失败库三端空态＋最后安装版连续两轮快速启动/实时失败/无执行success保持错误；恢复后seen/重启、新run及历史推荐隔离由真实DB/API回归锁住；这不是新的真站discovery成功 |
| Real installed Chrome discovery | required | PASS (bounded, 2026-10-04) | 最新ZIP安装态8项hash匹配；网络自行恢复后重启前后topic/creator各2条，response_observed=true，exit0、active=0、业务/LLM零写；不表示长期代理稳定，Oct3断连失败保留 |
| Real logged-in bootstrap | required | PASS | 有界非空 2 like/2 favorite/1 follow 与授权对象精确匹配，两轮 0→5→5；历史真实空另记，cap=2 保留 partial，不证明大样本 |
| Real fresh guided-init UI, all four stages | required | PASS (final build, bounded) | 10-05本地日期最终ZIP八项hash匹配；真实popup新空库5事件→当前模型画像→10候选→2条实际显示，170秒。Likes停滞仍partial，不冒称完整历史 |
| Multi-page / nonempty terminal evidence | required | NOT_RUN (remaining account/terminal proof) | 10-05公开topic/creator各30条、cursor_observed、3页预算已通过；本账号saved/following complete，但Likes仍partial，缺大样本账号分页和可靠Likes末页，不用游标存在替代多页完整证明 |
| Real account switching | required | NOT_RUN | 单账号真实数据 + 自动化混账号 fail-closed 已通过；没有第二个授权登录账号，未代替用户切号 |
| Executor account continuity | required | PASS | 执行器级红绿测试复现并修复分页中切号、早期Likes错绑及同用户名/不同数字ID分支；页前后/提交前fresh身份复核，已知风险停止，切号丢弃整份快照；executor定向30项及全量1477项通过，不宣称原子快照或真实双账号已测 |
| Real in-flight extension reload | required | PASS | 原 claim 恢复为 completed/partial、2/2/1 回传，durable state 清除 |
| Real full-browser termination recovery | required | PASS | 终止前确认 in-progress，重启后同 claim `recovery_tab_gone`、无遗留；未有 accepted rows，不冒充非空续传 |
| Installed ACK suppression / replay | required | PASS | 真实已安装扩展、HTTP/API/SQLite；合成上游 0/1 行终态在提交后受控 503，经全浏览器正常重启逐字节重放，200 后清空 outbox，canonical 不变且下游无副作用；不是 TCP 丢包或真站数据 |
| Cross-source mutex | required | PASS | 实际 Linux.do 持锁期间 Instagram 保持 pending/unclaimed；注入延迟/断网后释放、Instagram 随后领取并保留 3 rows。仅故障下互斥，不是第二来源数据 E2E |
| Real candidate / recommendation LLM | required | PASS | 保留当前配置路由，真实非空 Instagram 事件生成的画像驱动 topic/creator 评估与推荐，未调阈值；早先已有画像的下游证据不混为同次 E2E |
| Real bootstrap/profile convergence | required | PASS | 有界 5 条真实账号事件经当前 deepseek-v4-flash 完成偏好及画像构建；未复制旧画像，不替代 fresh guided UI 或大样本验收 |
| Profile-term topic seed availability | required | PASS | 有界真实新画像 formal topic/creator discover 已通过；未知词仍按上游可用性 fail closed，不承诺任意词均有内容 |
| Recommendation surfaces: real card | required | PASS | 实际 popup、desktop、mobile 及原生 SIDE_PANEL 展示真实标题/作者，无横向溢出；原生侧栏 context 单独确认 |
| Recommendation surfaces: no-cover/text matrix | required | PASS | 明确合成 Instagram DTO → 实际 desktop/mobile/已安装 popup 页面/原生 SIDE_PANEL；3 类文字卡无横溢出且动作可命中。移动浮层遮挡已修复并有 3 宽度回归；不冒充真站请求 |
| Media proxy DNS / redirect / SSRF boundary | required | PASS | 每hop所有地址公网验证+数字IP固定，保留Host/TLS；代理经固定DoH，不信任未验证域名CONNECT。103项受控安全/API回归与真实CDN200/实际popup封面加载分别证明安全逻辑和功能，不把真图当攻击测试 |
| Firefox installed account E2E | required | BLOCKED | 最终 ZIP 再次实装与本地 fixture 执行通过，但没有已登录 Firefox 账号环境；普通网络已恢复，不复制 Chrome Cookie |
| Local packaged artifacts | required | PASS | Chrome/Firefox 两份 0.3.204 ZIP 已构建并记录 SHA-256，不代表发布或全新安装通过 |
| Chrome packaged artifact fresh load | required | PASS (earlier build) | 心跳/UI版本 ZIP 新解压 + 新无 Cookie profile 开发者加载、popup设置路径与3项运行bundle hash匹配；账号连续性版本由既有授权profile复验，不冒充新profile授权、匿名任务成功或CRX/商店安装 |
| Firefox packaged artifact fresh load | required | PASS | 最终 Firefox ZIP 已在152.0.1新profile重验，document_start MAIN、跨world/replay、实际creator executor与四项hash全通过；仅本地fixture/0上游转发，不是签名商店或账号E2E |
| Upstream mutation audit | required | NOT_RUN (partial evidence) | 10-03 Recent Searches子审计；10-05完整init被动请求分类及discovery前后2/2/1账号集合一致、业务零写通过。GraphQL/Bloks/分析日志未全面分类、Likes未完整，不据此推断全站状态不变 |
| Meta permission / product approval | required for production | BLOCKED | Instagram/Meta 条款要求自动采集授权；本分支的技术实现与默认关闭不能替代书面许可或产品风险决定 |
| Documentation / release boundary | required | PASS | 模块/API/CLI/init、变更日志、四份图、privacy/config/安装器文档同步；无新配置字段，httpx下限0.28/httpcore显式依赖随既有安装流程安装。仅本地ZIP/wheel，不bump/sign/commit/merge/push/publish |

## Verification commands

```bash
PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/pytest -q \
  tests/test_instagram_source.py tests/test_instagram_tasks.py \
  tests/test_instagram_contract.py \
  tests/test_instagram_wiring.py tests/test_instagram_discovery_resilience.py \
  tests/test_source_bootstrap.py \
  tests/test_source_auth_contract.py tests/test_web_guided_init.py

PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/pytest -q \
  tests/test_instagram_surface_matrix_e2e.py
# 此项需本机 Chrome + Playwright；使用本地合成 DTO，不请求 Instagram/LLM。

PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/python \
  scripts/audit_platform_source.py \
  --contract docs/platform-source-contract.instagram.toml --check --json

cd extension
npm test
# 本轮全量扩展复验限制并发，避免与 Python 全仓并跑时的已有 timer 测试抖动：
node --test --test-concurrency=4 --experimental-strip-types tests/*.test.ts
npm run typecheck
npm run build && npm run verify:assets
npm run build:firefox && npm run verify:assets:firefox
```

2026-08-12 最终静态/模拟验证：Instagram focused 87 passed；全仓 pytest 8352 passed、60 skipped；extension 1423 passed；全仓 Ruff/MyPy 与两套 build/asset PASS。真实账号、真实已安装扩展和 Meta permission 三类门禁仍保留独立 provenance，不能由这些结果替代。

## 2026-08-31 真实 E2E 中间记录

- 隔离后端绑定 `127.0.0.1:8420`，project/data root 为 `/tmp/openbiliclaw-instagram-e2e.y0N7Rw`，scheduler disabled；`openbiliclaw` 从当前 worktree `src/` 导入。测试前相关 event、seen、candidate、profile/soul 均为空。
- logged-out topic discover 经真实 Chrome、扩展 WebSocket、`next-task`、Instagram 页面请求与 `task-result` 完成：返回 5 个稳定数字 ID/canonical Reel URL，终态 `partial/item_cap_reached`，`response_observed=true`。重复提交同一 canonical result 得到 `ignored=true`，任务状态与 result hash 不变。
- creator 任务在旧安装产物上以 `response_envelope_unobserved` fail closed；同一公开 profile 页面真实 SSR 有 12 个 post/reel links，集合路径为 `xig_user_by_username.polaris_ordered_timeline_connection`。当前 worktree 已兼容该 key 并补精确 parser 回归，但必须重新加载 build 后才可把真实 creator 行升级为 PASS。
- logged-out `fetch-instagram --force` 在旧安装产物上把普通登录表单的 `required` 属性误判为 `challenge_required`；当前 worktree 已收紧为明确 challenge 证据，并补 `accounts/login + required` → `login_required` 回归。安装版复验仍是 NOT_RUN。
- smoke 只新增允许的 task/task-result：events、seen、candidate/cache/producer run、schedule、profile ledger 均保持 0，未创建 `soul.json` 或 bootstrap state。任务前后原 active Instagram tab 未切换，自建任务 tab 终态后关闭；没有执行任何上游状态变更动作。
- 修复后 extension 定向测试 19/19、TypeScript typecheck、全量 extension suite、Chrome/Firefox build 与两套 21-asset verifier 均通过。当前安装产物 ID/version/hash 尚未由用户在 `chrome://extensions` 重新加载并取证，因此真实已安装行保持 `NOT_RUN`。

## 2026-09-26 隔离真实环境验收

以下是首轮历史记录；当时未授予权限 / 未登录的结论已被文末后续状态取代，不能作为当前阻塞原因。

### 环境与证据边界

- 后端来自本 worktree，独立绑定 `127.0.0.1:28420`；临时根 `/tmp/openbiliclaw-instagram-e2e-20260926.5ZbNE7`。没有修改日常服务 `18420`、真实数据库或主工作区。
- 完成重启验证后已停止隔离后端，避免测试结束后继续产生模型调用；保留隔离数据与浏览器 profile 以便授权 / 登录后续验，不删除或覆盖日常环境。
- 独立 DB 初始 tasks / events / seen / candidates / cache / profile ledger 全部为 0；仅启用 Instagram，周期 scheduler 与账号增量关闭。事件消费、推荐文案修复是独立 runtime lane，不能把 scheduler 关闭误解成所有后台工作停止。
- 使用用户现有真实 LLM 路由与本机 Ollama embedding；没有改用测试模型、伪造评分、降低准入阈值或填充假个人事件。配置与画像副本仅保留在私有本地临时目录，报告不保存 key、Cookie、原始个人响应或画像内容。
- 先以空画像测试初始化失败边界；之后才复制已有的 Soul / preference / overrides 到隔离目录，以便独立验证下游。**这份画像不是由 Instagram 初始化产生的。**
- Chrome for Testing 加载当前扩展 `0.3.204`，ID `eikbajehgamillcimgogeobbngdemcla`。测试进程读取安装内产物计算 hash，与当前 worktree 构建一致：

| 产物 | SHA-256 |
| --- | --- |
| manifest | `eee447530d0147911debef2d591242016a81ad7ee70760eeb56c62d75a5fe5ec` |
| service worker | `ae77107aa123fa4001d41f70516fd2c88c793c720ba9de2c3666b480335ae9b0` |
| Instagram content script | `1431af07c5e9899221373ae6d9d7f7843bc9f4dbfdf0ef724bc8ad9910442359` |
| Instagram MAIN tap | `b34c5f1131b1fe17477bfb6197cd3ec8023a261b1d7943ec431b354d234f0dde` |

### 场景结果

| 场景 | 结果 | 实际证据与限制 |
| --- | --- | --- |
| 首次设置：仅选 Instagram、未登录 | PASS（失败路径） | 实际 setup UI 完成模型配置、探测与来源选择；在“账号初始化未就绪”停止。没有创建个人事件、任务或 soul；没有因未选 B 站而要求先登录 B 站 |
| CLI 无画像 discover | PASS（失败路径） | `discover --source instagram --limit 4 --force` 明确返回尚未初始化画像，exit 1，不制造候选 |
| 扩展设置 / 站点授权 | BLOCKED | 启用保存触发浏览器原生授权，`permissions.contains` 仍为 false，页面等待“保存中”。不能从桌面设置保存成功推断浏览器授权已授予，也没有通过修改浏览器配置绕过授权 |
| 私人 current-account / liked / saved / following | NOT_RUN | 没有测试浏览器登录。未验证分页、真实空列表、账号绑定、第二轮幂等或个人事件建画像 |
| 公开 creator | PASS（浏览器 / 内容读取） | `/setupspawn/` 返回 12 个公开 SSR nodes，集合为 `polaris_ordered_timeline_connection`；没有 publication timestamp 的条目保持未知 |
| 公开 topic | PASS / FAIL | `technology`、`music` 返回 HTTP 200 与 12 个 media nodes；`anime`、`动漫` 显示页面不可用，无对应 envelope / media links。后两者未记录 HTTP status，不把它们写成“已证实 404” |
| 真实 parser → 候选管线 | PASS（分段） | 6 条 creator 白名单 rows 和 6 条 music rows。music 使用当前生产 TS `parseInstagramObservedPayload` 解析真实公开响应的最小字段摘录；两批各新增 6，重复入队各 0。没有伪造 task-result / claim / bootstrap 证据 |
| 真实候选评估 | PASS，有性能风险 | 第一批 6 条均低分拒绝，约 116 秒；第二批 6 条中 2 条入池、4 条拒绝，约 101 秒。候选合计 12、rejected 10、cached 2；没有强行放宽门槛 |
| 真实推荐与文案 | PASS | durable `llm_usage` 记录 2 次 `discovery.evaluate_batch` 和 2 次 `recommendation.write_expression` 成功；生成 2 条推荐记录 |
| 桌面 / 移动 / 插件展示 | PASS | 真实页面显示 Instagram 来源、作者、封面和中文推荐文案；移动端 390×844 两张封面均加载、无横向溢出；反馈后插件显示剩余卡片与确认提示 |
| 本地收藏 / 喜欢 | PASS | 从移动页面操作后产生 favorite membership、推荐 like 投影及 Instagram feedback event；`native_save_tasks=0`，没有对 Instagram 点赞或收藏 |
| 反馈请求重放 / 冲突 | PASS | 使用原始 request ID 重放得到相同 event ID、`duplicate=true`；相同 ID 改为 dislike 返回 409，原反馈保持 like。首次探针误用带 `feedback:` namespace 的数据库 key，形成另一个独立请求，因此最终测试库有 2 个 feedback event；这是探针多提交一次，不是相同 request ID 去重失败 |
| 反馈学习 | PARTIAL | 已有画像上触发真实 `soul.posture_gate` / `soul.preference.chunk` 调用；观察到 5 条成功审计（1 次 feedback layer update、1 次 shadow-accept rebuild、3 次 topic lifecycle）。尚未验证所有信号最终收敛，也不是 Instagram bootstrap 画像 |
| 后端重启与配置持久化 | PASS（无浏览器任务） | 正常终止并重启隔离后端：12 candidates、2 cache、2 recommendations、1 favorite、2 feedback event 及 like 投影保留；tasks 与 native-save tasks 始终 0。桌面重新加载后 enabled、share=2、bootstrap=10 等配置仍一致 |
| MV3 中途恢复 / 跨源 mutex / 账号切换 | NOT_RUN | 需要先解决真实扩展授权 / 登录前置条件；模拟回归不能替代这些真实场景 |

### 发现的问题与处理

1. **P1 / 未修复：自动 topic seed 不是可用路由。** 当前画像关键词 fallback 直接拼 `/popular/<keyword>/`；相同环境中有些词不可用，而 `_run_topics` 遇到 failed 即返回，连带阻止 creator。需要有效 topic 映射 / seed 验证及有界失败隔离；不能仅把词翻译成英文，也不能把 challenge / 429 当可继续重试的普通 seed 失败。
2. **P1 / 环境风险：模型轻量探测成功不代表长内容请求稳定。** 首选真实服务返回 429，第二路出现 HTTP 200 但空内容，经历取消 JSON 约束 / 关闭 thinking 的有界重试后才由后续路由成功。真实候选批次耗时约 100–116 秒；有一次时间信息解析失败降级为 unknown。没有擅改用户模型顺序或额度。
3. **P2 / 已修复：桌面设置漏了实际 Instagram 来源卡。** 仅在状态标签表中注册不足以让用户配置。现补齐 HTML、配置填充 / 更新、分支预算、节流、bootstrap cap、候选份额和份额建议，复用既有卡片与字段标签；截图复核另补 Instagram 标志背景，避免白字落在浅色背景。真实 UI 保存后等待 apply revision 2 达到 `applied`，刷新与重启回读一致，原 LLM 配置结构未变。
4. **P2 / 待补验收：首次授权提示仍依赖扩展原生权限弹窗。** 桌面开关不能代替 optional host permission，未确认权限时扩展保存停留等待。本轮增加桌面卡片说明，但没有把“保存配置”当作站点可访问，也未证明取消授权 / 超时提示体验。
5. **前次真站问题已保留修复与回归。** creator SSR 新 key 和普通登录表单 `required` 误判已在工作树修复；当前构建包含这些变更。它们仍需在已授权 dispatcher 中做完整任务复验。

### 自动化验证与可复现入口

- 全仓 Python 首轮：`8354 passed, 60 skipped, 1 failed`，耗时 22 分 23 秒。唯一失败是旧 Linux.do / V2EX 测试把新增第三卡误算进“末尾两卡”；修正局部结构断言后，新增 Instagram + 相邻桌面源 + 微博 wiring 共 **33 passed**，再跑全部 `tests/test_desktop_web*` 为 **242 passed**（147 秒）。未将分组复验谎报为第二次全仓全绿。
- 来源 / 初始化重点回归：**484 passed, 34 skipped**；来源 audit **33 PASS / 10 N/A / 14 MANUAL / 0 missing**，registration check 为 true、fully verified 为 false；source contract metrics **6/6**。
- Extension：**1424 passed / 0 failed**；TypeScript typecheck PASS；Chrome / Firefox build 与各 **21 个** asset verifier PASS。未做 Firefox 安装态 E2E。
- Ruff 全仓 PASS；MyPy **262 source files** PASS；桌面 JS syntax check 与 `git diff --check` PASS。
- 浏览器截图在上述临时根中：`desktop-instagram-settings.png`、`mobile-recommendations.png`、`popup-recommendations.png`。临时探针与最小公开字段样本同目录，只作本机复现，不提交配置 / 画像 / 原始个人数据。

复验剩余主路径：用户在独立测试浏览器授予 Instagram 站点访问并自行登录 → 用当前已确认 hash 的 build 执行仅 Instagram 初始化 → 检查 current-account 与三个 scope → 第二次初始化验证幂等 / 账号分区 → 修复 topic seed 后从正式 dispatcher 完成 discover → 评估 / 展示 / 反馈 → 在真实任务中验证 worker、扩展、后端恢复与跨源 mutex。全部 required 门禁通过前，不可把本来源升级为完整真实 E2E 通过。

## 2026-09-26 问题修复与复验

### 已修复与回归证据

1. **无效 topic 阻断整个 cycle**：`动漫/anime` 等已知别名映射到当日有匿名内容证据的宽 topic；映射后去重，未知词仍是有界候选路由。专属 Page 不可用标题和断链正文必须同时成立才能返回 `public_page_unavailable`，不能因登录按钮、caption 或没有 envelope 就判定软 404。只有该错误继续下一个 seed；auth / challenge / rate / schema 错误即使带 partial rows 也停止后续任务。准入、评分、模型路由未放宽。
2. **failed task 丢失具体原因**：队列 failed 行已有 staged canonical payload，旧 producer 却只读取 completed，导致 `public_page_unavailable` 降为泛化 failed。现在两种终态均解析 canonical machine code；真实队列状态回归先红后绿。partial schema failure 不继续派发、但保留有效 rows 的回归同样先红后绿。
3. **原生站点授权无限等待**：设置保存最多等待 30 秒，显示等待授权 / 未保存，超时解锁并保留编辑；迟到授权不会自行提交已放弃的保存。本轮 VM 执行真实保存 handler 的 pending-permission 测试先红后绿。由于权限随后已授予，没有再撤销权限强造一次真人原生超时，自动化与真实 UI 证据分开记账。
4. **浏览器重启丢失 task/outbox**：从 `storage.session` 改为有界 `storage.local`，旧状态单向迁移。原任务页丢失时以原 claim 回传 `recovery_tab_gone`；无 ACK 则保留 exact-result bytes，后续 worker 重试同一 payload，ACK 后清理。空 session / local 已存 claim 及 503 → worker 重建 → 200 的行为测试通过，不注入假任务到真实后端。旧环境一次浏览器进程关闭暴露此问题，其关闭原因未确定，旧 stranded claim 未冒充恢复成功。
5. **恢复收尾可能误关普通 tab**：模拟浏览器重用旧 tab ID 时，原实现会关闭不带 marker 的普通页面；未完成任务与 pending final 两个入口均先红后绿。收尾现重新确认 Instagram HTTPS host 与任务标记，普通页面保留。
6. **重复模型空响应兼容重试**：通用 OpenAI-compatible provider 仅在非空且 JSON mode 解析有效后，记住实例内、模型 / reasoning / JSON mode 隔离的兼容参数，最多 32 组，不写配置。不复用失败或空结果，不跨模型；连续调用的自动化从两次各三请求降为三请求加一请求。真实复验没有重现空响应，因此不宣称已真实证明这项提速。

### 最新真实请求与隔离边界

- 新建 `/tmp/openbiliclaw-instagram-fixes-20260926.qm8mgw`（目录 `700`，配置 `600`），不复制 DB、事件或画像。仅沿用原有真实 LLM 路由；后端仍为 worktree `src/`，绑定 `127.0.0.1:28420`，scheduler 与增量拉取关闭。日常 `18420` 和主工作区未修改。
- 当前独立测试浏览器站点权限为 true；曾读取到 `sessionid` **存在布尔**为 true，未输出 Cookie value。它们不是 current-account 身份成功证据。
- `fetch-instagram --force --wait-seconds 180` 经真实 dispatcher、同源请求与回调，终态 **failed / challenge_required**。liked / saved / following 均为 `0`、complete 均为 false。已停止私人请求和安全验证自动化，不尝试绕过。
- 最终 fresh DB：Instagram task 为 1；events、seen、candidates、cache、producer runs、profile ledger、native-save tasks 与 init runs 均为 0；没有 `soul.json`。这里只做 `fetch-instagram` smoke，不把它冒称完整 guided init 建画像。
- 前一隔离根的正式 public discover：已授权 dispatcher 读取 `technology`，页面可见 24–36 个内容链接，但已安装 tap 未识别到匿名 media envelope，终态 **failed / response_envelope_unobserved**、items=0。页面自身出现 `xdt_fbsearch__top_serp_graphql` 与其他私有分支，未作为匿名内容解析或重放；DOM 链接也未被伪装成完整 media 结果。该环境仍保留一次进程关闭留下的旧 claim，未再启动它的后端。
- 更早同日匿名只读探针：`anime/photography/fitness/food` 均为 HTTP 200 的专属不可用页面（软 404）；`animation/art/technology/music/gaming` 观察到公开内容。它们只支持当时的 seed 候选选择，不保证登录上下文或将来同一路由有效。
- 保持真实模型链，对两条已脱敏公开 caption 做连续两次 JSON 分类：3.59 秒、2.14 秒，均返回合法 JSON。第一次首选服务 `RateLimitError` 后由既有备用路由成功；第二次按现有冷却跳过首选。首选额度 / 限流仍未解决；这是短分类探针，不是第二次完整候选批评估，更不是私人画像初始化。
- 最终只对本地静态扩展资源做 hash 核验；没有继续访问 Instagram。任务 local / session 状态均已清空，带临时响应观测脚本的自建公开页已关闭。已正常停止隔离后端，保留本机测试数据与浏览器 profile 供人工完成验证后续验。

### 最终自动化与构建

- 最后一次 Chrome unpacked reload 后，仅从已安装扩展读取静态资源，以下 SHA-256 均与 worktree 磁盘一致。扩展 ID `eikbajehgamillcimgogeobbngdemcla`、版本仍为 `0.3.204`；未发布新版本。权限仍为 true，local / session 均无待处理 Instagram task。此时后端已停止，popup 的 `ERR_CONNECTION_REFUSED` 是本地离线预期，不计作 Instagram 请求失败。

| 最终已安装产物 | SHA-256 |
| --- | --- |
| manifest | `eee447530d0147911debef2d591242016a81ad7ee70760eeb56c62d75a5fe5ec` |
| service worker | `955b0a6853580ffa6f742e92442902ad8a88c4d4127d16b740803b08cf531956` |
| Instagram content script | `e8c5add4bf8c5cc9ae630c82fa32eff89ee23e1b729a2712e5db2cadf1cf7343` |
| Instagram MAIN tap | `b34c5f1131b1fe17477bfb6197cd3ec8023a261b1d7943ec431b354d234f0dde` |
| popup | `4fc85d9eadf705309c6838a6e141631f57ae4867257d4953df9c4fe26998685a` |
| popup helpers | `bf678bcd4f6104ad5cf45d2674cdc8d23e5d8afa174a9cbf43e04926f341602c` |

- Python 全仓：**8365 passed、60 skipped**，1280.86 秒；运行期间仍在追加 producer 终态错误处理，不将其称为最终树完整重跑。追加修复后的来源 / 队列 / 契约 / LLM / 桌面 Instagram 组合定向复验：**186 passed**，35.67 秒。
- 扩展最终全量：**1430 passed、0 failed**，30.47 秒（`--test-concurrency=4`）。此前默认并发且与 Python 全仓同跑时，已有 Reddit native-save 测试遇到一次 50 ms 定时断言抖动；该文件独立 **22/22**、限并发全量及最终全量均通过，未修改那个测试或隐藏失败。
- TypeScript typecheck、Chrome / Firefox build 与每套 **21 个** manifest/WAR assets 检查均通过。Firefox 安装态真账号 E2E 仍未执行。
- 全仓 Ruff PASS；MyPy **262 source files** PASS；`git diff --check` PASS。来源 audit **33 PASS / 10 N/A / 14 MANUAL / 0 required missing**、`fully_verified=false`；source contract metrics **6/6**。
- 修复轮日志：fresh 临时根的 `python-final-focused.log`、`extension-final.log`、`extension-instagram-final.log`、`extension-reused-tab-red.log`、`mypy-final.log`、`chrome-build-final.log`、`firefox-build-final.log`、`source-audit-final.json`、`source-metrics-final.log`、`bootstrap-smoke.log`、`llm-real-probe.log`。Python 全仓日志在前一临时根的 `python-fixes-full.log`。配置、Cookie、原始个人响应均不写入仓库。

### 当前未闭合门禁

此处是用户确认登录前的阶段结论；当时要求人工验证的判断已被下节纠正。current-account、三 scope、分页 / 幂等 / 账号分区与 Instagram-only guided init 仍未通过。公开发现必须另行验证稳定的匿名响应契约，不能把已登录页面的私有 SERP 直接加进匿名 parser。真实 in-flight worker / extension / backend 恢复、跨源 mutex 和上游状态前后对照也尚未完成；不合并、不发布、不更改生产配置。

## 2026-09-26 用户确认登录后的复验

### 真站证据与原判断纠正

- 仍在 `feat/instagram-source@6975e9e9` 的未提交工作树，Python import 指向本 worktree；继续使用私有隔离根 `/tmp/openbiliclaw-instagram-fixes-20260926.qm8mgw`，端口 `28420`，scheduler / incremental 关闭、bootstrap cap=10。日常 `18420` 未修改。
- 已安装扩展、站点 permission 与 `sessionid` 存在布尔均确认，初始无 pending local task。新打开的普通首页没有登录表单、登录链接或 challenge 路由；不以这项 DOM 观察冒充数字身份验证。
- 正式 `fetch-instagram --force --wait-seconds 180` 再次得到旧执行器的 `failed/challenge_required`，exit 1、所有 scope 计数为零。随后单次脱敏诊断观察到同一路径实际为 **HTTP 200 / text/html、redirected=true、finalPath=/、866210 bytes**；有脚本 challenge 字样，无可见 challenge 文本 / 表单 / 登录表单。由此确认旧分类器存在误报，不能据它继续要求用户安全验证。
- GitHub 协议复核发现 instagrapi 的 [`account_info()`](https://github.com/subzeroid/instagrapi/blob/13ebe3b73f9a3fc2d495124c94d958d7b438e007/instagrapi/mixins/account.py) 使用 private request，而其 [`API_DOMAIN`](https://github.com/subzeroid/instagrapi/blob/13ebe3b73f9a3fc2d495124c94d958d7b438e007/instagrapi/config.py) 为 `i.instagram.com`；不能仅复制 path 就认定 `www` 同源路径可用。浏览器对 `i` 的一次原样请求返回 **403 JSON、无 user 身份字段**，没有模拟移动设备或改写 User-Agent。`www` 不带 edit 的变体为网络失败，未据此推断登录失效。
- 同源只读 `GET /api/v1/accounts/edit/web_form_data/` 返回 **200 JSON / status=ok**，含当前 username，但没有可接受的数字 ID；只输出字段名和布尔结论，没有导出用户名、email、电话、生日或响应正文。该结果确认网页会话有效，不等于完成本源数字账号分区。
- 尝试以该权威 username 请求公开 `web_profile_info` 解析数字 ID 时，收到 **HTTP 429 / text/html**，未得到 user，立即停止上游请求。没有把 Cookie ID / DOM 用户名补成“已验证账号”，也没有将未经真站成功与游客对照的新身份链落入生产。

### 修复与验收边界

- HTML 分类先剔除 script/style/template/noscript/comment，metadata / 普通标签属性不参与诊断；明确表单、可见错误文本与真实 final URL 仍识别 login / challenge。未知首页 HTML 返回 `html_response`，不返回 empty。
- JSON 只对错误字段值分类，避免 `checkpoint_url: null` 的字段名触发 challenge。真实 HTTP 429 始终优先为 `rate_limited`。
- 四条新增回归均先红后绿：bootstrap 收到首页 bundle 不误报验证、metadata 不冒充登录 / 验证、空 checkpoint 字段不误报、真正跳往 challenge 的空壳页面仍终止且不请求 scope。第一条来自真站最小脱敏反例，其余为边界合成回归，不冒称真实上游响应。
- 本轮仅改扩展分类器、回归及文档，**没有完成身份接口替换**。遭遇 429 后未重跑上游正式 smoke / guided init / discover；最终构建与自动化通过不能升级这些真实门禁。
- 最终隔离库 tasks 从 1 增至 2（均是历史 first-final failed 记录，不改写）；events、seen、candidates、profile ledger 均仍为 0，无 soul。自建 Instagram 诊断页已关闭，隔离后端正常停止；未复制任何 Cookie 或私人响应出浏览器。
- 最新自动化：扩展全量 **1434 passed / 0 failed**（32.17 秒）；Instagram 后端 source/contract/tasks/wiring/resilience **99 passed**（14.80 秒）；TypeScript、Chrome/Firefox build 与各 **21** assets 检查通过。Python 源码未改，本轮未重新跑全部 Python 仓库；先前 8365 全量与 186 最终定向证据保持原范围。
- 日志位于隔离根：`bootstrap-login-recheck.log`、`identity-html-red.log`、`identity-html-green.log`、`identity-metadata-red.log`、`identity-metadata-green.log`、`identity-redirect-red.log`、`identity-redirect-green.log`、`identity-null-checkpoint-red.log`、`extension-login-recheck-final.log`、`python-login-recheck.log`、`chrome-login-recheck-build.log`、`firefox-login-recheck-build.log`。

### 本轮最终安装态核验

修复后重新加载 Chrome unpacked 扩展，仅读取安装态静态资源核对 SHA-256，以下四项均与当前 worktree 构建一致；本表取代上节修复前的对应 hash。扩展 ID 仍为 `eikbajehgamillcimgogeobbngdemcla`、版本 `0.3.204`，local / session 均无 pending Instagram task。没有为核验产物再次访问 Instagram，也没有把安装成功当作真实初始化通过。

| 最终已安装产物 | SHA-256 |
| --- | --- |
| manifest | `eee447530d0147911debef2d591242016a81ad7ee70760eeb56c62d75a5fe5ec` |
| service worker | `d409832e76bb38dca7eb6d3b019141489f67876a617b9643b1bdf2efa59cb41f` |
| Instagram content script | `06f4ba49dde16230c9944e58afbd85851f2b1a55696fc32232a7ce774dcd2de6` |
| Instagram MAIN tap | `b34c5f1131b1fe17477bfb6197cd3ec8023a261b1d7943ec431b354d234f0dde` |

当前 verdict：**incremental only / live acceptance blocked**。已登录是真实状态；误报逻辑已修复；现用身份路由与匿名 discovery 仍未打通，真实请求受 429 限制。下一轮应先验证安全、稳定且能返回当前数字账号的网页协议，再实现身份适配，不通过反复重登或刷旧接口碰运气。

## 2026-09-26 当前配置 LLM 请求链复验

用户确认本轮测试对象为项目当前配置的 LLM 模型。范围为 **audit-only / LLM 请求链**，不改变 provider、model、路由顺序或生产配置，也不重新请求 Instagram。

- 只读加载主项目当前配置（包含本地及环境覆盖），确认 `llm` 配置与隔离根快照一致；Python import 仍指向本 worktree。默认链为 `glm-5.3-flash → deepseek-v4-flash → sensenova-6.8-flash-lite`，soul / discovery / recommendation / evaluation 均继承默认链。
- 复用本机 `llm_real_probe.py`，仅增强脱敏结果检查与状态码 / 模型 / usage 记录。输入为前次已脱敏的两条公开 caption，经真实 `build_llm_registry(config).complete(..., json_mode=True)` 连续调用两次，没有替换 provider 或模拟响应。验证合法 JSON、items 数量、输入输出 ID 对齐和非空 topic。
- 命令：在 worktree 执行 `OPENBILICLAW_PROJECT_ROOT=/tmp/openbiliclaw-instagram-fixes-20260926.qm8mgw PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/python /tmp/openbiliclaw-instagram-fixes-20260926.qm8mgw/llm_real_probe.py`，**exit 0**。

| 调用 | 实际链路与结果 | 总耗时 | 返回 token usage |
| --- | --- | --- | --- |
| 1 | GLM HTTP 429（0.21 秒）→ DeepSeek 成功；JSON / items / ID / topic 全部通过 | 3.92 秒 | prompt 396 / completion 275 / total 671 |
| 2 | registry 自动跳过冷却中的 GLM → DeepSeek 成功；相同结构检查全部通过 | 3.66 秒 | prompt 396 / completion 225 / total 621 |

实际成功实例均为 `openai_compatible`，请求模型和响应模型均为 `deepseek-v4-flash`。第三级 `sensenova-6.8-flash-lite` 未触发，不能据此宣称其当前可用。首选 GLM 仍受限流，备用链成功不等于首选恢复；返回 usage 只统计成功响应，不推断失败请求费用。

本轮结论：**当前配置的 LLM 请求链短结构化调用 PASS（fallback 成功）**。未重跑正式候选批评估、embedding、私人画像初始化或推荐全流程；没有修改产品代码、写入生产 memory / profile 或启动服务。Instagram 来源整体的真实验收仍受前述身份接口与匿名 discovery 门禁阻塞，不能用本项通过替代。

## 2026-09-28 初始化事件与 discovery 再验

### 环境与请求边界

- 仍使用 `feat/instagram-source@6975e9e9` 未提交工作树；主工作区已有其他改动，不修改、不合并。新建私有隔离根 `/tmp/openbiliclaw-instagram-recheck-20260928.rvExnw`（目录 700、配置 600），后端 `127.0.0.1:28420`；仅启用 Instagram，bootstrap cap=10，scheduler / incremental 关闭。通过配置加载器在内存继承当日真实 LLM 配置，不更改 provider/model/顺序，不输出秘密。
- Python import 位于 worktree `src/openbiliclaw`，CLI 为主项目 `.venv/bin/openbiliclaw` 加显式 `PYTHONPATH=src`。安装态 Chrome 扩展 ID/version 与 9 月 26 日相同；manifest/worker/Instagram content/MAIN tap 四项 SHA-256 与上节最终安装表及当前磁盘一致。站点 permission 与 `sessionid` 存在布尔为 true；local/session 无旧 Instagram task，普通事件 buffer/inflight 均为空。
- 空库先测 bootstrap 与无画像 discover。随后只复制前次隔离测试的 soul/preference/overrides 到新隔离目录以独立测试 discovery；**该画像不是本轮 Instagram 初始化生成的**。未复制旧任务、候选或事件。

### 真实结果

| 链路 | 入口 / 结果 | 数据与边界 |
| --- | --- | --- |
| 初始化原始事件 | `fetch-instagram --force --wait-seconds 180`，exit 1，真实任务约 4 秒后 `failed/html_response` | 身份未解析；liked/saved/following 均 0、complete 均 false；无 canonical 事件，不是成功空列表 |
| 无画像正式发现 | `discover --source instagram --limit 4 --force`，exit 1，提示尚未初始化画像 | 不创建 discover task；前置条件失败正确 |
| 有画像正式 topic | 相同正式命令，topic 任务约 82 秒后 `failed/response_envelope_unobserved` | 页面有 60 个 post/reel 链接，无登录表单/可见验证，但未得到契约可接纳 envelope；候选 0，creator suffix 不启动 |
| 独立 creator smoke | 通过真实 `InstagramTaskQueue.enqueue_with_id` 入队公开 `setupspawn`，max_items=4、max_pages=1，真实 dispatcher/kick 执行 | 约 6 秒后同样 `failed/response_envelope_unobserved`，items=0；这是独立传输 smoke，不冒充 formal producer 的 creator suffix 或候选入池 |
| 匿名 HTML 补充探针 | 从扩展页以 `credentials:omit` 请求已测试的 technology URL，HTTP 200 HTML、无重定向 | 没有取得可确证媒体 envelope；没有把 HTML 200、DOM 链接或私有 SERP 当成功候选 |
| 当前会话与数字账号 | 同源账号表单 GET 返回 200 JSON、权威 username 存在；随后 `web_profile_info` 返回 429 HTML | 再次确认不是“用户没登录”；未得到数字账号，立即停止所有 Instagram 上游请求，不改 UA/设备/代理或绕过限制 |

最终只有 3 个真实任务（bootstrap/topic/creator），均 failed；events、seen_items、discovery_candidates、profile_update_ledger、init_runs、recommendations、native_save_tasks 均为 0。尚未验证私人分页、事件幂等、账号分区或原始事件建画像；本轮无候选，故 **LLM eval/embedding/推荐未执行**。先前单独模型链成功不是本次 discovery E2E 证据。

### 新发现与修复

正式 topic 已失败，但原 CLI 仍 exit 0；真实命令和数据库终态共同暴露这个假成功信号。本轮修正为失败/未知 reason exit 1、明确空结果与正常跳过 exit 0，保留机器原因。回归使用真实 CLI、producer、SQLite 队列和文件配置/画像，唯一外部替身是浏览器 kick/result；HTTP 客户端禁止真网络。原断言 `exit_code == 1` 先红（实际 0），修复后通过，并追加登录/验证/限流/HTML/明确空结果矩阵。该离线浏览器替身不写入上面的真实测试库，也不计为真站成功。

新证据：`backend.log`、`bootstrap.log`、`discover-empty-profile.log`、`discover-formal.log`、`cli-exit-red.log`、`cli-exit-green.log`、`cli-exit-matrix-final.log`，均在本轮隔离根。矩阵 **6 passed**；完整 `tests/test_cli.py` 加 Instagram source/contract/tasks/wiring/resilience 合计 **360 passed、60 warnings**（21.76 秒，`python-final-focused.log`）。全仓 Ruff PASS，MyPy **262 source files** PASS（`mypy.log`），改动 Python 文件格式检查与 `git diff --check` PASS。本轮未重跑 Python 全仓或扩展套件，扩展代码与已核对的安装产物未改。没有为复验退出码再请求已经限流的 Instagram。

清理：已关闭自建 Instagram 诊断页与扩展 popup，正常停止隔离后端，28420 无监听，local/session 无 pending Instagram task；保留私有测试数据供复查。未改日常 18420、生产配置或主工作区，未执行点赞/收藏/关注/消息/搜索提交；页面自身的上游状态变化仍缺独立前后对照。整体 verdict 仍为 **incremental only / live acceptance blocked**，不是 complete。

## 2026-09-28 网页身份、原生 Likes 与 creator 修复

### 原因与变更

- `accounts/current_user` 属于移动端协议，网页端返回首页。改为同源账户表单用户名与新鲜 SSR named `PolarisViewer` 校验：外层 `id`、`data.id` 必须相同且非零，`data.username` 必须匹配表单。真实登录正例成立；`credentials:omit` 负例没有 viewer。拒绝 cookie/DOM 标签或公开作者 ID 代替身份。
- creator 实际通过 `/graphql/query` 返回既有 `xdt_api__v1__feed__user_timeline_graphql_connection`；旧监听器漏掉整个入口。补该入口后真实已安装扩展捕获 12 条有效媒体，独立真实队列/dispatcher/result 任务返回 4 条 `partial/item_cap_reached`。
- `/api/v1/feed/liked/` 在网页返回 HTTP 400 `useragent mismatch`。不伪装 UA；改为原生 Likes activity 任务页，被动读取已观察的 `/async/wbloks/fetch/?appid=com.instagram.privacy.activity_center.liked_media_screen` 及对应 refresh/next。仅数据解码，不执行表达式、不重放 POST。当前账号真站响应含严格空状态“你没有赞过任何内容”；没有非空真站样本，非空 AST 测试是自造 fixture，末页未知时保持 partial。
- GitHub 一手协议证据及许可/不可执行风险见 [研究记录](plans/2026-09-28-instagram-web-protocol-recheck.md)。原始私有响应、Cookie、用户名和数字账号不写入文档/fixture。

### 环境与真实结果

隔离根 `/tmp/openbiliclaw-instagram-repair-20260928.33qgHC`（700），配置 600；明确设置 `OPENBILICLAW_PROJECT_ROOT`，worktree `PYTHONPATH`，后端 28420，scheduler/incremental 关闭。沿用此前当前 LLM 配置，不更换模型路由。

一次启动最初遗漏 root 环境变量，路径解析落到 worktree `data/`；已立即停服并显式纠正。该错误运行新增一条 0-row smoke task，核查时间窗内 events/candidates/recommendations 新增均为 0；未删除/回滚未知数据。该运行不算隔离验收，也不证明当前模型路由。主工作区与日常 18420 未修改。

| 检查 | 真实结果 | 不代表什么 |
| --- | --- | --- |
| 最新安装构建 bootstrap CLI | `empty`；identity=true；liked/saved/following 各 0，三个 scope_complete=true | 不证明非空分页、事件写入或画像生成 |
| 无画像 formal discover | exit 1，明确要求初始化画像 | 空账号不能凭空生成用户偏好 |
| 最终构建 + 已有测试画像的 formal discover | exit 1；topic task `failed/response_envelope_unobserved` | 没有把未接纳的登录态 SERP 变成匿名结果；creator suffix 未启动 |
| 独立 creator 队列 | 4 条真实内容，cap-partial | 不是依赖 topic seed 的正式 producer 全链路 |
| 匿名 music HTML 探针 | HTTP 200，未含认可 media key，0 内容链接 | shell 不能作为 empty-success，也不替代执行页面 JS |
| 真实内容→当前模型链（分段） | 从真实 creator task 读取 4 条；新增 4，重复新增 0；首 provider 失败后 fallback，评估 4、低分拒绝 4、推荐 0 | 使用已有隔离测试画像副本，不冒充 Instagram 初始化画像；没有为凑推荐降低阈值 |

最新已安装 manifest/content/tap/worker 与磁盘 SHA-256 一致：

```text
manifest eee447530d0147911debef2d591242016a81ad7ee70760eeb56c62d75a5fe5ec
content  70d856700ea996d386531795973b20925ddb13531b6be07372b739b78534abe3
tap      1f48128501d927504e38247c5f837a483e58c320caed73ddfa3fa4e94180d1f7
worker   de6644546183a85681886a72f8ca0e07fd8d103cacd183042e89d3e6f63151b7
```

### 回归与剩余边界

- `npm test`：1443 passed；Chrome/Firefox build、typecheck、两份 21 assets 检查通过。
- `PYTHONPATH=src ... pytest tests/test_cli.py tests/test_instagram_{wiring,source,tasks,contract,discovery_resilience}.py -q`：360 passed / 60 warnings。不是新的全仓运行。
- Ruff 全 src/tests、MyPy 262 文件、`git diff --check` 通过；来源审计 33 PASS、0 MISSING、14 MANUAL、10 N/A，不把 MANUAL 当 PASS。
- topic 登录态私有 SERP 与匿名契约尚未兼容；已向用户询问是否允许明确支持登录态 topic，不悄悄改变鉴权语义。
- 当前账号没有个人信号，无法验证非空 liked、多页、画像收敛；真实 MV3 in-flight 恢复、跨源并发和上游状态前后审计仍未完成。最终仍是 **incremental / incomplete full acceptance**。

收尾检查：隔离队列 pending/in_progress=0，扩展 local/session pending 均 false；关闭自建诊断页与 popup，正常停止临时 28420 后端，保留私有测试目录。未提交、合并、发布或修改日常服务。
