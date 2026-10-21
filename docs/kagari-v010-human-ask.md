# KAGARI 0.1.0 Human Ask — local test-only prototype

Status: UNQUALIFIED_LOCAL_INTERACTIVE, **not released**, not a verified
ChatGPT/Work approval interface, not production user authentication.

Related: #271, ADR-0002 (detachable ownership), ADR-0004 (coexistence first,
conflict-only Ask). This implementation is intentionally separate from
Source publication authority and does not change User Project configuration
without a separate, exact positive confirmation.

## Operations and trust boundary

The Ask engine has two separate grant kinds:

1. AGENTS_SCOPED_EXCEPTION: only if the read-only AGENTS compatibility
   inspector found *confirmed exact* legacy instructions incompatible with
   the native KAGARI Task workflow. An Ask describes original line numbers,
   complete file hashes and exact unified diff. If explicitly approved and
   applied, the original AGENTS.md stays present; a narrow KAGARI-session
   addendum is appended. The original file bytes are saved in an euid-owned
   private 0700 external Ask directory for exact restore.
2. NATIVE_TASK_PERMISSION: only when Project opencode.json declares task:deny.
   The Ask permits the two specific installed native child Roles under the
   KAGARI-managed primary, with default wildcard task:deny retained. No
   Project opencode.json rewrite, no shell/Push/PR/Merge permissions.

Install-time permission Ask and Task-time AGENTS compatibility Ask are
**independent**. Approval for one does not imply the other. Merely detecting
AgentCore v3 is not an upgrade request or an authority grant.

A pending ticket contains canonical Project directory/device/inode identity,
Git config and current HEAD at request creation, source configuration
fingerprints, specific KAGARI native Task source SHA-256, exact conflict IDs,
edit preimage hash, effect, diff, a fresh nonce and a request ID derived from
canonical request bytes. Grants are checked against current Project/runtime
bytes and revoked status before use, and revalidated during the native Task
before host Product commit and Evidence publication. Native v4 Contract and
Evidence bind the exact grant request/decision digests.

A positive answer requires foreground terminal input of the exact
APPROVE <24-hex-request-id> phrase after the preview. Refusal uses
DENY <24-hex-request-id>; a missing, wrong, duplicate or noninteractive
response creates no authority. The actual executable has **no --yes,
--auto-approve or model-controlled acceptance flag**. The ticket/decision
state is local test-only self-attested TTY provenance: this does *not*
cryptographically identify the human behind a terminal, and no remote
ChatGPT approval was captured.

## Example on independently disposable, authorized Git Clone only

The following paths are placeholders, not a script that changes the user's
real Project. Use an approved test-only immutable KAGARI Nix store output.

    STORE=/nix/store/<approved-test-only-output>
    PROJECT=/absolute/private/disposable-project
    ASKPARENT=/absolute/private/0700-human-ask-parent
    TASK_SOURCE="$STORE/libexec/kagari-v010-native-generic/tools/kagari_v010_native_generic_task.py"
    ASK="$STORE/bin/kagari-v010-human-ask-test-only"

1. Prepare exact requests **without modifying Project**:

       "$ASK" request-agents --project-root "$PROJECT" \
         --state-root "$ASKPARENT/agents" --runtime-source "$TASK_SOURCE"
       "$ASK" request-task-permission --project-root "$PROJECT" \
         --state-root "$ASKPARENT/task-permission" --runtime-source "$TASK_SOURCE"

   Each call returns the complete Ask text, effect, relevant Project hashes,
   requested role scope and exact diff (if applicable). The private parent
   must already be owned by the current euid with mode 0700.

2. The user must separately inspect and decide each request in a **real
   terminal**. Unapproved actions stay BLOCKED. This is an example of the
   interface, not approval for any currently existing Project.

       "$ASK" approve --project-root "$PROJECT" \
         --state-root "$ASKPARENT/task-permission" --runtime-source "$TASK_SOURCE"

   The command prints the exact request then asks the human to type
   APPROVE <request-id>. Use deny to explicitly refuse.

3. After a positive AGENTS-specific approval only, install the exact scoped
   addendum (this **changes AGENTS.md** on the selected disposable Project):

       "$ASK" apply --project-root "$PROJECT" \
         --state-root "$ASKPARENT/agents" --runtime-source "$TASK_SOURCE"

   The apply gate checks the original AGENTS bytes/mode, Project identity,
   git configuration, OpenCode policy, exact runtime bytes and approved
   request/decision before atomic file replacement. No other Project file
   or Git index/ref is changed.

4. On an isolated disposable Clone with a separately approved TaskSpec,
   plan and test runner, the native executor may be invoked with both
   --agents-ask-root and --permission-ask-root. It does NOT auto-approve.
   An invalid/revoked/stale grant safely blocks Task execution. The legacy
   --allow-project-task-deny-test-only option is historical test-only
   operator opt-in, not equivalent to a Human Ask decision.

5. Inspect the current status or revoke grants separately:

       "$ASK" status --project-root "$PROJECT" \
         --state-root "$ASKPARENT/agents" --runtime-source "$TASK_SOURCE"
       "$ASK" revoke --project-root "$PROJECT" \
         --state-root "$ASKPARENT/agents" --runtime-source "$TASK_SOURCE"

   If AGENTS.md still exactly matches the approved postimage, revoke restores
   exact preimage bytes and original mode. If it was edited by somebody else,
   nothing is overwritten or discarded: return BLOCKED for manual review.
   Revoking the native Task role grant only invalidates its external Ask state;
   the Project OpenCode configuration stays unchanged throughout.

## Results already tested offline

The module's unit fixtures simulate foreground TTY input using mocks **only
inside tests**. They cover approve/deny/missing/duplicate decisions, wrong
confirmation, tampered requests/decisions, runtime version drift, Project
identity drift, custom prohibition, AGENTS exact apply/revoke/idempotency,
refusal to restore user-modified content, and separate permission granting.
The native Task integration tests establish no-model/no-mutation on missing
or revoked grants and show that both separate approved receipts are needed
to reach the model boundary. No real human authorization is claimed by
these simulated positive tests.

## Unsupported in this candidate

- ChatGPT/Work's native Human Ask transport and an identity-verified reply
  channel are **not connected**. A local TTY does not authenticate a human
  identity against an account or GitHub and must not be called production
  authorization.
- The generic Admin Install/Repair/Uninstall UX has not yet integrated this
  prototype's receipt directory lifecycle; the two commands are separately
  usable but do not form a released installer.
- Root AGENTS.md is v3-owned in some Projects. A scoped addendum, even
  consented to, may conflict with existing v3 maintenance/replacement tools.
  A true first-class external KAGARI instruction overlay requires verified
  OpenCode precedence; this test-only patch is not proof of that.
- Host crash/interruption across the atomic file replace and receipt write,
  hostile same-UID processes, ACL/xattr preservation, multi-writer exclusion,
  live Task cancelation/reconfiguration and remote authentication remain
  outside this bounded slice. Do not present it as an arbitrary Git Project
  production mutation guarantee.
- KAGARI/KUMIKI 0.1.0 release, source commit/publication and production use
  remain subject to separate exact-scope Human decisions. Never treat an Ask
  preview or simulated test answer as approval of a real Project.
