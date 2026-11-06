"""Pure offline R2a Task/sidecar/model-consent codec.

Data and digests are not Task grants, Native Human consent or model attestation.
No filesystem access, terminal, model invocation, Git, network, clock or random.
The separately trusted future Host must authenticate inputs and enforce OS storage isolation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

if __package__:
    from .kagari_task_model_binding import (
        FrozenModelBinding, ModelBindingError, ModelSelection, parse_model_binding_bytes
    )
else:  # Standalone import is allowed only from the future pinned Host package.
    from kagari_task_model_binding import (
        FrozenModelBinding, ModelBindingError, ModelSelection, parse_model_binding_bytes
    )

MAX_REQUEST = 64 * 1024
CTX_FIELDS = frozenset({
    "repository", "task", "job_id", "project_root", "project_device",
    "project_inode", "project_head", "project_index_sha256",
    "project_agents_sha256", "project_opencode_sha256", "workunit_clone",
    "model_executable", "model_executable_sha256", "package_root",
    "package_receipt_sha256",
})


class CodecError(ValueError):
    """Stable rejection code without including untrusted contents in the message."""

    def __init__(self, code: str) -> None:
        super().__init__("R2a data-only model consent codec rejected")
        self.code = code


@dataclass(frozen=True, slots=True)
class FrozenConsentPacket:
    """Immutable data packet; NEVER a Task or Human grant."""

    packet_bytes: bytes
    request_id: str
    request_sha256: str
    model: ModelSelection
    roles: tuple[str, ...]
    status: str = "DATA_ONLY_NOT_AN_AUTHORIZATION"


@dataclass(frozen=True, slots=True)
class DataOnlyVerification:
    """A matching digest is not permission to execute or publish."""

    status: str
    request_id: str
    request_sha256: str
    model: ModelSelection
    roles: tuple[str, ...]
    model_dispatched: bool
    provider_identity_attested: bool
    native_grant_provenance_attested: bool
    production_activation: bool


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf8") + b"\n"
    except Exception:
        raise CodecError("INVALID_PACKET") from None


def parse_canonical_json(raw: bytes, max_bytes: int) -> dict:
    try:
        if type(raw) is not bytes or not 1 <= len(raw) <= max_bytes:
            raise CodecError("INVALID_PACKET")
        reject = lambda _: (_ for _ in ()).throw(CodecError("INVALID_PACKET"))
        result = json.loads(
            raw.decode("utf8"),
            object_pairs_hook=lambda pairs: (
                obj if len(obj := dict(pairs)) == len(pairs) else reject(None)
            ),
            parse_constant=reject,
        )
        if type(result) is not dict or canonical_json_bytes(result) != raw:
            raise CodecError("INVALID_PACKET")
        return result
    except Exception:
        raise CodecError("INVALID_PACKET") from None

def validate_context(context: dict) -> dict:
    if type(context) is not dict or context.keys() != CTX_FIELDS:
        raise CodecError("INVALID_CONTEXT")

    hex64 = r"[0-9a-f]{64}"
    component = r"(?!\.{1,2}(?:/|\Z))[^/\\\x00-\x1f\x7f-\x9f]+"
    path = rf"/{component}(?:/{component})*"
    patterns = {
        "repository": r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+",
        "task": r"[1-9][0-9]*",
        "job_id": r"job-[0-9a-f]{24}",
        "project_head": r"(?:[0-9a-f]{40}|[0-9a-f]{64})",
        "project_index_sha256": hex64,
        "project_agents_sha256": hex64,
        "project_opencode_sha256": hex64,
        "model_executable_sha256": hex64,
        "package_receipt_sha256": hex64,
        "project_root": path,
        "workunit_clone": path,
        "model_executable": path,
        "package_root": path,
    }
    for field, pattern in patterns.items():
        value = context[field]
        if value is None and field in (
            "project_agents_sha256", "project_opencode_sha256"
        ):
            continue
        if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
            raise CodecError("INVALID_CONTEXT")

    for field, minimum in (("project_device", 0), ("project_inode", 1)):
        value = context[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise CodecError("INVALID_CONTEXT")

    root, clone = context["project_root"], context["workunit_clone"]
    if (
        re.fullmatch(re.escape(root) + r"(?:/.*)?", clone) is not None
        or re.fullmatch(re.escape(clone) + r"(?:/.*)?", root) is not None
    ):
        raise CodecError("INVALID_CONTEXT")
    return dict(context)

def validate_native_asks(asks: tuple[dict, ...], clone: str) -> tuple[dict, ...]:
    if type(asks) is not tuple or len(asks) != 2 or type(clone) is not str:
        raise CodecError('NATIVE_ASK_DRIFT')
    kinds = ('AGENTS_SCOPED_EXCEPTION', 'NATIVE_TASK_PERMISSION')
    active_keys = {
        'kind', 'status', 'request_id', 'request_sha256', 'decision_sha256',
        'runtime_source_sha256', 'project_root', 'granted_roles', 'released', 'production_qualified',
    }
    roles = ['kagari-v010-leaf-a', 'kagari-v010-leaf-b']
    result = []
    for i, ask in enumerate(asks):
        if type(ask) is not dict or any(type(k) is not str for k in ask):
            raise CodecError('NATIVE_ASK_DRIFT')
        if (type(ask.get('kind')) is not str or ask['kind'] != kinds[i]
                or type(ask.get('status')) is not str):
            raise CodecError('NATIVE_ASK_DRIFT')
        if i == 0 and ask['status'] == 'NOT_REQUIRED_NO_CONFIRMED_CONFLICT':
            if (set(ask) != {'kind', 'status', 'reason'}
                    or type(ask['reason']) is not str or ask['reason'] != 'HOST_VERIFIED_NO_AGENTS_CONFLICT'):
                raise CodecError('NATIVE_ASK_DRIFT')
            result.append(dict(ask))
            continue
        if set(ask) != active_keys or ask['status'] != 'ACTIVE_SCOPED_GRANT_TEST_ONLY':
            raise CodecError('NATIVE_ASK_DRIFT')
        for key, size in (('request_id', 24), ('request_sha256', 64),
                          ('decision_sha256', 64), ('runtime_source_sha256', 64)):
            if type(ask[key]) is not str or re.fullmatch('[0-9a-f]{%d}' % size, ask[key]) is None:
                raise CodecError('NATIVE_ASK_DRIFT')
        if type(ask['project_root']) is not str or ask['project_root'] != clone:
            raise CodecError('NATIVE_ASK_DRIFT')
        if type(ask['granted_roles']) is not list or len(ask['granted_roles']) != 2:
            raise CodecError('NATIVE_ASK_DRIFT')
        if any(type(role) is not str for role in ask['granted_roles']) or ask['granted_roles'] != roles:
            raise CodecError('NATIVE_ASK_DRIFT')
        if ask['released'] is not False or ask['production_qualified'] is not False:
            raise CodecError('NATIVE_ASK_DRIFT')
        copied = dict(ask)
        copied['granted_roles'] = ask['granted_roles'].copy()
        result.append(copied)
    return tuple(result)

def validate_task_snapshot(raw: bytes, project_head: str, *, allow_optional_v2: bool = False) -> dict:
    try:
        if type(raw) is not bytes:
            raise ValueError
        task = parse_canonical_json(raw, 1048576)
        if type(task) is not dict:
            raise ValueError
        version = task.get("schema_version")
        if (type(version) is not int or version not in (1, 2)
                or (version == 2 and allow_optional_v2 is not True)):
            raise ValueError
        fields = {"schema_version", "goal", "base_revision", "allowed_paths",
                  "tests", "acceptance", "subagent_count", "planning"}
        if version == 2:
            fields.add("optional_exploration_paths")
        if set(task) != fields:
            raise ValueError
        base = task["base_revision"]
        if (type(base) is not str or base != project_head or len(base) not in (40, 64)
                or any(c not in "0123456789abcdef" for c in base)):
            raise ValueError
        if any(type(task[k]) is not str or not task[k].strip() for k in ("goal", "acceptance")):
            raise ValueError
        count = task["subagent_count"]
        if type(count) is not int or count < 2 or task["planning"] not in ("auto", "specified"):
            raise ValueError
        for key in ("allowed_paths",) + (("optional_exploration_paths",) if version == 2 else ()):
            paths = task[key]
            if (type(paths) is not list or any(type(p) is not str for p in paths)
                    or len(set(paths)) != len(paths)
                    or (key == "allowed_paths" and not paths)
                    or (key == "optional_exploration_paths" and len(paths) > 32)):
                raise ValueError
        if type(task["tests"]) is not list:
            raise ValueError
        for test in task["tests"]:
            if type(test) is not dict or set(test) != {"argv", "cwd"} or test["cwd"] != ".":
                raise ValueError
            argv = test["argv"]
            if type(argv) is not list or not argv or any(type(arg) is not str for arg in argv):
                raise ValueError
        return task
    except Exception:
        raise CodecError("INVALID_TASKSPEC") from None

def make_consent_body(ctx: dict, spec: dict, binding: FrozenModelBinding, asks: tuple[dict, ...], nonce: str) -> dict:
    import json

    return {
        "schema_version": 1,
        "kind": "KAGARI_TASK_MODEL_CONSENT_PACKET_V1",
        "host_job_nonce": nonce,
        "context": dict(ctx),
        "TaskSpec_sha256": binding.task_spec_sha256,
        "TaskSpec_allowed_paths": list(spec["allowed_paths"]),
        "TaskSpec_tests": [dict(test) for test in spec["tests"]],
        "model_binding_raw_sha256": binding.raw_sha256,
        "model_binding_canonical_sha256": binding.canonical_sha256,
        "model_selection": {
            "provider_id": binding.model.provider_id,
            "model_id": binding.model.model_id,
            "variant": binding.model.variant,
            "roles": list(binding.roles),
        },
        "native_asks": [json.loads(json.dumps(ask)) for ask in asks],
        "scope": "DATA_ONLY_OWNED_CLONE_NO_PROJECT_WRITE_NO_PUSH",
        "model_dispatched": False,
        "source_product_mutations": False,
        "provider_identity_attested": False,
        "native_grant_provenance_attested": False,
        "production_activation": False,
    }

def prepare_consent_packet(
    *, normalized_taskspec_bytes: bytes, model_sidecar_bytes: bytes,
    host_context: dict, host_native_ask_readbacks: tuple[dict, ...],
    trusted_model_allowlist: frozenset[ModelSelection], host_job_nonce: str,
    allow_optional_v2: bool = False,
) -> FrozenConsentPacket:
    if type(host_job_nonce) is not str or len(host_job_nonce) != 32 or any(
        c not in "0123456789abcdef" for c in host_job_nonce
    ):
        raise CodecError("INVALID_NONCE")
    if type(allow_optional_v2) is not bool:
        raise CodecError("INVALID_ALLOW_OPTIONAL_V2")
    ctx = validate_context(host_context)
    spec = validate_task_snapshot(
        normalized_taskspec_bytes, ctx["project_head"],
        allow_optional_v2=allow_optional_v2,
    )
    taskspec_sha256 = sha256_hex(normalized_taskspec_bytes)
    try:
        binding = parse_model_binding_bytes(
            model_sidecar_bytes,
            expected_project_head=ctx["project_head"],
            expected_task_spec_sha256=taskspec_sha256,
            allowed_models=trusted_model_allowlist,
        )
    except ModelBindingError as exc:
        raise CodecError("MODEL_BINDING_REJECTED") from exc
    asks = validate_native_asks(host_native_ask_readbacks, ctx["workunit_clone"])
    body = make_consent_body(ctx, spec, binding, asks, host_job_nonce)
    request_id = sha256_hex(canonical_json_bytes(body))[:24]
    body["request_id"] = request_id
    packet_bytes = canonical_json_bytes(body)
    if len(packet_bytes) > MAX_REQUEST:
        raise CodecError("REQUEST_TOO_LARGE")
    return FrozenConsentPacket(
        packet_bytes=packet_bytes, request_id=request_id,
        request_sha256=sha256_hex(packet_bytes),
        model=binding.model, roles=binding.roles,
    )

def validate_consent_packet(
    *, packet_bytes: bytes, normalized_taskspec_bytes: bytes,
    model_sidecar_bytes: bytes, host_context: dict,
    host_native_ask_readbacks: tuple[dict, ...],
    trusted_model_allowlist: frozenset[ModelSelection],
    host_job_nonce: str, allow_optional_v2: bool = False,
) -> DataOnlyVerification:
    decoded = parse_canonical_json(packet_bytes, MAX_REQUEST)
    expected = prepare_consent_packet(
        normalized_taskspec_bytes=normalized_taskspec_bytes,
        model_sidecar_bytes=model_sidecar_bytes,
        host_context=host_context,
        host_native_ask_readbacks=host_native_ask_readbacks,
        trusted_model_allowlist=trusted_model_allowlist,
        host_job_nonce=host_job_nonce,
        allow_optional_v2=allow_optional_v2,
    )
    if (
        packet_bytes != expected.packet_bytes
        or decoded.get("request_id") != expected.request_id
    ):
        raise CodecError("IDENTITY_DRIFT")
    return DataOnlyVerification(
        status="DATA_ONLY_NOT_AN_AUTHORIZATION",
        request_id=expected.request_id,
        request_sha256=expected.request_sha256,
        model=expected.model,
        roles=expected.roles,
        model_dispatched=False,
        provider_identity_attested=False,
        native_grant_provenance_attested=False,
        production_activation=False,
    )
