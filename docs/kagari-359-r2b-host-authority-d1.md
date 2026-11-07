# KUMIKI #359 R2b D1 — Synthetic Host Consent Authority Contract

**State:** SOURCE-ONLY, SYNTHETIC, NON-AUTHORIZING. No Host service installed, no OS credential/UID/ACL/mount, no Native grant, no Human consent, no model dispatch, no source publication or release.

**Parent:** merged PR #373 supplies R2a pure Consent Codec, used here only to reconstruct and bind canonical data bytes. Existing shipped KAGARI v0.1.0 GA and `flake#kagari` are unchanged. KUMIKI #317 is outside this workstream. RITSU #3 is not unblocked for managed Astra max execution.

## Scope and immutable boundaries

D1 consists only of:

- `tools/kagari_model_consent_host_contract.py`: strict canonical bounded mock IPC request, data-only response, synthetic observation to exact R2a packet binding, and hard-denied authorization entrypoint.
- `tools/kagari_model_consent_host_store.py`: disposable SQLite protocol/state-machine simulator with no system service, OS authority, nor Host pathname creation. It receives a test-supplied `sqlite3.Connection`. **Never connect it to a production path or treat it as a durable authority.**
- `tests/test_kagari_model_consent_host_d1.py`: fully synthetic source/Native/model fixtures; temporary SQLite databases; concurrent compare-and-swap, duplicate/replay, expiry/revocation, crash and backup rollback tests.
- This D1 document, plus the earlier owner-private D0 design/security review.

No code may grant a Task, consume a credential, approve a human, install a service, invoke OpenCode or provide an executable model authorization. Explicit rejection is `HOST_AUTHORIZATION_NOT_IMPLEMENTED` or `EXECUTION_NOT_IMPLEMENTED`. The only acknowledged model tuple in positive fixtures is `openai/gpt-6-astra` with `variant=max` across planner, primary, leaf-a and leaf-b; this is **data**, not proof of actual model execution.

## Mock IPC request and response

`KAGARI_CONSENT_HOST_IPC_V1` is a canonical UTF-8 JSON object with **exactly** six fields:

| Field | Requirement |
|---|---|
| `schema_version` | Exact integer 1, not bool |
| `kind` | Exact `KAGARI_CONSENT_HOST_IPC_V1` |
| `operation` | `submit_candidate` or `status` only |
| `job_id` | `job-` + 24 lowercase hex digits |
| `request_id` | 24 lowercase hex digits |
| `packet_sha256` | 64 lowercase hex digits |

The parser rejects duplicate JSON keys, noncanonical representation, nonfinite constants, oversized (greater than 16384 bytes) or malformed input, unknown fields, alternate operations, path destinations, credentials, execution, approval claims, local FD/socket references and ambient OpenCode settings.

`submit_candidate` is **not** a real transport operation; `resolve_synthetic_candidate` accepts the parsed data **plus a test-created `SyntheticHostObservation` supplied out of band**, independently rebuilds the exact R2a packet and compares Job/request/full packet SHA. Rebuilt packet flags must indicate model not dispatched and provenance not attested. That observation object is constructible by a caller and **does not authenticate Project, Native Ask, model or Human**. A production daemon must never expose that object or trust an IPC caller-provided Host observation.

The separate status response is `KAGARI_CONSENT_HOST_IPC_RESULT_V1` and contains an explicit `DATA_ONLY_NOT_AN_AUTHORIZATION` result with `human_authenticated=false`, `native_grant_attested=false`, `model_dispatched=false`, and `production_activation=false`. A status query provides no replay token or writable destination.

**No transport implementation:** no actual Unix socket/server/client, no systemd listener, no SCM_RIGHTS, no peer credential processing. D1 tests exercise canonical IPC **messages**, not OS socket security or a credentialed ceremony.

## Synthetic transaction authority and state machine

The source-only `SyntheticConsentLedger` receives a connection supplied by its test harness. It opens no arbitrary filesystem path and does not acquire a security principal. Test files live exclusively in disposable temporary directories.

SQLite schema/operation binding is explicit: the **main** schema is the sole synthetic authority store. Initialization inspects `main.sqlite_master` and the main table/index constraints and rejects preexisting TEMP schema objects. **Every** later read/insert/update is qualified as `main.consent` or `main.epoch_metadata` (including index PRAGMAs), so a TEMP shadow created *after* initialization cannot intercept ledger access. Test-first regressions demonstrated that an unqualified TEMP consent table previously defeated main's UNIQUE constraints; the correction rejects preexisting shadowing and preserves main-table uniqueness after a late shadow. This is a synthetic SQLite state-binding invariant, **not** an OS/storage authority claim.

