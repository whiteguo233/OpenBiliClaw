import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("chat tab layout pins a compact composer below a flexible history pane", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const viewBlock = popupHtml.match(/\.view\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const chatShellBlock = popupHtml.match(/\.chat-shell\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const chatMessagesBlock = popupHtml.match(/\.chat-messages\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const chatFormBlock = popupHtml.match(/\.chat-form\s*\{[\s\S]*?\}/)?.[0] ?? "";

  assert.match(viewBlock, /flex:\s*1;/);
  assert.match(chatShellBlock, /flex:\s*1;/);
  assert.match(chatShellBlock, /overflow:\s*hidden;/);
  assert.match(chatMessagesBlock, /flex:\s*1;/);
  assert.match(chatMessagesBlock, /overflow-y:\s*auto;/);
  assert.doesNotMatch(chatMessagesBlock, /max-height:/);
  assert.match(chatFormBlock, /margin-top:\s*auto;/);
  assert.match(chatFormBlock, /flex-shrink:\s*0;/);
});

test("chat composer stays compact so short side panels keep room for messages", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const chatFormBlock = popupHtml.match(/\.chat-form\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const chatInputShellBlock = popupHtml.match(/\.chat-input-shell\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const chatInputBlocks = [...popupHtml.matchAll(/\.chat-input\s*\{[\s\S]*?\}/g)].map((match) => match[0]);
  const chatInputBlock = chatInputBlocks.at(-1) ?? "";
  const chatStatusEmptyBlock = popupHtml.match(/\.chat-status:empty\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const chatStatusFilledBlock =
    popupHtml.match(/\.chat-status:not\(:empty\)\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const chatMarkup = popupHtml.match(/<form id="chatForm"[\s\S]*?<\/form>/)?.[0] ?? "";

  assert.match(chatFormBlock, /gap:\s*8px;/);
  assert.match(chatFormBlock, /padding-top:\s*8px;/);
  assert.match(chatInputShellBlock, /gap:\s*6px;/);
  assert.match(chatInputShellBlock, /padding:\s*10px\s+12px;/);
  assert.match(chatInputBlock, /min-height:\s*44px;/);
  assert.match(chatInputBlock, /max-height:\s*88px;/);
  assert.match(chatStatusEmptyBlock, /display:\s*none;/);
  assert.match(chatStatusFilledBlock, /min-height:\s*16px;/);
  assert.match(chatMarkup, /<textarea id="chatInput" class="chat-input" rows="1"/);
});

test("chat textarea keeps inner spacing and readable line height", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const chatInputBlocks = [...popupHtml.matchAll(/\.chat-input\s*\{[\s\S]*?\}/g)].map((match) => match[0]);
  const chatInputBlock = chatInputBlocks.at(-1) ?? "";

  assert.match(chatInputBlock, /padding:\s*10px\s+12px;/);
  assert.match(chatInputBlock, /line-height:\s*1\.6;/);
  assert.match(chatInputBlock, /border-radius:\s*14px;/);
});

test("chat input placeholder rotation array and timer are defined", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupJs, /CHAT_PLACEHOLDERS\s*=\s*\[/);
  // At least 4 distinct placeholder hints
  const matches = popupJs.match(/比如：/g) ?? [];
  assert.ok(matches.length >= 4, `expected >=4 placeholder hints, got ${matches.length}`);
  assert.match(popupJs, /chatPlaceholderTimer/);
  assert.match(popupJs, /rotatePlaceholder/);
});

test("chat form reserves a dedicated status line for staged progress", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const chatStatusBlock = popupHtml.match(/\.chat-status\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const chatMarkup = popupHtml.match(/<form id="chatForm"[\s\S]*?<\/form>/)?.[0] ?? "";

  assert.match(chatStatusBlock, /font-size:\s*11px;/);
  assert.match(chatMarkup, /id="chatStatus"/);
});
