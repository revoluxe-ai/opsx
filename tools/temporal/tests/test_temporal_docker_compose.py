"""Тесты cloud/sudo fallback для docker compose в tempo_operator."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tempo_operator import (
    _cursor_cloud_agent_runtime,
    _docker_compose_file_args,
    _docker_permission_denied,
    _run_docker_compose_up,
    _temporal_cloud_compose_enabled,
)


def test_temporal_cloud_compose_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tempo_operator._cursor_cloud_agent_runtime", lambda: False)
    monkeypatch.delenv("STROY_TEMPORAL_CLOUD_COMPOSE", raising=False)
    monkeypatch.delenv("CURSOR_AGENT", raising=False)
    monkeypatch.delenv("CURSOR_CLOUD", raising=False)
    assert _temporal_cloud_compose_enabled() is False
    monkeypatch.setenv("STROY_TEMPORAL_CLOUD_COMPOSE", "1")
    assert _temporal_cloud_compose_enabled() is True


def test_temporal_cloud_compose_auto_on_cursor_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STROY_TEMPORAL_CLOUD_COMPOSE", raising=False)
    monkeypatch.setattr("tempo_operator._cursor_cloud_agent_runtime", lambda: True)
    assert _temporal_cloud_compose_enabled() is True


def test_temporal_cloud_compose_explicit_off_on_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tempo_operator._cursor_cloud_agent_runtime", lambda: True)
    monkeypatch.setenv("STROY_TEMPORAL_CLOUD_COMPOSE", "0")
    assert _temporal_cloud_compose_enabled() is False


def test_cursor_cloud_agent_runtime_cursor_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CURSOR_CLOUD", raising=False)
    monkeypatch.setenv("CURSOR_AGENT", "1")
    assert _cursor_cloud_agent_runtime() is True


def test_docker_compose_file_args_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tempo_operator._cursor_cloud_agent_runtime", lambda: False)
    monkeypatch.delenv("STROY_TEMPORAL_CLOUD_COMPOSE", raising=False)
    monkeypatch.delenv("CURSOR_AGENT", raising=False)
    monkeypatch.setattr(
        "tempo_operator._cursor_cloud_agent_runtime",
        lambda: False,
    )
    assert _docker_compose_file_args() == ["-f", "docker-compose.yml"]


def test_docker_compose_file_args_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STROY_TEMPORAL_CLOUD_COMPOSE", "1")
    args = _docker_compose_file_args()
    assert args == ["-f", "docker-compose.yml", "-f", "docker-compose.cloud.yml"]


def test_docker_permission_denied_detects_sock_error() -> None:
    result = subprocess.CompletedProcess(
        args=["docker"],
        returncode=1,
        stdout="",
        stderr='permission denied while trying to connect to the Docker daemon socket',
    )
    assert _docker_permission_denied(result) is True


def test_run_docker_compose_up_retries_sudo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))
        if cmd[0] == "docker":
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="permission denied",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("tempo_operator.subprocess.run", fake_run)
    monkeypatch.setattr("tempo_operator.HERE", tmp_path)
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "docker-compose.cloud.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setenv("STROY_TEMPORAL_CLOUD_COMPOSE", "1")

    assert _run_docker_compose_up() is True
    assert calls[0][0] == "docker"
    assert calls[1][:3] == ["sudo", "-n", "docker"]
    assert "docker-compose.cloud.yml" in calls[1]
