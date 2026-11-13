#!/usr/bin/env python3
"""Contract permission window for the KAGARI MVI worker (ADR-0008 W2, #418).

`decide` judges one request that Claude sends to `--permission-prompt-tool`
against the Task Contract (ported from #402). `serve` is the stdio MCP server
Claude launches for `run-once --permission contract`. In the MVI the runner owns
every git and GitHub operation, so Claude's `git` and `gh` are always denied.
Every decision is appended to a JSON-lines log; the agent user can edit that log
(known limitation, User decision 1-3a). Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Any, Dict, List, Mapping, Optional, Sequence, Tuple

CONTRACT_BLOCK = "kagari-commands"
TOOL_NAME = "decide"
SERVER_NAME = "kagari"
PROMPT_TOOL = "mcp__%s__%s" % (SERVER_NAME, TOOL_NAME)
# Strict on purpose: no pipes, lists, redirection, expansion, globbing,
# substitution, escapes or comments, so the parsed argv is what the shell runs.
SHELL_SPECIAL = frozenset(";&|<>`$(){}[]*?!#\\\n\r")
TILDE_EXPANSION = re.compile(r"(?:^|[\s=:])~")
INSTRUCTION_FILES = frozenset(("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md"))
READ_TOOLS = {"Read": "file_path", "Glob": "path", "Grep": "path"}
WRITE_TOOLS = {"Edit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}
NETWORK_TOOLS = frozenset(("WebFetch", "WebSearch"))
READ_ONLY_COMMANDS = frozenset((
    "basename", "cat", "cmp", "diff", "dirname", "echo", "file", "find", "grep", "head",
    "ls", "pwd", "realpath", "rg", "stat", "tail", "test", "true", "wc", "which",
))
# Options that run a program, write a file or follow symlinks out of the worktree.
READ_ONLY_FORBIDDEN_OPTIONS = {
    "find": ("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf",
             "-fls", "-L", "-follow"),
    "grep": ("-R", "--dereference-recursive"),
    "rg": ("--pre", "--search-zip", "-z", "-L", "--follow"),
    "ls": ("-L", "--dereference"),
    "diff": ("-r", "--recursive"),  # recursion follows symlinks to files outside the worktree
}
# cp/mv options that follow or create links (a repository symlink could point outside).
LINK_LONG_OPTIONS = ("--dereference", "--symbolic-link", "--link")
LINK_SHORT_FLAGS = frozenset("LHsl")
WORKTREE_FILE_COMMANDS = frozenset(("cp", "mkdir", "mv", "rm", "rmdir", "touch"))
GIT_DESTRUCTIVE = frozenset(("filter-branch", "filter-repo", "reflog", "gc", "prune",
                             "update-ref", "replace", "clean"))


class PolicyError(Exception):
    """The approved Issue body cannot become a permission policy."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__("%s: %s" % (code, detail) if detail else code)
        self.code = code


@dataclass(frozen=True)
class CommandRule:
    """One Contract command: exact argv, or argv prefix when the line ends with ``...``."""

    argv: Tuple[str, ...]
    prefix: bool = False

    def matches(self, argv: List[str]) -> bool:
        if self.prefix:
            return tuple(argv[:len(self.argv)]) == self.argv
        return tuple(argv) == self.argv


@dataclass(frozen=True)
class Policy:
    worktree: Path
    contract_id: str
    commands: Tuple[CommandRule, ...] = ()


@dataclass(frozen=True)
class Decision:
    behavior: str
    code: str
    message: str = ""
    updated_input: Optional[Mapping[str, Any]] = None
    block: bool = False  # a Human decision is required (ADR-0001): the Issue becomes blocked

    def response(self) -> Dict[str, Any]:
        """The ``--permission-prompt-tool`` reply, verified in ADR-0006 check (a)."""
        if self.behavior == "allow":
            return {"behavior": "allow", "updatedInput": dict(self.updated_input or {})}
        return {"behavior": "deny",
                "message": "%s: %s" % (self.code, self.message) if self.message else self.code}


def _allow(code: str, tool_input: Mapping[str, Any]) -> Decision:
    return Decision("allow", code, updated_input=dict(tool_input))


def _deny(code: str, message: str = "", block: bool = False) -> Decision:
    return Decision("deny", code, message, block=block)


def _shell_text_ok(text: str) -> bool:
    return not (SHELL_SPECIAL & set(text)) and TILDE_EXPANSION.search(text) is None


