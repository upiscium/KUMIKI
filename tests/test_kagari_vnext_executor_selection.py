"""#340 closed executor selection and deliberately unqualified Claude path."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from tests import test_kagari_vnext_main_service as service_fixture
from tools import kagari_vnext_main as main_module
from tools import kagari_vnext_main_service as service
from tools.kagari_vnext_executor_selection import (
    CLAUDE_CLI, OPENCODE, claude_runtime_diagnostic,
)
from tools.kagari_vnext_main import ApprovedTask, MainError
from issue_worktree import IssueWorktreeAllocator


class ExecutorSelectionTests(unittest.TestCase):
    """Compose the established local Git/fake OpenCode fixture, no host reads."""

    def setUp(self):
        service_fixture.MainServiceTest.setUp(self)

    def save_plan(self):
        self.config.write_text(json.dumps(self.plan) + "\n", encoding="utf-8")

    def plan_for(self, executor=CLAUDE_CLI):
        plan = copy.deepcopy(self.plan)
        task = plan["tasks"][0]
        task["executor"] = executor
        if executor == CLAUDE_CLI:
            task.pop("port", None)
        return plan

    def host(self):
        return service.MainHostCapabilities(
            read_issue=lambda number: self.source.facts[number].copy(),
            initial_collaboration_for_issue=lambda _issue, _worktree: None,
        )

    def make_main(self, plan=None):
        return service.build_main(plan or self.plan_for(), host=self.host())

    def assert_claude_observation(self, observed):
        self.assertEqual(set(observed), {
            "status", "issue", "repository", "approved_task", "executor",
            "task_view", "runtime", "kickoff",
        })
        self.assertEqual(set(observed["approved_task"]), {
            "repository", "issue", "contract_id", "title", "base_revision", "branch_ref",
        })
        # Independent literal contract: do not let a changed production helper
        # add a fabricated session/Ask field to both actual and expected output.
        self.assertEqual(observed["runtime"], {
            "executor": "claude-cli", "qualification": "UNQUALIFIED",
            "dispatch_allowed": False, "task_completed": False,
            "session_state": "UNKNOWN", "ask_capability": "UNQUALIFIED",
            "effective_policy": "UNVERIFIED", "reason": "CLAUDE_EXECUTOR_UNQUALIFIED",
        })
        self.assertEqual(observed["kickoff"], "UNKNOWN")

    def test_closed_executor_schema_and_legacy_default(self):
        legacy = service.load_selection(self.config)
        self.assertNotIn("executor", legacy["tasks"][0])
        self.assertEqual(self.make_main(legacy).approved[291].executor, OPENCODE)

        explicit = self.plan_for(OPENCODE)
        explicit["tasks"][0]["port"] = self.source.ports[291]
        self.plan = explicit
        self.save_plan()
        self.assertEqual(service.load_selection(self.config)["tasks"][0]["executor"],
                         OPENCODE)

        claude = self.plan_for()
        self.assertNotIn("port", claude["tasks"][0])
        self.assertEqual(service._validate_selection(claude), claude)
        self.assertEqual(self.make_main(claude).approved[291].executor, CLAUDE_CLI)

        invalid_tasks = []
        for value in (None, True, 1, "Claude", "Claude-cli", "claude-cli "):
            item = copy.deepcopy(claude["tasks"][0])
            item["executor"] = value
            invalid_tasks.append(item)
        for extra in ({"profile": "anything"}, {"configdir": "/tmp"},
                      {"probe": True}, {"qualification": "qualified"}):
            invalid_tasks.append({**claude["tasks"][0], **extra})
        invalid_tasks.extend((
            {**claude["tasks"][0], "port": self.source.ports[291]},
            {**claude["tasks"][0], "cleanup": None},
        ))
        for item in invalid_tasks:
            with self.subTest(item=item):
                bad = copy.deepcopy(claude)
                bad["tasks"] = [item]
                with self.assertRaises(MainError) as error:
                    service._validate_selection(bad)
                self.assertEqual(error.exception.code, "MAIN_PLAN_INVALID")
                with self.assertRaises(MainError) as direct:
                    service.build_main(bad)
                self.assertEqual(direct.exception.code, "MAIN_PLAN_INVALID")

        bad_opencode = copy.deepcopy(self.plan)
        bad_opencode["tasks"][0]["port"] = None
        with self.assertRaises(MainError):
            service.build_main(bad_opencode)

    def test_duplicate_json_keys_and_approved_task_type_validation(self):
        self.config.write_text(
            '{"schema_version":1,"project_root":"/tmp",'
            '"repository":"acme/widgets","runtime_home":"/tmp/run",'
            '"tasks":[{"issue":291,"branch_ref":"refs/heads/x",'
            '"base_revision":"' + self.source.base + '","contract_id":"'
            + self.source.pub.contract_id + '","port":4001,'
            '"executor":"claude-cli","executor":"opencode"}]}',
            encoding="utf-8")
        with self.assertRaises(MainError) as error:
            service.load_selection(self.config)
        self.assertEqual(error.exception.code, "MAIN_PLAN_INVALID")

        for invalid in (None, True, 0, "Claude", "opencode "):
            with self.subTest(executor=invalid):
                with self.assertRaises(MainError) as rejected:
                    ApprovedTask(291, "refs/heads/issue-291", self.source.base,
                                self.source.pub.contract_id, invalid)
                self.assertEqual(rejected.exception.code, "MAIN_EXECUTOR_INVALID")

    def test_legacy_python_plan_version_is_not_a_file_schema_bypass(self):
        legacy = copy.deepcopy(self.plan)
        del legacy["schema_version"]
        main = service.build_main(legacy, host=self.host())
        self.assertEqual(main.approved[291].executor, OPENCODE)
        self.assertNotIn("schema_version", legacy)
        self.assertEqual(service.build_cleanup_handlers(main, legacy), {})
        self.config.write_text(json.dumps(legacy), encoding="utf-8")
        with self.assertRaises(MainError) as file_error:
            service.load_selection(self.config)
        self.assertEqual(file_error.exception.code, "MAIN_PLAN_INVALID")
        for invalid_version in (None, True, "1", 0, 2):
            with self.subTest(schema_version=invalid_version):
                invalid = {**legacy, "schema_version": invalid_version}
                with self.assertRaises(MainError) as direct_error:
                    service.build_main(invalid, host=self.host())
                self.assertEqual(direct_error.exception.code, "MAIN_PLAN_INVALID")
                with self.assertRaises(MainError):
                    service.build_cleanup_handlers(main, invalid)
                self.config.write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaises(MainError) as serialized_error:
                    service.load_selection(self.config)
                self.assertEqual(serialized_error.exception.code, "MAIN_PLAN_INVALID")
        legacy["tasks"][0]["executor"] = "unrecognized"
        with self.assertRaises(MainError) as executor_error:
            service.build_main(legacy, host=self.host())
        self.assertEqual(executor_error.exception.code, "MAIN_PLAN_INVALID")

    def test_main_owns_read_only_cloned_selection_and_detects_forced_drift(self):
        caller_choice = self.source.approved[291]
        caller_mapping = {291: caller_choice}
        clone = main_module.RepositoryMain(
            self.source.store, approved=caller_mapping,
            read_issue=lambda number: self.source.facts[number].copy(),
            runtime_home=self.source.runtime,
            port_for_issue=self.source.ports.__getitem__,
            opencode_bin=str(self.source.bin),
        )
        self.assertIsNot(clone.approved[291], caller_choice)
        caller_mapping[292] = ApprovedTask(
            292, "refs/heads/issue-292", self.source.base, "a" * 64)
        self.assertNotIn(292, clone.approved)
        with self.assertRaises(TypeError):
            clone.approved[292] = caller_mapping[292]
        with self.assertRaises(AttributeError):
            clone.approved = caller_mapping

        selected = clone.approved[291]
        object.__setattr__(selected, "executor", CLAUDE_CLI)
        with self.assertRaises(MainError) as changed:
            clone._selected_executor(291)
        self.assertEqual(changed.exception.code, "MAIN_SELECTION_CHANGED")

    def test_claude_dispatch_and_guarded_pr_stop_before_effects_with_precedence(self):
        main = self.make_main()
        with self.assertRaises(MainError) as unapproved:
            main.dispatch(292)
        self.assertEqual(unapproved.exception.code, "ISSUE_NOT_APPROVED")
        with self.assertRaises(MainError) as owner:
            main.dispatch(291)
        self.assertEqual(owner.exception.code, "MAIN_OWNER_REQUIRED")
        with self.assertRaises(MainError) as naked_pr:
            main.guarded_draft_pr(291, object())
        self.assertEqual(naked_pr.exception.code, "MAIN_OWNER_REQUIRED")

        main.port_for_issue = Mock(side_effect=AssertionError("port access"))
        with patch.object(main, "_spec", side_effect=AssertionError("metadata")) as spec:
            with patch.object(main_module, "start_root_session",
                              side_effect=AssertionError("session")) as session:
                with patch.object(main, "_establish_initial_collaboration",
                                  side_effect=AssertionError("collaboration")) as collab:
                    with patch.object(IssueWorktreeAllocator, "ensure",
                                      side_effect=AssertionError("worktree")) as allocate:
                        with patch.object(main_module, "opencode_request",
                                          side_effect=AssertionError("OpenCode")) as request:
                            with service.MainSupervisor(main):
                                with self.assertRaises(MainError) as denied:
                                    main.dispatch(291)
                                self.assertEqual(
                                    denied.exception.code,
                                    "CLAUDE_EXECUTOR_UNQUALIFIED")
                                with self.assertRaises(MainError) as pr_denied:
                                    main.guarded_draft_pr(291, object())
                                self.assertEqual(
                                    pr_denied.exception.code,
                                    "CLAUDE_EXECUTOR_UNQUALIFIED")
                            for mock in (spec, session, collab, allocate, request,
                                         main.port_for_issue):
                                mock.assert_not_called()
        self.assertFalse((self.source.project / ".git" /
                          "kagari-main-issue-dispatch.lock").exists())
        self.assertFalse((self.source.project / ".worktrees").exists())
        self.assertFalse(self.source.runtime.exists())
        self.assertEqual(self.source.prompts(), [])

    def test_claude_observation_is_truthful_and_never_reads_opencode_runtime(self):
        main = self.make_main()
        fetch_head = self.source.project / ".git" / "FETCH_HEAD"
        fetch_head.write_bytes(b"claude-observation-preserves-fetch-head\n")
        before = fetch_head.read_bytes()
        with patch.object(main_module, "OpenCode",
                          side_effect=AssertionError("native session read")):
            with patch.object(main_module, "task_status",
                              side_effect=AssertionError("session status")):
                with patch.object(main_module, "opencode_request",
                                  side_effect=AssertionError("OpenCode API")):
                    observed = main.observe(291)
        self.assert_claude_observation(observed)
        self.assertEqual(observed["status"], "ok")
        self.assertEqual(observed["executor"], CLAUDE_CLI)
        self.assertEqual(observed["approved_task"], {
            "repository": "acme/widgets", "issue": 291,
            "contract_id": self.source.pub.contract_id,
            "title": self.source.facts[291]["title"],
            "base_revision": self.source.base,
            "branch_ref": self.source.branch_ref,
        })
        self.assertEqual(observed["runtime"], claude_runtime_diagnostic())
        self.assertEqual(observed["runtime"]["qualification"], "UNQUALIFIED")
        self.assertEqual(observed["runtime"]["session_state"], "UNKNOWN")
        self.assertEqual(observed["kickoff"], "UNKNOWN")
        self.assertNotIn("pending", observed)
        self.assertNotIn("context", observed)
        self.assertEqual(observed["task_view"]["task"], "291")
        self.assertEqual(fetch_head.read_bytes(), before)
        self.assertFalse((self.source.project / ".worktrees").exists())
        self.assertFalse((self.source.project / ".git" /
                          "kagari-main-issue-dispatch.lock").exists())

    def test_existing_claude_runtime_names_and_symlinked_ancestors_reconcile(self):
        main = self.make_main()
        home = main.runtime_home
        home.mkdir(parents=True)
        namespace = home / "issue-291"
        for kind in ("file", "directory", "symlink", "dangling"):
            with self.subTest(kind=kind):
                if namespace.is_symlink() or namespace.is_file():
                    namespace.unlink()
                elif namespace.exists():
                    shutil.rmtree(namespace)
                if kind == "file":
                    namespace.write_bytes(b"preserve-ambiguous-runtime")
                elif kind == "directory":
                    namespace.mkdir()
                elif kind == "symlink":
                    target = self.source.fixture.temp_root / "runtime-target"
                    target.mkdir(exist_ok=True)
                    namespace.symlink_to(target, target_is_directory=True)
                else:
                    namespace.symlink_to(home / "missing-target")
                link_target = namespace.readlink() if namespace.is_symlink() else None
                with patch.object(main_module, "OpenCode",
                                  side_effect=AssertionError("native session read")):
                    with patch.object(main_module, "task_status",
                                      side_effect=AssertionError("session status")):
                        with patch.object(main_module, "opencode_request",
                                          side_effect=AssertionError("OpenCode API")):
                            with self.assertRaises(MainError) as blocked:
                                main.observe(291)
                self.assertEqual(blocked.exception.code,
                                 "EXECUTOR_RECONCILIATION_REQUIRED")
                if kind == "file":
                    self.assertEqual(namespace.read_bytes(),
                                     b"preserve-ambiguous-runtime")
                elif kind == "directory":
                    self.assertEqual(list(namespace.iterdir()), [])
                else:
                    self.assertTrue(namespace.is_symlink())
                    self.assertEqual(namespace.readlink(), link_target)
        namespace.unlink()

        alias = self.source.fixture.temp_root / "runtime-home-alias"
        alias.symlink_to(self.source.fixture.temp_root, target_is_directory=True)
        main.runtime_home = alias / "runtime"
        with self.assertRaises(MainError) as unsafe:
            main.observe(291)
        self.assertEqual(unsafe.exception.code,
                         "EXECUTOR_RECONCILIATION_REQUIRED")

    def test_claude_observation_rejects_issue_and_worktree_drift(self):
        main = self.make_main()
        original = self.source.facts[291]["title"]
        self.source.facts[291]["title"] = "changed snapshot"
        with self.assertRaises(MainError) as drift:
            main.observe(291)
        self.assertEqual(drift.exception.code, "ISSUE_SNAPSHOT_DRIFT")
        self.source.facts[291]["title"] = original

        worktree = self.source.project / ".worktrees" / "291"
        worktree.mkdir(parents=True)
        with patch.object(main_module, "worktree_registered",
                          side_effect=main_module.ControlError(
                              "WORKTREE_MISMATCH", "fixture mismatch")):
            with self.assertRaises(MainError) as wrong_worktree:
                main.observe(291)
        self.assertEqual(wrong_worktree.exception.code, "TASK_WORKTREE_MISMATCH")

    def test_host_reader_cannot_change_pinned_selection_during_observation(self):
        main = self.make_main()
        def drifting_reader(number):
            object.__setattr__(main.approved[number], "executor", OPENCODE)
            return self.source.facts[number].copy()
        main.read_issue = drifting_reader
        with patch.object(main_module, "OpenCode", side_effect=AssertionError("native API")):
            with self.assertRaises(MainError) as blocked:
                main.observe(291)
        self.assertEqual(blocked.exception.code, "MAIN_SELECTION_CHANGED")

    def test_mixed_cycle_blocks_claude_and_submits_opencode_once(self):
        second = self.source.writer.create(
            task="292", branch_ref="refs/heads/issue-292",
            base_revision=self.source.base, title="Second local Task",
            body="OpenCode remains the default executor")
        self.source.approved[291] = ApprovedTask(
            291, self.source.branch_ref, self.source.base,
            self.source.pub.contract_id, CLAUDE_CLI)
        self.source.approved[292] = ApprovedTask(
            292, "refs/heads/issue-292", self.source.base,
            second.contract_id, OPENCODE)
        self.source.facts[292] = {
            "repository": "acme/widgets", "number": 292, "state": "OPEN",
            "title": "Second local Task",
            "body": "OpenCode remains the default executor",
        }
        self.source.ports[292] = service_fixture.main_fixture.next_port()
        main = self.source.make_main()
        with service.MainSupervisor(main) as runner:
            first = runner.cycle()
            again = runner.cycle()
        self.assertEqual(first["tasks"][0]["reason"],
                         "CLAUDE_EXECUTOR_UNQUALIFIED")
        self.assertEqual(first["tasks"][1]["prompt_state"], "SUBMITTED")
        self.assertEqual(again["tasks"][0]["reason"],
                         "CLAUDE_EXECUTOR_UNQUALIFIED")
        self.assertEqual(again["tasks"][1]["prompt_state"], "ALREADY_DISPATCHED")
        self.assertFalse((self.source.runtime / "issue-291").exists())
        self.assertEqual(len(self.source.prompts()), 1)

    def test_cleanup_callbacks_and_handlers_cannot_bypass_claude_gate(self):
        main = self.make_main()
        called = Mock()
        with service.MainSupervisor(main, cleanup_for_issue={291: called}) as runner:
            result = runner.cycle()
        self.assertEqual(result["tasks"][0]["status"], "blocked")
        self.assertEqual(result["tasks"][0]["reason"],
                         "CLAUDE_EXECUTOR_UNQUALIFIED")
        called.assert_not_called()

        forbidden = self.plan_for()
        forbidden["tasks"][0]["cleanup"] = {
            "pull_request": 91, "base_ref": "refs/heads/main"}
        with self.assertRaises(MainError):
            service.build_cleanup_handlers(main, forbidden)

        wrong_binding = self.plan_for(OPENCODE)
        wrong_binding["tasks"][0]["port"] = self.source.ports[291]
        with self.assertRaises(MainError) as mismatch:
            service.build_cleanup_handlers(main, wrong_binding)
        self.assertEqual(mismatch.exception.code,
                         "MAIN_CLEANUP_CONFIGURATION_INVALID")

    def test_cleanup_handler_rechecks_pinned_choice_after_owner_check(self):
        plan = copy.deepcopy(self.plan)
        plan["tasks"][0]["cleanup"] = {
            "pull_request": 91, "base_ref": "refs/heads/main"}
        main = self.main
        backend = Mock()
        with patch.object(service, "IssueWorktreeCleanup", return_value=backend):
            with patch.object(service, "github_pull_request_reader",
                              return_value=lambda _request: None):
                with patch.object(service, "stopped_session_from_runtime",
                                  return_value=lambda _request: True):
                    handler = service.build_cleanup_handlers(main, plan)[291]
        with service.MainSupervisor(main):
            choice = main.approved[291]
            object.__setattr__(choice, "executor", CLAUDE_CLI)
            try:
                with self.assertRaises(MainError) as changed:
                    handler()
                self.assertEqual(changed.exception.code, "MAIN_SELECTION_CHANGED")
                backend.cleanup.assert_not_called()
            finally:
                object.__setattr__(choice, "executor", OPENCODE)

    def test_packaged_claude_observation_if_supplied(self):
        package = os.getenv("KAGARI_VNEXT_MAIN_PACKAGE")
        if not package:
            self.skipTest("KAGARI_VNEXT_MAIN_PACKAGE not supplied")
        fake_gh = self.source.fixture.temp_root / "fake-gh-claude-observe"
        # Only an Issue-shaped local fixture is read. No host lease, accounts,
        # CLI preflight, model, GitHub or runtime service is accessed.
        body = json.dumps(self.source.facts[291], sort_keys=True)
        fake_gh.write_text(
            "#!" + sys.executable + "\nimport sys\n"
            "assert sys.argv[1:3] == ['issue', 'view']\n"
            "print(" + repr(body) + ")\n", encoding="utf-8")
        fake_gh.chmod(0o700)
        self.plan = self.plan_for()
        self.plan["github_bin"] = str(fake_gh)
        self.save_plan()
        fetch_head = self.source.project / ".git" / "FETCH_HEAD"
        fetch_head.write_bytes(b"unchanged Claude observation fixture\n")
        startup = self.source.fixture.temp_root / "package-observe-startup"
        startup.mkdir()
        armed = startup / "armed"
        accessed = startup / "passwd-accessed"
        # The parent's pwd patch cannot cross exec. Arm a child-only guard so
        # a regression trying to acquire the host lease fails before resolving
        # the real passwd home. Assert startup ran; silently ignored injection
        # must not turn this into a test against the user's host-state directory.
        (startup / "sitecustomize.py").write_text(
            "import pwd\nfrom pathlib import Path\n"
            "def deny_passwd_home(_uid):\n"
            "    Path(" + repr(str(accessed)) + ").touch()\n"
            "    raise RuntimeError('observe must not acquire a host lease')\n"
            "pwd.getpwuid = deny_passwd_home\n"
            "Path(" + repr(str(armed)) + ").touch()\n", encoding="utf-8")
        process = subprocess.run(
            [str(Path(package) / "bin/kagari-vnext-main"),
             "--config", str(self.config), "observe"],
            capture_output=True, text=True, timeout=30, check=False,
            env={**os.environ, "PYTHONPATH": str(startup), "PYTHONNOUSERSITE": "1"})
        self.assertTrue(armed.exists(), "packaged Python must arm test-only host guard")
        self.assertFalse(accessed.exists(), "observe attempted a host lease lookup")
        self.assertEqual(process.returncode, 0, (process.stdout, process.stderr))
        observed = json.loads(process.stdout)["tasks"][0]["observation"]
        self.assert_claude_observation(observed)
        self.assertEqual(observed["runtime"], claude_runtime_diagnostic())
        self.assertEqual(observed["kickoff"], "UNKNOWN")
        self.assertNotIn("pending", observed)
        self.assertFalse(self.source.runtime.exists())
        self.assertFalse((self.source.project / ".worktrees").exists())
        self.assertEqual(fetch_head.read_bytes(), b"unchanged Claude observation fixture\n")


if __name__ == "__main__":
    unittest.main()
