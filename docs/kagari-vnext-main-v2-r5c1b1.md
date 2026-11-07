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
