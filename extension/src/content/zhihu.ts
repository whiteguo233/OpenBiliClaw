/**
 * Zhihu content script entry point.
 * Bundled as dist/content/zhihu.js and injected into zhihu.com pages.
 */

import { startCollector } from "./kernel.js";
import { installZhihuMessageListener } from "./zhihu/task-executor.js";
import { isZhihuTaskTabLocation } from "./zhihu/task-mode.js";
import { zhihuAdapter } from "../shared/platforms/zhihu.js";
import { installNativeSaveExecutor } from "./native-save/runtime.ts";
import { shouldStartPassiveCollector } from "./native-save/task-mode.ts";
import { saveZhihu, verifyZhihu } from "./native-save/zhihu.ts";

installZhihuMessageListener();
installNativeSaveExecutor("zhihu", saveZhihu, verifyZhihu);
if (!isZhihuTaskTabLocation()) {
  void shouldStartPassiveCollector().then((shouldStart) => {
    if (shouldStart) startCollector(zhihuAdapter);
  });
}
