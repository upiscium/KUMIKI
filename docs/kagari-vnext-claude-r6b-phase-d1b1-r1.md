# #340 R6B D1B1 R1 — synthetic read-only Host binding shapes

**SOURCE-ONLY / UNTRUSTED SYNTHETIC / NON-WIRED / NO AUTHORITY.**
Original R1 development baseline: isolated remote-free
`main@62e3a363bb573e12e378297c5bc34d329bc1e338`.
Source publication: Draft PR #385, based on
`main@02c26d26c7656380dacb854ab1cde726f6f6b579`.
The original published HEAD at the time of independent PR-mode review was
`b259e1f5f91433849deb2bd37aa07547f573f071`; any later documentation
correction has its own review and publication lifecycle.
The combined source-only PR adds two R0 files and three R1 files, with
no changes to pre-existing tracked files. The initial R1 candidates were
drafted by the supervising assistant; GPT-6 Astra max subsequently modified
the R1 module and tests to reject contradictory synthetic journal evidence.
The documentation's review corrections were applied separately. No claim is
made that Astra max authored the entire artifact.

## Scope and reason

D1B0 modeled an independent monotonic anchor; R0 evaluated reuse of the
existing `RecoveryJournal` under a **narrower cooperative one-host profile**.
This R1 provides only a pure observation/identity contract:
`tools/_kagari_vnext_claude_r6b_phase_d1b1_r1.py`.
There are no file operations, directory discovery, lock acquisition, Git,
network, authentication, subprocess, Claude CLI, Main/Lane/Transport imports,
bootstrap or journal `read/create` calls in the module. It cannot execute,
allocate, authorize, reconcile, repair, retry or complete Tasks.

**Every input is caller-created and UNTRUSTED**, even if it has a name like
`SyntheticMainObservation` or `SyntheticBinding`. Python dataclass
types, exact hashes and a claimed held owner do not prove a live
`MainSupervisor`, authentic repository ID, fixed root, full history,
durability or permission. The module never returns a root path or token.
It is not a security boundary against malicious in-process Python input.

## Exact test-only identity contract

- Numeric `repository_id`: exact `int` in `1..10**12`; `issue`: exact
  `int` in `1..2**31-1`. Reject bool, string, floats and overflow.
- Source-fixed generation `ONE_SHOT_V1`. The R1 profile label is fixed to
  `r6b-d1b1-r1`, checked for equality but cannot rekey a reservation.
- `canonical(v)` = UTF-8/ASCII `json.dumps(v,sort_keys=True,
  separators=(",",":"),ensure_ascii=True,allow_nan=False).encode("ascii")`.
- `KEY_DOMAIN=b"kagari/claude/r6b-d1b1/reservation/v1"+bytes([10])`.
  Reservation key = lowercase hexadecimal SHA-256 of domain followed by
  canonical `[repository_id,issue,"ONE_SHOT_V1"]`.
- `ROOT_DOMAIN=b"kagari/claude/r6b-d1b1/root/v1"+bytes([10])`.
  Root component = lowercase hexadecimal SHA-256 of domain followed by
  canonical `[repository_id,issue]`.
- Both are **synthetic opaque components**, not a full directory path, an
  authenticated identifier or an implementation of a Host routing registry.
  Clone, Worktree, repository name/alias, Task attempt, session UUID,
  timestamp, project branch and policy cannot change them.

Golden vectors:

| Pair | Reservation key | Root component |
| --- | --- | --- |
| (1,23) | `aa2647fe3149f893fbdb79f69da741d6d5d82b017f8a9d94548c2ded8fb91125` | `ceb181a843e9934fcb57908b832382937e549f87c807624315308b8ce14130c8` |
| (12,3) | `4608be10c94a3aa9aeaa23053e391af9689c3eeda5b9a5cf1bea5c679e31a3c2` | `8291ffac23c550353f7e0230be3f5ecbabb3811a21fd125d07285905e4e77e39` |

## Closed observational outcomes

`observe_synthetic_host_binding(binding, selected, journal)` compares
exact synthetic shapes: selected numerical repo ID, Issue, current
repository-name spelling (diagnostic only), Contract and Task digest,
claimed owner state, claimed root component, and claimed reservation state.

| Status | Meaning |
| --- | --- |
| `REFUSED` | Invalid/mismatched Task, profile, selected name or owner claim, malformed journal shape, unsafe or conflicting root |
| `UNKNOWN` | Missing root or record, missing journal observation, partial/ambiguous history or unexpected malformed-object exception |
| `OBSERVED_BLOCKED` | A supplied matching reservation/result claim is retained and **must not be replayed** |

Every result has `qualification=UNQUALIFIED`, `dispatch_allowed=False`,
`task_completed=False`, `replay_allowed=False`, `commit_allowed=False`,
`authenticated_absence=False`, `effective_policy=UNVERIFIED`.
No result is a capability or reusable grant. These are mutable diagnostic
dicts and **are not to be consumed as authoritative Host evidence**.

A changed name with the same numeric ID does **not** alter the key. Mismatch
with the separately supplied selected Main name is refused, but this is
not real rename/transfer detection. The existing Main's repository-name
flock may still split after an actual rename; the future Host must address
it before wiring. R1 has NO live GitHub lookup and NO independent provenance.

## First-use and lost-history limitations

Neither a freshly provisioned empty directory nor a locally deleted,
recreated or rolled-back directory can be authenticated by this module.
`ABSENT` always returns `UNKNOWN`, not proof of virgin genesis or permission
to start. A same-UID process can delete a real `RecoveryJournal` record;
different roots can independently create the same key. No at-most-once or
exactly-once guarantee is claimed, and no filesystem crash/power-loss
qualification was performed.

The existing `_host_state_home()` and `_host_repo_lock()` helpers can
create directories, so R1 does **not** import/call them as read-only.
Future Host bindings must derive an existing private state root via trusted
OS ownership/dirfd-safe reads, keep numeric ID+Issue as the stable identity,
and implement separately authorized provisioning/genesis with evidence that
is not accidentally recreated on missing data. The current source does
not implement that, or a production CAS/lock, or independent rollback trust.

Real Claude execution also still needs independent exact CLI binary/version,
resolved policy/settings/hooks/MCP/plugins/tools/account/billing verification,
live Main/Issue/Task/Worktree checks, external effect-boundary rechecks and
explicit Human Task-launch approval. Main's existing
`CLAUDE_EXECUTOR_UNQUALIFIED` guard and OpenCode default are unchanged.
Ask/resume and arbitrary tool permissions remain out of scope.

## Validation boundaries

The isolated Python unittest tests golden digest vectors, numeric types,
renames, ambiguous histories, invalid/mismatched claims and permanent
non-grants. A static source test rejects Main/transport/Host imports and
filesystem/process/network effect operations in this module. This is
synthetic correctness evidence **only**, not an independent authority,
crash, credential or model security qualification. The original PR #385
published HEAD `b259e1f5f91433849deb2bd37aa07547f573f071`
(before this documentation amendment) received independent read-only
security and correctness reviews reporting no confirmed P0–P2 findings;
these are not formal GitHub approvals and do not cover this amendment.
Any further source change requires its own exact-content review and a
fresh exact-published-HEAD check after publication.

```sh
NIX_CONFIG='eval-cache = false' PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_d1b1_r1
```

**STOP:** No R1 Main/Lane/Transport wiring, no Host state changes, no
journal root creation, no real Task and no executor activation. The five
source-only R0/R1 files have been published in **Draft PR #385 only**;
the PR remains unmerged and Issue #340 remains Open. No Ready/Merge,
deployment or runtime activation is authorized by this publication.
