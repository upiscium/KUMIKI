# KAGARI Metadata v2 — Phase 2B2 opt-in Worktree admission

**Status: source-only candidate; not a production v2 Runtime.**
Metadata v1, installed GA v0.1.0 and the existing
native-v4-linked-worktree Runtime/Main/Task session remain unchanged.
This step only composes verified current Task authority with the established
#256 Attach lock and the #286 Git Worktree allocator.

## Exact opt-in / package identity

The new Host-only API is tools/kagari_metadata_v2_task_admission.py.
Its entrypoint is admit_worktree(...), **not a model runner or CLI**.
A trusted Host must explicitly supply V2Selection with profile
metadata-v2-linked-worktree, numeric repository ID/name, Issue and the
SHA-256 of the *actual held* #256 AttachmentLease receipt.
The entrypoint acquires the existing AttachmentGate hold_verified context,
checks the installed package and Project identity under its flock, and
does not interpret a plan value, open Issue or older v1 receipt as a
permission to use v2.

The opt-in package is an **additive source-only flake output**:
packages.x86_64-linux.kagari-metadata-v2-host, defined in
nix/kagari-metadata-v2-host.nix. It contains the complete imported Host
Python dependency closure, including v2 sources and existing verified
v1 Git allocator code, but **no bin directory, service, executable or
automatic startup**. The existing kagari-control Nix definition and
current GA outputs are not edited. Immutable Nix package output identities
change if source bytes change; existing #256 Attach receipt does **not**
silently become a new v2 package receipt. The installed v1 package identity
is still verified by hold_verified, with the v2 selection checked
independently by the trusted Host. Building this source add-on does not
install it or perform any archive creation.

Neither V2Selection nor BootstrapIdentity authenticates its own fields.
Only a trusted Host with a verified owner/Repo ID, current Git source,
selected Worktree/Issue, held Attach and exact **operation-specific Human
grant** may call this API. Credential management and live GitHub transport
remain separate. The mandatory legacy_issue_absent(request) Host callback
must confirm from the authoritative v1 task/assignment state that **this
exact Issue is not already assigned** to v1. Missing/ambiguous/failed
observations must return false or raise; no second Issue task identity is
silently admitted.

## Current Task authority (not historical checkpoint read)

CurrentTaskReader(identity, registered, approved, reader=..., observer=...)
has independent, Host-attested:
- RepositoryIdentity: numeric repository ID, canonical owner/name, original
  pinned SHA-1 v2 genesis;
- RegisteredIssue: Issue, Product branch/base, exact approved current
  Record ID and Contract ID;
- ApprovedIssue: exact current approved Issue title and body;
- Host-owned bounded ObjectReader/Observer for the same Git repository.

Its observe() operation reads exact, direct
refs/heads/kagari/metadata and refs/heads/kagari-metadata/N.
Phase 1 MetadataV2 Validation verifies the canonical orphan-rooted
archive/Issue ancestry, object hashes, tree bytes, paths, resource limits,
and typed objects. The target Issue must not already be archived.
The **current Issue metadata ref tip's** tasks/N/record must point to
the expected exact Record ID; its Contract must be present and match the
registered Product branch/base and approved immutable Issue title/body.
Cancelled/superseded dispositions fail closed; changed Record or Contract
IDs are not implicitly adopted. Both refs are observed again before
returning a CurrentTask. This is not equivalent to Phase 2A
ReadOnlyMetadataV2Host.read(ExactLocator), which intentionally permits
historical records and may still return a valid old Record after
the Task has been cancelled.

CurrentTask is just a bounded observation, **not transferable
authorization** for Worktree creation, Session kickoff or future writes.
The caller cannot supply arbitrary Issue ref names or a latest-tip
shortcut, and no parser/codec/authority database is forked.

## Reuse #286 / #256; no competing allocator

