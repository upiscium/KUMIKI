"""#291 Main one-Issue scoped orchestration integration, using real local Git.

Mock only the OpenCode HTTP/model process and external GitHub Issue reader.
#191 metadata, #286 linked Worktree, #163 launcher and #292 bound CLI all
execute their actual implementation; no production GitHub mutation.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_metadata_ref as git_fixture

V4 = Path(__file__).resolve().parents[1] / "components/agent-core-v4"
sys.path.insert(0, str(V4))
from task_record import TaskRecords
from issue_worktree import IssueWorktreeAllocator
from tools.kagari_vnext_issue_bootstrap import (
    ApprovedIssueSnapshot, bootstrap_approved_issue)
from tools.kagari_vnext_main import (
    ApprovedTask, RepositoryMain, MainError, github_issue_reader)
from task_collaboration import CollaborationReceipt
from tools import kagari_vnext_session as launch


FAKE_OPENCODE = r"""#!/usr/bin/env python3
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit
if "--version" in sys.argv:
    print("1.18.35")
    sys.exit(0)
if sys.argv[1] != "serve" or "--port" not in sys.argv:
    sys.exit(11)
port=int(sys.argv[sys.argv.index("--port")+1])
cwd=os.getcwd()
sid="ses_MAIN" + str(port) + "DemoSession012345"
per="per_MAIN" + str(port) + "Permission012345"
logs=Path(os.environ["KAGARI_TEST_PROMPT_LOG"])
class Handler(BaseHTTPRequestHandler):
    prompted=False
    user_parts=None
    def log_message(self,*_): pass
    def send(self,code,value):
        if code==204:
            self.send_response(204)
            self.end_headers()
            return
        body=json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def route(self):
        url=urlsplit(self.path)
        query=parse_qs(url.query)
        if url.path!="/global/health" and query.get("directory")!=[cwd]:
            return ""
        return url.path
    def do_GET(self):
        path=self.route()
        if path=="/global/health":
            return self.send(200,{"healthy":True,"version":"1.18.35"})
        if path=="/session/"+sid:
            return self.send(200,{"id":sid,"directory":cwd})
        if path=="/session/status":
            return self.send(200,{sid:{"type":"busy"}} if type(self).prompted else {})
        if path=="/session/"+sid+"/message":
            if type(self).user_parts is None:
                return self.send(200,[])
            return self.send(200,[{"info":{"role":"user"},
                                   "parts":type(self).user_parts}])
        if path=="/permission":
            return self.send(200,[{"id":per,"sessionID":sid,
                "permission":"bash","patterns":["pwd"],"always":["pwd *"],
                "metadata":{"command":"pwd"}}] if type(self).prompted else [])
        if path=="/question":
            return self.send(200,[])
        return self.send(404,{"error":"not found"})
    def do_POST(self):
        path=self.route()
        data=self.rfile.read(int(self.headers.get("Content-Length","0")))
        body=json.loads(data)
        if path=="/session":
            if body.get("title") is None:
                return self.send(400,{"error":"title required"})
            return self.send(200,{"id":sid,"directory":cwd})
        if path=="/session/"+sid+"/prompt_async":
            if body.get("agent")!="build" or not body.get("parts"):
                return self.send(400,{"error":"bad prompt"})
            with logs.open("a",encoding="utf-8") as f:
                f.write(json.dumps({"port":port,"body":body,"cwd":cwd})+"\n")
            type(self).prompted=True
            type(self).user_parts=[
                {**part, "id": "prt_mock_native_001",
                 "messageID": "msg_mock_native_001", "sessionID": sid}
                for part in body["parts"]]
            return self.send(204,None)
        return self.send(404,{"error":"not found"})
