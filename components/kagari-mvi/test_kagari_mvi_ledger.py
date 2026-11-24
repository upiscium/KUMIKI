"""Tests for the permission ledger (#430): matching, scopes, the hook, the comment and the CLI."""
import argparse
import datetime
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kagari_mvi as mvi  # noqa: E402
import kagari_mvi_ledger as led  # noqa: E402
import kagari_mvi_shell as sh  # noqa: E402
from kagari_mvi_testkit import FakeRunner, issue, labeled, edited, stream  # noqa: E402

NOW = datetime.datetime(2026, 11, 24, 12, 0, tzinfo=datetime.timezone.utc)


def grant(kind="bash", match="make deploy", scope="issue", repo="o/r", number=7, **more):
    base = {"id": "g1", "kind": kind, "match": match, "scope": scope, "repo": repo, "issue": number,
            "expires": "2026-12-24T00:00:00Z", "created": "2026-11-24T00:00:00Z", "reason": "t", "revoked": None}
    base.update(more)
    return base


def decide(grants, command):
    return led.decide(grants, "Bash", {"command": command}, NOW)[0]


class MatchTest(unittest.TestCase):
    def test_matching_command_is_allowed_and_other_is_denied(self):  # AC1, AC2
        grants = [grant()]
        self.assertEqual(decide(grants, "make deploy"), "allow")
        self.assertIsNone(decide(grants, "make other"))  # not a denied command: Claude's own rules decide
        wild = [grant(match="make build *")]
        self.assertEqual(decide(wild, "make build x --y"), "allow")
        self.assertIsNone(decide(wild, "make clean"))
        self.assertEqual(decide([grant(match="gh issue view *")], "gh issue view 5"), "deny")  # a grant cannot allow it

    def test_compound_and_unparsable_commands_are_never_allowed_automatically(self):  # AC3
        grants = [grant(match="make deploy"), grant(match="ls"), grant(kind="ssh", match=["h1"])]
        for command in ("make deploy && ls", "ls; make deploy", "ls | make deploy",
                        "make deploy\nls", "make deploy > out", "make 'deploy",
                        "echo $(make deploy)", "echo `ls`", "make deploy &", "ssh h1; ls"):
            with self.subTest(command=command):
                self.assertNotEqual(decide(grants, command), "allow")
        self.assertEqual(decide(grants, "ls; curl x"), "deny")  # still the speed bump
        self.assertEqual(decide(grants, "ssh h1 uptime"), "deny")  # ssh is denied whatever the grant says
        self.assertIsNone(sh.simple_argv("ls && ls"))
        self.assertEqual(sh.simple_argv("ls 'a b'"), ["ls", "a b"])

    def test_ssh_is_limited_to_listed_hosts_and_risky_options_are_refused(self):  # AC4
        grants = [grant(kind="ssh", match=["web1", "web2"])]
        ok = lambda command: led.matches(grants[0], "Bash", {"command": command})  # noqa: E731
        for command in ("ssh web1", "ssh -p 22 -i key deploy@web2 uptime", "ssh -oBatchMode=yes web1 ls"):
            with self.subTest(command=command):
                self.assertTrue(ok(command))
        for command in ("ssh -F cfg web1", "ssh -Fcfg web1", "ssh -S sock web1", "ssh -W h:22 web1", "ssh -oBatchMode=yes -W h:1 web1"):
            with self.subTest(command=command):  # AC5: -F, -S and -W are never automatic
                self.assertFalse(ok(command))
                self.assertIsNone(sh.ssh_target(sh.simple_argv(command)))
        for command in ("ssh other", "ssh deploy@other uptime", "ssh -o ProxyCommand='nc x 22' web1",
                        "ssh -oProxyCommand=nc web1", "ssh -o 'LocalCommand=id' -o PermitLocalCommand=yes web1",
                        "ssh -J jump web1", "ssh -o proxycommand=x web1", "ssh -Z web1", "ssh"):
            with self.subTest(command=command):
                self.assertEqual(decide(grants, command), "deny")  # denied before any grant is looked at
                self.assertFalse(ok(command))
        exact = [grant(match="ssh -o ProxyCommand=x web1")]  # a bash grant cannot be a way round it
        self.assertEqual(decide(exact, "ssh -o ProxyCommand=x web1"), "deny")
        with self.assertRaises(ValueError):
            led.add(os.devnull + "/x", "bash", "ssh -o ProxyCommand=x web1", "host", None, None, 1, "r")

    def test_issue_scope_is_not_used_for_another_issue(self):  # AC5
        grants = [grant(scope="issue", number=7), grant(id="r", scope="repo", number=None, match="curl a"),
                  grant(id="h", scope="host", repo=None, number=None, match="curl b")]
        names = lambda repo, number: [g["id"] for g in led.for_run(grants, repo, number, NOW)]  # noqa: E731
        self.assertEqual(names("o/r", 7), ["g1", "r", "h"])
        self.assertEqual(names("o/r", 8), ["r", "h"])
        self.assertEqual(names("o/other", 7), ["h"])
        self.assertEqual(names("O/R", 7), ["g1", "r", "h"])  # GitHub names are case-insensitive

    def test_expired_and_revoked_grants_are_not_used(self):  # AC6
        for more in ({"expires": "2026-11-24T11:59:59Z"}, {"revoked": "2026-11-24T10:00:00Z"}):
            grants = [grant(**more)]
            self.assertEqual(led.for_run(grants, "o/r", 7, NOW), [])
            self.assertIsNone(decide(grants, "make deploy"))


