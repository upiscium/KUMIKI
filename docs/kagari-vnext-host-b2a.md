# #291 Phase B2a — source-only Host integration qualification

**Not Active Runtime installation or live qualification.** v0.1.0 GA,
the default no-host Main CLI refusal, #256-owned sources, packages and
consumer installations are unchanged. No new executable or automatic host
loading is introduced. B2a adds only a trusted in-process observation seam
and offline tests. Independent correctness/security review belongs to the
coordinator, not the implementer.

## Deliberately bounded bridge

`tools/kagari_vnext_host_qualification.py::qualify_host` accepts the existing
trusted Main selection and explicitly injected `MainHostCapabilities` from
B1 `assemble_host`. Import with the reviewed source `tools` directory on
Python's import path, as required by the existing #256 host modules; keep
one consistent import style (`tools.*` or top-level) for the host types.
There is no plan/environment field that can select an implementation.

The trusted caller must already have separately prepared the exact #191
records, registered linked Worktrees, external #304 attachment receipt,
private host-state directory, immutable package identity, B1 callbacks,
operation prerequisites and stable recovery journal. B2a does not create
any of these or authorize missing #191 creation. Fixture approvals are not
production policies.

1. Refuse before observation unless `runtime_preview is True` and the caller
   supplied the exact existing capabilities type. Preview opt-in is **not**
   execution authorization.
2. Hold the existing #329 `attachment_gate.hold` around the entire probe.
   No change to #256 ownership, lock namespace, install/uninstall or repair.
3. Build the existing Main with those capabilities; briefly acquire the
   existing `MainSupervisor` owner and hold its in-process guard. A busy or
   unverifiable owner refuses. No new lease, broker or ownership database.
4. Read each selected Task through Main's existing #191/Issue checks. Ask
   the injected provider to construct its #194 facade for the **already
   registered** exact Worktree; check repository/remote/Task/branch/base and
   the presence of its injected #217 callback. Reobserve repository/principal
   and Task/Issue facts. This is construction/readiness observation, not a
   proof that the callbacks enforce every required publication guarantee.
5. Never call `dispatch`, `establish`, metadata capture, branch publication,
   authorization, intent recording or a model/Session. Revalidate the owner
   before release. Return only after attachment exit revalidation succeeds.

Every completed observation returns **`BLOCKED_LIVE`**, with
`HOST_BINDINGS_OBSERVED_ONLY`, the Issue list and attachment digest, and
three explicit blockers:

- `ACTIVE_RUNTIME_INSTALL_STOP_UNIMPLEMENTED_256`
- `SOURCE_GUARD_PUBLISHER_NOT_QUALIFIED`
- `GITHUB_DIRECT_REF_CAS_NOT_QUALIFIED`

`runtime_installed`, `main_started`, `task_execution_authorized` and
`model_or_session_started` remain false. Here `main_started=false` means no
active Runtime/Main cycle was launched; the existing owner lock was held
temporarily for the probe. Errors/uncertainty raise, never fabricate a
partial success receipt. Reports are ephemeral observations, **not** durable
runtime receipts, write permits, future dispatch authority or proof of an
approved #194/#217 mutation. Denied execution callbacks are not probed or
promoted into approval. #256 activation has no bypass flag or fallback.

### Effects and trust limits

The probe makes Git metadata fetches and injected GitHub reads. It can
populate local fetched objects/FETCH_HEAD and the existing Main owner can
create its ordinary lock file. It requests no product, ref, index, journal,
attachment or runtime mutation, allocation or cleanup. The host callbacks
are trusted Python capabilities, not sandboxed code. An arbitrary caller
can supply a malicious callback; this seam does not confer trust on it.
Production Source Guard and direct-ref CAS are **not** implemented here.
Tests deliberately use fake GitHub and real disposable local Git, never
credential discovery, a live API or an actual OpenCode/model process.

## Offline direct-ref CAS feasibility finding

The normative contract remains [#217 metadata transport](agent-core-metadata-ref.md):
atomically update only `refs/agentcore/metadata`, check the exact expected
old OID **and direct-ref type at the locked transaction boundary**, never
dereference a symbolic ref, and require the candidate's parent to equal
the expected old OID. Confirm the exact candidate after uncertain ACKs.

- A pre-read plus normal Git push is not that transaction. Client-only
  `--force-with-lease` checks an OID, not the locked server ref type; it is
  explicitly refused as qualification evidence, including when wrapped in
  a callable named `direct_ref_cas`. This probe never invokes it and still
  returns `BLOCKED_LIVE`.
- REST ref-update fast-forward behavior must not be treated as exact-old
  compare/exchange or atomic no-dereference evidence. The current bounded
  `GitHubHTTPS` executor exposes neither Git-ref mutation nor GraphQL.
- A possible future investigation is GraphQL `updateRefs`/`beforeOid`.
  That name alone is not evidence of the required direct-ref type, exact
  namespace, parent, concurrency and no-dereference guarantees. No current
  GitHub schema/docs or server behavior was contacted or verified here.
  Consequently this work does **not** claim GitHub CAS is impossible, nor
  implement an optimistic substitute. The precise blocker is absent
  demonstrated server-side direct-ref semantics for the #217 contract.
- Existing local bare-repo fixtures transfer candidate objects, use
  `update-ref --no-deref` with expected old OID, and reject symbolic metadata
  refs in a `reference-transaction` hook at `prepared`. Those fixtures can
  demonstrate the local safety contract and races, **not GitHub capability**.
  No such backend or new CAS authority is added to production.

## B2b gates — separately owned/approved

Before any live qualification/activation, require coordinator independent
correctness/security review of exact source hashes; #256's real owner-bound
Active Runtime install/stop, startup handshake, persistent runtime evidence,
quiescence/detach and crash/no-ACK reconciliation; independently supplied
#191/#194/#217 operation-specific authorizations and content policies; a
pinned Source Guard publisher proving exact destination/non-force Task-ref
publication; demonstrated server-side direct-ref CAS or continued
`BLOCKED_LIVE`; and separately approved disposable fixture, repository and
principal identities, least-privilege credentials and recovery procedure.
No second broker, host-created allow-all policy, automatic grant, source
guard bypass, generalized Runtime or client-only CAS downgrade is allowed.
Progress/cleanup routes and real model turns need their own qualification.
No source publication or consumer installation is implied by offline PASS.

## Offline evidence scope

Focused tests: `tests/test_kagari_vnext_host_qualification.py`. They compose
the actual #329 guard, Main owner, #191 checks and B1 assembly with fake
REST, inert package binaries and disposable Git. They cover disabled
Preview, absent injection/attachment, missing and denied authority,
publisher refusal, CAS absence/client-only nonqualification, identity and
receipt drift, single owner, uncertain/malformed observations and no
accidental publication/runtime/model effect. B1 regressions separately
exercise exact-candidate recovery and uncertain write receipts/no retry.

Run bounded shards using:

```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c \
  env TMPDIR=/tmp/opencode GIT_CONFIG_NOSYSTEM=1 \
  GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
  python -B -m unittest -v tests.test_kagari_vnext_host_qualification
```

Disposable fixture setup creates its own synthetic commits and local
publications; no implementation-clone staging, commits or publication are
performed. See the source-only validation evidence accompanying the handoff.
