"""KUMIKI #359 R2b D1: disposable, synthetic SQLite state machine.

NOT a daemon, not production-authoritative, not an OS trust boundary.
Connections come from the test harness (memory or disposable temporary DB).
No authenticated Native/Human grant or model dispatch exists in this module.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .kagari_model_consent_host_contract import (
    EPOCH, HEX_64, REVISION, HostContractError, SyntheticCandidate,
    SyntheticHostObservation, resolve_synthetic_candidate,
)
from .kagari_task_model_consent_codec import (
    CodecError, parse_canonical_json, canonical_json_bytes,
    validate_context, validate_native_asks,
)
from .kagari_task_model_binding import ModelSelection, ModelBindingError

PENDING = "PENDING_HUMAN_TEST_ONLY"
SIMULATED = "SIMULATED_TTY_ACK_DATA_ONLY"
DENIED = "DENIED"
REVOKED = "REVOKED"
EXPIRED = "EXPIRED"
UNCERTAIN = "RECOVERY_UNCERTAIN"
ALLOWED_TRANSITIONS = {
    PENDING: frozenset({SIMULATED, DENIED, REVOKED, EXPIRED}),
    SIMULATED: frozenset({REVOKED, EXPIRED}),
    DENIED: frozenset(),
    REVOKED: frozenset(),
    EXPIRED: frozenset(),
    UNCERTAIN: frozenset(),
}

def permitted_transition(old_state: str, target: str, *, tick: int, last_tick: int, expires_tick: int) -> bool:
    if type(old_state) is not str or type(target) is not str:
        return False
    if any(type(value) is not int or value < 0 for value in (tick, last_tick, expires_tick)):
        return False
    if tick < last_tick:
        return False
    if target == "EXPIRED":
        if tick < expires_tick:
            return False
    elif tick >= expires_tick:
        return False
    if old_state == "PENDING_HUMAN_TEST_ONLY":
        return target in ("SIMULATED_TTY_ACK_DATA_ONLY", "DENIED", "REVOKED", "EXPIRED")
    if old_state == "SIMULATED_TTY_ACK_DATA_ONLY":
        return target in ("REVOKED", "EXPIRED")
    return False


SQL_CREATE = """
CREATE TABLE epoch_metadata (
    fixed_id INTEGER PRIMARY KEY CHECK(fixed_id = 1),
    epoch TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1)
);
CREATE TABLE consent (
    job_id TEXT PRIMARY KEY,
    nonce TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL UNIQUE,
    packet_sha TEXT NOT NULL,
    state TEXT NOT NULL,
    revision INTEGER NOT NULL,
    expires_tick INTEGER NOT NULL,
    last_tick INTEGER NOT NULL,
    native_revision TEXT NOT NULL,
    epoch TEXT NOT NULL,
    packet BLOB NOT NULL
);
"""


class HostStoreError(ValueError):
    """Non-authoritative test store rejection with a stable reason code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("KAGARI R2b synthetic store rejected")


def _tick(value: int) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1



EXPECTED_PACKET_FIELDS = frozenset({
    "schema_version", "kind", "host_job_nonce", "context", "TaskSpec_sha256",
    "TaskSpec_allowed_paths", "TaskSpec_tests", "model_binding_raw_sha256",
    "model_binding_canonical_sha256", "model_selection", "native_asks",
    "scope", "model_dispatched", "source_product_mutations",
    "provider_identity_attested", "native_grant_provenance_attested",
    "production_activation", "request_id",
})
ROW_FIELDS = ("job_id","nonce","request_id","packet_sha","state",
              "revision","expires_tick","last_tick","native_revision",
              "epoch","packet")


