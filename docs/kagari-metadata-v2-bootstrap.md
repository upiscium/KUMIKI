# KAGARI Metadata v2 — Phase 2B1 Host bootstrap (source-only)

Status: isolated, explicit-opt-in Host capability candidate. **Not installed,
packaged, activated, migrated or connected to Main.** Existing v0.1.0 GA and
the ordinary v1 refs/agentcore/metadata lane remain unchanged.

## Scope and authority

tools/kagari_metadata_v2_bootstrap.py composes the reviewed Phase 1 MetadataV2
validation/publication API and the existing #140 RecoveryJournal create-once,
fsync-before-effect primitive. It does not create a second Task database,
own a GitHub token, invoke Git/GitHub itself, infer authorization from an
open Issue, or modify any Product file, branch, Worktree or runtime.

The trusted Host must independently supply all of the following:
- Authenticated numeric repository ID and canonical owner/name.
- Verified #256 Attach/Runtime binding, represented by its frozen SHA-256
  attach_digest for an explicitly selected v2-only profile. An ordinary
  v1 attachment does not by itself authorize v2 activation.
- Exact SHA-1 Git object repository, bounded ObjectReader, exact direct-ref
  Observer and atomically expected-absent non-force ref writers.
- A private, durable RecoveryJournal directory shared by cooperating Host
  writers for that repository, plus operation-scoped explicit authorization.
  The Python types and a caller-supplied repository ID are not authentication.

Types: BootstrapIdentity, ApprovedIssue, ArchiveCreate, IssueCreate,
ArchiveResult, IssueResult, BootstrapBlocked and BootstrapUncertain.

BootstrapIdentity(repository_id, repository, attach_digest, git_object_format)
is an attested Host statement. ApprovedIssue freezes one approved Issue number,
the Product branch, base revision, requirement title and body. The Host must
prove their identity and #286/#191 Worktree/Task/Contract authority; simply
finding an open Issue is insufficient.

## Archive: doctor() and install()

doctor() is read-only. Without a Host receipt and with an exact proven
absent refs/heads/kagari/metadata, it returns ABSENT. Foreign/unowned refs,
invalid/symbolic/unknown ref observations and changed receipt identity block.
With a matching receipt and provably direct archive ref, doctor validates
the entire bounded archive history against the same original genesis and
re-observes the ref before READY. A persisted intent but absent ref is
UNKNOWN, not authorization to produce another genesis.

Explicitly authorized install() follows these steps:

1. Observe the exact archive ref and require proven absence; no Git DWIM,
   prefix match, local checkout or Product ancestry is accepted.
2. Construct one independent orphan genesis with an empty tree and zero
   parents through Metadata v2 construct({}, (), nonce=frozen).
3. Create and fsync an immutable, create-once #140 journal record binding the
   generated nonce, candidate Git Commit OID, repo ID/name, attach_digest,
   version=2 and operation. Journal key is stable by numeric repository ID:
   competing or changed identities cannot silently create alternate receipts.
4. Only then invoke the trusted Host create_archive callback with an exact
   ArchiveCreate request (ref=refs/heads/kagari/metadata, expected_old=None,
   candidate=<original genesis>) and the candidate Git object bundle. The
   Host is responsible for genuine non-force expected-absent remote creation,
   direct-ref checks and repository capability grants.
5. A positive callback Ack alone does not prove success. Independently
   reobserve the archive and read back its original Git objects through
   the Host, proving exact genesis, empty orphan tree, same root/history and
   unchanged direct ref. Otherwise retain the journal and return uncertain.

An identical repeated install never regenerates the nonce or blindly retries.
It reconstructs the same candidate from the saved nonce, validates the
archive, and returns READY without a new write. The archive may later have
two-parent v2 merge commits; validated history still roots at the same
immutable independent genesis. A foreign archive is never adopted.

## First Issue lifecycle: register(ApprovedIssue)

Target is the exact refs/heads/kagari-metadata/<issue>, **not** Product branch
and **not** the v1 metadata ref.

