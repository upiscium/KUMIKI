"""R5C1B1 disposable bare-Git/registered Worktree + fake authenticated APIs.

No production repository/ref, network, model, Session or installed executable.
The subprocess tests start only this synthetic Python fixture, not KAGARI CLI.
"""
from __future__ import annotations

import base64
from contextlib import ExitStack, contextmanager
import copy
from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from tests import test_kagari_metadata_v2_issue_publisher as issue_fixture
from tests import test_kagari_metadata_v2_pr_checkpoint as comment_fixture
from tests.test_metadata_v2 import Fixture, REPOSITORY
from tools import kagari_vnext_main as main_module
from tools import kagari_vnext_main_service as service
from tools import kagari_vnext_main_v2_initial_checkpoint as coordinator
from tools.kagari_vnext_main_v2_observation import V2ReadOnlyMainHost
from tools.kagari_vnext_main_v2_pr_first_observation import V2PrFirstReadOnlyHost, PrFirstFacts
from tools.kagari_vnext_main_v2_pr_first_publication import ProductPrFacts, ProductPull
from kagari_metadata_v2_bootstrap import BootstrapIdentity, ApprovedIssue
from kagari_metadata_v2_host_binding import RepositoryIdentity, RegisteredIssue
from kagari_metadata_v2_task_admission import CurrentTaskReader, V2Selection, V2_PROFILE
from kagari_metadata_v2_issue_publisher import IssuePublisher, _key as metadata_key
from kagari_metadata_v2_pr_checkpoint import CommentFacts, decode_checkpoint
from kagari_vnext_trusted_host import RecoveryJournal
import kagari_vnext_attachment_gate as gate
from metadata_v2 import issue_ref
from task_collaboration import RepositoryFacts, PullFacts
from task_record import encode_record, encode_contract
from task_view import TaskViewSnapshots, encode_snapshot

PR, PRINCIPAL, BASE = comment_fixture.PR, comment_fixture.PRINCIPAL, comment_fixture.BASE


