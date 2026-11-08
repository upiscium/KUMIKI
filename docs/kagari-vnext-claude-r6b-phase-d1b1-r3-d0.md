# #340 R6B D1B1 R3 D0 — Host identity, one-shot genesis and lost-history handoff

**DESIGN-ONLY / LOCAL-ONLY / NON-WIRED / NO AUTHORITY.**
Baseline: `main@d8e3ccf00d53c37b0bed54b6f1223892d0eaf32a`, isolated
remote-free checkout after source-only PR #386 was merged. This document is
not published. No Commit/Push/PR, deploy, Host directory creation,
`RecoveryJournal.create`, CLI execution, permission change or activation.

## Decision

Build the **small cooperative single-Host, one-UID, one-Task/no-tools**
Claude diagnostic, using existing KAGARI ownership and v2 Task provenance
where actually available. Do **not** add an independent registry database,
GitHub App, Ruleset, remote service, signing authority or autonomous repair
loop. This consciously retains the lower guarantees accepted in D1B1 R0:
a same-UID writer, backup restore or a full local rollback can erase
one-shot history, so this profile is NOT cryptographically at-most-once,
exactly-once, multi-Host safe or adversarially rollback resistant.

The next implementation must make genuine progress over R1 synthetic claims
and R2 real **read-only** journal inspection. Do not add yet another
self-authenticating dataclass/boolean as a substitute for Host verification.

## Reuse inventory and limits

| Existing component | Use | Important boundary |
| --- | --- | --- |
| `kagari_metadata_v2_host_binding.RepositoryIdentity` | Candidate repository ID, name, genesis | Constructor validates shape, **not** origin. The trusted Host must independently establish its numeric ID. |
| `kagari_metadata_v2_task_admission.CurrentTaskReader.observe()` | Current v2 Task/Contract/Issue ref and identity under Host-supplied reader | Current observation is not a grant or proof of Main lock ownership. Compare against exact installed `RegisteredIssue`, approved Issue and current selection. |
| `V2ReadOnlyMainHost` + active AttachmentLease | Existing read-only v2 observation/lease verification | Existing source explicitly keeps execution blocked. Do not fall back to a v1 Task just because v2 is incomplete. |
| `MainSupervisor._assert_owned(main)` under `main._main_owner_guard` | Verify actual held Main owner immediately before any future effect | `_host_repo_lock()` currently contains `mkdir` calls. **Never call this directly from R2's strictly read-only observation**. An ordinary Python owner object, PID or stale lock pathname proves nothing. |
| `r1.root_component(repo_id, issue)`, `r1.reservation_key(...)` | Stable numeric ID + Issue routing, generation `ONE_SHOT_V1` | Neither a contract digest nor a repository name, Worktree, retry UUID or Claude session can change the route. |
| R2 `observe_existing_host_journal` | Observe existing private fixed journal root and a single record | Caller-supplied numeric ID remains unauthenticated. `UNKNOWN` never establishes virgin genesis. Never use an R2 return as an executable permit. |
| Existing `RecoveryJournal` | Future explicitly approved create-once reservation before any external effect | `create` provides same-directory O_EXCL and file+dir fsync, not complete-history provenance, cross-root CAS or authenticated absence. |

The implementation boundary shall be explicit:
`independently Host-verified repository ID + current Task/Issue
-> live Main owner (future) -> fixed R2 readback (R3A: always non-grant)
-> separate Human-approved Host provisioning (R3B)
-> separate fresh effect-time authorization + one create-once reservation
-> independently qualified Claude no-tools launch`.
Each arrow is a new acceptance gate, not an implicit installed capability.
R3A specifically cannot authorize or enter the latter operations.

## R3A: unqualified read-only Host binding contract (NEXT SOURCE-ONLY SLICE)

1. The trusted Host must independently establish exactly one canonical
   GitHub **numeric** repository ID bound to the same current repository
   and Main context (plus positive Issue). The `RepositoryIdentity` type,
   `CurrentTaskReader.observe()`, `RegisteredIssue` and `V2Selection`
   only check supplied data consistency; **none alone authenticate the
   numeric ID's external origin**. The present R3A design has not
   qualified a specific installed live provider for that ID. A future
   bounded read of canonical GitHub repository facts through the user's
   existing authenticated CLI/Host adapter (not a GitHub App), and exact
   comparison to the v2 identity/current Task and Main repository, is a
   candidate needing its own evidence and approval. Until the provider,
   freshness and same-repository binding are independently qualified,
   R3A is only an **UNQUALIFIED negative-path contract** and cannot call
   an asserted repo ID 'authenticated'. Reobserve after async boundaries.
