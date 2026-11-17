"""KUMIKI #359 R2b D1: source-only, synthetic Host IPC data contract.

This is NOT a socket server, authorization issuer, Native Ask verifier, or
production Host. All caller input is untrusted; accepted bytes never grant
Human permission, release eligibility, or model dispatch.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .kagari_task_model_consent_codec import (
    CodecError,
    FrozenConsentPacket,
    canonical_json_bytes,
    parse_canonical_json,
    prepare_consent_packet,
)

IPC_KIND = "KAGARI_CONSENT_HOST_IPC_V1"
RESPONSE_KIND = "KAGARI_CONSENT_HOST_IPC_RESULT_V1"
SYNTHETIC_ORIGIN = "HOST_SYNTHETIC_OBSERVATION_ONLY"
MAX_IPC_BYTES = 16 * 1024
MAX_PACKET_BYTES = 64 * 1024
HEX_24 = re.compile(r"[0-9a-f]{24}\Z")
HEX_32 = re.compile(r"[0-9a-f]{32}\Z")
HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
JOB_ID = re.compile(r"job-[0-9a-f]{24}\Z")
EPOCH = re.compile(r"epoch-[0-9a-f]{32}\Z")
REVISION = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")


class HostContractError(ValueError):
    """Stable error code; untrusted input bytes never appear in messages."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("KAGARI R2b synthetic Host contract rejected")


def parse_ipc_message(raw: bytes) -> dict:
    if type(raw) is not bytes or len(raw) > 16384:
        raise HostContractError("INVALID_IPC")
    try:
        message = parse_canonical_json(raw, max_bytes=16384)
    except CodecError as exc:
        raise HostContractError("INVALID_IPC") from exc
    if type(message) is not dict or set(message) != {
        "schema_version", "kind", "operation", "job_id", "request_id",
        "packet_sha256",
    }:
        raise HostContractError("INVALID_IPC")
    if (
        type(message["schema_version"]) is not int
        or message["schema_version"] != 1
        or message["kind"] != "KAGARI_CONSENT_HOST_IPC_V1"
        or message["operation"] not in ("submit_candidate", "status")
    ):
        raise HostContractError("INVALID_IPC")
    for key, prefix, length in (
        ("job_id", "job-", 24),
        ("request_id", "", 24),
        ("packet_sha256", "", 64),
    ):
        value = message[key]
        if (
            type(value) is not str
            or not value.startswith(prefix)
            or len(value) != len(prefix) + length
            or any(c not in "0123456789abcdef" for c in value[len(prefix):])
        ):
            raise HostContractError("INVALID_IPC")
    return message.copy()



@dataclass(frozen=True, slots=True)
class SyntheticHostObservation:
    """Host-simulated inputs, constructible by any caller; not authenticity."""

    normalized_taskspec_bytes: bytes
    model_sidecar_bytes: bytes
    host_context: dict
    host_native_ask_readbacks: tuple[dict, ...]
    trusted_model_allowlist: frozenset
    host_job_nonce: str
    authority_epoch: str
    native_revision: str
    expires_tick: int
    origin: str = SYNTHETIC_ORIGIN

    def __post_init__(self) -> None:
        if (
            self.origin != SYNTHETIC_ORIGIN
            or type(self.authority_epoch) is not str
            or EPOCH.fullmatch(self.authority_epoch) is None
            or type(self.native_revision) is not str
            or REVISION.fullmatch(self.native_revision) is None
            or type(self.expires_tick) is not int
            or self.expires_tick <= 0
        ):
            raise HostContractError("INVALID_SYNTHETIC_OBSERVATION")

    def reconstruct(self) -> FrozenConsentPacket:
        """Check only data integrity; never authenticate provenance or grant."""
        try:
            return prepare_consent_packet(
                normalized_taskspec_bytes=self.normalized_taskspec_bytes,
                model_sidecar_bytes=self.model_sidecar_bytes,
                host_context=self.host_context,
                host_native_ask_readbacks=self.host_native_ask_readbacks,
                trusted_model_allowlist=self.trusted_model_allowlist,
                host_job_nonce=self.host_job_nonce,
            )
        except (CodecError, ValueError, TypeError) as exc:
            raise HostContractError("SYNTHETIC_INPUT_REJECTED") from exc


