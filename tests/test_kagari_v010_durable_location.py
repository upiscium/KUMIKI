"""#256/#275 read-only durable external runtime location and Git-profile contract."""
from __future__ import annotations
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"tools"))
import kagari_v010_durable_location as d

class DurableLocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="kagari-v010-durable-")
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.project=root/"consumer"
        self.project.mkdir()
        self.state=root/"state"
        self.state.mkdir(mode=0o700)
        self.git("init","-q","-b","main")
        (self.project/"README.md").write_text("original\n")
        (self.project/"AGENTS.md").write_text("# user rules\n")
        (self.project/"opencode.json").write_text('{"permission":{"task":"deny"}}\n')
        self.git("add","-A")
        self.git("-c","user.name=Fixture","-c","user.email=fixture@example.invalid",
                 "commit","-qm","baseline")
        self.first=self.git("rev-parse","HEAD")
        (self.project/"README.md").write_text("a staged non-KAGARI user edit\n")
        self.git("add","README.md")
        self.prior_index=(self.project/".git/index").read_bytes()
        self.before={p:(self.project/p).read_bytes()
                     for p in ("README.md","AGENTS.md","opencode.json")}

    def git(self,*args):
        return subprocess.run(["git","-C",str(self.project),*args],
                              check=True,capture_output=True,timeout=15).stdout.decode().strip()

    def preserved(self):
        self.assertEqual(self.git("rev-parse","HEAD"),self.first)
        self.assertEqual((self.project/".git/index").read_bytes(),self.prior_index)
        for p,v in self.before.items():
            self.assertEqual((self.project/p).read_bytes(),v)

    def test_readonly_foreign_project_plans_durable_external_runtime(self):
        planned=d.plan(self.project,self.state)
        self.assertEqual(planned["status"],"READ_ONLY_DURABLE_LOCATION_PLAN")
        self.assertIs(planned["production_install_authorized"],False)
        self.assertIs(planned["task_permission_granted"],False)
        self.assertFalse(planned["runtime_location_exists"])
        self.assertEqual(planned["project"]["head"],self.first)
        self.assertIn("/kagari-v010/kagari-v010-consent-install-",
                      planned["runtime_root"])
        self.assertTrue(planned["runtime_root"].endswith("/runtime"))
        self.assertFalse((self.state/d.NAMESPACE).exists())
        self.assertEqual(d.plan(self.project,self.state),planned)
        self.preserved()

    def test_project_identity_follows_physical_directory_not_head(self):
        first=d.plan(self.project,self.state)
        # Reset cached staged data only within a disposable Git fixture.
        self.git("commit","-qm","unrelated Product update")
        changed=d.plan(self.project,self.state)
        self.assertEqual(changed["runtime_root"],first["runtime_root"])
        self.assertNotEqual(changed["project"]["head"],first["project"]["head"])

    def test_existing_owned_private_dirs_are_detected_without_mutation(self):
        p=d.plan(self.project,self.state)
        folder=Path(p["runtime_root"])
        folder.mkdir(parents=True,mode=0o700)
        for q in (folder.parent.parent,folder.parent,folder):
            q.chmod(0o700)
        observed=d.plan(self.project,self.state)
        self.assertTrue(observed["runtime_location_exists"])
        self.assertIs(observed["production_install_authorized"],False)
        self.preserved()

    def test_wrong_mode_and_symlink_runtime_locations_block(self):
        proposed=d.plan(self.project,self.state)
        namespace=Path(proposed["namespace"])
        namespace.mkdir(mode=0o755)
        with self.assertRaisesRegex(d.LocationBlocked,"owner/mode/mount"):
            d.plan(self.project,self.state)
        namespace.chmod(0o700)
        folder=Path(proposed["runtime_parent"])
        folder.symlink_to(self.project,target_is_directory=True)
        with self.assertRaises(d.LocationBlocked):
            d.plan(self.project,self.state)
        self.preserved()

    def test_linked_worktree_gitfile_blocks_at_doctor_not_mutated(self):
        branch=self.project.parent/"linked"
        self.git("worktree","add","-q","-b","linked-test",str(branch))
        self.assertTrue((branch/".git").is_file())
        with self.assertRaisesRegex(d.LocationBlocked,"linked worktree unsupported"):
            d.plan(branch,self.state)
        self.preserved()

    def test_unknown_git_root_and_overlapping_state_are_rejected(self):
        with self.assertRaisesRegex(d.LocationBlocked,"overlap"):
            d.plan(self.project,self.project)
        other=Path(self.tmp.name)/"not-a-git-repository"
        other.mkdir()
        with self.assertRaisesRegex(d.LocationBlocked,"ordinary Git"):
            d.plan(other,self.state)
        self.preserved()

if __name__=="__main__":
    unittest.main()
