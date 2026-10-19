# KAGARI / KUMIKI 0.1.0 — Known Limitations

**Target version:** 0.1.0. **Current candidate status:** UNRELEASED / NOT PRODUCTION QUALIFIED.
This is disclosure of the proposed release scope, not a claim that 0.1.0 has shipped.

## OpenCode Session Export output size (accepted for v0.1.0)

The KAGARI host writes \`opencode --pure export\` stdout directly into an
exclusive private Evidence file (created with mode 0600) to avoid the previously
observed 192 KiB truncated-PIPE capture. It applies a **35-second subprocess
timeout**. Once the exporter returns, KAGARI **rejects an export larger than
3 MiB** (or empty/invalid JSON); such a session cannot qualify as successful
Evidence.

**Limitation:** The 3 MiB check runs **after** the data has been written.
The temporary export file **is not guaranteed to stay within 3 MiB during
writing**. Very large session output may temporarily occupy more disk space
and, on a shared filesystem with little free space, could pressure unrelated
processes. There is no observed disk-exhaustion incident in the present #275
acceptance evidence. The Task/Evidence integrity gate remains fail-closed.

**v0.1.0 decision (Human, 2026-10-17):** Treat this as an accepted,
LOW-priority resource-consumption limitation, **not an independent v0.1.0
release blocker**. Do not change runtime code or the existing rejection checks
for this item in v0.1.0. Defer any per-export **write-time** limit to a release
after v0.1.0. No date or next version number is promised.

**Tracking:** [Issue #276 — Limit OpenCode Session Export disk usage while
writing](https://github.com/upiscium/Templates/issues/276). Any later change
must constrain only Export output, preserve OpenCode's own SQLite writes,
verify real complete export capture and normal Evidence readback, and avoid
reintroducing the historic PIPE truncation.

## Other restrictions (NOT waived by Issue #276)

- The current Nix package is a TEST_ONLY / UNRELEASED candidate, **not** a
  qualified production installer or public release.
- Native Planning and child session provenance are host-observed; independent
  cryptographic model/provider attestation is not established.
- Local interactive TTY approval is not proof of remote cryptographically
  authenticated Human identity. Project-specific exact grants are still required.
- The bounded disposable Linux/Nix/OpenCode scenario is not proof of generic
  support for arbitrary Projects, arbitrary shell/test sandboxing, or complete
  system-wide resource isolation.
- Independent exact-source correctness/security review, source Publication/CI,
  independently authorized real-model consumer acceptance, and Human
  Publication/Merge/Release decisions remain separate outstanding gates.

The accepted deferral of #276 applies **only** to export-file peak disk usage.
It does not waive any other security, Project-preservation, source-review, or
release qualification requirement. Broad resilience expansion remains frozen.

## RC.1 managed isolated Work Unit profile (unreleased)

The first 0.1.0-rc.1 consumer candidate is intentionally an **external
attachment and independently cloned Work Unit**, not a direct in-place code
editing engine for arbitrary user repositories. The Work Unit runs only from
the committed Project HEAD. Existing staged/dirty user content stays in
the original Project and is not included in the generated Work Unit. Direct
automatic Push/PR/Merge or integration of Work Unit Product commits into
original Project history is unsupported. Linked Git worktrees are outside
this profile (#272). Native model-runner internals are still marked
TEST_ONLY_UNQUALIFIED until real RC-specific Human-granted Product Task
acceptance and review. No general production qualification or release flag
may be inferred from the usability of Install/Repair and RC Task preparation.

## RC OpenCode local dependency initialization (r13)

The r12 RC managed Work Unit model Task safely BLOCKED when OpenCode installed
ignored dependency packages in .opencode during model execution. R13 supplies
an exact pinned Bun/OpenCode 1.18.35 dependency preflight BEFORE the unchanged
Native physical baseline. Custom dependency managers, other OpenCode versions,
user-owned package manifests and changed package registry settings remain
unqualified and fail closed. Per-Work-Unit dependency storage is approximately
55 MiB in the verified MUSUBI layout; external Bun cache may require network.
No blanket .opencode ignore is allowed. A positive r13 real-model Task has NOT
been accepted yet. The Human-approved #276 post-write Export cap limitation
remains an unrelated accepted LOW-priority post-v0.1.0 item.

R14 refinement: any pre-existing symlink, special file or unexpected
filesystem alias anywhere within a Project's tracked .opencode directory
is rejected before Bun initialization. Projects using linked OpenCode
agents/skills must obtain separate review and support; there is no
automatic symlink adoption or change of target in this first RC profile.

R15 corrects a distinct valid-user blocker (#280): a tracked, exact approved
AGENTS.md addendum is intentionally an unstaged Work Unit Git change. The
R14 completely-clean prewarm precondition would otherwise consume the one-shot
Job before model execution. R15 requires the host to revalidate the exact
active Native Human AGENTS Ask, and prewarm can accept ONLY its known 64-hex
postimage SHA with exactly one unstaged tracked AGENTS.md change and no other
dirty/staged paths. The original strict Git/index/config/refs and Native
physical Contract checks are maintained. Unapproved modifications, detached
or changed grants, symlinked AGENTS, and old package approvals still BLOCK.
No cross-generation approval transfer or positive production qualification
is implied until new real-model RC acceptance passes.
