# #271 — Generic TaskSpec → native parallel subagents → v4 (local candidate)

This is a **local, disposable, TEST_ONLY_UNQUALIFIED** integration, not a
production KAGARI/KUMIKI 0.1.0 release, generic installer or source Publication
authority.

**AGENTS compatibility update (2026-10-16):** The current source and test-only
packaged executor now use the separate read-only agents compatibility inspector
before model execution. Existing exact AgentCore-v3 lifecycle conflicts return
BLOCKED / AGENTS_COMPAT_ASK_REQUIRED with a preimage-bound proposed patch. An
unconfirmed custom prohibition returns a manual-review BLOCKED, not a false
automatic Ask. A separately implemented **local foreground-TTY TEST_ONLY
Human Ask engine** can create, explicitly answer, verify and revoke
two exact scoped grants. The native Task runner optionally accepts both
--agents-ask-root and --permission-ask-root, revalidates those grants
before model and Product operations, and records request/decision identities
in native v4 Contract/Evidence. A legacy test-only task-deny operator flag is
separately identified and never misreported as a Human-approved grant.

Neither this local TTY mechanism nor the user's general design-policy approval
constitutes a real authenticated ChatGPT/Work approval or permission to alter
a production Project. The earlier MUSUBI/TERREATE real-model R3 Task PASS
predates the Human Ask path and is historical evidence only. The prior slugify/labels demonstrations were bound to hardcoded
Product code. This tool now reads **arbitrary ordinary Git Project structure**
and an explicit TaskSpec/Plan and uses two actual OpenCode native Task leaves.

## M1 real Product-code acceptance findings (2026-10-17 JST)

The first disposable MUSUBI Product-code Task was BLOCKED, not PASS. Two
genuine concurrent native children edited both authorized Python product
files, but the host refused OpenCode session exports obtained via a Python
subprocess pipe. OpenCode 1.18.35 returned exit 0 and a truncated 192 KiB
JSON stream; direct file-backed output provided complete valid JSON. The
updated TEST_ONLY exporter uses an exclusive external Evidence file and
verifies its exit code, JSON structure and 3 MiB size after child completion.
Actual failed-session readback via this fix independently verified both
children and positive overlap. No retroactive Task success is claimed.

The original child A change also failed one preexisting observability test:
a mocked sys.stdin=io.StringIO does not have a .buffer attribute. The new
acceptance fixture passed but the 41-case legacy observability suite did
not. The original Product changes were preserved only as uncommitted
diagnostic diffs; the two specific grants were revoked and AGENTS restored.

**Open P2 (release blocker):** The file-backed exporter checks the 3 MiB
size only after its bounded 35-second subprocess exits, not while writing.
An attempted child-process RLIMIT_FSIZE limit broke OpenCode's SQLite
wal_checkpoint operation, so it was rejected. A suitably scoped write-time
output cap is not implemented, and this candidate remains
TEST_ONLY_UNQUALIFIED. Do not assert production runtime boundedness.

**Follow-up M1 R2 finding:** The file-backed exporter successfully verified
two real child Product writes. The actual product source changes independently
passed all 156 project test cases (2 acceptance plus 154 existing). The host
still BLOCKED the Task at its staged/dirty preservation check: the previous
check assumed that the complete `git diff --name-only` set remained identical,
which is impossible when KAGARI is authorized to modify *already tracked*
Product files. The updated local candidate captures the initially tracked
authorized Product names and permits precisely those new worktree-diff paths
in addition to preexisting dirty paths, while separately rechecking exact
staged entries/index/HEAD/config/refs and the full physical Product inventory.
New tracked/untracked and unexpected-dirty-path regression cases pass; the
read-only independent reviewer returned SCOPED PASS. This does not authorize
arbitrary new Product paths or modifying the unrelated staged README.

M1 R2 is **historically BLOCKED**, with no host product commit; source-only
Product changes, logs and Evidence were preserved as diagnostics. Its exact
Ask grants were revoked and Project AGENTS restored byte-for-byte. A subsequent
M1 Task must run under a fresh immutable package/Clone with its own exact
Human Ask grants; neither the original R1 nor R2 can be retroactively promoted
to PASS.

## TaskSpec optional-inspection contract (#315, opt-in source candidate)

The existing **TaskSpec schema_version=1 remains a closed exact-field contract**.
It cannot include optional-read metadata. Native Plan schema_version=1 and
the bounded two-leaf protocol remain unchanged.

