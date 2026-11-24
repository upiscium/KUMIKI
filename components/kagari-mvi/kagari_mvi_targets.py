"""Target repositories (ADR-0012 D1/D5): register / unregister, kept in one host file.

The list is /etc/kagari-mvi/targets, one `OWNER/REPO ACTOR` per line; the timer reads it and
calls run-once, review-once and cleanup-once for every line. Nothing is put in the repository.
`root` and `run` can be replaced, so that the tests touch no real host.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import re
from typing import Callable, Dict, Iterator, List, Optional

from kagari_mvi_exec import CommandError, Runner, subprocess_runner
from kagari_mvi_host import RUNNER, TARGETS

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")  # used with fullmatch: `$` would let a trailing newline in
LOCAL_SETTINGS = ".claude/settings.local.json"


def check_names(repo: str, actors: List[str]) -> None:
    owner, _, name = repo.partition("/")
    if not (NAME.fullmatch(owner) and NAME.fullmatch(name)):
        raise ValueError("OWNER/REPO expected: %r" % repo)
    for actor in actors:
        if not NAME.fullmatch(actor):
            raise ValueError("invalid login: %r" % actor)


def read_lines(path: str) -> List[str]:
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            parts = handle.read().split("\n")  # not splitlines(): it also splits at \x0b, \x1c, U+2028 ...
    except FileNotFoundError:
        return []
    if parts[-1] == "":
        parts.pop()
    return [part[:-1] if part.endswith("\r") else part for part in parts]


def entry(line: str) -> Optional[List[str]]:
    """The fields of a target line; None for a comment or a blank line."""
    fields = re.split(r"[ \t]+", line.strip(" \t"))  # as the shell's `read`: not str.split(), which also splits at \x0b ...
    return fields if fields[0] and not fields[0].startswith("#") else None


def read_targets(path: str) -> Dict[str, str]:
    """repo -> actor, in file order; comments and blank lines are skipped."""
    found: Dict[str, str] = {}
    for line in read_lines(path):
        fields = entry(line)
        if fields:
            found[fields[0]] = fields[1] if len(fields) > 1 else ""
    return found


def find(found: Dict[str, str], repo: str) -> Optional[str]:
    """The spelling in the list: GitHub names are case-insensitive, so O/R and o/r are one repository."""
    return next((key for key in found if key.lower() == repo.lower()), None)


def check_writable(path: str) -> None:
    """Fail before any label is made: the list is written as root (the labels use kagari-runner's login)."""
    probe = os.path.dirname(path)
    while not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:  # a relative root with nothing under it: "" or "/" would loop forever
            raise PermissionError("cannot write %s: no existing parent directory" % path)
        probe = parent
    if not os.access(probe, os.W_OK):
        raise PermissionError("cannot write %s: run register / unregister as root" % path)


def ensure_dir(path: str) -> None:
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
        os.chmod(folder, 0o755)  # kagari-runner reads the list: not what a strict umask would give


@contextlib.contextmanager
def locked(path: str) -> Iterator[None]:
    """One writer at a time: the list is read again and written inside this lock."""
    ensure_dir(path)
    descriptor = os.open(path + ".lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)  # closing releases the lock


def write_lines(path: str, lines: List[str]) -> None:
    ensure_dir(path)
    temp = path + ".new"
    with open(temp, "w", encoding="utf-8", newline="") as handle:
        handle.write("".join(line + "\n" for line in lines))
    os.chmod(temp, 0o644)
    os.replace(temp, path)


def commits_local_settings(repo: str, run: Runner) -> bool:
    """True when the default branch has .claude/settings.local.json (ADR-0008 D4 premise)."""
    try:
        run(["gh", "api", "--method", "GET", "repos/%s/contents/%s" % (repo, LOCAL_SETTINGS)])
        return True
    except CommandError as error:
        if "HTTP 404" in error.output:  # not a stray "404" in some other failure
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
    check_writable(path)

    def check_actor() -> Optional[str]:
        found = read_targets(path)
        key = find(found, repo)
        if key is not None and found[key] != actor:
            raise ValueError("%s is registered with actor %r: unregister it first to change it" % (key, found[key]))
        return key

    check_actor()
    if commits_local_settings(repo, run):
        raise ValueError("%s commits %s: not registered (the run would end `blocked`); remove the file first"
                         % (repo, LOCAL_SETTINGS))
    created = ensure_labels() if ensure_labels else []
    with locked(path):  # the list may have changed during the calls above: read it again here
        key = check_actor()
        if key is not None:
            out("already registered: %s%s" % (key, " (labels created: %s)" % ", ".join(created) if created else ""))
            return 0
        write_lines(path, read_lines(path) + ["%s %s" % (repo, actor)])
    out("registered: %s (actor %s); labels created: %s" % (repo, actor, ", ".join(created) or "(none)"))
    return 0


def unregister(repo: str, root: str = "/", out: Callable[[str], None] = print) -> int:
    check_names(repo, [])
    path = os.path.join(root, TARGETS.lstrip("/"))
    check_writable(path)
    with locked(path):
        lines = read_lines(path)
        kept = [line for line in lines if not (entry(line) and entry(line)[0].lower() == repo.lower())]
        if len(kept) == len(lines):
            out("not registered: %s" % repo)
            return 0
        write_lines(path, kept)  # comments and blank lines stay
    out("unregistered: %s (labels, Issues and PRs are untouched)" % repo)
    return 0


def runner_as_service_user(run: Runner = subprocess_runner) -> Runner:
    """When root registers, gh must use the credential of kagari-runner, as the timer does."""
    if os.geteuid() != 0:
        return run
    return lambda args, **kw: run(["sudo", "-n", "-u", RUNNER, "-H", "--"] + list(args), **kw)
