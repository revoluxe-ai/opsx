"""Pytest hooks for verify gate tests: ``agents`` / ``workflows`` live under opsx_runtime."""

import sys
from pathlib import Path

_RUNTIME_ROOT = Path(__file__).resolve().parents[2]
if str(_RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_ROOT))
