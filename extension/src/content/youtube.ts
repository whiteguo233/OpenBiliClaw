/**
 * YouTube content script entry point.
 * Bundled as dist/content/youtube.js and injected into youtube.com pages.
 */
import { installYtMessageListener } from "./yt/task-executor.js";
import { startCollector } from "./kernel.js";
import { youtubeAdapter } from "../shared/platforms/youtube.js";

startCollector(youtubeAdapter);
installYtMessageListener();
