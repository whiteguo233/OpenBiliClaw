# 变更日志

> 按里程碑记录各阶段交付内容。每次分支合回 main 时追加条目。

---

## v0.3.37 / extension v0.3.5: popup 与后端实时同步修复（2026-05-04）

### 改动

- **`delight.refreshed` 实时事件**: refresh tick 末尾比较 precompute 前后 delight 候选数,新增 ≥1 时通过 WebSocket 发 `{type: "delight.refreshed", count, total_pending}` 事件。**不带 per-item payload、不触发 chrome 通知**——纯粹是触发 popup 重拉 `/api/delight/pending-batch`。修复用户痛点「惊喜推荐只有重新加载插件才出来」。
- **`pool_status` 实时事件**: `_enforce_pool_cap` 后(每分钟跑一次)如果 pool_count 跟上次发布的不同,推 `{type: "pool_status", pool_available_count, pool_target_count}`。popup `mergeRuntimeStatusEvent` 已经有 handler,会自动重渲染。修复用户痛点「滚动列表时候选池数量不变」。
- **proactive_push_interval_seconds 600→120**: 把后台兜底推送 cadence 从 10 分钟收紧到 2 分钟。主路径已经是即时 `delight.refreshed`,这里只是安全网,降低延迟尾巴。
- **popup `onEvent` 加 `delight.refreshed` 分支**: 收到事件后调 `fetchPendingDelightBatch(20)` 重拉队列,`clearDelightQueue` + `pushDelightCandidate(item)` 串接 + `renderDelightSlot()`。出错静默,下一轮 proactive 推送会自愈。

### 影响

- 新 delight 在 backend 跑完 `precompute_delight_scores` 几秒内就出现在已打开的 popup 里,无需手动重新加载扩展。
- 候选池数量在 trim/reactivate 过的 60s 内同步到 popup UI。
- `proactive_push_interval_seconds` 默认值改了,如果你的 config.toml 显式设过 600 仍会沿用,新装/默认值是 120。

---

## v0.3.36: Delight LLM JSON 解析容错（2026-05-04）

### 修复

- **`LLMDelightScorer` 不再因 provider 输出形态崩溃**: DeepSeek 严格按 prompt 返 `[...]`,但 mimo-v2.5-pro 等模型在 JSON 模式下倾向返 `{"results": [...]}` / `{"items": [...]}` / 或多个 root 对象 newline 分隔(触发 `JSONDecodeError: Extra data`)。新增 `_extract_delight_entries` 兜底:tolerant parse → 已知 wrapper 键解包(results/items/delights/data/scores/candidates/output/list/array)→ JSONL 行级回退 → single-dict-with-bvid 包装。用户切到 mimo 后 12/12 失败 → 现在全 shape 都能吞下。

---

## v0.3.35: 惊喜推荐改两段式检索（粗召 + 精排）（2026-05-04）

### 改动

- **粗召回**: `get_pool_candidates_needing_delight_score` 加 `min_relevance_score=0.55` 参数,SQL `WHERE` 加上 `relevance_score >= 0.55` 过滤。原来 SQL 只 `ORDER BY relevance_score DESC LIMIT N`,池稀疏时会喂给 LLM 一堆 weak-fit 垃圾。0.55 对齐 discovery rubric「moderate fit」基准——再惊喜也得至少半 fit。
- **精排扩容**: `precompute_delight_scores` 的 `limit` 默认 30 → 50,每 cycle 让 LLM 多看 20 条候选,提高真惊喜被命中的概率。成本从 ¥0.06/cycle 升到 ¥0.10/cycle (¥0.80/天 vs ¥0.48/天),换约 67% 更宽的搜索面。

### 思路

`relevance_score` 是 discovery 阶段 LLM 已经判过的「用户-内容匹配度」,免费可用。当作粗召回信号 + LLM-judge 做精排,经典两段式: 砍掉 95% 没望命中的低质 item,把 LLM 调用集中在最值得评判的 candidate 上。

---

## v0.3.34: 惊喜推荐改用 LLM 评分（2026-05-04）

### 改动

- **`DelightScorer` 从 embedding-cosine 升级为 LLM batch 评分**:之前的实现用 `likes_alignment` / `deep_need_alignment` / `dislike_penalty` 等 embedding 余弦相似度——但「惊喜」语义上跟「相似度高」对立(用户不喜欢「又一条 DeepSeek 测评」),embedding 越高越像反而越不惊喜。新增 `LLMDelightScorer` 类:每个 batch (默认 5 条) 一次 LLM 调用,LLM 直接按预设 rubric 判分(0-1)+ 给出 rationale + hook,**惊喜的核心判据从「相似」变成「跨域呼应 / 隐藏需求 / 概念桥接」**。
- **省掉二次 reason generation 调用**:LLM 评分时已经返回 80-180 字的 rationale 和 2-4 字 hook,直接当 `delight_reason` / `delight_hook` 写入数据库,不再单独调 `_generate_delight_reason`。
- **成本**:稳态每 cycle ~6 batch call × ¥0.01 = ¥0.06/cycle,8 cycle/day = **~¥0.48/天**;省下来的 reason generation 是 ¥0.6/天,**净改善 -¥0.12/天**。首次池子完整重打分一次性 ¥1-2。
- **`build_delight_score_batch_prompt` 在 `llm/prompts.py` 新增**:静态 system prompt(cache-friendly,符合 v0.3.28+ 规约),user payload 用 sort_keys 保证 deterministic prefix。
- **数据迁移**:删掉所有 `pool_status='fresh'/'shown'` 的老 delight_score(都是 embedding-era 标定的不可信值),让 LLM scorer 全量重判。

### 测试

- 重写 `test_precompute_delight_scores_*` 用例反映新 LLM-batch 形态,LLM mock 返回 `[{bvid, score, rationale, hook}]` 数组。

---

## v0.3.33: Delight 候选过滤修复（2026-05-04）

### 修复

- **`get_delight_candidates` 不再返回 `pool_status='suppressed'` 的 item**:之前 SQL 包含 `IN ('fresh', 'shown', 'suppressed')`,但 suppressed 是被 topic-group cap / 来源配额裁出活跃池的 item,delight 评分还挂在上面。结果 popup 每次刷新调 `/api/delight/pending-batch?limit=20` 都从 562 条 suppressed 历史评分（v0.3.32 dislike/threshold 改前打的）里捞 20 条出来,**用户每次重新加载扩展都看到 20 个看似惊喜的"幽灵推荐"**。改成 `IN ('fresh', 'shown')`,只保留活跃池。
- **一次性清理 9991 条 suppressed 状态下的 delight 残留**:`UPDATE content_cache SET delight_score=0, delight_reason='', delight_hook='', delight_notified=0 WHERE pool_status='suppressed'`。修改 SQL 后这些数据本身已不会再 leak，但清掉避免 suppressed → reactivate 时再带着老 delight 漂回来。

### 测试

- 反转 `test_database_get_delight_candidate_allows_suppressed_delight_item` 的语义：原测试用注释「虽然普通池压掉了，但这条对你还是很可能是惊喜」固化了 bug 行为，现改名 `..._excludes_suppressed_pool_items` 并断言 None。

---

## v0.3.32: Embedding 与 LLM Provider 解耦 + OpenAI 协议兼容 provider（2026-05-04）

### 改动

- **`[llm.embedding]` 拥有独立的 `api_key` / `base_url`**：embedding 不再借用 `[llm.<provider>]` 的连接，避免「想用 OpenAI 跑 embedding 但 chat 走 DeepSeek」时被迫在两处填同一个块。`build_embedding_service` 直接根据 `[llm.embedding]` 构造一个独立 provider 实例，与 chat 端 `LLMRegistry` 完全解耦。
- **新增 `openai_compatible` 一级 provider**：用于接入 Groq / Together / Azure OpenAI / vLLM / 自建等任何走 OpenAI 协议的服务。和 `[llm.openai]` 完全独立（不再用 base_url override 复用 openai block），可以同时在一个项目里跑两套（chat 用真 OpenAI、辅助任务挂 Groq 加速）。`base_url` 必填，缺失会被 `_collect_config_issues` 拦下，避免 401 hit `api.openai.com`。Embedding 段也支持选 `openai_compatible`（多数 OpenAI-compat 后端都暴露 `/v1/embeddings`，比如 Together、vLLM、Azure）。
- **向后兼容回落**：老 config（仅设了 `[llm.embedding] provider` 没填 api_key）仍可工作 —— 透明回落到 `[llm.<provider>].api_key`，并打一条一次性 WARNING 提示迁移；下个大版本会移除该回落。
- **删掉 `embedding_wants_ollama` 自动注册 hack**：embedding 现在自己构造 Ollama，chat registry 不再因为 `[llm.embedding] provider="ollama"` 而被强插一条 embedding-only 条目。
- **API 层 `EmbeddingConfigOut` 暴露 `api_key`（已脱敏）+ `base_url`**：`PUT /api/config` 接受新字段；`api_key` 字段若收到含 `*` 的回显（脱敏值原样回写），保留原值不覆盖。
- **扩展 popup Embedding 段**：新增 `EMBEDDING API KEY` / `BASE URL` 字段；provider 切换时联动模型 placeholder（`bge-m3` / `text-embedding-3-small` / `gemini-embedding-001`）和字段可见性（Ollama 隐藏 api_key、Gemini 隐藏 base_url）。删除 OpenRouter 选项（无 embedding 接口）。
- **配置渲染 / 加载同步更新**：`save_config` 写出新字段，`_build_config` 接受新字段；老 TOML（无新字段）正常加载，新字段默认 `""`。

### 影响

- 跑老 config 的用户首次启动会看到一条 `[llm.embedding] api_key/base_url is empty — falling back to [llm.<x>] credentials. ...` 的 WARNING；行为不变，按提示把凭据搬到 `[llm.embedding]` 即可消失。
- `setup-embedding` 向导和扩展的 GET/PUT `/api/config` 调用方式均无破坏性改动。

---

## v0.3.31: Discovery 来源均衡兼容小红书（2026-05-03）

### 修复

- **小红书作为一等来源族参与候选池配额**:`_SOURCE_TARGET_SHARES` 增加 `xiaohongshu`，600 池目标约分配为 `search=141 / related_chain=141 / trending=35 / explore=141 / xiaohongshu=142`。`xhs-extension-task/search/profile` 等 raw source 会归并到同一个 `xiaohongshu` 来源族，避免小红书库存在 share-aware trim 中被当作未知来源或被拆成多个来源。
- **满池时也能恢复已 suppressed 的小红书高分候选**:`reactivate_under_quota_pool_sources()` 会在来源族低于配额时，从 `pool_status='suppressed'` 且带 `xsec_token` 的可打开候选中复活一批，再由 `trim_pool_to_target_count(source_share_quotas=...)` 按统一配额裁掉过量来源。现有被压住的小红书内容不必等重新浏览同一页面才有机会回到 fresh pool。
- **池子计数排除不可打开的小红书裸 URL**:`count_pool_candidates()` 和 `count_pool_candidates_by_source()` 现在只把带 `xsec_token` 的小红书行算作可用候选，避免 runtime 状态显示“池子满了”但 UI 实际不能推荐。
- **explore 域生成遇到 DeepSeek 空内容会自愈一次**:线上日志里的 `deepseek returned empty content` 来自 DeepSeek HTTP 200 但 `content=""`，之前普通模式没有 provider 层重试，导致 `discovery.explore.queries` 直接返回 0 个探索域。`DeepSeekProvider` 现在对空内容统一重试一次；`reasoning_effort` 开启时仍关闭 thinking 重试，普通模式按原参数重试。
- **小红书 bootstrap 任务无条件前台、discovery 始终后台**:之前 `xhs-task-dispatcher` 用 `isScrollableBootstrapTask`（即 `max_scroll_rounds > 0`）来决定 bootstrap 是否前台,所以若有用户用 `OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS=0` 跳过滚动会落到后台拉数据。语义改成「init-time bootstrap 始终前台 + discovery (search/creator) 始终后台」: bootstrap 是用户跑 `openbiliclaw init` 时主动期望看到的过程(透明性),且 XHS 虚拟列表只在 active tab 才正确分页;discovery 是后台连续扫描,不该打扰用户活跃浏览。
- **Ollama embedding 在系统代理环境下全失败**:用户开了本地 HTTP 代理（如 7897 端口的 VPN 客户端）时，`httpx.AsyncClient` 默认 `trust_env=True` 会把 localhost embedding 请求也走代理 → 全部 `httpx.ReadTimeout`。日志统计显示一天 140+ 次失败，**直接拖垮惊喜推荐**：`DelightScorer` 的 `likes_alignment` / `deep_need_alignment` / `dislike_penalty` 全返 0，99.5% 池内 item（604/607）落到 0.01-0.50 区间永远过不了 0.65 阈值。`OllamaProvider.embed()` 现强制 `trust_env=False`，绕开代理直连本地 Ollama。
- **EmbeddingService 缓存被空向量永久污染**:embedding 是用户配置 `provider="ollama"` 时的**主路径**（不是降级），但 `EmbeddingService.embed()` 之前会无条件把 provider 返回的 `[]` 也写进 L1 + L2 缓存。代理 bug 那段时间 ~140 次失败把 170 条核心 likes 文本（`游戏攻略` / `动漫杂谈` / `洛克王国` / `金铲铲之战` 等）全部毒化为空向量 → 即使修了代理，DelightScorer 永远从缓存拿到空列表 → likes_alignment 永远返 0。新增空向量守卫：provider 返 `[]` 时跳过缓存写入、打 WARNING 让失败模式在服务层可见而不是埋在 provider 日志里；同时清理了 `data/embedding_cache.db` 里已经被毒化的 170 条历史数据。
- **EmbeddingService 并发把本地 Ollama 打爆**:proxy fix 之后 daemon 立刻用并发 embed 补齐积压（delight scoring + 主题去重 + speculator + 池内 candidate batch 同时发起），实测一秒内 14+ 个并发请求灌进 bge-m3 单进程 GGUF runner，CPU 4 核 100%、`ollama runner` 占用 406%、curl 直连 30s 都收不到响应、所有 in-flight 请求 60s timeout 失败。新增 `EmbeddingService` 内部 `Semaphore(2)` 限流（默认 2，可通过 `max_concurrent_provider_calls` 改），同时把 `OllamaProvider.embed` 的 httpx timeout 从 60s 提到 120s 吸收冷启动 + 队列等待。
- **Speculator 探针长复合中文短语永远匹配不上事件**:LLM 生成的 probe 域名常是 `'AI图像生成工作流深度拆解'` 这种 13 字连续中文，原匹配器三条路径全失效（整串 substring 不命中、`[与和·、/\s及]+` 切不动、whitespace-tokenize 只产 1 个 token）→ 一天观察 0 次匹配，所有探针挂在 active 槽 3 天后 TTL 过期被拒。新增 Chinese-bigram 兜底：name 端要求 ≥4 个 distinct bigram、event 端要求 ≥2 个 bigram 重叠才算命中，配合上游 `confirmation_threshold=3` 防误升。
- **Speculator "generated N new" 日志骗人**:`result.generated` 之前取 `state.active` 全集，导致每轮 tick 都把携带过来的老探针重复打成 "generated 2 new"，制造在工作的假象。改成取 `_generate` 调用前后的 domain 集合差，只展示真正新增的；空集时落到 `force_tick: no-op (active full)` DEBUG 行。`Speculator observed` 日志同步从 DEBUG 升到 INFO，让事件→探针确认信号在生产日志里可见。
- **Speculator slot-aware 提早 skip LLM 调用**:`_should_generate` 之前只检查 `active_count < max_active`,但 LLM 几乎肯定会重复提案已存在 active 集合中的 domain → dedup 之后净新增 0。要求至少 2 个空闲 slot 才发起 LLM 调用,否则跳过。粗略估算每天省 ~¥0.04 的 speculator 浪费调用。
- **CLI 三个 Ollama 探测**(`_ollama_is_running` / `_ollama_has_model` / `_ollama_pull_model`)同样存在代理劫持问题,补 `trust_env=False`,避免 `setup-embedding` 在代理环境下误判 "Ollama 没启动"。
- **DelightScorer 增加 embedding 子系统死亡告警**:四个 embedding-driven 信号(likes / deep_need / insight / dislike)同时为 0.0 时,几乎只可能是 embedding 子系统挂了(用户的 likes/deep_needs/insights/disliked_topics 同时为空在稳态下不可能)。新增 per-candidate WARN 让失败信号在 recommendation 层可见,不再被埋在 1GB 的 provider HTTP DEBUG 里。
- **`trim_topic_group_overflow` 每分钟一行 INFO 噪音降级**:稳态下池子里 `人工智能:8 over cap` 这种数据每 60s 重复打一遍,一天 1440 条。Database 里的 emit 改成 DEBUG;Refresh 层的 `enforce_pool_cap: reactivated=N` 加 fingerprint 缓存,reactivated 数与上一 tick 相同则降到 DEBUG,变化时才 INFO。
- **EmbeddingService L1 cache 改 LRU**:之前用普通 dict + `next(iter)` 驱逐最老,实质是 FIFO,500 条容量 + bursty 访问下会驱逐刚刚命中过的热 key。改用 `OrderedDict` + `move_to_end(key)` on hit + `popitem(last=False)` on evict,正确 LRU。
- **OllamaProvider 加 1 次重试**:bge-m3 短暂 OOM / Ollama runner 重启 / 模型 hot-swap 这些瞬时故障之前直接返 `[]` 走静默降级。改成 `for attempt in (1, 2)` 模式,首次失败 DEBUG 一行后立刻重试,两次都失败才 WARN。同时把 `Ollama embedding failed` 日志改成 `failed after 2 attempts`。
- **`config.toml` 同步 v0.3.30 logging 默认值**:把用户旧的 `max_file_size_mb = 1024` 降到 100,补上 `aggregate_budget_mb = 500` / `unmanaged_truncate_mb = 200` / `unmanaged_max_age_days = 30`,让 v0.3.30 引入的日志兜底机制实际生效。这个改动只动 `config.toml`(gitignored),仓库 `config.example.toml` 早就是新值。
- **DelightScorer dislike_penalty 阈值/放大器按 bge-m3 重新标定**:之前 `(sim - 0.55) * 2.5` 是按 Gemini 标的,bge-m3 对低语义中文(直播片段标题、metadata)有"通用中文 cluster"现象,baseline cosine 0.78-0.85,所有候选都被 dislike 拉减 0.30 分。改成 `(sim - 0.78) * 1.5` 后:历史 3 条 ≥0.65 delight item 重打分从被 dislike 假阳性压到 0.20 → 恢复到真实 0.51-0.52,新候选最高 likes 也从被压到 0.13 → 真实 0.40-0.48。
- **DelightScorer threshold 同步按 bge-m3 实际分布下调**:0.65/0.75 默认是按 Gemini embedding 标的,在 bge-m3 上等于"永远不触发 delight"。基于实测 100 条池内 top-relevance 候选的实际分数分布(max=0.485, p95=0.440, p90=0.428),`DEFAULT_DELIGHT_THRESHOLD` 从 0.65 改成 0.45(对应 ~p95 的"特别匹配"位置),`CONSERVATIVE_DELIGHT_THRESHOLD` 从 0.75 改成 0.55。
- **DelightScorer "embedding 子系统死亡"告警改用直接探测**:之前判定条件是 4 个 embedding 信号同时为 0,但一个用户兴趣范围之外的合法内容(如 tech-only 用户看到一条历史纪录片标题)也会全 0,导致告警每条 candidate 都 false-positive。改成单次 `embed(content_text)` 探测,只有 provider 真返空向量才告警。

