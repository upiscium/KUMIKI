"""#337/#339 exact Issue Metadata CAS writer candidate for the installed gh login.

SOURCE-ONLY: no live effect runner is supplied by default. A trusted #291
Host must explicitly supply an exact effect runner, current grant and
the original durable #140 IssuePublisher intent. No GitHub App or new
credential system. This does NOT publish Archive J, delete a ref, or
authorize a Task/model/PR. For R30 only, 1 small canonical Issue bundle.
"""
from __future__ import annotations

import base64
from collections.abc import Callable
from types import MappingProxyType
import hashlib
import json
import re
import time

from kagari_metadata_v2_cli_readonly import (
    GhReadOnlyHost,CliReadRefused,_json,need
)
from kagari_metadata_v2_cli_objects import GhMetadataObjects
from kagari_metadata_v2_cli_session import GhCliSession
import metadata_codec as codec
from metadata_v2 import (
    ARCHIVE_REF,Bundle,Update,InvalidMetadata,git_id,issue_ref
)

_OID=re.compile(r"[0-9a-f]{40}\Z")
_NONCE=re.compile(r"[A-Za-z0-9-]{1,64}\Z")
_PATH=re.compile(
    r"(?:tasks/[1-9][0-9]{0,127}/record|"
    r"objects/(?:task-record|contract|evidence|task-view-snapshot)/"
    r"[0-9a-f]{2}/[0-9a-f]{64}\.json)\Z")
_ZERO="0"*40
_ACTOR={"name":"Metadata v2","email":"metadata@invalid","date":"2000-01-01T00:00:00Z"}
_HDR=b"author Metadata v2 <metadata@invalid> 946684800 +0000\ncommitter Metadata v2 <metadata@invalid> 946684800 +0000\n\n"
_MAX_BLOB=80*1024
_MAX_TREE_ENTRIES=256
_MAX_FILES=64
_MAX_TOTAL=768*1024
_EFFECT_LIMIT=2*1024*1024

# The effect runner is a trusted Host-supplied capability, not a CLI command
# name exposed to a Task/model. No default runner is installed in this slice.
Effect=Callable[[list[str],bytes,float],tuple[int,bytes]]
Authorize=Callable[[Update,float],bool]
OriginalIntent=Callable[[Update,float],bool]

class CliWriterRefused(CliReadRefused):
    """No external effect entered, original #337 intent remains authoritative."""

class CliWriterUncertain(CliReadRefused):
    """At least one remote object/ref effect may have occurred: never retry."""

def require(value:bool,code:str)->None:
    if not value:raise CliWriterRefused(code)

