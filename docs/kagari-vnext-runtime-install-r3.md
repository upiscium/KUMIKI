# #256 R3 — Native-v4 Runtime Install facade (source-only candidate)

**Not deployed.** Based on merged #256 PR #330, merge commit
`7e77d2eda29d954bd8db63732b7540217b93dd4c` (reviewed source HEAD
`91402c4a9c5159d118150ec59a917a31c6c7cbc6`). Does not change
#291's independent Trusted Host transport, version rollout, GA 0.1.0,
Project-owned flake/Justfile/AGENTS/OpenCode files, or the existing
`linked-worktree-mvi-preview` CLI contract.

## Intended user-facing contract

The new external immutable Nix package includes `kagari-vnext-runtime`
alongside the already-packaged `kagari-vnext-main`, Session, Attach,
Host Prepare and Worktree Install helpers. On the supported Nix/Linux host:

```sh
kagari-vnext-runtime install \
  --profile native-v4-linked-worktree \
  --project-root /absolute/path/to/ordinary-git-project \
  --state-home /absolute/private/external-host-state \
  --package-root /nix/store/EXACT-kagari-vnext-control
kagari-vnext-runtime doctor  --profile native-v4-linked-worktree ...same exact args...
kagari-vnext-runtime repair  --profile native-v4-linked-worktree ...same exact args...
kagari-vnext-runtime uninstall --profile native-v4-linked-worktree ...same exact args...
```

These commands do **not** evaluate the Project's `flake.nix` or `Justfile`,
start OpenCode, run a model, create a Task, invoke a Project script,
perform git add/commit/push, or grant new permissions.

The new facade **reuses** the exact native-v4 external package and existing
#310/#329 Host Attachment receipt/namespace/Host Prepare rules. It performs
the **existing** Attach package source/binary hash and symlink-boundary
checks for the native `kagari-vnext-main` executable too (without changing
Preview receipt format); no new state file, database, lock,
reservation mechanism or recovery layer is introduced.

On `ATTACHED` or `READY`, `runtime_installed=true` means **the immutable
external Runtime package and Host binding are installed/available**. It
**does not mean Main is running**; the same report explicitly returns
`runtime_running="NOT_OBSERVED"`, `main_started=false`,
`task_execution_authorized=false`, `model_or_session_started=false`.
The old Preview command continues to return `runtime_installed=false`,
because it has no Runtime completeness check.

The package may be present in the Nix store and still not be attached to a
given Project; `doctor` in that case returns `ABSENT`, not a running Main.
Uninstall removes the exact Host-owned Attach receipt, not the Nix store
package, Project's `/.worktrees/` ignore rule, worktrees, Task Records,
Evidence, Git state, independent Sessions or user data.

## Actual Main execution: reuse #291 and PR #330

Only a separately authorized Python Trusted Host that already holds
`MainHostCapabilities` can call
`kagari_vnext_runtime.launch_with_host(..., host=capabilities)`.
It verifies the exact plan Project, the installed Runtime/receipt and
existing Host preflight, then delegates into
`kagari_vnext_main_service.main(..., host=..., attachment=...)`.

That Main continues to enforce the existing Issue/Contract/Worktree,
Session, GitHub/Task effects and #330 paired Attach/Main lock rules.
The public Runtime CLI **does not** allow a plan or flag to synthesize these
capabilities and **does not** have a model-start action. This is a usable
host-packaged Runtime install surface, not proof of the separate #291
caller-independent model Task E2E.

## Deliberately bounded D03 and D04 policy

- Missing attachment/preparation receipts: use the existing owner-aware
  `repair` operation. Repeated Install/Repair remains idempotent.
- Corrupt/foreign receipts, unknown staged files, changed package identity:
  `BLOCKED` without deleting or silently rewriting evidence. No new
  automatic recovery or cross-version migration; upgrade is user-directed
  Uninstall then Install with a newly selected immutable package.
- Normal Uninstall keeps existing Attach inode-lock exclusion. If an active
  attached Main owns the lock, Uninstall waits for it to exit. Stop the
  running Main before an ordinary Uninstall; no new force-kill subsystem
  or automatic Task/Session cleanup is introduced by this slice.
- Existing Project-owned `flake.nix`, `flake.lock`, `Justfile`,
  `AGENTS.md`, `opencode.json`, and `.opencode/**` are never rewritten
  by this facade. #304's expressly approved `/.worktrees/` addition to
  Project `.gitignore` is the sole specific exception, and not committed
  automatically. Malformed settings may still block **actual OpenCode Task
  execution** and are not automatically repaired.

## Acceptance evidence and remaining dependencies

New source-only `tests.test_kagari_vnext_runtime` uses disposable Git and
an intentionally inert executable payload. It exercises Install/Doctor/
Repair/Uninstall/Reinstall, missing/corrupt receipts, foreign package,
missing Main binary, broken owner config preservation, and no-model
capability boundaries. The actual packaged Nix CLI and full regression
suite must be checked separately.

Not qualified in R3: automatic Host process startup/stop, live forced
Uninstall, real-model instructions/config precedence, #291 full detached
execution, real Session/Ask interoperability, crash recovery, durable
runtime observability, or release/consumer deployment. D03/D04 minimal
policy is recorded in #256. Deferred crash/restart redesign is #335/#336.

**Publication authority:** Commit, Push, Draft PR, Ready/Merge and Release
remain separate Human gates. R3 adds none of these authorizations.