class HookTest(unittest.TestCase):
    def run_hook(self, tmp, grants, tool_input, tool="Bash"):
        gpath, audit = os.path.join(tmp, "g.json"), os.path.join(tmp, "a.jsonl")
        with open(gpath, "w") as handle:
            json.dump({"grants": grants}, handle)
        out, code = led.hook(gpath, audit, json.dumps({"tool_name": tool, "tool_input": tool_input}), NOW)
        return out, code, led.read_audit(audit)

    def test_allowed_operation_is_recorded_with_its_grant(self):  # AC7
        with tempfile.TemporaryDirectory() as tmp:
            out, code, audit = self.run_hook(tmp, [grant(id="abc")], {"command": "make deploy"})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertEqual([(r["decision"], r["grant"], r["input"]["command"]) for r in audit],
                         [("allow", "abc", "make deploy")])

    def test_denied_operation_is_recorded_and_unrelated_is_not(self):  # AC2, AC7
        with tempfile.TemporaryDirectory() as tmp:
            out, code, audit = self.run_hook(tmp, [], {"command": "curl example.com"})
            self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertEqual([r["decision"] for r in audit], ["deny"])
            out, code, audit = self.run_hook(tmp, [], {"command": "ls"})
            self.assertEqual((out, code), ("", 0))

    def test_hook_fails_closed(self):  # AC2
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(led.hook(os.path.join(tmp, "g"), os.path.join(tmp, "no", "a"), "not json", NOW)[1], 2)

    def test_edit_grant_matches_a_path_glob(self):
        grants = [grant(kind="edit", match="/srv/x/*.txt")]
        self.assertEqual(led.decide(grants, "Edit", {"file_path": "/srv/x/a.txt"}, NOW)[0], "allow")
        self.assertIsNone(led.decide(grants, "Edit", {"file_path": "/srv/y/a.txt"}, NOW)[0])
        self.assertIsNone(led.decide(grants, "Edit", {"file_path": "/srv/x/../y/a.txt"}, NOW)[0])


