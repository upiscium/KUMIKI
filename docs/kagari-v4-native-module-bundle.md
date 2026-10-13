# KAGARI v4-native N1: detached module bundle

Status: **UNRELEASED / SOURCE_ONLY_UNQUALIFIED**.
Tracked by [#269](https://github.com/upiscium/Templates/issues/269).
Architecture: ADR-0002, #142 Admin, #146 trusted launcher,
#159 version identity, #189 v4.

## Ownership boundary for default adoption

**ProjectRoot** is an existing independently owned Git repository. Its
`flake.nix`, `flake.lock`, `Justfile`, `AGENTS.md`, `opencode.json`,
`.opencode/**`, source code, Git and Task/Evidence are Project-owned.
Default KAGARI attach/detach must not overwrite those files.

**RuntimeRoot** is external to ProjectRoot. A future trusted host installer
will select an independently qualified immutable v4 runtime. That runtime
receives ProjectRoot explicitly when operating on product work. A consumer
`PATH`, Justfile, Nix flake, local config or Task-provided environment
variable cannot select the execution authority.

The current development-only `tools/agent_core_admin.py` has a distinct,
root-managed inventory including Project `AGENTS.md`, `Justfile`,
`opencode.json` and `.automation/.opencode`. This **cannot** implement
ADR-0002 default non-invasive adoption by directly invoking `apply` on
a real consumer. Legacy cutover and explicit opt-in root integration remain
separate Admin decisions.

## Bounded source-only module package

`tools/kagari_v4_bundle.py` assembles 12 existing native Python modules
from the exact Git object database of a specified full commit. Mutable
checkout files, Project config, Just and Nix are not consulted for module
bytes. An external, previously absent destination is required.

Output contains only `manifest.json` and 12 regular 0444 files under
`lib/kagari_v4/`. It includes the native metadata ref, Task Record,
Evidence, Task View, operation prerequisites, collaboration/integration
and cleanup capabilities. It includes no v3 lifecycle, root Justfile,
root AGENTS.md or OpenCode consumer settings.

The closed canonical manifest contains schema_version=1, kind,
qualification=SOURCE_ONLY_UNQUALIFIED, target_version=1.0.0,
source_revision (full commit), source_repository_hint (an **unverified**
local Git origin observation or fixed Nix source label), and sorted
module file entries with relative path, SHA-256, size and mode. The proposed 1.0.0 is a
release target under #159, not an approved stable release.

The generator makes no release-approval claim and creates no signing
identity. Caller-provided digests are consistency checks only; they
do not independently qualify source review or authorization.

The build CLI requires an absolute source checkout with exactly one
canonical GitHub origin, an exact full commit, and a previously absent
output outside **any Git Project**. The check CLI
requires the bundle, expected manifest digest, exact revision, and
optionally the original Git repository for object-level byte equality.

The inspector verifies canonical JSON, closed file and directory
inventory, sizes, file modes, SHA-256 hashes and absence of symlinks or
special files. It does not import or execute bundled Python code.
Even after byte-level success its result remains approved=false,
can_install=false and can_execute=false. CLI exit 0 means
consistency validation, never permission to deploy or run KAGARI.

An interrupted build can leave an incomplete output for diagnosis.
The builder never overwrites an existing destination or automatically
deletes potentially valuable files from it.

## Qualification gates not yet satisfied

This module bundle is not a full standalone v4 runtime. The native
capabilities exist as source Python files but an independently installed
launcher, v4 OpenCode roles/config, dependency/toolchain contract and
verified Task bootstrap are still missing.

N1's eventual release qualification requires independent review of an
exact source revision and manifest, immutable Nix packaging/retention,
the separate trusted #146 launcher, and an Admin ownership contract.
A real default install may not use the existing root-managed Admin
operation without reconciling its scope with ADR-0002.

N2 must verify external OpenCode configuration precedence, writable
shadow state, Project cwd and provider-backed execution. N3 must prove
the native Task Record/Contract, metadata ref, Evidence, Task View,
PR checkpoints and disposable Work Units in a consumer Project.
N4/N5 require data preservation, separate correctness/security
review, CI and an explicitly approved release/activation gate.

No v3 runtime-compatibility work, real consumer deployment, Source
Guard host upgrade or automatic legacy cutover is part of this N1
candidate.

## Nix realization experiment — 2026-10-13

The separate experimental expression
`nix/kagari-v4-module-package.nix` makes the module-bundle content
independent of mutable working-tree source bytes. It requires an exact
full `sourceRevision` and uses Nix `builtins.fetchGit` with the
**fixed** `https://github.com/upiscium/Templates.git` origin. The
source checkout of the caller is not an authority selector; Nix copies exactly 12
native v4 Python modules into a Nix store output.

The Nix source identity is no longer a caller-supplied URL. Both
manifests record `source_repository_hint`, which is **not an
independently authenticated provenance assertion**. The Python source
builder rejects missing/unsupported GitHub origin formats, but a local
Git remote setting is user-controlled. The full revision remains a **development argument**;
a production installer must independently approve and select its
exact identity rather than trusting a Task-supplied revision.

Build-time checks reject missing/extra source files, symlinked
source directories, non-regular/executable source modules and
oversized inputs **before copying any module**, plus unexpected
copied bytes/modes. An internal
manifest describes 12 Nix-store files, SHA-256 and immutable mode
`0444`, plus the pinned source revision. It records
`qualification=SOURCE_ONLY_UNQUALIFIED`; the source-builder and
inspector API additionally return `approved=false`,
`can_install=false`, and `can_execute=false`. No launcher or
normal Task entrypoint is produced.

Manifest schema and file-mode contracts are now **unified** with the
standalone Python builder: both emit exactly the same canonical JSON
Manifest and use regular `0444` files. The Nix output additionally has
Nix-store immutability. This proves one shared **source-only** schema,
not two separate release authorities or an approved runtime.

Actual Nix 2.35.2 on arca-sandbox and Nix 2.34.8 on Adam both realized
the identical package from canonical Git commit
`311ee75f47b083746cd9342ddd50b571b3fae751`:

```text
/nix/store/497q723pfw2y8yi2ykflaamk6wbcn110-kagari-v4-native-module-candidate
```

For both hosts, the output NAR SHA-256 was:
`sha256-AjOpHC5rvsLa7L4LTpfQKBveYFvUM2mFLnsnY259VHI=`.
The manifest SHA-256 was:
`808b3f6dd39c6c7292695e75f4a68efc0fc8ebc285f77cf785e6c014e66c3add`.

Both inspections confirmed all 12 bundled files against the Git commit
and root-owned regular read-only Nix files. Import-only Python smoke
passed 12/12 modules. Changing a tracked working-tree module without
committing it did not change the pinned Nix output path. An invalid
full revision failed Nix evaluation instead of silently using HEAD.

**Boundaries:** independent reproducible realization is stronger than
a mutable filesystem copy, but it does not certify source review,
build trust, a source-authorized v4 runtime or release approval. This
candidate is not yet exposed through the repository's default flake
package outputs, has no host GC root, and is not an Admin launcher.
The existing host Source Guard pin remains unchanged. Nix GC may
collect this unreferenced candidate at any time.

Future steps: independently review the unified N1 distribution contract,
add a qualified Nix flake package output
from an exact independently selected full revision, and prove
host-managed retention and explicit rollback through #146/#142.
Do not turn this experiment into a live consumer deployment by
running Nix from a mutable Task checkout.

### Reproducing the isolated Nix build

On an authorized experimental machine with Nix and a full local Git clone,
evaluate the new standalone package expression directly (it is deliberately
**not** activated or exported as the default flake package):

```sh
nix build --impure --no-link --print-out-paths --expr '
  let
    f = builtins.getFlake "path:/absolute/KUMIKI-checkout";
    pkgs = f.inputs.nixpkgs.legacyPackages.x86_64-linux;
  in import /absolute/KUMIKI-checkout/nix/kagari-v4-module-package.nix {
    inherit pkgs;
    sourceRevision = "311ee75f47b083746cd9342ddd50b571b3fae751";
  }
'
```

The expression obtains its source from the **fixed GitHub remote** at
the exact commit, not the mutable local clone. The Nix expression
itself is still a mutable experimental input until reviewed, published
and realized from an independently selected full source revision.
The local source checkout provides only the experimental Nix expression
and its locked nixpkgs input, not KAGARI source-code authority.

After building, compare the store path and `nix hash path` on separate
hosts, validate the manifest and module SHA-256 against the exact commit,
and keep the experimental output **unselected**. No persistent Nix GC
root is installed by these commands.

## Independent static review findings and source-only corrections

The registered OpenCode Correctness reviewer and Security reviewer inspected
the earlier four-file local candidate independently. Both reported **REQUEST
CHANGES**; their findings are not evidence of an approved/released runtime.

A corrected local candidate addresses the identified issues:

- Nix no longer accepts a caller-supplied source Git URL. It pins
  `https://github.com/upiscium/Templates.git` with an exact full revision.
  Both formats record `source_repository_hint=upiscium/Templates` in
  the real experiment. Python checks the local configured origin against
  this hint, **not its upstream authenticity**. Only pinned Nix fetchGit
  establishes a fixed retrieval endpoint; neither is release approval.
- Nix checks `components` and its v4 module parent for symlinks, rejects
  non-regular or executable sources, and performs 4 MiB per-file / 16 MiB
  aggregate checks **before copying** modules into its output.
- The Python builder disables Git object replacement, streams subprocess
  stdout under fixed byte/time bounds, checks Git blob sizes before fetching
  their content, and refuses output beneath another existing Git worktree
  (including a suspicious `.git` marker), not merely its source checkout.

New targeted tests cover Git replacement refs, oversized Git listings and
blobs, source origin changes, and unrelated Project destination contamination.
These are bounded cooperative-error protections, not defense against a
malicious administrator with arbitrary filesystem access.

**Review disposition is still pending independent re-review of these
corrective bytes**. Static code review alone does not establish an immutable
approved source authority, secure runtime code execution, real Task lifecycle,
host-level GC pinning, or an AgentCore 1.0.0 release. The manifest remains
`SOURCE_ONLY_UNQUALIFIED`.

### Independent R2 review corrections

Second independent static reviews identified three remaining **P3** concerns
in the previous four-file snapshot. The source-only candidate has now been
corrected and requires an R3 exact-byte re-review before a PASS may be claimed:

- Python reports the origin as `source_repository_hint`, not as an authenticated
  repository source. A locally configured Git origin can be spoofed; this
  field is informational only. Nix fetches from the fixed actual GitHub URL.
  Neither is user approval or independent upstream release attestation.
- Python accepts only the **exact** configured `upiscium/Templates` spelling
  to preserve canonical manifest equality with Nix. Alternative mixed-case
  spellings are rejected, not silently normalized.
- Nix validates the **complete** native v4 source directory inventory before
  creating output/copied files, as well as checking all file size bounds
  before copying. Redundant post-copy checks remain in place.

Negative tests now cover the spoofed unverified-origin label, case mismatch,
Git replacement objects, oversized source, output location, and unknown file
inventory behavior. R2 Correctness and Security reviews did **not** yield a
final PASS; there is no release, Task runtime or deployment authorization.
