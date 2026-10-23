# KUMIKI / KAGARI — identity, compatibility, and migration

> **Current identity:** The existing GitHub repository was renamed to **[upiscium/KUMIKI](https://github.com/upiscium/KUMIKI)**, with immutable numeric GitHub repository ID **1183254623**. The separately approved strict Source Authority cutover has completed. This document is descriptive, never an authority to install, push, merge or edit user Projects.

## Identity and meaning / 名称と意味

| Role | Current product name | Historical name | Meaning |
| --- | --- | --- | --- |
| Repository and reproducible development/distribution framework | **KUMIKI（組木）** | `Templates` | Interlocking project structures, Nix/flake environments, template generation |
| Agent execution/governance component inside KUMIKI | **KAGARI（篝）** | `AgentCore` / `Agent Core` | Scoped Work Units, Human decisions, controlled execution, Evidence and durable Product |
| Persistent technical protocol identities | **unchanged** | `agent-core`, `agent::`, `refs/agentcore/metadata`, `.automation` | Preserve compatibility; any breaking revision needs a separate approved contract |

**English:** KUMIKI is the interlocking wooden structure of an *andon* (行灯). KAGARI is the light within that structure, illuminating accountable agent execution.

**日本語:** KUMIKI（組木）は、組木のように開発環境とテンプレートを組み立てる基盤です。KAGARI（篝）は行灯の内側に灯る光をモチーフとし、エージェントの実行・承認・証拠を管理します。

They are related product identities, not a plan to create a second `upiscium/KAGARI` GitHub repository.

## Verified repository transition and stable public release

- GitHub canonical repository: `upiscium/KUMIKI`, repository ID `1183254623`. The old `upiscium/Templates` URL may redirect for user convenience; **redirects are never new publication authority**.
- After [PR #287](https://github.com/upiscium/KUMIKI/pull/287), the reviewed main/source revision is `6e96dc8e5e2cb4748b8fc6a267ddb323560f2133`. The [Source Authority migration #246](https://github.com/upiscium/KUMIKI/issues/246) is complete: a root-owned protected Host Descriptor selects immutable code that accepts only the new canonical repo and rejects the old alias/foreign origins.
- The public [KAGARI / KUMIKI v0.1.0 bounded GA](https://github.com/upiscium/KUMIKI/releases/tag/v0.1.0) tag still points to `68e997a73bfa7f03661df1399dba7a5ae227b86e`; the old `v0.1.0-rc.1` remains fixed at `b0a5c0c7792e008e3959912d42d97e824f5d6715`. Immutable Store outputs and Receipts are unchanged. Do not relabel historical `Templates 4.0.0` / `AgentCore 1.0.0` plans as completed releases.
- **Bounded GA only:** Linux/Nix, owned ordinary Git Project, no-remote isolated Work Unit, fresh exact foreground-TTY Human grants, automatic durable verified Product recovery. The inner standalone Native engine remains `TEST_ONLY_UNQUALIFIED`, `production_activation=false` and downstream Product merge into the original Git repository requires separate Human action.

## Active migration work still outstanding

The repository trust boundary and name have migrated, but **contents of generated templates and installed downstream Projects are not automatically updated**:

- The canonical source `components/agent-core/.automation/UPSTREAM`, six generated template `.automation/UPSTREAM` copies and five published GitHub Template-Agent-* repositories still point at `github:upiscium/Templates`. Tracked under [#245](https://github.com/upiscium/KUMIKI/issues/245); only the component source should be changed and the generated copies rendered through the canonical renderer and verified before publication.
- Existing consumers including MUSUBI, Terreate and other Agent-ready repositories have separately owned `.automation/UPSTREAM` and permissions. Never bulk-edit or apply Git remote changes without each consumer's own guarded migration.
- The repository's GitHub About description and topics are owner-managed metadata; update as a distinct bounded settings action, not an implicit side effect of this docs change.
- Historical PRs, Issues, release receipts and exact `upiscium/Templates` references are retained as history. Stable `agent-core`, `agentCoreVersion`, `agentCorePayload`, `agent::` and `refs/agentcore/metadata` stay unchanged until a reviewed compatibility migration.

## Documentation and change boundaries

Visible human-facing prose should introduce **KAGARI (formerly AgentCore)** and then use KAGARI. Protocol docs, immutable tags, test fixtures, runtime directories and historical event records should retain exact technical identifiers.

See [#244](https://github.com/upiscium/KUMIKI/issues/244) for the separately staged KAGARI component-identity document, [KUMIKI rebrand inventory](kumiki-rebrand-inventory.md) (historical baseline), [#245 downstream migration](https://github.com/upiscium/KUMIKI/issues/245) and [architecture decisions](adr/README.md).

This document provides **no** new approval for Source Publication, GitHub Merge, Host Admin, production deployment, consumer migration or legacy protocol changes.
