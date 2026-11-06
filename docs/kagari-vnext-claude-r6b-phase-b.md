# #340 R6B Phase B — private source-only contracts

**UNQUALIFIED / `dispatch_allowed=False`. Not wired to Main or any CLI.**
`tools/_kagari_vnext_claude_r6b_phase_b.py` adds an internal observation contract,
not an executor. Existing Main dispatch, `lane.dispatch()`, preflight, transport,
the `CLAUDE_EXECUTOR_UNQUALIFIED` gate, Nix packaging/templates and user settings
are unchanged. There is no process/model-call API, callable transport, arbitrary
prompt, receipt writer, runtime root parameter, resume, second turn or retry.

## Existing authority, exact frozen intent

`_observe_main_intent` uses the current **exact MainSupervisor**, holding the
existing Main owner guard throughout. It reuses Phase A's binding observation
and `lane._task_identity` / `task_binding` (#191 Contract, registered Worktree,
branch/base and canonical Project/Worktree/Git common-dir). It additionally calls
`Main._spec(issue)` twice: the selected immutable Claude `ApprovedTask` must
still match the current non-retired Task and the exact live OPEN Issue snapshot.
Selection/store/path/snapshot drift, owner absence/loss and all v2 host maps fail
closed. Read failures return fixed sanitized diagnostics, never exception text.

The frozen, slotted intent binds exactly one existing Lane B **fixed no-tools
diagnostic operation**, native canonical UUID, executor, repository/remote,
Issue, branch/base, Contract, canonical paths, Issue snapshot digest, argv digest,
policy version and expected policy-context digests. It forwards no Issue content
to a model and does not invent a Task or create another authority registry.
Constructing/deserializing an equivalent Python value is **not** Main approval.
The intent's default dataclass repr/string display omits canonical Project,
Worktree and Git common-dir paths and the native session UUID (`repr=False`).
Those fields still participate in equality and explicit serialization/digests;
explicit serialization is private payload input, not a sanitized log display.

These are point-in-time observations, not a durable grant or an adversarial
in-process sandbox. Re-observation, a new UUID or a retained old intent can never
grant dispatch. Pure historical policy/receipt checks do **not** renew ownership
or revalidate a live Issue. Future effect integration must recheck authority at
the effect boundary; ordinary Python immutability is not hostile-process isolation.
The existing metadata/Issue adapters may use network I/O in a real host. This
module is not installed or called there; tests replace those I/O adapters.

## Effective policy is still unproved

`lane._argv` deterministically requests no built-in tools, empty strict MCP config,
the personal settings source and one print-mode diagnostic. Those flags, a
personal plan/profile, preflight's finite settings scan, help/version output,
empty stream tool lists or a successful sentinel are **not proof** of actual
effective policy. In particular, flags alone do not establish:

- Exact binary provenance and installed-version semantics.
- Complete settings-source discovery/precedence, including managed local,
  remote/cloud and dynamically supplied policy.
- Absence of lifecycle hooks, plugin activation, deferred/server tools, MCP
  startup, filesystem/network side effects or profile/environment overrides.
- A stable effective context from qualification through a future effect.

`_PolicyPins` describes expected host-pinned binary, config/profile context,
fully resolved settings, exact child environment and qualification-report
digests. No settings or credentials are read here. `_PolicyEvidence` must match
the exact intent/session/policy/pins/version/personal profile and a closed,
immutable list of explicit checks, with no environment overrides. Missing,
unknown, unchecked, mutable, mismatched or nonempty hook/plugin/MCP/tool claims
are rejected. This phase accepts only the **synthetic qualifier provenance
literal**, not a purported live report, CLI flags or preflight as a qualifier.

Matching a supplied synthetic report establishes self-consistency only. Digests
and provenance labels do not authenticate who collected evidence or that it is
true. The maximum result remains `UNQUALIFIED`, `effective_policy=UNVERIFIED`,
`dispatch_allowed=False`, `task_completed=False`, `ask_capability=UNQUALIFIED`.
**Production hooks are not claimed to be secured.** No trusted live collector,
report verifier, account qualification or effective-policy attestation exists
in this slice. Those remain BLOCKED pending independent qualification.

## Pending-before-effect receipt contract, not storage

`_pending_contract` returns a frozen payload and a relative, digest-only namespace.
It binds exact Task (including canonical Project/Worktree/Git common-dir), intent,
session/operation/policy digests and policy version. The namespace depends only
on **repository + Issue**, not clone location, Git common-dir, UUID, Contract,
remote, branch or policy: changing those cannot select a fresh retry namespace.
A different clone or moved common-dir keeps the namespace but changes exact Task
and intent digests; prior mismatched receipts remain `UNKNOWN` and require
reconciliation. No new persistent authority store is introduced. This is a new
private Phase B namespace; Lane B fixture receipts are neither adopted nor rewritten.

`PENDING_BEFORE_EFFECT` specifies required future ordering; constructing the
payload is **not a reservation, durable write, proof of absence or start grant**.
Result payloads reference the exact pending digest and carry only closed sanitized
status/reason/stage fields. No model/Issue/prompt text, raw environment/settings,
paths, session UUID, auth details, tokens or usage counters are in these receipts.

`_observe_receipts` is pure: `None` is unknown inventory, `()` is caller-asserted
absence without trusted completeness proof, and multiple entries are duplicates
(never “latest wins”). Empty pending and result tuples therefore return
`UNKNOWN / RECEIPT_RECONCILIATION_REQUIRED`, never a clean slate or permission to
start. Pending-only, result-only, interrupted, malformed, mismatched and duplicate
histories also require reconciliation. Even a matching synthetic pair only produces
a non-granting historical observation: **no automatic replay, new UUID retry,
second turn or Task completion**. Inventory completeness, authenticity and
durable/exclusive storage cannot be proved by this API.

Before any future effect, a separately reviewed host integration must implement
private/exclusive bounded storage, durable pending file **and directory** sync
before the effect, retention of uncertain attempts, exact result linkage,
owner/context rechecks, duplicate exclusion and explicit reconciliation without
replay. Deletion/replacement of history or changing repository/common-dir identity
must not be treated as fresh permission; clone location must not partition the
repository/Issue inventory. Crash behavior and recovery need their own tests.
None of that is silently supplied by this payload contract.

## Offline tests

```sh
nix develop --offline -c python3 -B -m unittest -q \
  tests.test_kagari_vnext_claude_r6b_phase_b \
  tests.test_kagari_vnext_claude_lane_b.ClaudeLaneBParserBoundaryTests \
  tests.test_metadata_codec
```

The new tests use real #191 codecs, Task Record reads, binding checks, Phase A,
`Main._spec` and ephemeral MainSupervisor locks. Git/metadata I/O is synthetic;
there is no Git commit/push/fetch, subprocess, model call, real runtime root,
network, credential read or real CLI/auth probe. Temporary paths stay inside the
checkout. Spawn/network/preflight/transport-shaped entrypoints are guarded, and
the source's private/non-launching import/call surface is AST-checked. Existing
Git-backed Lane B/Main integration suites remain separate regressions; their
fixture commits/pushes and fake processes are not needed to test this slice.
