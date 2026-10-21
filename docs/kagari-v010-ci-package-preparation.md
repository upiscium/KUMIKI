# KAGARI 0.1.0 — exact CI package preparation (#277)

This is a local **source candidate** for the existing `Template CI` eight-shard contract suite. It does not authorize source publication or count as a completed GitHub Actions run.

## Why the change is necessary

The canonical `tools/ci_contract_runner.py` discovers all tests, then distributes whole ordinary modules across eight shards. Adding tests can move an existing KAGARI package-test module from shard 5 to 4, 6 or 7. Hard-coding a shard-to-package map is therefore incorrect.

Three tests depend on real Nix store packages: `test_kagari_v010_native_package`, `test_kagari_v010_existing_smoke_package` and `test_kagari_v010_composed_candidate`. They cannot simply skip when a package has not been prepared. Without exact packages, two suites ERROR and one original smoke test produces an unreviewed skip, which the canonical report reconciler must refuse.

## Narrow implementation

`tools/kagari_v010_ci_packages.py` uses the **same** `ci_contract_runner.discover()` and `assignments()` implementation as CI. The workflow runs:

1. A no-Nix `--requirements-only` probe per shard, writing `needs_nix=true/false` to `GITHUB_OUTPUT`.
2. Installs Nix **only** when this canonical assignment requires one or more KAGARI package tests.
3. Uses the lockfile-pinned current checkout to build the exact Nix expression(s) with `--no-link --print-out-paths`. Validates package basename, actual Nix store root, version/qualifier, receipt and source tool SHA-256.
4. Emits a single `KAGARI_V010_*_PACKAGE=/nix/store/...` line per required package to `GITHUB_ENV` for the following normal contract shard step.

This retains all original eight shards and every canonical test. Do not alter `KNOWN_CI_SKIP_IDS`, weaken `ci_contract_runner.py` or invent CI results from a local build. Nix inputs use the checked-out `flake.lock`; historic Native N1 baseline stays at `5e0a4fd35b0e2e8014ff35f2ce7dc8e600e84812`. The packages remain `TEST_ONLY_UNQUALIFIED` or `UNRELEASED_CANDIDATE`.

## Local verification and outstanding release gate

With the selected source on Adam, the canonical mapping probe was run for all eight shards. Package builds/receipts were validated for every shard that required them; the mapping changed as tests were added and was followed correctly. Focused CI-helper, native package, existing smoke and composed package tests should be run again on any future exact published revision.

**Not yet established:** GitHub Actions PASS for all eight actual shards plus reconciliation on a separately authorized Publication revision; full independent exact-byte correctness/security review; production-qualified installer or real model Task. The workflow is itself Publication-sensitive, so the complete source SHA/Manifest and review must be updated before any push/PR.
