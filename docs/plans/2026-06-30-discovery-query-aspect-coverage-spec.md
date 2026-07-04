# Discovery Query Aspect Coverage Spec

**Created:** 2026-06-30
**Scope:** unified keyword planner, discovery keyword store, query prompt contract, query/yield observability

## Goal

Make discovery search queries cover the user's profile interests deliberately instead of relying on
LLM free-form brainstorming. The planner should keep using pool distribution and recent keyword
history, but they must become supporting signals rather than the only anti-repeat mechanism.

The target behavior is:

- profile interests are represented as explicit search aspects;
- each generated query can be traced back to an aspect and query kind;
- under-covered profile interests receive guaranteed probe budget;
- high-frequency failed, expired, or zero-yield queries enter cooldown and stop recycling;
- broad high-weight domains no longer crowd out specific lower-weight interests forever;
- exploratory queries use their own quota and lifecycle instead of being mixed into `regular`.

## Current Diagnosis

The current unified keyword planner already sends useful context to the LLM:

- compact profile summary;
- per-platform `recent_keywords`;
- per-platform pool distribution hints (`avoid_topics`, `prefer_axes`, `cold_start`);
- platform supply hints from admitted content;
- platform need counts.

Those signals are insufficient for query diversity because they do not answer the central question:
**which profile interest has not received effective search coverage yet?**

Observed local data showed this failure mode:

- `discovery_keywords` had `14,357` rows but only `regular` `keyword_kind`.
- `109` flat profile interests existed; `87` had exact query-name coverage, but only `25` had a
  query reach `used`, and only `41` had positive yield.
- high-frequency terms were mostly terminal failures or expired rows, for example:
  - `篮球`: 278 rows, 236 failed, 42 expired, 0 used;
  - `王者荣耀`: 273 rows, 245 failed, 28 expired, 0 used;
  - `猎奇吃播`: 264 rows, 237 failed, 27 expired, despite being a disliked topic.

## Why Existing Inputs Do Not Stop Repeated High-Frequency Queries

### Pool Distribution Is Content-Side Saturation

Pool distribution describes content that has already entered the cache. It can tell the planner
"the current pool has too much topic X" or "platform Y historically admitted topic Z". It cannot see
queries that failed before producing content. A query like `篮球` can fail hundreds of times and still
not appear as an overrepresented pool topic because no content was admitted from it.

Pool distribution also works at `topic_group` granularity after evaluation, not at raw query
granularity. That makes it useful for content balance, but too late and too coarse for preventing
query-level loops.

### Recent Keywords Are A Short Window, Not Negative Memory

`history_keywords()` currently considers recent `claimed`, `executing`, and `used` rows. It does not
include large volumes of `failed` or `expired` history. The uniqueness constraint only blocks
in-flight duplicates (`pending`, `claimed`, `executing`), so a failed or expired query can be generated
again in later cycles.

This means recent keywords prevent immediate local duplication, but not long-term repetition of
barren query families.

### Neither Signal Tracks Profile-Interest Coverage

A profile interest can be:

- present in the profile but cut from compact prompt context;
- present in prompt context but skipped by the LLM;
- generated as a pending query but never claimed;
- claimed but failed;
- used but zero-yield;
- covered only by a broad category query, not by its specific interest.

Pool distribution and recent keywords do not distinguish these states. The planner needs a
first-class aspect coverage ledger.

## Design

### 1. Aspect Inventory

Build an explicit aspect inventory from the effective profile before prompt compaction. Each aspect
represents one searchable taste unit.

Aspect fields:

- `aspect_id`: stable normalized key, e.g. `科技/AI Agent`;
- `domain`: broad interest domain;
- `interest_name`: specific interest or domain name;
- `profile_weight`: profile weight;
- `source`: profile source/provenance when available;
- `is_domain_level`: true for broad domain aspects;
- `is_specific_level`: true for specific interests;
- `dislike_similarity`: lexical/embedding proximity to disliked topics;
- `created_from`: `profile_domain`, `profile_specific`, `speculative_interest`, or `feedback_repair`.

The aspect inventory must not be limited to the current compact prompt's 64 `interests`. Compact
prompting is an LLM cost strategy; aspect scheduling needs the full active profile inventory.

### 2. Profile Context Expansion

Keep query generation bounded, but make the bounded profile view richer than the current compact
summary. The previous compact profile was optimized for cost and cache stability; it can hide
long-tail interests before the planner ever has a chance to schedule them.

Use three separate profile views:

- `inventory_view`: full effective profile used by the scheduler to build aspect inventory. This
  view is not sent directly to the LLM.
- `generation_context_view`: richer bounded context sent to the LLM alongside selected aspect
  slots.
- `slot_view`: the actual `must_cover_aspects` selected for the current planner pass.

Recommended first-step caps:

| Field | Current query cap | Recommended cap |
| --- | ---: | ---: |
| stable list fields (`core_traits`, `values`, `deep_needs`, etc.) | 8 | 12 |
| `interest_domains` | 16 | 24 |
| specifics per interest domain | 8 | 12 |
| flat interest candidate pool | 128 | 256 |
| selected flat interests | 64 | 96 |
| optional max selected flat interests | 64 | 128 |
| speculative interests | 8 | 12 |
| recent awareness | excluded | 8 |
| active insights | excluded | 8 |
| disliked topic candidate pool | 128 | 128 |
| selected disliked topics | 64 | 64 |

The increase should happen in two places:

1. Enlarge the candidate pool so long-tail profile interests can enter aspect scheduling.
2. Moderately enlarge the LLM-visible context so generated phrases have enough vocabulary and
   adjacency signals.

Do not treat the larger profile context as a replacement for aspect coverage. A bigger prompt still
allows the model to ignore lower-rank interests; the selected slots are the binding contract.

### 3. Freshness Lane

Add a small, short-lived freshness lane to query generation:

- `recent_awareness[:8]`
- `active_insights[:8]`
- `speculative_interests[:12]`

These fields should be summarized separately from the stable taste context. Stable profile fields can
keep a longer cache TTL; freshness fields should use a shorter TTL or a separate digest component so
new interests can influence discovery without making every stable query plan uncacheable.

Freshness signals should not automatically become `core`. They should enter as candidates for
`world_scan`, `bridge`, or `undercovered_specific` slots after passing dislike and negative-memory
filters.

### 4. Inspiration Search Probe

Add a lightweight search-preview stage before brainstorming. This stage uses selected aspects to run
small, bounded Exa web searches through Agent-Reach / `mcporter`, then extracts inspiration seeds
from real web and platform language. It should improve query richness without letting noisy search
results directly enter the keyword pool.

Flow:

```text
aspect slot -> seed query -> Exa search preview -> inspiration seeds
  -> lateral expansions -> profile curation -> detail expansion -> brainstormed angles
```

Default probe backend:

```bash
mcporter call 'exa.web_search_exa(query: "query", numResults: 5)'
```

For platform-specific inspiration, scope Exa when possible:

