"""Ключ дедупликации одиночного pytest-probe: revisions причинных файлов и параметры запуска."""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from agents.verify.dispatch_unit.repro_cohort import pytest_module
from workflows.verify.pytest_workflow_argv import WORKFLOW_PYTEST_MARKEXPR

_TRACE_FILE_RE = re.compile(r'File "([^"]+\.py)"')
_OBSERVATION_FILE_RE = re.compile(r"(?:^|[\s:])([\w./\\-]+\.py)(?::\d+)?(?:\s|$)")


def _git_blob_sha(repo: Path, rel_path: str) -> str:
    """SHA blob в индексе/worktree; при ошибке — хеш содержимого файла."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "hash-object", rel_path],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    target = (resource_path(repo, rel_path)).resolve()
    if target.is_file():
        return hashlib.sha256(target.read_bytes()).hexdigest()
    return f"missing:{rel_path}"


def _conftest_chain(repo: Path, test_module: str) -> list[str]:
    """conftest.py от каталога теста вверх до ``tests/``."""
    rel = Path(test_module.replace("\\", "/"))
    parts: list[str] = []
    directory = (resource_path(repo, rel.parent)).resolve()
    tests_root = (resource_path(repo, "tests")).resolve()
    while directory.is_relative_to(tests_root) or directory == tests_root:
        candidate = directory / "conftest.py"
        if candidate.is_file():
            try:
                parts.append(str(candidate.relative_to(repo)).replace("\\", "/"))
            except ValueError:
                parts.append(str(candidate))
        if directory == tests_root:
            break
        directory = directory.parent
    root_conftest = resource_path(repo, "conftest.py")
    if root_conftest.is_file():
        try:
            parts.append(str(root_conftest.relative_to(repo)).replace("\\", "/"))
        except ValueError:
            parts.append("conftest.py")
    return sorted(set(parts))


def causal_paths_for_probe(
    repo: Path,
    *,
    node_id: str,
    observation: str,
) -> list[str]:
    """Пути теста, conftest и продуктовые файлы из traceback observation."""
    paths: set[str] = set()
    module = pytest_module(node_id).replace("\\", "/")
    paths.add(module)
    paths.update(_conftest_chain(repo, module))
    text = observation or ""
    for match in _TRACE_FILE_RE.finditer(text):
        raw = match.group(1).replace("\\", "/")
        if raw.startswith("/") or ":" in raw[:3]:
            continue
        if (resource_path(repo, raw)).is_file() or raw.endswith(".py"):
            paths.add(raw.lstrip("./"))
    for match in _OBSERVATION_FILE_RE.finditer(text):
        raw = match.group(1).replace("\\", "/")
        if "test" in raw or raw.startswith("src/"):
            paths.add(raw.lstrip("./"))
    safe: list[str] = []
    for item in sorted(paths):
        if not item.endswith(".py"):
            continue
        if ".." in item:
            continue
        safe.append(item)
    return safe


def batch_workers_from_state(state: dict[str, Any]) -> int:
    """Число workers исходной пачки (xdist в argv нет — по умолчанию 1)."""
    raw = state.get("quality_gate_raw")
    if isinstance(raw, dict):
        pytest_block = raw.get("pytest")
        if isinstance(pytest_block, dict):
            command = pytest_block.get("command")
            if isinstance(command, list):
                for index, token in enumerate(command):
                    if token == "-n" and index + 1 < len(command):
                        try:
                            return max(1, int(command[index + 1]))
                        except ValueError:
                            return 1
                    if isinstance(token, str) and token.startswith("-n"):
                        try:
                            return max(1, int(token[2:]))
                        except ValueError:
                            return 1
    return 1


def build_single_repro_probe_key(
    repo: Path,
    *,
    node_id: str,
    observation: str,
    state: dict[str, Any],
) -> str:
    """Стабильный ключ probe: revisions + interpreter/cwd/markexpr/timeout/workers."""
    paths = causal_paths_for_probe(repo, node_id=node_id, observation=observation)
    revisions = {path: _git_blob_sha(repo, path) for path in paths}
    launch = {
        "markexpr": WORKFLOW_PYTEST_MARKEXPR,
        "workers": batch_workers_from_state(state),
        "cwd": str(repo.resolve()),
        "node_id": node_id,
    }
    material = json.dumps(
        {"revisions": revisions, "launch": launch},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
