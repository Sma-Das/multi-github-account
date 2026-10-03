# GitHub account router

[Try the interactive demo](https://github-account-router-demo.vercel.app) · [Automatic setup guide](setup.md)

## Set it up with your coding agent

Give this prompt to a coding agent with terminal access to the machine where your repositories live:

```text
Set up GitHub account router on this machine. Fetch and follow:
https://raw.githubusercontent.com/Sma-Das/multi-github-account/main/setup.md

Install ghr, discover my signed-in GitHub accounts and workspace repositories,
map my chosen folders to the right accounts, configure HTTPS Git routing,
verify account selection and remote access, and start the dashboard.
If I want remote access through Tailscale, configure Tailscale Serve and give
me the full HTTPS dashboard session URL.

Carry out the setup rather than just explaining it. Ask me only for missing
folder-to-account choices or steps that require my browser login or Tailscale
enablement. Use repository-scoped credentials instead of gh auth switch,
keep GitHub tokens out of output, and finish with my actual mappings,
verification results, dashboard URL, and agent launch commands.
```

Include your workspace paths and desired account mappings with the prompt to reduce questions. The full agent setup guide is in [setup.md](setup.md).

`ghr` routes GitHub authentication by local folder. Agents in different repositories can push and call the GitHub API at the same time, using different accounts, without running `gh auth switch`.

The CLI includes a local web dashboard for assigning folders and inspecting upstream remotes. It uses accounts already signed into GitHub CLI. There are no runtime Python dependencies.

## Interactive demo

[Open the public demo](https://github-account-router-demo.vercel.app) to try the dashboard before installing. It starts with three sample GitHub accounts, six folder routes, and eight discoverable repositories.

Add or edit routes, filter by account, scan sample workspaces, and generate agent launch commands. Changes are saved only in your browser. Use **Reset demo** to restore the sample data.

The demo is a static Vercel deployment. It shares the local dashboard's UI but uses a browser-only mock API. It has no GitHub login, machine filesystem, credential store, or backend API.

## Why account switching races

`gh auth switch` changes the active account in shared GitHub CLI configuration. Agent A switches to its account, agent B switches to another account, then agent A pushes with B's credentials. A lock around the switch alone does not fix this because the operation happens later.

`ghr` removes that shared write:

1. An HTTPS Git credential helper resolves the working folder to a hostname and account, then reads that account's credential with `gh auth token --hostname HOST --user USER`.
2. `ghr exec` starts each agent with its own `GH_TOKEN`, or `GH_ENTERPRISE_TOKEN`, and process-local Git helper configuration.
3. `ghr gh` runs GitHub CLI commands with the same folder-based selection.

Repository owners do not determine the login automatically. A repo might belong to an organization, or both accounts might have access. The mapping is explicit.

## Install

Requires macOS or Linux, Python 3.9+, Git 2.31+, and a current [GitHub CLI](https://cli.github.com/). The CLI must support `gh auth status --json hosts` and `gh auth token --user`.

### Homebrew

This repository also works as a custom Homebrew tap when you supply its URL:

```sh
brew tap Sma-Das/multi-github-account https://github.com/Sma-Das/multi-github-account.git
brew install --HEAD Sma-Das/multi-github-account/ghr
```

The HEAD formula installs the Python CLI, dashboard assets, and the `gh` dependency. It installs from `main`; there is no versioned release or Homebrew Core entry yet.

### From a checkout

```sh
brew install gh python@3.13
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/ghr --help
```

You can also use `pipx install .` if you already use pipx. Examples below assume `ghr` is on your PATH.

Install directly from this repository with pipx:

```sh
pipx install git+https://github.com/Sma-Das/multi-github-account.git
```

For a versioned Homebrew release, add a tagged source archive URL and its SHA-256 to `Formula/ghr.rb`. Test it with `brew test ghr` before publishing.

## Set up your accounts and folders

Log into each account once. GitHub CLI stores the credentials, usually in the OS keychain.

```sh
gh auth login --hostname github.com --git-protocol https
# Repeat for your second account, selecting that account in the browser.
ghr accounts

ghr map ~/GitHub/repo-1 --account user-1
ghr map ~/GitHub/repo-2 --account user-1
ghr map ~/GitHub/repo-3 --account user-2
ghr map ~/GitHub/repo-4 --account user-2
```

Or map parent folders:

```sh
ghr map ~/work/personal --account user-1
ghr map ~/work/company --account user-2
```

The longest matching folder path wins. An explicit repository mapping overrides a parent folder mapping. Symlinks resolve to their actual locations. Mapping a subfolder inside a checkout maps the checkout root.

Linked Git worktrees inherit the route of their primary checkout, including worktrees in `/tmp`. You can map a worktree explicitly to override that inheritance.

Mappings contain only paths, hostnames, and account names. They live in `~/.config/ghr/config.json`, or `$XDG_CONFIG_HOME/ghr/config.json`. Set `GHR_CONFIG` to use another file. Updates use a lock and atomic replacement so simultaneous UI and CLI changes do not lose mappings.

### Enable ordinary Git commands

Inside a repository, configure routing for just that repository:

```sh
ghr setup
git push
```

For automatic routing across all your HTTPS GitHub repositories:

```sh
ghr setup --global
```

This replaces the helper list for `https://github.com`, enables `credential.useHttpPath` for that host, and leaves other hosts alone. Unmapped folders stop with a mapping error. They do not fall back to your active `gh` account or another credential helper.

An existing repository-specific credential helper can override a global helper. In that repository, run `ghr setup`, or use `ghr exec`, whose process-local configuration takes precedence.

The installed helper records the Python installation path. Run setup again if you move or replace that installation.

`ghr map` saves a route. It does not edit Git configuration. `ghr exec` works without running `ghr setup`.

## Run agents concurrently

Start each agent for the repository it will work in:

```sh
# Terminal 1
ghr exec --path ~/GitHub/repo-1 -- claude

# Terminal 2
ghr exec --path ~/GitHub/repo-3 -- opencode
```

Each process gets its own credentials. Its child shells inherit them, so regular HTTPS `git push` and `gh pr create` use that account. A running process keeps its selected account when you edit the route in the dashboard. Restart or run another `ghr exec` to select a new route.

To run individual operations:

```sh
ghr gh --path ~/GitHub/repo-1 -- pr create
ghr exec --path ~/GitHub/repo-3 -- git push
ghr whoami --path ~/GitHub/repo-3
```

For agents launched outside this wrapper, put the instructions in `docs/agent-instructions.md` into your agent's repository instructions. Shell aliases and prompt hooks are unreliable in non-interactive agent shells.

### Account scope

An agent launched with `ghr exec` has one selected account and host. Launch separate processes for repositories with different accounts. The Git helper stops if that process targets an unrelated checkout on the routed host. If an agent operates on another repo using `git -C`, `gh --repo`, `GH_REPO`, or by changing directories, run that operation through a new `ghr exec --path TARGET` or `ghr gh --path TARGET` with an explicit mapping. Folder selection takes precedence over repository ownership.

Do not call `gh auth switch`, `login`, `logout`, or `setup-git` inside concurrent agents. Manage sign-ins directly with `gh` before launching agents. `ghr gh` rejects these account-management commands.

## Manage routes in the browser

```sh
ghr ui
```

The dashboard opens on `http://127.0.0.1:8765`. It shows stored accounts and folder routes. Scan a workspace to discover repositories and inspect fetch and push remotes, then assign accounts from the list. Scans skip dependency and hidden folders, check five levels deep, and stop at 200 repositories or 10,000 visited folders.

The server binds only to loopback. API requests require a random per-session bearer secret carried in the launch URL's fragment. The page removes the fragment and stores the dashboard session in that tab. The API rejects cross-origin requests and unexpected Host headers. GitHub tokens are never sent to the dashboard, written to its config, or returned by its account-list endpoint. No external scripts, styles, fonts, or analytics are loaded.

Use `ghr ui --no-browser --port 8766` for another port, or `--port 0` for an available port. Stop with Ctrl-C. The dashboard session URL authorizes mapping changes on the hosting machine.

### Access over Tailscale

Use Tailscale Serve to access the dashboard from another device on your tailnet without an SSH tunnel. MagicDNS supplies the hostname; Serve terminates HTTPS and proxies to the loopback server.

Find this machine's full `*.ts.net` DNS name with `tailscale status --json`, under `Self.DNSName`. Then, on the hosting machine:

```sh
ghr ui --no-browser --public-url https://YOUR-MACHINE.YOUR-TAILNET.ts.net
```

In another terminal on that same machine:

```sh
tailscale serve --bg http://127.0.0.1:8765
```

If Serve is disabled, Tailscale prints a link to enable it for your tailnet. Open the HTTPS session URL printed by `ghr ui` on your other device. Include its fragment, which contains the dashboard session secret. The other device must be connected to the tailnet and allowed to reach the hosting machine.

`--public-url` adds one exact trusted HTTPS origin and host. The backend still binds only to loopback, still checks its session secret, and does not trust arbitrary forwarded headers. Tailscale Serve limits network access to your tailnet.

Stop this Serve endpoint with `tailscale serve --https=443 off`. Mapping changes apply to folders and accounts on the hosting machine.

## Enterprise and SSH

For GitHub Enterprise Server:

```sh
gh auth login --hostname git.company.example
ghr map ~/work/company --host git.company.example --account work-user
ghr setup --global --host git.company.example
ghr gh --path ~/work/company/my-repo --host git.company.example -- pr list
```

Hosts on `*.ghe.com` use `GH_TOKEN`, following GitHub CLI's documented behavior. Other enterprise hosts use `GH_ENTERPRISE_TOKEN`. If the same folder has routes for multiple hosts, specify `--host`.

HTTPS Git authentication is supported. SSH Git authentication uses SSH keys instead of HTTPS credential helpers, so an SSH push does not use this router. You can keep SSH for Git and still use `ghr gh` for the API, or change the fetch and any separate push URL to HTTPS:

```sh
git remote set-url origin https://github.com/OWNER/REPO.git
# Only if your remote has an explicit push URL:
git remote set-url --push origin https://github.com/OWNER/REPO.git
```

The router does not change commit authorship. Use repository-local `user.name` and `user.email`, or Git's `includeIf`, for personal/work commit identities. Routing also does not resolve branch conflicts between agents pushing to the same branch. Give those agents separate branches and worktrees.

## Remove routing

Remove a mapping with `ghr unmap PATH --host github.com`. Remove host-specific helper configuration at the scope you configured:

```sh
git config --global --unset-all credential.https://github.com.helper
git config --global --unset-all credential.https://github.com.useHttpPath
# Use --local inside the repo if you used repository-scoped setup.
gh auth setup-git --hostname github.com
```

The setup command replaces that host's previous helper list. Restore any custom helper configuration from your own backup if you used one.

## Development and checks

```sh
python3 -m unittest discover -s tests -v
node --check ghr/web/app.js
ruby -c Formula/ghr.rb
```

Tests invoke the real Git credential protocol with an offline `gh` fixture. They exercise concurrent account selection, ambient-token removal, linked worktrees, process pinning, concurrent mapping writes, enterprise tokens, and dashboard authorization. They do not contact GitHub or push real branches.

### Build and deploy the demo

The demo build uses Node.js 22+ and no npm dependencies:

```sh
npm run test:demo
npm run build:demo
python3 -m http.server 8877 --bind 127.0.0.1 --directory demo/dist
```

Open `http://127.0.0.1:8877` to preview it. `scripts/build-demo.mjs` copies only the public UI assets and adds `demo/demo.js`, the mock API and sample data. The generated `demo/dist` directory is ignored by Git.

To deploy from the repository root with your own Vercel account:

```sh
vercel --prod
```

`vercel.json` configures the static build and output directory. The demo's Content Security Policy disallows network API connections. The local Python dashboard continues to use its authenticated API.

## Related work

- [GitHub CLI's multi-account design](https://github.com/cli/cli/blob/trunk/docs/multiple-accounts.md) describes the shared active account and the `gh auth token --user` automation hook. The document is historical; current environment behavior is in [the CLI manual](https://cli.github.com/manual/gh_help_environment).
- [ghctx](https://github.com/jasonwbarnett/ghctx) uses shell-local tokens and optional prompt hooks. It documents this same account-switching race. `ghr` uses an executable agent wrapper and a Git helper rather than depending on shell hooks, and adds a local mapping dashboard.
- SSH host aliases plus Git `includeIf` are useful for transport credentials and commit identity. `gh` API authentication still needs a process-specific token.
