# KUMIKI #291 — R5C1A Product branch and Draft PR Host integration

Status: isolated source-only candidate; no real Product Git/GitHub, production Session or model, release, installation, Issue closure, or Source Guard publication is authorized by this phase.

## Trust and owner boundary

MainV2ProductPrCapabilities is an explicitly installed Host-only Python dependency, backed by exact selected Issues. MainSupervisor(publication_only=True, attachment_lease=...) uses the existing repository Main OS flock and forbids dispatch/cleanup/worktree admission. Both Main and Host validate held #256 Attach and current #337 Metadata v2 Issue/Record/Contract, approved live OPEN Issue title/body, no legacy same-Issue assignment and clean registered #286 linked Issue Worktree. No user-plan field, Issue visibility, task status flag, PR number or model output can establish a Host capability. The packaged normal CLI deliberately rejects this mode.

The only bounded effect callbacks provided by the trusted Host are plan_bootstrap, bootstrap, push_branch and create_draft, plus read_facts, operation-specific authorize and an existing private #140 RecoveryJournal. Main and the Task adapter do not have a generic GitHub client/token, Git runner or unrestricted mutation capability. Host callbacks must enforce the approved repository numeric identity, exact Task branch, original base/Contract/Record, non-force Product push and same-repository Draft PR at the effect boundary. Supplying a dataclass or complete=True cannot authenticate GitHub without a trusted, fully bounded Host callback.

## One-Task publication

1. Reverify R5A0 current Task, registered linked Worktree, exact branch/base/Contract and Git base ancestry. Check clean index/untracked/modified submodules explicitly, ignoring user Git config that would conceal submodule changes.
2. If the clean Task branch still points to the approved base, obtain a deterministic empty bootstrap commit candidate from the trusted Host without changing a ref. Require its tree to match the base tree; independently validate its parent/tree after the scoped bootstrap effect. A separate explicit grant and fsynced #140 intent precede local Task-branch movement.
3. Read a complete authenticated inventory of every PR matching the Task branch (including closed, merged, foreign and duplicate PR), with explicit ABSENT/PRESENT remote-branch observation. Require no conflicting existing PR. Push only the exact clean Task HEAD via a Host non-force callback, with an independent remote ref readback. Never reset a different remote head.
4. Create or adopt only one same-numeric-repository, same actor, OPEN Draft PR with the exact Task head/base, approved Issue title/body. Re-read authentic PR and remote branch facts; an API success ACK alone is insufficient. No model launch is enabled.

Every effect has a stable first-lifecycle journal key over repository ID, Issue, original Record/Contract/branch/base and operation (bootstrap, push or draft-pr), with immutable candidate/previous-head, actor, original Archive/Issue metadata refs, content and expected remote stored before the callback. Any unknown ACK or changed ref is reconciled by reading the original identity, not blind callback retry. Existing matching intent is readback-only. A conflicting candidate, ref or PR title/body fails closed. The existing Main owner flock serializes Host operations within its supported host; it is not multi-host consensus.

A positive result is pr_first_draft_established with the exact Product subject and Draft PR number, but checkpoint=NOT_PUBLISHED, kickoff=NOT_SENT and all Task execution, Session/model, Metadata or GitHub write authorization flags false. It is not the R5C0 confirmed initial checkpoint or a license for a first model Turn.

## Synthetic acceptance and later gates

Tests run on disposable Project/.worktrees Git, #256 synthetic immutable Attach and #337 local bare-Git Metadata v2; GitHub remote refs/PR operations use fake exact Host callbacks. They cover base/bootstrap/PR creation and idempotent adoption, before- and after-effect lost ACK on each operation, explicit grants, external drift, dirty or foreign Product Git, incomplete/unknown remote facts, issue/record invalidation and no legacy/model/Session/Ask effect. A positive test is not evidence of real GitHub Ruleset/CAS or branch protection.

#337 PR #357 Phase 2D1 defines the canonical kagari-pr-checkpoint/v2 codec and durable PR comment publisher. R5C1B must reuse it and replace R5C0's incompatible provisional initial capsule rather than adding a second v2 wire protocol. #337 Phase 3 real disposable GitHub protection/CAS and historical archive reader, plus exact #194 v2 initial checkpoint integration, remain separate gates. No real Product PR/Metadata publication, #163 v2 Session/model, #292 Ask reply, Nix activation, Runtime rollout, release or Issue #291 closure is implied by merging source-only R5C1A.

## Independent review R1: cross-stage recovery correction

The first independent OpenCode source-only review reported CHANGES REQUIRED with one concrete P2: a retry with an unresolved Push(H1) or Draft(H1) journal could re-enter with a changed Product Worktree at H2/base and allocate or run a new earlier-stage bootstrap/push before discovering the incompatible later-stage receipt. That could strand contradictory original intents.

R5C1A now reads and validates bootstrap, push and Draft journals as one coherent first-lifecycle stream BEFORE planning a bootstrap candidate or invoking any new grant/effect. It checks each original immutable repository/Issue/Record/Contract/branch/base/Metadata/actor/subject capture and rejects any inconsistent head/subject or stage ordering. A missing earlier journal is not authority to create a conflicting effect when a later original journal is pending. Existing exact saved effects remain readback-only and are never retried blindly.

Specific disposable regressions reproduce an original unacknowledged Push of H1 followed by an external reset to base, and an unacknowledged Draft for H1 followed by a changed H2 local head. The second invocation must not create a new bootstrap journal, mutate Product Git, push or create a PR. This correction does not enable a real remote backend or change #337's independent Metadata publisher.
