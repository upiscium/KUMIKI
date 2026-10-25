# KAGARI #286 — opt-in Issue-linked Worktree host, P1 candidate

**Status:** Source-only implementation candidate. Not activated in KAGARI GA,
not yet published, and not yet integrated with #163 Session dispatch / #291
Main Orchestrator. Existing independent-clone GA remains the default.

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
  state for exact host reconciliation. No automatic `reset`, `prune`,
  `worktree remove` or branch deletion.

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
Session, decide a semantic specification change, manage a PR, or clean up
Issue Worktrees. It does not install/uninstall/release KAGARI or replace GA's
independent-clone mode. Exact-root OpenCode `serve` Session creation,
subagent delegation, Evidence/Task View progress, recovery and MUSUBI are
separate #163/#291/#288/#292 integration milestones.

The first MVI needs a truthful positive allocator on one eligible Issue before
a higher-level Main can launch a task; advanced teardown, cross-host
coordination and generic plugin security policies must not be invented solely
for this primitive.
