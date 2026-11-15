"""Machine-checkable legacy Cursor CLI transport call graph."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAME_ROOT = ROOT.parents[1]

PATTERNS = {
    "RealCursorAdapter(": re.compile(r"\bRealCursorAdapter\s*\("),
    "run_agent_command(": re.compile(r"\brun_agent_command\s*\("),
    "agent --print": re.compile(r"agent\s+--print"),
    "--resume": re.compile(r"--resume"),
}

CLASSIFY: dict[str, str] = {
    "tools/temporal/cursor_adapter.py": "COMPATIBILITY_ONLY",
    "tools/temporal/start.py": "ACTIVE_NONCANONICAL",
    "tools/temporal/cursor_execution_activity.py": "TEST_ONLY",
    "tools/temporal/tests/test_cursor_adapter_encoding.py": "TEST_ONLY",
}


def _rel(path: Path) -> str:
    try:
        return path.relative_to(GAME_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def classify_file(rel: str) -> str:
    if rel in CLASSIFY:
        return CLASSIFY[rel]
    if "/tests/" in rel or rel.endswith("_test.py") or "/test_" in rel:
        return "TEST_ONLY"
    if "dogfood" in rel or "fixture" in rel:
        return "TEST_ONLY"
    return "UNKNOWN"


def scan_tree(base: Path) -> list[dict]:
    hits: list[dict] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in {".py", ".md", ".mjs", ".json"}:
            continue
        rel = _rel(path)
        if rel.startswith(".git/"):
            continue
        if "/.venv/" in rel or rel.startswith("tools/cursor_executor/.venv/"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                kind = classify_file(rel)
                if rel == "tools/temporal/start.py" and label == "RealCursorAdapter(":
                    kind = "ACTIVE_NONCANONICAL"
                hits.append(
                    {
                        "pattern": label,
                        "path": rel,
                        "line": line,
                        "classification": kind,
                    }
                )
    return hits


def active_canonical_count(hits: list[dict]) -> int:
    return sum(1 for h in hits if h["classification"] == "ACTIVE_CANONICAL")


def test_active_canonical_legacy_cursor_callers_zero():
    hits = scan_tree(GAME_ROOT / "tools")
    active = active_canonical_count(hits)
    assert active == 0, f"ACTIVE_CANONICAL legacy hits: {active}\n{hits}"


if __name__ == "__main__":
    hits = scan_tree(GAME_ROOT / "tools")
    active = active_canonical_count(hits)
    print(f"ACTIVE_CANONICAL={active}")
    for h in hits:
        print(h)
    sys.exit(0 if active == 0 else 1)