For new, explicitly host-approved test-only tasks only, **TaskSpec
schema_version=2** requires the additional
`optional_exploration_paths` array. The shared TaskSpec loader defaults to
rejecting v2: only the native generic executor explicitly calls it with
`allow_optional_v2=True`. Existing GA/RC lifecycle entrypoints use the
default and continue to admit v1 only. Each entry is a canonical
Project-relative path independently validated by the host; at most 32
nonduplicate paths, excluding Git/Project configuration and any path
overlapping an assigned Product. The TaskSpec's immutable bytes and consent
are the source of that classification; a model's instruction, tool error
message or status report never adds paths to the approved list.

A failed `read`, `glob`, `grep` or `list` is diagnostic only if its
verified canonical target exactly matches a host-declared optional path,
does not escape the Project, **and** the native tool error is exactly a
file-not-found result for the requested path. In OpenCode 1.18.35, this is
`state.status="error"` with `state.error="File not found: /exact/path"`.
Plain error status, permission denial, missing/malformed error details,
or other tool failure remains `BLOCKED` even on a whitelisted path.
Unknown necessary inputs and failed Product final-readbacks remain
`BLOCKED` as well. A tool version with different error semantics must
be requalified rather than silently treating unknown errors as optional. Successful Product editing,
post-edit reads, host tests, commit and Evidence are independently required.

This source change **does not modify an already released package**, old
consumed one-shot Jobs or past BLOCKED records. Schema v2 needs its own
reviewed source publication, package tests and release qualification.

## End-to-end flow

1. Host reads `kagari_v010_general_task_spec.py` and
   `kagari_v010_basic_plan.py` (copied, hash-identical from reviewed C/D
   development candidates). Require exact TaskSpec v1 (or explicit opt-in
   TaskSpec v2 for host-classified optional inspections), closed Plan v1,
   one exact Git HEAD,
   two nonoverlapping subagent steps owning all allowed Product paths, a joining
   primary step, and at least one declared test. This integration currently
   permits planning=specified or host-observed planning=auto in the local
   TEST_ONLY profile. In auto mode the host captures and hashes actual model
   JSONL, validates the Plan against the TaskSpec, and only then dispatches.
   The provider identity is not independently attested and the procedure is
   not production-qualified. The original Project branch and physical
   inventory, Git index and settings are snapshotted before execution.
2. The host creates a **separate 0700 runtime/evidence directory** outside
   the Project and writes the exact Task Contract and Record using existing
   native v4 `TaskRecords` and `MetadataStore` with a standalone local bare
   `metadata.git` and exact direct-ref CAS. It does not use a GitHub remote.
3. The host writes **external** OpenCode role definitions under its runtime
   directory. The primary has no edit or shell authority and has only the two
   narrowly allowed native `task` subagent roles. Each child is
   `mode: subagent`, may edit only its assigned Product paths, and cannot
   delegate or use a shell. Models are chosen from the user's configured
   OpenCode profile; no `--model` override. Existing Project
   `AGENTS.md`, `opencode.json`, Justfile and flake are never overwritten.
   Project AGENTS instructions are included in the bounded parent prompt as
   well as loaded normally by OpenCode.
4. The host runs one real configured OpenCode primary. Require **two actual
   `task` tool calls in a single parent assistant batch**, distinct child
   sessions, common parent, and strictly overlapping live child intervals.
   Export actual parent/child sessions and verify each child's bounded
   `apply_patch` or `edit` tools only touched its own Product files; the
   child must subsequently read its own changes and return unambiguous
   `COMPLETED` status. A failed read-only inspection never excuses a
   failed write or required input: only exact TaskSpec-v2 whitelisted,
   in-Project, optional inspection errors are tolerated.
5. Verify the complete Project physical change set including ignored entries
   and modes; only the allowed Products and necessary new parent directories
   may differ. Preserve preexisting index/staged/dirty data, Git config,
   HEAD/branch and existing refs. Run the declared literal-argv tests under
   **separate explicit test execution consent** in the disposable Project.
   Tests cannot mutate Product bytes or Git metadata.
6. The **host only** stages and commits exactly the Product paths with Git
   hooks disabled. The model/children never stage or commit. Check commit
   parent/scope, prior staged/dirty changes and physical file snapshots.
7. Bind a product-commit-specific v4 Evidence object to the original Contract,
   parent/child session IDs, a canonical SHA-256 digest of the exact test receipt
   list, and transcript/export hashes. The receipt file remains separately
   stored; its canonical JSON must match the Evidence digest on readback.
   The fresh-process readback also verifies each retained test log against
   its receipt SHA-256, and the raw OpenCode transcript and parent/child
   session exports against their Evidence digests. Missing or changed files
   safely block qualification. Persist a native Task View snapshot and read
   back Contract/Record/Evidence/View in a new Python process. All evidence is retained under the private
   runtime directory.

Example **local only** invocation (all inputs/target are separately
prepared disposable paths; no command below touches a real user Project):

