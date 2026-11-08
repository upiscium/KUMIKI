# #340 R6B D1B1 R3A — unqualified v2/R2 Host-binding negative-path contract

**SOURCE-ONLY / ISOLATED SYNTHETIC / NON-WIRED / ALL RESULTS DENY / NO ACTIVATION.**

Prepared on isolated remote-free checkout
`main@ddabe2c4524d78c07afdfaba378378c2ac8ec37d`.
`R3 D0` is a local-only companion design, not an already published
artifact. No Commit/Push/PR, no Host mutation, no new Journal/genesis,
no Claude CLI or model Task, no Main or Transport wiring.

## Narrow implementation

`tools/_kagari_vnext_claude_r6b_phase_d1b1_r3a.py` defines
`assess_r3a_unqualified(...)` and uses the **existing Metadata v2
Python data types**, not a second implementation of Task decoding:

- `RepositoryIdentity`, `RegisteredIssue`, `ApprovedIssue`,
  `V2Selection` and `CurrentTask`.
- `R1` numeric repository ID + positive Issue limits for the **fixed**
  root/reservation identity. No new keying function and no new attempt UUID.
- `R2`'s exact closed report format and non-grant fields.

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
| Repository name across all supplied v2 views and claimed Main | `UNKNOWN` for rename/alias drift, **no re-key** |
| Registered vs approved base/branch, current vs registered record/contract/base/branch | `REFUSED` for mismatches |
| Two supplied current `CurrentTask` observations | `UNKNOWN` for changed snapshot/metadata tip/archive tip |
| Main owner, Attachment and absence of legacy v1 assignment | Claimed held/clear may proceed to **observation only**; missing/stale/collision blocks |
| Expected `<project>/.worktrees/<issue>` vs reported Worktree | `REFUSED` for lexical mismatch; **no symlink/inode/Git verification** |
| R2 report absent, REFUSED, UNKNOWN or retained reservation | Always missing/blocked/refused; **never permission** |
| Forged extra R2 fields, successful-launch claim or any `True` grant | `REFUSED` |

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
repo-ID and name drift, stale current record and tips, record/contract,
owner and Attachment claims, worktree mismatch, missing/unsafe R2
observations, forged permissions and permanent non-grants. It has no
live GitHub, Main, Journal or CLI operations.

Offline targeted test command:

```sh
NIX_CONFIG='eval-cache = false' PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_d1b1_r3a
```

This is a **source-only negative-path correctness slice**. It is not
real Host admission, identity attestation, a durable one-shot protocol,
a permission engine, or proof of filesystem race/rollback safety.
