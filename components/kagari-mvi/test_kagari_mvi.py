"""Minimal tests for kagari_mvi with gh/git replaced by a fake runner.

Run: python3 components/kagari-mvi/test_kagari_mvi.py  (or: just mvi::test)
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kagari_mvi as mvi  # noqa: E402
import kagari_mvi_exec as ex  # noqa: E402


TEST_BODY = "body %d\n\n### テストコマンド\n```sh\npython3 -m unittest\n```\n"


def issue(number, labels, created="2026-11-01T00:00:00Z", pr=False, state="open"):
    data = {"number": number, "title": "Issue %d" % number, "body": TEST_BODY % number,
            "state": state, "created_at": created,
            "labels": [{"name": name} for name in labels]}
    if pr:
        data["pull_request"] = {"url": "x"}
    return data


def stream(result, is_error=False, denials=0):
    return "\n".join(json.dumps(event) for event in (
        {"type": "system", "subtype": "init"},
        {"type": "result", "is_error": is_error, "result": result,
         "permission_denials": [{"tool_name": "Bash"}] * denials}))


def labeled(login, name=mvi.APPROVED, at="2026-11-14T01:00:00Z"):
    return {"event": "labeled", "label": {"name": name}, "actor": {"login": login}, "created_at": at}


def edited(at):
    return {"data": {"repository": {"issue": {"lastEditedAt": at}}}}


class FakeRunner:
    """Answers `gh api` calls from a route table and records every call."""

    def __init__(self, routes, fail=None, outputs=None):
        self.routes = routes  # {(method, path-prefix): response}
        self.fail = fail or {}  # {argv-substring: output}
        self.outputs = outputs if outputs is not None else {
            "stream-json": stream("done\nstatus: COMPLETED"), "diff --cached --name-only": " M a.py",
            "rev-parse HEAD": "abc123\n"}
        self.calls = []

    def __call__(self, args, cwd=None, input=None, timeout=None):
        is_api = list(args[:2]) == ["gh", "api"]
        self.calls.append((list(args), cwd, json.loads(input) if input and is_api else input))
        joined = " ".join(args)
        for needle, output in self.fail.items():
            if needle in joined:
                raise mvi.CommandError(args, 1, output)
        if not is_api:
            return next((out for needle, out in self.outputs.items() if needle in joined), "")
        method, path = ("DIFF" if "diff" in args[5] else args[3]), args[6]
        for (route_method, prefix), response in self.routes.items():
            if route_method == method and path.startswith(prefix):
                if isinstance(response, str):
                    return response
                return json.dumps(response) if response is not None else ""
        return ""

    def api_calls(self, method, prefix):
        return [body for args, _, body in self.calls
                if args[:2] == ["gh", "api"] and args[3] == method and args[6].startswith(prefix)]

    def added_labels(self):
        return [name for body in self.api_calls("POST", "repos/o/r/issues/") if body and "labels" in body
                for name in body["labels"]]

    def removed_labels(self):
        return [args[6].rsplit("/", 1)[1] for args, _, _ in self.calls
                if args[:2] == ["gh", "api"] and args[3] == "DELETE"]


class SelectionTest(unittest.TestCase):
    def test_picks_oldest_approved_without_state_labels(self):
        issues = [
            issue(5, [mvi.APPROVED], created="2026-11-03T00:00:00Z"),
            issue(3, [mvi.APPROVED], created="2026-11-02T00:00:00Z"),
            issue(9, [mvi.APPROVED], created="2026-11-04T00:00:00Z"),
        ]
        self.assertEqual(mvi.select_issue(issues)["number"], 3)

    def test_excludes_state_labels_prs_closed_and_unapproved(self):
        issues = [issue(n, [mvi.APPROVED, label], created="2026-10-0%dT00:00:00Z" % n)
                  for n, label in enumerate(mvi.EXCLUDED, start=1)]
        issues += [
            issue(6, [mvi.APPROVED], created="2026-10-06T00:00:00Z", pr=True),
            issue(7, [mvi.APPROVED], created="2026-10-07T00:00:00Z", state="closed"),
            issue(8, ["bug"], created="2026-10-08T00:00:00Z"),
        ]
        self.assertIsNone(mvi.select_issue(issues))
        issues.append(issue(10, [mvi.APPROVED, "bug"], created="2026-10-10T00:00:00Z"))
        self.assertEqual(mvi.select_issue(issues)["number"], 10)

    def test_stale_running_is_not_reselected(self):
        self.assertFalse(mvi.is_candidate(issue(1, [mvi.APPROVED, mvi.RUNNING])))


class ActorTest(unittest.TestCase):
    def test_last_approved_labeled_event_wins(self):
        events = [labeled("upiscium"), {"event": "unlabeled", "label": {"name": mvi.APPROVED},
                                        "actor": {"login": "upiscium"}},
                  labeled("someone"), labeled("upiscium", name="bug"), {"event": "commented"}]
        self.assertEqual(mvi.approval_actor(events), "someone")

    def test_check_actor(self):
        self.assertIsNone(mvi.check_actor("upiscium", ["upiscium"]))
        self.assertIn("someone", mvi.check_actor("someone", ["upiscium"]))
        self.assertIsNotNone(mvi.check_actor(None, ["upiscium"]))

    def test_disallowed_actor_blocks_without_running(self):
        runner = FakeRunner({
            ("GET", "repos/o/r/issues?"): [issue(4, [mvi.APPROVED])],
            ("GET", "repos/o/r/issues/4/timeline"): [labeled("someone")],
        })
        code = mvi.Worker("o/r", run=runner, log=lambda _: None).run_once(["upiscium"], "/nonexistent")
        self.assertEqual(code, 0)
        self.assertEqual(runner.added_labels(), [mvi.BLOCKED])
        comments = runner.api_calls("POST", "repos/o/r/issues/4/comments")
        self.assertEqual(len(comments), 1)
        self.assertIn("someone", comments[0]["body"])
        self.assertFalse(any(args[0] == "git" for args, _, _ in runner.calls))


    def test_body_edited_after_approval_blocks_before_running(self):
        runner = FakeRunner({
            ("GET", "repos/o/r/issues?"): [issue(4, [mvi.APPROVED])],
            ("GET", "repos/o/r/issues/4/timeline"): [labeled("upiscium", at="2026-11-14T01:00:00Z")],
            ("POST", "graphql"): edited("2026-11-14T01:00:05Z"),
        })
        code = mvi.Worker("o/r", run=runner, log=lambda _: None).run_once(["upiscium"], "/nonexistent")
        self.assertEqual(code, 0)
        self.assertEqual(runner.added_labels(), [mvi.BLOCKED])
        self.assertIn("edited after", runner.api_calls("POST", "repos/o/r/issues/4/comments")[0]["body"])
        self.assertFalse(any(args[0] in ("git", "sudo") for args, _, _ in runner.calls))

    def test_check_body_unchanged(self):
        approved = "2026-11-14T01:00:00Z"
        self.assertIsNone(mvi.check_body_unchanged(None, approved))
        self.assertIsNone(mvi.check_body_unchanged("2026-11-14T00:59:59Z", approved))
        self.assertIsNotNone(mvi.check_body_unchanged("2026-11-14T01:00:01Z", approved))
        self.assertIsNotNone(mvi.check_body_unchanged("2026-11-14T00:00:00Z", None))


class ParseTest(unittest.TestCase):
    def test_test_command_heading_and_fence(self):
        self.assertEqual(mvi.test_command(TEST_BODY % 1), "python3 -m unittest")
        self.assertEqual(mvi.test_command("## Test command\n\n```\n\njust test\nmore\n```"), "just test")
        self.assertIsNone(mvi.test_command("no heading\n```\nmake\n```"))
        self.assertIsNone(mvi.test_command("### テストコマンド\n### 次\n```\nmake\n```"))

    def test_agent_outcome(self):
        done = ex.agent_outcome(stream("ok\nstatus: COMPLETED", denials=2), False)
        self.assertEqual((done["completed"], done["status"], done["denials"], done["auth"]),
                         (True, "COMPLETED", 2, False))
        blocked = ex.agent_outcome(stream("status: BLOCKED: needs X"), False)
        self.assertEqual((blocked["completed"], blocked["status"]), (False, "BLOCKED: needs X"))
        self.assertTrue(ex.agent_outcome("Not logged in · Please run /login", True)["auth"])
        self.assertTrue(ex.agent_outcome(stream("API Error: 401 {}", is_error=True), True)["auth"])
        self.assertFalse(ex.agent_outcome(stream("401 in code\nstatus: COMPLETED"), False)["auth"])
        # An unrelated failure whose stream mentions 401 / login elsewhere is not an auth error.
        noisy = json.dumps({"type": "user", "uuid": "a401b", "content": "see /login docs"})
        timeout = noisy + "\n" + stream("max turns", is_error=True) + "\ntimed out after 3600s"
        self.assertFalse(ex.agent_outcome(timeout, True)["auth"])


class RunOnceTest(unittest.TestCase):
    def routes(self, existing_pr=None):
        return {
            ("GET", "repos/o/r/issues?"): [issue(7, [mvi.APPROVED])],
            ("GET", "repos/o/r/issues/7/timeline"): [labeled("upiscium")],
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
        for rule in ("Bash(curl *)", "Bash(gh *)", "Bash(git push *)", "Bash(sudo)", "Agent"):
            self.assertIn(rule, deny)
        self.assertTrue(any(rule.startswith("Edit(//") and rule.endswith("/wt/issue-7/**)") for rule in allow))
        self.assertNotIn("Edit", allow)
        self.assertTrue(any(rule.startswith("Write(//") and rule.endswith("/wt/issue-7/**/.claude/**)")
                            for rule in deny))
        # kill -1 must be the direct child of sudo: no timeout/env wrapper it would also kill.
        self.assertEqual(kill, ["sudo", "-n", "-u", "kagari-agent", "-H", "--", "sh", "-c", "kill -KILL -1; true"])
        self.assertNotIn("-lc", test)  # no login shell: agent-written ~/.profile is ignored
        self.assertTrue(test[-3].endswith("/wt/issue-7.verify"))
        self.assertEqual(test[-2:], ["python3 -m unittest", str(ex.TEST_TIMEOUT)])
        self.assertEqual(claude[claude.index("timeout"):claude.index("timeout") + 4],
                         ["timeout", "-k", "10", str(ex.AGENT_TIMEOUT)])
        checkout = next(args for args, _, _ in runner.calls if "checkout" in args)
        self.assertEqual(checkout[checkout.index("--work-tree") + 1], test[-3])
        add = next(args for args, _, _ in runner.calls if "--all" in args)
        self.assertIn(":(exclude,glob)**/__pycache__/**", add)
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

    def test_allowed_protected(self):
        body = ("### Protected files to change\n- `CLAUDE.md` — 理由\n* ./.claude/\n- /AGENTS.md\n"
                "### テストコマンド\n- not.this\n")
        self.assertEqual(ex.allowed_protected(body), ["CLAUDE.md", ".claude/", "AGENTS.md"])
        self.assertEqual(ex.allowed_protected("no heading\n- CLAUDE.md\n"), [])

    def test_protected_paths(self):
        changed = ["AGENTS.md", "pkg/CLAUDE.local.md", "a/.claude/settings.json", ".claude/x",
                   ".github/workflows/ci.yml", "src/claude.md", "docs/.github/workflows/x",
                   ".github/CODEOWNERS", "src/main.py"]
        self.assertEqual(ex.protected_paths(changed), changed[:5])

    def test_committed_settings_local_blocks_before_claude(self):
        runner = FakeRunner(self.routes())
        with tempfile.TemporaryDirectory() as workdir:
            os.makedirs(os.path.join(workdir, "wt", "issue-7", ".claude"))
            open(os.path.join(workdir, "wt", "issue-7", ".claude", "settings.local.json"), "w").close()
            self.assertEqual(self.run_worker(runner, workdir), 1)
        self.assertFalse(any("stream-json" in args for args, _, _ in runner.calls))
        self.assertIn("settings.local.json", self.comments(runner, 7)[0])

    def test_missing_test_command_blocks_before_start(self):
        routes = self.routes()
        no_test = issue(7, [mvi.APPROVED])
        no_test["body"] = "no test command"
        routes[("GET", "repos/o/r/issues?")] = [no_test]
        runner = FakeRunner(routes)
        self.assertEqual(self.run_worker(runner), 0)
        self.assertEqual(runner.added_labels(), [mvi.BLOCKED])
        self.assertFalse(any(args[0] in ("git", "sudo") for args, _, _ in runner.calls))

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


REVIEW_BODY = ("add greet\n\n### 受入条件\n- [ ] greet() returns Hello\n- [x] tests added\n\n"
               "### 非目標\n- nothing else\n")


class ReviewParseTest(unittest.TestCase):
    def test_acceptance_criteria_section_only(self):
        self.assertEqual(ex.acceptance_criteria(REVIEW_BODY), ["greet() returns Hello", "tests added"])
        self.assertEqual(ex.acceptance_criteria("no criteria"), [])

    def test_verdicts_missing_line_is_unknown(self):
        text = "notes\nAC1: PASS — greet.py:2\n**AC3**: FAIL - nope\n"
        self.assertEqual(ex.review_verdicts(text, 3), [
            ("PASS", "greet.py:2"), ("UNKNOWN", "(no verdict from the reviewer)"), ("FAIL", "nope")])
        self.assertEqual(ex.review_verdicts("AC1: **pass** — a.py:1", 1), [("PASS", "a.py:1")])
        self.assertEqual(ex.review_verdicts("AC1: FAIL — x\nAC1: PASS — y", 1),
                         [("UNKNOWN", "(conflicting verdicts from the reviewer)")])
        # An empty evidence must not swallow the next line (which hid the conflict).
        self.assertEqual(ex.review_verdicts("AC1: PASS\nAC1: FAIL — no test", 1),
                         [("UNKNOWN", "(conflicting verdicts from the reviewer)")])
        self.assertEqual(ex.review_verdicts("AC1: PASS\nAC2: PASS — y", 2), [("PASS", ""), ("PASS", "y")])
        for text in ("AC1: PASSED", "AC1: not PASS", "AC1 PASS"):
            self.assertEqual(ex.review_verdicts(text, 1)[0][0], "UNKNOWN", text)


class ReviewOnceTest(unittest.TestCase):
    def routes(self, comments=None, body=REVIEW_BODY):
        implemented = issue(7, [mvi.APPROVED, mvi.IMPLEMENTED])
        implemented["body"] = body
        return {
            ("GET", "repos/o/r/issues?"): [implemented, issue(8, [mvi.APPROVED, mvi.IMPLEMENTED,
                                                                 mvi.REVIEWED])],
            ("GET", "repos/o/r/pulls?"): [{"number": 70}],
            ("DIFF", "repos/o/r/pulls/70"): "diff --git a/greet.py b/greet.py\n+def greet(): ...\n",
            ("GET", "repos/o/r/pulls/70"): {"number": 70, "head": {"sha": "abc123"}},
            ("GET", "user"): {"login": "runner"},
            ("GET", "repos/o/r/issues/70/comments"): comments if comments is not None else [
                {"user": {"login": "runner"},
                 "body": mvi.TEST_REPORT_PREFIX + "\n- tested commit: `abc123`\n- exit code: 0"},
                {"user": {"login": "outsider"}, "body": mvi.TEST_REPORT_PREFIX + " FAKE PASS"}],
        }

    def run_review(self, runner):
        with tempfile.TemporaryDirectory() as workdir:
            return mvi.Worker("o/r", run=runner, log=lambda _: None).review_once(workdir)

    def test_review_posts_table_and_marks_reviewed(self):
        runner = FakeRunner(self.routes(), outputs={
            "stream-json": stream("AC1: PASS — greet.py:1\nAC2: FAIL — no test file")})
        self.assertEqual(self.run_review(runner), 0)
        claude = next(args for args, _, _ in runner.calls if "stream-json" in args)
        self.assertEqual(claude[claude.index("--tools") + 1], "Read,Grep,Glob")
        settings = json.loads(next(a for a in claude if a.startswith("--settings="))[11:])
        for tool in ("Bash", "Edit", "Write", "WebFetch"):
            self.assertIn(tool, settings["permissions"]["deny"])
        prompt = next(inp for args, _, inp in runner.calls if "stream-json" in args)
        self.assertIn("AC1: greet() returns Hello", prompt)
        self.assertIn("+def greet()", prompt)
        self.assertIn("exit code: 0", prompt)
        self.assertNotIn("FAKE PASS", prompt)  # only the runner's own test comment is used
        self.assertNotIn("nothing else", prompt)  # non-goals are not criteria
        report = runner.api_calls("POST", "repos/o/r/issues/70/comments")[0]["body"]
        self.assertIn("reviewed head: `abc123`", report)
        self.assertIn("| AC2 | tests added | **FAIL** | no test file |", report)
        self.assertIn("not PASS: 1 of 2", report)
        self.assertIn("runner test result: for this head", report)
        self.assertNotIn("WARNING", prompt)
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.REVIEWED])
        self.assertEqual(runner.removed_labels(), ["kagari%3Arunning"])
        add = next(args for args, _, _ in runner.calls if "--detach" in args)
        self.assertEqual(add[-1], "abc123")

    def test_missing_criteria_blocks(self):
        runner = FakeRunner(self.routes(body="no criteria here"))
        self.assertEqual(self.run_review(runner), 1)
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.BLOCKED])
        self.assertFalse(any("stream-json" in args for args, _, _ in runner.calls))

    def test_no_runner_test_comment_is_stated(self):
        runner = FakeRunner(self.routes(comments=[]), outputs={"stream-json": stream("AC1: UNKNOWN — x")})
        self.assertEqual(self.run_review(runner), 0)
        prompt = next(inp for args, _, inp in runner.calls if "stream-json" in args)
        self.assertIn("no runner test result", prompt)
        self.assertIn("WARNING", prompt)
        report = runner.api_calls("POST", "repos/o/r/issues/70/comments")[0]["body"]
        self.assertIn("missing or for another commit", report)

    def test_stale_test_result_and_errored_reviewer_are_reported(self):
        stale = [{"user": {"login": "runner"},
                  "body": mvi.TEST_REPORT_PREFIX + "\n- tested commit: `old999`\n- exit code: 0"}]
        runner = FakeRunner(self.routes(comments=stale),
                            outputs={"stream-json": stream("AC1: PASS — a", is_error=True)})
        self.assertEqual(self.run_review(runner), 0)
        prompt = next(inp for args, _, inp in runner.calls if "stream-json" in args)
        self.assertIn("WARNING", prompt)
        report = runner.api_calls("POST", "repos/o/r/issues/70/comments")[0]["body"]
        self.assertIn("missing or for another commit", report)
        self.assertIn("reviewer session: ended with an error", report)

    def test_nothing_to_review(self):
        runner = FakeRunner({("GET", "repos/o/r/issues?"): [issue(8, [mvi.APPROVED, mvi.IMPLEMENTED,
                                                                         mvi.REVIEWED])]})
        self.assertEqual(self.run_review(runner), 0)
        self.assertEqual(len(runner.calls), 1)


if __name__ == "__main__":
    unittest.main()
