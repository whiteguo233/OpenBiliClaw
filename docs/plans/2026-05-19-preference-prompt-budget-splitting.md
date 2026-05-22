# Preference Prompt Budget Splitting Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `PreferenceAnalyzer` split or compact oversized event chunks before LLM calls so preference analysis no longer fails with context-window errors such as `n_keep >= n_ctx`.

**Architecture:** Keep the existing count-based `event_chunk_size` as the first-pass splitter, then add a prompt-size guard inside `PreferenceAnalyzer` before each structured LLM call. Oversized multi-event chunks are recursively bisected; oversized single events are converted to compact prompt-safe events; provider context-overflow errors trigger the same retry path. No tokenizer dependency, config field, CLI flag, or provider API change is required.

**Tech Stack:** Python dataclasses, pytest/pytest-asyncio, existing `LLMProviderError` / `LLMServiceError` wrappers, Ruff, MyPy.

---

### Task 1: Add Prompt-Budget Split Regression Test

**Files:**
- Test: `tests/test_preference_analyzer.py`

**Step 1: Write the failing test**

Add a fake service that fails if any prompt exceeds a caller-supplied char budget:

```python
class BudgetCapturingStructuredService:
    def __init__(self, max_prompt_chars: int) -> None:
        self.max_prompt_chars = max_prompt_chars
        self.calls: list[dict[str, str]] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
    ) -> LLMResponse:
        self.calls.append(
            {"system_instruction": system_instruction, "user_input": user_input}
        )
        assert len(system_instruction) + len(user_input) <= self.max_prompt_chars
        return LLMResponse(
            content='{"interests": [{"name": "科技", "category": "知识", "weight": 0.7}]}',
            provider="openai",
        )
```

Then add:

```python
@pytest.mark.asyncio
async def test_chunked_analysis_splits_by_prompt_budget_before_llm_call() -> None:
    from openbiliclaw.llm.prompts import build_preference_analysis_prompt
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    base_messages = build_preference_analysis_prompt(events=[], existing_preference={})
    budget = len(base_messages[0]["content"]) + 1800
    service = BudgetCapturingStructuredService(max_prompt_chars=budget)
    analyzer = PreferenceAnalyzer(service, max_prompt_chars=budget)

    events = [
        {
            "event_type": "view",
            "title": f"长事件 {idx}",
            "context": "这是一段偏好上下文" * 80,
            "metadata": {"source_platform": "bilibili", "bvid": f"BV{idx}"},
        }
        for idx in range(4)
    ]

    preference = await analyzer.analyze_events(
        events=events,
        existing_preference={},
        event_chunk_size=4,
    )

    assert preference["interests"][0]["name"] == "科技"
    assert len(service.calls) > 1
    assert all(
        len(call["system_instruction"]) + len(call["user_input"]) <= budget
        for call in service.calls
    )
```

Add an entry-path regression proving budget protection is not limited to callers that pass `event_chunk_size`:

```python
@pytest.mark.asyncio
async def test_analyze_events_uses_budget_splitting_without_explicit_chunk_size() -> None:
    from openbiliclaw.llm.prompts import build_preference_analysis_prompt
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    base_messages = build_preference_analysis_prompt(events=[], existing_preference={})
    budget = len(base_messages[0]["content"]) + 1800
    service = BudgetCapturingStructuredService(max_prompt_chars=budget)
    analyzer = PreferenceAnalyzer(service, max_prompt_chars=budget)

    events = [
        {
            "event_type": "view",
            "title": f"自动分片 {idx}",
            "context": "这是一段偏好上下文" * 80,
            "metadata": {"source_platform": "bilibili", "bvid": f"BV_AUTO_{idx}"},
        }
        for idx in range(4)
    ]

    await analyzer.analyze_events(events=events, existing_preference={})

    assert len(service.calls) > 1
    assert all(
        len(call["system_instruction"]) + len(call["user_input"]) <= budget
        for call in service.calls
    )
```

**Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_preference_analyzer.py -q -k "splits_by_prompt_budget"
```

Expected: FAIL because current code sends the whole 4-event chunk to the fake service, and the no-explicit-chunk-size path always calls `_analyze_events_single()`.

**Step 3: Commit**

Do not commit yet. Continue to Task 2 so the red-green commit includes implementation.

---

### Task 2: Implement Local Prompt-Budget Splitting

**Files:**
- Modify: `src/openbiliclaw/soul/preference_analyzer.py`
- Test: `tests/test_preference_analyzer.py`

**Step 1: Add dataclass fields and helpers**

In `PreferenceAnalyzer`, add conservative defaults:

```python
    max_prompt_chars: int = 24_000
    compact_title_chars: int = 180
    compact_context_chars: int = 600
    compact_metadata_value_chars: int = 300
