"""Детерминированный MCP ``run_pytest``: один node id под ``tests/``, без shell у модели."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents.adapters.agent_capability_options import AgentCustomToolSpec
from workflows.implementation.node_subprocess import (
    run_subprocess_utf8,
    subprocess_utf8_env,
)

RUN_PYTEST_TOOL_NAME = "run_pytest"
_OUTPUT_TAIL_CHARS = 3_500
_DEFAULT_TIMEOUT_SECONDS = 600.0
_SHELL_METACHAR_RE = re.compile(r"[;&|`$<>\r\n\0\\\"]")
# Poetry ``virtualenvs.prefer-active`` запускает pytest в уже активированном
# venv хоста (opsx-cli / LangGraph worker). У продукта там нет pytest.
# Снятие только ``VIRTUAL_ENV`` недостаточно: poetry.exe хоста и PATH/Scripts
# снова выбирают тот же интерпретатор. Флаг ниже перекрывает config.
_HOST_VENV_ENV_KEYS = (
    "VIRTUAL_ENV",
    "VIRTUAL_ENV_PROMPT",
    "POETRY_ACTIVE",
    "_OLD_VIRTUAL_PATH",
    "_OLD_VIRTUAL_PROMPT",
    "_OLD_VIRTUAL_PYTHONHOME",
)
_HOST_VENV_ENV_KEYS_UPPER = frozenset(key.upper() for key in _HOST_VENV_ENV_KEYS)
_REPO_VENV_DIR_NAMES = (".venv", ".venv_win", ".venv-ae")


def _resolve_unique_tests_basename(repo_root: Path, basename: str) -> str | None:
    """Единственный ``tests/**/<basename>`` в репо или ``None`` при неоднозначности."""
    name = basename.strip().replace("\\", "/")
    if not name or "/" in name or ".." in name or not name.endswith(".py"):
        return None
    tests_root = repo_root.resolve() / "tests"
    if not tests_root.is_dir():
        return None
    hits: list[str] = []
    for path in tests_root.rglob(name):
        if path.is_file() and path.name == name:
            rel = path.relative_to(repo_root.resolve()).as_posix()
            hits.append(rel)
    unique = list(dict.fromkeys(hits))
    if len(unique) == 1:
        return unique[0]
    return None


def validate_repair_hop_pytest_node_id(
    node_id: str,
    *,
    repo_root: Path | None = None,
) -> str:
    """Проверить и нормализовать pytest target под ``tests/`` (node id или файл).

    Bare basename (например ``test_foo.py``) резолвится в единственный путь под
    ``tests/``; shell-команды для pytest запрещены (§27.0.1).

    Raises:
        ValueError: пустой id, shell-метасимволы, вне ``tests/``, неоднозначный basename.
    """
    raw = str(node_id or "").strip()
    if not raw:
        msg = "pytest node_id must not be empty"
        raise ValueError(msg)
    if _SHELL_METACHAR_RE.search(raw):
        msg = "pytest node_id contains forbidden shell metacharacters"
        raise ValueError(msg)
    if any(sep in raw for sep in (" ", ",", "\t")):
        msg = "pytest node_id must be a single node id"
        raise ValueError(msg)
    normalized = raw.replace("\\", "/")
    if not normalized.startswith("tests/"):
        if repo_root is not None and "/" not in normalized and normalized.endswith(".py"):
            resolved = _resolve_unique_tests_basename(repo_root, normalized)
            if resolved is None:
                msg = (
                    "pytest node_id must start with tests/ "
                    "or be a unique test module basename under tests/"
                )
                raise ValueError(msg)
            normalized = resolved
        else:
            msg = "pytest node_id must start with tests/"
            raise ValueError(msg)
    file_part = normalized.split("::", 1)[0]
    if "/.." in file_part or file_part.startswith(".."):
        msg = "pytest node_id path must not traverse parent directories"
        raise ValueError(msg)
    if not file_part.endswith(".py"):
        msg = f"pytest node_id invalid file part: {file_part!r}"
        raise ValueError(msg)
    if "::" in normalized:
        file_part, test_part = normalized.split("::", 1)
        if not test_part.strip():
            msg = f"pytest node_id invalid shape: {normalized!r}"
            raise ValueError(msg)
    return normalized


def _resolve_poetry(repo_root: Path) -> list[str]:
    del repo_root
    poetry = shutil.which("poetry")
    if poetry:
        return [poetry]
    return ["python", "-m", "poetry"]


def _norm_path_key(value: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(value)))


def host_runtime_prefixes() -> list[str]:
    """Корни интерпретатора процесса OPSX, а не Poetry-env продукта.

    ``VIRTUAL_ENV`` часто пуст: супервизор запускает ``python.exe`` из venv
    напрямую. Poetry ``env info -p`` тогда возвращает ``sys.prefix`` этого
    процесса (opsx-cli), где нет product pytest.
    """
    raw: list[str] = []
    virtual = os.environ.get("VIRTUAL_ENV")
    if virtual and str(virtual).strip():
        raw.append(str(virtual))
    prefix = str(sys.prefix or "")
    base = str(getattr(sys, "base_prefix", "") or prefix)
    if prefix and _norm_path_key(prefix) != _norm_path_key(base):
        raw.append(prefix)
    executable = Path(sys.executable)
    if executable.parent.name.lower() in {"scripts", "bin"}:
        raw.append(str(executable.parent.parent))
    deduped: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = _norm_path_key(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _repo_venv_roots(repo_root: Path) -> list[Path]:
    """Корни локальных venv продукта в репозитории (не opsx-cli / panel host)."""
    root = repo_root.resolve()
    return [root / name for name in _REPO_VENV_DIR_NAMES]


def _under_repo_product_venv(path: str | Path, repo_root: Path) -> bool:
    """True, если путь лежит в ``.venv*`` корня репозитория продукта."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        resolved = Path(path)
    for venv_root in _repo_venv_roots(repo_root):
        try:
            venv_resolved = venv_root.resolve()
        except OSError:
            venv_resolved = venv_root
        if _same_dir(venv_resolved, resolved):
            return True
        try:
            if resolved.is_relative_to(venv_resolved):  # py3.11+
                return True
        except (OSError, ValueError):
            normalized = _norm_path_key(resolved)
            prefix = _norm_path_key(venv_resolved)
            if normalized.startswith(prefix + os.sep) or normalized.startswith(prefix + "\\"):
                return True
    return False


