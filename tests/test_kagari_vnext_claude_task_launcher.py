"""#393 W1 launcher against a fake CLI; real-CLI evidence is recorded separately."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

from tools import kagari_vnext_claude_task_launcher as launcher
from tools.kagari_vnext_claude_task_launcher import (
    LaunchError, PluginAllowance, TaskLaunch, diagnose_instruction_files, launch_task,
)

BUILTIN = {"name": "cc-plugin-agents-md", "path": "builtin", "source": "cc-plugin-agents-md@builtin"}
AGENTS = ["claude", "Explore", "general-purpose", "Plan", "statusline-setup", "web-fetch"]
SUCCESS = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1}
ERROR = {"type": "result", "subtype": "success", "is_error": True, "api_error_status": 429}
PARENT = {
    "HOME": "/nonexistent", "PATH": os.environ["PATH"], "CLAUDECODE": "1",
    "CLAUDE_CODE_SESSION_ID": "parent-session", "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "3",
    "CLAUDE_CODE_PLUGIN_DIRS": "/parent/plugins", "ANTHROPIC_API_KEY": "parent-secret",
    "UNRELATED": "value",
}
FAKE_CLI = """\
    #!{python} -I
    import json, os, sys, time
    from pathlib import Path
    root = Path(__file__).resolve().parent
    if "--version" in sys.argv:
        print((root / "version").read_text().strip())
        raise SystemExit(0)
    (root / "capture.json").write_text(json.dumps(dict(
        argv=sys.argv[1:], env=dict(os.environ), cwd=os.getcwd(), stdin=sys.stdin.read())))
    plan = json.loads((root / "plan.json").read_text())
    if plan.get("touch"):
        Path(plan["touch"]).write_text("changed")
    for event in plan["events"]:
        print(json.dumps(event) if not isinstance(event, str) else event, flush=True)
    time.sleep(plan.get("sleep", 0))
    raise SystemExit(plan.get("exit", 0))
