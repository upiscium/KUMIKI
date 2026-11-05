"""#291 R5C0 — disposable, Host-only v2 initial PR/checkpoint proof tests.

No GitHub account/remote, production Git, model, Session, PR publishing or
Nix install is used. #337 bare-Git and #256 Project/Attach are synthetic.
"""
from __future__ import annotations

import base64
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from tests import test_kagari_metadata_v2_task_admission as v2_fixture
from tests.test_metadata_v2 import REPOSITORY
from tools import kagari_vnext_main as main_module
from tools import kagari_vnext_main_service as service
from tools import kagari_vnext_main_v2_pr_first_observation as pr_module
from tools.kagari_vnext_main import MainError
from tools.kagari_vnext_main_v2_observation import V2ReadOnlyMainHost
from tools.kagari_vnext_main_v2_pr_first_observation import (
    V2PrFirstReadOnlyHost, V2PrFirstRefused, PrFirstFacts, decode_initial_capsule)
import kagari_vnext_attachment_gate as gate
from kagari_metadata_v2_host_binding import (
    ReadOnlyMetadataV2Host, ExactLocator, VERSION)
from task_collaboration import RepositoryFacts, PullFacts, CommentFacts
from task_view import encode_snapshot
from metadata_v2 import ARCHIVE_REF, issue_ref
from metadata_v2_archive import archive_issue
from task_record import encode_record
from tools.kagari_vnext_task_binding import ControlError


PR = 51
PRINCIPAL = 4288
BASE_REF = "refs/heads/main"


def view_snapshot(src, subject, *, issue=101, record=None, contract=None,
                  metadata_tip=None, pr_number=PR, boundary="explicit-handoff",
                  previous=None):
    registered = record or src.registered
    metadata_tip = metadata_tip or src.fixture.observe(
        issue_ref(str(issue)), time.monotonic() + 2).commit
    task = str(issue)
    ref = registered.branch_ref
    git = {
        "head": subject, "tree": "b" * 40, "branch_ref": ref,
        "branch_matches_task": True,
        "status": {key: 0 for key in
                   ("index_changes", "worktree_changes", "untracked",
                    "conflicts", "submodule_changes")},
        "worktrees": [], "default_branch": {"state": "not_requested"},
        "remote_head": {"state": "observed", "head_oid": subject},
        "submodule_worktrees": "not_inspected", "staged_gitlinks": 0,
    }
    payload = {
        "schema_version": 1, "repository": REPOSITORY, "task": task,
        "subject": subject, "branch_ref": ref,
        "git": git,
        "authority": {
            "state": "observed", "metadata_commit": metadata_tip,
            "record_id": registered.record_id,
            "contract_id": registered.contract_id,
            "base_revision": registered.base_revision,
            "branch_ref": ref, "disposition": None,
        },
        "evidence": [],
        "github": {"state": "observed", "pull_request": {
            "base_repository": REPOSITORY,
            "head_repository": REPOSITORY, "number": pr_number,
            "state": "open", "draft": True,
            "base_ref": "main", "head_ref": ref[len("refs/heads/"):],
            "head_oid": subject,
        }},
        "previous_checkpoint": previous or {"state": "absent"},
    }
    return encode_snapshot(REPOSITORY, task, subject, boundary, payload)


def capsule_body(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True).encode("ascii")
    digest = hashlib.sha256(pr_module._DOMAIN + raw).hexdigest()
    return (f"<!-- kagari-v2-initial-checkpoint/v1 sha256={digest} -->\n"
            "Task checkpoint (initial):\n"
            "Capsule: " + base64.b64encode(raw).decode("ascii"))