def command_rule(text: str, allow_prefix: bool = True) -> CommandRule:
    """One Contract command line; shell syntax and git/gh are refused."""
    text = text.strip()
    if not _shell_text_ok(text):
        raise PolicyError("CONTRACT_COMMAND_INVALID", text)
    try:
        argv = shlex.split(text)
    except ValueError as exc:
        raise PolicyError("CONTRACT_COMMAND_INVALID", text) from exc
    prefix = allow_prefix and bool(argv) and argv[-1] == "..."
    argv = argv[:-1] if prefix else argv
    if not argv or "=" in argv[0]:
        raise PolicyError("CONTRACT_COMMAND_INVALID", text)
    if PurePosixPath(argv[0]).name in ("git", "gh"):
        raise PolicyError("CONTRACT_COMMAND_FORBIDDEN", text)
    return CommandRule(tuple(argv), prefix)


def contract_commands(body: str, test_command: Optional[str]) -> Tuple[CommandRule, ...]:
    """The test command (exact) plus the single ``kagari-commands`` block (User decision 1-4)."""
    rules = [command_rule(test_command, allow_prefix=False)] if test_command else []
    lines = (body or "").replace("\r\n", "\n").split("\n")
    starts = [i for i, line in enumerate(lines) if re.fullmatch(r"\s*```%s\s*" % CONTRACT_BLOCK, line)]
    if len(starts) > 1:
        raise PolicyError("CONTRACT_COMMANDS_DUPLICATED")
    if not starts:
        return tuple(rules)
    for line in lines[starts[0] + 1:]:
        if re.fullmatch(r"\s*```\s*", line):
            return tuple(rules)
        if line.strip() and not line.strip().startswith("# "):
            rules.append(command_rule(line))
    raise PolicyError("CONTRACT_COMMANDS_UNCLOSED")


def _resolve(policy: Policy, raw: str) -> Path:
    path = Path(raw)
    return (path if path.is_absolute() else policy.worktree / path).resolve(strict=False)


def _inside(policy: Policy, raw: str) -> bool:
    resolved = _resolve(policy, raw)
    return resolved == policy.worktree or policy.worktree in resolved.parents


def _protected(policy: Policy, raw: str) -> bool:
    if not _inside(policy, raw):
        return True
    parts = _resolve(policy, raw).relative_to(policy.worktree).parts
    return (not parts or ".git" in parts or ".claude" in parts or parts[-1] in INSTRUCTION_FILES)


def _decide_read(policy: Policy, tool: str, tool_input: Mapping[str, Any]) -> Decision:
    raw = tool_input.get(READ_TOOLS[tool])
    if raw is not None and not isinstance(raw, str):
        return _deny("MALFORMED_REQUEST", "path is not a string")
    if raw is not None and not _inside(policy, raw):
        return _deny("PATH_OUTSIDE_WORKTREE", raw)
    pattern = tool_input.get("pattern") if tool == "Glob" else None
    if isinstance(pattern, str) and (pattern.startswith(("/", "~"))
                                     or ".." in PurePosixPath(pattern).parts):
        return _deny("PATH_OUTSIDE_WORKTREE", pattern)
    return _allow("READ_IN_WORKTREE", tool_input)


def _decide_write(policy: Policy, tool: str, tool_input: Mapping[str, Any]) -> Decision:
    raw = tool_input.get(WRITE_TOOLS[tool])
    if not isinstance(raw, str) or not raw:
        return _deny("MALFORMED_REQUEST", "path is missing")
    if not _inside(policy, raw):
        return _deny("PATH_OUTSIDE_WORKTREE", raw)
    if _protected(policy, raw):
        return _deny("PROTECTED_PATH", raw)
    return _allow("WRITE_IN_WORKTREE", tool_input)


def _operands(arg: str) -> List[str]:
    """``arg`` and any path it may carry as an option value (``--file=x``, ``-fx``)."""
    values = [arg]
    if arg.startswith("--") and "=" in arg:
        values.append(arg.split("=", 1)[1])
    elif arg.startswith("-") and not arg.startswith("--") and len(arg) > 2:
        values.append(arg[2:])
    return [value for value in values if value]


def _has_option(name: str, arg: str, option: str) -> bool:
    """``option`` given as ``arg``, also inside a short cluster (``-rN``) except for find."""
    if arg == option or arg.startswith(option + "="):
        return True
    short = len(option) == 2 and option[0] == "-" and option[1] != "-"
    return (name != "find" and short and arg.startswith("-") and not arg.startswith("--")
            and option[1] in arg[1:])