- Bilibili slots: add domain preference/filter for `bilibili.com`;
- Reddit slots: add domain preference/filter for `reddit.com`;
- broad `world_scan`: leave unscoped or use a small curated domain set;
- source platforms without useful web indexing: fallback to the platform-native adapter or the
  deterministic seed fallback.

Use inspiration search only where outside language helps:

- `undercovered_specific`: use when an aspect has low or stale coverage;
- `bridge`: use when connecting two concepts needs platform-native vocabulary;
- `world_scan`: use to discover current adjacent scenes, entities, and phrases;
- `feedback_repair`: optional, usually only when yielded content is too narrow;
- `core`: skip by default unless the aspect has gone stale or repeated direct queries underperform.

Probe limits for the first implementation:

- `1-3` seed queries per aspect slot;
- `5-10` preview items per seed query;
- Exa result title, URL, source domain, short snippet, and lightweight metadata only;
- no full content fetch;
- no recommendation admission from preview results;
- short TTL cache keyed by
  `(platform, profile_kw_digest, aspect_id, query_kind, probe_backend, seed_query, freshness_digest)`.

Inspiration seed fields:

- `inspiration_id`: stable id within the probe result;
- `slot_id`: source slot for this planner pass;
- `aspect_id`: source aspect;
- `seed_query`: preview query used to fetch the snippets;
- `probe_backend`: `exa` for the first implementation;
- `source_platform`: target platform context, e.g. `bilibili`, `reddit`, or `web`;
- `source_domains`: domains that supplied evidence, e.g. `bilibili.com`;
- `source_terms`: extracted entities, scenes, phrases, or adjacent topics;
- `evidence_titles`: small capped list of titles or snippets that justify the seed;
- `evidence_urls`: small capped list of URLs that justify the seed;
- `reason`: why this seed may enrich query generation;
- `risk_flags`: `generic`, `saturated`, `disliked_adjacent`, `failed_family`, or `low_confidence`;
- `expires_at`: cache expiration for volatile inspiration.

The preview results and inspiration seeds must not be inserted into `discovery_keywords`. They only
serve as inputs to brainstormed angles. This keeps the search probe from becoming an uncontrolled
feedback loop where the system searches whatever happened to appear in the last result page.

### 5. Brainstormed Query Angles

Add a bounded brainstorming stage between aspect selection and final keyword generation. This stage
should generate search angles, not final keywords. The goal is to make the model explore possible
extensions of an interest while keeping insertion controlled by deterministic filters and selected
slots.

This mirrors the useful part of the Superpowers brainstorming flow:

```text
context -> possible directions -> critique/selection -> concrete plan
```

For discovery query generation, map that to:

```text
aspect slot -> inspiration seeds -> lateral expansions -> curated details
  -> brainstormed angles -> angle curation -> query realization -> insertion
```

Use brainstorming only where it adds diversity:

- `undercovered_specific`: always use brainstorm angles;
- `bridge`: always use brainstorm angles;
- `world_scan`: always use brainstorm angles;
- `feedback_repair`: optional, depending on whether yielded content already gives concrete terms;
- `core`: may skip brainstorming and directly realize queries to control cost.

Brainstormed angle fields:

- `angle_id`: stable id within the planner pass or cache entry;
- `aspect_id`: source aspect;
- `query_kind`: intended generation policy;
- `label`: short human-readable search direction, e.g. `真实工作流`;
- `rationale`: why this direction is relevant to the user's profile;
- `inspiration_ids`: inspiration seeds that informed this angle, when any;
- `expansion_ids`: curated lateral expansions that informed this angle, when any;
- `platform_terms`: platform-native terms and phrases to use;
- `avoid_terms`: terms to avoid because they are generic, saturated, disliked, or failed before;
- `novelty_tags`: short labels used for diversity selection.

The raw brainstorm output must not be inserted into `discovery_keywords`. Only realized and filtered
queries can be stored. This is the hard gate that keeps brainstorming from becoming another source
of noisy high-frequency keywords.

Cache brainstorm angles by
`(profile_kw_digest, platform, aspect_id, query_kind, inspiration_digest, expansion_digest,
freshness_digest)`. Stable aspects can reuse angles across planner cycles, while freshness-sensitive,
inspiration-sensitive, and expansion-sensitive angles should expire quickly.

### 6. Lateral Expansion And Profile Curator

Add a controlled lateral expansion stage after inspiration seeds are extracted. The goal is to keep
useful Exa-derived ideas alive across planner cycles and expand them sideways into adjacent scenes,
entities, comparisons, and sub-problems.

This stage is a bounded exploration graph, not an infinite recursive search loop:

```text
inspiration seed -> expansion candidates -> profile-aware curator -> detail expander -> angle inputs
```

Expansion candidate fields:

- `expansion_id`: stable id for the candidate;
- `parent_inspiration_id`: source inspiration seed;
- `parent_expansion_id`: optional previous-hop expansion;
- `hop`: expansion depth, initially `1`;
- `relation`: `sub_scene`, `adjacent_cost`, `comparison`, `decision_context`,
  `social_trend`, `tooling_context`, or `entity_variant`;
- `text`: candidate phrase or scene;
- `detail_axes`: specific axes that can later become query vocabulary;
- `source_terms`: evidence or terms inherited from the inspiration seed;
- `status`: `new`, `expanded`, `curated`, `selected`, `realized`, `yielded`, `failed`,
  or `cooled_down`.

Profile-aware curator input:

- compact but richer profile context;
- selected aspect and query kind;
- disliked topics;
- negative memory;
- pool distribution and saturation hints;
- coverage debt;
- inspiration seed and expansion candidate;
- prior yield/failure stats for the same inspiration and expansion family.

Profile-aware curator output:

```json
{
  "expansion_id": "e1",
  "decision": "keep",
  "score": 0.82,
  "reason": "Specific, searchable, profile-aligned, and not in a failed query family.",
  "feedback": "",
  "risk_flags": []
}
```

Possible decisions:

- `keep`: candidate can feed detail expansion;
- `revise`: candidate is promising but too broad; return feedback for a more specific rewrite;
- `reject`: candidate is generic, disliked, saturated, or historically barren;
- `cooldown`: candidate family should not be expanded until cooldown expires.

Detail expander turns kept or revised candidates into concrete angle vocabulary:

```json
{
  "expansion_id": "e1",
  "detail_axes": ["一线城市", "打工人", "月度预算", "外卖 vs 做饭", "真实账单"],
  "angle_label": "年轻人饮食成本对比",
  "query_terms": ["外卖和做饭成本对比", "打工人吃饭开销", "一线城市做饭成本"]
}
```

Exploration limits:

- expand at most `2` inspiration seeds per aspect per planner cycle;
- generate at most `5-8` expansion candidates per inspiration seed;
- keep at most `2-3` curated expansions per aspect per planner cycle;
- default max hop is `1`; allow hop `2` only after positive yield;
- do not expand candidates with active cooldown, disliked adjacency, or repeated zero-yield history.

### 7. Coverage Ledger

