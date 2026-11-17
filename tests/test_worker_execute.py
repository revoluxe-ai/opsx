"""Регрессии execute_request worker: ранний sys.path без UnboundLocalError."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from opsx_cli import worker
from opsx.project_context import bind_project_context, reset_project_context


class WorkerExecuteRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_sys_path_insert_before_mode_dispatch(self) -> None:
        """Внутренний `import sys` в execute_request делал ранний sys.path локальным."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "proj"
            core = root / "core"
            for path in (project / "opsx" / "project", core):
                path.mkdir(parents=True)
            (project / "opsx" / "project" / "project.json").write_text(
                "{}", encoding="utf-8"
            )
            request = {
                "project_root": str(project),
                "core_root": str(core),
                "mode": "__unsupported__",
            }
            token = bind_project_context(project, core)
            try:
                before = list(worker.sys.path)
                with self.assertRaises(ValueError) as ctx:
                    await worker.execute_request(request)
                self.assertIn("REQUEST_MODE_UNSUPPORTED", str(ctx.exception))
                after = list(worker.sys.path)
                self.assertGreater(len(after), len(before))
            finally:
                reset_project_context(token)


if __name__ == "__main__":
    unittest.main()