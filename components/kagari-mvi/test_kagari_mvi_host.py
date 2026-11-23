import contextlib
import io
import json
import os
import subprocess
import tempfile
import time
import unittest

import kagari_mvi as mvi
import kagari_mvi_host as host
from kagari_mvi_exec import CommandError


class FakeSystem:
    """Stands in for useradd, systemctl and the rest; keeps users and groups in memory."""

    def __init__(self, root, gh_ok=False):
        self.root, self.gh_ok = root, gh_ok
        self.users, self.groups, self.calls = set(), set(), []
        self.enabled, self.fail_on, self.visudo = False, None, []  # fail_on: (command, last argument)

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
        if name == "systemctl" and args[1] == "is-enabled":
            if not self.enabled:
                raise CommandError(args, 1, "disabled")
            return ""
        if name == "visudo":  # was the file already live when it was checked?
            self.visudo.append((args[-1], os.path.exists(os.path.join(self.root, "etc/sudoers.d/kagari-mvi"))))
        if self.fail_on and self.fail_on == (name, last):
            raise CommandError(args, 1, "injected failure")
        self.calls.append(args)
        if name == "systemctl" and args[1] == "enable":
            self.enabled = True
        elif name == "systemctl" and args[1] == "disable":
            self.enabled = False
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

    def read(self, target):
        with open(self.path(target)) as handle:
            return handle.read()

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
        calls, receipt = list(self.sys.calls), self.read(host.RECEIPT)
        stamp = os.stat(self.path(host.RECEIPT)).st_mtime_ns
        self.lines.clear()
        self.host.install()
        self.assertEqual(self.sys.calls, calls)
        self.assertEqual(self.read(host.RECEIPT), receipt)
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

    def test_a_failure_halfway_keeps_the_receipt_of_what_was_created(self):
        self.sys.fail_on = ("useradd", host.AGENT)
        with self.assertRaises(CommandError):
            self.host.install()
        recorded = self.host.load()
        self.assertEqual(sorted(recorded), ["group:" + host.GROUP, "user:" + host.RUNNER])
        self.sys.fail_on = None
        self.lines.clear()
        self.host.install()  # the same command finishes the job, without uninstalling first
        self.assertEqual(sorted(self.host.load()), sorted(host.IDS))
        self.assertFalse(any("not in the receipt" in line for line in self.lines))

    def test_a_failed_timer_enable_is_retried_by_the_next_install(self):
        self.sys.fail_on = ("systemctl", "kagari-mvi.timer")
        with self.assertRaises(CommandError):
            self.host.install()
        self.assertFalse(self.sys.enabled)
        self.sys.fail_on = None
        before = len(self.sys.calls)
        self.lines.clear()
        self.host.install()
        self.assertTrue(self.sys.enabled)
        self.assertEqual([c[1] for c in self.sys.calls[before:]], ["daemon-reload", "enable"])
        self.assertIn("  enable kagari-mvi.timer", self.lines)
        self.assertEqual(sorted(self.host.load()), sorted(host.IDS))

    def test_sudoers_holds_only_the_runner_to_agent_rule_and_is_checked_before_it_is_live(self):
        self.host.install()
        with open(self.path(host.SUDOERS)) as handle:
            self.assertEqual(handle.read(), "kagari-runner ALL=(kagari-agent) NOPASSWD: ALL\n")
        self.assertEqual(os.stat(self.path(host.SUDOERS)).st_mode & 0o7777, 0o440)
        checked, live = self.sys.visudo[0]
        self.assertTrue(checked.endswith(".new"))  # sudoers.d ignores a name with a dot
        self.assertFalse(live)

    def test_a_sudoers_file_that_fails_the_check_is_never_installed(self):
        self.sys.fail_on = ("visudo", self.path(host.SUDOERS) + ".new")
        with self.assertRaises(CommandError):
            self.host.install()
        self.assertFalse(os.path.exists(self.path(host.SUDOERS)))
        self.assertFalse(os.path.exists(self.path(host.SUDOERS) + ".new"))
        self.assertNotIn("sudoers:" + host.SUDOERS, self.host.load())

    def run_service(self, targets, codes):
        """Run the generated ExecStart body with `sh`; `codes` maps a command to its exit code."""
        text = host.unit_texts()[host.SERVICE]
        body = text.split("ExecStart=/bin/sh -c '", 1)[1].rsplit("'\n", 1)[0].replace("$$", "$")
        log, fake = os.path.join(self.root, "calls.log"), os.path.join(self.root, "fake.sh")
        listing = os.path.join(self.root, "targets")
        if os.path.exists(log):
            os.remove(log)  # one run, one log
        with open(fake, "w") as handle:  # records its arguments, exits with the code set for the command
            handle.write('#!/bin/sh\necho "$*" >> %s\ncase "$1" in\n%s\n*) exit 0;;\nesac\n' % (
                log, "\n".join("%s) exit %d;;" % (name, code) for name, code in codes.items())))
        os.chmod(fake, 0o755)
        with open(listing, "w") as handle:
            handle.write(targets)
        body = body.replace("/usr/bin/python3 %s/components/kagari-mvi/kagari_mvi.py" % host.CHECKOUT, fake)
        body = body.replace(host.TARGETS, listing)
        code = subprocess.run(["sh", "-c", body]).returncode
        try:
            with open(log) as handle:
                calls = handle.read().splitlines()
        except FileNotFoundError:
            calls = []
        return code, calls

    def test_the_service_calls_the_three_entries_for_every_target_and_keeps_a_failure_visible(self):
        code, calls = self.run_service("a/x alice\nb/y bob", {})  # the last line has no newline
        self.assertEqual(code, 0)
        self.assertEqual([c.split()[0] for c in calls], ["run-once", "review-once", "cleanup-once"] * 2)
        self.assertEqual(calls[0], "run-once --repo a/x --workdir /srv/kagari --allow-actor alice")
        self.assertEqual(calls[4], "review-once --repo b/y --workdir /srv/kagari")
        for entry in ("run-once", "review-once", "cleanup-once"):  # usage limit (3): not a failure, nothing skipped
            code, calls = self.run_service("a/x alice\nb/y bob\n", {entry: 3})
            self.assertEqual((code, len(calls)), (0, 6), entry)
        for entry in ("run-once", "review-once", "cleanup-once"):  # a failure anywhere fails the service
            code, calls = self.run_service("a/x alice\nb/y bob\n", {entry: 1})
            self.assertEqual((code, len(calls)), (1, 6), entry)

    def test_the_service_skips_comments_and_blank_lines_and_a_missing_list_is_not_run(self):
        code, calls = self.run_service("# a comment\n\na/x alice\n", {})
        self.assertEqual((code, len(calls)), (0, 3))

    def test_the_unit_texts_follow_the_readme_example(self):
        texts = host.unit_texts()
        service, timer = texts[host.SERVICE], texts[host.TIMER]
        self.assertIn("TimeoutStartSec=3h", service)
        self.assertIn("User=%s" % host.RUNNER, service)
        self.assertIn("ConditionPathExists=%s" % host.TARGETS, service)
        self.assertIn("OnBootSec=5min", timer)
        self.assertIn("OnUnitInactiveSec=10min", timer)
        self.assertNotIn("OnUnitActiveSec", timer)
        self.assertIn("WantedBy=timers.target", timer)

    def test_a_half_made_checkout_is_removed_so_that_the_next_install_makes_it_whole(self):
        self.sys.fail_on = ("git", "main")  # the `checkout --detach main` step
        with self.assertRaises(CommandError):
            self.host.install()
        self.assertFalse(os.path.exists(self.path(host.CHECKOUT)))
        self.assertNotIn("checkout:" + host.CHECKOUT, self.host.load())
        self.sys.fail_on = None
        self.host.install()
        self.assertEqual(self.host.state("checkout:" + host.CHECKOUT, self.host.load()), "ok")

    def test_a_half_made_directory_is_removed_so_that_the_next_install_makes_it_whole(self):
        self.sys.fail_on = ("chown", self.path("/srv/kagari/wt"))
        with self.assertRaises(CommandError):
            self.host.install()
        self.assertFalse(os.path.exists(self.path("/srv/kagari/wt")))
        self.sys.fail_on = None
        self.host.install()
        self.assertEqual(os.stat(self.path("/srv/kagari/wt")).st_mode & 0o7777, 0o3770)

    def test_uninstall_stopping_half_way_still_keeps_the_receipt_in_step(self):
        self.host.install()
        self.sys.fail_on = ("userdel", host.AGENT)  # purge reaches the users last
        with self.assertRaises(CommandError):
            self.host.uninstall(purge=True)
        recorded = self.host.load()
        self.assertNotIn("checkout:" + host.CHECKOUT, recorded)  # gone before the failure: not recorded
        self.assertNotIn("unit:" + host.TIMER, recorded)
        self.assertIn("user:" + host.AGENT, recorded)  # still there: still recorded
        self.assertFalse(os.path.exists(self.path(host.CHECKOUT)))

    def test_root_is_not_a_command_line_option_and_a_revision_cannot_look_like_an_option(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            mvi.parse_args(["install", "--root", "/tmp/elsewhere"])
        with self.assertRaises(ValueError):
            host.Host(self.root, kumiki_ref="--upload-pack=x")


AGENT = host.AGENT

if __name__ == "__main__":
    unittest.main()