class R5C0PrFirstTests(unittest.TestCase):
    def setUp(self):
        src = v2_fixture.V2TaskAdmissionTest("runTest")
        self.addCleanup(src.doCleanups)
        src.setUp()
        self.src = src
        self.issue = 101
        self.approved = src.approved
        self.facts = {101: self.issue_facts(src.approved)}
        self.plan = {
            "schema_version": 1, "project_root": str(src.project),
            "repository": REPOSITORY,
            "runtime_home": str(src.home / "r5c0-no-runtime"),
            "tasks": [self.selection(src.registered)],
        }
        self.allow_read = True
        self.legacy_clear = True
        self.callback = None
        self.issued = []
        self.product_worktree = None
        self.subject = src.base
        self.metadata_commit = None
        self.snapshot_id = None
        self.fact_reads = []
        self.repo_facts = RepositoryFacts(REPOSITORY, BASE_REF, src.identity.repository_id)
        self.pull = self.pr_facts(src.registered)
        self.comments = ()
        self.remote_head = src.base
        self.complete = True
        self.main_ref_calls = []

    @staticmethod
    def selection(registered):
        return {
            "issue": registered.issue,
            "branch_ref": registered.branch_ref,
            "base_revision": registered.base_revision,
            "contract_id": registered.contract_id,
            "port": 35000 + registered.issue,
        }

    @staticmethod
    def issue_facts(approved):
        return {
            "repository": approved.repository, "number": approved.issue,
            "state": "OPEN", "title": approved.title, "body": approved.body,
        }

    def pr_facts(self, registered, *, number=PR, head=None):
        return PullFacts(
            number, REPOSITORY, REPOSITORY, "main",
            registered.branch_ref[len("refs/heads/"):],
            head or self.subject, "open", True,
            "Implement exact Issue", "R5C0 synthetic")

    def read_issue(self, number):
        self.main_ref_calls.append(number)
        return copy.deepcopy(self.facts[number])

    def read_facts(self, req):
        self.fact_reads.append(req)
        if self.callback is not None:
            return self.callback(req)
        return PrFirstFacts(
            self.repo_facts, PRINCIPAL, self.remote_head,
            (self.pull,) if self.pull is not None else (),
            tuple(self.comments), self.complete)

    def host(self, lease, *, registered=None, approved=None, read_facts=None,
             observer=None, reader=None, expected_base_ref=BASE_REF):
        src = self.src
        registered = registered or src.registered
        approved = approved or src.approved
        rd = V2ReadOnlyMainHost(
            src.selection(registered.issue),
            src.authority(
                registered=registered, approved=approved, observer=observer),
            lease, lambda req: self.legacy_clear,
            lambda req: self.allow_read)
        history = ReadOnlyMetadataV2Host(
            src.identity, registered, reader=reader or src.fixture.reader,
            observer=observer or src.fixture.observe)
        return V2PrFirstReadOnlyHost(
            rd, history, expected_base_ref, read_facts or self.read_facts)

    def setup_published_checkpoint(self):
        src = self.src
        if self.product_worktree is None:
            alloc = src.admit()  # disposable local linked Worktree only
            self.product_worktree = Path(alloc.worktree)
            self.subject = src.attached.git("-C", alloc.worktree, "rev-parse", "HEAD")
        parent = src.fixture.observe(issue_ref("101"), time.monotonic()+2).commit
        self.snapshot_id, raw = view_snapshot(src, self.subject, metadata_tip=parent)
        self.metadata_commit = src.fixture.store().publish_issue("101", [raw])
        self.pull = self.pr_facts(src.registered, head=self.subject)
        self.remote_head = self.subject
        self.comments = (self.comment(),)

    def comment(self, *, payload=None, number=PR, author=PRINCIPAL, cid=701):
        if payload is None:
            payload = {
                "schema_version": 1, "metadata_version": 2,
                "repository_id": self.src.identity.repository_id,
                "repository": REPOSITORY, "issue": 101,
                "branch_ref": self.src.registered.branch_ref,
                "base_ref": BASE_REF,
                "pr_number": number, "principal": author,
                "subject": self.subject,
                "metadata_commit": self.metadata_commit,
                "record_id": self.src.registered.record_id,
                "contract_id": self.src.registered.contract_id,
                "snapshot_id": self.snapshot_id,
                "boundary": "initial", "key": "initial",
                "report": "Initial frozen Task handoff",
            }
        return CommentFacts(
            REPOSITORY, "pr", number, cid, author, capsule_body(payload))

    def main(self, lease, *, extra_hosts=None):
        hosts = extra_hosts or {101: self.host(lease)}
        return service.build_main(
            copy.deepcopy(self.plan),
            host=service.MainV2PrFirstReadOnlyCapabilities(
                self.read_issue, hosts))

    def observe(self, *, cycle=False, hosts=None):
        src = self.src
        with gate.hold_verified(src.project, src.home, src.package) as lease:
            main = self.main(lease, extra_hosts=hosts(lease) if hosts else None)
            with patch.object(
                    main_module, "start_root_session",
                    side_effect=AssertionError("no session allowed")), patch.object(
                    main_module, "opencode_request",
                    side_effect=AssertionError("no prompt allowed")), patch.object(
                    main_module, "isolated_metadata_observation",
                    side_effect=AssertionError("no v1 metadata")):
                if cycle:
                    with service.MainSupervisor(main, observe_only=True) as runner:
                        return runner.cycle(dispatch=False)
                return main.observe(101)

    def state(self):
        src = self.src
        return (
            src.attached.git("rev-parse", "HEAD"),
            src.attached.git("status", "--porcelain=v1", "-uall"),
            (src.project / ".git/index").read_bytes(),
            src.attached.receipt().read_bytes(),
        )

    def preserved(self, old):
        self.assertEqual(old, self.state())
        self.assertFalse((self.src.home / "r5c0-no-runtime").exists())

    def refused(self, code):
        with self.assertRaises(MainError) as raised:
            self.observe()
        self.assertEqual(code, raised.exception.code)

    def test_positive_exact_current_pr_and_initial_checkpoint(self):
        self.setup_published_checkpoint()
        before = self.state()
        result = self.observe()
        self.assertEqual("blocked", result["status"])
        self.assertEqual("V2_MAIN_BLOCKED_LIVE", result["reason"])
        self.assertEqual("PROVED", result["pr_first"]["proof"])
        self.assertEqual(PR, result["pr_first"]["pull_request"])
        self.assertEqual(self.metadata_commit, result["pr_first"]["metadata_commit"])
        self.assertEqual(self.snapshot_id, result["pr_first"]["snapshot_id"])
        for flag in ("task_execution_authorized", "worktree_mutation_authorized",
                     "github_write_authorized", "session_started", "prompt_sent"):
            self.assertIs(result[flag], False)
        self.assertEqual(2, len(self.fact_reads))
        self.preserved(before)

    def test_supervisor_keeps_main_blocked_and_never_dispatches(self):
        self.setup_published_checkpoint()
        result = self.observe(cycle=True)
        self.assertEqual("observe", result["mode"])
        row = result["tasks"][0]
        self.assertEqual("blocked", row["status"])
        self.assertEqual("PROVED", row["observation"]["pr_first"]["proof"])

    def test_positive_metadata_tip_advanced_since_checkpoint(self):
        self.setup_published_checkpoint()
        original = self.metadata_commit
        src = self.src
        from metadata_codec import encode_object
        _, blob = encode_object("evidence", REPOSITORY, "101", self.subject, {
            "schema_version": 1, "kind": "structured",
            "producer": {"name": "fixture"},
            "created_at": "2026-11-05T00:00:00Z", "payload": {"assertion": True}})
        next_tip = src.fixture.store().publish_issue("101", [blob])
        self.assertNotEqual(original, next_tip)
        result = self.observe()
        self.assertEqual("PROVED", result["pr_first"]["proof"])
        self.assertEqual(original, result["pr_first"]["metadata_commit"])

    def test_archived_history_readable_but_archived_current_task_not_admitted(self):
        self.setup_published_checkpoint()
        src = self.src
        archive = archive_issue(
            src.fixture.store(), "101",
            retention_policy="disposable-fixture-only",
            verify_terminal=lambda *_: True, archive_writer=src.fixture.writer)
        current_issue = src.fixture.observe(
            issue_ref("101"), time.monotonic()+2).commit
        src.fixture.git("update-ref", "--no-deref", "-d",
                        issue_ref("101"), current_issue)
        history = ReadOnlyMetadataV2Host(
            src.identity, src.registered, reader=src.fixture.reader,
            observer=src.fixture.observe)
        located = history.read(ExactLocator(
            VERSION, src.identity.repository_id, REPOSITORY, 101,
            self.metadata_commit, "task-view-snapshot", self.snapshot_id, self.subject))
        self.assertEqual("explicit-handoff", located["payload"]["boundary"])
        self.refused("V2_MAIN_CURRENT_TASK_UNAVAILABLE")

    def test_missing_or_duplicate_matching_pr_refuses(self):
        self.setup_published_checkpoint()
        self.pull = None
        self.refused("R5C0_PR_ABSENT_OR_AMBIGUOUS")
        self.pull = self.pr_facts(self.src.registered)
        def double(req):
            return PrFirstFacts(self.repo_facts, PRINCIPAL, self.subject,
                                (self.pull, replace(self.pull, number=79)),
                                tuple(self.comments), True)
        self.callback = double
        self.refused("R5C0_PR_ABSENT_OR_AMBIGUOUS")

    def test_wrong_open_draft_base_head_or_foreign_repo_refuses(self):
        self.setup_published_checkpoint()
        for k, value in [
            ("base_ref", "feature"), ("head_oid", "b"*40),
            ("head_repository", "foreign/repo"), ("base_repository", "other/repo"),
            ("state", "closed"), ("draft", False)
        ]:
            with self.subTest(k=k):
                old = self.pull
                self.pull = replace(self.pull, **{k: value})
                try:
                    self.refused("R5C0_PR_IDENTITY_MISMATCH")
                finally:
                    self.pull = old

    def test_remote_task_head_or_incomplete_listing_refuses(self):
        self.setup_published_checkpoint()
        self.remote_head = "b"*40
        self.refused("R5C0_PR_FACTS_MISMATCH")
        self.remote_head = self.subject
        self.complete = False
        self.refused("R5C0_PR_FACTS_MISMATCH")

    def test_missing_duplicate_foreign_or_v1_checkpoint_refuses(self):
        self.setup_published_checkpoint()
        old = self.comments
        self.comments = ()
        self.refused("R5C0_INITIAL_CHECKPOINT_MISSING_OR_DUPLICATE")
        self.comments = (old[0], replace(old[0], comment_id=702))
        self.refused("R5C0_INITIAL_CHECKPOINT_MISSING_OR_DUPLICATE")
        self.comments = (replace(old[0], author_id=99),)
        self.refused("R5C0_INITIAL_CHECKPOINT_MISSING_OR_DUPLICATE")
        self.comments = (replace(old[0], body=old[0].body.replace(
            "kagari-v2-initial-checkpoint/v1", "agentcore-task-checkpoint/v1")),)
        self.refused("R5C0_CAPSULE_INVALID")
        self.comments = old

    def test_wrong_comment_principal_number_and_binding_refuse(self):
        self.setup_published_checkpoint()
        old = self.comments
        for change in ({"number": 99}, {"repository": "foreign/repo"},
                       {"kind": "issue"}, {"comment_id": 0}):
            with self.subTest(change=change):
                self.comments = (replace(old[0], **change),)
                try:
                    self.refused("R5C0_CHECKPOINT_COMMENT_MISMATCH")
                finally:
                    self.comments = old
        for key, v in (
            ("principal", 992), ("record_id", "1"*64),
            ("contract_id", "2"*64), ("subject", "b"*40),
            ("base_ref", "refs/heads/other"), ("metadata_version", 1),
            ("issue", 202), ("pr_number", 52)
        ):
            with self.subTest(key=key):
                p = decode_initial_capsule(old[0].body)
                p[key] = v
                self.comments = (self.comment(payload=p),)
                try:
                    self.refused("R5C0_CAPSULE_INVALID" if key == "metadata_version"
                                 else "R5C0_CHECKPOINT_BINDING_MISMATCH")
                finally:
                    self.comments = old

    def test_unreachable_wrong_or_missing_metadata_commit_and_snapshot_refuse(self):
        self.setup_published_checkpoint()
        old = self.comments
        for key, val in (
            ("metadata_commit", "b"*40),
            ("snapshot_id", "c"*64)
        ):
            p = decode_initial_capsule(old[0].body)
            p[key] = val
            self.comments = (self.comment(payload=p),)
            try:
                self.refused("R5C0_METADATA_HISTORY_UNAVAILABLE")
            finally:
                self.comments = old

    def test_snapshot_incorrect_initial_boundary_blocks(self):
        self.setup_published_checkpoint()
        src = self.src
        parent = src.fixture.observe(issue_ref("101"), time.monotonic()+2).commit
        sid, raw = view_snapshot(src, self.subject, metadata_tip=parent,
                                 boundary="turn-end")
        wrong_commit = src.fixture.store().publish_issue("101", [raw])
        p = decode_initial_capsule(self.comments[0].body)
        p["snapshot_id"], p["metadata_commit"] = sid, wrong_commit
        self.comments = (self.comment(payload=p),)
        self.refused("R5C0_SNAPSHOT_BINDING_MISMATCH")

    def test_product_dirty_or_worktree_registration_missing_refuses(self):
        self.setup_published_checkpoint()
        p = self.product_worktree
        (p / "dirty.txt").write_text("not pristine\n")
        self.refused("R5C0_PRODUCT_NOT_PRISTINE")
        (p / "dirty.txt").unlink()
        # Inject a Git registration mismatch inside the observer rather than
        # altering the #256 Project receipt while the Attach hold is active.
        with patch.object(pr_module, "worktree_registered",
                          side_effect=ControlError(
                              "WORKTREE_MISMATCH", "foreign branch")):
            self.refused("R5C0_WORKTREE_UNVERIFIED")

    def test_main_issue_drift_during_pr_callback_refuses(self):
        self.setup_published_checkpoint()
        def drift(req):
            self.facts[101]["body"] = "changed scope"
            return PrFirstFacts(self.repo_facts, PRINCIPAL, self.subject,
                                (self.pull,), self.comments, True)
        self.callback = drift
        self.refused("V2_MAIN_ISSUE_SNAPSHOT_DRIFT")

    def test_current_ref_lost_mid_pr_read_refuses(self):
        self.setup_published_checkpoint()
        src = self.src
        def lose_ref(req):
            observed = src.fixture.observe(
                issue_ref("101"), time.monotonic()+2).commit
            src.fixture.git("update-ref", "--no-deref", "-d",
                            issue_ref("101"), observed)
            return PrFirstFacts(self.repo_facts, PRINCIPAL, self.subject,
                                (self.pull,), self.comments, True)
        self.callback = lose_ref
        # Historical checkpoint is no longer reachable when its only active
        # Issue ref disappears. Refusal precedes final current-Task recheck.
        self.refused("R5C0_METADATA_HISTORY_UNAVAILABLE")

    def test_pr_facts_changed_during_second_host_read_refuses(self):
        self.setup_published_checkpoint()
        counter = [0]
        def move(req):
            counter[0] += 1
            return PrFirstFacts(self.repo_facts, PRINCIPAL,
                                self.subject if counter[0] == 1 else "b"*40,
                                (self.pull,), self.comments, True)
        self.callback = move
        self.refused("R5C0_PR_FACTS_MISMATCH")

    def test_unavailable_host_callback_refuses_no_empty_checkpoint(self):
        self.setup_published_checkpoint()
        def down(req):
            raise OSError("GitHub not reachable")
        self.callback = down
        self.refused("R5C0_PR_FACTS_UNAVAILABLE")

    def test_capsule_rejects_duplicate_json_key_and_noncanonical_digest(self):
        self.setup_published_checkpoint()
        p = decode_initial_capsule(self.comments[0].body)
        content = json.dumps(p, sort_keys=True, separators=(",", ":")).encode()
        # Insert duplicate JSON field, while resigning the digest to prove
        # duplicate field refusal is not merely checksum mismatch.
        changed = content[:-1] + b',"issue":101}'
        digest = hashlib.sha256(pr_module._DOMAIN + changed).hexdigest()
        bad = (f"<!-- kagari-v2-initial-checkpoint/v1 sha256={digest} -->\n"
               "Task checkpoint (initial):\nCapsule: " +
               base64.b64encode(changed).decode())
        with self.assertRaisesRegex(V2PrFirstRefused, "DUPLICATE"):
            decode_initial_capsule(bad)
        with self.assertRaises(V2PrFirstRefused):
            decode_initial_capsule(self.comments[0].body + "garbage")

    def test_nonliteral_host_selection_refuses_v1_fallback(self):
        with gate.hold_verified(self.src.project, self.src.home,
                                self.src.package) as lease:
            main = service.build_main(copy.deepcopy(self.plan))
            with self.assertRaisesRegex(MainError, "R5C0_HOST_INVALID"):
                service.MainV2PrFirstReadOnlyCapabilities(self.read_issue, {})
            with self.assertRaisesRegex(MainError, "MAIN_HOST_CAPABILITIES_INVALID"):
                service.build_main(copy.deepcopy(self.plan), host={"proof": True})
            with self.assertRaisesRegex(MainError, "R5C0_HOST_BINDING_MISMATCH"):
                main_module.RepositoryMain(
                    main.store, approved=dict(main.approved), read_issue=self.read_issue,
                    runtime_home=main.runtime_home,
                    port_for_issue=main.port_for_issue,
                    v2_pr_first_for_issue={101: self.host(lease)})

    def test_two_issue_host_map_is_all_or_nothing(self):
        src = self.src
        second = src.approved_issue(202)
        registered = src.publish_first(second)
        self.plan["tasks"].append(self.selection(registered))
        with gate.hold_verified(src.project, src.home, src.package) as lease:
            with self.assertRaisesRegex(MainError, "V2_MAIN_PARTIAL_HOST_SELECTION"):
                self.main(lease)

    def test_no_effects_or_v1_task_reader(self):
        self.setup_published_checkpoint()
        before = self.state()
        from tools import kagari_vnext_task_binding as task
        from tools import kagari_vnext_opencode_control as upstream
        with patch.object(task, "verify_task_record",
                          side_effect=AssertionError("v1 Record forbidden")), patch.object(
                task, "resolve",
                side_effect=AssertionError("v1 binding forbidden")), patch.object(
                upstream, "reply",
                side_effect=AssertionError("Ask reply forbidden")):
            result = self.observe()
        self.assertEqual("PROVED", result["pr_first"]["proof"])
        self.preserved(before)

    def test_attachment_revocation_in_final_callback_blocks_proof(self):
        self.setup_published_checkpoint()
        count = [0]
        expired = [False]
        original_verify = gate.AttachmentLease.verify
        def revoke(req):
            count[0] += 1
            if count[0] == 2:
                expired[0] = True
            return PrFirstFacts(self.repo_facts, PRINCIPAL, self.subject,
                                (self.pull,), self.comments, True)
        def check(lease, *, project=None):
            if expired[0]:
                raise gate.GateBlocked("GATE_ATTACHMENT_CHANGED_DURING_USE")
            return original_verify(lease, project=project)
        self.callback = revoke
        with patch.object(gate.AttachmentLease, "verify", new=check):
            self.refused("V2_MAIN_ATTACHMENT_UNAVAILABLE")
        self.assertTrue(expired[0])

    def test_malformed_checkpoint_recursion_and_unicode_fail_typed(self):
        self.setup_published_checkpoint()
        original = self.comments
        raw = b'{"report":' + b'[' * 1100 + b'0' + b']' * 1100 + b'}'
        digest = hashlib.sha256(pr_module._DOMAIN + raw).hexdigest()
        nested = (
            f"<!-- kagari-v2-initial-checkpoint/v1 sha256={digest} -->\n"
            "Task checkpoint (initial):\n"
            "Capsule: " + base64.b64encode(raw).decode("ascii"))
        for bad in (nested, original[0].body + "\ud800"):
            with self.subTest(kind="malformed-body"):
                self.comments = (replace(original[0], body=bad),)
                self.refused("R5C0_CAPSULE_INVALID")
        payload = decode_initial_capsule(original[0].body)
        payload["report"] = "\ud800"
        self.comments = (self.comment(payload=payload),)
        self.refused("R5C0_CAPSULE_INVALID")
        self.comments = (replace(original[0], body=None),)
        self.refused("R5C0_PR_FACTS_MISMATCH")
        self.comments = original

    def test_real_disposable_submodule_ignore_all_does_not_hide_dirt(self):
        """Real Git submodule with ignore=all; R5C0 must check nested state."""
        src = self.src
        self.product_worktree = Path(src.admit().worktree)
        wt = self.product_worktree
        subsource = src.project.parent / "source-submodule"
        subsource.mkdir()
        src.attached.git("init", "-q", "-b", "main", str(subsource))
        src.attached.git("-C", str(subsource), "config", "user.name", "fixture")
        src.attached.git("-C", str(subsource), "config", "user.email", "fixture@example.invalid")
        (subsource / "hello.txt").write_text("initial\n")
        src.attached.git("-C", str(subsource), "add", "hello.txt")
        src.attached.git("-C", str(subsource), "commit", "-qm", "initial")
        src.attached.git(
            "-C", str(wt), "-c", "protocol.file.allow=always",
            "submodule", "add", "-q", str(subsource), "deps/sample")
        src.attached.git("-C", str(wt), "add", ".gitmodules", "deps/sample")
        src.attached.git("-C", str(wt), "commit", "-qm", "add disposable submodule")
        self.subject = src.attached.git("-C", str(wt), "rev-parse", "HEAD")
        self.setup_published_checkpoint()
        nested = wt / "deps" / "sample"
        src.attached.git("-C", str(wt), "config", "submodule.deps/sample.ignore", "all")
        src.attached.git("-C", str(nested), "config", "user.name", "fixture")
        src.attached.git("-C", str(nested), "config", "user.email", "fixture@example.invalid")
        original = (nested / "hello.txt").read_text()

        def check_dirty(kind):
            # Git default status may be clean due to ignore=all even though
            # the exact nested Worktree is visibly changed.
            ignored = pr_module.git(wt, "status", "--porcelain=v1", "-uall")
            explicit = pr_module.git(
                wt, "status", "--porcelain=v1", "-uall",
                "--ignore-submodules=none")
            self.assertEqual("", ignored, kind)
            self.assertTrue(explicit, kind)
            self.refused("R5C0_PRODUCT_NOT_PRISTINE")

        (nested / "hello.txt").write_text("modified tracked\n")
        check_dirty("tracked")
        (nested / "hello.txt").write_text(original)
        (nested / "untracked.txt").write_text("new file\n")
        check_dirty("untracked")
        (nested / "untracked.txt").unlink()
        (nested / "hello.txt").write_text("new nested commit\n")
        src.attached.git("-C", str(nested), "add", "hello.txt")
        src.attached.git("-C", str(nested), "commit", "-qm", "nested head moved")
        check_dirty("changed-gitlink")
