"""Tests for permissions declared in an Issue body (#431): snapshot only, hook, blocking, comments."""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kagari_mvi as mvi  # noqa: E402
import kagari_mvi_declared as dec  # noqa: E402
import kagari_mvi_ledger as led  # noqa: E402
from kagari_mvi_testkit import TEST_BODY, FakeRunner, edited, issue, labeled  # noqa: E402

NOW = datetime.datetime(2026, 11, 28, 12, 0, tzinfo=datetime.timezone.utc)


def body(*lines, heading="### 追加で許可する操作"):
    return TEST_BODY % 7 + "\n" + heading + "\n" + "\n".join(lines) + "\n"


def snapshot(text, number=7, ledger_file=None):
    with tempfile.TemporaryDirectory() as tmp:
        path = ledger_file or os.path.join(tmp, "ledger.json")
        worker = mvi.Worker("o/r", run=FakeRunner({}), log=lambda _: None, ledger_path=path)
        grants, audit = os.path.join(tmp, "g.json"), os.path.join(tmp, "a.jsonl")
        worker.write_grants(grants, audit, number, text)
        return json.load(open(grants, encoding="utf-8"))["grants"], path


def allowed(grants, command):
    return led.decide(grants, "Bash", {"command": command}, NOW)[0]


class SnapshotTest(unittest.TestCase):
    def test_declared_grants_enter_only_this_issues_snapshot_and_not_the_ledger(self):  # AC1
        text = body("- bash: ls -l", "- bash: make build *")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ledger.json")
            led.save(path, [])
            before = open(path, encoding="utf-8").read()
            grants, _ = snapshot(text, 7, path)
            self.assertEqual([g["match"] for g in grants], ["ls -l", "make build *"])
            self.assertTrue(all(g["scope"] == "issue" and g["issue"] == 7 and g["repo"] == "o/r" for g in grants))
            self.assertEqual(open(path, encoding="utf-8").read(), before)  # the ledger file is unchanged
            self.assertEqual(led.load(path), [])
            self.assertEqual(snapshot(TEST_BODY % 7, 7, path)[0], [])  # the next run without it: nothing
            self.assertEqual(snapshot(None, 8, path)[0], [])  # another Issue: nothing
            self.assertEqual(led.for_run(grants, "o/r", 8, led.utcnow()), [])  # and the scope would not apply
            self.assertEqual(led.for_run(grants, "x/y", 7, led.utcnow()), [])

    def test_english_heading_and_backticks_are_read(self):  # AC1
        text = body("- bash: `ls -l`", heading="### Additional permissions")
        self.assertEqual(dec.matches(text), ["ls -l"])

    def test_hook_allows_declared_and_denies_everything_else(self):  # AC2
        grants, _ = snapshot(body("- bash: ls -l", "- bash: make build *"))
        for command in ("ls -l", "make build x"):
            self.assertEqual(allowed(grants, command), "allow")
        self.assertIsNone(allowed(grants, "ls -a"))
        self.assertIsNone(allowed(grants, "ls -l && rm x"))
        self.assertEqual(allowed(grants, "curl http://x"), "deny")
        self.assertEqual(allowed(grants, "git push origin x"), "deny")
        self.assertEqual(allowed(grants, "ls -l; curl x"), "deny")

    def test_no_declaration_changes_nothing(self):  # AC6
        self.assertIsNone(dec.declared(TEST_BODY % 7))
        self.assertEqual(dec.report([], []), "")
        self.assertEqual(snapshot(TEST_BODY % 7)[0], [])


class UnusableTest(unittest.TestCase):
    def test_each_unusable_line_is_a_problem(self):  # AC3
        for line in ("- bash: curl http://x", "- bash: gh issue view 1", "- bash: git push origin x",
                     "- ssh: web1", "- edit: /a/b/c", "just text", "- bash:", "- ", "-", "- bash: ls && rm x",
                     "- bash: *", "- bash: ls (scope=repo)", "- bash: ls --scope host", "- bash: ls リポジトリ全体",
                     "- bash: ls 'x"):
            with self.subTest(line=line):
                self.assertTrue(dec.problems(body(line)), line)
        self.assertFalse(dec.problems(body("- bash: ls -l", "- bash: make *")))


