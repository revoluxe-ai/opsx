"""Файловый журнал: append-only NDJSON, индекс fingerprint и spool при сбое."""

from __future__ import annotations

from opsx.project_context import resource_path

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bug_log.file_lock import FileLock
from bug_log.fingerprint import fingerprint_for
from bug_log.ids import BugRef, bug_id_from_fingerprint, case_id_for, sighting_id_for
from bug_log.lifecycle import apply_status
from bug_log.schemas import (
    BugCandidate,
    BugObservation,
    BugSummary,
    JournalHealth,
    JournalScope,
)

logger = logging.getLogger("opsx.bug_journal")


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dedupe(kind: str, *parts: str) -> str:
    import hashlib

    raw = "|".join([kind, *parts])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class FileBugJournal:
    """Журнал одного прогона поверх общего event store репозитория.

    Args:
        scope: change, run и путь репозитория. Агенты change_id не передают.
    """

    def __init__(self, scope: JournalScope) -> None:
        self.scope = scope
        root = Path(scope.repo_root) / "opsx" / "runs" / "bug-log"
        self._root = root
        self._events = resource_path(root, "BUG_JOURNAL.ndjson")
        self._index_path = resource_path(root, "bug_index.json")
        self._lock_path = resource_path(root, "BUG_JOURNAL.lock")
        self._spool = resource_path(root, "_spool")
        self._seen_dedupe: set[str] = set()
        self._events_n = 0
        self._spooled = 0
        self._dropped = 0
        self._index = self._load_index()

    def _load_index(self) -> dict[str, Any]:
        if not self._index_path.is_file():
            return {"fingerprints": {}, "bugs": {}, "aliases": {}}
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.error("bug journal index unreadable; starting empty")
            return {"fingerprints": {}, "bugs": {}, "aliases": {}}
        if not isinstance(data, dict):
            return {"fingerprints": {}, "bugs": {}, "aliases": {}}
        data.setdefault("fingerprints", {})
        data.setdefault("bugs", {})
        data.setdefault("aliases", {})
        return data

    def _save_index(self) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._index_path)

    def _append_locked(self, event: dict[str, Any]) -> None:
        key = str(event.get("dedupe_key") or "")
        if key and key in self._seen_dedupe:
            return
        line = json.dumps(event, ensure_ascii=False) + "\n"
        try:
            self._events.parent.mkdir(parents=True, exist_ok=True)
            with self._events.open("a", encoding="utf-8") as fh:
                fh.write(line)
            if key:
                self._seen_dedupe.add(key)
            self._events_n += 1
        except OSError:
            logger.error("bug journal append failed; spooling event %s", event.get("kind"))
            self._spool.mkdir(parents=True, exist_ok=True)
            spool_name = f"{event.get('event_id', 'evt')}.ndjson"
            try:
                (self._spool / spool_name).write_text(line, encoding="utf-8")
                self._spooled += 1
            except OSError:
                self._dropped += 1
                logger.error("bug journal spool failed")

    def _base_event(self, kind: str, bug_ids: list[str], dedupe: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "event_id": dedupe[:24],
            "dedupe_key": dedupe,
            "observed_at": _utc(),
            "kind": kind,
            "bug_ids": bug_ids,
            "change_id": self.scope.change_id,
            "change_scope": self.scope.change_scope,
            "run_id": self.scope.run_id,
            "stream_log": self.scope.stream_log,
            "mission_id": self.scope.mission_id,
        }

    def _register_sync(self, candidate: BugCandidate) -> BugRef:
        fp = fingerprint_for(
            lane=candidate.lane,
            observation=candidate.observation,
            location=candidate.location,
            tool=candidate.tool,
        )
        with FileLock(self._lock_path):
            self._index = self._load_index()
            existing = self._index["fingerprints"].get(fp)
            created = existing is None
            bug_id = str(existing or bug_id_from_fingerprint(fp))
            row = self._index["bugs"].setdefault(
                bug_id,
                {
                    "fingerprint": fp,
                    "status": None,
                    "first_seen_change": self.scope.change_id,
                    "recurrence": 0,
                },
            )
            status, anomaly = apply_status(row.get("status"), "OBSERVED")
            row["status"] = status
            row["recurrence"] = int(row.get("recurrence") or 0) + 1
            self._index["fingerprints"][fp] = bug_id
            if candidate.local_id:
                alias_key = f"{self.scope.run_id}:{candidate.alias_scheme or candidate.lane}:{candidate.local_id}"
                self._index["aliases"][alias_key] = bug_id
            ordinal = int(row["recurrence"])
            sighting = sighting_id_for(
                bug_id=bug_id,
                run_id=self.scope.run_id,
                lane=candidate.lane,
                round_n=0,
                ordinal=ordinal,
            )
            if created:
                self._append_locked(
                    {
                        **self._base_event(
                            "BUG_REGISTERED",
                            [bug_id],
                            _dedupe("BUG_REGISTERED", bug_id, fp),
                        ),
                        "summary": candidate.observation[:240],
                        "detail": {"fingerprint": fp, "lane": candidate.lane, **candidate.detail},
                        "case_id": case_id_for(bug_id),
                    }
                )
            self._append_locked(
                {
                    **self._base_event(
                        "SIGHTING",
                        [bug_id],
                        _dedupe("SIGHTING", sighting),
                    ),
                    "summary": candidate.observation[:240],
                    "sighting_id": sighting,
                    "local_id": candidate.local_id,
                    "detail": {
                        "location": candidate.location,
                        "tool": candidate.tool,
                        "severity": candidate.severity,
                        **candidate.detail,
                    },
                    "case_id": case_id_for(bug_id),
                }
            )
            if anomaly:
                self._append_locked(
                    {
                        **self._base_event(
                            "ANOMALY",
                            [bug_id],
                            _dedupe("ANOMALY", bug_id, self.scope.run_id, str(ordinal)),
                        ),
                        "summary": "status transition rejected",
                    }
                )
            if candidate.local_id:
                self._append_locked(
                    {
                        **self._base_event(
                            "ALIAS_ADDED",
                            [bug_id],
                            _dedupe(
                                "ALIAS",
                                bug_id,
                                self.scope.run_id,
                                candidate.local_id,
                            ),
                        ),
                        "summary": candidate.local_id,
                        "detail": {"scheme": candidate.alias_scheme or candidate.lane},
                    }
                )
            self._save_index()
        return BugRef(bug_id=bug_id, local_id=candidate.local_id, change_id=self.scope.change_id)

    def _record_sync(self, observation: BugObservation) -> None:
        with FileLock(self._lock_path):
            self._index = self._load_index()
            row = self._index["bugs"].setdefault(
                observation.bug_id,
                {
                    "fingerprint": "",
                    "status": "OBSERVED",
                    "first_seen_change": self.scope.change_id,
                    "recurrence": 1,
                },
            )
            if observation.status:
                status, anomaly = apply_status(row.get("status"), observation.status)
                row["status"] = status
                if anomaly:
                    self._append_locked(
                        {
                            **self._base_event(
                                "ANOMALY",
                                [observation.bug_id],
                                _dedupe(
                                    "ANOMALY",
                                    observation.bug_id,
                                    observation.status,
                                    observation.summary[:40],
                                ),
                            ),
                            "summary": f"rejected {observation.status}",
                        }
                    )
            dedupe = _dedupe(
                observation.kind,
                observation.bug_id,
                self.scope.run_id,
                observation.provenance.hop or "",
                observation.provenance.execution_id or "",
                observation.summary[:80],
            )
            self._append_locked(
                {
                    **self._base_event(observation.kind, [observation.bug_id], dedupe),
                    "summary": observation.summary,
                    "evidence_refs": observation.evidence_refs,
                    "detail": observation.detail,
                    "provenance": observation.provenance.model_dump(mode="json"),
                    "related_bug_id": observation.related_bug_id,
                    "relation": observation.relation,
                    "case_id": case_id_for(observation.bug_id),
                }
            )
            self._save_index()

    async def register(self, candidate: BugCandidate) -> BugRef:
        return await asyncio.to_thread(self._register_sync, candidate)

    async def record(self, observation: BugObservation) -> None:
        await asyncio.to_thread(self._record_sync, observation)

    async def link(
        self,
        source: BugRef,
        target: BugRef,
        relation: str,
        evidence: tuple[str, ...] = (),
    ) -> None:
        await self.record(
            BugObservation(
                bug_id=source.bug_id,
                kind="LINKED",
                summary=relation,
                related_bug_id=target.bug_id,
                relation=relation,
                evidence_refs=list(evidence),
            )
        )

    def resolve(self, bug_id: str) -> BugSummary | None:
        row = self._index["bugs"].get(bug_id)
        if not isinstance(row, dict):
            return None
        return BugSummary(
            bug_id=bug_id,
            status=str(row.get("status") or "OBSERVED"),
            first_seen_change=row.get("first_seen_change"),
            recurrence_count=int(row.get("recurrence") or 0),
            fingerprint=str(row.get("fingerprint") or ""),
        )

    def health(self) -> JournalHealth:
        return JournalHealth(events=self._events_n, spooled=self._spooled, dropped=self._dropped)

    def replay_spool(self) -> int:
        """Долить события из spool в основной журнал. Возвращает число строк."""
        if not self._spool.is_dir():
            return 0
        moved = 0
        with FileLock(self._lock_path):
            for path in sorted(self._spool.glob("*.ndjson")):
                text = path.read_text(encoding="utf-8")
                with self._events.open("a", encoding="utf-8") as fh:
                    fh.write(text if text.endswith("\n") else text + "\n")
                path.unlink()
                moved += 1
        return moved