def _is_host_runtime(
    path: str | Path,
    hosts: list[str],
    *,
    repo_root: Path | None = None,
) -> bool:
    """OPSX host venv (opsx-cli/panel), never the product repo ``.venv``."""
    if repo_root is not None and _under_repo_product_venv(path, repo_root):
        return False
    for host in hosts:
        if _same_dir(host, Path(path)):
            return True
    return False


def _environ_key(env: Mapping[str, str], name: str) -> str | None:
    """Ключ окружения без учёта регистра (Windows ``Path`` / ``PATH``)."""
    folded = name.casefold()
    for key in env:
        if key.casefold() == folded:
            return key
    return None


def _strip_host_venv_from_path(env: dict[str, str], host_venv: str | None) -> None:
    """Убрать ``Scripts``/``bin`` venv хоста из PATH дочернего pytest."""
    if not host_venv or not str(host_venv).strip():
        return
    bin_name = "Scripts" if os.name == "nt" else "bin"
    try:
        host_bin = os.path.normcase(os.path.normpath(str((Path(host_venv) / bin_name).resolve())))
    except OSError:
        host_bin = os.path.normcase(os.path.normpath(str(Path(host_venv) / bin_name)))
    path_key = _environ_key(env, "PATH") or "PATH"
    kept: list[str] = []
    for part in str(env.get(path_key) or "").split(os.pathsep):
        if not part:
            continue
        try:
            normalized = os.path.normcase(os.path.normpath(str(Path(part).resolve())))
        except OSError:
            normalized = os.path.normcase(os.path.normpath(part))
        if normalized == host_bin:
            continue
        kept.append(part)
    env[path_key] = os.pathsep.join(kept)


