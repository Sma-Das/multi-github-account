# GitHub authentication for agents

This machine uses `ghr` to route GitHub credentials by repository folder.

Computers have independent account stores and folder paths. Use `ghr machines list` to inspect paired dashboards, and `ghr accounts --machine NAME`, `ghr list --machine NAME`, or `ghr scan --machine NAME '~/Projects'` for remote management. Agent and Git commands execute on the computer that owns the checkout.

- Run `ghr whoami --path /absolute/path/to/repo` to inspect the selected account and remotes.
- For API operations use `ghr gh --path /absolute/path/to/repo -- pr create`, or the corresponding `gh` subcommand.
- For HTTPS Git operations use `ghr exec --path /absolute/path/to/repo -- git push`.
- When an entire agent starts through `ghr exec --path REPO -- AGENT`, its regular HTTPS Git and `gh` commands inherit the selected credentials. Work within that account scope.
- When targeting another repository or account, use a fresh `ghr exec --path TARGET -- COMMAND` or `ghr gh --path TARGET -- SUBCOMMAND`.
- A migration checkout may use multiple accounts. Use `ghr gh --path REPO --remote SOURCE_OR_DESTINATION -- SUBCOMMAND` to select both the API account and repository. Ordinary HTTPS Git operations use the account assigned to the actual remote URL.
- Use `--repo OWNER/REPO` for a remote with different fetch/push URLs. Use `ghr exec --path REPO --account USER -- COMMAND` for a temporary explicit account override.
- Never run `gh auth switch`. Do not perform shared account management inside concurrent agents.
- If no mapping exists, ask the user to map the repository with `ghr map REPO --account USER`. Do not guess from the repository owner or retry with another account.
- SSH remotes use SSH keys, not this HTTPS credential helper.
- Use a separate branch and worktree for each agent modifying the same repository.
