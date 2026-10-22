# #246 — KUMIKI repository rename: pinned Source Authority migration

> **Status: STRICT POST-CUTOVER SOURCE CANDIDATE STAGED, NOT ACTIVATED.** These changes are data in an isolated Task checkout and **do not** replace the installed source authority, authorize a Github slug change, publish source, or modify a host-owned descriptor. `upiscium/Templates` remains the canonical live name until a separately authorized cutover.

## Exact pre-cutover source and trust baseline (2026-10-22)

| Item | Current observed value |
|---|---|
| Live GitHub canonical owner/repository | `upiscium/Templates` |
| Stable GitHub numeric repository ID | `1183254623` (must remain identical after rename) |
| New approved name | `upiscium/KUMIKI` |
| Current merged `main` | `68e997a73bfa7f03661df1399dba7a5ae227b86e` |
| Public GA / prerelease tags | `v0.1.0` points to `68e997a…`; `v0.1.0-rc.1` points to `b0a5c0c…` |
| Host pinned Guard source revision | `4afd2fbd72e486c4ad433c0c1f6062459ad139a0` |
| Host immutable source publisher | `/nix/store/6gj00a2q2by63pvn5gily7l2m8rh3svd-templates-source-collaboration` |
| Host immutable source locator | `/nix/store/mfwlz7y449idgs7jx4j6ra4imiyjr3ha-kumiki-source-authority-locator/bin/kumiki-source` |
| Host-owned descriptor | `/etc/kumiki/source-authority.json`, schema 2, exact immutable paths/digests |
| Current source-guard identity | Strict `REPO = "upiscium/Templates"`; new slug is not yet accepted |
| Current release-gate identity | Strict `CANONICAL_REPO = "upiscium/Templates"`, old HTTPS `.git` URL |
| Source guard source paths | `tools/source_collaboration.py`, `tools/source_authority_locator.py`, `tools/agent_core_release_gate.py`; all require independent migration review |

## Migration implementation (strict future phase)

The reviewed #246 candidate makes **only these canonical identity changes** in executable source:

1. The future *pinned* `source_collaboration.py` requires `upiscium/KUMIKI` in checked remote identity and GitHub source PR / Issue endpoints. It rejects old `upiscium/Templates` for **new post-cutover publication**. It keeps the existing `SOURCE_AUTHORITY` sensitive-path set, Issue binding, exact change manifest, protected Commit/Push/Draft PR and human decision gates.
2. The future *pinned* `source_authority_locator.py` requires descriptor `repository="upiscium/KUMIKI"`, **`repository_id=1183254623`**, exact reviewed source revision, matching immutable source publisher and locator executable/digest. It cannot be activated by changing an environment variable or mutable checkout; existing root-owned descriptor, pin/owner/type/hash/revision/no-symlink checks are retained.
3. The post-cutover `agent_core_release_gate.py` uses the new **strict** canonical repository and Git URL, with unchanged PR/source/CI provenance checks. A separate **fixed, hash-bound Issue #117 v1 historical exception** retains its **original literal** `upiscium/Templates` evidence (SHA-256 `317dd08ea4840dac4a820043630315ece7c098e13ff77c992af63568442190ce`). This is **not** a new live-remote alias and does not loosen new release evidence.
4. Synthetic source-authority and GitHub workflow tests use the new canonical name, including case-variants, old-slug negative probes, unrelated owners, alternate hosts, URL rewrites, PR mismatches and historical v1 immutable fixture preservation.

This is a **strict-new-only candidate**, not a dual-identity production mode. It is intentionally not activated on the still-old live repository. The already installed old trusted Guard remains available for existing `upiscium/Templates` publications until the exact cutover.

## Safe authority sequence (independent decisions, no permanent stranding)

**A. Local staging, review and protected old-repository source Publication**

- Review and test the new source/policy/locator/release-gate bytes, the exact old immutable policy, full source baseline, live GitHub repository ID and affected downstream references.
- Publish the proposed new guarded source to a **dedicated #246 Task branch** using the currently installed **old** immutable source publisher against `upiscium/Templates`, only with an exact Human Source Publication grant. Review GitHub CI, source PR exact scope and ask for **separate Merge authorization**. Ordinary Tasks must never install or select their own Guard.
- After Merge, independently build the *exact fully committed and reviewed* source revision (not an uncommitted `path:.` snapshot) into new immutable `#source-collaboration` and `#source-authority-locator` Nix Store outputs. Verify the compiled trusted revision, policy identity, launcher/policy/locator hashes, source parity, and fail-closed tests. An uncommitted clone's source-collaboration build can embed an all-zero untrusted revision and **must not** be activated.

**B. Human exact Admin/bootstrap authorization before GitHub rename**

The operator must separately approve the exact new host-owned selector/launcher pair, including full SHA-256 digests and a rollback path. A normal Source Publication, KAGARI `v0.1.0` release or GitHub settings role is **not** permission to update `/etc/kumiki/source-authority.json`.

