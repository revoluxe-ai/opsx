"""Ревизии файлов ремонта для дедупликации finding_commit."""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import json
import subprocess
from pathlib import Path


def repair_files_revision_key(repo: Path, files: list[str]) -> str:
    """Стабильный ключ revision закоммиченных путей (git blob SHA)."""
    revisions: dict[str, str] = {}
    for rel in sorted(set(files)):
        proc = subprocess.run(
            ["git", "-C", str(repo), "hash-object", rel],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            revisions[rel] = proc.stdout.strip()
        else:
            target = resource_path(repo, rel)
            if target.is_file():
                revisions[rel] = hashlib.sha256(target.read_bytes()).hexdigest()
            else:
                revisions[rel] = "missing"
    material = json.dumps(revisions, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
