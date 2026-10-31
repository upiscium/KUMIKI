# #304 / #256 — host attachment and Issue allocator cross-component acceptance

This test-only slice combines the **existing** trusted opt-in
`kagari-vnext-attach` host preparation and the **existing** v4-native
`IssueWorktreeAllocator.ensure`, in a disposable real Git Project using
the #191 Task Record fixture. It is not an installation of active Main,
a running OpenCode Session or a consumer repository rollout.

`tests/test_kagari_vnext_attach_issue_integration.py` demonstrates:

- Without `/.worktrees/`, the allocator refuses an otherwise eligible Issue.
- Explicit linked-MVI host Install safely appends the owner ignore rule,
  and two distinct Task Records can each acquire a separate registered linked
  Worktree. Repeated allocator calls resume exactly those worktrees.
- Host Repair and Doctor are idempotent. Uninstall removes the verified host
  attachment only, preserving the owner ignore rule, both Task Worktrees,
  and original Project HEAD/index.
- Conflicting owner ignore negation refuses Install before assignment.
- Broken owner flake/Just/OpenCode settings, unrelated staged/index and
  untracked files are preserved. No Task/Model/Session or permission is
  launched during the fixture.

**Not yet qualified:** Real v4-native Main/Task host start-to-stop lifecycle
and actual runtime entitlement. The existing `kagari-vnext-attach` receipt
correctly reports `runtime_installed=false` and
`task_execution_authorized=false`. A future trusted host must own
preflight → Task eligibility → Main startup/stop/repair ordering, continuously
reconcile this receipt and stop on conflicting state. Do not bypass that gap
by granting execution from a test-only host attachment.
