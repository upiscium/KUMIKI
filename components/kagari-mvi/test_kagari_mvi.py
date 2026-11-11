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


def labeled(login, name=mvi.APPROVED):
    return {"event": "labeled", "label": {"name": name}, "actor": {"login": login}}


class FakeRunner:
    """Answers `gh api` calls from a route table and records every call."""

    def __init__(self, routes, fail=None, outputs=None):
        self.routes = routes  # {(method, path-prefix): response}
        self.fail = fail or {}  # {argv-substring: output}
        self.outputs = outputs if outputs is not None else {
            "stream-json": stream("done\nstatus: COMPLETED"), "status --porcelain": " M a.py"}
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
        method, path = args[3], args[6]
        for (route_method, prefix), response in self.routes.items():
            if route_method == method and path.startswith(prefix):
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


class ParseTest(unittest.TestCase):
    def test_test_command_heading_and_fence(self):
        self.assertEqual(mvi.test_command(TEST_BODY % 1), "python3 -m unittest")
        self.assertEqual(mvi.test_command("## Test command\n\n```\n\njust test\nmore\n```"), "just test")
        self.assertIsNone(mvi.test_command("no heading\n```\nmake\n```"))
        self.assertIsNone(mvi.test_command("### テストコマンド\n### 次\n```\nmake\n```"))

    def test_agent_outcome(self):
        done = mvi.agent_outcome(stream("ok\nstatus: COMPLETED", denials=2), False)
        self.assertEqual((done["completed"], done["status"], done["denials"], done["auth"]),
                         (True, "COMPLETED", 2, False))
        blocked = mvi.agent_outcome(stream("status: BLOCKED: needs X"), False)
        self.assertEqual((blocked["completed"], blocked["status"]), (False, "BLOCKED: needs X"))
        self.assertTrue(mvi.agent_outcome("Not logged in · Please run /login", True)["auth"])
        self.assertFalse(mvi.agent_outcome(stream("401 in code\nstatus: COMPLETED"), False)["auth"])


class RunOnceTest(unittest.TestCase):
    def routes(self, existing_pr=None):
        return {
            ("GET", "repos/o/r/issues?"): [issue(7, [mvi.APPROVED])],
            ("GET", "repos/o/r/issues/7/timeline"): [labeled("upiscium")],
            ("GET", "repos/o/r/pulls?"): [existing_pr] if existing_pr else [],
            ("GET", "repos/o/r/labels"): [],
            ("POST", "repos/o/r/pulls"): {"number": 70, "html_url": "https://x/pull/70"},
            ("GET", "repos/o/r"): {"default_branch": "main"},
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
        self.assertEqual(len(argvs), 2)  # Claude, then the runner's own test run
        claude, test = argvs
        self.assertEqual(claude[:6], ["sudo", "-n", "-u", "kagari-agent", "-H", "--"])
        self.assertIn("CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1", claude)
        for flag in ("--strict-mcp-config", "--setting-sources=project,local", "stream-json"):
            self.assertIn(flag, claude)
        self.assertNotIn("--bare", claude)
        settings = json.loads(next(a for a in claude if a.startswith("--settings="))[11:])
        self.assertIn("Bash(python3 -m unittest)", settings["permissions"]["allow"])
        self.assertEqual(test[-3:], ["sh", "-c", "exec 2>&1; python3 -m unittest"])
        commit = next(args for args, _, _ in runner.calls if "commit" in args)
        self.assertIn("--git-dir", commit)
        self.assertIn("core.hooksPath=/dev/null", commit)
        self.assertFalse(any(args[0] == "git" and "push" in args and "core.hooksPath=/dev/null" not in args
                             for args, _, _ in runner.calls))
        pulls = runner.api_calls("POST", "repos/o/r/pulls")
        self.assertEqual(len(pulls), 1)
        self.assertTrue(pulls[0]["draft"])
        self.assertIn("Closes #7", pulls[0]["body"])
        report = self.comments(runner, 70)[0]
        self.assertIn("exit code: 0", report)
        self.assertIn("not reported by Claude", report)
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.IMPLEMENTED])
        self.assertEqual(runner.removed_labels(), ["kagari%3Arunning"])

    def test_failing_test_creates_pr_then_blocks(self):
        runner = FakeRunner(self.routes(), fail={"sh -c exec 2>&1; python3 -m unittest": "FAILED (failures=1)"})
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
        self.assertIn("kagari-agent の再ログインが必要", self.comments(runner, 7)[0])

    def test_no_changes_blocks_without_pr(self):
        runner = FakeRunner(self.routes())
        runner.outputs["status --porcelain"] = ""
        self.assertEqual(self.run_worker(runner), 1)
        self.assertEqual(runner.api_calls("POST", "repos/o/r/pulls"), [])
        self.assertIn("no changes", self.comments(runner, 7)[0])

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

    def test_existing_pr_is_reused(self):
        runner = FakeRunner(self.routes(existing_pr={"number": 70, "html_url": "u"}))
        self.assertEqual(self.run_worker(runner, "/nonexistent"), 0)
        self.assertEqual(runner.api_calls("POST", "repos/o/r/pulls"), [])
        self.assertFalse(any(args[0] in ("git", "sudo") for args, _, _ in runner.calls))
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.IMPLEMENTED])

    def test_no_candidate_is_a_noop(self):
        runner = FakeRunner({("GET", "repos/o/r/issues?"): [issue(1, [mvi.APPROVED, mvi.BLOCKED])]})
        self.assertEqual(self.run_worker(runner, "/nonexistent"), 0)
        self.assertEqual(len(runner.calls), 1)


if __name__ == "__main__":
    unittest.main()
