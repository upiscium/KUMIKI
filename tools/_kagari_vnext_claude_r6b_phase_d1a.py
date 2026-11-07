"""SOURCE-ONLY / TEST-ONLY pure observations, never host authority or permission.

Every witness, including the separately supplied reference, is UNTRUSTED synthetic
data. Exact Python types and unkeyed digests check shape/linkage, NOT attestation,
completeness, durability, freshness or absence of prior effects. No I/O or wiring.
"""
from dataclasses import asdict, dataclass
import hashlib
import json

SYNTHETIC = "R6B_D1A_SYNTHETIC_TEST_ONLY_UNTRUSTED"
MAX_ITEMS = 8
MAX_SEQUENCE = 2**63 - 1


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticBinding:
    repository_id: str  # Opaque canonical sha256 identity, never a remote/path alias.
    issue: int
    root_id: str  # Identity only: no path, resolver or root allocator exists here.
    epoch: int


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticRecord:
    sequence: int
    sha256: str
    predecessor_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticAttempt:
    attempt_id: str  # Digest of a fake attempt identity; never a routing key.
    manifest_sha256: str | None
    reservation: SyntheticRecord | None
    pending: SyntheticRecord | None
    result: SyntheticRecord | None
    acknowledged: bool


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticAttemptPins:
    attempt_id: str
    manifest_sha256: str
    reservation_sha256: str
    pending_sha256: str
    result_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticCheckpointProposal:
    binding: SyntheticBinding
    sequence: int
    inventory_sha256: str
    source: str = SYNTHETIC


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticCheckpointReference:
    """Models a deferred independent trust-anchor interface; NOT authenticated."""
    binding: SyntheticBinding
    sequence: int
    checkpoint_sha256: str
    source: str = SYNTHETIC


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticRegistrySnapshot:
    """One repository+Issue slice, not a registry implementation or full host scan."""
    roots: tuple[SyntheticBinding, ...]
    checkpoints: tuple[SyntheticCheckpointProposal, ...]
    inventory: tuple[SyntheticAttempt, ...] | None
    source: str = SYNTHETIC


def _report(reason: str, stage: str = "NOT_OBSERVED") -> dict[str, str | bool]:
    return {
        "status": "UNKNOWN" if stage == "NOT_OBSERVED" else "UNQUALIFIED",
        "qualification": "UNQUALIFIED", "dispatch_allowed": False,
        "task_completed": False, "replay_allowed": False,
        "authenticated_absence": False, "effective_policy": "UNVERIFIED",
        "ask_capability": "UNQUALIFIED", "evidence": SYNTHETIC,
        "reason": reason, "stage": stage,
    }


