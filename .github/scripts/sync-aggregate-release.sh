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

latest_release_with_prefix() {
  local prefix="$1"

  if [ -n "$release_tag" ] && [[ "$release_tag" == "$prefix"* ]]; then
    printf '%s\n' "$release_tag"
    return
  fi

  local releases
  releases="$(
    gh release list \
      --repo "$repo" \
      --limit 100 \
      --json tagName,isDraft \
      --jq '.[] | select(.isDraft == false) | .tagName'
  )"

  while IFS= read -r tag_name; do
    if [[ "$tag_name" == "$prefix"* ]]; then
      printf '%s\n' "$tag_name"
      return
    fi
  done <<< "$releases"
}

extension_tag="$(latest_release_with_prefix "extension-v")"
desktop_tag="$(latest_release_with_prefix "desktop-v")"

extension_line="Not published yet."
chrome_extension_asset_line="No Chrome-compatible extension release asset is available yet."
firefox_extension_asset_line="No Firefox extension release asset is available yet."
if [ -n "$extension_tag" ]; then
  extension_version="${extension_tag#extension-v}"
  extension_line="[${extension_tag}](https://github.com/${repo}/releases/tag/${extension_tag})"
  chrome_extension_asset_line="\`openbiliclaw-extension-v${extension_version}.zip\`"
  firefox_extension_asset_line="\`openbiliclaw-extension-v${extension_version}-firefox.zip\`"
fi

desktop_line="Not published yet."
desktop_note=""
if [ -n "$desktop_tag" ]; then
  desktop_line="[${desktop_tag}](https://github.com/${repo}/releases/tag/${desktop_tag})"
  if [ "$desktop_tag" != "desktop-v${project_version}" ]; then
    desktop_note=" The latest desktop installer can lag the backend source version."
  fi
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

download_release_assets "$extension_tag" "openbiliclaw-extension-v*.zip"
download_release_assets "$desktop_tag" "*.dmg" "*.exe"

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

## Downloads

- Chrome / Edge / Brave extension: use ${chrome_extension_asset_line}
- Firefox 140+ extension: use ${firefox_extension_asset_line}
- macOS / Windows desktop app: use the attached \`.dmg\` / \`.exe\` installer when present

Attached package assets:

${asset_list}
## Notes

- Chrome Web Store updates can lag GitHub releases because Google review is asynchronous.
- The desktop app is still unsigned and experimental; first launch may need the README bypass steps.
- Automation channel releases remain available as \`backend-v*\`, \`extension-v*\`, and \`desktop-v*\`.

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