Add a persistent coverage ledger keyed by `(profile_kw_digest, platform, aspect_id)`.

Suggested fields:

- `generated_count`
- `pending_count`
- `claimed_count`
- `used_count`
- `failed_count`
- `expired_count`
- `yield_count`
- `zero_yield_used_count`
- `last_generated_at`
- `last_used_at`
- `last_yield_at`
- `cooldown_until`
- `last_failure_reason`

This ledger should update from the keyword lifecycle and yield backfill path. It answers:

- which interests were never tried;
- which interests were tried but did not execute;
- which interests execute but produce no admitted content;
- which interests are productive enough to exploit.

### 8. Query Kinds

Use `keyword_kind` as a real policy dimension instead of leaving everything as `regular`.

Initial query kinds:

- `core`: reliable high-yield profile interests;
- `undercovered_specific`: specific profile interests with high coverage debt;
- `bridge`: profile interest plus adjacent domain or cognitive style;
- `world_scan`: profile-adjacent world knowledge exploration;
- `feedback_repair`: query variants derived from yielded content, tags, topic groups, and titles.

`explore` can remain the Bilibili explore-strategy-specific kind, but planner-generated exploratory
searches should be represented explicitly as `world_scan` or bridge-style aspects unless they are
intended for `ExploreStrategy` consumption.

### 9. Aspect Selection

Each planner pass should select aspect slots first, then ask the LLM to generate query candidates for
those slots.

Recommended slot mix for the first implementation:

- `40% core`
- `30% undercovered_specific`
- `15% bridge`
- `10% world_scan`
- `5% feedback_repair`

The exact percentages should be config-backed and clamped so small batches still contain at least one
under-covered slot when any exists.

Aspect score:

```text
score =
  profile_weight
  + coverage_debt
  + uncertainty_bonus
  + query_kind_quota_bonus
  + platform_fit
  - pool_saturation_penalty
  - repeated_failure_penalty
  - zero_yield_penalty
  - dislike_similarity_penalty
```

Where:

- `coverage_debt` grows when an aspect has no generated/used/yield history or has not been tried
  recently;
- `uncertainty_bonus` favors aspects with too little sample data;
- `platform_fit` uses platform supply priors, but cannot fully suppress scheduled
  `undercovered_specific` probes;
- `pool_saturation_penalty` still uses current pool distribution;
- `repeated_failure_penalty` uses keyword/aspect negative memory, not only recent keywords.

### 10. LLM Prompt Contract

The LLM should not receive a flat request like "generate 30 keywords". It should receive selected
slots and, for slots that need diversity, first use inspiration seeds from search preview, then
expand and curate lateral candidates, then return brainstormed search angles. A final realization
step turns selected angles into concrete query candidates.

Inspiration extraction input shape:

```json
{
  "platform": "bilibili",
  "slot": {
    "slot_id": "s1",
    "aspect_id": "生活/生活成本",
    "query_kind": "undercovered_specific",
    "domain": "生活",
    "interest_name": "生活成本"
  },
  "seed_query": "一线城市生活成本",
  "probe_backend": "exa",
  "domain_filters": ["bilibili.com"],
  "preview_items": [
    {
      "title": "上海租房通勤吃饭一个月到底花多少",
      "url": "https://www.bilibili.com/video/BV...",
      "source_domain": "bilibili.com",
      "snippet": "普通打工人在上海的月度账单复盘，包含租房、通勤和吃饭开销"
    },
    {
      "title": "5000 元月薪如何做月度预算",
      "url": "https://www.bilibili.com/video/BV...",
      "source_domain": "bilibili.com",
      "snippet": "收入有限时的真实消费结构"
    }
  ],
  "avoid_terms": ["生活", "猎奇吃播"]
}
```

Inspiration extraction output shape:

```json
{
  "inspiration_seeds": [
    {
      "inspiration_id": "i1",
      "slot_id": "s1",
      "aspect_id": "生活/生活成本",
      "seed_query": "一线城市生活成本",
      "probe_backend": "exa",
      "source_platform": "bilibili",
      "source_domains": ["bilibili.com"],
      "source_terms": ["上海租房通勤", "月度账单", "吃饭开销"],
      "evidence_titles": ["上海租房通勤吃饭一个月到底花多少"],
      "evidence_urls": ["https://www.bilibili.com/video/BV..."],
      "reason": "More specific than the broad phrase 生活成本 and matches real Bilibili wording.",
      "risk_flags": []
    },
    {
      "inspiration_id": "i2",
      "slot_id": "s1",
      "aspect_id": "生活/生活成本",
      "seed_query": "一线城市生活成本",
      "probe_backend": "exa",
      "source_platform": "bilibili",
      "source_domains": ["bilibili.com"],
      "source_terms": ["5000 元月薪", "月度预算", "消费复盘"],
      "evidence_titles": ["5000 元月薪如何做月度预算"],
      "evidence_urls": ["https://www.bilibili.com/video/BV..."],
      "reason": "Turns the aspect into a concrete budget-review scene.",
      "risk_flags": []
    }
  ]
}
```

Lateral expansion output shape:

```json
{
  "expansions": [
    {
      "expansion_id": "e1",
      "parent_inspiration_id": "i1",
      "parent_expansion_id": "",
      "hop": 1,
      "relation": "sub_scene",
      "text": "上海租房通勤月度账单",
      "detail_axes": ["一线城市", "租房", "通勤", "月度账单"],
      "source_terms": ["上海租房通勤", "月度账单", "吃饭开销"]
    },
    {
      "expansion_id": "e2",
      "parent_inspiration_id": "i1",
      "parent_expansion_id": "",
      "hop": 1,
      "relation": "comparison",
      "text": "外卖和做饭成本对比",
      "detail_axes": ["外卖", "做饭", "饮食开销"],
      "source_terms": ["吃饭开销", "月度账单"]
    }
  ]
}
```

Profile curator output shape:

```json
{
  "curated_expansions": [
    {
      "expansion_id": "e1",
      "decision": "keep",
      "score": 0.86,
      "reason": "Specific, searchable, profile-aligned, and not saturated.",
      "feedback": "",
      "risk_flags": []
    },
    {
      "expansion_id": "e2",
      "decision": "revise",
      "score": 0.74,
      "reason": "Useful comparison, but should avoid generic saving-money phrasing.",
      "feedback": "Rewrite as a concrete monthly food-cost comparison.",
      "risk_flags": []
    }
  ]
}
```

Detail expansion output shape:

```json
{
  "expanded_details": [
    {
      "expansion_id": "e1",
      "angle_label": "城市租房通勤成本",
      "detail_axes": ["上海", "租房", "通勤", "吃饭", "月度账单"],
      "query_terms": ["上海租房通勤月开销", "一线城市月度账单复盘"]
    }
  ]
}
```

Brainstorm input shape:

```json
{
  "platform": "bilibili",
  "profile_context": {
    "stable_interests": ["AI Agent", "数码科技", "心理健康", "生活成本"],
    "interest_domains": [
      {"domain": "科技", "specifics": ["AI Agent", "多模态学习", "数码科技"]},
      {"domain": "生活", "specifics": ["生活成本", "心理健康"]}
    ],
    "freshness": {
      "recent_awareness": ["开源 agent 框架讨论升温"],
      "active_insights": ["用户最近对工具实用性和真实体验更敏感"]
    },
    "disliked_topics": ["猎奇吃播"]
  },
  "inspiration_context": [
    {
      "inspiration_id": "i1",
      "aspect_id": "生活/生活成本",
      "probe_backend": "exa",
      "source_domains": ["bilibili.com"],
      "source_terms": ["上海租房通勤", "月度账单", "吃饭开销"],
      "reason": "More specific than the broad phrase 生活成本 and matches real Bilibili wording."
    }
  ],
  "curated_expansions": [
    {
      "expansion_id": "e1",
      "parent_inspiration_id": "i1",
      "relation": "sub_scene",
      "text": "上海租房通勤月度账单",
      "curator_score": 0.86,
      "detail_axes": ["一线城市", "租房", "通勤", "月度账单"],
      "query_terms": ["上海租房通勤月开销", "一线城市月度账单复盘"]
    }
  ],
  "slots": [
    {
      "slot_id": "s1",
      "aspect_id": "生活/生活成本",
      "query_kind": "undercovered_specific",
      "need": 3,
      "domain": "生活",
      "interest_name": "生活成本",
      "avoid_keywords": ["生活", "生活成本"],
      "platform_fit_hint": "bilibili: personal budget, city living, real cost breakdown"
    }
  ]
}
```

Brainstorm output shape:

```json
{
  "bilibili": [
    {
      "slot_id": "s1",
      "angles": [
        {
          "angle_id": "a1",
          "label": "城市租房通勤成本",
          "rationale": "Uses real Bilibili wording from the inspiration seed to make 生活成本 searchable.",
          "inspiration_ids": ["i1"],
          "expansion_ids": ["e1"],
          "platform_terms": ["上海租房", "通勤", "月度账单"],
          "avoid_terms": ["生活"],
          "novelty_tags": ["city-life", "budget"]
        },
        {
          "angle_id": "a2",
          "label": "打工人预算复盘",
          "rationale": "Turns living-cost interest into concrete monthly budget-review content.",
          "inspiration_ids": ["i2"],
          "expansion_ids": [],
          "platform_terms": ["5000 元月薪", "预算复盘", "打工人"],
          "avoid_terms": ["生活热点"],
          "novelty_tags": ["budget", "personal-finance"]
        }
      ]
    }
  ]
}
```

Realization output shape:

```json
{
  "bilibili": [
    {
      "slot_id": "s1",
      "angle_id": "a1",
      "queries": [
        "上海租房通勤月开销",
        "一线城市月度账单复盘"
      ]
    },
    {
      "slot_id": "s1",
      "angle_id": "a2",
      "queries": [
        "打工人月薪预算复盘",
        "5000 元月薪消费复盘"
      ]
    }
  ]
}
```

The parser should reject:

- angles that cannot be traced back to a known `slot_id`;
- inspiration seeds that cannot be traced back to a known `slot_id` and `aspect_id`;
- expansion candidates that cannot be traced back to a known `inspiration_id` or allowed parent
  expansion;
- query candidates that cannot be traced back to a known `slot_id` and `angle_id`;
- angle references to unknown or rejected `inspiration_id` values;
- angle references to rejected or cooled-down `expansion_id` values;
- raw brainstorm labels returned as final keywords without realization.

### 11. Candidate Filtering And Diversified Selection

Before inserting pending keywords:

1. Normalize query text.
2. Drop exact and near-duplicate candidates already in-flight.
3. Drop candidates matching disliked topics or disliked franchises as a hard filter.
4. Drop candidates whose normalized query or semantic family is under cooldown.
5. Drop candidates whose inspiration seed has active `disliked_adjacent`, `failed_family`, or
   `low_confidence` risk flags unless a later curation step explicitly cleared the risk.
6. Drop candidates whose expansion is rejected, cooled down, or above the allowed hop limit.
7. Penalize broad naked category words such as `游戏`, `篮球`, `动漫`, `科技` unless the slot explicitly
   requested a broad core query.
8. Select final candidates using MMR-style relevance/diversity scoring.
9. Enforce xQuAD-style aspect coverage: a candidate gains value when it covers an uncovered selected
   aspect, and loses value when it only duplicates an already covered aspect.

Research basis:

- MMR: relevance plus novelty to reduce redundant results.
- xQuAD: explicit sub-query/aspect coverage for diversification.
- Rocchio/relevance feedback: positive and negative feedback should reshape future queries.
- Contextual bandits: exploration/exploitation allocation should learn from observed rewards.

### 12. Negative Memory

Add query-level negative memory independent of the pending keyword table.

Suggested key:

```text
(platform, normalized_keyword, profile_kw_digest or profile_cluster_digest)
```

Tracked fields:

- `generated_count`
- `failed_count`
- `expired_count`
- `used_count`
- `yield_count`
- `last_terminal_status`
- `last_seen_at`
- `cooldown_until`

Cooldown rules:

- repeated `failed` without yield -> increasing cooldown;
- repeated `expired` without claim -> cooldown unless caused by profile digest change;
- `used` with zero yield after the admit safety window -> cooldown;
- disliked-topic match -> hard block, not cooldown;
- positive yield -> reduce penalty and allow future exploit.

This directly addresses the failure where `failed` and `expired` rows are invisible to
`recent_keywords`.

### 13. Interaction With Existing Signals

Keep existing signals, but assign clear ownership:

- `recent_keywords`: short-term in-flight and just-used duplicate suppression;
- `pool_distribution`: current content saturation and cold-start breadth;
- `supply_hint`: platform-specific historical supply strength;
- `coverage_ledger`: profile-interest coverage and execution effectiveness;
- `inspiration_probe`: Exa-derived platform/web vocabulary, scenes, entities, and adjacent phrases;
- `lateral_expansion_graph`: reusable frontier of inspiration-derived adjacent search directions;
- `profile_curator`: profile-aware scoring, rejection, revision feedback, and cooldown decisions;
- `negative_memory`: long-term query failure and zero-yield suppression.

No single signal should dominate. In particular, supply hints must not erase
`undercovered_specific` quota.

## Example Flow

Assume the current profile contains these signals:

- high-weight interests: `AI Agent`, `动漫`, `游戏机制分析`, `NBA`;
- medium/long-tail interests: `数码科技`, `心理健康`, `生活成本`, `文化评论`, `游戏评价`;
- recent awareness: `开源 agent 框架讨论升温`, `AI 硬件与本地模型热度上升`;
- disliked topics: `猎奇吃播`;
- current pool distribution: `动漫` and `游戏` are already well supplied;
- keyword memory: `篮球`, `王者荣耀`, and `猎奇吃播` have many failed or expired rows.

### Step 1: Build Aspect Inventory