"""


def init_event(plugins=(BUILTIN,), agents=AGENTS, servers=("gate",)):
    return {"type": "system", "subtype": "init", "claude_code_version": "2.1.293", "model": "claude-sonnet-5-5",
            "permissionMode": "default", "plugins": list(plugins), "agents": list(agents), "skills": ["debug"],
            "mcp_servers": [{"name": name, "status": "connected"} for name in servers]}


def agent_use(tool_id, kind="Explore", background=None):
    tool_input = {"prompt": "x"}
    if kind is not None:
        tool_input["subagent_type"] = kind
    if background is not None:
        tool_input["run_in_background"] = background
    return {"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": "Agent", "input": tool_input}]}}


def agent_result(tool_id, text="[Subagent hand-back] ok", is_error=None):
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": [{"type": "text", "text": text}]}
    if is_error is not None:
        block["is_error"] = is_error
    return {"type": "user", "parent_tool_use_id": None, "message": {"content": [block]}}


class FixtureCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.claude = self.bin / "claude"
        self.claude.write_text(textwrap.dedent(FAKE_CLI.format(python=sys.executable)))
        self.claude.chmod(self.claude.stat().st_mode | stat.S_IXUSR)
        (self.bin / "version").write_text("2.1.293 (Claude Code)\n")
        repo = self.root / "repo"
        repo.mkdir()
        self.git("init", "-q", "-b", "main", cwd=repo)
        (repo / "README").write_text("fixture\n")
        self.git("add", "-A", cwd=repo)
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "init", cwd=repo)
        self.repo = repo
        self.worktree = self.root / "wt"
        self.git("worktree", "add", "-q", "-b", "task", str(self.worktree), cwd=repo)
        self.profile = self.root / "profile"
        self.profile.mkdir()
        self.mcp = self.root / "mcp.json"
        self.mcp.write_text(json.dumps({"mcpServers": {"gate": {"command": "x"}}}))
        self.evidence = self.root / "evidence"

    def git(self, *args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    def plan(self, events, **options):
        (self.bin / "plan.json").write_text(json.dumps({"events": events, **options}))

    def request(self, **changes):
        values = dict(
            executable=self.claude, worktree=self.worktree, evidence_dir=self.evidence,
            claude_config_dir=self.profile, prompt="do the task\n", task_rules="# rules\n",
            mcp_config=self.mcp, permission_prompt_tool="mcp__gate__approve", timeout=20.0)
        values.update(changes)
        return TaskLaunch(**values)

    def launch(self, **changes):
        return launch_task(self.request(**changes), PARENT)

    def capture(self):
        return json.loads((self.bin / "capture.json").read_text())

    def record(self):
        return json.loads((self.evidence / "launch.json").read_text())

    def assert_refused(self, code, **changes):
        with self.assertRaises(LaunchError) as caught:
            self.launch(**changes)
        self.assertEqual(caught.exception.code, code)
        self.assertFalse((self.bin / "capture.json").exists())


class EnvironmentAndArgvTests(FixtureCase):
    def test_ac01_child_environment_is_clean_and_recorded(self):
        self.plan([init_event(), SUCCESS])
        self.assertEqual(self.launch()["status"], "COMPLETED")
        env = self.capture()["env"]
        for name in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_PLUGIN_DIRS", "ANTHROPIC_API_KEY", "UNRELATED"):
            self.assertNotIn(name, env)
        self.assertEqual(env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"], "1")
        self.assertEqual(env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"], "1")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(self.profile))
        record = self.record()["environment"]
        self.assertNotIn("CLAUDECODE", record["names"])
        self.assertEqual(record["fixed"]["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"], "1")
        self.assertIn("CLAUDECODE", record["removed_parent_names"])
        self.assertNotIn("parent-secret", (self.evidence / "launch.json").read_text())

    def test_launch_form_prompt_rules_and_settings(self):
        self.plan([init_event(), SUCCESS])
        self.launch(model="sonnet")
        captured = self.capture()
        argv = captured["argv"]
        self.assertEqual(captured["cwd"], str(self.worktree))
        self.assertEqual(captured["stdin"], "do the task\n")
        self.assertEqual(argv[:4], ["-p", "--output-format", "stream-json", "--verbose"])
        for expected in ("--strict-mcp-config", f"--mcp-config={self.mcp}", "--setting-sources=project,local",
                         "--permission-prompt-tool=mcp__gate__approve", "--model=sonnet"):
            self.assertIn(expected, argv)
        self.assertIn(f"--append-system-prompt-file={self.evidence / 'task-rules.md'}", argv)
        self.assertEqual((self.evidence / "task-rules.md").read_text(), "# rules\n")
        settings = json.loads(next(item for item in argv if item.startswith("--settings="))[len("--settings="):])
        self.assertEqual(settings["permissions"]["allow"], [])
        self.assertEqual(settings["permissions"]["ask"], ["Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"])
        self.assertEqual(settings["env"], {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1",
                                           "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"})
        for name in ("general-purpose", "claude", "fork"):
            self.assertIn(f"Agent({name})", settings["permissions"]["deny"])

    def test_project_subagents_are_denied(self):
        agents = self.worktree / ".claude/agents"
        agents.mkdir(parents=True)
        (agents / "writer.md").write_text("---\nname: bold-writer\ndescription: x\n---\nbody\n")
        self.plan([init_event(agents=[*AGENTS, "bold-writer"]), SUCCESS])
        self.assertEqual(self.launch()["status"], "COMPLETED")
        self.assertIn("bold-writer", self.record()["denied_subagents"])


class RefusalTests(FixtureCase):
    def write_settings(self, value, name="settings.json", root=None):
        directory = (root or self.worktree) / ".claude"
        directory.mkdir(exist_ok=True)
        (directory / name).write_text(json.dumps(value))

    def test_unregistered_or_unlinked_worktree(self):
        self.assert_refused("WORKTREE_NOT_LINKED", worktree=self.repo)
        fake = self.root / "fake"
        fake.mkdir()
        (fake / ".git").write_text("gitdir: /nowhere\n")
        self.assert_refused("WORKTREE_NOT_REGISTERED", worktree=fake)

    def test_old_cli_version(self):
        (self.bin / "version").write_text("2.1.292 (Claude Code)\n")
        self.assert_refused("CLI_VERSION_UNSUPPORTED")

    def test_project_settings_are_recorded_and_never_block(self):
        self.write_settings({"hooks": {}, "permissions": {"allow": ["Bash(touch *)", "Read(src/**)"]}})
        self.plan([init_event(), SUCCESS])
        self.assertEqual(self.launch()["status"], "COMPLETED")
        recorded = self.record()["project_settings"]["worktree/.claude/settings.json"]
        self.assertEqual(recorded["keys"], ["hooks", "permissions"])
        self.assertEqual(recorded["non_read_only_allow"], ["Bash(touch *)"])

    def test_main_checkout_local_settings_are_recorded(self):
        # Claude reads settings.local.json from the main checkout in a worktree.
        self.write_settings({"permissions": {"allow": ["Bash(gh issue *)"]},
                             "env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "7"}},
                            name="settings.local.json", root=self.repo)
        self.plan([init_event(), SUCCESS])
        self.assertEqual(self.launch()["status"], "COMPLETED")
        recorded = self.record()["project_settings"]["main/.claude/settings.local.json"]
        self.assertEqual(recorded["path"], str(self.repo / ".claude/settings.local.json"))
        self.assertEqual(recorded["non_read_only_allow"], ["Bash(gh issue *)"])
        self.assertEqual(recorded["env_names"], ["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"])

    def test_bare_main_repository_has_no_main_checkout_settings(self):
        bare = self.root / "bare.git"
        self.git("clone", "-q", "--bare", str(self.repo), str(bare), cwd=self.root)
        worktree = self.root / "bare-wt"
        self.git("worktree", "add", "-q", str(worktree), "main", cwd=bare)
        self.plan([init_event(), SUCCESS])
        self.assertEqual(self.launch(worktree=worktree)["status"], "COMPLETED")
        self.assertEqual(self.record()["project_settings"], {})

    def test_invalid_project_settings_are_recorded_and_never_block(self):
        (self.worktree / ".claude").mkdir()
        (self.worktree / ".claude/settings.json").write_text("{")
        self.plan([init_event(), SUCCESS])
        self.assertEqual(self.launch()["status"], "COMPLETED")
        self.assertEqual(self.record()["project_settings"]["worktree/.claude/settings.json"], {"valid": False})

    def test_evidence_location_and_gate_configuration(self):
        self.assert_refused("EVIDENCE_DIR_INVALID", evidence_dir=self.worktree / "evidence")
        self.evidence.mkdir()
        self.assert_refused("EVIDENCE_DIR_INVALID")
        self.evidence.rmdir()
        self.assert_refused("PERMISSION_TOOL_NOT_CONFIGURED", permission_prompt_tool="mcp__other__approve")
        self.mcp.write_text("{}")
        self.assert_refused("MCP_CONFIG_INVALID")


class InstructionFileTests(FixtureCase):
    def diagnose(self):
        return diagnose_instruction_files(self.worktree)

    def assert_configuration(self, name, effective):
        result = self.diagnose()
        self.assertEqual((result["configuration"], result["agents_md_effective"]), (name, effective))

    def test_ac03_m1_agents_only(self):
        (self.worktree / "AGENTS.md").write_text("a\n")
        self.assert_configuration("agents_only", True)

    def test_ac03_m2_claude_masks_agents(self):
        (self.worktree / "AGENTS.md").write_text("a\n")
        (self.worktree / "CLAUDE.md").write_text("c\n")
        self.assert_configuration("claude_only", False)

    def test_ac03_m3_import(self):
        (self.worktree / "AGENTS.md").write_text("a\n")
        (self.worktree / "CLAUDE.md").write_text("c\n\n@AGENTS.md\n")
        self.assert_configuration("claude_imports_agents", True)

    def test_ac03_m4_local_masks_agents(self):
        (self.worktree / "AGENTS.md").write_text("a\n")
        (self.worktree / "CLAUDE.local.md").write_text("l\n")
        self.assert_configuration("claude_local_present", False)
        self.assertTrue(self.diagnose()["claude_local_present"])

    def test_no_files_and_quoted_import_and_ancestor(self):
        self.assert_configuration("none", False)
        (self.worktree / "AGENTS.md").write_text("a\n")
        (self.worktree / "CLAUDE.md").write_text("see `@AGENTS.md`\n\n```\n@AGENTS.md\n```\n")
        self.assert_configuration("claude_only", False)
        (self.worktree / "CLAUDE.md").unlink()
        (self.root / "CLAUDE.local.md").write_text("l\n")
        self.assert_configuration("claude_local_present", False)
        self.assertEqual([item["scope"] for item in self.diagnose()["files"]], ["worktree", "ancestor"])

    def test_diagnosis_is_recorded_at_launch(self):
        (self.worktree / "AGENTS.md").write_text("a\n")
        self.plan([init_event(), SUCCESS])
        self.launch()
        self.assertEqual(self.record()["instructions"]["configuration"], "agents_only")


class ProjectFilePreservationTests(FixtureCase):
    def test_ac02_bytes_and_absence_preserved(self):
        (self.worktree / "AGENTS.md").write_bytes(b"\xef\xbb\xbfa\r\n")
        self.plan([init_event(), SUCCESS])
        outcome = self.launch(task_rules="must-not-land-in-project\n")
        self.assertEqual(outcome["status"], "COMPLETED")
        self.assertTrue(outcome["project_files_unchanged"])
        self.assertEqual((self.worktree / "AGENTS.md").read_bytes(), b"\xef\xbb\xbfa\r\n")
        self.assertFalse((self.worktree / "CLAUDE.md").exists())
        self.assertFalse((self.worktree / "CLAUDE.local.md").exists())

    def test_ac02_session_change_fails_the_run(self):
        self.plan([init_event(), SUCCESS], touch=str(self.worktree / "CLAUDE.md"))
        outcome = self.launch()
        self.assertEqual((outcome["status"], outcome["reason"]), ("FAILED", "PROJECT_INSTRUCTIONS_CHANGED"))


class SubagentTests(FixtureCase):
    def test_ac04_denied_subagent_attempt_is_recorded_as_not_executed(self):
        self.plan([init_event(), agent_use("t1", "general-purpose"), agent_result("t1", "denied by rule", True),
                   agent_use("t2", None), agent_result("t2", "denied by rule", True), SUCCESS])
        outcome = self.launch()
        self.assertEqual(outcome["status"], "COMPLETED")
        self.assertEqual([(a["subagent_type"], a["executed"]) for a in outcome["agent_attempts"]],
                         [("general-purpose", False), (None, False)])

    def test_ac04_background_request_run_in_foreground_is_recorded(self):
        self.plan([init_event(), agent_use("t1", "Explore", True), agent_result("t1"), SUCCESS])
        outcome = self.launch()
        self.assertEqual(outcome["status"], "COMPLETED")
        attempt = outcome["agent_attempts"][0]
        self.assertTrue(attempt["run_in_background"])
        self.assertFalse(attempt["background_launched"])

    def assert_stopped(self, events, code):
        self.plan(events, sleep=30)
        outcome = self.launch()
        self.assertEqual((outcome["status"], outcome["reason"]), ("FAILED", code))
        self.assertLess(outcome["returncode"], 0)

    def test_ac04_background_launch_stops_the_session(self):
        self.assert_stopped([init_event(), agent_use("t1", "Explore", True),
                             agent_result("t1", "Async agent launched successfully.")], "BACKGROUND_SUBAGENT_STARTED")

    def test_ac04_background_system_event_stops_the_session(self):
        self.assert_stopped([init_event(), {"type": "system", "subtype": "background_tasks_changed"}],
                            "BACKGROUND_SUBAGENT_STARTED")

    def test_ac04_denied_subagent_that_ran_stops_the_session(self):
        self.assert_stopped([init_event(), agent_use("t1", "general-purpose"), agent_result("t1")],
                            "DENIED_SUBAGENT_EXECUTED")

    def test_unknown_subagent_type_in_init_stops_the_session(self):
        self.assert_stopped([init_event(agents=[*AGENTS, "new-builtin"])], "UNEXPECTED_SUBAGENT")


class CompletionTests(FixtureCase):
    def assert_outcome(self, events, status, reason, **options):
        self.plan(events, **options)
        outcome = self.launch()
        self.assertEqual((outcome["status"], outcome["reason"]), (status, reason))
        return outcome

    def test_ac05_stream_is_saved_as_evidence(self):
        self.plan([init_event(), SUCCESS])
        outcome = self.launch()
        saved = (self.evidence / "stream.jsonl").read_bytes()
        self.assertEqual([json.loads(line)["type"] for line in saved.splitlines()], ["system", "result"])
        self.assertEqual(outcome["stream_bytes"], len(saved))
        self.assertEqual(stat.S_IMODE((self.evidence / "stream.jsonl").stat().st_mode), 0o600)
        self.assertEqual(json.loads((self.evidence / "outcome.json").read_text())["status"], "COMPLETED")

    def test_ac05_last_result_decides_success_after_error(self):
        outcome = self.assert_outcome([init_event(), ERROR, SUCCESS], "COMPLETED", "RESULT_SUCCESS")
        self.assertEqual(outcome["result_count"], 2)

    def test_ac05_last_result_decides_error_after_success(self):
        outcome = self.assert_outcome([init_event(), SUCCESS, ERROR], "FAILED", "RESULT_ERROR")
        self.assertEqual(outcome["final_result"]["api_error_status"], 429)

    def test_events_after_last_result_do_not_change_the_decision(self):
        self.assert_outcome([init_event(), SUCCESS, {"type": "system", "subtype": "thinking_tokens"}],
                            "COMPLETED", "RESULT_SUCCESS")

    def test_error_result_with_success_subtype_fails(self):
        self.assert_outcome([init_event(), ERROR], "FAILED", "RESULT_ERROR", exit=1)

    def test_nonzero_exit_fails_even_after_success_result(self):
        self.assert_outcome([init_event(), SUCCESS], "FAILED", "PROCESS_EXIT_NONZERO", exit=1)

    def test_missing_or_malformed_stream(self):
        self.assert_outcome([SUCCESS], "FAILED", "NO_INIT_EVENT")
        self.evidence = self.root / "evidence2"
        self.assert_outcome([init_event()], "FAILED", "NO_RESULT_EVENT")
        self.evidence = self.root / "evidence3"
        self.assert_outcome([init_event(), "not json", SUCCESS], "FAILED", "MALFORMED_STREAM")

    def test_timeout_stops_the_process_group(self):
        self.plan([init_event()], sleep=30)
        outcome = self.launch(timeout=0.5)
        self.assertEqual((outcome["status"], outcome["reason"]), ("FAILED", "TIMEOUT"))

    def test_cli_version_and_model_are_recorded(self):
        self.plan([init_event(), {**SUCCESS, "modelUsage": {"claude-sonnet-5-5": {}}}])
        outcome = self.launch()
        self.assertEqual(outcome["cli_version"], "2.1.293")
        self.assertEqual(outcome["model"], "claude-sonnet-5-5")
        self.assertEqual(outcome["model_usage"], ["claude-sonnet-5-5"])
        self.assertEqual(self.record()["cli_version"], "2.1.293 (Claude Code)")

    def test_internal_error_does_not_leave_the_process_running(self):
        self.plan([init_event()], sleep=30)
        started = time.monotonic()
        with patch.object(launcher.StreamMonitor, "feed", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.launch()
        self.assertLess(time.monotonic() - started, 15)

    def test_malformed_init_items_fail_closed(self):
        event = init_event()
        event["mcp_servers"] = ["gate"]
        event["agents"] = [["nested"]]
        self.plan([event], sleep=30)
        self.assertEqual(self.launch()["status"], "FAILED")

    def test_gate_server_not_connected_stops_the_session(self):
        event = init_event()
        event["mcp_servers"] = [{"name": "gate", "status": "failed"}]
        self.plan([event], sleep=30)
        self.assertEqual(self.launch()["reason"], "MCP_SERVERS_MISMATCH")


class PluginAllowlistTests(FixtureCase):
    def inline(self, version="1.2.3"):
        return {"name": "reviewed", "path": "/p/reviewed", "source": "reviewed@inline", "version": version}

    def test_ac06_empty_allowlist_records_only_builtin_plugins(self):
        self.plan([init_event(), SUCCESS])
        outcome = self.launch()
        self.assertEqual(outcome["status"], "COMPLETED")
        self.assertEqual(outcome["plugins"], [])
        self.assertEqual([item["source"] for item in outcome["builtin_plugins"]], [BUILTIN["source"]])
        self.assertEqual(self.record()["plugin_allowlist"], [])
        self.assertFalse([item for item in self.capture()["argv"] if item.startswith("--plugin-dir")])

    def test_ac06_plugin_outside_allowlist_stops_the_session(self):
        rogue = {"name": "arca-issue-handoff", "path": "/p", "source": "arca-issue-handoff@arca-local", "version": "0.1.0"}
        self.plan([init_event(plugins=[rogue]), SUCCESS], sleep=30)
        outcome = self.launch()
        self.assertEqual((outcome["status"], outcome["reason"]), ("FAILED", "PLUGIN_NOT_ALLOWED"))
        self.assertEqual(outcome["violations"][0]["plugins"], ["arca-issue-handoff@arca-local"])
        self.assertEqual(outcome["plugins"][0]["version"], "0.1.0")

    def test_ac06_allowlisted_plugin_is_loaded_with_version_recorded(self):
        directory = self.root / "reviewed"
        directory.mkdir()
        self.plan([init_event(plugins=[self.inline(), BUILTIN]), SUCCESS])
        outcome = self.launch(plugin_allowlist=(PluginAllowance("reviewed@inline", directory),))
        self.assertEqual(outcome["status"], "COMPLETED")
        self.assertEqual(outcome["plugins"], [{"name": "reviewed", "source": "reviewed@inline", "version": "1.2.3"}])
        self.assertIn(f"--plugin-dir={directory}", self.capture()["argv"])

    def test_ac06_allowlisted_plugin_that_did_not_load_fails(self):
        directory = self.root / "reviewed"
        directory.mkdir()
        self.plan([init_event(), SUCCESS], sleep=30)
        outcome = self.launch(plugin_allowlist=(PluginAllowance("reviewed@inline", directory),))
        self.assertEqual(outcome["reason"], "PLUGIN_ALLOWLIST_NOT_LOADED")

    def test_ac06_missing_plugin_field_is_unverifiable(self):
        event = init_event()
        del event["plugins"]
        self.plan([event], sleep=30)
        self.assertEqual(self.launch()["reason"], "PLUGINS_UNVERIFIABLE")


if __name__ == "__main__":
    unittest.main()
