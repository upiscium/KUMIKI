"""GA candidate: persistent Git Product patch must outlive disposable Work Unit."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch as mockpatch

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "tools/kagari_v010_ga_product_archive.py"
spec = importlib.util.spec_from_file_location("ga_product_archive", source)
ga = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ga)


class ProductArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="kagari-ga-artifact-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "private-workunit"
        self.repo.mkdir(mode=0o700)
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.history = self.state / "history"
        self.history.mkdir(mode=0o700)
        self.job = self.history / "job-abcdef0123456789abcdef01"
        self.job.mkdir(mode=0o700)
        (self.job / "evidence").mkdir(mode=0o700)
        (self.repo / "docs").mkdir()
        (self.repo / "assets").mkdir()
        (self.repo / "docs/a.txt").write_text("Before\n")
        (self.repo / "assets/data.bin").write_bytes(b"\x00\x01\xff\x00")
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "baseline")
        self.base = self.git("rev-parse", "HEAD")
        (self.repo / "docs/a.txt").write_text("After\n")
        (self.repo / "assets/data.bin").write_bytes(b"\x00\x99\xff\x00")
        self.git("add", "-A")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "two approved Product edits")
        self.commit = self.git("rev-parse", "HEAD")
        self.scope = ["assets/data.bin", "docs/a.txt"]
        self.save_records()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args],
                                       timeout=25).decode().strip()

    def write(self, name, doc):
        (self.job / name).write_bytes(ga.canonical(doc))
        (self.job / name).chmod(0o600)

    def save_records(self):
        self.write("job.json", {
            "schema_version":1, "kind":"KAGARI_GA_WORKUNIT_JOB",
            "job_id":self.job.name,"workunit_clone":str(self.repo),
            "Project_head":self.base,"allowed_paths":self.scope,
            "ga_receipt_sha256":"a" * 64,
        })
        self.write("result.json", {
            "status":"GA_MANAGED_WORKUNIT_TASK_PASS",
            "Native_exit":0,"Native_status":"REAL_NATIVE_GENERIC_TASK_TEST_ONLY_PASS",
            "Product_commit":self.commit,
            "native_v4_Evidence_readback":{
                "status":"FRESH_PROCESS_NATIVE_READBACK_PASS",
                "product_commit":self.commit,
            },
        })
        self.write("evidence/report.json", {
            "status":"REAL_NATIVE_GENERIC_TASK_TEST_ONLY_PASS",
            "product_commit":self.commit,
        })

    def test_complete_binary_patch_replay_and_disposable_clone_loss(self):
        status = ga.archive(self.job)
        self.assertEqual(status["status"], "DURABLE_PRODUCT_ARTIFACT_VERIFIED")
        self.assertEqual(status["product_commit"], self.commit)
        self.assertEqual(ga.verify(self.job), status)
        self.assertTrue((self.job / ga.ARTIFACT).is_file())
        self.assertEqual((self.job / ga.ARTIFACT).stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.job / ga.RECEIPT).stat().st_mode & 0o777, 0o600)
        receipt = json.loads((self.job / ga.RECEIPT).read_bytes())
        self.assertEqual(receipt["actual_product_paths"], self.scope)
        self.assertEqual(receipt["source_head"], self.base)
        self.assertEqual(receipt["product_commit"], self.commit)
        self.assertEqual(receipt["qualification"], "ARTIFACT_ONLY_NOT_GA")
        self.assertFalse(receipt["source_project_mutated"])

        # A separate ordinary full-history repository at the original source
        # must reproduce the change, without touching the source Work Unit.
        clone = self.root / "manual-replay"
        subprocess.run(["git", "clone", "-q", "--no-local", str(self.repo),
                        str(clone)], check=True, timeout=35)
        subprocess.run(["git", "-C", str(clone), "checkout", "-q",
                        "--detach", self.base], check=True, timeout=35)
        env = os.environ.copy()
        env.update(GIT_COMMITTER_NAME="Consumer Operator",
                   GIT_COMMITTER_EMAIL="operator@example.invalid")
        with (self.job / ga.ARTIFACT).open("rb") as stream:
            subprocess.run(["git", "-C", str(clone), "am", "--no-gpg-sign"],
                           stdin=stream, check=True, env=env, timeout=35,
                           stdout=subprocess.DEVNULL)
        self.assertEqual((clone / "docs/a.txt").read_text(), "After\n")
        self.assertEqual((clone / "assets/data.bin").read_bytes(), b"\x00\x99\xff\x00")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.commit)

        # /tmp deletion is simulated only on our own disposable fixture.
        shutil.rmtree(self.repo)
        self.assertEqual(ga.verify(self.job), status)

    def test_refuses_unknown_product_commit_scope_without_writing(self):
        self.scope = ["docs/a.txt"]
        self.save_records()
        with self.assertRaisesRegex(ga.Blocked, "outside Contract"):
            ga.archive(self.job)
        self.assertFalse((self.job / ga.ARTIFACT).exists())

    def test_refuses_false_success_without_writing(self):
        result = json.loads((self.job / "result.json").read_bytes())
        result["status"] = "BLOCKED"
        self.write("result.json", result)
        with self.assertRaisesRegex(ga.Blocked, "completion"):
            ga.archive(self.job)
        self.assertFalse((self.job / ga.ARTIFACT).exists())

    def test_refuses_result_commit_forgery(self):
        result = json.loads((self.job / "result.json").read_bytes())
        result["Product_commit"] = "0" * 40
        self.write("result.json", result)
        with self.assertRaises(ga.Blocked):
            ga.archive(self.job)
        self.assertFalse((self.job / ga.ARTIFACT).exists())

    def test_refuses_existing_patch_or_symlink(self):
        forbidden = self.root / "human-private"
        forbidden.write_bytes(b"unrelated human bytes")
        (self.job / ga.ARTIFACT).symlink_to(forbidden)
        with self.assertRaisesRegex(ga.Blocked, "already exists"):
            ga.archive(self.job)
        self.assertEqual(forbidden.read_bytes(), b"unrelated human bytes")
        self.assertFalse((self.job / ga.RECEIPT).exists())

    def test_refuses_nonprivate_job_folder(self):
        self.job.chmod(0o755)
        with self.assertRaisesRegex(ga.Blocked, "private"):
            ga.archive(self.job)
        self.assertFalse((self.job / ga.ARTIFACT).exists())

    def test_rejects_oversized_patch_before_disk_write(self):
        with mockpatch.object(ga, "PATCH_LIMIT", 40):
            with self.assertRaisesRegex(ga.Blocked, "budget"):
                ga.archive(self.job)
        self.assertFalse((self.job / ga.ARTIFACT).exists())
        self.assertFalse((self.job / ga.RECEIPT).exists())

    def test_readback_detects_patch_tampering(self):
        ga.archive(self.job)
        with (self.job / ga.ARTIFACT).open("ab") as f:
            f.write(b"tampered")
        with self.assertRaisesRegex(ga.Blocked, "changed or"):
            ga.verify(self.job)

    def test_completion_archive_before_success_result_is_saved(self):
        result = json.loads((self.job / "result.json").read_bytes())
        (self.job / "result.json").unlink()
        view = ga.archive(self.job, result_override=result)
        self.assertEqual(view["status"], "DURABLE_PRODUCT_ARTIFACT_VERIFIED")
        self.assertFalse((self.job / "result.json").exists())
        self.write("result.json", result)
        self.assertEqual(ga.verify(self.job), view)

    def test_corrupt_archive_before_success_does_not_create_result(self):
        result=json.loads((self.job/"result.json").read_bytes())
        (self.job/"result.json").unlink()
        view=ga.archive(self.job,result_override=result)
        self.assertEqual(view["status"],"DURABLE_PRODUCT_ARTIFACT_VERIFIED")
        self.assertFalse((self.job/"result.json").exists())
        with (self.job/ga.ARTIFACT).open("ab") as stream:
            stream.write(b"corrupted before host success")
        with self.assertRaisesRegex(ga.Blocked,"changed or"):
            ga.verify(self.job,result_override=result)
        self.assertFalse((self.job/"result.json").exists())

    def test_missing_workunit_does_not_change_verified_receipt(self):
        ga.archive(self.job)
        receipt = (self.job / ga.RECEIPT).read_bytes()
        shutil.rmtree(self.repo)
        view = ga.verify(self.job)
        self.assertEqual(view["product_commit"], self.commit)
        self.assertEqual((self.job / ga.RECEIPT).read_bytes(), receipt)
        with self.assertRaises(ga.Blocked):
            ga.archive(self.job)

    def test_cli_rejects_absent_job(self):
        cmd = [sys.executable, "-I", "-B", str(source),
               "verify", "--jobdir", str(self.root / "does-not-exist")]
        proc = subprocess.run(cmd,capture_output=True,text=True,timeout=20)
        self.assertEqual(proc.returncode,2)
        self.assertEqual(json.loads(proc.stdout)["status"],"BLOCKED")

if __name__ == "__main__":
    unittest.main()
