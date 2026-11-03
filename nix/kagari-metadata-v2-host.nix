{ pkgs }:

# #337 Phase 2B2: immutable, explicitly selected v2 Host source add-on.
# No executable/daemon, no v1 runtime replacement, no automatic Install,
# Task start or extra authorization. The caller installs a trusted Host.
pkgs.runCommand "kagari-metadata-v2-host-sources" {} ''
  mkdir -p "$out/tools" "$out/components/agent-core-v4"

  # Keep dependency closure explicit and pinned to exactly this source tree.
  cp ${../tools/kagari_metadata_v2_task_admission.py} "$out/tools/kagari_metadata_v2_task_admission.py"
  cp ${../tools/kagari_metadata_v2_bootstrap.py} "$out/tools/kagari_metadata_v2_bootstrap.py"
  cp ${../tools/kagari_metadata_v2_host_binding.py} "$out/tools/kagari_metadata_v2_host_binding.py"
  cp ${../tools/kagari_vnext_attachment_gate.py} "$out/tools/kagari_vnext_attachment_gate.py"
  cp ${../tools/kagari_vnext_attach.py} "$out/tools/kagari_vnext_attach.py"
  cp ${../tools/kagari_vnext_host_prepare.py} "$out/tools/kagari_vnext_host_prepare.py"
  cp ${../tools/kagari_vnext_worktree_ignore.py} "$out/tools/kagari_vnext_worktree_ignore.py"
  cp ${../tools/kagari_vnext_main.py} "$out/tools/kagari_vnext_main.py"
  cp ${../tools/kagari_vnext_main_observation.py} "$out/tools/kagari_vnext_main_observation.py"
  cp ${../tools/kagari_vnext_main_service.py} "$out/tools/kagari_vnext_main_service.py"
  cp ${../tools/kagari_vnext_trusted_host.py} "$out/tools/kagari_vnext_trusted_host.py"
  cp ${../tools/kagari_vnext_issue_bootstrap.py} "$out/tools/kagari_vnext_issue_bootstrap.py"
  cp ${../tools/kagari_vnext_task_binding.py} "$out/tools/kagari_vnext_task_binding.py"
  cp ${../tools/kagari_vnext_session.py} "$out/tools/kagari_vnext_session.py"
  cp ${../tools/kagari_vnext_opencode_control.py} "$out/tools/kagari_vnext_opencode_control.py"
  cp ${../tools/kagari_vnext_progress.py} "$out/tools/kagari_vnext_progress.py"
  cp ${../tools/kagari_vnext_test_evidence.py} "$out/tools/kagari_vnext_test_evidence.py"

  cp ${../components/agent-core-v4/metadata_v2.py} "$out/components/agent-core-v4/metadata_v2.py"
  cp ${../components/agent-core-v4/metadata_v2_archive.py} "$out/components/agent-core-v4/metadata_v2_archive.py"
  cp ${../components/agent-core-v4/metadata_codec.py} "$out/components/agent-core-v4/metadata_codec.py"
  cp ${../components/agent-core-v4/metadata_ref.py} "$out/components/agent-core-v4/metadata_ref.py"
  cp ${../components/agent-core-v4/task_record.py} "$out/components/agent-core-v4/task_record.py"
  cp ${../components/agent-core-v4/issue_worktree.py} "$out/components/agent-core-v4/issue_worktree.py"
  cp ${../components/agent-core-v4/issue_worktree_cleanup.py} "$out/components/agent-core-v4/issue_worktree_cleanup.py"
  cp ${../components/agent-core-v4/evidence.py} "$out/components/agent-core-v4/evidence.py"
  cp ${../components/agent-core-v4/task_view.py} "$out/components/agent-core-v4/task_view.py"
  cp ${../components/agent-core-v4/task_collaboration.py} "$out/components/agent-core-v4/task_collaboration.py"
  cp ${../components/agent-core-v4/collaboration_git.py} "$out/components/agent-core-v4/collaboration_git.py"
  cp ${../components/agent-core-v4/operation_prerequisites.py} "$out/components/agent-core-v4/operation_prerequisites.py"

  # No bin/ is created. Only an independently trusted Host may select/import
  # this source output, bind #256's *existing* held Attach receipt and grant
  # an exact v2 Issue/Worktree operation. No runtime is activated by building.
''
