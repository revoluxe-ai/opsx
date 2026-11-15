"""
Product vs process finding filter for the Temporal review/repair POC.

The loop may only automate the **game** product tree. Tooling (including this
POC under `tools/temporal/`), OPSX, Cursor surfaces, and other repo junk must
not trigger `/opsx-orch-bug-fix` or security product patches.

Allowlist is the gate. Process prefixes below are documentation plus extra
denylist coverage for paths that might lack a trailing slash.
"""

from __future__ import annotations

import re
from typing import Iterable

# Game surfaces the loop may review/repair. Anything else is excluded.
PRODUCT_LOCATION_PREFIXES: tuple[str, ...] = (
    "src/",
    "tests/",
    "backend/",
    "openspec/changes/",
)

# Security lane reviews only game runtime + tests (not openspec/backend/process).
SECURITY_REVIEW_PATH_PREFIXES: tuple[str, ...] = (
    "src/",
    "tests/",
)

# Canonical process/tooling surfaces (repo-relative, forward slashes, lowercase).
# Source: `.cursor/commands/opsx-bug-intake-clear.md` Pass 0.25 plus this POC.
PROCESS_LOCATION_PREFIXES: tuple[str, ...] = (
    ".cursor/",
    ".agents/",
    "opsx/",
    "opsx-knowledge/",
    ".tmp/opsx",
    "tools/temporal/",
    "tools/",
)

PROCESS_LOCATION_DOCUMENTED_SUBPREFIXES: tuple[str, ...] = (
    ".cursor/commands/",
    ".cursor/rules/",
    ".cursor/skills/",
    ".cursor/agents/",
    ".cursor/pipeline/",
    ".cursor/static-check/",
)

_PRODUCT_LANE_RANK: tuple[tuple[str, int], ...] = (
    ("src/", 0),
    ("backend/", 1),
    ("tests/", 2),
    ("openspec/changes/", 3),
)


def _normalize_cell(value: str) -> str:
    return " ".join(value.strip().split())


def normalize_location_path(location: str) -> str:
    """Strip markdown/backticks and trailing :line from Bugbot LOC cells."""
    path = _normalize_cell(location).strip("`")
    match = re.match(r"^(?P<file_path>.+):(?P<line>\d+)$", path)
    if match:
        path = match.group("file_path")
    return path.replace("\\", "/")


def _normalized_repo_relative_path(location: str) -> str:
    path = normalize_location_path(location)
    if path.startswith("./"):
        path = path[2:]
    lowered = path.lower()
    for marker in ("/game/", "\\game\\"):
        idx = lowered.rfind(marker.replace("\\", "/"))
        if idx != -1:
            path = path[idx + len(marker) - 1 :].lstrip("/")
            break
    return path.lower()


def _normalized_repo_path(path: str) -> str:
    return path.replace("\\", "/").lower().lstrip("./")


def matches_path_prefixes(path: str, prefixes: tuple[str, ...]) -> bool:
    """True when a repo-relative path starts with any of the given prefixes."""
    norm = _normalized_repo_path(path)
    return any(norm.startswith(prefix) for prefix in prefixes)


def is_product_repo_path(path: str) -> bool:
    """True when a repo-relative path is inside the game product allowlist."""
    return matches_path_prefixes(path, PRODUCT_LOCATION_PREFIXES)


def product_lane_rank(path: str) -> tuple[int, str]:
    """Sort key for capped git file-diffs: src first, then backend/tests/openspec."""
    norm = path.replace("\\", "/").lower().lstrip("./")
    for prefix, rank in _PRODUCT_LANE_RANK:
        if norm.startswith(prefix):
            return (rank, norm)
    return (99, norm)


def is_repair_excluded_location(location: str) -> bool:
    """Return True when a finding is outside the game product allowlist."""
    return not is_product_repo_path(_normalized_repo_relative_path(location))


def partition_repair_findings(
    findings: Iterable[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    repairable: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    for row in findings:
        if is_repair_excluded_location(row["location"]):
            excluded.append(row)
        else:
            repairable.append(row)
    return repairable, excluded
