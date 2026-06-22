# Discover 重构：双缓冲背压 + 合并关键词生成（Design Spec v2）

> Status: Approved for P1 — 已过 Codex 对抗 review 3 轮（R3 PASS）+ owner 决策已锁定（2026-06-14）
> Date: 2026-06-14
> Scope: 仅 Discover 的**搜索（search）子来源关键词生成**。**不涉及** Recommend / serve / 排序、Soul / 画像生成，也**不动**各平台的非 search 子来源（见 §1）。

## 0. 一句话

把"每平台各自定时调 LLM 生成**搜索**词"改成 **缺口拉动的双缓冲背压模型**：一个**关键词存储**（cache + 历史 + 产出）夹在「生成」和「抓取」之间。生成只在缓存见底且池子有真实缺口时触发（一次合并 LLM 调用覆盖所有缺货平台，带历史去重 + 池子分布避让），抓取只在池子有缺口、有未搜词、且距上次抓取过了限流地板时触发；**池满即全停，被消费后自动重启**。

---

## 1. 范围（精确）

只改**每个平台的 search 子来源**的"关键词从哪来"。其余一律不动：

| 改 | 不改（保持现状的调度/预算/cadence）|
|---|---|
| B站 `search`、小红书 `xhs-search`、抖音 `dy search`、YouTube `yt_search`、X `x-search` 的**关键词来源** | B站 `trending` / `explore` / `related_chain`；YouTube `yt_trending` / `yt_channel`；X `x-feed` / `x-creator`；抖音 `hot` / `feed`；以及它们各自的 daily budget / min_interval |

> ⚠️ 关键约束（Codex 指出）：YouTube/X producer 在**同一周期**里同时跑 search 与非 search 策略；抖音 `_keywords` 在 `sources` 分支判断**之前**就会算（即便没开 search）。所以本方案**只接管 search 那一路的关键词**，绝不能因"关键词缓存空"而误停 `yt_trending` / `x-feed` / 抖音 `hot` 等。详见 §11 迁移。

**Out**：Recommend/serve/MMR/排序/文案；Soul/画像生成（画像在此**只是输入**）；内容池被谁消耗（Discover 只读"池子数量 vs 目标"）；上表右列的非 search 子来源。

---

## 2. 动机

当前每平台在独立 loop、各自节奏里各调一次 LLM 生成 search 词。两个问题：

1. **成本**：每次都重发整份 `build_profile_summary`（~8–20k tokens）。跨平台 + 跨时间双重冗余（画像 12h 才变一次）。
2. **丰富度**：**没有跨轮关键词记忆/去重**（`last_intermediates` 用完即弃，`search.py:96`）。同一份稳定画像 → 相似词 → 相似内容；serve 端 MMR 只能在已抓进来的里挑多样，救不了供给重复。

目标：**更少 LLM 调用 + 每次搜的词不重复（真丰富度）+ 只在需要时工作。**

---

## 3. 当前基线（已按 Codex 核对修正）

