"""Bounded KAGARI/OpenCode shared-Ask CLI tests; fake upstream, no real LLM."""
from __future__ import annotations

import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from tools.kagari_vnext_opencode_control import (
    TaskBinding, ControlError, OpenCode, status, reply)
import tools.kagari_vnext_opencode_control as transport


SESSION = "ses_KAGARI292test12345"
PERMISSION = "per_KAGARI292test12345"
QUESTION = "que_KAGARI292test12345"


class Server(ThreadingHTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def emit(self, code: int, obj: object):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def route(self) -> tuple[str, str]:
        url = urlsplit(self.path)
        directory = parse_qs(url.query).get("directory", [""])[0]
        if directory != self.server.directory:
            self.emit(400, {"error": "wrong directory"})
            return "", ""
        return url.path, directory

    def do_GET(self):
        self.server.last_authorization = self.headers.get("Authorization")
        path, _ = self.route()
        if path == "/session/" + SESSION:
            response = {"id": SESSION, "directory": self.server.session_directory}
            if self.server.session_parent_id is not None:
                response["parentID"] = self.server.session_parent_id
            self.emit(200, response)
        elif path == "/session/status":
            self.emit(200, {} if getattr(self.server, "sparse_status", False) else
                      {SESSION: {"type": self.server.session_status}})
        elif path == "/permission":
            if getattr(self.server, "permission_http_error", False):
                self.emit(400, {"error": 'Expected JSON value, got undefined at [0]["metadata"]["timeout"]'})
            else:
                self.emit(200, list(self.server.permissions))
        elif path == "/question":
            self.emit(200, list(self.server.questions))
        else:
            self.emit(404, {"error": "missing"})

    def do_POST(self):
        path, _ = self.route()
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            payload = json.loads(raw)
        except ValueError:
            self.emit(400, {"error": "invalid"})
            return
        if path == "/permission/" + PERMISSION + "/reply":
            matches = [p for p in self.server.permissions if p["id"] == PERMISSION]
            if len(matches) != 1 or payload.get("reply") not in ("once", "reject"):
                self.emit(409, False)
                return
            self.server.permissions.clear()
            self.server.replies.append((path, payload))
            self.emit(200, True)
        elif path == "/question/" + QUESTION + "/reply":
            matches = [p for p in self.server.questions if p["id"] == QUESTION]
            expected = getattr(self.server, "accepted_question_answers", [["Yes"]])
            if len(matches) != 1 or payload.get("answers") != expected:
                self.emit(409, False)
                return
            self.server.questions.clear()
            self.server.replies.append((path, payload))
            self.emit(200, True)
        else:
            self.emit(404, {"error": "missing"})


class KAGARIControlCLITests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="kagari-292-cli-tests-")
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.worktree = self.root / "issue-worktree"
        self.worktree.mkdir()
        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.directory = str(self.worktree)
        self.server.session_directory = str(self.worktree)
        self.server.session_parent_id = None
        self.server.session_status = "busy"
        self.server.sparse_status = False
        self.server.permissions = [
            {"id": PERMISSION, "sessionID": SESSION, "permission": "bash",
             "patterns": ["pwd"], "always": ["pwd *"],
             "metadata": {"command": "pwd"}}]
        self.server.questions = [
            {"id": QUESTION, "sessionID": SESSION,
             "questions": [{"question": "Proceed?",
                            "options": [{"label": "Yes"}, {"label": "No"}],
                            "multiple": False}]}]
        self.server.replies = []
        self.server.last_authorization = None
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.stop_server)
        self.binding = self.root / "task-binding.json"
        self.record = {
            "schema_version": 1,
            "repository": "upiscium/KUMIKI",
            "issue": 292,
            "worktree": str(self.worktree),
            "session_id": SESSION,
            "server_url": "http://127.0.0.1:" + str(self.server.server_port),
            "contract_sha256": "f" * 64,
            "contract_revision": "r1",
        }
        self.store()

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)

    def store(self):
        self.binding.write_text(json.dumps(self.record) + "\n")

    def run_cli(self, *args):
        """Exercise internal transport functions, not a second executable Ask CLI."""
        operation = args[0]
        flags = dict(zip(args[1::2], args[2::2]))
        namespace = argparse.Namespace(
            request_id=flags.get("--request-id"),
            kind=flags.get("--kind"),
            decision=flags.get("--decision"),
            answers_json=flags.get("--answers-json"))
        try:
            binding = TaskBinding.from_mapping(json.loads(self.binding.read_text()))
            client = OpenCode(binding)
            if operation == "status":
                result = status(binding, client)
            elif operation == "asks":
                result = {"status": "ok", "pending": client.pending()}
            elif operation == "ask-show":
                selected = [ask for ask in client.pending()
                            if ask["request_id"] == namespace.request_id]
                if len(selected) != 1:
                    raise ControlError("ASK_NOT_PENDING", "Exact Ask not pending")
                result = {"status": "ok", "ask": selected[0]}
            elif operation == "ask-reply":
                result = reply(binding, client, namespace)
            else:
                raise AssertionError("Unknown internal test operation")
            return 0, result
        except ControlError as exc:
            return 2, {"status": "error", "error_code": exc.code}

    def test_reconnect_after_pending_ask_uses_current_upstream_not_events(self):
        # A newly constructed observer sees an Ask emitted before attachment.
        binding = TaskBinding.from_mapping(self.record)
        observer = OpenCode(binding)
        asks = observer.pending()
        self.assertEqual({a["request_id"] for a in asks},
                         {PERMISSION, QUESTION})
        result = status(binding, observer)
        self.assertEqual(result["pending_asks"], 2)
        self.assertEqual(result["session_state"], "busy")
        self.assertEqual(result["task_attention"], "NEEDS_APPROVAL")

    def test_api_permission_http_400_is_not_silent_empty_ask(self):
        self.server.permission_http_error = True
        binding = TaskBinding.from_mapping(self.record)
        observer = OpenCode(binding)
        with self.assertRaises(ControlError) as caught:
            observer.pending()
        self.assertEqual(caught.exception.code, "UPSTREAM_HTTP")
        with self.assertRaises(ControlError):
            status(binding, observer)
        self.assertEqual(self.server.replies, [])

    def test_real_empty_pending_ask_is_distinct_from_api_error(self):
        self.server.permissions.clear()
        self.server.questions.clear()
        binding = TaskBinding.from_mapping(self.record)
        observer = OpenCode(binding)
        self.assertEqual(observer.pending(), [])
        observed = status(binding, observer)
        self.assertEqual(observed["pending_asks"], 0)
        self.assertEqual(observed["task_attention"], "UNKNOWN")

    def test_status_reads_upstream_not_guessed_completion(self):
        code, result = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertEqual(result["session_state"], "busy")
        self.assertEqual(result["pending_asks"], 2)
        self.assertEqual(result["task"]["issue"], 292)
        self.server.session_status = "UNSUPPORTED"
        _, result = self.run_cli("status")
        self.assertEqual(result["session_state"], "UNKNOWN")

    def test_verified_idle_session_is_absent_from_sparse_status(self):
        self.server.sparse_status = True
        code, result = self.run_cli("status")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["session_state"], "idle")
        # Runtime idleness does not imply that the Issue has completed.
        self.assertNotIn("completed", result)
        self.assertEqual(result["pending_asks"], 2)

    def test_transport_module_has_no_standalone_answer_entrypoint(self):
        self.assertFalse(callable(getattr(transport, "main", None)))
        self.assertFalse(callable(getattr(transport, "build_parser", None)))
        self.assertFalse(callable(getattr(TaskBinding, "read", None)))
        self.assertEqual(len(self.server.replies), 0)

    def test_permission_and_question_preserve_upstream_details(self):
        code, result = self.run_cli("asks")
        self.assertEqual(code, 0)
        self.assertEqual([p["kind"] for p in result["pending"]],
                         ["permission", "question"])
        self.assertEqual(result["pending"][0]["upstream"]["patterns"], ["pwd"])
        self.assertEqual(result["pending"][1]["upstream"]["questions"][0]["question"], "Proceed?")
        _, show = self.run_cli("ask-show", "--request-id", PERMISSION)
        self.assertEqual(show["ask"]["request_id"], PERMISSION)

    def test_permission_once_reply_and_no_duplicate(self):
        code, result = self.run_cli("ask-reply", "--kind", "permission",
                                    "--request-id", PERMISSION, "--decision", "once")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["applied"])
        self.assertEqual(self.server.replies[0][1], {"reply": "once"})
        _, listing = self.run_cli("asks")
        self.assertEqual(len(listing["pending"]), 1)
        code, duplicate = self.run_cli("ask-reply", "--kind", "permission",
                                       "--request-id", PERMISSION, "--decision", "once")
        self.assertEqual(code, 2)
        self.assertEqual(duplicate["error_code"], "ASK_NOT_PENDING")
        self.assertEqual(len(self.server.replies), 1)

    def test_permission_reject_reply_does_not_grant(self):
        code, result = self.run_cli(
            "ask-reply", "--kind", "permission",
            "--request-id", PERMISSION, "--decision", "reject")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["applied"])
        self.assertEqual(self.server.replies[0][1], {"reply": "reject"})
        self.assertEqual(self.server.permissions, [])
        code, repeated = self.run_cli(
            "ask-reply", "--kind", "permission",
            "--request-id", PERMISSION, "--decision", "reject")
        self.assertEqual(code, 2)
        self.assertEqual(repeated["error_code"], "ASK_NOT_PENDING")
        self.assertEqual(len(self.server.replies), 1)

    def test_question_reply(self):
        code, result = self.run_cli(
            "ask-reply", "--kind", "question", "--request-id", QUESTION,
            "--answers-json", '[["Yes"]]')
        self.assertEqual(code, 0, result)
        self.assertEqual(self.server.replies[0][1]["answers"], [["Yes"]])
        code, bad = self.run_cli("ask-reply", "--kind", "question",
                                  "--request-id", QUESTION, "--answers-json", '[["Yes"]]')
        self.assertEqual(bad["error_code"], "ASK_NOT_PENDING")

    def test_invalid_question_answers_do_not_send(self):
        code, result = self.run_cli("ask-reply", "--kind", "question",
                                    "--request-id", QUESTION, "--answers-json", "[]")
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "INVALID_REPLY")
        self.assertEqual(self.server.replies, [])

    def test_single_choice_does_not_accept_multiple_selections(self):
        code, result = self.run_cli(
            "ask-reply", "--kind", "question", "--request-id", QUESTION,
            "--answers-json", '[["Yes","No"]]')
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "INVALID_REPLY")
        self.assertEqual(self.server.replies, [])

    def test_custom_disabled_rejects_unoffered_label(self):
        self.server.questions[0]["questions"][0]["custom"] = False
        code, result = self.run_cli(
            "ask-reply", "--kind", "question", "--request-id", QUESTION,
            "--answers-json", '[["Unlisted"]]')
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "INVALID_REPLY")
        self.assertEqual(self.server.replies, [])

    def test_explicit_custom_question_permits_free_text(self):
        self.server.questions[0]["questions"][0]["custom"] = True
        self.server.accepted_question_answers = [["My custom answer"]]
        code, result = self.run_cli(
            "ask-reply", "--kind", "question", "--request-id", QUESTION,
            "--answers-json", '[["My custom answer"]]')
        self.assertEqual(code, 0, result)
        self.assertTrue(result["applied"])
        self.assertEqual(self.server.replies[-1][1]["answers"], [["My custom answer"]])

    def test_invalid_question_answers_types_are_rejected(self):
        for answer in ('{}', 'null', '["Yes"]', '[[3]]', '[[]]', '[["Yes"],["No"]]'):
            with self.subTest(answer=answer):
                code, result = self.run_cli(
                    "ask-reply", "--kind", "question",
                    "--request-id", QUESTION, "--answers-json", answer)
                self.assertEqual(code, 2)
                self.assertEqual(result["error_code"], "INVALID_REPLY")
        self.assertEqual(self.server.replies, [])
        self.assertEqual(len(self.server.questions), 1)

    def test_different_session_ask_is_not_exposed(self):
        self.server.permissions.append({
            "id": "per_anotherSession444", "sessionID": "ses_someOtherSession",
            "permission": "bash", "patterns": ["*"], "always": [],
            "metadata": {}})
        _, result = self.run_cli("asks")
        self.assertEqual(len(result["pending"]), 2)

    def test_session_directory_mismatch_blocks_reply(self):
        self.server.session_directory = str(self.root)
        code, result = self.run_cli("ask-reply", "--kind", "permission",
                                    "--request-id", PERMISSION, "--decision", "once")
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "SESSION_MISMATCH")
        self.assertFalse(self.server.replies)

    def test_optional_open_code_server_basic_auth(self):
        with patch.dict("os.environ", {"OPENCODE_SERVER_USERNAME": "opencode",
                                       "OPENCODE_SERVER_PASSWORD": "probe-secret"}):
            code, result = self.run_cli("status")
        self.assertEqual(code, 0, result)
        expected = base64.b64encode(b"opencode:probe-secret").decode()
        self.assertEqual(self.server.last_authorization, "Basic " + expected)

    def test_non_loopback_endpoint_rejected(self):
        self.record["server_url"] = "http://example.com:4096"
        self.store()
        code, result = self.run_cli("asks")
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "INVALID_BINDING")

    def test_invalid_loopback_port_is_reported_not_crash(self):
        self.record["server_url"] = "http://127.0.0.1:notaport"
        self.store()
        code, result = self.run_cli("asks")
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "INVALID_BINDING")

    def test_missing_or_wrong_revision_rejected(self):
        self.record.pop("contract_revision")
        self.store()
        code, result = self.run_cli("asks")
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "INVALID_BINDING")

    def test_stale_wrong_kind_does_not_send(self):
        code, result = self.run_cli("ask-reply", "--kind", "question",
                                    "--request-id", PERMISSION, "--answers-json", '[["Yes"]]')
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "ASK_NOT_PENDING")
        self.assertEqual(self.server.replies, [])


if __name__ == "__main__":
    unittest.main()
