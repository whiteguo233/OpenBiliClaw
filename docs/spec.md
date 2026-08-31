# OpenBiliClaw — 项目规格说明书 (SPEC) v0.3

> *你的跨平台 AI 内容朋友，比你更懂你想看什么* 🎯

---

## 1. 项目定位

OpenBiliClaw 是一个**本地优先、开源的跨平台个性化内容发现 AI Agent**。它像一个深度了解你的朋友或专属内容编辑——不仅知道你喜欢看什么，更理解你**为什么**喜欢，你**是一个什么样的人**，然后主动去 B 站、小红书、抖音、YouTube、X、知乎、Reddit、Linux.do、Bangumi 和通用 Web 等来源帮你发现那些你会喜欢但自己找不到的内容。
OpenBiliClaw 是一个**本地优先、开源的跨平台个性化内容发现 AI Agent**。它像一个深度了解你的朋友或专属内容编辑——不仅知道你喜欢看什么，更理解你**为什么**喜欢，你**是一个什么样的人**，然后主动去 B 站、小红书、抖音、YouTube、X、知乎、Reddit、Bangumi、微博和通用 Web 等来源帮你发现那些你会喜欢但自己找不到的内容。

**核心理念**：
- 不是冷冰冰的推荐算法，而是一个**有温度的 AI 朋友**
- 不是被动过滤推荐流，而是**主动探索发现**
- 不是浅层兴趣匹配，而是**深层理解人格与需求**

### 与单平台官方推荐的区别

| 维度 | 单平台官方推荐 | OpenBiliClaw |
|------|------------|--------------|
| 推荐逻辑 | 协同过滤 + 热度，容易信息茧房 | LLM 深层理解 + 探索式发现 |
| 用户理解 | 隐式标签，用户不可见 | 深度人格画像，像朋友一样理解你 |
| 控制权 | 用户只能点"不感兴趣" | 对话式调整 + 主动"教"Agent |
| 发现方式 | 基于已有行为推荐相似内容 | 主动搜索、跨领域探索、挖掘潜在兴趣 |
| 推荐语气 | 算法式、无温度 | 朋友式、有人味、有洞察 |

---

## 2. 核心功能模块

### 2.1 🧠 用户灵魂引擎 (User Soul Engine)

**目标**：从"他做了什么"到"他为什么这样"到"他是一个什么样的人"——建立有深度和温度的用户理解。

#### 2.1.1 行为数据采集

**浏览器插件（核心采集入口）**：
- 通过统一 `PlatformAdapter` 捕捉 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Linux.do 普通页面的交互行为；Reddit 初始化 saved/upvoted/subscribed 信号复用插件登录态任务桥，日常 discovery 默认使用 rdt-cli 登录态命令后端，不可用时 fallback 到插件任务。Linux.do 的隔离任务 tab 只运行同源只读 executor、不会启动普通 collector：公开 discovery 支持 search/hot/feed/creator/related，个人 bootstrap 支持 bookmarks/likes/read_history。其余行为链覆盖点击、滚动、停留、评论、点赞、收藏、分享、关注、搜索，以及 B 站特有投币；click 在 capture 阶段记录，scroll 同时覆盖页面和内部 feed / modal 滚动容器
- 通过统一 `PlatformAdapter` 捕捉 B 站 / 小红书 / 抖音 / YouTube / X / 知乎的交互行为；Reddit 初始化 saved/upvoted/subscribed 信号复用插件登录态任务桥，日常 discovery 默认使用 rdt-cli 登录态命令后端，不可用时 fallback 到插件任务：点击、滚动、停留、评论、点赞、收藏、分享、关注、搜索，以及 B 站特有投币；click 在 capture 阶段记录，scroll 同时覆盖页面和内部 feed / modal 滚动容器
- 微博公开 discovery 由后端匿名 visitor 完成；插件只在显式 guided init 时申请微博 host permission，使用隔离同源任务页只读导入收藏、关注和 mentions。后端不接收 Cookie，不做普通行为采集、站内写回或 native-save；个人 bootstrap 当前为 init-only
- 记录行为发生时的**完整上下文**：对应的 DOM 页面快照、当前浏览路径、时间戳、平台来源与内容 ID；后端把来源平台、稳定内容 ID 和来源置信度写入 durable event ledger，旧事件无法确认时保留未知，不凭标题或任务名猜测
- 捕捉用户的**微行为**：鼠标悬停、视频进度条跳转、视频暂停 / 继续、页面导航等
- 采集用户亲手写的**评论 / 弹幕正文**（最强的兴趣表达之一）：X 回复正文与 B 站评论 / 弹幕正文均经 MAIN-world 网络 tap 在**提交成功后**采集（业务码校验），双端截断 200 字符 + 剥离控制字符后进入 `metadata.comment_text`（弹幕 `comment_kind="danmaku"`）
- **小红书赞 / 收藏强信号**由 MAIN-world `xhs-action-tap`（`obc-xhs-action`，与 token sniffer 隔离）在网络层认定：like/dislike/collect/uncollect 写端点业务成功才发，替代此前「按钮文案匹配、图标按钮漏采」的 DOM 路径；xhs adapter 声明 `tapAuthoritativeActions:{like,favorite,retraction}`，kernel 抑制对应 DOM 发射，事件 URL 与后端 note 键型互通以支持赞→撤销折价
- 记录用户的**主动反馈**：`dislike` 类动作统一规范成 `feedback` 事件，避免各平台负反馈语义分叉
- 插件 side panel 与桌面 / 移动 Web 使用同一 platform-neutral 保存契约：卡片先本地保存，保存页显式同步并轮询逐项任务；默认关闭自动同步，首次开启提示将修改对应平台账号；本地删除不删除平台记录
- 插件 side panel 与桌面 / 移动 Web 使用同一 30 天内容历史契约：recommendation-owned click、推荐展示和本地保存移除快照分别投影为「主动点开过 / 出现过但没点开 / 最近移除」，按 canonical identity 去重分页；保存移除项可恢复，封面不做整页预热，只按视口懒加载走现有图片代理缓存
- 本机调试可通过 `/api/extension/e2e/run` 驱动已安装插件在抖音 / 小红书 / X 真实页面执行白名单 DOM 操作，再由后端校验 `/api/events` 是否自然入库；runner 会把复用 tab 归位到平台入口并在回传结果前 flush 捕捉 buffer，该链路不伪造行为事件，用于验证捕捉层本身。`/api/events` 的每个 `event_id`、`/api/feedback` 与 `/api/recommendation-click` 的 `request_id` 都是 trim 后 1–400 字符的必填稳定键；缺失、空白或超长由请求模型在任何 event/projection 写入前返回 422。同一动作重试必须复用，服务端不补随机键。`/api/events` 在画像明确未初始化时会拒收普通行为事件，首轮画像信号只由 guided init 来源任务拉取；初始化后所有 accepted event 都先经统一 ingress commit，HTTP 只 wake、不等待 pipeline / LLM。app-owned `EventProcessingScheduler` 让 `profile_events` generic consumer 与 `content_feedback` consumer 按各自 durable cursor 扫描显式 owner，以 event row ID 生成稳定 signal，并用 `checkpointed_enqueue_batch()` 在同一个 `pipeline_state.json` snapshot 中原子发布 buffer+cursor，再由 owner 调 `tick_if_buffered()`；独立周期画像维护才调用完整 `tick()`。首次 app startup 只 await owner fence、本地 durable 准备与 scheduler admission，真正 scan/checkpoint/consume 在 scheduler-owned background task 中继续；provider 401、pending buffer LLM 或永不返回调用不能延迟 listener/health。shutdown 取消并 gather；热重载仍同步 pause/drain/recover/rebind，不缩短 owner pass 或破坏 cursor/buffer。5 秒 safety scan 继续覆盖丢 wake。retraction 投影在 generic cursor 前完成，hypothesis/import feedback 由其它 owner 处理或只越过 feedback cursor。两个 owner 首次接管都先按最大 event row id 发布 cutover fence，旧 direct-ingest 行不重学。`feedback_state.json` 只作迁移 provenance/兼容镜像，不是 owner 权威。`pending_signal_events` 仍只是 search / related_chain refresh 的触发水位，不是画像待处理数。`/api/feedback` 另明确采用 event-first 两次 commit：durable event 后才单独更新 recommendation projection，第二步失败由同 `request_id` duplicate retry 校验并补投影，不宣称跨表原子；相同 ID 的不同 payload 维持 409。

- 上述三个公开事件 ID 字段采用严格 JSON string 校验，不把数字、布尔或其它 JSON 类型自动转成
  字符串；这类非字符串输入与缺失、空白、超长输入一样在 route 前 422 且零写入。

**B 站数据接口**：
- 通过 B 站 API 获取结构化数据（历史记录、收藏夹、关注列表等）
- 作为浏览器插件采集的补充和验证

