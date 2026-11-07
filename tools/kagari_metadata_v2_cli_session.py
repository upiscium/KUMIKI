"""#339 R35: invocation-scoped transport bound to *existing* gh CLI login.

The trusted Host captures the already selected CLI token once, validates its
numeric GitHub actor using that *same token*, and uses GH_TOKEN (documented
gh precedence) for every subsequent GET/Blob/Tree/Commit/GraphQL operation.

This is NOT a GitHub App, PAT manager, credential service, model capability,
runtime activation, or permission grant. Never log/repr/persist a token or
pass it in argv. A Host-owned session must be closed after the bounded
operation; best-effort Python references cannot guarantee memory zeroization.
"""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import shutil
import subprocess
import tempfile
import time

_MAX_OUT=2*1024*1024
_REPO=re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\Z",re.ASCII)
_OID=re.compile(r"[0-9a-f]{40}\Z",re.ASCII)
_TOKEN=re.compile(r"[\x21-\x7e]{16,4096}\Z",re.ASCII)
Invoke=Callable[[list[str],bytes|None,float,dict[str,str]],tuple[int,bytes]]


class GhSessionRefused(ValueError):
    """No new authorization; remote effect may be uncertain if already sent."""


def _require(condition:bool,reason:str)->None:
    if not condition:
        raise GhSessionRefused(reason)


def _spawn(argv:list[str],body:bytes|None,timeout:float,
           env:dict[str,str])->tuple[int,bytes]:
    """Bounded stdout, no stderr, no shell or command-line token."""
    try:
        with tempfile.TemporaryFile() as output:
            completed=subprocess.run(
                argv,input=body,stdin=subprocess.DEVNULL if body is None else None,
                stdout=output,stderr=subprocess.DEVNULL,env=env,check=False,
                timeout=max(.01,min(timeout,20.)))
            output.seek(0)
            read=output.read(_MAX_OUT+1)
        _require(len(read)<=_MAX_OUT,"cli_session_output_limit")
    except (OSError,subprocess.TimeoutExpired) as error:
        raise GhSessionRefused("cli_session_process_unavailable") from error
    return completed.returncode,read


def _capture_token_pipe(argv:list[str],body:bytes|None,timeout:float,
                        env:dict[str,str])->tuple[int,bytes]:
    """Read credential stdout through an in-memory OS pipe only.

    Never redirect the bearer to a regular, unlinked, spooled or tmp file.
    Bound reads (<=4098 bytes) and timeout; kill/reap on every early exit.
    """
    _require(body is None and type(timeout) in (int,float) and timeout>0,
             "cli_session_capture_request_invalid")
    deadline=time.monotonic()+max(.01,min(timeout,20.))
    process=None
    try:
        process=subprocess.Popen(
            argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,env=env,bufsize=0,close_fds=True)
        _require(process.stdout is not None,"cli_session_capture_pipe_unavailable")
        captured=bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ)
            while True:
                remaining=deadline-time.monotonic()
                _require(remaining>0,"cli_session_capture_deadline")
                events=selector.select(remaining)
                _require(bool(events),"cli_session_capture_deadline")
                chunk=os.read(process.stdout.fileno(),
                              max(1,4099-len(captured)))
                if not chunk:
                    break
                captured.extend(chunk)
                _require(len(captured)<=4098,"cli_session_capture_limit")
        remaining=deadline-time.monotonic()
        _require(remaining>0,"cli_session_capture_deadline")
        returncode=process.wait(timeout=remaining)
        return returncode,bytes(captured)
    except (OSError,subprocess.TimeoutExpired) as error:
        raise GhSessionRefused("cli_session_capture_unavailable") from error
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()


