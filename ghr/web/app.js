const fragment = location.hash.slice(1);
if (fragment) sessionStorage.setItem('ghr-session', fragment);
history.replaceState(null, '', '/');
const session = sessionStorage.getItem('ghr-session');
let state = { accounts: [], mappings: [] };
let scannedPath = null;
const $ = (id) => document.getElementById(id);

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function notice(message, error = false) {
  $('notice').textContent = message;
  $('notice').className = error ? 'error' : 'success';
  $('notice').hidden = false;
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { Authorization: `Bearer ${session || ''}`, 'Content-Type': 'application/json' },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed.');
  return data;
}
function accountOptions(select, host) {
  select.replaceChildren(el('option', 'Choose an account'));
  select.firstChild.value = '';
  for (const account of state.accounts.filter(a => a.state === 'success' && (!host || a.host === host))) {
    const option = el('option', `@${account.account} · ${account.host}`);
    option.value = JSON.stringify({ host: account.host, account: account.account });
    select.append(option);
  }
}
async function refresh() {
  state = await api('/api/state');
  $('account-count').textContent = state.accounts.length;
  $('mapping-count').textContent = state.mappings.length;
  $('config-path').textContent = state.config;
  $('accounts').replaceChildren();
  for (const account of state.accounts) {
    const card = el('div', undefined, 'account-card');
    card.append(el('div', account.account.slice(0, 2).toUpperCase(), 'avatar'));
    const info = el('div');
    info.append(el('h3', `@${account.account}`), el('p', account.host, 'muted'));
    card.append(info, el('span', account.state === 'success' ? 'Connected' : 'Needs login', account.state === 'success' ? 'badge' : 'badge warning'));
    $('accounts').append(card);
  }
  if (!state.accounts.length) $('accounts').append(el('p', 'No stored accounts. Run gh auth login in your terminal.', 'empty'));
  accountOptions($('account'));
  $('mappings').replaceChildren();
  $('empty-mappings').hidden = !!state.mappings.length;
  for (const mapping of state.mappings) {
    const row = el('tr');
    const folder = el('td');
    folder.append(el('code', mapping.path));
    row.append(folder, el('td', `@${mapping.account}`), el('td', mapping.host, 'muted'));
    const action = el('td');
    const remove = el('button', 'Remove', 'quiet small');
    remove.setAttribute('aria-label', `Remove route for ${mapping.path}`);
    remove.addEventListener('click', async () => {
      remove.disabled = true;
      try {
        await api('/api/mappings', { method: 'DELETE', body: JSON.stringify(mapping) });
        await refresh();
        notice('Route removed.');
      } catch (error) { notice(error.message, true); remove.disabled = false; }
    });
    action.append(remove); row.append(action); $('mappings').append(row);
  }
  if (!$('scan-path').value) $('scan-path').value = `${state.home}/GitHub`;
  if (scannedPath) {
    const { repositories } = await api(`/api/scan?path=${encodeURIComponent(scannedPath)}`);
    renderRepositories(repositories);
  }
}
async function saveRoute(path, identity) {
  const route = await api('/api/mappings', { method: 'PUT', body: JSON.stringify({ path, ...identity }) });
  await refresh();
  notice(`Saved ${route.path} → @${route.account}.`);
}
$('mapping-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.target.querySelector('button');
  button.disabled = true;
  try {
    await saveRoute($('folder').value, JSON.parse($('account').value));
    $('folder').value = '';
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
});
$('refresh').addEventListener('click', () => refresh().catch(error => notice(error.message, true)));
function renderRepositories(repositories) {
    $('repositories').replaceChildren();
    if (!repositories.length) $('repositories').append(el('p', 'No repositories found. Scan checks up to five folders deep and stops at 200 repos.', 'empty'));
    for (const repo of repositories) {
      const row = el('div', undefined, 'repo-card');
      const details = el('div', undefined, 'repo-details');
      details.append(el('h3', repo.path.split('/').pop()), el('code', repo.path, 'muted'));
      const unique = [...new Set(repo.remotes.map(r => `${r.name} · ${r.host}/${r.repo} · ${r.protocol.toUpperCase()}`))];
      for (const remote of unique) details.append(el('p', remote, 'remote'));
      if (!unique.length) details.append(el('p', 'No supported upstream remote', 'remote'));
      details.append(el('p', repo.account ? `Routed to @${repo.account}` : 'No account route', repo.account ? 'routed' : 'muted'));
      const controls = el('div', undefined, 'repo-controls');
      const select = el('select');
      select.setAttribute('aria-label', `Account for ${repo.path}`);
      const hosts = [...new Set(repo.remotes.map(r => r.host))];
      accountOptions(select, hosts.length === 1 ? hosts[0] : undefined);
      const button = el('button', 'Save route', 'quiet');
      button.addEventListener('click', async () => {
        if (!select.value) { notice('Choose an account first.', true); return; }
        button.disabled = true;
        try {
          const identity = JSON.parse(select.value);
          await saveRoute(repo.path, identity);
          details.lastChild.textContent = `Routed to @${identity.account}`;
          details.lastChild.className = 'routed';
        } catch (error) { notice(error.message, true); }
        finally { button.disabled = false; }
      });
      controls.append(select, button); row.append(details, controls); $('repositories').append(row);
    }
}
$('scan-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('scan-button').disabled = true;
  $('scan-button').textContent = 'Scanning…';
  try {
    const path = $('scan-path').value;
    const { repositories } = await api(`/api/scan?path=${encodeURIComponent(path)}`);
    scannedPath = path;
    renderRepositories(repositories);
    notice(`Found ${repositories.length} repositories.`);
  } catch (error) { notice(error.message, true); }
  finally { $('scan-button').disabled = false; $('scan-button').textContent = 'Scan folders →'; }
});
refresh().catch(error => notice(error.message, true));
