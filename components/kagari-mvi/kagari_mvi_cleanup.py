"""`cleanup-once`: after a human merged a KAGARI PR, delete its branches and working directories (#432).

Only what a merged PR holds is cleaned: the PR must be merged, no PR of the branch may be open, and
the local and remote branch tips must still be the merged head (a later run may have left new,
unpushed work there for a human to see). Kept apart from kagari_mvi.py because it runs on its own
entry point, after the human merge, and never inside run-once or review-once.
"""
from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional

from kagari_mvi_exec import SAFE_GIT, CommandError

LEFTOVER = re.compile(r"^issue-(\d+)(?:\.verify|\.review)?$")
BRANCH = re.compile(r"^kagari/issue-(\d+)$")
SUFFIXES = ("", ".verify", ".review")


class CleanupSteps:
    """Host-side cleanup; the host class provides api, api_pages, repo, slug, run, log and the AgentSteps."""

    def cleanup_once(self, workdir: str) -> int:
        base = os.path.join(workdir, "wt", self.slug)
        clone = os.path.join(workdir, "repos", self.slug)
        try:
            numbers = self.leftover_numbers(base, clone)
        except (CommandError, OSError) as error:
            self.log("cleanup failed before any Issue: %s" % error)
            return 1
        failed = 0
        for number in numbers:
            branch = "kagari/issue-%d" % number
            try:
                reason = self.keep_reason(clone, branch)
                if reason:
                    self.log("#%d kept: %s" % (number, reason))
                    continue
                self.remove_merged(base, clone, number, branch)
            except (CommandError, OSError, ValueError) as error:  # one failure must not stop the rest
                failed += 1
                self.log("#%d cleanup failed: %s" % (number, error))
                continue
            self.log("#%d cleaned: %s merged" % (number, branch))
        return 1 if failed else 0

    def leftover_numbers(self, base: str, clone: str) -> List[int]:
        """Working directories and local branches: a branch left by a failed run is retried."""
        names = os.listdir(base) if os.path.isdir(base) else []
        found = {int(match.group(1)) for match in map(LEFTOVER.match, names) if match}
        if os.path.isdir(os.path.join(clone, ".git")):
            listed = self.run(["git", "-C", clone, "branch", "--list", "--format=%(refname:short)",
                               "kagari/issue-*"])
            found |= {int(match.group(1)) for match in map(BRANCH.match, listed.split()) if match}
        return sorted(found)

    def keep_reason(self, clone: str, branch: str) -> Optional[str]:
        head = urllib.parse.quote("%s:%s" % (self.repo.split("/")[0], branch), safe="")
        pulls: List[Dict[str, Any]] = self.api_pages(self.repo_path("/pulls?state=all&head=%s" % head))
        merged = [pull for pull in pulls if pull.get("merged_at")]
        if any(pull.get("state") == "open" for pull in pulls) or not merged:
            return "%s has no merged PR, or an open one" % branch
        heads = {(pull.get("head") or {}).get("sha"): pull["number"] for pull in merged}
        remote, local = self.remote_tip(branch), self.local_tip(clone, branch)
        if remote and remote not in heads:  # pushed after the merge
            return "%s has commits after the merged PR" % branch
        if local and local not in heads and not any(  # behind is fine: e.g. a human pushed, then merged
                self.is_ancestor(clone, local, sha, number) for sha, number in heads.items()):
            return "%s has commits after the merged PR" % branch
        return None

    def is_ancestor(self, clone: str, tip: str, head: str, pull: int) -> bool:
        self.run(["git", "-C", clone] + SAFE_GIT + ["fetch", "origin", "pull/%d/head" % pull])
        try:
            self.run(["git", "-C", clone, "merge-base", "--is-ancestor", tip, head])
        except CommandError as error:
            if error.code == 1:  # not an ancestor; other codes are real failures
                return False
            raise
        return True

    def local_tip(self, clone: str, branch: str) -> Optional[str]:
        if not os.path.isdir(os.path.join(clone, ".git")):
            return None
        out = self.run(["git", "-C", clone, "for-each-ref", "--format=%(objectname)", "refs/heads/" + branch])
        return out.strip() or None

    def remote_tip(self, branch: str) -> Optional[str]:
        refs = self.api("GET", self.repo_path("/git/matching-refs/heads/" + urllib.parse.quote(branch, safe="/")))
        exact = [ref for ref in refs or [] if ref.get("ref") == "refs/heads/" + branch]  # a prefix match
        return exact[0]["object"]["sha"] if exact else None

    def remove_merged(self, base: str, clone: str, number: int, branch: str) -> None:
        if self.remote_tip(branch):  # GitHub may have deleted it on merge
            self.api("DELETE", self.repo_path("/git/refs/heads/" + urllib.parse.quote(branch, safe="/")))
        self.kill_agent_processes()  # nothing may keep writing into the directories
        for suffix in SUFFIXES:
            path = os.path.join(base, "issue-%d%s" % (number, suffix))
            if os.path.lexists(path):
                self.remove_leftover(path)
        if self.local_tip(clone, branch):  # last: a failure leaves the branch, so the next run retries
            self.run(["git", "-C", clone, "worktree", "prune"])
            self.run(["git", "-C", clone] + SAFE_GIT + ["branch", "-D", branch])
