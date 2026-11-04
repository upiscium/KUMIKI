# KAGARI Metadata v2 — Phase 2C2 Archive Host coordinator

**Status: source-only, not installed, not activated, not a GitHub CAS or live Main implementation.**

Phase 2C2 adds tools/kagari_metadata_v2_archive_host.py and
tests/test_kagari_metadata_v2_archive_host.py. The adapter reuses
components/agent-core-v4/metadata_v2_archive.py::archive_issue without
changing any core schema, tree constructor or archive DAG validator. The
existing v1 runtime, product repositories, GA v0.1.0 and
refs/agentcore/metadata remain unaffected.

## Exact Host-owned inputs and lifecycle boundary

ArchiveHost requires a trusted BootstrapIdentity (numeric GitHub repository
ID, owner/name, original Attach receipt hash), RepositoryIdentity (same ID,
owner/name, original pinned SHA-1 orphan genesis), RegisteredIssue (Task,
Product branch/base, original Contract and first Record binding), ApprovedIssue
(exact frozen Issue title/body), and a live held #256 AttachmentLease.

The Host also supplies same-repository bounded reader/observer of exact
direct Git refs and a protected RecoveryJournal; none is created from
untrusted model text. A remote ref/commit hash or typed dataclass is not
a self-authenticating grant. Issue numbers and owners cannot redirect the
writer to the default/Product branch; the archive target is always
refs/heads/kagari/metadata.

The Host supplies a context manager hold_quiescence(operation) returning an
ArchiveQuiescenceLease. The lock must be an already qualified exclusive
Host-controlled repository/Task ownership boundary, held across the *whole*
candidate assembly, decision, writer and first positive readback. Repeated
lock.check(operation) calls must fail if the original live owner was lost or
the repository/Task scope changed. This API does NOT fabricate a v2 Main owner
or acquire another generic lock/daemon. As of Phase 2C2, #291 v2 Main
observation is read-only, returning V2_MAIN_BLOCKED_LIVE. A fake test Host
may supply positive mutual exclusion, but production integration must first
qualify the real v2 writer/Session owner and cannot call v1 Main or Cleanup
for the v2 mode.

## Current terminal Record and positive facts

CurrentTerminalRecord is validated against the *current* direct active Issue
ref C, not a historical checkpoint: original contract bytes and approved
title/body, exact Product branch/base, current Record pointer, original
orphan genesis, same-Issue first lifecycle and absence of an archive pointer
for that Issue. Unlike the active-only Phase 2B2 CurrentTaskReader, this
reader accepts a canonical current #191 cancelled/superseded Record while
forbidding a stored generic completed/running state.

The trusted Host's read_terminal callback provides a TerminalFacts record
from independently authenticated GitHub/Product/Session facts. The record
must bind repo ID/name, Issue, current Record/Contract, original Product
branch/base, a specific Human decision digest and positive no-session,
no-unknown-metadata-writer, no-unpublished-unique-Product-work and Product
preservation evidence.

Two disjoint routes are accepted:

- MERGED_PR: exact authenticated Human-authorized Product PR with state
  MERGED (not CLOSED), exact PR number, source HEAD, Product base,
  merge Commit, proof of remote merge ancestry/integration, no unique
  unpublished Product data, stopped Session and quiescent writers. The
  current Record may legitimately have no disposition.
- CANCELLED or SUPERSEDED: exact current canonical Human-owned disposition
  and matching replacement identity if superseded, with independently
  attested preservation of all unique Product data and stopped Session/
  writers. A Record disposition or archived ref by itself is NOT
  terminality proof and never authorizes deletion.

The source checks the shape, identity and literal-positive verdict fields
returned by the trusted Host; verifying the *genuine* Human decision, PR
merge facts, Product ancestry and stopped Session belongs to the Host.
The Host must not infer stopped from a missing PID/context/receipt or a
closed unmerged PR, nor clean/erase a dirty Task Worktree merely to pass
the predicate. A false/unknown value blocks effects.

Terminal facts, current Archive/Issue heads, AttachmentLease and exclusive
quiescence lease are checked before and after operation-specific Human
authorization, and again immediately before the external Archive CAS.
The *original* core Validation deadline is checked after potentially slow
callbacks, before and after journal fsync, and again at the effect boundary.

## Archive construction and immutable attempt

archive_issue() reads direct archive A and source Issue C and produces a
true canonical two-parent Merge Commit J with parents exactly (A,C) and
the permitted union tree. First-lifecycle Archive must have no existing
tasks/N/record. The original complete Issue commit history/immutable objects,
Record/Contract/Evidence and any Task View snapshots remain reachable by
exact OIDs as ancestors of J; no Product commit is a parent.

