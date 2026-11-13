"""Process side of the KAGARI MVI worker (ADR-0007 I-2): agent, tests, commit.

Runs local processes only and knows nothing about GitHub. Claude and the test
command run as the agent user through sudo; git commit runs as the runner user
with an explicit git dir and hooks disabled (D3).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

TAIL_LINES = 20
TAIL_CHARS = 2000
GIT_IDENTITY = ["-c", "user.name=kagari-mvi", "-c", "user.email=kagari-mvi@localhost"]
# The worktree is writable by the agent: never let it choose code that git runs here.
SAFE_GIT = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"] + GIT_IDENTITY
# Build outputs that tests leave behind; never committed (pathspec excludes).
ARTIFACTS = [":(exclude,glob)**/%s" % pattern for pattern in (
    "__pycache__/**", "*.pyc", ".pytest_cache/**", "node_modules/**", ".mypy_cache/**")]
# Only these built-in tools exist in the session (no Cron, RemoteTrigger, Skill, ...).
# --restricted also ignores user/project/local settings and confines file tools to the cwd.
AGENT_TOOLS = "Bash,Read,Edit,Write,Glob,Grep"
# Bash is allowed broadly (it runs as the agent user, without GitHub credentials, D3);
# these are denied as a speed bump, not a boundary (python3 -c can still reach the network).
DENIED_COMMANDS = ("curl", "wget", "gh", "git push", "git remote", "ssh", "scp", "sudo", "su",
                   "nc", "ncat", "socat", "telnet")
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
TEST_HEADING = re.compile(r"^#{2,6}[ \t]*(?:テストコマンド|Test command)[ \t]*$", re.M | re.I)
AUTH_PATTERN = re.compile(r"login expired|not logged in|invalid api key|authentication_error|"
                          r"oauth token|please run /login|api error:? *401\b|\b401 unauthorized", re.I)
AGENT_RULES = """You implement exactly one GitHub Issue for the KAGARI MVI worker.
- Implement only what the Issue asks. Edit files in the current directory only.
- You may run shell commands (build, lint, tests). Do not commit: the host commits
  and opens the pull request for you.
- The host denies network and credential commands (curl, wget, gh, git push, ssh, sudo, ...).
- Do not change CLAUDE.md, AGENTS.md, `.claude/` or `.github/workflows/` unless the Issue
  lists the path under `### 変更する保護ファイル`. Other changes there stop the run before push.
- The Issue's test command, which the host re-runs to decide the result: {test}
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


def tail(text: str) -> str:
    lines = text.strip().splitlines()[-TAIL_LINES:]
    return "\n".join(lines)[-TAIL_CHARS:]


def protected_paths(paths: Sequence[str]) -> List[str]:
    """Changed paths that a human must review before anything is pushed."""
    return [path for path in paths
            if path.rsplit("/", 1)[-1] in INSTRUCTION_FILES or ".claude" in path.split("/")
            or path.startswith(".github/workflows/")]


def is_allowed(path: str, allowed: Sequence[str]) -> bool:
    return any(path == item or (item.endswith("/") and path.startswith(item)) for item in allowed)


def test_command(body: Optional[str]) -> Optional[str]:
    """First line of the first code block under a `### テストコマンド` heading."""
    heading = TEST_HEADING.search(body or "")
    if heading is None:
        return None
    section = (body or "")[heading.end():]
    fence = re.search(r"^```[^\n]*\n(.*?)^```", section, re.M | re.S)
    if fence is None or re.search(r"^#{1,6}\s", section[:fence.start()], re.M):
        return None
    lines = [line.strip() for line in fence.group(1).splitlines() if line.strip()]
    return lines[0] if lines else None


def agent_outcome(output: str, failed: bool) -> Dict[str, Any]:
    """Summarize Claude's stream-json: auth failure, final status, permission denials."""
    result, plain = None, []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            plain.append(line)  # stderr and other non-JSON text
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            result = event
    errored = failed or result is None or bool(result.get("is_error"))
    text = str((result or {}).get("result") or "")
    statuses = re.findall(r"status:\s*(COMPLETED|BLOCKED[^\n]*)", text)
    return {
        # Only the final result and stderr: file contents and ids may contain "401".
        "errored": errored,
        "auth": errored and bool(AUTH_PATTERN.search("\n".join(plain + [text]))),
        "status": statuses[-1].strip() if statuses else "(none)",
        "completed": not errored and bool(statuses) and statuses[-1] == "COMPLETED",
        "denials": len((result or {}).get("permission_denials") or []),
        "detail": tail("\n".join(plain + [text])),  # readable: no raw stream-json
        "text": text,
    }


