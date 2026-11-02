"""Offline fake-CLI tests for the unqualified synthetic Lane B candidate.

All authentication and model-shaped output in this module comes from a fake
executable and disposable local Git metadata fixtures. No real Claude account,
credential, host settings, or model transport is used.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import fcntl
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
import uuid
from unittest import mock

from tests import test_metadata_ref as fixtures
import task_record
from tools import kagari_vnext_claude_lane_b as lane
from tools import kagari_vnext_claude_preflight as preflight


class ClaudeLaneBOfflineFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.MetadataRefTest("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

        self.project = self.fixture.product
        self.base = fixtures.git("rev-parse", "HEAD", cwd=self.project).decode().strip()
        self.branch_ref = "refs/heads/issue-340"
        worktree_parent = self.project / ".worktrees"
        worktree_parent.mkdir()
        self.worktree = worktree_parent / "340"
        fixtures.git(
            "worktree", "add", "-b", "issue-340", str(self.worktree), self.base,
            cwd=self.project,
        )
        self.publication = task_record.TaskRecords(
            self.fixture.store, authorize=lambda _request: True
        ).create(
            task="340",
            branch_ref=self.branch_ref,
            base_revision=self.base,
            title="Synthetic Lane B fixture",
            body="Only disposable fixture output is eligible for observation.",
        )
        self.task = lane.LaneBTask(
            project_root=self.project,
            worktree=self.worktree,
            repository="acme/widgets",
            issue=340,
            branch_ref=self.branch_ref,
            base_revision=self.base,
            expected_contract_id=self.publication.contract_id,
        )

        self.runtime_root = self.fixture.temp_root / "private-runtime"
        self.runtime_root.mkdir(mode=0o700)
        self.runtime_root.chmod(0o700)
        self.profile = self.fixture.temp_root / "personal-profile"
        self.profile.mkdir(mode=0o700)
        self.profile.chmod(0o700)
        self.managed = self.fixture.temp_root / "managed-settings"
        self.managed.mkdir(mode=0o700)
        self.managed.chmod(0o700)
        self.home = self.fixture.temp_root / "home"
        self.home.mkdir(mode=0o700)
        self.home.chmod(0o700)
        self.config_path = self.fixture.temp_root / "claude-preflight.json"
        self.mode_path = self.fixture.temp_root / "fake-cli-mode.json"
        self.log_path = self.fixture.temp_root / "fake-cli-log.jsonl"
        self.fake_executable = self.fixture.temp_root / "fake-claude"
        self._write_mode({})
        self._write_fake_cli()
        self._write_config()
        self.environ = {
            "HOME": str(self.home),
            "USER": "fixture-user",
            "LOGNAME": "fixture-user",
            "PATH": os.defpath,
            "LANG": "C.UTF-8",
        }

    def _assert_unqualified_result(self, result: dict[str, object]) -> None:
        self.assertEqual("UNQUALIFIED", result["qualification"])
        self.assertFalse(result["dispatch_allowed"])
        self.assertFalse(result["task_completed"])
        self.assertEqual("UNQUALIFIED", result["ask_capability"])
        self.assertEqual("UNVERIFIED", result["effective_policy"])
        self.assertNotIn("pending_ask", result)

    def _write_mode(self, mode: dict[str, object]) -> None:
        self.mode_path.write_text(json.dumps(mode), encoding="utf-8")

    def _write_config(self, *, company: bool = False, plan: str = "pro") -> None:
        profiles: dict[str, dict[str, str]] = {
            "personal": {
                "config_dir": str(self.profile),
                "subscription_type": plan,
            }
        }
        if company:
            company_profile = self.fixture.temp_root / "company-profile"
            company_profile.mkdir(exist_ok=True, mode=0o700)
            company_profile.chmod(0o700)
            profiles["company"] = {
                "config_dir": str(company_profile),
                "subscription_type": "team",
            }
        document = {
            "schema_version": 1,
            "claude_bin": str(self.fake_executable),
            "profiles": profiles,
        }
        self.config_path.write_text(json.dumps(document), encoding="utf-8")
        self.config_path.chmod(0o600)

    def _write_fake_cli(self) -> None:
        source = r'''#!PYTHON
import json
import os
from pathlib import Path
import sys
import time

LOG = Path(LOG_PATH)
MODE_PATH = Path(MODE_PATH_VALUE)
EXPECTED = "KUMIKI_LANE_B_DIAGNOSTIC_OK"

def record(args):
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "argv": args,
            "cwd": os.getcwd(),
            "config_dir": os.environ.get("CLAUDE_CONFIG_DIR"),
            "stdin": sys.stdin.read(),
        }, sort_keys=True) + "\n")

args = sys.argv[1:]
record(args)
mode = json.loads(MODE_PATH.read_text(encoding="utf-8"))
if args == ["--version"]:
    print("Claude Code 2.1.293")
    raise SystemExit(0)
if args == ["--help"]:
    print(" ".join([
        "--print", "--output-format", "--verbose", "--session-id",
        "--permission-mode", "--setting-sources", "--tools",
        "--disallowedTools", "--strict-mcp-config", "--mcp-config",
        "--resume",
    ]))
    raise SystemExit(0)
if args == ["auth", "status"]:
    logged_in = mode.get("logged_in", True)
    document = {
        "loggedIn": logged_in,
        "authMethod": "claude.ai",
        "apiProvider": "firstParty",
        "subscriptionType": mode.get("auth_plan", "pro"),
        "configDirectory": os.environ["CLAUDE_CONFIG_DIR"],
    }
    print(json.dumps(document))
    raise SystemExit(0 if logged_in else 1)

if "-p" not in args:
    raise SystemExit(7)
if mode.get("kind") == "timeout":
    time.sleep(2)
session_id = args[args.index("--session-id") + 1]
kind = mode.get("kind", "success")
init = {
    "type": "system", "subtype": "init", "session_id": session_id,
    "cwd": os.getcwd(), "version": "2.1.293", "permissionMode": "default",
    "tools": [], "mcp_servers": [], "plugins": [],
}
assistant = {
    "type": "assistant", "session_id": session_id,
    "parent_tool_use_id": None,
    "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": EXPECTED}],
        "stop_reason": "end_turn",
    },
}
terminal = {
    "type": "result", "subtype": "success", "is_error": False,
    "session_id": session_id, "result": EXPECTED,
    "permission_denials": [], "num_turns": 1,
}
if kind == "session_spoof":
    init["session_id"] = "not-the-requested-session"
if kind == "cwd_spoof":
    init["cwd"] = "/not/the/task/worktree"
if kind == "init_tools":
    init["tools"] = ["Bash"]
if kind == "wrong_response":
    assistant["message"]["content"] = [{"type": "text", "text": "not-the-sentinel"}]
if kind == "wrong_assistant":
    assistant["type"] = "user"
if kind == "tool_use":
    assistant["message"]["content"] = [{"type": "tool_use", "name": "Bash"}]
if kind == "tool_deferred":
    assistant["message"]["stop_reason"] = "tool_deferred"
if kind == "denials":
    terminal["permission_denials"] = [{"tool_name": "Bash"}]
if kind == "wrong_result":
    terminal["result"] = "not-the-sentinel"
if kind == "result_spoof":
    terminal["session_id"] = "foreign-session"
if kind == "rate_limited":
    terminal.update({"subtype": "error", "is_error": True,
                     "error": {"type": "rate_limit_error"}})
if kind == "rate_limited_wrong_session":
    terminal["session_id"] = "foreign-session"
    terminal["errors"] = [{"type": "rate_limit_error"}]
if kind == "errors":
    terminal["errors"] = [{"type": "api_error"}]
if kind == "illtyped_errors":
    terminal["errors"] = "not-an-error-list"
if kind == "contradictory_success":
    terminal["errors"] = [{"type": "api_error"}]
if kind == "terminal_reason":
    terminal["terminal_reason"] = "unknown"
if kind == "terminal_stop_reason":
    terminal["stop_reason"] = "max_tokens"
if kind == "invalid_stop_reason":
    assistant["message"]["stop_reason"] = "max_tokens"
if kind == "deferred_tool_use":
    terminal["deferred_tool_use"] = False
if kind == "api_error":
    terminal["api_error_status"] = 429
if kind == "oversized":
    sys.stdout.write("x" * 70000)
    raise SystemExit(0)
if kind == "malformed":
    sys.stdout.write('{"type":"system","type":"assistant"}\n')
    raise SystemExit(0)
if kind == "secret_stderr":
    sys.stderr.write("fixture-secret-token secret@example.invalid\n")
if kind == "result_only":
    events = [terminal]
elif kind == "no_output":
    events = []
elif kind == "init_only":
    events = [init]
elif kind == "assistant_only":
    events = [init, assistant]
elif kind == "duplicate_init":
    events = [init, init, assistant, terminal]
elif kind == "lost_terminal":
    events = [init, assistant]
elif kind == "trailing_event":
    events = [init, assistant, terminal, {"type": "user", "session_id": session_id}]
else:
    events = [init, assistant, terminal]
sys.stdout.write("".join(json.dumps(event, separators=(",", ":")) + "\n"
                      for event in events))
raise SystemExit(int(mode.get("returncode", 0)))
'''
        source = source.replace("PYTHON", sys.executable, 1)
        source = source.replace("LOG_PATH", repr(str(self.log_path)), 1)
        source = source.replace("MODE_PATH_VALUE", repr(str(self.mode_path)), 1)
        self.fake_executable.write_text(source, encoding="utf-8")
        self.fake_executable.chmod(0o700)

    def _session(self) -> str:
        return str(uuid.uuid4())

    def _transport(self, invocation: lane.FixtureInvocation):
        return preflight._run_bounded(
            invocation.executable,
            invocation.argv,
            cwd=invocation.cwd,
            env=dict(invocation.environment),
            timeout=invocation.timeout,
        )

    def _run(
        self,
        *,
        session: str | None = None,
        authorize=None,
        transport=None,
        task: lane.LaneBTask | None = None,
        runtime_root: Path | None = None,
        timeout: float = 5.0,
        lock_timeout: float = 0.5,
    ):
        if authorize is None:
            authorize = lambda _subject: True
        if transport is None:
            transport = self._transport
        result = lane._exercise_fixture(
            self.task if task is None else task,
            config_path=self.config_path,
            runtime_root=self.runtime_root if runtime_root is None else runtime_root,
            session_id=self._session() if session is None else session,
            authorize=authorize,
            fixture_transport=transport,
            environ=self.environ,
            managed_settings_root=self.managed,
            timeout=timeout,
            lock_timeout=lock_timeout,
        )
        self._assert_unqualified_result(result)
        return result

    def _observe(self, task: lane.LaneBTask, *, runtime_root: Path, session_id: str):
        result = lane.observe(task, runtime_root=runtime_root, session_id=session_id)
        self._assert_unqualified_result(result)
        return result

    def _logs(self) -> list[dict[str, object]]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]

    def _namespace(self, task: lane.LaneBTask | None = None) -> Path:
        selected = self.task if task is None else task
        identity = lane._task_identity(selected)
        self.assertIsNotNone(identity)
        assert identity is not None
        return self.runtime_root / lane._namespace_name(selected, identity)

    def _receipt_bytes(self) -> bytes:
        namespace = self._namespace()
        return b"".join(
            path.read_bytes()
            for path in (namespace / "pending.json", namespace / "result.json")
            if path.exists()
        )

    def test_public_dispatch_is_inert_and_never_qualifies(self) -> None:
        called: list[str] = []
        with mock.patch.object(preflight, "run_preflight", side_effect=AssertionError("probe")):
            result = lane.dispatch(
                object(), authorize=lambda _subject: called.append("grant"),
                fixture_transport=lambda _invocation: called.append("transport"),
            )
        self._assert_unqualified_result(result)
        self.assertEqual([], called)
        self.assertEqual("UNQUALIFIED", result["qualification"])
        self.assertFalse(result["dispatch_allowed"])
        self.assertFalse(result["task_completed"])
        self.assertNotIn("pending_ask", result)
        self.assertEqual("UNQUALIFIED", result["ask_capability"])
        self.assertEqual("UNVERIFIED", result["effective_policy"])
        self.assertFalse(self.runtime_root.joinpath("lane-b-unused").exists())
        self.assertEqual([], self._logs())

    def test_happy_fake_cli_observes_exact_fixed_personal_invocation(self) -> None:
        session = self._session()
        tracked_before = (self.project / "tracked.txt").read_bytes()
        grants: list[lane.LaneBGrantSubject] = []

        def authorize(subject):
            grants.append(subject)
            self.assertEqual(lane.OPERATION, subject.operation)
            self.assertEqual("acme/widgets", subject.repository)
            self.assertEqual(340, subject.issue)
            self.assertEqual(self.publication.contract_id, subject.contract_id)
            self.assertIn("--setting-sources", subject.argv)
            self.assertEqual(lane.FIXED_PROMPT, subject.argv[-1])
            with self.assertRaises(FrozenInstanceError):
                subject.issue = 999
            return True

        result = self._run(session=session, authorize=authorize)
        self.assertEqual("OBSERVED", result["status"])
        self.assertEqual("TURN_RESULT_OBSERVED", result["stage"])
        self.assertEqual("SYNTHETIC_TURN_RESULT", result["reason"])
        self.assertEqual(session, result["session_id"])
        self.assertEqual("UNQUALIFIED", result["qualification"])
        self.assertFalse(result["dispatch_allowed"])
        self.assertFalse(result["task_completed"])
        self.assertEqual(3, len(grants))
        self.assertTrue(all(subject == grants[0] for subject in grants))
        self.assertEqual(
            [
                "-p", "--output-format", "stream-json", "--verbose",
                "--session-id", session, "--permission-mode", "default",
                "--setting-sources", "user", "--tools", "",
                "--disallowedTools", "mcp__*", "--strict-mcp-config",
                "--mcp-config", '{"mcpServers":{}}', lane.FIXED_PROMPT,
            ],
            list(grants[0].argv),
        )
        self.assertNotIn("--bare", grants[0].argv)
        self.assertNotIn("--allowedTools", grants[0].argv)
        model_logs = [entry for entry in self._logs() if "-p" in entry["argv"]]
        self.assertEqual(1, len(model_logs))
        self.assertEqual(str(self.worktree), model_logs[0]["cwd"])
        self.assertEqual(str(self.profile), model_logs[0]["config_dir"])
        self.assertEqual("", model_logs[0]["stdin"])
        self.assertEqual(tracked_before, (self.project / "tracked.txt").read_bytes())

    def test_grant_callback_cannot_mutate_the_executed_argv(self) -> None:
        session = self._session()
        invocations: list[lane.FixtureInvocation] = []

        def mutate(subject):
            object.__setattr__(subject, "argv", ("--dangerous-callback-mutation",))
            return True

        def capture(invocation):
            invocations.append(invocation)
            return self._transport(invocation)

        result = self._run(session=session, authorize=mutate, transport=capture)
        self.assertEqual("AUTHORIZATION_DENIED", result["reason"])
        self.assertEqual([], invocations)
        self.assertEqual([], self._logs())

    def test_receipt_subject_is_json_native_and_round_trips_exactly(self) -> None:
        identity = lane._task_identity(self.task)
        self.assertIsNotNone(identity)
        assert identity is not None
        context = lane._config_context(self.config_path, identity, self.managed)
        self.assertIsNotNone(context)
        assert context is not None
        subject = lane._receipt_subject(self.task, identity, context, self._session(), lane.EXPECTED_VERSION)
        decoded = json.loads(lane._canonical_json(subject))
        self.assertEqual(subject, decoded)
        self.assertIsInstance(decoded["binary_signature"], list)

    def test_company_profile_and_non_personal_plan_fail_before_auth(self) -> None:
        self._write_config(company=True)
        denied = self._run()
        self.assertEqual("PERSONAL_PROFILE_REQUIRED", denied["reason"])
        self.assertEqual([], self._logs())

        self._write_config(plan="team")
        denied_plan = self._run(session=self._session())
        self.assertEqual("PERSONAL_PROFILE_REQUIRED", denied_plan["reason"])
        self.assertEqual([], self._logs())

    def test_project_hooks_settings_are_refused_before_auth(self) -> None:
        claude_dir = self.project / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({"hooks": {}}), encoding="utf-8")
        result = self._run()
        self.assertEqual("PERSONAL_PROFILE_REQUIRED", result["reason"])
        self.assertEqual([], self._logs())

    def test_authentication_mismatch_does_not_launch_model(self) -> None:
        self._write_mode({"logged_in": False})
        session = self._session()
        result = self._run(session=session)
        self.assertEqual("AUTH_NOT_MATCHED", result["reason"])
        self.assertFalse(any("-p" in entry["argv"] for entry in self._logs()))
        self.assertTrue(any(entry["argv"] == ["auth", "status"] for entry in self._logs()))
        self.assertFalse((self._namespace() / "pending.json").exists())
        log_count = len(self._logs())
        retry = self._run(session=session)
        self.assertEqual("RECONCILIATION_REQUIRED", retry["reason"])
        self.assertEqual(log_count, len(self._logs()))

    def test_stale_missing_and_wrong_identity_fail_before_authorization(self) -> None:
        called: list[str] = []
        stale = replace(self.task, expected_contract_id="f" * 64)
        absent = replace(self.task, issue=999)
        wrong_repository = replace(self.task, repository="acme/other")
        unregistered_worktree = self.fixture.temp_root / "unregistered-worktree"
        unregistered_worktree.mkdir()
        unregistered = replace(self.task, worktree=unregistered_worktree)
        for task in (stale, absent, wrong_repository, unregistered):
            with self.subTest(task=task):
                result = self._run(task=task, authorize=lambda _subject: called.append("grant"))
                self.assertEqual("TASK_IDENTITY_MISMATCH", result["reason"])
        self.assertEqual([], called)
        self.assertEqual([], self._logs())

    def test_bad_locator_and_noncanonical_opencode_session_are_rejected(self) -> None:
        bad_task = replace(self.task, branch_ref="refs/heads/issue-340/../main")
        result = self._run(task=bad_task)
        self.assertEqual("INVALID_LOCATOR", result["reason"])
        result = self._run(session="ses_opencode_session_id")
        self.assertEqual("INVALID_LOCATOR", result["reason"])
        self.assertEqual([], self._logs())

    def test_denied_nonboolean_and_raising_grants_never_probe_auth(self) -> None:
        for index, callback in enumerate((
            lambda _subject: False,
            lambda _subject: 1,
            lambda _subject: None,
            lambda _subject: (_ for _ in ()).throw(RuntimeError("private")),
        )):
            with self.subTest(callback=callback):
                attempt_root = self.fixture.temp_root / f"denied-runtime-{index}"
                attempt_root.mkdir(mode=0o700)
                attempt_root.chmod(0o700)
                result = self._run(session=self._session(), authorize=callback,
                                   runtime_root=attempt_root)
                self.assertEqual("AUTHORIZATION_DENIED", result["reason"])
                self.assertEqual([], list(attempt_root.iterdir()))
        self.assertEqual([], self._logs())

    def test_task_drift_during_authorization_blocks_before_auth_or_turn(self) -> None:
        namespace = self._namespace()
        def mutate_metadata(_subject):
            task_record.TaskRecords(
                self.fixture.store, authorize=lambda _request: True
            ).reauthorize(
                task="340",
                branch_ref=self.branch_ref,
                base_revision=self.base,
                expected_record_id=self.publication.record_id,
                title="Fixture specification changed",
                body="Authorization callback changed canonical Task metadata.",
            )
            return True

        result = self._run(authorize=mutate_metadata)
        self.assertEqual("CONTEXT_DRIFT", result["reason"])
        self.assertEqual([], self._logs())
        self.assertFalse(namespace.exists())
        self.assertEqual([], list(self.runtime_root.iterdir()))

    def test_config_drift_during_authorization_blocks_before_auth(self) -> None:
        def mutate_config(_subject):
            self.config_path.write_text(self.config_path.read_text() + "\n", encoding="utf-8")
            return True

        result = self._run(authorize=mutate_config)
        self.assertEqual("CONTEXT_DRIFT", result["reason"])
        self.assertEqual([], self._logs())

    def test_grant_revocation_after_probes_and_before_turn_prevents_model_call(self) -> None:
        for revoke_at, pending_expected in ((2, False), (3, True)):
            with self.subTest(revoke_at=revoke_at):
                root = self.fixture.temp_root / f"revoked-runtime-{revoke_at}"
                root.mkdir(mode=0o700)
                root.chmod(0o700)
                calls = []

                def authorize(_subject):
                    calls.append(1)
                    return len(calls) < revoke_at

                result = self._run(authorize=authorize, runtime_root=root)
                self.assertEqual("AUTHORIZATION_DENIED", result["reason"])
                self.assertEqual(revoke_at, len(calls))
                self.assertFalse(any("-p" in entry["argv"] for entry in self._logs()))
                namespace = next(root.iterdir())
                self.assertEqual(pending_expected, (namespace / "pending.json").exists())
                self.assertFalse((namespace / "result.json").exists())
                self.log_path.unlink(missing_ok=True)

    def test_storage_root_namespace_and_lock_replacement_after_grant_fail_closed(self) -> None:
        for target in ("root", "namespace", "lock"):
            with self.subTest(target=target):
                root = self.fixture.temp_root / f"swap-runtime-{target}"
                root.mkdir(mode=0o700)
                root.chmod(0o700)
                grants = []

                def replace_storage(_subject):
                    grants.append(1)
                    if target != "root" and len(grants) == 1:
                        return True
                    identity = lane._task_identity(self.task)
                    assert identity is not None
                    namespace = root / lane._namespace_name(self.task, identity)
                    if target == "root":
                        moved = root.with_name(root.name + "-moved")
                        root.rename(moved)
                        root.mkdir(mode=0o700)
                        root.chmod(0o700)
                    elif target == "namespace":
                        moved = namespace.with_name(namespace.name + "-moved")
                        namespace.rename(moved)
                        namespace.mkdir(mode=0o700)
                        namespace.chmod(0o700)
                    else:
                        lock_path = namespace / "lock"
                        moved = lock_path.with_name("lock-moved")
                        lock_path.rename(moved)
                        lock_path.touch(mode=0o600)
                        lock_path.chmod(0o600)
                    return True

                result = self._run(authorize=replace_storage, runtime_root=root)
                self.assertEqual("RECONCILIATION_REQUIRED", result["reason"])
                self.assertFalse(any("-p" in entry["argv"] for entry in self._logs()))
                if target == "root":
                    self.assertEqual([], self._logs())
                self.assertFalse(any(
                    entry.name in {"pending.json", "result.json"}
                    for directory in root.iterdir() if directory.is_dir()
                    for entry in directory.iterdir()
                ))
                self.log_path.unlink(missing_ok=True)

    def test_existing_namespace_without_lock_is_not_repaired(self) -> None:
        root = self.fixture.temp_root / "missing-lock-runtime"
        root.mkdir(mode=0o700)
        root.chmod(0o700)
        identity = lane._task_identity(self.task)
        self.assertIsNotNone(identity)
        assert identity is not None
        namespace = root / lane._namespace_name(self.task, identity)
        namespace.mkdir(mode=0o700)
        result = self._run(runtime_root=root)
        self.assertEqual("RECONCILIATION_REQUIRED", result["reason"])
        self.assertFalse((namespace / "lock").exists())
        self.assertEqual([], self._logs())

        damaged_root = self.fixture.temp_root / "damaged-lock-runtime"
        damaged_root.mkdir(mode=0o700)
        damaged_root.chmod(0o700)
        damaged_namespace = damaged_root / lane._namespace_name(self.task, identity)
        damaged_namespace.mkdir(mode=0o700)
        external = self.fixture.temp_root / "external-lock-target"
        external.write_text("not a lock", encoding="utf-8")
        (damaged_namespace / "lock").symlink_to(external)
        damaged = self._run(runtime_root=damaged_root)
        self.assertEqual("RECONCILIATION_REQUIRED", damaged["reason"])
        self.assertTrue((damaged_namespace / "lock").is_symlink())
        self.assertEqual("not a lock", external.read_text(encoding="utf-8"))
        self.assertEqual([], self._logs())

    def test_missing_both_receipts_with_existing_lock_requires_reconciliation(self) -> None:
        root = self.fixture.temp_root / "deleted-receipts-runtime"
        root.mkdir(mode=0o700)
        root.chmod(0o700)
        session = self._session()
        self.assertEqual("OBSERVED", self._run(session=session, runtime_root=root)["status"])
        namespace = next(root.iterdir())
        (namespace / "pending.json").unlink()
        (namespace / "result.json").unlink()
        log_count = len(self._logs())
        retry = self._run(session=session, runtime_root=root)
        self.assertEqual("RECONCILIATION_REQUIRED", retry["reason"])
        self.assertEqual(log_count, len(self._logs()))

    def test_runtime_root_must_be_private_canonical_and_external(self) -> None:
        shared = self.fixture.temp_root / "shared-cache"
        shared.mkdir(mode=0o755)
        shared.chmod(0o755)
        result = lane._exercise_fixture(
            self.task,
            config_path=self.config_path,
            runtime_root=shared,
            session_id=self._session(),
            authorize=lambda _subject: True,
            fixture_transport=self._transport,
            environ=self.environ,
            managed_settings_root=self.managed,
        )
        self._assert_unqualified_result(result)
        self.assertEqual("RUNTIME_ROOT_INVALID", result["reason"])
        self.assertEqual([], self._logs())

        inside_project = lane._exercise_fixture(
            self.task,
            config_path=self.config_path,
            runtime_root=self.project,
            session_id=self._session(),
            authorize=lambda _subject: True,
            fixture_transport=self._transport,
            environ=self.environ,
            managed_settings_root=self.managed,
        )
        self._assert_unqualified_result(inside_project)
        self.assertEqual("RUNTIME_ROOT_INVALID", inside_project["reason"])

    def test_lock_timeout_is_bounded_and_does_not_probe_or_turn(self) -> None:
        namespace = self._namespace()
        namespace.mkdir(mode=0o700)
        lock_path = namespace / "lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
        holder = os.open(lock_path, os.O_RDWR)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            start = time.monotonic()
            result = self._run(lock_timeout=0.03)
            self.assertLess(time.monotonic() - start, 1.0)
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            os.close(holder)
        self.assertEqual("LOCK_TIMEOUT", result["reason"])
        self.assertEqual([], self._logs())

    def test_concurrent_duplicate_attempts_share_one_fake_turn(self) -> None:
        session = self._session()
        entered = threading.Event()
        release = threading.Event()
        second_contended = threading.Event()
        first_lock_inode: list[tuple[int, int]] = []
        results: list[dict[str, object]] = []
        call_count: list[int] = []

        real_flock = fcntl.flock

        def instrumented_flock(fd, operation):
            try:
                result = real_flock(fd, operation)
            except BlockingIOError:
                info = os.fstat(fd)
                if (threading.current_thread().name == "lane-b-second"
                        and operation & fcntl.LOCK_EX and operation & fcntl.LOCK_NB
                        and first_lock_inode == [(info.st_dev, info.st_ino)]):
                    second_contended.set()
                raise
            else:
                if (threading.current_thread().name == "lane-b-first"
                        and operation & fcntl.LOCK_EX and operation & fcntl.LOCK_NB):
                    info = os.fstat(fd)
                    first_lock_inode.append((info.st_dev, info.st_ino))
                return result

        def delayed_transport(invocation):
            call_count.append(1)
            entered.set()
            self.assertTrue(release.wait(25))
            return self._transport(invocation)

        first = threading.Thread(
            target=lambda: results.append(
                self._run(session=session, transport=delayed_transport, lock_timeout=1.0)
            ),
            name="lane-b-first",
            daemon=True,
        )
        second = threading.Thread(
            target=lambda: results.append(
                self._run(session=session, lock_timeout=1.0)
            ),
            name="lane-b-second",
            daemon=True,
        )
        with mock.patch.object(lane.fcntl, "flock", side_effect=instrumented_flock):
            first.start()
            try:
                self.assertTrue(entered.wait(20), "first fixture transport never started")
                second.start()
                self.assertTrue(second_contended.wait(20), "second thread never contended on held lock")
            finally:
                release.set()
                if first.ident is not None:
                    first.join(30)
                if second.ident is not None:
                    second.join(30)
        self.assertFalse(first.is_alive(), "first fixture worker exceeded bounded cleanup")
        self.assertFalse(second.is_alive(), "second fixture worker exceeded bounded cleanup")
        self.assertEqual(1, len(call_count))
        self.assertEqual(2, len(results))
        self.assertEqual(["OBSERVED", "OBSERVED"], sorted(result["status"] for result in results))
        self.assertEqual(1, sum("-p" in entry["argv"] for entry in self._logs()))

    def test_pending_after_transport_disconnect_prevents_any_retry(self) -> None:
        session = self._session()
        transport_calls: list[int] = []

        def disconnect(_invocation):
            transport_calls.append(1)
            raise RuntimeError("model output unavailable")

        first = self._run(session=session, transport=disconnect)
        self.assertEqual("UNKNOWN", first["status"])
        self.assertEqual("TRANSPORT_FAILURE", first["reason"])
        pending = self._namespace() / "pending.json"
        self.assertTrue(pending.exists())
        self.assertTrue((self._namespace() / "result.json").exists())
        calls_after_first = len(self._logs())

        retry = self._run(session=self._session())
        self.assertEqual("RECONCILIATION_REQUIRED", retry["reason"])
        self.assertEqual(1, len(transport_calls))
        self.assertEqual(calls_after_first, len(self._logs()))

    def test_existing_result_is_stable_but_changed_session_requires_reconciliation(self) -> None:
        session = self._session()
        first = self._run(session=session)
        self.assertEqual("OBSERVED", first["status"])
        count_after_turn = len(self._logs())
        repeated = self._run(session=session, authorize=lambda _subject: self.fail("grant repeated"))
        self.assertEqual(first, repeated)
        self.assertEqual(count_after_turn, len(self._logs()))
        different_attempt = self._run(session=self._session())
        self.assertEqual("RECONCILIATION_REQUIRED", different_attempt["reason"])
        self.assertEqual(count_after_turn, len(self._logs()))

    def test_readonly_observe_is_stable_and_receipt_tampering_reconciles(self) -> None:
        session = self._session()
        expected = self._run(session=session)
        observed = self._observe(self.task, runtime_root=self.runtime_root, session_id=session)
        self.assertEqual(expected, observed)
        count = len(self._logs())
        result_path = self._namespace() / "result.json"
        result_path.write_text("{}", encoding="utf-8")
        result_path.chmod(0o600)
        tampered = self._observe(self.task, runtime_root=self.runtime_root, session_id=session)
        self.assertEqual("RECONCILIATION_REQUIRED", tampered["reason"])
        self.assertEqual(count, len(self._logs()))

    def test_hardlinked_receipt_is_rejected(self) -> None:
        session = self._session()
        self._run(session=session)
        pending = self._namespace() / "pending.json"
        os.link(pending, self.fixture.temp_root / "receipt-hardlink")
        result = self._observe(self.task, runtime_root=self.runtime_root, session_id=session)
        self.assertEqual("RECONCILIATION_REQUIRED", result["reason"])

    def test_observe_rejects_wrong_version_or_profile_even_with_recomputed_pair_hash(self) -> None:
        for field, invalid in (("binary_version", "9.9.9"), ("credential_profile", "company"),
                               ("personal_plan", "team")):
            with self.subTest(field=field):
                root = self.fixture.temp_root / f"tampered-{field}"
                root.mkdir(mode=0o700)
                root.chmod(0o700)
                session = self._session()
                self._run(session=session, runtime_root=root)
                namespace = next(root.iterdir())
                pending_path = namespace / "pending.json"
                result_path = namespace / "result.json"
                pending = json.loads(pending_path.read_text(encoding="utf-8"))
                result = json.loads(result_path.read_text(encoding="utf-8"))
                pending["subject"][field] = invalid
                result["pending_sha256"] = lane._sha256(lane._canonical_json(pending))
                pending_path.write_bytes(lane._canonical_json(pending))
                result_path.write_bytes(lane._canonical_json(result))
                pending_path.chmod(0o600)
                result_path.chmod(0o600)
                observed = self._observe(self.task, runtime_root=root, session_id=session)
                self.assertEqual("RECONCILIATION_REQUIRED", observed["reason"])

    def test_observation_triples_cannot_claim_completion_or_block_incoherently(self) -> None:
        self.assertFalse(lane._valid_observation({
            "status": "OBSERVED", "reason": "SYNTHETIC_TURN_RESULT", "stage": "UNKNOWN"
        }))
        self.assertFalse(lane._valid_observation({
            "status": "UNKNOWN", "reason": "PROCESS_TIMEOUT", "stage": "TURN_RESULT_OBSERVED"
        }))
        self.assertFalse(lane._valid_observation({
            "status": "UNKNOWN", "reason": "TOOL_POLICY_UNSUPPORTED", "stage": "SESSION_OBSERVED"
        }))

    def test_result_without_pending_and_symlink_receipt_fail_closed(self) -> None:
        namespace = self._namespace()
        namespace.mkdir(mode=0o700)
        lock_fd = os.open(namespace / "lock", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(lock_fd)
        (namespace / "result.json").write_text("{}", encoding="utf-8")
        (namespace / "result.json").chmod(0o600)
        result = self._run()
        self.assertEqual("RECONCILIATION_REQUIRED", result["reason"])
        self.assertEqual([], self._logs())

        # A separate Task cache with a symlink pending marker is not followed.
        other_runtime = self.fixture.temp_root / "runtime-two"
        other_runtime.mkdir(mode=0o700)
        other_runtime.chmod(0o700)
        identity = lane._task_identity(self.task)
        self.assertIsNotNone(identity)
        assert identity is not None
        other_ns = other_runtime / lane._namespace_name(self.task, identity)
        other_ns.mkdir(mode=0o700)
        lock_fd = os.open(other_ns / "lock", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(lock_fd)
        secret_target = self.fixture.temp_root / "external-receipt-target"
        secret_target.write_text("not a receipt", encoding="utf-8")
        (other_ns / "pending.json").symlink_to(secret_target)
        result = lane._exercise_fixture(
            self.task,
            config_path=self.config_path,
            runtime_root=other_runtime,
            session_id=self._session(),
            authorize=lambda _subject: True,
            fixture_transport=self._transport,
            environ=self.environ,
            managed_settings_root=self.managed,
        )
        self._assert_unqualified_result(result)
        self.assertEqual("RECONCILIATION_REQUIRED", result["reason"])
        self.assertEqual("not a receipt", secret_target.read_text(encoding="utf-8"))

    def test_stream_validator_rejects_spoofing_tools_errors_and_incomplete_output(self) -> None:
        cases = {
            "session_spoof": ("UNKNOWN", "SESSION_MISMATCH"),
            "cwd_spoof": ("UNKNOWN", "WORKTREE_MISMATCH"),
            "result_only": ("UNKNOWN", "MALFORMED_STREAM"),
            "no_output": ("UNKNOWN", "MALFORMED_STREAM"),
            "init_only": ("UNKNOWN", "MALFORMED_STREAM"),
            "assistant_only": ("UNKNOWN", "MALFORMED_STREAM"),
            "wrong_assistant": ("UNKNOWN", "MALFORMED_STREAM"),
            "duplicate_init": ("UNKNOWN", "MALFORMED_STREAM"),
            "trailing_event": ("UNKNOWN", "MALFORMED_STREAM"),
            "tool_use": ("BLOCKED", "TOOL_POLICY_UNSUPPORTED"),
            "tool_deferred": ("BLOCKED", "TOOL_POLICY_UNSUPPORTED"),
            "deferred_tool_use": ("BLOCKED", "TOOL_POLICY_UNSUPPORTED"),
            "denials": ("BLOCKED", "TOOL_POLICY_UNSUPPORTED"),
            "wrong_response": ("UNKNOWN", "RESPONSE_MISMATCH"),
            "wrong_result": ("UNKNOWN", "RESULT_MISMATCH"),
            "result_spoof": ("UNKNOWN", "SESSION_MISMATCH"),
            "rate_limited_wrong_session": ("UNKNOWN", "SESSION_MISMATCH"),
            "errors": ("UNKNOWN", "RESULT_MISMATCH"),
            "illtyped_errors": ("UNKNOWN", "RESULT_MISMATCH"),
            "contradictory_success": ("UNKNOWN", "RESULT_MISMATCH"),
            "terminal_reason": ("UNKNOWN", "RESULT_MISMATCH"),
            "terminal_stop_reason": ("UNKNOWN", "RESULT_MISMATCH"),
            "invalid_stop_reason": ("UNKNOWN", "SCHEMA_UNSUPPORTED"),
            "api_error": ("UNKNOWN", "RESULT_MISMATCH"),
            "malformed": ("UNKNOWN", "MALFORMED_STREAM"),
            "oversized": ("UNKNOWN", "OUTPUT_LIMIT"),
            "rate_limited": ("UNKNOWN", "RATE_LIMITED"),
        }
        for kind, (expected_status, expected_reason) in cases.items():
            with self.subTest(kind=kind):
                self._write_mode({"kind": kind})
                attempt_runtime = self.fixture.temp_root / f"runtime-{kind}"
                attempt_runtime.mkdir(mode=0o700)
                attempt_runtime.chmod(0o700)
                result = self._run(session=self._session(), runtime_root=attempt_runtime)
                self.assertEqual(expected_status, result["status"])
                self.assertEqual(expected_reason, result["reason"])
                self.assertFalse(result["task_completed"])
                self.assertEqual("UNQUALIFIED", result["qualification"])

    def test_nonzero_exit_timeout_and_secret_stderr_are_sanitized(self) -> None:
        self._write_mode({"kind": "success", "returncode": 7})
        nonzero = self._run(session=self._session())
        self.assertEqual("UNKNOWN", nonzero["status"])
        self.assertEqual("PROCESS_NONZERO", nonzero["reason"])

        # Each prior observation is task-scoped and cannot be retried. Use a
        # distinct disposable Task identity for a bounded timeout fixture.
        self._write_mode({"kind": "success"})
        task_records = task_record.TaskRecords(
            self.fixture.store, authorize=lambda _request: True
        )
        fixtures.git(
            "worktree", "add", "-b", "issue-341", str(self.project / ".worktrees" / "341"),
            self.base, cwd=self.project,
        )
        publication = task_records.create(
            task="341", branch_ref="refs/heads/issue-341", base_revision=self.base,
            title="Timeout fixture Task", body="Disposable local fixture only.",
        )
        timeout_task = lane.LaneBTask(
            self.project, self.project / ".worktrees" / "341", "acme/widgets", 341,
            "refs/heads/issue-341", self.base, publication.contract_id,
        )
        self._write_mode({"kind": "timeout"})
        timed_out = self._run(task=timeout_task, timeout=0.75)
        self.assertEqual("UNKNOWN", timed_out["status"])
        self.assertEqual("PROCESS_TIMEOUT", timed_out["reason"])

        # Isolate the successful secret-stderr case in a third disposable Task.
        fixtures.git(
            "worktree", "add", "-b", "issue-342", str(self.project / ".worktrees" / "342"),
            self.base, cwd=self.project,
        )
        publication = task_records.create(
            task="342", branch_ref="refs/heads/issue-342", base_revision=self.base,
            title="Sanitization fixture Task", body="Disposable local fixture only.",
        )
        secret_task = lane.LaneBTask(
            self.project, self.project / ".worktrees" / "342", "acme/widgets", 342,
            "refs/heads/issue-342", self.base, publication.contract_id,
        )
        self._write_mode({"kind": "secret_stderr"})
        secret = self._run(task=secret_task)
        self.assertEqual("OBSERVED", secret["status"])
        all_receipts = self._receipt_bytes_for_task(timeout_task) + self._receipt_bytes_for_task(secret_task)
        self.assertNotIn(b"fixture-secret-token", all_receipts)
        self.assertNotIn(b"secret@example.invalid", all_receipts)
        self.assertNotIn("fixture-secret-token", json.dumps(secret))
        self.assertNotIn("secret@example.invalid", json.dumps(secret))

    def _receipt_bytes_for_task(self, task: lane.LaneBTask) -> bytes:
        namespace = self._namespace(task)
        return b"".join(
            path.read_bytes()
            for path in (namespace / "pending.json", namespace / "result.json")
            if path.exists()
        )

    def test_auth_plan_override_and_environment_override_refuse(self) -> None:
        self._write_mode({"auth_plan": "team"})
        wrong_auth_plan = self._run()
        self.assertEqual("AUTH_NOT_MATCHED", wrong_auth_plan["reason"])
        self.assertFalse(any("-p" in entry["argv"] for entry in self._logs()))

        self.log_path.unlink(missing_ok=True)
        environment = dict(self.environ, ANTHROPIC_API_KEY="fixture-only-not-a-secret")
        result = lane._exercise_fixture(
            self.task,
            config_path=self.config_path,
            runtime_root=self.runtime_root,
            session_id=self._session(),
            authorize=lambda _subject: self.fail("grant after override"),
            fixture_transport=self._transport,
            environ=environment,
            managed_settings_root=self.managed,
        )
        self._assert_unqualified_result(result)
        self.assertEqual("PERSONAL_PROFILE_REQUIRED", result["reason"])
        self.assertEqual([], self._logs())

    def test_child_help_must_expose_full_fixed_flag_surface(self) -> None:
        original = self.fake_executable.read_text(encoding="utf-8")
        # Make just the additional --help capability observation incomplete.
        self.fake_executable.write_text(
            original.replace(
                '"--disallowedTools", "--strict-mcp-config", "--mcp-config",',
                '"--disallowedTools", "--strict-mcp-config",',
            ),
            encoding="utf-8",
        )
        result = self._run()
        self.assertEqual("CLI_FLAGS_UNVERIFIED", result["reason"])
        self.assertFalse(any("-p" in entry["argv"] for entry in self._logs()))
        self.assertFalse((self._namespace() / "pending.json").exists())

    def test_fixture_transport_requires_exit_code_and_handles_oversize_safely(self) -> None:
        timeout_result = lane._validate_transport({"stdout": b"", "stderr": b"",
                                                   "timed_out": True, "returncode": 0})
        self.assertIsNotNone(timeout_result)
        assert timeout_result is not None
        self.assertEqual(-1, timeout_result["returncode"])
        for case in ("missing-exit", "oversize"):
            with self.subTest(case=case):
                root = self.fixture.temp_root / f"transport-{case}"
                root.mkdir(mode=0o700)
                root.chmod(0o700)

                def transport(invocation):
                    if case == "oversize":
                        return {"stdout": b"x" * (lane.MAX_CAPTURE + 1), "stderr": b""}
                    session = invocation.argv[invocation.argv.index("--session-id") + 1]
                    events = [
                        {"type": "system", "subtype": "init", "session_id": session,
                         "cwd": str(invocation.cwd), "version": lane.EXPECTED_VERSION,
                         "permissionMode": "default", "tools": [], "mcp_servers": [], "plugins": []},
                        {"type": "assistant", "session_id": session, "parent_tool_use_id": None,
                         "message": {"role": "assistant", "content": [{"type": "text", "text": lane.EXPECTED_RESPONSE}],
                                     "stop_reason": "end_turn"}},
                        {"type": "result", "subtype": "success", "is_error": False,
                         "session_id": session, "result": lane.EXPECTED_RESPONSE,
                         "permission_denials": [], "num_turns": 1},
                    ]
                    return {"stdout": b"".join(json.dumps(event).encode() + b"\n" for event in events),
                            "stderr": b""}  # Deliberately no returncode.

                result = self._run(runtime_root=root, transport=transport)
                self.assertNotEqual("OBSERVED", result["status"])
                self.assertFalse(result["task_completed"])
                if case == "missing-exit":
                    self.assertEqual("TRANSPORT_FAILURE", result["reason"])
                else:
                    self.assertEqual("OUTPUT_LIMIT", result["reason"])

    def test_pending_without_result_is_never_auto_retried_even_with_same_session(self) -> None:
        session = self._session()
        with mock.patch.object(lane, "_write_result", return_value=False):
            result = self._run(
                session=session,
                transport=lambda _invocation: (_ for _ in ()).throw(RuntimeError("disconnect")),
            )
        self.assertEqual("RECONCILIATION_REQUIRED", result["reason"])
        self.assertTrue((self._namespace() / "pending.json").exists())
        self.assertFalse((self._namespace() / "result.json").exists())
        count = len(self._logs())
        again = self._run(session=session)
        self.assertEqual("RECONCILIATION_REQUIRED", again["reason"])
        self.assertEqual(count, len(self._logs()))

    def test_parse_stream_flags_timeout_overflow_and_invalid_json(self) -> None:
        session = self._session()
        events = [
            {
                "type": "system", "subtype": "init", "session_id": session,
                "cwd": str(self.worktree), "tools": [], "mcp_servers": [],
            }
        ]
        partial = (json.dumps(events[0]) + "\n").encode()
        timed = lane.parse_stream(
            partial, b"", requested_session_id=session, expected_cwd=self.worktree,
            returncode=0, timed_out=True,
        )
        self.assertEqual("SESSION_OBSERVED", timed["stage"])
        self.assertEqual("UNKNOWN", timed["status"])
        self.assertEqual("PROCESS_TIMEOUT", timed["reason"])
        assistant = {
            "type": "assistant", "session_id": session, "parent_tool_use_id": None,
            "message": {"role": "assistant", "content": [{"type": "text", "text": lane.EXPECTED_RESPONSE}]},
        }
        assistant_timeout = lane.parse_stream(
            partial + (json.dumps(assistant) + "\n").encode(), b"",
            requested_session_id=session, expected_cwd=self.worktree,
            returncode=-1, timed_out=True,
        )
        self.assertEqual("RESPONSE_OBSERVED", assistant_timeout["stage"])
        self.assertEqual("PROCESS_TIMEOUT", assistant_timeout["reason"])
        overflow = lane.parse_stream(
            partial, b"", requested_session_id=session, expected_cwd=self.worktree,
            returncode=0, overflow=True,
        )
        self.assertEqual("OUTPUT_LIMIT", overflow["reason"])
        self.assertEqual("UNKNOWN", overflow["status"])
        malformed = lane.parse_stream(
            b'{"a":1,"a":2}\n', b"", requested_session_id=session,
            expected_cwd=self.worktree, returncode=0,
        )
        self.assertEqual("MALFORMED_STREAM", malformed["reason"])
        self.assertEqual("UNKNOWN", malformed["status"])
        lost_terminal = lane.parse_stream(
            partial + (json.dumps(assistant) + "\n").encode() + b'{"bad":}\n', b"",
            requested_session_id=session, expected_cwd=self.worktree, returncode=0,
        )
        self.assertEqual("RESPONSE_OBSERVED", lost_terminal["stage"])
        self.assertEqual("UNKNOWN", lost_terminal["status"])
        self.assertEqual("MALFORMED_STREAM", lost_terminal["reason"])


class ClaudeLaneBParserBoundaryTests(unittest.TestCase):
    """Cheap pure-parser boundaries; no Git, settings, CLI or metadata fixtures."""

    def _valid_events(self, session: str, cwd: Path) -> list[dict[str, object]]:
        return [
            {"type": "system", "subtype": "init", "session_id": session,
             "cwd": str(cwd), "version": lane.EXPECTED_VERSION, "permissionMode": "default",
             "tools": [], "mcp_servers": [], "plugins": []},
            {"type": "assistant", "session_id": session, "parent_tool_use_id": None,
             "message": {"role": "assistant", "content": [{"type": "text", "text": lane.EXPECTED_RESPONSE}],
                         "stop_reason": "end_turn"}},
            {"type": "result", "subtype": "success", "is_error": False,
             "session_id": session, "result": lane.EXPECTED_RESPONSE,
             "permission_denials": [], "num_turns": 1},
        ]

    def _assert_unknown(self, data: bytes, session: str, cwd: Path) -> None:
        self.assertLess(len(data), lane.MAX_CAPTURE)
        result = lane.parse_stream(data, b"", requested_session_id=session,
                                   expected_cwd=cwd, returncode=0)
        self.assertEqual("UNKNOWN", result["status"])
        self.assertNotEqual("TURN_RESULT_OBSERVED", result["stage"])

    def test_one_over_line_event_and_depth_limits_and_duplicate_keys_never_complete(self) -> None:
        session = str(uuid.uuid4())
        cwd = Path("/fixture/worktree")
        events = self._valid_events(session, cwd)
        lines = [json.dumps(event, separators=(",", ":")).encode() + b"\n" for event in events]

        too_long = b'{"type":"trailing","padding":"' + b"x" * (lane.MAX_LINE + 1) + b'"}\n'
        self._assert_unknown(b"".join(lines) + too_long, session, cwd)

        too_many = b"".join(lines + [b'{"type":"extra"}\n'] * (lane.MAX_EVENTS - len(lines) + 1))
        self._assert_unknown(too_many, session, cwd)

        deep: object = "x"
        for _ in range(lane.MAX_JSON_DEPTH + 1):
            deep = [deep]
        over_depth = self._valid_events(session, cwd)
        over_depth[0]["extra"] = deep
        depth_bytes = b"".join(json.dumps(event, separators=(",", ":")).encode() + b"\n"
                               for event in over_depth)
        self._assert_unknown(depth_bytes, session, cwd)

        duplicate = b'{"type":"system","type":"system","subtype":"init"}\n'
        self._assert_unknown(duplicate, session, cwd)


if __name__ == "__main__":
    unittest.main()
