"""#339 R11: opt-in HTTP host lifetime composes existing #256/#291 owners.

Disposable real Git/#191/#194 fixture; FakeGitHub and fake OpenCode only.
Inert external Attach package. No real model, public listener or production
Task/credentials, GitHub writes, background service or release.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import linecache
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

from tests import test_kagari_vnext_main_host as host_fixture
from tools import kagari_vnext_attach as attach
from tools import kagari_loopback_main_bridge as bridge_module
from tools import kagari_vnext_main_service as service
from tools.kagari_vnext_main import MainError
from tools.kagari_loopback_main_bridge import (MainBridgeError, attached_main_control_server)
from tools.kagari_loopback_control import ControlTransportError


class AttachedLoopbackTests(unittest.TestCase):
    def setUp(self):
        self.fixture = host_fixture.MainHostTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.source.project
        self.main = service.build_main(self.fixture.plan, host=self.fixture.host())
        root = self.fixture.source.fixture.temp_root
        self.home = root / "r11-attach-state"
        self.home.mkdir(mode=0o700)
        self.package = root / "r11-inert-package"
        (self.package / "bin").mkdir(parents=True)
        for name in attach.REQUIRED_BINARIES:
            path = self.package / "bin" / name
            path.write_bytes(b"#!/bin/sh\nexit 97 # must never execute\n")
            path.chmod(0o555)
        self.package.chmod(0o555)
        (self.package / "bin").chmod(0o555)
        self.addCleanup(self.thaw_package)
        self.config = service.MainAttachmentConfig(self.home, self.package)
        self.http = build_opener(ProxyHandler({}))
        self.contract = self.fixture.source.pub.contract_id

    def thaw_package(self):
        self.package.chmod(0o755)
        (self.package / "bin").chmod(0o755)

    def install(self):
        result = attach.operate(
            self.project, self.home, self.package, "install", attach.PROFILE)
        self.assertEqual(result["status"], "ATTACHED")

    def request(self, server, path, *, body=None, timeout=24):
        url = "http://127.0.0.1:" + str(server.server_port) + path
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(url, data=data, method="POST" if body is not None else "GET",
                      headers={"Content-Type": "application/json"} if data else {})
        try:
            with self.http.open(req, timeout=timeout) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read())
            finally:
                error.close()

    def dispatch(self, server, contract):
        # The disposable real #194 PR-first fixture validates Metadata
        # history synchronously. This larger test timeout is NOT a production
        # short-ACK guarantee; its actual duration is recorded separately.
        return self.request(server, "/tasks/291/dispatch", body={
            "schema_version": 1, "expected_contract_id": contract}, timeout=90)

    def test_absent_attachment_blocks_before_any_listener_or_task(self):
        with patch(
            "tools.kagari_loopback_main_bridge.create_main_control_server"
        ) as make_listener:
            with self.assertRaises(MainError) as caught:
                with attached_main_control_server(self.main, self.config):
                    self.fail("Absent Attach cannot start the listener")
            self.assertEqual(caught.exception.code, "MAIN_ATTACHMENT_UNVERIFIED")
            make_listener.assert_not_called()
        self.assertEqual(self.fixture.source.prompts(), [])
        self.assertFalse(self.fixture.source.runtime.exists())
        self.assertFalse((self.home / attach.STATE_DIR).exists())

    def test_attached_listener_is_explicit_read_only_until_http_dispatch(self):
        self.install()
        with attached_main_control_server(self.main, self.config) as server:
            port = server.server_port
            self.assertEqual(server.server_address[0], "127.0.0.1")
            self.assertEqual(self.request(server, "/health"), (200, {
                "operation_authority": "none",
                "service": "kagari-control", "status": "ok",
            }))
            self.assertEqual(self.request(server, "/tasks/291/status"), (
                200, {"state": "UNKNOWN", "task": "291"}))
            self.assertEqual(self.request(server, "/tasks/291/asks"), (
                409, {"error_code": "ASK_SESSION_NOT_STARTED"}))
            with self.assertRaises(MainError) as caught:
                with service.MainSupervisor(self.fixture.source.make_main()):
                    self.fail("Second Main cannot own this repository")
            self.assertEqual(caught.exception.code, "MAIN_ALREADY_ACTIVE")
            self.assertEqual(self.fixture.source.prompts(), [])
            self.assertEqual(self.fixture.pushes, [])
            self.assertEqual(self.fixture.github.pulls, [])
        with self.assertRaises(OSError):
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                self.fail("Listener remained reachable after Host lease release")
        self.assertEqual(attach.operate(
            self.project, self.home, self.package, "doctor", attach.PROFILE
        )["status"], "READY")
        with service.MainSupervisor(self.fixture.source.make_main()):
            pass

    def test_attachment_does_not_invent_missing_host_publisher(self):
        self.install()
        # The installed Attach is not the #194 operation grant.
        self.main.initial_collaboration_for_issue = None
        with attached_main_control_server(self.main, self.config) as server:
            self.assertEqual(self.dispatch(server, self.contract), (
                409, {"error_code": "MAIN_INITIAL_COLLABORATION_REQUIRED"}))
            self.assertEqual(self.fixture.source.prompts(), [])
            self.assertEqual(self.fixture.github.pulls, [])
            self.assertEqual(self.fixture.pushes, [])

    def test_invalid_binding_or_port_releases_original_owner(self):
        with self.assertRaises(MainBridgeError) as bad:
            with attached_main_control_server(self.main, None):
                self.fail("Non-host attachment accepted")
        self.assertEqual(bad.exception.code, "MAIN_ATTACHMENT_BINDING_INVALID")
        self.install()
        with self.assertRaises(ControlTransportError) as invalid:
            with attached_main_control_server(self.main, self.config, port=70000):
                self.fail("Invalid listener port accepted")
        self.assertEqual(str(invalid.exception), "INVALID_PORT")
        # Failure to construct a listener does not retain a Main/Attach lease.
        with service.MainSupervisor(self.fixture.source.make_main()):
            pass
        self.assertEqual(self.fixture.source.prompts(), [])

    def test_host_bound_http_dispatch_reuses_actual_pr_first_receipt(self):
        self.install()
        # Verify the exact disposable executable before any Task effect.
        self.assertEqual(self.main.opencode_bin, str(self.fixture.source.bin))
        version = subprocess.run(
            [self.main.opencode_bin, "--version"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=8, check=False)
        self.assertEqual((version.returncode, version.stdout.strip()),
                         (0, "1.18.35"), version.stderr)
        with attached_main_control_server(self.main, self.config) as server:
            self.assertEqual(self.dispatch(server, "f" * 64), (
                409, {"error_code": "TASK_CONTRACT_MISMATCH"}))
            foreign = self.request(server, "/tasks/292/dispatch", body={
                "schema_version": 1, "expected_contract_id": self.contract})
            self.assertEqual(foreign, (
                409, {"error_code": "TASK_NOT_APPROVED"}))
            first = self.dispatch(server, self.contract)
            again = self.dispatch(server, self.contract)
            self.assertEqual(first[0], 202, first)
            self.assertEqual(first[1]["prompt_state"], "SUBMITTED")
            self.assertEqual(again[0], 200, again)
            self.assertEqual(again[1]["prompt_state"], "ALREADY_DISPATCHED")
            self.assertEqual(len(self.fixture.source.prompts()), 1)
            self.assertEqual(len(self.fixture.github.pulls), 1)
            self.assertEqual(len(self.fixture.pushes), 1)
            observed = None
            for _ in range(12):
                observed = self.request(server, "/tasks/291/asks")
                if observed[0] == 200:
                    break
                self.assertEqual(observed[0], 503, observed)
                time.sleep(0.15)
            self.assertEqual(observed[0], 200, observed)
            self.assertEqual(observed[1]["contract_id"], self.contract)
            self.assertEqual(len(observed[1]["pending"]), 1)
            self.assertEqual(observed[1]["pending"][0]["kind"], "permission")
        self.assertEqual(len(self.fixture.source.prompts()), 1)

    def test_attached_effect_checks_refuse_drift_without_dispatch(self):
        self.install()
        ignore = self.project / ".gitignore"
        before = ignore.read_bytes()
        with attached_main_control_server(self.main, self.config) as server:
            try:
                ignore.write_bytes(b"/.worktrees/\n!/.worktrees/\n")
                result = self.dispatch(server, self.contract)
                self.assertEqual(result, (
                    503, {"error_code": "MAIN_ATTACHMENT_UNVERIFIED"}))
                self.assertEqual(self.fixture.source.prompts(), [])
                self.assertEqual(self.fixture.github.pulls, [])
            finally:
                ignore.write_bytes(before)
        self.assertEqual(attach.operate(
            self.project, self.home, self.package, "doctor", attach.PROFILE
        )["status"], "READY")

    def test_listener_drains_inflight_read_before_lease_release(self):
        self.install()
        entered, release, completed = (
            threading.Event(), threading.Event(), threading.Event())
        with ThreadPoolExecutor(max_workers=1) as pool:
            with attached_main_control_server(self.main, self.config) as server:
                original = server.status_provider
                def blocked(task):
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError("test callback was never released")
                    completed.set()
                    return original(task)
                server.status_provider = blocked
                future = pool.submit(self.request, server, "/tasks/291/status")
                self.assertTrue(entered.wait(4))
                # Exit starts shutdown while the admitted request is active;
                # only this test-controlled event completes the provider.
                releaser = threading.Thread(
                    target=lambda: (time.sleep(0.25), release.set()), daemon=True)
                releaser.start()
            releaser.join(timeout=3)
            self.assertTrue(completed.is_set())
            self.assertEqual(future.result(timeout=3), (
                200, {"state": "UNKNOWN", "task": "291"}))
        self.assertEqual(self.fixture.source.prompts(), [])
        with service.MainSupervisor(self.fixture.source.make_main()):
            pass


    def test_startup_interrupt_after_real_thread_start_closes_listener(self):
        self.install()
        real_start = threading.Thread.start
        started = []

        def interrupt_after_start(worker):
            real_start(worker)
            if worker.name == "kagari-loopback":
                started.append(worker)
                raise KeyboardInterrupt("simulated SIGINT after real thread start")

        with patch.object(threading.Thread, "start", interrupt_after_start):
            with self.assertRaises(KeyboardInterrupt):
                with attached_main_control_server(self.main, self.config):
                    self.fail("startup interruption must not yield a listener")
        self.assertEqual(len(started), 1)
        self.assertFalse(started[0].is_alive())
        with service.MainSupervisor(self.fixture.source.make_main()):
            pass

    def test_shutdown_defers_sigint_only_during_semaphore_drain(self):
        self.install()
        before = signal.getsignal(signal.SIGINT)
        with attached_main_control_server(self.main, self.config) as server:
            original = server.drain_admitted_handlers
            observed = []

            def checked_drain():
                if threading.current_thread() is threading.main_thread():
                    observed.append(signal.getsignal(signal.SIGINT) is not before)
                return original()

            server.drain_admitted_handlers = checked_drain
        if threading.current_thread() is threading.main_thread():
            self.assertEqual(observed, [True])
            self.assertIs(signal.getsignal(signal.SIGINT), before)
        with service.MainSupervisor(self.fixture.source.make_main()):
            pass

    def test_sigint_during_shutdown_waits_until_socket_is_closed(self):
        self.install()
        interrupted = []
        with self.assertRaises(KeyboardInterrupt):
            with attached_main_control_server(self.main, self.config) as server:
                port = server.server_port
                shutdown = server.shutdown

                def interrupted_once():
                    interrupted.append(True)
                    if len(interrupted) == 1:
                        raise KeyboardInterrupt("simulated host SIGINT")
                    return shutdown()

                server.shutdown = interrupted_once
        self.assertEqual(len(interrupted), 2)
        with self.assertRaises(OSError):
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                self.fail("SIGINT left a reachable Listener without Host ownership")
        with service.MainSupervisor(self.fixture.source.make_main()):
            pass
        self.assertEqual(self.fixture.source.prompts(), [])

    def test_sigint_during_handler_drain_defers_owner_release(self):
        self.install()
        entered, release, finished = (
            threading.Event(), threading.Event(), threading.Event())
        attempts = []
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(KeyboardInterrupt):
                with attached_main_control_server(self.main, self.config) as server:
                    port = server.server_port
                    # Simulate actual CPython ThreadingMixIn behavior:
                    # pop_all removes live request threads from its tracking
                    # collection BEFORE joining. SIGINT at that point means
                    # calling server_close again cannot find those threads.
                    original = server.status_provider

                    def held(task):
                        entered.set()
                        if not release.wait(5):
                            raise RuntimeError("unreleased synthetic observer")
                        finished.set()
                        return original(task)

                    server.status_provider = held
                    result = pool.submit(
                        self.request, server, "/tasks/291/status")
                    self.assertTrue(entered.wait(4))
                    # Capture the actual per-server _Threads instance after
                    # process_request created it; simulate interruption after
                    # its pop_all() has consumed the tracked live handler.
                    original_join = server._threads.join

                    def interrupted_join():
                        attempts.append(True)
                        if len(attempts) == 1:
                            owned = server._threads.pop_all()
                            self.assertTrue(owned, "expected admitted handler")
                            raise KeyboardInterrupt("SIGINT inside real handler join")
                        return original_join()

                    server._threads.join = interrupted_join
                    releaser = threading.Thread(
                        target=lambda: (time.sleep(1.25), release.set()),
                        daemon=True)
                    releaser.start()
            self.assertTrue(
                finished.is_set(), "Host leases released before admitted handler drained")
            releaser.join(timeout=3)
            self.assertEqual(result.result(timeout=3), (
                200, {"state": "UNKNOWN", "task": "291"}))
        self.assertEqual(len(attempts), 2)
        self.assertTrue(finished.is_set())
        with self.assertRaises(OSError):
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                self.fail("SIGINT left an open Listener")
        with service.MainSupervisor(self.fixture.source.make_main()):
            pass
        self.assertEqual(self.fixture.source.prompts(), [])


    def test_real_other_thread_sigint_during_cleanup_preserves_host_lease(self):
        """Regression for Python signal callbacks despite main pthread mask.

        A child is mandatory: the old implementation may abandon a live
        non-daemon listener.  An outer timeout keeps the parent suite safe.
        """
        if not hasattr(signal, "pthread_kill"):
            self.skipTest("Linux POSIX pthread_kill required")
        command = [
            sys.executable, "-c",
            "from tests.test_kagari_loopback_attached import _real_sigint_child; "
            "_real_sigint_child()",
        ]
        try:
            child = subprocess.run(
                command, cwd=Path(__file__).resolve().parents[1],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, timeout=14, check=False,
            )
        except subprocess.TimeoutExpired:
            self.fail("SIGINT regression child failed to drain Host-owned listener")
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertIn("SIGINT_OWNER_DRAIN_PASS", child.stdout)



    def test_single_sigint_at_teardown_entry_does_not_release_owner_early(self):
        """Real SIGINT before the cleanup shield: regression for P2 review."""
        command = [
            sys.executable, "-c",
            "from tests.test_kagari_loopback_attached import "
            "_teardown_entry_sigint_child; _teardown_entry_sigint_child()",
        ]
        try:
            child = subprocess.run(
                command, cwd=Path(__file__).resolve().parents[1],
                stdin=subprocess.DEVNULL, capture_output=True,
                text=True, timeout=15, check=False,
            )
        except subprocess.TimeoutExpired:
            self.fail("unprotected teardown regression child did not stop")
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertIn("SIGINT_TRANSITION_OWNER_DRAIN_PASS", child.stdout)



    def test_real_sigint_between_bind_and_thread_creation_closes_listener(self):
        """A bound socket must be closed before the Host ownership ends."""
        command = [
            sys.executable, "-c",
            "from tests.test_kagari_loopback_attached import "
            "_initialization_sigint_child; _initialization_sigint_child()",
        ]
        try:
            child = subprocess.run(
                command, cwd=Path(__file__).resolve().parents[1],
                stdin=subprocess.DEVNULL, capture_output=True,
                text=True, timeout=15, check=False,
            )
        except subprocess.TimeoutExpired:
            self.fail("listener setup SIGINT fixture failed to shut down")
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertIn("SIGINT_INITIALIZATION_SOCKET_CLOSE_PASS", child.stdout)



    def test_thread_constructor_failure_closes_bound_listener(self):
        """A non-SIGINT construction error cannot leave a listening socket."""
        self.install()
        bound = []
        create = bridge_module.create_main_control_server

        def capture(*args, **kwargs):
            server = create(*args, **kwargs)
            bound.append(server)
            return server

        try:
            with patch(
                "tools.kagari_loopback_main_bridge.create_main_control_server",
                side_effect=capture,
            ), patch(
                "tools.kagari_loopback_main_bridge.Thread",
                side_effect=RuntimeError("THREAD_CONSTRUCTION_FAILED"),
            ):
                # Existing #291 attached_supervisor intentionally maps
                # host RuntimeError to MAIN_ATTACHMENT_UNVERIFIED.
                with self.assertRaises(MainError) as failure:
                    with attached_main_control_server(
                        self.main, self.config
                    ):
                        self.fail("thread constructor was not invoked")
                self.assertEqual(
                    failure.exception.code, "MAIN_ATTACHMENT_UNVERIFIED")
            self.assertEqual(len(bound), 1)
            self.assertEqual(bound[0].socket.fileno(), -1)
            with service.MainSupervisor(self.fixture.source.make_main()):
                pass
        finally:
            for server in bound:
                if server.socket.fileno() != -1:
                    server.server_close()


def _real_sigint_child():
    """Real POSIX signal in disposable, fixture-only child; no external Task."""
    case = AttachedLoopbackTests("runTest")
    case.setUp()
    entered, release, finished = (
        threading.Event(), threading.Event(), threading.Event())
    server = None
    sender = None
    try:
        case.install()
        real_is_alive = threading.Thread.is_alive
        delivered = []
        caught = False

        def inject_other_thread_sigint(thread):
            if thread.name == "kagari-loopback" and not delivered:
                delivered.append(True)
                assert sender is not None and sender.ident is not None
                # Unmasked receiver; Python callback still runs on main.
                signal.pthread_kill(sender.ident, signal.SIGINT)
                time.sleep(0.04)
            return real_is_alive(thread)

        with ThreadPoolExecutor(max_workers=1) as pool:
            with patch.object(threading.Thread, "is_alive",
                              inject_other_thread_sigint):
                try:
                    with attached_main_control_server(case.main, case.config) as server:
                        original_status = server.status_provider

                        def hold_status(task):
                            entered.set()
                            if not release.wait(5):
                                raise RuntimeError("test did not release HTTP callback")
                            finished.set()
                            return original_status(task)

                        server.status_provider = hold_status
                        future = pool.submit(case.request, server,
                                             "/tasks/291/status")
                        assert entered.wait(3), "HTTP callback not admitted"
                        # An unmasked thread remains alive until after SIGINT.
                        sender = threading.Thread(
                            target=lambda: (time.sleep(0.7), release.set()),
                            name="sigint-target-unmasked", daemon=False)
                        sender.start()
                except KeyboardInterrupt:
                    caught = True
            done_before_lease_release = finished.is_set()
            print("SIGINT_IN_OTHER_THREAD", delivered,
                  "CATCH", caught,
                  "HANDLER_DONE_AT_OWNER_EXIT", done_before_lease_release,
                  flush=True)
            # If old code abandoned a listener, safely drain it before
            # returning a failing child exit (do not leave non-daemon threads).
            release.set()
            if sender is not None:
                sender.join(timeout=3)
            if server is not None and server.socket.fileno() != -1:
                server.shutdown()
                server.server_close()
                server.drain_admitted_handlers()
            if server is not None:
                assert future.result(timeout=3)[0] == 200
            with service.MainSupervisor(case.fixture.source.make_main()):
                pass
            assert delivered and caught and done_before_lease_release, (
                "Host lease exited before admitted HTTP callback completed")
            print("SIGINT_OWNER_DRAIN_PASS", flush=True)
    finally:
        release.set()
        if sender is not None:
            sender.join(timeout=3)
        if server is not None and server.socket.fileno() != -1:
            server.shutdown()
            server.server_close()
            server.drain_admitted_handlers()
        case.doCleanups()



def _teardown_entry_sigint_child():
    """Disposable SIGINT trace immediately before entering teardown protection.

    Old revision released the Host lease at this boundary while an admitted
    HTTP callback was still active. The subprocess owns unconditional cleanup.
    """
    case = AttachedLoopbackTests("runTest")
    case.setUp()
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    triggered = []
    server = None
    worker = None
    caught = False
    try:
        case.install()
        code = attached_main_control_server.__wrapped__.__code__

        def transition_interrupt(frame, event, arg):
            if frame.f_code is code and event == "line" and not triggered:
                source = linecache.getline(frame.f_code.co_filename,
                                           frame.f_lineno).strip()
                if source in ("interrupted = None",
                              "_drain_before_owner_release()"):
                    triggered.append((frame.f_lineno, source))
                    sys.settrace(None)
                    signal.raise_signal(signal.SIGINT)
            return transition_interrupt

        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                with attached_main_control_server(case.main, case.config) as server:
                    observed = server.status_provider

                    def blocked(task):
                        entered.set()
                        if not release.wait(5):
                            raise RuntimeError("test callback was not released")
                        finished.set()
                        return observed(task)

                    server.status_provider = blocked
                    future = pool.submit(
                        case.request, server, "/tasks/291/status")
                    assert entered.wait(3), "No admitted HTTP callback"
                    # A correct teardown must outlive this delayed release.
                    # The old bug drops the Host lease before the delay ends.
                    worker = threading.Thread(
                        target=lambda: (time.sleep(0.8), release.set()),
                        name="release-held-callback", daemon=True)
                    worker.start()
                    sys.settrace(transition_interrupt)
            except KeyboardInterrupt:
                caught = True
            finally:
                sys.settrace(None)

            done_at_owner_exit = finished.is_set()
            print("TEARDOWN_PROLOGUE_SIGINT", triggered,
                  "CAUGHT", caught, "DONE_AT_EXIT", done_at_owner_exit,
                  flush=True)
            release.set()
            if worker is not None:
                worker.join(timeout=3)
            if server is not None and server.socket.fileno() != -1:
                server.shutdown()
                server.server_close()
                server.drain_admitted_handlers()
            assert future.result(timeout=3)[0] == 200
            with service.MainSupervisor(case.fixture.source.make_main()):
                pass
            assert triggered and caught and done_at_owner_exit, (
                "Host lease released before admitted callback completed")
            print("SIGINT_TRANSITION_OWNER_DRAIN_PASS", flush=True)
    finally:
        sys.settrace(None)
        release.set()
        if server is not None and server.socket.fileno() != -1:
            server.shutdown()
            server.server_close()
            server.drain_admitted_handlers()
        case.doCleanups()



def _initialization_sigint_child():
    """Real single-SIGINT trace after loopback bind, before Thread creation."""
    case = AttachedLoopbackTests("runTest")
    case.setUp()
    bound = []
    fired = []
    caught = False
    code = attached_main_control_server.__wrapped__.__code__
    create = bridge_module.create_main_control_server

    def track_creation(*args, **kwargs):
        server = create(*args, **kwargs)
        bound.append(server)
        return server

    def intercept_after_bind(frame, event, arg):
        if frame.f_code is code and event == "line" and not fired:
            line = linecache.getline(
                frame.f_code.co_filename, frame.f_lineno).strip()
            if line.startswith(("thread = Thread(target=server.serve_forever,",
                                "thread = Thread(")):
                fired.append((frame.f_lineno, line))
                sys.settrace(None)
                signal.raise_signal(signal.SIGINT)
        return intercept_after_bind

    try:
        case.install()
        with patch(
            "tools.kagari_loopback_main_bridge.create_main_control_server",
            side_effect=track_creation
        ):
            try:
                sys.settrace(intercept_after_bind)
                with attached_main_control_server(case.main, case.config):
                    raise AssertionError("one SIGINT must prevent normal yield")
            except KeyboardInterrupt:
                caught = True
            finally:
                sys.settrace(None)
        closed_at_owner_exit = len(bound) == 1 and bound[0].socket.fileno() == -1
        print("SIGINT_AFTER_BIND", fired, "CAUGHT", caught,
              "SOCKET_CLOSED_AT_OWNER_EXIT", closed_at_owner_exit,
              flush=True)
        with service.MainSupervisor(case.fixture.source.make_main()):
            pass
        assert fired and caught and closed_at_owner_exit, (
            "Listener remained bound after Host leases were released")
        print("SIGINT_INITIALIZATION_SOCKET_CLOSE_PASS", flush=True)
    finally:
        sys.settrace(None)
        for server in bound:
            if server.socket.fileno() != -1:
                server.server_close()
        case.doCleanups()


if __name__ == "__main__":
    unittest.main()
