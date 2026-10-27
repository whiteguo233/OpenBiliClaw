# Profile-usage registry

> **Purpose.** One row per surface that consumes the user profile — so that
> per-stage tailoring is deliberate, the portrait boundary is auditable, and no
> new serializer fork appears by accident. Seeded from the profile-views spec
> (`docs/plans/2026-07-18-profile-views-spec.md` §D1–D8) and the 2026-07-16
> token-diet audit. Every `file:line` below was cross-checked against the working
> tree. Task 5 (Wave B) relocated the three content-pipeline serializers into
> `soul/profile_views.py`; `discovery/strategies/_utils.py` now re-exports them.

## The serialization paths

The profile reaches an LLM prompt through a small set of serializers. The three
content-pipeline serializers now live in `soul/profile_views.py` (Task 5 moved
them verbatim from `discovery/strategies/_utils.py`, which keeps re-export stubs
so every legacy import path stays valid):

| Serializer | Defined at | Shape | Portrait? | Notes |
| --- | --- | --- | --- | --- |
| `build_profile_catalog` / `select_profile_context` | `soul/profile_views.py` | local catalog → immutable budgeted projection | **No** | Local catalog has no top-48/256 positive-interest cutoff. Preserve scoped negatives, style/context; select whole relevant facts + global taste anchors using deterministic lexical relevance. No model call, source mutation, or creator-name inference. Projection exposes digest, bytes, fits and selected interest names; mandatory overflow is not success. |
| `build_profile_summary` | `soul/profile_views.py:360` (re-export `discovery/strategies/_utils.py`) | dict | **No** | Canonical structured profile; portrait deliberately excluded (`profile_views.py:371-375`). `favorite_up_users` also excluded (`profile_views.py:392`). |
| `compact_content_prompt_profile_summary` | `soul/profile_views.py:512` (re-export `discovery/strategies/_utils.py`) | dict | **No** | Caps a `build_profile_summary` dict for high-volume content prompts. Aliased as `compact_evaluation_profile_summary` (`discovery/engine.py:102`). Dislike floor preserved (`profile_views.py:46-50`). |
| `build_query_generation_profile_summary` | `soul/profile_views.py:914` (re-export `discovery/strategies/_utils.py`) | dict | **No** | Query-trimmed taste shape; drops awareness/insights/timestamps. Interests cap 64, domains ≤16. |
| `build_cognition_profile_view_v1` / `CognitionProfileViewV1` | `soul/profile_views.py` | stable soul + stable preference + volatile cognition | **Yes, when soul is supplied** | Named, uncapped cognition-only projection. Removes storage/init bookkeeping and the duplicate `soul.interest` subtree, filters archived positive interests, preserves negative evidence and unknown semantic fields, and splits recent awareness/active insights from the stable prefix. Awareness/Insight historically received the full soul snapshot, including `personality_portrait`, so compact-v1 deliberately preserves it. Preference does not supply a soul snapshot. |
| `speculation` (→ `to_llm_context(include_portrait=False)`) | `soul/profile_views.py` (`speculation`); renderer `soul/profile.py:720` (onion) / `:115` (flat) | str | **No** (opted out) | String view for the two speculator prompts. Task 7 collected the former in-line `to_llm_context(include_portrait=False)` fork into a façade view that delegates to the profile's own renderer (zero behaviour change). `include_portrait=True` default still keeps the portrait for eval/persona rendering (not this path). |
| `chat_core_memory` / `render_core_memory_blocks` | `soul/profile_views.py` (`chat_core_memory`), `memory/manager.py` (`render_core_memory_blocks` / `render_core_memory_prompt`) | `(stable, volatile)` str pair | **Yes** (stable block) | Chat core-memory view. Reads the **effective** profile (AI ⊕ overrides via `_effective_soul_data`, `manager.py`), so manual edits show. `complete_with_core_memory` injects `stable_block` (portrait/identity/preference) into system and `volatile_block` (awareness/insights) ahead of the user turn — awareness churn no longer breaks the cached system prefix (Task 6). `render_core_memory_prompt` kept as the concatenated compat wrapper for non-chat readers. |
| `ProfileResponse` (Agent Bridge) | `integrations/openclaw/schemas.py` + `operations.py:get_profile` | dataclass | **Yes** (intentional) | External Agent Bridge surface; portrait is deliberately re-exposed and each list is capped at five items. |

