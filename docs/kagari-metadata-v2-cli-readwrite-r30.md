# KUMIKI #339 / #337 — R30 existing-CLI Git objects and Issue CAS candidate

**Status:** isolated source-only candidate, 2026-11-07 JST. No GitHub
commit/ref/Issue/PR mutation, Commit/Push/Draft PR, Runtime activation,
model/Ask, Archive merge or deletion. This is not a deployed Host Writer.

## Baseline and mandate

Human decision: **KUMIKI is standalone**, using the installed CLI
environment's *existing* GitHub login (prefer `gh api` and authenticated
Git transport). GitHub App, a second credential account, PAT manager, JWT,
token refresh daemon, auth proxy and second Task SoT are NOT prerequisites.
CLI credentials are *transport* privilege, not Issue/Contract/Task grants.

Source based on `upiscium/KUMIKI main@edd39b7ff891e6d57db7ecaecd28713bbd6ab51e`.
R29 read-only Host adapter and tests were copied into this isolated checkout
byte-for-byte; R30 adds `ObjectReader` and opt-in Issue CAS Writer.

## #337 canonical ObjectReader (real read-only API)

`tools/kagari_metadata_v2_cli_objects.py` takes the same authenticated
`GhReadOnlyHost` from R29, reconstructs raw SHA-1 Git objects from
bounded GitHub REST Blob/Commit/Tree responses, and verifies their
`metadata_v2.git_id` before returning `(kind,bytes)`.
- Reject duplicate/unsafe tree paths, symlink/executable modes, oversized
  objects, invalid author/date, unsupported REST normalization, foreign OIDs,
  expired deadlines, unavailable CLI/auth. Do not substitute Product history.
- Canonical known Git empty tree `4b825dc642cb6eb9a060e54bf8d69288fbee4904`
  is locally reconstructed from fixed `b""`. The *hosting* orphan Genesis
  ref/commit must still be independently attested by trusted Host.
- **Actual GitHub API nuance observed in R30:** `GET /git/trees/<commitSHA>`
  can dereference a Commit OID and return a 200 response with the input SHA.
  We therefore probe Blob, then **Commit before Tree**, then verify raw Git
  SHA; otherwise the read would misclassify a real Commit. This was
  discovered in real R15 fixture smoke and corrected before acceptance.

## #337 Issue-ONLY CAS Writer (candidate; not installed)

`tools/kagari_metadata_v2_cli_issue_writer.py` implements the existing
`metadata_v2.Writer(Update,Bundle,deadline)->bool` shape:
- Constructor requires the identical R29 read Host and R30 ObjectReader,
  plus an **explicit immutable positive numeric `expected_actor_id` bound
  by the trusted Main/Host at admission**. It must not be acquired afresh
  from `host._identity` after a later `gh auth` switch. The `effect`
  transport, `authorize` grant and `original_intent` verification
  callbacks have **NO DEFAULT**. Without all three supplied by trusted
  Main/Host it fails before any remote POST. The principal binding and
  callbacks are operation authority, not credentials transferable to a model.
- #337 IssuePublisher must already have fsynced the *original* #140 intent,
  admitted the exact approved Issue/Contract/Worktree/Attachment, and held
  one repository Main owner and any required writer exclusivity. Its outer
  trusted Host retains effect-time Task grant checks and unknown-ACK recovery.
- Writer accepts **only** `refs/heads/kagari-metadata/<canonical-N>`.
  It refuses Archive/other/Product refs, retention-policy Archive writes,
  changed actor/repository, stale Archive or Issue expected OIDs,
  foreign/invalid Bundle objects and unexpected object trees.
- Small bounded initial Issue Bundle only: one-parent (existing Issue C or
  orphan Genesis G), immutable canonical Metadata v2 author/committer bytes,
  generated nonce, allowed `objects/...json` and `tasks/N/record` only.
  Flatten deterministic SHA-validated Git trees and upload exact Blob,
  Tree and Commit objects through injected Host effect runner with the
  existing `gh` user; verify each expected Git SHA/remote readback.
