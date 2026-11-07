# KUMIKI #337 Phase 2D1 — versioned PR Checkpoint and exact archive reader

> **Current #337 policy supersedes the external Archive-protection gates below (2026-11-07).** KUMIKI Metadata v2 uses ordinary Git Archive/Issue branches. Neither GitHub Rulesets, Branch Protection, admin/settings changes nor dedicated Apps are required. Earlier retention-policy/bypass/negative-actor requirements in this historical phase document are **not active acceptance gates**. See [ordinary-branch contract](kagari-metadata-v2-ordinary-branch.md) and KUMIKI Issue #337 for the current contract. Preserve the original historical test results; do not interpret them as current ordinary-branch canary proof.

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
   repository, PR, comment and author IDs using **strict positive integer
   types**, not Python int/float or bool equality. Check exact string types
   for repository, comment kind and bounded body. The **Host-attested expected
   principal** is independent of capsule claims, before decoding.
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
I/O source**, including allocations and deadlines. Comment body and report
UTF-8 conversion must fail closed as `CheckpointRefused` for malformed
Python Unicode (including lone surrogate code points), never leak an encoding
exception that a pre-effect publisher might misclassify as uncertain.
Exact direct refs with
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
repository, Task branch and base. All Host-attested `PullFacts` fields must
have exact expected types (not Python truthy or int/bool-equal substitutes).
The historical Snapshot/Record/Contract
must already be retrievable. An initial checkpoint requires an absent
previous-Snapshot observation and no prior owned v2 checkpoint. When
adopting an existing initial checkpoint, its authenticated comment ID must
also precede **every** other owned v2 checkpoint ID; a later valid turn
must not break an idempotent initial read. Recheck the same invariant after
posting an initial checkpoint, treating a post-effect violation as uncertain,
never as permission to retry. Later turns/handoffs require the
**explicit previous comment ID**, original
previous Snapshot/subject/commit IDs, an **exact-read verified** initial
checkpoint, and a fully observed unambiguous current predecessor. Both new
posts and adoption must traverse the **full authenticated PR comment chain**
from the explicit predecessor to the initial comment, checking every comment
ID against its exact pinned Snapshot/subject/commit tuple and requiring
`previous_checkpoint.commits_after.state = "observed"` at every link.
Only an authenticated author's comment **starting with** the literal
`<!-- kagari-pr-checkpoint/v2` capsule opener is treated as an owned
checkpoint candidate; malformed first-line openers fail closed, while a
normal PR comment that merely **mentions** the format name is ignored.
**Additionally, every owned v2 checkpoint in the complete inventory** is
exact-read and checked, in chronological comment-ID order, against the
immediately preceding owned checkpoint. This enforces a **single linear
stream**, not merely a valid ancestor chain for the selected checkpoint;
forks, abandoned siblings, and out-of-order unrelated owned comments block
both new posting and adoption. The verified stream is capped at 64 owned
checkpoints: the **65th is refused before grant, journal or comment effect**,
while existing checkpoints remain eligible for idempotent readback. The
40-second absolute publication deadline is passed into every exact comment
and Metadata read. Nested Git-object and ref-observation callbacks receive
`min(inner_validation_deadline, publication_deadline)`, with pre/post-I/O
and completion checks. The trusted Host must honor the cutoff **before**
allocating or blocking; Python alone cannot preempt an uncooperative callback.
The final checkpoint comment must also have an ID **strictly greater than its
explicit predecessor**, both when adopting an existing comment and when
reconciling a newly posted comment. An inverse or equal ID rejects adoption;
after a posting effect the outcome is uncertain and must not be retried blindly.
Ambiguous, missing, cyclic, over-budget or unknown-observation histories
fail closed. **Only new posts** must name the latest confirmed predecessor.
No latest-tip substitution.

Before even reading an intent, the Host's stream lease must serialize the
(repository ID, Issue, PR, principal) stream, examine the **complete durable
checkpoint-intent journal across all keys**, and return literal True only
when every other key is positively reconciled with a verified original PR
comment; otherwise publication is refused. The lease must stay exclusive
through effect and final readback, including ambiguous effects. A Host
lease-release failure after confirmed readback, or after a possibly
persisted intent/effect, is **CheckpointUncertain**, even when the lease
returns a `CheckpointRefused` error. This also applies when the lease
release itself raises a `BaseException` such as `KeyboardInterrupt`
**after successful post and readback** or a possibly durable intent/effect,
even though ordinary `Exception` handlers would not intercept it. A
pre-intent interrupt still propagates without being mislabeled as an effect.
If the lease's context manager **suppresses** a body exception, the
publisher explicitly restores that
refusal or uncertainty; it must never silently return `None`. Only
genuinely pre-intent rejection remains `CheckpointRefused`.
A matching current-key prior intent may be reconciled
but never replayed. The Host
must authenticate and bound this cross-key scan itself; a simple in-memory
lock, single-key check, or advisory boolean without durable inventory is
not sufficient.