**Linux.do 只读扩展任务源**：

```mermaid
flowchart LR
    H[backend producer / init] --> Q[(linuxdo_tasks)]
    Q --> E[authenticated extension dispatcher]
    E --> T[isolated real linux.do tab]
    T --> G[same-origin GET only]
    G --> N[normalized topics / counts<br/>or structured error]
    N --> R[/api/sources/linuxdo/task-result]
    R --> P[events or pending-eval candidates]
```

- 上游网络请求必须全部使用 GET；不得发帖、点赞、收藏、关注、编辑或执行任何状态变更。
- 五个 discovery mode 固定为 `search / hot / feed / creator / related`；三个 bootstrap scope 固定为 `linuxdo_bookmarks / linuxdo_likes / linuxdo_read_history`，分别映射 `favorite / like / view`。
- 公开 discovery 的登录为 optional；个人 bootstrap 必须由 `GET /session/current.json` 正面确认 `current_user.username`。`_t` 只能作为“可能已登录”的布尔提示，其值不得上传。
- 扩展只回传白名单归一化字段与结构化错误；Cookie、CSRF 数据、原始 JSON/HTML、挑战页正文都不得离开浏览器。canonical 内容身份为 `content_id="topic:<topic_id>"`，`content_type="post"`。
- Linux.do executor 必须在扩展自己创建并持有的任务 tab 中隔离运行。生产 payload 的单请求超时默认且最多 30 秒；discovery 默认且最多 5 页，bootstrap 按每页 20 条和 limit 自动扩页（300 条为 15 页）且最多 15 页，输入列表最多 5 个、每分支最多 300 项、单响应最多 2 MiB。content executor 的 120 秒 / 50 页 / 20 输入 cap 只作第二层绝对防御，dispatcher 不允许合法后端任务触达。任务完成后只关闭自己的 tab。pending 领取等待约 3 分钟；`in_progress` 按任务形状最长约 29 分钟，再留 30 秒结果余量，后端 claim lease 约 35 分钟，共享 dispatcher mutex stale 窗口约 36 分钟。dispatcher 对已领取的非法 payload 必须立即回传失败；执行前必须把 task/tab/deadline 写入 session storage，MV3 重启必须先恢复 runner 再 polling，使存活 task tab 可继续回传而无需重跑站点请求。
- Linux.do bootstrap 的部分 scope 失败必须返回 `degraded`，保留已成功 scope 的事件与有界 `scope_errors`；discovery 分页或多输入中途失败也必须保留已得 topic 并返回 `degraded/input_errors`，让 producer 以部分完成入候选管线；零有效 item 的失败才返回 `failed`。bootstrap 的 `failed/degraded` 都不得进入默认 6 小时近期任务复用。
- guided init Stage-1 基础预算保持 30 分钟；使用默认预算时，Linux.do-only 至少给 32.5 分钟，Linux.do 与至少一个其它来源并选时给 62.5 分钟。显式 `collection_timeout_seconds` override 必须原样生效，不得静默扩容。
- `https://linux.do/*` host permission 只用于普通页面的统一行为 adapter，以及扩展创建的真实站点 task tab 中上述同源 GET；公开 discovery 不要求登录，个人 bootstrap 才复用登录会话。自动化任务协议、分页、cap、错误与隔离测试是合入门槛；真实已登录账号 E2E 当前尚未完成，发布前必须补做并记录结果。

#### 2.1.2 多层网状记忆架构 (Memory Architecture)

> 参考 MemGPT/Letta 的分层记忆设计和认知心理学模型，打造专为"理解一个人"设计的记忆系统。

**核心设计理念**：不是简单的数据存储，而是一个**活的、不断生长和自我修正的理解网络**。每一层之间有网状关联，上层理解会指导下层数据的解读，下层新数据会修正上层理解。

```
┌─────────────────────────────────────────────────────────────┐
│                   🌟 灵魂层 (Soul Layer)                     │
│  "他是一个什么样的人"                                         │
│  人格特质 · 核心价值观 · 深层需求 · 生活状态                     │
│  ↕ 双向修正                                                 │
├─────────────────────────────────────────────────────────────┤
│              💡 洞察层 (Insight Layer)                        │
│  "为什么他会这样"                                             │
│  动机分析 · 心理需求推断 · 潜在兴趣假设 · 行为模式归因            │
│  ↕ 双向修正                                                 │
├─────────────────────────────────────────────────────────────┤
│              📅 觉察层 (Awareness Layer)                      │
│  "每天他在发生什么变化"                                        │
│  每日观察笔记 · 兴趣趋势 · 情绪状态推测 · 阶段性总结             │
│  ↕ 双向修正                                                 │
├─────────────────────────────────────────────────────────────┤
│              📊 偏好层 (Preference Layer)                     │
│  "他喜欢什么/不喜欢什么"                                      │
│  兴趣标签(带权重+时间衰减) · 风格偏好 · 情境模式 · 探索倾向      │
│  ↕ 数据提取                                                 │
├─────────────────────────────────────────────────────────────┤
│              📝 事件层 (Event Layer)                          │
│  "他做了什么"                                                │
│  原始行为日志 · DOM快照 · 点击/搜索/收藏记录 · 评论/弹幕正文 · 反馈记录 │
└─────────────────────────────────────────────────────────────┘
```

**层间关系 — 网状而非单向**：

- **自底向上**：事件层的新数据不断注入偏好层，偏好层的变化推动觉察层更新观察笔记，觉察层的发现修正洞察层的推断，洞察层最终塑造灵魂层的人格理解
- **自顶向下**：灵魂层的人格理解指导洞察层如何解读新行为，洞察层告诉觉察层应该关注什么变化，偏好层根据上层理解来校准标签权重
- **跨层关联**：一个事件可能直接触发灵魂层的修正（重大行为变化），灵魂层可能直接影响事件层的采集策略（关注特定类型的行为）

**记忆类型**（参考 MemGPT/Letta 模式）：

| 记忆类型 | 作用 | 对应层 | 存储方式 |
|---------|------|--------|---------|
| **核心记忆** (Core Memory) | 始终在 Agent 上下文中的关键信息 | 灵魂层 + 偏好层摘要 | JSON 文件(可自编辑) |
| **情景记忆** (Episodic Memory) | 具体的交互片段和发现故事 | 事件层 + 觉察层 | SQLite + 向量索引 |
| **语义记忆** (Semantic Memory) | 用户相关的事实和知识 | 偏好层 + 洞察层 | 知识图谱/JSON（当前实现使用 JSON，未引入知识图谱） |
| **工作记忆** (Working Memory) | 当前会话的即时上下文 | 运行时 | 内存 |

**自我编辑能力**：Agent 可以自主决定什么信息该记住、什么该遗忘、什么该从事件层提升到灵魂层。

#### 2.1.3 画像输出

**自然语言人格描述**（像一个了解你好多年的老朋友）：

> *"小白骨子里是一个追求'深度理解世界运作方式'的人——无论是 AI 原理、历史脉络还是一道菜的风味逻辑，他都想搞明白'为什么'。他表面上是个技术宅，但我观察到他对摄影的兴趣在增长，我怀疑这是他在寻找一种技术之外的创造性表达。工作压力大的时候他会刷 RPG 游戏实况来获得掌控感，这时候千万别推他技术视频。他最近在看探店视频——不是因为想学做菜，而是享受'发现隐藏的好东西'那种感觉。这个特质很有意思，也许可以给他推荐一些小众但高质量的 UP 主。"*

**结构化数据**：JSON 格式的多层记忆数据，供系统内部使用。

#### 2.1.4 用户自述通道 — 苏格拉底式深度对话

不是简单地"记录用户说了什么"，而是**主动追问、假设、确认、调整**：

```
用户：我最近对美食不太感兴趣了
Agent：了解。不过我很好奇——你之前看探店视频是因为喜欢美食本身，
      还是享受"发现隐藏好东西"的过程？如果是后者，也许我可以
      帮你在其他领域找到类似的发现感？
用户：嗯你说得对，我确实更喜欢发现的过程
Agent：那我理解了。这是一个很有意思的特质——你可能也会喜欢"小众
      宝藏UP主挖掘"或者"冷门但高质量的纪录片"这种内容。
      我先假设你对这类内容有兴趣，推荐一些试试？如果不对我再调整。
```

核心策略：
- **追问 Why** — 不止记录偏好，挖掘背后动机
- **提出假设** — 基于理解主动猜测，而不是等用户说
- **确认验证** — 带着假设去推荐，看结果来验证
- **动态调整** — 根据验证结果修正理解模型

---

### 2.2 🔍 内容发现引擎 (Content Discovery Engine)

**目标**：像一个熟悉多个内容社区的专业编辑一样，通过多种方式主动发现好内容。

