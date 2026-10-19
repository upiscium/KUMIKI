# Local test-only packaged TaskSpec -> native two-subagent -> v4 execution.
# Never claim generic Project install, publication, or v0.1.0 release authority.
{ pkgs }:
let
  sourceRevision = "5e0a4fd35b0e2e8014ff35f2ce7dc8e600e84812";
  manifestSha256 = "521b1f6dbda3f4a1fc8937f8ace3f560d24a1931b3317d52162652ce457ec07c";
  existingSmoke = import ./kagari-v010-existing-smoke-package.nix {
    inherit pkgs sourceRevision;
    expectedV4ManifestSha256 = manifestSha256;
  };
  main = ../tools/kagari_v010_native_generic_task.py;
  spec = ../tools/kagari_v010_general_task_spec.py;
  plan = ../tools/kagari_v010_basic_plan.py;
  autoPlanner = ../tools/kagari_v010_auto_planning.py;
  proof = ../tools/kagari_v010_native_subagent_proof.py;
  agentsCompat = ../tools/kagari_v010_agents_compat.py;
  humanAsk = ../tools/kagari_v010_human_ask.py;
  consentAdmin = ../tools/kagari_v010_consent_admin.py;
in
assert pkgs.stdenv.hostPlatform.isLinux;
pkgs.runCommand "kagari-0.1.0-native-generic-test-only" {
  nativeBuildInputs = [ pkgs.python3 pkgs.coreutils ];
  passthru = {
    version = "0.1.0";
    qualification = "TEST_ONLY_UNQUALIFIED";
    released = false;
    productionActivation = false;
    genericProductionTaskQualified = false;
  };
} ''
  set -eu
  root="$out/libexec/kagari-v010-native-generic"
  share="$out/share/kagari-v010-native-generic"
  mkdir -p "$out/bin" "$root/tools" "$root/components/agent-core-v4" "$share"
  install -m 0444 ${main} "$root/tools/kagari_v010_native_generic_task.py"
  install -m 0444 ${spec} "$root/tools/kagari_v010_general_task_spec.py"
  install -m 0444 ${plan} "$root/tools/kagari_v010_basic_plan.py"
  install -m 0444 ${autoPlanner} "$root/tools/kagari_v010_auto_planning.py"
  install -m 0444 ${proof} "$root/tools/kagari_v010_native_subagent_proof.py"
  install -m 0444 ${agentsCompat} "$root/tools/kagari_v010_agents_compat.py"
  install -m 0444 ${humanAsk} "$root/tools/kagari_v010_human_ask.py"
  install -m 0444 ${consentAdmin} "$root/tools/kagari_v010_consent_admin.py"
  for f in ${existingSmoke}/libexec/kagari-v010-existing-smoke/components/agent-core-v4/*.py; do
    install -m 0444 "$f" "$root/components/agent-core-v4/$(basename "$f")"
  done
  install -m 0444 ${existingSmoke}/share/kagari-v010-existing-smoke/n1-manifest.json "$share/n1-manifest.json"
  cat > "$out/bin/kagari-v010-native-generic-test-only" <<EOF
  #!${pkgs.runtimeShell}
  exec ${pkgs.python3}/bin/python3 -I -B -c 'import runpy,sys; p=sys.argv.pop(1); sys.path.insert(0,p.rsplit("/",1)[0]); runpy.run_path(p,run_name="__main__")' "$root/tools/kagari_v010_native_generic_task.py" "\$@"
  EOF
  cat > "$out/bin/kagari-v010-native-subagent-proof-test-only" <<EOF
  #!${pkgs.runtimeShell}
  exec ${pkgs.python3}/bin/python3 -I -B -c 'import runpy,sys; p=sys.argv.pop(1); sys.path.insert(0,p.rsplit("/",1)[0]); runpy.run_path(p,run_name="__main__")' "$root/tools/kagari_v010_native_subagent_proof.py" "\$@"
  EOF
  cat > "$out/bin/kagari-v010-agents-compat-test-only" <<EOF
  #!${pkgs.runtimeShell}
  exec ${pkgs.python3}/bin/python3 -I -B -c 'import runpy,sys; p=sys.argv.pop(1); sys.path.insert(0,p.rsplit("/",1)[0]); runpy.run_path(p,run_name="__main__")' "$root/tools/kagari_v010_agents_compat.py" "\$@"
  EOF
  cat > "$out/bin/kagari-v010-human-ask-test-only" <<EOF
  #!${pkgs.runtimeShell}
  exec ${pkgs.python3}/bin/python3 -I -B -c 'import runpy,sys; p=sys.argv.pop(1); sys.path.insert(0,p.rsplit("/",1)[0]); runpy.run_path(p,run_name="__main__")' "$root/tools/kagari_v010_human_ask.py" "\$@"
  EOF
  cat > "$out/bin/kagari-v010-consent-admin-test-only" <<EOF
  #!${pkgs.runtimeShell}
  exec ${pkgs.python3}/bin/python3 -I -B -c 'import runpy,sys; p=sys.argv.pop(1); sys.path.insert(0,p.rsplit("/",1)[0]); runpy.run_path(p,run_name="__main__")' "$root/tools/kagari_v010_consent_admin.py" "\$@"
  EOF
  chmod 0555 "$out/bin/"*
  ${pkgs.python3}/bin/python3 -I -B - "$out" <<'PY'
  import hashlib, json, pathlib, stat, sys
  dest = pathlib.Path(sys.argv[1])
  lib = dest / "libexec/kagari-v010-native-generic"
  share = dest / "share/kagari-v010-native-generic"
  names = (
      "kagari_v010_native_generic_task.py",
      "kagari_v010_general_task_spec.py",
      "kagari_v010_basic_plan.py",
      "kagari_v010_auto_planning.py",
      "kagari_v010_native_subagent_proof.py",
      "kagari_v010_agents_compat.py",
      "kagari_v010_human_ask.py",
      "kagari_v010_consent_admin.py",
  )
  tools = {name: hashlib.sha256((lib / "tools" / name).read_bytes()).hexdigest()
           for name in names}
  wrappers = (
      "kagari-v010-native-generic-test-only",
      "kagari-v010-native-subagent-proof-test-only",
      "kagari-v010-agents-compat-test-only",
      "kagari-v010-human-ask-test-only",
      "kagari-v010-consent-admin-test-only",
  )
  wrapper_hashes = {
      name: hashlib.sha256((dest / "bin" / name).read_bytes()).hexdigest()
      for name in wrappers
  }
  n1 = (share / "n1-manifest.json").read_bytes()
  assert hashlib.sha256(n1).hexdigest() == "${manifestSha256}"
  original = json.loads(n1)
  assert original["source_revision"] == "${sourceRevision}"
  assert original["target_version"] == "1.0.0"
  assert original["qualification"] == "SOURCE_ONLY_UNQUALIFIED"
  module_hashes = {
      pathlib.Path(item["path"]).name: hashlib.sha256(
          (lib / "components/agent-core-v4" /
           pathlib.Path(item["path"]).name).read_bytes()).hexdigest()
      for item in original["files"]
  }
  receipt = {
      "public_target_version": "0.1.0",
      "baseline_source_revision": "${sourceRevision}",
      "n1_manifest_sha256": "${manifestSha256}",
      "tool_sha256": tools,
      "wrapper_sha256": wrapper_hashes,
      "v4_module_sha256": module_hashes,
      "qualification": "TEST_ONLY_UNQUALIFIED",
      "candidate_tools_uncommitted": True,
      "released": False,
      "production_activation": False,
      "generic_production_task_qualified": False,
      "agents_read_only_inspect_supported": True,
      "agents_production_consent_or_mutation_supported": False,
      "human_ask_local_tty_test_only_supported": True,
      "human_ask_remote_user_identity_verified": False,
      "human_ask_production_qualified": False,
      "human_ask_admin_lifecycle_test_only_supported": True,
      "human_ask_admin_production_qualified": False,
      "host_observed_auto_planning_test_only_supported": True,
      "model_planner_provider_identity_attested": False,
      "auto_planning_production_qualified": False,
      "source_publication_approved": False,
  }
  (share / "receipt.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")
  (share / "receipt.json").chmod(0o444)
  expected_modules = {pathlib.Path(item["path"]).name for item in original["files"]}
  actual_modules = {p.name for p in (lib / "components/agent-core-v4").iterdir()}
  assert expected_modules == actual_modules and len(actual_modules) == 12
  for item in original["files"]:
      file = lib / "components/agent-core-v4" / pathlib.Path(item["path"]).name
      assert hashlib.sha256(file.read_bytes()).hexdigest() == item["sha256"]
      assert stat.S_IMODE(file.stat().st_mode) == 0o444
  for name in tools:
      assert stat.S_IMODE((lib / "tools" / name).stat().st_mode) == 0o444
  PY
  "$out/bin/kagari-v010-native-generic-test-only" --help >/dev/null
  "$out/bin/kagari-v010-native-subagent-proof-test-only" --help >/dev/null
  "$out/bin/kagari-v010-agents-compat-test-only" --help >/dev/null
  "$out/bin/kagari-v010-human-ask-test-only" --help >/dev/null
  "$out/bin/kagari-v010-consent-admin-test-only" --help >/dev/null
''