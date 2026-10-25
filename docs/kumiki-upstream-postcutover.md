# KUMIKI repository cutover — generated UPSTREAM migration (#245)

> **Scope:** The existing GitHub repository is `upiscium/KUMIKI` (immutable ID `1183254623`), with its independently managed root-owned Source Authority. This document defines the canonical source and compatibility contract; it does not authorize mutation of existing consumer repositories or rewrite historical evidence.

## Owned source and generated copies

The canonical `components/agent-core/.automation/UPSTREAM` should declare `repository = "github:upiscium/KUMIKI"`. Its `ref = "main"` and `component = "components/agent-core"` remain unchanged. All six `templates/*/.automation/UPSTREAM` copies must be regenerated from this source by the canonical renderer, then checked for byte parity.

The five published Template-Agent repositories remain generated distribution destinations. The distribution manifest descriptions and publisher's current source label should use KUMIKI. The publication token, workflow condition, destination repository IDs, branch names and permissions must not change under this naming migration.

## Existing-project and KAGARI compatibility

Previously installed AgentCore v3 projects often have `github:upiscium/Templates` in `.automation/UPSTREAM`. They must **not** be silently rewritten or treated as foreign solely because the owning GitHub repository was renamed.

The KAGARI 0.1.0 `agents_compat` tool is *read-only structural diagnostics*, **not Source Publication authority**. A known old or new repo-name string only contributes to `V3_STRUCTURALLY_IDENTIFIED`; `source_cryptographically_authenticated=false` remains in force. The classifier never authorizes a Task, grant, Push, PR, Release, installation or Product merge from that classification. Add positive current/legacy tests and preserve a foreign-owner negative case.

## Deferred scopes and publication gates

- The legacy `v0.1.0` GA and `v0.1.0-rc.1` public tagged source, binary Receipts and historical Evidence remain immutable.
- Current `KAGARI` v4/GA source pin strings outside this work unit must be individually reviewed before changing them. Do not global-replace historical evidence.
- Updating five GitHub Template repository **About descriptions** is a separate owner-settings operation; their generated file content should update only after main CI and independently authorized source Merge.
- Existing consumer repositories (MUSUBI, Terreate, blog-upiscium-dev, KASANE and others) require separate exact rights and review.

Before publishing, require exact Scope/Manifest verification with the installed pinned Source Authority, renderer 6/6, distribution manifest validation, focused KAGARI compatibility and automation-upgrade tests, Nix Flake validation, independent security/source review, and explicit exact-scope Human Source Publication approval. Merge and downstream adoption remain separate approvals.
