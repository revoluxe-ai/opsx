"""Worker vs product pytest: Poetry core, не product venv."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opsx_cli.context import OpsxError, Project
from opsx_cli.worker_runtime import (
    core_poetry_python,
    interpreter_imports_langgraph,
    is_legacy_managed_runtime_pin,
    is_repository_product_venv,
    resolve_worker_interpreter,
    worker_interpreter_candidates,
)


class WorkerRuntimeTests(unittest.TestCase):
    def test_legacy_managed_pin_detected(self) -> None:
        assert is_legacy_managed_runtime_pin(
            "opsx/runs/environments/sha/Scripts/python.exe"
        )

    def test_product_venv_is_not_worker_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product = root / ".venv" / "Scripts" / "python.exe"
            product.parent.mkdir(parents=True, exist_ok=True)
            product.write_text("", encoding="utf-8")
            project = Project.at(str(root))
            metadata = {
                "core_sha": "abc123",
                "runtime_python": ".venv/Scripts/python.exe",
            }
            candidates = worker_interpreter_candidates(project, metadata)
            assert all(
                not is_repository_product_venv(project, Path(path))
                for path in candidates
            )

    def test_resolve_worker_prefers_core_poetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            poetry_py = root / "poetry-venv" / "Scripts" / "python.exe"
            poetry_py.parent.mkdir(parents=True, exist_ok=True)
            poetry_py.write_text("", encoding="utf-8")
            project = Project.at(str(root))
            project.core.mkdir(parents=True, exist_ok=True)
            metadata = {
                "core_sha": "sha1",
                "runtime_python": "opsx/runs/environments/sha1/Scripts/python.exe",
            }

            def fake_import(path: str) -> bool:
                return os.path.normcase(path) == os.path.normcase(str(poetry_py))

            with patch(
                "opsx_cli.worker_runtime.core_poetry_python",
                return_value=poetry_py,
            ), patch(
                "opsx_cli.worker_runtime.interpreter_imports_langgraph",
                side_effect=fake_import,
            ):
                resolved = resolve_worker_interpreter(project, metadata)
            assert os.path.normcase(resolved) == os.path.normcase(str(poetry_py))

    def test_resolve_worker_raises_without_langgraph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Project.at(tmp)
            (project.core).mkdir(parents=True, exist_ok=True)
            metadata = {"core_sha": "missing"}
            with patch(
                "opsx_cli.worker_runtime.core_poetry_python", return_value=None
            ), patch(
                "opsx_cli.worker_runtime.interpreter_imports_langgraph",
                return_value=False,
            ):
                with self.assertRaises(OpsxError):
                    resolve_worker_interpreter(project, metadata)

    def test_langgraph_probe_uses_memory_saver(self) -> None:
        probe_path = (
            r"C:\opsx\python.exe" if os.name == "nt" else "/usr/bin/python3"
        )
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            assert interpreter_imports_langgraph(probe_path)
            args = run.call_args[0][0]
            assert "langgraph.checkpoint.memory" in args[-1]

    def test_core_poetry_python_parses_env_info(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            core = Path(tmp)
            venv = core / "venv"
            py = venv / "Scripts" / "python.exe"
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("", encoding="utf-8")

            class Completed:
                returncode = 0
                stdout = f"{venv}\n"
                stderr = ""

            with patch("shutil.which", return_value="poetry"), patch(
                "subprocess.run", return_value=Completed()
            ):
                resolved = core_poetry_python(core)
            assert resolved == py