- Recheck the original Host-admitted **numeric CLI actor**, exact repository
  identity, Issue/Archive direct refs, and independent Host grant/intent
  **again after object upload**. Fail closed on an unexpected actor switch;
  after any upload, unknown state is classified as `CliWriterUncertain`.
  The #291 Main/registered Task's immutable `base_revision`, current
  Contract/Worktree/Attachment and effect-time approval are separately
  revalidated by the trusted Publisher/Host callbacks. The *current default
  branch `main` tip is not pinned by this Writer*: unrelated Issue/PR merges
  may advance it without invalidating a still-authorized Task operation.
  Then submit **one** GraphQL `updateRefs` mutation with two atomic updates:
  (1) move exactly the selected Issue ref from expected-old C or all-zero
  expected-absent to candidate, **force=false**;
  (2) no-op Archive A from expected-old A to A, **force=false**,
  acting as the same-transaction Archive version guard. This is the R15
  exercised GitHub server protocol. Never update Product/main or Archive.
- Match original `clientMutationId` in response; re-read actor and both
  direct refs (Issue candidate and unchanged Archive). Only then return
  `True`. All uncertain effects/readback errors raise a typed
  `CliWriterUncertain`, with **no second POST, fresh nonce or CAS retry**.
  The original #337 journal/recovery path is the sole source of truth.
- The writer object does **NOT** run live POST via its own defaults; only
  an independently approved, trusted Host callback can supply that
  effect capability. The source presence is not live authorization.

## Proof and limitations

- Combined 3 focused modules (R29 read-only, R30 raw reader and Issue
  CAS writer) in local disposable checkout: **37/37 PASS** on synthetic
  responses. Includes expected-absent, two-ref CAS, duplicate/regrant,
  stale-old refusal, foreign paths, effect-ACK unknown, short-circuit on
  missing Host approval and GitHub tree-Commit SHA dereference regression.
- Actual authenticated `gh` read-only R15 private fixture: exact
  Blob `053a99e346e499042eddc431c0c255a9c565d229`,
  Tree `61c6378884d2d7cb378a8013f944ac25646ad81d`,
  Commit `cb031c0777d2dc4670b26c641bbc960ba21c514a`
  and canonical empty Tree all hash-verified **PASS**. No API writes.
- **UNPROVEN:** any live R30 Writer POST/atomic CAS, Host callback
  integrity under actual #291 MainSupervisor, actual synthetic Issue/PR
  creation, GitHub PR checkpoint, Archive/terminality/race/delete,
  process-crash/lost-network ACK and live model/Ask.
- No independent source/security review is asserted unless a pinned review
  log exists; no acceptance of complete #337 P3B2/P3C/P3D or #339 E2E.
- Preserve original user threat model: `127.0.0.1` only/no mandatory
  same-host HTTP auth; CLI account may have broad privileges, bounded
  *operation* checks are not credential-level least privilege.

## Next separate grants

1. Review this R30 candidate and complete adjacent regression tests
   independently; if accepted, **separately authorize** source
   Commit/Push/Draft PR, then Ready/Merge after CI and review.
2. Extend the trusted Host callbacks to exact #291 Product PR-first and
   checkpoint permission/lease/Journals; support bounded orphan-G
   bootstrap under #337's separately authorized install gate.
3. Under a distinct Human-approved **disposable one-Issue G2/G3 packet**,
   enable only exact approved operations on the private
   `upiscium/kagari-v2-oneissue-e2e` fixture. Its existing historical
   Ruleset ID `26750190` may remain for fixture audit, but is **not a
   required KUMIKI installation setting or ordinary-branch acceptance gate**.
   GitHub-specific `updateRefs` is one candidate transport: its expected-old
   two-ref semantics require a separately approved real canary and are
   **not** guaranteed by ordinary Git. Do not assume N=1 before GitHub
   returns the Issue number; do not modify the original R15 fixture.
4. #291 v2 model start and #292 Human Ask continuation are later,
   independent gates. No GitHub App onboarding.

## R31 bounded correction — direct Issue Tree binding

