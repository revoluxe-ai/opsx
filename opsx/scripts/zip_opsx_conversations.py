#!/usr/bin/env python3
"""Собрать zip с диалогами OPSX: conversation.json, live logs, run_dialogs."""

from __future__ import annotations

from opsx.project_context import resource_path

import zipfile
from datetime import datetime
from pathlib import Path


def collect_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for base in (
        resource_path(root, "openspec/changes"),
        resource_path(root, "opsx/runs/verify"),
        resource_path(root, "opsx/runs/dialogs"),
    ):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if "run_dialogs" in path.parts:
                files.append(path)
                continue
            name = path.name
            if name.endswith((".conversation.json", ".live.log", ".live.ndjson")):
                files.append(path)
    return sorted(set(files))


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = resource_path(root, f"opsx-conversations-{ts}.zip")
    files = collect_files(root)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.relative_to(root).as_posix())
    print(f"files={len(files)}")
    print(f"size={out.stat().st_size}")
    print(f"PATH:{out}")


if __name__ == "__main__":
    main()
