# #337 Metadata v2 core — Phase 1, source only

Run: `KUMIKI-337-PHASE1-20261101-R1`.
Base: `985102cc5ef77f30cac4b03778ea16e59e95c160` on
`feat/337-phase1-v2-local`. No source staging, commits, publication, installation,
activation, GitHub writes, Task/model processes, or production ref operations.
Independent correctness/security review is performed separately by Coordinator;
author verification is not independent review or B2b qualification.

## Implemented slice

Two new modules, independent of the v1 MetadataStore:

* `metadata_v2.py`: pure Git object construction; bounded exact-object and
  history validation; Issue writer and exact-commit reader using injected Host
  callbacks. No subprocess, network client, credential loader, global Host,
  private v1 mutator, or replacement of `metadata_ref.METADATA_REF`.
* `metadata_v2_archive.py`: isolated archiver, separately injected terminal
  verifier and archive writer. There is deliberately **no deletion API**.

The persistent archive is `refs/heads/kagari/metadata`. An active Issue is
`refs/heads/kagari-metadata/N`, where N is canonical positive ASCII decimal
(at most 128 digits). The Host instance is bound to one exact, case-sensitive
`owner/name` repository identity and a separately provisioned immutable genesis
OID. Provisioning/pinning that identity and genesis is a Host responsibility;
metadata cannot attest to its own provenance.

`construct({}, (), nonce="genesis")` produces a pure in-memory orphan genesis
bundle. No ref is created by this function. Every validated root must be this
exact pinned commit with an empty tree and no parents. Product ancestry,
unrelated empty roots, nonempty roots and absent parents fail closed. A first
Issue commit is a child of genesis, **not** of the current archive tip. Later
active commits have exactly one parent in the same Issue lifecycle.

Objects retain the unchanged v1 canonical JSON envelope and domain-separated
SHA-256 content IDs. All four kinds are schema-checked through public decoders:
task-record, contract, evidence, task-view-snapshot. Only canonical object paths
and `tasks/N/record` pointers are admitted. Trees verify actual Git object hashes,
types, mode `100644`, sorted unique names, exact paths, repository/task/subject
bindings, pointer targets, Record→Contract links and bounded same-Task Evidence
supersession. A snapshot's authority/Evidence/previous-snapshot references must
resolve at their **exact validated ancestor commits**, not at a latest tip.
This is graph integrity, not live fact verification or authority issuance.

Active Issue trees may contain only their own objects and pointer, including
objects newly introduced at paths absent in the prior tree. Immutable objects
cannot disappear or change on any historical transition, even if later restored.
Pointers cannot disappear. `publish_issue` checks an explicit expected-prior
Record ID before changing its pointer. Typed Record optional disposition remains
only `cancelled` or `superseded`; terminal completion is not invented as a state.

An archive commit has **exactly two parents, ordered (archive A, exact Issue C)**.
Its tree must equal the validated strict union of A and C. Its first parent is
genesis or another qualified archive commit; its second parent is a linear Issue
commit. The Issue must have a Record. Re-archiving any Task already represented
in the archive, even with an identical pointer, is rejected. Ordinary merges,
three parents, reversed roles, omissions, overwrite/deletion, foreign paths and
Product parents fail. No automatic Git merge is run.

The exact-commit reader verifies ancestry from the observed active Issue ref or
archive, then reads that commit's own tree with required kind/ID/task/subject
bindings. It re-observes both refs and refuses changed/uncertain observations.
The old Issue OID remains readable after fixture-only branch deletion and a new
clone because it is a real second parent of the retained archive. Current tips
are reachability roots, never substitute checkpoint identities.

## Host interfaces and trust boundary

The new core does not pretend to implement production transport. Host callbacks
must be installed by trusted construction, not accepted from an agent or JSON.
Code using these modules must not expose writer construction as untrusted API.

* `ObjectReader(oid, max_bytes, absolute_monotonic_deadline)` returns
  `(Git_type, exact_raw_bytes)`. The Host must bound reading/allocating and respect
  the deadline **before** returning. Core verifies type, size and Git SHA-1 hash.
* `Observer(exact_ref, deadline)` returns `RefState(exact_ref, oid_or_None, True)`
  only for an unambiguous exact direct ref (or proven absence). Unknown reads
  raise. No prefix matching, refspec normalization, or tip-based identity.
* `Writer(Update, Bundle, deadline)` is a trusted authorization and mutation
  boundary, not a general push callback. Issue and archive writers are injected
  separately. The bundle contains exact in-memory Git objects, not a tree merge
  instruction. A narrow Host can store those objects without any product index.
* `Update` binds repository, genesis, task, exact destination, expected archive
  A, expected Issue C (or absence), candidate OID and archive retention policy ID.
  Host must validate provenance and authorization for these exact bytes and
  **atomically** check both refs' expected values and direct types, then update
  only the exact destination non-force. Issue candidates have parent C (or
  genesis for first creation); archive candidates have parents (A, C).
