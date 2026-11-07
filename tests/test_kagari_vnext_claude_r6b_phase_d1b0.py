"""Invented immutable tuples only; source reads stay inside the checkout."""
import ast
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import _kagari_vnext_claude_r6b_phase_d1b0 as d

B = d.SyntheticBinding("1" * 64, 340, "2" * 64, 2)
T = d.SyntheticTombstone("3" * 64, "4" * 64, "5" * 64, 7, "6" * 64, "7" * 64)
G = d.SyntheticHead(B, 0, 1, "8" * 64, (), ())
H = replace(G, sequence=1, generation=2, digest="9" * 64, tombstones=(T,))
R = d.SyntheticRegistry((B,))
OTHER, RESULT = "a" * 64, "b" * 64
LIMIT = 2**63 - 1
EVENTS = ("RESERVATION_WRITE", "RESERVATION_FSYNC", "PENDING_WRITE", "PENDING_FSYNC",
          "EFFECT_BOUNDARY", "RESULT_WRITE", "RESULT_FSYNC", "ACKNOWLEDGED")


def anchor(head=H):
    return d.SyntheticAnchor((head,), head)


def successor(head=H, **changes):
    values = dict(sequence=head.sequence + 1, generation=head.generation + 1, digest=OTHER)
    values.update(changes)
    return replace(head, **values)


def window(count):
    size = (0, 1, 1, 2, 2, 2, 3, 3, 3)[count]
    inventory = (T.reservation_sha256, T.pending_sha256, RESULT)[:size]
    head = replace(H, sequence=1 + size, generation=2 + size, inventory=inventory)
    return head, d.SyntheticRestart(T, inventory, EVENTS[:count], RESULT if count >= 6 else None)


