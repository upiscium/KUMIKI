"""Real local-Git fixtures for #286 opt-in Issue-lifetime linked Worktrees.

No production repository, GitHub Issue, persistent KAGARI GA state, or
model provider is touched. The #191 canonical metadata ref is a local bare
test remote with a fixture-only CAS callback.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

from tests import test_metadata_ref as fixtures

V4 = Path(__file__).resolve().parents[1] / "components/agent-core-v4"
sys.path.insert(0, str(V4))
import issue_worktree
from issue_worktree import IssueWorktreeAllocator, IssueWorktreeError
from task_record import TaskRecords


class IssueWorktreeTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MetadataRefTest("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.product
        self.meta = self.fixture.store
        # Mandatory supported-host setup: the managed namespace is ignored.
        (self.project / ".gitignore").write_text("/.worktrees/\n")
        fixtures.git("add", ".gitignore", cwd=self.project)
        fixtures.git("commit", "-q", "-m", "ignore managed namespace", cwd=self.project)
        self.base = fixtures.git("rev-parse", "HEAD", cwd=self.project).decode().strip()
        self.records = TaskRecords(self.meta, authorize=lambda _req: True)
        self.pub = self.records.create(
            task="286", branch_ref="refs/heads/issue-286",
            base_revision=self.base, title="Issue-linked Worktree",
            body="One Task per Issue; preserve attached Project")
        self.alloc = IssueWorktreeAllocator(self.meta)
        self.kw = {
            "task": "286",
            "base_revision": self.base,
            "branch_ref": "refs/heads/issue-286",
            "expected_contract_id": self.pub.contract_id,
        }

    def git(self, *args: str, cwd: Path | None = None):
        return fixtures.git(*args, cwd=cwd or self.project).decode().strip()

    def assert_reject(self, code, *, args=None):
        with self.assertRaises(IssueWorktreeError) as error:
            self.alloc.ensure(**(args or self.kw))
        self.assertEqual(error.exception.code, code)

    def _first(self):
        result = self.alloc.ensure(**self.kw)
        self.assertEqual(result.status, "CREATED")
        self.assertEqual(result.repository, "acme/widgets")
        self.assertEqual(result.contract_id, self.pub.contract_id)
        self.assertEqual(result.worktree, str(self.project / ".worktrees" / "286"))
        self.assertEqual(result.head, self.base)
        self.assertEqual(self.git("symbolic-ref", "HEAD", cwd=Path(result.worktree)),
                         "refs/heads/issue-286")
        return result

    def test_first_assignment_and_retry_resume_same_branch(self):
        first = self._first()
        second = self.alloc.ensure(**self.kw)
        self.assertEqual(second.status, "RESUMED")
        self.assertEqual(second.worktree, first.worktree)
        self.assertEqual(second.head, first.head)
        entries = self.git("worktree", "list", "--porcelain")
        self.assertEqual(entries.count("branch refs/heads/issue-286"), 1)

    def test_second_issue_has_distinct_worktree(self):
        first = self._first()
        second_record = self.records.create(
            task="287", branch_ref="refs/heads/issue-287", base_revision=self.base,
            title="Second accepted Issue", body="Distinct Worktree identity")
        second = self.alloc.ensure(
            task="287", branch_ref="refs/heads/issue-287", base_revision=self.base,
            expected_contract_id=second_record.contract_id)
        self.assertEqual(second.status, "CREATED")
        self.assertNotEqual(first.worktree, second.worktree)
        self.assertEqual(self.alloc.ensure(**self.kw).status, "RESUMED")
        self.assertEqual(self.git("symbolic-ref", "HEAD", cwd=Path(first.worktree)),
                         "refs/heads/issue-286")

    def test_existing_sibling_worktree_product_is_preserved(self):
        sibling = self.project / ".worktrees" / "sibling"
        sibling.parent.mkdir(exist_ok=True)
        self.git("worktree", "add", "-b", "issue-999", str(sibling), self.base)
        (sibling / "sibling-unique.txt").write_text("other Issue progress\n")
        sibling_before = self.git("status", "--porcelain=v1",
                                  "--untracked-files=all", cwd=sibling)
        sibling_head = self.git("rev-parse", "HEAD", cwd=sibling)
        self._first()
        self.assertEqual((sibling / "sibling-unique.txt").read_text(),
                         "other Issue progress\n")
        self.assertEqual(self.git("status", "--porcelain=v1",
                                  "--untracked-files=all", cwd=sibling), sibling_before)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=sibling), sibling_head)

    def test_project_dirty_staged_untracked_and_index_remain_intact(self):
        (self.project / "tracked.txt").write_text("dirty user working copy\n")
        (self.project / "extra.txt").write_text("never touch user untracked\n")
        (self.project / ".staged.txt").write_text("stage retained\n")
        self.git("add", ".staged.txt")
        index = self.project / ".git" / "index"
        before_index = index.read_bytes()
        before_head = self.git("rev-parse", "HEAD")
        before_status = self.git("status", "--porcelain=v1", "--untracked-files=all")
        original = (self.project / "tracked.txt").read_bytes()
        self._first()
        self.assertEqual(index.read_bytes(), before_index)
        self.assertEqual(self.git("rev-parse", "HEAD"), before_head)
        self.assertEqual(self.git("status", "--porcelain=v1", "--untracked-files=all"),
                         before_status)
        self.assertEqual((self.project / "tracked.txt").read_bytes(), original)

    def test_resume_after_task_branch_moves(self):
        first = self._first()
        wt = Path(first.worktree)
        (wt / "work.txt").write_text("progress\n")
        self.git("add", "work.txt", cwd=wt)
        self.git("commit", "-q", "-m", "continue work", cwd=wt)
        advanced = self.git("rev-parse", "HEAD", cwd=wt)
        self.assertNotEqual(advanced, self.base)
        resumed = self.alloc.ensure(**self.kw)
        self.assertEqual(resumed.status, "RESUMED")
        self.assertEqual(resumed.head, advanced)

    def test_existing_kagari_cli_can_read_allocated_issue_worktree(self):
        """#286's exact registration is consumable by merged #292 CLI."""
        from tests.test_kagari_vnext_opencode_control import Server, Handler, SESSION
        from tools.kagari_vnext_task_binding import main as task_cli
        allocation = self._first()
        server = Server(("127.0.0.1", 0), Handler)
        server.directory = allocation.worktree
        server.session_directory = allocation.worktree
        server.session_parent_id = None
        server.session_status = "idle"
        server.sparse_status = True
        server.permissions = []
        server.questions = []
        server.replies = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            context = self.fixture.temp_root / "kagari-runtime.json"
            context.write_text(json.dumps({
                "schema_version": 1,
                "project_root": str(self.project),
                "worktree": allocation.worktree,
                "repository": "acme/widgets",
                "issue": 286,
                "base_revision": self.base,
                "branch_ref": "refs/heads/issue-286",
                "server_url": f"http://127.0.0.1:{server.server_port}",
                "session_id": SESSION,
            }))
            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                code = task_cli(["--context", str(context), "status"])
            value = json.loads(capture.getvalue())
            self.assertEqual(code, 0, value)
            self.assertEqual(value["session_state"], "idle")
            self.assertEqual(value["task"]["contract_sha256"], self.pub.contract_id)
            self.assertEqual(value["task"]["worktree"], allocation.worktree)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_nonmatching_contract_rejects_before_effect(self):
        self.assert_reject("TASK_CONTRACT_CHANGED",
                           args={**self.kw, "expected_contract_id": "a" * 64})
        self.assertFalse((self.project / ".worktrees" / "286").exists())

    def test_reauthorized_task_does_not_accept_old_contract(self):
        self.records.reauthorize(
            task="286", branch_ref="refs/heads/issue-286",
            base_revision=self.base, expected_record_id=self.pub.record_id,
            title="New requirement", body="Explicitly changed by Human")
        self.assert_reject("TASK_CONTRACT_CHANGED")
        self.assertFalse((self.project / ".worktrees" / "286").exists())

    def test_missing_canonical_metadata_ref_is_reported(self):
        with patch.object(self.meta, "fetch_tip", return_value=None):
            self.assert_reject("TASK_RECORD_ABSENT")
        self.assertFalse((self.project / ".worktrees" / "286").exists())

    def test_cancelled_task_is_not_allocated(self):
        self.records.set_disposition(
            task="286", branch_ref="refs/heads/issue-286",
            base_revision=self.base, expected_record_id=self.pub.record_id,
            disposition={"kind": "cancelled"})
        self.assert_reject("TASK_RETIRED")

    def test_wrong_task_and_branch_ref_refused(self):
        self.assert_reject("BRANCH_ISSUE_ID_MISMATCH",
                           args={**self.kw, "branch_ref": "refs/heads/issue-999"})
        self.assert_reject("TASK_RECORD_INVALID",
                           args={**self.kw, "branch_ref": "refs/heads/feat/286-not-record"})
        self.assert_reject("TASK_RECORD_ABSENT",
                           args={**self.kw, "task": "287",
                                 "branch_ref": "refs/heads/issue-287"})

    def test_existing_unregistered_path_is_never_overwritten(self):
        target = self.project / ".worktrees" / "286"
        target.mkdir(parents=True)
        (target / "unique.txt").write_text("retain\n")
        self.assert_reject("UNREGISTERED_TARGET_PRESENT")
        self.assertEqual((target / "unique.txt").read_text(), "retain\n")

    def test_unignored_namespace_refuses_before_worktree_creation(self):
        (self.project / ".gitignore").write_text("*.tmp\n")
        self.assert_reject("MANAGED_WORKTREE_NOT_IGNORED")
        self.assertFalse((self.project / ".worktrees").exists())
        self.assertFalse((self.project / ".git" / "kagari-issue-worktree-locks").exists())

    def test_file_only_ignore_does_not_cover_managed_namespace(self):
        (self.project / ".gitignore").write_text("/.worktrees/286/README.md\n")
        self.assert_reject("MANAGED_WORKTREE_NOT_IGNORED")
        self.assertFalse((self.project / ".worktrees").exists())

    def test_incomplete_native_git_registration_is_reported_as_ambiguous(self):
        original = issue_worktree._git
        target = self.project / ".worktrees" / "286"

        def damaged_registration(root, *args, **kwargs):
            result = original(root, *args, **kwargs)
            if "worktree" in args and "add" in args:
                (target / ".git").write_text(
                    "gitdir: /nonexistent-kagari286-gitdir\n")
            return result

        with patch.object(issue_worktree, "_git", side_effect=damaged_registration):
            self.assert_reject("WORKTREE_CREATION_AMBIGUOUS")
        self.assertTrue(target.exists())
        self.assertEqual((target / ".git").read_text(),
                         "gitdir: /nonexistent-kagari286-gitdir\n")

    def test_partial_git_add_result_remains_for_manual_reconciliation(self):
        original = issue_worktree._git

        def interrupted(root, *args, **kwargs):
            if "worktree" in args and "add" in args:
                original(self.project, "branch", "issue-286", self.base)
                raise IssueWorktreeError("INJECTED_GIT_FAILURE")
            return original(root, *args, **kwargs)

        with patch.object(issue_worktree, "_git", side_effect=interrupted):
            self.assert_reject("WORKTREE_CREATION_AMBIGUOUS")
        self.assertEqual(self.git("rev-parse", "refs/heads/issue-286"), self.base)
        self.assert_reject("ORPHAN_BRANCH_REQUIRES_RECONCILIATION")

    def test_branch_checked_out_in_other_worktree_is_refused(self):
        other = self.fixture.temp_root / "outside-286"
        self.git("worktree", "add", "-b", "issue-286", str(other), self.base)
        self.assert_reject("BRANCH_ALREADY_IN_WORKTREE")
        self.assertFalse((self.project / ".worktrees" / "286").exists())

    def test_orphaned_branch_must_not_be_adopted(self):
        self.git("branch", "issue-286", self.base)
        self.assert_reject("ORPHAN_BRANCH_REQUIRES_RECONCILIATION")
        self.assertFalse((self.project / ".worktrees" / "286").exists())

    def test_symbolic_namespace_is_refused(self):
        other = self.fixture.temp_root / "unrelated"
        other.mkdir()
        (self.project / ".worktrees").symlink_to(other, target_is_directory=True)
        self.assert_reject("WORKTREE_NAMESPACE_INVALID")

    def test_root_session_worktree_pointer_tamper_is_refused(self):
        first = self._first()
        wt = Path(first.worktree)
        (wt / ".git").write_text("gitdir: /tmp/forged-admin\n")
        self.assert_reject("GIT_OBSERVATION_FAILED")

    def test_duplicate_process_calls_result_in_one_registration(self):
        def call(_):
            return self.alloc.ensure(**self.kw).status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            states = sorted(pool.map(call, range(2)))
        self.assertEqual(states, ["CREATED", "RESUMED"])
        self.assertEqual(self.git("worktree", "list", "--porcelain").count(
            "branch refs/heads/issue-286"), 1)

    def test_namespace_and_git_common_dir_are_real(self):
        first = self._first()
        wt = Path(first.worktree)
        self.assertTrue((wt / ".git").is_file())
        self.assertEqual(self.git("rev-parse", "--path-format=absolute",
                                  "--git-common-dir", cwd=wt),
                         str(self.project / ".git"))
        self.assertEqual(self.git("rev-parse", "--show-toplevel", cwd=wt),
                         first.worktree)


if __name__ == "__main__":
    unittest.main()
