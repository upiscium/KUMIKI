# KAGARI external host attachment preview — #256 candidate

**Opt-in, source-only, unqualified preview. #256 remains open.** This is a
bounded host attachment building block, not full KAGARI v4 runtime integration
or a claim that the v4-native installer is complete. The released v0.1.0 GA
independent-Clone lifecycle and v3 development bootstrap are unchanged.

Requested source baseline: `main` at
`f83f72d250b9b395ad07ed849996e3fb315703c5`, branch
`task/256-v4-host-attach-r1`. These Git identities must be independently
verified by the coordinator; no Git command was run in this implementation
session.

## Explicit external invocation

The existing optional `kagari-control` Nix package gains `kagari-vnext-attach`.
No other Nix output is changed. The coordinator must build/check this source
candidate before any qualification claim. Invoke the externally packaged CLI,
not a Project script, flake, Just recipe, or local bootstrap:

```sh
/nix/store/<immutable-control-output>/bin/kagari-vnext-attach plan \
  --profile linked-worktree-mvi-preview \
  --project-root /absolute/canonical/git-project \
  --state-home /absolute/private/external-state \
  --package-root /nix/store/<immutable-control-output>
```

Use the same explicit arguments with `doctor`, `install`, `repair`, or
`uninstall`. There is no default profile, inferred package, download, implicit
upgrade, or implicit authorization. Supply the **resolved output directory**,
not a `result` symlink or a mutable Nix profile. Do not copy these placeholder
store paths literally.

Requirements:

* Project root: canonical absolute owner-owned primary real Git checkout with
  a real `.git` directory, not a symlink, subdirectory, bare repository, or
  linked-Worktree root. Project and Git-directory device/inode are pinned.
* State home: already existing, canonical absolute owner-owned directory with
  exact mode `0700`. It must be outside and must not enclose the Project.
  The CLI does not create or chmod this authority boundary.
* Package root: explicitly supplied, canonical absolute read-only directory,
  separate from both Project and state home (neither may enclose the other).
  Its `bin` must be a real read-only directory containing all of
  `kagari-vnext-attach`, `kagari-vnext-host-prepare`,
  `kagari-vnext-worktree-install`, `kagari-vnext-task`, and
  `kagari-vnext-session`. Required executables must be read-only regular
  executable files, owned by root or the invoking user. In-package executable
  links are supported; external executable links are accepted only into
  canonical immutable `/nix/store/<hash>-<name>/...` outputs, as produced by
  `symlinkJoin`. The resolved executable parents through the output root are
  checked read-only as well. A symlinked `bin` or package root is refused.

No package executable is run to probe version or readiness. Identity includes
resolved paths, filesystem metadata and SHA-256 of every required executable,
package directories, caller-selected Project/state identity, and the three
same-package host Python source hashes. Read-only checks are an explicit
filesystem contract, **not** kernel-enforced immutability or a signature/store
closure attestation. The host owner must select a trusted immutable package
and protect its ancestors. This preview does not resolve Nix closure trust,
garbage-collection roots, or executable dependency provenance.

## Lifecycle and state

`plan` and `doctor` inspect readiness without publishing an attachment or
appending `.gitignore`. Existing host-preparation/Git-private synchronization
can still create lock files; these are not a promise of zero filesystem
effects. Missing attachment is `ABSENT`, a valid matching attachment is `READY`,
and a valid attachment whose prerequisite is missing is `NEEDS_REPAIR`.
Conflicts, corruption, unsafe ownership/modes, or different identity are
`BLOCKED` (exit 2), never a guessed repair or overwrite.

`install` and `repair` reuse `tools/kagari_vnext_host_prepare.py`'s existing
receipt, lock, source identity, publication and ignore-preflight primitives.
The adapter forwards the **original caller-selected Project inode** into the
unchanged ignore helper. Merely calling `host.operate` after outer validation
would let a newly substituted checkout become that function's new identity;
the adapter avoids that gap without changing the existing host-prepare CLI.
There is exactly one ignore install/repair call per successful operation.
This host prerequisite needs **no per-Task Human Ask**. It does not grant Task
authority. Conflicting ignore rules block before an attachment is published.

