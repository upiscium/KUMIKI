# KAGARI v0.1.0 — Native Task / Human Ask / Admin integration

**Status:** local disposable TEST_ONLY_UNQUALIFIED candidate for #271.
**Not** production installation, identity-verified approval, generalized safety
sandboxing, stable release, or permission to alter original MUSUBI/TERREATE.

This bridges the real native-v4 parent + two-child runtime with the existing
independent Human Ask engine and a non-executing package-attachment lifecycle.
It is separate from the older fixed \`existing-smoke\` Admin path.

## Installed layout and ownership

Given two independent inputs — (1) an ordinary local Git ProjectRoot in a
test-only disposable Clone, and (2) a **private 0700** external runtime tree
under a \`kagari-v010-consent-install-*\` temporary parent:

    /tmp/kagari-v010-consent-install-*/           # owner: operator, mode 0700
      runtime/                                   # only native KAGARI attachment
        kagari-v010/
          owner.json                             # mode 0600, exact identities
          package -> /nix/store/...test-only     # immutable external Nix link
      asks-<random-hex>/                         # retained private audit receipts
        agents/                                  # only if exact known conflict
        task-permission/                         # only if Project task:deny

Owner binds canonical Project root/device/inode, expected test-only Nix
package path and separately supplied SHA-256 of its exact receipt, Task source
SHA-256, and the IDs/paths of every required Ask. The immutable Nix package
verifier checks package and tool bytes/modes, historical N1 manifest and
explicit TEST_ONLY_UNQUALIFIED capability metadata without executing payload.
This is a cooperative local test-only trust profile, NOT independently
published distribution authority.

The installer NEVER edits Product files, \`.automation/**\`, root
\`opencode.json\`, \`Justfile\`, \`flake.nix\`, Git HEAD/index/staged data, or
AGENTS.md just to attach KAGARI. Initial permission and AGENTS Ask requests
are **PENDING**, not rights. An unknown/modified custom AGENTS prohibition is
manual-review BLOCKED for Task execution, never an automatic upgrade.

## Exact supported operations

Use a separately reviewed, exact, immutable KAGARI **test-only** Nix output.
The receipt SHA-256 must be provided from a reviewed operator descriptor, not
read blindly from candidate bytes as authorization.

    PACKAGE=/nix/store/<pinned-kagari-0.1.0-native-generic-test-only>
    RECEIPT_SHA256=<independently-reviewed-64-hex>
    PROJECT=/absolute/isolated/disposable-git-project
    RUNTIME=/tmp/kagari-v010-consent-install-XXXXXX/runtime
    ADMIN="$PACKAGE/bin/kagari-v010-consent-admin-test-only"

    "$ADMIN" inspect --test-only \
      --project-root "$PROJECT" --runtime-root "$RUNTIME" \
      --package-root "$PACKAGE" \
      --expected-receipt-sha256 "$RECEIPT_SHA256"

    "$ADMIN" install --test-only \
      --project-root "$PROJECT" --runtime-root "$RUNTIME" \
      --package-root "$PACKAGE" \
      --expected-receipt-sha256 "$RECEIPT_SHA256"

Here \`RUNTIME\` and its private parent must exist already with mode 0700,
and the parent name must have the expected disposable prefix.

Install will return \`INSTALLED_TEST_ONLY\`, required Ask kinds and precise
PENDING status. It does NOT launch a model, generate Human approval, change
AGENTS or silently override \`task: deny\`.

The matching Ask commands (same exact CLI arguments) are:

- \`show-agents\` and \`show-permission\`: show full exact request and effect.
- \`approve-agents\` / \`approve-permission\`: each requires a **real,
  foreground controlling terminal**, then typing \`APPROVE <request-id>\`.
- \`deny-agents\` / \`deny-permission\`: exact typed refusal.
- \`apply-agents\`: independently apply a previously approved exact AGENTS
  addendum after checking original bytes, approved Git history and ownership.
- \`inspect\`: report installed package, current Ask and Task-ready states.
- \`repair\`: restore only an absent known package link if owner and Ask
  inventories are still intact. Never invent fresh grants or auto-reapprove.
- \`uninstall\`: retire pending/denied/unapplied Asks, revoke Task role grant,
  restore exact unchanged KAGARI-owned AGENTS addendum if approved and applied,
  then detach the package owner/link. **If AGENTS was changed by somebody else,
  preserve it and refuse uninstall** instead of overwriting. Old revoked Ask
  receipts remain private and cannot be reapproved. An AGENTS grant that
  was already revoked and whose exact original bytes were restored is not
  treated as active merely because its historical activation record remains:
  uninstall may detach it without a second Project rewrite. Reinstall creates
  a separate fresh Ask generation and requests fresh decisions.

After **separate** real Human approvals for both kinds, a KAGARI-scoped
real Task can be submitted using \`run-task\` with additional exact
\`--task-spec\`, \`--plan\`, \`--opencode\`, \`--output\`, \`--repository\`,
\`--task\` and \`--timeout\`. The wrapper verifies the installed owner and
current scoped Ask grants and executes only its packaged native Task binary,
with existing \`--execute-model --run-tests --disposable-project\` controls and
bound \`--agents-ask-root\`/\`--permission-ask-root\` inputs. It NEVER passes
the old \`--allow-project-task-deny-test-only\` bypass flag or executes a
Project-provided shell command. Task-specific test command arguments are
still separately controlled by the TaskSpec Contract.

## Human authority and test boundary

Two separate decisions are necessary on a Project with **both** an exact
known v3 lifecycle conflict and \`permission.task = deny\`. One approval does
not imply the other. Grant receipts bind installed KAGARI native source bytes,
Project identity/configuration, exact conflict scope and the reply; native-v4
Contract and Evidence revalidate them. This is local TTY self-attestation.
**Neither ChatGPT's Ask UI, a connected identity provider nor a verified
human account is implemented here.** Unit fixtures mocking foreground input
are not real approvals.

The initial positive offline loop is tested against disposable Git fixtures;
the packaged real Nix CLI is also tested without any Human answer:
\`inspect → install(PENDING) → noninteractive approval BLOCKED → uninstall →
revoked audit\`, preserving Git and Project bytes. Real MUSUBI/TERREATE
full-history Clone acceptance with actual *Human* approval and real-model
Task execution **after** this integration remains a separate test gate.

Not covered here: crash interruption across Ask restoration and owner unlink,
same-UID malicious mutations, signed approval identity, external persistent
installer state, guaranteed behavior of other legacy v3 maintenance tools,
or general model sandboxing. Preserve #271 resilience freeze and do not
promote this to production qualification from fixture tests alone.

## Development verification

    KAGARI_V010_NATIVE_GENERIC_PACKAGE=/nix/store/<exact-built-output> \
      nix develop --command python3 -B -m unittest -q \
      tests.test_kagari_v010_consent_admin \
      tests.test_kagari_v010_human_ask \
      tests.test_kagari_v010_agents_compat \
      tests.test_kagari_v010_native_generic_task \
      tests.test_kagari_v010_native_package

The current immutable output receipt must mark
\`human_ask_admin_lifecycle_test_only_supported=true\`,
\`human_ask_admin_production_qualified=false\`,
\`production_activation=false\`, and \`released=false\`.