def _link_option(arg: str) -> bool:
    """A cp/mv option that follows or creates links (-L -H -s -l, also inside -rL)."""
    if arg.startswith("--"):
        return arg.split("=", 1)[0] in LINK_LONG_OPTIONS
    if not arg.startswith("-"):
        return False
    flags = re.split(r"[tTS]", arg[1:], maxsplit=1)[0]  # letters after -t/-T/-S are a value
    return bool(LINK_SHORT_FLAGS & set(flags))


def _git_decision(argv: List[str]) -> Decision:
    """The runner owns git in the MVI: deny all, and block on Human-decision operations."""
    sub = argv[1] if len(argv) > 1 else ""
    rest = argv[2:]
    if sub == "push" and any(a in ("-f", "--force", "--mirror", "--delete", "-d")
                             or a.startswith(("--force", "+", ":")) for a in rest):
        return _deny("HISTORY_DESTRUCTION_NEEDS_HUMAN", "force push or remote ref deletion", True)
    if sub in ("merge", "tag"):
        return _deny("%s_NEEDS_HUMAN" % sub.upper(), block=True)
    if (sub in GIT_DESTRUCTIVE or (sub == "reset" and "--hard" in rest)
            or (sub == "branch" and any(a in ("-D", "--delete", "-d") for a in rest))
            or (sub == "worktree" and rest[:1] in (["remove"], ["prune"]))):
        return _deny("HISTORY_DESTRUCTION_NEEDS_HUMAN", "git %s" % sub, True)
    return _deny("GIT_HOST_OWNED", "the runner commits and pushes")


def _gh_decision(argv: List[str]) -> Decision:
    sub = argv[1] if len(argv) > 1 else ""
    action = argv[2] if len(argv) > 2 else ""
    if sub == "label" or (sub in ("issue", "pr") and action == "edit" and any(
            a.startswith(("--add-label", "--remove-label")) for a in argv)):
        return _deny("APPROVAL_LABEL_NEEDS_HUMAN", block=True)
    if sub == "pr" and action in ("merge", "ready"):
        return _deny("MERGE_NEEDS_HUMAN", "gh pr %s" % action, True)
    if sub == "release":
        return _deny("RELEASE_NEEDS_HUMAN", block=True)
    return _deny("GITHUB_HOST_OWNED", "the runner reads and writes GitHub")


def _decide_bash(policy: Policy, tool_input: Mapping[str, Any]) -> Decision:
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return _deny("MALFORMED_REQUEST", "command is missing")
    try:
        argv = shlex.split(command)
    except ValueError:
        return _deny("SHELL_SYNTAX_NOT_ALLOWED", "unbalanced quotes")
    # git/gh first, so a Human-decision operation blocks even when a path argument leaves the worktree.
    name = PurePosixPath(argv[0]).name if argv else ""
    if name in ("git", "gh") and _shell_text_ok(command):
        return _git_decision(argv) if name == "git" else _gh_decision(argv)
    if not _shell_text_ok(command):
        return _deny("SHELL_SYNTAX_NOT_ALLOWED", "one plain command without shell operators")
    if "=" in argv[0]:
        return _deny("SHELL_SYNTAX_NOT_ALLOWED", "no environment assignments")
    # Every word is treated as a possible path, so symlinks and ``..`` that leave
    # the worktree are caught without knowing each command's syntax.
    for value in (value for arg in argv for value in _operands(arg)):
        if not _inside(policy, value):
            return _deny("PATH_OUTSIDE_WORKTREE", value)
    if name in READ_ONLY_COMMANDS:
        forbidden = READ_ONLY_FORBIDDEN_OPTIONS.get(name, ())
        if any(_has_option(name, arg, option) for arg in argv[1:] for option in forbidden):
            return _deny("COMMAND_OPTION_NOT_ALLOWED", name)
        return _allow("READ_ONLY_COMMAND", tool_input)
    if name in WORKTREE_FILE_COMMANDS:
        if name in ("cp", "mv") and any(_link_option(a) for a in argv[1:]):
            return _deny("COMMAND_OPTION_NOT_ALLOWED", name)
        # Every operand and option value (-t DIR, --target-directory=DIR) is a possible target.
        targets = [v for a in argv[1:] for v in _operands(a) if not v.startswith("-")]
        if not targets or any(_protected(policy, value) for value in targets):
            return _deny("PROTECTED_PATH", name)
        return _allow("WORKTREE_FILE_COMMAND", tool_input)
    if any(rule.matches(argv) for rule in policy.commands):
        return _allow("CONTRACT_COMMAND", tool_input)
    return _deny("OUT_OF_CONTRACT", name)


