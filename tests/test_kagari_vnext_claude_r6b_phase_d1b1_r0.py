"""#340 R6B D1B1 R0: synthetic-only evidence of existing RecoveryJournal limits.

No executor, model, credentials, repo mutation, or authorizing receipt.
Tests create only disposable private directories with invented identities.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

from tools.kagari_vnext_trusted_host import RecoveryJournal, TrustedHostError

_DOMAIN = b"kumiki-r6b-d1b1-r0-fixture/one-shot/v1\n"


def _stable_key(repository_id: int, issue: int) -> str:
    """TEST-ONLY invariant operation key, not a qualified Host registry."""
    if type(repository_id) is not int or repository_id < 1:
        raise ValueError("repo")
    if type(issue) is not int or issue < 1:
        raise ValueError("issue")
    return hashlib.sha256(_DOMAIN + f"{repository_id}:{issue}:generation-1".encode("ascii")).hexdigest()


def _record(label: str = "reserved") -> dict:
    """Invented data; matching it never proves live Host authority."""
    return {"schema": 1, "kind": "SYNTHETIC_TEST_ONLY", "state": label,
            "task_digest": "a" * 64, "policy_digest": "b" * 64,
            "no_replay": True}


class StandaloneRecoveryJournalEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="kumiki-d1b1-r0-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve(strict=True)
        self.root = self._root("journal")
        self.key = _stable_key(1138, 340)

    def _root(self, name: str) -> Path:
        root = self.base / name
        root.mkdir(mode=0o700)
        return root

    def _direct_record(self, key: str, raw: bytes) -> Path:
        """Create a 0600 direct fixture to exercise parsing rather than mode rejection."""
        path = self.root / key
        path.write_bytes(raw)
        path.chmod(0o600)
        return path

    def test_create_read_stable_key_in_existing_private_directory(self):
        journal = RecoveryJournal(self.root)
        journal.create(self.key, _record())
        self.assertEqual(journal.read(self.key), _record())
        self.assertEqual((self.root / self.key).stat().st_mode & 0o777, 0o600)

    def test_repeat_same_key_is_not_a_retry_even_with_equal_data(self):
        first = RecoveryJournal(self.root)
        second = RecoveryJournal(self.root)
        first.create(self.key, _record())
        with self.assertRaises(TrustedHostError):
            second.create(self.key, _record())
        self.assertEqual(first.read(self.key), _record())

    def test_test_only_key_constructor_uses_canonical_repository_issue(self):
        self.assertEqual(_stable_key(1138, 340), self.key)
        self.assertNotEqual(_stable_key(1138, 341), self.key)
        self.assertNotEqual(_stable_key(1139, 340), self.key)
        with self.assertRaises(ValueError):
            _stable_key(True, 340)
        with self.assertRaises(ValueError):
            _stable_key(1138, 0)

    def test_two_competing_writers_one_live_directory_have_one_winner(self):
        barrier = threading.Barrier(2)
        results = []
        lock = threading.Lock()

        def write(label):
            journal = RecoveryJournal(self.root)
            barrier.wait(timeout=5)
            try:
                journal.create(self.key, _record(label))
                item = ("created", label)
            except TrustedHostError:
                item = ("blocked", label)
            with lock:
                results.append(item)

        threads = [threading.Thread(target=write, args=(label,)) for label in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
            self.assertFalse(t.is_alive())
        self.assertEqual(sorted(item[0] for item in results), ["blocked", "created"])
        self.assertEqual(RecoveryJournal(self.root).read(self.key)["state"],
                         next(label for state, label in results if state == "created"))

    def test_partial_canonical_record_blocks_future_create(self):
        good = _stable_key(1138, 341)
        self._direct_record(good, b'{"state":"valid"}')
        self._direct_record(self.key, b'{"state":')
        journal = RecoveryJournal(self.root)
        self.assertEqual(journal.read(good), {"state": "valid"})
        with self.assertRaisesRegex(TrustedHostError, "invalid_json"):
            journal.read(self.key)
        with self.assertRaises(TrustedHostError):
            journal.create(self.key, _record())

    def test_noncanonical_record_blocks_future_create(self):
        self._direct_record(self.key, b'{"state": "invented"}')
        journal = RecoveryJournal(self.root)
        with self.assertRaisesRegex(TrustedHostError, "recovery_record_invalid"):
            journal.read(self.key)
        with self.assertRaises(TrustedHostError):
            journal.create(self.key, _record())

    def test_failed_file_fsync_never_licenses_a_second_create(self):
        journal = RecoveryJournal(self.root)
        with patch("tools.kagari_vnext_trusted_host.os.fsync", side_effect=OSError("synthetic")):
            with self.assertRaises(TrustedHostError):
                journal.create(self.key, _record())
        self.assertTrue((self.root / self.key).exists())
        with self.assertRaises(TrustedHostError):
            journal.create(self.key, _record())

    def test_failed_directory_fsync_never_licenses_a_second_create(self):
        original = os.fsync
        calls = []

        def maybe_fail(fd):
            calls.append(fd)
            if len(calls) == 2:
                self.assertTrue(stat.S_ISDIR(os.fstat(fd).st_mode))
                raise OSError("synthetic directory fsync failure")
            return original(fd)

        journal = RecoveryJournal(self.root)
        with patch("tools.kagari_vnext_trusted_host.os.fsync", side_effect=maybe_fail):
            with self.assertRaises(TrustedHostError):
                journal.create(self.key, _record())
        self.assertGreaterEqual(len(calls), 2)
        with self.assertRaises(TrustedHostError):
            journal.create(self.key, _record())

    def test_symlink_record_denied_without_following_target(self):
        target = self.base / "target"
        target.write_bytes(b"unrelated fixture")
        (self.root / self.key).symlink_to(target)
        journal = RecoveryJournal(self.root)
        with self.assertRaises(TrustedHostError):
            journal.read(self.key)
        with self.assertRaises(TrustedHostError):
            journal.create(self.key, _record())
        self.assertEqual(target.read_bytes(), b"unrelated fixture")

    def test_live_instance_rejects_root_replacement(self):
        journal = RecoveryJournal(self.root)
        moved = self.base / "old-root"
        self.root.rename(moved)
        self.root.mkdir(mode=0o700)
        with self.assertRaises(TrustedHostError):
            journal.read(self.key)
        with self.assertRaises(TrustedHostError):
            journal.create(self.key, _record())

    def test_nonexistent_root_constructor_fails_without_genesis(self):
        with self.assertRaises(TrustedHostError):
            RecoveryJournal(self.base / "nonexistent")

    def test_live_instance_rejects_removed_root(self):
        journal = RecoveryJournal(self.root)
        shutil.rmtree(self.root)
        with self.assertRaises(TrustedHostError):
            journal.read(self.key)

    def test_fresh_instance_can_accept_recreated_empty_root_limitation(self):
        old = RecoveryJournal(self.root)
        old.create(self.key, _record())
        shutil.rmtree(self.root)
        self.root.mkdir(mode=0o700)
        fresh = RecoveryJournal(self.root)
        self.assertIsNone(fresh.read(self.key))
        # New instance has no persisted identity/complete-history authority.

    def test_same_uid_record_deletion_allows_second_create_limitation(self):
        journal = RecoveryJournal(self.root)
        journal.create(self.key, _record("original"))
        (self.root / self.key).unlink()
        second = RecoveryJournal(self.root)
        self.assertIsNone(second.read(self.key))
        second.create(self.key, _record("recreated"))
        self.assertEqual(second.read(self.key)["state"], "recreated")
        # No real effect: demonstrates why at-most-once is NOT a guarantee.

    def test_two_independent_roots_can_both_accept_same_key_limitation(self):
        other = self._root("different-journal")
        a, b = RecoveryJournal(self.root), RecoveryJournal(other)
        a.create(self.key, _record("root-a"))
        b.create(self.key, _record("root-b"))
        self.assertEqual(a.read(self.key)["state"], "root-a")
        self.assertEqual(b.read(self.key)["state"], "root-b")
        # This deliberately demonstrates the absence of cross-root CAS.

    def test_absent_key_is_not_authenticated_absence_or_launch_permission(self):
        self.assertIsNone(RecoveryJournal(self.root).read(self.key))
        # The API returns no provenance, inventory proof, or dispatch grant.

    def test_result_record_and_self_claimed_completion_are_not_proof(self):
        journal = RecoveryJournal(self.root)
        result_key = hashlib.sha256(("synthetic-result:" + self.key).encode()).hexdigest()
        forged = {"schema": 1, "state": "RESULT_CLAIMED",
                  "task_completed": True, "dispatch_allowed": True}
        journal.create(result_key, forged)
        self.assertEqual(journal.read(result_key), forged)
        self.assertIsNone(journal.read(self.key))
        # An arbitrary caller-authored field must never become KAGARI authority.

    def test_private_root_requirements_refuse_unsafe_directory(self):
        self.root.chmod(0o755)
        with self.assertRaises(TrustedHostError):
            RecoveryJournal(self.root)

if __name__ == "__main__":
    unittest.main()
