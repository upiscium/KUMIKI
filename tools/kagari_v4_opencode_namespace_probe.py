"""Disposable N2 mount-overlay diagnostic; never accepts a real consumer.

Run only --pure debug config, with normal Project discovery left enabled.
Unprivileged namespaces/OverlayFS must work; there is no unsafe fallback.
This is not a production sandbox, model runner, or qualified v4 launcher.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

STATES = ("absent", "empty", "custom", "existing_ignore", "malformed")
SOURCE_ROLE = "PROJECT_ROLE_SENTINEL"
RULE_PATH = "USER_RULE.md"
MAX_OUTPUT = 1024 * 1024
_SAFE_PATH = re.compile(r"/[A-Za-z0-9_./-]+\Z", re.ASCII)


class NamespaceProbeError(RuntimeError):
    """An experiment failed or the required isolation is unavailable."""


def tool(name: str) -> str:
    for base in ("/run/current-system/sw/bin", "/usr/bin", "/bin"):
        path = Path(base) / name
        try:
            meta = path.stat()
        except OSError:
            continue
        if (
            meta.st_uid == 0
            and stat.S_ISREG(meta.st_mode)
            and not meta.st_mode & 0o022
            and os.access(path, os.X_OK)
        ):
            return str(path)
    raise NamespaceProbeError(f"fixed root-owned tool unavailable: {name}")


def snapshot(root: Path) -> dict[str, str]:
    """Exact small fixture file inventory, including Git metadata/ignored files."""
    result = {}
    for p in sorted(root.rglob("*")):
        mode = p.lstat().st_mode
        key = p.relative_to(root).as_posix()
        if stat.S_ISREG(mode):
            result[key] = hashlib.sha256(p.read_bytes()).hexdigest()
        elif stat.S_ISDIR(mode):
            result[key + "/"] = "DIRECTORY"
        else:
            raise NamespaceProbeError("unexpected special fixture file")
    return result


def bounded_run(
    argv: list[str], env: dict[str, str], timeout: float
) -> tuple[int, bytes, bytes]:
    if not 0 < timeout <= 60:
        raise NamespaceProbeError("timeout outside (0, 60]")
    proc = subprocess.Popen(
        argv,
        cwd="/",
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": MAX_OUTPUT, "stderr": 64 * 1024}
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            assert proc.stdout is not None and proc.stderr is not None
            selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
            selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise NamespaceProbeError("diagnostic timeout")
                events = selector.select(remaining)
                if not events:
                    raise NamespaceProbeError("diagnostic timeout")
                for event, _ in events:
                    key = event.data
                    data = os.read(
                        event.fileobj.fileno(),
                        min(65536, limits[key] - len(buffers[key]) + 1),
                    )
                    if not data:
                        selector.unregister(event.fileobj)
                        continue
                    buffers[key].extend(data)
                    if len(buffers[key]) > limits[key]:
                        raise NamespaceProbeError(f"{key} byte limit exceeded")
        proc.wait(timeout=max(0.001, deadline - time.monotonic()))
        return proc.returncode, bytes(buffers["stdout"]), bytes(buffers["stderr"])
    except subprocess.TimeoutExpired as exc:
        raise NamespaceProbeError("diagnostic timeout") from exc
    finally:
        # A private PID namespace also kills its descendants when init exits.
        # Kill the process group even when a direct child already exited.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()


_CHILD_SCRIPT = r"""set -eu
project=$1
scratch=$2
opencode=$3
mount=$4
# Propagation is private; these mounts never appear in the host namespace.
"$mount" --bind "$project" "$project"
"$mount" -o remount,bind,ro "$project"
if test -d "$project/.opencode"; then
  "$mount" -t overlay overlay -o "lowerdir=$project/.opencode,upperdir=$scratch/upper,workdir=$scratch/work,userxattr" "$project/.opencode"
fi
readlink /proc/self/ns/mnt > "$scratch/child-mount-ns"
cd "$project"
pwd -P > "$scratch/child-cwd"
# The exact fixture product tree is read-only outside the scoped overlay.
if (printf unexpected > "$project/.namespace-write-canary") 2>/dev/null; then
  exit 86
