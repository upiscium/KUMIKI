"""#339 opt-in localhost API source-only transport tests."""
from concurrent.futures import ThreadPoolExecutor
import json
import socket
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from tools.kagari_loopback_control import (
    ControlTransportError, create_control_server,
)


class LoopbackControlTests(unittest.TestCase):
    def setUp(self):
        self.provider = lambda task: {"task": task, "state": "RUNNING"}
        self.server = create_control_server(lambda task: self.provider(task), max_inflight=2)
        self.worker = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.worker.start()
        self.addCleanup(self.stop)
        self.base_url = "http://127.0.0.1:" + str(self.server.server_port)
        self.client = build_opener(ProxyHandler({}))

    def stop(self):
        if getattr(self, "_stopped", False):
            return
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=3)
        self._stopped = True

    def fetch(self, path, method="GET"):
        req = Request(self.base_url + path, method=method)
        try:
            with self.client.open(req, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read())
            finally:
                error.close()

    def test_listener_is_ipv4_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        for name in ("0.0.0.0", "::", "::1", "localhost", "192.0.2.1"):
            with self.subTest(host=name), self.assertRaisesRegex(
                ControlTransportError, "NON_LOOPBACK_BIND_FORBIDDEN"
            ):
                create_control_server(lambda task: {}, host=name)

    def test_health_without_auth(self):
        self.assertEqual(self.fetch("/health"), (200, {
            "service": "kagari-control", "status": "ok",
            "operation_authority": "none",
        }))

    def test_unencodable_ask_is_503_not_disconnected(self):
        self.server.ask_provider = lambda task: {
            "task": task, "contract_id": "a" * 64,
            "root_session_id": "ses_readonly12345678",
            "pending": [{"upstream": {"question": "\ud800"}}],
        }
        self.assertEqual(
            self.fetch("/tasks/317/asks"),
            (503, {"error_code": "ASK_OBSERVATION_UNVERIFIED"}),
        )

    def test_task_status_is_provider_owned(self):
        self.provider = lambda task: {
            "task": task, "state": "BLOCKED", "opaque_secret": "not-shared"
        }
        self.assertEqual(self.fetch("/tasks/317/status"),
                         (200, {"task": "317", "state": "BLOCKED"}))

    def test_unknown_task_and_malformed_path_fail_closed(self):
        self.provider = lambda task: {"task": "318", "state": "RUNNING"}
        self.assertEqual(self.fetch("/tasks/317/status")[0], 503)
        for path in ("/tasks/0317/status", "/tasks/1/../2/status",
                     "/tasks/1/status?other=2", "/unknown"):
            with self.subTest(path=path):
                self.assertEqual(self.fetch(path)[0], 404)

    def test_unhashable_or_malformed_state_is_json_503(self):
        for bad in ([], {}, None, 42, True):
            with self.subTest(state=bad):
                self.provider = lambda task, observed=bad: {
                    "task": task, "state": observed,
                }
                self.assertEqual(
                    self.fetch("/tasks/317/status"),
                    (503, {"error_code": "UNVERIFIED_TASK_STATUS"}),
                )

    def test_provider_failure_never_reports_success(self):
        def unavailable(_task):
            raise RuntimeError("metadata unavailable")
        self.provider = unavailable
        self.assertEqual(self.fetch("/tasks/317/status"),
                         (503, {"error_code": "TASK_OBSERVATION_UNAVAILABLE"}))

    def test_post_and_mutation_denied(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                self.assertEqual(self.fetch("/tasks/317/status", method)[0], 405)
        self.assertEqual(self.fetch("/tasks/317/dispatch", "POST")[0], 405)

    def test_independent_task_requests_can_run_concurrently(self):
        started = threading.Event()
        release = threading.Event()

        def bounded_status(task):
            if task == "1":
                started.set()
                if not release.wait(timeout=2.5):
                    raise RuntimeError("slow task was not released")
            return {"task": task, "state": "RUNNING"}

        self.provider = bounded_status
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                slow = pool.submit(self.fetch, "/tasks/1/status")
                self.assertTrue(started.wait(2))
                fast = pool.submit(self.fetch, "/tasks/2/status")
                self.assertEqual(fast.result(timeout=2),
                                 (200, {"task": "2", "state": "RUNNING"}))
                release.set()
                self.assertEqual(slow.result(timeout=2)[0], 200)
        finally:
            release.set()

    def test_capacity_rejects_third_request_without_invoking_provider(self):
        entered = {i: threading.Event() for i in ("1", "2")}
        release = threading.Event()
        seen = []

        def blocked(task):
            seen.append(task)
            if task in entered:
                entered[task].set()
                if not release.wait(timeout=3):
                    raise RuntimeError("fixture request timed out")
            return {"task": task, "state": "RUNNING"}

        self.provider = blocked
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.fetch, "/tasks/1/status")
                second = pool.submit(self.fetch, "/tasks/2/status")
                self.assertTrue(all(flag.wait(2) for flag in entered.values()))
                self.assertEqual(
                    self.fetch("/tasks/3/status"),
                    (503, {"error_code": "SERVER_BUSY"}),
                )
                self.assertEqual(seen, ["1", "2"] if seen[0] == "1" else ["2", "1"])
                release.set()
                self.assertEqual(first.result(timeout=3)[0], 200)
                self.assertEqual(second.result(timeout=3)[0], 200)
        finally:
            release.set()
        self.assertEqual(self.fetch("/health")[0], 200)

    def test_graceful_close_waits_for_inflight_request(self):
        entered = threading.Event()
        release = threading.Event()
        closed = threading.Event()

        def blocked(task):
            entered.set()
            if not release.wait(timeout=3):
                raise RuntimeError("fixture request timed out")
            return {"task": task, "state": "RUNNING"}

        self.provider = blocked
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.fetch, "/tasks/1/status")
            self.assertTrue(entered.wait(2))
            closer = threading.Thread(target=lambda: (self.stop(), closed.set()))
            closer.start()
            try:
                self.assertFalse(closed.wait(0.15), "close discarded a live request")
            finally:
                release.set()
            self.assertEqual(first.result(timeout=3)[0], 200)
            self.assertTrue(closed.wait(3), "close failed to join live request")
            closer.join(timeout=3)

    def test_dispatch_requires_host_repository_binding(self):
        with self.assertRaisesRegex(ControlTransportError,
                                    "DISPATCH_REPOSITORY_REQUIRED"):
            create_control_server(
                lambda task: {}, dispatch_provider=lambda task, contract: {},
            )

    def test_invalid_concurrency_settings_rejected(self):
        for n in (0, -1, True, 65, "2"):
            with self.subTest(max_inflight=n):
                with self.assertRaisesRegex(ControlTransportError, "INVALID_MAX_INFLIGHT"):
                    create_control_server(lambda task: {}, max_inflight=n)

    def test_abandoned_partial_headers_release_worker_capacity(self):
        self.server.request_timeout_seconds = 0.2
        sockets = []
        try:
            for _ in range(2):
                sock = socket.create_connection(self.server.server_address, timeout=2)
                sock.sendall(b"GET /health HTTP/1.1\r\nHost: ")
                sockets.append(sock)
            time.sleep(0.65)
            # Both half-written local requests must eventually give back slots.
            self.assertEqual(self.fetch("/health")[0], 200)
        finally:
            for sock in sockets:
                sock.close()


if __name__ == "__main__":
    unittest.main()