def _checked_packet(raw: bytes, *, job_id: str, nonce: str,
                    request_id: str, full_sha: str) -> dict:
    """Check stored R2a schema and canonical internal identity; not provenance."""
    try:
        packet = parse_canonical_json(raw, 65536)
        if type(packet) is not dict:
            raise HostStoreError("INVALID_PACKET")
        base = {k: v for k, v in packet.items() if k != "request_id"}
        if (
            set(packet) != EXPECTED_PACKET_FIELDS
            or type(packet["schema_version"]) is not int
            or packet["schema_version"] != 1
            or packet["kind"] != "KAGARI_TASK_MODEL_CONSENT_PACKET_V1"
            or packet["request_id"] != request_id
            or hashlib.sha256(canonical_json_bytes(base)).hexdigest()[:24] != request_id
            or hashlib.sha256(raw).hexdigest() != full_sha
            or type(packet["context"]) is not dict
            or packet["context"].get("job_id") != job_id
            or packet["host_job_nonce"] != nonce
            or packet["scope"] != "DATA_ONLY_OWNED_CLONE_NO_PROJECT_WRITE_NO_PUSH"
            or type(packet["model_selection"]) is not dict
            or set(packet["model_selection"]) != {
                "provider_id", "model_id", "variant", "roles"
            }
            or packet["model_selection"]["roles"] != [
                "planner", "primary", "leaf-a", "leaf-b"
            ]
            or type(packet["native_asks"]) is not list
            or len(packet["native_asks"]) != 2
            or any(packet[key] is not False for key in (
                "model_dispatched", "source_product_mutations",
                "provider_identity_attested", "native_grant_provenance_attested",
                "production_activation",
            ))
        ):
            raise HostStoreError("INVALID_PACKET")
        # Shared R2a validators enforce the nested context/Ask data schema.
        # This remains structural validation, not current Host/Native authority.
        ctx = validate_context(packet["context"])
        validate_native_asks(tuple(packet["native_asks"]), ctx["workunit_clone"])
        model = packet["model_selection"]
        if (
            type(model["roles"]) is not list
            or model["roles"] != ["planner", "primary", "leaf-a", "leaf-b"]
        ):
            raise HostStoreError("INVALID_PACKET")
        ModelSelection(
            model["provider_id"], model["model_id"], model["variant"]
        )
        for field in (
            "TaskSpec_sha256", "model_binding_raw_sha256",
            "model_binding_canonical_sha256",
        ):
            if type(packet[field]) is not str or HEX_64.fullmatch(packet[field]) is None:
                raise HostStoreError("INVALID_PACKET")
        paths = packet["TaskSpec_allowed_paths"]
        if (
            type(paths) is not list or not paths
            or any(type(p) is not str for p in paths)
            or len(set(paths)) != len(paths)
        ):
            raise HostStoreError("INVALID_PACKET")
        tests = packet["TaskSpec_tests"]
        if type(tests) is not list:
            raise HostStoreError("INVALID_PACKET")
        for test in tests:
            if (
                type(test) is not dict
                or set(test) != {"argv", "cwd"}
                or test["cwd"] != "."
                or type(test["argv"]) is not list
                or not test["argv"]
                or any(type(arg) is not str for arg in test["argv"])
            ):
                raise HostStoreError("INVALID_PACKET")
        return packet
    except (KeyError, TypeError, ValueError, RecursionError, AttributeError) as exc:
        raise HostStoreError("INVALID_PACKET") from exc


def _valid_persisted_state_revision(state: object, revision: object) -> bool:
    if isinstance(revision, bool) or not isinstance(revision, int):
        return False
    if not isinstance(state, str):
        return False
    if state == PENDING:
        return revision == 0
    if state in (SIMULATED, DENIED):
        return revision == 1
    if state in (REVOKED, EXPIRED):
        return revision in (1, 2)
    return False

