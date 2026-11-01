"""Offline #337 Phase 2B1 Host bootstrap tests; no real GitHub or model."""

from __future__ import annotations

import dataclasses
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "components/agent-core-v4"))

from kagari_metadata_v2_bootstrap import (
    ApprovedIssue, ArchiveCreate, Bootstrap, BootstrapBlocked, BootstrapIdentity,
    BootstrapUncertain, IssueCreate,
)
from kagari_vnext_trusted_host import RecoveryJournal
from metadata_v2 import ARCHIVE_REF, Limits, MetadataV2, RefState, issue_ref
from metadata_v2_archive import archive_issue
from task_record import encode_contract, encode_record
from tests.test_metadata_v2 import Fixture, REPOSITORY, SUBJECT


class MetadataV2BootstrapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="kagari-337-2b1-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = Fixture(self.root)
        self.original_genesis = self.fixture.genesis
        # B1 bootstrap starts with no archive ref. Only the disposable fixture
        # mutates its bare repo setup; never a live product or GitHub remote.
        self.fixture.git("update-ref", "--no-deref", "-d", ARCHIVE_REF,
                         self.original_genesis)
        self.journal_path = self.root / "host-journal"
        self.journal_path.mkdir(mode=0o700)
        self.context = BootstrapIdentity(
            424242, REPOSITORY, "a" * 64, "sha1")
        self.install_allowed = True
        self.issue_allowed = True
        self.archive_calls = []
        self.issue_calls = []
        self.install_grants = []
        self.issue_grants = []

    def approved(self, issue=101, **changes):
        data = dict(repository_id=424242, repository=REPOSITORY, issue=issue,
                    branch_ref=f"refs/heads/task-{issue}", base_revision=SUBJECT,
                    title=f"Issue {issue}", body="approved exact body")
        data.update(changes)
        return ApprovedIssue(**data)

    def journal(self) -> RecoveryJournal:
        # The same private directory is reused after simulated restart.
        return RecoveryJournal(self.journal_path)

    def allow_install(self, request):
        self.install_grants.append(request)
        return self.install_allowed

    def allow_issue(self, request):
        self.issue_grants.append(request)
        return self.issue_allowed

    def archive_writer(self, intent, bundle, deadline):
        self.archive_calls.append(intent)
        self.assertIsInstance(intent, ArchiveCreate)
        self.assertEqual(intent.candidate, bundle.commit)
        self.assertEqual(intent.ref, ARCHIVE_REF)
        self.assertIsNone(intent.expected_old)
        self.assertEqual(intent.identity, self.context)
        self.assertTrue(self.journal().read(intent.intent_key))
        state = self.fixture.observe(ARCHIVE_REF, deadline)
        self.assertTrue(state.direct)
        self.assertIsNone(state.commit)
        self.fixture.install(bundle)
        self.fixture.set_ref(ARCHIVE_REF, bundle.commit)
        self.fixture.genesis = bundle.commit
        return True

    def issue_writer(self, request, bundle, deadline):
        self.issue_calls.append(request)
        stored = self.journal().read(
            self.bootstrap()._issue_key(int(request.task)))
        self.assertIsNotNone(stored)
        self.assertEqual(stored["candidate"], request.candidate)
        self.assertEqual(stored["genesis"], self.fixture.genesis)
        self.assertEqual(request.candidate, bundle.commit)
        return self.fixture.writer(request, bundle, deadline)

    def bootstrap(self, **overrides):
        kwargs = dict(
            reader=self.fixture.reader,
            observer=self.fixture.observe,
            journal=self.journal(),
            authorize_install=self.allow_install,
            create_archive=self.archive_writer,
            authorize_issue=self.allow_issue,
            create_issue=self.issue_writer,
        )
        kwargs.update(overrides)
        return Bootstrap(self.context, **kwargs)

    def test_doctor_is_read_only_absent_and_installed(self):
        b = self.bootstrap()
        self.assertEqual("ABSENT", b.doctor().state)
        self.assertEqual([], self.archive_calls)
        self.assertEqual([], list(self.journal_path.iterdir()))
        installed = b.install()
        self.assertEqual("CREATED", installed.state)
        self.assertEqual(installed.genesis, installed.archive_tip)
        self.assertEqual("READY", self.bootstrap().doctor().state)
        self.assertEqual("READY", self.bootstrap().install().state)
        self.assertEqual(1, len(self.archive_calls))
        self.assertEqual(1, len(list(self.journal_path.iterdir())))

    def test_archive_is_orphan_empty_and_unrelated_genesis_is_not_adopted(self):
        b = self.bootstrap()
        initial = b.install()
        v = MetadataV2(REPOSITORY, initial.genesis,
                       self.fixture.reader, self.fixture.observe).validation()
        node = v.archive(initial.archive_tip)
        self.assertEqual((), node.parents)
        self.assertEqual({}, dict(node.snapshot.files))
        self.assertNotEqual(initial.genesis, self.original_genesis)
        self.assertEqual(initial.genesis, self.fixture.genesis)

    def test_install_denied_missing_capabilities_and_foreign_archive(self):
        self.install_allowed = False
        b = self.bootstrap()
        with self.assertRaisesRegex(BootstrapBlocked, "authority_denied"):
            b.install()
        self.assertFalse(list(self.journal_path.iterdir()))
        self.assertEqual(0, len(self.archive_calls))
        with self.assertRaises(BootstrapBlocked):
            self.bootstrap(authorize_install=None).install()
        self.fixture.set_ref(ARCHIVE_REF, self.original_genesis)
        with self.assertRaisesRegex(BootstrapBlocked, "unowned_archive"):
            b.doctor()
        with self.assertRaisesRegex(BootstrapBlocked, "unowned_archive"):
            b.install()
        self.assertEqual(0, len(self.archive_calls))

    def test_symbolic_or_wrong_direct_archive_ref_refused_without_effect(self):
        for bad in (
            lambda ref, deadline: RefState(ref, self.original_genesis, False),
            lambda ref, deadline: RefState("refs/heads/foreign", None, True),
            lambda ref, deadline: (_ for _ in ()).throw(TimeoutError("unknown")),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(BootstrapBlocked):
                    self.bootstrap(observer=bad).install()
        self.assertFalse(list(self.journal_path.iterdir()))

    def test_two_issue_first_lifecycle_from_same_genesis_and_durable_restart(self):
        b = self.bootstrap()
        genesis = b.install().genesis
        one = b.register(self.approved())
        two = b.register(self.approved(202))
        self.assertEqual("CREATED", one.state)
        self.assertEqual("CREATED", two.state)
        self.assertEqual(2, len(self.issue_calls))
        for issue, result in [(101, one), (202, two)]:
            node = MetadataV2(REPOSITORY, genesis,
                self.fixture.reader, self.fixture.observe).validation().issue(
                    result.issue_commit, str(issue))
            self.assertEqual((genesis,), node.parents)
            self.assertEqual(frozenset({str(issue)}), node.snapshot.tasks)
            self.assertEqual(result.record_id, node.snapshot.pointers[str(issue)])
        self.assertEqual("RESUMED", self.bootstrap().register(self.approved()).state)
        self.assertEqual("RESUMED", self.bootstrap().register(self.approved(202)).state)
        self.assertEqual(2, len(self.issue_calls))
        self.assertEqual("READY", self.bootstrap().doctor().state)
        self.assertEqual(genesis, self.fixture.observe(
            ARCHIVE_REF, time.monotonic() + 2).commit)

    def test_genesis_only_precreated_issue_ref_blocks_first_publication(self):
        genesis = self.bootstrap().install().genesis
        self.fixture.set_ref(issue_ref("101"), genesis)
        with self.assertRaisesRegex(BootstrapBlocked, "existing_issue_ref"):
            self.bootstrap().register(self.approved())
        self.assertEqual(0, len(self.issue_calls))
        self.assertEqual(genesis, self.fixture.observe(
            issue_ref("101"), time.monotonic() + 2).commit)

    def test_wrong_issue_authority_and_changed_approved_snapshot_block(self):
        self.bootstrap().install()
        self.issue_allowed = False
        with self.assertRaisesRegex(BootstrapBlocked, "authority_denied"):
            self.bootstrap().register(self.approved())
        self.assertEqual(0, len(self.issue_calls))
        self.assertEqual(1, len(list(self.journal_path.iterdir())))
        self.issue_allowed = True
        first = self.bootstrap().register(self.approved())
        for approved in (
            self.approved(title="changed requirements"),
            self.approved(body="changed body"),
            self.approved(branch_ref="refs/heads/review-101"),
            self.approved(base_revision="b" * 40),
        ):
            with self.subTest(approved=approved), self.assertRaisesRegex(
                    BootstrapBlocked, "receipt_identity_mismatch"):
                self.bootstrap().register(approved)
        self.assertEqual(first.issue_commit, self.fixture.observe(
            issue_ref("101"), time.monotonic() + 2).commit)
        self.assertEqual(1, len(self.issue_calls))

    def test_foreign_repository_and_attach_drift_are_not_new_lifecycle(self):
        self.bootstrap().install()
        self.bootstrap().register(self.approved())
        with self.assertRaisesRegex(BootstrapBlocked, "identity_mismatch"):
            self.bootstrap().register(
                self.approved(repository_id=424243))
        foreign_context = dataclasses.replace(self.context,
                                             attach_digest="b" * 64)
        with self.assertRaisesRegex(BootstrapBlocked, "receipt_identity_mismatch"):
            Bootstrap(foreign_context, reader=self.fixture.reader,
                observer=self.fixture.observe, journal=self.journal()).doctor()
        self.assertEqual(1, len(self.archive_calls))
        self.assertEqual(1, len(self.issue_calls))

    def test_install_ack_lost_after_apply_and_before_apply_without_retry(self):
        def applied_then_unknown(intent, bundle, deadline):
            self.archive_writer(intent, bundle, deadline)
            raise TimeoutError("ACK lost")
        with self.assertRaises(BootstrapUncertain):
            self.bootstrap(create_archive=applied_then_unknown).install()
        self.assertEqual(1, len(self.archive_calls))
        self.assertEqual("READY", self.bootstrap().doctor().state)
        self.assertEqual("READY", self.bootstrap().install().state)
        self.assertEqual(1, len(self.archive_calls))

        # An independent fresh fixture has no ref effect, but has an attempted
        # intent. Retry remains UNKNOWN rather than inventing a new nonce.
        with tempfile.TemporaryDirectory(prefix="b1-no-ack-") as t:
            f = Fixture(Path(t))
            f.git("update-ref", "--no-deref", "-d", ARCHIVE_REF, f.genesis)
            jpath = Path(t) / "state"
            jpath.mkdir(mode=0o700)
            journal = RecoveryJournal(jpath)
            calls = []
            def fail_before(*args):
                calls.append(args)
                raise TimeoutError("before")
            b = Bootstrap(self.context, reader=f.reader, observer=f.observe,
                          journal=journal, authorize_install=lambda _: True,
                          create_archive=fail_before)
            with self.assertRaises(BootstrapUncertain):
                b.install()
            with self.assertRaisesRegex(BootstrapUncertain, "ref_absent"):
                b.doctor()
            with self.assertRaises(BootstrapUncertain):
                b.install()
            self.assertEqual(1, len(calls))

    def test_issue_ack_lost_after_apply_and_before_apply_without_retry(self):
        self.bootstrap().install()
        def applied_then_unknown(update, bundle, deadline):
            self.issue_writer(update, bundle, deadline)
            raise TimeoutError("ACK lost")
        with self.assertRaises(BootstrapUncertain):
            self.bootstrap(create_issue=applied_then_unknown).register(
                self.approved())
        self.assertEqual(1, len(self.issue_calls))
        self.assertEqual("RESUMED", self.bootstrap().register(self.approved()).state)
        self.assertEqual(1, len(self.issue_calls))

        def absent_ack(*args):
            self.issue_calls.append(args[0])
            return False
        with self.assertRaises(BootstrapUncertain):
            self.bootstrap(create_issue=absent_ack).register(
                self.approved(202))
        with self.assertRaises(BootstrapUncertain):
            self.bootstrap().register(self.approved(202))
        self.assertEqual(2, len(self.issue_calls))
        self.assertIsNone(self.fixture.observe(issue_ref("202"),
            time.monotonic() + 2).commit)

    def test_competing_issue_registration_does_not_publish_twice(self):
        self.bootstrap().install()
        entered = threading.Event()
        release = threading.Event()
        errors = []
        answers = []
        def slow_writer(update, bundle, deadline):
            entered.set()
            if not release.wait(timeout=4):
                raise TimeoutError("test timed out")
            return self.issue_writer(update, bundle, deadline)
        def first_worker():
            try:
                answers.append(self.bootstrap(create_issue=slow_writer).register(
                    self.approved()))
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=first_worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=4))
            with self.assertRaises(BootstrapUncertain):
                self.bootstrap().register(self.approved())
        finally:
            release.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, len(answers))
        self.assertEqual(1, len(self.issue_calls))

    def test_original_issue_receipt_survives_archive_of_another_issue(self):
        genesis = self.bootstrap().install().genesis
        first = self.bootstrap().register(self.approved())
        self.bootstrap().register(self.approved(202))
        archive_issue(self.fixture.store(), "202",
            retention_policy="fixture-not-real-ruleset",
            verify_terminal=lambda *_: True, archive_writer=self.fixture.writer)
        self.assertNotEqual(genesis, self.bootstrap().doctor().archive_tip)
        recovered = self.bootstrap().register(self.approved())
        self.assertEqual("RESUMED", recovered.state)
        self.assertEqual(first.issue_commit, recovered.issue_commit)
        self.assertEqual(2, len(self.issue_calls))

    def test_archive_completed_same_issue_blocks_reopened_registration(self):
        self.bootstrap().install()
        issued = self.bootstrap().register(self.approved())
        store = self.fixture.store()
        archived = archive_issue(store, "101",
            retention_policy="fixture-not-real-ruleset",
            verify_terminal=lambda *_: True, archive_writer=self.fixture.writer)
        self.assertNotEqual(archived, self.fixture.genesis)
        with self.assertRaisesRegex(BootstrapBlocked, "already_archived"):
            self.bootstrap().register(self.approved())
        self.assertEqual(issued.issue_commit, self.fixture.observe(issue_ref("101"),
            time.monotonic() + 2).commit)
        self.assertEqual("READY", self.bootstrap().doctor().state)

    def test_post_ack_invalid_or_moved_archive_ref_is_uncertain(self):
        def fake_ack(*_):
            return True
        with self.assertRaises(BootstrapUncertain):
            self.bootstrap(create_archive=fake_ack).install()
        self.assertFalse(self.fixture.observe(ARCHIVE_REF,
            time.monotonic() + 2).commit)
        with self.assertRaises(BootstrapUncertain):
            self.bootstrap().install()

    def test_resource_bounds_and_malformed_context(self):
        for update in (
            {"git_object_format": "sha256"}, {"repository_id": 0},
            {"repository_id": True}, {"repository": "acme/widgets/x"},
            {"attach_digest": "invalid"},
        ):
            with self.subTest(update=update), self.assertRaises(BootstrapBlocked):
                dataclasses.replace(self.context, **update)
        with self.assertRaises(BootstrapBlocked):
            self.approved(branch_ref="refs/tags/101")
        with self.assertRaises(BootstrapBlocked):
            self.approved(branch_ref="refs/heads/task-202")
        with self.assertRaises(BootstrapBlocked):
            self.approved(title="")
        with self.assertRaises(BootstrapBlocked):
            self.approved(base_revision="bad")
        self.bootstrap().install()
        with self.assertRaises(BootstrapBlocked):
            self.bootstrap(limits=Limits(commits=1)).register(self.approved())
        self.assertEqual(0, len(self.issue_calls))

    def test_no_product_head_index_dirty_or_v1_ref_modification(self):
        project = self.root / "owner-product"
        project.mkdir()
        self.fixture.git("init", "-q", str(project), bare=False)
        tracked = project / "tracked.txt"
        tracked.write_text("initial\n")
        self.fixture.git("-C", str(project), "add", "tracked.txt", bare=False)
        self.fixture.git("-C", str(project), "-c", "user.name=Fixture",
             "-c", "user.email=fixture@invalid", "-c", "commit.gpgsign=false",
             "commit", "-qm", "base", bare=False)
        tracked.write_text("staged\n")
        self.fixture.git("-C", str(project), "add", "tracked.txt", bare=False)
        tracked.write_text("dirty\n")
        (project / "untracked.txt").write_text("owner\n")
        # Existing unrelated v1 and product refs are fixture-only.
        self.fixture.set_ref("refs/agentcore/metadata", self.original_genesis)
        def before():
            return (
                self.fixture.git("-C", str(project), "rev-parse", "HEAD", bare=False),
                self.fixture.git("-C", str(project), "ls-files", "--stage", bare=False),
                self.fixture.git("-C", str(project), "status", "--porcelain=v1",
                                 "-uall", bare=False),
                tracked.read_bytes(), (project / "untracked.txt").read_bytes(),
                self.fixture.observe("refs/agentcore/metadata",
                    time.monotonic() + 2).commit,
            )
        original = before()
        self.bootstrap().install()
        self.bootstrap().register(self.approved())
        self.bootstrap().register(self.approved(202))
        self.assertEqual(original, before())
        self.assertEqual(1, len(self.archive_calls))
        self.assertEqual(2, len(self.issue_calls))


if __name__ == "__main__":
    unittest.main()
