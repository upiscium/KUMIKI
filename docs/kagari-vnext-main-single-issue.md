# KAGARI #291 — Repository Main: approved-Issue vertical slice

## Phase A — trusted-host initial collaboration (source-only)

This Phase-A candidate is based on `65082ec5e8e3ffcb9b69073b6c30d65cde6328f1`.
It remains **uncommitted, unactivated and pending independent review**. The
historical r7 notes below describe the preceding implementation. No real
GitHub writes, user Project installation or real model Task execution are
part of Phase A; the shipped KAGARI v0.1.0 default is unchanged.

The existing opt-in `tools.kagari_vnext_main_service` module now exports:

```python
MainHostCapabilities(
    read_issue=trusted_issue_reader,
    initial_collaboration_for_issue=trusted_collaboration_provider,
)
build_main(plan, *, host=None)
main(argv=None, *, host=None)
```

A trusted **in-process execution host** supplies these Python capabilities
to the ordinary runner for one cycle, `observe`, or `--follow`. Alternatively
it constructs Main with `build_main` and holds the existing `MainSupervisor`
across cycles. There is no provider CLI option, environment switch, plan
field, arbitrary module/path loader, credential handling or new privilege
system. The selection JSON still only selects exact previously approved
Task identities; it never creates a grant. The default packaged executable
passes no host and refuses new first dispatch with
`MAIN_INITIAL_COLLABORATION_REQUIRED`, before Worktree/Session allocation.

The provider accepts `(issue: int, worktree: Path)` and returns the existing
`InitialCollaboration(facade, metadata_provider, report)`. The facade must be
the real #194 `TaskCollaboration`, rooted in the exact registered #286
Worktree with the same repository/remote/Issue/branch/base. The host installs
the existing `TaskGit` publication endpoint, operation prerequisites,
`GitHubTransport`, execution authorization, immutable `WriteIntent` recorder
and content policy. Main does not supply permissive defaults or implement
unguarded GitHub/shell writes. `establish` remains the sole owner of guarded
bootstrap/push/Draft PR/initial checkpoint establishment; Main checks its
receipt against actual Git subject and re-observed PR/checkpoint provenance
before first Task Session creation and model kickoff.

The existing #217 metadata callback has signature
`(repository, task, branch_ref, pr_number, subject) -> MetadataBinding`.
It must publish the real immutable #144 `explicit-handoff` snapshot with
the exact #191 record/Contract and subject/PR, under genuine host grants.
It owns interrupted publication recovery: persist the exact candidate and
binding with a stable repository/Task/branch/PR/subject/record/Contract key,
not the advancing metadata tip. Re-capturing a different snapshot after an
uncertain write is unsafe. #194 validates/re-reads that binding, and adopts
an already posted identical initial checkpoint without calling the publisher
again. This seam does **not** implement a production publisher, grant source
or network adapter; those are supplied by the external trusted host.

### First-Task adoption and uncertainty

The host can first use the existing `bootstrap_approved_issue` with the exact
approved snapshot and genuine #191 `AuthorityRequest` callback. An identical
existing Task Record is read-only `RESUMED`, without replaying create
authorization. Main then adopts its exact registered Worktree through the
unchanged #286 allocator. The Task need not have been created by this Main
or by the same model Session. The immutable Contract, exact base/branch and
live Issue snapshot still have to match; dirty/unregistered/orphan/retired
bindings are not pristine adoption. A different approved Issue gets its own
independent Worktree and root Session without a per-Job Question.

Before a first kickoff, pre-existing `context.json`, `launch.pending.json`
or `process.json` without either Main kickoff receipt is now
`MAIN_SESSION_RECONCILIATION_REQUIRED`, **before collaboration effects**.
This includes an apparently idle, otherwise valid Session created outside
Main and a crash after Session creation but before writing a kickoff receipt.
It is not safe to infer that the first prompt was never sent. Preserve and
reconcile this state with the host; do not delete locators, synthesize receipts
or launch another Session. After a recorded Main kickoff, existing #163/#292
exact-root verification and Main's pending/sent prompt reconciliation remain
unchanged and retries do not re-create the Task, PR, Session or prompt.

