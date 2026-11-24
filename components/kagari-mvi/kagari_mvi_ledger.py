"""The permission ledger (#430, ADR-0010 D2): what a human allowed once is allowed again.

~/.local/state/kagari-mvi/ledger.json of kagari-runner (file 0600, directory 0700). kagari-agent
cannot read it: before each Claude run the runner copies only the grants that apply to that run
(repository, Issue, not expired, not revoked) into a snapshot file, and a PreToolUse hook
(`hook` below, run by Claude as the agent user) reads the snapshot. A grant that matches is
allowed and recorded in the audit file; a DENIED_COMMANDS command without a grant is denied; every
other operation is left to Claude's own rules. Stage 1: nothing waits for a human answer.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import fnmatch
import json
import os
import re
import secrets
import shlex
import sys
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

import kagari_mvi_shell as shell

LEDGER = "~/.local/state/kagari-mvi/ledger.json"
SCOPES = ("issue", "repo", "host")  # the default is the narrowest
KINDS = ("bash", "ssh", "edit")
HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
MAX_LISTED = 10
Grant = Dict[str, Any]


def default_path() -> str:
    return os.path.expanduser(LEDGER)


def stamp(now: datetime.datetime) -> str:
    return now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def load(path: str) -> List[Grant]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return []
    return [g for g in data.get("grants", []) if isinstance(g, dict)] if isinstance(data, dict) else []


def save(path: str, grants: List[Grant]) -> None:
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    temp = path + ".new"
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"version": 1, "grants": grants}, handle, ensure_ascii=False, indent=2)
    os.chmod(temp, 0o600)
    os.replace(temp, path)


@contextlib.contextmanager
def locked(path: str) -> Iterator[None]:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    descriptor = os.open(path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def is_active(grant: Grant, now: datetime.datetime) -> bool:
    return not grant.get("revoked") and str(grant.get("expires", "")) > stamp(now)  # ISO UTC sorts as text


def applies(grant: Grant, repo: str, issue: int) -> bool:
    scope = grant.get("scope")
    if scope == "host":
        return True
    same_repo = str(grant.get("repo", "")).lower() == repo.lower()
    return same_repo and (scope == "repo" or (scope == "issue" and grant.get("issue") == issue))


def for_run(grants: List[Grant], repo: str, issue: int, now: datetime.datetime) -> List[Grant]:
    """The grants a run may use: this is what the agent-side hook is given."""
    return [g for g in grants if is_active(g, now) and applies(g, repo, issue)]


def matches(grant: Grant, tool: str, tool_input: Dict[str, Any]) -> bool:
    kind, match = grant.get("kind"), grant.get("match")
    if kind in ("bash", "ssh") and tool == "Bash":
        argv = shell.simple_argv(str(tool_input.get("command", "")))
        if argv is None:  # compound or unparsable: never automatic, whatever the grant says
            return False
        if kind == "ssh":
            target = shell.ssh_target(argv)
            return target is not None and target[0] in (match or [])
        wanted = shell.simple_argv(str(match))
        if not wanted or shell.unreadable_ssh(argv):
            return False
        if wanted[-1] == "*":
            return len(argv) >= len(wanted) and argv[:len(wanted) - 1] == wanted[:-1]
        return argv == wanted
    if kind == "edit" and tool in ("Edit", "Write"):
        path = str(tool_input.get("file_path", ""))
        return bool(path) and ".." not in path.split("/") and fnmatch.fnmatchcase(path, str(match))
    return False


def decide(grants: List[Grant], tool: str, tool_input: Dict[str, Any],
           now: datetime.datetime) -> Tuple[Optional[str], Optional[Grant], str]:
    """("allow", grant, why) | ("deny", None, why) | (None, None, ""): the last leaves it to Claude."""
    for grant in grants:
        if is_active(grant, now) and matches(grant, tool, tool_input):
            return "allow", grant, "allowed by ledger grant %s" % grant.get("id")
    if tool == "Bash":
        found = shell.command_denied(str(tool_input.get("command", "")))
        if found:
            return "deny", None, ("`%s` is denied and no ledger grant matches (kagari-mvi ledger add)" % found)
    return None, None, ""


def hook(grants_path: str, audit_path: str, stdin: str,
         now: Optional[datetime.datetime] = None) -> Tuple[str, int]:
    """PreToolUse hook: (stdout, exit code). Any failure is exit 2, which blocks the tool call."""
    try:
        now = now or utcnow()
        event = json.loads(stdin)
        tool, tool_input = str(event.get("tool_name", "")), event.get("tool_input") or {}
        decision, grant, why = decide(load(grants_path), tool, tool_input, now)
        if decision is None:
            return "", 0
        with open(audit_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"time": stamp(now), "tool": tool, "input": tool_input,
                                     "decision": decision, "grant": grant.get("id") if grant else None},
                                    ensure_ascii=False) + "\n")
        return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                                  "permissionDecisionReason": why}}), 0
    except Exception as error:  # noqa: BLE001 - fail closed
        print("kagari ledger hook failed: %s" % error, file=sys.stderr)
        return "", 2


def hook_command(grants_path: str, audit_path: str) -> str:
    """The hook command line; `|| exit 2`: a missing interpreter must not let the call through."""
    return "%s %s hook %s %s || exit 2" % (shlex.quote(sys.executable or "python3"),
                                          shlex.quote(os.path.abspath(__file__)),
                                          shlex.quote(grants_path), shlex.quote(audit_path))


def read_audit(audit_path: str) -> List[Dict[str, Any]]:
    try:
        with open(audit_path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        with contextlib.suppress(ValueError):
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
    return records


def describe(tool: str, tool_input: Dict[str, Any]) -> str:
    if tool == "Bash":
        return str(tool_input.get("command", ""))
    return str(tool_input.get("file_path") or tool_input.get("path") or json.dumps(tool_input)[:200])


def denied_operations(audit: List[Dict[str, Any]], claude_denials: Sequence[Any]) -> List[Dict[str, Any]]:
    """Operations refused in a run, once each: from the hook's audit and Claude's own denial list."""
    found: Dict[Tuple[str, str], Dict[str, Any]] = {}
    items = [(r.get("tool"), r.get("input")) for r in audit if r.get("decision") == "deny"]
    items += [(d.get("tool_name"), d.get("tool_input")) for d in claude_denials if isinstance(d, dict)]
    for tool, tool_input in items:
        if isinstance(tool, str) and isinstance(tool_input, dict):
            found.setdefault((tool, describe(tool, tool_input)), {"tool": tool, "input": tool_input})
    return list(found.values())


def candidate(op: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(kind, match) a human could register for a refused operation; None if it can never be allowed."""
    tool, tool_input = op["tool"], op["input"]
    if tool == "Bash":
        argv = shell.simple_argv(str(tool_input.get("command", "")))
        if argv is None:
            return None
        target = shell.ssh_target(argv) if os.path.basename(argv[0]) == "ssh" else None
        if target:
            return "ssh", target[0]
        if shell.unreadable_ssh(argv):
            return None
        return "bash", " ".join(shlex.quote(word) for word in argv)
    if tool in ("Edit", "Write") and tool_input.get("file_path"):
        return "edit", str(tool_input["file_path"])
    return None


