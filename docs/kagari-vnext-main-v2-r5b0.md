# KUMIKI #291 R5B0 — Metadata v2 current Task Session/Ask observation (source-only)

**Status:** Uncommitted isolated implementation candidate. This is NOT a Session launcher, native Ask answerer, Task/PR publisher, model turn, Nix package activation or live Product operation.

## Boundaries and upstreams

- Based on merged `main@0cbaa29eb72ecf8ed5c4f63b425422f8ca65dafe` (includes #291 R5A0 PR #350, R5A1 PR #352, #337 Phase 2C1 PR #351, #339 R11 PR #353). The #337 Phase 2C2 draft is separately owned and unchanged. The active GA/runtime distribution remains unchanged.
- R5B0 is explicitly selected by trusted Python Host `MainV2SessionReadOnlyCapabilities`; its all-Issue `sessions` mapping binds exact `V2MainSessionReadOnlyHost` objects and the same R5A0 `V2ReadOnlyMainHost` for each selected Issue. Nothing in a user-supplied plan, Issue title, model text, existing locator or ordinary CLI creates a v2 authority. R5A1 Worktree-admission capability is separate and cannot be smuggled through this Host object.
- Every observation begins and ends with R5A0's exact live GitHub Issue OPEN/title/body check, #337 current Issue ref, nonretired Task Record, Contract/branch/base, archive state, held #256 AttachmentLease and literal-`True` Host read grant/legacy-assignment absence. Current v2 refs are rechecked after Host and upstream callbacks. A historical v2 locator cannot substitute for current Task authority.
- The trusted Host supplies a `V2SessionLocator` bound to exact repository/Issue/Contract. Its `ABSENT` state is an **explicit positive Host observation** of Session absence, not a missing file, absent callback, or absent UI. `UNKNOWN` is a distinct outcome with `pending_asks=null` and `pending_count=null`; missing/malformed Host results REFUSE.
- On `PRESENT`, verify the exact expected `<project>/.worktrees/<issue>` linked Git Worktree under the #292 Git-only `worktree_registered()` check (Project root, common Git directory, branch, HEAD and Git Worktree inventory). Do **not** call the existing v1-only `verify_task_record()` or `resolve()`: those access `MetadataStore.fetch_tip()` and old `TaskRecords` and are not a valid Metadata v2 authorization path.
- Reuse #292's pure `TaskBinding.from_mapping()` with exact Contract ID and `OpenCode.verify_session()`, `OpenCode.pending()`, `status()`. All upstream calls are GET-only under the existing loopback OpenCode client. Verify the Session ID, exact Worktree directory, absence of a parent Session, and that pending Permission/Question IDs and current Task binding remain stable across repeated reads. No `OpenCode.reply()`, native POST or Session start is reachable.
- `PENDING_ASK` reflects actual current native pending requests; `RUNNING` reflects OpenCode busy/retry without pending requests; `idle` maps to `UNKNOWN / IDLE_NOT_TASK_COMPLETION`; missing/invalid upstream API or changing pending state refuses instead of claiming an empty queue. The concrete v2 Main response retains `status=blocked / V2_MAIN_BLOCKED_LIVE`, does not claim success/Task completion, and returns no execution, Worktree mutation or GitHub-write grant. If an existing Session was positively observed, `runtime=EXISTING_SESSION_OBSERVED` and `kickoff=UNKNOWN`, not a fabricated `NOT_STARTED`/turn receipt.
- Existing `MainSupervisor(observe_only=True).cycle(dispatch=False)` collects these bounded observations per Issue; the normal Main/Dispatch/PR/cleanup paths still fail closed for the v2 lane. The #339 loopback bridge still expects a v1 `status=ok`; do not modify that unrelated owner or falsely coerce R5B0's status for its convenience.

## Tests and acceptance limits

- `tests/test_kagari_vnext_main_v2_session_observation.py`: disposable local Git plus #337 current-v2 metadata and #256 Attach fixture, with a mock GET-only OpenCode API; tests positive Host-confirmed absence, uncertain locator, verified busy/idle root Session, native Permission/Question, cross-Issue Session/Worktree, foreign/upstream changes, Live Issue/Record drift, v1 fallback prohibition and unchanged Product HEAD/index/worktree/evidence. The tests invoke #337's own Worktree allocator on *disposable* fixtures only for `PRESENT`.
- No actual OpenCode backend, HTTP server, real Session/process, model or Task executed. No production GitHub CAS/Ruleset, native Ask reply, lost-ACK live integration, #194 v2 PR-first checkpoint or #292 CLI hands-on test is demonstrated.
- Retain R5A0/R5A1 source behavior, #337 Phase 2B2, #292 v1 CLI, #163 v1 Session, #339 Main/bridge and v1 Main regressions. Independent OpenCode source review should check exact source/test bytes and concrete P1/P2. CI inventory verification is not full GitHub CI.

## Follow-on gates

R5B0 source-only observer does NOT authorize a real v2 Session start or first model turn. The #194 initial **Draft PR + remotely retrievable metadata checkpoint** prerequisite and #337 real GitHub protected-ref CAS must be proven separately. Future effectful Session and Ask reply requires exact Host grant and durable lost-ack recovery, then #292's Human-operated disposable CLI usability preview prior to merge of that candidate. Opt-in Nix packaging and real Product trial are independent.

**This phase authorizes no Commit/Push/Draft PR/Ready/Merge, real Product Worktree, real Session/model/Ask reply, release or deployment. Keep Issue #291 OPEN.**

## Independent review follow-up — final Attachment lease check

The first independent OpenCode review found a concrete **P2**: R5A0 checked the held #256 lease **before** its final external Issue callback, so an otherwise unchanged final Issue/Record read could complete after the lease became invalid. R5B0 originally performed only a final current-Record comparison and could still return a Session/Ask observation.

The corrected R5B0 module invokes the **same existing** `V2ReadOnlyMainHost._verify_lease(project)` **after** the last current-Record check, immediately before returning. Failure is translated to `V2SessionObservationRefused` and then the existing Main per-Issue `blocked` result; **no Session observation is returned**. It adds neither a new lock nor a separate authority system. A regression test now simulates the lease becoming invalid during the final Issue callback by instrumenting the already-defined `AttachmentLease.verify()` method, leaving the disposable #256 receipt bytes/inode unchanged. This verifies the exact last-check window without breaking the fixture's own immutable Attach hold.

### Review dispositions and evidence boundary

OpenCode independent R1 reported **CHANGES REQUIRED** with the one P2 described above. R5B0 then added the final re-verification and an exact conflict-window fixture. The follow-up independent **R2 source review returned SOURCE ACCEPT with no additional concrete P1/P2 in the supplied code**. The reviewer did not execute tests or verify the full installed runtime. The coordinator's final regression logs and Source Guard receipt must be checked separately. This is only a source-only qualification, not a live Session/Ask, GitHub CAS or production authorization.
