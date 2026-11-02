"""Conservative no-model diagnostic for an explicitly configured Claude CLI.

The default probes only version and help. ``--probe-auth`` additionally invokes
``claude auth status``, which may use the network or create/update local state.
This is not a sandbox, does not start a model request, and confers no execution
or readiness authority. Its finite settings scan cannot prove effective policy.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Mapping


SCHEMA_VERSION = 1
EXECUTOR = "claude-cli"
QUALIFICATION = "UNQUALIFIED"
EFFECTIVE_POLICY = "UNVERIFIED"
DISPATCH_ALLOWED = False

PROFILE_NAMES = ("personal", "company")
SUBSCRIPTION_TYPES = frozenset(("pro", "max", "ultra", "team", "enterprise"))
CANDIDATE_VERSIONS = frozenset(("2.1.293",))
CAPABILITY_FLAGS = (
    "--print",
    "--output-format",
    "--permission-mode",
    "--session-id",
    "--resume",
)
EXPECTED_CAPABILITIES = frozenset(CAPABILITY_FLAGS)

CONFIG_LIMIT = 64 * 1024
SETTINGS_LIMIT = 64 * 1024
COMMAND_OUTPUT_LIMIT = 64 * 1024
SETTINGS_SCAN_LIMIT = 256 * 1024
MAX_SETTINGS_FILES = 32
MAX_SETTINGS_ENTRIES = 128
DEFAULT_TIMEOUT = 5.0
DEFAULT_MANAGED_SETTINGS_ROOT = Path("/etc/claude-code")

_VERSION_LINE = re.compile(
    r"\A(?:Claude Code\s+)?v?(?P<version>[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})"
    r"(?:\s+\(Claude Code\))?\Z"
)
class _DuplicateJSONKey(ValueError):
    pass


class _ScanProblem(Exception):
    """A settings/configuration scan could not be completed safely."""


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError


def _loads_strict(data: bytes) -> Any:
    text = data.decode("utf-8", errors="strict")
    return json.loads(
        text,
        object_pairs_hook=_pairs_without_duplicates,
        parse_constant=_reject_json_constant,
    )


def _result(
    profile: str,
    status: str,
    *,
    version: str | None = None,
    capabilities: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    reason = {
        "VERSION_CHECKED": "version_checked",
        "AUTH_STATUS_MATCHED": "auth_status_matched",
        "NOT_INSTALLED": "not_installed",
        "NOT_AUTHENTICATED": "not_authenticated",
        "UNSUPPORTED_VERSION": "unsupported_version",
        "WRONG_BILLING_MODE": "wrong_billing_mode",
        "WRONG_PROFILE": "wrong_profile",
        "PROFILE_UNAVAILABLE": "profile_unavailable",
        "INVALID_CONFIGURATION": "invalid_configuration",
        "UNKNOWN": "unknown",
    }[status]
    return {
        "schema_version": SCHEMA_VERSION,
        "executor": EXECUTOR,
        "profile": profile if profile in PROFILE_NAMES else "personal",
        "status": status,
        "reason": reason,
        "version": version,
        "capabilities": list(capabilities),
        "executor_qualification": QUALIFICATION,
        "dispatch_allowed": DISPATCH_ALLOWED,
        "effective_policy": EFFECTIVE_POLICY,
    }


def _no_symlink_components(path: Path) -> bool:
    """Return true only when every existing component is non-symlink."""
    if not path.is_absolute():
        return False
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current = current / part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                return False
            if stat.S_ISLNK(mode):
                return False
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _canonical_directory(value: Any) -> Path | None:
    if not isinstance(value, str) or not value or not os.path.isabs(value):
        return None
    path = Path(value)
    if not _no_symlink_components(path):
        return None
    try:
        mode = path.stat().st_mode
        canonical = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not stat.S_ISDIR(mode) or str(canonical) != value:
        return None
    return canonical


def _canonical_config_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(os.path.abspath(path))


def _config_file_signature(path: Path) -> tuple[int, ...] | None:
    if not _no_symlink_components(path):
        return None
    try:
        info = path.lstat()
    except (OSError, ValueError):
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    return _SettingsBudget._signature(info)


def _profile_directory_identity(path: Path) -> tuple[int, int, int] | None:
    if _canonical_directory(str(path)) != path:
        return None
    try:
        info = path.lstat()
    except (OSError, ValueError):
        return None
    if not stat.S_ISDIR(info.st_mode):
        return None
    return info.st_dev, info.st_ino, info.st_mode


def _read_regular_file(path: Path, limit: int, *, strict_ancestors: bool = False) -> bytes:
    if strict_ancestors and not _no_symlink_components(path):
        raise _ScanProblem
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except (OSError, ValueError) as exc:
        raise _ScanProblem from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise _ScanProblem
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(fd, min(8192, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit:
            raise _ScanProblem
        return data
    finally:
        os.close(fd)


def _read_config(path_value: str | os.PathLike[str]) -> tuple[dict[str, Any] | None, str]:
    try:
        path = Path(path_value)
        if not path.is_absolute():
            path = Path(os.path.abspath(path))
        data = _read_regular_file(path, CONFIG_LIMIT, strict_ancestors=True)
        document = _loads_strict(data)
    except (OSError, ValueError, UnicodeError, RecursionError, _ScanProblem, _DuplicateJSONKey):
        return None, "INVALID_CONFIGURATION"
    if not isinstance(document, dict) or set(document) != {"schema_version", "claude_bin", "profiles"}:
        return None, "INVALID_CONFIGURATION"
    if type(document.get("schema_version")) is not int or document["schema_version"] != SCHEMA_VERSION:
        return None, "INVALID_CONFIGURATION"
    if not isinstance(document.get("claude_bin"), str) or not document["claude_bin"]:
        return None, "INVALID_CONFIGURATION"
    profiles = document.get("profiles")
    if not isinstance(profiles, dict) or "personal" not in profiles or not set(profiles).issubset(PROFILE_NAMES):
        return None, "INVALID_CONFIGURATION"
    canonical_profiles: dict[str, tuple[Path, str]] = {}
    for name, profile in profiles.items():
        if not isinstance(profile, dict) or set(profile) != {"config_dir", "subscription_type"}:
            return None, "INVALID_CONFIGURATION"
        config_dir_value = profile.get("config_dir")
        subscription_type = profile.get("subscription_type")
        if not isinstance(config_dir_value, str) or not os.path.isabs(config_dir_value):
            return None, "INVALID_CONFIGURATION"
        if not isinstance(subscription_type, str) or subscription_type not in SUBSCRIPTION_TYPES:
            return None, "INVALID_CONFIGURATION"
        directory = _canonical_directory(config_dir_value)
        if directory is None:
            # Missing configured profiles are distinguishable from malformed paths.
            candidate = Path(config_dir_value)
            try:
                candidate.lstat()
            except FileNotFoundError:
                return None, "PROFILE_UNAVAILABLE"
            except (OSError, ValueError):
                return None, "PROFILE_UNAVAILABLE"
            return None, "INVALID_CONFIGURATION"
        canonical_profiles[name] = (directory, subscription_type)
    configured_dirs = [item[0] for item in canonical_profiles.values()]
    for index, left in enumerate(configured_dirs):
        for right in configured_dirs[index + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                return None, "INVALID_CONFIGURATION"
    document["_validated_profiles"] = canonical_profiles
    return document, ""


def _executable_path(value: str) -> tuple[Path | None, str]:
    if not os.path.isabs(value):
        return None, "INVALID_CONFIGURATION"
    path = Path(value)
    try:
        canonical = path.resolve(strict=True)
        info = canonical.stat()
    except FileNotFoundError:
        return None, "NOT_INSTALLED"
    except OSError:
        return None, "NOT_INSTALLED"
    except (RuntimeError, ValueError):
        return None, "INVALID_CONFIGURATION"
    if not stat.S_ISREG(info.st_mode) or not os.access(canonical, os.X_OK):
        return None, "INVALID_CONFIGURATION"
    return canonical, ""


def _executable_identity(value: str, expected: Path) -> tuple[int, ...] | None:
    try:
        current = Path(value).resolve(strict=True)
        info = current.stat()
    except (OSError, RuntimeError, ValueError):
        return None
    if current != expected or not stat.S_ISREG(info.st_mode) or not os.access(current, os.X_OK):
        return None
    return _SettingsBudget._signature(info)


def _looks_like_override(name: str) -> bool:
    upper = name.upper()
    if name == "CLAUDE_CONFIG_DIR":
        return False
    if upper.startswith("ANTHROPIC_"):
        return True
    if upper.startswith(("CLAUDE_", "CLAUDE_CODE_")):
        return True
    if upper.startswith(("NODE_OPTIONS", "NODE_PATH", "PYTHONPATH", "PYTHONHOME")):
        return True
    if upper.startswith(("LD_", "DYLD_")):
        return True
    if upper.startswith(("OTEL_EXPORTER_", "AWS_", "GOOGLE_", "GCLOUD_", "GCP_", "AZURE_")):
        return True
    if upper in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}:
        return True
    if upper.endswith(("_API_KEY", "_AUTH_TOKEN", "_ACCESS_TOKEN")) or upper in {
        "API_KEY",
        "AUTH_TOKEN",
        "ACCESS_TOKEN",
    }:
        return True
    if upper in {
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
        "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
    }:
        return True
    return False


def _environment_has_override(environ: Mapping[str, Any]) -> bool:
    return any(isinstance(key, str) and _looks_like_override(key) for key in environ)


_BILLING_SETTING_KEYS = frozenset(
    {
        "apikeyhelper",
        "apikey",
        "authtoken",
        "oauthtoken",
        "authmethod",
        "apiprovider",
        "provider",
        "modelprovider",
        "model",
        "defaultmodel",
        "fallbackmodel",
        "baseurl",
        "apibaseurl",
        "subscriptiontype",
        "forceloginmethod",
        "loginmethod",
        "configdir",
        "configdirectory",
        "credential",
        "credentials",
        "awsprofile",
        "awsregion",
        "usebedrock",
        "usevertex",
        "usegooglevertex",
    }
)
_UNSAFE_SETTING_KEYS = frozenset(
    {
        "hooks",
        "plugins",
        "enabledplugins",
        "plugindirs",
        "pluginmarketplaces",
        "mcpservers",
        "mcp",
        "mcpconfig",
        "mcptoolsets",
        "statusline",
        "hook",
        "plugin",
        "managedsettings",
        "managedsettingsurl",
        "remotesettings",
        "remotesettingsurl",
        "settingssources",
        "cloudmanagedsettings",
        "awsauthrefresh",
        "awscredentialexport",
        "gcpauthrefresh",
        "extraknownmarketplaces",
        "strictknownmarketplaces",
        "knownmarketplaces",
        "marketplaces",
    }
)


def _inspect_settings_value(value: Any) -> str:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                return "UNKNOWN"
            lowered = key.lower()
            if lowered == "apikeyhelper" or lowered in _BILLING_SETTING_KEYS:
                return "WRONG_BILLING_MODE"
            if lowered in _UNSAFE_SETTING_KEYS:
                return "UNKNOWN"
            if lowered == "env":
                if not isinstance(child, dict):
                    return "UNKNOWN"
                for env_key in child:
                    if not isinstance(env_key, str):
                        return "UNKNOWN"
                    if env_key == "CLAUDE_CONFIG_DIR" or _looks_like_override(env_key):
                        return "WRONG_BILLING_MODE"
            result = _inspect_settings_value(child)
            if result:
                return result
    elif isinstance(value, list):
        for child in value:
            result = _inspect_settings_value(child)
            if result:
                return result
    return ""


def _optional_lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise _ScanProblem from exc


def _existing_components_no_symlink(path: Path) -> bool:
    """Check existing ancestors while allowing a not-yet-created tail."""
    if not path.is_absolute():
        return False
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current = current / part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                return True
            if stat.S_ISLNK(mode):
                return False
        return True
    except (OSError, ValueError):
        return False


class _SettingsBudget:
    def __init__(self) -> None:
        self.file_count = 0
        self.entry_count = 0
        self.total_bytes = 0
        self.records: dict[str, tuple[int, ...] | None] = {}

    @staticmethod
    def _signature(info: os.stat_result | None) -> tuple[int, ...] | None:
        if info is None:
            return None
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    def record(self, path: Path, info: os.stat_result | None) -> None:
        key = str(path)
        signature = self._signature(info)
        if key in self.records and self.records[key] != signature:
            raise _ScanProblem
        self.records[key] = signature

    def charge_file(self, info: os.stat_result) -> None:
        self.file_count += 1
        self.total_bytes += info.st_size
        if (
            self.file_count > MAX_SETTINGS_FILES
            or info.st_size > SETTINGS_LIMIT
            or self.total_bytes > SETTINGS_SCAN_LIMIT
        ):
            raise _ScanProblem

    def charge_entry(self) -> None:
        self.entry_count += 1
        if self.entry_count > MAX_SETTINGS_ENTRIES:
            raise _ScanProblem

    def snapshot(self) -> tuple[tuple[str, tuple[int, ...] | None], ...]:
        return tuple(sorted(self.records.items()))


def _bounded_scandir(path: Path, budget: _SettingsBudget) -> list[tuple[str, os.stat_result]]:
    entries: list[tuple[str, os.stat_result]] = []
    try:
        with os.scandir(path) as iterator:
            for entry in iterator:
                budget.charge_entry()
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise _ScanProblem from exc
                budget.record(path / entry.name, info)
                entries.append((entry.name, info))
    except (OSError, ValueError) as exc:
        raise _ScanProblem from exc
    return entries


def _inspect_optional_settings_file(path: Path, budget: _SettingsBudget) -> str:
    info = _optional_lstat(path)
    budget.record(path, info)
    if info is None:
        return ""
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return "UNKNOWN"
    try:
        budget.charge_file(info)
        value = _loads_strict(_read_regular_file(path, SETTINGS_LIMIT, strict_ancestors=True))
    except (OSError, ValueError, UnicodeError, RecursionError, _ScanProblem, _DuplicateJSONKey):
        return "UNKNOWN"
    if not isinstance(value, dict):
        return "UNKNOWN"
    return _inspect_settings_value(value)


def _inspect_project_settings(project_root: Path, budget: _SettingsBudget) -> str:
    claude_dir = project_root / ".claude"
    info = _optional_lstat(claude_dir)
    budget.record(claude_dir, info)
    if info is None:
        return ""
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return "UNKNOWN"
    for name in ("settings.json", "settings.local.json"):
        result = _inspect_optional_settings_file(claude_dir / name, budget)
        if result:
            return result
    return ""


def _inspect_managed_settings(root: Path, budget: _SettingsBudget) -> str:
    if not root.is_absolute():
        return "UNKNOWN"
    if not _existing_components_no_symlink(root):
        return "UNKNOWN"
    try:
        info = _optional_lstat(root)
    except _ScanProblem:
        return "UNKNOWN"
    budget.record(root, info)
    if info is None:
        return ""
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return "UNKNOWN"
    try:
        entries = _bounded_scandir(root, budget)
    except _ScanProblem:
        return "UNKNOWN"
    for name, entry_info in entries:
        if name not in {"managed-settings.json", "managed-settings.d"}:
            return "UNKNOWN"
        if stat.S_ISLNK(entry_info.st_mode):
            return "UNKNOWN"
    main_file = root / "managed-settings.json"
    result = _inspect_optional_settings_file(main_file, budget)
    if result:
        return result
    settings_dir = root / "managed-settings.d"
    try:
        dir_info = _optional_lstat(settings_dir)
    except _ScanProblem:
        return "UNKNOWN"
    budget.record(settings_dir, dir_info)
    if dir_info is None:
        return ""
    if stat.S_ISLNK(dir_info.st_mode) or not stat.S_ISDIR(dir_info.st_mode):
        return "UNKNOWN"
    try:
        files = _bounded_scandir(settings_dir, budget)
    except _ScanProblem:
        return "UNKNOWN"
    for name, file_info in files:
        if not name.endswith(".json"):
            return "UNKNOWN"
        if stat.S_ISLNK(file_info.st_mode) or not stat.S_ISREG(file_info.st_mode):
            return "UNKNOWN"
    for name, _ in sorted(files, key=lambda item: item[0]):
        result = _inspect_optional_settings_file(settings_dir / name, budget)
        if result:
            return result
    return ""


def _inspect_settings(
    selected_profile_dir: Path,
    project_root: Path,
    managed_settings_root: Path,
) -> tuple[str, tuple[tuple[str, tuple[int, ...] | None], ...]]:
    budget = _SettingsBudget()
    try:
        profile_result = _inspect_optional_settings_file(selected_profile_dir / "settings.json", budget)
        if profile_result:
            return profile_result, budget.snapshot()
        project_result = _inspect_project_settings(project_root, budget)
        if project_result:
            return project_result, budget.snapshot()
        managed_result = _inspect_managed_settings(managed_settings_root, budget)
        return managed_result, budget.snapshot()
    except Exception:
        return "UNKNOWN", budget.snapshot()


def _child_environment(environ: Mapping[str, Any], config_dir: Path) -> dict[str, str]:
    allowed = ("HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "TERM", "TMPDIR")
    child = {key: environ[key] for key in allowed if isinstance(environ.get(key), str)}
    child["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return child


def _signal_process_group(process: subprocess.Popen[bytes], sig: int) -> None:
    try:
        os.killpg(process.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            if sig == signal.SIGKILL and process.poll() is None:
                process.kill()
        except OSError:
            pass


def _stop_process_group(process: subprocess.Popen[bytes]) -> bool:
    _signal_process_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=0.04)
    except subprocess.TimeoutExpired:
        pass
    _signal_process_group(process, signal.SIGKILL)
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        _signal_process_group(process, signal.SIGKILL)
        # A kernel-blocked child may not be reapable yet. Do not turn a bounded
        # diagnostic into an unlimited wait or report that cleanup succeeded.
        return False
    return True


def _run_bounded(
    executable: Path,
    args: tuple[str, ...],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
) -> dict[str, Any] | None:
    if timeout <= 0:
        return {"timed_out": True, "overflow": False, "io_error": False}
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    streams: dict[str, Any] = {}
    output = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    overflow = False
    timed_out = False
    io_error = False
    deadline = time.monotonic() + timeout
    returncode: int | None = None
    try:
        process = subprocess.Popen(
            [str(executable), *args],
            cwd=str(cwd),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
        streams = {"stdout": process.stdout, "stderr": process.stderr}
        selector = selectors.DefaultSelector()
        for name, stream in streams.items():
            assert stream is not None
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            if process.poll() is not None:
                returncode = process.returncode
                # Stop descendants that may have inherited either pipe.
                _signal_process_group(process, signal.SIGKILL)
            if selector.get_map():
                events = selector.select(min(remaining, 0.1))
            else:
                # EOF may precede a normal process exit. Wait only in short
                # intervals so a silent hang still hits this invocation's deadline.
                try:
                    returncode = process.wait(timeout=min(remaining, 0.05))
                    break
                except subprocess.TimeoutExpired:
                    continue
            for key, _ in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), min(8192, COMMAND_OUTPUT_LIMIT + 1 - total))
                except BlockingIOError:
                    continue
                except OSError:
                    io_error = True
                    break
                if not chunk:
                    try:
                        selector.unregister(key.fileobj)
                    except (KeyError, ValueError):
                        pass
                    try:
                        key.fileobj.close()
                    except OSError:
                        pass
                    continue
                total += len(chunk)
                if total > COMMAND_OUTPUT_LIMIT:
                    overflow = True
                    break
                output[key.data].extend(chunk)
            if overflow or io_error:
                break
        if not timed_out and not overflow and not io_error and process.poll() is not None:
            returncode = process.returncode
    except Exception:
        io_error = True
    finally:
        if process is not None:
            try:
                if not _stop_process_group(process):
                    io_error = True
            except Exception:
                _signal_process_group(process, signal.SIGKILL)
                try:
                    process.kill()
                except Exception:
                    pass
                try:
                    process.wait(timeout=1.0)
                except Exception:
                    pass
        if selector is not None:
            try:
                selector.close()
            except Exception:
                pass
        for stream in streams.values():
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
    if process is None or io_error:
        return None
    if timed_out or overflow:
        return {"timed_out": timed_out, "overflow": overflow, "io_error": False}
    if returncode is None:
        return None
    return {
        "stdout": bytes(output["stdout"]),
        "stderr": bytes(output["stderr"]),
        "returncode": returncode,
        "timed_out": False,
        "overflow": False,
        "io_error": False,
    }


def _parse_version(output: bytes) -> str | None:
    try:
        text = output.decode("utf-8", errors="strict").strip()
    except UnicodeError:
        return None
    match = _VERSION_LINE.fullmatch(text)
    return match.group("version") if match else None


def _capabilities(output: bytes) -> tuple[str, ...] | None:
    try:
        text = output.decode("utf-8", errors="strict")
    except UnicodeError:
        return None
    found = []
    for flag in CAPABILITY_FLAGS:
        if re.search(r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])", text):
            found.append(flag)
    return tuple(found)


def _check_auth_status(
    run: Mapping[str, Any],
    *,
    expected_plan: str,
    selected_dir: Path,
) -> str:
    if run.get("timed_out") or run.get("overflow") or run.get("io_error"):
        return "UNKNOWN"
    try:
        document = _loads_strict(run["stdout"])
    except (KeyError, ValueError, UnicodeError, RecursionError, _DuplicateJSONKey):
        return "UNKNOWN"
    if not isinstance(document, dict) or type(document.get("loggedIn")) is not bool:
        return "UNKNOWN"
    logged_in = document["loggedIn"]
    code = run.get("returncode")
    if not logged_in:
        return "NOT_AUTHENTICATED" if code == 1 else "UNKNOWN"
    if code != 0:
        return "UNKNOWN"
    required = ("authMethod", "apiProvider", "subscriptionType", "configDirectory")
    if any(key not in document for key in required):
        return "UNKNOWN"
    if any(not isinstance(document[key], str) for key in required):
        return "UNKNOWN"
    if document["configDirectory"] != str(selected_dir):
        return "WRONG_PROFILE"
    if (
        document["authMethod"] != "claude.ai"
        or document["apiProvider"] != "firstParty"
        or document["subscriptionType"] != expected_plan
    ):
        return "WRONG_BILLING_MODE"
    return "AUTH_STATUS_MATCHED"


def _run_preflight_impl(
    config: str | os.PathLike[str],
    project_root: str | os.PathLike[str],
    *,
    profile: str = "personal",
    probe_auth: bool = False,
    environ: Mapping[str, Any] | None = None,
    managed_settings_root: str | os.PathLike[str] = DEFAULT_MANAGED_SETTINGS_ROOT,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Implementation for the public exception-safe preflight wrapper."""
    safe_profile = profile if profile in PROFILE_NAMES else "personal"
    process_environment: Mapping[str, Any] = os.environ if environ is None else environ
    if profile not in PROFILE_NAMES:
        return _result(safe_profile, "INVALID_CONFIGURATION")
    if _environment_has_override(process_environment):
        return _result(safe_profile, "WRONG_BILLING_MODE")
    try:
        supplied_project = Path(project_root)
        if not supplied_project.is_absolute():
            return _result(safe_profile, "INVALID_CONFIGURATION")
        project = supplied_project.resolve(strict=True)
        if not project.is_dir():
            return _result(safe_profile, "INVALID_CONFIGURATION")
    except (OSError, RuntimeError, ValueError, TypeError):
        return _result(safe_profile, "INVALID_CONFIGURATION")

    config_path = _canonical_config_path(config)
    document, config_status = _read_config(config_path)
    if document is None:
        return _result(safe_profile, config_status)
    profiles: dict[str, tuple[Path, str]] = document["_validated_profiles"]
    selected = profiles.get(profile)
    if selected is None:
        return _result(safe_profile, "PROFILE_UNAVAILABLE")
    selected_dir, expected_plan = selected
    config_signature = _config_file_signature(config_path)
    profile_identity = _profile_directory_identity(selected_dir)
    if config_signature is None or profile_identity is None:
        return _result(safe_profile, "UNKNOWN")

    executable, executable_status = _executable_path(document["claude_bin"])
    if executable is None:
        return _result(safe_profile, executable_status)
    executable_identity = _executable_identity(document["claude_bin"], executable)
    if executable_identity is None:
        return _result(safe_profile, "UNKNOWN")
    try:
        managed_root = Path(managed_settings_root)
    except (TypeError, ValueError):
        return _result(safe_profile, "UNKNOWN")
    settings_status, settings_snapshot = _inspect_settings(selected_dir, project, managed_root)
    if settings_status:
        return _result(safe_profile, settings_status)

    def state_unchanged() -> bool:
        if _config_file_signature(config_path) != config_signature:
            return False
        if _executable_identity(document["claude_bin"], executable) != executable_identity:
            return False
        if _profile_directory_identity(selected_dir) != profile_identity:
            return False
        current_status, current_snapshot = _inspect_settings(selected_dir, project, managed_root)
        return not current_status and current_snapshot == settings_snapshot

    def run_probe(args: tuple[str, ...]) -> tuple[dict[str, Any] | None, bool]:
        if not state_unchanged():
            return None, False
        probe_result = _run_bounded(
            executable, args, cwd=project, env=child_env, timeout=timeout
        )
        if not state_unchanged():
            return probe_result, False
        return probe_result, True

    child_env = _child_environment(process_environment, selected_dir)
    version_run, stable = run_probe(("--version",))
    if (
        not stable
        or version_run is None
        or version_run.get("timed_out")
        or version_run.get("overflow")
        or version_run.get("io_error")
        or version_run.get("returncode") != 0
    ):
        return _result(safe_profile, "UNKNOWN")
    version = _parse_version(version_run["stdout"])
    if version is None:
        return _result(safe_profile, "UNKNOWN")
    if version not in CANDIDATE_VERSIONS:
        return _result(safe_profile, "UNSUPPORTED_VERSION", version=version)

    help_run, stable = run_probe(("--help",))
    if (
        not stable
        or help_run is None
        or help_run.get("timed_out")
        or help_run.get("overflow")
        or help_run.get("io_error")
        or help_run.get("returncode") != 0
    ):
        return _result(safe_profile, "UNKNOWN", version=version)
    help_output = help_run["stdout"] + help_run["stderr"]
    capability_flags = _capabilities(help_output)
    if capability_flags is None:
        return _result(safe_profile, "UNKNOWN", version=version)
    if set(capability_flags) != EXPECTED_CAPABILITIES:
        return _result(
            safe_profile,
            "UNSUPPORTED_VERSION",
            version=version,
            capabilities=capability_flags,
        )
    if not probe_auth:
        return _result(
            safe_profile,
            "VERSION_CHECKED",
            version=version,
            capabilities=capability_flags,
        )

    auth_run, stable = run_probe(("auth", "status"))
    if not stable or auth_run is None:
        return _result(
            safe_profile, "UNKNOWN", version=version, capabilities=capability_flags
        )
    auth_status = _check_auth_status(
        auth_run, expected_plan=expected_plan, selected_dir=selected_dir
    )
    return _result(
        safe_profile, auth_status, version=version, capabilities=capability_flags
    )


