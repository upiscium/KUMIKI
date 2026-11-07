# #291 R5C1B1 — canonical Metadata v2 initial checkpoint coordinator

**SOURCE ONLY / explicitly opt-in / publication only.** Based on
`main@af679a2627f8dec39188f275d19bfb13c5a91fc0`. This source change does not
activate a package, launch OpenCode/Task/model/Session, reply to Ask, publish a
Product commit/branch/PR, or install a GitHub/metadata transport. v1 dispatch
and v0.1.0 GA are not changed. No CLI or plan-file field enables this lane.

## Host boundary

`V2InitialCheckpointHost` is the single coordinator. The trusted Host supplies:

- A canonical `V2PrFirstReadOnlyHost`, including `V2ReadOnlyMainHost`, the current
  #337 Task reader, held #256 `AttachmentLease`, and `ExactCheckpointReader`.
- Exact #337 `IssuePublisher` and `SourceOnlyCheckpointPublisher` instances,
  independently authenticated and scoped to the same repository/Issue and
  principal. Their separate authorization callbacks remain mandatory. The
  checkpoint publisher's exact reader must be the proof's very same object.
- R5C1A `ProductPrFacts` reads including the original PR author/title/body and
  remote branch HEAD, plus the proof's `PrFirstFacts` reader. The checkpoint
  adapter independently reads the PR and complete comment inventory. These
  sources must agree; an empty tuple and `complete=True` are not grants.
- An exact Host-produced Task View, a 32-character lower-case hex operation ID,
  initial report, and a private #140 `RecoveryJournal` directory.

All #337 classes are imported by their canonical **top-level** names. Do not
load `tools.kagari_metadata_v2_*` aliases: that creates different dataclass
identities. #291 modules follow the existing package/top-level import pattern.

The only Main entry is the Host-only Python assembly:

1. Hold `gate.hold_verified(...)` for the entire operation.
2. Construct the Host adapters above from authenticated sources, not from a
   Task's claimed PR number, actor, Issue JSON, plan flags or model output.
3. Use `build_main(plan, host=MainV2InitialCheckpointCapabilities(read_issue,
   {issue: coordinator, ...}))`. The map must exactly cover all selected Issues;
   cross-Issue, partial, mixed lifecycle lanes and wrong types are refused.
4. Within `MainSupervisor(main, attachment_lease=lease,
   publication_only=True)`, call `publish_initial_checkpoint(issue)`.

The existing canonical repository singleton is held through publication and
readback. Main rechecks the exact owner, selection and held attachment. No
dispatch/cycle is invoked. Calling the service CLI with even an injected
initial-publisher Host returns `R5C1B1_HOST_ONLY` before loading its plan.
Python capability objects/callbacks are **trusted Host code**, not a sandbox
against arbitrary same-process monkeypatching or a malicious same-UID process.

## Original-plan durability and ordering

The create-once key is scoped to repository ID + Issue + initial operation;
it deliberately does **not** vary with today's Record, PR, principal, tip,
operation ID or subject. The journal pins:

- original approved Issue content, registration/Record/Contract, repository
  genesis and attachment identity;
- registered pristine Worktree identity, Product HEAD/tree and original
  authenticated Product PR/remote facts and ordinary comment inventory;
- original expected Archive and Issue metadata tips;
- the exact bytes **and returned ID** from
  `encode_snapshot(repository, str(issue), Product_HEAD,
  boundary="explicit-handoff", view=exact_host_view)`;
- operation ID and immutable canonical initial-capsule parameters, including
  PR/principal/report. Its metadata commit comes only from #337's original
  candidate receipt, not from a newly observed Issue tip.

The plan is fsynced and read back **before** invoking `IssuePublisher.publish`
with `IssueWrite(..., objects=(snapshot_bytes,), next_record_id=None)`.
The existing publisher alone constructs/journals/publishes its candidate. Its
Host writer still must implement exact direct-ref, dual expected-tip CAS and
an independently granted non-force Issue-ref update. There is no new Git writer.

Only after positive metadata confirmation is the canonical `V2PRCheckpoint`
passed to `SourceOnlyCheckpointPublisher.publish(...,
previous_comment_id=None)`. #337 owns rendering, its exclusive stream lease,
comment intent, exact-ID history read and lost-ACK reconciliation. There is no
second checkpoint codec or custom repost loop. Invocation-local canonical
facades wrap installed grants/effects with Main/Issue/Task/Worktree/lease and
original PR checks; they do not modify the installed publisher instances.

A retry in a fresh interpreter reads the **same original plan** first. It
does not call `read_view` again. The original IssueWrite receipt is validated
by #337 and the same IssueWrite is reconciled. The same resulting capsule then
reconciles the same original comment. Nothing replaces or removes a journal.