```

Add helpers near `_analyze_events_chunked()`:

```python
    def _prompt_char_count(self, messages: list[dict[str, str]]) -> int:
        return sum(len(message.get("content", "")) for message in messages)

    def _prompt_fits_budget(self, messages: list[dict[str, str]]) -> bool:
        return self.max_prompt_chars <= 0 or self._prompt_char_count(messages) <= self.max_prompt_chars
```

Use `max_prompt_chars <= 0` as an escape hatch for tests or emergency local debugging only; do not expose it in config.

**Step 2: Split before provider call**

Add an initial chunk-size estimator:

```python
    def _estimate_budget_chunk_size(self, *, event_count: int, prompt_chars: int) -> int:
        if event_count <= 0:
            return 1
        if self.max_prompt_chars <= 0 or prompt_chars <= self.max_prompt_chars:
            return max(1, event_count)
        estimated = event_count * self.max_prompt_chars // max(prompt_chars, 1)
        return max(1, min(event_count, estimated))
```

In `analyze_events()`, route oversized single-path batches into `_analyze_events_chunked()`:

```python
        whole_batch_prompt = build_preference_analysis_prompt(
            events=events,
            existing_preference=existing_preference,
        )
        prompt_chars = self._prompt_char_count(whole_batch_prompt)
        should_chunk_by_count = event_chunk_size > 0 and len(events) > event_chunk_size
        should_chunk_by_budget = self.max_prompt_chars > 0 and prompt_chars > self.max_prompt_chars
        if should_chunk_by_count or should_chunk_by_budget:
            initial_chunk_size = (
                event_chunk_size
                if event_chunk_size > 0
                else self._estimate_budget_chunk_size(
                    event_count=len(events),
                    prompt_chars=prompt_chars,
                )
            )
            return await self._analyze_events_chunked(
                events=events,
                existing_preference=existing_preference,
                chunk_size=initial_chunk_size,
            )
