# #340 R6B Phase D1B R0 — pure protocol shapes and crash windows

**SOURCE-ONLY / SYNTHETIC / NON-WIRED / UNQUALIFIED. NOT activation.**
Base: remote-free isolated `main` at
`edd39b7ff891e6d57db7ecaecd28713bbd6ab51e`.
Only this document, `tools/_kagari_vnext_claude_r6b_phase_d1b0.py`, and
`tests/test_kagari_vnext_claude_r6b_phase_d1b0.py` are new. Existing tracked
files, Main's `CLAUDE_EXECUTOR_UNQUALIFIED`, and default OpenCode are unchanged.
No Main/Lane/Transport wiring, executor, host service, Task launch, actual registry,
anchor store, receipt root, policy collector, credential/profile/settings access,
Claude invocation, installation, deployment, publication or Git mutation is added.

## Narrow continuation, not a new protection framework

[D1A](kagari-vnext-claude-r6b-phase-d1a.md) already checks synthetic receipt linkage;
[D1](kagari-vnext-claude-r6b-phase-d.md#phase-d1-proposal--separately-reviewed-future-interfaces-only)
sets the future boundaries. R0 reuses D1A's immutable `SyntheticBinding` shape and
adds only exact-head CAS **preview** and read-only crash-window classification.
It does not duplicate hashing/receipt parsing, add storage, or implement genesis.

All input domains are invented and UNTRUSTED, including the anchor, its store ack,
the separately supplied expected head, root-state claims and event/fsync claims.
Separate Python types are not independent administration, authentication, durable
state or freshness. Digest fields are exact lowercase 64-hex **opaque pins**:
R0 does not even recompute their commitment to payloads. D1A's unkeyed consistency
checks also cannot supply trust. A same-user forged checkpoint, matching path,
retained file, apparent fsync, or fully self-consistent forged universe is no proof.
There are no HMACs, signatures, authenticated flags, secrets or hidden registries.

Every result has `qualification=UNQUALIFIED`, `dispatch_allowed=False`,
`replay_allowed=False`, `task_completed=False`, `authenticated_absence=False`,
`commit_allowed=False`, and `effective_policy=UNVERIFIED`. Results contain only
fixed labels and booleans, never paths, root/attempt IDs, UUIDs or supplied values.
An observed stage is not an instruction, permission, capability or commit token.

## Future trust domains and minimal identity contract

| Boundary | Required future authority; NOT present in R0 |
| --- | --- |
| **HOST-OWNED registry** | Independently administered normalized opaque repository ID + positive Issue → one stable root identity + epoch. Repository ID is not a remote URL, cwd, clone/common-dir, UUID, Contract or caller policy. Root identity is not a path. The registry owns canonicalization and alias resolution; no caller normalization or coercion occurs here. |
| **INDEPENDENT authenticated monotonic anchor** | Separately protected, durable authority for the complete retained head/tombstone history, independent of deletable receipts and of caller/receipt-writer credentials. Registry lookup cannot manufacture a trusted anchor. Authenticate origin, freshness, completeness and monotonicity, including store/ack agreement across restarts. |

**One genesis owner:** the future host registry authority is the sole provisioning
coordinator and sole requester of genesis. The independent anchor serializes that
request with atomic create-if-absent under the canonical repository+Issue key;
this is not a second initializer. Receipt writers, clones, restart observers and
Task callers must never initialize. Provisioning must retain a durable ownership
record before root allocation. Partial provisioning freezes the key for explicit
authority-led reconciliation; a missing root/anchor is never permission to try a
different root. R0 has no provision/create/genesis API, including on a miss.

Aliases MUST resolve to the existing key, not a fresh namespace. Migration must
freeze admission and carry the same complete retained history and anchor lineage;
an epoch change must be an independently authorized continuity transition, never a
reset or a workaround for an ambiguous effect. Root loss, movement or recreation
(even with the same claimed ID), changed canonical key/epoch, competing roots and
alias migration all fail closed in R0. R0 cannot detect a lie that says PRESENT.

## Synthetic API and exact CAS boundary

All shapes are frozen/slotted, checked by exact type including nested records;
tuples are bounded, bool-as-int and subclasses are rejected. Issues are
`1..2**31-1`, epochs/generations `1..2**63-1`, sequences `0..2**63-1`.
Zero sequence + generation 1 + empty history is only a supplied genesis shape,
not authenticated absence. For a retained single tombstone, the HEAD must have
`sequence == len(inventory) + 1` and `generation == len(inventory) + 2`;
contradictory claimed history fails closed before any observational success.
This bounds the single-attempt model to sequence 1–4; MAX_SEQUENCE overflow is
unreachable for a valid retained head, and malformed oversized heads are refused.
Uninitialized exact-type objects fail closed.
These checks are not an adversarial in-process sandbox.

- `evaluate_binding_and_anchor(binding, registry, anchor, expected, inventory)`:
  compare one mapping, one stored head and its exact acknowledged head, separate
  expected current head, and the supplied ordered receipt inventory. Missing,
  stale, ahead, forked or divergent evidence has distinct fixed rejection reasons.
- `preview_anchor_cas(binding, registry, anchor, expected_old, proposed)`:
  require the entire observed old head to equal expected old, including
  `(repository, issue, root identity, epoch, sequence, digest, generation)`, retained
  tombstone and inventory. Sequence AND generation must each advance exactly one,
  with a different digest, no overflow and no key/root/epoch change. A competing
  writer/fork makes the old expectation stale; neither latest-wins nor a second
  UUID repairs it. No returned proposed head or actual compare-and-swap exists.
- First preview claims exactly one tombstone with empty receipt inventory, before
  receipt writes. Its exact attempt/intent/policy/authority-epoch and planned
  reservation/pending digests remain pinned. Later previews append exactly one
  receipt pin: reservation → pending → result. No rewrite, deletion, reset, second
  attempt or tombstone retirement is supported, **even after a result/ack**.
  Attempt identity is never a routing key. This intentionally small single-attempt
  model does not verify a complete multi-attempt log or authorize subsequent work.
- `classify_restart(...)` is strictly observational. It requires matching anchor
  and receipt inventory, the exact retained tombstone, then an exact event-prefix
  shape and result pin. Repeated calls neither learn state nor repair, initialize,
  append, retry, acknowledge or complete a Task. No output can enable an effect.

Future CAS MUST atomically compare the full expected head and persist its full
successor; a check followed by an ordinary write is not CAS. Bind canonical
commitments to all fields and retained inventory using the chosen real trust
source. A durable ack must identify the actually committed generation/digest;
store/ack disagreement is retained uncertainty, not permission to retry CAS or
dispatch. R0's shape-only digest change proves none of these properties.

## Crash windows: retained uncertainty, never automatic replay

The event tuple is a claim of reservation write/fsync → pending write/fsync →
effect boundary → result write/fsync → result acknowledgement. Skipped, reordered,
duplicate or unknown events are rejected. An effect cannot be placed before the
exact pending tombstone and its sync claim. Fsync labels are NOT crash evidence.

| Window / supplied observation | R0 diagnostic and future obligation |
| --- | --- |
| Before registry provision / root provision | `registry_missing` / `root_missing`; no initialize-on-miss. |
| Root provisioned, before anchor genesis | `anchor_missing`; retain provisioning ownership, do not allocate again. |
| Anchor tombstone claimed, before reservation write | `before_reservation_write`; claimed attempt still blocks another attempt. |
| Reservation written, before fsync / after fsync claim | `reservation_unsynced` / `reservation_sync_claimed`; no retry or effect. |
| Pending written, before fsync | `pending_unsynced`; no effect. |
| Pending sync claimed, before effect | `pending_before_effect`; observation alone never licenses effect. |
| Effect boundary crossed, outcome unknown | `effect_unknown`; retain exact tombstone, forbid resend/new UUID. |
| Result written, before fsync | `result_unsynced`; neither completion nor replay. |
| Result sync claimed, before ack OR ack lost | `result_ack_unknown`; these histories are observationally indistinguishable. |
| Matching complete tuple including result ack | `CLOSED_SHAPE_OBSERVED_ONLY`; still no Task completion, retry or commit. |
| Anchor store advanced but ack lost/stale, or ack ahead of store | `anchor_ack_missing` / `anchor_store_ack_divergence`; freeze, never promote the ack into storage truth. |
| Receipt inventory differs from head | `receipt_inventory_differs`; preserve both, do not repair or choose the newer side. |

Common binding/anchor/inventory rejection takes precedence over crash labels.
For example, a receipt write without a corresponding checkpoint can report
inventory divergence rather than an unsynced window. Tests also supply matching
invented snapshots to reach every later branch; that does not endorse storing
unsynced receipts as durable. Real write/anchor advancement ordering and each gap
require crash qualification. Empty snapshots report `never_seen_not_proven` on
restart, never authenticated absence. There is **no exactly-once claim**.

## D1B1 practical handoff — separately scoped and reviewed

Before any real Claude effect, all of the following are **MUST**, not R0 grants:

1. Establish actual trusted host authority, sole genesis ownership, normalized
   repository+Issue identity, retained provisioning records and migration policy.
2. Select and independently review a real durable authenticated monotonic anchor
   with a trust source outside caller/receipt-writer control. Alternatives include
   an independently administered transactional store or a separately protected
   append-only authority with atomic generation checks. No host service or new
   protection framework is selected here; signing one same-user file is inadequate.
3. Specify crash-qualified durable atomic writes and CAS on a supported filesystem
   and ownership/topology model. Qualify exclusive reservation, file AND directory
   sync, tombstone retention before effects, result linkage, real crash/restart,
   lost acks and store/ack divergence. Test every listed window independently;
   no `/tmp` receipt or real host checkpoint is created by R0.
4. Verify fresh policy identity independently: exact binary/version/provenance and
   semantics, full resolved/managed settings, hooks/MCP/plugins/tools, environment
   and profile isolation, account/terms and prohibited overrides. Pins are not a
   collector, policy truth, authenticated absence or credential verification.
5. Keep Main's refusal until a separate integration qualifies its **live** effect
   recheck: current Main owner, Issue, #191 binding, selected executor, authority
   epoch, exact intent/reservation and fresh effective policy. No Lane/Transport
   path, fallback or historical receipt may bypass it; Ask/resume remains unsupported.
6. Require separate explicit human Task-launch approval plus functional/recovery
   qualification. Design, tests, CAS previews and later review do not authorize
   launching Claude/KAGARI, deployment, executor enablement or publication.

## Verification scope

The 28 deterministic tests use immutable literal/tuple fixtures and `replace`.
Every checked outcome asserts the exact complete non-grant diagnostic, including identity
checks that permission fields are actually `False`. Positive controls are observed
only; branch-specific negatives include stale/fork/missing evidence, root/alias
changes, inventory divergence, oversized/inconsistent CAS counters,
duplicate/new attempts, all crash windows and malformed shapes. Event fixtures
and numeric bounds are independently specified in tests; constructor traps assert
no history initialization on a miss. Runtime traps prohibit I/O,
environment/credential reads, process/network operations and dynamic imports
during model calls. Static tests allow only dataclasses plus the pure D1A
binding import and check no new
Main/Lane/Transport references. Only boundary tests read checkout source files.

```sh
NIX_CONFIG='eval-cache = false' PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_d1b0
NIX_CONFIG='eval-cache = false' PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_d1a
NIX_CONFIG='eval-cache = false' PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_c.PhaseCSourceBoundaryTests
NIX_CONFIG='eval-cache = false' PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_b.PhaseBSourceBoundaryTests
NIX_CONFIG='eval-cache = false' PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_d_outside_git.PhaseDSourceBoundaryTests
```

Full B/C/D0 storage and full Lane suites are intentionally excluded: they create
filesystem/process fixtures, including outside-checkout roots. No real filesystem
crash, host service, policy/auth probe, model execution or runtime qualification
is tested. Independent review is deferred; no independent reviewer is invoked.
