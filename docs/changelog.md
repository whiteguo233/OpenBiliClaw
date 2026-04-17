# 变更日志

> 按里程碑记录各阶段交付内容。每次分支合回 main 时追加条目。

---

## M8: 插件后端 API（进行中）

### 多源行为采集：插件跨站 MVP

- **PlatformAdapter 接口**：`extension/src/shared/types.ts` 新增 `PlatformAdapter` 契约（`sourcePlatform` / `detectPageType` / `extractContentId` / `cardSelector` / `searchInputSelector` / `videoSelector` / `inferActionType` / `buildEventMetadata`），作为跨站适配唯一入口
- **Collector kernel 拆分**：原 `content/collector.ts` 拆成 `content/kernel.ts`（平台无关的 click / scroll / hover / search / navigation / video 观察器）+ 每个平台一个 entry（`bilibili.ts` / `xiaohongshu.ts`），构建产物变成两份 content script bundle
- **Shared 拆解**：`shared/behavior.ts` 收窄为 DOM snapshot + `createBehaviorEvent` 内核；B 站专用逻辑（`extractBvid` / 卡片选择器 / 动作关键字）下沉到 `shared/platforms/bilibili.ts`，新增 `shared/platforms/xiaohongshu.ts`（`extractNoteId` 覆盖 `/explore/{id}` / `/discovery/item/{id}` / `/search_result/{id}` 三类 URL）
- **BehaviorEvent.source_platform**：TypeScript + Pydantic 两侧都加上 `source_platform` 字段；插件上报时由 kernel 自动填（`bilibili` / `xiaohongshu`），后端 `/api/events` 把它并入 `metadata`，空串 / 留白回退 `bilibili` 保证旧扩展版本兼容
- **Manifest + 构建**：`manifest.json` 新增 `*://*.xiaohongshu.com/*` host permission 和第二条 content_script 匹配；`scripts/build.mjs` 新增 xhs entry，`dist/content/{bilibili,xiaohongshu}.js` 一起产出
- **MVP 采集范围**：小红书侧先接 snapshot / click / scroll / search；`videoSelector = null` 的适配器直接跳过视频播放器观察
- **xhs 强信号补齐**：`inferXiaohongshuActionType` 沿用与 B 站共享的中文动作词（`点赞 / 收藏 / 评论`）+ 英文回退，命中后由 `STRONG_SIGNAL_TYPES` 触发即时上报；xhs 没有"投币"，coin 分支不做匹配
- **测试**：`extension/tests/collector-helpers.test.ts` 替换为双平台单测（bilibili + xhs adapter，覆盖 like / favorite / comment 正反例），`dist-module-specifiers.test.ts` 校验两份 bundle 无 ESM 残留；后端新增 `test_events_endpoint_preserves_source_platform` 验证 xhs 事件与回退行为。全量 87/87 extension 测试 + 752 passed backend

### 跨源画像融合：source_platform_mix

- **PreferenceLayer / OnionProfile 新增 `source_platform_mix: dict[str, float]`**：持久化记录各来源的行为占比（normalized 到 1.0），序列化 / 反序列化 / Onion↔Legacy 转换全部打通
- **PreferenceAnalyzer 自动计算**：`compute_source_platform_mix()` 从批次事件的 `metadata.source_platform` 按计数归一化；`_merge_source_mix()` 用 EMA（alpha=0.3）与历史画像融合，避免一次跨站浏览就抹掉长期 B 站记录；事件缺 `source_platform` 字段时回退 `bilibili`（老数据兼容）
- **LLM 上下文自动注入**：当 `len(source_platform_mix) > 1` 时，`SoulProfile.to_llm_context()` 和 `OnionProfile.to_llm_context()` 会追加 `## 来源分布` 小节（`bilibili 60% · xiaohongshu 40%` 风格），下游推荐 / 对话 prompts 即时知道用户是多源用户
- **暂不动 LLM prompt 内的画像抽取**：preference prompt 仍不区分来源，兴趣标签未按站点打标；等多源行为量堆起来再改 prompt，避免过早优化
- **测试**：`test_preference_analyzer.py` 新增 5 个用例（mix 计数 / 空事件 / EMA 融合 / 空批次保留 prior / analyze_events 端到端），`test_soul_profile.py` 新增 7 个用例（PreferenceLayer 往返、SoulProfile / OnionProfile 多源 context、单源不渲染）。全量 765 passed + 1 skipped backend

### Phase 7 双端端到端测试

- **后端 E2E**（`tests/test_phase7_e2e.py`）：真 SQLite `Database` + 真 `MemoryManager` + Pydantic `BehaviorEventBatchIn` 校验 + 真 `PreferenceAnalyzer`（仅 LLM 本身 stub）+ 真 `OnionProfile` 序列化往返，走完混合 bilibili + xhs 批次 → 事件入库 → 偏好抽取 → 画像落盘 → LLM context 渲染的整条链路，并用第二轮纯 bilibili 批次验证 EMA 融合能保留历史 xhs 占比（0.4 → 0.28）而非抹掉
- **扩展 E2E**（`extension/tests/phase7-e2e.test.ts`）：用真 `createBehaviorEvent` + 真 `xiaohongshuAdapter` / `bilibiliAdapter` + 真 `enqueueBufferedEvent` / `shouldFlushImmediately`，覆盖 xhs 点赞 → 强信号即时 flush、多源事件在 buffer 中共存不撞 dedupe、xhs 非动作点击不触发强信号三条路径
- 全量 766 passed + 1 skipped backend / 90 passed extension

### 多源内容适配：CDP 登录态 + URL 回填

- **多源架构落地**：`sources/` 新增 `SourceAdapter` 协议 + `SourceRecipe` 数据模型，`ContentDiscoveryEngine.register_adapter()` 让 B 站之外的内容源（小红书、知乎、V2EX 等）以同一接口挂载
- **BilibiliAdapter**：把四大 B 站策略（search / trending / related_chain / explore）包装成 adapter，推进"内容源"与"策略"的解耦
- **WebSourceAdapter / XiaohongshuAdapter**：通用浏览器 + LLM 抽取通道，默认走 CDP 连 Chrome；搜索结果页已真实 E2E 验证（10/10 笔记拿到 24 位 hex note ID + 可点击 URL）
- **BrowserManager 双后端**：
  - CDP 后端：Playwright `connect_over_cdp` 复用预启动的登录 Chrome，唯一能稳定抓小红书的路径
  - agent-browser 后端：匿名回退，兼容旧行为
- **PageSnapshot + 锚点回填**：一次 CDP 往返同时拿 `innerText` 和所有 `<a>` 的 `(text, href)`。`WebSourceAdapter` 按标题模糊匹配锚点，回填 `content_url`；从 URL 路径派生 `content_id`。解决了 `innerText` 丢弃 href 导致候选无法点击的问题
- **LLM 空值修复**：`llm_extractor.py` 之前把 LLM 返回的 JSON `null` 通过 `str(None)` 变成字符串 `"None"`，污染每个空字段的真值判断。改为 `str(x or "").strip()`
- **配置**：新增 `[sources.browser]` 段（`cdp_url` + `headed`），与 `[bilibili.browser]` 独立
- **可选依赖**：`playwright>=1.40` 进入 `[browser]` optional-dependencies group，`pip install 'openbiliclaw[browser]'` 按需安装
- **测试**：`tests/test_browser_manager.py`（7 个）+ `tests/test_web_adapter.py`（4 个，含 URL 回填）+ `tests/test_xhs_e2e.py`（`@pytest.mark.integration`，真 Chrome + 真小红书）。全量 751 passed

### B 站 API 空响应容错

