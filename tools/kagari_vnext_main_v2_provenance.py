"""#291 D2-P0 opt-in, source-only original-operation provenance validator.

This module checks facts returned by a *trusted* Host's bounded publication
gateway. No authenticated Host gateway, durable store, egress-fence or GitHub
transport is implemented or installed here. Dataclass values and arbitrary
callbacks alone cannot attest actual Host authority or remote authorship.
A production caller MUST NOT wire an unqualified or caller-supplied gateway.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any

from kagari_metadata_v2_pr_checkpoint import V2PRCheckpoint, render_checkpoint


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class D2P0Rejected(ValueError):
    """Fail closed: original creator provenance cannot be positively proven."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(ok: object, code: str) -> None:
    # Comparisons below check exact types; final nonempty string checks may
    # return that string rather than the literal boolean True in Python.
    if not ok:
        raise D2P0Rejected(code)


@dataclass(frozen=True, slots=True)
class OriginalOperation:
    """An *expected* scope assembled from the exact original #140 plan."""

    repository_id: int
    repository: str
    issue: int
    operation_id: str
    contract_id: str
    pr_number: int
    principal: int
    subject: str
    snapshot_id: str
    plan_sha256: str

    @classmethod
    def from_plan(cls, *, repository_id: int, repository: str, issue: int,
                  operation_id: str, contract_id: str, pr_number: int,
                  principal: int, subject: str, snapshot_id: str,
                  plan: dict[str, Any]) -> OriginalOperation:
        _require(type(plan) is dict and
                 plan.get("operation_id") == operation_id,
                 "D2P0_ORIGINAL_PLAN_INVALID")
        result = cls(repository_id, repository, issue, operation_id,
                     contract_id, pr_number, principal, subject, snapshot_id,
                     _sha(_canonical(plan)))
        _require(type(repository_id) is int and repository_id > 0 and
                 type(issue) is int and issue > 0 and
                 type(pr_number) is int and pr_number > 0 and
                 type(principal) is int and principal > 0 and
                 type(repository) is str and repository and
                 type(operation_id) is str and
                 re.fullmatch(r"[0-9a-f]{32}", operation_id) is not None and
                 type(contract_id) is str and contract_id and
                 type(subject) is str and subject and
                 type(snapshot_id) is str and snapshot_id,
                 "D2P0_ORIGINAL_SCOPE_INVALID")
        return result


@dataclass(frozen=True, slots=True)
class HostClaim:
    """Host-attested facts. Their authenticity is Host's external obligation."""

    operation: OriginalOperation
    issuer: str
    epoch: int
    issue_anchor: str
    pr_stream_anchor: str
    exclusive_egress: bool
    complete_history: bool
    prior_worker_quiescent: bool
    # The Host certifies complete monotone epoch lineage and individually
    # qualified ACK issuance epochs. Historic ACKs do not authorize a NEW
    # send/ACK by a revoked worker. This is verified Host evidence, not a
    # guessed numeric ack.epoch <= current_epoch shortcut.
    epoch_lineage: tuple[int, ...] = (1,)
    valid_ack_epochs: tuple[int, ...] = (1,)


@dataclass(frozen=True, slots=True)
class HostPostAck:
    """Durable verified response of the actual credential-bearing POST."""

    operation: OriginalOperation
    issuer: str
    epoch: int
    attempt_id: str
    pr_number: int
    principal: int
    comment_id: int
    checkpoint_key: str
    body_sha256: str


