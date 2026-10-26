"""Offline source/manifest and import/CLI checks; never invoke a model."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPRESSION = ROOT / "nix/kagari-v010-existing-smoke-package.nix"
SOURCES = {
    "kagari_v010_existing_project.py": "9b5ccb2af9e15cbed20b790b3f3783432149b8656de81ee03f8bbff878b265d1",
    "kagari_v010_model_task.py": "c76713443223df0d8e859881fa8bc8c1ede9ad8206fa316e488ad1fa2e1082f8",
    "kagari_v4_opencode_namespace_probe.py": "177537ee97ae2c33abd82dcad572bdfd9f10b931ad246c429423b05a6563c1fe",
}
MODULES = {
    "cleanup_resources.py", "collaboration_git.py", "evidence.py",
    "integration_git.py", "metadata_codec.py", "metadata_ref.py",
    "operation_prerequisites.py", "task_cleanup.py", "task_collaboration.py",
    "task_integration.py", "task_record.py", "task_view.py",
}
# Source-only opt-in next-MVI code. Not part of the fixed GA package manifest.
UNRELEASED_NEXT_MVI_MODULES = {
    "issue_worktree.py", "issue_worktree_cleanup.py",
}


def check_cli(test, argv):
    for flags, code, message in (
        (["--help"], 0, "--project-root"),
        ([], 2, "explicit --execute-model"),
        (["--project-root", "/tmp/project", "--output", "/tmp/out",
          "--executable", "/tmp/opencode"], 2, "explicit --execute-model"),
    ):
        result = subprocess.run(
            [*argv, *flags], cwd="/", capture_output=True, text=True,
            timeout=15, check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        test.assertEqual(result.returncode, code, result.stderr)
        test.assertIn(message, result.stdout if code == 0 else result.stderr)


class ExistingSmokePackageTests(unittest.TestCase):
    def test_source_and_expression_guards(self):
        expression = EXPRESSION.read_text()
        self.assertIn("./kagari-v010-model-smoke-package.nix", expression)
        self.assertIn("${expectedV4ManifestSha256}", expression)
        self.assertIn("'target_version'] == '1.0.0'", expression)
        self.assertIn('qualification = "TEST_ONLY_UNQUALIFIED"', expression)
        self.assertIn("allowsActivation = false;", expression)
        self.assertNotIn("--model", expression)
        for name, sha in SOURCES.items():
            self.assertEqual(hashlib.sha256((ROOT / "tools" / name).read_bytes()).hexdigest(), sha)
            self.assertIn(sha, expression)
        source_modules = {p.name for p in (ROOT / "components/agent-core-v4").glob("*.py")}
        self.assertEqual(source_modules, MODULES | UNRELEASED_NEXT_MVI_MODULES)
        self.assertTrue(UNRELEASED_NEXT_MVI_MODULES.isdisjoint(MODULES))

    def test_nix_expression_parses(self):
        nix = shutil.which("nix-instantiate")
        if nix is None:
            self.skipTest("nix-instantiate not available")
        result = subprocess.run([nix, "--parse", str(EXPRESSION)], capture_output=True,
                                text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_isolated_source_import_and_cli_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = root / "tools"
            native = root / "components/agent-core-v4"
            tools.mkdir()
            native.mkdir(parents=True)
            for name in SOURCES:
                shutil.copyfile(ROOT / "tools" / name, tools / name)
            for name in MODULES:
                shutil.copyfile(ROOT / "components/agent-core-v4" / name, native / name)
            check_cli(self, [sys.executable, "-I", "-B", "-c",
                             'import runpy,sys; p=sys.argv.pop(1); sys.path.insert(0,p.rsplit("/",1)[0]); runpy.run_path(p,run_name="__main__")',
                             str(tools / "kagari_v010_existing_project.py")])

    def test_real_store_package_if_supplied(self):
        path = os.environ.get("KAGARI_V010_EXISTING_SMOKE_PACKAGE")
        if not path:
            self.skipTest("set KAGARI_V010_EXISTING_SMOKE_PACKAGE to built store path")
        package = Path(path)
        self.assertTrue(package.is_absolute() and package.is_dir())
        native = package / "libexec/kagari-v010-existing-smoke/components/agent-core-v4"
        tools = package / "libexec/kagari-v010-existing-smoke/tools"
        manifest_bytes = (package / "share/kagari-v010-existing-smoke/n1-manifest.json").read_bytes()
        receipt = json.loads((package / "share/kagari-v010-existing-smoke/receipt.json").read_bytes())
        self.assertEqual(receipt["qualification"], "TEST_ONLY_UNQUALIFIED")
        self.assertFalse(receipt["released"] or receipt["production_activation"] or receipt["qualifies_runtime"])
        self.assertEqual(hashlib.sha256(manifest_bytes).hexdigest(), receipt["n1_manifest_sha256"])
        manifest = json.loads(manifest_bytes)
        self.assertEqual(manifest["target_version"], "1.0.0")
        self.assertEqual(manifest["source_revision"], receipt["source_revision"])
        self.assertEqual(manifest["qualification"], "SOURCE_ONLY_UNQUALIFIED")
        self.assertEqual({p.name for p in native.glob("*.py")}, MODULES)
        for entry in manifest["files"]:
            data = (native / Path(entry["path"]).name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
            self.assertEqual(len(data), entry["size"])
        self.assertEqual({p.name for p in tools.glob("*.py")}, set(SOURCES))
        for name, sha in SOURCES.items():
            self.assertEqual(hashlib.sha256((tools / name).read_bytes()).hexdigest(), sha)
        self.assertEqual(receipt["existing_project_adapter_sha256"], SOURCES["kagari_v010_existing_project.py"])
        check_cli(self, [str(package / "bin/kagari-v010-existing-smoke")])


if __name__ == "__main__":
    unittest.main()
