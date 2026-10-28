# KAGARI vNext — Repository Main singleton / eligibility preview (#291)

This is a **source-only opt-in controller candidate**, not a running or
production-ready Main Orchestrator. It controls only one process-local
ownership lease for an exact ordinary Git repository and a bounded
*input* set of Issue numbers. The v0.1.0 independent-Clone GA,
existing Task data, Git refs, and OpenCode Sessions are unchanged.

## Trust and ownership boundaries

The user or an external trusted host must explicitly choose
`--profile linked-worktree-mvi-preview`, a canonical primary Git
`--project-root`, canonical owner-only private `--state-home`
(mode `0700`), exact GitHub `--repository owner/name` matching the
local `remote.origin.url`, and a bounded `--eligible-issues` set of
positive numbers. The set is an eligibility *input*, not a permission
grant. The Main must separately verify live GitHub Issue title/body,
an approved immutable Contract, exact Worktree/Task Record and
granted execution before launching any Task. This preview does **none**
of those operations.

This initial candidate is **Linux single-host only**, local
filesystem only, and supports an ordinary primary Git repository.
It does not promise multi-host coordination, distributed leader
election, remote GitHub verification, dynamic Issue discovery, or
production-ready crash recovery.

The controller opens the Project and .git directories using
no-follow directory FDs, validates their device/inode against
canonical paths, rejects a linked Worktree and included Git config,
and binds the exact repository origin. Git observation uses a
root-managed executable with system/user Git config, unsafe
environment variables, hooks and fsmonitor disabled. No network,
project writes, ref updates, Branch/Worktree allocation or OpenCode
subprocess is performed.

One live controller locks the actual Project-root directory and
the corresponding host-private lock file. The root lock prevents
accidental split brain when different state directories are passed
for the same local repository. The host must use **one stable state
home across restarts**. A saved PID is *only an observation* and never
an authority or a reason to kill another process.

## CLI shape

From a separate checked-out KUMIKI source tree (not yet in the
shipped Nix package), with Python 3 and a suitable system Git:

```sh
python3 tools/kagari_vnext_main_control.py plan \
  --profile linked-worktree-mvi-preview \
  --project-root /absolute/project \
  --state-home /absolute/private/state \
  --repository example/project \
  --eligible-issues 291 304 --issue 291

python3 tools/kagari_vnext_main_control.py doctor \
  --profile linked-worktree-mvi-preview \
  --project-root /absolute/project \
  --state-home /absolute/private/state \
  --repository example/project --eligible-issues 291 304

# Bounded *preview owner*, not a Main agent or Task executor:
python3 tools/kagari_vnext_main_control.py hold \
  --profile linked-worktree-mvi-preview \
  --project-root /absolute/project \
  --state-home /absolute/private/state \
  --repository example/project --eligible-issues 291 304 \
  --hold-seconds 30
```

`plan`/`doctor` report the current ownership state without
creating Project files or host-owned namespace when absent.
`hold` acquires the exclusive owner lease and reports `HOLDING`,
then releases with `CLEAN_RELEASE` on normal stop. Requests from
another Main for the same exact local Project report
`MAIN_ALREADY_LIVE`, regardless of Issue input or alternate
host-state path.

An interrupted/force-killed owner leaves a private `ACTIVE`
observation record and is **not** automatically resumed or
replaced; `doctor` reports `RECONCILIATION_REQUIRED`. The
separate explicit `reconcile` operation requires the exact
reported record SHA-256, exclusive live locks and unchanged binding
to mark it `RECONCILED_NO_EXECUTION`; it never approves Issues
or starts Tasks. Modified, unknown, symlinked, hardlinked, unsafe,
or staged host state blocks non-destructively.

Responses are machine-readable JSON, explicitly indicating
`github_issue_verification=NOT_VERIFIED`,
`execution_authority=NOT_EXECUTION_AUTHORIZED`,
`task_execution_authorized=false`, and
`model_or_session_started=false`.

## Acceptance / limitations

Local disposable fixtures validate barrier-synchronized concurrent
two-process launch attempts (not guaranteed simultaneous kernel lock acquisition),
cross-state-home singleton enforcement, bounded Issue sets, unchanged
dirty/staged Project state, conflict/drift denial, interrupted Main
reconciliation and strict host record ownership. No consumer
repository is changed by these tests.

```sh
nix develop --command python3 -B -m unittest -v \
  tests.test_kagari_vnext_main_control
```

**Not qualified:** Verified GitHub Issue SoT snapshot and revision
reconciliation (#191), Issue-to-Task scheduling, #286 linked Worktree
allocation, #163 exact-root OpenCode dispatch and #292 Ask routing,
full Main crash/Task-Evidence recovery, multiple live Task Orchestrators,
or #256 complete detachable v4 runtime. Complete #291 MVI acceptance
requires one-Issue E2E followed by a second authorized independent
Issue, with separate Human integration and release approvals.
