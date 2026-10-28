"""Disposable real-Git acceptance for opt-in host preparation #304.

Never starts OpenCode, grants Task authority or changes an existing Project.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import kagari_vnext_host_prepare as host
import kagari_vnext_worktree_ignore as ignore


class HostPrepareTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="kagari304-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.home = self.base / "private-state"
        self.home.mkdir(mode=0o700)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.project / "README.md").write_bytes(b"baseline\n")
        self.git("add", "README.md")
        self.git("commit", "-qm", "baseline")
        self.before_head = self.git("rev-parse", "HEAD")
        self.before_index = (self.project / ".git/index").read_bytes()

    def git(self, *args, cwd=None):
        p = subprocess.run(["git", "-C", str(cwd or self.project), *args],
                           capture_output=True, check=True, timeout=15)
        return p.stdout.decode().strip()

    def operate(self, op, **kwargs):
        return host.operate(kwargs.get("project", self.project),
                            kwargs.get("state_home", self.home),
                            op, kwargs.get("profile", host.PROFILE))

    def blocked(self, code, op="install", **kwargs):
        with self.assertRaises((host.HostBlocked, ignore.IgnoreBlocked)) as caught:
            self.operate(op, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def record(self):
        return self.home / host.STATE_DIR / host.state_name(
            host.project_identity(self.project))

    def assert_project_preserved(self):
        self.assertEqual(self.git("rev-parse", "HEAD"), self.before_head)
        self.assertEqual((self.project / ".git/index").read_bytes(),
                         self.before_index)

    def test_plan_doctor_absent_do_not_create_host_state(self):
        for op in ("plan", "doctor"):
            r = self.operate(op)
            self.assertEqual(r["status"], "ABSENT")
            self.assertEqual(r["ignore"]["status"], "NEEDS_UPDATE")
            self.assertFalse(r["host_preflight_recorded"])
            self.assertFalse(r["runtime_installed"])
        self.assertFalse((self.home / host.STATE_DIR).exists())
        self.assertFalse((self.project / ".gitignore").exists())

    def test_install_and_reinstall_and_repair(self):
        r = self.operate("install")
        self.assertEqual(r["status"], "PREPARED")
        self.assertEqual(r["ignore"]["status"], "UPDATED")
        self.assertEqual(r["ignore"]["changed_paths"], [".gitignore"])
        self.assertTrue(r["ignore"]["git_ignore_effective"])
        self.assertTrue(r["ignore"]["needs_commit_for_new_clones"])
        self.assertFalse(r["runtime_installed"])
        self.assertFalse(r["task_execution_authorized"])
        self.assertFalse(r["model_or_session_started"])
        self.assertTrue(self.record().exists())
        self.assertEqual(stat.S_IMODE(self.record().stat().st_mode), 0o600)
        self.assertEqual((self.project / ".gitignore").read_bytes(), b"/.worktrees/\n")
        self.assert_project_preserved()
        again = self.operate("install")
        self.assertEqual(again["status"], "READY")
        self.assertEqual(again["ignore"]["status"], "UNCHANGED")
        repaired = self.operate("repair")
        self.assertEqual(repaired["status"], "READY")
        self.assertEqual(repaired["ignore"]["status"], "UNCHANGED")
        self.assertEqual(self.operate("doctor")["status"], "READY")
        self.assert_project_preserved()

    def test_full_project_owner_configuration_is_untouched(self):
        originals = {
            "flake.nix": b"this is intentionally not a valid Nix flake\n",
            "flake.lock": b"not valid JSON\n",
            "Justfile": b"no recipe: :\n",
            "AGENTS.md": b"user rules\n",
            "opencode.json": b"broken but owned by user\n",
            "README.md": b"original staged changes\n",
        }
        for name, data in originals.items():
            (self.project / name).write_bytes(data)
        self.git("add", "README.md")
        self.before_index = (self.project / ".git/index").read_bytes()
        (self.project / "untracked.txt").write_bytes(b"important untracked\n")
        self.assertEqual(self.operate("install")["status"], "PREPARED")
        for name, data in originals.items():
            self.assertEqual((self.project / name).read_bytes(), data)
        self.assertEqual((self.project / "untracked.txt").read_bytes(),
                         b"important untracked\n")
        self.assert_project_preserved()
        self.assertEqual(self.git("status", "--porcelain").count(".gitignore"), 1)

    def test_existing_valid_ordered_negation_no_change(self):
        before = b"!/.worktrees/\n/.worktrees/\n"
        (self.project / ".gitignore").write_bytes(before)
        r = self.operate("install")
        self.assertEqual(r["status"], "PREPARED")
        self.assertEqual(r["ignore"]["status"], "UNCHANGED")
        self.assertEqual((self.project / ".gitignore").read_bytes(), before)
        self.assert_project_preserved()

    def test_explicit_profile_refuses_ga_no_project_change(self):
        self.blocked("EXPLICIT_LINKED_MVI_PROFILE_REQUIRED",
                     profile="ga-bounded-independent-clone")
        self.assertFalse((self.project / ".gitignore").exists())
        self.assertFalse((self.home / host.STATE_DIR).exists())

    def test_conflict_blocks_host_record_and_preserves_owner_bytes(self):
        raw = b"/.worktrees/\n!/.worktrees/\n"
        (self.project / ".gitignore").write_bytes(raw)
        self.blocked("GITIGNORE_NEGATION_CONFLICT")
        self.assertEqual((self.project / ".gitignore").read_bytes(), raw)
        self.assertFalse(self.record().exists())
        self.assert_project_preserved()

    def test_symlink_ignore_refused_and_no_host_record(self):
        outside = self.base / "outside"
        outside.write_bytes(b"user data\n")
        (self.project / ".gitignore").symlink_to(outside)
        self.blocked("GITIGNORE_NOT_REGULAR")
        self.assertEqual(outside.read_bytes(), b"user data\n")
        self.assertFalse(self.record().exists())

    def test_repair_recovers_deleted_owner_rule_without_commit(self):
        self.operate("install")
        (self.project / ".gitignore").unlink()
        self.assertEqual(self.operate("doctor")["status"], "NEEDS_REPAIR")
        r = self.operate("repair")
        self.assertEqual(r["status"], "READY")
        self.assertEqual(r["ignore"]["status"], "UPDATED")
        self.assertTrue(self.record().exists())
        self.assert_project_preserved()

    def test_bad_rule_after_attach_refuses_without_reassigning_task(self):
        self.operate("install")
        (self.project / ".gitignore").write_bytes(b"/.worktrees/\n!/.worktrees/\n")
        self.blocked("GITIGNORE_NEGATION_CONFLICT", op="repair")
        self.assertTrue(self.record().exists())
        self.assertFalse((self.project / ".worktrees").exists())

    def test_existing_committed_rule_status_reports_no_commit_needed(self):
        (self.project / ".gitignore").write_bytes(b"/.worktrees/\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "ignore already")
        self.before_head = self.git("rev-parse", "HEAD")
        self.before_index = (self.project / ".git/index").read_bytes()
        r = self.operate("install")
        self.assertFalse(r["ignore"]["needs_commit_for_new_clones"])
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assert_project_preserved()

    def test_state_home_must_be_private(self):
        self.home.chmod(0o755)
        self.blocked("STATE_HOME_NOT_PRIVATE")
        self.assertFalse((self.project / ".gitignore").exists())

    def test_state_home_inside_project_is_refused(self):
        inside = self.project / "private-state"
        inside.mkdir(mode=0o700)
        self.blocked("HOST_STATE_MUST_BE_OUTSIDE_PROJECT",
                     state_home=inside)
        self.assertFalse((self.project / ".gitignore").exists())

    def test_state_home_symlink_refused(self):
        alias = self.base / "alias"
        alias.symlink_to(self.home, target_is_directory=True)
        self.blocked("STATE_HOME_INVALID", state_home=alias)

    def test_project_root_symlink_refused(self):
        alias = self.base / "aliased-project"
        alias.symlink_to(self.project, target_is_directory=True)
        self.blocked("PROJECT_ROOT_NOT_CANONICAL", project=alias)

    def test_modified_host_state_refused_without_overwrite(self):
        self.operate("install")
        state = self.record()
        expected = state.read_bytes()
        changed = expected.replace(b"PREFLIGHT_ONLY", b"ACTIVATED")
        state.write_bytes(changed)
        with self.assertRaises(host.HostBlocked):
            self.operate("repair")
        self.assertEqual(state.read_bytes(), changed)

    def test_missing_host_record_recreated_without_rewriting_ignore(self):
        self.operate("install")
        self.record().unlink()
        existing = (self.project / ".gitignore").read_bytes()
        r = self.operate("repair")
        self.assertEqual(r["status"], "PREPARED")
        self.assertEqual(r["ignore"]["status"], "UNCHANGED")
        self.assertEqual((self.project / ".gitignore").read_bytes(), existing)

    def test_interrupted_host_stage_blocks_without_deleting_contents(self):
        self.operate("install")
        stage = self.home / host.STATE_DIR / "kagari-host-prep-stage-suspect"
        stage.write_bytes(b"possible valuable receipt\n")
        self.blocked("HOST_STAGE_REQUIRES_REVIEW", "repair")
        self.assertEqual(stage.read_bytes(), b"possible valuable receipt\n")

    def test_different_host_source_requires_explicit_reconciliation(self):
        self.operate("install")
        with patch.object(host, "source_sha", return_value="f" * 64):
            self.blocked("HOST_STATE_IDENTITY_CHANGED", "doctor")
        self.assertEqual(self.operate("doctor")["status"], "READY")

    def test_helper_called_once_per_prepare_operation(self):
        original = ignore.operate
        observed = []

        def counted(project, operation, **kwargs):
            observed.append(operation)
            return original(project, operation, **kwargs)
        with patch.object(ignore, "operate", side_effect=counted):
            self.operate("install")
            self.operate("repair")
        self.assertEqual(observed, ["install", "repair"])

    def test_host_preparation_precedes_two_issue_linked_worktrees(self):
        from tests import test_metadata_ref as fixtures
        v4 = Path(__file__).resolve().parents[1] / "components/agent-core-v4"
        sys.path.insert(0, str(v4))
        from issue_worktree import IssueWorktreeAllocator
        from task_record import TaskRecords

        fixture = fixtures.MetadataRefTest("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        original_head = fixtures.git(
            "rev-parse", "HEAD", cwd=fixture.product).decode().strip()
        index = (fixture.product / ".git/index").read_bytes()

        report = host.operate(fixture.product, self.home, "install")
        self.assertEqual(report["status"], "PREPARED")
        self.assertTrue(report["host_preflight_recorded"])
        self.assertFalse(report["task_execution_authorized"])
        self.assertEqual((fixture.product / ".gitignore").read_bytes(),
                         b"/.worktrees/\n")
        self.assertEqual((fixture.product / ".git/index").read_bytes(), index)

        records = TaskRecords(fixture.store, authorize=lambda _req: True)
        allocator = IssueWorktreeAllocator(fixture.store)
        names = set()
        for issue in (304, 305):
            branch = f"refs/heads/issue-{issue}"
            record = records.create(
                task=str(issue), branch_ref=branch,
                base_revision=original_head, title=f"Issue {issue}",
                body="explicitly authorized fixture Task")
            contract = dict(task=str(issue), branch_ref=branch,
                            base_revision=original_head,
                            expected_contract_id=record.contract_id)
            created = allocator.ensure(**contract)
            self.assertEqual(created.status, "CREATED")
            self.assertEqual(allocator.ensure(**contract).status, "RESUMED")
            names.add(created.worktree)
        self.assertEqual(len(names), 2)
        self.assertEqual((fixture.product / ".git/index").read_bytes(), index)
        self.assertEqual(fixtures.git(
            "rev-parse", "HEAD", cwd=fixture.product).decode().strip(),
            original_head)

    def test_host_identity_pin_blocks_replaced_project_before_ignore_write(self):
        initial = self.project
        moved_original = self.base / "moved-original"
        original = ignore.operate

        def replace_checkout_before_helper(project, operation, **kwargs):
            initial.rename(moved_original)
            initial.mkdir()
            subprocess.run(["git", "-C", str(initial), "init", "-q",
                            "-b", "main"], check=True, capture_output=True)
            return original(project, operation, **kwargs)

        with patch.object(ignore, "operate",
                          side_effect=replace_checkout_before_helper):
            self.blocked("PROJECT_IDENTITY_CHANGED_BEFORE_PREFLIGHT")
        # The old Project and the newly created foreign Project both survive.
        self.assertEqual((moved_original / "README.md").read_bytes(),
                         b"baseline\n")
        self.assertFalse((moved_original / ".gitignore").exists())
        self.assertFalse((initial / ".gitignore").exists())
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=moved_original),
                         self.before_head)
        self.assertFalse(list((self.home / host.STATE_DIR).glob("attach-*.json")))

    def test_project_swapped_during_validation_never_pins_foreign_root(self):
        # The outer host must hold the selected inode *before* it invokes
        # the Git root validator. Switching to a second valid repo after
        # validation must never turn that repo into the approved target.
        original_validator = ignore.primary_project
        selected = self.project
        moved_original = self.base / "original-during-validation"
        before_fds = len(os.listdir("/proc/self/fd"))

        def swap_after_git_validation(project):
            result = original_validator(project)
            selected.rename(moved_original)
            selected.mkdir()
            subprocess.run(
                ["git", "-C", str(selected), "init", "-q", "-b", "main"],
                check=True, capture_output=True,
            )
            (selected / "README.md").write_bytes(b"foreign repository\n")
            return result

        with patch.object(ignore, "primary_project",
                          side_effect=swap_after_git_validation):
            self.blocked("PROJECT_IDENTITY_CHANGED_DURING_VALIDATION")
        self.assertEqual(len(os.listdir("/proc/self/fd")), before_fds)
        self.assertFalse((selected / ".gitignore").exists())
        self.assertFalse((moved_original / ".gitignore").exists())
        self.assertEqual((selected / "README.md").read_bytes(),
                         b"foreign repository\n")
        self.assertEqual((moved_original / "README.md").read_bytes(),
                         b"baseline\n")
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=moved_original),
                         self.before_head)
        state_namespace = self.home / host.STATE_DIR
        self.assertFalse(state_namespace.exists())

    def test_helper_identity_pin_stops_bad_expected_inode_before_mutation(self):
        with self.assertRaises(ignore.IgnoreBlocked) as ctx:
            ignore.operate(
                self.project, "install",
                expected_project_identity=(self.project.stat().st_dev, 0))
        self.assertEqual(ctx.exception.code,
                         "PROJECT_IDENTITY_CHANGED_BEFORE_PREFLIGHT")
        self.assertFalse((self.project / ".gitignore").exists())
        self.assert_project_preserved()

    def test_cli_json_install_and_doctor(self):
        script = Path(host.__file__)
        prefix = [sys.executable, str(script)]
        suffix = ["--profile", host.PROFILE, "--project-root", str(self.project),
                  "--state-home", str(self.home)]
        for operation, expected in (("install", "PREPARED"),
                                    ("doctor", "READY")):
            command = [*prefix, operation, *suffix]
            p = subprocess.run(command, capture_output=True, timeout=20, check=False)
            self.assertEqual(p.returncode, 0, p.stderr.decode())
            report = json.loads(p.stdout)
            self.assertEqual(report["status"], expected)
            self.assertFalse(report["runtime_installed"])
        self.assert_project_preserved()


if __name__ == "__main__":
    unittest.main()
