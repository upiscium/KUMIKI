"""Offline, bounded #271 TaskSpec/plan validation and deterministic wave receipts.

This module does not call a provider, execute tests, or dispatch agents. A ready
wave is an instruction to a separate coordinator, not evidence of dispatch.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

MAX_JSON_BYTES = 65536
MAX_TEXT = 4096
HEX = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SPEC_KEYS = {"schema_version", "goal", "base_revision", "allowed_paths",
             "tests", "acceptance", "subagent_count", "planning"}
PLAN_KEYS = {"schema_version", "base_revision", "steps"}
STEP_KEYS = {"id", "role", "agent", "depends_on", "allowed_paths", "instruction"}


class PlanError(ValueError):
    """Untrusted TaskSpec, plan, or transcript failed closed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PlanError(message)


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise PlanError(f"non-finite JSON value: {value}")


def parse_single_json(raw: str | bytes) -> Any:
    """One strict UTF-8 JSON value, no markdown, duplicate keys or trailing data."""
    require(type(raw) in (str, bytes), "JSON input must be text or bytes")
    try:
        text = raw.decode("utf-8", "strict") if isinstance(raw, bytes) else raw
        encoded = text.encode("utf-8", "strict")
        require(0 < len(encoded) <= MAX_JSON_BYTES, "JSON input exceeds byte limit or is empty")
        value = json.loads(text, object_pairs_hook=_unique_pairs,
                           parse_constant=_reject_constant)
        # The Python JSON decoder accepts escaped unpaired surrogates; UTF-8 does not.
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PlanError("invalid single UTF-8 JSON value") from exc


def _object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    require(type(value) is dict and set(value) == keys, f"invalid {label} keys")
    return value


def _text(value: Any, label: str) -> str:
    require(type(value) is str and bool(value.strip()) and len(value) <= MAX_TEXT
            and not any(ord(c) < 32 and c not in "\n\t" for c in value),
            f"invalid {label}")
    return value


def _paths(value: Any, label: str, *, empty: bool = False) -> list[str]:
    require(type(value) is list and len(value) <= 64 and (empty or bool(value)),
            f"invalid {label} list")
    for path in value:
        require(type(path) is str and 0 < len(path.encode("utf-8", "strict")) <= 240
                and not path.startswith("/") and "\\" not in path
                and all(part not in ("", ".", "..") for part in path.split("/"))
                and not any(ord(c) < 32 or ord(c) == 127 for c in path),
                f"invalid {label} path")
        require(path != ".git" and not path.startswith(".git/"),
                f"Git metadata forbidden in {label}")
    require(len(set(value)) == len(value), f"duplicate {label} path")
    return value


def validate_task_spec(value: Any) -> dict[str, Any]:
    spec = _object(value, SPEC_KEYS, "TaskSpec")
    require(type(spec["schema_version"]) is int and spec["schema_version"] == 1,
            "unknown TaskSpec schema")
    _text(spec["goal"], "goal")
    _text(spec["acceptance"], "acceptance")
    require(type(spec["base_revision"]) is str and HEX.fullmatch(spec["base_revision"]) is not None,
            "invalid base_revision")
    _paths(spec["allowed_paths"], "TaskSpec allowed_paths")
    require(type(spec["subagent_count"]) is int and spec["subagent_count"] == 2,
            "this bounded protocol requires exactly two subagents")
    require(type(spec["planning"]) is str and spec["planning"] in ("auto", "specified"),
            "unknown planning mode")
    tests = spec["tests"]
    require(type(tests) is list and 1 <= len(tests) <= 16, "invalid tests list")
    for test in tests:
        _object(test, {"argv", "cwd"}, "test")
        require(test["cwd"] == "." and type(test["cwd"]) is str, "test cwd must be .")
        argv = test["argv"]
        require(type(argv) is list and 1 <= len(argv) <= 32, "invalid test argv")
        for arg in argv:
            _text(arg, "test argv item")
            require("\x00" not in arg, "NUL in test argv")
    return spec


