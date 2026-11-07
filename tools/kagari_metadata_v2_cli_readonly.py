"""#337/#339: exact GitHub CLI read-only Host transport candidate.

Uses the **installed CLI user's** existing `gh` auth: no App/PAT, login flow,
secret manager, daemon, side-channel permission state or effects. Source-only;
it does NOT implement or authorize GitHub metadata/PR writes, Task dispatch,
archive delete, or v2 activation.

The trusted Host (not the Task/model) supplies the exact expected repository
and may inject a runner in disposable unit tests. The returned source
identities are observations, NOT Issue/Contract/Task authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import subprocess
import tempfile
import time
from collections.abc import Callable

_REPO=re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\Z",re.ASCII)
_REF=re.compile(r"refs/heads/(?:kagari/metadata|kagari-metadata/[1-9][0-9]{0,127}|main)\Z",re.ASCII)
_OID=re.compile(r"[a-f0-9]{40}\Z",re.ASCII)
_MAX_JSON=2*1024*1024
Run=Callable[[list[str],float],tuple[int,bytes]]

class CliReadRefused(ValueError):
    """Missing/foreign/uncertain GitHub CLI observations; never retry writes."""

def need(value:bool,code:str)->None:
    if not value: raise CliReadRefused(code)

def _run(argv:list[str],timeout:float)->tuple[int,bytes]:
    """No shell, no injected token/Authorization header, no stderr disclosure."""
    try:
        # Cap Python allocations before JSON decode; no token or stderr logs.
        with tempfile.TemporaryFile() as output:
            done=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=output,
                                stderr=subprocess.DEVNULL,check=False,
                                timeout=max(.01,min(timeout,20.)))
            output.seek(0)
            body=output.read(_MAX_JSON+1)
        need(len(body)<=_MAX_JSON,"cli_json_limit")
    except (OSError,subprocess.TimeoutExpired) as e:
        raise CliReadRefused("cli_read_unavailable") from e
    return done.returncode,body

def _strict(pairs:list[tuple[str,object]])->dict:
    result={}
    for key,value in pairs:
        need(key not in result,"cli_duplicate_json_key")
        result[key]=value
    return result

def _json(raw:bytes)->object:
    need(type(raw) is bytes and len(raw)<=_MAX_JSON,"cli_json_limit")
    try:
        return json.loads(raw.decode("utf-8","strict"),
                          object_pairs_hook=_strict,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError,ValueError,TypeError,RecursionError) as e:
        if isinstance(e,CliReadRefused): raise
        raise CliReadRefused("cli_json_invalid") from e

@dataclass(frozen=True,slots=True)
class CliRepo:
    name:str
    rest_id:int
    graphql_id:str
    actor:str
    actor_id:int
    default_oid:str

class GhReadOnlyHost:
    """One exact repo selection, app-level authority remains outside this type."""
    def __init__(self,repository:str,repo_id:int,graphql_id:str,
                 *,runner:Run=_run)->None:
        need(type(repository) is str and len(repository)<=200
             and _REPO.fullmatch(repository) is not None
             and type(repo_id) is int and repo_id>0
             and type(graphql_id) is str and 0<len(graphql_id)<=160
             and callable(runner),"cli_host_selection_invalid")
        self.repository=repository
        self.repo_id=repo_id
        self.graphql_id=graphql_id
        self._runner=runner
        self._identity:CliRepo|None=None

    def _api(self,endpoint:str,deadline:float,*,allow_absent:bool=False):
        need(time.monotonic()<deadline,"cli_deadline")
        rc,data=self._runner(["gh","api","-X","GET",endpoint],
                             min(20.,deadline-time.monotonic()))
        need(time.monotonic()<deadline,"cli_deadline")
        parsed=_json(data)
        if rc:
            if allow_absent and type(parsed) is dict and parsed.get("status")=="404":
                return None
            raise CliReadRefused("cli_api_unavailable")
        need(type(parsed) is dict,"cli_api_object_required")
        return parsed

    def preflight(self,deadline:float)->CliRepo:
        """Authenticate and pin owner CLI user + REST/GraphQL repo identity."""
        actor=self._api("user",deadline)
        repo=self._api("repos/"+self.repository,deadline)
        name=repo.get("full_name")
        assert actor is not None and repo is not None
        need(name==self.repository
             and type(repo.get("id")) is int and repo["id"]==self.repo_id
             and repo.get("node_id")==self.graphql_id
             and repo.get("default_branch")=="main"
             and repo.get("archived") is False
             and type(actor.get("id")) is int and actor["id"]>0
             and type(actor.get("login")) is str and len(actor["login"])<=80,
             "cli_repo_or_actor_identity_mismatch")
        # Host must still independently obtain exact approved Issue/grant.
        head=self.observe("refs/heads/main",deadline)
        need(head is not None and _OID.fullmatch(head) is not None,
             "cli_main_unavailable")
        value=CliRepo(self.repository,self.repo_id,self.graphql_id,
                      actor["login"],actor["id"],head)
        self._identity=value
        return value

    def observe(self,ref:str,deadline:float)->str|None:
        """GitHub REST direct ref only; 404 is absence, all other error => deny."""
        need(type(ref) is str and _REF.fullmatch(ref) is not None,
             "cli_ref_not_allowed")
        need(self._identity is not None or ref=="refs/heads/main",
             "cli_preflight_required")
        got=self._api("repos/"+self.repository+"/git/ref/"+ref.removeprefix("refs/"),
                      deadline,allow_absent=True)
        if got is None:
            return None
        obj=got.get("object")
        need(got.get("ref")==ref and type(obj) is dict
             and obj.get("type")=="commit"
             and type(obj.get("sha")) is str and _OID.fullmatch(obj["sha"]) is not None,
             "cli_ref_identity_invalid")
        return obj["sha"]

    def metadata_observer(self,ref:str,deadline:float):
        """Exact #337 MetadataV2 Observer callback, never a writer/grant."""
        from metadata_v2 import RefState
        need(ref=="refs/heads/kagari/metadata" or
             (type(ref) is str and ref.startswith("refs/heads/kagari-metadata/")),
             "cli_metadata_ref_required")
        return RefState(ref,self.observe(ref,deadline),True)

    def issue(self,n:int,deadline:float)->dict:
        need(self._identity is not None,"cli_preflight_required")
        need(type(n) is int and n>0,"cli_issue_invalid")
        value=self._api(f"repos/{self.repository}/issues/{n}",deadline)
        need(type(value.get("number")) is int
             and value["number"]==n and "pull_request" not in value
             and value.get("state") in ("open","closed")
             and type(value.get("title")) is str and type(value.get("body")) in (str,type(None)),
             "cli_issue_identity_invalid")
        return {"number":n,"title":value["title"],"body":value.get("body") or "",
                "state":value["state"],
                "actor":self._identity.actor,"repo_id":self.repo_id}

    def readback(self,deadline:float)->dict:
        need(self._identity is not None,"cli_preflight_required")
        return {"name":self.repository,
                "rest_id":self.repo_id,
                "graphql_id":self.graphql_id,
                "actor":self._identity.actor,
                "actor_id":self._identity.actor_id,
                "main_oid":self.observe("refs/heads/main",deadline),
                "archive_oid":self.observe("refs/heads/kagari/metadata",deadline)}
