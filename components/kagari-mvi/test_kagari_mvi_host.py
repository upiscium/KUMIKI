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
        self.uids, self.dirty, self.no_gh = {}, "", False
        self.claude_help = "Usage: claude [--restricted]"
        self.claude_calls, self.git_fails = [], False
        self.owners, self.remote, self.service_state = {}, host.KUMIKI_URL, "inactive"

    def __call__(self, args, cwd=None, input=None, timeout=None):
        args = list(args)
        name, last = args[0], args[-1]
        if name == "getent":
            if last not in (self.users if args[1] == "passwd" else self.groups):
                raise CommandError(args, 2, "")
            uid = self.uids.setdefault(last, 1000 + len(self.uids))
            return "%s:x:%d:%d:\n" % (last, uid, uid) if args[1] == "passwd" else "%s:x:%d:\n" % (last, uid)
        if name == "gh" and self.no_gh:
            raise CommandError(args, 127, "command not found")
        if name == "gh":
            return ""
        if name == "sudo" and any("claude" in arg for arg in args):
            self.claude_calls.append((args, timeout))
            return self.claude_help
        if name == "sudo":  # gh auth status
            if not self.gh_ok:
                raise CommandError(args, 1, "not logged in")
            return ""
        if name == "stat":  # owner:group of a path, as chown left it
            return self.owners.get(last, "root:root") + "\n"
        if name == "git" and "get-url" in args:
            return self.remote + "\n"
        if name == "systemctl" and args[1] == "show":
            return self.service_state + "\n"
        if name == "git" and self.git_fails and ("rev-parse" in args or "status" in args):
            raise CommandError(args, 128, "fatal: detected dubious ownership")
        if name == "git" and "rev-parse" in args:
            return "abc123\n"
        if name == "git" and "status" in args:
            return self.dirty
        if name == "systemctl" and args[1] == "is-enabled":
            if not self.enabled:
                raise CommandError(args, 1, "disabled")
            return ""
        if name == "visudo":  # was the file already live when it was checked?
            self.visudo.append((args[-1], os.path.exists(os.path.join(self.root, "etc/sudoers.d/kagari-mvi"))))
        if self.fail_on and self.fail_on == (name, last):
            raise CommandError(args, 1, "injected failure")
        self.calls.append(args)
        if name == "chown":  # a chown may clear setgid: only a chmod after it keeps the mode
            os.chmod(last, os.stat(last).st_mode & 0o777)
            self.owners[last] = args[-2]
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
            with open(os.path.join(last, "README"), "w") as handle:  # a regular file next to .git
                handle.write("x")
        return ""


class HostBase(unittest.TestCase):
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


