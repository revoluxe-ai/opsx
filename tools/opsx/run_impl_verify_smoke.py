"""Poetry-friendly entry: impl_verify node smoke (live SDK hop).

Usage from repo root::

    poetry install --with dev
    poetry run opsx-impl-verify-smoke -- --change-id invest-yalta-max-funnel --timeout 120

Requires ``tools/langgraph_runtime/.venv`` (cursor-sdk bootstrap); see
``opsx/opsx_runtime/runtime_bootstrap.py``.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import os
import runpy
import sys
from pathlib import Path


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    launcher = resource_path(repo, "opsx", "runs", "verify", "launch_impl_verify_node_smoke.py")
    if not launcher.is_file():
        raise SystemExit(f"missing launcher: {launcher}")

    runtime = resource_path(repo, "opsx", "opsx_runtime")
    os.environ.setdefault("CURSOR_USE_HTTP1", "1")
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    prev = os.environ.get("PYTHONPATH", "")
    rt = str(runtime)
    os.environ["PYTHONPATH"] = rt if not prev else f"{rt}{os.pathsep}{prev}"

    os.chdir(repo)
    sys.argv = [str(launcher), *sys.argv[1:]]
    runpy.run_path(str(launcher), run_name="__main__")


if __name__ == "__main__":
    main()
