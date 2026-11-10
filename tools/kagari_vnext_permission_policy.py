"""#394 W2-a: decide one Claude permission request against the Task Contract.

Pure decision logic for ADR-0006 D4. The stdio MCP server, the decision log,
the Git configuration check and the launcher wiring are W2-b. Nothing here
starts a process; paths are only resolved, never read or written.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import shlex
from typing import Any, Mapping

CONTRACT_BLOCK = "kagari-commands"
# Strict on purpose: no pipes, lists, redirection, expansion, globbing,
# substitution, escapes or comments, so the parsed argv is what the shell runs.
SHELL_SPECIAL = frozenset(";&|<>`$(){}[]*?!#\\\n\r")
# Tilde expands only at the start of a word or after ``=``/``:``, so ``HEAD~1`` stays usable.
TILDE_EXPANSION = re.compile(r"(?:^|[\s=:])~")
INSTRUCTION_FILES = frozenset(("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md"))
READ_TOOLS = {"Read": "file_path", "Glob": "path", "Grep": "path"}
WRITE_TOOLS = {"Edit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}
NETWORK_TOOLS = frozenset(("WebFetch", "WebSearch"))
READ_ONLY_COMMANDS = frozenset((
    "basename", "cat", "cmp", "diff", "dirname", "echo", "file", "find", "grep", "head",
    "ls", "pwd", "realpath", "rg", "stat", "tail", "test", "true", "wc", "which",
))
# Options that run a program, write a file or follow symlinks out of the
# worktree from an otherwise read-only command.
READ_ONLY_FORBIDDEN_OPTIONS = {
    "find": ("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls",
             "-L", "-follow"),
    "grep": ("-R", "--dereference-recursive"),
    "rg": ("--pre", "--search-zip", "-z", "-L", "--follow"),
    "ls": ("-L", "--dereference"),
}
WORKTREE_FILE_COMMANDS = frozenset(("cp", "mkdir", "mv", "rm", "rmdir", "touch"))
GIT_READ = frozenset(("status", "diff", "log", "show", "rev-parse", "ls-files"))
GIT_DIFF_LIKE = frozenset(("diff", "log", "show"))
# Git options that run helpers, write files or point Git somewhere else.
GIT_FORBIDDEN_OPTIONS = ("--output", "--ext-diff", "--textconv", "--exec", "--git-dir",
                         "--work-tree", "--upload-pack", "--receive-pack", "--paginate", "-O")
GIT_HARDENED_PREFIX = ("git", "-c", "core.fsmonitor=false", "-c", "core.pager=cat", "--no-pager")
GIT_DESTRUCTIVE = frozenset(("filter-branch", "filter-repo", "reflog", "gc", "prune", "update-ref", "replace"))
GH_READ_ACTIONS = frozenset(("view", "list", "diff", "checks", "status"))
GH_API_WRITE_FLAGS = ("-f", "-F", "--field", "--raw-field", "--input")


class PolicyError(Exception):
    """The approved Issue body cannot become a permission policy."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CommandRule:
    """One Contract command: exact argv, or argv prefix when the line ends with ``...``."""

    argv: tuple[str, ...]
    prefix: bool = False

    def matches(self, argv: list[str]) -> bool:
        if self.prefix:
            return len(argv) >= len(self.argv) and tuple(argv[:len(self.argv)]) == self.argv
        return tuple(argv) == self.argv


@dataclass(frozen=True)
class Policy:
    worktree: Path
    contract_id: str
    commands: tuple[CommandRule, ...] = ()
    # Set by the host only after it found no Git helper (filter, external
    # diff, fsmonitor) configured for this worktree.
    git_read: bool = False


@dataclass(frozen=True)
class Decision:
    behavior: str
    code: str
    message: str = ""
    updated_input: Mapping[str, Any] | None = None
    # A Human decision is required (ADR-0001): Main must set the Task BLOCKED.
    block: bool = False

    def response(self) -> dict[str, Any]:
        """The ``--permission-prompt-tool`` reply, verified in ADR-0006 check (a)."""
        if self.behavior == "allow":
            return {"behavior": "allow", "updatedInput": dict(self.updated_input or {})}
        return {"behavior": "deny", "message": f"{self.code}: {self.message}" if self.message else self.code}


