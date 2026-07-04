# Init Chunk Cognition Context Design

## Goal

During first-run init, each preference-analysis chunk should also extract lightweight awareness and insight candidates. The merged candidates should enrich the immediately following `soul.profile_build` call, but should not be persisted as long-term `awareness` or `insight` records.

## Architecture

`PreferenceAnalyzer` already runs every init event chunk through a structured LLM call and folds each partial preference into one merged preference object. Extend that same structured response with optional `awareness_candidates` and `insight_candidates`. The analyzer will merge and deduplicate those candidates into a private `_init_cognition_context` key on the returned preference dict.

`SoulEngine.build_initial_profile()` will consume `_init_cognition_context` as ephemeral prompt context. It will append the candidates to the existing persisted awareness/insight lists passed to `ProfileBuilder.build()`, without saving them into the `awareness` or `insight` memory layers. The persisted `preference` layer will strip the private key before writing `preference.json`.

## Data Flow

1. `run_guided_init()` calls `soul_engine.analyze_events(events, event_chunk_size=200)`.
2. Each chunk LLM response may include:
   - `awareness_candidates`: objects with `observation`, optional `trend`, optional `emotion_guess`.
   - `insight_candidates`: objects with `hypothesis`, optional `evidence`, optional `confidence`.
3. `PreferenceAnalyzer._analyze_events_chunked()` folds normal preference fields as before and separately merges candidate lists.
4. `SoulEngine.analyze_events()` persists the normal preference fields and stores the private context in memory for the next profile build.
5. `SoulEngine.build_initial_profile()` combines persisted awareness/insight with the ephemeral init candidates for this profile-build prompt only.

## Error Handling

Candidate fields are optional. Existing providers or tests returning old schema continue to work. Invalid candidate rows are ignored. Duplicate awareness observations and duplicate insight hypotheses are removed by normalized text.

## Tests

Add focused tests for:

- chunked preference analysis merges awareness/insight candidates from multiple chunks;
- `SoulEngine.analyze_events()` strips `_init_cognition_context` from persisted preference while retaining it in process;
- `build_initial_profile()` passes ephemeral candidates into `ProfileBuilder` and does not write them into `awareness.json` or `insight.json`.