| 维度 | 现状（含修正）|
|---|---|
| 内容池 | `content_cache`，`pool_target_count=300`（1–600），`pool_source_shares=5:1:1:1:1`（仅已启用平台间分），serve 时每 `topic_group` ≤ `max_per_topic_group=3` |
| B站触发 | **池子低于目标时**，`["search","related_chain"]`+`trending`+`explore` 会**一起**进 plan（`refresh.py:1357,1361,1367,1380`）；事件(≥6 信号)/时间(trending 3h/explore 12h) cadence 只在**未低于目标**时用 |
| 外站触发 | 各 producer 独立 60s 轮询 + 节流地板：小红书 1h、抖音 30m、YT 60m、X 60m（`*_producer.py`）|
| 每轮 search 词数 | B站 search 8（`search.py:48`）；小红书 5（`xhs_producer.py:53`）；YT 6（`youtube.py:82`）；X 4（`x.py:153`）；**抖音 producer 实际传 `keywords_per_run=1`**（`douyin_producer.py:90`，覆盖 strategy 默认 5）|
| 有效入池阈值 | 来自 `candidate_pipeline._default_score_thresholds`：search 0.65 / trending 0.60 / hot 0.60 / related(_chain) 0.65 / explore 0.58 / feed 0.60 / backfill 0.60（`candidate_pipeline.py:31`，**覆盖** strategy 里的 0.70 默认）|
| 缺口口径 | 补池不是只看"池里可见数"，还会算 **raw-material headroom + pending/evaluating 在途行**（`refresh.py:2109,2119,2122`；`database.py:1973,1984`）|
| 池子分布感知 | **仅 B站**：search 收 `pool_distribution_hints`（avoid_topics/styles/franchises + source_deficits；`prefer_axes` 现为空，已屏蔽）；explore 收 `covered_topic_groups`。`get_pool_distribution_counts` 只按**全局** topic/style/franchise 统计，**不分平台**（`database.py:2102,2136`）|
| 确定性兜底 | **只有** B站(`search.py:426`)/YouTube(`youtube.py:148`)/抖音(`douyin_direct.py:202`) 在 LLM 失败/无兴趣时回退兴趣名；**小红书(`xhs_keyword_gen.py:64,68`)、X(`x.py:184,198`) 直接返回空**（⚠️ 本方案依赖兜底，故 P1 必须给 XHS/X 补上）|
| 关键词记忆 | **无**（核心缺口）|
| 策略关键词注入口 | 不统一：B站 `SearchStrategy` **无**外部 query 入参（`search.py:59`）；YouTube search **无**（`youtube.py:92`）；X 有 `query`（单个，`x.py:162`）；抖音有 `seed_keywords`（`douyin_direct.py:113`）；小红书是 producer 级（`xhs_producer.py:89`）。⚠️ 直接"读缓存"会破坏 B站/YouTube，必须先加注入口（§7.4）|

---

## 4. 新模型总览：双缓冲背压

每个平台维护**三态**（合一张表，§5.1），由轻量轮询按**真实缺口**驱动：

```
   生成① 条件：关键词缓存 < 低水位 且 该平台 search 缺口 > 0（含在途扣减）
   （输入三件套：profile + 历史窗口 + 池子分布；带"别重复/别堆满"约束）
 [profile_kw_digest] ──合并LLM──▶ [关键词存储] ──抓取②──▶ [内容池] ──(被消费,out)──▶ 掏空
                                  (pending/平台)         (目标按 share)
   抓取② 条件：search 缺口 > 0 且 缓存有可领取(claim)的词 且 距上次抓取 ≥ 限流地板

   全停：内容池满 且 关键词缓存满
   重启：池被消费 → 缺口重现 → ②先跑(有词)/①先跑(没词)
```

- **触发模型（一句话）**：**定时轮询（规划器 ~2min / producer 60s）只负责"检查"；真正执行（生成/抓取）由"缺口 + 事件"驱动**——缺口 = 用户消费推出的 headroom，事件 = B站 ≥6 信号 / 画像 digest 变；**限流地板**封顶执行频率。空闲零调用、有消费连续补，**不是固定周期定时任务**。
- 频率是**派生量**；**丰富度来自"每次抓不同的词"，不是来自提高抓取频率**——抓取频率仍被平台限流地板封顶。

---

## 5. 核心组件

### 5.1 关键词存储 `discovery_keywords`（cache + history + yield 合一）

**必须像现有任务队列（`xhs_tasks`/`dy_tasks`）一样有原子领取语义**（Codex Critical）。

| 字段 | 含义 |
|---|---|
| `id` | 主键 |
| `platform` | bilibili / xiaohongshu / douyin / youtube / twitter |
| `keyword` | 搜索词 |
| `profile_kw_digest` | 生成时画像关键字段的稳定 digest（§8，**不用** `OnionProfile.version`）|
| `status` | `pending`→`claimed`→（内联:`used`/`failed`）｜（异步:`executing`→`used`/`failed`），可 `expired` |
| `created_at`/`claimed_at`/`executing_at`/`used_at` | 时间戳（租约回收用）|
| `attempts` | 失败重试计数（超限→`failed`）|
| `yield_count` | 该词带来的**新入池**条数（§5.3，P1 就写；按 `source_keyword_id` 回填）|

