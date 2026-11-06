"""Offline contract tests for a future opt-in model selection sidecar.

No KAGARI Task, OpenCode session, network or source publication is involved.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from tools.kagari_task_model_binding import (
    FrozenModelBinding,
    ModelBindingError,
    ModelSelection,
    load_model_binding,
    revalidate_model_binding,
)

HEAD = "a" * 40
TASK_DIGEST = "b" * 64
APPROVED = ModelSelection("openai", "gpt-6-astra", "max")
ROLE_SET = ["planner", "primary", "leaf-a", "leaf-b"]


class ModelBindingContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="kumiki-model-binding-r1-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "model-selection.json"
        self.data = {
            "schema_version": 1,
            "kind": "KAGARI_TASK_MODEL_BINDING",
            "project_head": HEAD,
            "task_spec_sha256": TASK_DIGEST,
            "model": {
                "provider_id": "openai",
                "model_id": "gpt-6-astra",
                "variant": "max",
            },
            "roles": list(ROLE_SET),
        }

    def write(self, data: dict | None = None, *, compact: bool = False) -> None:
        self.path.write_text(
            json.dumps(
                self.data if data is None else data,
                sort_keys=compact,
                separators=(",", ":") if compact else None,
            ),
            encoding="utf-8",
        )

    def load(
        self,
        *,
        head: str = HEAD,
        digest: str = TASK_DIGEST,
        allowed: frozenset[ModelSelection] = frozenset({APPROVED}),
    ) -> FrozenModelBinding:
        return load_model_binding(
            self.path,
            expected_project_head=head,
            expected_task_spec_sha256=digest,
            allowed_models=allowed,
        )

    def assert_fails(self, code: str = "INVALID_BINDING") -> None:
        with self.assertRaises(ModelBindingError) as error:
            self.load()
        self.assertEqual(error.exception.code, code)

    def test_valid_strict_identity_and_immutable_snapshot(self) -> None:
        self.write()
        frozen = self.load()
        self.assertIsInstance(frozen, FrozenModelBinding)
        self.assertEqual(frozen.model, APPROVED)
        self.assertEqual(frozen.roles, tuple(ROLE_SET))
        self.assertEqual(frozen.project_head, HEAD)
        self.assertEqual(frozen.task_spec_sha256, TASK_DIGEST)
        self.assertEqual(frozen.raw_sha256, hashlib.sha256(self.path.read_bytes()).hexdigest())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            frozen.model = ModelSelection("openai", "other", "max")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            frozen.roles = ("planner",)
        self.assertEqual(revalidate_model_binding(
            self.path, frozen, allowed_models=frozenset({APPROVED})
        ), frozen)

    def test_accepts_sha256_git_object_head(self) -> None:
        self.data["project_head"] = "c" * 64
        self.write()
        self.assertEqual(self.load(head="c" * 64).project_head, "c" * 64)

    def test_canonical_hash_is_order_whitespace_independent(self) -> None:
        self.write()
        first = self.load()
        self.write(compact=True)
        second = self.load()
        self.assertNotEqual(first.raw_sha256, second.raw_sha256)
        self.assertEqual(first.canonical_sha256, second.canonical_sha256)

    def test_post_preparation_whitespace_reformat_is_not_silently_accepted(self) -> None:
        self.write()
        previous = self.load()
        self.write(compact=True)
        with self.assertRaises(ModelBindingError) as error:
            revalidate_model_binding(self.path, previous, allowed_models=frozenset({APPROVED}))
        self.assertEqual(error.exception.code, "IDENTITY_DRIFT")

    def test_prepared_selection_removal_from_allowlist_fails_closed(self) -> None:
        self.write()
        frozen = self.load()
        with self.assertRaises(ModelBindingError) as error:
            revalidate_model_binding(
                self.path, frozen, allowed_models=frozenset({ModelSelection("openai", "other", "max")})
            )
        self.assertEqual(error.exception.code, "UNSUPPORTED_MODEL")

    def test_changed_sidecar_model_requires_new_binding(self) -> None:
        self.write()
        frozen = self.load()
        self.data["model"]["variant"] = "high"
        self.write()
        with self.assertRaises(ModelBindingError):
            revalidate_model_binding(self.path, frozen, allowed_models=frozenset({
                APPROVED, ModelSelection("openai", "gpt-6-astra", "high")
            }))

    def test_invalid_root_fields_default_deny(self) -> None:
        for name in list(self.data):
            with self.subTest(missing=name):
                altered = dict(self.data)
                del altered[name]
                self.write(altered)
                self.assert_fails()
        altered = dict(self.data)
        altered["unexpected"] = "data"
        self.write(altered)
        self.assert_fails()

    def test_schema_version_and_kind_are_exact(self) -> None:
        for bad in (True, 1.0, 0, 2, None, "1"):
            with self.subTest(schema=repr(bad)):
                self.data["schema_version"] = bad
                self.write()
                self.assert_fails()
        self.data["schema_version"] = 1
        for bad in ("KAGARI_TASK_MODEL", "", None, 17):
            with self.subTest(kind=repr(bad)):
                self.data["kind"] = bad
                self.write()
                self.assert_fails()

    def test_unknown_missing_nested_model_fields(self) -> None:
        original = dict(self.data["model"])
        for key in ("provider_id", "model_id", "variant"):
            with self.subTest(missing=key):
                self.data["model"] = {k: v for k, v in original.items() if k != key}
                self.write()
                self.assert_fails()
        self.data["model"] = {**original, "secret": "not-allowed"}
        self.write()
        self.assert_fails()

    def test_duplicate_keys_are_rejected_at_any_depth(self) -> None:
        self.write()
        raw = self.path.read_text()
        self.path.write_text(raw.replace('"schema_version": 1,',
                                         '"schema_version": 1, "schema_version": 1,'))
        self.assert_fails()
        self.write()
        raw = self.path.read_text()
        self.path.write_text(raw.replace('"variant": "max"',
                                         '"variant": "max", "variant": "max"'))
        self.assert_fails()

    def test_nonfinite_and_malformed_utf8_are_rejected(self) -> None:
        for bad in (b'NaN', b'Infinity', b'-Infinity'):
            with self.subTest(bad=bad):
                self.write()
                self.path.write_bytes(self.path.read_bytes().replace(b'"schema_version": 1',
                                                                      b'"schema_version": ' + bad))
                self.assert_fails()
        self.path.write_bytes(b"\xff\xfe")
        self.assert_fails()
        self.path.write_bytes(b"{not-json}")
        self.assert_fails()

    def test_wrong_binding_identity_is_not_authorized(self) -> None:
        self.write()
        with self.assertRaises(ModelBindingError) as x:
            self.load(head="c" * 40)
        self.assertEqual(x.exception.code, "IDENTITY_DRIFT")
        with self.assertRaises(ModelBindingError) as x:
            self.load(digest="c" * 64)
        self.assertEqual(x.exception.code, "IDENTITY_DRIFT")
        for invalid in ("z" * 40, "A" * 40, "a" * 39, ""):
            with self.subTest(head=invalid):
                with self.assertRaises(ModelBindingError):
                    self.load(head=invalid)
        for invalid in ("B" * 64, "f" * 63, ""):
            with self.subTest(digest=invalid):
                with self.assertRaises(ModelBindingError):
                    self.load(digest=invalid)

    def test_wrong_roles_and_role_order_fail_closed(self) -> None:
        for bad in (
            [], ROLE_SET[:-1], ROLE_SET + ["leaf-c"],
            ["primary", "planner", "leaf-a", "leaf-b"],
            ["planner", "primary", "leaf-a", "leaf-a"],
            ["planner", "primary", "leaf-a", 4],
            "planner,primary,leaf-a,leaf-b",
        ):
            with self.subTest(roles=repr(bad)):
                self.data["roles"] = bad
                self.write()
                self.assert_fails()

    def test_model_identity_and_variant_strict(self) -> None:
        for field, bad in (
            ("provider_id", "OpenAI"), ("provider_id", "openai/other"),
            ("provider_id", "*"), ("provider_id", ""),
            ("model_id", "../gpt-6-astra"), ("model_id", "gpt 6"),
            ("model_id", "gpt-6/alternate"),
            ("variant", "MAX"), ("variant", "ultrafast"),
            ("variant", "unknown"), ("variant", "*"),
        ):
            with self.subTest(field=field, bad=bad):
                self.data["model"] = {
                    "provider_id": "openai", "model_id": "gpt-6-astra", "variant": "max",
                }
                self.data["model"][field] = bad
                self.write()
                self.assert_fails("INVALID_MODEL")

    def test_all_supported_variants_require_host_allowlist(self) -> None:
        for name in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(variant=name):
                approved = ModelSelection("openai", "gpt-6-astra", name)
                self.data["model"]["variant"] = name
                self.write()
                self.assertEqual(self.load(allowed=frozenset({approved})).model, approved)
                if name != "max":
                    self.assert_fails("UNSUPPORTED_MODEL")

    def test_selection_allowlist_must_be_explicit(self) -> None:
        self.write()
        for bad in (frozenset(), None, set([APPROVED]), frozenset({"gpt-6-astra"})):
            with self.subTest(allowed=repr(bad)):
                with self.assertRaises(ModelBindingError):
                    self.load(allowed=bad)

    def test_unsafe_paths_missing_symlink_directory_and_hardlink(self) -> None:
        self.write()
        with self.assertRaises(ModelBindingError):
            load_model_binding(Path("relative.json"),
                               expected_project_head=HEAD,
                               expected_task_spec_sha256=TASK_DIGEST,
                               allowed_models=frozenset({APPROVED}))
        actual = self.path
        self.path = actual.with_name("not-found.json")
        self.assert_fails()
        self.path = Path(self.temp.name)
        self.assert_fails()
        self.path = actual.with_name("link.json")
        self.path.symlink_to(actual)
        self.assert_fails()
        self.path = actual.with_name("hardlink.json")
        os.link(actual, self.path)
        self.assert_fails()

    def test_oversized_empty_and_non_regular_sidecar_rejected(self) -> None:
        self.path.write_bytes(b"")
        self.assert_fails()
        self.path.write_bytes(b"x" * (16 * 1024 + 1))
        self.assert_fails()

    def test_fifo_rejected_without_blocking(self) -> None:
        import multiprocessing

        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO only on POSIX")
        os.mkfifo(self.path)
        ctx = multiprocessing.get_context("fork")

        def attempt_read() -> None:
            try:
                self.load()
            except ModelBindingError:
                return
            raise AssertionError("FIFO sidecar unexpectedly accepted")

        process = ctx.Process(target=attempt_read)
        process.start()
        try:
            process.join(timeout=1.5)
            self.assertFalse(process.is_alive(), "sidecar reader blocked on a FIFO")
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.terminate()
            process.join()

    def test_deep_json_is_bounded_validation_error(self) -> None:
        self.path.write_bytes(b"[" * 6000 + b"0" + b"]" * 6000)
        self.assert_fails("INVALID_BINDING")

    def test_decoder_recursion_failure_has_stable_error(self) -> None:
        from unittest import mock

        self.write()
        with mock.patch(
            "tools.kagari_task_model_binding.json.loads",
            side_effect=RecursionError("excessive JSON depth"),
        ):
            self.assert_fails("INVALID_BINDING")

    def test_selection_cannot_be_constructed_with_wrong_types(self) -> None:
        for field,bad in (("provider_id", True), ("model_id", None),
                          ("variant", 3), ("variant", "xhigh!")):
            with self.subTest(field=field,bad=bad):
                model=dict(provider_id="openai",model_id="gpt-6-astra",variant="max")
                model[field]=bad
                with self.assertRaises(ModelBindingError):
                    ModelSelection(**model)


if __name__ == "__main__":
    unittest.main()