### 测试

- 新增 storage / refresh runtime 回归测试覆盖小红书来源族归一、under-quota suppressed 复活、满池裁剪传递小红书配额。
- 新增 LLM provider 回归测试覆盖 DeepSeek 普通模式空内容重试。
- 新增 `test_observe_matches_long_chinese_composite_phrase` 覆盖 bigram 匹配兜底（命中真实标题、不误中无关内容）。

---

## v0.3.30: 日志自动清理（按大小 / 按年龄 / 按总预算）（2026-05-02）

用户实测发现 `logs/` 目录下有几个未托管的大文件占盘:`backend-restart.log` 2.2 GB、`openbiliclaw-restart.log` 296 MB,加上原本的 `openbiliclaw.log` 1 GB 主日志,整个目录 5 GB+。原 `RotatingFileHandler` 只管 *本身配置的那个* 文件,其他 stdout-redirect 出来的脚本日志完全没人管。补一套 unmanaged 日志兜底清理。

### 新增

- **启动时自动 sweep `logs/` 目录的 unmanaged 文件**(`logging_setup._sweep_unmanaged_logs`):
  1. 单文件超过 `unmanaged_truncate_mb` MB → 直接 `truncate` 为 0(留一行 marker)。专治 `backend-restart.log` 这类被脚本无限 append 但项目代码控制不到的文件
  2. mtime 超过 `unmanaged_max_age_days` 天 → 直接删除
  3. 整个 logs/ 目录(含 managed)总大小超过 `aggregate_budget_mb` MB → 按 mtime 从最旧的 *unmanaged* 文件开始删,直到回到预算内。**Managed 文件(`<filename>` + `<filename>.N`)永远不被这个 pass 删**(rotation 自己管)
  
  每个 truncate / delete 都打 INFO 日志,daemon 启动时 tail 一眼能看到清了什么
- **`openbiliclaw logs-prune` CLI**(默认 dry-run)—— 手动触发兜底清理,可临时用更激进 / 更保守的阈值。`--apply` 才真改文件。Rich 表格按 traffic-light 色显示 keep / truncate / delete (age) / delete (budget) 四种 plan
- 4 个新单测覆盖 truncate / age delete / aggregate budget eviction / sweep_unmanaged=False 跳过

### 默认值变化(影响新装)

- **`max_file_size_mb` 1024 → 100**:1 GB 单文件太大,绝大多数 daemon 跑两天就把磁盘吃掉一截。100 MB × 2 backups = 200 MB 上限,够 1-2 周 INFO 级日志
- **`aggregate_budget_mb = 500`**(新):整个 `logs/` 目录总磁盘预算 500 MB,unmanaged 超出按时间评最早删
- **`unmanaged_truncate_mb = 200`**(新):单文件超过 200 MB 直接 truncate
- **`unmanaged_max_age_days = 30`**(新):30 天前的 unmanaged 文件直接删

### 修改

- `LoggingConfig` 加 3 个新字段(`aggregate_budget_mb` / `unmanaged_truncate_mb` / `unmanaged_max_age_days`),旧 config.toml 没有这些字段也兼容(用 dataclass 默认值)
- `configure_logging` 新增 `sweep_unmanaged: bool = True` kwarg。CLI `_initialize_logging` 检测 `logs-prune` 命令时传 `False`,避免 dry-run 被全局 callback 顺手清掉(否则 dry-run 等于自动 apply)
- `config.example.toml` 同步更新,加上 4 行注释说明每个阈值的意义

### 修复

- **扩展自动同步 B 站 Cookie 的首装竞态**:如果扩展已安装但本地后端还没起来,之前首次 POST 失败后要等 cookie 变化或最长 1 小时 alarm 才会重试,导致 AI agent 一句话安装后看起来"自动获取不到 Cookie"。现在 service worker 冷启动会启动 cookie sync,POST 失败时把 alarm 临时切到 1 分钟重试,成功后恢复 60 分钟刷新;`startCookieSync()` 也改成真正幂等,避免重复注册 `chrome.cookies.onChanged` 监听器。
- **后端可主动要求扩展回传 Cookie**:`/api/runtime-stream?client=background` 建连时,如果后端解析不到 B 站 Cookie,会先发 `bilibili_cookie_sync_requested`;扩展收到后立即 POST 当前浏览器 Cookie 到 `/api/bilibili/cookie`。这让后端启动后不用等下一轮 alarm,能主动拉起一次 Cookie 同步。
- **AI agent 一句话安装不再跳过 embedding / 小红书确认**:`agent_bootstrap.py` 新增 `--yes-xhs` / `--no-xhs` 并在 auto-init 前检查两个显式决策:embedding 方案和小红书收藏 / 点赞 opt-in。凭据齐全但没问这两项时,bootstrap 返回 `status=needs_decisions` 而不是直接跑 `openbiliclaw init`;install.sh / install.ps1 的状态块会把默认 `--embedding-provider ollama --embedding-model bge-m3 --no-xhs` 示例命令打印出来,让智能体必须先问用户再继续。
- **插件推荐列表滚到底续页不再卡住**:side panel 推荐 tab 在首次渲染、切回推荐页和追加完成后都会重新检查一次底部距离,不再只依赖新的 scroll 事件触发 `/api/recommendations/append`。
- **插件初始化后不再误显示 init 提示**:popup 空推荐状态会优先识别 `manual_refresh_state=running`、pending signal 和候选池补货信号;初始化后首轮补货 / 池子已有内容但 `initialized` 标记短暂滞后时,不再继续显示“还没完成初始化”。
- **插件发布版本推进到 `extension-v0.3.3`**:本次插件 release 包含 Cookie 自动同步竞态、推荐续页和初始化状态提示修复。

### 测试

- 全套 944 通过 / 16 失败(基线) / 15 跳过 — 0 新回归

---

## v0.3.29: prompt-cache 通用化改造 + 命中率观测 + Claude 显式 marker（2026-05-02）

为 daemon 长跑成本拉低 50-80% 做架构性铺垫。挖到 v0.3.26 计费台账没有 cache 字段(provider 报但没归一化),v0.3.27 prompt builders 多个把 per-call 变量塞进 system 消息(让 provider-side 自动缓存命中率永远是 0),Claude 这种"显式 marker 才激活" 的 provider 完全没接入。三个层一起改。

### 新增 (Layer 3 — 跨 provider 的命中率观测基础)

- **每家 LLM provider 提取 cache 字段并 normalize 到 `LLMResponse.usage["cached_input_tokens"]`** —— OpenAI 系 (`prompt_tokens_details.cached_tokens`)、DeepSeek (`prompt_cache_hit_tokens`)、Claude (`cache_read_input_tokens`,另外保留 `cache_creation_input_tokens` 单独记账)、Gemini (`usage_metadata.cached_content_token_count`),OpenRouter / 中转站 / 国产官方因为继承 OpenAIProvider 自动获益
- **`pricing.CACHE_HIT_DISCOUNT`** 表 + `estimate_cost(..., cached_tokens=N)` 扩展 —— 各家 cache 折扣率列表(DeepSeek 0.10 / OpenAI 0.50 / Claude 0.10 / Gemini 0.25 / Ollama 0 / 未知 0.5),split prompt_tokens 按 cached/non-cached 分别计费
- **`Database.llm_usage` 加 `cached_input_tokens` 列 + migration `_ensure_llm_usage_cache_columns`** —— 存量 DB 自动 backfill,新调用按 cache 折扣存账。`query_llm_usage_by_caller` / `_total` / `_since_id` 全部返回 cache 字段
- **`UsageRecorder` 提取 cache 字段并写库** —— INFO 日志多了 `cache_hit=4000/8500 (47%)` 注释,直接 tail daemon 看实时命中率
- **`openbiliclaw cost --by caller` 加 cache 命中率列** —— 红 (<30%) / 黄 (30-60%) / 绿 (>60%) 三色,红色 caller = prompt 前缀有污染,直接定位到要 audit 的 builder
- **`init` 收尾的 cost summary 也展示 per-caller cache 命中率** —— 跑完一次 init 直接看命中分布

### 重构 (Layer 1 — 让 system_prompt 100% 静态以激活 provider 缓存)

之前 audit 出 `build_batch_content_evaluation_prompt` / `build_content_evaluation_prompt` / `build_recommendation_expression_prompt` / `build_batch_expression_prompt` / `build_delight_reason_prompt` 这 5 个最热点的 builder 都把 `source_hint` / `_platform_friend_label` / `_platform_content_label` / `_render_tone_profile` 拼接到 system_prompt,**每次切 strategy / platform / 用户 → 整个 ~3500 token 的 system prompt 失配,provider 自动 cache 永远命不上**。改造成"system 100% 静态 + 所有变量挪到 user_prompt 前缀":

- 5 个 builder 全部用 module-level 常量 `_<NAME>_SYSTEM_PROMPT` 表达 system,每个常量都是字符串字面量(不能 f-string,不能拼接,不能 substitute);所有原 system 里的变量(source_context / source_platform / tone_profile / friend_label / content_label)挪到 user_prompt
- user_prompt 顺序: 平台 / 上下文 / tone (semi-stable per user) → profile (slow-changing) → content_batch (every call)。这样 provider auto-cache 不仅命中 system,顺序合理时还能延伸命中 user 前缀
- JSON 序列化全部加 `sort_keys=True`,防止 dict 顺序变动让 cache miss
- system 里加一句 "下面 user 消息会给出 <X>(...)" 让 LLM 明确知道去哪里读变量(prompt engineering 上不损失)

### 例外 (Layer 1 单用户场景下保留 user-specific system)

- **`build_socratic_dialogue_prompt` 保持原样** —— 它的 system 包含 friend_label / tone / core_memory_text。在 OpenBiliClaw 这种**单用户场景**下,per-user 状态在该用户的多次调用里稳定 → cache 仍命中。多用户部署才需要重构,目前不必

### 工程纪律 (Layer 4)

- **`CLAUDE.md` 新增 "LLM Prompt-Cache Convention" 段** —— 给未来贡献者立规则:任何新 prompt builder MUST 满足 system 100% 静态,JSON 序列化必须 deterministic,所有变量入 user_prompt
- **`test_llm_prompts.py::test_prompt_builder_system_messages_are_call_invariant`** —— 自动化兜底:遍历所有 prompt builder,两组不同 input → assert system msg byte-identical,违反则报错并指明 cache-poisoning builder

### Layer 2 — Claude 显式 cache marker

- **`ClaudeProvider` 自动给 system message 打 ephemeral cache_control 标记** —— Anthropic prompt cache 是显式机制,纯字符串 `system="..."` 永远不缓存,必须用 list-of-blocks 形式 + `cache_control: {"type": "ephemeral"}` 才会激活。新增 `_render_system_param()` 把 system 文本包成单 block 列表 + cache marker,5min TTL,90% off on cache reads,首次写 +25% 加价。系统 prompt 短于 per-model 阈值时(Sonnet 1024 / Opus-Haiku 2048 token)Anthropic 静默忽略 marker,所以这个改动对短 prompt 也安全
- 2 个新单测 covering: marker 正确插入到 system list-of-blocks 形式,以及 `cache_read_input_tokens` / `cache_creation_input_tokens` 通过 `LLMResponse.usage` 正确流转

### 仍未做(deferred)

- **Gemini 显式 Context Caching API** —— Gemini 的 prompt cache 不是 in-line marker,而是另起一个 `cachedContents.create()` API 提前上传 stable 部分得到 `cache_id`,然后调 `complete()` 时引用 cache_id。需要 cache_id LRU 池 + TTL 管理,改动量比 Claude 大得多。先观察 Layer 3 数据 —— 如果用 Gemini 的人多且命中率确实低,再投资

### 测试

- 8 个新单测覆盖 cache 折扣计算 / per-caller 持久化 / 跨 provider 命中字段 round-trip / Claude cache_control marker 注入 / Claude cache_read+creation token 提取
- audit invariant 测试覆盖 6 个 cache-friendly builder
- 全套 940 通过 / 16 失败(基线) / 15 跳过 — 0 新回归

### 预期效果

- DeepSeek 默认场景:`discovery.evaluate_batch` 5 次 strategy 评估,从原本 5 次 cold(~17500 input tokens 全收钱)→ 第 1 次 cold + 后 4 次命中 ~3500 token system,**该 caller 总成本立即砍 60-70%**
- 同效果适用于 `recommendation.evaluate_batch` / `_expression` / `_delight_reason` / `_content_evaluation`
- OpenAI 50% / Claude 90% / Gemini 75% cache 折扣,自动派(DeepSeek/OpenAI/中转站)无需改 SDK 调用,显式派(Claude)由 ClaudeProvider 内部自动注入 marker
- 跑一段时间后 `openbiliclaw cost --by caller --days 7` 应该能看到顶层 caller 的命中率从 0 跳到 60-80%

### 下一步

- Gemini 显式 Context Caching 等数据驱动决策(见上 deferred 段)
- 数据驱动的优化:看 `--by caller` 命中率 < 60% 的 caller,逐个 audit 是不是新加的 builder 没遵守 cache 公约

---

## v0.3.28: LLM 费用观测全链路打通（caller 标签 + 实时日志 + per-init 总结）（2026-05-02）

之前 `UsageRecorder` 的 `caller` 字段虽然在表结构 + recorder API + DB 查询里都已就位,但**整个代码库里没有一个 LLM 调用点真的传 `caller="<module>"`** —— 所有行的 caller 都是空字符串,意味着当年设计的 per-module 费用 attribution 完全失效,`openbiliclaw cost` 能看到 by-day / by-provider/model 但看不出"钱花在哪一层",这是用户最关心的视角。补全:

### 新增

- **27 个 LLM 调用点全部 wire 上 caller 标签** —— 覆盖 `recommendation.evaluate_batch / .delight_reason / .write_expression / .expression`、`discovery.trending.rids / .search.queries / .explore.queries / .evaluate_single / .evaluate_batch`、`eval.scenario_gen / .relevance / .specificity / .query_quality`、`soul.preference / .preference.chunk / .profile_build / .insight / .awareness / .role_update / .values_update / .core_update / .speculate / .dialogue / .dialogue.tools / .dialogue.tool_followup / .dialogue_insight`、`sources.{platform}.extract / sources.xhs.keyword_gen`、`api.sentiment`。还把 `LLMService.complete_with_tools` / `complete_socratic_dialogue` 也加了 `caller` 形参并 forward 到内部 `complete_with_core_memory` —— 之前这两个方法漏接 `caller`,让 dialogue 路径的费用全归到 untagged
- **`UsageRecorder.record()` 每次 LLM 调用打 INFO 日志** —— `[llm-cost] caller=discovery.evaluate_batch model=deepseek-v4-flash tokens=850→230 ≈ ¥0.0010`。tail daemon 日志 (`journalctl -fu openbiliclaw` / `docker logs -f openbiliclaw-backend`) 就能看费用实时累积,不用等跑完才查
- **单次调用超阈值时打 WARN** —— 默认 ¥0.10 阈值(可通过 `OPENBILICLAW_LLM_EXPENSIVE_CNY` 环境变量调)。抓 runaway prompt(忘了截断历史 / 误开 reasoning_effort=max / 单 batch 太大)用,WARN 行包含 caller / model / token / 实际花费,定位很快
- **`openbiliclaw cost --by caller`** —— `cost` CLI 加了第三个表(by-caller),展示按模块的费用占比 + token 数。`--by all`(默认) / `--by day` / `--by provider` / `--by caller` 四档
- **init 结束时自动打印本次 init 的 cost summary** —— 不用再手动 `openbiliclaw cost`,init 完成后直接显示按 caller 拆分的费用占比(本次 init 总 N 次调用 ≈ ¥X,其中 discovery.evaluate_batch 占 60% / soul.profile_build 占 15% 等)。靠 `Database.max_llm_usage_id() / query_llm_usage_since_id()` 在 init 入口快照行 id,出口反查,把累积 usage 限定到本次 init 窗口
- `pricing.py` 加常量 `EXPENSIVE_CALL_CNY_THRESHOLD = 0.10`(可环境变量覆盖)

### 修改

- `Database.query_llm_usage_by_caller(days=N)` 新方法,SQL 按 caller 分组聚合,`ORDER BY cost_cny DESC` 让最贵的调用排第一
- `LLMService.complete_with_tools` / `complete_socratic_dialogue` 签名加 `caller: str = ""`,forward 到 inner `complete_with_core_memory(caller=caller)`

### 测试

