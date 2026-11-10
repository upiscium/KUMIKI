"""#393 W1: one headless Claude Task Orchestrator in a registered Issue worktree.

Host-only component for ADR-0006 D3.1/D4/D4a. It is not wired to KAGARI Main
(W4) and holds no permission logic: every decision that no rule resolves goes
to the caller's ``--permission-prompt-tool`` server (W2).
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time
from typing import Any, Mapping

MINIMUM_CLI_VERSION = (2, 1, 293)
ENV_INHERITED = ("HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "TERM", "TMPDIR")
ENV_FIXED = {
    "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1",
    "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
}
# Project settings carry CLAUDE.md/AGENTS.md loading and local carries
# CLAUDE.local.md, so user settings (and with them user and synced plugins) are
# the only source that can be left out.
SETTING_SOURCES = "project,local"
READ_ONLY_SUBAGENTS = frozenset(("Explore", "Plan"))
DENIED_BUILTIN_SUBAGENTS = ("general-purpose", "claude", "statusline-setup",
                            "claude-code-guide", "web-fetch", "fork")
READ_ONLY_TOOLS = frozenset(("Read", "Glob", "Grep"))
# ask outranks allow from every scope, so a Project allow rule for these tools
# still reaches the permission gate instead of being resolved by Claude.
GATED_TOOLS = ("Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch")
PROJECT_INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md")
MAX_STREAM_BYTES = 256 * 1024 * 1024
VERSION_TIMEOUT = 30.0
DRAIN_SECONDS = 2.0
TERMINATE_SECONDS = 5.0


class LaunchError(Exception):
    """Refused before the Claude process started."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PluginAllowance:
    """One reviewed plugin: ``source`` as init reports it, e.g. ``name@inline``."""

    source: str
    directory: Path


@dataclass(frozen=True)
class TaskLaunch:
    executable: Path
    worktree: Path
    evidence_dir: Path
    claude_config_dir: Path
    prompt: str
    task_rules: str
    mcp_config: Path
    permission_prompt_tool: str
    model: str | None = None
    plugin_allowlist: tuple[PluginAllowance, ...] = ()
    timeout: float = 3600.0


def _exact_absolute(path: Path, code: str) -> None:
    if not path.is_absolute() or path.resolve(strict=False) != path:
        raise LaunchError(code, str(path))


def _strip_code(text: str) -> str:
    text = re.sub(r"^(```|~~~).*?^\1[^\n]*$", "", text, flags=re.M | re.S)
    return re.sub(r"`[^`\n]*`", "", text)


def _imports_agents(text: str) -> bool:
    return re.search(r"(?<![\w@/.])@(?:\./)?AGENTS\.md(?![\w.-])", _strip_code(text)) is not None


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", "replace")


def diagnose_instruction_files(worktree: Path) -> dict[str, Any]:
    """Classify which instruction files Claude reads from ``worktree`` and above."""
    found: list[dict[str, Any]] = []
    for directory in (worktree, *worktree.parents):
        for name in PROJECT_INSTRUCTION_FILES:
            path = directory / name
            if path.is_file():
                found.append({
                    "name": name, "path": str(path),
                    "scope": "worktree" if directory == worktree else "ancestor",
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                })
    names = {entry["name"] for entry in found}
    claude_files = [entry for entry in found if entry["name"] != "AGENTS.md"]
    imported = any(_imports_agents(_read_text(Path(entry["path"]))) for entry in claude_files)
    if imported:
        configuration = "claude_imports_agents"
    elif "CLAUDE.local.md" in names:
        configuration = "claude_local_present"
    elif claude_files:
        configuration = "claude_only"
    elif "AGENTS.md" in names:
        configuration = "agents_only"
    else:
        configuration = "none"
    return {
        "configuration": configuration,
        "agents_md_effective": "AGENTS.md" in names and (imported or not claude_files),
        "claude_local_present": "CLAUDE.local.md" in names,
        "files": found,
    }


def project_file_snapshot(worktree: Path) -> dict[str, str | None]:
    return {
        name: hashlib.sha256((worktree / name).read_bytes()).hexdigest()
        if (worktree / name).is_file() else None
        for name in PROJECT_INSTRUCTION_FILES
    }


def _read_only_rule(rule: Any) -> bool:
    return isinstance(rule, str) and rule.split("(", 1)[0] in READ_ONLY_TOOLS


