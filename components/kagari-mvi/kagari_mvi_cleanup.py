"""`cleanup-once`: after a human merged a KAGARI PR, delete its branches and working directories (#432).

Only merged PRs are cleaned. An open PR, or one closed without a merge, keeps everything so a
human can still look at it. Kept apart from kagari_mvi.py because it runs on its own entry point,
after the human merge, and never inside run-once or review-once.
"""
from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any, Dict, List

from kagari_mvi_exec import SAFE_GIT, CommandError

LEFTOVER = re.compile(r"^issue-(\d+)(?:\.verify|\.review)?$")
SUFFIXES = ("", ".verify", ".review")


class CleanupSteps:
    """Host-side cleanup; the host class provides api, api_pages, repo, slug, run, log and the AgentSteps."""

    def cleanup_once(self, workdir: str) -> int:
        base = os.path.join(workdir, "wt", self.slug)
        names = os.listdir(base) if os.path.isdir(base) else []
        numbers = sorted({int(match.group(1)) for match in map(LEFTOVER.match, names) if match})
        failed = 0
        for number in numbers:
            branch = "kagari/issue-%d" % number
            pulls = self.pulls_for(branch)
            if any(pull.get("state") == "open" for pull in pulls) or not any(
                    pull.get("merged_at") for pull in pulls):
                self.log("#%d kept: %s has no merged PR, or an open one" % (number, branch))
                continue
            try:
                self.remove_merged(workdir, base, number, branch)
            except (CommandError, OSError) as error:  # one Issue's failure must not stop the rest
                failed += 1
                self.log("#%d cleanup failed: %s" % (number, error))
                continue
            self.log("#%d cleaned: %s merged" % (number, branch))
        return 1 if failed else 0

    def pulls_for(self, branch: str) -> List[Dict[str, Any]]:
        head = urllib.parse.quote("%s:%s" % (self.repo.split("/")[0], branch), safe="")
        return self.api_pages(self.repo_path("/pulls?state=all&head=%s" % head))

    def remove_merged(self, workdir: str, base: str, number: int, branch: str) -> None:
        self.kill_agent_processes()  # nothing may keep writing into the directories
        for suffix in SUFFIXES:
            path = os.path.join(base, "issue-%d%s" % (number, suffix))
            if os.path.lexists(path):
                self.remove_leftover(path)
        clone = os.path.join(workdir, "repos", self.slug)
        if os.path.isdir(os.path.join(clone, ".git")):
            self.run(["git", "-C", clone, "worktree", "prune"])
            if self.run(["git", "-C", clone, "branch", "--list", branch]).strip():
                self.run(["git", "-C", clone] + SAFE_GIT + ["branch", "-D", branch])
        ref = "heads/" + urllib.parse.quote(branch, safe="/")
        if self.api("GET", self.repo_path("/git/matching-refs/" + ref)):  # GitHub may have deleted it
            self.api("DELETE", self.repo_path("/git/refs/" + ref))
