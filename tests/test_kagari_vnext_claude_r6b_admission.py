"""R6B Main-owned Claude Task admission observation, fake metadata only."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import uuid
import unittest
from unittest.mock import patch

from tests import test_kagari_vnext_claude_lane_b as fixture_module
from tools import kagari_vnext_claude_r6b_admission as candidate
from tools import kagari_vnext_claude_lane_b as lane
from tools import kagari_vnext_main as mainmod
from tools import kagari_vnext_main_service as service


class R6BAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.ClaudeLaneBOfflineFixtureTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture.task
        t = self.task
        self.main = mainmod.RepositoryMain(
            self.fixture.fixture.store,
            approved={340: mainmod.ApprovedTask(
                t.issue, t.branch_ref, t.base_revision,
                t.expected_contract_id, "claude-cli")},
            read_issue=lambda _n: {},
            runtime_home=self.fixture.runtime_root,
            port_for_issue=lambda _n: 12345,
        )
        self.sid = str(uuid.uuid4())
        self.lock_home = self.fixture.fixture.temp_root / "main-lock-state"
        self.lock_home.mkdir(mode=0o700)

    def _assert_denied(self, result):
        self.assertEqual("UNQUALIFIED", result["qualification"])
        self.assertFalse(result["dispatch_allowed"])
        self.assertFalse(result["task_completed"])
        self.assertEqual("UNQUALIFIED", result["ask_capability"])
        self.assertEqual("UNVERIFIED", result["effective_policy"])

    def test_requires_live_main_owner_and_contract(self):
        r = candidate._observe_main_owned_binding(self.main, self.task, self.sid)
        self._assert_denied(r)
        self.assertEqual("MAIN_OWNER_REQUIRED", r["reason"])
        with patch.object(service, "_host_state_home", return_value=self.lock_home):
            with service.MainSupervisor(self.main):
                observed = candidate._observe_main_owned_binding(
                    self.main, self.task, self.sid)
                self._assert_denied(observed)
                self.assertEqual("EFFECTIVE_POLICY_UNVERIFIED", observed["reason"])
                self.assertEqual("TASK_BINDING_OBSERVED", observed["stage"])
                self.assertEqual(self.sid, observed["session_id"])
                wrong = candidate._observe_main_owned_binding(
                    self.main, replace(self.task, expected_contract_id="f" * 64), self.sid)
                self._assert_denied(wrong)
                self.assertEqual("R6B_SELECTION_MISMATCH", wrong["reason"])
        self.assertEqual("UNQUALIFIED", lane.dispatch()["status"])

    def test_wrong_executor_and_invalid_inputs_fail_closed(self):
        self.assertEqual("R6B_INPUT_INVALID",
                         candidate._observe_main_owned_binding(
                             object(), self.task, self.sid)["reason"])
        self.assertEqual("R6B_INPUT_INVALID",
                         candidate._observe_main_owned_binding(
                             self.main, self.task, "not-uuid")["reason"])
        with patch.object(service, "_host_state_home", return_value=self.lock_home):
            with service.MainSupervisor(self.main):
                broken = replace(self.task, worktree=Path("/tmp/unregistered-worktree"))
                r = candidate._observe_main_owned_binding(self.main, broken, self.sid)
                self._assert_denied(r)
                self.assertEqual("R6B_TASK_BINDING_INVALID", r["reason"])

    def test_wrong_executor_and_unknown_paths_do_not_grant(self):
        with patch.object(service, "_host_state_home", return_value=self.lock_home):
            with service.MainSupervisor(self.main):
                with patch.object(self.main, "_selected_choice", return_value=mainmod.ApprovedTask(
                    self.task.issue, self.task.branch_ref, self.task.base_revision,
                    self.task.expected_contract_id, "opencode")):
                    r = candidate._observe_main_owned_binding(self.main, self.task, self.sid)
                    self.assertEqual("UNQUALIFIED", r["status"])
                    self.assertEqual("R6B_SELECTION_MISMATCH", r["reason"])
                    self.assertFalse(r["dispatch_allowed"])
                with patch.object(lane, "_task_identity", side_effect=RuntimeError("synthetic")):
                    r = candidate._observe_main_owned_binding(self.main, self.task, self.sid)
                    self.assertEqual("UNKNOWN", r["status"])
                    self.assertEqual("R6B_RECONCILIATION_REQUIRED", r["reason"])
                    self.assertFalse(r["dispatch_allowed"])
                    self.assertFalse(r["task_completed"])
                with patch.object(lane, "_task_identity", return_value=None):
                    r = candidate._observe_main_owned_binding(self.main, self.task, self.sid)
                    self.assertEqual("UNQUALIFIED", r["status"])
                    self.assertEqual("R6B_TASK_BINDING_INVALID", r["reason"])
                    self.assertFalse(r["dispatch_allowed"])
                with patch.object(self.main._main_owner, "_assert_owned", side_effect=RuntimeError("synthetic")):
                    r = candidate._observe_main_owned_binding(self.main, self.task, self.sid)
                    self.assertEqual("UNKNOWN", r["status"])
                    self.assertFalse(r["dispatch_allowed"])