约束/索引：
- **部分唯一**（Codex R2）：`UNIQUE(platform, keyword, profile_kw_digest) WHERE status IN ('pending','claimed','executing')` —— 只防"在途重复入库"；`used`/`expired`（历史）不参与唯一，故 digest 不变、窗口过期后**仍可重新生成同词**（解决"唯一约束挡回收"）。
- 索引 `(platform, status, profile_kw_digest)`、`(platform, status, used_at)`。
- **原子领取**：`BEGIN IMMEDIATE` + `pending→claimed`（带 `claimed_at`），对标 `xhs_tasks.py:276,301`、`dy_tasks.py:397,411`。
- **租约回收**：`claimed` 超 `claim_lease`（~10min）、`executing` 超**任务超时**未变 → 回 `pending`（防 loop/任务崩溃泄漏）。

**状态机（词只在终态才离开"在途"，绝不在 enqueue 时标 `used`；`used` 与 yield **解耦**——yield 一律在 admit 按 `source_keyword_id` 回填，见 §5.3）**：
- **内联评估并入池**（B站 search、抖音 plugin：抓→评估→admit 都在本调用内）：claim→抓（阻塞到结果）→admit→同步标 `used`；抓取异常/空→`failed`（重试）。
- **fetch-only → 交共享 pipeline 延后入池**（**X、YouTube** producer 只取 raw、不在 producer admit）：claim→抓 raw→交 `discovery_candidates` 后即 `used`（词已被消费；admit 由 candidate_pipeline 后续做）。
- **真正异步**（**仅小红书**：扩展 out-of-band 执行）：claim→enqueue 平台任务（携 `source_keyword_id`）→词置 `executing`（**不是 used**）→任务终态回调（成功/失败/超时）→标 `used` 或 `failed`(重试)；无回调则 `executing` 租约回收。
- 状态映射：缓存=`pending`；历史窗口=`status IN ('claimed','executing','used') 且时间在 W 内`（含在途，防重复生成）；digest 变→旧 `pending` 批 `expired`、`used`/`executing` 保留（去重 + 等回填）；窗口外 `used` **归档**（保 yield 历史）后才允许回收。

### 5.2 关键词规划器（合并生成，单飞）

新增 `_loop_keyword_planner`（轮询 1–3 min，平时零成本）：

1. 扫所有平台，找 `due` = {缓存 pending < 低水位 **且** 该平台 search **真实缺口** > 0}。真实缺口**复用现有补池口径**（含 raw headroom + 在途，§3），不是只看可见池数（Codex Major：防振荡/防对着已满的 raw 队列继续生成）。
   **B站 额外催化（owner 决策，§16.3）**：B站 在其现有触发命中时也直接进 `due`——池子低于目标（四策略一起跑）或 ≥6 信号事件（画像可能刚漂移，正好重算 `profile_kw_digest`/换词），不必等缓存掉到低水位。其余平台纯缺口驱动。
2. `due` 空 → 无操作。
3. `due` 非空 → 对每平台现算池子分布 + 取历史窗口 → 组装**一次合并 LLM 调用** → 新词 `pending` 入库补到高水位。
4. **单飞 = 短 CAS 锁，不跨 LLM 持事务**（Codex R2 Major）：用一张**锁表**行做 CAS（`owner` + `locked_until`）——`BEGIN IMMEDIATE` 内 CAS 抢锁后**立即 commit 释放事务** → 在**不持任何 DB 事务**下调 LLM → 再短事务写回结果并续约/释放锁。**绝不在调 LLM 时持 SQLite 写锁**（否则阻塞所有 writer）。`locked_until` 到期自动可抢（防规划器中途崩溃）。
5. 失败/缺平台块 → 该平台回退确定性兴趣名（**前提：P1 已给 XHS/X 补兜底**）。
6. **稀疏画像不饿死**（Codex R2 Major）：若某平台有缺口但 LLM 只能产出"历史里已有"的词（兴趣太少、窗口盖全）→ 触发**回收**：把该平台**最久未用的 `used` 词**（最小 `used_at`）放回 `pending`，按更短有效窗口轮换。保证"有缺口 + 有任何历史词"时缓存不空——稀疏用户就在自己的小词集上更长周期轮换（这是正确行为）。

### 5.3 产出追踪（P1，不延后）