- 修复 `_json_object()` 对 `None` 无防护的问题：B 站 `ranking/v2` / `web-interface/view` 等接口在限流或空分区 / 删档视频场景会返回 `"data": null`，导致下游 `None.get(...)` 抛 `AttributeError` / `KeyError`
- `_json_object()` 新增 `None → {}` 短路分支，与 `_json_list()` 的 `None → []` 对称，一次性覆盖 11 处调用点（ranking / comments / search WBI / favorites cursor / video info 等）
- `get_video_info()` 将硬下标 `payload["data"]` 改为 `.get("data")`，`"data": null` 时退化为字段全默认的 `VideoInfo` 而非崩溃
- Discovery 四大策略（trending / search / explore / related_chain）的异常日志从 `logger.exception(..., exc_info=outcome)` 改为 `logger.error(..., exc_info=outcome, extra=...)`，idiomatic 之外补上 `strategy` / `error_type` / query 等结构化字段，便于观测
- 新增 2 条回归用例（`test_get_ranking_returns_empty_list_when_data_is_null` / `test_get_video_info_returns_defaults_when_data_is_null`）

### 后端 Release 自动发包

- 新增 tag 驱动的 GitHub Actions release workflow：推送 `v*` tag 后会自动构建 macOS / Windows 后端桌面包
- 后端 release 产物现已统一上传到 GitHub Releases，和浏览器插件一样走“下载附件”分发路径
- 新增版本化后端归档命名规则，例如 `OpenBiliClaw-macos-v0.1.1.zip`、`OpenBiliClaw-windows-v0.1.1.zip`
- README / 文档导航已同步补充“从 Releases 下载后端”的入口说明
- 首版桌面后端包暂未签名，文档中已明确 macOS Gatekeeper / Windows SmartScreen 可能出现的安全提示

### 推荐引擎解耦重构

- **新增 `serve()` 统一入口** (`recommendation/engine.py`)，所有推荐路径 (generate / reshuffle / append) 合并为一个方法，通过 `expression_mode` 参数区分实时 LLM 和预缓存两种模式
- **废弃 `discovered` 直传路径**：`generate_recommendations()` 不再接受上游传入的候选列表，引擎始终从 content_cache pool 自主拣选，与 Discovery 完全解耦
- **新增 `PoolCurator`** (`recommendation/curator.py`)，推荐侧二次评分：`rec_score = 0.4×relevance + 0.2×freshness - 0.15×topic_fatigue - 0.15×source_monotony + 0.1×serendipity ± feedback`
  - `_freshness_score()`：sigmoid 衰减，半衰期 3 天
  - `_topic_fatigue()`：近 N 条推荐中同 topic 的频率惩罚
  - `_source_monotony()`：近 N 条推荐中同 source 的频率惩罚
  - `_serendipity_bonus()`：explore 来源加分
  - `FeedbackSignals`：dislike UP → -0.20, dislike topic → -0.10, like → +0.05
- **自动补货机制**：reshuffle / append 后检查 `needs_replenishment()`，池子低于 50 时自动触发 `trigger_manual_refresh()`
- **过期淘汰**：新增 `evict_stale_pool_items()`，14 天未消费的 fresh 内容标记为 stale，每次 refresh cycle 自动清理
- **DB 新增查询**：`get_recent_recommendation_signals()` 和 `get_feedback_signals()` 为 Curator 提供评分上下文
- 新增 24 个 PoolCurator 单元测试，全部 476 个测试通过

### Discovery 评估优化框架

- **新增 `DiscoveryEvaluator`** (`eval/discovery_evaluator.py`)，支持 7 维质量评估：relevance、diversity、specificity、query_quality、explanation_quality、novelty、no_echo_chamber
- **新增 `DISCOVERY_FIELD_TO_PARAM` 归因映射**，17 个评估维度归因到 5 个 prompt（`search_queries_prompt` / `trending_rids_prompt` / `content_evaluation_prompt` / `explore_domains_prompt` / `recommendation_expression_prompt`）
- **新增 `ScenarioGenerator` + `MockBilibiliClient`** (`eval/discovery_scenario.py`)，为每个 persona 离线生成模拟 B 站内容宇宙（60 条视频 + 搜索索引 + 排行榜 + 相关图 + 行为事件），MockBilibiliClient 满足策略的 3 个 Protocol 接口
- **新增 `create_discovery_optimizer()`** (`eval/discovery_optimizer.py`)，复用 `PromptOptimizer` 核心但注入 discovery 专属参数注册表和白名单
- **新增 `run_discovery_optimizer_agent()`** (`eval/agents.py`)，发现系统专用优化 agent，可自主读文件并提出 prompt diff
- **新增自动优化脚本** (`scripts/run_discovery_auto_optimize.py`)，SGD 风格循环：persona → scenario → discover → 7 维评估 → exploit/explore → accept/rollback
- **新增人工评估脚本** (`scripts/run_discovery_eval.py`)，交互式展示发现结果和中间产物，人工打分后可触发优化
- **SearchStrategy 统一走 LLM 评估**：新增 `llm_evaluation` 和 `score_threshold` 字段，默认开启 `evaluate_content()` LLM 打分，去掉了 0.62 硬上限
- **4 个策略新增 `last_intermediates`**：运行后暴露中间产物（搜索词/分区/种子/域），供评估系统独立评估决策质量
- **`PromptOptimizer` 参数化**：`__init__` 新增 `modifiable_files` 和 `field_to_param` 可选参数，soul 和 discovery 共享 apply/commit/rollback 机制
- 新增 39 个单元测试覆盖评估器打分函数、MockClient Protocol 兼容性、ScenarioPool 缓存

### 猜测兴趣系统 (Speculative Interest Lifecycle)

- **新增 `InterestSpeculator` 引擎** (`soul/speculator.py`)，实现猜测兴趣的完整生命周期：生成 → 观测 → 转正/拒绝 → 冷却
- **高频生成**：每 10 分钟检查一次，Init 和进程启动时通过 `force_tick()` 立即触发
- **兴趣上限保护**：一级兴趣（域数）上限 15、二级兴趣（细项数）上限 60，确认兴趣 + 活跃猜测达到上限时自动跳过生成
- **LLM 驱动的兴趣猜测**：基于心理学桥接推理生成 3-5 个新兴趣方向，排除冷却期方向
- **轻量级事件观测**：每次事件 ingest 时通过关键词匹配检查是否与猜测兴趣相关，无需 LLM 调用
- **自动转正**：猜测兴趣被 3 次以上事件确认后自动提升为正式兴趣（source="speculated", weight=0.3）
- **拒绝 + 冷却**：TTL（默认 3 天）到期未确认的猜测进入 7 天冷却期，期间不再猜测该方向
- **双来源种子**：`PreferenceAnalyzer` 每次偏好分析附带产出的 `speculative_interests` 现被保留并注入 speculator 作为种子
- **Pipeline 集成**：`ingest_batch()` 自动触发观测，`tick()` 自动处理过期/转正/生成
- **Discovery 集成**：`SoulEngine.get_profile()` 附加 `_active_speculations`，`build_profile_summary()` 自动包含猜测兴趣，所有策略 LLM prompt 可见
- **API 集成**：`GET /api/profile` 返回 `speculative_interests` 字段
- **7 项配置项**：`speculation_interval_minutes / ttl_days / cooldown_days / confirmation_threshold / max_active / max_primary_interests / max_secondary_interests`
- 新增 27 个单元测试覆盖观测匹配、转正、过期冷却、兴趣上限、force_tick、间隔单位等

### SoulProfile 五层洋葱模型重构

