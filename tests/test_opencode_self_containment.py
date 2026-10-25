from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = (
    "agent-base",
    "agent-python",
    "agent-rust",
    "agent-nix",
    "agent-cpp-cmake",
    "agent-typescript-node",
)


class OpencodeSelfContainmentTest(unittest.TestCase):
    def test_root_flake_and_lock_have_no_external_contract_input_or_check(self) -> None:
        flake = (ROOT / "flake.nix").read_text(encoding="utf-8")
        lock_text = (ROOT / "flake.lock").read_text(encoding="utf-8")
        lock = json.loads(lock_text)
        root_inputs = lock["nodes"][lock["root"]].get("inputs", {})

        for identity in ("opencodeContract", "OpencodeContract", "opencode-contract"):
            with self.subTest(identity=identity):
                self.assertNotIn(identity, flake)
                self.assertNotIn(identity, lock_text)
        self.assertNotIn("opencodeContract", root_inputs)
        self.assertNotIn("OpencodeContract", root_inputs)

    def test_retired_updater_workflow_and_helper_are_absent(self) -> None:
        self.assertFalse(
            (ROOT / ".github" / "workflows" / "update-opencode-contract.yml").exists()
        )
        self.assertFalse((ROOT / "tools" / "check_opencode_contract_lock_update.py").exists())

    def test_retired_permission_manifests_are_absent_from_core_and_templates(self) -> None:
        source_directories = [ROOT / "components" / "agent-core"]
        source_directories.extend(ROOT / "templates" / template for template in TEMPLATES)

        for directory in source_directories:
            with self.subTest(directory=directory.relative_to(ROOT)):
                self.assertTrue(directory.is_dir())
                self.assertFalse((directory / "opencode-contract-permissions.toml").exists())

    def test_generated_flakes_do_not_depend_on_the_retired_contract_input(self) -> None:
        for template in TEMPLATES:
            flake = (ROOT / "templates" / template / "flake.nix").read_text(encoding="utf-8")
            with self.subTest(template=template):
                for identity in ("opencodeContract", "OpencodeContract", "opencode-contract"):
                    self.assertNotIn(identity, flake)

    def test_agent_core_v3_version_retained_and_current_upstream_is_kumiki(self) -> None:
        core = ROOT / "components" / "agent-core" / ".automation"
        self.assertEqual("3\n", (core / "VERSION").read_text(encoding="utf-8"))
        self.assertEqual(
            'repository = "github:upiscium/KUMIKI"\n'
            'ref = "main"\n'
            'component = "components/agent-core"\n',
            (core / "UPSTREAM").read_text(encoding="utf-8"),
        )

    def test_legacy_root_manifest_is_outside_current_upgrade_ownership(self) -> None:
        script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import automation_upgrade as upgrade
legacy = Path("opencode-contract-permissions.toml")
assert not upgrade.managed(legacy)
assert not upgrade.deletable_managed(legacy)
try:
    upgrade.migration_path(str(legacy), field="remove_paths", managed_delete=True)
except upgrade.UpgradeError:
    pass
else:
    raise AssertionError("legacy root file must not become an upgrade deletion target")
"""
        result = subprocess.run(
            [sys.executable, "-I", "-c", script,
             str(ROOT / "components" / "agent-core" / ".automation" / "bin")],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ci_keeps_local_verification_and_drops_the_external_gate(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "template-ci.yml").read_text(
            encoding="utf-8"
        )
        for local_check in (
            "python3 tools/ci_contract_runner.py verify",
            "python3 tools/ci_contract_runner.py run",
            "python3 tools/ci_contract_runner.py reconcile",
            "python3 tools/render_templates.py check",
            "nix flake check --all-systems --no-build --no-update-lock-file",
            "tests.test_source_bootstrap_upgrade_bridge.BootstrapUpgradeBridgeTest.test_installed_maintenance_check_preserves_active_receipt_paths",
        ):
            self.assertIn(local_check, workflow)
        self.assertIn("Public maintenance launcher smoke", workflow)
        for external_gate in ("OpencodeContract", "opencodeContract", "opencode-contract"):
            self.assertNotIn(external_gate, workflow)


if __name__ == "__main__":
    unittest.main()