2. Verify exact issue, contract and record IDs; reject stale/archive,
   mismatched or absent Task, changed Attach, conflicting v1 Issue assignment,
   wrong Worktree identity, wrong repository ID or owner. No auto fallback,
   alias re-keying or new Task creation.
3. Prove live Main ownership using the existing
   `MainSupervisor._assert_owned` within its owner guard only at a Host
   boundary that is already allowed to maintain the Main lock namespace.
   R2 is **not** that boundary: its existing-only observer must remain
   free of Main's bootstrap side effects. Before a read-only Main owner
   adapter is wired, explicitly qualify its no-create/observational
   behavior or reuse the live owner from an already-held Main cycle.
4. Only then supply the verified canonical numeric ID and Issue to the R2
   existing-only reader. Never pass an agent-selected root. Readback
   `OBSERVED_BLOCKED` is a veto only; `UNKNOWN` requires Human/Host
   reconciliation; `REFUSED` stops the operation. Even an exact match must
   return `dispatch_allowed=False`, `authenticated_absence=False`,
   `task_completed=False`, `replay_allowed=False`,
   `commit_allowed=False`, `effective_policy=UNVERIFIED`.
5. Recheck identity, current Task/Issue, Main owner and R2 result at the
   eventual effect boundary. A read-only observation is never a durable
   authorization to launch later. R3A **does not implement this live
   effect-boundary check** and must remain NON-WIRED.

### Repository rename/transfer

The Main `flock` is currently keyed by textual repository name, while R1
routing is keyed by stable numeric ID. A renamed/moved repository may acquire
a second Main lock even though it maps to the same journal key. **Do not
automatically switch names, initialize a fresh root, or assert a global
single-Host owner across that transition.** If the verified numeric ID
matches but canonical name differs from the bound Main name, freeze Claude
admission as `UNKNOWN/RECONCILIATION_REQUIRED`. An explicit Human-supervised
migration must quiesce all old/new Main owners and retain the same numeric
root and history; do not add a parallel registry or new global lock in R3A.
Qualified rename/transfer execution remains deferred.

## R3B: explicit *first-use* provisioning (separately approved, NOT R3A)

**Never provision on observer read-miss.** Treat a missing directory, a
pre-existing empty root, an absent record and an unavailable history as
different diagnostics, but **none** as evidence of a first installation.

For a **verified first installation only**, a separately authorized
Host/admin operation may create the fixed owner-private namespace (never
under `/tmp` in an installed product) after fresh Host/Main and exact
Task checks and one explicit Human **provisioning** approval. It creates a
versioned immutable **genesis/identity marker only**, using a separate
domain-separated marker key under the numeric repo+Issue root, with the
existing `RecoveryJournal` O_EXCL and file+directory sync. **Provisioning
must NOT create or consume the one-shot execution reservation key.**
The later launch path creates that reservation **exactly once**, only after
its own separate Human no-tools Task-launch approval and fresh checks.
A missing marker or partial/uncertain genesis blocks for manual inspection;
absence of the reservation after genesis is still not proof it was never
used. This **cannot** be interpreted as a first-use grant by R2 or
restart observation. The future Host effect path has an entirely separate
*explicit first-use decision*—fresh Human approval of this specific
Task and a cooperative-profile operator assertion of **no prior launch
for this exact key**—before considering a single reservation create.
Without both, stop. This assertion is accepted operational trust,
**not** protection against historical deletion/rollback.
A simulated `fsync` result is not power-loss proof.

The provisioning decision is an **operator assertion of fresh deployment
under the cooperative profile**, not authenticated proof that no old root
was ever deleted or restored. If the Host or Human cannot establish that
history was never previously used, it MUST NOT treat the directory as
fresh. This accepts the documented weak guarantee instead of inventing
an independent monotonic anchor after D1B1 deliberately declined it.

Important sequencing before a **future** external effect:

`previously approved Host genesis with separate marker key
-> separately Human-approved FIRST-USE effect operation for one no-tools Task
-> fresh cooperative Host/operator assertion this key has never launched
-> verified Host identity/current Main/Issue/Task and fixed-key recheck
-> effect-path create-once reservation under ONE_SHOT_V1 key exactly once
-> effect-path verify its OWN exact durable O_EXCL/create outcome
-> final fresh policy and Main/Task check -> single qualified CLI spawn`.