#### 2.2.1 不喜欢的即时推荐边界

普通 dislike 的语义是“不再推荐这张卡 / 已确认主题”，不是“禁止搜索这个词”。单卡反馈提交后同步把
recommendation 标为 processed 并失效推荐快照；主题 dislike 一旦写入 flat preference，
`SoulEngine.get_effective_disliked_topics()` 就把 flat preference、Soul 与用户 overrides 合成当前权威快照，
`get_profile()` 在完整 Soul 重建前也会带上它。

推荐历史缓存把该快照 digest 纳入命中条件；推荐首屏、换一批、追加、OpenClaw fallback 与主动通知在最终输出
边界再次按最新快照过滤。结构化 topic 精确命中始终排除；自然语言子串若误杀整个多卡窗口，只恢复没有精确
topic 命中的条目，单条 push 不恢复。异步 embedding + LLM 清池继续减少无效库存，但不再承担展示正确性。
Discovery 可以继续宽搜，普通 dislike 不撤销关键词或来源任务。

#### 发现策略

| 策略 | 说明 |
|------|------|
| **兴趣关键词搜索** | 根据用户画像生成关键词组合搜索；B 站生产路径在既有请求预算内预留 1 个 `pubdate` 请求（最多 5 条），与普通相关性结果交错进入评估窗口，只补近期供给而不改变 relevance/admission |
| **搜索灵感脑暴** | 可选地从 like 二级兴趣抽样；`OnionProfile.interest.likes` 会优先展开 specifics，一级 domain 只在缺少 specifics 时兜底，并按 parent 计数降权防止小窗口被同一领域占满；结合 recent interest selection count、关键词覆盖频次、raw candidate 数量 / 占比 / dominant content type 和最终候选池占比降权高频兴趣，coverage join 统一走 `_normalize_match_text()` 折叠大小写 / 空白漂移，画像整理会同步迁移 keyword 与 selection ledger 标签，完整 coverage 只在本地控制环使用，LLM payload 只携带 must-cover + 少量 cooldown 摘要；随后由 `discovery.keyword_brainstorm` 脑暴带 `kind_fit=regular|explore|both` 的搜索 probe branch，每兴趣最多 2 条，regular + explore 同轮触发时共用一次 brainstorm 和一次 grounding stage；按 `[discovery].inspiration_search_backends` 通过 search provider 链（默认已启用平台源 → Exa → You.com free MCP）grounding 具体实体 / 社区词 / 讨论点，stage 级搜索预算由 `inspiration_max_probe_searches_per_stage` 控制，平台源扇出由 `inspiration_platforms_per_probe` 控制，每 probe 翻页 / 扩量由 `inspiration_search_pages_per_probe` 控制，B 站 / 抖音 / X 等 risk-controlled 来源受 `inspiration_riskcontrolled_probe_budget` 与 cooldown / 限流约束；`platform_sources` 只把 B站 / YouTube / X / Reddit / Bangumi、抖音 direct client，以及小红书 / 知乎 bridge 可用时的搜索标题 / URL / 摘要作为灵感 evidence，不入候选池；泛词不是硬错误，会交给 curator 结合画像、平台 guide 和覆盖约束判断；再经 `discovery.keyword_inspiration` 做 Profile Curator / Detail Expander，优先生成按平台 keyed 的 `platform_keywords`；`platform_guides.query_style` 明确 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi 的平台检索语法；写库前由系统侧执行 must-cover 排序、每平台二级兴趣 / lens family 上限、原样证据标题 / URL / 过长 query / 平台语言不匹配 / 平台检索语法不匹配过滤、grounding hint `source_interest` 校正、explore 横向 lens 校验，缺失 must-cover 兴趣时用 `discovery.keyword_inspiration.repair` 做一次 bounded repair，repair 仍缺词时用 deterministic platform-native backfill 补齐；新配置默认以混合模式开启，与旧 merged keyword planner 并行，admission yield 会回填 inspiration / expansion 反馈计数；实验开关可让 due 平台完全跳过旧 merged keyword planner，只用新流程产词，并在 B 站 explore 到期时写入 `keyword_kind="explore"` 的探索词池；`keyword-inspiration-dry-run` 可真实预览中间链路但不写关键词池，且使用独立 preview selection scope，`keyword-inspiration-report` 对比 inspiration / merged cohort、输出 production / preview 抽中分布并给出 replace 门禁 |
| **相关推荐链探索** | 从已知好内容出发，沿相关推荐不断深入 |
| **分区热门/排行榜** | 固定全站榜，并按本地洗牌轮转覆盖非 0 分区榜，结合用户画像筛选 |
| **UP 主追踪** | 追踪关注的和发现的优质 UP 主的新动态 |
| **评论区挖掘** | 从评论区发现用户推荐的其他内容/UP 主 |
| **跨领域探索** | 刻意推荐用户从未接触过但心理画像暗示可能喜欢的领域；当统一 `KeywordPlanner` 已有 merged keyword 调用、`explore_refresh_hours` 到期或即将到期且 B 站仍有补货空间时，默认会把 `explore_domains` 合并进同一次关键词生成，把探索 query 写入 B 站 `keyword_kind="explore"` query cache。开启 inspiration-only 替换模式后，这部分也改由 search-backed inspiration flow 生成 `query_kind="explore"` 的 B 站探索词。`ExploreStrategy` 后续从该 explore 候选池 claim query 搜索；池为空时不再单独打一次 explore 计划 LLM |
| **热点关联** | 追踪热点话题，判断是否与用户深层兴趣相关 |

Linux.do 同样纳入统一关键词 planner 的九平台目标与 `platform_guides.query_style`；其 search query 使用社区话题风格，候选仍只进入统一待评估池。Linux.do 不是 inspiration grounding 的后端直连来源：真实取数依旧由扩展 task tab 完成。

#### 内容评估

> 评估的核心依据是**用户的 Soul（灵魂画像）和深层兴趣**，而非通用指标。

- **核心评估**：这个内容是否匹配这个用户的深层兴趣和当前状态？
- **时效性基准与三态推荐资格**：来源 `published_at` 与本轮精确 UTC `evaluated_at` 一起进入单条、批量及推荐池补分类 prompt；模型基于 prompt 可见正文原子输出 `temporal_class/confidence/reason`，以及 `validity_mode`（`none/explicit_deadline/event_state/version_state/freshness_only`）、`valid_until`、`scope`（`none/core/hook`）、逐字 `evidence` 和 `state`（`unknown/active/expired/superseded`）。代码侧统一生成 `temporal_evaluated_at/temporal_next_review_at/temporal_policy_version/temporal_evidence_complete`；`relevance_score` 与新旧完全解耦。确定性 policy 返回 `eligible/review_due/expired`：只有置信度 `>=0.80`、完整且作用于 `core` 的证据组经过逐字 grounding 后，已过 `explicit_deadline` 或结构化事件 `expired` / 版本 `superseded` 才 hard expire。deadline 必须从 Agent 实际可见文本中逐字取得日期、具体时刻和时区，并与规范化 `valid_until` 表示同一瞬间；active / 终态必须分别有正向、无条件的当前状态 / 结束替代语义，条件、假设、可能或未来态句子不能作为 state 证据。日期-only、反向证据、`hook`、低置信、缺字段、未 grounding、不一致状态和无效时间全部 fail-neutral。`breaking/current/versioned` 的 1 / 14 / 60 天是复审频率，不是死亡线；旧 v1 `breaking/current` 的 3 / 60 天窗口也仅生成 `review_due`，`versioned` v1 行另有 120 天准入 TTL。评估缓存绑定 prompt-visible 内容、发布时间摘要与独立小时桶，并由 v6 namespace 隔离旧契约。
- **近期供给、排序与生命周期复核**：B 站 API 主搜索与扩展 fallback 都只提供一个小型 recent lane；lane provenance 贯穿 `DiscoveredContent → discovery_candidates`，但不改变来源策略、相关性阈值或配额。合格候选间继续使用有界 publication bonus；推荐侧对每个候选窗口聚合比较“含 bonus”与“无 bonus”Top10/50/100，按 class/source/age 记录进入退出，shadow 不含候选身份/文本，写失败 fail-open，且本身不调整三态 policy。`review_due` 的 discovery candidate 回到待评估状态，正式池条目进入可逆 `temporal_review_hold`；两边都按逐行 1 / 2 / 4 / 8 / 16 / 24 小时 not-before 租约退避，候选租约未到时不可 claim、也不计 raw / projected / 来源容量。hold 不展示、不计库存，由现有 evaluator 复审后可恢复 `fresh`。评估落库后 admission 重读 durable row，仅最终状态仍为 `evaluated` 才能入池；`expired` 则转 `rejected_temporal_stale` / `stale`。`PoolServeSnapshot`、最终 recommendation + shown 写事务以及 API 1 秒快照都会使用同一证据组复核，避免连续刷新或 snapshot 竞态绕过 hold/expiry。
- **可选辅助指标**：播放量/点赞/弹幕质量等——由用户画像决定是否参考（有些用户在意质量指标，有些人不在意）
- **统一待评估池与准入**：API daemon 的不同来源 raw candidates 进入 `discovery_candidates` 后，由唯一 `CandidateEvalCoordinator` tokenized claim；默认 3 个 30 条 LLM worker 并行，任一完成即补位，SQLite 完成提交与 admission 串行。pipeline 单次 enqueue callback 立即唤醒这个 owner，refresh / managed producer 不再同步 drain。raw 清空且 projected 仍低于目标时，coordinator 调用 quota-aware supply wave，即时 tick 所有欠份额 producer 并执行 B 站 refresh；同平台周期 / 即时 tick 由 per-source lock 去重。补池生产性以真实 `inserted/enqueued` 为准，全部 duplicate 即使跑过策略也进入 30/60/120/300/600 秒退避，真实入队立即清零。串行 lane 先原子持久化全部 token-owned 相关性与 temporal v2 证据组，再按 `target - available - admitted_pending_available` 执行相关性门和三态 eligibility；`review_due` 重新排队复审，`expired` 终态拒绝，只有 `eligible` 可 admission。raw 重抓、旧缓存或不完整评估不能局部覆盖证据组，也不能复活 hold/stale 行。`admitted_pending_available` 只统计补齐表达后能进入当前 topic 三条展示窗口的 pending-copy，超过 headroom 的达标结果保留为 `evaluated`。评估输入包含正文 / 标签 / 互动指标；`[discovery].eval_prefilter_mode` 默认 shadow 只记录 would-filter，enforce 才会让明显低相似且非 explore 的候选本地低分缓存并跳过 LLM；多模态评估开启且模型支持图像时会复用运行时图片缓存。OpenClaw direct one-shot 不启动 daemon owner，`recommend(refresh_if_needed=True)` 的首轮 source supply / inline claim 固定 ≤4，并在 durable admission 后同步 drain ≤4 条 expression copy。调度 projected 固定为 `available + admitted_pending_available + evaluated_pending_admission`，普通 raw 与同 topic 深层 pending-copy 不计入；表达协调器以 `max(copy-ready 缺口, min(available 缺口, admitted_pending_available))` 接手已入池 eligible 素材。来源只影响取数方式、配额和 prompt 上下文，平台节流、raw ceiling 与相关性阈值不变。
- **来源定向回填**：主策略不足时，历史 `content_cache` backfill 先按本轮 strategy 的 `source_platform` 在 SQL 中过滤，再做平衡与 `LIMIT`；空 legacy 平台只归 B 站。一次 B 站 / YouTube / 抖音定向运行不能被其它平台的高分历史行补满。

