# Metadata v2 — ordinary Git transport local fixture (Phase 3B2 R0)

**Status:** Source-only tests and design, based on KUMIKI #337 after PR #381.
Not a production Host adapter, live GitHub canary, install, Release, or activation.
The new test file is `tests/test_kagari_metadata_v2_ordinary_git_transport.py`.
No repository Ruleset, Branch Protection, GitHub App, admin setting, webhook, or
nonstandard Git server feature is introduced.

## What is exercised

- Every test uses fresh Python `TemporaryDirectory` Git repositories.
  `ordinary-remote.git` is a **real bare receive-pack remote** with an empty
  template, not the earlier synthetic protected-hook receiver. No HTTP, GitHub,
  credential, or external network is accessed. The test uses the local
  `file://` Git transport and the installed `git` binary only.
- An independent synthetic Product `main` root and a Metadata-only orphan
  genesis are distinct branches. All Metadata operations leave `main`
  unchanged. Each Issue's first active Commit descends from the shared genesis,
  never from the most recent Archive tip.
- The real `MetadataV2` core (injected local Host reader/observer/writer)
  builds, validates, publishes and **re-reads via the bare remote** two Issue
  branches. `archive_issue(..., retention_policy=None)` builds true two-parent
  J(A,C) validated union commits, then the fixture pushes them as ordinary
  non-force Git branch updates. Its terminality callback is a **synthetic
  placeholder** checking only task/ref/deadline, not live Human authorization,
  Task terminality, stopped writers, or Product preservation.
  No GitHub policy is required.
- A stale/non-fast-forward Archive contender is rejected by an **ordinary
  non-force Git push**; this test does not attribute that rejection to the
  client versus receive-pack. No extra protected-ref configuration is needed.
  This does not prevent another actor from force-pushing or deleting Archive.
- A fixture-only cleanup checks that exact Issue tip C is reachable from the
  current ordinary Archive. It then uses Git's standard
  `--force-with-lease=refs/heads/kagari-metadata/N:<expected_C>` with a
  **delete refspec**, so a concurrently changed ref C' refuses deletion.
  This is ordinary client-side Git expected-old coordination, **not a new
  GitHub Ruleset or a production cleanup permission**. The real Host must own
  the Task/lease/terminality/effect boundary and handle unknown outcome.
- After the two Issue branches are deleted from this disposable bare remote,
  a fresh independent clone still reads exact historical Commit/Record/Contract
  objects from the retained Archive DAG. The default branch stays unchanged.
- An injected lost ACK *after* an actual Issue Git push produces
  `UncertainPublication`. The remote is positively observed; re-submitting
  the first-Record operation is refused. This test alone does not persist a
  production recovery journal.

## Exact scope / omissions

Local fixture tests are evidence for Git transport semantics, **not** for
production GitHub authentication, repository/ref type races, **live Task
terminality/Human authorization/stopped writers**, a durable production-only
writer lease, PR checkpoint comment originals, Product PR merge authority,
or safe metadata deletion with real external actors.
No source file in `components/` or `tools/` is changed by this R0 slice.
Legacy v1 remains untouched. External force/delete risk is an accepted
property of ordinary Git, per #337.

## Next separately approved GitHub canary — staged and minimal

Target only the already-created private synthetic disposable repository
`upiscium/kagari-metadata-v2-p3-disposable-20261107` (ID `1449093045`).
Its previously observed default `refs/heads/main` was
`14c3d50f1beb4a37cb84fae41bfd65260687d09c`; re-attest that **exact**
ID, visibility, main OID, absence of unexplained refs and enabled Git object
format immediately before any external write. Do not assume a prior read
authorizes later effects.

P3B2-A (requires **specific new approval**): generate an independently
synthetic, bounded corpus with the exact disposable repository identity; pin
all outbound bytes/hashes in a private receipt before publishing. Scope only
`refs/heads/kagari/metadata`, `refs/heads/kagari-metadata/101`, and
`refs/heads/kagari-metadata/202`; publish orphan genesis, Issue C1/C2 and
true two-parent J1/J2 via **normal non-force Git**, with explicit original
OIDs and post-write independent remote readbacks. No Product/main changes,
PR creation, Issues, tokens in receipts, workflow install, Ruleset,
GitHub App, Phase 4 activation, or branch deletion. If any ref unexpectedly
exists or a write has unknown result, stop and reconcile the original,
without automatic retry.

P3B2-B (separately approved after A): additional current-Task/Host binding
and synthetic PR/checkpoint tests, if still needed by #337. P3C cleanup
(additional destructive approval): conditional ordinary Git Issue-ref delete
with a verified expected-old lease, stopped KUMIKI writer, Archive ancestry
readback, stale-C negative test and fresh-clone re-read; do not mistake a
successful local fixture for a qualified GitHub delete. No need to create
new GitHub settings or any App at any stage.

**Stop boundaries:** R0 does not authorize any Commit/Push/Draft PR in
`upiscium/KUMIKI`, any real GitHub Metadata ref effect or cleanup, or any
Ready/Merge/Deploy/Activation. Keep KUMIKI #337 Open.
