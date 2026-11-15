"""Durable PASS для deterministic quality gates (pytest, pyright, mypy).

Идентичность product: ``src/``, product ``tests/`` (без ``tests/opsx``),
``migrations/``, ``configs/`` и корневые testing-конфиги — без Git.

Идентичность verifier: policy-файлы runner/diagnostics и снимок команд,
timeout, Python/venv и релевантных env-переменных (DB, PYTEST_ADDOPTS).

При отсутствии ``quality_gate_environment_id`` в state подставляется
локальная attestation из runtime (тот же контур, что в verifier digest).
"""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import json
import logging
import os
import platform
import sys
from pathlib import Path
from typing import Any, Mapping

from agents.check_receipts import best_effort_write_pass, content_digest, read_pass
from agents.state import GraphState

_LOG = logging.getLogger(__name__)

QUALITY_GATES_CHECK = "quality_gates"
QUALITY_GATES_CONTRACT = "quality_gates_v1"
QUALITY_GATES_PASS_VERDICT = "PASS"

# Полный gate patch с report/tails легко превышает лимит check-receipts (2 MiB).
_RECEIPT_TAIL_CHARS = 2_000


def slim_quality_gate_pass_for_receipt(patch: Mapping[str, Any]) -> dict[str, Any]:
    """Урезанный PASS для durable cache: exit codes и diagnostics, без мегабайтных tail."""
    slim: dict[str, Any] = {
        key: value
        for key, value in patch.items()
        if key != "quality_gate_report_text"
    }
    raw = slim.get("quality_gate_raw")
    if isinstance(raw, dict):
        slim_raw: dict[str, Any] = {}
        for name, check in raw.items():
            if not isinstance(check, dict):
                slim_raw[name] = check
                continue
            trimmed = dict(check)
            for tail_key in ("stdout_tail", "stderr_tail", "run_log"):
                tail = trimmed.get(tail_key)
                if isinstance(tail, str) and len(tail) > _RECEIPT_TAIL_CHARS:
                    trimmed[tail_key] = (
                        f"… [receipt-truncated; last {_RECEIPT_TAIL_CHARS} chars]\n"
                        f"{tail[-_RECEIPT_TAIL_CHARS:]}"
                    )
            slim_raw[name] = trimmed
        slim["quality_gate_raw"] = slim_raw
    return slim

_QUALITY_PRODUCT_DIRS = ("src", "tests", "migrations", "configs")
_QUALITY_PRODUCT_CONFIGS = (
    "pyproject.toml",
    "poetry.lock",
    "mypy.ini",
    "pytest.ini",
    "pyrightconfig_strict.json",
    "pyrightconfig.json",
    "setup.cfg",
)
_PRODUCT_SUFFIXES = frozenset(
    {".py", ".sql", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}
)
QUALITY_VERIFIER_POLICY_FILES = (
    "opsx/opsx_runtime/workflows/verify/gate_repair/runner.py",
    "opsx/opsx_runtime/workflows/verify/gate_repair/diagnostics.py",
    "opsx/opsx_runtime/workflows/verify/gate_repair/schemas.py",
    "opsx/opsx_runtime/workflows/verify/gate_repair/pytest_gate_timeout.py",
    "opsx/opsx_runtime/workflows/verify/gate_repair/pytest_subprocess_streaming.py",
    "opsx/opsx_runtime/agents/check_receipts.py",
    "opsx/opsx_runtime/agents/verify/quality_gates_receipt.py",
    "opsx/opsx_runtime/workflows/verify/pytest_execute/invoke.py",
    "opsx/opsx_runtime/workflows/verify/routing_log.py",
    "opsx/opsx_runtime/agents/verify/pytest_run_health.py",
)
_RELEVANT_ENV_NAMES = (
    "DATABASE_URL",
    "REDIS_URL",
    "PYTHONPATH",
    "PYTEST_ADDOPTS",
    "PYTHONHASHSEED",
    "CI",
    "VIRTUAL_ENV",
    "POETRY_VIRTUALENVS_PATH",
)


def _relevant_env_snapshot() -> dict[str, str]:
    return {name: os.environ.get(name, "") for name in _RELEVANT_ENV_NAMES}


