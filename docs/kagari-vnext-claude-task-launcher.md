# Headless Claude Task Orchestrator launcher (#393, ADR-0006 W1)

`tools/kagari_vnext_claude_task_launcher.py` starts one headless Claude Code
session in a registered Issue worktree and saves the stream as Evidence.
**Source component only:** KAGARI Main does not call it yet (W4), and it holds
no permission decisions (W2). `--resume`, MUSUBI and parallel Tasks are out of
scope.

## What one launch does

`launch_task(TaskLaunch, parent_environment)` refuses with `LaunchError` before
any process starts, or runs the session and returns the outcome.

| Concern | Behavior |
| --- | --- |
| Environment | Built from an allowlist (`HOME`, `USER`, `LOGNAME`, `PATH`, `LANG`, `LC_ALL`, `TERM`, `TMPDIR`) plus the requested `CLAUDE_CONFIG_DIR`. Always sets `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1` and `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, both in the process environment and in the `env` of `--settings`. A Project settings `env` overrides the process environment, but command-line settings outrank Project settings. |
| Setting sources | `--setting-sources=project,local`. `project` is what loads `AGENTS.md`/`CLAUDE.md`, and `local` is what loads `CLAUDE.local.md`; user settings, and with them user and synced plugins, are left out. |
| Project settings | Stay the Project's own and never block a launch, so KAGARI stays easy to attach and detach. The worktree's `.claude/settings.json` and `.claude/settings.local.json`, and the main checkout's `.claude/settings.local.json` (the one Claude reads in a worktree, even in an untrusted workspace), are recorded in `launch.json`: keys, `allow` rules outside `Read`, `Glob`, `Grep`, and `env` names. |
| Permissions | `allow` is empty. `ask` lists `Bash`, `Edit`, `Write`, `NotebookEdit`, `WebFetch`, `WebSearch`; ask outranks allow from every scope, so a Project `allow` rule for these tools still reaches `--permission-prompt-tool`. Project `deny` rules still apply first. Reads inside the worktree need no approval under `--permission-mode manual`. |
| Subagents | `Explore` and `Plan` stay available. Other built-ins and every `.claude/agents` definition are denied as `Agent(<name>)`. An omitted `subagent_type` falls back to `general-purpose` and is denied too. |
| MCP | `--strict-mcp-config`. Every server in the caller's config must be `connected` in `init`. |
| Task rules | Written to the Evidence directory and passed with `--append-system-prompt-file`. Project instruction files are never written; their hashes are compared before and after. |
| Prompt | Sent on stdin, so no variadic flag can swallow it. |

## Plugin allowlist

`TaskLaunch.plugin_allowlist` defaults to empty. Each `PluginAllowance` names
the `source` that `init` must report and the directory passed to
`--plugin-dir`. Non-built-in plugins in `init` must equal the allowlist.
Plugins whose `init` entry has `path: builtin` and a `@builtin` source ship with
the CLI, cannot be turned off from here, and are recorded separately.

## Stop conditions

The stream is parsed while it arrives. These stop the process group at once:

`PLUGIN_NOT_ALLOWED`, `PLUGIN_ALLOWLIST_NOT_LOADED`, `PLUGINS_UNVERIFIABLE`,
`UNEXPECTED_SUBAGENT`, `SUBAGENTS_UNVERIFIABLE`, `MCP_SERVERS_MISMATCH`,
`BACKGROUND_SUBAGENT_STARTED`, `DENIED_SUBAGENT_EXECUTED`, `TIMEOUT`,
`STREAM_LIMIT`.

## Completion

`COMPLETED` requires no stop condition, unchanged Project instruction files, an
`init` event, no malformed line, exit code 0, and a **last** `result` event with
`is_error: false` and `subtype: success`. `subtype` alone is not enough: a rate
limited run reports `subtype: success` with `is_error: true`.

## Evidence

| File | Content |
| --- | --- |
| `launch.json` | Written before the process starts: argv, environment names, removed parent names, CLI version, instruction diagnosis, Project settings diagnosis, file hashes, allowlist, denied subagents. |
| `stream.jsonl` | The full stream-json output. |
| `stderr.log` | Process stderr. |
| `task-rules.md`, `prompt.txt` | Exactly what the session received. |
| `outcome.json` | Status, reason, model, CLI version, loaded plugins with versions, Agent attempts, violations, result count, stream hash. |

The directory must not exist yet and must be outside the worktree. Environment
values are not recorded, apart from the two fixed variables and
`CLAUDE_CONFIG_DIR`.

## Instruction diagnosis

`diagnose_instruction_files` walks the worktree and its ancestors, because a
`CLAUDE.local.md` above a nested worktree also stops `AGENTS.md` from loading.

| `configuration` | Meaning | `agents_md_effective` |
| --- | --- | --- |
| `agents_only` | Only `AGENTS.md` | true |
| `claude_only` | `CLAUDE.md` or `.claude/CLAUDE.md` without an import | false |
| `claude_imports_agents` | A Claude file has an `@AGENTS.md` import | true |
| `claude_local_present` | `CLAUDE.local.md` without an import | false |
| `none` | No instruction file | false |
