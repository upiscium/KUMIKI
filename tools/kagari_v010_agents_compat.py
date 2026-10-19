#!/usr/bin/env python3
"""Read-only KAGARI v0.1.0 AGENTS.md coexistence / conflict diagnostics.

This module detects only known exact legacy-v3 lifecycle directives. It never
automatically upgrades, grants permissions, modifies a Project or executes
user-provided code. Its proposed patch is a preview, not consent or authority.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import stat
import sys
import tomllib
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_INSTRUCTIONS = 512 * 1024
MAX_METADATA = 64 * 1024
BEGIN = "<!-- BEGIN KAGARI v0.1.0 SCOPED SESSION ADDENDUM -->"
END = "<!-- END KAGARI v0.1.0 SCOPED SESSION ADDENDUM -->"
CONSUMER = "KAGARI_NATIVE_V4_TASK"
UPSTREAM = "github:upiscium/Templates"
# Python interprets the \u0060 sequences below as literal Markdown backticks.
KNOWN_CONFLICTS: tuple[dict[str, str], ...] = (
    {
        "id": "V3_MANDATORY_INITIALIZE",
        "signature": (
            "- Before planning, editing, delegation, or project commands in a new "
            "primary-agent or Task-Orchestrator session, read \u0060.automation/INIT.md\u0060 "
            "and complete the \u0060initialize\u0060 skill. "
            "Initialization is read-only and failures block work."
        ),
        "required": "",
        "operation": "KAGARI primary launch and parallel native Task delegation",
        "reason": (
            "Legacy primary initialization is mandatory before delegation, but "
            "KAGARI uses separate host preflight, TaskSpec and native-v4 Contract."
        ),
    },
    {
        "id": "V3_EXCLUSIVE_TASK_HANDOFF",
        "signature": (
            "- Main accepts exactly one startup handoff: a newly started "
            "pristine Task must use \u0060just agent::task-start-from-issue "
            "<numeric-issue> <slug>\u0060 followed by "
            "\u0060just agent::contract-check <task>\u0060 returning exactly "
            "\u0060status: READY\u0060 with \u0060mode: initial\u0060; an existing "
            "already-launched resumable Task must use only "
            "\u0060just agent::contract-resume-check <task>\u0060 returning "
            "exactly \u0060status: READY\u0060 with \u0060mode: resume\u0060. "
            "Main launches exactly one Task Orchestrator only after the "
            "matching handoff. Placeholder Tasks and the low-level "
            "\u0060task-start\u0060 path are not normal Main authority; "
            "\u0060integration-pending\u0060, \u0060merged\u0060, and "
            "\u0060cancelled\u0060 are not resumable."
        ),
        "required": "",
        "operation": "KAGARI TaskSpec-based Task creation and dispatch",
        "reason": (
            "The legacy exclusive Issue/Just/READY handoff is incompatible with "
            "the native-v4 host-owned TaskSpec/Contract lifecycle."
        ),
    },
    {
        "id": "V3_GUARDED_JUST_MUTATIONS",
        "signature": (
            "- Do not bypass guarded Just recipes with raw state-changing "
            "Git or GitHub commands."
        ),
        "required": "",
        "operation": "KAGARI host-owned allowlisted local product commit",
        "reason": (
            "The KAGARI host commits an approved, tested exact Product scope "
            "instead of calling the legacy v3 Just Task commit route."
        ),
    },
    {
        "id": "V3_EXCLUSIVE_TASK_ORCHESTRATOR",
        "signature": (
            "- One Task owns one branch, one worktree, one disposable Task State, "
            "and one Task Orchestrator."
        ),
        "required": "",
        "operation": "KAGARI native-v4 metadata and concurrent child sessions",
        "reason": (
            "KAGARI stores native-v4 Contract/Record/Evidence outside the "
            "legacy Task-Orchestrator and .task-state lifecycle."
        ),
    },
)

ADDENDUM = """\
<!-- BEGIN KAGARI v0.1.0 SCOPED SESSION ADDENDUM -->
## KAGARI-managed execution exception (separate Human approval required)

Only for sessions launched through the independently installed KAGARI runtime,
with exact Project/TaskSpec/Contract identity and a valid human consent receipt:
- The AgentCore v3 requirements for \u0060.automation/INIT.md\u0060 initialization,
  the legacy Issue/Just/READY Task handoff, v3-only Task-Orchestrator state and
  Just Task commit routes are replaced FOR THAT SESSION ONLY by the KAGARI host
  preflight, native-v4 Contract, scoped concurrent child delegation, tests,
  allowlisted host product commit, and native-v4 Evidence/Task View readback.