The scheduler builds inventory from the full effective profile, not only the LLM-visible compact
summary.

Example aspects:

| aspect_id | domain | interest_name | initial state |
| --- | --- | --- | --- |
| `科技/AI Agent` | 科技 | AI Agent | core, productive |
| `科技/数码科技` | 科技 | 数码科技 | under-covered |
| `生活/心理健康` | 生活 | 心理健康 | under-covered |
| `生活/生活成本` | 生活 | 生活成本 | under-covered |
| `文化/文化评论` | 文化 | 文化评论 | under-covered |
| `游戏/游戏评价` | 游戏 | 游戏评价 | broad domain saturated, specific aspect under-covered |
| `体育/NBA` | 体育 | NBA | partially covered |

### Step 2: Score Coverage Debt

The ledger and memory change the plan before the LLM is called:

- `动漫` and broad `游戏` receive pool saturation penalties;
- `篮球` and `王者荣耀` receive repeated-failure cooldown;
- `猎奇吃播` is hard-blocked by disliked-topic matching;
- `数码科技`, `心理健康`, `生活成本`, and `文化评论` receive coverage-debt bonuses;
- `AI Agent` remains eligible as `core`, but cannot consume the whole batch.

For a `30` keyword batch, the selector might allocate:

| query_kind | slots | example aspects |
| --- | ---: | --- |
| `core` | 12 | `科技/AI Agent`, `体育/NBA` |
| `undercovered_specific` | 9 | `科技/数码科技`, `生活/心理健康`, `生活/生活成本`, `文化/文化评论` |
| `bridge` | 4 | `AI Agent + 工具实用性`, `游戏评价 + 机制分析` |
| `world_scan` | 3 | `本地模型硬件`, `开源 agent 框架` |
| `feedback_repair` | 2 | from recently yielded titles/tags |

### Step 3: Inspiration Search Probe

The planner creates a few seed queries for selected slots and runs lightweight Exa preview searches
through Agent-Reach / `mcporter`. Preview results are not admitted to recommendation and are not
inserted as keywords.

Example seed queries:

```json
{
  "bilibili": [
    {
      "slot_id": "s1",
      "aspect_id": "科技/数码科技",
      "probe_backend": "exa",
      "domain_filters": ["bilibili.com"],
      "seed_queries": ["数码产品长期使用体验", "AI 硬件 本地模型"]
    },
    {
      "slot_id": "s2",
      "aspect_id": "生活/生活成本",
      "probe_backend": "exa",
      "domain_filters": ["bilibili.com"],
      "seed_queries": ["一线城市生活成本", "年轻人月度预算"]
    }
  ]
}
```

Equivalent probe command shape:

```bash
mcporter call 'exa.web_search_exa(query: "一线城市生活成本", numResults: 5, includeDomains: ["bilibili.com"])'
```

Example preview snippets:

```json
{
  "seed_query": "一线城市生活成本",
  "probe_backend": "exa",
  "domain_filters": ["bilibili.com"],
  "preview_items": [
    {
      "title": "上海租房通勤吃饭一个月到底花多少",
      "url": "https://www.bilibili.com/video/BV...",
      "source_domain": "bilibili.com",
      "snippet": "普通打工人在上海的月度账单复盘，包含租房、通勤和吃饭开销"
    },
    {
      "title": "普通打工人 5000 元月薪预算复盘",
      "url": "https://www.bilibili.com/video/BV...",
      "source_domain": "bilibili.com",
      "snippet": "收入有限时的真实消费结构"
    }
  ]
}
```

Extracted inspiration seeds:

```json
{
  "inspiration_seeds": [
    {
      "inspiration_id": "i1",
      "slot_id": "s1",
      "aspect_id": "科技/数码科技",
      "probe_backend": "exa",
      "source_domains": ["bilibili.com"],
      "source_terms": ["长期使用体验", "真实评测", "生产力设备"],
      "reason": "Bilibili uses hands-on review language more than the broad 数码科技 label."
    },
    {
      "inspiration_id": "i2",
      "slot_id": "s1",
      "aspect_id": "科技/数码科技",
      "probe_backend": "exa",
      "source_domains": ["bilibili.com"],
      "source_terms": ["本地模型", "AI 硬件", "实测"],
      "reason": "Connects 数码科技 with current local-model hardware interest."
    },
    {
      "inspiration_id": "i3",
      "slot_id": "s2",
      "aspect_id": "生活/生活成本",
      "probe_backend": "exa",
      "source_domains": ["bilibili.com"],
      "source_terms": ["上海租房通勤", "月度账单", "吃饭开销"],
      "reason": "Makes 生活成本 concrete and searchable."
    },
    {
      "inspiration_id": "i4",
      "slot_id": "s2",
      "aspect_id": "生活/生活成本",
      "probe_backend": "exa",
      "source_domains": ["bilibili.com"],
      "source_terms": ["5000 元月薪", "预算复盘", "打工人"],
      "reason": "Turns the aspect into a real-life budget-review scene."
    }
  ]
}
```

### Step 4: Lateral Expansion, Profile Curation, And Detail Expansion

The planner expands useful inspiration seeds sideways, then asks a profile-aware curator to decide
which expansion candidates deserve query budget.

Example lateral expansions:

```json
{
  "expansions": [
    {
      "expansion_id": "e1",
      "parent_inspiration_id": "i3",
      "hop": 1,
      "relation": "sub_scene",
      "text": "上海租房通勤月度账单",
      "detail_axes": ["上海", "租房", "通勤", "吃饭", "月度账单"]
    },
    {
      "expansion_id": "e2",
      "parent_inspiration_id": "i3",
      "hop": 1,
      "relation": "comparison",
      "text": "外卖和做饭成本对比",
      "detail_axes": ["外卖", "做饭", "一线城市", "饮食开销"]
    },
    {
      "expansion_id": "e3",
      "parent_inspiration_id": "i4",
      "hop": 1,
      "relation": "decision_context",
      "text": "5000 元月薪消费取舍",
      "detail_axes": ["打工人", "月薪", "预算", "消费复盘"]
    }
  ]
}
```

Example curator output:

```json
{
  "curated_expansions": [
    {
      "expansion_id": "e1",
      "decision": "keep",
      "score": 0.87,
      "reason": "Specific, searchable, and profile-aligned.",
      "risk_flags": []
    },
    {
      "expansion_id": "e2",
      "decision": "keep",
      "score": 0.81,
      "reason": "Concrete comparison angle with enough platform vocabulary.",
      "risk_flags": []
    },
    {
      "expansion_id": "e3",
      "decision": "revise",
      "score": 0.72,
      "feedback": "Promising but should be phrased as monthly budget review, not generic finance."
    }
  ]
}
```

Example detail expansion:

