# #340 R6B D1B1 R3A — unqualified v2/R2 Host-binding negative-path contract

**SOURCE-ONLY / SYNTHETIC / NON-WIRED / ALL RESULTS DENY / NO ACTIVATION.**

Original development on isolated remote-free
`main@ddabe2c4524d78c07afdfaba378378c2ac8ec37d`.
Draft PR #389 first published
`54fbf20f532ec3294a1d06e722034f1abb54a727`,
then published the reviewed R1 input-hardening corrections at
`583d873b32d8579159579184e3e274226cc7c7d6` (current published HEAD
when R2 began). The R3 D0 companion design is in the same Draft PR.
**R2 one-sided CurrentTask validation-order and documentation corrections
are local-only, NOT committed or pushed**, until separately approved.
They require new exact-published-head CI and PR-mode review after publication.
No Ready/Merge/Deploy/activation, Host mutation, new Journal/genesis,
Claude CLI or model Task, or Main/Transport wiring.

## Narrow implementation

`tools/_kagari_vnext_claude_r6b_phase_d1b1_r3a.py` defines
`assess_r3a_unqualified(...)` and uses the **existing Metadata v2
Python data types**, not a second implementation of Task decoding:

- `RepositoryIdentity`, `RegisteredIssue`, `ApprovedIssue`,
  `V2Selection` and `CurrentTask`. **Every scalar field** of the
  submitted v2 data types is checked for expected primitive type and
  bounded format before any cross-object comparisons; missing slots
  in uninitialized exact-class objects are also `REFUSED`. The canonical
  `task_record.validate_branch_ref` is reused for **every** branch
  component, including intermediate `.lock` cases; frozen dataclass
  class identity alone does not protect fields from in-process mutation.
- `R1` numeric repository ID + positive Issue limits for the **fixed**
  root/reservation identity. No new keying function and no new attempt UUID.
- `R2`'s exact closed report format, exact-string dictionary keys and
  non-grant fields. Custom Python key equality cannot substitute for
  an actual string key.

The input values—including ones with names such as `RepositoryIdentity`,
`CurrentTask`, `main_owner_claim`, `attachment_claim`, or
`r2_report`—are **caller-supplied and UNTRUSTED**. A matching dataclass
never attests a live GitHub numeric ID, active Main, held AttachmentLease,
registered Worktree or complete Journal history.

R3A **does not call** `CurrentTaskReader.observe()`,
`MainSupervisor._assert_owned()`, any `R2` filesystem observer, the
Worktree allocator, `RecoveryJournal.read/create`, Git, HTTP, CLI or
subprocess. Importing the existing v2 observation source follows the
repository's existing top-level v2 class-identity convention; it does not
activate the Main lifecycle.

## Checks (synthetic consistency only)

| Input comparison | Negative outcome |
| --- | --- |
| Exact existing v2 classes and fixed `V2_PROFILE` | `REFUSED` for wrong classes/profile |
| Numeric repository ID, Issue, v2 selection, registered/approved identity | `REFUSED` for invalid/mismatched ID or Issue |
| Repository name across all supplied v2 views, claimed Main, **and current Task** (same numeric ID) | **Malformed** names `REFUSED` before comparison; after registered-Task and both CurrentTask numeric IDs/bindings pass, valid-name rename/alias drift is `UNKNOWN`, **no re-key**. A concurrent name drift never hides a wrong Issue/Record/Contract |
| Malformed fields within otherwise exact-class v2 instances | `REFUSED` before user-defined equality methods are reached |
| Registered vs approved base/branch; **each** current snapshot vs selected numeric ID/Issue and registered record/contract/base/branch | `REFUSED` for mismatches in either snapshot, even when the snapshots also differ |
| Two supplied current `CurrentTask` observations | **Every available, valid-shaped snapshot** is bound to the selected numeric ID/Issue and registered Task before missing, incomplete, name-drift or tip-drift classification. A wrong binding in the other snapshot is `REFUSED` even if one is missing; otherwise missing/incomplete is `UNKNOWN`. With two complete bound snapshots, valid name or metadata/archive tip drift is `UNKNOWN` |
| Main owner, Attachment and absence of legacy v1 assignment | Claimed held/clear may proceed to **observation only**; missing/stale/collision blocks |
| Expected `<project>/.worktrees/<issue>` vs reported Worktree | `REFUSED` for lexical mismatch or subclassed `Path`; concrete `Path` only, **no symlink/inode/Git verification** |
| R2 report absent, REFUSED, UNKNOWN or retained reservation | Always missing/blocked/refused; **never permission** |
| Forged extra R2 fields, successful-launch claim or any `True` grant | `REFUSED` |

