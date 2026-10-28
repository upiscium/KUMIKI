#!/usr/bin/env python3
"""Dynamic source-bound KAGARI Nix package setup for the canonical CI shards.

The canonical ci_contract_runner decides *which* shard owns every test. New
modules can move assignments. This helper discovers the actual mapping and
builds only the immutable packages required by a particular shard.
It does not skip any canonical tests, modify Project data, or confer release
authority. Stdout emits only GITHUB_OUTPUT/GITHUB_ENV assignment lines.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

BASE_REV = "5e0a4fd35b0e2e8014ff35f2ce7dc8e600e84812"
N1_SHA = "521b1f6dbda3f4a1fc8937f8ace3f560d24a1931b3317d52162652ce457ec07c"
# Existing coverage is an independent sanity check. Additional new test
# modules are discovered from their actual package-env reads below.
CORE_PACKAGE_TEST_MODULES = {
    "test_kagari_v010_native_package": "native",
    "test_kagari_v010_existing_smoke_package": "existing",
    "test_kagari_v010_composed_candidate": "composed",
    "test_kagari_v010_durable_admin_preview": "composed",
    "test_kagari_v010_rc_lifecycle": "rc",
    "test_kagari_v010_ga_lifecycle": "ga",
    "test_kagari_v010_ga_stable_lifecycle": "ga-stable",
    # These source-pinned vNext Main tests must run in CI, regardless of the
    # canonical shard number assigned to their modules.
    "test_kagari_vnext_main_service": "vnext-main",
    "test_kagari_vnext_cleanup_isolation": "vnext-main",
}
PACKAGES = {
    "native": ("kagari-v010-native-generic-package.nix",
               "KAGARI_V010_NATIVE_GENERIC_PACKAGE",
               "kagari-0.1.0-native-generic-test-only"),
    "existing": ("kagari-v010-existing-smoke-package.nix",
                 "KAGARI_V010_EXISTING_SMOKE_PACKAGE",
                 "kagari-0.1.0-existing-smoke-test-only"),
    "composed": ("kagari-v010-composed-candidate.nix",
                 "KAGARI_V010_COMPOSED_PACKAGE",
                 "kagari-0.1.0-composed-unreleased-candidate"),
    "rc": ("kagari-v010-rc-candidate.nix",
           "KAGARI_V010_RC_PACKAGE",
           "kagari-0.1.0-rc.1"),
    "ga": ("kagari-v010-ga-candidate.nix",
           "KAGARI_V010_GA_PACKAGE",
           "kagari-0.1.0-ga-candidate"),
    "ga-stable": ("kagari-v010-ga-stable.nix",
           "KAGARI_V010_GA_STABLE_PACKAGE",
           "kagari-0.1.0"),
    # Optional vNext control plane, distinct from shipped 0.1.0 GA.
    "vnext-main": ("kagari-vnext-control.nix",
                   "KAGARI_VNEXT_MAIN_PACKAGE",
                   "kagari-vnext-control"),
}
SHARD_RANGE = range(8)


class CIBlocked(ValueError):
    pass


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise CIBlocked(reason)


def sha(path: Path) -> str:
    require(path.is_file() and not path.is_symlink(),
            "missing or symlinked package/source file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_root(root: Path) -> Path:
    require(root.is_absolute() and root.is_dir() and not root.is_symlink()
            and root.resolve() == root, "expected exact canonical source root")
    return root


def source_package_reads(source_text: str) -> set[str]:
    """Identify actual os.environ.get/os.getenv Nix package requirements.

    Unlike grepping comments/expected literals in a CI helper test, inspect
    runtime calls. Any unknown KAGARI package-variable name fails closed.
    """
    tree=ast.parse(source_text)
    env_to_package={entry[1]:name for name,entry in PACKAGES.items()}
    found=set()
    for node in ast.walk(tree):
        if not isinstance(node,ast.Call):
            continue
        fn=node.func
        recognized=False
        if isinstance(fn,ast.Attribute):
            if (fn.attr=="getenv" and isinstance(fn.value,ast.Name)
                    and fn.value.id=="os"):
                recognized=True
            if (fn.attr=="get" and isinstance(fn.value,ast.Attribute)
                    and fn.value.attr=="environ"
                    and isinstance(fn.value.value,ast.Name)
                    and fn.value.value.id=="os"):
                recognized=True
        if not recognized:
            continue
        # Both os.getenv(key=...) and os.environ.get(key=...) are valid
        # Python and must not hide required Nix package dependencies.
        require(all(k.arg is not None for k in node.keywords),
                "dynamic environment variable name cannot establish "
                "the complete KAGARI package requirements")
        if node.args:
            require(not any(k.arg=="key" for k in node.keywords),
                    "duplicate environment variable key is ambiguous")
            arg=node.args[0]
        else:
            keys=[k.value for k in node.keywords if k.arg=="key"]
            require(len(keys)==1,
                    "dynamic environment variable name cannot establish "
                    "the complete KAGARI package requirements")
            arg=keys[0]
        # The loader cannot prove whether a computed key is a KAGARI package
        # variable. Silent omission would turn future required package tests
        # into an unreviewed SKIP. Reject all dynamic getenv/environ.get keys
        # until their exact value has a separately reviewed static binding.
        require(isinstance(arg,ast.Constant) and type(arg.value) is str,
                "dynamic environment variable name cannot establish "
                "the complete KAGARI package requirements")
        key=arg.value
        if (key.startswith(("KAGARI_V010_", "KAGARI_VNEXT_"))
                and key.endswith("_PACKAGE")):
            require(key in env_to_package,
                    "unrecognized KAGARI package dependency in test source: " + key)
            found.add(env_to_package[key])
    return found


def package_module_requirements(root: Path) -> dict[str, set[str]]:
    """A new package-consuming test module requires no hardcoded update."""
    tests=root/"tests"
    require(tests.is_dir(), "canonical tests directory missing")
    packages={}
    for f in sorted(tests.glob("test*.py")):
        require(f.is_file() and not f.is_symlink(),
                "nonregular test source in canonical package dependency scan")
        needed=source_package_reads(f.read_text())
        if needed:
            packages[f.stem]=needed
    for module,expected in CORE_PACKAGE_TEST_MODULES.items():
        require(packages.get(module)=={expected},
                "required core package test disappeared or changed env needs: "+module)
    return packages


def package_assignment(root: Path) -> dict[int, list[str]]:
    """Use canonical test assignment AND actual package reads per test module."""
    root=source_root(root)
    tools=str(root/"tools")
    if tools not in sys.path:
        sys.path.insert(0,tools)
    import ci_contract_runner as ci
    tests=ci.discover()
    by_id=ci.assignments([test.id() for test in tests])
    module_requirements=package_module_requirements(root)
    modules={name:set() for name in module_requirements}
    for test in tests:
        module=test.id().split(".",1)[0]
        if module in modules:
            modules[module].add(by_id[test.id()])
    require(all(len(shards)==1 for shards in modules.values()),
            "missing or split canonical KAGARI package test module")
    result={shard:set() for shard in SHARD_RANGE}
    for module,shards in modules.items():
        shard=next(iter(shards))
        result[shard].update(module_requirements[module])
    return {shard:sorted(packages) for shard,packages in result.items()}

def package_expression(root: Path, package: str) -> str:
    root = source_root(root)
    require(package in PACKAGES, "unrecognized KAGARI package profile")
    file = root/"nix"/PACKAGES[package][0]
    require(file.is_file() and not file.is_symlink(), "missing selected Nix expression")
    if package == "vnext-main":
        # Build exactly this checkout's opt-in control-plane output, not GA
        # v0.1.0, another checkout, or a mutable remote reference.
        return (f'let flake = builtins.getFlake {json.dumps("path:" + str(root))}; '
                'in flake.packages.x86_64-linux.kagari-control')
    attrs = "inherit pkgs;"
    if package == "existing":
        attrs += (f' sourceRevision = "{BASE_REV}"; '
                  f'expectedV4ManifestSha256 = "{N1_SHA}";')
    return (f'let flake = builtins.getFlake {json.dumps("path:" + str(root))}; '
            'pkgs = flake.inputs.nixpkgs.legacyPackages.x86_64-linux; '
            f'in import (builtins.toPath {json.dumps(str(file))}) '
            '{ ' + attrs + ' }')


def parse_store_path(stdout: str, package: str) -> Path:
    require(package in PACKAGES, "unrecognized package profile")
    suffix = PACKAGES[package][2]
    pattern = re.compile(r"/nix/store/[0-9a-df-np-sv-z]{32}-" +
                         re.escape(suffix) + r"\Z")
    value = stdout.strip()
    require(pattern.fullmatch(value) is not None and "\n" not in value,
            "Nix must return exactly one profile-matched immutable store path")
    result = Path(value)
    require(result.is_dir() and not result.is_symlink(),
            "Nix output unavailable or symlinked")
    return result


def receipt(path: Path) -> dict:
    require(path.is_file() and not path.is_symlink(), "missing package receipt")
    try:
        info = json.loads(path.read_bytes())
    except (OSError, ValueError, UnicodeError) as exc:
        raise CIBlocked("unreadable package receipt") from exc
    require(type(info) is dict, "package receipt is not an object")
    return info


def verify_package(root: Path, package: str, path: Path) -> None:
    root=source_root(root)
    if package == "native":
        data = receipt(path/"share/kagari-v010-native-generic/receipt.json")
        require(data.get("qualification") == "TEST_ONLY_UNQUALIFIED"
                and data.get("public_target_version") == "0.1.0"
                and data.get("baseline_source_revision") == BASE_REV
                and data.get("n1_manifest_sha256") == N1_SHA
                and data.get("released") is False,
                "native package receipt is not exact/unreleased")
        tool_hashes = data.get("tool_sha256", {})
        require(type(tool_hashes) is dict and tool_hashes,
                "native tool identity missing")
        for name, expected in tool_hashes.items():
            require(sha(root/"tools"/name) == expected,
                    "native selected source SHA mismatch: " + name)
            require(sha(path/"libexec/kagari-v010-native-generic/tools"/name)
                    == expected, "native installed source SHA mismatch")
    elif package == "existing":
        data = receipt(path/"share/kagari-v010-existing-smoke/receipt.json")
        require(data.get("qualification") == "TEST_ONLY_UNQUALIFIED"
                and data.get("version") == "0.1.0"
                and data.get("source_revision") == BASE_REV
                and data.get("n1_manifest_sha256") == N1_SHA
                and data.get("released") is False,
                "historical existing-smoke identity differs")
        require(data.get("existing_project_adapter_sha256") ==
                sha(root/"tools/kagari_v010_existing_project.py"),
                "historical smoke source SHA mismatch")
    elif package == "composed":
        data = receipt(path/"share/kagari-v010-composed/receipt.json")
        require(data.get("qualification") == "UNRELEASED_CANDIDATE"
                and data.get("version") == "0.1.0"
                and data.get("source_baseline_revision") == BASE_REV
                and data.get("released") is False
                and data.get("production_activation") is False
                and data.get("consumer_production_qualified") is False,
                "composed package falsely claims production qualification")
        require(data.get("cli_sha256") ==
                sha(root/"tools/kagari_v010_composed_cli.py") ==
                sha(path/"libexec/kagari-v010-composed/kagari_v010_composed_cli.py"),
                "composed CLI is not the selected source identity")
        require(data.get("durable_preview_qualified") is False
                and data.get("generic_task_qualified") is False,
                "composed preview source must remain unqualified")
        # Hash-check all source members newly capable of reaching a Native
        # Task, rather than only the original smoke-only CLI entrypoint.
        for filename, field in (
                ("kagari_v010_durable_admin_preview.py", "durable_admin_sha256"),
                ("kagari_v010_durable_location.py", "durable_location_sha256")):
            actual=sha(root/"tools"/filename)
            installed=sha(path/"libexec/kagari-v010-composed"/filename)
            require(data.get(field)==actual==installed,
                    "composed durable source identity drift: "+filename)
        native_path = Path(str(data.get("native_root", "")))
        require(native_path.is_absolute()
                and native_path.parent == Path("/nix/store")
                and native_path.is_dir()
                and data.get("native_receipt_sha256") ==
                sha(native_path/"share/kagari-v010-native-generic/receipt.json"),
                "composed Native dependency receipt SHA mismatch")
    elif package == "rc":
        data = receipt(path/"share/kagari-v010-rc/receipt.json")
        require(data.get("qualification")=="RC_RESTRICTED_PROFILE"
                and data.get("version")=="0.1.0-rc.1"
                and data.get("root")==str(path)
                and data.get("source_baseline_revision")==BASE_REV
                and data.get("publication_attestation")=="EXTERNAL_UNVERIFIED"
                and data.get("release_channel")=="prerelease"
                and "released" not in data
                and "source_publication_approved" not in data
                and data.get("production_activation") is False
                and data.get("managed_isolated_Work_Unit_supported") is True
                and data.get("direct_live_Project_Task_supported") is False,
                "RC receipt cannot claim release or direct in-place Project authority")
        require(data.get("cli_sha256")==
                sha(root/"tools/kagari_v010_rc_lifecycle.py")==
                sha(path/"libexec/kagari-v010-rc/kagari_v010_rc_lifecycle.py"),
                "RC consumer CLI is not the selected immutable source")
        for filename,source,field in (
                ("libexec/kagari-v010-rc/kagari_v010_rc_opencode_bootstrap.py",
                 "tools/kagari_v010_rc_opencode_bootstrap.py","opencode_bootstrap_sha256"),
                ("share/kagari-v010-rc/opencode-plugin-package-lock.json",
                 "nix/kagari-v010-rc-opencode-plugin-lock.json","opencode_plugin_lock_sha256"),
                ("share/kagari-v010-rc/opencode-node-modules-package-lock.json",
                 "nix/kagari-v010-rc-opencode-node-modules-lock.json",
                 "opencode_node_modules_lock_sha256")):
            require(data.get(field)==sha(root/source)==sha(path/filename),
                    "RC pre-model plugin/cache source identity mismatch: "+field)
        bun=Path(str(data.get("bun_executable","")))
        require(data.get("bun_version")=="1.3.13"
                and bun.is_absolute() and bun.parent.name=="bin"
                and bun.parent.parent.parent==Path("/nix/store")
                and bun.parent.parent.name.endswith("-bun-1.3.13")
                and bun.is_file() and not bun.is_symlink()
                and data.get("opencode_plugin_version")=="1.18.35"
                and data.get("opencode_dependencies_bootstrapped_before_Native_snapshot") is True
                and data.get("native_Project_scope_inventory_relaxed") is False,
                "RC Bun/config warmup profile not exact or Native strict scope relaxed")
        composed_root=Path(str(data.get("composed_root","")))
        require(composed_root.parent==Path("/nix/store")
                and composed_root.is_dir()
                and data.get("composed_receipt_sha256")==
                sha(composed_root/"share/kagari-v010-composed/receipt.json"),
                "RC composed package receipt is not source-bound")
        verify_package(root,"composed",composed_root)
        native_root=Path(str(data.get("native_root","")))
        require(native_root.parent==Path("/nix/store") and native_root.is_dir()
                and data.get("native_receipt_sha256")==
                sha(native_root/"share/kagari-v010-native-generic/receipt.json"),
                "RC Native component receipt drift")
        verify_package(root,"native",native_root)
        require(data.get("native_component_qualification")=="TEST_ONLY_UNQUALIFIED"
                and data.get("git_remote_Push_implemented") is False,
                "RC must declare its internal Native limitation and no Push")
    elif package == "ga":
        data = receipt(path/"share/kagari-v010-ga/receipt.json")
        require(data.get("qualification")=="GA_CANDIDATE_NOT_QUALIFIED"
                and data.get("version")=="0.1.0"
                and data.get("root")==str(path)
                and data.get("release_channel")=="ga-candidate"
                and data.get("publication_attestation")=="EXTERNAL_UNVERIFIED"
                and data.get("production_activation") is False
                and data.get("native_component_qualification")=="TEST_ONLY_UNQUALIFIED"
                and data.get("managed_isolated_Work_Unit_supported") is True
                and data.get("direct_live_Project_Task_supported") is False
                and "released" not in data
                and "source_publication_approved" not in data,
                "GA candidate must not silently claim GA qualification/release")
        for src,dst,key in (
             ("tools/kagari_v010_ga_lifecycle.py",
              "libexec/kagari-v010-ga/kagari_v010_ga_lifecycle.py","cli_sha256"),
             ("tools/kagari_v010_ga_opencode_bootstrap.py",
              "libexec/kagari-v010-ga/kagari_v010_ga_opencode_bootstrap.py","opencode_bootstrap_sha256"),
             ("tools/kagari_v010_ga_product_archive.py",
              "libexec/kagari-v010-ga/kagari_v010_ga_product_archive.py","durable_product_archive_sha256"),
             ("nix/kagari-v010-rc-opencode-plugin-lock.json",
              "share/kagari-v010-ga/opencode-plugin-package-lock.json","opencode_plugin_lock_sha256"),
             ("nix/kagari-v010-rc-opencode-node-modules-lock.json",
              "share/kagari-v010-ga/opencode-node-modules-package-lock.json",
              "opencode_node_modules_lock_sha256")):
            require(data.get(key)==sha(root/src)==sha(path/dst),
                    "GA immutable source/receipt field differs: "+key)
        require(data.get("bun_version")=="1.3.13"
                and data.get("opencode_plugin_version")=="1.18.35"
                and data.get("native_Project_scope_inventory_relaxed") is False
                and data.get("git_remote_Push_implemented") is False,
                "GA candidate Bun/Native/Git controls changed")
        composed_root=Path(str(data.get("composed_root","")))
        native_root=Path(str(data.get("native_root","")))
        require(composed_root.parent==Path("/nix/store")
                and data.get("composed_receipt_sha256")==
                sha(composed_root/"share/kagari-v010-composed/receipt.json")
                and native_root.parent==Path("/nix/store")
                and data.get("native_receipt_sha256")==
                sha(native_root/"share/kagari-v010-native-generic/receipt.json"),
                "GA external immutable component chain not receipt-bound")
        verify_package(root,"composed",composed_root)
        verify_package(root,"native",native_root)
    elif package == "vnext-main":
        # This symlinkJoin has no GA qualification receipt. Verify all five
        # entrypoints and their single shared immutable source copy instead.
        expected = {
            "kagari-vnext-task": "tools/kagari_vnext_task_binding.py",
            "kagari-vnext-session": "tools/kagari_vnext_session.py",
            "kagari-vnext-worktree-install": "tools/kagari_vnext_worktree_ignore.py",
            "kagari-vnext-host-prepare": "tools/kagari_vnext_host_prepare.py",
            "kagari-vnext-main": "tools/kagari_vnext_main_service.py",
        }
        roots = set()
        store_root = re.compile(
            r"/nix/store/[0-9a-df-np-sv-z]{32}-kagari-vnext-control-sources")
        for binary, relative in expected.items():
            executable = path/"bin"/binary
            require(executable.is_file() and executable.stat().st_mode & 0o111,
                    "missing executable in vNext package: " + binary)
            script = executable.read_text(encoding="utf-8")
            located = set(store_root.findall(script))
            require(len(located) == 1,
                    "vNext wrapper source identity ambiguous: " + binary)
            source = next(iter(located))
            require(f'-B {source}/{relative} "$@"' in script
                    and script.count("exec ") == 1,
                    "vNext wrapper entrypoint differs: " + binary)
            roots.add(source)
        require(len(roots) == 1,
                "vNext wrappers do not share one immutable source")
        installed = Path(next(iter(roots)))
        require(installed.parent == Path("/nix/store")
                and installed.is_dir() and not installed.is_symlink(),
                "vNext source output is not immutable Nix store content")
        definition = (root/"nix/kagari-vnext-control.nix").read_text("utf-8")
        copies = []
        for line in definition.splitlines():
            if not line.strip().startswith("cp $" + "{../"):
                continue
            match = re.fullmatch(
                r'cp \$\{\.\./([a-zA-Z0-9_./-]+)\} "\$out/([a-zA-Z0-9_./-]+)"',
                line.strip())
            require(match is not None,
                    "ambiguous vNext source-copy declaration")
            copies.append(match.groups())
        require(len(copies) >= 20
                and len({src for src, _ in copies}) == len(copies),
                "vNext source copy inventory incomplete or ambiguous")
        for relative, target in copies:
            require(relative == target
                    and relative.startswith(("tools/", "components/agent-core-v4/"))
                    and ".." not in Path(relative).parts,
                    "vNext Nix copy target differs from source")
            require(sha(root/relative) == sha(installed/relative),
                    "vNext installed source differs from checkout: " + relative)
    elif package == "ga-stable":
        data=receipt(path/"share/kagari-v010-ga/receipt.json")
        basis=receipt(path/"share/kagari-v010-ga/bounded-qualification.json")
        require(data.get("version")=="0.1.0"
                and data.get("qualification")=="GA_BOUNDED_PROFILE_QUALIFIED"
                and data.get("native_component_qualification")=="TEST_ONLY_UNQUALIFIED"
                and data.get("release_channel")=="stable"
                and data.get("publication_attestation")=="EXTERNAL_UNVERIFIED"
                and data.get("production_activation") is False
                and data.get("root")==str(path),
                "GA stable support profile must remain exact and limited")
        require(basis.get("kind")=="KAGARI_0_1_0_BOUNDED_GA_QUALIFICATION_BASIS"
                and basis.get("qualification")=="GA_BOUNDED_PROFILE_QUALIFIED"
                and basis.get("native_component_qualification")=="TEST_ONLY_UNQUALIFIED"
                and data.get("bounded_profile_id")==basis.get("scope_id")
                and data.get("bounded_qualification_sha256")==
                     sha(root/"docs/kagari-v010-ga-bounded-qualification.json")
                ==sha(path/"share/kagari-v010-ga/bounded-qualification.json"),
                "GA support qualification evidence/source/receipt mismatch")
        for src,inst,key in (
            ("tools/kagari_v010_ga_stable_lifecycle.py",
             "libexec/kagari-v010-ga/kagari_v010_ga_stable_lifecycle.py","cli_sha256"),
            ("tools/kagari_v010_ga_opencode_bootstrap.py",
             "libexec/kagari-v010-ga/kagari_v010_ga_opencode_bootstrap.py","opencode_bootstrap_sha256"),
            ("tools/kagari_v010_ga_product_archive.py",
             "libexec/kagari-v010-ga/kagari_v010_ga_product_archive.py","durable_product_archive_sha256"),
            ("nix/kagari-v010-rc-opencode-plugin-lock.json",
             "share/kagari-v010-ga/opencode-plugin-package-lock.json","opencode_plugin_lock_sha256"),
            ("nix/kagari-v010-rc-opencode-node-modules-lock.json",
             "share/kagari-v010-ga/opencode-node-modules-package-lock.json","opencode_node_modules_lock_sha256")):
            require(data.get(key)==sha(root/src)==sha(path/inst),
                    "GA stable immutable source/Receipt not exact: "+key)
        composed=Path(str(data.get("composed_root","")))
        native=Path(str(data.get("native_root","")))
        require(composed.parent==native.parent==Path("/nix/store")
                and data.get("composed_receipt_sha256")==
                    sha(composed/"share/kagari-v010-composed/receipt.json")
                and data.get("native_receipt_sha256")==
                    sha(native/"share/kagari-v010-native-generic/receipt.json"),
                "GA stable native/composed immutable source receipts mismatch")
        verify_package(root,"composed",composed)
        verify_package(root,"native",native)
    else:
        raise CIBlocked("unsupported immutable KAGARI package")


def build(root: Path, package: str) -> tuple[str, str]:
    command = ["nix", "--extra-experimental-features", "nix-command flakes",
               "build", "--impure", "--no-link", "--print-out-paths", "--expr",
               package_expression(root, package)]
    print(f"KAGARI_CI_NIX_BUILD profile={package}", file=sys.stderr)
    result = subprocess.run(command, cwd=root, text=True, capture_output=True,
                            timeout=600, check=False)
    require(result.returncode == 0,
            "Nix build failed: " + result.stderr[-1800:])
    output = parse_store_path(result.stdout, package)
    verify_package(root, package, output)
    print(f"KAGARI_CI_PACKAGE_VERIFIED profile={package} "
          f"store_path={output.name}", file=sys.stderr)
    return PACKAGES[package][1], str(output)


def run(root: Path, shard: int, *, requirements_only: bool) -> list[str]:
    require(shard in SHARD_RANGE, "contract shard must be in range 0..7")
    needed = package_assignment(root)[shard]
    if requirements_only:
        return ["needs_nix="+("true" if needed else "false")]
    # Compose output atomically on stdout only after ALL required Nix builds
    # and package/source receipts passed. A failing build emits no assignments.
    verified = [build(root, package) for package in needed]
    return [key + "=" + value for key, value in verified]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--requirements-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = Path(__file__).resolve().parents[1]
        lines = run(root, args.shard, requirements_only=args.requirements_only)
    except (CIBlocked, OSError, subprocess.SubprocessError) as exc:
        print("KAGARI_CI_BLOCKED: " + str(exc), file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
