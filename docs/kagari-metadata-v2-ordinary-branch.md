# Metadata v2 — ordinary Git Archive branches (R1 source-only)

**Authoritative policy:** KUMIKI Issue #337, Human decision 2026-11-07 JST.
**Status:** Source-only API/journal compatibility implementation and isolated tests. No runtime activation, no live Metadata ref/PR changes, no GitHub Ruleset/Branch Protection/App requirement.

## Invariants and supported behavior

- Archive is an ordinary Git branch: `refs/heads/kagari/metadata`. Active Issue refs are ordinary `refs/heads/kagari-metadata/N`.
- Metadata genesis, immutable object graph/Subject/Task/Record/Contract semantics, exact historical snapshot locators, authenticated KUMIKI Host approval, original Task terminality, held Host quiescence, and non-force Archive append remain.
- Build true two-parent J(A,C) and validate its semantic union; the trusted Host then publishes through ordinary Git. Re-read exact J ancestry and Issue C at the observation boundary.
- Never silently treat Unknown ACK as success: persist the original candidate and reconcile it on later Host observation, without constructing a fresh attempt.
- After positive archival readback, ordinary Git Issue-ref cleanup may be qualified separately. This R1 does **not** implement remote deletion or approve a destructive test. The old product/v1 ref remains untouched.
- The Archive branch is intentionally **not protected against an unrelated actor** who deletes or force-rewrites it. KUMIKI only promises correct behavior of KUMIKI-owned operations under its own writer authority; it does not demand GitHub repository settings or independent negative actors.

## Versioned compatibility with previously published source APIs

- `metadata_v2_archive.archive_issue(... retention_policy=None, verify_terminal=..., archive_writer=...)` is the new ordinary-branch mode. The terminal verifier and trusted writer remain mandatory.
- `metadata_v2.Update.retention_policy: str | None` remains as a compatibility field for existing v2 Host callbacks; None conveys ordinary-branch mode, **not** a fabricated Ruleset label. Existing nonempty string values remain accepted for legacy callers, including fresh operations during migration and recovery of old journals. They never attest an external Ruleset; new callers should omit the field. Invalid strings or other types are refused.
- `kagari_metadata_v2_archive_host.ArchiveOperation.retention_policy=None` is the ordinary-branch mode. No new GitHub App ID, access token, Ruleset ID or role is needed to instantiate it.
- Journal v2 is **unchanged**: original `schema=1`, `version=2`, exact `policy` string and the rest of the existing fields. Legacy attempts with unknown ACK must be recoverable under their original operation.
- Journal v3 for ordinary-branch mode: `schema=1`, `version=3`, exact `branch_mode="ordinary-git-branch"`, with **no `policy` field**. All other historical identity fields and the original candidate/terminal facts remain bound and rechecked.
- Both versions retain the same A/C-derived operation key. If a saved attempt's journal version/mode differs from the requested operation, refuse **without a new write, retry, overwrite or mode reinterpretation**.
- Internal KUMIKI authorization, bounded resource/operation quotas, Task/Contract identity, Product work preservation, and original Archive DAG validation are not Ruleset substitutes. They are still required for KUMIKI's own correct behavior.

## Scope and follow-up

R1 qualifies source-only ordinary-branch mode, plus preservation of legacy readback/recovery. It does **not** prove production Git transport wiring, cross-ref atomic CAS under uncooperative external actors, consumer activation, Issue-ref deletion, or absence of concurrent external Archive rewriting.

P3B0 initial disposable identity observation is already complete. Mandatory P3B1 Ruleset/actor gates are **retired** by Issue #337. A later independently authorized disposable ordinary-branch integration test shall cover two Issue refs, ordinary non-force J1/J2 appends, exact ref/OID checkpoint readbacks, bounded competing publish/ACK-loss cases, ordinary Issue-ref cleanup and fresh clone. No configuration changes are needed. Existing historical Ruleset-oriented Phase 3A files stay as non-authorizing artifacts; they must not be used as current acceptance gates.

All Commit/Push/PR, Ready/Merge, canary, deletion and activation remain separately gated. Preserve KUMIKI v1/GA and keep #337 OPEN.
