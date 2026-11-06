# KUMIKI #291 R5C1B0 — canonical v2 initial checkpoint read-only convergence

**Status:** source-only, opt-in. No real Product GitHub, metadata ref or PR-comment mutation; no model/Session/Ask, install/activation, release or deployment. The canonical #337 Phase 2D1 source was merged by PR #357 before this slice. The R5C0 preliminary codec is obsolete.

## One canonical format

`tools.kagari_vnext_main_v2_pr_first_observation.V2PrFirstReadOnlyHost` now requires a Host-injected `kagari_metadata_v2_pr_checkpoint.ExactCheckpointReader`. It does not decode `kagari-v2-initial-checkpoint/v1` or directly reconstruct #144 Snapshots using its own parser. It expects exactly one original owned `kagari-pr-checkpoint/v2` capsule with `boundary=initial` and `key=initial`; old v1 / provisional bodies and ambiguous or noninitial checkpoints fail closed. The canonical #337 codec owns closed schema, canonical UTF-8/JSON/base64, digest and display verification.

The **current** #337 Task Record/Contract, live approved GitHub Issue, absence of v1 same-Issue assignment, exact linked pristine Worktree (including modified/untracked submodules), Task branch remote HEAD, single same-repository OPEN Draft PR, configured base, and held #256 AttachmentLease are still verified before and after the exact historical read. A historical checkpoint does not grant Task execution or override current Issue/Record authority.

## Exact authenticated History read

The Host must supply an independently bound `ExactCheckpointReader` with the **same** `RepositoryIdentity`, `RegisteredIssue` and expected base as the current v2 Main reader. The Host pins the original numeric principal independently of capsule author claims. The PR inventory now contains #337 `CommentFacts(repository_id, repository, pr_number, comment_id, author_id, body, kind)` facts with exact integer types, increasing IDs and bounded UTF-8 bodies. An old #194 `task_collaboration.CommentFacts` lacking numeric repository ID is *not* accepted or cast. A complete authenticated inventory is a Host requirement, not proven by a caller-returned Boolean.

Main identifies the one initial owned comment from the full authenticated inventory. It calls `ExactCheckpointReader.read(pr_number, comment_id)` using that **specific** ID; the reader verifies independent exact Host comment facts both before and after the original immutable Metadata Commit/Record/Contract/Snapshot and archive/Issue DAG traversal. Main additionally matches the returned canonical capsule to its inventory's decoded capsule and confirms the original Snapshot's nonterminal disposition. Any mismatch, missing ID, stale/readback drift or malformed/moved ref blocks that Issue.

The exact reader's #337 historical verification is not the same as a fresh GitHub PR or Task grant. Main re-reads the current Issue, Record, PR inventory/remote HEAD, Product Worktree status and AttachmentLease after that reader succeeds. Therefore a prior immutable valid checkpoint cannot authorize a now-retired Task, closed or moved PR, or changed Worktree. No fallback to v1 or partial success is allowed.

## Authority and compatibility

A valid initial checkpoint yields a **read-only** `pr_first.proof=PROVED`, exact PR/comment ID, Metadata Commit/Snapshot ID and `checkpoint_schema=kagari-pr-checkpoint/v2`. Main still returns `status=blocked` / `reason=V2_MAIN_BLOCKED_LIVE`, `task_execution_authorized=False`, `github_write_authorized=False`, `worktree_mutation_authorized=False`, `session_started=False` and `prompt_sent=False`. A normal packaged Main CLI without trusted capabilities cannot select this opt-in Python Host lane.

The preceding R5C0 doc is retained as historical documentation of the formerly provisional candidate, not as an active codec specification. This change adds no second checkpoint parser, writer, journal, ref namespace, credential or model executor. Production authenticated Host callbacks, bounded network source reads, real remote CAS/Ruleset, caller-independent Session/Ask and E2E remain separately gated.

## Synthetic verification

`tests.test_kagari_vnext_main_v2_pr_first_observation` uses disposable #337 bare-Git and local #256 Attach fixtures, plus Host-supplied PR inventory/exact-comment callbacks. Positive proof uses the canonical #337 render/decode/read contracts. Negative tests must include obsolete v1/provisional markers, duplicate/foreign/changed comments, independent principal/Host binding, closed/moved PR, stale current Task/Worktree/Attach, malformed Unicode/digest, wrong immutable historical Metadata/Snapshot and readback mismatch. Re-run #337's own canonical checkpoint unit tests and existing Main/Host/v1 workstream regressions; independent OpenCode correctness/security source review on final exact bytes is required.

**Still NYI:** composing and posting the initial v2 Metadata Snapshot and PR Checkpoint (R5C1B1); real Product branch/PR Host integration, actual GitHub CAS/Ruleset and Host ownership proof, first model Turn, Session/Ask reply, Runtime activation and consumer E2E. Keep Issue #291 OPEN.
