"""Mapping persistence, repository discovery, and account-specific credentials."""

import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit


class RouterError(Exception):
    pass


TOKEN_VARS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")


def clean_env():
    env = os.environ.copy()
    for key in TOKEN_VARS:
        env.pop(key, None)
    # gh debug output may include HTTP authorization headers.
    env.pop("GH_DEBUG", None)
    env.pop("DEBUG", None)
    return env


def capture(command, cwd=None, env=None):
    try:
        result = subprocess.run(command, cwd=cwd, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RouterError(f"Could not run {command[0]}. Check that it is installed and responsive.") from error
    return result


def canonical(path):
    return Path(path).expanduser().resolve()


def config_path():
    override = os.environ.get("GHR_CONFIG")
    if override:
        return canonical(override)
    root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return root / "ghr" / "config.json"


def read_config():
    try:
        data = json.loads(config_path().read_text())
    except FileNotFoundError:
        return {"version": 1, "mappings": []}
    except (OSError, ValueError) as error:
        raise RouterError("Cannot read ghr config. Repair the JSON before continuing.") from error
    if (not isinstance(data, dict) or data.get("version") != 1
            or not isinstance(data.get("mappings"), list)):
        raise RouterError("Unsupported ghr config format.")
    for mapping in data["mappings"]:
        if (not isinstance(mapping, dict)
                or not all(isinstance(mapping.get(k), str) for k in ("path", "host", "account"))
                or not Path(mapping["path"]).is_absolute()):
            raise RouterError("Invalid mapping in ghr config.")
        validate_identity(mapping["host"], mapping["account"])
        if "repo" in mapping:
            mapping["repo"] = normalize_repo(mapping["repo"])
    return data


@contextlib.contextmanager
def edit_config():
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Lock a stable sibling, not the JSON inode which is replaced on every write.
    fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_config()
        yield data
        tmp_fd, tmp_name = tempfile.mkstemp(prefix=".config-", dir=path.parent)
        try:
            with os.fdopen(tmp_fd, "w") as stream:
                json.dump(data, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)


def validate_identity(host, account):
    if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", host):
        raise RouterError("Use a hostname such as github.com, without a scheme, path, or port.")
    if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_-]*", account):
        raise RouterError("Invalid GitHub account name.")


def normalize_repo(value):
    if not isinstance(value, str):
        raise RouterError("Use a remote repository in OWNER/REPO format.")
    value = unquote(value).strip("/").removesuffix(".git").lower()
    if (not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", value)
            or any(part in (".", "..") for part in value.split("/"))):
        raise RouterError("Use a remote repository in OWNER/REPO format.")
    return value


def remote_target(path, name, host=None):
    info = repository(canonical(path))
    if not info:
        raise RouterError("Select a remote from inside a Git checkout.")
    targets = {(r["host"], normalize_repo(r["repo"])) for r in info["remotes"]
               if r["name"] == name and (host is None or r["host"] == host.lower())}
    if len(targets) != 1:
        raise RouterError("That remote is missing or has different fetch/push targets. Use --host and --repo OWNER/REPO to select an exact target.")
    hostname, target = next(iter(targets))
    return hostname, target


def accounts():
    result = capture(["gh", "auth", "status", "--json", "hosts"], env=clean_env())
    try:
        hosts = json.loads(result.stdout)["hosts"]
        # Explicitly allowlist fields. Never relay token-bearing gh output to the UI.
        return [{"host": host, "account": item["login"], "state": item.get("state", "unknown"),
                 "active": bool(item.get("active")), "source": item.get("tokenSource", "unknown")}
                for host, items in hosts.items() for item in items if item.get("login")]
    except (ValueError, KeyError, TypeError) as error:
        raise RouterError("Cannot list accounts. Install a current gh and run gh auth login for each account.") from error


def token_for(host, account):
    validate_identity(host, account)
    result = capture(["gh", "auth", "token", "--hostname", host, "--user", account], env=clean_env())
    token = result.stdout.strip()
    if result.returncode or not token or "\n" in token or "\r" in token:
        raise RouterError(f"No stored credential for {account} on {host}. Run gh auth login --hostname {host}.")
    return token


