# Follow-up brainstorm (round 2): CI fix + button placement + strict enforcement

Items identified while addressing the 4 CI failures, the
"button too big" placement issue, and the bug-check pass.
Ordered roughly by pain ÷ effort. Round 1's list is still in
`docs/brainstorm-2026-05.md`.

## 1. The launcher silently drops in-flight chat turns

`extension/popup/popup-launcher.js` imports `fetchChatTurns` at line 14
but never calls it. The chat tab kicks off a turn via `startChatTurn`,
polls for completion in-memory, and loses everything if the popup
closes mid-reply. The backend `chat_turns` table is already durable
(`v0.3.x+: Durable popup chat turns`), so recovery is just plumbing.

**Fix sketch**: on launcher init, `fetchChatTurns({ session: "popup-launcher", limit: 1 })`,
and if the most-recent turn is `status="pending"`, resume polling with its
`turn_id`. ~15 LOC.

## 2. `_warn_on_stranded_source_shares` has dead code after my fix

After today's `_normalized_pool_source_shares` revert (back to `share > 0`
filter), `_source_target_counts` only emits positive entries. The
`if target <= 0: continue` check inside `_warn_on_stranded_source_shares`
(refresh.py:1677) is now unreachable. Either delete it or comment that
it's defensive belt-and-braces.

Trivial. 1 LOC.

## 3. `_source_quotas_for_trim` is used but never directly tested

The new helper that adds explicit `0` quotas for share-disabled platforms
has no test coverage. The test that motivated it
(`test_disabled_bilibili_share_skips_bilibili_refresh_strategies`)
exercises a different code path (refresh-plan, not trim). A direct test:

```python
def test_source_quotas_for_trim_includes_zero_for_disabled_sources():
    controller = ContinuousRefreshController(
        ...,
        pool_source_shares={"bilibili": 8},  # xhs/douyin/youtube missing
    )
    quotas = controller._source_quotas_for_trim()
    assert quotas == {
        "bilibili": 600,
        "xiaohongshu": 0,
        "douyin": 0,
        "youtube": 0,
    }
```

Plus an end-to-end test that `_enforce_pool_cap` actually trims xhs items
from a pool when xhs share goes to 0. ~30 LOC.

## 4. Strict-enforcement only kicks in via `_enforce_pool_cap`

If the pool sits below target and `_enforce_pool_cap` is short-circuited
(it doesn't fire when `pool_available < pool_target_count` AND the
discovery loop just hands control to refresh), share=0 items linger.
The trim functions only run via `_enforce_pool_cap`.

So: user lowers xhs from 2 to 0 while pool is at 580/600. The pool sits
at 580 (under target) until other producers fill the gap; meanwhile
the old xhs items keep getting served as recommendations because the
trim hasn't run.

**Fix sketch**: have `_enforce_pool_cap` ALSO run when shares change,
not just when pool > target. Track a `_last_normalized_shares_fingerprint`
on the controller; when it differs from current, force a trim pass once.
~20 LOC.

## 5. Mark-as-repost button label is now hidden behind a tooltip-only emoji

After today's placement fix, the button is just `🔁` — the meaning is
only discoverable via hover (`title="手动标记为搬运视频..."`). On touch
devices and for keyboard nav users, hover isn't available. Two options:

- Add a small `(?)`-style helper line near the icon row explaining
  what each icon does (one-off legend, dismissible)
- Use `aria-label` more aggressively + ensure screen readers announce
  it. Already set, but could verify with a real screen reader.

Lean toward the helper line — emoji-only icons in productivity tools
generally need affordances.

## 6. Watch-later button on the launcher would be valuable

The launcher shows the watch-later **count** (`watch-later-count`) but
has no way to ADD to it. Users have to open the full popup to bookmark
a card. With 5-icon room now established in the desktop UI, the launcher
could add a star button too — small enough to fit in the existing
rec-card layout, big enough to be discoverable.

~30 LOC (mostly mirroring the desktop pattern).

## 7. The 4 fields on the recommendation card are not interchangeable across UIs

We have 5 places that render a recommendation card with action icons:

| Surface | like | dislike | dismiss | watch-later | mark-repost |
|---|---|---|---|---|---|
| Mobile WebUI | ✓ | ✓ | ✓ | ✓ (star) | — |
| Desktop WebUI | ✓ | ✓ | ✓ | ✓ (star) | ✓ (new, this session) |
| Extension popup (full) | text ("多来点") | text ("少来点") | — | text ("☆ 稍后再看") | text ("🔁 标记为搬运") |
| Launcher (Safari popup) | "👍 感兴趣" | — | — | — (count only) | — |
| Launcher per-card | one stub button | — | — | — | — |

This is divergent enough that a "feedback action" concept should
probably be lifted into a single shared component. Right now each UI
re-implements optimistic flip + reconcile + error recovery, and the
bugs we found this session were in 2 of those 5 places.

Big refactor (~300 LOC). Worth doing before adding a 6th surface.

## 8. `_normalize_shares` in source_policy still accepts share=0

`runtime/source_policy.py::_normalize_shares` accepts `share >= 0`
(my v0.3.89 commit), while `refresh.py::_normalized_pool_source_shares`
now filters `share > 0`. The boundary is correct (popup → source_policy
preserves the user's "I set xhs to 0" → refresh.py drops it), but
the asymmetry could trip up a future reader who finds one and not
the other.

**Fix sketch**: add a docstring note in each function pointing at
the other, explaining why the filter is asymmetric. Trivial.

## 9. CI minutes budget

The macOS smoke build I added in the prior session takes ~5–8 min
per run, and a 10x billing multiplier vs Linux. Even with `concurrency:
cancel-in-progress`, a 5-commit feat branch push burns ~25 macOS-minutes
(GitHub free tier allots 2000/month for public repos, but only 2000 of
those are macOS-equivalent through the multiplier). Worth:

- Adding `paths-ignore` for `docs/**` (the macOS build doesn't care
  about doc-only changes — currently it only triggers on
  `extension/**` and `safari/**`, but documenting the policy in the
  workflow header would help)
- Maybe make the macOS build trigger only on `main` push + PRs
  targeting main, not on every feat-branch push

Tradeoff: less coverage on in-flight branches.

## 10. The trim helper allocates a fresh dict on every call

`_source_quotas_for_trim()` builds a new dict from `_PLATFORM_SOURCE_ORDER`
and merges with `_source_target_counts()`. `_enforce_pool_cap` runs every
~60 seconds; this isn't a hotspot but the allocation is wasteful for
configs that don't change. A `functools.lru_cache` keyed on the
`pool_source_shares` snapshot would be tidy.

Microscopic perf win, but the trim/quotas dicts get passed to SQLite
which does serialize them. Not urgent.

## 11. Round-1 brainstorm items still open

From `docs/brainstorm-2026-05.md`:
- Item 4: mark-as-repost has no undo
- Item 5: backend-endpoint.ts hardcodes http://
- Item 6: popup config UI exposes port but not host
- Item 7: Signed + notarized macOS build path
- Item 8: iOS Safari target
- Item 9: Missing unit tests for share=0 / skip_detection /
  mark_content_as_youtube_repost / yt-replacer/mark-as-repost endpoint
- Item 10: Mark-as-repost expression suffix is verbose
- Item 11: Pre-existing `xhs-task-dispatcher` test failures
- Item 12: Duplicated state objects in launcher vs popup
