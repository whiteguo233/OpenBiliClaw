# 2026-05-24 - Interest Probe Challenge Spec

## 0. Scope

This spec tightens the role of interest probes after user feedback that the
system can overfit quickly: one "多来点" or a few chat turns can make later
recommendations feel trapped by the user's most recent actions.

Interest probes should be the product surface that breaks the information
bubble. The main recommendation feed may optimize for fit, but probes must
deliberately test directions outside the current dominant profile.

Affected areas:

| Area | Required outcome |
|------|------------------|
| Interest probe generation | Generate a wider, explicit range of exploration distances instead of only adjacent interests |
| Probe confirmation | Treat probe-message confirmation and profile-page confirmation as direct confirmation |
| Chat confirmation | Promote only strong affirmative chat expressions directly; route weak expressions to short-term exploration |
| Recommendation use | Confirmation can write the profile, but recommendation share must ramp gradually |

Out of scope:

- Replacing the existing recommendation ranking system.
- Making challenge probes random or unrelated to the user.
- Letting a single content click create a long-term interest.
- Solving provider failures, embedding failures, or semantic dedupe failures directly.

## 1. Problem

The current system already has probe novelty and experience-axis diversity:

- `SpeculativeInterest` has `experience_mode` and `entry_load`.
- probe selection tracks recent `probed_domains` and `probed_axes`.
- `ProbeNoveltyGuard` filters obvious repeats against the profile, active
  speculations, cooldown state, and probe feedback history.

Those rules reduce local repetition, but the probe still behaves mostly like
"take known interests and drill a little deeper." That is useful for click
quality, but it is too conservative for an anti-bubble surface.

The feedback pattern we want to avoid:

```text
user watches one video
user clicks "多来点"
system over-amplifies the nearby topic
recommendations and probes both collapse around that topic
```

The product correction is:

```text
main feed = fit and usefulness
interest probes = controlled challenge against the current profile
```

## 2. Definitions

### 2.1 Confirmation vs Amplification

Confirmation and feed amplification are separate decisions.

```text
confirmation = write or promote the interest in the profile
amplification = how much recommendation inventory this interest can occupy
```

A user can directly confirm a direction without the feed immediately becoming
dominated by that direction.

### 2.2 Exploration Distance

Each generated probe must carry an exploration-distance label. The label is
used for generation quotas, probe ordering, and future diagnostics.

| Distance | Meaning | Example |
|----------|---------|---------|
| `near` | A narrow extension inside a known axis | "机器人技术" -> "四足机器人调参" |
| `lateral` | Same viewing posture or adjacent domain, different topic | "机器人技术" -> "手作机械结构" |
| `bridge` | Connected by motive, aesthetic, or cognitive style | "游戏机制拆解" -> "桌游规则设计" |
| `wildcard` | Deliberately farther away, still explainable | "技术拆解" -> "冷门职业现场记录" |

`lateral`, `bridge`, and `wildcard` are challenge probes. They are not random
recommendations. Each must explain why it might resonate.

### 2.3 Direct Confirmation

Direct confirmation means the user explicitly accepts a direction-level or
profile-level claim. It should promote the direction without waiting for a
multi-event behavioural threshold.

Direct confirmation does not mean max weight, feed flooding, or permanent
dominance.

## 3. Product Rules

### 3.1 Probe Distance Quotas

The generator should oversample and then locally select a balanced active probe
set. Recommended default quota:

```text
near      40%
lateral   30%
bridge    20%
wildcard  10%
```

This keeps enough nearby probes to remain useful, while reserving explicit
capacity for anti-bubble exploration.

If the model cannot produce enough viable candidates for a bucket, selection may
fall back to the nearest available bucket. The fallback must be logged or
observable in tests so silent collapse is detectable.

Enforcement is split by layer:

```text
prompt: soft quota guidance and schema pressure
local selector: hard quota when viable candidates exist
```

The selector must not merely prefer quota coverage. It must enforce these hard
rules when enough candidates pass novelty and quality gates:

- `near` cannot exceed `ceil(limit * 0.40)` if viable challenge candidates exist.
- At least one challenge probe must be selected when any viable `lateral`,
  `bridge`, or `wildcard` candidate exists.
