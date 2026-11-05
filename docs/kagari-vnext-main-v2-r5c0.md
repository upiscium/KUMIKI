# KUMIKI #291 R5C0 — opt-in Metadata v2 PR-first initial checkpoint read-only proof

**Status:** Uncommitted source-only candidate. No runtime activation, real GitHub
reads/writes, Product PR, Session/model, Ask reply, release or deployment.

## Scope

- Candidate based on `upiscium/KUMIKI main@8a4858c7c68cccf831e9a081b463558764f024b5`, merged #291 R5B0 PR #355. The code uses one isolated source clone; #337 Archive Host/Issue writer, #339 loopback server, #194 v1 collaboration and existing immutable Nix control package remain unchanged.
- A trusted Host explicitly chooses `MainV2PrFirstReadOnlyCapabilities` with a full exact Issue map. Its per-Issue `V2PrFirstReadOnlyHost` holds the same #256 AttachmentLease, #337 `CurrentTaskReader`, R5A0 `V2ReadOnlyMainHost` and #337 `ReadOnlyMetadataV2Host`. It requires trusted, complete bounded PR/remote-head/comment facts read callbacks and an independently configured expected base branch. An Issue title, model output, JSON plan or prior Session locator cannot provide this capability.
- The existing `RepositoryMain`, `MainSupervisor(observe_only=True)` and `status=blocked / V2_MAIN_BLOCKED_LIVE` remain the control plane. The R5C0 result adds `pr_first.proof=PROVED` as **read-only evidence**, with all write/Task/Session/prompt authorization flags false. No new Main, PR writer, Host authority database, queue or lock.

## Verified inputs and exact read path

1. Begin and end with the R5A0 verification of current v2 Issue ref, Record/Contract, active versus archived Task, approved GitHub Issue OPEN/title/body, absence of a legacy same-Issue assignment and held AttachmentLease. An old historical PR locator cannot replace current Task authority.
2. Use #292's Git-only `worktree_registered`, verify the exact `<project>/.worktrees/<issue>` linked Worktree Git common-dir, branch/HEAD, and a pristine index/worktree/untracked status. No `verify_task_record` / `resolve` v1 lookup. Branch OID is checked against trusted Host-observed remote Product Task head.
3. Host returns an *exhaustive* bounded list of branch-matching Product PRs, exact remote head, repository/default-base identity, principal and exact PR comments. Require one unambiguous **OPEN Draft PR** in the same numeric repository, same head repo/base repo, same head branch, configured base and exact Product head SHA. Incomplete/unknown fact callback and duplicate/foreign/wrong-base/closed/changed-head PR fail closed. Host callbacks must authenticate GitHub reads; typed dataclasses and `complete=True` cannot authenticate a malicious Host.
4. Parse exactly one **v2-specific** initial checkpoint from the authenticated principal. Closed canonical JSON, canonical base64, SHA-256 domain-separated integrity marker `kagari-v2-initial-checkpoint/v1`, bounded 16 KiB comment size, exact metadata version 2, initial key/boundary, principal, numeric repository ID, Issue, Product branch/base, PR number, Product HEAD, Record/Contract, metadata Commit and Snapshot ID. A #194 v1-only capsule is **not** upgraded to v2 authority; repeated owned initial comments or conflicting identities refuse. The digest is integrity, not proof of GitHub actor identity.
5. Resolve the specific `ExactLocator(version=2, repository ID/name, Issue, metadata_commit, task-view-snapshot, snapshot ID, Product head)` through #337 `ReadOnlyMetadataV2Host`, which validates the historical metadata tree/Record/Contract and active Issue/archive reachability. Recheck closed Task View Schema using #144 `decode_snapshot`, not merely the presence of an object: boundary **explicit-handoff**, previous checkpoint **absent**, exact Record/Contract/base/branch, Product head, GitHub Draft PR projection, repository/task/subject and no terminal disposition.
6. After all Host/metadata reads, repeat the *same* authenticated PR/remote/comment facts; verify current v2 Task and approved Issue unchanged, registered Worktree/HEAD/pristine status unchanged and **re-verify held Attach after the final callback**. Unknown/disagreement means `MainError` per-Issue `blocked`, never an inferred absent PR or launch grant.

