"""Host-managed KUMIKI source-authority locator contract tests (synthetic)."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock


MODULE = Path(__file__).resolve().parents[1] / "tools" / "source_authority_locator.py"
spec = importlib.util.spec_from_file_location("source_authority_locator", MODULE)
assert spec is not None and spec.loader is not None
locator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(locator)


class SourceAuthorityLocatorTest(unittest.TestCase):
    REVISION = "81dbe8708a05c10f8d94bd926c117afcd30696a8"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / "nix" / "store"
        self.store.mkdir(parents=True)
        self.output = self.store / ("a" * 32 + "-templates-source-collaboration")
        self.launcher = self.output / locator.LAUNCHER_RELATIVE
        self.policy = self.output / locator.POLICY_RELATIVE
        self.locator_output = self.store / ("b" * 32 + "-kumiki-source-authority-locator")
        self.locator_binary = self.locator_output / locator.LOCATOR_RELATIVE
        self.locator_binary.parent.mkdir(parents=True)
        self.locator_binary.write_bytes(b"#!/bin/sh\n# synthetic host-owned locator\n")
        self.locator_binary.chmod(0o555)
        self.launcher.parent.mkdir(parents=True)
        self.policy.parent.mkdir(parents=True)
        self.descriptor = self.root / "host" / "source-authority.json"
        self.descriptor.parent.mkdir()
        self.launcher.write_bytes(("#!/bin/sh\nexec '/nix/store/python/bin/python3' "
                                   f"-I '{self.policy}' \"$@\"\n").encode())
        self.launcher.chmod(0o555)
        self.policy.write_text(
            '#!python\nTRUSTED_SOURCE_BASE = "'
            + self.REVISION + '"\n', encoding="utf-8"
        )
        self.policy.chmod(0o555)
        self.data = {
            "schema_version": 2,
            "repository": "upiscium/Templates",
            "repository_id": 1183254623,
            "revision": self.REVISION,
            "output_path": str(self.output),
            "locator_path": str(self.locator_binary),
        }
        self.persist()

    def persist(self) -> None:
        self.data["launcher_sha256"] = hashlib.sha256(
            self.launcher.read_bytes()).hexdigest()
        self.data["policy_sha256"] = hashlib.sha256(
            self.policy.read_bytes()).hexdigest()
        self.data["locator_sha256"] = hashlib.sha256(
            self.locator_binary.read_bytes()).hexdigest()
        self.descriptor.write_text(json.dumps(self.data), encoding="utf-8")
        self.descriptor.chmod(0o600)

    def resolve(self) -> tuple[Path, str]:
        return locator.resolve_authority(
            self.descriptor, store_root=self.store,
            secure_root=self.root, owner_uid=os.getuid(),
            locator_path=self.locator_binary)

    def blocked(self, message: str) -> None:
        with self.assertRaisesRegex(locator.LocatorError, message):
            self.resolve()

    def test_valid_exact_host_selection(self) -> None:
        self.assertEqual((self.launcher, self.REVISION), self.resolve())
        self.assertTrue(stat.S_IMODE(self.launcher.stat().st_mode) & 0o111)

    def test_unapproved_task_built_locator_rejected_even_if_bytes_identical(self) -> None:
        task_output = self.store / ("c" * 32 + "-kumiki-source-authority-locator")
        task_binary = task_output / locator.LOCATOR_RELATIVE
        task_binary.parent.mkdir(parents=True)
        task_binary.write_bytes(self.locator_binary.read_bytes())
        task_binary.chmod(0o555)
        with self.assertRaisesRegex(locator.LocatorError,
                                    "running locator does not match host-approved"):
            locator.resolve_authority(
                self.descriptor, store_root=self.store,
                secure_root=self.root, owner_uid=os.getuid(),
                locator_path=task_binary,
            )
        self.assertEqual((self.launcher, self.REVISION), self.resolve())

    def test_tampered_approved_locator_digest_refused(self) -> None:
        self.locator_binary.chmod(0o644)
        self.locator_binary.write_bytes(self.locator_binary.read_bytes() + b"# drift\n")
        self.locator_binary.chmod(0o555)
        self.blocked("host-approved locator digest mismatch")

    def test_group_writable_approved_locator_refused(self) -> None:
        self.locator_binary.chmod(0o775)
        self.blocked("untrusted authority file owner")

    def test_unapproved_locator_path_outside_store_refused(self) -> None:
        self.data["locator_path"] = str(self.root / "mutable-locator")
        self.persist()
        self.blocked("host-approved locator must be an exact Nix-store")

    def test_wrong_repository_name(self) -> None:
        self.data["repository"] = "upiscium/Another"
        self.persist()
        self.blocked("repository identity")

    def test_wrong_repository_id(self) -> None:
        self.data["repository_id"] += 1
        self.persist()
        self.blocked("repository identity")

    def test_boolean_schema_is_not_integer_one(self) -> None:
        self.data["schema_version"] = True
        self.persist()
        self.blocked("schema")

    def test_descriptor_extra_field_rejected(self) -> None:
        self.data["mutable_override"] = "bad"
        self.persist()
        self.blocked("unknown or missing")

    def test_descriptor_duplicate_json_key_rejected(self) -> None:
        raw = self.descriptor.read_text()
        self.descriptor.write_text(raw[:-1] + ', "repository": "upiscium/Templates"}')
        self.blocked("duplicate descriptor")

    def test_stale_revision_rejected_even_with_matching_digests(self) -> None:
        self.data["revision"] = "a" * 40
        self.persist()
        self.blocked("revision does not match policy")

    def test_zero_revision_rejected(self) -> None:
        self.data["revision"] = "0" * 40
        self.persist()
        self.blocked("invalid approved")

    def test_digest_rejects_byte_modified_policy(self) -> None:
        self.policy.chmod(0o644)
        self.policy.write_text(self.policy.read_text() + "# tampered\n")
        self.policy.chmod(0o555)
        self.blocked("digest mismatch")

    def test_digest_rejects_byte_modified_launcher(self) -> None:
        self.launcher.chmod(0o644)
        self.launcher.write_bytes(self.launcher.read_bytes() + b"# changed\n")
        self.launcher.chmod(0o555)
        self.blocked("digest mismatch")

    def test_policy_references_another_revision(self) -> None:
        self.policy.chmod(0o644)
        self.policy.write_text('TRUSTED_SOURCE_BASE = "' + "b" * 40 + '"\n')
        self.policy.chmod(0o555)
        self.persist()
        self.blocked("revision does not match policy")

    def test_launcher_cannot_exec_different_policy(self) -> None:
        self.launcher.chmod(0o644)
        self.launcher.write_text("#!/bin/sh\necho unexpected\n")
        self.launcher.chmod(0o555)
        self.persist()
        self.blocked("does not reference verified policy")

    def test_descriptor_symlink_refused(self) -> None:
        shadow = self.root / "shadow"
        self.descriptor.rename(shadow)
        self.descriptor.symlink_to(shadow)
        self.blocked("file unavailable or symlink")

    def test_fifo_descriptor_rejected_without_waiting_for_writer(self) -> None:
        self.descriptor.unlink()
        os.mkfifo(self.descriptor)
        self.blocked("untrusted authority file owner")

    def test_installed_binary_symlink_refused(self) -> None:
        shadow = self.root / "shadow"
        self.launcher.rename(shadow)
        self.launcher.symlink_to(shadow)
        self.blocked("file unavailable or symlink")

    def test_store_subdirectory_symlink_refused(self) -> None:
        original = self.output / "actual_bin"
        self.launcher.parent.rename(original)
        self.launcher.parent.symlink_to(original, target_is_directory=True)
        self.blocked("untrusted authority directory")

    def test_group_writable_descriptor_refused(self) -> None:
        self.descriptor.chmod(0o620)
        self.blocked("untrusted authority file owner")

    def test_nested_json_is_blocked_without_traceback(self) -> None:
        self.descriptor.write_text("[" * 3800 + "0" + "]" * 3800)
        with self.assertRaisesRegex(locator.LocatorError,
                                    "invalid descriptor JSON|descriptor must be an object"):
            self.resolve()
        with mock.patch.object(locator.json, "loads", side_effect=RecursionError()):
            self.blocked("invalid descriptor JSON")

    def test_descriptor_world_writable_refused(self) -> None:
        self.descriptor.chmod(0o666)
        self.blocked("untrusted authority file owner")

    def test_host_parent_world_writable_refused(self) -> None:
        self.descriptor.parent.chmod(0o777)
        self.blocked("untrusted authority directory")

    def test_sticky_group_writable_nix_store_parent_is_supported(self) -> None:
        # Real NixOS /nix/store is often root:nixbld mode 1775.
        self.store.chmod(0o1775)
        with mock.patch.object(locator, "DEFAULT_STORE", self.store):
            self.assertEqual((self.launcher, self.REVISION), self.resolve())

    def test_group_writable_store_without_sticky_bit_is_refused(self) -> None:
        self.store.chmod(0o775)
        with mock.patch.object(locator, "DEFAULT_STORE", self.store):
            self.blocked("untrusted authority directory")

    def test_world_writable_sticky_store_is_refused(self) -> None:
        self.store.chmod(0o1777)
        with mock.patch.object(locator, "DEFAULT_STORE", self.store):
            self.blocked("untrusted authority directory")

    def test_nix_output_wrong_suffix_rejected(self) -> None:
        self.data["output_path"] = str(self.store / ("a" * 32 + "-wrong"))
        self.persist()
        self.blocked("not the pinned Nix-store package")

    def test_nix_output_outside_fixed_store_rejected(self) -> None:
        self.data["output_path"] = str(self.root / "other" /
                                      ("a" * 32 + "-templates-source-collaboration"))
        self.persist()
        self.blocked("not the pinned Nix-store package")

    def test_noncanonical_store_path_rejected(self) -> None:
        self.data["output_path"] = str(self.store) + "/junk/../" + self.output.name
        self.persist()
        self.blocked("invalid immutable output path")

    def test_wrong_source_executable_mode_rejected(self) -> None:
        self.launcher.chmod(0o444)
        self.persist()
        self.blocked("not executable")

    def test_illegal_and_relative_operation_arguments_rejected(self) -> None:
        for argv in (
            ["--worktree", "/tmp/task", "merge", "228"],
            ["--worktree", "task", "publication-check", "228"],
            ["--descriptor", "/tmp/forged", "--worktree", "/tmp/task", "push", "228"],
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(locator.LocatorError):
                    locator.validate_args(argv)

    def test_known_guarded_operation_arguments_accepted(self) -> None:
        for operation in ("publication-check", "commit", "push", "pr-create", "checkpoint"):
            locator.validate_args(["--worktree", "/repo/task", operation, "228"])

    def test_installed_selector_keeps_a_fixed_descriptor_path(self) -> None:
        expected = self.root / "host" / "source-authority.json"
        installed = self.output / "bin" / "kumiki-source"
        with mock.patch.object(locator, "HOST_DESCRIPTOR_PATH", str(expected)), \
             mock.patch.object(locator, "DEFAULT_STORE", self.store), \
             mock.patch.object(locator, "__file__", str(installed)):
            self.assertEqual(expected, locator._installed_descriptor())

    def test_valid_installed_main_reports_ready(self) -> None:
        output = io.StringIO()
        with mock.patch.object(locator, "_installed_descriptor", return_value=self.descriptor), \
             mock.patch.object(locator, "resolve_authority", return_value=(self.launcher, self.REVISION)), \
             redirect_stdout(output):
            self.assertEqual(0, locator.main(["--check"]))
        state = json.loads(output.getvalue())
        self.assertEqual("READY", state["status"])
        self.assertEqual(self.REVISION, state["revision"])
        self.assertEqual(str(self.launcher), state["launcher"])

    def test_installed_main_handoffs_exact_guarded_argv(self) -> None:
        arguments = ["--worktree", "/tmp/example-task", "publication-check", "228"]
        with mock.patch.object(locator, "_installed_descriptor", return_value=self.descriptor), \
             mock.patch.object(locator, "resolve_authority", return_value=(self.launcher, self.REVISION)), \
             mock.patch.object(locator.os, "execv", side_effect=RuntimeError("intercepted")) as call:
            with self.assertRaisesRegex(RuntimeError, "intercepted"):
                locator.main(arguments)
        call.assert_called_once_with(str(self.launcher), [str(self.launcher), *arguments])

    def test_exec_failure_returns_fail_closed_json(self) -> None:
        error = io.StringIO()
        arguments = ["--worktree", "/tmp/example-task", "publication-check", "228"]
        with mock.patch.object(locator, "_installed_descriptor", return_value=self.descriptor), \
             mock.patch.object(locator, "resolve_authority", return_value=(self.launcher, self.REVISION)), \
             mock.patch.object(locator.os, "execv", side_effect=OSError("simulated failure")), \
             redirect_stderr(error):
            self.assertEqual(2, locator.main(arguments))
        response = json.loads(error.getvalue())
        self.assertEqual("BLOCKED", response["status"])
        self.assertIn("could not start", response["reason"])

    def test_mutable_checkout_cannot_act_as_installed_locator(self) -> None:
        errors = io.StringIO()
        with redirect_stderr(errors):
            status = locator.main(["--check"])
        self.assertEqual(2, status)
        self.assertIn("not installed authority", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
