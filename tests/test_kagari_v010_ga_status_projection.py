from __future__ import annotations
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import kagari_v010_ga_stable_lifecycle as ga


class FakeAdmin:
    def verify_package(self, native, digest):
        return {"verified": True}

    def summary(self, clone, runtime, native, identity):
        return {"status": "INSTALLED_TEST_ONLY",
                "ready_for_model_task": True, "required_asks": [],
                "ask_status": {}, "ask_state_root": None}


class TaskProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kagari-314-projection-")
        self.addCleanup(self.temp.cleanup)
        self.jobdir = Path(self.temp.name) / ("job-" + "a" * 24)
        self.jobdir.mkdir(mode=0o700)
        self.record = {"workunit_clone": str(Path(self.temp.name) / "unit"),
                       "native_runtime": str(Path(self.temp.name) / "native"),
                       "task_spec_sha256": "b" * 64,
                       "Project_root": str(Path(self.temp.name) / "project"),
                       "Project_head": "c" * 40}
        self.request = {"request_id": "request-314"}
        self.receipt = {"native_receipt_sha256": "d" * 64}
        self.admin = FakeAdmin()

    def write(self, name, payload):
        ga.save_new(self.jobdir / name, ga.canonical(payload))

    def dispatched(self):
        self.write("task-approval.json",
                   {"schema_version": 1,
                    "kind": ga.ASK_KIND,
                    "request_id": self.request["request_id"],
                    "request_sha256": ga.sha(ga.canonical(self.request)),
                    "decision": "APPROVED",
                    "origin": "LOCAL_FOREGROUND_TTY_NOT_REMOTE_VERIFIED"})
        self.write("run-dispatch.json",
                   {"schema_version": 1,
                    "Task_approval_sha256": ga.sha(
                        ga.bytes_regular(self.jobdir / "task-approval.json", exact_mode=0o600)),
                    "kind": "KAGARI_GA_ONE_SHOT_DISPATCH",
                    "Task_request_id": self.request["request_id"],
                    "TaskSpec_sha256": self.record["task_spec_sha256"],
                    "Project_head": self.record["Project_head"],
                    "native_receipt_sha256": self.receipt["native_receipt_sha256"]})

    def view(self):
        return ga.task_details(self.jobdir, self.record, self.request,
                               Path("/tmp/native"), self.admin, self.receipt)

    def result(self, status):
        return {"schema_version": 1,
                "kind": "KAGARI_GA_MANAGED_WORKUNIT_TASK_RESULT",
                "job_id": self.jobdir.name,
                "original_Project_root": self.record["Project_root"],
                "native_runtime_receipt_sha256": self.receipt["native_receipt_sha256"],
                "status": status,
                "Product_commit": "e" * 40 if status == "GA_MANAGED_WORKUNIT_TASK_PASS" else None,
                "native_v4_Evidence_readback": {"status": "FRESH_PROCESS_NATIVE_READBACK_PASS"}
                   if status == "GA_MANAGED_WORKUNIT_TASK_PASS" else None}

    def test_ready_before_dispatch(self):
        view = self.view()
        self.assertEqual(view["status"], "GA_JOB_READY")
        self.assertIsNone(view["Product_commit"])
        self.assertFalse(view["run_dispatched"])

    def test_valid_exact_approval_is_reported(self):
        self.write("task-approval.json",
                   {"schema_version": 1,
                    "kind": ga.ASK_KIND,
                    "request_id": self.request["request_id"],
                    "request_sha256": ga.sha(ga.canonical(self.request)),
                    "decision": "APPROVED",
                    "origin": "LOCAL_FOREGROUND_TTY_NOT_REMOTE_VERIFIED"})
        self.assertTrue(self.view()["Task_approved"])

    def test_invalid_approval_is_not_reported_as_authorized(self):
        self.write("task-approval.json",
                   {"schema_version": 1,
                    "kind": ga.ASK_KIND,
                    "request_id": self.request["request_id"],
                    "request_sha256": "0" * 64,
                    "decision": "APPROVED",
                    "origin": "LOCAL_FOREGROUND_TTY_NOT_REMOTE_VERIFIED"})
        with self.assertRaises(ga.RCBlocked):
            self.view()

    def test_broken_dispatch_symlink_never_projects_readiness(self):
        (self.jobdir / "run-dispatch.json").symlink_to(
            self.jobdir / "not-a-dispatch")
        with self.assertRaises(ga.RCBlocked):
            self.view()

    def test_consumed_dispatch_without_result_never_ready(self):
        self.dispatched()
        view = self.view()
        self.assertEqual(view["status"], "GA_JOB_RESULT_UNAVAILABLE")
        self.assertTrue(view["run_dispatched"])
        self.assertIsNone(view["Product_commit"])

    def test_dispatched_approval_removed_fails_closed(self):
        self.dispatched()
        (self.jobdir / "task-approval.json").unlink()
        with self.assertRaises(ga.RCBlocked):
            self.view()

    def test_dispatch_approval_digest_mismatch_fails_closed(self):
        self.dispatched()
        p = self.jobdir / "run-dispatch.json"
        dispatch = ga.load(p, exact_mode=0o600)
        dispatch["Task_approval_sha256"] = "0" * 64
        p.write_bytes(ga.canonical(dispatch))
        with self.assertRaises(ga.RCBlocked):
            self.view()

    def test_blocked_result_remains_blocked(self):
        self.dispatched()
        self.write("result.json", self.result("BLOCKED"))
        view = self.view()
        self.assertEqual(view["status"], "GA_JOB_BLOCKED")
        self.assertEqual(view["result_status"], "BLOCKED")
        self.assertIsNone(view["Product_commit"])

    def test_verified_success_reports_actual_commit(self):
        self.dispatched()
        self.write("result.json", self.result("GA_MANAGED_WORKUNIT_TASK_PASS"))
        import kagari_v010_ga_product_archive as archive
        expected = {"status": "DURABLE_PRODUCT_ARTIFACT_VERIFIED",
                    "product_commit": "e" * 40}
        with patch.object(archive, "verify", return_value=expected) as verifier:
            view = self.view()
        self.assertTrue(verifier.called)
        self.assertEqual(view["status"], "GA_JOB_COMPLETED")
        self.assertEqual(view["Product_commit"], "e" * 40)
        self.assertRegex(view["result_sha256"], r"^[0-9a-f]{64}$")

    def test_mismatched_result_identity_fails_closed(self):
        self.dispatched()
        result = self.result("BLOCKED")
        result["job_id"] = "job-" + "f" * 24
        self.write("result.json", result)
        with self.assertRaises(ga.RCBlocked):
            self.view()

    def test_terminal_without_dispatch_fails_closed(self):
        self.write("result.json", self.result("BLOCKED"))
        with self.assertRaises(ga.RCBlocked):
            self.view()

    def test_unverified_success_archive_fails_closed(self):
        self.dispatched()
        self.write("result.json", self.result("GA_MANAGED_WORKUNIT_TASK_PASS"))
        import kagari_v010_ga_product_archive as archive
        with patch.object(archive, "verify", side_effect=ValueError("unverified")):
            with self.assertRaises(ga.RCBlocked):
                self.view()


if __name__ == "__main__":
    unittest.main()
