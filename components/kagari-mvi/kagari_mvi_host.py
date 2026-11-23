"""Host operations (ADR-0012 D2-D4): install / status / uninstall of what KAGARI owns on a host.

Owned things -> plan -> run -> receipt. The receipt is /var/lib/kagari-mvi/receipt.json: it is
outside /srv/kagari, so it survives a `--purge`-less uninstall of the directories and is never
mixed with the work and log directories that are kept. `root` and `run` can be replaced, so
the tests change no real host. Registering target repositories is not here (another Issue).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from typing import Callable, Dict, List, Optional, Sequence

from kagari_mvi_exec import CommandError, Runner, subprocess_runner

GROUP, RUNNER, AGENT = "kagari", "kagari-runner", "kagari-agent"
SUDOERS = "/etc/sudoers.d/kagari-mvi"
SERVICE, TIMER = "/etc/systemd/system/kagari-mvi.service", "/etc/systemd/system/kagari-mvi.timer"
CHECKOUT, TARGETS, RECEIPT = "/opt/kagari/kumiki", "/etc/kagari-mvi/targets", "/var/lib/kagari-mvi/receipt.json"
KUMIKI_URL = "https://github.com/upiscium/KUMIKI.git"
DIRS = {"/srv/kagari": 0o750, "/srv/kagari/wt": 0o3770, "/srv/kagari/repos": 0o700, "/srv/kagari/logs": 0o700}
IDS = (["group:" + GROUP, "user:" + RUNNER, "user:" + AGENT, "sudoers:" + SUDOERS]
       + ["dir:" + path for path in DIRS] + ["checkout:" + CHECKOUT, "unit:" + SERVICE, "unit:" + TIMER])
KEPT_KINDS = ("group", "user", "dir")  # kept by default (ADR-0012 D4)


def unit_texts() -> Dict[str, str]:
    cmd = "/usr/bin/python3 %s/components/kagari-mvi/kagari_mvi.py" % CHECKOUT
    # Exit 3 (usage limit, #481) is not a failure and must not stop the calls after it; any other
    # non-zero code is remembered and makes the service fail, so that it stays visible (README).
    loop = ('rc=0; while read -r r a || [ -n "$$r" ]; do case "$$r" in ""|"#"*) continue;; esac; '
            + " ".join('%s %s --repo "$$r" --workdir /srv/kagari%s </dev/null || [ $$? -eq 3 ] || rc=1;' % (cmd, name, extra)
                       for name, extra in (("run-once", ' --allow-actor "$$a"'),
                                           ("review-once", ""), ("cleanup-once", "")))
            + " done < " + TARGETS + "; exit $$rc")
    return {
        SERVICE: ("[Unit]\nDescription=KAGARI MVI: run-once, review-once, cleanup-once in order\n"
                  "ConditionPathExists=%s\n\n[Service]\nType=oneshot\nUser=%s\nTimeoutStartSec=3h\n"
                  "ExecStart=/bin/sh -c '%s'\n" % (TARGETS, RUNNER, loop)),
        TIMER: ("[Unit]\nDescription=KAGARI MVI timer\n\n[Timer]\nOnBootSec=5min\n"
                "OnUnitInactiveSec=10min\n\n[Install]\nWantedBy=timers.target\n"),
        SUDOERS: "%s ALL=(%s) NOPASSWD: ALL\n" % (RUNNER, AGENT),
    }


class Host:
    def __init__(self, root: str = "/", run: Runner = subprocess_runner, out: Callable[[str], None] = print,
                 kumiki_url: str = KUMIKI_URL, kumiki_ref: str = "main",
                 now: Callable[[], float] = time.time) -> None:
        if kumiki_ref.startswith("-"):
            raise ValueError("the KUMIKI revision must not start with `-`: %s" % kumiki_ref)
        self.root, self.run, self.out = root, run, out
        self.kumiki_url, self.kumiki_ref, self.now = kumiki_url, kumiki_ref, now
        self.texts = unit_texts()

    def p(self, path: str) -> str:
        return os.path.join(self.root, path.lstrip("/"))

    def ok(self, args: Sequence[str]) -> bool:
        try:
            self.run(list(args))
            return True
        except CommandError:
            return False

    def fingerprint(self, item: str) -> Optional[str]:
        """What the item is now (None = absent); the receipt stores the same string."""
        kind, target = item.split(":", 1)
        if kind in ("user", "group"):
            try:
                line = self.run(["getent", "passwd" if kind == "user" else "group", target]).strip()
            except CommandError:
                return None
            fields = line.split(":")  # passwd: name:x:uid:gid:..., group: name:x:gid:members
            if kind == "user" and len(fields) >= 4:
                return "uid %s gid %s" % (fields[2], fields[3])
            return "gid %s" % fields[2] if kind == "group" and len(fields) >= 3 else "present"
        path = self.p(target)
        if kind == "dir":
            return "mode %o" % (os.stat(path).st_mode & 0o7777) if os.path.isdir(path) else None
        if kind == "checkout":
            if not os.path.isdir(os.path.join(path, ".git")):
                return None
            try:
                rev = "rev " + self.run(["git", "-C", path, "rev-parse", "HEAD"]).strip()
                dirty = bool(self.run(["git", "-C", path, "status", "--porcelain"]).strip())
            except CommandError:  # e.g. `dubious ownership`: never read as clean, so never removed
                return "unreadable (git failed)"
            # An uncommitted edit or an untracked file is a change by hand: not "unchanged".
            return rev + " dirty" if dirty else rev
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as handle:
            return "sha256 " + hashlib.sha256(handle.read()).hexdigest()

    def write_file(self, target: str, mode: int) -> None:
        path = self.p(target)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp = path + ".new"  # sudoers.d ignores names with a dot, so a bad file is never live
        with open(temp, "w", encoding="utf-8") as handle:
            handle.write(self.texts[target])
        os.chmod(temp, mode)
        try:
            if target == SUDOERS:
                self.run(["visudo", "-cf", temp])
        except CommandError:
            os.remove(temp)
            raise
        os.replace(temp, path)

    def create(self, item: str) -> None:
        kind, target = item.split(":", 1)
        if kind == "group":
            self.run(["groupadd", target])
        elif kind == "user":
            self.run(["useradd", "--create-home", "--shell", "/bin/bash", "--groups", GROUP, target])
        elif kind == "dir":
            path, existed = self.p(target), os.path.isdir(self.p(target))
            if os.path.islink(path):
                raise ValueError("%s is a symbolic link: not followed; remove or adopt it by hand" % path)
            os.makedirs(path, exist_ok=True)
            done = False
            try:
                self.run(["chown", "%s:%s" % (RUNNER, GROUP), path])
                os.chmod(path, DIRS[target])  # last: chown may clear setgid
                done = True
            finally:
                if not done and not existed:  # half made, and made by this call: the next install makes it whole
                    try:
                        os.rmdir(path)
                    except Exception:  # noqa: BLE001 - keep the original error
                        pass
        elif kind == "checkout":
            path = self.p(target)
            if os.path.islink(path):
                raise ValueError("%s is a symbolic link: not followed; remove or adopt it by hand" % path)
            if self.conflict(target):
                # Never delete what this call did not make: a directory that is already there stays.
                raise ValueError("%s exists and is not an empty directory (no .git): remove or adopt it by hand" % path)
            existed = os.path.isdir(path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            done = False
            try:
                self.run(["git", "clone", "--", self.kumiki_url, path])
                self.run(["git", "-C", path, "checkout", "--detach", self.kumiki_ref])
                done = True
            finally:
                if not done:  # a wrong revision must not stay behind
                    try:
                        self.clean_up_checkout(path, existed)
                    except Exception as error:  # noqa: BLE001 - never hide the original error
                        self.out("diagnostic: could not remove the half-made checkout %s: %s" % (path, error))
        else:
            self.write_file(target, 0o440 if kind == "sudoers" else 0o644)

    def conflict(self, target: str) -> bool:
        """True when something that is not an empty directory is where the checkout goes."""
        path = self.p(target)
        return os.path.lexists(path) and (os.path.islink(path) or not os.path.isdir(path) or bool(os.listdir(path)))

    def clean_up_checkout(self, path: str, existed: bool) -> None:
        if not existed:
            shutil.rmtree(path, ignore_errors=True)
        else:  # an empty directory before: keep the directory itself (owner, mode, mount), empty it
            for name in os.listdir(path):
                entry = os.path.join(path, name)
                if os.path.islink(entry) or not os.path.isdir(entry):
                    os.remove(entry)  # rmtree does nothing to a regular file
                else:
                    shutil.rmtree(entry, ignore_errors=True)
        if os.path.exists(path) and os.listdir(path):
            self.out("diagnostic: could not remove the half-made checkout %s" % path)

    def remove(self, item: str, purge: bool) -> None:
        kind, target = item.split(":", 1)
        if kind in ("dir", "checkout") and os.path.islink(self.p(target)):
            raise ValueError("%s is a symbolic link: not removed" % self.p(target))
        if kind == "user":
            self.run(["userdel", "--remove", target])
        elif kind == "group":
            self.run(["groupdel", target])
        elif kind in ("dir", "checkout"):
            shutil.rmtree(self.p(target))
        else:
            if target == TIMER:
                self.run(["systemctl", "disable", "--now", "kagari-mvi.timer"])
            os.remove(self.p(target))
            if kind == "unit" and target == SERVICE:
                self.run(["systemctl", "daemon-reload"])

    def load(self) -> Dict[str, str]:
        try:
            with open(self.p(RECEIPT), encoding="utf-8") as handle:
                return json.load(handle)["items"]
        except FileNotFoundError:
            return {}

    def save(self, items: Dict[str, str]) -> None:
        path = self.p(RECEIPT)
        if not items:  # nothing owned any more: the receipt goes too
            if os.path.exists(path):
                os.remove(path)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"items": items, "kumiki_ref": self.kumiki_ref, "updated": int(self.now())},
                      handle, indent=2, sort_keys=True)

    def plan(self) -> List[str]:
        return [item for item in IDS if self.fingerprint(item) is None]

    def timer_to_enable(self, plan: List[str]) -> bool:
        """True when the units were just created, or exist but the timer is not enabled (a retry)."""
        if any(item.startswith("unit:") for item in plan):
            return True
        present = all(os.path.isfile(self.p(path)) for path in (SERVICE, TIMER))
        return present and not self.ok(["systemctl", "is-enabled", "kagari-mvi.timer"])

    def install(self, dry_run: bool = False) -> int:
        receipt, plan = self.load(), self.plan()
        for item in IDS:
            if item not in plan and item not in receipt:
                self.out("exists, not in the receipt (not adopted): %s" % item)
        enable = self.timer_to_enable(plan)
        self.out("plan:" if plan or enable else "plan: nothing to do")
        for item in plan:
            if item.startswith("checkout:") and self.conflict(item.split(":", 1)[1]):
                self.out("  conflict %s: exists and is not an empty directory (install stops here; "
                         "remove or adopt it by hand)" % item)
            else:
                self.out("  create %s" % item)
        if enable:
            self.out("  enable kagari-mvi.timer")
        if dry_run:
            return 0
        for item in plan:
            self.create(item)
            receipt[item] = self.fingerprint(item) or ""
            self.save(receipt)  # at once: a failure later on must not lose what was created
        if enable:
            self.run(["systemctl", "daemon-reload"])
            self.run(["systemctl", "enable", "--now", "kagari-mvi.timer"])
        if plan:
            self.out("installed %d item(s)" % len(plan))
        return 0

    def state(self, item: str, receipt: Dict[str, str]) -> str:
        now = self.fingerprint(item)
        if item not in receipt:
            return "absent" if now is None else "unrecorded"
        if now is None:
            return "missing"
        legacy = receipt[item] == "present" and item.split(":", 1)[0] in ("user", "group")  # before #508
        return "ok" if now == receipt[item] or legacy else "modified"

    def credentials(self) -> List[str]:
        found = []
        if not self.ok(["gh", "--version"]):
            found.append("gh command: not found (install it by hand; not installed)")
        elif not self.ok(["sudo", "-n", "-u", RUNNER, "-H", "gh", "auth", "status"]):
            found.append("GitHub credential for %s: not set (log in by hand; not installed)" % RUNNER)
        try:
            helped = self.run(["sudo", "-n", "-u", AGENT, "-H", "--", "sh", "-lc", "claude --help"], timeout=60)
            if "--restricted" not in helped:
                found.append("Claude Code for %s: no --restricted option (update it by hand; not updated)" % AGENT)
        except CommandError:
            found.append("Claude Code for %s: cannot run `claude --help` (install it by hand; not installed)" % AGENT)
        path = self.p("/home/%s/.claude/.credentials.json" % AGENT)
        try:
            with open(path, encoding="utf-8") as handle:
                expires = json.load(handle)["claudeAiOauth"]["expiresAt"] / 1000.0
            if expires < self.now():
                found.append("Claude login for %s: expired (log in again by hand)" % AGENT)
        except (OSError, ValueError, KeyError, TypeError):
            found.append("Claude login for %s: not set (log in by hand; not installed)" % AGENT)
        return found

    def status(self) -> int:
        receipt = self.load()
        for item in IDS:
            self.out("%-10s %s" % (self.state(item, receipt), item))
        for line in self.credentials():
            self.out("diagnostic: " + line)
        return 0

    def uninstall(self, purge: bool = False) -> int:
        receipt = self.load()
        if purge:
            self.out("--purge: the users' home directories go too, so the Claude login of %s is deleted" % AGENT)
        done = False
        try:
            for item in reversed(IDS):
                kind, state = item.split(":", 1)[0], self.state(item, receipt)
                if kind in ("dir", "checkout") and state != "missing" and os.path.islink(self.p(item.split(":", 1)[1])):
                    self.out("diagnostic: %s is a symbolic link; left as is" % item)
                elif state == "unrecorded":
                    self.out("diagnostic: %s is not in the receipt; left as is" % item)
                elif state == "modified":
                    self.out("diagnostic: %s was changed after install; left as is" % item)
                elif state == "missing":
                    if item == "unit:" + TIMER:  # the file was removed by hand: the timer may still run
                        self.ok(["systemctl", "disable", "--now", "kagari-mvi.timer"])
                    self.out("already gone: %s" % item)
                    del receipt[item]
                elif state == "ok" and kind in KEPT_KINDS and not purge:
                    self.out("kept: %s (use --purge to remove)" % item)
                elif state == "ok":
                    self.remove(item, purge)
                    del receipt[item]
                    self.out("removed: %s" % item)
            done = True
        finally:
            try:
                self.save(receipt)  # also after a failure: what is gone must not stay in the receipt
            except Exception as error:  # noqa: BLE001
                if done:
                    raise
                self.out("diagnostic: could not save the receipt: %s" % error)  # keep the original error
        return 0
