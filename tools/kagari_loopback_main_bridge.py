"""KAGARI #339: thin bridge to the existing #291 repository Main.

This is not a new grant, Task Record, scheduler, or permission authority.
The Main owns GitHub Issue/Contract/Worktree/Session and prompt receipts.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from tools.kagari_vnext_main import ApprovedTask, MainError, RepositoryMain
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
        if type(choice) is not ApprovedTask or choice.issue != issue:
            raise MainBridgeError("TASK_NOT_APPROVED")
        return issue, choice

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
