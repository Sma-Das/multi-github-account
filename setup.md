# Automatic setup for coding agents

These instructions are for a model with terminal access to the machine that owns the repositories and GitHub credentials. Carry out the setup, verify the result, and give the user the dashboard URL and commands for launching their agents.

Use the user's existing account and folder choices. Ask for missing choices when multiple accounts could apply. Browser sign-in and one-time Tailscale enablement may require the user to complete a step; continue with independent work while waiting.

## 1. Inspect the machine

Check the operating system and whether `ghr`, `gh`, Git, Homebrew, Python, pipx, and Tailscale are available. Reuse an existing working installation. Read the [README](https://github.com/Sma-Das/multi-github-account/blob/main/README.md) for the current commands and behavior.

For SSH setup, inspect the effective SSH configuration and use the user's exact alias. An alias such as `sma.local` may map to a LAN address, another username, and a specific identity file. On macOS, include Homebrew's binary directory in the command environment. Keychain credentials can be available in the logged-in GUI session while unavailable over SSH; run the dashboard and seeding service in that user's GUI launchd domain when that is the case.

If the user already supplied workspace folders, scan those. Otherwise, look for existing workspace folders such as `~/GitHub`, `~/Projects`, or `~/work`, and ask which ones they want managed. Keep discovery within those folders.

## 2. Install the router

Prefer Homebrew when it is already installed:

```sh
brew tap Sma-Das/multi-github-account https://github.com/Sma-Das/multi-github-account.git
brew install --HEAD Sma-Das/multi-github-account/ghr
```

The formula installs GitHub CLI and Python as dependencies. If `ghr` is already installed through this tap but lacks the needed commands, update it with:

```sh
brew upgrade --fetch-HEAD Sma-Das/multi-github-account/ghr
```

If Homebrew is unavailable and pipx is installed, use:

```sh
pipx install git+https://github.com/Sma-Das/multi-github-account.git
```

For an existing pipx installation, use `pipx upgrade github-account-router`. Ensure pipx's executable directory is on PATH for the current session as well as future shells.

If neither installer is available, use an existing Python 3.9+ to create a virtual environment in the user's application directory and install from GitHub:

```sh
python3 -m venv "$HOME/.local/share/ghr/venv"
"$HOME/.local/share/ghr/venv/bin/python" -m pip install git+https://github.com/Sma-Das/multi-github-account.git
```

Use that environment's `bin/ghr` executable in subsequent commands, or add a launcher in the user's executable directory. Install GitHub CLI using the operating system's supported package manager if it is missing. Git 2.31+ is required.

Verify that the executable resolves and the dashboard options are available:

```sh
ghr --version
ghr ui --help
```

## 3. Discover signed-in accounts

```sh
ghr accounts
ghr list
```

`ghr accounts` lists stored GitHub CLI accounts without revealing tokens, even if the current shell has an ambient `GH_TOKEN`. Preserve existing routes that already match the user's choices.

If an intended account is missing or reports an error, have the user complete a GitHub CLI browser login for that host. Clear ambient token and debug variables for this command so it can update the stored account:

```sh
env -u GH_TOKEN -u GITHUB_TOKEN -u GH_ENTERPRISE_TOKEN -u GITHUB_ENTERPRISE_TOKEN \
  -u GH_DEBUG -u DEBUG gh auth login --hostname github.com --git-protocol https --web
```

Repeat for additional accounts, choosing the intended account in the browser each time. Substitute the actual hostname for GitHub Enterprise. Re-run `ghr accounts` afterward.

Manage sign-ins before launching concurrent agents. Account routing uses stored credentials by username; never use `gh auth switch` as part of setup or agent operation. Keep tokens out of terminal output, files, chat, and repository instructions.

## 4. Assign folders to accounts

Scan each workspace:

```sh
ghr scan /absolute/path/to/workspace
```

The output includes checkout paths, fetch and push remotes, and existing routes. Scans stop at 200 repositories and check five levels deep. Scan deeper subfolders separately if needed.

Use the user's folder-to-account choices. A repository owner may be an organization, and multiple accounts may have access to the same repository. If the correct account is ambiguous, ask for that mapping rather than guessing from the owner.

Map individual checkouts or parent folders using the real paths, hosts, and signed-in usernames:

```sh
ghr map /absolute/path/to/personal-workspace --host github.com --account PERSONAL_USER
ghr map /absolute/path/to/company-workspace --host github.com --account WORK_USER
```

The longest matching folder wins. Explicit checkout routes override parent routes. Linked worktrees inherit their primary checkout's route. Mapping a subfolder inside a checkout maps the whole checkout, so use the checkout root when explaining the result.

Inspect each checkout's remotes separately. Migration work may require multiple accounts in the same folder. Set its default account, then bind each GitHub remote with `ghr map REPO --remote NAME --account USER`. Use `--repo OWNER/REPO` if fetch and push URLs differ under one remote name. Once a host has remote-specific bindings in a checkout, all Git targets on that host require an explicit binding. If the source account is not known or cannot access the upstream, leave that remote unassigned and report the missing credential instead of mapping it to the destination account.

HTTPS Git remotes use this router. SSH remotes use SSH keys. If the user wants Git routing for an SSH checkout, change its fetch and any separate push URLs to HTTPS while preserving their host, owner, and repository. Otherwise, retain SSH and use the router for GitHub API commands and agent launches.

## 5. Configure and verify Git routing

For each chosen checkout, configure its host-specific helper:

```sh
ghr setup --path /absolute/path/to/repo --host github.com
```

For parent-folder mappings, apply setup to the checkouts discovered beneath those folders. Future checkouts can use `ghr exec` immediately or run their own `ghr setup`.

If the user requested machine-wide Git routing, configure each intended host globally instead:

```sh
ghr setup --global --host github.com
```

Global setup routes ordinary HTTPS Git commands on that host and stops unmapped folders. Repository-specific helpers can override global configuration; apply repository-scoped setup to those checkouts when needed.

For every chosen checkout, inspect the route, verify the API account, and test read-only remote access. Substitute the actual host and remote name:

```sh
ghr whoami --path /absolute/path/to/repo --host github.com
ghr gh --path /absolute/path/to/repo --host github.com -- api /user --jq .login
ghr exec --path /absolute/path/to/repo --host github.com -- git ls-remote origin HEAD
```

The API login must match the selected account. A public remote can be read anonymously, so a successful `ls-remote` alone does not verify account selection or push permission. Report what was actually checked. If validation fails, fix the sign-in, mapping, host, or remote before proceeding.

## 6. Launch the dashboard

For a browser on the hosting machine:

```sh
ghr ui
```

For a user on another machine with Tailscale, first inspect `tailscale status --json` for `Self.DNSName` and `tailscale serve status` for existing services. Use the full DNS name without its trailing dot. Start the dashboard with that HTTPS origin:

```sh
ghr ui --no-browser --public-url https://YOUR-MACHINE.YOUR-TAILNET.ts.net
```

In another terminal on the hosting machine, start Tailscale Serve:

```sh
tailscale serve --bg http://127.0.0.1:8765
```

If Serve prints an enablement link, give it to the user and resume once they have completed that step. Preserve existing Serve services. If HTTPS port 443 is already assigned, choose a separate supported HTTPS port and include that port in `--public-url` and `tailscale serve --https=PORT`.

For a persistent background preview, use the machine's process manager or a detached process with a local log. Capture the actual session URL printed by `ghr ui`; the fragment is required to authenticate the dashboard. Give that full URL to the user. Check that both the page and authenticated API respond at the selected address. The receiving device must be connected to the tailnet and allowed to reach the host.

If the user has multiple computers, repeat installation and account verification on each one. Start paired dashboards with `--persistent-session`, then use `ghr machines add NAME --url FULL_SESSION_URL` on their chosen hub. Verify `ghr accounts --machine NAME` and `ghr list --machine NAME`, and switch computers in the dashboard. Use quoted remote paths such as `'~/Projects'`; a shell-expanded home path from the hub is not the other computer's home. Keep GitHub tokens on the machine owning the repositories.

## 7. Set up agent usage and report the result

Give the user launch commands for their actual repositories and agent executables:

```sh
ghr exec --path /absolute/path/to/personal-repo -- claude
ghr exec --path /absolute/path/to/company-repo -- opencode
```

Each process has one selected account and host. An operation targeting another account must use a fresh `ghr exec --path TARGET -- COMMAND` or `ghr gh --path TARGET -- SUBCOMMAND`. Plain `gh` outside a wrapped process still uses its usual account selection.

Configured Git remotes can use different identities inside one wrapped agent. For API operations in migration checkouts, give the agent `ghr gh --path REPO --remote NAME -- SUBCOMMAND` commands. A temporary `--account USER` override selects an account for just that process and does not rewrite its saved routes.

Use [docs/agent-instructions.md](https://github.com/Sma-Das/multi-github-account/blob/main/docs/agent-instructions.md) as the text for the user's existing repository-level agent instructions. Merge the relevant instructions into that file if the user wants automatic routing for agents already launched elsewhere.

Finish with a short report containing:

- The installed executable and version.
- The folder-to-account mappings and Git configuration scope.
- The verification results and any outstanding browser sign-in or Tailscale step.
- The full dashboard session URL and how to stop its process.
- Agent launch commands using the user's real paths.
