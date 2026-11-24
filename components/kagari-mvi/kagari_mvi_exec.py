"""Process side of the KAGARI MVI worker (ADR-0007 I-2): agent, tests, commit.

Runs local processes only and knows nothing about GitHub. Claude and the test
command run as the agent user through sudo; git commit runs as the runner user
with an explicit git dir and hooks disabled (D3).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import kagari_mvi_ledger as ledger
import kagari_mvi_tsumgi as tsumgi
from kagari_mvi_shell import DENIED_COMMANDS
from kagari_mvi_tsumgi import TSUMGI_MODEL, TSUMGI_URL  # noqa: F401 - used by callers and tests
# Moved out in #490; re-exported so that kagari_mvi, the tests and the recipes keep their names.
from kagari_mvi_issue import (ALLOWED_HEADING, CRITERIA_HEADING, PLACEMENT_HEADINGS,  # noqa: F401
                              TEST_HEADING, acceptance_criteria, allowed_protected,
                              delegation, missing_placement, section_items, section_text,
                              test_command)
from kagari_mvi_parse import (AUTH_PATTERN, PURPOSE_BYTES, PURPOSE_LINES, SENTENCE_END,  # noqa: F401
                              TAIL_CHARS, TAIL_LINES, TESTS_LINE, VERDICT_LINE, agent_outcome,
                              delegation_verdict, review_verdicts, tail, test_purposes, tests_verdict)
from kagari_mvi_report import report, review_report  # noqa: F401

TSUMGI_MAX_BYTES = tsumgi.max_input_bytes()  # a larger review input is not sent (#434)

GIT_IDENTITY = ["-c", "user.name=kagari-mvi", "-c", "user.email=kagari-mvi@localhost"]
# The worktree is writable by the agent: never let it choose code that git runs here.
SAFE_GIT = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"] + GIT_IDENTITY
ARTIFACTS = [":(exclude,glob)**/%s" % pattern for pattern in (  # build outputs, caches: never committed
    "__pycache__/**", "*.pyc", ".pytest_cache/**", "node_modules/**", ".mypy_cache/**",
    ".npm-cache/**", ".npm/**", ".cache/**", ".yarn/cache/**", ".pnpm-store/**")]
# Only these built-in tools exist in the session (no Cron, RemoteTrigger, Skill, ...).
# --restricted also ignores user/project/local settings and confines file tools to the cwd.
AGENT_TOOLS = "Bash,Read,Edit,Write,Glob,Grep"
# Bash is allowed broadly (it runs as the agent user, without GitHub credentials, D3); the
# DENIED_COMMANDS (kagari_mvi_shell.py) are in `permissions.deny`, and the ledger hook denies them too: no
# ledger grant allows them until a real Claude Code shows how a hook allow and a deny rule rank (#527).
# Changes to these need a human decision (ADR-0009 D1): instruction files and Claude
# settings at any depth, and CI workflows. The runner checks the commit before pushing.
INSTRUCTION_FILES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")
PROTECTED_GLOBS = tuple("**/" + name for name in INSTRUCTION_FILES) + (
    "**/.claude/**", ".github/workflows/**")
REVIEW_TOOLS = "Read,Grep,Glob"  # no Bash, no editing, no Web (I-3)
MAX_DIFF_CHARS = 200_000
AGENT_TIMEOUT = 3600
TEST_TIMEOUT = 1800
AGENT_SCRIPT = 'cd "$1" && umask 002 && shift && exec "$@"'
# Tests: no login shell (agent-written ~/.profile is ignored) and a throwaway HOME.
TEST_SCRIPT = ('cd "$1" && umask 002 && HOME=$(mktemp -d) && export HOME && '
               'exec timeout -k 10 "$3" sh -c "$2" 2>&1')  # $2 command, $3 seconds
AGENT_RULES = """You implement exactly one GitHub Issue for the KAGARI MVI worker.
- Implement only what the Issue asks. Edit files in the current directory only.
- You may run shell commands (build, lint, tests). Do not commit: the host commits
  and opens the pull request for you.
