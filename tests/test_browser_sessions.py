"""Browser pairing, renewal, restart persistence, and cookie authorization boundaries."""

from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from ghr.server import BROWSER_SESSION_SECONDS, make_server


class BrowserSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ghr-browser-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"GHR_CONFIG": str(self.root / "config.json")})
        environment.start()
        self.addCleanup(environment.stop)
        accounts = patch("ghr.server.accounts", return_value=[])
        accounts.start()
        self.addCleanup(accounts.stop)

    def dashboard(self, origin="https://github.example.com", persistent=True):
        server, url = make_server(0, public_url=origin, persistent=persistent)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        public, key = url.split("#")
        return base, public.rstrip("/"), key

    def request(self, dashboard, path="/api/state", headers=None, method="GET", body=None):
        base, origin, _ = dashboard
        headers = {"Host": urlsplit(origin).netloc, **(headers or {})}
        return urlopen(Request(base + path, headers=headers, method=method, data=body), timeout=5)

    def pair(self, dashboard):
        _, origin, key = dashboard
        response = self.request(dashboard, "/api/session", method="POST",
                                headers={"Authorization": "Bearer " + key, "Origin": origin})
        self.assertEqual(json.load(response), {"connected": True})
        cookie = response.headers["Set-Cookie"]
        self.assertNotIn(key, cookie)
        return cookie

    def cookie_header(self, value):
        return SimpleCookie(value).output(header="").strip().split(";", 1)[0]

    def assert_status(self, status, *args, **kwargs):
        with self.assertRaises(HTTPError) as error:
            self.request(*args, **kwargs)
        self.assertEqual(error.exception.code, status)
        self.assertIsNone(error.exception.headers.get("Set-Cookie"))

    def test_pairing_persists_and_new_visits_use_only_a_cookie(self):
        dashboard = self.dashboard()
        self.assert_status(401, dashboard)
        cookie = self.pair(dashboard)
        parsed = next(iter(SimpleCookie(cookie).values()))
        self.assertTrue(parsed["httponly"])
        self.assertTrue(parsed["secure"])
        self.assertEqual(parsed["samesite"], "Strict")
        self.assertEqual(parsed["path"], "/")
        self.assertEqual(parsed["max-age"], str(BROWSER_SESSION_SECONDS))
        self.assertFalse(parsed["domain"])
        headers = {"Cookie": self.cookie_header(cookie)}
        self.assertEqual(json.load(self.request(dashboard, headers=headers))["authentication"], "browser-session")
        self.assertEqual(json.load(self.request(dashboard, "/api/machines", headers=headers)), {"machines": []})
        # The origin and persisted signing key remain stable across process restarts.
        restarted = self.dashboard()
        self.assertEqual(dashboard[2], restarted[2])
        self.assertEqual(json.load(self.request(restarted, headers=headers))["authentication"], "browser-session")

    def test_cookie_renews_with_use_and_expired_or_forged_cookies_fail(self):
        dashboard = self.dashboard()
        with patch("ghr.server.time.time", return_value=1_000_000_000):
            cookie = self.pair(dashboard)
        header = self.cookie_header(cookie)
        with patch("ghr.server.time.time", return_value=1_000_000_100):
            response = self.request(dashboard, headers={"Cookie": header})
            renewed = next(iter(SimpleCookie(response.headers["Set-Cookie"]).values())).value
            self.assertEqual(int(renewed.split(".")[0]), 1_000_000_100 + BROWSER_SESSION_SECONDS)
        with patch("ghr.server.time.time", return_value=1_000_000_000 + BROWSER_SESSION_SECONDS):
            self.assert_status(401, dashboard, headers={"Cookie": header})
        with patch("ghr.server.time.time", return_value=1_000_000_100):
            for invalid in (header[:-1] + ("1" if header[-1] == "0" else "0"),
                            header.split("=", 1)[0] + "=not-a-cookie"):
                self.assert_status(401, dashboard, headers={"Cookie": invalid})

    def test_cookie_cannot_cross_origins_or_survive_key_rotation(self):
        first = self.dashboard()
        headers = {"Cookie": self.cookie_header(self.pair(first))}
        self.assert_status(401, self.dashboard("https://other.example.com"), headers=headers)
        # Even if the cookie is manually copied onto another origin, its signature fails.
        local_origin = f"http://127.0.0.1:{urlsplit(first[0]).port}"
        name = self.cookie_header(self.pair((first[0], local_origin, first[2]))).split("=", 1)[0]
        copied = name + "=" + headers["Cookie"].split("=", 1)[1]
        self.assert_status(401, (first[0], local_origin, first[2]), headers={"Cookie": copied})
        self.assert_status(401, self.dashboard(persistent=False), headers=headers)

    def test_cookie_writes_require_same_origin_and_bearers_remain_usable(self):
        dashboard = self.dashboard()
        headers = {"Cookie": self.cookie_header(self.pair(dashboard)), "Content-Type": "application/json"}
        path = "/api/mappings"
        body = json.dumps({"path": str(self.root), "host": "github.com"}).encode()
        for origin in (None, "https://evil.example.com", f"http://127.0.0.1:{urlsplit(dashboard[0]).port}"):
            self.assert_status(403, dashboard, path, headers={**headers, **({"Origin": origin} if origin else {})},
                               method="DELETE", body=body)
        self.assertFalse((self.root / "config.json").exists())
        self.assert_status(403, dashboard, headers={**headers, "Sec-Fetch-Site": "cross-site"})
        self.assert_status(401, dashboard, headers={**headers, "Authorization": "Bearer wrong"})
        result = self.request(dashboard, path, method="DELETE", body=body,
                              headers={**headers, "Origin": dashboard[1]})
        self.assertTrue(json.load(result)["removed"])
        bearer = self.request(dashboard, headers={"Authorization": "Bearer " + dashboard[2]})
        self.assertEqual(json.load(bearer)["authentication"], "session")
        self.assertIsNone(bearer.headers.get("Set-Cookie"))

    def test_pairing_requires_a_credential_and_exact_origin(self):
        dashboard = self.dashboard()
        self.assert_status(401, dashboard, "/api/session", method="POST", headers={"Origin": dashboard[1]})
        for origin in (None, "https://evil.example.com"):
            self.assert_status(403, dashboard, "/api/session", method="POST",
                               headers={"Authorization": "Bearer " + dashboard[2], **({"Origin": origin} if origin else {})})
        local = self.dashboard(origin=None)
        cookie = next(iter(SimpleCookie(self.pair(local)).values()))
        self.assertTrue(cookie["httponly"])
        self.assertFalse(cookie["secure"])


if __name__ == "__main__":
    unittest.main()