- 修了 ~30 个测试 fake 让它们的 `complete_*` 签名也接 `caller` 形参(否则生产调用点传 `caller=...` 会让 fake 报 TypeError)。批量改了 17 个测试文件
- 全套测试 16 失败 / 931 通过,跟 baseline 完全一致 —— 0 新回归

---

## v0.3.27: 安装文档全面同步至 init wizard 当前形态 + DeepSeek V4 默认模型（2026-05-02）

### 修改

- `docs/openclaw-quickstart.md` —— 把 `init` 4 阶段向导描述同步到 v0.3.27+ 当前形态:Phase 1 LLM(DeepSeek 默认 / Ollama+网关收进高级)、Phase 2 配置、Phase 3 Embedding(Ollama bge-m3 默认)、Phase 4 Per-module 覆盖。新增独立的 🌸 小红书数据可选问题(在 wizard 之后、数据拉取之前),并明确"扩展会在浏览器开前台 tab 抢一次焦点"的真实行为。`init` 阶段列表新增可选小红书拉取步,并提示用 `openbiliclaw cost` 查看花费
- **DeepSeek 默认模型 `deepseek-chat` → `deepseek-v4-flash`** —— 旧 `deepseek-chat` / `deepseek-reasoner` DeepSeek 官方将于 2026/07/24 弃用。`config.example.toml` 早就指向 v4-flash,但 `cli.py` `_PROVIDER_DEFAULTS` 还在写 `deepseek-chat`,导致 init 向导给出过期的默认值。修复点:`_PROVIDER_DEFAULTS["deepseek"].model`、`_LLM_MENU` hint、Phase 2 配置阶段新增 `_PROVIDER_MODEL_HINT` 表(每个 provider 在 prompt 模型名前显示一行可选清单,DeepSeek 那行明确列 v4-flash / v4-pro 两档 + 旧名弃用日期),让用户明确确认而不是回车跳过一个看不懂的字符串。同步更新 `docs/{openclaw-quickstart,docker-deployment,agent-install,agent-deployment,modules/config,modules/llm}.md`、`scripts/agent_bootstrap.py` 示例、`extension/popup/popup.html` placeholder、`pricing.py` 加 `deepseek-v4-pro` 行
- **OpenAI 协议兼容: 9-preset 子菜单 (Kimi / MiniMax / 通义 / 智谱 / Yi / 中转站 / 自建 / Azure / 其它)** —— 之前选第 7 项 "OpenAI 协议兼容" 就掉到一个让用户手填 Base URL + 模型名的裸 prompt,普通用户不知道每家的 endpoint 长什么样,中转站 / Azure / vLLM 三种用法的差异也没说清。新增 `_OPENAI_COMPAT_PRESETS` 表 + `_prompt_openai_compat()` helper:选第 7 项后弹出 9 行子菜单,**Base URL + 默认模型按 preset 自动填好**(Kimi `api.moonshot.cn/v1` + `moonshot-v1-8k`;MiniMax `api.minimaxi.chat/v1` + `abab6.5s-chat`;通义 `dashscope.aliyuncs.com/compatible-mode/v1` + `qwen-plus`;智谱 `open.bigmodel.cn/api/paas/v4` + `glm-4-flash`;Yi `api.lingyiwanwu.com/v1` + `yi-medium`;中转站 / Azure / vLLM-LMStudio 也都各自有合理的 prompt 引导)。每个 preset 在 prompt 模型名前显示该家的"可选模型"清单。同步 `docs/{openclaw-quickstart,docker-deployment,agent-install}.md` 全部展开 9 个 preset 的清单,AI agent 注释里加"看到 Kimi / 通义 / 智谱 / Yi / Moonshot / MiniMax / Qwen / GLM / 中转站 / OneAPI / Azure / vLLM / LMStudio 等关键词时,优先引导走第 7 项子菜单"
- **默认模型全面刷新到 2026-05 当前线上(之前几乎全部过期)** —— 用户实测发现 init 向导推的默认模型几乎都已停服或被替代。Web 搜索确认每家当前线上情况后,逐项更新 `_PROVIDER_DEFAULTS`、`_LLM_MENU` hint、`_PROVIDER_MODEL_HINT`、`_OPENAI_COMPAT_PRESETS`、`config.example.toml`、`pricing.py`:
  - **OpenAI**: `gpt-4o-mini` → `gpt-5-nano`(GPT-5 nano 是当前最便宜款 $0.05/$0.4 per M;gpt-4o 系列 2026-02 已从 ChatGPT 退役)。完整可选: gpt-5-nano / gpt-5.4-nano / gpt-5.4-mini / gpt-5.5(4/2026 旗舰)/ gpt-5.5-pro
  - **Claude**: `claude-sonnet-4-5-20250929` → `claude-sonnet-4-6`(Sonnet 4.6 1M ctx)。完整: claude-haiku-4-5(便宜)/ sonnet-4-6(默认)/ opus-4-7(旗舰 / agentic 最强)
  - **Gemini**: `gemini-2.0-flash-exp` → `gemini-2.5-flash`(2.0-flash-exp 已淘汰)。完整: 2.5-flash(默认)/ 3-flash-preview(新)/ 3.1-pro(旗舰)/ 3.1-flash-lite-preview(最便宜)
  - **OpenRouter**: `openai/gpt-4o-mini` → `openai/gpt-5-nano`(对齐 OpenAI 默认)
  - **Ollama**: `llama3` → `qwen2.5:7b`(项目中文优先,qwen2.5 比同尺寸 llama3 中文好得多)
  - **Kimi**: `moonshot-v1-8k`(2026-05-25 停服)→ `kimi-k2.6`(最新 / 256K ctx / 多模态)。Base URL `api.moonshot.cn/v1` → `api.moonshot.ai/v1`(国际站为主)
  - **MiniMax**: `abab6.5s-chat`(已被 M 系列替代)→ `MiniMax-M2.7`(4/2026 / 228K ctx / $0.30 ~ $1.20 per M)。Base URL `api.minimaxi.chat/v1` → `api.minimax.io/v1`
  - **通义**: 仍用 `qwen-plus` 别名(自动跟最新快照,当前 → qwen3.6-plus)。endpoint 不变
  - **智谱 ChatGLM**: `glm-4-flash` → `glm-4.7-flash`(1/2026 发布的免费旗舰 / 200K ctx);可选 `glm-5`(2/2026 付费旗舰 / 745B MoE)
  - **Yi**: 仍用 `yi-medium`,在 hint 里加上 `yi-lightning`(新 / 快)
  - **DeepSeek**: ✅ 之前修对了,仍是 `deepseek-v4-flash`/`deepseek-v4-pro`
  - **pricing.py**: 加 GPT-5 / Claude 4.6+ / Gemini 3.x / Kimi K2.6 / MiniMax M2.7 / Qwen flash-plus-max / GLM 4.7-flash + 5 / Yi spark-medium-large 的单价行,旧 V3/V4o/Sonnet 4.5 等保留兼容
- **OpenAI 协议兼容引导深度补强** —— 之前 9-preset 子菜单只解决了 "Base URL + 模型自动填" 一层,用户实际还会卡在"在哪里申请 Key / 这家服务到底是干嘛的 / 选完之后 embedding 怎么办"这三个问题。每个 preset metadata 扩展为 `description` / `signup_url` / `domain_alt` / `supports_embedding` / `embedding_alt`,`_prompt_openai_compat()` 重写为四段式引导:
  - **选完后展示一段服务介绍**(Kimi → "国产长上下文老牌 256K ctx,长文档理解强";MiniMax → "代码 / agent 场景 SOTA,$0.30/$1.20 per M";智谱 → "GLM-4.7-Flash 完全免费,GLM-5 是 Claude Opus 级")
  - **直接打印 Key 申请链接**(国内/国际两个地址都列),用户 cmd-click 就能去注册
  - **国内域名替代提示**(Kimi `api.moonshot.cn/v1`;MiniMax `api.minimaxi.com/v1`)
  - **预提醒 embedding 怎么办**: Kimi / MiniMax / Yi / 自建 没 embedding endpoint(打印黄色 ⓘ 提醒 Phase 3 自动 fallback Ollama bge-m3,免费 / 离线);Qwen / GLM / Azure / 中转站 有 embedding(打印 💡 提示 Phase 3 高级选项可指向同一 base_url)
  - **结尾打印将写入的 (base_url, model) 二元组**,catch typo
- **`scripts/agent_bootstrap.py --llm-preset {kimi,minimax,qwen,zhipu,yi,self-hosted,relay,azure,custom}`** —— AI agent 驱动的非交互式安装路径补一刀。之前 AI agent 用 `--llm-base-url` + `--llm-model` 配 OpenAI 兼容服务时,得自己记住每家的 endpoint(经常写错);现在 `--llm-preset kimi` 一句话搞定,base_url 和默认模型从 `LLM_PRESETS` 表里取(和 cli.py 的 `_OPENAI_COMPAT_PRESETS` 同步)。隐式锁 `--provider=openai`,显式传不同 provider 会冲突报错。`--llm-base-url` / `--llm-model` 可以 per-field 覆盖 preset 默认。`docs/agent-install.md` 加 8 行示例(每家服务一行)
- **OpenAI 协议兼容子菜单 — 中转站(relay) 提到第 1 位 + 主菜单第 7 项 label 突出"中转站"** —— 复盘发现协议兼容选项的真正主流场景是"我买了中转站 / OneAPI Key,想用人民币付钱跑 OpenAI/Claude/国产模型"。之前菜单按"国产官方 → 自建 → 中转站 → Azure → 其它"排序,把最常见的中转站埋在第 7 个,普通用户得先翻过 5 个国产官方项才看到自己的选项。重排为:relay 第 1 位(default,带 ★ 标记 + "大多数人选这个"标注) → Kimi/MiniMax/Qwen/Zhipu/Yi 国产官方 → Azure → 自建 → custom 兜底。同步:主菜单第 7 项 label 改为"中转站 / OpenAI 协议兼容服务(OneAPI / 团队网关 / 国产官方 / Azure / 自建)";子菜单 intro 显式区分三类用户(中转站 / 国产官方 / 企业 Azure-自建);`docs/{openclaw-quickstart,docker-deployment,agent-install}.md` 同步重排表格 + 补"国内绝大多数中国用户选这个就对了"框架

---

## v0.3.26: LLM 计费模块 + 默认配置成本调优（2026-05-02）

新增本地 LLM 用量与花费追踪,顺手把 `config.example.toml` 里几个会让新装用户立刻烧钱的默认值改了。重启 daemon 后,跑 `openbiliclaw cost` 就能看每天实际花了多少。

### 新增

- **`openbiliclaw cost` CLI 命令** —— 显示最近 N 天 LLM 调用的按天 / 按 provider/model 分布,以及估算花费。每次成功 LLM 调用都会写一条到 `llm_usage` 表(timestamp / provider / model / caller / tokens / 估算单价)。`UsageRecorder` 是单点 hook,挂在 `LLMService.complete_with_core_memory` 之后,失败被吞,不影响业务热路径
- `src/openbiliclaw/llm/pricing.py` —— DeepSeek / OpenAI / Claude / Gemini / OpenRouter / Ollama 的 CNY 单价表,USD 系预乘 7.2 让账面统一。未知 provider 走通用 fallback 而不是静默 0
- `Database.insert_llm_usage` / `query_llm_usage_by_day` / `query_llm_usage_by_provider` / `query_llm_usage_total` —— 新表 `llm_usage` + 4 个查询方法,SQL 预聚合按日期/provider 分组
- `LLMService` 加可选 `usage_recorder` 字段 + `caller` 参数(预留给未来按模块归因);daemon 路径(`runtime_context`)自动注入

### 修改 default 值(影响新装用户)

- **`reasoning_effort = "max"` → `""`** —— 之前默认开启 thinking 模式,DeepSeek 每次按 32K tokens 预算计费,在 discovery 评估这种打分类高频小任务上完全没必要,日花费被放大 5-10x。新装从此不再被坑;旧用户 config.toml 不会自动改,需要手工编辑或删 `config.toml` 重新走 init
- **`discovery_cron = "0 */4 * * *"` → `"0 */8 * * *"`** —— 8 小时一次发现 vs 4 小时一次,LLM 评估调用减半,UI 上换一批的"新鲜度"基本无感(pool 始终保持 600 个候选)。需要更频繁可手工调回

### 测试

- `tests/test_llm_usage.py` —— 13 个单测覆盖 pricing 数学、DB round-trip、UsageRecorder 边界(sink=None / sink 抛错 / response 无 usage 字段等)

---

## v0.3.25: discovery 成本优化(reasoning_effort + pool-aware + batch_size)（2026-05-02）

针对 daemon 运行一天烧 ¥10-20 的问题,挖到三个真实成本源,逐一压平。综合下来日花费从 ¥21 降到 ¥0.5 左右。

### 修复 / 优化

- **discovery 内容评估 batch_size 从 10 升到 30** —— 评估器已经在批量调用,但默认 batch=10 导致每个策略 30 个候选要拆 3 次 LLM 调用,~3500 tokens 的 system prompt 重复付 3 次。升到 30(配合现有 `_EVALUATE_BATCH_HARD_CAP=30`)做到 1 次评估搞定一个策略,token 总量降 54%。`max_tokens` 同步从 8192 升到 16384 给输出留 10x 头空间。回归测试 `test_evaluate_content_batch_default_size_30_uses_single_llm_call` 钉死"25 候选 = 1 个 LLM 调用"
- **pool-aware refresh limit** —— `_requested_refresh_limit` 之前永远 floor 在 30,意味着 pool 在 595/600 时还要每个策略请求 30 个候选,然后 trim_pool_to_target_count 把多余的全标 suppressed。改成按 gap 缩放:`per_strategy_target = max(5, gap * 3 // 4)`,gap 小时请求小,直接省 50-77% 的 LLM 评估调用。生产数据(13 天 11K 缓存)证明 88% 评估都是花在被立即 suppressed 的内容上的浪费

### 影响

- 单纯改 default `reasoning_effort` 已经把日花费从 ¥21 降到 ¥3.5
- 配合 `discovery_cron 8h` + pool-aware sizing + batch_size=30,steady state 日花费降到 ¥0.5
- 可用 `openbiliclaw cost` (v0.3.26 新增) 实际验证

---

## v0.3.24: 跨源事件格式统一 + soul prompt 接入 context（2026-05-02）

把 B 站 / 小红书 / 扩展点击 / 反馈等所有事件源统一到一个 `build_event()` 构造器里,所有 LLM 消费者(preference / awareness / profile_builder)都看一份带自然语言 `context` 的标准化数据。

### 新增

- **`src/openbiliclaw/sources/event_format.py`** —— `build_event()` + `format_event_context()` 单点入口,所有 producer 都走它;`SOURCE_BILIBILI / SOURCE_XIAOHONGSHU / SOURCE_WEB` 常量
- **统一 shape**: `{event_type, title, url?, context: str, metadata: {source_platform, author, ...}}`,`context` 是中文一句话描述(如 "在B 站看了《讲透历史叙事》,作者:历史实验室"),LLM 直接读不需要 schema-aware 翻译

### 修改

- 所有事件 producer 重写走 `build_event`:`_history_item_to_event`、收藏、关注、`xhs_bootstrap_notes_to_events`、`/api/events`、`/api/feedback`、`/api/recommendations/{id}/click`
- `_summarize_history` 输出新增 `contexts` / `recent_contexts` / `older_contexts`,profile_builder prompt 加 rule 13 引导 LLM 优先用 context 理解行为
- preference / awareness 分析 prompt 加 rule 8/9/5 同样引导

### 修复

- **DB context 列双重 JSON 编码 bug** —— `insert_event` 之前 unconditional 把 string 也 json.dumps 包一层引号;LLM 看到 `\"内容\"`(triple-escaped 在 prompt 里);现在 string 直存,dict/list 才编码;`MemoryManager` 默认值 `{}` → `""`

### 测试

- `tests/test_event_format.py` —— 15 个测试覆盖 producer 一致性、round-trip 不再 double-encode、legacy dict 兼容
- `tests/test_profile_builder.py` —— 4 个测试覆盖新 contexts 输出 + B 站 raw history 自动合成 fallback

---

## v0.3.23: xhs 滚动改进 + 推荐管线小修补（2026-05-02）

- xhs `bootstrap_profile` 滚动型任务改为前台 tab 执行(后台 tab 在小红书上只渲染浅层 wrapper,触发不到完整瀑布流懒加载);非滚动任务保持后台
- 滚动容器探测从固定 `document/window` 升级为优先小红书 feed/waterfall/masonry 容器,排除零高度 wrapper 和 sidebar
- 收藏/点赞分组导入对齐开源实现:`profile.user.notes[1]` 收藏、`[2]` 点赞;profile state 解析补齐 `displayTitle` / `cover.urlDefault`

---

## v0.3.22: xhs init 数据真正进画像 + UX 反馈完善（2026-05-01）

`openbiliclaw init` 端到端审计后修复多个让小红书数据基本无效的 bug。

### 修复

- **CLI 等待 8s 太短** → 拆 enqueue/collect API,enqueue 在 B 站拉数据前发出,B 站拉数据期间扩展并行跑,等需要数据时通常已经好了。env var `OPENBILICLAW_XHS_BOOTSTRAP_WAIT_SECONDS` 默认 30s
- **`max_scroll_rounds=0` 硬编码** → 默认 3,env `OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS`;`max_items_per_scope` 20 → 50
- **5 种完成状态分别打反馈** —— ok / empty / timeout / failed / skipped 都给用户看得懂的中文消息;之前完成但 0 notes 的情况静默,现在会提示"扩展跑通但没拿到 notes(可能未登录小红书 / 个人主页没有公开收藏)"

### 测试

- `tests/test_cli.py` 加 3 个回归:`test_collect_xhs_bootstrap_events_status_branches`、`test_enqueue_xhs_bootstrap_task_uses_env_overrides`、更新已有 init 集成测试

---

## v0.3.21: 装机流程 docker / PowerShell / CLI 向导对齐 v0.3.20（2026-05-01）

