"""Offline Phase B: synthetic registered Git facts; no Git/model processes.

Real #191 encoding/decoding, task_binding checks, Main._spec and MainSupervisor
are exercised. Only metadata/Git I/O and the host lock home are substituted.
All ephemeral directories are inside this checkout, not a real runtime root.
"""
from __future__ import annotations

import ast
import builtins
import copy
from dataclasses import FrozenInstanceError, asdict, fields, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

# Preserve ordinary import caching/type identity while rejecting transport imports.
_import = builtins.__import__


def _no_claude_transport(name, globals=None, locals=None, fromlist=(), level=0):
    if any(part.rsplit(".", 1)[-1] == "kagari_vnext_claude_transport"
           for part in (name, *(fromlist or ()))):
        raise AssertionError("real Claude transport import")
    return _import(name, globals, locals, fromlist, level)


with patch("builtins.__import__", side_effect=_no_claude_transport):
    from tools import _kagari_vnext_claude_r6b_phase_b as candidate

from tools import kagari_vnext_claude_lane_b as lane
from tools import kagari_vnext_claude_r6b_admission as phase_a
from tools import kagari_vnext_main as mainmod
from tools import kagari_vnext_main_service as service
from tools import kagari_vnext_task_binding as binding
from metadata_ref import MetadataStore, MetadataRefError
import task_record


ROOT = Path(__file__).resolve().parents[1]
SESSION = "b6a7f5a3-0da4-4141-b98d-89311798cab4"
OTHER_SESSION = "12cadac6-0e38-4472-9221-d1fb73f4ba5c"