The R30 Writer's structural bundle traversal accepted a syntactically
valid foreign `tasks/8/record` tree under an Issue `7` CAS Update,
if the trusted effect/grant/intent callbacks were artificially supplied.
The canonical upstream #337 Publisher has semantic admission, so this
isolated direct-callback demonstration **does not prove a production
cross-Issue exploit**. It did expose a missing defensive boundary in
the new Host transport.

R31 added an exact `tasks/<update.task>/record` path check,
rejected any other `tasks/*` path and required the canonical 64-hex
record pointer followed by LF **before any Blob/Tree/Commit upload**.
It does not duplicate or replace #337's full semantic Metadata graph
validator; that validator, actual Task/Contract/Worktree grants, and
original #140 journal remain mandatory upstream.

Reproduction before correction:
`P2_REPRODUCED_FOREIGN_ISSUE_TREE_ACCEPTED ['tasks/8/record']`.
R31 corrected deterministic tests: foreign, mixed and malformed
Issue trees fail before the first injected effect. Full CLI-focused
suite: **40/40 PASS** (previous R30: 37/37).

**Independent external review not yet qualified:** OpenCode's explicit
hosted and local model connectivity tests both timed out; Claude CLI
reported expired OAuth and failed to authenticate. These are tooling
blockers, not evidence of source acceptance. R31 must not Commit/Push/
Draft PR until an independent source security/correctness review is
recovered or a later Human explicitly accepts that residual risk.

## R32 correction — canonical Metadata envelopes at the Writer boundary

A second source-only direct-callback repro found that R31 would
accept a SHA-valid `objects/evidence/...` Git Blob whose decoded
Metadata task was Issue 8 while the proposed CAS ref was Issue 7.
The Task Record pointer already matched Issue 7, so R31's
`tasks/N/record` path guard alone did not catch this.

R32 reuses **existing** `components/agent-core-v4/metadata_codec.py`
at the Issue CAS Writer boundary, before uploading any Git object:
- Require the selected Issue's canonical 64-hex+LF Record pointer to
  name a `task-record` object present in the *same proposed tree*.
- Decode every `objects/...json` envelope and bind its canonical
  content digest, repository, Issue number and kind to the exact tree
  path. Reject foreign Issue/repository objects, changed path digest,
  wrong kind and invalid canonical JSON with typed refusals.
- Retain #337's independent full Task/Contract/Record/Evidence graph
  validation and #140 durable original intent/grant. This is a
  defensive check within the existing Host boundary, not a new
  authentication system or separate Task authority.

Pre-fix synthetic reproduction logged under R32:
`P2_REPRODUCED_FOREIGN_ISSUE_ENVELOPE_ADMITTED`.
The fix leaves the GitHub App dependency rejected and does not
authorize any real GitHub write or model execution.

## R32 independent-review correction of exact source candidate

Two independent OpenCode read-only source reviewers used a *restricted
no-subagent/no-skill/no-Bash/no-edit agent* on separate SHA-identical
clones. Initial security verdict: **SOURCE ACCEPT** (scope-limited).
Initial correctness verdict: **CHANGES REQUIRED**, three concrete
typed-contract findings and an inadequate prepopulated fake readback.

The following corrections retain the same ambient `gh` authentication
and add no standalone credential or approval service:

- GitHub REST Tree entry names and Commit messages with unpaired
  Unicode surrogates now raise `CliReadRefused`, not uncaught
  `UnicodeEncodeError`.
- REST repository `id` and Issue `number` use exact Python integer
  type checks; JSON `true` can no longer equal expected integer `1`.
- Invalid/noncanonical Issue numbers and malformed `Bundle.objects`
  entry shapes now produce typed `CliWriterRefused` before an effect.
- The fake GitHub Host no longer supplies pending remote Git objects
  before a POST. It makes exact Blob, Tree and Commit responses
  readable only after the matching synthetic upload operation;
  positive CAS tests therefore verify actual POST-first readback order
  and missing-ACK still never repeats effects.

Focused suite after these corrections: **46/46 PASS**, including the
R31 and R32 cross-Issue adversarial tests. Exact latest final review
must be repeated, because a review of the earlier candidate cannot
authorize publishing modified bytes. Live GitHub ref writes remain
unauthorized and NOT_RUN.