v0.3.20 的 UX 改动只在 Bash + AI 智能体路径生效,Docker 部署文档 / Windows PowerShell 安装器 / 直跑 CLI 向导仍是旧契约——同一个项目三种说辞。本次对齐:

- `docs/docker-deployment.md` Phase 1 主推改成 DeepSeek 默认,Ollama 加 16GB+ 硬件门槛,自建网关挪到"高级"折叠节;Phase 3 embedding 改成"3 选 1 + 默认推荐"
- `scripts/install.ps1` 镜像 install.sh 的 D4 (cookie-only 绿字 backend ready) + B4 (REUSE_FROM 警告) 修复
- `cli.py` `_LLM_MENU` 重排:DeepSeek 第一,Ollama 第六加门槛,网关第七"(高级)";`_interactive_embedding_setup` 从 4 选 1 重写成默认 Ollama bge-m3 + Gemini 取舍 + follow + 2 个高级选项

---

## v0.3.20: 装机流程 UX 修复 + Embedding 自动 fallback（2026-05-01）

针对"一句话给智能体安装"流程从普通用户视角做了若干修复：3 个真 bug（Claude/DeepSeek/OpenRouter 主模型 + 跟随 LLM 的 embedding 静默失败、`base_url` 残留、复用旧 Key 无校验）和 5 个 UX 改进（主菜单去掉自建网关 / Embedding 改成"有默认值的取舍提问" / 状态块软化 / README 加 AI Agent 前置 / Ollama 加硬件门槛说明）。

### 修复

- **B1 真 bug**：`build_embedding_service` 现在用新增的 `LLMProvider.supports_embedding` 标志做 fallback，而不是脆弱的 `hasattr(provider, "embed")`。Claude / DeepSeek / OpenRouter 标记为 `False`（前两个没 embedding API、OpenRouter 路由覆盖不全）；OpenAI / Gemini / Ollama 标记为 `True`。当主 LLM 无 embedding 能力时自动回退到 ollama → gemini → openai 链中第一个能用的，而不是返回 `None` 让推荐管线在运行时炸。同时 `OpenAIProvider` 新增 `embed()` 走 `/v1/embeddings`，为之前 OpenAI 用户没显式配 embedding 时的同样静默 None bug 补上一刀
- **B1 配套**：`agent_bootstrap.py` 在主 LLM 是 Claude / DeepSeek / OpenRouter 且用户没显式传 `--embedding-*` 时，自动写 `[llm.embedding] provider="ollama" model="bge-m3"`，并把 `bge-m3` 加进 ollama 模型预拉清单，让首次装机就把模型拉好——不再"装完了才发现 embedding 没拉模型"
- **B2 真 bug**：`set_toml_string_value` 之前只更新不删除，从自建网关（option 4）切回 OpenAI 官方（option 2）会留 `base_url` 残留，请求继续打老网关。新增 `clear_toml_string_value` / `clear_config_value`；当 `--provider openai` 显式给出且 `--llm-base-url` 未给时，自动清空 `[llm.openai] base_url`，让 SDK 回到 `https://api.openai.com/v1`，并发 `base_url_reset` 事件
- **B4 提示**：`install.sh` 复用既有 checkout 的 API Key 时摘要里加一段 ⓘ 提示，说明复用 Key 不会做校验，401 时怎么用 `REUSE_FROM=` 跳过。复用本身保持原行为（无侵入），只把"信息可见性"从隐式抬到显式

### 体验

- **D1 / D3 主菜单**：`docs/agent-install.md` Step 1 把"OpenAI 协议兼容自建网关"从平级 4 选 1 移到 "Advanced" 折叠节，主菜单只剩 3 项；新主推改成 DeepSeek（¥0.001/千 token，几乎免费），Ollama 改回"完全离线 / 不要 Key"路径并明确加上 16GB+ 内存 / CPU 推理慢的硬件门槛——不再误导新手把 Ollama 当"零摩擦"
- **D2 Embedding 改成"有默认的取舍提问"**：早期版本是"三选一让用户读 200 字解释"，本次改 v1（完全隐藏）发现霸道，最终落地 v2 ——Step 3 仍然问，但每个选项有清晰的取舍说明 + 默认推荐"不确定就回 1"：① 本地 Ollama bge-m3（默认 / 免费 / 离线）② 云端 Gemini（质量更高 / 跨语言更稳 / 需要 Key）③ 跟随主 LLM。同时保留"用户跳过 / 选项 3 + 主 LLM 是 Claude/DeepSeek/OpenRouter"时 bootstrap 的自动写 Ollama 兜底，避免运行时静默失败
- **D4 状态文案**：`install.sh` 摘要在"只缺 B 站 Cookie"这种走扩展自动同步路径的预期状态下，不再打印黄字 `partial / credentials still missing`（普通用户读成"装失败了"），改为绿字 `backend ready — waiting for browser extension to sync B站 Cookie`，并把 Next steps 改成专门的扩展安装引导
- **D5 README 前置**：`README.md` / `README_EN.md` 在"复制粘贴给 AI 智能体一键部署"上方加 📌 前置说明——你需要先有 Claude Code / Codex CLI / Cursor / Windsurf 任一；没有的用户直接看下方"自己跑一句话装机脚本"，而不是被动卡在"AI 智能体是啥"上

### 测试

- `tests/test_llm_registry.py` 新增 4 个回归测试：`test_build_embedding_service_falls_back_when_claude_is_default`（Claude → Ollama 自动回退）、`..._when_deepseek_is_default`（同上，重点验证 DeepSeek 即便继承了 OpenAIProvider.embed 也会被 `supports_embedding=False` 排除）、`..._returns_none_with_no_capable_provider`（无可用 embedding provider 时 None 而不是崩）、`test_openai_provider_supports_embedding_flag_is_set`（六个 provider 的 supports_embedding 标志正确）

### 影响范围

- 修改文件：`src/openbiliclaw/llm/{base,openai_provider,openrouter_provider,gemini_provider,registry}.py`、`scripts/{agent_bootstrap.py,install.sh}`、`docs/agent-install.md`、`README.md`、`README_EN.md`
- 行为变化：之前 OpenAI 用户没显式配 embedding 也会静默返回 None；这次 OpenAI 用户会自动用 OpenAI 的 `text-embedding-3-small`，会少量计费。如果想省 quota 显式传 `--embedding-provider ollama --embedding-model bge-m3`

---

## v0.3.19: 初始化画像混入小红书信号（2026-05-01）

本次把小红书初始化画像导入接到现有事件层：`openbiliclaw init` 会继续拉 B 站历史 / 收藏 / 关注，同时 best-effort 等待浏览器插件执行 `bootstrap_profile` 任务，把小红书收藏、点赞和小红书页面内浏览记录信号混入首轮偏好分析与画像生成。

### 新增

- 后端 `XhsTaskQueue` 支持返回 task id 的入队方法，并新增 `xhs_bootstrap_notes_to_events()`：`saved -> favorite`、`liked -> like`、`xhs_history -> view`，metadata 统一带 `source_platform="xiaohongshu"`、`note_id`、`xsec_token`、`import_source` 和 `signal_strength`
- `/api/sources/xhs/task-result` 对 `bootstrap_profile` result 会缓存 notes、保留 task result，并把转换后的事件写入 memory event layer
- 插件新增 `src/content/xhs/bootstrap.ts`，从小红书页面已渲染 state 解析 scoped notes；后台 dispatcher 识别 `bootstrap_profile`，先打开 `/explore` 找当前登录用户的 profile URL，再在同一 tab 跳到个人主页读取 `user.notes` 分组
- 收藏 / 点赞导入对齐开源实现：profile 页 `user.notes` 的 `[1]` 作为收藏、`[2]` 作为赞过；如果分组尚未加载，插件会点击 profile 页对应 tab 等待页面自己补齐 state
- profile state 解析补齐小红书 noteCard 字段：`displayTitle`、`user.nickName`、`cover.urlDefault`；受控滚动每轮会合并 state + DOM，再发送新增 partial，减少虚拟列表导致的漏采
- `bootstrap_profile` 支持显式 `max_scroll_rounds` 的受控滚动；content script 会把首批和滚动新增 notes 以 `status="partial"` 分批回传，background 等后端 `/task-result` 确认后再继续滚动，最后用 `status="ok"` 完成任务
- 滚动型 `bootstrap_profile` 会以前台 tab 打开 `/explore`，由 content script 在页面内点击导航栏“我”进入 profile；background 收到 `next_url_clicked=true` 后不再 `tabs.update(profileUrl)`，只等待同一 tab 导航完成并重新下发任务，避免直接跳 profile 触发验证码。不滚动任务仍保持后台执行；只有找不到可点击入口、只能从 state 推出 profile URL 时才回退到直接导航
- profile 二次执行前会等待小红书 React 页面真正渲染出 profile state、收藏/赞过 tab 文案或 note 卡片，避免 `tabs.onUpdated complete` 早于页面内容加载时直接返回 0 条
- 后端任务 payload 可控制滚动节奏：`scroll_wait_ms` 控制每轮滚动后的停留等待，`max_stagnant_scroll_rounds` 控制连续无新增多少轮后停止；插件端会做上下限裁剪，dispatcher 会按更长等待放宽任务 timeout
- 滚动 partial 批次现在会按 `max_items_per_scope` 的剩余名额裁剪，避免最后一轮页面一次新增多条时分批回传超过 scope 上限
- profile 滚动目标从固定 `document/window` 升级为优先探测小红书 feed / waterfall / masonry 容器，并排除零高度、`overflow-y` 非滚动式的普通 wrapper 和 `channel-list` / sidebar 这类非内容侧栏；没有内容容器时会退回到窗口级小步 `wheel` / `scrollBy`，贴近用户手动前台滚动。debug 会同时记录排名靠前的 `scroll_candidates` 和每轮 target、scrollTop、scrollHeight、clientHeight、before/after top、新增数，便于判断是否真正触发瀑布流加载
- `openbiliclaw init` 会把 XHS bootstrap 事件加入 `SoulEngine.analyze_events()` 的同批输入，并把对应 notes 追加到 `build_initial_profile()` 的 history

### 约束

- 后端仍不直接登录、爬取或调用小红书私有接口；小红书数据只来自用户浏览器里的插件
- `xhs_history` 指小红书网页自己明确暴露的浏览记录/足迹 state，不是读取 Chrome browser history；普通 `/explore` 推荐流不会再被当成浏览记录导入
- 收藏、点赞、浏览记录三个 scope 都是 best-effort：插件未连接、未登录或页面不暴露数据时，初始化继续使用 B 站数据完成；滚动也只在任务显式请求时启用

### 测试

- `tests/test_xhs_tasks.py`
- `tests/test_api_xhs_ingest.py::TestXhsTaskResults::test_xhs_bootstrap_task_result_records_events`
- `tests/test_api_xhs_ingest.py::TestXhsTaskResults::test_xhs_bootstrap_partial_results_accumulate_until_final`
- `tests/test_cli.py::test_init_includes_xhs_bootstrap_events`
- `extension/tests/xhs-task-executor.test.ts`
- `extension/tests/xhs-task-dispatcher.test.ts`

---

## v0.3.18: 把 franchise_key 升成一等字段，撤掉 v0.3.17 的标题黑名单（2026-04-30）

v0.3.17 用了**硬编码 IP 别名表 + 标题子串匹配**做 franchise 判定。社区反馈说这种黑白名单做法在长期不可持续——覆盖不全、人工维护成本高、对 LLM 编出新写法（"提瓦特 重制"、"原神 4.5 须弥"）容易漏判或误判。这次撤掉，改成**让 LLM 在内容评估阶段直接打 IP 标签**，作为 `content_cache` 的一等字段持久化。

### 撤掉的

- `src/openbiliclaw/recommendation/franchise.py`（13 个 IP 的硬编码 alias 表 + `extract_franchise()` heuristic）
- `tests/test_franchise.py`
- `_FEEDBACK_DISLIKE_FRANCHISE_PENALTY` 在 curator 里依然保留，但实现底盘换了

### 新增的：`franchise_key` 作为一等字段

**Schema**（`storage/database.py`）：

- `content_cache` 表新增 `franchise_key TEXT DEFAULT ''` 列
- `_ensure_content_cache_topic_columns()` 加 `ALTER TABLE` 迁移，老库无痛升级
- `cache_content` INSERT/UPDATE 把 `franchise_key` 纳入，`COALESCE(NULLIF(excluded.x, ''), content_cache.x)` 模式——避免被 0 值覆盖
- `get_recommendations` SELECT 多带 `c.franchise_key` 出来，给 API dedup 用
- `get_feedback_signals` SELECT 多带 `c.franchise_key`，给 curator dislike 传播用

**LLM prompt**（`llm/prompts.py`）：

`build_batch_content_evaluation_prompt` + 单 item 评估的 prompt 都加了 franchise_key 字段：

```
7. franchise_key 规则：内容如果明确属于某个具体 IP / 系列 / 作品 / 品牌，
   填它的规范名（中文优先），用于跨 topic_group 的同 IP 去重。例：
   - 「AI 重绘原神地图」「提瓦特摄影」「蒙德角色真实化」 → "原神"
   - 「星穹铁道 1.6 实战」「崩铁 角色养成」 → "崩坏:星穹铁道"
   - 「ChatGPT 工作流」「OpenAI 新模型」 → "ChatGPT"
   - 「番茄炒蛋 5 分钟教程」 → ""（一般科普 / 美食 / 通用资讯都填空字符串，不要硬凑）
   - 同一 IP 必须用相同写法。
```

LLM 已经看了 title + description + topic + style，让它顺手再标一个 IP 几乎零额外延迟。比 heuristic 准很多——「提瓦特摄影」这种隐性引用 LLM 能识别，硬编码表照不到。

**Pipeline**（`discovery/engine.py`）：

- `DiscoveredContent` 新增 `franchise_key: str = ""` field
- `to_cache_kwargs()` 把它带过去
- `_evaluate_batch` 解析 LLM 响应里的 `franchise_key`，写入 `content.franchise_key` + 评估缓存元组
- 缓存元组从 4-tuple 升到 5-tuple，老 4-tuple 兼容降级（绕过升级期 in-flight 进程崩溃）
- `evaluate_content`（单 item 版）同步处理

**Curator**（`recommendation/curator.py`）：

- `FeedbackSignals.disliked_franchises` 来源换成 `row.get("franchise_key")`（DB 里的真值），不再从 title 提
- `_feedback_adjustment` 比较 `item.franchise_key`（也是 DB 里的真值），不再调 heuristic 抽取
- 罚分常量保留 0.07（heuristic vs LLM 不影响这个值的合理性）

**API**（`api/app.py`）：

- `_cap_by_franchise()` 内联在 app.py，按 row 的 `franchise_key` 列做窗口内去重，不依赖标题
- 空 `franchise_key` 永远透传——一般内容不被限流

### 测试

- `tests/test_pool_curator.py` 新增 3 个：`disliked_franchises={"原神"}` 时，candidate `franchise_key="原神"` 扣分；`franchise_key="塞尔达传说"` 不扣；`franchise_key=""` 不扣（保护 LLM 还没标的内容）
- `tests/test_api_app.py` 新增 2 个：`_cap_by_franchise` 单元测；`/api/recommendations` 端到端——5 条 `franchise_key="原神"` 行 + 1 条 `""`，响应里只剩 2 条原神 + 番茄炒蛋

### 致谢

社区反馈「不要做黑白名单」，方向完全正确。把 franchise 升成一等字段是正解——后续还能让 `RelatedChainStrategy` 按 `franchise_key` 限制同 IP 链路深度、让 SQL 层 `trim_topic_group_overflow` 多加一个轴，全都靠这一列展开。

---

## v0.3.17: 修推荐流过度泛化 IP（一屏 5 条原神 / 提瓦特）（2026-04-30）

社区报告：点了一条「AI 重绘原神地图」之后，推荐弹窗连续出 5 条原神 / 提瓦特 / 蒙德视频。深度分析定位了 5 个层级的问题，本次先修最影响视觉体验的 3 个：

### 根因（社区分析，全部代码验证过）

1. **正反馈泛化过强**：单次 `recommendation_click` 就能让 PreferenceAnalyzer 把「原神」写入 `interests` 权重 0.6（在 `preference.json` line 348 实际命中）
2. **负反馈泛化不足**：点踩某条原神视频只记 `topic_key` 级 dislike，原神这个 IP 不会被降权（`curator.py:130-148` 验证）
3. **多样性维度太粗**：当前用 `topic_group` 限流，但同一 IP 被 LLM 拆到「游戏」「游戏动漫」「人工智能」「游戏摄影」「游戏盘点」5 个 group，绕过限流（`engine.py` 验证）
4. **`/api/recommendations` 无最终去重**：`LIMIT 20 ORDER BY DESC`，5 条原神在前则全数透传（`app.py:606`）
5. **`related_chain` 缺 IP 上限**：只按 seed_index 限流，沿原神 seed 滚 5 个邻居 = 全是原神（`related_chain.py:159` 验证）

### 本版本修复（focused subset）

新增 `src/openbiliclaw/recommendation/franchise.py`：基于标题的 heuristic franchise 提取器。预置 13 个高频 IP 的 alias 表（原神 / 星穹铁道 / 崩坏 3 / 绝区零 / 鸣潮 / 明日方舟 / 黑神话 / 塞尔达 / 我的世界 / Apex / 英雄联盟 / ChatGPT / DeepSeek），中文别名走子串匹配，英文走 `\b` 词边界（避免「lol」匹配普通笑反应）。

接入 2 个点：

1. **`/api/recommendations` 最终去重**（fix 根因 #4）：拉 40 条候选，调 `dedup_by_franchise(max_per_franchise=2)` 限同一 IP 在窗口里最多出现 2 次，再截到 20 返回
2. **Curator 的 `disliked_franchises` 集合**（fix 根因 #2）：`PoolCurator.build_context` 现在在处理 dislike 反馈时，从被踩 item 的 title 提取 franchise 加入 set；`_feedback_adjustment` 对 title 命中同 franchise 的候选扣 `_FEEDBACK_DISLIKE_FRANCHISE_PENALTY = 0.07`（比 topic 软一档，避免一条踩永久封 IP）

