"""#340 R6B D1B1 R1: pure UNTRUSTED Host binding diagnostics.

SOURCE-ONLY / SYNTHETIC / NON-WIRED / NO FILESYSTEM, GIT OR TRANSPORT.
Neither the supplied "Host" nor "Main" is actually authenticated. An
observational match never constitutes provenance, authority, or permission.
No root discovery, reservation, journal read/create, model launch or retry.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

PROFILE = "r6b-d1b1-r1"
SCOPE = "R6B_D1B1_R1_SYNTHETIC_UNTRUSTED"
GENERATION = "ONE_SHOT_V1"
KEY_DOMAIN = b"kagari/claude/r6b-d1b1/reservation/v1" + bytes([10])
ROOT_DOMAIN = b"kagari/claude/r6b-d1b1/root/v1" + bytes([10])
REPO_MAX = 10**12
ISSUE_MAX = 2**31 - 1
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REPO = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+\Z", re.ASCII)
_ROOT_STATES = frozenset(("PRESENT", "MISSING", "MOVED", "REPLACED", "MULTIPLE", "UNSAFE"))
_RECORD_STATES = frozenset(("ABSENT", "RESERVED", "RESULT", "PARTIAL", "DIVERGENT", "ACK_LOST"))


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticBinding:
    """Invented, UNTRUSTED Host-supplied Task binding, not a capability."""
    repository_id: int
    repository_name: str
    issue: int
    contract_digest: str
    task_digest: str
    profile: str = PROFILE


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticMainObservation:
    """Invented selected Task and lock-held claim, never a MainSupervisor."""
    repository_id: int
    repository_name: str
    issue: int
    contract_digest: str
    task_digest: str
    owner_claim: str = "HELD"


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticJournalObservation:
    """Invented root and record states. No real directory is inspected."""
    root_state: str
    directory_digest: str | None
    record_state: str


def _valid_number(v: object, maximum: int) -> bool:
    return type(v) is int and 1 <= v <= maximum


def _valid_digest(v: object) -> bool:
    return type(v) is str and _SHA.fullmatch(v) is not None


def _valid_name(v: object) -> bool:
    return (type(v) is str and 3 <= len(v) <= 260
            and _REPO.fullmatch(v) is not None
            and v.split("/", 1)[1] not in (".", ".."))


def _canonical(v: object) -> bytes:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _identity(repository_id: object, issue: object) -> None:
    if not _valid_number(repository_id, REPO_MAX) or not _valid_number(issue, ISSUE_MAX):
        raise ValueError("SYNTHETIC_IDENTITY_INVALID")


def _sha256(domain: bytes, values: list[object]) -> str:
    return hashlib.sha256(domain + _canonical(values)).hexdigest()


def reservation_key(repository_id: object, issue: object) -> str:
    """Stable shape-only key; NEVER return it as an executable grant."""
    _identity(repository_id, issue)
    return _sha256(KEY_DOMAIN, [repository_id, issue, GENERATION])


def root_component(repository_id: object, issue: object) -> str:
    """Numeric ID+Issue only, NEVER a host path or a root resolver."""
    _identity(repository_id, issue)
    return _sha256(ROOT_DOMAIN, [repository_id, issue])


def _binding(v: object) -> bool:
    return (type(v) is SyntheticBinding
            and _valid_number(v.repository_id, REPO_MAX)
            and _valid_number(v.issue, ISSUE_MAX)
            and _valid_name(v.repository_name)
            and _valid_digest(v.contract_digest)
            and _valid_digest(v.task_digest)
            and type(v.profile) is str and v.profile == PROFILE)


def _selection(v: object) -> bool:
    return (type(v) is SyntheticMainObservation
            and _valid_number(v.repository_id, REPO_MAX)
            and _valid_number(v.issue, ISSUE_MAX)
            and _valid_name(v.repository_name)
            and _valid_digest(v.contract_digest)
            and _valid_digest(v.task_digest)
            and type(v.owner_claim) is str
            and v.owner_claim in ("HELD", "MISSING", "STALE"))


def _journal(v: object) -> bool:
    if not (type(v) is SyntheticJournalObservation
            and type(v.root_state) is str and v.root_state in _ROOT_STATES
            and (v.directory_digest is None or _valid_digest(v.directory_digest))
            and type(v.record_state) is str and v.record_state in _RECORD_STATES):
        return False
    # Internal consistency only; these synthetic claims remain UNTRUSTED.
    if v.root_state == "PRESENT":
        return v.directory_digest is not None
    return (v.directory_digest is None
            and v.record_state not in ("RESERVED", "RESULT"))


def _report(status: str, reason: str) -> dict[str, str | bool]:
    # Closed, entirely non-grant result. Mutable dict is diagnostic only.
    return {"status": status, "reason": reason, "qualification": "UNQUALIFIED",
            "evidence": SCOPE, "stage": "OBSERVATION_ONLY",
            "dispatch_allowed": False, "task_completed": False,
            "replay_allowed": False, "commit_allowed": False,
            "authenticated_absence": False, "effective_policy": "UNVERIFIED"}


def observe_synthetic_host_binding(
    binding: object, selected: object, journal: object,
) -> dict[str, str | bool]:
    """Observe three fabricated witnesses. No authority, even on exact match.

    REFUSED: invalid/mismatched binding, selected Task, root identity or profile.
    UNKNOWN: absent root/history, ambiguous/partial result or untrusted evidence.
    OBSERVED_BLOCKED: supplied retained reservation/result; no replay or grant.
    """
    try:
        if not _binding(binding):
            return _report("REFUSED", "binding_invalid")
        if not _selection(selected):
            return _report("REFUSED", "main_selection_invalid")
        if selected.owner_claim != "HELD":
            return _report("REFUSED", "main_owner_not_proven")
        if (binding.repository_id, binding.issue, binding.contract_digest,
                binding.task_digest) != (selected.repository_id, selected.issue,
                                        selected.contract_digest, selected.task_digest):
            return _report("REFUSED", "task_identity_mismatch")
        # A name is not a routing key; this can only catch a supplied mismatch.
        # Real rename/transfer cannot be detected without a trusted Host.
        if binding.repository_name != selected.repository_name:
            return _report("REFUSED", "repository_name_mismatch_unqualified")
        if journal is None:
            return _report("UNKNOWN", "journal_observation_missing")
        if not _journal(journal):
            return _report("REFUSED", "journal_observation_invalid")
        if journal.root_state == "MISSING":
            return _report("UNKNOWN", "root_missing_not_genesis")
        if journal.root_state != "PRESENT":
            return _report("REFUSED", "root_unqualified")
        if journal.directory_digest != root_component(binding.repository_id, binding.issue):
            return _report("REFUSED", "root_component_mismatch")
        if journal.record_state == "ABSENT":
            return _report("UNKNOWN", "record_absence_not_authenticated")
        if journal.record_state in ("PARTIAL", "DIVERGENT", "ACK_LOST"):
            return _report("UNKNOWN", "reconciliation_required")
        return _report("OBSERVED_BLOCKED", "retained_attempt_observed_only")
    except Exception:
        return _report("UNKNOWN", "malformed_untrusted_witness")
