"""#337: bounded, dependency-free checkpoint publication state-machine tests.

The reference model is deliberately separate from the publisher. Each trace
runs against a fresh disposable SHA-1 Git fixture and synthetic Host/journal.
This is a bounded transition matrix, not Hypothesis, formal proof, real GitHub
CAS, or a substitute for real Host attestation.
"""
from __future__ import annotations

import dataclasses
import itertools
import unittest
from contextlib import contextmanager

# Import the fixture module, not its TestCase class as a global name: unittest
# discovery must NOT duplicate the existing 55 focused regression tests.
from tests import test_kagari_metadata_v2_pr_checkpoint as _baseline

from kagari_metadata_v2_pr_checkpoint import (
    CheckpointRefused, CheckpointUncertain, ConfirmedCheckpoint,
)


@contextmanager
def _isolated_fixture():
    """Reuse the existing exercised Host/Git fixture without inherited tests."""
    fixture = _baseline.CheckpointV2Test("test_v2_closed_capsule_and_exact_original_read")
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.doCleanups()


def _outcome(action):
    """Capture BaseException so a raw interrupt becomes a failed assertion."""
    try:
        return action()
    except BaseException as error:
        return error


def _planned_lease(fixture, release):
    """Host lease; the release event is independent from the posting event."""
    @contextmanager
    def lease(value, deadline):
        with fixture.stream_lease(value, deadline) as cleared:
            try:
                yield cleared
            except BaseException:
                if release == "suppress":
                    return  # __exit__ consumes a body error
                raise
            finally:
                if release == "refuse":
                    raise CheckpointRefused("model_lease_release_failure")
                if release == "interrupt":
                    raise KeyboardInterrupt("model_lease_release_interrupt")
    return lease


def _journal_receipts(fixture):
    """Capture full durable journal bytes, not only the file count."""
    return tuple(sorted((p.name, p.read_bytes()) for p in fixture.jpath.iterdir()))


def _post_for(fixture, event):
    if event == "ack":
        return fixture.post

    if event in {"lost_before", "lost_after"}:
        fixture.writer_error = "before" if event == "lost_before" else "after"
        return fixture.post

    def post_then_interrupt(pr_number, body, deadline):
        fixture.post(pr_number, body, deadline)
        raise KeyboardInterrupt("model_post_interrupt_after_effect")

    if event == "interrupt_after":
        return post_then_interrupt
    raise AssertionError(f"unknown post event {event}")


