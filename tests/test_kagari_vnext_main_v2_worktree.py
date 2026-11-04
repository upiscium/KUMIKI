"""#291 R5A1: source-only Main-owned v2 Worktree admission on disposable Git.

No real Product repo, GitHub writer/PR, Task Session/model, daemon, package
activation, release or publisher is ever invoked by this fixture.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import test_kagari_metadata_v2_task_admission as v2_fixture
from tests.test_metadata_v2 import REPOSITORY
from tools import kagari_vnext_main as main_module
from tools import kagari_vnext_main_service as service
from tools.kagari_vnext_main import MainError
from tools.kagari_vnext_main_v2_observation import V2ReadOnlyMainHost
from tools.kagari_vnext_main_v2_worktree import V2MainWorktreeHost
import kagari_vnext_attachment_gate as gate
from task_record import encode_record
from metadata_v2 import ARCHIVE_REF, issue_ref


class R5A1WorktreeTest(unittest.TestCase):
    def setUp(self):
        source = v2_fixture.V2TaskAdmissionTest("runTest")
        self.addCleanup(source.doCleanups)
        source.setUp()
        self.source = source
        self.issue = source.registered.issue
        self.facts = {
            self.issue: self.issue_facts(source.approved),
        }
        self.grants = []
        self.legacy_checks = []
        self.issue_reads = []
        self.allow = True
        self.allow_observe = True
        self.legacy_clear = True
        self.plan = {
            "schema_version": 1,
            "repository": REPOSITORY,
            "project_root": str(source.project),
            "runtime_home": str(source.home / "r5a1-no-runtime"),
            "tasks": [self.choice(source.registered)],
        }
        self.lock_home = source.project.parent / "r5a1-main-owner-home"
        self.lock_home.mkdir(mode=0o700)
        patched = patch.object(
            service, "_host_state_home", return_value=self.lock_home)
        patched.start()
        self.addCleanup(patched.stop)

    @staticmethod
    def choice(registered):
        return {
            "issue": registered.issue, "branch_ref": registered.branch_ref,
            "base_revision": registered.base_revision,
            "contract_id": registered.contract_id,
            "port": 32200 + registered.issue,
        }

    @staticmethod
    def issue_facts(approved):
        return {
            "repository": approved.repository, "number": approved.issue,
            "state": "OPEN", "title": approved.title, "body": approved.body,
        }

    def read_issue(self, issue):
        self.issue_reads.append(issue)
        return copy.deepcopy(self.facts[issue])

    def observe_authorize(self, request):
        return self.allow_observe

    def legacy(self, request):
        self.legacy_checks.append(request)
        return self.legacy_clear

    def authorize(self, request):
        self.grants.append(request)
        return self.allow

    def make_host(self, lease, *, number=101, approved=None,
                  registered=None, authorize=None, legacy=None,
                  observer=None, selection=None):
        source = self.source
        approved = approved or source.approved
        registered = registered or source.registered
        selection = selection or source.selection(number)
        readonly = V2ReadOnlyMainHost(
            selection,
            source.authority(
                registered=registered, approved=approved, observer=observer),
            lease, legacy or self.legacy, self.observe_authorize,
        )
        return V2MainWorktreeHost(readonly, authorize or self.authorize)

    def main(self, hosts):
        return service.build_main(
            copy.deepcopy(self.plan), host=service.MainV2WorktreeCapabilities(
                read_issue=self.read_issue, admissions=hosts))

    def with_owner(self, action, *, host_override=None, hosts=None):
        source = self.source
        with gate.hold_verified(source.project, source.home, source.package) as lease:
            all_hosts = hosts(lease) if callable(hosts) else {
                self.issue: host_override or self.make_host(lease)}
            main = self.main(all_hosts)
            with service.MainSupervisor(
                    main, attachment_lease=lease, admission_only=True) as runner:
                return action(runner, main, lease)

    def one(self, **kwargs):
        return self.with_owner(
            lambda runner, *_: runner.admit_worktree(self.issue), **kwargs)

    def before(self):
        src = self.source
        return {
            "HEAD": src.attached.git("rev-parse", "HEAD"),
            "status": src.attached.git("status", "--porcelain=v1", "-uall"),
            "index": (src.project / ".git/index").read_bytes(),
            "receipt": src.attached.receipt().read_bytes(),
        }

    def preserved(self, before):
        src = self.source
        self.assertEqual(before["HEAD"], src.attached.git("rev-parse", "HEAD"))
        self.assertEqual(before["status"], src.attached.git(
            "status", "--porcelain=v1", "-uall"))
        self.assertEqual(before["index"], (src.project / ".git/index").read_bytes())
        self.assertEqual(before["receipt"], src.attached.receipt().read_bytes())
        self.assertFalse((src.home / "r5a1-no-runtime").exists())

    def no_worktree(self, issue=101):
        self.assertFalse((self.source.project / ".worktrees" / str(issue)).exists())

    def test_approved_one_issue_create_and_resume_are_only_effects(self):
        before = self.before()
        with patch.object(main_module, "start_root_session",
                          side_effect=AssertionError("Session forbidden")), patch.object(
                          main_module, "opencode_request",
                          side_effect=AssertionError("model forbidden")), patch.object(
                          main_module, "isolated_metadata_observation",
                          side_effect=AssertionError("legacy metadata forbidden")):
            created = self.one()
            resumed = self.one()
        self.assertEqual("worktree_admitted", created["status"])
        self.assertEqual("CREATED", created["worktree_state"])
        self.assertEqual("RESUMED", resumed["worktree_state"])
        self.assertEqual(created["worktree"], resumed["worktree"])
        self.assertEqual(created["contract_id"], self.source.registered.contract_id)
        self.assertEqual("NOT_STARTED", created["runtime"])
        self.assertEqual("NOT_SENT", created["kickoff"])
        for field in ("task_execution_authorized", "session_started",
                      "pr_authorized", "github_write_authorized"):
            self.assertIs(created[field], False)
        self.assertEqual(self.source.base, self.source.attached.git(
            "-C", created["worktree"], "rev-parse", "HEAD"))
        self.assertGreaterEqual(len(self.grants), 6)
        self.assertTrue(all(req.operation == "v2-worktree-admission"
                            for req in self.grants))
        self.preserved(before)

    def test_two_issues_have_distinct_registered_worktrees(self):
        second_approved = self.source.approved_issue(202)
        second_registered = self.source.publish_first(second_approved)
        self.plan["tasks"].append(self.choice(second_registered))
        self.facts[202] = self.issue_facts(second_approved)
        before = self.before()
        def two_hosts(lease):
            return {
                101: self.make_host(lease),
                202: self.make_host(lease, number=202, approved=second_approved,
                                    registered=second_registered),
            }
        def operation(runner, *_):
            return runner.admit_worktree(101), runner.admit_worktree(202)
        first, second = self.with_owner(operation, hosts=two_hosts)
        self.assertEqual(("CREATED", "CREATED"),
                         (first["worktree_state"], second["worktree_state"]))
        self.assertNotEqual(first["worktree"], second["worktree"])
        self.assertEqual(2, len(list((self.source.project / ".worktrees").iterdir())))
        self.preserved(before)

    def test_negative_no_owner_and_no_admission_capability(self):
        before = self.before()
        with gate.hold_verified(self.source.project, self.source.home,
                                self.source.package) as lease:
            main = self.main({self.issue: self.make_host(lease)})
            with self.assertRaisesRegex(MainError, "V2_MAIN_WORKTREE_OWNER_REQUIRED"):
                main.admit_v2_worktree(self.issue)
            with service.MainSupervisor(main, observe_only=True) as reader:
                with self.assertRaisesRegex(MainError, "V2_MAIN_ADMISSION_OWNER_REQUIRED"):
                    reader.admit_worktree(self.issue)
            with self.assertRaisesRegex(MainError, "V2_MAIN_ADMISSION_CONFIGURATION_INVALID"):
                service.MainSupervisor(main, admission_only=True)
            with self.assertRaisesRegex(MainError, "V2_MAIN_EXECUTION_NOT_QUALIFIED"):
                with service.MainSupervisor(main):
                    pass
            with self.assertRaisesRegex(MainError, "V2_MAIN_ADMISSION_CONFIGURATION_INVALID"):
                service.MainSupervisor(main, attachment_lease=lease,
                                       admission_only=True, cleanup_for_issue={
                                           self.issue: lambda: None})
        self.preserved(before)
        self.no_worktree()

    def test_non_admission_cycle_and_direct_dispatch_remain_blocked(self):
        before = self.before()
        def operation(runner, main, _lease):
            with self.assertRaisesRegex(MainError, "V2_MAIN_EXECUTION_NOT_QUALIFIED"):
                runner.cycle(dispatch=True)
            with self.assertRaisesRegex(MainError, "V2_MAIN_EXECUTION_NOT_QUALIFIED"):
                main.dispatch(self.issue)
            with self.assertRaisesRegex(MainError, "V2_MAIN_PR_NOT_QUALIFIED"):
                main.guarded_draft_pr(self.issue, object())
            return "blocked"
        self.assertEqual("blocked", self.with_owner(operation))
        self.preserved(before)
        self.no_worktree()

    def test_missing_grant_and_nonliteral_grant_refuse_pre_git_lock(self):
        before = self.before()
        for value in (False, "yes", None, 1):
            with self.subTest(value=value):
                self.allow = value
                with self.assertRaisesRegex(MainError, "V2_MAIN_WORKTREE_GRANT_REQUIRED"):
                    self.one()
                self.no_worktree()
                self.assertFalse((self.source.project / ".git" /
                                  "kagari-issue-worktree-locks").exists())
        self.preserved(before)

    def test_false_legacy_assigned_refuses(self):
        before = self.before()
        self.legacy_clear = False
        with self.assertRaisesRegex(MainError, "V2_MAIN_EXISTING_V1_ASSIGNMENT"):
            self.one()
        self.assertFalse(self.grants)
        self.preserved(before)
        self.no_worktree()

    def test_grant_callback_unavailable_refuses_pre_git(self):
        before = self.before()
        def explode(req):
            raise RuntimeError("private error")
        with self.assertRaisesRegex(MainError, "V2_MAIN_HOST_GRANT_UNAVAILABLE"):
            self.one(hosts=lambda lease: {101: self.make_host(
                lease, authorize=explode)})
        self.preserved(before)
        self.no_worktree()

    def test_closed_or_changed_live_issue_refuses(self):
        before = self.before()
        for key, value in (("state", "CLOSED"), ("title", "changed"),
                           ("body", "changed"), ("repository", "foreign/issue"),
                           ("number", 202)):
            with self.subTest(key=key):
                old = self.facts[101][key]
                self.facts[101][key] = value
                try:
                    with self.assertRaisesRegex(MainError, "V2_MAIN_ISSUE_SNAPSHOT_DRIFT"):
                        self.one()
                finally:
                    self.facts[101][key] = old
                self.no_worktree()
        self.preserved(before)

    def test_issue_drift_during_grant_refuses_before_git_effect(self):
        before = self.before()
        def mutator(req):
            self.facts[101]["body"] = "mutated during grant"
            return True
        with self.assertRaisesRegex(MainError, "V2_MAIN_ISSUE_SNAPSHOT_DRIFT"):
            self.one(hosts=lambda lease: {101: self.make_host(
                lease, authorize=mutator)})
        self.preserved(before)
        self.no_worktree()

    def test_current_task_retirement_and_historical_record_refused(self):
        before = self.before()
        src = self.source
        rid, record = encode_record(
            REPOSITORY, "101", src.base,
            {"schema_version": 1, "branch_ref": src.approved.branch_ref,
             "contract_id": src.registered.contract_id,
             "disposition": {"kind": "cancelled"}})
        src.fixture.store().publish_issue(
            "101", [record], record_update=(src.registered.record_id, rid))
        with self.assertRaisesRegex(MainError, "V2_MAIN_WORKTREE_RECONCILIATION_REQUIRED"):
            self.one()
        self.preserved(before)
        self.no_worktree()

    def test_current_ref_drift_during_grant_refuses_before_git(self):
        before = self.before()
        src = self.source
        def mutate(req):
            rid, record = encode_record(
                REPOSITORY, "101", src.base,
                {"schema_version": 1, "branch_ref": src.approved.branch_ref,
                 "contract_id": src.registered.contract_id,
                 "disposition": {"kind": "cancelled"}})
            src.fixture.store().publish_issue(
                "101", [record], record_update=(src.registered.record_id, rid))
            return True
        with self.assertRaisesRegex(MainError, "V2_MAIN_WORKTREE_RECONCILIATION_REQUIRED"):
            self.one(hosts=lambda lease: {101: self.make_host(
                lease, authorize=mutate)})
        self.preserved(before)
        self.no_worktree()

    def test_partial_host_map_is_denied_not_v1_fallback(self):
        second_approved = self.source.approved_issue(202)
        second_registered = self.source.publish_first(second_approved)
        self.plan["tasks"].append(self.choice(second_registered))
        before = self.before()
        with gate.hold_verified(self.source.project, self.source.home,
                                self.source.package) as lease:
            with self.assertRaisesRegex(MainError, "V2_MAIN_PARTIAL_HOST_SELECTION"):
                self.main({101: self.make_host(lease)})
        self.preserved(before)
        self.no_worktree(101)
        self.no_worktree(202)

    def test_duplicate_main_owner_cannot_concurrently_admit(self):
        before = self.before()
        def operation(runner, main, lease):
            self.assertIs(main._main_owner, runner)
            other = self.main({101: self.make_host(lease)})
            with self.assertRaisesRegex(MainError, "MAIN_ALREADY_ACTIVE"):
                with service.MainSupervisor(
                        other, admission_only=True, attachment_lease=lease):
                    pass
            return runner.admit_worktree(101)
        result = self.with_owner(operation)
        self.assertEqual("CREATED", result["worktree_state"])
        self.preserved(before)

    def test_no_nested_attachment_hold_and_owner_lock_valid_at_grant(self):
        before = self.before()
        calls = []
        original = gate.hold_verified
        def observed_hold(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)
        def checked(req):
            # Single preexisting owned Attach and Main held during every grant.
            main_lock = service._host_repo_lock(REPOSITORY)
            self.assertTrue(main_lock.is_file())
            return True
        with patch.object(gate, "hold_verified", side_effect=observed_hold):
            result = self.one(hosts=lambda lease: {
                101: self.make_host(lease, authorize=checked)})
        self.assertEqual("CREATED", result["worktree_state"])
        self.assertEqual(1, len(calls))
        self.preserved(before)

    def test_registration_collision_preserves_orphan_branch(self):
        before = self.before()
        self.source.attached.git("branch", "task-101", self.source.base)
        with self.assertRaisesRegex(MainError, "V2_MAIN_WORKTREE_RECONCILIATION_REQUIRED"):
            self.one()
        self.assertEqual(self.source.base, self.source.attached.git(
            "rev-parse", "refs/heads/task-101"))
        self.no_worktree()
        self.preserved(before)

    def test_product_dirty_staged_untracked_and_v1_ref_unchanged(self):
        src = self.source
        (src.project / "README.md").write_text("staged\n")
        src.attached.git("add", "README.md")
        (src.project / "README.md").write_text("dirty\n")
        (src.project / "owner.txt").write_text("untouched\n")
        src.fixture.set_ref("refs/agentcore/metadata", src.fixture.genesis)
        v1_before = src.fixture.observe(
            "refs/agentcore/metadata", __import__("time").monotonic()+2)
        before = self.before()
        res = self.one()
        self.assertEqual("CREATED", res["worktree_state"])
        self.preserved(before)
        self.assertEqual("untouched\n", (src.project / "owner.txt").read_text())
        self.assertEqual(v1_before, src.fixture.observe(
            "refs/agentcore/metadata", __import__("time").monotonic()+2))

    def test_post_create_current_ref_drift_leaves_worktree_for_reconciliation(self):
        src = self.source
        before = self.before()
        count = [0]
        def move_on_late_grant(req):
            count[0] += 1
            if count[0] == 3:
                rid, record = encode_record(
                    REPOSITORY, "101", src.base,
                    {"schema_version": 1, "branch_ref": src.approved.branch_ref,
                     "contract_id": src.registered.contract_id,
                     "disposition": {"kind": "cancelled"}})
                src.fixture.store().publish_issue(
                    "101", [record], record_update=(src.registered.record_id, rid))
            return True
        with self.assertRaisesRegex(MainError, "V2_MAIN_WORKTREE_RECONCILIATION_REQUIRED"):
            self.one(hosts=lambda lease: {101: self.make_host(
                lease, authorize=move_on_late_grant)})
        self.assertGreaterEqual(count[0], 3)
        self.assertTrue((src.project / ".worktrees" / "101").exists())
        self.assertEqual("refs/heads/task-101", src.attached.git(
            "-C", str(src.project / ".worktrees" / "101"), "symbolic-ref", "HEAD"))
        self.preserved(before)

    def test_cli_has_no_worktree_mode_even_with_worktree_host(self):
        before = self.before()
        config = self.source.project.parent / "r5a1-selection.json"
        config.write_text(json.dumps(self.plan))
        self.addCleanup(lambda: config.unlink(missing_ok=True))
        from contextlib import redirect_stdout
        from io import StringIO
        with gate.hold_verified(self.source.project, self.source.home,
                                self.source.package) as lease:
            host = service.MainV2WorktreeCapabilities(
                self.read_issue, {101: self.make_host(lease)})
            out = StringIO()
            with redirect_stdout(out):
                status = service.main(["--config", str(config), "dispatch"], host=host)
            self.assertEqual(2, status)
            self.assertEqual("V2_MAIN_WORKTREE_HOST_ONLY",
                             json.loads(out.getvalue())["error_code"])
        self.preserved(before)
        self.no_worktree()

    def test_expired_lease_is_not_reusable(self):
        before = self.before()
        with gate.hold_verified(self.source.project, self.source.home,
                                self.source.package) as lease:
            main = self.main({101: self.make_host(lease)})
        with self.assertRaisesRegex(MainError, "V2_MAIN_WORKTREE_OWNER_REQUIRED"):
            main.admit_v2_worktree(101)
        with self.assertRaisesRegex(MainError, "V2_MAIN_ADMISSION_CONFIGURATION_INVALID"):
            service.MainSupervisor(main, admission_only=True)
        self.preserved(before)
        self.no_worktree()

    def test_expired_attachment_lease_blocks_admission_owner_before_git(self):
        before = self.before()
        with gate.hold_verified(self.source.project, self.source.home,
                                self.source.package) as lease:
            main = self.main({101: self.make_host(lease)})
        with self.assertRaisesRegex(MainError, "MAIN_ATTACHMENT_UNVERIFIED"):
            with service.MainSupervisor(
                    main, attachment_lease=lease, admission_only=True):
                self.fail("expired AttachmentLease must not acquire Main")
        self.preserved(before)
        self.no_worktree()

    def test_main_observe_lane_remains_blocked_without_new_permissions(self):
        before = self.before()
        with gate.hold_verified(self.source.project, self.source.home,
                                self.source.package) as lease:
            main = self.main({101: self.make_host(lease)})
            with service.MainSupervisor(main, observe_only=True) as reader:
                cycle = reader.cycle(dispatch=False)
                self.assertEqual("observe", cycle["mode"])
                self.assertEqual("blocked", cycle["tasks"][0]["status"])
                self.assertEqual("V2_MAIN_BLOCKED_LIVE",
                                 cycle["tasks"][0]["reason"])
                with self.assertRaisesRegex(MainError, "MAIN_OBSERVE_ONLY"):
                    reader.cycle(dispatch=True)
        self.preserved(before)
        self.no_worktree()

    def test_exact_current_issue_ref_absent_refuses_before_git(self):
        before = self.before()
        src = self.source
        existing = src.fixture.observe(issue_ref("101"), __import__("time").monotonic()+2)
        src.fixture.git("update-ref", "--no-deref", "-d",
                        issue_ref("101"), existing.commit)
        with self.assertRaisesRegex(MainError, "V2_MAIN_WORKTREE_RECONCILIATION_REQUIRED"):
            self.one()
        self.preserved(before)
        self.no_worktree()

    def test_post_create_live_issue_drift_retains_actual_git_branch(self):
        src = self.source
        before = self.before()
        count = [0]
        def mutate_late_issue(req):
            count[0] += 1
            if count[0] == 3:
                self.facts[101]["title"] = "external Issue changed"
            return True
        with self.assertRaisesRegex(MainError, "V2_MAIN_WORKTREE_RECONCILIATION_REQUIRED"):
            self.one(hosts=lambda lease: {101: self.make_host(
                lease, authorize=mutate_late_issue)})
        self.assertGreaterEqual(count[0], 3)
        worktree = src.project / ".worktrees" / "101"
        self.assertTrue(worktree.is_dir())
        self.assertEqual(src.base, src.attached.git(
            "-C", str(worktree), "rev-parse", "HEAD"))
        self.preserved(before)

    def test_post_allocation_live_issue_drift_is_reconciliation_not_preflight(self):
        # Deliberately mutate the live Issue only AFTER #337 ensure() returns
        # CREATED, to exercise the Main's final external-Issue recheck rather
        # than #337's pre/post _authority checks inside the Git Issue lock.
        from tools import kagari_vnext_main_v2_worktree as bridge
        before = self.before()
        original = bridge.V2WorktreeAllocator.ensure
        mutated = []
        def after_allocation(allocator, **kwargs):
            allocated = original(allocator, **kwargs)
            self.assertEqual("CREATED", allocated.status)
            mutated.append(True)
            self.facts[101]["body"] = "external edit after Git CREATE"
            return allocated
        with patch.object(bridge.V2WorktreeAllocator, "ensure", after_allocation):
            with self.assertRaisesRegex(
                    MainError, "V2_MAIN_WORKTREE_RECONCILIATION_REQUIRED"):
                self.one()
        self.assertEqual([True], mutated)
        worktree = self.source.project / ".worktrees" / "101"
        self.assertTrue(worktree.is_dir())
        self.assertEqual("refs/heads/task-101", self.source.attached.git(
            "-C", str(worktree), "symbolic-ref", "HEAD"))
        self.preserved(before)