def _allow(code: str, tool_input: Mapping[str, Any]) -> Decision:
    return Decision("allow", code, updated_input=dict(tool_input))


def _deny(code: str, message: str = "", block: bool = False) -> Decision:
    return Decision("deny", code, message, block=block)


def _shell_text_ok(text: str) -> bool:
    return not (SHELL_SPECIAL & set(text)) and TILDE_EXPANSION.search(text) is None


def parse_contract_commands(body: str) -> tuple[CommandRule, ...]:
    """Read the single fenced ``kagari-commands`` block of the approved Issue body."""
    lines = body.replace("\r\n", "\n").split("\n")
    starts = [index for index, line in enumerate(lines)
              if re.fullmatch(rf"\s*```{CONTRACT_BLOCK}\s*", line)]
    if not starts:
        return ()
    if len(starts) > 1:
        raise PolicyError("CONTRACT_COMMANDS_DUPLICATED")
    rules = []
    for line in lines[starts[0] + 1:]:
        if re.fullmatch(r"\s*```\s*", line):
            return tuple(rules)
        text = line.strip()
        if not text or text.startswith("# "):
            continue
        if not _shell_text_ok(text):
            raise PolicyError("CONTRACT_COMMAND_INVALID", text)
        try:
            argv = shlex.split(text)
        except ValueError as exc:
            raise PolicyError("CONTRACT_COMMAND_INVALID", text) from exc
        prefix = argv[-1] == "..."
        argv = argv[:-1] if prefix else argv
        if not argv or "=" in argv[0]:
            raise PolicyError("CONTRACT_COMMAND_INVALID", text)
        if PurePosixPath(argv[0]).name in ("git", "gh"):
            raise PolicyError("CONTRACT_COMMAND_FORBIDDEN", text)
        rules.append(CommandRule(tuple(argv), prefix))
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
    return (not parts or ".git" in parts or parts[0] == ".claude"
            or (len(parts) == 1 and parts[0] in INSTRUCTION_FILES))


def _decide_read(policy: Policy, tool: str, tool_input: Mapping[str, Any]) -> Decision:
    raw = tool_input.get(READ_TOOLS[tool])
    if raw is not None and not isinstance(raw, str):
        return _deny("MALFORMED_REQUEST", "path is not a string")
    if raw is not None and not _inside(policy, raw):
        return _deny("PATH_OUTSIDE_WORKTREE", raw)
    pattern = tool_input.get("pattern") if tool == "Glob" else None
    if isinstance(pattern, str) and (pattern.startswith(("/", "~")) or ".." in PurePosixPath(pattern).parts):
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


def _operands(arg: str) -> list[str]:
    """``arg`` and any path it may carry as an option value (``--file=x``, ``-fx``)."""
    values = [arg]
    if arg.startswith("--") and "=" in arg:
        values.append(arg.split("=", 1)[1])
    elif arg.startswith("-") and not arg.startswith("--") and len(arg) > 2:
        values.append(arg[2:])
    return [value for value in values if value]


def _git_classify(argv: list[str]) -> Decision | None:
    if len(argv) < 2 or argv[1].startswith("-"):
        return _deny("GIT_COMMAND_NOT_ALLOWED", "Git global options are not allowed")
    sub, rest = argv[1], argv[2:]
    if sub == "push":
        force = any(arg in ("-f", "--force", "--mirror", "--delete", "-d") or arg.startswith("--force")
                    or arg.startswith(("+", ":")) for arg in rest)
        if force:
            return _deny("HISTORY_DESTRUCTION_NEEDS_HUMAN", "force push or remote ref deletion", block=True)
        return _deny("PUBLICATION_HOST_OWNED", "the host publishes the Task branch")
    if sub == "merge":
        return _deny("MERGE_NEEDS_HUMAN", block=True)
    if sub == "tag":
        return _deny("TAG_NEEDS_HUMAN", block=True)
    if (sub in GIT_DESTRUCTIVE or (sub == "reset" and "--hard" in rest) or sub == "clean"
            or (sub == "branch" and any(arg in ("-D", "--delete", "-d") for arg in rest))
            or (sub == "worktree" and rest[:1] in (["remove"], ["prune"]))):
        return _deny("HISTORY_DESTRUCTION_NEEDS_HUMAN", f"git {sub}", block=True)
    return None


