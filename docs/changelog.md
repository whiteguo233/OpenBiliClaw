# 变更日志

> 按里程碑记录各阶段交付内容。每次分支合回 main 时追加条目。

---

## M8: 插件后端 API（进行中）

### B站动态语气优化 — `tone/m94-bilibili-tone`

- 新增 `ToneProfile` 派生层，从画像、偏好摘要和近期反馈推断 `density / warmth / playfulness / directness`
- 推荐表达、画像总结和聊天 prompt 统一接入这层语气系统，基础风格改为“老B友”，但会随用户理解逐步细调
- 推荐理由减少算法解释腔，画像减少心理报告感，聊天保留追问能力但更像懂 B 站语境的老朋友

### OpenRouter Provider 支持 — `llm/openrouter-provider`

- 新增 `OpenRouterProvider`，通过 OpenAI-compatible 调用链接入统一的超时、重试、错误归一化和 JSON mode
- 配置层新增 `[llm.openrouter]`，支持 `api_key`、`model`、`base_url` 以及可选请求头 `http_referer` / `x_title`
- `LLMRegistry` 现在可以自动注册 `openrouter`，并支持把它设为默认 provider

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
