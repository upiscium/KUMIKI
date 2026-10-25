{ pkgs }:

# Two KAGARI-owned vNext surfaces: user-facing Ask CLI and Main-side
# exact-root Session launcher. Neither replaces shipped 0.1.0 GA.
let
  sources = pkgs.runCommand "kagari-vnext-control-sources" {} ''
    mkdir -p "$out/tools" "$out/components/agent-core-v4"
    cp ${../tools/kagari_vnext_opencode_control.py} "$out/tools/kagari_vnext_opencode_control.py"
    cp ${../tools/kagari_vnext_task_binding.py} "$out/tools/kagari_vnext_task_binding.py"
    cp ${../tools/kagari_vnext_session.py} "$out/tools/kagari_vnext_session.py"
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
in pkgs.symlinkJoin {
  name = "kagari-vnext-control";
  paths = [ task session ];
}
