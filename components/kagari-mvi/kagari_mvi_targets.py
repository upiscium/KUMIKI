"""Target repositories (ADR-0012 D1/D5): register / unregister, kept in one host file.

The list is /etc/kagari-mvi/targets, one `OWNER/REPO ACTOR` per line; the timer reads it and
calls run-once, review-once and cleanup-once for every line. Nothing is put in the repository.
`root` and `run` can be replaced, so that the tests touch no real host.
"""
from __future__ import annotations

import os
import re
from typing import Callable, Dict, List, Optional

from kagari_mvi_exec import CommandError, Runner, subprocess_runner
from kagari_mvi_host import RUNNER, TARGETS

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
LOCAL_SETTINGS = ".claude/settings.local.json"


def check_names(repo: str, actors: List[str]) -> None:
    owner, _, name = repo.partition("/")
    if not (NAME.match(owner) and NAME.match(name)) or "/" in name:
        raise ValueError("OWNER/REPO expected: %r" % repo)
    for actor in actors:
        if not NAME.match(actor):
            raise ValueError("invalid login: %r" % actor)


def read_targets(path: str) -> Dict[str, str]:
    """repo -> actor, in file order; comments and blank lines are skipped."""
    found: Dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                fields = line.split()
                if fields and not fields[0].startswith("#"):
                    found[fields[0]] = fields[1] if len(fields) > 1 else ""
    except FileNotFoundError:
        pass
    return found


def write_targets(path: str, targets: Dict[str, str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".new"
    with open(temp, "w", encoding="utf-8") as handle:
        handle.write("".join("%s %s\n" % item for item in targets.items()))
    os.chmod(temp, 0o644)
    os.replace(temp, path)


def commits_local_settings(repo: str, run: Runner) -> bool:
    """True when the default branch has .claude/settings.local.json (ADR-0008 D4 premise)."""
    try:
        run(["gh", "api", "--method", "GET", "repos/%s/contents/%s" % (repo, LOCAL_SETTINGS)])
        return True
    except CommandError as error:
        if "404" in error.output or "Not Found" in error.output:
            return False
        raise


def register(repo: str, actors: Optional[List[str]] = None, root: str = "/", run: Runner = subprocess_runner,
             ensure_labels: Optional[Callable[[], List[str]]] = None,
             out: Callable[[str], None] = print) -> int:
    actors = [a.strip() for value in (actors or []) for a in value.split(",") if a.strip()]
    actors = actors or [repo.split("/")[0]]  # default: the owner
    check_names(repo, actors)
    actor = ",".join(actors)
    path = os.path.join(root, TARGETS.lstrip("/"))
    targets = read_targets(path)
    if repo in targets and targets[repo] != actor:
        raise ValueError("%s is registered with actor %r: unregister it first to change it" % (repo, targets[repo]))
    if commits_local_settings(repo, run):
        raise ValueError("%s commits %s: not registered (the run would end `blocked`); remove the file first"
                         % (repo, LOCAL_SETTINGS))
    created = ensure_labels() if ensure_labels else []
    if repo in targets:
        out("already registered: %s%s" % (repo, " (labels created: %s)" % ", ".join(created) if created else ""))
        return 0
    targets[repo] = actor
    write_targets(path, targets)
    out("registered: %s (actor %s); labels created: %s" % (repo, actor, ", ".join(created) or "(none)"))
    return 0


def unregister(repo: str, root: str = "/", out: Callable[[str], None] = print) -> int:
    check_names(repo, [])
    path = os.path.join(root, TARGETS.lstrip("/"))
    targets = read_targets(path)
    if repo not in targets:
        out("not registered: %s" % repo)
        return 0
    del targets[repo]
    write_targets(path, targets)
    out("unregistered: %s (labels, Issues and PRs are untouched)" % repo)
    return 0


def runner_as_service_user(run: Runner = subprocess_runner) -> Runner:
    """When root registers, gh must use the credential of kagari-runner, as the timer does."""
    if os.geteuid() != 0:
        return run
    return lambda args, **kw: run(["sudo", "-n", "-u", RUNNER, "-H", "--"] + list(args), **kw)