## Fail-closed limits / deferred gates

- A plan with no readable original metadata receipt is
  `R5C1B1_METADATA_RECEIPT_MISSING` / `UNCERTAIN`. This includes a crash after
  plan fsync but before #337's candidate journal, or denial at that stage.
  There is deliberately no automatic continuation that allocates a candidate.
- A receipt with no positively confirmed metadata effect, or a comment intent
  with no positively confirmed comment, remains `UNCERTAIN`; no blind retry.
- Current Issue tip may equal the original expected tip or the original
  receipt's candidate during reconciliation. Publication proceeds to comment
  only after #337 confirms that candidate is current and its whole tree exact.
  A later tip, changed Archive tip, terminal/changed Record/Contract, changed
  Issue/PR/actor/Worktree/lease/owner fails closed. Historical readability alone
  does not renew authority. R5C1B0 may separately observe later history; this
  initial **publisher** intentionally does not adopt it.
- #140 has a **16 KiB record bound**. Oversized original plans are deferred
  before any metadata effect; no sidecar blobs or second persistence mechanism
  are introduced. Inventory is additionally bounded to 100 entries / 8 MiB.
- This small initial lane accepts one already-published, Task-specific,
  pristine, registered Worktree HEAD and one exact open Draft PR. It performs
  no R5C1A bootstrap/push/PR creation. Snapshots must have absent PREVIOUS,
  exact current authority, matching Git tree/remote/PR and registered Worktree
  entry. Optional Evidence and default-branch observations are deferred;
  supply `[]` and `{"state":"not_requested"}`, respectively.
- Any reserved checkpoint opener (including obsolete/foreign ones), duplicate
  initial, changed original inventory or noninitial stream blocks this MVI,
  except the exact intended canonical initial during recovery. Ordinary
  discussion is not a checkpoint, but inventory changes are conservatively
  refused. No deletion/edit/migration of old comments is attempted.
- Metadata publication and GitHub comments are **not one atomic transaction**.
  The existing checkpoint stream lease cannot be acquired with an exact capsule
  until the metadata candidate is known. A pending/denied comment stream can
  therefore leave a confirmed Snapshot without a comment. That is an explicit
  deferred gate, never simulated success or Task admission. Host CAS/grants and
  stream exclusivity must still operate inside each real effect boundary;
  cooperative local ownership cannot enforce cross-host GitHub uniqueness.
- A 90-second coordinator budget encloses the operation; nested #337 object/ref
  reads and comment operations receive the tighter deadline. Grants/effects
  recheck after journal fsync and callbacks. #337 itself owns bounded graph
  reads (rather than recursively re-running authority after every Git object).
  Host reads must independently enforce I/O/output bounds: synchronous Python
  cannot preempt a blocked callback. No background watchdog/process is added.

`InitialCheckpointRefused.state == "UNCHANGED"` means no possible publication
boundary was entered by this invocation. Once an original intent exists or
may have persisted, failures are conservatively
`InitialCheckpointUncertain.state == "UNCERTAIN"`. These are bounded errors,
not permission requests. Conflicting plans are typed `R5C1B1_PLAN_CONFLICT`.

## Positive outcome is still blocked

Success requires a fresh canonical `V2PrFirstReadOnlyHost.observe` proof after
comment publication, matching the exact metadata commit/Snapshot/comment ID.
The result remains `status="blocked"`, `reason="V2_MAIN_BLOCKED_LIVE"`, with
`initial_checkpoint="CONFIRMED"` and `pr_first.proof="PROVED"`. Task,
Worktree mutation, GitHub/metadata write, model-turn, Session and Ask authority
flags are false; runtime is `NOT_STARTED` and kickoff `NOT_SENT`.

## Disposable tests and verification status

`tests/test_kagari_vnext_main_v2_initial_checkpoint.py` reuses the existing
#337 Issue writer's synthetic bare-Git + #256 fixture and the checkpoint tests'
view/PR/comment/stream adapters. It adds a disposable registered Product
Worktree. Fresh-interpreter retries reconstruct only those local fixtures;
no KAGARI CLI or model executable is started. Tests cover canonical success,
repeat/recovery, lost ACKs, conflicting/missing intents, pre/post-effect drift,
inventory/projection refusal and strict Main/CLI/no-v1/no-model boundaries.

For a separately authorized verifier, the intended command is:

```text
nix develop --command python -m unittest tests.test_kagari_vnext_main_v2_initial_checkpoint
```