class HostTest(HostBase):
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

    def login_lines(self, **dates):
        path = self.path("/home/%s/.claude/.credentials.json" % host.AGENT)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            json.dump({"claudeAiOauth": {k: (time.time() + d * 86400) * 1000 for k, d in dates.items()}}, handle)
        self.lines.clear()
        self.host.status()
        return [line for line in self.lines if "Claude login" in line]

    def test_login_is_judged_by_the_refresh_token_expiry(self):
        got = self.login_lines(expiresAt=-0.2, refreshTokenExpiresAt=15)
        self.assertEqual(len(got), 1)
        self.assertIn("valid until", got[0])
        self.assertNotIn("expired", got[0])
        self.assertRegex(got[0], r"\d{4}-\d\d-\d\d \d\d:\d\d UTC")

    def test_login_near_expiry_warns(self):
        got = self.login_lines(expiresAt=1, refreshTokenExpiresAt=3.5)
        self.assertTrue(got and "warning" in got[0] and "expires in 3 days" in got[0])

    def test_login_past_refresh_expiry_is_expired(self):
        got = self.login_lines(expiresAt=1, refreshTokenExpiresAt=-1)
        self.assertTrue(got and "expired" in got[0])

    def test_login_without_refresh_expiry_uses_the_access_expiry(self):
        self.assertIn("expired", self.login_lines(expiresAt=-1)[0])
        self.assertIn("valid until", self.login_lines(expiresAt=20)[0])

    def test_broken_credentials_file_is_not_set(self):
        path = self.path("/home/%s/.claude/.credentials.json" % host.AGENT)
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as handle:
            handle.write("{broken")
        self.lines.clear()
        self.host.status()
        self.assertTrue(any("Claude login" in line and "not set" in line for line in self.lines))

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
            handle.write('#!/bin/sh\ncat > /dev/null\necho "$*" >> %s\ncase "$1" in\n%s\n*) exit 0;;\nesac\n' % (
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

    def test_the_service_skips_comments_and_blank_lines(self):
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

    def test_a_directory_that_was_already_there_is_never_removed_by_a_failed_checkout(self):
        os.makedirs(self.path(host.CHECKOUT))
        keep = os.path.join(self.path(host.CHECKOUT), "precious.txt")
        with open(keep, "w") as handle:
            handle.write("not ours")
        with self.assertRaises(ValueError):
            self.host.install()
        self.assertTrue(os.path.exists(keep))
        self.assertNotIn("checkout:" + host.CHECKOUT, self.host.load())

    def test_an_empty_directory_that_was_already_there_stays_after_a_failed_checkout(self):
        os.makedirs(self.path(host.CHECKOUT))
        self.sys.fail_on = ("git", "main")
        with self.assertRaises(CommandError):
            self.host.install()
        self.assertTrue(os.path.isdir(self.path(host.CHECKOUT)))
        self.assertEqual(os.listdir(self.path(host.CHECKOUT)), [])

    def test_a_service_file_removed_before_a_failure_is_not_forgotten_by_the_next_uninstall(self):
        self.host.install()
        self.sys.fail_on = ("systemctl", "daemon-reload")  # after the service file is gone
        with self.assertRaises(CommandError):
            self.host.uninstall()
        self.assertFalse(os.path.exists(self.path(host.SERVICE)))
        self.sys.fail_on = None
        self.lines.clear()
        self.host.uninstall()
        self.assertIn("already gone: unit:" + host.SERVICE, self.lines)
        self.assertNotIn("unit:" + host.SERVICE, self.host.load())

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


    def test_a_receipt_from_before_the_uid_fingerprint_still_matches_and_purge_still_works(self):
        self.host.install()
        receipt = self.host.load()
        for item in receipt:
            if item.split(":", 1)[0] in ("user", "group"):
                receipt[item] = "present"  # what #500 wrote
        self.host.save(receipt)
        self.assertEqual(self.host.state("user:" + host.AGENT, receipt), "ok")
        self.assertEqual(self.host.state("group:" + host.GROUP, receipt), "ok")
        self.host.uninstall(purge=True)
        self.assertEqual((self.sys.users, self.sys.groups), (set(), set()))

    def test_a_failed_checkout_leaves_no_regular_file_in_a_directory_that_was_empty_before(self):
        os.makedirs(self.path(host.CHECKOUT))
        self.sys.fail_on = ("git", "main")  # the clone made .git and a README, then the checkout failed
        with self.assertRaises(CommandError):
            self.host.install()
        self.assertEqual(os.listdir(self.path(host.CHECKOUT)), [])
        self.sys.fail_on = None
        self.host.install()  # no `conflict`: the next install makes the checkout whole
        self.assertEqual(self.host.state("checkout:" + host.CHECKOUT, self.host.load()), "ok")

    def test_a_failing_git_is_not_read_as_a_clean_checkout_and_does_not_stop_the_others(self):
        self.host.install()
        self.sys.git_fails = True
        self.lines.clear()
        self.assertEqual(self.host.status(), 0)  # no traceback
        self.assertTrue(any(line.startswith("unreadable") and host.CHECKOUT in line for line in self.lines))
        self.lines.clear()
        self.host.uninstall(purge=True)
        self.assertTrue(os.path.isdir(self.path(host.CHECKOUT)))  # left as is
        self.assertTrue(any("checkout:" + host.CHECKOUT in line and "left as is" in line for line in self.lines))
        self.assertEqual((self.sys.users, self.sys.groups), (set(), set()))  # the items after it went on
        self.assertFalse(os.path.exists(self.path("/srv/kagari")))

    def test_status_asks_claude_through_a_login_shell_with_a_time_limit(self):
        self.host.status()
        args, timeout = self.sys.claude_calls[-1]
        self.assertEqual(args[-3:], ["sh", "-lc", "claude --help"])
        self.assertIn(host.AGENT, args)
        self.assertEqual(timeout, 60)

    def test_a_checkout_whose_state_could_not_be_read_at_install_is_never_removed_later(self):
        self.sys.git_fails = True  # git fails right after the clone: "unreadable" is what the receipt would hold
        self.host.install()
        self.assertEqual(self.host.state("checkout:" + host.CHECKOUT, self.host.load()), "unreadable")
        self.sys.dirty = " M file"  # even when git works again, and there is an uncommitted edit
        self.sys.git_fails = False
        self.host.uninstall(purge=True)
        self.assertTrue(os.path.isdir(self.path(host.CHECKOUT)))


AGENT = host.AGENT

class HardeningTest(HostBase):
    """#508: fingerprints and clean-up that do not trust names alone."""

    def test_uninstall_leaves_a_checkout_with_uncommitted_edits_and_says_so(self):
        self.host.install()
        self.sys.dirty = " M components/kagari-mvi/kagari_mvi.py\n"
        self.lines.clear()
        self.host.uninstall()
        self.assertTrue(any("was changed after install" in line and host.CHECKOUT in line for line in self.lines))
        self.assertIn("checkout:" + host.CHECKOUT, self.host.load())
        self.assertNotIn("removed: checkout:" + host.CHECKOUT, self.lines)

    def test_purge_does_not_remove_a_user_recreated_under_the_same_name(self):
        self.host.install()
        self.sys.uids[host.AGENT] = 5555  # same name, another uid
        self.lines.clear()
        self.host.uninstall(purge=True)
        self.assertTrue(any("was changed after install" in line and host.AGENT in line for line in self.lines))
        self.assertIn(host.AGENT, self.sys.users)
        self.assertNotIn("removed: user:" + host.AGENT, self.lines)
        self.assertIn("removed: user:" + host.RUNNER, self.lines)

    def test_create_refuses_and_remove_does_not_follow_a_symbolic_link(self):
        os.makedirs(self.path("/srv"))
        os.makedirs(self.path("/elsewhere"))
        os.symlink(self.path("/elsewhere"), self.path("/srv/kagari"))
        with self.assertRaises(ValueError):
            self.host.create("dir:/srv/kagari")
        with self.assertRaises(ValueError):
            self.host.remove("dir:/srv/kagari", True)
        self.assertTrue(os.path.isdir(self.path("/elsewhere")))
        os.makedirs(self.path("/opt/kagari"))
        os.symlink(self.path("/elsewhere"), self.path(host.CHECKOUT))
        with self.assertRaises(ValueError):
            self.host.create("checkout:" + host.CHECKOUT)
        with self.assertRaises(ValueError):
            self.host.remove("checkout:" + host.CHECKOUT, True)
        self.assertEqual(self.sys.calls, [])

    def test_uninstall_leaves_a_symbolic_link_in_place_of_the_checkout(self):
        self.host.install()
        os.makedirs(self.path("/elsewhere/.git"))
        real = self.path(host.CHECKOUT)
        os.rename(real, real + ".moved")
        os.symlink(self.path("/elsewhere"), real)
        self.lines.clear()
        self.host.uninstall()
        self.assertTrue(any("symbolic link" in line for line in self.lines))
        self.assertTrue(os.path.isdir(self.path("/elsewhere/.git")))

    def test_dry_run_shows_a_conflict_at_the_checkout_place(self):
        os.makedirs(self.path(host.CHECKOUT))
        with open(os.path.join(self.path(host.CHECKOUT), "x.txt"), "w") as handle:
            handle.write("x")
        self.host.install(dry_run=True)
        self.assertTrue(any(line.startswith("  conflict checkout:") for line in self.lines))
        self.assertNotIn("  create checkout:" + host.CHECKOUT, self.lines)

    def test_a_failed_checkout_keeps_the_owner_and_mode_of_an_empty_directory(self):
        path = self.path(host.CHECKOUT)
        os.makedirs(path)
        os.chmod(path, 0o2750)
        before = os.stat(path)
        self.sys.fail_on = ("git", "main")
        with self.assertRaises(CommandError):
            self.host.install()
        after = os.stat(path)
        self.assertEqual((after.st_ino, after.st_uid, after.st_gid, after.st_mode),
                         (before.st_ino, before.st_uid, before.st_gid, before.st_mode))
        self.assertEqual(os.listdir(path), [])

    def test_an_error_in_clean_up_does_not_hide_the_original_error(self):
        self.sys.fail_on = ("git", "main")
        original = host.shutil.rmtree
        host.shutil.rmtree = lambda *a, **k: (_ for _ in ()).throw(OSError("clean-up broke"))
        self.addCleanup(setattr, host.shutil, "rmtree", original)
        with self.assertRaises(CommandError) as caught:
            self.host.install()
        self.assertEqual(caught.exception.output, "injected failure")

    def test_a_failing_receipt_save_does_not_hide_the_error_of_uninstall(self):
        self.host.install()
        self.sys.fail_on = ("systemctl", "kagari-mvi.timer")
        self.host.save = lambda items: (_ for _ in ()).throw(OSError("disk full"))
        with self.assertRaises(CommandError):
            self.host.uninstall()

    def test_the_timer_is_disabled_even_when_its_file_was_removed_by_hand(self):
        self.host.install()
        os.remove(self.path(host.TIMER))
        self.sys.calls.clear()
        self.host.uninstall()
        self.assertIn(["systemctl", "disable", "--now", "kagari-mvi.timer"], self.sys.calls)

    def test_status_diagnoses_a_missing_gh_and_a_claude_without_restricted(self):
        self.sys.no_gh, self.sys.claude_help = True, "Usage: claude [--print]"
        self.host.status()
        self.assertTrue(any("gh command: not found" in line for line in self.lines))
        self.assertTrue(any("no --restricted" in line for line in self.lines))
        self.assertEqual(self.sys.calls, [])

    def test_status_does_not_warn_about_a_claude_that_has_restricted(self):
        self.host.status()
        self.assertFalse(any("--restricted" in line for line in self.lines))

    def test_run_once_without_gh_ends_with_a_message_not_a_traceback(self):
        import kagari_mvi_exec as ex
        with tempfile.TemporaryDirectory() as empty:
            old, os.environ["PATH"] = os.environ["PATH"], empty
            try:
                with self.assertRaises(CommandError) as caught:
                    ex.subprocess_runner(["gh", "--version"])
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = mvi.main(["run-once", "--repo", "o/r", "--workdir", empty, "--allow-actor", "a"])
            finally:
                os.environ["PATH"] = old
        self.assertIn("not found", caught.exception.output)
        self.assertEqual(code, 1)
        self.assertIn("not found", err.getvalue())


if __name__ == "__main__":
    unittest.main()