@dataclass(frozen=True, slots=True)
class SyntheticCandidate:
    """Normalized packet identity from a mock Host, not permission."""

    job_id: str
    request_id: str
    packet_sha256: str
    host_job_nonce: str
    authority_epoch: str
    native_revision: str
    expires_tick: int
    packet_bytes: bytes
    state: str = "DATA_ONLY_NOT_AN_AUTHORIZATION"


def resolve_synthetic_candidate(raw_ipc: bytes, obs: SyntheticHostObservation) -> SyntheticCandidate:
    """Simulate a trusted host readback; do not expose obs via real IPC."""
    message = parse_ipc_message(raw_ipc)
    if message["operation"] != "submit_candidate":
        raise HostContractError("OPERATION_NOT_SUPPORTED")
    if type(obs) is not SyntheticHostObservation:
        raise HostContractError("SYNTHETIC_OBSERVATION_REQUIRED")
    packet = obs.reconstruct()
    body = parse_canonical_json(packet.packet_bytes, MAX_PACKET_BYTES)
    if (
        message["job_id"] != body["context"]["job_id"]
        or message["request_id"] != packet.request_id
        or message["packet_sha256"] != packet.request_sha256
        or body["host_job_nonce"] != obs.host_job_nonce
        or body["model_dispatched"] is not False
        or body["production_activation"] is not False
        or body["provider_identity_attested"] is not False
        or body["native_grant_provenance_attested"] is not False
        or body["scope"] != "DATA_ONLY_OWNED_CLONE_NO_PROJECT_WRITE_NO_PUSH"
    ):
        raise HostContractError("CANDIDATE_IDENTITY_DRIFT")
    return SyntheticCandidate(
        job_id=message["job_id"],
        request_id=packet.request_id,
        packet_sha256=packet.request_sha256,
        host_job_nonce=obs.host_job_nonce,
        authority_epoch=obs.authority_epoch,
        native_revision=obs.native_revision,
        expires_tick=obs.expires_tick,
        packet_bytes=packet.packet_bytes,
    )


def make_status_reply(
    *, job_id: str, request_id: str, packet_sha256: str,
    synthetic_state: str, revision: int,
) -> bytes:
    """Serialize a data-only synthetic state; never say APPROVED."""
    if (
        type(job_id) is not str or JOB_ID.fullmatch(job_id) is None
        or type(request_id) is not str or HEX_24.fullmatch(request_id) is None
        or type(packet_sha256) is not str or HEX_64.fullmatch(packet_sha256) is None
        or type(synthetic_state) is not str
        or synthetic_state not in {
            "PENDING_HUMAN_TEST_ONLY", "SIMULATED_TTY_ACK_DATA_ONLY",
            "DENIED", "REVOKED", "EXPIRED", "RECOVERY_UNCERTAIN",
        }
        or type(revision) is not int or revision < 0
    ):
        raise HostContractError("INVALID_STATUS")
    return canonical_json_bytes({
        "schema_version": 1,
        "kind": RESPONSE_KIND,
        "status": "DATA_ONLY_NOT_AN_AUTHORIZATION",
        "job_id": job_id,
        "request_id": request_id,
        "packet_sha256": packet_sha256,
        "synthetic_state": synthetic_state,
        "revision": revision,
        "human_authenticated": False,
        "native_grant_attested": False,
        "model_dispatched": False,
        "production_activation": False,
    })


def source_only_authorization(*_args: object, **_kwargs: object) -> None:
    """Guard against accidental future use as a real approval/dispatch API."""
    raise HostContractError("HOST_AUTHORIZATION_NOT_IMPLEMENTED")
