"""Pytest: путь к ``named_bug_repair`` для импорта ``bootstrap`` / ``server``."""

from __future__ import annotations

import sys
from pathlib import Path

_UI_ROOT = Path(__file__).resolve().parents[1]
ui_root = str(_UI_ROOT)
if ui_root not in sys.path:
    sys.path.insert(0, ui_root)