#!/usr/bin/env python3
"""KAGARI MVI worker (ADR-0007 I-1/I-2): Issue labels -> Claude Code -> Draft PR.

State lives only in GitHub Issue labels (ADR-0007 D2). One run handles at most
one Issue. Only the Issue title and body are read; comments are never read (D4).
Uses the Python standard library plus `gh api` (REST), `git`, `sudo` and `claude`.
Claude and the test command run as the agent user, which has no GitHub
credentials (D3); git commit and push run here, as the runner user.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

APPROVED = "kagari:approved"
RUNNING = "kagari:running"
IMPLEMENTED = "kagari:implemented"
REVIEWED = "kagari:reviewed"
BLOCKED = "kagari:blocked"
EXCLUDED = (RUNNING, IMPLEMENTED, REVIEWED, BLOCKED)

LABELS = {
    APPROVED: ("0e8a16", "User only: this Issue may be implemented"),
    RUNNING: ("fbca04", "kagari-mvi worker: in progress (lock)"),
    IMPLEMENTED: ("1d76db", "kagari-mvi worker: Draft PR created"),
    REVIEWED: ("5319e7", "kagari-mvi worker: review comment posted"),
    BLOCKED: ("b60205", "kagari-mvi worker: failed or needs a decision"),
}

PER_PAGE = 100
TAIL_LINES = 20
TAIL_CHARS = 2000
GIT_IDENTITY = ["-c", "user.name=kagari-mvi", "-c", "user.email=kagari-mvi@localhost"]
# The worktree is writable by the agent: never let it choose code that git runs here.
SAFE_GIT = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"] + GIT_IDENTITY
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


def label_names(issue: Dict[str, Any]) -> List[str]:
    return [label["name"] for label in issue.get("labels", [])]


def is_candidate(issue: Dict[str, Any]) -> bool:
    """Open, approved, not a PR, and in none of the worker states."""
    if "pull_request" in issue or issue.get("state") != "open":
        return False
    names = label_names(issue)
    return APPROVED in names and not any(name in names for name in EXCLUDED)


def select_issue(issues: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The oldest candidate by created_at (then number), or None."""
    candidates = [issue for issue in issues if is_candidate(issue)]
    if not candidates:
        return None
    return min(candidates, key=lambda issue: (issue["created_at"], issue["number"]))


def approval_actor(events: List[Dict[str, Any]]) -> Optional[str]:
    """Login of the actor of the last `labeled` event for kagari:approved."""
    actor = None
    for event in events:
        if event.get("event") == "labeled" and (event.get("label") or {}).get("name") == APPROVED:
            actor = (event.get("actor") or {}).get("login")
    return actor


def check_actor(actor: Optional[str], allowed: Sequence[str]) -> Optional[str]:
    """None if allowed, else the reason to block (comment text)."""
    if actor is None:
        return "No `labeled` event for `%s` was found in the timeline." % APPROVED
    if actor not in allowed:
        return ("`%s` was added by `%s`, who is not in the allow list (%s)."
                % (APPROVED, actor, ", ".join("`%s`" % name for name in allowed)))
    return None


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
    }


class Blocked(Exception):
    """Stop processing and block the Issue with this comment."""


