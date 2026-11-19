# KUMIKI #364 — safe Git inspection Phase A

**Status: SOURCE ONLY / PROTOTYPE / NOT ACTIVATED.**

The Phase A candidate introduces a Task-scoped typed OpenCode inspection tool
and a deterministic Python helper. Neither is placed in an Agent allowlist or
connected to Task Orchestrator, Maintenance Orchestrator, verification,
publication, or the mandatory Git whitespace check. This does not unblock
KUMIKI #356 or Blog #35.

## Source artifacts

- components/agent-core/.automation/bin/safe_git_inspect.py
- components/agent-core/.opencode/tool/safe_git_inspect.ts
- identical generated copies in all six templates
- tests/test_safe_git_inspect_phase_a.py

The tool receives only numeric Task ID and an operation (identity or
index-meta). It resolves the Task root from the trusted OpenCode worktree
context and refuses a session outside that Worktree. It invokes Python with
typed arguments, no shell, a checked Git executable and a sanitized
environment. A nested Justfile is never evaluated.

The helper checks canonical cwd, registered branch and Worktree, matching
HEAD, positive Task ID and Task State identity marker. It never accepts a
user-selected repository path or Git argument. The origin name comes from
local configuration: it is NOT independently authenticated to GitHub. The
Task identity marker is not a substitute for validating a full frozen Issue
Contract. These stronger authority checks belong in Phase C.

The only fixed internal Git operations are rev-parse HEAD / object format /
root, symbolic-ref, worktree list --porcelain -z, remote get-url origin and
ls-files --stage -z. All calls disable core.fsmonitor, use a checked absolute
system/Nix Git binary and a sanitized environment (no inherited GIT_*,
system/global configuration, credentials, pager or prompts). Local Git config
still exists, so this is not a general no-execution proof for arbitrary Git
commands. No diff, show, status, external diff or filter conversion is run.

The identity operation returns bounded JSON with Task, registered branch,
HEAD, object format, repository label and worktree hash. The index-meta
operation adds the count and SHA-256 of raw stage-0 index records. Both
responses say PROTOTYPE_ONLY, not PASS or READY. Symlink/submodule and
unmerged index records are unsupported and rejected. Output limits are
enforced as bytes are read, including a per-command deadline.

## Verification

The offline test suite creates disposable repositories, invokes the actual
Python CLI and verifies exact Task/Worktree binding, stable identity, index
metadata, wrong Task/root refusal, duplicate Task Worktree refusal,
symlinked/missing identity marker rejection, unsupported index types,
template parity and refusal of out-of-scope operations.

A hostile fixture configures core.fsmonitor, diff.external, filter.clean
and diff-specific helper commands, then injects GIT_* environment variables.
The candidate must leave the harmless external-execution marker absent;
an unguarded Git control must activate the fixture marker.

Run the focused suite in the dedicated source Worktree:

~~~~
nix develop --command python3 -B -m unittest discover -s tests -p test_safe_git_inspect_phase_a.py -v
~~~~

## Explicit remaining gates

Phase A does NOT implement changed-file inventory, untracked/staged content
handling, patch generation, whitespace checks, rename inference,
submodules, binary/invalid-UTF8 normalization, real host authority receipts,
or native OpenCode runtime qualification. Phase B must implement supported
worktree observations and whitespace semantics; unsupported inputs must be
reported instead of quietly passing. Phase C must bind results to a trusted
canonical Issue Contract, integrate the mandatory Task/Maintenance
publication gates without deleting them, qualify native permissions, perform
independent reviews and run full CI.

The mutable source checkout is not an immutable host-owned install. No
claim is made against an unrestricted hostile same-UID process able to
replace trusted scripts or Git administrative data. Publication, consumer
upgrade and the existing blocked evidence require separate Human approval.

## Phase A independent-review corrections (source only)

The initial independent static review identified defects, which were
addressed without relaxing any role permissions:

- Task branch identity now follows the installed legacy Agent Core
  prefixes task/<id>-... and fix/<id>-..., rather than recognizing source
  repository feature branches as consumer Tasks.
- A Task marker containing *any* duplicate or contradictory Task ID, branch
  or Worktree entry is rejected. Its metadata comparison excludes atime
  (which normal reads can update), but checks inode, size, mode, owner,
  mtime_ns, ctime_ns and path/directory binding.
- Identity, branch, registered Worktree, remote label and Task marker are
  read and compared both before and after an operation. The index listing
  is read twice for index-meta. If the observations differ, the operation
  fails closed. **This does not establish an atomic snapshot or defeat an
  unrestricted same-UID writer with ABA/control of Git administrative data.**