Admission 会因 score/已看/franchise 配额拒掉，"5 词 ≈ N 条"不成立，故 yield 必须实测、入 P1。**`source_keyword_id` 端到端传播契约**（Codex R2 Major：现有 schema 都没这列，P1 加）：
- **加列**：`discovery_candidates`（`candidate_pool.py`）、`content_cache`（`database.py`）、各平台任务 payload（`xhs_tasks`/`dy_tasks`）、normalized 候选（`DiscoveredContent.raw_payload` 或新列）各带 `source_keyword_id`。
- **链路**：claim 词(id) → 抓取/enqueue 携 `source_keyword_id` → 候选透传 → admit 成功 `UPDATE discovery_keywords SET yield_count=yield_count+1 WHERE id=?`。
- **幂等**：yield 自增按 `(source_keyword_id, content_id)` 去重，容忍部分/乱序/重试不重复计数。
- **抖音回流缺口**：抖音 task-result 当前**不把 search 视频回流到 discovery_candidates**（`api/app.py`）——P1 需补这条回流，**或**确认抖音 plugin search 内联路径已直接走 `evaluate_content_batch`（二选一明确，不能悬空）。
- 用途：① 高水位按实测产出估（缺口÷平均 yield）；② 连续 0 产出→判枯竭→`expired`/冷却。

### 5.4 缺口驱动的抓取

各平台 search 抓取（沿用现有 producer/strategy 框架，§7.4 注入）：

1. 距上次该平台抓取 < 限流地板 → 跳过（风控硬约束）。
2. 该平台 search 缺口（含在途）= 0 → 跳过。
3. 无可 claim 的 `pending` 词 → 跳过（等规划器补）。
4. 否则：原子 claim ~N 个词 → 调该平台 search → 走现有评估/入池 → 标 `used`、写 `used_at`、回填 `yield_count`。
5. **快环不触发①**（只消费），避免 plan↔池子 互追（§9）。

---

## 6. 控制逻辑（参数）

| 参数 | 含义 | 默认（owner 已接受作起步基准，§16.2）|
|---|---|---|
| `pool_share_target` | 该平台 search 池目标（300 × share 推出）| 例：小红书 ≈ 33 |
| `kw_cache_high` / `kw_cache_low` | 高/低水位 | 30 / 10（P1 起步；P3 → 缺口÷yield 动态）|
| `gen_batch` | 单平台单次生成词数 | ~30 |
| `fetch_batch` | 单次 claim 的词数 | ~5 |
| `history_window` | 去重窗口 | 最近 150 词 或 24–48h，滚动 |
| `fetch_floor` | 抓取最小间隔（沿用现有 min_interval）| 小红书 1h / 抖音 30m / YT·X 60m / B站按风控 |
| `claim_lease` | 领取租约 | 10 min |
| `planner_poll` / `fetch_poll` | 轮询 | 1–3 min / 2–5 min |
| `plan_ttl` | 兜底失效 | 6–12h |

触发真值表见 §4 + §5；缺口一律用**含在途**的口径。

---

## 7. 生成步详解（①）

### 7.1 输入三件套
```
① 输入 = profile（build_profile_summary，整份发一次）
        + 历史窗口（每平台：最近 used/claimed 的词 → "别再出"）   ← 词级去重，新建
        + 池子分布（avoid_topics/styles/franchises → "别再堆"）    ← 主题级避让，复用
```
池子分布**现读实时 DB**，不随关键词缓存一起缓存 → 缓存虽旧，下一批生成看的一定是最新分布。

### 7.2 合并 prompt 形状
- **system**：静态、全平台共用一份（call-invariant，符合 prompt-cache 约定）。
- **user**：`<profile_summary>`（一次）+ `<platforms>`（数组，**只含本轮 due 平台**），每平台块含 `need / supply / recent_keywords / avoid_*`。
- **output**：`{"bilibili":[...], "xiaohongshu":[...], …}` 只含请求平台。
- **成本归因**（Codex Minor）：合并调用按平台块在 usage metadata 里分别记 caller（如 `discovery.planner:bilibili`），保 `cost --by caller` 不丢平台维度。

### 7.3 平台供给优势（P2）
每平台静态"供给优势"描述（B站学习/梗、小红书生活/美妆、抖音娱乐/热点、YT 英文长内容、X 实时讨论），让模型把同一兴趣映射到各平台强项，并允许**弃权**（兴趣与该平台供给不匹配就少出/不出）。P3 可演进为数据驱动（各平台各 topic 历史入池率 / yield）。

### 7.4 策略关键词注入口（Critical，P1 必做）
合并生成后，关键词要能喂回各 search 路径。现状不统一，需补：