- **新增 OnionProfile 数据结构**，将平面 SoulProfile 重构为五层嵌套模型：
  - **Core Layer**: 最稳定的核心特质（core_traits）、深层需求（deep_needs）和 MBTI 人格类型及维度强度
  - **Values Layer**: 价值观（values）和内在驱动力（motivational_drivers）
  - **Interest Layer**: 树形兴趣结构（domain → specifics），支持"国际时事 → 中东局势 / 欧洲政治"的多层级组织；同时包含 dislikes 树和 favorite_up_users 列表
  - **Role Layer**: 生活阶段（life_stage）和当前处境（current_phase）
  - **Surface Layer**: 可观察的认知风格（cognitive_style）、内容偏好（style）、使用场景（context）和探索开放度（exploration_openness）
- **MBTI 人格类型**现已内置 Core 层，包含 4 个维度的极向选择和强度评分（0.0-1.0），便于更精准的个性化推荐
- **树形兴趣结构**提升了画像表达能力，from_legacy() 自动将 v1 flat interests 转换成领域树，支持兴趣聚合与精细化表述
- **双存储方案**：soul_profile.json 存储结构化 OnionProfile v2，soul_profile.md 镜像人类可读版本，soul_changelog.md 记录每次画像更新的时间戳、触发来源、变化摘要和影响范围
- **向后兼容垫片属性**：OnionProfile 暴露 core_traits / deep_needs / motivational_drivers / values / cognitive_style / life_stage / current_phase 等垫片属性，支持现有代码无修改地访问旧接口
- **自动格式迁移**：SoulEngine 和 ProfileBuilder 透明检测 v1/v2 格式，from_dict() 自动调用 from_legacy() 迁移，已初始化的画像无缝升级到五层结构
- **兴趣树可视化**：interest.likes 和 interest.dislikes 现支持完整的 domain / specifics / weight / source 链路，便于前端展示兴趣图谱和精细反馈

### OpenClaw Adapter 集成

- 新增 `src/openbiliclaw/integrations/openclaw/`，在不改动核心推荐与学习主链的前提下，为 OpenClaw 提供独立 adapter 层
- 新增 bootstrap、DTO、operation 和协议中立 skill descriptor，可对外暴露 `sync_account / get_profile / recommend / submit_feedback / get_runtime_status`
- 新增 `src/openbiliclaw/integrations/openclaw/cli.py` JSON CLI bridge，以及仓库级 `skills/openbiliclaw-adapter/SKILL.md`，按 OpenClaw skill 目录约定提供真实可发现技能
- CLI bridge 新增 `doctor` 与 `emit-skill-descriptors`，便于调试 OpenClaw skill pack 和导出当前 skill 定义
- OpenClaw `recommend` 现已默认走快路径，不再无条件触发 runtime refresh；如需显式刷新，可使用 `--refresh-if-needed`
- 显式 refresh 超时或失败时，OpenClaw adapter 现会自动回退到缓存推荐，避免交互入口长时间挂住
- 新增 adapter / skill 单元测试，并补充集成层文档、架构说明和导航入口
- 新增 `docs/openclaw-quickstart.md`，并在 `skills/openbiliclaw-adapter/SKILL.md` 中补充 Docker 优先 / 本地兜底的部署决策、首次 `openbiliclaw init` 和 `doctor` 自检指引，方便 OpenClaw 直接落地接入

### B 站搜索 412 降噪

- `BilibiliAPIClient.search()` 现在会先从 `nav` 获取 WBI key，并切到 `/x/web-interface/wbi/search/type` 发起签名搜索请求
- 搜索请求会附带搜索页 `Referer` 和 `Origin`，更贴近浏览器真实搜索链路
- 搜索接口返回 `412 Precondition Failed` 时，客户端会记录搜索受限 warning 并保守返回空结果，不再把单次 search 失败放大成整轮 discover traceback

### discovery 兴趣锚定收口

- `ExploreStrategy` 现在允许“核心兴趣的近邻扩展”，不再把包含高权重兴趣词的方向一律视作过度相似
- 跨域外推新增硬约束：至少优先保留 2 个锚定前 5 个高权重兴趣的方向，真正不直接提及核心兴趣词的远邻方向最多保留 1 个
- `SearchStrategy` 映射搜索结果时会对高权重兴趣命中给起始锚定分，把更贴近核心喜好的 search 候选从低分池里拉出来
- `ExploreStrategy` 对没有直接兴趣锚点的远邻方向新增轻量距离惩罚，避免这类内容在排序里压过更贴近用户喜好的候选

### 推荐换一批批量与补货余量调整

- popup 的 `/api/recommendations/reshuffle` 默认批量从 `5` 提到 `10`，单次“换一批”会尽量给够 10 条；池子不够时仍允许少于 10 条
- `RecommendationEngine.reshuffle_recommendations()` 的风格多样性回填逻辑已修正，不再因为前排候选都属于同一 `style_key` 就把整批数量卡到 2~4 条
- `scheduler.pool_target_count` 默认值从 `30` 提到 `150`，后台会为 popup 连续换一批保留更大的 discovery pool 余量
- 配置现已为 `scheduler.pool_target_count` 增加 `1..300` 的范围校验；运行时单轮 discover 补货请求也会封顶在 `60`

### popup 画像分组加厚与避雷项展示

- `/api/profile-summary` 现在会返回更厚一些的画像分组：`core_traits` 最多 `6` 条、`top_interests` 最多 `8` 条，并新增 `disliked_topics`
- popup「我的画像」页新增 `最近明显会避开` 分组，不再只能看到“喜欢什么”，也能看到稳定避雷方向
- 画像生成 prompt 里 `core_traits` 的建议上限也已从 `5` 放宽到 `6`，避免前端扩容后后端长期仍只吐固定 3~5 条

### popup 画像多层认知重构

- `SoulProfile` 新增 `cognitive_style / motivational_drivers / current_phase`，画像生成现在会同时消费 `history + preference + awareness + insights`
- `personality_portrait` 的 prompt 已改成优先总结“怎么处理信息 / 在内容里长期在找什么 / 最近处于什么阶段”，兴趣 topic 只允许作为少量证据出现
- `/api/profile-summary` 与 popup 画像 tab 已同步接入这三层新字段，不再只展示一段 prose 加兴趣 chips

### explore 外推方向多样性增强

- `build_explore_domains_prompt()` 现在会明确要求跨领域外推至少覆盖 3 类不同内容方向，避免全部落在同一个抽象轴上
- prompt 新增“同一母题换皮只能保留 1 个”的约束，用来压住 `博弈论 / 桌游机制 / 策略模型` 这类近义探索方向连续灌池
- `why_it_might_resonate` 现在被要求先回到用户的认知需求和信息处理偏好，再解释题材为什么可能打动他

### explore 单簇灌池与补货状态语义修正

- runtime refresh 现在会在补货后温和压一轮 `explore` 高风险子簇的过量 fresh 候选，优先处理制造 / 工艺 / 材料、博弈 / 桌游 / 机制这类容易连续刷屏的相邻方向
- discovery runtime state 新增 `last_discovered_count`，补货状态不再只用“可立即换库存净增”来表达本轮 refresh 的结果
- popup pool summary 现在会区分“正在补货”“这轮找到了内容但可换库存没变”“刚补进 N 条”，不再把 refresh 进行中和上一轮净新增为 0 混成同一句

### popup 推荐头部信息面板整理

- 推荐 tab 头部已从“标题 + 按钮 + 三行池子状态”改成单张轻量信息卡，主操作和状态层级更清楚
- 候选池摘要现在拆成 `当前可换 / 最近补进 / 现在在忙` 三块语义面板，不再像一段连续日志
- 点击 `换一批` 时，进行中的文案会直接进入“现在在忙”状态块，避免按钮旁边再漂一条独立提示导致布局抖动
- 推荐 tab 头部现已进一步收成紧凑双层结构：标题行 + 状态 chips 行，明显减少首屏占用，让推荐内容更早露出
- pool summary 文案同步收短成 chip 友好的形式，例如 `还有 151 条可换 / 刚补进 6 条 / 这会儿先不补货`

### popup For You 编辑式重排