---

### 2.3 📬 推荐与呈现 (Recommendation & Delivery)

**目标**：像一个真正了解你的朋友，在合适的时候以真诚的方式推荐内容。

#### 推荐类型

- **即时推荐**：发现特别匹配的内容时即时推送
- **每日精选**：定时推荐列表
- **个人专题**：深度个性化的主题推荐——完全基于对这个人的理解，不是通用分类

> 专题示例（不是"周末放松包"这种通用的，而是只属于这个人的）：
> - *"你最近在探索摄影——这几个视频从你习惯的'搞明白原理'的角度讲构图和光影，我觉得很对你的胃口"*
> - *"最近工作是不是有点累？这两个 RPG 实况节奏特别好，适合你晚上用来切换状态"*
> - *"我发现一个 UP 主讲历史的方式跟你喜欢的那种'深层逻辑分析'风格很像，但他讲的是经济史，你说不定会打开一个新世界"*

#### 推荐表达（有温度、有洞察的朋友式推荐）

不是：*"因为你观看了相关视频，推荐以下内容"* ❌

而是：*"我觉得你会喜欢这个——这个 UP 主讲 AI 的角度很独特，有点像你喜欢的那种'把复杂的事情讲透'的风格，但他会加入很多生活化的类比。我理解你最近对 AI 的关注不仅是工作需要，更多是一种对未来的好奇，这个视频正好聊到了你可能感兴趣的方向。"* ✅

核心要素：
- **"我觉得"** — 有主观判断，像朋友一样
- **"我理解你"** — 展示对用户的深层理解
- **关联洞察** — 不只是"你看过类似的"，而是"我理解你为什么喜欢"
- **个性化** — 每一条推荐都只属于这个用户

---

### 2.4 🔄 反馈学习系统 (Feedback Loop)

- **隐式反馈**（浏览器插件自动采集）：是否点击、观看时长、是否收藏分享
- **显式反馈**：在插件中点赞/踩、对话式反馈
- **桌面端提交屏障**：普通推荐与正向/避雷探针的非聊天动作先即时更新 UI，10 秒内可真实撤销且不写后端；超时或页面离开才提交，失败恢复原状态。评论/聊天因依赖文本语义与服务端回复保持直接提交
- **记忆迭代**：反馈触发多层记忆网络更新——事件层记录事实，偏好层调整权重，觉察层写观察笔记，洞察层修正假设，灵魂层在必要时更新人格理解
- **策略自省**：Agent 自我评估推荐命中率，反思发现策略和理解模型的有效性

---

### 2.5 🔧 Skill 系统 (Extensible Skills)

**目标**：支持自定义扩展能力，让用户和社区可以为 Agent 增加新技能。

- **Skill 定义**：每个 Skill 是一个独立模块，包含说明文档 + 执行逻辑
- **内置 Skill**：B 站 / 知乎等来源搜索、内容浏览、评论区分析、作者追踪等
- **自定义 Skill**：用户可以创建新 Skill 扩展 Agent 的能力
  - 例如：新平台接入、特定领域的内容评估策略、新的推荐呈现方式
- **Skill 注册**：Agent 自动发现可用 Skill，根据任务需要选择调用

#### 2.5.1 Agent Bridge 能力协商与兼容边界

OpenClaw、Hermes、WorkBuddy 等宿主通过同一份 `agent-bridge/v2` 能力清单发现
OpenBiliClaw 的当前功能；`integrations/openclaw/skill.py` 是 descriptor 的唯一注册表，
`integrations/agent.py` 提供协议中立的 Python 别名，历史 `integrations.openclaw` 路径和
`openbiliclaw.integrations.openclaw.cli` 命令继续兼容。宿主启动或升级后先调用
`capabilities`，不得根据旧版 skill 名称猜测能力。

当前桥接边界覆盖多源推荐与分页消费、活动流和平台可用性、兴趣 / 避雷四态探针、惊喜反馈、
带 `turn_id` 的 durable 对话历史、画像编辑，以及 local-first 保存列表。所有写入动作都返回
稳定 request / turn 标识；外部账号 native-save 只能通过显式授权的 `sync_saved` 触发，不能把
本地 membership 当成平台同步成功。扩展 mutation 未观察到即时选中态时，只允许同一任务在终止
旧 document sender 后重载 exact URL；新 document generation 完成 READY 握手后执行只读 persisted-target
核对，且只有正面状态证据可升级成功。知乎目标是全局 `知乎收藏` 开关而非命名收藏夹，复核绝不点击“已收藏”。task-result 必须得到 2xx，同一 canonical terminal
payload 可幂等重放，变化后的晚回调不可覆盖首写。新增核心功能时必须同步更新 operation DTO / handler、skill
descriptor、CLI（适用时）、capability manifest、幂等测试和集成文档，保持宿主不会悄悄落后于内核。

---

### 2.6 📦 本机数据迁移

**目标**：让单用户 Agent 的可移植状态能安全、可理解地从一台机器搬到另一台机器，而不复制目标机器的网络身份或在运行中替换数据库。