```json
{
  "expanded_details": [
    {
      "expansion_id": "e1",
      "angle_label": "城市租房通勤成本",
      "query_terms": ["上海租房通勤月开销", "一线城市月度账单复盘"]
    },
    {
      "expansion_id": "e2",
      "angle_label": "年轻人饮食成本对比",
      "query_terms": ["外卖和做饭哪个更省钱", "打工人一个月吃饭开销"]
    },
    {
      "expansion_id": "e3",
      "angle_label": "打工人月度预算复盘",
      "query_terms": ["5000 元月薪预算复盘", "年轻人消费预算复盘"]
    }
  ]
}
```

Only curated expansions feed brainstorm angles. Rejected and cooled-down candidates remain in the
graph for observability but do not receive query budget.

### Step 5: Brainstorm Search Angles

The LLM receives the richer `generation_context_view`, inspiration seeds, curated expansion details,
plus binding slots. For slots that need diversity, it generates search angles instead of final
queries.

Example Bilibili slots:

```json
{
  "platform": "bilibili",
  "slots": [
    {
      "slot_id": "s1",
      "aspect_id": "科技/数码科技",
      "query_kind": "undercovered_specific",
      "need": 3,
      "avoid_keywords": ["数码科技"],
      "platform_fit_hint": "prefer hands-on reviews, long-term use, creator experience"
    },
    {
      "slot_id": "s2",
      "aspect_id": "生活/生活成本",
      "query_kind": "undercovered_specific",
      "need": 2,
      "avoid_keywords": ["生活"],
      "platform_fit_hint": "prefer personal budget, city living, real cost breakdown"
    },
    {
      "slot_id": "s3",
      "aspect_id": "科技/AI Agent",
      "query_kind": "bridge",
      "need": 2,
      "platform_fit_hint": "connect AI Agent with practical workflow and tool evaluation"
    }
  ]
}
```

Possible brainstorm output:

```json
{
  "bilibili": [
    {
      "slot_id": "s1",
      "angles": [
        {
          "angle_id": "a1",
          "label": "长期使用体验",
          "rationale": "把数码科技兴趣落到真实体验和耐用性，而不是泛科技新闻。",
          "inspiration_ids": ["i1"],
          "expansion_ids": [],
          "platform_terms": ["长期使用", "真实体验", "复盘"],
          "avoid_terms": ["数码科技"],
          "novelty_tags": ["hands-on", "review"]
        },
        {
          "angle_id": "a2",
          "label": "本地模型硬件",
          "rationale": "结合 recent awareness 中的本地模型硬件热度。",
          "inspiration_ids": ["i2"],
          "expansion_ids": [],
          "platform_terms": ["本地模型", "AI 硬件", "实测"],
          "avoid_terms": ["AI 新闻"],
          "novelty_tags": ["local-llm", "hardware"]
        }
      ]
    },
    {
      "slot_id": "s2",
      "angles": [
        {
          "angle_id": "a3",
          "label": "城市月度账单",
          "rationale": "把生活成本兴趣落到可搜索、可评估的真实开销复盘。",
          "inspiration_ids": ["i3", "i4"],
          "expansion_ids": ["e1", "e3"],
          "platform_terms": ["一线城市", "月度开销", "租房"],
          "avoid_terms": ["生活"],
          "novelty_tags": ["budget", "city-life"]
        }
      ]
    },
    {
      "slot_id": "s3",
      "angles": [
        {
          "angle_id": "a4",
          "label": "真实工作流",
          "rationale": "连接 AI Agent 和用户对工具实用性的近期洞察。",
          "platform_terms": ["工作流", "实战", "自动化"],
          "avoid_terms": ["AI"],
          "novelty_tags": ["workflow", "practice"]
        }
      ]
    }
  ]
}
```

Example Reddit brainstorm output for the same profile might include angles such as:

```json
{
  "reddit": [
    {
      "slot_id": "s1",
      "angles": [
        {
          "angle_id": "a1",
          "label": "long-term gadget ownership",
          "platform_terms": ["long term review", "daily driver", "ownership experience"],
          "novelty_tags": ["review", "ownership"]
        },
        {
          "angle_id": "a2",
          "label": "local LLM hardware setup",
          "platform_terms": ["local LLM", "hardware setup", "GPU"],
          "novelty_tags": ["local-llm", "hardware"]
        }
      ]
    },
    {
      "slot_id": "s2",
      "angles": [
        {
          "angle_id": "a3",
          "label": "monthly budget breakdown",
          "platform_terms": ["monthly budget", "cost of living", "expense breakdown"],
          "novelty_tags": ["budget", "city-life"]
        }
      ]
    },
    {
      "slot_id": "s3",
      "angles": [
        {
          "angle_id": "a4",
          "label": "framework experience",
          "platform_terms": ["LangGraph", "AutoGen", "real experience"],
          "novelty_tags": ["tools", "comparison"]
        }
      ]
    }
  ]
}
```

### Step 6: Curate Angles And Realize Queries

The planner filters and diversifies angles before asking for final query text:

- drop angles that are too generic, e.g. `科技新闻`, `游戏热点`;
- drop angles too close to disliked or failed query families;
- keep a mix of practical, comparative, problem-driven, and adjacent-world angles;
- prefer angles that cover under-covered aspects.

Selected angles are then realized into concrete search terms:

```json
{
  "bilibili": [
    {
      "slot_id": "s1",
      "angle_id": "a1",
      "expansion_id": "",
      "queries": ["数码产品长期使用体验", "生产力设备真实评测"]
    },
    {
      "slot_id": "s1",
      "angle_id": "a2",
      "expansion_id": "",
      "queries": ["AI 硬件 本地模型 实测"]
    },
    {
      "slot_id": "s2",
      "angle_id": "a3",
      "expansion_id": "e1",
      "queries": ["一线城市生活成本", "年轻人月度预算复盘"]
    },
    {
      "slot_id": "s3",
      "angle_id": "a4",
      "expansion_id": "",
      "queries": ["AI Agent 工作流实战", "开源 Agent 工具体验"]
    }
  ]
}
```

### Step 7: Filter And Insert

Before insertion:

- broad naked categories like `科技`, `游戏`, `生活` are dropped unless a broad slot asked for them;
- `猎奇吃播` is blocked;
- `篮球` and `王者荣耀` are skipped while cooldown is active;
- near-duplicates of recent or pending rows are removed;
- the final selection keeps at least one query per selected under-covered aspect when candidates
  exist.

Inserted rows carry metadata:

| keyword | aspect_id | inspiration_backend | inspiration_id | expansion_id | angle_id | query_kind | generation_reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `数码产品长期使用体验` | `科技/数码科技` | `exa` | `i1` | `none` | `a1` | `undercovered_specific` | coverage debt |
| `一线城市生活成本` | `生活/生活成本` | `exa` | `i3` | `e1` | `a3` | `undercovered_specific` | coverage debt |
| `AI Agent 工作流实战` | `科技/AI Agent` | `none` | `none` | `none` | `a4` | `bridge` | core + freshness |

### Step 8: Update Ledger

After fetch and evaluation:

- if `数码产品长期使用体验` yields admitted content, `科技/数码科技.yield_count`,
  `inspiration_id=i1.yielded_count`, and `angle_id=a1.yielded_count` increase, allowing the aspect
  and its platform-native wording to move from probe to exploit;