* An absent Issue ref does not prove a first lifecycle. Host must enforce a
  separately retained first-lifecycle reservation/tombstone, even if an active
  ref was deleted before archival. Core rejects reopen detectable in the archive;
  the fixture also models a Host reservation. Epoch/reopen semantics are absent.
* `archive_issue(... retention_policy=..., verify_terminal=..., archive_writer=...)`
  requires all three. The verifier receives the exact candidate-bound `Update`.
  It separately checks terminal Task and no active writers; a Record disposition
  is not proof. The archive writer must recheck/hold that verified provenance,
  writer exclusion, policy and both ref preconditions at the mutation boundary.
  A successful earlier verifier callback is **not** a lock or server proof.

Core validates the candidate graph before invoking a writer. Only literal `True`
Ack plus exact ref/object readback is success. Rejection, malformed result,
exception, timeout, absent/changed ref or unconfirmed objects raises
`UncertainPublication`. There is no retry or deletion. Even if the mutation
actually succeeded, an unknown Ack is not reported as success. Preserve the
Issue branch and reconcile the exact candidate externally. Confirmed archival
also does not grant production deletion permission.

### Limits

Default per-operation limits: 256 commits, 4,096 cumulative traversed tree
entries, 32 MiB unique read bytes, 32,768 decoded JSON container
markers, ten seconds. Before each **new** canonical object is decoded, its
actual JSON structural openings outside quoted strings are counted; more than
8,192 openings per object or the cumulative budget fails closed. Exact Git
Tree OIDs and canonical Metadata object IDs are decoded only once within a
Validation; repeated Commit nodes still independently validate their own
parent graph and snapshot ancestor links. This is a conservative structural
allocation budget, **not an OS RSS or arbitrary Python callback memory cap**. Configurable limits are capped
at 1,024 commits, 16,384 entries, 128 MiB and sixty seconds. Each raw Git object
is additionally capped at the codec's object limit (1 MiB + 4 KiB). Tree depth
is at most four; Evidence supersession at most 64 links. Construction is capped
at 4,096 files / 32 MiB and uses only fixed-depth allowed paths. Candidate
validation and readback share one budget/deadline; conservative exhaustion may
reject otherwise valid long histories. No persistent validation cache is trusted.

A Python callback cannot be preempted by this core. The Host must enforce I/O
timeouts, output/size bounds, cancellation and credential/policy isolation.
Post-return deadline checks alone do not qualify a Host. Resource exhaustion
after a mutation is uncertain, not a reason to delete or retry blindly.

## Explicitly not implemented / blocked outside this slice

* **BLOCKED: production Host/remote mutation adapter.** No real CAS, GitHub
  conditional deletion/ref-type primitive, ruleset change, credential path or
  server policy/protection is implemented or proven here. Same-OID symbolic-ref
  substitution requires a server transaction check; client observation is not
  enough. No archive deletion/non-FF guarantee against privileged bypass.
* **Not implemented: production initialization or deletion.** Genesis pinning,
  first-lifecycle reservation persistence, terminal/writer exclusion and retention
  enforcement require a separately approved Host. No fake default callbacks.
* **Not implemented: same-Issue reopen/epoch semantics.** Explicitly rejected
  for archived Tasks; erased unarchived lifecycles require Host tombstones.
* **Not implemented: SHA-256-format Git repositories.** This bounded adapter
  reads/constructs SHA-1-format Git objects only; full 64-hex metadata content
  IDs and the existing envelope subject grammar remain unchanged.
* **Not implemented: PR checkpoint version/locator integration, current live
  Task authority APIs or existing capability transport adapters.** Those stay
  Phase 2; v1 remains independently readable through its unchanged API/ref.
* **Not implemented: arbitrary Git commit extensions.** Signed/extra-header
  commits, tags, executable/symlink/gitlink entries, empty directories and
  alternate/replace/shallow-history interpretation are not supported. Exact raw
  objects, rather than a revision walker affected by replace refs, are validated.
* No production install/activation, GA v0.1.0 edits, release, B2b qualification,
  source Git commit/staging/push/PR, Issue writes or real Task/model processes.

## Isolated evidence and review handoff

`tests/test_metadata_v2.py` uses only disposable local bare Git repositories,
fixed file-protocol Git subprocesses with five-second per-command timeouts and
independent per-test setup. The source modules themselves never invoke Git.
Fixture-only object writes, local ref CAS, Issue deletion and new clone exercise
actual Git ancestry, not just a tree-shaped Python mock. A local `update` hook
rejects archive deletion/non-fast-forward through **receive-pack only**. Local
privileged `update-ref` bypasses that hook; fixture Host checks are intentionally
not presented as atomic server security. **This is not a real GitHub guarantee.**