def _checked_stored_record(row: tuple, epoch: str) -> None:
    """Fail closed if a persisted synthetic row is corrupt or impossible."""
    if type(row) is not tuple or len(row) != len(ROW_FIELDS):
        raise HostStoreError("RECOVERY_UNCERTAIN")
    job, nonce, request_id, digest, status, revision, expiry, tick, native, saved_epoch, raw = row
    if (
        type(job) is not str or re.fullmatch(r"job-[0-9a-f]{24}", job) is None
        or type(nonce) is not str or re.fullmatch(r"[0-9a-f]{32}", nonce) is None
        or type(request_id) is not str or re.fullmatch(r"[0-9a-f]{24}", request_id) is None
        or type(digest) is not str or HEX_64.fullmatch(digest) is None
        or type(status) is not str or status not in {
            PENDING, SIMULATED, DENIED, REVOKED, EXPIRED
        }
        or not _valid_persisted_state_revision(status, revision)
        or not _tick(expiry) or expiry <= 0
        or not _tick(tick)
        or (status == EXPIRED and tick < expiry)
        or (status != EXPIRED and tick >= expiry)
        or type(native) is not str or REVISION.fullmatch(native) is None
        or saved_epoch != epoch
        or type(raw) is not bytes
    ):
        raise HostStoreError("RECOVERY_UNCERTAIN")
    try:
        _checked_packet(raw, job_id=job, nonce=nonce,
                        request_id=request_id, full_sha=digest)
    except HostStoreError as exc:
        raise HostStoreError("RECOVERY_UNCERTAIN") from exc


def _checked_schema(db: sqlite3.Connection) -> None:
    """Require exact pinned D1 DDL and all three unique identity constraints."""
    if db.execute("SELECT 1 FROM temp.sqlite_master LIMIT 1").fetchone() is not None:
        raise HostStoreError("RECOVERY_UNCERTAIN")
    expected_ddl = {
        "epoch_metadata": SQL_CREATE.strip().split(";")[0].strip(),
        "consent": SQL_CREATE.strip().split(";")[1].strip(),
    }
    objects = db.execute(
        "SELECT type,name,sql FROM main.sqlite_master ORDER BY name"
    ).fetchall()
    actual_tables = {name: sql.strip() for kind, name, sql in objects
                     if kind == "table" and type(sql) is str}
    if actual_tables != expected_ddl:
        raise HostStoreError("RECOVERY_UNCERTAIN")
    for kind, name, sql in objects:
        if kind == "table" and name in expected_ddl:
            continue
        if kind != "index" or not name.startswith("sqlite_autoindex_consent_") or sql is not None:
            raise HostStoreError("RECOVERY_UNCERTAIN")
    unique_columns = set()
    for item in db.execute("PRAGMA main.index_list('consent')").fetchall():
        index_name, is_unique = item[1], item[2]
        if not is_unique or not index_name.startswith("sqlite_autoindex_consent_"):
            raise HostStoreError("RECOVERY_UNCERTAIN")
        cols = tuple(info[2] for info in db.execute(
            "PRAGMA main.index_info('" + index_name + "')"
        ).fetchall())
        unique_columns.add(cols)
    if unique_columns != {("job_id",), ("nonce",), ("request_id",)}:
        raise HostStoreError("RECOVERY_UNCERTAIN")


def _data_only_status(row: tuple, epoch: str) -> dict:
    _checked_stored_record(row, epoch)
    return dict(
        job_id=row[0],
        request_id=row[2],
        packet_sha256=row[3],
        state=row[4],
        revision=row[5],
        status="DATA_ONLY_NOT_AN_AUTHORIZATION",
        human_authenticated=False,
        model_dispatched=False,
    )

