"""Offline acceptance for read-only coexistence-first AGENTS.md diagnostics."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import kagari_v010_agents_compat as compat


class AgentsCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="kagari-agents-compat-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / "project"
        self.project.mkdir(mode=0o700)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.project)],
                       check=True, capture_output=True, timeout=20)
        (self.project / "README.md").write_text("product file\n")
        self.known_rules = [rule["signature"] for rule in compat.KNOWN_CONFLICTS]

    def v3(self):
        directory = self.project / ".automation"
        directory.mkdir(exist_ok=True)
        (directory / "VERSION").write_text("3\n")
        (directory / "UPSTREAM").write_text(
            'repository = "github:upiscium/Templates"\n'
            'ref = "main"\n'
            'component = "components/agent-core"\n')
        (directory / "ownership.toml").write_text(
            'version = 1\n[paths]\n"AGENTS.md" = "replace"\n')

    def rules(self, extras="", *, subset=None):
        text = "# Agent Operating Rules\n\n## Durable invariants\n"
        for line in (self.known_rules if subset is None else subset):
            text += line + "\n"
        text += extras
        (self.project / "AGENTS.md").write_text(text)
        return text

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.project), *args],
                              check=True, capture_output=True, timeout=20).stdout

    def tracked_and_staged(self):
        self.git("add", "-A")
        subprocess.run(
            ["git", "-C", str(self.project),
             "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "commit", "-qm", "baseline"], capture_output=True, check=True)
        (self.project / "README.md").write_text("unrelated staged user edit\n")
        self.git("add", "README.md")
        return {
            "head": self.git("rev-parse", "HEAD"),
            "index": (self.project / ".git/index").read_bytes(),
            "content": {
                str(f.relative_to(self.project)): f.read_bytes()
                for f in self.project.rglob("*")
                if f.is_file() and ".git" not in f.relative_to(self.project).parts
            },
        }

    def test_structurally_identifies_v3_and_exact_four_conflicts(self):
        self.v3()
        source = self.rules()
        before = self.tracked_and_staged()
        result = compat.inspect(self.project)
        self.assertEqual(result["state"], "ASK_REQUIRED_FOR_CONFLICTING_KAGARI_TASK")
        self.assertEqual(result["legacy"]["classification"], "V3_STRUCTURALLY_IDENTIFIED")
        self.assertFalse(result["legacy"]["source_cryptographically_authenticated"])
        self.assertEqual(len(result["confirmed_conflicts"]), 4)
        self.assertEqual({x["id"] for x in result["confirmed_conflicts"]},
                         {rule["id"] for rule in compat.KNOWN_CONFLICTS})
        for entry in result["confirmed_conflicts"]:
            self.assertTrue(source.splitlines()[entry["line"] - 1].strip()
                            == entry["exact_instruction"])
            self.assertEqual(entry["line_sha256"],
                             hashlib.sha256(entry["exact_instruction"].encode()).hexdigest())
        ask = result["ask_candidate"]
        self.assertEqual(ask["status"], "NOT_APPROVED")
        self.assertIn(compat.BEGIN,
                      ask["proposed_file_change"]["unified_diff"])
        self.assertEqual(ask["proposed_file_change"]["preimage_sha256"],
                         result["agents"]["sha256"])
        self.assertFalse(ask["proposed_file_change"]["apply_available"])
        self.assertEqual(ask["full_migration"], "SEPARATE_OPT_IN_NOT_REQUESTED")
        self.assertFalse(result["automatic_file_edit_available"])
        self.assertFalse(result["human_permission_granted"])
        after = self.tracked_state()
        self.assertEqual(before, after)

    def tracked_state(self):
        return {
            "head": self.git("rev-parse", "HEAD"),
            "index": (self.project / ".git/index").read_bytes(),
            "content": {
                str(f.relative_to(self.project)): f.read_bytes()
                for f in self.project.rglob("*")
                if f.is_file() and ".git" not in f.relative_to(self.project).parts
            },
        }

    def test_v3_is_not_by_itself_a_reason_to_upgrade(self):
        self.v3()
        self.rules(subset=[
            "- Leaf agents must not create subagents.",
            "- Each configured role model is authoritative.",
        ])
        result = compat.inspect(self.project)
        self.assertEqual(result["legacy"]["classification"], "V3_STRUCTURALLY_IDENTIFIED")
        self.assertEqual(result["state"], "NO_EXACT_KNOWN_CONFLICT")
        self.assertEqual(result["confirmed_conflicts"], [])
        self.assertIsNone(result["ask_candidate"])
        self.assertFalse(result["task_compatibility_proven"])

    def test_repo_name_cutover_is_structural_not_authority(self):
        self.v3()
        self.rules(subset=[])
        upstream = self.project / ".automation" / "UPSTREAM"
        for repository, classification in (
            ("github:upiscium/KUMIKI", "V3_STRUCTURALLY_IDENTIFIED"),
            ("github:upiscium/Templates", "V3_STRUCTURALLY_IDENTIFIED"),
            ("github:another-owner/KUMIKI", "PARTIAL_OR_FOREIGN_AUTOMATION"),
        ):
            with self.subTest(repository=repository):
                upstream.write_text(
                    f'repository = "{repository}"\n'
                    'ref = "main"\n'
                    'component = "components/agent-core"\n'
                )
                report = compat.inspect(self.project)
                self.assertEqual(report["legacy"]["classification"], classification)
                self.assertFalse(report["legacy"]["source_cryptographically_authenticated"])
                self.assertFalse(report["task_compatibility_proven"])
                self.assertFalse(report["human_permission_granted"])
                self.assertIsNone(report["ask_candidate"])

    def test_non_string_upstream_repository_is_not_structurally_authoritative(self):
        self.v3()
        self.rules(subset=[])
        upstream = self.project / ".automation" / "UPSTREAM"
        for repository_line in (
            "repository = []",
            'repository = { owner = "upiscium" }',
        ):
            with self.subTest(repository_line=repository_line):
                upstream.write_text(
                    repository_line + "\n"
                    'ref = "main"\n'
                    'component = "components/agent-core"\n'
                )
                result = compat.inspect(self.project)
                self.assertEqual(
                    result["legacy"]["classification"], "PARTIAL_OR_FOREIGN_AUTOMATION"
                )
                self.assertFalse(result["legacy"]["source_cryptographically_authenticated"])
                self.assertFalse(result["task_compatibility_proven"])
                self.assertIsNone(result["ask_candidate"])

    def test_normal_foreign_agents_remain_unchanged_and_unqualified(self):
        self.rules(subset=[
            "- Run the exact repository tests after touching files.",
            "- Product code must remain inside src.",
        ])
        result = compat.inspect(self.project)
        self.assertEqual(result["state"], "NO_EXACT_KNOWN_CONFLICT")
        self.assertEqual(result["legacy"]["classification"], "NO_AUTOMATION_IDENTITY")
        self.assertFalse(result["task_compatibility_proven"])
        self.assertIsNone(result["ask_candidate"])

    def test_mixed_known_v3_and_uncertain_custom_policy_is_manual_only(self):
        self.v3()
        before = self.rules(extras="- Never delegate to subagents.\n")
        report = compat.inspect(self.project)
        self.assertEqual(report["legacy"]["classification"],
                         "V3_STRUCTURALLY_IDENTIFIED")
        self.assertEqual(report["state"], "REVIEW_REQUIRED_UNCONFIRMED_CONFLICT")
        self.assertEqual(len(report["confirmed_conflicts"]), 4)
        self.assertEqual(len(report["suspected_conflicts"]), 1)
        self.assertIsNone(report["ask_candidate"])
        self.assertFalse(report["human_permission_granted"])
        self.assertEqual((self.project / "AGENTS.md").read_text(), before)

    def test_foreign_explicit_legacy_instruction_still_reports_ask(self):
        self.rules(subset=[
            compat.KNOWN_CONFLICTS[2]["signature"],
        ])
        result = compat.inspect(self.project)
        self.assertEqual(result["state"], "ASK_REQUIRED_FOR_CONFLICTING_KAGARI_TASK")
        self.assertEqual(result["legacy"]["classification"], "NO_AUTOMATION_IDENTITY")
        self.assertEqual(len(result["confirmed_conflicts"]), 1)

    def test_version_marker_alone_is_not_authoritative(self):
        self.v3()
        (self.project / ".automation/UPSTREAM").write_text(
            'repository = "github:someone/other"\n'
            'component = "components/agent-core"\n')
        self.rules(subset=[])
        result = compat.inspect(self.project)
        self.assertEqual(result["legacy"]["classification"], "PARTIAL_OR_FOREIGN_AUTOMATION")
        self.assertIsNone(result["ask_candidate"])

    def test_quoted_and_fenced_v3_rules_not_detected(self):
        first, second = self.known_rules[:2]
        source = (
            "# Quoted historical policy examples\n\n"
            + "> " + first + "\n\n"
            + chr(96) * 3 + "markdown\n" + second + "\n" + chr(96) * 3 + "\n"
        )
        (self.project / "AGENTS.md").write_text(source)
        self.assertEqual(compat.detect_known_conflicts(source), [])
        self.assertEqual(compat.inspect(self.project)["state"], "NO_EXACT_KNOWN_CONFLICT")

    def test_custom_prohibitions_request_review_not_automatic_patch(self):
        self.rules(subset=[
            "- Never delegate to subagents.",
            "- Do not use the OpenCode task tool.",
            "- サブエージェントの使用を禁止する。",
        ])
        result = compat.inspect(self.project)
        self.assertEqual(result["state"], "REVIEW_REQUIRED_UNCONFIRMED_CONFLICT")
        self.assertEqual(len(result["suspected_conflicts"]), 3)
        self.assertEqual(result["confirmed_conflicts"], [])
        self.assertIsNone(result["ask_candidate"])
        self.assertFalse(result["human_permission_granted"])

    def test_directive_with_added_exemption_is_manual_not_false_ask(self):
        old = compat.KNOWN_CONFLICTS[0]["signature"]
        updated = old + " This restriction does not apply to KAGARI."
        self.rules(subset=[updated])
        result = compat.inspect(self.project)
        self.assertEqual(result["state"], "REVIEW_REQUIRED_UNCONFIRMED_CONFLICT")
        self.assertEqual(result["confirmed_conflicts"], [])
        self.assertEqual(result["suspected_conflicts"][0]["id"], "MODIFIED_V3_INITIALIZE")
        self.assertIsNone(result["ask_candidate"])

    def test_custom_policy_inside_fenced_example_not_warning(self):
        original = "- Never delegate to subagents."
        fenced = chr(96) * 3 + "markdown\n" + original + "\n" + chr(96) * 3
        (self.project / "AGENTS.md").write_text(fenced)
        result = compat.inspect(self.project)
        self.assertEqual(result["suspected_conflicts"], [])
        self.assertEqual(result["state"], "NO_EXACT_KNOWN_CONFLICT")

    def test_modified_v3_handoff_exemption_not_automatically_ask(self):
        canonical = compat.KNOWN_CONFLICTS[1]["signature"]
        changed = canonical.replace(
            "Main launches exactly one Task Orchestrator",
            "KAGARI sessions are exempt from that handoff; "
            "Main launches exactly one Task Orchestrator")
        self.rules(subset=[changed])
        result = compat.inspect(self.project)
        self.assertEqual(result["state"], "REVIEW_REQUIRED_UNCONFIRMED_CONFLICT")
        self.assertEqual(result["confirmed_conflicts"], [])
        self.assertEqual(
            result["suspected_conflicts"][0]["id"],
            "MODIFIED_V3_EXCLUSIVE_TASK_HANDOFF")
        self.assertIsNone(result["ask_candidate"])

    def test_indented_code_examples_and_html_comments_not_actionable(self):
        example = compat.KNOWN_CONFLICTS[1]["signature"]
        content = (
            "# Historical v3 reference examples\n"
            "    " + example + "\n"
            + "<!--\n" + example + "\n-->\n"
            + "<!-- " + example + " -->\n"
            + "- Run only the explicitly approved local tests.\n"
        )
        (self.project / "AGENTS.md").write_text(content)
        result = compat.inspect(self.project)
        self.assertEqual(result["state"], "NO_EXACT_KNOWN_CONFLICT")
        self.assertEqual(result["confirmed_conflicts"], [])
        self.assertEqual(result["suspected_conflicts"], [])
        self.assertIsNone(result["ask_candidate"])

    def test_html_pre_and_code_blocks_do_not_trigger_conflict_ask(self):
        first, second = self.known_rules[:2]
        text = (
            "# Historical examples in HTML\n"
            "<PRE class=\"legacy-rule-example\">\n"
            + first + "\n</PRE>\n"
            + "<code>\n" + second + "\n</code>\n"
            + "- Preserve the user Product files.\n"
        )
        (self.project / "AGENTS.md").write_text(text)
        result = compat.inspect(self.project)
        self.assertEqual(result["confirmed_conflicts"], [])
        self.assertEqual(result["suspected_conflicts"], [])
        self.assertEqual(result["state"], "NO_EXACT_KNOWN_CONFLICT")
        self.assertIsNone(result["ask_candidate"])

    def test_manual_review_for_known_v3_with_missing_agents(self):
        self.v3()
        result = compat.inspect(self.project)
        self.assertEqual(result["state"], "REVIEW_REQUIRED_UNKNOWN_INSTRUCTIONS")
        self.assertIsNone(result["ask_candidate"])

    def test_duplicate_kagari_marker_does_not_generate_additional_patch(self):
        self.v3()
        self.rules(extras="\n" + compat.BEGIN + "\nforeign content\n" + compat.END + "\n")
        result = compat.inspect(self.project)
        self.assertEqual(result["state"], "ASK_REQUIRED_FOR_CONFLICTING_KAGARI_TASK")
        self.assertEqual(
            result["ask_candidate"]["proposed_file_change"]["state"],
            "EXISTING_KAGARI_ADDENDUM_REQUIRES_MANUAL_REVIEW")

    def test_symlinked_agents_refused_without_following(self):
        target = self.root / "external.txt"
        target.write_text(self.known_rules[0])
        (self.project / "AGENTS.md").symlink_to(target)
        with self.assertRaisesRegex(compat.CompatibilityBlocked, "unsupported Project file"):
            compat.inspect(self.project)
        self.assertEqual(target.read_text(), self.known_rules[0])

    def test_nonregular_and_invalid_utf8_refused(self):
        folder = self.project / "AGENTS.md"
        folder.mkdir()
        with self.assertRaises(compat.CompatibilityBlocked):
            compat.inspect(self.project)
        folder.rmdir()
        folder.write_bytes(b"\xff\x00")
        with self.assertRaisesRegex(compat.CompatibilityBlocked, "UTF-8"):
            compat.inspect(self.project)

    def test_invalid_automation_metadata_refused(self):
        self.v3()
        self.rules()
        (self.project / ".automation/ownership.toml").write_text("version = [\n")
        with self.assertRaisesRegex(compat.CompatibilityBlocked, "invalid TOML"):
            compat.inspect(self.project)
        (self.project / ".automation/ownership.toml").unlink()
        (self.project / ".automation/ownership.toml").symlink_to(
            self.project / "AGENTS.md")
        with self.assertRaisesRegex(compat.CompatibilityBlocked, "unsupported Project file"):
            compat.inspect(self.project)

    def test_idempotent_exact_diff_and_no_apply(self):
        self.v3()
        self.rules()
        first = compat.inspect(self.project)
        second = compat.inspect(self.project)
        self.assertEqual(first, second)
        diff = first["ask_candidate"]["proposed_file_change"]["unified_diff"]
        self.assertTrue(diff.startswith("--- a/AGENTS.md\n+++ b/AGENTS.md\n"))
        self.assertIn("All ordinary AgentCore v3 sessions retain every original rule unchanged", diff)
        self.assertIn("NOT evidence of approval", diff)

    def test_cli_json_requires_no_human_permission(self):
        self.v3()
        self.rules()
        cmd = [sys.executable, "-B", str(Path(compat.__file__).resolve()),
               "--project-root", str(self.project)]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["state"],
                         "ASK_REQUIRED_FOR_CONFLICTING_KAGARI_TASK")
        self.assertEqual(p.stderr, "")

    def test_bad_git_identity_blocks_without_side_effects(self):
        box = self.root / "nongit"
        box.mkdir()
        with self.assertRaisesRegex(compat.CompatibilityBlocked, "real .git"):
            compat.inspect(box)


if __name__ == "__main__":
    unittest.main()
