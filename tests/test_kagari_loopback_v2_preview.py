"""KUMIKI #339 R19 Phase A: opt-in v2 Main read-only HTTP preview.

Disposable v2 Git and Attach fixtures; never real GitHub, native model,
Ask reply, Task dispatch, production activation or source publication.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import MappingProxyType
import copy
import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from tests import test_kagari_vnext_main_v2_readonly as v2_ro
from tests import test_kagari_vnext_main_v2_session_observation as v2_session
import kagari_vnext_attachment_gate as gate
from tools import kagari_vnext_main as main_module
from tools import kagari_vnext_main_v2_session_observation as v2_session_module
from tools.kagari_loopback_main_bridge import (
    MainBridge, MainBridgeError, create_main_control_server)
from tools.kagari_vnext_main import ApprovedTask


@contextmanager
def listening(main):
    server = create_main_control_server(main)
    thread = threading.Thread(target=server.serve_forever, daemon=False)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        server.drain_admitted_handlers()
        thread.join(timeout=3)
        if thread.is_alive():
            raise AssertionError("R19 disposable listener did not terminate")


def request(server, path: str, *, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    url = "http://127.0.0.1:" + str(server.server_port) + path
    req = Request(url, method="POST" if body is not None else "GET",
                  data=data, headers={"Content-Type": "application/json"} if data else {})
    client = build_opener(ProxyHandler({}))
    try:
        with client.open(req, timeout=10) as response:
            return response.status, json.loads(response.read(65537))
    except HTTPError as error:
        with error:
            return error.code, json.loads(error.read(65537))


class V2StatusPreviewTests(unittest.TestCase):
    def setUp(self):
        source = v2_ro.MainV2ReadOnlyTest("runTest")
        self.addCleanup(source.doCleanups)
        source.setUp()
        self.source = source
        self.before = source.snapshot()
        self.issue = source.issue

    def test_r5a0_current_v2_status_is_unknown_not_503(self):
        s = self.source
        with gate.hold_verified(s.source.project,s.source.home,s.source.package) as lease:
            main = s.main(s.host(lease))
            with patch.object(main_module, "start_root_session",
                              side_effect=AssertionError("no session start")), patch.object(
                    main_module, "opencode_request",
                    side_effect=AssertionError("no model turn")), patch.object(
                    main_module, "isolated_metadata_observation",
                    side_effect=AssertionError("no v1 fallback")):
                with listening(main) as server:
                    self.assertEqual(request(server,f"/tasks/{self.issue}/status"),
                                     (200,{"task":str(self.issue),"state":"UNKNOWN"}))
                    # R5A0 has no positive Session-absence observation.
                    self.assertEqual(request(server,f"/tasks/{self.issue}/asks"),
                                     (503,{"error_code":"ASK_SESSION_UNCERTAIN"}))
        s.assert_no_product_effects(self.before)

    def test_v2_real_dispatch_still_unqualified(self):
        s = self.source
        with gate.hold_verified(s.source.project,s.source.home,s.source.package) as lease:
            main = s.main(s.host(lease))
            contract = s.source.registered.contract_id
            with listening(main) as server:
                self.assertEqual(request(
                    server,f"/tasks/{self.issue}/dispatch",
                    body={"schema_version":1,"expected_contract_id":contract}),
                    (503,{"error_code":"V2_MAIN_EXECUTION_NOT_QUALIFIED"}))
        s.assert_no_product_effects(self.before)

    def test_v2_changed_current_record_refuses_both_reads(self):
        s = self.source
        with gate.hold_verified(s.source.project,s.source.home,s.source.package) as lease:
            main = s.main(s.host(lease))
            view = main.observe(self.issue)
            foreign = dict(view)
            foreign["current_record"] = {**view["current_record"],
                                         "contract_id":"f"*64}
            with patch.object(main, "observe", return_value=foreign):
                with listening(main) as server:
                    self.assertEqual(request(server,f"/tasks/{self.issue}/status"),
                                     (503,{"error_code":"TASK_OBSERVATION_UNAVAILABLE"}))
                    self.assertEqual(request(server,f"/tasks/{self.issue}/asks"),
                                     (503,{"error_code":"V2_MAIN_OBSERVATION_MISMATCH"}))
        s.assert_no_product_effects(self.before)

    def test_v2_bogus_execution_grant_refuses(self):
        s = self.source
        with gate.hold_verified(s.source.project,s.source.home,s.source.package) as lease:
            main = s.main(s.host(lease))
            view = main.observe(self.issue)
            bogus = {**view,"task_execution_authorized":True}
            with patch.object(main, "observe", return_value=bogus):
                with listening(main) as server:
                    self.assertEqual(request(server,f"/tasks/{self.issue}/status"),
                                     (503,{"error_code":"TASK_OBSERVATION_UNAVAILABLE"}))
        s.assert_no_product_effects(self.before)

    def test_boolean_approved_task_issue_cannot_impersonate_one(self):
        s = self.source
        with gate.hold_verified(s.source.project,s.source.home,s.source.package) as lease:
            main = s.main(s.host(lease))
            chosen = s.source.registered
            malformed = ApprovedTask(
                True, chosen.branch_ref, chosen.base_revision,
                chosen.contract_id)
            # Only this verifier check needs malformed external Main state.
            # Construct a no-side-effect stub; real RepositoryMain admission
            # would independently reject the noncanonical selection.
            stub = object.__new__(type(main))
            stub._approved = MappingProxyType({1: malformed})
            with self.assertRaisesRegex(MainBridgeError,"TASK_NOT_APPROVED"):
                MainBridge(stub)._approved("1")
        s.assert_no_product_effects(self.before)

    def test_issue_fields_independently_reject_float_and_bool(self):
        s = self.source
        with gate.hold_verified(s.source.project,s.source.home,s.source.package) as lease:
            main = s.main(s.host(lease))
            view = main.observe(self.issue)
            choice = ApprovedTask(
                1, s.source.registered.branch_ref,
                s.source.registered.base_revision,
                s.source.registered.contract_id)
            bridge = MainBridge(main)
            for field in ("top","approved"):
                for bad in (True,1.0):
                    with self.subTest(field=field,bad=bad):
                        spoofed=dict(view)
                        spoofed["issue"]=1
                        spoofed["approved_task"]={**view["approved_task"],"issue":1}
                        if field=="top":spoofed["issue"]=bad
                        else:spoofed["approved_task"]["issue"]=bad
                        with self.assertRaisesRegex(
                            MainBridgeError,"V2_MAIN_OBSERVATION_MISMATCH"):
                            bridge._v2_readonly_view(1,choice,spoofed)
        s.assert_no_product_effects(self.before)

    def test_boolean_issue_number_cannot_impersonate_issue_one(self):
        s = self.source
        with gate.hold_verified(s.source.project,s.source.home,s.source.package) as lease:
            main = s.main(s.host(lease))
            view = main.observe(self.issue)
            choice = ApprovedTask(
                1, s.source.registered.branch_ref,
                s.source.registered.base_revision,
                s.source.registered.contract_id)
            spoofed = {**view, "issue": True,
                       "approved_task": {**view["approved_task"], "issue": True}}
            with self.assertRaisesRegex(
                    MainBridgeError, "V2_MAIN_OBSERVATION_MISMATCH"):
                MainBridge(main)._v2_readonly_view(1, choice, spoofed)
        s.assert_no_product_effects(self.before)

    def test_v2_unapproved_issue_is_never_discovered(self):
        s = self.source
        with gate.hold_verified(s.source.project,s.source.home,s.source.package) as lease:
            main = s.main(s.host(lease))
            with listening(main) as server:
                self.assertEqual(request(server,"/tasks/102/asks"),
                                 (409,{"error_code":"TASK_NOT_APPROVED"}))
        s.assert_no_product_effects(self.before)


class V2SessionPreviewTests(unittest.TestCase):
    def setUp(self):
        source = v2_session.R5B0SessionReadOnlyTests("runTest")
        self.addCleanup(source.doCleanups)
        source.setUp()
        self.source = source
        self.before = source.snapshot()

    def _valid_native_asks(self):
        """Model the required OpenCode 1.18.35 QuestionInfo/Option fields.

        The older #291 R5B0 observation fixture intentionally omits optional
        display details; R21's positive HTTP view has a stricter validated
        supported-body subset.
        """
        self.source.native_asks()
        for question in self.source.questions[0]["questions"]:
            question["question"] = "Continue with this Task?"
            for option in question["options"]:
                option["description"] = option["label"] + " choice"

    def _probe(self):
        s = self.source
        with gate.hold_verified(s.src.project,s.src.home,s.src.package) as lease:
            main = s.main({s.issue:s.make_host(lease)})
            with patch.object(v2_session_module.OpenCode, "request",
                    new=lambda client,path,method="GET",payload=None:
                    s.mock_request(client,path,method,payload)), patch.object(
                    main_module, "start_root_session",
                    side_effect=AssertionError("Session start forbidden")), patch.object(
                    main_module, "opencode_request",
                    side_effect=AssertionError("Model turn forbidden")), patch.object(
                    main_module, "isolated_metadata_observation",
                    side_effect=AssertionError("v1 metadata fallback forbidden")):
                with listening(main) as server:
                    return (
                        request(server,"/tasks/101/status"),
                        request(server,"/tasks/101/asks"))

    def test_host_confirmed_absent_yields_409_not_empty_queue(self):
        status,asks = self._probe()
        self.assertEqual(status,(200,{"task":"101","state":"UNKNOWN"}))
        self.assertEqual(asks,(409,{"error_code":"ASK_SESSION_NOT_STARTED"}))
        self.assertEqual([],self.source.http_calls)
        self.source.preserved(self.before)

    def test_unknown_host_session_does_not_invent_absence(self):
        self.source.locator_state="UNKNOWN"
        status,asks=self._probe()
        self.assertEqual(status,(200,{"task":"101","state":"UNKNOWN"}))
        self.assertEqual(asks,(503,{"error_code":"ASK_SESSION_UNCERTAIN"}))
        self.assertEqual([],self.source.http_calls)
        self.source.preserved(self.before)

    def test_false_pending_count_is_not_verified_absent(self):
        s = self.source
        with gate.hold_verified(s.src.project,s.src.home,s.src.package) as lease:
            main = s.main({s.issue:s.make_host(lease)})
            view = main.observe(s.issue)
            assert view["session_observation"]["state"]=="NOT_STARTED"
            spoofed = {**view, "session_observation": {
                **view["session_observation"], "pending_count": False}}
            with patch.object(main, "observe", return_value=spoofed):
                with listening(main) as server:
                    self.assertEqual(
                        request(server,"/tasks/101/asks"),
                        (503,{"error_code":"ASK_SESSION_UNCERTAIN"}))
        s.preserved(self.before)

    def test_existing_native_pending_is_readonly_and_task_bound(self):
        self.source.existing_worktree()
        self.before = self.source.snapshot()
        self.source.locator_state="PRESENT"
        self._valid_native_asks()
        status, asks = self._probe()
        self.assertEqual(status, (200, {"task": "101", "state": "UNKNOWN"}))
        self.assertEqual(200, asks[0])
        result = asks[1]
        self.assertEqual("101", result["task"])
        self.assertEqual(self.source.src.registered.contract_id, result["contract_id"])
        self.assertEqual(v2_session.SID, result["root_session_id"])
        self.assertEqual(2, len(result["pending"]))
        self.assertEqual(
            {v2_session.PER, v2_session.QUE},
            {x["request_id"] for x in result["pending"]})
        self.assertEqual({"permission", "question"},
                         {x["kind"] for x in result["pending"]})
        self.assertTrue(all(x["upstream"]["sessionID"] == v2_session.SID
                            for x in result["pending"]))
        self.assertGreater(len(self.source.http_calls), 0)
        self.source.preserved(self.before)

    def test_present_native_session_without_pending_is_not_empty_success(self):
        self.source.existing_worktree()
        self.before = self.source.snapshot()
        self.source.locator_state="PRESENT"
        self.source.native_status="idle"
        status, asks = self._probe()
        self.assertEqual(status, (200, {"task": "101", "state": "UNKNOWN"}))
        self.assertEqual(asks, (503, {"error_code": "ASK_SESSION_UNCERTAIN"}))
        self.source.preserved(self.before)

    def _spoof_pending(self, transform):
        s = self.source
        s.existing_worktree()
        self.before = s.snapshot()
        self._valid_native_asks()
        with gate.hold_verified(s.src.project, s.src.home, s.src.package) as lease:
            main = s.main({s.issue: s.make_host(lease)})
            with patch.object(v2_session_module.OpenCode, "request",
                    new=lambda client, path, method="GET", payload=None:
                    s.mock_request(client, path, method, payload)):
                actual = main.observe(s.issue)
                self.assertEqual("PENDING_ASK",
                                 actual["session_observation"]["state"])
                changed = copy.deepcopy(actual)
                transform(changed["session_observation"])
                with patch.object(main, "observe", return_value=changed):
                    with listening(main) as server:
                        status = request(server, "/tasks/101/status")
                        asks = request(server, "/tasks/101/asks")
        self.assertEqual(status, (200, {"task": "101", "state": "UNKNOWN"}))
        s.preserved(self.before)
        return asks

    def test_bogus_upstream_session_fails_closed(self):
        def modify(session):
            session["pending_asks"][0]["upstream"]["sessionID"]="ses_foreign12345"
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code": "ASK_BINDING_MISMATCH"}))

    def test_foreign_contract_in_bound_ask_fails_closed(self):
        def modify(session):
            session["pending_asks"][0]["task"]["contract_sha256"]="f"*64
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code": "ASK_BINDING_MISMATCH"}))

    def test_permission_requires_native_body_not_just_ids(self):
        def modify(session):
            upstream=session["pending_asks"][0]["upstream"]
            upstream.pop("permission",None)
            upstream.pop("patterns",None)
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code":"ASK_OBSERVATION_INVALID"}))

    def test_permission_rejects_malformed_patterns(self):
        def modify(session):
            session["pending_asks"][0]["upstream"]["patterns"]=[False]
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code":"ASK_OBSERVATION_INVALID"}))

    def test_question_requires_nested_question_body(self):
        def modify(session):
            session["pending_asks"][1]["upstream"].pop("questions",None)
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code":"ASK_OBSERVATION_INVALID"}))

    def test_question_missing_text_is_not_a_verified_ask(self):
        def modify(session):
            session["pending_asks"][1]["upstream"]["questions"][0].pop("question")
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code":"ASK_OBSERVATION_INVALID"}))

    def test_question_wrong_text_type_is_not_a_verified_ask(self):
        def modify(session):
            session["pending_asks"][1]["upstream"]["questions"][0]["question"]=False
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code":"ASK_OBSERVATION_INVALID"}))

    def test_question_excessive_text_is_not_a_verified_ask(self):
        def modify(session):
            session["pending_asks"][1]["upstream"]["questions"][0]["question"]="x"*5000
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code":"ASK_OBSERVATION_INVALID"}))

    def test_question_missing_option_description_is_not_verified(self):
        def modify(session):
            session["pending_asks"][1]["upstream"]["questions"][0]["options"][0].pop("description")
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code":"ASK_OBSERVATION_INVALID"}))

    def test_question_optional_flag_wrong_type_is_not_verified(self):
        def modify(session):
            session["pending_asks"][1]["upstream"]["questions"][0]["multiple"]=0
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code":"ASK_OBSERVATION_INVALID"}))

    def test_oversized_extra_upstream_data_is_rejected_by_existing_http_bound(self):
        def modify(session):
            session["pending_asks"][0]["upstream"]["metadata"]["padding"]="x"*70000
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code":"ASK_OBSERVATION_TOO_LARGE"}))

    def test_question_rejects_malformed_option_and_flags(self):
        def modify(session):
            question=session["pending_asks"][1]["upstream"]["questions"][0]
            question["options"]=[{"label":None}]
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code":"ASK_OBSERVATION_INVALID"}))

    def test_duplicate_request_identity_fails_closed(self):
        def modify(session):
            session["pending_asks"][1]=copy.deepcopy(session["pending_asks"][0])
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code": "ASK_BINDING_MISMATCH"}))

    def test_wrong_pending_count_fails_closed(self):
        def modify(session):
            session["pending_count"]=0
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code": "ASK_SESSION_UNCERTAIN"}))

    def test_boolean_issue_in_session_subject_fails_closed(self):
        def modify(session):
            session["task"]["issue"]=True
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code": "ASK_SESSION_UNCERTAIN"}))

    def test_consistent_foreign_absolute_worktree_is_still_rejected(self):
        def modify(session):
            foreign="/tmp/foreign-registered-worktree"
            session["worktree"]=foreign
            session["task"]["worktree"]=foreign
            for pending in session["pending_asks"]:
                pending["task"]["worktree"]=foreign
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code":"ASK_SESSION_UNCERTAIN"}))

    def test_foreign_worktree_binding_fails_closed(self):
        def modify(session):
            session["pending_asks"][0]["task"]["worktree"]="/tmp/foreign-issue"
        self.assertEqual(self._spoof_pending(modify),
                         (503, {"error_code": "ASK_BINDING_MISMATCH"}))


if __name__=="__main__":
    unittest.main()
