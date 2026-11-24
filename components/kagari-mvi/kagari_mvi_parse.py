"""Reading Claude's and the reviewer's output: outcome, declared test purposes, verdicts.

Pure text functions; the output is untrusted text, so everything here is bounded.
Split out of kagari_mvi_exec.py (#490).
"""
from __future__ import annotations

import datetime
import json
import re
from typing import Any, Dict, List, Optional, Tuple

TAIL_LINES = 20
TAIL_CHARS = 2000
PURPOSE_LINES = 40  # Claude's declared test purposes stay small for comments and TSUMGI
PURPOSE_BYTES = 3000
AUTH_PATTERN = re.compile(r"login expired|not logged in|invalid api key|authentication_error|"
                          r"oauth token|please run /login|api error:? *401\b|\b401 unauthorized", re.I)
# [^\w\n] / [ \t]: a verdict never spans lines (an empty evidence must not swallow the next line).
VERDICT_LINE = re.compile(r"^[^\w\n]*AC(\d+)[^\w\n]*?[:：][^\w\n]*(PASS|FAIL|UNKNOWN)\b[*_]*[ \t]*"
                          r"[-—–:：]*[ \t]*(.*)$", re.M | re.I)
TESTS_LINE = re.compile(r"^[^\w\n]*TESTS[^\w\n]*?[:：][^\w\n]*(PASS|FAIL|UNKNOWN)\b[*_]*[ \t]*"
                        r"[-—–:：]*[ \t]*(.*)$", re.M | re.I)
DELEGATION_LINE = re.compile(r"^[^\w\n]*DELEGATION[^\w\n]*?[:：][^\w\n]*(UNKNOWN|OUT|IN)\b[*_]*[ \t]*"
                             r"[-—–:：]*[ \t]*(.*)$", re.M | re.I)

# A sentence ends at . 。 ! ? ！ ？ … (before closing brackets or quotes), but not at an abbreviation's dot.
SENTENCE_END = re.compile(r"(?<!\be\.g)(?<!\bi\.e)(?<!\betc)(?<!\bvs)(?<!\bcf)"
                          r"[.。!?！？…][)\]）」』\"'”’]*$", re.I)


def tail(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-TAIL_LINES:])[-TAIL_CHARS:]


