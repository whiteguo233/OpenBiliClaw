#!/usr/bin/env bash
set -euo pipefail
if [[ "${MACOS_SIGNING_ENABLED:-}" == false ]]; then
  echo 'macOS signing explicitly disabled; building an ad-hoc experimental package.'
  exit 0
fi
names=(APPLE_DEVELOPER_ID_CERTIFICATE_BASE64 APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD APPLE_TEAM_ID APPLE_NOTARY_USER APPLE_NOTARY_PASSWORD)
present=0
for name in "${names[@]}"; do
  if [[ -n "${!name:-}" ]]; then present=$((present + 1)); fi
done
if [[ "$present" == 0 && "${MACOS_SIGNING_ENABLED:-}" != true ]]; then
  echo '::warning::No Apple credentials configured; building an ad-hoc experimental package.'
  exit 0
fi
for name in "${names[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "::error::Missing required signing credential: $name"
    exit 1
  fi
done
umask 077
keychain_path="$RUNNER_TEMP/obc-desktop-signing.keychain-db"
cert_path="$RUNNER_TEMP/obc-desktop-signing.p12"
keychain_password="$(openssl rand -base64 24)"
echo "::add-mask::$keychain_password"
echo "APPLE_SIGNING_KEYCHAIN=$keychain_path" >> "$GITHUB_ENV"
trap 'rm -f "$cert_path"' EXIT
security create-keychain -p "$keychain_password" "$keychain_path"
security set-keychain-settings -lut 21600 "$keychain_path"
security unlock-keychain -p "$keychain_password" "$keychain_path"
printf '%s' "$APPLE_DEVELOPER_ID_CERTIFICATE_BASE64" | base64 --decode > "$cert_path"
security import "$cert_path" -P "$APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD" -T /usr/bin/codesign -t cert -f pkcs12 -k "$keychain_path"
security list-keychains -d user -s "$keychain_path" "$HOME/Library/Keychains/login.keychain-db"
security default-keychain -d user -s "$keychain_path"
security set-key-partition-list -S apple-tool:,apple: -s -k "$keychain_password" "$keychain_path" >/dev/null
identity="$(security find-identity -v -p codesigning "$keychain_path" | awk -F'"' '/Developer ID Application:/{print $2; exit}')"
if [[ -z "$identity" || "$identity" != *"($APPLE_TEAM_ID)" ]]; then
  echo '::error::No Developer ID Application identity matching APPLE_TEAM_ID'
  exit 1
fi
xcrun notarytool store-credentials obc-desktop-notary \
  --apple-id "$APPLE_NOTARY_USER" --team-id "$APPLE_TEAM_ID" \
  --password "$APPLE_NOTARY_PASSWORD" --keychain "$keychain_path"
printf 'APPLE_SIGNING_IDENTITY=%s\nAPPLE_NOTARY_PROFILE=obc-desktop-notary\nAPPLE_NOTARY_KEYCHAIN=%s\n' "$identity" "$keychain_path" >> "$GITHUB_ENV"
