"""AC-WT-04: exact, post-Merge Issue Worktree cleanup via real local Git.

Every delete happens in a disposable fixture's Git repository. No live
GitHub Issue/PR, real user Project, OpenCode Session, or GA environment is
removed or modified by this suite.
"""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_issue_worktree_v4 as issue_fixtures
from issue_worktree import IssueWorktreeError
import issue_worktree_cleanup as cleanup_module
from issue_worktree_cleanup import (
    IssueWorktreeCleanup, MergedPullRequest, PullRequestRequest,
    SessionReleaseRequest, github_pull_request_reader, stopped_session_from_runtime,
)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.inner = issue_fixtures.IssueWorktreeTest("runTest")
        self.inner.setUp()
        self.addCleanup(self.inner.doCleanups)
        self.project = self.inner.project
        self.alloc = self.inner.alloc
        self.kw = self.inner.kw
        self.git = self.inner.git
        self.git("branch", "-m", "main")
        self.target = self.project / ".worktrees" / "286"
        self.main_ref = "refs/heads/main"
        self.current_merge = None
        self.current_head = None
        self.read_calls = []
        self.session_calls = []
        self.stopped = True
        self.facade = IssueWorktreeCleanup(
            self.alloc,
            read_pull_request=self.pr_facts,
            session_released=self.session_read,
        )

    def pr_facts(self, request: PullRequestRequest):
        self.read_calls.append(request)
        return MergedPullRequest(
            "acme/widgets", 28, "MERGED", self.main_ref,
            "refs/heads/issue-286", self.current_head,
            self.current_merge)

    def session_read(self, request):
        self.session_calls.append(request)
        return self.stopped

    def run_cleanup(self, **overrides):
        return self.facade.cleanup(**(
            self.kw | {"pull_request": 28} | overrides))

    def reject(self, code, **kwargs):
        with self.assertRaises(IssueWorktreeError) as raised:
            self.run_cleanup(**kwargs)
        self.assertEqual(raised.exception.code, code)

    def _prepared(self, *, own_commit: bool = True, merged: bool = True):
        self.inner._first()
        if own_commit:
            (self.target / "issue-work.txt").write_text(
                "Unique Issue-scoped Product work\n")
            self.git("add", "issue-work.txt", cwd=self.target)
            self.git("commit", "-q", "-m", "Issue 286 implementation",
                     cwd=self.target)
        self.current_head = self.git("rev-parse", "HEAD", cwd=self.target)
        if merged:
            self.git("merge", "--no-ff", "-m", "Merge Issue 286",
                     "issue-286", cwd=self.project)
            self.current_merge = self.git("rev-parse", "HEAD")
        else:
            self.current_merge = self.git("rev-parse", "HEAD")
        return self.current_head

    def test_safe_merge_commit_cleanup_and_idempotent_retry(self):
        head = self._prepared()
        pre_main_head = self.git("rev-parse", "HEAD")
        first = self.run_cleanup()
        self.assertEqual(first.state, "CLEANED")
        self.assertEqual(first.head_oid, head)
        self.assertEqual(first.merge_oid, pre_main_head)
        self.assertEqual(first.worktree, str(self.target))
        self.assertFalse(self.target.exists())
        self.assertEqual(self.git("rev-parse", "HEAD"), pre_main_head)
        self.assertNotIn("refs/heads/issue-286",
                         self.git("for-each-ref", "--format=%(refname)"))
        self.assertIn("Unique Issue-scoped Product work",
                      self.git("show", "HEAD:issue-work.txt"))
        self.assertGreaterEqual(len(self.session_calls), 2)
        self.assertEqual(self.session_calls[0].worktree, str(self.target))
        self.assertEqual(self.read_calls[0].issue, 286)
        second = self.run_cleanup()
        self.assertEqual(second.state, "ALREADY_CLEANED")
        self.assertEqual(second.head_oid, head)
        self.assertFalse(self.target.exists())
        with self.assertRaises(IssueWorktreeError) as err:
            self.alloc.ensure(**self.kw)
        self.assertEqual(err.exception.code, "WORKTREE_CLEANED_OR_CLEANUP_PENDING")

    def test_preserves_original_dirty_staged_and_sibling_issue(self):
        self._prepared()
        prior = self.inner.records.create(
            task="287", branch_ref="refs/heads/issue-287",
            base_revision=self.inner.base, title="Separate Issue",
            body="Do not remove sibling")
        sibling = self.alloc.ensure(
            task="287", base_revision=self.inner.base,
            branch_ref="refs/heads/issue-287",
            expected_contract_id=prior.contract_id)
        sibling_path = Path(sibling.worktree)
        (sibling_path / "sibling-unique.txt").write_text("preserve")
        (self.project / "original-untracked.txt").write_text("preserve")
        (self.project / "original-staged.txt").write_text("preserve")
        self.git("add", "original-staged.txt")
        before = self.git("status", "--porcelain=v1", "--untracked-files=all",
                          cwd=self.project)
        self.run_cleanup()
        self.assertEqual(
            self.git("status", "--porcelain=v1", "--untracked-files=all",
                     cwd=self.project), before)
        self.assertEqual((self.project / "original-untracked.txt").read_text(),
                         "preserve")
        self.assertEqual((sibling_path / "sibling-unique.txt").read_text(),
                         "preserve")
        self.assertEqual(self.alloc._registered(sibling_path,
                                               "refs/heads/issue-287"),
                         sibling.head)

    def test_open_pr_cannot_authorize_worktree_deletion(self):
        self._prepared()
        with patch.object(self.facade, "read_pull_request",
                          return_value=replace(
                              self.pr_facts(PullRequestRequest(
                                  "acme/widgets", 286, 28,
                                  "refs/heads/issue-286")),
                              state="OPEN")):
            self.reject("PR_NOT_EXACT_MERGED_TASK")
        self.assertTrue(self.target.exists())

    def test_no_local_merge_proof_refuses_squash_and_stale_main(self):
        self._prepared(merged=False)
        # PR is factually merged remotely but the local main does not
        # include the current Task branch; no proof of preserved work.
        self.reject("TASK_HISTORY_NOT_IN_MERGE")
        self.assertTrue(self.target.exists())
        self.assertTrue((self.target / "issue-work.txt").exists())

    def test_squash_merge_has_no_full_commit_ancestry(self):
        self._prepared(merged=False)
        self.git("merge", "--squash", "issue-286", cwd=self.project)
        self.git("commit", "-q", "-m", "Squash merged Issue", cwd=self.project)
        self.current_merge = self.git("rev-parse", "HEAD")
        self.reject("TASK_HISTORY_NOT_IN_MERGE")
        self.assertTrue(self.target.exists())

    def test_incorrect_pr_identity_or_stale_head_blocks(self):
        self._prepared()
        valid = self.pr_facts(PullRequestRequest(
            "acme/widgets", 286, 28, "refs/heads/issue-286"))
        for variant in (
            replace(valid, head_oid="a" * 40),
            replace(valid, base_ref="refs/heads/other"),
            replace(valid, repository="foreign/repo"),
            replace(valid, number=29),
            replace(valid, head_ref="refs/heads/issue-287"),
        ):
            with self.subTest(variant=variant):
                with patch.object(self.facade, "read_pull_request",
                                  return_value=variant):
                    self.reject(
                        "PR_HEAD_MISMATCH" if variant.head_oid != valid.head_oid
                        and variant == replace(valid, head_oid=variant.head_oid)
                        else "PR_NOT_EXACT_MERGED_TASK")
                self.assertTrue(self.target.exists())

    def test_active_session_or_unknown_ownership_blocks(self):
        self._prepared()
        self.stopped = False
        self.reject("SESSION_RELEASE_UNVERIFIED")
        self.stopped = None
        self.reject("SESSION_RELEASE_UNVERIFIED")
        with patch.object(self.facade, "session_released",
                          side_effect=ConnectionError):
            self.reject("SESSION_RELEASE_UNVERIFIED")
        self.assertTrue(self.target.exists())

    def test_tracked_dirty_data_blocks_removal(self):
        self._prepared()
        (self.target / "issue-work.txt").write_text("uncommitted Product edit\n")
        self.reject("WORKTREE_HAS_UNPRESERVED_DATA")
        self.assertTrue(self.target.exists())

    def test_untracked_unique_data_blocks_removal(self):
        self._prepared()
        (self.target / "unique-user.txt").write_text("retain untracked\n")
        self.reject("WORKTREE_HAS_UNPRESERVED_DATA")
        self.assertTrue((self.target / "unique-user.txt").exists())

    def test_ignored_data_blocks_removal(self):
        self.inner._first()
        (self.target / ".gitignore").write_text("/.worktrees/\n*.tmp\n")
        self.git("add", ".gitignore", cwd=self.target)
        self.git("commit", "-q", "-m", "Tracked ignore pattern", cwd=self.target)
        self.current_head = self.git("rev-parse", "HEAD", cwd=self.target)
        self.git("merge", "--no-ff", "-m", "Merge fixture",
                 "issue-286", cwd=self.project)
        self.current_merge = self.git("rev-parse", "HEAD")
        (self.target / "fixture-output.tmp").write_text("ignored but unique\n")
        self.assertEqual(
            self.git("status", "--porcelain=v1", "--untracked-files=all",
                     cwd=self.target), "")
        self.reject("WORKTREE_HAS_UNPRESERVED_DATA")
        self.assertTrue((self.target / "fixture-output.tmp").exists())

    def test_absent_target_without_cleanup_intent_does_not_delete_branch(self):
        self._prepared()
        self.git("worktree", "remove", "--", str(self.target))
        self.reject("WORKTREE_NOT_REGISTERED_FOR_CLEANUP")
        self.assertIn("refs/heads/issue-286",
                      self.git("for-each-ref", "--format=%(refname)"))

    def test_explicit_worktree_lock_blocks_cleanup(self):
        self._prepared()
        self.git("worktree", "lock", str(self.target), cwd=self.project)
        self.reject("WORKTREE_UNAVAILABLE")
        self.assertTrue(self.target.exists())

    def test_lost_ack_after_worktree_remove_resumes_branch_cleanup(self):
        head = self._prepared()
        original = cleanup_module._git
        def lose_ack(root, *args, **kwargs):
            result = original(root, *args, **kwargs)
            if "worktree" in args and "remove" in args:
                raise IssueWorktreeError("simulated lost response")
            return result
        with patch.object(cleanup_module, "_git", side_effect=lose_ack):
            self.reject("WORKTREE_REMOVE_OUTCOME_UNCERTAIN")
        self.assertFalse(self.target.exists())
        self.assertEqual(self.git("rev-parse", "refs/heads/issue-286"), head)
        after = self.run_cleanup()
        self.assertEqual(after.state, "ALREADY_CLEANED")
        self.assertFalse(self.target.exists())

    def test_lost_ack_after_branch_delete_is_idempotent(self):
        self._prepared()
        original = cleanup_module._git
        def lose_ack(root, *args, **kwargs):
            result = original(root, *args, **kwargs)
            if "branch" in args and "-d" in args:
                raise IssueWorktreeError("simulated lost response")
            return result
        with patch.object(cleanup_module, "_git", side_effect=lose_ack):
            self.reject("BRANCH_REMOVE_OUTCOME_UNCERTAIN")
        after = self.run_cleanup()
        self.assertEqual(after.state, "ALREADY_CLEANED")
        self.assertFalse(self.target.exists())

    def test_journal_mismatch_prevents_cross_task_replay(self):
        self._prepared()
        self.run_cleanup()
        self.reject("CLEANUP_INTENT_MISMATCH", pull_request=29)

    def test_disposed_or_changed_task_contract_refuses(self):
        self._prepared()
        self.reject("TASK_CONTRACT_CHANGED",
                    expected_contract_id="a" * 64)
        self.inner.records.set_disposition(
            task="286", branch_ref=self.kw["branch_ref"],
            base_revision=self.kw["base_revision"],
            expected_record_id=self.inner.pub.record_id,
            disposition={"kind": "cancelled"})
        self.reject("TASK_RETIRED")
        self.assertTrue(self.target.exists())

    def test_gh_merged_reader_validates_exact_pr_and_never_writes(self):
        response = {
            "number": 28, "state": "MERGED",
            "mergedAt": "2026-10-25T13:00:00Z",
            "baseRefName": "main", "headRefName": "issue-286",
            "headRefOid": "b" * 40, "mergeCommit": {"oid": "c" * 40},
            "headRepository": {"nameWithOwner": "acme/widgets"},
            "isCrossRepository": False}
        request = PullRequestRequest(
            "acme/widgets", 286, 28, "refs/heads/issue-286")
        gh = github_pull_request_reader("acme/widgets")
        with patch.object(cleanup_module.subprocess, "run",
                          return_value=subprocess.CompletedProcess(
                              ["gh"], 0, json.dumps(response), "")) as tool:
            result = gh(request)
        self.assertEqual(result.head_oid, "b" * 40)
        self.assertEqual(result.merge_oid, "c" * 40)
        args = tool.call_args.args[0]
        self.assertEqual(args[:7], [
            "gh", "pr", "view", "28", "--repo", "acme/widgets", "--json"])
        self.assertEqual(tool.call_args.kwargs["stdin"],
                         subprocess.DEVNULL)
        for overrides, code in (
            ({"state": "CLOSED", "mergedAt": None}, "PR_NOT_PROVEN_MERGED"),
            ({"isCrossRepository": True}, "PR_IDENTITY_MISMATCH"),
            ({"headRefName": "issue-287"}, "PR_IDENTITY_MISMATCH"),
        ):
            with self.subTest(overrides=overrides):
                with patch.object(cleanup_module.subprocess, "run",
                                  return_value=subprocess.CompletedProcess(
                                      ["gh"], 0, json.dumps(response | overrides),
                                      "")):
                    with self.assertRaises(IssueWorktreeError) as error:
                        gh(request)
                    self.assertEqual(error.exception.code, code)

    def test_exact_stopped_root_session_evidence_requires_dead_pid_and_closed_port(self):
        runtime = self.inner.fixture.temp_root / "session-runtime"
        runtime.mkdir()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-c", "import time;time.sleep(30)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        request = SessionReleaseRequest(
            "acme/widgets", 286, self.kw["expected_contract_id"],
            str(self.target), self.kw["branch_ref"], "a" * 40)
        (runtime / "context.json").write_text(json.dumps({
            "repository": "acme/widgets", "issue": 286,
            "worktree": str(self.target),
            "branch_ref": self.kw["branch_ref"],
            "server_url": f"http://127.0.0.1:{port}",
            "session_id": "ses_1234567890123",
        }))
        (runtime / "process.json").write_text(json.dumps({
            "pid": process.pid, "port": port,
            "worktree": str(self.target),
        }))
        stopped = stopped_session_from_runtime(runtime)
        try:
            self.assertFalse(stopped(request))
            process.terminate()
            process.wait(timeout=5)
            self.assertTrue(stopped(request))
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", port))
                listener.listen(1)
                self.assertFalse(stopped(request))
            self.assertTrue(stopped(request))
            self.assertFalse(stopped(replace(request, issue=287)))
            (runtime / "context.json").unlink()
            self.assertFalse(stopped(request))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
