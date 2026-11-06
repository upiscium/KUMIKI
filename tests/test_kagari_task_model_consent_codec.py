"""Synthetic, offline R2a consent codec security contract.

This test suite never uses a real Project, Native Ask, Human consent, or model.
All consent inputs are synthetic and codec calls are in-memory. One R1 loader parity\ntest uses a temporary file; no provider/model attestation is claimed.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.kagari_task_model_binding import (
    ModelSelection,
    load_model_binding,
    parse_model_binding_bytes,
)
from tools.kagari_task_model_consent_codec import (
    CodecError,
    canonical_json_bytes,
    parse_canonical_json,
    prepare_consent_packet,
    validate_consent_packet,
)


class TestPureConsentCodec(unittest.TestCase):
    def setUp(self) -> None:
        self.head = "a" * 40
        self.spec = {
            "schema_version": 1,
            "goal": "Offline synthetic consent test",
            "base_revision": self.head,
            "allowed_paths": ["src/example.py"],
            "tests": [{"argv": ["python", "-m", "unittest"], "cwd": "."}],
            "acceptance": "pass deterministic checks",
            "subagent_count": 2,
            "planning": "specified",
        }
        self.context = {
            "repository": "upiscium/KUMIKI",
            "task": "359",
            "job_id": "job-" + "1" * 24,
            "project_root": "/synthetic/source-project",
            "project_device": 12,
            "project_inode": 34,
            "project_head": self.head,
            "project_index_sha256": "b" * 64,
            "project_agents_sha256": None,
            "project_opencode_sha256": None,
            "workunit_clone": "/synthetic/owned-clone",
            "model_executable": "/nix/store/synthetic-opencode/bin/opencode",
            "model_executable_sha256": "c" * 64,
            "package_root": "/nix/store/synthetic-kagari-versioned",
            "package_receipt_sha256": "d" * 64,
        }
        self.binding = {
            "schema_version": 1,
            "kind": "KAGARI_TASK_MODEL_BINDING",
            "project_head": self.head,
            "task_spec_sha256": hashlib.sha256(canonical_json_bytes(self.spec)).hexdigest(),
            "model": {
                "provider_id": "openai", "model_id": "gpt-6-astra", "variant": "max",
            },
            "roles": ["planner", "primary", "leaf-a", "leaf-b"],
        }
        self.asks = (
            {
                "kind": "AGENTS_SCOPED_EXCEPTION",
                "status": "NOT_REQUIRED_NO_CONFIRMED_CONFLICT",
                "reason": "HOST_VERIFIED_NO_AGENTS_CONFLICT",
            },
            {
                "kind": "NATIVE_TASK_PERMISSION",
                "status": "ACTIVE_SCOPED_GRANT_TEST_ONLY",
                "request_id": "1" * 24,
                "request_sha256": "2" * 64,
                "decision_sha256": "3" * 64,
                "runtime_source_sha256": "4" * 64,
                "project_root": self.context["workunit_clone"],
                "granted_roles": ["kagari-v010-leaf-a", "kagari-v010-leaf-b"],
                "released": False,
                "production_qualified": False,
            },
        )
        self.allowed = frozenset({ModelSelection("openai", "gpt-6-astra", "max")})
        self.nonce = "e" * 32

    def kwargs(self) -> dict:
        return {
            "normalized_taskspec_bytes": canonical_json_bytes(copy.deepcopy(self.spec)),
            "model_sidecar_bytes": json.dumps(self.binding, indent=2).encode("utf-8"),
            "host_context": copy.deepcopy(self.context),
            "host_native_ask_readbacks": copy.deepcopy(self.asks),
            "trusted_model_allowlist": self.allowed,
            "host_job_nonce": self.nonce,
        }

    def packet(self, **changes):
        args = self.kwargs()
        args.update(changes)
        return prepare_consent_packet(**args)

    def check_failure(self, expected_code: str, *, packet=None, **changes):
        args = self.kwargs()
        args.update(changes)
        with self.assertRaises(CodecError) as raised:
            if packet is None:
                prepare_consent_packet(**args)
            else:
                validate_consent_packet(packet_bytes=packet.packet_bytes, **args)
        self.assertEqual(raised.exception.code, expected_code)

    def test_exact_roundtrip_data_only(self):
        kw = self.kwargs()
        first = prepare_consent_packet(**kw)
        second = prepare_consent_packet(**kw)
        self.assertEqual(first.packet_bytes, second.packet_bytes)
        self.assertEqual(first.request_id, second.request_id)
        self.assertEqual(first.request_sha256, second.request_sha256)
        self.assertEqual(first.model, second.model)
        self.assertEqual(first.status, second.status)
        for packet in (first, second):
            self.assertEqual(tuple(packet.roles), ("planner", "primary", "leaf-a", "leaf-b"))
            result = validate_consent_packet(packet_bytes=packet.packet_bytes, **kw)
            self.assertEqual(result.status, "DATA_ONLY_NOT_AN_AUTHORIZATION")
            for name in ("model_dispatched", "provider_identity_attested",
                         "native_grant_provenance_attested", "production_activation"):
                self.assertIs(getattr(result, name), False)

    def test_expected_nonce_mismatch(self):
        kw = self.kwargs()
        packet = prepare_consent_packet(**kw)
        nonce = kw["host_job_nonce"]
        changed = dict(kw, host_job_nonce=nonce[:-1] + ("1" if nonce[-1] == "0" else "0"))
        with self.assertRaises(CodecError) as caught:
            validate_consent_packet(packet_bytes=packet.packet_bytes, **changed)
        self.assertEqual(caught.exception.code, "IDENTITY_DRIFT")

    def test_no_io_even_when_all_host_ops_forbidden(self):
        import contextlib
        kw = self.kwargs()
        baseline = prepare_consent_packet(**kw)
        expected = validate_consent_packet(packet_bytes=baseline.packet_bytes, **kw)
        with contextlib.ExitStack() as stack:
            guards = [
                stack.enter_context(mock.patch(target, side_effect=AssertionError("forbidden I/O")))
                for target in ("builtins.open", "os.open", "os.mkdir",
                               "subprocess.run", "builtins.input")
            ]
            packet = prepare_consent_packet(**kw)
            result = validate_consent_packet(packet_bytes=packet.packet_bytes, **kw)
            for name in ("packet_bytes", "request_id", "request_sha256", "model", "roles", "status"):
                self.assertEqual(getattr(packet, name), getattr(baseline, name))
            for name in ("status", "model_dispatched", "provider_identity_attested",
                         "native_grant_provenance_attested", "production_activation"):
                self.assertEqual(getattr(result, name), getattr(expected, name))
            for guard in guards:
                guard.assert_not_called()

    def test_bound_request_has_all_exact_model_and_native_evidence_fields(self) -> None:
        packet = self.packet()
        decoded = parse_canonical_json(packet.packet_bytes, 65536)
        self.assertEqual(decoded["kind"], "KAGARI_TASK_MODEL_CONSENT_PACKET_V1")
        self.assertEqual(decoded["model_selection"], {
            "provider_id": "openai", "model_id": "gpt-6-astra",
            "variant": "max", "roles": ["planner", "primary", "leaf-a", "leaf-b"],
        })
        self.assertEqual(decoded["host_job_nonce"], self.nonce)
        self.assertEqual(decoded["native_asks"][1]["request_sha256"], "2" * 64)
        self.assertEqual(decoded["context"]["model_executable_sha256"], "c" * 64)
        self.assertEqual(decoded["context"]["package_receipt_sha256"], "d" * 64)
        self.assertEqual(
            decoded["TaskSpec_sha256"],
            hashlib.sha256(self.kwargs()["normalized_taskspec_bytes"]).hexdigest(),
        )
        self.assertEqual(
            decoded["model_binding_raw_sha256"],
            hashlib.sha256(self.kwargs()["model_sidecar_bytes"]).hexdigest(),
        )
        self.assertEqual(
            packet.request_id,
            hashlib.sha256(canonical_json_bytes({
                k: v for k, v in decoded.items() if k != "request_id"
            })).hexdigest()[:24],
        )
        self.assertNotIn("approved", decoded)
        self.assertFalse(decoded["model_dispatched"])
        self.assertFalse(decoded["provider_identity_attested"])
        self.assertFalse(decoded["native_grant_provenance_attested"])
        self.assertFalse(decoded["production_activation"])

    def test_model_variant_drift_and_role_reorder_rejected(self) -> None:
        original = self.packet()
        for value in ("high", "xhigh", "low", "invalid"):
            with self.subTest(variant=value):
                binding = copy.deepcopy(self.binding)
                binding["model"]["variant"] = value
                new_raw = json.dumps(binding).encode()
                self.check_failure(
                    "MODEL_BINDING_REJECTED", model_sidecar_bytes=new_raw,
                )
        for roles in (["primary", "planner", "leaf-a", "leaf-b"],
                      ["planner", "primary", "leaf-b", "leaf-a"],
                      ["planner", "primary", "leaf-a", "leaf-a"]):
            with self.subTest(roles=roles):
                binding = copy.deepcopy(self.binding)
                binding["roles"] = roles
                self.check_failure(
                    "MODEL_BINDING_REJECTED",
                    model_sidecar_bytes=json.dumps(binding).encode(),
                )
        self.assertEqual(original.model.variant, "max")

    def test_raw_sidecar_byte_change_preserving_canonical_meaning_rejected(self):
        packet = self.packet()
        compact_raw = json.dumps(self.binding, sort_keys=True,
                                 separators=(",", ":")).encode()
        self.assertNotEqual(compact_raw, self.kwargs()["model_sidecar_bytes"])
        self.check_failure(
            "IDENTITY_DRIFT", packet=packet, model_sidecar_bytes=compact_raw,
        )

    def test_project_context_and_package_identity_change_invalidates_packet(self):
        packet = self.packet()
        for field, value in (
            ("project_index_sha256", "e" * 64),
            ("model_executable_sha256", "f" * 64),
            ("package_receipt_sha256", "a" * 64),
            ("project_inode", 99999),
            ("project_device", 99999),
            ("job_id", "job-" + "2" * 24),
        ):
            with self.subTest(field=field):
                context = copy.deepcopy(self.context)
                context[field] = value
                self.check_failure("IDENTITY_DRIFT", packet=packet, host_context=context)

    def test_project_head_not_retained_for_different_sidecar(self) -> None:
        changed = copy.deepcopy(self.context)
        changed["project_head"] = "f" * 40
        self.check_failure("INVALID_TASKSPEC", host_context=changed)

    def test_native_ask_generation_and_revocation_drift(self) -> None:
        packet = self.packet()
        for field, value in (("request_id", "5" * 24),
                             ("request_sha256", "6" * 64),
                             ("decision_sha256", "7" * 64),
                             ("runtime_source_sha256", "8" * 64)):
            with self.subTest(field=field):
                asks = list(copy.deepcopy(self.asks))
                asks[1][field] = value
                self.check_failure("IDENTITY_DRIFT", packet=packet,
                                   host_native_ask_readbacks=tuple(asks))
        asks = list(copy.deepcopy(self.asks))
        asks[1]["status"] = "REVOKED"
        self.check_failure("NATIVE_ASK_DRIFT",
                           host_native_ask_readbacks=tuple(asks))
        asks = list(copy.deepcopy(self.asks))
        asks[1]["released"] = True
        self.check_failure("NATIVE_ASK_DRIFT",
                           host_native_ask_readbacks=tuple(asks))
        self.check_failure("NATIVE_ASK_DRIFT",
                           host_native_ask_readbacks=self.asks[:1])

    def test_mutated_task_spec_cannot_reuse_existing_model_binding(self) -> None:
        packet = self.packet()
        changed = copy.deepcopy(self.spec)
        changed["allowed_paths"] = ["src/another.py"]
        self.check_failure(
            "MODEL_BINDING_REJECTED", normalized_taskspec_bytes=canonical_json_bytes(changed),
        )
        changed["allowed_paths"] = ["src/example.py"]
        changed["tests"][0]["argv"] = ["python", "-m", "compileall"]
        self.check_failure(
            "MODEL_BINDING_REJECTED", normalized_taskspec_bytes=canonical_json_bytes(changed),
        )
        self.assertTrue(packet.request_id)

    def test_reject_fake_approval_and_unexpected_packet_fields(self):
        packet = self.packet()
        data = parse_canonical_json(packet.packet_bytes, 65536)
        for key, value in (
            ("approved", True),
            ("human_approval", "APPROVED"),
            ("provider_identity_attested", True),
            ("model_dispatched", True),
        ):
            with self.subTest(key=key):
                changed = dict(data)
                changed[key] = value
                forged = canonical_json_bytes(changed)
                args = self.kwargs()
                with self.assertRaises(CodecError) as ctx:
                    validate_consent_packet(packet_bytes=forged, **args)
                self.assertEqual(ctx.exception.code, "IDENTITY_DRIFT")

    def test_packet_duplicate_json_nonfinite_and_invalid_utf8(self):
        packet = self.packet()
        for bad in (
            b'{"a":1,"a":1}',
            b'{"x":NaN}',
            b'{"x":Infinity}',
            b'\xff\xfe',
            packet.packet_bytes + b' ',
        ):
            with self.subTest(raw=bad[:24]):
                with self.assertRaises(CodecError) as e:
                    validate_consent_packet(packet_bytes=bad, **self.kwargs())
                self.assertEqual(e.exception.code, "INVALID_PACKET")

    def test_bad_host_nonce_context_types_and_unknown_context_keys(self):
        for invalid in (True, None, "", "x" * 32, "A" * 32, "f" * 33):
            with self.subTest(nonce=repr(invalid)):
                self.check_failure("INVALID_NONCE", host_job_nonce=invalid)
        for field, bad in (("project_device", True), ("project_inode", 0),
                           ("project_root", "/synthetic/../source"),
                           ("workunit_clone", self.context["project_root"]),
                           ("repository", "evil/company/foo"),
                           ("task", "0")):
            with self.subTest(field=field):
                data = dict(self.context)
                data[field] = bad
                self.check_failure("INVALID_CONTEXT", host_context=data)
        data = dict(self.context)
        data["approved"] = True
        self.check_failure("INVALID_CONTEXT", host_context=data)

    def test_fail_closed_unapproved_model_allowlist(self):
        for allow in (
            frozenset(),
            frozenset({ModelSelection("openai", "gpt-6-astra", "high")}),
            {"openai/gpt-6-astra"},
            None,
        ):
            with self.subTest(allow=repr(allow)):
                self.check_failure("MODEL_BINDING_REJECTED",
                                   trusted_model_allowlist=allow)

    def test_optional_v2_must_be_explicit_and_bound_to_new_sidecar_hash(self):
        changed = copy.deepcopy(self.spec)
        changed["schema_version"] = 2
        changed["optional_exploration_paths"] = ["docs/reference.md"]
        snap = canonical_json_bytes(changed)
        binding = copy.deepcopy(self.binding)
        binding["task_spec_sha256"] = hashlib.sha256(snap).hexdigest()
        raw = json.dumps(binding, indent=2).encode()
        self.check_failure(
            "INVALID_TASKSPEC", normalized_taskspec_bytes=snap,
            model_sidecar_bytes=raw,
        )
        kw = self.kwargs()
        kw.update(normalized_taskspec_bytes=snap, model_sidecar_bytes=raw,
                  allow_optional_v2=True)
        packet = prepare_consent_packet(**kw)
        checked = validate_consent_packet(packet_bytes=packet.packet_bytes, **kw)
        self.assertEqual(checked.status, "DATA_ONLY_NOT_AN_AUTHORIZATION")

    def test_strict_task_version_and_duplicate_paths(self):
        for value in (True, 3, "1", -1):
            with self.subTest(schema=value):
                spec = dict(self.spec)
                spec["schema_version"] = value
                self.check_failure("INVALID_TASKSPEC",
                                   normalized_taskspec_bytes=canonical_json_bytes(spec))
        spec = copy.deepcopy(self.spec)
        spec["allowed_paths"].append(spec["allowed_paths"][0])
        self.check_failure("INVALID_TASKSPEC",
                           normalized_taskspec_bytes=canonical_json_bytes(spec))

    def test_untrusted_replay_cannot_introduce_authority(self):
        packet = self.packet()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            packet.request_id = "f" * 24
        kw = self.kwargs()
        check = validate_consent_packet(packet_bytes=packet.packet_bytes, **kw)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            check.status = "APPROVED"
        self.assertFalse(hasattr(check, "approved"))
        self.assertFalse(hasattr(check, "consent_granted"))
        self.assertFalse(hasattr(check, "execute"))

    def test_pure_sidecar_byte_parser_matches_existing_file_loader(self):
        raw = self.kwargs()["model_sidecar_bytes"]
        common = dict(
            expected_project_head=self.head,
            expected_task_spec_sha256=self.binding["task_spec_sha256"],
            allowed_models=self.allowed,
        )
        with tempfile.TemporaryDirectory(prefix="r2a-pure-byte-parity-") as tmp:
            path = Path(tmp) / "binding.json"
            path.write_bytes(raw)
            from_file = load_model_binding(path, **common)
        from_bytes = parse_model_binding_bytes(raw, **common)
        self.assertEqual(from_bytes, from_file)
        self.assertEqual(from_bytes.raw_sha256, hashlib.sha256(raw).hexdigest())

    def test_codec_rejects_oversize_snapshot_and_invalid_raw_byte_type(self):
        for raw in (b"", b" " * (1048576 + 1), "not bytes"):
            with self.subTest(task=repr(type(raw))):
                self.check_failure("INVALID_TASKSPEC", normalized_taskspec_bytes=raw)
        for raw in (b"", b"x" * (16384 + 1), "not bytes"):
            with self.subTest(binding=repr(type(raw))):
                self.check_failure("MODEL_BINDING_REJECTED", model_sidecar_bytes=raw)


if __name__ == "__main__":
    unittest.main()
