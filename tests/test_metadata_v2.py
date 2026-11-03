"""Disposable local Git fixtures only. No production transport or Task process."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components/agent-core-v4"))

import metadata_codec as codec
import metadata_v2 as v2
from metadata_v2_archive import archive_issue
from task_record import encode_contract, encode_record


REPOSITORY = "acme/widgets"
SUBJECT = "a" * 40


class Fixture:
    """Offline Host model. NOT a server CAS/ref-type/lifecycle guarantee."""

    def __init__(self, root: Path):
        self.root = root
        self.bare = root / "remote.git"
        self.calls: list[v2.Update] = []
        self.reservations: set[str] = set()
        self.git("init", "--bare", "--object-format=sha1", str(self.bare), bare=False)
        self.genesis_bundle = v2.construct({}, (), nonce="genesis")
        self.genesis = self.genesis_bundle.commit
        self.install(self.genesis_bundle)
        self.set_ref(v2.ARCHIVE_REF, self.genesis)
        # A receive-pack-only model of archive deletion/non-FF protection.
        # Local update-ref bypasses update hooks, as a privileged Host would.
        hook = self.bare / "hooks/update"
        hook.write_text(
            "#!/bin/sh\n"
            "[ \"$1\" = refs/heads/kagari/metadata ] || exit 0\n"
            "[ \"$3\" != 0000000000000000000000000000000000000000 ] || exit 1\n"
            "git merge-base --is-ancestor \"$2\" \"$3\"\n", encoding="ascii")
        hook.chmod(0o700)

    def git(self, *args: str, data: bytes | None = None, bare: bool = True,
            check: bool = True) -> bytes:
        environment = {k: value for k, value in os.environ.items() if not k.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
                           GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="file")
        command = ["git"] + (["--git-dir", str(self.bare)] if bare else []) + list(args)
        result = subprocess.run(command, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=environment, timeout=5, cwd=self.root)
        if check and result.returncode:
            raise AssertionError(f"fixture Git failed {args!r}: {result.stderr!r}")
        if not check:
            return str(result.returncode).encode()
        return result.stdout

    def install(self, bundle: v2.Bundle) -> None:
        for key, (kind, data) in bundle.objects.items():
            actual = self.git("hash-object", "-w", "-t", kind, "--stdin", data=data).decode().strip()
            assert key == actual

    def set_ref(self, ref: str, commit: str, old: str | None = None) -> None:
        self.git("update-ref", "--no-deref", ref, commit, old or "0" * 40)

    def observe(self, ref: str, deadline: float) -> v2.RefState:
        if time.monotonic() >= deadline:
            raise TimeoutError()
        symbolic = self.git("symbolic-ref", "-q", ref, check=False)
        if symbolic == b"0":
            return v2.RefState(ref, None, False)
        # Git for-each-ref matches prefixes; filter by the exact canonical name.
        # A child ref must never stand in for an absent Issue metadata ref.
        lines = self.git("for-each-ref", "--format=%(refname) %(objectname)", ref).decode().splitlines()
        exact = [entry.partition(" ")[2] for entry in lines
                 if entry.partition(" ")[0] == ref]
        if len(exact) > 1:
            raise ValueError("ambiguous fixture ref")
        return v2.RefState(ref, exact[0] if exact else None, True)

    def reader(self, key: str, maximum: int, deadline: float) -> tuple[str, bytes]:
        v2.oid(key)
        if time.monotonic() >= deadline:
            raise TimeoutError()
        size = int(self.git("cat-file", "-s", key))
        if size > maximum:
            raise ValueError("fixture object size limit")
        kind = self.git("cat-file", "-t", key).decode().strip()
        return kind, self.git("cat-file", kind, key)

    def writer(self, request: v2.Update, bundle: v2.Bundle, deadline: float) -> bool:
        self.calls.append(request)
        if (request.repository != REPOSITORY or request.genesis != self.genesis
                or bundle.commit != request.candidate
                or request.ref not in {v2.ARCHIVE_REF, v2.issue_ref(request.task)}):
            return False
        archive = self.observe(v2.ARCHIVE_REF, deadline)
        issue = self.observe(v2.issue_ref(request.task), deadline)
        if (not archive.direct or not issue.direct or archive.commit != request.expected_archive
                or issue.commit != request.expected_issue):
            return False
        if request.ref != v2.ARCHIVE_REF and request.expected_issue is None:
            if request.task in self.reservations:
                return False
            self.reservations.add(request.task)
        self.install(bundle)
        expected = request.expected_archive if request.ref == v2.ARCHIVE_REF else request.expected_issue
        self.set_ref(request.ref, request.candidate, expected)
        return True

    def store(self, *, writer: bool = True, **kwargs) -> v2.MetadataV2:
        return v2.MetadataV2(REPOSITORY, self.genesis, self.reader, self.observe,
                             issue_writer=self.writer if writer else None, **kwargs)


class MetadataV2Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="metadata-v2-offline-")
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(Path(self.temp.name))
        self.store = self.fixture.store()

    def task_objects(self, task="101", title="first", disposition=None):
        contract_id, contract = encode_contract(REPOSITORY, task, SUBJECT,
            {"schema_version": 1, "title": title, "body": "bounded requirements"})
        payload = {"schema_version": 1, "branch_ref": f"refs/heads/task-{task}", "contract_id": contract_id}
        if disposition:
            payload["disposition"] = disposition
        record_id, record = encode_record(REPOSITORY, task, SUBJECT, payload)
        return record_id, (contract, record)

    def publish(self, task="101"):
        record_id, objects = self.task_objects(task)
        commit = self.store.publish_issue(task, objects, record_update=(None, record_id))
        return commit, record_id

    def archive(self, task="101", **kwargs):
        options = dict(retention_policy="fixture-receive-only-v1", verify_terminal=lambda request, deadline: True,
                       archive_writer=self.fixture.writer)
        options.update(kwargs)
        return archive_issue(self.store, task, **options)

    def test_fixture_observer_does_not_adopt_child_ref_as_exact_issue_ref(self):
        wanted = v2.issue_ref("101")
        self.fixture.set_ref(wanted + "/child", self.fixture.genesis)
        observed = self.fixture.observe(wanted, time.monotonic() + 2)
        self.assertEqual(wanted, observed.ref)
        self.assertIsNone(observed.commit)
        self.assertTrue(observed.direct)
        record_id, objects = self.task_objects()
        with self.assertRaises(v2.UncertainPublication):
            self.store.publish_issue("101", objects, record_update=(None, record_id))
        self.assertEqual(self.fixture.genesis,
            self.fixture.observe(wanted + "/child", time.monotonic() + 2).commit)

    def test_two_concurrent_issues_archive_delete_and_fresh_clone_exact_old_oid(self):
        first, record_id = self.publish()
        second, _ = self.publish("202")
        self.assertEqual((self.fixture.genesis,), self.store.validation().issue(first, "101").parents)
        self.assertEqual((self.fixture.genesis,), self.store.validation().issue(second, "202").parents)
        merged = self.archive()
        self.assertEqual((self.fixture.genesis, first), self.store.validation().archive(merged).parents)
        # Only this disposable fixture deletes; source core exposes no delete API.
        self.fixture.git("update-ref", "--no-deref", "-d", v2.issue_ref("101"), first)
        later = self.archive("202")
        self.assertEqual((merged, second), self.store.validation().archive(later).parents)
        third, _ = self.publish("303")
        self.assertEqual((self.fixture.genesis,), self.store.validation().issue(third, "303").parents)
        clone = self.fixture.root / "new-clone.git"
        self.fixture.git("clone", "--bare", "--no-hardlinks", str(self.fixture.bare), str(clone), bare=False)
        clone_fixture = object.__new__(Fixture)
        clone_fixture.root, clone_fixture.bare = self.fixture.root, clone
        reader = v2.MetadataV2(REPOSITORY, self.fixture.genesis, clone_fixture.reader, clone_fixture.observe)
        record = reader.read_object(first, "task-record", record_id, task="101", subject=SUBJECT)
        self.assertEqual(record_id, codec.encode_object("task-record", REPOSITORY, "101", SUBJECT, record["payload"])[0])
        self.assertNotEqual(first, later)  # reader did not substitute latest archive tip

    def test_repeated_tree_reuses_large_decoded_evidence(self):
        first, _ = self.publish()
        payload = {"schema_version": 1, "kind": "structured",
                   "producer": {"name": "fixture"},
                   "created_at": "2026-11-01T00:00:00Z",
                   "payload": {"items": [{} for _ in range(5_000)]}}
        big_id, big = codec.encode_object("evidence", REPOSITORY, "101", SUBJECT, payload)
        second = self.store.publish_issue("101", [big])
        files = dict(self.store.validation().issue(second, "101").snapshot.files)
        heads = [second]
        for i in range(24):
            candidate = v2.construct(files, (heads[-1],), nonce=f"repeat-{i}")
            self.fixture.install(candidate)
            self.fixture.set_ref(v2.issue_ref("101"), candidate.commit, heads[-1])
            heads.append(candidate.commit)
        v = self.store.validation()
        v.issue(heads[-1], "101")
        self.assertEqual(1, len({id(v.nodes[h].snapshot) for h in heads}))
        self.assertEqual(1, len({id(v.nodes[h].snapshot.objects[big_id]) for h in heads}))
        self.assertLessEqual(v.used_containers, 5_100)

    def test_large_evidence_decoded_once_across_distinct_trees(self):
        self.publish()
        payload = {"schema_version": 1, "kind": "structured",
                   "producer": {"name": "fixture"},
                   "created_at": "2026-11-01T00:00:00Z",
                   "payload": {"items": [{} for _ in range(4_000)]}}
        big_id, big = codec.encode_object("evidence", REPOSITORY, "101", SUBJECT, payload)
        heads = [self.store.publish_issue("101", [big])]
        for i in range(6):
            _, small = codec.encode_object("evidence", REPOSITORY, "101", SUBJECT,
                {"schema_version": 1, "kind": "small", "producer": {"name": "fixture"},
                 "created_at": "2026-11-01T00:00:00Z", "payload": {"i": i}})
            heads.append(self.store.publish_issue("101", [small]))
        v = self.store.validation()
        v.issue(heads[-1], "101")
        self.assertGreater(len({id(v.nodes[h].snapshot) for h in heads}), 2)
        self.assertEqual(1, len({id(v.nodes[h].snapshot.objects[big_id]) for h in heads}))
        self.assertLessEqual(v.used_containers, 4_150)

    def test_json_container_budget_rejects_before_host_writer(self):
        self.publish()
        before = len(self.fixture.calls)
        payload = {"schema_version": 1, "kind": "bomb", "producer": {"name": "fixture"},
                   "created_at": "2026-11-01T00:00:00Z",
                   "payload": {"items": [{} for _ in range(9_000)]}}
        _, encoded = codec.encode_object("evidence", REPOSITORY, "101", SUBJECT, payload)
        with self.assertRaisesRegex(v2.InvalidMetadata, "JSON container budget"):
            self.store.publish_issue("101", [encoded])
        self.assertEqual(before, len(self.fixture.calls))

    def test_cumulative_container_budget_rejects_distinct_objects(self):
        self.publish()
        before = len(self.fixture.calls)
        objects = []
        for i in range(5):
            payload = {"schema_version": 1, "kind": "bomb", "producer": {"name": "fixture"},
                       "created_at": "2026-11-01T00:00:00Z",
                       "payload": {"items": [{} for _ in range(7_000)], "i": i}}
            _, encoded = codec.encode_object("evidence", REPOSITORY, "101", SUBJECT, payload)
            objects.append(encoded)
        with self.assertRaisesRegex(v2.InvalidMetadata, "cumulative decoded JSON container budget"):
            self.store.publish_issue("101", objects)
        self.assertEqual(before, len(self.fixture.calls))

    def test_duplicate_incoming_json_decoded_only_once(self):
        self.publish()
        payload = {"schema_version": 1, "kind": "offline-test",
                   "producer": {"name": "fixture"}, "created_at": "2026-11-01T00:00:00Z",
                   "payload": {"items": [{} for _ in range(7000)]}}
        evidence_id, encoded = codec.encode_object("evidence", REPOSITORY, "101", SUBJECT, payload)
        original = codec.decode_object
        admitted_decodes = 0

        def guarded(data, **kwargs):
            nonlocal admitted_decodes
            if (data == encoded and kwargs.get("expected_task") == "101"
                    and kwargs.get("expected_id") is None):
                admitted_decodes += 1
                if admitted_decodes > 1:
                    raise AssertionError("duplicate admitted JSON decoded more than once")
            return original(data, **kwargs)

        with patch.object(codec, "decode_object", side_effect=guarded):
            candidate = self.store.publish_issue("101", [encoded] * 12)
        self.assertEqual(1, admitted_decodes)
        self.assertEqual("101", self.store.read_object(
            candidate, "evidence", evidence_id, task="101", subject=SUBJECT)["task"])

    def test_large_json_rejected_before_first_decode(self):
        self.publish()
        payload = {"schema_version": 1, "kind": "bomb",
                   "producer": {"name": "fixture"}, "created_at": "2026-11-01T00:00:00Z",
                   "payload": {"items": [{} for _ in range(9000)]}}
        _, large = codec.encode_object("evidence", REPOSITORY, "101", SUBJECT, payload)
        original = codec.decode_object

        def guarded(data, **kwargs):
            if data == large:
                raise AssertionError("oversized JSON decoded before quota")
            return original(data, **kwargs)

        with patch.object(codec, "decode_object", side_effect=guarded):
            with self.assertRaisesRegex(v2.InvalidMetadata, "container budget"):
                self.store.publish_issue("101", [large])

    def test_cumulative_quota_precedes_excess_object_decode(self):
        self.publish()
        objects = []
        for index in range(5):
            payload = {"schema_version": 1, "kind": "bomb",
                       "producer": {"name": "fixture"}, "created_at": "2026-11-01T00:00:00Z",
                       "payload": {"items": [{}] * 7000, "index": index}}
            _, data = codec.encode_object("evidence", REPOSITORY, "101", SUBJECT, payload)
            objects.append(data)
        original = codec.decode_object

        def guarded(data, **kwargs):
            if data == objects[-1]:
                raise AssertionError("over-quota object was decoded")
            return original(data, **kwargs)

        with patch.object(codec, "decode_object", side_effect=guarded):
            with self.assertRaisesRegex(v2.InvalidMetadata, "cumulative.*container budget"):
                self.store.publish_issue("101", objects)

    def test_readback_must_drop_optimistic_tree_cache(self):
        missing_blobs = set()

        def reader(key, maximum, deadline):
            if key in missing_blobs:
                raise ValueError("Host cannot serve candidate blob")
            return self.fixture.reader(key, maximum, deadline)

        def writer(request, bundle, deadline):
            self.assertTrue(self.fixture.writer(request, bundle, deadline))
            missing_blobs.update(k for k, pair in bundle.objects.items() if pair[0] == "blob")
            return True

        self.store.reader = reader
        self.store.issue_writer = writer
        record_id, objects = self.task_objects()
        with self.assertRaisesRegex(v2.UncertainPublication, "readback"):
            self.store.publish_issue("101", objects, record_update=(None, record_id))

    def test_readback_must_recheck_candidate_tree(self):
        missing_trees = set()

        def reader(key, maximum, deadline):
            if key in missing_trees:
                raise ValueError("Host cannot serve candidate tree")
            return self.fixture.reader(key, maximum, deadline)

        def writer(request, bundle, deadline):
            self.assertTrue(self.fixture.writer(request, bundle, deadline))
            missing_trees.update(k for k, pair in bundle.objects.items() if pair[0] == "tree")
            return True

        self.store.reader = reader
        self.store.issue_writer = writer
        record_id, objects = self.task_objects()
        with self.assertRaisesRegex(v2.UncertainPublication, "readback"):
            self.store.publish_issue("101", objects, record_update=(None, record_id))

    def test_cross_issue_new_object_absent_from_old_tree_is_rejected_before_host(self):
        self.publish()
        _, foreign = self.task_objects("202")
        calls = len(self.fixture.calls)
        with self.assertRaises(codec.MetadataCodecError):
            self.store.publish_issue("101", foreign)
        self.assertEqual(calls, len(self.fixture.calls))

    def test_malicious_synthetic_writer_cross_issue_history_rejected(self):
        first, _ = self.publish()
        v = self.store.validation()
        files = dict(v.issue(first, "101").snapshot.files)
        for data in self.task_objects("202")[1]:
            envelope = codec.decode_object(data)
            content_id = codec.encode_object(envelope["kind"], REPOSITORY, "202", SUBJECT, envelope["payload"])[0]
            files[codec.object_path(envelope["kind"], content_id)] = data
        bad = v2.construct(files, (first,))
        self.fixture.install(bad)
        with self.assertRaises(v2.InvalidMetadata):
            self.store.validation().issue(bad.commit, "101")

    def test_record_conflicting_expected_prior_and_valid_cancelled_update(self):
        first, old_id = self.publish()
        new_id, new_objects = self.task_objects(disposition={"kind": "cancelled"})
        with self.assertRaisesRegex(v2.InvalidMetadata, "expected-prior"):
            self.store.publish_issue("101", new_objects, record_update=(None, new_id))
        next_commit = self.store.publish_issue("101", new_objects, record_update=(old_id, new_id))
        self.assertEqual((first,), self.store.validation().issue(next_commit, "101").parents)
        self.assertEqual({"kind": "cancelled"}, self.store.read_object(next_commit, "task-record", new_id,
            task="101", subject=SUBJECT)["payload"]["disposition"])

    def test_completed_disposition_and_extra_schema_fields_are_not_invented(self):
        with self.assertRaises(ValueError):
            self.task_objects(disposition={"kind": "completed"})
        _, data = codec.encode_object("contract", REPOSITORY, "101", SUBJECT,
            {"schema_version": 1, "title": "x", "body": "", "extra": True})
        with self.assertRaises(v2.InvalidMetadata):
            self.store.publish_issue("101", [data])
        self.assertEqual([], self.fixture.calls)

    def test_same_issue_reopen_after_archive_refuses(self):
        first, _ = self.publish()
        self.archive()
        self.fixture.git("update-ref", "--no-deref", "-d", v2.issue_ref("101"), first)
        before = len(self.fixture.calls)
        with self.assertRaisesRegex(v2.InvalidMetadata, "reopen"):
            self.publish()
        self.assertEqual(before, len(self.fixture.calls))

    def test_deleted_unarchived_lifecycle_requires_host_reservation(self):
        first, _ = self.publish()
        self.fixture.git("update-ref", "--no-deref", "-d", v2.issue_ref("101"), first)
        with self.assertRaises(v2.UncertainPublication):
            self.publish()
        self.assertIsNone(self.fixture.observe(v2.issue_ref("101"), time.monotonic() + 2).commit)

    def test_no_callback_no_effect_and_missing_archive_capabilities_refuse(self):
        record_id, objects = self.task_objects()
        with self.assertRaisesRegex(v2.InvalidMetadata, "no effects"):
            self.fixture.store(writer=False).publish_issue("101", objects, record_update=(None, record_id))
        self.assertEqual([], self.fixture.calls)
        self.publish()
        for missing in ("retention_policy", "verify_terminal", "archive_writer"):
            with self.subTest(missing=missing):
                before = len(self.fixture.calls)
                with self.assertRaisesRegex(v2.InvalidMetadata, "archive requires"):
                    self.archive(**{missing: None})
                self.assertEqual(before, len(self.fixture.calls))

    def test_disposition_is_not_terminal_proof_and_verifier_gets_exact_bindings(self):
        issue, _ = self.publish()
        verifier = Mock(return_value=False)
        with self.assertRaisesRegex(v2.InvalidMetadata, "not separately verified"):
            self.archive(verify_terminal=verifier)
        request, _ = verifier.call_args.args
        self.assertEqual((self.fixture.genesis, issue, v2.ARCHIVE_REF, "101"),
                         (request.expected_archive, request.expected_issue, request.ref, request.task))
        self.assertEqual(1, len(self.fixture.calls))

    def test_stale_archive_writer_preserves_both_issue_refs(self):
        first, _ = self.publish()
        second, _ = self.publish("202")
        winner = []

        def race(request, bundle, deadline):
            winner.append(self.archive("202"))
            return self.fixture.writer(request, bundle, deadline)

        with self.assertRaises(v2.UncertainPublication):
            self.archive(archive_writer=race)
        self.assertEqual(winner[0], self.fixture.observe(v2.ARCHIVE_REF, time.monotonic() + 2).commit)
        for task, commit in (("101", first), ("202", second)):
            self.assertEqual(commit, self.fixture.observe(v2.issue_ref(task), time.monotonic() + 2).commit)

    def test_changed_issue_after_terminal_verification_refuses_archive(self):
        self.publish()

        def race(request, bundle, deadline):
            new_id, objects = self.task_objects(title="changed")
            self.store.publish_issue("101", objects, record_update=(self.task_objects()[0], new_id))
            return self.fixture.writer(request, bundle, deadline)

        with self.assertRaises(v2.UncertainPublication):
            self.archive(archive_writer=race)
        self.assertEqual(self.fixture.genesis, self.fixture.observe(v2.ARCHIVE_REF, time.monotonic() + 2).commit)

    def test_unknown_archive_ack_keeps_branch_even_when_mutation_applied(self):
        first, record_id = self.publish()

        def lost_ack(request, bundle, deadline):
            self.assertTrue(self.fixture.writer(request, bundle, deadline))
            raise TimeoutError("unknown Ack")

        with self.assertRaisesRegex(v2.UncertainPublication, "preserve Issue"):
            self.archive(archive_writer=lost_ack)
        self.assertEqual(first, self.fixture.observe(v2.issue_ref("101"), time.monotonic() + 2).commit)
        self.assertEqual("101", self.store.read_object(first, "task-record", record_id,
                         task="101", subject=SUBJECT)["task"])

    def test_changed_or_absent_readback_is_uncertain_never_deleted(self):
        self.publish()
        old_observer = self.store.observer

        def change_observation(request, bundle, deadline):
            self.fixture.writer(request, bundle, deadline)
            self.store.observer = lambda ref, end: v2.RefState(ref, None, True)
            return True

        try:
            with self.assertRaises(v2.UncertainPublication):
                self.archive(archive_writer=change_observation)
        finally:
            self.store.observer = old_observer
        self.assertIsNotNone(self.fixture.observe(v2.issue_ref("101"), time.monotonic() + 2).commit)

    def test_symbolic_same_oid_substitution_is_host_blocker(self):
        first, _ = self.publish()
        self.fixture.set_ref("refs/heads/product", first)

        def substitute(request, bundle, deadline):
            self.fixture.git("update-ref", "-d", v2.issue_ref("101"), first)
            self.fixture.git("symbolic-ref", v2.issue_ref("101"), "refs/heads/product")
            return self.fixture.writer(request, bundle, deadline)

        self.store.issue_writer = substitute
        _, objects = self.task_objects(title="attempted")
        with self.assertRaises(v2.UncertainPublication):
            self.store.publish_issue("101", objects)
        self.assertEqual(first.encode() + b"\n", self.fixture.git("rev-parse", "refs/heads/product"))

    def test_missing_foreign_repository_contract_pointer_and_subject_refuse(self):
        record_id, objects = self.task_objects()
        with self.assertRaises(v2.InvalidMetadata):
            self.store.publish_issue("101", [objects[1]], record_update=(None, record_id))
        foreign_id, foreign = encode_contract("other/repo", "101", SUBJECT,
            {"schema_version": 1, "title": "foreign", "body": ""})
        with self.assertRaises(codec.MetadataCodecError):
            self.store.publish_issue("101", [foreign])
        with self.assertRaises(v2.InvalidMetadata):
            self.store.publish_issue("101", objects, record_update=(None, foreign_id))
        first, record_id = self.publish()
        with self.assertRaises(codec.MetadataCodecError):
            self.store.read_object(first, "task-record", record_id, task="101", subject="b" * 40)
        with self.assertRaises(codec.MetadataCodecError):
            self.store.read_object(first, "task-record", record_id, task="101", subject=None)
        with self.assertRaises(v2.InvalidMetadata):
            self.store.read_object(first, "evidence", "0" * 64, task="101", subject=SUBJECT)

    def test_unreachable_candidate_and_latest_tip_not_checkpoint(self):
        first, record_id = self.publish()
        files = self.store.validation().issue(first, "101").snapshot.files
        unreachable = v2.construct(files, (first,))
        self.fixture.install(unreachable)
        with self.assertRaisesRegex(v2.InvalidMetadata, "unreachable"):
            self.store.read_object(unreachable.commit, "task-record", record_id, task="101", subject=SUBJECT)
        second = self.store.publish_issue("101", [])
        self.assertNotEqual(first, second)
        self.assertEqual("101", self.store.read_object(first, "task-record", record_id,
                         task="101", subject=SUBJECT)["task"])

    def test_wrong_root_product_parent_archive_as_issue_and_ordinary_merge_refuse(self):
        first, _ = self.publish()
        second, _ = self.publish("202")
        files = self.store.validation().issue(first, "101").snapshot.files
        cases = [v2.construct(files, ()), v2.construct(files, (first, second)),
                 v2.construct(files, (second, self.fixture.genesis))]
        product = v2.construct({}, (), nonce="product-root")
        self.fixture.install(product)
        cases.append(v2.construct(files, (product.commit,)))
        for bundle in cases:
            with self.subTest(commit=bundle.commit):
                self.fixture.install(bundle)
                with self.assertRaises(v2.InvalidMetadata):
                    self.store.validation().history(bundle.commit)
        with self.assertRaises(v2.InvalidMetadata):
            self.store.validation().archive(first)
        merged = self.archive()
        with self.assertRaises(v2.InvalidMetadata):
            self.store.validation().issue(merged, "101")

    def test_three_parents_and_missing_parent_refuse(self):
        first, _ = self.publish()
        raw = self.fixture.reader(first, 10000, time.monotonic() + 2)[1]
        replacement = (f"parent {self.fixture.genesis}\nparent {'b' * 40}\nparent {'c' * 40}\n").encode()
        raw = raw.replace(f"parent {self.fixture.genesis}\n".encode(), replacement)
        key = v2.git_id("commit", raw)
        self.fixture.install(v2.Bundle(key, {key: ("commit", raw)}))
        with self.assertRaises(v2.InvalidMetadata):
            self.store.validation().history(key)
        files = self.store.validation().issue(first, "101").snapshot.files
        missing = v2.construct(files, ("b" * 40,))
        self.fixture.install(missing)
        with self.assertRaises(v2.InvalidMetadata):
            self.store.validation().history(missing.commit)

    def test_deletion_restoration_overwrite_and_wrong_digest_refuse(self):
        first, _ = self.publish()
        files = dict(self.store.validation().issue(first, "101").snapshot.files)
        path = next(p for p in files if p.startswith("objects/contract"))
        # Delete a Record pointer (objects remain internally well formed).
        deleted_files = {p: data for p, data in files.items() if not p.startswith("tasks/")}
        deleted = v2.construct(deleted_files, (first,))
        restored = v2.construct(files, (deleted.commit,))
        self.fixture.install(deleted)
        self.fixture.install(restored)
        with self.assertRaisesRegex(v2.InvalidMetadata, "pointer deleted"):
            self.store.validation().history(restored.commit)
        files[path] = b"overwritten"
        bad = v2.construct(files, (first,))
        self.fixture.install(bad)
        with self.assertRaises(v2.InvalidMetadata):
            self.store.validation().history(bad.commit)
        with self.assertRaisesRegex(v2.InvalidMetadata, "digest"):
            v2.Validation(REPOSITORY, self.fixture.genesis,
                lambda key, maximum, end: ("commit", b"wrong bytes")).history(first)

    def test_nonregular_mode_foreign_path_and_missing_blob_refuse(self):
        first, _ = self.publish()
        blob = self.fixture.git("hash-object", "-w", "--stdin", data=b"x").decode().strip()
        for mode, name, child in ((b"100755", b"bad", blob), (b"120000", b"bad", blob),
                                  (b"160000", b"bad", first), (b"100644", b"foreign", blob),
                                  (b"100644", b"foreign", "f" * 40)):
            with self.subTest(mode=mode, child=child):
                tree = mode + b" " + name + b"\0" + bytes.fromhex(child)
                tree_key = v2.git_id("tree", tree)
                self.fixture.install(v2.Bundle(tree_key, {tree_key: ("tree", tree)}))
                with self.assertRaises(v2.InvalidMetadata):
                    self.store.validation().tree(tree_key)
        # Missing blob under a fully allowed path, not just a foreign path.
        tree = b"100644 record\0" + bytes.fromhex("f" * 40)
        leaf = v2.git_id("tree", tree)
        task_tree = b"40000 101\0" + bytes.fromhex(leaf)
        task_key = v2.git_id("tree", task_tree)
        root_tree = b"40000 tasks\0" + bytes.fromhex(task_key)
        root_key = v2.git_id("tree", root_tree)
        self.fixture.install(v2.Bundle(root_key, {
            leaf: ("tree", tree), task_key: ("tree", task_tree), root_key: ("tree", root_tree)}))
        with self.assertRaisesRegex(v2.InvalidMetadata, "unavailable"):
            self.store.validation().tree(root_key)

    def test_evidence_supersession_exact_bytes_and_missing_target(self):
        first, _ = self.publish()
        payload = {"schema_version": 1, "kind": "offline-test", "producer": {"fixture": True},
                   "created_at": "2026-11-01T00:00:00Z", "payload": {"ok": True}}
        content_id, data = codec.encode_object("evidence", REPOSITORY, "101", SUBJECT, payload)
        second = self.store.publish_issue("101", [data])
        self.assertEqual(payload, self.store.read_object(second, "evidence", content_id,
                         task="101", subject=SUBJECT)["payload"])
        with self.assertRaisesRegex(v2.InvalidMetadata, "absent at exact commit"):
            self.store.read_object(first, "evidence", content_id, task="101", subject=SUBJECT)
        payload["supersedes"] = "f" * 64
        _, missing = codec.encode_object("evidence", REPOSITORY, "101", SUBJECT, payload)
        with self.assertRaisesRegex(v2.InvalidMetadata, "Evidence target"):
            self.store.publish_issue("101", [missing])
        payload["supersedes"] = content_id
        newer_id, newer = codec.encode_object("evidence", REPOSITORY, "101", SUBJECT, payload)
        third = self.store.publish_issue("101", [newer])
        self.assertEqual(content_id, self.store.read_object(third, "evidence", newer_id,
                         task="101", subject=SUBJECT)["payload"]["supersedes"])

    def test_snapshot_closed_schema_and_exact_ancestor_links(self):
        from task_view import encode_snapshot
        first, record_id = self.publish()
        contract_id = codec.decode_object(self.task_objects()[1][1])["payload"]["contract_id"]
        branch = "refs/heads/task-101"
        view = {
            "schema_version": 1, "repository": REPOSITORY, "task": "101", "subject": SUBJECT,
            "branch_ref": branch,
            "git": {"head": SUBJECT, "tree": "b" * 40, "branch_ref": branch,
                    "branch_matches_task": True,
                    "status": {name: 0 for name in ("index_changes", "worktree_changes", "untracked", "conflicts", "submodule_changes")},
                    "worktrees": [], "default_branch": {"state": "not_requested"},
                    "remote_head": {"state": "not_requested"}, "submodule_worktrees": "not_inspected",
                    "staged_gitlinks": 0},
            "authority": {"state": "observed", "metadata_commit": first, "record_id": record_id,
                          "contract_id": contract_id, "base_revision": SUBJECT,
                          "branch_ref": branch, "disposition": None},
            "evidence": [], "github": {"state": "not_requested"},
            "previous_checkpoint": {"state": "not_requested"},
        }
        snapshot_id, snapshot = encode_snapshot(REPOSITORY, "101", SUBJECT, "turn-end", view)
        second = self.store.publish_issue("101", [snapshot])
        self.assertEqual(first, self.store.read_object(second, "task-view-snapshot", snapshot_id,
                         task="101", subject=SUBJECT)["payload"]["view"]["authority"]["metadata_commit"])
        # Reusing the same tree at another commit must not reuse the prior
        # commit's ancestry authorization for Snapshot links.
        source_tree = dict(self.store.validation().issue(second, "101").snapshot.files)
        forged = v2.construct(source_tree, (self.fixture.genesis,), nonce="wrong-ancestor")
        self.fixture.install(forged)
        validator = self.store.validation()
        validator.issue(second, "101")  # primes the tree-cache
        with self.assertRaisesRegex(v2.InvalidMetadata, "exact metadata ancestor"):
            validator.issue(forged.commit, "101")
        view["authority"]["metadata_commit"] = "f" * 40
        _, missing = encode_snapshot(REPOSITORY, "101", SUBJECT, "turn-end", view)
        before = len(self.fixture.calls)
        with self.assertRaisesRegex(v2.InvalidMetadata, "exact metadata ancestor"):
            self.store.publish_issue("101", [missing])
        self.assertEqual(before, len(self.fixture.calls))

    def test_archive_existing_task_conflict_and_union_tamper_refuse(self):
        first, _ = self.publish()
        merged = self.archive()
        with self.assertRaises(v2.InvalidMetadata):
            self.archive()
        second, _ = self.publish("202")
        # Tree omits second Issue entirely despite two qualifying parent roles.
        bad = v2.construct(self.store.validation().archive(merged).snapshot.files, (merged, second))
        self.fixture.install(bad)
        with self.assertRaisesRegex(v2.InvalidMetadata, "exact validated union"):
            self.store.validation().archive(bad.commit)

    def test_receive_hook_models_archive_protection_not_github_guarantee(self):
        self.publish()
        self.archive()
        # Only local receive-pack in disposable bare repo; no network involved.
        for spec in (f":{v2.ARCHIVE_REF}", f"+{self.fixture.genesis}:{v2.ARCHIVE_REF}"):
            with self.subTest(spec=spec):
                self.assertNotEqual(b"0", self.fixture.git("push", str(self.fixture.bare), spec, check=False))

    def test_resource_limits_deadlines_and_bad_task_spelling_refuse(self):
        first, _ = self.publish()
        for limits in (v2.Limits(commits=1), v2.Limits(entries=1), v2.Limits(bytes=10)):
            with self.subTest(limits=limits):
                with self.assertRaises(v2.InvalidMetadata):
                    self.fixture.store(limits=limits).validation().history(first)
        v = self.store.validation()
        v.deadline = time.monotonic() - 1
        with self.assertRaisesRegex(v2.InvalidMetadata, "deadline"):
            v.history(first)
        for task in ("0", "01", "-1", "1/../../main", "1\n", "１", 1, "1" * 129):
            with self.subTest(task=task):
                with self.assertRaises(v2.InvalidMetadata):
                    v2.issue_ref(task)
        for value in (float("nan"), float("inf"), -1, True):
            with self.assertRaises(v2.InvalidMetadata):
                v2.Limits(seconds=value)

    def test_v1_ref_is_independent_and_never_read_or_replaced(self):
        import metadata_ref
        self.assertEqual("refs/agentcore/metadata", metadata_ref.METADATA_REF)
        self.fixture.set_ref(metadata_ref.METADATA_REF, self.fixture.genesis)
        self.publish()
        self.archive()
        self.assertEqual(self.fixture.genesis.encode() + b"\n",
                         self.fixture.git("rev-parse", metadata_ref.METADATA_REF))


if __name__ == "__main__":
    unittest.main()
