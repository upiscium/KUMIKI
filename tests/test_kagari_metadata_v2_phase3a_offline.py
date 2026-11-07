"""#337 P3A: no remote effects, credentials, real ref writes or permissions.

All fixtures are invented self-reported candidates. Nothing below establishes
real GitHub actor authority, actual Ruleset enforcement or source provenance.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import kagari_metadata_v2_phase3a_offline as mod


def candidate() -> dict:
    """Syntactic disposable-only policy (NOT a trusted external observation)."""
    return {
        "schema": 1, "kind": "phase3a-offline-candidate",
        "repository": {
            "id": 424242, "name": "synthetic-example/phase3-canary",
            "visibility": "private", "object_format": "sha1",
            "default_oid": "a" * 40, "disposable_claim": True,
        },
        "corpus": {
            "origin": "independently_generated_synthetic",
            "sha256": hashlib.sha256(b"generated fixture only").hexdigest(),
            "storage": "offline-only", "classification": "synthetic_only",
        },
        "refs": {
            "archive": mod.ARCHIVE,
            "issues": ["refs/heads/kagari-metadata/101",
                       "refs/heads/kagari-metadata/102"],
        },
        "actors": [
            {"id": 19, "role": "archive_writer", "candidate_ref": mod.ARCHIVE},
            {"id": 20, "role": "negative_actor", "candidate_ref": mod.ARCHIVE},
        ],
        "rulesets": {
            "retention": {
                "id": 40, "target_ref": mod.ARCHIVE,
                "enforcement": "active",
                "rules": ["deletion", "non_fast_forward"],
                "bypass_actor_ids": [], "inventory": "candidate_only",
            },
            "write_scope": {
                "id": 41, "target_ref": mod.ARCHIVE,
                "enforcement": "active",
                "rules": ["creation", "update"],
                "bypass_actor_ids": [19], "inventory": "candidate_only",
            },
        },
        "gates": {
            "external_read_proven": False,
            "remote_write_authorized": False,
            "conditional_delete_qualified": False,
        },
    }


def encoded(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


def changed(path: tuple, value) -> dict:
    result = copy.deepcopy(candidate())
    obj = result
    for part in path[:-1]:
        obj = obj[part]
    obj[path[-1]] = value
    return result


class Phase3AOfflineOnlyTest(unittest.TestCase):
    def test_valid_disposable_candidate_remains_external_not_run(self):
        data = encoded(candidate())
        parsed = mod.validate_candidate(data)
        self.assertEqual(424242, parsed["repository"]["id"])
        self.assertIs(parsed["gates"]["remote_write_authorized"], False)
        manifest = mod.build_not_run_manifest(data)
        loaded = mod.validate_not_run_manifest(manifest, data)
        self.assertEqual(manifest, mod.build_not_run_manifest(data))
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         loaded["candidate_sha256"])
        self.assertEqual("untrusted_offline_candidate", loaded["source"])
        self.assertEqual("NONE", loaded["authorization"])
        self.assertEqual("NOT_RUN", loaded["external_proof"])
        self.assertEqual("NOT_RUN", loaded["external_effects"])
        self.assertIs(loaded["canary_ready"], False)
        self.assertEqual(11, len(loaded["results"]))
        self.assertEqual(
            [f"AC-META2-{n:02d}" for n in range(1, 12)],
            [r["ac"] for r in loaded["results"]])
        self.assertEqual(
            {"NOT_RUN", "BLOCKED"}, {r["status"] for r in loaded["results"]})
        self.assertTrue(all(r["evidence"] == [] for r in loaded["results"]))
        self.assertEqual(8, len(loaded["pending"]))

    def test_no_read_write_command_git_or_network_capability(self):
        data = encoded(candidate())
        # No real file, Git, subprocess or network operation may be attempted.
        with (patch("builtins.open", side_effect=AssertionError("filesystem")),
              patch.object(Path, "open", side_effect=AssertionError("path")),
              patch.object(subprocess, "run",
                           side_effect=AssertionError("subprocess")),
              patch.object(subprocess, "Popen",
                           side_effect=AssertionError("process")),
              patch.object(socket, "socket",
                           side_effect=AssertionError("network"))):
            manifest = mod.build_not_run_manifest(data)
            self.assertEqual(11, len(mod.validate_not_run_manifest(
                manifest, data)["results"]))

    def test_false_and_true_are_not_integer_identity(self):
        for path, value in (
            (("schema",), True),
            (("schema",), 1.0),
            (("repository", "id"), True),
            (("repository", "id"), 424242.0),
            (("actors", 0, "id"), True),
            (("rulesets", "retention", "id"), 40.0),
            (("rulesets", "write_scope", "bypass_actor_ids"), [True]),
        ):
            with self.subTest(path=path, value=value):
                with self.assertRaises(mod.OfflinePreflightRefused):
                    mod.validate_candidate(encoded(changed(path, value)))

    def test_repository_binding_and_visibility_fail_closed(self):
        cases = [
            (("repository", "name"), "upiscium/KUMIKI"),
            (("repository", "id"), 1183254623),
            (("repository", "name"), "../KUMIKI"),
            (("repository", "name"), "org/.hidden"),
            (("repository", "name"), "org/foo/bar"),
            (("repository", "name"), ""),
            (("repository", "id"), -1),
            (("repository", "id"), 0),
            (("repository", "id"), 2**53),
            (("repository", "disposable_claim"), False),
            (("repository", "disposable_claim"), 1),
            (("repository", "visibility"), "unknown"),
            (("repository", "visibility"), 123),
            (("repository", "object_format"), "sha256"),
            (("repository", "default_oid"), "G" * 40),
            (("repository", "default_oid"), "0" * 64),
        ]
        for path, value in cases:
            with self.subTest(path=path, value=value):
                with self.assertRaises(mod.OfflinePreflightRefused):
                    mod.validate_candidate(encoded(changed(path, value)))

    def test_synthetic_only_corpus_and_storage_fail_closed(self):
        for path, value in (
            (("corpus", "origin"), "production_export"),
            (("corpus", "origin"), "unknown"),
            (("corpus", "sha256"), "0" * 40),
            (("corpus", "sha256"), "z" * 64),
            (("corpus", "storage"), "https://untrusted.example"),
            (("corpus", "storage"), "remote"),
            (("corpus", "classification"), "secret"),
        ):
            with self.subTest(path=path):
                with self.assertRaises(mod.OfflinePreflightRefused):
                    mod.validate_candidate(encoded(changed(path, value)))

    def test_exact_archive_and_two_canonical_issue_refs(self):
        for value in (
            [],
            ["refs/heads/kagari-metadata/101"],
            ["refs/heads/kagari-metadata/101"] * 2,
            ["refs/heads/kagari-metadata/102",
             "refs/heads/kagari-metadata/101"],
            ["refs/heads/kagari-metadata/01",
             "refs/heads/kagari-metadata/102"],
            ["refs/heads/kagari-metadata/101",
             "refs/heads/kagari-metadata/0"],
            ["refs/heads/kagari-metadata/101",
             "refs/heads/kagari-metadata/102/other"],
            ["refs/heads/kagari-metadata/101",
             "refs/heads/main"],
            [["bad"], ["bad"]],
            [True, False],
        ):
            with self.subTest(issue_refs=value):
                with self.assertRaises(mod.OfflinePreflightRefused):
                    mod.validate_candidate(
                        encoded(changed(("refs", "issues"), value)))
        with self.assertRaises(mod.OfflinePreflightRefused):
            mod.validate_candidate(encoded(
                changed(("refs", "archive"), "refs/heads/main")))

    def test_core_128_digit_canonical_issue_ref_boundary(self):
        # Same canonical numeric grammar as metadata_v2.issue_ref, including
        # the maximum 128 digits, unlike an arbitrary 9-digit truncation.
        first = "1" * 128
        second = "2" * 128
        accepted = changed(
            ("refs", "issues"),
            ["refs/heads/kagari-metadata/" + first,
             "refs/heads/kagari-metadata/" + second])
        self.assertEqual(
            accepted["refs"]["issues"],
            mod.validate_candidate(encoded(accepted))["refs"]["issues"])
        beyond = changed(
            ("refs", "issues"),
            ["refs/heads/kagari-metadata/" + first,
             "refs/heads/kagari-metadata/" + "2" * 129])
        with self.assertRaises(mod.OfflinePreflightRefused):
            mod.validate_candidate(encoded(beyond))

    def test_surrogate_escaped_inside_valid_schema_fails_before_shape(self):
        # A UTF-8-safe JSON source may contain an ASCII "\\ud800" escape:
        # only the *decoded* string contains an unpaired surrogate.
        for path in (
            ("repository", "name"),
            ("corpus", "origin"),
            ("rulesets", "retention", "target_ref"),
        ):
            with self.subTest(path=path):
                with self.assertRaises(
                    mod.OfflinePreflightRefused) as error:
                    mod.validate_candidate(
                        encoded(changed(path, "synthetic/\ud800")))
                self.assertEqual("offline_json_decoding_invalid",
                                 str(error.exception))
        with_bad_key = candidate()
        with_bad_key["repository"]["bad\ud800"] = "invalid"
        with self.assertRaises(mod.OfflinePreflightRefused) as error:
            mod.validate_candidate(encoded(with_bad_key))
        self.assertEqual("offline_json_decoding_invalid",
                         str(error.exception))

    def test_role_and_bypass_scope_inconsistencies_refused(self):
        baseline = candidate()
        cases = []
        c = copy.deepcopy(baseline)
        c["actors"][1]["id"] = 19
        cases.append(c)
        c = copy.deepcopy(baseline)
        c["actors"][1]["role"] = "archive_writer"
        cases.append(c)
        c = copy.deepcopy(baseline)
        c["actors"][0]["candidate_ref"] = "refs/heads/main"
        cases.append(c)
        c = copy.deepcopy(baseline)
        c["actors"][0]["token"] = "LEAK"
        cases.append(c)
        c = copy.deepcopy(baseline)
        c["actors"] = c["actors"][:1]
        cases.append(c)
        c = copy.deepcopy(baseline)
        c["actors"] = [c["actors"][0], None]
        cases.append(c)
        c = copy.deepcopy(baseline)
        c["rulesets"]["retention"]["bypass_actor_ids"] = [19]
        cases.append(c)
        c = copy.deepcopy(baseline)
        c["rulesets"]["retention"]["bypass_actor_ids"] = [[]]
        cases.append(c)
        c = copy.deepcopy(baseline)
        c["rulesets"]["write_scope"]["bypass_actor_ids"] = [20]
        cases.append(c)
        c = copy.deepcopy(baseline)
        c["rulesets"]["retention"]["id"] = 41
        cases.append(c)
        for index, obj in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(mod.OfflinePreflightRefused):
                    mod.validate_candidate(encoded(obj))

    def test_required_retention_policy_cannot_be_weakened(self):
        for path, value in (
            (("rulesets", "retention", "enforcement"), "evaluate"),
            (("rulesets", "retention", "enforcement"), "disabled"),
            (("rulesets", "retention", "rules"), ["deletion"]),
            (("rulesets", "retention", "rules"),
             ["non_fast_forward", "required_linear_history"]),
            (("rulesets", "retention", "target_ref"),
             "refs/heads/kagari/metadata/extra"),
            (("rulesets", "retention", "inventory"), "unknown"),
            (("rulesets", "retention", "inventory"), "complete"),
            (("rulesets", "write_scope", "enforcement"), "evaluate"),
            (("rulesets", "write_scope", "rules"), ["update", "update"]),
            (("rulesets", "write_scope", "rules"),
             ["creation", "update", "deletion"]),
            (("rulesets", "write_scope", "inventory"), "missing"),
        ):
            with self.subTest(path=path, value=value):
                with self.assertRaises(mod.OfflinePreflightRefused):
                    mod.validate_candidate(encoded(changed(path, value)))

    def test_no_source_only_promotion_by_self_report(self):
        for path, value in (
            (("gates", "external_read_proven"), True),
            (("gates", "remote_write_authorized"), True),
            (("gates", "conditional_delete_qualified"), True),
            (("gates", "external_read_proven"), 1),
            (("gates", "remote_write_authorized"), "false"),
        ):
            with self.subTest(path=path):
                with self.assertRaises(mod.OfflinePreflightRefused):
                    mod.validate_candidate(encoded(changed(path, value)))

    def test_unknown_fields_duplicate_json_unicode_and_size_rejected(self):
        cases = [
            b"",
            b"[]" ,
            b"{" + b"x" * 35000 + b"}",
            b'{"schema":1,"schema":1}',
            b'{"schema":1,"schema":2}',
            b'{"bad":NaN}',
            b'{"bad":Infinity}',
            b'\xff',
            b'{"bad":"\\ud800"}',
            b'{"bad":"a"' + b" " * 32000 + b"}",
        ]
        extra = candidate()
        extra["token"] = "never permitted"
        cases.append(encoded(extra))
        for data in cases:
            with self.subTest(data_prefix=data[:35]):
                with self.assertRaises(mod.OfflinePreflightRefused):
                    mod.validate_candidate(data)
        for path in (
            ("corpus", "unknown"),
            ("repository", "private_token"),
            ("rulesets", "retention", "api_key"),
        ):
            data = candidate()
            part = data
            for key in path[:-1]:
                part = part[key]
            part[path[-1]] = "stray"
            with self.assertRaises(mod.OfflinePreflightRefused):
                mod.validate_candidate(encoded(data))

    def test_manifest_remains_not_run_even_if_bytes_are_tampered(self):
        candidate_bytes = encoded(candidate())
        report = mod.build_not_run_manifest(candidate_bytes)
        cases = []
        record = json.loads(report)
        record["results"][0]["status"] = "PASS"
        cases.append(encoded(record))
        record = json.loads(report)
        record["results"][2]["evidence"] = ["fabricated"]
        cases.append(encoded(record))
        record = json.loads(report)
        record["results"] = record["results"][:-1]
        cases.append(encoded(record))
        record = json.loads(report)
        record["canary_ready"] = True
        cases.append(encoded(record))
        record = json.loads(report)
        record["authorization"] = "Human"
        cases.append(encoded(record))
        record = json.loads(report)
        record["pending"] = []
        cases.append(encoded(record))
        record = json.loads(report)
        record["token"] = "LEAK"
        cases.append(encoded(record))
        for obj in cases:
            with self.subTest(prefix=obj[:40]):
                with self.assertRaises(mod.OfflinePreflightRefused):
                    mod.validate_not_run_manifest(obj, candidate_bytes)
        alternative = changed(("repository", "id"), 424243)
        with self.assertRaises(mod.OfflinePreflightRefused):
            mod.validate_not_run_manifest(report, encoded(alternative))
        with self.assertRaises(mod.OfflinePreflightRefused):
            mod.validate_not_run_manifest(report[:-1], candidate_bytes)


if __name__ == "__main__":
    unittest.main()
