"""Tests for the Contract permission window (#418). No process is started.

Run: python3 components/kagari-mvi/test_kagari_mvi_policy.py  (or: just mvi::test)
"""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kagari_mvi_policy as pol  # noqa: E402

BODY = """do it

### テストコマンド
```sh
python3 -m unittest -v
```

```kagari-commands
# extra checks
python3 -m pyflakes ...
```
"""


class PolicyCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "src").mkdir()
        os.symlink("/etc", self.root / "escape")
        self.policy = pol.Policy(self.root, "o/r#1", pol.contract_commands(BODY, "python3 -m unittest -v"))

    def tearDown(self):
        self.tmp.cleanup()

    def decide(self, tool, **tool_input):
        return pol.decide(self.policy, {"tool_name": tool, "input": tool_input, "tool_use_id": "t"})

    def bash(self, command):
        return self.decide("Bash", command=command)


class DecisionTableTest(PolicyCase):
    def test_table(self):
        root = str(self.root)
        cases = [
            (self.decide("Edit", file_path=root + "/src/a.py"), "allow", "WRITE_IN_WORKTREE", False),
            (self.decide("Write", file_path="/tmp/x.py"), "deny", "PATH_OUTSIDE_WORKTREE", False),
            (self.decide("Write", file_path=root + "/escape/passwd"), "deny", "PATH_OUTSIDE_WORKTREE", False),
            (self.decide("Edit", file_path=root + "/CLAUDE.md"), "deny", "PROTECTED_PATH", False),
            (self.decide("Write", file_path=root + "/.claude/settings.local.json"), "deny", "PROTECTED_PATH", False),
            (self.decide("Edit", file_path=root + "/src/.git/config"), "deny", "PROTECTED_PATH", False),
            (self.decide("Read", file_path=root + "/src/a.py"), "allow", "READ_IN_WORKTREE", False),
            (self.decide("Read", file_path="/etc/passwd"), "deny", "PATH_OUTSIDE_WORKTREE", False),
            (self.decide("Glob", pattern="../**"), "deny", "PATH_OUTSIDE_WORKTREE", False),
            (self.bash("python3 -m unittest -v"), "allow", "CONTRACT_COMMAND", False),
            (self.bash("python3 -m unittest -v extra"), "deny", "OUT_OF_CONTRACT", False),
            (self.bash("python3 -m pyflakes src"), "allow", "CONTRACT_COMMAND", False),
            (self.bash("python3 -c 'import os'"), "deny", "OUT_OF_CONTRACT", False),
            (self.bash("ls src"), "allow", "READ_ONLY_COMMAND", False),
            (self.bash("cat /etc/passwd"), "deny", "PATH_OUTSIDE_WORKTREE", False),
            (self.bash("find . -exec rm {} ;"), "deny", "SHELL_SYNTAX_NOT_ALLOWED", False),
            (self.bash("find . -delete"), "deny", "COMMAND_OPTION_NOT_ALLOWED", False),
            (self.bash("mkdir src/new"), "allow", "WORKTREE_FILE_COMMAND", False),
            (self.bash("rm CLAUDE.md"), "deny", "PROTECTED_PATH", False),
            (self.bash("ls | cat"), "deny", "SHELL_SYNTAX_NOT_ALLOWED", False),
            (self.bash("curl https://example.com"), "deny", "OUT_OF_CONTRACT", False),
            (self.bash("git status"), "deny", "GIT_HOST_OWNED", False),
            (self.bash("git push origin HEAD"), "deny", "GIT_HOST_OWNED", False),
            (self.bash("git push --force origin HEAD"), "deny", "HISTORY_DESTRUCTION_NEEDS_HUMAN", True),
            (self.bash("git merge main"), "deny", "MERGE_NEEDS_HUMAN", True),
            (self.bash("git tag v1"), "deny", "TAG_NEEDS_HUMAN", True),
            (self.bash("git reset --hard HEAD"), "deny", "HISTORY_DESTRUCTION_NEEDS_HUMAN", True),
            (self.bash("gh pr merge 3"), "deny", "MERGE_NEEDS_HUMAN", True),
            (self.bash("gh issue edit 3 --add-label kagari:approved"), "deny", "APPROVAL_LABEL_NEEDS_HUMAN", True),
            (self.bash("gh release create v1"), "deny", "RELEASE_NEEDS_HUMAN", True),
            (self.bash("gh issue view 3"), "deny", "GITHUB_HOST_OWNED", False),
            (self.decide("WebFetch", url="https://example.com"), "deny", "NETWORK_NOT_ALLOWED", False),
            (self.decide("Agent", prompt="x"), "deny", "TOOL_NOT_ALLOWED", False),
        ]
        for decision, behavior, code, block in cases:
            self.assertEqual((decision.behavior, decision.code, decision.block), (behavior, code, block),
                             decision)

    def test_malformed_requests_are_denied(self):
        for request in (None, [], {"tool_name": "Bash"}, {"tool_name": 3, "input": {}},
                        {"tool_name": "Bash", "input": {}}, {"tool_name": "Edit", "input": {"file_path": 3}}):
            self.assertEqual(pol.decide(self.policy, request).behavior, "deny", request)

    def test_response_shape(self):
        self.assertEqual(self.bash("ls").response(), {"behavior": "allow", "updatedInput": {"command": "ls"}})
        self.assertEqual(self.bash("git tag v1").response(), {"behavior": "deny", "message": "TAG_NEEDS_HUMAN"})


