"""#337 Phase 3A: pure, untrusted offline candidate and NOT_RUN receipt codec.

No network, subprocess, Git, file I/O, tokens, Host callbacks, Ruleset
mutation, ref deletion, or deployment interfaces are implemented here.
Parsing a self-reported candidate is NOT proof of GitHub identity, permissions,
remote Ruleset enforcement, synthetic provenance, or approval.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

ARCHIVE = "refs/heads/kagari/metadata"
_PREFIX = "refs/heads/kagari-metadata/"
_MAX_INPUT_BYTES = 32 * 1024
_MAX_MANIFEST_BYTES = 16 * 1024
_ID_LIMIT = (1 << 53) - 1
_SHA = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z", re.ASCII)
_ISSUE = re.compile(r"[1-9][0-9]{0,127}\Z", re.ASCII)
_KUMIKI_REPOSITORY_ID = 1183254623  # Canonical production ID in README.md; deny aliasing.
_ALLOWED_ROLES = ("archive_writer", "negative_actor")
_AC_GATES = (
    ("AC-META2-01", "P3B1/P3B2", "BLOCKED"),
    ("AC-META2-02", "P3B2", "NOT_RUN"),
    ("AC-META2-03", "P3B2", "NOT_RUN"),
    ("AC-META2-04", "P3B2/P3C/P3D", "BLOCKED"),
    ("AC-META2-05", "P3B2", "BLOCKED"),
    ("AC-META2-06", "P3B2/P3C", "BLOCKED"),
    ("AC-META2-07", "P3C/P3D", "BLOCKED"),
    ("AC-META2-08", "P3B1/P3B2/P3C/P3D", "BLOCKED"),
    ("AC-META2-09", "P3B2", "BLOCKED"),
    ("AC-META2-10", "P3B2/P3C/P3D", "BLOCKED"),
    ("AC-META2-11", "P3D/PHASE4", "BLOCKED"),
)


class OfflinePreflightRefused(ValueError):
    """Ill-shaped, inconsistent or unsafe self-reported offline input."""


def _need(value: bool, reason: str) -> None:
    if not value:
        raise OfflinePreflightRefused(reason)


def _exact(obj: object, keys: set[str], reason: str) -> dict:
    _need(type(obj) is dict and obj.keys() == keys, reason)
    return obj


def _positive(value: object) -> bool:
    return type(value) is int and 0 < value <= _ID_LIMIT


def _match(value: object, pattern: re.Pattern[str]) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _safe_name(value: object) -> bool:
    if not _match(value, _NAME):
        return False
    return all(part not in (".", "..") and not part.startswith(".")
               for part in value.split("/"))


def _issue_ref(value: object) -> bool:
    return (type(value) is str and value.startswith(_PREFIX)
            and _match(value[len(_PREFIX):], _ISSUE))


def _unique_ids(values: object) -> bool:
    return (type(values) is list and all(_positive(v) for v in values)
            and len(values) == len(set(values)))


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key, value in pairs:
        _need(key not in found, "offline_duplicate_json_key")
        found[key] = value
    return found


def _reject_constant(_: str) -> None:
    raise OfflinePreflightRefused("offline_nonfinite_json_number")


def _require_utf8_tree(root: object) -> None:
    """Reject escaped unpaired surrogates *after* JSON decoding.

    Iterative walk also handles deeply nested attacker-owned objects without
    trusting the pre-decoding textual JSON representation.
    """
    pending: list[object] = [root]
    while pending:
        node = pending.pop()
        if type(node) is str:
            node.encode("utf-8", "strict")
        elif type(node) is dict:
            pending.extend(node.keys())
            pending.extend(node.values())
        elif type(node) is list:
            pending.extend(node)


def _loads(raw: bytes, *, max_size: int) -> dict:
    _need(type(raw) is bytes and 0 < len(raw) <= max_size,
          "offline_json_input_bytes_invalid")
    try:
        decoded = raw.decode("utf-8", "strict")
        value = json.loads(decoded, object_pairs_hook=_no_duplicate_keys,
                           parse_constant=_reject_constant)
        # JSON escape sequences (e.g. "\\ud800") become surrogates only
        # after decoding. Check values and keys, not just the source string.
        _require_utf8_tree(value)
    except (UnicodeError, json.JSONDecodeError, ValueError, TypeError,
            OverflowError, RecursionError) as error:
        if isinstance(error, OfflinePreflightRefused):
            raise
        raise OfflinePreflightRefused("offline_json_decoding_invalid") from error
    _need(type(value) is dict, "offline_json_object_required")
    return value


def _dump(value: dict) -> bytes:
    # The canonical receipt is a deterministic digestable *local record*,
    # not a signature, Host attestation, or immutable external evidence.
    return (json.dumps(value, sort_keys=True, ensure_ascii=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


def validate_candidate(raw: bytes) -> dict:
    """Check only the shape and internally consistent **claimed** policy.

    The output is NOT an authorization or a verified real GitHub readback.
    In particular, a complete-bypass-inventory flag is self-reported.
    """
    p = _loads(raw, max_size=_MAX_INPUT_BYTES)
    _exact(p, {"schema", "kind", "repository", "corpus", "refs",
               "actors", "rulesets", "gates"}, "offline_candidate_shape_invalid")
    _need(type(p["schema"]) is int and p["schema"] == 1
          and p["kind"] == "phase3a-offline-candidate",
          "offline_candidate_version_invalid")

    repo = _exact(p["repository"],
                  {"id", "name", "visibility", "object_format", "default_oid",
                   "disposable_claim"},
                  "offline_repository_shape_invalid")
    _need(_positive(repo["id"]) and _safe_name(repo["name"])
          and repo["name"].lower() != "upiscium/kumiki"
          and repo["id"] != _KUMIKI_REPOSITORY_ID
          and repo["visibility"] in ("private", "public")
          and type(repo["visibility"]) is str
          and repo["object_format"] == "sha1"
          and _match(repo["default_oid"], _SHA)
          and repo["disposable_claim"] is True,
          "offline_repository_identity_invalid")

    corpus = _exact(p["corpus"], {"origin", "sha256", "storage", "classification"},
                    "offline_corpus_shape_invalid")
    _need(corpus["origin"] == "independently_generated_synthetic"
          and _match(corpus["sha256"], _DIGEST)
          and corpus["storage"] == "offline-only"
          and corpus["classification"] == "synthetic_only",
          "offline_corpus_boundary_invalid")

    refs = _exact(p["refs"], {"archive", "issues"},
                  "offline_ref_scope_shape_invalid")
    _need(refs["archive"] == ARCHIVE
          and type(refs["issues"]) is list
          and len(refs["issues"]) == 2
          and all(_issue_ref(ref) for ref in refs["issues"])
          and refs["issues"] == sorted(set(refs["issues"])),
          "offline_ref_scope_invalid")

    actors = p["actors"]
    _need(type(actors) is list and len(actors) == len(_ALLOWED_ROLES),
          "offline_actor_inventory_invalid")
    role_ids: dict[str, int] = {}
    for entry in actors:
        _exact(entry, {"id", "role", "candidate_ref"},
               "offline_actor_shape_invalid")
        _need(_positive(entry["id"])
              and type(entry["role"]) is str
              and entry["role"] in _ALLOWED_ROLES
              and entry["role"] not in role_ids
              and entry["candidate_ref"] == ARCHIVE,
              "offline_actor_identity_invalid")
        role_ids[entry["role"]] = entry["id"]
    _need(len(set(role_ids.values())) == len(_ALLOWED_ROLES),
          "offline_actor_duplicate_id")

    rulesets = _exact(p["rulesets"], {"retention", "write_scope"},
                      "offline_ruleset_inventory_invalid")
    for name, required_rules in (("retention", {"deletion", "non_fast_forward"}),
                                 ("write_scope", {"creation", "update"})):
        rule = _exact(
            rulesets[name],
            {"id", "target_ref", "enforcement", "rules",
             "bypass_actor_ids", "inventory"},
            "offline_ruleset_shape_invalid")
        _need(_positive(rule["id"]) and rule["target_ref"] == ARCHIVE
              and rule["enforcement"] == "active"
              and type(rule["rules"]) is list
              and len(rule["rules"]) == len(required_rules)
              and all(type(x) is str for x in rule["rules"])
              and set(rule["rules"]) == required_rules
              and _unique_ids(rule["bypass_actor_ids"])
              and rule["inventory"] == "candidate_only",
              "offline_ruleset_policy_invalid")
    _need(rulesets["retention"]["id"] != rulesets["write_scope"]["id"]
          and rulesets["retention"]["bypass_actor_ids"] == []
          and rulesets["write_scope"]["bypass_actor_ids"]
          == [role_ids["archive_writer"]],
          "offline_ruleset_bypass_invalid")

    gates = _exact(p["gates"], {"external_read_proven",
                               "remote_write_authorized",
                               "conditional_delete_qualified"},
                   "offline_gate_shape_invalid")
    _need(all(value is False for value in gates.values()),
          "offline_external_gate_must_remain_false")
    return p


def build_not_run_manifest(candidate: bytes) -> bytes:
    """Make one reproducible, non-promotable P3A-only receipt; no side effects."""
    p = validate_candidate(candidate)
    report = {
        "schema": 1,
        "kind": "phase3a-offline-not-run",
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "repository_id": p["repository"]["id"],
        "repository_name": p["repository"]["name"],
        "synthetic_digest_claim": p["corpus"]["sha256"],
        "source": "untrusted_offline_candidate",
        "external_effects": "NOT_RUN",
        "external_proof": "NOT_RUN",
        "authorization": "NONE",
        "canary_ready": False,
        "results": [{"ac": ac, "gate": gate, "status": status, "evidence": []}
                    for ac, gate, status in _AC_GATES],
        "pending": ["authentic_repository_id", "effective_ruleset_and_bypass",
                    "actors_and_permissions", "real_host_owner",
                    "external_synthetic_provenance", "remote_atomic_cas",
                    "conditional_delete_adapter", "separate_human_grants"],
    }
    data = _dump(report)
    _need(len(data) <= _MAX_MANIFEST_BYTES,
          "offline_receipt_bytes_limit")
    return data


def validate_not_run_manifest(raw: bytes, candidate: bytes) -> dict:
    """Reject any tampered/promoted PASS, missing AC or extra receipt fields."""
    actual = _loads(raw, max_size=_MAX_MANIFEST_BYTES)
    expected_raw = build_not_run_manifest(candidate)
    _need(raw == expected_raw and actual == _loads(expected_raw,
                                                     max_size=_MAX_MANIFEST_BYTES),
          "offline_receipt_mismatch_or_promotion")
    return actual
