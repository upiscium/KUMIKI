# Main executor selection — unqualified Claude gate (#340)

**Source candidate, not activation.** Main can now explicitly select the
`claude-cli` executor name, but that choice remains **UNQUALIFIED**. Dispatch,
guarded PR continuation and cleanup are refused before their effects. Nothing
here launches Claude, probes an account, installs a host backend, or qualifies
Ask/recovery. OpenCode remains the default.

## Narrow integration boundary

`tools/kagari_vnext_executor_selection.py` is a small name/status contract,
not an executor interface or permission system. Main and its service import
this module; they do **not** import or invoke the private Lane B fixture.
The opt-in Nix `kagari-control` output includes the new source dependency;
there is no new executable or live Claude transport. Shipped GA and generated
templates are unchanged.

This is a deliberately incomplete integration. It makes an unsupported choice
visible and fail-closed without moving OpenCode's Session, kickoff/recovery or
Ask semantics into a speculative generic backend. The existing Main owner,
Issue eligibility, metadata, Worktree and guarded collaboration boundaries
remain in place.

## Selection-file contract

The existing schema-1 selection file gains optional `tasks[].executor`:

| Task choice | Port | Cleanup | Behavior |
| --- | --- | --- | --- |
| Omitted, or `"opencode"` | Required, valid and unique among OpenCode Tasks | Existing policy applies | Existing OpenCode path |
| `"claude-cli"` | Must be absent | Must be absent, including null | Unqualified; no execution or cleanup |

For example, inside an otherwise valid, explicitly approved selection file:

```json
{
  "issue": 340,
  "branch_ref": "refs/heads/issue-340",
  "base_revision": "<exact approved Git revision>",
  "contract_id": "<exact existing Contract digest>",
  "executor": "claude-cli"
}
```

The placeholders above are not valid identities or authorization. Names are
case-sensitive and only these two literals are accepted. Unknown fields,
duplicate JSON keys and invalid combinations fail closed. Direct Python
`build_main` callers use the same plan validator; they do not bypass the
closed executor/task schema. For compatibility, an existing in-process Python
host may omit `schema_version` (implicitly 1); serialized plan files must still
include it. An explicit invalid version is never defaulted. Older installed Main versions reject the new field
rather than understanding it; updating source does not update an installed
host package.

There is no global default-executor override, company/personal scheduler,
directory-based routing, profile path, provider loader, probe flag or
qualification switch in this plan. Existing global `model`, `agent` and
`opencode_bin` settings remain OpenCode settings; they are not translated into
Claude invocation parameters. A future qualified Claude backend will require
a separately installed, explicit fixed personal credential context.

Selection is **not a grant**. It neither creates nor rewrites the #191 Task
Contract. It is an exact trusted-host choice alongside the selected
Issue/branch/base/Contract; it does not make an arbitrary plan an approved Task.
No new durable authority or account-selection record is introduced.

## Frozen in-process choice and effect gates

`ApprovedTask` has a trailing `executor="opencode"` default, preserving existing
four-positional-argument callers. Main clones the supplied values, exposes a
read-only approved mapping and pins their fields. The caller cannot add/change
approved Tasks by mutating its old dictionary, and detected internal choice
drift yields `MAIN_SELECTION_CHANGED`. This is a cooperative in-process
integrity check, not protection from malicious arbitrary Python execution.

For dispatch and direct guarded PR continuation, the order is:

1. Require membership in the explicit approved Issue selection.
2. Require the existing live repository Main owner.
3. Validate the pinned choice and reject Claude with
   `CLAUDE_EXECUTOR_UNQUALIFIED`.
4. Only OpenCode continues to its existing Product dispatch lock, live Task
   validation, Worktree allocation, PR-first collaboration and native Session.

The Claude refusal occurs **before** Product dispatch-lock creation, `_spec`
metadata fetch, allocation, collaboration, executable resolution, auth probe
or model call. It therefore makes **no claim that live Task eligibility was
validated by that rejected dispatch**. Acquiring the existing Main host lease
is still an independent prerequisite and may touch its ordinary host lock.

Cleanup is independently gated. Plans cannot request Claude cleanup, handlers
check their exact pinned selection again, and the supervisor checks before
calling a host-supplied cleanup callback. A blocked Claude Issue does not
prevent a separately approved OpenCode Issue from continuing in the same
cycle. No fallback or executor substitution occurs.

The standalone `kagari-vnext-session` command remains an OpenCode-specific
entrypoint outside this Main selection file. This change is not a new global
permission policy for every host API, nor authorization to call that command
as a workaround for a blocked Claude Task.

## Factual observation, not fabricated native status

Claude observation uses the existing isolated metadata reader, frozen Task
Contract/live Issue checks and Git-backed Task View. It does not call OpenCode
Session/Permission/Question APIs, Claude preflight, or the Lane B fixture.
The original Product `FETCH_HEAD` is preserved by the existing isolation path.

When the per-Issue runtime namespace is absent, the query can succeed with a
factual Task View and this **executor diagnostic**, not a successful execution:

```json
{
  "executor": "claude-cli",
  "qualification": "UNQUALIFIED",
  "dispatch_allowed": false,
  "task_completed": false,
  "session_state": "UNKNOWN",
  "ask_capability": "UNQUALIFIED",
  "effective_policy": "UNVERIFIED",
  "reason": "CLAUDE_EXECUTOR_UNQUALIFIED"
}
```

The observation's `kickoff` is `UNKNOWN`. It has no `pending` list, Session ID,
context locator, `NOT_STARTED` or `NOT_SENT` claim. The runtime diagnostic does
not replace the separate factual Task View or establish Task completion.

Any pre-existing per-Issue runtime name—even an empty directory, ordinary file,
symlink or dangling symlink—or an unsafe/inaccessible ancestor produces
`EXECUTOR_RECONCILIATION_REQUIRED`. Contents are not traversed or reinterpreted
as Claude state. A changed Issue snapshot or invalid registered Worktree is
also rejected through the existing identity checks.

This protects observation from adopting OpenCode state after a selection
change. It is **not** a general cross-executor migration/recovery protocol.
Reloading a plan requires an explicit trusted-host decision; no runtime files
are deleted, renamed or translated automatically. Main cannot discover an
arbitrary external Lane B test cache, and never treats those private fixture
receipts as authoritative runtime data. Persisted executor binding for future
real Claude sessions remains a separate implementation/qualification gate.

## Offline checks and remaining gates

`tests/test_kagari_vnext_executor_selection.py` uses the established local
Git/metadata/fake-OpenCode fixtures. It covers default and invalid selections,
direct-constructor validation, frozen-choice drift, refusal before effects,
truthful observation, runtime reconciliation, cleanup bypass attempts and a
mixed Claude-blocked/OpenCode-running cycle with no duplicate native prompt.
Its package-backed observation test uses a fake GitHub executable and no real
account or model. Package source-parity checks include the new import.

```sh
python3 -B -m unittest -v tests.test_kagari_vnext_executor_selection
python3 -B -m unittest -v tests.test_kagari_vnext_main \
  tests.test_kagari_vnext_main_service tests.test_kagari_vnext_main_host \
  tests.test_kagari_vnext_cleanup_isolation tests.test_kagari_vnext_trusted_host
```

Still required: a reviewed trusted-host transport/authority interface,
exact-version real CLI qualification, explicit account/billing/terms clearance,
an authorized disposable real smoke, then exact Ask/defer/resume and recovery
qualification. Only after those gates can live Main dispatch be considered.
This slice does not complete #340 or authorize host rollout, source publication,
model usage, PR, merge, or release.
