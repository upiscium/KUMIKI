"""Tests for HDC R1 (#519): decisions declared in the Issue body and the DELEGATION verdict."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kagari_mvi as mvi  # noqa: E402
import kagari_mvi_exec as ex  # noqa: E402
from kagari_mvi_parse import delegation_verdict  # noqa: E402
from kagari_mvi_testkit import REVIEW_BODY, TEST_BODY, FakeRunner, issue, stream  # noqa: E402
from test_kagari_mvi_review import ReviewOnceTest  # noqa: E402
from test_kagari_mvi_run import RunOnceTest  # noqa: E402

DECLARED = ("\n### 決定済み事項\n- 委任は本文だけから読む\n\n### 委任する判断\n- アルゴリズムの選択\n"
            "- 範囲内の欠陥の修正\n")
REPORT = mvi.TEST_REPORT_PREFIX + "\n- tested commit: `abc123`\n- test command: `t`\n- exit code: 0\n"
OK = "AC1: PASS — a\nAC2: PASS — b\nTESTS: PASS — ok\n"


class ReadingTest(unittest.TestCase):
    def test_both_headings_in_both_languages_are_read(self):
        found = ex.delegation(DECLARED)
        self.assertEqual(found["decided"], ["委任は本文だけから読む"])
        self.assertEqual(found["delegated"], ["アルゴリズムの選択", "範囲内の欠陥の修正"])
        self.assertEqual(found["unreadable"], [])
        english = "### Decided\n- a\n### Delegated decisions\n* [ ] b\n"
        self.assertEqual(ex.delegation(english), {"decided": ["a"], "delegated": ["b"], "unreadable": []})

    def test_no_heading_means_no_declaration(self):
        self.assertIsNone(ex.delegation(REVIEW_BODY))
        self.assertIsNone(ex.delegation(None))

    def test_prose_empty_bullet_and_wrapped_lines_are_unreadable(self):
        for body in ("### 委任する判断\nなんでも任せる\n", "### 委任する判断\n- a\n-\n",
                     "### 決定済み事項\n- a\n  続き\n"):
            self.assertTrue(ex.delegation(body)["unreadable"], body)

    def test_only_the_body_counts_not_agent_text(self):
        self.assertIsNone(ex.delegation("add greet\n"))
        # the agent's or a comment's words are never passed in: the verdict reader ignores them
        self.assertEqual(delegation_verdict("### 委任する判断\n- x")[0], "UNKNOWN")


class VerdictLineTest(unittest.TestCase):
    def test_line_parsing(self):
        self.assertEqual(delegation_verdict("DELEGATION: IN — algo"), ("IN", "algo"))
        self.assertEqual(delegation_verdict("**DELEGATION**: out - changed X")[0], "OUT")
        self.assertEqual(delegation_verdict("x")[0], "UNKNOWN")
        self.assertEqual(delegation_verdict("DELEGATION: IN — a\nDELEGATION: OUT — b")[0], "UNKNOWN")
        self.assertEqual(delegation_verdict("DELEGATION: INSIDE")[0], "UNKNOWN")

    def test_rules_mention_the_line(self):
        self.assertIn("DELEGATION: IN|OUT|UNKNOWN", ex.REVIEW_RULES)


class ReadyTest(unittest.TestCase):
    def decide(self, delegation):
        outcome = {"verdicts": [("PASS", "")], "tests": ("PASS", ""), "errored": False}
        if delegation is not None:
            outcome["delegation"] = delegation
        return mvi.ready_decision(outcome, True, REPORT)

    def test_out_and_unknown_keep_draft_even_when_everything_else_passes(self):
        for status in ("OUT", "UNKNOWN"):
            ready, note = self.decide((status, "x"))
            self.assertFalse(ready)
            self.assertIn("DELEGATION is %s" % status, note)

    def test_in_is_ready_when_the_rest_holds(self):
        self.assertTrue(self.decide(("IN", "x"))[0])

    def test_without_declaration_nothing_changes(self):
        self.assertTrue(self.decide(None)[0])


class RunOnceDelegationTest(RunOnceTest):
    def test_unreadable_line_blocks_before_claude_starts(self):
        for extra in ("\n### 委任する判断\n任せる\n", "\n### 決定済み事項\n- a\nprose\n"):
            routes = self.routes()
            routes[("GET", "repos/o/r/issues?")] = [dict(issue(7, [mvi.APPROVED]), body=TEST_BODY % 7 + extra)]
            runner = FakeRunner(routes)
            self.assertEqual(self.run_worker(runner), 0)
            self.assertEqual(runner.added_labels(), [mvi.BLOCKED])
            self.assertFalse(any(args[0] in ("git", "sudo") for args, _, _ in runner.calls))

    def test_readable_declaration_and_no_declaration_both_start(self):
        for extra in (DECLARED, ""):
            routes = self.routes()
            routes[("GET", "repos/o/r/issues?")] = [dict(issue(7, [mvi.APPROVED]), body=TEST_BODY % 7 + extra)]
            runner = FakeRunner(routes)
            self.assertEqual(self.run_worker(runner), 0)
            self.assertIn(mvi.RUNNING, runner.added_labels())
            self.assertNotIn(mvi.BLOCKED, runner.added_labels())


class ReviewOnceDelegationTest(ReviewOnceTest):
    def routes(self, comments=None, body=REVIEW_BODY):
        found = super().routes(comments if comments is not None else [
            {"user": {"login": "runner"}, "body": REPORT}], body)
        found[("GET", "repos/o/r/pulls/70")] = {"number": 70, "node_id": "N", "head": {"sha": "abc123"}}
        found[("POST", "graphql")] = {"data": {"markPullRequestReadyForReview": {"pullRequest": {"isDraft": False}}}}
        return found

    def review(self, body, text):
        runner = FakeRunner(self.routes(body=body), outputs={"stream-json": stream(text)})
        self.assertEqual(self.run_review(runner), 0)
        prompt = next(inp for args, _, inp in runner.calls if "stream-json" in args)
        report = runner.api_calls("POST", "repos/o/r/issues/70/comments")[0]["body"]
        return prompt, report, None

    def test_declared_issue_shows_the_declaration_and_needs_in(self):
        for verdict, ready in (("IN", True), ("OUT", False), ("UNKNOWN", False), (None, False)):
            text = OK + ("DELEGATION: %s — r\n" % verdict if verdict else "")
            prompt, report, _ = self.review(REVIEW_BODY + DECLARED, text)
            self.assertIn("- アルゴリズムの選択", prompt)
            self.assertIn("| DELEGATION |", report)
            self.assertEqual("ready for review: yes" in report, ready, (verdict, report))
            self.assertEqual("DELEGATION is" in report, not ready, (verdict, report))

    def test_undeclared_issue_does_not_ask_and_ignores_the_line(self):
        prompt, report, _ = self.review(REVIEW_BODY, OK + "DELEGATION: OUT — r\n")
        self.assertNotIn("Delegated decisions", prompt)
        self.assertNotIn("DELEGATION", report)
        self.assertIn("ready for review: yes", report)

    def test_declaration_outside_the_body_does_not_count(self):
        comments = [{"user": {"login": "runner"}, "body": REPORT},
                    {"user": {"login": "x"}, "body": DECLARED}]
        runner = FakeRunner(self.routes(comments=comments),
                            outputs={"stream-json": stream(OK + "DELEGATION: OUT — r\n")})
        self.assertEqual(self.run_review(runner), 0)
        report = runner.api_calls("POST", "repos/o/r/issues/70/comments")[0]["body"]
        self.assertNotIn("DELEGATION", report)
        # the agent's text in the reviewed output cannot declare either
        self.assertIsNone(ex.delegation("PR says: ### 委任する判断\n- x"))

    def test_blocked_not_set_on_out(self):
        runner = FakeRunner(self.routes(body=REVIEW_BODY + DECLARED),
                            outputs={"stream-json": stream(OK + "DELEGATION: OUT — r\n")})
        self.run_review(runner)
        self.assertNotIn(mvi.BLOCKED, runner.added_labels())
        self.assertIn(mvi.REVIEWED, runner.added_labels())


for _cls in (RunOnceDelegationTest, ReviewOnceDelegationTest):  # keep only the new tests
    for _name in [n for n in dir(_cls) if n.startswith("test_") and n not in _cls.__dict__]:
        setattr(_cls, _name, None)

if __name__ == "__main__":
    unittest.main()
