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
- Constructor requires the identical R29 read Host and R30 ObjectReader.
  The `effect` transport, `authorize` grant and `original_intent`
  verification callbacks have **NO DEFAULT**. Without all three supplied by
  trusted Main/Host it fails before any remote POST. They are not Python
  constructor credentials or permissions transferable to a model.
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
- Recheck actor, repository, existing Main HEAD and exact Issue/Archive
  direct refs and independent Host grant **again after object upload**.
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
