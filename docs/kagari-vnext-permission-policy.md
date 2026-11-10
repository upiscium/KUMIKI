# Task permission decisions (#394, ADR-0006 W2-a)

`tools/kagari_vnext_permission_policy.py` decides one request that Claude sends
to `--permission-prompt-tool` (`tool_name`, `input`, `tool_use_id`).
**Decision logic only:** the stdio MCP server, the append-only decision log,
the Git configuration check and the launcher wiring are W2-b. Nothing here
starts a process.

`decide(Policy, request)` returns a `Decision`. `Decision.response()` is the
reply verified in ADR-0006 check (a): `{"behavior": "allow", "updatedInput": …}`
or `{"behavior": "deny", "message": "<CODE>: <detail>"}`. Anything not
recognized is denied.

## Contract commands

The approved Issue body may hold one fenced block that lists the test and
verification commands the Task may run:

````markdown
```kagari-commands
# lines starting with "# " are comments
python3 -m unittest ...
just test
```
````

- One command per line, parsed with `shlex`. A line ending in `...` allows any
  further arguments; otherwise the argv must match exactly.
- Shell operators, environment assignments and `git` / `gh` are refused when
  the body is parsed (`PolicyError`), so a Contract cannot authorize publication.
- No block means no Contract commands. More than one block, or an unclosed
  block, is a `PolicyError`.
- A prefix rule such as `python3 ...` would allow any Python program. List the
  narrowest form that the Task needs.

## Decisions

| Request | Decision |
| --- | --- |
| `Read`, `Glob`, `Grep` | Allowed inside the worktree. |
| `Edit`, `Write`, `NotebookEdit` | Allowed inside the worktree, except `.git` anywhere, `.claude/`, and the root `AGENTS.md`, `CLAUDE.md`, `CLAUDE.local.md`. Paths are resolved through symlinks and `..`. |
| `WebFetch`, `WebSearch`, `Agent`, other MCP tools | Denied. |
| `Bash` | One plain command only: no `; & \| < > $ \` ( ) { } [ ] * ? ! #`, backslash, newline, leading `~`, or `NAME=value` prefix. Every word, and any value attached to an option (`--file=x`, `-fx`), that resolves outside the worktree is denied. Then the rules below apply in order. |

Bash, in order:

1. `git` and `gh` commands that need a Human decision (ADR-0001) are denied with
   `block: true`, which tells Main to set the Task BLOCKED: merge, `gh pr ready`,
   tags, releases, label changes, force push or remote ref deletion, and history
   destruction (`reset --hard`, `clean`, `branch -D`, `worktree remove`,
   `reflog`, `gc`, `filter-branch`, ...).
2. Other GitHub writes and plain `git push` are denied without blocking: the
   host publishes the branch and writes to GitHub. GitHub reads are denied too,
   because the Contract snapshot is the Task's source.
3. Git reads (`status`, `diff`, `log`, `show`, `rev-parse`, `ls-files`) are
   allowed only when `Policy.git_read` is true. The host sets it after finding no
   clean filter, external diff or fsmonitor configured (#364). The command is
   rewritten to `git -c core.fsmonitor=false -c core.pager=cat --no-pager <sub>`,
   plus `--no-ext-diff --no-textconv` for `diff`, `log`, `show`. Options that run
   helpers, write files or redirect Git are denied. Other Git commands are denied.
4. Read-only commands (`ls`, `cat`, `grep`, `rg`, `find`, `head`, `tail`, `wc`,
   `echo`, ...) are allowed, except options that execute, write or follow
   symlinks (`find -exec`/`-delete`/`-fprint`/`-L`, `grep -R`, `rg --pre`/`-z`/`-L`,
   `ls -L`).
5. `mkdir`, `touch`, `cp`, `mv`, `rm`, `rmdir` are allowed when no operand is a
   protected path or the worktree root.
6. Contract commands are allowed.
7. Everything else is `OUT_OF_CONTRACT`.

## Known limits

- Paths are resolved when the request is decided. A symlink created between the
  decision and the run is not seen.
- Projects that use a Git helper such as git-lfs cannot use Git reads yet.
  Supporting known helpers is a follow-up (Human decision, 2026-11-10).
- A Contract command runs Project code, which can do anything the user can. The
  Contract is Human-approved, and these decisions only keep the session from
  reaching beyond it through Claude's own tools.