Relevant regression modules are
`tests.test_kagari_vnext_main_v2_pr_first_publication`,
`tests.test_kagari_vnext_main_v2_pr_first_observation`,
`tests.test_kagari_metadata_v2_issue_publisher`, and
`tests.test_kagari_metadata_v2_pr_checkpoint`.

**Not executed in this source-only implementation session.** Only file
read/scan/edit operations were authorized; no shell/Git/network/test commands,
independent reviewer, actual publication, activation, commit, push or PR was run.


## Independent review corrections (source-only)

A direct helper invocation must not bypass Main: the internal publication
helper validates the actual MainSupervisor object, its held flock, selected
Issue and exact AttachmentLease, rather than trusting a caller-supplied
no-op owner-check callback. Main increments a bounded active-publication
counter under its RLock and decrements it in a finally block. While active,
MainSupervisor.__exit__ refuses even same-thread callback reentry and
preserves the repository flock. Cross-thread shutdown is serialized by the
Main RLock. The corresponding P1/P2 regressions are exercised by disposable
tests. No malicious same-UID exclusion or live GitHub guarantee is implied.


A second independent review identified that an active counter alone is
insufficient to bind a reentrant helper call to the exact publishing Issue.
The owner now pins both Issue ID and Host instance throughout the publication
and clears them in finally; internal helpers independently validate current
Main selection and OPENCODE executor. A callback cannot borrow the active
counter for another Issue.


The private coordinator also consumes a one-invocation guard under the
canonical Main owner lock. The guard stays set until the outer Main route
finishes (including exception paths). Reentry for the same Issue and Host
is refused before a new intent or effect, even when an authenticated Host
callback attempts to invoke the helper directly with its real owner.


Signal/interruption review: the outer Main method now enters its try/finally
before modifying the owner publication state. Its finally clears the
exit-blocking active flag first under the held Main RLock, then all auxiliary
identity and invocation flags. Interrupted initialization must not strand the
flock or make a later attempt inherit stale publication authority.


Shutdown recovery after an asynchronous interrupt is distinct from callback
reentry. Under the Main RLock, Supervisor exit checks the actual publishing
Main method frame on its current thread. A callback whose publication is
still active remains blocked from releasing the flock. If an interrupt
already unwound the publication frame but left an active flag stranded
during finalization, Supervisor clears the stale identity and releases
the singleton lock. This is bounded in-process recovery, not an arbitrary
signal- or malicious-thread safety guarantee.


## Postpublication PR #375 P2 correction (source-only)

The Main route now guards *owner finalization* separately from the
coordinator's original-plan effect. If a BaseException interrupts cleanup
after the Host publication invocation is entered, it surfaces as
InitialCheckpointUncertain(R5C1B1_OWNER_FINALIZATION_UNCERTAIN), with the
original exception retained as cause. A pre-invocation signal and a
coordinator exception whose cleanup succeeds preserve their existing
semantics. The original plan and #337 journals remain unchanged, and the
MainSupervisor stale-flag recovery may release the flock after the
publishing method frame has unwound. This is an in-process source contract,
not a claim of perfect arbitrary asynchronous-signal atomicity.


## D1A approved contract — bounded process-boundary recovery (R3 source-only)

D1A Option A is the Human-approved **contract** for the initial Metadata v2
Checkpoint publication lane of #291. It does not install or qualify a
production publication worker and does not authorize live effects. The
in-process guards above remain useful for the specific tested callback and
finalization boundaries, but **do not** establish atomic teardown or typed
`UNCERTAIN` at every asynchronous Python signal instruction.

### Results and authority

| Observation | Required handling |
| --- | --- |
| Exact metadata candidate, original Snapshot and canonical PR comment all independently confirmed | `initial_checkpoint=CONFIRMED`; still `V2_MAIN_BLOCKED_LIVE`; Task/model/Session/Ask flags remain false |
| Proven pre-effect in-band refusal | `UNCHANGED` only with positive proof that no durable boundary was entered |
| In-band failure after possibly persisted #140/#337 intent | Preserve original plan and receipts; typed `UNCERTAIN` where implemented; no automatic new candidate or repost |
| Publication worker died, outer shutdown interrupted, its **worker-result ACK** was lost, or raw `KeyboardInterrupt`/`BaseException` escaped | **Operator-level `UNKNOWN_ABORTED`**, not a new persisted enum or transferable permission; stop all writes on that owner |
| Individual #337 Metadata write or GitHub comment **effect-API ACK** is unknown while the worker remains alive | Preserve the original candidate/comment receipt and report in-band `UNCERTAIN` when supported; exact remote readback is required before any new effect |
| Missing/partial/unreadable original journal, missing required candidate receipt, unverifiable ref/comment, owner/lease/PR drift or old worker not proven stopped | **BLOCKED**, not inferred `UNCHANGED`; never blind retry |

