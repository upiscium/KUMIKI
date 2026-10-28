{ pkgs }:

# KAGARI-owned opt-in vNext Task, Session, ignore-preflight and host attachment
# entrypoints. None replaces the shipped GA or automatically starts a Task.
let
  sources = pkgs.runCommand "kagari-vnext-control-sources" {} ''
    mkdir -p "$out/tools" "$out/components/agent-core-v4"
    cp ${../tools/kagari_vnext_opencode_control.py} "$out/tools/kagari_vnext_opencode_control.py"
    cp ${../tools/kagari_vnext_task_binding.py} "$out/tools/kagari_vnext_task_binding.py"
    cp ${../tools/kagari_vnext_session.py} "$out/tools/kagari_vnext_session.py"
    cp ${../tools/kagari_vnext_worktree_ignore.py} "$out/tools/kagari_vnext_worktree_ignore.py"
    cp ${../tools/kagari_vnext_host_prepare.py} "$out/tools/kagari_vnext_host_prepare.py"
    cp ${../tools/kagari_vnext_attach.py} "$out/tools/kagari_vnext_attach.py"
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
in pkgs.symlinkJoin {
  name = "kagari-vnext-control";
  paths = [ task session worktreeInstall hostPrepare hostAttach ];
}
