"""Интерпретатор subprocess worker (LangGraph): только Poetry env ``opsx/core``."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from .context import OpsxError, Project

_REPO_VENV_DIR_NAMES = (".venv", ".venv_win", ".venv-ae")
_LEGACY_MANAGED_PREFIX = "opsx/runs/environments/"
_LANGGRAPH_PROBE = (
    "from langgraph.checkpoint.memory import MemorySaver; "
    "MemorySaver  # noqa: F841"
)


def _norm_path_key(value: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(value)))


def is_repository_product_venv(project: Project, python_path: Path) -> bool:
    """True, если интерпретатор лежит в локальном venv продукта в корне репозитория."""
    try:
        resolved = python_path.resolve()
    except OSError:
        resolved = python_path
    root = project.root.resolve()
    for name in _REPO_VENV_DIR_NAMES:
        venv_root = root / name
        try:
            venv_resolved = venv_root.resolve()
        except OSError:
            venv_resolved = venv_root
        bin_name = "Scripts" if os.name == "nt" else "bin"
        expected = venv_resolved / bin_name / (
            "python.exe" if os.name == "nt" else "python"
        )
        if _norm_path_key(resolved) == _norm_path_key(expected):
            return True
        try:
            if resolved.is_relative_to(venv_resolved):
                return True
        except (OSError, ValueError):
            prefix = _norm_path_key(venv_resolved)
            normalized = _norm_path_key(resolved)
            if normalized.startswith(prefix + os.sep) or normalized.startswith(
                prefix + "\\"
            ):
                return True
    return False


def is_legacy_managed_runtime_pin(pin: str) -> bool:
    """Устаревший pip-venv под ``opsx/runs/environments`` — не использовать."""
    normalized = pin.replace("\\", "/").strip().lower()
    return normalized.startswith(_LEGACY_MANAGED_PREFIX)


def resolve_pinned_interpreter(project: Project, pin: str) -> Path | None:
    """Абсолютный или project-relative путь к закреплённому Python."""
    text = str(pin).strip()
    if not text or is_legacy_managed_runtime_pin(text):
        return None
    absolute = Path(text)
    if absolute.is_file():
        return absolute
    relative = project.root / text
    if relative.is_file():
        return relative
    return None


def interpreter_imports_langgraph(path: str) -> bool:
    """Проверка, что интерпретатор тянет LangGraph checkpoint API worker'а."""
    try:
        result = subprocess.run(
            [path, "-c", _LANGGRAPH_PROBE],
            capture_output=True,
            timeout=30,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def core_poetry_python(core_root: Path) -> Path | None:
    """Активный Poetry-env каталога ``opsx/core`` (opsx-cli), не продукта."""
    import shutil

    poetry = shutil.which("poetry")
    if not poetry:
        return None
    try:
        result = subprocess.run(
            [poetry, "env", "info", "-p"],
            cwd=core_root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    lines = [line.strip().strip('"') for line in (result.stdout or "").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return None
    root = Path(lines[-1])
    if os.name == "nt":
        candidate = root / "Scripts" / "python.exe"
    else:
        candidate = root / "bin" / "python"
    if candidate.is_file():
        return candidate
    return None


def worker_interpreter_candidates(project: Project, metadata: dict) -> list[str]:
    """Poetry ``opsx/core`` первым; product ``.venv`` и legacy managed — никогда."""
    seen: set[str] = set()
    ordered: list[str] = []

    def add(path: str | Path | None) -> None:
        if path is None:
            return
        text = str(path).strip()
        if not text:
            return
        key = _norm_path_key(text)
        if key in seen:
            return
        candidate = Path(text)
        if not candidate.is_file():
            return
        if is_repository_product_venv(project, candidate):
            return
        seen.add(key)
        ordered.append(text)

    if project.core.is_dir():
        add(core_poetry_python(project.core))

    runtime = metadata.get("runtime_python")
    if isinstance(runtime, str) and runtime.strip():
        pinned = resolve_pinned_interpreter(project, runtime.strip())
        if pinned is not None and not is_repository_product_venv(project, pinned):
            add(pinned)

    add(sys.executable)
    return ordered


def interpreter_imports_cursor_sdk(path: str) -> bool:
    """Проверка, что в Poetry worker установлен пакет ``cursor_sdk``."""
    try:
        result = subprocess.run(
            [path, "-c", "import cursor_sdk"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def poetry_cursor_extra_requested(project: Project, settings: dict) -> bool:
    """Нужен ли extra ``cursor`` (``cursor-sdk``) по ``opsx/project/project.json``."""
    from .settings import instance_layer

    layer = instance_layer(settings)
    adapter = layer.get("adapter") or {}
    extras = adapter.get("extras") or []
    return "cursor" in extras


def _resolve_python312_executable() -> Path | None:
    """Найти Python 3.12+ для extra ``cursor`` (``cursor-sdk`` маркер в pyproject)."""
    import shutil

    if shutil.which("py"):
        try:
            result = subprocess.run(
                ["py", "-3.12", "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            path = Path(result.stdout.strip())
            if path.is_file():
                return path
    for candidate in (
        Path(r"C:\Program Files\Python312\python.exe"),
        Path.home() / "AppData/Local/Programs/Python/Python312/python.exe",
    ):
        if candidate.is_file():
            return candidate
    return None


def resolve_worker_interpreter(project: Project, metadata: dict) -> str:
    """Первый кандидат с LangGraph; product pytest venv не рассматривается."""
    for path in worker_interpreter_candidates(project, metadata):
        if interpreter_imports_langgraph(path):
            return path
    raise OpsxError(
        "WORKER_RUNTIME_MISSING_LANGGRAPH",
        "OPSX worker Python must import langgraph. "
        "From opsx/core run: poetry install --extras runtime --extras web",
    )


async def ensure_core_poetry_runtime(project: Project) -> str:
    """``poetry install`` в core (extras из project.json) и путь Poetry-python."""
    from .process import run
    from .storage import read_json

    if not project.core.is_dir():
        raise OpsxError("CORE_MISSING", "opsx/core submodule is not present.")
    settings = await read_json(project.settings, {})
    extras = ["runtime", "web"]
    if poetry_cursor_extra_requested(project, settings):
        extras.append("cursor")
        py312 = await asyncio.to_thread(_resolve_python312_executable)
        if py312 is None:
            raise OpsxError(
                "CURSOR_PYTHON312_REQUIRED",
                "Adapter extra 'cursor' needs Python 3.12+. Install 3.12 and run "
                "poetry env use <python312> in opsx/core.",
            )
        await run(
            ["poetry", "env", "use", str(py312), "--no-ansi"],
            cwd=project.core,
            timeout=120,
        )
    argv = ["poetry", "install", "--no-ansi"]
    for extra in extras:
        argv.extend(["--extras", extra])
    await run(argv, cwd=project.core, timeout=900)
    python = core_poetry_python(project.core)
    if python is None:
        raise OpsxError(
            "POETRY_ENV_MISSING",
            "Poetry env for opsx/core was not found after poetry install.",
        )
    if not interpreter_imports_langgraph(str(python)):
        raise OpsxError(
            "WORKER_RUNTIME_MISSING_LANGGRAPH",
            "Poetry env in opsx/core still cannot import langgraph.",
        )
    if "cursor" in extras and not interpreter_imports_cursor_sdk(str(python)):
        raise OpsxError(
            "WORKER_RUNTIME_MISSING_CURSOR_SDK",
            "Poetry env in opsx/core cannot import cursor_sdk. "
            "Run: poetry install --extras cursor (requires Python 3.12+).",
        )
    return str(python)


# Back-compat for installer imports during migration away from managed venv.
def managed_runtime_executable(project: Project, core_sha: str) -> Path:
    """Deprecated: legacy ``opsx/runs/environments`` layout (Poetry replaces this)."""
    rel = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return project.runs / "environments" / core_sha / rel