`storage/database.py` 的 `get_feedback_signals` 同步加 `c.title` 到查询，因为 franchise 提取需要 title。

### 没修的（留作后续）

- 根因 #1（点击 → IP 兴趣过度强化）：需要改 PreferenceAnalyzer 的 prompt 或加 TTL/最小确认次数
- 根因 #3（topic_group 多样性维度太粗）：需要在 content_cache 加 `franchise_key` 字段并由 LLM 评估时填，配合 SQL 限流
- 根因 #5（related_chain IP 上限）：同上，需要 `franchise_key` 才能在 strategy 内部限

这三个的正解都是把 franchise 上升为一等字段（DB column + LLM tag），而不是停留在 title heuristic。本次先用 heuristic 解掉用户最直接看到的问题，franchise_key 字段方案随后规划。

### 测试

- `tests/test_franchise.py`（10 个）：原神 / 提瓦特 / 蒙德 / 枫丹 / Genshin 都映射到同一 canonical key；`lol` 不会误匹配；多 franchise 时按声明顺序取首；无 franchise 的内容直接透传
- `tests/test_pool_curator.py` 新增 2 个：disliked_franchises 含「原神」时，「提瓦特摄影集锦」（不同 topic_key + 不同 up_mid）扣分；`塞尔达` 不会被殃及

### 编码乱码风险

社区还提到部分 B 站标题在数据库里有编码迹象，可能导致关键词过滤不稳。**这次没动**——但 v0.3.14 修过 memory JSON 的 GBK→UTF-8，方向类似。如果用户能复现具体的乱码字段，可以再开 issue 单独修。

### 致谢

社区诊断质量极高：5 个根因 + 5 个具体行号 + 5 个修复建议，本次修复完全按照其中可执行子集落地。

---

## v0.3.16: README 推荐顺序调整 + 多源登录前置说明（2026-04-30）

两个 README/安装文档层面的调整，没动代码：

### 1. README 后端安装方式重排：一句话装机优先，桌面包后置

之前两份 README 都把「下载后端桌面包」放第一位，「AI 一句话装机」第二位，「自己跑脚本」第三位，「Docker」混在中间。但首版桌面包未签名，会触发 macOS Gatekeeper / Windows SmartScreen，对普通用户其实最不友好。新顺序按「实际可用度」排：

1. **首选**：让 AI agent 跑 `agent-install.md`（零摩擦，agent 把 LLM/Embedding/Cookie 都问全 + 自动跑 init）
2. **或**：AI agent + Docker（v0.3.11+ 自带 Ollama embedding sidecar）
3. **或**：自己跑 `install.sh` / `install.ps1`（同一份脚本）
4. **末位**（折叠在 `<details>` 里）：下载未签名桌面包，要点「右键 → 打开」绕过 Gatekeeper

### 2. README 增加「多源登录前置」段

很多用户装好扩展后发现「为什么没有小红书内容？」——原因是后端不爬小红书，发现/详情都靠扩展在用户登录态的浏览器里跑。新增一张表，明确每个源的登录要求 + 不登录的后果：

| 源 | 登录方式 | 不登录的后果 |
|---|---|---|
| B 站 | 浏览器登录 https://www.bilibili.com（v0.3.12+ 扩展自动同步 Cookie） | 拉不到历史/收藏/关注，画像缺失，推荐降级为公共热门 |
| 小红书 | 浏览器登录 https://www.xiaohongshu.com | **完全没有小红书内容**（后端不直接抓） |
| 通用 Web 源 | 该站点正常登录 | 同上 |

并强烈推荐小红书用 CDP 模式 Chrome 复用登录态（`--remote-debugging-port=9222` + `[sources.browser] cdp_url`），避免反爬。

`docs/docker-deployment.md` 也加了同样的多源登录前置段，并把 CDP url 改成 `host.docker.internal:9222`，方便容器访问宿主机的 CDP 端口。

### 3. README_EN 同步翻译

两份 README 严格一致。
---

## v0.3.15: 一连串 Windows 装机踩坑修复 + Ollama embedding-only 不应做 chat fallback（2026-04-30）

社区反馈了一组 Windows 原生路径的坑，集中修复：

### 1. CLI 在 GBK 控制台打 emoji 直接崩

`openbiliclaw init` 开场打的「⏱」在简体中文 Windows 默认 GBK 控制台触发 `UnicodeEncodeError: 'gbk' codec can't encode character '⏱'`。修复：在 `cli.py` 顶部加 `_force_utf8_stdout_on_windows()`：

- `os.name == "nt"` 时设 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`（这俩对子进程也生效）
- 用 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` 把流的 codec 换成 UTF-8 + 替换错误处理

POSIX 上完全是 no-op。`errors="replace"` 是最后一道兜底——即使有少数字符译不动，也只会显示 `?` 而不是崩溃。

### 2. install.ps1 的 `python -c '...f"{...}"...'` 在 PS 5.1 下被剥引号

PowerShell 5.1 把单引号 PS 字符串里的内嵌 `"..."` 传给 native command 时会丢内层引号。结果 `python -c 'print(f"{x}.{y}")'` 实际执行 `python -c print(fx.y)` → SyntaxError → 安装器误报「Python 3.11+ is required」。

修复：去掉 f-string 和内嵌引号，用 `print(sys.version_info[0], sys.version_info[1])`，输出 `3 11` 用空格切分。Python 端不再有 `f"..."`，PS 5.1 引号 bug 触发不到。

### 3. Bash 在 Windows 上误踩 WSL

`docs/agent-install.md` 让 AI agent 在 Windows 跑 `curl ... | bash`，但 Windows 上 `bash` 默认指向 `C:\Windows\System32\bash.exe`（WSL 启动器）。WSL 没装时报 `execvpe(/bin/bash) failed: No such file or directory`。

修复：agent-install.md 加显眼警告，告诉 AI agent 在 Windows 默认走 PowerShell；如必须用 bash，显式调 `& "C:\Program Files\Git\bin\bash.exe" -c "..."`。

### 4. 后端 Ollama embedding-only 注册不应进入 chat fallback chain

最严重的一个：用户日志里出现 `All providers failed (openai, ollama). Last error: ollama request failed: 404 page not found`。根因——`[llm.embedding] provider="ollama"` 触发 `_maybe_ollama_provider` 注册一个仅有 `bge-m3`（embedding 模型）的 Ollama provider。`LLMRegistry.register()` 不区分 chat/embedding 用途，主 provider 失败时 fallback chain 把它当成 chat provider 用，打 `/api/chat?model=llama3` → 404，还把 404 误归因「fallback 也挂了」。

修复：

- `LLMRegistry.register()` 加 `chat_capable: bool = True` 参数 + 内部 `_chat_disabled` 集合
- `_fallback_order()` 跳过 `_chat_disabled` 里的 provider
- `build_llm_registry()` 调 `_ollama_is_chat_capable(config)` 判定：用户必须在 `[llm.ollama] model` 显式给了 chat 模型，或把 ollama 设成默认/任一模块的 provider，否则视作 embedding-only，注册时传 `chat_capable=False`

回归测试：

- `tests/test_llm_registry.py::test_embedding_only_ollama_is_excluded_from_chat_fallback` —— 模拟「主 OpenAI 挂了 + Ollama 只配了 embedding」场景，断言 chat 链里**没有** ollama，断言主 provider 的错误如实抛出（不会再被「ollama 也挂了」掩盖）
- `test_ollama_with_explicit_chat_model_is_chat_capable` —— 反向验证：用户给了 `[llm.ollama] model="llama3"` 时，Ollama 仍然在 fallback 链里，符合预期

### 5. UTF-8 持久化（v0.3.14 已修，这里只是关联引用）

社区报告里同时提到 `MemoryLayer.load/save` 没指定 UTF-8 ——**已经在 v0.3.14 修了**，这里不重复。

### 致谢

非常感谢社区的细致复现 + 系统性总结。一份报告解锁四个独立 bug + 一个架构问题，PR 级质量。

---

## v0.3.14: 修 Windows GBK 默认编码导致接口 500（2026-04-30）

社区反馈在简体中文 Windows 上后端用默认 GBK locale 启动时，扩展请求 `/api/delight/pending-batch?limit=20`、`/api/activity-feed?limit=10` 等接口都会返回 500，根因是 `MemoryLayer.load()` / `save()` 在 `src/openbiliclaw/memory/manager.py` 用了不带 `encoding=` 的 `open()`：

```python
with open(self.storage_path) as f:        # ← 没指定编码
    self._data = json.load(f)             # GBK 解码 UTF-8 文件 → 报错
```

`/api/health` 是常量字符串、不读 memory 文件，所以仍然 200——bug 只在业务接口现身。

### 修复

- `MemoryLayer.load()` / `save()` 显式 `encoding="utf-8"`
- `BilibiliAuthManager.load_cookie()` / `_save_cookie()` 也补上（cookie 当前是 ASCII 不受影响，但同样不该依赖平台默认编码）
- 项目里其他文本模式 `open(...)` 全部 audit 过——`config.py` 的两处用 `"rb"` 走 `tomllib`，正确；其余都已经显式 UTF-8

### 回归测试

`tests/test_memory_manager.py::test_memory_layer_load_uses_utf8_even_when_default_locale_is_gbk`：

通过 monkeypatch `builtins.open`，让任何不带 `encoding=` 的 text-mode 调用回退到 GBK——精准模拟简体中文 Windows 的默认行为。验证：

- `MemoryLayer.load()` 仍能正确读取含中文 + emoji 的 UTF-8 文件
- `MemoryLayer.save()` 也不会触发 `UnicodeEncodeError`
- 文件最终仍是合法 UTF-8

撤回 `manager.py` 的 fix 时，这个测试会精确报出 `UnicodeDecodeError: 'gbk' codec can't decode byte 0x80`——和 prod 复现的错误一字不差。

### 致谢

非常感谢社区报告——bug 摘要、根因定位、修复思路、本地验证全跑通，整理得非常清楚，PR 级别的报告。

---

## v0.3.13: 各种安装路径都把「装扩展自动同步」放到 Cookie 步骤的首选（2026-04-30）

v0.3.12 加了扩展自动同步 Cookie，但各个安装路径的引导（向导 / 文档 / install.sh / install.ps1）都还按 F12 那套老流程在问。新用户根本不知道有更简单的路径，结果还在手动贴 Cookie。

修了 5 处：

- **`scripts/install.sh`** 状态块缺 `bilibili.cookie` 时，先打印 `(A) [recommended] Install the browser extension and let it auto-sync` 教程 + 链接，再列 `(B) F12 五步` 兜底
- **`scripts/install.ps1`** 同样的 (A)/(B) 二选一引导
- **`docs/agent-install.md` Step 4** 完全重写：明确告诉 AI agent 默认走扩展路径，不再上来就让用户 F12；如果用户选扩展，agent 不传 `--bilibili-cookie`，让 bootstrap 走 `running_with_missing_secrets` 状态，再告诉用户「装扩展，等同步」，最后再让 agent 自己跑 `openbiliclaw init`
- **`src/openbiliclaw/cli.py` 的 `_interactive_auth_setup`** 改成 2 选 1：1) 装扩展自动同步（默认，选了直接 `typer.Exit(0)`，提示之后扩展同步好再跑 `openbiliclaw init`） 2) 现场手贴
- **`docs/docker-deployment.md` / `docs/openclaw-quickstart.md`** 同步把扩展放到 Cookie 步骤的首选

效果：装扩展是默认路径，F12 是「死活不想装扩展」时的兜底。agent-install.md 给 AI agent 的指令也变了：默认不要追问 Cookie，鼓励用户装扩展，扩展同步完后续 init 就齐活了。

---

## v0.3.12: 浏览器扩展自动同步 B 站 Cookie 到后端，再也不用 F12（2026-04-30）

之前用户配 B 站 Cookie 必须自己 F12 → Network → 复制 Cookie 头 → 粘到向导里。这个体验对刚接触本项目的人极不友好，而且 Cookie 过期/刷新后还得重做。其实扩展本来就跑在 bilibili.com 上，能直接读用户的 Cookie，把这个流程自动化是天然的。

### Backend：新增 `POST /api/bilibili/cookie`

在 `src/openbiliclaw/api/app.py` 加了一个端点，接收扩展推过来的 Cookie：

1. **校验**：先用 `AuthManager.validate_cookie` 打一次 `api.bilibili.com/x/web-interface/nav`，确认 Cookie 真的处于登录状态——避免无效 Cookie 覆盖一个还在工作的旧 Cookie
2. **持久化**：写到 `data/bilibili_cookie.json`（运行时真正用的源）+ `config.toml` 的 `[bilibili].cookie`（镜像，给 `config-show` 用）
3. **热重载**：调 `RuntimeContext.rebuild_from_config` 原子换掉 BilibiliAPIClient，下一次 API 调用就用新 Cookie
4. **广播**：通过 WebSocket runtime-stream 发 `bilibili_cookie_synced` 事件，扩展 popup 可以停掉「请登录」提示

请求 model 在 `api/models.py` 新增：`BilibiliCookieIn`（`cookie`, `source`, `validate_with_bilibili`）+ `BilibiliCookieResponse`（`ok`, `authenticated`, `username`, `user_id`, `message`）。

### Extension：自动读 + 推

`extension/src/background/cookie-sync.ts` 新文件，service-worker 启动时挂上：

- **触发场景**
  - `chrome.runtime.onInstalled` / `onStartup` → 启动一次同步
  - `chrome.cookies.onChanged` 监听器（domain 收尾匹配 `bilibili.com`）→ 用户登录/登出/Cookie 刷新立即同步。debounce 2s 避免一次登录触发 6-10 次 POST
  - 每小时一次 alarm 兜底（防止 service worker 卸载期间漏掉 onChanged 事件）

- **只推有意义的 Cookie**：`SESSDATA` / `bili_jct` / `DedeUserID` 三件套缺一不发，避免后端做无谓的 nav 校验

- **只在用户登录时推**：未登录直接 `return false`，不打扰后端

`manifest.json` 加 `cookies` 权限 + 版本 0.3.1 → 0.3.2。

### 安全模型

- 后端默认绑 `127.0.0.1`，外网摸不到这个端点
- Cookie 全程在用户本机：浏览器 → service worker → localhost backend → 本地磁盘
- CORS 现状是 `*`，对 localhost 后端来说没意义（任何打到 127.0.0.1 的请求本来就来自本机）
- 用户改成 `--host 0.0.0.0` 应该自己加 auth 层（这是历史 stance，没改）

### 用户感知

- 装好扩展 → 几秒内自动同步 → 后端日志看到 `cookie_synced`，`/api/runtime-status` 返回登录态
- Cookie 过期了？扩展会在下次 `chrome.cookies.onChanged` 自动推新的，无需手动操作
- 一句话装机的 wizard 里仍保留 cookie prompt 作为兜底，给不装扩展的用户用

---

## v0.3.11: Docker 自带 Ollama embedding sidecar + CLI 向导也能自动装 Ollama（2026-04-30）

v0.3.10 把一句话装机（install.sh / install.ps1 → agent_bootstrap.py）的 Ollama 自动安装做齐了，但还有两条路径漏了：

1. **Docker 模式**：用户跑 `docker compose up -d --build` 后，embedding 段默认空着，第一次发请求才发现「咦，需要个 embedding API key 或一个 host 上跑的 Ollama」
2. **手动安装** + 直接跑 `openbiliclaw init`：CLI 向导只会检测 Ollama，没装的话提示用户去装，没启用「我帮你装」

### 1. `docker-compose.yml` 多了 `ollama` sidecar

```yaml
services:
  ollama:
    image: ollama/ollama:latest
    # 启动时拉 bge-m3，daemon 一直跑
    # healthcheck 等到 bge-m3 就绪才报 healthy
  openbiliclaw-backend:
    depends_on: { ollama: { condition: service_healthy } }
    environment:
      OPENBILICLAW_SEED_OLLAMA_DEFAULTS: "1"
      OPENBILICLAW_OLLAMA_BASE_URL: "http://ollama:11434/v1"
      OPENBILICLAW_EMBEDDING_MODEL: "bge-m3"
volumes:
  openbiliclaw_ollama:  # bge-m3 持久化，重建容器不重拉
```

### 2. `docker_runtime.py` 启动时按 env 自动写 embedding 默认

`bootstrap_runtime_root` 复制 `config.example.toml` 到 volume 后，如果 `OPENBILICLAW_SEED_OLLAMA_DEFAULTS` 为真，就把这三个值填进去：
- `[llm.ollama] base_url = http://ollama:11434/v1`
- `[llm.embedding] provider = ollama`
- `[llm.embedding] model = bge-m3`

已有的 `config.toml` 不会被覆盖——用户改过的偏好都会保留。

效果：用户跑 `docker compose up -d --build` 后，**只需要一个 chat 模型的 API Key**，embedding 完全免费 + 离线 + 用完即走。第一次启动多花 2–4 分钟下载 bge-m3（~568MB），后续从 named volume `openbiliclaw_ollama` 直接复用。

不要 sidecar 的用户：把 `docker-compose.yml` 的 `ollama` 服务块和后端的 `OPENBILICLAW_SEED_OLLAMA_DEFAULTS` env 删掉就行。

### 3. CLI 向导（`openbiliclaw init` 直接跑）也支持自动装 Ollama

新增两个 helper：
- `_ollama_install_if_missing()`：检测 → 询问用户 → brew/winget/install.sh
- `_ollama_start_serve_background()`：后台启动 daemon，轮询 `/api/version` 等 15s

Phase 1（选 Ollama 做 chat）和 Phase 3 选项 2（选 Ollama 做 embedding）都接入了这套：用户不再需要先去外面装 Ollama，向导一条龙搞定。

---

## v0.3.10: 选 Ollama 时一句话装机自己装 Ollama + 拉模型（2026-04-30）