def check_claim(expected: OriginalOperation, witness: object) -> HostClaim:
    _require(type(witness) is HostClaim, "D2P0_HOST_CLAIM_REQUIRED")
    claim = witness
    _require(type(claim.operation) is OriginalOperation and
             _canonical(asdict(claim.operation)) == _canonical(asdict(expected)) and
             type(claim.issuer) is str and 1 <= len(claim.issuer) <= 128 and
             type(claim.epoch) is int and 0 < claim.epoch <= (1 << 63)-1 and
             type(claim.issue_anchor) is str and claim.issue_anchor ==
             _sha(_canonical([expected.repository_id, expected.issue])) and
             type(claim.pr_stream_anchor) is str and claim.pr_stream_anchor ==
             _sha(_canonical([expected.repository_id, expected.pr_number, "initial"])) and
             claim.exclusive_egress is True and
             claim.complete_history is True and
             claim.prior_worker_quiescent is True and
             type(claim.epoch_lineage) is tuple and
             1 <= len(claim.epoch_lineage) <= 32 and
             all(type(n) is int and 0 < n <= claim.epoch
                 for n in claim.epoch_lineage) and
             tuple(sorted(set(claim.epoch_lineage))) == claim.epoch_lineage and
             claim.epoch_lineage[-1] == claim.epoch and
             type(claim.valid_ack_epochs) is tuple and
             1 <= len(claim.valid_ack_epochs) <= len(claim.epoch_lineage) and
             all(type(n) is int and n in claim.epoch_lineage
                 for n in claim.valid_ack_epochs) and
             len(set(claim.valid_ack_epochs)) == len(claim.valid_ack_epochs),
             "D2P0_HOST_CLAIM_UNQUALIFIED")
    return claim


def check_ack(expected: OriginalOperation, claim: HostClaim,
              capsule: V2PRCheckpoint, comment_id: int,
              witness: object) -> HostPostAck:
    _require(type(witness) is HostPostAck, "D2P0_DURABLE_ACK_REQUIRED")
    ack = witness
    _require(type(capsule) is V2PRCheckpoint and
             capsule.repository_id == expected.repository_id and
             capsule.repository == expected.repository and
             capsule.issue == expected.issue and
             capsule.pr_number == expected.pr_number and
             capsule.principal == expected.principal and
             capsule.contract_id == expected.contract_id and
             capsule.subject == expected.subject and
             capsule.snapshot_id == expected.snapshot_id and
             capsule.boundary == "initial" and
             type(comment_id) is int and comment_id > 0,
             "D2P0_CHECKPOINT_SCOPE_INVALID")
    _require(type(ack.operation) is OriginalOperation and
             _canonical(asdict(ack.operation)) == _canonical(asdict(expected)) and
             type(ack.issuer) is str and ack.issuer == claim.issuer and
             type(ack.epoch) is int and
             ack.epoch in claim.valid_ack_epochs and
             type(ack.attempt_id) is str and
             re.fullmatch(r"[0-9a-f]{32}", ack.attempt_id) is not None and
             type(ack.pr_number) is int and ack.pr_number == capsule.pr_number and
             type(ack.principal) is int and ack.principal == capsule.principal and
             type(ack.comment_id) is int and ack.comment_id == comment_id and
             type(ack.checkpoint_key) is str and ack.checkpoint_key == capsule.key and
             type(ack.body_sha256) is str and
             ack.body_sha256 == _sha(render_checkpoint(capsule).encode("utf-8")),
             "D2P0_ACK_MISMATCH")
    return ack


@dataclass(frozen=True, slots=True)
class D2P0HostGateway:
    """Host-supplied scoped source-only callbacks; NOT a Host trust root.

    assert_entry is *read-only* and must positively qualify Host control
    before any #140 plan or Metadata effect. establish_claim is the Host
    durable compare-and-claim after the original plan is fsynced. post is
    the Host's only credential-bearing egress; independent read_ack accesses
    Host-private durable evidence. The source-only adapter cannot enforce
    these obligations against a compromised or non-cooperating Host.
    """

    assert_entry: Callable[[OriginalOperation], HostClaim]
    establish_claim: Callable[[OriginalOperation, bool], HostClaim]
    post: Callable[[OriginalOperation, V2PRCheckpoint, int, str, float], object]
    read_ack: Callable[[OriginalOperation, V2PRCheckpoint, int], HostPostAck | None]

    def __post_init__(self) -> None:
        _require(all(callable(c) for c in
                     (self.assert_entry, self.establish_claim,
                      self.post, self.read_ack)),
                 "D2P0_HOST_GATEWAY_INVALID")
