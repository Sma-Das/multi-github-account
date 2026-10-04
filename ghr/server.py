"""Loopback-only dashboard. The browser never receives GitHub credentials."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlsplit
import webbrowser

from .core import (RouterError, accounts, add_mapping, config_path, read_config,
                   remove_mapping, scan, validate_identity)


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


def make_server(port=8765, public_url=None):
    public_origin = trusted_origin(public_url)
    session = secrets.token_urlsafe(32)
    assets = Path(__file__).parent / "web"

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
            origin = self.headers.get("Origin")
            if origin is not None and origin not in allowed_origins:
                self.respond(403, {"error": "Cross-origin requests are not allowed."})
                return False
            supplied = self.headers.get("Authorization", "")
            if not secrets.compare_digest(supplied.encode(), ("Bearer " + session).encode()):
                self.respond(401, {"error": "Open the session URL printed by ghr ui."})
                return False
            return True

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path.startswith("/api/"):
                if not self.authorized():
                    return
                try:
                    if url.path == "/api/state":
                        self.respond(200, {"accounts": accounts(), "mappings": read_config()["mappings"],
                                           "config": str(config_path()), "home": str(Path.home())})
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
            if urlsplit(self.path).path != "/api/mappings":
                self.respond(404, {"error": "Unknown endpoint."})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8192 or self.headers.get("Content-Type") != "application/json":
                    self.respond(400, {"error": "Expected a small JSON request."})
                    return
                data = json.loads(self.rfile.read(size))
                fields = ("path", "host") if remove else ("path", "host", "account")
                if not isinstance(data, dict) or not all(isinstance(data.get(k), str) and data[k] for k in fields):
                    raise ValueError("Missing mapping fields.")
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

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    origin = public_origin or f"http://127.0.0.1:{server.server_port}"
    return server, f"{origin}/#{session}"


def serve(port=8765, open_browser=True, public_url=None):
    server, url = make_server(port, public_url)
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
