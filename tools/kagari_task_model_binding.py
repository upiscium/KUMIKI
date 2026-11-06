"""Offline, versioned TaskSpec-bound model-selection sidecar contract.

This module validates data only. It does not grant a Task, invoke a model,
resolve a provider, or weaken the shipped KAGARI v0.1.0 runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_BYTES = 16 * 1024
KIND = "KAGARI_TASK_MODEL_BINDING"
ROLES = ("planner", "primary", "leaf-a", "leaf-b")
_VARIANTS = frozenset({"low", "medium", "high", "xhigh", "max"})
_ROOT_FIELDS = frozenset(
    {"schema_version", "kind", "project_head", "task_spec_sha256", "model", "roles"}
)
_MODEL_FIELDS = frozenset({"provider_id", "model_id", "variant"})
_HEX40_OR_64 = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PROVIDER = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z")
_MODEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")


class ModelBindingError(ValueError):
    """Stable validation code; messages never echo untrusted file contents."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ModelSelection:
    """Exact provider/model/variant; never an ambient fallback request."""

    provider_id: str
    model_id: str
    variant: str

    def __post_init__(self) -> None:
        if (
            type(self.provider_id) is not str
            or _PROVIDER.fullmatch(self.provider_id) is None
            or type(self.model_id) is not str
            or _MODEL.fullmatch(self.model_id) is None
            or type(self.variant) is not str
            or self.variant not in _VARIANTS
        ):
            raise ModelBindingError("INVALID_MODEL", "invalid model selection")


@dataclass(frozen=True, slots=True)
class FrozenModelBinding:
    """Data snapshot only; not a Human approval or executable authority."""

    project_head: str
    task_spec_sha256: str
    model: ModelSelection
    roles: tuple[str, ...]
    raw_sha256: str
    canonical_sha256: str


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _invalid(message: str) -> ModelBindingError:
    return ModelBindingError("INVALID_BINDING", message)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid("duplicate JSON field")
        result[key] = value
    return result


def _nonfinite(_token: str) -> None:
    raise _invalid("nonfinite JSON constant")


def _read_sidecar(path: Path) -> bytes:
    if not isinstance(path, Path) or not path.is_absolute():
        raise _invalid("absolute sidecar Path required")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise _invalid("sidecar must be a regular non-hardlinked file")
            if info.st_size < 1 or info.st_size > MAX_BYTES:
                raise _invalid("invalid sidecar size")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(MAX_BYTES + 1)
            if len(data) < 1 or len(data) > MAX_BYTES:
                raise _invalid("invalid sidecar size")
            return data
        finally:
            os.close(fd)
    except OSError as exc:
        raise _invalid("sidecar file cannot be read") from exc


def _trusted_context(
    expected_project_head: str,
    expected_task_spec_sha256: str,
    allowed_models: frozenset[ModelSelection],
) -> None:
    if (
        type(expected_project_head) is not str
        or _HEX40_OR_64.fullmatch(expected_project_head) is None
        or type(expected_task_spec_sha256) is not str
        or _SHA256.fullmatch(expected_task_spec_sha256) is None
    ):
        raise _invalid("invalid trusted TaskSpec identity")
    if (
        type(allowed_models) is not frozenset
        or not allowed_models
        or not all(type(item) is ModelSelection for item in allowed_models)
    ):
        raise _invalid("explicit nonempty trusted model allowlist required")


def load_model_binding(
    path: Path,
    *,
    expected_project_head: str,
    expected_task_spec_sha256: str,
    allowed_models: frozenset[ModelSelection],
) -> FrozenModelBinding:
    """Validate a bounded JSON sidecar against separately trusted Task context.

    An exact approved selection is necessary but is NOT sufficient for consent.
    The caller must independently bind this snapshot to later Human/Native Asks.
    """
    _trusted_context(expected_project_head, expected_task_spec_sha256, allowed_models)
    raw = _read_sidecar(path)
    try:
        data = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_strict_object,
            parse_constant=_nonfinite,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise _invalid("malformed sidecar JSON") from exc
    if type(data) is not dict or data.keys() != _ROOT_FIELDS:
        raise _invalid("sidecar fields do not match v1")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise _invalid("unsupported sidecar schema version")
    if type(data["kind"]) is not str or data["kind"] != KIND:
        raise _invalid("invalid sidecar kind")
    if (
        type(data["project_head"]) is not str
        or _HEX40_OR_64.fullmatch(data["project_head"]) is None
        or type(data["task_spec_sha256"]) is not str
        or _SHA256.fullmatch(data["task_spec_sha256"]) is None
    ):
        raise _invalid("invalid sidecar TaskSpec reference")
    if (
        data["project_head"] != expected_project_head
        or data["task_spec_sha256"] != expected_task_spec_sha256
    ):
        raise ModelBindingError("IDENTITY_DRIFT", "TaskSpec identity changed")
    role_list = data["roles"]
    if type(role_list) is not list or role_list != list(ROLES):
        raise _invalid("expected exactly four ordered roles")
    model_data = data["model"]
    if type(model_data) is not dict or model_data.keys() != _MODEL_FIELDS:
        raise _invalid("model selection fields do not match v1")
    model = ModelSelection(**model_data)
    if model not in allowed_models:
        raise ModelBindingError("UNSUPPORTED_MODEL", "model not allowed by trusted policy")
    canonical = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return FrozenModelBinding(
        data["project_head"], data["task_spec_sha256"],
        model, ROLES, _hash(raw), _hash(canonical)
    )


def revalidate_model_binding(
    path: Path,
    frozen: FrozenModelBinding,
    *,
    allowed_models: frozenset[ModelSelection],
) -> FrozenModelBinding:
    """Fail closed on post-preparation byte, scope or trusted-model drift."""
    if type(frozen) is not FrozenModelBinding:
        raise _invalid("frozen model binding required")
    current = load_model_binding(
        path,
        expected_project_head=frozen.project_head,
        expected_task_spec_sha256=frozen.task_spec_sha256,
        allowed_models=allowed_models,
    )
    if current != frozen:
        raise ModelBindingError("IDENTITY_DRIFT", "sidecar snapshot changed")
    return current
