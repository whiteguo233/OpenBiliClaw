# Submission Progress Visibility Design

**Problem**

The popup currently shows a single static `发送中...` state for both chat messages and recommendation feedback. When the backend or model response is slow, users cannot tell whether the request is still in flight, the UI is syncing profile data, or background recommendation refresh is still running.

**Scope**

Limit this change to the extension popup. Reuse existing backend behavior and the existing runtime stream instead of redesigning the API.

**Recommended Approach**

1. Add explicit front-end progress stages for chat and feedback.
2. Keep stage messages local and lightweight:
   - Chat: waiting for reply, syncing profile, syncing recent activity, done.
   - Feedback: submitting feedback, accepted, syncing profile, syncing recent activity.
3. Reuse runtime stream events only for feedback background refresh, so the status line can surface `refresh.started`, `refresh.strategy`, `refresh.pool_updated`, and `refresh.failed`.

**Why This Approach**

This is the smallest change that gives users real progress signals without introducing new backend streaming endpoints. It also keeps chat and feedback aligned on one status model while preserving current request flows.

**Validation**

1. Helper tests for stage copy and runtime event mapping.
2. Targeted popup tests for chat status line markup.
3. Manual browser verification:
   - Chat send shows multiple stages instead of a single static state.
   - Feedback status line updates immediately and reacts to runtime refresh events when available.