```sh
nix develop --command python3 -B tools/kagari_v010_native_generic_task.py \
  --execute-model --run-tests --disposable-project \
  --project-root /absolute/private-scratch/project \
  --task-spec /absolute/private-scratch/task-spec.json \
  --plan /absolute/private-scratch/task-plan.json \
  --opencode /nix/store/PINNED-opencode/bin/opencode \
  --output /absolute/private-scratch/evidence \
  --repository fixture/kagari-native --task 271
```

The `--repository` identity and numeric `--task` here are **disposable
metadata ownership labels**, not permission to post to GitHub. The runtime
refuses missing explicit model/test/Project consent and refuses inaccessible,
misconfigured or noncanonical source paths.

## Meaning and limitations

Success means `REAL_NATIVE_GENERIC_TASK_TEST_ONLY_PASS` for the **bounded
positive profile** only: two native model-backed leaves concurrently
implemented a Project-independent declared Task; the host verified tests,
made the scoped local product commit, stored native v4 Evidence and read it
back. It does not assert universal Task success, hostile same-UID isolation,
provider transcripts cryptographically authenticated, arbitrary Git writer
exclusion, arbitrary tests sandboxed, distribution/package production
readiness, persistent runtime upgrade, production-qualified auto Planning,
real user Project installation, draft PR/checkpoints, GitHub publication or release.

This is explicitly a *disposable* Project tool. An arbitrary test executable
can have side effects outside the Project; it is not an OS sandbox. Runtime
role-specific Task permissions may interact with Project `task:deny`.
The user has approved this test-only native delegation, **not silent
overriding of an existing Project policy**. A real MUSUBI/TERREATE
integration must explicitly resolve the Task authority boundary instead
of rewriting Project `opencode.json`. Role configuration and normal provider
configuration are separately owned.

This tool and unit suite are new uncommitted candidate files in the B lane;
the source bundle/packaging/CI and exact Publication/Ready/Merge/Release
authority are still pending. Frozen crash/recovery experiments remain frozen.

Focused no-provider tests:

```sh
nix develop --command python3 -B -m unittest -v tests.test_kagari_v010_native_generic_task
```

## Accepted v0.1.0 resource limitation — deferred beyond 0.1.0

OpenCode Session Export uses a private 0600 direct output file, 35-second
timeout and a **3 MiB check only after writing**. Oversized exports do not
qualify as successful Evidence, but peak temporary disk consumption while
writing is not capped at 3 MiB. The Human accepted documenting this LOW-priority
risk for v0.1.0 instead of implementing a new output cap now.
See [Known Limitations](kagari-v010-known-limitations.md) and
[post-v0.1.0 backlog #276](https://github.com/upiscium/Templates/issues/276).
This deferral does not weaken Task, Contract, Evidence or Human Ask checks.

## #274 — Work Unit completion vs host acceptance (local candidate)

A native leaf completes only its own authorized Product edit and a final
read of the modified file; it does not execute TaskSpec tests, stage/commit
Git, or generate v4 Evidence. When leaf edits and final readback succeed, the
leaf reports status: COMPLETED and explicitly states
tests: NOT_RUN_BY_LEAF (HOST_OWNED). KAGARI itself subsequently runs declared
tests, checks Project scope and Git preservation, creates the Product-only
commit, and persists/reads back Contract, Task Record, Evidence and Task View.

If an actual leaf-owned operation (such as an authorized file edit or final
readback) fails, the leaf must instead report status: BLOCKED with its cause.
The host must continue refusing that state; BLOCKED is never silently
promoted to COMPLETED. An unexecuted host-owned test is not in itself a
leaf failure, but it cannot be reported as PASS.

This clarification addresses the observed #271 M2 first attempt, in which
one leaf changed its assigned file yet returned BLOCKED solely because its
no-shell role could not execute host-owned tests. A subsequent attempt was
successful; the earlier result is retained as BLOCKED, not reclassified.
Prompt clarity improves repeatability but is not a deterministic model-output
guarantee, a new grant, or permission to release the TEST_ONLY candidate.

## R10 source-only correction: prevent tool-free planner's initial Git write

First R9 MUSUBI auto Planning in a new ordinary Clone was BLOCKED because
OpenCode initialized its .git/opencode workspace ID while the tool-free
Planner used the Product Project as cwd. With the same Git state initialized,
R9 completed its full 156/156 native model acceptance and revoke/reinstall
matrix without changing any configured Native Task permissions.

The R10 Planning-only correction runs the read-free/Tool-free Planner from
the private external Evidence root instead, while retaining **all**
physical Project, Git-control, HEAD/ref/index/config, staged work and
Human Ask grant revalidation. It does not exempt .git/opencode from
modification detection or change the editing-capable native primary's
explicit disposable Project cwd. See [#278](https://github.com/upiscium/Templates/issues/278).
