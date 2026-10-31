# #256 R1 — Host attachment admission guard (source-only candidate)

**Status:** nonexecuting v4-native prerequisite, **not a completed KAGARI
Runtime installation**. This file belongs to a new isolated source branch,
based on `main@c56ea920f63e0dbb67352d5f35665946e6b46f45`.

## Purpose and trust boundary

The merged #301/#304 Host Prepare, #310 Attach preview and #327/#328
cross-component tests establish the external opt-in linked-Worktree
`/.worktrees/` prerequisite. They do **not** bind this Host attachment
to an active repository Main or a live Session.

`kagari-vnext-attachment-gate doctor` and
`tools/kagari_vnext_attachment_gate.py::hold` provide a narrower primitive:

- Check an **already existing** canonical primary Project, owner-only
  external state directory, immutable exact package, and existing, canonical
  host-attachment receipt. Neither the gate nor Doctor installs anything.
- Reuse Attach's receipt validation, package binary digests, package/Project
  identities and the existing host-prepare `doctor` to require effective
  `/.worktrees/` exclusion. No separate per-Task Ask or second lock database.
- Hold the **existing Attach namespace inode flock** throughout a bounded
  trusted-host operation. An independently invoked
  `kagari-vnext-attach uninstall` or `repair` cannot change that receipt
  until the guard exits. A second gate immediately fails with
  `GATE_ATTACHMENT_BUSY`, rather than waiting on an unknown host.
- Re-validate exact package, Project, host source, receipt inode/bytes and
  worktree-ignore readiness on exit; unfamiliar, missing, tampered or stale
  state is preserved and rejected. No auto-reconcile of unknown state.
- Return `ATTACHMENT_READY_ONLY` with exact receipt digest, explicitly
  setting `runtime_installed=false`, `main_started=false`,
  `task_execution_authorized=false` and `model_or_session_started=false`.

The public Nix package `.#kagari-control` adds the opt-in
`bin/kagari-vnext-attachment-gate` helper. It uses the package's own
immutable Python sources, not the Project's `flake.nix` or `Justfile`.
Existing Host Attach and Main packages are unchanged. The new binary is not
automatically installed into any consumer Project.

## Explicit opt-in sequence (illustrative, disposable host)

```sh
# After a separate human-approved host install, not from a Task:
<reviewed-store-path>/bin/kagari-vnext-attachment-gate doctor \
  --profile linked-worktree-mvi-preview \
  --project-root /absolute/primary-project \
  --state-home /absolute/private-external-host-state \
  --package-root <same-reviewed-store-path>
```

The only public operation is **doctor**. The Python `hold(...)` context
must be entered by a separately trusted Host and held across the bounded
operation that *relies* on the attachment receipt. It must not be exposed
as an Issue/Leaf approval or called inside a nested Install/Uninstall.
The caller still needs the independent canonical repository Main lease,
a valid exact #191 Task Contract, #163 Session evidence and all other
operation-specific permissions. A receipt digest is **not a capability**.

## Acceptance and negative cases

The disposable fixture `tests/test_kagari_vnext_attachment_gate.py`
uses a real primary Git Project, deliberately inert fake package binaries,
real host receipt/ignore primitives and an independent thread invoking the
real Attach Uninstall. It verifies:

- Absent attachment refuses without creating state, Git objects or ignore
  rules; CLI fails closed with exit 2.
- Explicit Install followed by repeated Doctor confirms the pinned receipt
  without changing its inode, its bytes, or Project HEAD/index.
- Unrelated-but-byte-identical foreign package, symlink receipt, absent or
  negated `/.worktrees/` refuse; no unknown data is removed.
- The guard blocks concurrent Uninstall until exit, and promptly rejects a
  second guard; Project owner bytes and the unstaged ignore rule survive.
- The public CLI never claims Main, Task, Session, Model or runtime activation.

## Still OPEN / not established

This slice does **not** start or stop Main/Session, create an active-runtime
receipt, validate the selected Issues against live Task Records, provide a
background service/daemon, or independently guarantee that a Host using
`MainSupervisor` has enclosed every effect in the attachment guard.
A bare one-shot Doctor is a read-only observation, not a lasting lease.

The next activation slice must have the **trusted** Host integrate this
guard into an owner-checked Main/Session lifecycle, with startup handshake,
one Main per repo, persistently observable active-runtime evidence, graceful
quiescence before detach, crash/no-ACK reconciliation, and actual disconnected
ChatGPT caller E2E. Never infer these properties from Preview success or an
unverified PID; existing #291/#312 boundaries remain separate.

No Commit, Push, Draft PR, Ready, Merge, Release, consumer deployment or
real model/Task execution is implied by this source-only candidate.