def _parse_bundle(update:Update,bundle:Bundle):
    require(type(bundle) is Bundle and bundle.commit==update.candidate
            and type(bundle.objects) in (dict,MappingProxyType),
            "cli_bundle_identity_invalid")
    require(len(bundle.objects)<=512 and bundle.commit in bundle.objects,
            "cli_bundle_size_invalid")
    by_oid=dict(bundle.objects)
    for key,entry in by_oid.items():
        require(type(entry) is tuple and len(entry)==2,
                "cli_bundle_entry_invalid")
        kind,data=entry
        require(type(key) is str and _OID.fullmatch(key) is not None
                and kind in ("blob","tree","commit")
                and type(data) is bytes and len(data)<=_MAX_TOTAL
                and git_id(kind,data)==key,
                "cli_bundle_git_digest_invalid")
    raw=by_oid[bundle.commit]
    require(raw[0]=="commit","cli_bundle_commit_missing")
    commit=raw[1]
    header,sep,body=commit.partition(b"\n\n")
    require(bool(sep) and body.startswith(b"Metadata v2 ")
            and body.endswith(b"\n") and body.count(b"\n")==1
            and re.fullmatch(rb"[A-Za-z0-9-]{1,64}",body[len(b"Metadata v2 "):-1])
            is not None,"cli_commit_message_invalid")
    lines=header.split(b"\n")
    prior=update.expected_issue or update.genesis
    require(len(lines)==4 and lines[0].startswith(b"tree ")
            and lines[1]==b"parent "+prior.encode()
            and lines[2]+b"\n"+lines[3]+b"\n\n"==_HDR,
            "cli_commit_parent_or_actor_invalid")
    tree_oid=lines[0][len(b"tree "):].decode("ascii","ignore")
    require(_OID.fullmatch(tree_oid) is not None,
            "cli_commit_tree_invalid")

    visited=set()
    files={}
    def walk(tree_key:str,prefix:str,depth:int):
        require(depth<=6 and tree_key not in visited and tree_key in by_oid
                and by_oid[tree_key][0]=="tree","cli_bundle_tree_invalid")
        visited.add(tree_key)
        raw_tree=by_oid[tree_key][1]
        pos=0
        parts=0
        while pos<len(raw_tree):
            sp=raw_tree.find(b" ",pos)
            null=raw_tree.find(b"\0",sp+1)
            require(sp>pos and null>sp+1 and null+21<=len(raw_tree),
                    "cli_bundle_tree_bytes_invalid")
            mode=raw_tree[pos:sp]
            try:name=raw_tree[sp+1:null].decode("utf-8","strict")
            except UnicodeDecodeError as e:raise CliWriterRefused("cli_tree_name_invalid") from e
            key=raw_tree[null+1:null+21].hex()
            require(name not in ("",".","..") and "/" not in name
                    and "\x00" not in name and key in by_oid,
                    "cli_tree_path_or_child_invalid")
            path=prefix+name
            if mode==b"40000":
                walk(key,path+"/",depth+1)
            else:
                require(mode==b"100644" and by_oid[key][0]=="blob"
                        and _PATH.fullmatch(path) is not None
                        and path not in files,"cli_blob_path_invalid")
                data=by_oid[key][1]
                require(len(data)<=_MAX_BLOB,"cli_blob_size_invalid")
                files[path]=(key,data)
            parts+=1
            require(parts<=_MAX_TREE_ENTRIES and len(files)<=_MAX_FILES,
                    "cli_tree_entries_limit")
            pos=null+21
    walk(tree_oid,"",0)
    require(1<=len(files)<=_MAX_FILES and
            sum(len(data) for _,data in files.values())<=_MAX_TOTAL,
            "cli_bundle_files_limit")
    # A validated #337 IssuePublisher must never route Issue N's CAS
    # candidate to a tree that carries task pointers for another Issue.
    # Recheck the exact path binding here before any object POST, even
    # if the Host grants this Update. The canonical metadata codec/graph
    # still belongs to #337's prior validation, not to the transport.
    record_path=f"tasks/{update.task}/record"
    require(record_path in files and all(
                not path.startswith("tasks/") or path==record_path
                for path in files),
            "cli_bundle_cross_issue_tree")
    require(re.fullmatch(rb"[0-9a-f]{64}\n",files[record_path][1])
            is not None,"cli_bundle_record_pointer_invalid")
    record_id=files[record_path][1][:-1].decode("ascii")
    require(codec.object_path("task-record",record_id) in files,
            "cli_bundle_record_object_missing")
    # Canonical Git object OIDs alone do not prove that Metadata envelope
    # contents are for this Issue/repository or match their relative path.
    # Reuse #337's existing closed codec at the Host writer boundary.
    for path,(_,data) in files.items():
        if not path.startswith("objects/"):
            continue
        parts=path.split("/")
        require(len(parts)==4 and parts[3].endswith(".json"),
                "cli_bundle_object_path_invalid")
        kind=parts[1]
        content_id=parts[3][:-5]
        try:
            codec.decode_object(data,expected_id=content_id,
                                expected_repository=update.repository,
                                expected_task=update.task,
                                expected_kind=kind)
            require(codec.object_path(kind,content_id)==path,
                    "cli_bundle_object_path_mismatch")
        except (codec.MetadataCodecError,ValueError) as e:
            raise CliWriterRefused("cli_bundle_object_envelope_invalid") from e
    require(visited | {key for key,_ in files.values()} | {bundle.commit}
            ==set(by_oid),"cli_bundle_unreachable_objects")
    return tree_oid,body.decode("ascii"),files