- Internal Git calls have a total operation deadline shorter than the outer
  tool deadline; a SIGTERM handler unwinds the Python code through process
  group cleanup. Other signals (especially SIGKILL) and hostile child
  process races remain a separate integration qualification risk.
- The no-mutation fixture now checks the **linked Task Worktree index**,
  not only the Main Worktree index. Additional checks cover SHA-256 format,
  contradictory Task markers and first-read atime updates.

The security review also identified that trusting an executable solely from
its root-owned file metadata is inadequate: a caller-writable PATH may
contain a symlink named git to a different root-owned interpreter. The
source-only OpenCode tool no longer resolves Python or Git via Bun.which or
ambient PATH. Instead it requires an explicitly installed and root-owned
host configuration at:

    /etc/kagari/safe-git-inspection-host.json

This file is **not installed or modified by Phase A**. Its reviewed schema
uses version 1 with two entries named git and python, each containing an
absolute root-owned, non-symlink executable path and its exact SHA-256.
Both executable file bytes are independently hashed before launch.
The tool also requires every directory from the filesystem root to each
approved executable and host config to be root-owned, non-symlinked and
non-group/world writable. This rejects provisioned binaries in attacker-
renameable parent directories at inspection time. This is a preflight
pathname validation, not a fully atomic fd-pinned execution proof.
The native tool fails closed when the config or pin is absent or inconsistent.
The source itself remains a mutable unactivated candidate, not trusted host
authority.

Git's documented GIT_NO_LAZY_FETCH flag is explicitly enabled, and
GIT_ALLOW_PROTOCOL is set to an empty allowlist for the child Git process.
This attempts to prohibit implicit promisor fetch and any transport
helpers, including in sparse/partial clones. Support for sparse indexes
remains unqualified; no adversarial missing-tree/promise fixture has been
accepted. The documented Git variable semantics should be checked against
the exact deployed Git version:
https://git-scm.com/docs/git

The global source and six generated opencode.json copies explicitly set
safe_git_inspect to deny, instead of assuming that the absence of an allow
means the custom tool is disabled. No Agent role grants this tool. The
tool's effective native OpenCode permissions and automatic loading
behaviour still need a real-session test at the later activation gate.

The prototype is **not** an authenticated host-owned publication receipt.
No release, upgrade, PR, Merge, protected consumer edit or remote operation
is included in this source-only phase.

## Bounded Phase A review follow-up (2026-11-06)

Independent read-only correctness/security review of the source candidate
identified and motivated these source-only corrections:

- A registered sibling such as `fix/364-a/b` is now recognized as the same
  Task ID for duplicate-detection purposes, even though the currently
  selected branch syntax remains narrower and fail-closed.
- A completed/reaped Git child is not killed via a stale numeric PID/PGID;
  an actively running child process group is still terminated on error.
- Bare common-repository entries in the NUL-delimited Worktree inventory
  are skipped rather than treating their intentionally absent HEAD as a
  malformed linked Worktree.
- Worktree path bytes, including trailing ASCII whitespace, are preserved
  exactly; newline-delimited Git command results remove only the protocol
  newline.
- The OpenCode native tool now checks the Task root and all ancestor
  directories for directory type, ownership and non-group/world-writability
  before following the worktree's source path. This blocks a different user
  account from renaming a writable parent during inspection. It does not
  establish isolation from an unrestricted same-UID writer, writable mount,
  or arbitrary trusted-host modifications.

The regular CI regression now checks the native ancestor guard's source
contract and all generated tool copies, without an unreviewed skip when the
OpenCode plugin is unavailable in a fresh Python-only CI checkout. This
static check is NOT native execution acceptance.

A separately executed disposable Bun proof used the actual installed
OpenCode plugin in a temporary fixture (without touching protected Task
State): a worktree under a world-writable ancestor was rejected with
`Task root has an untrusted ancestor directory`; with trusted user-owned
ancestors, the missing root-owned /etc/kagari host PIN was rejected before
execution. No host PIN was provisioned. Native host-plugin loading, positive
pinned execution, digest validation and real OpenCode permission semantics
are not CI-qualified in Phase A.

These changes do not expand Phase A beyond identity/index-meta or grant the
tool to any agent. No production consumer accepted this candidate. A fresh
security/code re-review and full CI remain mandatory before publication.

## Post-review subprocess and CI corrections (2026-11-06)