class CheckpointModelTransitions(unittest.TestCase):
    def test_post_ack_and_lease_release_product_with_retry_oracle(self):
        """4 post modes x 4 lease exits: persisted state governs all retries."""
        post_events = ("ack", "lost_before", "lost_after", "interrupt_after")
        release_events = ("clean", "refuse", "interrupt", "suppress")
        for post_event, release in itertools.product(post_events, release_events):
            with self.subTest(post_event=post_event, release=release):
                with _isolated_fixture() as f:
                    expected_id = f.next_comment + 1
                    publisher = f.publisher_for(
                        post=_post_for(f, post_event),
                        stream_lease=_planned_lease(f, release),
                    )
                    first = _outcome(lambda: publisher.publish(f.initial))

                    # Independent finite oracle: post callback runs once;
                    # a side effect is visible for all except lost-before.
                    visible = post_event != "lost_before"
                    verified_in_first_call = (
                        post_event in ("ack", "lost_after")
                        and release in ("clean", "suppress")
                    )
                    expected_class = (ConfirmedCheckpoint if verified_in_first_call
                                      else CheckpointUncertain)
                    self.assertIs(type(first), expected_class)
                    self.assertEqual(1, f.post_calls)
                    self.assertEqual(int(visible), len(f.comments))
                    self.assertEqual(1, len(list(f.jpath.iterdir())))
                    original_comments = tuple(f.comments)
                    original_journal = _journal_receipts(f)
                    if visible:
                        self.assertEqual(expected_id, f.comments[0].comment_id)
                        self.assertEqual(_baseline.render_checkpoint(f.initial),
                                         f.comments[0].body)
                    if verified_in_first_call:
                        self.assertEqual(expected_id, first.comment_id)
                        self.assertEqual(f.initial, first.checkpoint)

                    # Re-entry with a normal Host must *never* post again.
                    f.writer_error = False
                    repeated = _outcome(lambda: f.publisher.publish(f.initial))
                    self.assertIs(
                        type(repeated),
                        ConfirmedCheckpoint if visible else CheckpointUncertain,
                    )
                    if visible:
                        self.assertEqual(expected_id, repeated.comment_id)
                        self.assertEqual(f.comments[0].comment_id,
                                         repeated.comment_id)
                        self.assertEqual(f.initial, repeated.checkpoint)
                    self.assertEqual(1, f.post_calls)
                    self.assertEqual(int(visible), len(f.comments))
                    self.assertEqual(original_comments, tuple(f.comments))
                    self.assertEqual(original_journal, _journal_receipts(f))

    def test_pre_intent_refusal_or_interrupt_never_leaves_effect(self):
        """Denied admission and pre-intent Host faults permit a later fresh try."""
        for event in ("grant_denied", "lease_pending", "draft_changed",
                      "lease_release_refused", "lease_enter_interrupted"):
            with self.subTest(event=event):
                with _isolated_fixture() as f:
                    if event == "grant_denied":
                        publisher = f.publisher_for(grant=lambda *_: False)
                    elif event == "draft_changed":
                        original = f.pr
                        f.pr = dataclasses.replace(f.pr, draft=False)
                        publisher = f.publisher
                    elif event in ("lease_pending", "lease_release_refused"):
                        @contextmanager
                        def deny(value, deadline):
                            try:
                                yield False
                            finally:
                                if event == "lease_release_refused":
                                    raise CheckpointRefused("model_release_refused")
                        publisher = f.publisher_for(stream_lease=deny)
                    else:
                        @contextmanager
                        def interrupted_enter(value, deadline):
                            raise KeyboardInterrupt("model_before_lease")
                            yield True
                        publisher = f.publisher_for(stream_lease=interrupted_enter)

                    denied = _outcome(lambda: publisher.publish(f.initial))
                    self.assertIs(
                        type(denied),
                        KeyboardInterrupt if event == "lease_enter_interrupted"
                        else CheckpointRefused,
                    )
                    self.assertEqual(0, f.post_calls)
                    self.assertFalse(f.comments)
                    self.assertFalse(list(f.jpath.iterdir()))

                    # Reset the only modified PR fact before using the normal Host.
                    if event == "draft_changed":
                        f.pr = original
                    confirmed = f.publisher.publish(f.initial)
                    self.assertIs(type(confirmed), ConfirmedCheckpoint)
                    self.assertEqual(1, f.post_calls)
                    self.assertEqual(1, len(f.comments))
                    self.assertEqual(1, len(list(f.jpath.iterdir())))

    def test_two_checkpoint_stream_and_cross_key_pending_state(self):
        """4 outcomes for the next key after a confirmed initial checkpoint."""
        for post_event in ("ack", "lost_before", "lost_after", "interrupt_after"):
            with self.subTest(post_event=post_event):
                with _isolated_fixture() as f:
                    expected_initial_id = f.next_comment + 1
                    initial = f.publisher.publish(f.initial)
                    self.assertIs(type(initial), ConfirmedCheckpoint)
                    self.assertEqual(expected_initial_id, initial.comment_id)
                    self.assertEqual(expected_initial_id, f.comments[0].comment_id)
                    predecessor = {
                        "state": "observed",
                        "checkpoint_id": str(initial.comment_id),
                        "subject": f.initial.subject,
                        "metadata_commit": f.initial.metadata_commit,
                        "snapshot_id": f.initial.snapshot_id,
                        "commits_after": {"state": "observed", "count": 0},
                    }
                    turn = f.add_snapshot(
                        f.initial.metadata_commit,
                        boundary="turn-end",
                        previous=predecessor,
                    )
                    expected_turn_id = f.next_comment + 1
                    publisher = f.publisher_for(post=_post_for(f, post_event))
                    first_turn = _outcome(
                        lambda: publisher.publish(
                            turn, previous_comment_id=initial.comment_id))
                    visible = post_event != "lost_before"
                    first_confirmed = post_event in ("ack", "lost_after")
                    self.assertIs(
                        type(first_turn),
                        ConfirmedCheckpoint if first_confirmed
                        else CheckpointUncertain,
                    )
                    self.assertEqual(2, f.post_calls)
                    self.assertEqual(1 + int(visible), len(f.comments))
                    self.assertEqual(2, len(list(f.jpath.iterdir())))
                    if visible:
                        self.assertEqual(expected_turn_id, f.comments[-1].comment_id)
                        self.assertEqual(_baseline.render_checkpoint(turn),
                                         f.comments[-1].body)
                    if first_confirmed:
                        self.assertEqual(expected_turn_id, first_turn.comment_id)
                        self.assertEqual(turn, first_turn.checkpoint)
                    original_comments = tuple(f.comments)
                    original_journal = _journal_receipts(f)

                    f.writer_error = False
                    retry_turn = _outcome(
                        lambda: f.publisher.publish(
                            turn, previous_comment_id=initial.comment_id))
                    self.assertIs(
                        type(retry_turn),
                        ConfirmedCheckpoint if visible else CheckpointUncertain)
                    if visible:
                        self.assertEqual(expected_turn_id, retry_turn.comment_id)
                        self.assertEqual(turn, retry_turn.checkpoint)
                    self.assertEqual(2, f.post_calls)
                    self.assertEqual(1 + int(visible), len(f.comments))
                    self.assertEqual(original_comments, tuple(f.comments))
                    self.assertEqual(original_journal, _journal_receipts(f))

                    # Other keys must be blocked by an unconfirmed durable
                    # turn intent, even when the initial key is already valid.
                    retry_initial = _outcome(
                        lambda: f.publisher.publish(f.initial))
                    if visible:
                        self.assertIs(type(retry_initial), ConfirmedCheckpoint)
                        self.assertEqual(initial.comment_id,
                                         retry_initial.comment_id)
                        self.assertEqual(f.initial, retry_initial.checkpoint)
                    else:
                        self.assertIs(type(retry_initial), CheckpointRefused)
                        self.assertIn("stream_pending", str(retry_initial))
                    self.assertEqual(2, f.post_calls)
                    self.assertEqual(1 + int(visible), len(f.comments))
                    self.assertEqual(original_comments, tuple(f.comments))
                    self.assertEqual(original_journal, _journal_receipts(f))


if __name__ == "__main__":
    unittest.main()
