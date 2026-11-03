"""Fake-executable-only tests for the conservative Claude CLI preflight."""
from __future__ import annotations

import builtins
import json
import os
import io
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, call, patch

from tools import kagari_vnext_claude_preflight as preflight


class ClaudePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="claude-preflight-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.personal = self.root / "profile-personal"
        self.company = self.root / "profile-company"
        self.managed = self.root / "managed"
        self.fake = self.root / "fake-claude"
        self.log = self.root / "calls.jsonl"
        self.config = self.root / "preflight.json"
        self.project.mkdir()
        self.personal.mkdir()
        self.company.mkdir()
        self.managed.mkdir()
        (self.root / "home").mkdir()
        self.environment = {
            "HOME": str(self.root / "home"),
            "USER": "preflight-test",
            "LOGNAME": "preflight-test",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
        }
        self.write_config()
        self.write_fake()

    def write_config(self, *, personal_dir=None, company_dir=None, include_company=True):
        profiles = {
            "personal": {
                "config_dir": str(personal_dir or self.personal),
                "subscription_type": "pro",
            }
        }
        if include_company:
            profiles["company"] = {
                "config_dir": str(company_dir or self.company),
                "subscription_type": "team",
            }
        self.config.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "claude_bin": str(self.fake),
                    "profiles": profiles,
                }
            ),
            encoding="utf-8",
        )

    def write_fake(
        self,
        *,
        version="2.1.293",
        version_exit=0,
        help_text=" ".join(preflight.CAPABILITY_FLAGS),
        help_exit=0,
        auth_text=None,
        auth_exit=0,
        auth_stderr="",
        version_delay=0,
        version_bytes=None,
        close_streams=False,
        close_delay=0,
        fork_descendant=False,
        settings_on_version=None,
        replace_profile_with_symlink=False,
    ):
        if auth_text is None:
            auth_text = json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "apiProvider": "firstParty",
                    "subscriptionType": "pro",
                    "configDirectory": "__CLAUDE_CONFIG_DIR__",
                    "email": "private@example.invalid",
                    "organization": "private-org",
                }
            )
        source = f'''#!{sys.executable}
import json, os, sys, time
log = {str(self.log)!r}
entry = {{
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "env": dict(os.environ),
    "stdin_eof": os.read(0, 1) == b"",
}}
with open(log, "a", encoding="utf-8") as stream:
    stream.write(json.dumps(entry) + "\\n")
args = sys.argv[1:]
if args == ["--version"]:
    time.sleep({version_delay!r})
    if {settings_on_version!r} is not None:
        settings_path = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "settings.json")
        with open(settings_path, "w", encoding="utf-8") as stream:
            stream.write({settings_on_version!r})
    if {replace_profile_with_symlink!r}:
        profile_path = os.environ["CLAUDE_CONFIG_DIR"]
        os.rename(profile_path, profile_path + ".moved")
        os.symlink({str(self.company)!r}, profile_path)
    if {version_bytes!r} is not None:
        sys.stdout.buffer.write(b"x" * {version_bytes!r})
    else:
        sys.stdout.write({version!r})
    sys.stdout.flush()
    if {close_streams!r} or {fork_descendant!r}:
        if {fork_descendant!r}:
            child = os.fork()
            if child == 0:
                time.sleep(2)
                os._exit(0)
        sys.stderr.flush()
        nullfd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(nullfd, 1)
        os.dup2(nullfd, 2)
        os.close(nullfd)
        time.sleep({close_delay!r})
    sys.exit({version_exit!r})
if args == ["--help"]:
    sys.stdout.write({help_text!r})
    sys.exit({help_exit!r})
if args == ["auth", "status"]:
    sys.stdout.write({auth_text!r}.replace("__CLAUDE_CONFIG_DIR__", os.environ["CLAUDE_CONFIG_DIR"]))
    sys.stderr.write({auth_stderr!r})
    sys.exit({auth_exit!r})
sys.exit(73)
'''
        self.fake.write_text(source, encoding="utf-8")
        self.fake.chmod(0o755)

    def run_check(self, **kwargs):
        config = kwargs.pop("config", self.config)
        project_root = kwargs.pop("project_root", self.project)
        return preflight.run_preflight(
            config,
            project_root,
            environ=kwargs.pop("environ", self.environment),
            managed_settings_root=kwargs.pop("managed_settings_root", self.managed),
            **kwargs,
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def clear_calls(self):
        self.log.unlink(missing_ok=True)

    def assert_safe_output(self, result):
        self.assertEqual(
            set(result),
            {
                "schema_version",
                "executor",
                "profile",
                "status",
                "reason",
                "version",
                "capabilities",
                "executor_qualification",
                "dispatch_allowed",
                "effective_policy",
            },
        )
        encoded = json.dumps(result)
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn("private@example.invalid", encoded)
        self.assertNotIn("private-org", encoded)
        self.assertEqual(result["executor"], "claude-cli")
        self.assertEqual(result["executor_qualification"], "UNQUALIFIED")
        self.assertFalse(result["dispatch_allowed"])
        self.assertEqual(result["effective_policy"], "UNVERIFIED")

    def test_default_personal_is_version_help_only_and_auth_is_opt_in(self):
        result = self.run_check()
        self.assertEqual(result["status"], "VERSION_CHECKED")
        self.assertEqual(result["profile"], "personal")
        self.assertEqual(result["version"], "2.1.293")
        self.assertEqual(result["capabilities"], list(preflight.CAPABILITY_FLAGS))
        self.assertEqual([call["argv"] for call in self.calls()], [["--version"], ["--help"]])
        self.assert_safe_output(result)

        self.clear_calls()
        result = self.run_check(probe_auth=True)
        self.assertEqual(result["status"], "AUTH_STATUS_MATCHED")
        self.assertEqual(
            [call["argv"] for call in self.calls()],
            [["--version"], ["--help"], ["auth", "status"]],
        )
        self.assert_safe_output(result)

    def test_explicit_company_uses_only_company_profile_and_plan(self):
        self.write_fake(
            auth_text=json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "apiProvider": "firstParty",
                    "subscriptionType": "team",
                    "configDirectory": "__CLAUDE_CONFIG_DIR__",
                }
            )
        )
        result = self.run_check(profile="company", probe_auth=True)
        self.assertEqual(result["status"], "AUTH_STATUS_MATCHED")
        self.assertEqual(result["profile"], "company")
        self.assertEqual(
            [call["argv"] for call in self.calls()],
            [["--version"], ["--help"], ["auth", "status"]],
        )
        self.assertTrue(all(call["env"]["CLAUDE_CONFIG_DIR"] == str(self.company) for call in self.calls()))
        self.assertTrue(all("--print" not in call["argv"] and "-p" not in call["argv"] for call in self.calls()))

    def test_company_missing_is_unavailable_without_personal_fallback(self):
        self.write_config(include_company=False)
        result = self.run_check(profile="company", probe_auth=True)
        self.assertEqual(result["status"], "PROFILE_UNAVAILABLE")
        self.assertEqual(result["profile"], "company")
        self.assertEqual(self.calls(), [])

    def test_all_configured_profiles_must_exist_and_be_separate_canonical_directories(self):
        absent = self.root / "absent-profile"
        self.write_config(company_dir=absent)
        result = self.run_check()
        self.assertEqual(result["status"], "PROFILE_UNAVAILABLE")
        self.assertEqual(self.calls(), [])

        self.write_config(company_dir=self.personal)
        self.assertEqual(self.run_check()["status"], "INVALID_CONFIGURATION")
        self.assertEqual(self.calls(), [])

        nested = self.personal / "nested"
        nested.mkdir()
        self.write_config(company_dir=nested)
        self.assertEqual(self.run_check()["status"], "INVALID_CONFIGURATION")
        self.assertEqual(self.calls(), [])

        symlink = self.root / "profile-link"
        symlink.symlink_to(self.company, target_is_directory=True)
        self.write_config(company_dir=symlink)
        self.assertEqual(self.run_check()["status"], "INVALID_CONFIGURATION")
        self.assertEqual(self.calls(), [])

    def test_config_is_duplicate_safe_and_rejects_unknown_keys(self):
        self.config.write_text(
            '{"schema_version":1,"schema_version":1,"claude_bin":"/x","profiles":{}}',
            encoding="utf-8",
        )
        self.assertEqual(self.run_check()["status"], "INVALID_CONFIGURATION")
        self.assertEqual(self.calls(), [])
        self.write_config()
        content = json.loads(self.config.read_text())
        content["unexpected"] = "do-not-print"
        self.config.write_text(json.dumps(content), encoding="utf-8")
        result = self.run_check()
        self.assertEqual(result["status"], "INVALID_CONFIGURATION")
        self.assertNotIn("do-not-print", json.dumps(result))
        self.assertEqual(self.calls(), [])

    def test_config_file_and_ancestor_symlinks_are_rejected(self):
        leaf_link = self.root / "config-link.json"
        leaf_link.symlink_to(self.config)
        result = self.run_check(config=leaf_link)
        self.assertEqual(result["status"], "INVALID_CONFIGURATION")
        self.assertEqual(self.calls(), [])

        parent_link = self.root / "config-parent-link"
        parent_link.symlink_to(self.root, target_is_directory=True)
        result = self.run_check(config=parent_link / self.config.name)
        self.assertEqual(result["status"], "INVALID_CONFIGURATION")
        self.assertEqual(self.calls(), [])

    def test_missing_executable_is_not_installed_and_non_executable_is_invalid(self):
        content = json.loads(self.config.read_text())
        content["claude_bin"] = str(self.root / "missing-claude")
        self.config.write_text(json.dumps(content), encoding="utf-8")
        self.assertEqual(self.run_check()["status"], "NOT_INSTALLED")
        self.assertEqual(self.calls(), [])

        content["claude_bin"] = str(self.root)
        self.config.write_text(json.dumps(content), encoding="utf-8")
        self.assertEqual(self.run_check()["status"], "INVALID_CONFIGURATION")
        self.assertEqual(self.calls(), [])

    def test_forbidden_process_environment_key_presence_blocks_spawn_even_when_empty(self):
        names = (
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_API_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "CLAUDE_CODE_OAUTH_TOKEN_FILE",
            "CLAUDE_CODE_API_KEY_HELPER_TTL_MS",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_PROFILE",
            "CLAUDE_CODE_FUTURE_SELECTOR",
            "AWS_PROFILE",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "NODE_OPTIONS",
            "NODE_PATH",
            "LD_PRELOAD",
            "DYLD_INSERT_LIBRARIES",
            "PYTHONPATH",
        )
        for name in names:
            with self.subTest(name=name):
                self.clear_calls()
                environment = dict(self.environment)
                environment[name] = ""
                result = self.run_check(environ=environment)
                self.assertEqual(result["status"], "WRONG_BILLING_MODE")
                self.assertEqual(self.calls(), [])
                self.assertNotIn(name, json.dumps(result))

    def test_claude_config_dir_is_replaced_with_selected_profile(self):
        environment = dict(self.environment, CLAUDE_CONFIG_DIR="/ambient/ignored")
        result = self.run_check(environ=environment)
        self.assertEqual(result["status"], "VERSION_CHECKED")
        self.assertTrue(all(call["env"]["CLAUDE_CONFIG_DIR"] == str(self.personal) for call in self.calls()))
        self.assertTrue(all("CLAUDE_CONFIG_DIR" in call["env"] for call in self.calls()))

    def test_settings_environment_and_api_key_helper_block_before_spawn(self):
        cases = (
            {"apiKeyHelper": ""},
            {"env": {"ANTHROPIC_BASE_URL": ""}},
            {"env": {"CLAUDE_CODE_USE_VERTEX": "false"}},
        )
        for value in cases:
            with self.subTest(settings=value):
                self.clear_calls()
                (self.personal / "settings.json").write_text(json.dumps(value), encoding="utf-8")
                result = self.run_check()
                self.assertEqual(result["status"], "WRONG_BILLING_MODE")
                self.assertEqual(self.calls(), [])
                (self.personal / "settings.json").unlink()

    def test_unsafe_settings_and_bad_files_fail_closed_before_spawn(self):
        for value in (
            {"hooks": {}},
            {"plugins": []},
            {"mcpServers": {}},
            {"awsAuthRefresh": ""},
            {"awsCredentialExport": ""},
            {"gcpAuthRefresh": ""},
            {"extraKnownMarketplaces": []},
        ):
            with self.subTest(settings=value):
                self.clear_calls()
                (self.project / ".claude").mkdir(exist_ok=True)
                settings = self.project / ".claude" / "settings.local.json"
                settings.write_text(json.dumps(value), encoding="utf-8")
                result = self.run_check()
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertEqual(self.calls(), [])
                settings.unlink()

        bad = self.managed / "managed-settings.json"
        bad.write_text('{"env":', encoding="utf-8")
        result = self.run_check()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(self.calls(), [])
        bad.unlink()

        unknown_source = self.managed / "remote-settings.json"
        unknown_source.write_text("{}", encoding="utf-8")
        result = self.run_check()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(self.calls(), [])

    def test_settings_symlinks_oversize_and_duplicate_keys_fail_closed(self):
        link = self.personal / "settings.json"
        target = self.root / "outside-settings.json"
        target.write_text("{}", encoding="utf-8")
        link.symlink_to(target)
        self.assertEqual(self.run_check()["status"], "UNKNOWN")
        self.assertEqual(self.calls(), [])
        link.unlink()

        link.write_bytes(b"{" + b" " * (preflight.SETTINGS_LIMIT + 1) + b"}")
        self.assertEqual(self.run_check()["status"], "UNKNOWN")
        self.assertEqual(self.calls(), [])
        link.write_text('{"env":{},"env":{}}', encoding="utf-8")
        self.assertEqual(self.run_check()["status"], "UNKNOWN")
        self.assertEqual(self.calls(), [])

    def test_candidate_version_and_help_flag_allowlist(self):
        self.write_fake(version="Claude Code v2.1.294")
        result = self.run_check()
        self.assertEqual(result["status"], "UNSUPPORTED_VERSION")
        self.assertEqual(result["version"], "2.1.294")
        self.assertEqual([call["argv"] for call in self.calls()], [["--version"]])

        self.clear_calls()
        self.write_fake(help_text="--printsomething --output-format --permission-mode --session-id --resume")
        result = self.run_check()
        self.assertEqual(result["status"], "UNSUPPORTED_VERSION")
        self.assertEqual(
            result["capabilities"],
            ["--output-format", "--permission-mode", "--session-id", "--resume"],
        )
        self.assertEqual([call["argv"] for call in self.calls()], [["--version"], ["--help"]])

    def test_auth_false_expired_and_exit_code_contradictions(self):
        for logged_in, exit_code, expected in (
            (False, 1, "NOT_AUTHENTICATED"),
            (False, 0, "UNKNOWN"),
            (True, 1, "UNKNOWN"),
        ):
            with self.subTest(logged_in=logged_in, exit_code=exit_code):
                self.clear_calls()
                payload = json.dumps({"loggedIn": logged_in})
                self.write_fake(auth_text=payload, auth_exit=exit_code)
                result = self.run_check(probe_auth=True)
                self.assertEqual(result["status"], expected)
                calls = self.calls()
                self.assertEqual(
                    [call["argv"] for call in calls],
                    [["--version"], ["--help"], ["auth", "status"]],
                )
                self.assertTrue(all(call["env"]["CLAUDE_CONFIG_DIR"] == str(self.personal) for call in calls))
                self.assertFalse(any(call["env"]["CLAUDE_CONFIG_DIR"] == str(self.company) for call in calls))

    def test_malformed_duplicate_missing_and_secret_auth_output_is_never_reported(self):
        secret = "SECRET-AUTH-STDOUT-9981"
        cases = (
            ("malformed", "not json " + secret, 0),
            ("duplicate", '{"loggedIn":true,"loggedIn":true}', 0),
            ("missing fields", json.dumps({"loggedIn": True, "email": secret}), 0),
            ("deep nested", '{"loggedIn":true,"unknown":' + "[" * 1800 + "0" + "]" * 1800 + "}", 0),
        )
        for case, auth_text, exit_code in cases:
            with self.subTest(case=case):
                self.clear_calls()
                self.write_fake(auth_text=auth_text, auth_exit=exit_code, auth_stderr="SECRET-AUTH-STDERR-441")
                result = self.run_check(probe_auth=True)
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertEqual(
                    [call["argv"] for call in self.calls()],
                    [["--version"], ["--help"], ["auth", "status"]],
                )
                encoded = json.dumps(result)
                self.assertNotIn(secret, encoded)
                self.assertNotIn("SECRET-AUTH-STDERR-441", encoded)
                self.assertNotIn("private@example.invalid", encoded)

        self.clear_calls()
        valid_with_identity = {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "apiProvider": "firstParty",
            "subscriptionType": "pro",
            "configDirectory": "__CLAUDE_CONFIG_DIR__",
            "email": secret,
            "organization": "SECRET-ORG-772",
        }
        self.write_fake(
            auth_text=json.dumps(valid_with_identity),
            auth_stderr="SECRET-AUTH-STDERR-441",
        )
        result = self.run_check(probe_auth=True)
        self.assertEqual(result["status"], "AUTH_STATUS_MATCHED")
        self.assertEqual([call["argv"] for call in self.calls()][-1], ["auth", "status"])
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn("SECRET-ORG-772", json.dumps(result))
        self.assertNotIn("SECRET-AUTH-STDERR-441", json.dumps(result))

    def test_wrong_profile_and_wrong_billing_are_distinct(self):
        base = {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "apiProvider": "firstParty",
            "subscriptionType": "pro",
            "configDirectory": "/different/selected/profile",
        }
        self.write_fake(auth_text=json.dumps(base))
        self.assertEqual(self.run_check(probe_auth=True)["status"], "WRONG_PROFILE")
        self.assertEqual(
            [call["argv"] for call in self.calls()],
            [["--version"], ["--help"], ["auth", "status"]],
        )

        self.clear_calls()
        for field, value in (
            ("authMethod", "api_key"),
            ("authMethod", "oauth_token"),
            ("authMethod", "third_party"),
            ("apiProvider", "bedrock"),
            ("apiProvider", "thirdParty"),
            ("subscriptionType", "max"),
        ):
            with self.subTest(field=field, value=value):
                self.clear_calls()
                candidate = dict(base)
                candidate["configDirectory"] = "__CLAUDE_CONFIG_DIR__"
                candidate[field] = value
                self.write_fake(auth_text=json.dumps(candidate))
                result = self.run_check(probe_auth=True)
                self.assertEqual(result["status"], "WRONG_BILLING_MODE")
                self.assertEqual([call["argv"] for call in self.calls()], [["--version"], ["--help"], ["auth", "status"]])

    def test_timeout_overflow_eof_and_nonzero_are_unknown(self):
        self.write_fake(version_delay=2)
        result = self.run_check(timeout=0.2)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual([call["argv"] for call in self.calls()], [["--version"]])

        self.clear_calls()
        self.write_fake(version_bytes=preflight.COMMAND_OUTPUT_LIMIT + 4096)
        result = self.run_check()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual([call["argv"] for call in self.calls()], [["--version"]])

        self.clear_calls()
        self.write_fake(version="")
        result = self.run_check()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual([call["argv"] for call in self.calls()], [["--version"]])

        self.clear_calls()
        self.write_fake(version_exit=17)
        result = self.run_check()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual([call["argv"] for call in self.calls()], [["--version"]])

    def test_eof_waits_for_normal_exit_and_times_out_if_closed_streams_hang(self):
        self.write_fake(close_streams=True, close_delay=0.04)
        result = self.run_check(timeout=0.5)
        self.assertEqual(result["status"], "VERSION_CHECKED")
        self.assertEqual([call["argv"] for call in self.calls()], [["--version"], ["--help"]])

        self.clear_calls()
        self.write_fake(close_streams=True, close_delay=2)
        result = self.run_check(timeout=0.2)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual([call["argv"] for call in self.calls()], [["--version"]])

    def test_descendant_inherited_pipe_is_killed_without_waiting_for_its_sleep(self):
        self.write_fake(fork_descendant=True)
        result = self.run_check(timeout=0.7)
        self.assertEqual(result["status"], "VERSION_CHECKED")
        self.assertEqual([call["argv"] for call in self.calls()], [["--version"], ["--help"]])

    def test_unreapable_child_cleanup_never_uses_an_unlimited_wait(self):
        process = Mock()
        process.wait.side_effect = subprocess.TimeoutExpired("synthetic", 1)
        with patch.object(preflight, "_signal_process_group"):
            self.assertFalse(preflight._stop_process_group(process))
        self.assertEqual(process.wait.call_args_list, [call(timeout=0.04), call(timeout=1.0)])

    def test_settings_change_between_probes_fails_unknown_without_help(self):
        self.write_fake(settings_on_version='{"apiKeyHelper":""}')
        result = self.run_check()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual([call["argv"] for call in self.calls()], [["--version"]])

    def test_profile_replaced_by_symlink_after_version_fails_before_help_or_auth(self):
        self.write_fake(replace_profile_with_symlink=True)
        result = self.run_check(probe_auth=True)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual([call["argv"] for call in self.calls()], [["--version"]])

    def test_auth_stderr_overflow_is_unknown_and_capped(self):
        self.write_fake(auth_stderr="z" * (preflight.COMMAND_OUTPUT_LIMIT + 1))
        result = self.run_check(probe_auth=True)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(
            [call["argv"] for call in self.calls()],
            [["--version"], ["--help"], ["auth", "status"]],
        )
        self.assertNotIn("zzzz", json.dumps(result))

    def test_deep_settings_json_fails_unknown_without_spawn(self):
        (self.personal / "settings.json").write_text("[" * 1800 + "0" + "]" * 1800, encoding="utf-8")
        result = self.run_check()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(self.calls(), [])

    def test_managed_directory_entry_cap_fails_unknown_without_spawn(self):
        settings_dir = self.managed / "managed-settings.d"
        settings_dir.mkdir()
        for index in range(preflight.MAX_SETTINGS_ENTRIES + 1):
            (settings_dir / f"{index:04d}.json").write_text("{}", encoding="utf-8")
        result = self.run_check()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(self.calls(), [])

        for entry in settings_dir.iterdir():
            entry.unlink()
        for index in range(preflight.MAX_SETTINGS_FILES + 1):
            (settings_dir / f"{index:02d}.json").write_text("{}", encoding="utf-8")
        result = self.run_check()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(self.calls(), [])

        for entry in settings_dir.iterdir():
            entry.unlink()
        large_document = json.dumps({"padding": "x" * (60 * 1024)})
        for index in range(5):
            (settings_dir / f"{index:02d}.json").write_text(large_document, encoding="utf-8")
        result = self.run_check()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(self.calls(), [])

    def test_settings_permission_error_and_fifo_config_fail_closed(self):
        settings_path = self.personal / "settings.json"
        settings_path.write_text("{}", encoding="utf-8")
        real_open = preflight.os.open

        def deny_settings(path, *args, **kwargs):
            if str(path) == str(settings_path):
                raise PermissionError("synthetic permission failure")
            return real_open(path, *args, **kwargs)

        with patch.object(preflight.os, "open", side_effect=deny_settings):
            result = self.run_check()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(self.calls(), [])

        fifo = self.root / "config-fifo"
        os.mkfifo(fifo)
        result = self.run_check(config=fifo)
        self.assertEqual(result["status"], "INVALID_CONFIGURATION")
        self.assertEqual(self.calls(), [])

    def test_credentials_store_sentinels_are_never_opened(self):
        profile_store = self.personal / ".credentials.json"
        home_store = Path(self.environment["HOME"]) / ".claude.json"
        profile_store.write_text("PROFILE-CREDENTIAL-SENTINEL", encoding="utf-8")
        home_store.write_text("HOME-CREDENTIAL-SENTINEL", encoding="utf-8")
        opened = []
        sentinels = {str(profile_store), str(home_store)}

        def tracking(real_open):
            def tracked(path, *args, **kwargs):
                if str(path) in sentinels:
                    opened.append(str(path))
                    raise AssertionError("credential sentinel must not be opened")
                return real_open(path, *args, **kwargs)
            return tracked

        with patch.object(preflight.os, "open", side_effect=tracking(os.open)), \
             patch.object(builtins, "open", side_effect=tracking(builtins.open)), \
             patch.object(io, "open", side_effect=tracking(io.open)):
            # Prove the instrumentation detects each ordinary access route.
            for access in (
                lambda: os.open(profile_store, os.O_RDONLY),
                lambda: builtins.open(profile_store),
                lambda: home_store.open(),
                lambda: home_store.read_text(),
            ):
                with self.assertRaises(AssertionError):
                    access()
            self.assertEqual(len(opened), 4)
            opened.clear()
            result = self.run_check()
        self.assertEqual(result["status"], "VERSION_CHECKED")
        self.assertEqual(opened, [])
        self.assertEqual([call["argv"] for call in self.calls()], [["--version"], ["--help"]])

    def test_invalid_cli_argument_does_not_echo_secret_input(self):
        secret = "SECRET-CLI-ARGUMENT-8842"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exit_result:
                preflight.main(
                    ["--config", str(self.config), "--project-root", str(self.project), "--profile", secret]
                )
        self.assertEqual(exit_result.exception.code, 1)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "INVALID_CONFIGURATION")
        self.assertNotIn(secret, stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(self.calls(), [])

    def test_child_environment_cwd_stdin_and_argv_are_narrow(self):
        result = self.run_check()
        self.assertEqual(result["status"], "VERSION_CHECKED")
        calls = self.calls()
        self.assertEqual([entry["argv"] for entry in calls], [["--version"], ["--help"]])
        for entry in calls:
            self.assertEqual(entry["cwd"], str(self.project))
            self.assertTrue(entry["stdin_eof"])
            self.assertEqual(
                set(entry["env"]),
                {"HOME", "USER", "LOGNAME", "PATH", "LANG", "CLAUDE_CONFIG_DIR"},
            )
            self.assertEqual(entry["env"]["CLAUDE_CONFIG_DIR"], str(self.personal))
            self.assertNotIn("--print", entry["argv"])
            self.assertNotIn("-p", entry["argv"])
        self.assert_safe_output(result)


if __name__ == "__main__":
    unittest.main()
