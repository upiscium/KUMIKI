# KAGARI Metadata v2 — Phase 3 disposable GitHub verification (design R0)

**Status:** Design-only, source-only KUMIKI #337 candidate. Not installed, not
activated, not a GitHub remote test, not permission for any external write.
Baseline: merged #337 R9 PR #369 on `main`, SHA-1 Git source-only adapters
and synthetic Host/RecoveryJournal tests. Do not touch v1 GA or legacy metadata.

## Decision and hard prerequisites

Phase 3 must use one **separately approved disposable GitHub repository**,
not `upiscium/KUMIKI` or another production repository. The approved scope
must freeze numeric repository ID + name, baseline default-branch OID,
source/runner revision, exact ref allowlist, actor IDs and permissions, and
operation/time/output bounds **before** any real effect. A typed callback
or a successful bare Git fixture is not an authenticated Host or server CAS.

Current gaps (BLOCKED, do not silently assume available):
- `ArchiveHost` in `tools/kagari_metadata_v2_archive_host.py` is source-only,
  needs a verified v2 Main/Session owner and quiescence authority. Its writer
  is a Host callback, not real GitHub expected-old CAS.
- The #194 v2 checkpoint PR-comment publisher has **synthetic** Host
  observation, lease and journal only. Real author, complete comment inventory,
  effect-time authorization, readback and original comment-ID retention
  remain to be qualified.
- **Expected-old conditional deletion of a remote Issue Metadata ref has no
  qualified Phase 2D adapter** in the current #337 source. Existing #198
  *local Task resource cleanup* is NOT authorization for Metadata ref deletion.
  Phase 3 deletion is blocked until that source-only adapter, its independent
  reviews and an explicit destructive test approval exist.
- Disposable repo selection, credential/actor boundaries, branch Rulesets
  and original comment-body attestation are not proven by this design.

## Staged authority gates

| Gate | Permitted scope after separate Human approval | Positive completion evidence |
| --- | --- | --- |
| P3A | This document, source-only harness / receipt-schema design and offline review | No remote effect; pinned source SHA; independent correctness/security review |
| P3B0 | Read-only **named disposable repo** preflight | Numeric repo ID, permissions, effective active rules and full bypass inventory, exact ref match and initial OIDs |
| P3B1 | Disposable-only Ruleset installation/attestation | Active exact ref-targeted retention + writer scope and complete bypass inventory; **no branch creation or J push required yet** |
| P3B2 | Disposable refs, Draft PR/checkpoint, terminality, archive, race + protection canary | Two Issue histories; checkpoint original IDs **before** terminal archive; valid J1/J2; actual server-side delete/non-FF denial, C-write race block, unknown-ACK cross-key recovery |
| P3C | **Additional destructive approval**, only after Phase 2D source qualification | Conditional expected-old Issue-ref deletion; stale-C refusal; protected archive and exact original OIDs retained |
| P3D | Independently audited clean-clone final acceptance | Original Git objects, Record/Contract/Snapshot, checkpoint history readable after permitted deletion; v1 unchanged and default/Product refs matching **approved expected deltas** |

P3B2 success never grants P3C. P3D never grants Phase 4 activation.
A valid two-parent Archive append is **not** a P3B1 prerequisite; P3B2
owns the synthetic ref/bootstrap and terminality prerequisites.

## Effective Ruleset and writer design

Protected Archive ref is `refs/heads/kagari/metadata`; active Issue refs
are `refs/heads/kagari-metadata/<canonical_issue_number>`.
The Archive **must accept true two-parent Merge Commit J** via ordinary
append-only push. Do not apply GitHub's `required_linear_history` rule:
it rejects merge commits. Do not impose PR-only transport requirements on
a direct-ref append writer unless positively verified compatible.

Recommended **two separate Rulesets to test**, not claim deployed:

