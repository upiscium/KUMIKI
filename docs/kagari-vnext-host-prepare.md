# KAGARI MVI host preparation preview (#304)

**Status:** opt-in source-only, unqualified host-prepare slice for #304 / #256.
This is **not** a complete KAGARI v4 installer, not a Main Orchestrator,
not a Task or OpenCode Session launcher, and not a GA deployment. The
released KAGARI v0.1.0 independent-Clone profile is completely separate.

The goal is to make safe preparation of the project-owned .gitignore
**automatic inside an explicit host Install/Repair preparation operation**,
rather than asking the user or every Issue/Task to run a standalone command.

## External entrypoint

The optional KUMIKI Nix package .#kagari-control contains the host-preparation
binary, kagari-vnext-host-prepare. The caller selects the experimental linked
Worktree profile explicitly and supplies a private external state home:

    kagari-vnext-host-prepare install \
      --profile linked-worktree-mvi-preview \
      --project-root /absolute/ordinary-git-project \
      --state-home /absolute/private/host-state

    kagari-vnext-host-prepare doctor \
      --profile linked-worktree-mvi-preview \
      --project-root /absolute/ordinary-git-project \
      --state-home /absolute/private/host-state

    kagari-vnext-host-prepare repair \
      --profile linked-worktree-mvi-preview \
      --project-root /absolute/ordinary-git-project \
      --state-home /absolute/private/host-state

The host state home must already exist, be canonical, user-owned and mode
0700. It must be separate from and not enclose the Project. Do not use
a Project flake, Justfile, opencode.json, AGENTS.md, or local KAGARI
scripts as the host bootstrap. It imports the already packaged vNext
ignore helper directly from the same immutable Nix output and binds its
SHA-256 to a deterministic private receipt.

Plan and doctor report readiness and missing configuration. Install and
repair call the #301 ignore reconciler exactly once before they create
or accept a host-private receipt. A safe append needs **no per-Task
Human Ask**. A conflict refuses this preparation with the exact #301
code. Task Record, Git refs/index/HEAD, GitHub issues and OpenCode sessions
are untouched. The Project owns .gitignore, including the inserted
/.worktrees/, and retains it after any future KAGARI uninstall.

The JSON output distinguishes the preflight result and host state:
status PREPARED (fresh host receipt), READY (existing matching host
receipt), ABSENT or NEEDS_REPAIR for doctor, and fail-closed BLOCKED.
The nested ignore fields expose changed_paths, git_ignore_effective,
needs_commit_for_new_clones and preserved_preimage. The host also reports
runtime_installed=false, task_execution_authorized=false and
model_or_session_started=false.

An uncommitted .gitignore rule makes the local namespace safe, but
does not propagate to clones. Neither entrypoint runs Git stage, commit,
push or source publication.

## Host receipt, safety and recovery

The only additional state is under a private host path:
<state-home>/kagari-vnext-host-prep/attach-<project-identity-hash>.json.
The receipt is exact, mode 0600, and binds the root canonical path,
filesystem device/inode, explicit profile and the helper source SHA-256.
Private lock and atomic no-clobber receipt publication refuse unexpected
state. Known missing receipt is repaired idempotently. Modified/corrupt
receipts, different source versions or interrupted stage residue **block**
rather than overwrite possibly unique state or infer an upgrade grant.

The host passes the original Project device/inode into the #301
helper, which checks the **opened directory descriptor** before modifying
any owner file. If the original checkout path was replaced with another
repository before the helper opened it, preparation fails and does not
write into that replacement. The host also rechecks identity before
publishing its private receipt.

The host prepares .gitignore before writing a receipt. Therefore a
process crash after safe owner-file preparation but before receipt creation
can leave the owner rule in place; retry preserves the rule and creates a
receipt without duplicate lines. A retry does not manufacture Task authority.
The unrelated Task allocator still independently checks whether .worktrees/
is effectively ignored.

## Qualification limits / next integration

This source slice does **not** install an AgentCore-v4 runtime payload,
connect to OpenCode, register Task Contracts, guarantee one active Main,
qualify Issue-linked Worktree default mode, perform secure live detach,
or replace the v3 development bootstrap. It intentionally does not
implement uninstall. It is a host-preparation building block that the
future v4-native installer must invoke before enabling #286's Issue-linked
Worktree assignment. Actual v4 payload install/repair, session/Task
ownership and uninstall safety remain under #256 / #291.

Acceptance here is source-level and disposable Git fixture validation, not
production deployment. Related: #301 (verified ignore primitive), #304
(host hook), #256 (detachable v4 native installer), #286 (Issue-bound
Worktrees), #291 (Main orchestrator), #163 (exact-root sessions).

## Local acceptance

    nix develop --command python3 -B -m unittest -v \
      tests.test_kagari_vnext_host_prepare \
      tests.test_kagari_vnext_worktree_ignore

The fixtures exercise private host state, missing/valid/invalid ignore rules,
repeat operations, malformed receipts and external source identity, owner
Project data preservation with broken flake and Justfile, and no Git
index/HEAD changes. They never modify user repositories.
