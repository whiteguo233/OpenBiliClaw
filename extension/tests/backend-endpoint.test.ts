import assert from "node:assert/strict";
import test from "node:test";

import {
  __resetBackendEndpointForTests,
  getBackendBaseUrl,
  isValidBackendHost as isValidPopupBackendHost,
  isValidBackendPort as isValidPopupBackendPort,
  updateBackendEndpoint,
} from "../popup/popup-backend-config.js";
import {
  isValidBackendHost as isValidSharedBackendHost,
  isValidBackendPort as isValidSharedBackendPort,
} from "../src/shared/backend-endpoint.ts";

const validPorts: unknown[] = [1, 8420, 65535, "1", "8420", "65535", " 19090 "];
const invalidPorts: unknown[] = [
  0,
  65536,
  -1,
  "",
  "   ",
  "1.5",
  "1e3",
  "123abc",
  "0x20",
  "+8420",
  null,
  undefined,
];

test("popup backend port validation accepts only complete decimal integers in range", () => {
  for (const port of validPorts) {
    assert.equal(isValidPopupBackendPort(port), true, `${String(port)} should be valid`);
  }
  for (const port of invalidPorts) {
    assert.equal(isValidPopupBackendPort(port), false, `${String(port)} should be invalid`);
  }
});

test("shared backend port validation accepts only complete decimal integers in range", () => {
  for (const port of validPorts) {
    assert.equal(isValidSharedBackendPort(port), true, `${String(port)} should be valid`);
  }
  for (const port of invalidPorts) {
    assert.equal(isValidSharedBackendPort(port), false, `${String(port)} should be invalid`);
  }
});

const validHosts: unknown[] = [
  "",
  "   ",
  "localhost",
  "127.0.0.1",
  "192.168.1.100",
  "openbiliclaw.local",
  "nas-1.lan",
];
const invalidHosts: unknown[] = [
  "http://192.168.1.100",
  "192.168.1.100:8420",
  "192.168.1.256",
  "bad host",
  "-bad.local",
  "bad-.local",
  null,
  undefined,
];

test("popup backend host validation accepts bare IPs and hostnames only", () => {
  for (const host of validHosts) {
    assert.equal(isValidPopupBackendHost(host), true, `${String(host)} should be valid`);
  }
  for (const host of invalidHosts) {
    assert.equal(isValidPopupBackendHost(host), false, `${String(host)} should be invalid`);
  }
});

test("shared backend host validation accepts bare IPs and hostnames only", () => {
  for (const host of validHosts) {
    assert.equal(isValidSharedBackendHost(host), true, `${String(host)} should be valid`);
  }
  for (const host of invalidHosts) {
    assert.equal(isValidSharedBackendHost(host), false, `${String(host)} should be invalid`);
  }
});

test("popup backend endpoint update persists host and port together", async () => {
  __resetBackendEndpointForTests();
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  const writes: Array<Record<string, unknown>> = [];
  (globalThis as { chrome?: unknown }).chrome = {
    storage: {
      local: {
        set(items: Record<string, unknown>, callback: () => void) {
          writes.push(items);
          callback();
        },
      },
    },
  };

  try {
    const endpoint = await updateBackendEndpoint(" 192.168.1.100 ", "19090");

    assert.deepEqual(endpoint, { host: "192.168.1.100", port: 19090 });
    assert.deepEqual(writes, [
      {
        popup_backend_endpoint: {
          host: "192.168.1.100",
          port: 19090,
        },
      },
    ]);
    assert.equal(await getBackendBaseUrl(), "http://192.168.1.100:19090/api");
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
    __resetBackendEndpointForTests();
  }
});
