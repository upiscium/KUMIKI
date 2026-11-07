# KUMIKI #337 Phase 2A — versioned read-only Metadata v2 Host binding

> **2026-11-07 #337 compatibility notice:** Archive and Issue Metadata branches are ordinary Git branches. Older Ruleset, dedicated App, protected-Archive and server-side-retention requirements below are historical, not active prerequisites for KUMIKI operation or acceptance. See [the ordinary-branch contract](kagari-metadata-v2-ordinary-branch.md). Keep internal KUMIKI permission, terminality and readback controls.

**Status:** source-only candidate; not installed, activated, published, or
configured as a production adapter. Phase 2B/2C/2D and real GitHub Phase 3
remain separate Human gates. Phase 1 source landed through PR #341.

## Purpose and hard boundary

`tools/kagari_metadata_v2_host_binding.py` supplies a narrow, typed Host
consumer of the already-reviewed `metadata_v2.Validation` and v2 Git DAG.
The bound Host can retrieve an immutable Record, Contract, Evidence, or
Task View Snapshot by its **specified** original Git commit and exact
content-addressed ID, using active Issue ancestry or retained archive
ancestry. It never substitutes the latest metadata tip for an old checkpoint.

The existing v1 `MetadataStore` on `refs/agentcore/metadata`, `#191`
Task Record API, `#144` Task View, `#145` Evidence, `#291`
`RepositoryMain` and `#256` Install/Runtime remain unchanged. There is no
new default provider, version selection in a plan file, HTTP service, Nix
copy rule, Git transport, token lookup, credential, mutation callback,
workflow phase or Task authority.

## Types and caller contract

`RepositoryIdentity(repository_id, repository, genesis, git_object_format)`
is an immutable Host-owned binding of the positive numeric canonical
GitHub repository ID, case-exact owner/name, pinned independent orphan
genesis SHA-1 Git OID, and explicitly `git_object_format="sha1"`. It is
**attested by the trusted caller**, not authenticated by this Python
dataclass. Supplying matching numbers and strings from arbitrary JSON is
not repository identity proof.

`RegisteredIssue(repository_id, repository, issue, branch_ref,
base_revision, record_id, contract_id)` binds the trusted #286/#191
registration. The Issue is a positive integer; the full Product branch,
base/Contract subject and immutable 64-hex Record/Contract content IDs
are exact. This class does not itself inspect an installed Worktree,
check live GitHub Issue text, or create a Task.

`ExactLocator(version, repository_id, repository, issue,
metadata_commit, kind, object_id, subject)` requires `version=2`, the
same canonical repo ID/name/Issue as above, a **full 40-hex SHA-1 commit**
(not `latest` or branch name), one of the four v1 canonical object
kinds, one full SHA-256 Metadata object ID and the exact 40-/64-hex
subject. `from_mapping` accepts an ordinary dict only with all required
keys and no others. It does not parse legacy PR comments or convert
a v1 locator: that work is Phase 2D.

`ReadOnlyMetadataV2Host(identity, registered, reader=..., observer=...)`
accepts only explicitly injected bounded `ObjectReader` and direct-ref
`Observer`; it constructs `MetadataV2` **without any writer** and
exposes `read(ExactLocator)`. It has no `publish`, `install`,
`archive` or `delete` operation. Python code that can replace trusted
Host callbacks or inject a writer is beyond this adapter's guarantees.

### Read sequence (one bounded validation)

1. Reject unsupported version/identity, invalid Git format or malformed
   exact locator before acting on metadata. No caller-selected Git ref,
   remote hostname, Git command or default branch is accepted.
2. Via the Host-provided callbacks, observe exact direct
   `refs/heads/kagari/metadata` and optional
   `refs/heads/kagari-metadata/N`. Missing/unknown/symbolic **archive**
   ref refuses; the Issue ref may be *proven absent* only when its history
   remains reachable through the valid archive. Unknown/symbolic Issue
   ref observations also refuse.
3. Validate the archive's entire bounded two-parent DAG and the Issue's
   genesis-rooted linear history. Both are **reachability roots only**.
   Resolve the requested original `metadata_commit`; reject if absent.
4. In that *same commit's* validated snapshot require
   `tasks/N/record` equals the bound Record ID, require that Record
   and Contract IDs are present and correctly typed, and verify their
   Issue, repository, base revision, Product branch and Contract link.
   A new/latest Record pointer cannot retroactively validate an older
   or different historical checkpoint.
5. Decode the requested kind/ID/Task/subject from the **same pinned
   commit's** bytes with the existing closed Metadata envelope. Validate
   resource deadline and re-observe both exact refs; reject changed or
   uncertain results, without retries or side effects.

The `snapshot_links` validator still checks snapshot authority,
Evidence/previous-checkpoint graph, and ancestor binding at every relevant
Git commit. Metadata payloads are *data*, not a permit for Task execution,
Human decisions, GitHub mutation or archive deletion.

## Host capability obligations / limitations

- The trusted Host must independently bind the authenticated repository ID,
  exact name, genesis and issued Task/Worktree/Contract registration to the
  **same** Git object reader and ref observer. This adapter cannot prove
  that a caller-supplied ID is truly the GitHub repository ID.
- Object reads must enforce `max_bytes` and monotonic deadline **before**
  allocation/blocking. `RefState` must mean exact direct ref or
  *proven* absence, not `rev-parse` DWIM/prefix lookup. Uncertain reads
  raise. Client-side repeated ref observations are not atomic CAS, lock,
  ABA exclusion, or proof against a server-side delete/recreate race.
- Metadata v2 core supports only SHA-1-format **Git** repositories in
  this slice; Metadata content IDs remain domain-separated SHA-256.
- Default validator limits are 256 commits, 4096 cumulative tree entries,
  32 MiB unique raw-object bytes, 32768 decoded JSON containers and ten
  seconds (with the core's separate hard caps). No arbitrary Python
  callback may claim bounded execution merely because a deadline is
  passed. This adapter adds no generic shell/read interface.
- Phase 2A does not initialize the orphan, mutate either Metadata ref,
  guarantee server protection, conditionally delete Issue refs, attach
  a real Worktree, send a GitHub PR checkpoint or choose an opt-in profile.
  It cannot establish live remote retention or any Ready/Merge eligibility.
- Explicit terminal disposition, writer quiescence, unknown ACK intents,
  real GitHub Ruleset no-delete/no-force policy and ref-type/TOCTOU tests
  remain Phases 2B/2C/2D and 3. No automatic replay, force push,
  migration from `refs/agentcore/metadata`, or GA v0.1.0 cutover.

## Offline test and evidence

`tests/test_kagari_metadata_v2_host_binding.py` reuses Phase 1's
strict disposable bare-Git `Fixture`. Read-only behavior is checked
against exact active and archived original commits (including source-ref
deletion and new clone), different-Issue exclusion, Evidence subject,
Record update/revision drift, bad locator schema/version, missing and
symbolic ref, observer races, wrong repo identity, unsupported format,
resource limits, and unchanged dirty/staged/untracked Product Git state.
Those temporary fixture Git writes model setup **only**; the adapter
itself performs no writes, network calls or subprocesses.

Qualification requires focused tests, v1/Phase 1 regression tests,
fixed SHA-256 source manifest, independent correctness/security
source inspection, and a separate Human gate before source publication.
All tests on synthetic local Git are **not** production GitHub acceptance.
