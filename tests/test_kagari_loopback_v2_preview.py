"""KUMIKI #339 R19 Phase A: opt-in v2 Main read-only HTTP preview.

Disposable v2 Git and Attach fixtures; never real GitHub, native model,
Ask reply, Task dispatch, production activation or source publication.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import MappingProxyType
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

    def test_existing_native_pending_is_not_implicitly_actionable(self):
        self.source.existing_worktree()
        self.before = self.source.snapshot()
        self.source.locator_state="PRESENT"
        self.source.native_asks()
        status,asks=self._probe()
        self.assertEqual(status,(200,{"task":"101","state":"UNKNOWN"}))
        self.assertEqual(asks,(503,{"error_code":"ASK_SESSION_UNCERTAIN"}))
        self.assertGreater(len(self.source.http_calls),0)
        self.source.preserved(self.before)


if __name__=="__main__":
    unittest.main()
