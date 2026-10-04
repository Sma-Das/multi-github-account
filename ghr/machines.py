"""Paired dashboards. Only management metadata crosses machine boundaries."""
import fcntl
import json
import os
import re
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .core import RouterError, config_path


def registry_path():
    return config_path().parent / "machines.json"


def read_registry():
    try:
        data = json.loads(registry_path().read_text())
    except FileNotFoundError:
        return {"version": 1, "machines": []}
    except (OSError, ValueError) as error:
        raise RouterError("Cannot read the machine registry.") from error
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("machines"), list):
        raise RouterError("Unsupported machine registry format.")
    for machine in data["machines"]:
        if not isinstance(machine, dict) or not all(isinstance(machine.get(k), str) for k in ("name", "url", "session")):
            raise RouterError("Invalid paired machine in the registry.")
        parse_session_url(machine["name"], machine["url"] + "/#" + machine["session"])
    return data


def public_machine(machine):
    return {key: machine[key] for key in ("name", "url")}


def list_machines():
    return [public_machine(m) for m in read_registry()["machines"]]


def find_machine(name):
    for machine in read_registry()["machines"]:
        if machine["name"] == name:
            return machine
    raise RouterError(f"Unknown machine {name}. Pair it with ghr machines add.")


def parse_session_url(name, url):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", name) or name == "local":
        raise RouterError("Use a short machine name with lowercase letters, digits, hyphens, or underscores. 'local' is reserved.")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise RouterError("Invalid dashboard session URL.") from error
    local_http = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")
    if (not url.isascii() or any(c.isspace() for c in url) or not parsed.hostname
            or (parsed.scheme != "https" and not local_http) or parsed.username is not None
            or parsed.password is not None or parsed.path not in ("", "/") or parsed.query
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", parsed.fragment)
            or (port is not None and not 1 <= port <= 65535)):
        raise RouterError("Use the complete HTTPS session URL printed by ghr ui, including its fragment. HTTP is allowed only for loopback dashboards.")
    return {"name": name, "url": f"{parsed.scheme}://{parsed.netloc.lower()}", "session": parsed.fragment}


def update_registry(name, machine=None):
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_registry()
        data["machines"] = [m for m in data["machines"] if m["name"] != name]
        if machine:
            data["machines"].append(machine)
        data["machines"].sort(key=lambda m: m["name"])
        fd, temporary = tempfile.mkstemp(prefix=".machines-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _fields(data, keys):
    return {key: data[key] for key in keys if key in data}


def sanitize(path, data):
    endpoint = urlsplit(path).path
    if endpoint == "/api/state":
        return {"accounts": [_fields(a, ("host", "account", "state", "active", "source")) for a in data["accounts"]],
                "mappings": [_fields(m, ("path", "host", "account", "repo")) for m in data["mappings"]],
                **_fields(data, ("home", "config", "version")),
                "machine": _fields(data.get("machine", {}), ("hostname",))}
    if endpoint == "/api/scan":
        rows = []
        for repo in data["repositories"]:
            rows.append({**_fields(repo, ("path", "primary", "account")),
                         "remotes": [_fields(r, ("host", "repo", "protocol", "name", "direction", "account")) for r in repo["remotes"]]})
        return {"repositories": rows}
    return _fields(data, ("path", "host", "account", "repo", "removed"))


def request_machine(machine, path, method="GET", body=None):
    endpoint = urlsplit(path).path
    allowed = (method == "GET" and endpoint in ("/api/state", "/api/scan")) or (method in ("PUT", "DELETE") and endpoint == "/api/mappings")
    if not allowed or not path.startswith("/api/"):
        raise RouterError("Only dashboard state, repository scans, and route management can be forwarded.")
    headers = {"Authorization": "Bearer " + machine["session"], "Content-Type": "application/json"}
    payload = None if body is None else json.dumps(_fields(body, ("path", "host", "account", "repo"))).encode()
    request = Request(machine["url"] + path, data=payload, headers=headers, method=method)
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise RouterError("The machine returned too much metadata.")
        return sanitize(path, json.loads(raw))
    except HTTPError as error:
        if error.code in (401, 403):
            raise RouterError(f"Machine {machine['name']} rejected the session. Pair it again with its current dashboard URL.") from error
        if error.code == 400:
            try:
                message = json.loads(error.read(8192)).get("error", "Invalid remote route.")
            except ValueError:
                message = "Invalid remote route."
            raise RouterError(f"{machine['name']}: {message}") from error
        raise RouterError(f"Machine {machine['name']} returned HTTP {error.code}.") from error
    except (URLError, OSError, ValueError, KeyError, TypeError) as error:
        raise RouterError(f"Cannot reach machine {machine['name']} or read its dashboard metadata.") from error


def remote_request(name, path, method="GET", body=None):
    return request_machine(find_machine(name), path, method, body)


def add_machine(name, url):
    machine = parse_session_url(name, url)
    request_machine(machine, "/api/state")
    update_registry(name, machine)
    return public_machine(machine)


def remove_machine(name):
    update_registry(name)
