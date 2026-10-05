/**
 * YouTube content script entry point.
 * Bundled as dist/content/youtube.js and injected into youtube.com pages.
 */
import { installYtMessageListener } from "./yt/task-executor.js";
import { startCollector } from "./kernel.js";
import { youtubeAdapter } from "../shared/platforms/youtube.js";
import { installNativeSaveExecutor } from "./native-save/runtime.ts";
import { shouldStartPassiveCollector } from "./native-save/task-mode.ts";
import { saveYouTube, verifyYouTube } from "./native-save/youtube.ts";

installYtMessageListener();
installNativeSaveExecutor("youtube", saveYouTube, verifyYouTube);
void shouldStartPassiveCollector().then((shouldStart) => {
  if (shouldStart) startCollector(youtubeAdapter);
});
