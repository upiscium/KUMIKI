# Claude Lane B — source-only offline candidate (#340, design r1)

**UNQUALIFIED. No live dispatch, Task completion or Ask support is claimed.**
This slice follows the [design-r1 recommendation](https://github.com/upiscium/KUMIKI/issues/340#issuecomment-6556904509).
It tests a narrow no-tools diagnostic protocol around the official CLI's
documented print-mode shape, using **fake executables and disposable local
Git metadata fixtures only**. There is no Claude SDK/API client, account login,
token proxy, background agent, or production model transport.

## What is—and is not—connected

`tools/kagari_vnext_claude_lane_b.py` contains:

- Immutable Task-locator, authorization-subject and fixture-invocation types.
- `dispatch`: an inert public candidate API. It always returns `UNQUALIFIED`
  before reading configuration/metadata, calling an authorization callback,
  probing the CLI, creating receipts, or starting any process. No switch or
  configuration flag makes this function execute a model.
- `_exercise_fixture`: an explicitly private testing seam. Tests supply both
  an authorization callback and a fake transport; this is not a live host grant
  installer or a production launch path.
- `parse_stream`: a bounded, conservative parser for the synthetic protocol.
- `observe`: re-observation of a fixture's historical receipt pair; no CLI
  probe, answer, resume or resend.

The OpenCode launcher, Task/Ask CLI, scheduler and shipped GA are unchanged.
The separate [Main executor-selection gate](kagari-vnext-executor-selection.md)
now recognizes an explicit Claude choice, but does not call this fixture seam
or install a live transport. This source-only candidate has no installed CLI
wrapper. The earlier standalone preflight remains the only Claude-specific
command in the opt-in Nix package. No generated template changes or new
authority database are needed for this slice.

The private Python seam is not an adversarial sandbox: a caller able to import
and execute arbitrary Python already has that process's authority. Its name
and injection boundary make testing explicit; they are not a security boundary
against a malicious in-process caller. A separately reviewed host integration
must provide real authority and confinement before any live executor exists.

## Fixed individual credential context

The candidate consumes the earlier preflight configuration format with
**exactly one `personal` profile**, an explicitly canonical executable path,
and a configured individual plan (`pro`, `max`, or `ultra`). A `company` entry,
Team/Enterprise plan, API-key/provider override, or ambiguous configuration is
rejected. A plan is an operator assertion, not proof of account ownership.

The child receives the exact selected `CLAUDE_CONFIG_DIR` and a narrowly
constructed environment. A shell function, `$PWD`, Main's cwd, and the location
of `~/Documents/Work/` do not select the account. Neither rate limits nor other
failures trigger a company/API/other-executor retry. The original standalone
diagnostic can still inspect explicit company profiles; that does not make
company Task execution part of this candidate.

Fake version/help/auth probes must match the candidate **2.1.293** response
contract and expose the full fixed flag set. This version number is a fixture
target, **not a qualified real CLI version**. Rolling documentation and help
strings do not prove installed-version behavior or stream-schema stability.

## Task and operation boundary

The existing `verify_task_record` is reused to check #191 metadata, exact
Contract digest, branch/base identity, the registered linked Worktree, and the
original Project's Git common directory. It does not approve a Task or validate
live GitHub Issue eligibility. Its metadata reads use a disposable local bare
remote in tests; a real metadata remote may require Git network access.

Separately, the fixture authorization callback receives an exact immutable
subject containing the operation, Task/Contract identity, canonical cwd/common
directory, binary/config context, native UUID, and prompt/argv/policy digests.
Only literal `True` is accepted. The subject and context are rechecked at
effect boundaries, including after probes and before the fixture turn. The
callback simulates an external grant; the candidate does not construct Main's
ownership, live Issue eligibility, initial collaboration checkpoint, external
tool guards or publication authority.

The first diagnostic intentionally has **no Claude tools**, rather than
granting even read-only file access. The prompt is fixed to request the ASCII
sentinel `KUMIKI_LANE_B_DIAGNOSTIC_OK`. There is no arbitrary prompt parameter.
The fixture asserts these options, as separate argv entries:

```text
-p --output-format stream-json --verbose
--session-id <canonical UUID>
--permission-mode default
--setting-sources user
--tools ""
--disallowedTools "mcp__*"
--strict-mcp-config
--mcp-config {"mcpServers":{}}
<fixed diagnostic prompt>
```

`--tools ""` removes built-ins; it is not `--allowedTools` autoapproval. MCP
tools are separately excluded. `stdin` is `DEVNULL`, cwd is the verified Task
Worktree, and captures/timeouts use the bounded preflight runner in tests.
There is no `--bare` (which excludes subscription OAuth), bypass flag,
`--no-session-persistence`, `--bg`, or `--resume` in this first invocation.

These controls **do not prove a real process has no filesystem/network effects**.
User and managed settings, hooks, instructions and other upstream behaviors
still need exact-version qualification. The existing preflight's hook/MCP
refusal is retained, not relaxed to make this test pass. All observations keep
`effective_policy: UNVERIFIED`.

## Receipts and retry semantics

Fixtures use an existing, private, canonical runtime root outside the Project,
Worktree and Git common directory. A Task namespace is derived from repository,
Git common-dir and Issue—not from the session UUID or Contract revision—so a
new attempted UUID is not a new retry namespace for that Task.

Under a bounded lock, the candidate writes and syncs an exclusive `pending`
receipt **before** calling the injected transport. A separate exclusive result
receipt refers to the pending digest and contains only sanitized observations.
Pending history is retained. The receipts bind Task/Contract, canonical paths,
binary identity/version, a digest of the fixed credential context, native
session UUID and fixed prompt/policy identity. Raw prompt/model text, auth JSON,
stderr, account IDs, emails and credential contents are not persisted.

- A matching complete pair can be reread without another auth probe or turn.
- A pending-only, result-only, conflicting, missing-history or malformed state
  requires reconciliation; no automatic resend or new session is attempted.
- A lost acknowledgement/transport exception remains unknown. A successful
  process exit or a PID does not recover an unobserved result.
- A stale Contract or changed path/configuration/grant stops execution.
- Unsupported Ask/tool requests remain blocked/unqualified, not a fabricated
  pending or answered Ask. No `pending_ask: false` claim is made.

The receipts are **historical self-consistency evidence**, not authenticated
Task authority or a tamper-proof exactly-once ledger. `observe` does not prove
that the old executable, account or configuration is still current. Arbitrary
deletion/replacement of the runtime root cannot be made safe by these files.
No cleanup, automatic repair, transcript parsing or live session recovery is
implemented. Storage must be trusted and responsive; repeated inode checks
do not eliminate hostile same-user TOCTOU or stalled-filesystem risks.

## Observation semantics

The synthetic parser bounds total bytes, line size, event count and JSON depth;
rejects duplicate keys; and checks session/cwd and event order. It expects one
init, one exact assistant response and one coherent success result. Unknown
shapes are not silently promoted to success.

| Evidence | Maximum claim |
| --- | --- |
| Process spawn / empty stdout | No session or response established. |
| Matching `system/init` | `SESSION_OBSERVED`, **not** accepted model work. |
| Exact matching assistant sentinel | `RESPONSE_OBSERVED`. |
| Coherent matching terminal result plus explicit exit 0 | `TURN_RESULT_OBSERVED`, synthetic only. |
| Tools, denials, deferred call | Unsupported policy/Ask; not replyable. |
| Truncated/malformed output, timeout, nonzero exit, lost result | Unknown/incomplete; never Task completion or a resend grant. |

All public results retain `qualification: UNQUALIFIED`,
`dispatch_allowed: false`, `task_completed: false`, and
`ask_capability: UNQUALIFIED`. The fixed sentinel is not a proof of useful work,
model provenance, or fulfillment of the original Task requirements.

## Offline verification and remaining work

```sh
python3 -B -m unittest -v tests.test_kagari_vnext_claude_lane_b
python3 -B -m unittest -v tests.test_kagari_vnext_claude_preflight \
  tests.test_kagari_vnext_task_binding tests.test_kagari_vnext_session \
  tests.test_kagari_vnext_main tests.test_kagari_vnext_main_service \
  tests.test_kagari_vnext_opencode_control
```

The fixtures create local Git repositories and registered linked Worktrees;
all Claude-shaped processes are fake. They exercise exact argv/cwd/stdin,
personal-only config, missing/denied/drifting grants, session spoofing,
incomplete/error/tool streams, lock contention, receipt corruption and
duplicate/reconciliation behavior. They are **not** AC-CLAUDE-03/04 real smoke.

Before live use: qualify official binary provenance and exact CLI schema,
current terms/billing, externally guarded operation authority and isolation,
then explicitly authorize one disposable no-tools real smoke. Only afterward
consider read-only tools and the separate single-defer Ask/resume fixture.
Main integration, caller-disconnect recovery, permission-host review, MUSUBI
read/reply, Product code/test/review/guarded PR and comparative evaluation remain
separate work. Native background Lane A is not a prerequisite or a substitute
for Lane B's missing Ask/recovery qualification.

Current references: [CLI reference](https://code.claude.com/docs/en/cli-reference),
[headless](https://code.claude.com/docs/en/headless),
[defer hook](https://code.claude.com/docs/en/hooks#defer-a-tool-call-for-later),
[agent view](https://code.claude.com/docs/en/agent-view),
[legal and compliance](https://code.claude.com/docs/en/legal-and-compliance).
