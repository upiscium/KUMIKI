"""Phase A host route: real Git/TaskGit/#191/#217/#194, no live GitHub/model.

Only external GitHub facts/effects and OpenCode are fixture implementations.
The host callbacks below are test grants, never production authorization.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import copy
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests import test_kagari_vnext_main as fixtures
from tests import test_metadata_ref as git_fixture
from tests.test_task_collaboration_v4 import FakeGitHub
from tools import kagari_vnext_main as main_module
from tools import kagari_vnext_main_service as service
from tools.kagari_vnext_issue_bootstrap import ApprovedIssueSnapshot, bootstrap_approved_issue
from tools.kagari_vnext_main import InitialCollaboration, MainError
from collaboration_git import TaskGit
from issue_worktree import IssueWorktreeAllocator
import operation_prerequisites as prerequisites
from task_collaboration import MetadataBinding, OPERATIONS, TaskCollaboration
from task_view import TaskViewSnapshots


class MainHostTests(unittest.TestCase):
    def setUp(self):
        self.source = fixtures.MainOrchestratorTest("runTest")
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        # No replacement of Main preflight, TaskGit, facade or metadata reader.
        self.source.initial_fixture_patch.stop()
        self.lock_patch = patch.object(
            service, "_host_state_home", return_value=self.source.fixture.temp_root)
        self.lock_patch.start()
        self.addCleanup(self.lock_patch.stop)
        self.github = FakeGitHub()
        self.publications = {291: self.source.pub}
        self.pushes = []
        self.intents = []
        self.authorizations = []
        self.snapshots = {}
        self.deny_operation = None
        self.deny_content = False
        self.deny_intent = False
        self.deny_snapshot = False
        self.provider_calls = []
        self.report = "Initial approved Task checkpoint."
        self.plan = {
            "schema_version": 1, "project_root": str(self.source.project),
            "repository": "acme/widgets", "runtime_home": str(self.source.runtime),
            "opencode_bin": str(self.source.bin),
            "tasks": [self.selection(291)],
        }
        self.config = self.source.fixture.temp_root / "main-selection.json"

    def selection(self, issue):
        choice = self.source.approved[issue]
        return {"issue": issue, "branch_ref": choice.branch_ref,
                "base_revision": choice.base_revision, "contract_id": choice.contract_id,
                "port": self.source.ports[issue]}

    def host(self):
        return service.MainHostCapabilities(
            read_issue=lambda n: self.source.facts[n].copy(),
            initial_collaboration_for_issue=self.install)

    def install(self, issue: int, worktree: Path) -> InitialCollaboration:
        self.provider_calls.append((issue, worktree))
        choice = self.source.approved[issue]
        self.assertEqual(worktree, self.source.project / ".worktrees" / str(issue))
        store = self.source.fixture._writer(worktree)

        def publish(intent):
            self.assertEqual((intent.repository, intent.task, intent.branch_ref),
                             (store.repository, str(issue), choice.branch_ref))
            self.pushes.append(intent)
            git_fixture.git("push", "--no-follow-tags", "origin",
                            f"{intent.subject}:{intent.branch_ref}", cwd=worktree)

        git = TaskGit(store, task=str(issue), branch_ref=choice.branch_ref,
                      base_revision=choice.base_revision,
                      default_branch_ref="refs/heads/main", publish=publish)
        engines = {}
        for operation in OPERATIONS:
            policy = prerequisites.OperationPolicy(
                operation, "phase_a_fixture", "execution",
                (prerequisites.CheckSpec("fixture_facts", "fixture_host"),))
            engines[operation] = prerequisites.OperationPrerequisites(
                policy, checks={"fixture_facts": lambda context: prerequisites.CheckResult(
                    context.binding, "SATISFIED", "fixture_facts_current", {}, {}, "none")})

        def authorize(request):
            self.authorizations.append(request)
            return (request.repository == store.repository and request.task == str(issue)
                    and request.branch_ref == choice.branch_ref
                    and request.operation != self.deny_operation)

        def record(intent):
            self.intents.append(intent)
            return not self.deny_intent

        facade = TaskCollaboration(
            store, git, self.github, prerequisites_by_operation=engines,
            authorize=authorize, record_intent=record,
            content_policy=lambda request: not self.deny_content)

        def metadata(repository, task, branch, number, subject):
            publication = self.publications[issue]
            key = (repository, task, branch, number, subject,
                   publication.record_id, publication.contract_id)
            if key not in self.snapshots:
                captured = TaskViewSnapshots(
                    store, authorize=lambda request: not self.deny_snapshot).capture(
                        task=task, branch_ref=branch, base_revision=choice.base_revision,
                        boundary="explicit-handoff", default_branch_ref="refs/heads/main",
                        observe_remote_head=True, pr_number=number,
                        github_reader=facade.github_observation)
                self.snapshots[key] = MetadataBinding(
                    captured.metadata_commit, publication.record_id,
                    publication.contract_id, captured.snapshot_id)
            return self.snapshots[key]

        return InitialCollaboration(facade, metadata, self.report)

    def run_main(self, *, host=True, operation="dispatch", follow=False):
        self.config.write_text(json.dumps(self.plan), encoding="utf-8")
        args = ["--config", str(self.config)]
        if follow:
            args += ["--follow", "--interval", "2"]
        args += [operation]
        output = StringIO()
        with redirect_stdout(output):
            code = service.main(args, host=self.host() if host else None)
        self.assertEqual(code, 0, output.getvalue())
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def allocate(self):
        return IssueWorktreeAllocator(self.source.store).ensure(
            task="291", branch_ref=self.source.branch_ref,
            base_revision=self.source.base, expected_contract_id=self.source.pub.contract_id)

    def assert_no_session(self):
        self.assertEqual(self.source.prompts(), [])
        self.assertFalse((self.source.runtime / "issue-291" / "context.json").exists())

    def test_ordinary_host_route_pr_first_then_restart_does_not_duplicate(self):
        request = main_module.opencode_request

        def require_pr_first(url, *, payload=None):
            if payload is not None:
                self.assertIn("/prompt_async?", url)
                self.assertEqual(len(self.github.pulls), 1)
                self.assertEqual(len(self.github.comments[("pr", 91)]), 1)
                self.assertEqual(len(self.snapshots), 1)
            return request(url, payload=payload)

        with patch.object(main_module, "opencode_request", side_effect=require_pr_first):
            first = self.run_main()[0]["tasks"][0]
        self.assertEqual(first["prompt_state"], "SUBMITTED", first)
        self.assertEqual(first["session_state"], "CREATED")
        self.assertEqual(len(self.github.pulls), 1)
        self.assertEqual(len(self.github.comments[("pr", 91)]), 1)
        self.assertEqual(len(self.snapshots), 1)
        self.assertEqual(len(self.pushes), 1)
        writes = len(self.github.write_calls)
        again = self.run_main()[0]["tasks"][0]
        self.assertEqual(again["prompt_state"], "ALREADY_DISPATCHED", again)
        self.assertEqual(again["session_state"], "RESUMED")
        self.assertEqual(len(self.github.write_calls), writes)
        self.assertEqual(len(self.source.prompts()), 1)
        self.assertEqual(self.source.prompts()[0]["cwd"],
                         str(self.source.project / ".worktrees" / "291"))
        self.assertNotIn("model", self.source.prompts()[0]["body"])
        self.assertEqual(again["runtime"]["pending_asks"], 1)  # native permission
        self.assertTrue(self.authorizations)
        self.assertTrue(self.intents)

    def test_other_session_materialized_pristine_task_is_adopted_before_first_session(self):
        allocated = self.allocate()
        self.assertEqual(allocated.status, "CREATED")
        # #191 read-only adoption must not replay a create grant.
        resumed = bootstrap_approved_issue(
            self.source.store, ApprovedIssueSnapshot(
                "acme/widgets", 291, self.source.branch_ref, self.source.base,
                self.source.facts[291]["title"], self.source.facts[291]["body"]),
            read_issue=lambda n: self.source.facts[n].copy(),
            authorize_create=lambda request: self.fail("must not recreate existing Task"))
        self.assertEqual(resumed.state, "RESUMED")
        self.assertEqual(resumed.record_id, self.source.pub.record_id)
        self.assertFalse(self.source.runtime.exists())
        result = self.run_main()[0]["tasks"][0]
        self.assertEqual(result["worktree_state"], "RESUMED", result)
        self.assertEqual(result["session_state"], "CREATED")
        self.assertEqual(result["prompt_state"], "SUBMITTED")
        self.assertEqual(len(self.source.prompts()), 1)

    def test_existing_pr_checkpoint_without_main_runtime_is_adopted_not_republished(self):
        allocated = self.allocate()
        installed = self.install(291, Path(allocated.worktree))
        receipt = installed.facade.establish(installed.report, installed.metadata_provider)
        self.assertFalse(self.source.runtime.exists())
        writes = len(self.github.write_calls)
        result = self.run_main()[0]["tasks"][0]
        self.assertEqual(result["prompt_state"], "SUBMITTED", result)
        self.assertEqual(len(self.github.write_calls), writes)
        self.assertEqual(len(self.snapshots), 1)
        self.assertEqual(self.github.pulls[0].head_oid, receipt.subject)

    def test_restart_after_main_checkpoint_before_session_reobserves_without_duplicate(self):
        with patch.object(main_module, "start_root_session",
                          side_effect=MainError("FIXTURE_STOP_BEFORE_SESSION")):
            stopped = self.run_main()[0]["tasks"][0]
        self.assertEqual(stopped.get("reason"), "FIXTURE_STOP_BEFORE_SESSION", stopped)
        self.assertTrue((self.source.runtime / "issue-291" /
                         "initial-collaboration.json").is_file())
        self.assert_no_session()
        writes = len(self.github.write_calls)
        result = self.run_main()[0]["tasks"][0]
        self.assertEqual(result.get("prompt_state"), "SUBMITTED", result)
        self.assertEqual(len(self.provider_calls), 2)
        self.assertEqual(len(self.github.write_calls), writes)
        self.assertEqual(len(self.snapshots), 1)
        self.assertEqual(len(self.source.prompts()), 1)

    def test_observe_does_not_call_host_collaboration_or_allocate(self):
        result = self.run_main(operation="observe")[0]
        self.assertEqual(result["tasks"][0]["observation"]["runtime"], "NOT_STARTED")
        self.assertEqual(self.provider_calls, [])
        self.assertFalse((self.source.project / ".worktrees").exists())
        self.assert_no_session()

    def test_follow_uses_same_host_route_and_never_reprompts(self):
        class TwoCycles(threading.Event):
            def __init__(self):
                super().__init__()
                self.cycles = 0

            def wait(self, timeout=None):
                self.cycles += 1
                if self.cycles == 2:
                    self.set()
                return self.is_set()

        stopped = TwoCycles()
        # Replace just Main's foreground timer, not the process/session timers.
        with patch.object(service, "threading") as clock:
            clock.Event.return_value = stopped
            cycles = self.run_main(follow=True)
        self.assertEqual([r["tasks"][0]["prompt_state"] for r in cycles],
                         ["SUBMITTED", "ALREADY_DISPATCHED"])
        self.assertEqual(len(self.source.prompts()), 1)

    def test_absent_host_refuses_before_allocation_even_with_pr_selection(self):
        self.plan["tasks"][0]["pr_number"] = 91
        with patch.object(service, "github_issue_reader",
                          return_value=lambda n: self.source.facts[n].copy()):
            result = self.run_main(host=False)[0]["tasks"][0]
        self.assertEqual(result["reason"], "MAIN_INITIAL_COLLABORATION_REQUIRED")
        self.assertFalse((self.source.project / ".worktrees").exists())
        self.assertEqual(self.github.write_calls, [])
        self.assert_no_session()

    def test_denied_existing_facade_authority_blocks_before_model(self):
        self.deny_operation = "collaboration.draft_pr"
        result = self.run_main()[0]["tasks"][0]
        self.assertEqual(result["status"], "blocked", result)
        self.assertEqual(result["reason"], "MAIN_INITIAL_COLLABORATION_REFUSED")
        self.assertIn(self.deny_operation, [r.operation for r in self.authorizations])
        self.assertEqual(self.github.pulls, [])
        self.assert_no_session()

    def test_denied_content_or_intent_or_snapshot_cannot_start_model(self):
        for flag in ("deny_content", "deny_intent", "deny_snapshot"):
            with self.subTest(flag=flag):
                setattr(self, flag, True)
                result = self.run_main()[0]["tasks"][0]
                self.assertEqual(result["status"], "blocked", result)
                self.assert_no_session()
                setattr(self, flag, False)

    def test_dirty_existing_task_is_not_pristine_adoption(self):
        allocated = self.allocate()
        (Path(allocated.worktree) / "untracked.txt").write_text("preserve me")
        result = self.run_main()[0]["tasks"][0]
        self.assertEqual(result["status"], "blocked", result)
        self.assertEqual(self.github.write_calls, [])
        self.assert_no_session()

    def test_unknown_or_partial_session_without_main_kickoff_requires_reconciliation(self):
        runtime = self.source.runtime / "issue-291"
        runtime.mkdir(parents=True)
        for filename in ("context.json", "launch.pending.json", "process.json"):
            with self.subTest(filename=filename):
                path = runtime / filename
                path.write_text("{}")
                result = self.run_main()[0]["tasks"][0]
                self.assertEqual(result.get("reason"), "MAIN_SESSION_RECONCILIATION_REQUIRED", result)
                self.assertEqual(self.github.write_calls, [])
                self.assertEqual(self.source.prompts(), [])
                path.rename(path.with_suffix(".preserved"))

    def test_live_session_created_outside_main_is_not_a_pristine_task(self):
        from tools.kagari_vnext_session import start
        allocation = self.allocate()
        controller = service.build_main(self.plan, host=self.host())
        args = controller._session_args(self.source.approved[291], Path(allocation.worktree))
        started = start(args)
        context = Path(started["context"])
        before = context.read_bytes()
        result = self.run_main()[0]["tasks"][0]
        self.assertEqual(result.get("reason"), "MAIN_SESSION_RECONCILIATION_REQUIRED", result)
        self.assertEqual(context.read_bytes(), before)
        self.assertEqual(self.source.prompts(), [])
        self.assertEqual(self.github.write_calls, [])

    def test_outside_session_after_checkpoint_cannot_receive_main_first_prompt(self):
        from tools.kagari_vnext_session import start
        establish = TaskCollaboration.establish
        outside = {}

        def establish_then_outside_start(facade, report, provider):
            receipt = establish(facade, report, provider)
            # Main already passed its early runtime check and #194 completed.
            # This is a genuine independent #163 invocation, not a fake context.
            controller = service.build_main(self.plan, host=self.host())
            args = controller._session_args(self.source.approved[291], facade._store.root)
            created = start(args)
            self.assertEqual(created["outcome"], "CREATED")
            runtime = Path(created["context"]).parent
            outside["files"] = {name: (runtime / name).read_bytes()
                                for name in ("context.json", "process.json")}
            outside["head"] = git_fixture.git("rev-parse", "HEAD", cwd=facade._store.root)
            outside["receipt"] = receipt
            outside["writes"] = len(self.github.write_calls)
            return receipt

        with patch.object(TaskCollaboration, "establish", establish_then_outside_start):
            result = self.run_main()[0]["tasks"][0]
        self.assertEqual(self.source.prompts(), [], result)
        self.assertEqual(result.get("reason"), "SESSION_RECONCILIATION_REQUIRED", result)
        runtime = self.source.runtime / "issue-291"
        self.assertEqual({name: (runtime / name).read_bytes() for name in outside["files"]},
                         outside["files"])
        self.assertFalse((runtime / "prompt.pending.json").exists())
        self.assertFalse((runtime / "prompt.sent.json").exists())
        self.assertTrue((runtime / "initial-collaboration.json").is_file())
        self.assertEqual(git_fixture.git("rev-parse", "HEAD",
                                        cwd=self.source.project / ".worktrees" / "291"),
                         outside["head"])
        self.assertEqual(len(self.github.write_calls), outside["writes"])
        self.assertEqual(len(self.github.pulls), 1)
        self.assertEqual(len(self.github.comments[("pr", 91)]), 1)
        self.assertEqual(len(self.snapshots), 1)

    def test_process_only_state_after_checkpoint_refuses_without_new_session(self):
        establish = TaskCollaboration.establish
        preserved = b'{"stage":"outside partial launch after checkpoint"}'

        def establish_then_partial_launch(facade, report, provider):
            receipt = establish(facade, report, provider)
            runtime = self.source.runtime / "issue-291"
            runtime.mkdir(parents=True, exist_ok=True)
            (runtime / "process.json").write_bytes(preserved)
            return receipt

        with patch.object(TaskCollaboration, "establish", establish_then_partial_launch):
            result = self.run_main()[0]["tasks"][0]
        self.assertEqual(result.get("reason"), "SESSION_RECONCILIATION_REQUIRED", result)
        self.assert_no_session()
        runtime = self.source.runtime / "issue-291"
        self.assertEqual((runtime / "process.json").read_bytes(), preserved)
        self.assertFalse((runtime / "launch.pending.json").exists())
        self.assertFalse((runtime / "prompt.pending.json").exists())
        self.assertEqual(len(self.github.pulls), 1)
        self.assertEqual(len(self.github.comments[("pr", 91)]), 1)
        self.assertTrue((runtime / "initial-collaboration.json").is_file())

    def test_altered_kickoff_identity_cannot_enable_session_resume(self):
        first = self.run_main()[0]["tasks"][0]
        self.assertEqual(first.get("prompt_state"), "SUBMITTED", first)
        accepted = self.source.runtime / "issue-291" / "prompt.sent.json"
        original = json.loads(accepted.read_text())
        for field, value in (("issue", 999), ("contract_id", "f" * 64)):
            with self.subTest(field=field):
                accepted.write_text(json.dumps({**original, field: value}))
                before = accepted.read_bytes()
                with patch.object(main_module, "start_root_session",
                                  side_effect=AssertionError("unverified kickoff cannot resume")):
                    result = self.run_main()[0]["tasks"][0]
                self.assertEqual(result.get("reason"), "PROMPT_RECONCILIATION_REQUIRED", result)
                self.assertEqual(accepted.read_bytes(), before)
                self.assertEqual(len(self.source.prompts()), 1)

    def test_receipted_missing_context_never_replaces_session_or_process_evidence(self):
        from tools import kagari_vnext_session as launcher
        # A replacement process must produce a different Session ID, unlike
        # the older port-only fixture. Count actual serve Popen calls as well.
        self.source.bin.write_text(fixtures.FAKE_OPENCODE.replace(
            'sid="ses_MAIN" + str(port) + "DemoSession012345"',
            'sid="ses_MAIN" + str(port) + "_" + str(os.getpid()) + "DemoSession012345"'))
        popen = launcher.subprocess.Popen
        launches = []

        def count_launches(argv, *args, **kwargs):
            process = popen(argv, *args, **kwargs)
            if argv[:2] == [str(self.source.bin), "serve"]:
                launches.append(process)
            return process

        with patch.object(launcher.subprocess, "Popen", side_effect=count_launches):
            first = self.run_main()[0]["tasks"][0]
            self.assertEqual(first.get("prompt_state"), "SUBMITTED", first)
            self.assertEqual(len(launches), 1)
            runtime = self.source.runtime / "issue-291"
            receipt = json.loads((runtime / "prompt.sent.json").read_text())
            self.assertIn("_" + str(launches[0].pid) + "DemoSession", receipt["session_id"])
            launches[0].terminate()
            launches[0].wait(timeout=5)
            self.assertTrue(launcher._port_available(self.source.ports[291]))
            context = runtime / "context.json"
            context.rename(runtime / "context.preserved.json")
            self.assertFalse((runtime / "launch.pending.json").exists())
            worktree = self.source.project / ".worktrees" / "291"

            def git_state():
                return (git_fixture.git("rev-parse", "HEAD", cwd=worktree),
                        git_fixture.git("status", "--porcelain=v1", cwd=worktree),
                        (worktree / ".git").read_bytes())

            git_before = git_state()
            github_before = copy.deepcopy((self.github.pulls, self.github.comments,
                                           self.github.write_calls))
            for name in ("prompt.sent.json", "prompt.pending.json"):
                with self.subTest(receipt=name):
                    if name == "prompt.pending.json":
                        (runtime / "prompt.sent.json").rename(runtime / name)
                    before = {path.name: (path.stat().st_ino, path.read_bytes())
                              for path in runtime.iterdir() if path.is_file()}
                    result = self.run_main()[0]["tasks"][0]
                    self.assertEqual(len(launches), 1, result)
                    self.assertEqual(result.get("reason"), "SESSION_RECONCILIATION_REQUIRED", result)
                    self.assertEqual({path.name: (path.stat().st_ino, path.read_bytes())
                                      for path in runtime.iterdir() if path.is_file()}, before)
                    self.assertEqual(git_state(), git_before)
                    self.assertEqual((self.github.pulls, self.github.comments,
                                      self.github.write_calls), github_before)
                    self.assertEqual(len(self.source.prompts()), 1)
                    self.assertFalse(context.exists())
                    self.assertFalse((runtime / "launch.pending.json").exists())

    def test_mismatched_contract_branch_or_issue_snapshot_fails_before_provider(self):
        original = self.plan["tasks"][0].copy()
        for field, value in (("contract_id", "a" * 64),
                             ("branch_ref", "refs/heads/issue-291-wrong")):
            with self.subTest(field=field):
                self.plan["tasks"][0] = {**original, field: value}
                result = self.run_main()[0]["tasks"][0]
                self.assertEqual(result["status"], "blocked", result)
        self.plan["tasks"][0] = original
        self.source.facts[291]["body"] = "not the approved requirements"
        result = self.run_main()[0]["tasks"][0]
        self.assertEqual(result["reason"], "ISSUE_SNAPSHOT_DRIFT")
        self.assertEqual(self.provider_calls, [])
        self.assert_no_session()

    def test_stale_existing_initial_checkpoint_cannot_launch(self):
        allocated = self.allocate()
        installed = self.install(291, Path(allocated.worktree))
        installed.facade.establish(installed.report, installed.metadata_provider)
        self.report = "A different unapproved initial checkpoint report."
        writes = len(self.github.write_calls)
        result = self.run_main()[0]["tasks"][0]
        self.assertEqual(result["status"], "blocked", result)
        self.assertEqual(len(self.github.write_calls), writes)
        self.assert_no_session()

    def test_missing_checkpoint_after_unconfirmed_post_never_starts_session(self):
        self.github.post_comment = lambda intent: None
        result = self.run_main()[0]["tasks"][0]
        self.assertEqual(result["status"], "blocked", result)
        self.assertEqual(len(self.github.pulls), 1)
        self.assertEqual(self.github.comments, {})
        self.assert_no_session()

    def test_different_approved_issue_gets_independent_worktree_and_session(self):
        issue = 292
        self.source.facts[issue] = {**self.source.facts[291], "number": issue,
                                    "title": "Independent approved Issue"}
        self.publications[issue] = bootstrap_approved_issue(
            self.source.store, ApprovedIssueSnapshot(
                "acme/widgets", issue, "refs/heads/issue-292", self.source.base,
                self.source.facts[issue]["title"], self.source.facts[issue]["body"]),
            read_issue=lambda n: self.source.facts[n].copy(),
            authorize_create=lambda req: req.task == "292" and req.operation == "create")
        self.source.approved[issue] = replace(
            self.source.approved[291], issue=issue, branch_ref="refs/heads/issue-292",
            contract_id=self.publications[issue].contract_id)
        self.source.ports[issue] = fixtures.next_port()
        self.plan["tasks"].append(self.selection(issue))
        # The faithful transport assigns a unique PR number for each creation.
        create = self.github.create_draft

        def create_next(intent):
            result = create(intent)
            self.github.next_pr += 1
            return result

        self.github.create_draft = create_next
        results = self.run_main()[0]["tasks"]
        self.assertEqual([r.get("prompt_state") for r in results], ["SUBMITTED"] * 2, results)
        self.assertNotEqual(results[0]["context"], results[1]["context"])
        self.assertEqual({p["cwd"] for p in self.source.prompts()},
                         {str(self.source.project / ".worktrees" / str(n)) for n in (291, 292)})
        again = self.run_main()[0]["tasks"]
        self.assertEqual([r["prompt_state"] for r in again], ["ALREADY_DISPATCHED"] * 2)
        self.assertEqual(len(self.source.prompts()), 2)

    def test_bool_or_plan_provider_path_is_not_a_host_capability(self):
        with self.assertRaises(MainError) as error:
            service.build_main(self.plan, host=True)
        self.assertEqual(error.exception.code, "MAIN_HOST_CAPABILITIES_INVALID")
        for reader, provider in ((True, self.install), (lambda n: {}, True)):
            with self.subTest(reader=reader, provider=provider):
                with self.assertRaises(MainError) as error:
                    service.MainHostCapabilities(reader, provider)
                self.assertEqual(error.exception.code, "MAIN_HOST_CAPABILITIES_INVALID")
        self.plan["host_provider"] = "/arbitrary/plugin.py"
        self.config.write_text(json.dumps(self.plan))
        with self.assertRaises(MainError) as error:
            service.load_selection(self.config)
        self.assertEqual(error.exception.code, "MAIN_PLAN_INVALID")
        self.assert_no_session()


if __name__ == "__main__":
    unittest.main()
