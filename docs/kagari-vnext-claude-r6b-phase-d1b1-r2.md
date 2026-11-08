# #340 R6B D1B1 R2 — existing Host journal read-only observer

**Status: local source-only candidate; no Commit/Push/PR, no Host setup,
no Claude CLI, no Main/Task wiring, no runtime activation.**
Based on `main@f8bba7cdb55a0fa48ac194ee538724064226214e` after R0/R1
entered main via PR #385. The repository copy is isolated and has no remote.

## Purpose and boundary

R1 added deterministic numeric Repository ID + Issue key/root components
and inert synthetic evidence. R2 takes **one bounded step toward a real
Host integration**: it reads an existing owner-private Linux state path,
then uses the existing `RecoveryJournal.read` to observe a create-once
reservation. It **cannot create the path or reservation** and never returns
a usable permission or Task completion.

`tools/_kagari_vnext_claude_r6b_phase_d1b1_r2.py` exposes
`observe_existing_host_journal(repository_id, issue)`. This is **not
a trusted Main Host adapter**: the numeric repository ID and Issue are still
caller-provided and unauthenticated. There is no live Main owner, approved
Task/Contract, Issue, Worktree, GitHub identity or effective CLI policy
verification. It must not be bound directly to agent-facing authority.

## Existing-only OS state lookup

For the cooperative single-user Linux profile, the observer obtains the
current effective UID and passwd-entry home. It does **not** use the
process's `HOME` or `XDG_STATE_HOME` environment values, arbitrary cwd,
clone/worktree, Git remote spelling or Task UUID.

The *candidate* existing root is:

```text
<passwd-home>/.local/state/kagari/claude-one-shot/<R1 root_component(repository_id, issue)>/
```

It traverses the existing directories using `O_DIRECTORY|O_NOFOLLOW`,
dirfd-relative opens, regular directory + effective UID checks, and
group/world-write rejection. Under `kagari` all components must be
owner-private. `.local` and `state` may be owner-controlled but not
group/world writable, consistent with Main's Host namespace principles.
The owner home path must be absolute and canonical. No `mkdir`,
`chmod`, symlink following, install, repair, alternate-root selection,
persistent binding write or fallback to a fresh directory is performed.
A missing directory returns `UNKNOWN`.

For the final root, it uses `RecoveryJournal` and a single R1
`reservation_key(repository_id, issue)` lookup. That existing API bounds
the record at 16 KiB, disallows symlinks, and checks ownership, mode and
canonical JSON. A pinned root descriptor is compared with the visible root
after the read. **This does not authenticate the directory against an
older boot, detect storage rollback or exclude a concurrent same-UID writer.**
The passwd lookup may invoke host NSS facilities; R2 performs no explicit
HTTP or Claude/Git subprocess operation.

## Closed, always non-grant results

| Observed state | R2 diagnostic |
| --- | --- |
| Invalid numeric repo ID or Issue | `REFUSED` |
| Noncanonical/unsafe home, symlink or unsafe directory | `REFUSED` |
| Missing owner home or Host directory | `UNKNOWN` |
| Existing root, but no reservation key | `UNKNOWN` — **not authenticated genesis** |
| Malformed/symlink record, read error or uncertain history | `UNKNOWN` |
| Existing readable record (even when it claims success/permission) | `OBSERVED_BLOCKED` — **never a dispatch grant** |

All results include
`qualification=UNQUALIFIED`,
`dispatch_allowed=False`,
`replay_allowed=False`,
`task_completed=False`,
`commit_allowed=False`,
`authenticated_absence=False`, and
`effective_policy=UNVERIFIED`.
The returned diagnostic dictionaries are mutable and are **not
Host capabilities**, proof of authentic Task history, or permissions.

## Failures deliberately outside the guarantees

- A newly provisioned empty root cannot be distinguished from a previously
  used root whose reservation was deleted or rolled back. In both cases
  observation is `UNKNOWN`, not permission to launch or a request to repair.
- A same-UID writer can forge a valid canonical record, delete a record,
  replace a root or restore a snapshot. The best this observer can do with
  readable fabricated evidence is **block**, not authenticate.
- R1's numeric repository identity is still Host-asserted, not verified
  against GitHub's canonical ID or the live selected Main. The existing
  Main owner lock is keyed by textual repository name; real rename/transfer
  safety has not been qualified. This source introduces no new GitHub
  App, Ruleset or external service to address it.
- No crash/power-cut proof, cross-root CAS, complete-history authentication,
  at-most-once or exactly-once guarantee. This is a **read-side only** slice.
- No Claude binary/policy/settings/hooks/MCP/account/terms qualification,
  session/Ask lifecycle, output handling, effect-boundary recheck or
  Human launch approval. `CLAUDE_EXECUTOR_UNQUALIFIED` remains in place.

## Isolated synthetic tests

`tests/test_kagari_vnext_claude_r6b_phase_d1b1_r2.py` injects a
**disposable passwd-home path** using mocks; its only writes are setting up
and altering `TemporaryDirectory` fixtures. It covers:

- missing namespace no-create, existing empty root UNKNOWN, stable
  readback and a forged completion record that blocks only;
- deletion, recreation, wrong repo/Issue, unrelated record, malformed JSON
  and symlinked record;
- symlinked/unsafe directory, valid private symlink targets, replaced root
  during read, read-only group-permissive ancestors, invalid numeric IDs,
  bad passwd home and static source-effect checks.

R2's test fixture constructor calls `RecoveryJournal.create` to fabricate
a reservation; **R2 itself never calls it**. These tests do not perform
actual Host configuration changes, real power loss or GitHub/Claude I/O.

Reproduce on the isolated checkout:

```sh
NIX_CONFIG='eval-cache = false' PYTHONDONTWRITEBYTECODE=1 nix develop --offline -c python3 -B -m unittest -q tests.test_kagari_vnext_claude_r6b_phase_d1b1_r2
```

## Next independent acceptance slice

Before any **single** real no-tools Claude Task launch:

1. Define an explicit, separately approved Host provisioning/genesis step,
   with the existing `RecoveryJournal`, fixed numeric repo+Issue root, and
   a manual first-use/recovery decision; **never initialize on missing data
   during observation**. A genesis record in the same root by itself
   cannot resist complete-root rollback or missing-file deletion.
2. Bind the numeric Repository ID, exact selected Task/Contract/Worktree and
   live Main owner to this private root. Recheck at the actual effect
   boundary. Resolve name-keyed Main lock split on real rename/transfer
   within the accepted cooperative single-host profile.
3. Independently qualify actual installed Claude CLI, tools/permission
   settings, account/billing and supported no-tools mode; only then request
   Human approval for one disposable CLI smoke.
4. Retain OpenCode default; no external trust service or GitHub Apps are
   mandatory for the intentionally reduced cooperative profile. Be explicit
   that the accepted local profile cannot guarantee retention against
   malicious/same-UID or storage rollback.