## R34 — exact-published-head PR #383 Correctness correction (source-only)

PR #383 HEAD `ef099a0c0965bbde7bfd80a82627f9eef7832c1c`
passed exact GitHub CI 16/16 checks but independent PR-mode
Correctness returned CHANGES REQUIRED (one P1, two P2). This R34
correction is **isolated local source only**, not a GitHub publication.

- **Host-bound principal:** `GhIssueCASWriter(..., expected_actor_id=...)`
  now requires a positive non-boolean numeric identity supplied by the
  trusted Main/Host during admission. It is *never* silently derived
  from an updated ambient `gh` login. Both before and after Host
  callbacks, before the first Blob effect, before CAS after object
  upload, and during post-CAS readback the actor must match the
  original admitted value. This is normal CLI login reuse, **not**
  a GitHub App or additional credential management system.
- **Task base vs current `main`:** `registered.base_revision` is
  the immutable Issue/Contract/Worktree subject of the upstream
  #291/#337 trusted admission. Ordinary unrelated Merge Commit
  advancement of `refs/heads/main` does *not* revoke an otherwise
  current Issue-scoped Metadata operation. The Writer rechecks
  exact Issue/Archive refs and Host grant/intent, **not** an
  unrelated default-branch tip. The old R30 statement claiming
  a raw Main HEAD stability check was incorrect and is withdrawn.
- **Failure classification:** A trusted `authorize` or
  `original_intent` callback raising an ordinary Exception before
  any upload now becomes `CliWriterRefused`. If it happens *after*
  object transfer, the existing upper guard converts that unknown
  state to `CliWriterUncertain`. No blind rerun, new nonce,
  write bypass, credential export, or success on ambiguous ACK.
- **Negative tests:** CLI actor switch before first effect, between
  calls, during grant, after uploads, or after CAS; callback failure
  before and after upload; invalid expected actor; independent
  `main` advancement while Issue/Contract/grant remain valid.

Independent review of the exact R34 bytes and separate Human
authorization are still needed before a correction Commit/Push to
the existing Draft PR #383. No Ready/Merge, Archive deletion,
real GitHub CAS, model Task/Ask or activation is authorized.

### R34 staged-upload deadline classification — independent Security P2

The first R34 Security source review found an additional P2: the
internal `_post` pre-call deadline/capability/size checks could raise
`CliWriterRefused` for a second or later POST despite prior GitHub
object upload effects already having entered the transport.

Each `Writer.__call__` now owns an invocation-local `effects_started`
marker. It is set immediately before the first effect runner call,
and never carried into a different operation. A pre-call failure
before the first effect remains a typed `CliWriterRefused`, while
any later deadline/capability/size pre-call failure becomes
`CliWriterUncertain`. The original fsynced intent remains the
sole recovery authority; this change does not retry, roll back or
mint a different candidate.

Deterministic regression checks both direct pre-call classifications
and injects deadline expiration **after the first successful Blob
POST and GET readback**, before the next Blob POST. The result must be
`CliWriterUncertain`, with exactly one synthetic effect call and
no CAS mutation. This does not attest actual GitHub lost-ACK behavior.

## R35 — invocation-bound existing GitHub CLI credential (source-only)

The independently reviewed, published R34 PR #383 source remained
**CHANGES REQUIRED** for one Security P2: a process observing CLI actor A
before and after an effect could miss a concurrent A→B→A switch of the
ambient `gh auth` selection. It could falsely report a successful
effect under A although a Blob or GitHub GraphQL CAS ran as B.
R34's sampled `expected_actor_id` is necessary but not sufficient.

R35 introduces `tools/kagari_metadata_v2_cli_session.py`, an
**invocation-scoped adapter to the already installed CLI login**.
The trusted Host must:

1. Supply the previously admitted numeric GitHub actor ID and exact
   repository identity, and call `GhCliSession.capture(repo, actor_id, deadline)`.
   That method runs `gh auth token --hostname github.com` **once**
   without login/refresh/installation and without printing or persisting
   stdout. It verifies `gh api user` with *that captured credential*,
   using the documented `GH_TOKEN` priority over ambient `gh auth`.