The early Main check is only a preflight: an independent #163 starter may
win while Worktree allocation or PR-first establishment is in progress.
Main therefore uses the Python-only
`start(args, strict_new_session=True)` for a first launch. The starter checks
`context.json`, `launch.pending.json` and `process.json` (including dangling
symlinks) **after acquiring its existing runtime `.lock`**, before allowing
resume or process creation, and retains that lock through Session creation.
State appearing after Main's preflight is refused with
`SESSION_RECONCILIATION_REQUIRED`, preserving the outside Session/runtime
and already authorized #194 artifacts. This is not an unlocked last-minute
recheck or a second lock/authority database. To permit normal restart, Main
first validates an existing kickoff receipt's Issue/Contract/prompt identity;
the exact native Session ID is still verified before accepting or recovering
that kickoff. Invalid or lost receipt evidence requires reconciliation.
The #163 CLI/default `start(args)` retains its idempotent CREATED/RESUMED
behavior; the strict parameter is an in-process caller restriction, not a
public CLI option or an execution grant. A dead Session with unclear prompt
identity is not automatically replaced or re-prompted.

For an already validated kickoff receipt, Main now passes the exact recorded
ID using Python-only `resume_session_id`. This is mutually exclusive with
`strict_new_session=True`. Under that **same** runtime `.lock`, resume-only
requires a regular non-symlink context with the exact receipted Session ID,
no pending launch marker (including a dangling symlink), and intact process
evidence for the expected Worktree/port. The saved PID is only a consistency
field, never authority: #191/Contract and the native exact-root Session are
still verified. Missing/partial/symlink/mismatched state refuses with
`SESSION_RECONCILIATION_REQUIRED` before port checks, process creation or
process-record overwrites. In particular, a valid prompt receipt plus a free
port and retained process record but missing context may **not** create a
replacement Session. Main keeps its post-return Session-ID comparison as
defense-in-depth. Callers supplying neither restriction retain the existing
default #163 CREATED/RESUMED policy; no CLI switch or new grant is introduced.

R2 regression coverage includes a real synthetic Session started immediately
after #194 checkpoint establishment, process-only partial state at the same
boundary, and a second Python process waiting on the real runtime `.lock`
while its current owner publishes partial launch evidence. All must refuse
without a new initial prompt. This is cooperative single-host runtime-lock
serialization, not exclusion of hostile same-UID writers replacing lock
inodes or modifying runtime files without honoring that lock.

### Phase-A evidence and residual gates

`tests/test_kagari_vnext_main_host.py` exercises the ordinary host-installed
runner with real temporary Git repositories/linked Worktrees, real #191
Records, `TaskGit`, #194 facade and #217 snapshot publication/readback. Only
GitHub transport and OpenCode HTTP/model are faithful local fixtures. It
covers first dispatch, pre-materialized pristine adoption, existing initial
PR checkpoint adoption, restarted/follow Main, separate Issues, observation,
absent/denied host capabilities, mismatched bindings, dirty source, unknown
or partial Session state, and stale/unconfirmed checkpoints. Test grant and
metadata journals are fixtures, not a new production authority store.

Production host installation must still provide genuine narrowly scoped
#191/#194/#217 endpoints and a durable publisher recovery implementation.
Actual GitHub writes, real model execution and a real consumer E2E are
Phase B, not claimed by these tests. Independent review and Human-approved
installation/publication remain separate gates. Existing canonical shared
Main lock, unsafe ancestor/nonempty lock refusal, fork-safe release, native
tool Permissions and Human product decisions are preserved.

## Prior implementation context

