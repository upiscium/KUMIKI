"""Minimal tests for kagari_mvi with gh/git replaced by a fake runner.

Run: python3 components/kagari-mvi/test_kagari_mvi.py  (or: just mvi::test)
"""
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
import kagari_mvi_tsumgi as ts  # noqa: E402


PLACEMENT = ("### 全体ロジック\nrun-once の流れ\n\n### 置く理由\n同じ段で止められる\n\n"
             "### 却下した置き場所\n- review-once：実装の後になる\n\n")
TEST_BODY = "body %d\n\n" + PLACEMENT + "### テストコマンド\n```sh\npython3 -m unittest\n```\n"


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


class TsumgiTest(unittest.TestCase):
    def test_over_estimated_limit_is_not_sent_and_recorded(self):
        runner = FakeRunner({})
        with tempfile.TemporaryDirectory() as tmp:
            record = os.path.join(tmp, "logs", "o__r", "x.jsonl")
            big = "x" * (ts.max_input_bytes() + 1)
            reply = ts.chat(runner, "rules", big, "summary", record)
            self.assertFalse(reply["sent"])
            self.assertIn("too large for TSUMGI", reply["error"])
            self.assertEqual(reply["text"], "")
            self.assertEqual(runner.calls, [])
            with open(record) as handle:
                entry = json.loads(handle.read())
            self.assertEqual((entry["purpose"], entry["sent"], entry["input_tokens"]), ("summary", False, None))
            self.assertGreater(entry["estimated_input_tokens"], ts.INPUT_TOKEN_LIMIT * ts.MARGIN)

    def test_tokens_are_recorded_each_call(self):
        out = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
                          "usage": {"prompt_tokens": 120, "completion_tokens": 7}})
        runner = FakeRunner({}, outputs={"chat/completions": out})
        with tempfile.TemporaryDirectory() as tmp:
            record = os.path.join(tmp, "u.jsonl")
            for _ in range(2):
                reply = ts.chat(runner, "rules", "hello", "classify", record)
            self.assertEqual((reply["text"], reply["input_tokens"], reply["output_tokens"]), ("ok", 120, 7))
            with open(record) as handle:
                entries = [json.loads(line) for line in handle]
            self.assertEqual(len(entries), 2)
            self.assertEqual((entries[0]["input_tokens"], entries[0]["output_tokens"]), (120, 7))
            self.assertIsNone(entries[0]["text"])  # the record keeps counts, not the answer

    def test_unclosed_reasoning_is_an_error_not_an_answer(self):
        out = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "<think>AC1: PASS"}}]})
        reply = ts.chat(FakeRunner({}, outputs={"chat/completions": out}), "rules", "hi", "review")
        self.assertEqual(reply["text"], "")
        self.assertIn("reasoning was not closed", reply["error"])

    def test_bad_estimate_setting_is_an_error_result(self):
        reply = ts.chat(FakeRunner({}), "rules", "hi", "review", bytes_per_token=0)
        self.assertFalse(reply["sent"])
        self.assertIsNotNone(reply["error"])

    def test_estimate_settings(self):
        self.assertEqual(ts.estimate_tokens("あ" * 10, bytes_per_token=3), 10)
        self.assertEqual(ts.max_input_bytes(bytes_per_token=2, margin=0.5), ts.INPUT_TOKEN_LIMIT)


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


REVIEW_BODY = ("add greet\n\n### 受入条件\n- [ ] greet() returns Hello\n- [x] tests added\n\n"
               "### 非目標\n- nothing else\n")


