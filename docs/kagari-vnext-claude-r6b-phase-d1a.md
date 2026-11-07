# #340 R6B Phase D1A — synthetic read-only contract MVI

**SOURCE-ONLY / NON-WIRED / TEST-ONLY / UNQUALIFIED. Not activation.**
Base HEAD: `52f2a1c80ef932c2e79b5d7477602813dad958a0`, isolated remote-free
checkout. Only this document, `tools/_kagari_vnext_claude_r6b_phase_d1a.py` and
`tests/test_kagari_vnext_claude_r6b_phase_d1a.py` are added. No tracked file changes.
Main's `CLAUDE_EXECUTOR_UNQUALIFIED` refusal is untouched.

This implements a deliberately smaller, pure-data slice of the
[D1 proposal](kagari-vnext-claude-r6b-phase-d.md#phase-d1-proposal--separately-reviewed-future-interfaces-only),
not a host registry, receipt store, executor or cryptographic trust mechanism.
It does not import B/C, Main, Lane, transport or any I/O facility. There is no CLI,
callback, filesystem traversal/read/write, environment read, process, network,
root allocation, initialization, repair, retry, reference update or service API.
No real Claude/KAGARI Task, policy/account/auth probe or effective-policy
collection is performed. No production path or raw identity is returned.

## Trust boundary: every witness is synthetic and untrusted

All witness classes are explicitly named `Synthetic*`, frozen, slotted and checked
by **exact type**, including nested records. Dictionary lookalikes, subclasses,
boolean-as-integer values, mutable arrays and malformed values are rejected.
Incomplete exact-type objects also fail closed. These checks are not an in-process
adversarial sandbox or a way to authenticate the constructor/caller.

The source marker and every diagnostic say
`R6B_D1A_SYNTHETIC_TEST_ONLY_UNTRUSTED`. A purported live/host-authenticated marker
is invalid. A "trusted-typed checkpoint proposal" here means only the proposed
future interface's shape, **not actually trusted or authenticated evidence**.
There are no signatures, HMACs, keys, secrets, signing helpers or attestation bits.
Unkeyed SHA256 checks detect only inconsistencies relative to supplied values.
The caller can invent an entirely self-consistent universe, including its reference.
That universe can earn an observational stage, never authority.

## Inputs and deterministic checks

`observe_routing(binding, snapshot, reference)` returns only a sanitized diagnostic.
`reconcile(binding, snapshot, reference, pins)` repeats routing checks, then checks
the exact synthetic history. Neither mutates inputs or retains state between calls.

| Witness | Meaning in this fixture only |
| --- | --- |
| `SyntheticBinding` | Canonical opaque repository ID + positive Issue, fixed root identity + positive epoch. Repository/root IDs are exactly 64 lowercase ASCII hex characters. They are not paths, remote spellings, clone identities or automatically resolved aliases. A future host must supply a genuinely stable repository identity. |
| `SyntheticRegistrySnapshot` | One repository+Issue slice containing root bindings, checkpoint proposals and an inventory. Tuples are bounded to 8 items each; exactly one root and one checkpoint must be present. Missing roots, conflicts and duplicates cannot select a new history. |
| `SyntheticCheckpointProposal` | Binding, positive sequence, digest of the entire ordered inventory and synthetic source label. Its digest also covers binding, sequence and source. No persisted checkpoint or append operation is implemented. |
| `SyntheticCheckpointReference` | Separately supplied expected binding, current sequence and proposal digest, modeling the **deferred independent trust anchor**. Its distinct type is not proof of independence, provenance or freshness. It cannot be extracted from a registry as a trusted reference here. |
| `SyntheticAttempt` | Digest-only attempt identity, optional manifest digest, optional reservation/pending/result records, and an exact boolean acknowledgement indicator. No actual receipt bytes are parsed or stored. |
| `SyntheticRecord` / `SyntheticAttemptPins` | Records contain positive sequence, digest and predecessor digest. Pins supply exact invented attempt, manifest, reservation, pending and result digests. These are indicators, not actual operation/policy/authority validation. |

Issues are bounded to `1..2**31-1`; epochs and sequences to `1..2**63-1`.
No normalization/coercion, unbounded iteration, recursive external schema or path
interpretation is supported. Identity changes in root, proposal or reference are
rejected against the fixed binding, including repository/Issue aliases and epoch
rollback or replacement. Missing reference is UNKNOWN, never genesis evidence.

The proposal must match the reference's **exact current sequence and digest**.
A lower sequence is `checkpoint_rolled_back`; any higher one is `checkpoint_gap`
(including one unanchored increment); equal sequence with different digest is
`checkpoint_fork`. This is not an append-chain verifier or a reference refresh
protocol. Removal of a supplied checkpoint is `checkpoint_deleted_or_missing`;
the verifier does not observe actual disk deletion. Inventory corruption is
checked separately from reference mismatch. Hashes use canonical ASCII JSON
`[SYNTHETIC, payload]`, sorted object keys and compact separators. Array order is
preserved. Inventory payload is the list of complete attempt dictionaries;
proposal payload is its complete dictionary. Hashing follows bounded validation.

Reconciliation deliberately supports **only one closed attempt**, although it can
inspect up to eight supplied attempts to reject duplicate/extra history. It never
picks the latest attempt or lets a second UUID replace retained uncertainty.
Manifest and all present record digests must match pins exactly. Predecessors must
link manifest → reservation → pending → result. Present records must be in that
sequence order and not beyond the checkpoint. A closed fixture additionally has
consecutive sequences starting at 1 and ending at the checkpoint (therefore
reservation=1, pending=2, result=checkpoint=3). This strict small model cannot
represent broader event histories; those need a separately reviewed interface.
Sequence indicators do **not** prove that pending was actually durable before an
effect, or that any result/effect/acknowledgement happened.

## Outcomes: no permission, including on success

All diagnostics have `qualification=UNQUALIFIED`, `dispatch_allowed=False`,
`replay_allowed=False`, `task_completed=False`, `authenticated_absence=False`,
`effective_policy=UNVERIFIED` and `ask_capability=UNQUALIFIED`. They contain only
fixed labels and booleans, no root path, repository ID, attempt ID or input value.

| Situation | Diagnostic / consequence |
| --- | --- |
| Missing registry/root/reference/inventory; malformed or conflicting evidence | Specific fixed reason, `status=UNKNOWN`, `stage=NOT_OBSERVED`; no initialize-on-miss. |
| Empty inventory, or manifest without any records | `never_seen_not_proven`: **no receipt observed in this supplied snapshot**, not authenticated proof of no prior effect. Such proof is unobtainable here. |
| Reservation without pending/result | `reservation_only`; uncertainty is retained. |
| Pending without result, with or without reservation | `pending_only`; no resend. |
| Result without pending | `result_only`; cannot infer missing pending or an effect. |
| Matching pending/result but no reservation | `reservation_missing`; not a closed history. |
| Wrong result digest or predecessor | `result_mismatch`; no completion claim. |
| Duplicate/extra attempts or changed requested attempt identity | `duplicate_attempts`, `extra_attempt` or `attempt_mismatch`; no second-UUID grant. |
| Closed linked records but acknowledgement absent | `ack_lost`; no replay. Ack without result is independently rejected. |
| Structurally matching routing | `status=UNQUALIFIED`, `stage=ROUTING_OBSERVED_ONLY`, `reason=observed_only`; not a route/capability output. |
| Fully matching closed synthetic history | `status=UNQUALIFIED`, `stage=CLOSED_HISTORY_OBSERVED_ONLY`, `reason=observed_only`; still no dispatch, replay or Task completion. |

The first failing check determines the reason; all other combinations remain
conservatively UNKNOWN. Nothing detects a jointly forged or jointly rolled-back
snapshot **and** reference, authenticates complete history, or provides restart
memory. This is not an "exactly once" guarantee or an executable no-replay ledger.

## Offline verification

The D1A suite has 23 focused tests: constructed fake literals only for witnesses,
plus a source-only import/Main-gate check. No filesystem receipt fixture is made.
History mutations rebind inventory/proposal/reference digests and assert successful
routing before checking the exact reconciliation rejection reason. Routing negative
cases also assert their specific rejection branch, not merely generic UNKNOWN.
Positive controls assert the complete non-grant diagnostic, and runtime traps cover
filesystem access, environment access, process creation and networking.

```sh
PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_d1a
PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_c.PhaseCSourceBoundaryTests
PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_b.PhaseBSourceBoundaryTests
PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_d_outside_git.PhaseDSourceBoundaryTests
```

Only compatible B/C/D0 source-boundary tests are appropriate in this no-record
scope. Their full storage suites are intentionally not run: B/C create filesystem
fixtures and D0 creates real outside-checkout `/tmp` roots. This run is not a rerun
of D0 crash/storage evidence. Independent review is deferred to a later authorized
step; no independent reviewer is launched by this implementation.

## D1B next — separately scoped and reviewed, not authorized by D1A

1. **Real host-owned stable external registry:** independently administered stable
   repository identity, aliases/migration policy, one retained root identity/epoch
   across clones/restarts, and explicit lost/replaced-root handling. No caller cwd,
   remote, UUID, Contract or policy may choose another history; no auto-initialize.
2. **Independent authenticated checkpoint/replay anchor:** authenticated genesis,
   complete bounded retained attempts, monotonic state, rollback/deletion/fork/gap
   detection, freshness and restart-safe retention independent of deletable receipts.
   Signing one receipt or storing the only anchor beside it cannot satisfy this.
3. **Crash/filesystem qualification:** actual host ownership, topology/mount and
   supported filesystem model; exclusive reservation/tombstone, file/directory sync
   before effects, durable result linkage, crash/restart reconciliation and lost-ack
   behavior. D1A sequence indicators are not durability or hostile-mutation tests.
4. **Effective-policy verification:** independent exact-version binary provenance
   and semantics, full resolved settings precedence/managed sources, hooks, MCP,
   plugins/tools, environment/profile isolation, personal-account/terms clearance
   and prohibited override checks. No credentials or real settings were read here.
5. **Main live effect boundary:** retain the refusal until independently qualified
   integration rechecks Main ownership, live Issue, #191 binding, selected executor,
   policy freshness, authority epoch and exact reservation immediately before an
   effect. Ask/resume and deferred/tool states remain unsupported; no implicit grant.
6. **Separate explicit Task-launch authorization:** D1B design, implementation or
   review cannot authorize launching Claude/KAGARI, dispatch, publication or
   activation. Functional/recovery qualification and human launch approval remain
   separate gates. No false exactly-once promise at any stage.
