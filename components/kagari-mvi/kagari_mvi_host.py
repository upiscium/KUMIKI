"""Host operations of KAGARI (ADR-0012 D2-D4): install / status / uninstall [--purge].

Owned things are listed once (`owned`), then planned, applied and recorded in a receipt.
`uninstall` removes only what the receipt lists and what is unchanged. The root path and
the command runner are injectable, so the tests change nothing on a real host.
Python standard library only. GitHub credentials and the Claude login are never installed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from kagari_mvi_exec import CommandError, Runner, subprocess_runner

RUNNER, AGENT, GROUP = "kagari-runner", "kagari-agent", "kagari"
SRV, CHECKOUT = "/srv/kagari", "/opt/kagari/kumiki"
# Outside /srv/kagari, so that `--purge` of the work directories never removes the record.
RECEIPT = "/var/lib/kagari/receipt.json"
SUDOERS = "/etc/sudoers.d/kagari-mvi"
SERVICE, TIMER = "/etc/systemd/system/kagari-mvi.service", "/etc/systemd/system/kagari-mvi.timer"
KUMIKI_URL = "https://github.com/upiscium/KUMIKI.git"
DIRS = ((SRV, 0o750), (SRV + "/wt", 0o3770), (SRV + "/repos", 0o700), (SRV + "/logs", 0o700))
SUDOERS_TEXT = "%s ALL=(%s) NOPASSWD: ALL\n" % (RUNNER, AGENT)
SERVICE_TEXT = """[Unit]
Description=KAGARI MVI worker (run-once, review-once, cleanup-once in order)

[Service]
Type=oneshot
User=%s
# Per-repository ExecStart lines are added when repositories are registered (separate Issue).
ExecStart=/bin/true
""" % RUNNER
TIMER_TEXT = """[Unit]
Description=Run the KAGARI MVI worker periodically

[Timer]
OnCalendar=*:0/15
Persistent=true