- Network and credential commands (curl, wget, gh, git push, ssh, sudo, ...) are denied by the permission
  rules, also behind env, bash -c or $(...). A permission ledger grant cannot allow them.
- Do not change CLAUDE.md, CLAUDE.local.md, AGENTS.md, `.claude/` or `.github/workflows/` unless the Issue
  lists the path under `### 変更する保護ファイル`. Other changes there stop the run before push.
- The Issue's test command, which the host re-runs to decide the result: {test}
- Just before the status line, declare what each test is for (the host posts it as is;
  it does not decide the result):
  tests:
  - <test name>: <what it is meant to guarantee> (AC<n>)
  untested: <what the tests do not check, or none>
  A criterion no test can check (e.g. which files changed) goes under untested as
  `AC<n>: <why no test can check it>` (one per line as a list if several); the reviewer
  checks it in the diff. A criterion a test could check needs a test.
- End your last message with one line: `status: COMPLETED` if the Issue is done and the
  test command passes, otherwise `status: BLOCKED: <reason>`.
"""


class CommandError(Exception):
    """A gh/git subprocess exited non-zero."""

    def __init__(self, args: Sequence[str], code: int, output: str) -> None:
        super().__init__("%s exited %d" % (" ".join(args[:3]), code))
        self.args_list = list(args)
        self.code = code
        self.output = output


Runner = Callable[..., str]


def subprocess_runner(args: Sequence[str], cwd: Optional[str] = None,
                      input: Optional[str] = None, timeout: Optional[float] = None) -> str:
    """Run a command and return stdout; raise CommandError with stdout+stderr."""
    try:
        proc = subprocess.run(list(args), cwd=cwd, input=input, capture_output=True,
                              text=True, env=_command_env(), timeout=timeout, start_new_session=True,
                              stdin=subprocess.DEVNULL if input is None else None)
    except FileNotFoundError:
        raise CommandError(args, 127, "command not found: %s (install it first)" % args[0])
    except subprocess.TimeoutExpired as error:
        partial = "".join(part.decode(errors="replace") if isinstance(part, bytes) else part
                          for part in (error.stdout, error.stderr) if part)
        raise CommandError(args, 124, partial + "\ntimed out after %ss" % timeout)
    if proc.returncode != 0:
        raise CommandError(args, proc.returncode, (proc.stdout or "") + (proc.stderr or ""))
    return proc.stdout


def _command_env() -> Dict[str, str]:
    env = dict(os.environ)
    env["GH_PROMPT_DISABLED"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["NO_COLOR"] = "1"
    return env


def protected_paths(paths: Sequence[str]) -> List[str]:
    """Changed paths that a human must review before anything is pushed."""
    return [path for path in paths
            if path.rsplit("/", 1)[-1] in INSTRUCTION_FILES or ".claude" in path.split("/")
            or path.startswith(".github/workflows/")]




class Blocked(Exception):
    """Stop processing and block the Issue with this comment."""


class UsageLimit(Exception):
    """Claude's usage limit was hit: nothing failed, retry once it resets (#481)."""

    def __init__(self, resets: str) -> None:
        super().__init__(resets)
        self.resets = resets