The Project owns `.gitignore`, including `/.worktrees/`. Safe preparation may
append that rule; existing bytes and staged state are preserved by the existing
helper. It may retain the displaced preimage in Git-private storage and uses
its existing private locks. Nothing evaluates even malformed `flake.nix`,
`flake.lock`, `Justfile`, `opencode.json`, or `AGENTS.md`. No Git add, commit,
index modification, ref update, source bootstrap, or history operation runs.
An uncommitted ignore rule is local; it does not propagate to new clones.

The single attachment artifact is:

```text
<state-home>/kagari-vnext-attach/attach-<sha256-of-project-path>.json
```

This exact canonical mode-`0600`, single-link owner-owned record is **both
receipt and pinned package descriptor**. There is no separately mutable
symlink, launcher, or pair of files that can disagree. Atomic publication uses
an exclusive private staging inode, fsync, and a no-clobber hard link. Linux
directory-inode `flock` serializes this CLI's attachment operations without
creating another attachment artifact. The namespace must be private `0700`,
on the state home's filesystem, and not a detected mount point. A replaced
checkout at the same path conflicts with its old receipt rather than gaining
another implicit attachment. Matching install/repair repeats preserve the
receipt inode; missing receipts can be explicitly recreated.

After successful install the status is `ATTACHED`, **not runtime installed**.
Every report explicitly includes:

```json
{
  "runtime_installed": false,
  "task_execution_authorized": false,
  "main_started": false,
  "model_or_session_started": false,
  "git_stage_or_commit_performed": false
}
```

`uninstall` validates exact identity and removes **only that verified attachment
receipt**. It does not run ignore repair or the package. The result is `DETACHED`,
then `ABSENT` on repeats. It leaves `.gitignore` (including the appended rule)
and all owner content, Task, Evidence, existing Worktrees, index, refs, source,
and package intact. The empty/shared attachment directory and independent
host-preparation receipts/locks are retained; none confers runtime or Task
authority. There is no recursive removal, wildcard cleanup, chmod, or force
switch. Corrupt, foreign, hardlinked or symlinked receipts are not removed.

## Recovery and limits

Preparation precedes attachment publication. A crash may leave an owner ignore
rule or preparation receipt but no attachment: explicit repair can finish
without duplicate rules. Interrupted `.stage-*` files and unknown namespace
entries block and are retained for inspection. A missing preparation receipt
or ignore rule can be repaired; modified records, changed package/source
identity, and ambiguous data require independent owner investigation. There
is no automatic upgrade, adoption, destructive recovery, or detach of a receipt
whose pinned package is missing/changed. Preserve the original package until
detach. These are cooperative host-owner filesystem operations, not a security
boundary against a malicious concurrent process with the same uid; that process
can change directories or ignore the locks. No pathname-based unlink can offer
a universal compare-and-delete guarantee against such a process.

This candidate never starts Main, Task, OpenCode, models, or sessions; does
not install a full AgentCore-v4 runtime, qualify linked Worktrees as the product
default, authorize task execution, or implement live-runtime detach. #256's
full lifecycle/integration and qualification remain open. #304/#301 are the
unchanged host-preparation and ignore prerequisites, not proof of completion
of #256 or the Main orchestrator.

## Coordinator verification (not executed in the implementation session)

Run from the repository root using the authorized host:

```sh
python3 -B -m unittest -v tests.test_kagari_vnext_attach tests.test_kagari_vnext_host_prepare tests.test_kagari_vnext_worktree_ignore
```

The new tests use only disposable real Git Projects and an external fake
read-only package whose executables must never be invoked. They cover owner
dirty/staged preservation and invalid flake/Just, exact pins and authority
flags, missing/corrupt/foreign state, symlink/hardlink/mode refusal, repeat
operations, safe detach, persistent owner `.gitignore`, and ignore conflicts
without attachment. They do not validate a real Nix closure or production
deployment. Git branch/base verification, Nix packaging checks, and test
execution are explicitly delegated to the coordinator, not claimed here.
