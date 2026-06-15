/**
 * OpenBiliClaw — Bilibili content script entry.
 *
 * Injected into bilibili.com pages. Wires the generic collector
 * kernel to the bilibili-specific platform adapter.
 */

import { startCollector } from "./kernel.js";
import { installBiliMessageListener } from "./bili/task-executor.js";
import { bilibiliAdapter } from "../shared/platforms/bilibili.js";

startCollector(bilibiliAdapter);
installBiliMessageListener();

console.log(
  "[OpenBiliClaw] Bilibili behavior collector initialized on",
  bilibiliAdapter.detectPageType(window.location.href),
  "page",
);
