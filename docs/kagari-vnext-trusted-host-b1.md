# #291 Phase B1 trusted host — source-only candidate

This is **not live qualification**, a grant, or a new CLI mode. Phase B2/B3
remain unqualified. Shipped KAGARI v0.1.0 GA is unchanged. The default
`kagari-vnext-main` still refuses dispatch without in-process host capabilities.
Nothing loads a host from a plan, environment variable, Issue body, or path.

`tools/kagari_vnext_trusted_host.py` supplies `assemble_host`, returning the
existing `MainHostCapabilities` and genuine #194 `TaskCollaboration` with
#191-bound registered Worktree/TaskGit and #217 `TaskViewSnapshots`.
The module is copied into opt-in `kagari-control` sources for future host
import; no executable, installed service or automatic host is added.

## Required trusted caller inputs (not provided by B1)

The trusted Python caller must independently supply **all** of:

- exact primary `MetadataStore`, Issue/Task, branch/base, #191 record and
  Contract IDs; exact external repository numeric ID, principal numeric ID,
  default branch and (when observed) PR identity;
- a Worktree-store factory installing a genuine #217 **direct-ref CAS**.
  Client-side `git push --force-with-lease` is not that guarantee. No live
  GitHub CAS backend or server-side direct-ref guarantee is asserted here;
- a bounded branch publisher enforcing the pinned KUMIKI Source Guard,
  destination identity and ordinary non-force exact Task-ref publication.
  Assembly adds a one-use #194 intent check; it does not replace that guard;
- separate operation-specific `OperationPrerequisites` engines with real,
  bounded factual check callbacks, explicit execution `authorize`, durable
  `record_intent`, prose `content_policy`, and `authorize_snapshot` callbacks.
  Prerequisite satisfaction and recovery files never authorize execution;
- a stable, private (0700), user-owned recovery directory explicitly created
  by the host, retained across restarts; an approved, stable initial report;
- separately scoped credentials and external fixture authorization. A host
  may pass `GitHubHTTPS(repository, token)` as `execute`, or inject a bounded
  fake REST executor for synthetic tests. B1 never discovers credentials.

Missing callbacks, missing required engines, a read-only metadata store,
foreign Worktree/authority or unapproved typed intents fail closed. Synthetic
test grants/checks are **only fixtures**, not production policy. Assembly does
not self-authorize the first real Task. Keep credential-bearing executors and
callbacks private to the trusted host, not in model tool/config surfaces.

## Fixed GitHub surface

The agent-facing transport has only #194 protocol methods. REST calls use
`api.github.com` over TLS, exact owner/repo, `/user`, repository/Issue/PR
reads, all-state PR pages, Issue-comment pages, draft-PR creation and comment
creation. No raw path/method operation, shell, redirects, automatic retry or
Link-following exists. Both executor and transport bound pages to 1..100,
100 items/page; responses are capped at 1 MiB and sockets time out at 15 s.
#194 also performs duplicate/drift checks, aggregate limits and exact
pre-/post-observation. This is potentially API-expensive, not rate-limit
qualification or a hard real-time guarantee.

Consumed JSON facts are validated, duplicate keys/oversize responses and
malformed/extra list entries rejected. REST's additional object fields are
ignored and cannot become authority. Null Issue/PR bodies normalize to empty
text; other false-y values are invalid. All writes need a typed #194 intent,
an acknowledged host record after authorization/content checks and a one-use
permit bound to the approved content. PR comments additionally check exact
branch/base/repository/subject. **`edit_metadata` deliberately refuses** with
`metadata_edit_requires_external_exclusion`: GitHub field PATCH is not CAS.
Designing an exclusion protocol is outside this candidate, not silent success.

## Restart/uncertainty contract

The private create-once journal stores only candidate/attempt evidence, no
Task truth, queue, locks, grants or workflow state. Files and directory are
fsynced before remote publication. Partial/foreign/symlinked records block.
The caller must preserve this directory; a new/empty directory is **not** a
safe way to retry an uncertain operation. Single cooperative Adam host only.

For the initial `explicit-handoff` snapshot the stable key includes repository,
Task, branch, PR, subject, record ID and Contract ID, not advancing metadata
tip. `on_candidate` durably records exact candidate and snapshot ID before
#217 CAS. Reconnect confirms that exact commit/object is remotely reachable,
validates its graph and exact #191/PR binding, and returns the same immutable
`MetadataBinding`, even after unrelated tip advancement. If a recorded
candidate is not reachable, it stops with
`snapshot_recovery_required_no_recapture`; it never captures a replacement.
CAS contention requiring a second candidate likewise stops rather than
silently re-keying. Recovery/republication of an unpublished candidate is not
implemented; coordinator/host investigation is required.

Before GitHub creation the transport records an attempt (stable per branch
draft or per target/comment key, independent of observation/view changes).
#194 can adopt an exactly observed PR/checkpoint after lost acknowledgement.
If absent/ambiguous it cannot retry the recorded attempt, even after reconnect;
`recovery_required_no_retry` is the underlying blocker. The facade reports
its existing `draft_pr_outcome_uncertain`/`checkpoint_outcome_uncertain` or
`metadata_provider_failed` at the outer boundary. No new success receipt is
fabricated. Preserve the files and inspect exact remote facts before any
future human-approved recovery; do not delete records to force continuation.

## Qualification still needed before B2

Coordinator-owned independent correctness/security reviews of a frozen source
snapshot; a separately approved, exact single-Issue disposable GitHub fixture;
trusted host identity and grants for #191, each #194 operation, #217 snapshot
and metadata CAS; least-privilege GitHub credentials and repository/principal
IDs; verified Source Guard branch publisher and direct-ref server capability;
stable journal ownership/lifetime and an approved recovery procedure. A host
should not set Main selection `pr_number` until its separate progress-reader
route is also qualified (baseline `build_main` uses its existing CLI reader
for that optional observation). No real fixture/Task/PR/model turn, deploy,
source publication, GA install change or B2/B3 acceptance occurred in B1.

Offline synthetic tests live in `tests/test_kagari_vnext_trusted_host.py`.
Run expensive methods in small shards under the native 120 s command bound;
all REST executes against an injected fake and the real HTTPS constructor is
guarded. Existing Main/host regressions use synthetic local OpenCode HTTP,
not an actual model. Independent review is not performed by this implementer.
