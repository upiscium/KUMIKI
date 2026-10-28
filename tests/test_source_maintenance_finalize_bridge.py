from __future__ import annotations

import fcntl
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "components" / "agent-core" / ".automation" / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

BRIDGE_SPEC = importlib.util.spec_from_file_location(
    "source_finalize_bridge_test", ROOT / "tools" / "automation_recovery_bridge.py"
)
assert BRIDGE_SPEC and BRIDGE_SPEC.loader
bridge = importlib.util.module_from_spec(BRIDGE_SPEC)
BRIDGE_SPEC.loader.exec_module(bridge)

MAINTENANCE_SPEC = importlib.util.spec_from_file_location(
    "source_finalize_maintenance_test", BIN / "maintenance_lifecycle.py"
)
assert MAINTENANCE_SPEC and MAINTENANCE_SPEC.loader
maintenance = importlib.util.module_from_spec(MAINTENANCE_SPEC)
MAINTENANCE_SPEC.loader.exec_module(maintenance)


class FirstAdoptionFinalizeBridgeTest(unittest.TestCase):
    def git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(
            ("git", *args), cwd=cwd, check=True, text=True, capture_output=True
        )
        return result.stdout.strip()

    def configure(self, repo: Path) -> None:
        self.git(repo, "config", "user.name", "Bootstrap Finalize Test")
        self.git(repo, "config", "user.email", "bootstrap@example.invalid")

    def test_verified_module_bundle_loads_actual_private_state_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            self.git(source, "init", "-b", "main")
            self.configure(source)
            for _, relative in bridge.CANONICAL_MODULES:
                destination = source / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, destination)
            self.git(source, "add", ".")
            self.git(source, "commit", "-m", "verified module fixture")
            revision = self.git(source, "rev-parse", "HEAD")
            pinned = mock.Mock(stdout="response\n")
            with mock.patch.object(bridge, "_pinned_run", return_value=pinned) as run, \
                 bridge._verified_modules(source, revision) as loaded:
                self.assertIn("git_private_state", loaded)
                self.assertIn("maintenance_lifecycle", loaded)
                self.assertTrue(callable(loaded["maintenance_lifecycle"].maintenance_finalize))
                result = loaded["agent_core"].gh(
                    "api", "repos/upiscium/Templates", cwd=source,
                    input_text="expected body",
                )

            self.assertEqual(result, "response")
            run.assert_called_once_with(
                ["gh", "api", "repos/upiscium/Templates"],
                cwd=source,
                input_text="expected body",
            )
            self.assertNotIn("expected body", run.call_args.args[0])

    def test_stale_main_bootstraps_fast_forward_and_exact_idempotent_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            remote = top / "origin.git"
            main = top / "consumer"
            publisher = top / "publisher"
            subprocess.run(
                ("git", "init", "--bare", "--initial-branch=main", str(remote)),
                cwd=top,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ("git", "init", "--initial-branch=main", str(main)),
                cwd=top,
                check=True,
                capture_output=True,
                text=True,
            )
            self.configure(main)
            (main / "Justfile").write_text("default:\n    @just --list\n", encoding="utf-8")
            self.git(main, "add", "Justfile")
            self.git(main, "commit", "-m", "old Agent Core")
            stale_main = self.git(main, "rev-parse", "HEAD")
            self.git(main, "remote", "add", "origin", str(remote))
            self.git(main, "push", "-u", "origin", "main")
            self.git(
                main,
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                "refs/remotes/origin/main",
            )
            common = Path(self.git(main, "rev-parse", "--git-common-dir"))
            if not common.is_absolute():
                common = main / common
            (common / "info").mkdir(parents=True, exist_ok=True)
            (common / "info" / "exclude").write_text(
                "/.worktrees/\n/.task-state/\n", encoding="utf-8"
            )

            task = main / ".worktrees" / "22-maintenance"
            self.git(main, "worktree", "add", "-b", "task/22-maintenance", str(task))
            (task / "Justfile").write_text(
                "maintenance-finalize task pr:\n    @true\n", encoding="utf-8"
            )
            self.git(task, "add", "Justfile")
            self.git(task, "commit", "-m", "install current Agent Core")
            task_head = self.git(task, "rev-parse", "HEAD")
            self.git(task, "push", "-u", "origin", "task/22-maintenance")

            state_dir = task / ".task-state"
            state_dir.mkdir()
            state = state_dir / "task.md"
            state.write_text(
                "# Task 22\n\n## Identity\n\n"
                "- Task ID: 22\n- Branch: task/22-maintenance\n"
                f"- Worktree: {task}\n\n## Current state\n\n"
                "- Status: initialized\n- Blockers: none\n- Unverified: none\n",
                encoding="utf-8",
            )
            # The terminal CAS must see all of its actual persisted authority
            # files, even when this bridge fixture mocks remote GitHub reads.
            for filename in (
                "contract.json", "issue.json", "work-units.json",
                "verification.json", "automation-maintenance.consumed.json",
            ):
                (state_dir / filename).write_text("{}\n", encoding="utf-8")

            subprocess.run(
                ("git", "clone", str(remote), str(publisher)),
                cwd=top,
                check=True,
                capture_output=True,
                text=True,
            )
            self.configure(publisher)
            self.git(publisher, "fetch", "origin", "task/22-maintenance")
            self.git(publisher, "merge", "--no-ff", "--no-edit", "origin/task/22-maintenance")
            merge_oid = self.git(publisher, "rev-parse", "HEAD")
            self.git(publisher, "push", "origin", "main")
            self.assertEqual(self.git(main, "rev-parse", "HEAD"), stale_main)
            self.assertNotIn("maintenance-finalize", (main / "Justfile").read_text(encoding="utf-8"))

            record = maintenance.lifecycle.WorktreeRecord(
                task, "task/22-maintenance", task_head
            )
            canonical_body = "canonical body"
            live_body = canonical_body + "\n"
            publication = (["Justfile"], "22: maintenance", canonical_body)
            # Real Linux FLOCK (nonblocking): reconstructing publication
            # evidence while already in the terminal Task lock must fail.
            # The old finalizer executed this for a third time and deadlocked.
            with maintenance.lifecycle.work_units_lock(record):
                pass
            lock_path = state_dir / "work-units.lock"

            def reconstruct_with_real_lock(*_args):
                with lock_path.open("rb") as locked_file:
                    fcntl.flock(locked_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        return publication
                    finally:
                        fcntl.flock(locked_file.fileno(), fcntl.LOCK_UN)

            merged = {
                "number": 23,
                "title": "22: maintenance",
                "body": live_body,
                "baseRefName": "main",
                "headRefName": "task/22-maintenance",
                "headRefOid": task_head,
                "isDraft": False,
                "isCrossRepository": False,
                "mergeCommit": {"oid": merge_oid},
                "mergeable": "MERGEABLE",
                "statusCheckRollup": [],
                "state": "MERGED",
            }
            revision = "a" * 40

            def run_bridge() -> dict:
                with mock.patch.object(bridge, "_clean_root", return_value=revision), \
                     mock.patch.object(bridge, "_verify_bootstrap"), \
                     mock.patch.object(bridge, "_verified_modules") as modules, \
                     mock.patch.object(bridge, "maintenance_environment", return_value=nullcontext()), \
                     mock.patch.object(
                         sys,
                         "argv",
                         ["bridge", "maintenance-finalize", str(main), "22", "23", revision],
                     ), \
                     mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                    modules.return_value.__enter__.return_value = {
                        "maintenance_lifecycle": maintenance
                    }
                    self.assertEqual(bridge.main(), 0)
                return json.loads(output.getvalue())

            with mock.patch.object(
                maintenance,
                "_stored_contract",
                return_value=(record, {"repository": "upiscium/AgentKnowledgeVault"}),
            ), mock.patch.object(
                maintenance, "_validate_consumed_receipt", return_value={"commit_sha": task_head}
            ) as receipts, mock.patch.object(
                maintenance, "_require_live_maintenance_contract"
            ) as live_issue, mock.patch.object(
                maintenance, "_publication_evidence", side_effect=reconstruct_with_real_lock
            ) as publications, mock.patch.object(
                maintenance.agent_core, "pr_details", return_value=merged
            ) as prs, mock.patch.object(
                maintenance.agent_core,
                "canonical_repository",
                return_value="upiscium/AgentKnowledgeVault",
            ), mock.patch.object(
                maintenance.lifecycle, "require_resolved_contract"
            ):
                first = run_bridge()
                first_state = state.read_bytes()
                second = run_bridge()
                self.assertEqual(live_issue.call_count, 4)
                self.assertTrue(all(
                    call.kwargs.get("runner") is bridge.trusted_gh_run
                    for call in live_issue.call_args_list
                ))

            self.assertEqual(self.git(main, "rev-parse", "HEAD"), merge_oid)
            self.assertEqual(first["transition"], "finalized")
            self.assertEqual(first["status"], "FINALIZED")
            self.assertEqual(first["publishedHead"], task_head)
            self.assertEqual(second["transition"], "already-finalized")
            self.assertEqual(second["status"], "FINALIZED")
            self.assertEqual(state.read_bytes(), first_state)
            self.assertIn("- Status: merged", first_state.decode("utf-8"))
            self.assertEqual(first_state.count(b"### Maintenance publication"), 1)
            self.assertEqual(receipts.call_count, 4)
            self.assertEqual(publications.call_count, 4)
            self.assertEqual(prs.call_count, 4)
            self.assertTrue(all(call.args[1] == "23" for call in prs.call_args_list))
            self.assertEqual(merged["body"], live_body)
            self.assertEqual(live_body.count("\n"), 1)
            self.assertEqual(publication[2].rstrip("\n"), publication[2])


    def test_source_ref_lock_recovery_dispatch_binds_exact_identity_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "consumer"
            target.mkdir()
            revision = "a" * 40
            merged = "b" * 40
            task_head = "c" * 40
            digest = "d" * 64
            fake = mock.Mock()
            initial_report = {"status": "INSPECT_ONLY", "recoveryDigest": digest}
            with (
                mock.patch.object(bridge, "_clean_root", return_value=revision),
                mock.patch.object(bridge, "_verify_bootstrap"),
                mock.patch.object(bridge, "_validate_target_git_configuration"),
                mock.patch.object(bridge, "maintenance_environment", return_value=nullcontext()),
                mock.patch.object(bridge, "_verified_modules") as module_context,
                mock.patch.object(bridge, "_maintenance_ref_lock_recovery", return_value=initial_report) as recovery,
            ):
                module_context.return_value.__enter__.return_value = {
                    "maintenance_lifecycle": fake,
                }

                def run(args: list[str]) -> dict:
                    with (
                        mock.patch.object(sys, "argv", args),
                        mock.patch("sys.stdout", new_callable=io.StringIO) as output,
                    ):
                        self.assertEqual(bridge.main(), 0)
                    return json.loads(output.getvalue())

                args = [
                    "bridge", "maintenance-ref-locks", str(target),
                    "15", "17", merged, task_head, "1757545", revision,
                ]
                inspected = run(args)
                self.assertEqual(inspected["status"], "INSPECT_ONLY")
                self.assertEqual(inspected["implementationRevision"], revision)
                recovery.assert_called_with(
                    {"maintenance_lifecycle": fake}, target, "15", 17, merged, task_head,
                    1757545, apply_digest=None,
                )

                recovery.return_value = {
                    "status": "EXACT_REF_LOCKS_RECOVERED", "recoveryDigest": digest,
                }
                applied = run(args + ["--apply-digest", digest])
                self.assertEqual(applied["status"], "EXACT_REF_LOCKS_RECOVERED")
                recovery.assert_called_with(
                    {"maintenance_lifecycle": fake}, target, "15", 17, merged, task_head,
                    1757545, apply_digest=digest,
                )



if __name__ == "__main__":
    unittest.main()