**R2 always blocks or returns UNKNOWN, including immediately after a
successful reservation create.** Its pre-reservation `UNKNOWN` is a STOP
for all observer/restart callers, **not** a first-use grant. Only a later,
separately approved **first-use Host effect protocol**, entered through
its own explicit Human Task approval and fresh operator assertion of
unused history, may attempt the first reservation. The Host must
independently verify identity, current Main/Task and fixed root, then
bind and check its own exact `O_EXCL` success and durable readback. Neither
`UNKNOWN` nor `OBSERVED_BLOCKED` from R2 grants anything, and no
second reservation create is allowed. R3A does not implement that protocol.
If the operator cannot attest that this exact key has never launched,
or any previous effect may have crossed the boundary, stop and
reconcile—do not call the first-use path.

Reservation create uncertainty (including after opening the key but before
ACK) is **permanently non-retryable** until separate manual reconciliation;
there is no second Task UUID, alternate root, automatic overwrite, forked
branch or retry. Result-record claims do not imply Task completion; actual
effect and acknowledgement require separate Evidence.

## Restart/recovery state matrix

| Observation | Allowed outcome | Forbidden behavior |
| --- | --- | --- |
| No verified Host/Task/Main owner; name/ID or Contract drift | `REFUSED` or `UNKNOWN` | No R2 root selection or launch |
| Fixed root path missing, moved, symlinked, replaced or unsafe | `UNKNOWN`/`REFUSED` | No auto-mkdir, fresh genesis, alternate root |
| Root exists but reservation missing during observation/restart | R2 `UNKNOWN: history_absence_unproven`; observer/restart STOP | No inference of virgin first use. A separate Human-approved *first-use Host effect protocol* is not derived from this observation. |
| Reservation present, valid and matching | `OBSERVED_BLOCKED` | No replay, completion or new attempt |
| Reservation partial/malformed, fsync/ack uncertain | `UNKNOWN: reconciliation_required` | No rewrite, reset or retry |
| Task complete or user asks again | `OBSERVED_BLOCKED` or manual review | No reuse of one-shot `ONE_SHOT_V1` key |
| Repo rename/transfer, same numeric ID | Freeze and reconcile Main lock ownership | No second lock/root/alias-derived reservation |
| Full same-UID record+root loss/rollback, apparently empty | May be undetectable in accepted profile | **Never claim at-most-once against this threat** |

`UNKNOWN` is a STOP for **R2 observation and all restart/retry callers**,
not an instruction for an agent to try again. A fresh Host/admin first-use
protocol is a distinct, separately approved operation, NEVER entered merely
because observation returned `UNKNOWN`. It requires Human consent and
an operational assertion that this exact key has never launched; if history
is disputed or unavailable, it cannot use the first-use path. A different
user-approved Task after an uncertain old effect requires a separate
explicit scope and risk decision; it does not erase the old one-shot
identity or make reusing its key safe.

## Verification gates, in priority order

1. **R3A source-only integration contract and disposable synthetic tests:**
   independent Host v2 ID vs current Task mismatch/rename/owner-loss,
   absent/stale/retired v2 record, attachment drift, wrong Worktree, and
   existing journal present/missing/corrupt. All outcomes non-grant.
   Check no imports/wiring from active Main or Claude Transport.
2. **R3B future provisioning design + synthetic-only tests:** controlled
   first-use record creation, wrong owner/symlink/partial create, concurrent
   same-key creation, directory sync failure, restart readback and
   attempted second initialization; no host state installation.
3. **Later Host/integration evidence:** no-tools CLI policy, official CLI
   version/account/terms/billing, actual session acceptance, no file or
   network side-effects outside scope, Main/effect-time rechecks, crash and
   lost-ack tests, with separate Human one-shot Task-launch consent.
4. **PR/release/activation:** exact source + generated template consistency,
   offline tests, independent security review, exact-published-head CI;
   Human approval for each publication and activation gate.

## Explicitly NOT accomplished by this document

No actual identity attestation, live Main owner proof, read-only ownership
adapter, persistent root genesis, execution, Ask/resume, authenticated
history completeness, rollback defense, at-most-once across deleted root,
exactly-once, deployment or permission grant exists in this R3 D0 design.
The default OpenCode executor and Main's `CLAUDE_EXECUTOR_UNQUALIFIED`
remain unchanged.
