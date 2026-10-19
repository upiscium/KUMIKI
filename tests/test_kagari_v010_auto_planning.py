"""Offline M2 host-observed auto-Planning regression tests, no provider."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import kagari_v010_auto_planning as auto
import kagari_v010_basic_plan as protocol
import kagari_v010_native_generic_task as native


class AutoPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="kagari-auto-planner-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.project = self.folder / "project"
        self.project.mkdir()
        self.output = self.folder / "planning"
        self.executable = Path(sys.executable).resolve()
        self.spec = {
            "schema_version": 1,
            "goal": "Fix two real Python product modules independently",
            "base_revision": "a" * 40,
            "allowed_paths": ["tools/observability.py", "tools/session_state.py"],
            "tests": [{"argv": [sys.executable, "-B", "-m", "unittest"],
                       "cwd": "."}],
            "acceptance": "Both modules pass independent tests",
            "subagent_count": 2,
            "planning": "auto",
        }
        self.plan = {
            "schema_version": 1,
            "base_revision": self.spec["base_revision"],
            "steps": [
                {"id": "sub-1", "role": "subagent", "agent": "build",
                 "depends_on": [], "allowed_paths": ["tools/observability.py"],
                 "instruction": "Fix observability validation"},
                {"id": "sub-2", "role": "subagent", "agent": "build",
                 "depends_on": [], "allowed_paths": ["tools/session_state.py"],
                 "instruction": "Fix root parameter binding"},
                {"id": "integrate", "role": "primary", "agent": "build",
                 "depends_on": ["sub-1", "sub-2"],
                 "allowed_paths": [], "instruction": "Host tests and commits"},
            ],
        }
        self.session = "ses_auto_planning_123456789"

    def events(self, plan: dict | None = None, *, add_tools: bool = False) -> bytes:
        response = json.dumps(plan or self.plan, sort_keys=True)
        chunks = [
            {"type": "step_start", "sessionID": self.session,
             "part": {"type": "step-start"}},
            {"type": "text", "sessionID": self.session,
             "part": {"type": "text", "text": response}},
        ]
        if add_tools:
            chunks.append({"type": "tool_use", "sessionID": self.session,
                           "part": {"type": "tool", "tool": "read"}})
        chunks.append({"type": "step_finish", "sessionID": self.session,
                       "part": {"type": "step-finish"}})
        return b"".join(auto.encoded(event) for event in chunks)

    def invoke(self, jsonl: bytes, *, mutate: bool = False, rc: int = 0):
        checks = []

        def verify_readonly():
            checks.append("verified")
            if mutate:
                raise native.NativeBlocked("Project changed during Planning")

        def fake(argv, planner_cwd, env, stdout, stderr, timeout):
            self.assertEqual(argv[:3], [str(self.executable), "--pure", "run"])
            self.assertIn(auto.ROLE, argv)
            self.assertNotIn("--model", argv)
            self.assertEqual(planner_cwd, self.output)
            self.assertFalse(planner_cwd.is_relative_to(self.project))
            self.assertNotEqual(planner_cwd, self.project)
            self.assertEqual(env["OPENCODE_CONFIG_DIR"], str(self.output / "roles"))
            self.assertEqual(timeout, 30)
            stdout.write(jsonl)
            return rc

        root_existed_before_invoke = self.output.exists()
        with patch.object(auto, "run_planning_child", side_effect=fake):
            try:
                result = auto.generate(
                    self.project, self.spec, self.executable, self.output,
                    timeout=30, verify_unmodified=verify_readonly)
            except (auto.ModelPlanningBlocked, native.NativeBlocked):
                # Preflight refusal runs no subprocess or Project callback.
                # Once stdout exists, even a failed model must recheck state.
                self.assertEqual(checks,
                                 ["verified"] if (not root_existed_before_invoke
                                                  and (self.output / "stdout.jsonl").exists())
                                 else [])
                raise
        self.assertEqual(checks, ["verified"])
        return result

    def test_planner_assigns_tests_to_host_not_leaf(self):
        prompt = auto.planner_prompt(self.spec)
        self.assertIn("Leaf instructions cover ONLY", prompt)
        self.assertIn("MUST NOT assign test execution", prompt)
        self.assertIn("KAGARI HOST performs all TaskSpec tests", prompt)
        self.assertIn("Genuine failed leaf edits/readback remain BLOCKED", prompt)
        self.assertIn('"planning": "auto"', prompt)

    def test_host_observed_model_jsonl_plan_and_receipt(self):
        plan, binding = self.invoke(self.events())
        self.assertEqual(plan, self.plan)
        self.assertEqual(binding["mode"], "auto")
        self.assertEqual(binding["provenance"], "HOST_OBSERVED_JSONL_TEST_ONLY")
        self.assertFalse(binding["provider_identity_attested"])
        self.assertEqual(binding["model_stdout_sha256"],
                         hashlib.sha256(self.events()).hexdigest())
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        for name in ("stdout.jsonl", "stderr.raw", "prompt.txt", "receipt.json",
                     "plan.json", "invocation.json"):
            self.assertEqual((self.output / name).stat().st_mode & 0o777, 0o600)
        invocation = protocol.parse_single_json((self.output / "invocation.json").read_bytes())
        self.assertEqual(invocation["cwd"], str(self.output))
        self.assertEqual(invocation["task_context_project"], str(self.project))
        self.assertFalse(invocation["project_filesystem_access_required"])
        receipt = protocol.parse_single_json((self.output / "receipt.json").read_bytes())
        self.assertEqual(receipt["status"], "HOST_OBSERVED_VALIDATED_PLAN_TEST_ONLY")
        self.assertTrue(receipt["host_observed_model_output"])
        self.assertFalse(receipt["model_identity_independently_verified"])
        self.assertFalse(receipt["task_dispatched"])
        self.assertEqual(binding["planning_receipt_sha256"],
                         hashlib.sha256((self.output / "receipt.json").read_bytes()).hexdigest())
        self.assertEqual(binding["plan_file_sha256"],
                         hashlib.sha256((self.output / "plan.json").read_bytes()).hexdigest())
        self.assertTrue((self.output / "roles/agents/kagari-v010-planner.md").is_file())
        self.assertIn("task: deny", (self.output / "roles/agents/kagari-v010-planner.md").read_text())
        with self.assertRaisesRegex(auto.ModelPlanningBlocked, "fresh"):
            self.invoke(self.events())

    def test_untrusted_events_and_malformed_plan_fail_closed(self):
        invalid = [
            self.events(add_tools=True),
            self.events().replace(b'"type":"step_finish"', b'"type":"error"'),
            self.events().replace(b'"sessionID":"ses_auto_planning_123456789"',
                                  b'"sessionID":"ses_other_12345678"', 1),
            b'{"type":"text","type":"text","sessionID":"ses_123456789",'
            b'"part":{"type":"text","text":"{}"}}\n',
            self.events({**self.plan, "base_revision": "b" * 40}),
            self.events({**self.plan, "steps": self.plan["steps"][:-1]}),
        ]
        for index, jsonl in enumerate(invalid):
            with self.subTest(index=index):
                self.output = self.folder / f"blocked-{index}"
                with self.assertRaises((auto.ModelPlanningBlocked, protocol.PlanError)):
                    self.invoke(jsonl)
                self.assertFalse((self.output / "receipt.json").exists())
                self.assertFalse((self.output / "plan.json").exists())

    def test_read_only_project_mutation_and_model_nonzero_block(self):
        for key, options in (
            ("mutated", {"mutate": True}),
            ("nonzero", {"rc": 2}),
        ):
            self.output = self.folder / key
            with self.assertRaises((auto.ModelPlanningBlocked, native.NativeBlocked)):
                self.invoke(self.events(), **options)
            self.assertFalse((self.output / "receipt.json").exists())
            self.assertFalse((self.output / "plan.json").exists())

    def test_planner_cannot_use_project_cwd_or_create_first_run_git_marker(self):
        # A fresh actual Git Clone has no OpenCode workspace marker. The
        # model's tool-free setup previously created .git/opencode and the
        # strict before/after Git-control check correctly blocked the Task.
        subprocess.run(["git", "-C", str(self.project), "init", "-q"],
                       check=True, timeout=15, capture_output=True)
        self.assertFalse((self.project / ".git/opencode").exists())
        prior = native.git_control_inventory(self.project)
        observed = []

        def verify():
            observed.append("pass")
            self.assertEqual(native.git_control_inventory(self.project), prior)
            self.assertFalse((self.project / ".git/opencode").exists())

        def simulate_opencode(argv, planner_cwd, env, stdout, stderr, timeout):
            self.assertEqual(planner_cwd, self.output)
            self.assertFalse(planner_cwd.is_relative_to(self.project))
            self.assertFalse((planner_cwd / ".git").exists())
            # OpenCode may create its own initialization files. Those belong
            # in host Evidence, never the consumer's .git control directory.
            (planner_cwd / "opencode-preflight").write_text("host-only evidence")
            stdout.write(self.events())
            return 0

        with patch.object(auto, "run_planning_child", side_effect=simulate_opencode):
            plan, binding = auto.generate(
                self.project, self.spec, self.executable, self.output,
                timeout=30, verify_unmodified=verify)
        self.assertEqual(plan, self.plan)
        self.assertEqual(binding["mode"], "auto")
        self.assertEqual(observed, ["pass"])
        self.assertEqual(native.git_control_inventory(self.project), prior)

    def test_planner_refuses_evidence_cwd_inside_project(self):
        self.output = self.project / "planning-in-Product"
        with self.assertRaisesRegex(auto.ModelPlanningBlocked, "outside Project"):
            self.invoke(self.events())
        self.assertFalse(self.output.exists())

    def test_explicit_plan_must_not_claim_auto_provenance(self):
        self.spec["planning"] = "specified"
        with self.assertRaisesRegex(auto.ModelPlanningBlocked, "auto"):
            self.invoke(self.events())
        self.assertFalse(self.output.exists())

    def test_planner_subprocess_new_session_and_group_cleanup(self):
        class Child:
            pid = 43123
            def __init__(self, timeout=False):
                self.timeout = timeout
                self.calls = 0
            def wait(self, timeout):
                self.calls += 1
                if self.timeout and self.calls == 1:
                    raise subprocess.TimeoutExpired(["fake-planner"], timeout)
                return 0
        argv = [str(self.executable), "--pure", "run"]
        for should_timeout in (False, True):
            child = Child(timeout=should_timeout)
            with self.subTest(timeout=should_timeout), \
                 patch.object(auto.subprocess, "Popen", return_value=child) as spawned, \
                 patch.object(auto.os, "killpg") as killed:
                if should_timeout:
                    with self.assertRaisesRegex(auto.ModelPlanningBlocked, "timed out"):
                        auto.run_planning_child(
                            argv, self.project, {}, None, None, 20)
                else:
                    self.assertEqual(
                        auto.run_planning_child(argv, self.project, {}, None, None, 20), 0)
                self.assertTrue(spawned.call_args.kwargs["start_new_session"])
                self.assertEqual(spawned.call_args.kwargs["stdin"], subprocess.DEVNULL)
                killed.assert_called_once_with(child.pid, auto.signal.SIGKILL)
                self.assertGreaterEqual(child.calls, 1)

    def test_model_session_rejects_duplicate_and_missing_finish(self):
        with self.assertRaisesRegex(auto.ModelPlanningBlocked, "invalid planner JSONL"):
            auto.read_events(b'{"type":"text","type":"text",'
                             b'"sessionID":"ses_123456789",'
                             b'"part":{"type":"text","text":"{}"}}\n')
        without_finish = auto.encoded({
            "type": "step_start", "sessionID": self.session,
            "part": {"type": "step-start"}})
        with self.assertRaisesRegex(auto.ModelPlanningBlocked, "incomplete"):
            auto.read_events(without_finish)


if __name__ == "__main__":
    unittest.main()
