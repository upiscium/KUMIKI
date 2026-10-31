# #256 — Runtime owner / attachment boundary (source-only R1)

**Status:** test-only isolated candidate. This document is **not** a deployed
v4 Runtime, Main/Session activation, publication authorization or #256 closure.

## Baseline and exact ownership

- Source: `upiscium/KUMIKI` `main@526b28169d33c28aaa525939632bf5f5fe41b0de` (merged #291 PR #325).
- Existing merged `kagari-vnext-attach` and `kagari-vnext-host-prepare`
  perform explicit opt-in preview attachment and the #304 / #301
  `/.worktrees/` prerequisite. The receipt means **host attachment only**.
- Existing `kagari_vnext_main_control.Controller` keeps a real OS
  repository Main lease, with an ACTIVE/CLEAN/RECONCILED record in an
  external owner-only state directory. Existing `MainSupervisor` shares
  the stable host repository lock. Do **not** implement a second lock.
- External immutable Nix `kagari-control` currently exposes Main, Attach,
  Session, Host Prepare and Task entrypoints. These *separate* binaries
  are not a complete host-owned Runtime lifecycle or a durable process
  handoff contract.

## Disposable no-Task integrated test contract

`tests/test_kagari_vnext_attach_owner_boundary.py` exercises a real
Git repository with staged and dirty owner files, a deliberately
non-executable fake package and a disposable passwd-home/shared flock.
No GitHub query, Product commit, Task assignment, OpenCode, Model, Session,
Task evidence grant or consumer modification is allowed.

1. While a real subprocess Controller holds Main, Attach Doctor stays a
   truthful Preview (`runtime_installed=false`,
   `task_execution_authorized=false`). Another Controller and a
   MainSupervisor cannot acquire the same repository lease.
2. After the initiating process detaches its I/O stream, an independently
   invoked doctor still sees a live Main owner; SIGTERM produces CLEAN.
   This is **OS-process isolation only** and is not AC-MAIN-10 real Task
   continuation or a ChatGPT browser disconnection E2E.
3. SIGKILL leaves an ACTIVE record. A new owner refuses to start;
   exact-sha256 reconciliation is required and never grants a Task.
4. After clean ownership release, Attach Uninstall / Reinstall / Repair
   preserve Project HEAD, index, dirty data, both synthetic owner
   Task/Evidence sentinels, the `/.worktrees/` rule, and independent Main
   history. The existing Preview Uninstall is **not** a live Runtime stop.
5. Preview Uninstall while an independent Main Control process is still
   live only removes the Preview receipt. The Main remains live and locked:
   this **characterizes an existing missing lifecycle gate**, not safe
   active-Runtime detach or authority to terminate the Main.
6. A separately launched MainSupervisor can hold the shared lease and
   prevent Controller from starting; clean release permits takeover.

## Unqualified Runtime activation (explicit STOP)

This source-only slice introduces **no runtime executable** and does not
modify `kagari_vnext_attach.py`, Main, Session or Nix packaging.

**Do not use the existing Attach Preview receipt as a Task capability.**
In particular, `attach.uninstall` only removes its own pinned receipt and
currently does **not** stop or reconcile a live Main. This is correct for
the existing non-executing Preview but insufficient as a live Runtime
detach guarantee. A future implementation must not silently promote
this operation into a write-capable daemon.

The separately gated next implementation must:

- Bind a durable **active-runtime** receipt to the exact installed Nix
  package, Project root and `.git` identity, explicit profile, current
  #191 Issue Contract selection, real Main lease and #163 Session evidence.
  Any missing/stale/changed binding remains BLOCKED, without Tasks.
- Have a **trusted Host**, not a Task/leaf/Issue allocator, own the running
  Main process and its start/stop/reconcile; avoid an extra Task authority
  store or a duplicate Main singleton.
- Establish Install/Repair preflight **before** Issue-linked Worktree
  eligibility and preserve .gitignore Project ownership. Do not add a
  repeated per-Task Ask for the safe `/.worktrees/` rule.
- On detach, first quiesce/verify actual Main/Session ownership and
  pending effects, then drop only host-owned executable/receipt state.
  No PID-file deletion, implicit model kill, force reconciliation,
  unexpected Project edits or Task Record/Evidence deletion.
- Recover through actual exact-record and Session evidence after a lost
  acknowledgment or crash. Never equate the initiating ChatGPT/RDC call
  ID, a PID, an Issue number or a preview attachment with dispatch
  authority. A failed or uncertain acknowledgment cannot be retried
  as a new model prompt.
- Cover disposable active-owner Install → Doctor → clean Detach →
  Reinstall → Repair, live collision both directions, missing/foreign
  receipt, crash, detached caller, concurrent linked Issue eligibility,
  and negative Session/Ask cases. The real #291 AC-MAIN-09–12 and #312
  cross-controller/E2E gates remain independent.

## Local qualification (2026-10-30 JST)

- Latest pinned baseline: `main@526b28169d33c28aaa525939632bf5f5fe41b0de`.
  Opt-in current-main `.#kagari-control` build PASS; actual store output
  `/nix/store/cgqbpcyw7pmz5jd277m10xkq2y54n5my-kagari-vnext-control`.
- New six real-process fixture tests: **6/6 PASS** in isolation.
- New fixture + existing Main Control + MainSupervisor:
  **77/77 PASS, no skips**, explicit current-main package supplied.
  Actual terminal log SHA-256:
  `e923fc8bb6bfd920e23bd934e593da16a85f18d2c671173348add4ef7378499d`.
- An earlier concurrent 77-test run reported one **test-only teardown
  timeout** in this new suite's final clean-stop: inherited 5-second wait
  expired while unrelated package/Nix/test jobs were running. Focused six
  tests re-passed; the new fixture now requires actual CLEAN_RELEASE within
  a bounded 12 seconds and the complete 77-test rerun passed. Do not
  treat the original timeout as an established Main implementation defect.
- Independently, #304's test-only two-Issue host/Worktree integration on
  the same baseline: **121/121 PASS**, packaged host lifecycle smoke PASS,
  all-systems Nix Flake Check PASS. None tests actual Task execution.
- Protected `publication-check 256` must pass again on the frozen
  **two-file source-only scope** immediately before any proposed publication.
  Independent source review has **not** yet been performed on the final patch.

Publication into an Issue/PR, protected Commit/Push, Merge, release,
consumer activation and model dispatch remain **separate Human gates**.
