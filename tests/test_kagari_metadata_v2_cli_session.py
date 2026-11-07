"""R35: fake-only existing gh login capture and invocation-bound API transport.

No actual authentication tokens, GitHub network or external mutation.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from kagari_metadata_v2_cli_session import (
    GhCliSession,GhSessionRefused,_capture_token_pipe
)

REPO="synthetic/fixture"
TOKEN_A="synthetic-existing-cli-actor-A-token"
TOKEN_B="synthetic-existing-cli-actor-B-token"

class FakeProcess:
    def __init__(self):
        self.ambient=1
        self.return_invalid_token=False
        self.capture_failure=False
        self.bound_calls=[]
    def __call__(self,argv,body,timeout,env):
        assert argv[0]=="/fake/gh"
        assert 0<timeout<=20
        if argv[1:]==["auth","token","--hostname","github.com"]:
            if self.capture_failure:return 1,b""
            if self.return_invalid_token:return 0,b"bad\n"
            return 0,(TOKEN_A if self.ambient==1 else TOKEN_B).encode()+b"\n"
        assert env["GH_HOST"]=="github.com"
        assert env["GH_PROMPT_DISABLED"]=="1"
        assert "GITHUB_TOKEN" not in env and "GH_DEBUG" not in env
        assert "GH_CONFIG_DIR" not in env
        token=env["GH_TOKEN"]
        actor={TOKEN_A:1,TOKEN_B:2}[token]
        self.bound_calls.append((argv[1:],actor,body))
        if argv[1:]==["api","-X","GET","user"]:
            return 0,json.dumps({"login":"synthetic","id":actor}).encode()
        if argv[1:]==["api","-X","GET",f"repos/{REPO}"]:
            return 0,b'{"id":12,"node_id":"R_node","full_name":"synthetic/fixture","default_branch":"main","archived":false}'
        if argv[1:]==["api","graphql","--input","-"]:
            assert body is not None
            return 0,b'{"data":{"updateRefs":{"clientMutationId":"fake"}}}'
        if argv[1:]==["api","-X","POST",f"repos/{REPO}/git/blobs","--input","-"]:
            assert body is not None
            return 0,b'{"sha":"fake"}'
        return 1,b'{"status":"404"}'

class SessionTests(unittest.TestCase):
    def setUp(self):
        self.fake=FakeProcess()
        self.deadline=time.monotonic()+20
    def capture(self,actor=1):
        return GhCliSession.capture(
            REPO,actor,self.deadline,
            gh_binary="/fake/gh",invoke=self.fake)
    def test_production_capture_pipe_never_uses_tempfile(self):
        class MemoryPipeChild:
            def __init__(self,data):
                readfd,writefd=os.pipe()
                os.write(writefd,data)
                os.close(writefd)
                self.stdout=os.fdopen(readfd,"rb",buffering=0)
            def wait(self,timeout):
                return 0
            def poll(self):
                return 0
            def kill(self):
                raise AssertionError("completed child must not be killed")
        child=MemoryPipeChild(TOKEN_A.encode()+b"\n")
        with patch("kagari_metadata_v2_cli_session.subprocess.Popen",
                   return_value=child) as popen:
            with patch("kagari_metadata_v2_cli_session.tempfile.TemporaryFile",
                       side_effect=AssertionError("token stdout touched disk")):
                rc,value=_capture_token_pipe(
                    ["/fake/gh","auth","token","--hostname","github.com"],
                    None,2,{"GH_HOST":"github.com"})
        self.assertEqual((rc,value),(0,TOKEN_A.encode()+b"\n"))
        args=popen.call_args.kwargs
        self.assertIs(args["stdout"],subprocess.PIPE)
        self.assertIs(args["stderr"],subprocess.DEVNULL)
        self.assertIs(args["stdin"],subprocess.DEVNULL)

    def test_production_capture_pipe_enforces_hard_output_cap(self):
        class ExcessChild:
            def __init__(self):
                readfd,writefd=os.pipe()
                os.write(writefd,b"X"*4100)
                os.close(writefd)
                self.stdout=os.fdopen(readfd,"rb",buffering=0)
            def poll(self):return 0
            def wait(self,timeout):return 0
            def kill(self):raise AssertionError("already finished")
        with patch("kagari_metadata_v2_cli_session.subprocess.Popen",
                   return_value=ExcessChild()):
            with self.assertRaisesRegex(GhSessionRefused,
                                        "cli_session_capture_limit"):
                _capture_token_pipe(["/fake/gh"],None,2,{})

    def test_production_capture_pipe_timeout_kills_and_reaps_child(self):
        class HangingChild:
            def __init__(self):
                self.reader,self.writer=os.pipe()
                self.stdout=os.fdopen(self.reader,"rb",buffering=0)
                self.killed=False
            def poll(self):return 0 if self.killed else None
            def kill(self):self.killed=True
            def wait(self,timeout):
                os.close(self.writer)
                return 1
        child=HangingChild()
        with patch("kagari_metadata_v2_cli_session.subprocess.Popen",
                   return_value=child):
            with self.assertRaisesRegex(GhSessionRefused,
                                        "cli_session_capture_deadline"):
                _capture_token_pipe(["/fake/gh"],None,.01,{})
        self.assertTrue(child.killed)

    def test_capture_existing_cli_token_no_new_authentication(self):
        s=self.capture()
        self.assertEqual((s.repo,s.actor_id),(REPO,1))
        self.assertNotIn(TOKEN_A,repr(s))
        self.assertEqual(self.fake.bound_calls[0][1],1)
        self.assertEqual(self.fake.bound_calls[0][0],["api","-X","GET","user"])
        s.close()
        with self.assertRaises(GhSessionRefused):
            s.read(["gh","api","-X","GET","user"],1)
    def test_ambient_cli_switch_before_and_between_calls_stays_A(self):
        s=self.capture()
        self.fake.ambient=2
        with patch.dict(os.environ,{"GH_TOKEN":TOKEN_B,
                                    "GITHUB_TOKEN":TOKEN_B,
                                    "GH_DEBUG":"api",
                                    "GH_CONFIG_DIR":"/fake/external"},
                        clear=False):
            for cmd in (
                    ["gh","api","-X","GET","user"],
                    ["gh","api","-X","GET",f"repos/{REPO}"]):
                rc,_=s.read(cmd,3)
                self.assertEqual(rc,0)
            self.fake.ambient=1
            self.assertEqual(
                s.effect(["gh","api","-X","POST",
                          f"repos/{REPO}/git/blobs","--input","-"],
                         b'{"content":"YQ==","encoding":"base64"}',2)[0],0)
            self.fake.ambient=2
            self.assertEqual(
                s.effect(["gh","api","graphql","--input","-"],
                         b'{"query":"mutation fake"}',2)[0],0)
        self.assertTrue(self.fake.bound_calls)
        self.assertEqual({actor for _,actor,_ in self.fake.bound_calls},{1})
    def test_current_B_identity_cannot_be_admitted_as_A(self):
        self.fake.ambient=2
        with self.assertRaisesRegex(GhSessionRefused,"cli_session_actor_mismatch"):
            self.capture(actor=1)
        s=self.capture(actor=2)
        self.assertEqual(s.actor_id,2)
        s.close()
    def test_no_current_login_and_malformed_token_fail_closed(self):
        self.fake.capture_failure=True
        with self.assertRaisesRegex(GhSessionRefused,"cli_session_capture_unavailable"):
            self.capture()
        self.fake.capture_failure=False
        self.fake.return_invalid_token=True
        with self.assertRaisesRegex(GhSessionRefused,"cli_session_capture_invalid"):
            self.capture()
    def test_foreign_read_and_effect_are_refused_without_subprocess(self):
        s=self.capture()
        prior=len(self.fake.bound_calls)
        for argv in (
            ["gh","api","-X","GET","repos/foreign/repo"],
            ["gh","api","-X","PATCH",f"repos/{REPO}/git/refs"],
        ):
            with self.assertRaises(GhSessionRefused):
                s.read(argv,2)
        for argv in (
            ["gh","api","-X","POST","repos/foreign/repo/git/blobs","--input","-"],
            ["gh","api","-X","PATCH",f"repos/{REPO}/git/refs","--input","-"],
            ["gh","api","-X","POST",f"repos/{REPO}/issues","--input","-"],
            ["gh","api","graphql","--raw-field","query=mutation"],
        ):
            with self.assertRaisesRegex(GhSessionRefused,
                                        "cli_session_effect_not_allowed"):
                s.effect(argv,b"{}",2)
        self.assertEqual(len(self.fake.bound_calls),prior)
    def test_bad_host_and_binary_fail_without_capture(self):
        for repo,actor in (("../bad",1),(REPO,0),(REPO,True),(REPO,"1")):
            with self.subTest(repo=repo,actor=actor):
                with self.assertRaises(GhSessionRefused):
                    self.capture_for(repo,actor)
        with self.assertRaisesRegex(GhSessionRefused,"cli_session_binary_unavailable"):
            GhCliSession.capture(REPO,1,self.deadline,gh_binary="gh",invoke=self.fake)
    def capture_for(self,repo,actor):
        return GhCliSession.capture(repo,actor,self.deadline,
                                    gh_binary="/fake/gh",invoke=self.fake)
    def test_close_context_disables_bound_commands(self):
        with self.capture() as s:
            self.assertEqual(s.read(["gh","api","-X","GET","user"],2)[0],0)
        with self.assertRaisesRegex(GhSessionRefused,"cli_session_closed"):
            s.effect(["gh","api","graphql","--input","-"],b"{}",2)
    def test_bound_binary_and_token_never_in_arg_lists(self):
        s=self.capture()
        s.effect(["gh","api","graphql","--input","-"],b"{}",1)
        self.assertTrue(self.fake.bound_calls)
        self.assertTrue(all(TOKEN_A not in repr(argv)
                            for argv,_,_ in self.fake.bound_calls))
        self.assertNotIn(TOKEN_A,repr(s))

if __name__=="__main__":
    unittest.main()
