#!/usr/bin/env bash
set -euo pipefail

repo="${GITHUB_REPOSITORY:-whiteguo233/OpenBiliClaw}"
channel="${CHANNEL:-manual}"
release_tag="${RELEASE_TAG:-${GITHUB_REF_NAME:-}}"

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is required to sync the aggregate release" >&2
  exit 1
fi

project_version="$(
  python3 - <<'PY'
import tomllib
from pathlib import Path

pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
print(pyproject["project"]["version"])
PY
)"

aggregate_tag="${AGGREGATE_TAG:-openbiliclaw-v${project_version}}"
backend_tag="backend-v${project_version}"
title="OpenBiliClaw v${project_version}"
notes_file="$(mktemp)"
download_dir="$(mktemp -d)"
trap 'rm -f "$notes_file"; rm -rf "$download_dir"' EXIT

release_with_project_version() {
  local prefix="$1"
  local expected_tag="${prefix}${project_version}"

  if [ -n "$release_tag" ] && [ "$release_tag" = "$expected_tag" ]; then
    printf '%s\n' "$release_tag"
    return
  fi

  if gh release view "$expected_tag" --repo "$repo" >/dev/null 2>&1; then
    printf '%s\n' "$expected_tag"
  fi
}

extension_tag="$(release_with_project_version "extension-v")"
desktop_tag="$(release_with_project_version "desktop-v")"

extension_line="Not published yet."
chrome_extension_asset_line="not available yet for this version"
firefox_signed_asset_line="no signed XPI in this release — load the temporary zip below instead"
firefox_dev_asset_line="not available yet for this version"
safari_extension_asset_line="not available yet for this version"
if [ -n "$extension_tag" ]; then
  extension_version="${extension_tag#extension-v}"
  extension_line="[${extension_tag}](https://github.com/${repo}/releases/tag/${extension_tag})"
  chrome_extension_asset_line="use \`openbiliclaw-extension-v${extension_version}.zip\`"
  firefox_dev_asset_line="use \`openbiliclaw-extension-v${extension_version}-firefox.zip\` via \`about:debugging\`"
fi

desktop_line="Not published yet."
desktop_note=""
if [ -n "$desktop_tag" ]; then
  desktop_line="[${desktop_tag}](https://github.com/${repo}/releases/tag/${desktop_tag})"
fi