- 推荐 tab 的 `For You` 区块进一步改成内容优先的编辑式布局，头部导语、池子摘要和首张内容卡的层级明显分开
- 推荐卡片改成更清晰的纵向信息节奏：上层是封面和主题标签，中层是标题与推荐理由，下层是 UP 主信息和反馈操作
- 视觉上收敛了过重的装饰层，首屏更像内容推荐流，而不是状态面板拼装

### discovery pool 预生成推荐文案

- discovery pool 现在会在内容入池后异步批量预生成 `expression` 和 `topic_label`，`reshuffle/append` 不再现场兜底生成整批统一文案
- popup 推荐卡片改成“有预生成文案就展示，没生成好就先隐藏”，不再把空值补成固定占位文案
- runtime refresh 在补货后会顺手触发这轮 pool copy 预生成，保证“换一批”继续保持秒级响应


### popup 推荐自动续页

- 新增 `POST /api/recommendations/append`，popup 推荐 tab 滚到底时会继续从 discovery pool 追加下一批 10 条
- 自动续页会把当前已展示的 `bvid` 传给后端排除，避免追加时和当前列表重复
- `换一批` 仍保留为整组重开；自动续页只负责在当前列表底部继续往下接内容
- 修复了续页新卡片封面偶发空白的问题：popup API 现在会统一规范化 `cover_url`，同时封面不再依赖会误伤内部滚动容器的原生 lazy loading

### SQLite 修复与防损坏加固

- 新增 `openbiliclaw db-repair`，会先检查完整性、拒绝带占用修复、备份 `db/db-wal`，再尝试恢复到 repaired 副本并切换正式库
- `openbiliclaw start` 现在会在启动前检查数据库健康度；检测到损坏时会直接阻止启动，并提示先执行 `db-repair`
- 运行时增加默认 24 小时冷备份策略，自动把健康数据库备份到 `data/backups/`，并按“最近 7 份日备 + 4 份周备”轮转
- `Database` 的推荐更新写路径现已统一走带锁重试的写入口，减少 `database is locked` 后局部裸写带来的风险
- CLI / API 的高流量路径开始共享同一个 SQLite 实例，避免同进程重复初始化多份连接

### Docker 一键后端部署支持

- 新增 `Dockerfile`、`.dockerignore` 和单服务 `docker-compose.yml`，支持 `docker compose up -d` 启动后端
- CLI `start` 现在支持 `--host` / `--port`，同时新增 `serve-api` 作为容器友好的显式启动入口
- 默认 compose 现已改为 Docker named volumes，配置、数据、日志都与宿主机项目目录隔离
- 修复安装包运行时的根目录解析问题，容器内现在会正确读取 `/app/runtime/config.toml` 并把数据写入 `/app/runtime/data`
- 容器启动时现在会自动探测宿主机 Clash HTTP 代理；默认探测 `host.docker.internal:7897`，可达则透传代理，不可达则继续直连
- `openbiliclaw init` 现在支持交互式引导：Docker 用户首次执行时可直接补齐默认 provider、API Key 和 B 站 Cookie，然后继续完成初始化
- 容器内通过 `docker exec openbiliclaw ...` 执行任意 CLI 命令时，也会重复这层 runtime/bootstrap 逻辑，避免只有主进程有代理、交互命令却直连失败
- discovery 内部已经改为保守受控并发：Search / Trending / Related / Explore 会共享较小的 B 站请求与 LLM 评分并发上限，减少首轮 init/discover 的明显串行耗时
- `openbiliclaw init` 的 discover 阶段现在会按 `search + related_chain -> trending -> explore` 分阶段补货，尽量把首轮 fresh 候选池补到至少 `100` 条，降低第一次 `recommend` 直接空池子的概率
- `openbiliclaw init` 运行时会同步打印每个补货阶段的当前池子进度和本轮请求上限，首轮等待时不再只有一个静态“发现内容”标题
- 修复 `DiscoveryConcurrencyController` 在多次 `asyncio.run(...)` 间复用 semaphore 的跨事件循环问题，Docker/CLI 首轮分阶段补货不再在第二阶段报 `Semaphore ... is bound to a different event loop`

### discovery pool 目标扩容

- `scheduler.pool_target_count` 默认值现已从 `150` 提到 `300`，运行时会持续以 300 条 fresh 候选为目标补货
- `openbiliclaw init` 的首轮补货目标保持保守分层策略，但保底值已从 `50` 提到 `100`
- 现有护栏保持不变：`pool_target_count` 仍限制在 `1..300`，单轮 refresh discover 回填仍封顶 `60`

### 同批推荐多样性约束

- `generate_recommendations()` 和 `reshuffle_recommendations()` 现在不会只按分数直取前 N
- 同一批里会对重复 `tags/topic` 做软限流，尽量避免连续出现太多同一方向的内容
- 候选不足时仍会回填高分内容，保证多样性约束不会把推荐数量卡没

### topic_key 多样性强化

- `content_cache` 现在会持久化稳定 `topic_key`，推荐层不再只靠空 `tags` 猜 topic
- `SearchStrategy` 会把 query 派生的 `topic_key` 写入候选，`RelatedChainStrategy` 会把 seed chain 继承成 `topic_key`
- `generate_recommendations()` 和 `reshuffle_recommendations()` 现在优先按 `topic_key` 分桶，每个 topic 先出 1 条，再按分数回填
- `ContentDiscoveryEngine` 在写入 discovery pool 前会先压一轮同 topic 重复项，减少单一相关推荐链把池子灌满的情况

### 风格多样性与快速文案增强

- discovery 入池时会按标题、描述和基础理由轻规则补 `style_key`，区分 `deep_dive / news_brief / game_strategy / practical_guide / story_doc / visual_showcase / light_chat`
- `reshuffle_recommendations()` 现在会同时约束 `topic_key + style_key`，避免一批里虽然 topic 不同，但全是同一种“很干很学术”的内容风格
- 快速换一批的 fallback 文案不再直接裸用 `relevance_reason`，而会按 `style_key` 生成更自然的老B友短句

### 候选窗口来源交错与 10 条批次硬上限

- `get_pool_candidates()` 现在会对 discovery pool 做来源交错取样，优先把 `search / trending / related_chain / explore` 混进同一候选窗口，而不是先吐出一屏 `explore`
- `reshuffle_recommendations()` 现在会同时对 `topic_key + style_key + source` 加硬上限；10 条一批时单一来源最多 3 条，小批次也会优先保留不同来源，减少“换一批还是同一个味”的情况

### 来源优先补齐与风格误判修正

- discovery 与 recommendation 的多样性选择现在会优先补齐不同 `source`，再施加 `style` 上限，避免 `trending/search` 还没出场就被重复的 `explore` 候选挤掉
- `infer_style_key()` 补强了芯片/显微镜/纳米/理论/哲学等硬核解析词，以及“全过程 / 制造过程 / 工艺难度”等纪录片/工业流程词，减少大量硬内容被误判成 `light_chat`
- 推荐候选与选中摘要日志现在更容易对应“来源是否真的被补齐”，便于继续定位池子上游偏移问题

### 候选池按来源缺口补货

- runtime refresh 在池子低于 `pool_target_count` 时，不再一视同仁地把所有策略各跑一轮，而是会先统计 `search / related_chain / trending / explore` 当前池子占比
- 补货现在会优先补足缺口更大的来源；例如 `trending` 为 0、`explore` 已经超标时，会先补 `search/related` 和 `trending`，而不会继续加码 `explore`
- `database` 新增按来源统计 fresh pool 的能力，候选池状态现在不仅看总量，也看来源结构是否失衡

### 池子已满时的状态文案修正

- popup 候选池摘要现在会在 `pool_available_count >= pool_target_count` 且最近没有新增入池时，显示“这会儿先不补货，池子里已经够你换了”
- 不再用“刚补进 0 条新的”误导用户以为后端没在工作

