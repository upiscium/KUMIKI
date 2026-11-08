# KUMIKI #337 — P3C/B1 Exact-Lease Cleanup Host (source-only preview)

**Review base:** merged #387 / `main@ddabe2c4524d78c07afdfaba378378c2ac8ec37d` (historical PR base, not the current moving main).
**Source publication:** P3C/B1 source/test/docs were published for review through nonclosing PR #390 (opened as Draft). Consult the PR for its current lifecycle, HEAD, CI and review evidence; this source document does not assert those moving values or a local publication state.
**Runtime:** NOT installed/activated. No production GitHub Metadata ref mutation, provider I/O or model/Task execution has been performed by this source-only implementation.

## Boundary

`tools/kagari_metadata_v2_cleanup_host.py` composes the already merged
P3C/B0 `CleanupHistoryReader`, existing #140 `RecoveryJournal` and
a Host-held Attachment + Quiescence lease. It does **not** implement an actual
GitHub CLI transport, an installation trigger, a Task owner, a second
authorization service, or any runtime auto-wiring.

One fixed `CleanupOperation` binds original ordinary-branch Archive A/C/J
and immutable Record to a separate cleanup ID and deterministic C/J-scoped
create-once journal key. The original Archive publication journal and new
cleanup intent use distinct operation namespaces in the SAME private
`RecoveryJournal` directory. Neither may be substituted for the other.

For a first effect:
1. Revalidate Attach and acquire the caller's live, held Host lifecycle lock.
2. Check B0 historical Archive/Record/Contract provenance, require **current
   exact C** on the direct Issue ref and original J reachable through the
   present Archive. Old terminality is *not* a new grant.
3. Obtain **current** independently authenticated Task/Product/Session/Writer
   `TerminalFacts`, reconcile original route and immutable merged-PR/
   replacement identity, and check no unpublished Product work, a stopped
   session and quiescent writers. A Task/LLM boolean cannot supply these
   capabilities.
4. Acquire a **Host-owned Human grant lease** that serializes cleanup grant
   revocation with the external ref effect. Obtain a fresh cleanup-specific
   Human decision **under that held lease**, scope-bound to repository numeric
   ID, Issue ref, expected C, archived J and operation ID. Compare the
   cleanup decision against BOTH the current terminal decision ID and the
   persisted original Archive J decision ID; neither may be reused, even
   when those historical/current IDs differ. Revocation must use the *same*
   synchronization domain; a boolean check without exclusive serialization
   is inadequate.
5. Requalify both held locks, current history, terminal observations, prior
   cleanup attempts and grant before and after create-once, file+directory
   **fsynced cleanup intent**. After an attempted journal create, any error is
   uncertain, never a new implied permit.
6. Invoke **once** the caller's pinned `CleanupDeleteRequest` callback, then
   demand exact readback proving C absent with J still reachable, keeping
   **both** Host locks held throughout effect and readback. A `DELETED`
   response is only returned after both Host contexts exit without error;
   neither context may suppress a body or nested lock-release exception.
   A post-intent release failure is uncertain and must be reconciled from
   the saved cleanup intent, with no second deletion attempt.

## Actual effect adapter remains a separate gate

The trusted CLI adapter must verify the signed-in CLI account and repository
identity, and must implement one ordinary Git deletion for exactly
`refs/heads/kagari-metadata/N`, using
`--force-with-lease=<exact-ref>:<original-C>` without wildcard, default
branch, Product branch, Archive branch, force rewrite or automatic retry.
Merely receiving the typed request does **not** prove that a supplied
callback correctly implements the lease, terminal authority or grant.
There is NO live adapter in this preview.

No GitHub Ruleset, Branch Protection, App, admin setting, second principal
or new token is required by design. External Archive deletion/force rewrite
is outside KUMIKI's ordinary-branch durability guarantee.

## Restart / unknown ACK

When the deterministic cleanup key is already journaled, the Host bypasses
lock-acquisition, terminal callback, Human authorization and deletion
**entirely** and returns only an observation:

- `DELETED`: Issue ref absent and exact historical Archive still valid
  (observed state, not proof of which actor deleted it).
- `STILL_C`: Issue ref still has the original expected-old C.
- `MOVED`: direct Issue ref changed, with J independently rechecked.
- `UNKNOWN`: original Archive missing/rewound, symbolic or unreadable ref,
  inconsistent provenance, or other ambiguous observation.

An existing intent with a different cleanup ID or provenance refuses rather
than being replaced. In particular `STILL_C` never triggers a blind retry.
A new independently authorized recovery protocol would be required for any
new external attempt after an unknown outcome.

## B1-A/B1 cross-protocol admission (not yet implemented)

Merged #388 introduced `CleanupIntentHost` (B1-A), which persists an
**original-C-scoped reservation** and a separate prepared-only intent. The
B1 `CleanupHost` in PR #390 instead owns a **C/J-scoped cleanup intent** and
an injected one-shot delete callback. These journal keys and APIs do not
implicitly inspect or exclude one another, even though both use the same
`RecoveryJournal` directory. Neither a B1-A prepared receipt nor a B1 saved
intent is a transferable permission to delete.

Before any live Host wiring, `prior_attempts_cleared` must inspect **both**
protocols under the **same real lifecycle synchronization domain**, refuse
unresolved reservations, ambiguous writes and unknown effects for the same
repository/Issue/original C, and require an explicitly approved recovery or
handoff. The shared state machine must reconcile B1-A's separately planned
effect-attempt marker with B1's conservative intent-first/no-retry behavior;
neither side may silently replace receipts or retry a remote effect. Mixed
protocol-order, concurrent admission, crash/restart and moved-J/C regression
tests are still required. Source-only coexistence is **not** production
cross-protocol exclusion or permission to activate a deletion adapter.

## Tests and acceptance separation

`tests/test_kagari_metadata_v2_cleanup_host.py` exercises local disposable
bare Git only: exact deletion using expected-old update-ref, saved intent
before effect, repeat read-only recovery, stale/moved ref, Archive rewind,
unknown ACK both sides of effect, invalid Human/Task/lock authority,
cross-attempt collision, and poisoned saved intent.

The local fixture's `git update-ref -d <ref> <old>` is **not** a proof of
the future CLI remote `--force-with-lease` adapter. The R1 source checkpoint
passed **76/76** local tests, **15/15** GitHub CI jobs and **16/16** PR checks,
with independent Correctness/Security source-only reviews PASS. The subsequent
docs-only R2 also passed exact-HEAD CI. A synthetic latest-main source snapshot
passed **97/97** combined B1-A/B1 tests, but that suite did **not** exercise
cross-protocol reservation handoff or prove a real deletion adapter. The exact
published SHAs, CI runs and independent review evidence are in PR #390.

Remaining gates: verify/implement the real trusted CLI adapter and live Host
Task/Session/grant provenance, integrate #140 recovery/cleanup scheduling,
reconfirm historical versioned PR checkpoints after actual delete via fresh
clone, and run a separately authorized disposable GitHub canary with independent
correctness/security review. The PR is nonclosing, and separate Human decisions
are required for subsequent source publication, Ready/Merge, and optional runtime
activation. Preserve #337/#291 OPEN and v1 behavior unchanged.
