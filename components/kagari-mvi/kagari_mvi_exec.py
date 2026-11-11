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
- Do not run git, gh, curl or other network commands, and do not commit.
  The host commits and opens the pull request for you.
- You may run this test command, exactly as written: {test}
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
        "auth": errored and bool(AUTH_PATTERN.search("\n".join(plain + [text]))),
        "status": statuses[-1].strip() if statuses else "(none)",
        "completed": not errored and bool(statuses) and statuses[-1] == "COMPLETED",
        "denials": len((result or {}).get("permission_denials") or []),
        "detail": tail("\n".join(plain + [text])),  # readable: no raw stream-json
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
        if os.path.lexists(os.path.join(worktree, ".claude", "settings.local.json")):
            raise Blocked("The repository commits `.claude/settings.local.json`; it would widen "
                          "Claude's permissions (hooks, allow rules, plugins). Remove it to proceed.")
        settings = {
            "permissions": {"allow": ["Read", "Glob", "Grep", "Edit(/%s/**)" % worktree,
                                      "Write(/%s/**)" % worktree, "Bash(%s)" % test],
                            "deny": ["Agent", "WebFetch", "WebSearch", "NotebookEdit"]},
            "env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1"},
        }
        argv = [self.claude, "-p", "--output-format", "stream-json", "--verbose",
                "--no-session-persistence", "--setting-sources=local",
                "--settings=" + json.dumps(settings), "--strict-mcp-config",
                "--mcp-config=" + json.dumps({"mcpServers": {}}),
                "--append-system-prompt", AGENT_RULES.format(test=test)]
        prompt = "Issue #%d: %s\n\n%s\n" % (number, issue["title"], issue.get("body") or "")
        failed = False
        try:
            output = self.run(self.as_agent(worktree, bounded(AGENT_TIMEOUT) + argv), input=prompt,
                              timeout=AGENT_TIMEOUT + 60)
        except CommandError as error:
            output, failed = error.output, True
        logs = os.path.join(workdir, "logs")
        os.makedirs(logs, mode=0o700, exist_ok=True)
        with open(os.path.join(logs, "issue-%d-claude.jsonl" % number), "w",
                  encoding="utf-8") as handle:
            handle.write(output)
        outcome = agent_outcome(output, failed)
        if outcome["auth"]:
            raise Blocked("Claude Code failed to authenticate: %s needs to log in again "
                          "(kagari-agent の再ログインが必要). Not retried.\n\n```\n%s\n```"
                          % (self.agent_user, outcome["detail"].replace("```", "'''")))
        if failed:
            raise CommandError(argv, 1, output)
        return outcome

    def commit_changes(self, clone: str, worktree: str, number: int) -> bool:
        # Explicit --git-dir: the `.git` file in the worktree is agent-writable.
        git = ["git", "--git-dir", os.path.join(clone, ".git", "worktrees", "issue-%d" % number),
               "--work-tree", worktree] + SAFE_GIT
        self.run(git + ["add", "--all", "--", "."] + ARTIFACTS, cwd=worktree)
        if not self.run(git + ["diff", "--cached", "--name-only"], cwd=worktree).strip():
            return False
        self.run(git + ["commit", "--no-verify", "-m", "kagari-mvi: implement #%d" % number],
                 cwd=worktree)
        return True

    def run_test(self, clone: str, worktree: str, number: int, test: str) -> Tuple[int, str]:
        """Re-run the test ourselves on a clean checkout of the commit (as the agent user)."""
        # Not under timeout/as_agent: kill(-1) spares only the sending shell, not its parents.
        self.run(["sudo", "-n", "-u", self.agent_user, "-H", "--", "sh", "-c", "kill -KILL -1; true"])
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

def bounded(seconds: int) -> List[str]:
    """sudo does not relay SIGKILL: stop the agent-side process tree from inside."""
    return ["timeout", "-k", "10", str(seconds)]


def report(test: str, code: int, output: str, outcome: Dict[str, Any]) -> str:
    return ("kagari-mvi: test result recorded by the runner (not reported by Claude).\n\n"
            "- test command: `%s` (run by the runner as the agent user)\n- exit code: %d\n"
            "- Claude status: `%s`\n- permission denials: %d\n\n```\n%s\n```" % (
                test, code, outcome["status"], outcome["denials"],
                tail(output).replace("```", "'''")))
