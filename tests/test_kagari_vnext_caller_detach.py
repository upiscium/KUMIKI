"""#291 B2b R3: independent Python caller exit / fresh Main recovery (offline).

Runs every caller/observer in a NEW Python interpreter, reconstructing Main
from fixture-approved serialized selection and durable #191 Git metadata.
OpenCode is an isolated HTTP fake. No real model, GitHub mutation or deployment.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import traceback
import unittest
from unittest.mock import patch

from tests import test_kagari_vnext_main as source_fixture


def worker(config_path: Path, action: str) -> dict:
    """No inherited Python Main, mock state, process handle or repo owner."""
    from metadata_ref import MetadataStore
    from task_collaboration import CollaborationReceipt
    from tools.kagari_vnext_main import ApprovedTask, MainError, RepositoryMain
    from tools import kagari_vnext_main as main_module
    from tools import kagari_vnext_main_service as service

    config = json.loads(config_path.read_text(encoding="utf-8"))
    root = Path(config["project_root"])
    store = MetadataStore(root, config["repository"], config["remote"])
    issue = config["issue"]
    approved = ApprovedTask(issue, config["branch_ref"],
                            config["base_revision"], config["contract_id"])
    main = RepositoryMain(
        store, approved={issue: approved},
        read_issue=lambda n: config["issue_facts"][str(n)].copy(),
        runtime_home=Path(config["runtime_home"]),
        port_for_issue=lambda n: config["ports"][str(n)],
        opencode_bin=config["opencode_bin"],
        model=("openai", "gpt-6-sol"),
        initial_collaboration_for_issue=lambda _issue, _worktree: None)

    # The real #191 Record/Contract, #286 allocator and #163 Session still run.
    # Only the separately-gated #194 live PR receipt is synthetic.
    def fixture_preflight(_main, choice, worktree):
        subject = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=worktree,
            stdin=subprocess.DEVNULL, capture_output=True,
            text=True, check=True).stdout.strip()
        return CollaborationReceipt(
            repository=config["repository"], task=str(choice.issue),
            branch_ref=choice.branch_ref, subject=subject,
            pull_request=91, comment_id=201,
            metadata_commit=config["metadata_commit"],
            record_id=config["record_id"],
            contract_id=choice.contract_id,
            snapshot_id="f" * 64)

    with patch.object(RepositoryMain, "_establish_initial_collaboration",
                      fixture_preflight):
        if action == "observe":
            return main.observe(issue)
        if action not in ("dispatch", "lost_after_accept", "lost_before_send"):
            raise ValueError("UNKNOWN_TEST_ACTION")

        transport = main_module.opencode_request

        def uncertain_transport(url: str, *, payload=None):
            if action == "lost_before_send" and "/prompt_async?" in url:
                raise main_module.ControlError(
                    "OPENCODE_UNAVAILABLE", "synthetic undelivered HTTP POST")
            result = transport(url, payload=payload)
            if action == "lost_after_accept" and "/prompt_async?" in url:
                raise main_module.ControlError(
                    "OPENCODE_UNAVAILABLE", "synthetic lost HTTP ACK")
            return result

        with patch.object(service, "_host_state_home",
                          return_value=Path(config["host_state_home"])):
            with service.MainSupervisor(main):
                try:
                    if action == "dispatch":
                        return main.dispatch(issue)
                    with patch.object(main_module, "opencode_request",
                                      side_effect=uncertain_transport):
                        return main.dispatch(issue)
                except MainError as exc:
                    return {"blocked": exc.code}


class CallerDetachOfflineTests(unittest.TestCase):
    """Independent executables reconstruct from local durable fixture records."""

    def setUp(self) -> None:
        self.fixture = source_fixture.MainOrchestratorTest("runTest")
        # Nested setUp may fail after registering its own patches/resources.
        # Register nested teardown before invoking it, even on setup failure.
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.addCleanup(self._stop_only_fixture_server)

        self.config = self.fixture.fixture.temp_root / "selected-authority.json"
        selection = {
            "repository": "acme/widgets", "remote": self.fixture.store.remote,
            "issue": 291, "project_root": str(self.fixture.project),
            "branch_ref": self.fixture.branch_ref,
            "base_revision": self.fixture.base,
            "contract_id": self.fixture.pub.contract_id,
            "record_id": self.fixture.pub.record_id,
            "metadata_commit": self.fixture.pub.metadata_commit,
            "runtime_home": str(self.fixture.runtime),
            "host_state_home": str(self.fixture.fixture.temp_root),
            "opencode_bin": str(self.fixture.bin),
            "ports": {"291": self.fixture.ports[291]},
            "issue_facts": {"291": self.fixture.facts[291]},
        }
        self.config.write_text(json.dumps(selection, sort_keys=True),
                               encoding="utf-8")

    def _only_our_process(self) -> int | None:
        recorded = self.fixture.runtime / "issue-291" / "process.json"
        if not recorded.is_file():
            return None
        value = json.loads(recorded.read_text(encoding="utf-8"))
        pid = value.get("pid")
        if type(pid) is not int or pid <= 1:
            return None
        proc = Path(f"/proc/{pid}")
        try:
            actual_cwd = (proc / "cwd").resolve(strict=True)
            argv = (proc / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            return None
        expected_cwd = (self.fixture.project / ".worktrees" / "291").resolve()
        if (actual_cwd != expected_cwd
                or not any(str(self.fixture.bin).encode() == arg
                           for arg in argv)
                or b"serve" not in argv):
            return None
        return pid

    def _owned_pidfd(self) -> int | None:
        """Pin the observed server before testing its identity; never signal a PID."""
        recorded = self.fixture.runtime / "issue-291" / "process.json"
        if not recorded.is_file():
            return None
        value = json.loads(recorded.read_text(encoding="utf-8"))
        pid = value.get("pid")
        if type(pid) is not int or pid <= 1:
            return None
        try:
            fd = os.pidfd_open(pid, 0)
        except (ProcessLookupError, PermissionError):
            return None
        # A recycled numeric PID after pidfd_open cannot retarget this descriptor.
        if self._only_our_process() != pid:
            os.close(fd)
            return None
        return fd

    def _stop_only_fixture_server(self) -> None:
        fd = self._owned_pidfd()
        if fd is None:
            return
        try:
            try:
                signal.pidfd_send_signal(fd, signal.SIGTERM, None, 0)
            except ProcessLookupError:
                return
            observer = select.poll()
            observer.register(fd, select.POLLIN)
            if not observer.poll(1500):
                # Escalate only via the same pinned process descriptor.
                try:
                    signal.pidfd_send_signal(fd, signal.SIGKILL, None, 0)
                except ProcessLookupError:
                    pass
        finally:
            os.close(fd)

    def _new_process(self, action: str) -> dict:
        """A new interpreter with only the serialized fixture locator."""
        completed = subprocess.run(
            [sys.executable, "-B", "-m",
             "tests.test_kagari_vnext_caller_detach",
             "--worker", str(self.config), action],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=90, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        try:
            value = json.loads(completed.stdout)
        except ValueError:
            self.fail("Worker did not return parseable JSON: " + completed.stdout)
        self.assertNotIn("error", value, value.get("error"))
        return value["ok"]

    def test_failed_nested_fixture_setup_restores_environment(self):
        # The shared fixture patches environment before allocating a port.
        # An injected port failure must not leak its partial setup state.
        before = os.environ.get("KAGARI_TEST_PROMPT_LOG")
        failing = CallerDetachOfflineTests("runTest")
        try:
            with patch.object(source_fixture, "next_port",
                              side_effect=OSError("synthetic port failure")):
                with self.assertRaisesRegex(OSError, "synthetic port failure"):
                    failing.setUp()
        finally:
            failing.doCleanups()
        self.assertEqual(os.environ.get("KAGARI_TEST_PROMPT_LOG"), before)

    def test_cleanup_uses_stable_pidfd_instead_of_signaling_numeric_pid(self):
        # Controlled reused-PID window: the original PID may have changed
        # after verification; only the pinned process descriptor is signaled.
        fake_pid = 24680
        record = self.fixture.runtime / "issue-291" / "process.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"pid": fake_pid}), encoding="utf-8")
        fd = os.open("/dev/null", os.O_RDONLY)
        events = []

        def pin(pid, flags):
            events.append(("pin", pid, flags))
            return fd

        def identity():
            events.append(("verify", fake_pid))
            return fake_pid

        def signal_pinned(owned_fd, sig, info, flags):
            events.append(("signal", owned_fd, sig))
            self.assertEqual((owned_fd, info, flags), (fd, None, 0))

        with patch.object(os, "pidfd_open", side_effect=pin), \
             patch.object(self, "_only_our_process", side_effect=identity), \
             patch.object(signal, "pidfd_send_signal", side_effect=signal_pinned), \
             patch.object(select, "poll") as poller, \
             patch.object(os, "kill", side_effect=AssertionError(
                 "numeric PID must never receive a signal")):
            poller.return_value.poll.return_value = []
            self._stop_only_fixture_server()
        self.assertEqual(events, [
            ("pin", fake_pid, 0), ("verify", fake_pid),
            ("signal", fd, signal.SIGTERM), ("signal", fd, signal.SIGKILL)])

    def test_cleanup_refuses_unverified_pidfd_without_signaling(self):
        record = self.fixture.runtime / "issue-291" / "process.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"pid": 24681}), encoding="utf-8")
        fd = os.open("/dev/null", os.O_RDONLY)
        with patch.object(os, "pidfd_open", return_value=fd), \
             patch.object(self, "_only_our_process", return_value=None), \
             patch.object(signal, "pidfd_send_signal") as signal_pinned, \
             patch.object(os, "kill", side_effect=AssertionError(
                 "numeric PID must never receive a signal")):
            self._stop_only_fixture_server()
            signal_pinned.assert_not_called()

    def test_acknowledged_dispatch_survives_caller_exit_and_is_not_repeated(self):
        first = self._new_process("dispatch")
        self.assertEqual(first["prompt_state"], "SUBMITTED")
        self.assertIsNotNone(self._only_our_process())
        self.assertEqual(len(self.fixture.prompts()), 1)

        independent_view = self._new_process("observe")
        self.assertEqual(independent_view["kickoff"], "SENT")
        self.assertEqual(
            independent_view["approved_task"]["contract_id"],
            self.fixture.pub.contract_id)
        self.assertEqual(independent_view["runtime"]["pending_asks"], 1)

        reentry = self._new_process("dispatch")
        self.assertEqual(reentry["prompt_state"], "ALREADY_DISPATCHED")
        self.assertEqual(reentry["context"], first["context"])
        self.assertEqual(len(self.fixture.prompts()), 1)

    def test_lost_acceptance_ack_is_reconciled_in_fresh_process(self):
        first = self._new_process("lost_after_accept")
        self.assertEqual(first["blocked"], "OPENCODE_UNAVAILABLE")
        self.assertEqual(len(self.fixture.prompts()), 1)
        uncertain = self._new_process("observe")
        self.assertEqual(uncertain["kickoff"], "UNCERTAIN")
        self.assertEqual(uncertain["runtime"]["pending_asks"], 1)
        recovered = self._new_process("dispatch")
        self.assertEqual(recovered["prompt_state"], "RECOVERED_SUBMITTED")
        self.assertEqual(len(self.fixture.prompts()), 1)
        self.assertEqual(self._new_process("observe")["kickoff"], "SENT")

    def test_undelivered_post_does_not_become_permission_to_resend(self):
        first = self._new_process("lost_before_send")
        self.assertEqual(first["blocked"], "OPENCODE_UNAVAILABLE")
        self.assertEqual(len(self.fixture.prompts()), 0)
        self.assertEqual(self._new_process("observe")["kickoff"], "UNCERTAIN")
        second = self._new_process("dispatch")
        self.assertEqual(second["blocked"], "PROMPT_OUTCOME_UNCERTAIN")
        self.assertEqual(len(self.fixture.prompts()), 0)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        try:
            print(json.dumps({"ok": worker(Path(sys.argv[2]), sys.argv[3])},
                             sort_keys=True))
        except BaseException:
            print(json.dumps({"error": traceback.format_exc()}))
            raise SystemExit(2)
    else:
        unittest.main()
