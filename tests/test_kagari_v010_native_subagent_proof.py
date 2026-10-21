"""No-provider proof-boundary tests. Synthetic events are NOT real subagents."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import kagari_v010_native_subagent_proof as proof


class NativeSubagentProofTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="kagari-native-proof-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.project = self.base / "project"
        self.project.mkdir(mode=0o700)
        (self.project / "docs").mkdir()
        self.token = "ab12cd34ef56a890"
        (self.project / "AGENTS.md").write_text(
            "# Project instructions\nEvery new worker doc must have inheritance_token="
            + self.token + "\n"
        )
        (self.project / "README.md").write_text("Original user README\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", "AGENTS.md", "README.md")
        self.git("-c", "user.name=Smoke", "-c", "user.email=smoke@example.invalid",
                 "commit", "-qm", "baseline")
        self.head = self.git("rev-parse", "HEAD").decode().strip()
        (self.project / "README.md").write_text("Preserve staged user edit\n")
        self.git("add", "README.md")
        self.setup = self.base / "setup.json"
        self.events = self.base / "events.jsonl"
        self.setup.write_text(json.dumps({
            "project": str(self.project),
            "head": self.head,
            "index_sha256": proof.digest((self.project / ".git/index").read_bytes()),
            "project_instruction_token": self.token,
            "allowed": ["docs/worker-a.md", "docs/worker-b.md"],
        }))
        self.parent = "ses_parent_accepted"
        self.template = []
        for num, label in enumerate("ab"):
            sub = "kagari-v010-leaf-" + label
            child = "ses_child_" + label
            self.template.append({
                "sessionID": self.parent,
                "type": "tool_use",
                "part": {
                    "sessionID": self.parent,
                    "type": "tool",
                    "tool": "task",
                    "callID": "call_" + label,
                    "state": {
                        "status": "completed",
                        "time": {"start": 1000 + num * 5, "end": 1200 + num * 7},
                        "input": {"subagent_type": sub,
                                  "prompt": "Bounded work unit"},
                        "metadata": {
                            "sessionId": child,
                            "parentSessionId": self.parent,
                            "model": {"providerID": "openai", "modelID": "fixture-only"},
                        },
                        "output": (
                            '<task id="' + child + '" state="completed">\n'
                            '<task_result>\nstatus: COMPLETED\n'
                            'File: docs/worker-' + label + '.md\n'
                            '</task_result>\n</task>'
                        ),
                    }
                }
            })
            (self.project / ("docs/worker-" + label + ".md")).write_text(
                "kagari_worker=" + label.upper() +
                " inheritance_token=" + self.token + "\n"
            )
        self.write_events()
        self.write_exports()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.project), *args],
                              check=True, capture_output=True, timeout=20).stdout.strip()

    def write_events(self, events=None):
        value = self.template if events is None else events
        self.events.write_text("\n".join(json.dumps(x) for x in value) + "\n")

    def write_exports(self):
        folder = self.base / "native-session-exports"
        folder.mkdir(exist_ok=True)
        parent = {
            "info": {"id": self.parent, "agent": "kagari-v010-primary",
                     "directory": str(self.project)},
            "messages": [{
                "info": {"role": "assistant"},
                "parts": [{"type": "tool", "tool": "task", "callID": "call_a"},
                          {"type": "tool", "tool": "task", "callID": "call_b"}],
            }],
        }
        (folder / "parent-session.json").write_text(json.dumps(parent))
        for label in "ab":
            expected = str(self.project / ("docs/worker-" + label + ".md"))
            child = {
                "info": {"id": "ses_child_" + label, "parentID": self.parent,
                         "agent": "kagari-v010-leaf-" + label,
                         "directory": str(self.project),
                         "model": {"id": "fixture-only", "providerID": "openai"}},
                "messages": [{
                    "info": {"role": "assistant"},
                    "parts": [
                        {"type": "tool", "tool": "apply_patch",
                         "state": {"status": "completed", "input": {
                             "patchText": "*** Begin Patch\n*** Add File: " +
                                          expected + "\n+kagari_worker=" +
                                          label.upper() + " inheritance_token=" +
                                          self.token + "\n*** End Patch"}}},
                        {"type": "tool", "tool": "read",
                         "state": {"status": "completed", "input": {
                             "filePath": expected}}},
                        {"type": "text", "text": "status: COMPLETED\n"}
                    ],
                }],
            }
            (folder / (label + "-session.json")).write_text(json.dumps(child))

    def verify(self):
        return proof.verify(self.project, self.setup, self.events)

    def test_two_distinct_native_children_and_positive_overlap(self):
        result = self.verify()
        self.assertEqual(result["status"], "REAL_NATIVE_TWO_SUBAGENTS_PARALLEL_PASS")
        self.assertEqual(result["native_task_calls"], 2)
        self.assertEqual(result["overlap_ms"], 195)
        self.assertEqual(result["common_parent_session"], self.parent)
        self.assertEqual(len(result["children"]), 2)
        self.assertTrue(result["git_head_and_staged_index_preserved"])
        self.assertTrue(result["project_instructions_preserved_and_inherited"])
        self.assertFalse(result["generic_task_qualified"])
        self.assertFalse(result["v010_release_qualified"])
        self.assertEqual(self.git("diff", "--cached", "--name-only"), b"README.md")

    def test_no_parallel_overlap_or_false_completion_rejected(self):
        for kind in ("no_overlap", "same_child", "bad_parent", "bad_role",
                     "bad_status", "bad_model", "bad_output", "missing_child"):
            with self.subTest(kind=kind):
                events = copy.deepcopy(self.template)
                second = events[1]["part"]["state"]
                if kind == "no_overlap":
                    second["time"]["start"] = 1400
                    second["time"]["end"] = 1550
                elif kind == "same_child":
                    second["metadata"]["sessionId"] = events[0]["part"]["state"]["metadata"]["sessionId"]
                elif kind == "bad_parent":
                    events[1]["part"]["sessionID"] = "ses_wrong_parent"
                elif kind == "bad_role":
                    second["input"]["subagent_type"] = "build"
                elif kind == "bad_status":
                    second["status"] = "running"
                elif kind == "bad_model":
                    second["metadata"]["model"] = None
                elif kind == "bad_output":
                    second["output"] = "status: COMPLETED"
                elif kind == "missing_child":
                    events.pop()
                self.write_events(events)
                with self.assertRaises(proof.ProofBlocked):
                    self.verify()
        self.write_events()

    def test_reject_product_marker_or_instruction_loss(self):
        target = self.project / "docs/worker-a.md"
        target.write_text("kagari_worker=A\n")
        with self.assertRaisesRegex(proof.ProofBlocked, "instruction inheritance"):
            self.verify()
        target.write_text("kagari_worker=A inheritance_token=" + self.token + "\n")
        (self.project / "AGENTS.md").write_text("Altered Project instructions\n")
        with self.assertRaisesRegex(proof.ProofBlocked, "AGENTS.md modified"):
            self.verify()

    def test_reject_unrelated_git_staging_or_unknown_product(self):
        (self.project / "docs/other.md").write_text("foreign edit\n")
        with self.assertRaisesRegex(proof.ProofBlocked, "unexpected Project file"):
            self.verify()
        (self.project / "docs/other.md").unlink()
        (self.project / "README.md").write_text("Unexpected dirty README\n")
        with self.assertRaisesRegex(proof.ProofBlocked, "unstaged tracked"):
            self.verify()

    def test_reject_wrong_head_and_index_or_symlink_product(self):
        setup = json.loads(self.setup.read_text())
        setup["head"] = "0" * len(self.head)
        self.setup.write_text(json.dumps(setup))
        with self.assertRaisesRegex(proof.ProofBlocked, "Git HEAD changed"):
            self.verify()
        setup["head"] = self.head
        setup["index_sha256"] = "0" * 64
        self.setup.write_text(json.dumps(setup))
        with self.assertRaisesRegex(proof.ProofBlocked, "staged index changed"):
            self.verify()
        setup["index_sha256"] = proof.digest((self.project / ".git/index").read_bytes())
        self.setup.write_text(json.dumps(setup))
        child = self.project / "docs/worker-a.md"
        child.unlink()
        child.symlink_to("worker-b.md")
        with self.assertRaisesRegex(proof.ProofBlocked, "unaliased regular file"):
            self.verify()

    def test_reject_changed_git_config_and_modes(self):
        config = self.project / ".git/config"
        config.write_bytes(config.read_bytes() + b"\n[extra]\n key = value\n")
        with self.assertRaisesRegex(proof.ProofBlocked, "Git configuration"):
            self.verify()
        config.write_bytes(b"[core]\n\trepositoryformatversion = 0\n"
                           b"\tfilemode = true\n\tbare = false\n"
                           b"\tlogallrefupdates = true\n")
        self.assertEqual(self.verify()["native_task_calls"], 2)
        config.chmod(0o600)
        with self.assertRaisesRegex(proof.ProofBlocked, "file mode"):
            self.verify()

    def test_reject_child_wrong_file_and_parent_sequential_calls(self):
        folder = self.base / "native-session-exports"
        child = folder / "a-session.json"
        record = json.loads(child.read_text())
        parts = record["messages"][0]["parts"]
        patch = parts[0]["state"]["input"]["patchText"]
        parts[0]["state"]["input"]["patchText"] = patch.replace(
            "docs/worker-a.md", "docs/worker-b.md")
        child.write_text(json.dumps(record))
        with self.assertRaisesRegex(proof.ProofBlocked, "unauthorized"):
            self.verify()
        self.write_exports()
        parent = folder / "parent-session.json"
        record = json.loads(parent.read_text())
        record["messages"][0]["parts"] = record["messages"][0]["parts"][:1]
        record["messages"].append({
            "info": {"role": "assistant"},
            "parts": [{"type": "tool", "tool": "task", "callID": "call_b"}],
        })
        parent.write_text(json.dumps(record))
        with self.assertRaisesRegex(proof.ProofBlocked, "one parent tool batch"):
            self.verify()

    def test_reject_unlisted_file_even_when_git_ignored(self):
        (self.project / ".gitignore").write_text("private/\\n")
        (self.project / "private").mkdir()
        (self.project / "private/hidden").write_text("ignored foreign file\\n")
        with self.assertRaisesRegex(proof.ProofBlocked, "unexpected Project file"):
            self.verify()

    def test_reject_jsonl_invalid_and_extra_duplicate_child(self):
        self.events.write_bytes(b'{"type":\n')
        with self.assertRaisesRegex(proof.ProofBlocked, "malformed JSONL"):
            self.verify()
        self.write_events(self.template + [self.template[0]])
        with self.assertRaisesRegex(proof.ProofBlocked, "exactly two"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
