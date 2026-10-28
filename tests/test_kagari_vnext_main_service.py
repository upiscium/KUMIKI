"""#291 repository Main singleton/host entry tests using local Git fixtures."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import select
import shlex
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_kagari_vnext_main as main_fixture
from tests import test_metadata_ref as git_fixtures
from tools import kagari_vnext_main_service as service
from tools.kagari_vnext_main import ApprovedTask, MainError, RepositoryMain
from metadata_ref import MetadataStore


class MainServiceTest(unittest.TestCase):
    def setUp(self):
        self.source = main_fixture.MainOrchestratorTest("runTest")
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.main = self.source.main
        # Test-only patch to keep real host-owned lock files out of the
        # developer account. Production root is never config-controlled.
        self.host_lock_patch = patch.object(
            service, "_host_state_home", return_value=self.source.fixture.temp_root)
        self.host_lock_patch.start()
        self.addCleanup(self.host_lock_patch.stop)
        self.config = self.source.fixture.temp_root / "approved-issues.json"
        self.plan = {
            "schema_version": 1,
            "project_root": str(self.source.project),
            "repository": "acme/widgets",
            "runtime_home": str(self.source.runtime),
            "opencode_bin": str(self.source.bin),
            "model": {"provider_id": "openai", "model_id": "gpt-6-sol"},
            "tasks": [{
                "issue": 291,
                "branch_ref": self.source.branch_ref,
                "base_revision": self.source.base,
                "contract_id": self.source.pub.contract_id,
                "port": self.source.ports[291],
            }],
        }
        self.save_plan()

    def save_plan(self):
        self.config.write_text(json.dumps(self.plan) + "\n", encoding="utf-8")

    def test_singleton_blocks_second_controller_then_restart_rejoins(self):
        with service.MainSupervisor(self.main) as first:
            with self.assertRaises(MainError) as second:
                with service.MainSupervisor(self.source.make_main()):
                    self.fail("Second active Main must be refused")
            self.assertEqual(second.exception.code, "MAIN_ALREADY_ACTIVE")
            accepted = first.cycle()
            self.assertEqual(accepted["tasks"][0]["prompt_state"], "SUBMITTED")
            again = first.cycle()
            self.assertEqual(again["tasks"][0]["prompt_state"], "ALREADY_DISPATCHED")
            self.assertEqual(len(self.source.prompts()), 1)
        with service.MainSupervisor(self.source.make_main()) as restarted:
            recovered = restarted.cycle()
            self.assertEqual(recovered["tasks"][0]["prompt_state"],
                             "ALREADY_DISPATCHED")
            inspected = restarted.cycle(dispatch=False)
            self.assertEqual(inspected["mode"], "observe")
            self.assertEqual(inspected["tasks"][0]["observation"]["kickoff"], "SENT")
            self.assertEqual(len(self.source.prompts()), 1)

    def test_singleton_is_enforced_across_independent_processes(self):
        child = """
import sys
from pathlib import Path
from types import SimpleNamespace
from tools.kagari_vnext_main_service import MainSupervisor, MainError
source=SimpleNamespace(store=SimpleNamespace(
    root=Path(sys.argv[1]), repository="acme/widgets"),approved={})
import tools.kagari_vnext_main_service as service
service._host_state_home=lambda:Path(sys.argv[2])
try:
    with MainSupervisor(source):
        print("ACQUIRED")
except MainError as e:
    print(e.code)