SQLite stores exact request packet bytes plus SHA-256, immutable Job ID, request ID, nonce, Host epoch, synthetic Native generation and logical expiry. UNIQUE constraints cover Job ID, nonce and request ID. Every transition is an SQL `BEGIN IMMEDIATE` transaction with explicit state/revision/digest/native-generation/epoch preconditions. **The SQLite connection is test-harness-owned and must be exclusively serialized by the harness.** `_tx()` refuses an already-active caller transaction before attempting `BEGIN` and rolls back only a transaction it successfully started; it cannot roll back a preexisting caller transaction when `BEGIN` fails. D1 tests explicitly preserve caller-owned uncommitted data and verify no transaction ownership theft. Stored Native generation strings must match the same `REVISION` grammar required at initial synthetic admission; read, transition, and reopen reject malformed stored generation values. A concurrent stale update fails rather than merging an ambiguous consent state. **Register/transition return an exact data-only receipt captured and validated inside their own SQLite write transaction, before commit**, not a later status read. A second connection may commit a successor immediately after the first operation's commit, but that cannot rewrite the first operation's receipt. Test-first deterministic two-connection interleavings confirm exact registered PENDING/revision-0 and transitioned SIMULATED/revision-1 receipts even when a peer immediately commits DENIED or REVOKED. A separate later status read may of course observe that newer state.

**Source-only admission invariant:** the public synthetic ledger registration API takes `ipc_bytes` plus an **out-of-band `SyntheticHostObservation`** and MUST independently re-run `resolve_synthetic_candidate()` against the merged R2a codec before storing a request. It does **not** accept a directly constructed `SyntheticCandidate` as public registration authority. The mock observation remains freely constructible by test code and therefore cannot authenticate a real Host, Human or Native grant. A self-consistent but incomplete JSON packet with recomputed digests is rejected unless it exactly matches the separately supplied synthetic observation.

**Synthetic recovery invariant:** on initialization, compare the exact pinned table DDL, all required unique identity indexes and disallow unexpected schema objects. **Reachable revision is state-bound**, not merely nonnegative: PENDING_HUMAN_TEST_ONLY requires revision 0; SIMULATED_TTY_ACK_DATA_ONLY and DENIED require 1; REVOKED and EXPIRED require 1 or 2 (one transition directly from PENDING, or a second from SIMULATED). Any impossible state/revision combination must return RECOVERY_UNCERTAIN on read, reopen and transition. Deterministic security tests first reproduced acceptance of SIMULATED at revision 0 and PENDING at revision 1, then verified rejection after the exact Astra max generated check was added. On reopen, read and transition, check stored state membership, complete top-level packet schema/identity/digest, epoch, revision/expiry/tick invariants and no-execution flags. For nested contents, reuse merged R2a's pure Context and Native Ask shape validators, plus strict `ModelSelection` values/ordered roles, TaskSpec allowed-paths/test projections and raw/canonical digest syntax. The validator checks **structure, not independently authenticated Host provenance or source TaskSpec bytes**. An altered stored `APPROVED` state, null Native Ask entries, invalid model variant/context or expected table names with missing UNIQUE constraints must fail as `RECOVERY_UNCERTAIN`, never be reported as a valid status. These checks detect fixture corruption; they do **not** prove resistance to a malicious same-UID actor rewriting a real database or a consistent old backup.

```text
PENDING_HUMAN_TEST_ONLY
  ├──> SIMULATED_TTY_ACK_DATA_ONLY ──> REVOKED | EXPIRED
  ├──> DENIED
  ├──> REVOKED
  └──> EXPIRED

DENIED, REVOKED, EXPIRED, RECOVERY_UNCERTAIN
  └──> no outgoing transitions
```

Expiry uses an **exclusive** upper bound for non-expiry transitions: at or beyond the recorded `expires_tick`, no simulation/denial/revocation can be newly accepted. A forward explicit EXPIRED transition is permitted at that boundary. Logical ticks are **trusted synthetic inputs**, not an OS monotonic-clock attestation.

**Crucial:** `SIMULATED_TTY_ACK_DATA_ONLY` is not approval. A real `CREDENTIAL_BOUND_CONSENT_NO_EXECUTION` state is intentionally **absent/unreachable** in D1. `APPROVED`, `CONSUMED`, `EXECUTED` and similar states cannot be emitted. There is no path from a simulated acknowledgement to an executable capability. The SQLite ledger intentionally returns `human_authenticated=false` even for its simulated state.

### Durability, replay and recovery limitations

