# KAGARI 0.1.0 composed release candidate, still TEST_ONLY / not production.
{ pkgs }:
let
  native = import ./kagari-v010-native-generic-package.nix { inherit pkgs; };
  cli = ../tools/kagari_v010_composed_cli.py;
  durableAdmin = ../tools/kagari_v010_durable_admin_preview.py;
  durableLocation = ../tools/kagari_v010_durable_location.py;
in
assert pkgs.stdenv.hostPlatform.isLinux;
pkgs.runCommand "kagari-0.1.0-composed-unreleased-candidate" {
  nativeBuildInputs = [ pkgs.python3 pkgs.coreutils ];
  passthru = {
    version = "0.1.0";
    qualification = "UNRELEASED_CANDIDATE";
    released = false;
    productionActivation = false;
    consumerProductionQualified = false;
  };
} ''
  set -eu
  root="$out/libexec/kagari-v010-composed"
  share="$out/share/kagari-v010-composed"
  mkdir -p "$out/bin" "$root" "$share"
  install -m 0444 ${cli} "$root/kagari_v010_composed_cli.py"
  install -m 0444 ${durableAdmin} "$root/kagari_v010_durable_admin_preview.py"
  install -m 0444 ${durableLocation} "$root/kagari_v010_durable_location.py"
  cat > "$out/bin/kagari-v010-candidate" <<EOF
  #!${pkgs.runtimeShell}
  exec ${pkgs.python3}/bin/python3 -I -B "$root/kagari_v010_composed_cli.py" "$out" "${native}" "\$@"
  EOF
  chmod 0555 "$out/bin/kagari-v010-candidate"
  cat > "$out/bin/kagari-v010-durable-preview" <<EOF
  #!${pkgs.runtimeShell}
  exec ${pkgs.python3}/bin/python3 -I -B -c 'import runpy,sys; p=sys.argv.pop(1); sys.path.insert(0,p.rsplit("/",1)[0]); runpy.run_path(p,run_name="__main__")' "$root/kagari_v010_durable_admin_preview.py" "$out" "${native}" "\$@"
  EOF
  chmod 0555 "$out/bin/kagari-v010-durable-preview"
  ${pkgs.python3}/bin/python3 -I -B - "$out" "${native}" <<'PY'
  import hashlib, json, pathlib, sys
  root, native = (pathlib.Path(x) for x in sys.argv[1:3])
  script = root / "libexec/kagari-v010-composed/kagari_v010_composed_cli.py"
  durable_admin = root / "libexec/kagari-v010-composed/kagari_v010_durable_admin_preview.py"
  durable_location = root / "libexec/kagari-v010-composed/kagari_v010_durable_location.py"
  existing = native / "share/kagari-v010-native-generic/receipt.json"
  admin = native / "bin/kagari-v010-consent-admin-test-only"
  h = lambda f: hashlib.sha256(f.read_bytes()).hexdigest()
  assert (script.is_file() and durable_admin.is_file()
          and durable_location.is_file()
          and existing.is_file() and admin.is_file())
  baseline = json.loads(existing.read_bytes())
  assert baseline["public_target_version"] == "0.1.0"
  assert baseline["qualification"] == "TEST_ONLY_UNQUALIFIED"
  assert baseline["released"] is False
  assert baseline["production_activation"] is False
  receipt = {
    "version": "0.1.0",
    "qualification": "UNRELEASED_CANDIDATE",
    "released": False,
    "production_activation": False,
    "consumer_production_qualified": False,
    "generic_task_qualified": False,
    "source_publication_approved": False,
    "candidate_root": str(root),
    "native_root": str(native),
    "native_receipt_sha256": h(existing),
    "native_admin_sha256": h(admin),
    "cli_sha256": h(script),
    "durable_admin_sha256": h(durable_admin),
    "durable_location_sha256": h(durable_location),
    "durable_preview_qualified": False,
    "source_baseline_revision": baseline["baseline_source_revision"],
    "receipt_schema": 1,
    "native_runtime_identity": baseline["tool_sha256"]["kagari_v010_native_generic_task.py"],
    "native_auto_planner_identity": baseline["tool_sha256"]["kagari_v010_auto_planning.py"],
    "human_consent_scope": "LOCAL_TEST_ONLY_NOT_VERIFIED_REMOTE",
  }
  path = root / "share/kagari-v010-composed/receipt.json"
  path.write_text(json.dumps(receipt, sort_keys=True) + "\n")
  path.chmod(0o444)
  PY
  "$out/bin/kagari-v010-candidate" version >/dev/null
  "$out/bin/kagari-v010-candidate" status >/dev/null
  "$out/bin/kagari-v010-candidate" doctor >/dev/null
  "$out/bin/kagari-v010-durable-preview" --help >/dev/null
''