The trusted Host must also supply prior_attempts_cleared(operation) and
return literal True ONLY after inspecting all durable earlier attempts for
this same repository/Issue, including previous A,C keys, and positively
proving every old effect either fully reconciled or known not published.
The source verifies this before assembling a fresh candidate and again
before its external write boundary. A missing/unknown earlier attempt
must fail closed; a typed Human decision is not a substitute for this
cross-attempt reconciliation proof.

The journal key is domain-separated and derived from numeric repo ID,
Issue number, **original Archive A and Issue C**; two competing attempts
against the same A,C collide independently of operation ID/random Commit
nonce. The bounded fsynced create-once journal retains original candidate
J OID, operation ID, repository/Attach/Issue/Record/Contract/Product
identity, exact terminal fact snapshot, original A/C, policy ID, object
count and digest. The full Git object bundle is *not* stored in the
16-KiB journal; the Host must durably transport objects using its
authenticated object store before remote ref mutation.

The only supplied effect callback is a trusted Host
writer(Update,Bundle,deadline). It must independently verify effect-time
current Human/Task/session facts, both exact direct Archive/Issue refs,
expected-old archive A, unchanged Issue C, canonical repository identity,
server-side non-force CAS and bounded object transfer. Normal append-only
push ONLY to refs/heads/kagari/metadata is permitted. This source never
performs GitHub network writes and a retention_policy label is **not**
evidence of real Ruleset/no-delete/non-FF enforcement. Phase 3 must prove
the actual remote transaction and writer/bypass policy separately.

Positive writer ACK is not sufficient. The adapter independently reads the
original J and its complete Host-backed Archive history, exact parents, tree,
original Task Record/Contract, and unchanged direct Issue ref. It **rechecks
the same held quiescence lease, Attach and terminal Host facts after that
readback completes**, before returning PUBLISHED. Losing ownership during
readback preserves the original intent and returns UNCERTAIN rather than
claiming fully qualified success.

## Unknown acknowledgment, races, and positive recovery

On a lost ACK, false response, timeout or changed ref, a durable intent
remains and the operation is UNKNOWN. Do not force-push or retry with
a different nonce or reconstruct J. On process restart, the original
receipt selects readback-only: the Host reads the *original* J and proves
it is current Archive tip or reachable as a validated ancestor of a
later compatible two-parent aggregate; exactly (A,C) parents, exact
union tree, original Task Record pointer, unchanged current active C
and full original Issue history must validate. Only then return RECOVERED;
this fact is not a new Task grant or permission for ref deletion.

If another Issue wins concurrent Archive CAS and advances A to A', a
stale ordinary push fails. If an old attempt is UNKNOWN, *no* new
candidate is permitted merely because A' is now visible. A separately
Human-authorized new A' operation is only valid once the trusted Host
positively proves original J was **not** published and can account for
the full prior durable intent. It then freezes a fresh independent attempt
key. No automatic unbounded retry/rebase, competing rewrite or duplicate
same-Issue archive pointer is supported.

This source-only Host adapter never deletes the Issue ref and never
invokes v1 Worktree cleanup. Archive retention and destructive ref
deletion belong to distinct Phase 2D and Phase 3 gates.

## Qualification and explicit limits

The disposable local Git tests exercise:

- Positive merged-PR and separately approved cancelled/superseded paths
  with no active Session or unique unpreserved Product work.
- Two Issue histories and exact two-parent Archive DAG; original OIDs
  survive later Archive merges and fresh clone readback.
- PR merely closed, wrong Record/Contract/identity, missing Human approval,
  unknown/active Session, unsettled writer or unique Product data fail closed.
- Durable candidate before Host writer, same A,C competing operations,
  lost ACK before/after effect and readback-only recovery, plus a lost
  quiescence lease during final readback that returns UNCERTAIN.
- Stale Archive CAS/race, compatible later J ancestry, changed/symbolic
  Issue source, journal corruption/partial, changed Attach/quiescence owner.
- No Product HEAD/index, dirty/untracked owner files or v1 ref edits.

All local bare Git callbacks are **synthetic trusted Host fixtures**. They
do not prove genuine GitHub atomic updateRefs, remote direct-ref type,
Ruleset retention, owner/bypass scopes or live #291 Main/Session behavior.

The source-only test/review approval does NOT authorize Commit/Push/
Draft PR/Ready/Merge, actual metadata ref creation/deletion, installation,
Task/model execution, release, Production or any other external effect.
Each is a separate Human decision.

Phase 2D: versioned #194 checkpoint exact historical resolution and
conditionally authorized #198 Issue-ref cleanup only after Phase 3 proof.
Phase 3: disposable REAL GitHub archive CAS/Ruleset and writer scope.
Phase 4: explicit named consumer opt-in. Old v1/GA remain unchanged.
