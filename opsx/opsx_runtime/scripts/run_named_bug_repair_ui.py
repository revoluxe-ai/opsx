#!/usr/bin/env python3
"""Запуск локального UI Owner-named bug repair (composition root + HTTP server).

Из корня репозитория:

    poetry run python -u opsx/opsx_runtime/scripts/run_named_bug_repair_ui.py

Браузер: http://127.0.0.1:8765/
"""

from __future__ import annotations

from opsx.project_context import project_root

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
RUNTIME_ROOT = _SCRIPTS.parent
REPO_ROOT = project_root()
_UI_DIR = RUNTIME_ROOT / "ui" / "named_bug_repair"

for extra in (RUNTIME_ROOT, _UI_DIR):
    extra_s = str(extra)
    if extra_s not in sys.path:
        sys.path.insert(0, extra_s)

from bootstrap import apply_named_bug_repair_process_env  # noqa: E402
from server import main  # noqa: E402

apply_named_bug_repair_process_env()

if __name__ == "__main__":
    raise SystemExit(main())