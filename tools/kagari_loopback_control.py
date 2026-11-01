"""KAGARI #339: opt-in loopback-only Control Server transport, source-only R1.

No Task authority, persistence, OpenCode launch, permission reply, or mutation
lives here. Repository Main supplies current Task status through a callback.
Local hostile processes are explicitly outside the accepted threat model.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import socket
import threading
from typing import Callable

_TASK_STATUS = re.compile(r"/tasks/([1-9][0-9]{0,17})/status")
_TASK_DISPATCH = re.compile(r"/tasks/([1-9][0-9]{0,17})/dispatch")
_TASK_ASKS = re.compile(r"/tasks/([1-9][0-9]{0,17})/asks")
_ALLOWED_STATES = frozenset({"RUNNING", "BLOCKED", "COMPLETED", "UNKNOWN"})


class ControlTransportError(ValueError):
    """Invalid local transport configuration."""


class ControlActionError(ValueError):
    """Action refused or outcome uncertain by the existing trusted Main."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _LocalHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET
    # Non-daemon workers are joined by server_close(); a shutdown cannot
    # silently abandon an accepted Task dispatch or its ambiguous ACK.
    daemon_threads = False
    block_on_close = True

    def __init__(self, address: tuple[str, int], status_provider: Callable[[str], dict],
                 dispatch_provider: Callable[[str, str], dict] | None,
                 ask_provider: Callable[[str], dict] | None, max_inflight: int,
                 repository: str | None):
        self.repository = repository
        self.status_provider = status_provider
        self.dispatch_provider = dispatch_provider
        self.ask_provider = ask_provider
        self.max_inflight = max_inflight
        # Operational bound for accidentally abandoned/partial HTTP requests.
        self.request_timeout_seconds = 8.0
        self._slots = threading.BoundedSemaphore(max_inflight)
        super().__init__(address, _Handler)

    def process_request(self, request: socket.socket, client_address: tuple) -> None:
        """Bound admission *before* creating a request thread.

        When busy, the listener sends a minimal 503 and immediately closes
        the socket. No callback, model turn or new worker thread is scheduled.
        """
        if not self._slots.acquire(blocking=False):
            payload = b'{"error_code":"SERVER_BUSY"}'
            response = (
                b"HTTP/1.1 503 Service Unavailable\r\n"
                b"Content-Type: application/json\r\n"
                b"Cache-Control: no-store\r\n"
                b"Connection: close\r\n"
                + b"Content-Length: " + str(len(payload)).encode("ascii")
                + b"\r\n\r\n" + payload
            )
            try:
                request.settimeout(0.25)
                request.sendall(response)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request: socket.socket,
                               client_address: tuple) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class _Handler(BaseHTTPRequestHandler):
    server: _LocalHTTPServer

    def setup(self) -> None:
        self.request.settimeout(self.server.request_timeout_seconds)
        super().setup()

    def log_message(self, *args: object) -> None:
        # No process-local credentials, header contents or Task data in logs.
        return

    def _respond(self, code: int, payload: dict) -> None:
        content = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self._respond_bytes(code, content)

    def _respond_bytes(self, code: int, content: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        try:
            self.end_headers()
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            # A disconnected caller cannot revoke an already accepted Main
            # operation; Main's durable prompt receipt owns reconciliation.
            pass

    def _local_origin(self) -> bool:
        address = "127.0.0.1:" + str(self.server.server_port)
        origin = self.headers.get("Origin")
        return (self.headers.get("Host") == address
                and (origin is None or origin == "http://" + address))

    def do_GET(self) -> None:
        if not self._local_origin():
            self._respond(403, {"error_code": "NONLOCAL_HTTP_ORIGIN"})
            return
        if self.path == "/health":
            self._respond(200, {"service": "kagari-control", "status": "ok",
                                "operation_authority": "none"})
            return
        ask_match = _TASK_ASKS.fullmatch(self.path)
        if ask_match is not None and self.server.ask_provider is not None:
            task = ask_match.group(1)
            try:
                result = self.server.ask_provider(task)
            except ControlActionError as error:
                self._respond(409 if error.code in (
                    "TASK_NOT_APPROVED", "ASK_SESSION_NOT_STARTED") else 503,
                    {"error_code": error.code})
                return
            except Exception:
                self._respond(503, {"error_code": "ASK_OBSERVATION_UNAVAILABLE"})
                return
            if (type(result) is not dict or result.get("task") != task
                    or type(result.get("contract_id")) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", result["contract_id"]) is None
                    or type(result.get("root_session_id")) is not str
                    or re.fullmatch(r"ses_[A-Za-z0-9_-]{8,120}",
                                    result["root_session_id"]) is None
                    or type(result.get("pending")) is not list
                    or len(result["pending"]) > 32):
                self._respond(503, {"error_code": "ASK_OBSERVATION_UNVERIFIED"})
                return
            try:
                encoded = json.dumps(
                    result, sort_keys=True, ensure_ascii=False, allow_nan=False
                ).encode("utf-8")
            except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
                self._respond(503, {"error_code": "ASK_OBSERVATION_UNVERIFIED"})
                return
            if len(encoded) > 65536:
                self._respond(503, {"error_code": "ASK_OBSERVATION_TOO_LARGE"})
                return
            self._respond_bytes(200, encoded)
            return
        match = _TASK_STATUS.fullmatch(self.path)
        if match is None:
            self._respond(404, {"error_code": "UNKNOWN_ENDPOINT"})
            return
        task = match.group(1)
        try:
            result = self.server.status_provider(task)
        except Exception:
            self._respond(503, {"error_code": "TASK_OBSERVATION_UNAVAILABLE"})
            return
        if (type(result) is not dict or result.get("task") != task
                or type(result.get("state")) is not str
                or result["state"] not in _ALLOWED_STATES):
            self._respond(503, {"error_code": "UNVERIFIED_TASK_STATUS"})
            return
        # Do not return unverified fields or infer completion from OpenCode idle.
        self._respond(200, {"task": task, "state": result["state"]})

    def do_POST(self) -> None:
        match = _TASK_DISPATCH.fullmatch(self.path)
        if match is None or self.server.dispatch_provider is None:
            self._respond(405, {"error_code": "MUTATION_NOT_IMPLEMENTED"})
            return
        if not self._local_origin():
            self._respond(403, {"error_code": "NONLOCAL_HTTP_ORIGIN"})
            return
        if self.headers.get("Content-Type") != "application/json":
            self._respond(415, {"error_code": "JSON_REQUIRED"})
            return
        lengths = self.headers.get_all("Content-Length", [])
        if (len(lengths) != 1 or type(lengths[0]) is not str
                or re.fullmatch(r"[1-9][0-9]{0,3}", lengths[0]) is None):
            self._respond(400, {"error_code": "DISPATCH_REQUEST_INVALID"})
            return
        length = int(lengths[0])
        if length < 2 or length > 2048:
            self._respond(413, {"error_code": "DISPATCH_REQUEST_SIZE_INVALID"})
            return
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete body")
            def unique_pairs(pairs):
                obj = {}
                for key, value in pairs:
                    if key in obj:
                        raise ValueError("duplicate JSON key")
                    obj[key] = value
                return obj
            body = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
        except (ValueError, UnicodeError, RecursionError):
            self._respond(400, {"error_code": "DISPATCH_REQUEST_INVALID"})
            return
        if (type(body) is not dict or body.keys() != {
                "schema_version", "expected_contract_id"}
                or type(body["schema_version"]) is not int
                or body["schema_version"] != 1
                or type(body["expected_contract_id"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", body["expected_contract_id"]) is None):
            self._respond(400, {"error_code": "DISPATCH_REQUEST_INVALID"})
            return
        try:
            result = self.server.dispatch_provider(
                match.group(1), body["expected_contract_id"])
        except ControlActionError as error:
            # Only preflight refusals get a definite conflict response.
            # Any ambiguous or potentially partially executed Main operation
            # is reported as unavailable/unknown, never as a delivered ACK.
            definite = {
                "INVALID_TASK", "TASK_NOT_APPROVED", "TASK_CONTRACT_MISMATCH",
                "ISSUE_NOT_APPROVED", "MAIN_OWNER_REQUIRED",
                "MAIN_DISPATCH_BUSY", "MAIN_INITIAL_COLLABORATION_REQUIRED",
            }
            self._respond(409 if error.code in definite else 503,
                          {"error_code": error.code})
            return
        except Exception:
            # Never claim acknowledged delivery when a callback has unknown effects.
            self._respond(503, {"error_code": "DISPATCH_OUTCOME_UNKNOWN"})
            return
        if (type(result) is not dict or result.get("task") != match.group(1)
                or type(result.get("issue")) is not int
                or result["issue"] != int(match.group(1))
                or result.get("repository") != self.server.repository
                or result.get("contract_id") != body["expected_contract_id"]
                or result.get("status") not in ("accepted", "already_accepted")
                or result.get("prompt_state") not in (
                    "SUBMITTED", "RECOVERED_SUBMITTED", "ALREADY_DISPATCHED")):
            self._respond(503, {"error_code": "DISPATCH_RECEIPT_UNVERIFIED"})
            return
        self._respond(200 if result["status"] == "already_accepted" else 202,
                      {"task": result["task"], "issue": result["issue"],
                       "repository": result["repository"],
                       "contract_id": result["contract_id"],
                       "status": result["status"], "prompt_state": result["prompt_state"]})

    def _unsupported_mutation(self) -> None:
        self._respond(405, {"error_code": "MUTATION_NOT_IMPLEMENTED"})

    do_PUT = _unsupported_mutation
    do_PATCH = _unsupported_mutation
    do_DELETE = _unsupported_mutation


def create_control_server(
    status_provider: Callable[[str], dict], *,
    dispatch_provider: Callable[[str, str], dict] | None = None,
    ask_provider: Callable[[str], dict] | None = None,
    host: str = "127.0.0.1", port: int = 0,
    max_inflight: int = 16,
    repository: str | None = None,
) -> ThreadingHTTPServer:
    """Build an opt-in local HTTP server; caller owns start and clean shutdown.

    HTTP client authentication is intentionally absent per the accepted local
    threat model. Privileged actions will still require host-owned Task grants.
    """
    if host != "127.0.0.1":
        raise ControlTransportError("NON_LOOPBACK_BIND_FORBIDDEN")
    if type(port) is not int or not 0 <= port <= 65535:
        raise ControlTransportError("INVALID_PORT")
    if not callable(status_provider):
        raise ControlTransportError("STATUS_PROVIDER_REQUIRED")
    if dispatch_provider is not None and not callable(dispatch_provider):
        raise ControlTransportError("DISPATCH_PROVIDER_INVALID")
    if dispatch_provider is not None and (
            type(repository) is not str
            or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None):
        raise ControlTransportError("DISPATCH_REPOSITORY_REQUIRED")
    if ask_provider is not None and not callable(ask_provider):
        raise ControlTransportError("ASK_PROVIDER_INVALID")
    if type(max_inflight) is not int or not 1 <= max_inflight <= 64:
        raise ControlTransportError("INVALID_MAX_INFLIGHT")
    return _LocalHTTPServer((host, port), status_provider, dispatch_provider,
                            ask_provider, max_inflight, repository)