def product_pytest_subprocess_env(
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Окружение product pytest без активного venv хоста.

    RED фиксирует команды ``poetry run pytest`` в корне репозитория. Тот же
    hop-tool обязан выбрать Poetry-env проекта, а не venv процесса OPSX.
    ``POETRY_VIRTUALENVS_PREFER_ACTIVE=false`` обязателен: иначе poetry,
    установленный в venv OPSX, снова исполняет pytest там, где pytest нет.
    """
    env = subprocess_utf8_env(base=base)
    hosts = list(host_runtime_prefixes())
    host_key = _environ_key(env, "VIRTUAL_ENV")
    if host_key and str(env.get(host_key) or "").strip():
        hosts.append(str(env[host_key]))
    for key in list(env):
        if key.upper() in _HOST_VENV_ENV_KEYS_UPPER:
            env.pop(key, None)
    # Poetry читает ``virtualenvs.prefer-active-python`` как
    # POETRY_VIRTUALENVS_PREFER_ACTIVE_PYTHON. Короткое имя без _PYTHON
    # процесс игнорирует, и ``poetry run`` остаётся в venv хоста.
    env["POETRY_VIRTUALENVS_PREFER_ACTIVE"] = "false"
    env["POETRY_VIRTUALENVS_PREFER_ACTIVE_PYTHON"] = "false"
    seen: set[str] = set()
    for host in hosts:
        key = _norm_path_key(host)
        if key in seen:
            continue
        seen.add(key)
        _strip_host_venv_from_path(env, host)
    return env


def frozen_command_subprocess_env(repo_root: Path) -> dict[str, str]:
    """Окружение Node-прогона frozen ``poetry run pytest``.

    В env пишется ``OPSX_PYTEST_PYTHON`` — интерпретатор Poetry-проекта.
    Иначе CLI, запущенный из opsx-cli, снова исполняет pytest в venv хоста,
    где пакета pytest нет. ``VIRTUAL_ENV`` хоста в этот env не входит.
    """
    env = product_pytest_subprocess_env()
    project_python = resolve_product_pytest_python(repo_root, env)
    if project_python is None:
        project_python = resolve_repository_venv_python(
            repo_root,
            host_venv=os.environ.get("VIRTUAL_ENV"),
        )
    if project_python is not None:
        env["OPSX_PYTEST_PYTHON"] = str(project_python)
    return env


def _venv_python(venv_root: Path) -> Path | None:
    if os.name == "nt":
        candidate = venv_root / "Scripts" / "python.exe"
    else:
        candidate = venv_root / "bin" / "python"
    if candidate.is_file():
        return candidate
    return None


def _same_dir(left: str | None, right: Path) -> bool:
    if not left or not str(left).strip():
        return False
    try:
        return Path(left).resolve() == right.resolve()
    except OSError:
        return os.path.normcase(os.path.normpath(left)) == os.path.normcase(
            os.path.normpath(str(right))
        )


def resolve_repository_venv_python(
    repo_root: Path,
    *,
    host_venv: str | None = None,
) -> Path | None:
    """Интерпретатор локального venv репозитория, не venv процесса OPSX.

    Используется, когда ``poetry env info -p`` вернул venv хоста или ничего:
    ``poetry run`` в этом случае снова попадает в opsx-cli без pytest.
    """
    hosts = list(host_runtime_prefixes())
    if host_venv and str(host_venv).strip():
        hosts.append(str(host_venv))
    root = repo_root.resolve()
    for name in _REPO_VENV_DIR_NAMES:
        venv_root = root / name
        if _is_host_runtime(venv_root, hosts, repo_root=root):
            continue
        python = _venv_python(venv_root)
        if python is not None and not _python_is_host(python, hosts, repo_root=root):
            return python
    return None


def _poetry_stdout_path(stdout: str) -> Path | None:
    lines = [line.strip().strip('"') for line in (stdout or "").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return None
    return Path(lines[-1])


def _looks_like_path(text: str) -> bool:
    if "/" in text or "\\" in text:
        return True
    return len(text) > 2 and text[1] == ":"


def _iter_poetry_env_list(stdout: str) -> list[tuple[Path, bool]]:
    """Разобрать ``poetry env list --full-path`` (путь и маркер Activated)."""
    found: list[tuple[Path, bool]] = []
    for raw in (stdout or "").splitlines():
        text = raw.strip().strip('"')
        if not text or text.lower().startswith("no virtualenv"):
            continue
        activated = "(activated)" in text.casefold()
        path_text = text.split(" (", 1)[0].strip()
        if not _looks_like_path(path_text):
            continue
        found.append((Path(path_text), activated))
    return found


def _python_is_host(
    python: Path,
    hosts: list[str],
    *,
    repo_root: Path | None = None,
) -> bool:
    parent = python.parent
    root = parent.parent if parent.name.lower() in {"scripts", "bin"} else parent
    return _is_host_runtime(root, hosts, repo_root=repo_root) or _is_host_runtime(
        python,
        hosts,
        repo_root=repo_root,
    )


def _python_from_venv_root(
    root: Path,
    hosts: list[str],
    *,
    repo_root: Path | None = None,
) -> Path | None:
    if _is_host_runtime(root, hosts, repo_root=repo_root):
        return None
    python = _venv_python(root)
    if python is None or _python_is_host(python, hosts, repo_root=repo_root):
        return None
    return python


def resolve_product_pytest_python(
    repo_root: Path,
    env: Mapping[str, str],
) -> Path | None:
    """Интерпретатор Poetry-env репозитория, никогда не venv процесса OPSX.

    ``poetry env info -p`` при запущенном из venv процессе часто печатает
    ``sys.prefix`` хоста, даже если ``VIRTUAL_ENV`` пуст. Тогда берём первый
    не-хостовый env из ``poetry env list --full-path`` (кэш Poetry, не только
    ``.venv`` в корне репозитория).
    """
    root = repo_root.resolve()
    hosts = host_runtime_prefixes()
    prefix = _resolve_poetry(repo_root)
    info = run_subprocess_utf8(
        [*prefix, "env", "info", "-p"],
        cwd=repo_root,
        timeout=60,
        base_env=dict(env),
    )
    if int(info.returncode) == 0:
        info_root = _poetry_stdout_path(info.stdout or "")
        if info_root is not None:
            python = _python_from_venv_root(info_root, hosts, repo_root=root)
            if python is not None:
                return python
    listed = run_subprocess_utf8(
        [*prefix, "env", "list", "--full-path"],
        cwd=repo_root,
        timeout=60,
        base_env=dict(env),
    )
    if int(listed.returncode) != 0:
        return None
    candidates = [
        (activated, python)
        for env_root, activated in _iter_poetry_env_list(listed.stdout or "")
        if (python := _python_from_venv_root(env_root, hosts, repo_root=root))
        is not None
    ]
    activated_ones = [python for activated, python in candidates if activated]
    if activated_ones:
        return activated_ones[0]
    if candidates:
        return candidates[0][1]
    return None


def build_repair_hop_pytest_argv(repo_root: Path, node_id: str) -> list[str]:
    """Собрать argv subprocess для одного product pytest node id."""
    repo_root = repo_root.resolve()
    normalized = validate_repair_hop_pytest_node_id(node_id, repo_root=repo_root)
    prefix = _resolve_poetry(repo_root)
    return [
        *prefix,
        "run",
        "python",
        "-m",
        "pytest",
        normalized,
        "-q",
        "--tb=short",
    ]


def _tail_output(stdout: str, stderr: str, *, limit: int = _OUTPUT_TAIL_CHARS) -> str:
    merged = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
    if len(merged) <= limit:
        return merged
    return f"… [truncated; showing last {limit} chars]\n{merged[-limit:]}"


def run_repair_hop_pytest_sync(
    repo_root: Path,
    node_id: str,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Выполнить pytest для одного node id; вернуть exit code и хвост вывода."""
    repo_root = repo_root.resolve()
    normalized = validate_repair_hop_pytest_node_id(node_id, repo_root=repo_root)
    child_env = product_pytest_subprocess_env()
    project_python = resolve_product_pytest_python(repo_root, child_env)
    if project_python is None:
        project_python = resolve_repository_venv_python(
            repo_root,
            host_venv=os.environ.get("VIRTUAL_ENV"),
        )
    if project_python is not None:
        cmd = [
            str(project_python),
            "-m",
            "pytest",
            normalized,
            "-q",
            "--tb=short",
        ]
    else:
        cmd = build_repair_hop_pytest_argv(repo_root, normalized)
    completed = run_subprocess_utf8(
        cmd,
        cwd=repo_root,
        timeout=timeout_seconds,
        base_env=child_env,
    )
    tail = _tail_output(completed.stdout or "", completed.stderr or "")
    exit_code = int(completed.returncode)
    interpreter = str(cmd[0]) if cmd else ""
    return {
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "node_id": normalized,
        "python_executable": interpreter,
        "tail": tail,
        "timed_out": False,
    }


def _mcp_payload(**fields: Any) -> dict[str, Any]:
    text = json.dumps(fields, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        **fields,
    }


class RepairHopRunPytestRuntime:
    """Исполнитель custom MCP ``run_pytest`` для repair hops."""

    def __init__(self, *, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve()

    async def run_pytest(self, args: Mapping[str, Any], _ctx: Any) -> dict[str, Any]:
        """Запустить pytest по одному ``node_id`` из схемы tool."""
        if not isinstance(args, Mapping):
            return _mcp_payload(ok=False, code="INVALID_ARGS")
        node_raw = args.get("node_id")
        if node_raw is None:
            return _mcp_payload(ok=False, code="INVALID_ARGS", field="node_id")
        try:
            normalized = validate_repair_hop_pytest_node_id(
                str(node_raw),
                repo_root=self._repo_root,
            )
        except ValueError as exc:
            return _mcp_payload(ok=False, code="INVALID_NODE_ID", error=str(exc))
        try:
            result = await asyncio.to_thread(
                run_repair_hop_pytest_sync,
                self._repo_root,
                normalized,
            )
        except Exception as exc:
            return _mcp_payload(
                ok=False,
                code="PYTEST_RUN_FAILED",
                node_id=normalized,
                error=f"{type(exc).__name__}: {exc}",
            )
        return _mcp_payload(**result)


_RUN_PYTEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["node_id"],
    "properties": {
        "node_id": {
            "type": "string",
            "minLength": 1,
            "description": "Single pytest node id under tests/, e.g. tests/unit/foo.py::test_bar",
        },
    },
}


def build_run_pytest_custom_tool(*, repo_root: Path) -> dict[str, AgentCustomToolSpec]:
    """Вернуть map с одним custom tool ``run_pytest``."""
    runtime = RepairHopRunPytestRuntime(repo_root=repo_root)
    return {
        RUN_PYTEST_TOOL_NAME: AgentCustomToolSpec(
            description=(
                "Run pytest for exactly one tests/ target (node id or test module path) "
                "with the repository Poetry environment, not the OPSX host virtualenv. "
                "Use node_id only — never shell or poetry commands."
            ),
            input_schema=_RUN_PYTEST_SCHEMA,
            execute=runtime.run_pytest,
        ),
    }