| 平台 | 现状 | P1 改动 |
|---|---|---|
| B站 search | 无外部 query 入参 | `SearchStrategy.discover` 加 `queries: list[str] \| None`，传入则跳过内部生成 |
| YouTube yt_search | 无外部 query 入参 | 同上，`YoutubeSearchStrategy` 加 `queries=` |
| X x-search | 有 `query`（单个）| 扩成接受多词（循环/列表）|
| 抖音 dy search | 有 `seed_keywords` | 规划器把词写进 `seed_keywords`；并修 `_keywords` 让其**仅当 `'search' in sources`** 才取词（`douyin_direct.py:126` 现在分支前就算）|
| 小红书 xhs-search | producer 级 enqueue | 规划器/抓取把词喂给 xhs producer 的 enqueue 入口 |

**调用链改动（不止改 strategy 签名，Codex R2 Major）**：现状 `ContentDiscoveryEngine._call_strategy_discover` 只透传 `limit` + 可选 `pool_snapshot`（`engine.py:411`），X runtime 经 adapter 只传 `config={}`、adapter 读单个 `query`（`x_producer.py:133`；`twitter_adapter.py:87`）。需统一一个**关键词感知的抓取 API**：在 engine kwargs / `SourceRecipe.config` / 各 producer loop / adapter 边界**一路透传 `keywords: list[str]`**（像 `pool_snapshot` 那样条件转发），各平台 adapter/strategy 改造 + 测试。这是 §7.4 表的前置工程。

---

## 8. 陈旧 / 延迟 + digest（Critical）

- **根本机制：生成永远现读最新画像**。规划器生成那一刻调 `build_profile_summary` 拿的就是**此刻**的画像——**只缓存词、不缓存画像**。所以无论画像被 **12h 整理、聊天、反馈事件**哪条路径改的，**下一批生成的词一定反映最新画像**。新鲜度的根本来源是"现读"，digest 只是优化（见下）。
- **`profile_version` 不可用**：`OnionProfile.version` 是 schema 版本（默认 2），更新画像时**不自增**（`profile.py:520`；`manager.py:201,226`）。改用 **Discover 自己算的 `profile_kw_digest`**。规划器每轮现算；digest 变 → 旧 `pending` 作废 → 下次有缺口用新画像生成。
- **digest 必须规范化 + 量化**（Codex R2 Major：原始权重每个事件都漂移→缓存狂失效；只取 interests/domains/dislikes 又太窄、漏了 keyword prompt 实际读的字段）。规则：
  - **覆盖** keyword prompt 真正吃、且**慢变**的字段：interests **top-K 名+category**、interest_domains 名、disliked_topics、core_traits、values、motivational_drivers、current_phase、cognitive_style、style 粗粒度。
  - **权重量化**：兴趣权重按粗桶（如 0.1 步长 / rank 分档）后再入 hash —— 单事件小幅漂移被量化掉，不触发失效。
  - **排除高频低影响**字段：`recent_awareness`、`active_insights`（几乎不影响搜索词却频繁变）**不进** digest。
  - 结果：digest **只在材质性的关键词相关变化时变**（12h 整理、出现新强兴趣、避雷项变），日常事件小漂移不动它。
- **小漂移**：digest 不变就吃 `plan_ttl`（6–12h）滚过，无害。
- **digest 是路径无关的，且只解决一个边角情况**：digest 每轮直接对**当前画像**做 hash，画像被聊天/反馈/整理哪条路径改的都**自动覆盖**，**不用给每条改画像的路径埋点**。它**不是**新鲜度来源（那靠"现读"），只堵一个窗口——**池满/空闲时画像变了**：此时无缺口 → 不触发"下一批生成" → 旧词躺着；用户回来消费出缺口 → 头几次抓取会用到旧画像的词（到低水位才重生成）。digest 在画像**实质变化**时立刻作废 pending，把这窗口堵掉。
- **简化选项（owner 可选）**：想更简可**砍掉 digest**，纯靠"下一批用新画像"。代价仅是"空闲→画像大变→用户回来"时头几次抓取用旧词那一小段；其余情况（池在掉、会自然重生成）无差别，`plan_ttl` 仍兜底。**默认保留 digest**（hash 便宜、路径无关、自动覆盖聊天/反馈，且无需空闲期照样轮询画像）。
- **池子反馈延迟**：分布在生成/抓取时现读 DB，不烘焙进缓存。
- **防震荡**：①跟 digest（慢环），②跟缺口（快环），**②不触发①重生成**。

