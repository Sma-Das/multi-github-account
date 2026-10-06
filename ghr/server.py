"""Loopback-only dashboard. The browser never receives GitHub credentials."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import CookieError, SimpleCookie
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import time
from urllib.parse import parse_qs, urlsplit
import webbrowser

from .core import (RouterError, accounts, add_mapping, config_path, read_config,
                   remove_mapping, scan, validate_identity)
from . import __version__
from .machines import add_machine, list_machines, remote_request, remove_machine

BROWSER_SESSION_SECONDS = 30 * 24 * 60 * 60


def trusted_origin(public_url):
    """Accept one explicit HTTPS origin, never forwarded headers or wildcards."""
    if public_url is None:
        return None
    try:
        parsed = urlsplit(public_url)
        port = parsed.port
    except ValueError as error:
        raise RouterError("Invalid public dashboard URL.") from error
    if (not public_url.isascii() or any(char.isspace() for char in public_url)
            or parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)):
        raise RouterError("Use an HTTPS origin without a path, credentials, query, or fragment.")
    validate_identity(parsed.hostname, "valid")
    host = parsed.hostname.lower()
    if port is not None and port != 443:
        host += f":{port}"
    return f"https://{host}"


def session_secret(persistent=False):
    if not persistent:
        return secrets.token_urlsafe(32)
    path = config_path().parent / "ui-session"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            value = path.read_text().strip()
            if len(value) != 43 or not all(c.isalnum() or c in "_-" for c in value) or not value.isascii():
                raise RouterError("Invalid persistent dashboard session. Repair or remove the ui-session file.")
            os.chmod(path, 0o600)
            return value
        value = secrets.token_urlsafe(32)
        with os.fdopen(fd, "w") as stream:
            stream.write(value + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return value


def make_server(port=8765, public_url=None, persistent=False):
    public_origin = trusted_origin(public_url)
    session = session_secret(persistent)
    assets = Path(__file__).parent / "web"

    def cookie_name(origin):
        # Cookies have no port boundary. Scope loopback dashboards by their origin.
        return "ghr_browser_" + hashlib.sha256(origin.encode()).hexdigest()[:12]

    def cookie_signature(origin, expires):
        return hmac.new(session.encode(), f"browser-session\n{origin}\n{expires}".encode(), hashlib.sha256).hexdigest()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log the dashboard's session URL or request contents.

        def respond(self, status, body, content_type="application/json"):
            if content_type == "application/json":
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if getattr(self, "remember_browser", False):
                expires = str(int(time.time()) + BROWSER_SESSION_SECONDS)
                origin = self.browser_origin
                cookie = SimpleCookie()
                name = cookie_name(origin)
                cookie[name] = expires + "." + cookie_signature(origin, expires)
                cookie[name]["path"] = "/"
                cookie[name]["max-age"] = BROWSER_SESSION_SECONDS
                cookie[name]["httponly"] = True
                cookie[name]["samesite"] = "Strict"
                if origin.startswith("https://"):
                    cookie[name]["secure"] = True
                self.send_header("Set-Cookie", cookie[name].OutputString())
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            host = f"127.0.0.1:{self.server.server_port}"
            allowed_hosts = {host}
            allowed_origins = {f"http://{host}"}
            if public_origin:
                allowed_hosts.add(urlsplit(public_origin).netloc)
                allowed_origins.add(public_origin)
            if self.headers.get("Host", "").lower() not in allowed_hosts:
                self.respond(403, {"error": "Invalid dashboard host."})
                return False
            self.browser_origin = public_origin if self.headers.get("Host", "").lower() != host else f"http://{host}"
            origin = self.headers.get("Origin")
            if origin is not None and origin not in allowed_origins:
                self.respond(403, {"error": "Cross-origin requests are not allowed."})
                return False
            supplied = self.headers.get("Authorization", "")
            if secrets.compare_digest(supplied.encode(), ("Bearer " + session).encode()):
                self.authentication = "session"
                return True
            # An explicit invalid bearer must never fall back to ambient cookies.
            if supplied:
                self.respond(401, {"error": "Open the session URL printed by ghr ui."})
                return False
            try:
                cookies = SimpleCookie(self.headers.get("Cookie", ""))
                value = cookies[cookie_name(self.browser_origin)].value
                expires, signature = value.split(".", 1)
                valid = (expires.isascii() and expires.isdigit() and len(expires) <= 12
                         and int(expires) > time.time()
                         and hmac.compare_digest(signature.encode(), cookie_signature(self.browser_origin, expires).encode()))
            except (CookieError, KeyError, ValueError):
                valid = False
            if valid:
                if (self.headers.get("Sec-Fetch-Site") == "cross-site"
                        or (self.command != "GET" and origin != self.browser_origin)):
                    self.respond(403, {"error": "Browser requests must come from the dashboard origin."})
                    return False
                self.authentication = "browser-session"
                self.remember_browser = True
                return True
            self.respond(401, {"error": "Connect this browser once using the session URL printed by ghr ui. It will be remembered for future visits."})
            return False

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path.startswith("/api/"):
                if not self.authorized():
                    return
                try:
                    if url.path == "/api/state":
                        self.respond(200, {"accounts": accounts(), "mappings": read_config()["mappings"],
                                           "config": str(config_path()), "home": str(Path.home()),
                                           "version": __version__, "machine": {"hostname": socket.gethostname()},
                                           "authentication": self.authentication})
                    elif url.path == "/api/machines":
                        self.respond(200, {"machines": list_machines()})
                    elif url.path.startswith("/api/machines/"):
                        parts = url.path.split("/")
                        if len(parts) != 5 or parts[4] not in ("state", "scan"):
                            self.respond(404, {"error": "Unknown machine endpoint."})
                            return
                        target = "/api/" + parts[4] + ("?" + url.query if url.query else "")
                        self.respond(200, remote_request(parts[3], target))
                    elif url.path == "/api/scan":
                        query = parse_qs(url.query)
                        path = query.get("path", [str(Path.home() / "GitHub")])[0]
                        self.respond(200, {"repositories": scan(path)})
                    else:
                        self.respond(404, {"error": "Unknown endpoint."})
                except RouterError as error:
                    self.respond(400, {"error": str(error)})
                except OSError:
                    self.respond(500, {"error": "Could not access local configuration or folders."})
                return
            filenames = {"/": ("index.html", "text/html; charset=utf-8"),
                         "/favicon.svg": ("favicon.svg", "image/svg+xml"),
                         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                         "/style.css": ("style.css", "text/css; charset=utf-8")}
            if url.path not in filenames:
                self.respond(404, {"error": "Not found."})
                return
            name, kind = filenames[url.path]
            self.respond(200, (assets / name).read_bytes(), kind)

        def mutate(self, remove=False):
            if not self.authorized():
                return
            endpoint = urlsplit(self.path).path
            parts = endpoint.split("/")
            forwarded = len(parts) == 5 and parts[:3] == ["", "api", "machines"] and parts[4] == "mappings"
            if endpoint not in ("/api/mappings", "/api/machines") and not forwarded:
                self.respond(404, {"error": "Unknown endpoint."})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8192 or self.headers.get("Content-Type") != "application/json":
                    self.respond(400, {"error": "Expected a small JSON request."})
                    return
                data = json.loads(self.rfile.read(size))
                if endpoint == "/api/machines":
                    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
                        raise ValueError("Missing machine name.")
                    if remove:
                        remove_machine(data["name"])
                        self.respond(200, {"removed": True})
                    else:
                        if not isinstance(data.get("url"), str):
                            raise ValueError("Missing dashboard URL.")
                        self.respond(200, add_machine(data["name"], data["url"]))
                    return
                fields = ("path", "host") if remove else ("path", "host", "account")
                if not isinstance(data, dict) or not all(isinstance(data.get(k), str) and data[k] for k in fields):
                    raise ValueError("Missing mapping fields.")
                if forwarded:
                    self.respond(200, remote_request(parts[3], "/api/mappings", "DELETE" if remove else "PUT", data))
                    return
                if remove:
                    remove_mapping(data["path"], data["host"], data.get("repo"))
                    self.respond(200, {"removed": True})
                else:
                    self.respond(200, add_mapping(data["path"], data["host"], data["account"], data.get("repo")))
            except (ValueError, TypeError):
                self.respond(400, {"error": "Invalid mapping JSON."})
            except RouterError as error:
                self.respond(400, {"error": str(error)})
            except OSError:
                self.respond(500, {"error": "Could not save the local mapping file."})

        def do_PUT(self):
            self.mutate()

        def do_DELETE(self):
            self.mutate(remove=True)

        def do_POST(self):
            if urlsplit(self.path).path != "/api/session":
                self.respond(404, {"error": "Unknown endpoint."})
                return
            if not self.authorized():
                return
            if self.headers.get("Origin") != self.browser_origin:
                self.respond(403, {"error": "Browser pairing must come from the dashboard origin."})
                return
            self.remember_browser = True
            self.respond(200, {"connected": True})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    origin = public_origin or f"http://127.0.0.1:{server.server_port}"
    return server, f"{origin}/#{session}"


def serve(port=8765, open_browser=True, public_url=None, persistent=False):
    server, url = make_server(port, public_url, persistent)
    print(f"Account dashboard: {url}", flush=True)
    print("Dashboard session. Press Ctrl-C to stop.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