[Install]
WantedBy=timers.target
"""


def owned(url: str = KUMIKI_URL, ref: str = "main") -> List[Dict[str, Any]]:
    """The one list of what `install` owns (ADR-0012 D2); repositories are not part of it."""
    items = [{"key": "group:" + GROUP, "kind": "group", "name": GROUP}]
    items += [{"key": "user:" + n, "kind": "user", "name": n} for n in (RUNNER, AGENT)]
    items.append({"key": "sudoers", "kind": "file", "path": SUDOERS, "text": SUDOERS_TEXT, "mode": 0o440})
    items += [{"key": "dir:" + p, "kind": "dir", "path": p, "mode": m} for p, m in DIRS]
    items.append({"key": "checkout", "kind": "checkout", "path": CHECKOUT, "url": url, "ref": ref})
    items.append({"key": "service", "kind": "file", "path": SERVICE, "text": SERVICE_TEXT, "mode": 0o644})
    items.append({"key": "timer", "kind": "file", "path": TIMER, "text": TIMER_TEXT, "mode": 0o644})
    return items


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Host:
    def __init__(self, root: str = "/", run: Runner = subprocess_runner,
                 out: Callable[[str], None] = print) -> None:
        self.root, self.run, self.out = root, run, out

    def at(self, path: str) -> str:
        return os.path.join(self.root, path.lstrip("/"))

    def ok(self, args: Sequence[str], cwd: Optional[str] = None) -> Optional[str]:
        try:
            return self.run(list(args), cwd=cwd)
        except CommandError:
            return None

    # --- receipt ---------------------------------------------------------------
    def read_receipt(self) -> Dict[str, Any]:
        try:
            with open(self.at(RECEIPT), encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            return {"schema": 1, "items": {}}

    def write_receipt(self, receipt: Dict[str, Any]) -> None:
        path = self.at(RECEIPT)
        if not receipt["items"]:
            if os.path.exists(path):
                os.remove(path)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(receipt, handle, indent=1, sort_keys=True)
        os.replace(path + ".tmp", path)

    # --- observation -----------------------------------------------------------
    def fingerprint(self, item: Dict[str, Any]) -> Optional[str]:
        """What the item is now (None if absent): content hash, mode, or git revision."""
        kind, path = item["kind"], self.at(item.get("path", "/"))
        if kind in ("user", "group"):
            found = self.ok(["getent", "passwd" if kind == "user" else "group", item["name"]])
            return None if found is None else "%s:%s" % (kind, item["name"])
        if kind == "file":
            if not os.path.isfile(path):
                return None
            with open(path, encoding="utf-8") as handle:
                return "sha256:%s:%o" % (sha(handle.read()), os.stat(path).st_mode & 0o7777)
        if kind == "dir":
            return "dir:%o" % (os.stat(path).st_mode & 0o7777) if os.path.isdir(path) else None
        if not os.path.isdir(os.path.join(path, ".git")):
            return None
        rev = (self.ok(["git", "-C", path, "rev-parse", "HEAD"]) or "unknown").strip()
        dirty = (self.ok(["git", "-C", path, "status", "--porcelain"]) or "").strip()
        return "git:" + rev + (":dirty" if dirty else "")

    def states(self, items: List[Dict[str, Any]], receipt: Dict[str, Any]) -> Dict[str, str]:
        """missing / ok (ours, unchanged) / modified (ours, changed) / foreign (exists, not ours)."""
        result = {}
        for item in items:
            now, recorded = self.fingerprint(item), receipt["items"].get(item["key"])
            result[item["key"]] = ("missing" if now is None else "foreign" if recorded is None
                                   else "ok" if now == recorded else "modified")
        return result

    # --- install ---------------------------------------------------------------
    def install(self, items: List[Dict[str, Any]], dry_run: bool = False) -> int:
        receipt = self.read_receipt()
        states = self.states(items, receipt)
        todo = [i for i in items if states[i["key"]] == "missing"]
        self.out("plan:")
        for item in items:
            verb = "create" if states[item["key"]] == "missing" else "keep (%s)" % states[item["key"]]
            self.out("  %-10s %s" % (verb, item["key"]))
        for item in items:
            if states[item["key"]] in ("modified", "foreign"):
                self.out("diagnostic: %s is %s: left as is" % (item["key"], states[item["key"]]))
        if dry_run:
            self.out("dry-run: nothing changed")
            return 0
        if not todo:
            self.out("nothing to do")
            return 0
        for item in todo:
            self.create(item)
            receipt["items"][item["key"]] = self.fingerprint(item)
            self.write_receipt(receipt)  # record each step: a failure keeps what was done
        if any(i["kind"] == "file" and i["path"].startswith("/etc/systemd") for i in todo):
            self.run(["systemctl", "daemon-reload"])
            self.run(["systemctl", "enable", "--now", os.path.basename(TIMER)])
        self.out("installed %d item(s)" % len(todo))
        return 0

    def create(self, item: Dict[str, Any]) -> None:
        kind, path = item["kind"], self.at(item.get("path", "/"))
        if kind == "group":
            self.run(["groupadd", item["name"]])
        elif kind == "user":
            self.run(["useradd", "--create-home", "--shell", "/bin/bash", "--groups", GROUP, item["name"]])
        elif kind == "dir":
            self.run(["install", "-d", "-o", RUNNER, "-g", GROUP, "-m", "%o" % item["mode"], path])
        elif kind == "checkout":
            self.run(["git", "clone", item["url"], path])
            self.run(["git", "-C", path, "checkout", "--detach", item["ref"]])
        else:
            if item["path"] == SUDOERS:
                self.run(["visudo", "-cf", "-"], input=item["text"])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(item["text"])
            os.chmod(path, item["mode"])

    # --- status ----------------------------------------------------------------
    def status(self, items: List[Dict[str, Any]]) -> int:
        receipt = self.read_receipt()
        states = self.states(items, receipt)
        for item in items:
            self.out("%-10s %s" % (states[item["key"]], item["key"]))
        for key in sorted(set(receipt["items"]) - {i["key"] for i in items}):
            self.out("diagnostic: receipt lists unknown item %s" % key)
        self.diagnose_credentials()
        return 0 if all(s == "ok" for s in states.values()) else 1

    def diagnose_credentials(self) -> None:
        """Only reports; GitHub credentials and the Claude login are done by a person (D2)."""
        if self.ok(["sudo", "-n", "-u", RUNNER, "-H", "--", "gh", "auth", "status"]) is None:
            self.out("diagnostic: GitHub credentials: not set for %s (log in yourself: gh auth login)" % RUNNER)
        else:
            self.out("diagnostic: GitHub credentials: set for %s" % RUNNER)
        path = self.at("/home/%s/.claude/.credentials.json" % AGENT)
        try:
            with open(path, encoding="utf-8") as handle:
                expires = (json.load(handle).get("claudeAiOauth") or {}).get("expiresAt")
        except (OSError, ValueError, AttributeError):
            self.out("diagnostic: Claude login: not set for %s (run /login yourself)" % AGENT)
            return
        if isinstance(expires, (int, float)) and expires / 1000 < time.time():
            self.out("diagnostic: Claude login: expired (run /login again)")
        else:
            self.out("diagnostic: Claude login: set for %s" % AGENT)

    # --- uninstall -------------------------------------------------------------
    def uninstall(self, items: List[Dict[str, Any]], purge: bool = False, dry_run: bool = False) -> int:
        receipt = self.read_receipt()
        states = self.states(items, receipt)
        if purge:
            self.out("warning: --purge deletes the users' home directories, including the Claude "
                     "login that a person made, and everything under %s" % SRV)
        removed_units = False
        for item in reversed(items):
            key, state = item["key"], states[item["key"]]
            if state == "missing":
                receipt["items"].pop(key, None)
            elif state in ("foreign", "modified"):
                self.out("diagnostic: %s is %s (%s): not removed" % (key, state, "changed after install"
                         if state == "modified" else "not in the receipt"))
            elif item["kind"] in ("user", "group", "dir") and not purge:
                self.out("keep: %s (use --purge to remove)" % key)
            else:
                self.out("%s: %s" % ("would remove" if dry_run else "remove", key))
                if dry_run:
                    continue
                try:
                    self.remove(item)
                except (CommandError, OSError) as error:
                    self.out("diagnostic: %s could not be removed: %s" % (key, error))
                    continue
                receipt["items"].pop(key, None)
                removed_units = removed_units or item.get("path") in (SERVICE, TIMER)
        if dry_run:
            self.out("dry-run: nothing changed")
            return 0
        if removed_units:
            self.run(["systemctl", "daemon-reload"])
        self.write_receipt(receipt)  # kept items stay listed, so that a later --purge still knows them
        return 0

    def remove(self, item: Dict[str, Any]) -> None:
        kind, path = item["kind"], self.at(item.get("path", "/"))
        if item.get("path") == TIMER:
            self.ok(["systemctl", "disable", "--now", os.path.basename(TIMER)])
        if kind == "user":
            self.run(["userdel", "--remove", item["name"]])
        elif kind == "group":
            self.run(["groupdel", item["name"]])
        elif kind == "file":
            os.remove(path)
        elif kind == "checkout" or item["key"] != "dir:" + SRV:
            shutil.rmtree(path)
        else:
            os.rmdir(path)  # /srv/kagari itself: only when nothing else is left in it


def add_parser(sub: Any) -> None:
    for name, text in (("install", "install the host (idempotent)"), ("status", "show the host state"),
                       ("uninstall", "remove what install installed and is unchanged")):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("--root", default="/", help="root path (testing only)")
        if name != "status":
            cmd.add_argument("--dry-run", action="store_true", help="print the plan; change nothing")
        if name == "install":
            cmd.add_argument("--kumiki-url", default=KUMIKI_URL)
            cmd.add_argument("--kumiki-ref", default="main", help="version to pin (recorded in the receipt)")
        if name == "uninstall":
            cmd.add_argument("--purge", action="store_true", help="also remove users, group and /srv/kagari")


def main(args: argparse.Namespace, run: Runner = subprocess_runner) -> int:
    host = Host(args.root, run)
    items = owned(getattr(args, "kumiki_url", KUMIKI_URL), getattr(args, "kumiki_ref", "main"))
    if args.command == "install":
        return host.install(items, args.dry_run)
    if args.command == "status":
        return host.status(items)
    return host.uninstall(items, args.purge, args.dry_run)


if __name__ == "__main__":
    sys.exit("run: kagari_mvi.py install|status|uninstall")
