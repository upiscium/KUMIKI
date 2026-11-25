"""Classify a remote `kagari/issue-N` branch left behind when PR creation failed (#551)."""
from __future__ import annotations

from typing import Callable, Sequence

from kagari_mvi_exec import SAFE_GIT


def orphan_branch(run: Callable[..., str], clone: str, number: int, base: str) -> str:
    """Return "none", "runner" (one `kagari-mvi: implement #N` commit on base) or "unknown"."""
    ref = "refs/remotes/origin/kagari/issue-%d" % number
    git: Sequence[str] = ["git", "-C", clone] + SAFE_GIT
    if not (run(list(git) + ["for-each-ref", "--format=%(refname)", ref]) or "").strip():
        return "none"
    log = run(list(git) + ["log", "--format=%s", "origin/%s..%s" % (base, ref)]) or ""
    subjects = [line for line in log.splitlines() if line.strip()]
    return "runner" if subjects == ["kagari-mvi: implement #%d" % number] else "unknown"