Status: **isolated r7 source-correction candidate, not committed, published,
merged or activated**. The main branch already contains the independently
reviewed [PR #299](https://github.com/upiscium/KUMIKI/pull/299) (#286
Issue Worktree/cleanup) and [PR #300](https://github.com/upiscium/KUMIKI/pull/300)
(#163 native OpenCode Session), merged by Human approval at
`716491364c8bd0b9504a0941c3616198a1851041`. Newer main
`f33cc3f01d8efc6120e49909f6f56ff1df31cce8` additionally contains
[PR #302](https://github.com/upiscium/KUMIKI/pull/302), the separate
opt-in Worktree `.gitignore` install/repair preflight. Its package entrypoint
`kagari-vnext-worktree-install` remains available alongside the Main.
Current source base `f83f72d250b9b395ad07ed849996e3fb315703c5`
also contains [PR #305](https://github.com/upiscium/KUMIKI/pull/305),
providing the opt-in `kagari-vnext-host-prepare` Install/Repair entrypoint.
The combined Nix package retains this prerequisite together with the Main;
this source candidate does not silently invoke Host Prepare or mint install
permissions. Only Main-owned source, tests, Nix packaging and documentation
are added here. The released v0.1.0 GA default remains unchanged.

## Responsibilities of this first Main slice

`tools/kagari_vnext_main.py` provides a narrow trusted-host API,
`RepositoryMain(store, approved, read_issue, runtime_home, port_for_issue, ...)`.
**All mutating `dispatch(issue_number)` calls require a live
`MainSupervisor(main)` repository-owned execution context**. Calling
`RepositoryMain.dispatch()` directly without an owning supervisor returns
`MAIN_OWNER_REQUIRED` even when the Task Contract and Issue ID are approved;
a separate clone must acquire the same canonical host lock before its Main
can launch a Task. All Main-owned mutations—including PR-first
checkpoint establishment, explicit guarded Draft PR continuation and callback-
backed Worktree cleanup—require the same live Main owner and do not accept a
plan-file/Issue-identity Boolean as a grant. Observe-only access remains
lock-free. The supervisor holds its owner guard across in-flight mutations;
a fork-inherited child closes only its duplicate FD and cannot unlock the
parent's canonical repository lock. Multi-host election and hostile same-UID
process exclusion remain explicitly out of scope.
The explicitly selected `ApprovedTask` set is supplied by the host after
Human scope approval. **Constructing an arbitrary object, or observing an
open GitHub Issue, is not itself an execution grant.** The host must have
already installed a legitimate #191 Task Record containing the exact
immutable requirements and must provide the correct local metadata CAS
capability where creation is needed.

For one eligible ID, `dispatch(issue_number)`:

1. Serializes Main handoff inside the repository with a local advisory lock.
   Same-Issue retries wait for up to eight seconds; if the first owner is still
   active, they return `MAIN_DISPATCH_BUSY` without a duplicate Task effect
   or new per-job Human Ask. A later retry must revalidate Task authority.
2. Re-reads the latest Issue facts (repository/number/open state/title/body)
   through a trusted callback, and checks exact equality with the immutable
   approved #191 Task Contract. An optional helper
   `github_issue_reader("owner/repo")` uses `gh issue view --json` to
   fetch these facts **read-only** from GitHub.
3. Reuses #286 `IssueWorktreeAllocator.ensure` to create or resume the
   exact registered Issue-linked Worktree without resetting the Task HEAD.
4. Before the **first implementation Turn**, requires a trusted host-installed
   #194 `TaskCollaboration` facade with exact Issue/branch/Worktree/Contract,
   operation-scoped grants and durable #217 metadata publisher. The facade's
   `establish()` positively establishes/reconciles exact Task branch push,
   Draft PR, initial immutable Snapshot and initial PR checkpoint. The receipt
   is checked against real Git HEAD and exact remotely retrievable PR facts.
   Missing/denied/uncertain capability blocks model kickoff; a local receipt
   is only a crash/retry locator, never a substitute authority.
5. Reuses #163 `start_root_session` to start or reattach to the actual
   Worktree-rooted OpenCode 1.18.35 server/root Session.
6. Builds the initial Task instruction from the exact approved Issue
   title/body and Contract ID, preserving scope and refusing automatic
   Merge/Release or any other Issue launch.
7. Submits one native `prompt_async` to the existing Session. A
   local operational `prompt.pending.json` is written *before* the
   non-idempotent POST; on acknowledged delivery it becomes
   `prompt.sent.json`. If acknowledgement is uncertain, a retry returns
   `PROMPT_OUTCOME_UNCERTAIN` rather than issuing another model turn.
8. Returns the common #292 Task/Ask status. `observe(issue_number)`
   reconstructs factual #144 Task View from the **exact registered Issue
   Worktree** as soon as it exists, including when the Session has not
   started. Before Worktree allocation it returns the approved Task
   Contract and the observed original Project baseline with
   `branch_matches_task=false`. After Session creation it also reports
   the actual native Session and pending Ask, without duplicating Ask
   state or treating runtime idleness as Task completion.

Only a trusted Main/host calls these methods; no extra per-Task start approval
is requested for in-scope work. The opt-in `kagari-vnext-main` runner now
holds a **host-owned canonical-repository lock**, shared by independently
cloned Project roots on the supported single host/UID. An Issue-local Git lock
separately serializes individual dispatch. This does not implement multi-host
leader election or hostile same-UID exclusion. The production GitHub
transport and initial #144/#217 metadata publisher are **host-supplied** via
the Phase-A in-process interface above, not selected by the CLI; with no installed #194 capability, first dispatch
refuses rather than inventing consent. Full real-Issue Task/Evidence/PR/model
E2E and wider eligible-set scheduling remain unqualified.

## Tested boundaries

The suite `tests/test_kagari_vnext_main.py` uses the real #191 local
Git-metadata fixture, actual #286 Git Worktree allocation, actual #163
child-process launcher, and #292 CLI observations with only the model
server and GitHub Issue reader replaced by local fixtures. It checks
explicit approved IDs, same Task retry, two distinct eligible Issues,
live Issue body drift, reauthorized Task Contract, closed Issue, uncertain
first prompt acknowledgement, concurrency, Task View and no effects on
unapproved IDs.

A separate executable end-to-end smoke script may exercise **real**
OpenCode 1.18.35 with a deliberately fake locally approved Issue:
it validates the same Main-to-Task path, actual native `pwd`
Permission, #292 `once` response, model continuation and factually
unchanged Issue Worktree. It **does not** claim that an actual GitHub Issue
has been executed by KAGARI. The `github_issue_reader` adapter can
independently be read-only tested against an actual repository Issue,
without interpreting issue visibility as execution approval.

## Completion gates

The prerequisite #299/#300 PRs are already merged; this r7 candidate does
not change shipped GA or publish source. The first Main substantial remaining
work is installation of approved production-host #194/#217 capabilities and an actual
authorized
GitHub Issue and Task lifecycle, including setting up a trustworthy
explicit allowed-Issue source, #191 Contract creation from that
approval, bound Task start, continuation/retries, Evidence/PR creation,
Task completion reconciliation, and independent review of the exact
source combination. The cooperative host singleton retains a stable repository-keyed lock
namespace across process restarts and independent same-host clones; crash
recovery still requires factual Task/Session reconciliation. Multi-host
singleton is out of scope.

A later MUSUBI WebUI should consume exactly the common #292 Task/Ask
interface; the optional KAGARI terminal TUI remains Post-MVI #297.
Do not add a duplicate authorization store or new Permission broker
merely to wire the UI.

## Main host runner (opt-in, source candidate)

The separate `kagari-vnext-main` executable provides a **single active local
Main** in a supported ordinary Git repository. It holds one trusted host-owned lock in the fixed
`~/.local/state/kagari/repository-main-locks/` namespace (SHA-256 of the
case-normalized `github.com/owner/repository` identity), not a separate
per-checkout `.git` lock. After a clean exit/crash, the OS releases the lock;
the restarted Main reconstructs actual Task Worktrees and OpenCode Sessions
from existing #191 metadata, Git registration, #163 runtime context and #292
CLI rather than trusting a stale PID or asking to restart each job.

A trusted host provides one finite, explicitly selected list of eligible
Issue/Task identities. This list is **not** the grant itself: each Task must
already possess a legitimate immutable #191 Task Record and its exact live
GitHub Issue title/body. Example host-owned `main-approved.json`:

```json
{
  "schema_version": 1,
  "project_root": "/absolute/ordinary-git-project",
  "repository": "owner/repo",
  "runtime_home": "/absolute/host-state/kagari-main",
  "tasks": [
    {
      "issue": 123,
      "branch_ref": "refs/heads/issue-123",
      "base_revision": "<actual-40-hex-Git-commit>",
      "contract_id": "<actual-64-hex-Task-Contract-ID>",
      "port": 47640
    }
  ]
}
```

The supported commands are:

```sh
# Run one bounded eligible-Issue assignment/reconnect cycle:
kagari-vnext-main --config /abs/main-approved.json dispatch

# Read observations without starting any Task:
kagari-vnext-main --config /abs/main-approved.json observe

# Hold the one local Main lock and continuously reconcile explicit allowed IDs:
kagari-vnext-main --config /abs/main-approved.json --follow --interval 30 dispatch
```

The `--follow` form is a foreground host process; service installation and
activation are **not** part of the tested local source candidate. It emits
one JSON cycle containing every approved Issue, Task status and any block
reason. An absent/bad Task Record or changed Issue blocks only the affected
Task. It does not discover every open Issue or automatically advance to a
new phase.

The native initial prompt is submitted **once**, with the actual immutable
Issue requirements. When the upstream POST acknowledgement is lost, Main
checks the same live root Session for the **exact original user prompt**;
one observed matching user message allows it to mark the existing kickoff
as accepted without another model call. Missing or ambiguous evidence
remains `PROMPT_OUTCOME_UNCERTAIN`; the Main does not blindly repeat it.
No generic Task/approval/Ask database has been created.

The local singleton uses a kernel advisory file lock in a fixed private host
namespace, keyed to canonical repository identity; it excludes cooperating
Main runners even across same-host local clones, but does not provide hostile
same-UID exclusion or multi-host leader election. Even with a
running Main, native Task execution remains governed by #191; human product
decisions, source Merge/Release, the full Task completion/evidence loop and
MUSUBI user interaction remain separate. One active Main does **not** claim
that an OpenCode native task nested at depth=2 is possible.

## Exact Issue bootstrap, read-only progress and guarded Draft PR handoff

The trusted #291 host now has a narrow, explicitly authorized initializer:
`tools/kagari_vnext_issue_bootstrap.py` exports
`bootstrap_approved_issue(store, ApprovedIssueSnapshot(...),
read_issue=..., authorize_create=...)`.

- The caller supplies the **exact previously approved** repository, Issue,
  branch, creation-base commit, title and body. A live read of the same open
  GitHub Issue must match all fields before #191 may publish anything.
- `authorize_create` is the existing #191 **host-issued**
  `AuthorityRequest` callback, not an approval granted by a JSON file or by
  this module. A denied callback means **no Task Record is created**.
- First success publishes the canonical immutable #191 Contract and Record
  with the established metadata CAS mechanism. Same-identity re-invocation
  returns `RESUMED` without issuing another authorization or write.
  Changed, disposed, foreign and stale snapshots fail closed.
- There is deliberately **no public CLI bootstrap subcommand that grants
  itself permission**. Connecting a genuine Human-authorized Issue source is
  a trusted host/Arca integration step. The callback cannot be implemented
  as `lambda _: True` in production merely because the Issue is open.

For ongoing progress, the optional per-Issue host `tasks` entry may include:

```json
{
  "pr_number": 123,
  "evidence": [
    {
      "evidence_id": "<actual immutable 64-hex Evidence ID>",
      "subject": "<actual bound Task commit OID>"
    }
  ]
}
```

These are **observation selections** only, not authority or completion
declarations. `github_pr_reader(owner/repo)` reads the selected PR
through `gh pr view` and rejects forked, foreign, mismatching head branch
or invalid PR identities. The current `Main.observe` projects the exact
existing #144 Task View with that PR and selected #217 immutable Evidence.
Missing or unreadable facts are reported unavailable; no independent
Evidence discovery, fabricated PASS, inferred Task completion or PR
creation occurs. Initial kickoff/Session idle states remain separate from
Task completion.

When **another trusted host capability** supplies an existing fully
configured #194 `TaskCollaboration` object, `RepositoryMain.guarded_draft_pr`
can hand over the exact current approved Issue and Worktree branch to
`TaskCollaboration.ensure_draft_pr`. That existing facade — not #291 —
owns the operation prerequisites, source identity, separate execution
authorization, frozen Write Intent, GitHub postcondition and duplicate Draft
PR reconciliation. The normal Main dispatch and `--follow` cycle **never**
call this method automatically: they may not bypass source-publication
or independent Merge/Release gates. Tests run the positive and refusal
paths against #194's existing hermetic GitHub transport, not a live PR.

These additions remain **r7 source-only** after #299/#300 merged. A
production #194 GitHub/metadata provider installation is still an integration gate;
without it normal first dispatch refuses. No actual user GitHub Issue was
bootstrapped or dispatched,
and no actual PR or Evidence was written by this candidate. Later full
Task life-cycle work must connect verified test/Evidence producers and the
authorized #194 source publication adapter to Task progression.

## Host-selected test execution and immutable Evidence — next lifecycle slice

The vNext package now also contains the **host-only**
`tools/kagari_vnext_test_evidence.py` module. Its
`TaskTestEvidenceRunner(main).run(issue, run_id=..., argv=(...), timeout_seconds=...)`
accepts one explicit test command chosen by the trusted execution host for an
existing, approved #191 Task. It is not an unauthenticated public test command
endpoint or a generic model-command execution interface.

The runner checks the current approved Issue/Contract, exact Git-registered
Issue Worktree, and factual #144 Task View. Before testing, the target Task
must have a **clean** Worktree and a real committed HEAD. The subprocess is
executed with `shell=False`, `stdin=/dev/null`, exact Worktree CWD,
an explicit timeout and bounded test specification. After execution, the
approved Contract and Git HEAD/clean status are checked again. Changes to
the Task HEAD, dirty output, or Issue semantics block publishing a positive
result for the original subject.

Measured result (PASS, FAIL or TIMEOUT), exit status, duration, exact
command identity, stdout/stderr SHA-256 and byte lengths are encoded in the
existing immutable #217 Evidence envelope, bound to the actual Task commit.
Raw command output (which may contain secrets or unrelated paths) is **not**
stored in Evidence. No separately invented Evidence database or test-result
interpretation authority exists. `PASS` means **that command exited zero for
that exact clean commit**, not that all Issue acceptance criteria passed.

Each `run_id` is one operational attempt. Before starting it, the runner
writes a local `started` marker. Before the metadata CAS effect, it
persists the exact immutable `evidence_id` in a candidate marker.
A retry:
- accepts a previously confirmed Evidence after re-reading it from #217;
- can recover a lost metadata publication acknowledgment if the **same
  Evidence object** is positively readable from the canonical metadata ref;
- never repeats the test when execution or publication outcome is unknown.
A different Task head or command must use a new explicit run ID after host
reconciliation.

The returned `evidence_id` and `subject` can then be passed as the
existing `EvidenceRef` selection to `RepositoryMain.observe` / #144 Task
View. The trusted host decides whether to invoke #194 guarded source
collaboration on the current Task; this runner does not automatically create
a PR, grant publication authorization or declare a Task complete.

Local fixture tests exercise actual Git linked Worktrees, real subprocesses,
the #217 metadata CAS, PASS/FAIL, duplicate-run prevention, lost ACK positive
reconciliation, stale Contract, pre/post dirty Worktree and moved HEAD.
These are **isolated host tests**; no product GitHub Issue or PR was modified.

### Explicit Evidence-to-Draft PR transition

A trusted host can call
`TaskTestEvidenceRunner(main).guarded_draft_after_pass(issue,
run_id=..., collaboration=existing_task_collaboration)` **after** a test run.
This host-only method positively re-reads a `confirmed` immutable Evidence
object from the canonical #217 metadata ref, requires actual `PASS` with
exit code zero, confirms that its Contract/subject equal the *current*
approved and clean registered Worktree, and then delegates to the **existing**
#194 `TaskCollaboration.ensure_draft_pr`.

Missing, failed, timed-out, stale-HEAD or unconfirmed Evidence does not
advance to Draft PR creation. Even with a passing test, a host-installed
#194 publication/intent authorization denial blocks the PR. A retry on the
same eligible Task and successful #194 postcondition adopts the same Draft PR
instead of creating a duplicate. No automatic Merge/Release permission is
introduced; this optional continuation is never executed by the normal
Main `dispatch` or `--follow` loop.

The cross-component integration fixture runs an actual test subprocess,
publishes actual #217 Evidence to a disposable Git metadata remote and
passes it to the unmodified #194 FakeGitHub collaboration facade; it verifies
write denial, exact PR creation/adoption, failed tests and advanced HEAD
refusal. This is *not* a live user repository PR or a substitute for
real CI/reviewer acceptance and Human Merge authorization.

The #194 facade may be constructed with its own `MetadataStore` rooted in
the exact **registered Issue Worktree**, whereas Main's canonical metadata
reader remains rooted in the primary Project. Before handing off, Main now
accepts either an identical primary Store (legacy local fixture) or a
separately constructed Store with the exact Issue Worktree root, repository,
remote and native Git branch registration. Unregistered, foreign or
other-Issue stores are refused. A separate integration test constructs an
actual Worktree-scoped #194 facade, confirms Draft PR creation through its
existing approval/intent path and verifies exact duplicate adoption; no raw
GitHub PR mutations are introduced into Main.

## Per-Issue cleanup failure isolation in the repository Main (#299 / #291)

After a trusted operator selects an eligible Issue for **retirement**,
the Main plan may include the *explicit* cleanup field in that Issue's
existing task entry:

~~~json
{
  "issue": 291,
  "branch_ref": "refs/heads/issue-291",
  "base_revision": "<exact immutable Task base>",
  "contract_id": "<exact 64-hex #191 Contract>",
  "port": 47650,
  "cleanup": {
    "pull_request": 97,
    "base_ref": "refs/heads/main"
  }
}
~~~

This is an **operator-selected host continuation**, not authority derived
from a merged PR, an inactive-looking Session or Issue visibility. Without
`cleanup`, the Task retains the original Main dispatch/resume path. Once a
Task is explicitly selected for cleanup, Main does **not** dispatch it again
within a normal dispatch/follow cycle, even if cleanup is refused.

For each selected cleanup Issue, Main invokes the independently qualified
#299 IssueWorktreeCleanup using the exact canonical #191 Task identity,
read-only GitHub merged-PR facts and the conservative same-host #163 runtime
stop check. That backend is the sole owner of any non-force Git
Worktree/branch removals and operational retry journal.

Main isolates cleanup results **per Issue** in its cycle JSON:

~~~json
{
  "tasks": [
    {"issue": 291, "operation": "cleanup", "status": "blocked",
     "reason": "SESSION_RELEASE_UNVERIFIED"},
    {"issue": 292, "status": "ok", "prompt_state": "SUBMITTED"}
  ]
}
~~~

Thus an unmerged/squash PR, dirty or untracked Product data, busy/unknown
OpenCode Session, changed Task/branch or an unavailable cleanup host keeps
that Issue blocked **without terminating Main or interrupting other Issue
Sessions**. The next active Issue continues on its existing independent
Worktree/root Session; a subsequent Main cycle does not resend its model
prompt. Once exact cleanup prerequisites become true, Main returns
`CLEANED`, and further cycles return `ALREADY_CLEANED`. Its Task branch is
not re-created by a stale Main retry because #299's cleanup marker blocks
the allocator. Mid-removal uncertainty is reported as blocked; it is **not**
claimed that the Worktree directory still exists until reconciliation.

The `observe` operation is read-only and never triggers cleanup, regardless
of selected retirement. A configured Cleanup is performed only by an
explicit Main dispatch/follow operation on a human-selected Task. The
supported guarantee is a cooperating **single-host repository Main**, not
multi-host coordination or proof that arbitrary independent processes
are no longer active.

Local integration tests use real temporary Git merges and Issue Worktrees
plus independent fake OpenCode HTTP child processes. They test refusals for
unreleased Session, dirty Product files, unmerged PR and unexpected host
failure; confirm that another eligible Issue starts and remains live; then
confirm safe cleanup/removal and idempotent retry of the retired Issue while
the other Session keeps running. No user Project Worktree was deleted.

## Read-only Main observation does not update Product Git metadata

The opt-in Main observe operation now reads canonical #191/#217 metadata
through a disposable Git metadata store outside the original Project.
The same existing MetadataStore remote-ref/history validation is retained;
only its FETCH_HEAD and fetched Git objects are written to transient Git
storage. The real Project or registered Task Worktree supplies exact HEAD,
branch, dirty status and native Worktree registration for #144 Task View.

CLI observe neither creates Main ownership locks nor Dispatches, prunes,
deletes or updates Project Git metadata/ref/index/FETCH_HEAD. A live
Session's connection locator is checked against the previously verified
immutable #191 Contract and the existing #292 TaskBinding and native
OpenCode root Session status/Ask checks; it does not re-fetch metadata
into the original repository to reauthenticate the same Contract.

The temporary Git object store references original committed Git objects
through read-only alternates but stores every fetched metadata object
and FETCH_HEAD privately. This may add transient disk/network overhead
per observe operation. Any inability to obtain trustworthy canonical
metadata results blocks only that observation. Existing normal Main
dispatch/write paths continue using the unmodified #191 MetadataStore.

Local Git fixture tests confirm original FETCH_HEAD bytes and mtime
remain unchanged both before Session allocation and during an active
Session. Packaged CLI observation verifies the same invariant. This
prepublication fix is confined to #291 source; merged #299/#300 code and
the shipped GA remain unchanged.

### Read-only metadata observer: reviewed remote-selection boundaries

The opt-in Main observation lane fetches #217 metadata in an ephemeral Git
repository, not the original Product Git object directory. The same #191
Task Record and immutable Contract must still resolve, and an active
OpenCode root Session must match its exact Issue/Worktree/Session ID.

Direct file remote paths starting with `./` or `../` are resolved relative
to the **original** Project root before any fetch into ephemeral Git;
resolving against the temporary Git directory could fetch a different
repository or give an inaccurate unavailable result. A named remote is
reconstructed only when the original local Git config has **exactly one**
unambiguous fetch URL. Git NUL-delimited config output is parsed without
trimming away empty values or whitespace: multiple `remote.<name>.url`
entries (including an empty second entry), empty URLs, or URLs with leading
or trailing whitespace fail closed rather than silently choosing a source.

The original Project `.git/FETCH_HEAD` bytes and modification time remain
unchanged during read-only observations with either a named remote or
a relative direct file remote. Unknown/ambiguous remote metadata blocks
that Issue's Task View rather than changing Git state. A changed or
unavailable #163 root Session results in a **per-Issue observation block**,
not termination of the whole Main observation cycle or a replacement
model dispatch. The normal authorized Task dispatch and immutable
metadata publication paths are unchanged.

These tests use only temporary real Git remotes, temporary linked
Worktrees, and mock OpenCode HTTP endpoints. They do not mutate any
existing external user repository.