def _sha(value: object) -> bool:
    return (type(value) is str and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def _positive(value: object, maximum: int = MAX_SEQUENCE) -> bool:
    return type(value) is int and 1 <= value <= maximum


def _bounded(value: object) -> bool:
    return type(value) is tuple and len(value) <= MAX_ITEMS


def _source(value: object) -> bool:
    return type(value) is str and value == SYNTHETIC


def _binding(value: object) -> bool:
    return (type(value) is SyntheticBinding and _sha(value.repository_id)
            and _positive(value.issue, 2**31 - 1) and _sha(value.root_id)
            and _positive(value.epoch))


def _record(value: object) -> bool:
    return value is None or (type(value) is SyntheticRecord and _positive(value.sequence)
                            and _sha(value.sha256) and _sha(value.predecessor_sha256))


def _attempt(value: object) -> bool:
    return (type(value) is SyntheticAttempt and _sha(value.attempt_id)
            and (value.manifest_sha256 is None or _sha(value.manifest_sha256))
            and all(_record(r) for r in (value.reservation, value.pending, value.result))
            and type(value.acknowledged) is bool)


def _checkpoint(value: object, kind: type) -> bool:
    return (type(value) is kind and _binding(value.binding) and _positive(value.sequence)
            and _source(value.source) and _sha(value.inventory_sha256 if
                kind is SyntheticCheckpointProposal else value.checkpoint_sha256))


def _digest(value: object) -> str:
    # Called only on bounded validated data; public entrypoints do not deserialize.
    data = json.dumps([SYNTHETIC, value], sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")
    return hashlib.sha256(data).hexdigest()


def _binding_difference(expected: SyntheticBinding, observed: SyntheticBinding) -> str:
    if (expected.repository_id, expected.issue) != (observed.repository_id, observed.issue):
        return "repository_issue_mismatch"
    if expected.root_id != observed.root_id:
        return "root_mismatch"
    if observed.epoch < expected.epoch:
        return "epoch_rollback"
    return "epoch_mismatch" if observed.epoch != expected.epoch else ""


def _routing(binding: object, snapshot: object, reference: object) -> str:
    if not _binding(binding):
        return "binding_invalid"
    if snapshot is None:
        return "registry_missing"
    if type(snapshot) is not SyntheticRegistrySnapshot or not _source(snapshot.source):
        return "registry_invalid"
    if not _bounded(snapshot.roots):
        return "roots_invalid"
    if not snapshot.roots:
        return "root_missing"
    if len(snapshot.roots) != 1:
        return "multiple_roots"
    if not _binding(snapshot.roots[0]):
        return "root_invalid"
    mismatch = _binding_difference(binding, snapshot.roots[0])
    if mismatch:
        return mismatch
    if not _bounded(snapshot.checkpoints):
        return "checkpoints_invalid"
    if not snapshot.checkpoints:
        return "checkpoint_deleted_or_missing"
    if len(snapshot.checkpoints) != 1:
        return "duplicate_checkpoint"
    proposal = snapshot.checkpoints[0]
    if not _checkpoint(proposal, SyntheticCheckpointProposal):
        return "checkpoint_invalid"
    if reference is None:
        return "checkpoint_reference_missing"
    if not _checkpoint(reference, SyntheticCheckpointReference):
        return "checkpoint_reference_invalid"
    for observed in (proposal.binding, reference.binding):
        mismatch = _binding_difference(binding, observed)
        if mismatch:
            return mismatch
    if proposal.sequence < reference.sequence:
        return "checkpoint_rolled_back"
    if proposal.sequence > reference.sequence:
        return "checkpoint_gap"
    if _digest(asdict(proposal)) != reference.checkpoint_sha256:
        return "checkpoint_fork"
    inventory = snapshot.inventory
    if inventory is None:
        return "inventory_missing"
    if not _bounded(inventory) or not all(_attempt(a) for a in inventory):
        return "inventory_invalid"
    if _digest([asdict(a) for a in inventory]) != proposal.inventory_sha256:
        return "inventory_corrupt"
    if len({a.attempt_id for a in inventory}) != len(inventory):
        return "duplicate_attempts"
    return "" if inventory else "never_seen_not_proven"


def observe_routing(binding: object, snapshot: object, reference: object) -> dict[str, str | bool]:
    """Compare synthetic pins without returning a route, allocating or initializing."""
    try:
        reason = _routing(binding, snapshot, reference)
        return _report(reason) if reason else _report("observed_only", "ROUTING_OBSERVED_ONLY")
    except AttributeError:  # Includes deliberately uninitialized exact-type objects.
        return _report("malformed_input")


def reconcile(binding: object, snapshot: object, reference: object,
              pins: object) -> dict[str, str | bool]:
    """Read-only single-attempt MVI. Even closed history cannot grant a new attempt.

    Pins represent exact invented manifest/reservation/pending/result digests, not
    real receipt parsing, policy verification or trusted completion assertions.
    """
    try:
        if type(pins) is not SyntheticAttemptPins or not all(_sha(v) for v in (
                pins.attempt_id, pins.manifest_sha256, pins.reservation_sha256,
                pins.pending_sha256, pins.result_sha256)):
            return _report("attempt_pins_invalid")
        reason = _routing(binding, snapshot, reference)
        if reason:
            return _report(reason)
        if len(snapshot.inventory) != 1:
            return _report("extra_attempt")
        attempt = snapshot.inventory[0]
        if attempt.attempt_id != pins.attempt_id:
            return _report("attempt_mismatch")
        if attempt.manifest_sha256 is None:
            return _report("manifest_missing")
        if attempt.manifest_sha256 != pins.manifest_sha256:
            return _report("manifest_mismatch")
        r, p, t = attempt.reservation, attempt.pending, attempt.result
        for name, record, digest, predecessor in (
            ("reservation", r, pins.reservation_sha256, pins.manifest_sha256),
            ("pending", p, pins.pending_sha256, pins.reservation_sha256),
            ("result", t, pins.result_sha256, pins.pending_sha256),
        ):
            if record is not None and (record.sha256 != digest or record.predecessor_sha256 != predecessor):
                return _report(name + "_mismatch")
        records = tuple(v for v in (r, p, t) if v is not None)
        if any(v.sequence > snapshot.checkpoints[0].sequence for v in records):
            return _report("record_after_checkpoint")
        if any(a.sequence >= b.sequence for a, b in zip(records, records[1:])):
            return _report("record_order_invalid")
        if len(records) == 3 and (r.sequence != 1 or t.sequence != snapshot.checkpoints[0].sequence
                or any(b.sequence != a.sequence + 1 for a, b in zip(records, records[1:]))):
            return _report("record_gap")
        if attempt.acknowledged and t is None:
            return _report("acknowledgement_without_result")
        if t is not None and p is None:
            return _report("result_only")
        if p is not None and t is None:
            return _report("pending_only")
        if p is None and t is None:
            return _report("reservation_only" if r is not None else "never_seen_not_proven")
        if r is None:
            return _report("reservation_missing")
        if not attempt.acknowledged:
            return _report("ack_lost")
        return _report("observed_only", "CLOSED_HISTORY_OBSERVED_ONLY")
    except AttributeError:
        return _report("malformed_input")
