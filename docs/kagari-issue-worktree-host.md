# KAGARI #286 — opt-in Issue-linked Worktree host, P1 candidate

**Status:** Source-only opt-in next-MVI candidate in Draft PR #299,
not Merge-approved, not activated in KAGARI GA and not yet integrated with
#163 Session dispatch / #291 Main Orchestrator. The allocator P1 and
explicit post-Merge cleanup are separate host operations. Existing
independent-clone GA remains the default.

## Responsibility and minimal contract

`components/agent-core-v4/issue_worktree.py` exposes
`IssueWorktreeAllocator(store).ensure(...)`. The trusted caller must have
already selected an eligible externally designed GitHub Issue and established
its immutable #191 Task Record and Contract through its authorized path.

This allocator **never creates or reauthorizes** a Task Record. It reads that
record from `refs/agentcore/metadata` via the installed `MetadataStore`,
refuses Human-cancelled/superseded Tasks, and checks exact expected Contract ID,
Task number, bound full branch ref and original creation-base commit.

The host invokes the narrow API, for example:

```python
from issue_worktree import IssueWorktreeAllocator

allocation = IssueWorktreeAllocator(existing_metadata_store).ensure(
    task="286",
    branch_ref="refs/heads/issue-286",
    base_revision=immutable_task_creation_base,
    expected_contract_id=immutable_contract_id,
)
# allocation.status: CREATED or RESUMED
# allocation.worktree: exact registered Project/.worktrees/286
```

`MetadataStore` must already be constructed for the validated ordinary
primary Git Project and canonical owner/repository. Its `origin` is the
existing Task metadata remote. No extra credentials, grant store, or
`KAGARI_WORKTREE_APPROVED=true` style authorization is introduced.

The primary Project's **tracked** `/.worktrees/` Git ignore rule must already
exist; the allocator refuses an unignored managed namespace rather than
silently making the user's original checkout dirty.

## Lifecycle

- **CREATED:** Branch absent, expected target absent, and canonical #191
  Contract matches. Standard `git worktree add` creates a linked Worktree
  under `<original-Project>/.worktrees/<Issue-number>` from the immutable
  creation-base commit. Verify the native Git registration, exact HEAD/branch,
  shared Git directory and reciprocal `.git` pointer afterwards.
- **RESUMED:** A previously registered exact Issue Worktree and branch both
  exist with a matching current HEAD. A Task branch may have legitimately
  advanced from its creation-base; do not reset or re-create it.
- **REFUSED / blocked:** Invalid/stale Contract, disposed Task, unregistered
  target, orphan branch, conflicting registration or changed Git pointer are
  explicit `IssueWorktreeError.code` conditions. The caller reports the code
  and does not run OpenCode.
- **AMBIGUOUS:** Git `worktree add` failure or partial result reports
  `WORKTREE_CREATION_AMBIGUOUS`, retaining any unique directory/ref/admin
  state for exact host reconciliation. No automatic reset or prune; the
  separately guarded cleanup described below is the **only** supported
  path for Worktree/branch removal after verified Merge.

Same-Issue local calls serialize their Git effect with a small host-owned lock
inside the ordinary Git admin directory. This is local **operational**
coordination, not Task authority or a multi-host consensus service. Different
Issues have distinct branches and Worktrees. Product and metadata decisions
still come from the Task Record/Git/GitHub, not the lock file.

## Bounded tests and evidence

The isolated fixture suite `tests/test_issue_worktree_v4.py` exercises actual
Git linked Worktree creation/resume/two-Issue separation, Task Record
reauthorization and cancellation, dirty/staged/untracked preservation,
advanced-branch resumption, orphan branch/unregistered path refusal, symlinked
namespace or changed Git pointer refusal, two-caller serialization, and an
injected partial `git worktree add` result. Fixtures use a temporary local
Git repository and local bare metadata remote; **no production repository or
GitHub Issue changes** occur.

Run from the isolated source clone:

```sh
nix develop --no-write-lock-file --command \
  python3 -B -m unittest -v tests.test_issue_worktree_v4
```

## Explicitly outside this P1 slice

This does not automatically select Issues, schedule models, supervise a
Session, decide a semantic specification change, manage a PR, or
**automatically** clean up Issue Worktrees merely because a PR is merged.
The host must invoke the separate guarded cleanup after positive PR/Session
verification. It does not install/uninstall/release KAGARI or replace GA's
independent-clone mode. Exact-root OpenCode `serve` Session creation,
subagent delegation, Evidence/Task View progress, recovery and MUSUBI are
separate #163/#291/#288/#292 integration milestones.