def decide(policy: Policy, request: Any) -> Decision:
    """Decide one ``--permission-prompt-tool`` request; anything unrecognized is denied."""
    if not isinstance(request, Mapping):
        return _deny("MALFORMED_REQUEST")
    tool, tool_input = request.get("tool_name"), request.get("input")
    if not isinstance(tool, str) or not isinstance(tool_input, Mapping):
        return _deny("MALFORMED_REQUEST")
    if tool in READ_TOOLS:
        return _decide_read(policy, tool, tool_input)
    if tool in WRITE_TOOLS:
        return _decide_write(policy, tool, tool_input)
    if tool == "Bash":
        return _decide_bash(policy, tool_input)
    if tool in NETWORK_TOOLS:
        return _deny("NETWORK_NOT_ALLOWED", tool)
    return _deny("TOOL_NOT_ALLOWED", tool)


# --- contract file and decision log (written by the runner / the server) --------
def write_contract(path: str, contract_id: str, rules: Sequence[CommandRule]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"contract_id": contract_id,
                   "commands": [{"argv": list(r.argv), "prefix": r.prefix} for r in rules]}, handle)


def load_policy(worktree: str, contract_path: str) -> Policy:
    with open(contract_path, encoding="utf-8") as handle:
        data = json.load(handle)
    rules = tuple(CommandRule(tuple(c["argv"]), bool(c["prefix"])) for c in data["commands"])
    return Policy(Path(worktree).resolve(), str(data["contract_id"]), rules)


def read_log(path: str) -> List[Dict[str, Any]]:
    """Decisions the server recorded; unreadable lines are skipped (the agent can edit the log)."""
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


# --- stdio MCP server (newline-delimited JSON-RPC 2.0) ----------------------------
TOOL_SCHEMA = {"type": "object", "properties": {"tool_name": {"type": "string"},
                                                "input": {"type": "object"},
                                                "tool_use_id": {"type": "string"}},
               "required": ["tool_name", "input"]}


def handle(policy: Policy, message: Any, log: Optional[IO[str]]) -> Optional[Dict[str, Any]]:
    """One JSON-RPC message -> its response (None for notifications)."""
    if not isinstance(message, dict) or "method" not in message:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    method, mid = message.get("method"), message.get("id")
    if "id" not in message:
        return None  # notification, e.g. notifications/initialized
    if method == "initialize":
        version = (message.get("params") or {}).get("protocolVersion", "2024-11-05")
        result: Dict[str, Any] = {"protocolVersion": version, "capabilities": {"tools": {}},
                                  "serverInfo": {"name": SERVER_NAME, "version": "1"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": TOOL_NAME, "inputSchema": TOOL_SCHEMA,
                             "description": "Decide a permission request against the Contract."}]}
    elif method == "tools/call":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        request = params.get("arguments") if params.get("name") == TOOL_NAME else None
        try:
            decision = decide(policy, request) if request is not None else _deny("UNKNOWN_TOOL")
        except Exception as error:  # noqa: BLE001 - fail closed on any input (e.g. NUL in a path)
            decision = _deny("MALFORMED_REQUEST", type(error).__name__)
        if log is not None:
            log.write(json.dumps({"contract_id": policy.contract_id, "request": request,
                                  "behavior": decision.behavior, "code": decision.code,
                                  "message": decision.message, "block": decision.block},
                                 ensure_ascii=False) + "\n")
            log.flush()
        result = {"content": [{"type": "text", "text": json.dumps(decision.response())}]}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown method"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(policy: Policy, stdin: IO[str], stdout: IO[str], log: Optional[IO[str]]) -> None:
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            response: Optional[Dict[str, Any]] = {
                "jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            response = handle(policy, message, log)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="kagari_mvi_policy", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("serve", help="stdio MCP server for --permission-prompt-tool")
    run.add_argument("--worktree", required=True)
    run.add_argument("--contract", required=True)
    run.add_argument("--log", required=True)
    args = parser.parse_args(argv)
    policy = load_policy(args.worktree, args.contract)
    with open(args.log, "a", encoding="utf-8") as log:
        serve(policy, sys.stdin, sys.stdout, log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
