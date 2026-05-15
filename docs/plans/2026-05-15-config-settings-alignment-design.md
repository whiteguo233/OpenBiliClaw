# Config Settings Alignment Design

## Goal

Align `config.toml`, `/api/config`, and the extension settings page so users can edit the important runtime configuration from the plugin without losing advanced fields on save.

## Current Gaps

- `Config` contains source, scheduler, logging, per-module LLM, and provider-advanced fields that are not all exposed by `/api/config`.
- `save_config()` currently drops several loaded fields during TOML rewrite: scheduler speculation settings, scheduler auto-update settings, and unmanaged log cleanup settings.
- The popup settings page only edits a small subset of the config API and cannot edit `sources.*`, per-module LLM overrides, Bilibili browser settings, storage path, account sync interval, source shares, or log file settings.
- Some popup placeholders and config docs still show older defaults.

## Chosen Approach

Use `Config` as the source of truth and make every existing persisted field round-trip through `save_config()`. Expand `/api/config` to expose and update the practical runtime fields needed by the popup: source budgets, scheduler advanced fields, source shares, log rotation, per-module LLM overrides, OpenRouter headers, DeepSeek reasoning, Bilibili browser settings, and storage/data paths.

The popup will keep the current compact first-screen settings but add advanced sections for lower-frequency controls. This keeps the UI usable while still making the configuration surface complete enough for real administration.

## Data Flow

1. `GET /api/config?reveal_keys=true` returns a typed JSON snapshot of `Config`.
2. `extension/popup/popup.js` populates form fields from that snapshot.
3. The user edits visible fields and saves.
4. `PUT /api/config` applies only supplied fields, writes `config.toml`, hot-reloads runtime components, and returns masked config plus issues.
5. Existing keys and hidden advanced settings survive save/load round-trips.

## Testing

- Python config round-trip test for fields that were previously dropped.
- FastAPI config API tests for exposing and updating advanced config blocks.
- Extension tests verifying `updateConfig()` can send the expanded payload.
- Static popup settings test verifying the expected advanced field IDs exist in `popup.html`.

## Documentation

Update `docs/modules/config.md` and `docs/changelog.md` for the expanded settings API and corrected defaults.