def denial_report(ops: List[Dict[str, Any]], repo: str, issue: int) -> str:
    """One comment section: what was refused and the grants that could be registered (with scopes)."""
    if not ops:
        return ""
    lines = ["", "Operations denied in this run (no matching permission ledger grant):", ""]
    for op in ops[:MAX_LISTED]:
        text = describe(op["tool"], op["input"]).replace("`", "'").replace("\n", " ")[:300]
        lines.append("- `%s`: `%s`" % (op["tool"], text))
        found = candidate(op)
        if found is None:
            lines.append("  - no grant can allow this one (compound or unparsable command, or no ledger kind for it)")
            continue
        lines.append("  - to allow it, a human runs one of (as root or kagari-runner; default scope `issue`):")
        for scope in SCOPES:
            parts = ["kagari-mvi ledger add --kind %s --match %s --scope %s" % (
                found[0], shlex.quote(found[1]), scope)]
            if scope != "host":
                parts.append("--repo %s" % shlex.quote(repo))
            if scope == "issue":
                parts.append("--issue %d" % issue)
            parts.append('--reason "<why>"')
            lines.append("    - `%s`" % " ".join(parts).replace("`", "'"))
    if len(ops) > MAX_LISTED:
        lines.append("- ... and %d more" % (len(ops) - MAX_LISTED))
    return "\n".join(lines) + "\n"