- **用户入口**：桌面 Web「设置 → 通用 → 数据迁移」提供导出、导入、状态查询和取消 pending；四条 API 仅接受后端真实观察到的本机 loopback，请求来自浏览器时还必须同源，扩展、LAN 与远端调用不在授权范围。
- **包格式**：`.obcbackup` 是带版本、成员大小和 SHA-256 清单的标准 ZIP，明确为**未加密敏感文件**。它包含模型 / 来源凭据、SQLite、画像 / 记忆、平台 Cookie、图片缓存和少量安全 UI 偏好，但两层磁盘配置合并后会移除整段 `[api.auth]`，密码 / hash、session secret 和扩展设备 key 不进入包。配置快照来自磁盘两层，数据快照则固定读取本进程已持锁的 active data dir，不提前使用尚待重启的新目录。
- **可移植边界**：日志、旧备份、embedding / 评测 / 临时缓存、证书、自启动文件、OpenBiliClaw Web / 扩展访问会话、外部 CLI 凭据与环境变量值不导出；平台登录 Cookie 是明确包含的敏感状态。`source_omitted_environment_variables` 提示源机遗漏的变量名，`target_active_environment_variables` 单独提示目标进程仍会覆盖导入配置的变量名。
- **导入边界**：上传只做有界 ZIP 校验、checksum、配置构建与 SQLite integrity check，并发布私有 pending stage；active runtime 与 UI 偏好都不变化。导入携带 / 生成 UUID `request_id`；上传 / 校验期间 status 返回匹配 ID 的 `processing` 与 `uploading|validating` phase。连接结果不确定时，桌面端最多强制查询 3 次，对 `idle/cancelled` 间隔 500ms 再确认，不能以一次瞬时 `idle` 终结对账；每次打开「通用」也强制查询。重启前可取消 pending。下一次后端启动同时持有项目与 canonical data-dir 互斥锁后，通过 journaled replace 原子应用配置 / 数据；桌面端只在 status 已为 `applied` 后按 `migration_id` 为每个浏览器应用一次白名单偏好，用户后续修改不再被旧 status 覆盖。
- **故障与身份**：替换失败恢复原配置 / 数据；成功后原文件保留为 `pre-import` 回滚副本。目标机器的数据 / 数据库 / 日志路径、API host / port、网络 / TLS / 自启动和 CDP 设置继续生效；Bilibili 专用代理与本机浏览器可执行文件路径也保留目标值，目标证书 / 自启动文件保留。整段 `api.auth` 采用目标机现值，再轮换文件 session secret、把 auth epoch 严格提升为来源 / 目标当前值最大值加一以强制撤销旧会话，并清空 / 关闭扩展远程配对。

---

## 3. 系统架构

```text
interactive (dialogue / config probe) ──────────────┐
                                                    ├─ runtime total gate (default 4) ─ ordered instance chain ─ adapter
background ─ background admission (default 3) ──────┘
             ├─ refill: expression > evaluation > supply
             │  ├─ supply includes explore queries / source extraction while low
             │  └─ while queued: guarantee 2, may borrow all 3
             │     expression owner: 8 immediate / 3s fixed tail / 60 drain / 30×2 provider
             └─ maintenance: at most 1 while refill waits;
                parked when canonical available = 0

guided init: signals → preferences → full profile commit
                                  → discovery → evaluation → copy → canonical pool ready
                                  → terminal → runtime schedules optional probes

Agent hosts (OpenClaw / Hermes / WorkBuddy)
        → capabilities(agent-bridge/v2) + JSON CLI / skill descriptors
        → integrations.agent alias / integrations.openclaw compatibility adapter
        → runtime / soul / recommendation / saved_sync owners

config recovery control plane (normal or degraded; business APIs stay gated)
                ├─ draft → /api/config/probe-service → temporary registry → total gate
                └─ draft → /api/config/discover-models → exact instance GET /models
                          → editable model list + local effort advisory (no config write)
config save control plane: persist first → HTTP 202 queued/apply_revision → latest-wins queue → runtime receipt/status
                           └─ data_dir changed → restart_required; active locked dir stays until full restart
publication-date preference: [bilibili] config → RuntimeContext → effective inventory → PoolCurator → serving score/gate
                             ├─ Bilibili only; out-of-range rows stay in the pool; weight 0.5 default, 1 strict
                             └─ strict only → Bilibili API / extension search pubtime_begin + pubtime_end
migration control plane: local export → checksummed plaintext .obcbackup
                      → local import + request_id validates/stages ↔ status/cancel
                      → restart + runtime lock → journaled config/data replace → applied | rollback
XHS hidden search tab → MAIN search-response normalizer → isolated replay/DOM fallback → task final
XHS/DY/YT/Zhihu/Reddit/Linux.do task final: canonical staged result (XHS bootstrap payload caps enforced)
                                          → durable event receipt → atomic bounded seen-key → terminal flip
XHS/DY/YT/Zhihu/Reddit/V2EX task final: canonical staged result (source caps/fields enforced)
                                 → durable event receipt → atomic bounded seen-key → terminal flip
                                 stale lease reclaim replays first write; staged row rejects late mutation
V2EX identity ladder: PAT verified > browser observed > config/accepted
                    → mismatch pauses account projection only
                    → resolved identity-scoped seen/affinity + complete favorite 2-miss outbox
extension-online periodic re-pull: explicit opt-in → presence + profile/init/config gates → persisted round-robin
                                 → one active bootstrap across six task tables → EventHub → extension
Douyin source supply: daemon presence gate (explicit manual call bypasses it)
                     → one shared plugin-cycle wait budget → terminal dy_task → pending_eval
                     absent → zero enqueue; timeout/error/budget → bounded retry floor

cover images: proxy foreground ─┐
              refresh prefetch ─┴→ app-stable coordinator(total 4 / bg 3, fg priority)
                                  → cache-key singleflight → whitelist fetch (sinaimg included, direct)
                                  → atomic cache
dialogue entries → app-stable execution lease(max active 1; reload pause/drain)
  durable dialogue → confirmation entry(pending list / cards)
                 → chat_turn(reply_to_turn_id + payload + fixed turn time)
                 → server-frozen DialogueTurnBinding → pending SQLite
                 → rowid-ordered durable reply worker → SocraticDialogue(queued)
                 → visible completion CAS
                   transient/cancel → pending + bounded in-place retry; explicit invalid → failed CAS
  direct chat/probes → same lease through response + ctx-dependent side effects
post-reply learning/object settlement (independent of durable reply backlog)
                 → typed settlement queue[all 11 declared kinds] → one actual worker + guard
                 → pending≤3 → user open(no cooldown) | system 12h+object 72h
                   → confirmation INSERT → attached user INSERT (created_at,rowid)
                 → one context digest → prompt/history/event/learn/settlement provenance
                 → anchor snapshot(kind + ref + generation) → existing insight extraction
                 → kind×relation matrix ┐
                 → hypothesis card action ┴→ frozen snapshot → worker-only apply
                   action≤1s: completed → 200 | blocked → 202 processing
                              └→ popup/mobile/desktop GET poll 1/2/5s, deadline 30s
                   pending open busy → 503 dialogue_busy/Retry-After → UI auto-retry ≤25m
                   active clarifying → only current holder; hide in sessions already showing it
                   confusion object failure → replay_queue(max 5, head-fenced) → 12h recovery
                   → lightweight ref winner receipt
                   → event → object → derived → rebuild-marker → applied
                   → publication-only retry → cross-session projection → exact-generation release
                   → stable-key audit observer (failure does not block applied)

degraded registry → provider-free ping(degraded) → static /web | /setup | /m
                  ├─ GET/PUT config → restart runtime
                  └─ skip hydration; recommendation / discovery / profile APIs stay 503

reshuffle HTTP → temporal review-hold / expiry retirement → PoolServeSnapshot → isolated serve DB worker/read transaction
               → unchanged MMR → final temporal recheck + short atomic recommendation+shown write
               → current-card exclusions + durable seen_items are mandatory guards
               → non-empty success records one neutral reshuffle event, never N dismisses
  PC Web platform tab → optional source_platform (additive, canonical)
                      → platform-scoped candidates, no cross-platform floor
                      → same curator / MMR / diversity / persist path
  platform-availability → isolated read of the canonical available set
                        → {total_available, by_platform}; total == sum(by_platform)
pool maintenance → isolated maintenance DB worker → ≤50 mutations/transaction
                 → commit/release lock → unchanged skip / 10m safety sweep
```

对话回复与其后的 11-kind learning/settlement 是相邻但独立的 lane：Web/API durable runtime
先由 app-owned 单 worker 按 `chat_turns.rowid` 领取 pending，再在稳定
execution lease 内使用 `SocraticDialogue(queued)`；成功写入 user+agent 历史并用
`WHERE status='pending'` 发布一次 completion CAS 后，同步
提交 typed `learn`，由唯一 `DialogueSettlementQueue` worker 在线内 await
`learn_from_dialogue`；CLI/OpenClaw 只在两个兼容构造点使用 `legacy_direct`，保持
既有 detached direct learning，位于 queue/guard 外。其余 10 个 typed kind 的
卡片四动作、锚建立/释放/恢复、普通 chat settles、探针/疑惑 reply/open/replay、
GET reconcile 与 legacy façade 也已全部接入同一个 production dispatcher/worker；
protected mutation 只允许 actual worker Task；嵌套 settle 沿该 task 的调用栈直调
`_apply_*`，不 submit、不 inline dispatcher，也不存在 child 临时授权。继承 context
的 active/detached child 对 mutation 与递归 admission 均 fail closed。
队列 job 不持久化：action 本地等待 1 秒后按需返回 202，popup/移动/桌面在 30 秒内读取
durable turn，重启丢 job 时允许同 action 重新提交；不增加 job table 或恢复 scanner。
pending-open 是更严格的 required local transaction：长 LLM job 占住 worker 时不先
admission，而返回 `dialogue_busy` 让 popup/移动/桌面带等待态自动重试；热重载保持 admission
直到队列 idle，再原子 pause/revoke，25 分钟安全窗覆盖 20 分钟 provider timeout。
两条学习路径都使用 task-local bypass 跳过 background admission、保留 total gate，
避免空库存反向阻塞纠偏。若学习真正新增长期避雷项，则在偏好落盘后立即复用共享
dislike writeback，精确清池与后续语义精判不等待完整画像重建。provider、限流、配置、
失败/超时与取消都会回滚临时用户历史并保持 durable `pending`，在队头原位有界退避；
只有显式空/无效响应才持久化安全错因与 `failed / reply=""`。桌面 Web 的推荐、
runtime 与次级 hydration 是独立分支；已有卡片的后台恢复跳过可能补池的推荐 GET，
只同步 runtime / 库存状态，空列表或明确手动刷新才读取推荐快照。