def git(path, *args):
    return capture(["git", "-C", str(path), *args])


def parse_remote(url):
    if "://" in url:
        parsed = urlsplit(url)
        if parsed.scheme not in ("https", "ssh") or not parsed.hostname:
            return None
        host, path, protocol = parsed.hostname.lower(), parsed.path, parsed.scheme
    else:
        match = re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", url)
        if not match:
            return None
        host, path, protocol = match[1].lower(), match[2], "ssh"
    path = path.strip("/").removesuffix(".git")
    if len(path.split("/")) != 2 or any(p in ("", ".", "..") for p in path.split("/")):
        return None
    # Remote URLs can contain a PAT in userinfo. Return only routing metadata.
    return {"host": host, "repo": path, "protocol": protocol}


def repository(path):
    root_result = git(path, "rev-parse", "--show-toplevel")
    if root_result.returncode:
        return None
    root = canonical(root_result.stdout.strip())
    common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    common_dir = canonical(common.stdout.strip()) if not common.returncode else root / ".git"
    # Linked worktrees inherit the mapping of their primary checkout.
    primary = common_dir.parent if common_dir.name == ".git" else root
    remotes = []
    names = git(root, "remote")
    for name in names.stdout.splitlines():
        for direction, flags in (("fetch", []), ("push", ["--push"])):
            urls = git(root, "remote", "get-url", *flags, "--all", name)
            for url in urls.stdout.splitlines():
                remote = parse_remote(url)
                if remote:
                    remotes.append(dict(remote, name=name, direction=direction))
    return {"path": str(root), "primary": str(primary), "remotes": remotes}


def is_within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve(path, host=None, use_pin=False, target=None):
    path = canonical(path)
    repo = repository(path)
    target = normalize_repo(target) if target else None
    pinned = {k: os.environ.get("GHR_PINNED_" + k.upper()) for k in ("path", "host", "account")}
    if use_pin and all(pinned.values()):
        validate_identity(pinned["host"], pinned["account"])
        if (host is None or host.lower() == pinned["host"]) and any(
                is_within(candidate, canonical(pinned["path"]))
                for candidate in [path, canonical(repo["primary"]) if repo else path]):
            if os.environ.get("GHR_PINNED_OVERRIDE") != "1":
                try:
                    routes = json.loads(os.environ.get("GHR_PINNED_REMOTES", "[]"))
                except ValueError as error:
                    raise RouterError("Invalid process-scoped remote routes.") from error
                if not isinstance(routes, list) or any(not isinstance(r, dict) or not isinstance(r.get("repo"), str)
                                                     or not isinstance(r.get("account"), str) for r in routes):
                    raise RouterError("Invalid process-scoped remote routes.")
                if routes:
                    for route in routes:
                        if route["repo"] == target:
                            validate_identity(pinned["host"], route["account"])
                            return dict(pinned, account=route["account"], repo=target, repository=repo, inherited=False)
                    raise RouterError("No account mapping for this remote. Use ghr map --remote NAME --account USER, or an explicit --account for this operation.")
            return dict(pinned, repository=repo, inherited=False)
        raise RouterError("This process is scoped to another checkout. Run ghr exec --path TARGET -- COMMAND for that repository.")
    mappings = read_config()["mappings"]
    candidates = [path]
    if repo and canonical(repo["primary"]) != path:
        candidates.append(canonical(repo["primary"]))
        # A broad parent-folder route must not hide the primary checkout's route.
        explicit = any(is_within(path, canonical(m["path"]))
                       and is_within(canonical(m["path"]), canonical(repo["path"]))
                       and (host is None or m["host"] == host.lower()) for m in mappings)
        if not explicit:
            candidates.reverse()
    for candidate in candidates:
        matches = [m for m in mappings if is_within(candidate, canonical(m["path"]))
                   and (host is None or m["host"] == host.lower())]
        if matches:
            matches.sort(key=lambda m: len(canonical(m["path"]).parts), reverse=True)
            longest = len(canonical(matches[0]["path"]).parts)
            best = [m for m in matches if len(canonical(m["path"]).parts) == longest]
            if len({m["host"] for m in best}) != 1:
                raise RouterError("This folder has mappings for multiple hosts. Pass --host to select one.")
            scoped = [m for m in best if m.get("repo")]
            choices = [m for m in scoped if normalize_repo(m["repo"]) == target] if target and scoped else [m for m in best if not m.get("repo")]
            if len(choices) != 1:
                if target:
                    raise RouterError(f"No account mapping for remote {target}. Use ghr map --repo {target} --account USER.")
                raise RouterError("Select a remote-specific route with --remote NAME or --repo OWNER/REPO, or set a default folder route.")
            mapping = choices[0]
            if repo and repo["remotes"] and not any(r["host"] == mapping["host"] for r in repo["remotes"]):
                raise RouterError("The mapped hostname does not match any remote. SSH aliases need HTTPS remotes.")
            return dict(mapping, repository=repo, inherited=candidate != path)
    raise RouterError(f"No account mapping for {path}. Run ghr map {shlex.quote(str(path))} --account USER.")


