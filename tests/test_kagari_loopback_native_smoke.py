"""#339 R4 opt-in native OpenCode 1.18.35 two-server read-only smoke.

Disposable HOME/XDG and Worktrees; NO model prompt, Task dispatch, native Ask
reply, live repository, credential, privileged Git, or persistent host service.
A successful test proves only native server/session and #292 read APIs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from tools.kagari_vnext_opencode_control import OpenCode, TaskBinding, status
from tools.kagari_vnext_session import SUPPORTED_OPENCODE


@unittest.skipUnless(os.environ.get("KAGARI_339_NATIVE_SMOKE") == "1",
                     "opt-in: isolate native OpenCode servers, no model prompts")
class NativeWorkerSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kagari339-native-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.worker_environments: list[dict[str, str]] = []
        self.binary = shutil.which("opencode")
        if not self.binary:
            self.skipTest("OpenCode unavailable")
        version = subprocess.run([self.binary, "--version"],
                                 capture_output=True, text=True,
                                 stdin=subprocess.DEVNULL, timeout=8, check=False)
        self.assertEqual((version.returncode, version.stdout.strip()),
                         (0, SUPPORTED_OPENCODE))
        # Never pass the operator's API credentials, provider settings,
        # worktree config or ambient OPENCODE_ server password into fixtures.
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TERM": "dumb",
            "NO_COLOR": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
        self.http = build_opener(ProxyHandler({}))

    def _request(self, url: str, *, data=None):
        raw = None if data is None else json.dumps(data).encode("utf-8")
        request = Request(url, data=raw,
                          headers={"Accept": "application/json",
                                   "Content-Type": "application/json"})
        with self.http.open(request, timeout=2) as response:
            content = response.read(65537)
            self.assertLessEqual(len(content), 65536)
            return json.loads(content) if content else None

    def _spawn(self, number: int):
        workspace = self.root / ("project-" + str(number))
        workspace.mkdir(mode=0o700)
        worker_state = self.root / ("worker-" + str(number))
        worker_state.mkdir(mode=0o700)
        env = dict(self.env)
        for key, directory in (
            ("HOME", "home"),
            ("XDG_CONFIG_HOME", "config"),
            ("XDG_DATA_HOME", "data"),
            ("XDG_CACHE_HOME", "cache"),
            ("XDG_STATE_HOME", "state"),
            ("XDG_RUNTIME_DIR", "run"),
        ):
            path = worker_state / directory
            path.mkdir(mode=0o700)
            env[key] = str(path)
        self.worker_environments.append(env)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        logfile = self.root / ("server-" + str(number) + ".log")
        with logfile.open("wb") as out:
            proc = subprocess.Popen(
                [self.binary, "serve", "--pure", "--hostname", "127.0.0.1",
                 "--port", str(port)],
                cwd=workspace, stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT, env=env,
                start_new_session=True)
        self.addCleanup(self._stop, proc)
        base = "http://127.0.0.1:" + str(port)
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline:
            self.assertIsNone(proc.poll(), "native OpenCode server exited")
            try:
                health = self._request(base + "/global/health")
                if (type(health) is dict and health.get("healthy") is True
                        and health.get("version") == SUPPORTED_OPENCODE):
                    return workspace, base, proc
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        self.fail("native OpenCode did not become healthy (disposable fixture)")

    @staticmethod
    def _stop(proc):
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def test_two_native_servers_and_task_bound_read_only_sessions(self):
        workers = [self._spawn(n) for n in (1, 2)]
        for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                    "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
            self.assertEqual(len({env[key] for env in self.worker_environments}), 2)
        sessions = []
        for n, (workspace, base, proc) in enumerate(workers, 1):
            root_session = self._request(
                base + "/session?" + urlencode({"directory": str(workspace)}),
                data={"title": "KAGARI #339 isolated native test " + str(n),
                      "permission": [
                          {"permission": "bash", "pattern": "*", "action": "ask"},
                          {"permission": "edit", "pattern": "*", "action": "ask"},
                      ]})
            self.assertIsInstance(root_session, dict)
            self.assertEqual(root_session.get("directory"), str(workspace))
            self.assertIsNone(root_session.get("parentID"))
            mapping = {
                "schema_version": 1,
                "repository": "acme/fixture",
                "issue": n, "worktree": str(workspace),
                "server_url": base, "session_id": root_session["id"],
                "contract_sha256": str(n) * 64,
                "contract_revision": "contract:" + str(n) * 16,
            }
            binding = TaskBinding.from_mapping(mapping)
            client = OpenCode(binding)
            client.verify_session()
            self.assertEqual(client.pending(), [])
            result = status(binding, client)
            self.assertEqual(result["session_state"], "idle")
            self.assertEqual(result["pending_asks"], 0)
            self.assertNotEqual(result["task_attention"], "COMPLETED")
            sessions.append((base, root_session["id"], workspace, proc))
        self.assertEqual(len({base for base, *_ in sessions}), 2)
        self.assertEqual(len({sid for _, sid, *_ in sessions}), 2)
        self.assertTrue(all(proc.poll() is None for *_, proc in sessions))


if __name__ == "__main__":
    unittest.main()