def diagnose_project_settings(worktree: Path, main_checkout: Path | None) -> dict[str, Any]:
    """Record Project settings; they stay the Project's own and never block a launch.

    In a worktree Claude reads ``settings.local.json`` from the main checkout,
    so that file is recorded too.
    """
    report: dict[str, Any] = {}
    candidates = [("worktree/.claude/settings.json", worktree / ".claude/settings.json"),
                  ("worktree/.claude/settings.local.json", worktree / ".claude/settings.local.json")]
    if main_checkout is not None and main_checkout != worktree:
        candidates.append(("main/.claude/settings.local.json", main_checkout / ".claude/settings.local.json"))
    for name, path in candidates:
        if not path.is_file():
            continue
        try:
            settings = json.loads(path.read_bytes())
        except ValueError:
            settings = None
        if not isinstance(settings, dict):
            report[name] = {"valid": False}
            continue
        permissions = settings.get("permissions")
        allow = permissions.get("allow") if isinstance(permissions, dict) else None
        env = settings.get("env")
        report[name] = {
            "valid": True, "path": str(path), "keys": sorted(settings),
            "non_read_only_allow": [rule for rule in allow if not _read_only_rule(rule)]
            if isinstance(allow, list) else [],
            "env_names": sorted(map(str, env)) if isinstance(env, dict) else [],
        }
    return report


def project_subagent_names(worktree: Path) -> list[str]:
    names = set()
    root = worktree / ".claude" / "agents"
    for path in sorted(root.rglob("*.md")) if root.is_dir() else ():
        names.add(path.stem)
        match = re.search(r"^name:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", _read_text(path), re.M)
        if match:
            names.add(match.group(1))
    return sorted(names)


def build_settings(project_agents: list[str]) -> dict[str, Any]:
    denied = [*DENIED_BUILTIN_SUBAGENTS, *project_agents]
    # Command-line settings outrank Project settings, so their env keeps the
    # fixed values even when a Project settings file sets the same names.
    return {"env": dict(ENV_FIXED), "permissions": {
        "allow": [], "ask": list(GATED_TOOLS), "deny": [f"Agent({name})" for name in denied]}}


def build_environment(parent: Mapping[str, str], claude_config_dir: Path) -> dict[str, str]:
    environment = {name: parent[name] for name in ENV_INHERITED if name in parent}
    environment["CLAUDE_CONFIG_DIR"] = str(claude_config_dir)
    environment.update(ENV_FIXED)
    return environment


def build_argv(request: TaskLaunch, rules_path: Path, settings: Mapping[str, Any]) -> list[str]:
    argv = [
        str(request.executable), "-p", "--output-format", "stream-json", "--verbose",
        "--permission-mode", "manual", "--no-session-persistence",
        f"--setting-sources={SETTING_SOURCES}",
        f"--settings={json.dumps(settings, sort_keys=True)}",
        "--strict-mcp-config", f"--mcp-config={request.mcp_config}",
        f"--permission-prompt-tool={request.permission_prompt_tool}",
        f"--append-system-prompt-file={rules_path}",
    ]
    if request.model is not None:
        argv.append(f"--model={request.model}")
    argv += [f"--plugin-dir={item.directory}" for item in request.plugin_allowlist]
    return argv


