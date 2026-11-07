# #340 R6B Phase D0 — real outside-Git verification and D1 plan

**SOURCE-ONLY / UNQUALIFIED / `dispatch_allowed=False`. Not activation.**
Base: merged main `8c2a76bdd53fda7ce3bd69a28e86ee569799940b`, isolated remote-free
checkout. Only this document and
`tests/test_kagari_vnext_claude_r6b_phase_d_outside_git.py` are added; no existing
tracked file changes. Main's `CLAUDE_EXECUTOR_UNQUALIFIED` remains in place.
No production routing, service, executor, Task, real Claude/model/auth/profile
probe, network, Git mutation, publication or deployment is introduced.

## Bounded evidence

This complements [Phase C](kagari-vnext-claude-r6b-phase-c.md), whose positive
storage tests substitute the enclosing-Git observation. D0 instead uses actual
Linux `TemporaryDirectory(prefix="kumiki-r6b-d0-", dir="/tmp")` roots, checks 0700
without repairing modes, and calls the **unmodified `_outside_git`**. There is no
mocked-Git fallback or skip on outside-Git failure. Intents, policy pins, settings
digests and matching effective-policy surfaces are test-only inventions. No
policy/settings/account facts are collected.

The self-contained unittest covers:

- Actual factory allocation, manifest and namespace; canonical ASCII JSON,
  exact digest-linked manifest/reservation/pending bytes, 0700 directories,
  same-user single-link 0600 files and the closed namespace inventory.
- Call-through traces of real exclusive/nofollow creation and successful fsync:
  manifest file, namespace, root and scratch parent during initialization;
  reservation file + namespace, then pending file + namespace **before** the
  `FIXTURE_PENDING_DURABLE` report. That report is still `status=UNQUALIFIED`,
  effective policy UNVERIFIED, non-completing and non-dispatchable.
- Pending-only observation, duplicate reservation and changed UUID remain
  UNKNOWN; a new UUID keeps the repository+Issue namespace, not a new grant.
- Injected fsync failures 1..4, separately for initialization and reservation,
  each in a separate temporary root: UNKNOWN, retained partial artifacts, no
  automatic retry/repair, and no successful report. A consumed reservation
  handle cannot write or sync again. Failed initialization returns no handle.
- Safe, deterministic competing-file/symlink injection within test-owned roots:
  real O_EXCL refuses the collision, retains evidence and leaves the target
  unchanged. No hostile concurrent worker, external target or FIFO is used.
- Existing paths/completeness claims cannot reopen proven genesis. Using a
  previous root as scratch parent allocates a different child, not a resume.
  A second independent root can store a second **synthetic** pending receipt;
  this demonstrates the missing cross-root authority, not legitimate retry.
  A self-authored result pair can satisfy historical consistency but still
  cannot authenticate execution, authorize replay or complete a Task.
- Process/network/config/authority-entry guards and source checks: no new direct
  Main/transport imports or integration. Phase B's existing transitive Main
  imports are unchanged; no Main instance/owner is created by D0.

### What this does not prove

`FIXTURE_PENDING_DURABLE` here means the expected Linux fsync syscalls returned,
not that a real crash or power loss was survived. No crash, kill, reboot,
power-failure simulation, restart/recovery protocol or actual effect occurred.
Marker-only `_outside_git` success is not complete Git-topology qualification
(including environment-routed admin dirs), filesystem/mount qualification or
host routing authority. Locks and inode checks assume cooperative same-user
local storage, not hostile same-user mutation or forged Python handles.

Digest self-consistency and bounded enumeration are **not** authenticated complete
history, trusted absence, rollback detection or an exactly-once/replay guarantee.
No stable cross-clone/restart routing or cross-root registry exists. A fresh
disposable root cannot clear an uncertain production attempt. Policy claims do
not prove binary semantics, managed policy, account identity or no-tools safety.
The source-only Lane/Main refusal, Ask and executor qualification are unchanged.

## Phase D1 proposal — separately reviewed future interfaces only

These are minimal design boundaries, **not implemented APIs or permission to
launch**. Review each independently with negative fixtures before integration.