class GhCliSession:
    """One trusted Host invocation, one immutable bearer context.

    A token from the user's already installed GitHub CLI login is used in
    subprocess environment *only*. No token is published or exposed by any
    public accessor. The test runner may be injected by trusted unit tests,
    but all commands still receive the identical captured GH_TOKEN.
    """

    __slots__=("_repo","_actor_id","_gh","_token","_invoke")

    def __init__(self,repo:str,actor_id:int,gh:str,token:str,
                 invoke:Invoke)->None:
        _require(type(repo) is str and _REPO.fullmatch(repo) is not None
                 and type(actor_id) is int and actor_id>0
                 and type(gh) is str and gh.startswith("/")
                 and type(token) is str and _TOKEN.fullmatch(token) is not None
                 and callable(invoke),
                 "cli_session_selection_invalid")
        self._repo=repo
        self._actor_id=actor_id
        self._gh=gh
        self._token=token
        self._invoke=invoke

    def __repr__(self)->str:
        return "<GhCliSession (private invocation credential)>"

    @property
    def repo(self)->str:
        return self._repo

    @property
    def actor_id(self)->int:
        return self._actor_id

    @classmethod
    def capture(cls,repo:str,expected_actor_id:int,deadline:float,
                *,gh_binary:str|None=None,
                invoke:Invoke|None=None)->GhCliSession:
        """Capture the ALREADY logged-in gh token once; no login/refresh.

        Host must supply its independently approved expected numeric actor.
        The capture command is never exposed to Task/model input.
        """
        _require(type(repo) is str and _REPO.fullmatch(repo) is not None
                 and type(expected_actor_id) is int and expected_actor_id>0
                 and time.monotonic()<deadline,
                 "cli_session_admission_invalid")
        binary=gh_binary if gh_binary is not None else shutil.which("gh")
        _require(type(binary) is str and binary.startswith("/"),
                 "cli_session_binary_unavailable")
        try:
            home=pwd.getpwuid(os.getuid()).pw_dir
        except KeyError as error:
            raise GhSessionRefused("cli_session_home_unavailable") from error
        env=dict(os.environ)
        env["GH_HOST"]="github.com"
        env["GH_PROMPT_DISABLED"]="1"
        env["GH_NO_UPDATE_NOTIFIER"]="1"
        env.pop("GH_DEBUG",None)
        env.pop("DEBUG",None)
        token_invoker=_capture_token_pipe if invoke is None else invoke
        try:
            status,raw=token_invoker(
                [binary,"auth","token","--hostname","github.com"],None,
                min(20.,deadline-time.monotonic()),env)
        except Exception as error:
            raise GhSessionRefused("cli_session_capture_unavailable") from error
        _require(status==0 and type(raw) is bytes and len(raw)<=4098,
                 "cli_session_capture_unavailable")
        try:
            token=raw.rstrip(b"\r\n").decode("ascii","strict")
        except UnicodeError as error:
            raise GhSessionRefused("cli_session_capture_invalid") from error
        _require(_TOKEN.fullmatch(token) is not None,
                 "cli_session_capture_invalid")
        session=cls(repo,expected_actor_id,binary,token,
                    _spawn if invoke is None else invoke)
        try:
            rc,body=session.read(["gh","api","-X","GET","user"],
                                 max(.01,min(20.,deadline-time.monotonic())))
            result=json.loads(body.decode("utf-8"))
            _require(rc==0 and type(result) is dict
                     and type(result.get("id")) is int
                     and result["id"]==expected_actor_id,
                     "cli_session_actor_mismatch")
            _require(time.monotonic()<deadline,"cli_session_admission_deadline")
        except Exception as error:
            session.close()
            if isinstance(error,GhSessionRefused):raise
            raise GhSessionRefused("cli_session_identity_unavailable") from error
        return session

    def _env(self)->dict[str,str]:
        _require(type(self._token) is str and bool(self._token),
                 "cli_session_closed")
        try:
            home=pwd.getpwuid(os.getuid()).pw_dir
        except KeyError as error:
            raise GhSessionRefused("cli_session_home_unavailable") from error
        # Never inherit ambient GH_TOKEN/GITHUB_TOKEN/GH_CONFIG_DIR or GH_DEBUG:
        # a concurrent 'gh auth switch' cannot alter this invocation context.
        return {
            "HOME":home,"PATH":str(Path(self._gh).parent),
            "GH_TOKEN":self._token,"GH_HOST":"github.com",
            "GH_PROMPT_DISABLED":"1","GH_NO_UPDATE_NOTIFIER":"1",
            "GIT_TERMINAL_PROMPT":"0","NO_COLOR":"1","LC_ALL":"C",
            "GIT_CONFIG_NOSYSTEM":"1","GIT_CONFIG_GLOBAL":os.devnull,
            "GIT_CONFIG_COUNT":"0",
        }

    def _call(self,argv:list[str],payload:bytes|None,timeout:float):
        _require(type(argv) is list and all(type(x) is str for x in argv)
                 and type(timeout) in (int,float) and timeout>0,
                 "cli_session_request_invalid")
        env=self._env()
        try:
            return self._invoke([self._gh,*argv[1:]],payload,timeout,env)
        except GhSessionRefused:raise
        except Exception as error:
            raise GhSessionRefused("cli_session_request_unavailable") from error

    def read(self,argv:list[str],timeout:float)->tuple[int,bytes]:
        _require(len(argv)==5 and argv[:4]==["gh","api","-X","GET"],
                 "cli_session_read_not_allowed")
        endpoint=argv[4]
        _require(endpoint=="user" or endpoint=="repos/"+self._repo
                 or endpoint.startswith("repos/"+self._repo+"/"),
                 "cli_session_read_repo_mismatch")
        return self._call(argv,None,timeout)

    def effect(self,argv:list[str],body:bytes,timeout:float)->tuple[int,bytes]:
        _require(type(body) is bytes and len(body)<=_MAX_OUT,
                 "cli_session_effect_body_invalid")
        graphql=argv==["gh","api","graphql","--input","-"]
        post=(len(argv)==7 and argv[:4]==["gh","api","-X","POST"]
              and argv[5:]==["--input","-"]
              and argv[4] in {
                  f"repos/{self._repo}/git/blobs",
                  f"repos/{self._repo}/git/trees",
                  f"repos/{self._repo}/git/commits",
              })
        _require(graphql or post,"cli_session_effect_not_allowed")
        return self._call(argv,body,timeout)

    def close(self)->None:
        # Drop the only strong reference held by this session. Python cannot
        # guarantee removal of prior immutable strings from process memory.
        self._token=None

    def __enter__(self)->GhCliSession:
        return self

    def __exit__(self,*_)->None:
        self.close()
