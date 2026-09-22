# LLM 多模型支持

> 运行时并发由单一 `LLMConcurrencyGate` 管理：所有 provider 请求受总 gate（默认 3）约束，后台还受 `max(1, total-1)`（默认 2）约束。后台 admission 依据 canonical durable inventory 把工作分为 `refill.expression > refill.evaluation > refill.supply > maintenance`；有 refill waiter 时保证下一批新准入至少两个 refill 槽并可借满三个，库存为零时 park 新 maintenance（**park 有 5 分钟上限** `MAINTENANCE_STARVATION_GRACE_SECONDS`：库存持续为零且补货始终不来时——所有 source 关闭、凭据失效或网络不通——到点强制放行并打 WARNING 指出补货可能失败。画像流水线归 `soul.*` = maintenance，无上限的 park 会让日常浏览静默停止更新画像，而 maintenance 本身不可能把池子补上；库存恢复非 EMPTY 时该豁免立即撤销并为下次重新武装）。对话、`api.sentiment` 与用户主动发起的 `api.config_probe` 是交互流量；未知 caller 只告警一次并按 maintenance 处理。旧 `bypass_semaphore=True` 只绕过后台 gate，`PrioritySemaphore` 仍从 `llm.service` 兼容导出。

热重载不会替换 gate 对象，而是原地 `reconfigure()`：升容立即按优先级唤醒等待者；降容不撤销已进入 provider 的工作，并在 active 降到新容量以下前停止新准入。配置探测使用 `api.config_probe` 交互分类，只经过 total gate：即使 canonical inventory 为空，用户仍能测试并修复阻塞初始化的模型配置，但探测不会绕过总 provider 并发上限。

> 统一的多 LLM Provider 接口，支持为同一 Provider 类型建立多个独立端点实例，并通过全局或分模块的有序实例链完成故障切换、retry 和健康检查。

## 概述

`llm/` 包提供了一套抽象的 LLM 调用接口，上层模块（Soul Engine、Discovery Engine 等）通过 `LLMService` 或 `LLMRegistry` 发起调用，不需要关心底层用的是哪个模型。

核心设计：
- **Provider 抽象** — `LLMProvider` ABC 定义统一接口
- **Registry 管理** — 以实例 ID 注册端点；同一种 adapter 可出现多次，并按配置的任意长度实例链依次调用
- **Service 门面** — `LLMService` 封装 prompt 组装 + 调用 + 校验
- **统一异常** — 所有 provider 错误归一化为标准异常类型

## 已实现功能