1. Archive *retention*: exact archive ref target with `deletion` and
   `non_fast_forward`, `enforcement=active`. The Archive writer must
   **not** bypass this Ruleset. Explicitly classify any admin/owner/bypass
   actors outside the approved threat model; an unobserved bypass means
   the retention/deletion gate is BLOCKED.
2. Archive *write restriction*, if needed: a distinct exact-target
   `creation` / `update` Ruleset with the approved bootstrap/append actor
   allowed only through that Ruleset's explicit writer/bypass policy.
   Prove that this actor's access does not bypass the retention Ruleset.
   A separate Issue-ref scope may authorize future conditional deletion,
   but must not include the protected Archive ref.

Read and hash **all** effective active repo/org Rulesets applying to the
selected archive ref, their IDs, enforcement, match/exclude patterns,
source, exact rule types, and bypass actors. GitHub's
`GET /repos/{owner}/{repo}/rules/branches/{branch}` exposes effective
active rules (including organization rules); a privileged ruleset-detail
read is needed for complete bypass attestations, since read-only API
responses may omit bypass actors. `evaluate` / `disabled` is insufficient.
Patterns use GitHub `fnmatch`; `*` does not span a `/`.
Layered Rulesets and classic branch protection must be considered together.

**P3B1 alone** qualifies configuration and effective policy for a ref that
may not yet exist. **Only after separately authorized P3B2 creates the
disposable Archive/Issue refs**, test *both* writer and an independent
non-bypass actor: Archive delete denied and non-FF rewrite denied by the
**server**, with original Archive OID/ancestry unchanged. The positive
two-parent J append must also succeed under the *same* retention
policy. A client hook denial is not proof. If a prohibited attempt
unexpectedly succeeds, stop immediately, preserve independent original
refs/object backup, report FAIL and do not proceed to Issue ref deletion.
Do not make an unsupported claim that a privileged owner is protected.

## Data-origin and egress boundary — before **any** object transfer

Only **independently generated synthetic** Contract/Issue descriptions,
Record/Evidence/Snapshot content, PR bodies/comments, Git objects and Product
history are permitted in the disposable GitHub repository. No production
Issue/PR/Contract/Evidence/Snapshot, vault, Git object, credential, fixture
log, attached file, or user data may be read out and sent to the canary,
even if it is not a token. The allowlisted synthetic corpus must have pinned
content hashes and a clean provenance receipt before the *first* Git object
transfer, ref creation or comment POST. Missing provenance blocks the effect.

Preapprove repository visibility (public/private), exact outbound content
allowlist, synthetic Product history and Human-owned test decision, intended
actors, and where evidence/logs/receipts are stored and who may read them.
Use independently scoped canary credentials, never write tokens to receipts.
Do not assume a private repo is secret-safe if its collaborators or Actions
can disclose content. Redact sensitive callback arguments and CI logs.

## P3B0/P3B2 canary evidence and bounded operation order

1. **P3B0 read-only preflight.** Confirm approved disposable repo numeric
   ID/name, SHA-1 format, baseline default/v1 OIDs, exact existing refs,
   actor permissions, visibility/data provenance, active Rulesets and limits.
   Unexplained refs, unapproved outbound data or unreadable bypass proof STOP.
2. **P3B2 bootstrap + Issue writer (after separate approval).** Install/adopt
   the orphan Archive genesis through the qualified Host. Register **two**
   canonical disposable Issue refs from that exact genesis, not aggregate
   latest; publish their first and subsequent typed Metadata with normal
   expected-old fast-forward writes. Reject wrong repo ID/Issue, foreign
   paths, Product ref, symbolic ref, changed C or invalid object graph.
