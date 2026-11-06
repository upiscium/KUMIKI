"""#337 Phase 2D1: disposable Git v2 PR checkpoint / historical reader tests.

Only local bare-Git and fake GitHub callbacks. Not real remote CAS, Ruleset,
atomic comment uniqueness, Product Task, model, install, release or deployment.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "components/agent-core-v4"))

from kagari_metadata_v2_host_binding import RegisteredIssue, RepositoryIdentity
from kagari_metadata_v2_pr_checkpoint import (
    VERSION, MARKER, V2PRCheckpoint, CommentFacts, PullFacts,
    CheckpointRefused, CheckpointUncertain, ExactCheckpointReader,
    SourceOnlyCheckpointPublisher, decode_checkpoint, render_checkpoint,
)
from kagari_vnext_trusted_host import RecoveryJournal
from metadata_v2 import ARCHIVE_REF, Limits, RefState, issue_ref
from metadata_v2_archive import archive_issue
from task_record import encode_contract, encode_record
from task_view import encode_snapshot
from tests.test_metadata_v2 import Fixture, REPOSITORY, SUBJECT

REPO_ID = 424242
PR = 235
PRINCIPAL = 8585
BRANCH = "refs/heads/task-101"
BASE = "refs/heads/main"


class CheckpointV2Test(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="metadata-v2-checkpoint-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.fixture = Fixture(self.root)
        self.store = self.fixture.store()
        self.identity = RepositoryIdentity(REPO_ID, REPOSITORY, self.fixture.genesis, "sha1")
        contract_id, contract = encode_contract(REPOSITORY, "101", SUBJECT, {
            "schema_version": 1, "title": "Issue 101", "body": "reviewed scope"})
        record_id, record = encode_record(REPOSITORY, "101", SUBJECT, {
            "schema_version": 1, "branch_ref": BRANCH, "contract_id": contract_id})
        self.record_id, self.contract_id = record_id, contract_id
        self.first = self.store.publish_issue(
            "101", [contract, record], record_update=(None, record_id))
        self.registered = RegisteredIssue(
            REPO_ID, REPOSITORY, 101, BRANCH, SUBJECT, record_id, contract_id)
        self.comments: list[CommentFacts] = []
        self.next_comment = 501
        self.post_calls = 0
        self.pr = PullFacts(
            REPO_ID, REPOSITORY, PR, BASE, BRANCH, SUBJECT, "open", True, PRINCIPAL)
        self.writer_error = False
        self.before_comment_read = None
        self.reader = self.bind()
        self.jpath = self.root / "journal"
        self.jpath.mkdir(mode=0o700)
        self.journal = RecoveryJournal(self.jpath)
        self.stream_lock = threading.RLock()
        self.publisher = self.publisher_for()
        self.initial = self.add_snapshot(self.first, boundary="initial")

    def view(self, authority_commit: str, *, previous=None, subject=SUBJECT,
             github_state="open", draft=True, pr_number=PR,
             remote_head=SUBJECT, branch=BRANCH) -> dict:
        return {
            "schema_version": 1, "repository": REPOSITORY, "task": "101",
            "subject": subject, "branch_ref": branch,
            "git": {
                "head": subject, "tree": "b" * 40, "branch_ref": branch,
                "branch_matches_task": True,
                "status": {k: 0 for k in (
                    "index_changes", "worktree_changes", "untracked",
                    "conflicts", "submodule_changes")},
                "worktrees": [], "default_branch": {"state": "not_requested"},
                "remote_head": {"state": "observed", "head_oid": remote_head},
                "submodule_worktrees": "not_inspected", "staged_gitlinks": 0,
            },
            "authority": {
                "state": "observed", "metadata_commit": authority_commit,
                "record_id": self.record_id, "contract_id": self.contract_id,
                "base_revision": SUBJECT, "branch_ref": branch,
                "disposition": None,
            },
            "evidence": [],
            "github": {
                "state": "observed",
                "pull_request": {
                    "base_repository": REPOSITORY,
                    "head_repository": REPOSITORY,
                    "number": pr_number,
                    "state": github_state, "draft": draft,
                    "base_ref": BASE.removeprefix("refs/heads/"),
                    "head_ref": branch.removeprefix("refs/heads/"),
                    "head_oid": subject,
                },
            },
            "previous_checkpoint": previous if previous is not None else {"state": "absent"},
        }

    def add_snapshot(self, authority_commit, *, boundary="initial",
                     previous=None, **view_changes):
        view = self.view(authority_commit, previous=previous, **view_changes)
        kind = "explicit-handoff" if boundary == "initial" else boundary
        snapshot_id, raw = encode_snapshot(REPOSITORY, "101", view["subject"], kind, view)
        commit = self.store.publish_issue("101", [raw])
        return V2PRCheckpoint(
            VERSION, REPO_ID, REPOSITORY, 101, PR, PRINCIPAL,
            BRANCH, BASE, view["subject"], commit, self.record_id,
            self.contract_id, snapshot_id, boundary, "initial" if boundary == "initial"
            else "turn-1", "bounded result",
        )

    def observe_comment(self, pr_number, comment_id, deadline):
        self.assertLess(time.monotonic(), deadline)
        if self.before_comment_read is not None:
            self.before_comment_read()
        for item in self.comments:
            if item.pr_number == pr_number and item.comment_id == comment_id:
                return item
        raise LookupError("fixture comment missing")

    def inventory(self, pr_number, deadline):
        self.assertLess(time.monotonic(), deadline)
        return tuple(c for c in self.comments if c.pr_number == pr_number)

    def observe_pr(self, pr_number, deadline):
        self.assertLess(time.monotonic(), deadline)
        self.assertEqual(pr_number, PR)
        return self.pr

    def post(self, pr_number, body, deadline):
        self.assertLess(time.monotonic(), deadline)
        self.post_calls += 1
        if not self.writer_error or self.writer_error == "after":
            self.next_comment += 1
            self.comments.append(CommentFacts(
                REPO_ID, REPOSITORY, pr_number, self.next_comment, PRINCIPAL, body))
        if self.writer_error:
            raise TimeoutError("synthetic lost acknowledgement")
        return self.next_comment

    def bind(self, *, registered=None, reader=None, observer=None,
             comment_reader=None, principal=PRINCIPAL):
        return ExactCheckpointReader(
            self.identity, registered or self.registered, base_ref=BASE,
            principal=principal, reader=reader or self.fixture.reader,
            observer=observer or self.fixture.observe,
            comment_reader=comment_reader or self.observe_comment)

    @contextmanager
    def stream_lease(self, value, deadline):
        """Synthetic Host-owned lock + *all-key* durable journal clearance.

        Real Hosts must bound the inventory and authenticate every receipt;
        fixture-only files are within the disposable journal directory.
        """
        self.assertLess(time.monotonic(), deadline)
        with self.stream_lock:
            # The synthetic lease is a Host stand-in: invalid text cannot
            # represent a positively verified original-body receipt.
            def safe_digest(body):
                try:
                    return (hashlib.sha256(body.encode("utf-8")).hexdigest()
                            if type(body) is str else None)
                except UnicodeError:
                    return None

            cleared = True
            for path in self.jpath.iterdir():
                record = json.loads(path.read_text())
                if ((record["repository_id"], record["issue"],
                     record["pr_number"], record["principal"])
                    != (value.repository_id, value.issue,
                        value.pr_number, value.principal)
                    or record["key"] == value.key):
                    continue
                matches = [
                    comment for comment in self.comments
                    if comment.pr_number == value.pr_number
                    and comment.author_id == value.principal
                    and safe_digest(comment.body) == record["body_sha256"]
                ]
                if len(matches) != 1:
                    cleared = False
                    break
                try:
                    self.reader.read(value.pr_number, matches[0].comment_id,
                                     deadline=deadline)
                except CheckpointRefused:
                    cleared = False
                    break
            yield cleared

    def publisher_for(self, *, grant=None, post=None, journal=None,
                      inventory=None, pull=None, stream_lease=None):
        return SourceOnlyCheckpointPublisher(
            self.reader, pull_reader=pull or self.observe_pr,
            inventory=inventory or self.inventory,
            authorize=grant or (lambda *_: True),
            post_comment=post or self.post,
            journal=journal or self.journal,
            stream_lease=stream_lease or self.stream_lease)

    def add_comment(self, value, *, author=PRINCIPAL, body=None):
        self.next_comment += 1
        comment = CommentFacts(REPO_ID, REPOSITORY, PR, self.next_comment, author,
                               body if body is not None else render_checkpoint(value))
        self.comments.append(comment)
        return comment

    def test_v2_closed_capsule_and_exact_original_read(self):
        body = render_checkpoint(self.initial)
        self.assertEqual(self.initial, decode_checkpoint(body))
        post = self.add_comment(self.initial)
        result = self.reader.read(PR, post.comment_id)
        self.assertEqual(post.comment_id, result.comment_id)
        self.assertEqual(self.initial.metadata_commit, result.checkpoint.metadata_commit)
        self.assertEqual("explicit-handoff", result.snapshot["boundary"])
        self.assertFalse(hasattr(self.reader, "write"))
        self.assertFalse(hasattr(self.reader, "delete"))

    def test_archive_delete_new_clone_preserves_exact_comment_history(self):
        old_oid = self.initial.metadata_commit
        old_comment = self.add_comment(self.initial)
        old_root = self.store.validation().issue(old_oid, "101")
        self.assertEqual("issue", old_root.role)
        archive1 = archive_issue(self.store, "101", retention_policy="fixture-only",
                                 verify_terminal=lambda *_: True,
                                 archive_writer=self.fixture.writer)
        self.fixture.git("update-ref", "--no-deref", "-d", issue_ref("101"),
                         old_oid)
        # A different Issue may advance the aggregate later.
        other_contract_id, contract = encode_contract(
            REPOSITORY, "202", SUBJECT, {
                "schema_version": 1, "title": "Second", "body": "independent"})
        other_record_id, record = encode_record(REPOSITORY, "202", SUBJECT, {
            "schema_version": 1, "branch_ref": "refs/heads/task-202",
            "contract_id": other_contract_id})
        second = self.store.publish_issue(
            "202", [contract, record], record_update=(None, other_record_id))
        archive2 = archive_issue(self.store, "202", retention_policy="fixture-only",
                                 verify_terminal=lambda *_: True,
                                 archive_writer=self.fixture.writer)
        self.assertNotEqual(archive1, archive2)
        self.assertNotEqual(old_oid, archive2)
        clone_dir = self.root / "clean-clone.git"
        self.fixture.git("clone", "--bare", "--no-hardlinks",
                         str(self.fixture.bare), str(clone_dir), bare=False)
        clone = object.__new__(Fixture)
        clone.root, clone.bare = self.root, clone_dir
        reader = self.bind(reader=clone.reader, observer=clone.observe)
        self.assertIsNone(clone.observe(issue_ref("101"),
                         time.monotonic() + 2).commit)
        restored = reader.read(PR, old_comment.comment_id)
        self.assertEqual(old_oid, restored.checkpoint.metadata_commit)
        self.assertEqual(self.initial.snapshot_id, restored.checkpoint.snapshot_id)
        with self.assertRaises(CheckpointRefused):
            reader.read(PR, old_comment.comment_id + 1)

    def test_exact_comment_never_uses_latest_checkpoint_or_record_pointer(self):
        old = self.add_comment(self.initial)
        next_record_id, updated = encode_record(REPOSITORY, "101", SUBJECT, {
            "schema_version": 1, "branch_ref": BRANCH,
            "contract_id": self.contract_id,
            "disposition": {"kind": "cancelled"},
        })
        latest = self.store.publish_issue(
            "101", [updated], record_update=(self.record_id, next_record_id))
        self.assertEqual(self.initial.metadata_commit,
                         self.reader.read(PR, old.comment_id).checkpoint.metadata_commit)
        new_registration = dataclasses.replace(
            self.registered, record_id=next_record_id)
        with self.assertRaises(CheckpointRefused):
            self.bind(registered=new_registration).read(PR, old.comment_id)
        with self.assertRaises(CheckpointRefused):
            self.reader.read(PR, latest)
        with self.assertRaises(CheckpointRefused):
            self.reader.read(PR, old.comment_id + 1)

    def test_render_and_decoder_reject_v1_spoof_extra_duplicate_and_changed_bytes(self):
        body = render_checkpoint(self.initial)
        attempts = [
            body.replace(MARKER, "agentcore-task-checkpoint/v1", 1),
            body.replace("Task checkpoint", "Different checkpoint", 1),
            body.replace("bounded result", "untrusted result", 1),
            body.replace("sha256=", "sha256=0", 1),
            body + "\nextra",
            body.replace("Capsule: ", "Capsule: !!", 1),
        ]
        for altered in attempts:
            with self.subTest(altered=altered[-60:]), self.assertRaises(CheckpointRefused):
                decode_checkpoint(altered)
        raw = base64.b64decode(body.rpartition("Capsule: ")[2])
        bogus = raw.replace(b'"version":2', b'"version":2,"version":2')
        duplicate = body.rpartition("Capsule: ")[0] + "Capsule: " + base64.b64encode(
            bogus).decode("ascii")
        with self.assertRaises(CheckpointRefused):
            decode_checkpoint(duplicate)
        mapping = dataclasses.asdict(self.initial)
        for change in [
            {"version": 1}, {"version": True}, {"repository_id": True},
            {"snapshot_id": "a"}, {"key": "initial-alt"}, {"boundary": "turn-end"},
            {"report": "\x00"}, {"report": "x" * 5000},
        ]:
            with self.subTest(change=change), self.assertRaises(CheckpointRefused):
                V2PRCheckpoint.from_mapping({**mapping, **change})
        with self.assertRaises(CheckpointRefused):
            V2PRCheckpoint.from_mapping({**mapping, "latest": True})
        with self.assertRaises(CheckpointRefused):
            V2PRCheckpoint.from_mapping({k: v for k, v in mapping.items()
                                        if k != "metadata_commit"})

    def test_reader_refuses_foreign_comment_identity_author_body_changes(self):
        original = self.add_comment(self.initial)
        for altered in [
            dataclasses.replace(original, author_id=3),
            dataclasses.replace(original, repository_id=2),
            dataclasses.replace(original, repository="other/widgets"),
            dataclasses.replace(original, kind="issue"),
            dataclasses.replace(original, pr_number=PR + 1),
            dataclasses.replace(original, comment_id=original.comment_id + 4),
            dataclasses.replace(original, body=original.body + "\n"),
        ]:
            self.comments[0] = altered
            with self.subTest(altered=altered), self.assertRaises(CheckpointRefused):
                self.reader.read(PR, original.comment_id)
        self.comments[0] = original
        calls = 0
        def change_mid_read():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.comments[0] = dataclasses.replace(
                    original, body=original.body + " ")
        self.before_comment_read = change_mid_read
        with self.assertRaises(CheckpointRefused):
            self.reader.read(PR, original.comment_id)

    def test_author_self_consistent_spoof_is_not_host_attested_principal(self):
        outsider = dataclasses.replace(self.initial, principal=424243)
        comment = self.add_comment(outsider, author=424243)
        # A forged capsule can be internally valid; its actor is not the
        # authorized principal of this Host-registered PR session.
        self.assertEqual(outsider, decode_checkpoint(comment.body))
        with self.assertRaisesRegex(CheckpointRefused, "registration_mismatch"):
            self.reader.read(PR, comment.comment_id)
        with self.assertRaises(CheckpointRefused):
            self.bind(principal=True)

    def test_reader_refuses_invalid_snapshot_binding_and_missing_graph(self):
        for kwargs in [
            {"github_state": "closed"},
            {"draft": False},
            {"pr_number": PR + 1},
            {"remote_head": "f" * 40},
        ]:
            candidate = self.add_snapshot(self.store.validation().issue(
                self.fixture.observe(issue_ref("101"), time.monotonic() + 2).commit,
                "101").parents[-1], **kwargs)
            # Keep the original record at its pinned commit while checking a
            # well-formed but inappropriate Task View PR observation.
            comment = self.add_comment(candidate)
            with self.subTest(kwargs=kwargs), self.assertRaises(CheckpointRefused):
                self.reader.read(PR, comment.comment_id)
        old = self.add_comment(self.initial)
        with self.assertRaises(CheckpointRefused):
            self.bind(observer=lambda ref, deadline: RefState(
                ref, None, True) if ref == ARCHIVE_REF
                else self.fixture.observe(ref, deadline)).read(PR, old.comment_id)

    def test_publisher_validated_grant_fsync_exact_readback_idempotent(self):
        first = self.publisher.publish(self.initial)
        self.assertEqual(1, self.post_calls)
        self.assertEqual(first, self.publisher.publish(self.initial))
        self.assertEqual(1, self.post_calls)
        self.assertEqual(1, len(list(self.jpath.iterdir())))
        self.assertEqual(self.initial.metadata_commit,
                         first.checkpoint.metadata_commit)

    def test_publisher_lost_ack_after_post_reconciles_original_id(self):
        self.writer_error = "after"
        result = self.publisher.publish(self.initial)
        self.assertEqual(self.next_comment, result.comment_id)
        self.assertEqual(1, self.post_calls)
        self.assertEqual(result, self.publisher.publish(self.initial))
        self.assertEqual(1, self.post_calls)

    def test_publisher_no_ack_before_post_never_replays(self):
        self.writer_error = "before"
        with self.assertRaises(CheckpointUncertain):
            self.publisher.publish(self.initial)
        self.assertEqual(1, self.post_calls)
        self.writer_error = False
        with self.assertRaises(CheckpointUncertain):
            self.publisher.publish(self.initial)
        self.assertEqual(1, self.post_calls)
        self.assertEqual(1, len(list(self.jpath.iterdir())))

    def test_publisher_requires_grant_and_current_exact_pr_without_writes(self):
        with self.assertRaises(CheckpointRefused):
            self.publisher_for(grant=lambda *_: False).publish(self.initial)
        self.assertEqual(0, self.post_calls)
        self.assertEqual([], list(self.jpath.iterdir()))
        for state in [
            dataclasses.replace(self.pr, draft=False),
            dataclasses.replace(self.pr, head_oid="b" * 40),
            dataclasses.replace(self.pr, repository_id=99),
            dataclasses.replace(self.pr, state="merged"),
        ]:
            with self.subTest(pr=state):
                self.pr = state
                with self.assertRaises(CheckpointRefused):
                    self.publisher.publish(self.initial)
        self.assertEqual(0, self.post_calls)
        self.assertEqual([], list(self.jpath.iterdir()))

    def test_publisher_key_conflict_and_duplicate_refusal(self):
        comment = self.add_comment(self.initial)
        self.assertEqual(comment.comment_id,
                         self.publisher.publish(self.initial).comment_id)
        self.assertEqual(0, self.post_calls)
        conflicting = dataclasses.replace(self.initial, report="different")
        with self.assertRaises(CheckpointRefused):
            self.publisher.publish(conflicting)
        self.add_comment(self.initial)
        with self.assertRaises(CheckpointRefused):
            self.publisher.publish(self.initial)
        self.assertEqual(0, self.post_calls)

    def test_noninitial_exact_previous_comment_and_graph(self):
        first = self.publisher.publish(self.initial)
        previous = {
            "state": "observed",
            "checkpoint_id": str(first.comment_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn = self.add_snapshot(self.initial.metadata_commit, boundary="turn-end",
                                 previous=previous)
        with self.assertRaises(CheckpointRefused):
            self.publisher.publish(turn)
        with self.assertRaises(CheckpointRefused):
            self.publisher.publish(turn, previous_comment_id=999)
        second = self.publisher.publish(turn, previous_comment_id=first.comment_id)
        self.assertEqual(turn.metadata_commit, second.checkpoint.metadata_commit)
        self.assertEqual(2, self.post_calls)
        self.assertEqual(second.comment_id, self.publisher.publish(
            turn, previous_comment_id=first.comment_id).comment_id)

    def test_pre_effect_drift_after_grant_blocks_comment_post(self):
        def mutate(_payload, _body, _deadline):
            self.pr = dataclasses.replace(self.pr, head_oid="b" * 40)
            return True
        with self.assertRaises(CheckpointRefused):
            self.publisher_for(grant=mutate).publish(self.initial)
        self.assertEqual(0, self.post_calls)
        self.assertEqual([], list(self.jpath.iterdir()))

    def test_journal_conflict_blocks_altered_body_even_if_prior_post_unknown(self):
        self.writer_error = "before"
        with self.assertRaises(CheckpointUncertain):
            self.publisher.publish(self.initial)
        self.writer_error = False
        with self.assertRaises(CheckpointRefused):
            self.publisher.publish(dataclasses.replace(self.initial, report="new"))
        self.assertEqual(1, self.post_calls)

    def test_symbolic_archive_or_unreachable_original_commit_fail_closed(self):
        post = self.add_comment(self.initial)
        self.fixture.set_ref("refs/heads/foreign-archive", self.fixture.genesis)
        self.fixture.git("update-ref", "--no-deref", "-d",
                         ARCHIVE_REF, self.fixture.genesis)
        self.fixture.git("symbolic-ref", ARCHIVE_REF, "refs/heads/foreign-archive")
        with self.assertRaises(CheckpointRefused):
            self.reader.read(PR, post.comment_id)

    def test_pending_unknown_other_key_blocks_stream_without_new_effect(self):
        first = self.publisher.publish(self.initial)
        previous = {
            "state": "observed", "checkpoint_id": str(first.comment_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn1 = self.add_snapshot(self.initial.metadata_commit,
                                  boundary="turn-end", previous=previous)
        self.writer_error = "before"
        with self.assertRaises(CheckpointUncertain):
            self.publisher.publish(turn1, previous_comment_id=first.comment_id)
        self.assertEqual(2, self.post_calls)
        self.writer_error = False
        turn2 = dataclasses.replace(turn1, key="turn-2")
        with self.assertRaisesRegex(CheckpointRefused, "stream_pending"):
            self.publisher.publish(turn2, previous_comment_id=first.comment_id)
        self.assertEqual(2, self.post_calls)
        self.assertEqual(2, len(list(self.jpath.iterdir())))

    def test_turn_requires_valid_exact_initial_not_just_owned_marker(self):
        first = self.add_comment(self.initial)
        initial_prev = {
            "state": "observed", "checkpoint_id": str(first.comment_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn1 = self.add_snapshot(self.initial.metadata_commit,
                                  boundary="turn-end", previous=initial_prev)
        second = self.add_comment(turn1)
        next_prev = {
            "state": "observed", "checkpoint_id": str(second.comment_id),
            "subject": turn1.subject, "metadata_commit": turn1.metadata_commit,
            "snapshot_id": turn1.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn2 = dataclasses.replace(
            self.add_snapshot(turn1.metadata_commit,
                              boundary="turn-end", previous=next_prev),
            key="turn-2",
        )
        corrupt = dataclasses.replace(self.initial, metadata_commit="f" * 40)
        self.comments[0] = dataclasses.replace(
            first, body=render_checkpoint(corrupt))
        self.assertEqual(corrupt, decode_checkpoint(self.comments[0].body))
        with self.assertRaises(CheckpointRefused):
            self.publisher.publish(turn2, previous_comment_id=second.comment_id)
        self.assertEqual(0, self.post_calls)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_existing_turn_adoption_must_verify_explicit_previous(self):
        first = self.add_comment(self.initial)
        previous = {
            "state": "observed", "checkpoint_id": str(first.comment_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn = self.add_snapshot(self.initial.metadata_commit,
                                 boundary="turn-end", previous=previous)
        recorded = self.add_comment(turn)
        with self.assertRaises(CheckpointRefused):
            self.publisher.publish(turn, previous_comment_id=999)
        with self.assertRaises(CheckpointRefused):
            self.publisher.publish(turn)
        confirmed = self.publisher.publish(turn,
                                           previous_comment_id=first.comment_id)
        self.assertEqual(recorded.comment_id, confirmed.comment_id)
        self.assertEqual(0, self.post_calls)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_readback_pr_drift_blocks_adoption_and_uncertain_after_post(self):
        self.add_comment(self.initial)
        def mutate_pr():
            self.pr = dataclasses.replace(self.pr, head_oid="b" * 40)
        self.before_comment_read = mutate_pr
        with self.assertRaisesRegex(CheckpointRefused, "pr_not_exact"):
            self.publisher.publish(self.initial)
        self.assertEqual(0, self.post_calls)

        # Separate fresh fixture of the writer's effect-time window.
        self.comments.clear()
        self.before_comment_read = mutate_pr
        self.pr = dataclasses.replace(self.pr, head_oid=SUBJECT)
        with self.assertRaises(CheckpointUncertain):
            self.publisher.publish(self.initial)
        self.assertEqual(1, self.post_calls)
        self.assertEqual(1, len(list(self.jpath.iterdir())))

    def test_stream_guard_is_required_and_literal_true(self):
        @contextmanager
        def deny(_value, _deadline):
            yield False
        with self.assertRaisesRegex(CheckpointRefused, "stream_pending"):
            self.publisher_for(stream_lease=deny).publish(self.initial)
        self.assertEqual(0, self.post_calls)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_broken_intermediate_pr_comment_chain_refused_new_and_adoption(self):
        first = self.add_comment(self.initial)
        # The Metadata Snapshot link is valid but this *comment ID* is not.
        dangling = {
            "state": "observed", "checkpoint_id": "999",
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn1 = self.add_snapshot(self.initial.metadata_commit,
                                  boundary="turn-end", previous=dangling)
        second = self.add_comment(turn1)
        valid_immediate = {
            "state": "observed", "checkpoint_id": str(second.comment_id),
            "subject": turn1.subject, "metadata_commit": turn1.metadata_commit,
            "snapshot_id": turn1.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn2 = dataclasses.replace(
            self.add_snapshot(turn1.metadata_commit,
                              boundary="turn-end", previous=valid_immediate),
            key="turn-2",
        )
        with self.assertRaises(CheckpointRefused):
            self.publisher.publish(turn2, previous_comment_id=second.comment_id)
        self.add_comment(turn2)
        with self.assertRaises(CheckpointRefused):
            self.publisher.publish(turn2, previous_comment_id=second.comment_id)
        self.assertEqual(0, self.post_calls)
        self.assertEqual([], list(self.jpath.iterdir()))

    def test_unknown_commits_after_refused_new_and_adoption(self):
        first = self.add_comment(self.initial)
        previous = {
            "state": "observed", "checkpoint_id": str(first.comment_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "unknown", "reason": "observation_failed"},
        }
        turn = self.add_snapshot(self.initial.metadata_commit,
                                 boundary="turn-end", previous=previous)
        with self.assertRaisesRegex(CheckpointRefused, "observation_unknown"):
            self.publisher.publish(turn, previous_comment_id=first.comment_id)
        self.add_comment(turn)
        with self.assertRaisesRegex(CheckpointRefused, "observation_unknown"):
            self.publisher.publish(turn, previous_comment_id=first.comment_id)
        self.assertEqual(0, self.post_calls)
        self.assertEqual([], list(self.jpath.iterdir()))

    def test_post_parent_deletion_is_uncertain_without_second_effect(self):
        initial = self.publisher.publish(self.initial)
        previous = {
            "state": "observed", "checkpoint_id": str(initial.comment_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn = self.add_snapshot(self.initial.metadata_commit,
                                 boundary="turn-end", previous=previous)

        def delete_parent(pr_number, body, deadline):
            self.comments[:] = [comment for comment in self.comments
                                if comment.comment_id != initial.comment_id]
            return self.post(pr_number, body, deadline)

        with self.assertRaises(CheckpointUncertain):
            self.publisher_for(post=delete_parent).publish(
                turn, previous_comment_id=initial.comment_id)
        self.assertEqual(2, self.post_calls)  # 1 initial + 1 uncertain turn
        self.assertEqual(2, len(list(self.jpath.iterdir())))

    def test_post_parent_valid_reencoding_is_uncertain(self):
        initial = self.publisher.publish(self.initial)
        previous = {
            "state": "observed", "checkpoint_id": str(initial.comment_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn = self.add_snapshot(self.initial.metadata_commit,
                                 boundary="turn-end", previous=previous)

        def edit_parent(pr_number, body, deadline):
            original = self.comments[0]
            edited = dataclasses.replace(self.initial, report="altered owner text")
            self.comments[0] = dataclasses.replace(
                original, body=render_checkpoint(edited))
            self.assertEqual(edited, decode_checkpoint(self.comments[0].body))
            return self.post(pr_number, body, deadline)

        with self.assertRaises(CheckpointUncertain):
            self.publisher_for(post=edit_parent).publish(
                turn, previous_comment_id=initial.comment_id)
        self.assertEqual(2, self.post_calls)
        self.assertEqual(2, len(list(self.jpath.iterdir())))

    def test_existing_successor_must_follow_its_direct_predecessor_id(self):
        initial = self.add_comment(self.initial)
        first_previous = {
            "state": "observed", "checkpoint_id": str(initial.comment_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn1 = self.add_snapshot(self.initial.metadata_commit,
                                  boundary="turn-end", previous=first_previous)
        self.next_comment = initial.comment_id + 1
        parent = self.add_comment(turn1)
        backdated_child_id = parent.comment_id - 1
        next_previous = {
            "state": "observed", "checkpoint_id": str(parent.comment_id),
            "subject": turn1.subject,
            "metadata_commit": turn1.metadata_commit,
            "snapshot_id": turn1.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn2 = dataclasses.replace(
            self.add_snapshot(turn1.metadata_commit,
                              boundary="turn-end", previous=next_previous),
            key="turn-2",
        )
        # Authenticated sorted inventory: child < parent while its
        # Snapshot refers forward to the later parent comment ID.
        self.comments.append(CommentFacts(
            REPO_ID, REPOSITORY, PR, backdated_child_id, PRINCIPAL,
            render_checkpoint(turn2)))
        self.comments.sort(key=lambda c: c.comment_id)
        self.assertEqual([initial.comment_id, backdated_child_id, parent.comment_id],
                         [c.comment_id for c in self.inventory(PR, time.monotonic() + 2)])
        # The stream-wide chain now rejects the backwards sibling before
        # the requested successor-specific order check runs.
        with self.assertRaisesRegex(CheckpointRefused, "stream_fork_or_order_invalid"):
            self.publisher.publish(turn2, previous_comment_id=parent.comment_id)
        self.assertEqual(0, self.post_calls)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_post_readback_rejects_nonincreasing_successor_id(self):
        initial = self.add_comment(self.initial)  # 501
        previous = {
            "state": "observed", "checkpoint_id": str(initial.comment_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn = self.add_snapshot(self.initial.metadata_commit,
                                 boundary="turn-end", previous=previous)

        def post_with_backdated_id(pr_number, body, deadline):
            self.post_calls += 1
            self.comments.append(CommentFacts(
                REPO_ID, REPOSITORY, pr_number, 500, PRINCIPAL, body))
            self.comments.sort(key=lambda c: c.comment_id)
            return 500

        with self.assertRaisesRegex(CheckpointUncertain, "post_outcome_uncertain"):
            self.publisher_for(post=post_with_backdated_id).publish(
                turn, previous_comment_id=initial.comment_id)
        self.assertEqual(1, self.post_calls)
        self.assertEqual(1, len(list(self.jpath.iterdir())))
        # The global initial-first invariant now detects this backward
        # sequence before the child-specific successor-order check.
        with self.assertRaisesRegex(CheckpointRefused, "initial_order_invalid"):
            self.publisher.publish(turn, previous_comment_id=initial.comment_id)
        self.assertEqual(1, self.post_calls)

    def test_initial_adoption_refuses_older_owned_turn(self):
        future_initial_id = self.next_comment + 2
        previous = {
            "state": "observed", "checkpoint_id": str(future_initial_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn = self.add_snapshot(self.initial.metadata_commit,
                                 boundary="turn-end", previous=previous)
        earlier_turn = self.add_comment(turn)
        later_initial = self.add_comment(self.initial)
        self.assertLess(earlier_turn.comment_id, later_initial.comment_id)
        self.assertEqual(future_initial_id, later_initial.comment_id)
        with self.assertRaisesRegex(CheckpointRefused, "initial_order_invalid"):
            self.publisher.publish(self.initial)
        with self.assertRaisesRegex(CheckpointRefused, "initial_order_invalid"):
            self.publisher.publish(turn, previous_comment_id=later_initial.comment_id)
        self.assertEqual(0, self.post_calls)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_initial_adoption_still_succeeds_with_later_owned_turn(self):
        earlier_initial = self.add_comment(self.initial)
        previous = {
            "state": "observed", "checkpoint_id": str(earlier_initial.comment_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn = self.add_snapshot(self.initial.metadata_commit,
                                 boundary="turn-end", previous=previous)
        later_turn = self.add_comment(turn)
        self.assertLess(earlier_initial.comment_id, later_turn.comment_id)
        result = self.publisher.publish(self.initial)
        self.assertEqual(earlier_initial.comment_id, result.comment_id)
        self.assertEqual(result, self.publisher.publish(self.initial))
        self.assertEqual(0, self.post_calls)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_initial_post_readback_with_earlier_owned_turn_is_uncertain(self):
        earlier_turn_id = self.next_comment + 1
        future_initial_id = self.next_comment + 2
        previous = {
            "state": "observed", "checkpoint_id": str(future_initial_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn = self.add_snapshot(self.initial.metadata_commit,
                                 boundary="turn-end", previous=previous)

        def post_with_earlier_turn(pr_number, body, deadline):
            self.assertLess(time.monotonic(), deadline)
            self.post_calls += 1
            self.comments.append(CommentFacts(
                REPO_ID, REPOSITORY, pr_number, earlier_turn_id, PRINCIPAL,
                render_checkpoint(turn)))
            self.comments.append(CommentFacts(
                REPO_ID, REPOSITORY, pr_number, future_initial_id, PRINCIPAL, body))
            self.next_comment = future_initial_id
            return future_initial_id

        with self.assertRaisesRegex(CheckpointUncertain, "post_outcome_uncertain"):
            self.publisher_for(post=post_with_earlier_turn).publish(self.initial)
        self.assertEqual(1, self.post_calls)
        self.assertEqual(1, len(list(self.jpath.iterdir())))
        with self.assertRaisesRegex(CheckpointRefused, "initial_order_invalid"):
            self.publisher.publish(self.initial)
        self.assertEqual(1, self.post_calls)

    def test_pull_facts_require_exact_boolean_and_numeric_types(self):
        original = self.pr
        invalid = [
            dataclasses.replace(original, draft=1),
            dataclasses.replace(original, draft="true"),
            dataclasses.replace(original, repository_id=float(REPO_ID)),
            dataclasses.replace(original, pr_number=float(PR)),
            dataclasses.replace(original, principal=float(PRINCIPAL)),
        ]
        for fact in invalid:
            with self.subTest(fact=fact), self.assertRaisesRegex(
                    CheckpointRefused, "pr_not_exact_open_draft"):
                self.pr = fact
                self.publisher.publish(self.initial)
        self.pr = original
        self.assertEqual(0, self.post_calls)
        self.assertEqual([], list(self.jpath.iterdir()))

    def test_forked_owned_comments_refuse_adoption_and_new_extension(self):
        initial = self.add_comment(self.initial)
        previous_initial = {
            "state": "observed", "checkpoint_id": str(initial.comment_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        branch_a = self.add_snapshot(self.initial.metadata_commit,
                                     boundary="turn-end", previous=previous_initial)
        branch_b = dataclasses.replace(
            self.add_snapshot(branch_a.metadata_commit,
                              boundary="turn-end", previous=previous_initial),
            key="turn-2",
        )
        a = self.add_comment(branch_a)
        b = self.add_comment(branch_b)
        self.assertEqual([initial.comment_id, a.comment_id, b.comment_id],
                         [c.comment_id for c in self.comments])
        for checkpoint, parent_id in [
            (self.initial, None),
            (branch_a, initial.comment_id),
            (branch_b, initial.comment_id),
        ]:
            with self.subTest(key=checkpoint.key), self.assertRaisesRegex(
                    CheckpointRefused, "stream_fork_or_order_invalid"):
                self.publisher.publish(checkpoint, previous_comment_id=parent_id)
        previous_b = {
            "state": "observed", "checkpoint_id": str(b.comment_id),
            "subject": branch_b.subject,
            "metadata_commit": branch_b.metadata_commit,
            "snapshot_id": branch_b.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        extension = dataclasses.replace(
            self.add_snapshot(branch_b.metadata_commit,
                              boundary="turn-end", previous=previous_b),
            key="turn-3",
        )
        with self.assertRaisesRegex(CheckpointRefused, "stream_fork_or_order_invalid"):
            self.publisher.publish(extension, previous_comment_id=b.comment_id)
        self.assertEqual(0, self.post_calls)
        self.assertEqual([], list(self.jpath.iterdir()))

    def test_valid_linear_three_comment_chain_supports_old_adoption(self):
        initial = self.publisher.publish(self.initial)
        previous_initial = {
            "state": "observed", "checkpoint_id": str(initial.comment_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        first = self.add_snapshot(self.initial.metadata_commit, boundary="turn-end",
                                  previous=previous_initial)
        first_comment = self.publisher.publish(first,
                                               previous_comment_id=initial.comment_id)
        previous_first = {
            "state": "observed", "checkpoint_id": str(first_comment.comment_id),
            "subject": first.subject,
            "metadata_commit": first.metadata_commit,
            "snapshot_id": first.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        second = dataclasses.replace(
            self.add_snapshot(first.metadata_commit, boundary="turn-end",
                              previous=previous_first), key="turn-2",
        )
        second_comment = self.publisher.publish(
            second, previous_comment_id=first_comment.comment_id)
        self.assertLess(initial.comment_id, first_comment.comment_id)
        self.assertLess(first_comment.comment_id, second_comment.comment_id)
        self.assertEqual(initial, self.publisher.publish(self.initial))
        self.assertEqual(first_comment, self.publisher.publish(
            first, previous_comment_id=initial.comment_id))
        self.assertEqual(second_comment, self.publisher.publish(
            second, previous_comment_id=first_comment.comment_id))
        self.assertEqual(3, self.post_calls)

    def test_post_competing_new_key_causes_uncertain_without_repost(self):
        initial = self.publisher.publish(self.initial)
        previous = {
            "state": "observed", "checkpoint_id": str(initial.comment_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        target = self.add_snapshot(self.initial.metadata_commit,
                                   boundary="turn-end", previous=previous)
        competing = dataclasses.replace(target, key="turn-2")

        def conflicting_effect(pr_number, body, deadline):
            self.assertLess(time.monotonic(), deadline)
            # An uncoordinated same-author client posts a valid sibling
            # immediately before this publisher's intended comment.
            self.add_comment(competing)
            return self.post(pr_number, body, deadline)

        with self.assertRaisesRegex(CheckpointUncertain, "post_outcome_uncertain"):
            self.publisher_for(post=conflicting_effect).publish(
                target, previous_comment_id=initial.comment_id)
        self.assertEqual(2, self.post_calls)  # initial and one intended turn
        self.assertEqual(2, len(list(self.jpath.iterdir())))
        with self.assertRaisesRegex(CheckpointRefused, "stream_fork_or_order_invalid"):
            self.publisher.publish(target, previous_comment_id=initial.comment_id)
        self.assertEqual(2, self.post_calls)  # no blind replay

    def test_historical_read_propagates_shared_deadline_to_git_and_refs(self):
        comment = self.add_comment(self.initial)
        observed_deadlines = []

        def bounded_object(key, maximum, deadline):
            observed_deadlines.append(deadline)
            return self.fixture.reader(key, maximum, deadline)

        def bounded_ref(ref, deadline):
            observed_deadlines.append(deadline)
            return self.fixture.observe(ref, deadline)

        # Give the inner Metadata validation a longer resource deadline so
        # the outer 20-second budget must be passed down as the I/O cutoff.
        reader = ExactCheckpointReader(
            self.identity, self.registered, base_ref=BASE,
            principal=PRINCIPAL, reader=bounded_object, observer=bounded_ref,
            comment_reader=self.observe_comment, limits=Limits(seconds=35),
        )
        shared_deadline = time.monotonic() + 20
        result = reader.read(PR, comment.comment_id, deadline=shared_deadline)
        self.assertEqual(comment.comment_id, result.comment_id)
        self.assertTrue(observed_deadlines)
        self.assertTrue(all(d <= shared_deadline for d in observed_deadlines))
        self.assertLess(time.monotonic(), shared_deadline)

    def test_historical_read_refuses_expired_or_unbounded_deadline(self):
        comment = self.add_comment(self.initial)
        for invalid in [time.monotonic() - 1, float("inf"),
                        float("nan"), time.monotonic() + 100]:
            with self.subTest(deadline=invalid), self.assertRaisesRegex(
                    CheckpointRefused, "deadline_invalid_or_expired"):
                self.reader.read(PR, comment.comment_id, deadline=invalid)
            with self.subTest(metadata_deadline=invalid), self.assertRaisesRegex(
                    CheckpointRefused, "deadline_invalid_or_expired"):
                self.reader.verify_metadata(self.initial, deadline=invalid)
        self.assertEqual(0, self.post_calls)

    def test_at_capacity_next_checkpoint_rejected_before_effect(self):
        initial = self.add_comment(self.initial)
        # Isolate the *capacity* pre-effect gate: the full-chain validator
        # has its own integration coverage. These 63 dummy snapshots are not
        # a real chain, and must never be passed to a production verifier.
        for i in range(1, 64):
            dummy = dataclasses.replace(
                self.initial, boundary="turn-end", key=f"fixture-{i}")
            self.add_comment(dummy)
        self.assertEqual(64, len(self.comments))
        previous = {
            "state": "observed", "checkpoint_id": str(initial.comment_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        next_turn = dataclasses.replace(
            self.add_snapshot(self.initial.metadata_commit,
                              boundary="turn-end", previous=previous),
            key="new-at-capacity",
        )
        publisher = self.publisher_for(
            grant=lambda *_: self.fail("must not request effect grant"))
        publisher._verify_owned_stream = lambda *_: None
        with self.assertRaisesRegex(CheckpointRefused, "stream_capacity_exceeded"):
            publisher.publish(next_turn, previous_comment_id=initial.comment_id)
        self.assertEqual(0, self.post_calls)
        self.assertEqual([], list(self.jpath.iterdir()))

    def test_comment_facts_exact_read_rejects_numeric_type_equivalence(self):
        source = self.add_comment(self.initial)
        invalid = [
            dataclasses.replace(source, repository_id=float(REPO_ID)),
            dataclasses.replace(source, pr_number=float(PR)),
            dataclasses.replace(source, comment_id=float(source.comment_id)),
            dataclasses.replace(source, author_id=float(PRINCIPAL)),
            dataclasses.replace(source, repository=42),
            dataclasses.replace(source, kind=True),
        ]
        for fact in invalid:
            with self.subTest(fact=fact), self.assertRaisesRegex(
                    CheckpointRefused, "comment_provenance_invalid"):
                reader = self.bind(comment_reader=lambda *_: fact)
                reader.read(PR, source.comment_id)
        self.assertEqual(0, self.post_calls)

    def test_comment_inventory_rejects_noncanonical_identity_types_before_effect(self):
        source = self.add_comment(self.initial)
        invalid = [
            dataclasses.replace(source, repository_id=float(REPO_ID)),
            dataclasses.replace(source, pr_number=float(PR)),
            dataclasses.replace(source, comment_id=float(source.comment_id)),
            dataclasses.replace(source, author_id=float(PRINCIPAL)),
            dataclasses.replace(source, repository=42),
            dataclasses.replace(source, kind=True),
        ]
        for fact in invalid:
            self.comments[:] = [fact]
            with self.subTest(fact=fact), self.assertRaisesRegex(
                    CheckpointRefused, "inventory_identity_invalid"):
                self.publisher.publish(self.initial)
        self.comments[:] = [source]
        self.assertEqual(source.comment_id,
                         self.publisher.publish(self.initial).comment_id)
        self.assertEqual(0, self.post_calls)
        self.assertFalse(list(self.jpath.iterdir()))

    def test_unpaired_surrogate_in_exact_comment_is_refused_not_raw_unicode(self):
        comment = self.add_comment(self.initial)
        malformed = dataclasses.replace(
            comment, body='{"bad": "\ud800"}')
        with self.assertRaisesRegex(CheckpointRefused, "comment_provenance_invalid"):
            self.bind(comment_reader=lambda *_: malformed).read(
                PR, comment.comment_id)
        with self.assertRaisesRegex(CheckpointRefused, "checkpoint_body_invalid"):
            decode_checkpoint(malformed.body)
        self.assertEqual(0, self.post_calls)
        self.assertEqual([], list(self.jpath.iterdir()))

    def test_unpaired_surrogate_inventory_is_refused_before_intent_or_post(self):
        comment = self.add_comment(self.initial)
        malformed = dataclasses.replace(comment, body="checkpoint \ud800")
        self.comments[:] = [malformed]
        with self.assertRaisesRegex(CheckpointRefused, "inventory_identity_invalid"):
            self.publisher.publish(self.initial)
        self.assertEqual(0, self.post_calls)
        self.assertEqual([], list(self.jpath.iterdir()))

    def test_unpaired_surrogate_report_is_rejected_by_capsule_codec(self):
        with self.assertRaisesRegex(CheckpointRefused, "checkpoint_report_invalid"):
            dataclasses.replace(self.initial, report="bad \ud800")
        self.assertEqual(0, self.post_calls)

    def test_unpaired_surrogate_after_effect_is_uncertain_without_repost(self):
        original = self.publisher.publish(self.initial)
        previous = {
            "state": "observed", "checkpoint_id": str(original.comment_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn = self.add_snapshot(self.initial.metadata_commit,
                                 boundary="turn-end", previous=previous)

        def mutate_parent_after_post(pr_number, body, deadline):
            result = self.post(pr_number, body, deadline)
            self.comments[0] = dataclasses.replace(
                self.comments[0], body="malformed \ud800")
            return result

        with self.assertRaisesRegex(CheckpointUncertain, "post_outcome_uncertain"):
            self.publisher_for(post=mutate_parent_after_post).publish(
                turn, previous_comment_id=original.comment_id)
        self.assertEqual(2, self.post_calls)
        self.assertEqual(2, len(list(self.jpath.iterdir())))
        # The synthetic Host can no longer attest the edited original
        # body digest, so the stream-wide lease refuses before inventory.
        with self.assertRaisesRegex(CheckpointRefused, "stream_pending"):
            self.publisher.publish(turn, previous_comment_id=original.comment_id)
        self.assertEqual(2, self.post_calls)

    def test_ordinary_own_comment_mentioning_marker_is_not_checkpoint(self):
        initial_comment = self.add_comment(self.initial)
        note = self.add_comment(
            self.initial, body=f"Code review note: {MARKER} is a format name")
        self.assertGreater(note.comment_id, initial_comment.comment_id)
        existing = self.publisher.publish(self.initial)
        self.assertEqual(initial_comment.comment_id, existing.comment_id)
        self.assertEqual(0, self.post_calls)
        previous = {
            "state": "observed", "checkpoint_id": str(initial_comment.comment_id),
            "subject": self.initial.subject,
            "metadata_commit": self.initial.metadata_commit,
            "snapshot_id": self.initial.snapshot_id,
            "commits_after": {"state": "observed", "count": 0},
        }
        turn = self.add_snapshot(self.initial.metadata_commit,
                                 boundary="turn-end", previous=previous)
        posted = self.publisher.publish(
            turn, previous_comment_id=initial_comment.comment_id)
        self.assertGreater(posted.comment_id, note.comment_id)
        self.assertEqual(1, self.post_calls)

    def test_malformed_first_line_checkpoint_opener_is_not_ignored(self):
        bad = self.add_comment(
            self.initial, body=f"<!-- {MARKER} sha256=bad -->\nCapsule: broken")
        with self.assertRaises(CheckpointRefused):
            self.publisher.publish(self.initial)
        self.assertEqual(0, self.post_calls)
        self.assertEqual([], list(self.jpath.iterdir()))
        self.assertEqual(bad, self.comments[0])

    def test_lease_exit_refusal_after_confirmed_post_is_uncertain(self):
        @contextmanager
        def release_failure(value, deadline):
            self.assertLess(time.monotonic(), deadline)
            yield True
            raise CheckpointRefused("synthetic_lease_release_failure")

        with self.assertRaisesRegex(
                CheckpointUncertain, "stream_lease_uncertain"):
            self.publisher_for(stream_lease=release_failure).publish(self.initial)
        self.assertEqual(1, self.post_calls)
        self.assertEqual(1, len(list(self.jpath.iterdir())))
        # Same-key recovery: the original posted ID, never a second post.
        confirmed = self.publisher.publish(self.initial)
        self.assertEqual(self.comments[0].comment_id, confirmed.comment_id)
        self.assertEqual(1, self.post_calls)

    def test_lease_exit_refusal_before_intent_stays_refused(self):
        @contextmanager
        def denied_then_release_failure(value, deadline):
            try:
                yield False
            finally:
                raise CheckpointRefused("synthetic_lease_release_failure")

        with self.assertRaisesRegex(
                CheckpointRefused, "synthetic_lease_release_failure") as raised:
            self.publisher_for(
                stream_lease=denied_then_release_failure).publish(self.initial)
        self.assertNotIsInstance(raised.exception, CheckpointUncertain)
        self.assertEqual(0, self.post_calls)
        self.assertEqual([], list(self.jpath.iterdir()))

    def test_lease_exit_refusal_overrides_unknown_post_but_stays_uncertain(self):
        @contextmanager
        def release_failure(value, deadline):
            try:
                yield True
            finally:
                # __exit__ deliberately overrides the body-side uncertain
                # exception, like a failing real Host lease release.
                raise CheckpointRefused("synthetic_lease_release_failure")

        self.writer_error = "before"
        with self.assertRaisesRegex(
                CheckpointUncertain, "stream_lease_uncertain"):
            self.publisher_for(stream_lease=release_failure).publish(self.initial)
        self.assertEqual(1, self.post_calls)
        self.assertEqual(1, len(list(self.jpath.iterdir())))
        self.writer_error = False
        with self.assertRaises(CheckpointUncertain):
            self.publisher.publish(self.initial)
        self.assertEqual(1, self.post_calls)

    def test_lease_suppressed_unknown_effect_is_still_uncertain(self):
        @contextmanager
        def suppress_body_exception(value, deadline):
            try:
                yield True
            except CheckpointRefused:
                return  # adversarial __exit__ suppresses CheckpointUncertain

        self.writer_error = "before"
        with self.assertRaises(CheckpointUncertain):
            self.publisher_for(
                stream_lease=suppress_body_exception).publish(self.initial)
        self.assertEqual(1, self.post_calls)
        self.assertEqual(1, len(list(self.jpath.iterdir())))
        self.writer_error = False
        with self.assertRaises(CheckpointUncertain):
            self.publisher.publish(self.initial)
        self.assertEqual(1, self.post_calls)

    def test_lease_suppressed_pre_intent_denial_stays_refused(self):
        @contextmanager
        def suppress_pre_intent_denial(value, deadline):
            try:
                yield False
            except CheckpointRefused:
                return  # adversarial __exit__ suppresses the require failure

        with self.assertRaisesRegex(
                CheckpointRefused, "checkpoint_stream_pending") as raised:
            self.publisher_for(
                stream_lease=suppress_pre_intent_denial).publish(self.initial)
        self.assertNotIsInstance(raised.exception, CheckpointUncertain)
        self.assertEqual(0, self.post_calls)
        self.assertEqual([], list(self.jpath.iterdir()))

    def test_post_interrupt_and_lease_exit_refusal_cannot_be_safe_refusal(self):
        @contextmanager
        def release_failure(value, deadline):
            try:
                yield True
            finally:
                # Host release overrides the BaseException from _post.
                raise CheckpointRefused("synthetic_lease_release_failure")

        def post_then_interrupt(pr_number, body, deadline):
            self.post(pr_number, body, deadline)
            raise KeyboardInterrupt("synthetic_interrupt_after_effect")

        with self.assertRaisesRegex(
                CheckpointUncertain, "stream_lease_uncertain"):
            self.publisher_for(
                stream_lease=release_failure,
                post=post_then_interrupt).publish(self.initial)
        self.assertEqual(1, self.post_calls)
        self.assertEqual(1, len(list(self.jpath.iterdir())))
        # Recovery adopts the original ID; never a second comment.
        confirmed = self.publisher.publish(self.initial)
        self.assertEqual(self.comments[0].comment_id, confirmed.comment_id)
        self.assertEqual(1, self.post_calls)


    def test_lease_release_interrupt_after_confirmed_post_is_uncertain(self):
        @contextmanager
        def interrupted_release(value, deadline):
            yield True
            raise KeyboardInterrupt("release interrupted after readback")

        with self.assertRaisesRegex(
                CheckpointUncertain, "checkpoint_stream_lease_uncertain"):
            self.publisher_for(
                stream_lease=interrupted_release).publish(self.initial)
        self.assertEqual(1, self.post_calls)
        self.assertEqual(1, len(list(self.jpath.iterdir())))
        confirmed = self.publisher.publish(self.initial)
        self.assertEqual(self.comments[0].comment_id, confirmed.comment_id)
        self.assertEqual(1, self.post_calls)

    def test_post_interrupt_after_effect_without_lease_override_is_uncertain(self):
        def post_then_interrupt(pr_number, body, deadline):
            self.post(pr_number, body, deadline)
            raise KeyboardInterrupt("post interrupted after comment creation")

        with self.assertRaisesRegex(
                CheckpointUncertain, "checkpoint_stream_lease_uncertain"):
            self.publisher_for(post=post_then_interrupt).publish(self.initial)
        self.assertEqual(1, self.post_calls)
        confirmed = self.publisher.publish(self.initial)
        self.assertEqual(self.comments[0].comment_id, confirmed.comment_id)
        self.assertEqual(1, self.post_calls)

    def test_pre_intent_lease_interrupt_is_not_misclassified(self):
        @contextmanager
        def interrupted_enter(value, deadline):
            raise KeyboardInterrupt("before lease acquisition")
            yield True  # an unreachable yield keeps the generator shape

        with self.assertRaisesRegex(KeyboardInterrupt, "before lease acquisition"):
            self.publisher_for(
                stream_lease=interrupted_enter).publish(self.initial)
        self.assertEqual(0, self.post_calls)
        self.assertEqual([], list(self.jpath.iterdir()))

    def test_persisted_intent_requires_exact_key_and_value_types(self):
        original = self.publisher.publish(self.initial)
        journal = self.journal
        changes = (
            ("float repository ID", lambda item: item.__setitem__(
                "repository_id", float(REPO_ID))),
            ("float PR number", lambda item: item.__setitem__(
                "pr_number", float(PR))),
            ("float principal", lambda item: item.__setitem__(
                "principal", float(PRINCIPAL))),
            ("float schema", lambda item: item.__setitem__("schema", 2.0)),
            ("bool instead of absent predecessor", lambda item: item.__setitem__(
                "previous_comment_id", False)),
            ("unexpected field", lambda item: item.__setitem__("extra", "x")),
            ("missing field", lambda item: item.pop("body_sha256")),
        )
        for name, mutate in changes:
            class AlteredJournal:
                def read(self, key):
                    altered = dict(journal.read(key))
                    mutate(altered)
                    return altered

                def create(self, *_):
                    raise AssertionError("malformed journal must not create")

            with self.subTest(change=name), self.assertRaisesRegex(
                    CheckpointRefused, "checkpoint_intent_conflict"):
                self.publisher_for(journal=AlteredJournal()).publish(
                    self.initial)
        self.assertEqual(1, self.post_calls)
        self.assertEqual(1, len(list(self.jpath.iterdir())))
        confirmed = self.publisher.publish(self.initial)
        self.assertEqual(original.comment_id, confirmed.comment_id)
        self.assertEqual(1, self.post_calls)


if __name__ == "__main__":
    unittest.main()