"""
        def run_child():
            result=subprocess.run(
                [sys.executable, "-B", "-c", child,
                 str(self.source.project), str(self.source.fixture.temp_root)], text=True,
                capture_output=True, timeout=12,
                cwd=Path(__file__).resolve().parents[1], check=True)
            return result.stdout.strip()
        with service.MainSupervisor(self.main):
            self.assertEqual(run_child(), "MAIN_ALREADY_ACTIVE")
        self.assertEqual(run_child(), "ACQUIRED")

    def test_one_repository_lock_excludes_distinct_clones_not_other_repositories(self):
        from types import SimpleNamespace
        second = self.source.fixture.temp_root / "same-repo-other-clone"
        third = self.source.fixture.temp_root / "unrelated-repo"
        for root in (second, third):
            subprocess.run(["git", "init", "-q", str(root)], check=True)
        def fake(root, repo):
            return SimpleNamespace(store=SimpleNamespace(root=root,repository=repo),
                                   approved={})
        with service.MainSupervisor(self.main):
            with self.assertRaises(MainError) as conflict:
                with service.MainSupervisor(fake(second,"ACME/Widgets")):
                    pass
            self.assertEqual(conflict.exception.code,"MAIN_ALREADY_ACTIVE")
            with service.MainSupervisor(fake(third,"acme/other")):
                pass
        with service.MainSupervisor(fake(second,"acme/widgets")):
            pass

    def test_unsafe_host_lock_namespace_refuses_without_model(self):
        other = self.source.fixture.temp_root / "symlinked-main-state"
        target = self.source.fixture.temp_root / "some-real-state"
        target.mkdir()
        other.symlink_to(target,target_is_directory=True)
        with patch.object(service,"_host_state_home",return_value=other):
            with self.assertRaises(MainError) as err:
                with service.MainSupervisor(self.main):
                    pass
        self.assertEqual(err.exception.code,"MAIN_LOCK_NAMESPACE_UNSAFE")
        self.assertEqual(len(self.source.prompts()),0)

    def test_direct_dispatch_refused_without_supervisor_even_with_task_grants(self):
        # #191 approved Issue/Contract and a valid #194 test fixture do not
        # confer repository Main ownership across distinct clones.
        with self.assertRaises(MainError) as error:
            self.main.dispatch(291)
        self.assertEqual(error.exception.code,"MAIN_OWNER_REQUIRED")
        self.assertFalse((self.source.project/".worktrees").exists())
        self.assertFalse(self.source.runtime.exists())
        self.assertEqual(len(self.source.prompts()),0)

    def test_direct_dispatch_other_clone_denied_while_valid_main_runs(self):
        # Real separate local Git clone: independent .git/common-dir, but
        # the *same canonical repository* and immutable approved Issue.
        clone=self.source.fixture.temp_root/"same-repo-genuine-clone"
        subprocess.run(["git","clone","-q",str(self.source.fixture.remote_path),
                        str(clone)],capture_output=True,check=True)
        other_store=MetadataStore(clone,"acme/widgets","origin")
        other=RepositoryMain(
            other_store, approved=self.source.approved,
            read_issue=lambda n:self.source.facts[n].copy(),
            runtime_home=self.source.fixture.temp_root/"other-host-task-runtime",
            port_for_issue=self.source.ports.__getitem__,
            opencode_bin=str(self.source.bin),
            initial_collaboration_for_issue=lambda _issue,_worktree:None)
        with service.MainSupervisor(self.main) as first:
            self.assertIs(first.main,self.main)
            with self.assertRaises(MainError) as naked:
                other.dispatch(291)
            self.assertEqual(naked.exception.code,"MAIN_OWNER_REQUIRED")
            with self.assertRaises(MainError) as second:
                with service.MainSupervisor(other):
                    pass
            self.assertEqual(second.exception.code,"MAIN_ALREADY_ACTIVE")
            with self.assertRaises(MainError) as unrelated:
                self.source.make_main().dispatch(291)
            self.assertEqual(unrelated.exception.code,"MAIN_OWNER_REQUIRED")
            self.assertFalse((clone/".worktrees").exists())
            self.assertFalse(other.runtime_home.exists())
            self.assertEqual(len(self.source.prompts()),0)
        # Existing Main ownership was released. The same Task can still be
        # safely dispatched by a verified restarted Main on its own checkout.
        with service.MainSupervisor(self.main) as resumed:
            self.assertEqual(resumed.cycle()["tasks"][0]["prompt_state"],"SUBMITTED")
        self.assertEqual(len(self.source.prompts()),1)

    def test_supervisor_exit_revokes_direct_dispatch_and_observe_stays_free(self):
        with service.MainSupervisor(self.main,observe_only=True) as reader:
            inspected=reader.cycle(dispatch=False)
            self.assertEqual(inspected["mode"],"observe")
            with self.assertRaises(MainError) as denied:
                self.main.dispatch(291)
            self.assertEqual(denied.exception.code,"MAIN_OWNER_REQUIRED")
        with service.MainSupervisor(self.main) as owned:
            self.assertEqual(owned.cycle()["tasks"][0]["prompt_state"],"SUBMITTED")
        with self.assertRaises(MainError) as after:
            self.main.dispatch(291)
        self.assertEqual(after.exception.code,"MAIN_OWNER_REQUIRED")
        self.assertEqual(len(self.source.prompts()),1)
        self.assertEqual(self.main.observe(291)["kickoff"],"SENT")

    def test_replaced_or_unlocked_owner_inode_refuses_direct_dispatch(self):
        with service.MainSupervisor(self.main) as runner:
            path=runner._lock_path
            self.assertIsNotNone(path)
            # Even an externally unlocked descriptor cannot continue under a
            # stale private Main session flag.
            import fcntl
            fcntl.flock(runner._file, fcntl.LOCK_UN)
            # Checking the original lock via a separate descriptor would
            # permit acquisition, so a stale Boolean/PID must not suffice.
            # Here replacing the visible path necessarily prevents a false
            # positive, even when the original descriptor remains open.
            relocated=path.with_suffix(".renamed")
            path.rename(relocated)
            with self.assertRaises(MainError) as denial:
                self.main.dispatch(291)
            self.assertEqual(denial.exception.code,"MAIN_OWNER_UNVERIFIED")
            self.assertEqual(len(self.source.prompts()),0)
        self.assertFalse((self.source.project/".worktrees").exists())

    def test_fork_child_cannot_unlock_the_parent_owned_repo_lock(self):
        # This is a *real Linux fork*, not a mocked subprocess. File-descrip-
        # tion flock ownership survives fork; a child LOCK_UN is catastrophic.
        from types import SimpleNamespace
        other_root=self.source.fixture.temp_root/"fork-rival-clone"
        subprocess.run(["git","init","-q",str(other_root)],check=True)
        rival=SimpleNamespace(store=SimpleNamespace(
            root=other_root,repository="acme/widgets"),approved={})
        with service.MainSupervisor(self.main) as parent:
            pid=os.fork()
            if pid==0:
                try:
                    parent.__exit__(None,None,None)
                except BaseException:
                    os._exit(71)
                os._exit(0)
            child,status=os.waitpid(pid,0)
            self.assertEqual(child,pid)
            self.assertEqual(os.waitstatus_to_exitcode(status),0)
            parent._assert_owned(self.main)
            with self.assertRaises(MainError) as blocked:
                with service.MainSupervisor(rival):
                    self.fail("Fork child must never unlock parent Main")
            self.assertEqual(blocked.exception.code,"MAIN_ALREADY_ACTIVE")
            self.assertEqual(len(self.source.prompts()),0)
        with service.MainSupervisor(rival):
            pass

    def test_forked_child_cannot_use_inherited_main_to_dispatch(self):
        # A copy of a Main object is not an owner. This tests the live PID
        # validation rather than merely the advisory lock's presence.
        with service.MainSupervisor(self.main) as parent:
            pid=os.fork()
            if pid==0:
                try:
                    self.main.dispatch(291)
                except MainError as e:
                    os._exit(0 if e.code=="MAIN_OWNER_REQUIRED" else 72)
                except BaseException:
                    os._exit(73)
                os._exit(74)
            child,status=os.waitpid(pid,0)
            self.assertEqual(child,pid)
            self.assertEqual(os.waitstatus_to_exitcode(status),0)
            self.assertEqual(parent.cycle()["tasks"][0]["prompt_state"],"SUBMITTED")
        self.assertEqual(len(self.source.prompts()),1)

    def test_one_issue_blocker_does_not_start_any_unapproved_issues(self):
        # A second Issue with a bad/missing Record may be in the explicit
        # list, but no Task/Worktree/Session can be created for it.
        self.source.approved[292] = ApprovedTask(
            292, "refs/heads/issue-292", self.source.base, "a" * 64)
        combined = self.source.make_main()
        with service.MainSupervisor(combined) as runner:
            result = runner.cycle()
        self.assertEqual([x["issue"] for x in result["tasks"]], [291, 292])
        self.assertEqual(result["tasks"][0]["prompt_state"], "SUBMITTED")
        self.assertEqual(result["tasks"][1]["status"], "blocked")
        self.assertEqual(result["tasks"][1]["reason"], "TASK_RECORD_ABSENT")
        self.assertFalse((self.source.project / ".worktrees" / "292").exists())

    def test_unapproved_issue_rejected_before_any_lock(self):
        with self.assertRaises(MainError) as exc:
            self.main.dispatch(292)
        self.assertEqual(exc.exception.code, "ISSUE_NOT_APPROVED")
        self.assertFalse((self.source.project / ".git" /
                          "kagari-repository-main.lock").exists())

    def test_optional_pr_and_evidence_are_explicit_observation_requests(self):
        self.plan["tasks"][0]["pr_number"]=99
        evidence_ref={"evidence_id":"a"*64,"subject":self.source.base}
        self.plan["tasks"][0]["evidence"]=[evidence_ref]
        self.save_plan()
        parsed=service.load_selection(self.config)
        with patch.object(service,"github_issue_reader",
                          return_value=lambda n:self.source.facts[n].copy()):
            with patch.object(service,"github_pr_reader",
                              return_value=lambda _request:None) as reader:
                bound=service.build_main(parsed)
        self.assertEqual(bound.pr_for_issue,{291:99})
        self.assertEqual(bound.evidence_for_issue[291][0].evidence_id,
                         evidence_ref["evidence_id"])
        reader.assert_called_once_with("acme/widgets",gh="gh")
        for invalid in (0,"99",True):
            self.plan["tasks"][0]["pr_number"]=invalid
            self.save_plan()
            with self.assertRaises(MainError):
                service.load_selection(self.config)
        self.plan["tasks"][0]["pr_number"]=99
        self.plan["tasks"][0]["evidence"]=[
            {"evidence_id":"bogus","subject":self.source.base}]
        self.save_plan()
        with self.assertRaises(MainError):
            service.load_selection(self.config)

    def test_selection_closed_schema_and_port_uniqueness(self):
        plan = service.load_selection(self.config)
        self.assertEqual(plan["tasks"][0]["issue"], 291)
        self.plan["tasks"][0]["broad_authority"] = "grant_everything"
        self.save_plan()
        with self.assertRaises(MainError) as exc:
            service.load_selection(self.config)
        self.assertEqual(exc.exception.code, "MAIN_PLAN_INVALID")
        self.plan["tasks"][0].pop("broad_authority")
        self.plan["tasks"].append({
            **self.plan["tasks"][0], "issue": 292, "branch_ref": "refs/heads/issue-292"})
        self.save_plan()
        with self.assertRaises(MainError) as exc:
            service.load_selection(self.config)
        self.assertEqual(exc.exception.code, "MAIN_PLAN_INVALID")

    def test_config_loaded_runner_executes_only_frozen_task_without_job_ask(self):
        with patch.object(service, "github_issue_reader",
                          return_value=lambda n: self.source.facts[n].copy()):
            buf = StringIO()
            with redirect_stdout(buf):
                exit_status = service.main(["--config", str(self.config), "dispatch"])
        self.assertEqual(exit_status, 0, buf.getvalue())
        result = json.loads(buf.getvalue())
        self.assertEqual(result["repository"], "acme/widgets")
        self.assertEqual(len(result["tasks"]), 1)
        self.assertEqual(result["tasks"][0]["issue"], 291)
        self.assertEqual(result["tasks"][0]["status"], "blocked")
        self.assertEqual(result["tasks"][0]["reason"],
                         "MAIN_INITIAL_COLLABORATION_REQUIRED")
        self.assertEqual(len(self.source.prompts()), 0)

        # The unconfigured CLI cannot mint #194 GitHub authority itself.
        with patch.object(service, "github_issue_reader",
                          return_value=lambda n: self.source.facts[n].copy()):
            buf = StringIO()
            with redirect_stdout(buf):
                exit_status = service.main(["--config", str(self.config), "dispatch"])
        self.assertEqual(exit_status, 0, buf.getvalue())
        result = json.loads(buf.getvalue())
        self.assertEqual(result["tasks"][0]["status"], "blocked")
        self.assertEqual(result["tasks"][0]["reason"],
                         "MAIN_INITIAL_COLLABORATION_REQUIRED")
        self.assertEqual(len(self.source.prompts()), 0)

    def test_packaged_main_observe_if_supplied(self):
        packaged=os.getenv("KAGARI_VNEXT_MAIN_PACKAGE")
        if not packaged:
            self.skipTest("KAGARI_VNEXT_MAIN_PACKAGE not supplied")
        executable=Path(packaged)/"bin/kagari-vnext-main"
        self.assertTrue(executable.is_file())
        # A hermetic fixture gh implementation. Only the approved local
        # Issue is ever read, and observe never starts a model Session.
        fake_gh=self.source.fixture.temp_root/"gh-fixture"
        body=json.dumps(self.source.facts[291],sort_keys=True)
        fake_gh.write_text("#!/bin/sh\nprintf '%s\\n' "
                           + shlex.quote(body) + "\n")
        fake_gh.chmod(0o700)
        self.plan["github_bin"]=str(fake_gh)
        self.save_plan()
        lock = self.source.project / ".git" / "kagari-repository-main.lock"
        fetch_head = self.source.project / ".git" / "FETCH_HEAD"
        fetch_head.write_bytes(b"original Product Git cache sentinel\\n")
        before_fetch = fetch_head.read_bytes()
        self.assertFalse(lock.exists())
        proc=subprocess.run(
            [str(executable),"--config",str(self.config),"observe"],
            text=True,capture_output=True,timeout=24,
            check=False,cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(proc.returncode,0,(proc.stdout,proc.stderr))
        result=json.loads(proc.stdout)
        self.assertEqual(result["tasks"][0]["observation"]["runtime"],
                         "NOT_STARTED")
        self.assertFalse(lock.exists())
        self.assertEqual(fetch_head.read_bytes(), before_fetch)
        self.assertEqual(len(self.source.prompts()),0)

    def test_packaged_main_explicit_pr_observation_if_supplied(self):
        packaged=os.getenv("KAGARI_VNEXT_MAIN_PACKAGE")
        if not packaged:
            self.skipTest("KAGARI_VNEXT_MAIN_PACKAGE not supplied")
        from tools.kagari_vnext_main_service import load_selection
        gh=self.source.fixture.temp_root/"gh-fixture-pr"
        issue=json.dumps(self.source.facts[291],sort_keys=True)
        pr=json.dumps({
            "number":93,"state":"OPEN","isDraft":True,
            "baseRefName":"main","headRefName":"issue-291",
            "headRefOid":self.source.base,
            "headRepository":{"nameWithOwner":"acme/widgets"},
            "isCrossRepository":False},sort_keys=True)
        gh.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = issue ]; then\n"
            "  printf '%s\\n' " + shlex.quote(issue) + "\n"
            "elif [ \"$1\" = pr ]; then\n"
            "  printf '%s\\n' " + shlex.quote(pr) + "\n"
            "else\n  exit 4\nfi\n")
        gh.chmod(0o700)
        self.plan["github_bin"]=str(gh)
        self.plan["tasks"][0]["pr_number"]=93
        self.save_plan()
        parsed=load_selection(self.config)
        self.assertEqual(parsed["tasks"][0]["pr_number"],93)
        result=subprocess.run(
            [str(Path(packaged)/"bin/kagari-vnext-main"),
             "--config",str(self.config),"observe"],
            text=True,capture_output=True,timeout=24,check=False,
            cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode,0,(result.stdout,result.stderr))
        payload=json.loads(result.stdout)
        fact=payload["tasks"][0]["observation"]["task_view"]["github"]
        self.assertEqual(fact["state"],"observed",fact)
        self.assertEqual(fact["pull_request"]["number"],93)
        self.assertEqual(fact["pull_request"]["head_ref"],"issue-291")
        self.assertFalse((self.source.project/".worktrees").exists())
        self.assertEqual(len(self.source.prompts()),0)

    def test_packaged_foreground_main_follow_graceful_sigterm_if_supplied(self):
        packaged=os.getenv("KAGARI_VNEXT_MAIN_PACKAGE")
        if not packaged:
            self.skipTest("KAGARI_VNEXT_MAIN_PACKAGE not supplied")
        executable=Path(packaged)/"bin/kagari-vnext-main"
        fake_gh=self.source.fixture.temp_root/"gh-fixture-follow"
        body=json.dumps(self.source.facts[291],sort_keys=True)
        fake_gh.write_text("#!/bin/sh\nprintf '%s\\n' "
                           + shlex.quote(body) + "\n")
        fake_gh.chmod(0o700)
        self.plan["github_bin"]=str(fake_gh)
        self.save_plan()
        proc=subprocess.Popen(
            [str(executable),"--config",str(self.config),
             "--follow","--interval","2","observe"],
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,text=True,
            cwd=Path(__file__).resolve().parents[1])
        try:
            ready,_,_=select.select([proc.stdout],[],[],12)
            self.assertTrue(ready,"Foreground Main did not emit an observation")
            first_line=proc.stdout.readline()
            result=json.loads(first_line)
            self.assertEqual(result["mode"],"observe")
            self.assertEqual(result["tasks"][0]["observation"]["runtime"],
                             "NOT_STARTED")
            proc.terminate()
            tail,errors=proc.communicate(timeout=8)
            self.assertEqual(proc.returncode,0,errors)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=8)
        with service.MainSupervisor(self.main):
            self.assertEqual(len(self.source.prompts()),0)
        self.assertFalse((self.source.project/".worktrees").exists())

    def test_read_only_observe_never_writes_original_fetch_head(self):
        fetch_head = self.source.project / ".git" / "FETCH_HEAD"
        fetch_head.write_bytes(b"original user Git FETCH_HEAD remains untouched\\n")
        original = fetch_head.read_bytes()
        mtime = fetch_head.stat().st_mtime_ns
        before = git_fixtures.git(
            "status", "--porcelain=v1", cwd=self.source.project)
        result = self.main.observe(291)
        self.assertEqual(result["runtime"], "NOT_STARTED")
        self.assertEqual(fetch_head.read_bytes(), original)
        self.assertEqual(fetch_head.stat().st_mtime_ns, mtime)
        self.assertEqual(git_fixtures.git(
            "status", "--porcelain=v1", cwd=self.source.project), before)

        # The same invariant must hold after #163 has a live root Session:
        # #292 resolve() previously fetched metadata back into Product Git.
        with service.MainSupervisor(self.main):
            self.main.dispatch(291)
        fetch_head.write_bytes(b"second local Git FETCH_HEAD sentinel\\n")
        original = fetch_head.read_bytes()
        mtime = fetch_head.stat().st_mtime_ns
        during = self.main.observe(291)
        self.assertEqual(during["kickoff"], "SENT")
        self.assertEqual(during["runtime"]["task"]["issue"], 291)
        self.assertEqual(fetch_head.read_bytes(), original)
        self.assertEqual(fetch_head.stat().st_mtime_ns, mtime)

    def test_isolated_observer_relative_direct_remote_uses_project_origin(self):
        # The #217 MetadataStore contract accepts ../local-bare.git remotes,
        # resolved relative to the real Product root, NOT the scratch Git
        # directory created for a read-only observation.
        relative = os.path.relpath(
            self.source.fixture.remote_path, self.source.project)
        self.assertTrue(relative.startswith("../"), relative)
        store = MetadataStore(self.source.project, "acme/widgets", relative)
        observer = RepositoryMain(
            store, approved=self.source.approved,
            read_issue=lambda n: self.source.facts[n].copy(),
            runtime_home=self.source.runtime,
            port_for_issue=self.source.ports.__getitem__,
            opencode_bin=str(self.source.bin))
        original_fetch = self.source.project / ".git" / "FETCH_HEAD"
        original_fetch.write_bytes(b"KEEP-GIT-FETCH-HEAD-UNCHANGED\n")
        before = original_fetch.read_bytes()
        observed = observer.observe(291)
        self.assertEqual(observed["runtime"], "NOT_STARTED")
        self.assertEqual(
            observed["approved_task"]["contract_id"],
            self.source.pub.contract_id)
        self.assertEqual(original_fetch.read_bytes(), before)
        self.assertEqual(len(self.source.prompts()), 0)

    def test_isolated_observer_named_remote_preserves_product_fetch_head(self):
        store = MetadataStore(self.source.project, "acme/widgets", "origin")
        observer = RepositoryMain(
            store, approved=self.source.approved,
            read_issue=lambda n: self.source.facts[n].copy(),
            runtime_home=self.source.runtime,
            port_for_issue=self.source.ports.__getitem__,
            opencode_bin=str(self.source.bin))
        fetch_head = self.source.project / ".git" / "FETCH_HEAD"
        fetch_head.write_bytes(b"KEEP-NAMED-REMOTE-FETCH-HEAD\n")
        old_bytes = fetch_head.read_bytes()
        old_mtime = fetch_head.stat().st_mtime_ns
        observed = observer.observe(291)
        self.assertEqual(observed["runtime"], "NOT_STARTED")
        self.assertEqual(
            observed["approved_task"]["contract_id"],
            self.source.pub.contract_id)
        self.assertEqual(fetch_head.read_bytes(), old_bytes)
        self.assertEqual(fetch_head.stat().st_mtime_ns, old_mtime)

    def test_isolated_observer_rejects_ambiguous_named_remote_urls(self):
        original_fetch = self.source.project / ".git" / "FETCH_HEAD"
        original_fetch.write_bytes(b"KEEP-AMBIGUOUS-REMOTE-FETCH-HEAD\n")
        before = original_fetch.read_bytes()
        git_fixtures.git(
            "config", "--local", "--add", "remote.origin.url",
            str(self.source.fixture.remote_path),
            cwd=self.source.project)
        with self.assertRaises(MainError) as error:
            self.main.observe(291)
        self.assertEqual(error.exception.code, "TASK_VIEW_UNAVAILABLE")
        self.assertEqual(original_fetch.read_bytes(), before)
        self.assertFalse(
            (self.source.project / ".worktrees").exists())
        self.assertEqual(len(self.source.prompts()), 0)

    def test_isolated_observer_rejects_empty_second_named_remote_url(self):
        # With plain text get-all plus .strip(), this second empty value
        # disappears and the apparently-single URL would be accepted.
        git_fixtures.git(
            "config", "--local", "--add", "remote.origin.url", "",
            cwd=self.source.project)
        head = self.source.project / ".git" / "FETCH_HEAD"
        head.write_bytes(b"RETAIN-EMPTY-REMOTE-GUARD\n")
        before = head.read_bytes()
        with self.assertRaises(MainError) as error:
            self.main.observe(291)
        self.assertEqual(error.exception.code, "TASK_VIEW_UNAVAILABLE")
        self.assertEqual(head.read_bytes(), before)
        self.assertEqual(len(self.source.prompts()), 0)

    def test_isolated_observer_rejects_whitespace_named_remote_url(self):
        git_fixtures.git(
            "config", "--local", "--replace-all", "remote.origin.url",
            " " + str(self.source.fixture.remote_path),
            cwd=self.source.project)
        head = self.source.project / ".git" / "FETCH_HEAD"
        head.write_bytes(b"RETAIN-WHITESPACE-REMOTE-GUARD\n")
        before = head.read_bytes()
        with self.assertRaises(MainError) as error:
            self.main.observe(291)
        self.assertEqual(error.exception.code, "TASK_VIEW_UNAVAILABLE")
        self.assertEqual(head.read_bytes(), before)
        self.assertEqual(len(self.source.prompts()), 0)

    def test_isolated_observer_preserves_original_index_refs_and_objects(self):
        project = self.source.project
        gitdir = project / ".git"
        fetch_head = gitdir / "FETCH_HEAD"
        fetch_head.write_bytes(b"ORIGINAL-PRODUCT-GIT-STATE-SENTINEL\n")

        def snapshot():
            index = gitdir / "index"
            return {
                "head": (gitdir / "HEAD").read_bytes(),
                "index": index.read_bytes(),
                "index_mtime_ns": index.stat().st_mtime_ns,
                "fetch_head": fetch_head.read_bytes(),
                "fetch_head_mtime_ns": fetch_head.stat().st_mtime_ns,
                "refs": git_fixtures.git(
                    "for-each-ref", "--format=%(refname) %(objectname)",
                    cwd=project),
                "worktrees": git_fixtures.git(
                    "worktree", "list", "--porcelain", cwd=project),
                "object_files": {
                    p.relative_to(gitdir).as_posix(): (
                        p.stat().st_size, p.stat().st_mtime_ns)
                    for p in (gitdir / "objects").rglob("*") if p.is_file()
                },
            }

        original = snapshot()
        observed = self.main.observe(291)
        self.assertEqual(observed["runtime"], "NOT_STARTED")
        self.assertEqual(snapshot(), original)
        self.assertFalse((project / ".worktrees").exists())

    def test_read_only_unavailable_session_blocks_one_issue_not_main(self):
        from tools import kagari_vnext_main as main_module
        with service.MainSupervisor(self.main):
            self.main.dispatch(291)
        self.assertEqual(len(self.source.prompts()), 1)
        with patch.object(
            main_module.OpenCode, "verify_session",
            side_effect=main_module.ControlError(
                "UPSTREAM_UNAVAILABLE", "synthetic unreachable root Session"),
        ):
            with service.MainSupervisor(self.main, observe_only=True) as main:
                cycle = main.cycle(dispatch=False)
                self.assertEqual(cycle["tasks"][0]["status"], "blocked")
                self.assertEqual(
                    cycle["tasks"][0]["reason"],
                    "SESSION_OBSERVATION_UNAVAILABLE")
        # An observation problem must not re-prompt or terminate OpenCode.
        self.assertEqual(len(self.source.prompts()), 1)

    def test_read_only_cycle_does_not_start_any_issue(self):
        lock = self.source.project / ".git" / "kagari-repository-main.lock"
        self.assertFalse(lock.exists())
        with service.MainSupervisor(self.main, observe_only=True) as runner:
            result = runner.cycle(dispatch=False)
            with self.assertRaises(MainError) as err:
                runner.cycle(dispatch=True)
            self.assertEqual(err.exception.code, "MAIN_OBSERVE_ONLY")
        self.assertFalse(lock.exists())
        self.assertEqual(result["tasks"][0]["observation"]["runtime"],
                         "NOT_STARTED")
        self.assertEqual(result["tasks"][0]["observation"]["kickoff"],
                         "NOT_SENT")
        self.assertEqual(len(self.source.prompts()), 0)
        self.assertFalse((self.source.project / ".worktrees").exists())


if __name__ == "__main__":
    unittest.main()
