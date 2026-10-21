# KAGARI 0.1.0 — composed unreleased candidate (#275)

Status: **UNRELEASED_CANDIDATE**, with **no production qualification or publication authority**. This is a local Linux/Nix integration candidate, not a v0.1.0 Release Candidate approval, release tag, or user-installable production distribution.

## What changed relative to the earlier unified smoke-only output

The composed candidate references an immutable full KAGARI Native-v4 test-only Nix package (with actual TaskSpec, model-generated auto Planning, two genuine OpenCode native child roles, scoped Git host commit, Contract/Task Record/Evidence/Task View, Human Ask and the separate Consent Admin). The older unified candidate included only fixed pre-existing smoke and did not contain this native runtime.

The composed package supplies a read-only identity/status/doctor command and an exact fixed Native Consent Admin routing entrypoint. It does not silently grant Task permissions or install into a Project. Runtime authority remains tied to the Native package and to exact per-Project Human Ask grants.

## Supported candidate CLI

The immutable Nix-store wrapper is named kagari-v010-candidate. Its commands are:

- version: report target 0.1.0, native receipt SHA and explicit nonproduction qualification.
- status: report candidate identity and supported operations, without claiming that any particular Project was inspected or permissions were granted.
- doctor: basic Linux profile detection only. No provider auth/model availability check.
- inspect, install, repair, uninstall: forward through the separately packaged native Consent Admin and require explicit --test-only, an absolute Project root, and a private external runtime root.
- show-agents, show-permission, approve-agents, approve-permission, deny-agents, deny-permission, apply-agents: remain exact local foreground TTY-only Human Ask operations. No remote cryptographic human identity attestation.
- run-task: requires --test-only, exact TaskSpec and OpenCode executable/output plus valid independently obtained scoped Human Ask grants. The host still enforces full Project preservation, native delegation, test/commit and retained v4 Evidence. It is NOT authorized for production.

The runtime root must be within an euid-owned, mode-0700 directory immediately under the system temporary directory with the native Consent Admin prefix. The Project and runtime ownership roots must remain separate. Never use a real Project to perform test-only consent or model testing.

## Offline build and audit

From the isolated development B-lane, evaluate nix/kagari-v010-composed-candidate.nix against its pinned nixpkgs flake inputs. The Nix output is a new immutable closure that includes its Native package dependency by Nix reference. The candidate package receipt binds:

- Its own CLI source SHA-256, candidate root and version.
- The exact native Nix path and native receipt SHA-256.
- The native Admin wrapper identity and native Task / auto planner source SHA-256.
- Explicit false release, production activation, production qualification and source publication claims.

The candidate package build invokes version/status/doctor only. It never contacts a model or changes a Project. It does not commit or publish anything in GitHub.

Test source: tests/test_kagari_v010_composed_candidate.py. Tests cover the receipt and exposed version/status/doctor; rejection of Project arguments in read-only metadata commands; rejection of unauthorized Admin attempts without --test-only; and on a disposable Git Project with a separate private runtime root, inspect → install pending grant → owner-aware partial-package Repair → run-task BLOCKED without consent → uninstall → reinstall with a fresh pending Ask generation → uninstall, preserving unrelated staged README/config throughout.

## Experimental durable lifecycle preview (not a release)

The composed package also includes a separate, precisely receipt-bound
\`kagari-v010-durable-preview\` for previewing persistent external ownership
and exercising non-executing Install/Inspect/Repair/Uninstall on **disposable**
consumer Clones with a private \`XDG_STATE_HOME\`-style state root.
It refuses non-disposable model Task execution **and all approve/deny/apply Human Ask operations** (returning BLOCKED) rather than
silently treating the native TEST_ONLY runner as production or creating unused Project instruction grants. See
[Durable Preview](kagari-v010-durable-preview.md). The existing composed
\`kagari-v010-candidate\` / native TEST_ONLY behavior is unchanged.

## Qualification boundaries and next gates

No production-ready installer, signed/published artifact, fully qualified arbitrary existing-Project support, CI of consolidated final source, independent exact-byte security review, or general model-backed run on this newly composed identity is yet established. The underlying native package has an **accepted v0.1.0 Known Limitation**: Session Export rejects data larger than 3 MiB only after writing; temporary disk use during writing is not limited to 3 MiB. See [Known Limitations](kagari-v010-known-limitations.md) and [deferred Issue #276](https://github.com/upiscium/Templates/issues/276). This issue alone is **not a v0.1.0 release blocker**. The previous #271 Human Ask grants are revoked and bound to the old runtime/Clone and cannot be reused.

Next stages require qualifying exact shipping bytes, independent correctness/security review, CI, and separately authorized consumer acceptance plus Human Publication/Merge/Release decisions. The export write-time disk bound is explicitly deferred beyond v0.1.0 per #276. Keep the existing resilience expansion frozen.
