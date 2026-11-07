"""Phase D0: real Linux outside-Git storage, never executor qualification.

Only test-owned TemporaryDirectory /tmp roots are written. Policy/intent values
are invented; _outside_git is never replaced. Call-through syscall traces and
injected OSError failures are NOT crash/power-loss or authenticated replay tests.
No existing-root recovery, production retry, Main operation or model invocation.
"""
from __future__ import annotations

import ast
import builtins
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import inspect
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch


_import = builtins.__import__


def _no_transport(name, globals=None, locals=None, fromlist=(), level=0):
    if any("claude_transport" in part for part in (name, *(fromlist or ()))):
        raise AssertionError("real transport import")
    return _import(name, globals, locals, fromlist, level)


with patch("builtins.__import__", side_effect=_no_transport):
    from tools import _kagari_vnext_claude_r6b_phase_c as candidate
    from tools import _kagari_vnext_claude_r6b_phase_b as phase_b
    from tools import kagari_vnext_claude_lane_b as lane


ROOT = Path(__file__).resolve().parents[1]
SESSION = "b6a7f5a3-0da4-4141-b98d-89311798cab4"
OTHER_SESSION = "12cadac6-0e38-4472-9221-d1fb73f4ba5c"
REAL_OUTSIDE_GIT = candidate._outside_git
FILES = ("manifest.json", "pending.json", "reservation.json", "result.json")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def identity(info):
    return info.st_dev, info.st_ino


