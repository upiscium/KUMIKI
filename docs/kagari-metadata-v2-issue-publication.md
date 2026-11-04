# KAGARI Metadata v2 — Phase 2C1 Host Issue Writer (source-only)

Status: additive, explicit Host-only API; **not packaged, installed, activated,
deployed, attached to Main, or backed by real GitHub CAS**. Existing v1
`MetadataStore`, `kagari-control`, GA v0.1.0, Tasks, Product Git and
`refs/agentcore/metadata` are unchanged.

## Implementation and selected authority

`tools/kagari_metadata_v2_issue_publisher.py` supplies
`IssuePublisher.publish(IssueWrite)` for ongoing first-lifecycle Issue-only
metadata publication. It delegates all canonical Git object construction,
validation, tree/history semantics, and positive core post-ACK readback to
existing `MetadataV2.publish_issue`. No new metadata schema or Git
transport is created.

The trusted Host must independently prove and supply:

- `BootstrapIdentity`: authenticated GitHub numeric repository ID, canonical
  owner/name, SHA-1 repository, and frozen verified Attach receipt digest.
- `RepositoryIdentity`: same numeric ID/name and the **original immutable
  independent orphan genesis** from Phase 2B1, not Product HEAD or latest
  aggregate tip. `RegisteredIssue` binds the exact first-lifecycle Record,
  immutable Contract, Product branch/base and Issue number.
- `ApprovedIssue`: current positive Host-approved Issue requirements, exact
  title/body and Product branch/base. An arbitrary GitHub Issue merely
  existing, a plan document, or a model output grants no publication.
- A live verified `#256 AttachmentLease` from
  `kagari_vnext_attachment_gate.hold_verified`, **held throughout** the
  operation; `IssuePublisher` rechecks it before preflight, callback,
  after grant, and after completion. A detached/repaired/changed package
  cannot inherit a previous writable grant.
- Bounded exact direct-ref observer and Git object reader bound by the Host
  to that same repository. They must enforce resource/time limits *before*
  network requests and allocating returned bytes, as Phase 1 requires.
- Private `#140 RecoveryJournal` owned by the trusted UID, and an
  operation-specific `authorize(IssueWriteApproval)` callback verifying
  Human approval, v1/v2 same-Issue exclusivity, Product subject and the
  exact byte/ID set. An operation ID or typed dataclass is **not**
  authentication. The Host is responsible for checking existing #291 Main
  owner / correct Task and preserving its existing locking semantics.
- A trusted `writer(Update,Bundle,deadline)` that verifies all exact ref
  types and *both* expected old ref commits in the remote transaction, stores
  the candidate objects first, and only then performs a **normal non-force**
  expected-old single-Issue ref publication. A GitHub updateRefs schema,
  local `git update-ref`, branch protection name, or a Python callback
  is not itself real GitHub CAS authorization. The callback may not expose a
  general Git runner/token to Task Leaves.

## One-shot object or Record publication

`IssueWrite` pins an operation-specific lower-case 32-hex ID,
`expected_archive`, `expected_issue`, a nonempty tuple of canonical
immutable `bytes` objects, and optional `next_record_id`.
Only `evidence`, `task-view-snapshot`, and `task-record` object types are
accepted here. The current approved Contract is immutable; reauthorization
to a new Contract/branch/base is not silently implemented by 2C1.

Before any JSON decoding, the adapter reuses the Phase 1 structural
container-marker scan: it refuses objects over 8,192 JSON containers,
rejects a request exceeding the cumulative configured container limit,
checks the existing byte/entry quotas, and bounds this pre-decode
admission pass by its own monotonic deadline. The core independently
revalidates canonical bytes and the complete Git tree/historical graph.
Over-limit requests are refused before any Human grant or Host ref effect.

An object-only write keeps the exact existing Task Record pointer.
For a Record pointer update, exactly one new Task Record must have the
same original approved Contract and branch/base, different content ID,
and a canonical `cancelled` or `superseded` disposition.
`record_update=(current_record_id,new_record_id)` is passed to
`MetadataV2.publish_issue` and the core validates its expected-prior
CAS. A generic `completed` or `running` Record state is forbidden.
Terminality of Product work still needs independent Phase 2C2 proof:
a cancellation Record alone is not permission to archive/delete.

Phase 2B2 `CurrentTaskReader.observe` pins the *current* Issue metadata
ref and its current Record+Contract, not a historical PR locator. The
writer's exact frozen operation must match the current Archive and Issue
ref heads before candidate publication. The core constructs an Issue
commit with the **single parent equal to the previous Issue tip**; no
archive merge, Product commit, or arbitrary ref is selected.

