"""Small, closed executor-name and unqualified-runtime vocabulary for #291."""
from __future__ import annotations

OPENCODE = "opencode"
CLAUDE_CLI = "claude-cli"
EXECUTOR_NAMES = frozenset((OPENCODE, CLAUDE_CLI))


def is_executor_name(value: object) -> bool:
    """Accept only the exact, case-sensitive executor literals."""
    return type(value) is str and value in EXECUTOR_NAMES


def claude_runtime_diagnostic() -> dict[str, object]:
    """Return facts only; this is not a qualification or dispatch capability."""
    return {
        "executor": CLAUDE_CLI,
        "qualification": "UNQUALIFIED",
        "dispatch_allowed": False,
        "task_completed": False,
        "session_state": "UNKNOWN",
        "ask_capability": "UNQUALIFIED",
        "effective_policy": "UNVERIFIED",
        "reason": "CLAUDE_EXECUTOR_UNQUALIFIED",
    }
