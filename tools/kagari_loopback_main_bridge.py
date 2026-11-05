"""KAGARI #339: thin bridge to the existing #291 repository Main.

This is not a new grant, Task Record, scheduler, or permission authority.
The Main owns GitHub Issue/Contract/Worktree/Session and prompt receipts.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import re
import signal
from threading import Thread, current_thread, main_thread

from tools.kagari_vnext_main import ApprovedTask, MainError, RepositoryMain
from tools.kagari_vnext_main_service import MainAttachmentConfig, attached_supervisor
from tools.kagari_loopback_control import ControlActionError, create_control_server

_ISSUE = re.compile(r"[1-9][0-9]{0,17}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ACK = frozenset({"SUBMITTED", "RECOVERED_SUBMITTED", "ALREADY_DISPATCHED"})


class MainBridgeError(ControlActionError):
    """Blocked, unverified, or uncertain Main operation; never a grant."""


@dataclass(frozen=True)
class MainBridge:
    main: RepositoryMain

    def __post_init__(self) -> None:
        if type(self.main) is not RepositoryMain:
            raise MainBridgeError("MAIN_REQUIRED")

    def _approved(self, task: str) -> tuple[int, ApprovedTask]:
        if type(task) is not str or not _ISSUE.fullmatch(task):
            raise MainBridgeError("INVALID_TASK")
        issue = int(task)
        choice = self.main.approved.get(issue)
        if (type(choice) is not ApprovedTask
                or type(choice.issue) is not int
                or choice.issue != issue):
            raise MainBridgeError("TASK_NOT_APPROVED")
        return issue, choice

    def _v2_readonly_view(
        self, issue: int, choice: ApprovedTask, view: object,
    ) -> dict:
        """Validate the existing #291 v2 current-Task observation, not a grant.

        R19 Phase A exposes only a conservative detached status and a
        positively Host-confirmed NOT_STARTED Ask refusal. An unknown or
        native-present Session is deliberately NOT offered as a completed
        Task, a confirmed empty Ask queue, or an Ask reply capability.
        """
        if type(view) is not dict:
            raise MainBridgeError("V2_MAIN_OBSERVATION_MISMATCH")
        approved = view.get("approved_task")
        current = view.get("current_record")
        if (view.get("status") != "blocked"
                or view.get("reason") != "V2_MAIN_BLOCKED_LIVE"
                or type(view.get("metadata_version")) is not int
                or view["metadata_version"] != 2
                or view.get("repository") != self.main.store.repository
                or type(view.get("issue")) is not int
                or view["issue"] != issue
                or type(approved) is not dict
                or approved.get("repository") != self.main.store.repository
                or type(approved.get("issue")) is not int
                or approved["issue"] != issue
                or approved.get("contract_id") != choice.contract_id
                or approved.get("branch_ref") != choice.branch_ref
                or approved.get("base_revision") != choice.base_revision
                or type(current) is not dict
                or current.get("contract_id") != choice.contract_id
                or type(current.get("record_id")) is not str
                or _DIGEST.fullmatch(current["record_id"]) is None
                or type(current.get("metadata_tip")) is not str
                or re.fullmatch(r"[0-9a-f]{40}", current["metadata_tip"]) is None
                or type(current.get("archive_tip")) is not str
                or re.fullmatch(r"[0-9a-f]{40}", current["archive_tip"]) is None
                or view.get("task_execution_authorized") is not False
                or view.get("worktree_mutation_authorized") is not False
                or view.get("github_write_authorized") is not False
                or view.get("kickoff") not in ("NOT_SENT", "UNKNOWN")):
            raise MainBridgeError("V2_MAIN_OBSERVATION_MISMATCH")
        return view

    def status(self, task: str) -> dict:
        """Map factual #291 observations to a conservative transport status.

        The OpenCode 'idle' state never establishes Task completion. No status
        adapter may independently turn this into a success/acceptance verdict.
        """
        issue, choice = self._approved(task)
        try:
            view = self.main.observe(issue)
        except MainError:
            raise MainBridgeError("MAIN_OBSERVATION_UNAVAILABLE") from None
        if issue in self.main.v2_readonly_for_issue:
            self._v2_readonly_view(issue, choice, view)
            # v2 Host has not qualified a dispatched model Task: no RUNNING,
            # BLOCKED-ON-HUMAN or COMPLETED inference from a read-only Session.
            return {"task": task, "state": "UNKNOWN"}
        if (type(view) is not dict or view.get("status") != "ok"
                or view.get("issue") != issue
                or view.get("repository") != self.main.store.repository
                or type(view.get("approved_task")) is not dict
                or view["approved_task"].get("contract_id") != choice.contract_id
                or type(view.get("task_view")) is not dict):
            raise MainBridgeError("MAIN_OBSERVATION_MISMATCH")
        kickoff = view.get("kickoff")
        if kickoff not in ("NOT_SENT", "SENT", "UNCERTAIN"):
            raise MainBridgeError("MAIN_OBSERVATION_MISMATCH")
        if kickoff != "SENT":
            return {"task": task, "state": "UNKNOWN"}
        runtime = view.get("runtime")
        if (type(runtime) is not dict or runtime.get("status") != "ok"
                or type(runtime.get("task")) is not dict
                or runtime["task"].get("issue") != issue
                or runtime["task"].get("repository") != self.main.store.repository
                or runtime["task"].get("contract_sha256") != choice.contract_id):
            raise MainBridgeError("MAIN_RUNTIME_MISMATCH")
        pending = runtime.get("pending_asks")
        state = runtime.get("session_state")
        if type(pending) is not int or pending < 0 or state not in (
                "busy", "retry", "idle", "UNKNOWN"):
            raise MainBridgeError("MAIN_RUNTIME_MISMATCH")
        if pending:
            return {"task": task, "state": "BLOCKED"}
        if state in ("busy", "retry"):
            return {"task": task, "state": "RUNNING"}
        return {"task": task, "state": "UNKNOWN"}


    def asks(self, task: str) -> dict:
        """Expose existing #292 native pending Ask view; no local Ask database.

        The root Session and immutable Contract are re-observed through #291.
        This method does not answer, approve, grant or retry an Ask.
        """
        issue, choice = self._approved(task)
        try:
            view = self.main.observe(issue)
        except MainError:
            raise MainBridgeError("ASK_OBSERVATION_UNAVAILABLE") from None
        if issue in self.main.v2_readonly_for_issue:
            self._v2_readonly_view(issue, choice, view)
            session = view.get("session_observation")
            if (type(session) is dict
                    and session.get("state") == "NOT_STARTED"
                    and session.get("reason") == "HOST_CONFIRMED_ABSENT"
                    and session.get("session_id") is None
                    and type(session.get("pending_asks")) is list
                    and session["pending_asks"] == []
                    and type(session.get("pending_count")) is int
                    and session["pending_count"] == 0
                    and view.get("runtime") == "NOT_STARTED"
                    and view.get("kickoff") == "NOT_SENT"):
                raise MainBridgeError("ASK_SESSION_NOT_STARTED")
            # R5A0 has no authenticated native Session locator; a v2
            # UNKNOWN or PRESENT locator also cannot establish a confirmed
            # empty Ask queue or a writable reply path at R19 Phase A.
            raise MainBridgeError("ASK_SESSION_UNCERTAIN")
        if (type(view) is not dict or view.get("status") != "ok"
                or view.get("issue") != issue
                or view.get("repository") != self.main.store.repository
                or type(view.get("approved_task")) is not dict
                or view["approved_task"].get("contract_id") != choice.contract_id):
            raise MainBridgeError("ASK_BINDING_MISMATCH")
        if view.get("kickoff") == "NOT_SENT":
            raise MainBridgeError("ASK_SESSION_NOT_STARTED")
        if view.get("kickoff") != "SENT":
            raise MainBridgeError("ASK_SESSION_UNCERTAIN")
        runtime = view.get("runtime")
        pending = view.get("pending")
        if (type(runtime) is not dict or runtime.get("status") != "ok"
                or type(runtime.get("task")) is not dict
                or type(pending) is not list or len(pending) > 32):
            raise MainBridgeError("ASK_OBSERVATION_INVALID")
        identity = runtime["task"]
        sid = identity.get("session_id")
        if (identity.get("repository") != self.main.store.repository
                or identity.get("issue") != issue
                or identity.get("contract_sha256") != choice.contract_id
                or type(sid) is not str
                or re.fullmatch(r"ses_[A-Za-z0-9_-]{8,120}", sid) is None):
            raise MainBridgeError("ASK_BINDING_MISMATCH")
        result = []
        seen = set()
        for item in pending:
            if type(item) is not dict or item.get("kind") not in (
                    "permission", "question"):
                raise MainBridgeError("ASK_OBSERVATION_INVALID")
            kind = item["kind"]
            rid = item.get("request_id")
            pattern = r"per_[A-Za-z0-9_-]{8,120}" if kind == "permission" else r"que_[A-Za-z0-9_-]{8,120}"
            bound = item.get("task")
            upstream = item.get("upstream")
            if (type(rid) is not str or re.fullmatch(pattern, rid) is None
                    or rid in seen or type(bound) is not dict
                    or bound.get("repository") != identity["repository"]
                    or bound.get("issue") != issue
                    or bound.get("contract_sha256") != choice.contract_id
                    or bound.get("session_id") != sid
                    or bound.get("worktree") != identity.get("worktree")
                    or type(upstream) is not dict
                    or upstream.get("id") != rid
                    or upstream.get("sessionID") != sid):
                raise MainBridgeError("ASK_BINDING_MISMATCH")
            seen.add(rid)
            # Keep exact upstream request details from the #292 read path.
            # The HTTP transport bounds the response; no Ask is auto-answered.
            result.append({"kind": kind, "request_id": rid, "upstream": upstream})
        return {
            "task": task, "contract_id": choice.contract_id,
            "root_session_id": sid, "pending": result
        }

    def dispatch(self, task: str, expected_contract_id: str) -> dict:
        """Request exactly one #291-owned, idempotently-reconciled kickoff.

        No extra queue, local dispatch receipt, automatic retry or Session
        launcher is added. MainSupervisor must already own the live Main.
        """
        issue, choice = self._approved(task)
        if (type(expected_contract_id) is not str
                or not _DIGEST.fullmatch(expected_contract_id)
                or expected_contract_id != choice.contract_id):
            raise MainBridgeError("TASK_CONTRACT_MISMATCH")
        try:
            receipt = self.main.dispatch(issue)
        except MainError as error:
            # Report precise observable blockage, never reinterpret it as ACK.
            raise MainBridgeError(error.code) from None
        if (type(receipt) is not dict or receipt.get("status") != "ok"
                or receipt.get("issue") != issue
                or receipt.get("repository") != self.main.store.repository
                or receipt.get("contract_id") != expected_contract_id
                or receipt.get("prompt_state") not in _ACK):
            raise MainBridgeError("DISPATCH_RESULT_UNVERIFIED")
        state = receipt["prompt_state"]
        return {
            "task": task, "issue": issue,
            "repository": self.main.store.repository,
            "contract_id": expected_contract_id,
            "prompt_state": state,
            "status": "already_accepted" if state == "ALREADY_DISPATCHED"
                      else "accepted",
        }


def create_main_control_server(
    main: RepositoryMain, *, host: str = "127.0.0.1", port: int = 0
):
    """Attach the transport to one existing #291 Main. No Main is started."""
    bridge = MainBridge(main)
    return create_control_server(
        bridge.status, dispatch_provider=bridge.dispatch,
        ask_provider=bridge.asks, host=host, port=port,
        repository=main.store.repository
    )


