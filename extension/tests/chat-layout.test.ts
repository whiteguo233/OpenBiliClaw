import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

function cssBlocks(popupHtml: string, selector: string): string[] {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return [...popupHtml.matchAll(new RegExp(`${escaped}\\s*\\{[\\s\\S]*?\\}`, "g"))].map(
    (match) => match[0],
  );
}

function cssBlockWith(popupHtml: string, selector: string, pattern: RegExp): string {
  return cssBlocks(popupHtml, selector).find((block) => pattern.test(block)) ?? "";
}

test("chat tab layout pins a compact composer below a flexible history pane", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const viewBlock = cssBlockWith(popupHtml, ".view", /flex:\s*1;/);
  const chatShellBlock = cssBlockWith(popupHtml, ".chat-shell", /overflow:\s*hidden;/);
  const chatMessagesBlock = cssBlockWith(popupHtml, ".chat-messages", /overflow-y:\s*auto;/);
  const chatFormBlock = cssBlockWith(popupHtml, ".chat-form", /margin-top:\s*auto;/);
  const chatFooterBlock =
    popupHtml.match(/\.shell:has\(#viewChat:not\(\[hidden\]\)\)\s+\.footer\s*\{[\s\S]*?\}/)?.[0] ?? "";

  assert.match(viewBlock, /flex:\s*1;/);
  assert.match(chatShellBlock, /flex:\s*1;/);
  assert.match(chatShellBlock, /overflow:\s*hidden;/);
  assert.match(chatMessagesBlock, /flex:\s*1;/);
  assert.match(chatMessagesBlock, /overflow-y:\s*auto;/);
  assert.doesNotMatch(chatMessagesBlock, /max-height:/);
  assert.match(chatFormBlock, /margin-top:\s*auto;/);
  assert.match(chatFormBlock, /flex-shrink:\s*0;/);
  assert.match(chatFooterBlock, /display:\s*none;/);
});

test("chat composer stays compact so short side panels keep room for messages", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const chatFormBlock = cssBlockWith(popupHtml, ".chat-form", /margin-top:\s*auto;/);
  const chatInputShellBlock = cssBlockWith(popupHtml, ".chat-input-shell", /padding:/);
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
  assert.match(chatInputBlock, /min-height:\s*68px;/);
  assert.match(chatInputBlock, /max-height:\s*88px;/);
  assert.match(chatStatusEmptyBlock, /display:\s*none;/);
  assert.match(chatStatusFilledBlock, /min-height:\s*16px;/);
  assert.match(chatMarkup, /<textarea id="chatInput" class="chat-input" rows="2"/);
});

test("chat textarea keeps inner spacing and readable line height", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const chatInputBlocks = [...popupHtml.matchAll(/\.chat-input\s*\{[\s\S]*?\}/g)].map((match) => match[0]);
  const chatInputBlock = chatInputBlocks.at(-1) ?? "";

  assert.match(chatInputBlock, /padding:\s*8px\s+10px;/);
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

test("chat tab scrolls restored history to the newest message", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const setActiveTabBlock =
    popupJs.match(/function setActiveTab\(tabName\) \{[\s\S]*?\n\}/)?.[0] ?? "";
  const hydrateBlock =
    popupJs.match(/async function hydrateChatHistory\(\) \{[\s\S]*?\n\}/)?.[0] ?? "";

  assert.match(popupJs, /function scrollChatMessagesToBottom\(\)/);
  assert.match(popupJs, /requestAnimationFrame/);
  assert.match(setActiveTabBlock, /tabName === "chat"[\s\S]*scrollChatMessagesToBottom\(\)/);
  assert.match(hydrateBlock, /replaceChildren\(\)[\s\S]*scrollChatMessagesToBottom\(\)/);
});