```

Inside `_run_chunk_resilient()`, build the chunk prompt before `_run_chunk_once()`:

```python
messages = build_preference_analysis_prompt(events=chunk, existing_preference={})
if not self._prompt_fits_budget(messages):
    if len(chunk) <= 1:
        compact = self._compact_event_for_prompt(chunk[0]) if chunk else {}
        compact_messages = build_preference_analysis_prompt(
            events=[compact],
            existing_preference={},
        )
        if not self._prompt_fits_budget(compact_messages):
            logger.warning(
                "preference event skipped because compact prompt still exceeds budget: title=%r prompt_chars=%d budget=%d",
                str(chunk[0].get("title", "")) if chunk and isinstance(chunk[0], dict) else "",
                self._prompt_char_count(compact_messages),
                self.max_prompt_chars,
            )
            return []
        return [await _run_chunk_once([compact])]
    midpoint = max(1, len(chunk) // 2)
    left, right = await _asyncio.gather(
        _run_chunk_resilient(chunk[:midpoint]),
        _run_chunk_resilient(chunk[midpoint:]),
    )
    return [*left, *right]
```

Keep `_run_chunk_once()` itself as the only function that calls `complete_structured_task()`.

**Step 3: Run focused test**

```bash
.venv/bin/pytest tests/test_preference_analyzer.py -q -k "splits_by_prompt_budget"
```

Expected: both prompt-budget split tests PASS.

**Step 4: Commit**

```bash
git add src/openbiliclaw/soul/preference_analyzer.py tests/test_preference_analyzer.py
git commit -m "fix(soul): split preference chunks by prompt budget"
```

---

### Task 3: Add Single-Event Compaction

**Files:**
- Modify: `src/openbiliclaw/soul/preference_analyzer.py`
- Test: `tests/test_preference_analyzer.py`

**Step 1: Write failing tests**

Add:

```python
@pytest.mark.asyncio
async def test_single_oversized_preference_event_is_compacted_before_llm_call() -> None:
    from openbiliclaw.llm.prompts import build_preference_analysis_prompt
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    base_messages = build_preference_analysis_prompt(events=[], existing_preference={})
    budget = len(base_messages[0]["content"]) + 2200
    service = BudgetCapturingStructuredService(max_prompt_chars=budget)
    analyzer = PreferenceAnalyzer(service, max_prompt_chars=budget)

    await analyzer.analyze_events(
        events=[
            {
                "event_type": "feedback",
                "title": "很长但重要的标题" + "x" * 2000,
                "context": "用户明确点踩了这条内容。" + "y" * 20_000,
                "inferred_satisfaction": "negative",
                "satisfaction_reason": "explicit_negative",
                "metadata": {
                    "source_platform": "bilibili",
                    "up_name": "测试UP",
                    "bvid": "BV_LONG",
                    "feedback_type": "dislike",
                    "raw_context": "z" * 50_000,
                },
            }
        ],
        existing_preference={},
    )

    assert len(service.calls) == 1
    user_input = service.calls[0]["user_input"]
    assert "测试UP" in user_input
    assert "BV_LONG" in user_input
    assert "feedback_type" in user_input
    assert "raw_context" not in user_input
    assert "z" * 1000 not in user_input
```

Add:

```python
@pytest.mark.asyncio
async def test_single_event_is_skipped_when_compact_prompt_still_exceeds_budget() -> None:
    from openbiliclaw.llm.prompts import build_preference_analysis_prompt
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    base_messages = build_preference_analysis_prompt(events=[], existing_preference={})
    budget = len(base_messages[0]["content"]) + 20
    service = BudgetCapturingStructuredService(max_prompt_chars=budget)
    analyzer = PreferenceAnalyzer(service, max_prompt_chars=budget)

    preference = await analyzer.analyze_events(
        events=[{"event_type": "view", "title": "too large", "context": "x" * 10_000}],
        existing_preference={},
    )

    assert service.calls == []
    assert preference["source_platform_mix"] == {"bilibili": 1.0}
```

**Step 2: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_preference_analyzer.py -q -k "oversized_preference_event or compact_prompt"
```

Expected: the first test fails because no compaction helper exists; the second may fail because the current code still calls the fake service.

**Step 3: Implement compaction**

Add module-level metadata allowlist:

```python
_COMPACT_METADATA_KEYS = frozenset(
    {
        "source_platform",
        "up_name",
        "author",
        "bvid",
        "aid",
        "content_id",
        "folder",
        "duration",
        "watch_seconds",
        "video_duration_seconds",
        "feedback_type",
        "reaction",
    }
)
```

Add helpers:

```python
    def _truncate_prompt_text(self, value: object, limit: int) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    def _compact_event_for_prompt(self, event: dict[str, object]) -> dict[str, object]:
        compact: dict[str, object] = {}
        for key in (
            "event_type",
            "type",
            "created_at",
            "inferred_satisfaction",
            "satisfaction_reason",
        ):
            value = event.get(key)
            if value not in (None, ""):
                compact[key] = value

        if event.get("title"):
            compact["title"] = self._truncate_prompt_text(
                event.get("title"),
                self.compact_title_chars,
            )
        if event.get("context"):
            compact["context"] = self._truncate_prompt_text(
                event.get("context"),
                self.compact_context_chars,
            )
        if event.get("url"):
            compact["url"] = self._truncate_prompt_text(event.get("url"), 300)

        metadata = event.get("metadata")
        compact_metadata: dict[str, object] = {}
        if isinstance(metadata, dict):
            for key in sorted(_COMPACT_METADATA_KEYS):
                value = metadata.get(key)
                if value in (None, ""):
                    continue
                if isinstance(value, str):
                    compact_metadata[key] = self._truncate_prompt_text(
                        value,
                        self.compact_metadata_value_chars,
                    )
                elif isinstance(value, int | float | bool):
                    compact_metadata[key] = value
        if compact_metadata:
            compact["metadata"] = compact_metadata
        return compact
```

**Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_preference_analyzer.py -q -k "splits_by_prompt_budget or oversized_preference_event or compact_prompt"
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/preference_analyzer.py tests/test_preference_analyzer.py
git commit -m "fix(soul): compact oversized preference events"
```

---

### Task 4: Recover From Provider Context-Overflow Errors

**Files:**
- Modify: `src/openbiliclaw/soul/preference_analyzer.py`
- Test: `tests/test_preference_analyzer.py`

**Step 1: Write failing tests**

Add a fake service:

```python
class ContextOverflowOnceStructuredService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
    ) -> LLMResponse:
        self.calls.append(user_input)
        if "PAIR_ONLY_OVERFLOWS" in user_input and user_input.count("PAIR_ONLY_OVERFLOWS") > 1:
            raise LLMProviderError(
                "openai request failed: HTTP 400: The number of tokens to keep "
                "from the initial prompt is greater than the context length "
                "(n_keep: 135132 >= n_ctx: 36096)"
            )
        return LLMResponse(
            content='{"interests": [{"name": "科技", "category": "知识", "weight": 0.7}]}',
            provider="openai",
        )
