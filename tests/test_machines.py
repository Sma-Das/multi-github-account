from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ghr.core import RouterError, read_config, helper_command
from ghr.machines import (add_machine, list_machines, parse_session_url, registry_path,
                          remote_request, remove_machine, request_machine)
from ghr.server import make_server, session_secret


class MachineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ghr-machines-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, {"GHR_CONFIG": str(self.root / "config.json")})
        env.start()
        self.addCleanup(env.stop)
        self.routes = []
        self.calls = []
        self.token = "T" * 43
        routes, calls, token = self.routes, self.calls, self.token

        class Remote(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, data):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                calls.append(self.headers.get("Authorization"))
                if self.headers.get("Authorization") != "Bearer " + token:
                    self.reply(401, {"error": "Invalid session"})
                elif self.path == "/api/state":
                    self.reply(200, {"accounts": [{"host": "github.com", "account": "remote-user", "state": "success", "token": "must-not-relay"}],
                                     "mappings": routes, "home": "/remote/home", "config": "/remote/config.json",
                                     "machine": {"hostname": "remote-host", "secret": "must-not-relay"}, "password": "must-not-relay"})
                else:
                    self.reply(200, {"repositories": []})

            def do_PUT(self):
                if self.headers.get("Authorization") != "Bearer " + token:
                    self.reply(401, {"error": "Invalid session"})
                    return
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                routes.append(data)
                self.reply(200, data)

            def do_DELETE(self):
                routes.clear()
                self.reply(200, {"removed": True})

        self.remote = ThreadingHTTPServer(("127.0.0.1", 0), Remote)
        threading.Thread(target=self.remote.serve_forever, daemon=True).start()
        self.addCleanup(self.remote.server_close)
        self.addCleanup(self.remote.shutdown)
        self.url = f"http://127.0.0.1:{self.remote.server_port}/#{self.token}"

    def test_pairing_lists_only_public_metadata_and_keeps_registry_private(self):
        result = add_machine("office", self.url)
        self.assertEqual(result["name"], "office")
        self.assertNotIn(self.token, json.dumps(result))
        self.assertNotIn(self.token, json.dumps(list_machines()))
        self.assertEqual(registry_path().stat().st_mode & 0o777, 0o600)
        self.assertTrue(all(call == "Bearer " + self.token for call in self.calls))
        remove_machine("office")
        self.assertEqual(list_machines(), [])

    def test_remote_routes_change_only_the_selected_machine_and_do_not_relay_credentials(self):
        add_machine("office", self.url)
        state = remote_request("office", "/api/state")
        self.assertEqual(state["accounts"][0]["account"], "remote-user")
        self.assertNotIn("must-not-relay", json.dumps(state))
        route = {"path": "/remote/home/project", "host": "github.com", "account": "remote-user", "repo": "source/project"}
        saved = remote_request("office", "/api/mappings", "PUT", route)
        self.assertEqual(saved, route)
        self.assertEqual(self.routes, [route])
        self.assertEqual(read_config()["mappings"], [])
        remote_request("office", "/api/mappings", "DELETE", route)
        self.assertEqual(self.routes, [])

    def test_parallel_pairing_preserves_each_machine(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda n: add_machine(f"machine-{n}", self.url), range(8)))
        self.assertEqual(len(list_machines()), 8)

    def test_pairing_rejects_invalid_urls_and_wrong_sessions_without_saving(self):
        for url in ("http://public.example/#" + self.token, "https://user:pass@example.com/#" + self.token,
                    "https://example.com/path#" + self.token, "https://example.com/#wrong"):
            with self.subTest(url=url), self.assertRaises(RouterError):
                parse_session_url("office", url)
        with self.assertRaises(RouterError):
            add_machine("office", self.url.replace(self.token, "W" * 43))
        self.assertEqual(list_machines(), [])
        machine = parse_session_url("office", self.url)
        with self.assertRaises(RouterError):
            request_machine(machine, "/api/credentials")
        with self.assertRaises(RouterError):
            request_machine(machine, "https://evil.example/api/state")

    def test_persistent_dashboard_session_survives_restart(self):
        first = session_secret(True)
        self.assertEqual(session_secret(True), first)
        self.assertNotEqual(session_secret(False), first)
        self.assertEqual((self.root / "ui-session").stat().st_mode & 0o777, 0o600)

    def test_homebrew_git_helper_uses_stable_opt_path(self):
        with patch("ghr.core.__file__", "/opt/homebrew/Cellar/ghr/HEAD-example/libexec/lib/python3.13/site-packages/ghr/core.py"), patch("pathlib.Path.is_file", return_value=True):
            self.assertEqual(helper_command(), "!/opt/homebrew/opt/ghr/libexec/bin/python -m ghr credential")

    def test_pairing_does_not_forward_session_keys_across_redirects(self):
        destination_calls = []
        class Destination(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                destination_calls.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
        destination = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
        threading.Thread(target=destination.serve_forever, daemon=True).start()
        self.addCleanup(destination.server_close)
        self.addCleanup(destination.shutdown)
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/api/state")
                self.end_headers()
        source = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=source.serve_forever, daemon=True).start()
        self.addCleanup(source.server_close)
        self.addCleanup(source.shutdown)
        with self.assertRaises(RouterError):
            add_machine("redirect", f"http://127.0.0.1:{source.server_port}/#{self.token}")
        self.assertEqual(destination_calls, [])

    def test_hub_proxy_requires_hub_authentication_and_isolates_remote_mutations(self):
        add_machine("office", self.url)
        server, url = make_server(0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base, token = url.split("#")
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        with self.assertRaises(HTTPError) as error:
            urlopen(base + "api/machines/office/state")
        self.assertEqual(error.exception.code, 401)
        state = json.load(urlopen(Request(base + "api/machines/office/state", headers=headers)))
        self.assertEqual(state["home"], "/remote/home")
        route = {"path": "/remote/home/project", "host": "github.com", "account": "remote-user"}
        json.load(urlopen(Request(base + "api/machines/office/mappings", headers=headers,
                                 data=json.dumps(route).encode(), method="PUT")))
        self.assertEqual(self.routes, [route])
        self.assertEqual(read_config()["mappings"], [])


if __name__ == "__main__":
    unittest.main()
