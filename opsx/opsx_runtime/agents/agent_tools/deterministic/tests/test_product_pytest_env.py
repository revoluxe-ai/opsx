"""Product ``run_pytest`` не наследует venv хоста OPSX (Poetry prefer-active)."""

from __future__ import annotations

import os
from pathlib import Path

from agents.agent_tools.deterministic import pytest_tool


def _touch_venv_python(venv: Path) -> Path:
    if os.name == "nt":
        path = venv / "Scripts" / "python.exe"
    else:
        path = venv / "bin" / "python"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def test_product_pytest_env_drops_host_virtualenv(monkeypatch) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", r"C:\opsx-cli-host-py3.11")
    monkeypatch.setenv("POETRY_ACTIVE", "1")
    monkeypatch.setenv("VIRTUAL_ENV_PROMPT", "opsx-cli")
    env = pytest_tool.product_pytest_subprocess_env()
    assert "VIRTUAL_ENV" not in env
    assert "POETRY_ACTIVE" not in env
    assert "VIRTUAL_ENV_PROMPT" not in env
    assert env.get("POETRY_VIRTUALENVS_PREFER_ACTIVE") == "false"
    assert env.get("POETRY_VIRTUALENVS_PREFER_ACTIVE_PYTHON") == "false"
    assert env.get("PYTHONUTF8") == "1"


def test_resolve_product_python_rejects_host_runtime_venv(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / "opsx-cli-host"
    host.mkdir()
    monkeypatch.setenv("VIRTUAL_ENV", str(host))

    def fake_run(cmd, **kwargs):
        assert kwargs.get("base_env") is not None
        assert "VIRTUAL_ENV" not in kwargs["base_env"]
        assert kwargs["base_env"].get("POETRY_VIRTUALENVS_PREFER_ACTIVE") == "false"
        assert cmd[-3:] in (["env", "info", "-p"], ["env", "list", "--full-path"])

        class Completed:
            returncode = 0
            stdout = str(host) + "\n"
            stderr = ""

        return Completed()

    monkeypatch.setattr(pytest_tool, "run_subprocess_utf8", fake_run)
    resolved = pytest_tool.resolve_product_pytest_python(
        tmp_path,
        pytest_tool.product_pytest_subprocess_env(),
    )
    assert resolved is None


def test_resolve_product_python_uses_repository_venv(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "adv-bot-venv"
    python = _touch_venv_python(project)
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "opsx-cli-host"))

    def fake_run(cmd, **kwargs):
        del cmd, kwargs

        class Completed:
            returncode = 0
            stdout = str(project)
            stderr = ""

        return Completed()

    monkeypatch.setattr(pytest_tool, "run_subprocess_utf8", fake_run)
    resolved = pytest_tool.resolve_product_pytest_python(
        tmp_path,
        pytest_tool.product_pytest_subprocess_env(),
    )
    assert resolved == python


def test_run_pytest_sync_uses_repository_python_not_host_venv(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = tmp_path / "tests" / "test_slice.py"
    module.parent.mkdir(parents=True)
    module.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    project_python = _touch_venv_python(tmp_path / "project-venv")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "opsx-cli-host"))
    monkeypatch.setattr(
        pytest_tool,
        "resolve_product_pytest_python",
        lambda *_args, **_kwargs: project_python,
    )
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["base_env"] = kwargs.get("base_env")
        captured["cwd"] = kwargs.get("cwd")

        class Completed:
            returncode = 0
            stdout = "1 passed"
            stderr = ""

        return Completed()

    monkeypatch.setattr(pytest_tool, "run_subprocess_utf8", fake_run)
    result = pytest_tool.run_repair_hop_pytest_sync(tmp_path, "tests/test_slice.py")
    assert result["ok"] is True
    assert result["exit_code"] == 0
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[0] == str(project_python)
    assert cmd[1:4] == ["-m", "pytest", "tests/test_slice.py"]
    base_env = captured["base_env"]
    assert isinstance(base_env, dict)
    assert "VIRTUAL_ENV" not in base_env
    assert captured["cwd"] == tmp_path.resolve()


def test_run_pytest_sync_poetry_fallback_strips_host_venv(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = tmp_path / "tests" / "test_slice.py"
    module.parent.mkdir(parents=True)
    module.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "opsx-cli-host"))
    monkeypatch.setattr(pytest_tool, "resolve_product_pytest_python", lambda *_a, **_k: None)
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["base_env"] = kwargs.get("base_env")

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return Completed()

    monkeypatch.setattr(pytest_tool, "run_subprocess_utf8", fake_run)
    result = pytest_tool.run_repair_hop_pytest_sync(tmp_path, "tests/test_slice.py")
    assert result["exit_code"] == 0
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert "run" in cmd
    assert "pytest" in cmd
    base_env = captured["base_env"]
    assert isinstance(base_env, dict)
    assert "VIRTUAL_ENV" not in base_env
    assert "POETRY_ACTIVE" not in base_env
    assert base_env.get("POETRY_VIRTUALENVS_PREFER_ACTIVE") == "false"