The publication worker's result ACK is distinct from the Metadata/PR effect
API ACK. Either can be unknown independently; neither permits interpreting
absence of a response as `UNCHANGED`. Operator-level `UNKNOWN_ABORTED`
applies when the outer worker result or its lifetime is uncertain, including
after an effect-API failure that prevents a reliable worker result.

A caller who cannot see an ACK cannot infer that a write did not happen. A
crash after `RecoveryJournal.create` starts may leave an incomplete file:
`O_EXCL` and fsync do not mean a partially written record is safe to
overwrite. The #140 journal refuses malformed/partial records. A persisted
original plan with **no canonical #337 candidate receipt** is also BLOCKED;
the coordinator must not manufacture a second candidate to continue. A
valid candidate receipt alone does not prove that the corresponding ref
was updated: require the exact original commit and object/tree readback.

A confirmed metadata candidate but no prior comment intent is a different
stage from an unknown POST ACK. The exact initial comment may be posted
only under an original-operation grant, authenticated complete inventory,
and a valid exclusive stream lease. When the original comment intent
exists and the POST acknowledgement is unknown, reconcile its exact
comment ID or block; **do not submit a new comment**.

### Host-only lifecycle gate — NYI for live use

The source-only coordinator and its existing `MainSupervisor` do **not**
implement a process supervisor, a signal router, a child/FD tracker, an
automatic teardown service, or a global transaction engine. A separately
qualified trusted Host must:

1. Launch one exact Issue/Contract/AttachmentLease-bound publication worker
   with bounded credentials, callbacks, operation identity and deadline.
   Never pass the writer capability to a Task or arbitrary CLI caller.
2. Quarantine the operation on interrupted shutdown, raw exception, timeout
   or missing ACK. Do not reuse the partially unwound Python Supervisor,
   dispatch another publication or auto-answer an Ask.
3. Prove the old effect-capable worker, all descendants, inherited file
   descriptions and pending I/O are quiescent before a new Host has effect
   authority. If unproven, **BLOCKED**. No lock-file deletion, forced unlock,
   stale-PID shortcut or unsupported lock takeover is allowed. This
   requirement applies even if a new local flock can be acquired:
   `flock` proves neither death of a fork descendant nor termination of a
   remote GitHub mutation.
4. In a fresh verified context, **read first**: bind repository ID, exact
   Issue/Task/Record/Contract, Product head/Draft PR, AttachmentLease,
   original plan, #337 metadata receipt and comment intent, and authenticated
   complete remote PR/comment/ref evidence. Recover only the **original**
   immutable candidate and comment; each permitted effect still requires
   a fresh effect-time grant, direct-ref CAS and stream lease.
5. Keep the v1/GA runtime, ongoing Task/Session and ChatGPT/RDC caller
   independence intact. **Loss of the caller's observation connection
   is not a crashed worker** and must not terminate admitted Task work.

The Host must separately qualify authoritative server-side dual-ref CAS,
archive Rulesets/retention, complete authenticated GitHub observations,
cross-writer checkpoint stream exclusion, worker/descendant teardown and
one-Issue live E2E before activation. This source-only PR does not prove
those properties. Arbitrary repeated SIGINT, compromised same-UID code and
malicious in-process mutation are not guarantees of this MVI.

### Executable disposable evidence, not live Host qualification

`tests.test_kagari_vnext_main_v2_d1a_recovery` exercises the actual
source-only coordinator against disposable #337 bare-Git/fake-GitHub
fixtures. With D2-P0, it tests **three separate self-SIGKILL
process deaths**: after metadata commit, after original comment POST
**without a durable Host ACK**, and after a durable Host ACK but before
the worker reports success. A **fresh interpreter** uses the same
original durable plan. The no-ACK case must BLOCK even though the
comment is visible; the other two cases recover their qualified
original claim/ACK without duplicate metadata writes or comments.
The fake remote comment is deliberately saved before SIGKILL to model
remote durability, not to certify real GitHub causality. The fixture alone uses
`Limits(seconds=60)` to bound slow synthetic Git traversal; this does not
change any production limit or 90-second coordinator deadline.

The same test module checks partial original journal fail-closed semantics
and fork-inherited `flock` ownership. These only demonstrate local
process/OS behavior; they do not create or certify the missing trusted
Host supervisor. A fork survivor is an explicit **BLOCKED** situation
until old descendants/FDs and remote effects are accounted for. Distinguish
test-pass evidence from an activated Host policy.

