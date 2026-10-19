# KAGARI 0.1.0 — durable external lifecycle preview (#256 / #275)

**UNRELEASED / DESIGN-PREVIEW. Not a production installer, not qualified for model Tasks.** This is a deliberately narrow next step beyond the earlier disposable-`/tmp` Consent Admin.

## Goal and ownership

A real consumer Project must not need to edit or evaluate its own `flake.nix`, `Justfile`, `opencode.json`, `AGENTS.md` or Git metadata simply to attach KAGARI. A local independent Nix package owns the runtime. Project data remain Project-owned.

`tools/kagari_v010_durable_location.py` provides a **read-only** Project-identity and XDG state-location plan. Supported initial Git topology is an ordinary euid-owned repository with a real `.git` directory (linked worktree is separately tracked in #272). The external namespace is `$XDG_STATE_HOME/kagari-v010/` or `~/.local/state/kagari-v010/`. Its per-Project directory name is based on the exact canonical Git root's device/inode/path identity, not a mutable Git branch HEAD. Package-owned runtime directories are 0700; unknown symlinks, modes, ownership or overlap with the Project are refused.

The proposed Nix candidate also contains **`kagari-v010-durable-preview`**, which reuses the independently receipted native Consent Admin code for external **inspect, install, repair, uninstall and read-only Human Ask display** operations, while binding the preview and Project-location source bytes in the composed package receipt. The preview retains exact per-Project pending consent requests and revocation, but **refuses Human Ask approve/deny/apply operations** until native Task qualification; it cannot add a Project AGENTS exception. `run-task` explicitly returns **BLOCKED**: this preview is not an authorization to run native `--disposable-project` Task mode on a normal Project.

Example, solely for an independently disposable Git Clone and private state root:

```sh
/nix/store/<reviewed-composed-output>/bin/kagari-v010-durable-preview \
  inspect --project-root /tmp/<private-fixture>/project \
  --state-home /tmp/<private-fixture>/state
```

A real user's Project must not be modified using this unreleased candidate. An operator can run the read-only planner without any package attach.

## Negative guarantees and next step

- No model execution, no Product code mutation, no unrelated Git index/config/AGENTS overwrite from ordinary install/repair/uninstall.
- Unknown package/receipt or lost ownership is **BLOCKED**, not silently repaired or reinstalled.
- Preview qualifier remains `DURABLE_PREVIEW_UNRELEASED`; the underlying native Task package remains `TEST_ONLY_UNQUALIFIED`. It does not claim production qualification.
- Approved local TTY Human Ask is not cryptographically authenticated remote identity; default Project instruction coexistence remains authoritative under ADR-0004.
- Actual 0.1.0 release needs a **separately qualified non-disposable model Task path** and real full-sequence consumer acceptance with fresh Project/package grants, full independent review, CI and explicit Human Publication/Merge/Release approvals.
- The Session Export write-time disk cap stays deferred under accepted Known Limitation #276; broad resilience expansion remains frozen.

## #275 native Task bridge — disposable full-history consumer trial only

The unreleased Durable Preview has a separately gated TEST_ONLY path to prove
that external attachment and the existing Native Task/Consent Admin cooperate.
Ordinary consumer Projects remain unsupported for model Tasks.

run-task, approve-agents, approve-permission, deny-agents, deny-permission and
apply-agents remain BLOCKED by default. With the explicit
--disposable-task-test-only switch, they may proceed only on a canonical
ordinary full-history Git Clone under a private euid-owned directory matching
/tmp/kagari-v010-durable-fixture-*/project. That Clone must have no remotes,
no Git object alternates, and no symlink or mount indirection.

The run-task path requires exact absolute --task-spec, --opencode and --output,
an optional --plan, canonical --repository, numeric --task and bounded
--timeout. It calls the existing Native Consent Admin's run_task function,
which first validates the installed package/owner and each required active
Project/runtime-bound Human Ask grant, then invokes the existing
--disposable-project native Task. This candidate does not bypass the
Project-wide task:deny policy or expand child agent roles.

Only a real foreground TTY can record the exact Human Ask response and
request ID. No source change, old approval or fixture flag constitutes
authorization. An active AGENTS patch can only be restored on uninstall when
the exact approved postimage remains unchanged; modified user content is
preserved with an explicit refusal.

After fresh exact Human consent, a real configured-model Task could edit and
commit only the authorized Product paths in the disposable Clone, run the
specified Project tests and persist native-v4 Evidence/Task View. A successful
test-only result is not a general consumer task or Release qualification.
Do not run this experiment against original user Projects.

## Composed-package/Ask generation binding (R9)

The Native Human Ask request is originally bound to Native runtime source
bytes. An amended composing Preview could otherwise gain a new ability to
execute while reusing a grant created under a previously non-executing Preview
that referenced the same Native runtime. The disposable Task bridge therefore
also writes a separate, private, mode 0600 identity record only when a NEW
durable attachment is first installed. This record binds the exact composing
Nix output, its Receipt and Bridge source SHA-256, Project filesystem identity,
the Native attachment owner record and the newly created Human Ask generation.

Before any disposable Task invocation or Human Ask approve/deny/apply operation,
the Bridge requires this exact identity record and checks all of its fields
against the currently installed runtime. If the record is absent (e.g. an
attachment installed by an older Preview), stale, corrupted or mis-owned,
Task/permission activation is BLOCKED. A repeat Install against an existing
older attachment must not silently manufacture the new identity record.
Uninstall followed by an explicit fresh Install produces new pending Human Ask
request IDs. Historical binding records remain private audit information;
they are not grants and are not consulted for new generations.

Even with a matching binding, active native Human Ask grants remain mandatory.
The binding alone never creates a model permission. Normal Project Tasks stay
unsupported; this is still TEST_ONLY and not a production qualification.
