# KUMIKI / KAGARI — historical pre-cutover identifier inventory

> **Historical snapshot:** this inventory describes the pre-rename `upiscium/Templates` GitHub `main` at `68e997a73bfa7f03661df1399dba7a5ae227b86e`, before the reviewed #246 Source Authority and #245 repository-name cutover. The **current** canonical repository is `upiscium/KUMIKI` (same numeric ID `1183254623`). All counts in this file are **frozen baseline counts**, not a claim about exact counts in current main. External consumers were not exhaustively scanned.

## Scope and explicit non-goals

- Target repository identity: `upiscium/KUMIKI` (same GitHub repository ID). Visible framework name: **KAGARI** (formerly AgentCore), maintained *within* KUMIKI.
- Preserve existing `agent-core`, `agent::`, `.automation`, `refs/agentcore/metadata`, payload schema, historical tag/release semantics, and all approved Task/PR states unless a separate migration explicitly qualifies them.
- **This historical inventory by itself does not change active remote URLs or runtime identifiers**; it makes the difference between human-facing names and authorization-critical identities explicit.
- Detection is an exhaustive UTF-8 scan of non-symlink source files under this clone (excluding `.git`, NUL/binary files and files over 2 MB). Matches are literal/re-pattern occurrences; duplicate generated files are listed independently.

## Summary of source name matches

| Identifier pattern | Occurrences |
|---|---:|
| `component_or_protocol` | 759 |
| `flake_canonical` | 28 |
| `framework_human` | 611 |
| `repo_canonical` | 160 |
| `repo_general_brand` | 451 |
| `source_launcher` | 84 |

**Affected text source files: 269**. Frozen machine-readable source inventory SHA-256: `287fcc9e5978865bdd9dde4d69d6fd1f01d7098b63a91ed0f55252effa210096`.

## Risk classification and action

| Category | Files | Planned action |
|---|---:|---|
| `ACTIVE_CI_AND_DISTRIBUTION` | 2 | GITHUB WORKFLOW: test exact source/consumer distribution after #245 |
| `ACTIVE_NIX_STORE_BUILD_IDENTITY` | 5 | NIX IMMUTABLE/BUILD: verify selectors and outputs before+after #245 |
| `ACTIVE_TOOL_OR_USER_VISIBLE_CLI` | 15 | ACTIVE TOOL/CLI: inspect each occurrence before migrating identity |
| `ACTIVE_UPSTREAM_IDENTITY_245` | 7 | ACTIVE CANONICAL UPSTREAM: migrate only in verified cutover #245 |
| `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 32 | COMPONENT: preserve technical runtime IDs; visible docs may be renamed separately #244 |
| `DISTRIBUTION_MANIFEST` | 2 | MANIFEST: verify upstream/consumer sync after cutover |
| `DOWNSTREAM_RELEASE_PROVENANCE_246` | 2 | EXACT SOURCE/PROVENANCE: release-gate integration migration #246 |
| `GENERATED_DISTRIBUTION_ARTIFACT` | 103 | GENERATED: modify owning component/renderer, not generated artifact directly |
| `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 40 | DOC: safe display text #243/#244; retain active old URL until a separately approved migration of that particular consumer |
| `OTHER_SOURCE` | 3 | OTHER: classify before changing |
| `TEST_FIXTURE_AND_CONTRACT` | 56 | TEST EVIDENCE: update when behavior/contract changes, preserve old historical fixtures |
| `TRUSTED_SOURCE_AUTHORITY_REQUIRES_246` | 2 | PINNED AUTHORITY: separate reviewed Admin bootstrap; no simple string replacement |

## Exact source paths and match counts

Each row is an affected source path on original merged-main before branding-only edits. Columns count occurrences of the canonical repo slug (`repo`), human framework name (`human`), technical component/protocol name (`protocol`), source launcher (`source`) and generic old product word (`Templates`). The `flake` subset counts only Nix `github:` selectors.

