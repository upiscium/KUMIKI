"""Local no-provider tests for the bounded native generic two-leaf Task slice."""
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
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import kagari_v010_native_generic_task as t
import kagari_v010_general_task_spec as specs


class NativeGenericTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kagari-native-generic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "docs").mkdir()
        (self.project / "tests").mkdir()
        (self.project / "README.md").write_text("Original documentation.\n")
        (self.project / "AGENTS.md").write_text(
            "Each worker document must contain local_nonce=f1e2d3c4b5a69788.\n"
            "Preserve staged README and do not edit settings.\n")
        (self.project / "tests/test_contract.py").write_text(
            "import pathlib,unittest\nroot=pathlib.Path(__file__).resolve().parents[1]\n"
            "class Contract(unittest.TestCase):\n"
            " def test_a(self):\n"
            "  self.assertIn('worker=A', (root/'docs/a.md').read_text())\n"
            " def test_b(self):\n"
            "  self.assertIn('worker=B', (root/'docs/b.md').read_text())\n")
        self.g("init", "-q", "-b", "main")
        self.g("add", "AGENTS.md", "README.md", "tests/test_contract.py")
        self.g("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
               "commit", "-qm", "baseline")
        self.head = self.g("rev-parse", "HEAD").decode()
        self.g("switch", "-q", "-c", "task/native")
        (self.project / "README.md").write_text("An existing staged user change.\n")
        self.g("add", "README.md")
        self.index = (self.project / ".git/index").read_bytes()
        self.output = self.root / "evidence"
        self.spec = {
            "schema_version": 1, "goal": "two disjoint docs from native leaves",
            "base_revision": self.head,
            "allowed_paths": ["docs/a.md", "docs/b.md"],
            "tests": [{"argv": [sys.executable, "-B", "-m", "unittest",
                               "discover", "-s", "tests", "-v"], "cwd": "."}],
            "acceptance": "both documentation files pass tests",
            "subagent_count": 2, "planning": "specified",
        }
        self.plan = {
            "schema_version": 1, "base_revision": self.head,
            "steps": [
                {"id": "sub-1", "role": "subagent", "agent": "build",
                 "depends_on": [], "allowed_paths": ["docs/a.md"],
                 "instruction": "write worker=A and local_nonce=f1e2d3c4b5a69788"},
                {"id": "sub-2", "role": "subagent", "agent": "build",
                 "depends_on": [], "allowed_paths": ["docs/b.md"],
                 "instruction": "write worker=B and local_nonce=f1e2d3c4b5a69788"},
                {"id": "integrate", "role": "primary", "agent": "build",
                 "depends_on": ["sub-1", "sub-2"],
                 "allowed_paths": [], "instruction": "host handles tests/commit"},
            ],
        }
        self.spec_file = self.root / "spec.json"
        self.plan_file = self.root / "plan.json"
        self.spec_file.write_text(json.dumps(self.spec))
        self.plan_file.write_text(json.dumps(self.plan))

    def g(self, *args):
        return t.git(self.project, *args)

    def write_products(self):
        (self.project / "docs/a.md").write_text(
            "worker=A local_nonce=f1e2d3c4b5a69788\n")
        (self.project / "docs/b.md").write_text(
            "worker=B local_nonce=f1e2d3c4b5a69788\n")

    def test_native_export_streams_large_session_to_exact_evidence_file(self):
        # A real OpenCode/Bun CLI can return a successful but 192 KiB-
        # truncated pipe capture. File-backed stdout preserves the full JSON.
        payload = {
            "info": {"id": "ses_bounded_test"},
            "messages": [{"parts": [{"text": "X" * (256 * 1024)}]}],
        }
        expected = json.dumps(payload).encode()
        self.assertGreater(len(expected), 192 * 1024)
        destination = self.root / "large-native-export.json"

        def export_to_file(argv, **kwargs):
            self.assertEqual(argv[-1], "ses_bounded_test")
            self.assertNotIn("capture_output", kwargs)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertEqual(argv[0], str(Path(sys.executable).resolve()))
            kwargs["stdout"].write(expected)
            return subprocess.CompletedProcess(argv, 0, stderr=b"")

        with patch.object(t.subprocess, "run", side_effect=export_to_file):
            observed = t._export_session(
                Path(sys.executable).resolve(), self.project, {},
                "ses_bounded_test", destination)
        self.assertEqual(observed, payload)
        self.assertEqual(destination.read_bytes(), expected)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.project / ".git/index").read_bytes(), self.index)

    def test_native_export_file_bound_refuses_oversized_payload(self):
        dest = self.root / "oversized-export.json"

        def output_too_large(argv, **kwargs):
            kwargs["stdout"].write(b"X" * (t.MAX_EXPORT + 1))
            return subprocess.CompletedProcess(argv, 0, stderr=b"")

        with patch.object(t.subprocess, "run", side_effect=output_too_large):
            with self.assertRaisesRegex(t.NativeBlocked, "exceeded size bound"):
                t._export_session(Path(sys.executable).resolve(),
                                  self.project, {}, "ses_big", dest)
        self.assertTrue(dest.exists())
        self.assertEqual((self.project / ".git/index").read_bytes(), self.index)

    def test_native_export_file_rejects_invalid_json_with_zero_exit(self):
        dest = self.root / "malformed-export.json"

        def truncated_json(argv, **kwargs):
            kwargs["stdout"].write(b'{"info":{"id":"ses_abc"},"messages":["')
            return subprocess.CompletedProcess(argv, 0, stderr=b"")

        with patch.object(t.subprocess, "run", side_effect=truncated_json):
            with self.assertRaisesRegex(t.NativeBlocked, "invalid native OpenCode session export"):
                t._export_session(Path(sys.executable).resolve(),
                                  self.project, {}, "ses_abc", dest)
        self.assertEqual((self.project / ".git/index").read_bytes(), self.index)

    def test_auto_plan_is_host_generated_before_product_model(self):
        self.spec["planning"] = "auto"
        self.spec_file.write_text(json.dumps(self.spec))
        observed = []
        def generate(project, spec, executable, root, *, timeout, verify_unmodified):
            self.assertEqual(project, self.project)
            self.assertEqual(spec["planning"], "auto")
            self.assertEqual(root, self.output / "planning")
            self.assertTrue(callable(verify_unmodified))
            verify_unmodified()
            observed.append("planner")
            return self.plan, {"mode": "auto",
                               "provenance": "HOST_OBSERVED_JSONL_TEST_ONLY",
                               "provider_identity_attested": False}
        with patch.object(t.model_planner, "generate", side_effect=generate) as planned, \
             patch.object(t, "spawn_model",
                          side_effect=t.NativeBlocked("INTENTIONAL_STOP_AFTER_PLAN")) as spawned:
            result = t.execute(
                self.project, self.spec_file, None,
                Path(sys.executable).resolve(), self.output,
                repository="fixture/kagari-native", task="271",
                consent=True, disposable=True)
        self.assertEqual(observed, ["planner"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["stage"], "model")
        self.assertEqual(result["planning"]["mode"], "auto")
        self.assertEqual(json.loads((self.output / "requested-task.json").read_bytes())["plan"],
                         self.plan)
        planned.assert_called_once()
        spawned.assert_called_once()
        self.assertEqual((self.project / ".git/index").read_bytes(), self.index)

    def test_auto_plan_cannot_be_supplied_as_external_plan(self):
        self.spec["planning"] = "auto"
        self.spec_file.write_text(json.dumps(self.spec))
        with patch.object(t.model_planner, "generate") as planned:
            with self.assertRaisesRegex(t.NativeBlocked,
                                        "must be generated by the host"):
                t.execute(
                    self.project, self.spec_file, self.plan_file,
                    Path(sys.executable).resolve(), self.output,
                    repository="fixture/kagari-native", task="271",
                    consent=True, disposable=True)
        planned.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_auto_planner_detects_mutated_git_info_not_visible_in_status(self):
        self.spec["planning"] = "auto"
        self.spec_file.write_text(json.dumps(self.spec))
        baseline_status = self.g("status", "--porcelain=v1")
        baseline_controls = t.git_control_inventory(self.project)

        def mutator(project, spec, executable, root, *,
                    timeout, verify_unmodified):
            git_exclude = self.project / ".git/info/exclude"
            git_exclude.write_text("*.private-kagari-hidden\\n")
            verify_unmodified()
            self.fail("Git control mutation should block Planning")

        with patch.object(t.model_planner, "generate", side_effect=mutator), \
             patch.object(t, "spawn_model") as child:
            report = t.execute(
                self.project, self.spec_file, None,
                Path(sys.executable).resolve(), self.output,
                repository="fixture/kagari-native", task="271",
                consent=True, disposable=True)
        self.assertEqual(report["stage"], "planning")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertNotEqual(t.git_control_inventory(self.project),
                            baseline_controls)
        self.assertEqual(self.g("status", "--porcelain=v1"), baseline_status)
        child.assert_not_called()
        self.assertFalse((self.output / "contract-binding.json").exists())

    def test_planner_project_mutation_blocks_before_contract_or_model(self):
        self.spec["planning"] = "auto"
        self.spec_file.write_text(json.dumps(self.spec))
        def malicious_planner(project, spec, executable, root, *,
                              timeout, verify_unmodified):
            (self.project / "README.md").write_text("Unapproved mutation!\\n")
            verify_unmodified()
            self.fail("planning unexpectedly accepted mutation")
        with patch.object(t.model_planner, "generate",
                          side_effect=malicious_planner), \
             patch.object(t, "spawn_model") as spawned:
            result = t.execute(
                self.project, self.spec_file, None,
                Path(sys.executable).resolve(), self.output,
                repository="fixture/kagari-native", task="271",
                consent=True, disposable=True)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["stage"], "planning")
        self.assertIn("auto Planning changed Project source", result["reason"])
        self.assertFalse((self.output / "contract-binding.json").exists())
        spawned.assert_not_called()
        self.assertEqual((self.project / ".git/index").read_bytes(), self.index)

    def test_generic_spec_plan_and_external_roles(self):
        t.guard_existing(self.project, self.output)
        spec = specs.load_task_spec(self.spec_file, self.project)
        t.verify_plan(spec, self.plan)
        pre = t.inventory(self.project)
        self.output.mkdir(mode=0o700)
        shas = t.write_roles(self.project, spec, self.plan, self.output / "roles")
        self.assertEqual(set(shas), {"kagari-v010-primary",
                                     "kagari-v010-leaf-a", "kagari-v010-leaf-b"})
        self.assertEqual(t.inventory(self.project), pre)
        agent = (self.output / "roles/agents/kagari-v010-leaf-a.md").read_text()
        self.assertIn(str(self.project / "docs/a.md"), agent)
        self.assertNotIn(str(self.project / "docs/b.md"), agent)
        self.assertIn("mode: subagent", agent)
        self.assertNotIn("model:", agent)
        parent = t.prompt_primary(self.project, spec, self.plan)
        self.assertIn("FIRST action", parent)
        self.assertIn("local_nonce=f1e2d3c4b5a69788", parent)
        self.assertEqual((self.project / ".git/index").read_bytes(), self.index)

    def test_leaf_completion_is_distinct_from_host_owned_tests(self):
        # Issue #274: no leaf may claim acceptance-test PASS, nor fail its
        # Work Unit solely because bash/test execution belongs to the host.
        t.guard_existing(self.project, self.output)
        self.output.mkdir(mode=0o700)
        t.write_roles(self.project, self.spec, self.plan, self.output / "roles")
        primary = t.prompt_primary(self.project, self.spec, self.plan)
        self.assertIn("only the KAGARI HOST runs tests", primary)
        self.assertIn("tests: NOT_RUN_BY_LEAF (HOST_OWNED)", primary)
        self.assertIn("status: BLOCKED if any actual leaf operation fails", primary)
        for name in ("kagari-v010-leaf-a", "kagari-v010-leaf-b"):
            role = (self.output / "roles/agents" / (name + ".md")).read_text()
            self.assertIn("KAGARI HOST, not this leaf, runs ALL declared TaskSpec tests", role)
            self.assertIn("tests: NOT_RUN_BY_LEAF (HOST_OWNED)", role)
            self.assertIn("Inability to run HOST-owned tests is NOT a leaf blocker", role)
            self.assertIn("status: COMPLETED", role)
            self.assertIn("status: BLOCKED", role)
            for deny in ("  bash: deny", "  task: deny", "  question: deny",
                         "  external_directory: deny"):
                self.assertIn(deny, role)

    def test_leaf_status_never_auto_promotes_real_blocked_to_completed(self):
        # The host, not the leaf, runs tests. But a failed *leaf* edit/read
        # stays BLOCKED even if its patch was partially applied.
        role = "kagari-v010-leaf-a"
        parent = "ses_parent_274"
        model = {"providerID": "openai", "modelID": "fixture"}
        target = str(self.project / "docs/a.md")
        write = {"type": "tool", "tool": "apply_patch",
                 "state": {"status": "completed",
                           "input": {"patchText": "*** Begin Patch\n"
                           "*** Add File: " + target + "\n+worker=A\n*** End Patch"}}}
        read = {"type": "tool", "tool": "read",
                "state": {"status": "completed", "input": {"filePath": target}}}
        def child(status):
            return {"info": {"parentID": parent, "agent": role,
                             "directory": str(self.project),
                             "model": {"id": "fixture", "providerID": "openai"}},
                    "messages": [{"info": {"role": "assistant"}, "parts": [
                        write, read, {"type": "text", "text": status}]}]}
        t._child_tools(child("status: COMPLETED\n"
                             "tests: NOT_RUN_BY_LEAF (HOST_OWNED)\n"),
                       role, parent, model, self.project, {"docs/a.md"})
        with self.assertRaisesRegex(t.NativeBlocked,
                                    "native leaf write/read/COMPLETED"):
            t._child_tools(child("status: BLOCKED\n"
                                 "reason: actual edit tool refused\n"),
                           role, parent, model, self.project, {"docs/a.md"})

    def test_terminal_status_parsing_rejects_conflicting_reports(self):
        self.assertEqual(t._leaf_status_lines(
            "<task_result>\nstatus: COMPLETED\n</task_result>"), ["COMPLETED"])
        self.assertEqual(t._leaf_status_lines(
            "status: COMPLETED\nstatus: BLOCKED\n"), ["COMPLETED", "BLOCKED"])
        self.assertEqual(t._leaf_status_lines("status: BLOCKED\n"), ["BLOCKED"])
        self.assertEqual(t._leaf_status_lines("status: COMPLETED fake"), [])
        self.assertEqual(t._leaf_status_lines("status: COMPLETED.\n"), ["COMPLETED"])

        role = "kagari-v010-leaf-a"
        parent = "ses_parent_274"
        model = {"providerID": "openai", "modelID": "fixture"}
        target = str(self.project / "docs/a.md")
        write = {"type": "tool", "tool": "apply_patch",
                 "state": {"status": "completed", "input": {
                     "patchText": "*** Begin Patch\n*** Add File: " + target
                     + "\n+worker=A\n*** End Patch"}}}
        read = {"type": "tool", "tool": "read",
                "state": {"status": "completed", "input": {"filePath": target}}}
        record = {"info": {"parentID": parent, "agent": role,
                           "directory": str(self.project),
                           "model": {"id": "fixture", "providerID": "openai"}},
                  "messages": [{"info": {"role": "assistant"},
                                "parts": [write, read,
                                          {"type": "text", "text": "status: COMPLETED\n"},
                                          {"type": "text", "text": "status: BLOCKED\n"}]}]}
        with self.assertRaisesRegex(t.NativeBlocked, "native leaf write/read/COMPLETED"):
            t._child_tools(record, role, parent, model, self.project, {"docs/a.md"})

    def test_fail_closed_unapproved_out_of_scope_or_unsafe_plan(self):
        wrong = copy.deepcopy(self.plan)
        wrong["steps"][1]["allowed_paths"] = ["docs/a.md"]
        with self.assertRaises(ValueError):
            t.verify_plan(self.spec, wrong)
        omitted = copy.deepcopy(self.plan)
        omitted["steps"][1]["allowed_paths"] = ["docs/other.md"]
        with self.assertRaises(ValueError):
            t.verify_plan(self.spec, omitted)
        self.spec["planning"] = "auto"
        with self.assertRaises(t.NativeBlocked):
            t.verify_plan(self.spec, self.plan)
        self.spec["planning"] = "specified"
        self.assertFalse(self.output.exists())
        with self.assertRaisesRegex(t.NativeBlocked, "authorization"):
            t.execute(self.project, self.spec_file, self.plan_file,
                      Path(sys.executable), self.output,
                      repository="fixture/kagari-native", task="271",
                      consent=False, disposable=True)
        self.assertFalse(self.output.exists())

    def test_v3_agents_conflict_blocks_before_task_or_model_and_shows_ask(self):
        (self.project / ".automation").mkdir()
        (self.project / ".automation/VERSION").write_text("3\n")
        (self.project / ".automation/UPSTREAM").write_text(
            'repository = "github:upiscium/Templates"\n'
            'component = "components/agent-core"\n')
        (self.project / ".automation/ownership.toml").write_text(
            'version = 1\n[paths]\n"AGENTS.md" = "replace"\n')
        agents = self.project / "AGENTS.md"
        agents.write_text("# Legacy v3 directives\n" + "\n".join(
            rule["signature"] for rule in t.agents_compat.KNOWN_CONFLICTS) + "\n")
        before_bytes = agents.read_bytes()
        before_index = (self.project / ".git/index").read_bytes()
        with self.assertRaises(t.AgentsCompatibilityAsk) as observed:
            t.execute(self.project, self.spec_file, self.plan_file,
                      Path(sys.executable).resolve(), self.output,
                      repository="fixture/kagari-native", task="271",
                      consent=True, disposable=True)
        diagnostic = observed.exception.diagnostic
        self.assertEqual(diagnostic["state"],
                         "ASK_REQUIRED_FOR_CONFLICTING_KAGARI_TASK")
        self.assertEqual(len(diagnostic["confirmed_conflicts"]), 4)
        self.assertEqual(
            diagnostic["ask_candidate"]["proposed_file_change"]["preimage_sha256"],
            t.sha(before_bytes))
        self.assertFalse(diagnostic["human_permission_granted"])
        self.assertFalse(diagnostic["automatic_file_edit_available"])
        self.assertEqual(agents.read_bytes(), before_bytes)
        self.assertEqual((self.project / ".git/index").read_bytes(), before_index)
        self.assertFalse(self.output.exists())

    def test_uncertain_custom_agents_rule_blocks_without_ask(self):
        (self.project / "AGENTS.md").write_text(
            "# Custom policy\n- Never delegate to subagents.\n")
        before = (self.project / "AGENTS.md").read_bytes()
        with self.assertRaisesRegex(t.NativeBlocked,
                                    "AGENTS_COMPAT_MANUAL_REVIEW_REQUIRED"):
            t.execute(self.project, self.spec_file, self.plan_file,
                      Path(sys.executable).resolve(), self.output,
                      repository="fixture/kagari-native", task="271",
                      consent=True, disposable=True)
        self.assertEqual((self.project / "AGENTS.md").read_bytes(), before)
        self.assertFalse(self.output.exists())

    def test_mixed_known_v3_and_custom_prohibition_blocks_without_ask(self):
        agents = self.project / "AGENTS.md"
        agents.write_text(
            "# Mixed legacy/custom policy\n" +
            t.agents_compat.KNOWN_CONFLICTS[0]["signature"] +
            "\n- Never delegate to subagents.\n")
        original = agents.read_bytes()
        index_before = (self.project / ".git/index").read_bytes()
        with self.assertRaisesRegex(t.NativeBlocked,
                                    "AGENTS_COMPAT_MANUAL_REVIEW_REQUIRED"):
            t.execute(self.project, self.spec_file, self.plan_file,
                      Path(sys.executable).resolve(), self.output,
                      repository="fixture/kagari-native", task="271",
                      consent=True, disposable=True)
        self.assertEqual(agents.read_bytes(), original)
        self.assertEqual((self.project / ".git/index").read_bytes(), index_before)
        self.assertFalse(self.output.exists())

    def _prepare_two_human_asks(self):
        from io import StringIO

        class TTY(StringIO):
            def isatty(self):
                return True

        original = (self.project / "AGENTS.md").read_text()
        (self.project / "AGENTS.md").write_text(
            original + "\n" +
            "\n".join(rule["signature"]
                      for rule in t.agents_compat.KNOWN_CONFLICTS) + "\n")
        (self.project / "opencode.json").write_text(
            '{"permission":{"task":"deny"},"default_agent":"build"}\n')
        self.agents_ask = self.root / "agents-ask"
        self.permission_ask = self.root / "permission-ask"
        source = Path(t.__file__).resolve()
        proposals = {}
        for kind, folder in (
            (t.human_ask.KIND_AGENTS, self.agents_ask),
            (t.human_ask.KIND_PERMISSION, self.permission_ask)
        ):
            proposals[kind] = t.human_ask.make_request(
                self.project, folder, source, kind)
        return source, proposals, TTY

    def _approve_two_simulated_test_only(self, TTY):
        # Test-only replacement of local terminal input; not a real human
        # authorization nor a production authority boundary.
        for folder in (self.agents_ask, self.permission_ask):
            request = t.human_ask.load_request(folder)
            with patch.object(t.human_ask.sys, "stdin", TTY()), \
                 patch.object(t.human_ask.sys, "stdout", TTY()), \
                 patch.object(t.human_ask, "require_foreground_terminal"), \
                 patch("builtins.input",
                       return_value="APPROVE " + request["request_id"]):
                t.human_ask.respond(folder, "approve")

    def test_human_ask_two_independent_unanswered_requests_block(self):
        source, proposals, _ = self._prepare_two_human_asks()
        original = (self.project / "AGENTS.md").read_bytes()
        with self.assertRaises(t.AgentsCompatibilityAsk):
            t.execute(self.project, self.spec_file, self.plan_file,
                      Path(sys.executable).resolve(), self.output,
                      repository="fixture/kagari-native", task="271",
                      consent=True, disposable=True,
                      agents_ask_root=self.agents_ask,
                      permission_ask_root=self.permission_ask)
        self.assertFalse(self.output.exists())
        self.assertEqual((self.project / "AGENTS.md").read_bytes(), original)
        self.assertFalse((self.agents_ask / "decision.json").exists())
        self.assertFalse((self.permission_ask / "decision.json").exists())

    def test_approved_agents_without_permission_still_blocked(self):
        source, proposals, TTY = self._prepare_two_human_asks()
        request = t.human_ask.load_request(self.agents_ask)
        with patch.object(t.human_ask.sys, "stdin", TTY()), \
             patch.object(t.human_ask.sys, "stdout", TTY()), \
             patch.object(t.human_ask, "require_foreground_terminal"), \
             patch("builtins.input", return_value="APPROVE " + request["request_id"]):
            t.human_ask.respond(self.agents_ask, "approve")
        t.human_ask.apply_agents(self.agents_ask, self.project, source)
        with self.assertRaisesRegex(t.NativeBlocked, "Project task:deny"):
            t.execute(self.project, self.spec_file, self.plan_file,
                      Path(sys.executable).resolve(), self.output,
                      repository="fixture/kagari-native", task="271",
                      consent=True, disposable=True,
                      agents_ask_root=self.agents_ask)
        self.assertFalse(self.output.exists())

    def test_both_asks_approved_reach_only_host_model_boundary(self):
        source, proposals, TTY = self._prepare_two_human_asks()
        self._approve_two_simulated_test_only(TTY)
        t.human_ask.apply_agents(self.agents_ask, self.project, source)
        original_index = (self.project / ".git/index").read_bytes()
        with patch.object(t, "spawn_model",
                          side_effect=t.NativeBlocked("INTENTIONAL_MODEL_STOP")) as invoked:
            report = t.execute(
                self.project, self.spec_file, self.plan_file,
                Path(sys.executable).resolve(), self.output,
                repository="fixture/kagari-native", task="271",
                consent=True, disposable=True,
                agents_ask_root=self.agents_ask,
                permission_ask_root=self.permission_ask)
        invoked.assert_called_once()
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["stage"], "model")
        self.assertEqual(len(report["human_ask_grants"]), 2)
        self.assertEqual({v["kind"] for v in report["human_ask_grants"]},
                         {t.human_ask.KIND_AGENTS, t.human_ask.KIND_PERMISSION})
        self.assertFalse(report["release_ready"])
        self.assertEqual((self.project / ".git/index").read_bytes(), original_index)
        self.assertIn(t.agents_compat.BEGIN,
                      (self.project / "AGENTS.md").read_text())
        self.assertTrue((self.output / "contract-binding.json").exists())

    def test_revoked_permission_blocks_even_if_agents_granted(self):
        source, proposals, TTY = self._prepare_two_human_asks()
        self._approve_two_simulated_test_only(TTY)
        t.human_ask.apply_agents(self.agents_ask, self.project, source)
        t.human_ask.revoke(self.permission_ask, self.project, source)
        with self.assertRaisesRegex(t.NativeBlocked, "HUMAN_ASK_GRANT_BLOCKED"):
            t.execute(self.project, self.spec_file, self.plan_file,
                      Path(sys.executable).resolve(), self.output,
                      repository="fixture/kagari-native", task="271",
                      consent=True, disposable=True,
                      agents_ask_root=self.agents_ask,
                      permission_ask_root=self.permission_ask)
        self.assertFalse(self.output.exists())

    def test_exact_physical_scope_and_disallowed_files(self):
        baseline = t.inventory(self.project)
        self.write_products()
        observed = t.inventory(self.project)
        allowed = {"docs/a.md", "docs/b.md"}
        self.assertEqual(t._scope_delta(baseline, observed, allowed), allowed)
        (self.project / "docs/unknown.md").write_text("foreign\n")
        with self.assertRaisesRegex(t.NativeBlocked, "outside or missing"):
            t._scope_delta(baseline, t.inventory(self.project), allowed)

    def test_model_git_gate_accepts_only_tracked_authorized_product_diffs(self):
        # The real M1 user case changes tracked Python product modules; Git
        # must show their dirty paths while an existing staged README remains.
        self.write_products()
        self.g("add", "--", "docs/a.md", "docs/b.md")
        self.g("-c", "core.hooksPath=" + os.devnull,
               "-c", "commit.gpgSign=false", "-c", "user.name=Fixture",
               "-c", "user.email=fixture@example.invalid",
               "commit", "--only", "-qm", "track products", "--",
               "docs/a.md", "docs/b.md")
        self.assertEqual(t.git_lines(
            self.project, "diff", "--cached", "--name-only", "-z"),
            ["README.md"])
        stage = t.git_lines(
            self.project, "diff", "--cached", "--name-only", "-z")
        dirty = t.git_lines(self.project, "diff", "--name-only", "-z")
        self.assertEqual(dirty, [])
        tracked_allowed = set(t.git_lines(
            self.project, "ls-files", "--cached", "-z", "--",
            "docs/a.md", "docs/b.md"))
        self.assertEqual(tracked_allowed, {"docs/a.md", "docs/b.md"})
        with (self.project / "docs/a.md").open("a") as out:
            out.write("real Product A change\n")
        with (self.project / "docs/b.md").open("a") as out:
            out.write("real Product B change\n")
        t.verify_model_git_diff(self.project, stage, dirty, tracked_allowed)
        self.assertEqual(self.g("diff", "--cached", "--name-only").decode(),
                         "README.md")

        # A new dirty path outside the TaskSpec is never accepted, even when
        # the staged index bytes themselves were preserved.
        with (self.project / "README.md").open("a") as out:
            out.write("malicious unrelated worktree edit\n")
        with self.assertRaisesRegex(t.NativeBlocked,
                                    "Model changed staged or existing dirty state"):
            t.verify_model_git_diff(self.project, stage, dirty, tracked_allowed)

    def test_model_git_gate_allows_new_untracked_authorized_products(self):
        # Historical doc creation tasks produce untracked allowed files,
        # which do not yet appear in git diff --name-only.
        stage = t.git_lines(
            self.project, "diff", "--cached", "--name-only", "-z")
        dirty = t.git_lines(self.project, "diff", "--name-only", "-z")
        self.write_products()
        self.assertEqual(t.git_lines(
            self.project, "diff", "--name-only", "-z"), dirty)
        t.verify_model_git_diff(self.project, stage, dirty, set())

    def test_tests_then_host_commit_preserves_unrelated_stage(self):
        self.write_products()
        self.output.mkdir()
        outcome = t.run_tests(self.project, self.spec, self.output, t.git_env())
        self.assertEqual(len(outcome), 1)
        self.assertEqual(outcome[0]["returncode"], 0)
        self.g("add", "--", "docs/a.md", "docs/b.md")
        self.g("-c", "core.hooksPath=" + os.devnull,
               "-c", "commit.gpgSign=false", "-c", "user.name=Test Host",
               "-c", "user.email=test@example.invalid",
               "commit", "--only", "-qm", "scope only", "--",
               "docs/a.md", "docs/b.md")
        self.assertEqual(self.g("rev-parse", "HEAD^").decode(), self.head)
        self.assertEqual(t.git_lines(self.project, "diff-tree", "--no-commit-id",
                                     "--name-only", "-r", "-z", "HEAD"),
                         ["docs/a.md", "docs/b.md"])
        self.assertEqual(t.git_lines(self.project, "diff", "--cached",
                                     "--name-only", "-z"), ["README.md"])
        self.assertEqual(t.inventory(self.project)["docs/a.md"][0], "file")
        t.verify_committed_products(self.project,
                                    self.g("rev-parse", "HEAD").decode(),
                                    t.inventory(self.project),
                                    {"docs/a.md", "docs/b.md"})

    def test_committed_git_clean_filter_mismatch_refused(self):
        # Git clean filters can change committed bytes while tests observe the
        # original working-tree files. No Evidence may certify those blobs.
        (self.project / ".gitattributes").write_text("docs/*.md filter=change\n")
        self.g("add", ".gitattributes")
        self.g("-c", "user.name=Test Host",
               "-c", "user.email=test@example.invalid",
               "commit", "--only", "-qm", "test attribute", "--", ".gitattributes")
        self.g("config", "filter.change.clean", "sed s/worker/CLEANED/g")
        self.g("config", "filter.change.smudge", "cat")
        self.write_products()
        tested = t.inventory(self.project)
        self.g("add", "docs/a.md", "docs/b.md")
        self.g("-c", "user.name=Test Host",
               "-c", "user.email=test@example.invalid",
               "commit", "--only", "-qm", "filtered products", "--",
               "docs/a.md", "docs/b.md")
        commit = self.g("rev-parse", "HEAD").decode()
        with self.assertRaisesRegex(t.NativeBlocked, "committed Git blob"):
            t.verify_committed_products(self.project, commit, tested,
                                        {"docs/a.md", "docs/b.md"})

    def test_project_task_deny_requires_separate_test_only_opt_in(self):
        (self.project / "opencode.json").write_text(
            '{"permission":{"task":"deny"},"default_agent":"build"}\n')
        with self.assertRaisesRegex(t.NativeBlocked, "Project task:deny requires"):
            t.execute(self.project, self.spec_file, self.plan_file,
                      Path(sys.executable).resolve(), self.output,
                      repository="fixture/kagari-native", task="271",
                      consent=True, disposable=True)
        self.assertFalse(self.output.exists())
        self.assertTrue((self.project / "opencode.json").is_file())

    def test_native_v4_auto_planning_binding_and_tamper_detection(self):
        # Completely synthetic metadata fixtures; tests retained-data hashes,
        # not model/provider identity.
        self.spec["planning"] = "auto"
        self.output.mkdir(mode=0o700)
        planning_root = self.output / "planning"
        planning_root.mkdir(mode=0o700)
        plan_raw = t.model_planner.encoded(self.plan)
        (planning_root / "plan.json").write_bytes(plan_raw)
        stdout = b"".join(t.model_planner.encoded(item) for item in [
            {"type": "step_start", "sessionID": "ses_fixture_planner123",
             "part": {"type": "step-start"}},
            {"type": "text", "sessionID": "ses_fixture_planner123",
             "part": {"type": "text", "text": json.dumps(self.plan)}},
            {"type": "step_finish", "sessionID": "ses_fixture_planner123",
             "part": {"type": "step-finish"}},
        ])
        (planning_root / "stdout.jsonl").write_bytes(stdout)
        spec_digest = t.sha(json.dumps(self.spec, sort_keys=True).encode())
        plan_digest = t.sha(json.dumps(self.plan, sort_keys=True).encode())
        receipt = {
            "status": "HOST_OBSERVED_VALIDATED_PLAN_TEST_ONLY",
            "session_id": "ses_fixture_planner123",
            "event_types": {"step_start": 1, "text": 1, "step_finish": 1},
            "host_observed_model_output": True,
            "model_identity_independently_verified": False,
            "tool_events_observed": False,
            "stdout_sha256": t.sha(stdout),
            "plan_file_sha256": t.sha(plan_raw),
            "plan_contract_sha256": plan_digest,
            "task_spec_contract_sha256": spec_digest,
        }
        receipt_raw = t.model_planner.encoded(receipt)
        (planning_root / "receipt.json").write_bytes(receipt_raw)
        pb = {
            "mode": "auto", "provenance": "HOST_OBSERVED_JSONL_TEST_ONLY",
            "provider_identity_attested": False,
            "task_spec_contract_sha256": spec_digest,
            "plan_contract_sha256": plan_digest,
            "plan_file_sha256": t.sha(plan_raw),
            "model_stdout_sha256": t.sha(stdout),
            "planning_receipt_sha256": t.sha(receipt_raw),
        }
        t.save(self.output / "requested-task.json",
               {"spec": self.spec, "plan": self.plan})
        native = t.NativeMetadata(self.project, self.output,
                                  "fixture/kagari-native", "271",
                                  "refs/heads/task/native", self.head)
        binding = native.create(self.spec, self.plan,
                                {"AGENTS.md": t.sha((self.project / "AGENTS.md").read_bytes())},
                                planning_binding=pb)
        self.write_products()
        self.g("add", "docs/a.md", "docs/b.md")
        self.g("-c", "core.hooksPath=" + os.devnull,
               "-c", "commit.gpgSign=false", "-c", "user.name=Fixture",
               "-c", "user.email=fixture@example.invalid",
               "commit", "--only", "-qm", "fixture products", "--",
               "docs/a.md", "docs/b.md")
        head = self.g("rev-parse", "HEAD").decode()
        (self.output / "test-0.log").write_bytes(b"fixture test passed\n")
        (self.output / "model.jsonl").write_bytes(b'{"type":"step_finish"}\n')
        exports = self.output / "native-session-exports"
        exports.mkdir()
        model = {
            "parent_session": "ses_fixture_parent",
            "children": [
                {"child_session": "ses_fixture_a"},
                {"child_session": "ses_fixture_b"},
            ],
            "overlap_ms": 7,
            "stdout_sha256": t.sha((self.output / "model.jsonl").read_bytes()),
            "export_sha256": {},
        }
        for kind, name in (("primary", "parent.json"), ("sub-1", "sub-1.json"),
                           ("sub-2", "sub-2.json")):
            file = exports / name
            file.write_bytes(("synthetic " + kind).encode())
            model["export_sha256"][kind] = t.sha(file.read_bytes())
        tests = [{"argv": ["fixture"], "returncode": 0,
                  "log_sha256": t.sha((self.output / "test-0.log").read_bytes())}]
        t.save(self.output / "test-receipts.json", tests)
        record = native.finish(binding, head, model, tests,
                               {"docs/a.md", "docs/b.md"},
                               planning_binding=pb)
        self.assertEqual(record["planning_binding"], pb)
        self.assertEqual(t.readback(record)["status"],
                         "FRESH_PROCESS_NATIVE_READBACK_PASS")
        receipt_file = planning_root / "receipt.json"
        original = receipt_file.read_bytes()
        receipt_file.write_bytes(b'{"forged":"receipt"}\n')
        with self.assertRaisesRegex(t.NativeBlocked, "Planning receipt retained artifact SHA mismatch"):
            t.readback(record)
        receipt_file.write_bytes(original)
        plan_file = planning_root / "plan.json"
        original = plan_file.read_bytes()
        plan_file.write_bytes(b'{"forged":"plan"}\n')
        with self.assertRaisesRegex(t.NativeBlocked, "model-produced Plan retained artifact SHA mismatch"):
            t.readback(record)
        plan_file.write_bytes(original)
        self.assertEqual(t.readback(record)["status"],
                         "FRESH_PROCESS_NATIVE_READBACK_PASS")

    def test_real_native_v4_contract_and_evidence_no_provider(self):
        self.output.mkdir(mode=0o700)
        native = t.NativeMetadata(self.project, self.output,
                                  "fixture/kagari-native", "271",
                                  "refs/heads/task/native", self.head)
        binding = native.create(self.spec, self.plan,
                                {"AGENTS.md": t.sha((self.project / "AGENTS.md").read_bytes())})
        native.revalidate(binding)
        self.write_products()
        self.g("add", "docs/a.md", "docs/b.md")
        self.g("-c", "core.hooksPath=" + os.devnull,
               "-c", "commit.gpgSign=false", "-c", "user.name=Test Host",
               "-c", "user.email=test@example.invalid",
               "commit", "--only", "-qm", "scope only", "--",
               "docs/a.md", "docs/b.md")
        head = self.g("rev-parse", "HEAD").decode()
        model = {
            "parent_session": "ses_fixture_parent",
            "children": [{"child_session": "ses_fixture_a"},
                         {"child_session": "ses_fixture_b"}],
            "overlap_ms": 50, "stdout_sha256": "a" * 64,
            "export_sha256": {"sub-1": "b" * 64, "sub-2": "c" * 64},
        }
        # Offline native evidence fixtures; no actual provider is called.
        log = self.output / "test-0.log"
        log.write_bytes(b"1 fixture test passed\\n")
        transcript = self.output / "model.jsonl"
        transcript.write_bytes(b'{"type":"step_finish"}\\n')
        exports = self.output / "native-session-exports"
        exports.mkdir()
        model["stdout_sha256"] = t.sha(transcript.read_bytes())
        for label, name in (("sub-1", "sub-1.json"), ("sub-2", "sub-2.json"),
                            ("primary", "parent.json")):
            path = exports / name
            path.write_bytes(("fixture export " + label).encode())
            model["export_sha256"][label] = t.sha(path.read_bytes())
        receipts = [{"argv": ["test"], "returncode": 0,
                     "log_sha256": t.sha(log.read_bytes())}]
        t.save(self.output / "test-receipts.json", receipts)
        persisted = native.finish(binding, head, model, receipts,
                                  {"docs/a.md", "docs/b.md"})
        self.assertEqual(t.readback(persisted)["status"],
                         "FRESH_PROCESS_NATIVE_READBACK_PASS")
        receipt_path = self.output / "test-receipts.json"
        original_receipts = receipt_path.read_bytes()
        receipt_path.write_text('[{"argv":["test"],"returncode":1}]')
        with self.assertRaisesRegex(t.NativeBlocked, "test receipt digest mismatch"):
            t.readback(persisted)
        receipt_path.write_bytes(original_receipts)
        before_log = log.read_bytes()
        log.write_bytes(b"tampered fixture test log")
        with self.assertRaisesRegex(t.NativeBlocked, "test log 0 retained artifact SHA mismatch"):
            t.readback(persisted)
        log.write_bytes(before_log)
        before_model = transcript.read_bytes()
        transcript.write_bytes(b"tampered model JSONL")
        with self.assertRaisesRegex(t.NativeBlocked, "model transcript retained artifact SHA mismatch"):
            t.readback(persisted)
        transcript.write_bytes(before_model)
        child_export = exports / "sub-1.json"
        before_export = child_export.read_bytes()
        child_export.write_bytes(b"tampered child session")
        with self.assertRaisesRegex(t.NativeBlocked, "session export sub-1 retained artifact SHA mismatch"):
            t.readback(persisted)
        child_export.write_bytes(before_export)
        self.assertEqual(t.readback(persisted)["status"],
                         "FRESH_PROCESS_NATIVE_READBACK_PASS")
        self.assertEqual(persisted["product_commit"], head)

    def test_native_child_must_read_after_final_edit(self):
        role = "kagari-v010-leaf-a"
        parent = "ses_parent_1"
        model = {"providerID": "openai", "modelID": "fixture"}
        target = str(self.project / "docs/a.md")
        patch_text = "*** Begin Patch\n*** Add File: " + target + "\n+worker=A\n*** End Patch"
        write = {"type": "tool", "tool": "apply_patch",
                 "state": {"status": "completed",
                           "input": {"patchText": patch_text}}}
        read = {"type": "tool", "tool": "read",
                "state": {"status": "completed",
                          "input": {"filePath": target}}}
        complete = {"type": "text", "text": "status: COMPLETED\n"}
        def record(parts):
            return {"info": {"parentID": parent, "agent": role,
                             "directory": str(self.project),
                             "model": {"id": "fixture", "providerID": "openai"}},
                    "messages": [{"info": {"role": "assistant"},
                                  "parts": parts}]}
        t._child_tools(record([write, read, complete]), role, parent,
                       model, self.project, {"docs/a.md"})
        for tools in ([read, write, complete],
                      [write, read, write, complete]):
            with self.subTest(order=[x.get("tool") for x in tools]):
                with self.assertRaisesRegex(t.NativeBlocked, "after its final write"):
                    t._child_tools(record(tools), role, parent, model,
                                   self.project, {"docs/a.md"})

    def test_native_parent_must_delegate_in_first_tool_batch(self):
        self.output.mkdir(mode=0o700)
        events = []
        parent = "ses_parent_1"
        for suffix in ("a", "b"):
            child = "ses_child_" + suffix
            events.append({
                "type": "tool_use", "sessionID": parent,
                "part": {
                    "sessionID": parent, "callID": "call_" + suffix,
                    "tool": "task",
                    "state": {"status": "completed",
                              "input": {"subagent_type": "kagari-v010-leaf-" + suffix},
                              "time": {"start": 100 if suffix == "a" else 105,
                                       "end": 300 if suffix == "a" else 320},
                              "metadata": {"sessionId": child,
                                           "parentSessionId": parent,
                                           "model": {"modelID": "fixture",
                                                     "providerID": "openai"}},
                              "output": '<task id="' + child +
                                        '" state="completed">\n'
                                        'status: COMPLETED\n</task>'},
                },
            })
        def parent_doc(first_tools):
            a = {"type": "tool", "tool": "task", "callID": "call_a"}
            b = {"type": "tool", "tool": "task", "callID": "call_b"}
            return {"info": {"id": parent, "agent": "kagari-v010-primary",
                             "directory": str(self.project)},
                    "messages": [
                        {"info": {"role": "user"}, "parts": [{"type": "text", "text": "request"}]},
                        {"info": {"role": "assistant"}, "parts": first_tools},
                        {"info": {"role": "assistant"}, "parts": [a, b]},
                    ]}
        current = []
        def fake_export(_exe, _project, _env, session, path):
            path.write_text("{}")
            if session == parent:
                return parent_doc(current)
            return {"info": {"id": session}}
        with patch.object(t, "_model_events", return_value=("x" * 64, events)), \
             patch.object(t, "_export_session", side_effect=fake_export), \
             patch.object(t, "_child_tools", return_value=None):
            current[:] = [{"type": "tool", "tool": "read", "callID": "call_foreign"}]
            with self.assertRaisesRegex(t.NativeBlocked, "first primary tool-call batch"):
                t.verify_delegation(Path(sys.executable), self.project,
                                    self.output, {}, self.plan)
        # A fresh output directory is required by the production flow. The
        # previous negative proof writes synthetic exports but no Product.
        first = self.output / "native-session-exports"
        for p in first.iterdir():
            p.unlink()
        first.rmdir()
        def successful_export(_exe, _project, _env, session, path):
            path.write_text("{}")
            if session == parent:
                val = parent_doc([{"type": "tool", "tool": "task", "callID": "call_a"},
                                  {"type": "tool", "tool": "task", "callID": "call_b"}])
                val["messages"] = val["messages"][:2]
                return val
            return {"info": {"id": session}}
        with patch.object(t, "_model_events", return_value=("x" * 64, events)), \
             patch.object(t, "_export_session", side_effect=successful_export), \
             patch.object(t, "_child_tools", return_value=None):
            proof = t.verify_delegation(Path(sys.executable), self.project,
                                        self.output, {}, self.plan)
            self.assertEqual(proof["native_tool_calls"], 2)
            self.assertGreater(proof["overlap_ms"], 0)

    def test_model_jsonl_fails_on_error_or_invalid(self):
        self.output.mkdir()
        (self.output / "model.jsonl").write_text('{"type":"error"}\n')
        with self.assertRaisesRegex(t.NativeBlocked, "provider error"):
            t._model_events(self.output / "model.jsonl")
        (self.output / "model.jsonl").write_text("bad json\n")
        with self.assertRaisesRegex(t.NativeBlocked, "invalid OpenCode JSONL"):
            t._model_events(self.output / "model.jsonl")


if __name__ == "__main__":
    unittest.main()