The operation requires a verified READY archive, valid explicit approved
Issue snapshot and authorize_issue(IssueCreate). It derives canonical #191
Contract and initial Task Record through existing encode_contract/encode_record.
Initial Record ID/Contract ID, Issue, Product branch/base, attach receipt,
archive genesis/tip and candidate Commit are recorded in a create-once journal
keyed by repository numeric ID plus Issue number. The journal entry is
persisted before the Host writer is invoked.

DO NOT create a placeholder Issue metadata ref pointing directly to genesis.
Phase 1 deliberately rejects that tip as a valid Issue history. The first
Issue Commit must *already* contain the initial Task Record pointer and
Contract, have exactly one parent (the preserved genesis), and be created
with an expected-absent ordinary, non-force ref publication. The existing
MetadataV2.publish_issue validates the candidate tree, history and content
before invoking the Host's injection point; Bootstrap checks the exact
Update/Bundle and persists candidate intent before passing control to the
Host callback. The Host must still recheck remote archive/Issue refs and Task
authority at the effect boundary.

On a repeated operation, a matching receipt allows only pinned readback
of the original candidate, current ref, Record/Contract and archive ancestry.
It must never regenerate a new candidate or replace a changed ref. A newer
archive may incorporate *other* Issues; the receipt's original archive tip
must remain within the validated archive history. If the Issue was archived,
reopened with a conflicting epoch, had a different title/body/branch/base,
or its metadata tip changed from the original first Commit, registration is
not silently resumed. Such future lifecycle/writer behavior requires a
separate phase. No Worktree or Main Session is created by register().

## Interrupted effects and failure handling

BootstrapBlocked means invalid/foreign/unsupported/unauthorized state; no
success is inferred. BootstrapUncertain means the ref operation may have
happened or an immutable attempted intent exists without confirmed ref.
No automatic retry, destructive cleanup, force push, ref rewrite or new
candidate is allowed. A crash between creating the journal and calling the
writer deliberately leaves an UNKNOWN state until reconciliation.

The Host's RecoveryJournal is owned by the trusted UID; its file mode,
hardlink/symlink restrictions, canonical JSON and fsync-before-effect behavior
are inherited from existing #140 source. It must be shared across all
cooperating writers. Multi-host exclusion, remote CAS, ABA prevention and
Ruleset enforcement are **not** proved by this adapter. Limits/deadline
come from Phase 1 Validation, and Host callbacks must themselves enforce
pre-allocation/timeouts.

## Deferred phases / compatibility boundary

Phase 2B1 does not edit or activate the current #256 runtime, Attach receipts,
#286 IssueWorktreeAllocator, #291 Main, v1 MetadataStore, Nix package, #194
collaboration or any GA runtime. They still consume v1 and cannot be handed
this v2 object as an in-place replacement.

Phase 2B2 separately owns real opt-in Host/Runtime package identity, explicit
profile selection and v2-aware Worktree admission. Immutable package changes
can invalidate old Attach receipts and must not be silently repaired/cut over.
Phase 2C owns terminal/no-writer archive merges, writing beyond first Issue
Commit and unknown-ACK reconciliation. Phase 2D owns PR versioned locators
and eventual conditional Issue ref deletion; delete remains blocked until
server-enforced archive no-delete/non-FF protection is demonstrated.
Phase 3 must use a separately authorized disposable real GitHub repository
and validated Ruleset, full remote race matrix and fresh-clone checkpoints.
Phase 4 is an explicit named consumer activation decision; no GA migration.

tests/test_kagari_metadata_v2_bootstrap.py uses disposable local bare SHA-1
Git fixtures and synthetic callbacks, including two Issues, lost Acks,
idempotency, no blind retries, collision refusal, and preserved staged/dirty
Product plus v1 ref. Local hooks/update-ref are **not** real GitHub proof.
Publication of Phase 2B1 source needs separate Commit/Push/Draft PR approval,
and later independent Ready/Merge authorization. #337 remains OPEN.
