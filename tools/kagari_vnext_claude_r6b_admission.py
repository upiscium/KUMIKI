"""R6B Phase A: Main-owned Claude Task identity observation, never execution."""
from __future__ import annotations
from typing import Any
from . import kagari_vnext_claude_lane_b as lane
from .kagari_vnext_main import RepositoryMain
from .kagari_vnext_executor_selection import CLAUDE_CLI


def _observe_main_owned_binding(
    main: RepositoryMain, task: lane.LaneBTask, session_id: str,
) -> dict[str, Any]:
    """Observe an exact authorized Task binding; never grant a model launch."""
    if (type(main) is not RepositoryMain or type(task) is not lane.LaneBTask
            or not lane._validate_task_locator(task, session_id)):
        return lane._public_result("UNQUALIFIED", "R6B_INPUT_INVALID")
    try:
        with main._main_owner_guard:
            owner = main._main_owner
            if owner is None:
                return lane._public_result("UNQUALIFIED", "MAIN_OWNER_REQUIRED")
            owner._assert_owned(main)
            if main.v2_readonly_for_issue:
                return lane._public_result("UNQUALIFIED", "V2_MAIN_EXECUTION_NOT_QUALIFIED")
            choice = main._selected_choice(task.issue)
            if (choice.executor != CLAUDE_CLI or
        task.repository != main.store.repository or
        task.remote != main.store.remote or
                    task.project_root != main.store.root or
                    (choice.issue, choice.branch_ref, choice.base_revision, choice.contract_id)
                    != (task.issue, task.branch_ref, task.base_revision, task.expected_contract_id)):
                return lane._public_result("UNQUALIFIED", "R6B_SELECTION_MISMATCH")
            identity = lane._task_identity(task)
            if identity is None:
                return lane._public_result("UNQUALIFIED", "R6B_TASK_BINDING_INVALID")
            owner._assert_owned(main)
            if main._selected_choice(task.issue) != choice:
                return lane._public_result("UNKNOWN", "R6B_RECONCILIATION_REQUIRED")
            return lane._public_result(
                "UNQUALIFIED", "EFFECTIVE_POLICY_UNVERIFIED",
                stage="TASK_BINDING_OBSERVED", session_id=session_id,
            )
    except Exception:
        return lane._public_result("UNKNOWN", "R6B_RECONCILIATION_REQUIRED")
