"""Tests of the host operations (#500). Nothing touches the real host: the root is a
temporary directory and every command goes to a fake runner."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kagari_mvi_host as hostmod  # noqa: E402
from kagari_mvi_exec import CommandError  # noqa: E402

MUTATING = ("useradd", "groupadd", "install", "systemctl", "userdel", "groupdel", "visudo", "clone")


class FakeSystem:
    """Keeps users, groups and the checkout as state; creates directories under the root."""

    def __init__(self, root, gh_ok=False):
        self.root, self.calls, self.users, self.groups, self.gh_ok = root, [], set(), set(), gh_ok

    def __call__(self, args, cwd=None, input=None, timeout=None):
        self.calls.append(list(args))
        name = args[0]
        if name == "getent":
            if args[2] not in (self.users if args[1] == "passwd" else self.groups):
                raise CommandError(args, 2, "")
        elif name == "groupadd":
            self.groups.add(args[1])
        elif name == "useradd":
            self.users.add(args[-1])
        elif name == "userdel":
            self.users.discard(args[-1])
        elif name == "groupdel":
            self.groups.discard(args[-1])
        elif name == "install":
            path = args[-1]  # the host already put the root in front
            os.makedirs(path, exist_ok=True)
            os.chmod(path, int(args[args.index("-m") + 1], 8))
        elif name == "git" and args[1] == "clone":
            os.makedirs(os.path.join(args[3], ".git"))
        elif name == "git" and "rev-parse" in args:
            return "abc123\n"
        elif name == "sudo" and not self.gh_ok:
            raise CommandError(args, 1, "not logged in")
        return ""

    def mutations(self):
        return [c for c in self.calls if c[0] in MUTATING or c[:2] == ["git", "clone"]]


class HostTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.system = FakeSystem(self.root)
        self.lines = []
        self.host = hostmod.Host(self.root, self.system, self.lines.append)
        self.items = hostmod.owned()

    def path(self, absolute):
        return os.path.join(self.root, absolute.lstrip("/"))

    def keys(self):
        return [i["key"] for i in self.items]

    def test_plan_of_an_empty_host_lists_every_owned_thing(self):
        self.host.install(self.items, dry_run=True)
        text = "\n".join(self.lines)
        for expected in ("group:kagari", "user:kagari-runner", "user:kagari-agent", "sudoers",
                         "dir:/srv/kagari", "dir:/srv/kagari/wt", "dir:/srv/kagari/repos",
                         "dir:/srv/kagari/logs", "checkout", "service", "timer"):
            self.assertIn("create     " + expected, text)
        self.assertEqual(self.system.mutations(), [])  # dry-run changed nothing
        self.assertFalse(os.path.exists(self.path(hostmod.RECEIPT)))

    def test_second_install_changes_nothing(self):
        self.host.install(self.items)
        self.assertTrue(os.path.exists(self.path(hostmod.RECEIPT)))
        self.assertEqual(self.host.read_receipt()["items"]["checkout"], "git:abc123")
        first_calls, first_receipt = len(self.system.calls), self.host.read_receipt()
        self.host.install(self.items)
        self.assertEqual(self.system.mutations(), [c for c in self.system.calls[:first_calls]
                                                   if c[0] in MUTATING or c[:2] == ["git", "clone"]])
        self.assertEqual(self.host.read_receipt(), first_receipt)
        self.assertIn("nothing to do", self.lines)

    def test_modified_sudoers_is_not_removed_but_diagnosed(self):
        self.host.install(self.items)
        sudoers = self.path(hostmod.SUDOERS)
        os.chmod(sudoers, 0o644)
        with open(sudoers, "a", encoding="utf-8") as handle:
            handle.write("# edited by a person\n")
        self.lines.clear()
        self.host.uninstall(self.items)
        self.assertTrue(os.path.exists(sudoers))
        self.assertTrue(any("diagnostic: sudoers is modified" in line for line in self.lines))
        self.assertFalse(os.path.exists(self.path(hostmod.TIMER)))  # unchanged things are removed
        self.assertIn("sudoers", self.host.read_receipt()["items"])

    def test_thing_not_in_the_receipt_is_not_removed(self):
        os.makedirs(os.path.dirname(self.path(hostmod.SUDOERS)))
        with open(self.path(hostmod.SUDOERS), "w", encoding="utf-8") as handle:
            handle.write(hostmod.SUDOERS_TEXT)  # same text, but not installed by us
        self.host.install(self.items)
        self.host.uninstall(self.items)
        self.assertTrue(os.path.exists(self.path(hostmod.SUDOERS)))

    def test_default_uninstall_keeps_users_group_and_srv_and_purge_removes(self):
        self.host.install(self.items)
        self.host.uninstall(self.items)
        self.assertEqual(self.system.users, {"kagari-runner", "kagari-agent"})
        self.assertEqual(self.system.groups, {"kagari"})
        for name in ("", "/wt", "/repos", "/logs"):
            self.assertTrue(os.path.isdir(self.path(hostmod.SRV + name)))
        for gone in (hostmod.SUDOERS, hostmod.SERVICE, hostmod.TIMER, hostmod.CHECKOUT):
            self.assertFalse(os.path.exists(self.path(gone)), gone)
        self.lines.clear()
        self.host.uninstall(self.items, purge=True)
        self.assertEqual((self.system.users, self.system.groups), (set(), set()))
        self.assertFalse(os.path.exists(self.path(hostmod.SRV)))
        self.assertFalse(os.path.exists(self.path(hostmod.RECEIPT)))
        self.assertTrue(any("login" in line and "warning" in line for line in self.lines))

    def test_reinstall_after_default_uninstall_works(self):
        self.host.install(self.items)
        self.host.uninstall(self.items)
        self.host.install(self.items)
        self.assertTrue(all(s == "ok" for s in self.host.states(
            self.items, self.host.read_receipt()).values()))

    def test_status_reports_missing_credentials_and_installs_nothing(self):
        code = self.host.status(self.items)
        text = "\n".join(self.lines)
        self.assertEqual(code, 1)
        self.assertIn("diagnostic: GitHub credentials: not set", text)
        self.assertIn("diagnostic: Claude login: not set", text)
        self.assertEqual(self.system.mutations(), [])
        self.assertFalse(os.path.exists(self.path(hostmod.RECEIPT)))
        self.assertFalse(any(c[:1] == ["sudo"] and "login" in c for c in self.system.calls))

    def test_status_reports_expired_login_and_receipt_difference(self):
        self.host.install(self.items)
        creds = self.path("/home/kagari-agent/.claude/.credentials.json")
        os.makedirs(os.path.dirname(creds))
        with open(creds, "w", encoding="utf-8") as handle:
            json.dump({"claudeAiOauth": {"expiresAt": 1000}}, handle)
        os.chmod(self.path(hostmod.SRV + "/logs"), 0o755)
        self.lines.clear()
        self.assertEqual(self.host.status(self.items), 1)
        text = "\n".join(self.lines)
        self.assertIn("modified   dir:/srv/kagari/logs", text)
        self.assertIn("Claude login: expired", text)


if __name__ == "__main__":
    unittest.main()