v0.3.6 把 Ollama 推荐成「新手默认」选项后，新问题来了：用户在向导里选了 Ollama，但实际上还得自己 `brew install ollama` / 装 Windows 安装包 / 跑 install.sh，再 `ollama pull llama3` —— 否则后端启动会卡在「Ollama not running」。这彻底违反了「一句话装机」的承诺。

`agent_bootstrap.py` 现在内置 4 阶段 Ollama 自动化：

1. **检测**：`shutil.which('ollama')` 找二进制
2. **安装**（如果没装）：
   - macOS → `brew install ollama`（没 brew 时报错并给出 https://ollama.com/download）
   - Windows → `winget install -e --id Ollama.Ollama`（自动接受 EULA；没 winget 时报错给 URL）
   - Linux → `curl -fsSL https://ollama.com/install.sh | sh`（官方脚本自带 systemd 配置）
3. **启动 daemon**（如果没在跑）：后台 spawn `ollama serve`，轮询 `/api/version` 等最多 15s
4. **拉模型**：检查 `/api/tags`，没拉的就 `ollama pull <name>`，进度流式打到 stdout

每个阶段单独发 `BootstrapResult` 事件（`ollama_installed` / `ollama_serving` / `ollama_model_pulled`），AI agent 解析 JSON 流就能精确知道卡在哪一步。最后还会发一个汇总 `ollama_ready` 事件。

触发条件：`--provider ollama` 或 `--embedding-provider ollama` 任一为真，且 `mode != docker`（Docker 模式下后端走 `host.docker.internal:11434` 找宿主 Ollama，自动装到容器内是错的）。新增 `--skip-ollama-setup` 给想自己管 Ollama 的用户兜底。

`docs/agent-install.md` 同步：Option 1（Ollama）的指引从「让用户自己装」改成「我会帮你装」，embedding 段也明确告诉 AI agent 不要让用户手动 `ollama pull bge-m3`。

---

## v0.3.9: 一句话装机适配 PowerShell 5.1（Win10/Win11 默认）（2026-04-30）

之前的 `iwr <url> | iex` 一句话在 Windows 10 / 11 上没装 PowerShell 7 的用户那里直接挂——PS 5.1 默认走 TLS 1.0/1.1，但 GitHub 现在只接受 TLS 1.2+，握手失败报「underlying connection was closed」，新手根本看不懂。

修了 4 件事：

1. **README.md / README_EN.md / docs/agent-install.md 一句话命令前缀加 TLS 1.2 设置**：
   ```powershell
   [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; iwr https://...install.ps1 -UseBasicParsing | iex
   ```
   PS 7+ 用户可以省掉前缀；PS 5.1 用户必须带

2. **`scripts/install.ps1` 自身启动时也设一次 TLS 1.2**：脚本一旦开始跑，后续的 git clone / pip / uv / Invoke-WebRequest 都覆盖到了

3. **修 `?? '' ` 这个 PS 7-only 语法**：line 281 用的 null 合并操作符 PS 5.1 不支持，改成显式 `if ($null -ne $ReuseFrom) { $ReuseFrom } else { '' }`

4. **`scripts/install.ps1` 的 .EXAMPLE 注释拆成 PS 5.1 / PS 7+ 两个示例**，让用户一眼能看出哪个对应自己

`#requires -Version 5.1` 已经在文件顶部，但 PS 解析器只在脚本开始执行时检查它，对脚本下载阶段（外面那个 iwr）无能为力，所以下载阶段必须靠用户预先设好 TLS。

---

## v0.3.8: init 启动前明确告诉用户预计用时（2026-04-30）

v0.3.7 把 init 自动跑了起来，但用户看到屏幕静默几十秒就开始怀疑「是不是卡了？」。这次给 `init` 加了一段开场白，跑之前明确告诉用户：

```
⏱  这一步首次运行预计需要 2–5 分钟，请保持网络畅通别中断。
  四个阶段会依次跑：
    1/4  拉 B 站历史 / 收藏 / 关注（≈ 20–60s，看你的列表大小）
    2/4  分析偏好（LLM 调用，≈ 30–90s）
    3/4  生成灵魂画像（LLM 调用，≈ 30–60s）
    4/4  发现首轮内容池（多策略并发 + LLM 评估，≈ 1–3 分钟）
全程会打印进度，不要以为卡住了——LLM 单次响应可能就要 10–30s。
```

每个阶段的耗时区间是按官方云模型（GPT-4o-mini / Gemini Flash）+ 国内网络估的；本地 Ollama 会更慢，看用户机器。

---

## v0.3.7: 一句话装机配齐凭据后自动跑 init（2026-04-30）

v0.3.6 的人机界面虽然好了，但有个流程漏洞：用户给完凭据后，AI agent 按文档照做加上了 `--skip-init`，结果装机流程在「config 写好、健康检查通过」就停了。**用户打开扩展看不到任何东西**——画像没生成、历史没拉、首轮内容池是空的，需要再手动跑一遍 `openbiliclaw init`。这彻底违反了「一句话装机」的承诺。

### 修复内容

1. **`docs/agent-install.md` Hard Rule 第 3 条彻底反转**：原来是「Never run `openbiliclaw init` unless the user explicitly asks」，新版是「Run init by default — DO NOT pass `--skip-init`」。给 AI agent 的指令非常明确：凭据齐了就让 init 自动跑

2. **示例命令删除 `--skip-init`**：`docs/agent-install.md` 里两个示例都不再带这个 flag

3. **`agent_bootstrap.py` 的 auto-init 逻辑修了三个 bug**：
   - 之前 venv python 路径硬编码 `.venv/bin/python`（POSIX），Windows 上找不到——改成按 `os.name == "nt"` 选 `.venv/Scripts/python.exe` 或 `.venv/bin/python`
   - Docker 模式之前不跑 init——新版用 `docker exec -i openbiliclaw-backend openbiliclaw init` 在容器里跑
   - 兜底从 `python3` 改成 `sys.executable`，更可靠

4. **`install.sh` / `install.ps1` 状态块加一段说明**：
   ```
   This auto-runs 'openbiliclaw init' once credentials check out:
     - pulls your Bilibili history
     - generates the soul profile
     - runs the first content discovery pass
   Takes 2-5 minutes. Without this step the extension shows nothing.
   ```
   还在 follow-up 命令旁边加了「DO NOT add --skip-init」提示，避免 AI agent 按惯性加上这个 flag

5. **agent-install.md 增加「报告最终状态」清单**：AI agent 装完后必须告诉用户：
   - ✅ 后端已启动
   - ✅ 配置已写入
   - ✅ 初始化已完成（拉历史、生成画像、跑发现）
   - 👉 下一步：装浏览器扩展

   并提示用户 init 首次运行需 2-5 分钟，避免被以为「卡住了」

---

## v0.3.6: 装机向导从普通用户视角彻底重写（2026-04-30）

v0.3.5 的向导虽然问全了，但顺序、措辞和默认都不够友好。基于线上 AI agent 实际跑出来的提问被反馈「太差」，v0.3.6 整个人机界面重写：

### 1 — Ollama 排第一，不再把 OpenAI 当默认

之前 `default="openai"`，但 OpenAI 是收费的、要去申请 Key 才能用，对刚接触本项目的用户极不友好。v0.3.6：

- 菜单第一项是 **本地 Ollama**（免费 / 离线 / 无需 API Key），明确标注「推荐新手」
- Tip 直接告诉用户：「不想花钱、刚接触本项目，就选 1」
- 默认值改成 `1=Ollama`，回车即用

### 2 — 「OpenAI 官方」和「OpenAI 协议兼容自建网关」拆成两个菜单项

之前 `openai` 一个项要覆盖「OpenAI 公司的服务」+「Azure / vLLM / LMStudio / OneAPI / 自建网关」，从用户心智模型看完全是两件事。AI agent 也分不清要不要追问 base_url。v0.3.6 把它们拆开：

- **菜单 2 = OpenAI 官方**：只问 API Key，base_url 走 `https://api.openai.com/v1`
- **菜单 7 = OpenAI 协议兼容自建网关**：强制问 Base URL（这是唯一区分两者的字段）+ API Key + 模型名

底层都还是写到 `[llm.openai]` 段（共享 OpenAI 协议解析器），但用户和 AI agent 不再需要在心里做这个映射

### 3 — Embedding 单独成一个清晰的问题，附带解释

之前向导问完聊天模型直接接 embedding，没有明确的「这是另一件事」标识。v0.3.6 在 embedding 阶段先打印解释：

> Embedding 是和聊天模型分开的：把视频标题/简介变成向量，用于跨视频去重和相似度判定。频次很高，所以单独拎出来配。

然后才进入 4 选 1 菜单。文案也改了：选项 1 从「跟随主 provider」改成「跟随你刚才选的 LLM（最省事，默认）」

### 4 — B 站 Cookie 教用户怎么拿，不是只丢一个 prompt

之前 `_interactive_auth_setup` 只问「请输入 B 站 Cookie:」，用户看完一脸懵——Cookie 是什么？怎么拿？v0.3.6 在 prompt 之前先打印：

- **为什么需要**：拉历史训画像 + 调 B 站 API 拿视频详情
- **数据安全保证**：只存本机 `data/bilibili_cookie.json`，不上传任何地方
- **怎么获取**：浏览器 F12 → Network → 复制 cookie 请求头的 5 步流程
- **更简单的替代**：装浏览器扩展自动复用登录态

### 5 — 每个字段都有「这是干嘛的」一句话说明

例如菜单 7 选项配置时：

> 你的网关 Base URL（必填，例 http://localhost:8000/v1）
> API Key（如果网关不鉴权可留空）
> 网关上实际部署的模型名（例 meta-llama/Llama-3.1-70B）

而不是冷冰冰的 `Base URL:` / `API Key:` / `model:`

### 6 — `docs/agent-install.md` 同步重写「Asking the user the right questions」段

AI agent（Claude / Codex / Cursor / OpenClaw）跑一句话装机时会读这份 contract。新版给 agent 的指令是：

- **不要一次性把所有问题倒给用户**，分 3 步走（LLM → Embedding → Cookie）
- **解释每个东西在干嘛**（在用户语境下）
- **按选项只问该选项需要的字段**（选 Ollama 就别问 API Key；选官方厂商就别问 base_url）
- **Cookie 一定要附获取步骤**

---

## v0.3.5: 装机向导问全所有问题，不再因「openai」歧义猜错（2026-04-29）

### 4 阶段安装向导（`init` / `setup-embedding`）

之前向导只问「provider + api_key」两件事，但 `openai` 在我们这里其实是**协议家族**——Azure / vLLM / LMStudio / OneAPI / 自建网关都走这一项，base_url 和 model 不一样答案就完全不同。少问的代价是用户配完后跑不通，再被引导回来手动改 `config.toml`。v0.3.5 把向导改成：

- **Phase 1 — Provider 选择**：先打印一张 provider 协议族表，明确告诉用户 `openai` 是协议家族不是厂商
- **Phase 2 — Provider 三件套**：base_url / api_key / model，每个 provider 都带合理默认；按回车接受，不强制重输
- **Phase 3 — Embedding（4 选 1）**：跟随主 provider / 本地 Ollama bge-m3 / 自定义 OpenAI 兼容服务（vLLM / OneAPI 等）/ 指定其他已知 provider
- **Phase 4 — Per-module 覆盖（可选）**：明显标注「高级，可跳过」。给 soul / discovery / recommendation / evaluation 单独设 provider/model（典型场景：发现 / 评估走便宜模型，画像走高质量模型）

### `agent_bootstrap.py` 新增 7 个 flag，AI agent 也能问全

之前 AI agent 只能传 `--llm-api-key` + `--bilibili-cookie`，不够覆盖向导新增的字段。v0.3.5 新增：

| Flag | 用途 |
|---|---|
| `--llm-base-url` | OpenAI 兼容服务的入口 URL |
| `--llm-model` | 主 provider 的 chat 模型名 |
| `--embedding-provider` | embedding provider（空字符串 = 跟随主 provider） |
| `--embedding-model` | embedding 模型名 |
| `--embedding-base-url` | 自托管 embedding 网关的 base_url |
| `--embedding-api-key` | 自托管 embedding 网关的 API Key |
| `--module-override MODULE=PROVIDER:MODEL` | 可重复，per-module 覆盖 |

`docs/agent-install.md` 同步加了一张「最小提问表」，明确告诉 AI agent 哪些问题在哪个 flag 上传——以后不会再因为 OpenAI 兼容服务被默认成官方 OpenAI 跑挂

### 修复：测试污染开发者真实 `config.toml`

之前 4 个 `_save_*` 单元测试只 `monkeypatch.chdir(tmp_path)`，但 `_project_root()` 优先读包安装路径，结果测试值（`sk-new` / `gemini-2.0-flash-exp` / 假 `claude` 覆盖等）会写进开发者的真实 `config.toml`。v0.3.5：4 个测试改用 `monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", tmp_path)`，配合 chdir 双重保险

### 文档

- `docs/modules/cli.md`：补全 `init` 4 阶段交互式 transcript + `setup-embedding` 4 选 1 表格
- `docs/modules/config.md`：`[llm.openai]` 强调协议家族 + 新增 `[llm.<module>]` 段说明
- `docs/agent-install.md`：最小提问表 + 完整 flag 示例

---

## v0.3.4: 原生 Windows 一句话装机（2026-04-29）

### Windows 原生支持，无需 Docker / WSL2

- 新增 `scripts/install.ps1`，行为对齐 `install.sh`：克隆 / 自动升级现有 checkout / 检测 Python 3.11+ / 调用 `agent_bootstrap.py` / 输出对齐 sprintf 格式的状态块
- 用户一句话装机：
  ```powershell
  iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
  ```
- 之前 `install.sh` 第 107 行直接拒绝 `MINGW*/MSYS*/CYGWIN*` 让 Windows 用户去装 WSL2 —— 现在 PowerShell 用户走 `install.ps1` 即可

### `agent_bootstrap.py` Windows 适配

- `start_local_backend`：POSIX 用 `start_new_session=True`，Windows 用 `creationflags=DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP`，让 backend 真正脱离父 console 跑
- `_find_pids_on_port`：Linux/Mac 走 `lsof`；Windows 解析 `netstat -ano` 找 LISTENING PID
- `_terminate_pids`：Linux/Mac 用 `os.kill(SIGTERM/SIGKILL)`；Windows shell out 到 `taskkill /PID /T [/F]`，正确处理 Windows 进程组停止语义

### 文档

- `README.md` / `README_EN.md` 一键命令分双平台展示，加 v0.3.4 提示"无需 Docker / WSL2"
- `docs/agent-install.md` 给 AI agent 加平台检测指引：能从用户环境推断就别问
- `docs/changelog.md` 新条目（本节）

> 仅后端发版（backend-v0.3.4）。Extension 自 v0.3.1 零改动，沿用 extension-v0.3.1。

---

## v0.3.3: 修复本地 Ollama embedding 兜底实际不生效（2026-04-29）

### 关键 bug 修复

**症状**：v0.3.0 引入的本地 Ollama embedding 兜底功能在用户跑 `setup-embedding` 配好后看似生效（`config.toml` 写入 `[llm.embedding] provider="ollama"`），但实际所有 embedding 调用仍然打到 Gemini。线上日志显示 100% 的 embedding 都在 `generativelanguage.googleapis.com/v1beta/.../gemini-embedding-001:batchEmbedContents`，0% 在 `localhost:11434`。

**根因**：`_maybe_ollama_provider` 只在 `[llm.ollama] model` 或 `base_url` 有填的时候才注册 ollama provider，但 `setup-embedding` 向导只写 `[llm.embedding]`，没碰 `[llm.ollama]`。Embedding 服务找不到 ollama provider，静默回退到 default LLM provider（Gemini）。

**修复**：

- `_maybe_ollama_provider` 现在也在 `[llm.embedding].provider == "ollama"` 时自动注册 ollama，使用默认 base_url `http://localhost:11434/v1`（不影响 default chat provider）
- `_save_embedding_provider_config` 在写 `[llm.embedding]` 时如果 `[llm.ollama] base_url` 还是空，自动填 `http://localhost:11434/v1`，避免后续配置检视时 `[llm.ollama]` 全空带来的疑惑

线上 backend 重启后实测 embedding 调用立刻切到 `localhost:11434/api/embeddings` ✓

---

## v0.3.2: supergroup 合并迁离 serve 热路径（2026-04-29）

### 推荐 serve 路径零 API 调用