### popup 动态状态卡与活动历史

- popup 底部提示区现在升级为两行可展开动态卡，默认显示“现在在忙什么 / 最近一次关键变化”
- 新增 `/api/activity-feed`，聚合认知更新、反馈记录、换一批和候选池补货等最近活动
- 点 `更多` 后会展开最近历史，不再只能看单条瞬时提示

### 画像认知卡片历史分页

- `/api/profile-summary` 现在会返回结构化认知卡片分页结果，新增 `has_more_cognition_updates / next_cognition_cursor`，popup 可继续拉取更早的认知变化
- popup「阿B 最近新记住了什么」升级为可展开卡片：默认看一句总结，展开后能看到“这对画像的影响 / 为什么这么判断 / 这次依据”
- 评论型认知卡片现在会带上对应内容标题，避免只看到“这个很好看”却不知道是在评价哪条内容
- 画像 tab 首屏先展示 3 条认知变化，并支持滚动自动续页；底部保留“加载更多 / 重试加载”按钮作为兜底

### 认知卡片上下文与展开状态澄清

- 认知卡片默认态现在固定显示“结论 + 上下文 + 状态提示”，例如 `来自：《某条内容》`、`来自最近这轮聊天：…`、`基于最近主题：…`
- `/api/profile-summary` 新增 `context_line / source_label / expand_hint`，前端不再把 `画像观察` 这类泛标签当作默认上下文
- popup 会显式区分 `展开 / 收起 / 仅结论`，不可展开卡片不再做成像按钮的样子；聚合判断拿不到可信对象时会保守回退为“基于最近几条相关内容”

### 推荐评论发送状态可见化

- 推荐卡片里的 `说说原因 -> 发出去` 现在会立刻切到 `发送中...`，成功后显示 `已发出` 并回写本地状态文案
- 请求失败时按钮会恢复可点，卡片本地会直接提示“这句还没发出去，可以再试一次”，不再只能靠底部横条猜测

### 账户侧定时同步 — `runtime/m115-account-sync`

- 本地后端运行时新增低频账户同步链路，会定期拉取 `history / favorites / following`
- 新数据会统一转成 `view / favorite / follow` 事件，再复用 `SoulEngine.analyze_events()` 更新偏好与画像
- 新增 `account_sync_state.json` 保存历史游标、收藏/关注签名和最近同步错误
- `runtime-status` 新增 `last_account_sync_at` / `last_account_sync_error`，便于 popup 或诊断页展示账户同步状态

### 聊天即时认知阈值放宽 — `runtime/m114-chat-cognition-threshold`

- popup/CLI 聊天现在对 `interest / value / goal / dislike` 这类单条中高置信信号更敏感，会更早进入「阿B 最近新记住了什么」
- 偏好重分析和画像重建仍保留原有重复出现/累计阈值，不会因为一句随口聊天就改动长期画像

### 单条强聊天即时认知更新 — `runtime/m113-immediate-chat-cognition`

- 单条高置信度聊天信号现在也可即时写入轻量 cognition update，供 popup「阿B 最近新记住了什么」优先展示
- 大规模偏好重分析和画像重建仍保留原有候选累计阈值，不会因为一次聊天就重写整张画像

### popup 画像摘要即时刷新

- side panel 在聊天、`多来点`、`少来点`、`说说原因` 成功后，会强制重拉 `/api/profile-summary`
- 修复“阿B 最近新记住了什么”只在首次打开画像 tab 时加载，之后不跟着新反馈/新聊天更新的问题

### 强反馈即时认知更新 — `runtime/m112-immediate-cognition-feedback`

- 单条 `dislike` / `comment` 反馈现在会即时写入轻量 cognition update，供 popup「阿B 最近新记住了什么」立刻展示
- 偏好重分析和画像重建仍保持现有 `>= 3` 条反馈阈值，不会因为一次反馈就重写整张画像

### 运行时实时状态流 — `runtime/m111-runtime-stream`

- 新增 `/api/runtime-stream` websocket，popup 打开期间可持续接收后端运行阶段事件
- 刷新器现在会广播“开始补候选 / 当前策略 / 刚补进几条新的 / 这批先换好了 / 补货失败”等状态
- popup 底部提示横条和池子摘要会随着事件流即时更新，不再只显示静态数字

### Popup 底部提示增强 — `extension/m110-hint-banner`

- popup 底部提示区从淡灰说明文案升级为带状态点的横条提示，成功 / 提示 / 错误三种状态现在更容易区分
- `喜欢 / 不喜欢 / 写一句 / 换一批 / 聊天发送` 等关键动作都会同步切换提示语气，减少“操作成功了但不明显”的问题

### 候选池容量与状态展示 — `runtime/m107-pool-status-capacity`

- `scheduler.pool_target_count` 现在可以控制 discovery pool 期望保有的可换候选数量，后台刷新器会持续补货直到池子接近目标
- `runtime-status` 新增 `pool_available_count`、`pool_target_count`、`last_replenished_count`、`recent_pool_topics`
- popup 推荐 tab 会展示“当前池子里还有多少条可换 / 刚补进多少条新的 / 最近主要在补什么”
- discovery pool 查询现在会排除已经进入 `recommendations` 的内容，减少“换一批还是老面孔”的情况

### 推荐卡片封面展示 — `extension/m108-cover-cards`

- `/api/recommendations` 与 `/api/recommendations/reshuffle` 现在都会返回 `cover_url`
- popup 推荐卡片升级为“封面 + 文本信息 + 操作区”结构，换一批时可以直接先看封面再决定点不点
- 封面缺失或加载失败时会回退到占位态，不影响换一批、打开视频和反馈流程

### 封面地址规范化修复 — `extension/m109-cover-normalization`

- popup 现在会把 `//i*.hdslb.com/...` 和 `http://i*.hdslb.com/...` 统一规范成 `https://...`
- 修复了部分推荐卡片因为协议相对地址或不安全地址导致封面加载失败的问题

### 插件侧边栏模式 — `extension-sidepanel`

- 扩展入口从 `action.default_popup` 切到 `side_panel.default_path`，点击扩展图标时会优先打开侧边栏
- service worker 新增统一的扩展 UI 打开链，通知和认知提醒也会优先把用户带回插件侧边栏上下文
- 现有 `popup/` 页面继续复用，但布局已从固定小弹窗改成更适合侧边栏浏览的长页面容器

### 候选池即时换一批 — `runtime/m106-pool-reshuffle`

- popup 推荐 tab 现已从“立即刷新完整补货”改成“换一批”，直接调用 `/api/recommendations/reshuffle`
- `content_cache` 现在作为真正的 discovery pool 使用，候选项新增 `pool_status`、`recommended_at`、`feedback_type`、`feedback_at`
- `RecommendationEngine.reshuffle_recommendations()` 会直接从池子里拣一批 `fresh` 候选，不等待完整 discover 完成
- popup 展示文案会优先使用候选池自带的 `relevance_reason`，朋友式 `expression` 成为增强层，不再阻塞即时换片

### Popup 手动刷新推荐 — `extension/m86-manual-refresh`

- popup 推荐 tab 新增“立即刷新”按钮，点击后会调用 `/api/recommendations/refresh` 触发一次完整补货
- 刷新期间按钮会进入“正在补货…”状态，成功后立即重拉运行状态和推荐列表
- 刷新失败时保留当前推荐，不清空内容，只给出轻量错误提示
- 后续修正：手动刷新现在走 `force_refresh()`，不会再因为 `below_threshold` 被短路

### 候选供给升级 — `candidate-supply`

