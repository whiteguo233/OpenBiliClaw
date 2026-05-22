# LM Studio JSON Schema Fix Design

## Goal

Fix [#12](https://github.com/whiteguo233/OpenBiliClaw/issues/12): when users configure LM Studio through an OpenAI-compatible endpoint, structured LLM calls must not fail with `response_format.type must be json_schema or text`, and the final error must not mislead users into thinking OpenBiliClaw ignored LM Studio and switched to Ollama.

## Problem Statement

Issue #12 reports `openbiliclaw init` reaching the preference-analysis step, then failing on the first structured LLM call:

```text
HTTP Request: POST http://127.0.0.1:1234/v1/chat/completions "HTTP/1.1 400 Bad Request"
...
LLMFallbackError: All providers failed (openai, ollama). Last error: ollama request failed: HTTP 404:
{"message": "model 'qwen2.5:7b' not found"}
```

The user configured LM Studio 0.4.13 on Windows 11, and LM Studio logs showed it received the POST but rejected the request. The confusing `ollama` tail happened only after the primary OpenAI-compatible provider had already failed.

There are two separate observations:

1. **Root failure:** `OpenAIProvider.complete(..., json_mode=True)` always sends `response_format={"type":"json_object"}`. LM Studio's OpenAI-compatible structured-output path rejects that value and accepts `json_schema` / `text`.
2. **Misleading final provider name:** after the primary provider fails, `LLMRegistry.complete()` walks the chat fallback chain. A template-generated config often still contains `[llm.ollama] model = "qwen2.5:7b"`, so the fallback tries Ollama and reports `model not found`. That does not mean the user configuration was ignored; it is a secondary fallback symptom.

## Root Cause

The structured-task flow is:

1. `PreferenceAnalyzer._run_chunk_once()` calls `registry.complete_structured_task(...)`.
2. `LLMService.complete_structured_task()` calls `complete_with_core_memory(..., json_mode=True)`.
3. `LLMRegistry.complete()` invokes the default provider first.
4. `OpenAIProvider.complete()` builds a Chat Completions request and unconditionally adds:

   ```python
   kwargs["response_format"] = {"type": "json_object"}
   ```

That request shape is valid for many OpenAI-compatible services, but not LM Studio 0.4.x. The provider maps the HTTP 400 into `LLMProviderError`, the registry tries the next registered provider, and the final combined error contains the later Ollama 404.

## Chosen Approach

Keep OpenAI `json_object` as the default for existing OpenAI-compatible backends, but add an LM Studio-compatible `json_schema` path in `OpenAIProvider`.

The behavior contract:

- If `json_mode=False`, request body remains unchanged.
- If `json_mode=True` and `base_url` points at LM Studio's default local server (`localhost:1234` / `127.0.0.1:1234` / `::1:1234`), send a generic `json_schema` response format on the first attempt.
- If `json_mode=True` and another compatible service first receives `json_object` but responds with an error saying `response_format.type` only accepts `json_schema` / `text`, retry that same call once with the generic `json_schema` response format.
- Do not change registry fallback semantics in this patch. The misleading Ollama tail disappears for the reported case because LM Studio no longer fails the first request. If another primary-provider error occurs, fallback behavior remains as designed and is documented separately.

## Response Format

Use a permissive schema that matches the existing `json_object` contract: "return some JSON object". The concrete request body:

```python
{
    "type": "json_schema",
    "json_schema": {
        "name": "structured_response",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        },
    },
}
```

This intentionally does not encode every downstream parser's exact object fields. OpenBiliClaw already prompts for task-specific schemas and parses/tolerates provider output in callers such as `PreferenceAnalyzer`, `AwarenessAnalyzer`, and `llm/json_utils.py`. Tight provider-level schemas would require threading task-specific schema metadata through `LLMService`, which is out of scope for this bug fix.

## Detection Rules

`OpenAIProvider` should prefer `json_schema` when:

- `base_url` contains `lmstudio` or `lm-studio`, or
- parsed `hostname` is one of `localhost`, `127.0.0.1`, `::1` and port is `1234`.

This covers LM Studio's default local endpoint (`http://127.0.0.1:1234/v1`) without changing behavior for other local OpenAI-compatible servers such as Ollama (`11434`) or vLLM (`8000`).

The error-triggered fallback should match only provider errors whose text contains:

- `response_format.type`
- `json_schema`
- `text`

This keeps the retry narrow to the exact compatibility failure reported by LM Studio and similar backends.

## Out of Scope

- Adding a new config field for response-format strategy.
- Changing `LLMRegistry` fallback order or disabling Ollama fallback globally.
- Validating the configured LM Studio model name against LM Studio's model list.
- Threading task-specific JSON schemas through every `complete_structured_task()` call.
- Replacing provider retries for all HTTP 4xx errors. A future cleanup can introduce non-retryable bad-request errors, but the reported LM Studio default-port case should avoid the 400 entirely.

## Testing

Add provider-level regression tests in `tests/test_llm_providers.py`:

- LM Studio default base URL with `json_mode=True` sends `response_format.type == "json_schema"` on the first attempt.
- A generic OpenAI-compatible endpoint still starts with `json_object`.
- If that endpoint raises a provider error containing the LM Studio-style allowed-type message, `OpenAIProvider` retries with `json_schema` and returns the successful response.
- Per-call model override tests remain unchanged; the new logic must not mutate provider `_model`.

Run:

```bash
.venv/bin/pytest tests/test_llm_providers.py -q -k "lm_studio_json_mode or schema_when_json_object_rejected"
.venv/bin/pytest tests/test_llm_providers.py -q
.venv/bin/ruff check src/ tests/
.venv/bin/mypy src/
.venv/bin/pytest
```

Expected final verification: full suite passes.

## Documentation

Update:

- `docs/modules/llm.md`: add the LM Studio JSON mode compatibility behavior to the implemented-features table and public API notes.
- `docs/changelog.md`: add a v0.3.75 bullet linking issue #12 and summarizing the `json_schema` fallback.

No architecture diagram update is required because this patch does not change module boundaries, provider registration, data flow, CLI commands, config schema, or dependencies.
