"""#339 source-only HTTP -> real #291 Main authority/receipt tests.

Uses disposable Git/Metadata and a fake OpenCode HTTP process supplied by
existing #291 fixture; no production Task, credential, or GitHub mutation.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

from tests import test_kagari_vnext_main as main_fixture
from tools.kagari_vnext_main_service import MainSupervisor
import tools.kagari_vnext_main_service as service
from tools.kagari_loopback_main_bridge import MainBridge, create_main_control_server


# This code runs in a separate Python interpreter with no KUMIKI imports,
# Task metadata object, Worktree handle, or MainSupervisor owner. The only
# observation capability it receives is an exact loopback listening port.
_FRESH_OBSERVER = r"""
import json
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

port, issue = sys.argv[1:]
base = "http://127.0.0.1:" + port + "/tasks/" + issue + "/"
opener = build_opener(ProxyHandler({}))
result = {}
for endpoint in ("status", "asks"):
    for _ in range(12):
        try:
            with opener.open(Request(base + endpoint), timeout=3) as response:
                code = response.status
                payload = json.loads(response.read(65537))
        except HTTPError as error:
            try:
                code = error.code
                payload = json.loads(error.read(65537))
            finally:
                error.close()
        if code == 503 and payload.get("error_code") in (
            "TASK_OBSERVATION_UNAVAILABLE", "ASK_OBSERVATION_UNAVAILABLE",
        ):
            time.sleep(0.15)
            continue
        result[endpoint] = {"code": code, "body": payload}
        break
    else:
        raise RuntimeError("fresh caller cannot establish bounded readback")