---

## 9. 时间线举例（小红书，目标 30，high/low=30/10，fetch_batch=5）

| 时刻 | 事件 | 池 | 缓存 pending | 历史 |
|---|---|---|---|---|
| T0 | 缓存空+缺口→**①生成 30** | 0 | 30 | 0 |
| T0+ | claim 5→抓→入~15 | 15 | 25 | 5 |
| T0+ | claim 5→~15 | ~30 满 | 20 | 10 |
| T0+ | 池满→②停；缓存 20>low→不①  | 满 | 20 | 10 | **全停** |
| +30m | 消费掉 12（out） | 18 | 20 | 10 |
| +30m | 缺口→claim 5→满 | ~30 | 15 | 15 |
| +1h | 消费 18 | 12 | 15 | 15 |
| +1h | claim 5→缓存=10 触 low | ~27 | 10 | 20 |
| +1h | low+缺口→**①生成 30 新词**（避开历史 20）| ~27 | 30 | 20 |
| 用户停看 | 缺口 0→①②全停 | 满 | … | … | **零成本** |
| +12h 画像变 digest | 旧 pending→expired→下次用新画像① | … | … | … |

---

## 10. 成本分析（粗估，示意）

改前每次都重发画像；外站每小时级 + B站缺口时四策略一起，**重发画像**是大头。改后①只在"缓存见底+真实缺口"触发、合并一次发画像一遍 → 估 **~4–6× 降本**，外加丰富度提升。实测以 `openbiliclaw cost --by caller` 为准（§7.2 保住平台维度）。

---

## 11. 迁移（与现有的关系）

| | 处理 |
|---|---|
| **保留** | 内容池(300/shares/max_per_topic)；评估+入池流水线（含 `candidate_pipeline` 阈值/配额/在途口径）；各平台抓取+normalize；限流地板；daily budget；**所有非 search 子来源**（trending/explore/related/feed/hot/channel/creator）原样 |
| **新增** | `discovery_keywords` 表（含 claim/lease/yield）；`_loop_keyword_planner`（合并生成+单飞）；缺口驱动的 search 抓取；XHS/X 的确定性兴趣名兜底 |
| **改动** | 5 个 search 关键词生成从"strategy/producer 内部调 LLM"→"读 `discovery_keywords`"；§7.4 各 strategy 加注入口；抖音 `_keywords` 仅 search 启用时才取词；现有 5 个 search 关键词 prompt builder 收编为合并 builder 的平台分块 |
| **与现有任务队列的边界**（Codex R1+R2，X/YT 形态经 P1-plan review 细化）| `discovery_keywords` 是**生成侧** cache/history；`xhs_tasks`/`dy_tasks` 是**执行侧**。**三种执行形态**、词终态语义不同（详 §5.1）：<br>• **内联评估并入池**（B站 search、抖音 plugin search：enqueue 后同步等待返回视频并评估入池 `douyin_plugin_search.py:168-194,268-285`）：claim→抓→admit→同步标 `used`。<br>• **fetch-only→pipeline 延后入池**（**X、YouTube** producer 只取 raw、不在 producer admit）：交 `discovery_candidates` 即标 `used`，admit 由 candidate_pipeline 后续做。<br>• **真正异步**（**仅小红书**：扩展 out-of-band）：claim→enqueue（携 `source_keyword_id`）→`executing`→**任务终态回调**才 `used`/`failed`(重试/超时回收)。<br>**`used` 只在终态**（不是 enqueue 时）、**与 yield 解耦**（yield 一律在 admit 按 `source_keyword_id` 回填）；XHS 需 task-result handler 回写 `used`/`failed`。执行预算/完成仍由平台任务队列负责 |
| **回退** | 规划器/LLM 失败或缓存空 → 各平台确定性兴趣名（P1 先把 XHS/X 补齐）|

---

## 12. 分期

