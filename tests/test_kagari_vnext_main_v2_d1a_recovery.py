"""#291 R5C1B1 D1A: disposable process-boundary crash/recovery contract.

Tests only. No production GitHub, production Metadata refs, models or Sessions.
The exact opt-in Host-worker lifetime/teardown is NOT implemented here.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tests import test_kagari_metadata_v2_issue_publisher as issue_fixture
from tests import test_kagari_vnext_main_v2_initial_checkpoint as base_fixture
from tools import kagari_vnext_main_v2_initial_checkpoint as coordinator
from kagari_vnext_trusted_host import RecoveryJournal
from metadata_v2 import issue_ref


# This synthetic budget is only for the disposable #337 fake-Git fixture.
# It does not change the production coordinator deadline or trusted Host.
_CHILD = r"""
import json
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace
import tests.test_kagari_vnext_main_v2_initial_checkpoint as base_fixture
from tests.test_metadata_v2 import Fixture
from metadata_v2 import Limits
from kagari_metadata_v2_issue_publisher import IssuePublisher
from kagari_metadata_v2_bootstrap import ApprovedIssue
from kagari_metadata_v2_host_binding import RepositoryIdentity, RegisteredIssue

def bounded_issue_publisher(*args, **kwargs):
    kwargs.setdefault("limits", Limits(seconds=60))
    return IssuePublisher(*args, **kwargs)

base_fixture.IssuePublisher = bounded_issue_publisher
mode, path = sys.argv[1:]
if mode == "recover":
    base_fixture._retry(path)
else:
    data = json.loads(Path(path).read_text())
    for name in ("project", "home", "package", "root", "jpath"):
        data[name] = Path(data[name])
    data["issue"] = ApprovedIssue(**data["issue"])
    data["registered"] = RegisteredIssue(**data["registered"])
    data["repo_identity"] = RepositoryIdentity(**data["repo_identity"])
    fixture = object.__new__(Fixture)
    fixture.root = data["root"]
    fixture.bare = fixture.root / "remote.git"
    fixture.genesis = data["repo_identity"].genesis
    fixture.calls, fixture.reservations = [], set()
    h = base_fixture._Harness(SimpleNamespace(**data, fixture=fixture),
                              initialize=False)
    def stop():
        if mode == "after_comment":
            # Fake GitHub effect is durable, but the Host did not receive or
            # persist the credential-bearing transport ACK.
            h.save_remote()
        os.kill(os.getpid(), signal.SIGKILL)
    h.hooks[{"after_metadata": "metadata_effect",
             "after_comment": "comment_effect",
             "after_ack": "provenance_ack"}[mode]] = stop
    h.run()
    raise AssertionError("expected self SIGKILL at original effect")
