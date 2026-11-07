# #340 R6B Phase D1B1 R0 — standalone one-shot RecoveryJournal contract

**SOURCE-ONLY / DESIGN + SYNTHETIC TESTS / NON-WIRED / UNQUALIFIED. No activation.**
Baseline: isolated, remote-free `main@62e3a363bb573e12e378297c5bc34d329bc1e338`.
Only this document and `tests/test_kagari_vnext_claude_r6b_phase_d1b1_r0.py`
are R0 additions; existing tracked files, KAGARI Main, Lane, transport, default
OpenCode and `CLAUDE_EXECUTOR_UNQUALIFIED` remain untouched.

## Decision: reduced operational guarantee, not equivalent security

[D1B0](kagari-vnext-claude-r6b-phase-d1b0.md) describes a *future*, separately
administered monotonic Registry/Anchor to withstand history loss and rollback.
D1B1 R0 instead evaluates a **strictly narrower cooperative, single-host,
single-Task/no-tools diagnostic**, using the existing #140
`tools.kagari_vnext_trusted_host.RecoveryJournal`. This is a deliberate
acceptance-condition downgrade, **not** a proof that the independent anchor
can be omitted while retaining D1B0's guarantees. The stronger option is
deferred, not implemented or implicitly satisfied.

This reduced profile assumes one trusted KAGARI Host installation, one
persistent local Linux filesystem, one cooperative effective OS user, no
hostile modification or restoration of its journal, and truthful Host
configuration. Hardware/power loss and process crashes have not been
qualified by R0. It cannot prevent a malicious process with equivalent UID,
root/privileged storage operators, backup restore, snapshot revert, volume
re-provision, bind mounts or inode reuse from deleting/replacing or rolling
back a directory. It cannot
authenticate absence, prove completeness, guarantee exactly-once execution,
or safely operate independent hosts/replicas. It cannot even guarantee
**at-most-once** execution if records can disappear or the Host selects a new
root; merely requiring no automatic retry on *observed* uncertainty is weaker.
Those are **explicitly absent guarantees**, not hidden prerequisites. No new GitHub App, Ruleset, API
service, database, or independent protection infrastructure is proposed.

## Existing primitives — what can actually be reused

- `RecoveryJournal(directory)` pins the inspected directory device/inode
  and requires a canonical, private, current-UID directory. `create(key,
  record)` uses `O_CREAT|O_EXCL|O_NOFOLLOW`, bounded canonical JSON, a
  single link file, then file and containing-directory `fsync`. `read(key)`
  checks the record's type, size, mode and canonical encoding.
- A second create at the **same key in the same live directory** is refused.
  A pre-existing, malformed or partially written file stays an obstruction.
  `create` does not expose an overwrite/delete/retry operation. A failure
  after opening a file may already have created the key and is **UNKNOWN**.
- The journal has **no global lock, manifest/complete-inventory check,
  authenticated checkpoint, cross-root CAS, generation ownership,
  event/result transaction, restart classifier or trust in caller inputs**.
  Its `(st_dev, st_ino)` root identity is process-local, **not a persistent
  binding**: a fresh instance constructed on a recreated root is indistinguishable
  from genesis. Two independently provisioned journal roots can each create
  the same key.
- `MainSupervisor` has a cooperative repository-owned `flock` and live
  owner checks, but neither it nor a hash of a locally authored record
  attests to the full history. R0 does not connect the two components.

## Proposed Host-owned one-shot binding (NOT implemented)

1. The existing trusted Main/Host configuration must independently fix
   **one numeric canonical repository identity**, positive Issue, one
   persistently provisioned journal root and a bounded one-shot operation
   generation. They must survive restart and not be chosen by model input,
   caller cwd, clone, Worktree path, Git remote spelling, Task UUID, session
   UUID or attempt ID. Verify live Main owner and current exact approved
   Issue/Task/Contract/Worktree before any effect. A dataclass or a supplied
   path is not Host authority. No new registry service is required.
2. At provisioning time, a future Host must independently establish and
   persist an identity binding for exactly one root on a non-temporary,
   qualified local filesystem and must qualify its parent-directory chain.
   A `RecoveryJournal` instance's transient inode check **does not
   implement this**. Missing,
   re-created, moved, multiple or unverifiable roots are required Host-level
   **UNKNOWN/BLOCKED** states, never fresh genesis or permission to choose
   another root. A newly constructed journal on a recreated empty root can
   in fact return `None` today. **R0 has no root provisioning/verification
   implementation**, and a complete rollback of *all* local evidence can
   remain undetectable in this deliberately reduced profile.
