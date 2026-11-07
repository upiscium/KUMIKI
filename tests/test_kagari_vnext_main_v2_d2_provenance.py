"""#291 R4A D2-P0 source-only Host original-POST provenance negatives.

All Hosts here are fake; no real credential, GitHub API, production Metadata
branch or process supervisor is used. We verify fail-closed policy ONLY under
trusted-boundary facts asserted by an isolated fake Host.
"""
from __future__ import annotations

import json
import unittest
from dataclasses import replace

from tests import test_kagari_metadata_v2_issue_publisher as issue_fixture
from tests.test_kagari_vnext_main_v2_initial_checkpoint import _Harness
# Canonical #337 top-level import path is initialized by the disposable
# fixture imports above; do not create a separate tools.* type identity.
from kagari_vnext_main_v2_provenance import (
    D2P0Rejected, OriginalOperation, check_claim, check_ack)
from kagari_metadata_v2_pr_checkpoint import decode_checkpoint
from tools import kagari_vnext_main_v2_initial_checkpoint as coordinator
from metadata_v2 import issue_ref


class D2P0ProvenanceSourceTest(unittest.TestCase):
    def setUp(self):
        src = issue_fixture.IssueWriterTest("runTest")
        src.setUp()
        self.addCleanup(src.doCleanups)
        self.src = src
        self.h = _Harness(src)

    def _stopped(self, code: str, *, original_writes: int = 0):
        with self.assertRaises(coordinator.InitialCheckpointUncertain) as caught:
            self.h.run()
        self.assertEqual(code, caught.exception.code)
        self.assertEqual("UNCERTAIN", caught.exception.state)
        self.assertEqual(original_writes, self.h.stats["writes"])
        return caught.exception

    def test_missing_host_provenance_blocks_before_plan_or_metadata(self):
        self.h.provenance_mode = "disabled"
        self._stopped("R5C1B1_D2_PROVENANCE_HOST_REQUIRED")
        self.assertIsNone(self.h.saved_plan())
        self.assertEqual({"writes": 0, "posts": 0, "views": 0},
                         self.h.stats)
        self.assertEqual(self.src.first, self.src.observe(issue_ref("101")))
        self.assertEqual({}, self.h.journal_bytes())

    def test_unqualified_host_claim_denied_before_plan(self):
        for mode in ("missing", "invalid"):
            with self.subTest(mode=mode):
                self.h.provenance_mode = mode
                code = "D2P0_HOST_CLAIM_REQUIRED"
                self._stopped(code)
                self.assertIsNone(self.h.saved_plan())
                self.assertEqual(0, self.h.stats["writes"])
                self.assertEqual(0, self.h.stats["posts"])

    def test_claim_creation_unavailable_blocks_before_metadata(self):
        self.h.provenance_mode = "no_claim"
        self._stopped("D2P0_HOST_CLAIM_REQUIRED")
        self.assertIsNotNone(self.h.saved_plan())  # create-once plan only
        self.assertEqual(0, self.h.stats["writes"])
        self.assertEqual(0, self.h.stats["posts"])
        self.assertEqual(self.src.first, self.src.observe(issue_ref("101")))
        self.assertFalse((self.src.root / "fake-d2-provenance.json").exists())

    def test_matching_comment_without_original_egress_ack_blocks(self):
        # Remote comment was posted, but Host never received the POST reply.
        # #337 may confirm matching text; D2-P0 must NOT.
        self.h.api.writer_error = "after"
        self._stopped("D2P0_DURABLE_ACK_REQUIRED", original_writes=1)
        original = self.h.saved_plan()
        self.assertIsNotNone(original)
        self.assertEqual({"writes": 1, "posts": 1, "views": 1},
                         self.h.stats)
        self.h.api.writer_error = False
        self._stopped("D2P0_DURABLE_ACK_REQUIRED", original_writes=1)
        self.assertEqual(original, self.h.saved_plan())
        self.assertEqual({"writes": 1, "posts": 1, "views": 1},
                         self.h.stats)

    def test_ack_removed_after_success_cannot_confirm_in_recovery(self):
        self.assertEqual("CONFIRMED", self.h.run()["initial_checkpoint"])
        proof_path = self.src.root / "fake-d2-provenance.json"
        original = json.loads(proof_path.read_text())
        self.assertIsNotNone(original["ack"])
        original["ack"] = None
        proof_path.write_text(json.dumps(original))
        self._stopped("D2P0_DURABLE_ACK_REQUIRED", original_writes=1)
        self.assertEqual({"writes": 1, "posts": 1, "views": 1}, self.h.stats)

    def test_ack_wrong_id_pr_epoch_or_digest_blocks_after_recovery(self):
        self.assertEqual("CONFIRMED", self.h.run()["initial_checkpoint"])
        for mutation in ({"comment_id": 701},
                         {"pr_number": 702},
                         {"principal": 703},
                         {"epoch": 704},
                         {"body_sha256": "f" * 64},
                         {"checkpoint_key": "other-key"}):
            with self.subTest(mutation=mutation):
                self.h.provenance_ack_mutation = mutation
                self._stopped("D2P0_ACK_MISMATCH", original_writes=1)
                self.assertEqual({"writes": 1, "posts": 1, "views": 1},
                                 self.h.stats)

    def test_then_valid_old_epoch_ack_can_be_verified_by_new_host(self):
        # A durable epoch-1 ACK is still evidence after a qualified epoch-2
        # takeover. Merely comparing ACK.epoch == current epoch is invalid.
        self.assertEqual("CONFIRMED", self.h.run()["initial_checkpoint"])
        saved = self.h.d2._load()
        scope = OriginalOperation(**saved["scope"])
        capsule = decode_checkpoint(self.h.api.comments[0].body)
        ack = self.h.d2.read_ack(
            scope, capsule, self.h.api.comments[0].comment_id)
        first = check_claim(scope, self.h.d2.claim(scope))
        next_host = replace(
            first, epoch=2, epoch_lineage=(1, 2),
            valid_ack_epochs=(1, 2), prior_worker_quiescent=True)
        self.assertEqual(
            ack, check_ack(scope, check_claim(scope, next_host),
                           capsule, ack.comment_id, ack))
        # An epoch-1 ACK cannot be accepted if the Host has not positively
        # qualified its historical issuance/lineage or predecessor shutdown.
        no_old_ack = replace(next_host, valid_ack_epochs=(2,))
        with self.assertRaises(D2P0Rejected) as caught:
            check_ack(scope, check_claim(scope, no_old_ack),
                      capsule, ack.comment_id, ack)
        self.assertEqual("D2P0_ACK_MISMATCH", caught.exception.code)
        with self.assertRaises(D2P0Rejected):
            check_claim(scope, replace(
                next_host, epoch_lineage=(2, 1)))
        # Python dataclass == treats True == 1. D2 never treats a boolean
        # as a valid repository/Issue ID in an embedded Host witness.
        forged = replace(scope, repository_id=True)
        with self.assertRaises(D2P0Rejected):
            check_claim(scope, replace(first, operation=forged))
        with self.assertRaises(D2P0Rejected):
            check_ack(scope, first, capsule, ack.comment_id,
                      replace(ack, operation=forged))

    def test_fake_scope_change_refuses_new_operation_or_different_pr(self):
        self.assertEqual("CONFIRMED", self.h.run()["initial_checkpoint"])
        prior = self.h.saved_plan()
        self.h.operation_id = "2" * 32
        self._stopped("R5C1B1_PLAN_CONFLICT", original_writes=1)
        self.h.operation_id = "1" * 32
        self.h.report = "changed intent report"
        self._stopped("R5C1B1_PLAN_CONFLICT", original_writes=1)
        self.assertEqual(prior, self.h.saved_plan())
        self.assertEqual({"writes": 1, "posts": 1, "views": 1},
                         self.h.stats)

    def test_creator_bound_fake_ack_allows_exact_original_repeat(self):
        first = self.h.run()
        self.assertEqual("CONFIRMED", first["initial_checkpoint"])
        for flag in coordinator._FLAGS:
            self.assertIs(first[flag], False, flag)
        self.assertEqual("NOT_STARTED", first["runtime"])
        self.assertEqual("NOT_SENT", first["kickoff"])
        self.assertEqual(first, self.h.run())
        self.assertEqual({"writes": 1, "posts": 1, "views": 1},
                         self.h.stats)