# Docker channel: report the GHCR images only when this exact version's
# manifest is actually pullable (same "no backfill" rule as the other
# channels). Anonymous registry check; any failure degrades to
# "Not published yet." without breaking the sync. Both the backend image AND
# the bundled-bge-m3 Ollama image must be present — the prebuilt compose needs
# both, so half a release must not read as "Docker ready".
docker_image_owner="$(printf '%s' "${repo%%/*}" | tr '[:upper:]' '[:lower:]')"
docker_image="ghcr.io/${docker_image_owner}/openbiliclaw-backend"
docker_ollama_image="ghcr.io/${docker_image_owner}/openbiliclaw-ollama"
docker_line="Not published yet."
docker_download_line=""

_ghcr_manifest_pullable() {
  # $1 = ghcr.io/owner/name ; checks the ${project_version} manifest anonymously
  local image="$1" token
  token="$(
    curl -fsSL "https://ghcr.io/token?scope=repository:${image#ghcr.io/}:pull" 2>/dev/null \
      | python3 -c 'import sys, json; print(json.load(sys.stdin).get("token", ""))' 2>/dev/null \
      || true
  )"
  [ -n "$token" ] || return 1
  curl -fsSL -o /dev/null \
    -H "Authorization: Bearer ${token}" \
    -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json" \
    "https://ghcr.io/v2/${image#ghcr.io/}/manifests/${project_version}" 2>/dev/null
}

if _ghcr_manifest_pullable "$docker_image" && _ghcr_manifest_pullable "$docker_ollama_image"; then
  docker_line="[\`${docker_image}:${project_version}\`](https://github.com/${repo}/pkgs/container/openbiliclaw-backend) + [\`${docker_ollama_image}:${project_version}\`](https://github.com/${repo}/pkgs/container/openbiliclaw-ollama) (multi-arch: amd64 + arm64; bge-m3 baked in)"
  docker_download_line="- Docker (self-hosted): download [\`docker-compose.prebuilt.yml\`](https://github.com/${repo}/blob/main/docker-compose.prebuilt.yml), run \`docker compose -f docker-compose.prebuilt.yml up -d\`, then open \`http://127.0.0.1:8420/setup/\`
"
fi

declare -a assets=()
seen_asset_names=$'\n'

asset_name_seen() {
  local candidate="$1"
  case "$seen_asset_names" in
    *$'\n'"$candidate"$'\n'*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

add_asset() {
  local asset="$1"
  local name
  name="$(basename "$asset")"

  if [ ! -f "$asset" ]; then
    return
  fi
  if asset_name_seen "$name"; then
    return
  fi

  assets+=("$asset")
  seen_asset_names+="${name}"$'\n'
}

add_glob_assets() {
  local pattern
  local match
  shopt -s nullglob
  for pattern in "$@"; do
    for match in $pattern; do
      add_asset "$match"
    done
  done
  shopt -u nullglob
}

if [ -n "${ASSET_GLOBS:-}" ]; then
  # shellcheck disable=SC2206
  asset_patterns=($ASSET_GLOBS)
  add_glob_assets "${asset_patterns[@]}"
fi

download_release_assets() {
  local source_tag="$1"
  shift

  if [ -z "$source_tag" ]; then
    return
  fi
  if ! gh release view "$source_tag" --repo "$repo" >/dev/null 2>&1; then
    return
  fi

  local target_dir="$download_dir/$source_tag"
  local pattern
  local asset
  mkdir -p "$target_dir"

  for pattern in "$@"; do
    if ! gh release download "$source_tag" \
      --repo "$repo" \
      --pattern "$pattern" \
      --dir "$target_dir" \
      --clobber >/dev/null 2>&1; then
      echo "No assets matched ${source_tag}:${pattern}; continuing" >&2
      continue
    fi
  done

  while IFS= read -r -d '' asset; do
    add_asset "$asset"
  done < <(find "$target_dir" -maxdepth 1 -type f -print0)
}

download_release_assets "$extension_tag" "openbiliclaw-extension-v*.zip" "openbiliclaw-extension-v*.xpi" "openbiliclaw-extension-v*-safari.dmg"
download_release_assets "$desktop_tag" "*.dmg" "*.exe"

if [ -n "$extension_tag" ]; then
  firefox_xpi_asset_name="openbiliclaw-extension-v${extension_version}-firefox.xpi"
  if asset_name_seen "$firefox_xpi_asset_name"; then
    firefox_signed_asset_line="use \`$firefox_xpi_asset_name\`"
  fi
  safari_extension_asset_name="openbiliclaw-extension-v${extension_version}-safari.dmg"
  if asset_name_seen "$safari_extension_asset_name"; then
    safari_extension_asset_line="use \`$safari_extension_asset_name\` (macOS 14+, Safari 18+)"
  fi
fi

asset_list="No package assets were attached by this run."
if [ "${#assets[@]}" -gt 0 ]; then
  asset_list=""
  for asset in "${assets[@]}"; do
    asset_list+="- \`$(basename "$asset")\`
"
  done
fi

cat > "$notes_file" <<EOF
This is the user-facing aggregate release. It keeps the current backend source tag, browser extension packages, and desktop installers visible together.

## Current Channels

- Backend source: [${backend_tag}](https://github.com/${repo}/tree/${backend_tag})
- Browser extension: ${extension_line}
- Desktop installer: ${desktop_line}.${desktop_note}
- Docker image: ${docker_line}

## Downloads

- Chrome / Edge / Brave extension: ${chrome_extension_asset_line}
- Firefox 140+ extension: ${firefox_signed_asset_line}
- Firefox temporary debugging package: ${firefox_dev_asset_line}
- Safari 18+ extension: ${safari_extension_asset_line}
- macOS / Windows desktop app: use the attached \`.dmg\` / \`.exe\` installer when present
${docker_download_line}

Attached package assets:

${asset_list}
## Notes

- Chrome Web Store updates can lag GitHub releases because Google review is asynchronous.
- The Safari \`.dmg\` is signed and notarized when Apple credentials are configured in CI; otherwise it is an unsigned experimental build and requires Safari Settings → Developer → Allow Unsigned Extensions after first launch.
- The desktop app is still unsigned and experimental; first launch may need the README bypass steps.
- Automation channel releases remain available as \`backend-v*\`, \`extension-v*\`, and \`desktop-v*\`; Docker images ride \`backend-v*\` tags to GHCR automatically.

Synced by channel: \`${channel}\`
EOF

sync_release_notes() {
  for attempt in 1 2 3; do
    if gh release view "$aggregate_tag" --repo "$repo" >/dev/null 2>&1; then
      if gh release edit "$aggregate_tag" \
        --repo "$repo" \
        --title "$title" \
        --notes-file "$notes_file" \
        --draft=false \
        --latest; then
        return
      fi
    else
      if [ -n "${GITHUB_SHA:-}" ]; then
        if gh release create "$aggregate_tag" \
          --repo "$repo" \
          --title "$title" \
          --notes-file "$notes_file" \
          --latest \
          --target "$GITHUB_SHA"; then
          return
        fi
      elif gh release create "$aggregate_tag" \
        --repo "$repo" \
        --title "$title" \
        --notes-file "$notes_file" \
        --latest; then
        return
      fi

      if gh release view "$aggregate_tag" --repo "$repo" >/dev/null 2>&1; then
        if gh release edit "$aggregate_tag" \
          --repo "$repo" \
          --title "$title" \
          --notes-file "$notes_file" \
          --draft=false \
          --latest; then
          return
        fi
      fi
    fi

    if [ "$attempt" -eq 3 ]; then
      return 1
    fi
    sleep "$((attempt * 5))"
  done
}

sync_release_notes

if [ "${#assets[@]}" -eq 0 ]; then
  echo "Aggregate release ${aggregate_tag} synced without package assets"
  exit 0
fi

is_aggregate_package_asset() {
  local asset_name="$1"

  case "$asset_name" in
    openbiliclaw-extension-v*.zip | openbiliclaw-extension-v*.xpi | openbiliclaw-extension-v*-safari.dmg | OpenBiliClaw-macos-v*.dmg | OpenBiliClaw-windows-*-Setup.exe)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

prune_existing_package_assets() {
  local asset_name

  # Only prune an existing package asset when we actually have a replacement of
  # the SAME NAME to upload this run. A partial release (e.g. the with-embedding
  # desktop variant failed to build) must NOT delete the previous good asset it
  # can't replace — otherwise half a release wipes the other half's downloads.
  local replacing=$'\n'
  local path base
  for path in "${assets[@]}"; do
    base="$(basename "$path")"
    replacing+="${base}"$'\n'
  done

  while IFS= read -r asset_name; do
    if [ -z "$asset_name" ]; then
      continue
    fi
    if ! is_aggregate_package_asset "$asset_name"; then
      continue
    fi
    # Skip unless a same-named file is in this run's upload set.
    case "$replacing" in
      *$'\n'"$asset_name"$'\n'*) ;;
      *) continue ;;
    esac

    delete_existing_package_asset "$asset_name"
  done < <(
    gh release view "$aggregate_tag" \
      --repo "$repo" \
      --json assets \
      --jq '.assets[].name'
  )
}

delete_existing_package_asset() {
  local asset_name="$1"
  local attempt
  local delete_log
  delete_log="$(mktemp)"

  for attempt in 1 2 3; do
    if gh release delete-asset "$aggregate_tag" "$asset_name" --repo "$repo" --yes > /dev/null 2>"$delete_log"; then
      rm -f "$delete_log"
      return 0
    fi

    if grep -qi "not found" "$delete_log"; then
      rm -f "$delete_log"
      return 0
    fi

    cat "$delete_log" >&2
    if [ "$attempt" -eq 3 ]; then
      rm -f "$delete_log"
      return 1
    fi
    sleep "$((attempt * 5))"
  done
}

prune_existing_package_assets

for asset in "${assets[@]}"; do
  for attempt in 1 2 3; do
    if gh release upload "$aggregate_tag" "$asset" --repo "$repo" --clobber; then
      break
    fi
    if [ "$attempt" -eq 3 ]; then
      exit 1
    fi
    sleep "$((attempt * 5))"
  done
done

echo "Aggregate release ${aggregate_tag} synced with ${#assets[@]} package asset(s)"
