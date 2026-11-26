"""README checks: no duplicated bullet on consecutive lines (ADR-0013 D2-8)."""
import os
import unittest

README = os.path.join(os.path.dirname(os.path.abspath(__file__)), "README.md")


def duplicate_bullets(text):
    """Return bullet lines (`- ` / `  - ` prefix) that repeat the previous line."""
    lines = text.split("\n")
    return [b for a, b in zip(lines, lines[1:])
            if b.lstrip().startswith("- ") and a == b]


class ReadmeDuplicateTest(unittest.TestCase):
    def test_readme_has_no_consecutive_duplicate_bullets(self):
        with open(README, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(duplicate_bullets(text), [])

    def test_readme_has_one_other_host_line(self):
        with open(README, encoding="utf-8") as f:
            lines = f.read().split("\n")
        self.assertEqual(lines.count("  - 他のホスト"), 1)

    def test_detects_duplicate(self):
        self.assertEqual(duplicate_bullets("- a\n  - x\n  - x\n"), ["  - x"])
        self.assertEqual(duplicate_bullets("- a\n- a\n"), ["- a"])

    def test_distinct_or_separated_bullets_pass(self):
        self.assertEqual(duplicate_bullets("- a\n- b\n- a\n"), [])


if __name__ == "__main__":
    unittest.main()
