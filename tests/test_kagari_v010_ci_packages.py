"""Offline contract for dynamic #277 Nix CI shard package preparation."""
from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
import kagari_v010_ci_packages as p

class CIPackageSetupTests(unittest.TestCase):
    def test_source_package_profiles_are_exact(self):
        self.assertEqual(set(p.PACKAGES), {"native","existing","composed","rc"})
        self.assertEqual(set(p.CORE_PACKAGE_TEST_MODULES), {
          "test_kagari_v010_native_package",
          "test_kagari_v010_existing_smoke_package",
          "test_kagari_v010_composed_candidate",
          "test_kagari_v010_durable_admin_preview",
          "test_kagari_v010_rc_lifecycle"})
        observed=p.package_module_requirements(ROOT)
        self.assertEqual(observed["test_kagari_v010_durable_admin_preview"],
                         {"composed"})
        self.assertEqual(observed["test_kagari_v010_rc_lifecycle"], {"rc"})
        self.assertEqual(observed["test_kagari_v010_rc_opencode_bootstrap"], {"rc"})
        # Core modules remain mandatory, but new package-backed test
        # modules are discovered automatically rather than forbidden.
        self.assertTrue(set(p.CORE_PACKAGE_TEST_MODULES)<=set(observed))
        self.assertEqual(observed["test_kagari_v010_ci_packages"], {"composed","rc"})
        self.assertEqual(observed["test_kagari_v010_durable_task_bridge"],
                         {"composed"})
        self.assertEqual(set(x[1] for x in p.PACKAGES.values()),{
          "KAGARI_V010_NATIVE_GENERIC_PACKAGE",
          "KAGARI_V010_COMPOSED_PACKAGE",
          "KAGARI_V010_EXISTING_SMOKE_PACKAGE",
          "KAGARI_V010_RC_PACKAGE"})

    def test_new_test_module_package_env_is_discovered_and_unknown_is_blocked(self):
        self.assertEqual(p.source_package_reads(
            'import os\nx=os.environ.get("KAGARI_V010_COMPOSED_PACKAGE")\n'),
            {"composed"})
        self.assertEqual(p.source_package_reads(
            'import os\nx=os.getenv("KAGARI_V010_NATIVE_GENERIC_PACKAGE")\n'),
            {"native"})
        self.assertEqual(p.source_package_reads(
            'import os\nx=os.environ.get("KAGARI_V010_RC_PACKAGE")\n'),
            {"rc"})
        with self.assertRaisesRegex(p.CIBlocked,"unrecognized KAGARI"):
            p.source_package_reads(
                'import os\nx=os.environ.get("KAGARI_V010_UNKNOWN_PACKAGE")\n')
        # Strings mentioning env names in tests/fixtures are not dependencies.
        self.assertEqual(p.source_package_reads(
            'expected="KAGARI_V010_COMPOSED_PACKAGE"\n'),set())

    def test_exact_Nix_expressions_and_historical_baseline(self):
        for profile in sorted(p.PACKAGES):
            expr=p.package_expression(ROOT,profile)
            self.assertIn("builtins.getFlake",expr)
            self.assertIn("nixpkgs.legacyPackages.x86_64-linux",expr)
            self.assertIn(p.PACKAGES[profile][0],expr)
            self.assertIn("inherit pkgs;",expr)
            if profile=="existing":
                self.assertIn(p.BASE_REV,expr)
                self.assertIn(p.N1_SHA,expr)
            else:
                self.assertNotIn("sourceRevision =",expr)
        with self.assertRaisesRegex(p.CIBlocked,"unrecognized"):
            p.package_expression(ROOT,"whatever")

    def test_shard_assignment_reflects_canonical_suite(self):
        # Canonical runner may change assignments when any module is added.
        imports=p.package_assignment(ROOT)
        self.assertEqual(set(imports),set(range(8)))
        self.assertEqual(set(x for names in imports.values() for x in names),
                         set(p.PACKAGES))
        # A package can legitimately be required on more than one shard
        # after new test modules move. No duplicate output per individual shard.
        self.assertTrue(all(len(names)==len(set(names))
                            for names in imports.values()))
        for idx, names in imports.items():
            expected=p.run(ROOT,idx,requirements_only=True)
            self.assertEqual(expected,
                ["needs_nix=" + ("true" if names else "false")])
        with self.assertRaisesRegex(p.CIBlocked,"range"):
            p.run(ROOT,9,requirements_only=True)

    def test_moved_test_module_changes_needed_package_without_hardcoding(self):
        def different():
            return {shard:(
                ["native","composed"] if shard==0 else
                ["existing"] if shard==2 else [])
                for shard in range(8)}
        with patch.object(p,"package_assignment",side_effect=lambda root:different()):
            self.assertEqual(p.run(ROOT,0,requirements_only=True),["needs_nix=true"])
            self.assertEqual(p.run(ROOT,2,requirements_only=True),["needs_nix=true"])
            self.assertEqual(p.run(ROOT,7,requirements_only=True),["needs_nix=false"])
            with patch.object(p,"build",side_effect=lambda _root,profile:
                              (p.PACKAGES[profile][1],"/nix/store/fixture-"+profile)):
                self.assertEqual(len(p.run(ROOT,0,requirements_only=False)),2)
                self.assertEqual(len(p.run(ROOT,2,requirements_only=False)),1)

    def test_store_path_validator_rejects_wrong_type_and_multiline(self):
        prefix="/nix/store/"+"a"*32
        valid=prefix+"-kagari-0.1.0-native-generic-test-only"
        with patch.object(Path,"is_dir",return_value=True),\
             patch.object(Path,"is_symlink",return_value=False):
            self.assertEqual(str(p.parse_store_path(valid+"\n","native")),valid)
        for value in ("",valid+"\n"+valid,
                      valid.replace("native-generic","composed-unreleased"),
                      valid+"-additional","/tmp/"+Path(valid).name):
            with self.subTest(value=value):
                with self.assertRaises(p.CIBlocked):
                    p.parse_store_path(value,"native")

    def test_composed_candidate_receipt_binds_native_task_bridge_bytes(self):
        import os
        value=os.environ.get("KAGARI_V010_COMPOSED_PACKAGE")
        if not value:
            self.skipTest("package must be prepared by #277 CI before test")
        output=Path(value)
        p.verify_package(ROOT,"composed",output)
        receipt=p.receipt(output/"share/kagari-v010-composed/receipt.json")
        for filename,field in (
                ("kagari_v010_durable_admin_preview.py","durable_admin_sha256"),
                ("kagari_v010_durable_location.py","durable_location_sha256")):
            self.assertEqual(p.sha(ROOT/"tools"/filename),receipt[field])
        self.assertIs(receipt["durable_preview_qualified"],False)

    def test_rc_package_verifies_immutable_source_and_native_receipts(self):
        import os
        value=os.environ.get("KAGARI_V010_RC_PACKAGE")
        if not value:
            self.skipTest("RC package must be built by selected #277 CI")
        path=Path(value)
        p.verify_package(ROOT,"rc",path)
        data=p.receipt(path/"share/kagari-v010-rc/receipt.json")
        self.assertEqual(data["cli_sha256"],
                         p.sha(ROOT/"tools/kagari_v010_rc_lifecycle.py"))
        self.assertEqual(data["qualification"],"RC_RESTRICTED_PROFILE")
        self.assertEqual(data["publication_attestation"],"EXTERNAL_UNVERIFIED")
        self.assertEqual(data["release_channel"],"prerelease")
        self.assertNotIn("released",data)
        self.assertFalse(data["direct_live_Project_Task_supported"])

    def test_Nix_failure_and_invalid_build_output_never_return_package_env(self):
        with patch.object(p.subprocess,"run") as run:
            run.return_value=subprocess.CompletedProcess(["nix"],1,"","not available")
            with self.assertRaisesRegex(p.CIBlocked,"Nix build failed"):
                p.build(ROOT,"native")
        with patch.object(p.subprocess,"run") as run:
            run.return_value=subprocess.CompletedProcess(["nix"],0,"wrong store path\n","")
            with self.assertRaisesRegex(p.CIBlocked,"profile-matched"):
                p.build(ROOT,"native")
        self.assertEqual(p.main(["--shard","-1"]),2)

    def test_workflow_keeps_existing_complete_contract_reconciliation(self):
        text=(ROOT/".github/workflows/template-ci.yml").read_text()
        self.assertEqual(text.count("- name: Python contract tests"),1)
        self.assertIn("ci_contract_runner.py run",text)
        self.assertIn("ci_contract_runner.py reconcile",text)
        self.assertIn("cachix/install-nix-action@v31",text)
        self.assertIn("kagari_v010_ci_packages.py",text)
        self.assertIn("id: kagari_pkg_needs",text)
        self.assertIn("--requirements-only",text)
        self.assertIn("steps.kagari_pkg_needs.outputs.needs_nix == 'true'",text)
        self.assertIn('>> "$GITHUB_OUTPUT"',text)
        self.assertIn('>> "$GITHUB_ENV"',text)
        self.assertIn("if-no-files-found: error",text)
        self.assertIn("Require every original gate to pass",text)
        self.assertNotIn("KNOWN_CI_SKIP_IDS",text)

if __name__ == "__main__":
    unittest.main()