- Project engineering conventions, protected files, approved model selection,
  scope, tests and human authority for Push/PR/Merge/Release stay in force.
- Leaf agents remain unable to delegate again or modify unrelated files.
- All ordinary AgentCore v3 sessions retain every original rule unchanged.

This block is a proposed exact-diff remediation, NOT evidence of approval.
<!-- END KAGARI v0.1.0 SCOPED SESSION ADDENDUM -->
"""


class CompatibilityBlocked(ValueError):
    """Unsafe input or unparseable Project metadata."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise CompatibilityBlocked(reason)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def checked_file(path: Path, cap: int, *, optional: bool) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if optional:
            return None
        raise CompatibilityBlocked("required Project file missing: " + path.name) from None
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink()
            and info.st_nlink == 1 and info.st_size <= cap,
            "unsupported Project file type/link/size: " + path.name)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CompatibilityBlocked("Project file unreadable: " + path.name) from exc
    require(len(raw) <= cap, "Project file exceeded inspection bound: " + path.name)
    return raw


def decode_text(raw: bytes, label: str) -> str:
    try:
        result = raw.decode("utf-8")
    except UnicodeError as exc:
        raise CompatibilityBlocked("invalid UTF-8 in " + label) from exc
    require("\x00" not in result, "NUL in " + label)
    return result


