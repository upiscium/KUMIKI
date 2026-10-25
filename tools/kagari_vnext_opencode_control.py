#!/usr/bin/env python3
"""CLI-first, KAGARI-owned view of one existing OpenCode Task session.

The KAGARI Main runtime will emit the binding file. This module consumes that
binding and OpenCode live APIs; it does not create Task authority, sessions,
or another pending-Ask database. The MUSUBI backend may call this same CLI.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


SESSION = re.compile(r"ses_[A-Za-z0-9_-]{8,120}\Z")
REQUEST = re.compile(r"(per|que)_[A-Za-z0-9_-]{8,120}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class ControlError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ControlError("UPSTREAM_REDIRECT", "OpenCode API redirected unexpectedly")


@dataclass(frozen=True)
class TaskBinding:
    repository: str
    issue: int
    worktree: str
    session_id: str
    server_url: str
    contract_sha256: str
    contract_revision: str

    @classmethod
    def from_mapping(cls, obj: object) -> "TaskBinding":
        """Validate a KAGARI-resolved live Task runtime mapping without a file."""
        if not isinstance(obj, dict) or obj.get("schema_version") != 1:
            raise ControlError("INVALID_BINDING", "Unsupported Task binding schema")
        repository = obj.get("repository")
        issue = obj.get("issue")
        worktree = obj.get("worktree")
        session_id = obj.get("session_id")
        server_url = obj.get("server_url")
        contract_sha256 = obj.get("contract_sha256")
        contract_revision = obj.get("contract_revision")
        if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
            raise ControlError("INVALID_BINDING", "Invalid repository identity")
        if type(issue) is not int or issue < 1:
            raise ControlError("INVALID_BINDING", "Invalid Issue number")
        if not isinstance(worktree, str) or not Path(worktree).is_absolute():
            raise ControlError("INVALID_BINDING", "Task Worktree must be absolute")
        if not isinstance(session_id, str) or not SESSION.fullmatch(session_id):
            raise ControlError("INVALID_BINDING", "Invalid OpenCode Session ID")
        if not isinstance(contract_sha256, str) or not HEX64.fullmatch(contract_sha256):
            raise ControlError("INVALID_BINDING", "Missing immutable specification digest")
        if not isinstance(contract_revision, str) or not contract_revision.strip():
            raise ControlError("INVALID_BINDING", "Missing specification revision")
        if not isinstance(server_url, str):
            raise ControlError("INVALID_BINDING", "Missing OpenCode endpoint")
        try:
            url = urlsplit(server_url)
            port = url.port
        except ValueError:
            raise ControlError("INVALID_BINDING", "Malformed OpenCode endpoint") from None
        if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost")
                or port is None or port < 1 or url.username or url.password
                or url.path or url.query or url.fragment):
            raise ControlError("INVALID_BINDING", "Only a plain loopback OpenCode endpoint is supported")
        return cls(repository, issue, worktree, session_id,
                   server_url.rstrip("/"), contract_sha256, contract_revision)

    def subject(self) -> dict[str, Any]:
        return {"repository": self.repository, "issue": self.issue,
                "session_id": self.session_id, "worktree": self.worktree,
                "contract_revision": self.contract_revision,
                "contract_sha256": self.contract_sha256}


class OpenCode:
    def __init__(self, binding: TaskBinding) -> None:
        self.binding = binding
        self.client = build_opener(NoRedirect())

    def request(self, path: str, method: str = "GET", payload: object = None) -> object:
        url = self.binding.server_url + path + "?" + urlencode(
            {"directory": self.binding.worktree})
        data = None if payload is None else json.dumps(
            payload, separators=(",", ":"), ensure_ascii=False).encode()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        password = os.environ.get("OPENCODE_SERVER_PASSWORD")
        if password:
            username = os.environ.get("OPENCODE_SERVER_USERNAME") or "opencode"
            token = base64.b64encode((username + ":" + password).encode()).decode()
            headers["Authorization"] = "Basic " + token
        request = Request(url, method=method, data=data, headers=headers)
        try:
            with self.client.open(request, timeout=8) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ControlError("UPSTREAM_INVALID", "OpenCode response too large")
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            raise ControlError("UPSTREAM_HTTP", "OpenCode API returned HTTP " + str(exc.code)) from None
        except (URLError, OSError, TimeoutError):
            raise ControlError("UPSTREAM_UNAVAILABLE", "OpenCode server unavailable") from None
        except (ValueError, UnicodeError):
            raise ControlError("UPSTREAM_INVALID", "Invalid OpenCode JSON response") from None

    def verify_session(self) -> None:
        session = self.request("/session/" + self.binding.session_id)
        if not isinstance(session, dict) or (session.get("id") != self.binding.session_id
                                             or session.get("directory") != self.binding.worktree
                                             or session.get("parentID") is not None):
            raise ControlError("SESSION_MISMATCH",
                               "OpenCode root Session ID, parent or directory differs from Task binding")

    def pending(self) -> list[dict[str, Any]]:
        self.verify_session()
        all_asks: list[dict[str, Any]] = []
        for kind, path in (("permission", "/permission"), ("question", "/question")):
            items = self.request(path)
            if not isinstance(items, list):
                raise ControlError("UPSTREAM_INVALID", "Pending Ask response is not a list")
            for entry in items:
                if not isinstance(entry, dict):
                    raise ControlError("UPSTREAM_INVALID", "Malformed pending Ask")
                if entry.get("sessionID") != self.binding.session_id:
                    continue
                rid = entry.get("id")
                if not isinstance(rid, str) or not REQUEST.fullmatch(rid):
                    raise ControlError("UPSTREAM_INVALID", "Malformed Task Ask ID")
                if (kind == "permission" and not rid.startswith("per_")) or (
                        kind == "question" and not rid.startswith("que_")):
                    raise ControlError("UPSTREAM_INVALID", "Pending Ask kind/ID mismatch")
                all_asks.append({"kind": kind, "request_id": rid,
                                 "task": self.binding.subject(), "upstream": entry})
        return all_asks


def status(binding: TaskBinding, client: OpenCode) -> dict[str, Any]:
    client.verify_session()
    runtime = client.request("/session/status")
    if not isinstance(runtime, dict):
        raise ControlError("UPSTREAM_INVALID", "Session status response is not a map")
    # OpenCode 1.18.35 lists active sessions, omitting verified idle ones.
    # This is runtime idleness only, never Task/Issue completion.
    observed = runtime.get(binding.session_id)
    state = ("idle" if binding.session_id not in runtime else
             observed.get("type", "UNKNOWN") if isinstance(observed, dict) else "UNKNOWN")
    if state not in ("busy", "retry", "idle"):
        state = "UNKNOWN"
    asks = client.pending()
    return {"status": "ok", "operation": "status", "task": binding.subject(),
            "session_state": state, "pending_asks": len(asks)}


def reply(binding: TaskBinding, client: OpenCode, args: argparse.Namespace) -> dict[str, Any]:
    pending = [x for x in client.pending() if x["request_id"] == args.request_id]
    if len(pending) != 1 or pending[0]["kind"] != args.kind:
        raise ControlError("ASK_NOT_PENDING", "Exact pending Ask was not found for this Task")
    if args.kind == "permission":
        if args.decision not in ("once", "reject") or args.answers_json is not None:
            raise ControlError("INVALID_REPLY", "Permission requires once/reject, without answers")
        payload = {"reply": args.decision}
        url = "/permission/" + args.request_id + "/reply"
    else:
        if args.decision is not None or args.answers_json is None:
            raise ControlError("INVALID_REPLY", "Question requires --answers-json")
        try:
            answers = json.loads(args.answers_json)
        except ValueError:
            raise ControlError("INVALID_REPLY", "Invalid question answers JSON") from None
        questions = pending[0]["upstream"].get("questions")
        if (not isinstance(answers, list) or not isinstance(questions, list)
                or len(answers) != len(questions)
                or any(not isinstance(v, list) or not v
                       or any(not isinstance(s, str) for s in v) for v in answers)):
            raise ControlError("INVALID_REPLY", "Question answers must match question count")
        for answer, question in zip(answers, questions):
            if not isinstance(question, dict):
                raise ControlError("UPSTREAM_INVALID", "Malformed upstream Question")
            if question.get("multiple") is not True and len(answer) != 1:
                raise ControlError("INVALID_REPLY", "Single-choice Question requires one answer")
            # OpenCode supports custom/free-text answers; only reject labels
            # outside the offered choices when the question explicitly disables custom.
            if question.get("custom") is False:
                options = question.get("options")
                if (not isinstance(options, list)
                        or any(not isinstance(o, dict) or not isinstance(o.get("label"), str)
                               for o in options)):
                    raise ControlError("UPSTREAM_INVALID", "Malformed upstream Question options")
                labels = {o["label"] for o in options}
                if any(value not in labels for value in answer):
                    raise ControlError("INVALID_REPLY", "Question choice is not offered")
        payload = {"answers": answers}
        url = "/question/" + args.request_id + "/reply"
    accepted = client.request(url, method="POST", payload=payload)
    if accepted is not True:
        raise ControlError("UPSTREAM_REJECTED", "OpenCode did not accept the reply")
    remaining = client.pending()
    if any(x["request_id"] == args.request_id for x in remaining):
        raise ControlError("REPLY_UNCONFIRMED", "Ask still pending after upstream reply")
    return {"status": "ok", "operation": "reply", "task": binding.subject(),
            "request_id": args.request_id, "kind": args.kind, "applied": True}