- `ContentDiscoveryEngine` 现在采用“主发现 + backfill”两阶段流程：主候选不足时会扩搜索、放宽高精度策略阈值，并从历史缓存补齐到目标上限
- `content_cache` 新增 `relevance_score`、`relevance_reason`、`candidate_tier`，缓存候选与实时发现候选终于共享同一套质量信号
- `RecommendationEngine` 和 `Database.get_unrecommended_content()` 现已统一按 `candidate_tier -> relevance_score -> last_scored_at -> view_count` 排序，避免缓存回读退化成只看播放量

### Popup 手动刷新异步化 — `runtime/m105-manual-refresh-async`

- `/api/recommendations/refresh` 现在只负责触发后台手动补货任务，立即返回接受结果
- `runtime-status` 新增 `manual_refresh_state` 和 `manual_refresh_message`，popup 会轮询后台状态，而不是同步等待整轮补货
- 手动刷新期间 popup 继续保留当前推荐列表，等后台补货完成后再统一重拉推荐

### Gemini 可选依赖导入修复 — `fix/gemini-optional-import`

- `google-genai` 缺失时，`openbiliclaw.llm` 和 `openbiliclaw.llm.registry` 现在仍可正常导入，不再因为 Gemini 顶层依赖阻塞整个测试收集
- 只有真正实例化 `GeminiProvider` 时才会抛出明确错误，提示安装 `google-genai`
- Gemini 功能测试改为“有 SDK 才跑功能，无 SDK 则验证友好降级”，恢复主线测试可运行性

### 关键认知变化提醒 — `runtime/m104-cognition-notify`

- 新增 `cognition_updates.json`，记录关键认知变化、来源、置信度和已通知状态
- 反馈刷新与聊天学习链路现在会生成 `interest_added`、`dislike_added`、`profile_shift` 三类认知变化
- 新增 `/api/cognition-updates/pending` 与 `/api/cognition-updates/seen`，供插件拉取并确认认知提醒
- service worker 现在会在推荐通知之后检查认知变化通知；popup “我的画像” tab 会展示“阿B 最近新记住了什么”

### 持续候选池刷新与通知 — `runtime/m103-continuous-refresh-notify`

- 新增 `ContinuousRefreshController`，在本地 API 运行时按“事件触发 + 定时保底”持续刷新候选池，并分层调度 Search/Related、Trending、Explore 策略
- 新增 `discovery_runtime.json`，持久化最近刷新时间、最近处理事件 ID 和最近通知时间
- `content_cache` 新增 `last_scored_at`、`notification_sent`、`notified_at`，用于候选保鲜和通知去重
- 新增 `/api/runtime-status` 与 `/api/notifications/pending`、`/api/notifications/sent`，popup 和 service worker 可分别读取运行状态、拉取待发通知并确认送达
- popup 现在会区分“未初始化 / 正在补货 / 推荐可用”三态，service worker 会对高置信且未通知的推荐触发浏览器通知并回写已发送状态

### Gemini Provider 支持 — `gemini-provider`

- 新增 `GeminiProvider`，按 Gemini 官方 quickstart 接入 `google-genai` SDK，支持统一的空响应校验、错误归一化和 usage 标准化
- 配置层新增 `[llm.gemini]`，支持 `api_key` 与 `model`，默认模型为 `gemini-2.5-flash`
- `LLMRegistry` 现在可以自动注册 `gemini`，并在 `config.toml` 缺 key 时回退读取 `GOOGLE_API_KEY` / `GEMINI_API_KEY`
### B站动态语气优化 — `tone/m94-bilibili-tone`

- 新增 `ToneProfile` 派生层，从画像、偏好摘要和近期反馈推断 `density / warmth / playfulness / directness`
- 推荐表达、画像总结和聊天 prompt 统一接入这层语气系统，基础风格改为“老B友”，但会随用户理解逐步细调
- 推荐理由减少算法解释腔，画像减少心理报告感，聊天保留追问能力但更像懂 B 站语境的老朋友

### OpenRouter Provider 支持 — `llm/openrouter-provider`

- 新增 `OpenRouterProvider`，通过 OpenAI-compatible 调用链接入统一的超时、重试、错误归一化和 JSON mode
- 配置层新增 `[llm.openrouter]`，支持 `api_key`、`model`、`base_url` 以及可选请求头 `http_referer` / `x_title`
- `LLMRegistry` 现在可以自动注册 `openrouter`，并支持把它设为默认 provider

### Popup UI 刷新 — `extension/popup-ui-refresh`

- popup 从深色工具面板重构为亮色三 tab 发现页，顶部采用 hero + inline 状态徽标，整体更贴近 B 站内容产品气质
- 推荐卡片、画像卡和聊天区统一为同一套浅色卡片系统，推荐内容成为 popup 首屏的主要视觉焦点
- 保持现有推荐、反馈、画像、聊天逻辑不变，仅刷新结构、层级与交互反馈；extension 测试、typecheck 和 build 均已通过

### 9.3 聊天学习链路 — `soul/m93-chat-learning`

- 聊天现在会落 `dialogue` 事件，并额外提取 `interest / dislike / goal / value / state` 类型的候选长期理解信号
- 新增 `insight_candidates.json` 作为中间状态，先累计聊天候选，再由阈值控制是否进入偏好层
- 只有高置信度且重复出现的聊天候选才会驱动偏好重分析，并在变化明显时重建画像
- CLI `chat` 与 popup “和阿B聊聊” 现在共用这条学习链，但仍保持受控更新，不会因为单轮对话立即改写画像

### 运行时 Cookie 回退修复 — `main`

- 修复 `auth login` 与运行时命令脱节的问题：`init`、浏览器集成和本地服务现在会优先使用显式配置 cookie，留空时自动回退到 `data/bilibili_cookie.json`
- 用户完成一次 `auth login` 后，不再需要把同一份 cookie 重复抄进 `config.toml`
- 新增认证测试，锁定显式 cookie 优先级和已保存 cookie 回退行为

### Popup 画像 / 聊天页签增强 — `extension/m84-popup-tabs`

- popup 新增 `推荐 / 我的画像 / 和阿B聊聊` 三个 tab，推荐不再是唯一入口
- 新增 `/api/profile-summary` 和 `/api/chat`，popup 可直接查看轻量画像摘要并发起对话
- 推荐卡片交互已收口为显式打开视频，不再因为 `喜欢 / 不喜欢 / 写一句` 或输入框点击误跳转
- popup 内的推荐反馈、画像查看和聊天现在共用同一套本地后端连接状态

### 9.2 画像更新 — `feedback/m92-profile-refresh`

- 新增 `feedback_state.json`，记录反馈重分析处理游标和最近一次处理时间
- 反馈累计达到阈值后，会自动触发偏好层重新分析
- 当高权重兴趣或不喜欢主题变化明显时，会自动重建并持久化 `soul.json`
- CLI `feedback` 与 API `/api/feedback` 在反馈成功后都会同步触发这条更新链

### 9.1 反馈处理 — `feedback/m91-processing`

- CLI `feedback` 命令扩展为支持 `like / dislike / comment`，其中 `comment` 必须带 `--note`
- 新增 `POST /api/feedback`，统一校验推荐存在性、更新反馈字段并追加 `feedback` 事件
- popup 的 `喜欢 / 不喜欢 / 写一句` 已接通真实后端，提交后会立即写回推荐记录
- `9.1` 的反馈写入链路现已在 CLI、API、popup 三端统一

### 8.3 Popup — `extension/m83-popup`

- popup 从占位页升级为真实面板：显示后端连接状态和最新推荐列表
- 新增 popup helper，统一处理推荐字段 fallback、popup 状态判断和 B 站视频 URL 构造
- 点击推荐卡片或“打开视频”按钮会直接跳转到对应 B 站视频页
- `喜欢 / 不喜欢` 按钮本轮先保留 UI 占位，后端反馈写回留给后续任务

### 8.1 行为采集 — `extension/m81-behavior-collection`