@contextmanager
def _defer_sigint_at_lifecycle_boundary():
    """Defer SIGINT across a short Host-owned startup/shutdown critical section.

    pthread_sigmask on the main thread is insufficient: another unmasked
    thread can receive SIGINT, whose Python handler then executes on main
    *despite* its mask. Temporarily replace the process SIGINT handler only
    on the main interpreter thread; replay one pending interruption after
    the critical section and restore the caller's original handler. A long
    running HTTP callback can delay this replay. This is not a process-wide
    signal-management service or a promise against SIGKILL/process crashes.
    """
    if current_thread() is not main_thread():
        yield
        return

    previous = signal.getsignal(signal.SIGINT)
    pending: list[tuple[int, object]] = []

    def defer(signum: int, frame: object) -> None:
        # Python callbacks run only on the main interpreter thread.
        # Do not call locks, allocate a worker, or raise from this handler.
        if not pending:
            pending.append((signum, frame))

    signal.signal(signal.SIGINT, defer)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
        if pending:
            signum, frame = pending[0]
            if callable(previous):
                previous(signum, frame)
            elif previous == signal.SIG_DFL:
                # Preserve the caller's OS-default signal disposition
                # *after* returning Host-owned resources.
                signal.raise_signal(signal.SIGINT)
            # An original SIG_IGN remains ignored.


