# KAGARI（篝）— framework identity and legacy AgentCore compatibility

> **Visible identity:** KAGARI (formerly AgentCore / Agent Core) is the agent execution and governance framework **within KUMIKI**. This page changes product vocabulary only. It does **not** declare new storage, protocol, Git refs, component paths, rights or user-project mutations.

## What is named KAGARI?

**English:** KAGARI is the light within KUMIKI's modular wooden andon structure. It guides accountable agent execution by recording Task boundaries, Human decisions, Work Unit ownership, Planning, evidence and durable Product results.

**日本語:** KAGARI（篝）はKUMIKI（組木）という行灯の構造の中で灯る光です。エージェントの実行、Human Ask、Taskの境界、Work Unit、証拠および成果物の永続化を担うコンポーネントです。

KUMIKI is the repository, generator and distribution framework. KAGARI is an included component, not a second GitHub repository and not a wholesale rename of every `agent-core` runtime.

## Current public release and supported use

The [KAGARI / KUMIKI v0.1.0 bounded GA release](https://github.com/upiscium/KUMIKI/releases/tag/v0.1.0) is publicly available with the Linux/Nix flake package `#kagari`. Its contract is **`GA_BOUNDED_PROFILE_QUALIFIED`**, limited to an ordinary euid-owned real `.git` Project, accepted dependency versions and an externally isolated no-Git-remote Work Unit.

The user-facing flow is `install` / `inspect` / `doctor` / `repair` / `uninstall`; each new Task requires exact package-, Project- and TaskSpec-bound foreground-TTY Human grants. A Task may plan with a real LLM and dispatch two concurrently writing Native children; host tests, an isolated Product-only commit, Native-v4 Evidence and an externally persistent verified binary patch preserve the result before reporting success. Product integration into the original Git repo requires an additional Human action.

The inner Native Task engine remains `TEST_ONLY_UNQUALIFIED`, the standalone source authority is not migrated by this package, and `production_activation=false`. The GA label does not promote arbitrary legacy/v3/v4 internals, cryptographically attest model identity or authorize use in unrestricted Project layouts.

The separate `#kagari-rc` package and `v0.1.0-rc.1` tag are immutable historical release evidence. See [historical pre-release GA readiness record](kagari-v010-ga-readiness.md), [known limitations](kagari-v010-known-limitations.md), and [release tracker](https://github.com/upiscium/KUMIKI/issues/283).

## Technical identifiers that remain unchanged

| Identifier | Classification | Compatibility policy |
|---|---|---|
| `components/agent-core/`, `components/agent-core-v4/` | Source/payload paths | Preserve. Changing a distributed payload requires exact `#159` provenance/release review |
| `agent::...`, `agent-core::...` | Public CLI/Just recipe names | Preserve; consider aliases only through separately reviewed migration contracts |
| `.automation/`, `.automation/UPSTREAM` | Per-project persistence and origin | Preserve schema/path and per-project ownership. Source/consumer **repository values** still require separately reviewed post-cutover migration under #245 |
| `refs/agentcore/metadata`, `agentCoreVersion`, `agentCorePayload` | Security-sensitive metadata/version schema | Preserve bit-for-bit; no decorative rename, hidden authority expansion or schema-version rewriting |
| `AgentCore 1.0.0`, `Templates 4.0.0` in historical plans | Old unshipped version-intent evidence | Preserve as historical plans, not claim they shipped under new names |
| `v0.1.0`, `v0.1.0-rc.1` | Immutable public tags | Retain exact SHA and existing SemVer scope |
| `templates-source`, canonical `upiscium/KUMIKI` | Trusted source publication | Stable CLI name retained; the approved root-owned strict-new Source Authority cutover is complete under #246. Historic `upiscium/Templates` evidence remains immutable |

A generated project may still contain historical **Agent Core VERSION 3** even while the KAGARI 0.1.0 managed Work Unit is externally attached. Their coexistence is deliberate: installing KAGARI must not silently rewrite legacy AgentCore configuration/permissions. Conflicts require a specific scoped Human Ask. A visual name change never grants extra Task, remote-push, source-merge or filesystem authority.

## Planned wording for migration and searchability

In current-facing docs: **KAGARI (formerly AgentCore)** on first mention, **KAGARI** thereafter. In protocol documentation and exact logs use the original symbol unchanged, and explain that it is a stable technical identifier. In historical release notes, source commits, Git refs, receipts and tagged source trees preserve the original spelling.

The reviewed repository cutover is complete: the existing GitHub repository is now `upiscium/KUMIKI`, with the unchanged numeric ID `1183254623`. A separately approved root-owned Host Source Authority also accepts only this new canonical name; old redirect URLs are not authority. Historical release evidence and technical identifiers remain unchanged. See [#242](https://github.com/upiscium/KUMIKI/issues/242), [#243](https://github.com/upiscium/KUMIKI/issues/243), [#244](https://github.com/upiscium/KUMIKI/issues/244), [#245](https://github.com/upiscium/KUMIKI/issues/245), [#246](https://github.com/upiscium/KUMIKI/issues/246).

## Compatibility acceptance

This naming-only change does **not** alter Task ownership, metadata/ref graph, Source Authority, local Work Unit safety fingerprints or release SemVer. Source review must confirm no distribution payload or persistent identifier changes, no generated template drift, current `v0.1.0` tag reproducibility and no effect on the public RC. Any later executable alias is a separate security/release change rather than an implication of this page.