def _gh_classify(argv: list[str]) -> Decision:
    sub = argv[1] if len(argv) > 1 else ""
    action = argv[2] if len(argv) > 2 else ""
    if sub == "label" or (sub in ("issue", "pr") and action == "edit"
                          and any(arg.startswith(("--add-label", "--remove-label")) for arg in argv)):
        return _deny("APPROVAL_LABEL_NEEDS_HUMAN", block=True)
    if sub == "pr" and action in ("merge", "ready"):
        return _deny("MERGE_NEEDS_HUMAN", f"gh pr {action}", block=True)
    if sub == "release":
        return _deny("RELEASE_NEEDS_HUMAN", block=True)
    if sub in ("issue", "pr") and action in GH_READ_ACTIONS:
        return _deny("GITHUB_READ_NOT_ALLOWED", "the Contract snapshot is the Task's source")
    if sub == "api":
        method = next((argv[index + 1] for index, arg in enumerate(argv[:-1]) if arg in ("-X", "--method")),
                      next((arg.split("=", 1)[1] for arg in argv if arg.startswith("--method=")), "GET"))
        if method.upper() == "GET" and not any(arg.startswith(GH_API_WRITE_FLAGS) for arg in argv[2:]):
            return _deny("GITHUB_READ_NOT_ALLOWED", "the Contract snapshot is the Task's source")
    return _deny("GITHUB_WRITE_HOST_OWNED", "the host writes to GitHub")


def _decide_git_read(policy: Policy, argv: list[str], tool_input: Mapping[str, Any]) -> Decision:
    if not policy.git_read:
        return _deny("GIT_READ_UNAVAILABLE", "this worktree configures a Git helper (#364)")
    sub, rest = argv[1], argv[2:]
    if any(arg.startswith(GIT_FORBIDDEN_OPTIONS) for arg in rest):
        return _deny("GIT_OPTION_NOT_ALLOWED")
    extra = ["--no-ext-diff", "--no-textconv"] if sub in GIT_DIFF_LIKE else []
    command = shlex.join([*GIT_HARDENED_PREFIX, sub, *extra, *rest])
    return Decision("allow", "GIT_READ_HARDENED", updated_input={**tool_input, "command": command})


def _decide_bash(policy: Policy, tool_input: Mapping[str, Any]) -> Decision:
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return _deny("MALFORMED_REQUEST", "command is missing")
    if not _shell_text_ok(command):
        return _deny("SHELL_SYNTAX_NOT_ALLOWED", "one plain command without shell operators")
    try:
        argv = shlex.split(command)
    except ValueError:
        return _deny("SHELL_SYNTAX_NOT_ALLOWED", "unbalanced quotes")
    if "=" in argv[0]:
        return _deny("SHELL_SYNTAX_NOT_ALLOWED", "no environment assignments")
    # Every word is treated as a possible path, so symlinks and ``..`` that
    # leave the worktree are caught without knowing each command's syntax.
    for value in (value for arg in argv for value in _operands(arg)):
        if not _inside(policy, value):
            return _deny("PATH_OUTSIDE_WORKTREE", value)
    name = argv[0]
    if name == "git":
        classified = _git_classify(argv)
        if classified is not None:
            return classified
        if argv[1] in GIT_READ:
            return _decide_git_read(policy, argv, tool_input)
        return _deny("GIT_COMMAND_NOT_ALLOWED", f"git {argv[1]}")
    if name == "gh":
        return _gh_classify(argv)
    if name in READ_ONLY_COMMANDS:
        forbidden = READ_ONLY_FORBIDDEN_OPTIONS.get(name, ())
        if any(arg == option or arg.startswith(option + "=") for arg in argv[1:] for option in forbidden):
            return _deny("COMMAND_OPTION_NOT_ALLOWED", name)
        return _allow("READ_ONLY_COMMAND", tool_input)
    if name in WORKTREE_FILE_COMMANDS:
        operands = [arg for arg in argv[1:] if not arg.startswith("-")]
        if not operands or any(_protected(policy, arg) for arg in operands):
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
