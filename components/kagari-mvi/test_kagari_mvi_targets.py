import os
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import kagari_mvi as mvi
import kagari_mvi_host as host
import kagari_mvi_targets as targets
from kagari_mvi_exec import CommandError
from kagari_mvi_testkit import FakeRunner


class TargetsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root, self.lines = tmp.name, []
        self.listing = os.path.join(self.root, host.TARGETS.lstrip("/"))
        self.labels = {"GET": [], "POST": None}
        self.make_runner()

    def make_runner(self, local_settings=False, existing=()):
        routes = {("GET", "repos/o/r/labels"): [{"name": n} for n in existing],
                  ("POST", "repos/o/r/labels"): {}}
        self.fake = FakeRunner(routes)
        inner = self.fake

        def run(args, **kw):
            if "contents/" in args[-1]:
                if local_settings:
                    return "{}"
                raise CommandError(args, 1, "gh: Not Found (HTTP 404)")
            return inner(args, **kw)
        self.run = run

    def test_entry_splits_at_spaces_and_tabs_only(self):
        for odd in ("\x0b", "\x1c", " ", "\x0c", "\x85"):
            with self.subTest(char=repr(odd)):
                self.assertEqual(targets.entry("o/r%sx a" % odd), ["o/r%sx" % odd, "a"])
                self.assertEqual(targets.entry("o/r a%sb" % odd), ["o/r", "a%sb" % odd])
        self.assertEqual(targets.entry("o/r alice"), ["o/r", "alice"])
        self.assertEqual(targets.entry(" \to/r \t alice\t "), ["o/r", "alice"])
        self.assertEqual(targets.entry("o/r"), ["o/r"])

    def test_entry_skips_comments_and_blank_lines(self):
        for line in ("", "   ", "\t", "# note", "  #o/r a", "#"):
            with self.subTest(line=line):
                self.assertIsNone(targets.entry(line))

    def register(self, repo="o/r", actors=None):
        return targets.register(repo, actors, root=self.root, run=self.run, out=self.lines.append,
                                ensure_labels=lambda: mvi.Worker(repo, run=self.run).ensure_labels())

    def listed(self):
        return targets.read_targets(self.listing)

    def test_register_adds_the_repo_and_creates_labels_and_a_second_call_changes_nothing(self):
        self.register(actors=["alice"])
        self.assertEqual(self.listed(), {"o/r": "alice"})
        self.assertEqual(len(self.fake.api_calls("POST", "repos/o/r/labels")), len(mvi.LABELS))
        with open(self.listing) as handle:
            before = handle.read()
        self.make_runner(existing=list(mvi.LABELS))  # labels now exist
        self.register(actors=["alice"])
        with open(self.listing) as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(self.fake.api_calls("POST", "repos/o/r/labels"), [])
        self.assertTrue(self.lines[-1].startswith("already registered"))

    def test_the_actor_defaults_to_the_owner_and_a_different_actor_is_refused(self):
        self.register()
        self.assertEqual(self.listed(), {"o/r": "o"})
        with self.assertRaises(ValueError):
            self.register(actors=["mallory"])
        self.assertEqual(self.listed(), {"o/r": "o"})

    def test_unregister_removes_only_the_list_entry(self):
        self.register()
        self.register("o/s")
        self.fake.calls.clear()
        targets.unregister("o/r", root=self.root, out=self.lines.append)
        self.assertEqual(list(self.listed()), ["o/s"])
        self.assertEqual(self.fake.calls, [])  # no gh call: labels, Issues and PRs are untouched
        targets.unregister("o/r", root=self.root, out=self.lines.append)  # idempotent
        self.assertEqual(self.lines[-1], "not registered: o/r")

    def test_a_repo_that_commits_settings_local_json_is_not_registered(self):
        self.make_runner(local_settings=True)
        with self.assertRaises(ValueError) as caught:
            self.register()
        self.assertIn("settings.local.json", str(caught.exception))
        self.assertFalse(os.path.exists(self.listing))
        self.assertEqual(self.fake.calls, [])  # and no label is created

    def test_a_failed_check_other_than_not_found_registers_nothing(self):
        def run(args, **kw):
            raise CommandError(args, 1, "HTTP 500")
        with self.assertRaises(CommandError):
            targets.register("o/r", None, root=self.root, run=run)
        self.assertFalse(os.path.exists(self.listing))

    def test_bad_names_are_refused(self):
        for repo in ("o", "o/r/x", "o/ r", "/r", "o/;rm", "o/r\n", "o\n/r", "o/r\n o", "o/.."):
            with self.assertRaises(ValueError, msg=repo):
                self.register(repo)
        for actor in ("a b", "a\nb", "a\n b", "-x", "a;b"):  # (a trailing newline is stripped: harmless)
            with self.assertRaises(ValueError, msg=repr(actor)):
                self.register(actors=[actor])
        self.assertFalse(os.path.exists(self.listing))  # nothing reached the list

    def test_the_timer_processes_every_registered_repo_in_order(self):
        for repo in ("a/x", "b/y"):
            self.register(repo, ["alice"] if repo == "a/x" else None)
        log, fake = os.path.join(self.root, "calls.log"), os.path.join(self.root, "fake.sh")
        with open(fake, "w") as handle:
            handle.write('#!/bin/sh\ncat > /dev/null\necho "$*" >> %s\n' % log)
        os.chmod(fake, 0o755)
        text = host.unit_texts()[host.SERVICE]
        body = text.split("ExecStart=/bin/sh -c '", 1)[1].rsplit("'\n", 1)[0].replace("$$", "$")
        body = body.replace("/usr/bin/python3 %s/components/kagari-mvi/kagari_mvi.py" % host.CHECKOUT, fake)
        body = body.replace(host.TARGETS, self.listing)
        self.assertEqual(subprocess.run(["sh", "-c", body]).returncode, 0)
        with open(log) as handle:
            calls = handle.read().splitlines()
        self.assertEqual([c.split()[0:3] for c in calls],
                         [[e, "--repo", r] for r in ("a/x", "b/y") for e in ("run-once", "review-once", "cleanup-once")])
        self.assertTrue(calls[0].endswith("--allow-actor alice"))
        self.assertTrue(calls[3].endswith("--allow-actor b"))

    def test_the_cli_parses_register_and_unregister(self):
        args = mvi.parse_args(["register", "o/r", "--allow-actor", "x"])
        self.assertEqual((args.command, args.target, args.allow_actor), ("register", "o/r", ["x"]))
        self.assertEqual(mvi.parse_args(["unregister", "o/r"]).target, "o/r")


    def test_only_an_http_404_means_the_file_is_absent(self):
        def run(args, **kw):
            raise CommandError(args, 1, "dial tcp 10.0.0.1:4040: i/o timeout")  # a "404" that is no HTTP 404
        with self.assertRaises(CommandError):
            targets.register("o/r", None, root=self.root, run=run)
        self.assertFalse(os.path.exists(self.listing))

    def test_a_missing_permission_stops_before_any_label_is_made(self):
        with mock.patch("os.access", return_value=False), self.assertRaises(PermissionError):
            self.register()
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(os.path.exists(self.listing))

    def test_the_directory_is_readable_by_the_service_user_even_with_a_strict_umask(self):
        old = os.umask(0o077)
        self.addCleanup(os.umask, old)
        self.register()
        folder = os.path.dirname(self.listing)
        self.assertEqual(os.stat(folder).st_mode & 0o777, 0o755)
        self.assertEqual(os.stat(self.listing).st_mode & 0o777, 0o644)

    def test_the_same_repository_in_another_case_is_one_entry(self):
        self.register("O/R")
        self.make_runner()
        self.register("o/r", ["O"])  # the owner's default is O: the same actor
        self.assertEqual(self.listed(), {"O/R": "O"})
        self.assertTrue(self.lines[-1].startswith("already registered: O/R"))
        targets.unregister("o/r", root=self.root, out=self.lines.append)  # finds it in any case
        self.assertEqual(self.listed(), {})

    def test_comments_and_blank_lines_stay_when_the_list_is_rewritten(self):
        os.makedirs(os.path.dirname(self.listing))
        with open(self.listing, "w") as handle:
            handle.write("# my note\n\nx/y alice\n  # indented note\nz/w bob")  # the last line has no newline
        self.register()
        with open(self.listing) as handle:
            self.assertEqual(handle.read().splitlines(),
                             ["# my note", "", "x/y alice", "  # indented note", "z/w bob", "o/r o"])
        targets.unregister("x/y", root=self.root, out=self.lines.append)
        with open(self.listing) as handle:
            self.assertEqual(handle.read().splitlines(), ["# my note", "", "  # indented note", "z/w bob", "o/r o"])

    def test_a_change_made_while_register_waits_for_github_is_not_lost(self):
        def labels():  # the network call: meanwhile another register added a repository
            os.makedirs(os.path.dirname(self.listing), exist_ok=True)
            with open(self.listing, "a") as handle:
                handle.write("p/q carol\n")
            return []
        targets.register("o/r", None, root=self.root, run=self.run, out=self.lines.append, ensure_labels=labels)
        self.assertEqual(self.listed(), {"p/q": "carol", "o/r": "o"})

    def test_unregister_does_not_remove_another_repository_with_a_longer_name(self):
        self.register()
        self.register("o/r2")
        targets.unregister("o/r", root=self.root, out=self.lines.append)
        self.assertEqual(list(self.listed()), ["o/r2"])

    def test_status_does_not_list_an_indented_comment_as_a_target(self):
        os.makedirs(os.path.dirname(self.listing))
        with open(self.listing, "w") as handle:
            handle.write("  # not a repository\nx/y alice\n")
        shown = []
        host.Host(self.root, run=lambda args, **kw: "", out=shown.append).status()
        self.assertIn("target     x/y (actor alice)", shown)
        self.assertFalse(any(line.startswith("target") and "#" in line for line in shown))

    def write_listing(self, text):
        os.makedirs(os.path.dirname(self.listing), exist_ok=True)
        with open(self.listing, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    def read_raw(self):
        with open(self.listing, encoding="utf-8", newline="") as handle:
            return handle.read()

    def test_special_characters_in_a_comment_do_not_split_the_line(self):
        comment = "# a\x0bb\x0cc\x1cd\x1de\x1ef\x85g\u2028h\u2029i"
        self.write_listing(comment + "\nx/y alice\n")
        self.register()
        self.assertEqual(self.read_raw(), comment + "\nx/y alice\no/r o\n")
        targets.unregister("x/y", root=self.root, out=self.lines.append)
        self.assertEqual(self.read_raw(), comment + "\no/r o\n")
        self.assertEqual(list(self.listed()), ["o/r"])

    def test_a_crlf_list_is_kept_intact_and_becomes_lf(self):
        self.write_listing("# note\r\nx/y alice\r\n\r\nz/w bob\r\n")
        self.assertEqual(targets.read_targets(self.listing), {"x/y": "alice", "z/w": "bob"})
        self.register()
        self.assertEqual(self.read_raw(), "# note\nx/y alice\n\nz/w bob\no/r o\n")
        self.write_listing("# note\r\nx/y alice\r\nz/w bob\r\n")
        targets.unregister("x/y", root=self.root, out=self.lines.append)
        self.assertEqual(self.read_raw(), "# note\nz/w bob\n")

    def test_the_lock_is_exclusive_taken_before_the_write_and_released_on_error(self):
        events = []
        real_flock, real_write = targets.fcntl.flock, targets.write_lines
        seen = {}

        def flock(fd, op):
            events.append(("flock", op))
            seen["fd"] = fd
            return real_flock(fd, op)

        def write(path, lines):
            events.append(("write",))
            return real_write(path, lines)
        with mock.patch.object(targets.fcntl, "flock", flock), mock.patch.object(targets, "write_lines", write):
            self.register()
        self.assertEqual(events, [("flock", targets.fcntl.LOCK_EX), ("write",)])
        with self.assertRaises(KeyError):
            with targets.locked(self.listing):
                raise KeyError("boom")
        with self.assertRaises(OSError):
            os.fstat(seen["fd"])  # the descriptor was closed: the lock is released
        with open(self.listing + ".lock") as other:  # and another holder can take it at once
            targets.fcntl.flock(other, targets.fcntl.LOCK_EX | targets.fcntl.LOCK_NB)

    def test_register_waits_while_another_process_holds_the_lock(self):
        os.makedirs(os.path.dirname(self.listing))
        code = ("import fcntl,sys,time\nf=open(sys.argv[1],'a')\nfcntl.flock(f,fcntl.LOCK_EX)\n"
                "print('held',flush=True)\ntime.sleep(1.0)\n")
        proc = subprocess.Popen(["python3", "-c", code, self.listing + ".lock"], stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.wait)
        self.assertEqual(proc.stdout.readline().strip(), "held")
        start = time.monotonic()
        self.register()
        self.assertGreaterEqual(time.monotonic() - start, 0.5)
        self.assertEqual(list(self.listed()), ["o/r"])

    def test_check_writable_ends_for_a_relative_root_with_nothing_under_it(self):
        for path in ("nothing-here-512/etc/kagari-mvi/targets", "/nonexistent-512/a/targets"):
            with mock.patch("os.path.isdir", return_value=False):
                with self.assertRaises((PermissionError, ValueError)):
                    targets.check_writable(path)
        with self.assertRaises((PermissionError, ValueError)):
            targets.check_writable("nothing-here-512/etc/targets")

    def test_unregister_without_permission_touches_neither_labels_nor_the_list(self):
        self.register()
        before, calls = self.read_raw(), len(self.fake.calls)
        with mock.patch("os.access", return_value=False), self.assertRaises(PermissionError):
            targets.unregister("o/r", root=self.root, out=self.lines.append)
        self.assertEqual(self.read_raw(), before)
        self.assertEqual(len(self.fake.calls), calls)

    def test_a_real_http_404_means_the_file_is_absent_and_registration_goes_on(self):
        def run(args, **kw):
            if "contents/" in args[-1]:
                raise CommandError(args, 1, "gh: Not Found (HTTP 404)")
            return "[]"
        self.assertFalse(targets.commits_local_settings("o/r", run))
        targets.register("o/r", None, root=self.root, run=run, out=self.lines.append)
        self.assertEqual(self.listed(), {"o/r": "o"})


if __name__ == "__main__":
    unittest.main()
