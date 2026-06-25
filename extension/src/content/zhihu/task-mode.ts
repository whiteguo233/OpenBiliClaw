export const ZHIHU_TASK_TAB_PARAM = "openbiliclaw_zhihu_task";
export const ZHIHU_TASK_TAB_URL = `https://www.zhihu.com/#${ZHIHU_TASK_TAB_PARAM}=1`;

export interface LocationLike {
  hash?: string;
  search?: string;
}

function hasTaskParam(paramsText: string | undefined): boolean {
  if (!paramsText) return false;
  const normalized = paramsText.startsWith("#") || paramsText.startsWith("?")
    ? paramsText.slice(1)
    : paramsText;
  if (!normalized) return false;
  return new URLSearchParams(normalized).has(ZHIHU_TASK_TAB_PARAM);
}

export function isZhihuTaskTabLocation(locationLike: LocationLike | undefined = globalThis.location): boolean {
  return hasTaskParam(locationLike?.hash) || hasTaskParam(locationLike?.search);
}