class RunOnceTest(unittest.TestCase):
    def routes(self, text, edited_at=None):
        found = issue(7, [mvi.APPROVED])
        found["body"] = text
        return {("GET", "repos/o/r/issues?"): [found], ("GET", "repos/o/r/issues/7/timeline"): [labeled("upiscium")],
                ("GET", "repos/o/r/pulls?"): [], ("GET", "repos/o/r/labels"): [],
                ("POST", "repos/o/r/pulls"): {"number": 70, "html_url": "https://x/pull/70"},
                ("GET", "repos/o/r"): {"default_branch": "main"}, ("POST", "graphql"): edited(edited_at)}

    def run_worker(self, runner):
        with tempfile.TemporaryDirectory() as tmp:
            worker = mvi.Worker("o/r", run=runner, log=lambda _: None, ledger_path=os.path.join(tmp, "l.json"))
            return worker.run_once(["upiscium"], tmp)

    def comments(self, runner, number):
        return [b["body"] for b in runner.api_calls("POST", "repos/o/r/issues/%d/comments" % number)]

    def test_unusable_declarations_block_before_claude_with_next(self):  # AC3
        for line in ("- bash: curl x", "- bash: ls (scope=host)", "- ssh: web1", "garbage", "- bash:"):
            with self.subTest(line=line):
                runner = FakeRunner(self.routes(body(line)))
                self.assertEqual(self.run_worker(runner), 0)
                self.assertFalse([a for a, _, _ in runner.calls if a[0] != "gh"])  # no Claude, no git
                self.assertEqual(runner.added_labels(), [mvi.BLOCKED])
                comment = self.comments(runner, 7)[0]
                self.assertIn("blocked [HUMAN_DECISION] before start", comment)
                self.assertIn("Next:", comment)

    def test_body_edited_after_approval_stops_the_run(self):  # AC4
        runner = FakeRunner(self.routes(body("- bash: ls -l"), edited_at="2026-11-14T02:00:00Z"))
        self.assertEqual(self.run_worker(runner), 0)
        self.assertEqual(runner.added_labels(), [mvi.BLOCKED])
        self.assertIn("was edited after", self.comments(runner, 7)[0])
        self.assertFalse([a for a, _, _ in runner.calls if a[0] != "gh"])

    def test_declaration_in_a_comment_is_not_read(self):  # AC5
        routes = self.routes(TEST_BODY % 7)
        routes[("GET", "repos/o/r/issues/7/comments")] = [{"body": "### 追加で許可する操作\n- bash: ls -l"}]
        runner = FakeRunner(routes)
        self.assertEqual(self.run_worker(runner), 0)
        claude = next(a for a, _, _ in runner.calls if "stream-json" in " ".join(a))
        self.assertFalse([c for c in runner.calls if c[0][:2] == ["gh", "api"] and "comments" in c[0][6]
                          and c[0][3] == "GET"])  # comments are never fetched
        self.assertNotIn("ls -l", " ".join(claude))
        self.assertEqual(dec.matches(TEST_BODY % 7), [])

    def test_report_lists_declared_and_used_operations(self):  # AC6
        runner = FakeRunner(self.routes(body("- bash: ls -l")))
        self.assertEqual(self.run_worker(runner), 0)
        report = self.comments(runner, 70)[0]
        self.assertIn("allowed by this Issue's declaration", report)
        self.assertIn("`ls -l`", report)
        text = dec.report(["ls -l"], [{"grant": "issue-7-decl-1", "tool": "Bash", "input": {"command": "ls -l"}},
                                      {"grant": "g1", "tool": "Bash", "input": {"command": "make"}}])
        self.assertIn("actually used", text)
        self.assertEqual(text.count("`ls -l`"), 2)
        self.assertNotIn("make", text)


if __name__ == "__main__":
    unittest.main()
