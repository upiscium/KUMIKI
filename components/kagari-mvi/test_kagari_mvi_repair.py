"""repair / reinstall / --adopt (ADR-0012 D3, D4; Issue #502). Uses the fakes of test_kagari_mvi_host."""
import os
import unittest

import kagari_mvi_host as host
from test_kagari_mvi_host import HostBase


class RepairTest(HostBase):
    def test_repair_restores_a_missing_item_and_diagnoses_a_modified_one(self):
        self.host.install()
        os.remove(self.path(host.SERVICE))
        self.edit_sudoers("# by hand\n")
        edited = self.read(host.SUDOERS)
        self.lines.clear()
        self.assertEqual(self.host.repair(), 1)  # the changed sudoers needs confirmation
        self.assertEqual(self.read(host.SERVICE), self.host.texts[host.SERVICE])
        self.assertEqual(self.read(host.SUDOERS), edited)  # not put back
        self.assertTrue(any("sudoers" in l and "changed" in l for l in self.lines))
        self.assertEqual(self.host.state("unit:" + host.SERVICE, self.host.load()), "ok")
        self.assertEqual(self.host.state("sudoers:" + host.SUDOERS, self.host.load()), "modified")

    def test_repair_restores_a_missing_directory_and_user_without_uninstall(self):
        self.host.install()
        os.rmdir(self.path("/srv/kagari/logs"))
        self.sys.users.discard(host.AGENT)
        self.assertEqual(self.host.repair(), 0)
        self.assertTrue(os.path.isdir(self.path("/srv/kagari/logs")))
        self.assertIn(host.AGENT, self.sys.users)
        self.assertFalse(any(c[0] in ("userdel", "groupdel") for c in self.sys.calls))

    def test_repair_of_a_healthy_host_changes_nothing(self):
        self.host.install()
        calls, receipt = list(self.sys.calls), self.read(host.RECEIPT)
        self.assertEqual(self.host.repair(), 0)
        self.assertEqual((self.sys.calls, self.read(host.RECEIPT)), (calls, receipt))


class AdoptTest(HostBase):
    def existing_host(self):
        """A host made by hand: everything is there, there is no receipt."""
        self.host.install()
        os.remove(self.path(host.RECEIPT))

    def test_status_shows_the_difference_on_a_host_without_a_receipt(self):
        self.existing_host()
        self.edit_sudoers("# by hand\n")
        self.lines.clear()
        self.host.status()
        self.assertIn("unrecorded unit:" + host.SERVICE, self.lines)
        self.assertTrue(any(l.startswith("diagnostic: unit:" + host.SERVICE) and "--adopt" in l for l in self.lines))
        self.assertTrue(any(l.startswith("diagnostic: sudoers:") and "not adoptable" in l for l in self.lines))

    def test_adopt_records_only_what_matches_and_touches_nothing_else(self):
        self.existing_host()
        self.edit_sudoers("# by hand\n")
        edited, calls = self.read(host.SUDOERS), list(self.sys.calls)
        self.host.install(adopt=True)
        receipt = self.host.load()
        self.assertNotIn("sudoers:" + host.SUDOERS, receipt)
        for item in host.IDS:
            if not item.startswith("sudoers:"):
                self.assertIn(item, receipt)
        self.assertEqual(self.read(host.SUDOERS), edited)
        self.assertEqual(self.sys.calls, calls)  # nothing was created or changed
        self.assertTrue(any("not adopted" in l and "sudoers" in l for l in self.lines))

    def test_without_adopt_nothing_is_recorded(self):
        self.existing_host()
        self.host.install()
        self.assertEqual(self.host.load(), {})

    def test_a_dirty_checkout_and_a_wrong_mode_are_not_adopted(self):
        self.existing_host()
        self.sys.dirty = " M file\n"
        os.chmod(self.path("/srv/kagari/logs"), 0o755)
        self.host.install(adopt=True)
        receipt = self.host.load()
        self.assertNotIn("checkout:" + host.CHECKOUT, receipt)
        self.assertNotIn("dir:/srv/kagari/logs", receipt)
        self.assertIn("dir:/srv/kagari", receipt)


class ReinstallTest(HostBase):
    def test_reinstall_keeps_logs_work_and_login(self):
        self.host.install()
        files = {"/srv/kagari/logs/run.log": "log", "/srv/kagari/wt/job/x": "work",
                 "/home/%s/.claude/.credentials.json" % host.AGENT: "{}"}
        for name, text in files.items():
            os.makedirs(os.path.dirname(self.path(name)), exist_ok=True)
            with open(self.path(name), "w") as handle:
                handle.write(text)
        self.assertEqual(self.host.reinstall(), 0)
        for name, text in files.items():
            self.assertEqual(self.read(name), text)
        self.assertEqual(self.sys.users, {host.RUNNER, host.AGENT})
        self.assertEqual(sorted(self.host.load()), sorted(host.IDS))
        self.assertTrue(os.path.exists(self.path(host.SERVICE)))
        self.assertTrue(os.path.isdir(os.path.join(self.path(host.CHECKOUT), ".git")))


if __name__ == "__main__":
    unittest.main()