def open_run_journal(
    *,
    repo_root: Path | str,
    run_id: str,
    change_id: str | None = None,
    change_scope: str = "CHANGE",
    stream_log: str | None = None,
    mission_id: str | None = None,
) -> FileBugJournal:
    """Открыть журнал, привязанный к прогону.

    Args:
        repo_root: Корень репозитория.
        run_id: ``run_uuid`` / session id.
        change_id: OpenSpec change или None.
        change_scope: CHANGE, STANDALONE или META.
        stream_log: Путь live-log, если есть.
        mission_id: Идентификатор миссии repair/verify.
    """
    scope = JournalScope(
        repo_root=str(repo_root),
        change_id=change_id,
        change_scope=change_scope,  # type: ignore[arg-type]
        run_id=run_id,
        stream_log=stream_log,
        mission_id=mission_id,
    )
    journal = FileBugJournal(scope)
    _ = journal.replay_spool()
    return journal


def require_live_journal(journal: object, *, live: bool) -> None:
    """Запретить live-прогон с ``NullBugJournal``.

    Raises:
        RuntimeError: код ``LIVE_BUG_JOURNAL_REQUIRED``.
    """
    from bug_log.null_journal import NullBugJournal

    if live and isinstance(journal, NullBugJournal):
        raise RuntimeError("LIVE_BUG_JOURNAL_REQUIRED")