3. **Draft PR/checkpoint FIRST, before terminality.** Prepare independently
   generated synthetic Product branches and Draft PRs with exact Subject,
   base and current Task approval. Under qualified Host PR facts and explicit
   checkpoint grant, publish initial/turn comments **while PR is open/Draft**,
   freeze immutable metadata commit/Snapshot/Record/Contract and exact
   original comment IDs/author/body digests. A moved PR, conflicting owned
   key or foreign author fails closed. Lost ACK reconciles the one original
   comment without repost. **Once the Product PR has merged/closed, there
   are no further Draft-only v2 checkpoint POSTs**; historical reader only.
   Under the held exclusive stream lease, enumerate **all durable intents
   across checkpoint keys**. Inject ACK loss leaving a different key's
   comment unobserved, restart, and prove another-key publication is
   refused with zero additional POSTs until positive full reconciliation.
4. **Human terminality AFTER checkpoints.** First exercise a separately
   approved synthetic `CANCELLED` or `SUPERSEDED` route with all Product work
   positively preserved and no active Session; this can keep default/Product
   refs unchanged. A distinct `MERGED_PR` route requires an independently
   authorized disposable Product PR merge and full merge ancestry proof.
   Its **explicitly approved** default/Product ref delta is part of the
   baseline/receipt, not an unexpected mutation. A closed-unmerged PR,
   ambiguous Human decision, active Session or unpublished Product work
   never qualifies.
5. **Archive J1 effect and C-race authority.** Verify current A and active
   Issue C1, terminality and original metadata/checkpoints. Acquire and
   **hold** the qualified Host-exclusive quiescence/Issue writer lease
   through candidate assembly, policy/grant, Git object transfer, effect
   and final readback. Writer checks expected-old A **and unchanged C1**
   again at the effect boundary. For non-cooperating C' write attempts,
   demonstrate authenticated Issue-ref writer scope rejects them while
   the original exclusive owner is held. A mere pre-effect GET of C is
   not atomic cross-ref CAS. If C can advance by an actor in the threat
   model without detection and exclusion, Archive acceptance is BLOCKED.
   Build validated union J1(A,C1) with exactly two parents and ordinary
   non-force Archive append; independently verify original A,C1, tree,
   reachability, Product isolation and terminal facts after readback.
6. **Racing archive, retention and unknown ACK.** Two distinct Issue
   operations starting from A race: stale A update must fail; an approved
   later J2(J1,C2) may follow only after earlier journals are positively
   reconciled. Test server-side Archive delete/non-FF refusals by the
   actual Archive append writer and independent non-bypass actor **now that
   the Archive ref exists**, under active B1 Rulesets. Test valid normal
   two-parent append under the same effective protection. A callback fault
   may drop ACK before/after one disposable remote effect; persist original
   candidate and exact readback, never new nonce/repost while UNKNOWN.
7. **P3C conditional-delete gate, separately authorized.** Require
   source-qualified Phase 2D expected-old deletion, protected Archive
   retention, C reachability, stopped owner, Product preservation and
   valid Human terminality. Intentionally race a disposable Issue C→C',
   then attempt stale-C conditional deletion; server must reject and C'
   remain. Git transport `--force-with-lease=<fullref>:<expected_oid>` is
   only a candidate before GitHub verification; generic REST DELETE has
   no documented expected-old precondition. **Any prohibited deletion
   success OR unknown outcome freezes all further mutations**: preserve
   original C' Git objects in an independent backup, immutable journal,
   remote observations/server response; report FAIL or UNKNOWN. Do NOT
   auto-restore/recreate/retry. Recovery needs separate Human approval.
8. **P3D fresh independent clone.** After any positively authorized
   deletion, prove original A,C1,C2,J1,J2 OIDs, true two-parent history,
   immutable Task Record/Contract/Evidence/Snapshot and the **pre-terminal**
   PR comment originals/IDs are still readable without latest-tip
   substitution. Legacy v1 remains unchanged. Default/Product refs must
   match the **explicitly approved expected delta** (zero for a route
   without Product merge); unexpected change is FAIL.

## Negative and fault matrix

