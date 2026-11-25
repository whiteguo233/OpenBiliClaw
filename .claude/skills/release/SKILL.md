---
name: release
description: Use when releasing a version, bumping project versions, or pushing release tags.
---

# Release OpenBiliClaw

Follow all six steps in order. Stop when any gate fails; never publish a partially verified
release.

## 1. Run pre-flight checks

Require a clean worktree on `main`, pull the latest commit, and run the full test suite:

```bash
git status --short
git branch --show-current
git pull --ff-only
.venv/bin/python -m pytest -q
```

Do not continue until `git status --short` is empty and the branch is `main`.

## 2. Bump mechanical versions

Use the repository tool as the only writer for mechanical version fields:

```bash
.venv/bin/python scripts/release.py --bump X.Y.Z
```

When the extension version changes, include `--extension X.Y.Z`; the flag may also be used
alone for an extension-only release. Never hand-edit mechanical version fields. The changelog
heading and README callout content in the next steps are deliberate hand edits.

## 3. Update the changelog

Hand-edit the top of `docs/changelog.md` with `## vX.Y.Z: theme (YYYY-MM-DD)` and the release's
verified changes. The final `scripts/release.py --check` warns if this heading is missing.

## 4. Replace the README callouts

Hand-edit the 📌 callouts under the recent-updates sections. Follow
[CLAUDE.md rule 10](../../../CLAUDE.md#documentation-requirements); the three hard limits are:

- Use at most four bullets.
- Replace the previous callout; never append another version callout.
- Keep `README.md` and `README_EN.md` in lockstep with the same items and order.

Then verify every mechanical field and both user-facing version headers:

```bash
.venv/bin/python scripts/release.py --check
```

## 5. Verify the signed SDK, then push tags individually

Every Core bump includes a matching five-platform SiliconFlow SDK. First commit/push the
reviewed Core version to a reachable branch **without tags**. Follow the
[SDK release gate](../../../docs/modules/packaging.md#免费模型组件发版门禁) to dispatch
the private build for that exact SHA, finalize the artifacts, and pin their catalog.
Proceed to tags only when `python scripts/verify_release_sdk.py` verifies all five public
archives from the release checkout. A queued/skipped build or billing failure stops here.
Extension-only releases with a different version skip this Core gate.

Review the release-only diff, including the verified pins, and commit any remaining changes:

```bash
git commit -m "chore: release X.Y.Z" -- <release-paths>
```

Create the aggregate `openbiliclaw-vX.Y.Z` tag and the applicable `backend-vX.Y.Z`,
`extension-vX.Y.Z`, and `desktop-vX.Y.Z` channel tags. Push **one tag per `git push`**
invocation. A four-tag push previously emitted no GitHub events, leaving all four release
workflows silently dead; never combine tag refspecs in one push.

```bash
git push origin openbiliclaw-vX.Y.Z
git push origin backend-vX.Y.Z
# Push each other applicable channel tag in its own invocation.
```

## 6. Verify publication

Run `gh run list --limit 10` and confirm that each pushed tag triggered exactly one run of its
corresponding release workflow. Inspect failures before retrying or pushing another tag. After
the container workflow succeeds, confirm the GHCR package is public; new packages may require
their visibility to be changed to public manually.