```
LAN clients ─ HTTP（默认）────────────→ IPv4 0.0.0.0 + IPv6 [::] listeners → one uvicorn / FastAPI app
public clients ─ HTTPS（可选）→ Caddy :443 ─ shared-loopback HTTP ─────────────────────────────┤
trusted LAN ─ HTTPS（可选）──→ TLS Proxy :8443 ─ loopback/Compose HTTP ───────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                  用户交互层 (浏览器插件)                        │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐    │
│  │ 统一行为采集   │  │ 推荐展示 UI   │  │ 对话/确认入口    │    │
│  │ Adapter: B/XHS│  │ (LUI 界面)   │  │ (durable turn) │    │
│  │ +DY/YT/X/ZH   │  │ +真实可换数   │  │                │    │
│  │ +停留满意度   │  │ +文字卡渲染   │  │ 待聊列表/卡片   │    │
│  └──────────────┘  └──────────────┘  └─────────────────┘    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ bili/xhs/dy/yt/zhihu/reddit/linuxdo/v2ex/weibo 任务调度 + 源开关/比例配置（后台 tab / 初始化导入 / 配比建议）│ │
│  │ 微博任务仅在显式 guided init 运行：同源只读导入收藏、关注、mentions；不上传 Cookie、不采集普通行为 │ │
│  │ XHS 自动任务：source/scheduler 领取门 → SQLite 节流/风控冷却 → 关闭/限流时不再开任务 tab │ │
│  │ XHS search：inactive tab → MAIN 搜索响应归一化 → isolated replay / DOM 兜底          │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ runtime-stream 20s idle 心跳 + B站/抖音/X Cookie 请求与扩展回传│   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 扩展捕捉 E2E：run -> runtime-stream -> 入口归位 -> DOM 操作 -> /api/events │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │普通 events/推荐点击 → generic durable cursor ─┐      │   │
│  │内容 feedback → content_feedback cursor ───────┴→ atomic buffer+cursor checkpoint│ │
│  │首启 fence+task admission→listener；后台 owner→tick_if_buffered│ │
│  │热重载 pause/drain/recover→rebind；周期画像维护→tick       │   │
│  │对话 → typed settlement worker → learning；旧反馈批仅 false 回退│ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ delight / interest.probe / avoidance.probe 主动推送（含probe_mode）│ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 后台 LLM 请求暂停配置（设置页调度区 + presence gate）          │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 开机自启动开关：/api/autostart-status + apply（本机可写）     │   │
│  │ CLI / 冻结桌面入口 -> runtime.autostart.reconcile -> OS 登录项│   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 配置离线缓存 + 降级模式静态恢复 UI（/web /setup /m，保存后原地恢复）│   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 手机版二维码：桌面/插件 -> 同 scheme /api/qr-info -> /m      │   │
│  │ 跳过 /api/health readiness / embedding probe                 │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │封面：proxy前台 + refresh预取 → app-stable 4/3优先lane → singleflight│ │
│  │     → 白名单 CDN → tmp+fsync+replace cache → UI              │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 海外网络：config/UI -> direct|system|custom -> LLM/YT/updater/GitHub stats │   │
│  │ 国内客户端保持独立直连；微博 httpx trust_env=false，不消费海外路由策略 │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ API Auth Gateway（可选）：/api/* 密码门禁中间件             │   │
│  │   本机/扩展免登录 · LAN/远程需密码 · auth_epoch 撤销         │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 推荐点击：content_id/url/source_platform -> source-aware click signal │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │30天历史：click events + recommendations + saved_item_removals│ │
│  │ -> /api/content-history 三分类分页 -> 插件/移动/桌面 lazy 封面 │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ durable chat：session=popup -> 插件/移动/桌面；主历史含 probe 聊天 │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 推荐/探针反馈：即时 UI -> 10s 可撤销 -> event commit/HTTP 200 -> 5s owner │ │
│  │ 对话/反馈新增长期避雷 -> shared dislike purge -> purged_by_dislike │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ runtime status：available/raw/pending 库存 -> 插件/移动/桌面 │   │
│  │ 补池：available-by-source deficit + raw-material headroom     │   │
│  │ 推荐消费池后：ServeResult 扣减快照 -> 精确异步复读 -> 三端收敛 │   │
│  │ 桌面已有卡片后台恢复：跳过可能补池的推荐 GET，只同步库存状态 │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 画像编辑：编辑面板 -> /api/profile/edit -> 覆盖层（插件/移动/桌面三端） │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 引导初始化：来源 + 前置清单 -> /api/init；微博以登录态 heartbeat + uid gate 后导入个人事件 │ │
│  │ 完整画像提交 -> 发现/评估/表达 -> canonical ready              │ │
│  └──────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────┤
│                      Agent 核心层                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │           Agent Orchestrator (自研)                   │   │
│  │   (任务调度 / 策略决策 / 多步推理 / 自省 / Skill 调度)    │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────┐      │
│  │ User Soul    │ │ Content      │ │ Recommendation │      │
│  │ Engine       │ │ Discovery    │ │ Engine         │      │
│  │ (词表画像+探针)│ │ (发现+待评估池)│ │ (排序+表达)     │      │
│  └──────────────┘ └──────────────┘ └────────────────┘      │
│  ┌──────────────────────────────────────────────────────┐   │
│  │     PoolCurator + 双轴 fatigue + per-group 窗口 + 新兴趣放大保护 │ │
│  │     request_replenishment + 定时/手动补货 + B/XHS/DY/YT/X/Zhihu/Reddit/Linux.do/Bangumi=5/1/1/1/1/1/1/1/1 │ │
│  │     request_replenishment + 定时/手动补货 + B/XHS/DY/YT/X/Zhihu/Reddit/Bangumi/V2EX=5/1/1/1/1/1/1/1/1 │ │
│  │     raw断供 → 欠份额 producer 即时并行唤醒 → 真实新增计数 / 无产出阶梯退避 │ │
│  │ API CandidateEvalCoordinator: available + eligible copy-pending + evaluated -> 3×30 -> serial admit │ │
│  │ evaluator: time-neutral relevance + atomic grounded temporal evidence -> tri-state eligibility │ │
│  │ temporal policy: 1/14/60d review clock; versioned 120d TTL; deadline/terminal evidence expires; gaps fail-neutral │ │
│  │ OpenClaw refresh: first source/eval <=4 -> copy <=4/no split retry -> canonical subset; both hosts recover first │ │
│  │ delight: copy/topic ready + seen_items guard -> score/snapshot -> UI × writes seen ledger │ │
│  │ reshuffle: current IDs + seen_items -> retire/snapshot/MMR -> final recheck+atomic persist │ │
│  │ maintenance worker: isolated connection -> <=50 mutations/batch -> commit/yield │ │
│  │     内容元数据：时长/互动/发布时间 -> candidates -> content_cache -> API -> 四端 │ │
│  │     Query inspiration cache: search preview -> inspiration/expansion -> keyword provenance │ │
│  │     InspirationKeywordPipeline: axis library learning loop (yield backfill/lifecycle) + breadth config │ │
│  │     LLM gate: scheduler + extension presence          │   │
│  │     Soul taxonomy: CATEGORY_VOCAB + category migration + homonym-aware consolidation │ │
│  │     Cognitive profile pipeline: 单对话锚(ref+generation) + 归属矩阵 + 台账 │ │
│  │       + 待聊≤3/主动零冷却/系统12h+对象72h/attached_to 去重             │ │
│  │       + frozen admission / worker-only apply / 轻量 ref winner / applied 投影 │ │
│  │       + confusions FIFO(≤5/队头 fencing/12h 补扫) + 冻结/held 重放 + 深层门控 │ │
│  │       (off/shadow 默认/enforce · 两接入点: 深层对话候选/soul 重建; 管线 VALUES·CORE 已封死) │ │
│  │     Autostart: user login item + Ollama preflight/self-heal + Ollama.app runtime 校验 │ │
│  │     Bili DOM fallback + XHS/Douyin/YouTube/X/Zhihu/Reddit/Linux.do/Bangumi producers: 按平台缺口独立补池 │ │
│  │     Bili DOM fallback + XHS/Douyin/YouTube/X/Zhihu/Reddit/Bangumi/V2EX producers: 按平台缺口独立补池 │ │
│  │     CLI discover --source douyin -> 同一正式 producer -> 统一关键词终态 -> pending eval │ │
│  │     Hot reload one-shots: interest/avoidance force_tick │   │
│  │     Probe arbiter: interest / avoidance 每轮最多推送一条   │   │
│  │     Interest probes: near 5 + challenge 3 独立 active 额度 │   │
│  │     Probe memory: domain / axis / distance + exploration buffer │ │
│  │     AccountSync: B站+X 账号增量 -> 48h 跨源去重 -> Pipeline │   │
│  │     Guided init: stage 3 full-profile commit barrier -> stage 4 discover/evaluate/copy/canonical verify │ │
│  │     Pool readiness: servable/raw/pending 统一库存口径       │   │
│  │     Atomic maintenance: canonical protected -> topic/source/raw -> invariant/rollback │ │
│  │     Source bootstrap seen-key guard -> Memory/Profile      │   │
│  │     Extension-online re-pull -> six bootstrap tables (global serial) -> installed extension │ │
│  │       -> staged durable ingress -> atomic seen keys (5000/source) -> terminal │ │
│  │     Profile overrides overlay: 用户编辑 -> profile_overrides.json │ │
│  │       -> get_profile()/sync_profile_files 读时叠加（抗画像重建）│ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ /api/saved/* -> membership 先提交 -> native_save_tasks/items 快照 -> router │
│  │ -> BilibiliNativeSaveAdapter（收藏夹/稍后再看）-> durable task-item poll │
│  │ 六平台 adapter -> ExtensionNativeSaveBroker -> extension_native_save_jobs -> native_save multiplex │
│  │ extension_native_save_jobs -> /api/sources/<slug>/next-task -> installed extension                │
│  │ exact OpenBiliClaw / YouTube Watch Later targets -> authenticated safe task-result                 │
│  │ trusted-local extension E2E exact auth -> single saved sync item -> six-field safe callback        │
│  │ -> /api/sources/{xhs,dy,yt,x,zhihu,reddit}；unsupported_adapter_missing 可重试 │
│  │ 微博 membership 仅本地：无 native adapter / 站内写回；个人事件使用独立同源只读任务 │
│  │ -> 插件/桌面/移动 saved UI；CLI config-show（自动同步默认关闭）    │
│  │ NATIVE_SAVE_EXECUTE/RESULT：tab-launch mutex（XHS exact manual 可越过）+ per-task deadline + bounded replay │
│  │ shared MV3 recovery barrier 在领取任务前清理全部 runner-owned orphan tabs       │
│  │ task tab 从 about:blank 起登记 owner，首次加载/重载均停普通 collector；删除失败保留 recovery owner │
│  │ 未观察到确认：abort+await mutation sender → fresh document READY/不同 ID → read-only verifier │
│  │ verifier 仅 exact execution 的 already_synced 可升级；exact terminal callback replay 幂等 ACK │
│  │ final/source URL 与 tab/task/item 严格关联；Reddit/X/YT/XHS/DY/Zhihu 6/6 已接 │
│  │ （fixture 全覆盖；2026-07-14 六平台 favorite + watch-later/fallback 真实终态均成功）│
│  │ Zhihu typed ID -> exact identity control/dialog -> OpenBiliClaw checked proof │
│  │ YT favorite 精确 OpenBiliClaw；重复 exact 行优先 checked/稳定复用；Watch Later 只认 WL │
│  │ unsupported_content_type 保持 local-only                         │
│  │ UI: pending + 空 task_id 可手动同步；非空 task_id / syncing 禁重复 │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              Skill System (可扩展技能)                 │   │
│  │  [搜索] [浏览] [评论分析] [UP主追踪] [自定义...]         │   │
│  └──────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────┤
│           多源适配层 (SourceAdapter Protocol, v0.3.0+)         │
│  ┌──────────────┐  ┌──────────────────┐  ┌─────────────┐    │
│  │ B 站 Adapter  │  │ Bili/小红书/抖音/YT/知乎/Reddit/Linux.do任务桥│ │ Web Adapter │  │
│  │ B站/微博 HTTP │  │ Bili/小红书/抖音/YouTube/知乎/Reddit任务桥│ │ Web Adapter │  │
│  │ (WBI API+DOM兜底)│ │ (扩展代理 + DOM-first + XHS持久熔断)│  │ (Playwright │    │
│  │              │  │ + profile/search/feed/yt/zhihu)│ │ + LLM 抽取)│    │
│  └──────────────┘  └──────────────────┘  └─────────────┘    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ sources.platforms：十一平台 alias / strategy / URL host      │ │
│  │                  → 统一 pool accounting / viewed identity │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ DouyinProducer -> Service: search/hot/feed 独立终态 -> raw candidates │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ YoutubeDiscoveryProducer: 后端直连 yt_search/trending/channel │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ XAdapter + XDiscoveryProducer: 服务端 cookie 重放(twitter-cli) │ │
│  │   search / feed / creator + likes/bookmarks 共用源健康状态机 │   │
│  │   行为采集: 扩展 MAIN-world GraphQL tap + generic collector   │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ ZhihuDiscoveryProducer: 插件登录态 search/hot/feed/creator/related -> pending eval │ │
│  │   fetch-zhihu 只做 smoke；guided init 勾选知乎才进首版画像       │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ RedditDiscoveryProducer: rdt-cli 默认 + 插件 fallback search/hot/subreddit/related -> pending eval │ │
│  │ [network].mode -> X twitter-cli / Reddit rdt-cli·OpenCLI；插件 fallback 跟随浏览器网络设置       │ │
│  │   Reddit bootstrap_events: saved/upvoted/subscribed -> 首版画像信号 │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ LinuxdoDiscoveryProducer: 插件同源 GET search/hot/feed/creator/related -> pending eval │ │
│  │   bookmarks/likes/read_history -> favorite/like/view；Cookie/raw response 不上传 │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ BangumiDiscoveryProducer: 默认匿名 API search/ranked/latest；可选个人令牌读私密收藏(401降级) │ │
│  │   显式公开 username collections -> 首版画像信号；无 Cookie、无站内写入 │ │
│  │   扩展身份桥(bgm.tv/bangumi.tv): 上报公开 uid+用户名做零配置账号识别，非任务桥/无行为采集 │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ V2EXDiscoveryProducer: 匿名 API/Feed search/node/tab/hot/latest；PAT 可选，401/403 降级匿名 │ │
│  │   有界 Topic 详情 + PAT Reply digest -> v2ex:<topic_id> 文字卡；Reply 不单独入池 │ │
│  │   四只读 scope + route/耗尽证明 -> staged ingress -> identity gate -> 账号分区 Node affinity │ │
│  │   首个 complete 收藏 scope 种基线；后续连续两次缺失 -> durable retract/restore │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Cookie/登录态、runtime-stream presence、任务持久化/claim、seen-key 去重 │ │
│  └──────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────┤
│      模块路由 → LLM 实例链 → Provider 适配 + Embedding（独立双层缓存） │
│  配置恢复草稿（正常/降级）→ probe 临时 registry / 精确实例 GET /models（不写盘）│
│  ┌──────────────────────────┐  ┌────────────────────────┐   │
│  │ OpenAI / Claude / Gemini │  │ EmbeddingService       │   │
│  │ DeepSeek / Ollama /      │  │ L1 内存 + L2 SQLite    │   │
│  │ OpenRouter / OrcaRouter  │  │ Ollama bge-m3 兜底可选  │   │
│  └──────────────────────────┘  └────────────────────────┘   │
│  可选视觉 / 弹幕预热：质心、关键帧、完整 document embedding；endpoint provenance + stable slot retry │
│  Desktop bundle: official Ollama.app runtime (ollama + runner dylibs/assets) │
│  LLMService caller bucket → inherit global chain / custom chain │
│  cognition named views → task gate: awareness_confusions compact; others legacy │
│    └→ token diet: preference packing + weighted recent/judged/relevant/important insight≤40 → full merge │
│  discovery evaluator: text + metrics + optional compressed cover image input │
│    └→ embedding prefilter shadow → privacy-safe decision → raw score/admission join → read-only gate │
│  OpenAI auth_mode: api_key / experimental Codex ChatGPT transport│
│  结构化 JSON helper: wrapper / fenced / JSONL / schema echo / MiMo 容错 │
├──────────────────────────────────────────────────────────────┤
│                    多层网状记忆存储                             │
│  ┌───────────┐ ┌─────────────┐ ┌────────────┐ ┌─────────┐  │
│  │ 核心记忆    │ │ 情景记忆     │ │ 语义记忆    │ │ 工作记忆 │  │
│  │ (JSON)     │ │ (SQLite +   │ │ (知识图谱/  │ │ (内存)  │  │
│  │ Soul+偏好   │ │  向量索引)   │ │  JSON)     │ │         │  │
│  └───────────┘ └─────────────┘ └────────────┘ └─────────┘  │
│  SQLite: events(inferred_satisfaction) / seen_items(views+saves+snapshot)   │
│          discovery_candidates → relevance + temporal eligible/review_due/expired admission │
│          evaluator_prefilter_shadow_audit (30d / 20k bounded, no raw content) │
│          eval_scorer: llm / shadow(LLM authoritative) / learned hybrid │
│          evaluator_learned_scorer_shadow_audit (30d / 20k bounded, no raw) │
│          discovery_keywords → 24h safe cross-digest pending reconcile (0=hard expiry) │
│          admitted pending copy → bounded copy-ready watermark → serve/refill │
│          discovery_keywords(+cohort gate) / discovery_inspiration_*│
│          content_cache(item_key; review_due → temporal_review_hold; expired → stale) │
│          recommendations(item_key) / chat_turns / card_settlements / avoidance_state          │
│          saved_items/memberships/native_save_states + durable task ledger │
└──────────────────────────────────────────────────────────────┘
```