- `collector.ts` 从最小 click/search 采集升级为多行为采集：点击、搜索、页面快照、视频 `view/pause/seek`、hover、scroll，以及评论/点赞/投币/收藏意图事件
- 补齐 SPA 导航感知：包装 `history.pushState` / `replaceState` 并监听 `popstate`，在 URL 变化时重新发送 `snapshot` 并重绑页面监听
- 新增纯逻辑 helper 和 Node 内置测试，覆盖页面识别、BV 提取、动作识别、缓冲去重与强信号 flush 判断
- `service-worker.ts` 改为带去重和失败回填的缓冲发送器，并使用 `chrome.alarms` 代替脆弱的 `setInterval`
- 新增 `extension/package.json`，提供 `npm test`、`npm run typecheck`、`npm run build`，让插件侧具备最小可验证构建链路
- 联调修复：补齐 manifest 图标资源，并把运行时脚本改为 `esbuild` bundle 单文件，解决 Chrome content script / service worker 的真实加载失败

### 8.2 后端 API — `api/m82-backend-api`

- 新增 FastAPI 应用，提供 `GET /api/health`、`POST /api/events`、`GET /api/recommendations`
- 插件上报的行为事件会映射到记忆系统事件层，并写入 SQLite `events` 表
- 推荐接口会返回推荐 ID、BV 号、标题、UP 主、推荐文案与展示状态，供插件 popup 使用
- CLI `openbiliclaw start` 从 stub 升级为真实本地 API 服务启动入口，默认监听 `127.0.0.1:8420`
- 联调修复：API 现已支持 extension 预检请求（CORS），并把 `/api/events` 改为 async 处理，避免 SQLite 线程错误

## M5: 内容发现引擎（进行中）

## M7: CLI 体验 ✅

### 7.1 chat 命令补平 — `cli/m71-chat-command`

- `openbiliclaw chat` 从 stub 升级为交互式 REPL，对接 `SocraticDialogue`
- 支持多轮对话，输入 `exit` / `quit` / 空行即可正常结束
- 新增 CLI 测试，覆盖画像缺失、单轮回复和退出路径

### 7.1 discover 命令补平 — `cli/m71-discover-command`

- `openbiliclaw discover` 从 stub 升级为真实命令：读取画像、执行 discovery engine、展示发现摘要与前 5 条预览
- 发现结果继续由 `ContentDiscoveryEngine` 写入 `content_cache`，CLI 只负责编排和展示
- 新增 CLI 测试，覆盖画像缺失、空发现结果和成功预览三条主路径

### 7.2 输出格式 — `cli/m72-output-format`

- `cli.py` 抽出统一 Rich 渲染 helper：页面标题、状态面板、键值表、占位态、推荐卡片
- `init` / `profile` / `recommend` / `feedback` / `config-show` / `auth status` / `health-check` / `browser` 命令全部切到统一展示风格
- `start` / `discover` / `chat` 的 stub 输出统一成“开发中”占位态，并附下一步提示
- CLI 测试补充输出结构断言，覆盖画像分区、推荐卡片、初始化摘要和状态面板语义

### 5.6 发现引擎编排 — `discovery/m56-engine-orchestration`

- `ContentDiscoveryEngine.discover()` 改为并发执行多个 discovery strategy，单个策略失败不会中断整体发现周期
- 引擎层对重复 `bvid` 进行合并，保留更高 `relevance_score` 的版本
- 新增 `Database.get_cached_content()`，并在发现完成后把最终结果写入 `content_cache`
- `evaluate_content()` 状态同步收口到 `5.5`：已被 Search / Trending / RelatedChain / Explore 复用
- 新增 discovery/storage 测试，覆盖并发编排、失败容错、高分去重和缓存写入读回

### 5.4 跨领域探索策略 — `discovery/m54-explore-strategy`

- `ExploreStrategy` 从空壳升级为可运行策略：先生成“高相关但有陌生感”的探索领域，再调用 B 站搜索
- 新增结构化 exploration prompt，要求输出 `domain` / `why_it_might_resonate` / `novelty_level` / `queries`
- 本地过滤与现有高权重兴趣过近的领域，避免“换皮搜索”
- 搜索候选统一复用 `ContentDiscoveryEngine.evaluate_content()`，并叠加基于 `novelty_level` 与 `exploration_openness` 的 exploration bonus
- 新增 explore 测试，覆盖领域过滤、bonus、生效阈值、部分失败容错和 engine 注册运行

### 5.3 相关推荐链策略 — `discovery/m53-related-chain`

- `RelatedChainStrategy` 从空壳升级为可运行策略：优先从事件层中的 `view` / `favorite` / `like` 视频挑选种子
- 种子不足时，先用偏好标签和常看 UP 主做小范围搜索补种子，再回退到 Search/Trending 的高分结果
- 对每个种子调用 `get_related_videos()`，沿相关推荐链最多扩展 2 层，并全局按 `bvid` 去重
- 统一复用 `ContentDiscoveryEngine.evaluate_content()` 对相关推荐候选打分，并按阈值过滤
- 新增 related-chain 测试，覆盖事件种子优先、fallback、二层扩展、去重、失败容错和 engine 注册运行

### 5.2 排行榜策略 — `discovery/m52-trending-strategy`

- `TrendingStrategy` 从空壳升级为可运行策略：拉取全站榜 `rid=0` 和相关分区榜，并按 `bvid` 去重
- 新增结构化分区选择 prompt，统一通过 `LLMService.complete_structured_task()` 选择额外 `rid`
- `ContentDiscoveryEngine.evaluate_content()` 现已实现：用 LLM 输出 `score/reason` 并写回 `DiscoveredContent`
- `TrendingStrategy` 对每条榜单内容执行相关性评估，只保留高于阈值的结果
- 新增 discovery 层测试，覆盖分区选择、阈值过滤、单榜单失败不中断和内容评估写回

### 5.1 搜索策略 — `discovery/m51-search-strategy`

- `SearchStrategy` 从空壳升级为可运行策略：基于画像生成搜索词、调用 B 站搜索并返回 `DiscoveredContent`
- 新增结构化搜索 query prompt，统一通过 `LLMService.complete_structured_task()` 生成 5 到 10 个 B 站搜索词
- 增加本地 fallback query 生成：当 LLM 返回坏 JSON 或空结果时，从兴趣标签和核心特质回退
- 对跨 query 搜索结果按 `bvid` 去重，并映射 `title` / `up_name` / `cover_url` / `duration` / `view_count` / `description`
- 新增 discovery 层测试，覆盖 query 生成、fallback、单 query 失败不中断和 engine 注册运行

## M4: 记忆系统（进行中）

### 4.5 核心记忆加载 — `memory/m45-core-memory`

- `MemoryManager.get_core_memory()` 从原始层数据改为稳定裁剪摘要，统一输出 `soul_summary` / `preference_summary` / `recent_awareness` / `active_insights`
- `MemoryManager.render_core_memory_prompt()` 改为固定区块渲染：用户画像、偏好摘要、近期观察、当前洞察
- `LLMService` 新增 `complete_with_core_memory()` / `complete_structured_task()`，统一自动注入 core memory
- `ProfileBuilder`、`PreferenceAnalyzer`、`AwarenessAnalyzer`、`InsightAnalyzer` 运行时全部改走统一 service 注入路径
- `SoulEngine` 现在内置 `LLMService`，保证画像、偏好、觉察、洞察链路都能共享同一份核心记忆上下文
- 后续收口修复已移除上述 4 个模块对原始 `registry.complete(..., json_mode=True)` 的 fallback，core memory 注入现在是强约束而非默认路径

### 4.4 觉察层与洞察层 — `memory/m44-awareness-insight`