- ① **実施**: prepare exact new pinned `/nix/store` outputs and a root-owned schema-2 descriptor for the *new* repo, plus the preserved, verified old descriptor/outputs for rollback. Determine which host-only Admin command performs the atomic replacement and pin switch; use no ephemeral env override or raw publisher fallback.
- ② **承認**: Human reviews and grants the exact new full revision, new repository name, fixed ID `1183254623`, Store paths, digest manifest, and designated host Admin command. No generic wildcard grant.
- ③ **影響**: after the slug cutover, the host selector authorizes only `upiscium/KUMIKI`; the old publisher no longer authorizes new tasks by design. No user Project/Work Unit state is deleted and no historical tag rewritten.
- ④ **否認時**: keep `upiscium/Templates` and the current pinned Guard intact; retain staged source candidate and current public GA with no host changes.

**C. Explicit live cutover go/no-go (#245, separate Human decision)**

Precondition: the exact new immutable Guard and locator are built/reviewed, new root descriptor bytes and rollback image are ready, existing old Guard still `READY`, remote `upiscium/KUMIKI` slug availability checked, GitHub repository ID `1183254623` confirmed, default main/tag SHAs captured, active publication Tasks paused.

1. Rename the **existing** GitHub repository in Settings (or a separately approved authenticated GitHub API) from `Templates` to `KUMIKI`. **Never create a replacement repo**, modify Git history or force-push branches.
2. Read back GitHub `nameWithOwner == "upiscium/KUMIKI"`, `id == 1183254623`, main and both GA/RC tag SHAs exactly unchanged; explicitly check the old URL redirect, but do not treat it as a trust assertion.
3. Atomically activate the **new reviewed root-owned descriptor and Nix Store selector** through the separately approved host Admin mechanism. Check pinned `kumiki-source --check`, valid `publication-check` against a post-cutover Issue-bound read-only test checkout whose `origin` explicitly names the *new* canonical repo; negative old/foreign origins remain BLOCKED.
4. Resume source publication only when the new host-selected Guard's repository, ID, revision and exact package digests match the independently approved decision. If any observation fails, STOP and execute the verified rollback below, with no unguarded Git operations.

**D. Post-cutover downstream source and distribution (#245)**

Prepare the downstream `.automation/UPSTREAM` and generated template/flake/CI reference updates as reviewed, independent Task changes. Their staging can happen beforehand; promotion must not make a released/pre-cutover consumer depend on the new URL while it is unavailable. After the cutover, verify raw Git HTTPS/SSH, `github:upiscium/KUMIKI` flake outputs, `v0.1.0#kagari`, old RC tag, generated drift, five GitHub template distribution targets, Source Authority publication and agent-core release gate. Update explicit external consumer links/remotes and owner-managed GitHub description/topics only as separately approved.

## Fail-closed STOP and bounded rollback

Stop if the requested new repo is another GitHub repository ID; if old/new tag SHA, CI or public Nix Store output differs; if source policy or locator SHA differs; if Admin cannot atomically select the expected reviewed Store output, if either valid phase has no tested publication path, or if old credentials/Task worktrees are unexpectedly modified.

Recovery: maintain old approved root-owned descriptor and locator/output for exact rollback, restore the old GitHub name **of the same ID** if needed, then atomically reselect the old pinned Source Authority pair. Verify `--check` and **old canonical** Source Publication read-only in an isolated Task checkout before resuming. Do not rely on GitHub redirect behavior to substitute for explicit canonical identity, and never force-rewrite tags, refs, branches or original user worktrees.

## Independent verification checklist

- [ ] New Source Authority synthetic strict-new + case variants + old-slug/foreign-host rejection and PR-binding tests PASS.
- [ ] New pinned Locator schema-2 exact-name + exact numeric-ID / revision / digest / owner / symlink tests PASS.
- [ ] AgentCore release gate strict-new provenance tests PASS; Issue #117 fixed historical v1 receipt SHA byte-identical; no broad historical alias accepted for new publication.
- [ ] Nix flake parity and `#source-collaboration` / `#source-authority-locator` candidates build, but remain **untrusted** until independently approved full source SHA and host Admin install.
- [ ] Source Publication, CI, independent review, Merge and Admin/pin decisions recorded as separate grants.
- [ ] GitHub cutover explicitly Human-authorized with readback of owner/name/ID/main/tags; rollback verified.
- [ ] Live new strict Guard `--check`, new-origin publication-check, old-origin rejection, Nix public tag outputs and downstream representatives checked.

**No authorization is derived from this document.** See [#242](https://github.com/upiscium/Templates/issues/242), [#245](https://github.com/upiscium/Templates/issues/245), [#246](https://github.com/upiscium/Templates/issues/246).