class AgentSteps:
    """Agent-side steps; the host class provides run, slug, agent_user, claude, agent_config_dir."""

    def as_agent(self, cwd: str, argv: List[str], login: bool = True) -> List[str]:
        """Run argv as the agent user; sudo resets the environment."""
        env = ["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1",
               "GIT_TERMINAL_PROMPT=0"]
        if self.agent_config_dir:
            env.append("CLAUDE_CONFIG_DIR=%s" % self.agent_config_dir)
        shell = ["sh", "-lc", AGENT_SCRIPT] if login else ["sh", "-c", TEST_SCRIPT]
        return ["sudo", "-n", "-u", self.agent_user, "-H", "--", "env"] + env + shell + ["sh", cwd] + argv

    def run_agent(self, workdir: str, worktree: str, issue: Dict[str, Any],
                  test: str) -> Dict[str, Any]:
        number = issue["number"]
        allowed = allowed_protected(issue.get("body"))
        if os.path.lexists(os.path.join(worktree, ".claude", "settings.local.json")):
            raise Blocked("The repository commits `.claude/settings.local.json`; it would widen "
                          "Claude's permissions (hooks, allow rules, plugins). Remove it to proceed.")
        settings = {
            "permissions": {"allow": ["Bash", "Read", "Glob", "Grep", "Edit(/%s/**)" % worktree,
                                      "Write(/%s/**)" % worktree],
                            "deny": ["Agent", "WebFetch", "WebSearch", "NotebookEdit"] + [
                                rule for name in DENIED_COMMANDS
                                for rule in ("Bash(%s)" % name, "Bash(%s *)" % name)] + [
                                "%s(/%s/%s)" % (tool, worktree, glob) for tool in ("Edit", "Write")
                                for glob in PROTECTED_GLOBS if not allowed]},
            "env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1"},
        }
        grants, audit = worktree + ".grants.json", worktree + ".audit.jsonl"
        self.write_grants(grants, audit, number)
        settings["hooks"] = {"PreToolUse": [{"matcher": "Bash|Edit|Write", "hooks": [
            {"type": "command", "command": ledger.hook_command(grants, audit)}]}]}
        prompt = "Issue #%d: %s\n\n%s\n" % (number, issue["title"], issue.get("body") or "")
        try:
            return self.launch_claude(workdir, worktree, "issue-%d-claude" % number, AGENT_TOOLS,
                                      settings, AGENT_RULES.format(test=test), prompt, audit=audit)
        finally:
            for path in (grants, audit):
                if os.path.lexists(path):
                    os.remove(path)

    def write_grants(self, grants: str, audit: str, number: int) -> None:
        """Snapshot of the ledger grants for this run (the agent cannot read the ledger itself)."""
        self.denied = []
        mine = ledger.for_run(ledger.load(self.ledger_path or ledger.default_path()), self.repo, number,
                              ledger.utcnow())
        # Never follow a link left in the shared worktree directory: the old file or link is removed first.
        ledger.create_private(grants, 0o644, json.dumps({"grants": mine}))
        ledger.create_private(audit, 0o660)  # the hook runs as the agent user and appends here

    def run_review(self, workdir: str, review_dir: str, number: int, criteria: List[str],
                   diff: str, test_report: str, declared: Optional[Dict[str, List[str]]] = None
                   ) -> Dict[str, Any]:
        settings = {"permissions": {"allow": ["Read", "Glob", "Grep"],
                                    "deny": ["Bash", "Edit", "Write", "NotebookEdit", "Agent",
                                             "WebFetch", "WebSearch"]},
                    "env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1"}}
        if len(diff) > MAX_DIFF_CHARS and self.reviewer == "claude":
            diff = diff[:MAX_DIFF_CHARS] + "\n[diff truncated; read the files for the rest]\n"
        prompt = ("Acceptance criteria:\n%s\n\nRunner test result:\n%s\n\nPR diff:\n%s\n" % (
            "\n".join("AC%d: %s" % (i, c) for i, c in enumerate(criteria, start=1)),
            test_report, diff))
        if declared is not None:
            prompt = ("Decided (fixed by the human; the PR must not change them):\n%s\n\nDelegated "
                      "decisions (the only kinds the agent may decide):\n%s\n\n" % (
                          "\n".join("- " + d for d in declared["decided"]) or "(none)",
                          "\n".join("- " + d for d in declared["delegated"]) or "(none)")) + prompt
        if self.reviewer == "tsumgi":
            outcome = self.run_tsumgi_review(workdir, number, criteria, prompt)
            outcome["delegation"] = delegation_verdict(outcome["text"]) if declared is not None else None
            return outcome
        outcome = self.launch_claude(workdir, review_dir, "issue-%d-review" % number, REVIEW_TOOLS,
                                     settings, REVIEW_RULES.format(access="The current directory is "
                                     "a checkout of the PR head; you may only read it."), prompt)
        outcome["verdicts"] = review_verdicts(outcome["text"], len(criteria))
        outcome["tests"] = tests_verdict(outcome["text"])
        outcome["delegation"] = delegation_verdict(outcome["text"]) if declared is not None else None
        outcome["reviewer"] = ("new `claude -p` session, tools %s only; same model family as the "
                               "implementer" % REVIEW_TOOLS)
        return outcome

    def run_tsumgi_review(self, workdir: str, number: int, criteria: List[str],
                          prompt: str) -> Dict[str, Any]:
        """One TSUMGI request, a model family other than the implementer's (ADR-0007 D6)."""
        reply = tsumgi.chat(self.run, REVIEW_RULES.format(access="You cannot read files."), prompt,
                            "review issue-%d" % number,
                            os.path.join(workdir, "logs", self.slug, "issue-%d-tsumgi.jsonl" % number))
        tokens = ("tokens in %s / out %s" % (reply["input_tokens"], reply["output_tokens"])
                  if reply["sent"] else "not sent (estimated %s tokens)" % reply["estimated_input_tokens"])
        reviewer = "TSUMGI `%s`, diff and test result only; %s" % (TSUMGI_MODEL, tokens)
        return {"errored": reply["error"] is not None, "denials": 0, "text": reply["text"],
                "verdicts": review_verdicts(reply["text"], len(criteria)),
                "tests": tests_verdict(reply["text"]),
                "reviewer": reviewer + ("; " + reply["error"] if reply["error"] else "")}

    def launch_claude(self, workdir: str, cwd: str, log_name: str, tools: str,
                      settings: Dict[str, Any], rules: str, prompt: str,
                      audit: Optional[str] = None) -> Dict[str, Any]:
        argv = [self.claude, "-p", "--output-format", "stream-json", "--verbose",
                "--no-session-persistence", "--tools", tools, "--restricted",
                "--permission-prompts", "none",
                "--settings=" + json.dumps(settings), "--strict-mcp-config",
                "--mcp-config=" + json.dumps({"mcpServers": {}}),
                "--append-system-prompt", rules]
        failed = False
        try:
            output = self.run(self.as_agent(cwd, bounded(AGENT_TIMEOUT) + argv), input=prompt,
                              timeout=AGENT_TIMEOUT + 60)
        except CommandError as error:
            output, failed = error.output, True
        logs = os.path.join(workdir, "logs", self.slug)
        os.makedirs(logs, mode=0o700, exist_ok=True)
        with open(os.path.join(logs, log_name + ".jsonl"), "w", encoding="utf-8") as handle:
            handle.write(output)
        outcome = agent_outcome(output, failed)
        if audit:
            records = ledger.read_audit(audit)
            self.denied = ledger.denied_operations(records, outcome["denied"])
            outcome["ledger_allowed"] = [r for r in records if r.get("decision") == "allow"]
            with open(os.path.join(logs, log_name + "-ledger.jsonl"), "w", encoding="utf-8") as handle:
                handle.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
        if outcome["limited"]:  # before auth: a limit is not a login problem
            raise UsageLimit(outcome["resets"])
        if outcome["auth"]:
            raise Blocked("Claude Code failed to authenticate: %s needs to log in again "
                          "(kagari-agent の再ログインが必要). Not retried.\n\n```\n%s\n```"
                          % (self.agent_user, outcome["detail"].replace("```", "'''")))
        if failed:
            raise CommandError(argv, 1, output)
        return outcome

    def commit_changes(self, clone: str, worktree: str, number: int,
                       allowed: Sequence[str] = ()) -> Tuple[Optional[str], List[str]]:
        """(sha, protected paths the Issue allowed); blocks on other protected paths."""
        # Explicit --git-dir: the `.git` file in the worktree is agent-writable.
        git = ["git", "--git-dir", os.path.join(clone, ".git", "worktrees", "issue-%d" % number),
               "--work-tree", worktree] + SAFE_GIT
        self.run(git + ["add", "--all", "--", "."] + ARTIFACTS, cwd=worktree)
        names = self.run(git + ["diff", "--cached", "--name-only", "--no-renames", "-z"],
                         cwd=worktree).split("\0")
        if not any(name.strip() for name in names):
            return None, []
        self.run(git + ["commit", "--no-verify", "-m", "kagari-mvi: implement #%d" % number],
                 cwd=worktree)
        sha = self.run(git + ["rev-parse", "HEAD"], cwd=worktree).strip()
        protected = [path for path in protected_paths(names) if not any(  # a trailing / covers a directory
            path == item or (item.endswith("/") and path.startswith(item)) for item in allowed)]
        if protected:
            raise Blocked("The changes touch files that need a human decision, so nothing was "
                          "pushed: %s. Commit %s is kept in `%s` for review." % (
                              ", ".join("`%s`" % path for path in protected), sha, worktree))
        return sha, protected_paths(names)

    def kill_agent_processes(self) -> None:
        # Not under timeout/as_agent: kill(-1) spares only the sending shell, not its parents.
        self.run(["sudo", "-n", "-u", self.agent_user, "-H", "--", "sh", "-c", "kill -KILL -1; true"])

    def remove_leftover(self, path: str) -> None:
        """Delete an earlier attempt's directory (git may have half-removed it already)."""
        if os.path.exists(path):  # npm and others ignore umask 002: reopen the agent's dirs first
            self.run(["sudo", "-n", "-u", self.agent_user, "-H", "--", "find", path, "-user", self.agent_user,
                      "-type", "d", "-exec", "chmod", "u+rwx,g+rwx", "{}", ";"])  # ";": before find reads it
            shutil.rmtree(path)

    def run_test(self, clone: str, worktree: str, number: int, test: str) -> Tuple[int, str]:
        """Re-run the test ourselves on a clean checkout of the commit (as the agent user)."""
        self.kill_agent_processes()
        verify = worktree + ".verify"  # no leftover processes may write here now
        self.remove_leftover(verify)
        os.makedirs(verify)
        self.run(["git", "--git-dir", os.path.join(clone, ".git", "worktrees", "issue-%d" % number),
                  "--work-tree", verify] + SAFE_GIT + ["checkout", "-f", "HEAD", "--", "."])
        try:
            return 0, self.run(self.as_agent(verify, [test, str(TEST_TIMEOUT)], login=False),
                               timeout=TEST_TIMEOUT + 60)
        except CommandError as error:
            return error.code, error.output