- if `一线城市生活成本` yields admitted content, `expansion_id=e1.yielded_count` increases. That
  permits one more hop from the same expansion family in a future planner cycle;
- if `一线城市生活成本` is claimed but yields nothing, the aspect keeps uncertainty but the exact query
  family receives zero-yield penalty;
- if a generated query expires unclaimed repeatedly, its aspect records execution failure instead of
  being mistaken for user disinterest.

The next planner cycle can then select different under-covered aspects instead of rediscovering the
same broad high-frequency terms.

## Data Model

### Extend `discovery_keywords`

Add nullable metadata columns:

- `aspect_id TEXT DEFAULT ''`
- `inspiration_backend TEXT DEFAULT ''`
- `inspiration_id TEXT DEFAULT ''`
- `inspiration_terms TEXT DEFAULT ''`
- `expansion_id TEXT DEFAULT ''`
- `expansion_label TEXT DEFAULT ''`
- `angle_id TEXT DEFAULT ''`
- `angle_label TEXT DEFAULT ''`
- `query_kind TEXT DEFAULT 'core'`
- `source_domain TEXT DEFAULT ''`
- `source_interest TEXT DEFAULT ''`
- `generation_reason TEXT DEFAULT ''`
- `normalized_keyword TEXT DEFAULT ''`

`keyword_kind` remains lifecycle routing (`regular`, `explore`, etc.) unless implementation chooses to
merge the concepts. If both fields exist, use:

- `keyword_kind`: which consumer may claim this row;
- `query_kind`: why this query was generated.

### Add `discovery_inspiration_probe_cache`

```sql
CREATE TABLE discovery_inspiration_probe_cache (
    platform TEXT NOT NULL,
    profile_kw_digest TEXT NOT NULL,
    aspect_id TEXT NOT NULL,
    query_kind TEXT NOT NULL,
    probe_backend TEXT NOT NULL DEFAULT 'exa',
    freshness_digest TEXT NOT NULL DEFAULT '',
    seed_query TEXT NOT NULL,
    domain_filters_json TEXT NOT NULL DEFAULT '[]',
    inspiration_id TEXT NOT NULL,
    source_domains_json TEXT NOT NULL DEFAULT '[]',
    source_terms_json TEXT NOT NULL DEFAULT '[]',
    evidence_titles_json TEXT NOT NULL DEFAULT '[]',
    evidence_urls_json TEXT NOT NULL DEFAULT '[]',
    reason TEXT NOT NULL DEFAULT '',
    risk_flags_json TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP,
    selected_count INTEGER NOT NULL DEFAULT 0,
    yielded_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (
        platform,
        profile_kw_digest,
        aspect_id,
        query_kind,
        probe_backend,
        freshness_digest,
        seed_query,
        inspiration_id
    )
);
```

This cache records the short-lived platform vocabulary that inspired query expansion. It should be
small, TTL-bound, and safe to rebuild.

### Add `discovery_inspiration_expansion_cache`

```sql
CREATE TABLE discovery_inspiration_expansion_cache (
    platform TEXT NOT NULL,
    profile_kw_digest TEXT NOT NULL,
    aspect_id TEXT NOT NULL,
    query_kind TEXT NOT NULL,
    inspiration_id TEXT NOT NULL,
    parent_expansion_id TEXT NOT NULL DEFAULT '',
    expansion_id TEXT NOT NULL,
    hop INTEGER NOT NULL DEFAULT 1,
    relation TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    detail_axes_json TEXT NOT NULL DEFAULT '[]',
    source_terms_json TEXT NOT NULL DEFAULT '[]',
    curator_decision TEXT NOT NULL DEFAULT '',
    curator_score REAL NOT NULL DEFAULT 0.0,
    curator_reason TEXT NOT NULL DEFAULT '',
    curator_feedback TEXT NOT NULL DEFAULT '',
    risk_flags_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'new',
    created_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP,
    last_selected_at TIMESTAMP,
    selected_count INTEGER NOT NULL DEFAULT 0,
    realized_count INTEGER NOT NULL DEFAULT 0,
    yielded_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    cooldown_until TIMESTAMP,
    PRIMARY KEY (
        platform,
        profile_kw_digest,
        aspect_id,
        query_kind,
        inspiration_id,
        expansion_id
    )
);
```

This cache is the persistent frontier for lateral expansion. It lets the planner continue from
useful inspiration families in later cycles without re-expanding every seed from scratch.

### Add `discovery_query_angle_cache`

```sql
CREATE TABLE discovery_query_angle_cache (
    platform TEXT NOT NULL,
    profile_kw_digest TEXT NOT NULL,
    aspect_id TEXT NOT NULL,
    query_kind TEXT NOT NULL,
    inspiration_digest TEXT NOT NULL DEFAULT '',
    expansion_digest TEXT NOT NULL DEFAULT '',
    freshness_digest TEXT NOT NULL DEFAULT '',
    angle_id TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    rationale TEXT NOT NULL DEFAULT '',
    inspiration_ids_json TEXT NOT NULL DEFAULT '[]',
    expansion_ids_json TEXT NOT NULL DEFAULT '[]',
    platform_terms_json TEXT NOT NULL DEFAULT '[]',
    avoid_terms_json TEXT NOT NULL DEFAULT '[]',
    novelty_tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP,
    last_selected_at TIMESTAMP,
    selected_count INTEGER NOT NULL DEFAULT 0,
    yielded_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (
        platform,
        profile_kw_digest,
        aspect_id,
        query_kind,
        inspiration_digest,
        expansion_digest,
        freshness_digest,
        angle_id
    )
);
```

This cache lets stable aspects reuse good brainstorm directions without paying a fresh LLM call every
planner cycle. It also gives the planner a way to learn which angle families actually yield useful
content.

### Add `discovery_aspect_coverage`

```sql
CREATE TABLE discovery_aspect_coverage (
    platform TEXT NOT NULL,
    profile_kw_digest TEXT NOT NULL,
    aspect_id TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT '',
    interest_name TEXT NOT NULL DEFAULT '',
    generated_count INTEGER NOT NULL DEFAULT 0,
    claimed_count INTEGER NOT NULL DEFAULT 0,
    used_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    expired_count INTEGER NOT NULL DEFAULT 0,
    yield_count INTEGER NOT NULL DEFAULT 0,
    zero_yield_used_count INTEGER NOT NULL DEFAULT 0,
    last_generated_at TIMESTAMP,
    last_used_at TIMESTAMP,
    last_yield_at TIMESTAMP,
    cooldown_until TIMESTAMP,
    last_failure_reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (platform, profile_kw_digest, aspect_id)
);
```

### Add `discovery_keyword_memory`

