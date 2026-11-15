"""Target-native fail-closed browser-check adapter."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable, cast

CONTRACT_ID = "OPSX-WB-BROWSER-CHECK-01"
REVISION_RE = re.compile(r"^[0-9a-fA-F]{40}$")
SCENARIO_RE = re.compile(r"^####\s+Scenario:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
BROWSER_MARKERS = ("browser", "vue", " spa ", "frontend", "web ui", "playwright", "cypress", "page loads")
TELEGRAM_MARKERS = ("telegram", "callback", "bot")

def canonical_contained(candidate: Path, approved_root: Path) -> Path | None:
    root = approved_root.resolve()
    raw = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = raw.resolve()
    except OSError:
        return None
    root_text = os.path.normcase(str(root))
    resolved_text = os.path.normcase(str(resolved))
    if resolved_text == root_text or resolved_text.startswith(root_text + os.sep):
        return resolved
    return None

def git_head(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""

def blocked(reason: str, **extra: object) -> dict[str, object]:
    out: dict[str, object] = {
        "STATUS": "BLOCKED",
        "REASON": reason,
        "PASS": "NO",
        "CONTRACT_ID": CONTRACT_ID,
        "GREEN_WRITER": "NO",
        "COMMIT_ALLOWED": "NO",
    }
    out.update(extra)
    return out

def projectization_block(root: Path) -> dict[str, object] | None:
    from opsx_runtime_gates import projectization_and_adoption_block

    return projectization_and_adoption_block(
        root,
        "python.pipeline.browser_check",
        blocked_factory=blocked,
    )

def browser_adapter_declared(root: Path) -> bool:
    if (root / "opsx" / "adapters" / "browser").exists():
        return True
    path = root / "opsx" / "projectization.yaml"
    text = path.read_text(encoding="utf-8", errors="replace").lower() if path.is_file() else ""
    return any(token in text for token in ("browser_adapter: true", "browser: true", "capability: browser"))

def run(
    repo_root: Path,
    request: dict[str, object],
    *,
    head_reader: Callable[[Path], str] | None = None,
) -> dict[str, object]:
    root = repo_root.resolve()
    gate = projectization_block(root)
    if gate is not None:
        return gate
    if str(request.get("mode") or "").lower() == "bounded":
        return blocked("BINDING_GAP")
    if request.get("GO") in (True, "GO", "true", "YES"):
        return blocked("WRITE_AUTHORITY_FORBIDDEN")
    head = (head_reader or git_head)(root).strip()
    if not REVISION_RE.fullmatch(head):
        return blocked("head_unreadable")
    revision = str(request.get("revision") or head).strip()
    if not REVISION_RE.fullmatch(revision):
        return blocked("bad_revision")
    if revision.lower() != head.lower():
        return blocked("stale_revision")
    change = str(request.get("change") or "").strip()
    if not change:
        return blocked("change_required")
    changes_root = (root / "openspec" / "changes").resolve()
    target = canonical_contained(changes_root / change, changes_root)
    if target is None:
        return blocked("path_outside_workspace")

    texts: list[str] = []
    if target.exists():
        for path in sorted(target.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".md", ".yaml", ".yml", ".json", ".txt"}:
                if path.stat().st_size <= 1_000_000:
                    texts.append(
                        path.read_text(encoding="utf-8-sig", errors="replace")
                        .replace("\r\n", "\n")
                        .replace("\r", "\n")
                    )
    combined = "\n".join(texts)
    lowered = " " + combined.lower() + " "
    scenarios = SCENARIO_RE.findall(combined)
    browser_markers = [marker.strip() for marker in BROWSER_MARKERS if marker in lowered]
    telegram_markers = [marker for marker in TELEGRAM_MARKERS if marker in lowered]
    evidence = {
        "revision": revision,
        "scenario_heading_count": len(scenarios),
        "browser_markers": browser_markers,
        "telegram_markers": telegram_markers,
    }

    if browser_markers:
        if not browser_adapter_declared(root):
            return blocked(
                "BROWSER_SURFACE_UNDECLARED",
                EVIDENCE=evidence,
                BROWSER_ADAPTER_DECLARED="NO",
            )
        return blocked(
            "BINDING_GAP",
            EVIDENCE=evidence,
            BROWSER_ADAPTER_DECLARED="YES",
        )

    return {
        "STATUS": "NOT_APPLICABLE",
        "REASON": "BROWSER_SCENARIO_NOT_DECLARED",
        "PASS": "NO",
        "CONTRACT_ID": CONTRACT_ID,
        "EVIDENCE": evidence,
        "BROWSER_ADAPTER_DECLARED": "NO",
        "GREEN_WRITER": "NO",
        "COMMIT_ALLOWED": "NO",
    }

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--request-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        request = json.loads(args.request_file.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        print(json.dumps(blocked(f"invalid_request:{exc}"), sort_keys=True))
        return 2
    print(json.dumps(run(args.repo_root, cast(dict[str, object], request)), sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