class Blocked(Exception):
    """Stop processing and block the Issue with this comment."""


class AgentSteps:
    """Agent-side steps; the host class provides run, agent_user, claude, agent_config_dir."""

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
                                "Bash(%s)" % rule for name in DENIED_COMMANDS
                                for rule in (name, name + " *")] + [
                                "%s(/%s/%s)" % (tool, worktree, glob) for tool in ("Edit", "Write")
                                for glob in PROTECTED_GLOBS if not allowed]},
            "env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1"},
        }
        prompt = "Issue #%d: %s\n\n%s\n" % (number, issue["title"], issue.get("body") or "")
        return self.launch_claude(workdir, worktree, "issue-%d-claude" % number, AGENT_TOOLS,
                                  settings, AGENT_RULES.format(test=test), prompt)

    def run_review(self, workdir: str, review_dir: str, number: int, criteria: List[str],
                   diff: str, test_report: str) -> Dict[str, Any]:
        settings = {"permissions": {"allow": ["Read", "Glob", "Grep"],
                                    "deny": ["Bash", "Edit", "Write", "NotebookEdit", "Agent",
                                             "WebFetch", "WebSearch"]},
                    "env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1"}}
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + "\n[diff truncated; read the files for the rest]\n"
        prompt = ("Acceptance criteria:\n%s\n\nRunner test result:\n%s\n\nPR diff:\n%s\n" % (
            "\n".join("AC%d: %s" % (i, c) for i, c in enumerate(criteria, start=1)),
            test_report, diff))
        outcome = self.launch_claude(workdir, review_dir, "issue-%d-review" % number, REVIEW_TOOLS,
                                     settings, REVIEW_RULES, prompt)
        outcome["verdicts"] = review_verdicts(outcome["text"], len(criteria))
        return outcome

    def launch_claude(self, workdir: str, cwd: str, log_name: str, tools: str,
                      settings: Dict[str, Any], rules: str, prompt: str) -> Dict[str, Any]:
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
        logs = os.path.join(workdir, "logs")
        os.makedirs(logs, mode=0o700, exist_ok=True)
        with open(os.path.join(logs, log_name + ".jsonl"), "w", encoding="utf-8") as handle:
            handle.write(output)
        outcome = agent_outcome(output, failed)
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
        protected = [path for path in protected_paths(names) if not is_allowed(path, allowed)]
        if protected:
            raise Blocked("The changes touch files that need a human decision, so nothing was "
                          "pushed: %s. Commit %s is kept in `%s` for review." % (
                              ", ".join("`%s`" % path for path in protected), sha, worktree))
        return sha, protected_paths(names)

    def kill_agent_processes(self) -> None:
        # Not under timeout/as_agent: kill(-1) spares only the sending shell, not its parents.
        self.run(["sudo", "-n", "-u", self.agent_user, "-H", "--", "sh", "-c", "kill -KILL -1; true"])

    def run_test(self, clone: str, worktree: str, number: int, test: str) -> Tuple[int, str]:
        """Re-run the test ourselves on a clean checkout of the commit (as the agent user)."""
        self.kill_agent_processes()
        verify = worktree + ".verify"  # no leftover processes may write here now
        shutil.rmtree(verify, ignore_errors=True)
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
  The current directory is a checkout of the PR head; you may only read it.
- Judge each criterion on evidence in the diff, the files and the test result. If the
  evidence is missing or unclear, answer UNKNOWN rather than PASS.
- Output exactly one line per criterion, in order:
  AC<n>: PASS|FAIL|UNKNOWN — <evidence as file:line, or the reason>
