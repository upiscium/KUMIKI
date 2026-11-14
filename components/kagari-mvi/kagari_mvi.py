#!/usr/bin/env python3
"""KAGARI MVI worker (ADR-0007 I-1/I-2): Issue labels -> Claude Code -> Draft PR.

State lives only in GitHub Issue labels (ADR-0007 D2). One run handles at most
one Issue. Only the Issue title and body are read; comments are never read (D4).
Uses the Python standard library plus `gh api` (REST), `git`, `sudo` and `claude`.
This file holds the GitHub side; kagari_mvi_exec.py runs the agent, tests and commit.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from kagari_mvi_exec import (SAFE_GIT, AgentSteps, Blocked, CommandError, Runner,
                             acceptance_criteria, report, review_report, subprocess_runner, tail,
                             test_command)

TEST_REPORT_PREFIX = "kagari-mvi: test result recorded by the runner"

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


def label_names(issue: Dict[str, Any]) -> List[str]:
    return [label["name"] for label in issue.get("labels", [])]


def is_candidate(issue: Dict[str, Any]) -> bool:
    """Open, approved, not a PR, and in none of the worker states."""
    if "pull_request" in issue or issue.get("state") != "open":
        return False
    names = label_names(issue)
    return APPROVED in names and not any(name in names for name in EXCLUDED)


def is_review_candidate(issue: Dict[str, Any]) -> bool:
    """Open, approved and implemented, not a PR, not locked, reviewed or blocked."""
    if "pull_request" in issue or issue.get("state") != "open":
        return False
    names = label_names(issue)
    return (APPROVED in names and IMPLEMENTED in names
            and not any(name in names for name in (RUNNING, REVIEWED, BLOCKED)))


def oldest(issues: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return min(issues, key=lambda issue: (issue["created_at"], issue["number"])) if issues else None


def select_issue(issues: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The oldest candidate by created_at (then number), or None."""
    return oldest([issue for issue in issues if is_candidate(issue)])


def last_approval(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The last `labeled` event for kagari:approved ({} if none)."""
    found: Dict[str, Any] = {}
    for event in events:
        if event.get("event") == "labeled" and (event.get("label") or {}).get("name") == APPROVED:
            found = event
    return found


def approval_actor(events: List[Dict[str, Any]]) -> Optional[str]:
    """Login of the actor of the last `labeled` event for kagari:approved."""
    return (last_approval(events).get("actor") or {}).get("login")


def check_body_unchanged(edited_at: Optional[str], approved_at: Optional[str]) -> Optional[str]:
    """None if the body was not edited after approval, else the reason to block (#422)."""
    when = lambda value: datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))  # noqa: E731
    if edited_at is None or (approved_at is not None and when(edited_at) <= when(approved_at)):
        return None
    return ("The Issue body was edited after `%s` was added (edited %s, approved %s). Check the "
            "body, then remove and add `%s` again." % (APPROVED, edited_at, approved_at, APPROVED))


def check_actor(actor: Optional[str], allowed: Sequence[str]) -> Optional[str]:
    """None if allowed, else the reason to block (comment text)."""
    if actor is None:
        return "No `labeled` event for `%s` was found in the timeline." % APPROVED
    if actor not in allowed:
        return ("`%s` was added by `%s`, who is not in the allow list (%s)."
                % (APPROVED, actor, ", ".join("`%s`" % name for name in allowed)))
    return None



