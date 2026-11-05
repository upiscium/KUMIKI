# KUMIKI #337 Phase 2D1 — versioned PR Checkpoint and exact archive reader

**Status:** source-only, isolated candidate. No Commit, Push, PR, activation,
GitHub publication, real metadata-ref mutation, Task/model, install, release,
or #198 conditional deletion is authorized by this phase.

## Purpose

The new module **tools/kagari_metadata_v2_pr_checkpoint.py** is an opt-in
successor to existing #194 agentcore-task-checkpoint/v1, **not a modification
of it**. The v1 codec, old comment IDs, refs/agentcore/metadata, native
collaboration facade, and v0.1.0 GA behavior remain untouched. The new
capsule has a distinct kagari-pr-checkpoint/v2 marker and domain-separated
SHA-256 over canonical, closed JSON. It refuses v1 bodies rather than
guessing their version or converting a locator in place.

A v2 capsule binds the exact v2 version; Host-attested numeric repository ID
and canonical owner/name; Issue, PR and principal IDs; Product/base branch
refs and exact Product subject; original 40-hex metadata_commit; 64-hex
Task Record, Contract and Snapshot IDs; checkpoint boundary, unique key
and immutable report. The actual PR conversation **comment ID** is obtained
from Host-authenticated facts and is never guessed from the body. The Host
also pins the expected principal independently; internal agreement between
comment author and capsule principal alone is not sufficient.

The capsule is a locator, not Task authority. The public SHA-256 marker
proves byte consistency, **not** authorship or immutability of a GitHub
comment. An original body digest pinned in a separately trusted receipt
is required to detect a malicious author editing and re-encoding a valid
body. This source-only phase does not claim that protection.

## Exact archived-history reader

The **ExactCheckpointReader.read(pr_number, comment_id)** API has no latest
selection and no write, archive or delete method.

1. Fetch the exact positive PR comment ID. Verify the authenticated numeric
   repository, PR, author, **Host-attested expected principal**, comment type
   and bounded body before decoding.
2. Decode only v2; reject v1, noncanonical base64/JSON, duplicate/extra keys,
   mismatched digest, altered display report and foreign namespace.
3. Bind the capsule to a Host-attested historical Issue registration and
   Product base/branch. A newer Task Record pointer cannot replace the
   recorded historical Record/Contract identity.
4. Reuse ReadOnlyMetadataV2Host to read the **same original commit** for
   Record, Contract and Snapshot. The v2 core independently validates the
   full bounded archive/Issue DAG, original Git object bytes and OIDs,
   Record pointer, Snapshot authority ancestors, Evidence and prior-Snapshot
   graph. The Snapshot must match the initial-draft PR facts, Product HEAD,
   exact PR, branch, subject and boundary. Later PR closure or merge does
   not invalidate those historical facts.
5. Re-fetch that same comment ID and require identical typed provenance and
   body. After Issue ref deletion, the original commit stays reachable by
   the protected two-parent archive. Archive and Issue tips are **reachability
   roots only**, never replacement checkpoint versions.

All object reads and comment inventories must be bounded **at their Host
I/O source**, including allocations and deadlines. Exact direct refs with
positive absence proof are mandatory. Repeated observations are not atomic
CAS or server-side protection. SHA-1 Git object format only in this slice;
metadata content IDs are SHA-256.

## Source-only #194 publisher adapter

**SourceOnlyCheckpointPublisher** is injectable and is **not included in
active KAGARI packages or instantiated by production runtime**. Its only
capabilities are explicitly supplied by a trusted Host: current PR
observation, complete bounded comment inventory, exact comment read,
Human/effect authorization, one scoped post-comment callback, durable
#140 RecoveryJournal create-once intent, and a **Host-owned exclusive stream
lease**. No token lookup, network client,
arbitrary Git command, Metadata ref write or Issue ref deletion is provided.

A candidate PR must be open/draft, with the exact published head, author,
repository, Task branch and base. The historical Snapshot/Record/Contract
must already be retrievable. An initial checkpoint requires an absent
previous-Snapshot observation and no prior owned v2 checkpoint. Later
turns/handoffs require the **explicit previous comment ID**, original
previous Snapshot/subject/commit IDs, an **exact-read verified** initial
checkpoint, and a fully observed unambiguous current predecessor. Both new
posts and adoption validate the explicit predecessor and historical binding;
**only new posts** must name the latest confirmed predecessor. No latest
substitution.

Before even reading an intent, the Host's stream lease must serialize the
(repository ID, Issue, PR, principal) stream, examine the **complete durable
checkpoint-intent journal across all keys**, and return literal True only
when every other key is positively reconciled with a verified original PR
comment; otherwise publication is refused. The lease must stay exclusive
through effect and final readback, including ambiguous effects. A matching
current-key prior intent may be reconciled but never replayed. The Host
must authenticate and bound this cross-key scan itself; a simple in-memory
lock, single-key check, or advisory boolean without durable inventory is
not sufficient.

The Host grant must also return literal True. Re-observe the PR and all
comments, persist and fsync a create-once intent keyed by repository ID,
Issue, PR, principal and checkpoint key, and only then invoke the scoped post.
A matching prior intent is **readback-only**: if still unconfirmed, report
CheckpointUncertain and do not repost. A lost ACK after a successful post
can succeed only through one exact owned comment, original metadata readback
and stable comment inventory **and PR facts** at the final boundary.
Conflicting same-key content, duplicate keys, an invalid initial record,
bad metadata, missing grant, stream-wide unresolved intents, drift and
unclear effects fail closed.

This does not provide multi-host consensus or atomic expected-absent GitHub
comment creation. Neither the comment capsule nor the #140 journal is an
execution grant. Effect-time authentication remains the trusted Host's duty.

## Verification and remaining gates

**tests/test_kagari_metadata_v2_pr_checkpoint.py** uses disposable SHA-1
bare Git and fake GitHub comment callbacks. It covers codec tampering and
type denial, exact PR comment IDs/provenance, later Record revisions,
two-parent Archive with multiple Issues, synthetic Issue ref deletion and
**new bare clone** historical readback, explicit previous checkpoint,
idempotent publishing, wrong PR head, denied grant, journal conflicts,
lost ACK both before and after post, pending unknown ACK under a different
key (stream guard), corrupted historical initial, adoption with a wrong
explicit predecessor, final PR drift during readback, and symbolic archive
refs.
Fixture-only ref deletion is test setup, **not a production deletion API**.

Remaining independent gates: source review and separate approval to
Commit/Push/Draft PR; exact-head CI; real disposable GitHub PR/comment
actor/readback tests and archive Ruleset retention/no-delete/no-non-FF
protection (Phase 3); #198 expected-old **conditional Issue metadata ref
deletion** with crash/race proof (not implemented here); optional immutable
historical comment-body attestation; opt-in #194 v2 Product Task/Session
and #291 Main integration; explicit Phase 4 activation/migration. Existing
v1 GA remains unaffected. Keep #337 open.

Passing local fixtures is NOT proof of production GitHub or Ruleset safety.
