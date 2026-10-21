#!/usr/bin/env python3
"""Read-only durable KAGARI v0.1.0 local lifecycle location preflight.

This module determines how a future *user* (not TEST_ONLY) runtime can be
attached outside a Project. It NEVER installs, creates directories, grants
OpenCode permissions, or claims that the current TEST_ONLY Admin is production
qualified. Its output is a design/compatibility plan for #256/#275 review.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

VERSION = "0.1.0"
NAMESPACE = "kagari-v010"
SUBPREFIX = "kagari-v010-consent-install-"


class LocationBlocked(ValueError):
    pass


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise LocationBlocked(reason)


def canonical_directory(path: Path) -> Path:
    require(path.is_absolute() and path == path.resolve()
            and path.is_dir() and not path.is_symlink(),
            "expected canonical absolute ordinary directory")
    return path


def private_if_present(path: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    canonical_directory(path)
    info=path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o700
            and not os.path.ismount(path),
            "KAGARI-owned runtime directory has unknown owner/mode/mount")
    return True


def project_identity(project: Path) -> dict:
    canonical_directory(project)
    require(project.stat().st_uid == os.geteuid(),
            "Project Git root not owned by effective user")
    dotgit=project/".git"
    require(dotgit.is_dir() and not dotgit.is_symlink(),
            "v0.1.0 supports an ordinary Git .git directory; linked worktree unsupported")
    env={k:v for k,v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_OPTIONAL_LOCKS="0",
               GIT_NO_REPLACE_OBJECTS="1")
    def git(*argv):
        try:
            return subprocess.run(["git","--no-optional-locks","-C",str(project),
                                   "-c","core.fsmonitor=false",*argv],
                                  env=env,check=True,capture_output=True,
                                  timeout=10).stdout.decode().strip()
        except (OSError,UnicodeError,subprocess.SubprocessError) as exc:
            raise LocationBlocked("unable to verify exact Project Git topology") from exc
    require(git("rev-parse","--show-toplevel")==str(project),
            "Project is not exact Git toplevel")
    head=git("rev-parse","HEAD")
    require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}",head) is not None,
            "Project HEAD unavailable or noncanonical")
    info=project.stat()
    return {"root":str(project),"head":head,"device":info.st_dev,"inode":info.st_ino}


def plan(project: Path, state_home: Path) -> dict:
    ident=project_identity(project)
    canonical_directory(state_home)
    require(state_home.stat().st_uid == os.geteuid(),
            "state home must be owned by effective user")
    require(not state_home.is_relative_to(project)
            and not project.is_relative_to(state_home),
            "state storage must not overlap Project-owned bytes")
    namespace=state_home/NAMESPACE
    parent_present=private_if_present(namespace)
    # Bind a durable external location to the physical Project identity,
    # not to a branch's mutable HEAD or Project-supplied config bytes.
    salt=(f"{ident['root']}\0{ident['device']}\0{ident['inode']}").encode()
    binding=hashlib.sha256(salt).hexdigest()[:24]
    parent=namespace/(SUBPREFIX+binding)
    runtime=parent/"runtime"
    parent_exists=private_if_present(parent)
    runtime_exists=private_if_present(runtime)
    require(not parent_exists or parent_present,
            "runtime location exists without its trusted namespace")
    require(not runtime_exists or parent_exists,
            "runtime exists without its owned parent")
    return {
        "status":"READ_ONLY_DURABLE_LOCATION_PLAN",
        "version":VERSION,
        "qualification":"DESIGN_ONLY_UNRELEASED",
        "production_install_authorized":False,
        "task_permission_granted":False,
        "project":ident,
        "state_home":str(state_home),
        "namespace":str(namespace),
        "runtime_parent":str(parent),
        "runtime_root":str(runtime),
        "runtime_location_exists":runtime_exists,
        "supported_git_topology":"ordinary .git directory; no linked worktree",
        "required_future_actions":[
            "verify independent immutable Nix source/package receipt",
            "create only KAGARI-owned 0700 external state directories",
            "install owner descriptor with exact physical Project identity",
            "request fresh Project/runtime-bound Human Ask where required",
            "qualify non-TEST_ONLY Task runner with real model on disposable clone",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project-root",required=True,type=Path)
    p.add_argument("--state-home",type=Path,
                   default=Path(os.environ.get("XDG_STATE_HOME") or
                                Path.home()/".local/state"))
    args=p.parse_args(argv)
    try:
        result=plan(args.project_root,args.state_home)
        print(json.dumps(result,sort_keys=True))
        return 0
    except (LocationBlocked,OSError,ValueError) as exc:
        print(json.dumps({"status":"BLOCKED","qualification":"DESIGN_ONLY_UNRELEASED",
                          "production_install_authorized":False,
                          "reason":str(exc)[:400]},sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
