"""Self-contained #256 attachment preview tests; disposable Git and fake payload.

The payload's executables deliberately fail if invoked. No Nix, network, real
OpenCode, Project flake evaluation, or pre-existing user repository is needed.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
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
import kagari_vnext_attach as attach


class AttachTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="kagari256-attach-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.project = self.base / "project"
        self.project.mkdir()
        self.home = self.base / "host-state"
        self.home.mkdir(mode=0o700)
        self.home.chmod(0o700)
        self.package = self.base / "immutable-package"
        self.package.mkdir()
        (self.package / "bin").mkdir()
        for name in attach.REQUIRED_BINARIES:
            binary = self.package / "bin" / name
            binary.write_text("#!/bin/sh\nexit 97 # must never execute payload\n")
            binary.chmod(0o555)
        (self.package / "bin").chmod(0o555)
        self.package.chmod(0o555)
        # Restore directory write permission solely for disposable test cleanup.
        self.addCleanup(self.writable_package)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.project / "README.md").write_bytes(b"baseline\n")
        self.git("add", "README.md")
        self.git("commit", "-qm", "baseline")
        self.head = self.git("rev-parse", "HEAD")
        self.index = (self.project / ".git/index").read_bytes()

    def writable_package(self):
        self.package.chmod(0o755)
        (self.package / "bin").chmod(0o755)

    def git(self, *args):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT="0")
        proc = subprocess.run(
            ["git", "-C", str(self.project), "-c", "core.hooksPath=/dev/null", *args],
            capture_output=True, check=True, timeout=20, env=env)
        return proc.stdout.decode().strip()

    def operate(self, operation="install", **kwargs):
        return attach.operate(kwargs.get("project", self.project),
                              kwargs.get("state_home", self.home),
                              kwargs.get("package_root", self.package), operation,
                              kwargs.get("profile", attach.PROFILE))

    def receipt(self):
        return self.home / attach.STATE_DIR / attach.state_name(
            {"project_root": str(self.project)})

    def blocked(self, code=None, operation="install", **kwargs):
        with self.assertRaises((attach.Blocked, attach.host.ignore.IgnoreBlocked,
                                OSError)) as caught:
            self.operate(operation, **kwargs)
        if code:
            self.assertEqual(getattr(caught.exception, "code", None), code)
        return caught.exception

    def assert_git_preserved(self):
        self.assertEqual(self.git("rev-parse", "HEAD"), self.head)
        self.assertEqual((self.project / ".git/index").read_bytes(), self.index)

    def test_absent_plan_and_doctor_do_not_attach_or_append(self):
        for op in ("plan", "doctor"):
            report = self.operate(op)
            self.assertEqual(report["status"], "ABSENT")
            self.assertFalse(report["host_attached"])
            self.assertFalse(report["runtime_installed"])
        self.assertFalse((self.home / attach.STATE_DIR).exists())
        self.assertFalse((self.project / ".gitignore").exists())
        self.assert_git_preserved()

    def test_install_pins_exact_package_with_no_runtime_authority(self):
        report = self.operate()
        self.assertEqual(report["status"], "ATTACHED")
        self.assertTrue(report["host_attached"])
        for key in ("runtime_installed", "task_execution_authorized", "main_started",
                    "model_or_session_started", "git_stage_or_commit_performed"):
            self.assertIs(report[key], False)
        document = json.loads(self.receipt().read_bytes())
        self.assertEqual(document["package"], attach.package_identity(self.package))
        self.assertEqual(document["authority"], "HOST_ATTACHMENT_ONLY")
        self.assertEqual(self.receipt().read_bytes(), attach.host.canonical(document))
        self.assertEqual(stat.S_IMODE(self.receipt().stat().st_mode), 0o600)
        self.assertEqual((self.project / ".gitignore").read_bytes(), b"/.worktrees/\n")
        self.assertFalse((self.project / ".worktrees").exists())
        self.assert_git_preserved()

    def test_repeat_install_repair_doctor_plan_preserve_receipt_inode(self):
        self.operate()
        before = self.receipt().read_bytes()
        inode = self.receipt().stat().st_ino
        for op in ("install", "repair", "doctor", "plan", "install"):
            self.assertEqual(self.operate(op)["status"], "READY")
            self.assertEqual(self.receipt().read_bytes(), before)
            self.assertEqual(self.receipt().stat().st_ino, inode)
        self.assertEqual((self.project / ".gitignore").read_bytes(), b"/.worktrees/\n")
        self.assert_git_preserved()

    def test_invalid_flake_just_and_dirty_staged_owner_files_preserved(self):
        originals = {"flake.nix": b"not valid Nix!\n", "flake.lock": b"{broken",
                     "Justfile": b"invalid: : :\n", "AGENTS.md": b"owner rules\n",
                     "opencode.json": b"not JSON", "untracked": b"precious\n"}
        for name, data in originals.items():
            (self.project / name).write_bytes(data)
        (self.project / "README.md").write_bytes(b"staged\n")
        self.git("add", "README.md")
        self.index = (self.project / ".git/index").read_bytes()
        originals["README.md"] = b"unstaged after staging\n"
        (self.project / "README.md").write_bytes(originals["README.md"])
        for op in ("install", "repair", "uninstall"):
            self.operate(op)
            for name, data in originals.items():
                self.assertEqual((self.project / name).read_bytes(), data)
            self.assert_git_preserved()

    def test_staged_and_unstaged_ignore_bytes_remain_owner_owned(self):
        path = self.project / ".gitignore"
        staged = b"# staged owner rules\ncache/\n"
        path.write_bytes(staged)
        self.git("add", ".gitignore")
        self.index = (self.project / ".git/index").read_bytes()
        working = staged + b"# additional unstaged owner rule\nlocal/\n"
        path.write_bytes(working)
        self.operate()
        expected = working + b"/.worktrees/\n"
        self.assertEqual(path.read_bytes(), expected)
        inode = path.stat().st_ino
        self.assert_git_preserved()
        self.operate("uninstall")
        self.assertEqual(path.read_bytes(), expected)
        self.assertEqual(path.stat().st_ino, inode)
        self.assert_git_preserved()

    def test_uninstall_idempotent_keeps_owner_ignore_task_and_evidence(self):
        self.operate()
        # These are not artifacts owned by the attachment implementation.
        task = self.home / "Task"
        task.mkdir()
        (task / "record.json").write_bytes(b"task contract\n")
        evidence = self.home / "Evidence"
        evidence.mkdir()
        (evidence / "proof").write_bytes(b"evidence bytes\n")
        prep = self.home / attach.host.STATE_DIR
        prep_before = {p.name: p.read_bytes() for p in prep.iterdir()}
        owner = (self.project / ".gitignore").read_bytes()
        inode = (self.project / ".gitignore").stat().st_ino
        self.assertEqual(self.operate("uninstall")["status"], "DETACHED")
        self.assertFalse(self.receipt().exists())
        self.assertEqual(self.operate("uninstall")["status"], "ABSENT")
        self.assertEqual((self.project / ".gitignore").read_bytes(), owner)
        self.assertEqual((self.project / ".gitignore").stat().st_ino, inode)
        self.assertEqual((task / "record.json").read_bytes(), b"task contract\n")
        self.assertEqual((evidence / "proof").read_bytes(), b"evidence bytes\n")
        self.assertEqual({p.name: p.read_bytes() for p in prep.iterdir()}, prep_before)
        self.assert_git_preserved()

    def test_uninstall_absent_never_calls_preflight(self):
        with patch.object(attach, "prepare", side_effect=AssertionError("preflight")):
            self.assertEqual(self.operate("uninstall")["status"], "ABSENT")
        self.assertEqual(list(self.home.iterdir()), [])

    def test_uninstall_does_not_repair_changed_owner_ignore(self):
        self.operate()
        changed = b"owner changed this\n!/.worktrees/\n"
        (self.project / ".gitignore").write_bytes(changed)
        with patch.object(attach, "prepare", side_effect=AssertionError("preflight")):
            self.assertEqual(self.operate("uninstall")["status"], "DETACHED")
        self.assertEqual((self.project / ".gitignore").read_bytes(), changed)

    def test_reinstall_after_uninstall_keeps_ignore_inode(self):
        self.operate()
        inode = (self.project / ".gitignore").stat().st_ino
        self.operate("uninstall")
        self.assertEqual(self.operate()["status"], "ATTACHED")
        self.assertEqual((self.project / ".gitignore").stat().st_ino, inode)

    def test_repair_missing_attachment_receipt_is_explicit_and_idempotent(self):
        self.operate()
        self.receipt().unlink()
        self.assertEqual(self.operate("doctor")["status"], "ABSENT")
        self.assertEqual(self.operate("repair")["status"], "ATTACHED")
        self.assertEqual(self.operate("repair")["status"], "READY")

    def test_repair_missing_preparation_receipt_and_ignore(self):
        self.operate()
        prep = self.home / attach.host.STATE_DIR
        for path in prep.glob("attach-*.json"):
            path.unlink()
        (self.project / ".gitignore").unlink()
        self.assertEqual(self.operate("doctor")["status"], "NEEDS_REPAIR")
        self.assertEqual(self.operate("repair")["status"], "READY")
        self.assertEqual((self.project / ".gitignore").read_bytes(), b"/.worktrees/\n")

    def test_corrupt_attachment_blocks_all_operations_without_overwrite(self):
        self.operate()
        self.receipt().write_bytes(b"{not JSON: valuable bytes}")
        for op in ("install", "repair", "doctor", "plan", "uninstall"):
            self.blocked("ATTACH_RECEIPT_IDENTITY_CHANGED", operation=op)
            self.assertEqual(self.receipt().read_bytes(), b"{not JSON: valuable bytes}")

    def test_corrupt_preparation_blocks_before_attachment(self):
        self.operate()
        self.receipt().unlink()
        prep = next((self.home / attach.host.STATE_DIR).glob("attach-*.json"))
        prep.write_bytes(b"not-json\n")
        self.blocked("HOST_STATE_INVALID", operation="repair")
        self.assertFalse(self.receipt().exists())
        self.assertEqual(prep.read_bytes(), b"not-json\n")

    def test_negation_conflict_no_attachment_no_owner_change(self):
        raw = b"/.worktrees/\n!/.worktrees/\n"
        (self.project / ".gitignore").write_bytes(raw)
        self.blocked("GITIGNORE_NEGATION_CONFLICT")
        self.assertFalse(self.receipt().exists())
        self.assertEqual((self.project / ".gitignore").read_bytes(), raw)
        self.assert_git_preserved()

    def test_symlink_ignore_refused_without_following(self):
        outside = self.base / "owner-file"
        outside.write_bytes(b"do not touch\n")
        (self.project / ".gitignore").symlink_to(outside)
        self.blocked("GITIGNORE_NOT_REGULAR")
        self.assertFalse(self.receipt().exists())
        self.assertEqual(outside.read_bytes(), b"do not touch\n")

    def test_state_home_missing_public_symlink_and_nested_refused(self):
        self.blocked(state_home=self.base / "absent")
        self.home.chmod(0o755)
        self.blocked("STATE_HOME_NOT_PRIVATE")
        self.home.chmod(0o700)
        alias = self.base / "state-alias"
        alias.symlink_to(self.home, target_is_directory=True)
        self.blocked("STATE_HOME_INVALID", state_home=alias)
        nested = self.project / "private"
        nested.mkdir(mode=0o700)
        self.blocked("HOST_STATE_MUST_BE_OUTSIDE_PROJECT", state_home=nested)
        self.assertFalse((self.project / ".gitignore").exists())

    def test_project_alias_subdirectory_and_non_git_root_refused(self):
        alias = self.base / "project-alias"
        alias.symlink_to(self.project, target_is_directory=True)
        self.blocked("PROJECT_ROOT_NOT_CANONICAL", project=alias)
        sub = self.project / "subdirectory"
        sub.mkdir()
        self.blocked(project=sub)
        plain = self.base / "plain"
        plain.mkdir()
        self.blocked(project=plain)
        self.assertFalse(self.receipt().exists())

    def test_explicit_profile_and_package_are_required_by_cli(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                attach.main(["install", "--project-root", str(self.project),
                             "--state-home", str(self.home)])
        self.assertEqual(caught.exception.code, 2)
        self.blocked("EXPLICIT_LINKED_MVI_PROFILE_REQUIRED", profile="v0.1.0")
        self.assertFalse((self.project / ".gitignore").exists())

    def test_package_root_must_be_canonical_read_only_and_complete(self):
        alias = self.base / "package-alias"
        alias.symlink_to(self.package, target_is_directory=True)
        self.blocked("PACKAGE_ROOT_NOT_CANONICAL", package_root=alias)
        self.package.chmod(0o755)
        self.blocked("PACKAGE_DIRECTORY_NOT_IMMUTABLE")
        self.package.chmod(0o555)
        (self.package / "bin").chmod(0o755)
        (self.package / "bin" / attach.REQUIRED_BINARIES[0]).unlink()
        (self.package / "bin").chmod(0o555)
        self.blocked()
        self.assertFalse((self.project / ".gitignore").exists())

    def test_writable_or_nonexecutable_package_binary_refused(self):
        binary = self.package / "bin" / attach.REQUIRED_BINARIES[0]
        for mode in (0o755, 0o444):
            binary.chmod(mode)
            self.blocked("PACKAGE_BINARY_UNSAFE")
        self.assertFalse(self.receipt().exists())

    def test_package_must_be_external_to_project_and_state_home(self):
        for parent in (self.project, self.home):
            moved = parent / "payload"
            # This filesystem refuses renaming a non-writable directory.
            # Restore read-only mode before testing package admission.
            self.package.chmod(0o755)
            self.package.rename(moved)
            self.package = moved
            self.package.chmod(0o555)
            self.blocked("PACKAGE_MUST_BE_EXTERNAL")
            self.assertFalse(self.receipt().exists())
            self.assertFalse((self.project / ".gitignore").exists())

    def test_package_binary_external_symlink_refused(self):
        outside = self.base / "external-cli"
        outside.write_bytes(b"#!/bin/sh\nexit 1\n")
        outside.chmod(0o555)
        (self.package / "bin").chmod(0o755)
        binary = self.package / "bin" / attach.REQUIRED_BINARIES[0]
        binary.unlink()
        binary.symlink_to(outside)
        (self.package / "bin").chmod(0o555)
        self.blocked("PACKAGE_BINARY_ESCAPES_PACKAGE")
        self.assertFalse(self.receipt().exists())

    def test_package_bin_directory_symlink_refused(self):
        self.package.chmod(0o755)
        (self.package / "bin").rename(self.package / "actual-bin")
        (self.package / "bin").symlink_to(self.package / "actual-bin")
        self.package.chmod(0o555)
        self.blocked("PACKAGE_DIRECTORY_NOT_IMMUTABLE")

    def test_changed_pinned_package_blocks_repair_and_uninstall(self):
        self.operate()
        original = self.receipt().read_bytes()
        binary = self.package / "bin" / attach.REQUIRED_BINARIES[0]
        binary.chmod(0o755)
        binary.write_bytes(b"#!/bin/sh\nexit 99 # changed\n")
        binary.chmod(0o555)
        for op in ("repair", "uninstall"):
            self.blocked("ATTACH_RECEIPT_IDENTITY_CHANGED", operation=op)
        self.assertEqual(self.receipt().read_bytes(), original)

    def test_symlink_and_hardlink_receipt_refused_never_deleted(self):
        self.operate()
        saved = self.base / "saved-receipt"
        self.receipt().rename(saved)
        self.receipt().symlink_to(saved)
        self.blocked(operation="uninstall")
        self.assertTrue(self.receipt().is_symlink())
        self.receipt().unlink()
        os.link(saved, self.receipt())
        self.blocked("ATTACH_RECEIPT_UNSAFE", operation="uninstall")
        self.assertTrue(saved.exists())
        self.assertTrue(self.receipt().exists())

    def test_public_receipt_and_namespace_refused(self):
        self.operate()
        self.receipt().chmod(0o644)
        self.blocked("ATTACH_RECEIPT_UNSAFE", operation="uninstall")
        self.receipt().chmod(0o600)
        self.receipt().parent.chmod(0o755)
        self.blocked("ATTACH_NAMESPACE_UNSAFE", operation="repair")

    def test_namespace_symlink_refused_no_external_deletion(self):
        outside = self.base / "outside-state"
        outside.mkdir(mode=0o700)
        valuable = outside / "important"
        valuable.write_bytes(b"preserve me")
        (self.home / attach.STATE_DIR).symlink_to(outside, target_is_directory=True)
        self.blocked(operation="uninstall")
        self.assertEqual(valuable.read_bytes(), b"preserve me")

    def test_interrupted_stage_and_unknown_data_preserved(self):
        self.operate()
        for name in (".stage-interrupted", "foreign-data"):
            path = self.receipt().parent / name
            path.write_bytes(b"inspect manually\n")
            self.blocked("ATTACH_UNKNOWN_OR_INTERRUPTED_STATE", operation="uninstall")
            self.assertEqual(path.read_bytes(), b"inspect manually\n")
            self.assertTrue(self.receipt().exists())
            path.unlink()

    def test_foreign_project_identity_in_receipt_refused(self):
        self.operate()
        document = json.loads(self.receipt().read_bytes())
        document["project"]["project_inode"] += 1
        data = attach.host.canonical(document)
        self.receipt().write_bytes(data)
        self.blocked("ATTACH_RECEIPT_IDENTITY_CHANGED", operation="repair")
        self.assertEqual(self.receipt().read_bytes(), data)

    def test_replacement_checkout_at_same_path_conflicts_with_receipt(self):
        self.operate()
        receipt = self.receipt().read_bytes()
        self.project.rename(self.base / "original-project")
        self.project.mkdir()
        self.git("init", "-q", "-b", "main")
        self.blocked("ATTACH_RECEIPT_IDENTITY_CHANGED", operation="repair")
        self.assertEqual(self.receipt().read_bytes(), receipt)
        self.assertFalse((self.project / ".gitignore").exists())
        self.assertEqual((self.base / "original-project/.gitignore").read_bytes(),
                         b"/.worktrees/\n")

    def test_swap_before_ignore_helper_cannot_authorize_replacement(self):
        original = attach.host.ignore.operate

        def substitute(project, action, **kwargs):
            project.rename(self.base / "selected-original")
            project.mkdir()
            self.git("init", "-q", "-b", "main")
            return original(project, action, **kwargs)

        with patch.object(attach.host.ignore, "operate", side_effect=substitute):
            self.blocked("PROJECT_IDENTITY_CHANGED_BEFORE_PREFLIGHT")
        self.assertFalse(self.receipt().exists())
        self.assertFalse((self.project / ".gitignore").exists())
        selected = self.base / "selected-original"
        self.assertFalse((selected / ".gitignore").exists())
        self.assertEqual((selected / ".git/index").read_bytes(), self.index)

    def test_nonregular_receipts_fail_without_waiting_on_fifo(self):
        self.operate()
        self.receipt().unlink()
        os.mkfifo(self.receipt(), mode=0o600)
        self.blocked("ATTACH_RECEIPT_UNSAFE", operation="uninstall")
        self.assertTrue(stat.S_ISFIFO(self.receipt().lstat().st_mode))
        self.receipt().unlink()
        prep = next((self.home / attach.host.STATE_DIR).glob("attach-*.json"))
        prep.unlink()
        os.mkfifo(prep, mode=0o600)
        self.blocked("HOST_STATE_FILE_UNSAFE", operation="repair")
        self.assertTrue(stat.S_ISFIFO(prep.lstat().st_mode))
        self.assertFalse(self.receipt().exists())

    def test_modified_receipt_blocks_before_owner_ignore_repair(self):
        self.operate()
        self.receipt().write_bytes(b"changed receipt")
        (self.project / ".gitignore").unlink()
        self.blocked("ATTACH_RECEIPT_IDENTITY_CHANGED", operation="repair")
        self.assertFalse((self.project / ".gitignore").exists())

    def test_attachment_receipt_duplicate_json_keys_refused(self):
        self.operate()
        canonical = self.receipt().read_bytes()
        modified = canonical.replace(b'"runtime_installed":false',
                                     b'"runtime_installed":true,"runtime_installed":false')
        self.assertNotEqual(modified, canonical)
        self.receipt().write_bytes(modified)
        self.blocked("ATTACH_RECEIPT_IDENTITY_CHANGED", operation="uninstall")
        self.assertEqual(self.receipt().read_bytes(), modified)

    def test_foreign_owner_receipt_refused(self):
        self.operate()
        actual = os.geteuid()
        # No privileged chown: namespace validation is done at the real uid,
        # then model a foreign owner at the receipt validation boundary only.
        with patch.object(attach.os, "geteuid", return_value=actual + 1):
            fd = os.open(self.receipt().parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(attach.Blocked) as caught:
                    attach.stored(fd, self.receipt().name, {})
                self.assertEqual(caught.exception.code, "ATTACH_RECEIPT_UNSAFE")
            finally:
                os.close(fd)
        self.assertTrue(self.receipt().exists())

    def test_no_clobber_publication_does_not_replace_existing_file(self):
        self.operate()
        before = self.receipt().read_bytes()
        fd = os.open(self.receipt().parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaises(attach.Blocked) as caught:
                attach.publish(fd, self.receipt().name, {"different": True})
            self.assertEqual(caught.exception.code, "ATTACH_RECEIPT_APPEARED")
        finally:
            os.close(fd)
        self.assertEqual(self.receipt().read_bytes(), before)
        self.assertFalse(list(self.receipt().parent.glob(".stage-*")))

    def test_helper_once_per_install_repair_and_never_for_detach(self):
        original = attach.host.ignore.operate
        calls = []

        def counted(project, action, **kwargs):
            calls.append(action)
            self.assertEqual(kwargs["expected_project_identity"],
                             (self.project.stat().st_dev, self.project.stat().st_ino))
            return original(project, action, **kwargs)

        with patch.object(attach.host.ignore, "operate", side_effect=counted):
            self.operate("install")
            self.operate("repair")
            self.operate("uninstall")
        self.assertEqual(calls, ["install", "repair"])

    def test_cli_json_success_and_blocked_without_executing_payload(self):
        suffix = ["--profile", attach.PROFILE, "--project-root", str(self.project),
                  "--state-home", str(self.home), "--package-root", str(self.package)]
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(attach.main(["install", *suffix]), 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "ATTACHED")
        self.receipt().write_bytes(b"corrupt")
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(attach.main(["uninstall", *suffix]), 2)
        report = json.loads(err.getvalue())
        self.assertEqual(report["status"], "BLOCKED")
        self.assertFalse(report["runtime_installed"])
        self.assertFalse(report["task_execution_authorized"])


if __name__ == "__main__":
    unittest.main()
