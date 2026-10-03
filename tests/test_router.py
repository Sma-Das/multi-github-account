"""Exercise actual Git helpers and subprocess isolation without network credentials."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ghr import core
from ghr.server import make_server


ROOT = Path(__file__).resolve().parents[1]


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ghr-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "gh"
        fake.write_bytes((ROOT / "tests" / "fixtures" / "gh").read_bytes())
        fake.chmod(0o755)
        env = {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
               "GHR_CONFIG": str(self.root / "config.json"),
               "GIT_CONFIG_GLOBAL": str(self.root / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
               "PYTHONPATH": str(ROOT)}
        self.environ = patch.dict(os.environ, env)
        self.environ.start()
        self.addCleanup(self.environ.stop)
        for key in (*core.TOKEN_VARS, "GHR_PINNED_PATH", "GHR_PINNED_HOST", "GHR_PINNED_ACCOUNT", "GIT_CONFIG_COUNT"):
            os.environ.pop(key, None)

    def run_cmd(self, *args, cwd=None, env=None, input=None, check=True):
        return subprocess.run(args, cwd=cwd or self.root, env=env, input=input,
                              capture_output=True, text=True, check=check)

    def repo(self, name, owner="owner", host="github.com"):
        path = self.root / name
        path.mkdir()
        self.run_cmd("git", "init", "-q", str(path))
        self.run_cmd("git", "-C", str(path), "remote", "add", "origin", f"https://{host}/{owner}/{name}.git")
        return path

    def cli(self, *args, **kwargs):
        return self.run_cmd(sys.executable, "-m", "ghr", *args, **kwargs)

    def fill(self, path, env=None):
        return self.run_cmd("git", "credential", "fill", cwd=path, env=env,
                            input="protocol=https\nhost=github.com\npath=org/repo.git\n\n")

    def test_parallel_git_credentials_and_api_calls_are_isolated(self):
        first, second = self.repo("repo-1"), self.repo("repo-2")
        core.add_mapping(first, "github.com", "user-1")
        core.add_mapping(second, "github.com", "user-2")
        # Simulate an existing wrong helper; setup must reset it.
        self.run_cmd("git", "config", "--global", "credential.helper", "!printf 'username=wrong\\npassword=wrong\\n'")
        core.setup_helper("github.com", global_scope=True)
        with patch.dict(os.environ, {"GH_TOKEN": "wrong-ambient", "GH_DEBUG": "api"}):
            with ThreadPoolExecutor(max_workers=8) as pool:
                tasks = [(first, "user-1"), (second, "user-2")] * 8
                responses = list(pool.map(lambda item: (self.fill(item[0]), item[1]), tasks))
                api = list(pool.map(lambda item: (self.cli("gh", "--path", str(item[0]), "--", "api", "/user"), item[1]), tasks))
        for response, account in responses:
            self.assertIn("username=" + account, response.stdout)
            self.assertIn("password=fixture-token-" + account, response.stdout)
            self.assertNotIn("fixture-token", response.stderr)
        for response, account in api:
            self.assertEqual(response.stdout.strip(), "fixture-token-" + account)
        self.assertEqual(core.accounts()[0]["active"], True)

    def test_unmapped_repo_stops_before_wrong_fallback(self):
        path = self.repo("unmapped")
        core.setup_helper("github.com", global_scope=True)
        result = self.run_cmd("git", "credential", "fill", cwd=path, check=False,
                              input="protocol=https\nhost=github.com\npath=org/repo.git\n\n",
                              env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No account mapping", result.stderr)
        self.assertNotIn("password=", result.stdout)

    def test_longest_folder_and_symlinks(self):
        parent = self.root / "work"
        parent.mkdir()
        path = self.repo("repo-specific")
        path.rename(parent / path.name)
        path = parent / path.name
        core.add_mapping(parent, "github.com", "user-1")
        core.add_mapping(path, "github.com", "user-2")
        link = self.root / "alias"
        link.symlink_to(path, target_is_directory=True)
        self.assertEqual(core.resolve(link)["account"], "user-2")
        self.assertEqual(core.resolve(parent)["account"], "user-1")

    def test_linked_worktree_inherits_and_can_override(self):
        primary = self.repo("primary")
        self.run_cmd("git", "-C", str(primary), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "fixture")
        worktree = self.root / "agent-worktree"
        self.run_cmd("git", "-C", str(primary), "worktree", "add", "-b", "agent", str(worktree))
        core.add_mapping(primary, "github.com", "user-1")
        self.assertEqual(core.resolve(worktree)["account"], "user-1")
        core.setup_helper("github.com", global_scope=True)
        self.assertIn("username=user-1", self.fill(worktree).stdout)
        core.add_mapping(worktree, "github.com", "user-2")
        self.assertEqual(core.resolve(worktree)["account"], "user-2")

    def test_process_credentials_stay_pinned_after_mapping_edit(self):
        path = self.repo("pinned")
        core.add_mapping(path, "github.com", "user-1")
        env = core.process_env(core.resolve(path))
        core.add_mapping(path, "github.com", "user-2")
        self.assertIn("username=user-1", self.fill(path, env).stdout)
        self.assertEqual(self.cli("gh", "--path", str(path), "--", "api", "/user", env=env).stdout.strip(), "fixture-token-user-2")

    def test_concurrent_mapping_writes_do_not_lose_updates(self):
        paths = [self.repo(f"repo-{i}") for i in range(12)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda p: self.cli("map", str(p), "--account", "user-1"), paths))
        self.assertEqual(len(core.read_config()["mappings"]), len(paths))
        self.assertEqual(core.config_path().stat().st_mode & 0o777, 0o600)

    def test_process_cannot_silently_use_another_repositories_account(self):
        first, second = self.repo("scope-first"), self.repo("scope-second")
        core.add_mapping(first, "github.com", "user-1")
        core.add_mapping(second, "github.com", "user-2")
        env = core.process_env(core.resolve(first))
        denied = self.run_cmd("git", "credential", "fill", cwd=second, env=env, check=False,
                              input="protocol=https\nhost=github.com\npath=org/repo.git\n\n")
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("scoped to another checkout", denied.stderr)
        self.assertNotIn("password=", denied.stdout)
        selected = self.cli("gh", "--path", str(second), "--", "api", "/user", env=env)
        self.assertEqual(selected.stdout.strip(), "fixture-token-user-2")

    def test_enterprise_and_existing_git_config_environment(self):
        path = self.repo("enterprise", host="git.example.com")
        core.add_mapping(path, "git.example.com", "user-2")
        with patch.dict(os.environ, {"GH_TOKEN": "wrong", "GITHUB_ENTERPRISE_TOKEN": "wrong",
                                     "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "color.ui", "GIT_CONFIG_VALUE_0": "false"}):
            env = core.process_env(core.resolve(path))
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("GITHUB_ENTERPRISE_TOKEN", env)
        self.assertEqual(env["GH_ENTERPRISE_TOKEN"], "fixture-token-user-2")
        self.assertEqual(env["GIT_CONFIG_COUNT"], "4")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "color.ui")
        result = self.run_cmd("git", "credential", "fill", cwd=path, env=env,
                              input="protocol=https\nhost=git.example.com\npath=org/repo.git\n\n")
        self.assertIn("username=user-2", result.stdout)

    def test_relative_config_is_made_absolute_for_child(self):
        path = self.repo("relative-config")
        with patch.dict(os.environ, {"GHR_CONFIG": "relative.json"}):
            env = core.process_env({"path": str(path), "host": "github.com", "account": "user-1"})
        self.assertEqual(env["GHR_CONFIG"], str(Path.cwd() / "relative.json"))

    def test_invalid_account_or_remote_host_cannot_be_saved(self):
        path = self.repo("validation")
        with self.assertRaises(core.RouterError):
            core.add_mapping(path, "github.com", "unknown")
        with self.assertRaises(core.RouterError):
            core.add_mapping(path, "git.example.com", "user-2")
        self.assertEqual(core.read_config()["mappings"], [])

    def test_store_and_erase_never_persist_secrets(self):
        for action in ("store", "erase"):
            output = io.StringIO()
            self.assertEqual(core.credential(action, io.StringIO("password=secret\n\n"), output), 0)
            self.assertEqual(output.getvalue(), "")
            self.assertFalse(core.config_path().exists())

    def test_dashboard_auth_origin_validation_and_mutations(self):
        path = self.repo("ui-repo")
        self.run_cmd("git", "-C", str(path), "remote", "set-url", "origin",
                     "https://fixture-embedded-secret@github.com/owner/ui-repo.git")
        server, url = make_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base, session = url.split("#")
        headers = {"Authorization": "Bearer " + session, "Content-Type": "application/json"}
        with self.assertRaises(HTTPError) as error:
            urlopen(base + "api/state")
        self.assertEqual(error.exception.code, 401)
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(base + "api/state", headers=dict(headers, Origin="https://evil.example")))
        self.assertEqual(error.exception.code, 403)
        state = json.load(urlopen(Request(base + "api/state", headers=headers)))
        self.assertNotIn("fixture-token", json.dumps(state))
        discovered = json.load(urlopen(Request(base + "api/scan?path=" + quote(str(path)), headers=headers)))
        self.assertNotIn("fixture-embedded-secret", json.dumps(discovered))
        self.assertEqual(discovered["repositories"][0]["remotes"][0]["repo"], "owner/ui-repo")
        route = {"path": str(path), "host": "github.com", "account": "user-2"}
        saved = json.load(urlopen(Request(base + "api/mappings", data=json.dumps(route).encode(), headers=headers, method="PUT")))
        self.assertEqual(saved["account"], "user-2")
        self.assertEqual(core.resolve(path)["account"], "user-2")
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(base + "api/mappings", data=b'[]', headers=headers, method="PUT"))
        self.assertEqual(error.exception.code, 400)
        json.load(urlopen(Request(base + "api/mappings", data=json.dumps(route).encode(), headers=headers, method="DELETE")))
        self.assertEqual(core.read_config()["mappings"], [])


if __name__ == "__main__":
    unittest.main()