| Fault / attack | Expected result |
| --- | --- |
| Archive Ruleset missing, disabled, ref pattern mismatch, or bypass unreadable | BLOCKED before deletion or archival qualification |
| Writer/outsider attempts Archive delete or non-FF rewrite | Server rejects, Archive original OID retained |
| Archive requires linear history or PR-only write against true J | Incompatible; do not weaken retention to manufacture success |
| Stale Archive A, competing writer wins | Stale CAS refused; retained J1; full reconciliation before new approval |
| Partial bootstrap/Issue write or unknown ACK | Journal UNKNOWN; exact original receipt readback; no blind retry |
| Other checkpoint key has an UNKNOWN durable intent after restart | Held stream lease refuses new-key POST; zero additional posts until positive reconciliation |
| C advances after Archive precheck but before J push | Host effect-time C recheck plus qualified exclusive Issue writer boundary block J; otherwise B2 BLOCKED |
| Issue C advanced before conditional expected-old delete | Ref C' remains; stale delete rejected |
| Stale-C delete unexpectedly succeeds or gives unknown result | Freeze further mutations, independently retain C' objects+journal+server evidence; no automatic restoration |
| Missing Session quiescence, terminal Human facts or Product preservation | Archive and delete refused pre-effect |
| PR head moved, author/body spoof or competing owned comment key | Refused/uncertain according to effect phase; original ID preserved |
| Wrong repo ID, symbolic ref, wrong DAG/tree or unauthorized ref | Refuse without mutation |
| Non-synthetic source data, unknown outbound visibility/storage or unscreened evidence | BLOCKED before first object transfer/comment POST |
| Expired I/O deadline, incomplete comment inventory or unbounded object payload | Fail closed, no partial success |
| Any v1 ref or unapproved Product/default ref delta | FAIL; preserve evidence and stop |

No destructive negative attempt is authorized by this document. All such
tests require an explicit disposable ref+actor approval and independently
pinned original object backup. Production KUMIKI is never a target.
For **any prohibited destructive effect that succeeds OR has UNKNOWN
outcome**, freeze every subsequent mutation. Retain the last known
original and contender C' object graph, journal, remote ref observations,
actor identity and server response; report FAIL/UNKNOWN. Do not
auto-restore, recreate, rewrite or retry. Forensics/recovery needs its
own bounded Human approval.

## Audit manifest and final verdict

Produce one bounded immutable-digest record per case with:
experiment ID; approved repository ID/name, actor role/ID (not token),
source and runner SHA; approved exact ref allowlist; repository visibility,
outbound **synthetic corpus provenance/hash** and approved evidence storage
destination/reader roles; initial and **approved expected** default/Product
ref deltas and exact pre/post v1 refs; orphan genesis; original A,C1,C2,J1,J2,
parent/tree OIDs; expected-old and candidate refs, complete cross-key journal
inventory and ACK state; Task/Contract/Record/Snapshot exact IDs, initial/
turn original comment IDs/body SHA-256; Ruleset ID/source/enforcement/ref
match/complete bypass inventory; method/operation and effective server
response including unexpected effect/UNKNOWN; approved Human terminality
route; original and competing C' backup OIDs; deadlines/timestamps;
source SHA and log/manifest hashes; residual ref inventory and preservation
result. No secrets, production content or speculative success from UNKNOWN.

Every acceptance item has a distinct `PASS`, `FAIL`, `BLOCKED` or
`NOT_RUN` result plus immutable evidence link/hash. A clean local fixture
cannot replace the disposable GitHub server proof. An unreadable Ruleset
bypass inventory or missing source-qualified conditional deleter keeps P3C
and P3D **BLOCKED** even if the non-destructive canary passes.

## #337 acceptance traceability (source of truth: Issue AC-META2-01..11)

The Phase 3 evidence manifest must carry **one row per Issue acceptance
criterion**, with positive/negative test IDs, exact gate, observed server
facts, byte/OID provenance, and PASS / FAIL / BLOCKED / NOT_RUN. At this
R0 design-only point **all real GitHub effects are NOT_RUN**, while later
gates that require missing Phase 2D deletion and qualified Host remain
BLOCKED. Passing source-only unit tests is not acceptance.

