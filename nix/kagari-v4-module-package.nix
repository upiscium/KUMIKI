# Unreleased N1 experiment. Not an approved KAGARI runtime or Admin launcher.
{ pkgs, sourceRevision }:
let
  # Fixed source identity; caller cannot substitute another repository.
  pinnedSource = builtins.fetchGit {
    url = "https://github.com/upiscium/Templates.git";
    rev = sourceRevision;
  };
  validRevision = builtins.match "([0-9a-f]{40}|[0-9a-f]{64})" sourceRevision != null;
  modules = [
    "cleanup_resources.py"
    "collaboration_git.py"
    "evidence.py"
    "integration_git.py"
    "metadata_codec.py"
    "metadata_ref.py"
    "operation_prerequisites.py"
    "task_cleanup.py"
    "task_collaboration.py"
    "task_integration.py"
    "task_record.py"
    "task_view.py"
  ];
in
assert validRevision;
pkgs.runCommand "kagari-v4-native-module-candidate" {
  nativeBuildInputs = [ pkgs.python3 ];
  passthru.qualifyingAuthority = false;
} ''
  set -eu
  source_dir=${pinnedSource}/components/agent-core-v4
  test -d ${pinnedSource}/components
  test ! -L ${pinnedSource}/components
  test -d "$source_dir"
  test ! -L "$source_dir"
  # Reject an unexpected source entry before creating/copied output files.
  python3 -I - "$source_dir" '${builtins.concatStringsSep "," modules}' <<'PY'
  from pathlib import Path
  import sys
  expected = set(sys.argv[2].split(","))
  if {p.name for p in Path(sys.argv[1]).iterdir()} != expected:
      raise SystemExit("unexpected native v4 source inventory")
  PY
  # Preflight every source file and the total budget before copying a module.
  total_bytes=0
  for name in ${builtins.concatStringsSep " " modules}; do
    test -f "$source_dir/$name"
    test ! -L "$source_dir/$name"
    test "$(stat -c %a "$source_dir/$name")" = 444
    file_bytes=$(stat -c %s "$source_dir/$name")
    test "$file_bytes" -le 4194304
    total_bytes=$((total_bytes + file_bytes))
    test "$total_bytes" -le 16777216
  done
  mkdir -p "$out/lib/kagari_v4"
  for name in ${builtins.concatStringsSep " " modules}; do
    test -f "$source_dir/$name"
    test ! -L "$source_dir/$name"
    # Pinned Git source files in the Nix store must be non-executable.
    test "$(stat -c %a "$source_dir/$name")" = 444
    install -m 0444 "$source_dir/$name" "$out/lib/kagari_v4/$name"
  done
  python3 -I - "$out" "$source_dir" '${sourceRevision}' <<'PY'
  import hashlib
  import json
  import pathlib
  import stat
  import sys

  output = pathlib.Path(sys.argv[1])
  source = pathlib.Path(sys.argv[2])
  source_revision = sys.argv[3]
  expected = [
      "cleanup_resources.py", "collaboration_git.py", "evidence.py",
      "integration_git.py", "metadata_codec.py", "metadata_ref.py",
      "operation_prerequisites.py", "task_cleanup.py", "task_collaboration.py",
      "task_integration.py", "task_record.py", "task_view.py",
  ]
  if {p.name for p in source.iterdir()} != set(expected):
      raise SystemExit("Unexpected v4 source module inventory")
  module_dir = output / "lib" / "kagari_v4"
  entries = []
  total_bytes = 0
  for name in sorted(expected):
      original = (source / name).read_bytes()
      target = module_dir / name
      package_bytes = target.read_bytes()
      total_bytes += len(package_bytes)
      if len(package_bytes) > 4 * 1024 * 1024 or total_bytes > 16 * 1024 * 1024:
          raise SystemExit("Native module byte budget exceeded")
      mode = stat.S_IMODE(target.lstat().st_mode)
      if original != package_bytes or mode != 0o444:
          raise SystemExit("Bundle byte or mode mismatch: " + name)
      entries.append({
          "path": "lib/kagari_v4/" + name,
          "sha256": hashlib.sha256(package_bytes).hexdigest(),
          "size": len(package_bytes),
          "mode": mode,
      })
  manifest = {
      "schema_version": 1,
      "kind": "kagari-v4-native-module-bundle",
      "qualification": "SOURCE_ONLY_UNQUALIFIED",
      "target_version": "1.0.0",
      "source_revision": source_revision,
      "source_repository_hint": "upiscium/Templates",
      "files": entries,
  }
  content = (
      json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
  ).encode("utf-8")
  (output / "manifest.json").write_bytes(content)
  PY
  chmod 0444 "$out/manifest.json"
''
