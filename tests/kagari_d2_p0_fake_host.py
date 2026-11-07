"""Disposable D2-P0 fake Host. NEVER an attested production trust root.

This model deliberately persists its own claim/ACK file inside a temporary
test fixture, not in KAGARI's production state or GitHub. It simulates
authenticated, single-egress operation ownership only for deterministic
source-only validator tests. Directly modifying its JSON demonstrates that
real Host authenticity and rollback detection are still NYI.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING

# Keep canonical top-level identity: the #291 coordinator and #337 both
# import top-level modules. Importing tools.* would duplicate exact types.
from kagari_vnext_main_v2_provenance import (
    D2P0HostGateway, HostClaim, HostPostAck, OriginalOperation, _canonical,
)
from kagari_metadata_v2_pr_checkpoint import V2PRCheckpoint, render_checkpoint

if TYPE_CHECKING:
    from tests.test_kagari_vnext_main_v2_initial_checkpoint import _Harness


class DisposableD2Host:
    def __init__(self, harness: "_Harness") -> None:
        self.h = harness
        self.path = harness.src.root / "fake-d2-provenance.json"
        self.issuer = "synthetic-host-on-disposable-git"
        self.epoch = 1
        self.scope_override = None
        self.ack_mutation = None
        self.mode = "ok"

    def _load(self):
        if self.path.exists():
            return json.loads(self.path.read_text())
        return None

    def _save(self, value):
        self.path.write_text(json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False))

    def claim(self, scope: OriginalOperation) -> HostClaim:
        return HostClaim(
            scope, self.issuer, self.epoch,
            hashlib.sha256(_canonical([scope.repository_id, scope.issue])).hexdigest(),
            hashlib.sha256(_canonical([scope.repository_id, scope.pr_number, "initial"])).hexdigest(),
            True, True, True,
            tuple(range(1, self.epoch + 1)),
            tuple(range(1, self.epoch + 1)))

    def entry(self, scope: OriginalOperation) -> HostClaim | None:
        if self.mode in {"missing", "invalid"}:
            return None if self.mode == "missing" else "arbitrary boolean cannot attest"
        current = self._load()
        if current is not None and current["scope"] != asdict(scope):
            return None
        return self.claim(scope)

    def establish(self, scope: OriginalOperation, recovering: bool) -> HostClaim | None:
        if self.mode == "no_claim":
            return None
        current = self._load()
        if current is None:
            if recovering:
                return None  # Never rebuild missing claim after possible effect.
            self._save({"scope": asdict(scope), "ack": None})
        elif current["scope"] != asdict(scope):
            return None
        return self.claim(scope)

    def post(self, scope: OriginalOperation, value: V2PRCheckpoint,
             pr: int, body: str, deadline: float):
        stored = self._load()
        if (stored is None or stored["scope"] != asdict(scope)
                or pr != value.pr_number or body != render_checkpoint(value)):
            raise RuntimeError("synthetic host egress scope/fence invalid")
        comment_id = self.h.post(pr, body, deadline)
        # Create synthetic durable ACK only after fake transport returns the
        # concrete comment ID. Lost remote ACK => no original Host ACK.
        if type(comment_id) is not int or comment_id <= 0:
            raise RuntimeError("synthetic host did not obtain a valid POST response")
        ack = HostPostAck(
            scope, self.issuer, self.epoch, "a"*32, pr,
            value.principal, comment_id, value.key,
            hashlib.sha256(body.encode("utf-8")).hexdigest())
        stored["ack"] = asdict(ack)
        self._save(stored)
        # Inject a SIGKILL AFTER durable Host ACK but BEFORE worker result.
        self.h.fire("provenance_ack")
        return comment_id

    def read_ack(self, scope: OriginalOperation, value: V2PRCheckpoint,
                 comment_id: int) -> HostPostAck | None:
        saved = self._load()
        if saved is None or saved.get("ack") is None:
            return None
        ack = dict(saved["ack"])
        if self.ack_mutation:
            ack.update(self.ack_mutation)
        ack["operation"] = OriginalOperation(**ack["operation"])
        return HostPostAck(**ack)

    def gateway(self) -> D2P0HostGateway:
        return D2P0HostGateway(
            self.entry, self.establish, self.post, self.read_ack)
