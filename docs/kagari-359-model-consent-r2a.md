# KUMIKI #359 — R2a Pure Model-Consent Codec

**Status:** Isolated source-only candidate. DATA ONLY; **no model execution, Human consent, Native grant, release, or deployment**. The previously reproduced R2 mixed-I/O same-UID TOCTOU P2 remains a blocker for any trusted Host persistence or live Task adoption. This document does **not** clear that P2 for R2b.

## What this layer actually does

`tools/kagari_task_model_consent_codec.py` accepts already captured, byte-exact inputs from a future **trusted KAGARI Host**. It does not own the source files, authenticate their provenance, read Project state, or persist any approval.

1. `normalized_taskspec_bytes`: **exact canonical JSON bytes** from the pinned, independent TaskSpec verifier, not the raw source-file hash. R2a rejects noncanonical JSON, invalid schema/version, mismatched Project HEAD, and inconsistent paths/tests; the authoritative Git/Project validation still belongs to the Host.
2. `model_sidecar_bytes`: exact original raw bytes, passed to **R1's shared pure byte parser** `parse_model_binding_bytes`. R1's existing `load_model_binding(path)` remains its file-access compatibility wrapper. The new parser keeps strict v1 model/provider/variant, four-role order, duplicate-field, SHA and allowlist checks. The packet records distinct **raw** and **canonical** SHA-256 digests.
3. `host_context`: synthetic/test-only structured binding of repository, Issue/Job, Project root, dev/inode/HEAD/index/instruction digest, Work Unit clone, OpenCode executable hash and versioned package/receipt hash. Fields are checked for exact shape, **not** independently authenticated as real Git or filesystem observations.
4. `host_native_ask_readbacks`: exactly two declared Ask kinds; a missing AGENTS Ask must use the exact Host-asserted no-conflict sentinel, and permission Ask must have a complete, exact-shape active generation with full request/decision/runtime digests. The codec cannot check real Native ownership, revocation or Human authorization. Those readbacks must come from a trusted Host, never user/model text.
5. `trusted_model_allowlist`: nonempty exact `frozenset[ModelSelection]` provided by the future pinned Host, not by arbitrary Project files, OpenCode defaults, the prompt or the sidecar. An exact `openai/gpt-6-astra` / `max` selection for planner, primary, leaf-a and leaf-b is supported as data; unsupported providers/variants cannot silently fall back.
6. `host_job_nonce`: externally generated, fresh, fixed 32-digit lowercase hex nonce. **R2a does not generate randomness**. New nonce, Native Ask/TaskSpec/Project/model bytes or package identity changes produce different request content.

The builder computes `request_id = sha256(canonical(request_without_id))[:24]`, then serializes the full request (with ID) into canonical UTF-8 JSON plus one final newline. `request_sha256` is the complete resulting packet SHA-256. The verifier accepts the packet only when it is byte-for-byte identical to the freshly rebuilt request using independent expected inputs, including the **same trusted nonce**. It does not accept an `approved` flag or executable authority fields.

`FrozenConsentPacket` carries immutable bytes, exact request identity, model/roles and `DATA_ONLY_NOT_AN_AUTHORIZATION` status. Its **serialized request body** carries the false effect/attestation flags; `DataOnlyVerification` also exposes `model_dispatched=False`, `provider_identity_attested=False`, `native_grant_provenance_attested=False`, and `production_activation=False` as attributes. A matching digest is an **integrity relationship**, not consent, authentication or an executable capability.

## Side-effect and compatibility boundaries

- No I/O in the R2a codec: no `open`, directory creation, `os`, `pathlib` stat/resolve, `fcntl`, TTY, subprocess/Git, model/provider request, clock or random generation. Tests explicitly instrument forbidden `builtins.open`, `os.open`, `os.mkdir`, `subprocess.run` and `builtins.input` while exercising both public operations.
- R1 file-loader behavior remains supported; the pure helper shares the **same validation source**, not a separately relaxed parser.
- R2a **does not import or execute** the old R2 mixed-I/O prototype. That prototype stays unmerged and preserved outside this source change.
- No changes to shipped **KAGARI v0.1.0 GA**, `flake#kagari`, source authority, KUMIKI #317, KUMIKI vNext / #291, or RITSU.
- No source Commit/Push/PR/Ready/Merge, no release, no deployment, no Native Human Ask creation/approval and no model invocation. Human authority is not derived from an assistant or LLM statement.

## Required future R2b Host boundary (NOT IMPLEMENTED)

Before any consent request is persisted or presented as an actionable Human approval, a separately reviewed Host must establish OS-enforced storage/Project separation. It must prevent attacker-controlled **same-UID directory renames/reparenting** of storage and the original Project during every check/write/cleanup, not merely validate an FD or mode 0700. The old R2 source already proved that a validated FD can be moved into Project after checks and write three records there while returning success.

The future Host also owns: normalized TaskSpec source provenance; exact Project Git/HEAD/index and instructions; independently current allowed model tuple; package/executable identity; Native Ask owner/active grant/expiry/revocation; secure fresh nonce and replay store; physically protected atomic consent record storage; foreground Human TTY, exact request presentation and once-only durable decision; and last-minute authorization before model dispatch. Do not trust a JSON `host_verified=true`, project-set environment, localhost listener, Unix socket peer ID alone, or a caller-constructed `DataOnlyVerification` as a grant. OS confinement, root/service credentials and any deployment need their own explicit Human/Admin decision.

R3 handles exact OpenCode planner/primary/two-leaf `--model` and `--variant` routing plus observed session metadata. R4 handles separately versioned candidate build, adversarial OS isolation evidence, CI, security review and release decision. R2a source-only PASS would **not** imply R2b, R3 or production qualification.

## Verification

Run from a disposable, no-remote checkout against the exact current-main parent:

```sh
nix develop --no-update-lock-file --command python3 -B -m unittest discover -s tests -p test_kagari_task_model_consent_codec.py -v
nix develop --no-update-lock-file --command python3 -B -m unittest discover -s tests -p test_kagari_task_model_binding.py -v
```

Negative tests include model/variant/role swaps, TaskSpec digest drift, changed raw sidecar whitespace with identical canonical meaning, modified Native Ask generation, changed Project/Job/package identity, invalid/duplicated/noncanonical JSON, unknown approval flags, an expected nonce mismatch and unsupported models. Repeating an identical nonce and packet is deliberately not blocked by R2a; only the future Host can enforce freshness and replay protection. A bounded test instrumentation proves the pure functions do not call known file/process/TTY entry points.

Astra max source authoring is established at **OpenCode session/request level** where recorded and checked; it is not provider-side proof of reasoning effort or a claim that every line was authored by the model. Mechanical R1 extraction and supplemental tests/docs were host-assembled and independently checked. Exact-code security review and separate source publication decisions remain mandatory.
