"""#337 Phase 2A: offline, source-only, exact v2 Host reads.

Temporary bare-Git remotes here model syntax/ancestry, NOT GitHub CAS, ref
protection, production grants, Worktree registration, or safe deletion.
"""
from __future__ import annotations

import dataclasses
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "components/agent-core-v4"))

from kagari_metadata_v2_host_binding import (
    VERSION, ExactLocator, MetadataV2HostRefused, ReadOnlyMetadataV2Host,
    RegisteredIssue, RepositoryIdentity,
)
from metadata_v2 import ARCHIVE_REF, Limits, RefState, issue_ref, InvalidMetadata
from metadata_v2_archive import archive_issue
from task_record import encode_contract, encode_record
import metadata_codec as codec
from tests.test_metadata_v2 import Fixture, REPOSITORY, SUBJECT


REPO_ID = 424242


class ReadOnlyHostBindingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="metadata-v2-host2a-")
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(Path(self.temp.name))
        self.builder = self.fixture.store()
        self.identity = RepositoryIdentity(REPO_ID, REPOSITORY, self.fixture.genesis, "sha1")
        self.contract_id, contract = encode_contract(
            REPOSITORY, "101", SUBJECT,
            {"schema_version": 1, "title": "Feature 101", "body": "bounded scope"})
        self.record_id, record = encode_record(
            REPOSITORY, "101", SUBJECT,
            {"schema_version": 1, "branch_ref": "refs/heads/task-101",
             "contract_id": self.contract_id})
        self.first = self.builder.publish_issue(
            "101", [contract, record], record_update=(None, self.record_id))
        self.issue = RegisteredIssue(
            REPO_ID, REPOSITORY, 101, "refs/heads/task-101", SUBJECT,
            self.record_id, self.contract_id,
        )
        self.host = self.bind()

    def bind(self, *, issue=None, identity=None, observer=None, reader=None,
             limits=None) -> ReadOnlyMetadataV2Host:
        return ReadOnlyMetadataV2Host(
            identity if identity is not None else self.identity,
            issue if issue is not None else self.issue,
            reader=reader if reader is not None else self.fixture.reader,
            observer=observer if observer is not None else self.fixture.observe,
            limits=limits if limits is not None else Limits(),
        )

    def locator(self, kind="task-record", object_id=None, commit=None, subject=SUBJECT,
                **replace) -> ExactLocator:
        data = {
            "version": VERSION, "repository_id": REPO_ID, "repository": REPOSITORY,
            "issue": 101, "metadata_commit": commit or self.first,
            "kind": kind, "object_id": object_id or self.record_id,
            "subject": subject,
        }
        data.update(replace)
        return ExactLocator(**data)

    def test_read_pinned_record_and_contract_does_not_offer_writer(self):
        calls = len(self.fixture.calls)
        record = self.host.read(self.locator())
        contract = self.host.read(self.locator("contract", self.contract_id))
        self.assertEqual(record["payload"]["contract_id"], self.contract_id)
        self.assertEqual(contract["payload"]["title"], "Feature 101")
        self.assertEqual(calls, len(self.fixture.calls))
        self.assertFalse(hasattr(self.host, "publish"))
        self.assertFalse(hasattr(self.host, "delete"))
        with self.assertRaisesRegex(InvalidMetadata, "writer required"):
            self.host._store.publish_issue("101", [])
        self.assertEqual(calls, len(self.fixture.calls))

    def test_evidence_subject_and_old_commit_never_substitute_newer_tip(self):
        object_id, raw = codec.encode_object(
            "evidence", REPOSITORY, "101", "b" * 40,
            {"schema_version": 1, "kind": "fixture", "producer": {"name": "test"},
             "created_at": "2026-11-01T00:00:00Z", "payload": {"accepted": True}},
        )
        updated = self.builder.publish_issue("101", [raw])
        self.assertEqual("b" * 40, self.host.read(self.locator(
            "evidence", object_id, updated, "b" * 40))["subject"])
        with self.assertRaises(MetadataV2HostRefused):
            self.host.read(self.locator("evidence", object_id, self.first, "b" * 40))
        with self.assertRaises(MetadataV2HostRefused):
            self.host.read(self.locator("evidence", object_id, updated, "a" * 40))
        self.assertEqual(self.contract_id, self.host.read(
            self.locator())["payload"]["contract_id"])

    def test_historical_record_binding_is_not_rebased_to_new_record_pointer(self):
        next_id, next_record = encode_record(
            REPOSITORY, "101", SUBJECT,
            {"schema_version": 1, "branch_ref": "refs/heads/task-101",
             "contract_id": self.contract_id, "disposition": {"kind": "cancelled"}},
        )
        newer = self.builder.publish_issue(
            "101", [next_record], record_update=(self.record_id, next_id))
        self.assertEqual("task-record", self.host.read(self.locator())["kind"])
        with self.assertRaisesRegex(MetadataV2HostRefused, "record_checkpoint"):
            self.host.read(self.locator(commit=newer))
        newer_issue = dataclasses.replace(self.issue, record_id=next_id)
        newer_host = self.bind(issue=newer_issue)
        self.assertEqual("cancelled", newer_host.read(
            self.locator(object_id=next_id, commit=newer))["payload"]["disposition"]["kind"])

    def test_two_issues_archive_original_commit_after_fixture_delete_and_fresh_clone(self):
        contract_id, contract = encode_contract(
            REPOSITORY, "202", SUBJECT,
            {"schema_version": 1, "title": "Other", "body": "independent"})
        record_id, record = encode_record(
            REPOSITORY, "202", SUBJECT,
            {"schema_version": 1, "branch_ref": "refs/heads/task-202", "contract_id": contract_id})
        second = self.builder.publish_issue(
            "202", [contract, record], record_update=(None, record_id))
        first_archive = archive_issue(self.builder, "101",
            retention_policy="disposable-hook-only", verify_terminal=lambda *_: True,
            archive_writer=self.fixture.writer)
        self.fixture.git("update-ref", "--no-deref", "-d", issue_ref("101"), self.first)
        next_archive = archive_issue(self.builder, "202",
            retention_policy="disposable-hook-only", verify_terminal=lambda *_: True,
            archive_writer=self.fixture.writer)
        self.assertNotEqual(first_archive, next_archive)
        clone_path = self.fixture.root / "fresh.git"
        self.fixture.git("clone", "--bare", "--no-hardlinks",
                         str(self.fixture.bare), str(clone_path), bare=False)
        clone = object.__new__(Fixture)
        clone.root, clone.bare = self.fixture.root, clone_path
        replacement = self.bind(reader=clone.reader, observer=clone.observe)
        self.assertIsNone(clone.observe(issue_ref("101"), time.monotonic() + 2).commit)
        self.assertEqual(self.record_id, codec.encode_object(
            "task-record", REPOSITORY, "101", SUBJECT,
            replacement.read(self.locator())["payload"])[0])
        with self.assertRaises(MetadataV2HostRefused):
            replacement.read(self.locator(metadata_commit=second))
        self.assertEqual(self.contract_id, replacement.read(
            self.locator(metadata_commit=first_archive))["payload"]["contract_id"])

    def test_wrong_repo_issue_version_fields_and_subject_fail_closed(self):
        cases = [
            {"repository_id": REPO_ID + 1}, {"repository": "other/widgets"},
            {"issue": 202}, {"subject": "b" * 40},
            {"object_id": "f" * 64}, {"kind": "evidence"},
            {"metadata_commit": "f" * 40},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(MetadataV2HostRefused):
                self.host.read(self.locator(**overrides))
        for overrides in ({"version": 1}, {"version": True}, {"version": "2"},
                          {"subject": None}, {"object_id": "ABC"},
                          {"metadata_commit": "0" * 64}, {"repository_id": True},
                          {"issue": 0}, {"kind": "unknown"}):
            with self.subTest(invalid=overrides), self.assertRaises(MetadataV2HostRefused):
                self.locator(**overrides)
        fields = dataclasses.asdict(self.locator())
        self.assertEqual(self.locator(), ExactLocator.from_mapping(fields))
        with self.assertRaises(MetadataV2HostRefused):
            ExactLocator.from_mapping({**fields, "latest": True})
        with self.assertRaises(MetadataV2HostRefused):
            ExactLocator.from_mapping({k: v for k, v in fields.items() if k != "metadata_commit"})
        with self.assertRaises(MetadataV2HostRefused):
            self.host.read(fields)

    def test_binding_wrong_record_contract_branch_revision_and_identity_refuse(self):
        for change in (
            {"record_id": "f" * 64}, {"contract_id": "f" * 64},
            {"branch_ref": "refs/heads/other-101"}, {"base_revision": "b" * 40},
        ):
            with self.subTest(change=change), self.assertRaises(MetadataV2HostRefused):
                self.bind(issue=dataclasses.replace(self.issue, **change)).read(self.locator())
        with self.assertRaisesRegex(MetadataV2HostRefused, "repository_mismatch"):
            self.bind(issue=dataclasses.replace(self.issue, repository_id=REPO_ID + 1))
        with self.assertRaisesRegex(MetadataV2HostRefused, "repository_mismatch"):
            self.bind(identity=dataclasses.replace(self.identity, repository="other/widgets"))
        with self.assertRaises(MetadataV2HostRefused):
            self.bind(issue=dataclasses.replace(self.issue, branch_ref="refs/tags/task-101"))

    def test_missing_wrong_or_symbolic_ref_is_refused(self):
        archive = self.fixture.genesis
        self.assertEqual(archive, self.fixture.observe(ARCHIVE_REF, time.monotonic() + 2).commit)
        variants = [
            lambda ref, deadline: RefState(ref, None, True) if ref == ARCHIVE_REF
            else self.fixture.observe(ref, deadline),
            lambda ref, deadline: RefState(ref, archive, False) if ref == ARCHIVE_REF
            else self.fixture.observe(ref, deadline),
            lambda ref, deadline: RefState("refs/heads/not-the-archive", archive, True)
            if ref == ARCHIVE_REF else self.fixture.observe(ref, deadline),
            lambda ref, deadline: (_ for _ in ()).throw(TimeoutError("ambiguous")),
        ]
        for observer in variants:
            with self.subTest(observer=observer), self.assertRaises(MetadataV2HostRefused):
                self.bind(observer=observer).read(self.locator())
        product = issue_ref("101")
        self.fixture.set_ref("refs/heads/product-target", self.first)
        self.fixture.git("update-ref", "--no-deref", "-d", product, self.first)
        self.fixture.git("symbolic-ref", product, "refs/heads/product-target")
        with self.assertRaises(MetadataV2HostRefused):
            self.host.read(self.locator())

    def test_archive_or_issue_ref_changed_during_read_is_not_accepted(self):
        for changed_ref in (ARCHIVE_REF, issue_ref("101")):
            observations = {}
            def raced(ref, deadline):
                observations[ref] = observations.get(ref, 0) + 1
                state = self.fixture.observe(ref, deadline)
                if ref == changed_ref and observations[ref] > 1:
                    return RefState(ref, self.first if ref == ARCHIVE_REF else self.fixture.genesis, True)
                return state
            with self.subTest(changed_ref=changed_ref):
                with self.assertRaises(MetadataV2HostRefused):
                    self.bind(observer=raced).read(self.locator())

    def test_invalid_repository_format_identity_and_resource_bounds(self):
        for change in (
            {"git_object_format": "sha256"}, {"genesis": "0" * 64},
            {"repository_id": 0}, {"repository_id": True}, {"repository": "ACME/widgets/sub"},
        ):
            with self.subTest(change=change), self.assertRaises(MetadataV2HostRefused):
                dataclasses.replace(self.identity, **change)
        for change in ({"issue": 0}, {"record_id": "0"}, {"base_revision": "invalid"}):
            with self.subTest(change=change), self.assertRaises(MetadataV2HostRefused):
                dataclasses.replace(self.issue, **change)
        with self.assertRaises(MetadataV2HostRefused):
            self.bind(limits=Limits(commits=1)).read(self.locator())
        with self.assertRaises(MetadataV2HostRefused):
            self.bind(reader=lambda *_: ("commit", b"forged")).read(self.locator())

    def test_product_git_head_index_dirty_and_refs_untouched_by_read(self):
        project = self.fixture.root / "product"
        project.mkdir()
        self.fixture.git("init", "-q", str(project), bare=False)
        note = project / "tracked.txt"
        note.write_text("v1\n", encoding="ascii")
        self.fixture.git("-C", str(project), "add", "tracked.txt", bare=False)
        self.fixture.git("-C", str(project), "-c", "user.name=Fixture",
                         "-c", "user.email=fixture@invalid", "-c", "commit.gpgsign=false",
                         "commit", "-qm", "base", bare=False)
        note.write_text("v2\n", encoding="ascii")
        self.fixture.git("-C", str(project), "add", "tracked.txt", bare=False)
        note.write_text("v3-dirty\n", encoding="ascii")
        (project / "owner.txt").write_text("owner preserved\n", encoding="ascii")
        def inspect():
            return (
                self.fixture.git("-C", str(project), "rev-parse", "HEAD", bare=False),
                self.fixture.git("-C", str(project), "ls-files", "--stage", bare=False),
                self.fixture.git("-C", str(project), "status", "--porcelain=v1", "-uall", bare=False),
                self.fixture.git("-C", str(project), "show-ref", bare=False),
                note.read_bytes(),
                (project / "owner.txt").read_bytes(),
            )
        before = inspect()
        self.assertEqual("task-record", self.host.read(self.locator())["kind"])
        self.assertEqual("contract", self.host.read(
            self.locator("contract", self.contract_id))["kind"])
        self.assertEqual(before, inspect())


if __name__ == "__main__":
    unittest.main()
