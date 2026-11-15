# -*- coding: utf-8 -*-
"""History-only retry и STAGNANT fingerprint для causal append recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .errors import HistoryErrorCode, HistoryPause
from .store import CausalEventStore, PublishResult


class HistoryRetryOrchestrator:
    """Координирует history-only append без повторного semantic worker."""

    def __init__(self, store: CausalEventStore) -> None:
        self.store = store
        self.state_path = store.causal_root / "history_retry_state.json"

    def _fingerprint(self, operation_id: str, err: Optional[str] = None) -> str:
        published = self.store.replay_deterministic() if self._safe_replay() else []
        max_seq = max((int(e["EVENT_SEQUENCE"]) for e in published), default=0)
        body = {
            "max_seq": max_seq,
            "operation_id": operation_id,
            "err": err or "",
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    def _safe_replay(self) -> list[Dict[str, Any]]:
        try:
            return self.store.replay_deterministic()
        except HistoryPause:
            return []

    def _last_fp(self, operation_id: str) -> Optional[str]:
        if not self.state_path.is_file():
            return None
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        return data.get(operation_id)

    def _save_fp(self, operation_id: str, fp: str) -> None:
        data: Dict[str, str] = {}
        if self.state_path.is_file():
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        data[operation_id] = fp
        self.state_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

    def run_history_only(
        self,
        operation_id: str,
        publish_fn: Callable[[], PublishResult],
    ) -> PublishResult:
        try:
            return publish_fn()
        except HistoryPause as pause:
            fp = self._fingerprint(operation_id, pause.code.value)
            if self._last_fp(operation_id) == fp:
                raise HistoryPause(
                    HistoryErrorCode.STAGNANT_CAUSAL_FAILURE,
                    f"repeated fingerprint for {operation_id}",
                    "PAUSE operator intervention",
                ) from pause
            self._save_fp(operation_id, fp)
            raise
