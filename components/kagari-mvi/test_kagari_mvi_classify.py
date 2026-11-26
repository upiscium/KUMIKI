"""Tests for the stop classification (HDC R1, #550): classify() and the blocked comments."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kagari_mvi as mvi  # noqa: E402
import kagari_mvi_exec as ex  # noqa: E402
import kagari_mvi_parse as parse  # noqa: E402
from kagari_mvi_classify import classify, FIX, STEPS  # noqa: E402
from kagari_mvi_testkit import issue, stream, labeled, edited, FakeRunner  # noqa: E402

H, O, T, U = "HUMAN_DECISION", "OPERATIONAL", "TECHNICAL", "UNKNOWN"


class ClassifyTest(unittest.TestCase):
    def test_blocked_messages(self):
        cases = [
            ("claude", "Claude Code failed to authenticate: kagari-agent needs to log in", O, STEPS["auth"]),
            ("commit", "Claude made no changes (status: BLOCKED: HUMAN_DECISION: pick A or B)", H, STEPS["decide"]),
            ("commit", "Claude made no changes (status: BLOCKED: OPERATIONAL: no token)", O, STEPS["env"]),
            ("commit", "Claude made no changes (status: BLOCKED: spec unclear)", T, STEPS["test"]),
            ("start", "The Issue body has no test command. Add a `### テストコマンド`", H, FIX),
            ("start", "The Issue body lacks placement sections (ADR-0011): `### 置く理由`.", H, FIX),
            ("start", "The Issue body was edited after `kagari:approved` was added", H, FIX),
            ("start", "The Issue's `### 決定済み事項` sections have lines that are not one-line bullets", H, FIX),
            ("start", "`kagari:approved` was added by `x`, who is not in the allow list (`y`).", H, FIX),
            ("claude", "The repository commits `.claude/settings.local.json`; it would widen", H, FIX),
            ("commit", "The changes touch files that need a human decision, so nothing was pushed", H, FIX),
            ("find-existing-pr", "PR #3 for `kagari/issue-7` already exists and", H, STEPS["pr"]),
            ("report", "Draft PR #3 was created, but status is `COMPLETED` and the test exit code is 1.", T,
             STEPS["test"]),
            ("report", "Draft PR #3 was created, but status is `BLOCKED: HUMAN_DECISION: pick A or B` and the "
             "test exit code is 0.", H, STEPS["decide"]),
            ("report", "Draft PR #3 was created, but status is `BLOCKED: HUMAN_DECISION: pick A or B` and the "
             "test exit code is 1.", H, STEPS["decide"]),
            ("report", "Draft PR #3 was created, but status is `BLOCKED: OPERATIONAL: no token` and the "
             "test exit code is 0.", O, STEPS["env"]),
            ("report", "Draft PR #3 was created, but status is `BLOCKED: OPERATIONAL: no token` and the "
             "test exit code is 1.", O, STEPS["env"]),
            ("report", "Draft PR #3 was created, but status is `BLOCKED: spec unclear` and the "
             "test exit code is 0.", T, STEPS["test"]),
            ("report", "Draft PR #3 was created, but status is `COMPLETED` and the test exit code is 0.", T,
             STEPS["test"]),
            ("find-pr", "No open PR for `kagari/issue-7` to review.", U, STEPS["unknown"]),
        ]
        for stage, text, kind, step in cases:
            with self.subTest(text=text):
                self.assertEqual(classify(stage, ex.Blocked(text), ""), (kind, step))
                self.assertEqual(classify(stage, ex.Blocked(text), text), (kind, step))

    def test_before_start_without_an_exception(self):
        self.assertEqual(classify("start", None, "anything"), (H, FIX))

    def test_temporary_words_are_operational_and_others_unknown(self):
        words = ["Internal Server Error", "Bad Gateway", "Service Unavailable", "Gateway Timeout", "HTTP 502",
                 "HTTP 503", "HTTP 504", "Could not resolve host", "Connection reset", "Connection timed out",
                 "timed out", "rate limit", "unexpected end of JSON input"]
        for word in words:
            for error in (ex.CommandError(["gh"], 1, "x " + word.upper() + " y"), RuntimeError(word.lower())):
                with self.subTest(word=word, error=type(error).__name__):
                    self.assertEqual(classify("draft-pr", error, str(error)), (O, STEPS["temp"]))
        self.assertEqual(classify("push", ex.CommandError(["git"], 1, "non-fast-forward"), "non-fast-forward"),
                         (U, STEPS["unknown"]))
        self.assertEqual(classify("x", ValueError("boom"), "ValueError: boom"), (U, STEPS["unknown"]))


class CommentTest(unittest.TestCase):
    def routes(self, body=None):
        item = issue(7, [mvi.APPROVED])
        if body is not None:
            item["body"] = body
        return {
            ("GET", "repos/o/r/issues?"): [item],
            ("GET", "repos/o/r/issues/7/timeline"): [labeled("upiscium")],
            ("GET", "repos/o/r/issues/7/comments"): [],
            ("GET", "repos/o/r/pulls?"): [],
            ("GET", "repos/o/r/labels"): [],
            ("POST", "repos/o/r/pulls"): {"number": 70, "html_url": "https://x/pull/70"},
            ("GET", "repos/o/r"): {"default_branch": "main"},
            ("POST", "graphql"): edited(None),
        }

    def run_once(self, runner):
        with tempfile.TemporaryDirectory() as workdir:
            code = mvi.Worker("o/r", run=runner, log=lambda _: None).run_once(["upiscium"], workdir)
        return code, [b["body"] for b in runner.api_calls("POST", "repos/o/r/issues/7/comments")]

    def test_stage_failure_comment_has_kind_tail_and_next(self):
        runner = FakeRunner(self.routes(), fail={"push origin": "line1\nHTTP 503 Service Unavailable"})
        code, posted = self.run_once(runner)
        self.assertEqual(code, 1)
        self.assertEqual(len(posted), 1)  # one failure, one comment
        lines = posted[0].split("\n")
        self.assertEqual(lines[0], "kagari-mvi: failed [OPERATIONAL] at stage `push`.")
        self.assertIn("HTTP 503 Service Unavailable", posted[0])
        self.assertEqual(lines[-1], "Next: " + STEPS["temp"])

    def test_json_truncation_is_operational_and_other_errors_unknown(self):
        runner = FakeRunner(self.routes(), fail={"push origin": "unexpected end of JSON input"})
        self.assertTrue(self.run_once(runner)[1][0].startswith("kagari-mvi: failed [OPERATIONAL]"))
        runner = FakeRunner(self.routes(), fail={"push origin": "rejected non-fast-forward"})
        self.assertTrue(self.run_once(runner)[1][0].startswith("kagari-mvi: failed [UNKNOWN]"))

    def test_blocked_stage_comment_and_denial_report_remain(self):
        runner = FakeRunner(self.routes())
        runner.outputs["stream-json"] = stream("status: BLOCKED: OPERATIONAL: no token", denials=1,
                                               denied=[{"tool_name": "Bash", "tool_input": {"command": "curl x"}}])
        runner.outputs["diff --cached --name-only"] = ""
        code, posted = self.run_once(runner)
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0].split("\n")[0], "kagari-mvi: blocked [OPERATIONAL] at stage `commit`.")
        self.assertIn("no token", posted[0])
        self.assertTrue(posted[0].endswith("Next: " + STEPS["env"]))

    def test_before_start_comment(self):
        runner = FakeRunner(self.routes(body="no test command here"))
        code, posted = self.run_once(runner)
        self.assertEqual(code, 0)
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0].split("\n")[0], "kagari-mvi: blocked [HUMAN_DECISION] before start.")
        self.assertIn("no test command", posted[0])
        self.assertEqual(posted[0].split("\n")[-1], "Next: " + FIX)

    def test_usage_limit_is_not_blocked_and_not_classified(self):
        from test_kagari_mvi_limit import limit_stream
        runner = FakeRunner(self.routes())
        runner.outputs["stream-json"] = limit_stream()
        code, posted = self.run_once(runner)
        self.assertEqual(code, 3)
        self.assertNotIn(mvi.BLOCKED, runner.added_labels())
        self.assertEqual(len(posted), 1)
        self.assertNotIn("[", posted[0].split("\n")[0])
        self.assertNotIn("Next:", posted[0])


class AgentRulesTest(unittest.TestCase):
    def test_rules_name_the_forms_and_status_is_read_as_is(self):
        self.assertIn("BLOCKED: HUMAN_DECISION:", ex.AGENT_RULES)
        self.assertIn("BLOCKED: OPERATIONAL:", ex.AGENT_RULES)
        self.assertIn("TECHNICAL", ex.AGENT_RULES)
        for line in ("status: BLOCKED: HUMAN_DECISION: pick A or B", "status: BLOCKED: OPERATIONAL: no token"):
            outcome = parse.agent_outcome(stream("work\n" + line), False) if hasattr(parse, "agent_outcome") \
                else ex.agent_outcome(stream("work\n" + line), False)
            self.assertEqual(outcome["status"], line[len("status: "):])


if __name__ == "__main__":
    unittest.main()
