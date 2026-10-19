# ADR-0004: KAGARI and Project AGENTS.md coexistence with conflict-only Ask

- **Status:** Accepted design (Human, 2026-10-16); local TEST_ONLY consent/apply verified 2026-10-17; production qualification pending
- **Source of approval:** https://github.com/upiscium/Templates/issues/271
- **Related:** ADR-0002, Issue #256, Issue #271; AgentCore v3 adoption/maintenance ownership
- **Priority:** Complete KAGARI/KUMIKI v0.1.0, not expansion of resilience features

## Problem

Existing Project AGENTS.md may be project-authored, AgentCore v3-managed, or a
mixture of both. Native KAGARI execution uses a separate TaskSpec, host-owned
Contract, two actual concurrent OpenCode subagents, tests, scoped host commit
and native-v4 Evidence. Preexisting v3 instructions can require the old
initialize/Just/Task-Orchestrator route, which KAGARI's standalone host does
not run. Overwriting or treating AGENTS.md as optional would both be wrong.

For example, the real MUSUBI/SwitchBoard and TERREATE repositories have
.automation/VERSION = 3, UPSTREAM = github:upiscium/Templates and explicit
ownership.toml entries for AGENTS.md. Their root AGENTS.md bytes are currently
identical; neither matches the bytes of the latest available AgentCore source
template. A VERSION marker alone therefore cannot authorize replacement.

## Decision

**Coexistence is the default. Only a concretely demonstrated instruction
conflict may cause a narrow Human Ask.** Detecting v3 is not a demand to
upgrade. The AGENTS file and all ordinary Project files remain untouched at
default Install, inspect, and diagnosis. Full v3-to-KAGARI migration is a
separate, explicit opt-in operation.

### 1. Independent classification stages

Identify legacy installation from read-only machine-readable evidence:
.automation/VERSION, UPSTREAM, ownership.toml and, where independently
available, exact known source manifest identity. This establishes a **structural
classification**, never cryptographic proof that arbitrary project content
came from Templates. Inspect actual AGENTS instructions separately.

Known conflicts must match precise, active directive text and be paired with
a specific incompatible KAGARI operation. This initial diagnostic checks:
1. v3 mandatory initialize/INIT.md before delegation
2. exclusive Main Issue/Just/READY Task handoff
3. v3 guarded Just-only Git mutation versus scoped host product commit
4. exclusive v3 Task Orchestrator/Task State requirement

Compatible instructions -- leaf no-redelegation, model authority, protected
Product paths and relevant Project checks -- remain fully applicable.
There is no blanket exception to AGENTS.md, Project rules or safety rules.

An unfamiliar instruction or a suspicious custom prohibition is not converted
to an automatic fix from keywords or LLM inference. Return a warning/manual
review result, with NO asserted approved conflict and NO automatic edit.
When recognized v3 conflicts AND uncertain custom prohibitions coexist,
the uncertain result takes precedence: manual-review BLOCKED with no
automatic Ask candidate. This prevents a narrow known-v3 exception from
accidentally dismissing a separate Project-authored prohibition.
An absence of recognized conflicts is **not a proof** of overall compatibility,
especially for nested AGENTS files, unknown rules or unknown effective OpenCode
instruction precedence.

### 2. Precise Human Ask, not implicit consent

For confirmed conflict only, assemble an Ask candidate containing:
- canonical Project/installed KAGARI identity, exact AGENTS preimage SHA-256;
- original line number, verbatim instruction and per-line hash;
- conflicting KAGARI operation, impact and least-scope remediation;
- exact proposed change diff (or explicitly verified runtime-only exception);
- denial outcome: the conflicting KAGARI Task is BLOCKED but safe independent
  Install can coexist; normal legacy v3 use continues unchanged;
- rollback/ownership boundary, effective-instruction and post-change tests.

No consent is inferred from install authorization, Task-tool permission
authorization, untracked file content, a prior test-only flag, an existing
similar addendum, or a model-generated instruction. Any new rights or expanded
scope require a new Ask.