- `RecommendationEngine` 新增 `_supergroup_canonical_map`，由 `prewarm_supergroup_embeddings` 在每次 refresh tick 后台填充；serve()` `_merge_topic_supergroups` 退化为纯 dict lookup（零 embedding API 调用，零 pairwise 比较）
- prewarm 时重新启用 `"label | top-5 sample titles"` 的语义消歧路径——titles 用来区分 embedding 空间里看似相似的短中文 label（赛博朋克 ≈ 动漫 在裸 label 下能到 sim ≥ 0.90），但只在后台付代价
- `Database.get_topic_group_samples` 给 prewarmer 提供带 sample title 的池子摘要
- 修复早期"label-only embedding 可能误合并短 label"的质量隐患，同时不影响 popup 0.6s 响应延迟

### 工程

- `refresh.py` 把 prewarm 的 `with suppress(Exception)` 换成 `try/except + logger.exception(...)`，失败现在会进日志而不是被吞掉
- `uv.lock` 跟进 0.3.1 → 0.3.2 版本号

> 仅后端发版（backend-v0.3.2）。Extension 自 v0.3.1 零改动，沿用 extension-v0.3.1。

---

## v0.3.1: 推荐丰富度收尾 + 装机/CI 修复（2026-04-29）

### 推荐丰富度二轮治理

- **SQL 层加 per-topic_group cap**：`get_pool_candidates` 用 ROW_NUMBER 把每个 topic_group 在候选窗口里的项数封顶 3，让 270 个池子 group 中的长尾 group 真正进得到候选窗口。同时 over-fetch 由 `limit*5` 涨到 `limit*8`，给下游 balance 多留 headroom
- `_balance_pool_rows` 取消 "len(rows) ≤ limit 直接返回 SQL 顺序" 的 shortcut，改成始终 round-robin，避免 SQL 把同 topic 项目堆到候选头部
- **PoolCurator 双轴 fatigue**：原本只看 `topic_key`（细粒度），动漫杂谈/补番/解说被当成 3 个独立 topic 各自不触发 fatigue。新增 `recent_topic_groups` 维度，跨 key/group 取 max
- **fatigue 曲线陡化**：`count/len*3` → `(count^1.5)/len*5`，count=2 的扣分从 0.20 → 0.47，count=3 从 0.30 → 0.87；`topic_fatigue` 权重 0.15 → 0.25
- 实测：连续三批"换一批"的 distinct topic 数从 ~12-15 提升到 ~18-22，原 3/3 批都霸屏的 topic 现在最多 1/3 批

### 装机器 / CI 修复

- `install.sh` 检测到现有 checkout 时自动 `git fetch + git pull --ff-only`（仅当工作树干净）。之前用户重跑一句话装永远停留在旧版
- `agent_bootstrap.start_local_backend` 加端口冲突检测：旧 OBC backend 还在跑就 SIGTERM 替换；非 OBC 进程占着端口就抛 RuntimeError 让调用方报清楚
- `.github/workflows/release-extension.yml`：把无效的 `shell: node` 替换成 `bash + jq`，extension release CI 解锁
- 修了 OpenClaw proactive e2e fake 的 `get_delight_candidates` 缺失方法

### 其他

- 弹窗 probe 反馈可见性 fix（延迟 profile 重新拉取）
- speculator 已确认 speculation 在 popup 隐藏直到正式 promote
- README / 仓库 About 重新定位为通用 Agent，加 release history 表

---

## v0.3.0: 多源架构回归 + 推荐稳态重写（2026-04-28）

### 多源（multi-source）

- 重新合入此前被回滚的 Phase 0 + Phase 1 多源架构（content_id 兼容层 / SourceAdapter / SourceRecipe / BilibiliAdapter），并叠加 Phase 2 完整投产
- 新增 `xiaohongshu_adapter` 与 `web_adapter`，支持小红书与通用 web 源
- 浏览器插件加 `host_permissions: *://*.xiaohongshu.com/*`，并新增对应 content scripts (`xiaohongshu.js`)、main-world token sniffer (`xhs-token-sniffer.js`)、background `xhs-task-dispatcher`
- popup 文案/动作面、设置页、收藏夹/概览均按多源接入更新

### 推荐池多样性 / discovery 渠道平衡

- trending / explore 在评估前按 rid / domain 做 round-robin 交错，让 30 条 hard-cap 公平覆盖各分区
- 新增 `Database.trim_topic_group_overflow`，每 refresh tick 触发，把任意 `topic_group` 在 fresh pool 的占比压在 ~10% 以内（实测把 `人工智能 / related_chain` 的 207 条压回 60）
- `_build_source_replenishment_plan` 把全部缺货 source 合并到一次 `discover()` 并行 fan-out，告别"每轮一种 source"的 60s 串行
- `trim_pool_to_target_count` 加 `source_share_quotas`，三段桶（protected / negotiable_untracked / negotiable_tracked）保护 under-quota 源不被 score-only 修剪误伤
- `cache_content` UPSERT 时把 `pool_status='suppressed'` 自动复活为 `'fresh'`，让 trending 这类慢更新源能复用 B 站 ranking 不变的池子
- `_SOURCE_TARGET_SHARES` trending 比例 3 → 1，匹配实际稳态（~46）而不是 120 这个永远摸不到的目标

### 换一批（reshuffle）性能：2.6s → 0.6s

- `_merge_topic_supergroups` 的 embedding 调用 sequential await → `asyncio.gather`
- embedding cache key 由 `label | sample_titles`（每轮变 → 0% 命中）改为 `label only`（命中率 ~100%）
- popup 的 10 条 recommendation insert 由 10 次独立 commit 合并为单 transaction（消除 fsync 串行阻塞）
- 在每个 refresh tick 后 prewarm 所有 `topic_group` 的 embedding —— 新 label 进池时由后台付 API round-trip 而不是用户点击时

### 本地 embedding 兜底

- `OllamaProvider.embed()`：通过 Ollama 原生 `/api/embeddings` 拿向量，失败返空降级
- `build_embedding_service` 按 provider 选默认 model：`gemini → gemini-embedding-001`，`openai → text-embedding-3-small`，`ollama → bge-m3`
- 新 CLI 命令 `openbiliclaw setup-embedding`：探测 `localhost:11434`、流式拉 `bge-m3`、写 `[llm.embedding]` 配置；同样的 wizard 也在 `init` 末尾询问
- `install.sh` / `agent-install.md` / `README.md` / `README_EN.md` / `docs/docker-deployment.md` 全部加了"可选启用本地 Ollama embedding"指引

### 工程

- 测试：新增 trending/explore 的 interleave 回归、`trim_topic_group_overflow` 跨源 cap、`trim_pool` 三桶保护、`cache_content` 复活、Ollama embed mock + URL 处理、registry 默认 model 选择、wizard 探测/拉取/持久化共 ~20 个新测试
- 类型：所有改动通过 `mypy strict`
- 多端 lint 干净（ruff + 扩展的 tsc/node test）

---

## M8: 插件后端 API（进行中）

### 兴趣探针丰富度修正：保留大胆探索，但不再塌成同一体验轴

- **症状**：兴趣探针的方向虽然名义上跨 category，但用户体感上经常是一整批“高概念、重入口、知识解释型”方向，丰富度不够
- **根因**：speculation prompt 只强制学科 / 桥接距离分散，没有约束用户体感上的 `experience_mode` / `entry_load`；active pool 也缺少入池前的本地平衡筛选；probe push 只看 `confirmation_count`，不会避开最近已经推过的体验轴
- **修复**：
  1. `SpeculativeInterest` 新增 `experience_mode` 和 `entry_load`
  2. speculation generation 改为过采样后再本地 balanced selection，保证 active pool 至少保留轻入口和非知识解释型候选
  3. runtime push 与 OpenClaw `get_next_probe()` 共用 probe selector：验证压力相同的候选里，优先选择最近没推过的体验轴
  4. `discovery_runtime_state` 新增 `probed_axes`，与既有 `probed_domains` 一起做 probe 去重
- **测试**：新增 speculator 多样性回归、runtime / OpenClaw probe 轴去重回归，并扩展主动推送 E2E 校验 `experience_mode` / `entry_load`

### 推荐池硬上限：`pool_target_count` 从软地板升为硬天花板

- **症状**：用户反馈 popup 显示 896 条可换，远超配置 `pool_target_count=600`。排查发现 600 只作为"低于它就补货"的地板（floor），`trending` 每 3 小时 / `explore` 每 12 小时 / 事件阈值触发的 refresh 都不看总量，会越线往池子里加内容。`_run_refresh_plan` 的中途 break 条件也只在"起步低于目标"时生效
- **修复**（source-of-truth 在 `runtime/refresh.py`）：
  1. 新增 `ContinuousRefreshController._enforce_pool_cap()`：在 `refresh_if_needed` 和 `force_refresh` 入口检查 pool ≥ target 则直接返回 `{"refreshed": False, "reason": "pool_at_cap"}`，不再触发 discover。pool > target 时先调用新 DB 方法 `trim_pool_to_target_count` 把溢出部分降为 `suppressed`；每次触发都会写 INFO 日志 `enforce_pool_cap: trimmed=..., pool_available=..., target=...`，失败捕获并 `logger.exception`
  2. `_run_refresh_plan` 中途 break 条件从 `initial_pool_below_target and current_pool_count >= target` 改为 `current_pool_count >= target`：任何策略在执行过程中把池子撑到目标就立刻停
  3. 新 DB 方法 `Database.trim_pool_to_target_count(target)`：按 `relevance_score` 降序 → `last_scored_at` 降序 → 非 `explore` 优先 → `bvid` 稳定序排序，保留前 target 条，其余标 `suppressed`。只动当前 `pool_status='fresh'` 且未进入 recommendations 的条目
- **文档一致性**：`docs/modules/config.md` 的 `pool_target_count` 描述原本承诺"到达目标后不再触发新 discover"，与旧实现不符。现在行为和文档对齐
- **测试**：新增 4 个测试覆盖 `refresh_if_needed` / `force_refresh` 在 cap 时返回 `pool_at_cap`、入口触发 trim、策略中途命中 cap 就停；调整 6 个原本依赖"pool_count == target"假设的测试（降到 pool_count=20 保持原意图）；`test_refresh_controller_triggers_event_refresh_when_signal_threshold_reached` 重命名为 `_falls_back_to_full_plan_when_below_target`——原测试覆盖的"pool ≥ target 时事件阈值触发"分支现在是不可达代码

### 惊喜推荐前移到推荐页首屏

- popup `recommend` tab 新增独立的惊喜推荐首屏卡位，不再只能依赖系统通知或临时消息才能看到 delight 候选
- popup 启动、后端重连和 `init_completed` 后会主动读取 `/api/delight/pending`，runtime stream 收到新的 `delight.candidate` 也会即时刷新首屏卡
- 惊喜推荐通知点击后会打开带 `?tab=recommend&delight=<bvid>` 的插件页面，直接落到对应候选，而不是只回到通用推荐页
- 首屏惊喜卡支持 `看看 / 不感兴趣 / 聊一聊 / 稍后看` 四个动作，并会把“已打开 / 已聊过 / 先少来点”保留成本地稳定态，而不是立刻消失

### 惊喜推荐运行时修复

- delight 运行时和后台打分不再各用一套门槛：共享阈值统一到默认 `0.70`，探索开放度低时自动提高到 `0.80`，避免真实数据里分数已经够高却永远过不了 `pending` 查询
- `precompute_delight_scores()` 现在会回填“已有高分但缺 `delight_reason / delight_hook`”的 backlog，不再只处理 `delight_score = 0` 的新候选
- 后台启动时会额外跑一次 delight 预热，即使当前没有普通推荐文案要补，也会把可推送的惊喜候选准备好
- `pending delight` 只会暴露文案已就绪的候选；`suppressed` 的高分库存也允许作为惊喜推荐入口，避免被普通池限流后直接从惊喜通道里消失

### 源无关内容分类：XHS 内容入库后自动 LLM 分类

- **症状**：XHS 内容通过 `_cache_xhs_notes` 直接入库 `content_cache`，绕过了 bilibili 内容必经的 LLM 评估管线，导致 `style_key` / `topic_group` / `relevance_score` 全为空。推荐多样性机制崩溃——所有 XHS 条目共享 `"unknown"` style 和单一 `"xhs-extension-task"` topic token，一轮 10 条推荐完全被 XHS 占满
- **修复**（推荐模块为源无关统一入口）：
  1. `recommendation/engine.py::classify_pool_backlog()`：检测 pool 中 `style_key` 和 `topic_group` 都为空的条目，调用与 bilibili 同款的 LLM batch 评估 prompt 打上分类标签，结果回写 DB。分类后所有内容只有内容特征（style / topic / score），没有来源标签
  2. `api/app.py::ingest_xhs_observed_urls`：入库后 `asyncio.create_task(_classify_new_pool_items())` 触发后台分类
  3. `asyncio.Lock` 防止并发重复 LLM 调用；失败标 0.01 分防无限重试
  4. `topic_key` 自动从 `topic_group` 回填，确保 `_diversity_tokens` 有可用 token
- **DB 保护**：`cache_content()` upsert 的 `topic_key` / `topic_group` / `style_key` / `relevance_score` / `relevance_reason` 改用 `COALESCE(NULLIF(excluded.xxx, ''), existing, '')` 保护——extension 重发同一笔记不会覆盖已分类字段
- **`author_name` 字段修复**：加入 INSERT 子句 + schema 迁移，之前这个字段写了等于没写
- **`_diversity_tokens` 修复**：移除 `source_strategy` 作为 topic fallback（根因），改用作者名 + 标题中文/英文关键词
- **共享定义**：提取 `VALID_STYLE_KEYS` 到 `discovery/engine.py` 模块级，`DiscoveredContent.to_cache_kwargs()` 作为唯一的字段映射源，消除 3 处 `_VALID_STYLES` + 2 处 20-kwarg `cache_content` 展开的重复
- **空标题过滤**：extension 端 `extractNoteMetadataFromAnchor` 空标题返回 null；后端 `_cache_xhs_notes` 跳过空标题笔记。DB 历史 46 条空标题行标为 suppressed
- **测试**：新增 12 个测试（5 个 unit + 7 个 E2E multi-source diversity suite）——覆盖分类流程、重复入库保护、混排多样性、并发锁、失败重试、空标题过滤

### 兴趣探针用户确认交互

- **产品形态**：WebSocket 推送 `interest.probe` 事件 → Chrome 系统通知"阿B 想确认：你对「XX」感兴趣吗？" → 点击打开 popup Profile tab → 卡片显示猜测方向 + 具体子方向 chips → 三按钮交互：「是」「不是」「多聊聊」
- **后端**：
  - `speculator.py::user_confirm_speculation(domain)`：直接 promote 到正式兴趣
  - `speculator.py::user_reject_speculation(domain)`：30 天冷却期
  - `api/app.py::POST /api/interest-probes/respond`：接收 confirm / reject / chat，chat 转发到 dialogue 引擎
- **去重冷却**：`_PROBE_COOLDOWN_HOURS = 4`，同一 domain 4 小时内只推一次，记录在 `discovery_runtime_state["probed_domains"]`
- **推送时机修复**：`_publish_delight_if_available` 和 `_publish_interest_probe_if_available` 从 `_run_refresh_plan` 内部移到 `run_forever` 主循环——之前 pool 满时不触发 refresh plan，推送永远到不了客户端
- **插件前端**：`popup.js::renderProbeCard()` + `handleProbeResponse()` + CSS 动画；service-worker 处理 `interest.probe` 事件创建 Chrome 通知
- **CLI**：`openbiliclaw delight`（手动查看惊喜推荐候选）+ `openbiliclaw probe`（手动列出猜测方向、序号确认/拒绝）

### 架构图更新

- **discovery-architecture.html**：新增 XHS 入库 + `classify_pool_backlog` 并行通道；`pool_target_count` 300→600；refresh loop 加 `_tick_xhs_producer`
- **recommendation-architecture.html**：serve() 管道加 `classify_pool_backlog` 安全网步骤；diversity 描述更新为源无关；解耦架构图加 XHS Extension 作为第二数据源经"源无关门"入池；模块边界加 `VALID_STYLE_KEYS` 共享常量

### 修复加入 xhs 后推荐列表出现 xhs 独占轮次，丰富度塌陷

- **症状**：引入小红书内容后，一轮推荐偶尔全是 xhs 笔记——`picked summary` 出现 `{"count":10,"styles":{"unknown":10},"sources":{"xhs-extension-task":10}}`，风格 / 主题 / 平台都单一，用户每次下拉都看到同一类短视频
- **根因**：`_select_diversified_batch` 的 style cap 依赖 `_style_token` 返回的桶名，但 xhs 笔记普遍 `style_key=""`——空字符串被当成"无 style"直接跳过 style cap 检查。多个 xhs 笔记在主循环和前几档 try_fill 里都能以"空 style"身份堆到同一批次；一旦前面 cascade 没选够，最后一档无条件兜底把所有剩余项全塞进来，就凑出 10/10 xhs 独家场
- **设计原则**：用户明确要求"任何来源平等视为内容"——不走平台黑白名单，只从内容维度（topic / style）保证丰富度。平台是产地标签，不是歧视依据
- **修复**（`recommendation/engine.py::_select_diversified_batch`）：
  1. `_style_token` 把空 `style_key` 映射成 sentinel `"unknown"`——未分类内容参与 per-style cap，和有 style 分类的条目走同一套配额逻辑，不再享受空字符串"免检"
  2. 最终兜底把原本的无条件硬塞换成"broad-topic 松口径"：`fallback_broad_cap = 2 × broad_cap`。topic 才是内容丰富度的真信号——同一个 broad topic 的条目即使平台 / style 不同也会让用户感到重复。没有 topic 的条目允许通过，避免候选池薄时返回空批次
  3. 宁可返回小批次（比如 6 条 topic-diverse）也不凑满 10 条单一 topic
  4. `_build_debug_summary` 加 `platforms` 字段，日志里能直接看 bilibili / xhs 比例——仅做观测，不参与筛选
- **测试**：
  - `tests/test_recommendation_engine.py::test_monoculture_pool_capped_by_broad_topic_not_platform`——纯 xhs 同 topic 池 13 条 → 兜底 broad-topic 天花板 6 条
  - `test_content_diversity_treats_platforms_equally`——xhs + bili 混池各自 topic-rich → 两边都有代表，不再人为限量
  - `test_pure_bilibili_rich_pool_fills_batch`——纯 bilibili 富池仍填满 limit
  - `test_reshuffle_recommendations_backfills_to_requested_limit_when_style_is_dominant`——同 style 但不同 topic → backfill 到 limit
  - 全量 28 passed（recommendation_engine.py）

### MAIN-world sniffer：从 xhs 自己的 API 响应里捞 `xsec_token`

