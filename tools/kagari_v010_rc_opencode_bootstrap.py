"""OpenCode 1.18.35 managed Work Unit bootstrap, before Native Task inventory.

This is an exact-support-profile *non-model* dependency preparation step. It
does not waive the strict Native Task physical Project/Git postcondition:
after the host prepares known OpenCode package files and records a frozen
baseline, all model effects must still be the Contract-allowlisted Product paths.

Supported only in an independently disposable no-remote Git Clone. Never run
this against the original consumer Project or take a blanket .opencode ignore.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from datetime import datetime, timezone

VERSION = "1.18.35"
BUN_VERSION = "1.3.13"
LOCK_SHA256 = "8a5bb0ae5806d4084f30baf1771755c3d6c18d301605cc52d0cbd41c5afb9207"
NODE_LOCK_SHA256 = "2a7add79e18a4bab9223c2840676235bc9cb10e36fbc356645e08afdd129b21f"
PLUGINS = {"@opencode-ai/plugin": VERSION}
MAX_DEPENDENCY_ENTRIES = 6500
MAX_DEPENDENCY_BYTES = 256 * 1024 * 1024
ALLOWED_TOOL_PATHS = frozenset({
    ".opencode", ".opencode/package.json", ".opencode/package-lock.json",
    ".opencode/bun.lock", ".opencode/.gitignore", ".opencode/node_modules",
})
GITIGNORE = b"node_modules\npackage.json\npackage-lock.json\nbun.lock\n.gitignore"
STORE_BUN = re.compile(r"/nix/store/[0-9a-df-np-sv-z]{32}-bun-1\.3\.13/bin/bun\Z")
EXTERNAL_DIR_PREFIX = "kagari-v010-rc-work-"


class BootstrapBlocked(ValueError):
    """Exact supported preflight could not establish dependency isolation."""


def need(ok: bool, reason: str) -> None:
    if not ok:
        raise BootstrapBlocked(reason)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(record: object) -> bytes:
    return (json.dumps(record, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode()


def read_regular(path: Path, *, maximum: int = 128 * 1024) -> bytes:
    try:
        st = path.lstat()
    except FileNotFoundError:
        raise BootstrapBlocked("required immutable OpenCode package file absent: "+str(path)) from None
    # Immutable Nix Store input files may be hardlink-deduplicated by Nix.
    # Their exact hashes are verified against the signed-off package receipt.
    # Only mutable Work Unit/Job authority files require a unique inode.
    immutable_store=path.is_relative_to(Path("/nix/store")) and path.resolve()==path
    need(stat.S_ISREG(st.st_mode) and st.st_size <= maximum
         and (st.st_nlink == 1 or immutable_store),
         "OpenCode bootstrap file unsafe: " + str(path))
    value=path.read_bytes()
    need(len(value) <= maximum, "OpenCode bootstrap input exceeded budget")
    return value


def validate_pinned_lock(raw: bytes) -> None:
    need(sha(raw)==LOCK_SHA256,
         "OpenCode 1.18.35 dependency lock differs from approved exact profile")
    try:
        manifest=json.loads(raw)
    except (ValueError,UnicodeError) as exc:
        raise BootstrapBlocked("invalid source-selected OpenCode dependency lock") from exc
    need(type(manifest) is dict and manifest.get("name")==".opencode"
         and manifest.get("lockfileVersion")==3
         and type(manifest.get("packages")) is dict
         and len(manifest["packages"])<=128,
         "OpenCode 1.18.35 package lock version/membership unsupported")
    need(manifest["packages"].get("",{}).get("dependencies")==PLUGINS,
         "OpenCode bootstrap lock does not pin exactly the selected plugin")
    for label,entry in manifest["packages"].items():
        need(type(label) is str and type(entry) is dict,
             "OpenCode package lock has unknown entry")
        if label:
            need(label.startswith("node_modules/")
                 and type(entry.get("version")) is str
                 and type(entry.get("resolved")) is str
                 and entry["resolved"].startswith("https://registry.npmjs.org/")
                 and type(entry.get("integrity")) is str
                 and entry["integrity"].startswith("sha512-"),
                 "OpenCode plugin lock contains non-registry or unpinned dependency")


def validate_node_lock(raw: bytes, root_lock: bytes) -> None:
    need(sha(raw)==NODE_LOCK_SHA256,
         "OpenCode node_modules installed dependency lock differs from source")
    try:
        outer=json.loads(root_lock)
        node=json.loads(raw)
    except (UnicodeError,ValueError) as exc:
        raise BootstrapBlocked("invalid pinned node dependency metadata") from exc
    need(type(node) is dict and node.get("name")==".opencode"
         and node.get("lockfileVersion")==3
         and type(node.get("packages")) is dict
         and len(node["packages"])<=128,
         "pinned Node package lock format not supported")
    root_items=outer["packages"]
    for name,entry in node["packages"].items():
        need(name in root_items and root_items[name]==entry
             and name.startswith("node_modules/")
             and type(entry.get("integrity")) is str
             and entry["integrity"].startswith("sha512-")
             and entry.get("resolved","").startswith("https://registry.npmjs.org/"),
             "node_modules lock does not match pinned full registry dependencies")


def allowed_tool_delta(path: str) -> bool:
    return path in ALLOWED_TOOL_PATHS or path.startswith(".opencode/node_modules/")


def workunit_guard(project: Path, jobdir: Path) -> None:
    need(project.is_absolute() and project.resolve()==project
         and project.is_dir() and not project.is_symlink()
         and project.name=="project"
         and project.parent.parent==Path("/tmp")
         and project.parent.name.startswith(EXTERNAL_DIR_PREFIX)
         and project.parent.resolve()==project.parent
         and not project.parent.is_symlink()
         and project.parent.stat().st_uid==os.geteuid()
         and stat.S_IMODE(project.parent.stat().st_mode)==0o700,
         "OpenCode dependency setup accepts only a private RC Work Unit Clone")
    need(jobdir.is_absolute() and jobdir.resolve()==jobdir
         and jobdir.is_dir() and not jobdir.is_symlink()
         and jobdir.stat().st_uid==os.geteuid()
         and stat.S_IMODE(jobdir.stat().st_mode)==0o700
         and not jobdir.is_relative_to(project)
         and not project.is_relative_to(jobdir),
         "OpenCode setup must use a separate private RC job directory")
    gitdir=project/".git"
    need(gitdir.is_dir() and not gitdir.is_symlink()
         and not (gitdir/"objects/info/alternates").exists(),
         "OpenCode bootstrap Work Unit must have independent real Git data")


def reject_preexisting_opencode_aliases(config_dir: Path) -> None:
    """Refuse alias/special entries BEFORE Bun inspects a user-owned tree.

    The workunit is already a full-history independent Clone, but a tracked
    .opencode symlink from the source Project could still redirect the package
    installer to unrelated user files. Only ordinary euid-owned directory/file
    entries can be present before prewarm. Do not traverse symlinks at all.
    The profile is intentionally narrower than arbitrary Project config
    compatibility; rejected data stays untouched.
    """
    need(config_dir.is_dir() and not config_dir.is_symlink(),
         "OpenCode tracked configuration directory is not ordinary")
    visited=0
    for folder,dirs,files in os.walk(config_dir, followlinks=False):
        parent=Path(folder)
        need(parent.resolve()==parent and not parent.is_symlink()
             and parent.stat().st_uid==os.geteuid(),
             "preexisting OpenCode config has redirected or unknown directory")
        for name in dirs+files:
            visited+=1
            need(visited<=4096, "preexisting OpenCode config tree exceeded budget")
            path=parent/name
            info=path.lstat()
            need(info.st_uid==os.geteuid()
                 and (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
                 and not path.is_symlink()
                 and not os.path.ismount(path),
                 "preexisting OpenCode config has symlink/special/foreign entry: "
                 +path.relative_to(config_dir).as_posix())


def native_integrity_engine(native_root: Path, expected_sha: str) -> object:
    path=native_root/"libexec/kagari-v010-native-generic/tools/kagari_v010_native_generic_task.py"
    need(read_regular(path,maximum=768*1024) and sha(path.read_bytes())==expected_sha,
         "RC bootstrap must use verified Native physical/Git inventory")
    directory=path.parent
    sys.path.insert(0,str(directory))
    import kagari_v010_native_generic_task as engine
    need(Path(engine.__file__).resolve()==path, "RC physical inventory runtime source drift")
    return engine


def git_output(project: Path, *args: str) -> bytes:
    env={k:v for k,v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_NOSYSTEM="1",
               GIT_OPTIONAL_LOCKS="0",GIT_NO_REPLACE_OBJECTS="1")
    result=subprocess.run(
        ["git","--no-optional-locks","-C",str(project),
         "-c","core.fsmonitor=false",*args],
        cwd=project,env=env,capture_output=True,timeout=25,check=False)
    need(result.returncode==0,"RC workunit Git state unavailable: "+str(args[:2]))
    return result.stdout.strip()


def verified_bootstrap_git_status(
        project: Path, *, approved_agents_postimage_sha256: str | None) -> bytes:
    """Permit only the exact host-verified AGENTS addendum as preexisting dirt.

    The caller must derive the expected SHA from an ACTIVE exact Native Human
    Ask grant; supplying this value is not itself authorization to run a Task.
    Without such a grant, preserve R14's original fully clean requirement.
    The normal native model physical Contract is entirely unchanged.
    """
    status = git_output(project, "status", "--porcelain=v1")
    if approved_agents_postimage_sha256 is None:
        need(status == b"",
             "OpenCode prewarm cannot start from a dirty or remote Work Unit")
        return status
    need(type(approved_agents_postimage_sha256) is str
         and re.fullmatch(r"[0-9a-f]{64}", approved_agents_postimage_sha256)
         is not None,
         "AGENTS prewarm exception must bind an exact active Human Ask digest")
    path = project / "AGENTS.md"
    try:
        info = path.lstat()
    except OSError as exc:
        raise BootstrapBlocked("approved AGENTS postimage is missing") from exc
    need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
         and info.st_nlink == 1 and not path.is_symlink(),
         "approved AGENTS postimage must be an ordinary owned unique file")
    need(sha(read_regular(path, maximum=200_000))
         == approved_agents_postimage_sha256,
         "AGENTS postimage differs from the active exact Human Ask")
    # git_output strips leading whitespace in porcelain output: the only
    # supported case is an unstaged tracked AGENTS.md mutation, no other paths.
    # No staged modification is accepted (M  vs  M in raw porcelain output).
    need(status == b"M AGENTS.md"
         and git_output(project, "diff", "--cached", "--name-only") == b"",
         "OpenCode prewarm has unapproved staged/dirty Project paths")
    return status


def save_bounded(path: Path, raw: bytes, max_length: int = 2*1024*1024) -> None:
    need(len(raw)<=max_length and not path.exists() and not path.is_symlink(),
         "OpenCode bootstrap evidence must be fresh and bounded")
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as out:
        out.write(raw);out.flush();os.fsync(out.fileno())


def package_inventory_digest(inventory:dict) -> str:
    selected={
        key:inventory[key] for key in sorted(inventory)
        if key==".opencode" or key.startswith(".opencode/")
    }
    return sha(canonical(selected))


def prewarm(project: Path, jobdir: Path, *,
            rc_root: Path, receipt: dict, native_root: Path,
            executable: Path,
            approved_agents_postimage_sha256: str | None = None) -> dict:
    """Warm a pinned tool cache and verify only tool-owned bytes were created."""
    workunit_guard(project,jobdir)
    installed_lock=rc_root/"share/kagari-v010-rc/opencode-plugin-package-lock.json"
    raw_lock=read_regular(installed_lock)
    validate_pinned_lock(raw_lock)
    installed_node_lock=rc_root/"share/kagari-v010-rc/opencode-node-modules-package-lock.json"
    raw_node_lock=read_regular(installed_node_lock)
    validate_node_lock(raw_node_lock,raw_lock)
    need(receipt.get("opencode_plugin_lock_sha256")==sha(raw_lock)
         and receipt.get("opencode_node_modules_lock_sha256")==sha(raw_node_lock)
         and receipt.get("opencode_plugin_version")==VERSION,
         "exact RC plugin/installed lock not bound to Nix receipt")
    bun=Path(receipt["bun_executable"])
    need(STORE_BUN.fullmatch(str(bun)) is not None
         and bun.is_file() and not bun.is_symlink()
         and os.access(bun,os.X_OK)
         and receipt.get("bun_version")==BUN_VERSION,
         "immutable RC Bun executable not available for pinned plugin prewarm")
    need(executable.is_absolute() and executable.resolve()==executable
         and executable.is_file() and not executable.is_symlink()
         and os.access(executable,os.X_OK),
         "exact configured OpenCode executable must be ordinary and executable")
    native=native_integrity_engine(native_root,receipt["native_generic_task_sha256"])
    need(git_output(project,"remote")==b""
         and git_output(project,"rev-parse","--is-shallow-repository")==b"false",
         "OpenCode prewarm requires independent, complete no-remote Git Work Unit")
    # Without active Human Ask this is precisely the previous clean gate.
    # For a scoped conflict, the RC host supplies the SHA only AFTER verifying
    # the exact active Native AGENTS addendum grant and immutable owner record.
    before_status=verified_bootstrap_git_status(
        project,
        approved_agents_postimage_sha256=approved_agents_postimage_sha256)
    # Avoid bootstrapping over any Project-provided dependency source.
    config_dir=project/".opencode"
    if config_dir.exists() or config_dir.is_symlink():
        need(config_dir.is_dir() and not config_dir.is_symlink()
             and config_dir.stat().st_uid==os.geteuid(),
             "Project .opencode config directory is not ordinary owned data")
    else:
        config_dir.mkdir(mode=0o700)
    # Explicitly reject ANY existing tracked symlink or filesystem alias
    # before an external package installer can parse or follow it.
    reject_preexisting_opencode_aliases(config_dir)
    for filename in ("package.json","package-lock.json",
                     "bun.lock","node_modules",".gitignore"):
        path=config_dir/filename
        need(not path.exists() and not path.is_symlink(),
             "Project's preexisting OpenCode package data requires manual review: "+filename)
    for filename in (".npmrc","bunfig.toml"):
        need(not (config_dir/filename).exists()
             and not (config_dir/filename).is_symlink(),
             "Project-owned custom package registry/script profile unsupported")
    before=native.inventory(project)
    before_git=native.git_control_inventory(project)
    before_index=(project/".git/index").read_bytes()
    before_head=git_output(project,"rev-parse","HEAD")
    before_ref=git_output(project,"symbolic-ref","-q","HEAD")
    before_refs=git_output(project,"show-ref")
    before_cfg=read_regular(project/".git/config",maximum=1024*1024)
    before_cfg_mode=stat.S_IMODE((project/".git/config").stat().st_mode)
    need(receipt.get("bun_version")==BUN_VERSION,"Bun binary version not source bound")
    external=jobdir/"opencode-bootstrap"
    need(not external.exists() and not external.is_symlink(),
         "RC OpenCode warmup state already exists: do not reuse this one-shot job")
    external.mkdir(mode=0o700)
    cache=external/"cache"
    cache.mkdir(mode=0o700)
    roles=external/"roles"
    roles.mkdir(mode=0o700)
    # We use an approved, pinned, generated package.json/lock, never untrusted
    # user Project's ignored package content or extension install hooks.
    manifest={"dependencies":PLUGINS}
    pkg_bytes=json.dumps(manifest,indent=2,sort_keys=True).encode()+b"\n"
    save_bounded(config_dir/"package.json",pkg_bytes,max_length=512)
    save_bounded(config_dir/"package-lock.json",raw_lock,max_length=128*1024)
    env={k:v for k,v in os.environ.items() if not k.startswith("GIT_")}
    env["OPENCODE_CONFIG_DIR"]=str(roles)
    env["BUN_INSTALL_CACHE_DIR"]=str(cache)
    env["PYTHONDONTWRITEBYTECODE"]="1"
    for name in ("OPENCODE_CONFIG","OPENCODE_CONFIG_CONTENT",
                 "OPENCODE_MODEL","OPENCODE_AGENT"):
        need(not env.get(name),
             "unreviewed model/config override prevents pinned OpenCode bootstrap")
    env.update(GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_NOSYSTEM="1",
               GIT_OPTIONAL_LOCKS="0",GIT_NO_REPLACE_OBJECTS="1")
    version=subprocess.run([str(executable),"--version"],cwd=external,
                           env=env,capture_output=True,timeout=15,check=False)
    need(version.returncode==0 and version.stdout.decode(errors="replace").strip()==VERSION,
         "initial RC package bootstrap supports only OpenCode "+VERSION)
    # --ignore-scripts forbids third-party install hooks. Bun/cache are pinned.
    installer=subprocess.run(
        [str(bun),"install","--no-save","--ignore-scripts",
         "--backend=copyfile","--no-progress",
         "--registry","https://registry.npmjs.org/"],
        cwd=config_dir,env=env,capture_output=True,timeout=180,check=False)
    need(len(installer.stdout)<128*1024 and len(installer.stderr)<128*1024,
         "OpenCode dependency installation emitted oversized diagnostics")
    save_bounded(external/"install.stdout",installer.stdout)
    save_bounded(external/"install.stderr",installer.stderr)
    need(installer.returncode==0,
         "immutable Bun dependency setup failed (do not launch model): "
         + installer.stderr[-550:].decode(errors="replace"))
    # OpenCode 1.18.35 first model invocation creates an npm-format nested
    # node_modules/.package-lock.json even when Bun populated all packages.
    # Seed EXACT bytes observed in a failed RC model run and pinned to the
    # identical versioned registry dependency graph. This avoids a late
    # unapproved Project write without masking anything in Native inventory.
    deps=config_dir/"node_modules"
    need(deps.is_dir() and not deps.is_symlink(),
         "OpenCode pinned dependency folder missing after Bun install")
    save_bounded(deps/".package-lock.json",raw_node_lock,max_length=128*1024)
    # Non-model agent list reproduces the real CLI's workspace initialization.
    listed=subprocess.run([str(executable),"--pure","agent","list"],
                          cwd=project,env=env,capture_output=True,timeout=120,
                          check=False)
    need(len(listed.stdout)<=2*1024*1024 and len(listed.stderr)<128*1024,
         "OpenCode role-config read exceeded bounded expected size")
    save_bounded(external/"agents.stdout",listed.stdout)
    save_bounded(external/"agents.stderr",listed.stderr)
    need(listed.returncode==0,
         "OpenCode cannot load exact configured agent list without a model")
    after=native.inventory(project)
    after_git=native.git_control_inventory(project)
    changes={x for x in before.keys()|after.keys()
             if before.get(x)!=after.get(x)}
    need(changes and all(allowed_tool_delta(x) for x in changes),
         "OpenCode dependency setup touched unapproved Project source: "
         + repr(sorted(x for x in changes if not allowed_tool_delta(x))[:15]))
    need(len(changes) <= MAX_DEPENDENCY_ENTRIES,
         "OpenCode bootstrap package generated excessive Project filesystem entries")
    need(all(after.get(x,())[0] in ("file","dir","link") for x in changes),
         "OpenCode prewarm created unsupported package filesystem type")
    # A symlink target inside node_modules must remain within that directory:
    # package bootstrap may not redirect outside the managed dependency tree.
    deps=config_dir/"node_modules"
    need(deps.is_dir() and not deps.is_symlink(),"OpenCode module dependency tree missing")
    root_resolved=deps.resolve(strict=True)
    total=0
    for name in sorted(changes):
        entry=after.get(name)
        need(entry is not None,"OpenCode bootstrap removed a Project file: "+name)
        p=project/name
        st=p.lstat()
        if stat.S_ISREG(st.st_mode):
            total+=st.st_size
            need(st.st_nlink==1,
                 "OpenCode dependency bootstrap must not hardlink into other Projects: "+name)
        elif stat.S_ISLNK(st.st_mode):
            need(name.startswith(".opencode/node_modules/"),
                 "OpenCode bootstrap unexpected symlink outside dependencies: "+name)
            target=(p.parent/os.readlink(p)).resolve(strict=False)
            need(target.is_relative_to(root_resolved),
                 "OpenCode package symlink escaped managed dependency root: "+name)
    need(total<=MAX_DEPENDENCY_BYTES,
         "OpenCode bootstrap dependency package exceeded bounded file-size budget")
    need(read_regular(config_dir/"package.json",maximum=512)==pkg_bytes
         and read_regular(config_dir/"package-lock.json",maximum=128*1024)==raw_lock
         and read_regular(deps/".package-lock.json",maximum=128*1024)==raw_node_lock,
         "OpenCode dependency bootstrap changed pinned package/root/nested lock")
    need(read_regular(config_dir/".gitignore",maximum=512).strip()==GITIGNORE.strip(),
         "OpenCode produced unexpected gitignore rules in dependency tree")
    # The sole permitted Git control initialization is the exact first-run
    # OpenCode Workspace ID; all refs/hooks/config/index must be unchanged.
    changes_git={x for x in before_git.keys()|after_git.keys()
                 if before_git.get(x)!=after_git.get(x)}
    if changes_git:
        need(changes_git=={"opencode"} and "opencode" not in before_git,
             "OpenCode prewarm unexpectedly modified Git control data")
        marker=read_regular(project/".git/opencode",maximum=64)
        workspace_info=(project/".git/opencode").stat()
        # OpenCode 1.18.35 creates mode 0644 on a clean Git Clone (the
        # identifier is a non-secret 40-hex workspace fingerprint). Existing
        # project profiles may use 0600; both are then frozen by the original
        # Native exact Git-control inventory.
        need(re.fullmatch(rb"[0-9a-f]{40}",marker) is not None
             and workspace_info.st_uid==os.geteuid()
             and stat.S_IMODE(workspace_info.st_mode) in (0o600,0o644),
             "OpenCode Workspace ID lacks exact expected format/owner/mode")
    need(git_output(project,"rev-parse","HEAD")==before_head
         and git_output(project,"symbolic-ref","-q","HEAD")==before_ref
         and git_output(project,"show-ref")==before_refs
         and (project/".git/index").read_bytes()==before_index
         and read_regular(project/".git/config",maximum=1024*1024)==before_cfg
         and stat.S_IMODE((project/".git/config").stat().st_mode)==before_cfg_mode
         and git_output(project,"status","--porcelain=v1")==before_status,
         "OpenCode plugin bootstrap altered Git/index/HEAD/refs/config or tracked Product")
    # Recheck approved AGENTS postimage after Bun and CLI config loading; a
    # matching porcelain status alone does not prove matching instruction bytes.
    need(verified_bootstrap_git_status(
             project,
             approved_agents_postimage_sha256=approved_agents_postimage_sha256)
         ==before_status, "OpenCode bootstrap altered approved AGENTS scope")
    need((config_dir/"bun.lock").is_file(),
         "pinned Bun package prewarm did not create an exact private lock")
    result={
        "schema_version":1,
        "kind":"KAGARI_RC_PINNED_OPENCODE_WORKUNIT_PREFLIGHT",
        "status":"HOST_PREPARED_OPENCODE_PACKAGES_STRICT_CONTRACT_BASELINE",
        "configured_opencode_version":VERSION,
        "configured_opencode_sha256":sha(read_regular(executable,maximum=75*1024*1024)),
        "immutable_bun_binary":str(bun),
        "bun_version":BUN_VERSION,
        "external_preflight":str(external),
        "Project_workunit":str(project),
        "Project_git_head":before_head.decode(),
        "Project_git_workspace_marker_created":changes_git=={"opencode"},
        "git_index_sha256":sha(before_index),
        "native_engine_sha256":receipt["native_generic_task_sha256"],
        "approved_scoped_AGENTS_postimage_sha256":approved_agents_postimage_sha256,
        "verified_initial_Git_porcelain_status":before_status.decode("ascii"),
        "Native_AGENTS_exact_scope_preserved":True,
        "allowed_generated_paths_count":len(changes),
        "generated_total_regular_bytes":total,
        "allowed_git_control_changes":sorted(changes_git),
        "generated_package_json_sha256":sha(pkg_bytes),
        "source_selected_package_lock_sha256":sha(raw_lock),
        "source_selected_node_modules_lock_sha256":sha(raw_node_lock),
        "generated_bun_lock_sha256":sha(read_regular(config_dir/"bun.lock",maximum=256*1024)),
        "bootstrap_inventory_sha256":package_inventory_digest(after),
        "native_Model_Project_physical_allowlist_unchanged":True,
        "original_consumer_Project_mutated":False,
        "model_invoked":False,
        "source_published":False,
        "timestamp_utc":datetime.now(timezone.utc).isoformat(),
    }
    save_bounded(external/"bootstrap-receipt.json",canonical(result),max_length=16*1024)
    return result