"""
ALLOWED_HEADING = re.compile(r"^#{2,6}[ \t]*(?:変更する保護ファイル|Protected files to change)[ \t]*$",
                             re.M | re.I)
CRITERIA_HEADING = re.compile(r"^#{2,6}[ \t]*(?:受入条件|Acceptance criteria)[ \t]*$", re.M | re.I)
# [^\w\n] / [ \t]: a verdict never spans lines (an empty evidence must not swallow the next line).
VERDICT_LINE = re.compile(r"^[^\w\n]*AC(\d+)[^\w\n]*?[:：][^\w\n]*(PASS|FAIL|UNKNOWN)\b[*_]*[ \t]*"
                          r"[-—–:：]*[ \t]*(.*)$", re.M | re.I)


def acceptance_criteria(body: Optional[str]) -> List[str]:
    """List items (`- [ ] ...` or `- ...`) under a `### 受入条件` heading."""
    return section_items(body, CRITERIA_HEADING)


def allowed_protected(body: Optional[str]) -> List[str]:
    """Protected paths the approved Issue lets Claude change; a trailing `/` covers a directory."""
    items = [item.strip("`") for item in section_items(body, ALLOWED_HEADING)]
    return [item[2:] if item.startswith("./") else item for item in items]


def section_items(body: Optional[str], pattern: "re.Pattern[str]") -> List[str]:
    heading = pattern.search(body or "")
    if heading is None:
        return []
    section = re.split(r"^#{1,6}\s", (body or "")[heading.end():], maxsplit=1, flags=re.M)[0]
    items = re.findall(r"^\s*[-*]\s+(?:\[[ xX]\]\s*)?(.+?)\s*$", section, re.M)
    return [item for item in items if item]


def review_verdicts(text: str, count: int) -> List[Tuple[str, str]]:
    """(status, evidence) per criterion; a missing line counts as UNKNOWN."""
    found: Dict[int, Tuple[str, str]] = {}
    for n, status, evidence in VERDICT_LINE.findall(text):
        verdict = (status.upper(), evidence.strip())
        if int(n) in found and found[int(n)][0] != verdict[0]:
            verdict = ("UNKNOWN", "(conflicting verdicts from the reviewer)")
        found[int(n)] = verdict
    return [found.get(i, ("UNKNOWN", "(no verdict from the reviewer)")) for i in range(1, count + 1)]


def review_report(head: str, criteria: List[str], outcome: Dict[str, Any], fresh: bool) -> str:
    verdicts, denials = outcome["verdicts"], outcome["denials"]
    cell = lambda value: value.replace("|", "\\|").replace("\n", " ")  # noqa: E731
    rows = ["| AC%d | %s | **%s** | %s |" % (i, cell(c), status, cell(evidence))
            for i, (c, (status, evidence)) in enumerate(zip(criteria, verdicts), start=1)]
    failing = sum(1 for status, _ in verdicts if status != "PASS")
    return ("kagari-mvi: independent review (new `claude -p` session, tools %s only; same "
            "model family as the implementer, ADR-0007 D6).\n\n- reviewed head: `%s`\n"
            "- not PASS: %d of %d\n- permission denials: %d\n- reviewer session: %s\n"
            "- runner test result: %s\n\n| # | 受入条件 | 判定 | 根拠 |\n| --- | --- | --- | --- |\n%s\n"
            % (REVIEW_TOOLS, head, failing, len(verdicts), denials,
               "ended with an error" if outcome["errored"] else "ok",
               "for this head" if fresh else "missing or for another commit (not evidence)",
               "\n".join(rows)))


def bounded(seconds: int) -> List[str]:
    """sudo does not relay SIGKILL: stop the agent-side process tree from inside."""
    return ["timeout", "-k", "10", str(seconds)]


def report(test: str, code: int, output: str, outcome: Dict[str, Any], sha: str,
           protected: Sequence[str] = ()) -> str:
    changed = "".join("- protected file changed (allowed by the Issue): `%s`\n" % path
                      for path in protected)
    return ("kagari-mvi: test result recorded by the runner (not reported by Claude).\n\n"
            "- tested commit: `%s`\n" % sha + changed +
            "- test command: `%s` (run by the runner as the agent user)\n- exit code: %d\n"
            "- Claude status: `%s`\n- permission denials: %d\n\n```\n%s\n```" % (
                test, code, outcome["status"], outcome["denials"],
                tail(output).replace("```", "'''")))