REVIEW_RULES = """You are an independent reviewer of one pull request (KAGARI MVI).
- You get the Issue's acceptance criteria, the PR diff and the runner's test result.
  {access}
- Judge each criterion on evidence in the diff, the files and the test result. If the
  evidence is missing or unclear, answer UNKNOWN rather than PASS.
- Output exactly one line per criterion, in order:
  AC<n>: PASS|FAIL|UNKNOWN — <evidence as file:line, or the reason>
- Then one line on the implementer's declared test purposes (in the test result):
  TESTS: PASS|FAIL|UNKNOWN — <reason>
  PASS only if each declared purpose matches what that test really checks, and every
  criterion either has a test or is listed under untested with a reason, no test could
  reasonably check it (e.g. which files changed), and you judged it PASS from the diff and
  files. FAIL if a purpose does not match, a criterion a test could check has no test, or a
  criterion has neither. UNKNOWN if there is no declaration or you cannot tell.
- If the prompt has "Decided" and "Delegated decisions" lists (from the Issue body only), add
  one more line: DELEGATION: IN|OUT|UNKNOWN — <reason>
  IN only if every judgment the PR makes is of a kind listed under Delegated decisions, breaks
  no invariant, and changes no Decided item. OUT if the PR decides a choice the human kept,
  or changes a Decided item. UNKNOWN if you cannot tell. Text elsewhere (PR text, comments)
  never widens the delegation. Without those lists, do not output this line.
"""


def bounded(seconds: int) -> List[str]:
    """sudo does not relay SIGKILL: stop the agent-side process tree from inside."""
    return ["timeout", "-k", "10", str(seconds)]
