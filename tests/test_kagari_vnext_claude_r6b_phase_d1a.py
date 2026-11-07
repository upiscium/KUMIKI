"""Pure invented D1A witnesses; no receipt files, real registry or live authority."""
import ast
from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import _kagari_vnext_claude_r6b_phase_d1a as d


def digest(value):
    return hashlib.sha256(json.dumps([d.SYNTHETIC, value], sort_keys=True,
                                    separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


BINDING = d.SyntheticBinding("1" * 64, 340, "2" * 64, 2)
PINS = d.SyntheticAttemptPins(digest("00000000-0000-4000-8000-000000000001"),
                              "4" * 64, "5" * 64, "6" * 64, "7" * 64)
GOOD = d.SyntheticAttempt(PINS.attempt_id, PINS.manifest_sha256,
    d.SyntheticRecord(1, PINS.reservation_sha256, PINS.manifest_sha256),
    d.SyntheticRecord(2, PINS.pending_sha256, PINS.reservation_sha256),
    d.SyntheticRecord(3, PINS.result_sha256, PINS.pending_sha256), True)
OTHER = "f" * 64


def fixture(attempts=(GOOD,), binding=BINDING, sequence=3):
    # Rebind EVERY digest for history tests, so later rejection branches are reached.
    proposal = d.SyntheticCheckpointProposal(binding, sequence,
        digest(None if attempts is None else [asdict(a) for a in attempts]))
    return (d.SyntheticRegistrySnapshot((binding,), (proposal,), attempts),
            d.SyntheticCheckpointReference(binding, sequence, digest(asdict(proposal))))


class PhaseD1AContractsTests(unittest.TestCase):
    def check(self, result, reason, stage="NOT_OBSERVED"):
        self.assertEqual(result, {
            "status": "UNKNOWN" if stage == "NOT_OBSERVED" else "UNQUALIFIED",
            "qualification": "UNQUALIFIED", "dispatch_allowed": False,
            "task_completed": False, "replay_allowed": False,
            "authenticated_absence": False, "effective_policy": "UNVERIFIED",
            "ask_capability": "UNQUALIFIED", "evidence": d.SYNTHETIC,
            "reason": reason, "stage": stage})
        for key in ("dispatch_allowed", "task_completed", "replay_allowed", "authenticated_absence"):
            self.assertIs(result[key], False)

    def history(self, attempt, reason, **kwargs):
        snapshot, reference = fixture((attempt,), **kwargs)
        self.check(d.observe_routing(BINDING, snapshot, reference), "observed_only", "ROUTING_OBSERVED_ONLY")
        self.check(d.reconcile(BINDING, snapshot, reference, PINS), reason)

    def test_closed_history_is_only_deterministic_observation(self):
        s, ref = fixture()
        before = asdict(s)
        for _ in range(2):
            self.check(d.observe_routing(BINDING, s, ref), "observed_only", "ROUTING_OBSERVED_ONLY")
            self.check(d.reconcile(BINDING, s, ref, PINS), "observed_only", "CLOSED_HISTORY_OBSERVED_ONLY")
        self.assertEqual(asdict(s), before)
        with self.assertRaises(FrozenInstanceError):
            s.inventory = ()

    def test_absent_registry_or_lost_root_never_initializes(self):
        s, ref = fixture()
        self.check(d.observe_routing(BINDING, None, ref), "registry_missing")
        self.check(d.observe_routing(BINDING, replace(s, roots=()), ref), "root_missing")

    def test_repository_issue_and_alias_changes(self):
        for changes, reason in (({"repository_id": OTHER}, "repository_issue_mismatch"),
                                ({"issue": 341}, "repository_issue_mismatch"),
                                ({"repository_id": "owner/repo"}, "root_invalid"),
                                ({"issue": "0340"}, "root_invalid")):
            s, ref = fixture(binding=replace(BINDING, **changes))
            self.check(d.observe_routing(BINDING, s, ref), reason)

    def test_fixed_root_and_epoch_in_all_witnesses(self):
        for change, reason in (({"root_id": OTHER}, "root_mismatch"),
                               ({"epoch": 1}, "epoch_rollback"), ({"epoch": 3}, "epoch_mismatch")):
            changed = replace(BINDING, **change)
            s, ref = fixture(binding=changed)
            self.check(d.observe_routing(BINDING, s, ref), reason)
            s, ref = fixture()
            self.check(d.observe_routing(BINDING, s, replace(ref, binding=changed)), reason)
            s = replace(s, checkpoints=(replace(s.checkpoints[0], binding=changed),))
            self.check(d.observe_routing(BINDING, s, ref), reason)

    def test_multiple_roots_and_duplicate_checkpoints(self):
        s, ref = fixture()
        for roots in ((BINDING, BINDING), (BINDING, replace(BINDING, root_id=OTHER))):
            self.check(d.observe_routing(BINDING, replace(s, roots=roots), ref), "multiple_roots")
        self.check(d.observe_routing(BINDING, replace(s, checkpoints=s.checkpoints * 2), ref), "duplicate_checkpoint")

    def test_deleted_and_rolled_back_checkpoint(self):
        s, ref = fixture()
        self.check(d.reconcile(BINDING, replace(s, checkpoints=()), ref, PINS), "checkpoint_deleted_or_missing")
        old, _ = fixture(sequence=2)
        self.check(d.reconcile(BINDING, old, ref, PINS), "checkpoint_rolled_back")

    def test_checkpoint_fork_and_unanchored_sequence_gap(self):
        s, ref = fixture()
        fork = replace(s, checkpoints=(replace(s.checkpoints[0], inventory_sha256=OTHER),))
        self.check(d.observe_routing(BINDING, fork, ref), "checkpoint_fork")
        for seq in (4, 6):
            future, _ = fixture(sequence=seq)
            self.check(d.observe_routing(BINDING, future, ref), "checkpoint_gap")

    def test_corrupted_inventory_cannot_hide_behind_matching_checkpoint(self):
        s, ref = fixture()
        s = replace(s, inventory=(replace(GOOD, acknowledged=False),))
        self.check(d.reconcile(BINDING, s, ref, PINS), "inventory_corrupt")

    def test_external_witness_forgery_and_self_authored_reference(self):
        s, ref = fixture()
        for bad in (asdict(ref), s.checkpoints[0], replace(ref, source="AUTHENTICATED_HOST")):
            self.check(d.observe_routing(BINDING, s, bad), "checkpoint_reference_invalid")
        self.check(d.observe_routing(BINDING, s, None), "checkpoint_reference_missing")
        self.check(d.observe_routing(BINDING, replace(s, source="HOST"), ref), "registry_invalid")
        bad = replace(s, checkpoints=(replace(s.checkpoints[0], source="HOST"),))
        self.check(d.observe_routing(BINDING, bad, ref), "checkpoint_invalid")
        self.check(d.reconcile(BINDING, s, replace(ref), PINS), "observed_only", "CLOSED_HISTORY_OBSERVED_ONLY")

    def test_zero_invalid_and_boolean_sequences(self):
        s, ref = fixture()
        for seq in (0, -1, True, 3.0, "3", d.MAX_SEQUENCE + 1):
            bad = replace(s, checkpoints=(replace(s.checkpoints[0], sequence=seq),))
            self.check(d.observe_routing(BINDING, bad, ref), "checkpoint_invalid")
            self.check(d.observe_routing(BINDING, s, replace(ref, sequence=seq)), "checkpoint_reference_invalid")
            bad = replace(s, inventory=(replace(GOOD, pending=replace(GOOD.pending, sequence=seq)),))
            self.check(d.reconcile(BINDING, bad, ref, PINS), "inventory_invalid")

    def test_empty_missing_and_manifest_only_inventory_prove_no_absence(self):
        for attempts, reason in (((), "never_seen_not_proven"), (None, "inventory_missing")):
            s, ref = fixture(attempts)
            self.check(d.observe_routing(BINDING, s, ref), reason)
            self.check(d.reconcile(BINDING, s, ref, PINS), reason)
        self.history(replace(GOOD, reservation=None, pending=None, result=None, acknowledged=False), "never_seen_not_proven")

    def test_pending_before_result_and_sequence_gap_checks(self):
        self.history(replace(GOOD, pending=replace(GOOD.pending, sequence=3),
                             result=replace(GOOD.result, sequence=2)), "record_order_invalid")
        self.history(replace(GOOD, result=replace(GOOD.result, sequence=4)), "record_gap", sequence=4)
        self.history(replace(GOOD, result=replace(GOOD.result, sequence=4)), "record_after_checkpoint")
        self.history(GOOD, "record_gap", sequence=4)
        shifted = replace(GOOD, **{name: replace(getattr(GOOD, name), sequence=i + 2)
                                  for i, name in enumerate(("reservation", "pending", "result"))})
        self.history(shifted, "record_gap", sequence=4)

    def test_pending_only_with_or_without_reservation(self):
        for reservation in (GOOD.reservation, None):
            self.history(replace(GOOD, reservation=reservation, result=None, acknowledged=False), "pending_only")

    def test_reservation_only(self):
        self.history(replace(GOOD, pending=None, result=None, acknowledged=False), "reservation_only")

    def test_result_only_and_missing_reservation(self):
        self.history(replace(GOOD, reservation=None, pending=None), "result_only")
        self.history(replace(GOOD, reservation=None), "reservation_missing")

    def test_ack_lost_and_impossible_acknowledgement(self):
        self.history(replace(GOOD, acknowledged=False), "ack_lost")
        self.history(replace(GOOD, result=None), "acknowledgement_without_result")

    def test_mismatched_result_digest_or_pending_link(self):
        for change in ({"sha256": OTHER}, {"predecessor_sha256": OTHER}):
            self.history(replace(GOOD, result=replace(GOOD.result, **change)), "result_mismatch")

    def test_exact_manifest_reservation_and_pending_indicators(self):
        self.history(replace(GOOD, manifest_sha256=None), "manifest_missing")
        self.history(replace(GOOD, manifest_sha256=OTHER), "manifest_mismatch")
        for name in ("reservation", "pending"):
            for field in ("sha256", "predecessor_sha256"):
                self.history(replace(GOOD, **{name: replace(getattr(GOOD, name), **{field: OTHER})}), name + "_mismatch")

    def test_duplicate_and_extra_attempts_are_not_latest_wins(self):
        for second, reason in ((GOOD, "duplicate_attempts"), (replace(GOOD, attempt_id=OTHER), "extra_attempt")):
            s, ref = fixture((GOOD, second))
            self.check(d.reconcile(BINDING, s, ref, PINS), reason)
        s, ref = fixture((GOOD,) * d.MAX_ITEMS)
        self.check(d.observe_routing(BINDING, s, ref), "duplicate_attempts")

    def test_second_uuid_cannot_clear_uncertainty_or_obtain_replay(self):
        s, ref = fixture((replace(GOOD, result=None, acknowledged=False),))
        for _ in range(2):
            self.check(d.reconcile(BINDING, s, ref, PINS), "pending_only")
            other = replace(PINS, attempt_id=digest("00000000-0000-4000-8000-000000000002"))
            self.check(d.reconcile(BINDING, s, ref, other), "attempt_mismatch")

    def test_arbitrary_inputs_exact_types_bounds_and_canonical_hex(self):
        s, ref = fixture()
        for bad in (None, {}, [], True, "../private", object()):
            self.check(d.observe_routing(bad, s, ref), "binding_invalid")
            self.check(d.observe_routing(BINDING, bad, ref), "registry_missing" if bad is None else "registry_invalid")
            self.check(d.observe_routing(BINDING, s, bad), "checkpoint_reference_missing" if bad is None else "checkpoint_reference_invalid")
            self.check(d.reconcile(BINDING, s, ref, bad), "attempt_pins_invalid")
        for field in ("repository_id", "root_id", "issue", "epoch"):
            for bad in (True, 0, -1, 1.0, "../private", "F" * 64, "a" * 63, "a" * 65, "a" * 63 + "\n"):
                self.check(d.observe_routing(replace(BINDING, **{field: bad}), s, ref), "binding_invalid")
        for field, reason in (("roots", "roots_invalid"), ("checkpoints", "checkpoints_invalid"), ("inventory", "inventory_invalid")):
            for bad in ([], {}, (GOOD,) * (d.MAX_ITEMS + 1)):
                self.check(d.observe_routing(BINDING, replace(s, **{field: bad}), ref), reason)
        for bad in (replace(GOOD, acknowledged=1), replace(GOOD, result={}), replace(GOOD, attempt_id=OTHER.upper())):
            self.check(d.observe_routing(BINDING, replace(s, inventory=(bad,)), ref), "inventory_invalid")
        for bad in (True, None, "F" * 64, "a" * 63 + "\n", "a" * 65):
            self.check(d.observe_routing(BINDING, s, replace(ref, checkpoint_sha256=bad)), "checkpoint_reference_invalid")
            self.check(d.reconcile(BINDING, s, ref, replace(PINS, pending_sha256=bad)), "attempt_pins_invalid")
        class Forged(d.SyntheticCheckpointReference):
            pass
        self.check(d.observe_routing(BINDING, s, Forged(BINDING, 3, OTHER)), "checkpoint_reference_invalid")
        self.check(d.observe_routing(BINDING, object.__new__(d.SyntheticRegistrySnapshot), ref), "malformed_input")

    def test_no_process_network_filesystem_or_environment_access(self):
        s, ref = fixture()
        for name in ("subprocess.Popen", "subprocess.run", "os.system", "os.posix_spawn", "os.fork",
                     "socket.socket", "socket.create_connection", "os.getenv", "os._Environ.__getitem__",
                     "os.open", "builtins.open", "io.open", "pathlib.Path.stat"):
            guard = self.enterContext(patch(name, side_effect=AssertionError(name)))
            self.addCleanup(guard.assert_not_called)
        self.check(d.observe_routing(BINDING, s, ref), "observed_only", "ROUTING_OBSERVED_ONLY")
        self.check(d.reconcile(BINDING, s, ref, PINS), "observed_only", "CLOSED_HISTORY_OBSERVED_ONLY")
        self.history(replace(GOOD, result=None, acknowledged=False), "pending_only")

    def test_source_import_boundary_and_main_gate_remain_intact(self):
        tree = ast.parse(Path(d.__file__).read_text(encoding="utf-8"))
        imports = {n.module if isinstance(n, ast.ImportFrom) else a.name
                   for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        self.assertEqual(imports, {"dataclasses", "hashlib", "json"})
        root = Path(__file__).parent.parent
        main = (root / "tools/kagari_vnext_main.py").read_text(encoding="utf-8")
        self.assertIn('raise MainError("CLAUDE_EXECUTOR_UNQUALIFIED")', main)
        for source in (main, (root / "tools/kagari_vnext_claude_lane_b.py").read_text(encoding="utf-8")):
            self.assertNotIn("r6b_phase_d1a", source)
