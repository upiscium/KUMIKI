"""Offline synthetic-only Phase C; every write stays inside this checkout.

Positive storage tests replace ONLY the enclosing-Git observation: their private
scratch directory models an external fixture universe. Real Git-root rejection
is checked separately. No real auth/profile, subprocess, tool, network or model.
"""
from __future__ import annotations

import ast
import builtins
from dataclasses import FrozenInstanceError, asdict, replace
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import Mock, call, patch


_import = builtins.__import__


def _no_transport(name, globals=None, locals=None, fromlist=(), level=0):
    if any("claude_transport" in part for part in (name, *(fromlist or ()))):
        raise AssertionError("real transport import")
    return _import(name, globals, locals, fromlist, level)


with patch("builtins.__import__", side_effect=_no_transport):
    from tools import _kagari_vnext_claude_r6b_phase_c as candidate

from tools import _kagari_vnext_claude_r6b_phase_b as phase_b
from tools import kagari_vnext_claude_lane_b as lane
from tools import kagari_vnext_main as mainmod


ROOT = Path(__file__).resolve().parents[1]
SESSION = "b6a7f5a3-0da4-4141-b98d-89311798cab4"
OTHER_SESSION = "12cadac6-0e38-4472-9221-d1fb73f4ba5c"
RESERVATION_EVENTS = (
    "reservation.create", "reservation.file_fsync", "namespace.dir_fsync",
    "pending.create", "pending.file_fsync", "namespace.dir_fsync", "successful_report",
)