- **P1（核心 + 必备正确性）**：`discovery_keywords` 表（claim/lease/yield/digest）+ 合并 builder + `_loop_keyword_planner`（单飞）+ §7.4 注入口 + 缺口驱动抓取（含在途口径）+ 历史窗口去重 + `profile_kw_digest` 失效 + **XHS/X 兜底** + **yield 追踪** + 成本归因。池子分布先**全局**推广到全平台。
- **P2**：平台供给优势静态表 + 弃权 + 关键词池轮换打磨。
- **P3（可选）**：动态缓存上限（缺口÷yield）/ 退役枯竭词；数据驱动供给优势；按平台饱和；按需并入 trending/explore。

---

## 13. 已决策 / 仍开放

**已决策（owner, 2026-06-14）：**
1. **正向补缺口（`prefer_axes`/undercovered）→ 不加，维持屏蔽**。池子分布只用负向 avoid（避饱和），不做正向补缺。先前那版被否的实现保持屏蔽不复活。
5. **B站 search 触发 → 保留**："低于目标→四策略一起 / ≥6 信号"作为生成①的**额外催化**，叠加在缺口驱动之上（见 §5.2）。其余平台纯缺口驱动。

**仍开放（P2/P3，不阻塞 P1）：**
2. **饱和粒度**：P1 用全局 avoid（**已知会过度避让**：B站 满的 topic 在小红书可能没有），按平台饱和列 P2。
3. **供给优势来源**：静态 vs 数据驱动（P2/P3）。
4. **缓存上限**：固定水位 vs 缺口÷yield 动态（P3）。

---

## 14. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 合并调用一次失败 → 全平台缺词（XHS/X 现无兜底）| **P1 先给 XHS/X 补确定性兜底**；部分输出可用（不要求全平台都返回）|
| 多 loop + 重启并发领取/重复搜 | `BEGIN IMMEDIATE` 原子 `pending→claimed` + 租约回收（对标现有任务队列）|
| 规划器与 producer/`_refresh_lock` 重入 | 规划器自带 DB 级单飞租约 |
| 缺口振荡（无视在途 raw）| 缺口用现有补池口径（含 raw headroom + 在途）|
| "持续"被误读为恒定高频 | 生成仅低水位触发；稳态次数 ∝ 消费；池满归零 |
| 抓取被误读为可无限提速 | 限流地板硬约束；丰富度靠换词不靠提频 |
| 新词≠新内容（bvid upsert 可复活已抑制行）| yield 追踪入 P1，枯竭词退役 |
| 历史窗口穷尽角度 / 老内容永不复搜 | 窗口滚动过期、老词回收 |
| 误停非 search 子来源 | §1/§11 明确只接管 search 一路；抖音 `_keywords` 仅 search 启用才取 |
| 异步任务在 enqueue 就标 used → 失败静默烧词 | §5.1/§11：`used` 只在终态；异步走 `executing`+终态回调；`failed` 重试；租约回收 |
| digest 随每事件权重漂移狂失效 / 漏字段 | §8：权重量化分桶 + 覆盖慢变 keyword 字段 + 排除 awareness/insights |
| 单飞锁跨 LLM 持 SQLite 写事务阻塞全员 | §5.2：短 CAS 锁表，调 LLM 时不持事务 |
| 唯一约束挡同词回收 | §5.1：部分唯一仅覆盖在途态 |
| 稀疏画像生成不出新词→缓存饿死 | §5.2：回收最久未用词、缩短有效窗口轮换 |
| yield 跨表对账丢失/重复计 | §5.3：`source_keyword_id` 端到端 + `(kw_id,content_id)` 幂等 |

---

## 15. 测试计划

- 单元：水位真值表各分支；含在途的缺口计算；历史去重（含 claimed 在途）；`profile_kw_digest` 变→作废 pending；原子 claim 防重复；租约回收；yield 回填；枯竭词退役。
- 合并 builder：system call-invariant；user 含 profile 一次 + 仅 due 平台块；输出按平台 key 解析；缺块/失败→各平台兜底（含新补的 XHS/X）；按平台成本归因。
- 集成：冷启动填满→停；消费→重启；池满全停零调用；限流地板生效；**非 search 子来源不受关键词缓存影响**；抖音 hot/feed-only 模式不再触发 search 关键词生成；xhs/dy 任务队列与关键词存储的 `used`/yield 跨表对账。
- 成本：`cost --by caller` 对比改前后调用数 + 平台归因不丢。

---

## 16. 决策（owner 已拍板，2026-06-14）

