"""R5C1A disposable Product Git/Host PR-first tests; no real GitHub effects.

#256 fixture contains a fake immutable package whose executables exit 97.
All Product Git/metadata refs and PR callbacks are disposable/local.
"""
from __future__ import annotations

from dataclasses import replace
import copy
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from tests import test_kagari_metadata_v2_task_admission as fixtures
from tests.test_metadata_v2 import REPOSITORY
from tools import kagari_vnext_main as main_module
from tools import kagari_vnext_main_service as service
from tools import kagari_vnext_main_v2_pr_first_publication as publication
from tools.kagari_vnext_main import MainError
from tools.kagari_vnext_main_v2_observation import V2ReadOnlyMainHost
from tools.kagari_vnext_main_v2_pr_first_publication import (
    ProductPrFacts, ProductPull, BootstrapCandidate,
    V2ProductPrFirstHost, ProductPrRefused)
from kagari_vnext_trusted_host import RecoveryJournal
from task_collaboration import RepositoryFacts
import kagari_vnext_attachment_gate as gate

PRINCIPAL = 9341
PR_NUMBER = 71
BASE_REF = "refs/heads/main"


class R5C1AProductPRTest(unittest.TestCase):
    def setUp(self):
        src = fixtures.V2TaskAdmissionTest("runTest")
        self.addCleanup(src.doCleanups)
        src.setUp()
        self.src = src
        alloc = src.admit()  # disposable local Git Worktree, no session/model
        self.worktree = Path(alloc.worktree)
        self.prs = []
        self.remote_head = None
        self.remote_known = True
        self.complete = True
        self.principal = PRINCIPAL
        self.callbacks = []
        self.grants = []
        self.allow = True
        self.read_allowed = True
        self.legacy_clear = True
        self.fact_hook = None
        self.bootstrap_mode = "ok"
        self.push_mode = "ok"
        self.draft_mode = "ok"
        self.journal = self.make_journal()
        self.repo = RepositoryFacts(REPOSITORY, BASE_REF, src.identity.repository_id)
        self.issues = {101: {
            "repository": REPOSITORY, "number": 101, "state": "OPEN",
            "title": src.approved.title, "body": src.approved.body,
        }}
        self.plan = {
            "schema_version": 1, "repository": REPOSITORY,
            "project_root": str(src.project),
            "runtime_home": str(src.home / "r5c1a-never-create-runtime"),
            "tasks": [{
                "issue": 101, "branch_ref": src.registered.branch_ref,
                "base_revision": src.base, "contract_id": src.registered.contract_id,
                "port": 34201,
            }]
        }
        self.lock_home = src.project.parent / "main-owner-r5c1a"
        self.lock_home.mkdir(mode=0o700)
        owner = patch.object(service, "_host_state_home", return_value=self.lock_home)
        owner.start()
        self.addCleanup(owner.stop)

    def make_journal(self):
        path = self.src.project.parent / "private-journal-r5c1a"
        path.mkdir(mode=0o700)
        return RecoveryJournal(path)

    def read_issue(self, issue):
        return copy.deepcopy(self.issues[issue])

    def read_facts(self, request):
        self.callbacks.append(("read_facts", request))
        if self.fact_hook:
            self.fact_hook(request)
        if not self.remote_known:
            raise OSError("unavailable remote")
        return ProductPrFacts(
            self.repo, self.principal,
            "ABSENT" if self.remote_head is None else "PRESENT",
            self.remote_head, tuple(self.prs), self.complete)

    def candidate(self, request, tree):
        self.callbacks.append(("plan_bootstrap", request))
        # Calculate an exact deterministic empty commit OID without writing
        # Git objects, refs, index or Working Tree before the durable intent.
        data = (f"tree {tree}\nparent {request.base_revision}\n"
                "author fixture <fixture@example.invalid> 0 +0000\n"
                "committer fixture <fixture@example.invalid> 0 +0000\n"
                "\nAgentCore Task 101 bootstrap\n").encode()
        value = hashlib.sha1(
            b"commit " + str(len(data)).encode() + b"\x00" + data).hexdigest()
        self.bootstrap_bytes = data
        return BootstrapCandidate(value, tree)

    def bootstrap(self, effect):
        self.callbacks.append(("bootstrap", effect))
        if self.bootstrap_mode == "before":
            raise OSError("bootstrap not executed")
        env = {k:v for k,v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                   GIT_TERMINAL_PROMPT="0")
        result = subprocess.run(
            ["git", "-C", str(self.worktree), "hash-object",
             "-w", "-t", "commit", "--stdin"],
            input=self.bootstrap_bytes, capture_output=True,
            env=env, timeout=10, check=True).stdout.decode().strip()
        self.assertEqual(effect.subject, result)
        self.src.attached.git("-C", str(self.worktree),
                              "update-ref", effect.request.branch_ref,
                              effect.subject, effect.expected_head)
        if self.bootstrap_mode == "after":
            raise OSError("ACK lost after bootstrap")

    def push(self, effect):
        self.callbacks.append(("push", effect))
        self.assertIs(effect.non_force, True)
        if self.push_mode == "before":
            raise OSError("push not executed")
        self.assertEqual(self.remote_head, effect.expected_remote)
        self.remote_head = effect.subject  # fake GitHub remote task branch only
        if self.push_mode == "after":
            raise OSError("ACK lost after push")

    def create(self, effect):
        self.callbacks.append(("create", effect))
        self.assertIs(effect.non_force, True)
        if self.draft_mode == "before":
            raise OSError("PR not created")
        self.assertEqual(self.remote_head, effect.subject)
        self.prs.append(ProductPull(
            PR_NUMBER, REPOSITORY, REPOSITORY, "main",
            effect.request.branch_ref.removeprefix("refs/heads/"),
            effect.subject, "open", True, self.principal,
            effect.title, effect.body))
        if self.draft_mode == "after":
            raise OSError("PR created, ACK lost")

    def authorize(self, effect):
        self.grants.append(effect)
        return self.allow

    def host(self, lease, *, journal=None, read_facts=None, authorize=None,
             base_ref=BASE_REF):
        src = self.src
        readonly = V2ReadOnlyMainHost(
            src.selection(), src.authority(), lease,
            lambda _: self.legacy_clear, lambda _: self.read_allowed)
        return V2ProductPrFirstHost(
            readonly, base_ref, read_facts or self.read_facts,
            self.candidate, self.bootstrap, self.push, self.create,
            authorize or self.authorize, journal or self.journal)

    def main(self, lease, hosts=None):
        if hosts is None:
            hosts = {101: self.host(lease)}
        return service.build_main(
            copy.deepcopy(self.plan),
            host=service.MainV2ProductPrCapabilities(
                self.read_issue, hosts))

    def operation(self, *, with_owner=True, publication_only=True,
                  override=None):
        src = self.src
        with gate.hold_verified(src.project, src.home, src.package) as lease:
            main = self.main(lease, {101: override(lease)} if override else None)
            with patch.object(
                main_module, "start_root_session",
                side_effect=AssertionError("no Session")), patch.object(
                main_module, "opencode_request",
                side_effect=AssertionError("no model")), patch.object(
                main_module, "isolated_metadata_observation",
                side_effect=AssertionError("no v1 Metadata")):
                if not with_owner:
                    return main.publish_v2_product_pr_first(101)
                with service.MainSupervisor(
                    main, attachment_lease=lease,
                    publication_only=publication_only) as runner:
                    return runner.publish_product_pr_first(101)

    def fresh_task(self):
        return self.src.attached.git(
            "-C", str(self.worktree), "rev-parse", "HEAD")

    def preserved_project(self):
        return (
            self.src.attached.git("rev-parse", "HEAD"),
            (self.src.project / ".git/index").read_bytes(),
            self.src.attached.receipt().read_bytes(),
        )

    def assert_preserved(self, value):
        self.assertEqual(value, self.preserved_project())
        self.assertFalse(
            (self.src.home / "r5c1a-never-create-runtime").exists())

    def refused(self, expected, *, uncertain=False):
        with self.assertRaises(MainError) as context:
            self.operation()
        self.assertEqual(expected, context.exception.code)

    def test_positive_from_clean_base_bootstrap_push_create_and_recovery(self):
        project = self.preserved_project()
        self.assertEqual(self.src.base, self.fresh_task())
        result = self.operation()
        self.assertEqual("pr_first_draft_established", result["status"])
        self.assertEqual("NOT_PUBLISHED", result["checkpoint"])
        self.assertEqual(PR_NUMBER, result["draft_pr"])
        self.assertNotEqual(self.src.base, result["subject"])
        self.assertEqual(self.remote_head, result["subject"])
        self.assertEqual(["bootstrap","push","draft-pr"],
                         [p.operation for p in self.grants])
        for key in ("task_execution_authorized", "session_started",
                    "prompt_sent", "metadata_write_authorized",
                    "github_write_authorized"):
            self.assertIs(result[key], False)
        assert_previous = len(self.grants)
        self.callbacks.clear()
        again = self.operation()
        self.assertEqual(result, again)
        self.assertEqual(assert_previous, len(self.grants))
        self.assertFalse(any(name in ("bootstrap", "push", "create")
                             for name, _ in self.callbacks))
        self.assert_preserved(project)

    def test_lost_ack_after_bootstrap_reconciles_without_replay(self):
        self.bootstrap_mode = "after"
        result = self.operation()
        self.assertEqual(PR_NUMBER, result["draft_pr"])
        self.callbacks.clear()
        self.operation()
        self.assertFalse(any(name in ("bootstrap", "push", "create")
                             for name, _ in self.callbacks))

    def test_lost_ack_after_push_reconciles_without_replay(self):
        self.push_mode = "after"
        result = self.operation()
        self.assertEqual(PR_NUMBER, result["draft_pr"])
        self.callbacks.clear()
        self.operation()
        self.assertFalse(any(name in ("bootstrap", "push", "create")
                             for name, _ in self.callbacks))

    def test_lost_ack_after_draft_reconciles_without_replay(self):
        self.draft_mode = "after"
        result = self.operation()
        self.assertEqual(PR_NUMBER, result["draft_pr"])
        self.callbacks.clear()
        self.operation()
        self.assertFalse(any(name in ("bootstrap", "push", "create")
                             for name, _ in self.callbacks))

    def test_bootstrap_before_ack_uncertain_no_blind_retry(self):
        self.bootstrap_mode = "before"
        self.refused("R5C1A_EFFECT_RECONCILIATION_REQUIRED")
        self.assertEqual(self.src.base, self.fresh_task())
        first = len([x for x in self.callbacks if x[0] == "bootstrap"])
        self.refused("R5C1A_EFFECT_RECONCILIATION_REQUIRED")
        self.assertEqual(first, len([x for x in self.callbacks if x[0] == "bootstrap"]))
        self.assertIsNone(self.remote_head)
        self.assertEqual([], self.prs)

    def test_push_before_effect_uncertain_no_duplicate_push(self):
        self.push_mode = "before"
        self.refused("R5C1A_EFFECT_RECONCILIATION_REQUIRED")
        self.assertNotEqual(self.src.base, self.fresh_task())
        count = len([x for x in self.callbacks if x[0] == "push"])
        self.refused("R5C1A_EFFECT_RECONCILIATION_REQUIRED")
        self.assertEqual(count, len([x for x in self.callbacks if x[0] == "push"]))
        self.assertIsNone(self.remote_head)
        self.assertEqual([], self.prs)

    def test_draft_before_effect_uncertain_no_duplicate_pr(self):
        self.draft_mode = "before"
        self.refused("R5C1A_EFFECT_RECONCILIATION_REQUIRED")
        self.assertIsNotNone(self.remote_head)
        count = len([x for x in self.callbacks if x[0] == "create"])
        self.refused("R5C1A_EFFECT_RECONCILIATION_REQUIRED")
        self.assertEqual(count, len([x for x in self.callbacks if x[0] == "create"]))
        self.assertEqual([], self.prs)

    def test_no_authorization_denies_before_any_write(self):
        self.allow = False
        self.refused("R5C1A_GRANT_REQUIRED")
        self.assertEqual(self.src.base, self.fresh_task())
        self.assertEqual([], [x for x in self.callbacks if x[0] in
                              ("bootstrap", "push", "create")])

    def test_wrong_read_grant_or_legacy_assignment_refuses(self):
        self.read_allowed = False
        self.refused("V2_MAIN_OBSERVATION_GRANT_REQUIRED")
        self.read_allowed = True
        self.legacy_clear = False
        self.refused("V2_MAIN_EXISTING_V1_ASSIGNMENT")

    def test_missing_or_partial_main_owner_refuses(self):
        with self.assertRaisesRegex(MainError, "R5C1A_OWNER_REQUIRED"):
            self.operation(with_owner=False)
        with self.assertRaisesRegex(MainError, "MAIN_ATTACHMENT_BINDING_INVALID"):
            self.operation(publication_only=False)

    def test_unknown_remote_is_not_absent_and_no_effect(self):
        self.remote_known = False
        self.refused("R5C1A_REMOTE_FACTS_UNKNOWN")
        self.assertEqual(self.src.base, self.fresh_task())

    def test_dirty_task_fails_closed(self):
        (self.worktree/"dirty.txt").write_text("unsaved")
        self.refused("R5C1A_PRODUCT_NOT_PRISTINE")
        self.assertIsNone(self.remote_head)

    def test_incorrect_repo_or_base_ref_refuses(self):
        self.repo = replace(self.repo, repository_id=99)
        self.refused("R5C1A_REMOTE_FACTS_INVALID")
        self.repo = replace(self.repo, repository_id=self.src.identity.repository_id)
        self.repo = replace(self.repo, default_branch_ref="refs/heads/other")
        self.refused("R5C1A_REMOTE_FACTS_INVALID")

    def test_remote_head_conflict_fails_before_push(self):
        self.remote_head = "a"*40
        self.refused("R5C1A_BOOTSTRAP_REMOTE_CONFLICT")
        self.assertEqual(self.src.base, self.fresh_task())

    def test_existing_foreign_or_closed_pr_does_not_get_adopted(self):
        self.prs = [ProductPull(
            PR_NUMBER, REPOSITORY, "other/fork", "main", "task-101",
            self.src.base, "open", True, PRINCIPAL,
            self.src.approved.title, self.src.approved.body)]
        self.refused("R5C1A_BOOTSTRAP_REMOTE_CONFLICT")

    def test_incomplete_pr_enumeration_blocks(self):
        self.complete = False
        self.refused("R5C1A_REMOTE_FACTS_INVALID")

    def test_issue_change_during_grant_refuses_before_bootstrap(self):
        def grant(req):
            self.issues[101]["title"] = "changed"
            return True
        self.authorize = grant
        self.refused("V2_MAIN_ISSUE_SNAPSHOT_DRIFT")
        self.assertEqual(self.src.base, self.fresh_task())

    def test_terminal_or_changed_current_record_fails_closed(self):
        # Source-only tests of v2 current ref/Record are separately extensive.
        from task_record import encode_record
        from metadata_v2 import issue_ref
        source = self.src
        rid, record = encode_record(
            REPOSITORY, "101", source.base,
            {"schema_version": 1, "branch_ref": source.registered.branch_ref,
             "contract_id": source.registered.contract_id,
             "disposition": {"kind": "cancelled"}})
        source.fixture.store().publish_issue(
            "101", [record], record_update=(source.registered.record_id, rid))
        self.refused("V2_MAIN_CURRENT_TASK_UNAVAILABLE")

    def test_wrong_host_binding_and_plan_never_mints_effect(self):
        with gate.hold_verified(self.src.project, self.src.home,
                                self.src.package) as lease:
            with self.assertRaisesRegex(MainError, "R5C1A_HOST_INVALID"):
                service.MainV2ProductPrCapabilities(self.read_issue,{})
            with self.assertRaisesRegex(MainError, "MAIN_HOST_CAPABILITIES_INVALID"):
                service.build_main(self.plan,host={"publish":True})
            with self.assertRaisesRegex(MainError, "R5C1A_HOST_BINDING_MISMATCH"):
                base = service.build_main(self.plan)
                main_module.RepositoryMain(
                    base.store, approved=dict(base.approved),
                    read_issue=self.read_issue, runtime_home=base.runtime_home,
                    port_for_issue=base.port_for_issue,
                    v2_product_pr_for_issue={101:self.host(lease)})

    def test_other_publisher_cannot_select_only_one_of_two_issues(self):
        src = self.src
        second = src.approved_issue(202)
        registered = src.publish_first(second)
        self.plan["tasks"].append({
            "issue": 202, "branch_ref": registered.branch_ref,
            "base_revision": registered.base_revision,
            "contract_id": registered.contract_id, "port": 34202})
        with gate.hold_verified(src.project, src.home, src.package) as lease:
            with self.assertRaisesRegex(MainError, "V2_MAIN_PARTIAL_HOST_SELECTION"):
                self.main(lease)

    def test_no_generic_cli_or_v1_fallback(self):
        with gate.hold_verified(self.src.project, self.src.home,
                                self.src.package) as lease:
            host = service.MainV2ProductPrCapabilities(
                self.read_issue, {101:self.host(lease)})
            with patch.object(service, "load_selection",
                              return_value=copy.deepcopy(self.plan)):
                self.assertEqual(
                    2, service.main(["--config", "/dev/null", "observe"], host=host))

    def test_duplicate_or_foreign_pr_after_publication_never_adopts(self):
        self.operation()
        normal = self.prs[0]
        self.prs.append(replace(normal, number=99))
        self.refused("R5C1A_PR_CONFLICT")
        self.prs[:] = [replace(normal, head_repository="other/fork")]
        self.refused("R5C1A_PR_CONFLICT")
        self.prs[:] = [replace(normal, state="merged", draft=False)]
        self.refused("R5C1A_PR_CONFLICT")
        self.prs[:] = [replace(normal, title="forged title")]
        self.refused("R5C1A_PR_CONFLICT")

    def test_drift_during_push_grant_refuses_before_new_push(self):
        def change(ref):
            self.grants.append(ref)
            if ref.operation == "push":
                self.remote_head = "a"*40
            return True
        self.authorize = change
        self.refused("R5C1A_PUSH_FACTS_DRIFT")
        self.assertEqual([], [x for x in self.callbacks if x[0]=="push"])
        self.assertEqual([], self.prs)

    def test_post_fsync_drift_blocks_effect_and_retains_original_intent(self):
        original_create = RecoveryJournal.create
        def changed_after_fsync(journal, key, record):
            original_create(journal, key, record)
            self.issues[101]["title"] = "externally changed"
        with patch.object(RecoveryJournal, "create",
                          new=changed_after_fsync):
            self.refused("R5C1A_INTENT_EFFECT_BOUNDARY_UNKNOWN")
        self.assertEqual(self.src.base, self.fresh_task())
        self.assertEqual([], [x for x in self.callbacks
                              if x[0] in ("bootstrap","push","create")])
        self.issues[101]["title"] = self.src.approved.title
        self.refused("R5C1A_EFFECT_RECONCILIATION_REQUIRED")
        self.assertEqual(self.src.base, self.fresh_task())

    def test_durable_intent_conflict_does_not_repeat_original_callback(self):
        self.operation()
        before = len([x for x in self.callbacks
                      if x[0] in ("bootstrap","push","create")])
        original = self.grants[0]
        key = publication._key(original)
        receipt = self.journal.read(key)
        self.assertEqual("bootstrap", receipt["operation"])
        modified = dict(receipt)
        modified["subject"] = "a"*40
        payload = publication.json.dumps(
            modified, sort_keys=True, separators=(",", ":")).encode()
        (self.journal.directory / key).write_bytes(payload)
        self.refused("R5C1A_INTENT_CONFLICT")
        self.assertEqual(before, len([x for x in self.callbacks
                                      if x[0] in ("bootstrap","push","create")]))

    def test_wrong_remote_subject_after_pr_fails_without_republish(self):
        self.operation()
        count = len([x for x in self.callbacks
                     if x[0] in ("bootstrap", "push", "create")])
        self.remote_head = "a"*40
        self.refused("R5C1A_REMOTE_REF_CONFLICT")
        self.assertEqual(count, len([x for x in self.callbacks
                                     if x[0] in ("bootstrap", "push", "create")]))

    def test_supervisor_publication_mode_blocks_generic_dispatch(self):
        src = self.src
        with gate.hold_verified(src.project, src.home, src.package) as lease:
            main = self.main(lease)
            with service.MainSupervisor(
                    main, attachment_lease=lease, publication_only=True) as runner:
                with self.assertRaisesRegex(
                        MainError, "V2_MAIN_EXECUTION_NOT_QUALIFIED"):
                    runner.cycle(dispatch=True)
                with self.assertRaisesRegex(
                        MainError, "V2_MAIN_ADMISSION_OWNER_REQUIRED"):
                    runner.admit_worktree(101)

    def test_pending_push_h1_refuses_new_bootstrap_after_external_base_reset(self):
        """R1 P2: H1 push intent must block new H2 bootstrap before effects."""
        src = self.src
        src.attached.git("-C", str(self.worktree), "commit",
                         "--allow-empty", "-qm", "existing Task-specific H1")
        h1 = self.fresh_task()
        self.assertNotEqual(src.base, h1)
        self.push_mode = "before"
        self.refused("R5C1A_EFFECT_RECONCILIATION_REQUIRED")
        self.assertEqual([], self.prs)
        self.assertIsNone(self.remote_head)
        saved_push = next(x for x in self.grants if x.operation == "push")
        self.assertEqual(h1, self.journal.read(
            publication._key(saved_push))["subject"])
        bootstrap_key = publication._key(replace(saved_push, operation="bootstrap"))
        self.assertIsNone(self.journal.read(bootstrap_key))
        src.attached.git("-C", str(self.worktree),
                         "update-ref", src.registered.branch_ref, src.base, h1)
        self.assertEqual(src.base, self.fresh_task())
        effects_before = len([x for x in self.callbacks if x[0] in
                              ("bootstrap","push","create")])
        self.refused("R5C1A_INTENT_CONFLICT")
        self.assertEqual(src.base, self.fresh_task())
        self.assertIsNone(self.journal.read(bootstrap_key))
        self.assertEqual(effects_before, len([x for x in self.callbacks if
                                             x[0] in ("bootstrap","push","create")]))

    def test_pending_draft_h1_refuses_new_push_for_external_h2(self):
        """R1 P2: a later-stage pending Draft cannot be bypassed by H2."""
        src = self.src
        src.attached.git("-C", str(self.worktree), "commit",
                         "--allow-empty", "-qm", "existing Task-specific H1")
        h1 = self.fresh_task()
        self.remote_head = h1  # already pushed before this first-lifecycle lane
        self.draft_mode = "before"
        self.refused("R5C1A_EFFECT_RECONCILIATION_REQUIRED")
        self.assertIsNone(self.journal.read(publication._key(
            replace(self.grants[0], operation="push"))))
        src.attached.git("-C", str(self.worktree), "commit",
                         "--allow-empty", "-qm", "externally changed Task H2")
        self.assertNotEqual(h1, self.fresh_task())
        count = len([x for x in self.callbacks if x[0] in
                     ("bootstrap","push","create")])
        self.refused("R5C1A_INTENT_CONFLICT")
        self.assertEqual(count, len([x for x in self.callbacks if
                                     x[0] in ("bootstrap","push","create")]))
        self.assertEqual(h1, self.remote_head)
        self.assertEqual([], self.prs)
        self.assertIsNone(self.journal.read(publication._key(
            replace(self.grants[0], operation="push"))))
