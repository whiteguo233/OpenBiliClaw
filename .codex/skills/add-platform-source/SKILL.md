---
name: add-platform-source
description: Add, repair, or validate an OpenBiliClaw platform source across fetching, account signals, discovery, and user surfaces. Use for source-specific integration work and its acceptance; exclude unrelated UI, recommendation-algorithm, or release-only work.
---

# Add Platform Source

Deliver the requested source capability through the actual product path. Use `docs/platform-source-integration.md` as the shared guide and `docs/platform-source-history-lessons.md` to find failure precedents. Paths below are relative to the repository root. Historical sessions and plans are evidence, not current instructions or proof that a missing capability is still missing.

## Select the scope and reading

Declare `full`, `discovery-only`, `capability-increment`, or `audit-only`. Plain “add a source” means `full`; a repair stays within the affected capability. Research/audit does not authorize implementation. Updating this skill requires documentation and skill validation, not a new platform contract or live account E2E.

Read the guide's execution protocol and completion criteria, then select the relevant sections:

| Work | Guide sections and useful precedents |
| --- | --- |
| New source | §0 contract, §1 upstream spike, registration map; select the remaining sections by capability |
| Auth / identity / settings | §0.1–0.7, §4–5; Bangumi/GitHub optional credentials, V2EX/Linux.do/Weibo capability readiness |
| Browser tasks / account signals | §2–3, §5; staged completion, MV3 recovery, incremental opt-in and task isolation |
| API / Feed / discovery | §1, §6–6.5; GitHub pagination/cooldown, keyword dual-track, shared admission |
| Cards / native save | §7–9; DTO-to-renderer evidence, exact identity and persisted save confirmation |
| Validation / delivery | §8–11; scoped tests, installed artifact provenance, separate E2E chains and documentation |

For full integration, scan the whole guide's capability headings for omissions. For a narrow repair, expand to adjacent sections only when the diff crosses those boundaries; do not reopen unrelated platform implementations.

## Execute with evidence

1. Preserve existing changes and use the repository's worktree rule for implementation. Record the baseline. Before tests or E2E, prove Python imports, CLI, backend config/data roots and any installed extension resolve to the intended checkout; reload alone does not deploy a worktree build.
2. Start with the relevant history-index rows, then inspect first integration and follow-up repairs in `git log --all` and current tests. Read available project sessions/PRs only to fill a concrete evidence gap. Distinguish root user requests from delegated reviews, and merged fixes from in-flight branches. Never copy credentials, private account data, raw transcripts, or historical authorization into the task.
3. For a new source, copy the contract and acceptance templates. For an increment, reuse/update its contract and record the changed gates. Keep applicability (`required` or `N/A`, with evidence) separate from execution (`PASS`, `FAIL`, `NOT_RUN`, or `BLOCKED`). Capability exclusions need executable proof where enforceable; out-of-scope mutations need scope evidence. `deferred` and missing E2E are never `N/A`.
4. Probe the changed upstream path read-only before inventing fixtures. Preserve redacted success, pagination/termination and counterexamples. Anonymous success does not prove authenticated pagination, current-account ownership, private bootstrap, or the installed dispatcher. If one route is unavailable, record that boundary and continue independent work within the requested contract.
5. Implement vertical slices: registry/contract → transport/normalization → event/bootstrap → formal discover/eval → config/status/init → surfaces. Reuse shared backend contracts and choose precedents per capability. Validate behavior at each affected boundary.
6. For source wiring, run the audit with the repository Python 3.11+ interpreter: `PYTHONPATH="$PWD/src" "$SOURCE_SKILL_PYTHON" scripts/audit_platform_source.py --contract <contract> --check --json`. Audit PASS proves registration evidence only. Review omissions against the raw contract, diff and artifacts; use an independent reviewer when requested or when the integration's risk warrants an authorized review, not as a mandatory extra process for every repair.
7. Record commands, exits and evidence levels in an acceptance copy. Only all-required `PASS` earns `complete` for the declared scope. Report `incremental only` for usable work with missing required proof, or `blocked` for work that cannot proceed. An audit/repair being complete does not certify the entire source. Report implemented, tested, merged, installed and released states separately.

## Preserve these boundaries

- **Auth is per capability when needed.** Reuse `SourceCapabilityAuth` and `SourceAuthContract.capabilities` for `capability-specific` sources; check source-specific status/setup/init projections before declaring a prerequisite missing. Backend code owns account resolution. Observed identity is not verified identity; current authoritative failure overrides stale hints. Preserve omitted/clear/masked credential semantics, fingerprint-scoped evidence and 2xx warnings even when disabled.
- **Content and transport evidence stay honest.** Namespaced stable IDs, canonical URLs, typed `author_name`, authoritative publication times and declared engagement availability must survive storage, DTOs and actual renderers. Follow only validated pagination destinations, including observed authenticated variants. Preserve accepted partial rows; no response, challenge HTML, parse failure and rate limit are not affirmative empty.
- **Browser execution is a lifecycle.** Capability/online checks and local mutex precede atomic backend claim. Retain staged first-final-wins results, bounded durable replay and backend scope caps. Prove task isolation through SPA/full navigation and verify installed assets for the browsers claimed by the contract.
- **Periodic sync is opt-in.** `source_incremental_enabled` defaults to false, including old configs without the key. Supporting incremental refresh must not enable it. Keep manual init, formal discovery, periodic bootstrap and native save distinct; test disabled scheduling and queued-task claim cancellation. Foreground task support is not permission to steal focus unattended.
- **Discovery must produce usable supply.** Search sources need both keyword-generation tracks, `KeywordFetchCoordinator.claim(<slug>)`, keyword identity/rollback, shared admission and shared cooldown across formal/inspiration paths. Show fetched → retained → evaluated → admitted counts; scheduler activity alone is insufficient.
- **E2E proves one chain at a time.** Separate public transport, current-account → event → init/profile, discover → real configured LLM/embedding → recommendation, and installed UI evidence. Static markup/build success is not rendered UI or account E2E. Use isolated storage, check all declared smoke sinks and ordinary event deltas, and preserve the user's configured provider/model/route.
- **Side effects follow the current request.** Carry forward authorization already given in this task; do not ask again at each gate or import permission from historical sessions. Upstream writes, production service changes and publication follow their actual scope. An uncertain native-save result permits read-only persisted-state verification, not another toggle. Complete independent authorized work before reporting a remaining external prerequisite.