- For `limit >= 4`, at least two distance bands must appear when candidates
  exist for two or more bands.
- `wildcard` remains low-frequency; missing wildcard candidates must not block
  the active pool.

For push-time selection, "viable" means the candidate has already passed the
filters in Section 6 steps 1-2: recent-domain exclusion and negative-feedback
exclusion. If those filters remove every challenge candidate, the hard quota
automatically degrades and a `near` probe may be pushed.

### 3.2 Confirmation Matrix

| User action | Confirmation behaviour | Recommendation behaviour |
|-------------|------------------------|--------------------------|
| Recommendation card "喜欢" / "多来点" | Not a long-term direct confirmation | Add to short-term exploration only |
| Probe message "喜欢" / "想试试" | Directly confirm the probe direction | Write profile with capped initial weight |
| Profile page "确认喜欢" | Directly confirm the profile direction | Write profile with higher user-confirmed trust |
| Chat says strong affirmative preference | Directly confirm the direction | Write profile with capped initial weight |
| Chat says weak / curious preference | Short-term exploration only | Do not write long-term profile yet |
| Plain click / normal watch | Weak evidence only | No direct long-term feed boost; weak short-term signal only |

Recommended initial weights:

```text
probe message confirmation: 0.45
profile page confirmation:  0.60
strong chat confirmation:   0.50
```

The exact values can be tuned, but the relative order should hold:

```text
profile confirmation > strong chat confirmation >= probe confirmation > card like > click
```

### 3.3 Chat Strength

Strong affirmative examples:

```text
这个方向我喜欢
这就是我想看的
以后多推这种
我就喜欢这种内容
这个可以加入我的画像
```

Weak or exploratory examples:

```text
有点意思
可以看看
偶尔看看也行
还行
先试试
```

Implementation may use LLM classification plus keyword fallback, but the output
must be a small scalar:

```text
strong_positive | weak_positive | neutral | negative
```

Only `strong_positive` direct-confirms the direction. `weak_positive` enters a
short-term exploration buffer.

### 3.4 Direct Confirmation Writeback

When a probe, profile action, or strong chat directly confirms a direction, the
profile writeback should preserve evidence source.

Recommended source labels:

```text
probe_confirmed
profile_confirmed
chat_confirmed
buffer_promoted
```

If the implementation must remain compatible with the existing
`source="speculated"` writeback path, it may keep `speculated` internally for
the first patch, but the desired API/debug surface should distinguish these
sources.

For profile entries, write:

```text
domain
specifics if available
weight
source
first_seen
last_seen
```

Direct confirmation should not erase prior specifics. If the confirmed
direction overlaps an existing domain, merge specifics and raise weight
conservatively instead of creating a duplicate interest.

Chat classification events should be observable. Whenever probe-scoped chat is
classified, persist a compact audit record with the feedback event:

```text
domain
classification: strong_positive | weak_positive | neutral | negative
raw_text_excerpt or summary
classifier: llm | keyword_fallback | failure_default
confidence if available
created_at
resulting_action
```

The raw text excerpt should be bounded in length so logs and runtime state do
not become another privacy or disk-pressure problem.

### 3.5 Amplification Guard

Newly confirmed directions must have a feed-share guard.

The guard has two scopes:

```text
per-batch hard cap:
  first 24 hours after direct confirmation:
  max_new_direction_items = max(1, floor(batch_limit * 0.25))

24h rolling budget:
  across all presented recommendation batches, the same direction should stay
  under 25% of served impressions while it is still newly confirmed

after additional positive evidence:
  allow gradual increase, but keep ordinary topic diversity caps active

after negative feedback:
  immediately reduce weight, pause amplification, or enter cooldown
```

This prevents a new challenge confirmation from creating a new information
bubble.

The matching key is a derived `amplification_key`, not an arbitrary label:

```text
amplification_key = normalized confirmed domain
  plus known matching specifics
  plus mapped topic_group/topic_key aliases when available
```

Both per-batch and 24h rolling checks must use the same `amplification_key`.

Enforcement owner:

