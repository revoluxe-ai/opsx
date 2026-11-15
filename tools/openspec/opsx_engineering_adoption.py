"""CLI entry: engineering adoption workflow (implementation in pipeline)."""

from __future__ import annotations

from opsx.project_context import resource_path

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_IMPL_MODULE_NAME = "opsx_engineering_adoption_impl"


def _load_impl() -> ModuleType:
    """Загрузить pipeline implementation с регистрацией в ``sys.modules`` до ``exec_module``.

    Без предварительной регистрации ``@dataclass`` в динамически загружаемом модуле
    падает с ``AttributeError`` (``cls.__module__`` не резолвится в ``sys.modules``).
    """

    root = Path(__file__).resolve().parents[2]
    module_path = resource_path(root, ".cursor/pipeline/engineering-adoption/engineering_adoption.py")
    if not module_path.is_file():
        raise RuntimeError(f"adoption workflow missing at {module_path}")
    spec = importlib.util.spec_from_file_location(_IMPL_MODULE_NAME, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load adoption workflow from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def main(argv: list[str] | None = None) -> int:
    """Делегировать в pipeline ``engineering_adoption.main``."""

    impl = _load_impl()
    return int(impl.main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
