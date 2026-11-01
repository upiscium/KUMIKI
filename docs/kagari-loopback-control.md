# KAGARI #339 — Loopback Control Server (source-only R5)

## Decision and ownership

KAGARI Main (#291) remains the single repository Task authority. This
small HTTP server is only a local transport for caller-independent access,
not another planner, permission database, Task registry or OpenCode backend.
Each approved Issue still owns a canonical Worktree and Task-rooted Session
through #286/#163; existing Task/Contract/Evidence/publication authorities
are not replaced.

The Human deliberately accepts a reduced host threat model:
- Bind the KAGARI API only to IPv4 127.0.0.1.
- No mandatory HTTP client authentication or localhost-token protocol.
- Malicious co-resident host processes/users are out of scope.
- No external network bind, reverse proxy, tunnel or public API is installed.

This is *not* permission to relax model-authored Task operations or Human-owned
publication gates. In-scope routine reads, edits and bounded tests may later be
automatic within isolated workers; restricted Bash, cross-Task effects, Git
mutation and Ready/Merge/Release remain guarded by their action owners.

## R1–R5 transport surface

A caller may construct the opt-in server through
`tools.kagari_loopback_control.create_control_server` and must explicitly own
its lifecycle. No standalone startup command, daemon, port assignment
policy or automatic installation is provided. Tests use an OS-selected port.

| Method/path | Result |
| --- | --- |
| GET /health | Stable local transport health, no authorization assertion |
| GET /tasks/<canonical-positive-id>/status | Read-only status supplied by KAGARI Main's verified provider |
| GET /tasks/<canonical-positive-id>/asks | Read-only pending Permission/Question from #292 via the exact #291 Task-root Session; no Ask reply |
| POST /tasks/<canonical-positive-id>/dispatch | Host-bound #291 Main dispatch; closed JSON request with exact expected Contract ID |
| PUT/PATCH/DELETE (all paths) and unsupported POST | 405; never route to Main dispatch |
| Unknown or malformed endpoint | 404 |
| Unavailable/mismatched provider observation | 503, no inferred Task success |
| Request concurrency limit reached | 503 `SERVER_BUSY`; no provider or Task callback executed |

Status transport permits RUNNING, BLOCKED, COMPLETED or UNKNOWN, but the
current #291 Main bridge deliberately reports only RUNNING, BLOCKED, UNKNOWN.
Idle/absent Session is not evidence of completed Issue. A pending native Ask is
BLOCKED; busy/retry without pending Ask is RUNNING; unknown or not-yet-sent
kickoff is UNKNOWN.

R2's POST body is exactly `{"schema_version":1,"expected_contract_id":"<64-hex>"}`,
with `Content-Type: application/json` and a bounded request size. The frontend
accepts no arbitrary prompt text, role override, model override, shell, path or
approval flag. Same-origin Host/Origin checks prevent external web pages from
causing state-changing localhost requests; this is not an authentication
mechanism and does not reintroduce malicious local-process protection.

`tools.kagari_loopback_main_bridge.create_main_control_server(main)` binds
one existing `RepositoryMain`, whose `observe`/`dispatch` own canonical
Task/Contract/Worktree/Session revalidation. The Main requires a live
`MainSupervisor` and existing #194 capabilities before any dispatch. No new
Task SoT, queue, local receipt, permission broker or independent launcher.

The HTTP result is 202 for a positively accepted first/recovered kickoff,
200 for an already-accepted existing kickoff, 409 for explicit authority or
reconciliation refusal, and 503 for an unknown effect or unverified result.
A lost HTTP response must be re-observed at Main's existing prompt receipt;
the HTTP layer must never blindly retry.

Thread-per-request handling accepts independent requests concurrently, but
the current Main's owner guard serializes dispatch operations. Active model
turns can continue concurrently after their individually verified kickoffs.
R3 tests send concurrent HTTP dispatch for two distinct approved Issues,
confirm distinct registered Worktrees and fake OpenCode root Sessions,
and separately read each Issue's pending Permission through the existing
#291 → #292 API. Repeating either dispatch produces no duplicate kickoff.

R3's read-only Ask endpoint has no independent Ask queue or consent broker.
It requires a current approved Task, verified root Session and Contract,
rejects a foreign Session or Task, and bounds the number and serialized size
of returned native pending requests. It includes original upstream Ask details
for Human inspection but **does not implement replies or grant permission**.
A Task not yet started returns an explicit 409; an uncertain, stale or
unverified Task/Session yields a non-success response. No new authorization
token is introduced for localhost clients per the accepted threat model.

This is not yet a bounded production scheduler, native model-backed two-Issue
E2E or Human Ask answer qualification.

## Acceptance evidence and limitations

Disposable loopback HTTP tests verify exact bind, lack of auth requirement,
status provider boundaries, rejection of mutation/invalid routes, and two
simultaneous Task status requests. These tests prove no network accessibility
beyond the listener's configured address; they do not independently audit
host firewall, forwarding, proxy configuration or browser-mediated requests.

R1–R3 tests use disposable local Git/Metadata and fake OpenCode HTTP workers
for the actual #291 Main handoff. R3 verifies two active independent
fake Sessions with separate registered Worktrees, concurrent HTTP requests,
no duplicate model kickoff and exact Task-bound #292 pending-Ask reads.
These are compatibility fixtures, not actual native OpenCode/model execution.
They do not install KAGARI, run a persistent listener on Adam, answer an Ask,
or edit #291/#317 Worktrees. The real #191 Contract and #291 receipt paths
are reused. Metadata v2 remains independent.

## Next slices (separately qualified)

1. Qualify actual Main owner/host capability wiring and full native #163
   OpenCode version; synthetic tests are not an installed host runtime.
2. Verify exact-version native OpenCode, real two-Issue model-backed E2E,
   including simultaneous worker progress and caller-disconnection. Keep
   duplicate dispatch receipt reconciliation and host-owned grants.
3. Add a separately bounded #292 Ask reply endpoint only when real Human
   decision identity, stale Contract, one-time reply and lost-ACK semantics
   are qualified. Do not silently auto-answer or create a second Ask broker.
4. Independently review exact source, qualify bounded HTTP worker limits,
   then evaluate guarded source publication and separately authorized
   production activation.

## R4 native OpenCode read-only smoke and HTTP method correction

The optional `tests.native_kagari_loopback_smoke` fixture is deliberately
**not discovered by canonical `tests/test*.py` CI**. Explicitly run
`KAGARI_339_NATIVE_SMOKE=1 python3 -B -m unittest -v tests.native_kagari_loopback_smoke`
to start two real OpenCode 1.18.35 `serve --pure` processes bound to
127.0.0.1 under distinct disposable HOME/XDG/workspace directories. The opt-in
module is deliberately outside the canonical `ci_contract_runner` discovery
pattern; the strict unreviewed-Skip policy is unchanged. An explicit invocation
without the opt-in environment or supported OpenCode executable **fails**,
rather than returning a misleading successful Skip. The fixture creates two
native root Sessions with read/edit Ask policies and uses the existing #292
`OpenCode.verify_session`/`pending`/`status` read path. Both processes
must be alive simultaneously with different root Session IDs, and neither
native idle Session may be mistaken for a completed Task. No model prompt,
Task-run authorization, actual Human Ask, provider credential, project
installation or persistent worker process is involved. Fixture-owned
children are terminated on cleanup.

R4 additionally fixes an HTTP transport contract error: R2/R3 had
`do_PUT = do_POST` (and analogous PATCH/DELETE), which inadvertently
allowed non-POST verbs to enter the Dispatch callback for the exact same
URL and valid Contract. New red-before/green-after regression verifies
those three methods return 405 and **do not call Main.dispatch**.
Only POST can request a model Task kickoff; this is distinct from the
API's deliberately unauthenticated localhost threat model.

These are bounded, isolated source/test changes only. Full native model
Task execution, a real Human Ask decision/reply, two-Issue end-to-end
caller-detachment, independent reviewers, activation and publication
remain separate acceptance gates.

## R5 bounded HTTP admission, graceful shutdown and E2E prerequisite gate

The optional local Control Server now requires at most `max_inflight=16`
simultaneous HTTP handlers by default (configurable as an integer 1–64).
Admission uses a non-blocking semaphore *before creating a request thread*;
overflow receives `503 SERVER_BUSY` with `Connection: close` and triggers no
Main/status/Ask callback. Active handlers release their slots even if
callbacks fail. The request-header/body socket timeout is 8 seconds of **I/O
inactivity**, so a client that stops sending bytes eventually releases its
handler. This is **not an absolute elapsed-time deadline**: a slow caller
continuously sending small fragments can occupy its admitted slot longer.
This bounds the number of admitted handler threads, not the duration of a
slow request, OS connection backlog, OpenCode model concurrency, memory
consumption of external callbacks or total Task execution time. Malicious
co-resident processes remain outside the accepted threat model; a future
absolute deadline should be justified by observed nonmalicious operational
needs rather than silently adding a new local-attacker security guarantee.

On shutdown, already admitted request threads are non-daemon and
`server_close()` waits for them. A long-running/blocked Host callback
can therefore delay graceful shutdown; R5 does not forcibly terminate it,
claim its result was canceled or retry an uncertain dispatch. A bounded
operator policy for very long callbacks and host restart remains a
separate integration concern. HTTP client disappearance during a reply
cannot undo a previously accepted kickoff: Main's existing receipts are
the only authority to reconcile an uncertain response.

Red-before/green-after regression checks (1) concurrency configuration
validation, (2) two active requests plus third request 503 without
running its provider, (3) graceful close waiting for an active request,
and (4) abandoned partial HTTP headers eventually releasing capacity.
Existing loopback-only/no-auth and Main/Ask/Dispatch behaviors are retained.
No new local client credentials, global Task scheduler, authority store,
or Worktree state mechanism was added.

**Native model-backed two-Issue E2E prerequisites, still unqualified:**
- An explicitly authorized disposable or approved real GitHub Issue set,
  exact #191 immutable Contract/Task identities and registered Worktrees.
- One live #291 `MainSupervisor` plus host-provided #194
  initial collaboration (GitHub transport/grant), #217 metadata ref/CAS
  capabilities and supported #163 OpenCode 1.18.35 runtime.
- A pinned model and actually working provider access under an approved
  scope. Read-only native server/Session tests do **not** establish this.
- A bounded Human Ask decision path through #292 and a receipt/reconnect
  test that handles unknown delivery without blind retransmission.
- Separate permission for live model turns, actual GitHub/Task effects,
  protected consumer updates and production activation as applicable.

## R9 fresh-client source-only observation qualification (after PR #342)

The approved `#339` source-only Control Server has merged into
`main` (regular PR #342 merge `add1f7e9598807ce03bc8fde63a17ff72337123e`).
No persistent listener, installed service or actual native model Task
has been activated by that source merge.

An isolated post-merge `tests.test_kagari_loopback_main_bridge` regression
now exercises **different Python interpreter processes**, each launched
with `stdin=DEVNULL`, a standalone HOME, no provider/GitHub credentials
and no KUMIKI project import or Main/Task handle. Each subprocess receives
only the 127.0.0.1 port and canonical Issue ID. The initiating Dispatch
HTTP request and `MainSupervisor` owner scope end before either new
process performs fresh `GET /tasks/{issue}/status` and
`GET /tasks/{issue}/asks` requests.

The synthetic #191 Issue/Contract/Worktree fixture and fake #163 OpenCode
processes verify:
- Two separately created client processes recover **the same** started Task,
  exact Contract ID, root Session ID and pending Permission from the
  surviving loopback listener, without any second Task kickoff. The expected
  Session and Permission IDs are first captured through trusted Main observation
  in the parent test; they are **not** passed to either fresh client.
- The fresh subprocesses each run with a distinct, empty temporary HOME/cwd
  **outside** the synthetic repository/metadata/Worktree fixture, and receive
  only the loopback port and Issue ID as explicit arguments. This isolates the
  test inputs but makes no same-UID adversarial access-control claim.
- Two independent Issues can be dispatched in parallel to different fake
  OpenCode Sessions; simultaneous fresh clients see separate Contract IDs,
  root Sessions and pending Ask request IDs, with exactly one fake kickoff
  per Issue.
- Before an Issue has begun, a detached read reports Task `UNKNOWN` and
  `409 ASK_SESSION_NOT_STARTED`, **not** an empty successful Ask queue.
- A transient `503 TASK_OBSERVATION_UNAVAILABLE` or
  `503 ASK_OBSERVATION_UNAVAILABLE` can trigger only a small bounded
  read-only retry inside the *test client*, never model dispatch, Ask reply
  or an override of Main authority.

These tests prove HTTP caller-lifetime separation over real OS processes
with **synthetic** approved Task records and fake OpenCode. The optional
`tests.native_kagari_loopback_smoke` separately proves real OpenCode
1.18.35 server/root Session read behavior without model turns. The two
proofs must **not** be combined into a claim that real GitHub-approved
Tasks or two native model-backed Issues have completed end to end.

### Post-merge dependency and authority boundary

At this checkpoint, #291 B2a host qualification (PR #331) and #337
Metadata v2 Phase 2A *read-only* Host binding (PR #343) were merged, but
the existing #291 Main's write path still relies on v1 MetadataStore
and host-supplied PR-first collaboration. The v2 writer/remote CAS,
protected archive retention, actual #194 publisher grants, #256 active
Main lifecycle, real bounded Issue/Contract fixture, provider/model
and Human Ask reply authority remain independently unqualified.
No local testing invents those missing privileges or changes the v1/v2
metadata boundary.

The next live B2b/B3 milestone therefore requires an **explicitly
authorized disposable repository/Issue set** and exact trusted Host
capabilities before any real model prompts, GitHub writes, Ask reply,
or production listener activation. The test-only R9 work neither
publishes nor activates that milestone.

#317 Native Shell/B2 acceptance remains independent; #332–#334 own automatic
Leaf recovery, durable Inbox and Cleanup.