- **动机**：上一轮 token 回填修了"已经见过 token 的 note 能对齐"，但搜索页从头到尾都不走探索流的 note，历史上 `xhs_observed_urls` 根本没存过它的 token。用户点到的 `69c7a7b000000000220030c9` 就属于这类——任何途径都没捞到过 token，点击直接撞 xhs 300031 登录墙
- **思路**：xhs 的 Web 端自己会拿 token 发 `/api/sns/web/*` 请求，token 就躺在 response JSON 里。劫持 `window.fetch` / `XMLHttpRequest`，扫 response body 里所有 `(note_id, xsec_token)` 对子，回传给后端 backfill
- **难点**：content script 跑在 isolated world，`window.fetch` 不是页面的 fetch，劫持没用。必须用 MV3 的 `world: "MAIN"` 声明，让脚本和页面共享同一个 realm
- **实现**：
  1. `extension/src/main/xhs-token-sniffer.ts`（新文件）：MAIN-world 脚本，wrap `window.fetch` 和 `XMLHttpRequest.prototype.{open,send}`。`extractTokenPairs` 对任意 JSON 做深度优先扫描，认 24-hex `note_id`/`noteId`/`id` + 非空 `xsec_token`/`xsecToken`。读 body 前先 `response.clone()`，不动原始流。安装代码用 `typeof window !== "undefined"` 守护，node 测试可以只导出 `extractTokenPairs` 用
  2. `extension/manifest.json`：加第二条 `content_scripts` 给 xhs——`world: "MAIN"`、`run_at: "document_start"`，抢在 xhs 自己注入 fetch 之前挂钩
  3. `extension/src/content/xiaohongshu.ts`：isolated world 里加 `window.addEventListener("message")` bridge，收 `source: "obc-xhs-sniffer"` 的 postMessage 后缓冲 1.5s 去重，再 `chrome.runtime.sendMessage` 到 service worker
  4. `extension/src/background/service-worker.ts`：`XHS_TOKENS_OBSERVED` 消息 POST 到 `/api/sources/xhs/tokens`
  5. `api/app.py::ingest_xhs_tokens`：用 sniffed pairs 合成 `https://www.xiaohongshu.com/explore/<id>?xsec_token=<tok>` 走已有的 `_backfill_xhs_tokens` UPDATE 路径——和探索流的回填合一，不走新分支
- **隐私边界**：sniffer 不改请求、不做指纹采集、不外传任何非 `(note_id, xsec_token)` 字段。这两个值对任何登录态 xhs session 而言都是公开可读的
- **效果**：用户每逛一次 xhs 任意页面（首页 / 搜索 / 个人页），后台就从 xhs 的 API 响应里自动把可见 note 的 token 收集齐。之前存成裸 URL 的历史数据会逐步被升级成带 token 版，推荐卡点击命中 xhs 登录墙的概率随之下降
- **测试**：
  - `extension/tests/xhs-token-sniffer.test.ts`：10 例覆盖 `extractTokenPairs`——flat/nested/arrays/dedupe/camelCase/reject 非 24-hex/reject 空 token/null 入参
  - `tests/test_api_xhs_ingest.py::TestXhsTokens`：`/api/sources/xhs/tokens` 端点——token 能 backfill 到已入库的 bare cache / 空 pairs noop / malformed pair 被丢
- **手工验证**：重新 build extension + reload chrome extension 后，随便打开一条 xhs note，后台日志里能看到 `tokens upgraded=N` 出现

### 修复 xhs 笔记分享 URL 丢失 `xsec_token` 导致登录墙拦截

- **症状**：缓存的 xhs `content_url` 绝大多数是裸 `https://www.xiaohongshu.com/explore/<id>`，不带 `xsec_token=...`。DB 抽样 260 条观测 URL 里只有 15 条（全部来自 `explore` 首页）带 token，`search` 页（133 条）/ task 页（92 条）全是裸的。外链分享 / 退出登录后打开都会被 xhs 拦到登录墙
- **根因**：xhs 搜索结果页的 React 组件把 `xsec_token` 留在组件 props 里，不写入 `<a href>`；内容脚本 `passive.ts::extractXhsNoteUrl` 只能从 href 捞 token——搜索页天然捞不到。笔记详情页的权威 token 其实在 `window.location.search` 里，但原先根本没被读取
- **修复**：三处联动
  1. `api/app.py::_pick_best_xhs_url`：`_cache_xhs_notes` 写 `content_url` 前先比较——incoming 有 token 就直接用；否则回查 `xhs_observed_urls`（历史带 token 的观测）和现有 `content_cache` 行，选一个带 token 的回来。这样 xhs 先逛 explore（token 到手）再搜同一条的场景能把 token 对齐过去
  2. `api/app.py::_backfill_xhs_tokens`：`/api/sources/xhs/observed-urls` 和 `/api/sources/xhs/task-result` 收到带 token 的 URL 时，一次 UPDATE 把 `content_cache` 里同 note_id 的裸 URL 改写成带 token 版——修已存入库的历史裸 URL
  3. `extension/src/content/xiaohongshu.ts::selfNoteAnchor`：用户直接坐在笔记详情页时，合成一个"自指 anchor"塞进 collector，把 `window.location.href` 里的权威 token 上报给后端。搜索页缺的 token 在用户点进任意一条笔记时立刻补全
- **测试**：
  - `tests/test_api_xhs_ingest.py::test_tokenized_url_upgrades_existing_bare_cache_row`——裸 URL 先入库、带 token 的同 note_id 后观测，最终 DB 必须是带 token 版
  - `tests/test_api_xhs_ingest.py::test_cache_prefers_tokenized_url_from_prior_observation`——先观测带 token，再来裸 URL + `notes` payload，不准回写成裸
  - 全量 807 passed + 15 skipped

### 修复推荐列表里 xhs 笔记被当成 bilibili 视频打开（URL 错指）

- **症状**：popup 打开 xhs 推荐卡片时跳到 `https://www.bilibili.com/video/<24位 xhs 笔记 ID>`——bilibili 上根本没这条视频，点开 404。xhs 和 bilibili 内容看似"混了"
- **根因**：`storage/database.py::get_recommendations` 的 SQL 只从 `content_cache` 拉 `title/up_name/cover_url`，**没拉 `content_id`/`content_url`/`source_platform`**。下游 `/api/recommendations` 读到 `source_platform=""` 就按默认兜底成 `"bilibili"`，读到 `content_url=""` 后 popup 的 `buildContentUrl(item)` 又走 `bilibili.com/video/${bvid}` 兜底——xhs 笔记 ID 被硬塞进 bilibili 命名空间
- **修复**：`get_recommendations` SQL 补上 `c.content_id`、`c.content_url`、`c.source_platform`（`LEFT JOIN content_cache`，xhs / bilibili 通吃）。之前几轮修 `_cache_xhs_notes` / `_cache_results` 写入路径时忽略了"读回推荐"这条链路
- **测试**：`tests/test_storage.py::test_get_recommendations_joins_multi_source_fields` 守这三字段在 join 之后还能读回；全量 51 passed（storage + xhs ingest）

### 修复 xhs 笔记入库时 `source` 为空、rescore 后 `source_platform` 被覆盖成 `bilibili`

- **两个相互放大的 bug**：
  1. `api/app.py::_cache_xhs_notes` 传的是 `source_strategy=f"xhs-extension-{page_type}"`，但 `Database.cache_content` 读的是 `source` kwarg，错拼的 key 被 `kwargs.get("source", "")` 默默丢弃——xhs 所有入库笔记 `source` 列永远是 `""`
  2. `discovery/engine.py::_cache_results` 只透传 `source`，**没透传 `source_platform`/`content_id`/`content_url`/`author_name`**。cache_content 的 upsert 分支 `source_platform = excluded.source_platform` 会把 xhs 行的 `source_platform` 回写成默认值 `"bilibili"`，每次 rescore 过一遍 pool 就被覆盖一次
- **连锁现象**：DB 里出现 35 行 `source_platform='bilibili'` 但 `bvid` 是 24 字符 xhs 笔记 ID（如 `68580835000000002203315d`）、title 写着"鸡煲复刻 / 杀戮尖塔进阶"的"假 bilibili 行"
- **修复**：
  - `api/app.py:972` 把 `source_strategy=` 改成 `source=`，同时注释说明错拼 key 会被静默丢弃的坑
  - `discovery/engine.py::_cache_results` 额外透传 `source_platform`/`content_id`/`content_url`/`author_name`
  - 两条读回路径 `_backfill_candidates` 和 `recommendation/engine.py::_rows_to_discovered` 也补上从 DB 行读 `source_platform`/`content_id`/`content_url` 的逻辑（之前读回时也丢字段，导致再入库时又是默认值）
- **历史数据修正**：一次性 SQL 修 169 行——把 `source_platform='bilibili'` 且 `bvid NOT LIKE 'BV%'` 的 35 行改回 `xiaohongshu`、补齐 `content_id`/`content_url`；把所有 `source=''` 的 xhs 行标为 `xhs-extension-task`
- **测试**：
  - `tests/test_api_xhs_ingest.py::test_notes_cache_populates_source_and_platform` 守 cache_content 正确 kwarg
  - `tests/test_discovery_engine.py::test_discovery_engine_cache_results_preserves_multi_source_fields` 守 rescore 不会把 xhs 行打回 bilibili
  - 全量 804 passed（之前 802 + 本次 2）

### 修复 xhs 任务 100% 超时（丢失 EXECUTE 握手）

- **症状**：CLI `discover --source xiaohongshu` 入队后，所有 `xhs_tasks` 都在 30s 后被写成 `status=failed`、`error=timeout`，候选池没增加一条小红书笔记
- **根因**：`extension/src/background/xhs-task-dispatcher.ts` 里 `executeTask()` 只 `chrome.tabs.create` 开了后台标签，从未给内容脚本发 `XHS_TASK_EXECUTE`。内容脚本 `task-executor.ts` 的 `chrome.runtime.onMessage` 监听器永远等不到触发，30s 硬超时必然命中
- **修复**：`tabs.create` 之后注册一次 `chrome.tabs.onUpdated` 监听，页面 `status === 'complete'` 命中时 `chrome.tabs.sendMessage(tabId, {action: "XHS_TASK_EXECUTE", data: {task_id, type}})` 再立即 `removeListener`（避免 SPA 内再跳转重复发）；`sendMessage` 被拒（内容脚本缺席）时上报 `error="sendMessage_failed"` 而非静默超时；`cleanupTask()` 也清掉残留监听器
- **测试**：`extension/tests/xhs-task-dispatcher.test.ts` 新增两条 e2e（完整握手 + `sendMessage` 失败路径），手搓 `chrome.tabs` / `fetch` mock，不依赖 jsdom。8 条 dispatcher 测试全绿

### 候选池上限提到 600

- `scheduler.pool_target_count` 默认值从 `300` 提到 `600`，允许范围同步改为 `1..600`
- 运行时行为保持不变：候选池达到目标后停止 discover，掉回目标以下再触发补货，避免无谓的远端调用
- 同步更新：`SchedulerConfig` / `RuntimeRefreshController` / API models / popup 设置面板（`min/max/placeholder`）/ 文档 / 相关测试

### 修复推荐卡片封面挤压

- 侧边栏宽屏下 `116px + 1fr` 的两列 grid 叠加 `aspect-ratio: 16/10` 会让封面被拉伸、文字被挤成一条。改回 flex 纵向布局（封面全宽在上、文字在下），和早期版本体验一致
- 同时把 520px 媒体查询里的 `grid-template-columns` 覆写清掉

### 日志按大小自动轮转

- **避免失控的 7GB 日志文件**：生产中 DEBUG 级别写的 httpcore/httpx tracelog 会把 `logs/openbiliclaw.log` 撑到几个 G。切换到 `logging.handlers.RotatingFileHandler`：单文件到达 `max_file_size_mb` 立刻轮转成 `<filename>.1`，超出 `backup_count` 的老份直接丢弃
- **启动时清理历史大日志**：光换 handler 不够——`RotatingFileHandler` 不会回头处理已经超标的旧文件。`_enforce_size_budget_once` 在 `configure_logging` 开头检查一次：超过 `max_file_size_mb` 的历史文件会被重命名成 `<filename>.1`（覆盖旧 `.1`）再让 handler 从空文件写起，这正对应用户说的"超过 1G 就清理"
- **配置**：`[logging]` 新增两字段 `max_file_size_mb`（默认 1024）和 `backup_count`（默认 1）。`max_file_size_mb=0` 退回原来的 `FileHandler`（不轮转）；`backup_count<1` 时同样回退，因为 stdlib 的 RotatingFileHandler 在 `backupCount=0` 时根本不会轮转
- **磁盘占用上限**：默认配置下 `openbiliclaw.log` + `openbiliclaw.log.1` 合计不超过 ~2GB
- **测试**：`tests/test_logging_setup.py` 新增 4 个（启用轮转 / size=0 禁用 / 启动时轮转超标文件 / 小文件不动），`tests/test_config.py` 新增 2 个（默认值、TOML 解析）。全量 802 passed

### CLI `discover` 支持按来源 / 策略触发

- `openbiliclaw discover` 增加 `--source {bilibili|xiaohongshu}` / `--strategy search,trending,…` / `--limit` / `--force` 四个选项，允许单独触发某个渠道或 Bilibili 单条策略
- `--source xiaohongshu` 路径复用 `XhsTaskProducer.produce_if_due()`，`--force` 时 `min_interval_hours=0` 绕过 4 小时节流；结果直接写入 `xhs_tasks` 表交由扩展后台抓取
- `--source bilibili`（默认）走原 `ContentDiscoveryEngine.discover()`，`--strategy` 透传为 `strategies=[…]`，空值时等价于跑全策略
- 参数校验：未知 source 或未知 Bilibili 策略名直接 Typer `BadParameter` 退出码 2；xhs 路径上同时传 `--strategy` 会打印友好提示然后忽略
- 文档：`docs/modules/cli.md` 的 `openbiliclaw discover` 章节重写，给出 B 站单策略 / xhs / `--force` 三个示例

### Soul 驱动 xhs 自动发现（producer 接上）

- **后端 producer 落地**：`runtime/xhs_producer.py` 的 `XhsTaskProducer` 读取 SoulProfile → 调 LLM 改写成小红书风格关键词 → `XhsTaskQueue.enqueue("search", {keyword})`。内置最小间隔（默认 4h）防止反复抢配额；每日预算由 `XhsTaskQueue.enqueue` 强制（`sources.xiaohongshu.daily_search_budget`，默认 30）
- **LLM 关键词生成**：`sources/xhs_keyword_gen.py` 把 B 站风格的兴趣标签重写成生活化、具象、长尾、带场景的 xhs 查询（避免单字类目词）。JSON 解析走容错路径，LLM 失败即跳过该轮
- **挂接现有刷新循环**：`ContinuousRefreshController.run_forever` 每轮调用 `_tick_xhs_producer()`，和 bilibili discovery 共用同一调度器，无需额外 cron
- **闭环打通**：backend producer → `xhs_tasks` 表 → 扩展 `xhs-task-dispatcher` 轮询 → `chrome.tabs.create({active:false})` 后台执行 → `xhs/task-executor`（首屏、不滚动）回传 URLs + 元数据 → `/api/sources/xhs/task-result` 写入 `content_cache`
- **配置**：`sources.xiaohongshu.daily_search_budget` 默认从 20 提到 30（匹配产品端对 xhs 采样密度的预期）
- **测试**：`tests/test_xhs_producer.py` 新增 5 个（disabled / 预算截断 / 节流 / 空关键词 / 无画像）。全量 796 passed

### 小红书安全发现架构 (xhs-safe-discovery)

- **GPL 隔离 sidecar**：`sidecar/xhs-downloader/` 将 GPL-3.0 的 XHS-Downloader 封装在独立 Docker 容器中，通过 HTTP（`POST /xhs/detail`）与主后端通信，避免 GPL 传染。Dockerfile 固定上游 commit `5f9bd54` 确保可复现构建
- **新 XiaohongshuAdapter**：替换旧的浏览器抓取适配器，改为 HTTP 客户端调用 sidecar。并发上限 2，单 URL 失败不影响批次。后端不再直接搜索小红书（完全移除 browser-based XiaohongshuAdapter）
- **扩展被动 URL 收集**：`extension/src/content/xhs/passive.ts` 在用户自然浏览时提取视口内可见的笔记 URL（含 `xsec_token`），去重后通过 `POST /api/sources/xhs/observed-urls` 上报。**严格不自动滚动**——自动滚动是小红书风控的经典触发信号
- **任务队列**：后端 `xhs_tasks` 表 + `XhsTaskQueue` 管理搜索/创作者任务，支持每日预算限制（按类型分开计数）。扩展通过 `GET /api/sources/xhs/next-task` 轮询，`POST /api/sources/xhs/task-result` 回报结果
- **后台标签页调度器**：`extension/src/background/xhs-task-dispatcher.ts` 以 alarm 驱动轮询，`chrome.tabs.create({ active: false })` 打开后台标签页执行任务，30s 硬超时，互斥锁保证单任务飞行
- **无滚动执行器**：`extension/src/content/xhs/task-executor.ts` 用 MutationObserver + 轮询等待卡片渲染（5s 上限），提取初始视口内最多 20 个 URL，绝不调用任何滚动方法
- **创作者订阅**：`xhs_creator_subscriptions` 表 + CRUD API（`/api/sources/xhs/creators`），支持 `due_for_fetch` 查询驱动夜间调度
- **配置**：`[sources.xiaohongshu]` 新增 `sidecar_url` / `daily_search_budget` / `daily_creator_budget` / `task_interval_seconds`；`OPENBILICLAW_XHS_SIDECAR_URL` 环境变量显式覆盖（因通用 env 模式无法处理含下划线的嵌套键）
- **docker-compose**：新增 `xhs-sidecar` 服务（内部 expose 5556，healthcheck，后端 depends_on healthy），后端自动注入 sidecar URL
- **测试**：`test_xiaohongshu_adapter.py`（7 个）、`test_api_xhs_ingest.py`（5 个）、`test_xhs_tasks.py`（16 个）、`xhs-passive.test.ts`（8 个）、`xhs-task-dispatcher.test.ts`（6 个）、`xhs-task-executor.test.ts`（3 个）。全量 797 passed backend / 107 passed extension

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

### 插件 / 后端 Release 通道拆分

- 后端 Release workflow 现在只响应 `backend-v*` tag，并继续自动构建 macOS / Windows 桌面包
- 新增插件专用 Release workflow，插件现在通过 `extension-v*` tag 单独发布 `openbiliclaw-extension-v*.zip`
- 后端和插件各自创建自己的 GitHub Release，不再把两类附件混在同一个 release 语义里
- README、模块文档和文档导航已同步改成“插件看 `extension-v*`、后端看 `backend-v*`”的下载说明
- 历史 `v0.1.0` / `v0.1.2` 发布记录保持不动，新发布从双通道策略开始执行

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