3. Derive an invariant key from the Host-owned canonical repo+Issue+operation
   generation (e.g. domain-separated SHA-256), **never from an attempt UUID,
   session, time or locally selected new path**. For the first diagnostic
   generation, retain the reservation forever. Later explicit Tasks require
   a separately qualified generation allocation/retention protocol; neither
   "successful" first run nor a new UUID silently unlocks one.
4. While the current Main ownership is held, construct an intent with the
   exact approved Task/Contract/Worktree, selected Claude CLI, policy/version
   pins and a no-replay marker. Reject unqualified effective tools, hooks,
   MCP, plugins, settings precedence, account/billing and executable
   identity; R0 cannot collect or authenticate these surfaces. Persist the
   create-once reservation **before invoking any transport effect**. The
   return of `create` is not a standalone execution grant; fresh live
   checks and separately approved Human launch authority remain mandatory.
5. If write/open/fsync/readback/owner validation is uncertain, preserve the
   record and return `UNKNOWN` for manual reconciliation. Do not re-create
   using an alternative key, root or UUID. A result or acknowledgement may
   be recorded in a **different, predetermined create-once key**, tied to
   the original reservation; that is not atomic with launch or proof that
   the model performed a useful Task. No automatic replay after a result.
6. On restart, read only the configured exact root and stable key under
   verified Host ownership. A missing record is **not authenticated absence**.
   Any observed reservation or inconsistent/missing result means no dispatch.
   Completion requires separate external evidence, exact result/session
   linkage, current Task checks and Human acceptance, not a journal field.
   No erase/reset/repair/competing-root recovery API belongs in the
   diagnostic lane.

### Failure windows and required diagnostic behavior

| Window | Narrow R0 interpretation |
| --- | --- |
| Before provisioning or uncertain/missing root | `UNKNOWN`; no initialize-on-miss |
| Reservation key absent in an apparently empty existing directory | `ABSENCE_UNPROVEN`; not an execution grant |
| Reservation write started, truncated or file/directory fsync uncertain | `UNKNOWN`; no retry even if readable |
| Reservation durably recorded, but before subprocess launch | Claimed reserved; crash cannot justify rerun |
| Launch attempted, outcome/result/ack missing or duplicated | Effect uncertainty; no resend/new UUID |
| Result record observed or ack reported | Observation only; no automatic Task completion/replay |
| Original root deleted/replaced; second root provisioned | Cross-root duplicate possible; outside guarantees |
| Single key file unlinked by same UID, or temporary-file cleanup | A later `create` can succeed; no at-most-once proof; missing is UNKNOWN |
| Host directory rolled back by same UID | Potentially undetectable; NOT at-most-once/exactly-once |

`fsync` returning on a local filesystem is not proof of surviving sudden
power loss; R0 has **no crash/power-cut qualification**. No CAS-preview from
D1B0, synthetically matching digest, Python type check or receipt readback
can be upgraded into authorization or authenticated complete history.

## R0 evidence and next qualification gate

The accompanying deterministic Python unittest tests the **existing**
`RecoveryJournal` only in disposable private directories with invented
repository IDs/Issue numbers and intent/result payloads. It tests both
create-once strengths and demonstrated limitations (competing roots,
recreated fresh roots, arbitrary caller-authored records, unverifiable absence),
plus injected `fsync` errors, same-UID single-record deletion, and hand-made
malformed files. These tests do **not** simulate an actual process crash or power
loss. No model invocation,
Task launch, credentials,
host install, service, config mutation, PR, branch or Git write is exercised.

R1 must separately design and verify a Host-provisioned *stable* root
binding and an idempotent, read-only restart admission gate; reuse Main
ownership and the existing journal rather than adding a new database.
Prove same-directory concurrent attempts, partial create/failure, and
one-shot no-retry under real configured owner in a disposable fixture.
Only after independent effective Claude CLI policy/account verification,
live Main effect-boundary recheck, crash/restart tests and distinct Human
Task-launch approval may a **single** no-tools diagnostic be considered.
Ask/resume, tools, multi-attempt, malicious same-UID protection, remote
replication and exactly-once guarantees are deferred and cannot be claimed.
Request early Human hands-on after that minimal honest slice rather than
building unrelated protective infrastructure first.

Reproduce (offline Nix, no installed-product change):

```sh
NIX_CONFIG='eval-cache = false' PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_d1b1_r0
```

R0 tests are **not** independent production/host trust qualification,
an independent PR review, or an executor activation permission.