| Priority / boundary | Minimal future input/output and fail-closed contract |
| --- | --- |
| **Must before any launch: host-owned routing** | Resolve a trusted stable repository identity + Issue through a host-owned registry to one pinned external storage identity/epoch. Caller cwd, clone/common-dir, remote spelling, UUID, Contract or policy cannot choose a fresh history. Explicitly qualify aliases/migration and lost/replaced roots; absent routing evidence means UNKNOWN, never initialize-on-miss. |
| **Must: authenticated complete inventory / rollback detection** | An independent trust anchor returns a bounded authenticated inventory/checkpoint bound to that repository+Issue, sequence/epoch and retained attempts. Authenticate genesis and completeness, detect deletion, rollback, forks, duplicate roots and stale checkpoints across restarts. Do not keep the only rollback anchor beside deletable receipts, or equate a valid digest/signature on one receipt with a complete inventory. |
| **Must: durable pending and restart reconciliation** | An exclusive, idempotent host reservation interface binds exact intent/policy/authority to an attempt and records a no-replay tombstone plus pending; file and directory fsync must finish before any effect. A separate read-only restart/reconcile interface reports retained incomplete/unknown states, including failure before/after pending, effect, result write and acknowledgement. Result durability and linkage require separate review. Disconnect or lost acknowledgement never triggers resend, root replacement or a new-UUID retry. Qualify the supported filesystem/crash model in a separately authorized test, not this D0 run. |
| **Must: external trusted effective-policy collector/verifier** | Host-selected, exact-version binary provenance/identity and semantics; canonical executable/profile/environment; full resolved settings precedence including managed local/remote sources; all lifecycle hooks, MCP, plugins, built-in/deferred tools and permissions; personal-account/plan and prohibited provider/credential overrides. Return independently authenticated, fresh evidence bound to intent and pinned context, without persisting credentials/raw settings. Account/terms/billing clearance and isolation must be independently established; argv and self-authored digests are insufficient. |
| **Must: Main live effect-boundary recheck** | Under the current Main owner, recheck the approved selection, live Issue eligibility/snapshot, #191 Contract/Task/branch/base/worktree/common-dir, authority epoch, policy freshness and storage reservation immediately before each eventual effect. Changes revoke admission; pending remains for reconciliation. No caller callback or old intent becomes a durable grant, no alternate executor/account fallback, and no routing around v2 exclusions. A distinct reviewed integration must retain the current refusal until all gates and explicit launch authorization are satisfied. |
| **Must define refusal now; capability later: Ask/resume** | The initial one-shot no-tools boundary must explicitly reject/retain unsupported tool, permission, Ask and deferred states rather than invent an answer or `pending_ask: false`. Before enabling Ask/resume, define authenticated native session + pending-Ask identity, exact operation/answer binding, single-use human authority, freshness/revocation, owner handoff and restart-safe reconciliation; recheck Main/policy at resume. No implicit autoapproval, resumed turn or replay from a historical receipt. |

**Deferrable beyond an explicitly approved one-shot no-tools diagnostic:** actual
Ask/resume implementation (remain unsupported meanwhile), read-only or mutating
tools, native background Lane A, broader Task/Product/PR/publication workflows,
multi-platform filesystems, comparative model evaluation and convenience UX.
They are not substitutes for the must-haves. Full Task executor activation also
requires its own functional/Human-feedback and recovery qualification. D1 design
review alone must not remove `CLAUDE_EXECUTOR_UNQUALIFIED`.

## Reproduction and result record

Run separately to prevent test import/patch state from masking the D0 boundary:

```sh
PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_d_outside_git
PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_c
PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_b
PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_lane_b.ClaudeLaneBParserBoundaryTests tests.test_metadata_codec
```

Verified on Linux in the specified checkout (2026-11-07), each command above in
its own Python process, with 120-second command bounds:

| Scope | Exact unittest result | Exit |
| --- | --- | --- |
| D0 real outside-Git | `Ran 9 tests in 0.147s` / `OK` | 0 |
| Phase C | `Ran 25 tests in 0.405s` / `OK` | 0 |
| Phase B | `Ran 23 tests in 0.214s` / `OK` | 0 |
| Lane parser boundary + metadata codec | `Ran 14 tests in 0.224s` / `OK` | 0 |

**71 tests passed; no failures or skips.** Parallel Nix C/B invocations each
emitted an ignored SQLite evaluation-cache `database ... is busy` warning from
Nix's user cache; this was not a test failure or policy/auth read. Test-owned
writes remained in disposable roots (D0: `/tmp`; existing B/C: checkout). No full
Lane suite was run: it includes fake process launches and Git-writing fixtures
outside this D0 scope.

Eight scoped Python files (D0, B/C/Lane modules and tests, metadata codec test)
parsed with `ast.parse` under the offline Nix devShell, bytecode disabled and
evaluation cache disabled for that check. Source-boundary assertions passed;
read-only review found no issues. Whitespace checks include both untracked new
files via `git diff --no-index --check`, not just the empty tracked diff. HEAD
still exactly matches the base above; `git remote -v`, tracked and staged diffs
are empty. The only dirty entries are the two permitted untracked additions.
