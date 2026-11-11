#!/usr/bin/env python3
"""KAGARI MVI worker (ADR-0007 I-1): Issue labels -> stub commit -> Draft PR.

State lives only in GitHub Issue labels (ADR-0007 D2). One run handles at most
one Issue. Only the Issue title and body are read; comments are never read (D4).
Uses the Python standard library plus `gh api` (REST) and `git` via subprocess.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Sequence

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


class CommandError(Exception):
    """A gh/git subprocess exited non-zero."""

    def __init__(self, args: Sequence[str], code: int, output: str) -> None:
        super().__init__("%s exited %d" % (" ".join(args[:3]), code))
        self.args_list = list(args)
        self.code = code
        self.output = output


Runner = Callable[..., str]


def subprocess_runner(args: Sequence[str], cwd: Optional[str] = None,
                      input: Optional[str] = None) -> str:
    """Run a command and return stdout; raise CommandError with stdout+stderr."""
    proc = subprocess.run(list(args), cwd=cwd, input=input, capture_output=True,
                          text=True, env=_command_env())
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


def quote_path(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class Worker:
    def __init__(self, repo: str, run: Runner = subprocess_runner,
                 log: Callable[[str], None] = lambda message: print(message, file=sys.stderr)) -> None:
        if repo.count("/") != 1 or not all(repo.split("/")):
            raise ValueError("--repo must be OWNER/REPO: %r" % repo)
        self.repo = repo
        self.run = run
        self.log = log

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
        if reason is not None:
            self.add_labels(number, BLOCKED)
            self.comment(number, "kagari-mvi: blocked before start.\n\n" + reason)
            self.log("#%d blocked: %s" % (number, reason))
            return 0

        self.add_labels(number, RUNNING)
        stage = "prepare"
        try:
            stage = "find-existing-pr"
            pr = self.find_open_pr(number)
            if pr is None:
                stage = "worktree"
                base = self.api("GET", self.repo_path())["default_branch"]
                worktree = self.prepare_worktree(workdir, number, base)
                stage = "stub-commit"
                self.stub_commit(worktree, issue)
                stage = "push"
                self.run(["git", "push", "origin", "HEAD:refs/heads/%s" % branch_name(number)],
                         cwd=worktree)
                stage = "draft-pr"
                pr = self.create_draft_pr(issue, base)
            else:
                self.log("#%d already has PR #%d; reusing it" % (number, pr["number"]))
            stage = "labels"
            self.add_labels(number, IMPLEMENTED)
            self.remove_label(number, RUNNING)
        except Exception as error:  # noqa: BLE001 - every failure must end as blocked
            self.block_after_failure(number, stage, error)
            return 1
        self.log("#%d implemented: %s" % (number, pr.get("html_url", pr["number"])))
        return 0

    def block_after_failure(self, number: int, stage: str, error: Exception) -> None:
        detail = error.output if isinstance(error, CommandError) else "%s: %s" % (
            type(error).__name__, error)
        self.log("#%d failed at %s: %s" % (number, stage, tail(detail)))
        try:
            self.remove_label(number, RUNNING)
        except Exception as cleanup:  # noqa: BLE001 - still try to add blocked
            self.log("could not remove %s: %s" % (RUNNING, cleanup))
        self.add_labels(number, BLOCKED)
        self.comment(number, "kagari-mvi: failed at stage `%s`.\n\n```\n%s\n```"
                     % (stage, tail(detail).replace("```", "'''")))

    def find_open_pr(self, number: int) -> Optional[Dict[str, Any]]:
        owner = self.repo.split("/")[0]
        head = quote_path("%s:%s" % (owner, branch_name(number)))
        pulls = self.api("GET", self.repo_path("/pulls?state=open&head=%s" % head))
        return pulls[0] if pulls else None

    def prepare_worktree(self, workdir: str, number: int, base: str) -> str:
        clone = os.path.join(workdir, "repos", self.repo.replace("/", "__"))
        worktree = os.path.join(workdir, "wt", "issue-%d" % number)
        if os.path.isdir(os.path.join(clone, ".git")):
            self.run(["git", "-C", clone, "fetch", "--prune", "origin"])
        else:
            os.makedirs(os.path.dirname(clone), exist_ok=True)
            self.run(["git", "clone", "https://github.com/%s.git" % self.repo, clone])
        if os.path.exists(worktree):
            # Leftover from an earlier attempt on this Issue: the worker owns it.
            self.run(["git", "-C", clone, "worktree", "remove", "--force", worktree])
        self.run(["git", "-C", clone, "worktree", "prune"])
        os.makedirs(os.path.dirname(worktree), exist_ok=True)
        self.run(["git", "-C", clone, "worktree", "add", "-B", branch_name(number),
                  worktree, "origin/%s" % base])
        return worktree

    def stub_commit(self, worktree: str, issue: Dict[str, Any]) -> None:
        """Stub implementation: write the Issue title and body, then commit."""
        number = issue["number"]
        relative = os.path.join(".kagari-mvi", "issue-%d.md" % number)
        path = os.path.join(worktree, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("# %s\n\n%s\n" % (issue["title"], issue.get("body") or ""))
        self.run(["git", "add", "--", relative], cwd=worktree)
        self.run(["git"] + GIT_IDENTITY + ["commit", "--no-verify", "-m",
                  "kagari-mvi: stub for #%d" % number], cwd=worktree)

    def create_draft_pr(self, issue: Dict[str, Any], base: str) -> Dict[str, Any]:
        number = issue["number"]
        body = "%s\n\n---\n\nCloses #%d\n" % (issue.get("body") or "", number)
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
    once.add_argument("--repo", required=True, help="OWNER/REPO")
    once.add_argument("--allow-actor", action="append", required=True,
                      help="login allowed to add kagari:approved (repeat or comma-separate)")
    once.add_argument("--workdir", required=True, help="directory for clones and worktrees")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        worker = Worker(args.repo)
        if args.command == "ensure-labels":
            created = worker.ensure_labels()
            print("created: %s" % (", ".join(created) if created else "(none)"))
            return 0
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
