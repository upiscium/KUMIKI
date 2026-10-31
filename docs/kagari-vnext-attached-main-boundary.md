# #256 R2 — Bound Attach / Main ownership (source-only candidate)

**Status: implementation candidate, not deployed.** Source baseline
`upiscium/KUMIKI main@7a89bb363a69a3c86fc2a50a8edc63b57f0fa9e3`
(after #329). KAGARI Runtime installation, long-lived process handoff,
active-runtime receipts and live Session/Ask end-to-end remain **OPEN**.

## What is now bound

A trusted Python Host can supply `MainAttachmentConfig` (exact external
state directory, immutable package and explicit linked Worktree MVI profile)
to `kagari_vnext_main_service.attached_supervisor` or to the
Python-only `main(..., host=..., attachment=...)` injection.

1. The existing #329 `kagari_vnext_attachment_gate.hold_verified` acquires
   the **same Attach namespace inode flock** that governs
   Install/Repair/Uninstall. It confirms the original Attach receipt, exact
   primary Git Project/package source identities and effective #301/#304
   `/.worktrees/` ignore preflight. A missing/changed/foreign receipt refuses.
2. Inside that hold, the existing #291/#312 `MainSupervisor` acquires the
   **same canonical per-repository Main flock** used by Main Control, not
   another lock or semantic Task database. A colliding Main refuses.
3. For a host-bound Main, `MainSupervisor._assert_owned` checks **both**
   its existing live owner lock and the `AttachmentLease.verify` proof
   before dispatching any Task/Session effect or executing a cleanup handler.
   The same assertion already guards direct `RepositoryMain.dispatch`.
   An altered Project, package, receipt or ignore preflight blocks before
   the next operation with `MAIN_ATTACHMENT_UNVERIFIED`.
4. The bound Supervisor retains both holds for the entire Python context,
   including repeated `--follow` cycles injected by the trusted Host.
   Uninstall/Repair cannot remove the receipt before the bound Main exits.
   The Supervisor is released first, then the outer Attach lease.
5. The lease also remembers the original process PID. A forked child cannot
   claim the parent's attachment and cannot release the parent's inode flock
   when it exits an inherited Python context.

Existing unbound Main/observe behavior remains available for independently
authorized uses. A caller explicitly requesting bound `observe` is refused
rather than silently executing an unbound path. The public CLI/config file
cannot add a Host-issued `MainAttachmentConfig`: it is Python-only injection.

## Example of the bounded trusted-host interface

```python
from pathlib import Path
from tools.kagari_vnext_main_service import (
    MainAttachmentConfig, attached_supervisor,
)

# 'main' is a separately built RepositoryMain with exact already-authorized
# #191 Task Records and #194/#163 Host capabilities, NOT created by this gate.
binding = MainAttachmentConfig(
    state_home=Path("/absolute/external-host-state"),
    package_root=Path("/nix/store/exact-reviewed-kagari-control"),
)
with attached_supervisor(main, binding) as runner:
    result = runner.cycle(dispatch=True)  # still subject to ALL prior gates
```

Merely constructing `MainAttachmentConfig`, reading a Preview receipt,
holding either lock, or observing a successful status **never grants**
Task/Model/Session/Source publication authority. The caller is the trusted
Host, not an Issue Task, Leaf or ChatGPT/RDC command. A plan-file field does
not select or fabricate this binding.

## Source-only acceptance evidence

`tests.test_kagari_vnext_attached_main` reuses the existing real-Git
MainService fixture with an inert fake immutable package, synthetic #191
Task/contract, and fake OpenCode endpoint. It exercises:

- Absent attachment refuses **before** creating a canonical Main lock or
  sending any fake model prompt.
- The real Main ownership lock and the existing Attach namespace lock
  remain acquired together. Another local Main and independent process
  cannot enter; the synthetic kickoff emits one prompt, and an identical
  repeated cycle does not emit a duplicate.
- Effective ignore negation is detected on an effect-time check; no
  synthetic model kickoff occurs, and unrelated Git HEAD/index stay intact.
- Live Uninstall waits until attached Main releases; owner untracked bytes,
  Git HEAD and index remain preserved. Existing Task Records and Evidence are
  outside the candidate's owned uninstall scope, not separately deleted.
- Forked child context exit cannot unlock the parent's Attach lease. Wrong
  project or stale/closed lease cannot be used as verification.
- Changed package binaries fail closed, release the Main lock after error,
  and require separate intentional repair. Explicit attached observe
  is not silently downgraded to unbound observation.

This suite is **not** a production OpenCode/model execution, detached
ChatGPT browser handoff, consumer installation or recovery proof. No
external GitHub write/PR/Release/consumer mutation is performed by tests.
The pre-existing source-only #327/#328 and #329 test boundaries remain.

## Remaining #256 / #291 work

This candidate still **does not** persist a durable active-runtime receipt,
provide a Host-owned process supervisor with accepted/unknown delivery
records, perform live Session/Ask handoff, or implement safe process stop
and crash/no-ACK reconciliation. An unbound existing Main remains a separate,
previously authorized execution lane; the new binding has not been made a
mandatory global policy.

The next implementation must start a trusted owner process independently
of the originating ChatGPT/RDC call, positively reconcile exact current
Main/Session evidence, stop or block new effects before Detach, then remove
only Host-owned executable state. Neither a PID nor a preview attachment
alone authorizes starting, stopping or retrying a model turn. Existing
#291 AC-MAIN-09–12 and #312 acceptance remain independent.

This source slice authorizes **no** GitHub Source Commit, Push, PR, Merge,
Release, real consumer install or real Task/Model operation.
