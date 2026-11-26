"""Why the worker stopped: four kinds and the next step for a human (HDC R1, #475). Pure."""
from __future__ import annotations

import re
from typing import Optional, Tuple

from kagari_mvi_exec import Blocked

HUMAN_DECISION, OPERATIONAL, TECHNICAL, UNKNOWN = "HUMAN_DECISION", "OPERATIONAL", "TECHNICAL", "UNKNOWN"

FIX = ("Fix the Issue or the repository as described, then remove kagari:blocked "
       "(and re-add kagari:approved if the Issue body changed).")
STEPS = {
    "auth": "Log in again as kagari-agent, then remove kagari:blocked.",
    "pr": "Review the PR, or close it and delete the branch, then remove kagari:blocked.",
    "test": "See the PR comment; fix the Issue or the code, then remove kagari:blocked.",
    "temp": ("Usually temporary (GitHub or the network). Wait, then remove kagari:blocked. "
             "If a branch was already pushed without a PR, delete it first."),
    "unknown": ("The effect is unclear. Compare GitHub and the branch with the work directory, "
                "then decide; do not just retry."),
    "decide": "Decide what the agent asked (see the reason), then remove kagari:blocked.",
    "env": "Fix the environment, credentials or external service the agent named, then remove kagari:blocked.",
}
TEMPORARY = ("Internal Server Error", "Bad Gateway", "Service Unavailable", "Gateway Timeout", "HTTP 502",
             "HTTP 503", "HTTP 504", "Could not resolve host", "Connection reset", "Connection timed out",
             "timed out", "rate limit", "unexpected end of JSON input")
BEFORE_START = ("No `labeled` event", "not in the allow list", "was edited after", "no test command",
                "placement sections", "not one-line bullets", "settings.local.json",
                "touch files that need a human decision", "Issue body has no", "decision sections")
AGENT = re.compile(r"status:\s*BLOCKED(?::\s*(HUMAN_DECISION|OPERATIONAL):)?", re.I)


def classify(stage: str, error: Optional[BaseException], text: str = "") -> Tuple[str, str]:
    """(kind, next_step). `text` is the message or output; `stage` is "start" before the run began."""
    message = "\n".join(part for part in (str(error) if error is not None else "",
                                          getattr(error, "output", ""), text or "") if part)
    if error is None or isinstance(error, Blocked):
        if message.startswith("Claude Code failed to authenticate"):
            return OPERATIONAL, STEPS["auth"]
        agent = AGENT.search(message)
        if agent and agent.group(1):
            return (HUMAN_DECISION, STEPS["decide"]) if agent.group(1).upper() == HUMAN_DECISION \
                else (OPERATIONAL, STEPS["env"])
        if agent:
            return TECHNICAL, STEPS["test"]
        if re.search(r"PR #\d+ for .* already exists", message):
            return HUMAN_DECISION, STEPS["pr"]
        if "Draft PR #" in message and "exit code is" in message:
            return TECHNICAL, STEPS["test"]
        if stage == "start" or any(key in message for key in BEFORE_START):
            return HUMAN_DECISION, FIX
        return UNKNOWN, STEPS["unknown"]
    if any(word.lower() in message.lower() for word in TEMPORARY):
        return OPERATIONAL, STEPS["temp"]
    return UNKNOWN, STEPS["unknown"]
