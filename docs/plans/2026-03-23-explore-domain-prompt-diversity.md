# Explore Domain Prompt Diversity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Tighten the explore-domain prompt so cross-domain extrapolation covers multiple directions instead of repeating one adjacent theme.

**Architecture:** Keep the behavior change prompt-only. Update the explore-domain system prompt with direction-coverage and anti-rephrasing rules, lock the new wording with a prompt unit test, then update the discovery docs and changelog to reflect the new behavior.

**Tech Stack:** Python, pytest, Ruff, Markdown docs

---

### Task 1: Lock the prompt contract with a failing test

**Files:**
- Modify: `tests/test_llm_prompts.py`
- Reference: `src/openbiliclaw/llm/prompts.py`

**Step 1: Write the failing test**

Add a test that builds `build_explore_domains_prompt()` and asserts the system prompt mentions:

- at least 3 different content directions
- only one domain per shared parent theme
- `why_it_might_resonate` should explain the user's cognitive need

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_llm_prompts.py -k explore_domains_prompt -q`

Expected: FAIL because the existing prompt does not contain the new wording.

**Step 3: Write minimal implementation**

Update `build_explore_domains_prompt()` in `src/openbiliclaw/llm/prompts.py` to include the new diversity rules without changing output schema.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_llm_prompts.py -k explore_domains_prompt -q`

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_llm_prompts.py src/openbiliclaw/llm/prompts.py
git commit -m "fix: diversify explore domain prompt"
```

### Task 2: Update discovery docs

**Files:**
- Modify: `docs/modules/discovery.md`
- Modify: `docs/changelog.md`

**Step 1: Write the doc change**

Document that `ExploreStrategy` prompt generation now explicitly requires multi-direction domain coverage and rejects same-theme paraphrase clusters.

**Step 2: Verify docs render cleanly**

Run: `git diff --check -- docs/modules/discovery.md docs/changelog.md`

Expected: no output

**Step 3: Commit**

```bash
git add docs/modules/discovery.md docs/changelog.md
git commit -m "docs: record explore prompt diversity rules"
```

### Task 3: Final verification

**Files:**
- Verify: `tests/test_llm_prompts.py`
- Verify: `src/openbiliclaw/llm/prompts.py`
- Verify: `docs/modules/discovery.md`
- Verify: `docs/changelog.md`

**Step 1: Run targeted tests**

Run: `./.venv/bin/pytest tests/test_llm_prompts.py -q`

Expected: PASS

**Step 2: Run lint on touched Python files**

Run: `./.venv/bin/ruff check src/openbiliclaw/llm/prompts.py tests/test_llm_prompts.py`

Expected: PASS

**Step 3: Check patch formatting**

Run: `git diff --check -- src/openbiliclaw/llm/prompts.py tests/test_llm_prompts.py docs/modules/discovery.md docs/changelog.md`

Expected: no output
