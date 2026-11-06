"""R6A private Claude transport fake-process regressions; no model account used."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import os
import shlex
import sys
import tempfile
import unittest
import uuid
from unittest import mock
from unittest.mock import patch

from tools import kagari_vnext_claude_lane_b as lane
from tools import kagari_vnext_claude_preflight as preflight
from tools import kagari_vnext_claude_transport as transport


class PrivateClaudeTransportTests(unittest.TestCase):
    def test_fake_process_and_fail_closed_inputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kumiki-r6a-") as temporary:
            root = Path(temporary).resolve(strict=True)
            profile = root / "personal"
            profile.mkdir(mode=0o700)
            binary = root / "fake-claude"
            binary.write_text("#!/bin/sh\nprintf 'fake-output\\n'\n")
            binary.chmod(0o700)
            invocation = lane.FixtureInvocation(
                binary, lane._argv(str(uuid.uuid4())), root,
                (("HOME", str(root)), ("PATH", "/usr/bin"),
                 ("CLAUDE_CONFIG_DIR", str(profile))), 5.0)
            actual = transport._run_bounded_transport(invocation)
            self.assertEqual(0, actual["returncode"])
            self.assertEqual(b"fake-output\n", actual["stdout"])
            self.assertEqual("UNQUALIFIED", lane.dispatch()["status"])
            linked = root / "linked"
            linked.symlink_to(binary)
            failures = (
                replace(invocation, argv=("--dangerously-skip-permissions",)),
                replace(invocation, argv=invocation.argv + ("--dangerously-skip-permissions",)),
                replace(invocation, timeout=-1),
                replace(invocation, timeout=True),
                replace(invocation, timeout=float("nan")),
                replace(invocation, output_limit=lane.MAX_CAPTURE + 1),
                replace(invocation, executable=linked),
                replace(invocation, executable=root / "missing-executable"),
                replace(invocation, cwd=Path(".")),
                replace(invocation, environment=invocation.environment + (("ANTHROPIC_API_KEY", "invalid"),)),
                replace(invocation, environment=invocation.environment + (("LD_PRELOAD", "not-allowed"),)),
                replace(invocation, environment=invocation.environment + (("PATH", "extra"),)),
                replace(invocation, environment=(("CLAUDE_CONFIG_DIR", str(root / "missing")),)),
            )
            for invalid in failures:
                with self.subTest(invalid=invalid):
                    with patch.object(preflight, "_run_bounded",
                                      side_effect=AssertionError("must not start")):
                        result = transport._run_bounded_transport(invalid)
                    self.assertEqual(-1, result["returncode"])
                    self.assertIs(True, result["io_error"])


    def test_child_receives_exact_argv_cwd_and_allowlisted_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kumiki-r6a-") as temporary:
            root = Path(temporary).resolve(strict=True)
            profile = root / "personal"
            profile.mkdir(mode=0o700)
            executable = root / "fake-claude"
            executable.write_text(
                "#!/bin/sh\n"
                "pwd; printf '%s\\n' \"$HOME\"; "
                "printf '%s\\n' \"$CLAUDE_CONFIG_DIR\"; "
                "printf '%s\\n' \"$1\"; printf '%s\\n' \"$2\"; "
                "printf '%s\\n' \"${KAGARI_R6A_NOT_ALLOWED:-UNSET}\"\n"
            )
            executable.chmod(0o700)
            env = (
                ("HOME", str(root)), ("PATH", "/usr/bin"),
                ("CLAUDE_CONFIG_DIR", str(profile)),
            )
            invocation = lane.FixtureInvocation(
                executable, lane._argv(str(uuid.uuid4())), root, env, 5.0
            )
            with mock.patch.dict(
                os.environ, {"KAGARI_R6A_NOT_ALLOWED": "LEAKED"}
            ):
                actual = transport._run_bounded_transport(invocation)
            self.assertEqual(0, actual["returncode"])
            self.assertEqual(
                actual["stdout"].decode().splitlines(),
                [str(root), str(root), str(profile), "-p", lane.FIXED_PROMPT, "UNSET"],
            )
            self.assertEqual(b"", actual["stderr"])
            self.assertIs(False, actual["io_error"])
            self.assertEqual("UNQUALIFIED", lane.dispatch()["status"])


    def test_bounded_child_overflow_timeout_and_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kumiki-r6a-") as temporary:
            root = Path(temporary).resolve(strict=True)
            profile = root / "profile"
            profile.mkdir(mode=0o700)
            executable = root / "fake-claude"
            env = (
                ("HOME", str(root)), ("PATH", "/usr/bin"),
                ("CLAUDE_CONFIG_DIR", str(profile)),
            )

            def run(source: str, seconds: float = 3.0):
                executable.write_text(
                    f"#!/bin/sh\nexec {shlex.quote(sys.executable)}"
                    f" -c {shlex.quote(source)}\n"
                )
                executable.chmod(0o700)
                inv = lane.FixtureInvocation(
                    executable, lane._argv(str(uuid.uuid4())), root, env, seconds
                )
                return transport._run_bounded_transport(inv)

            overflow = run('import sys;sys.stdout.buffer.write(b"x"*70000)')
            self.assertIs(True, overflow["overflow"])
            self.assertEqual(-1, overflow["returncode"])
            self.assertIs(False, overflow["io_error"])
            timeout = run("import time;time.sleep(2)", seconds=0.1)
            self.assertIs(True, timeout["timed_out"])
            self.assertEqual(-1, timeout["returncode"])
            self.assertIs(False, timeout["io_error"])
            nonzero = run('import sys;sys.stderr.write("expected");sys.exit(7)')
            self.assertEqual(7, nonzero["returncode"])
            self.assertEqual(b"expected", nonzero["stderr"])
            self.assertIs(False, nonzero["io_error"])
            self.assertEqual("UNQUALIFIED", lane.dispatch()["status"])


if __name__ == "__main__":
    unittest.main()
