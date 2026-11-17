"""Проекции журнала: by-change, by-bug, manifest и legacy bug-cases."""

from __future__ import annotations

from opsx.project_context import resource_path

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _write_ndjson(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


class BugJournalProjector:
    """Пересобираемые представления event store. Не пишут события сами."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.root = resource_path(repo_root, "opsx", "runs", "bug-log")
        self.events_path = resource_path(self.root, "BUG_JOURNAL.ndjson")

    def rebuild(self) -> dict[str, Any]:
        """Пересобрать срезы и manifest из NDJSON.

        Returns:
            Краткий отчёт: число событий и change.
        """
        events = _read_events(self.events_path)
        by_change: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_bug: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            change = event.get("change_id")
            if isinstance(change, str) and change:
                by_change[change].append(event)
            for bug_id in event.get("bug_ids") or []:
                if isinstance(bug_id, str):
                    by_bug[bug_id].append(event)
        for change, rows in by_change.items():
            _write_ndjson(resource_path(self.root, "by-change", change, "events.ndjson"), rows)
        for bug_id, rows in by_bug.items():
            _write_ndjson(resource_path(self.root, "by-bug", bug_id, "timeline.ndjson"), rows)
        manifest = self._manifest(events, by_change, by_bug)
        manifest_path = resource_path(self.root, "ANALYSIS_MANIFEST.json")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._write_legacy(by_bug)
        return {"events": len(events), "changes": sorted(by_change)}

    def _manifest(
        self,
        events: list[dict[str, Any]],
        by_change: dict[str, list[dict[str, Any]]],
        by_bug: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "event_count": len(events),
            "changes": {
                change: {
                    "events": len(rows),
                    "path": f"opsx/runs/bug-log/by-change/{change}/events.ndjson",
                    "openspec": f"openspec/changes/{change}/",
                }
                for change, rows in sorted(by_change.items())
            },
            "bugs": {
                bug_id: {
                    "events": len(rows),
                    "path": f"opsx/runs/bug-log/by-bug/{bug_id}/timeline.ndjson",
                }
                for bug_id, rows in sorted(by_bug.items())
            },
        }

    def _write_legacy(self, by_bug: dict[str, list[dict[str, Any]]]) -> None:
        """Минимальный CASE/LEDGER/ROUTE-TRACE как проекция, не второй writer."""
        cases = resource_path(self.repo_root, "opsx", "runs", "bug-cases")
        index_lines = ["# Bug cases", ""]
        for bug_id, rows in sorted(by_bug.items()):
            case = f"BUGCASE-{bug_id}"
            root = cases / case
            (resource_path(root, "findings")).mkdir(parents=True, exist_ok=True)
            (resource_path(root, "results")).mkdir(parents=True, exist_ok=True)
            change = next((row.get("change_id") for row in rows if row.get("change_id")), None)
            (resource_path(root, "CASE.md")).write_text(
                "\n".join(
                    [
                        f"CASE_ID: {case}",
                        f"ROOT_FINDING_ID: {bug_id}",
                        f"CHANGE: {change or ''}",
                        "STATUS: projected",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (resource_path(root, "findings", f"{bug_id}.md")).write_text(
                f"FINDING_ID: {bug_id}\nCASE_ID: {case}\nCHANGE: {change or ''}\n",
                encoding="utf-8",
            )
            ledger = ["# LEDGER", "", "## ROUTE_STATE", ""]
            trace_lines: list[str] = []
            for seq, row in enumerate(rows, start=1):
                ledger.append(f"- {row.get('observed_at')} {row.get('kind')} {row.get('summary', '')}")
                trace_lines.append(
                    json.dumps(
                        {
                            "SCHEMA_VERSION": "2",
                            "SEQUENCE": seq,
                            "EVENT_ID": row.get("event_id"),
                            "KIND": row.get("kind"),
                            "CHANGE_ID": change,
                            "BUG_ID": bug_id,
                        },
                        ensure_ascii=False,
                    )
                )
            (resource_path(root, "LEDGER.md")).write_text("\n".join(ledger) + "\n", encoding="utf-8")
            (resource_path(root, "results", "ROUTE-TRACE.jsonl")).write_text(
                "\n".join(trace_lines) + ("\n" if trace_lines else ""),
                encoding="utf-8",
            )
            index_lines.append(f"- {case} change={change or '-'} events={len(rows)}")
        cases.mkdir(parents=True, exist_ok=True)
        (cases / "INDEX.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