The first MVI needs a truthful positive allocator on one eligible Issue before
a higher-level Main can launch a task; advanced teardown, cross-host
coordination and generic plugin security policies must not be invented solely
for this primitive.


## After PR Merge — conservative Issue Worktree cleanup (AC-WT-04)

The new **opt-in, host-owned** module
components/agent-core-v4/issue_worktree_cleanup.py exports
IssueWorktreeCleanup(allocator, read_pull_request=...,
session_released=...).cleanup(...). It reuses the exact same per-Issue Git
lock and existing #191 Task Record/immutable Contract as ensure.

The trusted Main/host may initiate this **after the same Issue's PR was
merged** and its Task no longer requires a Worktree. There is deliberately
no unattended periodic deletion merely because an Issue is closed or an
OpenCode Session appears idle. A read-only GitHub PR verifier is provided by
github_pull_request_reader("owner/repo"). A positive session-release verifier
for the #163 runtime is provided by
stopped_session_from_runtime(absolute_runtime_dir). It returns false for a
live PID, listening server, missing context/process records or mismatched
Task; it never kills or detaches a Session.

A host wiring example, *not* a grant of GitHub or source authority:

~~~python
from issue_worktree import IssueWorktreeAllocator
from issue_worktree_cleanup import (
    IssueWorktreeCleanup, github_pull_request_reader,
    stopped_session_from_runtime,
)

cleanup = IssueWorktreeCleanup(
    IssueWorktreeAllocator(existing_canonical_store),
    read_pull_request=github_pull_request_reader("owner/repo"),
    session_released=stopped_session_from_runtime(
        absolute_runtime_dir_for_this_exact_issue),
)
result = cleanup.cleanup(
    task="123",
    base_revision=immutable_task_creation_base,
    branch_ref="refs/heads/issue-123",
    expected_contract_id=current_immutable_contract_id,
    pull_request=exact_completed_pr_number,
    base_ref="refs/heads/main",
)
~~~

**Delete gates (all required):**

- Current canonical #191 Task Record/Contract still identifies that Issue,
  branch, base and Contract; a cancelled/superseded Task or unknown metadata
  is not adopted.
- Live GitHub PR state is actually **MERGED**, from the exact repository
  and Task branch, with the exact source HEAD and an explicit merge commit.
- The primary Project is checked out on the specified base branch.
  Git ancestry shows that **the whole Task HEAD is included in the merge
  commit**, and that merge commit is reachable from local base HEAD.
  Thus committed Product changes remain preserved. **Squash/Rebase merges
  and stale local main without this ancestry proof block automatic deletion**
  until explicit Human reconciliation. A GitHub Merge label alone cannot
  establish preservation of unique committed work.
- The matching Task Session is verified stopped. The built-in same-host
  verifier requires exact #163 context/process records, dead PID and
  non-listening loopback port; any unknown observation blocks.
- Exact native linked-Worktree registration, branch, reciprocal Git pointer,
  common directory and HEAD match. Locked/prunable/foreign registration,
  staged, unstaged, untracked or **ignored** data all block deletion.
  Dirty/staged state in the original Project and sibling Worktrees is
  preserved.

After verification, the host writes an **operational-only** intent under
.git/kagari-issue-worktree-cleanups/<Issue>.json before external Git effects.
It uses normal git worktree remove (never --force), followed by normal
git branch -d (never force). It never resets the original Project, updates
main, prunes Git admin, deletes metadata/Evidence or changes GitHub.

If the removal partially succeeds or an acknowledgement is lost, a retry
with exactly the same PR/Task/head/merge identity re-verifies facts and
completes the remaining step. A repeated finished request returns
ALREADY_CLEANED. An existing cleanup marker prevents the old allocator
from resurrecting that Worktree on a stale Main retry.

The same-host lock prevents cooperating ensure/cleanup collision. The
trusted Main must coordinate root Session release and next dispatch.
Multi-host or independent unregistered Session ownership is outside
the current MVI. **Uncertain cleanup means retain the Worktree and branch.**

Tests: tests/test_issue_worktree_cleanup_v4.py uses disposable real Git
Worktrees and merge commits, fake PR facts, live/stopped process and port
observations, plus injected lost acknowledgements. It covers removal,
idempotence, interrupted removal recovery, source/sibling preservation,
unmerged/squash/stale-HEAD refusal and dirty/untracked/ignored/locked/
live-Session refusal. **No existing user Worktree is deleted by these
tests.** KAGARI v0.1.0 GA remains unchanged; this source is opt-in until
reviewed, merged and integrated with the #163/#291 trusted host.
