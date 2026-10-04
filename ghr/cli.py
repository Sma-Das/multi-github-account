import argparse
import json
import os
import sys
from urllib.parse import urlencode

from . import __version__
from .core import (RouterError, accounts, add_mapping, canonical, credential,
                   process_env, read_config, remote_target, remove_mapping, repository,
                   resolve, scan, setup_helper, normalize_repo, validate_identity)
from .machines import add_machine, list_machines, remote_request, remove_machine


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ghr", description="Route GitHub credentials by folder, without gh auth switch.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("accounts", "List stored gh accounts without showing tokens"), ("list", "List folder mappings")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--machine", help="Read from a paired computer")
    mapping = sub.add_parser("map", help="Map a checkout or parent folder to a stored account")
    mapping.add_argument("path", nargs="?", default=".")
    mapping.add_argument("--account", required=True)
    mapping.add_argument("--machine", help="Save the route on a paired computer")
    mapping.add_argument("--host")
    group = mapping.add_mutually_exclusive_group()
    group.add_argument("--remote", help="Bind the account to a named remote's repository")
    group.add_argument("--repo", help="Bind the account to an exact OWNER/REPO target")
    unmap = sub.add_parser("unmap", help="Remove a folder mapping")
    unmap.add_argument("path", nargs="?", default=".")
    unmap.add_argument("--machine")
    unmap.add_argument("--host")
    group = unmap.add_mutually_exclusive_group()
    group.add_argument("--remote")
    group.add_argument("--repo")
    who = sub.add_parser("whoami", help="Explain the selected account and upstream remotes")
    who.add_argument("--path", default=".")
    who.add_argument("--host")
    group = who.add_mutually_exclusive_group()
    group.add_argument("--remote")
    group.add_argument("--repo")
    setup = sub.add_parser("setup", help="Configure ordinary HTTPS git commands to use the router")
    setup.add_argument("--global", dest="global_scope", action="store_true", help="Apply to all checkouts for this host")
    setup.add_argument("--host", default="github.com")
    setup.add_argument("--path", default=".")
    for name in ("exec", "gh"):
        cmd = sub.add_parser(name, help="Run an agent/command with isolated credentials" if name == "exec" else "Run gh with the mapped account")
        cmd.add_argument("--path", default=".")
        cmd.add_argument("--host")
        cmd.add_argument("--account", help="Use a stored account for this command without changing any route")
        group = cmd.add_mutually_exclusive_group()
        group.add_argument("--remote", help="Select credentials and gh repository from a named remote")
        group.add_argument("--repo", help="Select credentials and gh repository for OWNER/REPO")
        cmd.add_argument("args", nargs=argparse.REMAINDER)
    helper = sub.add_parser("credential", help=argparse.SUPPRESS)
    helper.add_argument("action", choices=("get", "store", "erase"))
    finder = sub.add_parser("scan", help="Find checkouts under a folder")
    finder.add_argument("path", nargs="?", default=".")
    finder.add_argument("--machine")
    machines = sub.add_parser("machines", help="Pair and manage other computers' dashboards")
    machine_commands = machines.add_subparsers(dest="machine_action", required=True)
    machine_commands.add_parser("list")
    pair = machine_commands.add_parser("add")
    pair.add_argument("name")
    pair.add_argument("--url", required=True, help="Full dashboard session URL, including its fragment")
    forget = machine_commands.add_parser("remove")
    forget.add_argument("name")
    ui = sub.add_parser("ui", help="Open the local account mapping dashboard")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-browser", action="store_true")
    ui.add_argument("--persistent-session", action="store_true", help="Keep the dashboard session across restarts for paired computers")
    ui.add_argument("--public-url", help="Trusted HTTPS origin of a reverse proxy, such as Tailscale Serve")
    args = parser.parse_args(argv)
    try:
        if args.command == "machines":
            if args.machine_action == "list":
                result = list_machines()
            elif args.machine_action == "add":
                result = add_machine(args.name, args.url)
            else:
                remove_machine(args.name)
                result = {"removed": args.name}
            print(json.dumps(result, indent=2))
            return
        if getattr(args, "machine", None):
            if args.command in ("accounts", "list"):
                data = remote_request(args.machine, "/api/state")
                result = data["accounts" if args.command == "accounts" else "mappings"]
            elif args.command == "scan":
                result = remote_request(args.machine, "/api/scan?" + urlencode({"path": args.path}))["repositories"]
            else:
                if args.remote:
                    raise RouterError("Remote management uses --repo OWNER/REPO. A remote name is resolved on its own computer.")
                data = {"path": args.path, "host": args.host or "github.com"}
                if args.repo:
                    data["repo"] = args.repo
                if args.command == "map":
                    data["account"] = args.account
                result = remote_request(args.machine, "/api/mappings", "PUT" if args.command == "map" else "DELETE", data)
            print(json.dumps(result, indent=2))
            return
        if args.command == "accounts":
            result = accounts()
        elif args.command == "list":
            result = read_config()["mappings"]
        elif args.command == "map":
            host, target = remote_target(args.path, args.remote, args.host) if args.remote else (args.host or "github.com", args.repo)
            result = add_mapping(args.path, host, args.account, target)
        elif args.command == "unmap":
            host, target = remote_target(args.path, args.remote, args.host) if args.remote else (args.host or "github.com", args.repo)
            remove_mapping(args.path, host, target)
            result = {"removed": str(canonical(args.path)), "host": host}
        elif args.command == "whoami":
            host, target = remote_target(args.path, args.remote, args.host) if args.remote else (args.host, args.repo)
            result = resolve(args.path, host, target=target)
        elif args.command == "setup":
            setup_helper(args.host, args.global_scope, args.path)
            result = {"configured": args.host, "scope": "global" if args.global_scope else "repository"}
        elif args.command == "credential":
            sys.exit(credential(args.action, sys.stdin, sys.stdout))
        elif args.command == "scan":
            result = scan(args.path)
        elif args.command == "ui":
            from .server import serve
            serve(args.port, not args.no_browser, args.public_url, args.persistent_session)
            return
        else:
            command = args.args
            if command and command[0] == "--":
                command = command[1:]
            if args.command == "gh":
                command = ["gh", *command]
                if command[1:2] == ["auth"] and command[2:3] in (["switch"], ["login"], ["logout"], ["setup-git"]):
                    raise RouterError("Run account management directly with gh. Routed commands must not change shared auth state.")
            if not command:
                raise RouterError("Supply a command after --, for example: ghr exec -- claude")
            path = canonical(args.path)
            host, target = remote_target(path, args.remote, args.host) if args.remote else (args.host, args.repo)
            if args.account:
                host = host or "github.com"
                validate_identity(host, args.account)
                mapping = {"path": str(path), "host": host, "account": args.account,
                           "repository": repository(path), "override": True}
                if target:
                    mapping["repo"] = normalize_repo(target)
            else:
                mapping = resolve(path, host, target=target)
            if target:
                mapping["api_repo"] = normalize_repo(target)
            env = process_env(mapping)
            os.chdir(path)
            os.execvpe(command[0], command, env)
            return
        print(json.dumps(result, indent=2))
    except (RouterError, OSError) as error:
        print(f"ghr: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
