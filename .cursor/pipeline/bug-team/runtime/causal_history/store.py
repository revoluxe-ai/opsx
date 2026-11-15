# -*- coding: utf-8 -*-
"""Immutable events/{EVENT_ID}.json store + per-OPERATION_ID ACK."""

from __future__ import annotations

import hashlib
import sys

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl
import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .errors import HistoryErrorCode, HistoryPause
from .platform_adapter import fsync_directory, fsync_file, publish_no_replace


def canonical_payload_digest(payload: Dict[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k != "PAYLOAD_DIGEST"}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def slug_operation_id(operation_id: str) -> str:
    """Безопасное имя файла для per-OPERATION_ID ACK."""
    return operation_id.replace("/", "_").replace(":", "_")


def derive_event_id(case_id: str, operation_id: str, event_slot: str = "CAUSAL_EVENT") -> str:
    seed = f"{case_id}\0{operation_id}\0{event_slot}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


@dataclass
class EvidenceBinding:
    proof_path: str
    proof_digest: str
    execution_revision: str


@dataclass
class PublishResult:
    event_id: str
    event_sequence: int
    payload_digest: str
    event_path: Path
    ack_path: Path
    idempotent: bool


class CausalEventStore:
    """Canonical immutable causal events under results/causal/events/."""

    def __init__(self, case_root: Path, case_id: str) -> None:
        self.case_root = Path(case_root)
        self.case_id = case_id
        self.causal_root = self.case_root / "results" / "causal"
        self.events_dir = self.causal_root / "events"
        self.tmp_dir = self.events_dir / ".tmp"
        self.acks_dir = self.causal_root / "acks"
        self.acks_tmp = self.acks_dir / ".tmp"
        self.lock_path = self.causal_root / ".publish.lock"
        for d in (self.events_dir, self.tmp_dir, self.acks_dir, self.acks_tmp):
            d.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def publish_lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as fh:
            if sys.platform == "win32":
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if sys.platform == "win32":
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _list_published(self) -> List[Tuple[Path, Dict[str, Any]]]:
        out: List[Tuple[Path, Dict[str, Any]]] = []
        for p in sorted(self.events_dir.glob("*.json")):
            if p.name.startswith("."):
                continue
            out.append((p, json.loads(p.read_text(encoding="utf-8"))))
        return out

    def validate_replay_order(self) -> None:
        published = self._list_published()
        by_seq: Dict[int, str] = {}
        for path, obj in published:
            eid = obj.get("EVENT_ID", path.stem)
            if path.stem != eid:
                raise HistoryPause(
                    HistoryErrorCode.REPLAY_ORDER_BROKEN,
                    f"path stem != EVENT_ID for {eid}",
                    "PAUSE case",
                )
            seq = int(obj["EVENT_SEQUENCE"])
            if seq in by_seq and by_seq[seq] != eid:
                raise HistoryPause(
                    HistoryErrorCode.REPLAY_ORDER_BROKEN,
                    f"duplicate sequence {seq}",
                    "PAUSE case",
                )
            by_seq[seq] = eid
        if not by_seq:
            return
        max_seq = max(by_seq)
        if set(by_seq) != set(range(1, max_seq + 1)):
            raise HistoryPause(
                HistoryErrorCode.REPLAY_ORDER_BROKEN,
                f"sequence gaps: {sorted(by_seq)}",
                "PAUSE case",
            )

    def _allocate_sequence(self, event_id: str, part_path: Path) -> int:
        published = self._list_published()
        seqs = {int(o["EVENT_SEQUENCE"]) for _, o in published}
        if part_path.exists():
            part_obj = json.loads(part_path.read_text(encoding="utf-8"))
            s = int(part_obj["EVENT_SEQUENCE"])
            if part_obj.get("EVENT_ID") != event_id:
                raise HistoryPause(
                    HistoryErrorCode.AMBIGUOUS_TMP,
                    "tmp EVENT_ID mismatch",
                    "Operator review",
                )
            if s in seqs:
                raise HistoryPause(
                    HistoryErrorCode.SEQUENCE_ALLOCATION_COLLISION,
                    f"sequence {s} already used",
                    "PAUSE",
                )
            return s
        return (max(seqs) if seqs else 0) + 1

    def _verify_evidence(self, binding: EvidenceBinding) -> None:
        proof = self.case_root / binding.proof_path
        if not proof.is_file():
            raise HistoryPause(
                HistoryErrorCode.EVIDENCE_STALE,
                f"missing proof {binding.proof_path}",
                "Restore proof artifact",
            )
        digest = hashlib.sha256(proof.read_bytes()).hexdigest()
        if digest != binding.proof_digest:
            raise HistoryPause(
                HistoryErrorCode.EVIDENCE_STALE,
                "proof_digest mismatch",
                "Re-bind evidence",
            )

    def publish_event(
        self,
        operation_id: str,
        event_body: Dict[str, Any],
        binding: EvidenceBinding,
        *,
        event_slot: str = "CAUSAL_EVENT",
    ) -> PublishResult:
        self.validate_replay_order()
        self._verify_evidence(binding)
        event_id = derive_event_id(self.case_id, operation_id, event_slot)
        event_path = self.events_dir / f"{event_id}.json"
        part_path = self.tmp_dir / f"{event_id}.json.part"
        ack_slug = slug_operation_id(operation_id)
        ack_path = self.acks_dir / f"{ack_slug}.json"
        ack_part = self.acks_tmp / f"{ack_slug}.json.part"

        with self.publish_lock():
            if event_path.is_file():
                existing = json.loads(event_path.read_text(encoding="utf-8"))
                new_digest = canonical_payload_digest({**event_body, "EVENT_ID": event_id})
                if existing.get("PAYLOAD_DIGEST") != new_digest:
                    raise HistoryPause(
                        HistoryErrorCode.OPERATION_ID_PAYLOAD_COLLISION,
                        f"conflicting payload for {operation_id}",
                        "PAUSE",
                    )
                return self._ensure_ack(operation_id, event_id, existing, idempotent=True)

            seq = self._allocate_sequence(event_id, part_path)
            payload = {
                **event_body,
                "CASE_ID": self.case_id,
                "OPERATION_ID": operation_id,
                "EVENT_ID": event_id,
                "EVENT_SLOT": event_slot,
                "EVENT_SEQUENCE": seq,
                "EVIDENCE_BINDING": {
                    "proof_path": binding.proof_path,
                    "proof_digest": binding.proof_digest,
                    "execution_revision": binding.execution_revision,
                },
            }
            payload["PAYLOAD_DIGEST"] = canonical_payload_digest(payload)
            part_path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            fsync_file(part_path)
            status = publish_no_replace(part_path, event_path)
            if status == "exists":
                existing = json.loads(event_path.read_text(encoding="utf-8"))
                return self._ensure_ack(operation_id, event_id, existing, idempotent=True)
            fsync_file(event_path)
            fsync_directory(self.events_dir)
            return self._write_ack(operation_id, event_id, payload, ack_path, ack_part)

    def _write_ack(
        self,
        operation_id: str,
        event_id: str,
        event_payload: Dict[str, Any],
        ack_path: Path,
        ack_part: Path,
    ) -> PublishResult:
        ack_body = {
            "OPERATION_ID": operation_id,
            "EVENT_ID": event_id,
            "EVENT_SEQUENCE": event_payload["EVENT_SEQUENCE"],
            "PAYLOAD_DIGEST": event_payload["PAYLOAD_DIGEST"],
            "published_path": f"events/{event_id}.json",
            "ack_generation": 1,
        }
        ack_part.write_text(json.dumps(ack_body, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        fsync_file(ack_part)
        status = publish_no_replace(ack_part, ack_path)
        if status == "exists":
            return self._ensure_ack(operation_id, event_id, event_payload, idempotent=True)
        fsync_file(ack_path)
        fsync_directory(self.acks_dir)
        if ack_part.exists():
            ack_part.unlink()
        return PublishResult(
            event_id=event_id,
            event_sequence=int(event_payload["EVENT_SEQUENCE"]),
            payload_digest=event_payload["PAYLOAD_DIGEST"],
            event_path=self.events_dir / f"{event_id}.json",
            ack_path=ack_path,
            idempotent=False,
        )

    def _ensure_ack(
        self,
        operation_id: str,
        event_id: str,
        event_payload: Dict[str, Any],
        *,
        idempotent: bool,
    ) -> PublishResult:
        ack_path = self.acks_dir / f"{slug_operation_id(operation_id)}.json"
        if ack_path.is_file():
            ack = json.loads(ack_path.read_text(encoding="utf-8"))
            if (
                ack.get("EVENT_ID") == event_id
                and ack.get("PAYLOAD_DIGEST") == event_payload.get("PAYLOAD_DIGEST")
            ):
                return PublishResult(
                    event_id=event_id,
                    event_sequence=int(event_payload["EVENT_SEQUENCE"]),
                    payload_digest=event_payload["PAYLOAD_DIGEST"],
                    event_path=self.events_dir / f"{event_id}.json",
                    ack_path=ack_path,
                    idempotent=True,
                )
            raise HistoryPause(
                HistoryErrorCode.ACK_IDENTITY_COLLISION,
                "ACK conflicts with canonical event",
                "PAUSE",
            )
        return self._write_ack(
            operation_id,
            event_id,
            event_payload,
            ack_path,
            self.acks_tmp / f"{operation_id}.json.part",
        )

    def replay_deterministic(self) -> List[Dict[str, Any]]:
        self.validate_replay_order()
        published = self._list_published()
        published.sort(key=lambda t: int(t[1]["EVENT_SEQUENCE"]))
        return [obj for _, obj in published]
