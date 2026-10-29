# KAGARI GA historical Job observation (read-only)

The `show-task` command is bound to the currently attached GA package and
the original Project revision. That is correct for active one-shot Task
authority, but it cannot read an old completed Job after a Project HEAD
advance or a GA package upgrade.

The separate `history-show-task` operation observes a previously recorded
Job **without** making it runnable again:

```sh
/path/to/current/nix/ga-package/bin/kagari-v010-ga history-show-task \
  --project-root /absolute/unchanged/project \
  --state-home /absolute/external/private/state \
  --job-id job-exact-history-id
```

It uses the current immutable GA reader, not executable Python code from the
old package. It validates the current Project's unchanged physical
root/device/inode, the 0700 owned history directory, canonical 0600 Job and
Task request, the historical Nix GA receipt (root, SHA-256 and bounded profile),
TaskSpec bytes, exact previously recorded Human approval, one-shot dispatch
and its approval digest, terminal result identity, and verified durable
Product archive bytes/Evidence before reporting `GA_JOB_COMPLETED`.

A verified complete Job reports the original exact Product commit, result
status/digest and Native Evidence. A terminal BLOCKED result is reported as
blocked; a dispatched Job without result is `GA_JOB_RESULT_UNAVAILABLE`.
An undispatched archived Job reports `GA_JOB_NOT_DISPATCHED_HISTORIC`,
**never READY**. The historical Project HEAD and current Project HEAD are
reported separately.

No `approve-task`, `run-task`, new Ask, task replay, package repair,
source migration, Git mutation, Product patch application, Push, PR, release
or consumer reactivation is performed by this path. The output sets
`historical_read_only=true` and `task_execution_authorized=false`.

The old immutable release must still exist in `/nix/store` and its receipt
SHA must match both the archived Job and exact request. A missing old package,
Project physical identity replacement, incomplete/corrupt Job or archive,
stale approval, mismatched dispatch or unsafe file alias is **BLOCKED**; no
fallback to an unverified manifest or current active permission is allowed.
This is an owner-trusted local history reader, not a hostile same-UID
filesystem sandbox.

Tests use only disposable local Projects and Nix GA package fixtures. A
separately observed historical blog consumer Job with a confirmed Product
Archive and advanced Project HEAD was also checked read-only; do not replay
that historical Job or imply fresh source deployment.
