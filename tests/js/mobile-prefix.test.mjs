import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const web = new URL('../../src/openbiliclaw/web/', import.meta.url);

test('mobile free-model assets stay under the scanned backend prefix', async () => {
  const html = await readFile(new URL('index.html', web), 'utf8');
  const assets = [...html.matchAll(/(?:src|href)="([^"]*sponsored-settings\.(?:js|css))"/g)]
    .map(match => new URL(match[1], 'https://obc.example/tenant/one/m/').pathname);
  assert.deepEqual(assets.sort(), [
    '/tenant/one/shared/sponsored-settings.css',
    '/tenant/one/shared/sponsored-settings.js',
  ]);
});

async function load(relative, tag) {
  const source = await readFile(new URL(relative, web), 'utf8');
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}#${tag}`);
}

test('mobile API requests retain the backend prefix from the scanned page', async () => {
  const original = { location: globalThis.location, fetch: globalThis.fetch };
  const calls = [];
  try {
    globalThis.location = new URL('https://obc.example/tenant/one/m/?obc_connection=tailscale#/recommend');
    globalThis.fetch = async (url) => {
      calls.push(String(url));
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    };
    const api = await load('js/api.js', 'prefix-api');
    await api.fetchRecommendations();
    assert.equal(calls[0], 'https://obc.example/tenant/one/api/recommendations');
  } finally {
    globalThis.location = original.location;
    globalThis.fetch = original.fetch;
  }
});

test('mobile realtime connections retain the backend prefix and HTTPS', async () => {
  const original = { location: globalThis.location, WebSocket: globalThis.WebSocket };
  const connections = [];
  try {
    globalThis.location = new URL('https://obc.example/tenant/one/m/index.html#/recommend');
    globalThis.WebSocket = class {
      constructor(url) { connections.push(String(url)); }
      close() {}
    };
    const { createStreamClient } = await load('js/stream.js', 'prefix-stream');
    const stream = createStreamClient();
    stream.connect();
    stream.disconnect();
    assert.deepEqual(connections, ['wss://obc.example/tenant/one/api/runtime-stream']);
  } finally {
    globalThis.location = original.location;
    globalThis.WebSocket = original.WebSocket;
  }
});

test('mobile cover and preload URLs resolve beneath the scanned backend prefix', async () => {
  const { getCoverImageAttrs, getRecommendationCoverPreloadUrls } = await load('js/view-models.js', 'prefix-covers');
  const cover = 'https://i.example/cover.jpg';
  const page = 'http://[fd7a::12]:19090/tenant/m/';
  const expected = 'http://[fd7a::12]:19090/tenant/api/image-proxy?url=https%3A%2F%2Fi.example%2Fcover.jpg';
  assert.equal(new URL(getCoverImageAttrs(cover).src, page).href, expected);
  assert.deepEqual(getRecommendationCoverPreloadUrls([{ cover_url: cover }])
    .map(url => new URL(url, page).href), [expected]);
});
