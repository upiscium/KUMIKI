# KAGARI 0.1.0-rc.1 — managed Work Unit RC candidate

**Status:** `RC_CANDIDATE_UNRELEASED`. This is an **unpublished RC build**, not an approved production release. The user-facing operations below do not require `--test-only`, but its immutable Native Task engine remains internally marked `TEST_ONLY_UNQUALIFIED` pending final qualification. An RC installation does not grant model access or authorize GitHub Push, merge or integration into the source Project.

## Supported first RC profile

An existing, euid-owned ordinary Git repository (a real `.git` directory) on Linux/Nix with a configured, explicitly selected OpenCode executable and separately writable external state storage. The Project's `flake.nix`, `flake.lock`, `Justfile`, `AGENTS.md`, `opencode.json`, `.automation/**` and Git index/staged work remain Project-owned. An invalid/missing Project flake/Justfile does not prevent attachment. Linked Git worktrees are not qualified (Issue #272).

**An attached source Project is never directly modified by KAGARI's RC Task.** A Task uses a fresh independent, full-history, no-remote Git Clone in a private disposable Work Unit. That clone contains only committed Git HEAD bytes; existing dirty/staged Product state is preserved in the original and is not silently imported. Dirty Project `AGENTS.md` or `opencode.json` blocks preparation for separate human review. Model changes, host-only local commit and Native v4 Evidence stay in the generated Work Unit. Integrating such changes into the source Project is deliberately a separate, non-automated human decision; there is no Push/PR/Merge operation in this RC CLI.

## Install/diagnose/repair/uninstall

Use an independently built exact Nix Store path with an RC receipt reviewed for the intended source revision. The following is a layout example; substitute your independently verified output and existing Project:

```bash
RC="/nix/store/EXACT_RC_OUTPUT/bin/kagari-v010-rc"
PROJECT="/absolute/path/to/existing-git-repository"
STATE="$HOME/.local/state"      # existing canonical directory, owned by you

"$RC" version
"$RC" inspect --project-root "$PROJECT" --state-home "$STATE"
"$RC" install --project-root "$PROJECT" --state-home "$STATE"
"$RC" doctor --project-root "$PROJECT" --state-home "$STATE"
"$RC" repair --project-root "$PROJECT" --state-home "$STATE"
```

Install is idempotent when the installed ownership record and exact Nix link match. Repair is limited to a known missing RC-owned symlink. Unknown state, package changes, new symlinks, unexpected files or wrong filesystem ownership are refused and preserved for review. Reinstall after a clean Uninstall creates a **new install nonce**. Existing Work Unit/Evidence history remains external and survives detachment; historical Tasks and their approvals **cannot** become current-install authority.

## Prepare and inspect a bounded Task

```bash
"$RC" prepare-task \
  --project-root "$PROJECT" --state-home "$STATE" \
  --task-spec "/absolute/path/to/reviewed-task-spec.json" \
  --opencode "/absolute/path/to/actual-configured-opencode" \
  --repository "owner/repo" --task 275

"$RC" show-task \
  --project-root "$PROJECT" --state-home "$STATE" \
  --job-id "job-EXACT_JOB_ID"
```

TaskSpec v1 binds exact original Git HEAD, goal, allowed Product file paths, declared test argv, acceptance, at least two native children and `planning=auto` or `specified`. Currently the RC CLI's managed execution uses **auto Planning**. Preparing a Work Unit creates no model effects and gives no model permission.

The precise `RC_EXACT_MANAGED_WORKUNIT_TASK` request returned by `show-task` is one locally authenticated **foreground terminal** Ask. You must separately approve the bounded RC Task with `approve-task` and exact `APPROVE <request_id>`. If the committed Clone's legacy `AGENTS.md` or OpenCode `task:deny` requires a scoped exception, the installed Native Admin **also** produces its own separate `AGENTS_SCOPED_EXCEPTION` and `NATIVE_TASK_PERMISSION` request IDs, each independently approved with exact terminal text. Use `show-agents` / `approve-agents` / `apply-agents` and `show-permission` / `approve-permission` **with the same `--job-id` and Project/state arguments**. No blanket Project-wide `task: allow` is written.

