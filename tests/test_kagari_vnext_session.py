"""Issue-root OpenCode Session host integration, with a disposable HTTP stand-in.

Exercises actual child process creation in an independently rooted linked
Worktree, real #191 Task Records, and the merged public #292 Ask/status CLI.
No model/network/production Git repo involved; a separate real OpenCode smoke
is required for upstream compatibility qualification.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from tests import test_kagari_vnext_task_binding as task_fixtures
from tests import test_metadata_ref as git_fixtures
import task_record
from tools import kagari_vnext_session as launcher
from tools.kagari_vnext_task_binding import main as task_cli

# The fake executable runs as a real detached process. Its /session endpoints
# behave like OpenCode 1.18.35 for the covered start/status/Ask observations.
FAKE_OPENCODE = r"""#!/usr/bin/env python3
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
if "--version" in sys.argv:
    print("1.18.35")
    sys.exit(0)
if len(sys.argv) < 2 or sys.argv[1] != "serve" or "--port" not in sys.argv:
    sys.exit(18)
port = int(sys.argv[sys.argv.index("--port") + 1])
root = os.getcwd()
sid = "ses_KAGARI163fakeSession0001"
pid = "per_KAGARI163fakePermission01"
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass
    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/global/health":
            return self.reply(200, {"healthy": True, "version": "1.18.35"})
        if parse_qs(parsed.query).get("directory") != [root]:
            return self.reply(400, {"error": "wrong worktree"})
        if parsed.path == "/session/" + sid:
            return self.reply(200, {"id": sid, "directory": root})
        if parsed.path == "/session/status":
            return self.reply(200, {})
        if parsed.path == "/permission":
            return self.reply(200, [{
                "id": pid, "sessionID": sid, "permission": "bash",
                "patterns": ["pwd"], "always": ["pwd *"],
                "metadata": {"command": "pwd"}}])
        if parsed.path == "/question":
            return self.reply(200, [])
        return self.reply(404, {"error": "not found"})
    def do_POST(self):
        parsed = urlsplit(self.path)
        if parsed.path != "/session":
            return self.reply(404, {"error": "not found"})
        if parse_qs(parsed.query).get("directory") != [root]:
            return self.reply(400, {"error": "wrong worktree"})
        value = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if value.get("permission") != [
            {"permission": "bash", "pattern": "*", "action": "ask"},
            {"permission": "edit", "pattern": "*", "action": "ask"},
        ]:
            return self.reply(400, {"error": "native Ask requirement mismatch"})
        return self.reply(200, {"id": sid, "directory": root})
ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