def agent_outcome(output: str, failed: bool) -> Dict[str, Any]:
    """Summarize Claude's stream-json: auth failure, final status, permission denials."""
    result, plain, reset_at = None, [], None
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            plain.append(line)  # stderr and other non-JSON text
            continue
        if isinstance(event, dict):
            info = (event.get("api_error_params") or {}).get("rate_limit_info") or {}
            reset_at = info.get("resetsAt") or reset_at
        if isinstance(event, dict) and event.get("type") == "result":
            result = event
    errored = failed or result is None or bool(result.get("is_error"))
    # Only the final result decides: a limit that was retried past, then another failure, is a failure.
    limited = errored and (result or {}).get("api_error") == "usage_limit_reached"
    text = str((result or {}).get("result") or "")
    statuses = re.findall(r"status:\s*(COMPLETED|BLOCKED[^\n]*)", text)
    return {
        "purposes": test_purposes(text),
        # Only the final result and stderr: file contents and ids may contain "401".
        "errored": errored,
        "limited": limited,
        "resets": (datetime.datetime.fromtimestamp(reset_at, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                   if isinstance(reset_at, (int, float)) else text.strip() or "(unknown)"),
        "auth": errored and bool(AUTH_PATTERN.search("\n".join(plain + [text]))),
        "status": statuses[-1].strip() if statuses else "(none)",
        "completed": not errored and bool(statuses) and statuses[-1] == "COMPLETED",
        "denials": len((result or {}).get("permission_denials") or []),
        "denied": [d for d in ((result or {}).get("permission_denials") or []) if isinstance(d, dict)][:50],
        "detail": tail("\n".join(plain + [text])),  # readable: no raw stream-json
        "text": text,
    }


def test_purposes(text: str) -> Optional[str]:
    """Claude's last `tests:` list and `untested:` note (#453), bounded: it is untrusted text."""
    lines = text.replace("\r\n", "\n").splitlines()
    starts = [i for i, line in enumerate(lines) if re.match(r"^\W*tests\W*:\W*$", line, re.I)]
    if not starts:
        return None
    items: List[str] = []
    untested: Optional[List[str]] = None
    inline = gap = False
    bullet = r"^(?:[-*+]|\d{1,2}[.)])\s+\S"  # `2026. ...` is prose, not item 2026
    for raw in lines[starts[-1] + 1:]:
        line, fence = raw.strip(), raw.strip()[:3] in ("```", "~~~")
        note = re.match(r"^\W*untested\W*:\W*(.*)$", line, re.I)
        if re.match(r"^\W*status\W*:", line, re.I):
            break
        if untested is not None:  # one inline line, or the list right under `untested:`
            if inline or fence:
                break
            if re.match(bullet, line):
                untested.append(line)
            elif line and untested and not gap and raw[:1].isspace() and not SENTENCE_END.search(
                    untested[-1]):  # a bullet wrapped mid-sentence
                untested[-1] += " " + line
            elif line and untested and not gap and raw[:1].isspace():
                continue  # prose under a finished bullet: skip it, a later bullet still counts
            elif line:
                break  # prose after the declaration
            gap = not line and bool(untested)
        elif note:
            untested, inline = ([note.group(1)], True) if note.group(1) else ([], False)
        elif fence:
            continue
        elif re.match(bullet, line):
            items.append(line)
        elif re.match(r"^\d{3,}[.)]\s", line):
            continue  # `100. ...` is neither an item nor the continuation of one
        elif line and items:  # a wrapped item
            items[-1] += " " + line
    declared = "tests:\n%s\nuntested: %s" % ("\n".join(items), "; ".join(untested) if untested
                                              else "(not declared)" if untested is None else "")
    if len(declared.encode()) > PURPOSE_BYTES or len(declared.splitlines()) > PURPOSE_LINES:
        cut = declared.encode()[:PURPOSE_BYTES].decode(errors="ignore")  # bytes: TSUMGI's budget
        declared = "\n".join(cut.splitlines()[:PURPOSE_LINES]) + "\n(truncated by the runner)"
    return declared if items or untested else None


def tests_verdict(text: str) -> Tuple[str, str]:
    """The reviewer's `TESTS:` line; a missing or conflicting one counts as UNKNOWN."""
    found = [(status.upper(), evidence.strip()) for status, evidence in TESTS_LINE.findall(text)]
    if len({status for status, _ in found}) == 1:
        return found[-1]
    if not found:
        return ("UNKNOWN", "(no TESTS verdict from the reviewer)")
    return ("UNKNOWN", "(conflicting TESTS verdicts from the reviewer)")


def delegation_verdict(text: str) -> Tuple[str, str]:
    """The reviewer's `DELEGATION:` line (IN|OUT|UNKNOWN); a missing or conflicting one is UNKNOWN."""
    found = [(status.upper(), evidence.strip()) for status, evidence in DELEGATION_LINE.findall(text)]
    if found and len({status for status, _ in found}) == 1:
        return found[-1]
    return ("UNKNOWN", "(conflicting DELEGATION verdicts from the reviewer)" if found
            else "(no DELEGATION verdict from the reviewer)")


def review_verdicts(text: str, count: int) -> List[Tuple[str, str]]:
    """(status, evidence) per criterion; a missing line counts as UNKNOWN."""
    found: Dict[int, Tuple[str, str]] = {}
    for n, status, evidence in VERDICT_LINE.findall(text):
        verdict = (status.upper(), evidence.strip())
        if int(n) in found and found[int(n)][0] != verdict[0]:
            verdict = ("UNKNOWN", "(conflicting verdicts from the reviewer)")
        found[int(n)] = verdict
    return [found.get(i, ("UNKNOWN", "(no verdict from the reviewer)")) for i in range(1, count + 1)]
