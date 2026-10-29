# KAGARI pending native Ask recovery (opt-in vNext)

This is an operator diagnostic for an already registered Issue/Task. It does not create or approve work, alter an OpenCode server, repair its TUI, or replay a consumed Job.

## When a TUI attach does not display an existing Ask

Use the installed opt-in `kagari-vnext-task` with a **trusted, already issued** `--context` locator belonging to the exact Task. Resolve and inspect before acting:

```sh
kagari-vnext-task --context /absolute/trusted/runtime-locator.json binding-resolve
kagari-vnext-task --context /absolute/trusted/runtime-locator.json status
kagari-vnext-task --context /absolute/trusted/runtime-locator.json asks
```

The CLI queries the upstream pending Permission and Question lists at observation time; it does not depend on an earlier SSE Ask event. A pending request must include its exact ID, Session, Task/Issue/worktree/contract binding, operation and options. `task_attention=NEEDS_APPROVAL` means at least one verified pending native Ask was observed. `UNKNOWN` on an active Session with no verified Ask is **not** Task completion.

The optional upstream `opencode attach` TUI may fail to replay an Ask emitted before attachment. This is a separate upstream UI limitation, not proof that the request was answered.

## HTTP errors and disagreeing API surfaces

OpenCode 1.18.35 may expose pending requests through the legacy `GET /permission` even when newer version-specific endpoints return empty lists. If the authoritative legacy endpoint reports HTTP 400 (including malformed `metadata.timeout`), KAGARI reports `UPSTREAM_HTTP`; it must **not** reinterpret the error as an empty list or Task success. Stop and diagnose the original Session through the authorized host. Do not send a guessed or duplicated reply.

## Reply only after Human approval

Re-read the exact `asks` and `ask-show` response immediately before replying. A Human-approved reply must be scoped to the current request and bound contract; only `once` or `reject` is supported for a Permission, and Questions require exact offered answers. The CLI checks the current pending request again and confirms the upstream response. Never substitute `always`, silently approve, or reuse an old reply/contract.

A stale Session, mismatched Worktree/Issue, unavailable API, or ambiguity must fail closed. When KAGARI cannot observe authoritative pending state, retain the Task as unresolved rather than launching a second OpenCode Session or reissuing the Job.
