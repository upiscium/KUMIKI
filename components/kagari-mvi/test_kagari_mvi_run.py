"""Tests for `run-once` with gh/git replaced by a fake runner."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kagari_mvi as mvi  # noqa: E402
import kagari_mvi_exec as ex  # noqa: E402
from kagari_mvi_testkit import TEST_BODY, issue, stream, labeled, edited, FakeRunner  # noqa: E402


class RunOnceTest(unittest.TestCase):
    def routes(self, existing_pr=None):
        return {
            ("GET", "repos/o/r/issues?"): [issue(7, [mvi.APPROVED])],
            ("GET", "repos/o/r/issues/7/timeline"): [labeled("upiscium")],
            ("GET", "repos/o/r/issues/7"): issue(7, [mvi.APPROVED]),  # the direct re-read (#537)
            ("GET", "repos/o/r/pulls?"): [existing_pr] if existing_pr else [],
            ("GET", "repos/o/r/labels"): [],
            ("POST", "repos/o/r/pulls"): {"number": 70, "html_url": "https://x/pull/70"},
            ("GET", "repos/o/r"): {"default_branch": "main"},
            ("POST", "graphql"): edited(None),
        }

    def run_worker(self, runner, workdir=None):
        with tempfile.TemporaryDirectory() as tmp:
            worker = mvi.Worker("o/r", run=runner, log=lambda _: None)
            return worker.run_once(["upiscium"], workdir or tmp)

    def comments(self, runner, number):
        return [body["body"] for body in runner.api_calls("POST", "repos/o/r/issues/%d/comments" % number)]

    def test_an_issue_already_implemented_in_the_stale_search_is_left_alone(self):
        routes = self.routes()
        routes[("GET", "repos/o/r/issues/7")] = issue(7, [mvi.APPROVED, mvi.IMPLEMENTED])
        runner = FakeRunner(routes)
        self.assertEqual(self.run_worker(runner), 0)
        self.assertEqual(runner.added_labels(), [])
        self.assertEqual(runner.removed_labels(), [])
        self.assertEqual(self.comments(runner, 7), [])
        self.assertFalse([a for a, _, _ in runner.calls if a[0] != "gh"])  # no git, no worktree
        self.assertFalse(runner.api_calls("POST", "repos/o/r/pulls"))
        self.assertFalse(runner.api_calls("GET", "repos/o/r/issues/7/timeline"))

    def test_success_runs_agent_commits_tests_and_opens_draft_pr(self):
        runner = FakeRunner(self.routes())
        self.assertEqual(self.run_worker(runner), 0)
        argvs = [args for args, _, _ in runner.calls if args[0] == "sudo"]
        self.assertEqual(len(argvs), 3)  # Claude, kill leftovers, the runner's own test run
        claude, kill, test = argvs
        self.assertEqual(claude[:6], ["sudo", "-n", "-u", "kagari-agent", "-H", "--"])
        self.assertIn("CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1", claude)
        self.assertIn("sh", claude)
        self.assertIn("-lc", claude)
        self.assertEqual(claude[claude.index("--tools") + 1], "Bash,Read,Edit,Write,Glob,Grep")
        for flag in ("--strict-mcp-config", "--restricted", "none", "stream-json"):
            self.assertIn(flag, claude)
        self.assertNotIn("--bare", claude)
        settings = json.loads(next(a for a in claude if a.startswith("--settings="))[11:])
        allow = settings["permissions"]["allow"]
        self.assertIn("Bash", allow)
        deny = settings["permissions"]["deny"]
        self.assertIn("Agent", deny)
        self.assertIn("Bash(curl *)", deny)  # DENIED_COMMANDS are deny rules again (#527)
        hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertTrue(hook.endswith("|| exit 2"))
        self.assertTrue(any(rule.startswith("Edit(//") and rule.endswith("/wt/o__r/issue-7/**)") for rule in allow))
        self.assertNotIn("Edit", allow)
        self.assertTrue(any(rule.startswith("Write(//") and rule.endswith("/wt/o__r/issue-7/**/.claude/**)")
                            for rule in deny))
        # kill -1 must be the direct child of sudo: no timeout/env wrapper it would also kill.
        self.assertEqual(kill, ["sudo", "-n", "-u", "kagari-agent", "-H", "--", "sh", "-c", "kill -KILL -1; true"])
        self.assertNotIn("-lc", test)  # no login shell: agent-written ~/.profile is ignored
        self.assertTrue(test[-3].endswith("/wt/o__r/issue-7.verify"))
        self.assertEqual(test[-2:], ["python3 -m unittest", str(ex.TEST_TIMEOUT)])
        self.assertEqual(claude[claude.index("timeout"):claude.index("timeout") + 4],
                         ["timeout", "-k", "10", str(ex.AGENT_TIMEOUT)])
        checkout = next(args for args, _, _ in runner.calls if "checkout" in args)
        self.assertEqual(checkout[checkout.index("--work-tree") + 1], test[-3])
        add = next(args for args, _, _ in runner.calls if "--all" in args)
        self.assertIn(":(exclude,glob)**/__pycache__/**", add)
        for cache in (".npm-cache", ".npm", ".cache", ".yarn/cache", ".pnpm-store"):
            self.assertIn(":(exclude,glob)**/%s/**" % cache, add)
        commit = next(args for args, _, _ in runner.calls if "commit" in args)
        self.assertIn("--git-dir", commit)
        self.assertIn("core.hooksPath=/dev/null", commit)
        self.assertFalse(any(args[0] == "git" and "push" in args and "core.hooksPath=/dev/null" not in args
                             for args, _, _ in runner.calls))
        pulls = runner.api_calls("POST", "repos/o/r/pulls")
        self.assertEqual(len(pulls), 1)
        self.assertTrue(pulls[0]["draft"])
        self.assertIn("Closes #7", pulls[0]["body"])
        self.assertEqual(pulls[0]["base"], "main")
        self.assertIn("Base branch: `main`", pulls[0]["body"])
        report = self.comments(runner, 70)[0]
        self.assertIn("exit code: 0", report)
        self.assertIn("tested commit: `abc123`", report)
        self.assertIn("not reported by Claude", report)
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.IMPLEMENTED])
        self.assertEqual(runner.removed_labels(), ["kagari%3Arunning"])

    def test_declared_test_purposes_are_posted_without_changing_the_result(self):
        declared = ("done\ntests:\n- test_a: old\nuntested: old\n\ntests:\n  - test_greet: returns Hello (AC1)\n"
                    "- test_cli: prints ``` (AC2)\nuntested: unicode names\nstatus: COMPLETED")
        self.assertEqual(ex.test_purposes(declared), "tests:\n- test_greet: returns Hello (AC1)\n"
                         "- test_cli: prints ``` (AC2)\nuntested: unicode names")
        self.assertEqual(ex.test_purposes("tests:\n- a: b\nstatus: COMPLETED"), "tests:\n- a: b\nuntested: (not declared)")
        loose = "**tests:**\r\n\r\n1. test_a: checks\r\n   the parser (AC1)\r\n\r\nuntested:\r\n- CRLF\r\n\r\nstatus: COMPLETED"
        self.assertEqual(ex.test_purposes(loose), "tests:\n1. test_a: checks the parser (AC1)\nuntested: - CRLF")
        self.assertIsNone(ex.test_purposes("tests:\n\nstatus: COMPLETED"))
        huge = ex.test_purposes("tests:\n" + "- t: %s\n" % ("x" * 200) * 400 + "untested: none")
        self.assertLess(len(huge.encode()), ex.PURPOSE_BYTES + 50)
        self.assertTrue(huge.endswith("(truncated by the runner)"))
        wide = ex.test_purposes("tests:\n- t: " + "長" * 5000 + "\nuntested: none")  # one line, 15 KB
        self.assertLess(len(wide.encode()), ex.PURPOSE_BYTES + 50)
        fenced = "```\ntests:\n- t: a (AC1)\nuntested: none\n- not part of it\n```\nThanks, done.\nstatus: COMPLETED"
        self.assertEqual(ex.test_purposes(fenced), "tests:\n- t: a (AC1)\nuntested: none")
        loose_note = "tests:\n- t: a\n~~~\n```\nuntested:\n\n1. real HW that\n   is slow\n2. network\n\nThanks.\nstatus: COMPLETED"
        self.assertEqual(ex.test_purposes(loose_note), "tests:\n- t: a\nuntested: 1. real HW that is slow; 2. network")
        for tail, shown in (("- HW.\n  Needs a board.\n  Really.\n- net.\n  Thanks.\nDone.", "- HW.; - net."), ("- HW\n\n- net\n\n  more", "- HW; - net")):
            self.assertEqual(ex.test_purposes("tests:\n- t\nuntested:\n" + tail), "tests:\n- t\nuntested: " + shown)
        self.assertEqual(ex.test_purposes("tests:\n- t\nuntested:\n99. a\n2026. b"), "tests:\n- t\nuntested: 99. a")
        tail = lambda *lines: "tests:\n- t\nuntested:\n" + "\n".join(lines)  # noqa: E731
        for lines, shown in ((("- HW e.g.", "  the board", "- net"), "- HW e.g. the board; - net"),  # an abbreviation
                             (("- HW i.e.", "  the board", "- net"), "- HW i.e. the board; - net"),
                             (("- HW, net, etc.", "  and more", "- x"), "- HW, net, etc. and more; - x"),
                             (("- A vs.", "  B", "- x"), "- A vs. B; - x"),
                             (("- see cf.", "  README", "- x"), "- see cf. README; - x"),
                             (("- HW E.G.", "  the board", "- x"), "- HW E.G. the board; - x"),  # case-insensitive
                             (("- see ETC.", "  more", "- x"), "- see ETC. more; - x"),
                             (("- HW slow.)", "  Thanks.", "- net"), "- HW slow.); - net"),
                             (('- HW "slow."', "  Thanks.", "- net"), '- HW "slow."; - net'),
                             (("- HW 遅い…", "  Thanks.", "- net"), "- HW 遅い…; - net")):
            self.assertEqual(ex.test_purposes(tail(*lines)), "tests:\n- t\nuntested: " + shown)
        three = ex.test_purposes("tests:\n- t: a\n100. t100\n2026. y\n99. t99\nuntested: none")
        self.assertEqual(three, "tests:\n- t: a\n99. t99\nuntested: none")  # 100. and 2026. are dropped
        for count, cut in ((38, False), (39, True)):  # + `tests:` and `untested:` = 40 / 41 lines
            declared_lines = ex.test_purposes("tests:\n" + "- t\n" * count + "untested: none").splitlines()
            self.assertEqual((len(declared_lines), declared_lines[-1] == "(truncated by the runner)"),
                             (ex.PURPOSE_LINES + 1, True) if cut else (ex.PURPOSE_LINES, False))
        odd = ex.test_purposes("tests:\n- t: x" + "長" * 2000 + "\nuntested: none")  # cut inside a 3-byte char
        self.assertTrue(odd.endswith("(truncated by the runner)"))
        for text, shown in ((declared, "- test_greet: returns Hello (AC1)"), ("done\nstatus: COMPLETED", "申告なし")):
            runner = FakeRunner(self.routes())
            runner.outputs["stream-json"] = stream(text)
            self.assertEqual(self.run_worker(runner), 0)
            report = self.comments(runner, 70)[0]
            self.assertIn("テストの目的（Claude の申告。合否には使わない）", report)
            self.assertIn(shown, report)
            self.assertNotIn("prints ```", report)  # a fence in the declaration cannot close ours
            self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.IMPLEMENTED])

    def test_failing_test_creates_pr_then_blocks(self):
        runner = FakeRunner(self.routes(), fail={".verify python3 -m unittest 1800": "FAILED (failures=1)"})
        self.assertEqual(self.run_worker(runner), 1)
        self.assertEqual(len(runner.api_calls("POST", "repos/o/r/pulls")), 1)
        self.assertIn("exit code: 1", self.comments(runner, 70)[0])
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.BLOCKED])
        self.assertIn("Draft PR #70", self.comments(runner, 7)[0])

    def test_agent_blocked_status_creates_pr_then_blocks(self):
        runner = FakeRunner(self.routes())
        runner.outputs["stream-json"] = stream("status: BLOCKED: spec unclear")
        self.assertEqual(self.run_worker(runner), 1)
        self.assertEqual(len(runner.api_calls("POST", "repos/o/r/pulls")), 1)
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.BLOCKED])
        self.assertIn("BLOCKED: spec unclear", self.comments(runner, 7)[0])

    def test_auth_error_blocks_without_pr_or_retry(self):
        runner = FakeRunner(self.routes(), fail={"stream-json": "Not logged in · Please run /login"})
        self.assertEqual(self.run_worker(runner), 1)
        self.assertEqual(runner.api_calls("POST", "repos/o/r/pulls"), [])
        self.assertEqual(sum(1 for args, _, _ in runner.calls if "stream-json" in args), 1)
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.BLOCKED])
        comment = self.comments(runner, 7)[0]
        self.assertIn("kagari-agent の再ログインが必要", comment)
        self.assertIn("Please run /login", comment)
        self.assertNotIn('"type"', comment)  # no raw stream-json in the Issue comment

    def test_no_changes_blocks_without_pr(self):
        runner = FakeRunner(self.routes())
        runner.outputs["diff --cached --name-only"] = ""
        self.assertEqual(self.run_worker(runner), 1)
        self.assertEqual(runner.api_calls("POST", "repos/o/r/pulls"), [])
        self.assertIn("no changes", self.comments(runner, 7)[0])

    def test_protected_change_blocks_before_push(self):
        runner = FakeRunner(self.routes())
        runner.outputs["diff --cached --name-only"] = "src/a.py\0docs/CLAUDE.md\0"
        self.assertEqual(self.run_worker(runner), 1)
        self.assertFalse(any("push" in args for args, _, _ in runner.calls))
        self.assertEqual(runner.api_calls("POST", "repos/o/r/pulls"), [])
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.BLOCKED])
        comment = self.comments(runner, 7)[0]
        self.assertIn("`docs/CLAUDE.md`", comment)
        self.assertNotIn("src/a.py", comment)

    def test_protected_change_listed_in_issue_is_pushed_and_reported(self):
        routes = self.routes()
        allowed = issue(7, [mvi.APPROVED])
        allowed["body"] += "\n### 変更する保護ファイル\n- `.github/workflows/`\n- ./CLAUDE.md\n"
        routes[("GET", "repos/o/r/issues?")] = [allowed]
        runner = FakeRunner(routes)
        runner.outputs["diff --cached --name-only"] = ".github/workflows/ci.yml\0CLAUDE.md\0a.py\0"
        self.assertEqual(self.run_worker(runner), 0)
        self.assertTrue(any("push" in args for args, _, _ in runner.calls))
        claude = next(args for args, _, _ in runner.calls if "stream-json" in args)
        settings = json.loads(next(a for a in claude if a.startswith("--settings="))[11:])
        self.assertFalse(any("CLAUDE.md" in rule for rule in settings["permissions"]["deny"]))
        report = self.comments(runner, 70)[0]
        self.assertIn("allowed by the Issue): `.github/workflows/ci.yml`", report)
        self.assertIn("allowed by the Issue): `CLAUDE.md`", report)

    def test_protected_change_not_listed_still_blocks(self):
        routes = self.routes()
        allowed = issue(7, [mvi.APPROVED])
        allowed["body"] += "\n### 変更する保護ファイル\n- .github/workflows/ci.yml\n"
        routes[("GET", "repos/o/r/issues?")] = [allowed]
        runner = FakeRunner(routes)
        runner.outputs["diff --cached --name-only"] = ".github/workflows/ci.yml\0.github/workflows/x.yml\0"
        self.assertEqual(self.run_worker(runner), 1)
        self.assertFalse(any("push" in args for args, _, _ in runner.calls))
        comment = self.comments(runner, 7)[0]
        self.assertIn("`.github/workflows/x.yml`", comment)
        self.assertNotIn("ci.yml", comment)

    def test_missing_placement_accepts_english_headings_and_none_with_reason(self):
        self.assertEqual(ex.missing_placement(TEST_BODY % 1), [])
        english = ("## Overall logic\nflow\n## why here\nsame stage\n"
                   "#### Rejected placements\n- 無し：他に候補が無い\n")
        self.assertEqual(ex.missing_placement(english), [])
        self.assertEqual(ex.missing_placement(english.replace("same stage", " ")), ["置く理由"])
        self.assertEqual(ex.missing_placement("全体ロジック\n置く理由\n却下した置き場所\n"),
                         ["全体ロジック", "置く理由", "却下した置き場所"])

    def test_allowed_protected(self):
        body = ("### Protected files to change\n- `CLAUDE.md` — 理由\n* ./.claude/\n- /AGENTS.md\n"
                "### テストコマンド\n- not.this\n")
        self.assertEqual(ex.allowed_protected(body), ["CLAUDE.md", ".claude/", "AGENTS.md"])
        self.assertEqual(ex.allowed_protected("no heading\n- CLAUDE.md\n"), [])

    def test_shared_dir_is_per_repository_and_shared(self):
        with tempfile.TemporaryDirectory() as workdir:
            a = mvi.shared_dir(workdir, mvi.Worker("o/a").slug, "issue-7")
            b = mvi.shared_dir(workdir, mvi.Worker("o/b").slug, "issue-7")
            self.assertNotEqual(a, b)
            self.assertEqual(a, os.path.join(workdir, "wt", "o__a", "issue-7"))
            self.assertEqual(os.stat(os.path.dirname(a)).st_mode & 0o7777, 0o3770)

    def test_protected_paths(self):
        changed = ["AGENTS.md", "pkg/CLAUDE.local.md", "a/.claude/settings.json", ".claude/x",
                   ".github/workflows/ci.yml", "src/claude.md", "docs/.github/workflows/x",
                   ".github/CODEOWNERS", "src/main.py"]
        self.assertEqual(ex.protected_paths(changed), changed[:5])

    def test_committed_settings_local_blocks_before_claude(self):
        fake = FakeRunner(self.routes())

        def runner(args, **kwargs):  # the checkout brings a committed .claude/settings.local.json
            if args[-4:-2] == ["-B", "kagari/issue-7"]:
                os.makedirs(os.path.join(args[-2], ".claude"))
                open(os.path.join(args[-2], ".claude", "settings.local.json"), "w").close()
            return fake(args, **kwargs)
        with tempfile.TemporaryDirectory() as workdir:
            self.assertEqual(self.run_worker(runner, workdir), 1)
        self.assertFalse(any("stream-json" in args for args, _, _ in fake.calls))
        self.assertIn("settings.local.json", self.comments(fake, 7)[0])

    def test_leftover_dirs_are_reopened_as_agent_and_deleted(self):
        runner = FakeRunner(self.routes())
        with tempfile.TemporaryDirectory() as workdir:
            wt = os.path.join(workdir, "wt", "o__r")
            for name in ("issue-7", "issue-7.verify"):  # no .git: an earlier removal stopped halfway
                os.makedirs(os.path.join(wt, name, ".npm-cache"))
            self.assertEqual(self.run_worker(runner, workdir), 0)
            self.assertFalse(os.path.exists(os.path.join(wt, "issue-7")))
            self.assertFalse(os.path.exists(os.path.join(wt, "issue-7.verify", ".npm-cache")))
        joined = [" ".join(args) for args, _, _ in runner.calls]
        for name in ("issue-7", "issue-7.verify"):
            self.assertIn("sudo -n -u kagari-agent -H -- find %s/%s -user kagari-agent -type d "
                          "-exec chmod u+rwx,g+rwx {} ;" % (wt, name), joined)
        self.assertFalse(any("worktree remove" in j for j in joined))
        kill = "sudo -n -u kagari-agent -H -- sh -c kill -KILL -1; true"  # before deleting issue-7
        self.assertLess(joined.index(kill), joined.index(next(j for j in joined if " find %s/issue-7 " % wt in j)))

    def test_leftover_reopen_reaches_dirs_the_agent_cannot_read(self):
        runner = FakeRunner(self.routes())
        with tempfile.TemporaryDirectory() as workdir:
            leftover = os.path.join(workdir, "wt", "o__r", "issue-7")
            locked = os.path.join(leftover, "locked")
            os.makedirs(os.path.join(locked, "inner"))
            os.chmod(locked, 0)
            with mock.patch.object(ex.shutil, "rmtree"):  # keep it to run the real find on
                self.assertEqual(self.run_worker(runner, workdir), 0)
            find = next(args for args, _, _ in runner.calls if args[6:8] == ["find", leftover])
            subprocess.run([a for a in find[6:] if a not in ("-user", "kagari-agent")], check=True)
            self.assertEqual(os.stat(locked).st_mode & 0o770, 0o770)
            self.assertEqual(os.stat(os.path.join(locked, "inner")).st_mode & 0o070, 0o070)

    def test_leftover_that_cannot_be_reopened_blocks_at_worktree(self):
        runner = FakeRunner(self.routes(), fail={"-exec chmod u+rwx,g+rwx": "find: 'x': Permission denied"})
        with tempfile.TemporaryDirectory() as workdir:
            leftover = os.path.join(workdir, "wt", "o__r", "issue-7")
            os.makedirs(leftover)
            self.assertEqual(self.run_worker(runner, workdir), 1)
            self.assertTrue(os.path.exists(leftover))  # nothing is deleted after a failed reopen
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.BLOCKED])
        body = self.comments(runner, 7)[0]
        self.assertIn("`worktree`", body)
        self.assertIn("Permission denied", body)
        self.assertFalse(any("stream-json" in args for args, _, _ in runner.calls))

    def test_leftover_that_cannot_be_deleted_blocks_after_reopening(self):
        runner = FakeRunner(self.routes())
        reopened_first, real_rmtree = [], ex.shutil.rmtree

        def rmtree(path, **kwargs):  # TemporaryDirectory also uses rmtree
            if not path.endswith("/wt/o__r/issue-7"):
                return real_rmtree(path, **kwargs)
            reopened_first.append(any(args[:7] == ["sudo", "-n", "-u", "kagari-agent", "-H", "--", "find"]
                                      and path in args for args, _, _ in runner.calls))
            raise PermissionError(13, "Permission denied", path)
        with tempfile.TemporaryDirectory() as workdir:
            os.makedirs(os.path.join(workdir, "wt", "o__r", "issue-7"))
            with mock.patch.object(ex.shutil, "rmtree", side_effect=rmtree):
                self.assertEqual(self.run_worker(runner, workdir), 1)
        self.assertEqual(reopened_first, [True])
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.BLOCKED])
        body = self.comments(runner, 7)[0]
        self.assertIn("`worktree`", body)
        self.assertIn("Permission denied", body)
        self.assertFalse(any("stream-json" in args for args, _, _ in runner.calls))

    def test_missing_test_command_blocks_before_start(self):
        routes = self.routes()
        no_test = issue(7, [mvi.APPROVED])
        no_test["body"] = "no test command"
        routes[("GET", "repos/o/r/issues?")] = [no_test]
        runner = FakeRunner(routes)
        self.assertEqual(self.run_worker(runner), 0)
        self.assertEqual(runner.added_labels(), [mvi.BLOCKED])
        self.assertFalse(any(args[0] in ("git", "sudo") for args, _, _ in runner.calls))

    def test_missing_placement_blocks_before_start_and_names_the_headings(self):
        for body, missing in (("### テストコマンド\n```\nmake\n```\n", ["全体ロジック", "置く理由", "却下した置き場所"]),
                              (TEST_BODY.replace("同じ段で止められる", "") % 7, ["置く理由"]),
                              (TEST_BODY.replace("- review-once：実装の後になる", "- [ ]\n-") % 7,
                               ["却下した置き場所"])):
            routes = self.routes()
            routes[("GET", "repos/o/r/issues?")] = [dict(issue(7, [mvi.APPROVED]), body=body)]
            runner = FakeRunner(routes)
            self.assertEqual(self.run_worker(runner), 0)
            self.assertEqual(runner.added_labels(), [mvi.BLOCKED])
            self.assertFalse(any(args[0] in ("git", "sudo") for args, _, _ in runner.calls))
            comment = self.comments(runner, 7)[0]
            for name in ("全体ロジック", "置く理由", "却下した置き場所"):
                self.assertEqual("`### %s`" % name in comment, name in missing, (name, comment))

    def test_failure_in_push_blocks_with_stage_and_tail(self):
        runner = FakeRunner(self.routes(), fail={"push origin": "line1\n! [rejected] non-fast-forward"})
        self.assertEqual(self.run_worker(runner), 1)
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.BLOCKED])
        self.assertEqual(runner.removed_labels(), ["kagari%3Arunning"])
        body = self.comments(runner, 7)[0]
        self.assertIn("`push`", body)
        self.assertIn("non-fast-forward", body)
        self.assertEqual(runner.api_calls("POST", "repos/o/r/pulls"), [])

    def test_running_kept_when_blocked_cannot_be_added(self):
        runner = FakeRunner(self.routes(), fail={"push origin": "rejected"})

        def call(args, cwd=None, input=None, timeout=None):
            if input and mvi.BLOCKED in input:
                raise mvi.CommandError(args, 1, "HTTP 503")
            return runner(args, cwd=cwd, input=input, timeout=timeout)

        with tempfile.TemporaryDirectory() as workdir:
            with self.assertRaises(mvi.CommandError):
                mvi.Worker("o/r", run=call, log=lambda _: None).run_once(["upiscium"], workdir)
        self.assertEqual(runner.removed_labels(), [])

    def test_existing_pr_is_not_duplicated_nor_marked_implemented(self):
        runner = FakeRunner(self.routes(existing_pr={"number": 70, "html_url": "u"}))
        self.assertEqual(self.run_worker(runner, "/nonexistent"), 1)
        self.assertEqual(runner.api_calls("POST", "repos/o/r/pulls"), [])
        self.assertFalse(any(args[0] in ("git", "sudo") for args, _, _ in runner.calls))
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.BLOCKED])
        self.assertIn("PR #70", self.comments(runner, 7)[0])

    def test_no_candidate_is_a_noop(self):
        runner = FakeRunner({("GET", "repos/o/r/issues?"): [issue(1, [mvi.APPROVED, mvi.BLOCKED])]})
        self.assertEqual(self.run_worker(runner, "/nonexistent"), 0)
        self.assertEqual(len(runner.calls), 1)


if __name__ == "__main__":
    unittest.main()