A proposed, unapproved file addendum is a **preview only**: it is not a
runtime grant or automatic insertion point. Since v3 may own AGENTS.md with
replace semantics, a real approved edit must also prove compatibility with
legacy v3 upgrade/maintenance and cannot silently bypass its managed-file
ownership. A KAGARI-only external overlay is preferable if it can be proven
to preserve effective instruction semantics; simply concatenating an OpenCode
role prompt and AGENTS is not by itself proof of precedence or compatibility.

### 3. Narrow activation and reversibility

Any authorized addition must be bound to exact preimage bytes, installation
identity and consent receipt, and must be checked immediately before mutation.
Add only specifically reviewed instructions, never replace unrelated Project
guidance or silent-delete v3 sections.

For normal v3 sessions the prior rules remain unchanged. Only exact
KAGARI-managed sessions may use an approved exception. Leaf edit scope,
model selection, human approval for Push/PR/Merge/Release, and protected Project
configuration remain separately enforced.

On uninstall, remove only exact, unchanged KAGARI-owned content. If a person
has edited managed content or ownership is uncertain, preserve it and issue an
Ask rather than overwrite, reset or guess. Ordinary source, Git history/index,
Task/Record/Evidence remain Project data.

### 4. Separate Install and Task gates

Install may succeed with no root AGENTS modification even when a native Task
would conflict with v3 instructions. Task startup revalidates the applicable
rules and actual approved exception. No valid consent or changed preimage means
that Task is BLOCKED with the exact reason; do not reroute or silently bypass.

The Project's OpenCode permission.task = deny issue has a distinct install-time
permission manifest/Ask gate; it is NOT solved by changing AGENTS.md, and
permission approval alone does not waive AGENTS instructions.

## First implementation and limits

The source-local, read-only tools/kagari_v010_agents_compat.py is an initial
deterministic inspector. It cannot apply changes, authenticate consent, assert
general instruction compatibility, or enforce final runtime precedence. It
reports confirmed exact known-rule conflicts, suspect custom rules for manual
review, machine-readable identity and proposed exact diff without mutating
Project. The current TEST_ONLY native generic executor invokes it before
model execution, failing closed on known conflicts or uncertain prohibitions.

A separate **local interactive TEST_ONLY Human Ask prototype** is now
implemented in tools/kagari_v010_human_ask.py. It generates two independent
preimage- and runtime-bound requests for the AGENTS-scoped addendum and
KAGARI-only native Task permissions, requires an actual foreground local TTY
and exact typed request ID, persists the local decision, applies only an
approved exact AGENTS patch, validates grant and revocation, and can restore
original AGENTS bytes when the owned patch is still unchanged. The native
Task executor has a matching two-grant admission and revalidation path,
and v4 Contract/Evidence binds grant identities.

This is **not** ChatGPT/Work Human Ask, not cryptographic identity proof,
not a production Admin installer, and not sufficient to assume legacy v3
runtime instruction precedence is qualified; it remains a reviewed
development candidate with real Clone-only test authorization. The
unqualified Nix distribution now also carries the local Ask CLI. A
human reply conveyed through a non-authenticating script/TTY is not proof of
a specific person's authorization. No real Project approval is inferred.

A verified install/Task boundary must eventually wire these results to
versioned consent, optional safe applied changes, scope-limited runtime roles,
real model instruction-handoff tests, and uninstall restoration.

## Acceptance before marking implemented

- No AGENTS edit/Ask merely because v3 version marker exists.
- Exact legacy v3 clauses trigger only the relevant Task Ask with grounded
  evidence, never a complete file replacement.
- Unrelated text, quoted examples and uncertain rules cause no false
  actionable Ask.
- Refusal preserves the Project and blocks only affected KAGARI Task.
- Malformed, linked or unknown owner files cannot be silently rewritten.
- Explicit scoped approval, exact preimage checks and reversible ownership
  govern optional change/reinstall/repair/uninstall.
- Real disposable MUSUBI and TERREATE clones demonstrate coexistence before
  final runtime qualification, with all original Project code, Git/index,
  v3 files, and v3 normal-session behavior preserved.