`CognitionEventViewV1` lives in `soul/event_prompt_views.py`. It is the matching
event projection, not a profile serializer: it preserves event order/count and
semantic evidence, parses metadata deterministically, and removes only an
explicit narrow set of transport/projection bookkeeping fields.

`resize_profile_context` can budget an existing on-demand projection again for a larger
audit/repair envelope. It only selects whole already-loaded facts, preserves mandatory
and unknown fields, rejects unknown schemas, and never fetches or invents profile data.

## Consumer surfaces

Jev 完整评估先按候选选择不依赖同行的视图，再由具名
`build_shared_evaluation_profiles()` 将相同负偏好/风格/场景因子化到共享 `profile`。
各 `candidate_profiles.cN` 必须与共享块一起解释；不借用其它候选的正偏好。

| Surface | Trigger cadence | View / serializer | Fields (caps) | Portrait? | LLM? |
| --- | --- | --- | --- | --- | --- |
| Recommendation evaluation / expression | Per candidate (discovery + serve) | `compact_content_prompt_profile_summary(build_profile_summary(...))` — `recommendation/engine.py:126-127` | compact (20 core / 48 interests / 32 domains × 16 specifics / 12 recent; dislikes uncapped) | No | Yes |
| Director planning / pair decisions | Opt-in generation or rolling preplan | `build_profile_catalog` + `select_profile_context` by intent and shortlist; no core-memory injection | Budgeted slow/fast projections, explicit negatives uncut. Full source digest controls plan freshness; actual projections bind request identity. Latest 3 experienced screens + semantic actions/ACKs remain separate, not inferred interests | No | Yes (Agent and/or typed decision) |
| Discovery evaluation | Per candidate batch | `_evaluation_profile_summary` = `compact_evaluation_profile_summary(build_profile_summary(...))` — `discovery/engine.py:1874`; applied `discovery/engine.py:823` | compact | No | Yes |
| Jev discovery semantic filter | Opt-in candidate wave, independently of director strategy | `compact_content_prompt_profile_summary(build_profile_summary(...))` — `discovery/semantic_filter.py` | Same compact view; complete dislikes retained; oversize request fails open at the 24,000-byte engineering cap rather than truncating negative evidence | No | Yes (typed decision, not chat) |
| Jev full candidate evaluator | Selected instead of full LLM evaluation; single, batch and legacy recovery | Local `build_profile_catalog` → per-candidate `select_profile_context`; `candidate_profiles.cN` | Selected-model dual byte guards reserve candidate/questions/negatives first; independent of batch peers; keep mandatory constraints, split candidates or return missing. Full source/policy/budget identities prevent stale cache | No | Yes (typed decision; no generative fallback) |
| Discovery evaluation digest (cache key) | Per candidate batch | `_evaluation_profile_digest` — `discovery/engine.py:1862`; used `discovery/engine.py:1542`, `:2119` | digest over the compact prompt-visible slice | No | No (cache key) |
| Search keyword generation | Per discovery cycle | `build_query_generation_profile_summary` — `discovery/strategies/search.py:547`, `:550` | query-trimmed | No | Yes |
| Explore domain generation | Per discovery cycle | `build_query_generation_profile_summary` — `discovery/strategies/explore.py:428` | query-trimmed | No | Yes |
| Keyword planner | Per planning batch | `build_query_generation_profile_summary` — `runtime/keyword_planner.py:1221` | query-trimmed | No | Yes |
| Bilibili extension search fallback | Per producer run | `build_query_generation_profile_summary` — `runtime/bilibili_producer.py:41` | query-trimmed | No | Yes |
| Douyin direct keyword gen | Per discovery cycle | `build_query_generation_profile_summary` — `discovery/strategies/douyin_direct.py:71` | query-trimmed | No | Yes |
| YouTube keyword gen | Per discovery cycle | `build_query_generation_profile_summary` — `discovery/strategies/youtube.py:155` | query-trimmed | No | Yes |
| X keyword gen | Per discovery cycle | `build_query_generation_profile_summary` — `discovery/strategies/x.py:247` | query-trimmed | No | Yes |
| Xiaohongshu keyword gen | Per discovery cycle | `build_query_generation_profile_summary` — `sources/xhs_keyword_gen.py:49` | query-trimmed | No | Yes |
| Pool snapshot / diagnostics | On snapshot | `build_profile_summary` — `discovery/pool_snapshot.py:114` | full dict | No | No |
| Speculative interest generation | 12h speculation | `profile_views.speculation(profile)` — `soul/speculator.py:1386` | string, portrait excluded | No | Yes |
| Avoidance speculation | 12h speculation | `profile_views.speculation(profile)` — `soul/avoidance_speculator.py:1271` (getattr guard keeps `{}` fallback for non-object profiles) | string, portrait excluded | No | Yes |
| Page extractor | Per fetched page | none — profile not read; `inject_core_memory=False` — `sources/llm_extractor.py:85` | (none) | No | Yes |
| Chat (Socratic dialogue) | Per chat turn | `chat_core_memory` via `render_core_memory_blocks` → `complete_with_core_memory` — `llm/service.py` | effective core memory, split: system = portrait + 核心特质/价值观/深层需求/MBTI + 偏好摘要 (stable); user = 近期观察 + 当前洞察 (volatile) | Yes | Yes |
| Consolidator judge | 12h consolidation | `inject_core_memory=False` (opt-out) — `soul/consolidator.py:814` | none (judges cluster payload only) | No | Yes |
| Layer updaters (×3) | On profile update | `inject_core_memory=True` (intentional) — `soul/layer_updaters.py:324`, `:423`, `:538` | core memory (kept: connects evidence to user context) | Yes | Yes |
| Category migration | On migration | `inject_core_memory=False` (opt-out) — `soul/category_migration.py:145` | none (pure taxonomy mapping) | No | Yes |
| Pool purge (dislike match) | On new dislike | `inject_core_memory=False` (opt-out) — `soul/pool_purge.py:201` | none (judges dislike-vs-candidate payload only) | No | Yes |
| Dialogue-insight analyzer | Post-chat | `inject_core_memory=False` (opt-out) — `soul/dialogue_insight_analyzer.py:70` | core memory already in user prompt (injection was a duplicate) | Yes (user prompt) | Yes |
| Preference analysis (`soul.preference*`) | Init, event chunk, or feedback batch | `build_preference_analysis_prompt(..., input_view=...)`; `compact-v1` uses `CognitionEventViewV1` + `CognitionProfileViewV1.stable_preference` | Event count/order and semantic evidence preserved; profile/event bookkeeping removed; awareness/insight context remains uncapped when supplied | No (no soul snapshot supplied) | Yes |
| Plain awareness (`soul.awareness`) | Legacy direct awareness analysis | `build_awareness_prompt(..., input_view="legacy")` in production; compact seam exists only for controlled replay | Full legacy soul/preference/events | Yes (historical full soul) | Yes |
| Awareness with confusions (`soul.awareness_confusions`) | Production cognition cycle | `build_awareness_with_confusions_prompt(..., input_view=awareness_prompt_view)`; default `compact-v1` after the 2026-08-06 SenseTime task gate | Stable soul → stable preference → prior volatile cognition → current projected event batch; no semantic caps | Yes (historical full soul) | Yes |
| Insight (`soul.insight`) | Cognition cycle | `build_insight_prompt(..., input_view=insight_prompt_view)`; default `legacy` because the compact arm failed its task gate | Compact replay seam uses stable soul/preference then hypotheses/awareness; production remains full legacy input | Yes (historical full soul) | Yes |
| Probe sentiment judge | Per probe reply | `inject_core_memory=True` (intentional) — `api/app.py:6244` | core memory (kept: chat-adjacent tone reading) | Yes | Yes |
| Related-chain seed | Per discovery cycle | direct read `favorite_up_users[:1]` — `discovery/strategies/related_chain.py:392` | favorite UPs only | No | No |
| `/api/profile-summary` (UI) | On request | direct read — `api/app.py:3990` | full profile incl. portrait | Yes | No |
| Agent Bridge `get_profile` | On request | `ProfileResponse` — `integrations/openclaw/operations.py:get_profile` | portrait + 5 traits / 5 needs / 5 interests | Yes (external) | No |
| Delight scoring | Per candidate | embeddings only — `recommendation/delight.py` (no LLM profile prompt; spec D8) | (embedding vectors) | No | No |

