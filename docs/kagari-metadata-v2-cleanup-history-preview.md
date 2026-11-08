# KUMIKI #337 — P3C/B0 Cleanup History Readback (merged read-only source)

**Authority:** Issue #337 ordinary Git branches decision (2026-11-07).
**Source baseline:** `main@f8bba7cdb55a0fa48ac194ee538724064226214e`.
**Status (updated after #387 merge):** P3C/B0 read-only source/test/docs merged
as PR #387 on 2026-11-08 at main@ddabe2c4524d78c07afdfaba378378c2ac8ec37d;
not installed or activated. No production GitHub Metadata-ref deletion, GA
release, or model effect. The earlier disposable GitHub P3C-A acceptance is
historical transport proof, not a current production Host grant. P3C/B1
source-only Host coordination was subsequently published in Draft PR #390
(R1 HEAD `2a925dc632d62b0e59bca9d563b17835cbb55422`); no real CLI
cleanup adapter or production activation is implied (see cleanup-host-preview).

## Minimal role

`tools/kagari_metadata_v2_cleanup_history.py` is a read-only
`CleanupHistoryReader`. It **does not** create/delete Git refs, upload Git
objects, decide terminality, take Host locks, issue grants, or generate a new
recovery journal. It uses the existing trusted repository/approved Issue/Task
binding, `RecoveryJournal`, `metadata_v2.Validation` and original Archive
journal written by `ArchiveHost`.

It checks:

1. The precise originally authorized ordinary Git `ArchiveOperation`
   (A,C,Record,operation ID) and `ArchiveOutcome` (J) must match a **persisted**
   original Archive attempt under the same repository ID, Issue and A/C key.
   It accepts only Archive journal v3 ordinary Git mode, never reinterprets
   legacy v2 retention-policy labels. A typed outcome alone is not proof.
2. Re-read the *current* direct Archive ref and fully validate its Metadata
   DAG. The **original** J must be reachable as an Archive commit, have
   exactly (A,C) parents, a true union tree and the original Record pointer.
   The current Archive may be later than J if J remains a validated ancestor.
3. Read original C's exact Record and Contract bytes, using the registered
   Product subject, immutable object hashes and frozen approved Issue title/
   body; do not substitute the latest Archive tip or another Issue.
4. Read the *current* direct Issue ref. Return either `PRESENT_EXACT`
   (same old C, so a separately authorized Host *may consider* a delete)
   or `ALREADY_ABSENT` (a separately persisted cleanup recovery may
   reconcile without another delete). Any moved ref, symbolic/unknown
   observation, changed Archive, missing original J or identity mismatch
   refuses. Check both refs again before returning.

The typed `CleanupHistory` is **only an observation**, not a delete
authority token. A consumer must not treat `PRESENT_EXACT` or an earlier
terminal Record disposition as automatic approval.

## P3C/B1 source coordinator and remaining runtime gates

The separate source-only CleanupHost was published in Draft PR #390. The
following requirements remain binding for the future trusted Host/CLI effect
adapter; source publication alone is not production qualification. Reuse the
existing Host ownership and #140 `RecoveryJournal` rather than introducing
another Task state model or permissions service.

- **Before delete:** the real trusted Main/Host must verify current
  Task terminality, Human decision, Product work preservation, no remaining
  Task/metadata writers, current Attach/Worktree and held quiescence lease;
  requalify immutable original Archive J and active C immediately before
  the effect. An old Archive journal is provenance, not a fresh grant.
- **Durable cleanup intent:** create-once/ fsync a *separate operation-specific*
  cleanup intent keyed by canonical repository ID, Issue C and archived J
  before the first external delete; preserve original expected-old C and
  the one exact target ref. Never reinterpret an Archive publication intent
  as a cleanup intent.
- **Git effect:** one existing-CLI authenticated ordinary Git delete with
  a pinned canonical `refs/heads/kagari-metadata/N` destination and
  `--force-with-lease=<ref>:<original-C>`, no wildcard/default/Product/
  Archive target, no force overwrite and no GitHub repository configuration
  changes. Use the pre-existing User CLI login; no GitHub App/Ruleset,
  Branch Protection or admin prerequisite.
- **Unknown ACK/restart:** read the *same* durable cleanup intent, inspect
  current exact Issue and Archive refs, then classify `DELETED`, `STILL_C`,
  `MOVED`, or `UNKNOWN` without constructing a fresh delete attempt
  or blind retry. When C is absent, prove its original Record/Contract and
  any historical PR checkpoint can still be resolved from J in a fresh clone.
- **Explicit limits:** ordinary Git does not protect Archive against
  unrelated external force-push or deletion. That is an accepted limitation,
  not a reason to add a required GitHub App/Ruleset or new generalized CAS.

## Acceptance and publication separation

P3C/B0 qualifies *only* read-only inspection and ref-state classification,
with disposable synthetic ArchiveHost/local Git tests. No source modification
is published without explicit guarded Commit/Push/PR approval; any P3C/B1
actual Host callback, real GitHub mutation, deploy, v2 activation or v1
migration has a separate Human gate. Keep #337 and parent #291 open.
