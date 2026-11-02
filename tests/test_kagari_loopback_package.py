"""#339 opt-in KAGARI Control Server immutable Nix source composition.

Checks the package declaration, not production activation or Host grants.
The existing vNext CI package verifier checks every declared source copy's
bytes against the immutable Nix output in Nix-enabled CI.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
NIX = ROOT / "nix/kagari-vnext-control.nix"
CONTROL_SOURCES = (
    "kagari_loopback_control.py",
    "kagari_loopback_main_bridge.py",
)


class LoopbackPackageContractTests(unittest.TestCase):
    def test_exact_two_loopback_modules_have_immutable_source_copy(self):
        definition = NIX.read_text(encoding="utf-8")
        for name in CONTROL_SOURCES:
            with self.subTest(name=name):
                source = ROOT / "tools" / name
                self.assertTrue(source.is_file() and not source.is_symlink())
                expected = (
                    'cp ' + '$' + '{../tools/' + name + '} '
                    + '"$out/tools/' + name + '"'
                )
                # No duplicate source entries or a separate mutable file path.
                self.assertEqual(definition.count(expected), 1)
                self.assertIn(expected, definition)
        self.assertIn('name = "kagari-vnext-control";', definition)

    def test_no_second_listener_or_task_authority_entrypoint(self):
        definition = NIX.read_text(encoding="utf-8")
        # R10 is an importable Host-source inclusion only; #291 continues
        # to be the sole Main/Task authority, with separately approved launch.
        self.assertIn('name = "kagari-vnext-main";', definition)
        self.assertIn('name = "kagari-vnext-runtime";', definition)
        expected_bins = (
            "kagari-vnext-task", "kagari-vnext-session",
            "kagari-vnext-worktree-install", "kagari-vnext-host-prepare",
            "kagari-vnext-attach", "kagari-vnext-attachment-gate",
            "kagari-vnext-main", "kagari-vnext-runtime",
        )
        named_outputs = re.findall(r'(?m)^\s*name = "([^"]+)";', definition)
        self.assertCountEqual(
            named_outputs, (*expected_bins, "kagari-vnext-control"),
        )
        self.assertEqual(definition.count("pkgs.writeShellApplication {"), 8)
        self.assertEqual(definition.count("in pkgs.symlinkJoin {"), 1)
        self.assertIn(
            "paths = [ task session worktreeInstall hostPrepare "
            "hostAttach attachmentGate main runtime ];", definition,
        )
        self.assertEqual(
            len(re.findall(r'cp \$\{\.\./tools/kagari_loopback_'
                           r'[a-z_]+\.py\}', definition)),
            2,
        )


if __name__ == "__main__":
    unittest.main()
