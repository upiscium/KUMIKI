# KAGARI vNext: Task-scoped OpenCode Ask/status CLI (#292)

**Status:** isolated MVI implementation candidate, NOT a GA extension or live
KAGARI Main/Task integration. The shipped `kagari-0.1.0` output is unchanged.
The local server-backed transport has been observed on OpenCode 1.18.35.

## Intent and owner

KAGARI is the only Task/session/Ask management owner. This CLI uses a binding
that a future KAGARI Main runtime will produce. The binding is **runtime
location metadata, not independent authorization or a new Task Record**.
The canonical Issue/contract binding remains in KAGARI Task Record and
GitHub. MUSUBI calls the *same* CLI surface from its backend.

Current scope: status, exact current pending Permission/Question enumeration,
details and a single exact answer. It does **not** launch/supervise OpenCode,
author Task bindings, infer specification changes, accept products or
automatically begin another Issue. This narrow prototype can be integrated
with Main #291 once the Issue Worktree/runtime registration is available.

## Internal OpenCode transport (library only)

The helper module `tools/kagari_vnext_opencode_control.py` exposes
`TaskBinding.from_mapping`, `OpenCode.pending`, `status`, and `reply` as
internal functions. **It has no standalone CLI or executable reply entrypoint.**
The only supported human/MUSUBI interface is `kagari-vnext-task` below,
which rechecks the actual #191 Task Record, Worktree and root Session.

Only loopback OpenCode endpoints are supported in this initial MVI. If the
server uses Basic Auth, set `OPENCODE_SERVER_PASSWORD` (and optionally
`OPENCODE_SERVER_USERNAME`) in the process environment. Never store server
credentials or caller-controlled Contract digests in a runtime locator.

The separately built preview Nix package is
`nix build .#kagari-control`, exposing **only**
`bin/kagari-vnext-task`. The unbound helper is not packaged as a public
command.
Every successful response and failure uses machine-readable JSON.
Error result has `status:error` and a fixed `error_code`, exit status 2.
Responses include Task identity and contract revision and preserve the
OpenCode permission's original patterns and question's original options.

Responses are accepted only for a pending upstream request of exactly that
Session and kind. `once` or `reject` are supported for permissions (not
persistent `always` grants). The CLI re-reads pending state after answering
and does not maintain another permission authority, speculate about progress,
or retry possibly delivered answers. A stale/double request returns an error.

## Real integration status and remaining work

Verified:
- OpenCode 1.18.35 server/Session with native Permission and Question.
- API pending request is observable concurrently in optional TUI.
- API reply allows the model to continue.
- New CLI has scoped fake-upstream tests, plus an actual local Permission and
  Question round trip in an independently disposable linked Worktree.

Not yet qualified:
- KAGARI Main #291 creating/refreshing bindings from registered Issue/Worktree
  and started root sessions; duplicate or recovery pathways.
- KAGARI Task Orchestrator completion/Evidence and Arca goal-acceptance cycle.
- MUSUBI display/response using this binary; cross-host support.
- Multiple concurrent Task servers or descendant Ask routing.
- Host install/upgrade/release.

The next integration step is to have #291 supply *this* binding when it starts
an Issue's OpenCode server/root Session, without a second KAGARI Task state
database. If a binding becomes stale, reconstruct its runtime location from
the KAGARI-authoritative Task/Worktree registration and re-observe the upstream
Session, rather than treating the stale file as permission.

No extra contract authorities, generic session manager, direct OpenCode
MUSUBI backend, arbitrary remote endpoint, or monitoring daemon belong in
this slice.

## #291 Main handoff: current Task Record + Worktree + OpenCode Session

An additional **read-mostly** `kagari-vnext-task` executable now resolves
the live binding from the existing v4 #191 metadata ref. It does not accept
a cached JSON file as proof of Task authority: it checks the canonical Task
Record and immutable Contract, the exact Git-registered Issue Worktree and
branch, and the existing OpenCode Session's actual directory **on every
invocation**. A terminal Human disposition refuses the Task.

For daily CLI operation, KAGARI Main may provide a **connection-only**
locator file. It contains these eight Task/runtime locators but **no Contract
digest, grant or decision authority**; every command re-reads the authoritative
#191 Task Record and validates the actual Git Worktree/root OpenCode Session.

```json
{
  "schema_version": 1,
  "project_root": "/abs/repository",
  "worktree": "/abs/repository/.worktrees/123",
  "repository": "example/project",
  "issue": 123,
  "base_revision": "<actual creation-base Git OID>",
  "branch_ref": "refs/heads/issue-123",
  "server_url": "http://127.0.0.1:4096",
  "session_id": "ses_<actual root session ID>"
}
```

```sh
kagari-vnext-task --context /path/from/kagari/runtime-location.json --pretty status
kagari-vnext-task --context /path/from/kagari/runtime-location.json --pretty asks
kagari-vnext-task --context /path/from/kagari/runtime-location.json --pretty ask-show --request-id per_...
kagari-vnext-task --context /path/from/kagari/runtime-location.json --expected-contract-id <full ID shown in asks> --request-id per_... --kind permission --decision once ask-reply
```

The `--pretty` option indents the same machine-readable JSON for human
inspection; leaving it out keeps the compact JSON expected by MUSUBI.
Using `--context` and individual Task location flags together is rejected.

An integrator without a locator file can supply the equivalent exact
arguments directly:

```sh
kagari-vnext-task \
  --project-root /abs/repository --worktree /abs/repository/.worktrees/123 \
  --repository example/project --issue 123 \
  --base-revision <the-Task-Record-creation-base> \
  --branch-ref refs/heads/issue-123 --remote origin \
  --server-url http://127.0.0.1:4096 --session-id ses_... asks
```

The output includes the real `contract_sha256` (= the immutable #191
Contract ID), and pending actual OpenCode request IDs/details. For a permission
answer, pass **the same full Contract ID** using
`--expected-contract-id <64-hex>`, along with
`--request-id per_... --kind permission --decision once ask-reply`.
For a native Question pass
`--request-id que_... --kind question --answers-json '[["Yes"]]'`.
The CLI rejects answers if the canonical Task Record was reauthorized since
the question was observed (`STALE_TASK_CONTRACT`), missing or retired.
No `always` permission or approval bypass is introduced.

This checks matching canonical facts immediately before answering, but is
not a distributed atomic transaction with the upstream OpenCode response.
KAGARI Main is still responsible for pausing affected work on semantic changes,
establishing the actual approved Issue set, starting/supervising/reconciling
the server/root Session, and choosing source metadata remote. This tool
does **not** create those authorities or register Worktrees.

The new Nix preview intentionally exposes **only**
`bin/kagari-vnext-task` (live #191 Record + Git Worktree check).
The earlier unbound `kagari_vnext_opencode_control.py` is an internal
transport/helper and regression test surface; it must not be offered to
MUSUBI as an alternate authorization or state source. This keeps one public
Task/Ask path, with the same source of truth.
Production KAGARI GA remains independent and unchanged.
