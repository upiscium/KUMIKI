{ pkgs }:

# One public vNext KAGARI Task/Ask interface; no second unbound control CLI.
let
  sources = pkgs.runCommand "kagari-vnext-control-sources" {} ''
    mkdir -p "$out/tools" "$out/components/agent-core-v4"
    cp ${../tools/kagari_vnext_opencode_control.py} "$out/tools/kagari_vnext_opencode_control.py"
    cp ${../tools/kagari_vnext_task_binding.py} "$out/tools/kagari_vnext_task_binding.py"
    cp ${../components/agent-core-v4/metadata_codec.py} "$out/components/agent-core-v4/metadata_codec.py"
    cp ${../components/agent-core-v4/metadata_ref.py} "$out/components/agent-core-v4/metadata_ref.py"
    cp ${../components/agent-core-v4/task_record.py} "$out/components/agent-core-v4/task_record.py"
  '';
in pkgs.writeShellApplication {
  name = "kagari-vnext-task";
  runtimeInputs = [ pkgs.git ];
  text = ''
    exec ${pkgs.python3}/bin/python3 -B ${sources}/tools/kagari_vnext_task_binding.py "$@"
  '';
}