def add(path: str, kind: str, match: str, scope: str, repo: Optional[str], issue: Optional[int],
        days: int, reason: str, now: Optional[datetime.datetime] = None) -> Grant:
    now = now or utcnow()
    if kind not in KINDS or scope not in SCOPES:
        raise ValueError("kind is one of %s and scope one of %s" % (", ".join(KINDS), ", ".join(SCOPES)))
    if scope != "host" and not repo:
        raise ValueError("--scope %s needs --repo OWNER/REPO" % scope)
    if scope == "issue" and issue is None:
        raise ValueError("--scope issue needs --issue N")
    if days < 1 or not reason.strip():
        raise ValueError("--days must be 1 or more and --reason is required")
    value: Any = match
    if kind == "ssh":
        value = [h.strip() for h in match.split(",") if h.strip()]
        if not value or not all(HOST.fullmatch(h) for h in value):
            raise ValueError("--match for ssh is a comma-separated list of host names")
    elif kind == "bash" and (shell.simple_argv(match) is None or shell.unreadable_ssh(shell.simple_argv(match))):
        raise ValueError("a bash grant must be one plain command (no &&, ;, |, redirects or substitutions; "
                         "use --kind ssh for ssh, which ProxyCommand and the like never get through)")
    elif kind == "edit" and not match.startswith("/"):
        raise ValueError("an edit grant is an absolute path or glob")
    grant: Grant = {"id": secrets.token_hex(4), "kind": kind, "match": value, "scope": scope,
                    "repo": repo if scope != "host" else None, "issue": issue if scope == "issue" else None,
                    "expires": stamp(now + datetime.timedelta(days=days)), "created": stamp(now),
                    "reason": reason.strip(), "revoked": None}
    with locked(path):
        save(path, load(path) + [grant])
    return grant


def revoke(path: str, grant_id: str, now: Optional[datetime.datetime] = None) -> bool:
    now = now or utcnow()
    with locked(path):
        grants = load(path)
        hit = [g for g in grants if g.get("id") == grant_id and not g.get("revoked")]
        for grant in hit:
            grant["revoked"] = stamp(now)
        if hit:
            save(path, grants)
    return bool(hit)


def format_grant(grant: Grant, now: datetime.datetime) -> str:
    state = ("revoked" if grant.get("revoked") else "active" if is_active(grant, now) else "expired")
    where = {"issue": "%s#%s" % (grant.get("repo"), grant.get("issue")), "repo": grant.get("repo"),
             "host": "host"}.get(grant.get("scope"), "?")
    match = ",".join(grant["match"]) if isinstance(grant.get("match"), list) else grant.get("match")
    return "%s  %-7s %-4s %s  scope=%s (%s)  expires %s  [%s] %s" % (
        grant.get("id"), state, grant.get("kind"), match, grant.get("scope"), where, grant.get("expires"),
        grant.get("created"), grant.get("reason"))


def cli(args: argparse.Namespace, path: Optional[str] = None, out: Callable[[str], None] = print,
        now: Optional[datetime.datetime] = None) -> int:
    path, now = path or default_path(), now or utcnow()
    if args.ledger_command == "add":
        grant = add(path, args.kind, args.match, args.scope, args.repo, args.issue, args.days, args.reason, now)
        out("added: %s" % format_grant(grant, now))
    elif args.ledger_command == "revoke":
        out(("revoked: %s" if revoke(path, args.id, now) else "no active grant: %s") % args.id)
    else:
        grants = [g for g in load(path) if args.all or is_active(g, now)]
        for grant in grants:
            out(format_grant(grant, now))
        if not grants:
            out("(no grants)")
    return 0


def add_parsers(sub: Any) -> None:
    ledger = sub.add_parser("ledger", help="permission ledger: add | revoke | list (runs as kagari-runner)")
    commands = ledger.add_subparsers(dest="ledger_command", required=True)
    one = commands.add_parser("add", help="allow an operation again")
    one.add_argument("--kind", choices=KINDS, default="bash")
    one.add_argument("--match", required=True, help="bash: a command (`ls -l`, trailing `*` = any arguments); "
                     "ssh: comma-separated hosts; edit: absolute path glob")
    one.add_argument("--scope", choices=SCOPES, default="issue")
    one.add_argument("--repo", help="OWNER/REPO (scope issue or repo)")
    one.add_argument("--issue", type=int, help="Issue number (scope issue)")
    one.add_argument("--days", type=int, default=30, help="valid for this many days (default 30)")
    one.add_argument("--reason", required=True)
    commands.add_parser("revoke", help="stop using a grant").add_argument("id")
    commands.add_parser("list", help="show grants").add_argument("--all", action="store_true",
                                                                help="include expired and revoked")


def hook_main(argv: Sequence[str]) -> int:
    out, code = hook(argv[0], argv[1], sys.stdin.read())
    if out:
        print(out)
    return code


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "hook":
    sys.exit(hook_main(sys.argv[2:]))