```

Add:

```python
@pytest.mark.asyncio
async def test_provider_context_overflow_splits_chunk_and_retries() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = ContextOverflowOnceStructuredService()
    analyzer = PreferenceAnalyzer(service, max_prompt_chars=0)

    preference = await analyzer.analyze_events(
        events=[
            {"event_type": "view", "title": "PAIR_ONLY_OVERFLOWS A"},
            {"event_type": "view", "title": "PAIR_ONLY_OVERFLOWS B"},
        ],
        existing_preference={},
        event_chunk_size=2,
    )

    assert preference["interests"][0]["name"] == "科技"
    assert len(service.calls) == 3
```

Add a non-context regression:

```python
@pytest.mark.asyncio
async def test_non_context_provider_error_still_aborts_chunked_analysis() -> None:
    from openbiliclaw.soul.preference_analyzer import (
        PreferenceAnalysisError,
        PreferenceAnalyzer,
    )

    analyzer = PreferenceAnalyzer(
        FakeErrorStructuredService(LLMProviderError("provider down")),
        max_prompt_chars=0,
    )

    with pytest.raises(PreferenceAnalysisError, match="provider down"):
        await analyzer.analyze_events(
            events=[{"event_type": "view", "title": "x"}, {"event_type": "view", "title": "y"}],
            existing_preference={},
            event_chunk_size=2,
        )
```

**Step 2: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_preference_analyzer.py -q -k "context_overflow or non_context_provider_error"
```

Expected: first test fails because current `_run_chunk_resilient()` re-raises provider errors with `__cause__`; second should pass or keep passing.

**Step 3: Implement narrow context-overflow detection**

Add:

```python
    @staticmethod
    def _is_context_overflow_error(exc: PreferenceAnalysisError) -> bool:
        text = str(exc).lower()
        markers = (
            "context length",
            "maximum context",
            "n_ctx",
            "n_keep",
            "tokens to keep",
            "prompt is too long",
            "input is too long",
        )
        return any(marker in text for marker in markers)
```

In `_run_chunk_resilient()` change the provider-error branch:

```python
            except PreferenceAnalysisError as exc:
                if exc.__cause__ is not None and self._is_context_overflow_error(exc):
                    logger.warning(
                        "preference chunk exceeded provider context; splitting: events=%d error=%s",
                        len(chunk),
                        exc,
                    )
                    return await _split_or_compact_chunk(chunk)
                if exc.__cause__ is not None:
                    raise
```

Avoid duplicating split/compact logic by extracting the Task 2 logic into a nested `_split_or_compact_chunk()` helper inside `_analyze_events_chunked()`.

**Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_preference_analyzer.py -q -k "context_overflow or non_context_provider_error or splits_by_prompt_budget or oversized_preference_event or compact_prompt"
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/preference_analyzer.py tests/test_preference_analyzer.py
git commit -m "fix(soul): retry preference context overflow with smaller chunks"
```

---

### Task 5: Pass Explicit Chunk Size From Feedback Batch Processing

**Files:**
- Modify: `src/openbiliclaw/soul/engine.py`
- Test: `tests/test_soul_engine.py`

**Step 1: Write or update failing tests**

In `test_process_feedback_batch_updates_preference_after_threshold`, update the fake analyzer signature and assert the direct guard:

```python
    async def fake_analyze_events(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
        event_chunk_size: int = 0,
    ) -> dict[str, object]:
        assert len(events) == 3
        assert event_chunk_size == 200
        return {
            "interests": [
                {"name": "纪录片", "category": "知识", "weight": 0.9, "source": "feedback"}
            ],
            "style": {},
            "context": {},
            "exploration_openness": 0.4,
            "disliked_topics": ["标题党"],
            "favorite_up_users": [],
        }
```

Also update the fake analyzer in `test_process_feedback_batch_rebuilds_profile_when_preference_changes_significantly` to accept `event_chunk_size: int = 0` and assert `event_chunk_size == 200`.

**Step 2: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_soul_engine.py -q -k "process_feedback_batch"
```

Expected: FAIL because `process_feedback_batch_if_needed()` currently calls `analyze_events()` without `event_chunk_size`.

**Step 3: Implement direct feedback-batch chunking**

In `src/openbiliclaw/soul/engine.py`, change:

```python
        updated_preference = await self._preference_analyzer.analyze_events(
            events=feedback_events,
            existing_preference=existing_preference,
        )
```

to:

```python
        updated_preference = await self._preference_analyzer.analyze_events(
            events=feedback_events,
            existing_preference=existing_preference,
            event_chunk_size=200,
        )
```

