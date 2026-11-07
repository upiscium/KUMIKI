# #340 R6B Phase C — bounded disposable storage experiment

**Source-only / UNQUALIFIED / `dispatch_allowed=False`. Issue #340 remains open.**
No Main/lane/transport wiring, activation, packaging, installed process, account
probe or live policy collection. `CLAUDE_EXECUTOR_UNQUALIFIED` is unchanged.
The private module is executable only as Python fixture code, not a CLI/launcher.
There is no callable effect/transport argument and no positive dispatch grant.

## Admission means fixture storage only

The pure `_manifest` reuses Phase B's exact pending contract and its digest-only
**repository + Issue** namespace, including the existing `r6b-phase-b-` prefix.
No UUID, clone path, common-dir, remote, branch, Contract or policy change can
select another namespace within this fixture. All those exact intent bindings
still have to match. Manifest construction is not completeness evidence.

`_new_disposable_fixture` allocates a new private scratch root itself. It cannot
open/adopt an existing runtime root or accept caller-stated absence. Its
ephemeral handle is the genesis witness; there is no deserialization/resume API.
One fixture models one repository/Issue. Every operation holds a nonblocking
Linux directory-inode `flock`, with separate open descriptions even for threads,
and rechecks pinned root/namespace identities. Competing reservations return
UNKNOWN, not a second pending state.

Under the lock, complete bounded enumeration requires exactly one namespace in
the root and a closed set of at most four regular files in the namespace. Extra
names, nested directories, duplicates, unreadable entries, missing manifests,
partial histories, noncanonical JSON, bad linkage and mismatched intents fail
closed. No “latest wins”, wildcard receipt selection or caller completeness bit.
The only fresh inventory is the manifest created by that same fixture factory.
Even that inventory is not a historical observation of a completed operation.

Reservation first consumes the in-memory genesis bit, then exclusively creates
`reservation.json` (a retained no-replay tombstone), then `pending.json`.
Both files are fully written and **file fsync + namespace directory fsync** finish
before returning `stage=FIXTURE_PENDING_DURABLE`, still `status=UNQUALIFIED`.
Initialization also syncs the root and its parent. All files use 0600,
O_NOFOLLOW/O_EXCL/O_CLOEXEC; directory traversal uses nofollow directory handles.
Storage is canonical, same-user 0700; files are single-link, canonical ASCII JSON
bounded to 4096 bytes, with fixed short filenames. Receipts contain no raw paths,
UUID, prompts, settings, auth material or model output. Errors are sanitized.
Modes are checked, never repaired or relaxed (including restrictive umasks).

Write/sync/identity/inventory failures return UNKNOWN and retain partial files.
There is no cleanup, overwrite, recovery or retry API. The consumed bit also
blocks reentry if no file was successfully created or both receipt and tombstone
disappear. Losing that ephemeral handle does not permit reopening it as fresh.
Pending-only, result-only and UNKNOWN results require reconciliation. A complete
synthetic result pair is passed through Phase B's historical receipt checks and
remains unqualified, non-completing and non-replayable. Tests inject result files;
this MVI intentionally supplies no result writer or effect implementation.

## Effective policy schema, not a trust root

The small frozen evidence wrapper delegates all existing policy checks to
`phase_b._observe_policy`. Its additional closed, ordered surface list covers
settings, hooks, MCP, plugins, tools, managed-local and managed-remote config.
Presence/absence and empty claims have canonical digests; settings must match the
pinned effective-settings digest, and the whole synthetic surface list must
match the pinned qualification-report digest. Managed config may be claimed
present or absent, but Phase B's complete resolution claims are still required.
Hooks/MCP/plugins/tools may only be absent or present-empty in a matching fixture.

These are **self-consistency checks on fake claims**, not effective-policy
discovery, verification or attestation. No settings/profile/credentials are
read, and neither CLI argv nor these digests prove security. Even a perfect
synthetic match always returns UNQUALIFIED, effective_policy=UNVERIFIED,
dispatch_allowed=False, task_completed=False, ask_capability=UNQUALIFIED.

## Offline tests and explicit limitations

```sh
PYTHONDONTWRITEBYTECODE=1 nix develop --offline --no-write-lock-file -c python3 -B -m unittest -q \
  tests.test_kagari_vnext_claude_r6b_phase_c \
  tests.test_kagari_vnext_claude_r6b_phase_b \
  tests.test_kagari_vnext_claude_lane_b.ClaudeLaneBParserBoundaryTests \
  tests.test_metadata_codec
```

All test writes stay inside the checkout. To satisfy that isolation while testing
real locks/files/modes/fsync, positive storage fixtures substitute only the
enclosing-Git observation with a synthetic external boundary. Unmodified Git-root
rejection is tested separately. **Successful operation in an actual outside-Git
root is not exercised here.** The module rejects ancestor `.git` markers (file or
directory), bare/admin markers, symlinks and task-path overlap without spawning
Git or reading Git config. This conservative marker heuristic is not complete
host Git-topology qualification (e.g. arbitrary environment-routed admin dirs).

The fixture uses a cooperative same-user, local Linux filesystem. It does not
claim protection against hostile same-user mutation, forged Python handles,
deleted/replaced entire histories, mount tricks, rollback or power-loss behavior
beyond the tested syscall ordering and injected failures. An inventory is a
locked point-in-time view, not an authenticated append-only ledger. Each newly
allocated disposable fixture is a separate synthetic universe: **creating another
fixture/root is not a production retry or authority to evade repository/Issue
history**. There is deliberately no production root routing or cross-root registry.

Deferred gates: independently trusted effective-policy collector/verifier and
binary semantics; host-owned stable repository identity/root routing; authenticated
history/rollback and cross-process restart/reconciliation; real crash/filesystem
qualification; Main ownership/live Issue/context rechecks at an eventual effect
boundary; separately reviewed result durability and transport integration. None
of these gates is satisfied by this source MVI. No usable executor is claimed.
