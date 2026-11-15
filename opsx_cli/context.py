"""Explicit paths for one project; the core checkout is a separate repository."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re

DEFAULT_REPOSITORY = "https://github.com/revoluxe-ai/opsx.git"
CORE_RELATIVE = "opsx/core"


class OpsxError(RuntimeError):
    """An actionable CLI error with a stable machine-readable code."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Project:
    root: Path

    @classmethod
    def at(cls, value: str | Path = ".") -> "Project":
        root = Path(value).expanduser().resolve()
        if root == Path(root.anchor):
            raise OpsxError("PROJECT_ROOT_UNSAFE", "Choose a project directory, not a filesystem root.")
        return cls(root)

    @property
    def core(self) -> Path:
        return self.root / CORE_RELATIVE

    @property
    def settings(self) -> Path:
        return self.root / "opsx/project/project.json"

    @property
    def installation(self) -> Path:
        return self.root / "opsx/install.json"

    @property
    def runs(self) -> Path:
        return self.root / "opsx/runs"

    @property
    def panel_state(self) -> Path:
        return self.runs / "panel.json"

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env["OPSX_PROJECT_ROOT"] = str(self.root)
        env["OPSX_CORE_ROOT"] = str(self.core)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = os.pathsep.join(
            [str(self.core), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        return env


def safe_change_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) or value in (".", ".."):
        raise OpsxError("CHANGE_ID_UNSAFE", "Change id must be one directory name.")
    return value
