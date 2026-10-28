"""Read-only #291 Main adapters for existing #144 TaskView GitHub/Evidence.

Does not create PRs or Evidence, infer a completed Task, or select evidence
on an agent's behalf. A trusted operator supplies an exact PR number and
optional exact immutable EvidenceRefs; the kernel validates their bindings.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

V4 = Path(__file__).resolve().parents[1] / "components/agent-core-v4"
sys.path.insert(0, str(V4))
from task_view import (
    EvidenceRef, GitHubObservation, GitHubPullRequestFacts,
    GitHubPullRequestRequest,
)

if __package__:
    from .kagari_vnext_main import MainError
else:
    from kagari_vnext_main import MainError

_REPO = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+\Z")


def github_pr_reader(
    repository: str, *, gh: str = "gh",
) -> Callable[[GitHubPullRequestRequest], GitHubObservation]:
    """Return one closed, exact native #144 GitHub observation.

    The caller provides a positive PR number, Task and branch. Observing a PR
    never authorizes a mutation, and the observed head/base repo/branch
    identity must match the requested Task.
    """
    if (type(repository) is not str or not _REPO.fullmatch(repository)
            or type(gh) is not str or not gh):
        raise MainError("GITHUB_REPOSITORY_INVALID")

    def read(request: GitHubPullRequestRequest) -> GitHubObservation:
        if (type(request) is not GitHubPullRequestRequest
                or request.repository != repository
                or type(request.number) is not int or request.number < 1):
            raise MainError("GITHUB_PR_REQUEST_INVALID")
        args = [
            gh, "pr", "view", str(request.number), "--repo", repository,
            "--json", ("number,state,isDraft,baseRefName,headRefName,"
                       "headRefOid,headRepository,isCrossRepository"),
        ]
        try:
            p = subprocess.run(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, stdin=subprocess.DEVNULL, check=False,
                timeout=15,
                env={**os.environ, "GH_PROMPT_DISABLED": "1",
                     "GIT_TERMINAL_PROMPT": "0"})
            if p.returncode != 0 or len(p.stdout.encode()) > 65_536:
                raise MainError("GITHUB_PR_UNAVAILABLE")
            v = json.loads(p.stdout)
        except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
            raise MainError("GITHUB_PR_UNAVAILABLE") from None
        if (type(v) is not dict
                or type(v.get("number")) is not int
                or v["number"] != request.number
                or v.get("isCrossRepository") is not False
                or type(v.get("headRepository")) is not dict
                or v["headRepository"].get("nameWithOwner") != repository
                or v.get("headRefName") != request.branch_ref.removeprefix("refs/heads/")
                or v.get("state") not in ("OPEN", "CLOSED", "MERGED")
                or type(v.get("isDraft")) is not bool
                or type(v.get("baseRefName")) is not str
                or not v["baseRefName"]
                or type(v.get("headRefOid")) is not str
                or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}",
                                    v["headRefOid"])):
            raise MainError("GITHUB_PR_IDENTITY_MISMATCH")
        facts = GitHubPullRequestFacts(
            base_repository=repository,
            head_repository=repository,
            number=request.number, state=v["state"].lower(),
            draft=v["isDraft"], base_ref=v["baseRefName"],
            head_ref=v["headRefName"], head_oid=v["headRefOid"])
        return GitHubObservation(facts, None)

    return read
