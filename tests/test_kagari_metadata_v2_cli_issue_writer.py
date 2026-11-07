"""#339 R30: source-only Issue ref CAS writer with fake in-memory REST/GraphQL.

Never contacts or mutates GitHub. Only a trusted caller injecting an effect
capability can enter any POST; this module does not install that capability.
"""
from __future__ import annotations

import base64
from pathlib import Path
import json
import sys
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"tools"),str(ROOT/"components/agent-core-v4")]
from metadata_v2 import Update,ARCHIVE_REF,Bundle,construct
import metadata_codec as codec
from kagari_metadata_v2_cli_readonly import GhReadOnlyHost
from kagari_metadata_v2_cli_objects import GhMetadataObjects
from kagari_metadata_v2_cli_issue_writer import (
    GhIssueCASWriter,CliWriterRefused,CliWriterUncertain
)
import kagari_metadata_v2_cli_issue_writer as writer_module
from tests.test_kagari_metadata_v2_cli_objects import FakeGit,REPO,G

ISSUE="refs/heads/kagari-metadata/7"
ARCHIVE=f"repos/{REPO}/git/ref/heads/kagari/metadata"
IREAD=f"repos/{REPO}/git/ref/heads/kagari-metadata/7"
ZERO="0"*40

class FakeEffect(FakeGit):
    def __init__(self):
        super().__init__()
        self.calls_post=[]
        self.reject_cas=False
        self.fail_blob_ack=False
        # Stateful remote fixture: Git objects do NOT exist until the
        # corresponding GitHub POST has occurred. A response/readback
        # cannot succeed by borrowing prepopulated FakeGit objects.
        self.pending_readback={}
        prefix=f"repos/{REPO}/git/"
        for key,(kind,_) in self.bundle.objects.items():
            endpoint=prefix+{"blob":"blobs","tree":"trees",
                             "commit":"commits"}[kind]+"/"+key
            self.pending_readback[endpoint]=self.by_endpoint.pop(endpoint)
        self.by_endpoint[ARCHIVE]={
            "ref":ARCHIVE_REF,"object":{"type":"commit","sha":G}}
    def run_effect(self,argv,body,timeout):
        self.calls_post.append((argv,body,timeout))
        parsed=json.loads(body)
        if argv[:4]==["gh","api","-X","POST"]:
            kind=argv[4].split("/")[-1]
            if kind=="blobs":
                data=base64.b64decode(parsed["content"])
                key=next(key for key,(k,raw) in self.bundle.objects.items()
                         if k=="blob" and raw==data)
                endpoint=f"repos/{REPO}/git/blobs/{key}"
                self.by_endpoint[endpoint]=self.pending_readback[endpoint]
                if self.fail_blob_ack:return 1,b'{"errors":[{"message":"unknown"}]}'
                return 0,json.dumps({"sha":key}).encode()
            if kind=="trees":
                self.assert_tree_payload(parsed)
                key=next(key for key,(k,raw) in self.bundle.objects.items()
                         if k=="commit")
                tree=self.bundle.objects[key][1].split(b"\n",1)[0][5:].decode()
                for oid,(kind,_) in self.bundle.objects.items():
                    if kind=="tree":
                        endpoint=f"repos/{REPO}/git/trees/{oid}"
                        self.by_endpoint[endpoint]=self.pending_readback[endpoint]
                return 0,json.dumps({"sha":tree}).encode()
            if kind=="commits":
                self.assert_commit_payload(parsed)
                endpoint=f"repos/{REPO}/git/commits/{self.bundle.commit}"
                self.by_endpoint[endpoint]=self.pending_readback[endpoint]
                return 0,json.dumps({"sha":self.bundle.commit}).encode()
            raise AssertionError("UNEXPECTED_POST "+str(argv))
        assert argv==["gh","api","graphql","--input","-"]
        inp=parsed["variables"]["input"]
        updates=inp["refUpdates"]
        assert len(updates)==2
        assert updates[0]=={
            "name":ISSUE,"beforeOid":ZERO,
            "afterOid":self.bundle.commit,"force":False}
        assert updates[1]=={
            "name":ARCHIVE_REF,"beforeOid":G,"afterOid":G,"force":False}
        assert inp["repositoryId"]=="R_node"
        if self.reject_cas:
            return 1,b'{"errors":[{"message":"stale expected oid"}]}'
        self.by_endpoint[IREAD]={
            "ref":ISSUE,"object":{"type":"commit","sha":self.bundle.commit}}
        return 0,json.dumps({"data":{"updateRefs":{
            "clientMutationId":inp["clientMutationId"]}}}).encode()
    def assert_tree_payload(self,payload):
        rows=payload["tree"]
        assert len(rows)==3
        assert {item["path"] for item in rows}==set(self.files)
        assert all(item["mode"]=="100644" and item["type"]=="blob"
                   and item["sha"] in self.bundle.objects
                   and self.bundle.objects[item["sha"]][1]==self.files[item["path"]]
                   for item in rows)
    def assert_commit_payload(self,payload):
        assert payload["parents"]==[G]
        assert payload["author"]=={
            "name":"Metadata v2","email":"metadata@invalid",
            "date":"2000-01-01T00:00:00Z"}
        assert payload["message"]=="Metadata v2 r30-issue\n"
    def __call__(self,argv,timeout):
        return super().__call__(argv,timeout)

