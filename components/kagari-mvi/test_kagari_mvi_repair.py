"""repair / reinstall / --adopt (ADR-0012 D3, D4; Issue #502). Uses the fakes of test_kagari_mvi_host."""
import json
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
            if item.startswith(("sudoers:", "user:", "group:")):  # the edited one; users and groups are never adopted
                self.assertNotIn(item, receipt)
            else:
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

    def test_users_and_a_group_are_never_adopted_so_that_purge_cannot_delete_them(self):
        self.existing_host()
        self.lines.clear()
        self.host.install(adopt=True)
        receipt = self.host.load()
        for item in ("group:" + host.GROUP, "user:" + host.RUNNER, "user:" + host.AGENT):
            self.assertNotIn(item, receipt)
        self.assertTrue(any("never adopted" in line and "user:" + host.AGENT in line for line in self.lines))
        self.host.uninstall(purge=True)
        self.assertFalse(any(call[0] in ("userdel", "groupdel") for call in self.sys.calls))  # nobody was deleted
        self.assertEqual(self.sys.users, {host.RUNNER, host.AGENT})
        self.assertEqual(self.sys.groups, {host.GROUP})

    def test_a_directory_owned_by_someone_else_is_not_adopted_nor_removed_by_purge(self):
        self.existing_host()
        self.sys.owners[self.path("/srv/kagari")] = "root:root"
        self.host.install(adopt=True)
        self.assertNotIn("dir:/srv/kagari", self.host.load())
        self.host.uninstall(purge=True)
        self.assertTrue(os.path.isdir(self.path("/srv/kagari")))

    def test_a_clean_git_directory_that_is_not_kumiki_is_not_adopted_nor_removed(self):
        self.existing_host()
        self.sys.remote = "https://example.com/someone/else.git"
        self.host.install(adopt=True)
        self.assertNotIn("checkout:" + host.CHECKOUT, self.host.load())
        self.host.uninstall()
        self.assertTrue(os.path.isdir(self.path(host.CHECKOUT)))

    def test_a_directory_is_not_removed_by_purge_while_a_child_that_was_not_adopted_is_inside(self):
        self.existing_host()
        os.chmod(self.path("/srv/kagari/logs"), 0o755)  # not what KAGARI makes: not adopted
        self.host.install(adopt=True)
        self.assertNotIn("dir:/srv/kagari/logs", self.host.load())
        self.assertIn("dir:/srv/kagari", self.host.load())  # the parent is adopted
        with open(self.path("/srv/kagari/logs/precious.log"), "w") as handle:
            handle.write("a person's log")
        self.lines.clear()
        self.host.uninstall(purge=True)
        self.assertTrue(os.path.exists(self.path("/srv/kagari/logs/precious.log")))  # still there
        self.assertTrue(os.path.isdir(self.path("/srv/kagari")))
        self.assertTrue(any("/srv/kagari contains" in line and "not removed" in line for line in self.lines))
        self.assertIn("dir:/srv/kagari", self.host.load())  # the parent stays recorded

    def test_adopting_a_checkout_pins_its_real_revision_and_repair_restores_that(self):
        self.existing_host()
        self.host = host.Host(self.root, run=self.sys, out=self.lines.append)  # a new process: it made nothing
        self.host.install(adopt=True)
        self.assertEqual(json.loads(self.read(host.RECEIPT))["kumiki_ref"], "abc123")  # not "main"
        import shutil
        shutil.rmtree(self.path(host.CHECKOUT))
        self.host = host.Host(self.root, run=self.sys, out=self.lines.append)
        self.assertEqual(self.host.repair(), 0)
        revisions = [c[-1] for c in self.sys.calls if c[:2] == ["git", "-C"] and "--detach" in c]
        self.assertEqual(revisions[-1], "abc123")

    def test_adopt_dry_run_records_nothing(self):
        self.existing_host()
        self.lines.clear()
        self.host.install(dry_run=True, adopt=True)
        self.assertEqual(self.host.load(), {})
        self.assertTrue(any(line.strip().startswith("would adopt unit:") for line in self.lines))


