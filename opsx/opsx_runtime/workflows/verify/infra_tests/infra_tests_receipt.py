"""Durable PASS для детерминированного узла ``infra_tests``."""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Mapping

from agents.check_receipts import best_effort_write_pass, content_digest, read_pass
from agents.state import GraphState
from agents.verify.quality_gates_receipt import (
    collect_quality_gate_product_paths,
    local_runtime_environment_attestation,
)

_LOG = logging.getLogger(__name__)

INFRA_TESTS_CHECK = "infra_tests"
INFRA_TESTS_CONTRACT = "infra_tests_v1"
_INFRA_EXTRA_FILES = (
    "compose.test.yaml",
    "tests/docker-compose.test.yml",
    "opsx/project/infra",
)


def _collect_infra_product_paths(root: Path) -> set[Path]:
    paths = collect_quality_gate_product_paths(root)
    for rel in _INFRA_EXTRA_FILES:
        target = resource_path(root, rel)
        if target.is_file():
            paths.add(target)
        elif target.is_dir():
            paths.update(
                p
                for p in target.rglob("*")
                if p.is_file() and p.suffix.lower() in {".py", ".yaml", ".yml", ".json", ".toml"}
            )
    return paths


def _selection_digest(node_ids: tuple[str, ...]) -> str:
    return hashlib.sha256(
        json.dumps(list(node_ids), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class InfraTestsDurablePass:
    """Reuse ``PASS`` / ``PASS_NO_SCOPE`` без повторного docker/LLM hop."""

    @staticmethod
    def cache_key(
        state: GraphState,
        *,
        root: Path,
        node_ids: tuple[str, ...],
    ) -> tuple[str, str] | None:
        if not str(state.get("run_uuid") or "").strip():
            return None
        paths = _collect_infra_product_paths(root)
        if not paths:
            return None
        product = content_digest(root, paths)
        input_digest = hashlib.sha256(
            (product + "\0" + _selection_digest(node_ids)).encode("utf-8")
        ).hexdigest()
        verifier = hashlib.sha256(
            json.dumps(
                {
                    "contract": INFRA_TESTS_CONTRACT,
                    "environment": local_runtime_environment_attestation(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return input_digest, verifier

    @staticmethod
    def read_cached(
        state: GraphState,
        *,
        root: Path,
        change_id: str,
        node_ids: tuple[str, ...],
        pass_status: str,
    ) -> dict[str, Any] | None:
        try:
            cache_key = InfraTestsDurablePass.cache_key(state, root=root, node_ids=node_ids)
            if cache_key is None:
                return None
            input_digest, verifier_digest = cache_key
            stored = read_pass(
                root,
                change_id=change_id,
                check=INFRA_TESTS_CHECK,
                contract=INFRA_TESTS_CONTRACT,
                input_digest=input_digest,
                verifier_contract_digest=verifier_digest,
                pass_verdict=pass_status,
                input_paths=_collect_infra_product_paths(root),
            )
            if stored is None:
                _LOG.info(
                    "infra_tests_receipt miss change_id=%s reason=no_stored_pass",
                    change_id,
                )
                return None
            _LOG.info(
                "infra_tests_receipt hit change_id=%s status=%s",
                change_id,
                pass_status,
            )
            result = dict(stored["result"])
            result["infra_tests_execution_status"] = "REUSED_PASS"
            result["infra_tests_receipt"] = {
                "source": "durable_pass",
                "current_execution_id": str(state["run_uuid"]),
                "origin_execution_id": stored["origin_execution_id"],
                "evidence_id": stored["evidence_id"],
            }
            return result
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def save_pass(
        state: GraphState,
        *,
        root: Path,
        change_id: str,
        node_ids: tuple[str, ...],
        patch: Mapping[str, Any],
        pass_status: str,
    ) -> bool:
        cache_key = InfraTestsDurablePass.cache_key(state, root=root, node_ids=node_ids)
        if cache_key is None:
            return False
        input_digest, verifier_digest = cache_key
        saved = best_effort_write_pass(
            root=root,
            change_id=change_id,
            check=INFRA_TESTS_CHECK,
            contract=INFRA_TESTS_CONTRACT,
            input_digest=input_digest,
            verifier_contract_digest=verifier_digest,
            pass_verdict=pass_status,
            origin_execution_id=str(state["run_uuid"]),
            evidence_id=hashlib.sha256(
                json.dumps(dict(patch), sort_keys=True, default=str).encode("utf-8")
            ).hexdigest(),
            result=dict(patch),
        )
        if saved:
            _LOG.info("infra_tests_receipt saved change_id=%s status=%s", change_id, pass_status)
        return saved