V2WorktreeAllocator is a narrow, Host-only subclass of the existing
IssueWorktreeAllocator. The inherited Git Worktree logic remains exactly
the same: canonical Project and shared Git directory, Issue branch,
managed .worktrees namespace, no symlink/path spoof, exact Git registration
and backlink checks, same-Issue flock, no replacement of orphaned Product
branches, and preservation of partial Git state for reconciliation.

The inherited allocator's v1-only MetadataStore instance is used
**only** as a typed Project root/owner-name container, never to validate
v2 Task permission. Its _authority() call is overridden with a v2-only
check and runs **twice inside the existing Issue lock**: before and after
CREATE/RESUME. Each check performs all of:
- revalidate the held #256 AttachmentLease and fixed receipt digest;
- observe the **current** v2 Record/Contract/Issue metadata refs;
- require the trusted Host's positive legacy_issue_absent proof (cross-lane
  uniqueness) and literal-True exact Human operation authorization;
- reverify the Attach and current Issue metadata state after callbacks.

An initial preflight also rejects unauthorized/retired Tasks before
opening the Worktree allocator's lock. A post-create change in Task
authority returns a refusal, **leaves the existing Product Worktree and
branch intact** and requires explicit reconciliation; it never speculatively
deletes a branch or user data.

The old #286 IssueWorktreeCleanup rejects this subclass by exact type
and remains unchanged. Neither a confirmed CREATE nor RESUME grants
permission to delete a Worktree or any v2 metadata ref.

## Explicit non-goals and outstanding proof

- No v2-enabled RepositoryMain, Task/Session CLI, OpenCode launch, PR
  checkpoint writer, authoritative GitHub remote CAS/Ruleset or Archive
  publisher is implemented here. The current #291 Main and #163 Session
  still consume v1 MetadataStore; **passing a v2 Worktree to them remains
  unsupported** and must not be treated as success.
- This source-only Host adapter does not authenticate callbacks, prevent a
  malicious same-UID Host, prove multi-host consensus or eliminate ABA races
  in remote ref observation. It assumes trusted exact-ref observations,
  bounded readers, explicit Host operation grants, and server-enforced
  mutation permissions at the later effect boundary. Do not add a generic
  lock/daemon/queue merely for v2.
- A local Git fixture cannot prove GitHub server-atomic updateRefs, direct
  refs, or the archive Ruleset's no-delete/no-FF protection. Those are
  Phase 3 acceptance tasks; Phase 2C/2D own later metadata publication,
  true two-parent Archive merges, exact versioned checkpoints and protected
  conditional deletion; Phase 4 is a separate named consumer activation.
- The Host must reauthorize immediately before every later non-idempotent
  Session/Task/PR action in the appropriate phase. A lease held during
  Worktree CREATE is not a live Task execution permit.

## Acceptance / tests

tests/test_kagari_metadata_v2_task_admission.py exercises disposable
native SHA-1 metadata Git + #256 synthetic immutable installed Attach,
with the actual #286 worktree command path. It checks:
- positive two-Issue CREATE and RESUME with distinct real linked Worktrees;
- stale current v2 Record despite valid historical bytes, terminal
  dispositions, absent/symbolic/foreign refs, archive completion and
  changed requirement/Contract/base/branch;
- missing or mismatched Attach receipt; missing Host grant or uncertain
  legacy v1 same-Issue assignment; original product staged/dirty/untracked
  files, HEAD/index/other Worktrees and v1 metadata preserved;
- failure after Worktree allocation retains partial Git state;
- old v1 Task verification does not silently accept a v2-only Task.

Build/evaluate the additional Nix Host source package in an isolated
checkout and verify it can be imported from its output; confirm unchanged
original kagari-control and GA derivation paths. Run focused and adjacent
legacy v1/Metadata v2 regression tests and independent correctness/security
source-only reviewers. No user-owned repository, production Host, GitHub
metadata ref, real Task, model or persistent state is modified.

**Publication is separately gated:** Commit/Push/Draft PR requires explicit
Human authorization. Ready/Merge and any activation require their own
authorizations. Issue #337 remains open for 2C/2D/3/4.
