# Chat Input UX Design

**Problem**

The `Chat` tab has two interaction issues:

1. The textarea content sits too close to the rounded shell and is visually clipped.
2. The form only submits via the button, so quick keyboard send is missing.

**Scope**

Keep the change local to the popup chat form. Do not redesign the page or alter backend chat behavior.

**Approach**

1. Restore chat textarea inner spacing and line-height so typed text stays clear of the rounded container.
2. Add keyboard submission on `Enter`, while keeping `Shift+Enter` for newline and ignoring IME composition.

**Why This Approach**

This is the smallest change that matches common chat expectations and does not require restructuring the form or the request flow.

**Validation**

1. Static test for chat textarea spacing and keyboard contract.
2. Runtime test for key handling helper.
3. Manual browser check: text is readable while typing, `Enter` sends, `Shift+Enter` inserts newline.
