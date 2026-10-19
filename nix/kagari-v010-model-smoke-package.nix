# Isolated TEST_ONLY/M1_DEMO_UNQUALIFIED candidate; not a consumer installer.
{ pkgs, sourceRevision, expectedV4ManifestSha256 }:
let
  validRevision = builtins.isString sourceRevision && builtins.match "[0-9a-f]{40}" sourceRevision != null;
  validDigest = builtins.isString expectedV4ManifestSha256 && builtins.match "[0-9a-f]{64}" expectedV4ManifestSha256 != null;
  modules = import ./kagari-v4-module-package.nix { inherit pkgs sourceRevision; };
  modelTask = ../tools/kagari_v010_model_task.py;
  namespaceProbe = ../tools/kagari_v4_opencode_namespace_probe.py;
in
assert validRevision && validDigest;
assert pkgs.stdenv.hostPlatform.isLinux;
pkgs.runCommand "kagari-0.1.0-model-smoke-test-only" {
  nativeBuildInputs = [ pkgs.python3 pkgs.coreutils ];
  passthru = {
    version = "0.1.0";
    qualification = "TEST_ONLY/M1_DEMO_UNQUALIFIED";
    qualifiesRuntime = false;
    allowsActivation = false;
  };
} ''
  set -eu
  test "$(sha256sum ${modules}/manifest.json | cut -d ' ' -f1)" = "${expectedV4ManifestSha256}"
  test "$(sha256sum ${modelTask} | cut -d ' ' -f1)" = "c76713443223df0d8e859881fa8bc8c1ede9ad8206fa316e488ad1fa2e1082f8"
  test "$(sha256sum ${namespaceProbe} | cut -d ' ' -f1)" = "177537ee97ae2c33abd82dcad572bdfd9f10b931ad246c429423b05a6563c1fe"
  root="$out/libexec/kagari-v010-model-smoke"
  mkdir -p "$out/bin" "$root/tools" "$root/components/agent-core-v4" "$out/share/kagari-v010-model-smoke"
  install -m 0444 ${modelTask} "$root/tools/kagari_v010_model_task.py"
  install -m 0444 ${namespaceProbe} "$root/tools/kagari_v4_opencode_namespace_probe.py"
  for source in ${modules}/lib/kagari_v4/*.py; do
    install -m 0444 "$source" "$root/components/agent-core-v4/$(basename "$source")"
  done
  install -m 0444 ${modules}/manifest.json "$out/share/kagari-v010-model-smoke/n1-manifest.json"
  cat > "$out/share/kagari-v010-model-smoke/receipt.json" <<'JSON'
  {"version":"0.1.0","qualification":"TEST_ONLY/M1_DEMO_UNQUALIFIED","source_revision":"${sourceRevision}","n1_manifest_sha256":"${expectedV4ManifestSha256}","consumer_project_supported":false,"installer_supported":false,"released":false}
  JSON
  chmod 0444 "$out/share/kagari-v010-model-smoke/receipt.json"
  cat > "$out/bin/kagari-v010-model-smoke" <<EOF
  #!${pkgs.runtimeShell}
  exec ${pkgs.python3}/bin/python3 -I -B -c 'import runpy,sys; p=sys.argv.pop(1); sys.path.insert(0,p.rsplit("/",1)[0]); runpy.run_path(p,run_name="__main__")' "$root/tools/kagari_v010_model_task.py" "\$@"
  EOF
  chmod 0555 "$out/bin/kagari-v010-model-smoke"
  ${pkgs.python3}/bin/python3 -I -B - "$out" '${expectedV4ManifestSha256}' <<'PY'
  import hashlib, json, pathlib, sys
  root = pathlib.Path(sys.argv[1])
  manifest_bytes = (root / 'share/kagari-v010-model-smoke/n1-manifest.json').read_bytes()
  assert hashlib.sha256(manifest_bytes).hexdigest() == sys.argv[2]
  manifest = json.loads(manifest_bytes)
  assert manifest['schema_version'] == 1
  assert manifest['kind'] == 'kagari-v4-native-module-bundle'
  assert manifest['target_version'] == '1.0.0'
  assert manifest['qualification'] == 'SOURCE_ONLY_UNQUALIFIED'
  source_dir = root / 'libexec/kagari-v010-model-smoke/components/agent-core-v4'
  assert len(manifest['files']) == 12
  assert {p.name for p in source_dir.iterdir()} == {
      pathlib.PurePosixPath(entry['path']).name for entry in manifest['files']
  }
  for entry in manifest['files']:
      data = (source_dir / pathlib.PurePosixPath(entry['path']).name).read_bytes()
      assert hashlib.sha256(data).hexdigest() == entry['sha256']
      assert len(data) == entry['size']
  PY
  "$out/bin/kagari-v010-model-smoke" --help > /dev/null
''
