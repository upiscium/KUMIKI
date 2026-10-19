# #271 M2 existing-Project packaged smoke candidate

**TEST_ONLY_UNQUALIFIED — PRE-RELEASE. Not released, not a production
activation, not a consumer installer or qualified runtime.** This standalone
Nix output does not change the B CLI, root flake outputs, N1 source bundle,
frozen experiments, or Project-owned settings. Its adapter accepts only the
specific pre-existing disposable host-temp Git fixture contract it validates;
it does not install into arbitrary Projects.

`nix/kagari-v010-existing-smoke-package.nix` depends on the existing M1 model
smoke derivation. The new wrapper runs the exact C existing-Project adapter
SHA-256 `9b5ccb2af9e15cbed20b790b3f3783432149b8656de81ee03f8bbff878b265d1`
under Nix Python `-I -B`, with an explicit packaged `tools/` path for `runpy`.
Its other two SHA-verified tools (model task and namespace helper) and all 12
native v4 modules live in the **same** `libexec/kagari-v010-existing-smoke`
tree; execution does not import from this mutable checkout. The read-only
`share/kagari-v010-existing-smoke/receipt.json` records source revision,
manifest SHA, adapter SHA and unqualified status. The manifest is copied
unchanged from N1, including historic internal `target_version=1.0.0` and
`SOURCE_ONLY_UNQUALIFIED`; neither relabels the public 0.1.0 candidate.
Caller-provided revision/digest checks are not independent source approval.

## Offline build and CLI preflight (no provider/model)

```sh
python3 -m unittest discover -s tests -p test_kagari_v010_existing_smoke_package.py -v
nix build --offline --impure --no-link --print-out-paths --expr '
  let
    f = builtins.getFlake "path:/absolute/KUMIKI-checkout";
    pkgs = f.inputs.nixpkgs.legacyPackages.x86_64-linux;
  in import /absolute/KUMIKI-checkout/nix/kagari-v010-existing-smoke-package.nix {
    inherit pkgs;
    sourceRevision = "311ee75f47b083746cd9342ddd50b571b3fae751";
    expectedV4ManifestSha256 = "808b3f6dd39c6c7292695e75f4a68efc0fc8ebc285f77cf785e6c014e66c3add";
  }
'
KAGARI_V010_EXISTING_SMOKE_PACKAGE=/nix/store/<returned-path> python3 -m unittest discover -s tests -p test_kagari_v010_existing_smoke_package.py -v
/nix/store/<returned-path>/bin/kagari-v010-existing-smoke --help
```

The fixed upstream N1 revision and manifest must already be available locally
for `--offline`; a missing cached source or Nix permission is a build block,
not a reason to fetch or silently replace it. `--no-link` creates no result
symlink or host GC root; the store may collect the output. Build and `--help`
never run OpenCode. No resilience expansion or release qualification follows
from these checks.

## Coordinator-only real-model boundary

Only after the offline preflight passes, the authorized coordinator can supply
an **absolute** existing fixture `project/` under an owned mode-0700
`/tmp/kagari-v010-existing-*` directory, an **absolute** fresh output path
under a separate owned mode-0700 host-temp directory, and an **absolute**
existing OpenCode executable. The source enforces its exact baseline, staged
README, Project instructions and safe fixture layout before creating runner
artifacts. The invocation (not a preflight test) is:

```sh
/nix/store/<returned-path>/bin/kagari-v010-existing-smoke --project-root /absolute/fixture/project --output /absolute/separate/new-output --executable /absolute/path/to/opencode --execute-model
```

Do **not** run that command as part of this package build/test. There is no
`--model` override: the adapter uses the configured OpenCode build model. No
Git source commit, push, PR, or production Project activation is implied.