class OpenCodeSessionLaunchTests(unittest.TestCase):
    def setUp(self):
        # Reuse the actual TaskRecord/linked-Git fixture without inheriting
        # all existing CLI test methods into this class a second time.
        self.inner = task_fixtures.KAGARITaskBindingTests("runTest")
        self.inner.setUp()
        self.addCleanup(self.inner.doCleanups)
        for key in ("fixture", "project", "worktree", "base",
                    "branch_ref", "publication"):
            setattr(self, key, getattr(self.inner, key))
        self.launch_root = self.fixture.temp_root / "kagari-163-session-runtime"
        self.bin = self.fixture.temp_root / "fake-opencode"
        self.bin.write_text(FAKE_OPENCODE, encoding="utf-8")
        self.bin.chmod(0o700)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.taskargs = [
            "--project-root", str(self.project), "--worktree", str(self.worktree),
            "--repository", "acme/widgets", "--issue", "292",
            "--base-revision", self.base, "--branch-ref", self.branch_ref,
            "--expected-contract-id", self.publication.contract_id,
            "--runtime-dir", str(self.launch_root), "--port", str(self.port),
            "--opencode-bin", str(self.bin),
        ]
        self.addCleanup(self.stop_service)

    def stop_service(self):
        process = self.launch_root / "process.json"
        if process.exists():
            data = json.loads(process.read_text())
            pid = data.get("pid")
            if type(pid) is int:
                # Own test child only. Process is reaped before temp fixture
                # directories are deleted; other host services stay untouched.
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                owned = launcher._DETACHED_CHILDREN.pop(pid, None)
                if owned is not None:
                    owned.wait(timeout=5)
                else:
                    try:
                        os.waitpid(pid, 0)
                    except ChildProcessError:
                        pass

    def invoke(self, extra=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            status = launcher.main([*self.taskargs, *(extra or [])])
        return status, json.loads(buf.getvalue())

    def test_create_root_session_reuse_and_observe_same_ask_via_merged_cli(self):
        code, first = self.invoke()
        self.assertEqual(code, 0, first)
        self.assertEqual(first["outcome"], "CREATED")
        self.assertEqual(first["task"]["contract_sha256"],
                         self.publication.contract_id)
        self.assertEqual(first["task"]["worktree"], str(self.worktree))
        self.assertEqual(first["context"], str(self.launch_root / "context.json"))
        self.assertFalse((self.launch_root / "launch.pending.json").exists())
        self.assertTrue((self.launch_root / "process.json").exists())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(task_cli(["--context", first["context"], "status"]), 0)
        self.assertEqual(json.loads(out.getvalue())["session_state"], "idle")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(task_cli(["--context", first["context"], "asks"]), 0)
        pending = json.loads(out.getvalue())["pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["kind"], "permission")
        self.assertEqual(pending[0]["upstream"]["metadata"]["command"], "pwd")
        self.assertEqual(pending[0]["task"]["contract_sha256"],
                         self.publication.contract_id)
        code, second = self.invoke()
        self.assertEqual(code, 0, second)
        self.assertEqual(second["outcome"], "RESUMED")
        self.assertEqual(second["context"], first["context"])
        self.assertEqual(len(list(self.launch_root.glob("context.json"))), 1)

    def test_two_distinct_tasks_get_distinct_sessions_and_servers(self):
        first_code, first = self.invoke()
        self.assertEqual(first_code, 0, first)
        second_record = task_record.TaskRecords(
            self.fixture.store, authorize=lambda _req: True).create(
                task="293", branch_ref="refs/heads/issue-293",
                base_revision=self.base, title="Second approved Task",
                body="Different Issue Worktree, independent server")
        sibling = self.project / ".worktrees" / "293"
        git_fixtures.git("worktree", "add", "-b", "issue-293",
                         str(sibling), self.base, cwd=self.project)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            second_port = sock.getsockname()[1]
        second_root = self.fixture.temp_root / "separate-kagari-293"
        original_args = list(self.taskargs)
        for flag, value in (
            ("--issue", "293"), ("--worktree", str(sibling)),
            ("--branch-ref", "refs/heads/issue-293"),
            ("--expected-contract-id", second_record.contract_id),
            ("--port", str(second_port)), ("--runtime-dir", str(second_root)),
        ):
            original_args[original_args.index(flag) + 1] = value
        try:
            code, second = self.invoke_from_args(original_args)
            self.assertEqual(code, 0, second)
            self.assertEqual(second["outcome"], "CREATED")
            self.assertNotEqual(first["context"], second["context"])
            self.assertNotEqual(first["task"]["worktree"], second["task"]["worktree"])
            self.assertNotEqual(first["server_url"], second["server_url"])
        finally:
            proc = second_root / "process.json"
            if proc.exists():
                pid = json.loads(proc.read_text())["pid"]
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                owned = launcher._DETACHED_CHILDREN.pop(pid, None)
                if owned is not None:
                    owned.wait(timeout=5)

    def invoke_from_args(self, args):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = launcher.main(args)
        return code, json.loads(buffer.getvalue())

    def test_stale_contract_refuses_without_creating_runtime(self):
        self.taskargs[self.taskargs.index("--expected-contract-id") + 1] = "f" * 64
        code, result = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "STALE_TASK_CONTRACT")
        self.assertFalse(self.launch_root.exists())

    def test_wrong_worktree_does_not_start_server(self):
        self.taskargs[self.taskargs.index("--worktree") + 1] = str(self.project)
        code, result = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "WORKTREE_MISMATCH")
        self.assertFalse(self.launch_root.exists())

    def test_unregistered_issue_is_not_created(self):
        self.taskargs[self.taskargs.index("--issue") + 1] = "999"
        code, result = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "TASK_RECORD_ABSENT")
        self.assertFalse(self.launch_root.exists())

    def test_missing_session_with_context_requires_reconcile_not_duplicate(self):
        _, created = self.invoke()
        pid = json.loads((self.launch_root / "process.json").read_text())["pid"]
        os.kill(pid, signal.SIGTERM)
        os.waitpid(pid, 0)
        # Exit has been reaped; subsequent invoke must neither create a new
        # session nor silently accept stale runtime location as authority.
        code, result = self.invoke()
        self.assertEqual(code, 2)
        self.assertIn(result["error_code"],
                      {"OPENCODE_UNAVAILABLE", "UPSTREAM_UNAVAILABLE"})
        self.assertEqual(created["context"], str(self.launch_root / "context.json"))

    def test_uncertain_start_marker_blocks_duplicate(self):
        self.launch_root.mkdir()
        (self.launch_root / "launch.pending.json").write_text('{"stage":"uncertain"}')
        code, result = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "SESSION_RECONCILIATION_REQUIRED")
        self.assertFalse((self.launch_root / "context.json").exists())

    def test_other_issue_cannot_adopt_current_root_session(self):
        self.assertEqual(self.invoke()[0], 0)
        self.taskargs[self.taskargs.index("--port") + 1] = str(self.port + 1)
        code, result = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "SESSION_RECONCILIATION_REQUIRED")

    def test_unsupported_opencode_version_leaves_marker_without_live_effect(self):
        unsupported = self.fixture.temp_root / "opencode-unsupported"
        unsupported.write_text("#!/bin/sh\necho 1.18.30\n", encoding="utf-8")
        unsupported.chmod(0o700)
        self.taskargs[self.taskargs.index("--opencode-bin") + 1] = str(unsupported)
        code, result = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "OPENCODE_UNSUPPORTED")
        self.assertFalse((self.launch_root / "launch.pending.json").exists())
        self.assertFalse((self.launch_root / "process.json").exists())


if __name__ == "__main__":
    unittest.main()
