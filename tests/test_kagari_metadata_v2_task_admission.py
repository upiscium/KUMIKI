"""#337 Phase 2B2: disposable v2 current authority and real Git Worktree tests.

No real GitHub, production Project, installed KAGARI, network provider, Main,
Task, Session or model. The existing #256 Attach fixture exits 97 if invoked.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "components/agent-core-v4"))

import kagari_vnext_attachment_gate as gate
from kagari_metadata_v2_task_admission import (
    AdmissionRefused, CurrentTaskReader, V2Selection, V2WorktreeAllocator,
    V2_PROFILE, admit_worktree,
)
from kagari_metadata_v2_bootstrap import ApprovedIssue
from kagari_metadata_v2_host_binding import RegisteredIssue, RepositoryIdentity
from metadata_v2 import ARCHIVE_REF, MetadataV2, RefState, issue_ref
from metadata_v2_archive import archive_issue
from task_record import encode_contract, encode_record
from tests import test_kagari_vnext_attach as attach_fixture
from tests.test_metadata_v2 import Fixture, REPOSITORY


RID = 424242


class V2TaskAdmissionTest(unittest.TestCase):
    def setUp(self):
        attached = attach_fixture.AttachTests("runTest")
        attached.setUp()
        self.addCleanup(attached.doCleanups)
        self.attached = attached
        self.project, self.home, self.package = (
            attached.project, attached.home, attached.package)
        self.assertEqual(attached.operate("install")["status"], "ATTACHED")
        self.base = attached.head
        tmp = tempfile.TemporaryDirectory(prefix="337-v2-current-")
        self.addCleanup(tmp.cleanup)
        self.fixture = Fixture(Path(tmp.name))
        with gate.hold_verified(self.project, self.home, self.package) as lease:
            self.digest = lease._fingerprint
        self.identity = RepositoryIdentity(RID, REPOSITORY, self.fixture.genesis, "sha1")
        self.approved = self.approved_issue()
        self.registered = self.publish_first(self.approved)
        self.grants, self.legacy_proofs = [], []
        self.authorized, self.legacy_clear = True, True

    def approved_issue(self, number=101, **changes):
        args = dict(repository_id=RID, repository=REPOSITORY, issue=number,
                    branch_ref=f"refs/heads/task-{number}", base_revision=self.base,
                    title=f"Issue {number}", body="frozen approved requirements")
        args.update(changes)
        return ApprovedIssue(**args)

    def publish_first(self, approved):
        task = str(approved.issue)
        cid, contract = encode_contract(
            REPOSITORY, task, approved.base_revision,
            {"schema_version": 1, "title": approved.title, "body": approved.body})
        rid, record = encode_record(
            REPOSITORY, task, approved.base_revision,
            {"schema_version": 1, "branch_ref": approved.branch_ref, "contract_id": cid})
        commit = self.fixture.store().publish_issue(
            task, [contract, record], record_update=(None, rid))
        self.assertEqual(commit, self.fixture.observe(
            issue_ref(task), time.monotonic() + 2).commit)
        return RegisteredIssue(
            RID, REPOSITORY, approved.issue, approved.branch_ref,
            approved.base_revision, rid, cid)

    def selection(self, issue=101):
        return V2Selection(V2_PROFILE, RID, REPOSITORY, issue, self.digest)

    def authority(self, *, registered=None, approved=None, observer=None):
        return CurrentTaskReader(
            self.identity, registered or self.registered,
            approved or self.approved, reader=self.fixture.reader,
            observer=observer or self.fixture.observe)

    def authorize(self, request):
        self.grants.append(request)
        return self.authorized

    def legacy(self, request):
        self.legacy_proofs.append(request)
        return self.legacy_clear

    def admit(self, *, registered=None, approved=None, selection=None,
              reader=None, observer=None, authorize=None, legacy=None,
              package=None):
        return admit_worktree(
            project=self.project, state_home=self.home,
            package_root=package or self.package,
            identity=self.identity, registered=registered or self.registered,
            approved=approved or self.approved,
            selection=selection or self.selection(),
            reader=reader or self.fixture.reader,
            observer=observer or self.fixture.observe,
            authorize=authorize or self.authorize,
            legacy_issue_absent=legacy or self.legacy)

    def assert_no_worktree(self, issue=101):
        self.assertFalse((self.project / ".worktrees" / str(issue)).exists())

    def test_current_reader_requires_current_pointer_and_never_uses_history(self):
        evidence = self.authority().observe()
        self.assertEqual(self.registered.record_id, evidence.record_id)
        self.assertEqual(self.registered.contract_id, evidence.contract_id)
        self.assertEqual(self.fixture.observe(
            issue_ref("101"), time.monotonic()+2).commit, evidence.metadata_tip)
        self.assertEqual(self.fixture.genesis, evidence.archive_tip)
        self.assertFalse(hasattr(self.authority(), "publish"))

    def test_opt_in_create_resume_uses_existing_git_allocator(self):
        before_head = self.attached.git("rev-parse", "HEAD")
        before_index = (self.project / ".git/index").read_bytes()
        before_status = self.attached.git("status", "--porcelain=v1", "-uall")
        before_receipt = self.attached.receipt().read_bytes()
        first = self.admit()
        self.assertEqual("CREATED", first.status)
        self.assertEqual(self.base, first.head)
        self.assertEqual(str(self.project / ".worktrees" / "101"), first.worktree)
        self.assertEqual("RESUMED", self.admit().status)
        self.assertEqual(first.branch_ref, self.attached.git(
            "-C", first.worktree, "symbolic-ref", "HEAD"))
        self.assertEqual(before_head, self.attached.git("rev-parse", "HEAD"))
        self.assertEqual(before_index, (self.project / ".git/index").read_bytes())
        self.assertEqual(before_status, self.attached.git("status", "--porcelain=v1", "-uall"))
        self.assertEqual(before_receipt, self.attached.receipt().read_bytes())
        self.assertGreaterEqual(len(self.grants), 6)

    def test_two_issues_get_distinct_registered_worktrees(self):
        first = self.admit()
        second_approved = self.approved_issue(202)
        second_record = self.publish_first(second_approved)
        second = self.admit(
            approved=second_approved, registered=second_record,
            selection=self.selection(202))
        self.assertNotEqual(first.worktree, second.worktree)
        self.assertEqual("RESUMED", self.admit().status)
        self.assertEqual("RESUMED", self.admit(
            approved=second_approved, registered=second_record,
            selection=self.selection(202)).status)
        self.assertEqual(2, len(list((self.project / ".worktrees").iterdir())))

    def test_v1_and_product_staged_dirty_untracked_state_unchanged(self):
        (self.project / "README.md").write_text("staged\n")
        self.attached.git("add", "README.md")
        (self.project / "README.md").write_text("dirty\n")
        (self.project / "owner-unique.txt").write_text("do not touch\n")
        index = (self.project / ".git/index").read_bytes()
        before = (self.attached.git("status", "--porcelain=v1", "-uall"),
                  self.attached.git("rev-parse", "HEAD"),
                  (self.project / "README.md").read_bytes())
        self.fixture.set_ref("refs/agentcore/metadata", self.fixture.genesis)
        old_v1 = self.fixture.observe("refs/agentcore/metadata", time.monotonic()+2)
        self.admit()
        self.assertEqual(index, (self.project / ".git/index").read_bytes())
        self.assertEqual(before[0], self.attached.git("status", "--porcelain=v1", "-uall"))
        self.assertEqual(before[1], self.attached.git("rev-parse", "HEAD"))
        self.assertEqual(before[2], (self.project / "README.md").read_bytes())
        self.assertEqual(old_v1, self.fixture.observe(
            "refs/agentcore/metadata", time.monotonic()+2))
        self.assertEqual("do not touch\n",
                         (self.project / "owner-unique.txt").read_text())

    def test_negative_missing_or_bad_explicit_v2_selection(self):
        with self.assertRaises(AdmissionRefused):
            V2Selection("native-v4-linked-worktree", RID, REPOSITORY, 101, self.digest)
        with self.assertRaises(AdmissionRefused):
            V2Selection(V2_PROFILE, RID, REPOSITORY, 101, "wrong")
        with self.assertRaisesRegex(AdmissionRefused, "SELECTION"):
            self.admit(selection=dataclasses.replace(self.selection(), issue=202))
        self.assert_no_worktree()

    def test_denied_explicit_operation_or_existing_v1_assignment(self):
        self.authorized = False
        with self.assertRaisesRegex(AdmissionRefused, "HUMAN"):
            self.admit()
        self.assert_no_worktree()
        self.authorized = True
        self.legacy_clear = False
        with self.assertRaisesRegex(AdmissionRefused, "V1_ISSUE"):
            self.admit()
        self.assert_no_worktree()
        self.assertTrue(self.legacy_proofs)

    def test_missing_invalid_attach_or_package_refuses_before_worktree(self):
        self.attached.operate("uninstall")
        with self.assertRaises(AdmissionRefused):
            self.admit()
        self.assert_no_worktree()
        self.attached.operate("install")
        with self.assertRaisesRegex(AdmissionRefused, "INSTALL_RECEIPT"):
            self.admit(selection=dataclasses.replace(
                self.selection(), attach_digest="b" * 64))
        self.assert_no_worktree()

    def test_foreign_registration_contract_base_branch_and_requirements_refuse(self):
        changes = (
            ("registered", {"repository_id": RID+1}),
            ("registered", {"record_id": "f" * 64}),
            ("registered", {"contract_id": "e" * 64}),
            ("registered", {"branch_ref": "refs/heads/foreign-101"}),
            ("registered", {"base_revision": "f" * 40}),
            ("approved", {"title": "unapproved requirement"}),
            ("approved", {"body": "changed"}),
            ("approved", {"base_revision": "f" * 40}),
        )
        for target, change in changes:
            with self.subTest(change=change), self.assertRaises(AdmissionRefused):
                if target == "registered":
                    self.admit(registered=dataclasses.replace(self.registered, **change))
                else:
                    self.admit(approved=dataclasses.replace(self.approved, **change))
            self.assert_no_worktree()

    def test_cancelled_current_record_refuses_even_if_old_record_is_readable(self):
        prior = self.fixture.observe(issue_ref("101"), time.monotonic()+2).commit
        next_id, next_record = encode_record(
            REPOSITORY, "101", self.base,
            {"schema_version":1,"branch_ref":self.approved.branch_ref,
             "contract_id":self.registered.contract_id,
             "disposition":{"kind":"cancelled"}})
        new_tip = self.fixture.store().publish_issue(
            "101", [next_record],
            record_update=(self.registered.record_id, next_id))
        self.assertNotEqual(prior, new_tip)
        self.assertEqual("task-record", self.fixture.store(writer=False).read_object(
            prior, "task-record", self.registered.record_id,
            task="101", subject=self.base)["kind"])
        with self.assertRaises(AdmissionRefused):
            self.admit()
        self.assert_no_worktree()

    def test_current_supersession_and_reauthorization_do_not_adopt_old_grant(self):
        for disposition in (
            {"kind":"cancelled"},
            {"kind":"superseded","replacement":{"repository":REPOSITORY,"task":"202"}},
        ):
            with self.subTest(disposition=disposition):
                temp = tempfile.TemporaryDirectory(prefix="v2-super-")
                self.addCleanup(temp.cleanup)
                # Test each change independently by publishing first record in
                # a separate disposable remote.
                f = Fixture(Path(temp.name))
                cid, contract = encode_contract(
                    REPOSITORY,"101",self.base,
                    {"schema_version":1,"title":self.approved.title,"body":self.approved.body})
                rid, record = encode_record(
                    REPOSITORY,"101",self.base,
                    {"schema_version":1,"branch_ref":self.approved.branch_ref,
                     "contract_id":cid})
                f.store().publish_issue("101", [contract,record],record_update=(None,rid))
                next_id, next_record = encode_record(
                    REPOSITORY,"101",self.base,
                    {"schema_version":1,"branch_ref":self.approved.branch_ref,
                     "contract_id":cid,"disposition":disposition})
                f.store().publish_issue("101",[next_record],record_update=(rid,next_id))
                old = dataclasses.replace(self.registered, record_id=rid)
                identity = dataclasses.replace(self.identity, genesis=f.genesis)
                auth = CurrentTaskReader(identity,old,self.approved,
                    reader=f.reader,observer=f.observe)
                with self.assertRaises(AdmissionRefused):
                    auth.observe()

    def test_issue_already_archived_refuses(self):
        archive_issue(self.fixture.store(),"101",
            retention_policy="synthetic-only",
            verify_terminal=lambda *_: True,archive_writer=self.fixture.writer)
        with self.assertRaisesRegex(AdmissionRefused,"ARCHIVED"):
            self.admit()
        self.assert_no_worktree()

    def test_missing_symbolic_and_foreign_issue_ref_refuse(self):
        ref=issue_ref("101")
        expected=self.fixture.observe(ref,time.monotonic()+2).commit
        self.fixture.git("update-ref","--no-deref","-d",ref,expected)
        with self.assertRaises(AdmissionRefused):
            self.admit()
        self.assert_no_worktree()
        self.fixture.set_ref(ref,expected)
        def symbolic(name, deadline):
            if name==ref:
                return RefState(ref, expected, False)
            return self.fixture.observe(name,deadline)
        with self.assertRaises(AdmissionRefused):
            self.admit(observer=symbolic)
        self.assert_no_worktree()

    def test_changed_ref_during_read_refused(self):
        ref=issue_ref("101")
        count=[0]
        def race(name, deadline):
            state=self.fixture.observe(name,deadline)
            if name==ref:
                count[0]+=1
                if count[0] >= 2:
                    return RefState(ref,self.fixture.genesis,True)
            return state
        with self.assertRaises(AdmissionRefused):
            self.admit(observer=race)
        self.assert_no_worktree()

    def test_stale_current_record_after_initial_check_preserves_partial_worktree(self):
        # The existing #286 allocator checks authority inside its lock, both
        # before and after Git CREATE. On post-create drift it retains partial
        # effects for explicit reconciliation rather than deleting owner data.
        count=[0]
        def authorize(req):
            count[0]+=1
            if count[0]==3:
                next_id,next_record=encode_record(
                    REPOSITORY,"101",self.base,
                    {"schema_version":1,"branch_ref":self.approved.branch_ref,
                     "contract_id":self.registered.contract_id,
                     "disposition":{"kind":"cancelled"}})
                self.fixture.store().publish_issue(
                    "101",[next_record],
                    record_update=(self.registered.record_id,next_id))
            return True
        with self.assertRaises(AdmissionRefused):
            self.admit(authorize=authorize)
        self.assertGreaterEqual(count[0],3)
        self.assertTrue((self.project / ".worktrees" / "101").exists())
        self.assertEqual("refs/heads/task-101", self.attached.git(
            "-C",str(self.project / ".worktrees" / "101"),"symbolic-ref","HEAD"))

    def test_unregistered_orphan_product_branch_is_not_reset(self):
        self.attached.git("branch","task-101",self.base)
        with self.assertRaises(AdmissionRefused):
            self.admit()
        self.assert_no_worktree()
        self.assertEqual(self.base,self.attached.git(
            "rev-parse","refs/heads/task-101"))

    def test_task_binding_v1_does_not_accept_v2_as_v1_record(self):
        # A v2-created Worktree does not make the old CLI or Session v2-aware.
        allocation=self.admit()
        from kagari_vnext_task_binding import verify_task_record
        from kagari_vnext_opencode_control import ControlError
        import argparse
        # Set a real local remote containing ONLY v2 metadata refs. A missing
        # remote would yield a generic unavailable error, not prove that the
        # v1 binding refuses a valid v2-only current Task Record.
        self.attached.git("remote","add","origin",str(self.fixture.bare))
        with self.assertRaises(ControlError) as caught:
            verify_task_record(argparse.Namespace(
                project_root=self.project, worktree=Path(allocation.worktree),
                repository=REPOSITORY, remote="origin", issue=101,
                branch_ref=self.approved.branch_ref,
                base_revision=self.base))
        self.assertEqual(caught.exception.code,"TASK_RECORD_ABSENT")
        self.assertEqual("RESUMED",self.admit().status)


if __name__=="__main__":
    unittest.main()