- 新增 `AwarenessAnalyzer`：近期事件 -> `AwarenessNote`，支持坏 JSON 保护和同日去重
- 新增 `InsightAnalyzer`：觉察 + 偏好 + 画像 -> `InsightHypothesis`，支持假设合并与证据去重
- `SoulEngine.generate_awareness_note()` / `generate_insight()` 对接 analyzer，并持久化到 `awareness.json` / `insight.json`
- `SoulEngine.update_from_feedback()` 现在会写入 `feedback` 事件，并更新匹配洞察的 `validated` / `confidence`

### 4.3 灵魂层 — `memory/m43-soul-layer`

- 新增 `ProfileBuilder`：结构化画像 prompt、JSON 校验和 `SoulProfile` 构建
- `SoulEngine.build_initial_profile()` 从 history + preference 生成初始画像并持久化到 `data/memory/soul.json`
- `SoulEngine.get_profile()` 支持读取已保存画像，未初始化时抛 `SoulProfileNotInitializedError`
- `SoulProfile` 增加 `to_dict()` / `from_dict()` 及偏好层序列化辅助
- CLI `profile` 命令从 stub 升级为真实展示，缺失画像时提示后续执行 `openbiliclaw init`

### 4.2 偏好层 — `memory/m42-preference-layer`

- 新增 `PreferenceAnalyzer`：LLM structured extraction + JSON 解析 + 兴趣合并
- 新增 `build_preference_analysis_prompt()`：结构化偏好提取 prompt
- `SoulEngine.analyze_events()` 对接 `PreferenceAnalyzer`，偏好持久化到 JSON
- 兴趣标签带时间衰减（`decay_factor_per_week=0.9`）和最低权重过滤

### 4.1 事件层 — `memory/m41-event-layer`

- `Database` 新增 `query_events()` 和 `count_events_by_type()` 
- `MemoryManager.propagate_event()` 从 stub 改为 SQLite 持久化
- 事件类型枚举：`view`, `search`, `favorite`, `like`, `comment`, `click`, `feedback`
- 新增 `MemoryManager.query_events()` 和 `get_event_stats()` 委托方法

---

## M6: 推荐引擎（进行中）

### 6.3 推荐持久化 — `recommendation/m63-persistence`

- `recommendations` 表补齐结构化反馈字段：`feedback_type`、`feedback_note`、`feedback_at`
- 新增 `Database.get_recommendation_by_id()` 和 `update_recommendation_feedback()`，支持推荐反馈读写
- `RecommendationEngine` 新增 `record_feedback()` / `get_recommendation()` 入口
- CLI 新增 `feedback <id> <like|dislike> [--note ...]`，成功后会同步写入一条 `feedback` 事件
- 新增 recommendation/storage/cli 测试，覆盖反馈持久化、事件写入和不存在推荐的错误路径

## M7: CLI 交付（进行中）

### 7.1 核心命令 `init` — `cli/m71-init`

- 新增 `openbiliclaw init`，打通首次运行链路：认证检查、历史拉取、事件导入、偏好分析、画像生成、自动 discover
- 新增 `_build_bilibili_client()`、`_build_discovery_engine()` 和 `_history_item_to_event()`，把 CLI 编排边界固定下来
- `init` 支持阶段性进度输出，并在 discover 失败时给出“部分完成”提示，不丢弃已生成的画像
- 新增 CLI 测试，覆盖认证失败、历史为空、全流程成功和 discover 部分失败

### 6.2 朋友式推荐表达 — `recommendation/m62-expression`

- `RecommendationEngine.generate_expression()` 从 stub 升级为结构化 LLM 调用，输出 `expression` 和 `topic_label`
- `generate_recommendations()` 现在会为每条推荐补全朋友式文案，并回写到 `recommendations` 表
- 新增 `Database.update_recommendation_content()` 和 `mark_recommendations_presented()`，打通推荐文案更新与展示状态更新
- CLI `recommend` 从 stub 升级为真实展示入口，会读取用户画像、生成推荐并在输出后标记已展示
- 新增 recommendation/storage/cli 测试，覆盖文案生成、推荐历史回写和展示后状态更新

### 6.1 推荐排序 — `recommendation/m61-ranking`

- `RecommendationEngine.generate_recommendations()` 从 stub 升级为可运行排序入口
- 支持两种来源：显式传入 `discovered`，或直接从 `content_cache` 读取未推荐内容
- 新增 `Database.get_unrecommended_content()`、`insert_recommendation()`、`get_recommendations()`
- 每次生成推荐后，立即写入最小推荐历史记录，避免下一批重复选中同一内容
- 新增 recommendation/storage 测试，覆盖排序、缓存读取和去重闭环

## M3: Bilibili 接入层 ✅

### 3.3 agent-browser 集成 — `bili/m33-agent-browser`

- `BilibiliBrowser` 重写：`BrowserCommandError` 异常 + `open` → `snapshot -i --json` 流程
- CLI 新增 `browser status` / `browser open` / `browser content` 命令
- `is_available` 检测 + 官方安装提示

### 3.2 核心 API — `bili/m32-core-api`

- `BilibiliAPIClient` 新增统一请求助手 `_get_json()` + 轻量限流 `_respect_rate_limit()`
- 新增 cursor-based `get_user_history(max_items=200)`
- 新增 `get_favorite_folders()` / `get_all_favorites()` 带预算控制
- 新增 `get_following()` / `get_video_comments()`
- 新增 `FavoriteFolder`, `FavoriteFolderWithItems`, `FollowingUser`, `CommentInfo` 数据结构
- 新增集成测试骨架 `@pytest.mark.integration`

### 3.1 Cookie 认证 — `bili/m31-cookie-auth`

- `AuthManager`：cookie 持久化 + nav API 验证 + `SupportsNavClient` Protocol DI
- `BilibiliAPIClient.get_nav_info()`：解析 `/x/web-interface/nav`
- CLI 新增 `auth login`（交互式 + `--cookie`）和 `auth status`

---

## M2: LLM 多模型支持 ✅

### 2.3 Prompt 管理与 LLM Service — `llm/m23-prompt-management`

- 新增 `prompts.py`：Socratic 对话 prompt 构建 + core memory 注入
- 新增 `service.py`：`LLMService` 门面（prompt 组装 + registry 调用 + 空响应校验）
- 新增 `MemoryManager.render_core_memory_prompt()`
- `SocraticDialogue.respond()` 对接 LLMService，替换 TODO stub

### 2.2 Provider Registry — `llm/m22-registry`

- 新增 `build_llm_registry()`：从 Config 自动构建 + provider fallback
- `LLMRegistry.complete()`：sequential fallback，`LLMResponseError` 不触发 fallback
- CLI 新增 `health-check` 命令 + `config-show` 显示已注册 provider

### 2.1 Provider 实现 — `llm/m21-providers`

- 新增统一异常层级：`LLMProviderError` → `LLMRateLimitError` / `LLMTimeoutError` / `LLMResponseError`
- `OpenAIProvider` / `ClaudeProvider`：retry + 超时映射 + 空响应保护
- 新增 `OllamaProvider`（本地 LLM）
- 新增 `DeepSeekProvider`（继承 OpenAI）

---

## M1: 基础设施 ✅

### 1.3 日志系统 — `infra/m13-logging-system`

- 新增 `logging_setup.py`：Rich 控制台 + 文件 handler，防重复初始化
- `LoggingConfig`：level / file_level / directory / filename
- CLI 全局 `--log-level` 选项

### 1.2 配置系统 — `infra/m12-config-system`

- `config.py` 增强：`ConfigError` / `ConfigDiagnostics` / 严格校验
- CLI `config-show` 显示配置 + 引导提示
- `config.example.toml` 完整注释

### 1.1 开发环境和 CI — `infra-m1`

- Ruff + MyPy + Pytest 质量门禁
- GitHub Actions CI 工作流
- `tomllib` 配置加载
