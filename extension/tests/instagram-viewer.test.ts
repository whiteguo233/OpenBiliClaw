import assert from "node:assert/strict";
import test from "node:test";
import { parseInstagramViewer } from "../src/content/instagram/viewer.ts";

const html = (viewer: unknown) => `<script type="application/json" data-sjs>{"require":[["ScheduledServerJS",null,null,[{"__bbox":{"define":[["PolarisViewer",[],${JSON.stringify(viewer)},123]]}}]]]}</script>`;

test("fresh web viewer requires matching nested identity and account-form username", () => {
  assert.equal(parseInstagramViewer(html({ id: "42", data: { id: "42", username: "fixture" } }), "fixture"), "42");
  assert.equal(parseInstagramViewer(html({ id: "42", data: { id: "43", username: "fixture" } }), "fixture"), "");
  assert.equal(parseInstagramViewer(html({ id: "42", data: { id: "42", username: "other" } }), "fixture"), "");
  assert.equal(parseInstagramViewer(html({ id: "0", data: null }), "fixture"), "");
  assert.equal(parseInstagramViewer('<script data-sjs>{"user":{"id":"42","username":"fixture"}}</script>', "fixture"), "");
  assert.equal(parseInstagramViewer(html({ id: "42", data: { id: "42", username: "fixture" } }), ""), "");
});

test("conflicting viewer modules fail closed", () => {
  assert.equal(parseInstagramViewer(html({ id: "42", data: { id: "42", username: "fixture" } }) + html({ id: "43", data: { id: "43", username: "fixture" } }), "fixture"), "");
});
