"""#337 Phase 3B2: source-only ordinary Git receive-pack integration tests.

Only Python tempfile bare/file-protocol repositories are mutated. Never
contact GitHub, construct credentials, alter repository Rulesets, or exercise
a production Metadata ref. This tests transport behavior, not a production
Host authority, a live GitHub canary, or conditional-delete qualification.
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "components/agent-core-v4"), str(ROOT / "tools")]

from metadata_v2 import (  # noqa: E402
    ARCHIVE_REF, InvalidMetadata, Limits, MetadataV2, RefState,
    UncertainPublication, construct, issue_ref, union,
)
from metadata_v2_archive import archive_issue  # noqa: E402
from task_record import encode_contract, encode_record  # noqa: E402
from tests.test_metadata_v2 import Fixture, REPOSITORY, SUBJECT  # noqa: E402


class OrdinaryTransportFixture:
    """A bounded local simulation of the KUMIKI-owned Git transport."""

    def __init__(self, root: Path):
        self.root = root
        self.source = Fixture(root)
        # A previous fixture intentionally models protected refs with an
        # update hook. Ordinary-branch verification must not inherit it.
        hook = self.source.bare / "hooks/update"
        if not hook.exists():
            raise AssertionError("expected fixture-only protection hook")
        hook.unlink()
        self.remote = root / "ordinary-remote.git"
        self.empty_template = root / "empty-template"
        self.empty_template.mkdir()
        self.source.git(
            "init", "--bare", "--object-format=sha1",
            f"--template={self.empty_template}", str(self.remote), bare=False,
        )
        if (self.remote / "hooks" / "update").exists():
            raise AssertionError("ordinary transport must have no remote update hook")
        self.remote_url = self.remote.as_uri()
        self.write_count = 0
        self.fail_ack_after_push = False
        self.active_reader_repo = self.remote

        # Create an independent synthetic Product/default commit. This is
        # intentionally unrelated to the orphan Metadata genesis.
        empty_tree = self.source.git("mktree", data=b"").decode().strip()
        self.main_oid = self.source.git(
            "-c", "user.name=synthetic-kumiki-fixture",
            "-c", "user.email=synthetic@example.invalid",
            "commit-tree", empty_tree, "-m", "synthetic Product main root",
        ).decode().strip()
        self.source.set_ref("refs/heads/main", self.main_oid)
        self.push("refs/heads/main")
        self.push(ARCHIVE_REF)
        self._remote_git("symbolic-ref", "HEAD", "refs/heads/main")
        if self.remote_oid("refs/heads/main") != self.main_oid:
            raise AssertionError("initial default branch publication failed")
        self.store = MetadataV2(
            REPOSITORY, self.source.genesis,
            self.read_object, self.observe,
            issue_writer=self.writer,
            limits=Limits(seconds=30),
        )

    def _remote_git(self, *args: str, repo: Path | None = None,
                    data: bytes | None = None, check: bool = True) -> bytes:
        target = self.active_reader_repo if repo is None else repo
        return self.source.git(
            "--git-dir", str(target), *args, data=data, bare=False, check=check,
        )

    def remote_oid(self, ref: str, *, repo: Path | None = None) -> str | None:
        target = self.active_reader_repo if repo is None else repo
        lines = self._remote_git(
            "for-each-ref", "--format=%(refname) %(objectname)", ref, repo=target,
        ).decode().splitlines()
        found = [line.partition(" ")[2] for line in lines
                 if line.partition(" ")[0] == ref]
        if len(found) > 1:
            raise AssertionError("ambiguous exact Git ref")
        return found[0] if found else None

    def read_object(self, key: str, maximum: int, deadline: float):
        if time.monotonic() >= deadline:
            raise TimeoutError("reader deadline")
        if int(self._remote_git("cat-file", "-s", key)) > maximum:
            raise ValueError("reader byte limit")
        kind = self._remote_git("cat-file", "-t", key).decode().strip()
        return kind, self._remote_git("cat-file", kind, key)

    def observe(self, ref: str, deadline: float) -> RefState:
        if time.monotonic() >= deadline:
            raise TimeoutError("observer deadline")
        if self._remote_git("symbolic-ref", "-q", ref, check=False) == b"0":
            return RefState(ref, None, False)
        return RefState(ref, self.remote_oid(ref), True)

    def push(self, ref: str, *, destination: str | None = None) -> None:
        remote_ref = ref if destination is None else destination
        self.source.git(
            "push", "--porcelain", "--no-verify", self.remote_url,
            f"{ref}:{remote_ref}",
        )

    def writer(self, update, bundle, deadline: float) -> bool:
        self.write_count += 1
        if (update.repository != REPOSITORY
                or update.genesis != self.source.genesis
                or update.candidate != bundle.commit
                or update.ref not in {ARCHIVE_REF, issue_ref(update.task)}
                or (update.ref == ARCHIVE_REF
                    and update.retention_policy is not None)):
            return False
        a = self.observe(ARCHIVE_REF, deadline)
        c = self.observe(issue_ref(update.task), deadline)
        if (not a.direct or not c.direct
                or a.commit != update.expected_archive
                or c.commit != update.expected_issue):
            return False
        # Only the fixture may install object bytes into its source repo.
        # The core has already validated their exact tree/parent semantics.
        self.source.install(bundle)
        expected = (update.expected_archive if update.ref == ARCHIVE_REF
                    else update.expected_issue)
        self.source.set_ref(update.ref, bundle.commit, expected)
        if self.source.git(
            "push", "--porcelain", "--no-verify", self.remote_url,
            f"{update.ref}:{update.ref}", check=False,
        ) != b"0":
            return False
        if self.fail_ack_after_push:
            self.fail_ack_after_push = False
            raise TimeoutError("synthetic ACK lost after remote effect")
        return True

    def issue(self, task: str) -> tuple[str, str]:
        contract_id, contract = encode_contract(
            REPOSITORY, task, SUBJECT,
            {"schema_version": 1, "title": f"Synthetic {task}",
             "body": f"Independent fixture for {task}"},
        )
        record_id, record = encode_record(
            REPOSITORY, task, SUBJECT,
            {"schema_version": 1,
             "branch_ref": f"refs/heads/product-{task}",
             "contract_id": contract_id},
        )
        commit = self.store.publish_issue(
            task, [contract, record], record_update=(None, record_id),
        )
        return commit, record_id

    def archive(self, task: str) -> str:
        # Test-only terminality placeholder; this is not a live Task,
        # stopped writer, Product preservation, or Human approval proof.
        return archive_issue(
            self.store, task, retention_policy=None,
            verify_terminal=lambda update, deadline: (
                update.ref == ARCHIVE_REF
                and update.task == task and time.monotonic() < deadline
            ),
            archive_writer=self.writer,
        )

    def lease_delete(self, task: str, expected: str) -> bool:
        """Normal Git push deletion guarded by the observed expected tip."""
        ref = issue_ref(task)
        result = self.source.git(
            "push", "--no-verify", "--porcelain",
            f"--force-with-lease={ref}:{expected}",
            self.remote_url, f":{ref}", check=False,
        )
        return result == b"0"

    def cleanup(self, task: str, expected: str) -> bool:
        """Fixture-only preflight, not a production deletion adapter."""
        v = self.store.validation()
        a = v.observe(self.observe, ARCHIVE_REF)
        v.archive(a)
        tip = v.observe(self.observe, issue_ref(task), absent=True)
        if (tip != expected or expected not in v.nodes
                or v.nodes[expected].role != "issue"
                or v.nodes[expected].task != task):
            return False
        # This ordinary Git lease is not a GitHub Ruleset or a third-party
        # guarantee; it only refuses deletion if the remote ref has moved.
        return self.lease_delete(task, expected)

    def advance_issue_outside_archive(self, task: str, old: str) -> str:
        v = self.store.validation()
        source = v.issue(old, task)
        candidate = construct(
            dict(source.snapshot.files), (old,), nonce="synthetic-outside-actor",
        )
        self.source.install(candidate)
        self.source.set_ref(issue_ref(task), candidate.commit, old)
        self.push(issue_ref(task))
        return candidate.commit


class OrdinaryGitTransportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kumiki-337-ordinary-git-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fx = OrdinaryTransportFixture(self.root)

    def test_two_issues_normal_archive_append_cleanup_and_fresh_clone(self):
        fx = self.fx
        c1, record1 = fx.issue("101")
        c2, record2 = fx.issue("202")
        self.assertEqual(fx.source.genesis,
                         fx.store.validation().issue(c1, "101").parents[0])
        self.assertEqual(fx.source.genesis,
                         fx.store.validation().issue(c2, "202").parents[0])
        j1 = fx.archive("101")
        self.assertEqual((fx.source.genesis, c1),
                         fx.store.validation().archive(j1).parents)
        self.assertTrue(fx.cleanup("101", c1))
        self.assertIsNone(fx.remote_oid(issue_ref("101")))
        j2 = fx.archive("202")
        self.assertEqual((j1, c2), fx.store.validation().archive(j2).parents)
        self.assertTrue(fx.cleanup("202", c2))
        self.assertIsNone(fx.remote_oid(issue_ref("202")))
        self.assertEqual(j2, fx.remote_oid(ARCHIVE_REF))
        self.assertEqual(fx.main_oid, fx.remote_oid("refs/heads/main"))

        # Fresh independent clone after both temporary Issue refs vanished.
        fresh = self.root / "fresh-clone.git"
        fx.source.git(
            "clone", "--bare", "--no-hardlinks",
            fx.remote_url, str(fresh), bare=False,
        )
        fx.active_reader_repo = fresh
        reader = MetadataV2(
            REPOSITORY, fx.source.genesis, fx.read_object, fx.observe,
            limits=Limits(seconds=30),
        )
        obj1 = reader.read_object(
            c1, "task-record", record1, task="101", subject=SUBJECT,
        )
        obj2 = reader.read_object(
            c2, "task-record", record2, task="202", subject=SUBJECT,
        )
        self.assertEqual(obj1["payload"]["branch_ref"], "refs/heads/product-101")
        self.assertEqual(obj2["payload"]["branch_ref"], "refs/heads/product-202")
        # The locator must also recover exact immutable Contracts through
        # each original historical Issue commit, not merely validate them
        # indirectly while traversing the Archive DAG.
        for commit, record, task in ((c1, obj1, "101"), (c2, obj2, "202")):
            contract_id = record["payload"]["contract_id"]
            contract = reader.read_object(
                commit, "contract", contract_id, task=task, subject=SUBJECT,
            )
            self.assertEqual(
                contract["payload"],
                {"schema_version": 1, "title": f"Synthetic {task}",
                 "body": f"Independent fixture for {task}"},
            )
        self.assertEqual(j2, fx.remote_oid(ARCHIVE_REF))
        self.assertEqual(fx.main_oid, fx.remote_oid("refs/heads/main"))

    def test_stale_nonforce_archive_append_refused_by_plain_git(self):
        fx = self.fx
        c1, _ = fx.issue("101")
        c2, _ = fx.issue("202")
        j1 = fx.archive("101")
        v = fx.store.validation()
        before = v.archive(fx.source.genesis)
        issue2 = v.issue(c2, "202")
        stale = construct(
            union(before.snapshot, issue2.snapshot),
            (fx.source.genesis, c2), nonce="stale-archive",
        )
        fx.source.install(stale)
        stale_local = "refs/heads/fixture-stale-archive"
        fx.source.set_ref(stale_local, stale.commit)
        self.assertEqual(
            b"1", fx.source.git(
                "push", "--porcelain", "--no-verify", fx.remote_url,
                f"{stale_local}:{ARCHIVE_REF}", check=False,
            ),
        )
        self.assertEqual(j1, fx.remote_oid(ARCHIVE_REF))
        self.assertEqual(fx.main_oid, fx.remote_oid("refs/heads/main"))
        j2 = fx.archive("202")
        self.assertEqual((j1, c2), fx.store.validation().archive(j2).parents)
        self.assertEqual(j2, fx.remote_oid(ARCHIVE_REF))
        self.assertEqual(c1, fx.remote_oid(issue_ref("101")))

    def test_stale_cleanup_lease_does_not_delete_advanced_issue(self):
        fx = self.fx
        c1, _ = fx.issue("101")
        self.assertFalse(fx.cleanup("101", c1))
        self.assertEqual(c1, fx.remote_oid(issue_ref("101")))
        j1 = fx.archive("101")
        c_new = fx.advance_issue_outside_archive("101", c1)
        self.assertNotEqual(c1, c_new)
        # A stale expected-old lease makes an ordinary Git push fail;
        # this test does not attribute rejection to client or receiver.
        # No Rulesets or separate GitHub App are involved.
        self.assertFalse(fx.lease_delete("101", c1))
        self.assertFalse(fx.cleanup("101", c1))
        self.assertEqual(c_new, fx.remote_oid(issue_ref("101")))
        self.assertEqual(j1, fx.remote_oid(ARCHIVE_REF))
        self.assertEqual(fx.main_oid, fx.remote_oid("refs/heads/main"))

    def test_lost_ack_refuses_repeating_same_first_record(self):
        fx = self.fx
        fx.fail_ack_after_push = True
        with self.assertRaises(UncertainPublication):
            fx.issue("101")
        first = fx.remote_oid(issue_ref("101"))
        self.assertIsNotNone(first)
        self.assertEqual(1, fx.write_count)
        self.assertEqual(fx.source.genesis, fx.remote_oid(ARCHIVE_REF))
        # Caller cannot assume a failed exception implies the push did
        # not occur. Repeating the same first-Record publication is refused.
        with self.assertRaises(InvalidMetadata):
            fx.issue("101")
        self.assertEqual(first, fx.remote_oid(issue_ref("101")))
        self.assertEqual(1, fx.write_count)
        self.assertEqual(fx.main_oid, fx.remote_oid("refs/heads/main"))


if __name__ == "__main__":
    unittest.main()
