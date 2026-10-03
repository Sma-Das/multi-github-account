import argparse
import json
import os
import sys

from . import __version__
from .core import (RouterError, accounts, add_mapping, canonical, credential,
                   process_env, read_config, remove_mapping, resolve, scan, setup_helper)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ghr", description="Route GitHub credentials by folder, without gh auth switch.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("accounts", help="List stored gh accounts without showing tokens")
    sub.add_parser("list", help="List folder mappings")
    mapping = sub.add_parser("map", help="Map a checkout or parent folder to a stored account")
    mapping.add_argument("path", nargs="?", default=".")
    mapping.add_argument("--account", required=True)
    mapping.add_argument("--host", default="github.com")
    unmap = sub.add_parser("unmap", help="Remove a folder mapping")
    unmap.add_argument("path", nargs="?", default=".")
    unmap.add_argument("--host", default="github.com")
    who = sub.add_parser("whoami", help="Explain the selected account and upstream remotes")
    who.add_argument("--path", default=".")
    who.add_argument("--host")
    setup = sub.add_parser("setup", help="Configure ordinary HTTPS git commands to use the router")
    setup.add_argument("--global", dest="global_scope", action="store_true", help="Apply to all checkouts for this host")
    setup.add_argument("--host", default="github.com")
    setup.add_argument("--path", default=".")
    for name in ("exec", "gh"):
        cmd = sub.add_parser(name, help="Run an agent/command with isolated credentials" if name == "exec" else "Run gh with the mapped account")
        cmd.add_argument("--path", default=".")
        cmd.add_argument("--host")
        cmd.add_argument("args", nargs=argparse.REMAINDER)
    helper = sub.add_parser("credential", help=argparse.SUPPRESS)
    helper.add_argument("action", choices=("get", "store", "erase"))
    finder = sub.add_parser("scan", help="Find checkouts under a folder")
    finder.add_argument("path", nargs="?", default=".")
    ui = sub.add_parser("ui", help="Open the local account mapping dashboard")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-browser", action="store_true")
    ui.add_argument("--public-url", help="Trusted HTTPS origin of a reverse proxy, such as Tailscale Serve")
    args = parser.parse_args(argv)
    try:
        if args.command == "accounts":
            result = accounts()
        elif args.command == "list":
            result = read_config()["mappings"]
        elif args.command == "map":
            result = add_mapping(args.path, args.host, args.account)
        elif args.command == "unmap":
            remove_mapping(args.path, args.host)
            result = {"removed": str(canonical(args.path)), "host": args.host}
        elif args.command == "whoami":
            result = resolve(args.path, args.host)
        elif args.command == "setup":
            setup_helper(args.host, args.global_scope, args.path)
            result = {"configured": args.host, "scope": "global" if args.global_scope else "repository"}
        elif args.command == "credential":
            sys.exit(credential(args.action, sys.stdin, sys.stdout))
        elif args.command == "scan":
            result = scan(args.path)
        elif args.command == "ui":
            from .server import serve
            serve(args.port, not args.no_browser, args.public_url)
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
            mapping = resolve(path, args.host)
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
