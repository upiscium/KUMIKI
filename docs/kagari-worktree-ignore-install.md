# KAGARI vNext — Worktree ignore installation (#301)

> Opt-in host-owned preparation for the Issue-linked Worktree MVI (#286).
> The released v0.1.0 GA independent Clone remains unchanged and does not
> write to the attached Project. This does not install the full KAGARI runtime,
> grant Issue execution, or activate production.

## Install/repair contract

The trusted KAGARI vNext install/repair host must run the source-side
tools/kagari_vnext_worktree_ignore.py before allocating the first Issue
Worktree and honor a BLOCKED result. From a separate KUMIKI source checkout:

    nix develop --command python3 -B tools/kagari_vnext_worktree_ignore.py plan --project-root /absolute/owner-project
    nix develop --command python3 -B tools/kagari_vnext_worktree_ignore.py install --project-root /absolute/owner-project
    nix develop --command python3 -B tools/kagari_vnext_worktree_ignore.py repair --project-root /absolute/owner-project

The opt-in KUMIKI Nix package .#kagari-control also exposes
bin/kagari-vnext-worktree-install with the same plan/install/repair/doctor
arguments. For the first host-owned opt-in integration slice (#304), the separate
`kagari-vnext-host-prepare` command invokes that preflight during an
explicit `linked-worktree-mvi-preview` Install/Repair preparation. See
[host preparation](kagari-vnext-host-prepare.md). It records readiness
outside the Project but does **not** install a v4 runtime or start Tasks.
The future complete native-v4 attach host must incorporate this contract.

The Nix environment belongs to KUMIKI; it is never evaluated from the
target Project. Plan/doctor inspect without changing Project content.
Install/repair create a missing root .gitignore or append the exact anchored
line /.worktrees/ once. Existing lines, comments, modes and line endings
are preserved. A repeated installation is byte-identical and needs no
ordinary per-install Human Ask.

The command reports changed_paths, git_ignore_effective, and
needs_commit_for_new_clones. A local ignored namespace suffices for the
allocator's existing Git check. However, new clones will not inherit an
uncommitted rule. If needs_commit_for_new_clones is true, a separate,
owner-approved ordinary Project Git commit can persist it. This host step
never stages, commits, pushes or changes a Task approval.

The root .gitignore remains Project-owned. Uninstall does not remove or
revert it.

## Boundaries and refusal

Only an euid-owned canonical ordinary primary Git Project with a real
.git directory is supported. A symlinked Project alias or linked .git
gitfile is not silently adopted by the installer.

Only root .gitignore can be modified. Symlinks, hardlinks, special files,
unsafe owner/mode, concurrent preimage changes, effective negation conflicts,
already tracked .worktrees files, and unknown foreign namespace entries
cause a specific BLOCKED result. Registered sibling Worktrees are preserved.

Changed file bytes are prepared in a private Git-admin stage and published
atomically. The original inode and bytes are revalidated after exchange.
On successful replacement, the displaced original is **retained** as a
Git-private backup and its absolute path is returned in preserved_preimage:
an ordinary writer holding the old file descriptor could still write after
publication. Never automatically delete that backup. An ambiguous rollback
also retains user bytes for inspection; a failed postcheck for a newly
created file deliberately leaves the Project file in place rather than
unlinking a possibly replaced path. These checks cover accidental races,
not a hostile same-user actor with arbitrary filesystem rights. Original Project HEAD, index, refs, remotes, staged/dirty
work and non-KAGARI files are preserved.

The allocator remains read-only with respect to .gitignore and retains its
MANAGED_WORKTREE_NOT_IGNORED fail-closed gate. Task-specific subagents do not
modify the owner's ignore policy.

This helper is an MVI install/repair **preflight component**, not a complete
implementation of #256's detachable v4 installer. The higher-level trusted
KAGARI Main/host must invoke it as part of the MVI attach lifecycle before
claiming end-to-end automatic installation. The standalone GA v0.1.0 remains
non-invasive and unmodified.

## Local test evidence

    nix develop --command python3 -B -m unittest -v tests.test_kagari_vnext_worktree_ignore

Disposable Git tests cover missing/existing root files, idempotence, LF/CRLF,
no terminal newline, dirty/staged data preservation, refusal cases, concurrent
replacement/rollback, and two genuine Issue-linked Worktrees. Tests do not
modify any production or existing user repository.
