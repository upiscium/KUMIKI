# KAGARI v0.1.0 — host-observed auto Planning (M2 local integration)

Status: TEST_ONLY_UNQUALIFIED; not a released runtime or authenticated
provider attestation. The human-approved R3 specified-Plan M1 round trip is
historical evidence. This document describes the additional auto-Planning
path; it does not upgrade historical specified results.

## Contract and sequence

For a TaskSpec v1 with planning=specified, the operator supplies the
same explicit --plan path as before. No planning subprocess runs.

For a TaskSpec v1 with planning=auto, the operator must omit --plan.
The installed, exact Nix Task runner first revalidates the Project baseline,
two independently scoped Human Ask grants (including pre-approved v3
AGENTS exception if applicable), immutable package identity, Git HEAD,
refs, index, dirty/staged state, physical inventory and instruction hashes.
Planning cannot bypass an unanswered, revoked or mismatched Ask.
The host then runs one OpenCode planning-only role in a private external
0700 Evidence directory. No --model override is passed. The role denies
edit, bash, Task, question, read, web and skill tools. No Task execution
is authorized during this phase.

The host accepts one strict JSON Plan from one complete, tool-free OpenCode
JSONL session with one start and finish. The bounded v1 validator requires
exactly two independent nonoverlapping child assignments covering all
TaskSpec paths, followed by a joining primary Step. Paths, base revision,
fields, dependencies and instructions are validated. No model-added
scope, fallback Plan or self-attested caller transcript confers authority.
The host rechecks the entire physical Project inventory, current
HEAD/ref/index/config/refs, staged and dirty work, and Human Ask grants
before Product-model invocation or Contract publication. Any change
blocks the Task.

The host saves the direct model stdout JSONL, prompt, private role, exact
generated Plan and digest-bound receipt under the Task Evidence planning/
folder. Contract and native-v4 Evidence bind TaskSpec and Plan digests,
the raw Planning transcript digest and receipt digest. Independent
fresh-process readback checks these hashes and reparses stored stdout JSONL
to reproduce the exact Plan, making silent Plan/stream alteration detectable.

Only after this stage may the existing primary plus two concurrent native
children execute. Existing scoped Product, Git, test, host-only commit,
v4 Task Record/Evidence/Task View and Human Ask protections remain.
Planning PASS alone is never a Task PASS.

## Evidence and qualification

The new receipt states HOST_OBSERVED_VALIDATED_PLAN_TEST_ONLY and
host_observed_model_output=true, but explicitly records
model_identity_independently_verified=false. This distinguishes actual
host-observed OpenCode output from caller-supplied JSON, without claiming
cryptographic provider authentication or independently verified user
identity. The original offline validator correctly refuses external
transcript self-attestation.

On 2026-10-17 JST a separate clean Git fixture with byte-identical MUSUBI
Python product modules first validated real model Planning alone; its receipt
is retained under
~/.local/state/kagari271/musubi-m1-model-planning-20261017-r1/.
This earlier pilot itself is not evidence of an autonomous Product Task.

**Subsequent complete TEST_ONLY evidence — two independent executions:**

1. Native packaged direct Task, no user-supplied Plan and no v3 AGENTS/task
   policy present: genuine OpenCode auto Plan validated, two overlapping
   native Product-code subagents, all three MUSUBI-derived test suites
   **156/156 PASS**, exact product-only commit
   e046808e964b63a6ab4ad19cb40772f903599d91 (two existing Python
   files only), complete native-v4 Contract/Record/Evidence/Task View and
   fresh-process readback PASS. Child execution overlap 22,554ms. The
   staged, unrelated README stayed intact; original MUSUBI was untouched.
   See the private independent audit
   m2-integration-20261017-r1/generic-consumer-real-musubi-modules-r1/
   independent-full-auto-r1-audit.json under ~/.local/state/kagari271.
2. A separate Test-only **Install → auto Plan → real Product Task → tests →
   exact commit → v4 readback → Uninstall** also PASS on another disconnected
   ordinary Git Project with byte-identical MUSUBI Python source/test files
   and no v3 AGENTS/task:deny. Its first invocation correctly BLOCKED
   because cold OpenCode startup created the previously absent internal
   .git/opencode Project identity; no Product files, Git HEAD/index or
   staged README changed. After independently inspecting and accepting
   that one-time initialization in this disposable test fixture, a fresh
   Evidence-path execution fully passed: **156/156 Product tests**,
   two native children with **22,751ms** overlap, Product-only commit
   5903ceb455a31ed9a5c9aa567de625f73ea72e68, and fresh native
   readback PASS. Uninstall returned ABSENT, altered no Product/Project
   configuration, retained commit and staged README, denied subsequent
   run-task without the installed runtime, and retained fresh-process
   readback. Private before/after audits under
   m2-integration-20261017-r1/generic-consumer-admin-real-musubi-modules-r1/.

