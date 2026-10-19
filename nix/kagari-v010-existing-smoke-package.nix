# Isolated M2 existing-Project smoke candidate; not a release or production activation.
{ pkgs, sourceRevision, expectedV4ManifestSha256 }:
let
  validRevision = builtins.isString sourceRevision && builtins.match "[0-9a-f]{40}" sourceRevision != null;
  validDigest = builtins.isString expectedV4ManifestSha256 && builtins.match "[0-9a-f]{64}" expectedV4ManifestSha256 != null;
  modelSmoke = import ./kagari-v010-model-smoke-package.nix {
    inherit pkgs sourceRevision expectedV4ManifestSha256;
  };
  existingProject = ../tools/kagari_v010_existing_project.py;
in
assert validRevision && validDigest;
assert pkgs.stdenv.hostPlatform.isLinux;
pkgs.runCommand "kagari-0.1.0-existing-smoke-test-only" {
  nativeBuildInputs = [ pkgs.python3 pkgs.coreutils ];
  passthru = {
    version = "0.1.0";
    qualification = "TEST_ONLY_UNQUALIFIED";
    qualifiesRuntime = false;
    allowsActivation = false;
  };
} ''
  set -eu
  test "$(sha256sum ${existingProject} | cut -d ' ' -f1)" = "9b5ccb2af9e15cbed20b790b3f3783432149b8656de81ee03f8bbff878b265d1"
  test "$(sha256sum ${modelSmoke}/share/kagari-v010-model-smoke/n1-manifest.json | cut -d ' ' -f1)" = "${expectedV4ManifestSha256}"
  root="$out/libexec/kagari-v010-existing-smoke"
  mkdir -p "$out/bin" "$root/tools" "$root/components/agent-core-v4" "$out/share/kagari-v010-existing-smoke"
  for name in kagari_v010_model_task.py kagari_v4_opencode_namespace_probe.py; do
    install -m 0444 "${modelSmoke}/libexec/kagari-v010-model-smoke/tools/$name" "$root/tools/$name"
  done
  install -m 0444 ${existingProject} "$root/tools/kagari_v010_existing_project.py"
  for source in ${modelSmoke}/libexec/kagari-v010-model-smoke/components/agent-core-v4/*.py; do
    install -m 0444 "$source" "$root/components/agent-core-v4/$(basename "$source")"
  done
  install -m 0444 ${modelSmoke}/share/kagari-v010-model-smoke/n1-manifest.json "$out/share/kagari-v010-existing-smoke/n1-manifest.json"
  cat > "$out/share/kagari-v010-existing-smoke/receipt.json" <<'JSON'
  {"version":"0.1.0","qualification":"TEST_ONLY_UNQUALIFIED","source_revision":"${sourceRevision}","n1_manifest_sha256":"${expectedV4ManifestSha256}","existing_project_adapter_sha256":"9b5ccb2af9e15cbed20b790b3f3783432149b8656de81ee03f8bbff878b265d1","released":false,"production_activation":false,"qualifies_runtime":false}
  JSON
  chmod 0444 "$out/share/kagari-v010-existing-smoke/receipt.json"
  cat > "$out/bin/kagari-v010-existing-smoke" <<EOF
  #!${pkgs.runtimeShell}
  exec ${pkgs.python3}/bin/python3 -I -B -c 'import runpy,sys; p=sys.argv.pop(1); sys.path.insert(0,p.rsplit("/",1)[0]); runpy.run_path(p,run_name="__main__")' "$root/tools/kagari_v010_existing_project.py" "\$@"
  EOF
  chmod 0555 "$out/bin/kagari-v010-existing-smoke"
  ${pkgs.python3}/bin/python3 -I -B - "$out" '${sourceRevision}' '${expectedV4ManifestSha256}' <<'PY'
  import hashlib, json, pathlib, sys
  out, revision, manifest_sha = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
  base = pathlib.Path('${modelSmoke}')
  upstream_bytes = (base / 'share/kagari-v010-model-smoke/n1-manifest.json').read_bytes()
  assert hashlib.sha256(upstream_bytes).hexdigest() == manifest_sha
  upstream = json.loads(upstream_bytes)
  assert upstream['source_revision'] == revision
  assert upstream['qualification'] == 'SOURCE_ONLY_UNQUALIFIED'
  manifest_bytes = (out / 'share/kagari-v010-existing-smoke/n1-manifest.json').read_bytes()
  assert hashlib.sha256(manifest_bytes).hexdigest() == manifest_sha
  manifest = json.loads(manifest_bytes)
  assert manifest['schema_version'] == 1
  assert manifest['kind'] == 'kagari-v4-native-module-bundle'
  assert manifest['target_version'] == '1.0.0'
  assert manifest['qualification'] == 'SOURCE_ONLY_UNQUALIFIED'
  assert manifest['source_revision'] == revision
  root = out / 'libexec/kagari-v010-existing-smoke'
  native = root / 'components/agent-core-v4'
  assert len(manifest['files']) == 12
  assert {p.name for p in native.iterdir()} == {
      pathlib.PurePosixPath(entry['path']).name for entry in manifest['files']
  }
  for entry in manifest['files']:
      data = (native / pathlib.PurePosixPath(entry['path']).name).read_bytes()
      assert hashlib.sha256(data).hexdigest() == entry['sha256']
      assert len(data) == entry['size']
  expected = {
      'kagari_v010_existing_project.py': '9b5ccb2af9e15cbed20b790b3f3783432149b8656de81ee03f8bbff878b265d1',
      'kagari_v010_model_task.py': 'c76713443223df0d8e859881fa8bc8c1ede9ad8206fa316e488ad1fa2e1082f8',
      'kagari_v4_opencode_namespace_probe.py': '177537ee97ae2c33abd82dcad572bdfd9f10b931ad246c429423b05a6563c1fe',
  }
  tools = root / 'tools'
  assert {p.name for p in tools.iterdir()} == set(expected)
  for name, sha in expected.items():
      assert hashlib.sha256((tools / name).read_bytes()).hexdigest() == sha
  PY
  "$out/bin/kagari-v010-existing-smoke" --help > /dev/null
''
