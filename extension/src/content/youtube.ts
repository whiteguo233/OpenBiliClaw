/**
 * YouTube content script entry point.
 * Bundled as dist/content/youtube.js and injected into youtube.com pages.
 */
import { installYtMessageListener } from "./yt/task-executor.js";

installYtMessageListener();
