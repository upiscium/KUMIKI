# KAGARI 0.1.0 Publication Source selection (review only)

State: exact source selection frozen locally; public source publication NOT APPROVED.
The selected paths are scoped under Issue #275, rooted in baseline source
5e0a4fd35b0e2e8014ff35f2ce7dc8e600e84812. Selection is not permission
for Commit, Push, PR, Merge, Release, Source Guard switch or production Install.

The source set contains the current TEST_ONLY native Task/auto Planning runner,
exact Project-owned Human Ask/Consent Admin and package-owned composed CLI,
four new Nix packaging expressions, historical smoke-only dependencies,
targeted offline regression suites, and relevant architecture/usage docs.
The historical Native N1 manifest retains target_version=1.0.0 and
SOURCE_ONLY_UNQUALIFIED. Public KAGARI and KUMIKI target version is 0.1.0.

Previously accepted AGENTS coexistence design is in ADR-0004; the independent
2026-10-15 release priority/resilience freeze decision is ADR-0005,
renumbered to avoid collision and amended with the two-real-concurrent-children
acceptance decision. No other A/C/D lane variants are shipped. All current B
candidates outside the selected exact list are excluded.

The authoritative SHA-256, mode, origin, category and exclusion records reside
in the external private frozen Publication manifest, referenced by Issue #275.
The package was rebuilt from the selected source to the prior immutable Nix
output identity; that and offline test PASS do not confer production approval.

Known Limitations for the v0.1.0 target are documented in
[kagari-v010-known-limitations.md](kagari-v010-known-limitations.md).
In particular, Issue #276 tracks the accepted LOW-priority deferral of a
write-time Export disk cap to a later release. The existing 3 MiB post-write
Evidence refusal remains enforced. This narrow accepted limitation does **not**
itself block the v0.1.0 release.

Remaining independent release gates: exact-byte correctness/security review,
model/provider provenance and local TTY identity limitations acknowledged
without claiming stronger attestation, production permission policy, actual
consumer Project acceptance with fresh grants, authorized source Publication,
CI, Merge and Release. Broad resilience expansion remains frozen.

## Current local r5 integration scope (Publication NOT APPROVED)

Issue #277 established that the original GitHub CI discovered the new
KAGARI package tests but had not built their exact Nix output paths. The
current candidate includes dynamic package preparation based on the same
canonical eight-shard test assignment function, so moving tests to different
shards does not reintroduce this missing-package failure. The protected
GitHub Actions workflow is a selected Publication-sensitive file; actual CI
cannot be claimed PASS until source publication is separately approved.

A separate durable external lifecycle preview has been added for
Project-root identification, non-executing install/inspect/repair/uninstall
and fresh scoped Ask generations. **It blocks the native model Task path**
rather than granting unqualified non-disposable consumer execution.
The previous frozen r4 Manifest remains historical evidence; r5 has a
distinct exact scope/hash and requires new independent review and Human
Publication authorization. Neither TEST_ONLY nor durable preview is a
released production installer.

## R8 working source — disposable native Task/Consent bridge

A narrow experimental path connects the durable attachment's owner record
to the existing Native Consent Admin Task route, but ONLY for a separately
created full-history private /tmp/kagari-v010-durable-fixture-*/project Clone
with no Git remotes. Original strict foreground Human Ask and native
--disposable-project requirements remain mandatory. A changed immutable
candidate package and new Project identity require newly reviewed exact Ask
request IDs before any real model effects.

All normal user Projects remain BLOCKED for native model Tasks; this is not
a production-supported runtime or permission. A successor to the r7 source
manifest must be frozen and independently reviewed if this bridge is selected
for Publication. Source publication, Merge and Release remain separate gates.

## R9 exact composing-package approval generation safeguard

