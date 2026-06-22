# Config Service Probe Design

## Goal

Add user-triggered connectivity checks for the configured chat LLM provider and
embedding provider in both the desktop Web settings page and the browser
extension settings page.

## Context

`PUT /api/config` currently persists `config.toml` and hot-reloads runtime
components. That path is correct for saving, but it is too heavy for a
"test this value" button because a failed test should not write a bad config or
rebuild background services.

The backend already has the pieces needed for real probes:

- chat providers can be built through `build_llm_registry`;
- a specific chat provider can be called through `LLMRegistry.complete_provider`;
- embedding can be built through `build_embedding_service`;
- `EmbeddingService.probe()` already bypasses cache and hits the provider once.

## Approaches Considered

1. Save first, then reuse `/api/health`.
   This is simple, but it writes invalid values before the user knows whether
   they work, and it can trigger hot-reload rollback noise.

2. Add separate `POST /api/config/probe-service` and apply the submitted form
   config to a temporary in-memory `Config`.
   This lets users test unsaved values, keeps failures local to the button, and
   reuses the same provider construction logic as runtime config.

3. Probe directly from the browser.
   This would expose API keys to more browser-side code, would not work for
   providers that need backend SDKs, and would duplicate provider protocols.

## Decision

Use approach 2.

The new endpoint accepts `kind = "llm" | "embedding"` and a partial config
payload shaped like the existing `PUT /api/config` body. The backend loads the
current config, applies only the submitted LLM / embedding fields to a temporary
copy, and runs one lightweight real request. The endpoint never calls
`save_config`, never snapshots `config.toml`, and never hot-reloads runtime
components.

## API Shape

Request:

```json
{
  "kind": "llm",
  "config": {
    "llm": {
      "default_provider": "deepseek",
      "deepseek": {
        "api_key": "sk-...",
        "model": "deepseek-chat"
      }
    }
  }
}
```

Response:

```json
{
  "ok": true,
  "kind": "llm",
  "provider": "deepseek",
  "model": "deepseek-chat",
  "message": "LLM provider is available.",
  "error": "",
  "latency_ms": 842
}
```

Provider errors return HTTP 200 with `ok: false` so both settings pages can show
a normal inline failure state. Invalid request shapes still use FastAPI's normal
422 validation.

## Probe Behavior

LLM probe:

- uses the selected `llm.default_provider`;
- builds a temporary registry from the submitted config;
- calls exactly that provider with a tiny completion request;
- disables reasoning for the probe call where the provider supports the field;
- succeeds only when the provider returns non-empty content.

Embedding probe:

- uses the independent `[llm.embedding]` config from the submitted form;
- treats an empty provider as "not configured";
- builds the dedicated embedding service;
- calls `EmbeddingService.probe()` so cache cannot hide a broken service;
- succeeds only when the provider returns a non-empty vector.

Both probes use a capped timeout so a settings button cannot hang the UI for the
full production LLM timeout.

## UI Behavior

Both PCWeb and the extension settings page add:

- `测试 LLM` in the LLM model section;
- `测试 Embedding` in the embedding section.

Clicking a button collects the current form state, posts it to
`/api/config/probe-service`, disables the clicked button while the request is in
flight, and renders an inline status message. The probe does not auto-save
successful values; users still press the existing save button when satisfied.

## Tests

Coverage should prove:

- successful LLM probe uses the submitted unsaved provider config;
- failed LLM probe returns `ok: false` without writing config;
- successful embedding probe calls the provider, not the embedding cache;
- disabled or unbuildable embedding returns `ok: false`;
- extension API sends the expected route and payload;
- extension settings page exposes and wires both buttons;
- desktop Web settings page exposes and wires both buttons.

## Documentation

Update the config/API docs and extension docs to describe the new probe endpoint
and settings buttons. Add a changelog item for the user-visible diagnostics
feature.
