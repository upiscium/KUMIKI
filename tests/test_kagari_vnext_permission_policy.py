"""#394 W2-a permission decisions; the MCP server and real-CLI checks are W2-b."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import tempfile
import unittest

from tools.kagari_vnext_permission_policy import (
    CommandRule, Decision, Policy, PolicyError, decide, parse_contract_commands,
)

CONTRACT = """\
## 検証

```kagari-commands
# 単体テスト
python3 -m unittest ...
just test
./scripts/check.sh --strict
```
"""


class PolicyCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.worktree = root / "wt"
        (self.worktree / "src").mkdir(parents=True)
        (self.worktree / "src/app.py").write_text("x = 1\n")
        self.outside = root / "outside"
        self.outside.mkdir()
        os.symlink(self.outside, self.worktree / "escape")
        self.policy = Policy(self.worktree, "c" * 64, parse_contract_commands(CONTRACT), git_read=True)

    def decide(self, tool, policy=None, **tool_input):
        return decide(policy or self.policy, {"tool_name": tool, "input": tool_input, "tool_use_id": "t1"})

    def bash(self, command, policy=None):
        return self.decide("Bash", policy, command=command)

    def assert_decision(self, decision, behavior, code, block=False):
        self.assertEqual((decision.behavior, decision.code, decision.block), (behavior, code, block), decision)


class DecisionTableTests(PolicyCase):
    """AC-W2-01, including the GitHub write examples added on 2026-11-09."""

    def test_table(self):
        wt = self.worktree
        cases = [
            ("edit in scope", self.decide("Edit", file_path=str(wt / "src/app.py"), old_string="1", new_string="2"),
             "allow", "WRITE_IN_WORKTREE", False),
            ("new file in scope", self.decide("Write", file_path=str(wt / "src/new.py"), content=""),
             "allow", "WRITE_IN_WORKTREE", False),
            ("edit outside worktree", self.decide("Edit", file_path=str(self.outside / "a.py")),
             "deny", "PATH_OUTSIDE_WORKTREE", False),
            ("write through symlink", self.decide("Write", file_path=str(wt / "escape/a.py"), content=""),
             "deny", "PATH_OUTSIDE_WORKTREE", False),
            ("write with ..", self.decide("Write", file_path=str(wt / "src/../../outside/a.py"), content=""),
             "deny", "PATH_OUTSIDE_WORKTREE", False),
            ("Contract test command", self.bash("python3 -m unittest tests.test_app"),
             "allow", "CONTRACT_COMMAND", False),
            ("git push", self.bash("git push origin task/394"), "deny", "PUBLICATION_HOST_OWNED", False),
            ("label operation", self.bash("gh issue edit 394 --add-label approved"),
             "deny", "APPROVAL_LABEL_NEEDS_HUMAN", True),
            ("gh label", self.bash("gh label create approved"), "deny", "APPROVAL_LABEL_NEEDS_HUMAN", True),
            ("path outside worktree", self.bash(f"cat {self.outside}/secret"), "deny", "PATH_OUTSIDE_WORKTREE", False),
            ("gh issue comment", self.bash("gh issue comment 394 --body done"),
             "deny", "GITHUB_WRITE_HOST_OWNED", False),
            ("gh issue create", self.bash("gh issue create --title x --body y"),
             "deny", "GITHUB_WRITE_HOST_OWNED", False),
            ("gh api POST", self.bash("gh api -X POST repos/upiscium/KUMIKI/issues/394/comments -f body=x"),
             "deny", "GITHUB_WRITE_HOST_OWNED", False),
        ]
        for name, decision, behavior, code, block in cases:
            with self.subTest(name):
                self.assert_decision(decision, behavior, code, block)


class HumanDecisionTests(PolicyCase):
    """ADR-0001 operations are denied and tell Main to set the Task BLOCKED."""

    def test_blocking_operations(self):
        for command, code in [
            ("git push --force origin task/394", "HISTORY_DESTRUCTION_NEEDS_HUMAN"),
            ("git push --force-with-lease origin task/394", "HISTORY_DESTRUCTION_NEEDS_HUMAN"),
            ("git push origin +task/394", "HISTORY_DESTRUCTION_NEEDS_HUMAN"),
            ("git push origin :task/394", "HISTORY_DESTRUCTION_NEEDS_HUMAN"),
            ("git merge main", "MERGE_NEEDS_HUMAN"),
            ("git tag v1.0.0", "TAG_NEEDS_HUMAN"),
            ("git reset --hard HEAD~1", "HISTORY_DESTRUCTION_NEEDS_HUMAN"),
            ("git clean -fdx", "HISTORY_DESTRUCTION_NEEDS_HUMAN"),
            ("git branch -D other", "HISTORY_DESTRUCTION_NEEDS_HUMAN"),
            ("git worktree remove x", "HISTORY_DESTRUCTION_NEEDS_HUMAN"),
            ("git reflog expire --all", "HISTORY_DESTRUCTION_NEEDS_HUMAN"),
            ("gh pr merge 399", "MERGE_NEEDS_HUMAN"),
            ("gh pr ready 399", "MERGE_NEEDS_HUMAN"),
            ("gh pr edit 399 --remove-label approved", "APPROVAL_LABEL_NEEDS_HUMAN"),
            ("gh release create v1", "RELEASE_NEEDS_HUMAN"),
        ]:
            with self.subTest(command):
                self.assert_decision(self.bash(command), "deny", code, block=True)

    def test_contract_cannot_allow_git_or_gh(self):
        for line in ("git push ...", "gh pr merge 1", "/usr/bin/git status"):
            with self.subTest(line):
                with self.assertRaises(PolicyError) as caught:
                    parse_contract_commands(f"```kagari-commands\n{line}\n```\n")
                self.assertEqual(caught.exception.code, "CONTRACT_COMMAND_FORBIDDEN")


class GitReadTests(PolicyCase):
    def test_read_is_rewritten_to_hardened_form(self):
        decision = self.bash("git diff --check")
        self.assert_decision(decision, "allow", "GIT_READ_HARDENED")
        self.assertEqual(shlex.split(decision.updated_input["command"]), [
            "git", "-c", "core.fsmonitor=false", "-c", "core.pager=cat", "--no-pager",
            "diff", "--no-ext-diff", "--no-textconv", "--check"])
        status = self.bash("git status --short")
        self.assertEqual(shlex.split(status.updated_input["command"])[6:], ["status", "--short"])

    def test_revision_tilde_is_not_shell_expansion(self):
        decision = self.bash("git log -1 HEAD~1")
        self.assert_decision(decision, "allow", "GIT_READ_HARDENED")
        self.assertEqual(shlex.split(decision.updated_input["command"])[-1], "HEAD~1")
        self.assert_decision(self.bash("cat x=~/y"), "deny", "SHELL_SYNTAX_NOT_ALLOWED")

    def test_other_input_fields_survive_rewrite(self):
        decision = self.decide("Bash", command="git log -1", description="show head")
        self.assertEqual(decision.updated_input["description"], "show head")

    def test_unavailable_when_helper_configured(self):
        policy = Policy(self.worktree, "c" * 64, git_read=False)
        self.assert_decision(self.bash("git status", policy), "deny", "GIT_READ_UNAVAILABLE")

    def test_options_and_commands_outside_the_read_set(self):
        for command, code in [
            ("git diff --ext-diff", "GIT_OPTION_NOT_ALLOWED"),
            ("git diff --textconv", "GIT_OPTION_NOT_ALLOWED"),
            ("git diff --output=out.txt", "GIT_OPTION_NOT_ALLOWED"),
            ("git log --git-dir=other", "GIT_OPTION_NOT_ALLOWED"),
            ("git -c core.pager=less log", "GIT_COMMAND_NOT_ALLOWED"),
            ("git -C src status", "GIT_COMMAND_NOT_ALLOWED"),
            ("git config user.name x", "GIT_COMMAND_NOT_ALLOWED"),
            ("git add src/app.py", "GIT_COMMAND_NOT_ALLOWED"),
            ("git commit -m x", "GIT_COMMAND_NOT_ALLOWED"),
            ("git", "GIT_COMMAND_NOT_ALLOWED"),
        ]:
            with self.subTest(command):
                self.assert_decision(self.bash(command), "deny", code)

    def test_github_reads_are_not_allowed(self):
        for command in ("gh issue view 394", "gh pr list", "gh api repos/upiscium/KUMIKI"):
            with self.subTest(command):
                self.assert_decision(self.bash(command), "deny", "GITHUB_READ_NOT_ALLOWED")


class BashTests(PolicyCase):
    def test_shell_syntax_is_refused(self):
        for command in ("ls; rm -rf .", "ls && true", "ls | wc -l", "cat src/app.py > out", "echo $HOME",
                        "echo `id`", "ls *.py", "cat ~/x", "python3 -m unittest\nrm x", "echo a # b",
                        "echo \\x", "FOO=1 python3 -m unittest", "echo 'unbalanced"):
            with self.subTest(command):
                self.assert_decision(self.bash(command), "deny", "SHELL_SYNTAX_NOT_ALLOWED")

    def test_read_only_commands(self):
        for command in ("ls -la src", "cat src/app.py", "grep -rn x src", "wc -l src/app.py", "pwd",
                        "echo REPORT A=1", "find src -name app.py", "rg x src"):
            with self.subTest(command):
                self.assert_decision(self.bash(command), "allow", "READ_ONLY_COMMAND")

    def test_read_only_commands_that_execute_or_write(self):
        for command in ("find . -exec rm x", "find . -delete", "find . -fprint out", "rg --pre=sh x", "rg -z x",
                        "find -L .", "find . -follow", "grep -R x .", "rg --follow x", "ls -L escape-free"):
            with self.subTest(command):
                self.assert_decision(self.bash(command), "deny", "COMMAND_OPTION_NOT_ALLOWED")

    def test_worktree_file_commands(self):
        for command in ("mkdir -p src/pkg", "touch src/new.py", "mv src/app.py src/main.py", "rm src/app.py"):
            with self.subTest(command):
                self.assert_decision(self.bash(command), "allow", "WORKTREE_FILE_COMMAND")
        for command in ("rm -rf .", "rm -rf .git", "mv CLAUDE.md x", "touch .claude/settings.json", "rm -r src/.git",
                        "rm -rf"):
            with self.subTest(command):
                self.assert_decision(self.bash(command), "deny", "PROTECTED_PATH")

    def test_paths_leaving_the_worktree(self):
        for command in ("cat escape/secret", "ls ..", "cat src/../../outside/x", "cp src/app.py /tmp/x",
                        "python3 -m unittest --top-level-directory=/etc", "/usr/bin/python3 -m unittest",
                        "grep -f/etc/passwd x", "grep -f../outside/x x"):
            with self.subTest(command):
                self.assert_decision(self.bash(command), "deny", "PATH_OUTSIDE_WORKTREE")

    def test_contract_commands(self):
        self.assert_decision(self.bash("just test"), "allow", "CONTRACT_COMMAND")
        self.assert_decision(self.bash("./scripts/check.sh --strict"), "allow", "CONTRACT_COMMAND")
        self.assert_decision(self.bash("just test extra"), "deny", "OUT_OF_CONTRACT")
        self.assert_decision(self.bash("./scripts/check.sh"), "deny", "OUT_OF_CONTRACT")
        self.assert_decision(self.bash("python3 -c pass"), "deny", "OUT_OF_CONTRACT")
        self.assert_decision(self.bash("curl https://example.com"), "deny", "OUT_OF_CONTRACT")
        empty = Policy(self.worktree, "c" * 64)
        self.assert_decision(self.bash("just test", empty), "deny", "OUT_OF_CONTRACT")


class ToolTests(PolicyCase):
    def test_reads(self):
        self.assert_decision(self.decide("Read", file_path=str(self.worktree / "src/app.py")), "allow", "READ_IN_WORKTREE")
        self.assert_decision(self.decide("Grep", pattern="x"), "allow", "READ_IN_WORKTREE")
        self.assert_decision(self.decide("Read", file_path="/etc/passwd"), "deny", "PATH_OUTSIDE_WORKTREE")
        self.assert_decision(self.decide("Glob", pattern="/etc/**"), "deny", "PATH_OUTSIDE_WORKTREE")
        self.assert_decision(self.decide("Glob", pattern="../**"), "deny", "PATH_OUTSIDE_WORKTREE")
        self.assert_decision(self.decide("Grep", pattern="x", path=str(self.outside)), "deny", "PATH_OUTSIDE_WORKTREE")

    def test_protected_project_files(self):
        for relative in (".git/config", "CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude/settings.json",
                         ".claude/agents/x.md", "src/.git/hooks/pre-commit"):
            with self.subTest(relative):
                decision = self.decide("Write", file_path=str(self.worktree / relative), content="")
                self.assert_decision(decision, "deny", "PROTECTED_PATH")
        nested = self.decide("Edit", file_path=str(self.worktree / "docs/AGENTS.md"))
        self.assert_decision(nested, "allow", "WRITE_IN_WORKTREE")

    def test_other_tools_and_malformed_requests(self):
        self.assert_decision(self.decide("WebFetch", url="https://example.com"), "deny", "NETWORK_NOT_ALLOWED")
        self.assert_decision(self.decide("WebSearch", query="x"), "deny", "NETWORK_NOT_ALLOWED")
        self.assert_decision(self.decide("Agent", subagent_type="general-purpose"), "deny", "TOOL_NOT_ALLOWED")
        self.assert_decision(self.decide("mcp__other__write"), "deny", "TOOL_NOT_ALLOWED")
        for request in (None, [], {"tool_name": "Bash"}, {"tool_name": 1, "input": {}},
                        {"tool_name": "Bash", "input": {"command": 1}}, {"tool_name": "Edit", "input": {}}):
            with self.subTest(request=request):
                self.assertEqual(decide(self.policy, request).code, "MALFORMED_REQUEST")


class ResponseTests(unittest.TestCase):
    def test_wire_format(self):
        allow = Decision("allow", "X", updated_input={"command": "ls"})
        self.assertEqual(allow.response(), {"behavior": "allow", "updatedInput": {"command": "ls"}})
        self.assertEqual(Decision("deny", "OUT_OF_CONTRACT", "just").response(),
                         {"behavior": "deny", "message": "OUT_OF_CONTRACT: just"})
        self.assertEqual(Decision("deny", "MERGE_NEEDS_HUMAN").response(),
                         {"behavior": "deny", "message": "MERGE_NEEDS_HUMAN"})


class ContractParseTests(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(parse_contract_commands(CONTRACT), (
            CommandRule(("python3", "-m", "unittest"), prefix=True),
            CommandRule(("just", "test")),
            CommandRule(("./scripts/check.sh", "--strict")),
        ))
        self.assertEqual(parse_contract_commands("no block here\n```python\nprint()\n```\n"), ())
        self.assertEqual(parse_contract_commands("```kagari-commands\r\njust test\r\n```\r\n"),
                         (CommandRule(("just", "test")),))

    def test_invalid_blocks(self):
        for body, code in [
            ("```kagari-commands\njust test\n```\n```kagari-commands\nx\n```\n", "CONTRACT_COMMANDS_DUPLICATED"),
            ("```kagari-commands\njust test\n", "CONTRACT_COMMANDS_UNCLOSED"),
            ("```kagari-commands\njust test; rm x\n```\n", "CONTRACT_COMMAND_INVALID"),
            ("```kagari-commands\nFOO=1 just test\n```\n", "CONTRACT_COMMAND_INVALID"),
            ("```kagari-commands\n...\n```\n", "CONTRACT_COMMAND_INVALID"),
            ("```kagari-commands\njust 'test\n```\n", "CONTRACT_COMMAND_INVALID"),
        ]:
            with self.subTest(body):
                with self.assertRaises(PolicyError) as caught:
                    parse_contract_commands(body)
                self.assertEqual(caught.exception.code, code)


if __name__ == "__main__":
    unittest.main()