class RepairKeepsTest(HostBase):
    def pin(self, ref):
        return json.loads(self.read(host.RECEIPT))["kumiki_ref"]

    def checkout_revisions(self):
        return [c[-1] for c in self.sys.calls if c[:2] == ["git", "-C"] and "--detach" in c]

    def make_with_ref(self, ref):
        self.host = host.Host(self.root, run=self.sys, out=self.lines.append, kumiki_ref=ref)

    def test_repair_does_not_restore_what_is_not_in_the_receipt_or_could_not_be_read(self):
        self.host.install()
        receipt = self.host.load()
        del receipt["sudoers:" + host.SUDOERS]
        self.host.save(receipt)  # the sudoers is there, but not recorded
        self.sys.git_fails = True  # the checkout can not be read
        calls = list(self.sys.calls)
        self.lines.clear()
        self.assertEqual(self.host.repair(), 1)
        self.assertEqual(self.sys.calls, calls)  # nothing was created or changed
        self.assertTrue(any("sudoers" in line and "not in the receipt" in line for line in self.lines))
        self.assertTrue(any("checkout" in line and "could not be read" in line for line in self.lines))

    def test_repair_does_not_turn_on_a_timer_that_a_person_stopped(self):
        self.host.install()
        self.sys.enabled = False  # stopped by hand
        calls = list(self.sys.calls)
        self.assertEqual(self.host.repair(), 0)
        self.assertEqual(self.sys.calls, calls)
        self.assertFalse(self.sys.enabled)

    def test_repair_of_a_hand_made_host_does_not_report_users_and_groups_as_stuck(self):
        self.host.install()
        os.remove(self.path(host.RECEIPT))
        self.host.install(adopt=True)  # the adoptable things are recorded; users and groups are only noted
        self.lines.clear()
        self.assertEqual(self.host.repair(), 0)
        self.assertTrue(any(line.startswith("note: user:") for line in self.lines))

    def test_repair_restores_a_missing_checkout_at_the_pinned_revision_not_at_main(self):
        self.make_with_ref("v1.2")
        self.host.install()
        self.assertEqual(self.pin("v1.2"), "v1.2")
        import shutil
        shutil.rmtree(self.path(host.CHECKOUT))
        self.make_with_ref(None)  # repair has no --kumiki-ref
        self.assertEqual(self.host.repair(), 0)
        self.assertEqual(self.checkout_revisions()[-1], "v1.2")
        self.assertEqual(self.pin("v1.2"), "v1.2")

    def test_reinstall_keeps_the_pin_unless_a_revision_is_given_and_then_moves_it(self):
        self.make_with_ref("v1.2")
        self.host.install()
        self.make_with_ref(None)
        self.assertEqual(self.host.reinstall(), 0)
        self.assertEqual((self.checkout_revisions(), self.pin("v1.2")), (["v1.2", "v1.2"], "v1.2"))
        self.make_with_ref("v2.0")
        self.assertEqual(self.host.reinstall(), 0)
        self.assertEqual(self.checkout_revisions()[-1], "v2.0")
        self.assertEqual(self.pin("v2.0"), "v2.0")

    def test_reinstall_with_a_dirty_checkout_does_not_pretend_it_moved_the_pin(self):
        self.make_with_ref("v1.2")
        self.host.install()
        self.sys.dirty = " M file\n"  # edited by hand: uninstall leaves it, so install can not move it
        self.make_with_ref("v2.0")
        self.lines.clear()
        self.assertEqual(self.host.reinstall(), 1)
        self.assertTrue(os.path.isdir(os.path.join(self.path(host.CHECKOUT), ".git")))
        self.assertEqual(self.pin("v1.2"), "v1.2")  # the receipt still says what the checkout is
        self.assertTrue(any("checkout" in line and "not in order" in line for line in self.lines))

    def test_reinstall_refuses_while_the_service_is_running(self):
        self.host.install()
        for state in ("activating", "active"):
            self.sys.service_state = state
            calls, receipt = list(self.sys.calls), self.read(host.RECEIPT)
            self.lines.clear()
            self.assertEqual(self.host.reinstall(), 1)
            self.assertEqual((self.sys.calls, self.read(host.RECEIPT)), (calls, receipt))
            self.assertTrue(os.path.isdir(self.path(host.CHECKOUT)))
            self.assertTrue(any("refused" in line for line in self.lines))


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
