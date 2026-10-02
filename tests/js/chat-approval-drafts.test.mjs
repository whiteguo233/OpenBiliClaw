import assert from "node:assert/strict";
import test from "node:test";
await import("../../src/openbiliclaw/web/shared/agent-chat.js");
const { captureApprovalDrafts, restoreApprovalDrafts } = globalThis.OpenBiliClawAgentChat;

function card(id, { desktop = false, pending = true, editing = false } = {}) {
  const ownerDocument = { activeElement: null };
  const input = { value: "拒绝原因尚在输入", ownerDocument, focus() { ownerDocument.activeElement = this; } };
  const actions = { hidden: editing, after(editor) { result.editor = editor; } };
  const result = {
    dataset: desktop ? { agentApprovalId: id } : { approvalId: id },
    parentElement: null,
    editor: null,
    querySelector(selector) {
      if (selector.includes('action="approve"')) return pending ? {} : null;
      if (selector === ".agent-approval-actions") return actions;
      if (selector === ".agent-approval-reject") return this.editor;
      return null;
    },
  };
  result.editor = {
    hidden: !editing,
    querySelector: () => input,
    replaceWith(editor) { result.editor = editor; },
  };
  return { card: result, input, ownerDocument, actions };
}
function root(cards) { return { querySelectorAll: () => cards.map(item => item.card) }; }

for (const desktop of [false, true]) {
  test(`${desktop ? "desktop" : "mobile/popup"} approval editor preserves draft and focus through rerender`, () => {
    const old = card("ap1", { desktop, editing: true });
    old.ownerDocument.activeElement = old.input;
    const drafts = captureApprovalDrafts(root([old]));
    const fresh = card("ap1", { desktop });
    old.ownerDocument.activeElement = null;
    restoreApprovalDrafts(root([fresh]), drafts);
    assert.equal(fresh.card.editor, old.card.editor);
    assert.equal(fresh.card.editor.hidden, false);
    assert.equal(fresh.card.editor.querySelector("input.agent-approval-reason").value, "拒绝原因尚在输入");
    assert.equal(old.ownerDocument.activeElement, old.input);
    assert.equal(fresh.actions.hidden, true);
  });
}

test("approval terminal updates never restore stale decision inputs", () => {
  const old = card("ap1", { editing: true });
  const fresh = card("ap1", { pending: false });
  const originalEditor = fresh.card.editor;
  restoreApprovalDrafts(root([fresh]), captureApprovalDrafts(root([old])));
  assert.equal(fresh.card.editor, originalEditor);
});