def run_preflight(
    config: str | os.PathLike[str],
    project_root: str | os.PathLike[str],
    *,
    profile: str = "personal",
    probe_auth: bool = False,
    environ: Mapping[str, Any] | None = None,
    managed_settings_root: str | os.PathLike[str] = DEFAULT_MANAGED_SETTINGS_ROOT,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """No-model diagnostic for operator-trusted local filesystems only.

    The timeout bounds child observation, not kernel filesystem operations.
    File/entry/output counts are bounded; hostile or stalled mounts are not a
    supported input. This API must not be exposed as an untrusted path service.
    """
    safe_profile = profile if profile in PROFILE_NAMES else "personal"
    try:
        return _run_preflight_impl(
            config,
            project_root,
            profile=profile,
            probe_auth=probe_auth,
            environ=environ,
            managed_settings_root=managed_settings_root,
            timeout=timeout,
        )
    except Exception:
        # No path, environment value, or exception text escapes via this API.
        return _result(safe_profile, "UNKNOWN")


def _absolute_directory_argument(value: str) -> bool:
    return os.path.isabs(value)


class _FixedErrorArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        # argparse's normal error includes the supplied token/value in stderr.
        sys.stdout.write(
            json.dumps(_result("personal", "INVALID_CONFIGURATION"), sort_keys=True, separators=(",", ":"))
            + "\n"
        )
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    parser = _FixedErrorArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, metavar="PATH")
    parser.add_argument("--project-root", required=True, metavar="ABS")
    parser.add_argument("--profile", choices=PROFILE_NAMES, default="personal")
    parser.add_argument("--probe-auth", action="store_true")
    args = parser.parse_args(argv)
    if not _absolute_directory_argument(args.project_root):
        result = _result(args.profile, "INVALID_CONFIGURATION")
    else:
        try:
            result = run_preflight(
                args.config,
                args.project_root,
                profile=args.profile,
                probe_auth=args.probe_auth,
            )
        except Exception:
            # Keep unexpected implementation/runtime errors from exposing paths or
            # subprocess exception text in this fixed-output diagnostic.
            result = _result(args.profile, "UNKNOWN")
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0 if result["status"] in {"VERSION_CHECKED", "AUTH_STATUS_MATCHED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
