# B1 source-only implementation validation record

Runs: `KUMIKI-291-PHASEB1-TRUSTED-HOST-20261030-R1` and bounded continuation
`KUMIKI-291-PHASEB1-TRUSTED-HOST-20261030-R1B`, same native build Session.
Base HEAD: `526b28169d33c28aaa525939632bf5f5fe41b0de`.
Branch: `feat/291-phase-b1-trusted-host-r1`.
Root: `/home/upiscium/.local/state/kumiki291-phaseb1-trusted-host-20261030/repo`.

This is implementer test evidence, **not independent correctness/security review
or Phase B2/B3 acceptance**. No live GitHub/gh invocation, credentials inspection,
real model turn, source publication, index staging, commit or deploy was used.
New host tests inject fake REST and guard the real HTTPS constructor. Existing
host regressions use a synthetic local OpenCode HTTP process, never a model.

## Results

- **28/28 distinct new tests passed in completed shards** (no test skips).
- **25 distinct existing regressions passed** (no test skips): MainHost 6,
  MainService 4, TaskCollaboration 5, TaskViewSnapshots 5, MetadataStore 5.
- Opt-in `kagari-control` offline build: exit 0; packaged host import: exit 0.
- Flake evaluation with `--no-build`: exit 0, all evaluated checks passed;
  aarch64-linux/aarch64-darwin incompatible-system checks omitted by Nix.
  No full all-package build, full regression discovery or all-system check run.
- Initial red test was missing-module ImportError, exit 1, before implementation.
  No failing assertion was observed in the implemented B1 tests.
- Two native 120 s tool timeouts are **incomplete, not green** (details below).
- One regression invocation had a test-loader import-path error (5 errors,
  exit 1); corrected `PYTHONPATH=tests` invocation passed. No baseline test
  files were changed to accommodate that harness convention.

The following are the complete test/build commands. All ran from the root
above. Successful commands returned exit 0. Times are unittest-reported wall
seconds, not Nix startup time. Repeated passing methods count once above.

## Completed new-test shards

12 tests / 5.226 s:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest -v tests.test_kagari_vnext_trusted_host.JournalTests tests.test_kagari_vnext_trusted_host.HTTPBoundaryTests tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_all_missing_callbacks_fail_before_external_calls tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_missing_prerequisites_fail_before_calls tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_no_cas_no_write tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_issue_reader_maps_real_facts_not_extra_authority tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_bad_issue_body_state_and_identity_fail_without_publication tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_cross_issue_branch_record_contract_and_worktree
```

4 tests / 36.993 s:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest -v tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_denied_authority_before_branch_or_github_publication tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_denied_content_and_intent_do_not_create_pr tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_json_duplicate_oversized_wrong_shapes_and_principal tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_conflicting_source_or_unknown_recovery_state_blocks
```

2 tests / 77.790 s:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest -v tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_interrupt_before_publication_blocks_without_recapture tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_interrupt_after_remote_cas_recovers_exact_candidate_not_tip
```

2 tests / 71.856 s:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest -v tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_altered_approved_comment_content_cannot_use_permit tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_transport_writes_require_real_one_use_facade_permit
```

1 test / 71.141 s:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest -v tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_main_host_route_stops_before_session_and_reconnect_reuses_checkpoint
```

1 test / 43.665 s:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest -v tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_uncertain_comment_no_result_never_recaptures_or_reposts
```

3 tests / 66.202 s:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest -v tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_snapshot_denied_and_retry_no_duplicate_pr tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_cross_repo_issue_and_pr_facts tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_pagination_limits_duplicates_and_malformed_extra_entries
```

2 tests / 87.603 s:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest -v tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_lost_ack_pr_and_comment_exactly_reconciled tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_success_real_interfaces_and_reconnect_without_duplicate
```

