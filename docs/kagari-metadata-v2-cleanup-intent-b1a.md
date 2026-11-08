# KUMIKI #337 — P3C/B1-A durable cleanup intent (source-only)

**State:** Host-owned admission and create-once cleanup intent are published
as source/test/docs-only Draft PR #388. The implementation is not activated,
installed or deployed. It performs no Metadata Git ref mutation, Issue branch
deletion, model/Task execution or remote cleanup effect. Publication of this
source-only PR is not runtime admission or approval to perform a delete.

**Base:** `main@ddabe2c4524d78c07afdfaba378378c2ac8ec37d`
(PR #387 P3C/B0 merged). Ordinary Git branches are the approved lifecycle;
no mandatory GitHub App, Ruleset, Branch Protection, admin privilege or
separate account.

## Why B1-A is distinct from B1-B

The disposable actual GitHub P3C-A canary proved that conditional Issue-ref
deletion can work after a true two-parent Archive J and that original
Records/Contracts survive a clean clone with both Issue refs absent.

However, the canary did not establish that the **real KUMIKI Host** can
decide current terminality, stop and hold all writers, acquire fresh Human
authorization, durably record the deletion attempt, or recover after
an unknown network ACK. B1-A addresses **only** authority requalification
and durable intent creation. It intentionally does **not** implement a
delete callback.

## Implemented local API

`tools/kagari_metadata_v2_cleanup_intent.py` contains
`CleanupIntentHost.prepare(op, archived_outcome)`,
`CleanupIntentHost.read_saved(op, archived_outcome)`, and typed,
non-transferable observations.

- Reuses `CleanupHistoryReader.inspect` to prove original Repository/Issue
  identity, immutable Contract/Record, original Archive journal and archived
  J in the current exact DAG. Require the active Issue ref to remain at C
  before creating the first intent.
- Reuses `AttachmentLease`, `ArchiveQuiescenceLease`,
  `RecoveryJournal`, and the Host-owned
  `read_terminal`/`prior_attempts_cleared`/`authorize` callbacks.
  `TerminalFacts` must be freshly observed and match the original immutable
  Record disposition, Host repository/Issue/Contract binding, stopped
  Session, quiescent metadata writers, preserved Product work and the
  corresponding merged PR or cancelled/superseded evidence.
- Under one held Host lease, re-read the Archive and Issue, repeat
  independent TerminalFacts checks, obtain a positive scoped Human
  cleanup grant, and recheck the facts again before creating an intent.
- Reuse the **existing #140 RecoveryJournal** create-once/fsync contract
  for two records in the same Host-owned journal directory: first a
  cleanup-intent-reservation, then the complete cleanup-issue-intent.
  Both stable keys derive from canonical Repository ID, Issue and immutable
  original C under separate fixed domains, never from a newly generated
  operation nonce or a later Archive tip J.
  Under one held Host lease, persist and independently read back the
  reservation BEFORE attempting the full intent. Each record binds the
  original Issue ref/C, archived J, original Archive operation A/C, Record,
  Contract, Product subject and attachment digest. The full intent also
  records the digest of fresh terminal facts. Both records are
  **local journal evidence only**, never transferable Git delete permits.
- Runtime types for the full current TerminalFacts dataclass, including
  the 64-character Human decision ID and optional fields on cancelled or
  superseded Tasks, must be validated BEFORE attempting either durable
  journal write. Historical terminal facts cannot substitute current
  Host-issued authority.
- A second prepare for the same C refuses if either reservation or intent
  exists, without a new Human grant or new intent attempt.
  read_saved is read-only and never retries. A reservation without a
  final intent is RESERVED_ONLY_UNRESOLVED and blocks new admission.
  With a complete intent, C still present yields PRESENT_UNRESOLVED;
  absent C with proven retained Archive history yields
  ABSENT_ARCHIVED_UNATTRIBUTED, which does **not** prove this Host deleted it.
  Moved C, absent original J or malformed reservation/intent fails closed.
- If reservation creation itself loses its ACK and leaves no durable file,
  the full intent was never attempted, and there was no Git deletion
  callback to have run. Re-creating only the local reservation is safe.
  If a reservation exists, any later incomplete/ambiguous full-intent
  creation is blocked until a separately authorized Host recovery step.
- No GitHub URL, Git ref deletion, push API or actual effect callback is
  contained in the module. Tests use only disposable local Git and
  a synthetic Host authority fixture.

## B1-B (later, separately approved scope)

Only after source review, CI and another Human gate:

1. Bind the existing installed/authorized Host to **one** exact
   conditional Issue-ref delete effect under the same held quiescence lease,
   with a fixed `--force-with-lease=refs/heads/kagari-metadata/N:C`
   and delete refspec. No broad refspecs or force overwrite.
2. Preserve a separate durable **effect attempt marker** before the first
   Git push. Distinguish a reservation, an intent-only record, and an
   attempted remote effect across process crashes. Neither
   PRESENT_UNRESOLVED nor RESERVED_ONLY_UNRESOLVED permits automatic retry.
3. Reobserve canonical Repository ID and current Issue/Archive OIDs
   immediately before and after the effect. A stale/moved C refuses;
   an absent C is acceptable only with the retained original J and
   exact historical Record/Contract proof. On no/unknown ACK stop,
   preserve attempt receipts and reconcile the original candidate, never
   regenerate or blindly repeat a deletion.
4. Run disposable synthetic cleanup-race / no-ACK / restart tests,
   then a separately approved bounded live Host canary if required.
   Remote GitHub mutation is *not* authorized by B1-A.

## Explicit limits

- A typed `TerminalFacts` object is not independently
  self-authenticating. It must be issued by the trusted Host after
  separately proving real Session/Task/Worktree/PR facts. The B1-A
  synthetic fixture does not establish that external authority.
- Persisted historical facts and the newly created intent do not
  grant current deletion authority on restart. Real cleanup requires
  revalidating the effect-time Host state.
- The ordinary Archive can still be force-rewritten or deleted by
  an unrelated external actor. This is the accepted #337 limitation,
  not a justification for introducing mandatory GitHub configuration.
- PR #388 is published as a nonclosing Draft with `Refs #337`.
  Keep Issue #337 OPEN, default GA/v1 unchanged, and PR #388 unmerged
  until separate exact-published-head CI/review and Human Ready/Merge
  approval. This source-only publication grants no runtime cleanup effect.