| #337 AC | Minimum positive / negative proof | Gate(s) | At R0 |
| --- | --- | --- | --- |
| AC-META2-01 | Install-only orphan, exact idempotent ref adoption, foreign/symbolic refuse; archive no-delete/non-FF server enforced; zero unexpected Product/default change | B1,B2 | BLOCKED |
| AC-META2-02 | Two distinct active Issue refs from **genesis**, Task/Contract/Worktree binding, no inherited other Issue Record or cross-issue write | B2 | NOT_RUN |
| AC-META2-03 | Exact immutable object/Record payload; wrong Subject/JSON/hash/path/symlink rejected before effect | B2 | NOT_RUN |
| AC-META2-04 | True two-parent J with exact C ancestry and union; reject Product parent/invalid tree/OID/over-budget DAG; preserve original after deletion | B2,C,D | BLOCKED |
| AC-META2-05 | Competing A expected-old writer rejected, compatible new validated J2, incompatible/duplicate Issue refused | B2 | BLOCKED |
| AC-META2-06 | Worktree-only/ref-only, before/after push lost ACK, archive/no-delete, deletion lost ACK, changed C, post-effect drift; no blind retry | B2,C | BLOCKED |
| AC-META2-07 | Fresh clone after approved Issue ref deletion/Worktree removal reads exact old metadata/Snapshot/Contract/Evidence and Product Subject | C,D | BLOCKED |
| AC-META2-08 | Active effective Ruleset/bypass + server negative deletion/force + ref count only after retention; no unapproved ref touch | B1,B2,C,D | BLOCKED |
| AC-META2-09 | Current Record no disposition for authorized MERGED_PR or Human-owned cancelled/superseded; positive merge/preservation/quiescence; reject closed-unmerged and reopened Issue | B2 | BLOCKED |
| AC-META2-10 | Real disposable two-Issue PR/actor/race/Ruleset/conditional-delete positive and negative runs with independent review | B2,C,D | BLOCKED |
| AC-META2-11 | Synthetic legacy v1 fixture and old reader/refs remain retrievable without mutation; actual GA/v1 compatibility and any opt-in migration need separate Phase 4 qualification | D,Phase 4 | BLOCKED |

If an AC needs a server feature or producer not yet qualified, leave it
BLOCKED rather than narrowing its claim without Human scope. For the
MERGED_PR route, include the **approved** disposable default/Product delta
in AC01/09/11 evidence; no change to default may be assumed for that route.
Historical PR checkpoint comments must exist **before** Product terminality
and remain retrievable after the Archive/optional Issue deletion. Do not
claim v1 compatibility merely because a newly created disposable repo
lacks legacy refs; a distinct authorized synthetic v1 history/read-only
compatibility canary or preserved legacy reader proof is required.

## Approval boundary / next step

- **Now:** design-only, locally staged for independent review, no Commit,
  Push, PR, Ruleset, live ref/comment, delete, install or activation.
- **Next possible source-only gate:** inert Phase 3 evidence collector,
  capability/actor verifier, and disposable runner interface design, all
  without credentials or external effects.
- **Later external gates:** choose one named disposable repository,
  authorize read-only P3B0; then separately authorize Ruleset creation,
  disposable canary writes/negative attempts and exact conditional deletion.
- **Phase 4:** separate Human authorization for one named, versioned
  disposable consumer only after external gates. Do not auto-upgrade GA.
  Keep Issue #337 Open.

### Public interface references

GitHub Docs: REST "Get rules for a branch", Ruleset API, Available Rules,
Creating Rulesets (including fnmatch/bypass), REST Git References DELETE.
Git documentation: explicit `--force-with-lease=<refname>:<expect>` semantics.
These references describe potential interfaces, **not execution approval**.
