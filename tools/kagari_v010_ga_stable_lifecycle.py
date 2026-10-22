#!/usr/bin/env python3
"""KAGARI v0.1.0 GA candidate — isolated Work Unit profile; NOT YET GA QUALIFIED.

Attaches non-invasively to an ordinary Git consumer. Model effects are possible
only in a newly generated, independently cloned Work Unit with separate local
Human Task approval and original Native Human Asks as needed. Never directly
edits, commits or pushes the attached Project. This is a declared RC profile;
it does not rewrite TEST_ONLY evidence to claim production qualification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any

VERSION = "0.1.0"
PROFILE = "GA_BOUNDED_ISOLATED_WORKUNIT"
NAME = "kagari-v010-ga-stable"
PREFIX = "attach-"
RECEIPT = "share/kagari-v010-ga/receipt.json"
SRC_FILE = "libexec/kagari-v010-ga/kagari_v010_ga_stable_lifecycle.py"
NATIVE_MAIN = "libexec/kagari-v010-native-generic/tools/kagari_v010_native_generic_task.py"
STORE_NAME = re.compile(r"[0-9a-df-np-sv-z]{32}-kagari-0\.1\.0\Z")
PROJECT_ID = re.compile(r"attach-[0-9a-f]{24}\Z")
JOB_ID = re.compile(r"job-[0-9a-f]{24}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
ASK_KIND = "GA_EXACT_MANAGED_WORKUNIT_TASK"
STATE_OWNER = "KAGARI_GA_PROJECT_ATTACHMENT"


class RCBlocked(ValueError):
    """One or more exact authority/ownership constraints did not hold."""


def need(ok: bool, reason: str) -> None:
    if not ok:
        raise RCBlocked(reason)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(data: Any) -> bytes:
    return (json.dumps(data, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n").encode()


def bytes_regular(file: Path, *, max_bytes: int = 1024 * 1024,
                  exact_mode: int | None = None) -> bytes:
    try:
        info = file.lstat()
    except FileNotFoundError:
        raise RCBlocked("required exact file missing: " + str(file)) from None
    need(stat.S_ISREG(info.st_mode) and info.st_size <= max_bytes,
         "unsafe file type/size: " + str(file))
    if exact_mode is not None:
        need(stat.S_IMODE(info.st_mode) == exact_mode,
             "unsafe file mode: " + str(file))
    if exact_mode == 0o600:
        need(info.st_uid == os.geteuid() and info.st_nlink == 1,
             "unknown owner or alias-linked authority record")
    raw = file.read_bytes()
    need(len(raw) <= max_bytes, "file exceeded bounded read: " + str(file))
    return raw


def load(file: Path, *, exact_mode: int | None = None) -> dict:
    raw = bytes_regular(file, exact_mode=exact_mode, max_bytes=512*1024)
    def no_duplicate(pairs):
        obj = {}
        for key,value in pairs:
            need(key not in obj,"duplicate RC record key: "+key)
            obj[key]=value
        return obj
    try:
        obj = json.loads(raw,object_pairs_hook=no_duplicate,
                         parse_constant=lambda _:need(False,"nonfinite RC JSON"))
    except (ValueError,UnicodeError) as exc:
        raise RCBlocked("invalid RC authority JSON") from exc
    need(type(obj) is dict and (exact_mode != 0o600 or canonical(obj)==raw),
         "RC authority record is not canonical")
    return obj


def save_new(file: Path, payload: bytes, *, size: int = 512*1024) -> None:
    need(len(payload) <= size and not file.exists() and not file.is_symlink(),
         "RC record not fresh/bounded: "+str(file))
    fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        os.fchmod(f.fileno(),0o600)
        f.write(payload);f.flush();os.fsync(f.fileno())


def private(directory: Path, *, create: bool=False) -> Path:
    need(directory.is_absolute() and directory.resolve(strict=False)==directory
         and not directory.is_symlink(),
         "private RC path must be canonical")
    if create and not directory.exists():
        parent=directory.parent
        need(parent.is_dir() and not parent.is_symlink() and parent.resolve()==parent
             and parent.stat().st_uid==os.geteuid(),
             "unexpected RC parent before directory creation")
        directory.mkdir(mode=0o700)
    need(directory.is_dir() and not directory.is_symlink()
         and directory.stat().st_uid==os.geteuid()
         and stat.S_IMODE(directory.stat().st_mode)==0o700
         and not os.path.ismount(directory),
         "RC owned directory must be euid-owned mode 0700")
    return directory


def git_env() -> dict:
    env={k:v for k,v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_NOSYSTEM="1",
               GIT_OPTIONAL_LOCKS="0",GIT_NO_REPLACE_OBJECTS="1")
    return env


def git(project: Path, *args: str, check:bool=True,
        timeout:int=25) -> subprocess.CompletedProcess:
    argv=["git","--no-optional-locks","-C",str(project),
          "-c","core.fsmonitor=false",*args]
    try:
        completed=subprocess.run(argv,env=git_env(),check=False,
                                 capture_output=True,timeout=timeout)
    except (OSError,subprocess.SubprocessError) as exc:
        raise RCBlocked("Git Project operation unavailable: "+str(args[:2])) from exc
    if check:
        need(completed.returncode==0,
             "Git read/operation failed: "+str(args[:2]))
    return completed


def verify_package(rc: Path) -> tuple[dict,Path,Path,object,object,object]:
    """Pin every RC executable and historical native TEST_ONLY component."""
    need(rc.is_absolute() and rc.parent==Path("/nix/store")
         and STORE_NAME.fullmatch(rc.name) is not None
         and rc.is_dir() and not rc.is_symlink()
         and stat.S_IMODE(rc.stat().st_mode)==0o555,
         "RC executable must be from an exact immutable Nix Store output")
    need(Path(__file__).resolve()==rc/SRC_FILE,
         "RC CLI cannot execute outside the selected immutable package")
    receipt=load(rc/RECEIPT,exact_mode=0o444)
    for key,value in {
        "version":VERSION,"qualification":"GA_BOUNDED_PROFILE_QUALIFIED",
        "publication_attestation":"EXTERNAL_UNVERIFIED","production_activation":False,
        "release_channel":"stable",
        "managed_isolated_Work_Unit_supported":True,
        "direct_live_Project_Task_supported":False,
        "root":str(rc)
    }.items():
        need(type(receipt.get(key)) is type(value) and receipt[key]==value,
             "incorrect RC identity/qualification claim: "+key)
    need(receipt.get("cli_sha256")==sha(bytes_regular(rc/SRC_FILE,max_bytes=512*1024,
                                                       exact_mode=0o444)),
         "RC executable bytes do not match receipt")
    # Qualifies only the restricted integrated Work Unit profile, not bare Native.
    qualification_file=rc/"share/kagari-v010-ga/bounded-qualification.json"
    qualification_raw=bytes_regular(qualification_file,max_bytes=128*1024,
                                     exact_mode=0o444)
    need(receipt.get("bounded_qualification_sha256")==sha(qualification_raw)
         and receipt.get("bounded_profile_id")==
              "linux-nix-owned-ordinary-git-isolated-workunit-v1",
         "GA bounded qualification basis not hash-bound to receipt")
    basis=load(qualification_file,exact_mode=0o444)
    need(basis.get("kind")=="KAGARI_0_1_0_BOUNDED_GA_QUALIFICATION_BASIS"
         and basis.get("qualification")=="GA_BOUNDED_PROFILE_QUALIFIED"
         and basis.get("native_component_qualification")=="TEST_ONLY_UNQUALIFIED"
         and basis.get("production_activation") is False
         and basis.get("scope_id")==receipt.get("bounded_profile_id"),
         "GA bounded source evidence, Native scope or acceptance identity mismatch")
    # Pre-model OpenCode setup is restricted to the managed Work Unit and
    # bound to exact Nix source, a generated npm lock and Bun executable.
    bootstrap_path=rc/"libexec/kagari-v010-ga/kagari_v010_ga_opencode_bootstrap.py"
    product_archive_path=rc/"libexec/kagari-v010-ga/kagari_v010_ga_product_archive.py"
    bootstrap_lock=rc/"share/kagari-v010-ga/opencode-plugin-package-lock.json"
    bootstrap_node_lock=rc/"share/kagari-v010-ga/opencode-node-modules-package-lock.json"
    need(receipt.get("opencode_bootstrap_sha256")==
         sha(bytes_regular(bootstrap_path,max_bytes=512*1024,exact_mode=0o444))
         and receipt.get("durable_product_archive_sha256")==
         sha(bytes_regular(product_archive_path,max_bytes=512*1024,exact_mode=0o444))
         and receipt.get("opencode_plugin_lock_sha256")==
         sha(bytes_regular(bootstrap_lock,max_bytes=128*1024,exact_mode=0o444))
         and receipt.get("opencode_node_modules_lock_sha256")==
         sha(bytes_regular(bootstrap_node_lock,max_bytes=128*1024,exact_mode=0o444))
         and receipt.get("opencode_plugin_version")=="1.18.35"
         and receipt.get("bun_version")=="1.3.13"
         and receipt.get("opencode_dependencies_bootstrapped_before_Native_snapshot") is True
         and receipt.get("native_Project_scope_inventory_relaxed") is False,
         "unreviewed RC OpenCode cache bootstrap source/package authority")
    bun=Path(str(receipt.get("bun_executable","")))
    need(re.fullmatch(
         r"/nix/store/[0-9a-df-np-sv-z]{32}-bun-1\.3\.13/bin/bun",str(bun)) is not None
         and bun.is_file() and not bun.is_symlink() and os.access(bun,os.X_OK),
         "exact immutable RC-selected Bun prewarm executable unavailable")
    composed=Path(receipt["composed_root"])
    native=Path(receipt["native_root"])
    for root in (composed,native):
        need(root.parent==Path("/nix/store") and root.is_dir() and not root.is_symlink(),
             "RC component root is not immutable Nix Store output")
    need(sha(bytes_regular(composed/"share/kagari-v010-composed/receipt.json",
                           exact_mode=0o444))==receipt["composed_receipt_sha256"],
         "composed dependency receipt drift")
    need(sha(bytes_regular(native/"share/kagari-v010-native-generic/receipt.json",
                           exact_mode=0o444))==receipt["native_receipt_sha256"],
         "native dependency receipt drift")
    native_receipt=load(native/"share/kagari-v010-native-generic/receipt.json",
                        exact_mode=0o444)
    need(native_receipt["qualification"]=="TEST_ONLY_UNQUALIFIED"
         and native_receipt["released"] is False
         and native_receipt["production_activation"] is False,
         "historic native TEST_ONLY dependency does not match conservative RC profile")
    need(native_receipt["tool_sha256"]["kagari_v010_native_generic_task.py"]==
         sha(bytes_regular(native/NATIVE_MAIN,max_bytes=512*1024,exact_mode=0o444)),
         "native Task executable changed")
    composed_lib=composed/"libexec/kagari-v010-composed"
    native_lib=native/"libexec/kagari-v010-native-generic/tools"
    for path in (composed_lib,native_lib):
        need(path.is_dir() and not path.is_symlink(),"required verified dependency absent")
    sys.path.insert(0,str(composed_lib))
    sys.path.insert(0,str(native_lib))
    import kagari_v010_durable_location as location
    import kagari_v010_consent_admin as admin
    import kagari_v010_general_task_spec as task_spec
    # The existing Native source integrity contract is independently checked
    # before accepting any dependency import; do not import Project Python.
    for name,module in (
            ("kagari_v010_durable_location.py",location),
            ("kagari_v010_consent_admin.py",admin),
            ("kagari_v010_general_task_spec.py",task_spec)):
        origin=Path(module.__file__).resolve()
        expected=composed_lib/name if name.startswith("kagari_v010_durable_") else native_lib/name
        need(origin==expected and expected.is_file(),"untrusted RC component module origin")
    native_identity=admin.verify_package(native,receipt["native_receipt_sha256"])
    # Python -I never implicitly imports modules beside the invoked script.
    # Add ONLY the verified immutable RC member location to the lookup path.
    sys.path.insert(0,str(rc/"libexec/kagari-v010-ga"))
    import kagari_v010_ga_opencode_bootstrap as rc_prewarm
    need(Path(rc_prewarm.__file__).resolve()==bootstrap_path,
         "imported RC dependency bootstrap is not the reviewed immutable source")
    rc_prewarm.validate_pinned_lock(bootstrap_lock.read_bytes())
    rc_prewarm.validate_node_lock(bootstrap_node_lock.read_bytes(),
                                  bootstrap_lock.read_bytes())
    return receipt,composed,native,location,admin,task_spec


def project_info(location: object,project: Path,state_home: Path) -> tuple[dict,Path,Path]:
    location.project_identity(project)
    need(state_home.is_absolute() and state_home.resolve(strict=False)==state_home
         and state_home.is_dir() and not state_home.is_symlink()
         and state_home.stat().st_uid==os.geteuid(),
         "state-home must be owned absolute existing directory")
    need(not state_home.is_relative_to(project)
         and not project.is_relative_to(state_home),
         "RC state storage must be outside consumer Project")
    info=location.project_identity(project)
    digest=sha((f"{info['root']}\0{info['device']}\0{info['inode']}").encode())[:24]
    namespace=state_home/NAME
    attachment=namespace/(PREFIX+digest)
    return info,namespace,attachment


def owner(project: Path,rc: Path,receipt: dict, project_identity:dict)->dict:
    return {
        "schema_version":1,"kind":STATE_OWNER,"version":VERSION,
        "profile":PROFILE,
        "root":str(project),
        "device":project_identity["device"],"inode":project_identity["inode"],
        "ga_package_root":str(rc),"ga_receipt_sha256":sha(bytes_regular(rc/RECEIPT,exact_mode=0o444)),
        "created_head":project_identity["head"],
        "install_nonce":secrets.token_hex(16),
        "model_Task_authority":"PER_JOB_EXACT_LOCAL_TTY_APPROVAL_REQUIRED",
        "source_publication_authority":False,
        "release_authority":False,
    }


def attachment_state(path:Path,expected:dict)->tuple[str,dict|None]:
    if not path.exists() and not path.is_symlink():
        return "ABSENT",None
    private(path)
    entries={x.name for x in path.iterdir()}
    need(entries in ({"owner.json"},{"owner.json","package"}),
         "unknown files in RC attachment; preserve for review")
    record=load(path/"owner.json",exact_mode=0o600)
    for key in ("schema_version","kind","version","profile",
                "root","device","inode","ga_package_root","ga_receipt_sha256",
                "model_Task_authority","source_publication_authority","release_authority"):
        need(record.get(key)==expected[key]
             and type(record.get(key))==type(expected[key]),
             "RC attachment owner or package binding differs: "+key)
    need(type(record.get("install_nonce")) is str
         and re.fullmatch(r"[0-9a-f]{32}",record["install_nonce"]) is not None,
         "RC attachment nonce unrecognized")
    head=record.get("created_head")
    need(type(head) is str and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}",head),
         "RC install original Git revision unavailable")
    if "package" not in entries:return "MISSING_OWNED_PACKAGE_LINK",record
    link=path/"package"
    need(link.is_symlink() and os.readlink(link)==expected["ga_package_root"],
         "unknown/redirected immutable RC package symlink")
    return "INSTALLED_GA_CANDIDATE",record


def namespace_install(namespace:Path,attachment:Path) -> None:
    private(namespace,create=True)
    need(not attachment.exists() and not attachment.is_symlink(),"RC location already exists")
    attachment.mkdir(mode=0o700)
    private(attachment)


def installed_view(state:str,project:Path,namespace:Path,attachment:Path,
                   rc:Path,stored:dict|None)->dict:
    return {
        "status":state,"version":VERSION,"qualification":"GA_BOUNDED_PROFILE_QUALIFIED",
        "supported_profile":PROFILE,"publication_attestation":"EXTERNAL_UNVERIFIED",
        "production_activation":False,"direct_Project_edits_allowed":False,
        "Task_mode":"isolated_independent_Git_Work_Unit_only",
        "Project_root":str(project),
        "ga_state_namespace":str(namespace),
        "attachment_root":str(attachment),
        "package_root":str(rc),
        "installed_nonce":stored["install_nonce"] if stored else None,
        "Task_approval_required_per_run":True,
        "Project_mutations":[],
    }


def history_root(namespace:Path) -> Path:
    private(namespace)
    root=namespace/"history"
    return private(root,create=True)


def job_folder(namespace:Path,jobid:str) -> Path:
    need(JOB_ID.fullmatch(jobid) is not None,"invalid RC job identifier")
    return private(namespace/"history"/jobid)


def verified_attachment(path:Path,expected:dict)->dict:
    state,installed=attachment_state(path,expected)
    need(state=="INSTALLED_GA_CANDIDATE" and installed is not None,
         "exact RC attachment required (repair if known incomplete)")
    return installed


def now()->str:
    return datetime.now(timezone.utc).isoformat()


def create_task_request(rc:Path,receipt:dict,source:Path,ident:dict,
                        attached:dict,project:Path,prepared:dict,
                        repo:str,task:str,model_path:Path)->dict:
    frozen=prepared["task_spec_sha256"]
    return {
        "schema_version":1,"kind":ASK_KIND,"ga_version":VERSION,
        "profile":PROFILE,
        "Project_root":str(source),
        "Project_device":ident["device"],"Project_inode":ident["inode"],
        "Project_head":ident["head"],
        "GA_package_root":str(rc),
        "GA_receipt_sha256":attached["ga_receipt_sha256"],
        "GA_install_nonce":attached["install_nonce"],
        "workunit_clone":str(project),
        "workunit_head":ident["head"],
        "job_id":prepared["job_id"],
        "TaskSpec_sha256":frozen,
        "TaskSpec_allowed_paths":prepared["allowed_paths"],
        "TaskSpec_tests":prepared["tests"],
        "repository":repo,"task":task,
        "model_executable":str(model_path),
        "model_executable_sha256":sha(bytes_regular(model_path,max_bytes=75*1024*1024)),
        "scope":"ONLY_THIS_BOUNDED_WORKUNIT_LOCAL_PRODUCT_COMMIT_NO_PUSH",
        "human_response":"LOCAL_FOREGROUND_TTY_EXACT_TASK_CONSENT",
        "permissions":["two_native_parallel_child_edits","host_declared_tests",
                       "host_product_only_commit","local_v4_Evidence_readback"],
        "production_Project_mutations":False,
        "Project_source_remote_push_authorized":False,
        "model_provider_identity_cryptographically_attested":False,
        "nonce":secrets.token_hex(16),"created":now(),
    }


def prepare_task(rc:Path,composed:Path,native:Path,admin:object,
                 task_spec:object,receipt:dict,namespace:Path,attached:dict,ident:dict,
                 project:Path,spec_file:Path,model:Path,
                 repo:str,task:str)->dict:
    # An independently managed Clone consumes the committed HEAD version.
    # Preserve all user dirty/staged content in original, but refuse silently
    # ignoring dirty instruction/model configuration that would alter authority.
    need(type(repo) is str and re.fullmatch(
         r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*",repo),
         "explicit canonical repository identity required")
    need(type(task) is str and re.fullmatch(r"[1-9][0-9]*",task),
         "positive numeric native Task ID required")
    need(model.is_absolute() and model.resolve()==model and model.is_file()
         and not model.is_symlink() and os.access(model,os.X_OK),
         "exact model executable must be approved absolute binary")
    need(model.stat().st_size<=75*1024*1024,
         "model executable exceeds current RC identity budget")
    need(not git(project,"diff","HEAD","--name-only","--","AGENTS.md","opencode.json").stdout.strip(),
         "dirty Project AGENTS/opencode must be reviewed before source clone")
    spec=task_spec.load_task_spec(spec_file,project)
    need(spec["base_revision"]==ident["head"],
         "TaskSpec baseline must match exact attached Git Project HEAD")
    digest=sha(bytes_regular(spec_file,max_bytes=1024*1024))
    hist=history_root(namespace)
    jobid="job-"+secrets.token_hex(12)
    job=hist/jobid
    need(not job.exists(),"job identifier collision")
    job.mkdir(mode=0o700)
    private(job)
    save_new(job/"task-spec.json",canonical(spec))
    # Clone via Git protocol (no object-alternates/hardlinks); no Push remote.
    fixture=Path(tempfile.mkdtemp(prefix="kagari-v010-ga-work-",dir="/tmp"))
    need(stat.S_IMODE(fixture.stat().st_mode)==0o700,"independent Work Unit fixture is not private")
    clone=fixture/"project"
    process=git(project,"rev-parse","HEAD")
    need(process.stdout.decode().strip()==ident["head"],
         "source HEAD changed just before independent clone")
    try:
        subprocess.run(["git","clone","--no-local","--quiet",str(project),str(clone)],
                       check=True,capture_output=True,timeout=90,env=git_env())
        subprocess.run(["git","-C",str(clone),"remote","remove","origin"],
                       check=True,capture_output=True,timeout=20,env=git_env())
    except (OSError,subprocess.SubprocessError) as exc:
        raise RCBlocked("independent Work Unit Clone failed") from exc
    need(git(clone,"rev-parse","HEAD").stdout.decode().strip()==ident["head"]
         and git(clone,"remote").stdout.strip()==b""
         and git(clone,"rev-parse","--is-shallow-repository").stdout.strip()==b"false"
         and not (clone/".git/objects/info/alternates").exists(),
         "independent Work Unit source identity invalid")
    need(not git(clone,"status","--porcelain=v1").stdout.strip(),
         "independent committed Work Unit Clone unexpectedly dirty")
    need(not (clone/"AGENTS.md").is_file() or
         sha(bytes_regular(clone/"AGENTS.md",max_bytes=200000))==
         sha(bytes_regular(project/"AGENTS.md",max_bytes=200000)),
         "Project AGENTS modified during clone preparation")
    runtime_parent=Path(tempfile.mkdtemp(prefix="kagari-v010-consent-install-",dir="/tmp"))
    runtime=runtime_parent/"runtime"
    runtime.mkdir(mode=0o700)
    native_identity=admin.verify_package(native,receipt["native_receipt_sha256"])
    admin.roots(clone,runtime)
    ask_result=admin.install(clone,runtime,native,native_identity)
    need(ask_result["status"]=="INSTALLED_TEST_ONLY"
         and ask_result["ready_for_model_task"] in (True,False),
         "managed Work Unit native attachment not exact")
    installed_summary=admin.summary(clone,runtime,native,native_identity)
    need(installed_summary["status"]=="INSTALLED_TEST_ONLY"
         and "ask_status" in installed_summary,"native ask generation missing")
    planned={
        "schema_version":1,"kind":"KAGARI_GA_WORKUNIT_JOB",
        "job_id":jobid,
        "state":"PENDING_HUMAN_GA_TASK_AND_NATIVE_ASKS",
        "Project_root":str(project),
        "Project_head":ident["head"],
        "Project_device":ident["device"],"Project_inode":ident["inode"],
        "Project_git_index_sha256":sha(bytes_regular(project/".git/index",max_bytes=4*1024*1024)),
        "Project_AGENTS_sha256": sha(bytes_regular(project/"AGENTS.md",max_bytes=200000)) if (project/"AGENTS.md").is_file() else None,
        "Project_opencode_sha256":sha(bytes_regular(project/"opencode.json",max_bytes=200000)) if (project/"opencode.json").is_file() else None,
        "ga_install_nonce":attached["install_nonce"],
        "ga_receipt_sha256":attached["ga_receipt_sha256"],
        "native_receipt_sha256":receipt["native_receipt_sha256"],
        "workunit_clone":str(clone),
        "workunit_private_parent":str(fixture),
        "native_runtime":str(runtime),
        "native_runtime_parent":str(runtime_parent),
        "native_ask_state_root":installed_summary.get("ask_state_root"),
        "native_ask_ids":{key:item["request_id"] for key,item in
                          installed_summary["ask_status"].items()},
        "task_spec_sha256":sha((job/"task-spec.json").read_bytes()),
        "allowed_paths":spec["allowed_paths"],
        "tests":spec["tests"],
        "repository":repo,"task":task,
        "model_executable":str(model),
        "model_executable_sha256":sha(bytes_regular(model,max_bytes=75*1024*1024)),
        "created":now(),
    }
    request=create_task_request(rc,receipt,project,ident,attached,clone,
                                planned,repo,task,model)
    request["request_id"]=sha(canonical(request))[:24]
    save_new(job/"task-request.json",canonical(request))
    save_new(job/"job.json",canonical(planned))
    return {
        "status":"GA_WORKUNIT_AWAITING_EXACT_HUMAN_APPROVAL",
        "qualification":"GA_BOUNDED_PROFILE_QUALIFIED",
        "job_id":jobid,
        "ga_task_request_id":request["request_id"],
        "Project_root":str(project),
        "source_head":ident["head"],
        "managed_Work_Unit":str(clone),
        "TaskSpec_sha256":planned["task_spec_sha256"],
        "allowed_paths":planned["allowed_paths"],
        "native_required_asks":installed_summary.get("required_asks",[]),
        "native_ask_ids":planned["native_ask_ids"],
        "native_ask_state_root":planned["native_ask_state_root"],
        "Project_source_mutated":False,
        "model_invoked":False,
        "source_remote_push_authorized":False,
    }


def verify_job(jobdir:Path,rc:Path,attached:dict,project:Path,
               ident:dict,receipt:dict,*,allow_source_head_move:bool=False)->tuple[dict,dict]:
    record=load(jobdir/"job.json",exact_mode=0o600)
    request=load(jobdir/"task-request.json",exact_mode=0o600)
    need(record.get("kind")=="KAGARI_GA_WORKUNIT_JOB"
         and record.get("schema_version")==1
         and record.get("job_id")==jobdir.name
         and request.get("kind")==ASK_KIND
         and request.get("request_id")==sha(canonical(
             {k:v for k,v in request.items() if k!="request_id"}))[:24],
         "RC Work Unit job/request identity invalid")
    bound_head=record.get("Project_head") if allow_source_head_move else ident["head"]
    need(type(bound_head) is str and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}",bound_head) is not None,
         "historic Task baseline is not a full Git object ID")
    for key,val in {
        "Project_root":str(project),"Project_device":ident["device"],
        "Project_inode":ident["inode"],"Project_head":bound_head,
        "ga_install_nonce":attached["install_nonce"],
        "ga_receipt_sha256":attached["ga_receipt_sha256"],
        "native_receipt_sha256":receipt["native_receipt_sha256"],
    }.items():
        need(record.get(key)==val,"RC job original Project/package identity drift: "+key)
    for key,val in {
        "Project_root":str(project),
        "Project_device":ident["device"],"Project_inode":ident["inode"],
        "Project_head":bound_head,"GA_package_root":str(rc),
        "GA_install_nonce":attached["install_nonce"],
        "GA_receipt_sha256":attached["ga_receipt_sha256"],
        "job_id":jobdir.name,
        "TaskSpec_sha256":record["task_spec_sha256"]
    }.items():
        need(request.get(key)==val,"RC Task consent request invalid: "+key)
    spec=bytes_regular(jobdir/"task-spec.json",exact_mode=0o600,max_bytes=1024*1024)
    need(sha(spec)==record["task_spec_sha256"],
         "RC TaskSpec snapshot was altered after preparation")
    parsed=json.loads(spec)
    need(parsed["allowed_paths"]==record["allowed_paths"]
         and parsed["tests"]==record["tests"]
         and request.get("TaskSpec_allowed_paths")==record["allowed_paths"]
         and request.get("TaskSpec_tests")==record["tests"]
         and request.get("model_executable")==record["model_executable"]
         and request.get("model_executable_sha256")==record["model_executable_sha256"]
         and request.get("repository")==record["repository"]
         and request.get("task")==record["task"],
         "TaskSpec/model/repository authority drift after preparation")
    clone=Path(record["workunit_clone"])
    need(clone.parent.parent==Path("/tmp")
         and clone.parent.name.startswith("kagari-v010-ga-work-")
         and stat.S_IMODE(clone.parent.stat().st_mode)==0o700
         and clone.is_dir() and not clone.is_symlink()
         and git(clone,"remote").stdout.strip()==b""
         and git(clone,"rev-parse","--is-shallow-repository").stdout.strip()==b"false",
         "RC managed Work Unit clone no longer isolated")
    runtime=Path(record["native_runtime"])
    need(runtime.parent.parent==Path("/tmp")
         and runtime.parent.name.startswith("kagari-v010-consent-install-")
         and runtime.is_dir(),"Native Work Unit runtime directory moved")
    need(not project.is_relative_to(clone.parent)
         and not clone.is_relative_to(project),
         "Work Unit overlaps original Project")
    return record,request


def approved(jobdir:Path,request:dict)->dict:
    decision=load(jobdir/"task-approval.json",exact_mode=0o600)
    need(decision.get("schema_version")==1
         and decision.get("kind")==ASK_KIND
         and decision.get("request_id")==request["request_id"]
         and decision.get("request_sha256")==sha(canonical(request))
         and decision.get("decision")=="APPROVED"
         and decision.get("origin")=="LOCAL_FOREGROUND_TTY_NOT_REMOTE_VERIFIED",
         "RC Task grant is missing/stale or not approved")
    return decision


def task_details(jobdir:Path,record:dict,request:dict,
                 native:Path,admin:object,receipt:dict)->dict:
    clone=Path(record["workunit_clone"])
    runtime=Path(record["native_runtime"])
    identity=admin.verify_package(native,receipt["native_receipt_sha256"])
    status=admin.summary(clone,runtime,native,identity)
    return {
       "status":"GA_JOB_READY" if status.get("ready_for_model_task") else "GA_JOB_AWAITING_NATIVE_ASKS",
       "job_id":jobdir.name,
       "Task_request_id":request["request_id"],
       "Task_approved":(jobdir/"task-approval.json").is_file(),
       "native_attached":status["status"],
       "native_required_asks":status.get("required_asks",[]),
       "native_ask_status":status.get("ask_status",{}),
       "native_ask_state_root":status.get("ask_state_root"),
       "workunit":str(clone),
       "source_project":record["Project_root"],
       "original_head":record["Project_head"],
       "run_dispatched":(jobdir/"run-dispatch.json").exists(),
       "Product_commit":None,
    }


def approve_task(jobdir:Path,record:dict,request:dict,admin:object)->dict:
    need(not (jobdir/"task-approval.json").exists()
         and not (jobdir/"run-dispatch.json").exists(),
         "RC Task already approved/dispatched; cannot reuse")
    # The same independently hardened local TTY gate as Native Human Asks.
    admin.ask.require_foreground_terminal()
    print("\nKAGARI RC exact managed Work Unit Task consent",flush=True)
    for field in ("Project_root","Project_head","GA_package_root",
                  "GA_receipt_sha256","TaskSpec_sha256","TaskSpec_allowed_paths",
                  "TaskSpec_tests","repository","task","workunit_clone"):
        print(f"{field}: {request[field]}",flush=True)
    print("Original Project is READ-ONLY. Model/Product effects occur only in the owned Clone.",flush=True)
    phrase="APPROVE "+request["request_id"]
    print("Enter exactly "+phrase+" to authorize this one bounded Work Unit:",flush=True)
    need(input("> ").strip()==phrase,
         "Task consent phrase did not match; no approval")
    save_new(jobdir/"task-approval.json",canonical({
        "schema_version":1,"kind":ASK_KIND,
        "request_id":request["request_id"],"request_sha256":sha(canonical(request)),
        "decision":"APPROVED",
        "origin":"LOCAL_FOREGROUND_TTY_NOT_REMOTE_VERIFIED",
        "answered_at":now()
    }))
    return {"status":"GA_EXACT_TASK_APPROVED",
            "job_id":jobdir.name,"request_id":request["request_id"],
            "remote_user_identity_attested":False,
            "Project_mutations":[]}


def show_native_ask(jobdir:Path,record:dict,native:Path,
                    admin:object,receipt:dict,
                    action:str,kind:str)->dict:
    runtime=Path(record["native_runtime"])
    clone=Path(record["workunit_clone"])
    identity=admin.verify_package(native,receipt["native_receipt_sha256"])
    return admin.decision(clone,runtime,native,identity,kind,action)


def execute_workunit(jobdir:Path,record:dict,request:dict,
                     project:Path,rc:Path,native:Path,admin:object,
                     receipt:dict)->dict:
    # No direct original Project mutation or remote Push; executable Native
    # runner retains full tested model/Git/Contract/Plan/Evidence safeguards.
    approved(jobdir,request)
    need(not (jobdir/"run-dispatch.json").exists(),"RC job is one-shot; new consent required")
    need(sha(bytes_regular(Path(record["model_executable"]),max_bytes=75*1024*1024))
         ==record["model_executable_sha256"],
         "configured OpenCode binary differs from exact approved Task request")
    need(git(project,"rev-parse","HEAD").stdout.decode().strip()==record["Project_head"],
         "original Project HEAD moved after Task consent")
    need(sha(bytes_regular(project/".git/index",max_bytes=4*1024*1024))==
         record["Project_git_index_sha256"],
         "original Project staged Git index changed since preparation")
    for rel,key in (("AGENTS.md","Project_AGENTS_sha256"),
                    ("opencode.json","Project_opencode_sha256")):
        p=project/rel
        actual=sha(bytes_regular(p,max_bytes=200000)) if p.is_file() else None
        need(actual==record[key],"original Project instructions changed after Task preparation")
    clone=Path(record["workunit_clone"])
    runtime=Path(record["native_runtime"])
    native_identity=admin.verify_package(native,receipt["native_receipt_sha256"])
    owner,state=admin.validate_owner(clone,runtime,native_identity)
    need(state=="INSTALLED_TEST_ONLY" and owner is not None,
         "native Work Unit attachment missing or unexpected")
    summary=admin.summary(clone,runtime,native,native_identity)
    need(summary["ready_for_model_task"] is True,
         "native Human Asks must be exact and active before model dispatch")
    # A valid, scoped AGENTS grant intentionally changes tracked AGENTS.md in
    # this disposable Work Unit. Bind that ONE allowed pre-existing Git delta
    # to the exact immutable Native Human Ask generation before prewarm.
    approved_agents_postimage_sha256=None
    if admin.ask.KIND_AGENTS in owner["required_asks"]:
        target=admin.owner_ask_root(owner,admin.ask.KIND_AGENTS)
        agents_request=admin.ask.load_request(target)
        grant=admin.ask.grant_status(
            target,clone,native/admin.MAIN,admin.ask.KIND_AGENTS)
        need(grant["status"]=="ACTIVE_SCOPED_GRANT_TEST_ONLY"
             and agents_request["request_id"]==grant["request_id"]
             ==owner["request_ids"][admin.ask.KIND_AGENTS]
             and type(agents_request.get("patch_sha256")) is str
             and re.fullmatch(r"[0-9a-f]{64}",agents_request["patch_sha256"]) is not None,
             "RC prewarm requires the exact current activated Native AGENTS grant")
        approved_agents_postimage_sha256=agents_request["patch_sha256"]
        need(sha(bytes_regular(clone/"AGENTS.md",max_bytes=200000))
             ==approved_agents_postimage_sha256,
             "approved scoped AGENTS postimage changed before bootstrap")
    need(not (jobdir/"evidence").exists(),"Job Evidence must be fresh")
    save_new(jobdir/"run-dispatch.json",canonical({
        "schema_version":1,"kind":"KAGARI_GA_ONE_SHOT_DISPATCH",
        "Task_request_id":request["request_id"],
        "Task_approval_sha256":sha(bytes_regular(
            jobdir/"task-approval.json",exact_mode=0o600)),
        "TaskSpec_sha256":record["task_spec_sha256"],
        "native_root":str(native),"native_receipt_sha256":receipt["native_receipt_sha256"],
        "Project_head":record["Project_head"],
        "dispatched_at":now()
    }))
    # Stage pinned OpenCode dependency bytes before the unchanged Native
    # inventory baseline. Do not ignore .opencode paths in Native physical
    # postcondition. Any bootstrap failure leaves the one-shot marker and
    # blocks model dispatch rather than silently retrying.
    import kagari_v010_ga_opencode_bootstrap as rc_prewarm
    warmed=rc_prewarm.prewarm(
        clone,jobdir,rc_root=rc,receipt=receipt,
        native_root=native,executable=Path(record["model_executable"]),
        approved_agents_postimage_sha256=approved_agents_postimage_sha256)
    save_new(jobdir/"bootstrap-receipt.json",canonical(warmed))
    post_warm=admin.summary(clone,runtime,native,native_identity)
    need(post_warm["ready_for_model_task"] is True,
         "Native exact Human Ask was revoked while bootstrapping OpenCode")
    need(git(project,"rev-parse","HEAD").stdout.decode().strip()==record["Project_head"]
         and sha(bytes_regular(project/".git/index",max_bytes=4*1024*1024))==
         record["Project_git_index_sha256"],
         "Project HEAD/index mutated during isolated OpenCode bootstrap")
    result=admin.run_task(
        clone,runtime,native,native_identity,
        spec=jobdir/"task-spec.json",plan=None,
        opencode=Path(record["model_executable"]),
        output=jobdir/"evidence",
        repository=record["repository"],
        task=record["task"],timeout=240)
    need(result.get("native_task_exit") is not None,"Native runtime returned no Task exit")
    native_result=result.get("native_task_result")
    success=(result["status"]=="REAL_NATIVE_GENERIC_TASK_TEST_ONLY_PASS"
             and result["native_task_exit"]==0
             and type(native_result) is dict
             and native_result.get("fresh_process_readback",{}).get("status")==
             "FRESH_PROCESS_NATIVE_READBACK_PASS")
    need(git(project,"rev-parse","HEAD").stdout.decode().strip()==record["Project_head"],
         "original Project changed during RC isolated Task")
    need(sha(bytes_regular(project/".git/index",max_bytes=4*1024*1024))==
         record["Project_git_index_sha256"],
         "original Project index changed while RC Task ran")
    for rel,key in (("AGENTS.md","Project_AGENTS_sha256"),
                    ("opencode.json","Project_opencode_sha256")):
        path=project/rel
        actual=sha(bytes_regular(path,max_bytes=200000)) if path.is_file() else None
        need(actual==record[key],
             "original Project instructions changed while RC Task ran")
    doc={
       "schema_version":1,"kind":"KAGARI_GA_MANAGED_WORKUNIT_TASK_RESULT",
       "status":"GA_MANAGED_WORKUNIT_TASK_PASS" if success else "BLOCKED",
       "qualification":"GA_BOUNDED_PROFILE_QUALIFIED",
       "job_id":jobdir.name,
       "original_Project_root":str(project),
       "original_Project_HEAD_unchanged":True,
       "native_runtime_receipt_sha256":receipt["native_receipt_sha256"],
       "Native_status":result.get("status"),
       "Native_exit":result["native_task_exit"],
       "Product_commit":native_result.get("product_commit") if native_result else None,
       "native_v4_Evidence_readback":native_result.get("fresh_process_readback") if native_result else None,
       "native_model_Result":native_result,
       "Product_changes_in_clone_only":True,
       "OpenCode_dependency_bootstrap_sha256":sha(bytes_regular(
           jobdir/"bootstrap-receipt.json",exact_mode=0o600)),
       "Native_Project_strict_scope_guard_retained":True,
       "source_remote_push":False,
       "publication_attestation":"EXTERNAL_UNVERIFIED","recorded_at":now()
    }
    # GA-preparation slice: a successful Product commit must become durable
    # *before* any host Task PASS is persisted. The archive has an independent
    # exact SHA receipt and is entirely outside the disposable Git Clone.
    if success:
        import kagari_v010_ga_product_archive as product_archive
        exported=product_archive.archive(jobdir,result_override=doc)
        doc["durable_Product_artifact"]=exported
        # A failed second independent readback must never leave a persisted
        # Task PASS. Verify the exact completed host payload before commit.
        need(product_archive.verify(jobdir,result_override=doc)==exported,
             "GA Product archive cannot be verified before Task PASS")
    save_new(jobdir/"result.json",canonical(doc))
    return doc


def revoke_job_if_installed(jobdir:Path,record:dict,native:Path,
                            admin:object,receipt:dict)->dict:
    clone=Path(record["workunit_clone"])
    runtime=Path(record["native_runtime"])
    if not clone.is_dir() or not runtime.is_dir():
        raise RCBlocked("missing Work Unit/owned runtime: preserve history for operator review")
    identity=admin.verify_package(native,receipt["native_receipt_sha256"])
    # On an already uninstalled job, no Ask authority remains.
    return admin.uninstall(clone,runtime,native,identity)


def operate(args,rc:Path,receipt:dict,native:Path,location:object,
            admin:object,task_spec:object)->dict:
    if args.operation=="version":
        return {"version":VERSION,"qualification":"GA_BOUNDED_PROFILE_QUALIFIED",
                "profile":PROFILE,"publication_attestation":"EXTERNAL_UNVERIFIED",
                "production_activation":False,"release_channel":"stable",
                "native_component_qualification":"TEST_ONLY_UNQUALIFIED"}
    need(args.project_root is not None,"explicit --project-root required")
    project=Path(args.project_root)
    ident,namespace,attachment=project_info(location,project,args.state_home)
    expected=owner(project,rc,receipt,ident)
    # Owner generation nonce differs by design; not among immutable expected
    # binding fields until load and validation.
    state,existing=attachment_state(attachment,expected)
    if args.operation in ("inspect","doctor"):
        return installed_view(state,project,namespace,attachment,rc,existing)
    if args.operation=="install":
        if existing is not None:
            need(state=="INSTALLED_GA_CANDIDATE","known partial state requires repair")
            return installed_view(state,project,namespace,attachment,rc,existing)
        namespace_install(namespace,attachment)
        payload=owner(project,rc,receipt,ident)
        save_new(attachment/"owner.json",canonical(payload))
        (attachment/"package").symlink_to(rc,target_is_directory=True)
        state,record=attachment_state(attachment,expected)
        need(state=="INSTALLED_GA_CANDIDATE","RC install readback failed")
        return installed_view(state,project,namespace,attachment,rc,record)
    if args.operation=="repair":
        need(existing is not None,"cannot repair absent RC attach")
        if state=="MISSING_OWNED_PACKAGE_LINK":
            (attachment/"package").symlink_to(rc,target_is_directory=True)
        state,record=attachment_state(attachment,expected)
        need(state=="INSTALLED_GA_CANDIDATE","RC repair readback failed")
        return installed_view(state,project,namespace,attachment,rc,record)
    installed=verified_attachment(attachment,expected)
    if args.operation=="prepare-task":
        need(args.task_spec is not None and args.opencode is not None,
             "TaskSpec and configured OpenCode executable required")
        return prepare_task(rc,Path(receipt["composed_root"]),native,admin,task_spec,
                            receipt,namespace,installed,ident,project,args.task_spec,
                            args.opencode,args.repository,args.task)
    if args.operation=="uninstall":
        history=namespace/"history"
        revoked=[]
        preserved_history=[]
        if history.is_dir():
            private(history)
            for child in sorted(history.iterdir()):
                need(child.is_dir() and not child.is_symlink()
                     and JOB_ID.fullmatch(child.name) is not None,
                     "unknown task history content; refuse RC Uninstall")
                private(child)
                historical=load(child/"job.json",exact_mode=0o600)
                need(historical.get("kind")=="KAGARI_GA_WORKUNIT_JOB"
                     and historical.get("schema_version")==1
                     and historical.get("job_id")==child.name,
                     "unknown historical Work Unit owner record")
                # History is shared across all Projects using this state home,
                # but only the CURRENT Project+install generation may be
                # revoked. Different projects and previous, already-retired
                # install generations are immutable evidence, never authority.
                same_project=(historical.get("Project_root")==str(project)
                              and historical.get("Project_device")==ident["device"]
                              and historical.get("Project_inode")==ident["inode"])
                if not same_project or historical.get("ga_install_nonce")!=installed["install_nonce"]:
                    preserved_history.append(child.name)
                    continue
                job,request=verify_job(private(child),rc,installed,project,ident,receipt,
                                       allow_source_head_move=True)
                revoked.append({"job_id":child.name,
                                "native_retirement":revoke_job_if_installed(child,job,native,admin,receipt)})
        (attachment/"package").unlink()
        (attachment/"owner.json").unlink()
        attachment.rmdir()
        state,_=attachment_state(attachment,expected)
        need(state=="ABSENT","RC Uninstall owner detach incomplete")
        return {"status":"ABSENT","qualification":"GA_BOUNDED_PROFILE_QUALIFIED",
                "Project_mutations":[],
                "retired_native_asks":revoked,
                "historical_jobs_preserved":preserved_history,
                "task_history_retained":True,
                "source_remote_push":False,
                "publication_attestation":"EXTERNAL_UNVERIFIED"}
    need(args.job_id is not None,"explicit --job-id required for Task actions")
    jobdir=job_folder(namespace,args.job_id)
    job,request=verify_job(jobdir,rc,installed,project,ident,receipt)
    if args.operation=="show-task":
        summary=task_details(jobdir,job,request,native,admin,receipt)
        summary["GA_task_request"]=request
        return summary
    if args.operation=="approve-task":
        return approve_task(jobdir,job,request,admin)
    if args.operation=="run-task":
        return execute_workunit(jobdir,job,request,project,rc,native,admin,receipt)
    if args.operation in ("show-agents","show-permission",
                          "approve-agents","approve-permission",
                          "deny-agents","deny-permission","apply-agents"):
        suffix=args.operation.split("-",1)[1]
        kind=admin.ask.KIND_AGENTS if suffix=="agents" else admin.ask.KIND_PERMISSION
        action=args.operation.split("-",1)[0]
        return show_native_ask(jobdir,job,native,admin,receipt,action,kind)
    raise RCBlocked("unknown RC operation")


def main(argv:list[str]|None=None) -> int:
    params=list(sys.argv[1:] if argv is None else argv)
    if len(params)<2:
        print(json.dumps({"status":"BLOCKED","reason":"immutable RC wrapper identity missing"}))
        return 2
    rc=Path(params.pop(0))
    composed=Path(params.pop(0))
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation",choices=(
        "version","inspect","doctor","install","repair","uninstall",
        "prepare-task","show-task","approve-task","run-task",
        "show-agents","show-permission","approve-agents","approve-permission",
        "deny-agents","deny-permission","apply-agents"))
    parser.add_argument("--project-root",type=Path)
    parser.add_argument("--state-home",type=Path,
                        default=Path(os.environ.get("XDG_STATE_HOME") or
                                     Path.home()/".local/state"))
    parser.add_argument("--task-spec",type=Path)
    parser.add_argument("--opencode",type=Path)
    parser.add_argument("--repository",default="fixture/kagari-native")
    parser.add_argument("--task",default="271")
    parser.add_argument("--job-id")
    args=parser.parse_args(params)
    try:
        receipt,checked_comp,native,location,admin,task_spec=verify_package(rc)
        need(composed==checked_comp,"RC wrapper selected mismatched immutable composed")
        view=operate(args,rc,receipt,native,location,admin,task_spec)
        print(json.dumps(view,sort_keys=True,ensure_ascii=False))
        return 0 if view.get("status")!="BLOCKED" else 2
    except (RCBlocked,OSError,ValueError,KeyError,TypeError,
            subprocess.SubprocessError) as exc:
        print(json.dumps({"status":"BLOCKED","qualification":"GA_BOUNDED_PROFILE_QUALIFIED",
                          "reason":str(exc)[:600],"publication_attestation":"EXTERNAL_UNVERIFIED"},
                         sort_keys=True,ensure_ascii=False))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