class PhaseBContractsTests(unittest.TestCase):
    def setUp(self):
        # Unexpected process/network/config access is an assertion, not a probe.
        for name in ("subprocess.Popen", "os.system", "os.posix_spawn",
                     "socket.socket", "socket.create_connection"):
            guard = self.enterContext(patch(name, side_effect=AssertionError(name)))
            self.addCleanup(guard.assert_not_called)
        for module, names in (
            (lane.preflight, ("run_preflight", "_run_bounded", "_inspect_settings",
                              "_read_config", "_read_regular_file")),
            (lane, ("_exercise_fixture", "_auth_probe", "_config_context", "observe",
                    "_write_exclusive", "_read_receipt", "_runtime_root")),
            (mainmod, ("start_root_session", "opencode_request")),
        ):
            for name in names:
                guard = self.enterContext(patch.object(
                    module, name, side_effect=AssertionError(name)))
                self.addCleanup(guard.assert_not_called)

        temp = tempfile.TemporaryDirectory(prefix=".r6b-phase-b-", dir=ROOT)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / "project"
        self.common = self.project / ".git"
        self.worktree = self.project / ".worktrees" / "340"
        self.common.mkdir(parents=True)
        self.worktree.mkdir(parents=True)
        self.lock_home = self.root / "lock-home"
        self.lock_home.mkdir(mode=0o700)
        self.enterContext(patch.object(service, "_host_state_home", return_value=self.lock_home))

        self.base = "a" * 40
        self.tip = "b" * 40
        self.branch = "refs/heads/issue-340"
        self.snapshot = {"schema_version": 1, "title": "Synthetic Issue 340",
                         "body": "fixture-private-body-not-output"}
        self.contract_id, contract_bytes = task_record.encode_contract(
            "acme/widgets", "340", self.base, self.snapshot)
        self.contract = json.loads(contract_bytes)
        self.set_record()
        self.facts = {"number": 340, "repository": "acme/widgets", "state": "OPEN",
                      "title": self.snapshot["title"], "body": self.snapshot["body"]}
        self.git_facts = {
            (self.project, "check-ref-format", self.branch): "",
            (self.project, "rev-parse", "--show-toplevel"): str(self.project),
            (self.worktree, "rev-parse", "--show-toplevel"): str(self.worktree),
            (self.project, "rev-parse", "--path-format=absolute", "--git-common-dir"): str(self.common),
            (self.worktree, "rev-parse", "--path-format=absolute", "--git-common-dir"): str(self.common),
            (self.worktree, "symbolic-ref", "--quiet", "HEAD"): self.branch,
            (self.project, "rev-parse", "--verify", self.branch): self.base,
            (self.worktree, "rev-parse", "HEAD"): self.base,
            (self.project, "worktree", "list", "--porcelain"):
                f"worktree {self.worktree}\nHEAD {self.base}\nbranch {self.branch}\n",
        }
        self.enterContext(patch.object(binding, "git", side_effect=self.git_read))
        self.enterContext(patch.object(MetadataStore, "_git", autospec=True,
                                      side_effect=self.store_git))
        self.enterContext(patch.object(MetadataStore, "fetch_tip", autospec=True,
                                      side_effect=self.fetch_tip))
        self.enterContext(patch.object(MetadataStore, "read_record", autospec=True,
                                      side_effect=self.read_record))
        self.enterContext(patch.object(MetadataStore, "read_object", autospec=True,
                                      side_effect=self.read_object))
        self.store = MetadataStore(self.project, "acme/widgets")
        self.task = lane.LaneBTask(self.project, self.worktree, "acme/widgets", 340,
                                   self.branch, self.base, self.contract_id)
        self.main = self.make_main()
        self.pins = candidate._PolicyPins(*("12345"[i] * 64 for i in range(5)))

    def set_record(self, **extra):
        self.record_id, raw = task_record.encode_record(
            "acme/widgets", "340", self.base,
            {"schema_version": 1, "branch_ref": self.branch,
             "contract_id": self.contract_id, **extra})
        self.record = json.loads(raw)

    def git_read(self, root, *args):
        try:
            return self.git_facts[(root, *args)]
        except KeyError:
            raise binding.ControlError("GIT_MISMATCH", "synthetic unregistered fact") from None

    def store_git(self, store, args, **_kwargs):
        self.check_store(store)
        if args == ["rev-parse", "--show-object-format"]:
            return b"sha1\n"
        if args == ["rev-parse", "--show-toplevel"]:
            return str(self.project).encode()
        if args[:2] == ["config", "--local"]:
            return b""
        raise AssertionError("unexpected Git adapter operation")

    def check_store(self, store):
        if (store.root, store.repository, store.remote) != (self.project, "acme/widgets", "origin"):
            raise MetadataRefError("synthetic store mismatch")

    def fetch_tip(self, store):
        self.check_store(store)
        return self.tip

    def read_record(self, store, commit, task):
        self.check_store(store)
        self.assertEqual(commit, self.tip)
        return (self.record_id, copy.deepcopy(self.record)) if task == "340" else None

    def read_object(self, store, commit, kind, object_id, *, task, subject):
        self.check_store(store)
        if (commit, kind, object_id, task, subject) != (
                self.tip, "contract", self.contract_id, "340", self.base):
            raise MetadataRefError("synthetic contract mismatch")
        return copy.deepcopy(self.contract)

    def make_main(self, executor="claude-cli"):
        return mainmod.RepositoryMain(
            self.store, approved={340: mainmod.ApprovedTask(
                340, self.branch, self.base, self.contract_id, executor)},
            read_issue=lambda _number: dict(self.facts),
            runtime_home=self.root / "uncreated-runtime",
            port_for_issue=lambda _number: self.fail("port access"),
        )

    def observe(self, task=None, **kwargs):
        return candidate._observe_main_intent(
            self.main, self.task if task is None else task, SESSION,
            pins=self.pins, **kwargs)

    def intent(self):
        with service.MainSupervisor(self.main):
            intent, result = self.observe()
        self.assertIsNotNone(intent, result)
        self.assert_non_grant(result)
        self.assertEqual(result["stage"], "OPERATION_INTENT_OBSERVED")
        return intent

    def evidence(self, intent):
        return candidate._PolicyEvidence(
            candidate._SYNTHETIC_SOURCE, candidate._digest(asdict(intent)),
            intent.session_id, intent.policy_sha256, intent.policy_pins,
            lane.EXPECTED_VERSION, "personal", (), candidate._REQUIRED_CHECKS)

    def pair(self, intent):
        pending = candidate._pending_contract(intent)
        result = candidate._ResultReceipt(
            1, candidate._digest(asdict(pending)), "OBSERVED",
            "SYNTHETIC_TURN_RESULT", "TURN_RESULT_OBSERVED")
        return pending, result

    def assert_non_grant(self, result):
        self.assertEqual(set(result), {
            "qualification", "dispatch_allowed", "task_completed", "ask_capability",
            "effective_policy", "status", "reason", "stage", "session_id",
        })
        self.assertEqual(result["qualification"], "UNQUALIFIED")
        self.assertIs(result["dispatch_allowed"], False)
        self.assertIs(result["task_completed"], False)
        self.assertEqual(result["effective_policy"], "UNVERIFIED")
        self.assertEqual(result["ask_capability"], "UNQUALIFIED")
        self.assertIn(result["status"], ("UNQUALIFIED", "UNKNOWN"))
        self.assertNotIn(self.snapshot["body"], json.dumps(result))
        self.assertNotIn(str(self.project), json.dumps(result))
        self.assertFalse(self.main.runtime_home.exists())

    def assert_no_intent(self, observed):
        intent, result = observed
        self.assertIsNone(intent)
        self.assert_non_grant(result)
        self.assertEqual(result["stage"], "NOT_OBSERVED")
        return result

    def test_live_main_owner_phase_a_spec_and_frozen_exact_intent(self):
        self.assertEqual(self.assert_no_intent(self.observe())["reason"], "MAIN_OWNER_REQUIRED")
        with patch.object(phase_a, "_observe_main_owned_binding",
                          wraps=phase_a._observe_main_owned_binding) as phase:
            with patch.object(self.main, "_spec", wraps=self.main._spec) as spec:
                intent = self.intent()
        phase.assert_called_once()
        self.assertEqual(spec.call_count, 2)
        self.assertEqual(intent.operation, lane.OPERATION)
        self.assertEqual(intent.executor, "claude-cli")
        self.assertEqual(intent.session_id, SESSION)
        self.assertEqual(intent.issue_snapshot_sha256, candidate._digest(self.snapshot))
        self.assertEqual(intent.contract_id, self.contract_id)
        self.assertEqual(intent.git_common_dir, str(self.common))
        self.assertEqual(intent.worktree, str(self.worktree))
        self.assertEqual(intent.argv_sha256, candidate._digest(lane._argv(SESSION)))
        self.assertIsNot(intent.policy_pins, self.pins)
        for obj, name in ((intent, "operation"), (intent.policy_pins, "binary_sha256")):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, name, "different")
        with self.assertRaises(TypeError):
            candidate._OperationIntent(**asdict(intent), transport=lambda: None)
        # Repeated observation (even a new UUID) never mints dispatch permission.
        with service.MainSupervisor(self.main):
            for sid in (SESSION, OTHER_SESSION):
                _, result = candidate._observe_main_intent(self.main, self.task, sid, pins=self.pins)
                self.assert_non_grant(result)
            with self.assertRaises(mainmod.MainError) as blocked:
                self.main.dispatch(340)
            self.assertEqual(blocked.exception.code, "CLAUDE_EXECUTOR_UNQUALIFIED")
        self.assert_non_grant(lane.dispatch())

    def test_intent_default_repr_omits_paths_and_native_session_without_unbinding(self):
        intent = self.intent()
        sensitive = {"project_root", "worktree", "git_common_dir", "session_id"}
        self.assertEqual({f.name for f in fields(intent) if not f.repr}, sensitive)
        for name in sensitive:
            with self.subTest(name=name):
                value = getattr(intent, name)
                self.assertEqual(asdict(intent)[name], value)
                for display in (repr(intent), str(intent)):
                    self.assertNotIn(value, display)
                    self.assertNotIn(name + "=", display)
        self.assertEqual(candidate._pending_contract(intent).intent_sha256,
                         candidate._digest(asdict(intent)))

    def test_wrong_locator_issue_contract_executor_and_invalid_inputs(self):
        changes = (
            {"repository": "other/repo"}, {"remote": "upstream"}, {"issue": 341},
            {"base_revision": "f" * 40}, {"branch_ref": "refs/heads/other"},
            {"expected_contract_id": "f" * 64}, {"project_root": self.worktree},
            {"worktree": self.project}, {"worktree": self.root / "missing"},
            {"issue": True}, {"worktree": Path("relative")},
        )
        with service.MainSupervisor(self.main):
            for change in changes:
                with self.subTest(change=change):
                    self.assert_no_intent(self.observe(replace(self.task, **change)))
            for sid in ("ses_opencode", SESSION.upper(), "", None):
                self.assert_no_intent(candidate._observe_main_intent(
                    self.main, self.task, sid, pins=self.pins))
            for pins in (None, asdict(self.pins), replace(self.pins, binary_sha256=[])):
                self.assert_no_intent(candidate._observe_main_intent(
                    self.main, self.task, SESSION, pins=pins))
        self.main = self.make_main("opencode")
        with service.MainSupervisor(self.main):
            self.assertEqual(self.assert_no_intent(self.observe())["reason"], "R6B_SELECTION_MISMATCH")

    def test_wrong_registration_branch_common_dir_and_noncanonical_worktree(self):
        keys = (
            (self.project, "worktree", "list", "--porcelain"),
            (self.worktree, "symbolic-ref", "--quiet", "HEAD"),
            (self.worktree, "rev-parse", "--path-format=absolute", "--git-common-dir"),
            (self.worktree, "rev-parse", "HEAD"),
        )
        with service.MainSupervisor(self.main):
            for key in keys:
                with self.subTest(key=key), patch.dict(self.git_facts, {key: "wrong"}):
                    self.assert_no_intent(self.observe())
            alias = self.worktree.parent / "alias"
            alias.symlink_to(self.worktree, target_is_directory=True)
            self.assert_no_intent(self.observe(replace(self.task, worktree=alias)))

    def test_exact_live_issue_snapshot_rejects_each_mismatch(self):
        with service.MainSupervisor(self.main):
            for name, value in (("number", 341), ("number", True), ("repository", "other/repo"),
                                ("state", "CLOSED"), ("title", "drift"), ("body", "drift")):
                with self.subTest(name=name, value=value), patch.dict(self.facts, {name: value}):
                    result = self.assert_no_intent(self.observe())
                    self.assertEqual(result["reason"], "ISSUE_SNAPSHOT_DRIFT")

    def test_retired_stale_missing_and_corrupt_metadata_fail_closed(self):
        with service.MainSupervisor(self.main):
            for disposition in ({"kind": "cancelled"}, {"kind": "superseded", "replacement": {
                    "repository": "acme/widgets", "task": "341"}}):
                self.set_record(disposition=disposition)
                self.assert_no_intent(self.observe())
            self.set_record(contract_id="f" * 64)
            self.assert_no_intent(self.observe())
            self.set_record()
            self.record["payload"]["branch_ref"] = "refs/heads/tampered"
            self.assert_no_intent(self.observe())
            self.set_record()
            with patch.object(MetadataStore, "fetch_tip", return_value=None):
                self.assert_no_intent(self.observe())

    def test_drift_between_live_issue_reads_is_not_frozen_as_current(self):
        count = 0

        def read(_number):
            nonlocal count
            count += 1
            return {**self.facts, "body": "drift"} if count == 2 else dict(self.facts)

        self.main.read_issue = read
        with service.MainSupervisor(self.main):
            self.assertEqual(self.assert_no_intent(self.observe())["reason"], "ISSUE_SNAPSHOT_DRIFT")
        self.assertEqual(count, 2)

    def test_retirement_during_live_issue_read_and_selection_drift(self):
        def retire(_number):
            self.set_record(disposition={"kind": "cancelled"})
            return dict(self.facts)

        self.main.read_issue = retire
        with service.MainSupervisor(self.main):
            self.assert_no_intent(self.observe())
        self.set_record()

        def change_selection(_number):
            object.__setattr__(self.main.approved[340], "executor", "opencode")
            return dict(self.facts)

        self.main.read_issue = change_selection
        with service.MainSupervisor(self.main):
            self.assertEqual(self.assert_no_intent(self.observe())["status"], "UNKNOWN")

    def test_owner_lost_or_replaced_during_observation_and_observe_only(self):
        with service.MainSupervisor(self.main) as owner:
            def lose_owner(_number):
                owner.__exit__()
                return dict(self.facts)

            self.main.read_issue = lose_owner
            self.assertEqual(self.assert_no_intent(self.observe())["status"], "UNKNOWN")
        self.main = self.make_main()
        with service.MainSupervisor(self.main):
            def replace_owner(_number):
                self.main._main_owner = object()
                return dict(self.facts)

            self.main.read_issue = replace_owner
            self.assertEqual(self.assert_no_intent(self.observe())["status"], "UNKNOWN")
        self.main = self.make_main()
        with service.MainSupervisor(self.main, observe_only=True):
            self.assert_no_intent(self.observe())
        with patch.object(self.main, "_main_owner", object()):
            self.assert_no_intent(self.observe())

    def test_owner_restricted_modes_and_mid_observation_drift_are_excluded(self):
        with service.MainSupervisor(self.main) as owner:
            for name in ("admission_only", "publication_only"):
                with self.subTest(name=name):
                    # Synthetic mode changes retain the real owner/lock checks.
                    with patch.object(owner, name, True):
                        result = self.assert_no_intent(self.observe())
                        self.assertEqual(result["reason"], "V2_MAIN_EXECUTION_NOT_QUALIFIED")

                    def drift(_number):
                        setattr(owner, name, True)
                        return dict(self.facts)

                    with patch.object(owner, name, False), patch.object(
                            self.main, "read_issue", side_effect=drift):
                        result = self.assert_no_intent(self.observe())
                        self.assertEqual(result["status"], "UNKNOWN")
                        self.assertEqual(result["reason"], "R6B_RECONCILIATION_REQUIRED")

    def test_v2_all_maps_and_mid_observation_drift_are_excluded(self):
        names = ("v2_readonly_for_issue", "v2_worktree_for_issue", "v2_session_for_issue",
                 "v2_pr_first_for_issue", "v2_product_pr_for_issue")
        with service.MainSupervisor(self.main):
            for name in names:
                with self.subTest(name=name), patch.object(self.main, name, {341: object()}):
                    result = self.assert_no_intent(self.observe())
                    self.assertEqual(result["reason"], "V2_MAIN_EXECUTION_NOT_QUALIFIED")

            for name in names:
                with self.subTest(drift=name), patch.object(self.main, name, {}):
                    def drift(_number):
                        getattr(self.main, name)[340] = object()
                        return dict(self.facts)

                    with patch.object(self.main, "read_issue", side_effect=drift):
                        result = self.assert_no_intent(self.observe())
                        self.assertEqual(result["status"], "UNKNOWN")
                        self.assertEqual(result["reason"], "R6B_RECONCILIATION_REQUIRED")

    def test_reader_exception_is_sanitized_and_old_intent_is_not_revalidated_by_policy(self):
        intent = self.intent()
        self.main.read_issue = lambda _n: (_ for _ in ()).throw(RuntimeError("secret-marker"))
        with service.MainSupervisor(self.main):
            result = self.assert_no_intent(self.observe())
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertNotIn("secret-marker", json.dumps(result))
        # A pure historical self-consistency check is not renewed Main ownership.
        self.assert_non_grant(candidate._observe_policy(intent, self.evidence(intent)))

    def test_matching_synthetic_policy_and_receipts_remain_pure_and_non_granting(self):
        intent = self.intent()
        evidence = self.evidence(intent)
        pending, result = self.pair(intent)
        with patch.object(Path, "open", side_effect=AssertionError("file I/O")) as opening:
            with patch.object(Path, "stat", side_effect=AssertionError("path observation")) as stat:
                policy = candidate._observe_policy(intent, evidence)
                receipt = candidate._observe_receipts(intent, pending=(pending,), results=(result,))
                self.assertEqual(candidate._pending_contract(intent), pending)
        opening.assert_not_called()
        stat.assert_not_called()
        self.assertEqual(policy["stage"], "POLICY_EVIDENCE_OBSERVED")
        self.assertEqual(receipt["stage"], "RECEIPT_PAIR_OBSERVED")
        self.assert_non_grant(policy)
        self.assert_non_grant(receipt)

    def test_policy_rejects_untrusted_flags_preflight_and_mismatched_pins(self):
        intent = self.intent()
        good = self.evidence(intent)
        changes = [
            {"source": source} for source in ("CLI_FLAGS", "PREFLIGHT", "LIVE_TRUSTED_QUALIFIER", "UNKNOWN")
        ] + [
            {"intent_sha256": "f" * 64}, {"session_id": OTHER_SESSION},
            {"policy_sha256": "f" * 64}, {"cli_version": "2.1.294"}, {"profile": "company"},
        ] + [
            {"pins": replace(self.pins, **{f.name: "f" * 64})} for f in fields(self.pins)
        ]
        for change in changes:
            with self.subTest(change=change):
                result = candidate._observe_policy(intent, replace(good, **change))
                self.assert_non_grant(result)
                self.assertEqual(result["stage"], "NOT_OBSERVED")

    def test_policy_rejects_unchecked_or_ambiguous_settings_hooks_plugins_mcp_tools(self):
        intent = self.intent()
        good = self.evidence(intent)
        for i, (name, _state) in enumerate(good.checks):
            for state in ("UNKNOWN", "UNCHECKED", "MUTABLE", "NONEMPTY"):
                checks = good.checks[:i] + ((name, state),) + good.checks[i + 1:]
                with self.subTest(name=name, state=state):
                    result = candidate._observe_policy(intent, replace(good, checks=checks))
                    self.assert_non_grant(result)
                    self.assertEqual(result["stage"], "NOT_OBSERVED")

    def test_policy_rejects_environment_override_and_mutable_or_unknown_schema(self):
        intent = self.intent()
        good = self.evidence(intent)
        changes = [
            {"environment_overrides": (key,)} for key in (
                "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "UNKNOWN")
        ] + [
            {"environment_overrides": []}, {"checks": list(good.checks)},
            {"checks": tuple(list(pair) for pair in good.checks)},
            {"checks": good.checks[:-1]}, {"checks": good.checks + good.checks[:1]},
            {"checks": good.checks + (("unknown_surface", "EMPTY_EFFECTIVE"),)},
            {"pins": asdict(self.pins)}, {"pins": replace(self.pins, binary_sha256="unchecked")},
        ]
        for change in changes:
            with self.subTest(change=change):
                result = candidate._observe_policy(intent, replace(good, **change))
                self.assert_non_grant(result)
                self.assertEqual(result["stage"], "NOT_OBSERVED")
        self.assert_non_grant(candidate._observe_policy(intent, asdict(good)))
        with self.assertRaises(TypeError):
            candidate._PolicyEvidence(**asdict(good), unknown=True)

    def test_forged_intent_cannot_change_operation_session_policy_or_canonical_shape(self):
        intent = self.intent()
        for change in ({"operation": "arbitrary-task"}, {"policy_version": "v2"},
                       {"session_id": OTHER_SESSION}, {"issue": True}, {"executor": "opencode"},
                       {"argv_sha256": "f" * 64}, {"policy_sha256": "f" * 64},
                       {"project_root": "/a/../b"}, {"worktree": "relative"},
                       {"git_common_dir": []}, {"policy_pins": asdict(self.pins)}):
            with self.subTest(change=change):
                bad = replace(intent, **change)
                self.assertIsNone(candidate._pending_contract(bad))
                result = candidate._observe_policy(bad, self.evidence(intent))
                self.assert_non_grant(result)
                self.assertEqual(result["stage"], "NOT_OBSERVED")

    def test_pending_payload_is_exact_sanitized_and_not_a_durability_claim(self):
        intent = self.intent()
        pending, result = self.pair(intent)
        self.assertEqual(pending.state, "PENDING_BEFORE_EFFECT")
        self.assertEqual(pending.intent_sha256, candidate._digest(asdict(intent)))
        self.assertEqual(pending.policy_sha256, intent.policy_sha256)
        self.assertEqual(pending.operation, lane.OPERATION)
        self.assertRegex(pending.namespace, r"\Ar6b-phase-b-[a-f0-9]{64}\Z")
        self.assertEqual(pending.namespace, "r6b-phase-b-" + candidate._digest({
            "repository": intent.repository, "issue": intent.issue,
        }))
        serialized = json.dumps([asdict(pending), asdict(result)])
        for raw in (SESSION, str(self.project), lane.FIXED_PROMPT, self.snapshot["body"]):
            self.assertNotIn(raw, serialized)
        self.assertEqual(set(asdict(pending)), {
            "schema_version", "namespace", "task_sha256", "intent_sha256", "session_sha256",
            "operation", "policy_version", "policy_sha256", "state",
        })
        self.assertEqual(set(asdict(result)), {"schema_version", "pending_sha256", "status", "reason", "stage"})
        with self.assertRaises(FrozenInstanceError):
            pending.state = "COMPLETE"
        with self.assertRaises(TypeError):
            candidate._PendingReceipt(**asdict(pending), unknown=True)
        with self.assertRaises(TypeError):
            candidate._ResultReceipt(**asdict(result), tokens=42)

    def test_unknown_interrupted_duplicate_or_result_only_never_replay(self):
        intent = self.intent()
        first, last = self.pair(intent)
        cases = (
            (None, None), ((first,), None), (None, (last,)),
            ((first,), ()), ((), (last,)), ((first, first), (last,)),
            ((first,), (last, last)), ([first], [last]),
            ((asdict(first),), (last,)), ((first,), (asdict(last),)),
            ((first,), (replace(last, status="UNKNOWN", reason="INTERRUPTED", stage="UNKNOWN"),)),
        )
        for pending, results in cases:
            with self.subTest(pending=pending, results=results):
                result = candidate._observe_receipts(intent, pending=pending, results=results)
                self.assert_non_grant(result)
                self.assertEqual(result["status"], "UNKNOWN")
        absent = candidate._observe_receipts(intent, pending=(), results=())
        self.assert_non_grant(absent)
        self.assertEqual(absent["status"], "UNKNOWN")
        self.assertEqual(absent["reason"], "RECEIPT_RECONCILIATION_REQUIRED")
        self.assertEqual(absent["stage"], "NOT_OBSERVED")
        for _ in range(2):
            self.assert_non_grant(candidate._observe_receipts(intent, pending=(first,), results=(last,)))

    def test_wrong_session_task_operation_policy_digest_and_unsanitized_result(self):
        intent = self.intent()
        first, last = self.pair(intent)
        for field in fields(first):
            value = True if field.name == "schema_version" else "f" * 64
            bad = replace(first, **{field.name: value})
            # Rehashing a forged pending receipt is still not an exact match.
            forged_result = replace(last, pending_sha256=candidate._digest(asdict(bad)))
            with self.subTest(field=field.name):
                result = candidate._observe_receipts(intent, pending=(bad,), results=(forged_result,))
                self.assert_non_grant(result)
                self.assertEqual(result["status"], "UNKNOWN")
        for change in ({"pending_sha256": "f" * 64}, {"schema_version": True},
                       {"status": "SUCCESS"}, {"reason": "secret-marker"}, {"stage": "TASK_COMPLETED"}):
            result = candidate._observe_receipts(intent, pending=(first,), results=(replace(last, **change),))
            self.assert_non_grant(result)
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertNotIn("secret-marker", json.dumps(result))

    def test_new_session_contract_policy_branch_or_remote_cannot_escape_namespace(self):
        intent = self.intent()
        first, last = self.pair(intent)
        changed_pins = replace(intent.policy_pins, binary_sha256="f" * 64)
        changes = (
            {"session_id": OTHER_SESSION, "argv_sha256": candidate._digest(lane._argv(OTHER_SESSION)),
             "policy_sha256": candidate._policy_digest(OTHER_SESSION, intent.policy_pins)},
            {"contract_id": "f" * 64}, {"branch_ref": "refs/heads/another"}, {"remote": "other"},
            {"policy_pins": changed_pins, "policy_sha256": candidate._policy_digest(SESSION, changed_pins)},
        )
        for change in changes:
            with self.subTest(change=change):
                other = replace(intent, **change)
                self.assertEqual(candidate._pending_contract(other).namespace, first.namespace)
                result = candidate._observe_receipts(other, pending=(first,), results=(last,))
                self.assert_non_grant(result)
                self.assertEqual(result["status"], "UNKNOWN")

    def test_clone_or_moved_common_dir_keeps_namespace_but_changes_exact_digests(self):
        intent = self.intent()
        first, last = self.pair(intent)
        clone = self.root / "different-clone"
        # Pure synthetic locators: no clone, directory creation or Git operation.
        changes = (
            {"git_common_dir": str(self.root / "moved-common-dir")},
            {"project_root": str(clone), "worktree": str(clone / ".worktrees" / "340"),
             "git_common_dir": str(clone / ".git")},
        )
        for change in changes:
            with self.subTest(change=change):
                other = replace(intent, **change)
                pending = candidate._pending_contract(other)
                self.assertIsNotNone(pending)
                self.assertEqual(pending.namespace, first.namespace)
                self.assertNotEqual(pending.task_sha256, first.task_sha256)
                self.assertNotEqual(pending.intent_sha256, first.intent_sha256)
                for results in ((), (last,)):
                    result = candidate._observe_receipts(other, pending=(first,), results=results)
                    self.assert_non_grant(result)
                    self.assertEqual(result["status"], "UNKNOWN")
                    self.assertEqual(result["reason"], "RECEIPT_RECONCILIATION_REQUIRED")


class PhaseBSourceBoundaryTests(unittest.TestCase):
    def test_no_transport_spawn_cli_or_public_entrypoint(self):
        tree = ast.parse(Path(candidate.__file__).read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
                imports.extend(alias.name for alias in node.names)
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                self.assertTrue(node.name.startswith("_"), node.name)
        for name in imports:
            self.assertNotIn("transport", name)
            self.assertNotIn(name, {"subprocess", "os", "socket", "argparse", "Callable"})
        calls = {node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(calls & {"dispatch", "_exercise_fixture", "_run_bounded", "run_preflight",
                                  "Popen", "system", "open", "write_text", "write_bytes", "mkdir"})
        self.assertFalse(any(isinstance(node, ast.If) and "__name__" in ast.unparse(node.test)
                             for node in tree.body))
