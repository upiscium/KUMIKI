"""#291: canonical metadata observation without mutating product Git metadata.

The existing #217 MetadataStore.fetch_tip() intentionally uses Git fetch, which
writes FETCH_HEAD/objects. Read-only Main observers use a transient private Git
object/ref environment while observing the *real* Project or Task Worktree for
HEAD, status, branch and native Worktree registration. No Task authority is
replaced; the same remote metadata ref/history validators are executed.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Callable, Iterator

from metadata_ref import MetadataRefError, MetadataStore


def _git(root: Path | None, *args: str,
         preserve_output: bool = False) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
    cmd = ["git", "-c", "core.hooksPath=/dev/null",
           "-c", "credential.helper=", "-c", "protocol.allow=never",
           "-c", "protocol.file.allow=always",
           "-c", "protocol.http.allow=always",
           "-c", "protocol.https.allow=always",
           "-c", "protocol.ssh.allow=always"]
    if root is not None:
        cmd.extend(["-C", str(root)])
    cmd.extend(args)
    try:
        result = subprocess.run(
            cmd, env=env, stdin=subprocess.DEVNULL, capture_output=True,
            timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        raise MetadataRefError("isolated metadata read Git operation unavailable") from None
    if result.returncode != 0 or len(result.stdout) > 16384:
        raise MetadataRefError("isolated metadata read Git operation failed")
    try:
        decoded = result.stdout.decode("utf-8", "strict")
        # git config -z uses exact NUL delimiters. Generic strip() would
        # erase leading/trailing whitespace and hide empty configured URLs,
        # defeating the fail-closed unambiguous-remote check below.
        return decoded if preserve_output else decoded.strip()
    except UnicodeError:
        raise MetadataRefError("isolated metadata read invalid Git result") from None


class _IsolatedMetadataReader(MetadataStore):
    """Metadata Git commands use scratch objects; Git facts use real root.

    MetadataStore._git and _read_fetch_head are the only upstream paths that
    select self.root for local Git storage. Scoped root switching is safe
    because the reader is private to one synchronous Main observe() call.
    This capability is read-only: no direct-ref CAS is supplied.
    """

    def __init__(self, real_root: Path, scratch: Path,
                 repository: str, remote: str) -> None:
        self._scratch = scratch
        super().__init__(real_root, repository, remote)

    def _git(self, arguments: list[str], **kwargs):
        original = self.root
        self.root = self._scratch
        try:
            return super()._git(arguments, **kwargs)
        finally:
            self.root = original

    def _read_fetch_head(self):
        original = self.root
        self.root = self._scratch
        try:
            return super()._read_fetch_head()
        finally:
            self.root = original


@contextmanager
def isolated_metadata_observation(
    origin: MetadataStore,
) -> Iterator[Callable[[Path], MetadataStore]]:
    """Create an ephemeral Git metadata reader; original Git is untouched.

    Only the fixed remote metadata ref can be fetched, and only into a
    disposable Git repository. The real Git object directory is linked as
    read-only alternates for #191 Task commit/base consistency. No local
    branch, index, FETCH_HEAD or Worktree admin is created in the Project.
    """
    if type(origin) is not MetadataStore:
        raise MetadataRefError("ordinary canonical metadata store required")
    # Preserve the source store's existing transport checks, rather than
    # reading only the scratch clone's sanitized configuration.
    origin._check_transport_configuration()
    fmt = _git(origin.root, "rev-parse", "--show-object-format")
    if fmt not in ("sha1", "sha256"):
        raise MetadataRefError("unsupported original Git object format")
    git_common = Path(_git(
        origin.root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    objects = git_common / "objects"
    if not git_common.is_dir() or not objects.is_dir():
        raise MetadataRefError("Git object directory unavailable")
    remote_name = origin._configured_remote_name()
    effective_remote = origin.remote
    # MetadataStore also supports direct relative file remotes. Git resolves
    # them from its Product root; private scratch Git has a different cwd.
    if remote_name is None and effective_remote.startswith(("./", "../")):
        effective_remote = str((origin.root / effective_remote).resolve())
    remote_url: str | None = None
    if remote_name is not None:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", remote_name):
            raise MetadataRefError("unsupported remote name for isolated observation")
        # Git supports multiple fetch URLs for one remote. Silently
        # selecting only the first can change which authority we observe.
        configured = _git(origin.root, "config", "--local", "--null",
                          "--get-all", f"remote.{remote_name}.url",
                          preserve_output=True)
        if not configured.endswith("\0") or configured.count("\0") != 1:
            raise MetadataRefError("remote URL missing or ambiguous")
        remote_url = configured[:-1]
        if (not remote_url or remote_url != remote_url.strip()
                or "\n" in remote_url or "\r" in remote_url):
            raise MetadataRefError("remote URL missing or ambiguous")
        if ("://" not in remote_url and ":" not in remote_url
                and not Path(remote_url).is_absolute()):
            # Git's relative local remotes resolve relative to the source
            # working tree. The transient Git root must use the same target.
            remote_url = str((origin.root / remote_url).resolve())

    with tempfile.TemporaryDirectory(prefix="kagari-readonly-metadata-") as tmp:
        scratch = Path(tmp) / "git"
        _git(None, "init", "-q", f"--object-format={fmt}", str(scratch))
        alternate = scratch / ".git" / "objects" / "info" / "alternates"
        alternate.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        alternate.write_text(str(objects) + "\n", encoding="utf-8")
        if remote_name is not None:
            assert remote_url is not None
            _git(scratch, "remote", "add", remote_name, remote_url)

        def reader(real_root: Path) -> MetadataStore:
            # Independent readers share only private scratch Git storage.
            return _IsolatedMetadataReader(real_root, scratch,
                                           origin.repository, effective_remote)
        yield reader