This is not a replacement for the prompt-budget guard; it just avoids starting feedback batches as a single 500-event chunk when the caller already knows the batch can be large.

**Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_soul_engine.py -q -k "process_feedback_batch"
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/engine.py tests/test_soul_engine.py
git commit -m "fix(soul): chunk feedback preference reanalysis"
```

---

### Task 6: Regression Sweep For Existing Preference Behavior

**Files:**
- Test: `tests/test_preference_analyzer.py`

**Step 1: Run the full preference analyzer test file**

```bash
.venv/bin/pytest tests/test_preference_analyzer.py -q
```

Expected: PASS. Pay attention to the existing test:

- `test_chunked_analysis_splits_and_skips_rejected_single_event`
- satisfaction filter tests
- source platform mix tests
- invalid JSON wrapping tests

**Step 2: Fix any regression minimally**

Only adjust code touched by this plan. Do not refactor merge logic, JSON parsing, event filters, or prompt builders.

**Step 3: Run style and type checks for touched Python files**

```bash
.venv/bin/ruff check src/openbiliclaw/soul/preference_analyzer.py tests/test_preference_analyzer.py
.venv/bin/mypy src/openbiliclaw/soul/preference_analyzer.py
```

Expected: PASS.

**Step 4: Commit if any regression fix was needed**

```bash
git add src/openbiliclaw/soul/preference_analyzer.py tests/test_preference_analyzer.py
git commit -m "test(soul): cover preference prompt budget regressions"
```

Skip this commit if Task 6 required no code changes.

---

### Task 7: Update Soul Documentation And Changelog

**Files:**
- Modify: `docs/modules/soul.md`
- Modify: `docs/changelog.md`

**Step 1: Update `docs/modules/soul.md`**

In the behavior-event path section that currently says initialization uses concurrent chunks and recursively splits bad JSON / refusal chunks, add:

```markdown
偏好分析还会在每次 LLM 调用前检查 prompt 体积。`event_chunk_size` 只是第一层按条数粗分片；如果某个 chunk 的 `system_instruction + user_input` 超过本地保守预算，`PreferenceAnalyzer` 会继续递归二分该 chunk。若单条事件本身过长，会只保留 `event_type/title/context/inferred_satisfaction/satisfaction_reason` 和 `metadata.source_platform/up_name/bvid/feedback_type` 等偏好提取关键字段，截断长文本并丢弃 `raw_context`、字幕、评论、原始 payload 等大字段。compact 后仍超预算的单条事件会被跳过并记录 warning，其他事件继续参与合并。

如果 provider 返回明确的 context-window 错误（例如 `n_keep >= n_ctx`、`context length`、`prompt is too long`），偏好分析会按同一套拆分/compact 逻辑重试；认证、网络、限流、模型不存在等非上下文错误仍会让调用失败。
```

**Step 2: Update `docs/changelog.md`**

At the top current version block, add one bullet:

```markdown
- 偏好分析新增 prompt 预算保护：初始化 / bootstrap 时不再只按事件条数分片，超长 chunk 会在本地继续拆分，单条超长事件会保守 compact，provider 返回 `n_keep >= n_ctx` 等 context-window 错误时会用更小 chunk 重试，避免一个巨大事件批次中断整轮画像初始化。
```

**Step 3: Run docs sanity check**

```bash
rg -n "prompt 预算|n_keep|context-window|PreferenceAnalyzer" docs/modules/soul.md docs/changelog.md
```

Expected: new text is present in both files.

**Step 4: Commit**

```bash
git add docs/modules/soul.md docs/changelog.md
git commit -m "docs(soul): document preference prompt budget splitting"
```

---

### Task 8: Final Verification

**Files:**
- No edits unless verification reveals a real regression.

**Step 1: Run focused tests**

```bash
.venv/bin/pytest tests/test_preference_analyzer.py -q
.venv/bin/pytest tests/test_soul_engine.py -q -k "process_feedback_batch"
```

Expected: PASS.

**Step 2: Run project checks required for this scope**

```bash
.venv/bin/ruff check src/openbiliclaw/soul/preference_analyzer.py tests/test_preference_analyzer.py
.venv/bin/mypy src/openbiliclaw/soul/preference_analyzer.py
```

Expected: PASS.

**Step 3: Optional wider regression**

Run when time allows:

```bash
.venv/bin/pytest tests/test_soul_engine.py tests/test_pipeline_advanced.py -q
```

Expected: PASS. These are broader soul/pipeline tests and may take longer.

**Step 4: Final git check**

```bash
git status --short
```

Expected: only intentional committed changes, plus any pre-existing local files such as `config.toml.bak` that must not be added.
