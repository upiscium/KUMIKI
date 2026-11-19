"""Reading an Issue body (ADR-0007 D4, ADR-0009 D1, ADR-0011): headings, test command, criteria.

Pure text functions; no processes, no GitHub. Split out of kagari_mvi_exec.py (#490).
"""
from __future__ import annotations

import re
from typing import List, Optional

TEST_HEADING = re.compile(r"^#{2,6}[ \t]*(?:テストコマンド|Test command)[ \t]*$", re.M | re.I)
ALLOWED_HEADING = re.compile(r"^#{2,6}[ \t]*(?:変更する保護ファイル|Protected files to change)[ \t]*$",
                             re.M | re.I)
CRITERIA_HEADING = re.compile(r"^#{2,6}[ \t]*(?:受入条件|Acceptance criteria)[ \t]*$", re.M | re.I)
PLACEMENT_HEADINGS = [(names, re.compile(r"^#{2,6}[ \t]*(?:%s)[ \t]*$" % "|".join(names), re.M | re.I))
                      for names in (("全体ロジック", "Overall logic"), ("置く理由", "Why here"),
                                    ("却下した置き場所", "Rejected placements"))]


def test_command(body: Optional[str]) -> Optional[str]:
    """First line of the first code block under a `### テストコマンド` heading."""
    heading = TEST_HEADING.search(body or "")
    if heading is None:
        return None
    section = (body or "")[heading.end():]
    fence = re.search(r"^```[^\n]*\n(.*?)^```", section, re.M | re.S)
    if fence is None or re.search(r"^#{1,6}\s", section[:fence.start()], re.M):
        return None
    lines = [line.strip() for line in fence.group(1).splitlines() if line.strip()]
    return lines[0] if lines else None


def acceptance_criteria(body: Optional[str]) -> List[str]:
    """List items (`- [ ] ...` or `- ...`) under a `### 受入条件` heading."""
    return section_items(body, CRITERIA_HEADING)


def allowed_protected(body: Optional[str]) -> List[str]:
    """Protected paths the approved Issue lets Claude change; a trailing `/` covers a directory."""
    paths = [re.match(r"`([^`]+)`|(\S+)", item) for item in section_items(body, ALLOWED_HEADING)]
    return [re.sub(r"^(\./|/)+", "", match.group(1) or match.group(2)) for match in paths if match]


def missing_placement(body: Optional[str]) -> List[str]:
    """Placement headings (ADR-0011) that are absent or hold nothing but list markers."""
    return [names[0] for names, pattern in PLACEMENT_HEADINGS
            if not re.sub(r"[-*\s]|\[[ xX]\]", "", section_text(body, pattern) or "")]


def section_text(body: Optional[str], pattern: "re.Pattern[str]") -> Optional[str]:
    """Text from the heading to the next heading, or None without the heading."""
    heading = pattern.search(body or "")
    if heading is None:
        return None
    return re.split(r"^#{1,6}\s", (body or "")[heading.end():], maxsplit=1, flags=re.M)[0]


def section_items(body: Optional[str], pattern: "re.Pattern[str]") -> List[str]:
    section = section_text(body, pattern) or ""
    items = re.findall(r"^\s*[-*]\s+(?:\[[ xX]\]\s*)?(.+?)\s*$", section, re.M)
    return [item for item in items if item]
