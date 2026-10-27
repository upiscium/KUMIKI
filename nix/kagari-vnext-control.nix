{ pkgs }:

# KAGARI-owned vNext surfaces: Ask CLI, exact-root Session launcher and
# opt-in host Worktree install/repair preflight. None replaces shipped GA.
let
  sources = pkgs.runCommand "kagari-vnext-control-sources" {} ''
    mkdir -p "$out/tools" "$out/components/agent-core-v4"
    cp ${../tools/kagari_vnext_opencode_control.py} "$out/tools/kagari_vnext_opencode_control.py"
    cp ${../tools/kagari_vnext_task_binding.py} "$out/tools/kagari_vnext_task_binding.py"
    cp ${../tools/kagari_vnext_session.py} "$out/tools/kagari_vnext_session.py"
    cp ${../tools/kagari_vnext_worktree_ignore.py} "$out/tools/kagari_vnext_worktree_ignore.py"
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
in pkgs.symlinkJoin {
  name = "kagari-vnext-control";
  paths = [ task session worktreeInstall ];
}