Tests cover concurrent Issues, archival union, exact OIDs after branch deletion
and clone, genesis-derived later Issues, stale A/C writers, unknown Ack, refused
terminal verification/policy/callbacks, same-Issue reopen, cross-Issue insertion
at previously absent paths, conflicting Record updates, malformed schemas,
missing/foreign objects/parents/modes, invalid merges, historical mutation,
bounded resources, same-OID symbolic substitution, exact snapshot ancestor links,
and unchanged independent v1 ref. No test starts a Task/model process.

Author command evidence and final SHA-256 file digests are reported with the
terminal handoff; hashes cannot be self-embedded in this file without changing
its digest. Independent reviewers should assess the Host boundary obligations,
budget/callback limits, archive DAG classification and exact readback, without
interpreting the synthetic fixture as production authorization.

### Author verification results

Initial focused run: 24 tests, 8.888 seconds, OK. Subsequent source-only
hardening added mandatory non-null exact subject validation and bounded
snapshot ancestor-graph checks, with Evidence/snapshot tests.

Final focused run: **26 tests, 10.333 seconds, OK**. Actual command:

```sh
timeout 180s nix develop --offline --no-write-lock-file --no-update-lock-file --no-update-lock-file --command python -B -m unittest -v tests.test_metadata_v2
```

Execution disclosure: the final invocation accidentally duplicated the
idempotent `--no-update-lock-file` flag versus the approved exact command.
No network or source Git mutation was added; nevertheless this was an exact
command deviation and is not silently represented as the approved text.

**Adjacent regressions NOT RUN:** User rejected the requested bounded commands
for `tests.test_metadata_codec tests.test_evidence_v4`, `tests.test_metadata_ref`,
`tests.test_task_record_v4` and `tests.test_task_view_v4`. No equivalent command,
retry or redelegation was used. Their status is unverified, not PASS.
Independent correctness/security reviews remain pending Coordinator.

## R3 corrective source-only scope (2026-11-01)

Independent R2 Correctness and Security reviews required corrections for two
source paths: post-ACK readback reused candidate-derived tree snapshots, and
incoming `publish_issue` bytes were JSON-decoded before container admission.
R3 clears **raw-object, tree-snapshot, decoded-envelope and DAG caches** before
re-reading from the original Host object reader. A missing candidate tree or
blob must yield `UncertainPublication`, never a confirmed receipt or deletion.

Before the initial `codec.decode_object` on an incoming object, R3 now checks
canonical input bytes against the per-object JSON container count and a
running cumulative budget (including already charged immutable objects).
Identity-derived duplicate objects are not charged again; typed schemas and
exact immutable-byte checks still apply in snapshot validation. The quota is
a conservative structural allocation bound, **not a hard OS RSS limit**.

R3 regression evidence: the two previously untested failure modes were first
reproduced on the R2 source (both tests failed), then corrected. Four targeted
pre-decode cumulative/individual quota and missing-tree/blob readback cases
passed. Offline Nix source tests: **34/34 v2 + 57/57 metadata/codec + 80/80
Task Record/View/Evidence = 171/171 PASS**, with no skips. These are coordinator
executions, not independent reviewer tests. Fresh source-only R3 independent
correctness/security qualification is separately required before any
publication decision. Production Host/GitHub gates remain unchanged.

## R4/R5 source-only correction and verification (2026-11-01)

The R3 correctness reviewer found that duplicate incoming content IDs were
charged once but still decoded on every occurrence. R4 corrected the Issue
admission path to decode each newly admitted ID once and reuse the envelope for
identical repeated bytes. A red-before/green-after regression exercises this
behavior; neither cumulative pre-decode quotas nor later typed validation is
bypassed. The separate R2 publication readback repair also remains intact.

R5 uses a fresh isolated checkout at the same fixed base HEAD, without
modifying other candidates. Its disposable Git fixture now filters the exact
requested ref name instead of relying on prefix-matching `for-each-ref`.
An additional regression checks that a child ref never masquerades as an
absent parent Issue metadata ref; an attempted conflicting publication fails
closed. This is **fixture fidelity**, not proof of a production Host's exact
ref observations or atomic GitHub update semantics.

Fresh offline R5 test results: **36/36 v2 + 57/57 v1 Metadata Ref/Codec +
80/80 Task Record/View/Evidence = 173/173 PASS**. One initial semantic-suite
invocation failed during import because `tests/` was absent from PYTHONPATH;
re-running with `PYTHONPATH=tests:.` succeeded without source changes. The
older R1 author-only 'adjacent regressions NOT RUN' statement above is
historical; the R5 coordinator performed those regressions. Tests and fixture
checks do not establish remote retention, production Host CAS, or activation.

Independent Correctness and Security reviews must qualify the exact frozen R5
byte set before any separate publication decision. Phase 2/3/4 permissions and
all production/lifecycle prohibitions remain unchanged. No source Commit,
Push, PR, live GitHub metadata refs, deletion, install or real Task effects.
