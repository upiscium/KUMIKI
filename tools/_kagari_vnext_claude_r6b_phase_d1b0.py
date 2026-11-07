"""D1B R0: invented, UNTRUSTED protocol shapes; no storage, effects or authority.

Narrow extension of D1A: exact-head CAS preview and read-only crash observation,
not another receipt-chain verifier. Digest fields are opaque synthetic pins, not
computed commitments or authentication. Even a self-consistent universe grants
nothing. There is no genesis, commit, repair, retry or effect interface.
"""
from dataclasses import dataclass
from tools._kagari_vnext_claude_r6b_phase_d1a import SyntheticBinding

SYNTHETIC = "R6B_D1B0_SYNTHETIC_TEST_ONLY_UNTRUSTED"
MAX_SEQUENCE = 2**63 - 1
STEPS = ("RESERVATION_WRITE", "RESERVATION_FSYNC", "PENDING_WRITE", "PENDING_FSYNC",
         "EFFECT_BOUNDARY", "RESULT_WRITE", "RESULT_FSYNC", "ACKNOWLEDGED")


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticTombstone:
    attempt_id: str  # Digest-only attempt identity; never a routing key.
    intent_sha256: str
    policy_sha256: str
    authority_epoch: int
    reservation_sha256: str
    pending_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticHead:
    binding: SyntheticBinding
    sequence: int  # Zero denotes a supplied genesis SHAPE, never proven absence.
    generation: int
    digest: str
    tombstones: tuple[SyntheticTombstone, ...]
    inventory: tuple[str, ...]  # Ordered reservation, pending, result digest pins.


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticRegistry:
    """Future HOST-OWNED mapping domain, invented and untrusted here."""
    bindings: tuple[SyntheticBinding, ...]
    root_state: str = "PRESENT"  # A claim, not path/topology evidence.


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticAnchor:
    """Future INDEPENDENT monotonic domain; this type proves no independence."""
    heads: tuple[SyntheticHead, ...]
    acknowledged_head: SyntheticHead | None  # Anchor-store ack, not result ack.


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticRestart:
    tombstone: SyntheticTombstone
    inventory: tuple[str, ...] | None
    events: tuple[str, ...]  # Exact prefix of STEPS; claims, NOT fsync/effect proof.
    result_sha256: str | None = None


def _report(reason, stage="NOT_OBSERVED"):
    return {"status": "UNKNOWN" if stage == "NOT_OBSERVED" else "UNQUALIFIED",
            "qualification": "UNQUALIFIED", "dispatch_allowed": False,
            "replay_allowed": False, "task_completed": False,
            "authenticated_absence": False, "commit_allowed": False,
            "effective_policy": "UNVERIFIED", "evidence": SYNTHETIC,
            "reason": reason, "stage": stage}


def _sha(v):
    return type(v) is str and len(v) == 64 and all(c in "0123456789abcdef" for c in v)


def _number(v, minimum=1, maximum=MAX_SEQUENCE):
    return type(v) is int and minimum <= v <= maximum


def _tuple(v, maximum):
    return type(v) is tuple and len(v) <= maximum


def _binding(v):
    return (type(v) is SyntheticBinding and _sha(v.repository_id) and _sha(v.root_id)
            and _number(v.issue, maximum=2**31 - 1) and _number(v.epoch))


def _tombstone(v):
    return (type(v) is SyntheticTombstone and _number(v.authority_epoch)
            and all(_sha(x) for x in (v.attempt_id, v.intent_sha256, v.policy_sha256,
                                     v.reservation_sha256, v.pending_sha256)))


def _inventory(v):
    return _tuple(v, 3) and all(_sha(x) for x in v)


def _head(v):
    return (type(v) is SyntheticHead and _binding(v.binding) and _number(v.sequence, 0)
            and _number(v.generation) and _sha(v.digest) and _tuple(v.tombstones, 2)
            and all(_tombstone(t) for t in v.tombstones) and _inventory(v.inventory))


def _difference(expected, observed):
    if (expected.repository_id, expected.issue) != (observed.repository_id, observed.issue):
        return "repository_issue_mismatch"
    if expected.root_id != observed.root_id:
        return "root_identity_mismatch"
    return "epoch_mismatch" if expected.epoch != observed.epoch else ""


def _state(head):
    ts = head.tombstones
    if len({t.attempt_id for t in ts}) != len(ts):
        return "duplicate_attempts"
    if len(ts) > 1:
        return "outstanding_attempt_conflict"
    if not ts:
        if head.inventory:
            return "tombstone_missing"
        return "genesis_shape_invalid" if (head.sequence, head.generation) != (0, 1) else ""
    if head.sequence == 0 or head.generation == 1:
        return "tombstone_at_genesis"
    if (head.sequence, head.generation) != (len(head.inventory) + 1, len(head.inventory) + 2):
        return "head_counter_inventory_mismatch"
    pins = (ts[0].reservation_sha256, ts[0].pending_sha256)
    return "anchor_inventory_mismatch" if head.inventory[:2] != pins[:len(head.inventory)] else ""


