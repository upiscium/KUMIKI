"""N1: detached native v4 module bundle, strict byte-level development tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import kagari_v4_bundle as bundle


class V4BundleTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="kagari269-bundle-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.code = self.repo / bundle.SOURCE
        self.code.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("remote", "add", "origin", "https://github.com/upiscium/Templates.git")
        for name in bundle.MODULES:
            (self.code / name).write_text(f"# v4 fixture: {name}\n", encoding="utf-8")
        (self.repo / "Justfile").write_text("user owned data\n", encoding="utf-8")
        (self.repo / "opencode.json").write_text('{"default_agent":"plan"}\n')
        self.git("add", "--", bundle.SOURCE, "Justfile", "opencode.json")
        self.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "fixture")
        self.revision = self.git("rev-parse", "HEAD").stdout.strip()
        self.output = self.root / "module-output"

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    def built(self) -> dict:
        return bundle.build(self.repo, self.revision, self.output)

    def checked(
        self,
        digest: str | None = None,
        revision: str | None = None,
        *,
        repo: Path | None = None,
    ) -> dict:
        return bundle.inspect(
            self.output,
            digest or self.hash_manifest(),
            revision or self.revision,
            repo=repo,
        )

    def hash_manifest(self) -> str:
        return hashlib.sha256((self.output / "manifest.json").read_bytes()).hexdigest()

    def test_bundle_is_external_and_source_bound(self) -> None:
        source_before = (self.repo / "Justfile").read_bytes()
        index_before = (self.repo / ".git/index").read_bytes()
        result = self.built()
        checked = self.checked(repo=self.repo)
        self.assertEqual(checked["module_count"], 12)
        self.assertTrue(checked["git_source_matched"])
        self.assertFalse(checked["approved"])
        self.assertFalse(checked["can_execute"])
        self.assertFalse(checked["can_install"])
        self.assertEqual(result["manifest_sha256"], self.hash_manifest())
        self.assertEqual(source_before, (self.repo / "Justfile").read_bytes())
        self.assertEqual(index_before, (self.repo / ".git/index").read_bytes())
        self.assertFalse((self.output / "Justfile").exists())
        self.assertFalse((self.output / "opencode.json").exists())

    def test_canonical_manifest_has_shared_nix_file_modes(self) -> None:
        self.built()
        content = (self.output / "manifest.json").read_bytes()
        value = json.loads(content)
        self.assertEqual(content, bundle.serialize(value))
        self.assertEqual(
            set(value),
            {
                "schema_version",
                "kind",
                "qualification",
                "target_version",
                "source_revision",
                "source_repository_hint",
                "files",
            },
        )
        self.assertEqual(value["kind"], "kagari-v4-native-module-bundle")
        self.assertEqual(value["qualification"], "SOURCE_ONLY_UNQUALIFIED")
        self.assertEqual(value["source_repository_hint"], "upiscium/Templates")
        self.assertEqual(os.stat(self.output / "manifest.json").st_mode & 0o777, 0o444)
        for entry in value["files"]:
            self.assertEqual(entry["mode"], 0o444)
            self.assertEqual(
                os.stat(self.output / entry["path"]).st_mode & 0o777, 0o444
            )

    def test_legacy_writable_mode_in_manifest_is_rejected(self) -> None:
        self.built()
        path = self.output / "manifest.json"
        value = json.loads(path.read_bytes())
        value["files"][0]["mode"] = 0o644
        path.chmod(0o644)
        path.write_bytes(bundle.serialize(value))
        path.chmod(0o444)
        with self.assertRaisesRegex(bundle.BundleError, "invalid module metadata"):
            self.checked(digest=self.hash_manifest())

    def test_deterministic_manifest_from_exact_commit(self) -> None:
        first = self.built()
        new_target = self.root / "second"
        second = bundle.build(self.repo, self.revision, new_target)
        self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])
        self.assertEqual(
            (self.output / "manifest.json").read_bytes(),
            (new_target / "manifest.json").read_bytes(),
        )

    def test_mutable_checkout_does_not_change_pinned_bundle(self) -> None:
        original = self.built()
        (self.code / "evidence.py").write_text("modified working tree\n")
        (self.code / "untracked.py").write_text("untracked\n")
        other = bundle.build(self.repo, self.revision, self.root / "other")
        self.assertEqual(original["manifest_sha256"], other["manifest_sha256"])
        self.assertEqual(
            self.checked(repo=self.repo)["status"],
            "BYTE_VALIDATED_SOURCE_ONLY_UNQUALIFIED",
        )

    def test_refuses_another_git_project_output(self) -> None:
        other = self.root / "other-project"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        with self.assertRaisesRegex(bundle.BundleError, "another Git Project"):
            bundle.build(self.repo, self.revision, other / "bundle")
        self.assertFalse((other / "bundle").exists())

    def test_refuses_ambiguous_gitfile_parent(self) -> None:
        other = self.root / "other-claimed"
        other.mkdir()
        (other / ".git").write_text("untrusted Gitfile\\n")
        with self.assertRaisesRegex(bundle.BundleError, "another Git Project"):
            bundle.build(self.repo, self.revision, other / "bundle")
        self.assertFalse((other / "bundle").exists())

    def test_refuses_existing_destination_without_changes(self) -> None:
        self.output.mkdir()
        sentinel = self.output / "precious.txt"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(bundle.BundleError, "already exists"):
            self.built()
        self.assertEqual(sentinel.read_text(), "keep")

    def test_refuses_destination_inside_git_project(self) -> None:
        with self.assertRaisesRegex(bundle.BundleError, "overlaps|Git Project"):
            bundle.build(self.repo, self.revision, self.repo / "output")

    def test_git_replace_objects_cannot_spoof_pinned_revision(self) -> None:
        original = (self.code / "evidence.py").read_bytes()
        (self.code / "evidence.py").write_text("# replacement blob\\n")
        self.git("add", "--", f"{bundle.SOURCE}/evidence.py")
        self.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "replacement")
        substituted = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("replace", self.revision, substituted)
        self.built()
        self.assertEqual(
            (self.output / bundle.PREFIX / "evidence.py").read_bytes(), original
        )
        self.assertEqual(self.checked(repo=self.repo)["module_count"], 12)

    def test_oversized_native_blob_rejected_before_materialization(self) -> None:
        (self.code / "evidence.py").write_bytes(b"x" * (bundle.MAX_FILE + 1))
        self.git("add", "--", f"{bundle.SOURCE}/evidence.py")
        self.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "oversize")
        changed_revision = self.git("rev-parse", "HEAD").stdout.strip()
        with self.assertRaisesRegex(bundle.BundleError, "before materialization"):
            bundle.build(self.repo, changed_revision, self.output)
        self.assertFalse(self.output.exists())

    def test_oversized_git_tree_listing_is_bounded(self) -> None:
        for i in range(180):
            (self.code / f"surplus-{i:03d}.py").write_text("# extra\\n")
        self.git("add", "--", bundle.SOURCE)
        self.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "many")
        revision = self.git("rev-parse", "HEAD").stdout.strip()
        with self.assertRaisesRegex(
            bundle.BundleError, "Git output exceeds byte budget"
        ):
            bundle.build(self.repo, revision, self.output)
        self.assertFalse(self.output.exists())

    def test_local_git_origin_is_explicitly_unverified_hint(self) -> None:
        # A local checkout can set any Git remote. This must never confer
        # upstream provenance or authority on this source-only artifact.
        self.git(
            "remote", "set-url", "origin", "https://github.com/upiscium/Templates.git"
        )
        result = self.built()
        value = json.loads((self.output / "manifest.json").read_bytes())
        self.assertEqual(value["source_repository_hint"], "upiscium/Templates")
        self.assertEqual(value["qualification"], "SOURCE_ONLY_UNQUALIFIED")
        self.assertFalse(result["approved"])
        self.assertFalse(result["can_install"])
        self.assertFalse(result["can_execute"])

    def test_alternate_github_repository_casing_refused(self) -> None:
        self.git(
            "remote", "set-url", "origin", "https://github.com/UPISCIUM/templates.git"
        )
        with self.assertRaisesRegex(bundle.BundleError, "exact upiscium/Templates"):
            self.built()
        self.assertFalse(self.output.exists())

    def test_source_origin_changed_after_build_refused(self) -> None:
        self.built()
        self.git("remote", "set-url", "origin", "https://github.com/other/repo.git")
        with self.assertRaisesRegex(bundle.BundleError, "exact upiscium/Templates"):
            self.checked(repo=self.repo)

    def test_missing_canonical_github_source_origin_refused(self) -> None:
        self.git("remote", "remove", "origin")
        with self.assertRaises(bundle.BundleError):
            self.built()
        self.assertFalse(self.output.exists())

    def test_refuses_wrong_source_commit(self) -> None:
        with self.assertRaises(bundle.BundleError):
            bundle.build(self.repo, "b" * 40, self.output)

    def test_missing_tracked_module_is_rejected(self) -> None:
        (self.code / "task_view.py").unlink()
        self.git("add", "-u")
        self.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "drop module")
        new_revision = self.git("rev-parse", "HEAD").stdout.strip()
        with self.assertRaisesRegex(bundle.BundleError, "incomplete"):
            bundle.build(self.repo, new_revision, self.output)

    def test_new_tracked_module_is_rejected(self) -> None:
        (self.code / "unknown.py").write_text("unknown\n")
        self.git("add", "--", f"{bundle.SOURCE}/unknown.py")
        self.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "extra module")
        with self.assertRaisesRegex(bundle.BundleError, "unexpected"):
            bundle.build(
                self.repo, self.git("rev-parse", "HEAD").stdout.strip(), self.output
            )

    def test_manifest_hash_mismatch(self) -> None:
        self.built()
        with self.assertRaisesRegex(bundle.BundleError, "manifest digest"):
            self.checked(digest="a" * 64)

    def test_module_tamper_is_rejected(self) -> None:
        self.built()
        path = self.output / bundle.PREFIX / "evidence.py"
        path.chmod(0o644)
        path.write_text("tampered\n")
        path.chmod(0o444)
        with self.assertRaises(bundle.BundleError):
            self.checked()

    def test_module_symlink_refused(self) -> None:
        self.built()
        path = self.output / bundle.PREFIX / "evidence.py"
        path.unlink()
        path.symlink_to(self.repo / "Justfile")
        with self.assertRaises(bundle.BundleError):
            self.checked()

    def test_extra_file_refused(self) -> None:
        self.built()
        (self.output / "unknown.txt").write_text("hello\n")
        with self.assertRaisesRegex(bundle.BundleError, "undeclared"):
            self.checked()

    def test_extra_directory_refused(self) -> None:
        self.built()
        (self.output / "cache").mkdir()
        with self.assertRaisesRegex(bundle.BundleError, "undeclared"):
            self.checked()

    def test_canonical_manifest_bytes_enforced(self) -> None:
        self.built()
        path = self.output / "manifest.json"
        data = json.loads(path.read_text())
        path.chmod(0o644)
        path.write_text(json.dumps(data, indent=2) + "\n")
        path.chmod(0o444)
        with self.assertRaisesRegex(bundle.BundleError, "noncanonical"):
            self.checked(digest=self.hash_manifest())

    def test_manifest_extra_identity_field_rejected(self) -> None:
        self.built()
        path = self.output / "manifest.json"
        data = json.loads(path.read_text())
        data["approved"] = True
        path.chmod(0o644)
        path.write_bytes(bundle.serialize(data))
        path.chmod(0o444)
        with self.assertRaisesRegex(bundle.BundleError, "unknown or missing"):
            self.checked(digest=self.hash_manifest())

    def test_duplicate_json_key_fails_closed(self) -> None:
        self.built()
        path = self.output / "manifest.json"
        original = path.read_bytes()
        path.chmod(0o644)
        path.write_bytes(original.replace(b'{"files":', b'{"files":[],"files":', 1))
        path.chmod(0o444)
        with self.assertRaisesRegex(bundle.BundleError, "duplicate"):
            self.checked(digest=self.hash_manifest())

    def test_parent_directory_symlink_rejected(self) -> None:
        self.built()
        moved = self.root / "moved-lib"
        (self.output / "lib").rename(moved)
        (self.output / "lib").symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(bundle.BundleError, "unsafe native module parent"):
            self.checked()

    def test_fifo_module_fails_without_hanging(self) -> None:
        self.built()
        path = self.output / bundle.PREFIX / "evidence.py"
        path.unlink()
        os.mkfifo(path)
        with self.assertRaisesRegex(bundle.BundleError, "unsafe file type"):
            self.checked()

    def test_deeply_nested_manifest_fails_closed(self) -> None:
        self.built()
        path = self.output / "manifest.json"
        path.chmod(0o644)
        path.write_bytes((b"[" * 2000) + b"0" + (b"]" * 2000))
        path.chmod(0o444)
        with self.assertRaises(bundle.BundleError):
            self.checked(digest=self.hash_manifest())

    def test_file_mode_tamper_rejected(self) -> None:
        self.built()
        (self.output / bundle.PREFIX / "evidence.py").chmod(0o755)
        with self.assertRaisesRegex(bundle.BundleError, "unsafe"):
            self.checked()

    def test_source_commit_binding_uses_git_not_claimed_revision(self) -> None:
        self.built()
        with self.assertRaisesRegex(bundle.BundleError, "identity"):
            self.checked(revision="b" * 40)

    def test_missing_source_revision_rejected(self) -> None:
        self.built()
        with self.assertRaisesRegex(bundle.BundleError, "full commit"):
            self.checked(revision="main")

    def test_missing_bundle_file_fails_closed(self) -> None:
        self.built()
        (self.output / bundle.PREFIX / "task_record.py").unlink()
        with self.assertRaises(bundle.BundleError):
            self.checked()

    def test_cli_check_reports_unqualified_not_runtime_success(self) -> None:
        result = self.built()
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/kagari_v4_bundle.py"),
                "check",
                "--bundle",
                str(self.output),
                "--digest",
                result["manifest_sha256"],
                "--revision",
                self.revision,
                "--repo",
                str(self.repo),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        outcome = json.loads(proc.stdout)
        self.assertEqual(outcome["status"], "BYTE_VALIDATED_SOURCE_ONLY_UNQUALIFIED")
        self.assertFalse(outcome["approved"])
        self.assertFalse(outcome["can_execute"])


if __name__ == "__main__":
    unittest.main()