- The synthetic database persists across a normal test process restart; D1 tests deny previously terminal records. Duplicate request IDs, Job IDs and nonces are rejected within the *current* database.
- A different externally asserted `authority_epoch` fails closed on reopen. However, the D1 ledger **does not** have a separately protected monotonic epoch authority.
- **Demonstrated gap:** restoring an older but internally valid backup while retaining the same epoch can restore an older PENDING state. D1 deliberately tests this and confirms that *even then* there is no executable approval. **This is a D2–D5 OS/authority proof blocker**, not an acceptable production behavior. The future Host needs an independently protected new epoch, durable replay tombstones or fail-closed recovery/invalidations.
- SQLite transactions rollback after simulated crash-before-commit and serialize two competing decisions; this is not a guarantee about hardware durability, filesystem `fsync`, WAL backups or migration.
- An external Native revocation can occur after a snapshot; the current `native_revision` field is a synthetic string, not authenticated live Native permission. A real Host must get a current signed/protected Native revision and reverify immediately before credentialed decisions and future R3 dispatch.
- Logical ticks cannot prevent OS wall-clock rollback or prove expiry across reboot. A real Host must own and validate the expiry timebase.

## D2–D5 required Host security proof (not authorized by D1)

**D2: root-managed Host system service boundary.** The leading candidate for NixOS 26.11/systemd 261 is a version-pinned root-installed system unit with a distinct `DynamicUser`, private `StateDirectory`, `ProtectSystem=strict`, and explicit safe read-only source observation. Root-controlled/non-user-writable ancestors and service-only private state must be verified in the **effective** mount and ID mapping. An ordinary same-UID client cannot rename Host storage into a Project or substitute directories. No service, UID, ACL, NixOS configuration, socket, privileged bind, `sudo` or Nix activation was created here.

**D3: IPC/output closure.** Reject all untrusted ancillary FDs and caller-selected write paths/SQLite URI. Enumerate inherited descriptors, writable temporary/API mounts, journald/standard output and accessible AF_UNIX services; ensure none permits a Project-write deputy. A simple `RestrictAddressFamilies=AF_UNIX` alone is not peer isolation. Actual two-process precheck and postcheck rename attacks must be denied by OS policy, not merely mock tests.

**D4: authentic Native + Human consent.** Existing Native Ask file readability does not prove its grant was issued or is still active. The future Host must receive independent authority-signed/protected generation and revocation evidence. Local TTY or `SO_PEERCRED` does not identify a Human against an adversarial process using the same UID. A separately reviewed request-specific credentialed ceremony must present the exact Host-frozen subject, request digest, nonce, model ID/variant and scope via a trusted display path, not merely accept a caller-written `approved=true` or cached generic authentication. An unauthenticated TTY confirmation can **only** populate simulation state and must not be upgradable into real approval.

**D5: runtime/release gates.** Even a future authenticated Host decision is not R3 dispatch authorization without last-moment TaskSpec, Project, package, Native grant and exact model/variant revalidation. R3 implements OpenCode planner/primary/native leaf dispatch and effective session metadata checks; R4 implements versioned candidate qualification and separate Human release/deployment approvals. Never silently swap `max` for `high`, or alter the pinned KAGARI v0.1.0 GA.

## D1 verification scope

Run only under a disposable no-remote KUMIKI current-main checkout:

```sh
nix develop --no-update-lock-file --command python3 -B -m unittest discover -s tests -p test_kagari_model_consent_host_d1.py -v
nix develop --no-update-lock-file --command python3 -B -m unittest discover -s tests -p test_kagari_task_model_consent_codec.py -q
nix develop --no-update-lock-file --command python3 -B -m unittest discover -s tests -p test_kagari_task_model_binding.py -q
```

Positive and negative coverage includes malformed/colliding IPC, no approve/credential/FD/path fields, exact Astra max selection, stale full SHA, native generation drift, synthetic state-only replies, one-shot transitions, concurrent opposite decisions, duplicate nonce/replay, deadline-edge denial, rollback on exception, restart and restored old-backup uncertainty. Synthetic recovery tests also rehash invalid nested Native Ask/model/context data, and require **read, transition and reopening** to reject it as `RECOVERY_UNCERTAIN`. Passing these tests **cannot** establish OS confinement, real durable security, Native authenticity, Human approval, provider-side model attestation or executable permission.

The exact OpenCode `openai/gpt-6-astra`, `variant=max` source-authoring sessions produced the bounded IPC parser and pure transition-eligibility helper, which were incorporated in this D1 candidate. The surrounding SQLite store, model-suggestion correction at the exclusive expiry boundary, additional adversarial tests and documentation were integrated by the Host and must not be misattributed to Astra. Model-session routing metadata is not cryptographic proof of provider-side reasoning effort.

**Authorization boundary:** This D1 is source-only, no Commit/Push/PR/Ready/Merge/Release/deployment, no #317, no published GA change, and no RITSU task/model invocation. Independent source/security review remains required before any future source publication.
