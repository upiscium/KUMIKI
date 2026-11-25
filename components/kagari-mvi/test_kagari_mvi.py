"""Tests for kagari_mvi: Issue selection, approval, body parsing, TSUMGI and file sizes.

Run: python3 components/kagari-mvi/test_kagari_mvi.py  (runs every test_*.py here; or: just mvi::test)
run-once is in test_kagari_mvi_run.py and review-once in test_kagari_mvi_review.py.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kagari_mvi as mvi  # noqa: E402
import kagari_mvi_exec as ex  # noqa: E402
import kagari_mvi_tsumgi as ts  # noqa: E402
from kagari_mvi_testkit import TEST_BODY, REVIEW_BODY, issue, stream, labeled, edited, FakeRunner  # noqa: E402


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


class ReviewParseTest(unittest.TestCase):
    def test_acceptance_criteria_section_only(self):
        self.assertEqual(ex.acceptance_criteria(REVIEW_BODY), ["greet() returns Hello", "tests added"])
        self.assertEqual(ex.acceptance_criteria("no criteria"), [])

    def test_rules_let_a_criterion_without_a_test_pass_only_via_untested_and_the_diff(self):
        agent, review = (" ".join(rules.split()) for rules in (ex.AGENT_RULES, ex.REVIEW_RULES))
        self.assertIn("`AC<n>: <why no test can check it>`", agent)  # the implementer says which and why
        self.assertIn("A criterion a test could check needs a test.", agent)
        self.assertIn("listed under untested with a reason, no test could reasonably check it", review)
        self.assertIn("and you judged it PASS from the diff and files", review)
        self.assertIn("a criterion a test could check has no test", review)  # an excuse in untested: FAIL
        self.assertIn("or a criterion has neither", review)  # no test and no untested entry: FAIL
        self.assertNotIn("every criterion has a test;", review)

    def test_agent_rules_require_varying_the_condition_and_a_reason_for_no_tests(self):
        agent = " ".join(ex.AGENT_RULES.split())
        self.assertIn("the test must actually change that condition", agent)
        self.assertIn("run both with and without it in a child process", agent)
        self.assertIn("checks once inside a single condition does not count", agent)
        self.assertIn("write `- none: <reason>` under `tests:` as a list item (never leave it empty)", agent)
        self.assertNotIn("write `none: <reason>`", agent)

    def test_agent_rules_limit_tests_to_what_the_test_command_runs(self):
        agent = " ".join(ex.AGENT_RULES.split())
        self.assertIn("Under `tests:` write only tests that the Issue's test command actually runs.", agent)
        self.assertIn("Do not write a check the test command does not run", agent)

    def test_agent_rules_put_hand_checks_under_untested(self):
        agent = " ".join(ex.AGENT_RULES.split())
        self.assertIn("Write a check you did by hand under `untested:` as "
                      "`AC<n>: checked by hand with <command>`", agent)

    def test_test_purposes_keeps_a_bulleted_none_reason(self):
        got = ex.test_purposes("done.\ntests:\n- none: docs only, no test can check README text\n"
                               "untested: AC1: text\nstatus: COMPLETED")
        self.assertIn("- none: docs only, no test can check README text", got)

    def test_agent_rules_keep_the_status_and_untested_instructions(self):
        agent = " ".join(ex.AGENT_RULES.split())
        self.assertIn("untested: <what the tests do not check, or none>", agent)
        self.assertIn("- <test name>: <what it is meant to guarantee> (AC<n>)", agent)
        self.assertIn("End your last message with one line: `status: COMPLETED` if the Issue is done "
                      "and the test command passes, otherwise `status: BLOCKED: <reason>`.", agent)

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


if __name__ == "__main__":  # run every test_*.py here, as `just mvi::test` does
    unittest.main(module=None, argv=[sys.argv[0], "discover", "-s",
                                     os.path.dirname(os.path.abspath(__file__)), "-p", "test_*.py"])