class ReviewParseTest(unittest.TestCase):
    def test_acceptance_criteria_section_only(self):
        self.assertEqual(ex.acceptance_criteria(REVIEW_BODY), ["greet() returns Hello", "tests added"])
        self.assertEqual(ex.acceptance_criteria("no criteria"), [])

    def test_tests_verdict_line_missing_or_conflicting_is_unknown(self):
        self.assertEqual(ex.tests_verdict("AC1: PASS — a\n**TESTS**: FAIL — test_a checks nothing"),
                         ("FAIL", "test_a checks nothing"))
        self.assertEqual(ex.tests_verdict("AC1: PASS — a"), ("UNKNOWN", "(no TESTS verdict from the reviewer)"))
        self.assertEqual(ex.tests_verdict("TESTS: PASS — x\nTESTS: FAIL — y")[0], "UNKNOWN")
        self.assertEqual(ex.tests_verdict("TESTS: PASS — x\nsummary\nTESTS: PASS — y"), ("PASS", "y"))
        self.assertEqual(ex.tests_verdict("TESTS: PASSED")[0], "UNKNOWN")

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

    def run_tsumgi(self, runner):
        with tempfile.TemporaryDirectory() as workdir:
            return mvi.Worker("o/r", run=runner, log=lambda _: None, reviewer="tsumgi").review_once(workdir)

    def test_leftover_review_dir_is_reopened_as_agent_and_deleted(self):
        runner = FakeRunner(self.routes())
        with tempfile.TemporaryDirectory() as workdir:
            review_dir = os.path.join(workdir, "wt", "o__r", "issue-7.review")
            os.makedirs(os.path.join(review_dir, ".npm-cache"))
            mvi.Worker("o/r", run=runner, log=lambda _: None).review_once(workdir)
            self.assertFalse(os.path.exists(review_dir))
        joined = [" ".join(args) for args, _, _ in runner.calls]
        self.assertIn("sudo -n -u kagari-agent -H -- find %s -user kagari-agent -type d "
                      "-exec chmod u+rwx,g+rwx {} ;" % review_dir, joined)
        self.assertFalse(any("worktree remove" in j for j in joined))

    def test_leftover_review_dir_that_cannot_be_reopened_blocks(self):
        runner = FakeRunner(self.routes(), fail={"-exec chmod u+rwx,g+rwx": "find: 'x': Permission denied"})
        with tempfile.TemporaryDirectory() as workdir:
            os.makedirs(os.path.join(workdir, "wt", "o__r", "issue-7.review"))
            self.assertEqual(mvi.Worker("o/r", run=runner, log=lambda _: None).review_once(workdir), 1)
        self.assertIn(mvi.BLOCKED, runner.added_labels())
        self.assertNotIn(mvi.REVIEWED, runner.added_labels())
        body = runner.api_calls("POST", "repos/o/r/issues/7/comments")[0]["body"]
        self.assertIn("`checkout`", body)
        self.assertIn("Permission denied", body)
        self.assertFalse(any("stream-json" in args for args, _, _ in runner.calls))

    def test_tsumgi_review_posts_one_request_and_table(self):
        reply = json.dumps({"choices": [{"message": {
            "content": "<think>x</think>AC1: PASS — greet.py:1\nAC2: UNKNOWN — no test shown\n"
                       "TESTS: PASS — test_greet matches AC1"}}]})
        runner = FakeRunner(self.routes(), outputs={"chat/completions": reply})
        self.assertEqual(self.run_tsumgi(runner), 0)
        self.assertFalse(any("stream-json" in args for args, _, _ in runner.calls))
        curl = [(args, inp) for args, _, inp in runner.calls if args[0] == "curl"]
        self.assertEqual(len(curl), 1)
        self.assertEqual(curl[0][0][-1], ex.TSUMGI_URL + "/chat/completions")
        body = json.loads(curl[0][1])
        self.assertEqual(body["model"], ex.TSUMGI_MODEL)
        self.assertIn("You cannot read files.", body["messages"][0]["content"])
        self.assertIn("+def greet()", body["messages"][1]["content"])
        report = runner.api_calls("POST", "repos/o/r/issues/70/comments")[0]["body"]
        self.assertIn("TSUMGI `%s`" % ex.TSUMGI_MODEL, report)
        self.assertIn("| AC1 | greet() returns Hello | **PASS** | greet.py:1 |", report)
        self.assertIn("| TESTS | テストの目的（申告）と受入条件の対応 | **PASS** | test_greet matches AC1 |", report)
        self.assertIn("TESTS: PASS|FAIL|UNKNOWN", body["messages"][0]["content"])
        self.assertIn("reviewer session: ok", report)
        self.assertIn(mvi.REVIEWED, runner.added_labels())

    def test_tsumgi_large_pr_is_not_sent_and_unknown(self):
        routes = self.routes()
        routes[("DIFF", "repos/o/r/pulls/70")] = "+" + "x" * ex.TSUMGI_MAX_BYTES
        runner = FakeRunner(routes)
        self.assertEqual(self.run_tsumgi(runner), 0)
        self.assertFalse(any(args[0] == "curl" for args, _, _ in runner.calls))
        report = runner.api_calls("POST", "repos/o/r/issues/70/comments")[0]["body"]
        self.assertIn("too large for TSUMGI", report)
        self.assertIn("not PASS: 2 of 2", report)
        self.assertIn("reviewer session: ended with an error", report)

    def test_tsumgi_http_error_is_reported_not_blocked(self):
        runner = FakeRunner(self.routes(), fail={"chat/completions": '{"code":"context_length_exceeded"}'})
        self.assertEqual(self.run_tsumgi(runner), 0)
        report = runner.api_calls("POST", "repos/o/r/issues/70/comments")[0]["body"]
        self.assertIn("reviewer session: ended with an error", report)
        self.assertIn("not PASS: 2 of 2", report)
        self.assertIn("context_length_exceeded", report)  # TSUMGI's error body, not only curl's
        self.assertIn(mvi.REVIEWED, runner.added_labels())

    def test_tsumgi_cut_off_or_unclosed_reasoning_is_not_a_verdict(self):
        for choice in ({"finish_reason": "length", "message": {"content": "AC1: PASS — x"}},
                       {"finish_reason": "stop", "message": {"content": "<think>AC1: PASS — draft"}}):
            runner = FakeRunner(self.routes(), outputs={"chat/completions": json.dumps({"choices": [choice]})})
            self.assertEqual(self.run_tsumgi(runner), 0)
            report = runner.api_calls("POST", "repos/o/r/issues/70/comments")[0]["body"]
            self.assertIn("not PASS: 2 of 2", report)

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
        self.assertIn("| TESTS | テストの目的（申告）と受入条件の対応 | **UNKNOWN** | (no TESTS verdict", report)
        self.assertIn("TESTS: PASS|FAIL|UNKNOWN", next(a for a in claude if "independent reviewer" in a))
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


class LineLimitTest(unittest.TestCase):
    """ADR-0010 追補 2026-11-16: per file, 500 lines of code and 800 lines of tests."""

    def test_every_python_file_is_within_its_limit(self):
        here = os.path.dirname(os.path.abspath(__file__))
        names = sorted(n for n in os.listdir(here) if n.endswith(".py"))
        self.assertIn("kagari_mvi.py", names)
        for name in names:
            limit = 800 if name.startswith("test_") else 500
            with open(os.path.join(here, name), encoding="utf-8") as handle:
                lines = sum(1 for _ in handle)
            with self.subTest(file=name):
                self.assertLessEqual(lines, limit, "%s has %d lines (limit %d): split it by "
                                     "responsibility; do not raise the limit" % (name, lines, limit))


if __name__ == "__main__":
    unittest.main()