def _context(binding, registry, anchor):
    if not _binding(binding):
        return "binding_invalid"
    if registry is None:
        return "registry_missing"
    if (type(registry) is not SyntheticRegistry or not _tuple(registry.bindings, 2)
            or not all(_binding(b) for b in registry.bindings)
            or type(registry.root_state) is not str):
        return "registry_invalid"
    if not registry.bindings:
        return "root_missing"
    if len(registry.bindings) != 1:
        return "competing_roots"
    mismatch = _difference(binding, registry.bindings[0])
    if mismatch:
        return mismatch
    if registry.root_state != "PRESENT":
        return {"LOST": "root_lost", "MOVED": "root_moved", "RECREATED": "root_recreated",
                "ALIAS_MIGRATION": "alias_migration_unqualified"}.get(registry.root_state, "registry_invalid")
    if anchor is None:
        return "anchor_missing"
    if type(anchor) is not SyntheticAnchor or not _tuple(anchor.heads, 2):
        return "anchor_invalid"
    if not anchor.heads:
        return "anchor_missing"
    if not all(_head(h) for h in anchor.heads):
        return "anchor_invalid"
    if len(anchor.heads) != 1:
        return "competing_anchor_heads"
    head = anchor.heads[0]
    mismatch = _difference(binding, head.binding)
    if mismatch:
        return mismatch
    if anchor.acknowledged_head is None:
        return "anchor_ack_missing"
    if not _head(anchor.acknowledged_head):
        return "anchor_ack_invalid"
    if anchor.acknowledged_head != head:
        return "anchor_store_ack_divergence"
    return _state(head)


def _compare(binding, anchor, expected, cas=False):
    if expected is None:
        return "expected_head_missing"
    if not _head(expected):
        return "expected_head_invalid"
    mismatch = _difference(binding, expected.binding)
    if mismatch:
        return mismatch
    head = anchor.heads[0]
    if head == expected:
        return ""
    if cas:
        return "cas_expected_stale"
    if head.sequence < expected.sequence or head.generation < expected.generation:
        return "anchor_stale"
    if head.sequence > expected.sequence or head.generation > expected.generation:
        return "anchor_ahead"
    return "anchor_fork"


def evaluate_binding_and_anchor(binding, registry, anchor, expected, inventory):
    """Compare separately supplied synthetic domains; no trusted absence or route."""
    try:
        reason = _context(binding, registry, anchor) or _compare(binding, anchor, expected)
        if reason:
            return _report(reason)
        if inventory is None:
            return _report("receipt_inventory_missing")
        if not _inventory(inventory):
            return _report("receipt_inventory_invalid")
        if inventory != anchor.heads[0].inventory:
            return _report("receipt_inventory_differs")
        return _report("observed_only", "BINDING_ANCHOR_OBSERVED_ONLY")
    except AttributeError:
        return _report("malformed_input")


def preview_anchor_cas(binding, registry, anchor, expected_old, proposed):
    """Exact compare + one monotonic successor SHAPE; never update or return a head.

    First claim retains one exact tombstone BEFORE receipt writes. Subsequent
    previews only append one receipt pin; no second attempt, even after a result.
    """
    try:
        reason = _context(binding, registry, anchor) or _compare(binding, anchor, expected_old, True)
        if reason:
            return _report(reason)
        if not _head(proposed):
            return _report("proposed_head_invalid")
        reason = _difference(binding, proposed.binding)
        if reason:
            return _report(reason)
        old = anchor.heads[0]
        if old.sequence == MAX_SEQUENCE or old.generation == MAX_SEQUENCE:
            return _report("cas_counter_exhausted")
        if (proposed.sequence, proposed.generation) != (old.sequence + 1, old.generation + 1):
            return _report("cas_counter_not_successor")
        if proposed.digest == old.digest:
            return _report("cas_digest_unchanged")
        if old.tombstones and proposed.tombstones != old.tombstones:
            return _report("outstanding_attempt_blocks_retry")
        reason = _state(proposed)
        if reason:
            return _report(reason)
        if not old.tombstones:
            if len(proposed.tombstones) != 1 or proposed.inventory:
                return _report("cas_claim_requires_tombstone_only")
        elif (len(proposed.inventory) != len(old.inventory) + 1
              or proposed.inventory[:-1] != old.inventory):
            return _report("cas_inventory_not_append")
        return _report("observed_only", "CAS_PREVIEW_OBSERVED_ONLY")
    except AttributeError:
        return _report("malformed_input")


def classify_restart(binding, registry, anchor, expected, observation):
    """Read-only prefix classification. An effect-boundary claim means UNKNOWN effect."""
    try:
        if (type(observation) is not SyntheticRestart or not _tombstone(observation.tombstone)
                or not _tuple(observation.events, len(STEPS))
                or not all(type(e) is str for e in observation.events)
                or (observation.result_sha256 is not None and not _sha(observation.result_sha256))):
            return _report("restart_invalid")
        result = evaluate_binding_and_anchor(binding, registry, anchor, expected, observation.inventory)
        if result["stage"] == "NOT_OBSERVED":
            return result
        head = anchor.heads[0]
        if not head.tombstones:
            return _report("tombstone_missing" if observation.events else "never_seen_not_proven")
        if head.tombstones[0] != observation.tombstone:
            return _report("outstanding_attempt_blocks_retry")
        count = len(observation.events)
        if observation.events != STEPS[:count]:
            return _report("restart_order_invalid")
        if (count >= 6) != (observation.result_sha256 is not None):
            return _report("result_presence_mismatch")
        t = observation.tombstone
        pins = (t.reservation_sha256, t.pending_sha256, observation.result_sha256)
        size = 3 if count >= 6 else 2 if count >= 3 else 1 if count else 0
        if observation.inventory != pins[:size]:
            return _report("restart_inventory_mismatch")
        reasons = ("before_reservation_write", "reservation_unsynced", "reservation_sync_claimed",
                   "pending_unsynced", "pending_before_effect", "effect_unknown",
                   "result_unsynced", "result_ack_unknown", "observed_only")
        return _report(reasons[count], "CLOSED_SHAPE_OBSERVED_ONLY" if count == 8 else "NOT_OBSERVED")
    except AttributeError:
        return _report("malformed_input")
