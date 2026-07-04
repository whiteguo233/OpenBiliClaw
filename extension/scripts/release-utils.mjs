export function normalizeReleaseVersion(tagOrVersion) {
  if (tagOrVersion.includes("-v")) {
    const [, suffix] = tagOrVersion.split(/-v(.+)/, 2);
    return `v${suffix}`;
  }

  return tagOrVersion.startsWith("v") ? tagOrVersion : `v${tagOrVersion}`;
}

export function makeExtensionArchiveName(tagOrVersion) {
  return `openbiliclaw-extension-${normalizeReleaseVersion(tagOrVersion)}.zip`;
}

export function makeFirefoxSignedXpiName(tagOrVersion) {
  return `openbiliclaw-extension-${normalizeReleaseVersion(tagOrVersion)}-firefox.xpi`;
}