class WriterTests(unittest.TestCase):
    def setUp(self):
        self.fake=FakeEffect()
        self.host=GhReadOnlyHost(REPO,12,"R_node",runner=self.fake)
        self.deadline=time.monotonic()+15
        self.approved_actor_id=self.host.preflight(self.deadline).actor_id
        self.objects=GhMetadataObjects(self.host)
        self.update=Update(REPO,G,"7",ISSUE,G,None,self.fake.bundle.commit)
        self.grants=0
        self.intents=0
        self.allow=True
        self.orig=True
    def authorize(self,request,deadline):
        self.grants+=1
        return self.allow
    def intent(self,request,deadline):
        self.intents+=1
        return self.orig
    def writer(self,effect=True):
        return GhIssueCASWriter(
            self.host,self.objects,expected_actor_id=self.approved_actor_id,
            effect=self.fake.run_effect if effect else None,
            authorize=self.authorize,original_intent=self.intent)
    def test_positive_exact_two_ref_cas_expected_old(self):
        self.assertTrue(all(key not in self.fake.by_endpoint
                            for key in self.fake.pending_readback))
        self.assertIs(self.writer()(self.update,self.fake.bundle,self.deadline),True)
        self.assertTrue(all(key in self.fake.by_endpoint
                            for key in self.fake.pending_readback))
        kinds=[x[0][4].rsplit("/",1)[-1] if x[0][:4]==["gh","api","-X","POST"]
               else "graphql" for x in self.fake.calls_post]
        self.assertEqual(kinds,["blobs","blobs","blobs","trees","commits","graphql"])
        self.assertEqual(self.host.observe(ISSUE,self.deadline),self.fake.bundle.commit)
        self.assertEqual(self.host.observe(ARCHIVE_REF,self.deadline),G)
        self.assertGreaterEqual(self.grants,2)
        self.assertGreaterEqual(self.intents,2)
    def test_noncanonical_update_task_typed_refusal_before_post(self):
        for task in ("07","0","x",None,True):
            with self.subTest(task=task):
                request=replace(self.update,task=task)
                with self.assertRaisesRegex(
                        CliWriterRefused,"cli_writer_issue_scope_invalid"):
                    self.writer()(request,self.fake.bundle,self.deadline)
        self.assertEqual(self.fake.calls_post,[])

    def test_malformed_bundle_entry_shapes_typed_refusal_before_post(self):
        objects=dict(self.fake.bundle.objects)
        malformed=(
            Bundle(self.fake.bundle.commit,[]),
            Bundle(self.fake.bundle.commit,{**objects,"a"*40:("blob",)}),
            Bundle(self.fake.bundle.commit,{**objects,"b"*40:["blob",b"bad"]}),
        )
        for bundle in malformed:
            with self.subTest(bundle_type=type(bundle.objects).__name__):
                with self.assertRaises(CliWriterRefused):
                    self.writer()(self.update,bundle,self.deadline)
        self.assertEqual(self.fake.calls_post,[])

    def test_writer_actor_must_be_explicit_host_bound_positive_integer(self):
        for bad in (None,True,0,-1,"1",1.0):
            with self.subTest(bad=bad):
                with self.assertRaises(CliWriterRefused):
                    GhIssueCASWriter(
                        self.host,self.objects,expected_actor_id=bad,
                        effect=self.fake.run_effect,
                        authorize=self.authorize,original_intent=self.intent)
        self.assertEqual(self.fake.calls_post,[])

    def test_actor_switch_between_admission_and_first_effect_refuses_without_post(self):
        self.fake.by_endpoint["user"]["id"]=self.approved_actor_id+1
        # An intervening gh preflight must NOT silently repin the writer.
        self.host.preflight(self.deadline)
        with self.assertRaisesRegex(CliWriterRefused,"cli_writer_actor_changed"):
            self.writer()(self.update,self.fake.bundle,self.deadline)
        self.assertEqual(self.fake.calls_post,[])

    def test_actor_switch_between_calls_does_not_reauthorize_same_writer(self):
        writer=self.writer()
        self.assertIs(writer(self.update,self.fake.bundle,self.deadline),True)
        original_effect_count=len(self.fake.calls_post)
        self.fake.by_endpoint["user"]["id"]=self.approved_actor_id+1
        with self.assertRaisesRegex(CliWriterRefused,"cli_writer_actor_changed"):
            writer(self.update,self.fake.bundle,self.deadline)
        self.assertEqual(len(self.fake.calls_post),original_effect_count)

    def test_actor_switch_inside_authorization_callback_refuses_before_post(self):
        def change_actor_after_grant(*args):
            self.fake.by_endpoint["user"]["id"]=self.approved_actor_id+1
            return True
        writer=GhIssueCASWriter(
            self.host,self.objects,expected_actor_id=self.approved_actor_id,
            effect=self.fake.run_effect,
            authorize=change_actor_after_grant,original_intent=self.intent)
        with self.assertRaisesRegex(CliWriterRefused,"cli_writer_actor_changed"):
            writer(self.update,self.fake.bundle,self.deadline)
        self.assertEqual(self.fake.calls_post,[])

    def test_actor_switch_after_upload_prevents_cas_without_retry(self):
        actual=self.fake.run_effect
        def switching_effect(argv,payload,timeout):
            out=actual(argv,payload,timeout)
            if argv[:4]==["gh","api","-X","POST"] and argv[4].endswith("/commits"):
                self.fake.by_endpoint["user"]["id"]=self.approved_actor_id+1
            return out
        writer=GhIssueCASWriter(
            self.host,self.objects,expected_actor_id=self.approved_actor_id,
            effect=switching_effect,
            authorize=self.authorize,original_intent=self.intent)
        with self.assertRaisesRegex(CliWriterUncertain,"cli_writer_pre_cas_authority_changed"):
            writer(self.update,self.fake.bundle,self.deadline)
        self.assertTrue(self.fake.calls_post)
        self.assertFalse(any(x[0][2]=="graphql" for x in self.fake.calls_post))
        self.assertNotIn(IREAD,self.fake.by_endpoint)

    def test_actor_switch_after_cas_is_uncertain_not_success(self):
        actual=self.fake.run_effect
        def switching_effect(argv,payload,timeout):
            out=actual(argv,payload,timeout)
            if argv[:3]==["gh","api","graphql"]:
                self.fake.by_endpoint["user"]["id"]=self.approved_actor_id+1
            return out
        writer=GhIssueCASWriter(
            self.host,self.objects,expected_actor_id=self.approved_actor_id,
            effect=switching_effect,
            authorize=self.authorize,original_intent=self.intent)
        with self.assertRaisesRegex(CliWriterUncertain,"cli_writer_post_cas_readback_unknown"):
            writer(self.update,self.fake.bundle,self.deadline)
        self.assertEqual(self.host.observe(ISSUE,self.deadline),self.fake.bundle.commit)

    def test_callback_error_before_effect_yields_typed_refusal(self):
        for failing in ("grant","intent"):
            with self.subTest(failing=failing):
                def fail(*args):
                    raise RuntimeError("synthetic Host callback unavailable")
                writer=GhIssueCASWriter(
                    self.host,self.objects,expected_actor_id=self.approved_actor_id,
                    effect=self.fake.run_effect,
                    authorize=fail if failing=="grant" else self.authorize,
                    original_intent=fail if failing=="intent" else self.intent)
                with self.assertRaisesRegex(CliWriterRefused,"cli_writer_host_authority_unavailable"):
                    writer(self.update,self.fake.bundle,self.deadline)
                self.assertEqual(self.fake.calls_post,[])

    def test_callback_error_after_upload_is_uncertain_not_refused(self):
        for failing in ("grant","intent"):
            with self.subTest(failing=failing):
                fake=FakeEffect()
                host=GhReadOnlyHost(REPO,12,"R_node",runner=fake)
                expected_actor_id=host.preflight(self.deadline).actor_id
                objs=GhMetadataObjects(host)
                counts={"grant":0,"intent":0}
                def callback(kind):
                    def invoked(*args):
                        counts[kind]+=1
                        if kind==failing and counts[kind]>=2:
                            raise RuntimeError("synthetic later callback error")
                        return True
                    return invoked
                writer=GhIssueCASWriter(
                    host,objs,expected_actor_id=expected_actor_id,
                    effect=fake.run_effect,
                    authorize=callback("grant"),original_intent=callback("intent"))
                with self.assertRaisesRegex(CliWriterUncertain,"cli_writer_pre_cas_authority_changed"):
                    writer(self.update,fake.bundle,self.deadline)
                self.assertEqual(len(fake.calls_post),5)
                self.assertNotIn(IREAD,fake.by_endpoint)

    def test_main_head_advancing_independently_does_not_revoke_issue_grant(self):
        # Main is the Product default branch. A concurrent unrelated merge
        # must not change the registered Task base or invalidate a valid
        # Issue-scoped Metadata CAS.
        actual=self.fake.run_effect
        def merge_main_after_first_blob(argv,payload,timeout):
            out=actual(argv,payload,timeout)
            if argv[:4]==["gh","api","-X","POST"] and argv[4].endswith("/blobs"):
                self.fake.by_endpoint[f"repos/{REPO}/git/ref/heads/main"]["object"]["sha"]="6"*40
            return out
        writer=GhIssueCASWriter(
            self.host,self.objects,expected_actor_id=self.approved_actor_id,
            effect=merge_main_after_first_blob,
            authorize=self.authorize,original_intent=self.intent)
        self.assertIs(writer(self.update,self.fake.bundle,self.deadline),True)
        self.assertEqual(self.host.observe(ISSUE,self.deadline),self.fake.bundle.commit)

    def test_missing_effect_runner_blocks_before_any_remote_post(self):
        with self.assertRaisesRegex(CliWriterRefused,"cli_writer_uninstalled"):
            self.writer(False)(self.update,self.fake.bundle,self.deadline)
        self.assertEqual(self.fake.calls_post,[])
    def test_ungranted_effect_refused_before_any_remote_post(self):
        self.allow=False
        with self.assertRaisesRegex(CliWriterRefused,"cli_writer_original_intent_or_grant_required"):
            self.writer()(self.update,self.fake.bundle,self.deadline)
        self.assertEqual(self.fake.calls_post,[])
    def test_original_intent_missing_refuses_no_post(self):
        self.orig=False
        with self.assertRaises(CliWriterRefused):
            self.writer()(self.update,self.fake.bundle,self.deadline)
        self.assertEqual(self.fake.calls_post,[])
    def test_stale_archive_or_issue_blocks_no_post(self):
        self.fake.by_endpoint[ARCHIVE]["object"]["sha"]="8"*40
        with self.assertRaisesRegex(CliWriterRefused,"cli_writer_expected_refs_changed"):
            self.writer()(self.update,self.fake.bundle,self.deadline)
        self.assertEqual(self.fake.calls_post,[])
        self.fake.by_endpoint[ARCHIVE]["object"]["sha"]=G
        self.fake.by_endpoint[IREAD]={
            "ref":ISSUE,"object":{"type":"commit","sha":"9"*40}}
        with self.assertRaises(CliWriterRefused):
            self.writer()(self.update,self.fake.bundle,self.deadline)
        self.assertEqual(self.fake.calls_post,[])
    def test_foreign_and_archive_refs_rejected(self):
        for changed in (
            replace(self.update,ref=ARCHIVE_REF),
            replace(self.update,ref="refs/heads/main"),
            replace(self.update,repository="production/repo"),
            replace(self.update,task="8"),
            replace(self.update,retention_policy="policy"),
        ):
            with self.subTest(ref=changed.ref):
                with self.assertRaises(CliWriterRefused):
                    self.writer()(changed,self.fake.bundle,self.deadline)
        self.assertEqual(self.fake.calls_post,[])
    def test_foreign_issue_tree_refuses_before_any_object_upload(self):
        # Before R31 the adapter admitted a valid Git tree with tasks/8
        # under the approved Update/Issue7 ref. This was a reachable
        # metadata isolation violation if an effect callback was supplied.
        foreign=construct({"tasks/8/record":b"8"*64+b"\n"},
                          (G,),nonce="r31-foreign")
        request=replace(self.update,candidate=foreign.commit)
        with self.assertRaisesRegex(CliWriterRefused,"cli_bundle_cross_issue_tree"):
            self.writer()(request,foreign,self.deadline)
        self.assertEqual(self.fake.calls_post,[])

    def test_mixed_issue_tree_refuses_before_any_post(self):
        mixed=construct({"tasks/7/record":b"0"*64+b"\n",
                         "tasks/8/record":b"8"*64+b"\n"},
                        (G,),nonce="r31-mixed")
        request=replace(self.update,candidate=mixed.commit)
        with self.assertRaisesRegex(CliWriterRefused,"cli_bundle_cross_issue_tree"):
            self.writer()(request,mixed,self.deadline)
        self.assertEqual(self.fake.calls_post,[])

    def test_invalid_or_missing_own_record_pointer_refuses_before_post(self):
        for files in ({"tasks/7/record":b"fake record\n"},
                      {"tasks/7/record":b"f"*63+b"\n"},
                      {"tasks/8/record":b"8"*64+b"\n"}):
            with self.subTest(files=files):
                bundle=construct(files,(G,),nonce="r31-bad-pointer")
                request=replace(self.update,candidate=bundle.commit)
                with self.assertRaises(CliWriterRefused):
                    self.writer()(request,bundle,self.deadline)
        self.assertEqual(self.fake.calls_post,[])

    def test_foreign_issue_or_repository_metadata_envelope_refuses_before_post(self):
        # The R31 Writer accepted a SHA-valid foreign Issue-8 envelope in
        # objects/... while tasks/7/record itself was valid. The same
        # failure applies to a foreign repository envelope.
        for repository,task in ((REPO,"8"),("foreign/repo","7")):
            with self.subTest(repository=repository,task=task):
                cid,data=codec.encode_object(
                    "evidence",repository,task,G,{"note":"synthetic foreign"})
                files=dict(self.fake.files)
                files[codec.object_path("evidence",cid)]=data
                bundle=construct(files,(G,),nonce="r32-foreign-object")
                request=replace(self.update,candidate=bundle.commit)
                with self.assertRaisesRegex(
                        CliWriterRefused,"cli_bundle_object_envelope_invalid"):
                    self.writer()(request,bundle,self.deadline)
                self.assertEqual(self.fake.calls_post,[])

    def test_metadata_object_path_hash_and_kind_mismatch_refuses_before_post(self):
        cid,data=codec.encode_object(
            "evidence",REPO,"7",G,{"note":"synthetic legitimate"})
        for path in (
            "objects/evidence/ff/"+"f"*64+".json",
            codec.object_path("contract",cid),
        ):
            with self.subTest(path=path):
                files=dict(self.fake.files)
                files[path]=data
                bundle=construct(files,(G,),nonce="r32-path-tamper")
                request=replace(self.update,candidate=bundle.commit)
                with self.assertRaises(CliWriterRefused):
                    self.writer()(request,bundle,self.deadline)
                self.assertEqual(self.fake.calls_post,[])

    def test_changed_candidate_blob_rejected_before_post(self):
        malicious=dict(self.fake.bundle.objects)
        key=next(k for k,(kind,_) in malicious.items() if kind=="blob")
        malicious[key]=("blob",b"tampered")
        from metadata_v2 import Bundle
        with self.assertRaises(CliWriterRefused):
            self.writer()(self.update,Bundle(self.fake.bundle.commit,malicious),self.deadline)
        self.assertEqual(self.fake.calls_post,[])
    def test_remote_blob_ack_unknown_never_retries(self):
        self.fake.fail_blob_ack=True
        with self.assertRaisesRegex(CliWriterUncertain,"cli_writer_effect_ack_unknown"):
            self.writer()(self.update,self.fake.bundle,self.deadline)
        self.assertEqual(len(self.fake.calls_post),1)
        self.assertNotIn(IREAD,self.fake.by_endpoint)
    def test_graphql_server_reject_is_unknown_not_success(self):
        self.fake.reject_cas=True
        with self.assertRaises(CliWriterUncertain):
            self.writer()(self.update,self.fake.bundle,self.deadline)
        self.assertEqual(len(self.fake.calls_post),6)
        self.assertNotIn(IREAD,self.fake.by_endpoint)
    def test_current_grant_revoked_after_object_upload_refuses_cas(self):
        count=0
        def grant(*args):
            nonlocal count
            count+=1
            return count==1
        w=GhIssueCASWriter(self.host,self.objects,
                            expected_actor_id=self.approved_actor_id,
                            effect=self.fake.run_effect,
                            authorize=grant,original_intent=self.intent)
        with self.assertRaises(CliWriterUncertain):
            w(self.update,self.fake.bundle,self.deadline)
        self.assertEqual(len(self.fake.calls_post),5)
        self.assertNotIn(IREAD,self.fake.by_endpoint)
    def test_deadline_blocks_no_post(self):
        with self.assertRaises(CliWriterRefused):
            self.writer()(self.update,self.fake.bundle,time.monotonic()-1)
        self.assertEqual(self.fake.calls_post,[])

    def test_effect_deadline_refusal_type_depends_on_prior_effect(self):
        writer=self.writer()
        expired=time.monotonic()-1
        endpoint=f"repos/{REPO}/git/blobs"
        payload={"content":"YQ==","encoding":"base64"}
        with self.assertRaisesRegex(CliWriterRefused,
                                    "cli_writer_effect_capability_missing"):
            writer._post(endpoint,payload,expired,[False])
        with self.assertRaisesRegex(CliWriterUncertain,
                                    "cli_writer_staged_effect_uncertain"):
            writer._post(endpoint,payload,expired,[True])
        self.assertEqual(self.fake.calls_post,[])

    def test_deadline_expires_after_first_blob_readback_reports_uncertain(self):
        writer=self.writer()
        expired=[False]
        real_clock=time.monotonic
        original_reader=GhMetadataObjects.__call__
        first_upload_deadline=real_clock()+15
        def fake_writer_clock():
            return first_upload_deadline+1 if expired[0] else real_clock()
        def expire_after_first_blob_readback(reader,key,maximum,deadline):
            value=original_reader(reader,key,maximum,deadline)
            if value[0]=="blob" and not expired[0]:
                expired[0]=True
            return value
        # Patch only the writer module clock. GhReadOnlyHost and object
        # reader retain the ordinary clock while the synthetic deadline
        # deterministically expires between successful staged effects.
        with patch.object(writer_module,"time",
                          SimpleNamespace(monotonic=fake_writer_clock)):
            with patch.object(GhMetadataObjects,"__call__",
                              expire_after_first_blob_readback):
                with self.assertRaisesRegex(
                        CliWriterUncertain,"cli_writer_staged_effect_uncertain"):
                    writer(self.update,self.fake.bundle,first_upload_deadline)
        self.assertTrue(expired[0])
        self.assertEqual(len(self.fake.calls_post),1)
        self.assertEqual(self.fake.calls_post[0][0][4],
                         f"repos/{REPO}/git/blobs")
        self.assertNotIn(IREAD,self.fake.by_endpoint)

if __name__=="__main__":unittest.main()
