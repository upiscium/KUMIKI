# #339 / #337 — CLI-authenticated GitHub Host, R29 source-only read lane

**Status:** isolated source candidate; no Commit/Push/PR/Ready/Merge, no
production activation, no GitHub write, no Task/Session/model/Ask.

## Human decision and architecture

KUMIKI operates standalone in the installed CLI environment, reusing the
user's *existing* GitHub login. Standard GitHub Host transport is `gh` CLI
/ `gh api`, not a KUMIKI-managed GitHub App, JWT, PAT, token refresh
daemon or credential store. A KAGARI Task never receives a GitHub token.
This matches #339 Human direction and retains the existing #291 Main,
#337 Metadata v2, #191 Task/Contract, #256 attachment and #286
Worktree authority. Do not add another GitHub account, Main or SoT.

## First bounded, observable slice

`tools/kagari_metadata_v2_cli_readonly.py` provides the pure Host-side
selection/configuration `GhReadOnlyHost(repository, repo_id, graphql_id)`,
which calls only `gh api -X GET` with *no shell* using the ambient CLI
login. The constructor never authenticates or stores credentials.
It verifies `gh api user` (login + numeric ID), exact repository
REST ID and GraphQL Node ID, `main` branch and exact direct Git ref
identity; read-only Issue lookup returns only the exact selected Issue.
The `metadata_observer` method returns the canonical #337
`metadata_v2.RefState(ref, commit, direct=True)`. Expected absence is
returned only on a GitHub 404, never on auth or API failure.

If the CLI is unauthenticated, GitHub API is unavailable, response is
not bounded valid JSON, the repository/ref/Issue is foreign, a direct
ref points to a noncommit, the deadline expires or the repo identity
drifts, refuse explicitly. The Host must recheck the current GitHub
actor and exact Task/Contract/grants at each *effect* boundary; this
read-only slice does NOT qualify those writes.

The adapter allocates at most 2 MiB from `gh` stdout before parsing,
uses temporary files for process output (not token stores), discards
stderr to avoid credential leakage, checks bounded timeouts, rejects
duplicate JSON keys and nonfinite constants, and never runs a command
derived from untrusted JSON. It does not introduce an authentication
proxy, secret manager or custom credential provider.

## Deliberately unimplemented in this R29 slice

- GitHub `git/blobs`/`git/trees`/`git/commits` **upload** and
  byte-for-byte object read/decode; R15 proof only validates fixture
  feasibility. A complete #337 ObjectReader is not yet wired here.
- Signed in CLI is not a model/Task operation grant; no Metadata v2
  `Writer` callback, `updateRefs` mutation, object upload, PR branch,
  PR comment, concurrent writer lease, protected-branch negative test,
  Archive/Issue metadata ref creation or ref deletion.
- No `V2_MAIN_EXECUTION_NOT_QUALIFIED` removal. No v2 model launch,
  Human Ask reply, scheduling, service installation or release.

## Qualified evidence

- R29 isolated source checkout pinned KUMIKI
  `main@52f2a1c80ef932c2e79b5d7477602813dad958a0`.
- `tests.test_kagari_metadata_v2_cli_readonly`: 16/16 isolated
  test methods PASS using injected fake GET-only `gh` runner.
- Live Adam `gh` (authenticated as `upiscium`) exact private
  E2E fixture preflight, `main@550ef412c38429c2e5b6f17fa2b60843357afc35`,
  absent archive Ref and R15 archived OID
  `cb031c0777d2dc4670b26c641bbc960ba21c514a`: PASS,
  without any remote mutation.
- Source and test hashes belong to the final staged files; no changes
  to original KUMIKI Worktrees or existing PR #375.

## Next source-only slice

Add canonical raw-Git-object `ObjectReader` and a fully fail-closed
effect-time `Writer` adapter backed by the **same** installed `gh`
identity and exact selected repository. Before any remote CAS test,
pin a separate Human-approved synthetic Issue and exact ref/PR/write
scope. Use R15 server `updateRefs` receipts as a feasibility baseline;
never silently use App tokens, `force`, unconditional REST ref updates,
foreign refs or a generic arbitrary shell command transport.
