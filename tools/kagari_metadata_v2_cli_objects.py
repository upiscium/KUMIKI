"""#337 Metadata v2 canonical Git object reader via the existing gh login.

Host-side, GET-only, source-only. No App, PAT, credential store, Git push,
model, ref write or archive deletion. Read raw object bytes from REST
and reconstruct SHA-1 Git syntax; verify EVERY object digest before use.
"""
from __future__ import annotations

import base64
from datetime import datetime
import re
import time

from kagari_metadata_v2_cli_readonly import GhReadOnlyHost,CliReadRefused,need
from metadata_v2 import git_id

_SHA=re.compile(r"[0-9a-f]{40}\Z",re.ASCII)
_SAFE_ACTOR=re.compile(r"[^\r\n\0<>]{1,200}\Z")
_SAFE_EMAIL=re.compile(r"[^\r\n\0<> ]{1,200}\Z")
_EMPTY_TREE="4b825dc642cb6eb9a060e54bf8d69288fbee4904"

def _actor(actor:object)->bytes:
    need(type(actor) is dict,"cli_git_actor_invalid")
    name,email,date=(actor.get("name"),actor.get("email"),actor.get("date"))
    need(type(name) is str and _SAFE_ACTOR.fullmatch(name) is not None
         and type(email) is str and _SAFE_EMAIL.fullmatch(email) is not None
         and type(date) is str and len(date)<80,
         "cli_git_actor_invalid")
    try:
        parsed=datetime.fromisoformat(date.replace("Z","+00:00"))
        offset=parsed.utcoffset()
        need(offset is not None and (offset.total_seconds()%60)==0,
             "cli_git_actor_date_invalid")
        seconds=int(parsed.timestamp())
        minutes=int(offset.total_seconds()//60)
        sign=b"+" if minutes>=0 else b"-"
        offset_text=sign+f"{abs(minutes)//60:02d}{abs(minutes)%60:02d}".encode()
        return (name+" <"+email+"> "+str(seconds)+" ").encode("utf-8")+offset_text
    except (ValueError,TypeError,OverflowError) as e:
        raise CliReadRefused("cli_git_actor_date_invalid") from e

def _tree(obj:dict)->bytes:
    rows=obj.get("tree")
    need(obj.get("truncated") is False and type(rows) is list
         and len(rows)<=4096,"cli_git_tree_unavailable")
    seen=set()
    entries=[]
    for entry in rows:
        need(type(entry) is dict,"cli_git_tree_entry_invalid")
        name,mode,kind,key=(entry.get("path"),entry.get("mode"),
                            entry.get("type"),entry.get("sha"))
        need(type(name) is str and name
             and "/" not in name and "\x00" not in name and name not in (".","..")
             and name not in seen and type(mode) is str
             and type(key) is str and _SHA.fullmatch(key) is not None,
             "cli_git_tree_entry_invalid")
        try:
            name_bytes=name.encode("utf-8","strict")
        except UnicodeError as error:
            raise CliReadRefused("cli_git_tree_entry_invalid") from error
        need(len(name_bytes)<=256,"cli_git_tree_entry_invalid")
        seen.add(name)
        need((kind=="blob" and mode=="100644") or
             (kind=="tree" and mode in ("040000","40000")),
             "cli_git_tree_mode_invalid")
        raw=(b"40000" if kind=="tree" else b"100644")+b" "+name_bytes+b"\0"+bytes.fromhex(key)
        order=name_bytes+(b"/" if kind=="tree" else b"")
        entries.append((order,raw))
    return b"".join(raw for _,raw in sorted(entries))

def _commit(obj:dict,key:str)->bytes:
    tree=obj.get("tree")
    parents=obj.get("parents")
    message=obj.get("message")
    need(type(tree) is dict and type(tree.get("sha")) is str
         and _SHA.fullmatch(tree["sha"]) is not None
         and type(parents) is list and len(parents)<=2
         and type(message) is str and message.startswith("Metadata v2 "),
         "cli_git_commit_shape_invalid")
    try:
        body=message.encode("utf-8","strict")
    except UnicodeError as error:
        raise CliReadRefused("cli_git_commit_shape_invalid") from error
    need(len(body)<=512,"cli_git_commit_shape_invalid")
    parent_ids=[]
    for parent in parents:
        need(type(parent) is dict and type(parent.get("sha")) is str
             and _SHA.fullmatch(parent["sha"]) is not None,
             "cli_git_commit_parent_invalid")
        parent_ids.append(parent["sha"])
    hdr=(b"tree "+tree["sha"].encode()+b"\n"+
         b"".join(b"parent "+p.encode()+b"\n" for p in parent_ids)+
         b"author "+_actor(obj.get("author"))+b"\n"+
         b"committer "+_actor(obj.get("committer"))+b"\n\n")
    # REST may normalize final LF. Accept only the byte shape whose *Git SHA*
    # matches the exact caller-selected OID; never silently adopt new content.
    for candidate in (body,body+b"\n"):
        raw=hdr+candidate
        if git_id("commit",raw)==key:
            return raw
    raise CliReadRefused("cli_git_commit_digest_invalid")

class GhMetadataObjects:
    """Read-only canonical #337 ObjectReader callable, exact selected repo."""
    def __init__(self,host:GhReadOnlyHost)->None:
        need(type(host) is GhReadOnlyHost,"cli_object_host_invalid")
        self.host=host

    def __call__(self,key:str,maximum:int,deadline:float)->tuple[str,bytes]:
        need(self.host._identity is not None,"cli_preflight_required")
        need(type(key) is str and _SHA.fullmatch(key) is not None
             and type(maximum) is int and 0<maximum<=128*1024*1024
             and time.monotonic()<deadline,"cli_object_request_invalid")
        if key==_EMPTY_TREE:
            kind,raw="tree",b""
        else:
            base=f"repos/{self.host.repository}/git"
            kind=None
            raw=None
            # IMPORTANT: GitHub's /git/trees/{sha} also accepts a COMMIT
            # SHA and dereferences its tree, even reporting the input SHA.
            # Probe commits BEFORE trees; otherwise a real commit can be
            # misclassified as a tree. Verify the raw Git digest in all cases.
            # Non-404 errors (auth/transient) are never treated as absence.
            for candidate in ("blobs","commits","trees"):
                response=self.host._api(f"{base}/{candidate}/{key}",deadline,
                                        allow_absent=True)
                if response is None:
                    continue
                need(response.get("sha")==key,"cli_git_object_ref_mismatch")
                if candidate=="blobs":
                    need(response.get("encoding")=="base64"
                         and type(response.get("content")) is str,
                         "cli_git_blob_invalid")
                    try:
                        content="".join(response["content"].split()).encode("ascii")
                        need(len(content)<=((maximum+2)//3)*4+8,
                             "cli_git_blob_limit")
                        raw=base64.b64decode(content,validate=True)
                    except (UnicodeError,ValueError) as e:
                        raise CliReadRefused("cli_git_blob_invalid") from e
                    need(type(response.get("size")) is int
                         and len(raw)==response["size"],"cli_git_blob_size_invalid")
                    kind="blob"
                elif candidate=="trees":
                    raw=_tree(response)
                    kind="tree"
                else:
                    raw=_commit(response,key)
                    kind="commit"
                break
            need(kind is not None and raw is not None,
                 "cli_git_object_absent_or_unreadable")
        need(time.monotonic()<deadline and len(raw)<=maximum,
             "cli_git_object_deadline_or_size")
        need(git_id(kind,raw)==key,"cli_git_object_sha_mismatch")
        return kind,raw