1 test / 22.768 s:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest -v tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_uncertain_pr_no_result_never_reposts_even_on_reconnect
```

Earlier overlapping completed shard, 3 tests / 114.902 s:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest -v tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_lost_ack_pr_and_comment_exactly_reconciled tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_success_real_interfaces_and_reconnect_without_duplicate tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_uncertain_pr_no_result_never_reposts_even_on_reconnect
```

Each completed shard printed `Ran N tests ... OK`. Positive #191/#194/#217
integration verifies exact remotely reachable immutable snapshot and repeated
same-operation/reconnect. Negative cases cover callback denial/absence, typed
permit reuse/content alteration, foreign facts, malformed and extra API entries,
bounded pagination/duplicates, unknown/partial recovery state, pre-CAS and
post-CAS interruption and uncertain/lost-ack PR/comment writes. Unresolved
attempts stop without additional POST, recapture or real session/model start.

## Completed regression shards

MetadataStore: 5 tests / 1.030 s:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest -v tests.test_metadata_ref.MetadataRefTest.test_writer_without_trusted_cas_capability_refuses_mutation tests.test_metadata_ref.MetadataRefTest.test_candidate_parent_is_checked_before_trusted_capability tests.test_metadata_ref.MetadataRefTest.test_candidate_intent_precedes_push_and_confirm_returns_exact_candidate tests.test_metadata_ref.MetadataRefTest.test_lost_push_ack_returns_only_the_exact_reachable_candidate tests.test_metadata_ref.MetadataRefTest.test_confirm_rejects_unreachable_or_identity_mismatched_candidates
```

TaskViewSnapshots: 5 tests / 11.211 s:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c env PYTHONPATH=tests python -B -m unittest -v test_task_view_v4.TaskViewV4Test.test_noop_snapshot_publish_still_rechecks_subject test_task_view_v4.TaskViewV4Test.test_authorization_requires_literal_true_is_frozen_and_errors_do_not_leak test_task_view_v4.TaskViewV4Test.test_on_candidate_callback_is_bracketed_by_guards test_task_view_v4.TaskViewV4Test.test_candidate_intent_failure_aborts_without_leaking_exception_text test_task_view_v4.TaskViewV4Test.test_post_confirm_previous_change_refuses_receipt_and_candidate_intent_is_preserved
```

TaskCollaboration: 5 tests / 3.278 s:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c env PYTHONPATH=tests python -B -m unittest -v test_task_collaboration_v4.TaskCollaborationV4Test.test_frozen_intents_and_authorization_are_separate_from_prerequisites test_task_collaboration_v4.TaskCollaborationV4Test.test_create_adopts_one_exact_open_draft_and_lost_ack_is_reconciled test_task_collaboration_v4.TaskCollaborationV4Test.test_pull_pagination_is_complete_bounded_deduplicated_and_reobserved test_task_collaboration_v4.TaskCollaborationV4Test.test_every_prose_write_requires_literal_host_content_approval test_task_collaboration_v4.TaskCollaborationV4Test.test_hard_interruption_retries_reobserve_pr_and_checkpoint_without_duplicate
```

MainHost: 3 tests / 35.659 s:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest -v tests.test_kagari_vnext_main_host.MainHostTests.test_ordinary_host_route_pr_first_then_restart_does_not_duplicate tests.test_kagari_vnext_main_host.MainHostTests.test_absent_host_refuses_before_allocation_even_with_pr_selection tests.test_kagari_vnext_main_host.MainHostTests.test_unknown_or_partial_session_without_main_kickoff_requires_reconciliation
```

MainHost source/identity conflicts: 3 tests / 43.232 s:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest -v tests.test_kagari_vnext_main_host.MainHostTests.test_mismatched_contract_branch_or_issue_snapshot_fails_before_provider tests.test_kagari_vnext_main_host.MainHostTests.test_dirty_existing_task_is_not_pristine_adoption tests.test_kagari_vnext_main_host.MainHostTests.test_stale_existing_initial_checkpoint_cannot_launch
```

MainService: 4 tests / 2.191 s:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest -v tests.test_kagari_vnext_main_service.MainServiceTest.test_direct_dispatch_refused_without_supervisor_even_with_task_grants tests.test_kagari_vnext_main_service.MainServiceTest.test_optional_pr_and_evidence_are_explicit_observation_requests tests.test_kagari_vnext_main_service.MainServiceTest.test_selection_closed_schema_and_port_uniqueness tests.test_kagari_vnext_main_service.MainServiceTest.test_read_only_cycle_does_not_start_any_issue
```