Only after **all required exact decisions are active** does the following pass the authorization gate:

```bash
"$RC" run-task \
  --project-root "$PROJECT" --state-home "$STATE" \
  --job-id "job-EXACT_JOB_ID"
```

RC Task approval and Native Ask receipts are bound to the Project identity, exact Work Unit, TaskSpec, executable SHA, Nix package/receipt and install generation. A Job is **one-shot**: it creates an immutable dispatch marker before launching the original native Task runner and cannot be replayed after an uncertain outcome. Do not delete a dispatch marker to repeat a Task; instead inspect the job Evidence, then prepare a new Task and obtain new scoped approval.

The original Native host validates two genuinely concurrent OpenCode leaf sessions, exact Product file scope, declared tests, unrelated staged work, host-only local Product commit, Native v4 Task Record/Contract/Evidence/Task View and independent fresh-process readback. The historical Native engine still describes its internal path as `TEST_ONLY_UNQUALIFIED`; the RC adapter does not falsely change that established source or qualification merely by relabeling receipts.

Uninstall detaches only verified KAGARI owner records and retires pending/approved Native Work Unit grants for the **current installation generation**. Historical Work Units, Product commits and Native v4 Evidence survive. The RC does not automatically delete experimental Clone directories. Human-owned original Projects and unrelated task/data/history are preserved.

## Known limitations and release gates

