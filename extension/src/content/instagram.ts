/** Instagram task content-script entry point. Passive pages remain untouched. */

import { installInstagramTaskMessageListener } from "./instagram/task-executor.ts";

installInstagramTaskMessageListener();