## Build/import and flake evaluation

All exit 0. `path:.` deliberately includes new untracked source, without staging.
```sh
nix build --offline --no-write-lock-file --no-update-lock-file --no-link "path:.#kagari-control"
nix flake check --offline --no-write-lock-file --no-update-lock-file --no-build "path:."
nix-store --query --outputs /nix/store/2xy7avyjaxixb1csi2bq5pcz09kqv63m-kagari-vnext-control-sources.drv
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -c 'import sys; sys.path.insert(0, "/nix/store/6zpr9lf0pvvsrh9ah975w7imw1bvn2jh-kagari-vnext-control-sources/tools"); import kagari_vnext_trusted_host; print("packaged trusted host import OK; no execution")'
```
Source output: `/nix/store/6zpr9lf0pvvsrh9ah975w7imw1bvn2jh-kagari-vnext-control-sources`.
Build realized 8 opt-in control derivations; no output link/install or GA rebuild.
Import printed `packaged trusted host import OK; no execution`.

## Red, incomplete and harness-failure commands (not counted as green)

Initial pre-implementation red: exit 1, `Ran 1 test`, 1 loader error,
`ModuleNotFoundError: No module named 'tools.kagari_vnext_trusted_host'`:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest tests.test_kagari_vnext_trusted_host -v
```

Same command after implementation hit the native 120 s tool runtime limit.
14 methods visibly completed `ok`; the next was running; no unittest final
summary or numeric child exit code was returned. A subsequent proposed full
suite invocation was rejected before execution; it was **not retried**. The
coordinator then authorized bounded method shards in r1b.

A two-method shard also hit 120 s, first method visibly `ok`, second running;
no final summary/child exit code. Both later passed in completed shards above:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest -v tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_uncertain_comment_no_result_never_recaptures_or_reposts tests.test_kagari_vnext_trusted_host.TrustedHostTests.test_transport_writes_require_real_one_use_facade_permit
```

Baseline regression loader-path error: exit 1, `Ran 5 tests`, 5 loader errors,
`ModuleNotFoundError: No module named 'test_metadata_ref'`:
```sh
nix develop --offline --no-write-lock-file --no-update-lock-file -c python -B -m unittest -v tests.test_task_view_v4.TaskViewV4Test.test_noop_snapshot_publish_still_rechecks_subject tests.test_task_view_v4.TaskViewV4Test.test_authorization_requires_literal_true_is_frozen_and_errors_do_not_leak tests.test_task_view_v4.TaskViewV4Test.test_on_candidate_callback_is_bracketed_by_guards tests.test_task_view_v4.TaskViewV4Test.test_candidate_intent_failure_aborts_without_leaking_exception_text tests.test_task_view_v4.TaskViewV4Test.test_post_confirm_previous_change_refuses_receipt_and_candidate_intent_is_preserved
```

## Deliberate residual blockers

See [host contract and B2 prerequisites](kagari-vnext-trusted-host-b1.md).
External real authorization/check policies, durable WriteIntent recording,
Source-Guard branch publisher, credential/fixture identities and genuine
direct-ref CAS are required injected capabilities, **not implementations or
grants fabricated by B1**. Metadata PATCH remains refused. Unreachable snapshot
candidates/ambiguous GitHub attempts stop without automatic recovery. Real API
rate-limit behavior, actual GitHub schema compatibility, credential delivery,
server CAS and production journal placement are unqualified. Independent
coordinator-owned frozen-snapshot reviews remain pending.
