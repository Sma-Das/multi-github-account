/* Standalone mock API. No fetch, GitHub tokens, filesystem access, or server. */
(() => {
  const HOME = '/Users/demo';
  const STORAGE_KEY = 'ghr-demo-v1';
  const clone = value => JSON.parse(JSON.stringify(value));
  const accounts = [
    { host: 'github.com', account: 'alex-dev', state: 'success', active: true, source: 'demo' },
    { host: 'github.com', account: 'alex-work', state: 'success', active: false, source: 'demo' },
    { host: 'github.acme.example', account: 'alex-corp', state: 'error', active: false, source: 'demo' },
  ];
  const seeds = [
    { path: `${HOME}/GitHub/personal/portfolio`, host: 'github.com', account: 'alex-dev' },
    { path: `${HOME}/GitHub/personal/account-router`, host: 'github.com', account: 'alex-dev' },
    { path: `${HOME}/GitHub/work/api-service`, host: 'github.com', account: 'alex-work' },
    { path: `${HOME}/GitHub/work/dashboard`, host: 'github.com', account: 'alex-work' },
    { path: `${HOME}/GitHub/work/docs`, host: 'github.com', account: 'alex-work' },
    { path: `${HOME}/GitHub/enterprise/internal-tools`, host: 'github.acme.example', account: 'alex-corp' },
  ];
  const repositorySeeds = [
    ['personal/portfolio', 'github.com', 'alex-dev/portfolio', 'https'],
    ['personal/account-router', 'github.com', 'alex-dev/account-router', 'https'],
    ['personal/dotfiles', 'github.com', 'alex-dev/dotfiles', 'ssh'],
    ['work/api-service', 'github.com', 'acme/api-service', 'https'],
    ['work/dashboard', 'github.com', 'acme/dashboard', 'https'],
    ['work/docs', 'github.com', 'acme/docs', 'https'],
    ['work/design-system', 'github.com', 'acme/design-system', 'https'],
    ['enterprise/internal-tools', 'github.acme.example', 'platform/internal-tools', 'https'],
  ];
  let mappings = clone(seeds);
  const within = (path, parent) => path === parent || path.startsWith(parent === '/' ? '/' : parent + '/');

  function normalizePath(input) {
    if (typeof input !== 'string' || !input.trim()) throw new Error('Enter a folder path.');
    let value = input.trim();
    if (value === '~') value = HOME;
    else if (value.startsWith('~/')) value = HOME + value.slice(1);
    else if (!value.startsWith('/')) value = `${HOME}/GitHub/${value}`;
    const parts = [];
    for (const part of value.split('/')) {
      if (!part || part === '.') continue;
      if (part === '..') parts.pop();
      else parts.push(part);
    }
    return '/' + parts.join('/');
  }

  function validMapping(mapping) {
    return mapping && typeof mapping.path === 'string' && mapping.path.startsWith('/')
      && accounts.some(a => a.host === mapping.host && a.account === mapping.account);
  }

  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (stored?.version === 1 && Array.isArray(stored.mappings)
        && stored.mappings.length <= 200 && stored.mappings.every(validMapping)) {
      mappings = stored.mappings.map(m => ({ path: normalizePath(m.path), host: m.host, account: m.account }));
    }
  } catch { /* Missing, blocked, or invalid storage starts a fresh demo. */ }

  function persist() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, mappings })); }
    catch { /* The demo still works in memory if browser storage is blocked. */ }
  }

  function resolve(path, host) {
    const candidates = mappings.filter(m => m.host === host && within(path, m.path));
    candidates.sort((a, b) => b.path.length - a.path.length);
    return candidates[0]?.account || null;
  }

  async function request(path, options = {}) {
    // Small local delay lets the normal loading states remain visible.
    await new Promise(resolve => setTimeout(resolve, 100));
    const url = new URL(path, 'https://demo.invalid');
    const method = options.method || 'GET';
    if (method === 'GET' && url.pathname === '/api/state') {
      return clone({ accounts, mappings, config: `${HOME}/.config/ghr/config.json`, home: HOME });
    }
    if (method === 'GET' && url.pathname === '/api/scan') {
      const root = normalizePath(url.searchParams.get('path') || `${HOME}/GitHub`);
      const repositories = repositorySeeds.filter(([folder]) => within(`${HOME}/GitHub/${folder}`, root))
        .map(([folder, host, repo, protocol]) => {
          const path = `${HOME}/GitHub/${folder}`;
          return { path, primary: path, account: resolve(path, host), remotes: [
            { name: 'origin', direction: 'fetch', host, repo, protocol },
            { name: 'origin', direction: 'push', host, repo, protocol },
          ] };
        });
      return clone({ repositories });
    }
    if (url.pathname === '/api/mappings' && ['PUT', 'DELETE'].includes(method)) {
      let data;
      try { data = JSON.parse(options.body); } catch { throw new Error('Invalid route data.'); }
      if (!data || typeof data !== 'object' || typeof data.host !== 'string') throw new Error('Choose a GitHub host.');
      const folder = normalizePath(data.path);
      const host = data.host.toLowerCase();
      if (method === 'PUT') {
        if (!accounts.some(a => a.host === host && a.account === data.account && a.state === 'success')) {
          throw new Error('Choose a connected demo account.');
        }
        const remote = repositorySeeds.find(([name]) => `${HOME}/GitHub/${name}` === folder);
        if (remote && remote[1] !== host) throw new Error("That hostname does not match this repository's remotes.");
        if (mappings.length >= 200 && !mappings.some(m => m.path === folder && m.host === host)) {
          throw new Error('This demo supports up to 200 routes. Reset the demo to start over.');
        }
      }
      mappings = mappings.filter(m => m.path !== folder || m.host !== host);
      if (method === 'PUT') mappings.push({ path: folder, host, account: data.account });
      mappings.sort((a, b) => a.path.localeCompare(b.path) || a.host.localeCompare(b.host));
      persist();
      return method === 'PUT' ? { path: folder, host, account: data.account } : { removed: true };
    }
    throw new Error('This action is not part of the interactive demo.');
  }

  function reset() { mappings = clone(seeds); persist(); }
  globalThis.GHR_DEMO = Object.freeze({ request, reset });
})();
