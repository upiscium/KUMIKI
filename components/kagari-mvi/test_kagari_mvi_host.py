import json
import os
import tempfile
import time
import unittest

import kagari_mvi_host as host
from kagari_mvi_exec import CommandError


class FakeSystem:
    """Stands in for useradd, systemctl and the rest; keeps users and groups in memory."""

    def __init__(self, root, gh_ok=False):
        self.root, self.gh_ok = root, gh_ok
        self.users, self.groups, self.calls = set(), set(), []

    def __call__(self, args, cwd=None, input=None, timeout=None):
        args = list(args)
        name, last = args[0], args[-1]
        if name == "getent":
            if last not in (self.users if args[1] == "passwd" else self.groups):
                raise CommandError(args, 2, "")
            return ""
        if name == "sudo":  # gh auth status
            if not self.gh_ok:
                raise CommandError(args, 1, "not logged in")
            return ""
        if name == "git" and "rev-parse" in args:
            return "abc123\n"
        self.calls.append(args)
        if name == "useradd":
            self.users.add(last)
        elif name == "userdel":
            self.users.discard(last)
        elif name == "groupadd":
            self.groups.add(last)
        elif name == "groupdel":
            self.groups.discard(last)
        elif name == "git" and args[1] == "clone":
            os.makedirs(os.path.join(last, ".git"))
        return ""


class HostTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.sys = FakeSystem(self.root)
        self.lines = []
        self.make()

    def make(self):
        self.host = host.Host(self.root, run=self.sys, out=self.lines.append)

    def path(self, target):
        return self.host.p(target)

    def edit_sudoers(self, text):
        os.chmod(self.path(host.SUDOERS), 0o640)
        with open(self.path(host.SUDOERS), "a") as handle:
            handle.write(text)

    def test_plan_of_an_empty_host_lists_every_owned_thing(self):
        self.assertEqual(self.host.install(dry_run=True), 0)
        for item in host.IDS:
            self.assertIn("  create " + item, self.lines)
        self.assertEqual(self.sys.calls, [])  # dry run changed nothing
        self.assertFalse(os.path.exists(self.path("/srv/kagari")))
        self.assertFalse(os.path.exists(self.path(host.RECEIPT)))

    def test_install_creates_things_and_writes_the_receipt(self):
        self.host.install()
        self.assertEqual(self.sys.users, {host.RUNNER, host.AGENT})
        self.assertEqual(os.stat(self.path("/srv/kagari/wt")).st_mode & 0o7777, 0o3770)
        with open(self.path(host.RECEIPT)) as handle:
            items = json.load(handle)["items"]
        self.assertEqual(sorted(items), sorted(host.IDS))
        self.assertEqual(items["checkout:" + host.CHECKOUT], "rev abc123")

    def test_second_install_changes_nothing(self):
        self.host.install()
        calls, receipt = list(self.sys.calls), open(self.path(host.RECEIPT)).read()
        stamp = os.stat(self.path(host.RECEIPT)).st_mtime_ns
        self.lines.clear()
        self.host.install()
        self.assertEqual(self.sys.calls, calls)
        self.assertEqual(open(self.path(host.RECEIPT)).read(), receipt)
        self.assertEqual(os.stat(self.path(host.RECEIPT)).st_mtime_ns, stamp)
        self.assertEqual(self.lines, ["plan: nothing to do"])

    def test_uninstall_leaves_a_modified_sudoers_and_says_so(self):
        self.host.install()
        self.edit_sudoers("# by hand\n")
        self.lines.clear()
        self.host.uninstall(purge=True)
        self.assertTrue(os.path.exists(self.path(host.SUDOERS)))
        self.assertTrue(any("diagnostic" in line and host.SUDOERS in line and "changed" in line
                            for line in self.lines))
        self.assertFalse(os.path.exists(self.path(host.SERVICE)))  # unchanged things still go

    def test_uninstall_leaves_things_missing_from_the_receipt(self):
        os.makedirs(self.path("/srv/kagari/logs"))
        self.host.uninstall(purge=True)
        self.assertTrue(os.path.isdir(self.path("/srv/kagari/logs")))
        self.assertTrue(any("not in the receipt" in line for line in self.lines))

    def test_uninstall_default_keeps_users_group_and_srv_and_purge_removes_them(self):
        self.host.install()
        with open(self.path("/srv/kagari/logs/a.log"), "w") as handle:
            handle.write("log")
        self.host.uninstall()
        self.assertEqual(self.sys.users, {host.RUNNER, host.AGENT})
        self.assertEqual(self.sys.groups, {host.GROUP})
        self.assertTrue(os.path.exists(self.path("/srv/kagari/logs/a.log")))
        for gone in (host.SUDOERS, host.SERVICE, host.TIMER, host.CHECKOUT):
            self.assertFalse(os.path.exists(self.path(gone)), gone)
        self.assertEqual(self.host.state("user:" + AGENT, self.host.load()), "ok")  # still owned
        self.lines.clear()
        self.host.uninstall(purge=True)
        self.assertEqual((self.sys.users, self.sys.groups), (set(), set()))
        self.assertFalse(os.path.exists(self.path("/srv/kagari")))
        self.assertFalse(os.path.exists(self.path(host.RECEIPT)))
        self.assertTrue(any("login" in line and "deleted" in line for line in self.lines))

    def test_reinstall_after_uninstall_works(self):
        self.host.install()
        self.host.uninstall()
        self.lines.clear()
        self.host.install()
        self.assertTrue(os.path.exists(self.path(host.SUDOERS)))
        self.assertNotIn("  create user:" + host.RUNNER, self.lines)  # kept, so not recreated

    def test_status_diagnoses_missing_credentials_without_installing(self):
        self.host.status()
        self.assertTrue(any("GitHub credential" in line and "not set" in line for line in self.lines))
        self.assertTrue(any("Claude login" in line and "not set" in line for line in self.lines))
        self.assertTrue(any(line.startswith("absent") and host.SUDOERS in line for line in self.lines))
        self.assertEqual(self.sys.calls, [])
        self.assertFalse(os.path.exists(self.path("/srv/kagari")))

    def test_status_shows_receipt_difference_and_expired_login(self):
        self.host.install()
        self.edit_sudoers("x\n")
        path = self.path("/home/%s/.claude/.credentials.json" % host.AGENT)
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as handle:
            json.dump({"claudeAiOauth": {"expiresAt": (time.time() - 10) * 1000}}, handle)
        self.lines.clear()
        self.host.status()
        self.assertTrue(any(line.startswith("modified") and host.SUDOERS in line for line in self.lines))
        self.assertTrue(any("expired" in line for line in self.lines))


AGENT = host.AGENT

if __name__ == "__main__":
    unittest.main()