class GhIssueCASWriter:
    """Trusted Host-only, no live effects until injected and authorized.

    Caller: the existing #337 IssuePublisher creates the original fsynced
    intent, holds #291 Main / #256 Attachment gate, validates current
    Contract/Task and installs three independent Host callbacks below.
    """
    def __init__(self,host:GhReadOnlyHost,objects:GhMetadataObjects,
                 *,expected_actor_id:int, session:GhCliSession|None=None,
                 authorize:Authorize|None=None,
                 original_intent:OriginalIntent|None=None)->None:
        require(type(host) is GhReadOnlyHost
                and type(objects) is GhMetadataObjects
                and objects.host is host
                and type(expected_actor_id) is int and expected_actor_id>0,
                "cli_writer_host_invalid")
        # Never accept an independent effect runner: reads and writes must
        # use one Host-owned captured credential. Ambient gh auth switch
        # cannot rebind the exact transport identity between observations.
        if session is not None:
            require(type(session) is GhCliSession
                    and host._session is session
                    and getattr(host._runner,"__self__",None) is session
                    and session.repo==host.repository
                    and session.actor_id==expected_actor_id,
                    "cli_writer_session_mismatch")
        self.host,self.objects=host,objects
        self._expected_actor_id=expected_actor_id
        self._session=session
        self._effect_runner=session.effect if session is not None else None
        self._authorize=authorize
        self._original_intent=original_intent

    def _guard(self,update:Update,deadline:float)->None:
        try:
            current=self.host.preflight(deadline)
        except CliReadRefused as error:
            raise CliWriterRefused("cli_writer_bound_identity_unavailable") from error
        require(current.actor_id==self._expected_actor_id,
                "cli_writer_actor_changed")
        require(self._authorize is not None and self._original_intent is not None,
                "cli_writer_original_intent_or_grant_required")
        try:
            granted=self._authorize(update,deadline) is True
            original=self._original_intent(update,deadline) is True
        except Exception as error:
            raise CliWriterRefused("cli_writer_host_authority_unavailable") from error
        require(granted and original and time.monotonic()<deadline,
                "cli_writer_original_intent_or_grant_required")
        # A Host callback may yield to another local CLI actor. Re-read the
        # admitted identity after callbacks as well as before them.
        try:
            after_callbacks=self.host.preflight(deadline)
        except CliReadRefused as error:
            raise CliWriterRefused("cli_writer_bound_identity_unavailable") from error
        require(after_callbacks.actor_id==self._expected_actor_id,
                "cli_writer_actor_changed")
        require(self.host.observe(ARCHIVE_REF,deadline)==update.expected_archive
                and self.host.observe(update.ref,deadline)==update.expected_issue,
                "cli_writer_expected_refs_changed")

    def _post(self,endpoint:str,payload:dict,deadline:float,
              effects_started:list[bool],*,graphql:bool=False)->dict:
        # A pre-call deadline refusal is certain only before the *first*
        # effect. Once any object POST is attempted, preserve original
        # durable intent and report UNKNOWN even if the next POST did not run.
        if not (time.monotonic()<deadline and callable(self._effect_runner)):
            if effects_started[0]:
                raise CliWriterUncertain("cli_writer_staged_effect_uncertain")
            raise CliWriterRefused("cli_writer_effect_capability_missing")
        data=json.dumps(payload,sort_keys=True,separators=(",",":")).encode("ascii")
        if len(data)>_EFFECT_LIMIT:
            if effects_started[0]:
                raise CliWriterUncertain("cli_writer_staged_effect_uncertain")
            raise CliWriterRefused("cli_writer_effect_payload_limit")
        argv=(["gh","api","graphql","--input","-"] if graphql
              else ["gh","api","-X","POST",endpoint,"--input","-"])
        try:
            effects_started[0]=True
            rc,raw=self._effect_runner(argv,data,min(20.,deadline-time.monotonic()))
            response=_json(raw)
        except Exception as e:
            raise CliWriterUncertain("cli_writer_effect_ack_unknown") from e
        if (time.monotonic()>=deadline or rc!=0 or type(response) is not dict
                or response.get("errors")):
            raise CliWriterUncertain("cli_writer_effect_ack_unknown")
        return response

    def __call__(self,update:Update,bundle:Bundle,deadline:float)->bool:
        require(type(update) is Update and type(update.task) is str,
                "cli_writer_issue_scope_invalid")
        try:
            selected_ref=issue_ref(update.task)
        except InvalidMetadata as error:
            raise CliWriterRefused("cli_writer_issue_scope_invalid") from error
        require(update.repository==self.host.repository
                and update.ref==selected_ref
                and update.retention_policy is None
                and all(type(x) is str and _OID.fullmatch(x) is not None
                        for x in (update.genesis,update.expected_archive,update.candidate))
                and (update.expected_issue is None or
                     type(update.expected_issue) is str and
                     _OID.fullmatch(update.expected_issue) is not None)
                and time.monotonic()<deadline,
                "cli_writer_issue_scope_invalid")
        require(callable(self._effect_runner) and
                callable(self._authorize) and
                callable(self._original_intent),
                "cli_writer_uninstalled")
        tree_oid,message,files=_parse_bundle(update,bundle)
        # First effect guard uses the immutable Host-authorized actor, not
        # an identity silently refreshed by gh auth in another operation.
        self._guard(update,deadline)
        # Invocation-local effect marker; never carry UNKNOWN into another
        # authorized call. The #140 fsynced intent remains the recovery SoT.
        effects_started=[False]
        uploaded=set()
        for path,(key,data) in sorted(files.items()):
            if key in uploaded:continue
            out=self._post("repos/"+self.host.repository+"/git/blobs",{
                "content":base64.b64encode(data).decode("ascii"),"encoding":"base64"},
                deadline,effects_started)
            if out.get("sha")!=key:
                raise CliWriterUncertain("cli_writer_blob_mismatch")
            try:
                require(self.objects(key,len(data),deadline)==("blob",data),
                        "cli_writer_blob_readback_mismatch")
            except Exception as e:
                raise CliWriterUncertain("cli_writer_blob_readback_unknown") from e
            uploaded.add(key)

        entries=[{"path":p,"mode":"100644","type":"blob","sha":oid}
                 for p,(oid,_) in sorted(files.items())]
        tree=self._post("repos/"+self.host.repository+"/git/trees",
                        {"tree":entries},deadline,effects_started)
        if tree.get("sha")!=tree_oid:
            raise CliWriterUncertain("cli_writer_tree_mismatch")
        try:
            kind,raw=self.objects(tree_oid,_MAX_TOTAL,deadline)
            require(kind=="tree" and git_id("tree",raw)==tree_oid,
                    "cli_writer_tree_readback_mismatch")
        except Exception as e:
            raise CliWriterUncertain("cli_writer_tree_readback_unknown") from e

        commit=self._post("repos/"+self.host.repository+"/git/commits",{
            "tree":tree_oid,
            "parents":[update.expected_issue or update.genesis],
            "message":message,
            "author":_ACTOR,"committer":_ACTOR},deadline,effects_started)
        if commit.get("sha")!=update.candidate:
            raise CliWriterUncertain("cli_writer_commit_mismatch")
        try:
            require(self.objects(bundle.commit,len(bundle.objects[bundle.commit][1]),deadline)==bundle.objects[bundle.commit],
                    "cli_writer_commit_readback_mismatch")
        except Exception as e:
            raise CliWriterUncertain("cli_writer_commit_readback_unknown") from e

        # On GitHub, CAS is one server-side multi-ref update. The ordinary
        # Archive is an expected-old no-op guard; only this Issue ref moves.
        # This is a GitHub-specific transport, not a general Git guarantee.
        try:
            self._guard(update,deadline)
        except Exception as e:
            raise CliWriterUncertain("cli_writer_pre_cas_authority_changed") from e
        mutation={
            "query":"mutation R30CAS($input:UpdateRefsInput!){updateRefs(input:$input){clientMutationId}}",
            "variables":{"input":{
                "repositoryId":self.host.graphql_id,
                "clientMutationId":hashlib.sha256(
                    (update.repository+"\n"+update.ref+"\n"+update.candidate).encode()
                ).hexdigest()[:32],
                "refUpdates":[
                    {"name":update.ref,
                     "beforeOid":update.expected_issue or _ZERO,
                     "afterOid":update.candidate,"force":False},
                    {"name":ARCHIVE_REF,
                     "beforeOid":update.expected_archive,
                     "afterOid":update.expected_archive,"force":False},
                ],
            }},
        }
        outcome=self._post("graphql",mutation,deadline,effects_started,graphql=True)
        if (type(outcome.get("data")) is not dict or
                type(outcome["data"].get("updateRefs")) is not dict or
                outcome["data"]["updateRefs"].get("clientMutationId") !=
                mutation["variables"]["input"]["clientMutationId"]):
            raise CliWriterUncertain("cli_writer_cas_ack_unknown")
        try:
            current=self.host.preflight(deadline)
            need(current.actor_id==self._expected_actor_id and
                 self.host.observe(ARCHIVE_REF,deadline)==update.expected_archive
                 and self.host.observe(update.ref,deadline)==update.candidate,
                 "cli_writer_post_cas_ref_mismatch")
        except Exception as e:
            raise CliWriterUncertain("cli_writer_post_cas_readback_unknown") from e
        return True