def quote_path(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class Worker:
    def __init__(self, repo: str, run: Runner = subprocess_runner,
                 log: Callable[[str], None] = lambda message: print(message, file=sys.stderr),
                 agent_user: str = "kagari-agent", claude: str = "claude",
                 agent_config_dir: Optional[str] = None) -> None:
        if repo.count("/") != 1 or not all(repo.split("/")):
            raise ValueError("--repo must be OWNER/REPO: %r" % repo)
        self.repo = repo
        self.run = run
        self.log = log
        self.agent_user = agent_user
        self.claude = claude
        self.agent_config_dir = agent_config_dir

    # --- GitHub REST via `gh api` -------------------------------------------
    def api(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        args = ["gh", "api", "--method", method, "-H", "Accept: application/vnd.github+json", path]
        stdin = None
        if body is not None:
            args += ["--input", "-"]
            stdin = json.dumps(body)
        out = self.run(args, input=stdin)
        return json.loads(out) if out.strip() else None

    def api_pages(self, path: str) -> List[Dict[str, Any]]:
        """GET every page of a list endpoint (manual paging, no --paginate)."""
        items: List[Dict[str, Any]] = []
        sep = "&" if "?" in path else "?"
        page = 1
        while True:
            batch = self.api("GET", "%s%sper_page=%d&page=%d" % (path, sep, PER_PAGE, page))
            items.extend(batch or [])
            if not batch or len(batch) < PER_PAGE:
                return items
            page += 1

    def repo_path(self, suffix: str = "") -> str:
        return "repos/%s%s" % (self.repo, suffix)

    def add_labels(self, number: int, *names: str) -> None:
        self.api("POST", self.repo_path("/issues/%d/labels" % number), {"labels": list(names)})

    def remove_label(self, number: int, name: str) -> None:
        try:
            self.api("DELETE", self.repo_path("/issues/%d/labels/%s" % (number, quote_path(name))))
        except CommandError as error:
            if "404" not in error.output and "Label does not exist" not in error.output:
                raise

    def comment(self, number: int, body: str) -> None:
        self.api("POST", self.repo_path("/issues/%d/comments" % number), {"body": body})

    # --- commands -------------------------------------------------------------
    def ensure_labels(self) -> List[str]:
        existing = {label["name"] for label in self.api_pages(self.repo_path("/labels"))}
        created = []
        for name, (color, description) in LABELS.items():
            if name in existing:
                continue
            self.api("POST", self.repo_path("/labels"),
                     {"name": name, "color": color, "description": description})
            created.append(name)
        return created

    def list_open_approved(self) -> List[Dict[str, Any]]:
        return self.api_pages(self.repo_path(
            "/issues?state=open&labels=%s&sort=created&direction=asc" % quote_path(APPROVED)))

    def run_once(self, allowed: Sequence[str], workdir: str) -> int:
        issue = select_issue(self.list_open_approved())
        if issue is None:
            self.log("no approved Issue to process")
            return 0
        number = issue["number"]
        self.log("selected #%d" % number)

        events = self.api_pages(self.repo_path("/issues/%d/timeline" % number))
        reason = check_actor(approval_actor(events), allowed)
        test = test_command(issue.get("body"))
        if reason is None and test is None:
            reason = ("The Issue body has no test command. Add a `### テストコマンド` "
                      "heading followed by a code block whose first line is the command.")
        if reason is not None:
            self.add_labels(number, BLOCKED)
            self.comment(number, "kagari-mvi: blocked before start.\n\n" + reason)
            self.log("#%d blocked: %s" % (number, reason))
            return 0

        os.umask(0o002)  # worktree files must stay writable by the shared group
        self.add_labels(number, RUNNING)
        stage = "prepare"
        try:
            stage = "find-existing-pr"
            pr = self.find_open_pr(number)
            problem = None
            if pr is None:
                stage = "worktree"
                base = self.api("GET", self.repo_path())["default_branch"]
                clone, worktree = self.prepare_worktree(workdir, number, base)
                stage = "claude"
                outcome = self.run_agent(workdir, worktree, issue, test)
                stage = "commit"
                if not self.commit_changes(clone, worktree, number):
                    raise Blocked("Claude made no changes (status: %s)." % outcome["status"])
                stage = "test"
                code, output = self.run_test(clone, worktree, number, test)
                stage = "push"
                self.run(["git", "-C", clone] + SAFE_GIT + [
                    "push", "origin", "refs/heads/%s" % branch_name(number)])
                stage = "draft-pr"
                pr = self.create_draft_pr(issue, base)
                stage = "report"
                self.comment(pr["number"], report(test, code, output, outcome))
                if code != 0 or not outcome["completed"]:
                    problem = ("Draft PR #%d was created, but status is `%s` and the test "
                               "exit code is %d. See the PR comment." % (
                                   pr["number"], outcome["status"], code))
            else:
                problem = ("PR #%d for `%s` already exists and its test result is not re-checked. "
                           "Review it, or close it and delete the branch to retry."
                           % (pr["number"], branch_name(number)))
            stage = "labels"
            if problem is not None:
                raise Blocked(problem)
            self.add_labels(number, IMPLEMENTED)
            self.remove_label(number, RUNNING)
        except Exception as error:  # noqa: BLE001 - every failure must end as blocked
            self.block_after_failure(number, stage, error)
            return 1
        self.log("#%d implemented: %s" % (number, pr.get("html_url", pr["number"])))
        return 0

    def block_after_failure(self, number: int, stage: str, error: Exception) -> None:
        if isinstance(error, Blocked):
            body = "kagari-mvi: blocked at stage `%s`.\n\n%s" % (stage, error)
        else:
            detail = error.output if isinstance(error, CommandError) else "%s: %s" % (
                type(error).__name__, error)
            body = "kagari-mvi: failed at stage `%s`.\n\n```\n%s\n```" % (
                stage, tail(detail).replace("```", "'''"))
        self.log("#%d %s" % (number, body))
        # Add blocked before removing running: if adding fails, running stays
        # and the Issue is not picked again.
        self.add_labels(number, BLOCKED)
        try:
            self.remove_label(number, RUNNING)
        except Exception as cleanup:  # noqa: BLE001 - still post the comment
            self.log("could not remove %s: %s" % (RUNNING, cleanup))
        self.comment(number, body)

    def find_open_pr(self, number: int) -> Optional[Dict[str, Any]]:
        owner = self.repo.split("/")[0]
        head = quote_path("%s:%s" % (owner, branch_name(number)))
        pulls = self.api("GET", self.repo_path("/pulls?state=open&head=%s" % head))
        return pulls[0] if pulls else None

    def prepare_worktree(self, workdir: str, number: int, base: str) -> Tuple[str, str]:
        clone = os.path.join(workdir, "repos", self.repo.replace("/", "__"))
        worktree = os.path.join(workdir, "wt", "issue-%d" % number)
        if os.path.isdir(os.path.join(clone, ".git")):
            self.run(["git", "-C", clone] + SAFE_GIT + ["fetch", "--prune", "origin"])
        else:
            os.makedirs(os.path.dirname(clone), exist_ok=True)
            self.run(["git"] + SAFE_GIT + ["clone", "https://github.com/%s.git" % self.repo, clone])
        if os.path.exists(worktree):
            # Leftover from an earlier attempt on this Issue: the worker owns it.
            self.run(["git", "-C", clone, "worktree", "remove", "--force", worktree])
        self.run(["git", "-C", clone, "worktree", "prune"])
        os.makedirs(os.path.dirname(worktree), exist_ok=True)
        self.run(["git", "-C", clone] + SAFE_GIT + ["worktree", "add", "-B", branch_name(number),
                                                     worktree, "origin/%s" % base])
        return clone, worktree

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
                          % (self.agent_user, tail(output).replace("```", "'''")))
        if failed:
            raise CommandError(argv, 1, output)
        return outcome

    def commit_changes(self, clone: str, worktree: str, number: int) -> bool:
        # Explicit --git-dir: the `.git` file in the worktree is agent-writable.
        git = ["git", "--git-dir", os.path.join(clone, ".git", "worktrees", "issue-%d" % number),
               "--work-tree", worktree] + SAFE_GIT
        self.run(git + ["add", "--all"], cwd=worktree)
        if not self.run(git + ["status", "--porcelain"], cwd=worktree).strip():
            return False
        self.run(git + ["commit", "--no-verify", "-m", "kagari-mvi: implement #%d" % number],
                 cwd=worktree)
        return True

    def run_test(self, clone: str, worktree: str, number: int, test: str) -> Tuple[int, str]:
        """Re-run the test ourselves on a clean checkout of the commit (as the agent user)."""
        self.run(self.as_agent(worktree, ["kill -KILL -1 2>/dev/null; true", "60"], login=False))
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

    def create_draft_pr(self, issue: Dict[str, Any], base: str) -> Dict[str, Any]:
        number = issue["number"]
        body = "%s\n\n---\n\nCloses #%d\n" % (issue.get("body") or "", number)
        return self.api("POST", self.repo_path("/pulls"), {
            "title": issue["title"], "head": branch_name(number), "base": base,
            "body": body, "draft": True,
        })


def bounded(seconds: int) -> List[str]:
    """sudo does not relay SIGKILL: stop the agent-side process tree from inside."""
    return ["timeout", "-k", "10", str(seconds)]


def report(test: str, code: int, output: str, outcome: Dict[str, Any]) -> str:
    return ("kagari-mvi: test result recorded by the runner (not reported by Claude).\n\n"
            "- test command: `%s` (run by the runner as the agent user)\n- exit code: %d\n"
            "- Claude status: `%s`\n- permission denials: %d\n\n```\n%s\n```" % (
                test, code, outcome["status"], outcome["denials"],
                tail(output).replace("```", "'''")))


def branch_name(number: int) -> str:
    return "kagari/issue-%d" % number


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="kagari_mvi", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    labels = sub.add_parser("ensure-labels", help="create the five kagari:* labels if missing")
    labels.add_argument("--repo", required=True, help="OWNER/REPO")
    once = sub.add_parser("run-once", help="process at most one approved Issue")
    once.add_argument("--repo", required=True, help="OWNER/REPO")
    once.add_argument("--allow-actor", action="append", required=True,
                      help="login allowed to add kagari:approved (repeat or comma-separate)")
    once.add_argument("--workdir", required=True, help="directory for clones and worktrees")
    once.add_argument("--agent-user", default="kagari-agent", help="user that runs Claude and tests")
    once.add_argument("--claude", default="claude", help="claude executable for the agent user")
    once.add_argument("--agent-config-dir", help="CLAUDE_CONFIG_DIR for the agent (testing only)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "ensure-labels":
            worker = Worker(args.repo)
            created = worker.ensure_labels()
            print("created: %s" % (", ".join(created) if created else "(none)"))
            return 0
        allowed = [name.strip() for value in args.allow_actor for name in value.split(",")
                   if name.strip()]
        worker = Worker(args.repo, agent_user=args.agent_user, claude=args.claude,
                        agent_config_dir=args.agent_config_dir)
        return worker.run_once(allowed, os.path.abspath(args.workdir))
    except CommandError as error:
        print("kagari_mvi: %s\n%s" % (error, tail(error.output)), file=sys.stderr)
        return 1
    except ValueError as error:
        print("kagari_mvi: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