The Host grant must also return literal True. Re-observe the PR and all
comments, persist and fsync a create-once intent keyed by repository ID,
Issue, PR, principal and checkpoint key, and only then invoke the scoped post.
An existing journal intent must match **exact key set, value types and
values**; Python float/int/bool-equal aliases never count as matching
persisted identity or schema. A matching prior intent is **readback-only**:
if still unconfirmed, report CheckpointUncertain and do not repost.
A lost ACK after a successful post can succeed only through one exact
owned comment, original metadata readback
and stable comment inventory **and PR facts** at the final boundary.
The post-readback inventory must also retain every previously verified owned
checkpoint **with identical ID, author, repository and body**, rather than
accepting a changed but stable post-effect inventory. It must contain **exactly
one new owned key**, the intended checkpoint, and the resulting entire
owned stream must still form the same linear chain. A concurrent same-principal
checkpoint visible after the effect causes `CheckpointUncertain` and no blind
repost, even when all prior comments were preserved.
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
strict exact-comment and inventory identity types (including float-equal
numeric IDs), malformed Unicode in exact-read, inventory, report and
post-effect readback, harmless format-name mentions in ordinary comments,
malformed first-line capsule openers, post-success and post-intent lease
release errors, suppressed unknown-post and pre-intent context-manager errors,
pre-intent lease refusal, post-effect interrupt overridden by failing
lease release, release-time interrupt after confirmed readback, pre-intent
interrupt propagation, strict type and key-set journal reconciliation,
lost ACK both before and after post, pending unknown ACK under a different
key (stream guard), corrupted historical initial, adoption with a wrong
explicit predecessor, dangling intermediate comment ID, unknown
`commits_after` observation, original parent deletion or canonical-body
rewrite during posting, reverse chronological initial-comment
adoption, a valid later turn's idempotent initial adoption, post-effect
initial-order drift, sibling fork detection on existing adoption/new extension,
post-effect concurrent different-key insertion, strict Host PR fact types
(including `draft=1`), valid multi-turn linear idempotence, shared absolute
deadline propagation to Git objects/refs, expired or unbounded deadline
refusal, source-only 64-entry capacity gate before effect, final PR drift
during readback and symbolic archive refs.
Fixture-only ref deletion is test setup, **not a production deletion API**.

**tests/test_kagari_metadata_v2_pr_checkpoint_state_machine.py** adds a
dependency-free, *bounded transition model* using the same disposable SHA-1
Git and synthetic Host/RecoveryJournal fixture. It enumerates **25 traces**:
16 post-result × lease-release combinations (ACK, lost ACK before/after
posting, post-effect interrupt × normal/refused/interrupted/suppressing lease
exit); five pre-intent denial/interruption paths; and four second-checkpoint
outcomes after a confirmed initial checkpoint. An independent expectation
table checks exact result **type**, durable intent count and byte-for-byte
journal preservation across retries, visible original comment IDs and body,
returned checkpoint identity, post attempt count, safe same-key adoption, and
cross-key blocking while a previous effect remains unknown. Every trace
asserts no blind second post. This is deterministic bounded state exploration,
**not** Hypothesis/randomized property generation, an unbounded concurrency
proof, or verification of real GitHub transaction atomicity.

Remaining independent gates: source review and separate approval to
Commit/Push/Draft PR; exact-head CI; real disposable GitHub PR/comment
actor/readback tests and archive Ruleset retention/no-delete/no-non-FF
protection (Phase 3); #198 expected-old **conditional Issue metadata ref
deletion** with crash/race proof (not implemented here); optional immutable
historical comment-body attestation; opt-in #194 v2 Product Task/Session
and #291 Main integration; explicit Phase 4 activation/migration. Existing
v1 GA remains unaffected. Keep #337 open.

Passing local fixtures is NOT proof of production GitHub or Ruleset safety.