def local_runtime_environment_attestation() -> str:
    """Стабильный id локальной среды: Python, venv, OS, DB/pytest env — без Git."""
    payload = {
        "kind": "local_runtime_v1",
        "python": sys.version,
        "prefix": sys.prefix,
        "platform": platform.platform(),
        "env": _relevant_env_snapshot(),
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"local-runtime-{digest[:24]}"


def resolve_quality_gate_environment_id(state: Mapping[str, Any]) -> str:
    """Явный id из state или автоматическая локальная attestation."""
    explicit = str(state.get("quality_gate_environment_id") or "").strip()
    if explicit:
        return explicit
    return local_runtime_environment_attestation()


def collect_quality_gate_product_paths(root: Path) -> set[Path]:
    """Файлы product scope для digest (без ``tests/opsx``)."""
    paths: set[Path] = set()
    for rel in _QUALITY_PRODUCT_DIRS:
        base = resource_path(root, rel)
        if not base.is_dir():
            continue
        paths.update(
            p
            for p in base.rglob("*")
            if p.is_file()
            and p.suffix.lower() in _PRODUCT_SUFFIXES
            and p.relative_to(root).parts[:2] != ("tests", "opsx")
        )
    for rel in _QUALITY_PRODUCT_CONFIGS:
        path = resource_path(root, rel)
        if path.is_file():
            paths.add(path)
    return paths


class QualityGateDurablePass:
    """Единая точка: ключ кэша, чтение и запись durable PASS для quality gates."""

    @staticmethod
    def cache_key(
        state: GraphState,
        *,
        root: Path,
        commands: dict[str, list[str]],
        timeout_seconds: float | None,
    ) -> tuple[str, str] | None:
        """Пара (product_digest, verifier_digest) или None без run_uuid."""
        if not str(state.get("run_uuid") or "").strip():
            return None
        paths = collect_quality_gate_product_paths(root)
        if not paths:
            return None
        product_digest = content_digest(root, paths)
        policy_files = {resource_path(root, p) for p in QUALITY_VERIFIER_POLICY_FILES}
        if any(not p.is_file() for p in policy_files):
            raise ValueError("QUALITY_VERIFIER_CONTRACT_UNBOUND")
        policy_digest = content_digest(root, policy_files)
        env_id = resolve_quality_gate_environment_id(state)
        identity = {
            "contract": QUALITY_GATES_CONTRACT,
            "verifier_policy_digest": policy_digest,
            "commands": commands,
            "timeout_seconds": timeout_seconds,
            "environment": env_id,
            "runtime": [
                sys.version,
                sys.prefix,
                platform.platform(),
                _relevant_env_snapshot(),
            ],
        }
        verifier_digest = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        return product_digest, verifier_digest

    @staticmethod
    def product_paths(root: Path) -> set[Path]:
        return collect_quality_gate_product_paths(root)

    @staticmethod
    def read_cached_pass(
        state: GraphState,
        *,
        root: Path,
        change_id: str,
        commands: dict[str, list[str]],
        timeout_seconds: float | None,
        gates_are_strict_pass: Any,
    ) -> dict[str, Any] | None:
        """Вернуть patch с ``REUSED_PASS`` или None."""
        try:
            cache_key = QualityGateDurablePass.cache_key(
                state, root=root, commands=commands, timeout_seconds=timeout_seconds,
            )
            if cache_key is None:
                _LOG.info(
                    "quality_gates_receipt miss reason=no_cache_key change_id=%s",
                    change_id,
                )
                return None
            input_digest, verifier_digest = cache_key
            stored = read_pass(
                root,
                change_id=change_id,
                check=QUALITY_GATES_CHECK,
                contract=QUALITY_GATES_CONTRACT,
                input_digest=input_digest,
                verifier_contract_digest=verifier_digest,
                pass_verdict=QUALITY_GATES_PASS_VERDICT,
                input_paths=QualityGateDurablePass.product_paths(root),
            )
            if stored is None:
                _LOG.info(
                    "quality_gates_receipt miss reason=no_stored_pass "
                    "change_id=%s product_digest=%s…",
                    change_id,
                    input_digest[:12],
                )
                return None
            if not gates_are_strict_pass(stored["result"]):
                _LOG.info(
                    "quality_gates_receipt miss reason=stored_not_strict_pass change_id=%s",
                    change_id,
                )
                return None
            _LOG.info(
                "quality_gates_receipt hit REUSED_PASS change_id=%s "
                "origin_execution_id=%s",
                change_id,
                stored.get("origin_execution_id"),
            )
            return {
                **stored["result"],
                "quality_gates_execution_status": "REUSED_PASS",
                "quality_gate_receipt": {
                    "source": "durable_pass",
                    "current_execution_id": str(state["run_uuid"]),
                    "origin_execution_id": stored["origin_execution_id"],
                    "evidence_id": stored["evidence_id"],
                    "input_digest": input_digest,
                    "verifier_contract_digest": verifier_digest,
                    "environment_id": resolve_quality_gate_environment_id(state),
                },
            }
        except (ValueError, TypeError, OSError) as exc:
            _LOG.info(
                "quality_gates_receipt miss reason=%s change_id=%s",
                type(exc).__name__,
                change_id,
            )
            return None

    @staticmethod
    def save_pass_if_eligible(
        state: GraphState,
        *,
        root: Path,
        change_id: str,
        commands: dict[str, list[str]],
        timeout_seconds: float | None,
        patch: dict[str, Any],
        cache_key: tuple[str, str] | None,
        gates_are_strict_pass: Any,
    ) -> bool:
        """Записать receipt при строгом PASS и стабильном cache_key до/после прогона."""
        if cache_key is None or not gates_are_strict_pass(patch):
            return False
        try:
            after_key = QualityGateDurablePass.cache_key(
                state,
                root=root,
                commands=commands,
                timeout_seconds=timeout_seconds,
            )
        except (OSError, ValueError, TypeError):
            after_key = None
        if after_key != cache_key:
            return False
        input_digest, verifier_digest = cache_key
        receipt_payload = slim_quality_gate_pass_for_receipt(patch)
        saved = best_effort_write_pass(
            root=root,
            change_id=change_id,
            check=QUALITY_GATES_CHECK,
            contract=QUALITY_GATES_CONTRACT,
            input_digest=input_digest,
            verifier_contract_digest=verifier_digest,
            pass_verdict=QUALITY_GATES_PASS_VERDICT,
            origin_execution_id=str(state["run_uuid"]),
            evidence_id=hashlib.sha256(
                json.dumps(
                    receipt_payload.get("quality_gate_raw"),
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest(),
            result=receipt_payload,
        )
        if saved:
            _LOG.info(
                "quality_gates_receipt saved change_id=%s execution_id=%s",
                change_id,
                state.get("run_uuid"),
            )
        return saved