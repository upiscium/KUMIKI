# KAGARI Metadata v2 — #337 Phase 3A offline preflight (R1)

**Status: source-only implementation.** This is *not* a GitHub adapter,
read-only B0 proof, remote identity checker, credential verifier, permission
grant, Ruleset installation, Task/Host binding, remote conditional deletion,
CI canary, Phase 3 acceptance, or Runtime/GA activation.

The Phase 3 R0 design is in
`docs/kagari-metadata-v2-phase3-github-verification.md` (merged by PR #374).
This R1 implements only a bounded, pure, **untrusted local candidate linter**
and deterministic non-promotable `NOT_RUN` manifest codec:

- `tools/kagari_metadata_v2_phase3a_offline.py`: byte-bounded strict
  JSON parser, candidate actor/ref/Ruleset-shape consistency checker and
  canonical offline receipt builder / exact parser.
- `tests/test_kagari_metadata_v2_phase3a_offline.py`: synthetic-only
  positive/negative cases. No Git, subprocess, socket, file access, package
  installation or production resources are used by the subject API.

No CLI, filesystem, process, network, import-time I/O or callback to perform
effects is exposed by this module. Caller supplies bytes and receives a
validated Python mapping or canonical JSON bytes only.

## Trust boundary: self-reported input is never Host attestation

The only candidate accepted by `validate_candidate()` is an invented or
otherwise untrusted offline **policy proposal**. It does *not* independently
verify that a GitHub repo exists, is disposable/private, contains synthetic
data, has permissions, or has effective Rulesets. All `repository`,
`actors`, `rulesets`, `corpus` and identifiers in the input are self-reports.

Exactly these sections are accepted, with no extra keys:

- `schema=1`, `kind="phase3a-offline-candidate"`.
- `repository`: positive exact integer ID, exact owner/name, declared
  disposable status, explicit `public`/`private` visibility candidate,
  SHA-1 format and exact lowercase 40-hex default OID.
  `upiscium/KUMIKI` and its known canonical production repository ID
  (`1183254623`) are both forbidden as synthetic candidates, even if a
  supplied name attempts an alias. This is only a local denylist, **not**
  proof that another name or numeric ID denotes a disposable repository.
- `corpus`: origin `independently_generated_synthetic`, `synthetic_only`,
  claimed lowercase SHA-256 digest, and `offline-only` evidence storage.
  This digest is self-reported and **not an attested provenance claim**.
- `refs`: exact `refs/heads/kagari/metadata` and **two distinct**, sorted,
  canonical `refs/heads/kagari-metadata/<positive-N>` Issue refs only,
  using the existing core's 1–128 digit positive-decimal grammar.
  Product/default/v1 refs are not write targets.
- `actors`: two distinct positive numerical actors, `archive_writer` and
  `negative_actor`, scoped to the candidate Archive ref. These are proposed
  identities, **not authenticated actor permissions**.
- `rulesets`: *candidate* `retention` and `write_scope` rulesets with
  different positive IDs, exact Archive target and requested `active`
  enforcement. Retention must have `deletion` + `non_fast_forward` with
  **no bypass actors**. Write-scope has `creation` + `update` with **only**
  the proposed Archive writer bypass in this separate ruleset. Both must
  say `inventory="candidate_only"`. `required_linear_history` is rejected
  for the Archive because the design needs true two-parent merges.
  No value in this section attests actual effective repo/org rules or
  administrator/owner bypass policy.
- `gates`: literal `false` for `external_read_proven`,
  `remote_write_authorized` and `conditional_delete_qualified`.
  No external effect or further phase can be authorized through this codec.

Inputs are ≤32 KiB of UTF-8 JSON; duplicate keys, unknown fields, unsupported
numeric aliases (`true == 1`, float-equivalent IDs), bad refs, rule weakening,
retention writer bypass, nonfinite constants, **post-JSON-decoding escaped
unpaired Unicode surrogates** in keys or values and unsafe shape fail closed.
The parser does not execute any string as a command or URL.

## Output / no promotion

`build_not_run_manifest(candidate_bytes)` returns deterministic canonical
JSON with input **byte** SHA-256, proposed repository identity, synthetic
corpus **claim**, and one row per `AC-META2-01..11` with the original Phase 3
design gate and status `BLOCKED` or `NOT_RUN`. Evidence lists are empty;
`authorization="NONE"`, `canary_ready=false`, and both external proof and
effects remain `NOT_RUN`. It also lists eight unresolved external gates:
authentic repository ID, actual effective Ruleset and bypass, real actors
and permissions, Host owner, synthetic provenance, remote CAS, conditional
deletion adapter and independent Human grants.

`validate_not_run_manifest(data, candidate_bytes)` requires **byte-exact**
canonical equality with the sole legal builder result. Changing an AC to
`PASS`, adding an alleged proof, stripping blockers, adding arbitrary fields
or replaying against different input is rejected. There is intentionally
**no API for accepting externally generated PASS observations**. A SHA-256
receipt does not by itself authenticate a person or secure the filesystem.
P3B0/P3B1/P3B2/P3C/P3D need different, separately approved readers/runners.

## Remaining gates and tests

The current design-only R1 can test **local policy consistency**, not the
truth of any remote fact. Before P3B0, an explicitly selected disposable
repository, genuine authenticated remote principal, effective GitHub branch
Ruleset/bypass inventory, private/public visibility and synthetic corpus
provenance require **trusted independent evidence**. Never promote a
self-reported `candidate_only` Ruleset or synthetic corpus digest to real
server protection or egress authorization. No production Issue/PR, Git
object, Vault data, log or token is eligible for later canary transfer.

Phase 2D source-qualified expected-old Issue Metadata deletion remains
not implemented; #198 local Task cleanup is not an alternative. All
post-terminal ref deletion, negative force/delete tests, real GitHub
PR/comment operations, concurrent archive writer tests, fresh-clone
retention, install/migration/activation and any permissions/Ruleset
modification require separate explicit approvals under the merged Phase 3
design. Keep Issue #337 Open; preserve v1 GA.