fi
printf readonly > "$scratch/project-ro-proven"
exec "$opencode" --pure debug config
"""


def namespace_argv(project: Path, scratch: Path, executable: Path) -> list[str]:
    for p in (project, scratch):
        if (
            not p.is_absolute()
            or p.resolve(strict=True) != p
            or not p.is_dir()
            or _SAFE_PATH.fullmatch(str(p)) is None
        ):
            raise NamespaceProbeError("noncanonical or unsupported mount option path")
    if project == scratch or project in scratch.parents or scratch in project.parents:
        raise NamespaceProbeError("scratch and ProjectRoot must be independent")
    if not executable.is_absolute() or not executable.is_file():
        raise NamespaceProbeError("exact diagnostic executable required")
    return [
        tool("unshare"),
        "--user",
        "--map-root-user",
        "--mount",
        "--pid",
        "--fork",
        "--mount-proc",
        "--kill-child",
        "--propagation",
        "private",
        tool("sh"),
        "-c",
        _CHILD_SCRIPT,
        "kagari-n2-diagnostic",
        str(project),
        str(scratch),
        str(executable),
        tool("mount"),
    ]


def probe_case(
    executable: Path, state: str, isolated: bool = True, timeout: float = 25
) -> dict:
    """The only ProjectRoot used by this public entrypoint is a fresh fixture."""
    if state not in STATES:
        raise NamespaceProbeError("unknown fixture state")
    before_host_namespace = os.readlink("/proc/self/ns/mnt")
    with tempfile.TemporaryDirectory(prefix="kagari-n2-namespace-") as temp:
        root = Path(temp)
        project = root / "project"
        project.mkdir()
        scratch = root / "scratch"
        scratch.mkdir()
        subprocess.run(
            [tool("git"), "init", "-q", str(project)],
            check=True,
            capture_output=True,
            timeout=10,
        )
        for name in (
            "upper",
            "work",
            "home",
            "config",
            "data",
            "cache",
            "state",
            "external",
        ):
            (scratch / name).mkdir()
        (project / "AGENTS.md").write_text("PROJECT_AGENTS_BODY_NOT_MODEL_TESTED\n")
        (project / RULE_PATH).write_text("PROJECT_RELATIVE_RULE_BODY\n")
        (project / "README.md").write_text("Original Project product bytes\n")
        fixture_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        fixture_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

        def git(*args: str) -> str:
            child = subprocess.run(
                [
                    tool("git"),
                    "-C",
                    str(project),
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "commit.gpgSign=false",
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    *args,
                ],
                env=fixture_env,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            return child.stdout.strip()

        git("add", "README.md")
        git("commit", "-q", "-m", "Disposable preservation fixture")
        fixture_head = git("rev-parse", "HEAD")
        (project / "README.md").write_text("Valuable staged Project edit\n")
        git("add", "README.md")
        user = {
            "$schema": "https://opencode.ai/config.json",
            "instructions": [RULE_PATH],
            "agent": {
                "project-rule-marker": {"description": SOURCE_ROLE, "mode": "primary"}
            },
        }
        (project / "opencode.json").write_text(
            "{ malformed JSON" if state == "malformed" else json.dumps(user)
        )
        if state != "absent":
            d = project / ".opencode"
            d.mkdir()
            if state in ("custom", "existing_ignore", "malformed"):
                (d / "user.md").write_text("USER_OWNED_PROJECT_CONFIG\n")
                (d / "agents").mkdir()
                (d / "agents/project-directory-marker.md").write_text(
                    "---\ndescription: PROJECT_DIRECTORY_ROLE_SENTINEL\n"
                    "mode: primary\n---\nProject-owned role body, never executed.\n"
                )
            if state == "existing_ignore":
                (d / ".gitignore").write_text("# Existing user rules, do not replace\n")
        external = {
            "$schema": "https://opencode.ai/config.json",
            "username": "NAMESPACE_EXTERNAL",
        }
        (scratch / "external/opencode.json").write_text(json.dumps(external))
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("OPENCODE_", "GIT_", "XDG_"))
        }
        env.update(
            HOME=str(scratch / "home"),
            XDG_CONFIG_HOME=str(scratch / "config"),
            XDG_DATA_HOME=str(scratch / "data"),
            XDG_CACHE_HOME=str(scratch / "cache"),
            XDG_STATE_HOME=str(scratch / "state"),
            OPENCODE_CONFIG_DIR=str(scratch / "external"),
        )
        before = snapshot(project)
        if isolated:
            argv = namespace_argv(project, scratch, executable)
        else:
            # Positive-control mutation is allowed only inside this throwaway fixture.
            argv = [
                tool("sh"),
                "-c",
                'cd "$1"; exec "$2" --pure debug config',
                "control",
                str(project),
                str(executable),
            ]
        rc, stdout, stderr = bounded_run(argv, env, timeout)
        after = snapshot(project)
        changed = sorted(
            k for k in set(before) | set(after) if before.get(k) != after.get(k)
        )
        if isolated and changed:
            raise NamespaceProbeError(f"host ProjectRoot changed: {changed}")
        if state == "malformed":
            if rc == 0:
                raise NamespaceProbeError(
                    "malformed Project config was silently ignored"
                )
            config = {}
        else:
            if rc != 0:
                raise NamespaceProbeError(
                    f"namespace/debug command failed {rc}: {stderr[-500:]!r}"
                )
            config = json.loads(stdout)
            if (
                config.get("username") != "NAMESPACE_EXTERNAL"
                or config.get("agent", {})
                .get("project-rule-marker", {})
                .get("description")
                != SOURCE_ROLE
                or RULE_PATH not in config.get("instructions", [])
            ):
                raise NamespaceProbeError(
                    "normal Project configuration did not survive"
                )
        if state in ("custom", "existing_ignore"):
            marker = config.get("agent", {}).get("project-directory-marker", {})
            if marker.get("description") != "PROJECT_DIRECTORY_ROLE_SENTINEL":
                raise NamespaceProbeError("Project .opencode role discovery was lost")
        if git("rev-parse", "HEAD") != fixture_head:
            raise NamespaceProbeError("fixture Git HEAD changed")
        if before.get(".git/index") != after.get(".git/index"):
            raise NamespaceProbeError("fixture staged Git index changed")
        upper_ignore = (scratch / "upper/.gitignore").exists()
        if isolated:
            if (scratch / "child-cwd").read_text().strip() != str(project):
                raise NamespaceProbeError("ProjectRoot cwd changed")
            if (
                scratch / "child-mount-ns"
            ).read_text().strip() == before_host_namespace:
                raise NamespaceProbeError("mount namespace was not isolated")
            if not (scratch / "project-ro-proven").is_file():
                raise NamespaceProbeError("read-only Project mount not demonstrated")
            if state in ("empty", "custom") and not upper_ignore:
                raise NamespaceProbeError(
                    "auto-write was not observed in external upperdir"
                )
        elif not isolated:
            allowed_control_writes = {".git/opencode", ".opencode/.gitignore"}
            if set(changed) - allowed_control_writes:
                raise NamespaceProbeError(
                    f"positive control {state} had unexpected changes: {changed}"
                )
            if state in ("empty", "custom") and ".opencode/.gitignore" not in changed:
                raise NamespaceProbeError(
                    "positive control did not reproduce gitignore write"
                )
        result = {
            "state": state,
            "isolation": "mount_overlay" if isolated else "disposable_control",
            "status": "EXPECTED_CONFIG_BLOCK" if state == "malformed" else "PASS",
            "exit_code": rc,
            "project_changes": changed,
            "git_opencode_cache_created": ".git/opencode" in changed,
            "project_preserved": not changed,
            "git_head_preserved": True,
            "staged_index_preserved": True,
            "upper_gitignore_created": upper_ignore,
            "normal_discovery_enabled": True,
            "project_config_survived": bool(config),
            "nested_project_role_observed": state in ("custom", "existing_ignore"),
            "project_cwd_preserved": isolated,
            "model_instruction_delivery_proven": False,
            "model_calls": 0,
            "subagents": 0,
            "can_launch_runtime": False,
        }
    if root.exists() or os.readlink("/proc/self/ns/mnt") != before_host_namespace:
        raise NamespaceProbeError(
            "temporary directory or host namespace cleanup failed"
        )
    result["temporary_namespace_resources_cleaned"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2, choices=range(1, 5))
    args = parser.parse_args()
    cases = [(state, isolated) for isolated in (False, True) for state in STATES]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda pair: probe_case(args.executable, *pair), cases))
    print(
        json.dumps(
            {
                "schema_version": 1,
                "cases": results,
                "passed": len(results),
                "source_only": True,
                "runtime_qualified": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