print(json.dumps(result, sort_keys=True))
"""


class MainBridgeHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture = main_fixture.MainOrchestratorTest("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.main = self.fixture.main
        self.bridge = MainBridge(self.main)
        self.server = create_main_control_server(self.main)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = "http://127.0.0.1:" + str(self.server.server_port)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def get(self, url, *, body=None, method="GET", headers=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        hdrs = dict(headers or {})
        if body is not None:
            hdrs.setdefault("Content-Type", "application/json")
        request = Request(self.url + url, data=data, method=method, headers=hdrs)
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except HTTPError as err:
            try:
                return err.code, json.loads(err.read())
            finally:
                err.close()

    def send_dispatch(self, expected=None, *, headers=None, body_override=None):
        body = {"schema_version": 1,
                "expected_contract_id": expected or self.fixture.pub.contract_id}
        return self.get("/tasks/291/dispatch", body=body_override or body,
                        method="POST", headers=headers)

    def _fresh_subprocess_read(self, issue: int) -> dict:
        # No GitHub token, provider credentials, source checkout, Main,
        # TestFixture, shared session handle or ambient OPENCODE_ variables.
        # This subprocess can only observe the same Task via the loopback API.
        with tempfile.TemporaryDirectory(prefix="kagari339-fresh-reader-") as home:
            # This private process cwd/HOME is physically outside the entire
            # synthetic Main/Worktree/metadata fixture, not merely a different
            # Python object reference within the same shared directory.
            self.assertFalse(Path(home).is_relative_to(self.fixture.fixture.temp_root))
            env = {
                "HOME": home,
                "XDG_CONFIG_HOME": home,
                "XDG_DATA_HOME": home,
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            observed = subprocess.run(
                [sys.executable, "-B", "-c", _FRESH_OBSERVER,
                 str(self.server.server_port), str(issue)],
                cwd=home, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, timeout=18, check=False,
            )
        self.assertEqual(observed.returncode, 0, observed.stderr[-2000:])
        self.assertLess(len(observed.stdout), 65536)
        return json.loads(observed.stdout)

    def _parent_started_observation(self, issue: int) -> dict:
        # The parent, not the detached HTTP caller, obtains the original
        # trusted Main-observed native root Session and Ask request identity.
        for _ in range(12):
            try:
                result = self.main.observe(issue)
            except main_fixture.MainError as error:
                if error.code != "SESSION_OBSERVATION_UNAVAILABLE":
                    raise
                time.sleep(0.15)
                continue
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["kickoff"], "SENT")
            self.assertEqual(result["runtime"]["status"], "ok")
            self.assertEqual(len(result["pending"]), 1)
            return result
        self.fail("parent Main could not establish a bounded native observation")

    def test_fresh_process_after_dispatch_reads_same_task_and_pending_ask(self):
        # Only a disposable fake OpenCode server receives a kickoff. There is
        # no production agent, model request, user Answer or GitHub mutation.
        with patch.object(service, "_host_state_home",
                          return_value=self.fixture.fixture.temp_root):
            with MainSupervisor(self.main):
                receipt = self.send_dispatch()
                authoritative = self._parent_started_observation(291)
        self.assertEqual(receipt[0], 202)
        self.assertEqual(receipt[1]["prompt_state"], "SUBMITTED")
        self.assertEqual(len(self.fixture.prompts()), 1)
        # The initiating request and Main dispatch-owner lifetime have ended.
        # Two fresh, unrelated Python processes must recover the same facts.
        first = self._fresh_subprocess_read(291)
        second = self._fresh_subprocess_read(291)
        self.assertEqual(first, second)
        self.assertEqual(first["status"],
                         {"code": 200, "body": {"task": "291", "state": "BLOCKED"}})
        self.assertEqual(first["asks"]["code"], 200)
        ask_view = first["asks"]["body"]
        self.assertEqual(ask_view["task"], "291")
        self.assertEqual(ask_view["contract_id"], self.fixture.pub.contract_id)
        self.assertEqual(
            ask_view["root_session_id"],
            authoritative["runtime"]["task"]["session_id"],
        )
        self.assertEqual(len(ask_view["pending"]), 1)
        entry = ask_view["pending"][0]
        self.assertEqual(
            entry["request_id"], authoritative["pending"][0]["request_id"]
        )
        self.assertEqual(entry["kind"], "permission")
        self.assertEqual(entry["upstream"]["id"], entry["request_id"])
        self.assertEqual(entry["upstream"]["sessionID"], ask_view["root_session_id"])
        self.assertEqual(len(self.fixture.prompts()), 1,
                         "read-only fresh clients must not re-dispatch")

    def test_two_independent_fresh_processes_observe_distinct_task_asks(self):
        # The fixture's two synthetic #191 Contracts and fake OpenCode
        # instances are isolated. No actual provider turn is sent.
        self.fixture.facts[292] = {
            "repository": "acme/widgets", "number": 292, "state": "OPEN",
            "title": "Independent second Issue", "body": "Different approved Task.",
        }
        second = self.fixture.writer.create(
            task="292", branch_ref="refs/heads/issue-292",
            base_revision=self.fixture.base,
            title=self.fixture.facts[292]["title"],
            body=self.fixture.facts[292]["body"],
        )
        self.fixture.approved[292] = main_fixture.ApprovedTask(
            292, "refs/heads/issue-292", self.fixture.base, second.contract_id,
        )
        self.fixture.ports[292] = main_fixture.next_port()
        self.main = self.fixture.make_main()
        bridge = MainBridge(self.main)
        self.server.status_provider = bridge.status
        self.server.ask_provider = bridge.asks
        self.server.dispatch_provider = bridge.dispatch

        def dispatch(item):
            issue, contract = item
            return self.get(
                "/tasks/" + issue + "/dispatch", method="POST",
                body={"schema_version": 1, "expected_contract_id": contract},
            )
        with patch.object(service, "_host_state_home",
                          return_value=self.fixture.fixture.temp_root):
            with MainSupervisor(self.main):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    receipts = list(pool.map(dispatch, [
                        ("291", self.fixture.pub.contract_id),
                        ("292", second.contract_id),
                    ]))
                original = {
                    291: self._parent_started_observation(291),
                    292: self._parent_started_observation(292),
                }
        self.assertEqual([code for code, _ in receipts], [202, 202])
        self.assertEqual(len(self.fixture.prompts()), 2)

        # The dispatching client and owner are gone. New subprocesses only
        # have the HTTP port and requested Issue number, never the records.
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, later = list(pool.map(self._fresh_subprocess_read, (291, 292)))
        self.assertEqual(first["asks"]["code"], 200)
        self.assertEqual(later["asks"]["code"], 200)
        for expected, expected_contract, result in (
            (291, self.fixture.pub.contract_id, first),
            (292, second.contract_id, later),
        ):
            self.assertEqual(result["status"],
                             {"code": 200,
                              "body": {"task": str(expected), "state": "BLOCKED"}})
            self.assertEqual(result["asks"]["body"]["task"], str(expected))
            self.assertEqual(result["asks"]["body"]["contract_id"], expected_contract)
            self.assertEqual(len(result["asks"]["body"]["pending"]), 1)
            # The new process cannot choose or infer the expected binding:
            # compare against Main's source observation captured beforehand.
            self.assertEqual(
                result["asks"]["body"]["root_session_id"],
                original[expected]["runtime"]["task"]["session_id"],
            )
            self.assertEqual(
                result["asks"]["body"]["pending"][0]["request_id"],
                original[expected]["pending"][0]["request_id"],
            )
        self.assertNotEqual(
            first["asks"]["body"]["root_session_id"],
            later["asks"]["body"]["root_session_id"],
        )
        self.assertNotEqual(
            first["asks"]["body"]["pending"][0]["request_id"],
            later["asks"]["body"]["pending"][0]["request_id"],
        )
        self.assertEqual(len(self.fixture.prompts()), 2)

    def test_fresh_process_cannot_infer_missing_ask_is_empty_success(self):
        observed = self._fresh_subprocess_read(291)
        self.assertEqual(observed["status"],
                         {"code": 200, "body": {"task": "291", "state": "UNKNOWN"}})
        self.assertEqual(observed["asks"],
                         {"code": 409,
                          "body": {"error_code": "ASK_SESSION_NOT_STARTED"}})
        self.assertFalse(self.fixture.runtime.exists())
        self.assertEqual(self.fixture.prompts(), [])

    def test_main_observe_before_start_does_not_report_completed(self):
        self.assertEqual(self.get("/tasks/291/status"),
                         (200, {"task": "291", "state": "UNKNOWN"}))
        self.assertFalse(self.fixture.runtime.exists())

    def test_dispatch_without_live_main_owner_is_denied(self):
        code, body = self.send_dispatch()
        self.assertEqual(code, 409)
        self.assertEqual(body["error_code"], "MAIN_OWNER_REQUIRED")
        self.assertEqual(self.fixture.prompts(), [])

    def test_wrong_contract_rejected_without_launch(self):
        code, body = self.send_dispatch(expected="f" * 64)
        self.assertEqual((code, body["error_code"]),
                         (409, "TASK_CONTRACT_MISMATCH"))
        self.assertEqual(self.fixture.prompts(), [])

    def test_http_dispatch_and_duplicate_reuse_one_native_turn(self):
        with patch.object(service, "_host_state_home",
                          return_value=self.fixture.fixture.temp_root):
            with MainSupervisor(self.main):
                first = self.send_dispatch()
                second = self.send_dispatch()
                observed = self.get("/tasks/291/status")
        self.assertEqual(first[0], 202)
        self.assertEqual(first[1]["prompt_state"], "SUBMITTED")
        self.assertEqual(second[0], 200)
        self.assertEqual(second[1]["prompt_state"], "ALREADY_DISPATCHED")
        self.assertEqual(first[1]["contract_id"], self.fixture.pub.contract_id)
        self.assertEqual(len(self.fixture.prompts()), 1)
        self.assertEqual(observed, (200, {"task": "291", "state": "BLOCKED"}))

    def test_parallel_duplicate_http_dispatch_serializes(self):
        with patch.object(service, "_host_state_home",
                          return_value=self.fixture.fixture.temp_root):
            with MainSupervisor(self.main):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda _i: self.send_dispatch(), range(2)))
        self.assertEqual(sorted(r[0] for r in results), [200, 202])
        self.assertEqual(len(self.fixture.prompts()), 1)

    def test_cross_origin_and_malformed_dispatch_rejected(self):
        code, body = self.send_dispatch(headers={"Origin": "https://example.invalid"})
        self.assertEqual((code, body["error_code"]), (403, "NONLOCAL_HTTP_ORIGIN"))
        self.assertEqual(self.get("/tasks/291/status",
                                  headers={"Host": "example.invalid"})[0], 403)
        self.assertEqual(self.send_dispatch(body_override={
            "schema_version": 1, "expected_contract_id": self.fixture.pub.contract_id,
            "authorize": True,
        })[0], 400)
        self.assertEqual(self.fixture.prompts(), [])

    def test_foreign_task_not_accepted(self):
        code, body = self.get("/tasks/292/dispatch", method="POST",
                              body={"schema_version": 1,
                                    "expected_contract_id": self.fixture.pub.contract_id})
        self.assertEqual((code, body["error_code"]), (409, "TASK_NOT_APPROVED"))
        self.assertEqual(self.get("/tasks/292/status")[0], 503)
        self.assertEqual(self.fixture.prompts(), [])


    def test_unconfirmed_main_outcome_is_not_accepted(self):
        from tools.kagari_vnext_main import MainError
        with patch.object(self.main, "dispatch",
                          side_effect=MainError("PROMPT_OUTCOME_UNCERTAIN")) as called:
            status, body = self.send_dispatch()
        self.assertEqual(status, 503)
        self.assertEqual(body["error_code"], "PROMPT_OUTCOME_UNCERTAIN")
        called.assert_called_once_with(291)

    def test_invalid_dispatch_receipt_cannot_be_acknowledged(self):
        with patch.object(self.main, "dispatch",
                          return_value={"status": "ok", "issue": 291,
                                        "repository": "acme/widgets",
                                        "contract_id": self.fixture.pub.contract_id,
                                        "prompt_state": "UNVERIFIED"}) as called:
            status, body = self.send_dispatch()
        self.assertEqual(status, 503)
        self.assertEqual(body["error_code"], "DISPATCH_RESULT_UNVERIFIED")
        called.assert_called_once_with(291)

    def test_idle_session_does_not_imply_completed_task(self):
        view = self.main.observe(291)
        view["kickoff"] = "SENT"
        view["runtime"] = {
            "status": "ok", "task": {
                "issue": 291, "repository": "acme/widgets",
                "contract_sha256": self.fixture.pub.contract_id},
            "session_state": "idle", "pending_asks": 0,
        }
        with patch.object(self.main, "observe", return_value=view):
            self.assertEqual(self.get("/tasks/291/status"),
                             (200, {"task": "291", "state": "UNKNOWN"}))

    def test_reject_duplicate_json_keys_without_dispatch(self):
        raw = ('{"schema_version":1,"schema_version":1,'
               '"expected_contract_id":"' + self.fixture.pub.contract_id + '"}').encode()
        req = Request(self.url + "/tasks/291/dispatch",
                      data=raw, method="POST",
                      headers={"Content-Type": "application/json"})
        with patch.object(self.main, "dispatch") as called:
            with self.assertRaises(HTTPError) as response:
                build_opener(ProxyHandler({})).open(req, timeout=3)
            self.assertEqual(response.exception.code, 400)
            response.exception.close()
        called.assert_not_called()


    def test_asks_before_task_start_are_not_reported_as_empty_success(self):
        code, result = self.get("/tasks/291/asks")
        self.assertEqual((code, result["error_code"]), (409, "ASK_SESSION_NOT_STARTED"))
        self.assertEqual(self.get("/tasks/292/asks")[0], 409)

    def test_native_pending_ask_is_read_only_and_task_bound(self):
        with patch.object(service, "_host_state_home",
                          return_value=self.fixture.fixture.temp_root):
            with MainSupervisor(self.main):
                self.assertEqual(self.send_dispatch()[0], 202)
                def observe_with_bounded_read_retry():
                    for _ in range(5):
                        result = self.get("/tasks/291/asks")
                        if result[0] == 200:
                            return result
                        if result != (503, {"error_code": "ASK_OBSERVATION_UNAVAILABLE"}):
                            return result
                        # Retry only a factual read; never re-dispatch a model turn.
                        time.sleep(0.15)
                    return result

                first = observe_with_bounded_read_retry()
                second = observe_with_bounded_read_retry()
        self.assertEqual(first, second)
        code, view = first
        self.assertEqual(code, 200)
        self.assertEqual(view["task"], "291")
        self.assertEqual(view["contract_id"], self.fixture.pub.contract_id)
        self.assertEqual(len(view["pending"]), 1)
        ask = view["pending"][0]
        self.assertEqual(ask["kind"], "permission")
        self.assertTrue(ask["request_id"].startswith("per_"))
        self.assertEqual(ask["upstream"]["id"], ask["request_id"])
        self.assertEqual(ask["upstream"]["sessionID"], view["root_session_id"])
        self.assertEqual(ask["upstream"]["permission"], "bash")
        self.assertEqual(self.get("/tasks/291/asks", method="POST")[0], 405)
        self.assertEqual(len(self.fixture.prompts()), 1)

    def test_foreign_ask_session_fails_closed(self):
        with patch.object(service, "_host_state_home",
                          return_value=self.fixture.fixture.temp_root):
            with MainSupervisor(self.main):
                self.assertEqual(self.send_dispatch()[0], 202)
                forged = self.main.observe(291)
        forged["pending"][0]["upstream"]["sessionID"] = "ses_foreign12345678"
        with patch.object(self.main, "observe", return_value=forged):
            code, result = self.get("/tasks/291/asks")
        self.assertEqual((code, result["error_code"]),
                         (503, "ASK_BINDING_MISMATCH"))

    def test_parallel_two_issues_have_distinct_live_worker_sessions(self):
        self.fixture.facts[292] = {
            "repository": "acme/widgets", "number": 292, "state": "OPEN",
            "title": "Independent second Issue", "body": "Different approved Task."
        }
        second = self.fixture.writer.create(
            task="292", branch_ref="refs/heads/issue-292",
            base_revision=self.fixture.base,
            title=self.fixture.facts[292]["title"],
            body=self.fixture.facts[292]["body"])
        self.fixture.approved[292] = main_fixture.ApprovedTask(
            292, "refs/heads/issue-292", self.fixture.base, second.contract_id)
        self.fixture.ports[292] = main_fixture.next_port()
        self.main = self.fixture.make_main()
        bridge = MainBridge(self.main)
        self.server.status_provider = bridge.status
        self.server.dispatch_provider = bridge.dispatch
        self.server.ask_provider = bridge.asks

        def start(item):
            issue, contract = item
            return self.get(
                "/tasks/" + issue + "/dispatch",
                method="POST",
                body={"schema_version": 1, "expected_contract_id": contract})

        with patch.object(service, "_host_state_home",
                          return_value=self.fixture.fixture.temp_root):
            with MainSupervisor(self.main):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    receipts = list(pool.map(start, [
                        ("291", self.fixture.pub.contract_id),
                        ("292", second.contract_id)]))
                asks_a = self.get("/tasks/291/asks")
                asks_b = self.get("/tasks/292/asks")
                states = [self.get("/tasks/" + issue + "/status")
                          for issue in ("291", "292")]
                with ThreadPoolExecutor(max_workers=2) as pool:
                    repeated = list(pool.map(start, [
                        ("291", self.fixture.pub.contract_id),
                        ("292", second.contract_id)]))

        self.assertEqual([code for code, _ in receipts], [202, 202])
        self.assertEqual([code for code, _ in repeated], [200, 200])
        self.assertEqual(len(self.fixture.prompts()), 2)
        self.assertEqual(len({item["cwd"] for item in self.fixture.prompts()}), 2)
        self.assertEqual([code for code, _ in (asks_a, asks_b)], [200, 200])
        self.assertNotEqual(asks_a[1]["root_session_id"], asks_b[1]["root_session_id"])
        self.assertEqual([len(v["pending"]) for _, v in (asks_a, asks_b)], [1, 1])
        self.assertEqual([body["state"] for _, body in states],
                         ["BLOCKED", "BLOCKED"])


    def test_question_ask_preserves_options_without_reply(self):
        with patch.object(service, "_host_state_home",
                          return_value=self.fixture.fixture.temp_root):
            with MainSupervisor(self.main):
                self.assertEqual(self.send_dispatch()[0], 202)
                observed = self.main.observe(291)
        native = {
            "id": "que_KAGARI339Question12345",
            "sessionID": observed["runtime"]["task"]["session_id"],
            "questions": [{
                "question": "Choose approved approach",
                "multiple": False,
                "custom": False,
                "options": [{"label": "A"}, {"label": "B"}],
            }],
        }
        existing = observed["pending"][0]
        observed["pending"] = [{
            "kind": "question", "request_id": native["id"],
            "task": existing["task"], "upstream": native,
        }]
        with patch.object(self.main, "observe", return_value=observed):
            status, body = self.get("/tasks/291/asks")
        self.assertEqual(status, 200)
        self.assertEqual(body["pending"][0]["kind"], "question")
        self.assertEqual(body["pending"][0]["upstream"]["questions"],
                         native["questions"])
        self.assertEqual(len(self.fixture.prompts()), 1)


    def test_only_post_can_dispatch_even_with_valid_contract(self):
        request = {
            "schema_version": 1,
            "expected_contract_id": self.fixture.pub.contract_id,
        }
        with patch.object(self.main, "dispatch") as called:
            for method in ("PUT", "PATCH", "DELETE"):
                with self.subTest(method=method):
                    code, body = self.get(
                        "/tasks/291/dispatch", body=request, method=method)
                    self.assertEqual((code, body["error_code"]),
                                     (405, "MUTATION_NOT_IMPLEMENTED"))
        called.assert_not_called()


    def test_http_caller_disconnect_reuses_main_receipt_without_resend(self):
        import socket
        import time

        contract = self.fixture.pub.contract_id
        payload = json.dumps({
            "schema_version": 1, "expected_contract_id": contract,
        }).encode("utf-8")
        address = ("127.0.0.1", self.server.server_port)
        raw = (
            "POST /tasks/291/dispatch HTTP/1.1\r\n"
            + "Host: 127.0.0.1:" + str(address[1]) + "\r\n"
            + "Content-Type: application/json\r\n"
            + "Content-Length: " + str(len(payload)) + "\r\n"
            + "Connection: close\r\n\r\n"
        ).encode("ascii") + payload

        with patch.object(service, "_host_state_home",
                          return_value=self.fixture.fixture.temp_root):
            with MainSupervisor(self.main):
                with socket.create_connection(address, timeout=3) as stream:
                    stream.sendall(raw)
                    # End the caller's connection without reading the ACK.
                    stream.shutdown(socket.SHUT_RDWR)
                receipt = self.fixture.runtime / "issue-291" / "prompt.sent.json"
                deadline = time.monotonic() + 15
                while not receipt.is_file() and time.monotonic() < deadline:
                    time.sleep(0.1)
                self.assertTrue(receipt.is_file(), "Main did not persist ACK")
                second = self.send_dispatch()
        self.assertEqual(second[0], 200)
        self.assertEqual(second[1]["prompt_state"], "ALREADY_DISPATCHED")
        self.assertEqual(len(self.fixture.prompts()), 1)


    def test_decoder_recursion_failure_rejected_without_dispatch(self):
        # The 2 KiB limit does not currently reproduce native json.loads
        # recursion failure on Python 3.14. Simulate that rare decoder error
        # to verify the HTTP exception boundary explicitly.
        raw = b"[" * 1000 + b"0" + b"]" * 1000
        request = Request(
            self.url + "/tasks/291/dispatch", data=raw, method="POST",
            headers={"Content-Type": "application/json"},
        )
        decoder = json.loads

        def fail_on_nested(data, *args, **kwargs):
            if data == raw.decode("utf-8") and "object_pairs_hook" in kwargs:
                raise RecursionError("simulated JSON decoder limit")
            return decoder(data, *args, **kwargs)

        with patch.object(self.main, "dispatch") as called:
            with patch("tools.kagari_loopback_control.json.loads",
                       side_effect=fail_on_nested):
                with self.assertRaises(HTTPError) as response:
                    build_opener(ProxyHandler({})).open(request, timeout=3)
                self.assertEqual(response.exception.code, 400)
                self.assertEqual(decoder(response.exception.read()),
                                 {"error_code": "DISPATCH_REQUEST_INVALID"})
                response.exception.close()
        called.assert_not_called()

    def test_noncanonical_or_duplicate_content_length_rejected(self):
        import http.client
        import socket
        body = json.dumps({"schema_version": 1,
                           "expected_contract_id": self.fixture.pub.contract_id}).encode()
        for label, length_headers in (
            ("plus", "+%d" % len(body)),
            ("leading_zero", "0%d" % len(body)),
            ("duplicate", "%d\r\nContent-Length: %d" % (len(body), len(body))),
            ("oversized_digits", "9" * 5000),
        ):
            with self.subTest(case=label):
                headers = (
                    "POST /tasks/291/dispatch HTTP/1.1\r\n"
                    "Host: 127.0.0.1:%d\r\n"
                    "Content-Type: application/json\r\n"
                    "Content-Length: %s\r\nConnection: close\r\n\r\n"
                ) % (self.server.server_port, length_headers)
                with socket.create_connection(
                    ("127.0.0.1", self.server.server_port), timeout=3
                ) as stream:
                    stream.sendall(headers.encode("ascii") + body)
                    response = http.client.HTTPResponse(stream)
                    response.begin()
                    self.assertEqual(response.status, 400)
                    self.assertEqual(json.loads(response.read()),
                                     {"error_code": "DISPATCH_REQUEST_INVALID"})
                    response.close()
        self.assertEqual(self.fixture.prompts(), [])

    def test_forged_adapter_issue_or_repository_not_acknowledged(self):
        accepted = {
            "task": "291", "issue": 291, "repository": "acme/widgets",
            "contract_id": self.fixture.pub.contract_id,
            "status": "accepted", "prompt_state": "SUBMITTED",
        }
        original = self.server.dispatch_provider
        try:
            for replacement in (
                {"issue": 292}, {"repository": "other/project"},
            ):
                with self.subTest(replacement=replacement):
                    self.server.dispatch_provider = (
                        lambda task, contract, changes=replacement:
                            {**accepted, **changes}
                    )
                    status, result = self.send_dispatch()
                    self.assertEqual((status, result["error_code"]),
                                     (503, "DISPATCH_RECEIPT_UNVERIFIED"))
        finally:
            self.server.dispatch_provider = original
        self.assertEqual(self.fixture.prompts(), [])


if __name__ == "__main__":
    unittest.main()