- `PoolCurator` should own the rolling-window budget context because it already
  reads recent recommendation history and feedback. The implementation must use
  a real 24h recommendation-history query, not only the existing count-limited
  recent-history window.
- Final batch selection should own the per-batch hard cap because that layer has
  the complete selected batch composition.
- v1 should not add a second independent full ranking system. It should extend
  the existing curator context and final selection cap path.

### 3.6 Short-Term Buffer Promotion

The short-term exploration buffer is not a black hole. It has an explicit
promotion rule.

Recommended v1 scoring:

```text
weak_positive chat: +1.5
card "喜欢" / "多来点": +1.5
long watch / high dwell: +0.5
plain click: +0.25
negative signal: -3 and 48h cooldown/pause
```

During buffer cooldown, new positive events for the same direction are recorded
for audit but do not increase buffer score. Negative events can extend or reset
the cooldown.

A buffered direction promotes to a confirmed interest when all conditions hold:

```text
score >= 4.0
within 7 days
at least 3 positive evidence events
at least one explicit weak-positive event, not only passive clicks
```

Promotion source:

```text
buffer_promoted
```

Initial weight should be capped at or below probe confirmation weight:

```text
buffer_promoted weight <= 0.45
```

The 7-day promotion window and `expires_at` are intentionally different:

```text
promotion_window_days = 7
buffer_expires_at = first_seen + 10 days
```

Promotion checks run before expiry cleanup. After day 7, a buffer entry can
still influence short-term discovery until `expires_at`, but it is no longer
eligible for automatic promotion unless a new explicit weak-positive event
refreshes the window.

If v1 cannot implement buffer promotion in the same patch, it must explicitly
ship the buffer as "temporary influence only" and add a test or TODO proving it
does not silently write long-term profile state.

## 4. Data Model

### 4.1 Speculative Interest Fields

Keep existing fields:

```text
domain
category
reason
experience_mode
entry_load
confidence
weight
specifics
status
confirmation_count
confirmation_threshold
confirming_events
```

Add or persist the following fields:

```text
probe_mode: near | lateral | bridge | wildcard
challenge: bool
confirmation_source: probe_confirmed | profile_confirmed | chat_confirmed | buffer_promoted | ""
confirmed_at: ISO datetime | ""
```

If field churn is too large, `probe_mode` is the only required new persisted
field for the first implementation. `challenge` can be derived as:

```text
probe_mode in {"lateral", "bridge", "wildcard"}
```

### 4.2 Runtime State

Existing runtime fields remain useful:

```text
probed_domains
probed_axes
probe_feedback_history
```

Add a compact recent distance history:

```text
probed_distance_bands: { "near": timestamp, "lateral": timestamp, ... }
```

This allows probe push to avoid repeatedly surfacing only `near` probes even
when domains and axes differ.

Extend `probe_feedback_history` entries, or add an adjacent compact history, so
chat classification output is persisted for tuning and debugging:

```text
classification
raw_text_excerpt or summary
classifier
confidence
resulting_action
```

### 4.3 Short-Term Exploration Buffer

Card likes, "多来点", weak chat positives, and normal clicks should land in a
short-term exploration buffer rather than direct profile confirmation.

Suggested fields:

```text
domain
specifics
amplification_key
score
first_seen
expires_at
last_seen
positive_event_count
explicit_event_count
cooldown_until
recent_evidence: bounded event ids or summaries
```

This buffer can influence discovery and ranking temporarily, but it must not be
rendered as a confirmed profile interest.

## 5. Generation Design

### 5.1 Prompt Contract

`build_speculation_generation_prompt()` should ask the model to return
`probe_mode` for every candidate:

```json
{
  "domain": "城市基础设施观察",
  "category": "知识观察",
  "probe_mode": "bridge",
  "reason": "你喜欢复杂系统怎么运转，这类内容把城市当成一个可拆解的系统来看。",
  "experience_mode": "wander_observe",
  "entry_load": "light",
  "confidence": 0.55,
  "specifics": ["地铁调度观察", "城市排水系统", "机场运行幕后"]
}
```

Prompt rules:

- `near` can drill into known main axes.
- `lateral` should preserve viewing posture while changing topic.
- `bridge` should state the motive or cognitive bridge.
- `wildcard` must be farther away but still explainable.
- Do not make all probes `near`.
- Do not make all challenge probes heavy knowledge content.

