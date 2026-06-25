# Profile Consolidation Inventory Ceiling Spec

**Created:** 2026-06-24  
**Scope:** `ProfileConsolidator`, preference active-interest inventory, scheduler config, CLI/docs

## Goal

Every applied profile-consolidation run must leave active likes at or below a configured
upper bound whenever that is possible without touching user-protected interests. If merging
duplicates is not enough, the consolidator archives low-value tail interests instead of deleting
them.

## Current State

The current consolidation path is best-effort:

- Background consolidation runs at most once every 12 hours.
- Default likes boundary is top-512 by weight.
- `profile-consolidate --full` can manually open the likes boundary to all tags.
- Similarity clusters are judged by the LLM in batches of 32 clusters.
- `disliked_topics` already has a hard store cap of 128 in `PreferenceAnalyzer`.
- The preference layer has only active `interests`; there is no archived inventory.

This means a profile can stay above the desired active-like ceiling forever when:

- excess likes are distinct low-weight tail items rather than duplicates;
- duplicate clusters live outside top-512;
- input digest is unchanged and `run_if_due()` skips even though active likes are still over cap.

## Requirements

1. **Configurable active-like ceiling**
   - Add scheduler config:
     - `profile_consolidation_like_target_upper`, default `512`
     - `profile_consolidation_like_target_soft`, default `450`
     - `profile_consolidation_archive_enabled`, default `true`
   - Clamp values so upper is at least `1`, soft is at least `1`, and runtime behavior uses
     `min(soft, upper)`.

2. **Over-cap runs must not clean-skip**
   - `run_if_due()` may skip on unchanged input only when current active likes are at or below
     `like_target_upper`.
   - If active likes exceed the upper target, the run must execute even when the digest is unchanged.

3. **Over-cap runs use full active boundary**
   - When active likes exceed the upper target, the run must consider all active likes for clustering.
   - Normal under-cap runs keep the existing top-512 default boundary.

4. **Safe archival after merge**
   - After rule and LLM merges are projected/applied, if active likes still exceed the upper target and
     archiving is enabled, archive low-value tail interests until active likes are at or below
     `min(target_soft, target_upper)`.
   - Archive candidates are sorted by low weight, old `last_seen`, old `first_seen`, then name.
   - Protected interests are never archived:
     - user-added domains in `ProfileOverrides.interest_edits["likes"].add_domains`
     - user-pinned domain weights in `ProfileOverrides.interest_edits["likes"].weight_pins`
     - domains with specific edits in `ProfileOverrides.interest_edits["likes"].specific_edits`
   - If protected inventory alone exceeds the target, report a machine-readable reason instead of
     silently claiming the target was met.

5. **Archived inventory is retained and reversible**
   - Store archived likes under `preference["archived_interests"]`.
   - Archived entries are not used by `OnionProfile.populate_from_flat_preference()` because only
     active `interests` are passed into the tree.
   - Run records already keep a `before` snapshot; extend it to include `archived_interests` so
     `revert(run_id)` restores active and archived inventories.

6. **Archived likes can reactivate**
   - `PreferenceAnalyzer.merge_preferences()` must read existing `archived_interests`.
   - If a new preference item has the same `(name, category)` as an archived item, remove that item
     from archive and merge it back into active interests.
   - Archived interests not reactivated remain archived.

7. **Dry-run and reporting**
   - Dry-run must project archiving without writing.
   - `ConsolidationReport` must expose:
     - `likes_target_upper`
     - `likes_target_soft`
     - `archived_interests`
     - `protected_interests`
     - `inventory_reason`
   - CLI output must show projected archived count and any over-cap reason.

8. **Documentation**
   - Update `docs/modules/soul.md`, `docs/modules/config.md`, `docs/modules/cli.md`, and
     `docs/changelog.md`.

## Non-Goals

- Do not change `disliked_topics` beyond reporting. Its 128 cap already exists.
- Do not change recommendation pool inventory semantics.
- Do not introduce database migrations; the preference JSON layer can carry the new archived list.
- Do not make every normal consolidation run full-boundary; full boundary is only for over-cap or
  explicit `--full`.

## Acceptance Criteria

- [ ] Over-cap `run_if_due()` executes even when digest is unchanged.
- [ ] Over-cap `run_if_due()` sends tail duplicate clusters outside the default boundary.
- [ ] Applied consolidation with archiving enabled leaves active likes at or below the upper target
      when enough unprotected candidates exist.
- [ ] Protected interests are not archived.
- [ ] If the target cannot be met because protected inventory is too large, the report includes
      `inventory_reason="protected_inventory_exceeds_target"`.
- [ ] `revert(run_id)` restores `interests` and `archived_interests`.
- [ ] `PreferenceAnalyzer.merge_preferences()` reactivates matching archived interests.
- [ ] CLI dry-run shows projected archive count.
- [ ] Targeted tests, ruff, and mypy pass for touched code.
