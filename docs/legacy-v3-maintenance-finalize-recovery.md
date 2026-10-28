# Legacy v3 maintenance finalization recovery

This runbook is for the legacy Agent Core v3 consumer failure tracked by KUMIKI #306 and
`upiscium/blog-upiscium-dev#18`. It is not a v4 Task lifecycle, a generic Git lock
cleaner, or permission to edit a consumer's Task State directly.

## Recovery stages and authority

1. **Confirm an exact merged maintenance PR.** Record its repository, Task ID,
   PR number, maintenance Task commit, merge commit, and the dead finalizer PID.
   Main and the Task worktree must both be clean; Main must be synchronized
   with the merge commit. Check that the Task remains `initialized`.
2. **Use a trusted committed KUMIKI source checkout** with a pinned full source
   revision. The source-side bridge checks its source tree and implementation
   identity and loads verified module blobs. Never execute an uncommitted,
   hand-edited consumer finalizer as a substitute.
3. **Read-only inspection** of the two specific orphan ref locks:

   ```sh
   just agent-core maintenance-ref-locks \
     /absolute/path/to/consumer-main 15 17 \
     <exact-merged-main-SHA> <exact-task-head-SHA> \
     <confirmed-dead-finalizer-PID> <pinned-KUMIKI-source-revision>
   ```

   The source-side inspector requires canonical Task/PR evidence, a clean
   synchronized Main, stable Task evidence, matching owner/mode/link count and
   contents on both ref locks, and Linux `F_SETLEASE(F_WRLCK)` **exclusive file
   leases** on both pinned inodes. A write lease is granted only if no other
   process has an open descriptor for that file. The helper holds these leases
   through the final metadata/contents check and any approved deletion. It
   returns `INSPECT_ONLY` and a `recoveryDigest`. It does not delete locks.

4. **Separate explicit human approval is required** for removal of precisely
   those two locks. No general cleaning, force-unlock, or manual `rm` is
   authorized. After approval for the exact Digest and identities, call the
   same command with the Digest as its final argument:

   ```sh
   just agent-core maintenance-ref-locks \
     /absolute/path/to/consumer-main 15 17 \
     <exact-merged-main-SHA> <exact-task-head-SHA> \
     <confirmed-dead-finalizer-PID> <pinned-KUMIKI-source-revision> \
     <approved-recoveryDigest>
   ```

   The Digest is a state-comparison token, **not** proof of human approval;
   operator authority must be established independently before invoking the
   mutation. The tool revalidates the Task's committed-file fingerprints,
   tracked/untracked cleanliness, inspection evidence, and immutable Main/Task
   refs while holding terminal locks just before deleting only those two files.
   It never transitions Task State. A partially
   completed two-file deletion must be reported and re-evaluated, not retried
   blindly.

5. **Run source-side `maintenance-finalize`** with the same pinned KUMIKI
   implementation revision only after exact lock recovery. It freezes Task
   evidence *before* verifying it and compares those bytes again under the
   terminal lock; it also checks that the live Task ref/HEAD equals the
   published commit while holding ref locks. A merged Issue may be closed,
   but its other contracted fields must exactly match the frozen Issue.
   Require a completed terminal `merged` state and canonical publication
   evidence. After that,
   maintenance cleanup is a distinct destructive operation requiring another
   human approval.

## Fail-closed environment limitations

The recovery checker uses Linux kernel file leases instead of reading
`/proc/<pid>/fd`, which some hosts restrict even for same-UID processes.
`F_SETLEASE(F_WRLCK)` fails closed if the file has another open descriptor,
if the filesystem does not support leases, or if the caller lacks ownership
or lease permissions. The helper temporarily ignores SIGIO lease-break
notifications and restores the prior handler after closing the leases; it
also requires `F_GETLEASE == F_WRLCK` before any deletion. A competing file
open can break a lease, so this is not permission for concurrent Git work.
Actual same-filesystem synthetic tests on Adam exercised both no-holder
success and another-process-holder rejection without mocking process lists.
Do not disable inspection, use elevated privileges without separate
authorization, substitute an unverified tool, or manually remove the locks.

The inspector also refuses symlinks, hard links, non-0600 files, changed
content, changed inode metadata, a live/reused creator PID, changed Main/Task
refs, and non-matching PR or verification evidence. The `recoveryDigest`
includes both files' content hashes and ownership/mode/size/link-count data.
Immediately before deletion the helper rechecks both pinned FDs and directory
entries for their complete byte contents and original metadata. If a first
unlink has already occurred but a later `unlink` or `fsync` fails, it returns
`PARTIAL_REF_LOCK_RECOVERY` with the precise removed and remaining paths;
**do not retry blindly**. Unix unlink cannot atomically compare path inode
identity at deletion time, so an independently established quiescent repository
maintenance window remains necessary.

## Test coverage

Focused test modules:

- `tests/test_maintenance_lifecycle.py`
- `tests/test_source_maintenance_finalize_bridge.py`
- `tests/test_maintenance_ref_lock_recovery.py`
- `tests/test_task_contract.py`, `tests/test_task_lifecycle.py`
- `tests/test_task_contract_recovery.py`, `tests/test_task_contract_refresh.py`

The source bridge is a legacy recovery artifact only. Do not add this lock
recovery command to ordinary KAGARI template installation or to the v4 Task
execution path.