## Source-only implementation and tests

Changed *only* five files:
- `tools/kagari_vnext_main_v2_pr_first_observation.py`: new typed read-only Host verifier and v2 initial capsule parser.
- `tools/kagari_vnext_main.py`, `tools/kagari_vnext_main_service.py`: minimal explicit Host-only R5C0 Main observation branch. Original R5A0/A1/B0 and v1 dispatch remain unchanged.
- `tests/test_kagari_vnext_main_v2_pr_first_observation.py`: disposable local #337 v2 SHA-1 metadata bare-Git + #256 Project Attach and linked Worktree, mock authenticated GitHub PR/comment reads. No GitHub credentials/endpoint.
- This document.

Positive fixture: current registered Product Worktree with exact OPEN Draft PR, domain-separated initial capsule and `explicit-handoff` Snapshot at exact historical v2 Metadata Commit. Negative fixtures: no or duplicate PR, non-Draft/closed/foreign PR, wrong Product branch/base/head, actor/comment, v1 comment, forged/duplicate JSON, stale or unreachable Metadata Commit, wrong Snapshot boundary/IDs, archived-current Task, changed Issue/ref, PR changed mid-read, unregistered or dirty Worktree, revoked Attach, v1 fallback and no model/Session/PR/Ask side effects.

The current v2 Task reader purposely refuses an **archived current Task**. A historical checkpoint can still be read from archived metadata via `ReadOnlyMetadataV2Host`, but this cannot reauthorize an archived Task for first execution. If the active Issue ref has advanced without retiring the current Record, a checkpoint pinned to its older reachable metadata Commit remains valid if all other current PR/Issue facts are unchanged.

## Limits and future gates

- Actual GitHub PR enumeration/authentication, real protected Metadata v2 CAS/Ruleset and remote checkpoint authoring remain Host responsibilities **not proven** by mock facts. The v2 checkpoint format in R5C0 is a bounded read-only protocol candidate; a real writer must be reviewed and separately authorized in R5C1 before any implementation Turn.
- No production runtime installation, OpenCode Session start/prompt/Ask reply, model, PR publication, GitHub ref mutation, release, automatic Ready/Merge or Issue closure.
- The separate future R5C1 PR-first **effectful** phase must establish non-force Product push, exact Draft PR, v2 Metadata Snapshot publication via #337 with durable #140 journal and authenticated remote CAS, plus exact initial comment readback. Partial/uncertain ACK must not result in duplicate writes or launch.
- Before v2 Task execution, later separate #163 v2-aware Session/Ask grant must prove the full R5C1 gate, with #292 CLI hands-on preview before actual consumer Merge/rollout. Keep #291 OPEN.

**This source-only scope does not authorize Commit/Push/Draft PR/Ready/Merge or any production activation.**

## Independent reviewer corrections (R1)

The independent OpenCode source review initially returned `CHANGES REQUIRED`
with two concrete P2 issues.

1. Malformed checkpoint JSON could raise a raw `RecursionError`; an unpaired
   UTF-8 surrogate in the comment or report could raise `UnicodeEncodeError`
   instead of the bounded `V2PrFirstRefused` / Main per-Issue refusal.
   R5C0 now catches those parser/encoding failures and returns
   `R5C0_CAPSULE_INVALID` without exposing Task success.
2. Plain `git status --porcelain=v1 -uall` can honor
   `submodule.<path>.ignore=all`, hiding modified tracked files, untracked
   files, and a moved checked-out nested commit. R5C0 now explicitly passes
   `--ignore-submodules=none` on both Product status reads.

New tests reproduce nested JSON, invalid report/comment Unicode and use a
**real disposable linked Git Worktree with an initialized Git submodule**
configured `ignore=all`, checking modified tracked, untracked and
changed-nested-HEAD cases. The targeted corrections passed without changing
the existing Product or installed runtime. The final corrected source still
requires independent re-review and final adjacent regressions before READY.
