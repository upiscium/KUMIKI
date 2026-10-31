{ pkgs }:

# KAGARI opt-in vNext Task, Session, Worktree ignore, host prepare and Main.
# Preserve shipped GA; Main must not auto-launch unauthorized Tasks.
let
  sources = pkgs.runCommand "kagari-vnext-control-sources" {} ''
    mkdir -p "$out/tools" "$out/components/agent-core-v4"
    cp ${../tools/kagari_vnext_opencode_control.py} "$out/tools/kagari_vnext_opencode_control.py"
    cp ${../tools/kagari_vnext_task_binding.py} "$out/tools/kagari_vnext_task_binding.py"
    cp ${../tools/kagari_vnext_session.py} "$out/tools/kagari_vnext_session.py"
    cp ${../tools/kagari_vnext_worktree_ignore.py} "$out/tools/kagari_vnext_worktree_ignore.py"
    cp ${../tools/kagari_vnext_host_prepare.py} "$out/tools/kagari_vnext_host_prepare.py"
    cp ${../tools/kagari_vnext_attach.py} "$out/tools/kagari_vnext_attach.py"
    cp ${../tools/kagari_vnext_attachment_gate.py} "$out/tools/kagari_vnext_attachment_gate.py"
    cp ${../tools/kagari_vnext_main.py} "$out/tools/kagari_vnext_main.py"
    cp ${../tools/kagari_vnext_main_observation.py} "$out/tools/kagari_vnext_main_observation.py"
    cp ${../tools/kagari_vnext_main_service.py} "$out/tools/kagari_vnext_main_service.py"
    cp ${../tools/kagari_vnext_trusted_host.py} "$out/tools/kagari_vnext_trusted_host.py"
    cp ${../tools/kagari_vnext_issue_bootstrap.py} "$out/tools/kagari_vnext_issue_bootstrap.py"
    cp ${../tools/kagari_vnext_progress.py} "$out/tools/kagari_vnext_progress.py"
    cp ${../tools/kagari_vnext_test_evidence.py} "$out/tools/kagari_vnext_test_evidence.py"
    cp ${../components/agent-core-v4/issue_worktree.py} "$out/components/agent-core-v4/issue_worktree.py"
    cp ${../components/agent-core-v4/issue_worktree_cleanup.py} "$out/components/agent-core-v4/issue_worktree_cleanup.py"
    cp ${../components/agent-core-v4/evidence.py} "$out/components/agent-core-v4/evidence.py"
    cp ${../components/agent-core-v4/task_view.py} "$out/components/agent-core-v4/task_view.py"
    cp ${../components/agent-core-v4/task_collaboration.py} "$out/components/agent-core-v4/task_collaboration.py"
    cp ${../components/agent-core-v4/collaboration_git.py} "$out/components/agent-core-v4/collaboration_git.py"
    cp ${../components/agent-core-v4/operation_prerequisites.py} "$out/components/agent-core-v4/operation_prerequisites.py"
    cp ${../components/agent-core-v4/metadata_codec.py} "$out/components/agent-core-v4/metadata_codec.py"
    cp ${../components/agent-core-v4/metadata_ref.py} "$out/components/agent-core-v4/metadata_ref.py"
    cp ${../components/agent-core-v4/task_record.py} "$out/components/agent-core-v4/task_record.py"
  '';
  task = pkgs.writeShellApplication {
    name = "kagari-vnext-task";
    runtimeInputs = [ pkgs.git ];
    text = ''
      exec ${pkgs.python3}/bin/python3 -B ${sources}/tools/kagari_vnext_task_binding.py "$@"
    '';
  };
  session = pkgs.writeShellApplication {
    name = "kagari-vnext-session";
    runtimeInputs = [ pkgs.git ];
    text = ''
      exec ${pkgs.python3}/bin/python3 -B ${sources}/tools/kagari_vnext_session.py "$@"
    '';
  };
  worktreeInstall = pkgs.writeShellApplication {
    name = "kagari-vnext-worktree-install";
    runtimeInputs = [ pkgs.git ];
    text = ''
      exec ${pkgs.python3}/bin/python3 -B ${sources}/tools/kagari_vnext_worktree_ignore.py "$@"
    '';
  };
  hostPrepare = pkgs.writeShellApplication {
    name = "kagari-vnext-host-prepare";
    runtimeInputs = [ pkgs.git ];
    text = ''
      exec ${pkgs.python3}/bin/python3 -B ${sources}/tools/kagari_vnext_host_prepare.py "$@"
    '';
  };
  hostAttach = pkgs.writeShellApplication {
    name = "kagari-vnext-attach";
    runtimeInputs = [ pkgs.git ];
    text = ''
      exec ${pkgs.python3}/bin/python3 -B ${sources}/tools/kagari_vnext_attach.py "$@"
    '';
  };
  attachmentGate = pkgs.writeShellApplication {
    name = "kagari-vnext-attachment-gate";
    runtimeInputs = [ pkgs.git ];
    text = ''
      exec ${pkgs.python3}/bin/python3 -B ${sources}/tools/kagari_vnext_attachment_gate.py "$@"
    '';
  };
  main = pkgs.writeShellApplication {
    name = "kagari-vnext-main";
    runtimeInputs = [ pkgs.git pkgs.gh ];
    text = ''
      exec ${pkgs.python3}/bin/python3 -B ${sources}/tools/kagari_vnext_main_service.py "$@"
    '';
  };
in pkgs.symlinkJoin {
  name = "kagari-vnext-control";
  paths = [ task session worktreeInstall hostPrepare hostAttach attachmentGate main ];
}
