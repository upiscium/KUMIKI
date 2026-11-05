"""#291 R5A0 — v2 current-Task Main observation is deliberately nonexecuting.

Source-only: disposable #337 SHA-1 Git+Attach fixtures and existing #291
RepositoryMain/MainSupervisor, no real GitHub/Task/Session/model/PR effects.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import copy
import unittest
from unittest.mock import patch

from tests import test_kagari_metadata_v2_task_admission as v2_fixture
from tools import kagari_vnext_main as main_module
from tools import kagari_vnext_main_service as service
import kagari_vnext_attachment_gate as gate
from tools.kagari_vnext_main import MainError
from tools.kagari_vnext_main_v2_observation import (
    V2ReadOnlyMainHost, V2MainObservationRefused,
)
from tests.test_metadata_v2 import REPOSITORY
from task_record import encode_record


class MainV2ReadOnlyTest(unittest.TestCase):
    def setUp(self):
        source = v2_fixture.V2TaskAdmissionTest("runTest")
        # Nested setup can fail before completion; still own its cleanup.
        self.addCleanup(source.doCleanups)
        source.setUp()
        self.source = source
        self.issue = source.registered.issue
        self.facts = {
            self.issue: {
                "repository": REPOSITORY, "number": self.issue,
                "state": "OPEN", "title": source.approved.title,
                "body": source.approved.body,
            }
        }
        self.calls = []
        self.legacy_clear = True
        self.observation_granted = True
        self.plan = {
            "schema_version": 1, "project_root": str(source.project),
            "repository": REPOSITORY,
            "runtime_home": str(source.home / "r5a0-no-runtime"),
            "tasks": [self.selection(source.registered)],
        }

    @staticmethod
    def selection(registered):
        return {
            "issue": registered.issue, "branch_ref": registered.branch_ref,
            "base_revision": registered.base_revision,
            "contract_id": registered.contract_id, "port": 32610 + registered.issue,
        }

    def read_issue(self, number):
        self.calls.append(("issue", number))
        return copy.deepcopy(self.facts[number])

    def legacy(self, req):
        self.calls.append(("legacy", req.operation, req.selection.issue))
        return self.legacy_clear

    def authorize(self, req):
        self.calls.append(("authorize", req.operation, req.selection.issue))
        return self.observation_granted

    def host(self, lease, *, identity=None, reader=None, registered=None,
             approved=None, selection=None, legacy=None, authorize=None):
        source = self.source
        return V2ReadOnlyMainHost(
            selection or source.selection(),
            reader or source.authority(
                registered=registered, approved=approved),
            lease, legacy or self.legacy, authorize or self.authorize,
        )

    def main(self, host):
        return service.build_main(
            copy.deepcopy(self.plan),
            host=service.MainV2ReadOnlyCapabilities(
                read_issue=self.read_issue, observers={self.issue: host}))

    def assert_no_product_effects(self, before):
        source = self.source
        self.assertEqual(before["head"], source.attached.git("rev-parse", "HEAD"))
        self.assertEqual(before["status"], source.attached.git(
            "status", "--porcelain=v1", "-uall"))
        self.assertEqual(before["index"], (source.project / ".git/index").read_bytes())
        self.assertEqual(before["receipt"], source.attached.receipt().read_bytes())
        self.assertFalse((source.project / ".worktrees" / str(self.issue)).exists())
        self.assertFalse((source.home / "r5a0-no-runtime").exists())

    def snapshot(self):
        source = self.source
        return {
            "head": source.attached.git("rev-parse", "HEAD"),
            "status": source.attached.git("status", "--porcelain=v1", "-uall"),
            "index": (source.project / ".git/index").read_bytes(),
            "receipt": source.attached.receipt().read_bytes(),
        }

    def test_v2_observation_verifies_current_and_stays_blocked_live(self):
        before = self.snapshot()
        with gate.hold_verified(self.source.project, self.source.home,
                                self.source.package) as lease:
            main = self.main(self.host(lease))
            with patch.object(main_module, "start_root_session",
                              side_effect=AssertionError("must not launch")), patch.object(
                    main_module, "opencode_request",
                    side_effect=AssertionError("must not prompt")), patch.object(
                    main_module, "isolated_metadata_observation",
                    side_effect=AssertionError("must not read legacy v1")):
                first = main.observe(self.issue)
                again = main.observe_v2(self.issue)
        self.assertEqual(first, again)
        self.assertEqual(first["status"], "blocked")
        self.assertEqual(first["reason"], "V2_MAIN_BLOCKED_LIVE")
        self.assertEqual(first["metadata_version"], 2)
        self.assertEqual(first["current_record"]["record_id"],
                         self.source.registered.record_id)
        self.assertEqual(first["current_record"]["contract_id"],
                         self.source.registered.contract_id)
        self.assertEqual(first["runtime"], "NOT_STARTED")
        self.assertEqual(first["kickoff"], "NOT_SENT")
        self.assertFalse(first["task_execution_authorized"])
        self.assertFalse(first["worktree_mutation_authorized"])
        self.assertFalse(first["github_write_authorized"])
        self.assertGreaterEqual(len(self.calls), 4)
        self.assert_no_product_effects(before)

    def test_main_supervisor_observe_only_reports_blocked_not_ok(self):
        before = self.snapshot()
        with gate.hold_verified(self.source.project, self.source.home,
                                self.source.package) as lease:
            main = self.main(self.host(lease))
            with service.MainSupervisor(main, observe_only=True) as supervisor:
                result = supervisor.cycle(dispatch=False)
                self.assertEqual("observe", result["mode"])
                self.assertEqual("blocked", result["tasks"][0]["status"])
                self.assertEqual("V2_MAIN_BLOCKED_LIVE", result["tasks"][0]["reason"])
                self.assertEqual("NOT_SENT", result["tasks"][0]["observation"]["kickoff"])
        self.assert_no_product_effects(before)

    def test_v2_dispatch_and_pr_reject_before_v1_effects(self):
        before = self.snapshot()
        with gate.hold_verified(self.source.project, self.source.home,
                                self.source.package) as lease:
            main = self.main(self.host(lease))
            with self.assertRaisesRegex(MainError, "V2_MAIN_EXECUTION_NOT_QUALIFIED"):
                main.dispatch(self.issue)
            # No Main owner exists; the PR path already requires that owner.
            with self.assertRaises(MainError):
                main.guarded_draft_pr(self.issue, object())
            cleanup_plan = copy.deepcopy(self.plan)
            cleanup_plan["tasks"][0]["cleanup"] = {"unexpected": "not-used"}
            with self.assertRaisesRegex(MainError, "V2_MAIN_CLEANUP_NOT_QUALIFIED"):
                service.build_cleanup_handlers(main, cleanup_plan)
        self.assert_no_product_effects(before)

    def test_v2_missing_or_denied_host_fails_without_implicit_activation(self):
        before = self.snapshot()
        no_host = service.build_main(copy.deepcopy(self.plan))
        with self.assertRaisesRegex(MainError, "V2_MAIN_HOST_REQUIRED"):
            no_host.observe_v2(self.issue)
        with self.assertRaises(MainError) as old:
            no_host.observe(self.issue)
        self.assertNotEqual(old.exception.code, "V2_MAIN_BLOCKED_LIVE")
        for blocked in ("legacy", "authorize"):
            with self.subTest(blocked=blocked):
                self.legacy_clear = blocked != "legacy"
                self.observation_granted = blocked != "authorize"
                with gate.hold_verified(self.source.project, self.source.home,
                                        self.source.package) as lease:
                    main = self.main(self.host(lease))
                    with self.assertRaises(MainError) as denied:
                        main.observe(self.issue)
                self.assertEqual(denied.exception.code, (
                    "V2_MAIN_EXISTING_V1_ASSIGNMENT" if blocked == "legacy"
                    else "V2_MAIN_OBSERVATION_GRANT_REQUIRED"))
        self.assert_no_product_effects(before)

    def test_nonliteral_host_grants_and_unavailable_callback_refuse(self):
        before = self.snapshot()
        for callback, code in (
            (lambda req: "yes", "V2_MAIN_OBSERVATION_GRANT_REQUIRED"),
            (lambda req: (_ for _ in ()).throw(RuntimeError("private")),
             "V2_MAIN_HOST_OBSERVATION_UNAVAILABLE"),
        ):
            with self.subTest(code=code):
                with gate.hold_verified(self.source.project, self.source.home,
                                        self.source.package) as lease:
                    h = self.host(lease, authorize=callback)
                    with self.assertRaises(MainError) as error:
                        self.main(h).observe(self.issue)
                self.assertEqual(error.exception.code, code)
        self.assert_no_product_effects(before)

    def test_current_record_retirement_refuses_history(self):
        source = self.source
        before = self.snapshot()
        new_id, new_record = encode_record(
            REPOSITORY, str(self.issue), source.base,
            {"schema_version": 1, "branch_ref": source.approved.branch_ref,
             "contract_id": source.registered.contract_id,
             "disposition": {"kind": "cancelled"}})
        source.fixture.store().publish_issue(
            str(self.issue), [new_record],
            record_update=(source.registered.record_id, new_id))
        with gate.hold_verified(source.project, source.home, source.package) as lease:
            with self.assertRaisesRegex(MainError, "V2_MAIN_CURRENT_TASK_UNAVAILABLE"):
                self.main(self.host(lease)).observe(self.issue)
        self.assert_no_product_effects(before)

    def test_source_or_issue_mutation_during_grant_cannot_be_promoted(self):
        source = self.source
        before = self.snapshot()
        def drift_issue(req):
            self.facts[self.issue]["body"] = "changed live Issue"
            return True
        with gate.hold_verified(source.project, source.home, source.package) as lease:
            with self.assertRaisesRegex(MainError, "V2_MAIN_ISSUE_SNAPSHOT_DRIFT"):
                self.main(self.host(lease, authorize=drift_issue)).observe(self.issue)
        self.assert_no_product_effects(before)

    def test_current_ref_change_during_grant_refuses(self):
        source = self.source
        before = self.snapshot()
        def move_current(req):
            new_id, new_record = encode_record(
                REPOSITORY, str(self.issue), source.base,
                {"schema_version": 1, "branch_ref": source.approved.branch_ref,
                 "contract_id": source.registered.contract_id,
                 "disposition": {"kind": "cancelled"}})
            source.fixture.store().publish_issue(
                str(self.issue), [new_record],
                record_update=(source.registered.record_id, new_id))
            return True
        with gate.hold_verified(source.project, source.home, source.package) as lease:
            with self.assertRaisesRegex(MainError, "V2_MAIN_CURRENT_TASK_UNAVAILABLE"):
                self.main(self.host(lease, authorize=move_current)).observe(self.issue)
        self.assert_no_product_effects(before)

    def test_invalid_repository_issue_or_attached_lease_binding_refuse(self):
        before = self.snapshot()
        with gate.hold_verified(self.source.project, self.source.home,
                                self.source.package) as lease:
            with self.assertRaisesRegex(V2MainObservationRefused, "BINDING"):
                self.host(lease, selection=replace(self.source.selection(), issue=202))
            main = self.main(self.host(lease))
            selected = main.approved[self.issue]
            with self.assertRaises(TypeError):
                main.approved[self.issue] = replace(
                    selected, branch_ref="refs/heads/not-approved")
            # Simulate same-process corruption despite the read-only map.
            object.__setattr__(selected, "branch_ref", "refs/heads/not-approved")
            with self.assertRaisesRegex(MainError, "V2_MAIN_TASK_BINDING_MISMATCH"):
                main.observe(self.issue)
        self.assert_no_product_effects(before)

    def test_expired_attachment_lease_refuses_readonly_observation(self):
        before = self.snapshot()
        with gate.hold_verified(self.source.project, self.source.home,
                                self.source.package) as lease:
            main = self.main(self.host(lease))
        with self.assertRaisesRegex(MainError, "V2_MAIN_ATTACHMENT_UNAVAILABLE"):
            main.observe(self.issue)
        self.assert_no_product_effects(before)

    def test_rejects_v2_selection_from_plan_or_untyped_host(self):
        before = self.snapshot()
        forbidden = copy.deepcopy(self.plan)
        forbidden["metadata_version"] = 2
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(prefix="291-r5a0-plan-") as directory:
            plan_path = Path(directory) / "plan.json"
            import json
            plan_path.write_text(json.dumps(forbidden))
            with self.assertRaisesRegex(MainError, "MAIN_PLAN_INVALID"):
                service.load_selection(plan_path)
        with self.assertRaisesRegex(MainError, "MAIN_HOST_CAPABILITIES_INVALID"):
            service.build_main(copy.deepcopy(self.plan), host={"observer": True})
        with self.assertRaisesRegex(MainError, "V2_MAIN_HOST_INVALID"):
            service.MainV2ReadOnlyCapabilities(self.read_issue, {})
        self.assert_no_product_effects(before)

    def test_two_current_issues_are_observed_independently_without_worktrees(self):
        source = self.source
        second_approved = source.approved_issue(202)
        second_registered = source.publish_first(second_approved)
        self.facts[202] = {
            "repository": REPOSITORY, "number": 202, "state": "OPEN",
            "title": second_approved.title, "body": second_approved.body,
        }
        self.plan["tasks"].append(self.selection(second_registered))
        with gate.hold_verified(source.project, source.home, source.package) as lease:
            second = V2ReadOnlyMainHost(
                source.selection(202), source.authority(
                    approved=second_approved, registered=second_registered),
                lease, self.legacy, self.authorize)
            first = self.host(lease)
            main = service.build_main(
                copy.deepcopy(self.plan),
                host=service.MainV2ReadOnlyCapabilities(
                    self.read_issue, {101: first, 202: second}))
            with service.MainSupervisor(main, observe_only=True) as supervisor:
                cycle = supervisor.cycle(dispatch=False)
        self.assertEqual([101, 202], [x["issue"] for x in cycle["tasks"]])
        self.assertEqual(["blocked","blocked"], [x["status"] for x in cycle["tasks"]])
        self.assertNotEqual(
            cycle["tasks"][0]["observation"]["current_record"]["contract_id"],
            cycle["tasks"][1]["observation"]["current_record"]["contract_id"])
        self.assertFalse((source.project / ".worktrees").exists())
        self.assertFalse((source.home / "r5a0-no-runtime").exists())

    def test_partial_v2_host_map_cannot_dispatch_a_second_legacy_issue(self):
        before = self.snapshot()
        second = self.source.approved_issue(202)
        self.plan["tasks"].append(self.selection(self.source.publish_first(second)))
        with gate.hold_verified(self.source.project, self.source.home,
                                self.source.package) as lease:
            with self.assertRaisesRegex(MainError, "V2_MAIN_PARTIAL_HOST_SELECTION"):
                self.main(self.host(lease))
        self.assert_no_product_effects(before)

    def test_direct_supervisor_rejects_before_creating_main_owner(self):
        before = self.snapshot()
        with gate.hold_verified(self.source.project, self.source.home,
                                self.source.package) as lease:
            main = self.main(self.host(lease))
            with self.assertRaisesRegex(MainError, "V2_MAIN_EXECUTION_NOT_QUALIFIED"):
                with service.MainSupervisor(main):
                    self.fail("R5A0 must never acquire the Main owner")
            with self.assertRaisesRegex(MainError, "V2_MAIN_PR_NOT_QUALIFIED"):
                main.guarded_draft_pr(self.issue, object())
        self.assertIsNone(main._main_owner)
        self.assert_no_product_effects(before)

    def test_cli_explicit_v2_observe_only_and_dispatch_refusal(self):
        from contextlib import redirect_stdout
        from io import StringIO
        import json
        before = self.snapshot()
        config = self.source.project.parent / "r5a0-plan.json"
        config.write_text(json.dumps(self.plan), encoding="utf-8")
        try:
            with gate.hold_verified(self.source.project, self.source.home,
                                    self.source.package) as lease:
                host = service.MainV2ReadOnlyCapabilities(
                    self.read_issue, {self.issue: self.host(lease)})
                output = StringIO()
                with redirect_stdout(output):
                    code = service.main(["--config", str(config), "observe"], host=host)
                self.assertEqual(0, code)
                observed = json.loads(output.getvalue())
                self.assertEqual("observe", observed["mode"])
                self.assertEqual("blocked", observed["tasks"][0]["status"])
                self.assertEqual("V2_MAIN_BLOCKED_LIVE", observed["tasks"][0]["reason"])
                output = StringIO()
                with redirect_stdout(output):
                    code = service.main(["--config", str(config), "dispatch"], host=host)
                self.assertEqual(2, code)
                self.assertEqual("V2_MAIN_EXECUTION_NOT_QUALIFIED",
                                 json.loads(output.getvalue())["error_code"])
        finally:
            config.unlink(missing_ok=True)
        self.assert_no_product_effects(before)
