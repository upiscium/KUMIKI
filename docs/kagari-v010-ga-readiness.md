# KAGARI / KUMIKI 0.1.0 GA — bounded-profile qualification plan

**Status: BOUNDED GA PROFILE LOCALLY ACCEPTED / UNPUBLISHED / PRODUCTION NOT ACTIVATED.**
Primary tracker: [#283](https://github.com/upiscium/Templates/issues/283), under
[#271](https://github.com/upiscium/Templates/issues/271). This document
records present evidence and missing gates. It is not a product Release
certificate, a Human approval, or permission to mutate the Source Guard.

## GA product contract — explicitly limited first profile

- Linux/Nix and an ordinary, euid-owned Git repository with a real `.git`
  directory, configured OpenCode 1.18.35 and pinned Bun 1.3.13.
- External, immutable KAGARI RuntimeRoot; ProjectRoot owns flake, Justfile,
  AGENTS.md, OpenCode policy, Git index/staging, data and prior Evidence.
- All model/Product mutations happen in a new private, full-history,
  **no-remote Git Work Unit**, never in-place in the attached source Project.
- Project HEAD, TaskSpec, allowed Product paths, test argv and runtime
  identities are bound to **fresh foreground-TTY Human Ask receipts**.
  Existing disabled/retired grants never transfer to a new package.
- Model-created bounded Planning and two genuinely concurrent Native children.
  Host-owned tests, exact Product-only local commit and Native v4
  Record/Contract/Evidence/Task View with fresh-process readback.
- Result and replayable full-index **binary Product patch** must be retained
  in external persistent Job state and readable without the `/tmp` Work Unit.
  Human Product integration remains explicit. No automatic source Project
  Git changes, Push, PR, Merge or release activation.
- GA SemVer `0.1.0` is a public distribution contract, not an internal
  Native/v4 schema generation, a renamed RC receipt or the old N1
  `target_version=1.0.0`. The product must never claim a stronger
  qualification/identity guarantee than the exact accepted source warrants.

## Accepted RC evidence (2026-10-21 JST)

| Gate | Scope and actual evidence | State |
|---|---|---|
| Source/packaging | Public `v0.1.0-rc.1`, tag to merged source `b0a5c0c7…`, Nix package/receipt reproducible | PASS (RC only) |
| Local regression | Canonical 8 shards: 1,672 unique tests, 0 skipped | PASS (RC only) |
| Public CI | 15/15 jobs SUCCESS, 1,672 unique tests, 8 allowed skips | PASS (RC only) |
| Model Task | Fresh exact three TTY grants; two Native leaves, 24,418 ms overlap; 156/156 Product tests; local Product commit | PASS (RC only) |
| Evidence | Native v4 fresh-process readback and independent 59/59 model/uninstall checks | PASS (RC only) |
| Separate Project layout | TERREATE real clone lifecycle/ownership preservation 55/55 | PASS (RC only) |
| Human Ask retirement | Uninstall restored exact AGENTS SHA and revoked both grants; original MUSUBI untouched | PASS (RC only) |

## Separate GA candidate package — current implementation

This development branch retains three Linux-only Nix outputs. The public
`v0.1.0-rc.1#kagari-rc` and previously model-tested
`#kagari-candidate` outputs remain byte-for-byte identical to their
pre-existing acceptance. The new `#kagari` is a **distinct bounded GA
profile**, now accepted through its own fresh exact model run.

| Property | New bounded GA proposal | Previously tested candidate | Published RC |
|---|---|---|---|
| Nix output | `kagari-0.1.0` | `kagari-0.1.0-ga-candidate` | `kagari-0.1.0-rc.1` |
| CLI | `kagari-v010-ga` | `kagari-v010-ga` | `kagari-v010-rc` |
| Qualification | `GA_BOUNDED_PROFILE_QUALIFIED` (package declaration only, release pending) | `GA_CANDIDATE_NOT_QUALIFIED` | `RC_RESTRICTED_PROFILE` |
| External namespace | `kagari-v010-ga-stable` | `kagari-v010-ga` | `kagari-v010-rc` |
| Release channel | `stable` target, external release unverified | `ga-candidate` | `prerelease` |

The bounded profile declaration binds an exact
`docs/kagari-v010-ga-bounded-qualification.json` receipt SHA-256 to
the historical accepted real-model/Archive/Uninstall evidence and minimum
Linux/Nix Work Unit support contract. This declaration **does not**
cryptographically certify provider identity, promote the standalone internal
Native component (still `TEST_ONLY_UNQUALIFIED`), or activate a host.
`publication_attestation=EXTERNAL_UNVERIFIED` and
`production_activation=false` remain mandatory. The new Store path
has now passed new package-bound foreground-TTY model acceptance. External
publication and production activation remain separate closed gates.

Build only from a locally reviewed exact source checkout, for example:

```sh
nix build --impure --no-link --print-out-paths 'path:/absolute/GA-candidate/source#kagari'
# The returned Store path contains bin/kagari-v010-ga.
# Run bin/kagari-v010-ga version first; inspect its exact package receipt.
```

The GA candidate requires the same limited initial Linux/Nix 1.18.35 OpenCode
profile and pinned Bun 1.3.13, a real ordinary owned Git repository, explicit
external state home, private full-history no-remote Work Unit, and separately
approved foreground-TTY consent for every new job. It does not edit a live
attached Project or push/integrate a Product without a new explicit decision.

## Durable Product output after successful Task

The new `tools/kagari_v010_ga_product_archive.py` is **inside the separate
GA package** and source/receipt verified. After Native tests, Contract,
local Product-only commit and fresh-process v4 Evidence succeed, the GA runner
archives the completed Product commit before saving host success:

- `product-commit.patch`: bounded 16 MiB full-index binary Git format-patch,
  saved as mode-0600 in euid-owned external 0700 Job history
- `product-artifact.json`: exact source HEAD, Product commit and allowed/actual
  Product paths, GA package Receipt, native-v4 Evidence and patch SHA-256
- Independent `verify` reads the artifact without the disposable Work Unit.
  Missing/corrupt bytes, malformed scope, wrong Product parent, invalid owner,
  symlink/alias or oversized patch BLOCK rather than claiming a successful
  durable Product delivery.

No automatic patch application to the original Project is implemented. A
human operator must separately choose a target and replay it in a reviewed
no-remote Clone or other explicitly authorized destination.

The previously accepted MUSUBI RC Job was **post-hoc** archived separately;
its 4,346-byte patch SHA-256
`1d0ef1ff17ee1d01fd3523911505c951390acaa264309d25b1711f9845d451d7`
replayed to a no-remote Clone and exactly matched both authorized Product
files (independent 17/17 PASS). This is historical RC evidence, not a
successful model Task under the *new* GA identity.

## Verification and outstanding gates

- Separate GA packaging: local Nix build/receipt verification, CI package
  selection and GA lifecycle/negative tests PASS; published RC output unchanged.
- **Fresh real-model acceptance completed on this exact GA candidate**:
  three distinct Human Asks approved in Adam foreground TTY; the restricted
  managed MUSUBI Work Unit ran two genuinely overlapping Native children
  (30,084 ms), completed 156/156 Product tests, and made Product-only commit
  `a1a4bdd80be54fcb1900048ef7ae397c99a9c742`.
  Native v4 independent readback passed; the newly integrated archive
  automatically saved the 4,222-byte full-index binary patch (SHA-256
  `720af620549ce278160845b38ba6c137974914ce6ddd4b0253cba9bfffbcd6a2`)
  **before** the GA host saved Task success. Independent read-only post-Task
  audit: 57/57 PASS; independent no-remote Clone replay: 27/27 PASS;
  Uninstall revoked both grants/restored AGENTS; post-Uninstall audit: 31/31
  PASS. Original MUSUBI and the staged consumer README were untouched.
- Source-bound GA/RC package reproducibility, Nix flake all-systems evaluation,
  1,696/1,696 canonical tests (eight shards, zero SKIPs), and 23 focused
  GA lifecycle/archive tests all passed. A distinct real TERREATE Clone
  passed non-model GA Install, Doctor and Uninstall, with original unchanged.
- **New bounded GA `#kagari` accepted**: immutable `kagari-0.1.0`
  has its own distinct, exact foreground-TTY approvals and genuine
  configured-model execution. Two Native child sessions overlapped
  **29,804 ms**, **156/156 Product tests PASS** and the exact Product-only
  commit was `e9b7a192131705448f1b456841fc8cb9ce71b68c`. External
  Job state retained the 4,457-byte full-index binary patch, SHA-256
  `64cacbd855c36966cc82848e3537868a187b0cd8e5c7f298fe2e20e75ac7c51a`,
  saved before any host Task PASS. Independent Task/Evidence audit **57/57
  PASS**, isolated no-remote Git patch replay **27/27 PASS**, Uninstall
  revoked both Native grants and restored AGENTS.md, and post-Uninstall
  readback **31/31 PASS**. Original MUSUBI and staged consumer files
  remained unchanged. Standalone Native stays `TEST_ONLY_UNQUALIFIED`.
  The stable source's 8-shard canonical suite reconciled **1,708/1,708 PASS**,
  0 SKIP; earlier Git metadata timeouts did not recur in sequential retries.
  Independent bounded correctness, packaging/security, and archive reviews
  each returned source-scoped PASS, with no concrete new P1/P2.
- **Remaining public GA Release gates**: separate exact-scope Human
  Source Publication authorization, protected source Commit/Push/Draft PR,
  GitHub CI against that exact source, explicit Merge authorization,
  stable-tag Nix output reproducibility, and distinct GA Release approval.
  No source acceptance alone authorizes a Source Guard change or a
  production host activation; Product integration remains human-controlled.
  External publication attestation is `EXTERNAL_UNVERIFIED` until checked.
- Scope-limited external Draft PR/checkpoint acceptance from #271: a private
  disposable GitHub fixture passed with actual GitHub PR/comment API and
  local private bare Git metadata CAS. It is **not** shipping Product Push
  integration or a qualified GitHub metadata CAS.
- `#272` linked Git worktrees and `#273` arbitrary `.opencode` generated
  configurations remain unsupported. `#276` transient Session Export disk
  use remains an accepted LOW/Post-v0.1.0 limitation. Broad resilience and
  malicious same-UID defenses are still frozen.

**This document authorizes no Source Guard, publication, GA Release or
production installation. Product integration always needs a separate Human
decision.**