### 5.2 Local Selector

The local selector should balance:

```text
confidence
probe_mode quota
experience_mode diversity
entry_load diversity
recent probed domain / axis / distance history
negative probe feedback
```

Selection order should prefer quota coverage first, then confidence. This is
intentional: probe value comes from information gain, not only predicted click
probability.

Quota coverage is hard within the selector when viable candidates exist. Prompt
wording alone is insufficient because model output can collapse toward `near`.

## 6. Probe Selection and Push

When choosing the next probe:

1. Exclude recent `probed_domains`.
2. Exclude domains rejected or chat-negative in `probe_feedback_history`.
3. Prefer a fresh `experience_mode|entry_load` axis.
4. Prefer a fresh `probe_mode` distance band.
5. Use confirmation pressure and weight as tie-breakers.

If all available probes are `near`, the system may still push one. It should
not block the product surface waiting for a perfect challenge probe.

## 7. API and UI Behaviour

### 7.1 Probe Message

Probe cards should make the challenge visible without over-explaining:

```text
这条有点跳出你平时看的范围，要不要试试？
```

Actions:

```text
喜欢 / 想试试 -> direct confirm
不感兴趣 -> reject / cooldown
多聊聊 -> probe-scoped chat
```

### 7.2 Profile Page

Profile confirmation is the strongest user-editing signal.

Actions:

```text
确认喜欢 -> direct confirm with source=profile_confirmed
不是我 -> remove or cooldown the hypothesis
```

Profile-confirmed interests should show as confirmed profile items, not as
pending speculation.

### 7.3 Chat

Probe-scoped chat must classify the user expression after the assistant reply.

```text
strong_positive -> direct confirm
weak_positive   -> short-term exploration
neutral         -> no writeback
negative        -> reject / cooldown
```

Classification failure should default to `neutral`, not direct confirmation.

## 8. Failure Modes

| Failure | Required fallback |
|---------|-------------------|
| LLM fails to return `probe_mode` | Normalize to `near` and continue |
| All challenge candidates fail novelty guard | Push best non-duplicate `near` probe |
| Chat sentiment classifier fails | Treat as `neutral` |
| Profile writeback fails | Return API error and do not record confirmed UI event |
| Recommendation feed over-allocates new interest | PoolCurator budget context plus final batch caps must enforce the amplification guard |

## 9. Testing

Add focused regression coverage:

- generation prompt asks for `probe_mode` and explains all four distance bands.
- parser/defaulting treats missing `probe_mode` as `near`.
- local selector keeps at least one challenge probe when viable candidates exist.
- local selector does not choose only `near` candidates when `lateral` / `bridge`
  candidates pass novelty.
- probe-message confirm directly promotes the speculation.
- profile-page confirm directly writes a confirmed interest.
- strong chat positive directly confirms the direction.
- weak chat positive only enters short-term exploration.
- card "多来点" does not write a long-term profile interest by itself.
- repeated weak buffer evidence promotes only after the explicit score/window
  threshold is met.
- buffer cooldown ignores new positive score increments until cooldown expires.
- buffer `expires_at` is later than the promotion window, avoiding a day-7
  promotion/expiry race.
- newly confirmed directions are capped in recommendation batch share.
- newly confirmed directions are capped across the 24h rolling budget.
- per-batch caps use the exact `max(1, floor(batch_limit * 0.25))` formula.
- rolling and per-batch caps share the same `amplification_key`.
- probe-mode selector enforces hard distance coverage when candidates exist.
- chat classification result and bounded text evidence are persisted.

## 10. Non-Goals

- Do not let challenge probes bypass dislike or avoidance rules.
- Do not require embedding for first implementation.
- Do not rely on strict consecutive confirmation counts for direct confirmation.
- Do not make wildcard probes a large fraction of the product surface.
- Do not convert every confirmed challenge into high-weight recommendation
  dominance.

## 11. Open Questions

1. Should short-term exploration buffer be implemented as a new runtime-memory
   field or reuse existing feedback/recommendation tables? Defer this to the
   implementation plan.
