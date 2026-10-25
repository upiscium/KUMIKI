"""#291 -> #292 read-only Task/Worktree/OpenCode identity handoff.

Uses the actual existing #191 metadata substrate with a disposable *local*
Git remote. No real repository or human authorizations are modified.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from tests import test_metadata_ref as fixtures
import task_record
from tools.kagari_vnext_task_binding import ControlError, main, parser, resolve
from tests.test_kagari_vnext_opencode_control import Server, Handler, SESSION, PERMISSION


class KAGARITaskBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MetadataRefTest("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.product
        self.base = fixtures.git("rev-parse", "HEAD", cwd=self.project).decode().strip()
        self.branch_ref = "refs/heads/issue-292"
        namespace = self.project / ".worktrees"
        namespace.mkdir()
        self.worktree = namespace / "292"
        fixtures.git("worktree", "add", "-b", "issue-292", str(self.worktree), cwd=self.project)
        self.publication = task_record.TaskRecords(
            self.fixture.store, authorize=lambda _req: True).create(
                task="292", branch_ref=self.branch_ref, base_revision=self.base,
                title="Build Issue-scoped Task CLI", body="Verify live session Ask routing.")
        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.directory = str(self.worktree)
        self.server.session_directory = str(self.worktree)
        self.server.session_parent_id = None
        self.server.session_status = "idle"
        self.server.permissions = []
        self.server.questions = []
        self.server.replies = []
        import threading
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.options = [
            "--project-root", str(self.project), "--worktree", str(self.worktree),
            "--repository", "acme/widgets", "--issue", "292",
            "--base-revision", self.base, "--branch-ref", self.branch_ref,
            "--server-url", f"http://127.0.0.1:{self.server.server_port}",
            "--session-id", SESSION]

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def run_cli(self, *overrides):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(list(overrides or self.options))
        return rc, json.loads(buf.getvalue())

    def create_context(self):
        """KAGARI Main would supply location hints, not specification grants."""
        path = self.fixture.temp_root / "task-location.json"
        payload = {
            "schema_version": 1, "project_root": str(self.project),
            "worktree": str(self.worktree), "repository": "acme/widgets",
            "issue": 292, "base_revision": self.base,
            "branch_ref": self.branch_ref,
            "server_url": f"http://127.0.0.1:{self.server.server_port}",
            "session_id": SESSION,
        }
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return path, payload

    def test_short_context_command_uses_canonical_task_record(self):
        path, _ = self.create_context()
        code, result = self.run_cli("--context", str(path), "asks")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["task"]["contract_sha256"], self.publication.contract_id)
        self.assertEqual(result["pending"], [])
        code, status = self.run_cli("--context", str(path), "--pretty", "status")
        self.assertEqual(code, 0, status)
        self.assertEqual(status["task"]["issue"], 292)

    def test_context_must_not_inject_authority(self):
        path, values = self.create_context()
        values["contract_sha256"] = "f" * 64
        path.write_text(json.dumps(values) + "\n")
        code, result = self.run_cli("--context", str(path), "asks")
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "LOCATOR_INVALID")

    def test_context_cannot_override_location_fields(self):
        path, _ = self.create_context()
        code, result = self.run_cli(
            "--context", str(path), "--issue", "292", "asks")
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "LOCATOR_CONFLICT")

    def test_context_invalid_shape_fails_closed(self):
        path, values = self.create_context()
        values.pop("branch_ref")
        path.write_text(json.dumps(values) + "\n")
        code, result = self.run_cli("--context", str(path), "asks")
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "LOCATOR_INVALID")

    def test_root_session_required(self):
        self.server.session_parent_id = "ses_foreign_parent_9876"
        code, result = self.run_cli(*self.options, "asks")
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "SESSION_MISMATCH")

    def test_actual_task_record_matches_open_code_and_registration(self):
        code, result = self.run_cli()
        self.assertEqual(code, 0, result)
        record = result["binding"]
        self.assertEqual(record["repository"], "acme/widgets")
        self.assertEqual(record["issue"], 292)
        self.assertEqual(record["worktree"], str(self.worktree))
        self.assertEqual(record["contract_sha256"], self.publication.contract_id)
        self.assertEqual(record["contract_revision"],
                         "contract:" + self.publication.contract_id[:16])
        self.assertEqual(record["session_id"], SESSION)

    def test_live_session_directory_mismatch_rejected(self):
        self.server.session_directory = str(self.project)
        rc, result = self.run_cli()
        self.assertEqual(rc, 2)
        self.assertEqual(result["error_code"], "SESSION_MISMATCH")

    def test_wrong_repository_fails_before_binding(self):
        options = list(self.options)
        options[options.index("--repository") + 1] = "foreign/repository"
        code, response = self.run_cli(*options)
        self.assertEqual(code, 2)
        self.assertEqual(response["error_code"], "TASK_RECORD_INVALID")

    def test_wrong_branch_fails(self):
        options = list(self.options)
        options[options.index("--branch-ref") + 1] = "refs/heads/main"
        code, response = self.run_cli(*options)
        self.assertEqual(code, 2)
        self.assertEqual(response["error_code"], "WORKTREE_MISMATCH")

    def test_moved_to_other_worktree_fails(self):
        options = list(self.options)
        options[options.index("--worktree") + 1] = str(self.project)
        rc, result = self.run_cli(*options)
        self.assertEqual(rc, 2)
        self.assertEqual(result["error_code"], "WORKTREE_MISMATCH")

    def test_changed_original_base_fails(self):
        options = list(self.options)
        options[options.index("--base-revision") + 1] = "1" * 40
        code, response = self.run_cli(*options)
        self.assertEqual(code, 2)
        self.assertEqual(response["error_code"], "TASK_RECORD_INVALID")

    def test_explicit_cancelled_task_refused(self):
        task_records = task_record.TaskRecords(
            self.fixture.store, authorize=lambda _request: True)
        task_records.set_disposition(
            task="292", branch_ref=self.branch_ref, base_revision=self.base,
            expected_record_id=self.publication.record_id,
            disposition={"kind": "cancelled"})
        code, result = self.run_cli()
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "TASK_RETIRED")

    def test_missing_task_record_fails(self):
        options = list(self.options)
        options[options.index("--issue") + 1] = "999"
        code, response = self.run_cli(*options)
        self.assertEqual(code, 2)
        self.assertEqual(response["error_code"], "TASK_RECORD_ABSENT")

    def test_live_ask_reply_requires_current_specification(self):
        self.server.permissions = [{
            "id": PERMISSION, "sessionID": SESSION,
            "permission": "bash", "patterns": ["pwd"], "metadata": {"command": "pwd"},
            "always": ["pwd *"]}]
        _, listing = self.run_cli(*self.options, "asks")
        self.assertEqual(len(listing["pending"]), 1)
        self.assertEqual(listing["pending"][0]["task"]["contract_sha256"],
                         self.publication.contract_id)
        _, missing_revision = self.run_cli(
            *self.options, "--kind", "permission",
            "--request-id", PERMISSION, "--decision", "once", "ask-reply")
        self.assertEqual(missing_revision["error_code"], "REVISION_REQUIRED")
        self.assertEqual(len(self.server.permissions), 1)
        code, answer = self.run_cli(
            *self.options, "--expected-contract-id", self.publication.contract_id,
            "--kind", "permission", "--request-id", PERMISSION,
            "--decision", "once", "ask-reply")
        self.assertEqual(code, 0, answer)
        self.assertTrue(answer["applied"])
        self.assertEqual(len(self.server.permissions), 0)

    def test_reauthorized_specification_rejects_stale_answer(self):
        self.server.permissions = [{
            "id": PERMISSION, "sessionID": SESSION,
            "permission": "bash", "patterns": ["pwd"], "metadata": {"command": "pwd"},
            "always": ["pwd *"]}]
        task_records = task_record.TaskRecords(
            self.fixture.store, authorize=lambda _request: True)
        task_records.reauthorize(
            task="292", branch_ref=self.branch_ref, base_revision=self.base,
            expected_record_id=self.publication.record_id, title="Changed requirements",
            body="Human has changed the approved specification.")
        code, stale = self.run_cli(
            *self.options, "--expected-contract-id", self.publication.contract_id,
            "--kind", "permission", "--request-id", PERMISSION,
            "--decision", "once", "ask-reply")
        self.assertEqual(code, 2)
        self.assertEqual(stale["error_code"], "STALE_TASK_CONTRACT")
        self.assertEqual(len(self.server.permissions), 1)
        self.assertEqual(self.server.replies, [])


if __name__ == "__main__":
    unittest.main()