The cold-start Git Project identity was a **real first-run auto-Planning
compatibility defect** in the R9 model-backed acceptance, now tracked in
[#278](https://github.com/upiscium/Templates/issues/278). Its original
BLOCKED report remains historical evidence and is never reclassified.
The narrowly selected R10 source changes only the tool-free Planner's
execution cwd to the external private Evidence directory, preventing
OpenCode workspace-ID initialization from writing inside consumer .git.
The strict Project/Git-control pre/post diff remains fully enforced;
no Git-control mutation is silently permitted.

The exact rebuilt immutable Nix TEST_ONLY package is
/nix/store/rd8rpylvs1bl8ks633dq789c08d0k35q-kagari-0.1.0-native-generic-test-only,
with receipt SHA-256
441fd213a07fd6e6fb72e16ba4bbac583c1b26ea4078d4c7d0166950140cd1e0.
Six relevant focused suite regressions **106/106 PASS**. A read-only
independent reviewer returned SCOPED PASS after fixing Planning process
group/time-budget handling, JSONL event ordering and Git-control
fingerprinting; that is a source-only verdict, not independent test execution
or product release certification.

## Outstanding release and acceptance gates

- Fresh real MUSUBI/TERREATE disposable Clone auto-Plan → native Task →
  Project tests → product-only commit → v4 readback → Uninstall acceptance,
  after separately approved, exact Project and runtime-bound Human Ask.
- Independent final Planning security review and source/CI integration.
- Native ChatGPT/Work Ask, production human identity and cryptographically
  attested provider identity are NOT implemented.
- The OpenCode Session export's 3 MiB size limit is post-write; write-time
  disk boundedness is the Human-accepted LOW priority Known Limitation
  tracked in [#276](https://github.com/upiscium/Templates/issues/276),
  deferred beyond v0.1.0 rather than a required release blocker.
- Timeout is bounded but process-tree quiescence needs release
  qualification. Avoid inferring guarantees beyond the observed evidence.

No source Push/PR/Merge, original Project mutation, Guard switch or
release is authorized by this candidate.

## 2026-10-18 real R9 MUSUBI cold-start proof / R10 fix

Against the human-approved R9 immutable durable TEST_ONLY candidate,
a freshly independent full-history MUSUBI Clone had no .git/opencode
workspace ID. First auto Planning used cwd=Project and a working OpenCode
model returned a valid three-step Plan, but OpenCode first-run initialization
created a 40-byte, mode 0600 .git/opencode identifier. The strict host
Project/Git-control checkpoint rejected this, correctly BLOCKING before
Product edits, native Contract or v4 Evidence. The original R9 failure
receipt was retained.

A second identical-package, identical-grant, identical-TaskSpec attempt
on the same disposable Clone, with its one-time OpenCode ID now present,
PASSed the exact Native v4 round trip: two real overlapping child sessions,
156/156 MUSUBI Product tests, only two authorized committed Product files
(commit a3f04ffc211edccfd08eab42d173e363dbff833c), and independent
native v4 fresh-process Evidence/Task View readback. Uninstall revoked the
two Human Asks and restored original AGENTS bytes; Reinstall issued new,
unapproved requests and refused reuse; final Uninstall left Product
commit/Evidence and staged unrelated README preserved. Original MUSUBI
worktree/index remains untouched. Private audit:
~/.local/state/kagari275-qualification/r9-musubi-human-ask-e2e/
r9-final-lifecycle-audit.json. This is TEST_ONLY acceptance, not public
production qualification.

The R10 corrective source keeps the same deny-tool Planner permissions,
host-observed OpenCode JSONL/Plan validation, TaskSpec and strict Project/
Git integrity checks. Before spawning the *tool-free* model it ensures that
the Planner Evidence directory is canonical and disjoint from Project, then
uses that external root as the model subprocess cwd and records both
external cwd and read-only Project context in its invocation receipt.
OpenCode's model output can still bind to TaskSpec without accessing
consumer Git internals, avoiding the known first-run .git/opencode
initialization effect during planning. Release readiness still needs
exact-source independent review and an authorized new-package acceptance.