class PhaseCFixtureTests(unittest.TestCase):
    def setUp(self):
        for name in ("subprocess.Popen", "subprocess.run", "os.system", "os.posix_spawn",
                     "os.posix_spawnp", "os.fork", "socket.socket", "socket.create_connection"):
            guard = self.enterContext(patch(name, side_effect=AssertionError(name)))
            self.addCleanup(guard.assert_not_called)
        for module, names in (
            (lane.preflight, ("run_preflight", "_run_bounded", "_inspect_settings",
                              "_read_config", "_read_regular_file")),
            (lane, ("_exercise_fixture", "_auth_probe", "_config_context", "_task_identity",
                    "observe", "_runtime_root")),
            (mainmod, ("start_root_session", "opencode_request")),
            (phase_b, ("_observe_main_intent",)),
        ):
            for name in names:
                guard = self.enterContext(patch.object(module, name, side_effect=AssertionError(name)))
                self.addCleanup(guard.assert_not_called)
        temp = tempfile.TemporaryDirectory(prefix=".r6b-phase-c-", dir=ROOT)
        self.addCleanup(temp.cleanup)
        self.scratch = Path(temp.name)
        self.real_outside_git = candidate._outside_git

        def synthetic_external(path):
            self.assertTrue(path.is_relative_to(self.scratch))
            return True

        self.enterContext(patch.object(candidate, "_outside_git", side_effect=synthetic_external))
        project = self.scratch / "synthetic-project"
        self.task = lane.LaneBTask(project, project / "worktree", "acme/widgets", 340,
                                  "refs/heads/issue-340", "a" * 40, "b" * 64)
        self.intent, self.evidence = self.fake_policy()

    def fake_policy(self, *, session=SESSION, managed=False, empty=False):
        surfaces = tuple(candidate._Surface(
            name,
            "PRESENT" if name == "settings" or managed and name.startswith("managed_")
            else "PRESENT_EMPTY" if empty and name in {"hooks", "mcp", "plugins", "tools"}
            else "ABSENT",
            "3" * 64 if name == "settings" else "6" * 64 if managed and name.startswith("managed_")
            else candidate._EMPTY if empty and name in {"hooks", "mcp", "plugins", "tools"}
            else candidate._ABSENT,
        ) for name in candidate._SURFACES)
        pins = phase_b._PolicyPins("1" * 64, "2" * 64, "3" * 64, "4" * 64,
                                   phase_b._digest([asdict(s) for s in surfaces]))
        task = self.task
        # Deliberately fake Task/intent, not a new authority registry or Main approval.
        intent = phase_b._OperationIntent(
            lane.OPERATION, phase_b._POLICY_VERSION, "claude-cli", task.repository, task.remote,
            task.issue, task.branch_ref, task.base_revision, task.expected_contract_id,
            str(task.project_root), str(task.worktree), str(task.project_root / ".git"),
            "c" * 64, session, phase_b._digest(lane._argv(session)), pins,
            phase_b._policy_digest(session, pins))
        base = phase_b._PolicyEvidence(
            phase_b._SYNTHETIC_SOURCE, phase_b._digest(asdict(intent)), session,
            intent.policy_sha256, pins, lane.EXPECTED_VERSION, "personal", (), phase_b._REQUIRED_CHECKS)
        return intent, candidate._EffectivePolicyEvidence(base, surfaces)

    def assert_non_grant(self, result):
        self.assertEqual(set(result), {"qualification", "dispatch_allowed", "task_completed",
                                      "ask_capability", "effective_policy", "status", "reason",
                                      "stage", "session_id"})
        self.assertEqual(result["qualification"], "UNQUALIFIED")
        self.assertIs(result["dispatch_allowed"], False)
        self.assertIs(result["task_completed"], False)
        self.assertEqual(result["ask_capability"], "UNQUALIFIED")
        self.assertEqual(result["effective_policy"], "UNVERIFIED")
        self.assertIn(result["status"], ("UNKNOWN", "UNQUALIFIED"))
        self.assertNotIn(str(self.scratch), json.dumps(result))
        self.assertNotIn("secret-marker", json.dumps(result))

    def assert_unknown(self, result):
        self.assert_non_grant(result)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "RECEIPT_RECONCILIATION_REQUIRED")
        self.assertEqual(result["stage"], "NOT_OBSERVED")

    def fixture(self):
        fixture, result = candidate._new_disposable_fixture(self.scratch, self.intent)
        self.assert_non_grant(result)
        self.assertIsNotNone(fixture, result)
        return fixture

    def namespace(self, fixture):
        return fixture.root / phase_b._pending_contract(self.intent).namespace

    def reserve(self, fixture):
        result = candidate._reserve_fixture(fixture, self.intent, self.evidence)
        self.assert_non_grant(result)
        return result

    def reserve_with_trace(self, fixture, *, fail_at=None):
        namespace = self.namespace(fixture)
        events, files = [], {}
        sync_count = 0
        real_open, real_sync, public_result = os.open, os.fsync, lane._public_result

        def opening(path, flags, *args, **kwargs):
            self.assertTrue(Path(path).is_relative_to(self.scratch) if Path(path).is_absolute()
                            else str(path) in (*candidate._FILES, namespace.name))
            if flags & os.O_EXCL:
                self.assertIn(path, ("reservation.json", "pending.json"))
                for required in (os.O_CREAT, os.O_WRONLY, os.O_NOFOLLOW, os.O_CLOEXEC):
                    self.assertTrue(flags & required)
                self.assertEqual(candidate._dir_id(os.fstat(kwargs["dir_fd"])), fixture.namespace_id)
                if path == "pending.json":
                    # Check BEFORE creating pending, not merely the eventual fsync list.
                    self.assertEqual(tuple(events), RESERVATION_EVENTS[:3])
            fd = real_open(path, flags, *args, **kwargs)
            if flags & os.O_EXCL:
                info = os.fstat(fd)
                files[info.st_dev, info.st_ino] = Path(path).stem
                events.append(Path(path).stem + ".create")
            return fd

        def syncing(fd):
            nonlocal sync_count
            info = os.fstat(fd)
            if stat.S_ISDIR(info.st_mode):
                self.assertEqual(candidate._dir_id(info), fixture.namespace_id)
                event = "namespace.dir_fsync"
            else:
                self.assertIn((info.st_dev, info.st_ino), files)
                event = files[info.st_dev, info.st_ino] + ".file_fsync"
            sync_count += 1
            if sync_count == fail_at:
                events.append(event + ".failed")
                raise OSError("secret-marker")
            real_sync(fd)
            events.append(event)

        def reporting(*args, **kwargs):
            result = public_result(*args, **kwargs)
            if result["stage"] == "FIXTURE_PENDING_DURABLE":
                events.append("successful_report")
            return result

        with patch.object(os, "open", side_effect=opening), patch.object(
                os, "fsync", side_effect=syncing), patch.object(
                lane, "_public_result", side_effect=reporting):
            result = self.reserve(fixture)
        return result, tuple(events)

    def write(self, fixture, name, value):
        data = value if type(value) is bytes else lane._canonical_json(value)
        path = self.namespace(fixture) / name
        path.write_bytes(data)
        path.chmod(0o600)
        return path

    def result_payload(self, **changes):
        pending = phase_b._pending_contract(self.intent)
        return asdict(phase_b._ResultReceipt(
            1, phase_b._digest(asdict(pending)), "OBSERVED",
            "SYNTHETIC_TURN_RESULT", "TURN_RESULT_OBSERVED")) | changes

    def test_pure_manifest_and_synthetic_policy_never_read_or_grant(self):
        with patch.object(Path, "open", side_effect=AssertionError("read")), patch.object(
                Path, "stat", side_effect=AssertionError("stat")):
            manifest = candidate._manifest(self.intent)
            result = candidate._observe_effective_policy(self.intent, self.evidence)
        self.assert_non_grant(result)
        self.assertEqual(result["stage"], "SYNTHETIC_EFFECTIVE_POLICY_OBSERVED")
        self.assertLess(len(manifest), candidate._LIMIT)
        self.assertEqual(manifest, lane._canonical_json(json.loads(manifest)))
        for private in (SESSION.encode(), str(self.scratch).encode(), lane.FIXED_PROMPT.encode()):
            self.assertNotIn(private, manifest)
        with self.assertRaises(FrozenInstanceError):
            self.evidence.surfaces[0].presence = "UNKNOWN"

    def test_matched_settings_hooks_mcp_plugins_tools_and_managed_presence_absence(self):
        for managed in (False, True):
            for empty in (False, True):
                with self.subTest(managed=managed, empty=empty):
                    intent, evidence = self.fake_policy(managed=managed, empty=empty)
                    with patch.object(phase_b, "_observe_policy", wraps=phase_b._observe_policy) as reuse:
                        result = candidate._observe_effective_policy(intent, evidence)
                    reuse.assert_called_once_with(intent, evidence.base)
                    self.assert_non_grant(result)
                    self.assertEqual(result["stage"], "SYNTHETIC_EFFECTIVE_POLICY_OBSERVED")

    def test_policy_missing_duplicate_mutable_unknown_or_active_surfaces_refuse(self):
        good = self.evidence
        cases = [None, asdict(good), replace(good, surfaces=list(good.surfaces)),
                 replace(good, surfaces=good.surfaces[:-1]),
                 replace(good, surfaces=good.surfaces + good.surfaces[:1])]
        for index, surface in enumerate(good.surfaces):
            for change in ({"name": "unknown"}, {"presence": "UNKNOWN"}, {"presence": "PRESENT"},
                           {"sha256": "f" * 64}, {"sha256": []}):
                cases.append(replace(good, surfaces=good.surfaces[:index] +
                                     (replace(surface, **change),) + good.surfaces[index + 1:]))
        for evidence in cases:
            with self.subTest(evidence=evidence):
                result = candidate._observe_effective_policy(self.intent, evidence)
                self.assert_non_grant(result)
                # The one no-op settings presence mutation is still synthetic only.
                if evidence != good:
                    self.assertEqual(result["stage"], "NOT_OBSERVED")
        with self.assertRaises(TypeError):
            candidate._EffectivePolicyEvidence(good.base, good.surfaces, live=True)

    def test_cli_argv_live_labels_environment_and_phase_b_check_drift_never_qualify(self):
        for change in ({"source": "CLI_FLAGS"}, {"source": "LIVE_TRUSTED_QUALIFIER"},
                       {"environment_overrides": ("CLAUDE_CONFIG_DIR",)},
                       {"checks": phase_b._REQUIRED_CHECKS[:-1]}, {"profile": "company"}):
            with self.subTest(change=change):
                evidence = replace(self.evidence, base=replace(self.evidence.base, **change))
                result = candidate._observe_effective_policy(self.intent, evidence)
                self.assert_non_grant(result)
                self.assertEqual(result["stage"], "NOT_OBSERVED")
        self.assertIsNone(candidate._manifest(replace(self.intent, argv_sha256="f" * 64)))

    def test_reservation_reaches_policy_gate_and_consumes_freshness_on_rejection(self):
        cases = (
            ("missing", None), ("wrong-type", asdict(self.evidence)),
            ("bad-source", replace(self.evidence, base=replace(self.evidence.base, source="CLI_FLAGS"))),
            ("mismatched-session", replace(self.evidence, base=replace(
                self.evidence.base, session_id=OTHER_SESSION))),
            ("nonmatching-stage", self.evidence),
        )
        for label, evidence in cases:
            with self.subTest(case=label):
                fixture = self.fixture()
                namespace = self.namespace(fixture)
                before = {p.name: p.read_bytes() for p in namespace.iterdir()}
                self.assertEqual(set(before), {"manifest.json"})
                self.assertIs(fixture._fresh, True)
                with patch.object(candidate, "_exclusive", wraps=candidate._exclusive) as writer, patch.object(
                        candidate, "_checked_inventory", wraps=candidate._checked_inventory) as inventory:
                    with patch.object(candidate, "_observe_effective_policy",
                                      wraps=candidate._observe_effective_policy) as policy:
                        if label == "nonmatching-stage":
                            policy.return_value = lane._public_result(
                                "UNQUALIFIED", "SYNTHETIC_POLICY_MATCH_NOT_QUALIFICATION",
                                stage="POLICY_EVIDENCE_OBSERVED")
                        self.assert_unknown(candidate._reserve_fixture(fixture, self.intent, evidence))
                    policy.assert_called_once_with(self.intent, evidence)
                    self.assertIs(fixture._fresh, False)
                    for name in ("reservation.json", "pending.json"):
                        self.assertFalse((namespace / name).exists())
                    # Retry with real, matching policy cannot restore consumed genesis.
                    with patch.object(candidate, "_observe_effective_policy",
                                      wraps=candidate._observe_effective_policy) as retry_policy:
                        self.assert_unknown(self.reserve(fixture))
                    retry_policy.assert_not_called()
                    writer.assert_not_called()
                    inventory.assert_not_called()
                self.assertEqual(before, {p.name: p.read_bytes() for p in namespace.iterdir()})

    def test_managed_surface_variation_reaches_final_qualification_report_digest(self):
        intent, evidence = self.fake_policy(managed=True)
        for name in ("managed_local", "managed_remote"):
            with self.subTest(surface=name):
                surfaces = tuple(replace(s, sha256="7" * 64) if s.name == name else s
                                 for s in evidence.surfaces)
                changed = replace(evidence, surfaces=surfaces)
                payload = [asdict(s) for s in surfaces]
                digest = phase_b._digest(payload)
                self.assertNotEqual(digest, intent.policy_pins.qualification_report_sha256)
                self.assertEqual(surfaces[0], evidence.surfaces[0])  # Settings still match.
                self.assertEqual(phase_b._observe_policy(intent, changed.base)["stage"],
                                 "POLICY_EVIDENCE_OBSERVED")
                with patch.object(phase_b, "_observe_policy", wraps=phase_b._observe_policy) as base, patch.object(
                        phase_b, "_digest", wraps=phase_b._digest) as hashing:
                    result = candidate._observe_effective_policy(intent, changed)
                base.assert_called_once_with(intent, changed.base)
                # This list is hashed only after every individual surface check passes.
                hashing.assert_called_with(payload)
                self.assert_non_grant(result)
                self.assertEqual(result["reason"], "EFFECTIVE_POLICY_EVIDENCE_INVALID")
                self.assertEqual(result["stage"], "NOT_OBSERVED")

                # Positive control: the SAME surfaces pass when only their report
                # binding and its dependent synthetic intent/base digests are repinned.
                pins = replace(intent.policy_pins, qualification_report_sha256=digest)
                rebound = replace(intent, policy_pins=pins,
                                  policy_sha256=phase_b._policy_digest(SESSION, pins))
                matching = replace(changed, base=replace(
                    changed.base, pins=pins, policy_sha256=rebound.policy_sha256,
                    intent_sha256=phase_b._digest(asdict(rebound))))
                result = candidate._observe_effective_policy(rebound, matching)
                self.assert_non_grant(result)
                self.assertEqual(result["stage"], "SYNTHETIC_EFFECTIVE_POLICY_OBSERVED")

    def test_exclusive_creation_and_fsync_order_before_successful_report_and_no_effect_api(self):
        fixture = self.fixture()
        namespace = self.namespace(fixture)
        result, events = self.reserve_with_trace(fixture)
        self.assertEqual(result["stage"], "FIXTURE_PENDING_DURABLE")
        self.assertEqual(events, RESERVATION_EVENTS)
        self.assertEqual(stat.S_IMODE(fixture.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(namespace.stat().st_mode), 0o700)
        for path in namespace.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertLessEqual(len(path.read_bytes()), candidate._LIMIT)
            self.assertEqual(path.read_bytes(), lane._canonical_json(json.loads(path.read_bytes())))
        with self.assertRaises(TypeError):
            candidate._reserve_fixture(fixture, self.intent, self.evidence, effect=lambda: self.fail("effect"))

    def test_duplicate_pending_missing_result_and_new_uuid_never_replay(self):
        fixture = self.fixture()
        self.assertEqual(self.reserve(fixture)["stage"], "FIXTURE_PENDING_DURABLE")
        before = {p.name: p.read_bytes() for p in self.namespace(fixture).iterdir()}
        self.assert_unknown(candidate._observe_fixture(fixture, self.intent))
        self.assert_unknown(self.reserve(fixture))
        other, evidence = self.fake_policy(session=OTHER_SESSION)
        self.assertEqual(phase_b._pending_contract(other).namespace,
                         phase_b._pending_contract(self.intent).namespace)
        # A changed session is rejected by handle/intent equality, not inventory.
        with patch.object(candidate, "_expected", wraps=candidate._expected) as expected, patch.object(
                candidate, "_inventory", wraps=candidate._inventory) as inventory:
            self.assert_unknown(candidate._reserve_fixture(fixture, other, evidence))
        expected.assert_called_once_with(fixture, other)
        inventory.assert_not_called()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.namespace(fixture).iterdir()})

    def test_complete_pair_is_only_history_and_modified_intents_stop_before_inventory(self):
        fixture = self.fixture()
        self.reserve(fixture)
        self.write(fixture, "result.json", self.result_payload())
        observed = candidate._observe_fixture(fixture, self.intent)
        self.assert_non_grant(observed)
        self.assertEqual(observed["stage"], "RECEIPT_PAIR_OBSERVED")
        self.assertIsNone(observed["session_id"])
        self.assert_unknown(self.reserve(fixture))
        other_session, _ = self.fake_policy(session=OTHER_SESSION)
        pins = replace(self.intent.policy_pins, binary_sha256="f" * 64)
        changes = ({"remote": "other"}, {"branch_ref": "refs/heads/other"},
                   {"contract_id": "e" * 64}, {"issue_snapshot_sha256": "f" * 64},
                   {"git_common_dir": str(self.scratch / "clone.git")},
                   {"project_root": str(self.scratch / "clone")},
                   {"policy_pins": pins, "policy_sha256": phase_b._policy_digest(SESSION, pins)})
        before = {p.name: p.read_bytes() for p in self.namespace(fixture).iterdir()}
        for other in (other_session, *(replace(self.intent, **change) for change in changes)):
            with self.subTest(intent=other):
                pending = phase_b._pending_contract(other)
                self.assertIsNotNone(pending)
                self.assertEqual(pending.namespace, self.namespace(fixture).name)
                # These prove _expected's early equality rejection ONLY, not
                # cross-session or changed-Task inventory qualification.
                with patch.object(candidate, "_expected", wraps=candidate._expected) as expected, patch.object(
                        candidate, "_inventory", wraps=candidate._inventory) as inventory, patch.object(
                        candidate, "_checked_inventory", wraps=candidate._checked_inventory) as checked:
                    self.assert_unknown(candidate._observe_fixture(fixture, other))
                    self.assert_unknown(candidate._reserve_fixture(fixture, other, self.evidence))
                self.assertEqual(expected.call_args_list, [call(fixture, other), call(fixture, other)])
                inventory.assert_not_called()
                checked.assert_not_called()
                self.assertEqual(before, {p.name: p.read_bytes() for p in self.namespace(fixture).iterdir()})

    def test_complete_inventory_rejects_ambiguous_extra_or_nested_history(self):
        for name in ("pending-copy.json", "result-copy.json", "unknown", "nested"):
            with self.subTest(name=name):
                fixture = self.fixture()
                if name == "nested":
                    (self.namespace(fixture) / name).mkdir(mode=0o700)
                else:
                    self.write(fixture, name, {})
                self.assert_unknown(self.reserve(fixture))
                self.assertFalse((self.namespace(fixture) / "pending.json").exists())
        fixture = self.fixture()
        (fixture.root / "another-namespace").mkdir(mode=0o700)
        self.assert_unknown(self.reserve(fixture))

    def test_missing_manifest_result_only_and_initial_absence_are_not_admission(self):
        fixture = self.fixture()
        self.assert_unknown(candidate._observe_fixture(fixture, self.intent))
        self.write(fixture, "result.json", self.result_payload())
        self.assert_unknown(self.reserve(fixture))
        self.assert_unknown(candidate._observe_fixture(fixture, self.intent))
        missing = self.fixture()
        (self.namespace(missing) / "manifest.json").rename(self.scratch / "retained-manifest")
        self.assert_unknown(self.reserve(missing))
        for absent in (None, (), {}):
            self.assert_unknown(candidate._reserve_fixture(absent, self.intent, self.evidence))
        # No caller-stated inventory/completeness or existing-root opener.
        with self.assertRaises(TypeError):
            candidate._reserve_fixture(fixture, self.intent, self.evidence, pending=(), complete=True)

    def test_corrupt_partial_duplicate_json_noncanonical_and_oversized_history(self):
        for data in (b"{", b"{}", b'{"x":1,"x":1}', b'{ "x": 1 }',
                     b"[0]", b"NaN", b"x" * (candidate._LIMIT + 1), b"\xff"):
            with self.subTest(data=data[:40]):
                fixture = self.fixture()
                self.reserve(fixture)
                self.write(fixture, "pending.json", data)
                self.write(fixture, "result.json", self.result_payload())
                self.assert_unknown(candidate._observe_fixture(fixture, self.intent))
                self.assert_unknown(self.reserve(fixture))

    def test_duplicate_and_noncanonical_result_are_rejected_by_result_read(self):
        payload = self.result_payload()
        canonical = lane._canonical_json(payload)
        cases = (
            ("duplicate", canonical[:-1] + b',"status":"OBSERVED"}', lane.preflight._DuplicateJSONKey),
            ("noncanonical", json.dumps(payload, indent=2).encode("ascii"), candidate._Problem),
        )
        for label, data, error_type in cases:
            with self.subTest(case=label):
                self.assertEqual(json.loads(data), payload)  # Valid schema, not a receipt mismatch.
                self.assertNotEqual(data, canonical)
                fixture = self.fixture()
                self.assertEqual(self.reserve(fixture)["stage"], "FIXTURE_PENDING_DURABLE")
                self.write(fixture, "result.json", payload)
                self.assertEqual(candidate._observe_fixture(fixture, self.intent)["stage"],
                                 "RECEIPT_PAIR_OBSERVED")
                self.write(fixture, "result.json", data)
                namespace = self.namespace(fixture)
                before = {p.name: p.read_bytes() for p in namespace.iterdir()}
                self.assertEqual(set(before), set(candidate._FILES))
                errors = []
                real_read = candidate._read

                def reading(fd, name, identity):
                    self.assertEqual(name, "result.json")
                    try:
                        return real_read(fd, name, identity)
                    except (candidate._Problem, lane.preflight._DuplicateJSONKey) as error:
                        errors.append(type(error))
                        raise

                with patch.object(candidate, "_read", side_effect=reading) as reader, patch.object(
                        candidate, "_inventory", wraps=candidate._inventory) as inventory, patch.object(
                        candidate, "_checked_inventory", wraps=candidate._checked_inventory) as checked, patch.object(
                        lane.preflight, "_loads_strict", wraps=lane.preflight._loads_strict) as parser, patch.object(
                        phase_b, "_observe_receipts", wraps=phase_b._observe_receipts) as receipts:
                    self.assert_unknown(candidate._observe_fixture(fixture, self.intent))
                inventory.assert_called_once()
                reader.assert_called_once()
                parser.assert_called_once_with(data)
                self.assertEqual(errors, [error_type])
                checked.assert_not_called()
                receipts.assert_not_called()
                self.assert_unknown(self.reserve(fixture))
                self.assertEqual(before, {p.name: p.read_bytes() for p in namespace.iterdir()})

    def test_unknown_result_is_retained_and_never_replayed(self):
        for changes in ({"status": "UNKNOWN", "reason": "INTERRUPTED", "stage": "UNKNOWN"},
                        {"pending_sha256": "f" * 64}, {"schema_version": True},
                        {"reason": "secret-marker"}, {"extra": "secret-marker"}):
            with self.subTest(changes=changes):
                fixture = self.fixture()
                self.reserve(fixture)
                path = self.write(fixture, "result.json", self.result_payload(**changes))
                before = path.read_bytes()
                for _ in range(2):
                    self.assert_unknown(candidate._observe_fixture(fixture, self.intent))
                    self.assert_unknown(self.reserve(fixture))
                self.assertEqual(path.read_bytes(), before)

    def test_every_sync_failure_retains_unknown_without_retry_or_repair(self):
        for fail_at in range(1, 5):
            with self.subTest(fail_at=fail_at):
                fixture = self.fixture()
                result, events = self.reserve_with_trace(fixture, fail_at=fail_at)
                self.assert_unknown(result)
                failed_index = (1, 2, 4, 5)[fail_at - 1]
                self.assertEqual(events, RESERVATION_EVENTS[:failed_index] +
                                 (RESERVATION_EVENTS[failed_index] + ".failed",))
                self.assertIs(fixture._fresh, False)
                namespace = self.namespace(fixture)
                self.assertTrue((namespace / "reservation.json").exists())
                self.assertEqual((namespace / "pending.json").exists(), fail_at >= 3)
                before = {p.name: p.read_bytes() for p in namespace.iterdir()}
                with patch.object(candidate, "_exclusive", wraps=candidate._exclusive) as writer, patch.object(
                        os, "fsync", wraps=os.fsync) as syncing:
                    self.assert_unknown(self.reserve(fixture))
                    self.assert_unknown(candidate._observe_fixture(fixture, self.intent))
                writer.assert_not_called()
                syncing.assert_not_called()
                self.assertEqual(before, {p.name: p.read_bytes() for p in namespace.iterdir()})

    def test_short_write_zero_write_and_after_pending_failure(self):
        fixture = self.fixture()
        write = os.write
        with patch.object(os, "write", side_effect=lambda fd, data: write(fd, data[:7])):
            self.assertEqual(self.reserve(fixture)["stage"], "FIXTURE_PENDING_DURABLE")
        for failure in ("zero", "after-pending"):
            fixture = self.fixture()
            if failure == "zero":
                with patch.object(os, "write", return_value=0):
                    self.assert_unknown(self.reserve(fixture))
            else:
                checked = candidate._checked_inventory

                def fail_after(fd, expected):
                    if "pending.json" in expected:
                        raise OSError("secret-marker")
                    return checked(fd, expected)

                with patch.object(candidate, "_checked_inventory", side_effect=fail_after):
                    self.assert_unknown(self.reserve(fixture))
                self.assertTrue((self.namespace(fixture) / "pending.json").exists())
            self.assert_unknown(self.reserve(fixture))

    def test_deleted_pending_and_tombstone_do_not_reset_genesis(self):
        fixture = self.fixture()
        self.reserve(fixture)
        for name in ("pending.json", "reservation.json"):
            (self.namespace(fixture) / name).rename(self.scratch / ("retained-" + name))
        self.assert_unknown(self.reserve(fixture))
        self.assert_unknown(candidate._observe_fixture(fixture, self.intent))

    def test_symlinks_hardlinks_fifo_bad_modes_and_wrong_owner_fail_closed(self):
        for kind in ("symlink", "hardlink", "fifo", "file-mode", "namespace-mode", "root-mode"):
            with self.subTest(kind=kind):
                fixture = self.fixture()
                namespace = self.namespace(fixture)
                pending = namespace / "pending.json"
                target = self.scratch / ("target-" + kind)
                target.write_bytes(b"secret-marker")
                target.chmod(0o600)
                if kind == "symlink":
                    pending.symlink_to(target)
                elif kind == "hardlink":
                    os.link(target, pending)
                elif kind == "fifo":
                    os.mkfifo(pending, 0o600)
                elif kind == "file-mode":
                    (namespace / "manifest.json").chmod(0o640)
                else:
                    (namespace if kind == "namespace-mode" else fixture.root).chmod(0o750)
                self.assert_unknown(self.reserve(fixture))
                self.assertEqual(target.read_bytes(), b"secret-marker")
        fixture = self.fixture()
        with patch.object(os, "geteuid", return_value=os.geteuid() + 1):
            self.assert_unknown(self.reserve(fixture))
        alias = self.scratch / "root-alias"
        alias.symlink_to(fixture.root, target_is_directory=True)
        self.assert_unknown(candidate._reserve_fixture(replace(fixture, root=alias), self.intent, self.evidence))

    def test_competing_pending_at_exclusive_create_fails_closed_under_fixture_lock(self):
        for kind in ("file", "symlink"):
            with self.subTest(kind=kind):
                fixture = self.fixture()
                namespace = self.namespace(fixture)
                pending = namespace / "pending.json"
                # Outside this fixture, but still strictly inside the test scratch.
                target = self.scratch / ("race-target-" + kind)
                target.write_bytes(b"secret-marker")
                target_before = target.read_bytes(), target.stat()
                exclusive, real_open, real_write = candidate._exclusive, os.open, os.write
                attempts, conflicts, injected = [], [], []

                def competing(fd, name, data):
                    if name == "pending.json":
                        self.assertIs(fixture._fresh, False)
                        self.assertTrue((namespace / "reservation.json").is_file())
                        self.assertFalse(pending.exists())
                        self.assertEqual(candidate._dir_id(os.fstat(fd)), fixture.namespace_id)
                        probe = real_open(fixture.root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                        try:
                            # A distinct open description proves the real lock is held.
                            with self.assertRaises(BlockingIOError):
                                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        finally:
                            os.close(probe)
                        # Deterministic injection AFTER initial inventory, immediately
                        # before real _exclusive; no hostile concurrent worker/effect.
                        if kind == "symlink":
                            pending.symlink_to(target)
                        else:
                            self.write(fixture, "pending.json", b"competing-pending")
                        injected.append(pending.lstat().st_ino)
                    return exclusive(fd, name, data)

                def opening(path, flags, *args, **kwargs):
                    if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                        self.assertIn(path, ("reservation.json", "pending.json"))
                        for required in (os.O_EXCL, os.O_NOFOLLOW, os.O_CREAT, os.O_CLOEXEC):
                            self.assertTrue(flags & required)
                        self.assertEqual(candidate._dir_id(os.fstat(kwargs["dir_fd"])), fixture.namespace_id)
                        attempts.append(path)
                        try:
                            return real_open(path, flags, *args, **kwargs)
                        except FileExistsError:
                            conflicts.append(path)
                            raise
                    return real_open(path, flags, *args, **kwargs)

                def writing(fd, data):
                    info, reservation = os.fstat(fd), (namespace / "reservation.json").stat()
                    self.assertEqual((info.st_dev, info.st_ino), (reservation.st_dev, reservation.st_ino))
                    return real_write(fd, data)

                with patch.object(candidate, "_exclusive", side_effect=competing) as writer, patch.object(
                        os, "open", side_effect=opening), patch.object(os, "write", side_effect=writing) as writes:
                    self.assert_unknown(self.reserve(fixture))
                    self.assertEqual(injected, [pending.lstat().st_ino])
                    self.assertEqual(pending.is_symlink(), kind == "symlink")
                    if kind == "symlink":
                        self.assertEqual(pending.readlink(), target)
                    else:
                        self.assertEqual(pending.read_bytes(), b"competing-pending")
                    before = {p.name: p.read_bytes() for p in namespace.iterdir()}
                    self.assertEqual(set(before), {"manifest.json", "reservation.json", "pending.json"})
                    self.assert_unknown(self.reserve(fixture))
                    self.assert_unknown(candidate._observe_fixture(fixture, self.intent))
                    self.assertEqual(before, {p.name: p.read_bytes() for p in namespace.iterdir()})
                self.assertEqual([c.args[1] for c in writer.call_args_list], ["reservation.json", "pending.json"])
                self.assertEqual(attempts, ["reservation.json", "pending.json"])
                self.assertEqual(conflicts, ["pending.json"])
                writes.assert_called()  # Every actual module write was to reservation, never the target.
                self.assertEqual(target.read_bytes(), target_before[0])
                self.assertEqual(target.stat().st_ino, target_before[1].st_ino)
                self.assertEqual(target.stat().st_mtime_ns, target_before[1].st_mtime_ns)
                self.assertIs(fixture._fresh, False)

    def test_root_namespace_replacement_and_incomplete_initialization_are_not_repaired(self):
        for which in ("root", "namespace"):
            fixture = self.fixture()
            target = fixture.root if which == "root" else self.namespace(fixture)
            target.rename(self.scratch / ("retained-" + which))
            target.mkdir(mode=0o700)
            self.assert_unknown(self.reserve(fixture))
            self.assertEqual(list(target.iterdir()), [])
        with patch.object(os, "fsync", side_effect=OSError("secret-marker")):
            fixture, result = candidate._new_disposable_fixture(self.scratch, self.intent)
        self.assertIsNone(fixture)
        self.assert_unknown(result)

    def test_real_git_root_rejection_is_not_bypassed_in_module(self):
        self.assertFalse(self.real_outside_git(self.scratch))
        with patch.object(candidate, "_outside_git", self.real_outside_git):
            fixture, result = candidate._new_disposable_fixture(self.scratch, self.intent)
        self.assertIsNone(fixture)
        self.assert_unknown(result)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_concurrent_reservations_have_one_durable_pending_and_one_unknown(self):
        fixture = self.fixture()
        entered, release = threading.Event(), threading.Event()
        results, failures = [], []
        exclusive = candidate._exclusive

        def paused(fd, name, data):
            if name == "reservation.json":
                entered.set()
                if not release.wait(5):
                    raise AssertionError("fixture writer timeout")
            return exclusive(fd, name, data)

        def first():
            try:
                results.append(candidate._reserve_fixture(fixture, self.intent, self.evidence))
            except BaseException as error:
                failures.append(error)

        worker = threading.Thread(target=first)
        with patch.object(candidate, "_exclusive", side_effect=paused):
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assert_unknown(self.reserve(fixture))
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 1)
        self.assert_non_grant(results[0])
        self.assertEqual(results[0]["stage"], "FIXTURE_PENDING_DURABLE")
        self.assert_unknown(self.reserve(fixture))

    def test_public_dispatch_stays_inert_even_with_fake_tools_and_intent(self):
        effect = Mock(side_effect=AssertionError("tool execution"))
        with patch.object(candidate, "_reserve_fixture", side_effect=AssertionError("storage")) as writer:
            result = lane.dispatch(self.intent, authorize=effect, fixture_transport=effect, tool=effect)
        self.assert_non_grant(result)
        self.assertEqual(result["reason"], "CANDIDATE_ONLY")
        effect.assert_not_called()
        writer.assert_not_called()


