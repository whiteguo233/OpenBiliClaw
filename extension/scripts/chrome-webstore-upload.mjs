#!/usr/bin/env node

import { readFile, stat } from "node:fs/promises";
import { basename, resolve } from "node:path";

const OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token";
const CWS_API_BASE = "https://chromewebstore.googleapis.com";
const CWS_SCOPE = "https://www.googleapis.com/auth/chromewebstore";

function usage() {
  console.log(`Usage:
  node scripts/chrome-webstore-upload.mjs --zip <archive.zip> [--publish]

Required environment variables:
  CHROME_WEBSTORE_CLIENT_ID
  CHROME_WEBSTORE_CLIENT_SECRET
  CHROME_WEBSTORE_REFRESH_TOKEN
  CHROME_WEBSTORE_PUBLISHER_ID
  CHROME_WEBSTORE_EXTENSION_ID

Options:
  --zip <path>                  Chrome-compatible extension zip to upload.
  --publish                     Submit the uploaded package for review.
  --staged                      Publish as staged after approval.
  --skip-review                 Ask Chrome Web Store to skip review when eligible.
  --deploy-percentage <0-100>   Initial rollout percentage for publish.
  --poll-interval-seconds <n>   Upload-status polling interval. Default: 5.
  --wait-timeout-seconds <n>    Upload processing timeout. Default: 120.
  --help                        Show this help.
`);
}

function parseArgs(argv) {
  const options = {
    zip: "",
    publish: false,
    staged: false,
    skipReview: false,
    deployPercentage: null,
    pollIntervalSeconds: 5,
    waitTimeoutSeconds: 120,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--help" || arg === "-h") {
      usage();
      process.exit(0);
    }
    if (arg === "--zip") {
      options.zip = argv[++i] ?? "";
      continue;
    }
    if (arg === "--publish") {
      options.publish = true;
      continue;
    }
    if (arg === "--staged") {
      options.staged = true;
      options.publish = true;
      continue;
    }
    if (arg === "--skip-review") {
      options.skipReview = true;
      continue;
    }
    if (arg === "--deploy-percentage") {
      const value = Number.parseInt(argv[++i] ?? "", 10);
      if (!Number.isInteger(value) || value < 0 || value > 100) {
        throw new Error("--deploy-percentage must be an integer from 0 to 100");
      }
      options.deployPercentage = value;
      options.publish = true;
      continue;
    }
    if (arg === "--poll-interval-seconds") {
      options.pollIntervalSeconds = parsePositiveInt(
        argv[++i],
        "--poll-interval-seconds",
      );
      continue;
    }
    if (arg === "--wait-timeout-seconds") {
      options.waitTimeoutSeconds = parsePositiveInt(
        argv[++i],
        "--wait-timeout-seconds",
      );
      continue;
    }
    throw new Error(`Unknown argument: ${arg}`);
  }

  if (!options.zip) {
    throw new Error("--zip is required");
  }
  return options;
}

function parsePositiveInt(value, flag) {
  const parsed = Number.parseInt(value ?? "", 10);
  if (!Number.isInteger(parsed) || parsed < 1) {
    throw new Error(`${flag} must be a positive integer`);
  }
  return parsed;
}

function requireEnv(name) {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  const text = await response.text();
  let payload = {};
  if (text.trim()) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { raw: text };
    }
  }
  if (!response.ok) {
    const details = JSON.stringify(payload, null, 2);
    throw new Error(`HTTP ${response.status} ${response.statusText} from ${url}\n${details}`);
  }
  return payload;
}

async function getAccessToken({ clientId, clientSecret, refreshToken }) {
  const body = new URLSearchParams({
    client_id: clientId,
    client_secret: clientSecret,
    refresh_token: refreshToken,
    grant_type: "refresh_token",
  });
  const payload = await requestJson(OAUTH_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (typeof payload.access_token !== "string" || !payload.access_token) {
    throw new Error("OAuth token response did not include access_token");
  }
  if (typeof payload.scope === "string" && !payload.scope.includes(CWS_SCOPE)) {
    throw new Error(`OAuth token is missing required scope: ${CWS_SCOPE}`);
  }
  return payload.access_token;
}

function itemName({ publisherId, extensionId }) {
  return `publishers/${publisherId}/items/${extensionId}`;
}

async function uploadArchive({ archivePath, accessToken, publisherId, extensionId }) {
  const file = await readFile(archivePath);
  const uploadUrl = `${CWS_API_BASE}/upload/v2/${itemName({
    publisherId,
    extensionId,
  })}:upload`;
  return await requestJson(uploadUrl, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/zip",
    },
    body: file,
  });
}