At the effect boundary, the Host adapter rechecks the held Attach, live
current authority/ref heads, and then authorizes the exact
`IssueWriteApproval` including candidate Commit OID and requested object
IDs. It rechecks once again after the grant before writing the durable
intent. The adapter checks the *original* core publication deadline
after potentially slow approval and current-authority reads, then again
immediately after the durable Journal fsync, before calling the Host writer.
Expiration before the Journal refuses with no effect; expiration after a
persisted intent returns uncertain and retains that intent with no ref effect.
CurrentTaskReader's independent per-read deadline does not extend the
original publication deadline. The Host's writer must repeat exact CAS
and grant checks during its own remote effect; Python preflight does not
eliminate TOCTOU.

## Journal and interrupted-effect semantics

The immutable `RecoveryJournal` key is domain-separated from bootstrap
and indexed by `(numeric repository ID, Issue, original expected Issue tip)`.
This is deliberate: **two writers against the same old Issue tip must
collide**, even when one supplies a different operation ID or object data.

An exact, bounded, create-once journal record persists repository/Attach/
Issue/Record/Contract identity, original Archive/Issue heads, nonce-like
operation ID, full object IDs and raw SHA-256 digests, optional new Record
ID, and the **original candidate Commit OID**. The existing #140
`RecoveryJournal.create` fsyncs file+directory **before** any Host ref
writer callback. The bounded journal is not a storage mechanism for
large Git bundles; the Host uploads candidate blobs/trees/commit through
its authenticated object store before remote ref CAS.

On any callback exception, false ACK, timeout or post-write readback
uncertainty, retain the original receipt and return
`IssueWriteUncertain`. Never choose a different nonce/Commit on the same
original expected Issue tip, overwrite the receipt, force-push or delete
the Issue metadata branch. A new Host process receiving the *same*
`IssueWrite` and original registration reads the receipt first:
it does **not** call `publish_issue`, `authorize`, or `writer`.
It independently validates a direct current Issue ref pointing to
the original candidate, the exact candidate's one-parent history,
the complete resulting tree (not merely requested object presence),
the prior and resulting Record pointers, original Archive ancestry and
ref readback. Only then return `RECOVERED`; a writer-confirmed first
call returns `PUBLISHED`. Historical confirmation of a retired Record
does not reinstate permission for new writes.

The archive is allowed to move **for a different Issue** after the
original effect. The old Archive tip must remain a valid ancestor in
the current Archive DAG. If the same Issue was archived, the Issue ref
is absent or advanced to another candidate, an observer is symbolic
or uncertain, or the Host cannot read exact objects, return uncertain
without replay. Refusal is not a license to abandon the original journal
or automatically issue a new Task operation against a stale head.
A positively proved CAS rejection and a new approved operation
requires a later separately reviewed reconciliation policy; none is
implemented here.

## Tests, packaging and remaining gates

`tests/test_kagari_metadata_v2_issue_publisher.py` exercises disposable
local bare Git and the existing synthetic #256 Attach fixture:
- Evidence append and historical Task View Snapshot with exact ancestor;
- subsequent distinct write from the new current Issue tip;
- initial/no-effect/after-effect lost ACK and negative callback, replay
  returning identical original candidate without a second write;
- same-head differing operation requests/IDs fail instead of creating
  competing candidates, including simultaneous writers under the same
  Host lease; same-Issue ref advancement prevents replay;
- expired approval or slow Journal fsync never invokes the external writer,
  while a persisted intent is preserved for explicit reconciliation;
- a positive writer ACK followed by corrupted Host Git-object readback
  is uncertain, not success, and an exact later read confirms the old
  candidate without rewriting;
- over-budget per-object/cumulative JSON container counts are rejected
  *before* the over-budget object is decoded; slow scanning expires within
  the bounded predecode phase before write authorization;
- Product HEAD/index/staged/dirty/untracked data and v1 ref untouched;
- valid Human-owned cancelled Record update, immutable Contract preserved,
  later active Write denied; foreign Issue/repository/object/Contract rejected;
- Archive advance for a different Issue after the effect, with original
  candidate independently verified on replay.

All synthetic callbacks simulate a trusted server. They are **not real GitHub
direct-ref, atomic updateRefs, Ruleset or deletion evidence**. Read-only
Nix flake checks and existing Runtime/Main/v1 regression tests must pass.
No Nix output is changed by this source-only addition; including it in
a future immutable v2 Host package is a separate packaging/activation
decision and changes that package's source identity.

Phase 2C2 separately owns verified Product/Human terminality, quiescence,
true two-parent Archive merge Host authorization and durable unknown-ACK
reconciliation. Phase 2D owns versioned checkpoint integration and
conditional Issue-ref deletion. Phase 3 requires a separately authorized
disposable REAL GitHub repo with demonstrated archive no-delete/non-FF
Ruleset and writer scope; Phase 4 requires explicit consumer opt-in.

**Separate Human gates:** source-only implementation/tests/review is not
Commit/Push/Draft PR, Ready/Merge, installation, live ref mutation,
real Task/Model operation, destructive cleanup or Release authority.
Issue #337 remains OPEN.