def retained_metadata(info):
    # Reading evidence can change atime; it must not change identity/content metadata.
    return (*identity(info), info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class _NoEffectsTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(sys.platform.startswith("linux"), "D0 requires real Linux filesystem semantics")
        self.enterContext(patch("builtins.__import__", side_effect=_no_transport))
        for name in ("subprocess.Popen", "subprocess.run", "os.system", "os.posix_spawn",
                     "os.posix_spawnp", "os.fork", "os.execv", "os.execve",
                     "socket.socket", "socket.create_connection"):
            guard = self.enterContext(patch(name, side_effect=AssertionError(name)))
            self.addCleanup(guard.assert_not_called)
        for module, names in (
            (lane.preflight, ("run_preflight", "_run_bounded", "_inspect_settings",
                              "_read_config", "_read_regular_file", "_executable_path",
                              "_profile_directory_identity")),
            (lane, ("_exercise_fixture", "_auth_probe", "_config_context", "_task_identity",
                    "observe", "_runtime_root")),
            (phase_b, ("_observe_main_intent",)),
            # Existing Phase B transitive imports are not new Main wiring.
            (phase_b.RepositoryMain, ("__init__",)),
            (phase_b.MainSupervisor, ("__init__",)),
        ):
            for name in names:
                guard = self.enterContext(patch.object(module, name, side_effect=AssertionError(name)))
                self.addCleanup(guard.assert_not_called)

    def assert_non_grant(self, result, *, status="UNQUALIFIED"):
        self.assertEqual(set(result), {"qualification", "dispatch_allowed", "task_completed",
                                      "ask_capability", "effective_policy", "status", "reason",
                                      "stage", "session_id"})
        self.assertEqual(result["status"], status)
        self.assertEqual(result["qualification"], "UNQUALIFIED")
        self.assertIs(result["dispatch_allowed"], False)
        self.assertIs(result["task_completed"], False)
        self.assertEqual(result["ask_capability"], "UNQUALIFIED")
        self.assertEqual(result["effective_policy"], "UNVERIFIED")
        self.assertIsNone(result["session_id"])
        for private in ("/tmp/", SESSION, OTHER_SESSION, "d0-injected-sync-error"):
            self.assertNotIn(private, json.dumps(result))

    def assert_unknown(self, result):
        self.assert_non_grant(result, status="UNKNOWN")
        self.assertEqual(result["reason"], "RECEIPT_RECONCILIATION_REQUIRED")
        self.assertEqual(result["stage"], "NOT_OBSERVED")


class PhaseDOutsideGitTests(_NoEffectsTests):
    def setUp(self):
        super().setUp()
        self.scratch = self.new_scratch()
        self.intent, self.evidence = self.synthetic_policy()

    def new_scratch(self):
        temp = self.enterContext(tempfile.TemporaryDirectory(prefix="kumiki-r6b-d0-", dir="/tmp"))
        scratch = Path(temp)
        self.assertEqual(scratch.parent, Path("/tmp"))
        self.assertEqual(scratch.resolve(strict=True), scratch)
        self.assert_private(scratch, directory=True)
        self.assertIs(candidate._outside_git, REAL_OUTSIDE_GIT)
        self.assertTrue(candidate._outside_git(scratch), "real /tmp root rejected by _outside_git")
        return scratch

    def synthetic_policy(self, session=SESSION):
        settings = digest("D0 TEST-ONLY settings; no actual settings collected")
        names = ("settings", "hooks", "mcp", "plugins", "tools", "managed_local", "managed_remote")
        surfaces = tuple(candidate._Surface(
            name, "PRESENT" if name == "settings" else "ABSENT",
            settings if name == "settings" else digest(None),
        ) for name in names)
        pins = phase_b._PolicyPins(
            digest("D0 TEST-ONLY binary"), digest("D0 TEST-ONLY config context"), settings,
            digest("D0 TEST-ONLY environment"), digest([asdict(s) for s in surfaces]),
        )
        # Nonexistent paths under our scratch, not a registered or approved Task.
        project = self.scratch / "uncreated-synthetic-project"
        intent = phase_b._OperationIntent(
            operation=lane.OPERATION, policy_version=phase_b._POLICY_VERSION, executor="claude-cli",
            repository="synthetic/d0-fixture", remote="origin", issue=340,
            branch_ref="refs/heads/synthetic-issue-340", base_revision="a" * 40,
            contract_id=digest("D0 TEST-ONLY contract"), project_root=str(project),
            worktree=str(project / "worktree"), git_common_dir=str(project / "git-admin"),
            issue_snapshot_sha256=digest("D0 TEST-ONLY Issue snapshot"), session_id=session,
            argv_sha256=digest(lane._argv(session)), policy_pins=pins,
            policy_sha256=phase_b._policy_digest(session, pins),
        )
        base = phase_b._PolicyEvidence(
            phase_b._SYNTHETIC_SOURCE, digest(asdict(intent)), session, intent.policy_sha256,
            pins, lane.EXPECTED_VERSION, "personal", (), phase_b._REQUIRED_CHECKS,
        )
        self.assertTrue(phase_b._valid_intent(intent))
        evidence = candidate._EffectivePolicyEvidence(base, surfaces)
        observed = candidate._observe_effective_policy(intent, evidence)
        self.assert_non_grant(observed)
        self.assertEqual(observed["stage"], "SYNTHETIC_EFFECTIVE_POLICY_OBSERVED")
        return intent, evidence

    def assert_private(self, path, *, directory=False):
        info = path.lstat()
        self.assertTrue(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o700 if directory else 0o600)
        self.assertEqual(info.st_uid, os.geteuid())
        if not directory:
            self.assertEqual(info.st_nlink, 1)

    def expected_files(self):
        pending = phase_b._pending_contract(self.intent)
        self.assertIsNotNone(pending)
        self.assertEqual(pending.namespace, "r6b-phase-b-" + digest({
            "repository": self.intent.repository, "issue": self.intent.issue,
        }))
        pending_digest = digest(asdict(pending))
        return {
            "manifest.json": canonical({
                "schema_version": 1, "purpose": "DISPOSABLE_SYNTHETIC_ONLY",
                "namespace": pending.namespace, "pending_sha256": pending_digest, "files": FILES,
            }),
            "reservation.json": canonical({"schema_version": 1, "state": "RESERVED_NO_REPLAY",
                                           "pending_sha256": pending_digest}),
            "pending.json": canonical(asdict(pending)),
        }

    def fixture(self, parent=None):
        parent = self.scratch if parent is None else parent
        fixture, result = candidate._new_disposable_fixture(parent, self.intent)
        self.assert_non_grant(result)
        self.assertEqual(result["reason"], "DISPOSABLE_FIXTURE_ONLY")
        self.assertIsNotNone(fixture, "real outside-Git fixture initialization failed")
        self.assertEqual(fixture.root.parent, parent)
        self.assertIs(candidate._outside_git, REAL_OUTSIDE_GIT)
        self.assertTrue(candidate._outside_git(fixture.root))
        return fixture

    def namespace(self, fixture):
        return fixture.root / phase_b._pending_contract(self.intent).namespace

    def snapshot(self, directory):
        # Never follow even a test-owned symlink when preserving failure evidence.
        return {p.name: (retained_metadata(p.lstat()), p.readlink() if p.is_symlink() else p.read_bytes())
                for p in directory.iterdir()}

    def assert_files(self, namespace, expected):
        self.assertEqual({p.name for p in namespace.iterdir()}, set(expected))
        for name, data in expected.items():
            path = namespace / name
            self.assert_private(path)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(data, canonical(json.loads(data)))
            self.assertLessEqual(len(data), 4096)
            for private in (str(self.scratch).encode(), SESSION.encode(), lane.FIXED_PROMPT.encode()):
                self.assertNotIn(private, data)

    @contextmanager
    def trace_storage(self, parent, *, fail_at=None):
        """Real open/fsync calls, except one explicit sync failure; no crash claim."""
        events, sync_count = [], 0
        real_open, real_sync, public_result = os.open, os.fsync, lane._public_result

        def opening(path, flags, *args, **kwargs):
            probe = Path(path)
            self.assertTrue(probe.is_relative_to(parent) if probe.is_absolute()
                            else str(path) in (*FILES, phase_b._pending_contract(self.intent).namespace))
            if flags & os.O_CREAT:
                self.assertIn(path, FILES)
                for required in (os.O_EXCL, os.O_NOFOLLOW, os.O_CLOEXEC, os.O_WRONLY):
                    self.assertTrue(flags & required)
                self.assertEqual(args, (0o600,))
            fd = real_open(path, flags, *args, **kwargs)
            if flags & os.O_CREAT:
                events.append(("create", path))
            return fd

        def syncing(fd):
            nonlocal sync_count
            sync_count += 1
            target = identity(os.fstat(fd))
            if sync_count == fail_at:
                events.append(("fsync_failed", target))
                raise OSError("d0-injected-sync-error")
            real_sync(fd)
            events.append(("fsync", target))

        def reporting(*args, **kwargs):
            result = public_result(*args, **kwargs)
            if result["reason"] == "DISPOSABLE_FIXTURE_ONLY":
                events.append(("report", result["reason"]))
            if result["stage"] == "FIXTURE_PENDING_DURABLE":
                events.append(("report", result["stage"]))
            return result

        with patch.object(os, "open", side_effect=opening), patch.object(
                os, "fsync", side_effect=syncing), patch.object(
                lane, "_public_result", side_effect=reporting):
            yield events

    def reserve(self, fixture):
        return candidate._reserve_fixture(fixture, self.intent, self.evidence)

    def assert_no_retry(self, fixture):
        namespace = self.namespace(fixture)
        before = self.snapshot(namespace)
        self.assertIs(fixture._fresh, False)
        with patch.object(candidate, "_exclusive", wraps=candidate._exclusive) as writer, patch.object(
                os, "fsync", wraps=os.fsync) as syncing:
            self.assert_unknown(self.reserve(fixture))
            self.assert_unknown(candidate._observe_fixture(fixture, self.intent))
        writer.assert_not_called()
        syncing.assert_not_called()
        self.assertEqual(before, self.snapshot(namespace))

    def test_real_outside_git_manifest_and_pending_sync_before_non_grant(self):
        expected = self.expected_files()
        with self.trace_storage(self.scratch) as initialized:
            fixture = self.fixture()
        namespace = self.namespace(fixture)
        for directory in (self.scratch, fixture.root, namespace):
            self.assert_private(directory, directory=True)
        self.assertEqual({p.name for p in fixture.root.iterdir()}, {namespace.name})
        self.assert_files(namespace, {"manifest.json": expected["manifest.json"]})
        self.assertEqual(initialized, [("create", "manifest.json"), *(
            ("fsync", identity(p.lstat()))
            for p in (namespace / "manifest.json", namespace, fixture.root, self.scratch)
        ), ("report", "DISPOSABLE_FIXTURE_ONLY")])
        with self.trace_storage(self.scratch) as reserved:
            result = self.reserve(fixture)
        self.assert_non_grant(result)
        self.assertEqual(result["stage"], "FIXTURE_PENDING_DURABLE")
        self.assertEqual(result["reason"], "FIXTURE_PENDING_NOT_DISPATCH")
        self.assertEqual(reserved, [
            ("create", "reservation.json"), ("fsync", identity((namespace / "reservation.json").lstat())),
            ("fsync", identity(namespace.lstat())), ("create", "pending.json"),
            ("fsync", identity((namespace / "pending.json").lstat())),
            ("fsync", identity(namespace.lstat())), ("report", "FIXTURE_PENDING_DURABLE"),
        ])
        self.assert_files(namespace, expected)
        self.assert_no_retry(fixture)

    def test_pending_only_duplicate_and_changed_uuid_never_grant(self):
        fixture = self.fixture()
        result = self.reserve(fixture)
        self.assert_non_grant(result)
        self.assertEqual(result["stage"], "FIXTURE_PENDING_DURABLE")
        self.assert_no_retry(fixture)
        other, evidence = self.synthetic_policy(OTHER_SESSION)
        first, second = (phase_b._pending_contract(i) for i in (self.intent, other))
        self.assertEqual(first.namespace, second.namespace)
        self.assertEqual(first.task_sha256, second.task_sha256)
        self.assertNotEqual(first.intent_sha256, second.intent_sha256)
        self.assertNotEqual(first.session_sha256, second.session_sha256)
        before = self.snapshot(self.namespace(fixture))
        with patch.object(candidate, "_exclusive", wraps=candidate._exclusive) as writer:
            self.assert_unknown(candidate._reserve_fixture(fixture, other, evidence))
            self.assert_unknown(candidate._observe_fixture(fixture, other))
        writer.assert_not_called()
        self.assertEqual(before, self.snapshot(self.namespace(fixture)))

    def test_each_reservation_fsync_failure_retains_partial_history_without_retry(self):
        for fail_at in range(1, 5):
            with self.subTest(fail_at=fail_at):
                parent = self.new_scratch()  # Separate real /tmp universe per injected failure.
                fixture = self.fixture(parent)
                with self.trace_storage(parent, fail_at=fail_at) as events:
                    self.assert_unknown(self.reserve(fixture))
                namespace = self.namespace(fixture)
                expected = self.expected_files()
                if fail_at < 3:
                    del expected["pending.json"]
                self.assert_files(namespace, expected)
                targets = [namespace / "reservation.json", namespace, namespace / "pending.json", namespace]
                self.assertEqual([event for event in events if event[0].startswith("fsync")], [
                    ("fsync_failed" if index == fail_at else "fsync", identity(path.lstat()))
                    for index, path in enumerate(targets[:fail_at], 1)
                ])
                self.assertEqual([value for kind, value in events if kind == "create"],
                                 ["reservation.json"] + (["pending.json"] if fail_at >= 3 else []))
                self.assertEqual(events[-1][0], "fsync_failed")
                self.assert_no_retry(fixture)

    def test_each_initialization_fsync_failure_retains_manifest_but_no_genesis_handle(self):
        for fail_at in range(1, 5):
            with self.subTest(fail_at=fail_at):
                parent = self.new_scratch()
                with self.trace_storage(parent, fail_at=fail_at) as events:
                    fixture, result = candidate._new_disposable_fixture(parent, self.intent)
                self.assertIsNone(fixture)
                self.assert_unknown(result)
                roots = list(parent.iterdir())
                self.assertEqual(len(roots), 1)  # Partial root retained, no automatic retry allocation.
                root = roots[0]
                namespace = root / phase_b._pending_contract(self.intent).namespace
                self.assert_files(namespace, {"manifest.json": self.expected_files()["manifest.json"]})
                targets = (namespace / "manifest.json", namespace, root, parent)
                self.assertEqual(events, [("create", "manifest.json"), *(
                    ("fsync_failed" if index == fail_at else "fsync", identity(path.lstat()))
                    for index, path in enumerate(targets[:fail_at], 1)
                )])
                before = self.snapshot(namespace)
                with patch.object(candidate, "_exclusive", wraps=candidate._exclusive) as writer:
                    for not_a_handle in (None, root):
                        self.assert_unknown(self.reserve(not_a_handle))
                        self.assert_unknown(candidate._observe_fixture(not_a_handle, self.intent))
                writer.assert_not_called()
                self.assertEqual(before, self.snapshot(namespace))

    def write_test_file(self, path, data):
        self.assertTrue(path.is_relative_to(self.scratch))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)

    def test_competing_file_and_symlink_are_retained_without_following_or_overwrite(self):
        for kind in ("file", "symlink"):
            with self.subTest(kind=kind):
                fixture = self.fixture()
                namespace = self.namespace(fixture)
                target = self.scratch / ("test-owned-target-" + kind)
                self.write_test_file(target, b"synthetic target, never policy or credentials")
                target_before = target.read_bytes(), retained_metadata(target.lstat())
                pending = namespace / "pending.json"
                exclusive, conflicts = candidate._exclusive, []

                def competing(fd, name, data):
                    if name == "pending.json":
                        self.assertIs(fixture._fresh, False)
                        self.assertTrue((namespace / "reservation.json").is_file())
                        if kind == "symlink":
                            pending.symlink_to(target)
                        else:
                            self.write_test_file(pending, b"synthetic competing pending")
                    try:
                        return exclusive(fd, name, data)
                    except FileExistsError:
                        conflicts.append(name)
                        raise

                # Deterministic injection before real O_EXCL, not a concurrent attacker.
                with patch.object(candidate, "_exclusive", side_effect=competing):
                    self.assert_unknown(self.reserve(fixture))
                self.assertEqual(conflicts, ["pending.json"])
                self.assertEqual(pending.is_symlink(), kind == "symlink")
                if kind == "symlink":
                    self.assertEqual(pending.readlink(), target)
                else:
                    self.assertEqual(pending.read_bytes(), b"synthetic competing pending")
                self.assertEqual((namespace / "reservation.json").read_bytes(),
                                 self.expected_files()["reservation.json"])
                self.assert_no_retry(fixture)
                self.assertEqual((target.read_bytes(), retained_metadata(target.lstat())), target_before)

    def test_existing_root_is_not_a_reopenable_proven_genesis(self):
        fixture = self.fixture()
        namespace = self.namespace(fixture)
        before = self.snapshot(namespace)
        for not_a_handle in (fixture.root, str(fixture.root), {"root": fixture.root, "complete": True}):
            self.assert_unknown(self.reserve(not_a_handle))
            self.assert_unknown(candidate._observe_fixture(not_a_handle, self.intent))
        with self.assertRaises(TypeError):
            candidate._new_disposable_fixture(self.scratch, self.intent, existing_root=fixture.root)
        with self.assertRaises(TypeError):
            candidate._reserve_fixture(fixture, self.intent, self.evidence, complete=True)
        self.assertEqual(before, self.snapshot(namespace))
        # Passing a prior root as scratch_parent creates a CHILD, never reopens it.
        child = self.fixture(fixture.root)
        self.assertNotEqual(child.root, fixture.root)
        self.assertEqual(child.root.parent, fixture.root)
        self.assert_unknown(self.reserve(fixture))  # Extra root entry is not adopted/repaired.
        self.assertEqual(before, self.snapshot(namespace))
        # Forged/tampered Python _Fixture objects are outside this API-boundary test.

    def test_new_synthetic_root_is_not_legitimate_retry_authority(self):
        first = self.fixture()
        self.assert_non_grant(self.reserve(first))
        before = self.snapshot(self.namespace(first))
        second = self.fixture(self.new_scratch())
        self.assertNotEqual(first.root, second.root)
        self.assertEqual(self.namespace(first).name, self.namespace(second).name)
        result = self.reserve(second)
        self.assert_non_grant(result)
        self.assertEqual(result["stage"], "FIXTURE_PENDING_DURABLE")
        # This second synthetic success DEMONSTRATES the missing cross-root registry;
        # it does not assert production duplicate exclusion or a retry permission.
        self.assert_no_retry(first)
        self.assert_no_retry(second)
        self.assertEqual(before, self.snapshot(self.namespace(first)))

    def test_self_authored_result_pair_is_not_authentication_or_crash_evidence(self):
        fixture = self.fixture()
        self.assert_non_grant(self.reserve(fixture))
        pending = phase_b._pending_contract(self.intent)
        result = phase_b._ResultReceipt(1, digest(asdict(pending)), "OBSERVED",
                                        "SYNTHETIC_TURN_RESULT", "TURN_RESULT_OBSERVED")
        # Test-authored bytes, not a real result writer or authenticated ledger.
        self.write_test_file(self.namespace(fixture) / "result.json", canonical(asdict(result)))
        observed = candidate._observe_fixture(fixture, self.intent)
        self.assert_non_grant(observed)
        self.assertEqual(observed["stage"], "RECEIPT_PAIR_OBSERVED")
        self.assertEqual(observed["reason"], "SYNTHETIC_RECEIPT_PAIR_NO_REPLAY")
        before = self.snapshot(self.namespace(fixture))
        self.assert_unknown(self.reserve(fixture))
        self.assertEqual(before, self.snapshot(self.namespace(fixture)))