### 3.1 视觉 embedding 与候选预热契约

推荐的可选视觉链路由封面视觉加成（P1 cover）、用户视觉画像质心（P1 profile）和视频关键帧
（P3）组成；弹幕是独立的纯文本 P2 信号。`keyframe_enabled` 单独开启时，只要多模态
embedding 可用，仍会构建 P1/P3 共用的视觉质心；P1 cover bonus 仍只由
`visual_profile_enabled` 控制。

所有持久化向量都带 embedding provider / model fingerprint 与维度。质心命名空间、关键帧
sampling signature（包含算法版本与 `keyframe_max_frames`）或维度变化时，旧行视为待重建；
弹幕已有摘要时优先重嵌入，不重复抓取源数据。关键帧和弹幕 fetch / parse / embed 结果区分
`success`、确认的 `no_data` 与 `transient_failure`，只有安全成功条件才写完成状态，瞬时失败
保留下一轮重试资格。

预热只处理当前 fresh、可服务候选池，并使用 `keyframe_fetch_limit` / `danmaku_fetch_limit`。
`danmaku_max_chars` 控制完整摘要的 document embedding/cache 输入，不静默截断固定前缀。跨
平台 bonus 以 0 为固定点，正负两侧分别按平台内极值缩放；0、缺失和全零保持 0，禁止凭空
生成相反符号。离线视觉报告必须镜像生产评分。

