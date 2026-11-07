"""KUMIKI #364 Phase A: disposable, hostile Git-config no-exec regression."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
READER = ROOT / "components/agent-core/.automation/bin/safe_git_inspect.py"
TOOL = ROOT / "components/agent-core/.opencode/tool/safe_git_inspect.ts"
LAYOUTS = (
    "agent-base", "agent-cpp-cmake", "agent-nix",
    "agent-python", "agent-rust", "agent-typescript-node",
)


class SafeGitPhaseATest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="kagari-364-phase-a-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.main = self.home / "main"
        self.main.mkdir()
        self.git_binary = shutil.which("git")
        self.assertIsNotNone(self.git_binary)
        self.git("init", "-q", "-b", "main", cwd=self.main)
        self.git("remote", "add", "origin", "https://github.com/acme/widgets.git", cwd=self.main)
        self.git("config", "user.name", "Fixture", cwd=self.main)
        self.git("config", "user.email", "fixture@example.test", cwd=self.main)
        (self.main / ".gitignore").write_text("/.task-state/\n", encoding="utf-8")
        (self.main / ".gitattributes").write_text("*.txt filter=evil diff=evil\n", encoding="utf-8")
        (self.main / "readme.txt").write_text("original\n", encoding="utf-8")
        self.git("add", ".gitignore", ".gitattributes", "readme.txt", cwd=self.main)
        self.git("commit", "-qm", "base", cwd=self.main)
        self.task_root = self.home / "issue-364"
        self.git("worktree", "add", "-q", "-b", "task/364-inspect", str(self.task_root),
                 "main", cwd=self.main)
        self.write_marker()
        self.marker = self.home / "external-executed"
        self.script = self.home / "external.sh"
        self.script.write_text(
            "#!/bin/sh\nprintf HIT >> " + str(self.marker) + "\n"
            "if [ \"$1\" = \"2\" ]; then printf \"/\"; exit 0; fi\n"
            "cat\n", encoding="utf-8"
        )
        self.script.chmod(0o700)

    def git(self, *argv: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(
            [str(self.git_binary), *argv], cwd=cwd or self.task_root,
            capture_output=True, timeout=10, check=False
        )
        if check:
            self.assertEqual(result.returncode, 0, (argv, result.stderr))
        return result

    def write_marker(self) -> None:
        state = self.task_root / ".task-state"
        state.mkdir(exist_ok=True)
        (state / "task.md").write_text(
            "# Task\n"
            "- Task ID: 364\n"
            "- Branch: task/364-inspect\n"
            "- Worktree: " + str(self.task_root) + "\n",
            encoding="utf-8",
        )

    def cli(self, operation: str, *, task: str = "364", root: Path | None = None,
            cwd: Path | None = None,
            extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update({
            "KAGARI_GIT_INSPECT_ROOT": str(root or self.task_root),
            "KAGARI_GIT_INSPECT_BINARY": str(self.git_binary),
        })
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, "-I", "-B", str(READER), task, operation],
            cwd=cwd or self.task_root, env=env, capture_output=True, text=True,
            timeout=20, check=False,
        )

    def test_identity_and_index_phase_a_prototype(self) -> None:
        for operation in ("identity", "index-meta"):
            with self.subTest(operation=operation):
                run = self.cli(operation)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertLessEqual(len(run.stdout.encode()), 1536)
                data = json.loads(run.stdout)
                self.assertEqual(data["task"], "364")
                self.assertEqual(data["repository"], "acme/widgets")
                self.assertEqual(data["branch"], "task/364-inspect")
                self.assertEqual(data["operation"], operation)
                self.assertEqual(data["phase"], "A")
                self.assertEqual(data["qualification"], "PROTOTYPE_ONLY")
                self.assertEqual(data["object_format"], "sha1")
                self.assertEqual(len(data["head"]), 40)
                self.assertEqual(
                    data["worktree_sha256"], hashlib.sha256(str(self.task_root).encode()).hexdigest()
                )
                if operation == "index-meta":
                    self.assertEqual(data["index_entries"], 3)
                    # Independent exact-byte reference to canonical stage-0
                    # listing, rather than merely checking the hash length.
                    listing = self.git(
                        "-c", "core.fsmonitor=false",
                        "ls-files", "--stage", "-z",
                    ).stdout
                    self.assertEqual(data["index_listing_sha256"],
                                     hashlib.sha256(listing).hexdigest())
                self.assertEqual(json.loads(self.cli(operation).stdout), data)

    def test_hostile_git_config_and_env_do_not_execute_helpers(self) -> None:
        # Configuration is local to this disposable Git fixture.
        for key in ("diff.external", "diff.evil.command",
                    "filter.evil.clean", "core.fsmonitor"):
            self.git("config", key, str(self.script))
        (self.task_root / "readme.txt").write_text("modified with trailing space \n")
        env = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": str(self.script),
            "GIT_EXTERNAL_DIFF": str(self.script),
            "GIT_PAGER": str(self.script),
            "GIT_TRACE": str(self.marker),
            "HOME": str(self.home),
            "GIT_CONFIG_GLOBAL": str(self.home / "forged-global"),
            "GIT_ALLOW_PROTOCOL": "ssh:file",
            "GIT_NO_LAZY_FETCH": "0",
        }
        self.marker.unlink(missing_ok=True)
        index = Path(self.git(
            "rev-parse", "--path-format=absolute", "--git-path", "index",
            cwd=self.task_root,
        ).stdout.decode().strip())
        self.assertTrue(index.is_file())
        self.assertNotEqual(index, self.main / ".git/index")
        before = hashlib.sha256(index.read_bytes()).hexdigest()
        for operation in ("identity", "index-meta"):
            run = self.cli(operation, extra_env=env)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertFalse(self.marker.exists(), f"Unexpected helper invocation in {operation}")
        self.assertEqual(hashlib.sha256(index.read_bytes()).hexdigest(), before)
        # Confirm the attacker fixture genuinely dispatches the helper when unguarded.
        self.git("ls-files", "--stage", cwd=self.task_root)
        self.assertTrue(self.marker.exists(), "The hostile FSMonitor fixture never activated")

    def test_wrong_task_root_and_missing_marker_fail_closed(self) -> None:
        for task, root in (("365", None), ("364", self.main), ("x", None)):
            with self.subTest(task=task, root=root):
                bad = self.cli("identity", task=task, root=root)
                self.assertNotEqual(bad.returncode, 0)
        (self.task_root / ".task-state/task.md").unlink()
        self.assertNotEqual(self.cli("identity").returncode, 0)

    def test_replaced_marker_and_duplicate_worktree_fail_closed(self) -> None:
        path = self.task_root / ".task-state/task.md"
        original = path.read_text()
        path.unlink()
        foreign = self.home / "foreign"
        foreign.write_text(original)
        path.symlink_to(foreign)
        self.assertNotEqual(self.cli("identity").returncode, 0)
        path.unlink()
        path.write_text(original)
        other = self.home / "issue-364-duplicate"
        self.git("worktree", "add", "-q", "-b", "fix/364-duplicate", str(other),
                 "main", cwd=self.main)
        self.assertNotEqual(self.cli("identity").returncode, 0)

    def test_slash_suffixed_duplicate_task_is_rejected(self) -> None:
        other = self.home / "nested-branch-duplicate"
        self.git("worktree", "add", "-q", "-b", "fix/364-a/b",
                 str(other), "main", cwd=self.main)
        self.assertNotEqual(self.cli("identity").returncode, 0)

    def test_trailing_space_in_worktree_path_preserved(self) -> None:
        other = self.home / "valid-trailing-space "
        self.git("worktree", "add", "-q", "-b", "task/365-spaced",
                 str(other), "main", cwd=self.main)
        state = other / ".task-state"
        state.mkdir()
        (state / "task.md").write_text(
            "# Task\n- Task ID: 365\n- Branch: task/365-spaced\n"
            "- Worktree: " + str(other) + "\n")
        result = self.cli("identity", task="365", root=other, cwd=other)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["branch"], "task/365-spaced")

    def test_bare_inventory_entry_is_handled(self) -> None:
        spec = importlib.util.spec_from_file_location("safe_git_bare_fixture", READER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        oid = self.git("rev-parse", "--verify", "HEAD").stdout.strip()
        listing = (
            b"worktree /tmp/disposable-bare\x00bare\x00\x00"
            + b"worktree " + str(self.task_root).encode()
            + b"\x00HEAD " + oid
            + b"\x00branch refs/heads/task/364-inspect\x00\x00"
        )
        with mock.patch.object(module, "_git", return_value=listing):
            head, branch = module._registered(self.task_root, str(self.git_binary), "364", 40, 999.0)
        self.assertEqual(head, oid.decode())
        self.assertEqual(branch, "task/364-inspect")

    def test_nonzero_completed_git_child_is_not_killed_by_stale_pid(self) -> None:
        spec = importlib.util.spec_from_file_location("safe_git_exit_fixture", READER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # The source reads its own 'root' from the caller, but an arbitrary
        # directory is not a Git repository. Git exits nonzero after wait().
        with mock.patch.object(module.os, "killpg", side_effect=AssertionError("reaped PID reused")) as kill:
            with self.assertRaises(module.InspectionError):
                module._git(self.home, str(self.git_binary), "rev-parse", "--verify", "HEAD")
            kill.assert_not_called()

    def test_exited_git_leader_with_pipe_holding_descendant_is_reaped(self) -> None:
        """Confirm fork, exited group leader, and terminated pipe-holding child."""
        spec = importlib.util.spec_from_file_location("safe_git_group_fixture", READER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        script = self.home / "fixture-git-child"
        child_marker = self.home / "pipe-holder.pid"
        # Forked child inherits both stdout/stderr pipes; its parent writes
        # the descendant PID and exits without waiting. This is POSIX/Linux.
        script.write_text(
            "#!" + sys.executable + "\n"
            "import os, time\n"
            "pid = os.fork()\n"
            "if pid == 0:\n"
            "    time.sleep(4)\n"
            "    os._exit(0)\n"
            "with open(" + repr(str(child_marker)) + ", 'w') as handle:\n"
            "    handle.write(str(pid))\n"
            "os._exit(0)\n", encoding="utf-8",
        )
        script.chmod(0o700)
        original_killpg = os.killpg

        def terminate_unreaped_group(pgid: int, signum: int) -> None:
            # A zombie leader still owns its numeric PID/PGID, so signalling
            # the group cannot target a recycled identifier.
            leader_state = Path(f"/proc/{pgid}/stat").read_text().split()[2]
            self.assertEqual(leader_state, "Z")
            self.assertEqual(signum, signal.SIGKILL)
            # Inject cancellation while group cleanup is in progress. The
            # SIGTERM handler must never raise or skip the actual group kill.
            module._on_termination(signal.SIGTERM, None)
            original_killpg(pgid, signum)

        with mock.patch.object(module.os, "killpg",
                               side_effect=terminate_unreaped_group) as terminate_group:
            with self.assertRaises(module.InspectionError):
                module._git(
                    self.home, str(script), "rev-parse", "--verify", "HEAD",
                    deadline=time.monotonic() + 1.2,
                )
            self.assertEqual(terminate_group.call_count, 1)
            self.assertTrue(module._TERMINATION_REQUESTED)
        self.assertTrue(child_marker.is_file(), "Descendant never forked")
        child_pid = int(child_marker.read_text())
        for _ in range(60):
            status_path = Path(f"/proc/{child_pid}/stat")
            # The child can be reaped between a path existence check and
            # the read; missing /proc state means it has exited.
            try:
                state = status_path.read_text().split()[2]
            except (FileNotFoundError, ProcessLookupError):
                state = None
            if state in (None, "Z", "X"):
                break
            time.sleep(0.02)
        self.assertIn(state, (None, "Z", "X"), "Pipe-holding child survived SIGKILL")

    def test_sigterm_after_spawn_is_deferred_until_cleanup(self) -> None:
        """A SIGTERM in Popen's return window must not strand its child."""
        spec = importlib.util.spec_from_file_location("safe_git_signal_fixture", READER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        script = self.home / "cancellable-git-child"
        script.write_text(
            "#!" + sys.executable + "\n"
            "import time\n"
            "time.sleep(4)\n", encoding="utf-8",
        )
        script.chmod(0o700)
        real_spawn = subprocess.Popen

        def spawn_and_terminate(*args: object, **kwargs: object) -> subprocess.Popen:
            proc = real_spawn(*args, **kwargs)
            module._on_termination(signal.SIGTERM, None)
            return proc

        with mock.patch.object(module.subprocess, "Popen",
                               side_effect=spawn_and_terminate):
            with mock.patch.object(module.os, "killpg", wraps=os.killpg) as terminate:
                with self.assertRaisesRegex(module.InspectionError, "interrupted"):
                    module._git(self.home, str(script), "rev-parse", "--verify", "HEAD")
                self.assertEqual(terminate.call_count, 1)
        self.assertTrue(module._TERMINATION_REQUESTED)

    def test_sigterm_during_stdout_write_or_flush_returns_failure(self) -> None:
        """A cancelled response must never return a success exit status."""
        spec = importlib.util.spec_from_file_location("safe_git_stdout_fixture", READER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        old_handler = signal.getsignal(signal.SIGTERM)
        try:
            for injection in ("write", "flush"):
                with self.subTest(injection=injection):
                    output = mock.Mock()
                    # argparse inspects sys.stdout.fileno() for color support.
                    output.fileno.return_value = 1
                    output.isatty.return_value = False
                    output.buffer.write.return_value = 64

                    def cancel_write(data: bytes) -> int:
                        module._on_termination(signal.SIGTERM, None)
                        return len(data)

                    def cancel_flush() -> None:
                        module._on_termination(signal.SIGTERM, None)

                    if injection == "write":
                        output.buffer.write.side_effect = cancel_write
                    else:
                        output.buffer.flush.side_effect = cancel_flush

                    with mock.patch.object(module, "inspect", return_value={
                        "schema_version": 1, "phase": "A", "task": "364",
                        "operation": "identity", "qualification": "PROTOTYPE_ONLY",
                    }), mock.patch.dict(os.environ, {
                        "KAGARI_GIT_INSPECT_ROOT": str(self.task_root),
                        "KAGARI_GIT_INSPECT_BINARY": str(self.git_binary),
                    }), mock.patch.object(module.sys, "stdout", output):
                        self.assertEqual(module.main(["364", "identity"]), 2)
                    self.assertTrue(module._TERMINATION_REQUESTED)
                    output.buffer.write.assert_called_once()
                    output.buffer.flush.assert_called_once()
        finally:
            signal.signal(signal.SIGTERM, old_handler)

    def test_index_meta_rejects_unmerged_and_gitlink_records(self) -> None:
        """Index stage and object mode are independent fail-closed inputs."""
        spec = importlib.util.spec_from_file_location("safe_git_index_fixture", READER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        real_git = module._git
        original = Path.cwd()
        try:
            os.chdir(self.task_root)
            for label, data in (
                ("unmerged", b"100644 " + b"0" * 40 + b" 1\tconflict.txt\0"),
                ("gitlink", b"160000 " + b"0" * 40 + b" 0\tnested.git\0"),
            ):
                def substituted(root: Path, binary: str, *argv: str,
                                deadline: float | None = None) -> bytes:
                    if argv == ("ls-files", "--stage", "-z"):
                        return data
                    return real_git(root, binary, *argv, deadline=deadline)
                with self.subTest(label=label):
                    with mock.patch.object(module, "_git", side_effect=substituted):
                        with self.assertRaisesRegex(
                            module.InspectionError,
                            "Non-regular or unmerged index entry",
                        ):
                            module.inspect(self.task_root, "364", "index-meta",
                                           str(self.git_binary))
        finally:
            os.chdir(original)

    def test_conflicting_identity_marker_fails_closed(self) -> None:
        marker = self.task_root / ".task-state/task.md"
        original = marker.read_text()
        for prefix in ("- Task ID: 365", "- Branch: task/365-other",
                       "- Worktree: /other/location"):
            with self.subTest(prefix=prefix):
                marker.write_text(prefix + "\n" + original)
                self.assertNotEqual(self.cli("identity").returncode, 0)
        marker.write_text(original)

    def test_access_time_update_does_not_fake_mutation(self) -> None:
        marker = self.task_root / ".task-state/task.md"
        old_mtime = marker.stat().st_mtime
        os.utime(marker, (0, old_mtime))
        self.assertEqual(self.cli("identity").returncode, 0)

    def test_sha256_git_repository_identity(self) -> None:
        main = self.home / "sha256-main"
        main.mkdir()
        result = subprocess.run(
            [str(self.git_binary), "init", "-q", "-b", "main",
             "--object-format=sha256"], cwd=main, capture_output=True,
        )
        if result.returncode:
            self.skipTest("This Git does not support SHA-256 object-format fixtures")
        self.git("remote", "add", "origin", "https://github.com/acme/widgets.git", cwd=main)
        self.git("config", "user.name", "Fixture", cwd=main)
        self.git("config", "user.email", "fixture@example.test", cwd=main)
        (main / ".gitignore").write_text("/.task-state/\n")
        (main / "one.txt").write_text("hello\n")
        self.git("add", ".gitignore", "one.txt", cwd=main)
        self.git("commit", "-qm", "base", cwd=main)
        child = self.home / "sha256-task"
        self.git("worktree", "add", "-q", "-b", "fix/365-sha256",
                 str(child), "main", cwd=main)
        (child / ".task-state").mkdir()
        (child / ".task-state/task.md").write_text(
            "# Task\n- Task ID: 365\n- Branch: fix/365-sha256\n"
            "- Worktree: " + str(child) + "\n")
        for operation in ("identity", "index-meta"):
            result = self.cli(operation, task="365", root=child, cwd=child)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["object_format"], "sha256")
            self.assertEqual(len(data["head"]), 64)

    def test_stage_conflict_nonregular_symlink_refused(self) -> None:
        new = self.task_root / "symbolic-link"
        new.symlink_to("readme.txt")
        self.git("add", "symbolic-link")
        self.assertNotEqual(self.cli("index-meta").returncode, 0)

    def test_index_mutation_between_reads_fails_closed(self) -> None:
        """Deterministically interleave an actual fixture index write."""
        spec = importlib.util.spec_from_file_location("git_inspect_race_fixture", READER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original_git = module._git
        changed = False

        def interleave(root: Path, binary: str, *args: str,
                       deadline: float | None = None) -> bytes:
            nonlocal changed
            output = original_git(root, binary, *args, deadline=deadline)
            if args == ("ls-files", "--stage", "-z") and not changed:
                changed = True
                (root / "readme.txt").write_text("concurrent mutation\n")
                self.git("add", "readme.txt", cwd=root)
            return output

        old = Path.cwd()
        try:
            os.chdir(self.task_root)
            with mock.patch.object(module, "_git", side_effect=interleave):
                with self.assertRaisesRegex(module.InspectionError, "Index changed"):
                    module.inspect(self.task_root, "364", "index-meta",
                                   str(self.git_binary))
        finally:
            os.chdir(old)
        self.assertTrue(changed)

    def test_no_arbitrary_operation_or_root_argument(self) -> None:
        self.assertNotEqual(self.cli("diff").returncode, 0)
        self.assertNotEqual(self.cli("whitespace").returncode, 0)
        self.assertNotEqual(self.cli("show").returncode, 0)
        run = subprocess.run(
            [sys.executable, "-I", "-B", str(READER), "364", "identity", "--root", "/"],
            cwd=self.task_root, capture_output=True, text=True, check=False
        )
        self.assertNotEqual(run.returncode, 0)

    def test_native_tool_ancestor_guard_is_source_bound(self) -> None:
        """CI-visible guard contract without unreviewed host plugin dependencies."""
        native = TOOL.read_text(encoding="utf-8")
        self.assertIn("function checkedWorkspaceAncestors(root: string): void", native)
        self.assertIn("Task root has an untrusted ancestor directory", native)
        self.assertIn("checkedWorkspaceAncestors(root);", native)
        self.assertLess(
            native.index("checkedWorkspaceAncestors(root);"),
            native.index('const script = join(components[1], "safe_git_inspect.py");'),
        )
        for layout in LAYOUTS:
            self.assertEqual(
                (ROOT / "templates" / layout / ".opencode/tool/safe_git_inspect.ts").read_bytes(),
                TOOL.read_bytes(),
            )

    def test_native_source_only_unactivated_template_parity(self) -> None:
        for rel in (".automation/bin/safe_git_inspect.py",
                    ".opencode/tool/safe_git_inspect.ts"):
            source = (ROOT / "components/agent-core" / rel).read_bytes()
            for layout in LAYOUTS:
                with self.subTest(template=layout, path=rel):
                    self.assertEqual((ROOT / "templates" / layout / rel).read_bytes(), source)
        native = TOOL.read_text()
        self.assertIn("context.worktree", native)
        self.assertIn("shell: false", native)
        self.assertIn("PROTOTYPE_ONLY", native)
        for role in (ROOT / "components/agent-core/.opencode/agents").glob("*.md"):
            self.assertNotIn("safe_git_inspect: allow", role.read_text())
        for source_root in (ROOT / "components/agent-core", *(
            ROOT / "templates" / name for name in LAYOUTS
        )):
            config = json.loads((source_root / "opencode.json").read_text())
            self.assertEqual(config["permission"]["safe_git_inspect"], "deny")
        self.assertIn('HOST_CONFIG = "/etc/kagari/safe-git-inspection-host.json"', native)
        self.assertNotIn("Bun.which", native)
        self.assertIn("function checkedAncestorDirectories(path: string): void", native)
        self.assertIn("checkedAncestorDirectories(HOST_CONFIG)", native)
        self.assertIn("checkedAncestorDirectories(path)", native)
        self.assertIn("function checkedWorkspaceAncestors(root: string): void", native)
        self.assertIn("checkedWorkspaceAncestors(root)", native)
        self.assertIn("hostOwned(current, true)", native)
        self.assertIn("entry.sha256", native)


if __name__ == "__main__":
    unittest.main()