class StreamMonitor:
    """Parse stream-json as it arrives; the first violation stops the session."""

    def __init__(self, allowed_plugins: frozenset[str], denied_subagents: frozenset[str],
                 mcp_servers: frozenset[str]) -> None:
        self.allowed_plugins = allowed_plugins
        self.denied_subagents = denied_subagents
        self.mcp_servers = mcp_servers
        self.init: dict[str, Any] | None = None
        self.last_result: dict[str, Any] | None = None
        self.result_count = 0
        self.malformed_lines = 0
        self.agent_attempts: dict[str, dict[str, Any]] = {}
        self.violations: list[dict[str, Any]] = []

    @property
    def violation(self) -> str | None:
        return self.violations[0]["code"] if self.violations else None

    def _violate(self, code: str, **detail: Any) -> None:
        self.violations.append({"code": code, **detail})

    def feed(self, line: bytes) -> None:
        if not line.strip():
            return
        try:
            event = json.loads(line)
        except ValueError:
            self.malformed_lines += 1
            return
        if not isinstance(event, dict):
            self.malformed_lines += 1
            return
        kind, subtype = event.get("type"), event.get("subtype")
        if kind == "system" and subtype == "init" and self.init is None:
            self.init = event
            self._check_init(event)
        elif kind == "system" and subtype == "background_tasks_changed":
            self._violate("BACKGROUND_SUBAGENT_STARTED", event=subtype)
        elif kind == "result":
            self.result_count += 1
            self.last_result = event
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else ():
            if isinstance(block, dict):
                self._observe_block(block, event.get("parent_tool_use_id"))

    def _check_init(self, event: Mapping[str, Any]) -> None:
        plugins = event.get("plugins")
        if not isinstance(plugins, list):
            self._violate("PLUGINS_UNVERIFIABLE")
        else:
            loaded = {_plugin_source(item) for item in plugins if not _builtin_plugin(item)}
            extra, missing = loaded - self.allowed_plugins, self.allowed_plugins - loaded
            if extra:
                self._violate("PLUGIN_NOT_ALLOWED", plugins=sorted(map(str, extra)))
            if missing:
                self._violate("PLUGIN_ALLOWLIST_NOT_LOADED", plugins=sorted(missing))
        agents = event.get("agents")
        allowed = READ_ONLY_SUBAGENTS | self.denied_subagents
        unexpected = (sorted({str(name) for name in agents} - allowed)
                      if isinstance(agents, list) else None)
        if unexpected is None:
            self._violate("SUBAGENTS_UNVERIFIABLE")
        elif unexpected:
            self._violate("UNEXPECTED_SUBAGENT", agents=unexpected)
        servers = event.get("mcp_servers")
        entries = [item for item in servers if isinstance(item, dict)] if isinstance(servers, list) else []
        connected = {item.get("name") for item in entries if item.get("status") == "connected"}
        listed = {item.get("name") for item in entries}
        if connected != self.mcp_servers or listed != self.mcp_servers:
            self._violate("MCP_SERVERS_MISMATCH", servers=servers)

    def _observe_block(self, block: Mapping[str, Any], parent: Any) -> None:
        if block.get("type") == "tool_use" and block.get("name") in ("Agent", "Task"):
            tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
            self.agent_attempts[str(block.get("id"))] = {
                "tool_use_id": block.get("id"),
                "subagent_type": tool_input.get("subagent_type"),
                "run_in_background": tool_input.get("run_in_background") is True,
                "parent_tool_use_id": parent, "executed": None, "background_launched": False,
            }
        elif block.get("type") == "tool_result":
            attempt = self.agent_attempts.get(str(block.get("tool_use_id")))
            if attempt is not None:
                self._observe_agent_result(attempt, block)

    def _observe_agent_result(self, attempt: dict[str, Any], block: Mapping[str, Any]) -> None:
        attempt["executed"] = block.get("is_error") is not True
        content = block.get("content")
        first = content if isinstance(content, str) else (
            content[0].get("text", "") if isinstance(content, list) and content
            and isinstance(content[0], dict) else "")
        if str(first).lstrip().startswith("Async agent launched"):
            attempt["background_launched"] = True
            self._violate("BACKGROUND_SUBAGENT_STARTED", tool_use_id=attempt["tool_use_id"])
        kind = attempt["subagent_type"]
        if attempt["executed"] and (kind is None or kind in self.denied_subagents):
            self._violate("DENIED_SUBAGENT_EXECUTED", subagent_type=kind)

    def summary(self) -> dict[str, Any]:
        init = self.init or {}
        last = self.last_result or {}
        plugins = [item for item in init.get("plugins") or [] if isinstance(item, dict)]
        return {
            "cli_version": init.get("claude_code_version"),
            "model": init.get("model"),
            "permission_mode": init.get("permissionMode"),
            "plugins": [_plugin_record(item) for item in plugins if not _builtin_plugin(item)],
            "builtin_plugins": [_plugin_record(item) for item in plugins if _builtin_plugin(item)],
            "plugin_errors": init.get("plugin_errors"),
            "agents": init.get("agents"),
            "mcp_servers": init.get("mcp_servers"),
            "skills": init.get("skills"),
            "result_count": self.result_count,
            "final_result": {key: last.get(key) for key in (
                "subtype", "is_error", "api_error_status", "num_turns", "duration_ms",
            )} if self.last_result is not None else None,
            "model_usage": sorted(last.get("modelUsage") or []),
            "malformed_lines": self.malformed_lines,
            "agent_attempts": list(self.agent_attempts.values()),
            "violations": self.violations,
        }


def _builtin_plugin(item: Any) -> bool:
    return isinstance(item, dict) and item.get("path") == "builtin" and str(item.get("source", "")).endswith("@builtin")


def _plugin_source(item: Any) -> str:
    return str((item.get("source") or item.get("name")) if isinstance(item, dict) else item)


def _plugin_record(item: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item.get(key) for key in ("name", "source", "version")}


def _create_private(path: Path) -> Any:
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")


def _write_json(path: Path, value: Any) -> None:
    with _create_private(path) as sink:
        sink.write(json.dumps(value, indent=1, ensure_ascii=False, sort_keys=True).encode() + b"\n")


