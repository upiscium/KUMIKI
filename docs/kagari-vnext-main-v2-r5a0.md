# #291 R5A0 — Metadata v2 current-Task Main observation (source-only)

Status: **opt-in test candidate, NOT deployed or packaged, NOT v2 Task execution**. This scope is independent from #337 Phase 2B2 Worktree CREATE/RESUME and does not update or import legacy v1 metadata. #291/#337 remain OPEN.

## Frozen upstream for isolated review

The final isolated candidate branch `feat/291-r5a0-v2-currentmain` starts at **merged current main** `dc8a8bd555bec95021c14902ddfae76efb0f77e9` (2026-11-02 JST). PR #349 (#337 Phase 2B2) has merged as `11c971b1afec61f745ed6d01eae4fe193646ecf7`, and PR #348 (#339 package source) merged as `dc8a8bd555bec95021c14902ddfae76efb0f77e9`. The #291 R5A0 diff contains only `tools/kagari_vnext_main.py`, `tools/kagari_vnext_main_service.py`, `tools/kagari_vnext_main_v2_observation.py`, `tests/test_kagari_vnext_main_v2_readonly.py` and this document. None of the #337/#339-owned paths were edited. The four non-doc R5A0 source/test files have byte-identical SHA-256 across the original #349-head candidate and this latest-main worktree.

## Host-only opt-in

- `MainV2ReadOnlyCapabilities` is **Python-only**, not a config, environment variable, model response, or Issue-title flag. It supplies `read_issue` and an Issue-indexed map of exact `V2ReadOnlyMainHost` objects. The map must include **every** selected Issue: no v1/v2 mixed Task dispatch path in R5A0. The old `MainHostCapabilities` constructor and v1 plan semantics are unchanged.
- Each `V2ReadOnlyMainHost` binds exactly `#337 V2Selection(metadata-v2-linked-worktree)`, `CurrentTaskReader` (verified **current** Issue ref/Record, not historical checkpoint), an already held `#256 AttachmentLease`, and trusted Host callbacks `legacy_issue_absent` and `authorize_observation`. Only literal `True` accepts both callbacks. The trusted Host is responsible for authenticated numeric repository identity, same-Git exact-direct readers, one-LXC owner/credential control and operation-scoped grants; the Python class is **not** a new authority database.
- `CurrentTaskReader.observe()` validates the orphan-rooted v2 archive and current Issue metadata, exact Record/Contract/branch/base/approved title+body and no retired/archive disposition. The adapter rechecks exact live GitHub Issue state/identity/snapshot, current Task, and the same held attachment **after** Host callback admission; any ref/Issue drift, lost observation, stale/foreign receipt or missing Host grant fails closed.
- The imported #337 classes currently use canonical top-level `kagari_metadata_v2_task_admission` and `kagari_vnext_attachment_gate` import identities. The v2 Main module uses those same canonical types; its optional module is loaded **lazily** to break the existing #337 bootstrap → trusted_host → main_service import cycle. Do not change imports to a second `tools.*` copy of #337 classes without proving identity parity.

## Existing Main, no new runtime

- The **same** concrete `RepositoryMain` accepts this optional Host lane. `observe(issue)` routes the explicitly selected Issue to `observe_v2(issue)`; otherwise unchanged v1 observation uses `isolated_metadata_observation`. The v2 path never reads legacy `MetadataStore.fetch_tip` or `TaskRecords`.
- Successful v2 current-Task verification intentionally returns `status=blocked`, `reason=V2_MAIN_BLOCKED_LIVE`, `runtime=NOT_STARTED`, `kickoff=NOT_SENT`, and bounded current Record/Contract/archive-tip OIDs. All Task execution / Worktree mutation / GitHub write authority flags remain false. **Verified current Task is not a model-turn permit.**
- Existing `MainSupervisor(observe_only=True).cycle(dispatch=False)` returns a blocked Issue with that factual observation; it never says `ok` or `COMPLETED`. Normal CLI with explicitly injected v2 Host supports **observe** only; calling it as dispatch refuses before acquiring a Main owner or touching any Worktree. The same guard covers direct `MainSupervisor` dispatch, `RepositoryMain.dispatch`, `guarded_draft_pr` and configured cleanup. There is no v2 Session/root/broker/Ask reply/PR-first adapter in R5A0.
- A source-only `V2ReadOnlyMainHost` is not a product authenticator. Host-supplied callbacks may have side effects if buggy; the approved Host must supply **read-only** callbacks for this gate and hold the actual Attachment Lease. Tests use disposable local SHA-1 Git and synthetic callback grants, not a live GitHub principal. No RPC/HTTP endpoint, publisher or product deployment is added.

## Retained limits

- No `#337` v2 writer/remote GraphQL CAS/Ruleset/archival proof, v2 PR-first publisher, or immutable consumer activation is gained by this adapter.
- R5A0 does not import the optional v2 module into the already installed immutable `kagari-control` package or the #337 2B2 add-on. Packaging and versioned #256 Runtime binding must be separately approved/tested after the #337 and #339 source changes converge; **do not call this operational/installed v2 support**. The old installed v1 Main remains unchanged.
- R5A1 Worktree CREATE/RESUME, #163/#292 v2 native Session/Ask binding, #194 v2 Draft PR/checkpoint, live GitHub CAS and real model B2b/B3 E2E each require their own Human grants. No second Main, Task SoT, arbitrary localhost-token requirement or automatic destructive cleanup is introduced.
- #339's existing localhost-only/no-auth Control Server may read `RepositoryMain.observe`, but its existing `status=ok` guard rejects an R5A0 `blocked` Task. HTTP integration and live Task observation still require separate scope.

## Tests

`nix develop path:. --command python3 -B -m unittest -v tests.test_kagari_vnext_main_v2_readonly`

Dedicated isolated tests exercise current v2 Task positive read-only observation, two independent Issues, explicit Host and same held Attach receipt, no v1 metadata fallback, no Worktree/Session/PR/model effects, legacy same-Issue collision, denied/nonboolean/missing Host grants, retired current Record, stale ref and live Issue mutations during callbacks, expired lease, partial Host selection, direct dispatch/PR/cleanup refusal, and source-only CLI observe/dispatch separation. Related v1 and #337 suites must also pass.

**No Commit/Push/Draft PR/Ready/Merge/Release/deployment or real model/GitHub ref effect is authorized in this R5A0 gate.**