2. Use **the same concrete** `GhCliSession` in
   `GhReadOnlyHost(..., session=session)` and
   `GhIssueCASWriter(..., expected_actor_id=actor_id, session=session,
   authorize=..., original_intent=...)`.
   The Writer refuses any separate effect callback or a different
   credential session: only `session.effect` is accepted, and the
   Host's read runner must be `session.read`. A session without
   current Host grants, original durable Intent and exact OID proof
   still grants NO Metadata write authority.
3. Every GET, object upload and GraphQL `updateRefs` runs a subprocess
   with a fresh sanitized environment containing **the same**
   `GH_TOKEN` and `GH_HOST=github.com`. There is no token in
   argv, source, Task/model prompts, Metadata objects, journals,
   GitHub comments, or logged stderr. A concurrent `gh auth switch`
   affects **subsequent invocation capture**, not this operation's
   API credential. Host must close the session after the bounded
   operation; Python cannot guarantee secure memory zeroization.
4. Fail closed if the existing CLI is unauthenticated, token capture
   fails, the bound `gh api user` disagrees with the admitted actor,
   the session closes or expires, or the response is unavailable.
   After an object upload, the existing `CliWriterUncertain`
   handling and original #140 journal reconciliation remain required.
   A successful captured actor observation is not a substitute for
   #291/#337 current Task/Contract/Worktree/Attachment authority.

R35 isolated tests reproduce an ambient A→B→A selection change
between read and effect and during the effect sequence. The in-memory
runner records all Blob/Tree/Commit and GraphQL invocations as actor
**A**, the captured user, regardless of the ambient selection B.
Foreign-host/repo API commands, malformed tokens, cross-principal
capture, missing session and post-close attempts are rejected.
The underlying CLI is unchanged: this is **not** GitHub App onboarding,
a PAT request, a token-refresh daemon, a credential database,
an auth proxy, or a second GitHub account.

**Scope limit:** This is a source-only transport proof. The real
Host-owned object and Writer effect callbacks have NOT been activated,
and no GitHub Metadata ref, Issue, Product PR, Archive cleanup or
native model/Ask mutation is authorized. Real GitHub two-ref CAS,
stale-beforeOid rejection and remote lost-ACK verification remain
separate Human-approved disposable canary gates. Source Commit/Push to
Draft PR #383 requires an explicit later Human approval, with independent
security/correctness review of the exact R35 bytes first.

### R35 independent Security P2 — token capture must be memory-only

The first R35 independent Security review found that ordinary
`tempfile.TemporaryFile()` stdout used by the generic API reader
could put plaintext output of `gh auth token` into an unnamed but
potentially disk-backed file. That was inconsistent with the accepted
**in-memory only** capture contract.

The final R35 implementation routes *only credential capture* through
a dedicated `subprocess.Popen(stdout=PIPE)` bounded **OS memory pipe**
reader, enforced at **4098 output bytes** and under the same original
deadline. Any overflow, timeout or subprocess failure triggers cleanup
including termination and reaping of the child, without disclosing
stderr. The token remains exclusively in Host-owned Python memory and
the private subprocess `GH_TOKEN` environment; no token bytes are
placed in temporary files, local Git config, argv, journal, Task
messages, GitHub Issue/PR comments or source.

Ordinary **noncredential REST GET** response decoding still uses the
existing bounded 2 MiB temporary output facility, which does not
receive `gh auth token` stdout. Production capture pipe tests use a
synthetic child with an OS pipe, assert `stdout=subprocess.PIPE`
and fail if the generic `TemporaryFile` is invoked. Additional tests
cover oversized token output and bounded timeout child cleanup. The
same corrected source completed a real Adam `gh` login-bound
**GET-only** preflight without exposing the token.

The private Python process cannot promise perfect zeroization of
immutable strings or protect against a same-user hostile process
inspecting child environments; these are not the accepted KUMIKI
single-operator threat model. No new credential service is needed.
