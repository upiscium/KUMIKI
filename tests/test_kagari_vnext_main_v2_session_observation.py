"""#291 R5B0 — disposable Metadata v2 Session/Ask read-only Main tests.

All Git repositories and #256 Attach receipts come from disposable fixtures.
The OpenCode backend is a mock GET-only adapter: no server, process, model,
Session start, native Ask reply, GitHub write or production Worktree is used.
"""
from __future__ import annotations

import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_kagari_metadata_v2_task_admission as v2_fixture
from tests.test_metadata_v2 import REPOSITORY
from tools import kagari_vnext_main as main_module
from tools import kagari_vnext_main_service as service
from tools import kagari_vnext_main_v2_session_observation as session_module
from tools.kagari_vnext_main import MainError
from tools.kagari_vnext_main_v2_observation import V2ReadOnlyMainHost
from tools.kagari_vnext_main_v2_session_observation import (
    V2MainSessionReadOnlyHost, V2SessionLocator, V2SessionObservationRefused)
import kagari_vnext_attachment_gate as gate
from task_record import encode_record


SID = "ses_r5b0root123456"
PER = "per_r5b0permission123"
QUE = "que_r5b0question123"


class R5B0SessionReadOnlyTests(unittest.TestCase):
    def setUp(self):
        src = v2_fixture.V2TaskAdmissionTest("runTest")
        self.addCleanup(src.doCleanups)
        src.setUp()
        self.src = src
        self.issue = src.registered.issue
        self.facts = {101: self.issue_facts(src.approved)}
        self.plan = {
            "schema_version": 1, "project_root": str(src.project),
            "repository": REPOSITORY,
            "runtime_home": str(src.home / "r5b0-never-create-runtime"),
            "tasks": [self.task(src.registered)],
        }
        self.locator_state = "ABSENT"
        self.locator_hook = None
        self.upstream_hook = None
        self.native_status = "busy"
        self.http_calls = []
        self.host_reads = []
        self.issue_reads = []
        self.observation_granted = True
        self.legacy_clear = True
        self.permissions = []
        self.questions = []
        self.session_id = SID
        self.session_dir = None
        self.parent_id = None

    @staticmethod
    def task(registered):
        return {
            "issue": registered.issue,
            "branch_ref": registered.branch_ref,
            "base_revision": registered.base_revision,
            "contract_id": registered.contract_id,
            "port": 32000 + registered.issue,
        }

    @staticmethod
    def issue_facts(approved):
        return {
            "repository": approved.repository, "number": approved.issue,
            "state": "OPEN", "title": approved.title, "body": approved.body,
        }

    def issue_reader(self, issue):
        self.issue_reads.append(issue)
        return copy.deepcopy(self.facts[issue])

    def legacy(self, req):
        return self.legacy_clear

    def authorize_read(self, req):
        return self.observation_granted

    def selected_locator(self, req):
        self.host_reads.append(req)
        if self.locator_hook is not None:
            return self.locator_hook(req)
        if self.locator_state == "PRESENT":
            return V2SessionLocator(
                "PRESENT", req.repository, req.issue, req.contract_id,
                str(self.src.project / ".worktrees" / str(req.issue)),
                SID, "http://127.0.0.1:43211")
        return V2SessionLocator(
            self.locator_state, req.repository, req.issue, req.contract_id)

    def make_host(self, lease, *, registered=None, approved=None,
                  locator=None, observer=None):
        src = self.src
        registered = registered or src.registered
        readonly = V2ReadOnlyMainHost(
            src.selection(registered.issue),
            src.authority(registered=registered,
                          approved=approved or src.approved,
                          observer=observer),
            lease, self.legacy, self.authorize_read)
        return V2MainSessionReadOnlyHost(
            readonly, locator or self.selected_locator)

    def main(self, hosts):
        return service.build_main(
            copy.deepcopy(self.plan),
            host=service.MainV2SessionReadOnlyCapabilities(
                self.issue_reader, hosts))

    def snapshot(self):
        src = self.src
        return {
            "head": src.attached.git("rev-parse", "HEAD"),
            "status": src.attached.git("status", "--porcelain=v1", "-uall"),
            "index": (src.project / ".git/index").read_bytes(),
            "attach": src.attached.receipt().read_bytes(),
        }

    def preserved(self, earlier):
        src = self.src
        self.assertEqual(earlier["head"], src.attached.git("rev-parse", "HEAD"))
        self.assertEqual(earlier["status"], src.attached.git(
            "status", "--porcelain=v1", "-uall"))
        self.assertEqual(earlier["index"], (src.project / ".git/index").read_bytes())
        self.assertEqual(earlier["attach"], src.attached.receipt().read_bytes())
        self.assertFalse((src.home / "r5b0-never-create-runtime").exists())
        self.assertTrue(all(method == "GET" and payload is None
                            for _, method, payload in self.http_calls))

    def mock_request(self, _client, path, method="GET", payload=None):
        self.http_calls.append((path, method, payload))
        if method != "GET" or payload is not None:
            raise AssertionError("R5B0 must never issue HTTP writes")
        if self.upstream_hook is not None:
            self.upstream_hook(path)
        if path == "/session/" + SID:
            return {
                "id": self.session_id,
                "directory": self.session_dir
                             or str(self.src.project / ".worktrees" / "101"),
                "parentID": self.parent_id,
            }
        if path == "/session/status":
            return {} if self.native_status == "idle" else {
                SID: {"type": self.native_status}}
        if path == "/permission":
            return copy.deepcopy(self.permissions)
        if path == "/question":
            return copy.deepcopy(self.questions)
        raise AssertionError("Unexpected OpenCode API: " + path)

    def probe(self, *, hosts=None, mode="cycle"):
        src = self.src
        with gate.hold_verified(src.project, src.home, src.package) as lease:
            candidates = hosts(lease) if callable(hosts) else {
                101: self.make_host(lease)}
            main = self.main(candidates)
            with patch.object(
                    session_module.OpenCode, "request",
                    new=lambda client, path, method="GET", payload=None:
                    self.mock_request(client, path, method, payload)), patch.object(
                    main_module, "start_root_session",
                    side_effect=AssertionError("Session start forbidden")), patch.object(
                    main_module, "opencode_request",
                    side_effect=AssertionError("model turn forbidden")), patch.object(
                    main_module, "isolated_metadata_observation",
                    side_effect=AssertionError("v1 metadata fallback forbidden")):
                if mode == "direct":
                    return main.observe(101)
                with service.MainSupervisor(main, observe_only=True) as runner:
                    if mode == "dispatch":
                        return runner.cycle(dispatch=True)
                    return runner.cycle(dispatch=False)

    def existing_worktree(self):
        result = self.src.admit()  # Test-only disposable #337 Worktree fixture.
        self.assertIn(result.status, ("CREATED", "RESUMED"))
        self.locator_state = "PRESENT"
        return result.worktree

    def native_asks(self):
        self.permissions = [{
            "id": PER, "sessionID": SID, "permission": "bash",
            "patterns": ["pwd"], "metadata": {"command": "pwd"}}]
        self.questions = [{
            "id": QUE, "sessionID": SID,
            "questions": [{"header": "Proceed?", "options": [
                {"label": "Yes"}, {"label": "No"}]}]}]

    def blocked(self, response):
        self.assertEqual("observe", response["mode"])
        self.assertEqual("blocked", response["tasks"][0]["status"])
        return response["tasks"][0]

    def test_positive_host_confirmed_absence_not_started(self):
        before = self.snapshot()
        answer = self.blocked(self.probe())["observation"]
        self.assertEqual("V2_MAIN_BLOCKED_LIVE", answer["reason"])
        self.assertEqual("NOT_STARTED", answer["session_observation"]["state"])
        self.assertEqual([], answer["session_observation"]["pending_asks"])
        self.assertEqual("NOT_STARTED", answer["runtime"])
        self.assertEqual("NOT_SENT", answer["kickoff"])
        self.assertFalse(answer["task_execution_authorized"])
        self.assertEqual([], self.http_calls)
        self.assertGreater(len(self.host_reads), 0)
        self.preserved(before)

    def test_unknown_host_result_never_invents_empty_ask_or_completion(self):
        before = self.snapshot()
        self.locator_state = "UNKNOWN"
        result = self.probe(mode="direct")
        self.assertEqual("blocked", result["status"])
        view = result["session_observation"]
        self.assertEqual("UNKNOWN", view["state"])
        self.assertIsNone(view["pending_asks"])
        self.assertIsNone(view["pending_count"])
        self.assertEqual("UNKNOWN", result["runtime"])
        self.assertEqual([], self.http_calls)
        self.preserved(before)

    def test_existing_busy_session_is_rooted_and_read_only(self):
        self.existing_worktree()
        before = self.snapshot()
        answer = self.blocked(self.probe())["observation"]
        view = answer["session_observation"]
        self.assertEqual("RUNNING", view["state"])
        self.assertEqual(SID, view["session_id"])
        self.assertEqual("busy", view["session_state"])
        self.assertEqual([], view["pending_asks"])
        self.assertEqual("EXISTING_SESSION_OBSERVED", answer["runtime"])
        self.assertEqual("UNKNOWN", answer["kickoff"])
        self.assertFalse(answer["task_execution_authorized"])
        self.assertFalse(answer["github_write_authorized"])
        self.assertGreaterEqual(len(self.http_calls), 10)
        self.preserved(before)

    def test_real_native_permission_and_question_are_observed_not_answered(self):
        self.existing_worktree()
        self.native_asks()
        before = self.snapshot()
        answer = self.probe(mode="direct")
        view = answer["session_observation"]
        self.assertEqual("PENDING_ASK", view["state"])
        self.assertEqual(2, view["pending_count"])
        self.assertEqual({PER, QUE},
                         {x["request_id"] for x in view["pending_asks"]})
        self.assertEqual({"permission", "question"},
                         {x["kind"] for x in view["pending_asks"]})
        self.assertTrue(all(x["task"]["contract_sha256"] ==
                            self.src.registered.contract_id
                            for x in view["pending_asks"]))
        self.preserved(before)

    def test_idle_does_not_establish_task_completion(self):
        self.existing_worktree()
        self.native_status = "idle"
        before = self.snapshot()
        view = self.probe(mode="direct")["session_observation"]
        self.assertEqual("UNKNOWN", view["state"])
        self.assertEqual("IDLE_NOT_TASK_COMPLETION", view["reason"])
        self.assertNotEqual("COMPLETED", view["state"])
        self.preserved(before)

    def test_unavailable_locator_and_bad_host_results_refuse(self):
        before = self.snapshot()
        for locator in (lambda req: None,
                        lambda req: "ABSENT",
                        lambda req: V2SessionLocator(
                            "ABSENT", req.repository, 202, req.contract_id),
                        lambda req: V2SessionLocator(
                            "ABSENT", "foreign/repo", req.issue,
                            req.contract_id)):
            with self.subTest(locator=locator):
                row = self.blocked(self.probe(
                    hosts=lambda lease: {101: self.make_host(
                        lease, locator=locator)}))
                self.assertIn(row["reason"],
                              ("V2_SESSION_LOCATOR_MISMATCH",
                               "V2_SESSION_LOCATOR_UNAVAILABLE"))
                self.assertNotIn("observation", row)
        self.preserved(before)

    def test_locator_read_error_blocks_without_fabricating_no_pending(self):
        before = self.snapshot()
        def unavailable(_):
            raise RuntimeError("not reachable")
        row = self.blocked(self.probe(
            hosts=lambda lease: {101: self.make_host(
                lease, locator=unavailable)}))
        self.assertEqual("V2_SESSION_LOCATOR_UNAVAILABLE", row["reason"])
        self.preserved(before)

    def test_changed_issue_during_locator_read_blocks_all_status(self):
        before = self.snapshot()
        def mutate(req):
            self.facts[101]["title"] = "changed requirements"
            return V2SessionLocator("ABSENT", req.repository, req.issue,
                                    req.contract_id)
        row = self.blocked(self.probe(
            hosts=lambda lease: {101: self.make_host(
                lease, locator=mutate)}))
        self.assertEqual("V2_MAIN_ISSUE_SNAPSHOT_DRIFT", row["reason"])
        self.preserved(before)

    def test_stale_retired_current_record_refuses_old_v2_history(self):
        src = self.src
        before = self.snapshot()
        rid, record = encode_record(
            REPOSITORY, "101", src.base,
            {"schema_version": 1, "branch_ref": src.approved.branch_ref,
             "contract_id": src.registered.contract_id,
             "disposition": {"kind": "cancelled"}})
        src.fixture.store().publish_issue(
            "101", [record],
            record_update=(src.registered.record_id, rid))
        row = self.blocked(self.probe())
        self.assertEqual("V2_MAIN_CURRENT_TASK_UNAVAILABLE", row["reason"])
        self.assertEqual([], self.http_calls)
        self.preserved(before)

    def test_current_ref_change_during_remote_get_is_refused(self):
        self.existing_worktree()
        before = self.snapshot()
        mutated = [False]
        def change(path):
            if path != "/session/status" or mutated[0]:
                return
            mutated[0] = True
            src = self.src
            rid, record = encode_record(
                REPOSITORY, "101", src.base,
                {"schema_version": 1, "branch_ref": src.approved.branch_ref,
                 "contract_id": src.registered.contract_id,
                 "disposition": {"kind": "cancelled"}})
            src.fixture.store().publish_issue(
                "101", [record],
                record_update=(src.registered.record_id, rid))
        self.upstream_hook = change
        row = self.blocked(self.probe())
        self.assertTrue(mutated[0])
        self.assertEqual("V2_MAIN_CURRENT_TASK_UNAVAILABLE", row["reason"])
        self.preserved(before)

    def test_changes_in_native_ask_queue_during_get_refuse_snapshot(self):
        self.existing_worktree()
        self.native_asks()
        before = self.snapshot()
        changes = [0]
        def unstable(path):
            if path == "/permission":
                changes[0] += 1
                if changes[0] == 2:
                    self.permissions.clear()
        self.upstream_hook = unstable
        row = self.blocked(self.probe())
        self.assertEqual("V2_SESSION_UPSTREAM_CHANGED", row["reason"])
        self.assertGreaterEqual(changes[0], 2)
        self.preserved(before)

    def test_wrong_parent_or_foreign_session_refuses(self):
        self.existing_worktree()
        before = self.snapshot()
        for attr, value in (("parent_id", "ses_parent12345"),
                            ("session_id", "ses_foreign1234"),
                            ("session_dir", "/tmp/not-our-worktree")):
            with self.subTest(attr=attr):
                old = getattr(self, attr)
                setattr(self, attr, value)
                try:
                    row = self.blocked(self.probe())
                    self.assertEqual("V2_SESSION_UPSTREAM_UNAVAILABLE",
                                     row["reason"])
                finally:
                    setattr(self, attr, old)
        self.preserved(before)

    def test_foreign_worktree_locator_is_denied_before_get(self):
        self.existing_worktree()
        before = self.snapshot()
        def wrong(req):
            return V2SessionLocator(
                "PRESENT", req.repository, req.issue, req.contract_id,
                str(self.src.project / ".worktrees" / "202"),
                SID, "http://127.0.0.1:43211")
        row = self.blocked(self.probe(
            hosts=lambda lease: {101: self.make_host(
                lease, locator=wrong)}))
        self.assertEqual("V2_SESSION_WORKTREE_MISMATCH", row["reason"])
        self.assertEqual([], self.http_calls)
        self.preserved(before)

    def test_unregistered_worktree_refuses_without_http_read(self):
        before = self.snapshot()
        self.locator_state = "PRESENT"
        row = self.blocked(self.probe())
        self.assertEqual("V2_SESSION_WORKTREE_UNVERIFIED", row["reason"])
        self.assertEqual([], self.http_calls)
        self.preserved(before)

    def test_nonloopback_url_refuses_without_http_read(self):
        self.existing_worktree()
        before = self.snapshot()
        def nonloop(req):
            return V2SessionLocator(
                "PRESENT", req.repository, req.issue, req.contract_id,
                str(self.src.project / ".worktrees" / str(req.issue)),
                SID, "http://0.0.0.0:43211")
        row = self.blocked(self.probe(
            hosts=lambda lease: {101: self.make_host(
                lease, locator=nonloop)}))
        self.assertEqual("V2_SESSION_UPSTREAM_UNAVAILABLE", row["reason"])
        self.assertEqual([], self.http_calls)
        self.preserved(before)

    def test_foreign_native_ask_is_not_returned_as_our_pending(self):
        self.existing_worktree()
        before = self.snapshot()
        self.permissions = [{"id": PER, "sessionID": "ses_otherroot12345",
                             "permission": "bash", "patterns": ["pwd"]}]
        result = self.probe(mode="direct")
        self.assertEqual("RUNNING", result["session_observation"]["state"])
        self.assertEqual([], result["session_observation"]["pending_asks"])
        self.preserved(before)

    def test_default_and_v2_readonly_lane_are_unchanged(self):
        before = self.snapshot()
        no_host = service.build_main(copy.deepcopy(self.plan))
        with self.assertRaisesRegex(MainError, "V2_MAIN_HOST_REQUIRED"):
            no_host.observe_v2(101)
        with gate.hold_verified(self.src.project, self.src.home,
                                self.src.package) as lease:
            old = service.build_main(
                copy.deepcopy(self.plan),
                host=service.MainV2ReadOnlyCapabilities(
                    self.issue_reader, {101: self.make_host(lease).readonly}))
            result = old.observe(101)
            self.assertEqual("V2_MAIN_BLOCKED_LIVE", result["reason"])
            self.assertNotIn("session_observation", result)
        self.preserved(before)

    def test_partial_host_map_refuses_not_v1_fallback(self):
        src = self.src
        two = src.approved_issue(202)
        record = src.publish_first(two)
        self.plan["tasks"].append(self.task(record))
        before = self.snapshot()
        with gate.hold_verified(src.project, src.home, src.package) as lease:
            with self.assertRaisesRegex(MainError, "V2_MAIN_PARTIAL_HOST_SELECTION"):
                self.main({101: self.make_host(lease)})
        self.preserved(before)

    def test_two_issue_observations_keep_exact_contract_scope(self):
        src = self.src
        two = src.approved_issue(202)
        record = src.publish_first(two)
        self.plan["tasks"].append(self.task(record))
        self.facts[202] = self.issue_facts(two)
        self.locator_state = "ABSENT"
        before = self.snapshot()
        def hosts(lease):
            return {101: self.make_host(lease), 202: self.make_host(
                lease, registered=record, approved=two)}
        output = self.probe(hosts=hosts)
        self.assertEqual([101, 202], [x["issue"] for x in output["tasks"]])
        self.assertEqual(["blocked", "blocked"],
                         [x["status"] for x in output["tasks"]])
        first, second = (x["observation"] for x in output["tasks"])
        self.assertNotEqual(first["current_record"]["contract_id"],
                            second["current_record"]["contract_id"])
        self.assertEqual({101, 202}, {x.issue for x in self.host_reads})
        self.preserved(before)

    def test_observe_only_supervisor_and_direct_dispatch_forbidden(self):
        before = self.snapshot()
        row = self.blocked(self.probe())
        self.assertEqual("NOT_STARTED",
                         row["observation"]["session_observation"]["state"])
        with self.assertRaisesRegex(MainError, "MAIN_OBSERVE_ONLY"):
            self.probe(mode="dispatch")
        self.preserved(before)

    def test_source_only_no_v1_reader_start_prompt_reply_or_git_effect(self):
        self.existing_worktree()
        self.native_asks()
        before = self.snapshot()
        from tools import kagari_vnext_task_binding as task
        from tools import kagari_vnext_opencode_control as ctl
        with patch.object(task, "verify_task_record",
                          side_effect=AssertionError("v1 Record forbidden")), patch.object(
                task, "resolve",
                side_effect=AssertionError("v1 resolver forbidden")), patch.object(
                ctl, "reply",
                side_effect=AssertionError("native Ask reply forbidden")):
            observed = self.probe(mode="direct")
        self.assertEqual("PENDING_ASK",
                         observed["session_observation"]["state"])
        self.preserved(before)

    def test_invalid_locator_constructor_rejects_uncertain_absence(self):
        for state in ("", "absent", "STARTED", "PRESENT"):
            with self.subTest(state=state):
                with self.assertRaises(V2SessionObservationRefused):
                    V2SessionLocator(state, REPOSITORY, 101, "0" * 64)
        with self.assertRaises(V2SessionObservationRefused):
            V2SessionLocator("UNKNOWN", REPOSITORY, 101, "0" * 64,
                             "/tmp/foreign", SID, "http://127.0.0.1:80")

    def test_final_issue_callback_attachment_revocation_refuses_result(self):
        """P2 regression: lease becomes invalid during R5A0's final Issue read.

        Inject expiry through the *existing* AttachmentLease.verify() seam,
        not by changing #256's on-disk receipt (which itself requires inode
        immutability for the entire held Attach scope).
        """
        before = self.snapshot()
        src = self.src
        seen = [0]
        revoked = [False]
        original_verify = gate.AttachmentLease.verify

        def unchanged_issue_with_revocation(issue):
            seen[0] += 1
            if seen[0] == 4:
                revoked[0] = True
            return copy.deepcopy(self.facts[issue])

        def revoked_verify(lease, *, project):
            if revoked[0]:
                raise gate.GateBlocked("GATE_ATTACHMENT_CHANGED_DURING_USE")
            return original_verify(lease, project=project)

        with gate.hold_verified(src.project, src.home, src.package) as lease:
            host = self.make_host(lease)
            main = service.build_main(
                copy.deepcopy(self.plan),
                host=service.MainV2SessionReadOnlyCapabilities(
                    unchanged_issue_with_revocation, {101: host}))
            with patch.object(gate.AttachmentLease, "verify",
                              new=revoked_verify):
                with service.MainSupervisor(main, observe_only=True) as runner:
                    outcome = runner.cycle(dispatch=False)
                    blocked = self.blocked(outcome)
                    self.assertEqual(
                        "V2_MAIN_ATTACHMENT_UNAVAILABLE", blocked["reason"])
                    self.assertNotIn("observation", blocked)
            self.assertGreaterEqual(seen[0], 4)
            self.assertTrue(revoked[0])
        self.assertEqual([], self.http_calls)
        self.preserved(before)