def _kill_group(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _stop_process(process: subprocess.Popen[bytes]) -> int:
    _kill_group(process.pid, signal.SIGTERM)
    try:
        process.wait(TERMINATE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    _kill_group(process.pid, signal.SIGKILL)
    return process.wait()


def _supervise(request: TaskLaunch, argv: list[str], environment: Mapping[str, str],
               monitor: StreamMonitor) -> dict[str, Any]:
    evidence = request.evidence_dir
    digest = hashlib.sha256()
    total = 0
    pending = b""
    stop: str | None = None
    with open(evidence / "prompt.txt", "rb") as stdin, _create_private(evidence / "stream.jsonl") as sink, \
            _create_private(evidence / "stderr.log") as stderr:
        process = subprocess.Popen(
            argv, cwd=request.worktree, env=dict(environment), stdin=stdin,
            stdout=subprocess.PIPE, stderr=stderr, shell=False, close_fds=True, start_new_session=True,
        )
        assert process.stdout is not None
        descriptor = process.stdout.fileno()
        os.set_blocking(descriptor, False)
        selector = selectors.DefaultSelector()
        selector.register(descriptor, selectors.EVENT_READ)
        deadline = time.monotonic() + request.timeout
        exited_at: float | None = None
        eof = False
        try:
            while stop is None:
                now = time.monotonic()
                if monitor.violation is not None:
                    stop = monitor.violation
                elif now >= deadline:
                    stop = "TIMEOUT"
                elif total > MAX_STREAM_BYTES:
                    stop = "STREAM_LIMIT"
                if stop is not None:
                    break
                if process.poll() is not None:
                    exited_at = exited_at if exited_at is not None else now
                    if eof or now - exited_at >= DRAIN_SECONDS:
                        break
                if eof:
                    time.sleep(0.02)
                    continue
                if not selector.select(0.05):
                    continue
                try:
                    chunk = os.read(descriptor, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    eof = True
                    selector.unregister(descriptor)
                    continue
                sink.write(chunk)
                sink.flush()
                digest.update(chunk)
                total += len(chunk)
                *lines, pending = (pending + chunk).split(b"\n")
                for line in lines:
                    monitor.feed(line)
        except BaseException:
            _stop_process(process)
            raise
        else:
            returncode = _stop_process(process)
        finally:
            selector.close()
            process.stdout.close()
    if stop is None and pending:
        monitor.feed(pending)
    if stop is None and monitor.violation is not None:
        stop = monitor.violation
    return {"stop": stop, "returncode": returncode, "stream_bytes": total, "stream_sha256": digest.hexdigest()}


def judge_outcome(run: Mapping[str, Any], monitor: StreamMonitor, instructions_unchanged: bool) -> tuple[str, str]:
    """COMPLETED needs a clean exit and a successful *last* result event."""
    last = monitor.last_result
    if run["stop"] is not None:
        reason = run["stop"]
    elif not instructions_unchanged:
        reason = "PROJECT_INSTRUCTIONS_CHANGED"
    elif monitor.init is None:
        reason = "NO_INIT_EVENT"
    elif monitor.malformed_lines:
        reason = "MALFORMED_STREAM"
    elif last is None:
        reason = "NO_RESULT_EVENT"
    elif last.get("is_error") is not False or last.get("subtype") != "success":
        reason = "RESULT_ERROR"
    elif run["returncode"] != 0:
        reason = "PROCESS_EXIT_NONZERO"
    else:
        return "COMPLETED", "RESULT_SUCCESS"
    return "FAILED", reason


def _cli_version(request: TaskLaunch, environment: Mapping[str, str]) -> str:
    try:
        output = subprocess.run(
            [str(request.executable), "--version"], cwd=request.worktree, env=dict(environment),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=VERSION_TIMEOUT, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise LaunchError("CLI_VERSION_UNAVAILABLE", type(exc).__name__) from exc
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", output)
    if match is None or tuple(map(int, match.groups())) < MINIMUM_CLI_VERSION:
        raise LaunchError("CLI_VERSION_UNSUPPORTED", output)
    return output


def _check_registered_worktree(worktree: Path, environment: Mapping[str, str]) -> Path | None:
    """Return the main checkout, or None for a bare repository."""
    if not (worktree / ".git").is_file():
        raise LaunchError("WORKTREE_NOT_LINKED", str(worktree))
    try:
        listing = subprocess.run(
            ["git", "-C", str(worktree), "worktree", "list", "--porcelain"], env=dict(environment),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=VERSION_TIMEOUT, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise LaunchError("WORKTREE_NOT_REGISTERED", type(exc).__name__) from exc
    lines = listing.splitlines()
    if f"worktree {worktree}" not in lines:
        raise LaunchError("WORKTREE_NOT_REGISTERED", str(worktree))
    # The first porcelain block is the main worktree.
    main_block = listing.split("\n\n", 1)[0].splitlines()
    if "bare" in main_block or not main_block[0].startswith("worktree "):
        return None
    return Path(main_block[0].removeprefix("worktree "))


def _mcp_servers(request: TaskLaunch) -> frozenset[str]:
    try:
        servers = json.loads(request.mcp_config.read_bytes())["mcpServers"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LaunchError("MCP_CONFIG_INVALID", str(request.mcp_config)) from exc
    if not isinstance(servers, dict) or not servers:
        raise LaunchError("MCP_CONFIG_INVALID", str(request.mcp_config))
    if not any(request.permission_prompt_tool.startswith(f"mcp__{name}__") for name in servers):
        raise LaunchError("PERMISSION_TOOL_NOT_CONFIGURED", request.permission_prompt_tool)
    return frozenset(servers)


def launch_task(request: TaskLaunch, parent_environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Run one session to completion and return the outcome also saved as Evidence."""
    for path, code in ((request.executable, "EXECUTABLE_INVALID"), (request.worktree, "WORKTREE_INVALID"),
                       (request.evidence_dir, "EVIDENCE_DIR_INVALID"), (request.claude_config_dir, "PROFILE_INVALID"),
                       (request.mcp_config, "MCP_CONFIG_INVALID")):
        _exact_absolute(path, code)
    if not request.worktree.is_dir():
        raise LaunchError("WORKTREE_INVALID", str(request.worktree))
    if request.evidence_dir.is_relative_to(request.worktree):
        raise LaunchError("EVIDENCE_DIR_INVALID", "inside worktree")
    if not request.claude_config_dir.is_dir():
        raise LaunchError("PROFILE_INVALID", str(request.claude_config_dir))
    if request.timeout <= 0:
        raise LaunchError("TIMEOUT_INVALID", str(request.timeout))
    parent = os.environ if parent_environment is None else parent_environment
    environment = build_environment(parent, request.claude_config_dir)
    main_checkout = _check_registered_worktree(request.worktree, environment)
    mcp_servers = _mcp_servers(request)
    cli_version = _cli_version(request, environment)
    project_agents = project_subagent_names(request.worktree)
    settings = build_settings(project_agents)
    instructions = diagnose_instruction_files(request.worktree)
    before = project_file_snapshot(request.worktree)
    try:
        request.evidence_dir.mkdir(mode=0o700)
    except OSError as exc:
        raise LaunchError("EVIDENCE_DIR_INVALID", str(exc)) from exc
    rules_path = request.evidence_dir / "task-rules.md"
    with _create_private(rules_path) as sink:
        sink.write(request.task_rules.encode())
    with _create_private(request.evidence_dir / "prompt.txt") as sink:
        sink.write(request.prompt.encode())
    argv = build_argv(request, rules_path, settings)
    _write_json(request.evidence_dir / "launch.json", {
        "schema": "kagari.claude-task-launch.v1",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cli_version": cli_version, "argv": argv, "cwd": str(request.worktree),
        "environment": {
            "names": sorted(environment),
            "fixed": {name: environment[name] for name in sorted(ENV_FIXED)},
            "claude_config_dir": environment["CLAUDE_CONFIG_DIR"],
            "removed_parent_names": sorted(
                name for name in parent if name not in environment
                and name.startswith(("CLAUDE", "ANTHROPIC"))),
        },
        "instructions": instructions, "project_files_before": before,
        "project_settings": diagnose_project_settings(request.worktree, main_checkout),
        "plugin_allowlist": [item.source for item in request.plugin_allowlist],
        "denied_subagents": [*DENIED_BUILTIN_SUBAGENTS, *project_agents],
        "mcp_servers": sorted(mcp_servers),
        "permission_prompt_tool": request.permission_prompt_tool,
    })
    monitor = StreamMonitor(
        frozenset(item.source for item in request.plugin_allowlist),
        frozenset((*DENIED_BUILTIN_SUBAGENTS, *project_agents)), mcp_servers,
    )
    run = _supervise(request, argv, environment, monitor)
    after = project_file_snapshot(request.worktree)
    status, reason = judge_outcome(run, monitor, after == before)
    outcome = {
        "schema": "kagari.claude-task-outcome.v1", "status": status, "reason": reason,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **run, "project_files_after": after, "project_files_unchanged": after == before,
        **monitor.summary(),
    }
    _write_json(request.evidence_dir / "outcome.json", outcome)
    return outcome