class _Harness:
    """Reuse #337 Issue fixture and checkpoint fake API methods, never live APIs."""

    def __init__(self, src, *, initialize=True):
        self.src = src
        self.check = unittest.TestCase()
        self.hooks = {}
        self.operation_id = "1" * 32
        self.report = "Initial frozen Task handoff"
        self.allow_metadata = self.allow_comment = True
        self.allow_read = self.legacy_clear = True
        self.metadata_mode = "ok"
        self.view_change = None
        self.lock_home = src.root / "main-owner"
        self.jpath = src.root / "initial-plan"
        self.cpath = src.root / "comment-journal"
        if initialize:
            for path in (self.lock_home, self.jpath, self.cpath):
                path.mkdir(mode=0o700)
            self.worktree = src.project / ".worktrees/101"
            src.attached.git("worktree", "add", "-b", "task-101", str(self.worktree), src.base)
            src.attached.git("-C", str(self.worktree), "commit", "--allow-empty", "-qm",
                             "synthetic R5C1A Product subject")
        else:
            self.worktree = src.project / ".worktrees/101"
        self.subject = coordinator.git(self.worktree, "rev-parse", "HEAD")
        self.tree = coordinator.git(self.worktree, "rev-parse", "HEAD^{tree}")
        self.issues = {101: {"repository": REPOSITORY, "number": 101, "state": "OPEN",
                             "title": src.issue.title, "body": src.issue.body}}
        self.product = ProductPrFacts(
            RepositoryFacts(REPOSITORY, BASE, src.repo_identity.repository_id), PRINCIPAL,
            "PRESENT", self.subject, (ProductPull(
                PR, REPOSITORY, REPOSITORY, "main", "task-101", self.subject,
                "open", True, PRINCIPAL, src.issue.title, src.issue.body),), True)
        # Deliberately do not call this TestCase's setUp: it would publish a
        # snapshot before the coordinator. Reuse its API/view/stream fixtures
        # on the SAME IssueWriterTest bare-Git graph instead.
        api = comment_fixture.CheckpointV2Test("runTest")
        self.api = api
        api.fixture, api.identity, api.registered = src.fixture, src.repo_identity, src.registered
        api.record_id, api.contract_id = src.record_id, src.contract_id
        api.comments, api.next_comment, api.post_calls = [], 501, 0
        api.writer_error, api.before_comment_read = False, None
        api.pr = comment_fixture.PullFacts(
            src.repo_identity.repository_id, REPOSITORY, PR, BASE,
            src.registered.branch_ref, self.subject, "open", True, PRINCIPAL)
        api.jpath, api.journal = self.cpath, RecoveryJournal(self.cpath)
        api.stream_lock = threading.RLock()
        self.stats = {"writes": 0, "posts": 0, "views": 0}
        if not initialize:
            remote = json.loads((src.root / "fake-remote.json").read_text())
            api.comments = [CommentFacts(**c) for c in remote["comments"]]
            api.next_comment = remote["next_comment"]
            self.stats = remote["stats"]
        self.plan = {
            "schema_version": 1, "repository": REPOSITORY,
            "project_root": str(src.project), "runtime_home": str(src.home / "never-runtime"),
            "tasks": [{"issue": 101, "branch_ref": src.registered.branch_ref,
                       "base_revision": src.base, "contract_id": src.contract_id, "port": 34291}],
        }
        self.save_remote()

    def fire(self, name):
        callback = self.hooks.pop(name, None)
        if callback:
            callback()

    def save_remote(self):
        (self.src.root / "fake-remote.json").write_text(json.dumps({
            "comments": [asdict(c) for c in self.api.comments],
            "next_comment": self.api.next_comment, "stats": self.stats}))

    def read_issue(self, number):
        result = copy.deepcopy(self.issues[number])
        self.fire("issue")
        return result

    def read_product(self, request):
        result = self.product
        self.fire("product")
        return result

    def read_facts(self, request):
        pulls = tuple(PullFacts(p.number, p.repository, p.head_repository,
                               p.base_ref, p.head_ref, p.head_oid, p.state,
                               p.draft, p.title, p.body) for p in self.product.pulls)
        result = PrFirstFacts(self.product.repository, self.product.principal,
                              self.product.remote_head, pulls, tuple(self.api.comments),
                              self.product.complete)
        self.fire("facts")
        return result

    def read_view(self, request):
        self.stats["views"] += 1
        self.save_remote()
        view = self.api.view(request.metadata_tip, subject=self.subject, remote_head=self.subject)
        view["authority"]["base_revision"] = self.src.base
        view["git"]["tree"] = self.tree
        view["git"]["worktrees"] = [{
            "path_b64": base64.b64encode(bytes(self.worktree)).decode("ascii"),
            "head": self.subject, "branch_ref": self.src.registered.branch_ref}]
        if self.view_change:
            self.view_change(view)
        self.fire("view")
        return view

    def metadata_grant(self, approval):
        self.fire("metadata_grant")
        return self.allow_metadata

    def write_metadata(self, request, bundle, deadline):
        saved = RecoveryJournal(self.jpath).read(coordinator._key(
            self.src.repo_identity.repository_id, 101))
        self.check.assertEqual(saved["current"]["metadata_tip"], request.expected_issue)
        self.check.assertEqual(saved["current"]["archive_tip"], request.expected_archive)
        receipt = RecoveryJournal(self.src.jpath).read(metadata_key(
            self.src.repo_identity.repository_id, 101, request.expected_issue))
        self.check.assertEqual(bundle.commit, receipt["candidate"])
        self.check.assertEqual([saved["snapshot_id"]], receipt["object_ids"])
        self.stats["writes"] += 1
        self.save_remote()
        if self.metadata_mode == "before":
            raise TimeoutError("synthetic missing ACK, not applied")
        result = self.src.fixture.writer(request, bundle, deadline)
        self.fire("metadata_effect")
        if self.metadata_mode == "after":
            raise TimeoutError("synthetic missing ACK, applied")
        return result

    def comment_grant(self, *_):
        self.fire("comment_grant")
        return self.allow_comment

    def post(self, pr, body, deadline):
        self.stats["posts"] += 1
        self.check.assertEqual(1, len(list(self.cpath.iterdir())))
        try:
            return self.api.post(pr, body, deadline)
        finally:
            self.fire("comment_effect")
            self.save_remote()

    def exact_comment(self, *args):
        result = self.api.observe_comment(*args)
        self.fire("exact")
        return result

    def host(self, lease):
        src = self.src
        current = CurrentTaskReader(src.repo_identity, src.registered, src.issue,
                                    reader=src.fixture.reader, observer=src.fixture.observe)
        readonly = V2ReadOnlyMainHost(
            V2Selection(V2_PROFILE, src.repo_identity.repository_id, REPOSITORY, 101,
                        lease._fingerprint), current, lease,
            lambda _: self.legacy_clear, lambda _: self.allow_read)
        self.api.reader = self.api.bind(comment_reader=self.exact_comment)
        proof = V2PrFirstReadOnlyHost(readonly, self.api.reader, BASE, self.read_facts)
        metadata = IssuePublisher(
            BootstrapIdentity(src.repo_identity.repository_id, REPOSITORY, lease._fingerprint),
            src.repo_identity, src.registered, src.issue, lease=lease,
            reader=src.fixture.reader, observer=src.fixture.observe,
            journal=RecoveryJournal(src.jpath), authorize=self.metadata_grant,
            writer=self.write_metadata)
        checkpoint = self.api.publisher_for(grant=self.comment_grant, post=self.post)
        return coordinator.V2InitialCheckpointHost(
            proof, metadata, checkpoint, self.read_product, self.read_view,
            RecoveryJournal(self.jpath), self.operation_id, self.report)

    def main(self, lease, host=None):
        return service.build_main(copy.deepcopy(self.plan), host=service.MainV2InitialCheckpointCapabilities(
            self.read_issue, {101: host or self.host(lease)}))

    def run(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(service, "_host_state_home", return_value=self.lock_home))
            for obj, name in ((main_module, "start_root_session"), (main_module, "opencode_request"),
                              (main_module.RepositoryMain, "dispatch"),
                              (main_module.RepositoryMain, "_spec"),
                              (main_module.MetadataStore, "fetch_tip"),
                              (TaskViewSnapshots, "capture")):
                mock = stack.enter_context(patch.object(obj, name, side_effect=AssertionError("forbidden lane")))
                stack.callback(mock.assert_not_called)
            lease = stack.enter_context(gate.hold_verified(self.src.project, self.src.home, self.src.package))
            self.lease = lease
            main = self.main(lease)
            self.owner = stack.enter_context(service.MainSupervisor(
                main, attachment_lease=lease, publication_only=True))
            return self.owner.publish_initial_checkpoint(101)

    def saved_plan(self):
        return RecoveryJournal(self.jpath).read(coordinator._key(self.src.repo_identity.repository_id, 101))

    def journal_bytes(self):
        return {str(path): path.read_bytes() for directory in
                (self.jpath, self.src.jpath, self.cpath) for path in directory.iterdir()}

    def export(self):
        src = self.src
        context = {name: str(getattr(src, name)) for name in ("project", "home", "package", "root", "jpath")}
        context.update(base=src.base, first=src.first, record_id=src.record_id,
                       contract_id=src.contract_id, issue=asdict(src.issue),
                       registered=asdict(src.registered), repo_identity=asdict(src.repo_identity))
        path = src.root / "child-context.json"
        path.write_text(json.dumps(context))
        self.save_remote()
        return path


