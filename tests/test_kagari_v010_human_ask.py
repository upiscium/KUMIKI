"""Offline consent/Ask handshake and exact, reversible owner boundaries.

NO real Human grant is given by these tests. Each positive test mocks a
foreground terminal operator on independently disposable Git fixtures.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import kagari_v010_human_ask as ask
import kagari_v010_agents_compat as compat


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


class AskFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="kagari-human-ask-")
        self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name)
        self.project = self.root / "project"
        self.project.mkdir(mode=0o700)
        self.runtime_source = self.root / "runtime-source.py"
        self.runtime_source.write_text("# pinned fixture KAGARI runtime\n")
        self.agents_root = self.root / "ask-agents"
        self.permission_root = self.root / "ask-task"
        self.git("init", "-q", "-b", "main")
        (self.project / "README.md").write_text("Original user README\n")
        self.agents_file = self.project / "AGENTS.md"
        self.agents_file.write_text(
            "# Agent Operating Rules\n\n"
            + "\n".join(rule["signature"] for rule in compat.KNOWN_CONFLICTS)
            + "\n- Leaf agents must not create subagents.\n"
        )
        (self.project / "opencode.json").write_text(json.dumps({
            "$schema": "https://opencode.ai/config.json",
            "permission": {"task": "deny"},
            "default_agent": "build",
        }) + "\n")
        automation = self.project / ".automation"
        automation.mkdir()
        (automation / "VERSION").write_text("3\n")
        (automation / "UPSTREAM").write_text(
            'repository = "github:upiscium/Templates"\n'
            'ref = "main"\n'
            'component = "components/agent-core"\n'
        )
        (automation / "ownership.toml").write_text(
            'version = 1\n[paths]\n"AGENTS.md" = "replace"\n'
        )
        self.git("add", "-A")
        self.git("-c", "user.name=Fixture", "-c",
                 "user.email=fixture@example.invalid", "commit",
                 "-qm", "baseline")
        (self.project / "README.md").write_text(
            "Preserved unrelated user staged data\n")
        self.git("add", "README.md")
        self.before_head = self.git("rev-parse", "HEAD")
        self.before_index = (self.project / ".git/index").read_bytes()
        self.before_readme = (self.project / "README.md").read_bytes()
        self.before_agents = self.agents_file.read_bytes()
        self.before_config = (self.project / "opencode.json").read_bytes()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.project), *args], check=True,
            capture_output=True, timeout=20,
        ).stdout.strip()

    def assert_unrelated_preserved(self):
        self.assertEqual(self.before_head, self.git("rev-parse", "HEAD"))
        self.assertEqual(self.before_index, (self.project / ".git/index").read_bytes())
        self.assertEqual(self.before_readme, (self.project / "README.md").read_bytes())
        self.assertEqual(self.before_config, (self.project / "opencode.json").read_bytes())
        self.assertEqual(self.git("diff", "--cached", "--name-only"), b"README.md")

    def request(self, kind=ask.KIND_AGENTS):
        root = self.agents_root if kind == ask.KIND_AGENTS else self.permission_root
        return ask.make_request(self.project, root, self.runtime_source, kind)

    def simulated_human(self, folder, decision="approve", *, phrase=None):
        request = ask.load_request(folder)
        token = phrase if phrase is not None else decision.upper() + " " + request["request_id"]
        # The mock is test code only. The shipped CLI has no --approve/--yes
        # argument and requires sys.stdin + sys.stdout to be actual TTYs.
        with mock.patch.object(ask.sys, "stdin", FakeTTY()), \
             mock.patch.object(ask.sys, "stdout", FakeTTY()), \
             mock.patch.object(ask, "require_foreground_terminal"), \
             mock.patch("builtins.input", return_value=token):
            return ask.respond(folder, decision)

    def test_preview_only_does_not_edit_project_or_grant(self):
        proposal = self.request()
        self.assertEqual(proposal["kind"], ask.KIND_AGENTS)
        self.assertFalse(proposal["production_qualified"])
        self.assertEqual(proposal["confirmed_conflict_ids"],
                         sorted(rule["id"] for rule in compat.KNOWN_CONFLICTS))
        self.assertIn("KAGARI Human Ask", ask.render_ask(proposal))
        self.assertIn("AGENTS.md", proposal["unified_diff"])
        self.assertEqual(proposal["AGENTS_before_sha256"],
                         hashlib.sha256(self.before_agents).hexdigest())
        self.assertEqual(ask.status(
            self.agents_root, self.project, self.runtime_source)["status"],
            "PENDING_ASK")
        self.assertEqual(self.agents_file.read_bytes(), self.before_agents)
        self.assert_unrelated_preserved()
        self.assertFalse((self.agents_root / "decision.json").exists())

    def test_no_implicit_tty_consent(self):
        self.request()
        with mock.patch.object(ask.sys, "stdin", io.StringIO()), \
             mock.patch.object(ask.sys, "stdout", io.StringIO()):
            with self.assertRaisesRegex(ask.AskBlocked, "foreground interactive TTY"):
                ask.respond(self.agents_root, "approve")
        self.assertFalse((self.agents_root / "decision.json").exists())
        self.assertEqual(self.agents_file.read_bytes(), self.before_agents)

    def test_background_process_group_rejected_with_tty_streams(self):
        self.request()
        # Exercise just the foreground-terminal guard, never patch global
        # os.open/os.close while subprocess/Git may be using pipes.
        with mock.patch.object(ask.sys, "stdin", FakeTTY()), \
             mock.patch.object(ask.sys, "stdout", FakeTTY()), \
             mock.patch.object(ask.os, "tcgetpgrp", return_value=911), \
             mock.patch.object(ask.os, "getpgrp", return_value=912):
            with self.assertRaises(ask.AskBlocked):
                ask.require_foreground_terminal()
        self.assertFalse((self.agents_root / "decision.json").exists())

    def test_descendant_head_retains_project_scoped_permission(self):
        self.request(ask.KIND_PERMISSION)
        self.simulated_human(self.permission_root)
        (self.project / "next.txt").write_text("new Product\n")
        self.git("add", "next.txt")
        self.git("-c", "user.name=Fixture", "-c",
                 "user.email=fixture@example.invalid",
                 "commit", "--only", "-qm", "next Product", "--", "next.txt")
        active = ask.grant_status(
            self.permission_root, self.project,
            self.runtime_source, ask.KIND_PERMISSION)
        self.assertEqual(active["status"], "ACTIVE_SCOPED_GRANT_TEST_ONLY")

    def test_unrelated_older_head_invalidates_grant(self):
        # Ask on a descendant then move back to a revision predating the
        # approval; this is not ordinary forward Task progress.
        (self.project / "new-product.txt").write_text("first new Product\n")
        self.git("add", "new-product.txt")
        self.git("-c", "user.name=Fixture", "-c",
                 "user.email=fixture@example.invalid",
                 "commit", "--only", "-qm", "before approval",
                 "--", "new-product.txt")
        self.request(ask.KIND_PERMISSION)
        self.simulated_human(self.permission_root)
        self.git("reset", "--hard", "HEAD^")
        with self.assertRaisesRegex(ask.AskBlocked, "no longer descendant"):
            ask.grant_status(
                self.permission_root, self.project,
                self.runtime_source, ask.KIND_PERMISSION)

    def test_wrong_phrase_denied_without_record(self):
        self.request()
        with self.assertRaisesRegex(ask.AskBlocked, "confirmation phrase"):
            self.simulated_human(self.agents_root, phrase="APPROVE some-other-ask")
        self.assertFalse((self.agents_root / "decision.json").exists())
        self.assert_unrelated_preserved()

    def test_explicit_refusal_never_activates_or_modifies(self):
        self.request()
        result = self.simulated_human(self.agents_root, "deny")
        self.assertEqual(result["decision"], "DENIED")
        self.assertEqual(ask.status(
            self.agents_root, self.project, self.runtime_source)["status"],
            "DENIED")
        with self.assertRaisesRegex(ask.AskBlocked, "no positive"):
            ask.apply_agents(self.agents_root, self.project, self.runtime_source)
        with self.assertRaises(ask.AskBlocked):
            ask.grant_status(self.agents_root, self.project,
                             self.runtime_source, ask.KIND_AGENTS)
        self.assertFalse((self.agents_root / "activation.json").exists())
        self.assertEqual(self.agents_file.read_bytes(), self.before_agents)

    def test_approved_agents_exact_patch_grant_and_revoke(self):
        self.request()
        with self.assertRaisesRegex(ask.AskBlocked, "no positive"):
            ask.apply_agents(self.agents_root, self.project, self.runtime_source)
        receipt = self.simulated_human(self.agents_root)
        self.assertEqual(receipt["decision"], "APPROVED")
        self.assertEqual(ask.status(
            self.agents_root, self.project, self.runtime_source)["status"],
            "APPROVED_NOT_APPLIED")
        active = ask.apply_agents(self.agents_root, self.project, self.runtime_source)
        self.assertEqual(active["status"], "ACTIVE_SCOPED_GRANT_TEST_ONLY")
        self.assertEqual(active["kind"], ask.KIND_AGENTS)
        self.assertEqual(active["granted_roles"], list(ask.LEAVES))
        self.assertIn(compat.BEGIN.encode(), self.agents_file.read_bytes())
        self.assertTrue(self.agents_file.read_bytes().startswith(self.before_agents))
        self.assertEqual(ask.apply_agents(
            self.agents_root, self.project, self.runtime_source)["request_id"],
            active["request_id"])
        self.assert_unrelated_preserved()
        self.assertEqual(ask.status(
            self.agents_root, self.project, self.runtime_source)["status"],
            "ACTIVE_SCOPED_GRANT_TEST_ONLY")

        removed = ask.revoke(self.agents_root, self.project, self.runtime_source)
        self.assertEqual(removed["status"], "REVOKED_TEST_ONLY")
        self.assertTrue(removed["AGENTS_restored"])
        self.assertEqual(self.agents_file.read_bytes(), self.before_agents)
        self.assertEqual(ask.revoke(
            self.agents_root, self.project, self.runtime_source)["status"],
            "ALREADY_REVOKED")
        self.assertEqual(ask.status(
            self.agents_root, self.project, self.runtime_source)["status"],
            "REVOKED")
        with self.assertRaises(ask.AskBlocked):
            ask.grant_status(self.agents_root, self.project,
                             self.runtime_source, ask.KIND_AGENTS)
        self.assert_unrelated_preserved()

    def test_modified_approved_addendum_is_preserved_at_revoke(self):
        self.request()
        self.simulated_human(self.agents_root)
        ask.apply_agents(self.agents_root, self.project, self.runtime_source)
        self.agents_file.write_bytes(
            self.agents_file.read_bytes() + b"\n# foreign user edit\n")
        changed = self.agents_file.read_bytes()
        with self.assertRaisesRegex(ask.AskBlocked, "AGENTS.md changed"):
            ask.revoke(self.agents_root, self.project, self.runtime_source)
        self.assertEqual(self.agents_file.read_bytes(), changed)
        self.assertFalse((self.agents_root / "revocation.json").exists())
        status = ask.status(self.agents_root, self.project, self.runtime_source)
        self.assertEqual(status["status"], "BLOCKED_UNVERIFIED_GRANT")

    def test_stale_AGENTS_before_answer_is_not_authority(self):
        self.request()
        self.agents_file.write_bytes(
            self.agents_file.read_bytes() + b"\n# A user updated instructions\n")
        changed = self.agents_file.read_bytes()
        with self.assertRaisesRegex(ask.AskBlocked, "AGENTS.md changed"):
            self.simulated_human(self.agents_root)
        self.assertFalse((self.agents_root / "decision.json").exists())
        self.assertEqual(self.agents_file.read_bytes(), changed)

    def test_rewound_head_blocks_AGENTS_apply_without_mutation(self):
        # A previously authorized Project snapshot is no longer the active
        # Git history, although the Project AGENTS bytes remain identical.
        (self.project / "pre-ask-product.txt").write_text("new Product\n")
        self.git("add", "pre-ask-product.txt")
        self.git("-c", "user.name=Fixture", "-c",
                 "user.email=fixture@example.invalid", "commit",
                 "--only", "-qm", "pre-Ask baseline",
                 "--", "pre-ask-product.txt")
        self.request()
        self.simulated_human(self.agents_root)
        original = self.agents_file.read_bytes()
        self.git("reset", "--hard", "HEAD^")
        self.assertEqual(self.agents_file.read_bytes(), original)
        with self.assertRaisesRegex(ask.AskBlocked, "no longer descendant"):
            ask.apply_agents(
                self.agents_root, self.project, self.runtime_source)
        self.assertEqual(self.agents_file.read_bytes(), original)
        self.assertFalse((self.agents_root / "activation.json").exists())

    def test_stale_AGENTS_after_approval_blocks_apply(self):
        self.request()
        self.simulated_human(self.agents_root)
        self.agents_file.write_bytes(self.before_agents + b"\n# user edit\n")
        snapshot = self.agents_file.read_bytes()
        with self.assertRaisesRegex(ask.AskBlocked, "AGENTS.md changed"):
            ask.apply_agents(self.agents_root, self.project, self.runtime_source)
        self.assertEqual(self.agents_file.read_bytes(), snapshot)
        self.assertFalse((self.agents_root / "activation.json").exists())

    def test_permission_request_exact_roles_and_revoke(self):
        proposal = self.request(ask.KIND_PERMISSION)
        self.assertEqual(proposal["effective_task_allow"], list(ask.LEAVES))
        self.assertEqual(proposal["default_task_permission"], "deny")
        self.assertEqual(proposal["project_file_changes"], [])
        with self.assertRaises(ask.AskBlocked):
            ask.grant_status(self.permission_root, self.project,
                             self.runtime_source, ask.KIND_PERMISSION)
        approved = self.simulated_human(self.permission_root)
        self.assertEqual(approved["decision"], "APPROVED")
        active = ask.grant_status(self.permission_root, self.project,
                                  self.runtime_source, ask.KIND_PERMISSION)
        self.assertEqual(active["status"], "ACTIVE_SCOPED_GRANT_TEST_ONLY")
        self.assertEqual(active["granted_roles"], list(ask.LEAVES))
        self.assertEqual(self.agents_file.read_bytes(), self.before_agents)
        self.assertEqual((self.project / "opencode.json").read_bytes(),
                         self.before_config)
        self.assert_unrelated_preserved()
        outcome = ask.revoke(self.permission_root, self.project, self.runtime_source)
        self.assertEqual(outcome["status"], "REVOKED_TEST_ONLY")
        self.assertFalse(outcome["AGENTS_restored"])
        with self.assertRaises(ask.AskBlocked):
            ask.grant_status(self.permission_root, self.project,
                             self.runtime_source, ask.KIND_PERMISSION)

    def test_permission_request_denied_when_not_needed(self):
        (self.project / "opencode.json").write_text(
            json.dumps({"permission": {"task": "allow"}}))
        with self.assertRaisesRegex(ask.AskBlocked, "not denied"):
            self.request(ask.KIND_PERMISSION)
        self.assertFalse(self.permission_root.exists())

    def test_permission_config_drift_invalidates_consent(self):
        self.request(ask.KIND_PERMISSION)
        self.simulated_human(self.permission_root)
        (self.project / "opencode.json").write_bytes(
            self.before_config + b"\n")
        with self.assertRaisesRegex(ask.AskBlocked, "configuration changed"):
            ask.grant_status(self.permission_root, self.project,
                             self.runtime_source, ask.KIND_PERMISSION)

    def test_runtime_byte_drift_invalidates_grant(self):
        self.request(ask.KIND_PERMISSION)
        self.simulated_human(self.permission_root)
        self.runtime_source.write_text("# alternate KAGARI version\n")
        with self.assertRaisesRegex(ask.AskBlocked, "runtime bytes differ"):
            ask.grant_status(self.permission_root, self.project,
                             self.runtime_source, ask.KIND_PERMISSION)

    def test_changed_head_invalidates_unanswered_ticket(self):
        self.request()
        (self.project / "new.txt").write_text("other work\n")
        self.git("add", "new.txt")
        self.git("-c", "user.name=Fixture", "-c",
                 "user.email=fixture@example.invalid", "commit",
                 "-qm", "new work")
        with self.assertRaisesRegex(ask.AskBlocked, "HEAD changed"):
            self.simulated_human(self.agents_root)

    def test_duplicate_answer_and_unknown_record_are_blocked(self):
        self.request()
        self.simulated_human(self.agents_root)
        with self.assertRaisesRegex(ask.AskBlocked, "already answered"):
            self.simulated_human(self.agents_root)
        (self.agents_root / "foreign.txt").write_text("foreign file")
        with self.assertRaisesRegex(ask.AskBlocked, "unknown files"):
            ask.load_request(self.agents_root)
        self.assertEqual(self.agents_file.read_bytes(), self.before_agents)

    def test_modified_decision_record_invalidates_grant(self):
        self.request(ask.KIND_PERMISSION)
        self.simulated_human(self.permission_root)
        path = self.permission_root / "decision.json"
        doc = json.loads(path.read_bytes())
        doc["request_sha256"] = "0" * 64
        path.write_bytes(ask.canonical(doc))
        with self.assertRaisesRegex(ask.AskBlocked, "provenance"):
            ask.grant_status(self.permission_root, self.project,
                             self.runtime_source, ask.KIND_PERMISSION)

    def test_config_owner_symlinks_and_nonprivate_ask_root(self):
        self.request()
        foreign = self.root / "foreign-root"
        foreign.mkdir(mode=0o755)
        with self.assertRaisesRegex(ask.AskBlocked, "private euid-owned"):
            ask.make_request(
                self.project, foreign / "another-ask",
                self.runtime_source, ask.KIND_PERMISSION)
        self.assertFalse((foreign / "another-ask").exists())

    def test_cli_refuses_noninteractive_approval(self):
        self.request()
        module = pathlib.Path(ask.__file__).resolve()
        result = subprocess.run(
            [sys.executable, "-B", str(module), "approve",
             "--project-root", str(self.project),
             "--state-root", str(self.agents_root),
             "--runtime-source", str(self.runtime_source)],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "BLOCKED")
        self.assertFalse((self.agents_root / "decision.json").exists())
        self.assertEqual(self.agents_file.read_bytes(), self.before_agents)

    def test_exact_scope_cross_project_and_wrong_ask_kind(self):
        self.request()
        self.simulated_human(self.agents_root)
        self.permission_root.mkdir(mode=0o700)
        with self.assertRaisesRegex(ask.AskBlocked, "kind"):
            ask.grant_status(self.agents_root, self.project,
                             self.runtime_source, ask.KIND_PERMISSION)
        other = self.root / "other"
        other.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(other)],
                       capture_output=True, check=True)
        with self.assertRaisesRegex(ask.AskBlocked, "Project"):
            ask.grant_status(self.agents_root, other,
                             self.runtime_source, ask.KIND_AGENTS)
        self.assertEqual(self.agents_file.read_bytes(), self.before_agents)

    def test_custom_conflict_cannot_make_approval_ticket(self):
        self.agents_file.write_bytes(
            self.before_agents + b"\n- Never delegate to subagents.\n")
        with self.assertRaisesRegex(ask.AskBlocked, "confirmed AGENTS"):
            self.request()
        self.assertFalse(self.agents_root.exists())


if __name__ == "__main__":
    unittest.main()