## Portrait boundary (invariant)

`personality_portrait` is prohibited from content-pipeline prompt serializers.
It remains intentional on these established surfaces:

- **Chat core memory** (`render_core_memory_prompt` → `complete_with_core_memory`).
- **Cognition awareness/insight** — their legacy prompts already consumed the
  complete soul snapshot; `CognitionProfileViewV1.stable_soul` preserves the
  portrait so compact-v1 does not silently change interpretation. Preference
  compact does not receive a soul snapshot.
- **Agent Bridge external `ProfileResponse`** (`operations.py:get_profile`) — plus UI
  (`/api/profile-summary`) and eval personas, which are out of the profile-views
  scope but keep the portrait by design.

Every content-pipeline serializer (`build_profile_summary`,
`compact_content_prompt_profile_summary`,
`build_query_generation_profile_summary`, the `speculation` view →
`to_llm_context(include_portrait=False)`) MUST exclude it. Enforced by
`tests/test_profile_views_guards.py`.

## Maintenance-caller injection audit (Task 8)

Per-caller decision for the eight `complete_structured_task` /
`complete_with_core_memory` sites that inherited the default core-memory
injection. **Opt-out** = added `inject_core_memory=False`; **keep** = documented
in-code as intentionally core-memory-bearing.

| Call site | Decision | Reason |
| --- | --- | --- |
| `soul/consolidator.py:814` | opt-out | Merge/keep judged purely from the interest-label cluster payload; portrait irrelevant to whether two labels denote the same interest. |
| `soul/layer_updaters.py:324` (role) | keep | Role-layer self-update; injected "who the user is" context helps connect new evidence to the user's life stage. |
| `soul/layer_updaters.py:423` (values) | keep | Values-layer delta; core context legitimately informs whether a value should be added/removed (author-curated 用户背景 confirms the intent). |
| `soul/layer_updaters.py:538` (core) | keep | Deepest core layer under strongest diff protection; core context helps weigh whether evidence justifies a change. |
| `soul/category_migration.py:145` | opt-out | Pure taxonomy canonicalization of category labels; no user-specific judgment. |
| `soul/pool_purge.py:201` | opt-out | Dislike-match judgment fully specified by the user prompt (new dislikes + all dislikes + candidates). |
| `soul/dialogue_insight_analyzer.py:70` | opt-out | The prompt already serializes the full `core_memory` dict into the user message; injection was an exact duplicate. Model still sees core memory via the explicit param. |
| `api/app.py:6244` (probe sentiment) | keep | Chat-adjacent sentiment classification; reading tone in the user's own context is desirable. |

Guarded by `tests/test_maintenance_injection_audit.py` (pool purge + dialogue
insight), `tests/test_profile_consolidator.py::test_consolidation_judge_opts_out_of_core_memory_injection`,
and `tests/test_category_migration.py::test_category_mapping_opts_out_of_core_memory_injection`.