def parse_toml(raw: bytes | None, label: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        result = tomllib.loads(decode_text(raw, label))
    except tomllib.TOMLDecodeError as exc:
        raise CompatibilityBlocked("invalid TOML in " + label) from exc
    require(type(result) is dict, "expected TOML table in " + label)
    return result


def active_markdown_lines(source: str) -> list[tuple[int, str]]:
    """Only active bullets; ignore examples, blockquotes and HTML comments."""
    rows: list[tuple[int, str]] = []
    fenced: str | None = None
    html_code_block: str | None = None
    in_html_comment = False
    for number, original in enumerate(source.splitlines(), start=1):
        stripped = original.lstrip()
        # Four-space/tabs are Markdown indented code, not active directives.
        if original.startswith(("    ", "\t")):
            continue
        marker_match = re.match(r"^(" + chr(96) + r"{3,}|~{3,})", stripped)
        if marker_match and not in_html_comment:
            marker = marker_match.group(1)
            if fenced is None:
                fenced = marker
            elif marker[0] == fenced[0] and len(marker) >= len(fenced):
                fenced = None
            continue
        if fenced is not None:
            continue
        # HTML code/quotation blocks also display historical policy examples,
        # not live directives. Skip their full bodies and boundary lines.
        if html_code_block is not None:
            if re.search(r"</\s*" + html_code_block + r"\s*>", stripped, re.I):
                html_code_block = None
            continue
        opening_html = re.match(
            r"^<\s*(pre|code|script|style|blockquote|textarea)\b[^>]*>",
            stripped, re.I)
        if opening_html and not in_html_comment:
            name = opening_html.group(1).lower()
            if re.search(r"</\s*" + name + r"\s*>", stripped, re.I) is None:
                html_code_block = name
            continue
        # Skip the whole line containing a comment marker, including text on
        # either side. This favors missed matches over an ungrounded Human Ask.
        remaining = original
        saw_comment = False
        while remaining:
            if in_html_comment:
                saw_comment = True
                closing = remaining.find("-->")
                if closing < 0:
                    break
                in_html_comment = False
                remaining = remaining[closing + 3:]
            else:
                opening = remaining.find("<!--")
                if opening < 0:
                    break
                saw_comment = True
                in_html_comment = True
                remaining = remaining[opening + 4:]
        if saw_comment or in_html_comment or stripped.startswith(">"):
            continue
        if stripped.startswith("- "):
            rows.append((number, stripped))
    return rows


def detect_known_conflicts(source: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for rule in KNOWN_CONFLICTS:
        expected = [x for x in rule["required"].split("|") if x]
        for lineno, line in active_markdown_lines(source):
            # ALL four known legacy rules require exact whole-line identity.
            # An edited line is not evidence of an active original v3 directive.
            if line != rule["signature"]:
                continue
            if not all(fragment in line for fragment in expected):
                continue
            findings.append({
                "id": rule["id"],
                "line": lineno,
                "exact_instruction": line,
                "line_sha256": sha(line.encode("utf-8")),
                "conflicting_operation": rule["operation"],
                "why_conflicting": rule["reason"],
                "recognition": "EXACT_ACTIVE_MARKDOWN_V3_RULE",
                "proposed_scope": "KAGARI_MANAGED_SESSIONS_ONLY",
            })
    return findings


# A deliberately tiny catalogue of unambiguous but NON-AUTHENTICATED custom
# prohibitions. They warrant review, never an automatic edit or "confirmed"
# authorization Ask. Broader natural-language conflict detection is deferred.
SUSPECTED_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("MODIFIED_V3_EXCLUSIVE_TASK_HANDOFF", re.compile(
        r"^- Main accepts exactly one startup handoff:.+$")),
    ("MODIFIED_V3_INITIALIZE", re.compile(
        r"^- Before planning, editing, delegation, or project commands in "
        r"a new primary-agent or Task-Orchestrator session,.+$")),
    ("CUSTOM_NATIVE_TASK_DENIAL", re.compile(
        r"^- (?:Never|Do not|Don't) (?:use|invoke|call) "
        r"(?:the )?(?:OpenCode )?(?:Task|task) tool[.!]?$", re.IGNORECASE)),
    ("CUSTOM_SUBAGENT_DENIAL", re.compile(
        r"^- (?:Never|Do not|Don't) "
        r"(?:use|invoke|call|spawn|launch|delegate to) "
        r"(?:any )?(?:subagents?|child agents?)[.!]?$", re.IGNORECASE)),
    ("CUSTOM_JA_SUBAGENT_DENIAL", re.compile(
        r"^- サブエージェント(?:の使用|の起動|の実行)を禁止(?:する|します)[。.]?$")),
)


def suspected_conflicts(source: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    known_exact = {rule["signature"] for rule in KNOWN_CONFLICTS}
    for number, line in active_markdown_lines(source):
        if line in known_exact:
            continue
        for identifier, pattern in SUSPECTED_RULES:
            if pattern.fullmatch(line) is not None:
                found.append({
                    "id": identifier,
                    "line": number,
                    "exact_instruction": line,
                    "line_sha256": sha(line.encode("utf-8")),
                    "confidence": "SUSPECTED_UNVERIFIED_SCOPE",
                    "operation": "KAGARI native Task child delegation",
                    "next_action": "MANUAL_INSTRUCTION_REVIEW_NOT_AUTOMATIC_ASK",
                })
    return found


def legacy_identity(project: Path) -> dict[str, Any]:
    directory = project / ".automation"
    if directory.exists() or directory.is_symlink():
        info = directory.lstat()
        require(stat.S_ISDIR(info.st_mode) and not directory.is_symlink(),
                "unsafe .automation directory")
    version = checked_file(directory / "VERSION", MAX_METADATA, optional=True)
    upstream = checked_file(directory / "UPSTREAM", MAX_METADATA, optional=True)
    ownership = checked_file(directory / "ownership.toml", MAX_METADATA, optional=True)
    parsed_upstream = parse_toml(upstream, "UPSTREAM")
    parsed_ownership = parse_toml(ownership, "ownership.toml")
    installed_v3 = version in (b"3", b"3\n")
    declared_templates = bool(parsed_upstream
                              and parsed_upstream.get("repository") == UPSTREAM
                              and parsed_upstream.get("component") == "components/agent-core")
    owners = parsed_ownership.get("paths", {}) if parsed_ownership else {}
    owns_agents = type(owners) is dict and owners.get("AGENTS.md") == "replace"
    classification = (
        "V3_STRUCTURALLY_IDENTIFIED" if installed_v3 and declared_templates and owns_agents
        else "PARTIAL_OR_FOREIGN_AUTOMATION"
        if any(x is not None for x in (version, upstream, ownership))
        else "NO_AUTOMATION_IDENTITY"
    )
    return {
        "classification": classification,
        "version": decode_text(version, "VERSION").strip() if version else None,
        "templates_source_declared": declared_templates,
        "agents_root_managed_declared": owns_agents,
        "source_cryptographically_authenticated": False,
        "fingerprints": {
            "VERSION": sha(version) if version is not None else None,
            "UPSTREAM": sha(upstream) if upstream is not None else None,
            "ownership.toml": sha(ownership) if ownership is not None else None,
        },
    }


def propose_diff(original: str, preimage_sha: str) -> dict[str, Any]:
    if BEGIN in original or END in original:
        return {
            "state": "EXISTING_KAGARI_ADDENDUM_REQUIRES_MANUAL_REVIEW",
            "preimage_sha256": preimage_sha,
            "apply_available": False,
        }
    suffix = "\n\n" if original and not original.endswith("\n") else "\n"
    proposed = original + suffix + ADDENDUM
    delta = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), proposed.splitlines(keepends=True),
        fromfile="a/AGENTS.md", tofile="b/AGENTS.md"))
    return {
        "state": "UNAPPROVED_EXACT_DIFF_PREVIEW",
        "preimage_sha256": preimage_sha,
        "proposed_sha256": sha(proposed.encode("utf-8")),
        "unified_diff": delta,
        "apply_available": False,
        "approval_bound_to_preimage_required": True,
        "post_apply_effective_instruction_verification_required": True,
    }