The final independent static reviews confirmed the original Task-identity,
path, and stale-PID corrections but found two further limitations. Their
bounded source-only handling was:

- A new Python Git-process regression forks a child that inherits stdout
  and stderr while its Git-like leader exits; a short deadline forces
  timeout. The helper now checks `Popen.returncode` instead of calling
  `Popen.poll()` before killing the process group. Polling would reap the
  group leader and release its PID/PGID while pipe-holding descendants could
  still be alive. The helper sends SIGKILL while the group leader remains
  unreaped, then waits for that leader. The prior nonzero/reaped-PID
  regression still verifies that an already reaped leader is not signalled.
  This is not proof of full descendant cleanup when children detach or
  close both pipes before a successful leader exit; a stronger host-owned
  supervisor remains a later integration gate.
- The conditional Bun/OpenCode plugin test was a strict-CI problem: its
  unapproved skip would fail the canonical shard on a fresh checkout.
  Replaced with a dependency-independent source/byte-parity guard test
  (no skip). Separately, a one-off isolated Bun test importing the real
  installed plugin confirmed that world-writable ancestors and an absent
  root-owned host PIN were refused. Neither the new CI assertion nor the
  one-off direct `.execute()` test establishes OpenCode's real native tool
  permission behavior, configured host binaries, or a positive production
  execution flow.

The Phase A tool remains **PROTOTYPE_ONLY** and deny-default. No host PIN,
publication receipt or whitespace-check replacement is installed. The
entire candidate requires a new exact-source independent review, native
host acceptance and full CI before any publication.

## Phase A SIGTERM and index-contract corrections (2026-11-06)

Independent final-source security review found that a Python SIGTERM signal
handler that raises an exception can also raise while the guarded Git subprocess
is being spawned, terminated, reaped or having its pipes closed, aborting the
cleanup `finally` before it has completed. The prior group-kill fix addressed
PID reuse and zombie leaders on ordinary deadline failures, but not this
asynchronous exception boundary.

The new handler only records `_TERMINATION_REQUESTED`. The scoped `_git`
helper checks the flag at bounded safe points before spawn, while collecting
pipes (polling in <=0.2 second slices), and on completion; `main` also checks
the flag before emitting a successful result. SIGTERM no longer raises during
`killpg`, `wait` or pipe close, so child supervision runs to its normal
cleanup point. A pre-spawn and cleanup-time simulated signal regression checks
that group termination still occurs. Existing actual fork/pipe-holder checks
still validate the leader's unreaped state and the child's termination.

The index-meta test now independently hashes an exact Git `ls-files --stage -z`
listing and compares it to the prototype's reported digest. Separate negative
fixtures cover non-stage-0 (unmerged) and Gitlink mode records. These additions
do not permit the tool to publish whitespace/diff evidence or expand its
authorized Git operations.

**Residual limits:** These are source-only Phase A checks, not a POSIX/Linux
process isolation proof. A supervisor killed by SIGKILL cannot run Python
cleanup; detached/pipe-closed descendants, hostile same-UID actors, Python
startup/import path custody, and signal delivery in all conceivable thread/
kernel transitions remain beyond Phase A. The tool remains disabled by policy,
host PIN unprovisioned, without true native OpenCode effective-permission
acceptance or repository-wide CI.

## Phase A response commit-boundary correction (2026-11-06)

An additional final-source security review observed that a non-throwing
SIGTERM arriving after the pre-output cancellation check, but during a
successful stdout write/flush, could leave a complete JSON response and exit
status zero. The prototype now explicitly flushes stdout and rechecks the
termination flag after that flush. A deterministic test injects cancellation
at either the write or flush step and verifies that the CLI exits nonzero in
both cases. A Native Tool should not use stdout from a nonzero exit as
accepted response evidence.

**Commit boundary limitation:** This is a best-effort one-shot cancellation
protocol, not an atomic acknowledgment/abort handshake. A signal delivered
after the final cancellation check and before OS process exit remains a
late-cancellation race. Interpret such late signals as after the result's
last checked commit boundary, not as proof that every SIGTERM always cancels.
Actual OpenCode host cancellation semantics and any stronger atomic receipt
must be defined/verified in the separately authorized Phase C work.

The Phase A tool continues to return `PROTOTYPE_ONLY`, remain denied in all
roles, and carry no whitelist for publishing, deployment, or mandatory
whitespace checks. CI-wide execution and independent exact-source acceptance
are separate outstanding gates.