def add_mapping(path, host, account, target=None):
    path = canonical(path)
    host = host.lower()
    validate_identity(host, account)
    target = normalize_repo(target) if target is not None else None
    if not path.is_dir():
        raise RouterError("The folder must exist on this machine.")
    if not any(a["host"] == host and a["account"] == account and a["state"] == "success" for a in accounts()):
        raise RouterError(f"{account} is not signed in successfully on {host}. Run gh auth login --hostname {host}.")
    # A path inside a checkout maps the whole checkout, including its worktrees.
    repo = repository(path)
    if repo:
        path = canonical(repo["path"])
        if repo["remotes"] and not any(r["host"] == host for r in repo["remotes"]):
            raise RouterError("That hostname does not match this repository's remotes.")
        if target and not any(r["host"] == host and normalize_repo(r["repo"]) == target for r in repo["remotes"]):
            raise RouterError("That repository does not match a remote in this checkout.")
    elif target:
        raise RouterError("Remote-specific routes must belong to a Git checkout, not a parent folder.")
    mapping = {"path": str(path), "host": host, "account": account}
    if target:
        mapping["repo"] = target
    with edit_config() as data:
        data["mappings"] = [m for m in data["mappings"] if (m["path"], m["host"], m.get("repo")) != (str(path), host, target)]
        data["mappings"].append(mapping)
        data["mappings"].sort(key=lambda m: (m["path"], m["host"], m.get("repo", "")))
    return mapping


def remove_mapping(path, host, target=None):
    path = str(canonical(path))
    target = normalize_repo(target) if target is not None else None
    with edit_config() as data:
        data["mappings"] = [m for m in data["mappings"] if (m["path"], m["host"], m.get("repo")) != (path, host.lower(), target)]


def helper_command():
    # The module's installation path must work even without a venv on PATH.
    root = str(Path(__file__).resolve().parent.parent)
    return "!" + shlex.join([sys.executable, "-c",
        f"import sys; sys.path.insert(0, {root!r}); from ghr.cli import main; main()",
        "credential"])


def credential(action, stream, output):
    if action != "get":
        return 0  # gh owns storage and revocation; Git must not persist another copy.
    fields = {}
    for line in stream:
        line = line.rstrip("\n")
        if not line:
            break
        key, sep, value = line.partition("=")
        if sep:
            fields[key] = value
    try:
        host = fields.get("host", "").lower()
        if fields.get("protocol") != "https":
            raise RouterError("ghr supports HTTPS Git authentication only.")
        mapping = resolve(Path.cwd(), host, use_pin=True, target=fields.get("path"))
        token = token_for(host, mapping["account"])
        output.write(f"username={mapping['account']}\npassword={token}\n\n")
        return 0
    except RouterError as error:
        print(f"ghr: {error}", file=sys.stderr)
        # Stop Git from falling back to another helper or prompting for a wrong account.
        output.write("quit=true\n\n")
        return 1


