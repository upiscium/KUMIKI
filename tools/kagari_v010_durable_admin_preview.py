#!/usr/bin/env python3
"""Unreleased durability preview for the host-managed KAGARI v0.1 lifecycle.

This module reuses the receipt-verified native Admin's Project/owner/Ask
contracts, but stores ONLY KAGARI-owned attachments in a durable external
private location. Ordinary Project use cannot activate native Tasks or Human Asks. A separately
guarded TEST_ONLY operation is available only for an independently disposable
full-history no-remote Project fixture with exact foreground Human Asks.
This is not production Task qualification.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import subprocess

import kagari_v010_composed_cli as candidate
import kagari_v010_durable_location as location

STORE_NAME="libexec/kagari-v010-composed/kagari_v010_durable_admin_preview.py"
QUALIFICATION="DURABLE_PREVIEW_UNRELEASED"
OPERATIONS={
 "inspect","install","repair","uninstall","show-agents","show-permission",
 "approve-agents","approve-permission","deny-agents","deny-permission",
 "apply-agents","run-task",
}

class PreviewBlocked(ValueError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise PreviewBlocked(reason)


def verify(candidate_root: Path, native_root: Path) -> tuple[dict,object]:
    receipt=candidate.verify(candidate_root,native_root)
    script=candidate_root/STORE_NAME
    require(Path(__file__)==script and script.resolve()==script
            and receipt.get("durable_admin_sha256")==hashlib.sha256(script.read_bytes()).hexdigest(),
            "durable preview code is not the exact immutable candidate member")
    loc_script=candidate_root/"libexec/kagari-v010-composed/kagari_v010_durable_location.py"
    require(loc_script.is_file() and not loc_script.is_symlink()
            and receipt.get("durable_location_sha256")==
            hashlib.sha256(loc_script.read_bytes()).hexdigest(),
            "durable Project location planner differs from the package receipt")
    # Explicitly import only the independently verified Native receipt-bound
    # consent Admin. No copy of arbitrary Project Python is executed.
    import_path=native_root/"libexec/kagari-v010-native-generic/tools"
    require(import_path.is_dir() and not import_path.is_symlink(),
            "native consent Admin source root missing")
    if str(import_path) not in sys.path:
        sys.path.insert(0,str(import_path))
    import kagari_v010_consent_admin as admin
    identity=admin.verify_package(native_root,receipt["native_receipt_sha256"])
    require(identity["qualification"]=="TEST_ONLY_UNQUALIFIED",
            "unexpected Native source authority or qualification")
    return identity,admin


def create_private_location(loc:dict) -> None:
    # Create only KAGARI-owned directories, never Project or unrecognized
    # runtime entries. Existing unknown/misowned state must fail closed.
    namespace=Path(loc["namespace"])
    parent=Path(loc["runtime_parent"])
    runtime=Path(loc["runtime_root"])
    for path in (namespace,parent,runtime):
        if not path.exists() and not path.is_symlink():
            path.mkdir(mode=0o700)
        location.private_if_present(path)


def exact_disposable_fixture(project: Path) -> None:
    """Bound the experimental Task/Ask operations to one independent /tmp clone.

    This is an accidental-error/Project-identity boundary, not a hostile
    same-UID sandbox or a production authority. It never runs git mutation.
    """
    root=Path(tempfile.gettempdir()).resolve()
    parent=project.parent
    require(project.name=="project"
            and parent.parent==root
            and parent.name.startswith("kagari-v010-durable-fixture-")
            and parent.is_dir() and not parent.is_symlink()
            and parent.resolve()==parent,
            "only operator-owned independent /tmp durable-fixture Project allowed")
    info=parent.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.geteuid()
            and stat.S_IMODE(info.st_mode)==0o700
            and not os.path.ismount(parent)
            and not os.path.ismount(project),
            "disposable fixture parent must be private euid-owned 0700")
    require(project.stat().st_dev==parent.stat().st_dev,
            "disposable fixture Project crossed filesystem boundary")
    env={k:v for k,v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_NOSYSTEM="1",
               GIT_OPTIONAL_LOCKS="0",GIT_NO_REPLACE_OBJECTS="1")
    def git(*args):
        try:
            return subprocess.run(
                ["git","--no-optional-locks","-C",str(project),
                 "-c","core.fsmonitor=false",*args],
                env=env,check=True,timeout=15,capture_output=True
            ).stdout.decode().strip()
        except (OSError,UnicodeError,subprocess.SubprocessError) as exc:
            raise PreviewBlocked("disposable fixture Git inventory unavailable") from exc
    require(git("remote")=="" and git("rev-parse","--is-shallow-repository")=="false",
            "disposable full-history independent clone must have no Git remotes")
    require(not (project/".git/objects/info/alternates").exists(),
            "disposable Project must not share Git object storage")


def _bridge_binding(root: Path, native: Path, project: Path,
                    runtime: Path, admin: object, identity: dict) -> tuple[Path, bytes]:
    """Create a canonical, generation-bound proof of the composing wrapper.

    Native Human Ask grants bind the native executable source SHA. This sidecar
    separately ensures a *new* composed wrapper cannot consume an Ask granted
    under an older, non-executing durable Preview without a fresh Install.
    It is not a Human approval: even with the sidecar, grant_status is required.
    """
    owner, state=admin.validate_owner(project,runtime,identity)
    require(owner is not None and state=="INSTALLED_TEST_ONLY",
            "exact Native attachment required before disposable Task authority")
    ask_root=Path(owner["ask_parent"])
    require(ask_root.parent==runtime.parent
            and ask_root.name.startswith("asks-")
            and not ask_root.is_symlink(),
            "installed Ask generation is outside owned runtime")
    stat_project=project.stat()
    receipt=root/"share/kagari-v010-composed/receipt.json"
    raw_receipt=receipt.read_bytes()
    owner_file=runtime/admin.NAME/"owner.json"
    raw_owner=owner_file.read_bytes()
    record={
        "schema_version":1,
        "kind":"KAGARI_DURABLE_DISPOSABLE_TEST_BRIDGE_IDENTITY",
        "candidate_package_root":str(root),
        "candidate_receipt_sha256":hashlib.sha256(raw_receipt).hexdigest(),
        "durable_bridge_source_sha256":hashlib.sha256(
            (root/STORE_NAME).read_bytes()).hexdigest(),
        "native_package_root":str(native),
        "native_receipt_sha256":identity["receipt_sha256"],
        "project_root":str(project),
        "project_dev":stat_project.st_dev,
        "project_ino":stat_project.st_ino,
        "native_owner_sha256":hashlib.sha256(raw_owner).hexdigest(),
        "ask_state_root":str(ask_root),
        "request_ids":owner["request_ids"],
        "test_only":True,
        "human_permission_granted":False,
        "production_activation":False,
    }
    raw=(json.dumps(record,sort_keys=True,ensure_ascii=False,
                    separators=(",",":"))+"\n").encode()
    path=runtime.parent/("durable-bridge-"+ask_root.name+".json")
    return path,raw


def create_bridge_binding(root: Path, native: Path, project: Path,
                          runtime: Path, admin: object, identity: dict) -> None:
    path,raw=_bridge_binding(root,native,project,runtime,admin,identity)
    require(len(raw)<=16384 and not path.exists() and not path.is_symlink(),
            "unexpected existing bridge binding; refuse override")
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as output:
        os.fchmod(output.fileno(),0o600)
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(directory)
    finally:os.close(directory)


def require_bridge_binding(root: Path, native: Path, project: Path,
                           runtime: Path, admin: object, identity: dict) -> None:
    path,expected=_bridge_binding(root,native,project,runtime,admin,identity)
    try:info=path.lstat()
    except FileNotFoundError:
        raise PreviewBlocked(
            "new composing wrapper requires a fresh Install/Ask generation; "
            "uninstall and reinstall the disposable fixture before Human approval") from None
    require(stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid()
            and info.st_nlink==1 and stat.S_IMODE(info.st_mode)==0o600
            and info.st_size<=16384 and path.read_bytes()==expected,
            "composed runtime/Ask binding changed; do not reuse prior approval")


def execute(root:Path,native:Path,project:Path,state_home:Path,
            operation:str, *, disposable_test:bool=False,
            task_spec:Path|None=None,plan:Path|None=None,
            opencode:Path|None=None,output:Path|None=None,
            repository:str="fixture/kagari-native",task:str="271",
            timeout:int=150) -> dict:
    require(operation in OPERATIONS, "unsupported durable lifecycle action")
    ident,admin=verify(root,native)
    loc=location.plan(project,state_home)
    runtime=Path(loc["runtime_root"])
    permission_operations=("approve-agents","approve-permission",
                           "deny-agents","deny-permission","apply-agents")
    task_only=operation=="run-task"
    grant_action=operation in permission_operations
    if task_only or grant_action:
        require(disposable_test,
                ("native Task is not qualified for durable non-disposable consumer use; "
                 if task_only else
                 "Durable Preview may display, but cannot approve or apply, Human Asks before native Task qualification; ")
                + "exact --disposable-task-test-only fixture gate required")
        exact_disposable_fixture(project)
    else:
        require(not disposable_test,
                "disposable Task gate not valid for ordinary lifecycle/read-only inspect")
    if task_only:
        require(task_spec is not None and opencode is not None and output is not None,
                "disposable native Task requires absolute TaskSpec/OpenCode/Evidence output")
    else:
        require(task_spec is None and plan is None and opencode is None and output is None
                and repository=="fixture/kagari-native" and task=="271"
                and timeout==150,
                "model-specific Task arguments are not valid for non-Task operations")
    if not loc["runtime_location_exists"] and operation!="install":
        if operation=="inspect":
            return {"status":"ABSENT","project_mutations":[],
                    "runtime_root":str(runtime)}
        raise PreviewBlocked("durable runtime is absent; install must be explicit")
    if operation=="install":
        create_private_location(loc)
    # Native core API is reused, but NOT its disposable-only main()/roots().
    # This module owns the separate durable path selection and version claims.
    if operation=="inspect":
        result=admin.summary(project,runtime,native,ident)
    elif operation=="install":
        previous=admin.summary(project,runtime,native,ident)
        result=admin.install(project,runtime,native,ident)
        if previous["status"]=="ABSENT":
            create_bridge_binding(root,native,project,runtime,admin,ident)
    elif operation=="repair":
        result=admin.repair(project,runtime,native,ident)
    elif operation=="uninstall":
        result=admin.uninstall(project,runtime,native,ident)
    elif operation=="run-task":
        require_bridge_binding(root,native,project,runtime,admin,ident)
        # Admin revalidates installed owner, all currently required exact
        # Human Ask grants, Project policy, and Task/output before any model.
        # Native runner still sees --disposable-project, never production.
        result=admin.run_task(
            project,runtime,native,ident,spec=task_spec,plan=plan,
            opencode=opencode,output=output,repository=repository,
            task=task,timeout=timeout)
    else:
        if grant_action:
            require_bridge_binding(root,native,project,runtime,admin,ident)
        name=operation.split("-",1)[1]
        action=operation.split("-",1)[0]
        kind={"agents":admin.ask.KIND_AGENTS,
              "permission":admin.ask.KIND_PERMISSION}[name]
        result=admin.decision(project,runtime,native,ident,kind,action)
    return dict(result,
                qualification=QUALIFICATION,
                production_activation=False,
                production_install_qualified=False,
                native_model_task_qualified=False,
                runtime_root=str(runtime))


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation",choices=sorted(OPERATIONS))
    p.add_argument("--project-root",required=True,type=Path)
    p.add_argument("--state-home",type=Path,
                   default=Path(os.environ.get("XDG_STATE_HOME") or
                                Path.home()/".local/state"))
    p.add_argument("--disposable-task-test-only",action="store_true")
    p.add_argument("--task-spec",type=Path)
    p.add_argument("--plan",type=Path)
    p.add_argument("--opencode",type=Path)
    p.add_argument("--output",type=Path)
    p.add_argument("--repository",default="fixture/kagari-native")
    p.add_argument("--task",default="271")
    p.add_argument("--timeout",type=int,default=150)
    try:
        require(argv is not None and len(argv)>=2,
                "this CLI must be invoked by an immutable store-owned wrapper")
        root,native=Path(argv[0]),Path(argv[1])
        args=p.parse_args(argv[2:])
        result=execute(
            root,native,args.project_root,args.state_home,args.operation,
            disposable_test=args.disposable_task_test_only,
            task_spec=args.task_spec,plan=args.plan,opencode=args.opencode,
            output=args.output,repository=args.repository,task=args.task,
            timeout=args.timeout)
        print(json.dumps(result,sort_keys=True))
        return 0 if result.get("status")!="BLOCKED" else 2
    except (PreviewBlocked, candidate.CandidateBlocked, location.LocationBlocked,
            OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
        print(json.dumps({"status":"BLOCKED","qualification":QUALIFICATION,
                          "production_activation":False,
                          "reason":str(exc)[:400]},sort_keys=True))
        return 2

if __name__=="__main__":
    raise SystemExit(main(sys.argv[1:]))
