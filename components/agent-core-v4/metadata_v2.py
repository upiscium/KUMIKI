"""Source-only Metadata v2: bounded Git objects, never a Git transport.

The Host supplies exact object reads and (optionally) narrowly scoped mutations.
Neither metadata nor a callback supplied by an untrusted caller is authority.
No v1 MetadataStore mutator, ref override, subprocess or global Host is used.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import metadata_codec as codec
from evidence import decode_evidence
from task_record import decode_contract, decode_record
from task_view import decode_snapshot

ARCHIVE_REF = "refs/heads/kagari/metadata"
_OID = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_TASK = re.compile(r"[1-9][0-9]{0,127}\Z", re.ASCII)
_OBJECT = re.compile(
    r"objects/(task-record|contract|evidence|task-view-snapshot)/"
    r"([0-9a-f]{2})/([0-9a-f]{64})\.json\Z", re.ASCII,
)
_POINTER = re.compile(r"tasks/([1-9][0-9]{0,127})/record\Z", re.ASCII)
_ID_LINE = re.compile(rb"[0-9a-f]{64}\n\Z", re.ASCII)


class InvalidMetadata(ValueError):
    """Invalid, unavailable, conflicting or over-budget metadata."""


class UncertainPublication(InvalidMetadata):
    """Mutation may have happened. Preserve Issue ref; do not retry or delete."""


def issue_ref(task: str) -> str:
    if type(task) is not str or not _TASK.fullmatch(task):
        raise InvalidMetadata("task must be a canonical positive decimal")
    return f"refs/heads/kagari-metadata/{task}"


def oid(value: str) -> str:
    # This Phase 1 Git-object adapter deliberately supports SHA-1 Git only.
    # Metadata content IDs remain SHA-256, independently of Git's hash format.
    if type(value) is not str or not _OID.fullmatch(value):
        raise InvalidMetadata("expected exact SHA-1 Git OID; other formats not implemented")
    return value


def git_id(kind: str, data: bytes) -> str:
    if kind not in {"blob", "tree", "commit"} or type(data) is not bytes:
        raise InvalidMetadata("invalid Git object")
    return hashlib.sha1(f"{kind} {len(data)}\0".encode() + data).hexdigest()


# A canonical payload can encode hundreds of thousands of empty containers
# in under 1 MiB, causing much larger retained Python allocations. This
# conservative structural count runs BEFORE JSON decoding and excludes braces
# inside quoted strings. It limits objects per validation, not OS RSS.
MAX_CONTAINERS_PER_OBJECT = 8_192


def json_container_markers(data: bytes) -> int:
    if type(data) is not bytes:
        raise InvalidMetadata("canonical JSON bytes required")
    quoted = False
    escaped = False
    count = 0
    for byte in data:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                quoted = False
        elif byte == 0x22:
            quoted = True
        elif byte in (0x7B, 0x5B):
            count += 1
            if count > MAX_CONTAINERS_PER_OBJECT:
                raise InvalidMetadata("decoded JSON container budget exceeded")
    return count


@dataclass(frozen=True)
class Limits:
    commits: int = 256
    entries: int = 4096
    bytes: int = 32 * 1024 * 1024
    containers: int = 32_768
    seconds: float = 10.0

    def __post_init__(self) -> None:
        if (any(type(n) is not int or not 1 <= n <= cap for n, cap in
                ((self.commits, 1024), (self.entries, 16384),
                 (self.bytes, 128 * 1024 * 1024), (self.containers, 131_072)))
                or type(self.seconds) not in (int, float) or not 0 < self.seconds <= 60):
            raise InvalidMetadata("invalid resource limits")


@dataclass(frozen=True)
class RefState:
    """Trusted Host observation of one exact ref, not an ls-remote inference.

    Host must reject uncertain reads and prove directness. Same-OID symbolic
    substitution still requires a server-side transaction check on mutation.
    """

    ref: str
    commit: str | None
    direct: bool


@dataclass(frozen=True)
class Update:
    """Exact Host authorization/CAS request; never a transferable credential.

    The trusted Host confirms current KUMIKI-owned refs, expected-old values,
    genesis/repository provenance and first-lifecycle reservation. For archive,
    it also verifies terminal Task and no KUMIKI-owned concurrent writers.
    Ordinary Git has no cross-ref atomic CAS or immutable server retention.
    The optional retention_policy label remains for old journal compatibility;
    None means ordinary branch mode, not a GitHub Ruleset grant.
    It stores bundle objects, then non-force updates ONLY `ref` to candidate.
    """

    repository: str
    genesis: str
    task: str
    ref: str
    expected_archive: str
    expected_issue: str | None
    candidate: str
    retention_policy: str | None = None


@dataclass(frozen=True)
class Bundle:
    commit: str
    objects: Mapping[str, tuple[str, bytes]]


@dataclass(frozen=True)
class Snapshot:
    files: Mapping[str, bytes]
    objects: Mapping[str, dict[str, Any]]
    pointers: Mapping[str, str]

    @property
    def tasks(self) -> frozenset[str]:
        return frozenset(e["task"] for e in self.objects.values()) | self.pointers.keys()


@dataclass(frozen=True)
class Node:
    parents: tuple[str, ...]
    snapshot: Snapshot
    role: str  # genesis, issue, archive
    task: str | None


# Reader must enforce max_bytes/deadline BEFORE allocating/blocking. Core
# verifies them after return too, but cannot preempt arbitrary Python callbacks.
ObjectReader = Callable[[str, int, float], tuple[str, bytes]]
Observer = Callable[[str, float], RefState]
Writer = Callable[[Update, Bundle, float], bool]


class Validation:
    """One bounded operation; no persistent trust cache or nested new budgets."""

    def __init__(self, repository: str, genesis: str, reader: ObjectReader,
                 limits: Limits = Limits()) -> None:
        codec.encode_object("contract", repository, "1", "0" * 40, {})
        self.repository = repository
        self.genesis = oid(genesis)
        self.reader = reader
        self.limits = limits
        self.deadline = time.monotonic() + limits.seconds
        self.used_bytes = 0
        self.used_entries = 0
        self.used_containers = 0
        self.cache: dict[str, tuple[str, bytes]] = {}
        # Per-validation caches only. Hash-validated Git tree and canonical
        # content IDs are immutable; re-use decoder graphs between commits.
        self.tree_snapshots: dict[str, Snapshot] = {}
        self.decoded_objects: dict[tuple[str, str], tuple[bytes, dict[str, Any]]] = {}
        self.nodes: dict[str, Node] = {}

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise InvalidMetadata("validation deadline exceeded")

    def read(self, key: str, kind: str) -> bytes:
        self.check()
        oid(key)
        if key not in self.cache:
            maximum = min(self.limits.bytes - self.used_bytes, codec.MAX_OBJECT_BYTES)
            if maximum <= 0:
                raise InvalidMetadata("validation byte limit exceeded")
            try:
                result = self.reader(key, maximum, self.deadline)
            except Exception as error:
                raise InvalidMetadata("Git object unavailable") from error
            self.check()
            if (type(result) is not tuple or len(result) != 2
                    or type(result[1]) is not bytes or len(result[1]) > maximum
                    or git_id(result[0], result[1]) != key):
                raise InvalidMetadata("Git object type/size/digest mismatch")
            self.used_bytes += len(result[1])
            self.cache[key] = result
        actual_kind, data = self.cache[key]
        if actual_kind != kind:
            raise InvalidMetadata("wrong Git object type")
        return data

    def tree(self, key: str) -> Snapshot:
        self.check()
        oid(key)
        cached = self.tree_snapshots.get(key)
        if cached is not None:
            return cached
        files: dict[str, bytes] = {}
        pending = [(key, "", 0)]
        while pending:
            tree_oid, prefix, depth = pending.pop()
            if depth > 4:
                raise InvalidMetadata("tree depth limit exceeded")
            data = self.read(tree_oid, "tree")
            if prefix and not data:
                raise InvalidMetadata("empty directory")
            offset = 0
            previous = b""
            while offset < len(data):
                self.check()
                self.used_entries += 1
                if self.used_entries > self.limits.entries:
                    raise InvalidMetadata("tree entry limit exceeded")
                end = data.find(b"\0", offset)
                if end < 0 or end + 21 > len(data):
                    raise InvalidMetadata("malformed Git tree")
                try:
                    mode, name = data[offset:end].split(b" ", 1)
                    text = name.decode("ascii")
                except (ValueError, UnicodeError) as error:
                    raise InvalidMetadata("malformed tree entry") from error
                if not text or text in {".", ".."} or "/" in text:
                    raise InvalidMetadata("invalid tree name")
                sort_key = name + (b"/" if mode == b"40000" else b"")
                if previous and sort_key <= previous:
                    raise InvalidMetadata("unsorted or duplicate Git tree")
                previous = sort_key
                child = data[end + 1:end + 21].hex()
                path = prefix + text
                if mode == b"40000":
                    if not (path in {"objects", "tasks"}
                            or re.fullmatch(r"objects/(task-record|contract|evidence|task-view-snapshot)(/[0-9a-f]{2})?", path)
                            or re.fullmatch(r"tasks/[1-9][0-9]{0,127}", path)):
                        raise InvalidMetadata("foreign directory")
                    pending.append((child, path + "/", depth + 1))
                elif mode == b"100644":
                    if path in files or not (_OBJECT.fullmatch(path) or _POINTER.fullmatch(path)):
                        raise InvalidMetadata("foreign or duplicate path")
                    files[path] = self.read(child, "blob")
                else:
                    raise InvalidMetadata("non-regular blob mode")
                offset = end + 21
        checked = self.snapshot(files)
        self.tree_snapshots[key] = checked
        return checked

    def snapshot(self, files: Mapping[str, bytes]) -> Snapshot:
        objects: dict[str, dict[str, Any]] = {}
        pointers: dict[str, str] = {}
        for path, data in files.items():
            self.check()
            match = _OBJECT.fullmatch(path)
            if match:
                kind, prefix, content_id = match.groups()
                if prefix != content_id[:2]:
                    raise InvalidMetadata("wrong content ID prefix")
                cached = self.decoded_objects.get((kind, content_id))
                if cached is not None:
                    if cached[0] != data:
                        raise InvalidMetadata("cached immutable object bytes conflict")
                    objects[content_id] = cached[1]
                    continue
                containers = json_container_markers(data)
                if self.used_containers + containers > self.limits.containers:
                    raise InvalidMetadata("cumulative decoded JSON container budget exceeded")
                self.used_containers += containers
                try:
                    envelope = codec.decode_object(data, expected_id=content_id,
                        expected_repository=self.repository, expected_kind=kind)
                    task, subject = envelope["task"], envelope["subject"]
                    if kind == "contract":
                        decode_contract(data, object_id=content_id, repository=self.repository,
                                        task=task, base_revision=subject)
                    elif kind == "task-record":
                        decode_record(data, object_id=content_id, repository=self.repository,
                            task=task, base_revision=subject, branch_ref=envelope["payload"].get("branch_ref"))
                    elif kind == "evidence":
                        decode_evidence(data, evidence_id=content_id, repository=self.repository,
                                        task=task, subject=subject)
                    else:
                        decode_snapshot(data, snapshot_id=content_id, repository=self.repository,
                                        task=task, subject=subject)
                except (ValueError, KeyError, TypeError) as error:
                    raise InvalidMetadata("invalid typed metadata object") from error
                self.decoded_objects[(kind, content_id)] = (data, envelope)
                objects[content_id] = envelope
            else:
                match = _POINTER.fullmatch(path)
                if not match or not _ID_LINE.fullmatch(data):
                    raise InvalidMetadata("invalid Task Record pointer")
                pointers[match[1]] = data[:-1].decode("ascii")
        for task, content_id in pointers.items():
            target = objects.get(content_id)
            if not target or target["kind"] != "task-record" or target["task"] != task:
                raise InvalidMetadata("missing or foreign Record target")
        for envelope in objects.values():
            self.check()
            if envelope["kind"] == "task-record":
                target = objects.get(envelope["payload"]["contract_id"])
                if (not target or target["kind"] != "contract"
                        or (target["task"], target["subject"]) != (envelope["task"], envelope["subject"])):
                    raise InvalidMetadata("missing or foreign Contract target")
            if envelope["kind"] == "evidence" and "supersedes" in envelope["payload"]:
                target = objects.get(envelope["payload"]["supersedes"])
                if not target or target["kind"] != "evidence" or target["task"] != envelope["task"]:
                    raise InvalidMetadata("missing or foreign Evidence target")
                seen: set[str] = set()
                current = envelope
                while "supersedes" in current["payload"]:
                    self.check()
                    prior = current["payload"]["supersedes"]
                    if prior in seen or len(seen) >= 64:
                        raise InvalidMetadata("Evidence supersession cycle/limit")
                    seen.add(prior)
                    target = objects.get(prior)
                    if not target or target["kind"] != "evidence" or target["task"] != envelope["task"]:
                        raise InvalidMetadata("missing or foreign Evidence target")
                    current = target
        return Snapshot(MappingProxyType(dict(files)), MappingProxyType(objects), MappingProxyType(pointers))

    def snapshot_links(self, snapshot: Snapshot, parents: tuple[str, ...]) -> None:
        """Bind snapshot links to exact already-validated metadata ancestors.

        This validates stored graph integrity, not current Task/GitHub facts or
        PR checkpoint version/locator integration (which remains Phase 2).
        """
        ancestors: set[str] = set()
        pending = list(parents)
        while pending:
            self.check()
            key = pending.pop()
            if key not in ancestors:
                ancestors.add(key)
                pending.extend(self.nodes[key].parents)

        def pinned(commit: str) -> Snapshot:
            if commit not in ancestors:
                raise InvalidMetadata("snapshot link must name exact metadata ancestor")
            return self.nodes[commit].snapshot

        def target(tree: Snapshot, content_id: str, kind: str, task: str, subject: str) -> dict[str, Any]:
            envelope = tree.objects.get(content_id)
            if (not envelope or (envelope["kind"], envelope["task"], envelope["subject"])
                    != (kind, task, subject)):
                raise InvalidMetadata("missing or foreign snapshot graph object")
            return envelope

        for envelope in snapshot.objects.values():
            self.check()
            if envelope["kind"] != "task-view-snapshot":
                continue
            view = envelope["payload"]["view"]
            task = envelope["task"]
            authority = view["authority"]
            tree = pinned(authority["metadata_commit"])
            record = target(tree, authority["record_id"], "task-record", task, authority["base_revision"])
            target(tree, authority["contract_id"], "contract", task, authority["base_revision"])
            if (tree.pointers.get(task) != authority["record_id"]
                    or record["payload"]["contract_id"] != authority["contract_id"]
                    or record["payload"]["branch_ref"] != authority["branch_ref"]
                    or record["payload"].get("disposition") != authority["disposition"]):
                raise InvalidMetadata("snapshot authority binding mismatch")
            for selected in view["evidence"]:
                target(tree, selected["evidence_id"], "evidence", task, selected["subject"])
            previous = view["previous_checkpoint"]
            if previous["state"] == "observed":
                target(pinned(previous["metadata_commit"]), previous["snapshot_id"],
                       "task-view-snapshot", task, previous["subject"])

    def history(self, tip: str) -> Node:
        """Validate every edge, with archive-only two-parent union classification."""
        pending = [(oid(tip), False)]
        raw_nodes: dict[str, tuple[tuple[str, ...], Snapshot]] = {}
        visiting: set[str] = set()
        while pending:
            self.check()
            key, finish = pending.pop()
            if key in self.nodes:
                continue
            if not finish:
                if key in visiting:
                    raise InvalidMetadata("cyclic Git history")
                if len(raw_nodes) + len(self.nodes) >= self.limits.commits:
                    raise InvalidMetadata("history commit limit exceeded")
                visiting.add(key)
                raw = self.read(key, "commit")
                header, separator, _ = raw.partition(b"\n\n")
                lines = header.split(b"\n")
                # Deliberately narrow canonical commit headers: no graft-like
                # interpretation, continuation headers, signatures or extra fields.
                if not separator or b"\0" in raw or not lines[0].startswith(b"tree "):
                    raise InvalidMetadata("malformed commit")
                try:
                    tree = oid(lines[0][5:].decode("ascii"))
                    parents: list[str] = []
                    index = 1
                    while index < len(lines) and lines[index].startswith(b"parent "):
                        parents.append(oid(lines[index][7:].decode("ascii")))
                        index += 1
                    if (len(parents) > 2 or len(set(parents)) != len(parents)
                            or len(lines[index:]) != 2 or not lines[index].startswith(b"author ")
                            or not lines[index + 1].startswith(b"committer ")):
                        raise InvalidMetadata("unqualified commit headers/parents")
                except (UnicodeError, IndexError) as error:
                    raise InvalidMetadata("malformed commit headers") from error
                snapshot = self.tree(tree)
                raw_nodes[key] = (tuple(parents), snapshot)
                pending.append((key, True))
                pending.extend((parent, False) for parent in reversed(parents))
                continue
            parents, snapshot = raw_nodes.pop(key)
            visiting.remove(key)
            if not parents:
                if key != self.genesis or snapshot.files:
                    raise InvalidMetadata("history must root at pinned empty independent genesis")
                node = Node(parents, snapshot, "genesis", None)
            elif len(parents) == 1:
                parent = self.nodes[parents[0]]
                if parent.role not in {"genesis", "issue"} or len(snapshot.tasks) != 1:
                    raise InvalidMetadata("active Issue must be linear from genesis")
                task = next(iter(snapshot.tasks))
                if parent.task not in {None, task}:
                    raise InvalidMetadata("cross-Issue writer")
                transition(parent.snapshot, snapshot)
                node = Node(parents, snapshot, "issue", task)
            else:
                archive, issue = (self.nodes[p] for p in parents)
                if archive.role not in {"genesis", "archive"} or issue.role != "issue":
                    raise InvalidMetadata("ordinary merge is not an archive edge")
                expected = union(archive.snapshot, issue.snapshot)
                if dict(snapshot.files) != expected:
                    raise InvalidMetadata("archive merge must be exact validated union")
                node = Node(parents, snapshot, "archive", None)
            self.snapshot_links(snapshot, parents)
            self.nodes[key] = node
        return self.nodes[tip]

    def archive(self, tip: str) -> Node:
        node = self.history(tip)
        if node.role not in {"genesis", "archive"}:
            raise InvalidMetadata("archive ref points to active Issue history")
        return node

    def issue(self, tip: str, task: str) -> Node:
        issue_ref(task)
        node = self.history(tip)
        if node.role != "issue" or node.task != task:
            raise InvalidMetadata("Issue ref points to foreign history")
        return node

    def observe(self, observer: Observer, ref: str, *, absent: bool = False) -> str | None:
        self.check()
        try:
            state = observer(ref, self.deadline)
        except Exception as error:
            raise InvalidMetadata("exact ref observation unavailable") from error
        self.check()
        if type(state) is not RefState or state.ref != ref or state.direct is not True:
            raise InvalidMetadata("exact direct-ref observation required")
        if state.commit is None:
            if absent:
                return None
            raise InvalidMetadata("required ref absent")
        return oid(state.commit)


def transition(old: Snapshot, new: Snapshot) -> None:
    for path, data in old.files.items():
        if path.startswith("objects/") and new.files.get(path) != data:
            raise InvalidMetadata("immutable object deleted or overwritten")
        if path.startswith("tasks/") and path not in new.files:
            raise InvalidMetadata("Record pointer deleted")


def union(archive: Snapshot, issue: Snapshot) -> dict[str, bytes]:
    if len(issue.tasks) != 1 or not issue.pointers:
        raise InvalidMetadata("archive requires one Issue with a Record")
    if archive.tasks & issue.tasks:
        raise InvalidMetadata("same-Issue reopen/archive existing pointer not supported")
    result = dict(archive.files)
    for path, data in issue.files.items():
        if path in result and result[path] != data:
            raise InvalidMetadata("historical immutable bytes conflict")
        result[path] = data
    return result


def construct(files: Mapping[str, bytes], parents: tuple[str, ...], *, nonce: str | None = None) -> Bundle:
    """Pure Git object construction; validation is mandatory before publication.

    Not a transport, merge engine or authority API. No index/HEAD/ref is touched.
    """
    if len(parents) > 2:
        raise InvalidMetadata("too many parents")
    for parent in parents:
        oid(parent)
    if (len(files) > 4096 or sum(len(v) for v in files.values()) > 32 * 1024 * 1024
            or any(type(p) is not str or not (_OBJECT.fullmatch(p) or _POINTER.fullmatch(p))
                   or type(v) is not bytes or len(v) > codec.MAX_OBJECT_BYTES for p, v in files.items())):
        raise InvalidMetadata("invalid construction files/limits")
    objects: dict[str, tuple[str, bytes]] = {}

    def store(kind: str, data: bytes) -> str:
        key = git_id(kind, data)
        objects[key] = (kind, data)
        return key

    root: dict[str, Any] = {}
    for path, data in files.items():
        directory = root
        parts = path.split("/")
        for part in parts[:-1]:
            directory = directory.setdefault(part, {})
        directory[parts[-1]] = store("blob", data)

    def tree(directory: dict[str, Any]) -> str:
        rows = []
        for name, item in directory.items():
            is_tree = type(item) is dict
            child = tree(item) if is_tree else item
            rows.append((name.encode() + (b"/" if is_tree else b""),
                         (b"40000" if is_tree else b"100644") + b" " + name.encode()
                         + b"\0" + bytes.fromhex(child)))
        return store("tree", b"".join(row for _, row in sorted(rows)))

    tree_oid = tree(root)
    token = secrets.token_hex(16) if nonce is None else nonce
    if type(token) is not str or not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", token):
        raise InvalidMetadata("invalid construction nonce")
    raw = (f"tree {tree_oid}\n" + "".join(f"parent {p}\n" for p in parents)
           + "author Metadata v2 <metadata@invalid> 946684800 +0000\n"
           + "committer Metadata v2 <metadata@invalid> 946684800 +0000\n\n"
           + f"Metadata v2 {token}\n").encode()
    return Bundle(store("commit", raw), MappingProxyType(objects))


class MetadataV2:
    """Fixed repository/genesis Host instance, with no ambient write capability."""

    def __init__(self, repository: str, genesis: str, reader: ObjectReader,
                 observer: Observer, *, issue_writer: Writer | None = None,
                 limits: Limits = Limits()) -> None:
        Validation(repository, genesis, reader, limits)
        if not callable(reader) or not callable(observer) or (issue_writer is not None and not callable(issue_writer)):
            raise InvalidMetadata("invalid Host callbacks")
        self.repository, self.genesis = repository, genesis
        self.reader, self.observer, self.issue_writer = reader, observer, issue_writer
        self.limits = limits

    def validation(self) -> Validation:
        return Validation(self.repository, self.genesis, self.reader, self.limits)

    def publish_issue(self, task: str, objects: Sequence[bytes], *,
                      record_update: tuple[str | None, str] | None = None) -> str:
        ref = issue_ref(task)
        if self.issue_writer is None:
            raise InvalidMetadata("trusted Issue writer required; no effects")
        if (not isinstance(objects, Sequence) or isinstance(objects, (bytes, str))
                or len(objects) > self.limits.entries):
            raise InvalidMetadata("bounded object sequence required")
        v = self.validation()
        archive_oid = v.observe(self.observer, ARCHIVE_REF)
        archive = v.archive(archive_oid)
        if task in archive.snapshot.tasks:
            raise InvalidMetadata("same-Issue reopen not implemented")
        prior = v.observe(self.observer, ref, absent=True)
        base = v.issue(prior, task) if prior else v.history(self.genesis)
        files = dict(base.snapshot.files)
        total = 0
        pending_containers = 0
        admitted_objects: dict[str, tuple[bytes, dict[str, Any]]] = {}
        known_content_ids = {key[1] for key in v.decoded_objects}
        for data in objects:
            v.check()
            if type(data) is not bytes or len(data) > codec.MAX_OBJECT_BYTES:
                raise InvalidMetadata("bounded canonical object required")
            total += len(data)
            if total > self.limits.bytes:
                raise InvalidMetadata("request byte limit exceeded")
            content_id = hashlib.sha256(b"agentcore-metadata-object/v1\n" + data).hexdigest()
            admitted = admitted_objects.get(content_id)
            if admitted is None:
                if content_id not in known_content_ids:
                    containers = json_container_markers(data)
                    if v.used_containers + pending_containers + containers > v.limits.containers:
                        raise InvalidMetadata("cumulative decoded JSON container budget exceeded")
                    pending_containers += containers
                # Admission quota precedes the sole decode for this content ID.
                envelope = codec.decode_object(data, expected_repository=self.repository, expected_task=task)
                admitted_objects[content_id] = (data, envelope)
            else:
                if admitted[0] != data:
                    raise InvalidMetadata("duplicate content ID bytes mismatch")
                envelope = admitted[1]
            path = codec.object_path(envelope["kind"], content_id)
            if path in files and files[path] != data:
                raise InvalidMetadata("immutable object conflict")
            files[path] = data
        if record_update is not None:
            if type(record_update) is not tuple or len(record_update) != 2:
                raise InvalidMetadata("expected-prior/proposed Record IDs required")
            expected, proposed = record_update
            if base.snapshot.pointers.get(task) != expected:
                raise InvalidMetadata("Record expected-prior conflict")
            codec.object_path("task-record", proposed)
            files[f"tasks/{task}/record"] = (proposed + "\n").encode("ascii")
        snapshot = v.snapshot(files)
        if snapshot.tasks != {task}:
            raise InvalidMetadata("Issue tree must contain only own objects")
        transition(base.snapshot, snapshot)
        bundle = construct(files, (prior or self.genesis,))
        request = Update(self.repository, self.genesis, task, ref, archive_oid, prior, bundle.commit)
        return self._publish(v, request, bundle, self.issue_writer)

    def _publish(self, v: Validation, request: Update, bundle: Bundle, writer: Writer) -> str:
        """No retry, no delete, no success on ambiguous Ack or changed refs."""
        original_reader = v.reader
        v.reader = lambda key, maximum, deadline: bundle.objects[key] if key in bundle.objects else original_reader(key, maximum, deadline)
        if request.ref == ARCHIVE_REF:
            v.archive(bundle.commit)
        else:
            v.issue(bundle.commit, request.task)
        v.check()
        try:
            ack = writer(request, bundle, v.deadline)
        except Exception as error:
            raise UncertainPublication("Host Ack unknown; preserve Issue ref") from error
        if ack is not True:
            raise UncertainPublication("Host rejected or Ack unknown; preserve Issue ref")
        try:
            current_archive = v.observe(self.observer, ARCHIVE_REF)
            current_issue = v.observe(self.observer, issue_ref(request.task), absent=True)
            if (current_archive != (bundle.commit if request.ref == ARCHIVE_REF else request.expected_archive)
                    or current_issue != (request.expected_issue if request.ref == ARCHIVE_REF else bundle.commit)):
                raise InvalidMetadata("ref absent/changed after publication")
            # Read back through Host, not candidate overlay/cache. This proves
            # exact objects are available, not that Host meets server policy.
            v.reader = original_reader
            # Candidate-overlay data are never proof of Host-backed readback.
            v.cache.clear()
            v.tree_snapshots.clear()
            v.decoded_objects.clear()
            v.nodes.clear()
            if request.ref == ARCHIVE_REF:
                v.archive(bundle.commit)
            else:
                v.issue(bundle.commit, request.task)
        except InvalidMetadata as error:
            raise UncertainPublication("exact readback failed; preserve Issue ref") from error
        return bundle.commit

    def read_object(self, commit: str, kind: str, content_id: str, *, task: str, subject: str) -> dict[str, Any]:
        """Read exact commit identity via active ref OR retained archive ancestry."""
        issue_ref(task)
        oid(commit)
        path = codec.object_path(kind, content_id)
        # Codec optional expectations treat None as unbound; this API does not.
        codec.encode_object(kind, self.repository, task, subject, {})
        v = self.validation()
        archive = v.observe(self.observer, ARCHIVE_REF)
        v.archive(archive)
        active = v.observe(self.observer, issue_ref(task), absent=True)
        if active:
            v.issue(active, task)
        reachable: set[str] = set()
        pending = [archive] + ([active] if active else [])
        while pending:
            v.check()
            key = pending.pop()
            if key not in reachable:
                reachable.add(key)
                pending.extend(v.nodes[key].parents)
        if commit not in reachable:
            raise InvalidMetadata("exact commit unreachable")
        node = v.nodes[commit]
        if path not in node.snapshot.files:
            raise InvalidMetadata("exact object absent at exact commit")
        result = codec.decode_object(node.snapshot.files[path], expected_id=content_id,
            expected_repository=self.repository, expected_task=task, expected_subject=subject, expected_kind=kind)
        # Do not accept a changed ref observation as checkpoint identity.
        if v.observe(self.observer, ARCHIVE_REF) != archive or v.observe(self.observer, issue_ref(task), absent=True) != active:
            raise InvalidMetadata("ref changed during exact read")
        return result