class SyntheticConsentLedger:
    """Transaction semantics only; application MUST NOT connect a real Host DB."""

    def __init__(self, connection: sqlite3.Connection, host_epoch: str) -> None:
        if type(connection) is not sqlite3.Connection or (
            type(host_epoch) is not str or EPOCH.fullmatch(host_epoch) is None
        ):
            raise HostStoreError("INVALID_FIXTURE")
        if connection.in_transaction:
            raise HostStoreError("INVALID_TRANSACTION_STATE")
        self.db = connection
        self.epoch = host_epoch
        self.db.isolation_level = None
        self.db.execute("PRAGMA busy_timeout = 2500")
        try:
            self._initialize()
        except sqlite3.DatabaseError as exc:
            raise HostStoreError("RECOVERY_UNCERTAIN") from exc

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        if self.db.in_transaction:
            raise HostStoreError('INVALID_TRANSACTION_STATE')
        started = False
        try:
            self.db.execute('BEGIN IMMEDIATE')
            started = True
            yield self.db
            self.db.execute('COMMIT')
        except BaseException:
            if started and self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def _initialize(self) -> None:
        with self._tx() as db:
            existing = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM main.sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if not existing:
                for statement in SQL_CREATE.strip().split(";"):
                    if statement.strip():
                        db.execute(statement)
                db.execute(
                    "INSERT INTO main.epoch_metadata(fixed_id,epoch,schema_version) VALUES(1,?,1)",
                    (self.epoch,),
                )
            elif existing != {"epoch_metadata", "consent"}:
                raise HostStoreError("RECOVERY_UNCERTAIN")
            _checked_schema(db)
            row = db.execute(
                "SELECT epoch,schema_version FROM main.epoch_metadata WHERE fixed_id=1"
            ).fetchone()
            if row != (self.epoch, 1):
                raise HostStoreError("RECOVERY_UNCERTAIN")
            count = db.execute("SELECT COUNT(*) FROM main.epoch_metadata").fetchone()[0]
            if count != 1:
                raise HostStoreError("RECOVERY_UNCERTAIN")
            for stored in db.execute(
                "SELECT " + ",".join(ROW_FIELDS) + " FROM main.consent"
            ):
                _checked_stored_record(stored, self.epoch)

    def register(
        self, *, ipc_bytes: bytes, observation: SyntheticHostObservation,
        tick: int,
    ) -> dict:
        """Store a DATA-ONLY packet. No caller may use this as credential proof."""
        if not _tick(tick):
            raise HostStoreError("INVALID_CANDIDATE")
        try:
            candidate = resolve_synthetic_candidate(ipc_bytes, observation)
        except (HostContractError, TypeError, ValueError) as exc:
            raise HostStoreError("CANDIDATE_IDENTITY_DRIFT") from exc
        if candidate.authority_epoch != self.epoch:
            raise HostStoreError("EPOCH_MISMATCH")
        if (
            candidate.state != "DATA_ONLY_NOT_AN_AUTHORIZATION"
            or type(candidate.expires_tick) is not int
            or type(candidate.job_id) is not str
            or re.fullmatch(r"job-[0-9a-f]{24}", candidate.job_id) is None
            or type(candidate.request_id) is not str
            or re.fullmatch(r"[0-9a-f]{24}", candidate.request_id) is None
            or type(candidate.host_job_nonce) is not str
            or re.fullmatch(r"[0-9a-f]{32}", candidate.host_job_nonce) is None
            or type(candidate.native_revision) is not str
            or not candidate.native_revision
        ):
            raise HostStoreError("INVALID_CANDIDATE")
        if not (tick < candidate.expires_tick <= 2**63 - 1):
            raise HostStoreError("INVALID_EXPIRY")
        if (
            type(candidate.packet_bytes) is not bytes
            or not 0 < len(candidate.packet_bytes) <= 65536
            or hashlib.sha256(candidate.packet_bytes).hexdigest()
               != candidate.packet_sha256
        ):
            raise HostStoreError("INVALID_PACKET")
        _checked_packet(
            candidate.packet_bytes,
            job_id=candidate.job_id,
            nonce=candidate.host_job_nonce,
            request_id=candidate.request_id,
            full_sha=candidate.packet_sha256,
        )
        try:
            with self._tx() as db:
                db.execute(
                    """INSERT INTO main.consent(
                    job_id,nonce,request_id,packet_sha,state,revision,
                    expires_tick,last_tick,native_revision,epoch,packet)
                    VALUES(?,?,?,?,?,0,?,?,?,?,?)""",
                    (candidate.job_id, candidate.host_job_nonce,
                     candidate.request_id, candidate.packet_sha256,
                     PENDING, candidate.expires_tick, tick,
                     candidate.native_revision, self.epoch,
                     candidate.packet_bytes),
                )
                stored = db.execute(
                    "SELECT " + ",".join(ROW_FIELDS) + " FROM main.consent WHERE job_id=?",
                    (candidate.job_id,),
                ).fetchone()
                if stored is None:
                    raise HostStoreError("STORAGE_UNCERTAIN")
                receipt = _data_only_status(stored, self.epoch)
        except sqlite3.IntegrityError as exc:
            raise HostStoreError("DUPLICATE_IDENTITY") from exc
        except sqlite3.DatabaseError as exc:
            raise HostStoreError("STORAGE_UNCERTAIN") from exc
        return receipt

    def read(self, job_id: str) -> dict:
        """Return non-authorizing status; no packet or Host secret is exported."""
        if type(job_id) is not str or re.fullmatch(r"job-[0-9a-f]{24}", job_id) is None:
            raise HostStoreError("INVALID_JOB")
        try:
            row = self.db.execute(
                "SELECT " + ",".join(ROW_FIELDS) + " FROM main.consent WHERE job_id=?",
                (job_id,),
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise HostStoreError("STORAGE_UNCERTAIN") from exc
        if row is None:
            raise HostStoreError("UNKNOWN_JOB")
        return _data_only_status(row, self.epoch)


    def transition(
        self, *, job_id: str, expected_revision: int,
        target: str, packet_sha: str, native_revision: str, tick: int,
    ) -> dict:
        """Linearizable synthetic transition, never a credentialed grant."""
        if (
            type(job_id) is not str
            or re.fullmatch(r"job-[0-9a-f]{24}", job_id) is None
            or type(expected_revision) is not int or expected_revision < 0
            or type(packet_sha) is not str or HEX_64.fullmatch(packet_sha) is None
            or type(native_revision) is not str or not native_revision
            or not _tick(tick)
            or type(target) is not str
            or target not in {SIMULATED, DENIED, REVOKED, EXPIRED}
        ):
            raise HostStoreError("INVALID_TRANSITION")
        try:
            with self._tx() as db:
                row = db.execute(
                    "SELECT " + ",".join(ROW_FIELDS) + " FROM main.consent WHERE job_id=?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise HostStoreError("UNKNOWN_JOB")
                _checked_stored_record(row, self.epoch)
                old, revision, expires_tick, last_tick = row[4],row[5],row[6],row[7]
                stored_sha, stored_native, epoch = row[3],row[8],row[9]
                if epoch != self.epoch:
                    raise HostStoreError("EPOCH_MISMATCH")
                if revision != expected_revision:
                    raise HostStoreError("STALE_REVISION")
                if stored_sha != packet_sha:
                    raise HostStoreError("PACKET_MISMATCH")
                if stored_native != native_revision:
                    raise HostStoreError("NATIVE_REVISION_MISMATCH")
                if not permitted_transition(
                    old, target, tick=tick,
                    last_tick=last_tick, expires_tick=expires_tick,
                ):
                    raise HostStoreError("TRANSITION_REJECTED")
                changed = db.execute(
                    "UPDATE main.consent SET state=?,revision=revision+1,last_tick=? "
                    "WHERE job_id=? AND state=? AND revision=? "
                    "AND packet_sha=? AND native_revision=? AND epoch=?",
                    (target, tick, job_id, old, revision,
                     stored_sha, stored_native, self.epoch),
                )
                if changed.rowcount != 1:
                    raise HostStoreError("CONCURRENT_CHANGE")
                stored = db.execute(
                    "SELECT " + ",".join(ROW_FIELDS) + " FROM main.consent WHERE job_id=?",
                    (job_id,),
                ).fetchone()
                if stored is None:
                    raise HostStoreError("STORAGE_UNCERTAIN")
                receipt = _data_only_status(stored, self.epoch)
        except sqlite3.DatabaseError as exc:
            raise HostStoreError("STORAGE_UNCERTAIN") from exc
        return receipt

    def authorize_execution(self, *_args: object, **_kwargs: object) -> None:
        """D1 must never expose a usable Human grant or R3 execution."""
        raise HostStoreError("EXECUTION_NOT_IMPLEMENTED")
