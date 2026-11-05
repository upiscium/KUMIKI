"""Private transport candidate only; never wired to KAGARI Main or public dispatch."""
from __future__ import annotations
import math
import os
from pathlib import Path
from typing import Any
from . import kagari_vnext_claude_lane_b as lane
from . import kagari_vnext_claude_preflight as preflight


def _refused() -> dict[str, Any]:
    return {"stdout": b"", "stderr": b"", "returncode": -1,
            "timed_out": False, "overflow": False, "io_error": True}


def _run_bounded_transport(invocation: lane.FixtureInvocation) -> dict[str, Any]:
    """Internal probe requiring a caller-validated exact, tool-free invocation."""
    if type(invocation) is not lane.FixtureInvocation:
        return _refused()
    try:
        if type(invocation.timeout) not in (int, float) or not math.isfinite(invocation.timeout) or not 0 < invocation.timeout <= 30: return _refused()
        executable, cwd = invocation.executable, invocation.cwd
        if (not isinstance(executable, Path) or not isinstance(cwd, Path)
                or not executable.is_absolute() or not cwd.is_absolute()
                or executable.resolve(strict=True) != executable
                or cwd.resolve(strict=True) != cwd
                or not executable.is_file() or not os.access(executable, os.X_OK)
                or not cwd.is_dir()):
            return _refused()
        args = invocation.argv
        sid = args[args.index("--session-id") + 1]
        if not lane._valid_session_id(sid) or args != lane._argv(sid):
            return _refused()
        env = dict(invocation.environment)
        allowed = {"HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL",
                   "TERM", "TMPDIR", "CLAUDE_CONFIG_DIR"}
        if len(env) != len(invocation.environment) or not set(env) <= allowed:
            return _refused()
        if not env.get("CLAUDE_CONFIG_DIR"):
            return _refused()
        profile = Path(env["CLAUDE_CONFIG_DIR"])
        if (not profile.is_absolute() or profile.resolve(strict=True) != profile
                or not profile.is_dir()
                or not all(isinstance(value, str) for value in env.values())):
            return _refused()
        if invocation.output_limit != lane.MAX_CAPTURE:
            return _refused()
        result = preflight._run_bounded(invocation.executable, args,
            cwd=invocation.cwd, env=env, timeout=invocation.timeout)
        return lane._validate_transport(result) or _refused()
    except Exception:
        return _refused()
