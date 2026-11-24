"""Shared fixtures for the kagari_mvi tests: a fake gh/git runner and sample Issues."""
import json
import re
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kagari_mvi as mvi  # noqa: E402


PLACEMENT = ("### 全体ロジック\nrun-once の流れ\n\n### 置く理由\n同じ段で止められる\n\n"
             "### 却下した置き場所\n- review-once：実装の後になる\n\n")
TEST_BODY = "body %d\n\n" + PLACEMENT + "### テストコマンド\n```sh\npython3 -m unittest\n```\n"


def issue(number, labels, created="2026-11-01T00:00:00Z", pr=False, state="open"):
    data = {"number": number, "title": "Issue %d" % number, "body": TEST_BODY % number,
            "state": state, "created_at": created,
            "labels": [{"name": name} for name in labels]}
    if pr:
        data["pull_request"] = {"url": "x"}
    return data


def stream(result, is_error=False, denials=0, denied=None):
    denied = denied if denied is not None else [{"tool_name": "Bash"}] * denials
    return "\n".join(json.dumps(event) for event in (
        {"type": "system", "subtype": "init"},
        {"type": "result", "is_error": is_error, "result": result,
         "permission_denials": denied}))


def labeled(login, name=mvi.APPROVED, at="2026-11-14T01:00:00Z"):
    return {"event": "labeled", "label": {"name": name}, "actor": {"login": login}, "created_at": at}


def edited(at):
    return {"data": {"repository": {"issue": {"lastEditedAt": at}}}}


class FakeRunner:
    """Answers `gh api` calls from a route table and records every call."""

    def __init__(self, routes, fail=None, outputs=None):
        self.routes = routes  # {(method, path-prefix): response}
        self.fail = fail or {}  # {argv-substring: output}
        self.outputs = outputs if outputs is not None else {
            "stream-json": stream("done\nstatus: COMPLETED"), "diff --cached --name-only": " M a.py",
            "rev-parse HEAD": "abc123\n"}
        self.calls = []

    def __call__(self, args, cwd=None, input=None, timeout=None):
        is_api = list(args[:2]) == ["gh", "api"]
        self.calls.append((list(args), cwd, json.loads(input) if input and is_api else input))
        joined = " ".join(args)
        for needle, output in self.fail.items():
            if needle in joined:
                raise mvi.CommandError(args, 1, output)
        if not is_api:
            return next((out for needle, out in self.outputs.items() if needle in joined), "")
        method, path = ("DIFF" if "diff" in args[5] else args[3]), args[6]
        found = re.fullmatch(r"repos/o/r/issues/(\d+)", path)
        if method == "GET" and found and ("GET", path) not in self.routes:
            # No lag by default: a direct GET of an Issue shows what the search list shows (#537).
            listed = self.routes.get(("GET", "repos/o/r/issues?")) or []
            same = next((i for i in listed if i["number"] == int(found.group(1))), None)
            if same is not None:
                return json.dumps(same)
        for (route_method, prefix), response in self.routes.items():
            if route_method == method and path.startswith(prefix):
                if isinstance(response, str):
                    return response
                return json.dumps(response) if response is not None else ""
        return ""

    def api_calls(self, method, prefix):
        return [body for args, _, body in self.calls
                if args[:2] == ["gh", "api"] and args[3] == method and args[6].startswith(prefix)]

    def added_labels(self):
        return [name for body in self.api_calls("POST", "repos/o/r/issues/") if body and "labels" in body
                for name in body["labels"]]

    def removed_labels(self):
        return [args[6].rsplit("/", 1)[1] for args, _, _ in self.calls
                if args[:2] == ["gh", "api"] and args[3] == "DELETE"]


REVIEW_BODY = ("add greet\n\n### 受入条件\n- [ ] greet() returns Hello\n- [x] tests added\n\n"
               "### 非目標\n- nothing else\n")