远程浏览器扩展认证独立于平台登录态：管理员通过 CLI 生成设备密钥，后端只保存摘要；扩展向 `/api/auth/extension-token` 换取短会话。普通 HTTP 使用 Bearer Header，WebSocket 与图片代理只携带短会话 query。该能力默认关闭，撤销设备密钥会使全部现有会话立即失效。

可选 HTTPS 只增加传输入口，不改变 FastAPI 业务数据流。公网域名 Docker 部署叠加 Caddy
overlay：自动证书、REST / WebSocket 反代、shared-loopback upstream、宿主机 `8420` 仅 loopback，
并让 Uvicorn 只信任 `127.0.0.1` 的 forwarded headers。LAN/self-managed TLS Proxy 则要求
Web Origin 与 Host 的 host+port 精确同源，Chrome/Firefox 扩展 Origin 走现有扩展认证契约；
代理把 TLS cookie 标为 `Secure` 并转发真实 WebSocket。其证书生成使用 `[tls]` extra 的
`cryptography`，首次远程部署必须显式给出访问 IP/hostname SAN；缺少远程 SAN 时只承诺
localhost。两个入口互斥，默认 HTTP 不变。

---

## 4. 技术选型

| 模块 | 技术方案 | 说明 |
|------|---------|------|
| 编程语言 | **Python** (后端) + **TypeScript** (插件) | 后端 AI 生态 + 前端插件 |
| LLM 接入 | **多模型**：OpenAI / Claude / DeepSeek / 本地模型等 | 全部支持，优先效果 |
| B 站交互 | **API 优先** (bilibili-api-python)（实际实现使用自研 `BilibiliAPIClient`，不依赖此库）+ **agent-browser** (浏览器操作) | API 快速高效，agent-browser 补充复杂交互 |
| 浏览器操作 | **[agent-browser](https://github.com/vercel-labs/agent-browser)** | Vercel 的 AI Agent 专用浏览器 CLI |
| 浏览器插件 | **Chrome Extension** (Manifest V3) | 行为采集 + 交互 UI + LUI |
| 可选 HTTPS 入口 | **Caddy Docker overlay** + **Python stdlib TLS Proxy / cryptography `[tls]` extra** | 默认关闭；公网域名自动证书，或 LAN/self-managed 本地 CA/SAN，两种入口互斥 |
| Agent 框架 | **自研轻量框架**，按需扩展 | 灵活可控，支持 Skill 系统 |
| 记忆存储 | **SQLite** + **向量索引** + **JSON** | 分层存储，匹配不同记忆类型需求 |
| 任务调度 | **asyncio runtime loops** + `[scheduler]` 配置 | 按前端可换候选缺口、raw-material headroom、行为阈值和策略间隔执行内容发现；pending raw 评估有独立 loop；推荐 serve 与 pool maintenance 使用分离的单线程 SQLite worker，维护按 ≤50 行事务分批让锁；不依赖 cron |
| 运行模式 | **本地运行** | 用户自己的电脑上执行 |

---

## 5. 版本规划

### v0.1 — MVP：最小推荐闭环

> **核心目标**：证明"深度理解用户 → 主动发现内容 → 有温度地推荐"是可行的。

- [ ] 项目骨架搭建（Python 后端 + Chrome 插件 + 配置管理）
- [ ] B 站 API 接入 + agent-browser 集成
- [ ] 浏览器插件 MVP：基础行为采集（点击/浏览/搜索 + 页面快照）
- [ ] 多层记忆架构基础版（事件层 + 偏好层 + 灵魂层）
- [ ] 基础 Soul Engine：从行为数据中构建初步人格理解
- [ ] 基础内容搜索与推荐
- [ ] 插件内 UI：查看推荐、提供反馈、基础对话
- [ ] 多 LLM 支持框架

### v0.2 — 更深层的理解

- [ ] 完整行为采集（微行为、DOM 上下文、浏览路径）
- [ ] 完整五层记忆架构 + 网状关联 + 自我编辑能力
- [ ] 苏格拉底式深度对话（追问/假设/确认/调整）
- [ ] 多策略内容发现（相关推荐链、排行榜、评论区挖掘）
- [ ] "发现惊喜"模式：跨领域探索
- [ ] Skill 系统 v1：内置 Skill + 自定义 Skill 支持
- [ ] 推荐质量自省和策略迭代

### v0.3 — 更好的体验

- [ ] 插件 UI 升级：丰富的 LUI 交互体验
- [ ] 情境感知推荐（时间/情绪/场景自适应）
- [ ] 定时自动发现和推送
- [ ] 记忆可视化（查看 Agent 对你的理解）
- [x] 配置页跨机器导出 / 导入可移植用户状态
- [ ] UP 主追踪和新视频提醒

### v1.0 — 成熟的开源工具

> 注：项目已确定为严格单用户设计，不再计划多用户支持。

- [ ] 多用户支持 + 配置系统
- [ ] 完善的安装和使用文档
- [ ] 插件商店发布
- [ ] 社区 Skill 市场
- [x] 跨平台内容发现（已落地 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Linux.do / Bangumi / 通用 Web，后续继续扩展更多 adapter）
- [x] 跨平台内容发现（已落地 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / Bangumi / V2EX / 通用 Web，后续继续扩展更多 adapter）

---

## 6. 设计原则

1. **灵魂优于标签** — 理解一个人，而不是给他贴标签
2. **有温度的表达** — Agent 的每一次输出都像朋友在说话
3. **主动追问和假设** — 不等用户说，主动猜测并验证
4. **用户掌控权** — 用户可以查看、修正、引导 Agent 的理解
5. **隐私本地化** — 所有数据和计算在本地；用户可导出可移植状态，但迁移包含明文敏感信息，边界必须显式可见
6. **开放可扩展** — 通用开源设计 + Skill 系统

---

*文档版本: v0.3 | 日期: 2026-08-09 | 状态: 持续更新*