- RC candidate status is `UNRELEASED` until independent full exact-source correctness/security review, real-model RC-specific approval/Task acceptance, CI and explicit Human Source Publication, Merge and Release gates pass. Its operational path has not yet been published or validated as a public release.
- Initial managed Work Units run from independently generated temporary full-history Clones; they have no Git remotes and do not support automatic Product integration into the original Project.
- Underlying Native Task internal component is `TEST_ONLY_UNQUALIFIED`, although the user-facing RC adapter is named and scoped independently. Production Task qualification requires a fresh positive RC real-model test and separately approved source.
- Local foreground TTY Human Ask is **not** cryptographic remote user-identity attestation. Model provider identity is also host-observed, not independently cryptographically attested.
- General hostile same-UID modification, OS-level sandbox/fully hermetic test execution, linked Git worktrees and arbitrary Project configs are not qualified.
- The OpenCode Session Export 3 MiB cap applies *after writing*, not during. This is the Human-accepted, LOW-priority post-v0.1.0 Known Limitation [#276](https://github.com/upiscium/Templates/issues/276); do not reopen resilience expansion to address it.
- Source publication to `upiscium/Templates`, Package Release and activation are separate Human approvals. No RC build receipt authorizes any of them.

Related design and acceptance tracking: [#271](https://github.com/upiscium/Templates/issues/271), [#275](https://github.com/upiscium/Templates/issues/275), [#256](https://github.com/upiscium/Templates/issues/256), [#277](https://github.com/upiscium/Templates/issues/277), [#278](https://github.com/upiscium/Templates/issues/278).

## #275 RC-1 failed real-model run and R13 pinned OpenCode dependency bootstrap

The exact r12 RC-managed MUSUBI Work Unit accepted its three foreground Human
Ask decisions and completed model-generated Planning and physical Product
edits. It did NOT qualify as a successful Task: OpenCode 1.18.35 automatically
installed roughly 3,925 gitignored files under .opencode/node_modules plus
package.json, package-lock.json and .gitignore. The unchanged Native physical
inventory correctly rejected these out-of-Contract changes before 156 tests or
Product commit. The one-shot job was not replayed. Uninstall revoked the two
Native grants and restored AGENTS byte-for-byte. Failed Evidence/partial
Product edits remain preserved, and original MUSUBI remains unchanged.

R13's narrow RC-only correction does not ignore ANY .opencode directory.
After exact RC Task and Native Human Ask approvals, and before the original
Native runner captures its physical Project baseline, the host performs a
NO-MODEL dependency setup only inside its independent private Git Work Unit.
It uses Nix-immutable Bun 1.3.13 and two source-pinned npm registry integrity
lockfiles for OpenCode 1.18.35. Package lifecycle scripts are disabled.
Build cache and diagnostics are stored in private external RC Job state.

The host verifies that only pinned .opencode dependency manifests, lockfiles,
.gitignore and node_modules/** were created. The only permitted Git control
initialization is the expected euid-owned 40-hex .git/opencode Workspace ID.
Other Project data, tracked OpenCode agents, Git HEAD/refs/config/index,
approved AGENTS, Project model configuration and unrelated staged state must
remain unchanged. Once setup finishes, these dependency bytes are INCLUDED
in the original Native physical baseline: a subsequent model edit or package
mutation is still a Contract violation and BLOCKS.

The selected initial RC profile explicitly requires OpenCode 1.18.35,
Bun 1.3.13 and the exact pinned plugin dependency locks. Custom plugin
registries or preexisting user-owned ignored dependency data require manual
review and are refused. The setup can contact registry.npmjs.org, without
invoking an LLM, and may use approximately 55 MiB of per-Work-Unit files.
It does not claim a fully offline cold-cache profile.

A fresh independently cloned MUSUBI test confirmed that the Nix-bound Bun
prepares ALL 3,955 previously observed OpenCode model-generated .opencode
file paths, plus its own bun.lock, without changing original Product or Git.
This is not positive RC model-backed Task qualification; a new exact immutable
RC package / TaskSpec / Human Ask generation, complete source review,
Publication/CI and Release gates remain separately OPEN.

### RC managed Work Unit pre-existing symlink refusal (R14)

Source-excerpt security review identified that a tracked symlink nested in
a Project's pre-existing .opencode user configuration could redirect an
external package installer before post-write validation. R14 checks every
pre-existing .opencode entry BEFORE Bun runs; symlinks, special files,
foreign-owned aliases and mounted subdirectories are rejected, including
inside tracked agents/skills/commands. Normal ordinary tracked user files
remain preserved and are never edited by the bootstrap. Three additional
tests cover nested symlinks, redirected node_modules and ordinary tracked
configuration. This is a deliberate narrow support-profile restriction,
not a declaration that arbitrary Project configurations are qualified.

## R15 scoped AGENTS postimage bootstrap correction (#280)

The previous R14 pinned bootstrap required a completely clean Work Unit both
before and after Bun. A legitimate Human-approved, tracked AGENTS.md scoped
exception intentionally makes the Work Unit appear Git-dirty, so that
restriction falsely BLOCKED all such Tasks *after the one-shot dispatch marker
was written*. R15 makes this dependency-setup guard consistent with Native
Human Ask without weakening the Project Contract.

Immediately before dispatch, the RC host revalidates the exact active Native
AGENTS_SCOPED_EXCEPTION grant, identifies the current immutable request ID
and approved AGENTS postimage SHA-256 and passes **only that digest** to the
pinned, model-free prewarm. A Work Unit without this active grant still must
be entirely clean. A granted Work Unit can have **only** an unstaged, tracked
AGENTS.md modification with precisely the approved content digest; staged
AGENTS, unrelated changes, malformed/stale SHA, missing/symlinked instructions
or unexpected Git state all BLOCK before Bun. The same exact status, Git
HEAD/index/refs/config and AGENTS digest are rechecked after dependency setup.

No standing Project-wide permission is created. The original Native physical
source-allowlist, child scope, model selection, Product commit and Evidence
checks remain unchanged. The R14 immutable package and old Human Ask
generation are NOT updated or reused; this requires a newly reviewed package
and new per-Job/NATIVE Task grants. This correction is not real-model or RC
Release qualification by itself.
