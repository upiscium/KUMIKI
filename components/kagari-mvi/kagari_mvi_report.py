"""The text of the runner's comments: test result and review (ADR-0007 D6, ADR-0010 D3).

Split out of kagari_mvi_exec.py (#490).
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

import kagari_mvi_ledger as ledger
from kagari_mvi_parse import tail


def review_report(head: str, criteria: List[str], outcome: Dict[str, Any], fresh: bool) -> str:
    verdicts, denials = outcome["verdicts"], outcome["denials"]
    cell = lambda value: value.replace("|", "\\|").replace("\n", " ")  # noqa: E731
    rows = ["| AC%d | %s | **%s** | %s |" % (i, cell(c), status, cell(evidence))
            for i, (c, (status, evidence)) in enumerate(zip(criteria, verdicts), start=1)]
    failing = sum(1 for status, _ in verdicts if status != "PASS")
    rows.append("| TESTS | テストの目的（申告）と受入条件の対応 | **%s** | %s |" % (
        outcome["tests"][0], cell(outcome["tests"][1])))
    if outcome.get("delegation"):
        rows.append("| DELEGATION | 委任した判断の範囲内か（Issue 本文の宣言） | **%s** | %s |" % (
            outcome["delegation"][0], cell(outcome["delegation"][1])))
    return ("kagari-mvi: independent review (%s, ADR-0007 D6).\n\n- reviewed head: `%s`\n"
            "- not PASS: %d of %d\n- permission denials: %d\n- reviewer session: %s\n"
            "- runner test result: %s\n\n| # | 受入条件 | 判定 | 根拠 |\n| --- | --- | --- | --- |\n%s\n"
            % (outcome["reviewer"], head, failing, len(verdicts), denials,
               "ended with an error" if outcome["errored"] else "ok",
               "for this head" if fresh else "missing or for another commit (not evidence)",
               "\n".join(rows)))


def report(test: str, code: int, output: str, outcome: Dict[str, Any], sha: str,
           protected: Sequence[str] = ()) -> str:
    changed = "".join("- protected file changed (allowed by the Issue): `%s`\n"  # one line each
                      % path.replace("\r", "\\r").replace("\n", "\\n") for path in protected)
    allowed = outcome.get("ledger_allowed") or []
    used = "".join("- allowed by ledger grant `%s`: `%s`\n" % (
        r.get("grant"), str(ledger.describe(r.get("tool", ""), r.get("input") or {}))[:200].replace(
            "`", "'").replace("\n", " ")) for r in allowed[:20])
    return ("kagari-mvi: test result recorded by the runner (not reported by Claude).\n\n"
            "- tested commit: `%s`\n" % sha + changed + used +
            "- test command: `%s` (run by the runner as the agent user)\n- exit code: %d\n"
            "- Claude status: `%s`\n- permission denials: %d\n\n```\n%s\n```\n\n"
            "テストの目的（Claude の申告。合否には使わない）：\n\n%s" % (
                test, code, outcome["status"], outcome["denials"], tail(output).replace("```", "'''"),
                "```\n%s\n```" % outcome["purposes"].replace("```", "'''")
                if outcome.get("purposes") else "申告なし"))
