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


def grant(kind="bash", match="git push origin main", scope="issue", repo="o/r", number=7, **more):
    base = {"id": "g1", "kind": kind, "match": match, "scope": scope, "repo": repo, "issue": number,
            "expires": "2026-12-24T00:00:00Z", "created": "2026-11-24T00:00:00Z", "reason": "t", "revoked": None}
    base.update(more)
    return base


def decide(grants, command):
    return led.decide(grants, "Bash", {"command": command}, NOW)[0]


class MatchTest(unittest.TestCase):
    def test_matching_command_is_allowed_and_other_is_denied(self):  # AC1, AC2
        grants = [grant()]
        self.assertEqual(decide(grants, "git push origin main"), "allow")
        self.assertEqual(decide(grants, "git push origin other"), "deny")
        self.assertIsNone(decide(grants, "ls -l"))  # not a denied command: Claude's own rules decide
        wild = [grant(match="gh issue view *")]
        self.assertEqual(decide(wild, "gh issue view 5 --json title"), "allow")
        self.assertEqual(decide(wild, "gh pr merge 5"), "deny")

    def test_compound_and_unparsable_commands_are_never_allowed_automatically(self):  # AC3
        grants = [grant(match="git push origin main"), grant(match="ls"), grant(kind="ssh", match=["h1"])]
        for command in ("git push origin main && ls", "ls; git push origin main", "ls | git push origin main",
                        "git push origin main\nls", "git push origin main > out", "git push 'origin main",
                        "echo $(git push origin main)", "echo `ls`", "git push origin main &", "ssh h1; ls"):
            with self.subTest(command=command):
                self.assertNotEqual(decide(grants, command), "allow")
        self.assertEqual(decide(grants, "ls; curl x"), "deny")  # still the speed bump
        self.assertEqual(decide(grants, "ssh h1 uptime"), "allow")
        self.assertIsNone(sh.simple_argv("ls && ls"))
        self.assertEqual(sh.simple_argv("ls 'a b'"), ["ls", "a b"])

    def test_ssh_is_limited_to_listed_hosts_and_risky_options_are_refused(self):  # AC4
        grants = [grant(kind="ssh", match=["web1", "web2"])]
        for command in ("ssh web1", "ssh -p 22 -i key deploy@web2 uptime", "ssh -oBatchMode=yes web1 ls"):
            with self.subTest(command=command):
                self.assertEqual(decide(grants, command), "allow")
        for command in ("ssh other", "ssh deploy@other uptime", "ssh -o ProxyCommand='nc x 22' web1",
                        "ssh -oProxyCommand=nc web1", "ssh -o 'LocalCommand=id' -o PermitLocalCommand=yes web1",
                        "ssh -J jump web1", "ssh -o proxycommand=x web1", "ssh -Z web1", "ssh"):
            with self.subTest(command=command):
                self.assertEqual(decide(grants, command), "deny")
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
            self.assertEqual(decide(grants, "git push origin main"), "deny")


class HookTest(unittest.TestCase):
    def run_hook(self, tmp, grants, tool_input, tool="Bash"):
        gpath, audit = os.path.join(tmp, "g.json"), os.path.join(tmp, "a.jsonl")
        with open(gpath, "w") as handle:
            json.dump({"grants": grants}, handle)
        out, code = led.hook(gpath, audit, json.dumps({"tool_name": tool, "tool_input": tool_input}), NOW)
        return out, code, led.read_audit(audit)

    def test_allowed_operation_is_recorded_with_its_grant(self):  # AC7
        with tempfile.TemporaryDirectory() as tmp:
            out, code, audit = self.run_hook(tmp, [grant(id="abc")], {"command": "git push origin main"})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertEqual([(r["decision"], r["grant"], r["input"]["command"]) for r in audit],
                         [("allow", "abc", "git push origin main")])

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
                        {"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}))
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
        denied = [{"tool_name": "Bash", "tool_input": {"command": "ssh web1 uptime"}},
                  {"tool_name": "Bash", "tool_input": {"command": "ls && curl x"}}]
        runner = FakeRunner(self.routes(), outputs={
            "stream-json": stream("no\nstatus: BLOCKED: ssh denied", denied=denied)})
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.worker(runner, os.path.join(tmp, "none.json")).run_once(["upiscium"], tmp), 1)
        comments = [b["body"] for b in runner.api_calls("POST", "repos/o/r/issues/7/comments")]
        self.assertEqual(len(comments), 1)
        self.assertIn("ssh web1 uptime", comments[0])
        for scope in ("issue", "repo", "host"):
            self.assertIn("--kind ssh --match web1 --scope %s" % scope, comments[0])
        self.assertIn("--repo o/r --issue 7", comments[0])
        self.assertIn("no grant can allow this one", comments[0])  # the compound one
        self.assertIn(mvi.BLOCKED, runner.added_labels())


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
            self.run_cli(path, "add", "--kind", "ssh", "--match", "web1,web2", "--repo", "o/r", "--issue", "7",
                         "--reason", "deploy check")
            self.run_cli(path, "add", "--match", "make test", "--scope", "host", "--days", "1", "--reason", "x")
            grants = led.load(path)
            self.assertEqual([(g["kind"], g["scope"], g["match"]) for g in grants],
                             [("ssh", "issue", ["web1", "web2"]), ("bash", "host", "make test")])
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

    def test_add_validates_its_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "l.json")
            for argv in (["--match", "ls", "--reason", "x"],  # issue scope needs repo and issue
                         ["--match", "ls && ls", "--scope", "host", "--reason", "x"],
                         ["--kind", "ssh", "--match", "a b!", "--scope", "host", "--reason", "x"],
                         ["--match", "ls", "--scope", "host", "--reason", " "]):
                with self.subTest(argv=argv), self.assertRaises(ValueError):
                    self.run_cli(path, "add", *argv)
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
