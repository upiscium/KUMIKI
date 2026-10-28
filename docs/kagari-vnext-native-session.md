# KAGARI #163 — Issue-root native OpenCode Session handoff (vNext MVI)

**Status:** opt-in, isolated source candidate. This is not shipped GA and
not yet a complete KAGARI Main #291 or MUSUBI integration. It requires the
existing #191 canonical Task Record, a Git-registered Issue Worktree and
a separately authorized Issue selected by KAGARI Main. No new Task/Issue
authority is created here.

## Minimal session start

The Nix `.#kagari-control` package exposes `kagari-vnext-session`,
`kagari-vnext-task`, and the opt-in host-side `kagari-vnext-worktree-install`.
The latter is used by the separate `kagari-vnext-host-prepare` opt-in
host preparation entrypoint during explicit linked-Worktree MVI
Install/Repair preparation (Issues #301/#304). This only prepares
the original Project root `.gitignore` and an external host receipt;
it does not change the existing GA package, qualify the complete
installer, or authorize a Task. Main can call the
**host** session launcher as follows; these arguments are Main-side
location inputs, not routine commands for end users.

```sh
kagari-vnext-session \
  --project-root /abs/repository \
  --worktree /abs/repository/.worktrees/123 \
  --repository example/project --issue 123 \
  --base-revision <the-existing-Task-creation-base> \
  --branch-ref refs/heads/issue-123 \
  --expected-contract-id <the-current-64-hex-Contract-ID> \
  --runtime-dir /abs/host-runtime/issue-123 \
  --port 47640 --opencode-bin /abs/bin/opencode
```

On first start, the launcher re-reads the #191 Task Record/Contract and
verifies actual native Git Worktree registration, branch, common directory,
and exact Worktree path. It checks that the local binary is **OpenCode
1.18.35** (the tested version), launches `opencode serve --pure` with
`stdin=/dev/null` and `cwd=<exact Worktree>`, waits for loopback health,
creates one independent **root Session** through the native OpenCode API,
and verifies the ID/directory/parent before returning.

It writes only operational connection information under
`--runtime-dir` as `context.json` (the existing #292 `--context` schema),
plus `process.json` and a log. The Session has native `bash` and `edit`
Permission Ask enabled; **no `--auto`**, extra broad directory access,
or independent Ask/decision broker. Permission policy for autonomous
product edits is a later explicit Main/Task executor concern, not inferred
from the Issue title.

```sh
# The same previously merged public CLI owns Task/Ask observation and answers:
kagari-vnext-task --context /abs/host-runtime/issue-123/context.json status
kagari-vnext-task --context /abs/host-runtime/issue-123/context.json asks
```

On the same Task and live root Session, a repeated `start` returns
`outcome=RESUMED` and the same locator; it does not create another
Session or start another model turn. If `context.json` points to a lost
Session, or `launch.pending.json` remains from an uncertain start, the
helper refuses a duplicate start and reports that reconciliation is
required. A missing or changed immutable Contract also blocks starting
without further side effects.

## Responsibility and remaining work

Main #291 owns Issue eligibility, Worktree assignment through #286,
which Task starts, the actual bounded implementation prompt, model
supervision, progress/Evidence/PR reconciliation, and preserving user
instructions/choices. This helper does not autonomously prompt the model:
the native `prompt_async` API must be called by a separately scoped
authorized Main/Task scheduler. This separation makes it possible to
verify one precise runtime handoff before implementing long-lived Main.

Tests in `tests/test_kagari_vnext_session.py` use genuine local Git
metadata and Worktrees, spawn an isolated OpenCode-compatible HTTP child,
observe identical state and pending Permission from the existing #292 CLI,
test duplicate/reconnect behavior, wrong Issue or Contract, uncertain start,
version mismatch, and two independent Issue-root Sessions. Real OpenCode
1.18.35 model-backed compatibility must also be tested separately; fake
API fixtures are not accepted as full upstream runtime evidence.

No KAGARI v0.1.0 default changes, global scheduler, multi-host leader
election, ambient OpenCode native Task depth-2 privilege, unauthorized
next-Issue launch, or automatic Merge/Release are in scope.
