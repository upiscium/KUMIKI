"""#339 CLI auth read-only Host lane: no App/token, no network/remote effects."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"components/agent-core-v4"))
from kagari_metadata_v2_cli_readonly import GhReadOnlyHost,CliReadRefused,_json,_run
from metadata_v2 import RefState

REPO="synthetic/widgets"
G="1"*40
MAIN="2"*40
N="3"*40

class FakeGh:
    def __init__(self):
        self.calls=[]
        self.replies={
            "user":{"login":"cli-user","id":24},
            f"repos/{REPO}":{"full_name":REPO,"id":4242,"node_id":"R_opaque",
                             "default_branch":"main","archived":False},
            f"repos/{REPO}/git/ref/heads/main":{
                "ref":"refs/heads/main","object":{"type":"commit","sha":MAIN}},
            f"repos/{REPO}/git/ref/heads/kagari/metadata":{
                "ref":"refs/heads/kagari/metadata","object":{"type":"commit","sha":G}},
            f"repos/{REPO}/git/ref/heads/kagari-metadata/7":{
                "ref":"refs/heads/kagari-metadata/7","object":{"type":"commit","sha":N}},
            f"repos/{REPO}/issues/7":{"number":7,"state":"open","title":"test issue",
                                         "body":"one disposable issue"},
        }
    def __call__(self,argv,timeout):
        self.calls.append((argv,timeout))
        if len(argv)!=5 or argv[:4]!=["gh","api","-X","GET"]:
            raise AssertionError("NON_GET_GITHUB_OPERATION")
        endpoint=argv[4]
        if endpoint not in self.replies:
            return 1,b'{"message":"Not Found","status":"404"}'
        value=self.replies[endpoint]
        if isinstance(value,tuple):
            return value
        return 0,json.dumps(value).encode()

class CliHostTests(unittest.TestCase):
    def setUp(self):
        self.fake=FakeGh()
        self.host=GhReadOnlyHost(REPO,4242,"R_opaque",runner=self.fake)
        self.deadline=time.monotonic()+6
    def preflight(self):
        return self.host.preflight(self.deadline)
    def test_preflight_uses_existing_cli_user_not_github_app(self):
        state=self.preflight()
        self.assertEqual((state.actor,state.actor_id),("cli-user",24))
        self.assertEqual(state.default_oid,MAIN)
        self.assertEqual(len(self.fake.calls),3)
        self.assertTrue(all(v[0][:4]==["gh","api","-X","GET"] for v in self.fake.calls))
    def test_real_direct_ref_and_absent_ref(self):
        self.preflight()
        self.assertEqual(self.host.observe("refs/heads/kagari/metadata",self.deadline),G)
        self.assertEqual(self.host.observe("refs/heads/kagari-metadata/7",self.deadline),N)
        self.assertIsNone(self.host.observe("refs/heads/kagari-metadata/8",self.deadline))
    def test_ref_before_preflight_refuses_without_api(self):
        with self.assertRaises(CliReadRefused):
            self.host.observe("refs/heads/kagari/metadata",self.deadline)
        self.assertEqual(self.fake.calls,[])
    def test_bad_repo_selection_refuses(self):
        for name,ident,node in (("../x",4242,"R_opaque"),("x/y",False,"R_opaque"),
                                ("x/y",4242,""),("x/y",0,"R_opaque")):
            with self.subTest(name=name,ident=ident,node=node):
                with self.assertRaises(CliReadRefused):
                    GhReadOnlyHost(name,ident,node,runner=self.fake)
    def test_json_boolean_cannot_spoof_positive_integer_id_or_issue_number(self):
        fake=FakeGh()
        fake.replies[f"repos/{REPO}"]["id"]=True
        selected=GhReadOnlyHost(REPO,1,"R_opaque",runner=fake)
        with self.assertRaisesRegex(CliReadRefused,"cli_repo_or_actor_identity_mismatch"):
            selected.preflight(self.deadline)
        self.preflight()
        self.fake.replies[f"repos/{REPO}/issues/1"]={
            "number":True,"state":"open","title":"spoofed boolean","body":""}
        with self.assertRaisesRegex(CliReadRefused,"cli_issue_identity_invalid"):
            self.host.issue(1,self.deadline)

    def test_cross_repo_id_mismatch_refuses(self):
        self.fake.replies[f"repos/{REPO}"]["id"]=9191
        with self.assertRaisesRegex(CliReadRefused,"cli_repo_or_actor_identity_mismatch"):
            self.preflight()
    def test_account_auth_required(self):
        self.fake.replies["user"]=(1,b'{"message":"Bad credentials","status":"401"}')
        with self.assertRaisesRegex(CliReadRefused,"cli_api_unavailable"):
            self.preflight()
    def test_archived_or_foreign_repo_refuses(self):
        for field,value in (("archived",True),("node_id","R_wrong"),
                            ("full_name","malicious/foreign"),("default_branch","dev")):
            with self.subTest(field=field):
                f=FakeGh()
                f.replies[f"repos/{REPO}"][field]=value
                h=GhReadOnlyHost(REPO,4242,"R_opaque",runner=f)
                with self.assertRaises(CliReadRefused):
                    h.preflight(self.deadline)
    def test_ref_allowlist_rejects_foreign_symbols_and_product_branches(self):
        self.preflight()
        calls=len(self.fake.calls)
        for ref in ("refs/heads/other","refs/tags/v1","refs/heads/kagari-metadata/00",
                    "refs/heads/kagari-metadata/0","refs/heads/kagari-metadata/7/sub",
                    "refs/heads/task-7","refs/heads/main;touch /tmp/bad"):
            with self.subTest(ref=ref):
                with self.assertRaises(CliReadRefused):
                    self.host.observe(ref,self.deadline)
        self.assertEqual(len(self.fake.calls),calls)
    def test_symbolic_opaque_or_mismatched_ref_refused(self):
        self.preflight()
        endpoint=f"repos/{REPO}/git/ref/heads/kagari/metadata"
        cases=[
            {"ref":"refs/heads/kagari/metadata","object":{"type":"tag","sha":G}},
            {"ref":"refs/heads/foreign","object":{"type":"commit","sha":G}},
            {"ref":"refs/heads/kagari/metadata","object":{"type":"commit","sha":"z"*40}},
            {"ref":"refs/heads/kagari/metadata","object":{"type":"commit","sha":"1"*64}},
        ]
        for data in cases:
            with self.subTest(data=data):
                self.fake.replies[endpoint]=data
                with self.assertRaisesRegex(CliReadRefused,"cli_ref_identity_invalid"):
                    self.host.observe("refs/heads/kagari/metadata",self.deadline)
    def test_core_metadata_observer_exact_nominal_type(self):
        self.preflight()
        value=self.host.metadata_observer("refs/heads/kagari/metadata",self.deadline)
        self.assertIs(type(value),RefState)
        self.assertEqual((value.ref,value.commit,value.direct),
                         ("refs/heads/kagari/metadata",G,True))
        absent=self.host.metadata_observer("refs/heads/kagari-metadata/8",self.deadline)
        self.assertIs(type(absent),RefState)
        self.assertEqual((absent.commit,absent.direct),(None,True))
        with self.assertRaises(CliReadRefused):
            self.host.metadata_observer("refs/heads/main",self.deadline)

    def test_404_is_only_accepted_absence(self):
        self.preflight()
        endpoint=f"repos/{REPO}/git/ref/heads/kagari/metadata"
        self.fake.replies[endpoint]=(1,b'{"message":"Forbidden","status":"403"}')
        with self.assertRaisesRegex(CliReadRefused,"cli_api_unavailable"):
            self.host.observe("refs/heads/kagari/metadata",self.deadline)
    def test_issue_read_bound_to_id_and_non_pr(self):
        self.preflight()
        value=self.host.issue(7,self.deadline)
        self.assertEqual((value["number"],value["actor"],value["repo_id"]),
                         (7,"cli-user",4242))
        self.fake.replies[f"repos/{REPO}/issues/7"]["pull_request"]={"url":"..." }
        with self.assertRaisesRegex(CliReadRefused,"cli_issue_identity_invalid"):
            self.host.issue(7,self.deadline)
    def test_issue_false_and_foreign_deny(self):
        self.preflight()
        with self.assertRaisesRegex(CliReadRefused,"cli_issue_invalid"):
            self.host.issue(True,self.deadline)
        self.fake.replies[f"repos/{REPO}/issues/7"]["number"]=8
        with self.assertRaises(CliReadRefused):
            self.host.issue(7,self.deadline)
    def test_json_rejects_duplicate_nonfinite_and_oversize(self):
        for raw in (b'{"id":1,"id":2}',b'{"id":NaN}',b'x'*(2*1024*1024+1)):
            with self.subTest(rawsize=len(raw)):
                with self.assertRaises(CliReadRefused):
                    _json(raw)
    def test_deadline_and_uncertain_api_refuse(self):
        with self.assertRaisesRegex(CliReadRefused,"cli_deadline"):
            self.host.preflight(time.monotonic()-1)
        self.assertEqual(self.fake.calls,[])
        self.fake.replies[f"repos/{REPO}/git/ref/heads/main"]=(1,b"NOT_JSON")
        with self.assertRaises(CliReadRefused):
            self.preflight()
    def test_readback_does_not_grant_writer_or_task(self):
        self.preflight()
        observation=self.host.readback(self.deadline)
        self.assertEqual((observation["archive_oid"],observation["main_oid"]),(G,MAIN))
        self.assertFalse(any("authorize" in k or "writer" in k for k in observation))
        self.assertTrue(all(args[3]=="GET" for args,_ in self.fake.calls))

if __name__=="__main__":
    unittest.main()