1. 正向补缺口（`prefer_axes`）→ **不加，维持屏蔽**（§13.1）。
2. §6 水位/窗口/批量/租约默认值 → **接受作为起步基准**。
3. B站 search → **保留**事件/四策略触发作为①的额外催化（§5.2 / §13.5）。
4. P1 范围 → **确认** = 5 个 search 生成器 + 必备正确性（claim/lease、digest、注入口、XHS/X 兜底、yield、成本归因），trending/explore 暂不并（§12）。

---

## 17. Review log

- **R1（Codex，2026-06-14）**：CHANGES REQUIRED。已逐条吸收——
  - 事实修正（§3）：B站 低于目标四策略一起；抖音 producer `keywords_per_run=1`；有效阈值在 `candidate_pipeline`（search 0.65/explore 0.58/…）；XHS/X **无**确定性兜底；YT/X 同周期跑非 search 策略；`get_pool_distribution_counts` 不"drain"池子（口径澄清）。
  - Critical：`profile_version` 非修订计数 → 改 `profile_kw_digest`（§8）；缺原子 claim/lease → §5.1 状态机+索引+租约；策略 API 不支持注入 → §7.4 注入口。
  - Major：缺口须含 raw headroom+在途（§5.2/§3）；yield 提到 P1（§5.3）；XHS/X 兜底入 P1（§3/§14）；与 xhs/dy 任务队列双源 → §11 边界定义（`used`=已交执行 + `source_keyword_id` 对账）；抖音 search 未开仍生成 → §7.4 修；非 search 误停 → §1/§11；全局 vs 按平台 avoid → §13.2 决策。
  - Minor：成本归因 → §7.2 按平台块记 caller。
- **R2（Codex，续接，2026-06-14）**：CHANGES REQUIRED。已逐条吸收——
  - Critical：异步任务 enqueue 即标 `used`（失败静默烧词）→ §5.1/§11 改"`used` 只在终态"、异步加 `executing`+终态回调+重试/租约。
  - Major：`profile_kw_digest` 用原始权重狂失效 + 漏字段 → §8 权重量化 + 覆盖慢变 keyword 字段 + 排除 awareness/insights。
  - Major：`source_keyword_id` 未端到端 → §5.3 加列(candidate/cache/task/normalized)+链路+幂等+抖音回流缺口。
  - Major：注入只改 strategy 签名不够 → §7.4 增"调用链改动"（engine kwargs / `SourceRecipe.config` / adapter 一路透传 `keywords`）。
  - Major：`UNIQUE` 挡同词回收 → §5.1 改**部分唯一**（仅在途态）。
  - Major：稀疏画像饿死 → §5.2 回收最久未用词。
  - Major：单飞锁跨 LLM 持事务 → §5.2 短 CAS 锁表、调 LLM 不持事务。
  - 纠正：抖音 plugin search 是 **enqueue 后同步等待返回**（内联），非 fire-and-forget → §5.1/§11 归入内联类。
- **R3（Codex，续接，2026-06-14）**：**VERDICT: PASS — no material issues**。逐条确认 R2 全部解决，无新增问题。
- **决策锁定（owner，2026-06-14）**：①`prefer_axes` 不加维持屏蔽；②§6 默认值接受作起步；③B站 search 保留事件/四策略触发作①额外催化；④P1 范围确认（5 个 search 生成器 + 必备正确性，trending/explore 暂不并）。详见 §16。
- **澄清（owner Q&A，2026-06-14）**：§4 补"触发模型一句话"（定时轮询只检查、缺口/事件驱动执行、限流封顶，非固定周期）；§8 补"根本机制=生成永远现读最新画像（聊天/反馈/整理全覆盖）+ digest 是路径无关的优化、只堵空闲期画像变的窗口 + 砍掉 digest 的简化选项与代价"。
- **同步（P1-plan review 回灌，2026-06-14）**：§5.1/§11 把 X/YT 由"内联"细化为 **fetch-only→pipeline 延后 admit**（X/YT producer 不在 producer 内入池）；并明确 `used` 与 yield **解耦**（yield 一律在 admit 按 `source_keyword_id` 回填）。P1 实现 plan：`docs/plans/2026-06-14-discover-backpressure-P1-plan.md`（亦过 Codex 3 轮 PASS）。
