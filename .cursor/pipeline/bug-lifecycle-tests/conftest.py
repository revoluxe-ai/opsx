import sys
from pathlib import Path

import pytest

_PIPELINE = Path(__file__).resolve().parents[1]
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from bug_lifecycle.hermetic_boundary import (
    authorize_hermetic_sandbox,
    clear_hermetic_authority,
    register_hermetic_workspace,
    unregister_hermetic_workspace,
)


@pytest.fixture
def orch_hermetic_root(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir(parents=True, exist_ok=True)
    authorize_hermetic_sandbox(tmp_path)
    register_hermetic_workspace(root)
    try:
        yield root
    finally:
        unregister_hermetic_workspace(root)
        clear_hermetic_authority()
