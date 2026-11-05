# Claude CLI preflight candidate — #340, first slice

**Status: diagnostic source candidate; executor UNQUALIFIED.** This is not a
Claude executor, a host rollout, a terms clearance, or completion of Issue #340.
OpenCode's default dispatch, Task bindings, native Ask/reply/recovery, generated
Agent Core templates, and shipped GA are unchanged. No Claude CLI or SDK is
installed by this change. MUSUBI receives no new session or authority API.

The follow-on [Lane B source-only fixture](kagari-vnext-claude-lane-b.md)
implements the design-r1 personal-only diagnostic contract. Its public dispatch
is inert and its private tests do not qualify this preflight for live execution.
The separate [Main executor-selection gate](kagari-vnext-executor-selection.md)
does not invoke this diagnostic or the private Lane B seam. An explicit Claude
selection is still unqualified; it is not a preflight or model-use grant.

## Boundary

`tools/kagari_vnext_claude_preflight.py` provides a standalone diagnostic. The
optional Linux `kagari-control` Nix output also exposes it as
`kagari-vnext-claude-preflight`. Nothing in Main invokes it automatically or
consumes its result as a grant. Sources are copied explicitly in
`nix/kagari-vnext-control.nix`; the package verifier checks that immutable copy.
There is no generated-template change to render for this standalone slice.

Only an explicitly selected executable is invoked, with `--version` and
`--help`. `--probe-auth` additionally permits `auth status`, whose documented
default output is JSON. The undocumented `--json` variant is not assumed.
The candidate recognizes only version **2.1.293**; this is an exact synthetic
fixture target, not evidence that the real version is integration-qualified.
Help flag presence is recorded, not inferred to mean usable Ask/resume support.

This diagnostic never sends a model prompt, calls login/logout, copies
credentials, retries another account, changes settings, or creates a Task,
Worktree, Session or publication. **Invoking a user-selected executable is not
a sandbox or a guarantee of read-only/offline behavior.** In particular,
official `auth status` can create local CLI state; the documentation does not
guarantee that it is network-free or never refreshes state internally. Thus
auth probing is explicit and requires separately authorized host use. The
implementation itself does not request login, credential refresh, or a model
turn. Tests use fake executables and synthetic profiles only.

## Explicit profile configuration

An operator supplies a regular, bounded JSON file with exactly these fields
(paths below are illustrative, not an instruction to migrate an account):

```json
{
  "schema_version": 1,
  "claude_bin": "/absolute/path/to/official/claude",
  "profiles": {
    "personal": {
      "config_dir": "/home/example/.claude-personal",
      "subscription_type": "pro"
    },
    "company": {
      "config_dir": "/home/example/.claude",
      "subscription_type": "team"
    }
  }
}
```

`personal` is required and is the default. `company` is optional and is selected
only by `--profile company`. Every configured directory must already exist,
be canonical and symlink-free, and be distinct and nonnested. An unusable
configured profile fails closed; remove an unused optional entry from the
operator configuration rather than relying on fallback. Plans are exact
operator assertions from `pro`, `max`, `ultra`, `team`, `enterprise`.
**A plan does not prove account ownership.** The human must establish which
directory actually belongs to them or their employer; no account identifiers
are recorded by this diagnostic. Company preflight is not authorization to
consume employer usage.

This multi-profile schema belongs to the standalone diagnostic's first slice,
not a Task account scheduler. The later #340 scope correction excludes
`~/Documents/Work/` routing from KAGARI entirely; that convenience belongs to
the user's separate interactive shell/Home Manager setup. Lane B accepts only
the `personal` entry with an individual plan; it rejects a `company` entry or
Team/Enterprise plan. It never selects accounts by Worktree location. The
diagnostic's broader schema is retained here without expanding the executor
contract or authorizing employer usage.

The child gets the exact selected `CLAUDE_CONFIG_DIR`, regardless of an ambient
directory or shell alias. Its environment is narrowly constructed. Detected
API-key/token, Anthropic endpoint, Claude override, cloud-provider, proxy,
runtime-injection and related secret-routing keys fail closed even when empty;
their values and arbitrary names are never emitted. Nonselected profiles are
never tried on failure. This does not change bare `claude`, install
`claude-company`, modify Home Manager, or move existing sessions/credentials.

After separate authorization, the operator command shape is:

```text
kagari-vnext-claude-preflight --config /absolute/preflight.json \
  --project-root /absolute/project

kagari-vnext-claude-preflight --config /absolute/preflight.json \
  --project-root /absolute/project --profile personal --probe-auth
```

Do not run these against a real account as part of offline verification.

## Settings and output safety

The scan is limited to the selected profile's `settings.json`, the explicit
project's `.claude/settings.json` and `.claude/settings.local.json`, and Linux
`/etc/claude-code/managed-settings.json` plus bounded `managed-settings.d` JSON
files. Detected helpers, credential/provider overrides, hooks/plugins/MCP or
unrecognized managed sources are rejected, not executed or disabled. Malformed,
unreadable, oversized or symlinked settings are unknown. Settings are not
exported; no credential store, `.credentials.json`, `.claude.json`, or session
history is inspected. Secrets incidentally embedded in settings or CLI output
are never printed or saved.

This finite scan is **not effective-policy proof**: remote/cached policy,
uninspected configuration sources, and mutable paths cannot be certified by
it. Managed restrictions must not be bypassed. The result always reports
`effective_policy: UNVERIFIED`; even a matching auth response is not proof of
future billing, ownership, entitlements, rate-limit availability, or safe Task
execution. Concurrent source changes detected between probes stop the check;
this is not hostile-filesystem isolation or executable provenance attestation.

