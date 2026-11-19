"""Labels, approval and Ready decisions of the KAGARI MVI worker (ADR-0007 D2, ADR-0010 D3).

State lives only in GitHub Issue labels. Pure functions over Issue and event dicts: no
GitHub calls, no processes. Split out of kagari_mvi.py (#490).
"""
from __future__ import annotations

import datetime
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

TEST_REPORT_PREFIX = "kagari-mvi: test result recorded by the runner"

APPROVED = "kagari:approved"
RUNNING = "kagari:running"
IMPLEMENTED = "kagari:implemented"
REVIEWED = "kagari:reviewed"
BLOCKED = "kagari:blocked"
EXCLUDED = (RUNNING, IMPLEMENTED, REVIEWED, BLOCKED)

LABELS = {
    APPROVED: ("0e8a16", "User only: this Issue may be implemented"),
    RUNNING: ("fbca04", "kagari-mvi worker: in progress (lock)"),
    IMPLEMENTED: ("1d76db", "kagari-mvi worker: Draft PR created"),
    REVIEWED: ("5319e7", "kagari-mvi worker: review comment posted"),
    BLOCKED: ("b60205", "kagari-mvi worker: failed or needs a decision"),
}


def label_names(issue: Dict[str, Any]) -> List[str]:
    return [label["name"] for label in issue.get("labels", [])]


def has_labels(issue: Dict[str, Any], required: Sequence[str], excluded: Sequence[str]) -> bool:
    """An open Issue (not a PR) with every required label and none of the excluded ones."""
    names = label_names(issue)
    return ("pull_request" not in issue and issue.get("state") == "open"
            and all(name in names for name in required) and not any(name in names for name in excluded))


def is_candidate(issue: Dict[str, Any]) -> bool:  # approved, in none of the worker states
    return has_labels(issue, (APPROVED,), EXCLUDED)


def is_review_candidate(issue: Dict[str, Any]) -> bool:  # implemented, not locked/reviewed/blocked
    return has_labels(issue, (APPROVED, IMPLEMENTED), (RUNNING, REVIEWED, BLOCKED))


def oldest(issues: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return min(issues, key=lambda issue: (issue["created_at"], issue["number"])) if issues else None


def select_issue(issues: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The oldest candidate by created_at (then number), or None."""
    return oldest([issue for issue in issues if is_candidate(issue)])


def last_approval(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The last `labeled` event for kagari:approved ({} if none)."""
    found: Dict[str, Any] = {}
    for event in events:
        if event.get("event") == "labeled" and (event.get("label") or {}).get("name") == APPROVED:
            found = event
    return found


def approval_actor(events: List[Dict[str, Any]]) -> Optional[str]:
    """Login of the actor of the last `labeled` event for kagari:approved."""
    return (last_approval(events).get("actor") or {}).get("login")


def check_body_unchanged(edited_at: Optional[str], approved_at: Optional[str]) -> Optional[str]:
    """None if the body was not edited after approval, else the reason to block (#422)."""
    when = lambda value: datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))  # noqa: E731
    if edited_at is None or (approved_at is not None and when(edited_at) <= when(approved_at)):
        return None
    return ("The Issue body was edited after `%s` was added (edited %s, approved %s). Check the "
            "body, then remove and add `%s` again." % (APPROVED, edited_at, approved_at, APPROVED))


def report_header(test_report: str) -> str:
    """The runner-written lines of its test comment: the test output and Claude's declaration
    follow in code blocks and may contain any text, such as a fake `- exit code: 0`."""
    return test_report.split("```", 1)[0]


def runner_exit_code(test_report: str) -> Optional[int]:
    # Right under the test command line, which the runner writes from the approved Issue.
    found = re.search(r"^- test command: .*\n- exit code: (-?\d+)$", report_header(test_report), re.M)
    return int(found.group(1)) if found else None


def ready_decision(outcome: Dict[str, Any], fresh: bool, test_report: str) -> Tuple[bool, str]:
    """Ready only on every AC and TESTS PASS and a passing runner test of this head (ADR-0010 D3)."""
    why = [reason for failed, reason in (
        (any(status != "PASS" for status, _ in outcome["verdicts"]), "not every criterion is PASS"),
        (outcome["tests"][0] != "PASS", "TESTS is %s" % outcome["tests"][0]),
        (outcome["errored"], "the reviewer session ended with an error"),
        (not fresh or runner_exit_code(test_report) != 0, "no passing runner test of this head"))
        if failed]
    if why:
        return False, "\n- ready for review: no, the PR stays Draft (%s)\n" % "; ".join(why)
    return True, "\n- ready for review: yes, marked by the runner; a human decides the merge\n"


def check_actor(actor: Optional[str], allowed: Sequence[str]) -> Optional[str]:
    """None if allowed, else the reason to block (comment text)."""
    if actor is None:
        return "No `labeled` event for `%s` was found in the timeline." % APPROVED
    if actor not in allowed:
        return ("`%s` was added by `%s`, who is not in the allow list (%s)."
                % (APPROVED, actor, ", ".join("`%s`" % name for name in allowed)))
    return None