def setup_helper(host, global_scope=False, path="."):
    validate_identity(host, "valid")
    prefix = ["git", "config", "--global"] if global_scope else ["git", "-C", str(canonical(path)), "config", "--local"]
    key = f"credential.https://{host}.helper"
    # An empty helper resets the accumulated helper list, including osxkeychain and gh.
    result = capture(prefix + ["--replace-all", key, ""])
    if result.returncode:
        raise RouterError("Could not configure Git credentials. Run inside a checkout or use --global.")
    for args in (["--add", key, helper_command()],
                 ["--replace-all", f"credential.https://{host}.useHttpPath", "true"]):
        if capture(prefix + args).returncode:
            raise RouterError("Could not finish Git credential configuration. Run ghr setup again.")


def process_env(mapping):
    env = clean_env()
    # A relative override must still refer to the same file after the child chdir.
    env["GHR_CONFIG"] = str(config_path())
    host = mapping["host"]
    token = token_for(host, mapping["account"])
    key = "GH_TOKEN" if host == "github.com" or host.endswith(".ghe.com") else "GH_ENTERPRISE_TOKEN"
    env[key] = token
    env["GH_HOST"] = host
    env["GIT_TERMINAL_PROMPT"] = "0"
    # Pin the route for this process tree without editing the user's mapping file.
    env["GHR_PINNED_PATH"] = mapping["repository"]["path"] if mapping.get("repository") else mapping["path"]
    env["GHR_PINNED_HOST"] = host
    env["GHR_PINNED_ACCOUNT"] = mapping["account"]
    env["GHR_PINNED_OVERRIDE"] = "1" if mapping.get("override") else "0"
    roots = [canonical(env["GHR_PINNED_PATH"])]
    if mapping.get("repository"):
        roots.append(canonical(mapping["repository"]["primary"]))
    configured = [] if mapping.get("override") else read_config()["mappings"]
    targets = {m["repo"] for m in configured if m.get("repo") and m["host"] == host
               and any(is_within(root, canonical(m["path"])) for root in roots)}
    pinned_remotes = []
    for target in targets:
        selected = resolve(env["GHR_PINNED_PATH"], host, target=target)
        if selected.get("repo"):
            pinned_remotes.append({"repo": normalize_repo(target), "account": selected["account"]})
    env["GHR_PINNED_REMOTES"] = json.dumps(pinned_remotes)
    env.pop("GH_REPO", None)
    api_repo = mapping.get("api_repo") or mapping.get("repo")
    if api_repo:
        env["GH_REPO"] = f"{host}/{normalize_repo(api_repo)}"
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError as error:
        raise RouterError("GIT_CONFIG_COUNT must be an integer.") from error
    if count < 0:
        raise RouterError("GIT_CONFIG_COUNT must be nonnegative.")
    entries = [(f"credential.https://{host}.helper", ""),
               (f"credential.https://{host}.helper", helper_command()),
               (f"credential.https://{host}.useHttpPath", "true")]
    for offset, (name, value) in enumerate(entries, count):
        env[f"GIT_CONFIG_KEY_{offset}"] = name
        env[f"GIT_CONFIG_VALUE_{offset}"] = value
    env["GIT_CONFIG_COUNT"] = str(count + len(entries))
    return env


def scan(path, depth=5):
    path = canonical(path)
    if not path.is_dir():
        raise RouterError("Scan folder does not exist.")
    found = []
    skip = {"node_modules", "vendor", "dist", "build", "target", "Library"}
    visited = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        visited += 1
        if visited > 10000:
            break
        if ".git" in dirs or ".git" in files:
            repo = repository(root)
            if repo:
                try:
                    route = resolve(root)
                    repo["account"] = route["account"]
                except RouterError:
                    repo["account"] = None
                for remote in repo["remotes"]:
                    try:
                        remote["account"] = resolve(root, remote["host"], target=remote["repo"])["account"]
                    except RouterError:
                        remote["account"] = None
                found.append(repo)
            dirs[:] = []
        else:
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in skip)
            if len(Path(root).relative_to(path).parts) >= depth:
                dirs[:] = []
        if len(found) >= 200:
            break
    return found