class PhaseCSourceBoundaryTests(unittest.TestCase):
    def test_outside_git_each_marker_is_detected_without_real_repository_ancestors(self):
        path = Path("/synthetic-external/fixture/nested")
        probes = [parent / name for parent in (path, *path.parents)
                  for name in (".git", "HEAD", "objects", "refs", "commondir")]
        for index, target in enumerate(probes):
            with self.subTest(marker=target):
                def lstat(probe):
                    if probe == target:
                        return object()  # Only marker existence is observed.
                    raise FileNotFoundError(probe)

                with patch.object(Path, "lstat", autospec=True, side_effect=lstat) as observed:
                    self.assertFalse(candidate._outside_git(path))
                self.assertEqual(observed.call_args_list, [call(p) for p in probes[:index + 1]])
        # Pure mocked heuristic control: actual outside-Git storage remains unqualified.
        with patch.object(Path, "lstat", autospec=True, side_effect=FileNotFoundError) as observed:
            self.assertTrue(candidate._outside_git(path))
        self.assertEqual(observed.call_args_list, [call(p) for p in probes])

    def test_private_bounded_nonlaunching_source_and_no_integration(self):
        source = Path(candidate.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                imports.extend([node.module or "", *(alias.name for alias in node.names)])
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                self.assertTrue(node.name.startswith("_"), node.name)
        self.assertFalse(any(any(part in name for part in (
            "transport", "subprocess", "socket", "argparse", "main_service")) for name in imports))
        calls = {node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(calls & {"dispatch", "_exercise_fixture", "_run_bounded", "run_preflight",
                                 "_observe_main_intent", "_task_identity", "Popen", "system", "execv",
                                 "spawn", "unlink", "remove", "rmdir", "rename", "replace", "chmod"})
        self.assertFalse(any(isinstance(node, ast.If) and "__name__" in ast.unparse(node.test)
                             for node in tree.body))
        self.assertIn('"CLAUDE_EXECUTOR_UNQUALIFIED"', Path(mainmod.__file__).read_text())
        self.assertNotIn("r6b_phase_c", Path(mainmod.__file__).read_text())
        self.assertNotIn("r6b_phase_c", Path(lane.__file__).read_text())