"""


class D1AOriginalIntentRecoveryTest(unittest.TestCase):
    """Only the original durable operation may be reconciled after a crash."""

    def setUp(self):
        if not hasattr(signal, "SIGKILL") or os.name != "posix":
            self.skipTest("POSIX SIGKILL required for process-boundary proof")
        fixture = issue_fixture.IssueWriterTest("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.src = fixture
        self.h = base_fixture._Harness(fixture)

    def _child(self, mode: str, context: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-B", "-c", _CHILD, mode, str(context)],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=180,
        )

    def _prove_original_recovery(self, mode: str) -> None:
        context = self.h.export()
        child = self._child(mode, context)
        self.assertEqual(-signal.SIGKILL, child.returncode,
                         child.stderr[-2400:])
        plan = self.h.saved_plan()
        self.assertIsNotNone(plan)
        original_id = plan["snapshot_id"]
        original_b64 = plan["snapshot_b64"]
        self.assertNotEqual(self.src.first, self.src.observe(issue_ref("101")))
        remote = json.loads((self.src.root / "fake-remote.json").read_text())
        expected_posts = 1 if mode in {"after_comment", "after_ack"} else 0
        self.assertEqual({"writes": 1, "posts": expected_posts, "views": 1},
                         remote["stats"])
        if mode == "after_comment":
            self.assertEqual(1, len(remote["comments"]))
        # The next process obtains a fresh Main owner. It must read only the
        # saved plan, not capture another view or allocate a second candidate.
        resumed = self._child("recover", context)
        if mode == "after_comment":
            # D2-P0 deliberately changes the old D1A expectation:
            # exact matching comment is NOT original-operation provenance.
            self.assertNotEqual(0, resumed.returncode)
            self.assertIn("D2P0_DURABLE_ACK_REQUIRED", resumed.stderr)
            final = json.loads((self.src.root / "fake-remote.json").read_text())
            self.assertEqual({"writes": 1, "posts": 1, "views": 1},
                             final["stats"])
            self.assertEqual(original_id, self.h.saved_plan()["snapshot_id"])
            self.assertEqual(original_b64, self.h.saved_plan()["snapshot_b64"])
            return
        self.assertEqual(0, resumed.returncode, resumed.stderr[-2400:])
        receipt = json.loads(resumed.stdout)
        self.assertEqual("CONFIRMED", receipt["initial_checkpoint"])
        self.assertEqual("V2_MAIN_BLOCKED_LIVE", receipt["reason"])
        for flag in coordinator._FLAGS:
            self.assertIs(receipt[flag], False, flag)
        self.assertEqual("NOT_STARTED", receipt["runtime"])
        self.assertEqual("NOT_SENT", receipt["kickoff"])
        final = json.loads((self.src.root / "fake-remote.json").read_text())
        self.assertEqual({"writes": 1, "posts": 1, "views": 1}, final["stats"])
        self.assertEqual(1, len(final["comments"]))
        self.assertEqual(original_id, self.h.saved_plan()["snapshot_id"])
        self.assertEqual(original_b64, self.h.saved_plan()["snapshot_b64"])
        self.assertEqual(original_id, receipt["pr_first"]["snapshot_id"])
        self.assertEqual(final["comments"][0]["comment_id"],
                         receipt["pr_first"]["comment_id"])

    def test_sigkill_after_metadata_effect_recovers_original_only(self):
        self._prove_original_recovery("after_metadata")

    def test_sigkill_after_comment_post_without_host_ack_blocks(self):
        self._prove_original_recovery("after_comment")

    def test_sigkill_after_durable_host_ack_confirms_original_without_repost(self):
        self._prove_original_recovery("after_ack")

    def test_durable_plan_without_candidate_receipt_blocks_fresh_worker(self):
        # A completed original plan is not enough to authorize allocating
        # another IssueWriter candidate after unknown plan-creation ACK.
        original_create = RecoveryJournal.create
        def lost_plan_ack(journal, key, record):
            original_create(journal, key, record)
            if journal.directory == self.h.jpath:
                raise OSError("synthetic original plan fsync ACK unknown")
        with patch.object(RecoveryJournal, "create", new=lost_plan_ack):
            with self.assertRaises(coordinator.InitialCheckpointUncertain):
                self.h.run()
        original_plan = self.h.saved_plan()
        self.assertIsNotNone(original_plan)
        context = self.h.export()
        resumed = self._child("recover", context)
        self.assertNotEqual(0, resumed.returncode)
        self.assertIn("R5C1B1_METADATA_RECEIPT_MISSING", resumed.stderr)
        self.assertEqual(original_plan, self.h.saved_plan())
        remote = json.loads((self.src.root / "fake-remote.json").read_text())
        self.assertEqual({"writes": 0, "posts": 0, "views": 1},
                         remote["stats"])
        self.assertEqual(self.src.first, self.src.observe(issue_ref("101")))

    def test_partial_original_plan_blocks_without_new_effect(self):
        key = coordinator._key(self.src.repo_identity.repository_id, 101)
        path = self.h.jpath / key
        path.write_bytes(b'{"schema":')
        path.chmod(0o600)
        original = path.read_bytes()
        context = self.h.export()
        refused = self._child("recover", context)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("R5C1B1_PLAN_UNREADABLE", refused.stderr)
        self.assertEqual(original, path.read_bytes())
        remote = json.loads((self.src.root / "fake-remote.json").read_text())
        self.assertEqual({"writes": 0, "posts": 0, "views": 0},
                         remote["stats"])
        self.assertEqual(self.src.first, self.src.observe(issue_ref("101")))


class D1AForkLockBoundaryTest(unittest.TestCase):
    """flock ownership alone cannot prove all old worker descendants ended."""

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "fork") and
                         hasattr(signal, "SIGKILL"), "Linux/POSIX fork needed")
    def test_fork_survivor_keeps_lock_until_its_fd_closes(self):
        # A fork child inherits its parent's open file description. Closing
        # the parent's own fd (as after process termination) does NOT prove
        # the child is quiescent. The child is OUR direct process and is
        # reaped in finally even if an assertion fails.
        with tempfile.TemporaryDirectory(prefix="291-d1a-fork-") as directory:
            lock = Path(directory) / "owner.lock"
            fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            ready_r, ready_w = os.pipe()
            release_r, release_w = os.pipe()
            pid = os.fork()
            if pid == 0:
                try:
                    os.close(ready_r)
                    os.close(release_w)
                    os.write(ready_w, b"R")
                    os.close(ready_w)
                    os.read(release_r, 1)  # Parent-controlled release.
                    os.close(release_r)
                    os.close(fd)
                finally:
                    os._exit(0)
            os.close(ready_w)
            os.close(release_r)
            waited = False
            try:
                self.assertTrue(select.select([ready_r], [], [], 5)[0])
                self.assertEqual(b"R", os.read(ready_r, 1))
                # Parent closes its own FD; the inherited child FD keeps the
                # same kernel open-file-description lock until child exit.
                os.close(fd)
                fd = None
                with lock.open("rb+") as candidate:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(ready_r)
                try:
                    os.write(release_w, b"E")
                except BrokenPipeError:
                    pass
                os.close(release_w)
                if fd is not None:
                    os.close(fd)
                # waitpid WNOHANG proves PID is still OUR live child before
                # the bounded fallback kill. The test never kills others.
                deadline = time.monotonic() + 8
                while True:
                    got, status = os.waitpid(pid, os.WNOHANG)
                    if got == pid:
                        waited = True
                        break
                    if time.monotonic() >= deadline:
                        os.kill(pid, signal.SIGKILL)
                        got, status = os.waitpid(pid, 0)
                        waited = got == pid
                        break
                    time.sleep(.03)
            self.assertTrue(waited)
            self.assertEqual(0, os.waitstatus_to_exitcode(status))
            with lock.open("rb+") as candidate:
                fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(candidate, fcntl.LOCK_UN)
