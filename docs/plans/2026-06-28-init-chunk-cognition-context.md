# Init Chunk Cognition Context Implementation Plan

**Goal:** Enrich first-run profile generation by carrying chunk-level awareness and insight candidates from preference analysis into the initial `soul.profile_build` call.

**Architecture:** Extend the existing preference chunk structured-output schema with optional candidate fields, merge them into a private preference key, and consume that key only inside the next profile build. Persisted preference, awareness, and insight layers remain clean.

**Tech Stack:** Python, pytest, existing `PreferenceAnalyzer`, `SoulEngine`, `ProfileBuilder`.

---

### Task 1: Preference Chunk Candidate Merge

**Files:**
- Modify: `tests/test_preference_analyzer.py`
- Modify: `src/openbiliclaw/llm/prompts.py`
- Modify: `src/openbiliclaw/soul/preference_analyzer.py`

**Steps:**
1. Add a failing async test that runs chunked preference analysis with two chunk responses containing `awareness_candidates` and `insight_candidates`.
2. Verify the returned preference includes `_init_cognition_context` with deduplicated `awareness` and `insights`.
3. Update the preference prompt output schema/rules to allow optional chunk-level candidates.
4. Implement parsing and merge helpers in `PreferenceAnalyzer`.
5. Run the focused test.

### Task 2: Ephemeral Context Consumption

**Files:**
- Modify: `tests/test_soul_engine.py`
- Modify: `src/openbiliclaw/soul/engine.py`

**Steps:**
1. Add a failing test proving `analyze_events()` strips `_init_cognition_context` from persisted `preference.json`.
2. Add a failing test proving `build_initial_profile()` passes the ephemeral candidates to the profile builder and does not persist them in `awareness.json` / `insight.json`.
3. Implement an in-process `_init_cognition_context` slot on `SoulEngine`.
4. Convert candidate dicts to prompt-compatible awareness/insight dicts at profile-build time.
5. Clear the slot after it is consumed.

### Task 3: Documentation And Verification

**Files:**
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/init.md`
- Modify: `docs/changelog.md`

**Steps:**
1. Document that init preference chunks now emit ephemeral cognition context for profile build.
2. Run focused tests:
   - `pytest tests/test_preference_analyzer.py -q -k init_cognition`
   - `pytest tests/test_soul_engine.py -q -k init_cognition`
3. Run broader checks:
   - `pytest tests/test_preference_analyzer.py tests/test_soul_engine.py -q`
   - `ruff check src/ tests/`