ThreadingHTTPServer(("127.0.0.1",port),Handler).serve_forever()
"""


def next_port() -> int:
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]


class MainOrchestratorTest(unittest.TestCase):
    def setUp(self):
        self.fixture=git_fixture.MetadataRefTest("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.stop_children)
        self.project=self.fixture.product
        self.store=self.fixture.store
        (self.project/".gitignore").write_text("/.worktrees/\n")
        git_fixture.git("add",".gitignore",cwd=self.project)
        git_fixture.git("commit","-q","-m","ignore Issue worktree namespace",cwd=self.project)
        self.base=git_fixture.git("rev-parse","HEAD",cwd=self.project).decode().strip()
        self.writer=TaskRecords(self.store,authorize=lambda _req:True)
        self.issue=291
        self.branch_ref="refs/heads/issue-291"
        self.facts={
            291:{"repository":"acme/widgets","number":291,"state":"OPEN",
                 "title":"Implement bounded Main","body":"Demonstrate issue-specific Task execution."}
        }
        self.pub=bootstrap_approved_issue(
            self.store,
            ApprovedIssueSnapshot(
                repository="acme/widgets",issue=291,
                branch_ref=self.branch_ref,base_revision=self.base,
                title=self.facts[291]["title"],body=self.facts[291]["body"]),
            read_issue=lambda n:self.facts[n].copy(),
            authorize_create=lambda req: (
                req.operation=="create" and req.task=="291"
                and req.repository=="acme/widgets"
                and req.branch_ref==self.branch_ref
                and req.base_revision==self.base))
        self.bin=self.fixture.temp_root/"fake-opencode"
        self.bin.write_text(FAKE_OPENCODE)
        self.bin.chmod(0o700)
        self.log=self.fixture.temp_root/"main-prompts.jsonl"
        self.env=patch.dict(os.environ,{"KAGARI_TEST_PROMPT_LOG":str(self.log)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.ports={291:next_port()}
        self.approved={291:ApprovedTask(291,self.branch_ref,self.base,self.pub.contract_id)}
        self.runtime=self.fixture.temp_root/"host-owned-run"
        # These existing tests exercise the #163 root-Session transport and
        # model deduplication. Their exact #194 dependency is supplied as an
        # already-confirmed fixture receipt; separate tests exercise the real
        # PR-first facade and missing/denied capability in production code.
        def fixture_preflight(_main, choice, worktree):
            subject = git_fixture.git("rev-parse", "HEAD", cwd=worktree).decode().strip()
            return CollaborationReceipt(
                repository="acme/widgets", task=str(choice.issue),
                branch_ref=choice.branch_ref, subject=subject,
                pull_request=91, comment_id=201,
                metadata_commit=self.pub.metadata_commit,
                record_id=self.pub.record_id,
                contract_id=choice.contract_id,
                snapshot_id="f" * 64,
            )
        self.initial_fixture_patch=patch.object(
            RepositoryMain,"_establish_initial_collaboration",fixture_preflight)
        self.initial_fixture_patch.start()
        self.addCleanup(self.initial_fixture_patch.stop)
        self.main=self.make_main()

    def stop_children(self):
        # This dictionary contains only subprocesses started by these tests.
        for pid,proc in list(launch._DETACHED_CHILDREN.items()):
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
                proc.wait(timeout=5)
            launch._DETACHED_CHILDREN.pop(pid,None)

    def make_main(self) -> RepositoryMain:
        return RepositoryMain(
            self.store,approved=self.approved,
            read_issue=lambda n:self.facts[n].copy(),
            runtime_home=self.runtime,
            port_for_issue=lambda n:self.ports[n],
            opencode_bin=str(self.bin),model=("openai","gpt-6-sol"),
            initial_collaboration_for_issue=lambda issue, worktree: None)

    def dispatch(self, main: RepositoryMain, issue: int):
        """Exercise real supervisor ownership; no test-owned host lock leaks."""
        from tools import kagari_vnext_main_service as service
        with patch.object(service, "_host_state_home",
                          return_value=self.fixture.temp_root):
            with service.MainSupervisor(main):
                return main.dispatch(issue)

    def prompts(self):
        if not self.log.exists():return []
        return [json.loads(x) for x in self.log.read_text().splitlines() if x]

    def test_single_issue_full_handoff_resume_and_task_view(self):
        first=self.dispatch(self.main,291)
        self.assertEqual(first["status"],"ok")
        self.assertEqual(first["worktree_state"],"CREATED")
        self.assertEqual(first["session_state"],"CREATED")
        self.assertEqual(first["prompt_state"],"SUBMITTED")
        self.assertEqual(first["runtime"]["pending_asks"],1)
        self.assertEqual(len(self.prompts()),1)
        self.assertIn(self.facts[291]["body"],
                      self.prompts()[0]["body"]["parts"][0]["text"])
        self.assertIn(self.pub.contract_id,
                      self.prompts()[0]["body"]["parts"][0]["text"])
        self.assertIn(
            "Do not ask a separate Question before routine tool calls",
            self.prompts()[0]["body"]["parts"][0]["text"])
        self.assertTrue((self.project/".worktrees"/"291").is_dir())
        second=self.dispatch(self.main,291)
        self.assertEqual(second["worktree_state"],"RESUMED")
        self.assertEqual(second["session_state"],"RESUMED")
        self.assertEqual(second["prompt_state"],"ALREADY_DISPATCHED")
        self.assertEqual(first["context"],second["context"])
        self.assertEqual(len(self.prompts()),1)
        view=self.main.observe(291)
        self.assertEqual(view["kickoff"],"SENT")
        self.assertEqual(view["runtime"]["task"]["issue"],291)
        self.assertEqual(view["pending"][0]["kind"],"permission")
        self.assertIn("task_view",view)
        self.assertTrue(view["task_view"]["git"]["branch_matches_task"])
        self.assertEqual(view["task_view"]["git"]["head"],
                         git_fixture.git("rev-parse","HEAD",
                                         cwd=self.project/".worktrees"/"291").decode().strip())

    def test_concurrent_same_issue_start_serializes_without_duplicate_model(self):
        from tools import kagari_vnext_main_service as service
        with patch.object(service, "_host_state_home",
                          return_value=self.fixture.temp_root):
            with service.MainSupervisor(self.main):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results=list(pool.map(self.main.dispatch,[291,291]))
        states=sorted(r["prompt_state"] for r in results)
        self.assertEqual(states,["ALREADY_DISPATCHED","SUBMITTED"])
        self.assertEqual(len(self.prompts()),1)
        self.assertEqual(results[0]["context"],results[1]["context"])

    def test_inspect_before_approved_task_launch_is_read_only(self):
        result=self.main.observe(291)
        self.assertEqual(result["runtime"],"NOT_STARTED")
        self.assertEqual(result["approved_task"]["contract_id"],self.pub.contract_id)
        self.assertEqual(result["approved_task"]["title"],self.facts[291]["title"])
        self.assertIsInstance(result["task_view"],dict)
        self.assertFalse(result["task_view"]["git"]["branch_matches_task"])
        self.assertFalse((self.project/".worktrees").exists())
        self.assertFalse(self.runtime.exists())

    def test_task_view_is_available_after_worktree_but_before_session(self):
        created=IssueWorktreeAllocator(self.store).ensure(
            task="291",branch_ref=self.branch_ref,base_revision=self.base,
            expected_contract_id=self.pub.contract_id)
        self.assertEqual(created.status,"CREATED")
        before=self.main.observe(291)
        self.assertEqual(before["runtime"],"NOT_STARTED")
        self.assertEqual(before["kickoff"],"NOT_SENT")
        self.assertIsInstance(before["task_view"],dict)
        self.assertTrue(before["task_view"]["git"]["branch_matches_task"])
        self.assertEqual(before["approved_task"]["contract_id"],self.pub.contract_id)
        self.assertFalse(self.runtime.exists())

    def test_unapproved_issue_has_no_worktree_or_runtime(self):
        with self.assertRaises(MainError) as err:
            self.dispatch(self.main,292)
        self.assertEqual(err.exception.code,"ISSUE_NOT_APPROVED")
        self.assertFalse((self.project/".worktrees").exists())
        self.assertFalse(self.runtime.exists())

    def test_changed_live_issue_blocks_before_git_side_effect(self):
        self.facts[291]["body"]="a human edited Issue requirements after approval"
        with self.assertRaises(MainError) as err:
            self.dispatch(self.main,291)
        self.assertEqual(err.exception.code,"ISSUE_SNAPSHOT_DRIFT")
        self.assertFalse((self.project/".worktrees").exists())
        self.assertFalse(self.runtime.exists())

    def test_reauthorized_record_refuses_stale_approved_task(self):
        self.writer.reauthorize(
            task="291",branch_ref=self.branch_ref,base_revision=self.base,
            expected_record_id=self.pub.record_id,
            title="New human-approved requirements",
            body="A different scope was subsequently approved.")
        with self.assertRaises(MainError) as err:
            self.dispatch(self.main,291)
        self.assertEqual(err.exception.code,"TASK_CONTRACT_CHANGED")
        self.assertFalse((self.project/".worktrees").exists())

    def test_lost_kickoff_ack_is_never_automatically_resent(self):
        from tools import kagari_vnext_main as module
        with patch.object(module,"opencode_request",
                          side_effect=module.ControlError("OPENCODE_UNAVAILABLE",
                                                          "simulated lost ack")):
            with self.assertRaises(MainError) as err:
                self.dispatch(self.main,291)
        self.assertEqual(err.exception.code,"OPENCODE_UNAVAILABLE")
        self.assertTrue((self.runtime/"issue-291"/"prompt.pending.json").exists())
        with self.assertRaises(MainError) as again:
            self.dispatch(self.main,291)
        self.assertEqual(again.exception.code,"PROMPT_OUTCOME_UNCERTAIN")
        self.assertEqual(len(self.prompts()),0)

    def test_stalled_dispatch_lock_has_bounded_wait_and_no_task_effects(self):
        from tools import kagari_vnext_main as module
        lock=self.source_lock_path()
        lock.parent.mkdir(parents=True,exist_ok=True)
        with open(lock,"a+b") as held:
            fcntl.flock(held,fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with patch.object(module,"DISPATCH_LOCK_TIMEOUT_SECONDS",0.16):
                    with self.assertRaises(MainError) as error:
                        self.dispatch(self.main,291)
                self.assertEqual(error.exception.code,"MAIN_DISPATCH_BUSY")
                self.assertFalse((self.project/".worktrees").exists())
                self.assertFalse(self.runtime.exists())
            finally:
                fcntl.flock(held,fcntl.LOCK_UN)

    def source_lock_path(self):
        return self.project/".git"/"kagari-main-issue-dispatch.lock"

    def test_lost_ack_after_upstream_acceptance_recovers_without_resend(self):
        from tools import kagari_vnext_main as module
        send=module.opencode_request

        def accepted_then_response_lost(url, *, payload=None):
            response=send(url,payload=payload)
            if "/prompt_async" in url:
                raise module.ControlError("OPENCODE_UNAVAILABLE",
                                          "synthetic HTTP ACK loss")
            return response

        with patch.object(module,"opencode_request",
                          side_effect=accepted_then_response_lost):
            with self.assertRaises(MainError) as first:
                self.dispatch(self.main,291)
        self.assertEqual(first.exception.code,"OPENCODE_UNAVAILABLE")
        self.assertEqual(len(self.prompts()),1)
        self.assertTrue((self.runtime/"issue-291"/"prompt.pending.json").exists())
        recovered=self.dispatch(self.main,291)
        self.assertEqual(recovered["prompt_state"],"RECOVERED_SUBMITTED")
        self.assertFalse((self.runtime/"issue-291"/"prompt.pending.json").exists())
        self.assertTrue((self.runtime/"issue-291"/"prompt.sent.json").exists())
        self.assertEqual(len(self.prompts()),1)
        again=self.dispatch(self.main,291)
        self.assertEqual(again["prompt_state"],"ALREADY_DISPATCHED")
        self.assertEqual(len(self.prompts()),1)

    def test_two_separate_eligible_issues_use_different_worktrees_and_servers(self):
        self.facts[292]={
            "repository":"acme/widgets","number":292,"state":"OPEN",
            "title":"Second approved Issue","body":"A disjoint independent Issue."}
        second=self.writer.create(
            task="292",branch_ref="refs/heads/issue-292",
            base_revision=self.base,
            title=self.facts[292]["title"],body=self.facts[292]["body"])
        self.approved[292]=ApprovedTask(
            292,"refs/heads/issue-292",self.base,second.contract_id)
        self.ports[292]=next_port()
        main=self.make_main()
        first=self.dispatch(main,291)
        other=self.dispatch(main,292)
        self.assertEqual(len(self.prompts()),2)
        self.assertNotEqual(first["context"],other["context"])
        self.assertNotEqual(first["worktree"],other["worktree"])
        self.assertEqual(self.dispatch(main,291)["prompt_state"],"ALREADY_DISPATCHED")
        self.assertEqual(self.dispatch(main,292)["prompt_state"],"ALREADY_DISPATCHED")
        self.assertEqual(len(self.prompts()),2)

    def test_real_github_reader_adapter_requires_exact_repo_issue(self):
        expected={"number":291,"state":"OPEN","title":"T","body":"B"}
        completed=subprocess.CompletedProcess(
            args=["gh"],returncode=0,stdout=json.dumps(expected),stderr="")
        with patch("tools.kagari_vnext_main.subprocess.run",
                   return_value=completed) as run:
            value=github_issue_reader("acme/widgets")(291)
        self.assertEqual(value,{"repository":"acme/widgets",**expected})
        argv=run.call_args.args[0]
        self.assertEqual(argv,["gh","issue","view","291","--repo",
                               "acme/widgets","--json","number,title,body,state"])
        self.assertEqual(run.call_args.kwargs["stdin"],subprocess.DEVNULL)
        self.assertEqual(len(self.prompts()),0)

    def test_closed_issue_refuses_before_allocation(self):
        self.facts[291]["state"]="CLOSED"
        with self.assertRaises(MainError) as exc:
            self.dispatch(self.main,291)
        self.assertEqual(exc.exception.code,"ISSUE_SNAPSHOT_DRIFT")
        self.assertFalse((self.project/".worktrees").exists())


if __name__=="__main__":
    unittest.main()
