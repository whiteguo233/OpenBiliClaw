const handledProbeKeys = new Set();

function normalizeText(value) {
  return typeof value === "string" ? value.trim() : "";
}

export function normalizeProbeType(type) {
  return normalizeText(type) === "avoidance.probe" ? "avoidance.probe" : "interest.probe";
}

export function probeNotificationKey(type, domain) {
  const normalizedDomain = normalizeText(domain).toLowerCase();
  if (!normalizedDomain) {
    return "";
  }
  return `${normalizeProbeType(type)}:${normalizedDomain}`;
}

export function rememberHandledProbe(domain, type = "interest.probe") {
  const key = probeNotificationKey(type, domain);
  if (key) {
    handledProbeKeys.add(key);
  }
  return key;
}

export function forgetHandledProbe(domain, type = "interest.probe") {
  const key = probeNotificationKey(type, domain);
  if (key) {
    handledProbeKeys.delete(key);
  }
}

export function isProbeHandled(domain, type = "interest.probe") {
  const key = probeNotificationKey(type, domain);
  return Boolean(key && handledProbeKeys.has(key));
}

export function shouldHydrateProbe(item, type = "interest.probe") {
  const domain = normalizeText(item?.domain || item?.title);
  if (!domain || isProbeHandled(domain, type)) {
    return false;
  }
  const status = normalizeText(item?.status).toLowerCase() || "active";
  return status === "active" || status === "pending";
}

export function shouldDisplayProbeFromWebSocket(event, type = event?.type || "interest.probe") {
  return shouldHydrateProbe({ domain: event?.domain, status: "active" }, normalizeProbeType(type));
}

export function filterVisibleProbes(items, type = "interest.probe") {
  return Array.isArray(items)
    ? items.filter((item) => shouldHydrateProbe(item, type))
    : [];
}

export function removeProbeFromNotifications(notifications, domain, type = "interest.probe") {
  const key = probeNotificationKey(type, domain);
  if (!key || !Array.isArray(notifications)) {
    return [];
  }
  return notifications.filter((item) => probeNotificationKey(item?.type, item?.domain || item?.title) !== key);
}

export function mergeProbeNotifications(persisted, current) {
  const merged = [];
  const seen = new Set();
  for (const item of Array.isArray(persisted) ? persisted : []) {
    const type = normalizeProbeType(item?.type);
    if (!shouldHydrateProbe(item, type)) {
      continue;
    }
    const key = probeNotificationKey(type, item.domain || item.title);
    if (!key || seen.has(key)) {
      continue;
    }
    seen.add(key);
    merged.push({ ...item, type });
  }
  for (const item of Array.isArray(current) ? current : []) {
    const type = normalizeProbeType(item?.type);
    if (!shouldHydrateProbe(item, type)) {
      continue;
    }
    const key = probeNotificationKey(type, item.domain || item.title);
    if (!key || seen.has(key)) {
      continue;
    }
    seen.add(key);
    merged.push({ ...item, type });
  }
  return merged;
}

export function resetHandledProbesForTests() {
  handledProbeKeys.clear();
}