| 任务 | 状态 | 说明 |
|------|------|------|
| 2.1 Provider 实现 | ✅ | OpenAI / Claude / Gemini / DeepSeek / Ollama / OpenRouter / OrcaRouter / OpenAI-compatible，带 retry + 超时 |
| v0.3.x OrcaRouter Provider 支持 | ✅ | 新增 `OrcaRouterProvider`（OpenAI 兼容协议）：一个 Key 跑 150+ 模型，默认 `openai/gpt-4o`，默认端点 `https://api.orcarouter.ai/v1`；沿用统一超时 / 重试 / 错误归一化 / JSON mode 与 per-call model 覆盖。网关把 `reasoning_effort` 与嵌套 `reasoning` 对象都原样转发给上游路由，非推理模型会以 HTTP 400 拒绝（已对 `openai/gpt-4o` 实测），因此适配器**不发送任何推理参数**，推理模型使用自身默认档位 |
| 2.2 Provider Registry | ✅ | 多端点实例注册 + 全局 / 模块有序链 + 实例级 cooldown + health check |
| 2.3 Prompt 管理与 Service | ✅ | Prompt 构建器 + LLMService 门面 |
| 画像整理裁决 prompt | ✅ | `build_profile_consolidation_prompt()` 保持静态 system + 确定性 user JSON；likes 从“仅严格同义”调整为“是否重复占用同一推荐意图”，允许合并“搞笑 / 娱乐搞笑”这类无新增选择价值的同粒度标签，同时明确保留“篮球 / NBA”“AI技术 / AI视频技术”等会改变召回范围的父子兴趣。每个簇携带 `known_distinct_pairs`，模型不得重判或合并用户回滚 / 当前策略已确认分开的 pair；代码侧仍作相同约束的强校验。dislikes 继续只合并近乎同义项并严禁向上泛化 |
| Phase 2 provider-independent cognition views | ✅ | Preference、plain Awareness、Awareness-with-confusions 与 Insight builder 都有显式 `input_view="legacy"|"compact-v1"` seam；compact 使用 `CognitionEventViewV1` 与 `CognitionProfileViewV1` 删除 transport/storage 重复字段并按 stable soul → stable preference → volatile cognition → current batch 排序，system message、输出 schema、reasoning 和 token ceiling 不变。生产 rollout 逐 task 控制：只默认开启已通过 SenseTime 门的 `soul.awareness_confusions`，plain `soul.awareness` 固定 legacy，Preference/Insight 默认 legacy。该投影不依赖 tokenizer、模型或 provider cache。 |
| v0.3.182+ 对话洞察锚 prompt | ✅ | `build_dialogue_insight_prompt(..., active_list=None, anchor=None)` 保持模块级静态 system 与确定性 `sort_keys=True` user JSON。`anchor=None` 保留无锚字节形态；非空 `anchor` 只在 user message 追加 `<current_anchor>` 与 kind×relation 输出契约，不把代次数据污染 prompt-cache system 前缀。 |
| v0.3.182+ 对话结算在线内所有权 | ✅ | API runtime 的 `SocraticDialogue(mode=queued)` 在唯一 `DialogueSettlementQueue` worker 内 await 回复后的完整 `learn_from_dialogue`，普通 chat settles 与锚 relation 在同一 worker 直接 apply；不 detached、不进 task registry，也不为整项套 300 秒队列 timeout，provider 自身有限 timeout 与 total gate 不变。探针对话的 sentiment classifier 也只在 `probe.reply.apply` job 内调用一次；弱正向只把 typed `ExplorationIntent` 交回 dispatcher，真实 exploration 写入在 permit 外沿用既有路径。CLI/OpenClaw 两处 `legacy_direct` 继续既有 detached learning，不进入此所有权域。 |
| v0.3.164+ OpenAI-compatible JSON-object 合约 | ✅ | `LLMService.complete_structured_task()` 与 `complete_multimodal_structured_task()` 共享最小兼容层：已有大写 `JSON` 仅归一为小写 `json`；完全没有该 token 时只追加 `json`。这满足部分 OpenAI-compatible 端点对 `response_format=json_object` 的字面消息约束，不改变业务规则、画像、阈值、user 内容或 core-memory 排序；非结构化 `complete_with_core_memory()` 完全不改写 prompt。 |
| Sponsored Runtime 合约（Phase 1，未发布） | 🚧 | 新增 `sponsored_contracts.py` / `sponsored_tasks.py` / `prompt_contracts.py` 与 `scripts/generate_sponsored_policy.py`：canonical JSON-mode 入口收口为 `ensure_json_mode_contract()`（发送与 policy 生成同源）；pilot contract 为 `soul.consolidation.v1`（静态 system prompt + 闭环 JSON Schema + 未签名 policy 生成 + golden hash 测试）。Provider/Runtime 集成尚未接入，设计见 [Sponsored Runtime 设计](../plans/2026-09-22-sponsored-runtime-design.md)。 |
| v0.3.185+ 401 终态化与归属可见 | ✅ | `llm.base.LLMAuthError` 携带 `provider_name` / `endpoint`；OpenAI 系（openai / deepseek / ollama / openrouter / openai_compatible）、Claude、Gemini 的 `_map_error()` 在 401 时统一抛出它并记 WARNING（含 base_url + 上游 body 摘要），三家 `_is_retryable()` 一致视其为终态、**零重试**（此前 401 被当作可重试的通用 `LLMProviderError`，每分片白跑 3 次，在 provider 后台留下大量被拒请求）。`describe_llm_failure()` 的鉴权文案改为指名「{provider}（{host}）拒绝了当前 API key」并提示临时 token 过期这一成因；host 经 `urlsplit().hostname` 提取，base_url 内联凭据不外泄。裸子串 `"401"` 判定收紧为 `_LLM_AUTH_STATUS_RE` 限定形式（`HTTP 401` / `Error code: 401` / `"code":401` / `status_code=401`，排除 4010/4011），避免 request id 或 402 余额不足回包里的 `401` 让 auth 桶盖掉 rate-limited 桶 |
| OpenAI-compatible 非瞬态 4xx 快速失败 | ✅ | OpenAI 系把 400/403/404/405/422 映射为 provider 内部不可重试请求错误，仍保留原 HTTP 状态与响应体供 registry fallback 和用户诊断，但不再对同一个错误请求立即重发三次；`404 model route not found` 因而在一次请求后快速暴露，等待用户改模型名或路由。401 继续使用带 provider/endpoint 的专用鉴权错误，402/429 继续走 backoff；HTTP 5xx、timeout 与传输错误仍保留有界重试。`response_format` 400 的既有 json_schema 兼容重试发生在 flavor 层，不受 provider 传输重试终态化影响。 |
| v0.3.162+ LLM 失败可操作说明 | ✅ | `llm.base.describe_llm_failure()` 沿异常 cause/context 链翻译上层错误；新增 authentication / unauthorized / unauthenticated / invalid API key / api key not valid 与限定形式 401 鉴权桶，并将 insufficient quota / quota / exhausted / 429 归入「额度用尽或被限流」桶，API 与 CLI 继续消费同一函数，不新增 init reason code |
| v0.3.164 LLM 失败安全边界 | ✅ | `describe_llm_failure()` 识别 moderation、鉴权、额度/限流、provider/service 超时与空响应；`safe_llm_failure_message()` 为 API / CLI / OpenClaw 的公共边界提供固定安全兜底，未知异常不回传上游文本 |
| 异常报警钩子（未发布） | ✅ | `LLMRegistry` 的 fallback 链与精确路由在每个 provider 失败点调用 `record_diagnostics_alert()`：429 → `rate_limited`、鉴权 → `auth_failed`、超时 → `timeout`、空/坏响应 → `bad_response`、其它 provider 异常 → `provider_error`，全部实例失败追加 error 级 `all_providers_failed`；告警进入进程内环形缓冲并由 `/api/diagnostics/alerts` 与 runtime-stream `diagnostics.alert` 事件暴露（见 [api 模块](api.md)），记录本身永不抛错、不影响 fallback 行为 |
| v0.3.160+ Discovery 统一评估契约 | ✅ | 单条与 batch 内容评估 prompt 仅允许 `explore` 保留主题距离例外；`search` / `trending` / `hot` / `feed` / `related_chain` / `channel` / `creator` 及所有平台不得获得基础分、自动加分、较低门槛或事后画像关联，明显不匹配内容允许低于 admission 门槛 |
| 时间中性相关性 + 语义时效分型 | ✅ | `content_evaluation_clock()` 同时生成精确 UTC 评估时间和独立小时缓存桶；单条 / batch prompt 只把精确值放在 user message 的 `<evaluation_context>.evaluated_at`。`score` 不因内容新旧或缺时间变化，模型另行输出 `breaking/current/versioned/evergreen/historical/unknown`、置信度和简短理由；缺失或非法三元组整体降级为 `unknown/0/''`，不会废弃有效 relevance。system prompt 仍是模块级静态常量，推荐池补分类复用相同契约，eval cache namespace 升至 v4 隔离旧评分语义。 |
| 4.5 核心记忆加载 | ✅ | 统一 core memory 注入入口，覆盖 Soul 全链路 |
| v0.3.149+ 关键词合并 prompt 探索 block | ✅ | `build_merged_keywords_prompt()` 支持可选 `explore_domains_block`，只在 runtime 判断 B 站 explore refresh 到期 / 即将到期且有补货空间时追加；system prompt 明确这些 query 是探索性 B 站搜索方向，不应把常规兴趣关键词换皮成 explore。`parse_merged_keywords_with_presence_and_explore_domains()` 在保留平台关键词 decline / omission 语义的同时清洗 `explore_domains` |
| v0.3.147+ Prompt layer cache | ✅ | `profile_prompt_layers()` 把结构化画像拆为 `profile_core` / `profile_life_context` / `profile_interests` / `profile_style_context` / `profile_recent_context`，从稳定到易变排序；`PromptLayerRenderCache` 按层 digest 复用已渲染 JSON prompt block，供 discovery eval、推荐分类 / 文案 / delight 和统一关键词 planner 共享，画像核心不变时 provider 看到的前缀保持 byte-stable |
| v0.3.144+ 缓存前缀保护 | ✅ | `LLMService.complete_with_core_memory()` / `complete_structured_task()` / `complete_multimodal_structured_task()` 支持 `inject_core_memory=False`，供候选 eval、推荐分类 / delight、跨平台关键词生成、awareness / insight / speculation / profile build、初始化偏好分析这类已自带完整结构化上下文的路径跳过重复 memory 注入；`build_soul_profile_prompt()` 也保持静态 system，并把 tone / preference / awareness / insight 放在巨大 history 前，稳定 provider prompt-cache 前缀 |
| v0.3.150+ DeepSeek thinking 显式关闭 | ✅ | `DeepSeekProvider.complete(..., reasoning_effort="")` 会向 DeepSeek 请求体写入 `thinking={"type":"disabled"}`。DeepSeek v4 默认开启 thinking，单纯省略字段并不会关闭 reasoning；配置页 LLM 探测和短结构化任务因此能真正避免 thinking 先耗尽输出预算后返回空 `content` |
| 统一 reasoning effort 默认与映射 | ✅ | 支持原生档位的 provider 默认统一为 `medium`：OpenAI 官方 GPT-5/o-series 分别写入 Chat `reasoning_effort` 或 Responses `reasoning.effort`；Claude 4.6+ 写入 `output_config.effort`；Gemini 3 写入 `thinking_level`，Gemini 2.5 按当前输出上限用 50% thinking budget 近似中档；DeepSeek 将 portable `medium` 按官方规则归一为 native `high`；OpenRouter 用 `reasoning.effort` 跨厂商映射。OrcaRouter 网关把推理参数原样转发给上游路由、非推理模型以 HTTP 400 拒绝，因此**不发送** `reasoning_effort` / `reasoning`（推理模型用自身默认档位）。泛 OpenAI-compatible 无法可靠推断能力：空值不发送，只有新版实例中用户明确填写的非空值才按 OpenAI 字段透传；Ollama 不发送伪参数 |
| 渠道 caller 默认跟随模型 effort | ✅ | `LLMService` 将 `discovery.*` / `recommendation.*` / `sources.*` / `yt_search.*` / `runtime.bilibili_extension_search.*` 以及三类轻量 eval caller 的未指定 effort 解析为 `None`，即跟随各实例配置的 `reasoning_effort`（可手动设为 `low` / `medium` / `high` 或留空跟随模型）；显式 caller 参数始终优先。这样既避免强制空值导致部分 reasoning-first OpenAI-compatible 模型返回 `code 1210`，也让用户能按模型能力自行选择 effort。Soul / 画像与长场景继续使用 provider 的 `medium`（或用户配置值） |
| v0.3.150+ reasoning-only 诊断与兼容端点自愈 | ✅ | OpenAI-compatible / DeepSeek / OpenRouter / Ollama native 返回 HTTP 200 且含 `reasoning_content` / `reasoning` / `thinking`、但最终 `content` 为空时，错误会明确提示 `returned reasoning but no final content` 并带 `finish_reason`，避免和完全空响应混淆。泛 OpenAI-compatible 首请求仍保持标准兼容：空 effort 不发送非标准字段；若调用方明确传 `reasoning_effort=""`，端点却在去掉 `response_format` 后仍返回 reasoning-only，provider 才追加一次 `thinking={"type":"disabled"}` 重试，修复 SenseNova/DeepSeek relay 把输出预算全部耗在默认 thinking 的情况。 |
| 推理预算下限与评估分批自愈（未发布） | ✅ | 结构化调用统一以 `MIN_STRUCTURED_MAX_TOKENS=4096` 兜底 `max_tokens`（`complete_structured_task()` 与 `complete_multimodal_structured_task()`；上限语义，正常答完不产生额外成本，只在模型把预算全花在 thinking 时生效），`api.sentiment` 16→512；`llm.base.is_reasoning_budget_exhausted()` 沿 cause/context 链识别 `returned reasoning but no final content` + `finish_reason=length`，discovery `_evaluate_batch`、recommendation `_classify_batch_with_split_retry` / `_precompute_batch_with_split_retry` 在该签名下把批减半递归重试（深度 3、额外请求 6，直到 JSON 放得下），限流 / 鉴权 / 超时 / 传输错误仍原样上抛；`soul.posture_gate` 与 `soul.preference_analyzer` 改用同一 helper，行为不变。`recommendation.evaluate_batch` 8192→16384、统一关键词 planner 合并生成下限 4096→8192 |
| v0.3.117+ reasoning-first 探活 | ✅ | `LLMProvider.health_check()` 与配置页 LLM 测试探针统一使用 `max_tokens=4096`，避免 SenseNova 等 OpenAI-compatible reasoning-first 模型先产出 `message.reasoning`、尚未到 `message.content` 就被截断，从而误报空响应；通用 health check 同时显式传 `reasoning_effort=""`，所以 DeepSeek 不会让一次连通性探针继承 `medium/high/max`、扩成 16K/32K thinking 请求后在 init 门禁内假超时 |
| LLM Provider 实例路由 v2 | ✅ | `[llm.instances.<id>]` 把 adapter 类型与渠道端点解耦，同类型可配置多个 Base URL / token / model；`default_chain` 是任意长度全局故障切换链，`[llm.routes.<module>]` 默认继承，也可拥有自己的有序链。模块自定义链耗尽后不会越界 spill 到全局链；路由引用了未注册 / 已停用实例时按 bucket 打 WARNING（含实例 ID 与修复提示，issue #213：此前只是 INFO 级，用户看不到「聊聊口味」永久卡死的真实原因） |
| 实例模型发现与可编辑选择 | ✅ | PC Web、插件与 setup 把当前未保存实例交给 `POST /api/config/discover-models`，后端精确调用该端点的 OpenAI-compatible `GET /models`，不保存配置；模型和 Effort 都是可手填的 combobox，发现失败保留原输入。该草稿端点在 active registry 无法构建的 degraded 恢复态仍精确放行，不会被旧配置造成的 503 阻断。协议只标准化模型列表，没有 effort capability 枚举，因此 Effort 选项是本地 advisory，不冒充服务端事实 |
| v0.3.75 Per-module LLM 路由生效 | ✅ | `LLMService` 按 caller bucket 路由 soul / discovery / recommendation / evaluation；旧 `[llm.<module>] provider/model` 会无损投影为 v2 模块实例链，保留兼容但不再是推荐写法 |
| v0.3.75 Provider per-call model | ✅ | OpenAI / Claude / Gemini / DeepSeek / Ollama / OpenRouter / OrcaRouter / OpenAI-compatible 的 `complete(..., model=...)` 支持单次模型覆盖，不修改 provider 实例默认 `_model` |
| 体验优化：B站动态语气 | ✅ | 推荐、画像总结和聊天 prompt 统一接入 `ToneProfile`，在“老B友”基础上按用户画像微调语气 |
| v0.3.0 Ollama embedding 兜底 | ✅ | `OllamaProvider.embed()` 走原生 `/api/embeddings`，配合 `bge-m3` 模型可在 Mac/Win/Linux CPU 跑相似度计算，不需要额外的 embedding API Key |
| v0.3.0 EmbeddingService 双层缓存 | ✅ | L1 内存 + L2 SQLite 持久化；`build_embedding_service` 按 provider 自动选默认 model（gemini→gemini-embedding-001 / openai→text-embedding-3-small / ollama→bge-m3） |
| 可选封面 image-only embedding | ✅ | `[llm.embedding].multimodal_enabled` + 多模态 embedding 模型（`gemini-embedding-2` 族，或 `dashscope` + `qwen3-vl-embedding` 等）时，`EmbeddingService.embed_image()` 把压缩封面打成向量，与文本同 `model`/维度空间；discovery 入池按封面 URL 派生键（`image_embedding_cache_key_for_url`）预热，Delight 线上 `precompute_delight_scores` 消费（见 [recommendation 模块](recommendation.md) 封面视觉加成）。默认关闭；provider/model 不支持图像或开关关闭时自动 no-op（纯文本模型零成本、打分与旧版逐字节一致） |
| 视觉 / document embedding provenance | ✅ | dedicated `EmbeddingService` 为 openai / openai-compatible / gemini / ollama / dashscope / openrouter 及 fallback 建立稳定 provenance：逻辑 provider、去掉 query/fragment/userinfo 的规范化 endpoint、model 和实际已知 output dimension。`embedding_fingerprint` 与 L2 SQLite model namespace 同时隔离不同 endpoint；同配置跨进程稳定且不落 API key。`embed_document()` / `lookup_cached_document()` 对弹幕摘要使用完整文本 key，空向量不写缓存。关键帧 cache key 由 fingerprint + sampling signature + stable sampled-slot 隔离，避免模型、维度或采样变更复用旧向量 |
| DashScope 多模态 embedding | ✅ | `provider = "dashscope"` → `DashScopeEmbeddingProvider`：原生 multimodal-embedding API；`embed`/`embed_image` 独立向量（不 `enable_fusion`）；默认 `qwen3-vl-embedding`；`output_dimensionality` 对 qwen3-vl 透传 `dimension`；embedding-only（`complete` 拒绝） |
| v0.3.113 Embedding 目标维度 | ✅ | `[llm.embedding].output_dimensionality` 默认 1024，与 Ollama `bge-m3` 对齐；Gemini 传 `output_dimensionality`，`provider = "openai"` 且模型为 `text-embedding-3-*` 时传 `dimensions`，Ollama / OpenRouter / 泛 OpenAI-compatible 等未确认支持的后端不传。L2 cache 仅在 provider 确认支持目标维度时按 `model#dim=N` 签名隔离，同一文本的不同维度向量不会互相覆盖，也不会把未生效的兼容后端标成伪维度 |
| v0.3.155 Ollama embedding 诊断 + 自修 | ✅ | `llm/ollama_diagnostics.py`：`diagnose_ollama_embedding()` 把向量模型不可用分类为 `not_running` / `model_missing` / `model_broken` / `model_path_encoding` / `disk_full` / `network` / `model_oom` / `error`（先 `/api/tags` 判定服务与模型在位，再真打一次 embed——覆盖"模型在列表里但加载失败"的 500 场景）。`model_path_encoding` 专指 Windows 非 ASCII 用户名 / mojibake 路径导致 `llama-server` 无法从 `.ollama\models` 加载模型的失败，重新拉取不会修复，需迁移模型目录或手动设置 `OLLAMA_MODELS` 到纯英文路径；`model_oom` 从旧 `model_broken` 中拆出，明确内存不足时重拉无效；`disk_full` 既识别 pull / probe 错误文本，也会在拉取前检查 `OLLAMA_MODELS` / 托管模型目录所在卷是否至少有约 2.0GB 空间；`network` 区分无法访问 registry 的下载源问题与本地模型损坏。`pull_ollama_model()` 经原生 `/api/pull` 流式拉取 / 重拉模型并回调进度；两者均 `trust_env=False` 且可注入 `httpx.MockTransport` 测试。`OllamaProvider.embed()` 失败日志附带响应体错误片段（此前只有裸状态码）。供 `/api/init-status` 的 `embedding_check`/`embedding_detail` 与 `POST /api/embedding/repair` 一键修复使用（见 [init 模块](init.md)）。v0.3.206+：识别 Windows 访问违规崩溃（`0xc0000005` / `access violation`），在 `model_broken` 内给出「一键重拉 → 重启 → 内存/虚拟内存 → 杀软白名单 → 升级」排序清单，不再只提示「下载不完整或内存不足」 |
| v0.3.97 EmbeddingService 实时探活 | ✅ | `EmbeddingService.probe()` 绕过 L1/L2 缓存直接打一次 provider，返回是否拿到非空向量；供 `/api/health.embedding_ready` 做**实时**就绪判定（缓存命中的旧成功不会掩盖 provider 已掉线 / 模型没拉）。`/api/health` 侧自带 TTL + single-flight，probe 不缓存结果、每次都真打 |
| v0.3.114 配置页服务探测 | ✅ | `POST /api/config/probe-service` 对用户当前表单草稿做无写入真实探测：LLM 走临时 `LLMRegistry.complete_provider()`，embedding 走临时 `EmbeddingService.probe()`，结果供 PCWeb / 插件设置页行内展示。它属于 degraded 恢复控制面：不依赖失败的 active registry；也属于 guided init 写端门控的只读例外，初始化运行时仍可测试。真实 LLM 请求始终经过 RuntimeContext 的稳定 total gate；LLM 实例 / 链的外层 deadline 按配置 clamp 到 10–120 秒，桌面与 popup 使用 125 秒请求预算以覆盖本地 Ollama 冷启动。 |
| v0.3.20 Embedding fallback 能力识别 | ✅ | `LLMProvider.supports_embedding` 类属性显式声明 provider 是否真的有 embeddings endpoint。Claude / DeepSeek / OpenRouter / OrcaRouter 标 `False`（前者无 API、其余继承自 OpenAIProvider 但实际后端不路由 embeddings）；OpenAI / Gemini / Ollama 标 `True`。当前只在 `[llm.embedding].fallback_provider` 非空时尝试一个显式备选 provider |
| v0.3.89.1 OpenRouter embedding 显式路径 | ✅ | `[llm.embedding].provider = "openrouter"` 会构造独立 `OpenRouterProvider`（必须配 `model = "<vendor>/<model>"`）。它不参与 chat 实例链；embedding 自己未填凭据 / headers 时，可兼容借用首个启用的同类型 chat 实例，旧 `[llm.openrouter]` 也继续可读 |
| v0.3.20 OpenAI Provider embed | ✅ | `OpenAIProvider.embed()` 走 `/v1/embeddings`，默认 `text-embedding-3-small`。OpenAI 用户没显式配 embedding 时不再静默返回 None。失败返回 `[]`（与 Ollama / Gemini 一致），调用方降级处理 |
| v0.3.31 DeepSeek 空内容兜底 | ✅ | DeepSeek 返回 HTTP 200 但 `content=""` 时，provider 会重试一次；`reasoning_effort` 开启时仍先关闭 thinking 重试，普通模式则原参数重试，避免 explore / structured task 因一次空内容直接降级为空结果 |
| v0.3.32 Embedding 与 LLM Provider 解耦 | ✅ | `EmbeddingConfig` 拥有独立的 `api_key` / `base_url` / `output_dimensionality`；`build_embedding_service` 直接构造一个独立 provider 实例（不走 chat-side `LLMRegistry`），切换 chat 模型不会改变 embedding provider / model / 维度，并把旧的 `embedding_wants_ollama` 自动注册 hack 删掉 |
| 旧显式 fallback provider 兼容 | ✅ | 旧 `default_provider → fallback_provider` 仍可加载并投影为实例链；一旦由新版设置页保存，chat 配置写为 v2，并在首次迁移前永久保留 `config.toml.pre-llm-routing.bak`。回退旧二进制时用 `config-export-legacy` 生成固定 Provider 副本并显式报告同类型实例折叠 / 长链截断。Embedding 本轮仍保持独立 `[llm.embedding]` 配置和单备选语义，不跟随 chat 默认链 |
| Ollama chat 模型必须显式配置 | ✅ | 每个 `provider_type="ollama"` chat 实例都必须有非空 `model`；`base_url` 只定位服务，不能推断机器上有哪些模型，也绝不再暗补 `llama3`。配置保存与安装器会把缺 model 作为 blocking 项；embedding 仍由独立 provider 使用自己的 `bge-m3`，互不影响。旧 `[llm.ollama]` 保持兼容 |
| v0.3.32 OpenAI 协议兼容 provider | ✅ | `provider_type="openai_compatible"` 用于 Groq / Together / Azure OpenAI / vLLM / 自建等走 OpenAI 协议的服务；同类型实例之间凭据与 Base URL 完全隔离，`base_url` 必填。旧 `[llm.openai_compatible]` block 继续兼容，embedding 段也接受该类型 |
| Gemini reasoning-first 模型适配 | ✅ | Gemini thinking 不再由 `json_mode` 隐式决定，而由统一 effort 驱动：3.x 使用 `thinkingLevel`（已知仅支持 LOW/HIGH 或 MINIMAL/HIGH 的型号会映射到最近安全档）；2.5 Pro 使用合法正 budget，空渠道请求降到官方最小 128；2.5 Flash / Flash-Lite 的空渠道请求用 `thinking_budget=0` 真正关闭。这样既不向 reasoning-first 型号发送非法 zero budget，也不会把所有结构化深度任务一刀切关闭 reasoning |
| v0.3.71 Prompt-cache 与 400 诊断 | ✅ | `build_awareness_prompt` / `build_batch_content_evaluation_prompt` / `build_soul_profile_prompt` 的 user prompt 按稳定画像 / tone / preference 在前、本次批次或历史在后排序，并使用 `sort_keys=True` 的确定性 JSON；`OpenAIProvider._map_error()` 会把 OpenAI-compatible HTTP 400 响应体摘要写入 WARNING 和错误文本，便于定位 MiMo 等兼容服务的请求 schema 问题 |
| v0.3.71 Awareness 缓存形态回归锁 | ✅ | `build_awareness_prompt` 的 system 内容固定为模块级常量 `_AWARENESS_SYSTEM_PROMPT`，user 块顺序锁定为 `<soul_profile>` → `<preference_summary>` → `<recent_events>`，并通过 `tests/test_llm_prompts.py` 的 byte-equal / 末尾块 / 不同字典 key 序仍产相同字节三组回归测试保证未来改动不会再把变量数据放进 system、不把 recent_events 之后塞入稳定块、或丢掉 `sort_keys=True` |
| 偏好 prompt 内部字段过滤 | ✅ | 新增 `profile_views.preference_prompt_payload()`：在 `build_preference_analysis_prompt` / `build_awareness_prompt` / `build_awareness_with_confusions_prompt` / `build_insight_prompt` / `build_soul_profile_prompt` 五个 builder（前四条的 legacy 与 compact-v1 两条视图、灵魂画像单视图）以及 `render_preference_summary` 渲染器的入口剔除持久化兴趣记录里的内部记账字段（当前为增量衰减游标 `last_decay_at`），`build_cognition_profile_view_v1` 的递归投影同样过滤。该游标每次合并都会变化、不代表用户行为，序列化只会吃 prompt 预算并把偏好分析顶过 `max_prompt_chars` 触发额外分块；无游标输入的 prompt 逐字节不变（`tests/test_llm_prompts.py` 锁定） |
| v0.3.74 结构化输出共享解析 | ✅ | 新增 `llm/json_utils.py`，统一提供 `extract_llm_json_list()` / `extract_llm_json_object()` / `parse_llm_json_tolerant()`。调用方可传 item/object predicate 和 wrapper aliases，兼容 root array/object、`results/items/data/output/scores/evaluations` 等 wrapper、单行或 pretty-printed 多行 singleton dict、Markdown fenced JSON、JSONL、多 root echo 后最终结果，以及 MiMo 形态的 malformed `{ [ ... ] }` 数组包裹；`allow_singleton=True` 会显式把 root object 包成单元素列表，不再偶然依赖只支持单行的 JSONL fallback |
| v0.3.74 Ollama embedding 空凭据静默本地默认 | ✅ | `embedding.provider="ollama"` 且 embedding `api_key/base_url` 为空时直接构造本地 Ollama provider，默认 `http://localhost:11434/v1`；如果存在启用的 Ollama chat 实例，会优先复用首个匹配实例的地址并规范化到 `/v1`，旧 `[llm.ollama].base_url` 仍作为兼容来源，且都不会触发 `_emit_embedding_compat_warning()`。远端 embedding provider 留空凭据时仍保留一次性向后兼容 WARNING |
| v0.3.77 LM Studio JSON mode 兼容 | ✅ | `OpenAIProvider` 的 `json_mode=True` 对普通 OpenAI-compatible 后端默认使用 `json_object`，遇到 `response_format.type` 只允许 `json_schema/text` 时用通用 `json_schema` 重试；对本地 LM Studio（默认 `localhost/127.0.0.1:1234` 或 URL 含 `lmstudio` / `lm-studio`）首次请求即不发送 `response_format`，依赖 prompt 约束 JSON，避免 compat 层在 `json_object` / `json_schema` 下丢失 `message.content` 后再浪费一整次 LLM 调用 |
| v0.3.78 Codex OAuth 实验认证 | ✅ | OpenAI 实例设置 `auth_mode="codex_oauth"` 时复用 Codex CLI 的 ChatGPT OAuth 凭据；`codex_auth.py` 负责安全导入、落盘和刷新，`codex_chatgpt_provider.py` 走官方 `chatgpt.com/backend-api/codex/responses` SSE 传输（issue #170）。只允许官方 Codex 域名，拒绝把 ChatGPT token 发给第三方或 Platform API；旧 `[llm.openai]` 写法继续兼容 |
| v0.3.x LLM 限流识别 | ✅ | `is_llm_rate_limit_error()` 会沿异常链识别 `LLMRateLimitError`、cooldown、429 / quota / resource exhausted 文本；discovery / recommendation 批量调用据此跳过逐条 fallback，避免一次 provider 限流放大成 N 个必失败调用和堆栈日志 |
| v0.3.x 余额 / 账单错误熔断 | ✅ | OpenAI-compatible provider 会把 HTTP 402、`Insufficient Balance`、`payment required`、`billing`、余额不足等 provider 余额 / 账单失败归一为 `LLMRateLimitError`，跳过 provider 内部 retry，并让 registry cooldown 与批量任务的“跳过逐条 fallback”保护生效 |
| v0.3.x Eval-batch 负样本锚定与跨平台公平 | ✅ | `build_batch_content_evaluation_prompt` 新增可选 `negative_examples` kwarg；非空时在 user prompt `<source_context>` 与 `<content_batch>` 之间插入 `<negative_examples>` 块（`sort_keys=True` 决定性 JSON）。`None` / `[]` 退回原 user 字节形态以保留 cold-start 缓存前缀。`_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT` 加入永久规则：按话术 / 商业意图 / 标题结构层面 pattern-match 候选与示例，不要看关键词重叠；混源 batch 中不得仅因 `source_platform` 不同而抬高或压低 preference score，只能把平台作为内容语境。规则改动一次后 system message 保持 call-invariant |
| v0.3.171 Eval reason 减肥 | ✅ | `_SINGLE_CONTENT_EVALUATION_SYSTEM_PROMPT` / `_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT` 明确 reason 仅供内部诊断：`score < 0.5` 写空串，`score >= 0.5` 写不超过 30 个 Unicode code points 的精炼中文。`discovery.eval_reason.normalize_evaluation_reason()` 在 single / batch / cache hit 路径强制同一契约：`None`→空串，其它非字符串 fail closed，高分 strip+截断，低分与 diversity-cap drop 清空；缓存和持久化只接收归一化值。0.5 保持静态 prompt 常量且低于全部 admission 路径，推荐表达 / delight copy 不展示 `relevance_reason`。旧 2026-07-18 PASS 已作废；有效 replay v2 必须基于同一冻结 effective profile（含 overrides/speculations）与候选快照跑重复 A/A+A/B，使用生产 4096 ceiling、30 条 claim grouping 和 `mixed` context，逐 run 验证实际 route、embedding/recall 完整性，并输出 raw scores 与所有 blocking reasons |
| v0.3.x dislike-aware prompts | ✅ | `build_preference_analysis_prompt` 明确把 negative / dislike / thumbs_down 事件限制为 `disliked_topics` 与风格避让证据，禁止提取为正向兴趣；`build_awareness_prompt` 可从近期 dislike 生成“最近开始避开 X”的保守观察；单条 / 批量推荐表达 prompt 会消费 `profile_summary.disliked_topics`，命中避雷项时不得热情背书 |
| 负反馈一致性规则（issue #205） | ✅ | 两个 awareness system prompt（含/不含 confusions）均新增「负反馈一致性」规则：笔记声称「点踩 / dislike / 不感兴趣」时，所引事件必须真的存在 `feedback_type=dislike` 或 `inferred_satisfaction=negative`；近期事件里没有负反馈事件就绝不能在笔记中声称用户点踩。防止上游捕捉噪声被 LLM 放大为强负面叙事 |
| v0.3.x 避雷探针多样性 prompt | ✅ | `build_avoidance_generation_prompt` 会携带 `existing_avoidance_details`，让 LLM 看到已有 active 的 `source_mode`、`source_signal`、体验轴和 specifics；system prompt 要求同一 `source_mode` + 同一粗主题 / 证据源只生成一个候选，已有 AI positive_boundary 时不再输出 AI 教程 / 测评 / 趋势换皮项 |
| v0.3.x 第三方 API 网关适配（issue #72） | ✅ | Claude 实例的 `base_url` 可指向任意 Anthropic 协议网关；OpenAI / OpenAI-compatible 实例的 `api_flavor` 可选 Chat Completions 或 Responses。每个实例独立持有渠道 URL / token / model，所以同一 adapter 的多个网关可同时注册并互为降级；非法组合由配置校验 blocking 拦截 |
| v0.3.162+ 托管 Ollama 生命周期自愈 | ✅ | `runtime/ollama_supervisor.py` 记录托管 daemon 的完整启动规格并新增 watchdog；`with-embedding` 私有 11435 daemon 纳入一键修复与崩溃自动拉起（详见下方[托管 Ollama 生命周期](#托管-ollama-生命周期v03162)） |
| v0.3.165 海外网络三模式 | ✅ | `OpenAIProvider` / `ClaudeProvider` / `GeminiProvider`（含 DeepSeek / OpenRouter / OrcaRouter 子类与 embedding 实例）同时接收 `proxy` 与 `trust_env`。registry 统一读取 `[network].mode`：`direct` 注入忽略环境代理的 SDK transport，`system` 保留 SDK 环境继承，`custom` 注入指定代理并强制 `trust_env=False`。**Ollama 工厂不读该策略**——本地 / CN 直连由 `tests/test_network_proxy_isolation.py` 守卫 |
| v0.3.166 国内网关代理豁免 | ✅ | registry 按每个实例的 `base_url` 独立裁决代理，委托 `network.is_domestic_endpoint()`。国内大模型网关与 localhost / 内网自建端点即使全局为 `system` / `custom` 也强制直连；同一链里的境外实例仍走全局代理策略 |
| Issue #113 CA 环境防护 | ✅ | `network.set_outbound_proxy(..., mode="system")` 在任何继承环境的 SDK 客户端构造前检查 `SSL_CERT_FILE` / `SSL_CERT_DIR` / `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE`。只移除指向不存在目标的失效覆盖，让 httpx / OpenSSL 回退到默认可信 CA store；有效私有 CA、`HTTPS_PROXY` 等代理变量和 TLS 验证均保持不变，避免 Windows 遗留 CA 路径导致所有客户端在发请求前直接 `FileNotFoundError`。 |
| Issue #113 task-local 后台准入 bypass | ✅ | 内部 scope 通过 `ContextVar` 只影响当前异步上下文；scope 内 `LLMService.complete_with_core_memory()` 跳过库存敏感的后台 admission，但仍经过总 provider gate，退出 scope 后自动恢复。guided init 仅在阶段 2/3 使用，并行 discovery 不继承该 scope；既有公开 API 签名不变。 |
| Issue #153 L2 缓存版本化 BLOB | ✅ | `EmbeddingCache` 改为版本化 little-endian float32 二进制存储（`encoding=1`，`OBLV` 头 + dtype/dimension 元数据），4096 维向量从约 90 KiB JSON 降到约 16 KiB。读取对 legacy JSON（`encoding=0`）、BLOB 与 mixed-format（旧版回写）内容自适应解码，单行损坏降级为 cache miss。`migrate_encoding()` 按小批次幂等迁移（中断可续跑，进度即 `encoding` 列本身），损坏行标记 `encoding=-1` 后跳过；schema 升级保留旧行并补齐 `dimension` / `created_at` / `last_accessed_at` 元数据列 |
| Issue #153 L2 容量策略与 namespace 生命周期 | ✅ | 新增可配置磁盘预算（`[llm.embedding].cache_max_bytes`，默认 0 = 不设上限）与高低水位；超高位后按「非 active namespace（含 legacy 行）→ active namespace 最旧/最近未访问」顺序批量淘汰到低位，写路径按节奏抽样检查避免逐行开销。`last_accessed_at` 每次命中最多每分钟刷新一次。`delete_inactive()`（含 dry-run / keep_legacy）显式回收失效 namespace，`compact()` 执行 WAL checkpoint + `VACUUM INTO` + `integrity_check` + 原子替换做物理回收；`stats()` / `EmbeddingService.l2_cache_stats()` 暴露行数、逻辑载荷、主文件/WAL 大小、namespace 分布、水位与最近维护记录 |
| Issue #153 L2 运行时准备 | ✅ | `EmbeddingService` 构造时注册自身 provenance namespace 为 active，并执行一次/进程/库的运行时准备（JSON→BLOB 迁移 + 首轮预算维护，`_PREPARED_DB_PATHS` 去重）；任何准备失败只降级为普通缓存，不影响推荐正确性。CLI `embedding-cache-stats` / `embedding-cache-clean`（默认 dry-run，`--apply` 生效，`--keep-model` / `--keep-legacy` / `--no-compact` / `--batch-size`）提供诊断与一键回收入口 |
| Issue #188 Embedding 熔断 | ✅ | `EmbeddingService` 新增 circuit breaker：连续失败（异常或空向量）达到阈值（默认 3）后进入冷却窗口（默认 60s），冷却期内 `embed` / `embed_document` / `embed_image` / `probe` 直接返回 `[]` / `False`，不再触碰死端点，也不再逐条打 full-traceback WARN；冷却结束自动重探。阈值与冷却秒数可通过构造参数覆盖，便于测试与运维调参 |

### Embedding provenance API

`EmbeddingService.embedding_fingerprint`、`embedding_model`、`embedding_provider` 和
`embedding_dimension` 为推荐层提供当前向量命名空间；`embed_document()` 与
`lookup_cached_document()` 保留完整文档 key，专供弹幕摘要使用。`embed()`、`embed_image()`
和文档 embedding 在 provider 返回空向量时都不写完成缓存；连续失败会触发熔断冷却，防止
不可达 embedding 端点刷日志或反复出网。`EmbeddingService.l2_cache` /
`l2_cache_stats()` 把 L2 持久化缓存暴露给诊断与维护面（CLI 清理等），namespace 注册保持一致。

## 公开 API

### Provider 类

```python
from openbiliclaw.llm import (
    ClaudeProvider,
    DeepSeekProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAIProvider,
    OpenRouterProvider,
    OrcaRouterProvider,
)

# 创建 provider
provider = OpenAIProvider(api_key="sk-...", model="gpt-4o")
response = await provider.complete([{"role": "user", "content": "hello"}])
print(response.content)  # str
print(response.provider)  # "openai"
print(response.usage)     # {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}

# 单次调用覆盖模型；不会写回 provider._model
response = await provider.complete(
    [{"role": "user", "content": "hello"}],
    model="gpt-4.1-mini",
)

# JSON mode；普通 OpenAI-compatible 后端使用 response_format 约束并保留 json_schema fallback。
# 本地 LM Studio 首次请求即跳过 response_format，依赖 prompt 约束 JSON 输出。
response = await provider.complete(
    [{"role": "user", "content": "只返回 JSON 对象"}],
    json_mode=True,
)

# 健康检查
available = await provider.health_check()  # bool
# health_check 使用 max_tokens=4096，兼容先输出 reasoning 再输出 content 的服务。
# 设置页 / 插件的配置探针也使用同一个连通性探针预算。

provider = OpenRouterProvider(
    api_key="or-...",
    model="openai/gpt-4o-mini",
    http_referer="https://example.com",
    x_title="OpenBiliClaw",
)

provider = OrcaRouterProvider(
    api_key="sk-orca-...",
    model="openai/gpt-4o",
    base_url="https://api.orcarouter.ai/v1",
)

provider = GeminiProvider(
    api_key="gemini-key",
    model="gemini-2.5-flash",
)
```

### Codex OAuth 凭据辅助

```python
from openbiliclaw.llm.codex_auth import (
    get_valid_codex_token,
    import_codex_credentials,
    load_codex_credentials,
)
from openbiliclaw.llm.codex_chatgpt_provider import probe_codex_llm

# 导入官方 Codex CLI 登录态，默认读取 ~/.codex/auth.json，
# 写入 ~/.openbiliclaw/codex_auth.json。
credentials = import_codex_credentials()
print(credentials.account_id)

# Provider 运行时会调用它；临期时自动刷新。
token = await get_valid_codex_token()

# 真实能力探测：验证 ChatGPT token 能否调用官方 Codex LLM 通道。
# 结果会持久化到凭据文件，供 CLI --status 与 /api/health 区分
# "已导入" 与 "可调用"。
probe = await probe_codex_llm()  # model 留空 → 自动发现账号可用的 Codex 后端模型
print(probe.ok, probe.message)
```

Codex OAuth 是实验路径：OpenAI 官方 API 认证仍以 Platform API key 为稳定入口；该模块只复用本机 Codex CLI 凭据，不自建 OAuth PKCE 浏览器流程，也不会把 token 打印到 CLI 输出。`auth_mode="codex_oauth"` 使用独立的 `CodexChatGPTProvider`，把请求发往 `https://chatgpt.com/backend-api/codex/responses`（官方 Codex CLI 同款通道），不再把 ChatGPT token 当作 Platform API Key 发给 `api.openai.com`。

### Registry

```python
from openbiliclaw.llm import build_llm_registry
from openbiliclaw.config import load_config

registry = build_llm_registry(load_config())
print(registry.available_providers)  # 实例 ID，例如 ["deepseek-cn", "relay-hk"]
print(registry.default_provider)     # default_chain 的第一项，例如 "deepseek-cn"
print(registry.provider_type("relay-hk"))  # adapter 类型，例如 "openai_compatible"

# 按 [llm].default_chain 依次尝试；响应标记最终命中的实例 ID
response = await registry.complete([{"role": "user", "content": "hi"}])
print(response.instance_id)

# 也可执行一条显式实例链；用于模块自定义链和配置探测
response = await registry.complete_chain(
    ["relay-hk", "deepseek-cn"],
    [{"role": "user", "content": "hi"}],
)

# 精确调用单个实例，不走 fallback
response = await registry.complete_provider(
    "deepseek-cn",
    [{"role": "user", "content": "hi"}],
)
assert registry.is_chat_capable("deepseek-cn")

# 所有 chat-capable 实例的健康检查（embedding-only 项不会收到 chat 请求）
results = await registry.health_check_all()
# {"deepseek-cn": HealthCheckResult(available=True, is_default=True), ...}
```

### 配置草稿探测 API

```http
POST /api/config/probe-service
```

该接口面向设置页，不写配置文件。后端会把请求中的 `config.llm` 合并到当前配置的内存副本，然后按 `kind` 真实打一次目标服务：

- `kind="llm_instance"`：要求 `instance_id`，只探测一个实例，不触发其他实例。
- `kind="llm_chain"`：按草稿中的 `default_chain` 真实执行故障切换，返回最终命中的 `instance_id`。
- `kind="llm"` / `kind="llm_fallback"`：旧客户端兼容入口，分别映射旧默认项和旧备选项。
- `kind="embedding"`：构建临时 `EmbeddingService`，调用 `probe()` 绕过 L1/L2 cache 获取一次真实向量。

chat 探针使用 `max_tokens=4096`，避免 reasoning-first 服务尚未输出最终内容就被误判。LLM 实例 / 链的 outer timeout 使用 `[llm].timeout`，但固定夹在 10–120 秒之间：120 秒是有限控制面预算，同时覆盖 Ollama 约 31 秒的冷加载重试窗口；桌面 Web 与 popup 的 fetch timeout 为 125 秒，确保客户端不会先取消仍由后端合法持有的请求。deadline 到期返回结构化 `ok=false` 与 `LLM connectivity probe timed out after Ns.`，不抛裸 500。其它失败同样以 `ok=false` 的正常响应返回，前端可直接显示实例 / provider 类型 / model / latency / error。active registry 因旧配置构建失败时，该端点仍从提交草稿临时建 registry，并继续通过 RuntimeContext 的 total gate；guided init 运行时也精确放行这个无写入端点，LLM / 整链 / embedding 测试不会再被 POST 写端守卫误判为 `409 init_running`。它不会把 degraded 业务流量或配置保存一并放开。详见 [配置参考](config.md)。

### 配置草稿模型发现 API

```http
POST /api/config/discover-models
```

该接口同样不写配置。请求携带 `instance_id` 与当前页面的 `config.llm` 草稿；后端在内存副本中应用草稿，精确使用该实例自己的 Base URL、API Key、网络策略与认证方式调用 OpenAI-compatible `GET /models`。保存过的 masked key 会按配置更新的既有规则保留真实密钥，响应只返回排序去重后的模型 ID、耗时和安全错误，不回传凭据。它与草稿探测一起列入 degraded 精确 allow-list，因此旧 active registry 坏掉时仍可用 replacement draft 找模型。

- 支持 `openai` / `deepseek` / `openrouter` / `orcarouter` / `ollama` / `openai_compatible`；其他原生协议返回 `ok=false` 与继续手填的说明。
- 拉取失败、端点没有实现 `/models` 或列表为空都不会覆盖页面里已经输入的模型。
- OpenAI Models API 只提供 ID 等基础元数据，没有标准字段声明某模型支持哪些 reasoning effort。响应里的 `reasoning_efforts` 因此标记为 `local_advisory`，用于方便选择而不是服务端能力承诺，输入框始终允许手填。

### LLMService

```python
from openbiliclaw.llm import LLMService
from openbiliclaw.llm.service import module_overrides_from_config

service = LLMService(
    registry=registry,
    memory=memory_manager,
    module_overrides=module_overrides_from_config(config),
)
response = await service.complete_socratic_dialogue(
    user_message="我最近喜欢看纪录片",
    history=[...],
)
# prompt 自动包含用户画像（core memory）和动态 tone profile，空响应自动拦截

response = await service.complete_structured_task(
    system_instruction="你要从用户行为中提取结构化偏好。",
    user_input='{"events": [...]}',
)
# 自动注入 core memory，并以 json_mode 调用 provider

response = await service.complete_structured_task(
    system_instruction="你要批量评估候选内容。",
    user_input="<profile_core>...<profile_recent_context>...<content_batch>...",
    caller="discovery.evaluate_batch",
    inject_core_memory=False,
)
# 已在 user_input 携带完整结构化上下文的高频结构化任务
# (如候选 eval / 推荐分类与 delight / 关键词生成 / 画像分析) 可关闭额外 core memory 注入，
# 让 provider-side prompt cache 前缀更稳定。

from openbiliclaw.llm import is_llm_rate_limit_error

try:
    await service.complete_structured_task(system_instruction="...", user_input="...")
except Exception as exc:
    if is_llm_rate_limit_error(exc):
        # 批量调用方可跳过逐条 fallback，等待下一轮调度重试。
        ...
```

### Dialogue insight prompt

```python
from openbiliclaw.llm.prompts import build_dialogue_insight_prompt

messages = build_dialogue_insight_prompt(
    user_message="我支持这个判断，但想把表述改得更准确。",
    assistant_reply="你是在修正范围，而不是否定方向。",
    core_memory=memory.get_core_memory(),
    active_list={
        "speculations": [{"domain": "桌游"}],
        "insights": [{"hash": "2d0a6ff1", "hypothesis": "用户重视原始研究"}],
        "confusions": [{"id": "7", "topic": "桌游"}],
    },
    anchor={
        "kind": "hypothesis",
        "ref": "2d0a6ff1",
        "generation": 3,
        "hypothesis": "用户重视原始研究",
    },
)
```

`anchor` 是可选公开参数。未传或传 `None` 时，builder 不增加任何锚段，继续输出普通 `{candidates, settles}` 契约；传入时只在 user message 增加当前锚和 `anchor.relation` 契约，允许 hypothesis 的 `support/contradict/revise/ambiguous/unrelated` 或 confusion 的 `answer/ambiguous/unrelated`。调用方仍必须用持久化 ref+generation 做 CAS，prompt 中的 generation 不是授权凭证。

### Core-memory 注入默认表（维护/画像类调用点）

`complete_structured_task()` / `complete_with_core_memory()` 默认 `inject_core_memory=True`。
下表记录 Soul 维护类调用点经 Task 8 审计后的最终注入策略；完整逐点理由见
[`docs/profile-usage.md`](../profile-usage.md) 的「Maintenance-caller injection audit」。

| 调用点 | 注入 | 依据 |
| --- | --- | --- |
| `soul/consolidator.py` 簇裁决 | ❌ opt-out | 只按 user prompt 的簇成员列表裁决合并/保留，画像无关 |
| `soul/category_migration.py` 分类映射 | ❌ opt-out | 纯分类名规范化，无用户特定判断 |
| `soul/pool_purge.py` 厌恶精判 | ❌ opt-out | 判定材料（新厌恶 + 全部厌恶 + 候选）已全在 user prompt |
| `soul/dialogue_insight_analyzer.py` 洞察抽取 | ❌ opt-out | prompt 已显式 `json.dumps(core_memory)` 进 user 消息，注入是重复 |
| `soul/layer_updaters.py` role / values / core 更新 ×3 | ✅ 保留 | 更新画像层自身，注入上下文帮 LLM 把证据 connect 到用户情境 |
| `api/app.py` 探针情感判定 | ✅ 保留 | 聊天邻接，在用户自身语境里读语气/意图 |

维护类调用点关闭注入统一用 `llm.task_options.without_core_memory_kwargs(fn)`，它在旧 stub
不支持该参数时安全降级为空 kwargs。

### 结构化 JSON 解析 helper

```python
from openbiliclaw.llm.json_utils import extract_llm_json_list, extract_llm_json_object

scores = extract_llm_json_list(
    response.content,
    wrapper_aliases=("scores", "evaluations"),
    item_predicate=lambda item: isinstance(item, dict) and "score" in item,
)

profile_delta = extract_llm_json_object(
    response.content,
    wrapper_aliases=("result", "data"),
    object_predicate=lambda obj: isinstance(obj, dict) and "summary" in obj,
)
```

这些 helper 是 MiMo / OpenAI-compatible / reasoning 模型结构化输出的统一容错边界。`allow_singleton=True` 接受格式化为多行的合法 root object，并把它视为一个列表成员；调用方仍应用 predicate 限定自己真正接受的 shape，避免 schema echo 或 prompt 示例被误当作结果。

### Merged keyword prompt

```python
from openbiliclaw.llm.prompts import (
    build_merged_keywords_prompt,
    parse_merged_keywords_with_presence_and_explore_domains,
)

messages = build_merged_keywords_prompt(
    profile_summary=profile_summary,
    profile_blocks=profile_blocks,
    platform_blocks=[{"platform": "bilibili", "need": 8, "recent_keywords": []}],
    explore_domains_block={
        "need_domains": 5,
        "queries_per_domain": 3,
        "covered_topic_groups": ["人工智能", "认知科学"],
    },
)
keywords, present, explore_domains = parse_merged_keywords_with_presence_and_explore_domains(
    response.content,
    ["bilibili"],
    per_platform_cap=8,
)
```

`explore_domains_block` 是可选项；未传时 prompt 与解析仍按普通多平台关键词生成运行。合法普通平台 key 包含 `weibo` 与 `github`：微博供给说明要求面向中文实时公开讨论，优先具体人物、事件、作品和话题实体，并可搭配“热议 / 回应 / 进展”等微博语境词；GitHub 供给说明要求面向公开代码仓库、开源工具、框架、SDK、模板和工程实践，优先可直接命中 repository name / description / README 的英文技术实体短查询。微博 producer 的 hot 分支仍独立把热搜词当 query seed，GitHub 的 ranked/latest 分支也不消费 planner 关键词。传入 explore block 时，模型可在平台 key 之外额外返回 `explore_domains`，每个 domain 包含 `domain / novelty_level / queries`。这些 queries 会被 runtime 写入 B 站 `discovery_keywords` query cache，因此 prompt 规则要求它们保持探索性、跨域和 B 站可直接搜索，而不是普通兴趣关键词的换皮。

### Inspiration axis-keyword prompt

`build_inspiration_axis_keyword_prompt()` 是 regular / shared inspiration stage 唯一的 LLM 调用（caller `discovery.keyword_inspiration`），一次返回 `{axes[], keywords[]}`。system prompt 是模块级静态常量 `_INSPIRATION_AXIS_KEYWORD_SYSTEM_PROMPT`，其平台枚举包含 `weibo` 与 `github`；所有 per-call 数据（platform guides、已选兴趣、既有轴、fresh evidence、allocation targets）都在 user message 里按稳定→易变排序、`ensure_ascii=False, indent=2, sort_keys=True` 序列化。微博 / GitHub 的 guide 与 evidence 只帮助生成各自 search 关键词，不把 inspiration preview 直接写入候选池；GitHub 的最终查询还必须经过服务端 public-only sanitizer。

Phase 2.1（多平台丰富度修复 F1）在该静态 Rules 里新增一条**产出具体性规则**：`core_concept` 必须锚定 `fresh_evidence` 里的具体实体 / 事件 / 作品 / 人物 / 机制（专名、作品名、具名争议、具体机制），**不得直接复述 interest 或 axis_label**；prompt 内置正反例（反：`新游推荐` 只是话题名 → 不合格；正：`士官长 登陆PS5` / `腾讯网易 新游发布`），并保留出口——某槽位 evidence 确实没有具体锚点时**允许**退回话题级、不硬造专名。该规则是纯静态文本（无 f-string、无 per-call 变量），因此仍满足 byte-identical prompt-cache 契约，`test_prompt_builder_system_messages_are_call_invariant` 覆盖 `build_inspiration_axis_keyword_prompt` 并逐字校验跨两次不同输入的 system message 相同。装配端还有确定性 `is_specific` 排序把"产出具体候选"真正落到"选中具体候选"（见 [discovery.md](./discovery.md) 的 `materialize_platform_keywords`）。

### Prompt layer render cache

```python
from openbiliclaw.llm.prompt_cache import PromptLayerRenderCache, profile_prompt_layers

cache = PromptLayerRenderCache()
blocks = cache.render_json_layers(profile_prompt_layers(profile_summary))
stats = cache.stats()
```

`profile_prompt_layers()` 只负责确定层次和顺序：core / life / interests / style / recent，未知扩展字段进入末尾 `profile_extra`。`PromptLayerRenderCache` 不缓存业务画像本身，只缓存当前层 digest 对应的 JSON prompt block。调用方仍每次从最新 profile 构造 layer payload；digest 不变时复用完全相同的字符串，digest 变化时只替换该层。

### Cognition prompt input views

```python
from openbiliclaw.llm.prompts import build_awareness_with_confusions_prompt

messages = build_awareness_with_confusions_prompt(
    events=events,
    preference_summary=preference_summary,
    soul_profile=soul_profile,
    input_view="compact-v1",
)
```

`build_preference_analysis_prompt()`、`build_awareness_prompt()`、
`build_awareness_with_confusions_prompt()` 与 `build_insight_prompt()` 均公开同一可选
`input_view` 参数，默认值都是 `legacy`，所以直接调用者不会因升级隐式切臂。`compact-v1`
只改变 user message 的确定性投影和区块顺序；模块级 system message 与结构化输出契约不变。
生产 `SoulEngine` 的三个 task-scoped 配置值决定 Preference、Awareness-with-confusions 与
Insight，plain Awareness 则固定传 `legacy`；replay 显式传入两臂，不读取生产默认值。

Phase 3 在 builder 之外收紧**请求集合**，不新增模型专用格式：Preference 自动预算回退从
每个剩余 offset 对实际独立 chunk prompt 做精确最大前缀搜索，避免把只存在于完整请求的旧
preference 体积按事件比例重复计算，也避免后段事件尺寸偏斜造成不必要的递归拆分；Insight prompt 只携带最新 20 条 + 最新 20 条 judged/validated 假设（去重后
最多 40），持久层仍合并完整 hypothesis ledger。两条路径保持 system message、JSON schema、
reasoning、输出上限和 provider 路由不变，因此不依赖具体 tokenizer 或 prompt-cache 实现。
`scripts/replay_token_diet_phase3.py` 支持只渲染与固定 SenseTime 单实例 A/A+B；真实模式强制
temperature=0、单并发、可配置请求间隔、禁用 fallback，并对 provider usage、route、结构质量、
完整历史 merge 和隐私泄漏 fail closed。可选 `--keyword-e2e` 另在 disposable SQLite 中验证
digest 宽限的规划、领取、真实 B 站搜索、模型评估、准入缓存和 yield 回填，绝不写生产库。
2026-08-06 的固定 `openai_compatible/deepseek-v4-flash` 重放中，Preference / Insight 的
provider prompt token 分别减少 `44.72% / 45.96%`，total token 分别减少 `41.46% / 41.35%`；
偏好兴趣重合、creator、格式修复与洞察结构/重复门均通过。关键词 planning 从
`1 call / 9098 tokens` 降为零调用，并继续通过真实 B 站搜索、评估、入池与 yield 回填。

生产 Insight 在 Phase 3 固定窗口之上继续使用 provider-independent 的加权选择器：最近 8 条与
最近 8 条 judged/validated 为锚，当前 awareness/profile 相关性最多取 16 条，置信度、证据、
裁决、重复支持和多样性再取 8 条并补满最多 40 条。同状态近重复只竞争 prompt 槽，不修改持久
对象；confirmed/rejected/unjudged 冲突分别保留，模型输出仍与完整 ledger merge。固定窗口 helper
继续作为异常回退与历史 replay control。

`scripts/replay_weighted_insight_context.py` 先做只读 render，再固定 SenseTime 单实例执行
fixed A1/A2/A、weighted B，并用同一 input digest 的完整历史 F 作为 provider usage 基线；F 可从
已经逐项通过 route/schema/usage 校验的隐私安全 artifact 复用。442 条快照的 weighted prompt
为 `27725` token，较完整历史 `48523` 少 `42.86%`，较固定窗口 `26724` 多 `3.75%`；最终 B 的
严格 schema、repair、重复、完整历史 merge 与 A/A 结构噪声门全部通过。passing artifact SHA-256
为 `932c5d955b7449b88065e8a5aec408966e40e0c02c2fd8ee506ff11b68e75932`。

#### Runtime 全局补货优先 admission

`LLMConcurrencyGate.update_inventory(available=..., target=...)` 只消费 canonical durable snapshot，产生 `healthy / refill / empty` 三态。后台先取得 cancellation-safe `RefillAdmissionSemaphore`，再取得 total priority permit；退出时逆序释放，因此后台 holder 不会在等待 total 时占住交互保留槽。

| 流量类 | total priority | 说明 |
|---|---|---|
| interactive | 0 | `soul.dialogue*`、`api.sentiment`、`api.config_probe`，仅经过 total gate |
| refill.expression | 1 | 推荐文案回填，补货最高优先 |
| refill.evaluation | 2 | 候选 batch / single 评估 |
| refill.supply | 3 | durable inventory 低于目标时动态升级的关键词 / 原料生成；包含 `discovery.explore.queries` 与 `sources.*.extract`，防止 supply 等库存、库存又等 supply 的循环 |
| maintenance | 4 | Soul、评测、purge 与健康库存下的 discovery；未知 caller 也落此类 |

当 refill waiter 存在时，新 maintenance 最多一个；没有 runnable refill 时 maintenance 可借用所有空闲后台槽，保持 work-conserving。`empty` 只 park 新 maintenance，绝不会取消或抢占已经进入 provider 的 maintenance。状态输出同时包含 refill/maintenance active、waiting、priority-active 与 inventory state。

用户对话的首个 `soul.dialogue*` 回复本身属于 interactive。回复成功后派发的 `dialogue_insight → preference → profile/pool_purge` 仍保留各自 caller、maintenance 分类和用量归属，但继承 task-local `_background_admission_bypass`：它们跳过 background admission，避免用户明确纠偏在 `inventory=empty` 或后台暂停时反过来等待库存；total gate 与 total priority 仍然生效，不会绕过 provider 总并发或挤掉新的交互请求。

#### 分模块路由

`LLMService` 的 `module_overrides` 来自 `module_overrides_from_config(config)`，每项是“继承全局链”或一条独立的实例 ID 链。
路由不使用 caller 第一段朴素判断，而是内置 bucket。匹配规则支持精确匹配、`.` 子调用和 `_` 后缀调用，因此 `discovery.keyword` 可以覆盖当前的 `discovery.keyword_planner`，也能覆盖后续 `discovery.keyword_*` 形态：

| caller 前缀 | module bucket | 说明 |
|---|---|---|
| `recommendation.evaluate_batch` | `evaluation` | 推荐侧复用 evaluator 做候选评分 / 分类的质量模型 |
| `discovery.evaluate` | `evaluation` | discovery 单条 / 批量内容评估家族 |
| `discovery.eval` | `evaluation` | discovery eval 简写家族 |
| `eval` | `evaluation` | 通用 eval 调用 |
| `discovery.search` | `discovery` | B 站 search query 生成等发现查询任务 |
| `discovery.keyword` | `discovery` | 统一关键词 planner：覆盖 `discovery.keyword_planner` 与 `discovery.keyword_*` |
| `discovery.explore` | `discovery` | B 站 explore domain / query 生成 |
| `discovery.trending` | `discovery` | trending 相关发现生成任务 |
| `discovery.related` | `discovery` | related-chain 相关发现生成任务 |
| `discovery.x` | `discovery` | X / Twitter discovery keyword generation |
| `discovery.douyin` | `discovery` | 抖音 discovery keyword generation |
| `runtime.bilibili_extension_search` | `discovery` | 浏览器插件 B 站扩展搜索 query 生成 |
| `yt_search` | `discovery` | YouTube search query 生成 |
| `sources.xhs` | `discovery` | 小红书关键词 / 抽取等来源发现任务 |
| `recommendation` | `recommendation` | 其他推荐表达、批量文案等调用 |
| `pool_purge` | `soul` | 候选池清理会删除内容，走画像 / 判断质量模型 |
| `api.sentiment` | `soul` | API 情绪 / 语义判断，用户可见且质量敏感 |
| `soul` | `soul` | 偏好、画像、觉察、洞察、聊天等 Soul 调用 |

路由规则：

- `inherit = true`：直接执行 `[llm].default_chain`，全局调整顺序后模块自动跟随。
- `inherit = false`：只执行该模块的 `chain`；第一个实例失败后按链内顺序降级，整条链耗尽就返回错误，绝不再 spill 到全局链。
- 保存时会拦截不存在、被禁用、重复或空的自定义链引用；运行时仍防御性过滤非 chat-capable 实例。
- 旧 `[llm.<module>] provider/model` 会投影成等价实例链；若模块模型不同于端点默认模型，迁移会创建派生实例以保留原意。

### 异常体系

```
LLMProviderError          # 基类
├── LLMRateLimitError     # 429 / rate limit
├── LLMAuthError          # 401 凭据被拒（终态，不重试）；带 provider_name / endpoint
├── LLMTimeoutError       # 请求超时
└── LLMResponseError      # 响应无效（空内容）

LLMFallbackError          # 所有 provider 都失败
RegistryBuildError        # 无法构建 registry（无可用 provider）

LLMServiceError           # Service 层基类
├── LLMResponseContentError  # Service 层空响应
└── LLMProviderExecutionError  # Provider 调用失败
```

`openbiliclaw.llm.base.describe_llm_failure(exc)` 返回面向用户的中文错因，未识别时返回 `None`。特异性顺序为 moderation → auth → quota/rate-limit → timeout / provider / empty response，避免 401 或配额耗尽被降级成泛化不可用。

- `describe_llm_failure(exc) -> str | None`：识别 moderation、鉴权、额度/限流、超时、provider 全部不可用、provider/service 空响应。链上存在 `LLMAuthError` 时，鉴权文案会指名被拒的 provider 与 endpoint 主机名（凭据经 `urlsplit().hostname` 剥离）。
- 401 判定不接受裸 `"401"` 子串：auth 桶优先级高于 rate-limit 桶，若 request id（`req-1401ab`）或 402 余额回包里的数字也算，配额问题会被误报成 API key 填错。
- `safe_llm_failure_message(exc) -> str`：公共边界使用；未知异常退化为固定安全提示，不回传上游异常文本。

### `json_utils.validated_text_field`

```python
validated_text_field(value: object, *, field: str, content_key: str) -> str | None
```

校验结构化响应里的单个文本字段：返回去空白后的字符串，遇到非字符串返回 `None` 并记 WARNING。

调用方**必须**把 `None` 当作"该字段不可用"处理(重试或丢弃),不能退回 `str()`。模型偶尔会把整批结果嵌进标量字段(`{"reason": [{...}, {...}]}`),`str()` 会把它转成 Python repr —— 非空、能过校验、最终作为推荐文案展示给用户。

## 配置项

```toml
[llm]
routing_version = 2
default_chain = ["deepseek-official", "deepseek-relay"]
concurrency = 3
timeout = 60

[llm.instances.deepseek-official]
name = "DeepSeek 官方"
provider_type = "deepseek"
enabled = true
api_key = "sk-..."
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com/v1"
reasoning_effort = "medium"

# 同一个 provider_type 的第二个渠道；实例 ID、凭据、地址和模型相互独立。
[llm.instances.deepseek-relay]
name = "DeepSeek 中转"
provider_type = "openai_compatible"
enabled = true
api_key = "relay-..."
model = "deepseek-v4-flash"
base_url = "https://relay.example.com/v1"

[llm.routes.soul]
inherit = true

[llm.routes.discovery]
inherit = false
chain = ["deepseek-relay", "deepseek-official"]
```

其余 `soul` / `recommendation` / `evaluation` 路由未写时默认继承全局链。Embedding 暂时继续使用独立的 `[llm.embedding]` 配置，其模型、维度和凭据不会被 chat 实例链切换；完整字段、校验与旧格式迁移规则见 [配置参考](config.md)。

## 托管 Ollama 生命周期（v0.3.162+）

`runtime/ollama_supervisor.py` 负责本进程"拥有"的 Ollama daemon 的完整生命周期。

**记录的 daemon 规格**：模块级 `_ManagedDaemon(proc, base_url, models_dir)` 取代了旧的裸
`_managed_proc` 句柄。`proc` 为我们 spawn 的 `Popen`（可发信号），或 `None` 表示"收养"——
仅限专用私有端口（`with-embedding` 的 `127.0.0.1:11435`）在启动时已有 daemon 应答的
force-quit 残留场景；收养只做记录、绝不发信号，但让 watchdog 能在它死后按记录的
`(host, models_dir)` 拉起新 daemon。任何 restart 都复用记录规格：私有 daemon 永远不会
回到 11434、也不会丢私有模型目录。`stop_managed_ollama` 清整条记录。

**端点判定**：`is_managed_endpoint(endpoint)` 做 host:port 归一化比较（`localhost` ≡
`127.0.0.1` ≡ `::1`，scheme / `/v1` path 不敏感）；`may_manage_ollama_endpoint(endpoint)` =
默认 loopback 11434 **或** 已记录的托管 daemon，是 `api/app.py` 两个修复 gate
（not_running / provider_error）唯一使用的谓词。`ensure_managed_ollama(endpoint)` 按记录
路由 not_running 修复的启动动作（私有 → `start_managed_ollama_at(记录目录, 记录端口)`，
否则默认路径）。

**Watchdog**：`start_ollama_watchdog(interval_seconds=30)` 幂等地启动单个 daemon 线程
（`obc-ollama-watchdog`），两条成功启动路径（默认 + 私有，含收养分支）都会自动布防。
每周期探测记录端点：健康即清零失败计数；探测失败且（自有进程已退出，或收养记录不再应答）
才经 spec-aware `restart_managed_ollama()` 重启——探测失败但自有进程仍存活时不动它
（绝不因单次探测失败杀活 daemon）。连续重启失败按 5s 起步、翻倍、300s 封顶退避，
连续 5 次失败后放弃（上报 phase `down` + ERROR 日志），直到 `reset_watchdog_backoff()`
（任何一次成功启动 / 手动修复成功都会调用）或进程重启。重启用 restart-in-progress 标志
与手动修复互斥。

**修复覆盖**（`POST /api/embedding/repair` 的 `may_manage` 判定，其余条件：
`manage_ollama=true` + `ollama_required` + loopback）：

| Endpoint | 记录状态 | not_running 动作 | provider_error 动作 |
| --- | --- | --- | --- |
| `localhost:11434`（默认） | 有/无记录 | 默认路径启动（同旧行为） | spec-aware restart |
| `127.0.0.1:11435`（with-embedding 私有） | 有记录（spawn 或收养） | `start_managed_ollama_at(记录目录, 记录端口)` | spec-aware restart（私有路径） |
| 自定义端口 / 远端 | 无记录 | 409（不越权，同旧行为） | 409 |
| 任意 | `manage_ollama=false` | 409 | 409 |

拒绝原因：`external_ollama`（记录外的 daemon 在应答）、`adopted_alive`（收养 daemon 仍
活着——不能停我们不拥有的进程）、`private_daemon`（`restart_managed_ollama_with_models_dir`
是默认 daemon 的路径迁移工具，对私有记录拒绝）、`restart_in_progress`。

**`OLLAMA_KEEP_ALIVE` 归属**：私有 daemon 完全由我们拥有，`OLLAMA_KEEP_ALIVE=24h` 与
`OLLAMA_HOST` / `OLLAMA_MODELS` 一律**硬设**（用户环境里的 `OLLAMA_KEEP_ALIVE=0` 不会
渗入导致 5 分钟卸载 + 冷启动 502 被误诊为 `model_broken`）；默认 daemon 路径保持
`setdefault`，尊重用户的全局设置。

## 设计决策

1. **retry 与 thinking 策略**：传输 / provider 临时错误走 3 次重试 + 线性退避（0.25s × attempt）；通用 OpenAI-compatible 的 `LLMResponseError` 默认不重试。支持 portable effort 的官方 adapter 默认 `medium`，按各家原生 schema 映射；渠道型 caller 由 service 层默认传 `None`，即跟随实例配置的 `reasoning_effort`，用户可在模型设置里选择 `low` / `medium` / `high` 或留空跟随模型。显式传参仍优先。旧格式即使曾被保存器物化出默认 `medium`，升级后也继续按空值处理以保持原有 wire behavior。Ollama 仍不发送 effort。DeepSeek 例外：显式空值仍发送 `thinking={"type":"disabled"}`；portable `low/medium` 按官方兼容规则归一为 native `high`，`xhigh/max` 归一为 `max`；HTTP 200 但 `content=""` 时额外关闭 thinking 重试一次。per-call effort 直接生成本次请求参数，不修改共享 provider 状态，因此并发探针、短任务和显式 reasoning 不会串档。每个 `provider_type="deepseek"` 实例的 `base_url` 原样传入 SDK，并按 endpoint 决定直连或代理；HTTP 400 记录 provider response body 摘要，避免只看到 `Error code: 400`
2. **fallback 是显式实例链**：chat 按 `[llm].default_chain` 从左到右尝试任意数量的实例；链里可以同时出现多个相同 `provider_type`，因为实例 ID 才是路由与 cooldown 的身份。Embedding 仍使用独立的 `[llm.embedding] provider → fallback_provider`，留空表示禁用，不跟随 chat 链。
   - **何时切到下一实例**：链上遇到 `LLMProviderError` / `LLMTimeoutError` / `LLMRateLimitError`（限流只冷却当前实例 60 秒）或 `LLMResponseError`（HTTP 200 但空 / 坏 content）时继续。链耗尽后统一抛 `LLMFallbackError`，原始错误保留在 `__cause__`。
   - **边界**：`complete_provider()` 是精确单实例调用，不跨实例；模块 `inherit=false` 时执行自己的完整链，但链耗尽也不会 spill 到全局链。不存在、禁用、重复或非 chat-capable 的引用由配置校验拦截，运行时仍做防御性过滤。
   - **兼容**：旧 `default_provider` / `fallback_provider` 和模块 `provider/model` 读取时无损投影为实例链，不自动改盘；新版 UI 保存后才写 `routing_version=2`，首次迁移先保留逐字节旧文件备份。旧二进制不认识 v2，需用 `config-export-legacy` 导出并接受旧 schema 的显式折叠告警。旧 `fallback_enabled` 仍不参与 chat 路由。
   - **init 前置探测**：`InitPrereqs.chat_ready()` 按完整全局实例链依次真实探测；任一实例健康即 ready，因此主渠道故障但后续渠道可用时不会阻塞初始化。非 chat-capable 项永远不会收到 chat health check。
3. **Protocol DI**：`SupportsComplete` Protocol 解耦了调用方和具体实现，测试时可注入 Fake
4. **Prompt 集中管理**：所有 prompt 在 `prompts.py` 中定义，不散落在各模块
5. **统一上下文注入**：`complete_with_core_memory()` / `complete_structured_task()` 默认负责把核心记忆注入到 Soul 相关任务里；已在 `user_input` 自带完整结构化上下文的高频任务可传 `inject_core_memory=False`，或通过 `llm.task_options.without_core_memory_kwargs()` 在兼容旧 stub 的前提下关闭注入，避免动态 core memory 破坏 provider prompt-cache 前缀
6. **OpenAI-compatible 复用**：DeepSeek、OpenRouter、OrcaRouter 这类兼容 OpenAI 协议的 provider 复用同一套重试、超时和错误归一化逻辑，只在子类中注入默认地址或额外请求头
7. **Gemini 独立适配**：Gemini 走官方 `google-genai` SDK，不强行复用 OpenAI-compatible 抽象；provider 内部负责把统一 `messages` 渲染成 quickstart 风格的单文本 prompt
8. **Gemini 可选依赖降级**：环境里缺少 `google-genai` 时，`llm` 包和 registry 仍可正常导入；只有真正实例化 Gemini provider 时才会给出明确缺依赖错误。守卫捕获的是 `ImportError` 而非仅 `ModuleNotFoundError`（issue #80）——SDK 装上了但其原生传递依赖加载失败（如 Termux/Android 下 `cryptography` 的 manylinux 轮子 dlopen 失败）同样降级而不是让 CLI 启动即崩，实例化报错会附带底层 import 失败详情
9. **Prompt 风格集中收口**：推荐、画像和聊天的“老B友”语气由共享 `ToneProfile` 驱动，不允许各模块各自发散成不同人格；用户自定义语气（`[soul] reply_style`，issue #255）只允许经 `_render_tone_profile()` 这一个缝以追加行形式进入语气块，默认空值保持 prompt 逐字节不变；`[soul] dialogue_tone_prompt` 是唯一例外，可整体替换对话 prompt 的语气块（仅 `build_socratic_dialogue_prompt`，其余 builder 无此参数）
10. **Prompt-cache 约定**：高频结构化 builder 的 system prompt 必须保持静态；user prompt 按“tone / 画像 / 长期偏好 / 来源上下文 / 本批内容或历史”从稳定到易变排序，并使用确定性 JSON。使用完整 `profile_summary` 的高频链路优先经 `profile_prompt_layers()` 分层渲染，稳定层放前、recent 层放后；调用方不得再把同一份动态画像通过 core memory 追加进 system prompt，便于 DeepSeek / Claude / OpenAI / Gemini 的 provider-side prompt cache 复用前缀
11. **结构化输出只在 helper 处放宽**：业务模块不再各自手写 JSON 截取逻辑；容错集中在 `json_utils.py`，模块侧用 predicate 收紧语义，避免一个 provider 的异常 shape 修复污染其他任务。
12. **分模块链不隐式改意图**：默认 `inherit=true`；显式自定义链只在链内降级，引用失效或整链失败都不会偷跑到全局链。旧模块 model override 在迁移时成为独立派生实例，避免修改共享实例或污染其他模块。
13. **Codex OAuth 使用独立官方传输**：`auth_mode="codex_oauth"` 不再给 `OpenAIProvider` 注入 Platform API token，而是构造 `CodexChatGPTProvider`，请求发往 `https://chatgpt.com/backend-api/codex/responses`（官方 Codex CLI 同款通道），401 时安全刷新并重试一次。该模式只允许官方 Codex 域名，防止 ChatGPT OAuth token 泄露给 OpenAI-compatible 代理或 `api.openai.com`。
14. **失败分类先于批响应解析**：共享 classifier 保持 rate-limit / no-provider / auth / invalid-response 的特定语义优先级，并额外识别连接失败与 HTTP 500/502/503/504；调用方只把 provider transient 交给协调器退避，不把 JSON shape 错误误判成网络失败。
