# KAGARI 0.1.0 — AGENTS.md read-only compatibility preflight

**State:** source-local TEST_ONLY_UNQUALIFIED candidate under #271.
**Normative decision:** ADR-0004, coexistence-first / confirmed-conflict-only Ask.
**No automatic AGENTS.md write, no migration, no consent, no runtime activation.**

## Inspect existing Git Project

From the isolated KUMIKI development checkout:

    nix develop --command python3 -B tools/kagari_v010_agents_compat.py \
      --project-root /absolute/path/to/git-project

The inspector does not call Git or execute Project commands. It reads only
the regular bounded root AGENTS.md and three machine-readable paths under
.automation. It refuses unsafe symlinks/aliases, oversized files, invalid
UTF-8 or malformed metadata instead of treating them as clean projects.
No source/index/staged/HEAD/Task/Evidence modifications occur.

## Result interpretation

| State | Meaning | Next action |
|---|---|---|
| ASK_REQUIRED_FOR_CONFLICTING_KAGARI_TASK | Exact active known v3 lifecycle directive contradicts a specific KAGARI action | Display bounded remediation and seek a separate Human Ask **before Task execution** |
| REVIEW_REQUIRED_UNCONFIRMED_CONFLICT | A strict suspicious custom prohibition was observed; source/scope unverified | Warn/manual review, no auto-generated edit and no approved grant |
| REVIEW_REQUIRED_UNKNOWN_INSTRUCTIONS | Structurally installed v3 but missing root AGENTS.md | Manual diagnosis; do not guess v3 source bytes |
| NO_EXACT_KNOWN_CONFLICT | No exact covered directive matched | No automatic Ask; **not** proof of generic Task compatibility |
| BLOCKED_UNSAFE_INPUT | Unsafe Git Project/metadata or unreadable AGENTS | Preserve everything; no mutation |

The legacy classification has a separate value such as
V3_STRUCTURALLY_IDENTIFIED, PARTIAL_OR_FOREIGN_AUTOMATION or
NO_AUTOMATION_IDENTITY. The installed system is never determined by AGENTS.md
phrasing alone, and a v3 version marker cannot trigger a migration.

When confirmed v3 conflicts coexist with uncertain custom prohibitions,
the manual-review result takes precedence, so no actionable automatic Ask is
constructed until the custom instruction has been interpreted separately.

Each confirmed conflict includes an exact original AGENTS line, line number,
SHA-256, KAGARI operation, and reasoning. The proposed file delta has
preimage/proposed file SHA-256 and a unified diff. The proposal remains
NOT_APPROVED and apply_available=false even if the Project is identified as
v3. It is not an install authorization, permission grant or trusted receipt.

## Verified real Project inspection (2026-10-16 JST)

MUSUBI / upiscium/SwitchBoard and TERREATE / upiscium/Terreate were inspected
directly, read-only. Both have .automation/VERSION = 3, matching Templates
UPSTREAM and an ownership.toml mapping for AGENTS.md. Their AGENTS.md SHA-256
is 9f6ff442297e46a419c3bf031ba7fc436dde296965d26b6f61e1e629a06e5d0a.
The identical four known conflicts occur at lines 7, 10, 11 and 14:

- V3_MANDATORY_INITIALIZE
- V3_EXCLUSIVE_TASK_HANDOFF
- V3_GUARDED_JUST_MUTATIONS
- V3_EXCLUSIVE_TASK_ORCHESTRATOR

All inspected original files, HEAD, Git index/staged content and status were
verified byte-identical after the inspection. Private proof:
~/.local/state/kagari271/agents-compatibility-20261016/.

## Correct scope of this result

This is a narrow root-file text detector, **not** a complete natural-language
conflict classifier, a compatibility approval, an implementation of Install
or a valid AGENTS change. It does not inspect nested AGENTS/CLAUDE instruction
files or resolve all OpenCode instruction-precedence behavior. It also does not
confirm that an appended exception actually overrides a legacy directive.

The TEST_ONLY native generic Task executor now invokes this read-only
inspection before any model, metadata or Project mutation. An exact known
conflict returns BLOCKED with an AGENTS_COMPAT_ASK_REQUIRED gate and the
unapproved Ask candidate; an uncertain prohibition returns manual-review
BLOCKED without an auto-generated Ask. This is an **enforced refusal**, not
an implemented Human approval workflow. The immutable test-only package
exposes the separate kagari-v010-agents-compat-test-only CLI for inspection.

A separate **local foreground-terminal TEST_ONLY Human Ask engine**
has now been implemented in tools/kagari_v010_human_ask.py. It accepts
explicit typed approval or refusal for a specific unmodified AGENTS
preimage and the separate scoped OpenCode Task permission; it stores local
request/decision receipts, supports exact AGENTS apply/revoke and checks
grants on native Task dispatch. See docs/kagari-v010-human-ask.md.

Remaining engineering steps are to integrate this workflow with the
generic Installer/Uninstaller, establish an identity-verified real Human Ask
delivery and reply mechanism, verify v3 normal-session instruction precedence,
and qualify the exact source/release profile. Local TTY test evidence alone
does not authenticate the approving human, and previous MUSUBI/TERREATE
real-model successes predate the consent-enforcing runtime. No real
production Project has an approved KAGARI addendum installed.

A root file managed by legacy AgentCore v3 may be replaced by v3's own
maintenance workflow, so any actual KAGARI edit requires coordination with
that ownership contract, not a naïve append despite the preview. The
previous MUSUBI/TERREATE native multiagent tests demonstrated model execution,
not an AGENTS compatibility grant.

## Focused offline tests

    nix develop --command python3 -B -m unittest -v \
      tests.test_kagari_v010_agents_compat

The cases cover precise known v3 classification and conflicts, missing/foreign
metadata, non-conflicting v3 rules, custom prohibitions, altered v3 rules
and fenced/indented/HTML-comment/HTML-code examples, symlink/nonregular/invalid
source refusal, exact idempotent diff preview,
unrelated staged-file preservation and zero implicit authority.
