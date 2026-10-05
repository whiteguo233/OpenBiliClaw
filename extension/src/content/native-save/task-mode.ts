interface NativeSaveTaskTabResponse {
  native_save_task_tab?: unknown;
}

export type NativeSaveTaskTabQuery = () => Promise<unknown>;

/** Keep passive behavior collection out of runner-owned and background-task tabs. */
export async function shouldStartPassiveCollector(
  query: NativeSaveTaskTabQuery = () => chrome.runtime.sendMessage({
    type: "NATIVE_SAVE_TASK_TAB_QUERY",
  }),
): Promise<boolean> {
  try {
    const response = await query() as NativeSaveTaskTabResponse | undefined;
    return response?.native_save_task_tab !== true;
  } catch {
    // Ordinary user tabs must keep collecting if the worker is briefly restarting.
    return true;
  }
}