def inspect(project: Path) -> dict[str, Any]:
    root = Path(project)
    require(root.is_absolute() and root.is_dir() and not root.is_symlink()
            and root.resolve() == root,
            "Project root must be a canonical ordinary directory")
    require((root / ".git").is_dir() and not (root / ".git").is_symlink(),
            "ordinary Git Project with a real .git directory required")
    legacy = legacy_identity(root)
    raw = checked_file(root / "AGENTS.md", MAX_INSTRUCTIONS, optional=True)
    text = decode_text(raw, "AGENTS.md") if raw is not None else None
    findings = detect_known_conflicts(text) if text is not None else []
    suspected = suspected_conflicts(text) if text is not None else []
    if raw is None and legacy["classification"] == "V3_STRUCTURALLY_IDENTIFIED":
        state = "REVIEW_REQUIRED_UNKNOWN_INSTRUCTIONS"
    elif suspected:
        # A confirmed v3 clause does not cancel an unrelated ambiguous
        # prohibition. Never propose an automatic Ask while any such
        # instruction requires separate human interpretation.
        state = "REVIEW_REQUIRED_UNCONFIRMED_CONFLICT"
    elif findings:
        state = "ASK_REQUIRED_FOR_CONFLICTING_KAGARI_TASK"
    else:
        state = "NO_EXACT_KNOWN_CONFLICT"
    ask = None
    if state == "ASK_REQUIRED_FOR_CONFLICTING_KAGARI_TASK":
        assert raw is not None and text is not None
        ask = {
            "type": "AGENTS_COMPATIBILITY_REMEDIATION",
            "scope": CONSUMER,
            "status": "NOT_APPROVED",
            "project_root": str(root),
            "conflict_ids": sorted({finding["id"] for finding in findings}),
            "proposed_file_change": propose_diff(text, sha(raw)),
            "on_refusal": "COEXIST_INSTALL_ALLOWED; CONFLICTING_KAGARI_TASK_BLOCKED",
            "legacy_agentcore_v3": "UNCHANGED_FOR_NON_KAGARI_SESSIONS",
            "full_migration": "SEPARATE_OPT_IN_NOT_REQUESTED",
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "profile": "READ_ONLY_CONFLICT_PLANNER_UNQUALIFIED",
        "project_root": str(root),
        "consumer": CONSUMER,
        "state": state,
        "legacy": legacy,
        "agents": {
            "exists": raw is not None,
            "sha256": sha(raw) if raw is not None else None,
            "size": len(raw) if raw is not None else None,
        },
        "confirmed_conflicts": findings,
        "suspected_conflicts": suspected,
        "ask_candidate": ask,
        "coverage": (
            "ROOT_AGENTS_MD_EXACT_KNOWN_RULES_ONLY; UNKNOWN_TEXT_NOT_CERTIFIED;"
            " NESTED_RULES_AND_OPENCODE_EFFECTIVE_PRECEDENCE_NOT_TESTED"
        ),
        "installation_requires_agents_change": False,
        "task_compatibility_proven": False,
        "automatic_file_edit_available": False,
        "human_permission_granted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = inspect(args.project_root)
    except (CompatibilityBlocked, OSError, ValueError, TypeError) as exc:
        result = {
            "state": "BLOCKED_UNSAFE_INPUT",
            "reason": str(exc),
            "profile": "READ_ONLY_CONFLICT_PLANNER_UNQUALIFIED",
            "human_permission_granted": False,
        }
        print(json.dumps(result, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