```sql
CREATE TABLE discovery_keyword_memory (
    platform TEXT NOT NULL,
    normalized_keyword TEXT NOT NULL,
    profile_kw_digest TEXT NOT NULL DEFAULT '',
    generated_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    expired_count INTEGER NOT NULL DEFAULT 0,
    used_count INTEGER NOT NULL DEFAULT 0,
    yield_count INTEGER NOT NULL DEFAULT 0,
    last_terminal_status TEXT NOT NULL DEFAULT '',
    last_seen_at TIMESTAMP,
    cooldown_until TIMESTAMP,
    PRIMARY KEY (platform, normalized_keyword, profile_kw_digest)
);
```

## Observability

Every planner cycle should log a structured ledger:

- due platforms;
- profile context caps and selected counts;
- freshness lane counts by field;
- selected slot counts by `query_kind`;
- selected aspect ids;
- inspiration probe counts by backend, platform, aspect, and seed query;
- Exa domain-filter usage and fallback counts;
- extracted inspiration seed counts and rejected seed counts by reason;
- lateral expansion counts by relation and hop;
- profile curator decisions by `keep`, `revise`, `reject`, and `cooldown`;
- detail expansion counts and selected expansion ids;
- brainstorm angle counts by `query_kind`;
- rejected angle counts by reason;
- inserted query counts by `query_kind`;
- inserted query counts by inspiration seed;
- inserted query counts by expansion;
- inserted query counts by angle;
- skipped candidate counts by reason:
  - `duplicate_inflight`
  - `recent_keyword`
  - `negative_memory_cooldown`
  - `disliked_topic`
  - `near_duplicate`
  - `broad_naked_category`
- top coverage-debt aspects before and after the pass.

This should make future diagnosis possible without reading raw LLM prompts.

## Error Handling

- If Exa preview search fails or `mcporter` / Agent-Reach is unavailable, fall back to a
  platform-native adapter when available; otherwise continue with deterministic seed terms from the
  selected aspect and mark the probe failure in structured logs.
- If inspiration extraction returns no usable seeds, brainstorm from the selected aspect and freshness
  lane, but mark the slot as `uninspired_fallback` for observability.
- If inspiration seeds are all rejected by filters, do not insert any seed text directly as a keyword.
- If lateral expansion fails, continue with curated inspiration seeds directly and record
  `expansion_failed`.
- If the profile curator rejects all expansions, do not force expansion-based queries; fall back to
  the original selected aspect and record `curator_all_rejected`.
- If detail expansion fails, use the kept expansion text and inherited source terms as angle input.
- If brainstorm generation fails, fallback should build deterministic seed angles from the selected
  aspects, platform hints, and freshness lane instead of falling back to global top interests.
- If realization generation fails, fallback should realize queries from selected angles with simple
  platform-specific templates.
- If all brainstorm angles for a slot are rejected, mark the slot as generated with zero inserted and
  record `last_failure_reason`.
- If LLM generation fails, fallback should use the selected aspects and angles, not global top
  interest names.
- If all candidates for a slot are filtered, mark the slot as generated with zero inserted and record
  `last_failure_reason`.
- If too many selected aspects are in cooldown, backfill from the same quota bucket before borrowing
  from `core`.
- If profile is unavailable, planner keeps current skip behavior.
- If migration metadata columns are missing, claim/fetch paths should continue to work with legacy
  rows.

## Rollout Plan

1. Add schema and DAO methods for keyword metadata, aspect coverage, and keyword memory.
2. Expand query-generation profile context caps behind a feature flag.
3. Build aspect inventory and slot selector behind the same feature flag.
4. Add Exa-backed inspiration seed query generation, preview search probe, extraction parser, and
   TTL cache.
5. Add lateral expansion prompt/parser, profile-aware curator, detail expander, and expansion cache.
6. Add brainstorm angle prompt, parser, cache, and angle curation.
7. Add structured realization prompt and parser for selected angles.
8. Add candidate filtering, cooldown, and diversified selection.
9. Update lifecycle paths to write coverage, inspiration yield, expansion yield, angle yield, and
   negative memory.
10. Turn on observability while keeping old planner path as fallback.
11. Enable the slot-based planner by default after tests and local diagnosis show reduced repetition.

## Non-Goals

- Do not force every profile interest to be searched every cycle.
- Do not remove pool distribution or recent keyword hints.
- Do not change recommendation ranking or serving diversification.
- Do not require every source platform to be enabled.
- Do not make `world_scan` dominate core recommendations; it is a bounded exploration budget.
- Do not run a separate brainstorm LLM call for every individual keyword.
- Do not run Exa preview search for every individual keyword.
- Do not require `core` slots to use brainstorming when direct realization is sufficient.
- Do not admit Exa preview-search results into recommendation pools unless they are later fetched
  through normal keyword lifecycle.
- Do not allow unbounded recursive expansion. Default max hop is `1`; hop `2` requires prior yield.
- Do not let the profile curator expand or realize queries itself; it only scores, rejects, revises,
  and explains.

## Acceptance Criteria

- [ ] A profile interest absent from effective search coverage receives `undercovered_specific` slots
      within a bounded number of planner cycles.
- [ ] Repeated failed/expired high-frequency queries enter cooldown and are not reinserted while the
      cooldown is active.
- [ ] Disliked-topic-matching queries are blocked before insertion.
- [ ] Every newly generated keyword records `aspect_id`, `inspiration_backend`, `inspiration_id`,
      `expansion_id`, `angle_id`, `query_kind`, and normalized query metadata when those fields are
      available.
- [ ] Exa preview search results and extracted inspiration seeds are never inserted as keywords before
      brainstorm, curation, realization, and final filtering.
- [ ] `undercovered_specific`, `bridge`, and `world_scan` slots go through brainstormed angle
      generation before final query realization.
- [ ] Raw brainstorm angles are never inserted as keywords before curation and realization.
- [ ] Planner logs show selected aspect distribution and skipped-candidate reasons.
- [ ] Planner logs show Exa probe counts, domain-filter usage, extracted seed counts, rejected seed
      counts, and uninspired fallbacks.
- [ ] Planner logs show lateral expansion counts, profile curator decisions, detail expansion counts,
      selected expansions, and expansion-yield updates.
- [ ] Planner logs show generated, rejected, selected, inserted, and yielded angle counts.
- [ ] Lateral expansion is bounded by hop and per-cycle quotas; hop `2` only occurs after positive
      yield from the parent inspiration or expansion family.
- [ ] Existing pool distribution hints still influence saturation, but cannot eliminate the
      under-covered profile-interest quota.
- [ ] Expanded profile context includes more long-tail interests than the current compact summary
      without sending the raw full profile to the LLM.
- [ ] Freshness lane fields can influence `bridge` and `world_scan` slots without converting volatile
      topics into permanent `core` interests.
- [ ] Fallback generation uses selected aspect slots instead of top-ranked profile names.
- [ ] Tests cover inspiration extraction shape, lateral expansion shape, profile curator decisions,
      detail expansion shape, brainstorm prompt shape, realization prompt shape, parser shape, aspect
      coverage updates, inspiration cache updates, expansion cache updates, angle cache updates,
      negative memory cooldown, disliked-topic blocking, and high-frequency duplicate suppression.
