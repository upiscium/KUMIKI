# OpenCode policy ownership and retirement

## Ownership

Each repository using OpenCode owns the minimum correct safety policy and tests
for its own tools, agents, and operations. Templates owns its Agent Core policy,
the policy tests in `tests/test_opencode_policy.py`, and the corresponding
generated-template and distribution checks. The repository-local
`opencode.json` and `.opencode/**` are the configuration surfaces; generated
templates carry the Templates-owned configuration.

There is no cross-repository canonical role, model, effort, prompt, or
permission registry, and retirement does not establish a replacement shared
registry. A repository may use `dotnix/Global` as optional context, but its
minimum safety policy must be complete locally. Safety is not inherited from
Global and does not depend on Global being present, current, or configured.

Intentional duplication is preferable to policy coupling. Coincidentally
identical assignments are independently owned, not synchronized. Consuming
OpenCode, Git, Nix, model inference, or Templates distribution/adoption is a
functional dependency, not a transfer of the receiving repository's policy
ownership. Retired contract assets are not bulk-copied into a new registry.

Templates policy and tests describe Templates-owned behavior only. They do not
encode LocalLLM provider, endpoint, model, or deployment data, nor require
knowledge of a particular downstream repository's provider setup.

## Functional prerequisites and failure behavior

Using OpenCode requires a supported OpenCode version, the tools needed for the
requested operation, and a usable configured model with working credentials.
Repository checks can validate authored policy and generated structure; they
cannot make these host-side prerequisites available or establish that a user's
provider and credentials work.

An unused optional capability being absent is not itself a failure of an
otherwise configured invocation. If the requested operation actually requires
a capability or configured dependency and it is unavailable, report that
operation as unavailable or blocked; do not bypass its policy or claim it ran.
Do not fall back to another model, substitute a model, or retry the same
objective under a different model when the configured model is unavailable.

## Retirement scope and unchanged policy

The retired OpencodeContract dependency/audit is replaced by Templates-owned
safety and policy validation. This makes policy and test ownership explicit; it
does not revise the policy profile. Existing explicit roles, models, effort
settings, prompts, and permissions remain unchanged by this retirement. No
#230 role/model/effort/prompt/permission changes are imported.

Optional capability support tracked separately by #222 is outside this scope.
This document does not implement #222 or establish that optional capability
handling is proven at runtime. This change also does not alter the current
guarded lifecycle or activate/change the staged v4 runtime, and does not
implement #145 or #163.

## Optional-capability design boundary

Discoverability, an agent-name prefix, a hidden flag, or an advisory prompt does
not establish model reachability, quality, read-only authority, or confinement.
Do not add an unconditional `local-*` delegation allowlist. Optional absence
must never remove a required review, verification, evidence, or approval step.

Same-name environment model inheritance and separately named environment
agents are different mechanisms. Before enabling either under #222, demonstrate
the supported runtime's effective configuration and invocation behavior with
isolated Global/project directories and synthetic provider fixtures. Record the
resolved role, model, and permissions; exercise absent optional configuration,
compatible assignments, conflicting Global/project authority, unadmitted
agents, configured-provider failures, and all generated templates. Deleting
model literals alone is not proof of safe inheritance.

A timeout, missing required tool, or invalid evidence from a configured optional
invocation is not an absent assignment. Preserve the failure and apply the
explicit repository-owned recovery policy; this design does not implicitly
authorize another-model retries or future advisory retry semantics. If the
runtime cannot establish safe confinement, keep the existing baseline instead.
A local permission rule is not assumed to be a universal deny ceiling over
agent-specific overrides. Arbitrary hostile executables, credential-stealing
plugins, and administrator-controlled processes are outside a configuration-only
isolation claim. None of these optional mechanisms is implemented here.

## Verification and runtime limits

The documented repository checks cover local policy assertions and generated
or distribution structure. Passing them is not proof of live OpenCode
compatibility, provider/model availability, working credentials, permission
enforcement by a particular installed OpenCode build, or interactive approval
behavior. In particular, the depth-2 Ask procedure in
[`opencode-depth2-ask-smoke.md`](opencode-depth2-ask-smoke.md) remains
**UNVERIFIED** until run in an interactive OpenCode session with the configured
models and approval UI. This retirement does not claim that runtime smoke was
executed.

## Retained legacy root file

The retired root `opencode-contract-permissions.toml` is no longer emitted in
new Agent Core sources, generated templates, or distribution-owned snapshots.
Existing installed copies are inert: the current policy no longer consumes
them.

The current VERSION 3 upgrader manages `opencode.json`, `.opencode/**`, and
`.automation/**`; it does **not** own the root TOML file. Therefore a normal,
explicit VERSION 3 upgrade does not delete a retained copy of that root file.
This task adds no removal migration, Agent Core version bump, upgrade-ownership
expansion, or out-of-band mutation of installed repositories. Automatic cleanup
of existing copies is not promised.

A new distribution-owned snapshot may omit the obsolete file. That does not
change independent deployed or user repositories. Deleting a retained installed
copy, if desired, is separate repository-owner maintenance and must be reviewed
separately; it is not performed by this retirement.
