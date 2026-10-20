"""No-provider regression tests for the narrow RC OpenCode package prewarm.

These tests assert that no .opencode paths are exempted from the original
native physical Contract once the host-owned dependency bootstrap completes.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
import kagari_v010_rc_opencode_bootstrap as bootstrap
import kagari_v010_native_generic_task as native


class RCPrewarmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        value=os.environ.get("KAGARI_V010_RC_PACKAGE")
        if not value:
            raise RuntimeError("exact KAGARI_V010_RC_PACKAGE must be provided by CI")
        cls.package=Path(value)
        cls.receipt=json.loads(
            (cls.package/"share/kagari-v010-rc/receipt.json").read_bytes())
        cls.lock=(ROOT/"nix/kagari-v010-rc-opencode-plugin-lock.json").read_bytes()
        cls.node_lock=(
            ROOT/"nix/kagari-v010-rc-opencode-node-modules-lock.json").read_bytes()

    def test_source_selected_locks_are_exact_and_subsets(self):
        self.assertEqual(hashlib.sha256(self.lock).hexdigest(),
                         bootstrap.LOCK_SHA256)
        self.assertEqual(hashlib.sha256(self.node_lock).hexdigest(),
                         bootstrap.NODE_LOCK_SHA256)
        bootstrap.validate_pinned_lock(self.lock)
        bootstrap.validate_node_lock(self.node_lock,self.lock)
        outer=json.loads(self.lock)
        self.assertEqual(outer["packages"][""]["dependencies"],{
            "@opencode-ai/plugin":"1.18.35"})
        self.assertEqual(len(json.loads(self.node_lock)["packages"]),27)
        self.assertTrue(all(
            v["resolved"].startswith("https://registry.npmjs.org/")
            for v in json.loads(self.node_lock)["packages"].values()))

    def test_tampered_lock_and_nonregistry_lock_block(self):
        with self.assertRaisesRegex(bootstrap.BootstrapBlocked,"differs"):
            bootstrap.validate_pinned_lock(self.lock+b"\n")
        with self.assertRaisesRegex(bootstrap.BootstrapBlocked,"installed dependency lock"):
            bootstrap.validate_node_lock(self.node_lock+b" ",self.lock)
        mutated=json.loads(self.node_lock)
        k=next(iter(mutated["packages"]))
        mutated["packages"][k]["resolved"]="file:///tmp/malicious-package.tgz"
        with self.assertRaises(bootstrap.BootstrapBlocked):
            bootstrap.validate_node_lock(json.dumps(mutated).encode(),self.lock)

    def test_only_exact_tool_owned_paths_are_warmable(self):
        yes={
            ".opencode",
            ".opencode/node_modules",
            ".opencode/package.json",
            ".opencode/package-lock.json",
            ".opencode/bun.lock",
            ".opencode/.gitignore",
            ".opencode/node_modules/.package-lock.json",
            ".opencode/node_modules/@opencode-ai/plugin/index.js",
        }
        no={
            ".opencode/agents/build.md",
            ".opencode/plugins/user.js",
            ".opencode/commands/init.md",
            ".opencode/agent.json",
            ".opencode/.npmrc",
            ".opencode/node_modules-evil/README",
            ".git/opencode",
            ".git/config",
            "docs/a.md",
            "opencode.json",
            "AGENTS.md",
            "../outside",
        }
        for path in yes:
            with self.subTest(allowed=path):
                self.assertTrue(bootstrap.allowed_tool_delta(path))
        for path in no:
            with self.subTest(denied=path):
                self.assertFalse(bootstrap.allowed_tool_delta(path))

    def test_inventory_digest_binds_ignored_dependency_bytes(self):
        first={
            "docs/a.md":("file","a",0o644,1),
            ".opencode":("dir",0o755),
            ".opencode/package-lock.json":("file","digest1",0o600,1),
            ".opencode/node_modules":("dir",0o755),
            ".opencode/node_modules/plugin/file.js":("file","old",0o644,1),
        }
        changed=copy.deepcopy(first)
        changed[".opencode/node_modules/plugin/file.js"]=("file","new",0o644,1)
        external=copy.deepcopy(first)
        external["docs/a.md"]=("file","other",0o644,1)
        self.assertNotEqual(bootstrap.package_inventory_digest(first),
                            bootstrap.package_inventory_digest(changed))
        self.assertEqual(bootstrap.package_inventory_digest(first),
                         bootstrap.package_inventory_digest(external))

    def test_native_physical_scope_still_rejects_dependency_mutations(self):
        baseline={
            "docs/a.md":("file","old",0o644,1),
            ".opencode":("dir",0o755),
            ".opencode/package-lock.json":("file","locked",0o600,1),
            ".opencode/node_modules":("dir",0o755),
            ".opencode/node_modules/plugin/index.js":("file","installed",0o644,1),
        }
        allowed={"docs/a.md"}
        product=copy.deepcopy(baseline)
        product["docs/a.md"]=("file","authorized-change",0o644,1)
        self.assertEqual(native._scope_delta(baseline,product,allowed),allowed)
        unwanted=copy.deepcopy(product)
        unwanted[".opencode/node_modules/plugin/index.js"]=(
            "file","model-rewritten",0o644,1)
        with self.assertRaisesRegex(native.NativeBlocked,"outside or missing Contract"):
            native._scope_delta(baseline,unwanted,allowed)
        added=copy.deepcopy(product)
        added[".opencode/node_modules/untrusted.js"]=("file","payload",0o644,1)
        with self.assertRaisesRegex(native.NativeBlocked,"outside or missing Contract"):
            native._scope_delta(baseline,added,allowed)
        changed_agents=copy.deepcopy(product)
        changed_agents[".opencode/agents/build.md"]=("file","changed",0o644,1)
        with self.assertRaisesRegex(native.NativeBlocked,"outside or missing Contract"):
            native._scope_delta(baseline,changed_agents,allowed)

    def fixture(self):
        scratch=tempfile.TemporaryDirectory(prefix="kagari-v010-rc-work-",dir="/tmp")
        self.addCleanup(scratch.cleanup)
        parent=Path(scratch.name)
        project=parent/"project"
        project.mkdir()
        proc=subprocess.run(["git","-C",str(project),"init","-q"],
                            check=True,capture_output=True,timeout=15)
        state=tempfile.TemporaryDirectory(prefix="kagari-rc-prewarm-tests-")
        self.addCleanup(state.cleanup)
        jobdir=Path(state.name)
        return parent,project,jobdir

    def scoped_agents_worktree(self):
        """Independent test Git Project with only a tracked AGENTS addendum."""
        _parent,project,jobdir=self.fixture()
        agents=project/"AGENTS.md"
        agents.write_text("Original instructions.\n")
        (project/"product.txt").write_text("untouched product\n")
        subprocess.run(
            ["git","-C",str(project),"add","--","AGENTS.md","product.txt"],
            check=True,capture_output=True,timeout=15)
        subprocess.run([
            "git","-C",str(project),"-c","user.name=Fixture",
            "-c","user.email=fixture@example.invalid",
            "commit","-qm","baseline scoped agents fixture"],
            check=True,capture_output=True,timeout=15)
        self.assertEqual(bootstrap.git_output(project,"status","--porcelain=v1"),b"")
        postimage=agents.read_bytes()+b"\n## Exact scoped KAGARI exception\n"
        agents.write_bytes(postimage)
        digest=hashlib.sha256(postimage).hexdigest()
        return project,jobdir,digest

    def test_exact_active_agents_postimage_only_exception_to_clean_workunit(self):
        project,_jobdir,digest=self.scoped_agents_worktree()
        self.assertEqual(
            bootstrap.verified_bootstrap_git_status(
                project,approved_agents_postimage_sha256=digest),
            b"M AGENTS.md")
        self.assertEqual(bootstrap.git_output(
            project,"diff","--cached","--name-only"),b"")
        with self.assertRaisesRegex(bootstrap.BootstrapBlocked,"dirty"):
            bootstrap.verified_bootstrap_git_status(
                project,approved_agents_postimage_sha256=None)

    def test_missing_or_wrong_scoped_agents_digest_is_fail_closed(self):
        project,_jobdir,digest=self.scoped_agents_worktree()
        for invalid in ("0"*64,"",False,13,"A"*64):
            with self.subTest(invalid=invalid):
                with self.assertRaises(bootstrap.BootstrapBlocked):
                    bootstrap.verified_bootstrap_git_status(
                        project,approved_agents_postimage_sha256=invalid)
        self.assertEqual(
            bootstrap.verified_bootstrap_git_status(
                project,approved_agents_postimage_sha256=digest),
            b"M AGENTS.md")

    def test_scoped_agents_exception_rejects_staged_or_other_dirty_changes(self):
        project,_jobdir,digest=self.scoped_agents_worktree()
        product=project/"product.txt"
        product.write_text("unapproved product edit\n")
        with self.assertRaisesRegex(bootstrap.BootstrapBlocked,
                                    "unapproved staged/dirty"):
            bootstrap.verified_bootstrap_git_status(
                project,approved_agents_postimage_sha256=digest)
        product.write_text("untouched product\n")
        subprocess.run(["git","-C",str(project),"add","--","AGENTS.md"],
                       check=True,capture_output=True,timeout=15)
        with self.assertRaisesRegex(bootstrap.BootstrapBlocked,
                                    "unapproved staged/dirty"):
            bootstrap.verified_bootstrap_git_status(
                project,approved_agents_postimage_sha256=digest)
        subprocess.run(["git","-C",str(project),"reset","-q","--","AGENTS.md"],
                       check=True,capture_output=True,timeout=15)
        self.assertEqual(
            bootstrap.verified_bootstrap_git_status(
                project,approved_agents_postimage_sha256=digest),
            b"M AGENTS.md")

    def test_scoped_agents_exception_rejects_symlink_alias(self):
        project,jobdir,digest=self.scoped_agents_worktree()
        outside=jobdir/"private-rules"
        outside.write_bytes((project/"AGENTS.md").read_bytes())
        (project/"AGENTS.md").unlink()
        (project/"AGENTS.md").symlink_to(outside)
        with self.assertRaises(bootstrap.BootstrapBlocked):
            bootstrap.verified_bootstrap_git_status(
                project,approved_agents_postimage_sha256=digest)
        self.assertEqual(outside.read_bytes(),
                         b"Original instructions.\n\n## Exact scoped KAGARI exception\n")

    def test_disposable_project_and_private_external_root_required(self):
        _parent,project,jobdir=self.fixture()
        bootstrap.workunit_guard(project,jobdir)
        with self.assertRaisesRegex(bootstrap.BootstrapBlocked,"private RC Work Unit"):
            bootstrap.workunit_guard(project.parent,jobdir)
        nested=project/"owned-job"
        nested.mkdir(mode=0o700)
        with self.assertRaisesRegex(bootstrap.BootstrapBlocked,"separate private"):
            bootstrap.workunit_guard(project,nested)

    def test_symlinked_git_metadata_and_aliases_refused(self):
        _,project,jobdir=self.fixture()
        # Keep the .git alias payload on the same filesystem as the
        # disposable Git Project. CI TMPDIR may be a different mount.
        replacement=project.parent/"gitdata"
        (project/".git").rename(replacement)
        (project/".git").symlink_to(replacement,target_is_directory=True)
        with self.assertRaisesRegex(bootstrap.BootstrapBlocked,"independent real Git"):
            bootstrap.workunit_guard(project,jobdir)
        (project/".git").unlink()
        replacement.rename(project/".git")
        fake=jobdir/"linked-job"
        fake.symlink_to(jobdir,target_is_directory=True)
        with self.assertRaises(bootstrap.BootstrapBlocked):
            bootstrap.workunit_guard(project,fake)

    def test_preexisting_tracked_opencode_symlink_refused_before_package(self):
        _,project,jobdir=self.fixture()
        config=project/".opencode"
        (config/"agents").mkdir(parents=True)
        target=jobdir/"user-private"
        target.mkdir()
        (target/"secret").write_text("do-not-touch")
        (config/"agents/foreign.md").symlink_to(target/"secret")
        subprocess.run(["git","-C",str(project),"add","--",
                        ".opencode/agents/foreign.md"],
                       check=True,capture_output=True,timeout=15)
        subprocess.run([
            "git","-C",str(project),"-c","user.name=Fixture",
            "-c","user.email=fixture@example.invalid",
            "commit","-qm","tracked symlink adversary fixture"],
            check=True,capture_output=True,timeout=15)
        with self.assertRaisesRegex(bootstrap.BootstrapBlocked,
                                    "symlink/special/foreign"):
            bootstrap.reject_preexisting_opencode_aliases(config)
        # The test process already imported Native from the selected source
        # path, while the runtime loads that exact member from the verified
        # Nix package. Substitute only that loader in this no-model test to
        # isolate the early tracked-symlink refusal, not any authority gate.
        with patch.object(bootstrap,"native_integrity_engine",return_value=native):
            with self.assertRaisesRegex(bootstrap.BootstrapBlocked,
                                        "symlink/special/foreign"):
                bootstrap.prewarm(
                    project,jobdir,rc_root=self.package,receipt=self.receipt,
                    native_root=Path(self.receipt["native_root"]),
                    executable=Path(sys.executable).resolve())
        self.assertEqual((target/"secret").read_text(),"do-not-touch")
        self.assertFalse((config/"package.json").exists())
        self.assertFalse((config/"node_modules").exists())
        self.assertFalse((jobdir/"opencode-bootstrap").exists())

    def test_preexisting_node_modules_symlink_refused_before_install(self):
        _,project,jobdir=self.fixture()
        config=project/".opencode"
        config.mkdir()
        alias=jobdir/"external"
        alias.mkdir()
        (config/"node_modules").symlink_to(alias,target_is_directory=True)
        with self.assertRaises(bootstrap.BootstrapBlocked):
            bootstrap.reject_preexisting_opencode_aliases(config)
        with self.assertRaises(bootstrap.BootstrapBlocked):
            bootstrap.prewarm(
                project,jobdir,rc_root=self.package,receipt=self.receipt,
                native_root=Path(self.receipt["native_root"]),
                executable=Path(sys.executable).resolve())
        self.assertFalse((config/"package.json").exists())
        self.assertFalse(list(alias.iterdir()))

    def test_existing_normal_opencode_config_is_accepted_without_alias(self):
        _,project,jobdir=self.fixture()
        config=project/".opencode"
        (config/"agents/nested").mkdir(parents=True)
        (config/"agents/build.md").write_text("native-role")
        (config/"agents/nested/readme.txt").write_text("user rules")
        bootstrap.reject_preexisting_opencode_aliases(config)
        self.assertFalse((config/"package.json").exists())

    def test_wrong_rc_lock_authority_blocks_before_mutating_workunit(self):
        _,project,jobdir=self.fixture()
        bad=copy.deepcopy(self.receipt)
        bad["opencode_node_modules_lock_sha256"]="0"*64
        package=Path(bad["native_root"])
        model=Path(sys.executable).resolve()
        with self.assertRaisesRegex(bootstrap.BootstrapBlocked,"not bound"):
            bootstrap.prewarm(project,jobdir,rc_root=self.package,
                              receipt=bad,native_root=package,executable=model)
        self.assertFalse((project/".opencode").exists())
        self.assertFalse((jobdir/"bootstrap-receipt.json").exists())
        self.assertFalse((jobdir/"opencode-bootstrap").exists())

    def test_pinned_rc_receipt_binds_three_exact_inputs_and_bun(self):
        script=self.package/"libexec/kagari-v010-rc/kagari_v010_rc_opencode_bootstrap.py"
        lock=self.package/"share/kagari-v010-rc/opencode-plugin-package-lock.json"
        nested=self.package/"share/kagari-v010-rc/opencode-node-modules-package-lock.json"
        for file,key in (
            (script,"opencode_bootstrap_sha256"),
            (lock,"opencode_plugin_lock_sha256"),
            (nested,"opencode_node_modules_lock_sha256"),
        ):
            self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(),
                             self.receipt[key])
        self.assertEqual(script.read_bytes(),
                         (ROOT/"tools/kagari_v010_rc_opencode_bootstrap.py").read_bytes())
        self.assertEqual(lock.read_bytes(),self.lock)
        self.assertEqual(nested.read_bytes(),self.node_lock)
        self.assertEqual(self.receipt["bun_version"],"1.3.13")
        self.assertEqual(self.receipt["opencode_plugin_version"],"1.18.35")
        self.assertTrue(Path(self.receipt["bun_executable"]).is_file())
        self.assertTrue(self.receipt["opencode_dependencies_bootstrapped_before_Native_snapshot"])
        self.assertIs(self.receipt["native_Project_scope_inventory_relaxed"],False)

    def test_rc_user_version_remains_unreleased_and_native_test_only(self):
        proc=subprocess.run([str(self.package/"bin/kagari-v010-rc"),"version"],
                            capture_output=True,text=True,timeout=20)
        self.assertEqual(proc.returncode,0,proc.stderr)
        result=json.loads(proc.stdout)
        self.assertEqual(result["qualification"],"RC_CANDIDATE_UNRELEASED")
        self.assertEqual(result["native_component_qualification"],
                         "TEST_ONLY_UNQUALIFIED")
        self.assertIs(result["released"],False)
        self.assertIs(self.receipt["production_activation"],False)


if __name__=="__main__":
    unittest.main()
