"""#291: cleanup of one Issue cannot stall unrelated active Task Sessions.

All destructive Git cleanup uses a real *disposable* Git repository and linked
Worktrees. All OpenCode execution is the existing local fake HTTP subprocess.
No real GitHub PR, user Worktree, Main installation or session is modified.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_kagari_vnext_main as task_fixture
from tests import test_metadata_ref as git_fixture
from tools import kagari_vnext_main_service as service
from tools.kagari_vnext_main import ApprovedTask, MainError
from issue_worktree import IssueWorktreeAllocator
from issue_worktree_cleanup import (
    MergedPullRequest, IssueWorktreeError, CleanupReceipt,
)


class IssueCleanupIsolationTests(unittest.TestCase):
    def setUp(self):
        self.source = task_fixture.MainOrchestratorTest("runTest")
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.host_lock_patch=patch.object(
            service,"_host_state_home",return_value=self.source.fixture.temp_root)
        self.host_lock_patch.start()
        self.addCleanup(self.host_lock_patch.stop)
        self.root = self.source.project
        git_fixture.git("branch", "-m", "main", cwd=self.root)
        self.published = self.source.writer.create(
            task="292", branch_ref="refs/heads/issue-292",
            base_revision=self.source.base,
            title="Second eligible Issue", body="Keep work ongoing")
        self.source.facts[292] = {
            "repository": "acme/widgets", "number": 292, "state": "OPEN",
            "title": "Second eligible Issue", "body": "Keep work ongoing"}
        self.source.approved[292] = ApprovedTask(
            292, "refs/heads/issue-292", self.source.base,
            self.published.contract_id)
        self.source.ports[292] = task_fixture.next_port()
        self.main = self.source.make_main()
        # The Task eligible for cleanup is already merged and its GitHub
        # Issue may be CLOSED. Main's normal dispatch _spec requires OPEN,
        # which is why cleanup must be a separate per-Issue operation.
        self.source.facts[291]["state"] = "CLOSED"
        wt = IssueWorktreeAllocator(self.source.store).ensure(
            task="291", branch_ref=self.source.branch_ref,
            base_revision=self.source.base,
            expected_contract_id=self.source.pub.contract_id)
        self.retired_worktree = Path(wt.worktree)
        (self.retired_worktree / "result.txt").write_text(
            "Committed Task #291 changes\n")
        git_fixture.git("add", "result.txt", cwd=self.retired_worktree)
        git_fixture.git("commit", "-q", "-m", "Task 291 done",
                        cwd=self.retired_worktree)
        self.retired_head = self.git("rev-parse", "HEAD",
                                    cwd=self.retired_worktree)
        git_fixture.git("merge", "--no-ff", "-m", "Merge Issue 291",
                        "issue-291", cwd=self.root)
        self.merged_head = self.git("rev-parse", "HEAD")
        self.merged = True
        self.session_released = False
        self.plan = {
            "schema_version": 1, "project_root": str(self.root),
            "runtime_home": str(self.source.runtime),
            "repository": "acme/widgets",
            "opencode_bin": str(self.source.bin),
            # The rejoined #163 prompt is identified by its frozen model too.
            "model": {"provider_id": "openai", "model_id": "gpt-6-sol"},
            "tasks": [
                {
                    "issue": 291, "branch_ref": self.source.branch_ref,
                    "base_revision": self.source.base,
                    "contract_id": self.source.pub.contract_id,
                    "port": self.source.ports[291],
                    "cleanup": {"pull_request": 97,
                                "base_ref": "refs/heads/main"},
                },
                {
                    "issue": 292, "branch_ref": "refs/heads/issue-292",
                    "base_revision": self.source.base,
                    "contract_id": self.published.contract_id,
                    "port": self.source.ports[292],
                },
            ],
        }
        self.config = self.source.fixture.temp_root / "main-selection.json"
        self.write_selection()

    def git(self, *args, cwd=None):
        return git_fixture.git(*args, cwd=cwd or self.root).decode().strip()

    def write_selection(self):
        self.config.write_text(json.dumps(self.plan) + "\n", encoding="utf-8")

    def read_pr(self, request):
        self.assertEqual(request.repository, "acme/widgets")
        self.assertEqual(request.issue, 291)
        self.assertEqual(request.number, 97)
        return MergedPullRequest(
            "acme/widgets", 97, "MERGED" if self.merged else "OPEN",
            "refs/heads/main", self.source.branch_ref,
            self.retired_head, self.merged_head)

    def handlers(self):
        parsed = service.load_selection(self.config)
        with patch.object(service, "github_pull_request_reader",
                          return_value=self.read_pr) as pr_reader:
            with patch.object(
                service, "stopped_session_from_runtime",
                return_value=lambda _request: self.session_released,
            ) as session_checker:
                handlers = service.build_cleanup_handlers(self.main, parsed)
        pr_reader.assert_called_once_with("acme/widgets", gh="gh")
        session_checker.assert_called_once_with(
            self.source.runtime / "issue-291")
        self.assertEqual(list(handlers), [291])
        return handlers

    def assert_other_session_alive(self):
        process_file = self.source.runtime / "issue-292" / "process.json"
        data = json.loads(process_file.read_text())
        os.kill(data["pid"], 0)
        self.assertEqual(len(self.source.prompts()), 1)
        self.assertTrue(
            (self.root / ".worktrees" / "292").is_dir())
        self.assertTrue((self.root / ".worktrees" / "292" / ".git").is_file())

    def test_direct_cleanup_handler_requires_live_owner_before_any_side_effect(self):
        self.session_released=True
        callback=self.handlers()[291]
        branch_before=self.git("for-each-ref","--format=%(refname)")
        with self.assertRaises(MainError) as denied:
            callback()
        self.assertEqual(denied.exception.code,"MAIN_OWNER_REQUIRED")
        self.assertTrue(self.retired_worktree.exists())
        self.assertEqual(self.git("for-each-ref","--format=%(refname)"), branch_before)
        self.assertEqual(len(self.source.prompts()),0)
        with service.MainSupervisor(self.main):
            receipt=callback()
            self.assertEqual(receipt.state,"CLEANED")
        self.assertFalse(self.retired_worktree.exists())
        with self.assertRaises(MainError) as after:
            callback()
        self.assertEqual(after.exception.code,"MAIN_OWNER_REQUIRED")

    def test_cross_clone_lock_owner_does_not_allow_callback_to_cleanup(self):
        from types import SimpleNamespace
        other_root=self.source.fixture.temp_root/"other-host-cleanup-clone"
        subprocess.run(["git","init","-q",str(other_root)],check=True)
        other=SimpleNamespace(store=SimpleNamespace(
            root=other_root,repository="ACME/Widgets"),approved={})
        self.session_released=True
        callback=self.handlers()[291]
        with service.MainSupervisor(other):
            with self.assertRaises(MainError) as denied:
                callback()
            self.assertEqual(denied.exception.code,"MAIN_OWNER_REQUIRED")
            self.assertTrue(self.retired_worktree.exists())
        # Owner of an unrelated caller cannot promote its permission into
        # the real Main's cleanup callback.
        self.assertTrue(self.retired_worktree.exists())
        with service.MainSupervisor(self.main):
            self.assertEqual(callback().state,"CLEANED")

    def test_refused_retirement_preserves_worktree_and_other_session_continues(self):
        handlers = self.handlers()
        with service.MainSupervisor(
            self.main, cleanup_for_issue=handlers,
        ) as supervisor:
            first = supervisor.cycle()
            self.assertEqual(first["mode"], "dispatch")
            self.assertEqual([x["issue"] for x in first["tasks"]],
                             [291, 292])
            removed, active = first["tasks"]
            self.assertEqual(removed["operation"], "cleanup")
            self.assertEqual(removed["status"], "blocked")
            self.assertEqual(removed["reason"], "SESSION_RELEASE_UNVERIFIED")
            self.assertTrue(self.retired_worktree.exists())
            self.assertTrue((self.retired_worktree / "result.txt").exists())
            self.assertEqual(active["prompt_state"], "SUBMITTED")
            self.assert_other_session_alive()

            # A second repository Main cycle must neither repeat this
            # blocked cleanup as model work nor duplicate Issue #292.
            second = supervisor.cycle()
            self.assertEqual(second["tasks"][0]["status"], "blocked")
            self.assertEqual(second["tasks"][1]["prompt_state"],
                             "ALREADY_DISPATCHED")
            self.assert_other_session_alive()
            self.assertEqual(self.git("rev-parse", "HEAD"), self.merged_head)

    def test_retirement_after_positive_release_never_interrupts_sibling(self):
        with service.MainSupervisor(
            self.main, cleanup_for_issue=self.handlers(),
        ) as supervisor:
            first = supervisor.cycle()
            self.assertEqual(first["tasks"][0]["reason"],
                             "SESSION_RELEASE_UNVERIFIED")
            self.assert_other_session_alive()

            self.session_released = True
            second = supervisor.cycle()
            self.assertEqual(second["tasks"][0]["status"], "ok")
            self.assertEqual(second["tasks"][0]["operation"], "cleanup")
            self.assertEqual(second["tasks"][0]["cleanup_state"], "CLEANED")
            self.assertEqual(second["tasks"][1]["prompt_state"],
                             "ALREADY_DISPATCHED")
            self.assertFalse(self.retired_worktree.exists())
            self.assertNotIn("refs/heads/issue-291",
                             self.git("for-each-ref", "--format=%(refname)"))
            self.assertIn("Committed Task #291 changes",
                          self.git("show", "HEAD:result.txt"))
            self.assert_other_session_alive()

            final = supervisor.cycle()
            self.assertEqual(final["tasks"][0]["cleanup_state"],
                             "ALREADY_CLEANED")
            self.assertEqual(final["tasks"][1]["prompt_state"],
                             "ALREADY_DISPATCHED")
            self.assert_other_session_alive()

    def test_dirty_cleanup_blocks_only_retired_issue(self):
        self.session_released = True
        (self.retired_worktree / "keep-me.txt").write_text("untracked")
        with service.MainSupervisor(
            self.main, cleanup_for_issue=self.handlers(),
        ) as supervisor:
            outcome = supervisor.cycle()
        self.assertEqual(outcome["tasks"][0]["reason"],
                         "WORKTREE_HAS_UNPRESERVED_DATA")
        self.assertEqual(outcome["tasks"][1]["prompt_state"], "SUBMITTED")
        self.assertEqual((self.retired_worktree / "keep-me.txt").read_text(),
                         "untracked")
        self.assert_other_session_alive()

    def test_unmerged_cleanup_blocks_only_retired_issue(self):
        self.session_released = True
        self.merged = False
        with service.MainSupervisor(
            self.main, cleanup_for_issue=self.handlers(),
        ) as supervisor:
            outcome = supervisor.cycle()
        self.assertEqual(outcome["tasks"][0]["reason"],
                         "PR_NOT_EXACT_MERGED_TASK")
        self.assertEqual(outcome["tasks"][1]["prompt_state"], "SUBMITTED")
        self.assertTrue(self.retired_worktree.exists())
        self.assert_other_session_alive()

    def test_cleanup_host_exception_is_isolated_per_issue(self):
        def broken_host_reader():
            raise RuntimeError("fixture host dependency went away")
        with service.MainSupervisor(
            self.main, cleanup_for_issue={291: broken_host_reader},
        ) as supervisor:
            outcome = supervisor.cycle()
        self.assertEqual(outcome["tasks"][0]["reason"],
                         "CLEANUP_HOST_UNAVAILABLE")
        self.assertEqual(outcome["tasks"][1]["prompt_state"], "SUBMITTED")
        self.assertTrue(self.retired_worktree.exists())
        self.assert_other_session_alive()

    def test_observe_only_never_executes_selected_cleanup(self):
        def must_not_cleanup():
            self.fail("Read-only Main cycle attempted cleanup")
        lock = self.root / ".git" / "kagari-repository-main.lock"
        self.assertFalse(lock.exists())
        with service.MainSupervisor(
            self.main, cleanup_for_issue={291: must_not_cleanup},
            observe_only=True,
        ) as supervisor:
            outcome = supervisor.cycle(dispatch=False)
            with self.assertRaises(MainError) as attempt:
                supervisor.cycle(dispatch=True)
            self.assertEqual(attempt.exception.code, "MAIN_OBSERVE_ONLY")
        self.assertFalse(lock.exists())
        self.assertEqual(outcome["mode"], "observe")
        self.assertEqual(outcome["tasks"][0]["status"], "blocked")
        self.assertEqual(outcome["tasks"][1]["observation"]["runtime"],
                         "NOT_STARTED")
        self.assertTrue(self.retired_worktree.exists())
        self.assertFalse((self.source.runtime / "issue-292").exists())

    def test_cleanup_selection_requires_exact_pr_base_and_task(self):
        for invalid in (
            {"pull_request": 0, "base_ref": "refs/heads/main"},
            {"pull_request": True, "base_ref": "refs/heads/main"},
            {"pull_request": 97, "base_ref": "main"},
            {"pull_request": 97, "base_ref": self.source.branch_ref},
            {"pull_request": 97, "base_ref": "refs/heads/main",
             "force": True},
            {"pull_request": 98, "base_ref": "refs/heads/main"},
        ):
            with self.subTest(invalid=invalid):
                self.plan["tasks"][0]["cleanup"] = invalid
                self.plan["tasks"][0]["pr_number"] = 97
                self.write_selection()
                with self.assertRaises(MainError) as error:
                    service.load_selection(self.config)
                self.assertEqual(error.exception.code,
                                 "MAIN_PLAN_INVALID")
        self.assertTrue(self.retired_worktree.exists())

    def test_packaged_main_one_cleanup_failure_and_other_issue_if_supplied(self):
        package = os.environ.get("KAGARI_VNEXT_MAIN_PACKAGE")
        if not package:
            self.skipTest("Packaged Main path was not supplied")
        cli = Path(package) / "bin" / "kagari-vnext-main"
        self.assertTrue(cli.is_file())
        facts_292 = json.dumps(self.source.facts[292], sort_keys=True)
        merged_pr = json.dumps({
            "number": 97,
            "state": "MERGED",
            "mergedAt": "2026-10-25T20:00:00Z",
            "baseRefName": "main", "headRefName": "issue-291",
            "headRefOid": self.retired_head,
            "mergeCommit": {"oid": self.merged_head},
            "headRepository": {"nameWithOwner": "acme/widgets"},
            "isCrossRepository": False,
        }, sort_keys=True)
        gh = self.source.fixture.temp_root / "gh-issue-cleanup-stub"
        gh.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"issue\" ]; then\n"
            "  printf '%s\\n' " + shlex.quote(facts_292) + "\n"
            "elif [ \"$1\" = \"pr\" ]; then\n"
            "  printf '%s\\n' " + shlex.quote(merged_pr) + "\n"
            "else\n  exit 22\nfi\n")
        gh.chmod(0o700)
        self.plan["github_bin"] = str(gh)
        self.write_selection()

        # A disposable, already stopped #163 Session record for the retired
        # Issue. The other Issue's actual fake OpenCode server remains live.
        stopped = subprocess.Popen([sys.executable, "-c", "pass"])
        stopped.wait(timeout=5)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            unused_port = sock.getsockname()[1]
        runtime = self.source.runtime / "issue-291"
        runtime.mkdir(parents=True)
        (runtime / "context.json").write_text(json.dumps({
            "repository": "acme/widgets", "issue": 291,
            "worktree": str(self.retired_worktree),
            "branch_ref": self.source.branch_ref,
            "server_url": f"http://127.0.0.1:{unused_port}",
            "session_id": "ses_disposable291999999",
        }))
        (runtime / "process.json").write_text(json.dumps({
            "pid": stopped.pid, "port": unused_port,
            "worktree": str(self.retired_worktree),
        }))
        def invoke():
            result = subprocess.run(
                [str(cli), "--config", str(self.config), "dispatch"],
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
                timeout=35, cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(result.returncode, 0,
                             (result.stdout, result.stderr))
            output = json.loads(result.stdout)
            self.assertEqual(output["mode"], "dispatch")
            return output["tasks"]

        # The host already established the authorized Task/initial checkpoint
        # through the test facade. The packaged CLI has no production #194
        # grant adapter and must *resume*, never bootstrap an unapproved Task.
        with service.MainSupervisor(self.main):
            existing=self.main.dispatch(292)
        self.assertEqual(existing["prompt_state"], "SUBMITTED")
        # Real packaged Main, real Issue-291 native Git cleanup, read-only
        # fixture gh and a genuinely independent already-live Issue-292 server.
        first = invoke()
        self.assertEqual(first[0]["cleanup_state"], "CLEANED")
        self.assertEqual(first[1].get("prompt_state"), "ALREADY_DISPATCHED", first)
        self.assertFalse(self.retired_worktree.exists())
        self.assert_other_session_alive()
        second = invoke()
        self.assertEqual(second[0]["cleanup_state"], "ALREADY_CLEANED")
        self.assertEqual(second[1]["prompt_state"], "ALREADY_DISPATCHED")
        self.assert_other_session_alive()

    def test_non_selected_issues_have_no_implicit_cleanup_handler(self):
        self.plan["tasks"][0].pop("cleanup")
        self.write_selection()
        # Merely observing the merged PR is not a deletion permission.
        self.assertEqual(service.build_cleanup_handlers(
            self.main, service.load_selection(self.config)), {})
        self.assertTrue(self.retired_worktree.exists())


if __name__ == "__main__":
    unittest.main()
