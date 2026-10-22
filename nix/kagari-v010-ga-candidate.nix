# KAGARI 0.1.0 — isolated Work Unit RC candidate.
# Public release occurrence is externally attested, not embedded in build bytes.
# Human Release and production activation remain distinct gates. The existing native engine retains its tested internal
# TEST_ONLY qualification while the RC adapter adds non-disposable consumer
# attachment, immutable Task-specific consent and isolated Work Unit execution.
{ pkgs }:
let
  composed = import ./kagari-v010-composed-candidate.nix { inherit pkgs; };
  rcCli = ../tools/kagari_v010_ga_lifecycle.py;
  bootstrap = ../tools/kagari_v010_ga_opencode_bootstrap.py;
  durableProduct = ../tools/kagari_v010_ga_product_archive.py;
  pinnedPluginLock = ./kagari-v010-rc-opencode-plugin-lock.json;
  pinnedNodeLock = ./kagari-v010-rc-opencode-node-modules-lock.json;
  bun = pkgs.bun;
in
assert pkgs.stdenv.hostPlatform.isLinux;
pkgs.runCommand "kagari-0.1.0-ga-candidate" {
  nativeBuildInputs = [ pkgs.python3 pkgs.coreutils ];
  passthru = {
    version = "0.1.0";
    qualification = "GA_CANDIDATE_NOT_QUALIFIED";
    publicationAttestation = "EXTERNAL_UNVERIFIED";
    productionActivation = false;
    consumerAttachedOutsideProject = true;
    isolatedWorkUnitTaskProfile = true;
    directUserProjectTask = false;
  };
} ''
  set -eu
  root="$out/libexec/kagari-v010-ga"
  share="$out/share/kagari-v010-ga"
  mkdir -p "$out/bin" "$root" "$share"
  install -m 0444 ${rcCli} "$root/kagari_v010_ga_lifecycle.py"
  install -m 0444 ${bootstrap} "$root/kagari_v010_ga_opencode_bootstrap.py"
  install -m 0444 ${durableProduct} "$root/kagari_v010_ga_product_archive.py"
  install -m 0444 ${pinnedPluginLock} "$share/opencode-plugin-package-lock.json"
  install -m 0444 ${pinnedNodeLock} "$share/opencode-node-modules-package-lock.json"
  cat > "$out/bin/kagari-v010-ga" <<EOF
  #!${pkgs.runtimeShell}
  exec ${pkgs.python3}/bin/python3 -I -B "$root/kagari_v010_ga_lifecycle.py" "$out" "${composed}" "\$@"
  EOF
  chmod 0555 "$out/bin/kagari-v010-ga"
  ${pkgs.python3}/bin/python3 -I -B - "$out" "${composed}" <<'PY'
  import hashlib,json,pathlib,sys
  root,composed=(pathlib.Path(x) for x in sys.argv[1:])
  script=root/"libexec/kagari-v010-ga/kagari_v010_ga_lifecycle.py"
  prewarm=root/"libexec/kagari-v010-ga/kagari_v010_ga_opencode_bootstrap.py"
  archive=root/"libexec/kagari-v010-ga/kagari_v010_ga_product_archive.py"
  lock=root/"share/kagari-v010-ga/opencode-plugin-package-lock.json"
  node_lock=root/"share/kagari-v010-ga/opencode-node-modules-package-lock.json"
  composed_rec=composed/"share/kagari-v010-composed/receipt.json"
  h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
  assert script.is_file() and prewarm.is_file() and archive.is_file() and lock.is_file() and node_lock.is_file() and composed_rec.is_file()
  cref=json.loads(composed_rec.read_bytes())
  assert cref["qualification"]=="UNRELEASED_CANDIDATE"
  assert not cref["released"] and not cref["production_activation"]
  native=pathlib.Path(cref["native_root"])
  native_rec=native/"share/kagari-v010-native-generic/receipt.json"
  assert native_rec.is_file()
  nref=json.loads(native_rec.read_bytes())
  assert nref["qualification"]=="TEST_ONLY_UNQUALIFIED"
  assert nref["tool_sha256"]["kagari_v010_native_generic_task.py"]
  data={
    "schema_version":1,
    "version":"0.1.0",
    "qualification":"GA_CANDIDATE_NOT_QUALIFIED",
    "root":str(root),
    "cli_sha256":h(script),
    "opencode_bootstrap_sha256":h(prewarm),
    "durable_product_archive_sha256":h(archive),
    "opencode_plugin_lock_sha256":h(lock),
    "opencode_node_modules_lock_sha256":h(node_lock),
    "opencode_plugin_version":"1.18.35",
    "bun_executable":"${bun}/bin/bun",
    "bun_version":"${bun.version}",
    "opencode_dependencies_bootstrapped_before_Native_snapshot":True,
    "native_Project_scope_inventory_relaxed":False,
    "composed_root":str(composed),
    "composed_receipt_sha256":h(composed_rec),
    "native_root":str(native),
    "native_receipt_sha256":h(native_rec),
    "native_planner_sha256":nref["tool_sha256"]["kagari_v010_auto_planning.py"],
    "native_generic_task_sha256":nref["tool_sha256"]["kagari_v010_native_generic_task.py"],
    "source_baseline_revision":nref["baseline_source_revision"],
    "release_channel":"ga-candidate",
    "publication_attestation":"EXTERNAL_UNVERIFIED",
    "production_activation":False,
    "managed_isolated_Work_Unit_supported":True,
    "direct_live_Project_Task_supported":False,
    "native_component_qualification":"TEST_ONLY_UNQUALIFIED",
    "Human_Task_approval_per_job":True,
    "git_remote_Push_implemented":False,
    "allowed_Git_worktree_topology":"ordinary real Git .git directory",
  }
  path=root/"share/kagari-v010-ga/receipt.json"
  path.write_text(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(",",":"))+"\n")
  path.chmod(0o444)
  PY
  chmod 0555 "$out"
  "$out/bin/kagari-v010-ga" version >/dev/null
''