class RunTest(unittest.TestCase):
    def routes(self):
        return {("GET", "repos/o/r/issues?"): [issue(7, [mvi.APPROVED])],
                ("GET", "repos/o/r/issues/7/timeline"): [labeled("upiscium")],
                ("GET", "repos/o/r/pulls?"): [], ("GET", "repos/o/r/labels"): [],
                ("POST", "repos/o/r/pulls"): {"number": 70, "html_url": "https://x/pull/70"},
                ("GET", "repos/o/r"): {"default_branch": "main"}, ("POST", "graphql"): edited(None)}

    def worker(self, runner, ledger_path):
        return mvi.Worker("o/r", run=runner, log=lambda _: None, ledger_path=ledger_path)

    def test_run_gets_only_this_runs_grants_and_the_allowed_operations_are_logged(self):  # AC5, AC7
        seen = {}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state", "ledger.json")
            led.save(path, [grant(id="mine"), grant(id="other", number=8), grant(id="old", expires="2020-01-01T00:00:00Z")])
            inner = FakeRunner(self.routes())

            def runner(args, cwd=None, input=None, timeout=None):
                if "stream-json" in args:
                    settings = json.loads(next(a for a in args if a.startswith("--settings="))[11:])
                    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
                    snapshot = command.split()[3].strip("'")
                    audit = command.split()[4].strip("'")
                    seen["ids"] = [g["id"] for g in json.load(open(snapshot, encoding="utf-8"))["grants"]]
                    out, code = led.hook(snapshot, audit, json.dumps(
                        {"tool_name": "Bash", "tool_input": {"command": "make deploy"}}))
                    seen["allowed"] = code == 0 and "allow" in out
                return inner(args, cwd, input, timeout)

            self.assertEqual(self.worker(runner, path).run_once(["upiscium"], tmp), 0)
            log = os.path.join(tmp, "logs", "o__r", "issue-7-claude-ledger.jsonl")
            records = [json.loads(line) for line in open(log, encoding="utf-8").read().splitlines()]
            self.assertFalse(os.path.exists(os.path.join(tmp, "wt", "o__r", "issue-7.grants.json")))
        self.assertEqual(seen["ids"], ["mine"])
        self.assertTrue(seen["allowed"])
        self.assertEqual([(r["decision"], r["grant"]) for r in records], [("allow", "mine")])
        self.assertIn("allowed by ledger grant `mine`", "\n".join(
            b["body"] for b in inner.api_calls("POST", "repos/o/r/issues/70/comments")))

    def test_unmatched_command_blocks_with_one_comment_listing_candidates(self):  # AC2
        denied = [{"tool_name": "Bash", "tool_input": {"command": "make deploy @user"}},
                  {"tool_name": "Bash", "tool_input": {"command": "ssh web1 uptime"}},
                  {"tool_name": "Bash", "tool_input": {"command": "ls && curl x"}}]
        runner = FakeRunner(self.routes(), outputs={
            "stream-json": stream("no\nstatus: BLOCKED: ssh denied", denied=denied)})
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.worker(runner, os.path.join(tmp, "none.json")).run_once(["upiscium"], tmp), 1)
        comments = [b["body"] for b in runner.api_calls("POST", "repos/o/r/issues/7/comments")]
        self.assertEqual(len(comments), 1)
        self.assertIn("ssh web1 uptime", comments[0])
        for scope in ("issue", "repo", "host"):
            self.assertIn("--kind bash --match 'make deploy @user' --scope %s" % scope, comments[0])
        for line in comments[0].splitlines():  # AC7: outside a code span `@user` would notify; the listed text has a ZWSP
            if "@user" in line:
                self.assertTrue(line.strip().startswith("- `kagari-mvi ledger add"), line)
        self.assertIn("`Bash`: `make deploy @\u200buser`", comments[0])
        self.assertIn("--repo o/r --issue 7", comments[0])
        self.assertIn("no grant can allow this one", comments[0])  # the compound one
        self.assertIn(mvi.BLOCKED, runner.added_labels())


class DeniedCommandsTest(unittest.TestCase):
    def test_denied_commands_are_found_behind_prefixes_wrappers_and_substitutions(self):  # AC1
        for command in ("FOO=1 curl x", "env curl x", "time curl x", "bash -c 'curl x'", "echo $(curl x)",
                        "git -C . push", "echo `curl x`", "nohup curl x", "xargs -n 1 curl", "env -i A=1 wget x",
                        "sh -c \"ls && ssh h\"", "/usr/bin/curl x", "git -c a=b push origin", "echo \"$(gh pr list)\"",
                        "command curl x", "ls; (curl x)", "ls\ncurl x", "bash -lc 'FOO=1 env curl x'", "ls 'unclosed"):
            with self.subTest(command=command):
                self.assertEqual(decide([], command), "deny")
        for command in ("ls -l", "git status", "git -C . log", "echo curl", "python3 -c 'print(1); print(2)'",
                        "make test # curl", "grep push file"):
            with self.subTest(command=command):
                self.assertIsNone(decide([], command))

    def test_a_hash_inside_a_word_does_not_hide_what_follows(self):  # AC2
        self.assertEqual(sh.simple_argv("ls a#b"), ["ls", "a#b"])
        self.assertIsNone(sh.simple_argv("ls a#b && curl evil"))
        grants = [grant(match="ls *")]
        self.assertNotEqual(decide(grants, "ls a#b && curl evil"), "allow")
        self.assertEqual(decide(grants, "ls a#b && curl evil"), "deny")
        self.assertEqual(sh.command_denied("ls a#b && curl evil"), "curl")

    def test_permissions_deny_has_every_denied_command(self):  # AC3
        import kagari_mvi_exec as ex
        with tempfile.TemporaryDirectory() as tmp:
            seen = {}

            def runner(args, cwd=None, input=None, timeout=None):
                if "stream-json" in args:
                    seen["settings"] = json.loads(next(a for a in args if a.startswith("--settings="))[11:])
                return inner(args, cwd, input, timeout)

            inner = FakeRunner(RunTest.routes(None))
            mvi.Worker("o/r", run=runner, log=lambda _: None, ledger_path=os.path.join(tmp, "l.json")).run_once(
                ["upiscium"], tmp)
        deny = seen["settings"]["permissions"]["deny"]
        for name in ex.DENIED_COMMANDS:
            self.assertIn("Bash(%s *)" % name, deny)
            self.assertIn("Bash(%s)" % name, deny)

    def test_snapshot_files_are_fresh_files_and_a_link_to_the_ledger_is_not_followed(self):  # AC4
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = os.path.join(tmp, "ledger.json")
            led.save(ledger_path, [grant()])
            before = open(ledger_path, encoding="utf-8").read()
            grants, audit = os.path.join(tmp, "w.grants.json"), os.path.join(tmp, "w.audit.jsonl")
            os.symlink(ledger_path, grants)
            os.symlink(ledger_path, audit)
            worker = mvi.Worker("o/r", run=FakeRunner({}), log=lambda _: None, ledger_path=ledger_path)
            worker.write_grants(grants, audit, 7)
            self.assertEqual(open(ledger_path, encoding="utf-8").read(), before)
            for path, mode in ((grants, 0o644), (audit, 0o660)):
                self.assertFalse(os.path.islink(path))
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), mode)
            self.assertEqual([g["id"] for g in json.load(open(grants))["grants"]], ["g1"])
            os.symlink(ledger_path, ledger_path + ".new")  # the temporary file of save() too
            led.save(ledger_path, [grant(id="g2")])
            self.assertEqual([g["id"] for g in led.load(ledger_path)], ["g2"])

    def test_ledger_stops_for_a_user_other_than_root_and_kagari_runner(self):  # AC6
        with self.assertRaises(PermissionError):
            led.require_runner_user(1000, "someone")
        led.require_runner_user(0, "root")
        led.require_runner_user(1001, "kagari-runner")


