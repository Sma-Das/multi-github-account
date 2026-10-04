import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const script = await readFile(new URL('../demo/demo.js', import.meta.url), 'utf8');

function browser(storage = new Map()) {
  const context = vm.createContext({ URL, setTimeout,
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
  });
  vm.runInContext(script, context);
  return context.GHR_DEMO;
}
const put = (api, route) => api.request('/api/mappings', { method: 'PUT', body: JSON.stringify(route) });

test('demo starts populated without network, filesystem, or session credentials', async () => {
  const api = browser();
  const state = await api.request('/api/state');
  assert.equal(state.accounts.length, 3);
  assert.equal(state.mappings.length, 6);
  assert.equal(new Set(state.accounts.map(a => a.host)).size, 2);
  assert.ok(state.accounts.every(a => a.source === 'demo'));
  assert.ok(!JSON.stringify(state).includes('token'));
});

test('route changes persist only in the same browser storage, and reset restores defaults', async () => {
  const storage = new Map();
  const api = browser(storage);
  const route = { path: '~/GitHub/sandbox', host: 'github.com', account: 'alex-dev' };
  const result = await put(api, route);
  assert.equal(result.path, '/Users/demo/GitHub/sandbox');
  assert.equal((await browser(storage).request('/api/state')).mappings.length, 7);
  assert.equal((await browser().request('/api/state')).mappings.length, 6);
  await api.request('/api/mappings', { method: 'DELETE', body: JSON.stringify(result) });
  assert.equal((await api.request('/api/state')).mappings.length, 6);
  await put(api, route);
  api.reset();
  assert.equal((await browser(storage).request('/api/state')).mappings.length, 6);
});

test('sample scans honor folder boundaries and the longest host-specific route', async () => {
  const api = browser();
  const all = await api.request('/api/scan?path=~/GitHub');
  assert.equal(all.repositories.length, 8);
  assert.equal((await api.request('/api/scan?path=~/GitHub/personal')).repositories.length, 3);
  assert.equal((await api.request('/api/scan?path=~/GitHub/person')).repositories.length, 0);
  await put(api, { path: '~/GitHub/work', host: 'github.com', account: 'alex-dev' });
  const work = await api.request('/api/scan?path=~/GitHub/work');
  assert.equal(work.repositories.find(r => r.path.endsWith('/design-system')).account, 'alex-dev');
  assert.equal(work.repositories.find(r => r.path.endsWith('/api-service')).account, 'alex-work');
});

test('invalid accounts, remote host mismatches, and unknown actions do not mutate routes', async () => {
  const api = browser();
  await assert.rejects(put(api, { path: '~/GitHub/sandbox', host: 'github.com', account: 'unknown' }), /connected demo account/);
  await assert.rejects(put(api, { path: '~/GitHub/personal/portfolio', host: 'github.acme.example', account: 'alex-corp' }), /connected demo account/);
  await assert.rejects(put(api, { path: '~/GitHub/enterprise/internal-tools', host: 'github.com', account: 'alex-dev' }), /hostname/);
  await assert.rejects(api.request('/api/credentials'), /not part/);
  assert.equal((await api.request('/api/state')).mappings.length, 6);
});

test('blocked or malformed browser storage falls back to a working in-memory demo', async () => {
  const broken = browser(new Map([['ghr-demo-v1', '{invalid']]));
  assert.equal((await broken.request('/api/state')).mappings.length, 6);
  const blocked = vm.createContext({ URL, setTimeout, localStorage: {
    getItem() { throw new Error('Blocked'); }, setItem() { throw new Error('Blocked'); },
  } });
  vm.runInContext(script, blocked);
  await put(blocked.GHR_DEMO, { path: '~/GitHub/sandbox', host: 'github.com', account: 'alex-dev' });
  assert.equal((await blocked.GHR_DEMO.request('/api/state')).mappings.length, 7);
});

test('remote-scoped routes coexist with defaults and remove independently', async () => {
  const api = browser();
  const target = { path: '~/GitHub/work/api-service', host: 'github.com', account: 'alex-dev', repo: 'ACME/API-SERVICE.git' };
  const scoped = await put(api, target);
  assert.equal(scoped.repo, 'acme/api-service');
  const state = await api.request('/api/state');
  assert.equal(state.mappings.length, 7);
  const scanned = await api.request('/api/scan?path=~/GitHub/work/api-service');
  assert.equal(scanned.repositories[0].account, 'alex-work');
  assert.equal(scanned.repositories[0].remotes[0].account, 'alex-dev');
  await api.request('/api/mappings', { method: 'DELETE', body: JSON.stringify(scoped) });
  assert.equal((await api.request('/api/state')).mappings.length, 6);
  assert.equal((await api.request('/api/scan?path=~/GitHub/work/api-service')).repositories[0].remotes[0].account, 'alex-work');
});
