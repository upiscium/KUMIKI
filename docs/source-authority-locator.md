# KUMIKI host-managed source-authority locator — Issue #228

> **Status: reference implementation and distributable Nix package candidate, not installed or activated.**
> This candidate adds a new package output to `flake.nix` without changing the
> existing source Guard, active pin, or repository/host runtime configuration. In particular,
> it does **not** authorize an ordinary Task to activate its own replacement.

## Problem and trust model

The real `templates-source` executable lives at a reviewed,
**immutable Nix-store output**, not in a candidate Task checkout. An ordinary
Task may edit a newer `tools/source_collaboration.py` as data, but it must not
choose the policy that judges its publication. Hard-coding store paths in every
Task handoff is inconvenient; looking up `templates-source` in `PATH`, a
task-supplied environment variable, mutable `flake.nix` or `Justfile` would
destroy the separation.

The reference `tools/source_authority_locator.py` consumes one
**maintainer-owned descriptor at a path compiled into the host-installed
locator**. No `--descriptor`, environment-based selection, task checkout
lookup, Nix evaluation, fallback to Just, or raw Git publication is provided.
Running this checked-out script as an authority returns `BLOCKED`; it becomes
usable only after a separate, qualified host installation that substitutes
`@HOST_DESCRIPTOR_PATH@` and places the locator itself in an immutable
`/nix/store` output.

The reference implementation is intentionally independent of `dotnix`.
Host tooling such as NixOS/Home Manager may package/configure it later;
KUMIKI does not import that tooling.

## Candidate Nix package

The `source-authority-locator` flake output provides `bin/kumiki-source` with the descriptor location fixed to `/etc/kumiki/source-authority.json`. Build or installation from a mutable Task checkout does not confer publication authority. The host must select an independently approved full source revision and activate its output separately.

## Descriptor v2 (host-controlled, not project-controlled)

An illustrative JSON object is:

```json
{
  "schema_version": 2,
  "repository": "upiscium/Templates",
  "repository_id": 1183254623,
  "revision": "<full reviewed 40-hex Git revision>",
  "output_path": "/nix/store/<32-char-store-id>-templates-source-collaboration",
  "locator_path": "/nix/store/<32-char-store-id>-kumiki-source-authority-locator/bin/kumiki-source",
  "locator_sha256": "<64 lowercase hex SHA-256 of the approved locator executable>",
  "launcher_sha256": "<64 lowercase hex SHA-256 of bin/templates-source>",
  "policy_sha256": "<64 lowercase hex SHA-256 of lib/templates-source/source_collaboration.py>"
}
```

All nine keys are mandatory, with no extras or duplicate keys. Revision and
digests must be populated with **actual independently approved values**, not
the placeholders above. **The locator itself is host-pinned:** a running locator must have exactly the descriptor's `locator_path` and byte SHA-256. Merely building another Nix-store output from a mutable Task checkout cannot confer locator authority, even if that output embeds the same host descriptor path. This is an accidental-selection safeguard under the trusted AgentTeam model, not a defense against privileged agents deliberately replacing the verifier.

The repository ID remains fixed; any future GitHub
slug change (Templates → KUMIKI) requires a separate, reviewed contract and
installed Guard migration, not automatic acceptance of an arbitrary origin.

The host-owned descriptor and its ancestors must be real
directories/regular files (no symlink hops), owned by `root`, not
group/world writable. The **standard Nix store root** is an explicit exception:
`/nix/store` may be `root:nixbld` mode `1775` (group-writable **with sticky
bit**), while the selected output and its children must remain root-owned and
not group/world writable. A non-sticky or world-writable store root fails
closed. The installed reference defaults to checking from `/` and
`/nix/store`. A deployment using user-owned Home Manager state
would require a **separately reviewed** explicit trust/ownership rule, not
blindly passing a user-controlled path on each invocation. Keep the v2
descriptor at a host-controlled root-owned location or in a qualified
immutable Nix output.

The locator checks:

- canonical absolute descriptor/store paths, schema and exact repository;
- an exact host-approved `/nix/store/<hash>-kumiki-source-authority-locator/bin/kumiki-source` identity and SHA-256, in addition to the separately approved source executor output;
- exactly one `/nix/store/<hash>-templates-source-collaboration` output;
- root-owned, regular, non-writable-by-other launcher/policy and ancestors;
- SHA-256 identity of the **locator**, source launcher and installed policy;
- exact approved revision matching the installed `TRUSTED_SOURCE_BASE`;
- launcher references the exact inspected installed policy file;
- no symlink/redirection or mutable fallback; reject missing, stale,
  tampered or mismatched inputs **before invoking** the Guard.

This validates that a chosen **host-declared locator**, source executor bytes,
and approved revision are self-consistent. It is **not an independent proof** that a
maliciously built Nix derivation came from the reviewed Git source. Human
source review, reproducible/attested realization as available, and a trusted
host installer remain prerequisites. No new offline signing key is required.

## Intended installed command

When a separately approved build has substituted a fixed descriptor path
and installed the reference in `/nix/store`:

```sh
<nix-installed-kumiki-source> --check
<nix-installed-kumiki-source> --worktree /absolute/task/root publication-check 228
<nix-installed-kumiki-source> --worktree /absolute/task/root commit 228 <scope-digest> "message"
<nix-installed-kumiki-source> --worktree /absolute/task/root push 228 <expected-head>
<nix-installed-kumiki-source> --worktree /absolute/task/root pr-create 228
<nix-installed-kumiki-source> --worktree /absolute/task/root checkpoint 228 <pr> <expected-head> "body"
```

The locator verifies the static host descriptor and then calls the
**absolute reviewed Nix-store launcher**; the existing `templates-source`
enforces Issue/branch/manifest/index/remote/PR authority. The proposed
selector does not provide any merge, force push, reset, cleanup, host
configuration mutation, or unguarded publication action. Candidate
worktrees are handed off as data only. For a linked worktree, the same
absolute CLI contract applies.

## Activation and rollback — separate human/host decision

1. Review and test the precise KUMIKI source revision, including Guard and
   locator changes, before it can become a host authority.
2. Independently realize that **full revision** into the Nix store; inspect
   `TRUSTED_SOURCE_BASE` and calculate the *actual installed* launcher
   and policy SHA-256 digests.
3. Have the maintainer/NixOS activation layer create the root-owned
   descriptor and immutable locator package with **fixed** descriptor path.
   The mutable source checkout must never supply the selector.
4. Execute `--check`, negative tests, linked-worktree read-only publication
   checks, and post-activation identity/rollback checks.
5. Promote the new host pin as a **separate** explicit operation, retaining
   the prior approved descriptor/output. On missing or mismatched output,
   fail closed; roll back to an independently approved previous host pin.
   Never silently fall back to `path:.`, `just source::*`, or raw Git.

This candidate includes the locator library, unit tests, and a distributable
Nix package output. Actual host installation and pin update, live Adam
integration, end-to-end publisher smoke, and recovery/rollback exercise remain
open acceptance gates for #228. It is intentionally safe to develop in parallel
with #224 (retirement of OpencodeContract) and #232 (Agent model policy),
because it changes none of their existing files.

## Tests

`tests/test_source_authority_locator.py` uses a private synthetic store:
positive exact binding; bad schema/duplicate keys; wrong repo and revision;
launcher/policy digest drift; source base mismatch; symlink replacement;
non-executable launcher; group/world-writable config/parent; non-store
output; and mutable-checkout/CLI selector refusal. The test-only ability
to inject a private root/uid is not an installed CLI feature.