Each subprocess has a deadline, aggregate stdout/stderr byte cap, closed stdin,
explicit project cwd, no shell interpolation, and bounded process-group
cleanup. Neither raw output nor exception messages enter the result. These
controls do not prevent an untrusted executable from reading files, using the
network or escaping its process group. Only a separately verified, unmodified
official executable is eligible for later host qualification.

**Filesystem trust precondition:** the operator configuration, executable,
project, profiles and managed-settings locations must be on operator-trusted,
responsive local storage. Do not expose this diagnostic as a service accepting
untrusted paths, or point it at FUSE/network/hostile mounts. File/entry/byte
counts and child observation are bounded, but synchronous kernel filesystem
operations do not have an end-to-end wall-clock deadline. A stalled filesystem
can hang a scan before any CLI is started. Supporting such storage would require
a separately supervised scan boundary; it is outside this first candidate.
A killed child stuck in the kernel may also remain unreapable; cleanup failure
is not a successful diagnostic. This limitation must be revisited before any
automatic Task preflight integration.

## Result contract

All results have `schema_version: 1`, `executor: claude-cli`, the selected profile,
a fixed `status`/`reason`, a parsed version or null, observed flag names,
`executor_qualification: UNQUALIFIED`, `dispatch_allowed: false`, and
`effective_policy: UNVERIFIED`. No paths, emails, organization IDs, raw auth
JSON or credential values are output.

| Status | Meaning |
| --- | --- |
| `VERSION_CHECKED` | Candidate version and required help flags matched; authentication was **not checked**. |
| `AUTH_STATUS_MATCHED` | CLI-reported login/method/provider/plan/directory fields matched the explicit configuration, not an independent attestation. |
| `NOT_INSTALLED` | The configured executable cannot be found/accessed. |
| `NOT_AUTHENTICATED` | CLI reported logged out with the documented non-authenticated exit code. Expired/unavailable login never triggers fallback. |
| `UNSUPPORTED_VERSION` | Version or required help flags are outside the candidate contract. |
| `WRONG_BILLING_MODE` | A prohibited override or mismatched reported method/provider/plan was observed. |
| `WRONG_PROFILE` | CLI reported a different config directory. |
| `PROFILE_UNAVAILABLE` | Required profile/directory is unavailable. |
| `INVALID_CONFIGURATION` | The supplied operator configuration or paths are invalid. |
| `UNKNOWN` | Bounded observation failed, was ambiguous, or could not be safely inspected. |

Exit 0 means only `VERSION_CHECKED` or `AUTH_STATUS_MATCHED`; every other result
exits nonzero. **Exit 0 is never an executor readiness or activation gate.**

## Verification and remaining gates

Offline checks:

```sh
python3 -B -m unittest discover -s tests -p test_kagari_vnext_claude_preflight.py -v
python3 -B -m unittest tests.test_kagari_v010_ci_packages -v
python3 -B tools/render_templates.py check
python3 -B tools/ci_contract_runner.py verify
```

Tests cover explicit profile/environment/cwd/argv selection, auth contradictions,
missing/expired/wrong-profile/billing states, settings and secret-output refusal,
bounded malformed/oversized data, subprocess EOF/exit/timeout and no fallback.
These are synthetic tests, not AC-CLAUDE-03/04 real smoke evidence.

Still required under separate scope/authorization:

1. Official binary provenance and exact-version, non-secret real auth-schema
   qualification; host prerequisites and personal/company isolation.
2. Current terms review for the precise local user/host/product use. Official
   unmodified CLI sign-in is distinct from token brokering, but this diagnostic
   does not resolve every product/headless use case or grant legal approval.
3. Task/Contract-bound executor selection, external operation guards, accepted
   turn evidence, real session identity, cancellation and reconciliation.
4. Demonstrable real Human Ask/reply/resume. CLI streaming and `--resume` alone
   are not OpenCode native Ask parity. Until qualified, remain blocked; do not
   fabricate a pending/answered Ask or enable permission bypass flags. Continue
   to use the existing OpenCode path for authorized Tasks, never silently retry
   a Claude Task under another executor.

   Current official [hook documentation](https://code.claude.com/docs/en/hooks#defer-a-tool-call-for-later)
   describes print-mode `PreToolUse` deferral and resume without a new prompt.
   This is a concrete candidate for the later Ask bridge, not KAGARI E2E proof.
   The current diagnostic still refuses hooks/MCP settings. Enabling a reviewed
   exact hook/permission host will require a separate policy boundary rather
   than removing that refusal for arbitrary user/project configuration.
5. Authorized disposable real smoke, same-Task recovery, independent security
   review of that execution path, and comparative evaluation. No automatic
   activation, PR, release, host migration or Issue completion follows here.

Official references checked during design (2026-11-01; mutable documentation):
[CLI reference](https://code.claude.com/docs/en/cli-reference),
[multiple accounts](https://code.claude.com/docs/en/authentication#log-in-with-multiple-accounts),
[environment](https://code.claude.com/docs/en/env-vars),
[settings](https://code.claude.com/docs/en/settings),
[Claude directory](https://code.claude.com/docs/en/claude-directory),
[legal and compliance](https://code.claude.com/docs/en/legal-and-compliance).
