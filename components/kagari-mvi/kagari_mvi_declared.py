"""Permissions an Issue body declares for its own run (#431, ADR-0010 D2).

`### 追加で許可する操作` lists `- bash: <command>` lines. They are never written to the ledger: run-once puts
grants made from them into this run's snapshot only (scope `issue`). Approval is the consent; a later body edit
stops the run (#422). Only the body is read, never a comment.
"""
from __future__ import annotations

import datetime
import re
from typing import Any, Dict, List, Optional

import kagari_mvi_ledger as ledger
import kagari_mvi_shell as shell
from kagari_mvi_issue import section_text

HEADING = re.compile(r"^#{2,6}[ \t]*(?:追加で許可する操作|Additional permissions)[ \t]*$", re.M | re.I)
WIDER = re.compile(r"scope|スコープ|リポジトリ全体|ホスト全体|repo-wide|host-wide|--repo\b|--scope", re.I)


def declared(body: Optional[str]) -> Optional[List[str]]:
    """The non-blank lines of the section; None without the heading."""
    text = section_text(body, HEADING)
    if text is None:
        return None
    return [line.strip() for line in text.splitlines() if line.strip()]


def problem(line: str) -> Optional[str]:
    """Why a declared line cannot be used, or None. Same rules as `ledger add --kind bash`."""
    bullet = re.match(r"^[-*]\s+(.*)$", line)
    if not bullet or not bullet.group(1).strip():
        return "not a one-line bullet with text, or empty"
    kind, sep, match = bullet.group(1).partition(":")
    if not sep or kind.strip().lower() != "bash":
        return "only `bash: <command>` is supported"
    match = match.strip().strip("`").strip()
    if not match:
        return "empty command"
    if WIDER.search(match):
        return "the scope cannot be widened from an Issue (this Issue's run only)"
    if match == "*" or shell.simple_argv(match) in (None, [], ["*"]):
        return "must be one plain command (no &&, ;, |, redirects or substitutions; not `*` alone)"
    if shell.command_denied(match):
        return "`%s` is denied: no grant can allow it" % shell.command_denied(match)
    if shell.unreadable_ssh(shell.simple_argv(match)):
        return "unreadable ssh command"
    return None


def problems(body: Optional[str]) -> List[str]:
    return ["`%s`: %s" % (line[:80], why) for line in declared(body) or []
            for why in [problem(line)] if why]


def matches(body: Optional[str]) -> List[str]:
    """The commands declared (valid lines only)."""
    return [re.match(r"^[-*]\s+[^:]*:(.*)$", line).group(1).strip().strip("`").strip()  # type: ignore[union-attr]
            for line in declared(body) or [] if problem(line) is None]


def grants(body: Optional[str], repo: str, number: int, now: datetime.datetime) -> List[Dict[str, Any]]:
    expires = ledger.stamp(now + datetime.timedelta(days=1))
    return [{"id": "issue-%d-decl-%d" % (number, i), "kind": "bash", "match": match, "scope": "issue",
             "repo": repo, "issue": number, "expires": expires, "created": ledger.stamp(now),
             "reason": "declared in the Issue body", "revoked": None, "declared": True}
            for i, match in enumerate(matches(body), start=1)]


def report(commands: List[str], allowed: List[Dict[str, Any]]) -> str:
    """Comment section: the operations this Issue's declaration allowed, and what was actually used."""
    if not commands:
        return ""
    clean = lambda text: ledger.quiet(str(text)[:200].replace("`", "'").replace("\n", " "))  # noqa: E731
    lines = ["", "Operations allowed by this Issue's declaration (`### 追加で許可する操作`, this run only):", ""]
    lines += ["- `%s`" % clean(command) for command in commands[:ledger.MAX_LISTED * 2]]
    used = [r for r in allowed if str(r.get("grant", "")).startswith("issue-") and "-decl-" in str(r.get("grant"))]
    lines += ["", "Declared operations actually used (audit):", ""]
    lines += ["- `%s`" % clean(ledger.describe(r.get("tool", ""), r.get("input") or {})) for r in used[:20]] or ["- none"]
    return "\n".join(lines) + "\n"
