# KUMIKI #291 R5A1 — Main-owned Metadata v2 Worktree admission (source-only)

**Status:** Uncommitted isolated source candidate. No Product Worktree was created; CREATE/RESUME tests use disposable SHA-1 Git projects. No model, Session, GitHub mutation, PR, deployment, release or system service was invoked by implementation.

## Scope and provenance

- Current isolated source base: `upiscium/KUMIKI main@9b76c483c154c0f418de25825b65a94be8ec7dac` (merged #291 R5A0 PR #350 and #337 Phase 2C1 PR #351), verified 2026-11-04 JST. The original source/test bytes were prepared against `fdd52b562a70465ec7100af8800a46f5432648b9` and copied byte-identically to this new isolated clone.
- Existing #337 Phase 2B2 `CurrentTaskReader` and `V2WorktreeAllocator` are reused **unchanged**. #337 Phase 2C1 Issue writer PR #351 is now **merged** as `9b76c483c154c0f418de25825b65a94be8ec7dac`; R5A1 does not edit or invoke the writer.
- Change only R5A1 Main/Host binding, bounded new tests and this documentation. No change to #337's writer, #339 server/bridge, old v1 Runtime, #286 Git Worktree allocator, v1 Session, Nix/GA packages or ordinary CLI commands.

## Main ownership and operation flow

1. A trusted Host with the explicit `metadata-v2-linked-worktree` profile uses the existing `gate.hold_verified(project, state_home, package_root)` **once**, obtaining the verified #256 `AttachmentLease`. It constructs `V2ReadOnlyMainHost` with the same current-v2 `CurrentTaskReader` and that lease, plus a separate `V2MainWorktreeHost(readonly, authorize_worktree)`. A positive R5A0 observation alone never grants Git effects.
2. It injects a `MainV2WorktreeCapabilities(read_issue, admissions)` object through Python into the **same** concrete `RepositoryMain`. Every explicitly selected Issue must have the correct immutable Host binding (no mixed v1/v2 selection or partial fallback). Plan files and ordinary CLI cannot create this Host capability.
3. The Host enters the existing `MainSupervisor(main, attachment_lease=lease, admission_only=True)` inside the original Attach hold. **Lock order: Attach → canonical Main repository owner → existing #286 per-Issue Git Worktree lock.** All selected Issue hosts must reference the exact same held lease. No new lock or durable Task SoT is introduced.
4. The trusted Host invokes only `supervisor.admit_worktree(issue)`, not `cycle(dispatch=True)`. Main verifies its exact OS flock/PID/owner and the exact Host/Lease identity under `_main_owner_guard` throughout the effect. Direct `RepositoryMain.admit_v2_worktree` without a live admission-only Main owner refuses.
5. The Host module re-reads exact live GitHub Issue identity/OPEN/title/body, #337 current v2 Issue metadata tip, current nonretired Record, Contract, branch/base, archive and verified Attach lease **before Git effects**. It calls existing Host `legacy_issue_absent` and *separately* approved `authorize_worktree`; only literal `True` admits. Both callbacks are wrapped in exact current Issue and Main/Lease checks before and after invocation, with revalidation against the originally approved current tip.
6. The Host constructs the existing #337 `V2WorktreeAllocator(store, current_reader, lease, selection, ...)` and invokes inherited `ensure()` **once**. The allocator's `_authority` performs further current-record/lease/Host-grant checks before and after CREATE/RESUME inside #286's Issue lock. Calling #337's standalone `admit_worktree` under this hold would nest an Attach flock, so R5A1 does not call it.
7. After the Git result, Main/Lease/live Issue/current tip are revalidated. If a post-CREATE check fails, **do not roll back/delete** the branch/Worktree: preserve actual Git state and return a bounded `V2_MAIN_WORKTREE_RECONCILIATION_REQUIRED` error for explicit operator reconciliation. No blind retry.
8. Success means only `worktree_admitted/CREATED` or `worktree_admitted/RESUMED` for that exact Issue/Contract/Worktree. Output always reports `runtime=NOT_STARTED`, `kickoff=NOT_SENT`, and false Task execution/Session/PR/GitHub-write authority.

## Exclusions and safety boundaries

- Existing v1 `RepositoryMain.dispatch()`, Session, PR publication and cleanup cannot be reached through this v2 Host lane. `MainSupervisor(..., admission_only=True).cycle(dispatch=True)` and normal `main() --config ... dispatch` refuse. R5A0 `observe_only=True` retains its blocked/not-started behavior. No new CLI mode or HTTP endpoint is installed.
- Host-supplied GitHub Issue reads, repository ID, same-Git v2 bounded reader/observer and operation grants remain trusted Host obligations. Synthetic in-memory/bare-Git test verification is **not** a real GitHub atomic CAS/Ruleset, archive-retention or credentials qualification.
- The source is **not included** in current immutable `kagari-control` or #337 source add-on Nix outputs. Packaging, installation, live Product Worktree CREATE, #163/#292 v2 Session/Ask, #194 v2 PR checkpoint, #337 2C2/2D/Phase 3 remote CAS and R5D real model E2E are distinct gates.
- No security extension for hostile same-UID local processes is added; this retains the previously approved trusted one-host/loopback boundary.

## Tests and review contract

The dedicated `tests/test_kagari_vnext_main_v2_worktree.py` uses only #337's disposable Attach, local bare-Git current Task and #286 Git Worktree fixtures. It checks exact one-Issue CREATE/RESUME, two-Issue isolation, staged/dirty/untracked Product preservation, existing v1 ref, denial by absent/invalid/expired Host/lease, rejected nonliteral grants/legacy collision, changed live Issue/Record before and during callbacks, post-Git retirement preserving worktree, orphan/duplicate branch refusal, single Attach hold, same Main owner exclusion and absence of Session/PR/model calls.

Initial independent OpenCode review identified one bounded P2: a refusal in the **final post-`ensure()` check** could escape with an ordinary pre-effect error code. The correction now tracks positively completed allocator admission and always classifies a later Main/Lease/Issue/current-tip refusal as `V2_MAIN_WORKTREE_RECONCILIATION_REQUIRED`, retaining actual Git state. The dedicated `test_post_allocation_live_issue_drift_is_reconciliation_not_preflight` forces an external Issue edit *after* the allocator returns, and verifies both the classification and retained branch. A bounded independent OpenCode corrected-delta re-review returned **SOURCE ACCEPT**, with no new confirmed P1/P2 in the supplied code. This verdict is not a full-file hash verification, unshown-import audit or live integration proof.

Re-run R5A0, #337 Phase 2B2 and existing v1 Main/Host/Runtime regressions, then seek an independent OpenCode source correctness/security verdict for the exact bytes. Independently qualify against current main at any later publication gate. **No Commit/Push/Draft PR/Ready/Merge/release/real Product mutation is approved in this R5A1 source-only gate.**