class ContractParseTest(unittest.TestCase):
    def test_test_command_is_exact_and_block_allows_prefix(self):
        rules = pol.contract_commands(BODY, "python3 -m unittest -v")
        self.assertEqual(rules, (pol.CommandRule(("python3", "-m", "unittest", "-v"), False),
                                 pol.CommandRule(("python3", "-m", "pyflakes"), True)))
        self.assertFalse(pol.CommandRule(("a", "..."), False).prefix)

    def test_invalid_contracts(self):
        for body, test in (("", "make test && rm -rf x"), ("", "gh auth status"),
                           ("```kagari-commands\ngit status\n```", None),
                           ("```kagari-commands\nX=1 make\n```", None),
                           ("```kagari-commands\nmake\n", None),
                           ("```kagari-commands\n```\n```kagari-commands\n```", None)):
            with self.assertRaises(pol.PolicyError, msg=(body, test)):
                pol.contract_commands(body, test)

    def test_contract_file_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.json")
            rules = pol.contract_commands(BODY, "python3 -m unittest -v")
            pol.write_contract(path, "o/r#7", rules)
            loaded = pol.load_policy(tmp, path)
        self.assertEqual((loaded.contract_id, loaded.commands), ("o/r#7", rules))


class ServerTest(PolicyCase):
    def call(self, *messages):
        stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages) + "not json\n")
        stdout, log = io.StringIO(), io.StringIO()
        pol.serve(self.policy, stdin, stdout, log)
        return [json.loads(line) for line in stdout.getvalue().splitlines()], log.getvalue()

    def test_initialize_list_call_and_errors(self):
        responses, log = self.call(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "decide", "arguments": {
                "tool_name": "Bash", "input": {"command": "git merge main"}, "tool_use_id": "u"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "decide", "arguments": "x"}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "other", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 6, "method": "nope"})
        by_id = {r.get("id"): r for r in responses}
        self.assertEqual(by_id[1]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(by_id[2]["result"]["tools"][0]["name"], "decide")
        reply = json.loads(by_id[3]["result"]["content"][0]["text"])
        self.assertEqual(reply, {"behavior": "deny", "message": "MERGE_NEEDS_HUMAN"})
        for mid in (4, 5):
            self.assertEqual(json.loads(by_id[mid]["result"]["content"][0]["text"])["behavior"], "deny")
        self.assertEqual(by_id[6]["error"]["code"], -32601)
        self.assertEqual(by_id[None]["error"]["code"], -32700)  # the non-JSON line
        self.assertEqual(len(responses), 7)  # the notification gets no response
        records = [json.loads(line) for line in log.splitlines()]
        self.assertEqual(len(records), 3)
        self.assertEqual((records[0]["code"], records[0]["block"]), ("MERGE_NEEDS_HUMAN", True))

    def test_read_log_skips_garbage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "log")
            with open(path, "w") as handle:
                handle.write('{"behavior": "allow"}\nbroken\n[1]\n{"behavior": "deny", "block": true}\n')
            self.assertEqual(len(pol.read_log(path)), 2)
            self.assertEqual(pol.read_log(os.path.join(tmp, "missing")), [])


if __name__ == "__main__":
    unittest.main()