The disposable Native Task Bridge now writes a private per-Ask generation
identity record on *fresh* durable Install, binding the exact composed Nix
package/receipt/Bridge source SHA-256 to the installed Native owner and
Project. Older grant generations from an earlier composing Preview cannot
be activated by a newly changed wrapper: missing or mismatched binding safely
BLOCKS before Human Ask decision or model run. Explicit Uninstall/Reinstall
is required to issue fresh Human asks. This is not release permission and does
not weaken or bypass the original Native grant checks.

## R10 narrowed #278 source delta (NOT published)

The initial R9 real model acceptance exposed OpenCode's one-time .git/opencode
workspace identifier as a false BLOCK during tool-free auto Planning in a
fresh Clone. R9 was intentionally retained unmodified and its second
human-approved identical-package run completed the full Product/Evidence/
Uninstall/Reinstall TEST_ONLY acceptance.

The R10 source candidate changes only the auto Planner's working directory
to its private external Evidence root, updates exact invocation provenance,
and adds a fresh Git Clone initialization regression and disjoint-path
negative test. Native task/consent/AGENTS/CI authority logic is unchanged.
Publication Source must be SHA-refrozen and independently reviewed; no
prior approval is carried over to changed native package identity. No
source Push/PR/CI, Source Guard activation or Release is authorized by this
source-only correction. Track [#278](https://github.com/upiscium/Templates/issues/278).

## RC.1 source preview — managed isolated Work Unit consumer profile

A separate **unreleased, unqualified** 0.1.0-rc.1 candidate replaces user-facing
TEST_ONLY CLI flags with a normal Project attach/inspect/install/repair/
uninstall interface and a Task-specific `prepare-task → exact foreground
Human Ask → run-task` pathway. This first operational profile deliberately
runs any model effects in a newly created, independently full-history,
no-remote Git Work Unit, *not* in the source Project. No source change, Git
HEAD/index, AGENTS, model config, flake/Justfile or staged user bytes are
modified by the attach or model worker in the original Project.

This is a **candidate design and source-only implementation**, not an accepted
general consumer release. The older Native Task is still internally
TEST_ONLY_UNQUALIFIED and its actual executable and receipt are SHA-pinned.
RC Task approval is bound to exact attachment generation, TaskSpec, Project
HEAD, source Nix receipts, model executable and allowed Product paths.
Additional Native Human Asks remain required by the original test engine.
RC CLI itself never Pushes, merges or imports a completed Work Unit's local
Product commit into the user's repository.

An independent MUSUBI real-source full-history Clone and a different
TERREATE C++ repository Clone both passed local attach → known-partial Repair →
bounded Work Unit preparation → unapproved execution BLOCKED →
Uninstall → fresh Install → old Job denied → Uninstall with original
source configurations/Git index/staged changes preserved. These are offline
preconsent acceptance proofs, **not positive RC multi-agent model acceptance**.

Before any RC release, the complete new source including its protected GitHub
CI workflow must be SHA-frozen and independently reviewed, new RC-specific
Human Ask positive real-model E2E must pass, GitHub Publication must be
separately approved, and actual CI plus Human Merge/Release decisions are
required. Existing resilience expansion remains frozen; #276 remains an
accepted LOW-priority Known Limitation.

## R13 narrowly scoped RC OpenCode dependency bootstrap (not published)

The r12 real-model RC Job reached valid auto Planning and bounded Product
edits but BLOCKED at the strict Native physical inventory because the model
runtime generated ignored local OpenCode dependencies. The incident differs
from the resolved #278 first-run Git Workspace ID issue. Original user
Projects were untouched and r12 approvals were explicitly retired.

R13 source adds a no-model Nix-selected Bun OpenCode dependency setup within
the independent RC-managed Work Unit, with exact versioned integrity lockfiles,
immutable source/receipt SHA binding, and explicit proof that Git/Project
metadata never changes beyond the initial OpenCode Workspace ID. The original
Native Task model, Git and physical scope checks remain BYTE-IDENTICAL.
Local testing covered every file path observed during the failed r12 model run.
This does not transfer old Human Ask grants or source Publication/Release
authority. Freeze and independently review the revised source before RC Task
acceptance; no source Push/PR/Merge/Release is authorized.