async function fetchStatus({ accessToken, publisherId, extensionId }) {
  const statusUrl = `${CWS_API_BASE}/v2/${itemName({
    publisherId,
    extensionId,
  })}:fetchStatus`;
  return await requestJson(statusUrl, {
    method: "GET",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

function uploadState(payload) {
  return String(payload.uploadState || payload.lastAsyncUploadState || "");
}

async function waitForUpload({ accessToken, publisherId, extensionId, options }) {
  const deadline = Date.now() + options.waitTimeoutSeconds * 1000;
  while (Date.now() < deadline) {
    const status = await fetchStatus({ accessToken, publisherId, extensionId });
    const state = uploadState(status);
    console.log(`Chrome Web Store upload status: ${state || "unknown"}`);
    if (state === "SUCCEEDED") {
      return status;
    }
    if (state === "FAILED" || state === "NOT_FOUND") {
      throw new Error(`Chrome Web Store upload did not succeed: ${JSON.stringify(status, null, 2)}`);
    }
    await new Promise((resolveSleep) => {
      setTimeout(resolveSleep, options.pollIntervalSeconds * 1000);
    });
  }
  throw new Error(
    `Timed out waiting for Chrome Web Store upload after ${options.waitTimeoutSeconds}s`,
  );
}

async function publishItem({ accessToken, publisherId, extensionId, options }) {
  const publishUrl = `${CWS_API_BASE}/v2/${itemName({
    publisherId,
    extensionId,
  })}:publish`;
  const body = {
    publishType: options.staged ? "STAGED_PUBLISH" : "DEFAULT_PUBLISH",
  };
  if (options.skipReview) {
    body.skipReview = true;
  }
  if (options.deployPercentage !== null) {
    body.deployInfos = [{ deployPercentage: options.deployPercentage }];
  }
  return await requestJson(publishUrl, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const archivePath = resolve(options.zip);
  const archiveStats = await stat(archivePath);
  if (!archiveStats.isFile()) {
    throw new Error(`Archive is not a file: ${archivePath}`);
  }
  if (!archivePath.endsWith(".zip")) {
    throw new Error(`Chrome Web Store upload expects a .zip archive: ${archivePath}`);
  }

  const credentials = {
    clientId: requireEnv("CHROME_WEBSTORE_CLIENT_ID"),
    clientSecret: requireEnv("CHROME_WEBSTORE_CLIENT_SECRET"),
    refreshToken: requireEnv("CHROME_WEBSTORE_REFRESH_TOKEN"),
    publisherId: requireEnv("CHROME_WEBSTORE_PUBLISHER_ID"),
    extensionId: requireEnv("CHROME_WEBSTORE_EXTENSION_ID"),
  };

  console.log(`Uploading ${basename(archivePath)} to Chrome Web Store item ${credentials.extensionId}...`);
  const accessToken = await getAccessToken(credentials);
  const upload = await uploadArchive({
    archivePath,
    accessToken,
    publisherId: credentials.publisherId,
    extensionId: credentials.extensionId,
  });
  console.log(`Upload response: ${JSON.stringify(upload, null, 2)}`);

  const state = uploadState(upload);
  if (state === "IN_PROGRESS") {
    await waitForUpload({
      accessToken,
      publisherId: credentials.publisherId,
      extensionId: credentials.extensionId,
      options,
    });
  } else if (state !== "SUCCEEDED") {
    throw new Error(`Chrome Web Store upload did not succeed: ${JSON.stringify(upload, null, 2)}`);
  }

  if (!options.publish) {
    console.log("Upload succeeded. Skipping publish because --publish was not set.");
    return;
  }

  const published = await publishItem({
    accessToken,
    publisherId: credentials.publisherId,
    extensionId: credentials.extensionId,
    options,
  });
  console.log(`Publish response: ${JSON.stringify(published, null, 2)}`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
