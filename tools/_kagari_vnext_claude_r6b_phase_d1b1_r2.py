"""#340 R6B D1B1 R2: bounded read-only inspection of an EXISTING Host journal.

SOURCE-ONLY / NOT WIRED TO MAIN / NO TASK DISPATCH OR PROVISIONING.
This observes a password-database-derived owner state directory and reads
at most one reserved key using the existing RecoveryJournal. Never creates
a state directory, a journal key, a grant, or an executor process.

The numeric repository ID is an UNAUTHENTICATED caller assertion here.
The observer's result is always non-granting; it is NOT an authentication,
Host provenance, complete-history or absence proof. No reliable protection
from another process with the same UID or from storage rollback is claimed.
"""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import pwd
import stat

from . import _kagari_vnext_claude_r6b_phase_d1b1_r1 as r1
from .kagari_vnext_trusted_host import RecoveryJournal, TrustedHostError

EVIDENCE = "R6B_D1B1_R2_EXISTING_ROOT_READ_ONLY_UNQUALIFIED"
_SUBDIRS = (".local", "state", "kagari", "claude-one-shot")
_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


class _Absent(Exception):
    """Existing Host state is not available; never initialize on miss."""


class _Unsafe(Exception):
    """Existing namespace or pathname identity is unsafe."""


def _result(status: str, reason: str) -> dict[str, str | bool]:
    return {
        "status": status, "reason": reason,
        "qualification": "UNQUALIFIED", "stage": "READ_ONLY",
        "evidence": EVIDENCE,
        "dispatch_allowed": False, "task_completed": False,
        "replay_allowed": False, "commit_allowed": False,
        "authenticated_absence": False, "effective_policy": "UNVERIFIED",
    }


def _verify(fd: int, owner: int, *, private: bool) -> None:
    s = os.fstat(fd)
    if (not stat.S_ISDIR(s.st_mode) or s.st_uid != owner
            or s.st_mode & (0o077 if private else 0o022)):
        raise _Unsafe("invalid_directory_mode_or_owner")


def _open_existing(stack: ExitStack, parent: int | None,
                   name: str | Path, owner: int, *, private: bool) -> int:
    try:
        fd = os.open(name, _FLAGS, dir_fd=parent)
    except FileNotFoundError:
        raise _Absent("missing_existing_directory") from None
    except OSError:
        raise _Unsafe("unsafe_existing_directory") from None
    stack.callback(os.close, fd)
    _verify(fd, owner, private=private)
    return fd


def _observe_exact_existing_root(repository_id: int, issue: int) -> dict[str, str | bool]:
    """Inspect only; no mkdir, chmod, O_CREAT, or journal.create."""
    # Host / Main authority and current Task/Contract must be checked
    # elsewhere; no caller or result from this method has dispatch authority.
    owner = os.geteuid()
    home_text = pwd.getpwuid(owner).pw_dir
    if type(home_text) is not str or not home_text:
        return _result("REFUSED", "owner_home_invalid")
    home = Path(home_text)
    if not home.is_absolute():
        return _result("REFUSED", "owner_home_invalid")
    try:
        # Inspect existing ancestors in root-to-leaf order. A dangling
        # symlink in the passwd-home prefix is unsafe, not an absent home.
        # This is a read-side classification, not a pathname-race guarantee.
        for component in (*reversed(home.parents), home):
            if stat.S_ISLNK(component.lstat().st_mode):
                return _result("REFUSED", "owner_home_unsafe")
        if home.resolve(strict=True) != home:
            return _result("REFUSED", "owner_home_unsafe")
    except FileNotFoundError:
        return _result("UNKNOWN", "owner_home_missing")
    except (OSError, RuntimeError):
        return _result("REFUSED", "owner_home_unsafe")

    component = r1.root_component(repository_id, issue)
    key = r1.reservation_key(repository_id, issue)
    root_path = home.joinpath(*_SUBDIRS, component)

    with ExitStack() as stack:
        parent = _open_existing(stack, None, home, owner, private=False)
        for idx, component_name in enumerate((*_SUBDIRS, component)):
            # .local/state are existing shared Host ancestors: never create.
            # All KAGARI-specific subdirectories must be owner-only.
            parent = _open_existing(stack, parent, component_name, owner,
                                    private=idx >= 2)
        pinned = os.fstat(parent)
        journal = RecoveryJournal(root_path)
        record = journal.read(key)
        # A replaced visible root is not the same directory descriptor.
        visible = root_path.lstat()
        if ((visible.st_dev, visible.st_ino) != (pinned.st_dev, pinned.st_ino)
                or not stat.S_ISDIR(visible.st_mode)):
            return _result("REFUSED", "root_replaced")

    if record is None:
        # A missing reservation is NOT an authenticated first-use or
        # proof that an old record was never created.
        return _result("UNKNOWN", "history_absence_unproven")
    # A valid journal record is not an authenticated Task completion or a
    # launch grant. It is at most a veto on repeat execution.
    return _result("OBSERVED_BLOCKED", "existing_reservation_observed_only")


def observe_existing_host_journal(repository_id: object, issue: object) -> dict[str, str | bool]:
    """Read one existing owner-rooted journal key with permanent non-grants.

    This method is intentionally NOT wired into KAGARI Main or exposed as
    an agent permission capability. Even a forged same-UID record only blocks.
    """
    try:
        r1.reservation_key(repository_id, issue)
    except (TypeError, ValueError):
        return _result("REFUSED", "repository_issue_invalid")
    try:
        return _observe_exact_existing_root(repository_id, issue)
    except _Absent:
        return _result("UNKNOWN", "host_journal_missing")
    except _Unsafe:
        return _result("REFUSED", "host_journal_unsafe")
    except (TrustedHostError, OSError, ValueError, RuntimeError, PermissionError):
        return _result("UNKNOWN", "journal_read_or_history_uncertain")
    except Exception:
        return _result("UNKNOWN", "unverified_host_state")