An uninitialized exact-class `RepositoryIdentity`, `RegisteredIssue`,
`ApprovedIssue` or `V2Selection` is malformed configuration input and is
`REFUSED`. An unavailable or incomplete `CurrentTask` observation is
`UNKNOWN` **only after any available well-formed counterpart is checked
for wrong numeric identity and record/contract/branch/base binding**;
a mismatch in that counterpart is `REFUSED`. An uncertain observation
requires a fresh Host read and is **not** a valid replacement, grant or
proof of absence.

Even in the all-matching case the output is
`OBSERVED_BLOCKED / r2_reservation_claim_blocks_only`, not a Task grant.

Every output contains fixed labels and only:
`qualification=UNQUALIFIED`,
`dispatch_allowed=False`,
`replay_allowed=False`,
`task_completed=False`,
`commit_allowed=False`,
`authenticated_absence=False`,
`effective_policy=UNVERIFIED`.
No caller path, repository name/ID, Contract or secret is echoed.

**Important:** `HELD_CLAIMED` and `CLEAR_CLAIMED` remain *claims*.
Neither a fabricated old/current pair nor a matching R2 dictionary can
promote itself to verified Host authority. R3A does not expose a
`READY` or `AUTHORIZED` state. The R2 report identity is not
cryptographically tied to the v2 record by R3A.

## Deliberate separation from the future effect path

Real Host integration requires all of the following, in separate
reviewed and approved work:

1. An independently verified canonical GitHub numeric repository ID
   bound to the current Main repository and metadata-v2 Task. An
   existing authenticated Host/GitHub adapter may be reused; an App or
   new database is not mandatory for the accepted cooperative profile.
2. Current `CurrentTaskReader.observe()` in a held/verified Attachment
   lease, live Main ownership and exact Worktree/common-dir checks
   at the **actual** Host boundary. `_assert_owned` can reach the
   Main lock namespace bootstrap and **must not** be imported/called as
   a strict R2 read-only operation. Rename/transfer freezes admission
   because the current Main lock is keyed by textual repository name.
3. A separately approved provisioning/genesis operation, with one
   distinct genesis marker and **no consumption** of the one-shot
   execution reservation key. Unknown history, record deletion or
   rollback is never authenticated absence.
4. A distinct explicit Human-approved *first-use* operation under the
   cooperative profile. Only its future Host-owned effect path
   establishes non-retryable O_EXCL reservation success and durable
   readback, rechecks current Main/Task/policy, and—after separate
   Claude CLI and no-tools qualification—may consider one launch.
   R2/R3A results grant **none** of those permissions. Existing
   `CLAUDE_EXECUTOR_UNQUALIFIED` remains effective.
5. Independent account/billing, official binary/version, complete
   settings/hooks/MCP/tools preflight, effect boundary and crash/restart
   evidence before **separately approved** actual execution. Ask/resume,
   malicious same-UID rollback resistance, multi-Host and exactly-once
   remain outside the accepted minimal profile.

## Validation

`tests/test_kagari_vnext_claude_r6b_phase_d1b1_r3a.py` uses literal
in-memory synthetic v2 objects and `Path` values. Tests exercise
repo-ID and name drift (including the current-only old-name case),
**one-sided before/after Issue, repository ID, record, contract, base
and branch mismatches**, including a missing or incomplete counterpart,
ahead of name- and tip-drift classification,
stale current record and tips, record/contract,
owner and Attachment claims, worktree mismatch, mutated exact-class
v2 fields with custom equality traps, malformed and missing-Issue
branch names, intermediate `.lock` branch components, subclassed
`Path`, non-string R2 dict keys, forged permissions and permanent
non-grants.
The assessment has no live GitHub, Main, Journal or CLI operations.
The **static tests** read the R3A module's own source file for AST
checks; imports of existing v2 modules may resolve module paths and
change `sys.path`.
Neither property proves sandboxing of arbitrary in-process Python code.

Offline targeted test command:

```sh
NIX_CONFIG='eval-cache = false' PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_d1b1_r3a
```

This is a **source-only negative-path correctness slice**. It is not
real Host admission, identity attestation, a durable one-shot protocol,
a permission engine, or proof of filesystem race/rollback safety.
