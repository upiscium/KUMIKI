# KUMIKI #337 — P3C/B1-B one-shot cleanup effect contract

**Status:** Local-only source/test/docs prototype on
main@958fe5382a2481fbab8b637dbe17f005a23ac245 after nonclosing PR #388.
This code is **not installed, activated, deployed or wired to GitHub**.
No GitHub Metadata refs, remote repository settings or real Issue branches
are modified by the B1-B synthetic tests. Publication needs its own gate.

## Goal and safety boundary

The B1-A Host prepares two durable records in an existing private
RecoveryJournal: a reservation and a full original-C cleanup intent, after
fresh TerminalFacts, Human authorization and a held quiescence lease. Neither
record permits a delete. B1-B models the next effect-time gate, again held by
a trusted Host, using one exact scoped Git update and a recoverable journal.

The new source-only API is
tools/kagari_metadata_v2_cleanup_effect.py,
CleanupEffectHost.execute_once() and inspect(). It has NO built-in
GitHub HTTP transport, Git CLI invocation, deployment code, credential
lookup, activation path, model/Task dispatch or general-purpose ref writer.
A future installed Host, under a separate Human approval, must supply a
delete_exact callback guaranteeing ONE atomic direct-ref compare-and-delete
of exactly refs/heads/kagari-metadata/N at original C. The callback MUST
reject any moved, absent or symbolic ref; it must never fall back to blind
delete, arbitrary push refspecs or unrestricted force updates. A source-only
test callback uses git update-ref --no-deref -d <issue-ref> <original-C> in a
disposable local bare-Git fixture; it never calls GitHub.

## Normal one-shot operation

1. Re-read the original Archive journal and the current Archive J ancestor
   with the merged CleanupHistoryReader; verify original C, Record, Contract,
   canonical repository ID, issue, Product subject, direct ref C and
   unmodified Host attachment.
2. Require a complete previously fsynced B1-A reservation AND matching full
   intent. An absent, corrupt or foreign record fails closed.
3. Before taking an effect lock, fail if this fixed repository-ID/Issue/C
   effect marker already exists. Under the SAME held Host quiescence lease,
   recheck the original live ref, prior attempt reconciliation, fresh
   TerminalFacts, Product preservation, no writers, Session stopped and
   the exact new Human effect-time authorization. Do not reuse B1-A's Human
   decision as a transferable grant.
4. Reobserve the full original Archive history, intent, live Host facts and
   exclusive lease after authorization. Only then fsync/create-once the
   keyed cleanup-issue-effect-attempt record. Its state is
   MAY_HAVE_EXECUTED before any external effect can begin. Read this
   exact record back before calling any delete adapter.
5. Immediately before one scoped delete callback, repeat the same held
   lease, **prior-attempt clearance**, and current Host/Archive/Issue/facts
   checks after marker fsync, and verify a bounded deadline. After the
   final Host history, current-facts and intent reads, recheck prior-attempt
   clearance and the held quiescence lease **as the last effect-boundary
   checks**, immediately before the one delete callback.
   If clearance becomes unresolved during marker fsync or the following
   fresh-facts read, leave the marker in place, refuse the callback and
   report UNKNOWN without retry.
   The request binds repository ID/name, Issue, exact ref, expected old C,
   Archive J and current tip, durable intent/attempt keys and requires
   direct-ref equality.
6. Accept an effect success only after the trusted callback returns exactly
   True AND fresh Host readback proves the original C ref is absent while
   the original J and its immutable Record/Contract remain reachable
   from the unchanged Archive tip. Recheck **current terminal facts and
   prior-attempt clearance** after the delete, again before persistence
   of confirmation, and after the confirmation readback. Each boundary
   must recheck prior clearance and the live quiescence lease **after** its
   final potentially slow history/current-facts read, not merely before it.
   If Session, writers, terminal Human facts, prior clearance or the held
   lock changed during any of those reads, retain the attempt marker and
   report UNKNOWN; do not claim live authorization.
   A separate, fsynced cleanup-issue-effect-confirmation records the
   positive ACK + prior readback; it is historical evidence, not a
   promise of perpetual Session quiescence. Readback of a ref that is
   still present, changed, symbolic or whose J was lost yields UNKNOWN;
   never grant a new retry.
7. On interruption, journal ambiguity, lost ACK, lost Host lock, change of
   current Task or Archive, positive callback ACK without direct-ref absence,
   or failure to durably confirm success, return UNCERTAIN once the attempt
   marker might exist. Do not issue another deletion from this operation.

## Restart and read-only reconcile

inspect() may be called after a crash and does not acquire a new Human
effect grant, execute a Git ref operation or retry anything. It revalidates
the complete original B1-A intent and original-J/C archive history.

- INTENT_ONLY_REF_PRESENT: no effect marker was found, C is still current.
  This is NOT a delete permit.
- INTENT_ONLY_REF_ABSENT_UNATTRIBUTED: no marker found, ref absent with
  preserved Archive history. Identity of the deleting actor is unknown.
- ATTEMPT_UNCERTAIN_REF_PRESENT: durable effect marker exists, C remains.
  The effect outcome is unknown; repeated execute_once is refused.
- ATTEMPT_UNCERTAIN_REF_ABSENT_UNATTRIBUTED: marker exists, C is absent.
  Preserved archive history does not prove which actor caused absence.
  No retry or attribution based only on this observation.
- ACKED_ABSENT_ARCHIVED: only when an immutable confirmation receipt also
  exists, agrees with the exact effect marker and current readback still
  shows original J reachable and C absent. On subsequent ref resurrection
  or history rewrite, fail closed.

Persisted records are Host-owned local evidence, not tokens to empower
Leaves or an autonomous consumer to delete anything. The effect callback
must remain private to the trusted Host and be provisioned only after a
separate approval. A synthetic unit test is not proof that a live GitHub
transport implements exact direct-ref CAS atomically.

## Explicit limitations and follow-up acceptance

- This is the B1-B source/fixture contract, NOT a deployed owner, installed
  GitHub adapter or live acceptance. The real target's direct-ref and
  server-side expected-old semantics need their own bounded canary and
  independent verification.
- Existing GitHub Rulesets, Branch Protection, administrator settings,
  dedicated GitHub Apps and bypass roles are NOT required. Ordinary Git
  is the approved branch lifecycle. Unrelated external Archive force-push
  or deletion is an accepted limitation of the architecture.
- No automatic retry even if a previous delete may not have happened.
  A held lease prevents KUMIKI-owned competing writers but does not imply
  cross-system consensus. Operator-assisted recovery must explicitly
  reconcile the original attempt and may require a new Human gate.
- Retain the default v1 GA, v2 opt-in inactive, previous immutable
  checkpoint reads and Issue #337 OPEN. Do not auto-close this parent Issue
  for successful source publication or source-only merge.
- Before any publication: run isolated B1-B synthetic tests plus the
  existing B1-A, ArchiveHost and CleanupHistoryReader suites; independent
  Correctness/Security source review; later Source Guard
  Commit/Push/Draft PR and Ready/Merge each need separate approvals.
- Before any live effect: separate Human authorization for the exact
  repository, Issue C, Host, operation and ordinary-Git ref update,
  readback, recovery and disposable clean-clone proof. No live effect
  follows automatically from a source-only PR or this local prototype.