class PhaseDSourceBoundaryTests(_NoEffectsTests):
    def test_no_new_main_transport_imports_or_production_effect_interface(self):
        for path in (Path(__file__), Path(candidate.__file__)):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.extend([node.module or "", *(alias.name for alias in node.names)])
            self.assertFalse(any("transport" in name or "main" in name for name in imports), imports)
        self.assertEqual(tuple(inspect.signature(candidate._new_disposable_fixture).parameters),
                         ("scratch_parent", "intent"))
        self.assertEqual(tuple(inspect.signature(candidate._reserve_fixture).parameters),
                         ("fixture", "intent", "evidence"))
        self.assertEqual(tuple(inspect.signature(candidate._observe_fixture).parameters),
                         ("fixture", "intent"))
        main_source = (ROOT / "tools/kagari_vnext_main.py").read_text(encoding="utf-8")
        self.assertIn('raise MainError("CLAUDE_EXECUTOR_UNQUALIFIED")', main_source)
        for source in (main_source, Path(lane.__file__).read_text(encoding="utf-8")):
            self.assertNotIn("r6b_phase_c", source)
            self.assertNotIn("r6b_phase_d", source)
        self.assert_non_grant(lane.dispatch())
        self.assertIs(candidate._outside_git, REAL_OUTSIDE_GIT)
