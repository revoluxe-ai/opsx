"""Canonical OPSX run-store paths for Temporal POC writers.

Durable workflow state lives under `opsx/runs/temporal/**`.
Scratch locks, logs, and preflight artifacts stay under `.tmp/temporal/**`.
"""
from __future__ import annotations

from pathlib import Path


def _repo_root(repo_root: str | Path | None = None) -> Path:
    if repo_root is None:
        return Path(__file__).resolve().parents[2]
    return Path(repo_root).resolve()


def durable_runs_dir(repo_root: str | Path | None = None) -> Path:
    """Durable Temporal run records: `opsx/runs/temporal/runs`."""
    return _repo_root(repo_root) / "opsx" / "runs" / "temporal" / "runs"


def durable_gate_dir(repo_root: str | Path | None = None) -> Path:
    """Durable Temporal gate ack dirs: `opsx/runs/temporal/gates`."""
    return _repo_root(repo_root) / "opsx" / "runs" / "temporal" / "gates"


def durable_config_path(repo_root: str | Path | None = None) -> Path:
    """Durable Temporal overlay config: `opsx/runs/temporal/config/agent-models.overlay.json`."""
    return _repo_root(repo_root) / "opsx" / "runs" / "temporal" / "config" / "agent-models.overlay.json"


def scratch_temporal_dir(repo_root: str | Path | None = None) -> Path:
    """Ephemeral Temporal scratch: `.tmp/temporal` (locks, logs, preflight)."""
    return _repo_root(repo_root) / ".tmp" / "temporal"
