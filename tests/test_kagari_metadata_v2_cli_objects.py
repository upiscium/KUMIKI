"""R30: byte-exact Metadata v2 REST Git object Reader, fake GET-only transport."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"tools"),str(ROOT/"components/agent-core-v4")]
from kagari_metadata_v2_cli_readonly import GhReadOnlyHost,CliReadRefused
from kagari_metadata_v2_cli_objects import GhMetadataObjects,_EMPTY_TREE
from metadata_v2 import construct,git_id
import metadata_codec as codec
from task_record import encode_contract,encode_record

REPO="synthetic/fixture"
G="4"*40

class FakeGit:
    def __init__(self):
        cid,contract=encode_contract(REPO,"7",G,{
            "schema_version":1,"title":"synthetic issue","body":"read-only test"})
        rid,record=encode_record(REPO,"7",G,{
            "schema_version":1,"branch_ref":"refs/heads/task-7",
            "contract_id":cid})
        self.files={
            codec.object_path("contract",cid):contract,
            codec.object_path("task-record",rid):record,
            "tasks/7/record":(rid+"\n").encode(),
        }
        self.bundle=construct(self.files,(G,),nonce="r30-issue")
        self.by_endpoint={}
        self.calls=[]
        self.by_endpoint["user"]={"login":"owner","id":1}
        self.by_endpoint[f"repos/{REPO}"]={
            "full_name":REPO,"id":12,"node_id":"R_node",
            "archived":False,"default_branch":"main"}
        self.by_endpoint[f"repos/{REPO}/git/ref/heads/main"]={
            "ref":"refs/heads/main","object":{"type":"commit","sha":"5"*40}}
        for key,(kind,data) in self.bundle.objects.items():
            ep=f"repos/{REPO}/git/"
            if kind=="blob":
                self.by_endpoint[ep+"blobs/"+key]={
                    "sha":key,"size":len(data),"encoding":"base64",
                    "content":base64.b64encode(data).decode()}
            if kind=="tree":
                rows=[]
                i=0
                while i<len(data):
                    j=data.index(b" ",i)
                    k=data.index(b"\0",j)
                    mode=data[i:j].decode()
                    name=data[j+1:k].decode()
                    sha=data[k+1:k+21].hex()
                    rows.append({"path":name,"mode":mode if mode=="100644" else "040000",
                                 "type":"blob" if mode=="100644" else "tree",
                                 "sha":sha})
                    i=k+21
                self.by_endpoint[ep+"trees/"+key]={
                    "sha":key,"tree":rows,"truncated":False}
            if kind=="commit":
                raw=data.decode()
                header,body=raw.split("\n\n",1)
                parents=[x.removeprefix("parent ") for x in header.splitlines()
                         if x.startswith("parent ")]
                tree=next(x.removeprefix("tree ") for x in header.splitlines()
                          if x.startswith("tree "))
                who={"name":"Metadata v2","email":"metadata@invalid",
                     "date":"2000-01-01T00:00:00Z"}
                self.by_endpoint[ep+"commits/"+key]={
                    "sha":key,"tree":{"sha":tree},
                    "parents":[{"sha":x} for x in parents],
                    "author":who,"committer":who,
                    "message":body.rstrip("\n")}
    def __call__(self,argv,timeout):
        self.calls.append(argv)
        assert len(argv)==5 and argv[:4]==["gh","api","-X","GET"]
        key=argv[4]
        if key not in self.by_endpoint:
            return 1,b'{"status":"404","message":"Not Found"}'
        return 0,json.dumps(self.by_endpoint[key]).encode()

class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.fake=FakeGit()
        self.host=GhReadOnlyHost(REPO,12,"R_node",runner=self.fake)
        self.deadline=time.monotonic()+10
        self.host.preflight(self.deadline)
        self.reader=GhMetadataObjects(self.host)
    def test_all_bundle_objects_byte_exact(self):
        for key,(expected_kind,expected_bytes) in self.fake.bundle.objects.items():
            with self.subTest(kind=expected_kind,key=key):
                got=self.reader(key,100000,self.deadline)
                self.assertEqual(got,(expected_kind,expected_bytes))
    def test_empty_tree_sha_is_known_canonical_bytes(self):
        count=len(self.fake.calls)
        self.assertEqual(self.reader(_EMPTY_TREE,10,self.deadline),("tree",b""))
        self.assertEqual(len(self.fake.calls),count)
    def test_github_tree_api_commit_sha_dereference_never_reclassifies_commit(self):
        key=self.fake.bundle.commit
        # Actual GitHub GET /git/trees/{commit_sha} returns 200 with the
        # input SHA, but it is NOT a Git tree object of that SHA.
        self.fake.by_endpoint[f"repos/{REPO}/git/trees/{key}"]={
            "sha":key,"tree":[],"truncated":False}
        self.assertEqual(self.reader(key,1000,self.deadline),
                         self.fake.bundle.objects[key])

    def test_absent_object_refuses(self):
        with self.assertRaisesRegex(CliReadRefused,"cli_git_object_absent_or_unreadable"):
            self.reader("f"*40,1000,self.deadline)
    def test_wrong_blob_bytes_are_rejected_by_git_oid(self):
        key=next(k for k,(kind,_) in self.fake.bundle.objects.items() if kind=="blob")
        x=self.fake.by_endpoint[f"repos/{REPO}/git/blobs/{key}"]
        data=b"forged"
        x.update(content=base64.b64encode(data).decode(),size=len(data))
        with self.assertRaisesRegex(CliReadRefused,"cli_git_object_sha_mismatch"):
            self.reader(key,100,self.deadline)
    def test_mismatch_tree_rejected(self):
        key=next(k for k,(kind,_) in self.fake.bundle.objects.items() if kind=="tree")
        obj=self.fake.by_endpoint[f"repos/{REPO}/git/trees/{key}"]
        obj["tree"]=[]
        with self.assertRaises(CliReadRefused):
            self.reader(key,1000,self.deadline)
    def test_rest_escaped_unicode_surrogates_are_typed_refusals(self):
        tree=next(k for k,(kind,_) in self.fake.bundle.objects.items()
                  if kind=="tree" and self.fake.by_endpoint[
                      f"repos/{REPO}/git/trees/{k}"]["tree"])
        tref=self.fake.by_endpoint[f"repos/{REPO}/git/trees/{tree}"]
        tref["tree"][0]["path"]=chr(0xD800)
        with self.assertRaisesRegex(CliReadRefused,"cli_git_tree_entry_invalid"):
            self.reader(tree,100_000,self.deadline)
        commit=self.fake.bundle.commit
        cref=self.fake.by_endpoint[f"repos/{REPO}/git/commits/{commit}"]
        cref["message"]="Metadata v2 "+chr(0xD800)
        with self.assertRaisesRegex(CliReadRefused,"cli_git_commit_shape_invalid"):
            self.reader(commit,100_000,self.deadline)

    def test_malformed_commit_date_and_message_refused(self):
        key=self.fake.bundle.commit
        obj=self.fake.by_endpoint[f"repos/{REPO}/git/commits/{key}"]
        obj["author"]["date"]="not-a-date"
        with self.assertRaises(CliReadRefused):
            self.reader(key,1000,self.deadline)
        obj["author"]["date"]="2000-01-01T00:00:00Z"
        obj["message"]="foreign message"
        with self.assertRaises(CliReadRefused):
            self.reader(key,1000,self.deadline)
    def test_missing_auth_and_foreign_selected_repo_refused(self):
        x=GhReadOnlyHost(REPO,12,"R_node",runner=self.fake)
        with self.assertRaisesRegex(CliReadRefused,"cli_preflight_required"):
            GhMetadataObjects(x)("2"*40,100,self.deadline)
        self.fake.by_endpoint[f"repos/{REPO}"]["id"]=999
        with self.assertRaises(CliReadRefused):
            x.preflight(self.deadline)
    def test_object_reader_bounds_and_deadline(self):
        key=self.fake.bundle.commit
        with self.assertRaises(CliReadRefused):
            self.reader(key,10,self.deadline)
        with self.assertRaises(CliReadRefused):
            self.reader(key,100,time.monotonic()-1)
    def test_no_git_mutation_in_reading(self):
        for key in self.fake.bundle.objects:
            self.reader(key,1000,self.deadline)
        self.assertTrue(all(x[:4]==["gh","api","-X","GET"] for x in self.fake.calls))

if __name__=="__main__":unittest.main()
