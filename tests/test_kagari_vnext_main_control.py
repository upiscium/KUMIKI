"""Bounded source-only #291 acceptance: disposable Git and private host state.

No network, real Task/model/OpenCode, user Project, service or host preparation.
"""
from __future__ import annotations

import fcntl
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import select
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import kagari_vnext_main_control as main


class MainControlTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="kagari291-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.project = self.base / "project"
        self.project.mkdir(mode=0o700)
        self.home = self.base / "state"
        self.home.mkdir(mode=0o700)
        # Patch only in this disposable suite; neither a CLI flag nor an
        # inherited environment variable may choose the production Main lock.
        self.host_lease_home = self.base / "host-lease"
        self.host_lease_home.mkdir(mode=0o700)
        patcher = patch.object(main, "_host_state_home",
                               return_value=self.host_lease_home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repository = "example/project"
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("remote", "add", "origin", "https://github.com/example/project.git")
        (self.project / "tracked").write_bytes(b"baseline\n")
        self.git("add", "tracked")
        self.git("-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture")
        (self.project / "tracked").write_bytes(b"staged\n")
        self.git("add", "tracked")
        (self.project / "tracked").write_bytes(b"dirty unstaged\n")
        (self.project / "untracked").write_bytes(b"valuable\n")
        self.before = self.project_bytes()
        self.children = []
        self.addCleanup(self.stop_children)

    def git(self, *args, project=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
        result = subprocess.run(["git", "-C", str(project or self.project), *args],
                                env=env, capture_output=True, timeout=10, check=True)
        return result.stdout

    def project_bytes(self):
        # Exact contents of every regular fixture file, including HEAD, index,
        # refs, config, object database and dirty/untracked Product bytes.
        return {str(p.relative_to(self.project)): p.read_bytes()
                for p in self.project.rglob("*") if p.is_file()}

    def control(self, **kwargs):
        return main.Controller(kwargs.get("project", self.project),
                               kwargs.get("home", self.home),
                               kwargs.get("repository", self.repository),
                               kwargs.get("issues", [291, 304]),
                               kwargs.get("profile", main.PROFILE))

    def args(self, action, *, issues=(291, 304), home=None, project=None,
             repository=None, extra=()):
        # An isolated subprocess explicitly installs the test-only mock
        # before calling main(). Production has no mutable host-lock switch.
        wrapper = (
            "import importlib.util,pathlib,sys;"
            "spec=importlib.util.spec_from_file_location('kagari_vnext_main_control',"
            "sys.argv[1]);"
            "ctl=importlib.util.module_from_spec(spec);"
            "spec.loader.exec_module(ctl);"
            "ctl._host_state_home=lambda:pathlib.Path(sys.argv[2]);"
            "raise SystemExit(ctl.main(sys.argv[3:]))"
        )
        return [sys.executable, "-I", "-c", wrapper,
                str(TOOLS / "kagari_vnext_main_control.py"),
                str(self.host_lease_home),
                action, "--profile", main.PROFILE,
                "--project-root", str(project or self.project),
                "--state-home", str(home or self.home),
                "--repository", repository or self.repository,
                "--eligible-issues", *(str(i) for i in issues), *extra]

    def cli(self, action, **kwargs):
        result = subprocess.run(self.args(action, **kwargs), capture_output=True,
                                timeout=15, cwd=self.base)
        self.assertIn(result.returncode, (0, 2), result.stderr)
        self.assertEqual(result.stderr, b"")
        return result.returncode, [json.loads(line) for line in result.stdout.splitlines()]

    def hold(self, **kwargs):
        process = subprocess.Popen(
            self.args("hold", extra=("--hold-seconds", "30"), **kwargs),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.base)
        self.children.append(process)
        ready, _, _ = select.select([process.stdout], [], [], 10)
        self.assertTrue(ready, "owner did not report readiness within 10 seconds")
        line = process.stdout.readline()
        self.assertTrue(line, "owner exited without readiness")
        result = json.loads(line)
        self.assertEqual(result["status"], "HOLDING", result)
        self.assert_no_execution(result)
        return process, result

    def stop_children(self):
        for process in self.children:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)

    def clean_stop(self, process):
        process.terminate()
        out, err = process.communicate(timeout=5)
        self.assertEqual((process.returncode, err), (0, b""))
        self.assertEqual(json.loads(out)["status"], "CLEAN_RELEASE")

    def paths(self):
        with self.control() as control:
            key = control.key
        directory = self.home / main.NAMESPACE
        return directory / (key + ".lock"), directory / (key + ".json")

    def create_clean(self):
        with self.control() as control:
            result = control.start()
            control.release()
        return result

    def assert_no_execution(self, result):
        self.assertEqual(result["github_issue_verification"], "NOT_VERIFIED")
        self.assertEqual(result["execution_authority"], "NOT_EXECUTION_AUTHORIZED")
        self.assertFalse(result["task_execution_authorized"])
        self.assertFalse(result["model_or_session_started"])

    def blocked(self, code, action="start", **kwargs):
        with self.assertRaises(main.Blocked) as caught:
            with self.control(**kwargs) as control:
                getattr(control, action)()
        self.assertEqual(caught.exception.code, code)

    def test_plan_doctor_read_only_and_explicit_non_authority(self):
        for action in ("plan", "doctor"):
            code, results = self.cli(action, extra=("--issue", "291"))
            self.assertEqual(code, 0)
            self.assertEqual(results[0]["status"], "ABSENT")
            self.assertEqual(results[0]["issue_eligibility"], "ELIGIBLE_INPUT_ONLY")
            self.assert_no_execution(results[0])
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(self.project_bytes(), self.before)

    def test_two_processes_same_repo_and_different_issue_scope_refused(self):
        process, first = self.hold()
        for issues in ((291, 304), (999,)):
            code, results = self.cli("hold", issues=issues,
                                     extra=("--hold-seconds", "0.01"))
            self.assertEqual(code, 2)
            self.assertEqual(results[0]["code"], "MAIN_ALREADY_LIVE")
            self.assert_no_execution(results[0])
        code, results = self.cli("doctor")
        self.assertEqual(code, 2)
        self.assertEqual(results[0]["status"], "MAIN_ALREADY_LIVE_RECORD_NOT_READ")
        self.clean_stop(process)
        lock, record = self.paths()
        self.assertEqual(json.loads(record.read_bytes())["state"], "CLEAN")
        inode = lock.stat().st_ino
        second, result = self.hold()
        self.assertNotEqual(first["record"]["owner"]["token"], result["record"]["owner"]["token"])
        self.clean_stop(second)
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertEqual(self.project_bytes(), self.before)

    def test_barrier_synchronized_two_process_starts_exactly_one_wins(self):
        # Two real independent Python processes enter the CLI near the same
        # instant; no earlier HOLDING acknowledgement is used as a barrier.
        barrier = threading.Barrier(3)

        def launch():
            barrier.wait(timeout=10)
            process = subprocess.Popen(
                self.args("hold", extra=("--hold-seconds", "5")),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=self.base)
            self.children.append(process)
            return process

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(launch) for _ in range(2)]
            barrier.wait(timeout=10)
            processes = [f.result(timeout=12) for f in futures]

        statuses = []
        for process in processes:
            ready, _, _ = select.select([process.stdout], [], [], 12)
            self.assertTrue(ready, "concurrent Main did not report a result")
            line = process.stdout.readline()
            self.assertTrue(line, "concurrent Main exited silently")
            reply = json.loads(line)
            self.assert_no_execution(reply)
            statuses.append((process, reply))
        self.assertCountEqual([r["status"] for _, r in statuses],
                              ["HOLDING", "BLOCKED"])
        refused = next((p, r) for p, r in statuses
                       if r["status"] == "BLOCKED")
        self.assertEqual(refused[1]["code"], "MAIN_ALREADY_LIVE")
        refused[0].communicate(timeout=5)
        self.assertEqual(refused[0].returncode, 2)

        winner = next(p for p, r in statuses if r["status"] == "HOLDING")
        self.clean_stop(winner)
        self.assertEqual(self.project_bytes(), self.before)

    def test_alternate_state_home_cannot_bypass_live_owner(self):
        process, _ = self.hold()
        other = self.base / "other-state"
        other.mkdir(mode=0o700)
        code, results = self.cli("hold", home=other, issues=(42,),
                                 extra=("--hold-seconds", "0.01"))
        self.assertEqual((code, results[0]["code"]), (2, "MAIN_ALREADY_LIVE"))
        self.assertEqual(list(other.iterdir()), [])
        self.clean_stop(process)

    def test_crash_reconciliation_exact_digest_and_no_pid_grant(self):
        process, ready = self.hold()
        process.kill()
        process.communicate(timeout=5)
        lock, record = self.paths()
        before = record.read_bytes()
        code, results = self.cli("doctor")
        self.assertEqual((code, results[0]["status"]), (2, "RECONCILIATION_REQUIRED"))
        self.assertEqual(results[0]["record_sha256"], main.digest(before))
        self.blocked("RECONCILIATION_REQUIRED")
        # A nonexistent or recycled/live PID is never used as a durable grant.
        changed = json.loads(before)
        changed["owner"]["pid"] = os.getpid()
        record.write_bytes(main.canonical(changed))
        self.blocked("RECONCILIATION_REQUIRED")
        code, results = self.cli("reconcile", extra=("--expected-record-sha256", ready["record_sha256"]))
        self.assertEqual((code, results[0]["code"]), (2, "RECONCILE_DIGEST_MISMATCH"))
        code, results = self.cli("reconcile", extra=("--expected-record-sha256", main.digest(record.read_bytes())))
        self.assertEqual((code, results[0]["status"]), (0, "RECONCILED_NO_EXECUTION"))
        self.assert_no_execution(results[0])
        self.create_clean()
        self.assertTrue(lock.exists())
        self.assertEqual(self.project_bytes(), self.before)

    def test_reconcile_cannot_interrupt_live_owner(self):
        process, ready = self.hold()
        code, result = self.cli("reconcile", extra=("--expected-record-sha256", ready["record_sha256"]))
        self.assertEqual((code, result[0]["code"]), (2, "MAIN_ALREADY_LIVE"))
        self.clean_stop(process)

    def test_changed_issue_list_refused_after_clean_release(self):
        self.create_clean()
        _, record = self.paths()
        before = record.read_bytes()
        for issues in ([291], [291, 304, 305]):
            self.blocked("CONTEXT_OR_ISSUE_SCOPE_DRIFT", issues=issues)
        with self.control(issues=[304, 291]) as control:
            self.assertEqual(control.inspect()["status"], "CLEAN")
        self.assertEqual(record.read_bytes(), before)

    def test_noneligible_issue_refused_before_state_creation(self):
        code, results = self.cli("hold", extra=("--issue", "999", "--hold-seconds", "0.01"))
        self.assertEqual((code, results[0]["code"]), (2, "ISSUE_NOT_ELIGIBLE"))
        self.assertEqual(list(self.home.iterdir()), [])

    def test_scope_profile_and_repository_validation(self):
        for issues in ([], [0], [-1], [True], [1, 1], [main.MAX_ISSUE + 1], list(range(1, 130))):
            with self.assertRaises(main.Blocked):
                self.control(issues=issues)
        self.blocked("EXPLICIT_PREVIEW_PROFILE_REQUIRED", profile="ga")
        self.blocked("INVALID_REPOSITORY", repository="example/../other")
        self.blocked("REPOSITORY_IDENTITY_MISMATCH", repository="example/other")
        self.assertEqual(list(self.home.iterdir()), [])

    def test_foreign_context_copied_under_current_key_refused(self):
        self.create_clean()
        _, record = self.paths()
        original = record.read_bytes()
        changed = json.loads(original)
        changed["binding"]["project"]["project_inode"] += 1
        record.write_bytes(main.canonical(changed))
        self.blocked("CONTEXT_OR_ISSUE_SCOPE_DRIFT")
        self.assertEqual(record.read_bytes(), main.canonical(changed))

    def test_foreign_main_supervisor_lease_blocks_without_state_effects(self):
        # This is the exact repo-keyed host flock already used by PR #309.
        path = main._shared_host_lock_path(self.repository, create=True)
        self.assertEqual(path.parent.name, "repository-main-locks")
        with path.open("a+b") as owner:
            os.fchmod(owner.fileno(), 0o600)
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            code, response = self.cli(
                "hold", extra=("--hold-seconds", "0.05"))
            self.assertEqual((code, response[0]["code"]),
                             (2, "MAIN_ALREADY_LIVE"))
            self.assertEqual(list(self.home.iterdir()), [])
            code, response = self.cli("doctor")
            self.assertEqual((code, response[0]["status"]),
                             (2, "MAIN_ALREADY_LIVE_RECORD_NOT_READ"))
            fcntl.flock(owner, fcntl.LOCK_UN)
        code, response = self.cli("hold", extra=("--hold-seconds", "0.05"))
        self.assertEqual(code, 0, response)
        self.assertEqual([r["status"] for r in response],
                         ["HOLDING", "CLEAN_RELEASE"])
        self.assertEqual(self.project_bytes(), self.before)

    def test_different_checkouts_same_origin_share_canonical_host_lease(self):
        alternate = self.base / "alternate-clone"
        alternate.mkdir(mode=0o700)
        self.git("init", "-q", "-b", "main", project=alternate)
        self.git("remote", "add", "origin",
                 "https://github.com/example/project.git", project=alternate)
        home2 = self.base / "alternate-state"
        home2.mkdir(mode=0o700)
        process, first = self.hold()
        code, response = self.cli(
            "hold", project=alternate, home=home2,
            issues=(999,), extra=("--hold-seconds", "0.05"))
        self.assertEqual((code, response[0]["code"]),
                         (2, "MAIN_ALREADY_LIVE"))
        self.assertEqual(list(home2.iterdir()), [])
        self.clean_stop(process)
        code, response = self.cli(
            "hold", project=alternate, home=home2,
            issues=(999,), extra=("--hold-seconds", "0.05"))
        self.assertEqual(code, 0, response)
        self.assertEqual([r["status"] for r in response],
                         ["HOLDING", "CLEAN_RELEASE"])
        self.assertEqual(self.project_bytes(), self.before)

    def test_unknown_or_modified_canonical_host_lock_blocks(self):
        path = main._shared_host_lock_path(self.repository, create=True)
        path.write_bytes(b"foreign owner state")
        path.chmod(0o600)
        self.blocked("MAIN_HOST_LOCK_UNSAFE")
        self.assertEqual(path.read_bytes(), b"foreign owner state")
        path.unlink()
        path.write_bytes(b"")
        path.chmod(0o640)
        self.blocked("MAIN_HOST_LOCK_UNSAFE")
        path.chmod(0o600)
        alias = self.base / "hardlink"
        os.link(path, alias)
        self.blocked("MAIN_HOST_LOCK_UNSAFE")
        alias.unlink()
        path.unlink()
        outside = self.base / "sentinel"
        outside.write_bytes(b"do not touch")
        path.symlink_to(outside)
        self.blocked("MAIN_HOST_LOCK_UNSAFE")
        self.assertEqual(outside.read_bytes(), b"do not touch")
        path.unlink()
        self.assertEqual(self.project_bytes(), self.before)

    def test_shared_lock_path_is_unaffected_by_cli_state_home_or_home_env(self):
        with patch.dict(os.environ, {"HOME": str(self.base / "fake-home"),
                                     "XDG_STATE_HOME": str(self.base / "fake-xdg")}):
            path = main._shared_host_lock_path(self.repository, create=False)
        self.assertIsNone(path)
        self.assertEqual(list(self.host_lease_home.iterdir()), [])
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(self.project_bytes(), self.before)

    def test_different_repos_have_independent_owners(self):
        other = self.base / "other-project"
        other.mkdir(mode=0o700)
        self.git("init", "-q", "-b", "main", project=other)
        self.git("remote", "add", "origin", "git@github.com:example/other.git", project=other)
        process, first = self.hold()
        second, ready = self.hold(project=other, repository="example/other", issues=(42,))
        self.assertNotEqual(first["project_key"], ready["project_key"])
        self.clean_stop(second)
        self.clean_stop(process)

    def test_ambiguous_owner_record_refused_without_rewrite(self):
        self.create_clean()
        _, record = self.paths()
        good = json.loads(record.read_bytes())
        for raw in (b"{bad", b"", b"{}\n", main.canonical({**good, "schema": 2}),
                    main.canonical({**good, "state": "UNKNOWN"}),
                    main.canonical({**good, "owner": {"pid": True, "token": "x"}})):
            record.write_bytes(raw)
            self.blocked("AMBIGUOUS_OWNER_RECORD_REQUIRES_REVIEW")
            self.assertEqual(record.read_bytes(), raw)

    def test_state_home_unsafe_modes_and_symlink(self):
        for mode in (0o755, 0o750):
            self.home.chmod(mode)
            self.blocked("STATE_HOME_NOT_PRIVATE")
        self.home.chmod(0o777)
        self.blocked("DIRECTORY_OWNER_OR_MODE_UNSAFE")
        self.home.chmod(0o700)
        alias = self.base / "state-alias"
        alias.symlink_to(self.home, target_is_directory=True)
        self.blocked("NONCANONICAL_DIRECTORY", home=alias)

    def test_project_and_state_must_be_disjoint_canonical_directories(self):
        inside = self.project / "state"
        inside.mkdir(mode=0o700)
        self.blocked("STATE_MUST_BE_EXTERNAL", home=inside)
        alias = self.base / "project-alias"
        alias.symlink_to(self.project, target_is_directory=True)
        self.blocked("NONCANONICAL_DIRECTORY", project=alias)
        self.blocked("NONCANONICAL_DIRECTORY", project=Path("project"))

    def test_namespace_unknown_content_and_staging_refuse(self):
        self.create_clean()
        unknown = self.home / main.NAMESPACE / "unrecognized"
        unknown.write_bytes(b"do not remove")
        self.blocked("UNKNOWN_STATE_PATH_REQUIRES_REVIEW")
        self.assertEqual(unknown.read_bytes(), b"do not remove")

    def test_namespace_symlink_refused_without_touching_target(self):
        target = self.base / "target"
        target.mkdir(mode=0o700)
        (self.home / main.NAMESPACE).symlink_to(target, target_is_directory=True)
        code, result = self.cli("hold", extra=("--hold-seconds", "0.01"))
        self.assertEqual((code, result[0]["code"]), (2, "UNSAFE_OR_UNAVAILABLE_CONTEXT"))
        self.assertEqual(list(target.iterdir()), [])

    def test_lock_and_record_hardlinks_symlinks_and_modes_refused(self):
        self.create_clean()
        lock, record = self.paths()
        for path in (lock, record):
            path.chmod(0o640)
            self.blocked("STATE_FILE_NOT_PRIVATE")
            path.chmod(0o600)
            alias = self.base / (path.suffix + "-alias")
            os.link(path, alias)
            self.blocked("FILE_OWNER_MODE_OR_LINK_UNSAFE")
            alias.unlink()
            original = path.read_bytes()
            target = self.base / (path.suffix + "-target")
            target.write_bytes(original)
            target.chmod(0o600)
            path.unlink()
            path.symlink_to(target)
            code, result = self.cli("hold", extra=("--hold-seconds", "0.01"))
            self.assertEqual(code, 2)
            self.assertEqual(result[0]["code"], "UNSAFE_OR_UNAVAILABLE_CONTEXT")
            self.assertEqual(target.read_bytes(), original)
            path.unlink()
            path.write_bytes(original)
            path.chmod(0o600)

    def test_hostile_cwd_git_environment_and_path_are_not_authority(self):
        trap = self.base / "hostile"
        trap.mkdir()
        marker = self.base / "executed"
        evil = trap / "git"
        evil.write_text(f"#!/bin/sh\ntouch {marker}\nexit 99\n")
        evil.chmod(0o700)
        env = {**os.environ, "GIT_DIR": str(trap), "GIT_WORK_TREE": str(trap),
               "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor",
               "GIT_CONFIG_VALUE_0": str(evil), "GIT_INDEX_FILE": str(marker),
               "PATH": str(trap)}
        result = subprocess.run(self.args("hold", extra=("--hold-seconds", "0.01")),
                                cwd=trap, env=env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(self.project_bytes(), self.before)

    def test_config_include_and_linked_worktree_are_unsupported(self):
        external = self.base / "include"
        external.write_text("[core]\n bare = false\n")
        self.git("config", "include.path", str(external))
        self.blocked("GIT_CONFIG_INCLUDES_UNSUPPORTED")
        self.git("config", "--unset", "include.path")
        worktree = self.base / "worktree"
        self.git("worktree", "add", "--detach", str(worktree), "HEAD")
        code, result = self.cli("plan", project=worktree)
        self.assertEqual(code, 2)
        self.assertEqual(result[0]["code"], "UNSAFE_OR_UNAVAILABLE_CONTEXT")

    def test_no_dispatch_only_read_only_git_subprocesses(self):
        run = subprocess.run
        calls = []

        def observe(command, **kwargs):
            calls.append(command)
            return run(command, **kwargs)

        with patch.object(main.subprocess, "run", side_effect=observe):
            self.create_clean()
        self.assertTrue(calls)
        for command in calls:
            self.assertEqual(Path(command[0]).name, "git")
            self.assertIn("--no-optional-locks", command)
            self.assertIn(command[8], ("config", "rev-parse"))
        self.assertEqual(self.project_bytes(), self.before)
        self.assertFalse((self.project / ".worktrees").exists())

    def test_duration_is_bounded_and_no_owner_left_running(self):
        for seconds in ("0", "-1", "301", "nan", "inf"):
            code, result = self.cli("hold", extra=("--hold-seconds", seconds))
            self.assertEqual((code, result[0]["code"]), (2, "HOLD_DURATION_OUT_OF_BOUNDS"))
        code, result = self.cli("hold", extra=("--hold-seconds", "0.01"))
        self.assertEqual(code, 0)
        self.assertEqual([r["status"] for r in result], ["HOLDING", "CLEAN_RELEASE"])
        lock, record = self.paths()
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)
        self.assertEqual(self.project_bytes(), self.before)


if __name__ == "__main__":
    unittest.main()
