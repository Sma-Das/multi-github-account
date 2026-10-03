const fragment = location.hash.slice(1);
let session = /^[A-Za-z0-9_-]{43}$/.test(fragment) ? fragment : '';
try {
  if (session) sessionStorage.setItem('ghr-session', session);
  else session = sessionStorage.getItem('ghr-session') || '';
} catch { /* The launch URL still works when tab storage is unavailable. */ }
history.replaceState(null, '', '/');
const $ = (id) => document.getElementById(id);
let state = { accounts: [], mappings: [] };
let scannedPath = null;
let selectedAccount = '';
let currentView = 'overview';
let configPath = '';
let dialogReturnFocus = null;
const mobileViewport = matchMedia('(max-width: 760px)');

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  svg.classList.add('icon');
  svg.setAttribute('aria-hidden', 'true');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}
function notice(message, error = false) {
  $('notice-text').textContent = message;
  $('notice').classList.toggle('error', error);
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
function identityKey(identity) { return `${identity.host}/${identity.account}`; }
function shortPath(path) {
  return state.home && (path === state.home || path.startsWith(state.home + '/'))
    ? '~' + path.slice(state.home.length) : path;
}
function basename(path) { return path.split('/').filter(Boolean).pop() || '/'; }
function badge(text, kind = '') {
  const node = el('span', undefined, `badge ${kind}`.trim());
  if (kind !== 'neutral') node.append(el('span', undefined, 'status-dot'));
  node.append(document.createTextNode(text));
  return node;
}
function avatar(account, index = 0) {
  return el('span', account.slice(0, 2).toUpperCase(), `avatar${index % 2 ? ' purple' : ''}`);
}
function accountOptions(select, host) {
  const previous = select.value;
  const placeholder = el('option', 'Choose an account');
  placeholder.value = '';
  select.replaceChildren(placeholder);
  for (const account of state.accounts.filter(a => a.state === 'success' && (!host || a.host === host))) {
    const option = el('option', `@${account.account} · ${account.host}`);
    option.value = JSON.stringify({ host: account.host, account: account.account });
    select.append(option);
  }
  if ([...select.options].some(option => option.value === previous)) select.value = previous;
}
function setMenu(open) {
  document.body.classList.toggle('nav-open', open);
  $('sidebar-backdrop').hidden = !open;
  $('menu-toggle').setAttribute('aria-expanded', String(open));
  $('menu-toggle').setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  $('sidebar').inert = mobileViewport.matches && !open;
}
const views = {
  overview: ['Overview', 'Account routing', 'Manage GitHub identities across repositories and agents.'],
  routes: ['Folder routes', 'Folder routes', 'Assign the right GitHub identity to every workspace.'],
  repositories: ['Repositories', 'Your repositories', 'Find your checkouts. Connect them to the right accounts.'],
  agents: ['Agent setup', 'Agent setup', 'Launch agents with credentials scoped to their repository.'],
};
function setView(view) {
  currentView = view;
  $('breadcrumb-current').textContent = views[view][0];
  $('page-title').textContent = views[view][1];
  $('page-description').textContent = views[view][2];
  document.querySelectorAll('.nav-item[data-view]').forEach(button => {
    const active = button.dataset.view === view;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  $('view-overview').hidden = !['overview', 'routes'].includes(view);
  $('view-repositories').hidden = view !== 'repositories';
  $('view-agents').hidden = view !== 'agents';
  document.querySelectorAll('.overview-only').forEach(node => { node.hidden = view !== 'overview'; });
  setMenu(false);
}
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const next = theme === 'dark' ? 'light' : 'dark';
  $('theme-toggle').setAttribute('aria-label', `Switch to ${next} theme`);
  $('theme-toggle').title = `Switch to ${next} theme`;
  $('theme-toggle').replaceChildren(icon(next === 'light' ? 'sun' : 'moon'));
  try { localStorage.setItem('ghr-theme', theme); } catch { /* Theme still works without storage. */ }
}
function renderAccounts() {
  $('accounts').replaceChildren();
  state.accounts.forEach((account, index) => {
    const count = state.mappings.filter(m => identityKey(m) === identityKey(account)).length;
    const card = el('article', undefined, 'account-card');
    const top = el('div', undefined, 'account-top');
    const info = el('div', undefined, 'account-info');
    info.append(el('h3', `@${account.account}`), el('p', account.host));
    top.append(avatar(account.account, index), info,
      badge(account.state === 'success' ? 'Connected' : 'Needs login', account.state === 'success' ? '' : 'warning'));
    const bottom = el('div', undefined, 'account-bottom');
    const summary = el('span');
    summary.append(el('strong', String(count)), document.createTextNode(` folder route${count === 1 ? '' : 's'}`));
    const action = el('button', account.state === 'success' ? 'View routes' : 'Copy login command', 'text-button');
    action.append(icon(account.state === 'success' ? 'arrow' : 'copy'));
    action.addEventListener('click', () => {
      if (account.state !== 'success') {
        copyText(loginCommand(account.host), action);
        return;
      }
      selectedAccount = identityKey(account);
      $('route-search').value = '';
      renderFilters();
      renderMappings();
      setView('routes');
    });
    bottom.append(summary, action);
    card.append(top, bottom);
    $('accounts').append(card);
  });
  if (!state.accounts.length) $('accounts').append(el('div', 'No stored accounts yet. Add an account with gh auth login.', 'loading-placeholder'));
}
function renderFilters() {
  const keys = new Set(state.accounts.map(identityKey).concat(state.mappings.map(identityKey)));
  if (selectedAccount && !keys.has(selectedAccount)) selectedAccount = '';
  $('account-filters').replaceChildren();
  const entries = [['', 'All routes', state.mappings.length], ...[...keys].map(key => [
    key, '@' + key.slice(key.indexOf('/') + 1), state.mappings.filter(m => identityKey(m) === key).length,
  ])];
  for (const [key, name, count] of entries) {
    const button = el('button', name, `filter-tab${key === selectedAccount ? ' active' : ''}`);
    button.title = key || 'All routes';
    button.setAttribute('aria-pressed', String(key === selectedAccount));
    button.append(el('span', String(count), 'count'));
    button.addEventListener('click', () => { selectedAccount = key; renderFilters(); renderMappings(); });
    $('account-filters').append(button);
  }
}
function renderMappings() {
  const query = $('route-search').value.trim().toLowerCase();
  const mappings = state.mappings.filter(mapping => (!selectedAccount || identityKey(mapping) === selectedAccount)
    && `${mapping.path} ${mapping.account} ${mapping.host}`.toLowerCase().includes(query));
  $('mappings').replaceChildren();
  $('empty-mappings').hidden = !!mappings.length;
  if (!state.mappings.length) {
    $('empty-route-title').textContent = 'Your first route starts here.';
    $('empty-route-description').textContent = 'Assign a repository or workspace to a GitHub account. Your agents will know which identity to use.';
  } else {
    $('empty-route-title').textContent = 'No matching routes.';
    $('empty-route-description').textContent = 'Try another account or search term, or create a route for this account.';
  }
  mappings.forEach(mapping => {
    const row = el('tr');
    const folder = el('td');
    const content = el('div', undefined, 'folder-cell');
    const info = el('div');
    const path = el('code', shortPath(mapping.path));
    path.title = mapping.path;
    info.append(el('strong', basename(mapping.path)), path);
    content.append(icon('folder'), info);
    folder.append(content);
    const accountCell = el('td');
    const accountInfo = el('div', undefined, 'route-account');
    const accountIndex = state.accounts.findIndex(a => identityKey(a) === identityKey(mapping));
    accountInfo.append(avatar(mapping.account, Math.max(accountIndex, 0)), el('span', `@${mapping.account}`));
    accountCell.append(accountInfo);
    const status = el('td');
    const connected = state.accounts.some(a => identityKey(a) === identityKey(mapping) && a.state === 'success');
    status.append(badge(connected ? 'Configured' : 'Needs login', connected ? '' : 'warning'));
    const actions = el('td');
    const buttons = el('div', undefined, 'row-actions');
    const edit = el('button', undefined, 'icon-button');
    edit.append(icon('edit'));
    edit.setAttribute('aria-label', `Edit route for ${mapping.path}`);
    edit.title = 'Edit route';
    edit.addEventListener('click', () => openRoute(mapping));
    const remove = el('button', undefined, 'icon-button delete-button');
    remove.append(icon('trash'));
    remove.setAttribute('aria-label', `Remove route for ${mapping.path}`);
    remove.title = 'Remove route';
    remove.addEventListener('click', async () => {
      remove.disabled = true;
      try {
        await api('/api/mappings', { method: 'DELETE', body: JSON.stringify(mapping) });
        await refresh();
        notice(`Removed the route for ${basename(mapping.path)}.`);
      } catch (error) { notice(error.message, true); remove.disabled = false; }
    });
    buttons.append(edit, remove);
    actions.append(buttons);
    row.append(folder, accountCell, el('td', mapping.host, 'host-cell'), status, actions);
    $('mappings').append(row);
  });
}
function renderAgentRoutes() {
  const selected = $('agent-route').value;
  const placeholder = el('option', 'Choose a route');
  placeholder.value = '';
  $('agent-route').replaceChildren(placeholder);
  state.mappings.forEach(mapping => {
    const option = el('option', `${shortPath(mapping.path)} · @${mapping.account}`);
    option.value = JSON.stringify(mapping);
    $('agent-route').append(option);
  });
  if ([...$('agent-route').options].some(option => option.value === selected)) $('agent-route').value = selected;
  updateLaunchCommand();
}
async function refresh() {
  const next = await api('/api/state');
  state = next;
  configPath = state.config;
  const connected = state.accounts.filter(a => a.state === 'success').length;
  const hosts = [...new Set(state.accounts.map(a => a.host))];
  $('account-count').textContent = state.accounts.length;
  $('account-summary').textContent = `${connected} connected${connected < state.accounts.length ? ` · ${state.accounts.length - connected} needs login` : ' · Ready for routing'}`;
  $('mapping-count').textContent = state.mappings.length;
  $('nav-route-count').textContent = state.mappings.length;
  $('route-title-count').textContent = state.mappings.length;
  $('host-count').textContent = hosts.length;
  $('host-summary').textContent = hosts.join(' · ') || 'No hosts configured yet';
  $('config-location').title = `Copy configuration path: ${configPath}`;
  $('sync-status').textContent = `Last synced ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
  renderAccounts();
  renderFilters();
  renderMappings();
  renderAgentRoutes();
  accountOptions($('account'));
  if (!$('scan-path').value) $('scan-path').value = `${state.home}/GitHub`;
  if (scannedPath) {
    try {
      const { repositories } = await api(`/api/scan?path=${encodeURIComponent(scannedPath)}`);
      renderRepositories(repositories);
    } catch (error) { notice(`Routes updated. Repository scan could not refresh: ${error.message}`, true); }
  }
}
function openRoute(mapping = null) {
  dialogReturnFocus = document.activeElement;
  $('dialog-title').textContent = mapping ? 'Edit folder route' : 'Create a folder route';
  $('folder').value = mapping ? mapping.path : '';
  $('folder').readOnly = !!mapping;
  accountOptions($('account'), mapping ? mapping.host : undefined);
  $('account').value = mapping ? JSON.stringify({ host: mapping.host, account: mapping.account }) : '';
  $('dialog-error').hidden = true;
  $('route-dialog').showModal();
}
function closeRoute() { $('route-dialog').close(); }
async function saveRoute(path, identity) {
  const route = await api('/api/mappings', { method: 'PUT', body: JSON.stringify({ path, ...identity }) });
  await refresh();
  notice(`Routed ${basename(route.path)} to @${route.account}.`);
  return route;
}
function renderRepositories(repositories) {
  $('repositories').replaceChildren();
  $('scan-count').textContent = `${repositories.length} repositor${repositories.length === 1 ? 'y' : 'ies'} found`;
  if (!repositories.length) {
    const empty = el('div', undefined, 'empty-state');
    const illustration = el('span', undefined, 'empty-illustration');
    illustration.append(icon('folder'));
    empty.append(illustration, el('h3', 'No repositories found.'), el('p', 'Try another workspace or scan a deeper subfolder.'));
    $('repositories').append(empty);
  }
  for (const repo of repositories) {
    const row = el('article', undefined, 'repo-card');
    const identity = el('div', undefined, 'repo-identity');
    const details = el('div', undefined, 'repo-details');
    details.append(el('h3', basename(repo.path)), el('code', shortPath(repo.path)));
    const unique = [...new Set(repo.remotes.map(r => `${r.name} · ${r.host}/${r.repo} · ${r.protocol.toUpperCase()}`))];
    for (const remote of unique) details.append(el('p', remote));
    if (!unique.length) details.append(el('p', 'No supported upstream remote'));
    details.append(badge(repo.account ? `@${repo.account}` : 'No route', repo.account ? '' : 'neutral'));
    identity.append(icon('folder'), details);
    const controls = el('div', undefined, 'repo-controls');
    const select = el('select');
    select.setAttribute('aria-label', `Account for ${repo.path}`);
    const hosts = [...new Set(repo.remotes.map(r => r.host))];
    accountOptions(select, hosts.length === 1 ? hosts[0] : undefined);
    const button = el('button', 'Save route', 'button secondary');
    button.addEventListener('click', async () => {
      if (!select.value) { notice('Choose an account first.', true); select.focus(); return; }
      button.disabled = true;
      try { await saveRoute(repo.path, JSON.parse(select.value)); }
      catch (error) { notice(error.message, true); }
      finally { button.disabled = false; }
    });
    controls.append(select, button);
    row.append(identity, controls);
    $('repositories').append(row);
  }
}
function shellQuote(value) {
  return /^[a-zA-Z0-9_./:-]+$/.test(value) ? value : "'" + value.replaceAll("'", "'\\''") + "'";
}
function loginCommand(host) {
  const hostname = host ? ` --hostname ${shellQuote(host)}` : '';
  return 'env -u GH_TOKEN -u GITHUB_TOKEN -u GH_ENTERPRISE_TOKEN -u GITHUB_ENTERPRISE_TOKEN -u GH_DEBUG -u DEBUG'
    + ` gh auth login${hostname} --web`;
}
function updateLaunchCommand() {
  const selected = $('agent-route').value;
  const mapping = selected ? JSON.parse(selected) : null;
  const path = mapping ? mapping.path : '/path/to/repo';
  const host = mapping ? ` --host ${shellQuote(mapping.host)}` : '';
  $('launch-command').textContent = `ghr exec --path ${shellQuote(path)}${host} -- ${$('agent-executable').value}`;
}
async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    notice('Copied to clipboard.');
    if (button) {
      const svg = button.querySelector('svg');
      if (svg) {
        const original = svg.cloneNode(true);
        svg.replaceWith(icon('check'));
        setTimeout(() => {
          const current = button.querySelector('svg');
          if (current) current.replaceWith(original);
        }, 1400);
      }
    }
  } catch { notice(`Clipboard unavailable. Copy manually: ${text}`, true); }
}

document.querySelectorAll('.nav-item[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
mobileViewport.addEventListener('change', () => setMenu(false));
document.querySelectorAll('[data-new-route]').forEach(button => button.addEventListener('click', () => openRoute()));
$('discover-link').addEventListener('click', () => setView('repositories'));
$('agent-link').addEventListener('click', () => setView('agents'));
$('menu-toggle').addEventListener('click', () => setMenu($('sidebar-backdrop').hidden));
$('sidebar-backdrop').addEventListener('click', () => { setMenu(false); $('menu-toggle').focus(); });
$('theme-toggle').addEventListener('click', () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
$('dismiss-notice').addEventListener('click', () => { $('notice').hidden = true; });
$('route-search').addEventListener('input', renderMappings);
$('close-dialog').addEventListener('click', closeRoute);
$('cancel-dialog').addEventListener('click', closeRoute);
$('route-dialog').addEventListener('close', () => {
  if (dialogReturnFocus && dialogReturnFocus.isConnected) dialogReturnFocus.focus();
});
$('mapping-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('save-route').disabled = true;
  $('dialog-error').hidden = true;
  try {
    if (!$('account').value) throw new Error('Choose a connected GitHub account.');
    await saveRoute($('folder').value, JSON.parse($('account').value));
    closeRoute();
  } catch (error) {
    $('dialog-error').textContent = error.message;
    $('dialog-error').hidden = false;
  } finally { $('save-route').disabled = false; }
});
$('refresh').addEventListener('click', async () => {
  $('refresh').disabled = true;
  try { await refresh(); notice('Accounts and routes are up to date.'); }
  catch (error) { notice(error.message, true); }
  finally { $('refresh').disabled = false; }
});
$('scan-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('scan-button').disabled = true;
  $('scan-button').querySelector('span').textContent = 'Scanning…';
  try {
    const path = $('scan-path').value;
    const { repositories } = await api(`/api/scan?path=${encodeURIComponent(path)}`);
    scannedPath = path;
    renderRepositories(repositories);
    notice(`Found ${repositories.length} repositories.`);
  } catch (error) { notice(error.message, true); }
  finally {
    $('scan-button').disabled = false;
    $('scan-button').querySelector('span').textContent = 'Scan workspace';
  }
});
$('agent-route').addEventListener('change', updateLaunchCommand);
$('agent-executable').addEventListener('change', updateLaunchCommand);
$('copy-launch').addEventListener('click', () => copyText($('launch-command').textContent, $('copy-launch')));
$('copy-login').addEventListener('click', () => copyText(loginCommand(), $('copy-login')));
$('config-location').addEventListener('click', () => { if (configPath) copyText(configPath, $('config-location')); });
document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', () => copyText(button.dataset.copy, button)));
document.addEventListener('keydown', (event) => {
  const typing = event.target.closest('input, select, textarea, [contenteditable="true"]');
  if (event.key === 'Escape' && !$('route-dialog').open) setMenu(false);
  if (typing || event.metaKey || event.ctrlKey || event.altKey || $('route-dialog').open) return;
  if (event.key.toLowerCase() === 'n') { event.preventDefault(); openRoute(); }
  if (event.key === '/') {
    event.preventDefault();
    if (!['overview', 'routes'].includes(currentView)) setView('routes');
    $('route-search').focus();
  }
});
try { setTheme(localStorage.getItem('ghr-theme') === 'light' ? 'light' : 'dark'); }
catch { setTheme('dark'); }
$('session-label').textContent = location.hostname.endsWith('.ts.net') ? 'Tailnet session' : 'Local session';
setMenu(false);
refresh().catch(error => { $('sync-status').textContent = 'Could not connect'; notice(error.message, true); });
