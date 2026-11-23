"""Host operations (ADR-0012 D2-D4): install / status / uninstall of what KAGARI owns on a host.

Owned things -> plan -> run -> receipt. The receipt is /var/lib/kagari-mvi/receipt.json: it is
outside /srv/kagari, so it survives a `--purge`-less uninstall of the directories and is never
mixed with the work and log directories that are kept. `root` and `run` can be replaced, so
the tests change no real host. Registering target repositories is in kagari_mvi_targets.py.
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
                 kumiki_url: str = KUMIKI_URL, kumiki_ref: Optional[str] = None,
                 now: Callable[[], float] = time.time) -> None:
        if kumiki_ref and kumiki_ref.startswith("-"):
            raise ValueError("the KUMIKI revision must not start with `-`: %s" % kumiki_ref)
        self.root, self.run, self.out = root, run, out
        self.kumiki_url, self.now = kumiki_url, now
        self.recorded_ref = self.load_ref()
        self.kumiki_ref = kumiki_ref or self.recorded_ref or "main"  # repair / reinstall keep the pin unless told
        self.made_checkout = False  # the receipt's pin changes only when a checkout is made here
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
                self.made_checkout = True
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

    def load_ref(self) -> Optional[str]:
        try:
            with open(self.p(RECEIPT), encoding="utf-8") as handle:
                ref = json.load(handle).get("kumiki_ref")
            return ref if isinstance(ref, str) and ref and not ref.startswith("-") else None
        except (OSError, ValueError, AttributeError):
            return None

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
            pinned = self.kumiki_ref if self.made_checkout or not self.recorded_ref else self.recorded_ref
            json.dump({"items": items, "kumiki_ref": pinned, "updated": int(self.now())},
                      handle, indent=2, sort_keys=True)

    def plan(self) -> List[str]:
        return [item for item in IDS if self.fingerprint(item) is None]

    def timer_to_enable(self, plan: List[str]) -> bool:
        """True when the units were just created, or exist but the timer is not enabled (a retry)."""
        if any(item.startswith("unit:") for item in plan):
            return True
        present = all(os.path.isfile(self.p(path)) for path in (SERVICE, TIMER))
        return present and not self.ok(["systemctl", "is-enabled", "kagari-mvi.timer"])

    def expected(self, item: str) -> Optional[str]:
        """What KAGARI would make (None = cannot tell offline: users, group, checkout need other rules)."""
        kind, target = item.split(":", 1)
        if kind == "dir":
            return "mode %o" % DIRS[target]
        if kind in ("sudoers", "unit"):
            return "sha256 " + hashlib.sha256(self.texts[target].encode("utf-8")).hexdigest()
        return None

    def owner_of(self, target: str) -> Optional[str]:
        try:
            return self.run(["stat", "-c", "%U:%G", self.p(target)]).strip()
        except CommandError:
            return None

    def origin_of(self, target: str) -> Optional[str]:
        try:
            return self.run(["git", "-C", self.p(target), "remote", "get-url", "origin"]).strip()
        except CommandError:
            return None

    def matches(self, item: str) -> bool:
        """True when the existing item is what install would have made (so it may be adopted).

        A user or a group is never adopted: a name cannot show who made it, and once it is in the
        receipt `uninstall --purge` would run `userdel --remove` on it (and on its home directory)."""
        kind, target = item.split(":", 1)
        now = self.fingerprint(item)
        if kind in ("user", "group") or now is None or now.startswith("unreadable") or os.path.islink(self.p(target)):
            return False
        if kind == "checkout":  # clean, and it is KUMIKI: uninstall removes the whole directory
            return not now.endswith(" dirty") and self.origin_of(target) == self.kumiki_url
        if kind == "dir" and self.owner_of(target) != "%s:%s" % (RUNNER, GROUP):
            return False
        return now == self.expected(item)

    def install(self, dry_run: bool = False, adopt: bool = False) -> int:
        receipt, plan = self.load(), self.plan()
        for item in IDS:
            if item not in plan and item not in receipt:
                if adopt and self.matches(item):
                    self.out("adopt %s" % item if not dry_run else "  would adopt %s" % item)
                    if not dry_run:
                        receipt[item] = self.fingerprint(item) or ""
                        if item.startswith("checkout:") and not self.recorded_ref:
                            self.recorded_ref = receipt[item].split()[1]  # its real revision: repair restores that, not main
                        self.save(receipt)
                elif adopt and item.split(":", 1)[0] in ("user", "group"):
                    self.out("note: %s exists and is never adopted (--purge would delete it and its home); "
                             "remove it by hand if it is not wanted" % item)
                elif adopt:
                    self.out("diagnostic: %s differs from what KAGARI makes; not adopted, left as is" % item)
                else:
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
        if now.startswith("unreadable"):  # never "ok", even if the receipt holds the same words
            return "unreadable"
        legacy = receipt[item] == "present" and item.split(":", 1)[0] in ("user", "group")  # before #508
        return "ok" if now == receipt[item] or legacy else "modified"

    def repair(self) -> int:
        """Restore what is missing or absent without `uninstall`; never touch what a person changed."""
        receipt, stuck = self.load(), 0
        todo = []
        for item in IDS:
            state = self.state(item, receipt)
            if state in ("missing", "absent"):
                todo.append(item)
            elif state == "unrecorded" and item.split(":", 1)[0] in ("user", "group"):
                self.out("note: %s exists and is not in the receipt (users and groups are never adopted)" % item)
            elif state != "ok":
                stuck += 1
                why = {"modified": "was changed after install", "unrecorded": "is not in the receipt (try `install --adopt`)",
                       "unreadable": "could not be read (git failed)"}[state]
                self.out("diagnostic: %s %s; not restored. Check it, then fix it by hand or remove it and run repair" % (item, why))
        self.out("repair plan:" if todo else "repair: nothing is missing")
        for item in todo:
            self.out("  restore %s" % item)
        try:
            for item in todo:
                try:
                    self.create(item)
                except ValueError as error:  # a conflict is a diagnosis, not a reason to stop the others
                    stuck += 1
                    self.out("diagnostic: %s not restored: %s" % (item, error))
                    continue
                receipt[item] = self.fingerprint(item) or ""
                self.save(receipt)
            if any(item.startswith("unit:") for item in todo):  # not a timer that a person stopped
                self.run(["systemctl", "daemon-reload"])
                self.run(["systemctl", "enable", "--now", "kagari-mvi.timer"])
        finally:
            self.save(receipt)
        if stuck:
            self.out("%d item(s) need your confirmation" % stuck)
        return 1 if stuck else 0

    def reinstall(self) -> int:
        """`uninstall` (default: logs, work directories and logins are kept) then `install`."""
        if self.service_busy():
            self.out("refused: kagari-mvi.service is running; wait for it to finish (or stop it), then reinstall")
            return 1
        self.uninstall()
        code = self.install()
        receipt = self.load()
        left = [item for item in IDS if self.state(item, receipt) != "ok"
                and not (self.state(item, receipt) == "unrecorded" and item.split(":", 1)[0] in ("user", "group"))]
        for item in left:
            self.out("diagnostic: %s is not in order after reinstall (%s): check it by hand" % (item, self.state(item, receipt)))
        return code or (1 if left else 0)

    def service_busy(self) -> bool:
        try:
            state = self.run(["systemctl", "show", "-p", "ActiveState", "--value", "kagari-mvi.service"]).strip()
        except CommandError:
            return False
        return state in ("active", "activating", "deactivating", "reloading")

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
                oauth = json.load(handle)["claudeAiOauth"]
            # The login ends with the refresh token; the access token is renewed by itself.
            expires = float(oauth["refreshTokenExpiresAt"] if "refreshTokenExpiresAt" in oauth else oauth["expiresAt"]) / 1000.0
            left = expires - self.now()
            if left < 0:
                found.append("Claude login for %s: expired (log in again by hand)" % AGENT)
            elif left < 7 * 86400:
                found.append("warning: Claude login for %s: expires in %d days (log in again by hand)" % (AGENT, int(left // 86400)))
            else:
                found.append("Claude login for %s: valid until %s UTC" % (AGENT, time.strftime("%Y-%m-%d %H:%M", time.gmtime(expires))))
        except (OSError, ValueError, KeyError, TypeError):
            found.append("Claude login for %s: not set (log in by hand; not installed)" % AGENT)
        return found

    def status(self) -> int:
        receipt = self.load()
        for item in IDS:
            state = self.state(item, receipt)
            self.out("%-10s %s" % (state, item))
            if state == "unrecorded":
                self.out("diagnostic: %s %s" % (item, "matches what KAGARI makes: `install --adopt` records it"
                                                 if self.matches(item) else "differs from what KAGARI makes, or is a user / group: not adoptable"))
        try:
            with open(self.p(TARGETS), encoding="utf-8") as handle:
                listed = [fields for fields in (line.split() for line in handle) if fields and not fields[0].startswith("#")]
        except FileNotFoundError:
            listed = []
        for fields in listed:  # the list is edited by register / unregister
            self.out("target     %s (actor %s)" % (fields[0], fields[1] if len(fields) > 1 else "-"))
        if not listed:
            self.out("diagnostic: no target repository is registered (run `register OWNER/REPO`)")
        for line in self.credentials():
            self.out("diagnostic: " + line)
        return 0

    def uninstall(self, purge: bool = False) -> int:
        receipt = self.load()
        if purge:
            self.out("--purge: the users' home directories go too, so the Claude login of %s is deleted" % AGENT)
        done = False
        left: List[str] = []  # items that stay as they are: a directory with one of them inside stays too
        try:
            for item in reversed(IDS):
                kind, state = item.split(":", 1)[0], self.state(item, receipt)
                here = self.p(item.split(":", 1)[1])
                inside = [other for other in left if self.p(other.split(":", 1)[1]).startswith(here + os.sep)]
                if kind in ("dir", "checkout") and state != "missing" and os.path.islink(here):
                    left.append(item)
                    self.out("diagnostic: %s is a symbolic link; left as is" % item)
                elif state == "unrecorded":
                    left.append(item)
                    self.out("diagnostic: %s is not in the receipt; left as is" % item)
                elif state == "unreadable":
                    left.append(item)
                    self.out("diagnostic: %s could not be read (git failed); left as is" % item)
                elif state == "modified":
                    left.append(item)
                    self.out("diagnostic: %s was changed after install; left as is" % item)
                elif state == "ok" and inside:
                    left.append(item)
                    self.out("diagnostic: %s contains %s, which is left as is; not removed" % (item, inside[0]))
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
