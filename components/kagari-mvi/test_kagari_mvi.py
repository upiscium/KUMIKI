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


def issue(number, labels, created="2026-11-01T00:00:00Z", pr=False, state="open"):
    data = {"number": number, "title": "Issue %d" % number, "body": "body %d" % number,
            "state": state, "created_at": created,
            "labels": [{"name": name} for name in labels]}
    if pr:
        data["pull_request"] = {"url": "x"}
    return data


def labeled(login, name=mvi.APPROVED):
    return {"event": "labeled", "label": {"name": name}, "actor": {"login": login}}


class FakeRunner:
    """Answers `gh api` calls from a route table and records every call."""

    def __init__(self, routes, fail=None):
        self.routes = routes  # {(method, path-prefix): response}
        self.fail = fail or {}  # {argv-substring: output}
        self.calls = []

    def __call__(self, args, cwd=None, input=None):
        self.calls.append((list(args), cwd, json.loads(input) if input else None))
        joined = " ".join(args)
        for needle, output in self.fail.items():
            if needle in joined:
                raise mvi.CommandError(args, 1, output)
        if args[:2] != ["gh", "api"]:
            return ""
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

    def test_failure_in_push_blocks_with_stage_and_tail(self):
        runner = FakeRunner(self.routes(), fail={"git push": "line1\n! [rejected] non-fast-forward"})
        with tempfile.TemporaryDirectory() as workdir:
            code = mvi.Worker("o/r", run=runner, log=lambda _: None).run_once(["upiscium"], workdir)
            with open(os.path.join(workdir, "wt", "issue-7", ".kagari-mvi", "issue-7.md")) as handle:
                self.assertEqual(handle.read(), "# Issue 7\n\nbody 7\n")
        self.assertEqual(code, 1)
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.BLOCKED])
        self.assertEqual(runner.removed_labels(), ["kagari%3Arunning"])
        body = runner.api_calls("POST", "repos/o/r/issues/7/comments")[0]["body"]
        self.assertIn("`push`", body)
        self.assertIn("non-fast-forward", body)
        self.assertEqual(runner.api_calls("POST", "repos/o/r/pulls"), [])

    def test_running_kept_when_blocked_cannot_be_added(self):
        runner = FakeRunner(self.routes(), fail={"git push": "rejected"})

        def call(args, cwd=None, input=None):
            if input and mvi.BLOCKED in input:
                raise mvi.CommandError(args, 1, "HTTP 503")
            return runner(args, cwd=cwd, input=input)

        worker = mvi.Worker("o/r", run=call, log=lambda _: None)
        with tempfile.TemporaryDirectory() as workdir:
            with self.assertRaises(mvi.CommandError):
                worker.run_once(["upiscium"], workdir)
        self.assertEqual(runner.removed_labels(), [])

    def test_success_creates_one_draft_pr(self):
        runner = FakeRunner(self.routes())
        with tempfile.TemporaryDirectory() as workdir:
            code = mvi.Worker("o/r", run=runner, log=lambda _: None).run_once(["upiscium"], workdir)
        self.assertEqual(code, 0)
        pulls = runner.api_calls("POST", "repos/o/r/pulls")
        self.assertEqual(len(pulls), 1)
        self.assertEqual(pulls[0]["title"], "Issue 7")
        self.assertEqual(pulls[0]["head"], "kagari/issue-7")
        self.assertTrue(pulls[0]["draft"])
        self.assertIn("body 7", pulls[0]["body"])
        self.assertIn("Closes #7", pulls[0]["body"])
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.IMPLEMENTED])
        self.assertEqual(runner.removed_labels(), ["kagari%3Arunning"])

    def test_existing_pr_is_reused(self):
        runner = FakeRunner(self.routes(existing_pr={"number": 70, "html_url": "u"}))
        code = mvi.Worker("o/r", run=runner, log=lambda _: None).run_once(["upiscium"], "/nonexistent")
        self.assertEqual(code, 0)
        self.assertEqual(runner.api_calls("POST", "repos/o/r/pulls"), [])
        self.assertFalse(any(args[0] == "git" for args, _, _ in runner.calls))
        self.assertEqual(runner.added_labels(), [mvi.RUNNING, mvi.IMPLEMENTED])

    def test_no_candidate_is_a_noop(self):
        runner = FakeRunner({("GET", "repos/o/r/issues?"): [issue(1, [mvi.APPROVED, mvi.BLOCKED])]})
        code = mvi.Worker("o/r", run=runner, log=lambda _: None).run_once(["upiscium"], "/nonexistent")
        self.assertEqual(code, 0)
        self.assertEqual(len(runner.calls), 1)


if __name__ == "__main__":
    unittest.main()
