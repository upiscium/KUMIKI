"""#304: external vNext host attach preflight -> two issue-linked Worktrees.

This exercises the two independently packaged owners together, NOT a live
Main/Session controller or v4 runtime activation. Disposable real Git only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

from tests import test_metadata_ref as fixtures

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "components/agent-core-v4"))
import kagari_vnext_attach as attach
from issue_worktree import IssueWorktreeAllocator, IssueWorktreeError
from task_record import TaskRecords


class HostAttachWorktreeIntegrationTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.MetadataRefTest("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.project = fixture.product
        self.store = fixture.store
        self.root = Path(fixture.temp_root)
        self.home = self.root / "explicit-attach-state"
        self.home.mkdir(mode=0o700)
        self.package = self.root / "immutable-test-package"
        (self.package / "bin").mkdir(parents=True)
        for name in attach.REQUIRED_BINARIES:
            executable = self.package / "bin" / name
            executable.write_bytes(b"#!/bin/sh\nexit 99 # never execute payload\n")
            executable.chmod(0o555)
        (self.package / "bin").chmod(0o555)
        self.package.chmod(0o555)
        self.addCleanup(self.restore_package_modes)
        self.base = fixtures.git("rev-parse", "HEAD", cwd=self.project).decode().strip()
        self.index = (self.project / ".git/index").read_bytes()
        self.records = TaskRecords(self.store, authorize=lambda _req: True)
        self.allocator = IssueWorktreeAllocator(self.store)

    def restore_package_modes(self):
        self.package.chmod(0o755)
        (self.package / "bin").chmod(0o755)

    def attach(self, operation="install"):
        return attach.operate(self.project, self.home, self.package, operation,
                              attach.PROFILE)

    def registered(self, issue):
        n = str(issue)
        branch = "refs/heads/issue-" + n
        record = self.records.create(
            task=n, branch_ref=branch, base_revision=self.base,
            title="Issue " + n, body="Synthetic explicit authorized worktree")
        return {"task": n, "branch_ref": branch,
                "base_revision": self.base,
                "expected_contract_id": record.contract_id}

    def assert_project_index_and_head_unchanged(self):
        self.assertEqual(self.base,
                         fixtures.git("rev-parse", "HEAD", cwd=self.project).decode().strip())
        self.assertEqual(self.index, (self.project / ".git/index").read_bytes())

    def test_missing_ignore_requires_attach_then_two_issues_are_eligible(self):
        left = self.registered(286)
        right = self.registered(287)
        untouched = self.project / "unrelated.txt"
        untouched.write_bytes(b"human untracked notes\n")
        with self.assertRaises(IssueWorktreeError) as caught:
            self.allocator.ensure(**left)
        self.assertEqual("MANAGED_WORKTREE_NOT_IGNORED", caught.exception.code)
        self.assertFalse((self.project / ".worktrees").exists())
        self.assertFalse((self.project / ".gitignore").exists())
        self.assert_project_index_and_head_unchanged()

        attached = self.attach("install")
        self.assertEqual(attached["status"], "ATTACHED")
        self.assertTrue(attached["host_attached"])
        self.assertFalse(attached["runtime_installed"])
        self.assertFalse(attached["task_execution_authorized"])
        self.assertEqual((self.project / ".gitignore").read_bytes(),
                         b"/.worktrees/\n")
        self.assertEqual(attached["preflight"]["ignore"]["changed_paths"],
                         [".gitignore"])
        first = self.allocator.ensure(**left)
        second = self.allocator.ensure(**right)
        self.assertEqual((first.status, second.status), ("CREATED", "CREATED"))
        self.assertNotEqual(first.worktree, second.worktree)
        self.assertEqual(self.allocator.ensure(**left).status, "RESUMED")
        self.assertEqual(self.allocator.ensure(**right).status, "RESUMED")
        self.assert_project_index_and_head_unchanged()
        self.assertEqual(untouched.read_bytes(), b"human untracked notes\n")

        repair = self.attach("repair")
        self.assertEqual(repair["status"], "READY")
        self.assertEqual(self.attach("doctor")["status"], "READY")
        self.assertEqual(self.attach("uninstall")["status"], "DETACHED")
        self.assertEqual(self.attach("uninstall")["status"], "ABSENT")
        self.assertEqual((self.project / ".gitignore").read_bytes(),
                         b"/.worktrees/\n")
        for location in (first.worktree, second.worktree):
            self.assertTrue(Path(location).is_dir())
        self.assert_project_index_and_head_unchanged()

    def test_negated_ignore_refuses_install_and_never_allocates(self):
        context = self.registered(288)
        original = b"/.worktrees/\n!/.worktrees/\n"
        (self.project / ".gitignore").write_bytes(original)
        with self.assertRaises(attach.host.ignore.IgnoreBlocked):
            self.attach("install")
        with self.assertRaises(IssueWorktreeError) as caught:
            self.allocator.ensure(**context)
        self.assertEqual(caught.exception.code, "MANAGED_WORKTREE_NOT_IGNORED")
        self.assertEqual((self.project / ".gitignore").read_bytes(), original)
        self.assertFalse((self.home / attach.STATE_DIR).exists()
                         and list((self.home / attach.STATE_DIR).glob("attach-*.json")))
        self.assertFalse((self.project / ".worktrees").exists())
        self.assert_project_index_and_head_unchanged()

    def test_dirty_staged_project_and_broken_configs_are_preserved(self):
        work = self.registered(289)
        content = {"flake.nix": b"{ invalid nix",
                   "Justfile": b"!not a justfile\n",
                   "opencode.json": b"{not-json",
                   "AGENTS.md": b"Do not edit owner rules\n",
                   "notes/untracked.txt": b"not part of task\n"}
        (self.project / "notes").mkdir()
        for name, data in content.items():
            (self.project / name).write_bytes(data)
        (self.project / "tracked.txt").write_bytes(b"human staged file\n")
        fixtures.git("add", "tracked.txt", cwd=self.project)
        self.index = (self.project / ".git/index").read_bytes()
        first = self.attach("install")
        self.assertEqual(first["status"], "ATTACHED")
        self.assertEqual(self.allocator.ensure(**work).status, "CREATED")
        self.assertEqual(self.attach("repair")["status"], "READY")
        self.assertEqual(self.attach("uninstall")["status"], "DETACHED")
        for name, data in content.items():
            self.assertEqual((self.project / name).read_bytes(), data)
        self.assert_project_index_and_head_unchanged()
        self.assertFalse(list(self.project.glob("*.lock")))


if __name__ == "__main__":
    unittest.main()