def quote_path(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class Worker(AgentSteps):
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

    def body_edited_at(self, number: int) -> Optional[str]:
        """lastEditedAt of the Issue body. REST has no edit time, so GraphQL here only (#422)."""
        owner, name = self.repo.split("/")
        out = self.run(["gh", "api", "--method", "POST", "-H", "Accept: application/vnd.github+json",
                        "graphql", "-f", "query=query($o:String!,$n:String!,$i:Int!){repository("
                        "owner:$o,name:$n){issue(number:$i){lastEditedAt}}}",
                        "-f", "o=" + owner, "-f", "n=" + name, "-F", "i=%d" % number])
        found = ((json.loads(out).get("data") or {}).get("repository") or {}).get("issue")
        if not found:
            raise ValueError("GraphQL did not return Issue #%d: %s" % (number, out.strip()))
        return found["lastEditedAt"]

    def api_text(self, path: str, accept: str) -> str:
        return self.run(["gh", "api", "--method", "GET", "-H", "Accept: " + accept, path])

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

    def list_open_labeled(self, label: str) -> List[Dict[str, Any]]:
        return self.api_pages(self.repo_path(
            "/issues?state=open&labels=%s&sort=created&direction=asc" % quote_path(label)))

    def review_once(self, workdir: str) -> int:
        issue = oldest([i for i in self.list_open_labeled(IMPLEMENTED) if is_review_candidate(i)])
        if issue is None:
            self.log("no implemented Issue to review")
            return 0
        number = issue["number"]
        self.log("reviewing #%d" % number)
        self.add_labels(number, RUNNING)
        stage = "find-pr"
        try:
            pr = self.find_open_pr(number)
            if pr is None:
                raise Blocked("No open PR for `%s` to review." % branch_name(number))
            stage = "inputs"
            criteria = acceptance_criteria(issue.get("body"))
            if not criteria:
                raise Blocked("The Issue body has no `### 受入条件` list to review against.")
            pr = self.api("GET", self.repo_path("/pulls/%d" % pr["number"]))
            head = pr["head"]["sha"]
            diff = self.api_text(self.repo_path("/pulls/%d" % pr["number"]),
                                 "application/vnd.github.diff")
            test_report = self.runner_test_report(pr["number"])
            fresh = ("tested commit: `%s`" % head) in test_report
            if not fresh:  # e.g. commits were pushed after the runner's test
                test_report = ("WARNING: this runner test result is missing or for another commit "
                               "than the reviewed head %s. Do not use it as evidence.\n\n%s"
                               % (head, test_report))
            stage = "checkout"
            review_dir = self.prepare_review_dir(workdir, number, head)
            stage = "review"
            outcome = self.run_review(workdir, review_dir, number, criteria, diff, test_report)
            stage = "report"
            self.comment(pr["number"], review_report(head, criteria, outcome, fresh))
            stage = "labels"
            self.add_labels(number, REVIEWED)
            self.remove_label(number, RUNNING)
        except Exception as error:  # noqa: BLE001 - every failure must end as blocked
            self.block_after_failure(number, stage, error)
            return 1
        self.log("#%d reviewed: PR #%d" % (number, pr["number"]))
        return 0

    def runner_test_report(self, pr_number: int) -> str:
        """The runner's own latest test comment; text that others post is ignored."""
        me = self.api("GET", "user")["login"]
        comments = self.api_pages(self.repo_path("/issues/%d/comments" % pr_number))
        mine = [c["body"] for c in comments if (c.get("user") or {}).get("login") == me
                and (c.get("body") or "").startswith(TEST_REPORT_PREFIX)]
        return mine[-1] if mine else "(no runner test result was found on the PR)"

    def prepare_review_dir(self, workdir: str, number: int, head: str) -> str:
        clone = os.path.join(workdir, "repos", self.repo.replace("/", "__"))
        review_dir = os.path.join(workdir, "wt", "issue-%d.review" % number)
        self.kill_agent_processes()  # nothing left over may write into the review checkout
        self.run(["git", "-C", clone] + SAFE_GIT + ["fetch", "--prune", "origin"])
        if os.path.exists(review_dir):
            self.run(["git", "-C", clone, "worktree", "remove", "--force", review_dir])
        self.run(["git", "-C", clone, "worktree", "prune"])
        self.run(["git", "-C", clone] + SAFE_GIT + ["worktree", "add", "--detach", review_dir, head])
        return review_dir

    def list_open_approved(self) -> List[Dict[str, Any]]:
        return self.list_open_labeled(APPROVED)

    def run_once(self, allowed: Sequence[str], workdir: str) -> int:
        issue = select_issue(self.list_open_approved())
        if issue is None:
            self.log("no approved Issue to process")
            return 0
        number = issue["number"]
        self.log("selected #%d" % number)

        events = self.api_pages(self.repo_path("/issues/%d/timeline" % number))
        reason = check_actor(approval_actor(events), allowed)
        if reason is None:
            reason = check_body_unchanged(self.body_edited_at(number),
                                          last_approval(events).get("created_at"))
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
                sha = self.commit_changes(clone, worktree, number)
                if not sha:
                    raise Blocked("Claude made no changes (status: %s)." % outcome["status"])
                stage = "test"
                code, output = self.run_test(clone, worktree, number, test)
                stage = "push"
                self.run(["git", "-C", clone] + SAFE_GIT + [
                    "push", "origin", "refs/heads/%s" % branch_name(number)])
                stage = "draft-pr"
                pr = self.create_draft_pr(issue, base)
                stage = "report"
                self.comment(pr["number"], report(test, code, output, outcome, sha))
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

    def create_draft_pr(self, issue: Dict[str, Any], base: str) -> Dict[str, Any]:
        number = issue["number"]
        body = "%s\n\n---\n\nBase branch: `%s` (the repository's default branch)\n\nCloses #%d\n" % (
            issue.get("body") or "", base, number)
        return self.api("POST", self.repo_path("/pulls"), {
            "title": issue["title"], "head": branch_name(number), "base": base,
            "body": body, "draft": True,
        })


def branch_name(number: int) -> str:
    return "kagari/issue-%d" % number


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="kagari_mvi", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    labels = sub.add_parser("ensure-labels", help="create the five kagari:* labels if missing")
    labels.add_argument("--repo", required=True, help="OWNER/REPO")
    once = sub.add_parser("run-once", help="process at most one approved Issue")
    once.add_argument("--allow-actor", action="append", required=True,
                      help="login allowed to add kagari:approved (repeat or comma-separate)")
    review = sub.add_parser("review-once", help="review at most one implemented Issue's PR")
    for command in (once, review):
        command.add_argument("--repo", required=True, help="OWNER/REPO")
        command.add_argument("--workdir", required=True, help="directory for clones and worktrees")
        command.add_argument("--agent-user", default="kagari-agent",
                             help="user that runs Claude and tests")
        command.add_argument("--claude", default="claude", help="claude executable for the agent user")
        command.add_argument("--agent-config-dir", help="CLAUDE_CONFIG_DIR for the agent (testing only)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "ensure-labels":
            worker = Worker(args.repo)
            created = worker.ensure_labels()
            print("created: %s" % (", ".join(created) if created else "(none)"))
            return 0
        worker = Worker(args.repo, agent_user=args.agent_user, claude=args.claude,
                        agent_config_dir=args.agent_config_dir)
        if args.command == "review-once":
            return worker.review_once(os.path.abspath(args.workdir))
        allowed = [name.strip() for value in args.allow_actor for name in value.split(",")
                   if name.strip()]
        return worker.run_once(allowed, os.path.abspath(args.workdir))
    except CommandError as error:
        print("kagari_mvi: %s\n%s" % (error, tail(error.output)), file=sys.stderr)
        return 1
    except ValueError as error:
        print("kagari_mvi: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
