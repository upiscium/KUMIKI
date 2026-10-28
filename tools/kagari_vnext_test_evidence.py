"""KAGARI #291 — bounded host-owned test execution and immutable #217 Evidence.

No second Task authority, status database, or model command generator. A
trusted host chooses an explicit test command for one already-approved #191
Task. The test runs in the exact #286 registered Worktree. Only measured facts
are persisted using the existing #217 metadata CAS. An uncertain test outcome
is never executed twice under the same run ID.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
from typing import Any

V4 = Path(__file__).resolve().parents[1] / "components/agent-core-v4"
import sys
sys.path.insert(0, str(V4))
from evidence import Evidence, EvidenceError, encode_evidence
from metadata_ref import MetadataRefError, MetadataConflictError
from task_view import TaskViewError

if __package__:
    from .kagari_vnext_main import MainError, RepositoryMain, _json_write_once
    from .kagari_vnext_task_binding import worktree_registered, ControlError
else:
    from kagari_vnext_main import MainError, RepositoryMain, _json_write_once
    from kagari_vnext_task_binding import worktree_registered, ControlError

_RUN_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)
MAX_COMMAND_BYTES = 8192
TEST_LOCK_SECONDS = 5.0
MAX_TEST_SECONDS = 900


@dataclass(frozen=True)
class TestEvidenceReceipt:
    state: str                 # CREATED, RECOVERED, or RESUMED
    repository: str
    issue: int
    run_id: str
    contract_id: str
    subject: str
    evidence_id: str
    metadata_commit: str
    result: str                # PASS, FAIL, or TIMEOUT
    exit_code: int | None


def _digest_file(stream) -> tuple[str, int]:
    stream.seek(0)
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = stream.read(128 * 1024)
        if not chunk:
            break
        size += len(chunk)
        digest.update(chunk)
    return digest.hexdigest(), size


def _load_marker(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or path.stat().st_size > 32_768:
            raise ValueError("unsupported marker")
        value = json.loads(path.read_text("utf-8"))
        if type(value) is not dict:
            raise ValueError("unsupported marker")
        return value
    except (OSError, ValueError, UnicodeError):
        raise MainError("TEST_RUN_RECONCILIATION_REQUIRED") from None


class TaskTestEvidenceRunner:
    def __init__(self, main: RepositoryMain) -> None:
        if type(main) is not RepositoryMain:
            raise MainError("TEST_HOST_REQUIRED")
        self.main = main

    def _published(self, intent: dict, marker: dict, state: str) -> TestEvidenceReceipt:
        if not all(marker.get(k) == v for k, v in intent.items()):
            raise MainError("TEST_RUN_RECONCILIATION_REQUIRED")
        evidence_id = marker.get("evidence_id")
        if type(evidence_id) is not str or not re.fullmatch(r"[a-f0-9]{64}", evidence_id):
            raise MainError("TEST_RUN_RECONCILIATION_REQUIRED")
        try:
            tip = self.main.store.fetch_tip()
            if tip is None:
                raise MainError("TEST_EVIDENCE_OUTCOME_UNCERTAIN")
            actual = Evidence(self.main.store).read(
                tip, evidence_id, task=str(intent["issue"]),
                subject=intent["subject"])
        except (EvidenceError, MetadataRefError, MetadataConflictError, ValueError):
            raise MainError("TEST_EVIDENCE_OUTCOME_UNCERTAIN") from None
        payload = actual["payload"]["payload"]
        if (payload.get("run_id") != intent["run_id"]
                or payload.get("argv_sha256") != intent["argv_sha256"]
                or payload.get("contract_id") != intent["contract_id"]
                or payload.get("subject") != intent["subject"]
                or payload.get("result") not in ("PASS", "FAIL", "TIMEOUT")
                or (type(payload.get("exit_code")) is not int
                    and payload.get("exit_code") is not None)):
            raise MainError("TEST_EVIDENCE_OUTCOME_UNCERTAIN")
        return TestEvidenceReceipt(
            state, self.main.store.repository, intent["issue"],
            intent["run_id"], intent["contract_id"], intent["subject"],
            evidence_id, tip, payload["result"], payload["exit_code"])

    def run(
        self, issue: int, *,
        run_id: str, argv: tuple[str, ...], timeout_seconds: int = 120,
    ) -> TestEvidenceReceipt:
        if (type(run_id) is not str or not _RUN_ID.fullmatch(run_id)
                or type(argv) is not tuple or not 1 <= len(argv) <= 64
                or any(type(v) is not str or not v or "\x00" in v for v in argv)
                or len(json.dumps(argv).encode()) > MAX_COMMAND_BYTES
                or argv[0].startswith("-")
                or type(timeout_seconds) is not int
                or not 1 <= timeout_seconds <= MAX_TEST_SECONDS):
            raise MainError("TEST_SPEC_INVALID")
        choice, _snapshot = self.main._spec(issue)
        worktree = self.main.store.root / ".worktrees" / str(issue)
        try:
            worktree_registered(self.main.store.root, worktree, choice.branch_ref)
        except ControlError:
            raise MainError("TASK_WORKTREE_MISMATCH") from None
        before = self.main._live_view(choice, worktree)
        if before["git"]["branch_matches_task"] is not True:
            raise MainError("TASK_WORKTREE_MISMATCH")
        if any(before["git"]["status"].values()):
            raise MainError("TEST_SOURCE_DIRTY")
        subject = before["subject"]
        command_digest = hashlib.sha256(
            json.dumps({"argv": argv, "timeout": timeout_seconds},
                       separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        intent = {
            "schema_version": 1,
            "repository": self.main.store.repository,
            "issue": issue, "run_id": run_id,
            "contract_id": choice.contract_id,
            "subject": subject,
            "argv_sha256": command_digest,
        }

        runtime = self.main._runtime(issue)
        runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        prefix = "test-" + run_id
        lock_path = runtime / (prefix + ".lock")
        started = runtime / (prefix + ".started.json")
        candidate = runtime / (prefix + ".candidate.json")
        confirmed = runtime / (prefix + ".confirmed.json")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "rb+") as lock:
            deadline = time.monotonic() + TEST_LOCK_SECONDS
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise MainError("TEST_RUN_BUSY") from None
                    time.sleep(.05)
            try:
                if confirmed.exists():
                    return self._published(
                        intent, _load_marker(confirmed), "RESUMED")
                if candidate.exists():
                    marker = _load_marker(candidate)
                    receipt = self._published(intent, marker, "RECOVERED")
                    _json_write_once(confirmed, marker)
                    return receipt
                if started.exists():
                    if _load_marker(started) != intent:
                        raise MainError("TEST_RUN_RECONCILIATION_REQUIRED")
                    raise MainError("TEST_EXECUTION_OUTCOME_UNCERTAIN")

                # Mark the non-idempotent command as started BEFORE spawning.
                # If the process or Main crashes, a retry cannot re-run it
                # without new explicit run identity and host reconciliation.
                self.main._spec(issue)
                _json_write_once(started, intent)
                elapsed_start = time.monotonic()
                with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                    try:
                        process = subprocess.Popen(
                            list(argv), cwd=worktree, stdin=subprocess.DEVNULL,
                            stdout=stdout, stderr=stderr, start_new_session=True)
                    except OSError:
                        raise MainError("TEST_EXECUTION_UNAVAILABLE") from None
                    timed_out = False
                    try:
                        exit_code: int | None = process.wait(timeout=timeout_seconds)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=8)
                        exit_code = None
                    duration_ms = int((time.monotonic() - elapsed_start) * 1000)
                    stdout_digest, stdout_bytes = _digest_file(stdout)
                    stderr_digest, stderr_bytes = _digest_file(stderr)
                choice_after, _ = self.main._spec(issue)
                if choice_after != choice:
                    raise MainError("TASK_CONTRACT_CHANGED")
                # A tool can change Git's administrative Worktree registration
                # without changing the apparent HEAD, branch or file status.
                # Recheck exact native registration after the subprocess,
                # before claiming Evidence for this Issue's subject.
                try:
                    worktree_registered(
                        self.main.store.root, worktree, choice.branch_ref)
                except ControlError:
                    raise MainError("TEST_WORKTREE_REGISTRATION_CHANGED") from None
                after = self.main._live_view(choice, worktree)
                if after["subject"] != subject or after["git"]["branch_matches_task"] is not True:
                    raise MainError("TEST_SUBJECT_CHANGED")
                if any(after["git"]["status"].values()):
                    raise MainError("TEST_SOURCE_DIRTY_AFTER_RUN")
                result = ("TIMEOUT" if timed_out else
                          "PASS" if exit_code == 0 else "FAIL")
                envelope = {
                    "schema_version": 1,
                    "kind": "kagari/task-test-command-v1",
                    "producer": {"name": "kagari-main-host-test-runner", "version": "1"},
                    "created_at": datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"),
                    "payload": {
                        **intent,
                        "result": result, "exit_code": exit_code,
                        "duration_ms": duration_ms,
                        "stdout_sha256": stdout_digest,
                        "stdout_bytes": stdout_bytes,
                        "stderr_sha256": stderr_digest,
                        "stderr_bytes": stderr_bytes,
                    },
                }
                try:
                    evidence_id, data = encode_evidence(
                        self.main.store.repository, str(issue), subject,
                        envelope)
                except EvidenceError:
                    raise MainError("TEST_EVIDENCE_INVALID") from None
                marker = {**intent, "evidence_id": evidence_id}
                # This marker fixes the exact immutable bytes before the
                # metadata publication boundary. No retry reruns the command.
                _json_write_once(candidate, marker)
                try:
                    self.main.store.publish([data])
                except (MetadataRefError, MetadataConflictError):
                    raise MainError("TEST_EVIDENCE_OUTCOME_UNCERTAIN") from None
                receipt = self._published(intent, marker, "CREATED")
                _json_write_once(confirmed, marker)
                return receipt
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def guarded_draft_after_pass(
        self, issue: int, *, run_id: str, collaboration: object,
    ):
        """Explicit trusted-host continuation, never a follow-loop mutation.

        The test must be a confirmed immutable PASS for the *current*
        approved clean Task commit. Real PR create/adopt authorization remains
        solely in the existing #194 TaskCollaboration facade.
        """
        if type(run_id) is not str or not _RUN_ID.fullmatch(run_id):
            raise MainError("TEST_SPEC_INVALID")
        choice, _ = self.main._spec(issue)
        confirmed = self.main._runtime(issue) / (
            "test-" + run_id + ".confirmed.json")
        if not confirmed.exists():
            raise MainError("TEST_EVIDENCE_NOT_CONFIRMED")
        marker = _load_marker(confirmed)
        required = (
            "schema_version", "repository", "issue", "run_id",
            "contract_id", "subject", "argv_sha256")
        try:
            intent = {name: marker[name] for name in required}
        except KeyError:
            raise MainError("TEST_RUN_RECONCILIATION_REQUIRED") from None
        if (intent["schema_version"] != 1
                or intent["repository"] != self.main.store.repository
                or intent["issue"] != issue
                or intent["run_id"] != run_id
                or intent["contract_id"] != choice.contract_id):
            raise MainError("TEST_RUN_RECONCILIATION_REQUIRED")
        receipt = self._published(intent, marker, "RESUMED")
        if receipt.result != "PASS" or receipt.exit_code != 0:
            raise MainError("TEST_RESULT_NOT_PASS")
        worktree = self.main.store.root / ".worktrees" / str(issue)
        try:
            worktree_registered(
                self.main.store.root, worktree, choice.branch_ref)
        except ControlError:
            raise MainError("TASK_WORKTREE_MISMATCH") from None
        view = self.main._live_view(choice, worktree)
        if (view["subject"] != receipt.subject
                or view["git"]["branch_matches_task"] is not True
                or any(view["git"]["status"].values())):
            raise MainError("TEST_SUBJECT_CHANGED")
        return self.main.guarded_draft_pr(issue, collaboration)
