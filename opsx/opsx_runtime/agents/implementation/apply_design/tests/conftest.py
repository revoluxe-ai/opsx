"""Pytest: импорт ``agents`` из корня этого runtime, без pythonpath продукта."""

from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parents[4]
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))