| Path | Category | repo | flake | human | protocol | source | Templates |
|---|---|---:|---:|---:|---:|---:|---:|
| `.github/workflows/publish-template-repositories.yml` | `ACTIVE_CI_AND_DISTRIBUTION` | 1 | 0 | 0 | 0 | 0 | 4 |
| `.github/workflows/template-ci.yml` | `ACTIVE_CI_AND_DISTRIBUTION` | 0 | 0 | 0 | 2 | 0 | 0 |
| `.gitignore` | `OTHER_SOURCE` | 0 | 0 | 0 | 1 | 0 | 0 |
| `Justfile` | `OTHER_SOURCE` | 0 | 0 | 0 | 2 | 0 | 0 |
| `README.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 8 | 6 | 31 | 37 | 0 | 42 |
| `components/adapters/base/.automation/adoption.toml` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 0 | 0 | 0 |
| `components/adapters/base/README.md` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 1 | 1 | 1 | 0 | 0 | 1 |
| `components/adapters/cpp-cmake/README.md` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 0 | 0 | 0 |
| `components/adapters/nix/README.md` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 0 | 0 | 0 |
| `components/adapters/python/README.md` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 0 | 0 | 0 |
| `components/adapters/rust/README.md` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 0 | 0 | 0 |
| `components/adapters/typescript-node/.automation/INIT.fragment.md` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 0 | 0 | 0 |
| `components/agent-core-v4/cleanup_resources.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 0 | 1 | 0 | 0 |
| `components/agent-core-v4/collaboration_git.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 2 | 4 | 0 | 0 |
| `components/agent-core-v4/integration_git.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 0 | 3 | 0 | 0 |
| `components/agent-core-v4/metadata_codec.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 1 | 0 | 0 |
| `components/agent-core-v4/metadata_ref.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 4 | 7 | 0 | 0 |
| `components/agent-core-v4/operation_prerequisites.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 3 | 0 | 0 |
| `components/agent-core-v4/task_cleanup.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 1 | 0 | 0 |
| `components/agent-core-v4/task_collaboration.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 11 | 0 | 0 |
| `components/agent-core-v4/task_integration.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 0 | 5 | 0 | 0 |
| `components/agent-core/.automation/INIT.md` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 5 | 0 | 0 | 0 |
| `components/agent-core/.automation/PRISTINE_DISCARD.md` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 0 | 0 | 0 |
| `components/agent-core/.automation/README.md` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 9 | 6 | 0 | 6 |
| `components/agent-core/.automation/UPSTREAM` | `ACTIVE_UPSTREAM_IDENTITY_245` | 1 | 1 | 0 | 1 | 0 | 1 |
| `components/agent-core/.automation/bin/agent_core.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 0 | 0 | 0 |
| `components/agent-core/.automation/bin/automation_upgrade.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 23 | 10 | 0 | 7 |
| `components/agent-core/.automation/bin/git_private_state.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 1 | 0 | 0 |
| `components/agent-core/.automation/bin/init_context.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 4 | 2 | 0 | 0 |
| `components/agent-core/.automation/bin/local_delete.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 2 | 0 | 0 | 0 |
| `components/agent-core/.automation/bin/path_safety.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 0 | 0 | 0 |
| `components/agent-core/.automation/bin/repository_policy.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 0 | 0 | 0 |
| `components/agent-core/.automation/bin/task_contract.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 1 | 0 | 1 |
| `components/agent-core/.automation/bin/task_state_recovery.py` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 0 | 2 | 0 | 0 |
| `components/agent-core/.opencode/agents/build.md` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 0 | 0 | 0 | 1 |
| `components/agent-core/.opencode/agents/maintenance-orchestrator.md` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 0 | 0 | 2 |
| `components/agent-core/.opencode/commands/maintenance-run.md` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 0 | 0 | 0 | 2 |
| `components/agent-core/.opencode/skills/maintenance-orchestration/SKILL.md` | `COMPONENT_SOURCE_OR_VISIBLE_DOC` | 0 | 0 | 1 | 0 | 0 | 2 |
| `distribution/release-identity.json` | `DISTRIBUTION_MANIFEST` | 0 | 0 | 0 | 1 | 0 | 0 |
| `distribution/template-repositories.json` | `DISTRIBUTION_MANIFEST` | 5 | 0 | 0 | 0 | 0 | 5 |
| `docs/adr/0001-trusted-agentteam-and-human-decisions.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 1 | 0 | 0 | 0 | 0 | 1 |
| `docs/adr/0002-detachable-kagari-and-project-ownership.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 1 | 0 | 0 | 0 | 0 | 1 |
| `docs/adr/0003-self-hosted-development-and-recovery.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 1 | 0 | 0 | 0 | 3 | 1 |
| `docs/adr/0004-kagari-agents-coexistence.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 2 | 1 | 3 | 0 | 0 | 3 |
| `docs/adr/0005-v010-delivery-and-resilience-freeze.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 2 | 0 | 1 | 0 | 0 | 3 |
| `docs/adr/README.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 3 | 0 | 1 | 1 | 0 | 3 |
| `docs/agent-core-admin.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 6 | 0 | 0 | 0 |
| `docs/agent-core-autonomous-safe-cleanup.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 1 | 0 | 0 | 5 | 0 | 1 |
| `docs/agent-core-current-invariants.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 2 | 0 | 0 | 1 |
| `docs/agent-core-evidence.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 0 | 2 | 0 | 0 |
| `docs/agent-core-human-approved-integration.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 0 | 3 | 0 | 0 |
| `docs/agent-core-metadata-ref.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 5 | 11 | 0 | 0 |
| `docs/agent-core-operation-prerequisites.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 1 | 4 | 0 | 0 |
| `docs/agent-core-release-gate.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 7 | 13 | 0 | 7 |
| `docs/agent-core-task-collaboration.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 0 | 4 | 0 | 1 |
| `docs/agent-core-task-record.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 3 | 0 | 1 | 5 | 0 | 3 |
| `docs/agent-core-task-view.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 0 | 2 | 0 | 0 |
| `docs/agent-core-versioning.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 27 | 9 | 0 | 12 |
| `docs/agent-template-architecture.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 28 | 12 | 0 | 16 |
| `docs/ci-contract-sharding.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 4 | 0 | 0 | 0 | 0 | 4 |
| `docs/existing-repository-adoption.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 6 | 0 | 0 | 3 |
| `docs/github-template-distribution.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 8 | 1 | 1 | 1 | 0 | 8 |
| `docs/kagari-external-bootstrap.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 3 | 0 | 2 | 4 | 0 | 3 |
| `docs/kagari-v010-agents-compatibility.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 1 | 0 | 0 | 1 |
| `docs/kagari-v010-auto-planning.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 2 | 0 | 0 | 0 | 0 | 2 |
| `docs/kagari-v010-composed-candidate.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 1 | 0 | 0 | 0 | 0 | 1 |
| `docs/kagari-v010-ga-bounded-qualification.json` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 1 | 0 | 0 | 0 | 0 | 1 |
| `docs/kagari-v010-ga-readiness.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 2 | 0 | 0 | 0 | 0 | 2 |
| `docs/kagari-v010-human-ask.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 1 | 0 | 0 | 0 |
| `docs/kagari-v010-known-limitations.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 1 | 0 | 0 | 0 | 0 | 1 |
| `docs/kagari-v010-native-generic-task.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 2 | 0 | 1 | 0 | 0 | 2 |
| `docs/kagari-v010-publication-source.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 1 | 0 | 0 | 0 | 0 | 1 |
| `docs/kagari-v010-rc.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 8 | 1 | 0 | 0 | 0 | 8 |
| `docs/kagari-v4-native-module-bundle.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 5 | 0 | 1 | 0 | 0 | 5 |
| `docs/model-availability.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 2 | 0 | 0 | 0 |
| `docs/opencode-depth2-ask-smoke.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 2 | 0 | 0 | 0 |
| `docs/opencode-policy-ownership.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 0 | 0 | 3 | 0 | 0 | 6 |
| `docs/source-authority-locator.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 1 | 0 | 0 | 0 | 7 | 2 |
| `docs/source-collaboration.md` | `HUMAN_DOCS_WITH_POSSIBLE_EXECUTABLE_EXAMPLES` | 3 | 1 | 2 | 0 | 13 | 6 |
| `flake.nix` | `ACTIVE_NIX_STORE_BUILD_IDENTITY` | 0 | 0 | 0 | 0 | 10 | 0 |
| `just/source.just` | `OTHER_SOURCE` | 0 | 0 | 0 | 0 | 6 | 0 |
| `nix/kagari-v010-existing-smoke-package.nix` | `ACTIVE_NIX_STORE_BUILD_IDENTITY` | 0 | 0 | 0 | 4 | 0 | 0 |
| `nix/kagari-v010-model-smoke-package.nix` | `ACTIVE_NIX_STORE_BUILD_IDENTITY` | 0 | 0 | 0 | 3 | 0 | 0 |
| `nix/kagari-v010-native-generic-package.nix` | `ACTIVE_NIX_STORE_BUILD_IDENTITY` | 0 | 0 | 0 | 6 | 0 | 0 |
| `nix/kagari-v4-module-package.nix` | `ACTIVE_NIX_STORE_BUILD_IDENTITY` | 2 | 0 | 0 | 1 | 0 | 2 |
| `templates/agent-base/.automation/INIT.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 5 | 0 | 0 | 0 |
| `templates/agent-base/.automation/PRISTINE_DISCARD.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-base/.automation/README.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 9 | 6 | 0 | 6 |
| `templates/agent-base/.automation/UPSTREAM` | `ACTIVE_UPSTREAM_IDENTITY_245` | 1 | 1 | 0 | 1 | 0 | 1 |
| `templates/agent-base/.automation/adoption.toml` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-base/.automation/bin/agent_core.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-base/.automation/bin/automation_upgrade.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 23 | 10 | 0 | 7 |
| `templates/agent-base/.automation/bin/git_private_state.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 1 | 0 | 0 |
| `templates/agent-base/.automation/bin/init_context.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 4 | 2 | 0 | 0 |
| `templates/agent-base/.automation/bin/local_delete.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 2 | 0 | 0 | 0 |
| `templates/agent-base/.automation/bin/path_safety.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-base/.automation/bin/repository_policy.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-base/.automation/bin/task_contract.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 1 | 0 | 1 |
| `templates/agent-base/.automation/bin/task_state_recovery.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 2 | 0 | 0 |
| `templates/agent-base/.opencode/agents/build.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 0 | 0 | 1 |
| `templates/agent-base/.opencode/agents/maintenance-orchestrator.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 2 |
| `templates/agent-base/.opencode/commands/maintenance-run.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 0 | 0 | 2 |
| `templates/agent-base/.opencode/skills/maintenance-orchestration/SKILL.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 2 |
| `templates/agent-base/README.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 1 | 1 | 1 | 0 | 0 | 1 |
| `templates/agent-cpp-cmake/.automation/INIT.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 5 | 0 | 0 | 0 |
| `templates/agent-cpp-cmake/.automation/PRISTINE_DISCARD.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-cpp-cmake/.automation/README.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 9 | 6 | 0 | 6 |
| `templates/agent-cpp-cmake/.automation/UPSTREAM` | `ACTIVE_UPSTREAM_IDENTITY_245` | 1 | 1 | 0 | 1 | 0 | 1 |
| `templates/agent-cpp-cmake/.automation/bin/agent_core.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-cpp-cmake/.automation/bin/automation_upgrade.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 23 | 10 | 0 | 7 |
| `templates/agent-cpp-cmake/.automation/bin/git_private_state.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 1 | 0 | 0 |
| `templates/agent-cpp-cmake/.automation/bin/init_context.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 4 | 2 | 0 | 0 |
| `templates/agent-cpp-cmake/.automation/bin/local_delete.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 2 | 0 | 0 | 0 |
| `templates/agent-cpp-cmake/.automation/bin/path_safety.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-cpp-cmake/.automation/bin/repository_policy.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-cpp-cmake/.automation/bin/task_contract.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 1 | 0 | 1 |
| `templates/agent-cpp-cmake/.automation/bin/task_state_recovery.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 2 | 0 | 0 |
| `templates/agent-cpp-cmake/.opencode/agents/build.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 0 | 0 | 1 |
| `templates/agent-cpp-cmake/.opencode/agents/maintenance-orchestrator.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 2 |
| `templates/agent-cpp-cmake/.opencode/commands/maintenance-run.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 0 | 0 | 2 |
| `templates/agent-cpp-cmake/.opencode/skills/maintenance-orchestration/SKILL.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 2 |
| `templates/agent-cpp-cmake/README.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-nix/.automation/INIT.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 5 | 0 | 0 | 0 |
| `templates/agent-nix/.automation/PRISTINE_DISCARD.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-nix/.automation/README.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 9 | 6 | 0 | 6 |
| `templates/agent-nix/.automation/UPSTREAM` | `ACTIVE_UPSTREAM_IDENTITY_245` | 1 | 1 | 0 | 1 | 0 | 1 |
| `templates/agent-nix/.automation/bin/agent_core.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-nix/.automation/bin/automation_upgrade.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 23 | 10 | 0 | 7 |
| `templates/agent-nix/.automation/bin/git_private_state.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 1 | 0 | 0 |
| `templates/agent-nix/.automation/bin/init_context.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 4 | 2 | 0 | 0 |
| `templates/agent-nix/.automation/bin/local_delete.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 2 | 0 | 0 | 0 |
| `templates/agent-nix/.automation/bin/path_safety.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-nix/.automation/bin/repository_policy.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-nix/.automation/bin/task_contract.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 1 | 0 | 1 |
| `templates/agent-nix/.automation/bin/task_state_recovery.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 2 | 0 | 0 |
| `templates/agent-nix/.opencode/agents/build.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 0 | 0 | 1 |
| `templates/agent-nix/.opencode/agents/maintenance-orchestrator.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 2 |
| `templates/agent-nix/.opencode/commands/maintenance-run.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 0 | 0 | 2 |
| `templates/agent-nix/.opencode/skills/maintenance-orchestration/SKILL.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 2 |
| `templates/agent-nix/README.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-python/.automation/INIT.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 5 | 0 | 0 | 0 |
| `templates/agent-python/.automation/PRISTINE_DISCARD.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-python/.automation/README.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 9 | 6 | 0 | 6 |
| `templates/agent-python/.automation/UPSTREAM` | `ACTIVE_UPSTREAM_IDENTITY_245` | 1 | 1 | 0 | 1 | 0 | 1 |
| `templates/agent-python/.automation/bin/agent_core.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-python/.automation/bin/automation_upgrade.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 23 | 10 | 0 | 7 |
| `templates/agent-python/.automation/bin/git_private_state.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 1 | 0 | 0 |
| `templates/agent-python/.automation/bin/init_context.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 4 | 2 | 0 | 0 |
| `templates/agent-python/.automation/bin/local_delete.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 2 | 0 | 0 | 0 |
| `templates/agent-python/.automation/bin/path_safety.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-python/.automation/bin/repository_policy.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-python/.automation/bin/task_contract.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 1 | 0 | 1 |
| `templates/agent-python/.automation/bin/task_state_recovery.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 2 | 0 | 0 |
| `templates/agent-python/.opencode/agents/build.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 0 | 0 | 1 |
| `templates/agent-python/.opencode/agents/maintenance-orchestrator.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 2 |
| `templates/agent-python/.opencode/commands/maintenance-run.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 0 | 0 | 2 |
| `templates/agent-python/.opencode/skills/maintenance-orchestration/SKILL.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 2 |
| `templates/agent-python/README.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-rust/.automation/INIT.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 5 | 0 | 0 | 0 |
| `templates/agent-rust/.automation/PRISTINE_DISCARD.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-rust/.automation/README.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 9 | 6 | 0 | 6 |
| `templates/agent-rust/.automation/UPSTREAM` | `ACTIVE_UPSTREAM_IDENTITY_245` | 1 | 1 | 0 | 1 | 0 | 1 |
| `templates/agent-rust/.automation/bin/agent_core.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-rust/.automation/bin/automation_upgrade.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 23 | 10 | 0 | 7 |
| `templates/agent-rust/.automation/bin/git_private_state.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 1 | 0 | 0 |
| `templates/agent-rust/.automation/bin/init_context.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 4 | 2 | 0 | 0 |
| `templates/agent-rust/.automation/bin/local_delete.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 2 | 0 | 0 | 0 |
| `templates/agent-rust/.automation/bin/path_safety.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-rust/.automation/bin/repository_policy.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-rust/.automation/bin/task_contract.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 1 | 0 | 1 |
| `templates/agent-rust/.automation/bin/task_state_recovery.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 2 | 0 | 0 |
| `templates/agent-rust/.opencode/agents/build.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 0 | 0 | 1 |
| `templates/agent-rust/.opencode/agents/maintenance-orchestrator.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 2 |
| `templates/agent-rust/.opencode/commands/maintenance-run.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 0 | 0 | 2 |
| `templates/agent-rust/.opencode/skills/maintenance-orchestration/SKILL.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 2 |
| `templates/agent-rust/README.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-typescript-node/.automation/INIT.fragment.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-typescript-node/.automation/INIT.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 5 | 0 | 0 | 0 |
| `templates/agent-typescript-node/.automation/PRISTINE_DISCARD.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-typescript-node/.automation/README.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 9 | 6 | 0 | 6 |
| `templates/agent-typescript-node/.automation/UPSTREAM` | `ACTIVE_UPSTREAM_IDENTITY_245` | 1 | 1 | 0 | 1 | 0 | 1 |
| `templates/agent-typescript-node/.automation/bin/agent_core.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-typescript-node/.automation/bin/automation_upgrade.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 23 | 10 | 0 | 7 |
| `templates/agent-typescript-node/.automation/bin/git_private_state.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 1 | 0 | 0 |
| `templates/agent-typescript-node/.automation/bin/init_context.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 4 | 2 | 0 | 0 |
| `templates/agent-typescript-node/.automation/bin/local_delete.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 2 | 0 | 0 | 0 |
| `templates/agent-typescript-node/.automation/bin/path_safety.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-typescript-node/.automation/bin/repository_policy.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `templates/agent-typescript-node/.automation/bin/task_contract.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 1 | 0 | 1 |
| `templates/agent-typescript-node/.automation/bin/task_state_recovery.py` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 2 | 0 | 0 |
| `templates/agent-typescript-node/.opencode/agents/build.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 0 | 0 | 1 |
| `templates/agent-typescript-node/.opencode/agents/maintenance-orchestrator.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 2 |
| `templates/agent-typescript-node/.opencode/commands/maintenance-run.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 0 | 0 | 0 | 2 |
| `templates/agent-typescript-node/.opencode/skills/maintenance-orchestration/SKILL.md` | `GENERATED_DISTRIBUTION_ARTIFACT` | 0 | 0 | 1 | 0 | 0 | 2 |
| `tests/fixtures/README.md` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 0 | 1 | 3 |
| `tests/runtime/README.md` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 0 | 0 | 4 |
| `tests/runtime/runtime_smoke.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 1 | 0 | 0 | 3 |
| `tests/test_adopt_repository.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tests/test_agent_core_admin.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 6 | 3 | 0 | 0 |
| `tests/test_agent_core_admin_fs.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 1 | 0 | 0 | 0 |
| `tests/test_agent_core_automation.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 1 | 8 | 0 | 0 |
| `tests/test_agent_core_release_gate.py` | `DOWNSTREAM_RELEASE_PROVENANCE_246` | 1 | 0 | 0 | 2 | 0 | 3 |
| `tests/test_agent_core_release_identity.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 3 | 24 | 0 | 2 |
| `tests/test_automation_just_paths.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 1 | 13 | 0 | 0 |
| `tests/test_automation_upgrade.py` | `TEST_FIXTURE_AND_CONTRACT` | 4 | 4 | 11 | 55 | 2 | 5 |
| `tests/test_check_agent_core_release_identity.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 6 | 18 | 0 | 3 |
| `tests/test_cleanup_resources_v4.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tests/test_collaboration_git_v4.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 2 | 1 | 0 | 0 |
| `tests/test_evidence_v4.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tests/test_git_private_state.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 76 | 0 | 0 |
| `tests/test_init_contract.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 2 | 19 | 0 | 0 |
| `tests/test_integration_git_v4.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 3 | 0 | 0 |
| `tests/test_kagari_bootstrap.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 1 | 1 | 0 | 0 |
| `tests/test_kagari_v010_agents_compat.py` | `TEST_FIXTURE_AND_CONTRACT` | 1 | 1 | 1 | 2 | 0 | 1 |
| `tests/test_kagari_v010_existing_smoke_package.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 4 | 0 | 0 |
| `tests/test_kagari_v010_human_ask.py` | `TEST_FIXTURE_AND_CONTRACT` | 1 | 1 | 0 | 1 | 0 | 1 |
| `tests/test_kagari_v010_native_generic_task.py` | `TEST_FIXTURE_AND_CONTRACT` | 1 | 1 | 0 | 1 | 0 | 1 |
| `tests/test_kagari_v010_native_package.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 2 | 0 | 0 |
| `tests/test_kagari_v4_bundle.py` | `TEST_FIXTURE_AND_CONTRACT` | 7 | 0 | 0 | 0 | 0 | 6 |
| `tests/test_local_delete.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tests/test_maintenance_lifecycle.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 4 | 25 | 0 | 2 |
| `tests/test_metadata_codec.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 2 | 0 | 0 |
| `tests/test_metadata_ref.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 13 | 0 | 0 |
| `tests/test_nix_adapter.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tests/test_opencode_discovery.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tests/test_opencode_policy.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 5 | 0 | 0 |
| `tests/test_opencode_self_containment.py` | `TEST_FIXTURE_AND_CONTRACT` | 1 | 1 | 0 | 4 | 0 | 1 |
| `tests/test_operation_prerequisites_v4.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tests/test_post_merge_finalization.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 4 | 0 | 0 |
| `tests/test_pristine_discard.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 1 | 6 | 0 | 0 |
| `tests/test_project_bootstrap.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tests/test_render_templates.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 8 | 0 | 0 |
| `tests/test_repository_policy.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tests/test_role_reasoning_contract.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tests/test_source_authority_locator.py` | `TEST_FIXTURE_AND_CONTRACT` | 2 | 0 | 0 | 0 | 2 | 2 |
| `tests/test_source_bootstrap_upgrade_bridge.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 5 | 19 | 0 | 3 |
| `tests/test_source_collaboration.py` | `TEST_FIXTURE_AND_CONTRACT` | 28 | 0 | 0 | 0 | 28 | 32 |
| `tests/test_source_collaboration_e2e.py` | `TEST_FIXTURE_AND_CONTRACT` | 2 | 0 | 0 | 2 | 4 | 2 |
| `tests/test_source_maintenance_finalize_bridge.py` | `TEST_FIXTURE_AND_CONTRACT` | 2 | 0 | 2 | 1 | 0 | 2 |
| `tests/test_source_publication_recovery_bridge.py` | `TEST_FIXTURE_AND_CONTRACT` | 1 | 0 | 0 | 1 | 0 | 3 |
| `tests/test_task_cleanup_v4.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tests/test_task_collaboration_v4.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tests/test_task_contract.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 1 | 3 | 0 | 0 |
| `tests/test_task_contract_recovery.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 4 | 0 | 0 |
| `tests/test_task_contract_refresh.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 3 | 0 | 1 |
| `tests/test_task_integration_v4.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tests/test_task_lifecycle.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 6 | 0 | 0 |
| `tests/test_task_record_v4.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tests/test_task_state_recovery.py` | `TEST_FIXTURE_AND_CONTRACT` | 20 | 0 | 0 | 9 | 0 | 20 |
| `tests/test_task_view_v4.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tests/test_template_distribution.py` | `TEST_FIXTURE_AND_CONTRACT` | 0 | 0 | 0 | 3 | 0 | 0 |
| `tools/adapter_migration.py` | `ACTIVE_TOOL_OR_USER_VISIBLE_CLI` | 0 | 0 | 0 | 0 | 0 | 1 |
| `tools/adopt_repository.py` | `ACTIVE_TOOL_OR_USER_VISIBLE_CLI` | 0 | 0 | 8 | 5 | 0 | 0 |
| `tools/agent_core_admin.py` | `ACTIVE_TOOL_OR_USER_VISIBLE_CLI` | 0 | 0 | 15 | 2 | 0 | 0 |
| `tools/agent_core_release_gate.py` | `DOWNSTREAM_RELEASE_PROVENANCE_246` | 3 | 0 | 1 | 4 | 0 | 6 |
| `tools/agent_core_release_identity.py` | `ACTIVE_TOOL_OR_USER_VISIBLE_CLI` | 0 | 0 | 8 | 14 | 0 | 7 |
| `tools/automation_recovery_bridge.py` | `ACTIVE_TOOL_OR_USER_VISIBLE_CLI` | 0 | 0 | 5 | 21 | 0 | 7 |
| `tools/check_agent_core_release_identity.py` | `ACTIVE_TOOL_OR_USER_VISIBLE_CLI` | 0 | 0 | 5 | 5 | 0 | 4 |
| `tools/ci_contract_runner.py` | `ACTIVE_TOOL_OR_USER_VISIBLE_CLI` | 0 | 0 | 0 | 0 | 0 | 1 |
| `tools/kagari_v010_agents_compat.py` | `ACTIVE_TOOL_OR_USER_VISIBLE_CLI` | 1 | 1 | 2 | 2 | 0 | 1 |
| `tools/kagari_v010_consent_admin.py` | `ACTIVE_TOOL_OR_USER_VISIBLE_CLI` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tools/kagari_v010_existing_project.py` | `ACTIVE_TOOL_OR_USER_VISIBLE_CLI` | 0 | 0 | 0 | 2 | 0 | 0 |
| `tools/kagari_v010_human_ask.py` | `ACTIVE_TOOL_OR_USER_VISIBLE_CLI` | 0 | 0 | 1 | 0 | 0 | 0 |
| `tools/kagari_v010_model_task.py` | `ACTIVE_TOOL_OR_USER_VISIBLE_CLI` | 0 | 0 | 0 | 3 | 0 | 0 |
| `tools/kagari_v010_native_generic_task.py` | `ACTIVE_TOOL_OR_USER_VISIBLE_CLI` | 0 | 0 | 0 | 1 | 0 | 0 |
| `tools/kagari_v4_bundle.py` | `ACTIVE_TOOL_OR_USER_VISIBLE_CLI` | 2 | 0 | 0 | 1 | 0 | 2 |
| `tools/render_templates.py` | `ACTIVE_TOOL_OR_USER_VISIBLE_CLI` | 0 | 0 | 1 | 2 | 0 | 0 |
| `tools/source_authority_locator.py` | `TRUSTED_SOURCE_AUTHORITY_REQUIRES_246` | 1 | 0 | 0 | 0 | 3 | 1 |
| `tools/source_collaboration.py` | `TRUSTED_SOURCE_AUTHORITY_REQUIRES_246` | 1 | 0 | 0 | 0 | 5 | 3 |

## Cutover validation matrix (to execute under #245 and #246)

| Gate | Before rename | After rename | State at this inventory phase |
|---|---|---|---|
| Canonical GitHub repository ID | `upiscium/Templates`, ID `1183254623` | `upiscium/KUMIKI`, same ID | BEFORE only; target absent in account lookup |
| Trusted pinned Source Authority | old-slug-only Nix-installed policy | explicit reviewed transition; strict new-slug authority | #246 BLOCKS cutover |
| `tools/agent_core_release_gate.py` | old canonical repo + HTTP Git URL | migration-qualified new canonical identity | #246 BLOCKS cutover |
| `components/agent-core/.automation/UPSTREAM` and generated copies | old Nix repository/ref/path | new repo/ref with existing component path | #245 pending |
| Nix template selectors / flake | old `github:upiscium/Templates` remains functional | exact `github:upiscium/KUMIKI` resolutions | #245 pending |
| Template distribution workflow / 5 consumer repos | old canonical main publication | new canonical repository, same commits | #245 pending |
| `KAGARI v0.1.0` and `v0.1.0-rc.1` | existing immutable tags/receipts | unchanged tag object SHA & Nix Store identity | preserve before/after |
| Existing Git Task/PR/Issues and user clones | no reset; user worktrees untouched | historical links navigable; explicit clone remotes | #245 pending |
| Human-visible KUMIKI/KAGARI branding | root README and current docs staged | user-approved release naming | #243/#244 in progress |

## STOP / rollback

Do not rename GitHub or mutate pinned Source Authority until #246's exact independently reviewed migration path is authorized and verified. Reject redirects as proof of canonical source identity. Rollback must restore the exact repository-name/authority/UPSTREAM/flake resolution pair, with no force-push/reset or deletion of historical PR/Issue/release evidence.
