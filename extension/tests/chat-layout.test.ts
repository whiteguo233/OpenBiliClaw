import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("chat tab layout keeps chat shell and message list from collapsing", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");

  assert.match(popupHtml, /\.chat-shell\s*\{[\s\S]*?flex-shrink:\s*0;/);
  assert.match(popupHtml, /\.chat-messages\s*\{[\s\S]*?min-height:\s*72px;/);
  assert.match(popupHtml, /\.chat-messages\s*\{[\s\S]*?max-height:\s*clamp\(220px,\s*45vh,\s*420px\);/);
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

  assert.match(chatStatusBlock, /min-height:\s*16px;/);
  assert.match(chatStatusBlock, /font-size:\s*11px;/);
  assert.match(chatMarkup, /id="chatStatus"/);
});