def test_product_venv_stays_selectable_when_panel_runs_inside_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Панель в ``repo/.venv`` не должна отбрасывать product interpreter."""
    repo = tmp_path / "adv-bot"
    repo.mkdir()
    product_python = _touch_venv_python(repo / ".venv")
    opsx_host = tmp_path / "opsx-cli-host"
    opsx_host.mkdir()
    monkeypatch.setenv("VIRTUAL_ENV", str(repo / ".venv"))

    def fake_run(cmd, **kwargs):
        del kwargs
        tail = tuple(cmd[-3:])

        class Completed:
            returncode = 0
            stdout = str(repo / ".venv")
            stderr = ""

        if tail == ("env", "info", "-p"):
            return Completed()
        if tail == ("env", "list", "--full-path"):
            completed = Completed()
            completed.stdout = f"{repo / '.venv'} (Activated)\n"
            return completed
        raise AssertionError(cmd)

    monkeypatch.setattr(pytest_tool, "run_subprocess_utf8", fake_run)
    resolved = pytest_tool.resolve_product_pytest_python(
        repo,
        pytest_tool.product_pytest_subprocess_env(),
    )
    assert resolved == product_python


def test_resolved_python_never_under_opsx_cli_cache(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "adv-bot"
    repo.mkdir()
    product_python = _touch_venv_python(repo / ".venv")
    opsx_cli = tmp_path / "pypoetry" / "Cache" / "virtualenvs" / "opsx-cli-abc-py3.11"
    monkeypatch.setenv("VIRTUAL_ENV", str(opsx_cli))

    def fake_run(cmd, **kwargs):
        del kwargs
        tail = tuple(cmd[-3:])

        class Completed:
            returncode = 0
            stdout = str(opsx_cli)
            stderr = ""

        if tail == ("env", "info", "-p"):
            return Completed()
        if tail == ("env", "list", "--full-path"):
            listed = Completed()
            listed.stdout = (
                f"{opsx_cli} (Activated)\n{repo / '.venv'}\n"
            )
            return listed
        raise AssertionError(cmd)

    monkeypatch.setattr(pytest_tool, "run_subprocess_utf8", fake_run)
    resolved = pytest_tool.resolve_product_pytest_python(
        repo,
        pytest_tool.product_pytest_subprocess_env(),
    )
    assert resolved == product_python
    assert "opsx-cli" not in str(resolved).casefold()


def test_run_pytest_sync_uses_poetry_env_list_when_info_returns_process_prefix(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """``env info -p`` == sys.prefix и пустой VIRTUAL_ENV не выбирают opsx-cli."""
    module = tmp_path / "tests" / "test_slice.py"
    module.parent.mkdir(parents=True)
    module.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    host = tmp_path / "opsx-cli-host"
    host.mkdir()
    project_python = _touch_venv_python(tmp_path / "poetry-cache-venv")
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(pytest_tool.sys, "prefix", str(host))
    monkeypatch.setattr(pytest_tool.sys, "base_prefix", str(tmp_path / "base-python"))
    monkeypatch.setattr(
        pytest_tool.sys,
        "executable",
        str(host / "Scripts" / "python.exe"),
    )
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        tail = tuple(cmd[-3:])
        if tail == ("env", "info", "-p"):

            class Info:
                returncode = 0
                stdout = str(host)
                stderr = ""

            return Info()
        if tail == ("env", "list", "--full-path"):

            class Listed:
                returncode = 0
                stdout = f"{tmp_path / 'poetry-cache-venv'} (Activated)\n"
                stderr = ""

            return Listed()
        captured["cmd"] = list(cmd)
        captured["base_env"] = kwargs.get("base_env")

        class Completed:
            returncode = 1
            stdout = ""
            stderr = "1 failed"

        return Completed()

    monkeypatch.setattr(pytest_tool, "run_subprocess_utf8", fake_run)
    result = pytest_tool.run_repair_hop_pytest_sync(tmp_path, "tests/test_slice.py")
    assert result["exit_code"] == 1
    assert result["ok"] is False
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[0] == str(project_python)
    assert cmd[1:4] == ["-m", "pytest", "tests/test_slice.py"]
    base_env = captured["base_env"]
    assert isinstance(base_env, dict)
    assert "VIRTUAL_ENV" not in base_env
    assert base_env.get("POETRY_VIRTUALENVS_PREFER_ACTIVE") == "false"
