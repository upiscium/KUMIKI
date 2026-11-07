"""KUMIKI #359 R2b D1 synthetic-only IPC/SQLite tests; NO real Host or Human grant."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from tools.kagari_task_model_binding import ModelSelection
from tools.kagari_task_model_consent_codec import canonical_json_bytes
from tools.kagari_model_consent_host_contract import (
    HostContractError, SyntheticHostObservation, parse_ipc_message,
    resolve_synthetic_candidate, make_status_reply, source_only_authorization,
)
from tools.kagari_model_consent_host_store import (
    SyntheticConsentLedger, HostStoreError,
    PENDING, SIMULATED, DENIED, REVOKED, EXPIRED,
    permitted_transition,
)


def synthetic_fixture():
    """All identities are test data and carry no authenticated provenance."""
    head = "a" * 40
    project = "/synthetic/project-read-only"
    clone = "/synthetic/workunit-clone"
    context = {
        "repository": "upiscium/KUMIKI",
        "task": "359",
        "job_id": "job-" + "1" * 24,
        "project_root": project,
        "project_device": 123,
        "project_inode": 456,
        "project_head": head,
        "project_index_sha256": "b" * 64,
        "project_agents_sha256": None,
        "project_opencode_sha256": None,
        "workunit_clone": clone,
        "model_executable": "/nix/store/synthetic-opencode/bin/opencode",
        "model_executable_sha256": "c" * 64,
        "package_root": "/nix/store/synthetic-kagari",
        "package_receipt_sha256": "d" * 64,
    }
    taskspec = {
        "schema_version": 1,
        "goal": "synthetic host model selection",
        "base_revision": head,
        "allowed_paths": ["src/example.py"],
        "tests": [{"argv": ["python", "-m", "unittest"], "cwd": "."}],
        "acceptance": "unit test",
        "subagent_count": 2,
        "planning": "specified",
    }
    normalized = canonical_json_bytes(taskspec)
    sidecar = {
        "schema_version": 1,
        "kind": "KAGARI_TASK_MODEL_BINDING",
        "project_head": head,
        "task_spec_sha256": hashlib.sha256(normalized).hexdigest(),
        "model": {
            "provider_id": "openai",
            "model_id": "gpt-6-astra",
            "variant": "max",
        },
        "roles": ["planner", "primary", "leaf-a", "leaf-b"],
    }
    asks = (
        {"kind": "AGENTS_SCOPED_EXCEPTION",
         "status": "NOT_REQUIRED_NO_CONFIRMED_CONFLICT",
         "reason": "HOST_VERIFIED_NO_AGENTS_CONFLICT"},
        {"kind": "NATIVE_TASK_PERMISSION",
         "status": "ACTIVE_SCOPED_GRANT_TEST_ONLY",
         "request_id": "2" * 24,
         "request_sha256": "3" * 64,
         "decision_sha256": "4" * 64,
         "runtime_source_sha256": "5" * 64,
         "project_root": clone,
         "granted_roles": ["kagari-v010-leaf-a", "kagari-v010-leaf-b"],
         "released": False,
         "production_qualified": False},
    )
    observation = SyntheticHostObservation(
        normalized_taskspec_bytes=normalized,
        model_sidecar_bytes=json.dumps(sidecar, indent=2).encode("utf8"),
        host_context=context,
        host_native_ask_readbacks=asks,
        trusted_model_allowlist=frozenset({
            ModelSelection("openai", "gpt-6-astra", "max")
        }),
        host_job_nonce="e" * 32,
        authority_epoch="epoch-" + "a" * 32,
        native_revision="native-generation-1",
        expires_tick=80,
    )
    packet = observation.reconstruct()
    ipc = canonical_json_bytes({
        "schema_version": 1,
        "kind": "KAGARI_CONSENT_HOST_IPC_V1",
        "operation": "submit_candidate",
        "job_id": context["job_id"],
        "request_id": packet.request_id,
        "packet_sha256": packet.request_sha256,
    })
    return observation, ipc


class TestBoundedMockIpc(unittest.TestCase):
    def setUp(self):
        self.observation, self.ipc = synthetic_fixture()

    def test_exact_synthetic_candidate_is_data_only(self):
        parsed = parse_ipc_message(self.ipc)
        self.assertEqual(parsed["operation"], "submit_candidate")
        candidate = resolve_synthetic_candidate(self.ipc, self.observation)
        self.assertEqual(candidate.state, "DATA_ONLY_NOT_AN_AUTHORIZATION")
        self.assertEqual(candidate.authority_epoch, self.observation.authority_epoch)
        self.assertEqual(candidate.native_revision, "native-generation-1")
        self.assertEqual(
            hashlib.sha256(candidate.packet_bytes).hexdigest(),
            candidate.packet_sha256,
        )
        self.assertNotIn("approved", parsed)

    def test_status_only_does_not_mutate_or_authorize(self):
        request = json.loads(self.ipc)
        request["operation"] = "status"
        parsed = parse_ipc_message(canonical_json_bytes(request))
        self.assertEqual(parsed["operation"], "status")
        with self.assertRaises(HostContractError) as caught:
            resolve_synthetic_candidate(canonical_json_bytes(request), self.observation)
        self.assertEqual(caught.exception.code, "OPERATION_NOT_SUPPORTED")

    def test_ipc_cannot_approve_or_supply_authority_fields(self):
        original = json.loads(self.ipc)
        for operation in ("approve", "deny", "revoke", "consume", "simulate_ack",
                          "credential_bound", "exec", "deploy", 1, None):
            with self.subTest(operation=repr(operation)):
                msg = dict(original, operation=operation)
                with self.assertRaises(HostContractError):
                    parse_ipc_message(canonical_json_bytes(msg))
        for field in ("approved", "credential", "host_verified", "native_ask",
                      "db_path", "state_directory", "fd", "socket", "caller_uid",
                      "environment", "project_root"):
            with self.subTest(field=field):
                msg = dict(original, **{field: True})
                with self.assertRaises(HostContractError):
                    parse_ipc_message(canonical_json_bytes(msg))

    def test_ipc_duplicate_noncanonical_nonfinite_invalid_unicode(self):
        bad = (
            b'{"a":1,"a":2}',
            b'{"x":Infinity}',
            b'{"x":NaN}',
            b"not-json",
            b'\xff',
            self.ipc + b" ",
            self.ipc.replace(b"\n", b"\r\n"),
            b"x" * 16385,
        )
        for raw in bad:
            with self.subTest(data=repr(raw[:16])):
                with self.assertRaises(HostContractError) as caught:
                    parse_ipc_message(raw)
                self.assertEqual(caught.exception.code, "INVALID_IPC")

    def test_wrong_identity_hash_and_job_is_rejected(self):
        original = json.loads(self.ipc)
        for field, value in (
            ("job_id", "job-" + "f" * 24),
            ("packet_sha256", "f" * 64),
            ("request_id", "f" * 24),
        ):
            with self.subTest(field=field):
                changed = dict(original)
                changed[field] = value
                with self.assertRaises(HostContractError) as caught:
                    resolve_synthetic_candidate(canonical_json_bytes(changed), self.observation)
                self.assertEqual(caught.exception.code, "CANDIDATE_IDENTITY_DRIFT")

    def test_native_revision_mutation_invalidates_original_request(self):
        original = self.observation
        changed_asks = list(copy.deepcopy(original.host_native_ask_readbacks))
        changed_asks[1]["request_sha256"] = "f" * 64
        changed = replace(original, host_native_ask_readbacks=tuple(changed_asks))
        with self.assertRaises(HostContractError):
            resolve_synthetic_candidate(self.ipc, changed)

    def test_exact_max_is_not_silent_fallback(self):
        changed = replace(self.observation,
                          trusted_model_allowlist=frozenset({
                              ModelSelection("openai", "gpt-6-astra", "high")
                          }))
        with self.assertRaises(HostContractError) as caught:
            resolve_synthetic_candidate(self.ipc, changed)
        self.assertEqual(caught.exception.code, "SYNTHETIC_INPUT_REJECTED")

    def test_status_response_explicit_no_authority(self):
        candidate = resolve_synthetic_candidate(self.ipc, self.observation)
        reply = make_status_reply(
            job_id=candidate.job_id, request_id=candidate.request_id,
            packet_sha256=candidate.packet_sha256,
            synthetic_state=PENDING, revision=0,
        )
        obj = json.loads(reply)
        self.assertEqual(obj["status"], "DATA_ONLY_NOT_AN_AUTHORIZATION")
        self.assertFalse(obj["human_authenticated"])
        self.assertFalse(obj["native_grant_attested"])
        self.assertFalse(obj["model_dispatched"])
        self.assertNotIn("credential", obj)

    def test_nonstring_state_is_typed_rejection(self):
        candidate = resolve_synthetic_candidate(self.ipc, self.observation)
        for value in ([], {}, None, True):
            with self.subTest(value=repr(value)):
                with self.assertRaises(HostContractError):
                    make_status_reply(
                        job_id=candidate.job_id, request_id=candidate.request_id,
                        packet_sha256=candidate.packet_sha256,
                        synthetic_state=value, revision=0,
                    )

    def test_no_real_authorize_entrypoint(self):
        with self.assertRaises(HostContractError) as caught:
            source_only_authorization(self.ipc)
        self.assertEqual(caught.exception.code, "HOST_AUTHORIZATION_NOT_IMPLEMENTED")

    def test_packet_size_and_error_type_boundaries(self):
        for value in ("not bytes", None, [], b"", b"\x00"):
            with self.subTest(value=type(value)):
                with self.assertRaises(HostContractError):
                    parse_ipc_message(value)


class TestSyntheticLedger(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kumiki359-r2b-d1-sqlite-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.sqlite3"
        self.epoch = "epoch-" + "a" * 32
        self.conn = sqlite3.connect(self.path, timeout=3.0, isolation_level=None)
        self.addCleanup(self.conn.close)
        self.ledger = SyntheticConsentLedger(self.conn, self.epoch)
        self.observation, self.ipc = synthetic_fixture()
        self.candidate = resolve_synthetic_candidate(self.ipc, self.observation)

    def register(self, tick=10):
        return self.ledger.register(
            ipc_bytes=self.ipc, observation=self.observation, tick=tick
        )

    def transition(self, target, *, revision=0, tick=20, native=None, sha=None):
        return self.ledger.transition(
            job_id=self.candidate.job_id, expected_revision=revision,
            target=target, packet_sha=sha or self.candidate.packet_sha256,
            native_revision=native or self.candidate.native_revision, tick=tick,
        )

    def test_first_persistence_is_unapproved(self):
        result = self.register()
        self.assertEqual(result["state"], PENDING)
        self.assertEqual(result["revision"], 0)
        self.assertFalse(result["human_authenticated"])
        self.assertFalse(result["model_dispatched"])
        self.assertEqual(result["status"], "DATA_ONLY_NOT_AN_AUTHORIZATION")

    def test_duplicate_nonce_job_request_rejected(self):
        self.register()
        with self.assertRaises(HostStoreError) as c:
            self.register()
        self.assertEqual(c.exception.code, "DUPLICATE_IDENTITY")
        self.assertEqual(self.ledger.read(self.candidate.job_id)["revision"], 0)

    def test_simulated_ack_is_never_credentialed(self):
        self.register()
        r = self.transition(SIMULATED)
        self.assertEqual(r["state"], SIMULATED)
        self.assertEqual(r["revision"], 1)
        self.assertFalse(r["human_authenticated"])
        with self.assertRaises(HostStoreError):
            self.ledger.authorize_execution(self.candidate.job_id)

    def test_denial_is_terminal(self):
        self.register()
        self.assertEqual(self.transition(DENIED)["state"], DENIED)
        with self.assertRaises(HostStoreError):
            self.transition(SIMULATED, revision=1, tick=25)

    def test_revocation_after_simulation_is_terminal(self):
        self.register()
        self.transition(SIMULATED)
        result = self.transition(REVOKED, revision=1, tick=25)
        self.assertEqual(result["state"], REVOKED)
        with self.assertRaises(HostStoreError):
            self.transition(SIMULATED, revision=2, tick=30)

    def test_reject_stale_revision_even_if_same_target(self):
        self.register()
        self.transition(SIMULATED)
        with self.assertRaises(HostStoreError) as caught:
            self.transition(REVOKED, revision=0, tick=22)
        self.assertEqual(caught.exception.code, "STALE_REVISION")

    def test_native_revision_and_request_digest_drift(self):
        self.register()
        with self.assertRaises(HostStoreError) as caught:
            self.transition(SIMULATED, native="native-generation-other")
        self.assertEqual(caught.exception.code, "NATIVE_REVISION_MISMATCH")
        with self.assertRaises(HostStoreError) as caught:
            self.transition(SIMULATED, sha="f" * 64)
        self.assertEqual(caught.exception.code, "PACKET_MISMATCH")
        self.assertEqual(self.ledger.read(self.candidate.job_id)["state"], PENDING)

    def test_expiry_and_backward_clock_fail_closed(self):
        self.register(tick=10)
        with self.assertRaises(HostStoreError):
            self.transition(SIMULATED, tick=81)
        with self.assertRaises(HostStoreError):
            self.transition(SIMULATED, tick=9)
        result = self.transition(EXPIRED, tick=80)
        self.assertEqual(result["state"], EXPIRED)

    def test_expiry_boundary_is_exclusive_for_simulation(self):
        self.register(tick=10)
        self.assertFalse(
            permitted_transition(
                PENDING, SIMULATED, tick=80, last_tick=10, expires_tick=80
            )
        )
        with self.assertRaises(HostStoreError):
            self.transition(SIMULATED, tick=80)
        self.assertEqual(self.transition(EXPIRED, tick=80)["state"], EXPIRED)

    def test_forged_request_digest_cannot_be_registered(self):
        from dataclasses import replace
        original = json.loads(self.candidate.packet_bytes)
        original["request_id"] = "f" * 24
        forged_raw = canonical_json_bytes(original)
        forged_ipc = json.loads(self.ipc)
        forged_ipc["request_id"] = "f" * 24
        forged_ipc["packet_sha256"] = hashlib.sha256(forged_raw).hexdigest()
        with self.assertRaises(HostStoreError) as caught:
            self.ledger.register(
                ipc_bytes=canonical_json_bytes(forged_ipc),
                observation=self.observation, tick=10
            )
        self.assertEqual(caught.exception.code, "CANDIDATE_IDENTITY_DRIFT")

    def test_forged_authority_label_not_registerable(self):
        from dataclasses import replace
        forged_ipc = json.loads(self.ipc)
        forged_ipc["approved"] = "CREDENTIAL_BOUND_CONSENT_NO_EXECUTION"
        with self.assertRaises(HostStoreError):
            self.ledger.register(
                ipc_bytes=canonical_json_bytes(forged_ipc),
                observation=self.observation, tick=10
            )

    def test_direct_selfconsistent_incomplete_packet_is_rejected(self):
        from dataclasses import replace
        forged = {
            "kind": "KAGARI_TASK_MODEL_CONSENT_PACKET_V1",
            "context": {"job_id": self.candidate.job_id},
            "host_job_nonce": self.candidate.host_job_nonce,
            "model_dispatched": False,
            "production_activation": False,
            "provider_identity_attested": False,
            "native_grant_provenance_attested": False,
        }
        synthetic_id = hashlib.sha256(canonical_json_bytes(forged)).hexdigest()[:24]
        forged["request_id"] = synthetic_id
        raw = canonical_json_bytes(forged)
        forged_ipc = json.loads(self.ipc)
        forged_ipc["request_id"] = synthetic_id
        forged_ipc["packet_sha256"] = hashlib.sha256(raw).hexdigest()
        with self.assertRaises(HostStoreError):
            self.ledger.register(
                ipc_bytes=canonical_json_bytes(forged_ipc),
                observation=self.observation, tick=10
            )

    def test_schema_with_matching_names_but_missing_unique_rejected(self):
        from tools.kagari_model_consent_host_store import SQL_CREATE
        with tempfile.TemporaryDirectory(prefix="kumiki359-r2b-schema-") as root:
            db = sqlite3.connect(Path(root) / "untrusted-schema.sqlite3",
                                 isolation_level=None)
            self.addCleanup(db.close)
            invalid = SQL_CREATE.replace(
                "nonce TEXT NOT NULL UNIQUE", "nonce TEXT NOT NULL"
            ).replace(
                "request_id TEXT NOT NULL UNIQUE", "request_id TEXT NOT NULL"
            )
            db.executescript(invalid)
            db.execute(
                "INSERT INTO epoch_metadata(fixed_id,epoch,schema_version)"
                " VALUES(1,?,1)", (self.epoch,),
            )
            with self.assertRaises(HostStoreError):
                SyntheticConsentLedger(db, self.epoch)

    def test_malformed_persisted_approved_state_rejected(self):
        self.register()
        self.conn.execute(
            "UPDATE consent SET state='APPROVED' WHERE job_id=?",
            (self.candidate.job_id,),
        )
        with self.assertRaises(HostStoreError):
            self.ledger.read(self.candidate.job_id)
        another = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(another.close)
        with self.assertRaises(HostStoreError):
            SyntheticConsentLedger(another, self.epoch)

    def test_stored_packet_byte_tamper_is_recovery_uncertain(self):
        self.register()
        self.conn.execute(
            "UPDATE consent SET packet=? WHERE job_id=?",
            (b'{"corrupt":true}\n', self.candidate.job_id),
        )
        with self.assertRaises(HostStoreError) as caught:
            self.ledger.read(self.candidate.job_id)
        self.assertEqual(caught.exception.code, "RECOVERY_UNCERTAIN")

    def test_schema_extra_trigger_fails_closed_on_reopen(self):
        self.register()
        self.conn.execute(
            "CREATE TRIGGER injected AFTER UPDATE ON consent BEGIN SELECT 1; END"
        )
        another = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(another.close)
        with self.assertRaises(HostStoreError) as caught:
            SyntheticConsentLedger(another, self.epoch)
        self.assertEqual(caught.exception.code, "RECOVERY_UNCERTAIN")

    def _replace_stored_packet_synthetic_only(self, update):
        self.register()
        packet = json.loads(self.candidate.packet_bytes)
        update(packet)
        new_id = hashlib.sha256(canonical_json_bytes({
            key: value for key, value in packet.items() if key != "request_id"
        })).hexdigest()[:24]
        packet["request_id"] = new_id
        raw = canonical_json_bytes(packet)
        digest = hashlib.sha256(raw).hexdigest()
        self.conn.execute(
            "UPDATE consent SET packet=?,request_id=?,packet_sha=? WHERE job_id=?",
            (raw, new_id, digest, self.candidate.job_id),
        )
        return digest

    def test_recovery_rejects_invalid_native_revision_grammar(self):
        self.register()
        self.conn.execute(
            "UPDATE main.consent SET native_revision=? WHERE job_id=?",
            ("not a valid revision", self.candidate.job_id),
        )
        with self.assertRaises(HostStoreError) as reading:
            self.ledger.read(self.candidate.job_id)
        self.assertEqual(reading.exception.code, "RECOVERY_UNCERTAIN")
        with self.assertRaises(HostStoreError) as changing:
            self.transition(REVOKED, revision=0)
        self.assertEqual(changing.exception.code, "RECOVERY_UNCERTAIN")
        reopened = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(reopened.close)
        with self.assertRaises(HostStoreError) as recovering:
            SyntheticConsentLedger(reopened, self.epoch)
        self.assertEqual(recovering.exception.code, "RECOVERY_UNCERTAIN")

    def test_recovery_rejects_nested_native_ask_nulls_after_rehash(self):
        digest = self._replace_stored_packet_synthetic_only(
            lambda obj: obj.update(native_asks=[None, None])
        )
        with self.assertRaises(HostStoreError) as caught:
            self.ledger.read(self.candidate.job_id)
        self.assertEqual(caught.exception.code, "RECOVERY_UNCERTAIN")
        with self.assertRaises(HostStoreError):
            self.ledger.transition(
                job_id=self.candidate.job_id, expected_revision=0,
                target=SIMULATED, packet_sha=digest,
                native_revision=self.candidate.native_revision, tick=20,
            )
        other = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(other.close)
        with self.assertRaises(HostStoreError):
            SyntheticConsentLedger(other, self.epoch)

    def test_recovery_rejects_invalid_model_and_context_after_rehash(self):
        for modification in (
            lambda obj: obj["model_selection"].update(variant=None),
            lambda obj: obj["model_selection"].update(provider_id=""),
            lambda obj: obj.update(context={"job_id": self.candidate.job_id}),
        ):
            with self.subTest(modification=repr(modification)):
                # Restore a pristine disposable DB record for each mutation.
                self.conn.execute("DELETE FROM consent")
                digest = self._replace_stored_packet_synthetic_only(modification)
                with self.assertRaises(HostStoreError) as caught:
                    self.ledger.read(self.candidate.job_id)
                self.assertEqual(caught.exception.code, "RECOVERY_UNCERTAIN")
                with self.assertRaises(HostStoreError):
                    self.ledger.transition(
                        job_id=self.candidate.job_id, expected_revision=0,
                        target=SIMULATED, packet_sha=digest,
                        native_revision=self.candidate.native_revision, tick=20,
                    )
                another = sqlite3.connect(self.path, isolation_level=None)
                try:
                    with self.assertRaises(HostStoreError):
                        SyntheticConsentLedger(another, self.epoch)
                finally:
                    another.close()

    def test_recovery_rejects_preexisting_temp_consent_shadow(self):
        self.conn.execute(
            "CREATE TEMP TABLE consent AS SELECT * FROM main.consent WHERE 0"
        )
        with self.assertRaises(HostStoreError) as failure:
            SyntheticConsentLedger(self.conn, self.epoch)
        self.assertEqual(failure.exception.code, "RECOVERY_UNCERTAIN")

    def test_late_temp_shadow_cannot_bypass_main_uniqueness(self):
        self.conn.execute(
            "CREATE TEMP TABLE consent AS SELECT * FROM main.consent WHERE 0"
        )
        first = self.register()
        self.assertEqual(first["state"], PENDING)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM main.consent").fetchone()[0], 1
        )
        with self.assertRaises(HostStoreError) as failure:
            self.register()
        self.assertEqual(failure.exception.code, "DUPLICATE_IDENTITY")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM main.consent").fetchone()[0], 1
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM temp.consent").fetchone()[0], 0
        )

    def test_recovery_rejects_simulated_state_at_revision_zero(self):
        self.register()
        self.conn.execute(
            "UPDATE consent SET state=?, revision=? WHERE job_id=?",
            (SIMULATED, 0, self.candidate.job_id),
        )
        with self.assertRaises(HostStoreError) as read:
            self.ledger.read(self.candidate.job_id)
        self.assertEqual(read.exception.code, "RECOVERY_UNCERTAIN")
        with self.assertRaises(HostStoreError) as update:
            self.transition(REVOKED, revision=0)
        self.assertEqual(update.exception.code, "RECOVERY_UNCERTAIN")
        reopen = sqlite3.connect(self.path, timeout=3.0, isolation_level=None)
        self.addCleanup(reopen.close)
        with self.assertRaises(HostStoreError) as recovery:
            SyntheticConsentLedger(reopen, self.epoch)
        self.assertEqual(recovery.exception.code, "RECOVERY_UNCERTAIN")

    def test_recovery_rejects_pending_state_at_revision_one(self):
        self.register()
        self.conn.execute(
            "UPDATE consent SET state=?, revision=? WHERE job_id=?",
            (PENDING, 1, self.candidate.job_id),
        )
        with self.assertRaises(HostStoreError) as read:
            self.ledger.read(self.candidate.job_id)
        self.assertEqual(read.exception.code, "RECOVERY_UNCERTAIN")
        with self.assertRaises(HostStoreError) as update:
            self.transition(DENIED, revision=1)
        self.assertEqual(update.exception.code, "RECOVERY_UNCERTAIN")
        reopen = sqlite3.connect(self.path, timeout=3.0, isolation_level=None)
        self.addCleanup(reopen.close)
        with self.assertRaises(HostStoreError) as recovery:
            SyntheticConsentLedger(reopen, self.epoch)
        self.assertEqual(recovery.exception.code, "RECOVERY_UNCERTAIN")

    def test_bad_state_never_becomes_approved_or_consumed(self):
        self.register()
        for target in ("APPROVED", "CREDENTIAL_BOUND_CONSENT_NO_EXECUTION",
                       "CONSUMED", "DISPATCHED", "SIMULATED_TTY_ACK",
                       "PROMOTED", "", True):
            with self.subTest(target=repr(target)):
                with self.assertRaises(HostStoreError):
                    self.transition(target)
        self.assertEqual(self.ledger.read(self.candidate.job_id)["state"], PENDING)

    def test_epoch_change_is_detected_on_reopen(self):
        self.register()
        other = sqlite3.connect(self.path, timeout=3.0, isolation_level=None)
        self.addCleanup(other.close)
        with self.assertRaises(HostStoreError) as ctx:
            SyntheticConsentLedger(other, "epoch-" + "f" * 32)
        self.assertEqual(ctx.exception.code, "RECOVERY_UNCERTAIN")

    def test_reopen_same_epoch_preserves_test_only_state(self):
        self.register()
        self.transition(DENIED)
        another = sqlite3.connect(self.path, timeout=3.0, isolation_level=None)
        self.addCleanup(another.close)
        restarted = SyntheticConsentLedger(another, self.epoch)
        self.assertEqual(restarted.read(self.candidate.job_id)["state"], DENIED)
        with self.assertRaises(HostStoreError):
            restarted.transition(
                job_id=self.candidate.job_id, expected_revision=1,
                target=SIMULATED, packet_sha=self.candidate.packet_sha256,
                native_revision=self.candidate.native_revision, tick=25,
            )

    def test_unknown_existing_database_schema_is_blocked(self):
        with tempfile.TemporaryDirectory(prefix="kumiki359-r2b-wrong-db-") as path:
            connection = sqlite3.connect(Path(path) / "wrong.sqlite3", isolation_level=None)
            self.addCleanup(connection.close)
            connection.execute("CREATE TABLE hostile(x INTEGER)")
            with self.assertRaises(HostStoreError):
                SyntheticConsentLedger(connection, self.epoch)

    def test_transaction_rollback_after_injected_failure(self):
        self.register()
        with self.assertRaises(RuntimeError):
            with self.ledger._tx() as db:
                db.execute("UPDATE consent SET state=? WHERE job_id=?",
                           (SIMULATED, self.candidate.job_id))
                raise RuntimeError("synthetic crash before commit")
        self.assertEqual(self.ledger.read(self.candidate.job_id)["state"], PENDING)

    def test_caller_transaction_survives_failed_nested_begin(self):
        self.register()
        self.conn.execute("BEGIN")
        try:
            self.conn.execute(
                "UPDATE main.consent SET last_tick=11 WHERE job_id=?",
                (self.candidate.job_id,),
            )
            with self.assertRaises(HostStoreError) as blocked:
                self.transition(SIMULATED, revision=0, tick=20)
            self.assertEqual(blocked.exception.code, "INVALID_TRANSACTION_STATE")
            self.assertTrue(self.conn.in_transaction)
            self.assertEqual(
                self.conn.execute(
                    "SELECT last_tick FROM main.consent WHERE job_id=?",
                    (self.candidate.job_id,),
                ).fetchone()[0], 11,
            )
        finally:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
        self.assertEqual(
            self.conn.execute(
                "SELECT last_tick FROM main.consent WHERE job_id=?",
                (self.candidate.job_id,),
            ).fetchone()[0], 10,
        )

    def test_backup_rewind_is_unprovable_without_external_epoch(self):
        # A valid old backup with the SAME epoch cannot be distinguished by D1.
        self.register()
        backup = Path(self.temp.name) / "old-backup.sqlite3"
        with sqlite3.connect(backup) as backup_db:
            self.conn.backup(backup_db)
        self.transition(DENIED)
        restored = sqlite3.connect(backup, timeout=3.0, isolation_level=None)
        self.addCleanup(restored.close)
        reopened = SyntheticConsentLedger(restored, self.epoch)
        self.assertEqual(reopened.read(self.candidate.job_id)["state"], PENDING)
        # That does NOT enable any Human or executable permission.
        with self.assertRaises(HostStoreError):
            reopened.authorize_execution()
        # D2 needs an external, protected epoch or fail-closed restore policy.
        other = sqlite3.connect(backup, timeout=3.0, isolation_level=None)
        self.addCleanup(other.close)
        with self.assertRaises(HostStoreError):
            SyntheticConsentLedger(other, "epoch-" + "f" * 32)

    def test_transition_receipt_is_own_commit_even_after_successor(self):
        from contextlib import contextmanager
        from unittest import mock

        self.register()
        other = sqlite3.connect(self.path, timeout=3.0, isolation_level=None)
        self.addCleanup(other.close)
        peer = SyntheticConsentLedger(other, self.epoch)
        original_tx = self.ledger._tx

        @contextmanager
        def interleave_successor_after_commit():
            with original_tx() as connection:
                yield connection
            peer.transition(
                job_id=self.candidate.job_id, expected_revision=1,
                target=REVOKED, packet_sha=self.candidate.packet_sha256,
                native_revision=self.candidate.native_revision, tick=25,
            )

        with mock.patch.object(self.ledger, "_tx", interleave_successor_after_commit):
            result = self.transition(SIMULATED, revision=0, tick=20)
        self.assertEqual((result["state"], result["revision"]), (SIMULATED, 1))
        self.assertEqual(
            (peer.read(self.candidate.job_id)["state"],
             peer.read(self.candidate.job_id)["revision"]), (REVOKED, 2),
        )

    def test_register_receipt_is_own_commit_even_after_successor(self):
        from contextlib import contextmanager
        from unittest import mock

        other = sqlite3.connect(self.path, timeout=3.0, isolation_level=None)
        self.addCleanup(other.close)
        peer = SyntheticConsentLedger(other, self.epoch)
        original_tx = self.ledger._tx

        @contextmanager
        def interleave_successor_after_commit():
            with original_tx() as connection:
                yield connection
            peer.transition(
                job_id=self.candidate.job_id, expected_revision=0,
                target=DENIED, packet_sha=self.candidate.packet_sha256,
                native_revision=self.candidate.native_revision, tick=20,
            )

        with mock.patch.object(self.ledger, "_tx", interleave_successor_after_commit):
            result = self.register()
        self.assertEqual((result["state"], result["revision"]), (PENDING, 0))
        self.assertEqual(
            (peer.read(self.candidate.job_id)["state"],
             peer.read(self.candidate.job_id)["revision"]), (DENIED, 1),
        )

    def test_two_concurrent_conflicting_decisions_are_serialized(self):
        self.register()
        barrier = threading.Barrier(2)
        outcomes = []
        lock = threading.Lock()

        def worker(target):
            local = sqlite3.connect(self.path, timeout=4.0,
                                    isolation_level=None, check_same_thread=False)
            try:
                ledger = SyntheticConsentLedger(local, self.epoch)
                barrier.wait(timeout=4.0)
                try:
                    result = ledger.transition(
                        job_id=self.candidate.job_id, expected_revision=0,
                        target=target, packet_sha=self.candidate.packet_sha256,
                        native_revision=self.candidate.native_revision, tick=20,
                    )
                    record = ("OK", result["state"])
                except HostStoreError as exc:
                    record = ("REJECTED", exc.code)
                with lock:
                    outcomes.append(record)
            finally:
                local.close()

        threads = [threading.Thread(target=worker, args=(x,))
                   for x in (SIMULATED, DENIED)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=8.0)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(sum(x[0] == "OK" for x in outcomes), 1)
        self.assertEqual(sum(x[0] == "REJECTED" for x in outcomes), 1)
        self.assertEqual(self.ledger.read(self.candidate.job_id)["revision"], 1)

    def test_permitted_transition_synthetic_boundary_only(self):
        self.assertTrue(permitted_transition(PENDING, SIMULATED, tick=20,
                                             last_tick=10, expires_tick=80))
        self.assertFalse(permitted_transition(REVOKED, SIMULATED, tick=20,
                                              last_tick=10, expires_tick=80))
        self.assertFalse(permitted_transition(PENDING, SIMULATED, tick=9,
                                              last_tick=10, expires_tick=80))
        self.assertFalse(permitted_transition(PENDING, SIMULATED, tick=81,
                                              last_tick=10, expires_tick=80))
        self.assertTrue(permitted_transition(PENDING, EXPIRED, tick=80,
                                             last_tick=10, expires_tick=80))


if __name__ == "__main__":
    unittest.main()