def _overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def validate_plan(spec: dict[str, Any], value: Any) -> dict[str, Any]:
    validate_task_spec(spec)
    plan = _object(value, PLAN_KEYS, "plan")
    require(type(plan["schema_version"]) is int and plan["schema_version"] == 1,
            "unknown plan schema")
    require(type(plan["base_revision"]) is str
            and plan["base_revision"] == spec["base_revision"], "plan base mismatch")
    steps = plan["steps"]
    require(type(steps) is list and len(steps) == 3, "expected exactly three steps")
    by_id = {}
    for step in steps:
        _object(step, STEP_KEYS, "step")
        name = step["id"]
        require(type(name) is str and name in ("sub-1", "sub-2", "integrate")
                and name not in by_id, "unknown or duplicate step")
        by_id[name] = step
        _text(step["instruction"], "step instruction")
        require(step["agent"] == "build" and type(step["agent"]) is str,
                "unknown step agent")
        require(type(step["depends_on"]) is list
                and all(type(dep) is str for dep in step["depends_on"]),
                "invalid dependencies")
        paths = _paths(step["allowed_paths"], "step allowed_paths", empty=True)
        require(all(path in spec["allowed_paths"] for path in paths),
                "step path outside TaskSpec")
    require(set(by_id) == {"sub-1", "sub-2", "integrate"}, "missing required step")
    for name in ("sub-1", "sub-2"):
        step = by_id[name]
        require(step["role"] == "subagent" and type(step["role"]) is str
                and step["depends_on"] == [] and bool(step["allowed_paths"]),
                "two independent, scoped subagents must be ready")
    joining = by_id["integrate"]
    require(joining["role"] == "primary" and type(joining["role"]) is str
            and len(joining["depends_on"]) == 2
            and set(joining["depends_on"]) == {"sub-1", "sub-2"}
            and joining["allowed_paths"] == [], "primary must join both subagents")
    for first in by_id["sub-1"]["allowed_paths"]:
        for second in by_id["sub-2"]["allowed_paths"]:
            require(not _overlap(first, second), "concurrent wave paths overlap")
    return plan


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _attested_response(plan_text: str | bytes, attestation: Any) -> bool:
    """Check a supplied transcript artifact; its provenance is the caller's duty."""
    if attestation is None:
        return False
    record = _object(attestation, {"source", "transcript", "transcript_sha256",
                                   "assistant_response"}, "transcript attestation")
    require(record["source"] == "opencode" and type(record["source"]) is str,
            "unknown transcript source")
    transcript = record["transcript"]
    require(type(transcript) is str and bool(transcript) and len(transcript) <= MAX_JSON_BYTES,
            "invalid transcript")
    response = _text(record["assistant_response"], "assistant response")
    require(len(transcript.encode("utf-8")) <= MAX_JSON_BYTES
            and type(record["transcript_sha256"]) is str
            and SHA256.fullmatch(record["transcript_sha256"]) is not None
            and hashlib.sha256(transcript.encode("utf-8")).hexdigest() == record["transcript_sha256"],
            "transcript digest mismatch")
    require(response == (plan_text.decode("utf-8") if isinstance(plan_text, bytes) else plan_text)
            and response in transcript, "plan is not the attested assistant response")
    # Caller-supplied transcript text and digest are self-attested. They are
    # not independent provider provenance and MUST NOT qualify model_generated.
    return False


def plan_receipt(spec_text: str | bytes, plan_text: str | bytes, *,
                 transcript_attestation: Any = None) -> dict[str, Any]:
    """Validate before emitting *planning* evidence; never report execution success."""
    spec = validate_task_spec(parse_single_json(spec_text))
    plan = validate_plan(spec, parse_single_json(plan_text))
    require(transcript_attestation is None or spec["planning"] == "auto",
            "transcript attestation requires auto planning")
    model_generated = _attested_response(plan_text, transcript_attestation)
    by_id = {step["id"]: step for step in plan["steps"]}
    # Lexical order makes scheduling independent of model JSON step ordering.
    waves = [["sub-1", "sub-2"], ["integrate"]]
    return {"status": "PLAN_VALIDATED_NOT_DISPATCHED", "model_generated": model_generated,
            "model_provenance": "UNVERIFIED",
            "task_spec_sha256": _digest(spec),
            "plan_sha256": _digest({**plan, "steps": [by_id[n] for n in ("sub-1", "sub-2", "integrate")]}),
            "step_sha256": {name: _digest(by_id[name]) for wave in waves for name in wave},
            "waves": waves,
            "transcript_sha256": transcript_attestation["transcript_sha256"]
            if model_generated else None}