class PhaseD1B0Tests(unittest.TestCase):
    def check(self, result, reason, stage="NOT_OBSERVED"):
        self.assertEqual(result, {
            "status": "UNKNOWN" if stage == "NOT_OBSERVED" else "UNQUALIFIED",
            "qualification": "UNQUALIFIED", "dispatch_allowed": False,
            "replay_allowed": False, "task_completed": False,
            "authenticated_absence": False, "commit_allowed": False,
            "effective_policy": "UNVERIFIED", "evidence": "R6B_D1B0_SYNTHETIC_TEST_ONLY_UNTRUSTED",
            "reason": reason, "stage": stage})
        for key in ("dispatch_allowed", "replay_allowed", "task_completed", "authenticated_absence", "commit_allowed"):
            self.assertIs(result[key], False)

    def observe(self, head=H, **changes):
        args = dict(binding=B, registry=R, anchor=anchor(head), expected=head, inventory=head.inventory)
        args.update(changes)
        return d.evaluate_binding_and_anchor(**args)

    def cas(self, proposed, head=H, **changes):
        args = dict(binding=B, registry=R, anchor=anchor(head), expected_old=head, proposed=proposed)
        args.update(changes)
        return d.preview_anchor_cas(**args)

    def restart(self, head, observation, **changes):
        args = dict(binding=B, registry=R, anchor=anchor(head), expected=head, observation=observation)
        args.update(changes)
        return d.classify_restart(**args)

    def test_01_positive_shapes_are_frozen_observations_not_trust(self):
        before = asdict(anchor())
        for _ in range(2):
            self.check(self.observe(), "observed_only", "BINDING_ANCHOR_OBSERVED_ONLY")
        self.assertEqual(asdict(anchor()), before)
        for value, field in ((B, "epoch"), (T, "authority_epoch"), (H, "sequence"), (R, "bindings"),
                             (anchor(), "heads"), (window(4)[1], "events")):
            with self.assertRaises(FrozenInstanceError):
                setattr(value, field, None)

    def test_02_missing_provision_or_genesis_never_initializes(self):
        for changes, reason in (({"registry": None}, "registry_missing"),
                ({"registry": replace(R, bindings=())}, "root_missing"), ({"anchor": None}, "anchor_missing"),
                ({"anchor": d.SyntheticAnchor((), None)}, "anchor_missing"), ({"expected": None}, "expected_head_missing")):
            for _ in range(2):
                self.check(self.observe(**changes), reason)
        self.check(self.restart(*window(0), anchor=None), "anchor_missing")
        self.check(self.cas(H, G, anchor=None), "anchor_missing")

    def test_03_root_loss_move_and_same_identity_recreation(self):
        for state, reason in (("LOST", "root_lost"), ("MOVED", "root_moved"), ("RECREATED", "root_recreated")):
            self.check(self.observe(registry=replace(R, root_state=state)), reason)

    def test_04_key_root_and_epoch_pins_bind_every_domain_and_cas(self):
        for changes, reason in (({"repository_id": OTHER}, "repository_issue_mismatch"),
                ({"issue": 341}, "repository_issue_mismatch"), ({"root_id": OTHER}, "root_identity_mismatch"),
                ({"epoch": 1}, "epoch_mismatch"), ({"epoch": 3}, "epoch_mismatch")):
            b = replace(B, **changes)
            for kwargs in ({"registry": d.SyntheticRegistry((b,))}, {"anchor": anchor(replace(H, binding=b))},
                           {"expected": replace(H, binding=b)}):
                self.check(self.observe(**kwargs), reason)
            self.check(self.cas(successor(binding=b)), reason)

    def test_05_alias_migration_never_starts_history(self):
        self.check(self.observe(registry=replace(R, root_state="ALIAS_MIGRATION")), "alias_migration_unqualified")
        for b in (replace(B, repository_id="owner/repository"), replace(B, issue="0340")):
            self.check(self.observe(registry=d.SyntheticRegistry((b,))), "registry_invalid")

    def test_06_competing_root_or_anchor_is_not_latest_wins(self):
        for roots in ((B, B), (B, replace(B, root_id=OTHER))):
            self.check(self.observe(registry=replace(R, bindings=roots)), "competing_roots")
        for heads in ((H, H), (H, replace(H, digest=OTHER))):
            self.check(self.observe(anchor=d.SyntheticAnchor(heads, H)), "competing_anchor_heads")

    def test_07_stale_ahead_and_fork_branches(self):
        for newer in (
            replace(H, sequence=2, generation=3, inventory=(T.reservation_sha256,)),
            window(4)[0],
        ):
            self.check(self.observe(expected=newer), "anchor_stale")
            self.check(self.observe(newer, expected=H), "anchor_ahead")
        self.check(self.observe(expected=replace(H, digest=OTHER)), "anchor_fork")

    def test_08_anchor_store_ack_loss_and_divergence_both_directions(self):
        self.check(self.observe(anchor=replace(anchor(), acknowledged_head=None)), "anchor_ack_missing")
        for stored, ack in ((H, G), (G, H), (H, replace(H, digest=OTHER))):
            self.check(self.observe(anchor=d.SyntheticAnchor((stored,), ack)), "anchor_store_ack_divergence")

    def test_09_receipt_inventory_differs_missing_or_malformed(self):
        for value, reason in ((None, "receipt_inventory_missing"), ([], "receipt_inventory_invalid"),
                             ((OTHER,), "receipt_inventory_differs"), ((OTHER,) * 4, "receipt_inventory_invalid")):
            self.check(self.observe(inventory=value), reason)
        malformed = replace(H, sequence=2, generation=3, inventory=(OTHER,))
        self.check(self.observe(malformed), "anchor_inventory_mismatch")

    def test_10_duplicate_or_second_attempt_cannot_be_hidden(self):
        for second, reason in ((T, "duplicate_attempts"), (replace(T, attempt_id=OTHER), "outstanding_attempt_conflict")):
            self.check(self.observe(replace(H, tombstones=(T, second))), reason)

    def test_11_cas_claim_and_each_append_are_only_previews(self):
        self.check(self.cas(H, G), "observed_only", "CAS_PREVIEW_OBSERVED_ONLY")
        for size in range(3):
            pins = (T.reservation_sha256, T.pending_sha256, RESULT)
            old = replace(H, sequence=size + 1, generation=size + 2, inventory=pins[:size])
            before = asdict(old)
            for _ in range(2):
                self.check(self.cas(successor(old, inventory=pins[:size + 1]), old), "observed_only", "CAS_PREVIEW_OBSERVED_ONLY")
            self.assertEqual(asdict(old), before)
            self.check(self.observe(old), "observed_only", "BINDING_ANCHOR_OBSERVED_ONLY")

    def test_12_cas_requires_exact_observed_old_head(self):
        for changes in ({"sequence": 2}, {"generation": 3}, {"digest": OTHER}, {"inventory": (OTHER,)}):
            self.check(self.cas(successor(), expected_old=replace(H, **changes)), "cas_expected_stale")
        fork = replace(H, digest=OTHER)
        self.check(self.cas(successor(), anchor=anchor(fork)), "cas_expected_stale")

    def test_13_counters_are_single_step_and_never_wrap(self):
        for changes in ({"sequence": 1}, {"sequence": 3}, {"generation": 2}, {"generation": 4}):
            self.check(self.cas(successor(**changes)), "cas_counter_not_successor")
        for field in ("sequence", "generation"):
            # A single-attempt head cannot legitimately reach MAX_SEQUENCE.
            old = replace(H, **{field: LIMIT})
            self.check(self.cas(successor(**{field: LIMIT}), old),
                       "head_counter_inventory_mismatch")
            self.check(self.cas(successor(**{field: LIMIT + 1})), "proposed_head_invalid")

    def test_14_tombstone_is_exact_and_retained_even_after_result(self):
        for ts in ((), (replace(T, attempt_id=OTHER),), (replace(T, policy_sha256=OTHER),), (T, T)):
            for old in (H, window(8)[0]):
                self.check(self.cas(successor(old, tombstones=ts), old), "outstanding_attempt_blocks_retry")

    def test_15_cas_cannot_rewrite_skip_or_reset_inventory(self):
        old = window(4)[0]
        for inventory in ((), old.inventory, (T.reservation_sha256, T.pending_sha256, RESULT)):
            base = H if len(inventory) == 3 else old
            self.check(self.cas(successor(base, inventory=inventory), base),
                       "head_counter_inventory_mismatch")
        self.check(self.cas(successor(old, inventory=(T.reservation_sha256, OTHER, RESULT)), old),
                   "anchor_inventory_mismatch")
        self.check(self.cas(successor(inventory=(OTHER,))), "anchor_inventory_mismatch")
        self.check(self.cas(replace(H, inventory=(T.reservation_sha256,)), G),
                   "head_counter_inventory_mismatch")
        self.check(self.cas(successor(digest=H.digest)), "cas_digest_unchanged")

    def test_16_every_crash_window_has_a_fixed_non_grant(self):
        reasons = ("before_reservation_write", "reservation_unsynced", "reservation_sync_claimed",
                   "pending_unsynced", "pending_before_effect", "effect_unknown", "result_unsynced",
                   "result_ack_unknown", "observed_only")
        for count, reason in enumerate(reasons):
            self.check(self.restart(*window(count)), reason, "CLOSED_SHAPE_OBSERVED_ONLY" if count == 8 else "NOT_OBSERVED")

    def test_17_empty_snapshot_is_not_authenticated_absence(self):
        observation = d.SyntheticRestart(T, (), ())
        for _ in range(2):
            self.check(self.restart(G, observation), "never_seen_not_proven")
        self.check(self.restart(G, replace(observation, events=("RESERVATION_WRITE",))), "tombstone_missing")
        self.check(self.observe(replace(G, inventory=(OTHER,))), "tombstone_missing")

    def test_18_restart_requires_the_exact_pending_tombstone(self):
        h, o = window(4)
        for field in ("attempt_id", "intent_sha256", "policy_sha256", "reservation_sha256", "pending_sha256", "authority_epoch"):
            changed = replace(T, **{field: 8 if field == "authority_epoch" else OTHER})
            self.check(self.restart(h, replace(o, tombstone=changed)), "outstanding_attempt_blocks_retry")

    def test_19_effect_cannot_precede_pending_write_and_sync(self):
        h, o = window(5)
        for events in (("EFFECT_BOUNDARY",), EVENTS[:3] + ("EFFECT_BOUNDARY",), EVENTS[:4] * 2, ("UNKNOWN",)):
            self.check(self.restart(h, replace(o, events=events)), "restart_order_invalid")

    def test_20_restart_inventory_and_result_rejection_discrimination(self):
        h, o = window(7)
        self.check(self.restart(h, replace(o, inventory=())), "receipt_inventory_differs")
        self.check(self.restart(h, replace(o, result_sha256=None)), "result_presence_mismatch")
        self.check(self.restart(h, replace(o, result_sha256=OTHER)), "restart_inventory_mismatch")
        h, o = window(4)
        self.check(self.restart(h, replace(o, result_sha256=RESULT)), "result_presence_mismatch")
        self.check(self.restart(h, replace(o, events=())), "restart_inventory_mismatch")

    def test_21_unknown_effect_and_lost_result_ack_never_retry(self):
        for count, reason in ((5, "effect_unknown"), (7, "result_ack_unknown")):
            h, o = window(count)
            before = (asdict(h), asdict(o))
            for _ in range(2):
                self.check(self.restart(h, o), reason)
                self.check(self.restart(h, replace(o, tombstone=replace(T, attempt_id=OTHER))), "outstanding_attempt_blocks_retry")
            self.assertEqual((asdict(h), asdict(o)), before)

    def test_22_arbitrary_types_subclasses_and_incomplete_shapes(self):
        for bad in (True, {}, [], "../not-a-root", object()):
            for field, reason in (("binding", "binding_invalid"), ("registry", "registry_invalid"),
                                  ("anchor", "anchor_invalid"), ("expected", "expected_head_invalid")):
                self.check(self.observe(**{field: bad}), reason)
            self.check(self.cas(bad), "proposed_head_invalid")
            self.check(self.restart(H, bad), "restart_invalid")
        class Forged(d.SyntheticAnchor):
            pass
        self.check(self.observe(anchor=Forged((H,), H)), "anchor_invalid")
        self.check(self.observe(anchor=object.__new__(d.SyntheticAnchor)), "malformed_input")
        self.check(self.cas(object.__new__(d.SyntheticHead)), "malformed_input")
        self.check(self.restart(H, object.__new__(d.SyntheticRestart)), "malformed_input")

    def test_23_exact_numbers_digests_and_bounded_tuples(self):
        for bad in (True, -1, 1.0, "1", LIMIT + 1):
            for field in ("sequence", "generation"):
                self.check(self.observe(replace(H, **{field: bad})), "anchor_invalid")
        for bad in (None, True, "F" * 64, "a" * 63, "a" * 65, "a" * 63 + "\n"):
            self.check(self.observe(replace(H, digest=bad)), "anchor_invalid")
            self.check(self.observe(replace(H, tombstones=(replace(T, pending_sha256=bad),))), "anchor_invalid")
        for changes in ({"tombstones": [T]}, {"tombstones": (T,) * 3}, {"inventory": []}, {"inventory": (OTHER,) * 4}):
            self.check(self.observe(replace(H, **changes)), "anchor_invalid")
        for b in (replace(B, issue=True), replace(B, issue=2**31), replace(B, epoch=0)):
            self.check(self.observe(binding=b), "binding_invalid")

    def test_24_no_io_credentials_environment_process_or_model_invocation(self):
        for name in ("builtins.open", "io.open", "os.open", "os.stat", "os.listdir", "os.scandir", "os.write", "os.fsync",
                     "os.getenv", "os._Environ.__getitem__", "subprocess.Popen", "subprocess.run", "os.system",
                     "os.fork", "os.posix_spawn", "socket.socket", "socket.create_connection"):
            guard = self.enterContext(patch(name, side_effect=AssertionError(name)))
            self.addCleanup(guard.assert_not_called)
        with patch("builtins.__import__", side_effect=AssertionError("dynamic import")) as imports:
            self.check(self.observe(), "observed_only", "BINDING_ANCHOR_OBSERVED_ONLY")
            self.check(self.cas(H, G), "observed_only", "CAS_PREVIEW_OBSERVED_ONLY")
            self.check(self.restart(*window(5)), "effect_unknown")
            self.check(self.observe(registry=None), "registry_missing")
            imports.assert_not_called()

    def test_25_static_no_main_lane_transport_wiring(self):
        tree = ast.parse(Path(d.__file__).read_text(encoding="utf-8"))
        imports = {n.module if isinstance(n, ast.ImportFrom) else a.name
                   for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        self.assertEqual(imports, {"dataclasses", "tools._kagari_vnext_claude_r6b_phase_d1a"})
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertFalse(calls & {"open", "exec", "eval", "compile", "__import__", "getattr", "globals"})
        root = Path(__file__).parent.parent
        for name in ("kagari_vnext_main", "kagari_vnext_main_service", "kagari_vnext_claude_lane_b", "kagari_vnext_claude_transport"):
            source = (root / "tools" / (name + ".py")).read_text(encoding="utf-8")
            self.assertNotIn("r6b_phase_d1b0", source)
            if name == "kagari_vnext_main":
                self.assertIn('raise MainError("CLAUDE_EXECUTOR_UNQUALIFIED")', source)

    def test_26_misses_never_construct_history_or_mutate_inputs(self):
        a, o = anchor(), window(0)[1]
        before = (asdict(R), asdict(a), asdict(o))
        for cls in (d.SyntheticRegistry, d.SyntheticAnchor, d.SyntheticHead, d.SyntheticTombstone, d.SyntheticRestart):
            guard = self.enterContext(patch.object(cls, "__init__", side_effect=AssertionError("initialize-on-miss")))
            self.addCleanup(guard.assert_not_called)
        for _ in range(2):
            self.check(d.evaluate_binding_and_anchor(B, None, a, H, ()), "registry_missing")
            self.check(d.preview_anchor_cas(B, R, None, G, H), "anchor_missing")
            self.check(d.classify_restart(B, R, None, H, o), "anchor_missing")
        self.assertEqual((asdict(R), asdict(a), asdict(o)), before)

    def test_27_nested_shape_and_genesis_rejections(self):
        for changes, reason in (({"registry": replace(R, bindings=[B])}, "registry_invalid"),
                ({"registry": replace(R, root_state=True)}, "registry_invalid"),
                ({"registry": replace(R, root_state="AUTHENTICATED")}, "registry_invalid"),
                ({"anchor": replace(anchor(), heads=[H])}, "anchor_invalid"),
                ({"anchor": replace(anchor(), acknowledged_head={})}, "anchor_ack_invalid")):
            self.check(self.observe(**changes), reason)
        for changes in ({"events": []}, {"events": EVENTS + ("ACKNOWLEDGED",)}, {"events": (True,)},
                        {"tombstone": asdict(T)}, {"result_sha256": True}):
            h, o = window(0)
            self.check(self.restart(h, replace(o, **changes)), "restart_invalid")
        self.check(self.observe(replace(G, sequence=1)), "genesis_shape_invalid")
        self.check(self.observe(replace(H, sequence=0)), "tombstone_at_genesis")
        self.check(self.observe(replace(H, tombstones=(replace(T, authority_epoch=True),))), "anchor_invalid")

    def test_28_impossible_head_cannot_be_closed_or_cas_base(self):
        pins = (T.reservation_sha256, T.pending_sha256, RESULT)
        impossible = replace(H, inventory=pins)
        observation = d.SyntheticRestart(T, pins, EVENTS, RESULT)
        self.check(self.restart(impossible, observation), "head_counter_inventory_mismatch")
        self.check(self.observe(impossible), "head_counter_inventory_mismatch")
        self.check(self.cas(successor(impossible, inventory=pins), impossible),
                   "head_counter_inventory_mismatch")
        for invalid in (
            replace(H, sequence=50),
            replace(H, generation=50),
            replace(H, sequence=2),
            replace(H, sequence=2, generation=3),
        ):
            self.check(self.observe(invalid), "head_counter_inventory_mismatch")