Run with an authorized disposable environment:

```text
python -m unittest -v tests.test_kagari_vnext_main_v2_d1a_recovery
python -m unittest -q tests.test_kagari_vnext_main_v2_initial_checkpoint
```

After source-only qualification, independently review the exact source PR
HEAD and run the #337 IssuePublisher, PR Checkpoint, PR-first observation
and publication regressions. An inert source Merge, if separately approved,
does not authorize workers, Metadata refs, Task launch, deployment or
Release. Keep Issue #291 and #337 Open; PR Ready/Merge is a different
Human gate.


## R4A — D2-P0 ACK-only Host provenance validator (source-only)

R4A implements the Human-approved **D2-P0 strict ACK-only contract** for
the opt-in Initial Checkpoint Coordinator. Its default is **fail-closed**:
an unconfigured Host provenance gateway raises
`R5C1B1_D2_PROVENANCE_HOST_REQUIRED` **before any new #140 plan,
Metadata ref write, Host claim or GitHub comment POST**. This is still
source-only: no production Host implementation, credential-bearing transport,
stream-fence or provenance trust root is installed or activated.

The narrow `kagari_vnext_main_v2_provenance.py` module holds a closed
original-operation scope and validates Host-provided claim and POST ACK
facts. It does **not** authenticate arbitrary Python objects itself.
Only a separately qualified trusted Host may supply a gateway; an object,
a callback or a Boolean labeled `exclusive_egress=True` is **not** an
authenticated Host attestation outside that trust boundary. Source-only
tests inject a clearly marked **disposable fake Host**, so their positive
results are synthetic validation, not evidence of live GitHub provenance.

The R4A opt-in flow is:

1. Recheck the exact #140 operation, Issue/Contract/PR and original
   immutable Snapshot. Construct an `OriginalOperation` with the
   canonical original plan's SHA-256 and stable repository ID, Issue,
   operation ID, PR and checkpoint-subject bindings.
2. Before creating a new plan, ask the Host to check the stream and
   Issue lifecycle eligibility **read-only**. After the original plan is
   fsynced, require the Host to establish or verify the exact original
   claim. A missing, wrong or unqualified claim stops **before any new
   Metadata effect**; a replay must not invent a replacement claim.
3. Continue using the canonical #337 Metadata writer, exact initial
   PR checkpoint publisher and R5C1B0 reader without modifying their
   generic codecs or idempotent adoption semantics. For this #291 lane,
   the POST callback is specifically the Host gateway's credential-bearing
   egress rather than the generic publisher callback.
4. Even after #337 independently confirms the exact comment, require
   the Host's **durable original-operation POST ACK**, with issuer,
   epoch, attempt ID, request body digest, target PR and exact comment
   ID. An ACK must come from the actual Host send path and match
   independent authenticated comment/Metadata readback. The #337
   `ConfirmedCheckpoint` is not itself a creator-bound POST ACK.
5. If ACK is missing, mismatched, from an unexpected epoch/issuer,
   or its source cannot be authenticated, return `UNCERTAIN`/BLOCKED,
   never `CONFIRMED`, even when the comment text, actor and intent
   are identical. Never automatically resend an ambiguous POST.

D2-P0 deliberately tightens the earlier D1A disposable test: **SIGKILL
after comment POST but before durable Host ACK must BLOCK**, whereas a
valid ACK persisted before worker-result loss may be verified by a new
worker without a second POST or Snapshot. Full sender exclusivity,
legacy/edited/deleted comment completeness, credential custody, epoch
lineage and old worker/fork/IO quiescence remain **separate live Host
qualification gates**, not properties of source-only fake proof records.
Do not activate this lane without them.

`tests.kagari_d2_p0_fake_host` persists ephemeral fake claim/ACK facts
in the disposable test root only. It is intentionally not a production
store, credential provider, authenticated RPC transport or rollback-proof
ledger. The published v1 runtime, generic #337 behavior, Task/Session/
Ask launch and actual GitHub/Metadata effects remain unchanged by R4A.
A separately authorized Host and release gate are required for any live
use.


R4A's Host-provenance witness includes an explicit **qualified ACK-issuance
epoch lineage**: a successor at epoch 2 may check an original authentic
ACK durably issued in then-valid epoch 1 only if the trusted Host positively
attests that epoch-1 issuance and the old worker is quiescent. A new ACK
issued after revocation or an unknown lineage is rejected. Type-sensitive
canonical JSON comparisons distinguish an integer repository ID of `1`
from the boolean `true`; Python dataclass equality alone is insufficient.
The fake Host's lineage is not a real credential revocation mechanism.
