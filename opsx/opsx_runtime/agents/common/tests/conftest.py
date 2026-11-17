"""Pytest: импорт ``agents`` из корня opsx_runtime."""

from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parents[3]
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))