def _retry(path):
    """Fresh interpreter reconstructs authenticated *synthetic* Host adapters."""
    data = json.loads(Path(path).read_text())
    for name in ("project", "home", "package", "root", "jpath"):
        data[name] = Path(data[name])
    data["issue"] = ApprovedIssue(**data["issue"])
    data["registered"] = RegisteredIssue(**data["registered"])
    data["repo_identity"] = RepositoryIdentity(**data["repo_identity"])
    fixture = object.__new__(Fixture)
    fixture.root, fixture.bare = data["root"], data["root"] / "remote.git"
    fixture.genesis = data["repo_identity"].genesis
    fixture.calls, fixture.reservations = [], set()
    src = SimpleNamespace(**data, fixture=fixture)
    harness = _Harness(src, initialize=False)
    # A retry must not consult a new view even though the current tip moved.
    harness.read_view = lambda *_: (_ for _ in ()).throw(AssertionError("fresh snapshot forbidden"))
    print(json.dumps(harness.run(), sort_keys=True))


class InitialCheckpointTest(unittest.TestCase):
    def setUp(self):
        src = issue_fixture.IssueWriterTest("runTest")
        self.addCleanup(src.doCleanups)
        src.setUp()
        self.src, self.h = src, _Harness(src)

    def refused(self, state="UNCHANGED"):
        with self.assertRaises(coordinator.InitialCheckpointRefused) as caught:
            self.h.run()
        self.assertEqual(state, caught.exception.state)
        return caught.exception.code

    def assert_no_effect(self):
        self.assertEqual(0, self.h.stats["writes"])
        self.assertEqual(0, self.h.stats["posts"])
        self.assertEqual(self.src.first, self.src.observe(issue_ref("101")))

    def child_retry(self):
        context = self.h.export()
        code = ("from tests.test_kagari_vnext_main_v2_initial_checkpoint import _retry; "
                "import sys; _retry(sys.argv[1])")
        child = subprocess.run([sys.executable, "-c", code, str(context)],
                               cwd=Path(__file__).resolve().parents[1], capture_output=True,
                               text=True, timeout=180, check=True)
        return json.loads(child.stdout)

    def test_private_helper_cannot_bypass_main_owner(self):
        # P1 regression: an internal coordinator must reject fabricated
        # owner checks before any durable intent or external effect.
        with ExitStack() as stack:
            lease = stack.enter_context(gate.hold_verified(
                self.src.project, self.src.home, self.src.package))
            host = self.h.host(lease)
            with self.assertRaises(coordinator.InitialCheckpointRefused) as caught:
                host._publish_owned(
                    project=self.src.project, repository=REPOSITORY,
                    issue=101, branch_ref=self.src.registered.branch_ref,
                    base_revision=self.src.base,
                    contract_id=self.src.contract_id,
                    read_issue=self.h.read_issue,
                    check_main_owner=lambda: None, main_owner=object())
            self.assertEqual("R5C1B1_OWNER_REQUIRED", caught.exception.code)
        self.assertEqual({}, self.h.journal_bytes())
        self.assert_no_effect()

    def test_reentrant_supervisor_exit_is_blocked_during_effects(self):
        # P2 regression: callbacks must not release the same-thread flock
        # while metadata/comment effects remain in flight.
        observed = []
        def attempt_shutdown():
            with self.assertRaises(service.MainError) as caught:
                self.h.owner.__exit__(None, None, None)
            self.assertEqual("R5C1B1_PUBLICATION_ACTIVE", str(caught.exception))
            self.assertTrue(self.h.owner._entered)
            self.h.owner._assert_owned(self.h.owner.main)
            observed.append(True)
        self.h.hooks["metadata_grant"] = attempt_shutdown
        self.h.hooks["comment_effect"] = attempt_shutdown
        result = self.h.run()
        self.assertEqual([True, True], observed)
        self.assertEqual("CONFIRMED", result["initial_checkpoint"])
        self.assertEqual({"writes": 1, "posts": 1, "views": 1}, self.h.stats)

    def test_reentrant_other_issue_cannot_borrow_active_publication(self):
        # Regression: a valid Main owner and positive active counter do not
        # authorize a different Issue/Host from a reentrant Host callback.
        checked = []
        def attempt():
            owner = self.h.owner
            main = owner.main
            active = owner._initial_publish_issue
            host = main.v2_initial_checkpoint_for_issue[101]
            self.assertIs(owner._initial_publish_host, host)
            self.assertEqual(active, 101)
            owner._initial_publish_issue = 102
            try:
                with self.assertRaises(coordinator.InitialCheckpointRefused) as caught:
                    host._publish_owned(
                        project=self.src.project, repository=REPOSITORY,
                        issue=101, branch_ref=self.src.registered.branch_ref,
                        base_revision=self.src.base,
                        contract_id=self.src.contract_id,
                        read_issue=self.h.read_issue,
                        check_main_owner=lambda: None,
                        main_owner=owner)
                self.assertEqual("R5C1B1_OWNER_REQUIRED", caught.exception.code)
            finally:
                owner._initial_publish_issue = active
            checked.append(True)
        self.h.hooks["metadata_grant"] = attempt
        self.h.hooks["comment_effect"] = attempt
        result = self.h.run()
        self.assertEqual([True, True], checked)
        self.assertEqual("CONFIRMED", result["initial_checkpoint"])
        self.assertEqual({"writes": 1, "posts": 1, "views": 1}, self.h.stats)

    def test_publication_identity_cleared_after_refusal(self):
        def assert_owner_in_callback():
            owner = self.h.owner
            self.assertEqual(1, owner._initial_publish_active)
            self.assertEqual(101, owner._initial_publish_issue)
            self.assertIs(owner._initial_publish_host,
                          owner.main.v2_initial_checkpoint_for_issue[101])
        self.h.hooks["metadata_grant"] = assert_owner_in_callback
        self.h.allow_metadata = False
        self.refused("UNCERTAIN")
        self.assertEqual(0, self.h.owner._initial_publish_active)
        self.assertIsNone(self.h.owner._initial_publish_issue)
        self.assertIsNone(self.h.owner._initial_publish_host)
        self.assert_no_effect()

    def test_same_issue_helper_reentry_cannot_invoke_second_publication(self):
        # P1 regression: even the real owner and exact same issue/host may
        # not create a nested new publication during a Host callback.
        observed = []
        def nested():
            owner = self.h.owner
            host = owner.main.v2_initial_checkpoint_for_issue[101]
            self.assertTrue(owner._initial_publish_entered)
            with self.assertRaises(coordinator.InitialCheckpointRefused) as caught:
                host._publish_owned(
                    project=self.src.project, repository=REPOSITORY,
                    issue=101, branch_ref=self.src.registered.branch_ref,
                    base_revision=self.src.base,
                    contract_id=self.src.contract_id,
                    read_issue=owner.main.read_issue,
                    check_main_owner=lambda: None, main_owner=owner)
            self.assertEqual("R5C1B1_REENTRANT_PUBLICATION", caught.exception.code)
            self.assertEqual(0, self.h.stats["writes"])
            self.assertEqual(0, self.h.stats["posts"])
            observed.append(True)
        self.h.hooks["view"] = nested
        result = self.h.run()
        self.assertEqual([True], observed)
        self.assertEqual("CONFIRMED", result["initial_checkpoint"])
        self.assertEqual({"writes": 1, "posts": 1, "views": 1}, self.h.stats)
        self.assertFalse(self.h.owner._initial_publish_entered)

    def test_interrupt_during_owner_publication_initialization_releases_flock(self):
        # P2: a signal raised immediately AFTER the first owner field is set
        # must reach finally, permit supervisor exit, and leave no intent.
        original = service.MainSupervisor.__setattr__
        fired = []
        def interrupt(owner, name, value):
            original(owner, name, value)
            if name == "_initial_publish_active" and value == 1 and not fired:
                fired.append(True)
                raise KeyboardInterrupt("synthetic signal during owner initialization")
        with patch.object(service.MainSupervisor, "__setattr__", new=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.h.run()
        self.assertEqual([True], fired)
        self.assertFalse(self.h.owner._entered)
        self.assertEqual(0, self.h.owner._initial_publish_active)
        self.assertFalse(self.h.owner._initial_publish_entered)
        self.assertIsNone(self.h.owner._initial_publish_issue)
        self.assertIsNone(self.h.owner._initial_publish_host)
        self.assertIsNone(self.h.owner._file)
        self.assertEqual({}, self.h.journal_bytes())
        self.assert_no_effect()

    def test_interrupt_during_publication_body_releases_flock(self):
        # An interrupt in an actual Host callback must not strand the Main
        # even when a durable plan may have been written.
        def interrupted():
            raise KeyboardInterrupt("synthetic signal during capture")
        self.h.hooks["view"] = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.h.run()
        self.assertEqual(0, self.h.owner._initial_publish_active)
        self.assertFalse(self.h.owner._initial_publish_entered)
        self.assertIsNone(self.h.owner._file)
        self.assertFalse(self.h.owner._entered)
        self.assertEqual({}, self.h.journal_bytes())
        self.assert_no_effect()

    def test_interrupt_before_finally_flag_clear_releases_stale_owner(self):
        # P2: a single interrupt at the first cleanup assignment, after a
        # completed publication, must not strand the canonical Main lock.
        original = service.MainSupervisor.__setattr__
        fired = []
        def interrupt(owner, name, value):
            if (name == "_initial_publish_active" and value == 0
                    and getattr(owner, "_initial_publish_active", None) == 1
                    and not fired):
                fired.append(True)
                raise KeyboardInterrupt("synthetic signal before flag clear")
            original(owner, name, value)
        with patch.object(service.MainSupervisor, "__setattr__", new=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.h.run()
        self.assertEqual([True], fired)
        self.assertFalse(self.h.owner._entered)
        self.assertIsNone(self.h.owner._file)
        self.assertEqual(0, self.h.owner._initial_publish_active)
        self.assertIsNone(self.h.owner._initial_publish_issue)
        self.assertIsNone(self.h.owner._initial_publish_host)
        self.assertFalse(self.h.owner._initial_publish_entered)
        self.assertEqual({"writes": 1, "posts": 1, "views": 1}, self.h.stats)
        # A subsequent real owner should not inherit a stale flock.
        self.assertEqual("CONFIRMED", self.h.run()["initial_checkpoint"])

    def test_success_repeat_and_exact_canonical_identity_no_execution(self):
        before = (self.src.attached.git("rev-parse", "HEAD"),
                  (self.src.project / ".git/index").read_bytes(),
                  (self.h.worktree / ".git").read_bytes())
        result = self.h.run()
        self.assertEqual("V2_MAIN_BLOCKED_LIVE", result["reason"])
        self.assertEqual("CONFIRMED", result["initial_checkpoint"])
        self.assertEqual("PROVED", result["pr_first"]["proof"])
        for flag in coordinator._FLAGS:
            self.assertIs(result[flag], False)
        self.assertEqual({"writes": 1, "posts": 1, "views": 1}, self.h.stats)
        capsule = decode_checkpoint(self.h.api.comments[0].body)
        self.assertEqual(result["pr_first"]["metadata_commit"], capsule.metadata_commit)
        plan = self.h.saved_plan()
        self.assertEqual(self.src.first, plan["current"]["metadata_tip"])
        raw = base64.b64decode(plan["snapshot_b64"], validate=True)
        view = json.loads(raw)["payload"]["view"]
        self.assertEqual((plan["snapshot_id"], raw), encode_snapshot(
            REPOSITORY, "101", self.h.subject, "explicit-handoff", view))
        journals = self.h.journal_bytes()
        self.assertEqual(result, self.h.run())
        self.assertEqual(journals, self.h.journal_bytes())
        self.assertEqual({"writes": 1, "posts": 1, "views": 1}, self.h.stats)
        self.assertEqual(before, (self.src.attached.git("rev-parse", "HEAD"),
                                (self.src.project / ".git/index").read_bytes(),
                                (self.h.worktree / ".git").read_bytes()))
        self.assertFalse(Path(self.h.plan["runtime_home"]).exists())

    def test_same_intent_cross_interpreter_does_not_recapture_or_repost(self):
        original = self.h.run()
        journals = self.h.journal_bytes()
        self.assertEqual(original, self.child_retry())
        self.assertEqual(journals, self.h.journal_bytes())
        self.assertEqual({"writes": 1, "posts": 1, "views": 1}, json.loads(
            (self.src.root / "fake-remote.json").read_text())["stats"])

    def test_metadata_lost_ack_cross_interpreter_recovers_original_candidate(self):
        self.h.metadata_mode = "after"
        self.refused("UNCERTAIN")
        old = self.h.saved_plan()
        candidate = self.src.observe(issue_ref("101"))
        self.assertNotEqual(self.src.first, candidate)
        result = self.child_retry()
        self.assertEqual(candidate, result["pr_first"]["metadata_commit"])
        self.assertEqual(old["snapshot_id"], result["pr_first"]["snapshot_id"])
        self.assertEqual(old, self.h.saved_plan())
        self.assertEqual({"writes": 1, "posts": 1, "views": 1}, json.loads(
            (self.src.root / "fake-remote.json").read_text())["stats"])

    def test_metadata_missing_ack_without_effect_never_retries_write(self):
        self.h.metadata_mode = "before"
        self.refused("UNCERTAIN")
        journals = self.h.journal_bytes()
        self.h.metadata_mode = "ok"
        self.refused("UNCERTAIN")
        self.assertEqual(journals, self.h.journal_bytes())
        self.assertEqual({"writes": 1, "posts": 0, "views": 1}, self.h.stats)

    def test_comment_lost_ack_after_effect_reconciles_same_comment(self):
        self.h.api.writer_error = "after"
        result = self.h.run()
        journals = self.h.journal_bytes()
        self.assertEqual(result, self.h.run())
        self.assertEqual(journals, self.h.journal_bytes())
        self.assertEqual({"writes": 1, "posts": 1, "views": 1}, self.h.stats)

    def test_comment_missing_ack_without_effect_never_reposts(self):
        self.h.api.writer_error = "before"
        self.refused("UNCERTAIN")
        journals = self.h.journal_bytes()
        self.h.api.writer_error = False
        self.refused("UNCERTAIN")
        self.assertEqual(journals, self.h.journal_bytes())
        self.assertEqual({"writes": 1, "posts": 1, "views": 1}, self.h.stats)

    def test_conflicting_initial_operation_or_report_preserves_original(self):
        self.h.run()
        old = self.h.journal_bytes()
        self.h.operation_id = "2" * 32
        self.assertEqual("R5C1B1_PLAN_CONFLICT", self.refused("UNCERTAIN"))
        self.h.operation_id = "1" * 32
        self.h.report = "different initial intent"
        self.assertEqual("R5C1B1_PLAN_CONFLICT", self.refused("UNCERTAIN"))
        self.assertEqual(old, self.h.journal_bytes())
        self.assertEqual(1, self.h.stats["writes"])

    def test_plan_fsync_unknown_and_missing_metadata_receipt_deferred(self):
        original = RecoveryJournal.create
        def lost_ack(journal, key, value):
            original(journal, key, value)
            if journal.directory == self.h.jpath:
                raise OSError("synthetic fsync ACK unknown")
        with patch.object(RecoveryJournal, "create", new=lost_ack):
            self.refused("UNCERTAIN")
        old = self.h.journal_bytes()
        self.assertEqual("R5C1B1_METADATA_RECEIPT_MISSING", self.refused("UNCERTAIN"))
        self.assertEqual(old, self.h.journal_bytes())
        self.assert_no_effect()

    def test_unreadable_journal_is_not_absence(self):
        key = coordinator._key(self.src.repo_identity.repository_id, 101)
        path = self.h.jpath / key
        path.write_bytes(b"partial")
        path.chmod(0o600)
        self.assertEqual("R5C1B1_PLAN_UNREADABLE", self.refused("UNCERTAIN"))
        self.assertEqual(b"partial", path.read_bytes())
        self.assert_no_effect()

    def test_preflight_wrong_actor_principal_pr_and_unknown_inventory(self):
        original = self.h.product
        attempts = [replace(original, principal=PRINCIPAL + 1),
                    replace(original, principal=True), replace(original, complete=False),
                    replace(original, pulls=()), replace(original, remote_head="f" * 40)]
        for field, value in (("author_id", PRINCIPAL + 1), ("number", PR + 1),
                             ("title", "changed"), ("body", "changed"),
                             ("state", "closed"), ("draft", 1),
                             ("head_repository", "other/fork")):
            attempts.append(replace(original, pulls=(replace(original.pulls[0], **{field: value}),)))
        for invalid in attempts:
            with self.subTest(invalid=invalid):
                self.h.product = invalid
                self.refused()
                self.assert_no_effect()
        self.assertEqual({}, self.h.journal_bytes())

    def test_empty_proof_boolean_cannot_hide_independent_inventory(self):
        comment = CommentFacts(self.src.repo_identity.repository_id, REPOSITORY, PR, 501,
                               PRINCIPAL, "<!-- agentcore-task-checkpoint/v1 -->")
        self.h.api.comments.append(comment)
        original = self.h.read_facts
        self.h.read_facts = lambda req: replace(original(req), comments=())
        self.refused()
        self.assert_no_effect()

    def test_old_initial_foreign_openers_and_out_of_order_inventory_refuse(self):
        comment = CommentFacts(self.src.repo_identity.repository_id, REPOSITORY, PR, 501,
                               PRINCIPAL, "<!-- kagari-v2-initial-checkpoint/v1 -->")
        for items in ((comment,), (replace(comment, author_id=99),),
                      (replace(comment, body="ordinary"), replace(comment, body="discussion")),
                      (replace(comment, comment_id=502, body="a"), replace(comment, body="b"))):
            with self.subTest(items=items):
                self.h.api.comments[:] = items
                self.refused()
                self.assert_no_effect()

    def test_duplicate_canonical_initial_after_success_never_reposts(self):
        self.h.run()
        self.h.api.comments.append(replace(self.h.api.comments[0], comment_id=999))
        self.refused("UNCERTAIN")
        self.assertEqual(1, self.h.stats["posts"])
        self.assertEqual(1, self.h.stats["writes"])

    def test_snapshot_schema_projection_previous_and_journal_bounds(self):
        changes = [lambda v: v.update(extra=True),
                   lambda v: v["authority"].update(metadata_commit="f" * 40),
                   lambda v: v["authority"].update(contract_id="f" * 64),
                   lambda v: v["previous_checkpoint"].update(state="not_requested"),
                   lambda v: v["git"].update(tree="f" * 40),
                   lambda v: v["git"].update(worktrees=[]),
                   lambda v: v["github"]["pull_request"].update(number=PR + 1)]
        for change in changes:
            with self.subTest(change=change):
                self.h.view_change = change
                self.refused()
                self.assert_no_effect()
        self.h.view_change = None
        self.h.api.comments[:] = [CommentFacts(self.src.repo_identity.repository_id,
            REPOSITORY, PR, 500, 99, "ordinary discussion " + "x" * 17000)]
        self.assertEqual("R5C1B1_PLAN_SIZE_DEFERRED", self.refused())
        self.assert_no_effect()

    def test_issue_worktree_owner_lease_and_pr_drift_during_callbacks(self):
        # Fresh original-plan grant is before any Metadata ref write. All
        # changes occur in disposable fixtures; restore between attempts.
        for kind in ("issue", "worktree", "owner", "lease", "pr"):
            with self.subTest(kind=kind):
                def drift():
                    if kind == "issue":
                        self.h.issues[101]["title"] = "changed"
                    elif kind == "worktree":
                        (self.h.worktree / "dirty.txt").write_text("synthetic dirt")
                    elif kind == "owner":
                        self.h.owner.publication_only = False
                    elif kind == "lease":
                        self.h.lease._active = False
                    else:
                        self.h.product = replace(self.h.product, remote_head="f" * 40)
                self.h.hooks["view"] = drift
                self.refused()
                self.assert_no_effect()
                self.h.issues[101]["title"] = self.src.issue.title
                dirty = self.h.worktree / "dirty.txt"
                if dirty.exists():
                    dirty.unlink()  # disposable test-owned file only
                self.h.product = replace(self.h.product, remote_head=self.h.subject)

    def test_metadata_grant_denial_and_drift_leave_no_ref_effect(self):
        self.h.allow_metadata = False
        self.refused("UNCERTAIN")  # durable original plan already exists
        self.assert_no_effect()
        self.assertEqual("R5C1B1_METADATA_RECEIPT_MISSING", self.refused("UNCERTAIN"))

    def test_read_grant_legacy_collision_and_registration_refuse_before_intent(self):
        self.h.allow_read = False
        self.refused()
        self.h.allow_read = True
        self.h.legacy_clear = False
        self.refused()
        self.h.legacy_clear = True
        with patch.object(coordinator, "worktree_registered", side_effect=ValueError("unregistered")):
            self.refused()
        self.assert_no_effect()
        self.assertEqual({}, self.h.journal_bytes())

    def test_changed_current_record_contract_blocks_before_plan(self):
        rid, raw = encode_record(REPOSITORY, "101", self.src.base, {
            "schema_version": 1, "branch_ref": self.src.issue.branch_ref,
            "contract_id": self.src.contract_id, "disposition": {"kind": "cancelled"}})
        self.src.fixture.store().publish_issue("101", [raw], record_update=(self.src.record_id, rid))
        self.refused()
        self.assertEqual({}, self.h.journal_bytes())
        self.assertEqual(0, self.h.stats["writes"])

    def test_changed_contract_during_metadata_grant_cannot_reach_writer(self):
        def change():
            cid, contract = encode_contract(REPOSITORY, "101", self.src.base,
                {"schema_version": 1, "title": self.src.issue.title, "body": "new contract"})
            rid, record = encode_record(REPOSITORY, "101", self.src.base,
                {"schema_version": 1, "branch_ref": self.src.issue.branch_ref, "contract_id": cid})
            self.src.fixture.store().publish_issue(
                "101", [contract, record], record_update=(self.src.record_id, rid))
        self.h.hooks["metadata_grant"] = change
        self.refused("UNCERTAIN")
        self.assertEqual(0, self.h.stats["writes"])
        self.assertEqual(0, self.h.stats["posts"])
        self.assertEqual([], list(self.src.jpath.iterdir()))
        self.assertIsNotNone(self.h.saved_plan())

    def test_expired_coordinator_budget_never_enters_metadata_effect(self):
        clock = [time.monotonic()]
        self.h.hooks["view"] = lambda: clock.__setitem__(0, clock[0] + 91)
        with patch.object(coordinator, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            self.assertEqual("R5C1B1_DEADLINE_EXCEEDED", self.refused())
        self.assert_no_effect()
        self.assertEqual({}, self.h.journal_bytes())

    def test_plan_fsync_drift_leaves_intent_but_no_metadata_effect(self):
        original = RecoveryJournal.create
        def change(journal, key, record):
            original(journal, key, record)
            if journal.directory == self.h.jpath:
                self.h.issues[101]["body"] = "changed during fsync"
        with patch.object(RecoveryJournal, "create", new=change):
            self.refused("UNCERTAIN")
        self.assert_no_effect()
        self.assertIsNotNone(self.h.saved_plan())

    def test_wrong_post_author_cannot_turn_into_positive_proof(self):
        def foreign_author():
            self.h.api.comments[0] = replace(self.h.api.comments[0], author_id=PRINCIPAL + 1)
        self.h.hooks["comment_effect"] = foreign_author
        self.refused("UNCERTAIN")
        journals = self.h.journal_bytes()
        self.refused("UNCERTAIN")
        self.assertEqual(journals, self.h.journal_bytes())
        self.assertEqual({"writes": 1, "posts": 1, "views": 1}, self.h.stats)

    def test_pending_checkpoint_stream_is_explicit_deferred_gate(self):
        @contextmanager
        def pending(*_):
            yield False
        self.h.api.stream_lease = pending
        self.refused("UNCERTAIN")
        self.assertEqual({"writes": 1, "posts": 0, "views": 1}, self.h.stats)
        self.assertIsNotNone(self.h.saved_plan())

    def test_lease_lost_after_metadata_prevents_comment(self):
        self.h.hooks["metadata_effect"] = lambda: setattr(self.h.lease, "_active", False)
        self.refused("UNCERTAIN")
        self.assertEqual({"writes": 1, "posts": 0, "views": 1}, self.h.stats)

    def test_exact_read_drift_prevents_success_even_after_comment_post(self):
        self.h.hooks["exact"] = lambda: self.h.issues[101].update(title="changed during readback")
        self.refused("UNCERTAIN")
        self.assertEqual({"writes": 1, "posts": 1, "views": 1}, self.h.stats)

    def test_drift_after_metadata_effect_blocks_comment_and_preserves_plan(self):
        self.h.hooks["metadata_effect"] = lambda: self.h.issues[101].update(body="changed")
        self.refused("UNCERTAIN")
        old = self.h.journal_bytes()
        self.refused("UNCERTAIN")
        self.assertEqual(old, self.h.journal_bytes())
        self.assertEqual({"writes": 1, "posts": 0, "views": 1}, self.h.stats)

    def test_comment_grant_drift_before_post_and_post_effect_drift(self):
        self.h.hooks["comment_grant"] = lambda: self.h.issues[101].update(body="changed")
        self.refused("UNCERTAIN")
        self.assertEqual(0, self.h.stats["posts"])
        self.h.issues[101]["body"] = self.src.issue.body
        self.h.hooks["comment_effect"] = lambda: self.h.issues[101].update(body="changed again")
        self.refused("UNCERTAIN")
        self.assertEqual(1, self.h.stats["posts"])
        self.assertEqual(1, self.h.stats["writes"])

    def test_current_tip_beyond_original_candidate_never_creates_fresh_initial(self):
        self.h.run()
        original = self.h.journal_bytes()
        _, evidence = self.src.evidence(subject="f" * 40)
        self.src.fixture.store().publish_issue("101", [evidence])
        self.refused("UNCERTAIN")
        self.assertEqual(original, self.h.journal_bytes())
        self.assertEqual({"writes": 1, "posts": 1, "views": 1}, self.h.stats)

    def test_owner_required_strict_maps_no_dispatch_ask_cli_or_v1_fallback(self):
        from tools import kagari_vnext_opencode_control as control
        with gate.hold_verified(self.src.project, self.src.home, self.src.package) as lease:
            host = self.h.host(lease)
            main = self.h.main(lease, host)
            with self.assertRaisesRegex(coordinator.InitialCheckpointRefused, "OWNER_REQUIRED"):
                host.publish(project=self.src.project, repository=REPOSITORY, issue=101,
                             branch_ref=self.src.issue.branch_ref, base_revision=self.src.base,
                             contract_id=self.src.contract_id, read_issue=main.read_issue,
                             main_owner=None)
            with self.assertRaisesRegex(main_module.MainError, "R5C1B1_OWNER_REQUIRED"):
                main.publish_v2_initial_checkpoint(101)
            with self.assertRaisesRegex(main_module.MainError, "V2_MAIN_EXECUTION_NOT_QUALIFIED"):
                main.dispatch(101)
            with self.assertRaises(main_module.MainError):
                service.MainV2InitialCheckpointCapabilities(self.h.read_issue, {202: host})
            for bad_id in ("A" * 32, "1" * 31, "1" * 33, True):
                with self.assertRaises(coordinator.InitialCheckpointRefused):
                    replace(host, operation_id=bad_id)
            extra = copy.deepcopy(self.h.plan)
            extra["tasks"].append({**extra["tasks"][0], "issue": 202, "port": 34292})
            with self.assertRaisesRegex(main_module.MainError, "V2_MAIN_PARTIAL_HOST_SELECTION"):
                service.build_main(extra, host=service.MainV2InitialCheckpointCapabilities(
                    self.h.read_issue, {101: host}))
            with patch.object(service, "load_selection", side_effect=AssertionError("no CLI reads")) as cli:
                self.assertEqual(2, service.main([], host=service.MainV2InitialCheckpointCapabilities(
                    self.h.read_issue, {101: host})))
                cli.assert_not_called()
        with patch.object(control, "reply", side_effect=AssertionError("no Ask")) as ask:
            self.h.run()
            ask.assert_not_called()

    def test_canonical_singleton_and_publication_mode_exclude_other_lanes(self):
        with gate.hold_verified(self.src.project, self.src.home, self.src.package) as lease:
            with patch.object(service, "_host_state_home", return_value=self.h.lock_home):
                main = self.h.main(lease)
                with service.MainSupervisor(main, attachment_lease=lease, publication_only=True) as owner:
                    other = self.h.main(lease)
                    with self.assertRaisesRegex(main_module.MainError, "MAIN_ALREADY_ACTIVE"):
                        with service.MainSupervisor(other, attachment_lease=lease, publication_only=True):
                            self.fail("second singleton owner")
                    with self.assertRaisesRegex(main_module.MainError, "V2_MAIN_EXECUTION_NOT_QUALIFIED"):
                        owner.cycle(dispatch=True)
                    with self.assertRaises(main_module.MainError):
                        owner.admit_worktree(101)
                    with self.assertRaisesRegex(main_module.MainError, "R5C1A_HOST_REQUIRED"):
                        owner.publish_product_pr_first(101)
        self.assert_no_effect()

    def test_wrong_issue_metadata_publisher_binding_is_rejected(self):
        cid, contract = encode_contract(REPOSITORY, "202", self.src.base,
            {"schema_version": 1, "title": "Other", "body": "Other"})
        rid, record = encode_record(REPOSITORY, "202", self.src.base,
            {"schema_version": 1, "branch_ref": "refs/heads/task-202", "contract_id": cid})
        self.src.fixture.store().publish_issue("202", [contract, record], record_update=(None, rid))
        with gate.hold_verified(self.src.project, self.src.home, self.src.package) as lease:
            host = self.h.host(lease)
            other = RegisteredIssue(self.src.repo_identity.repository_id, REPOSITORY, 202,
                                    "refs/heads/task-202", self.src.base, rid, cid)
            approved = ApprovedIssue(other.repository_id, REPOSITORY, 202, other.branch_ref,
                                     self.src.base, "Other", "Other")
            foreign = IssuePublisher(host.metadata.identity, self.src.repo_identity, other, approved,
                lease=lease, reader=self.src.fixture.reader, observer=self.src.fixture.observe,
                journal=RecoveryJournal(self.src.jpath), authorize=lambda _: True,
                writer=self.h.write_metadata)
            with self.assertRaisesRegex(coordinator.InitialCheckpointRefused, "HOST_BINDING_MISMATCH"):
                replace(host, metadata=foreign)
        self.assert_no_effect()


if __name__ == "__main__":
    unittest.main()