class CliTest(unittest.TestCase):
    def run_cli(self, path, *argv):
        parser = argparse.ArgumentParser()
        led.add_parsers(parser.add_subparsers(dest="command", required=True))
        lines = []
        code = led.cli(parser.parse_args(["ledger"] + list(argv)), path, lines.append, NOW)
        return code, lines

    def test_add_list_and_revoke(self):  # AC8
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "home", "ledger.json")
            self.run_cli(path, "add", "--match", "make a", "--repo", "o/r", "--issue", "7",
                         "--reason", "deploy check")
            self.run_cli(path, "add", "--match", "make test", "--scope", "host", "--days", "1", "--reason", "x")
            grants = led.load(path)
            self.assertEqual([(g["kind"], g["scope"], g["match"]) for g in grants],
                             [("bash", "issue", "make a"), ("bash", "host", "make test")])
            self.assertEqual(grants[1]["expires"], "2026-11-25T12:00:00Z")
            self.assertEqual(grants[0]["created"], "2026-11-24T12:00:00Z")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
            _, lines = self.run_cli(path, "list")
            self.assertEqual(len(lines), 2)
            self.assertIn("deploy check", lines[0])
            self.run_cli(path, "revoke", grants[0]["id"])
            _, lines = self.run_cli(path, "list")
            self.assertEqual(len(lines), 1)
            _, lines = self.run_cli(path, "list", "--all")
            self.assertIn("revoked", lines[0])
            self.assertIn("no active grant", self.run_cli(path, "revoke", grants[0]["id"])[1][0])

    def test_a_broken_ledger_blocks_the_run_with_a_comment(self):  # AC8
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ledger.json")
            with open(path, "w") as handle:
                handle.write("{not json")
            runner = FakeRunner(RunTest.routes(None))
            self.assertEqual(mvi.Worker("o/r", run=runner, log=lambda _: None, ledger_path=path).run_once(
                ["upiscium"], tmp), 1)
        self.assertIn(mvi.BLOCKED, runner.added_labels())
        self.assertEqual(len(runner.api_calls("POST", "repos/o/r/issues/7/comments")), 1)

    def test_ledger_file_and_directory_are_private(self):  # AC8
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state", "ledger.json")
            old = os.umask(0)
            try:
                led.add(path, "bash", "make a", "host", None, None, 1, "r", NOW)
            finally:
                os.umask(old)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)

    def test_add_validates_its_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "l.json")
            for argv in (["--match", "ls", "--reason", "x"],  # issue scope needs repo and issue
                         ["--match", "ls && ls", "--scope", "host", "--reason", "x"],
                         ["--kind", "ssh", "--match", "web1", "--scope", "host", "--reason", "x"],  # denied
                         ["--match", "*", "--scope", "host", "--reason", "x"],  # AC5
                         ["--match", " * ", "--scope", "host", "--reason", "x"],
                         ["--kind", "edit", "--match", "*", "--scope", "host", "--reason", "x"],
                         ["--match", "curl x", "--scope", "host", "--reason", "x"],
                         ["--match", "FOO=1 git -C . push", "--scope", "host", "--reason", "x"],
                         ["--match", "ls", "--scope", "host", "--reason", " "]):
                with self.subTest(argv=argv), self.assertRaises(ValueError):
                    self.run_cli(path, "add", *argv)
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