@contextmanager
def attached_main_control_server(
    main: RepositoryMain, attachment: MainAttachmentConfig, *, port: int = 0,
):
    """Explicit trusted-Host opt-in listener bound to existing #256/#291 ownership.

    No standalone CLI, daemon, task discovery, new owner, or grant is added.
    Attach and repository Main locks must be held *before* binding the socket.
    On exit, listener shutdown and active request-thread draining precede
    releasing either original Host lease. Caller must provide the exact Main
    with its already authorized Issue/Contract and #194 Host capabilities.
    """
    if type(main) is not RepositoryMain or type(attachment) is not MainAttachmentConfig:
        raise MainBridgeError("MAIN_ATTACHMENT_BINDING_INVALID")

    with attached_supervisor(main, attachment) as owner:
        owner._assert_owned(main)
        # No listener is bound before the exception-safe startup scope.
        # This permits single-SIGINT recovery even between socket bind and
        # creation of the serving Thread.
        server = None
        thread = None
        started = False
        cleanup_complete = False
        interrupted: KeyboardInterrupt | None = None

        def _drain_before_owner_release() -> None:
            nonlocal cleanup_complete, interrupted

            def finish_before_release(operation):
                nonlocal interrupted
                # Even a synthetic interrupt from a stdlib close/join call
                # must not allow existing Host leases to be released early.
                while True:
                    try:
                        operation()
                        return
                    except KeyboardInterrupt as error:
                        if interrupted is None:
                            interrupted = error

            with _defer_sigint_at_lifecycle_boundary():
                if server is not None:
                    if started and thread is not None and thread.is_alive():
                        finish_before_release(server.shutdown)
                    # ThreadingMixIn.server_close() can lose its list after
                    # an interrupted pop_all()/join. The existing admission
                    # semaphore proves every admitted handler has exited.
                    finish_before_release(server.server_close)
                    finish_before_release(server.drain_admitted_handlers)
                    if started and thread is not None:
                        finish_before_release(thread.join)
                cleanup_complete = True
            if interrupted is not None:
                raise interrupted

        # A single SIGINT immediately upon entering the inner finally can
        # arrive BEFORE _defer_sigint_at_lifecycle_boundary is installed.
        # Keep an enclosing KeyboardInterrupt recovery frame alive across
        # both serving and cleanup so that one interrupted entry retries the
        # same idempotent drain instead of unwinding the Host lease.
        try:
            try:
                with _defer_sigint_at_lifecycle_boundary():
                    server = create_main_control_server(main, port=port)
                    thread = Thread(
                        target=server.serve_forever,
                        name="kagari-loopback", daemon=False)
                    try:
                        thread.start()
                    finally:
                        # A Thread.start wrapper may fail after it started
                        # the actual serving thread.
                        started = thread.ident is not None
                yield server
            finally:
                _drain_before_owner_release()
        except KeyboardInterrupt:
            if not cleanup_complete:
                _drain_before_owner_release()
            raise
