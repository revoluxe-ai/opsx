"""Построение строк реестра и manifest из разобранного контракта."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator


@dataclass(frozen=True)
class RegistryRow:
    """Одна строка transition registry (структурный baseline + §2 classification)."""

    transition_id: str
    base_sha: str
    source_state: str
    declared_trigger: str
    declared_guard: str | None
    declared_target: str
    json_pointer: str
    runtime_path_primary: str
    evidence_status: str
    gap: str
    runtime_paths: tuple[str, ...] = ()
    python_preempt_ref: str | None = None
    handler_adapter_ref: str | None = None

    def to_json_dict(self) -> dict[str, str | None | list[str]]:
        """Сериализация в JSON object для jsonl."""

        out: dict[str, str | None | list[str]] = {
            "transition_id": self.transition_id,
            "base_sha": self.base_sha,
            "source_state": self.source_state,
            "declared_trigger": self.declared_trigger,
            "declared_guard": self.declared_guard,
            "declared_target": self.declared_target,
            "json_pointer": self.json_pointer,
            "runtime_path_primary": self.runtime_path_primary,
            "evidence_status": self.evidence_status,
            "gap": self.gap,
        }
        if self.runtime_paths:
            out["runtime_paths"] = list(self.runtime_paths)
        if self.python_preempt_ref is not None:
            out["python_preempt_ref"] = self.python_preempt_ref
        if self.handler_adapter_ref is not None:
            out["handler_adapter_ref"] = self.handler_adapter_ref
        return out


def transition_id_for(state_id: str, ordinal: int) -> str:
    """Стабильный ID ``{state_id}::{ordinal}`` (0-based)."""

    return f"{state_id}::{ordinal}"


def json_pointer_for(state_id: str, ordinal: int) -> str:
    """JSON Pointer к объявлению перехода в vendored контракте."""

    return f"/states/{state_id}/transitions/{ordinal}"


def iter_registry_rows(contract: dict[str, Any], base_sha: str) -> Iterator[RegistryRow]:
    """Обойти все declared transitions в ``contract.states`` в детерминированном порядке.

    Args:
        contract: корневой объект ux_state_contract.json.
        base_sha: pin ревизии для поля base_sha в записях.

    Yields:
        RegistryRow для каждого элемента transitions[].
    """

    states = contract["states"]
    for state_id in sorted(states.keys()):
        state_obj = states[state_id]
        if not isinstance(state_obj, dict):
            raise ValueError(f"state {state_id!r} must be an object")
        raw_transitions = state_obj.get("transitions")
        transitions: list[Any]
        if raw_transitions is None:
            transitions = []
        elif isinstance(raw_transitions, list):
            transitions = raw_transitions
        else:
            raise ValueError(f"state {state_id!r}.transitions must be array or absent")
        for ordinal, tr in enumerate(transitions):
            if not isinstance(tr, dict):
                raise ValueError(
                    f"transition at {state_id}::{ordinal} must be an object"
                )
            trigger = tr.get("trigger")
            target = tr.get("target")
            if not isinstance(trigger, str) or not isinstance(target, str):
                raise ValueError(
                    f"transition {state_id}::{ordinal} requires string trigger and target"
                )
            guard_val = tr.get("guard")
            declared_guard: str | None
            if guard_val is None:
                declared_guard = None
            elif isinstance(guard_val, str):
                declared_guard = guard_val
            else:
                raise ValueError(
                    f"transition {state_id}::{ordinal} guard must be string or null"
                )
            yield RegistryRow(
                transition_id=transition_id_for(state_id, ordinal),
                base_sha=base_sha,
                source_state=state_id,
                declared_trigger=trigger,
                declared_guard=declared_guard,
                declared_target=target,
                json_pointer=json_pointer_for(state_id, ordinal),
                runtime_path_primary="UNMAPPED_AT_BASELINE",
                evidence_status="UNVERIFIED",
                gap="structural export only; runtime_path classification deferred to tasks §2",
            )


def state_ids_from_contract(contract: dict[str, Any]) -> list[str]:
    """Отсортированный список ключей ``states`` (включая нулевые transitions)."""

    return sorted(contract["states"].keys())


def expected_transition_ids(contract: dict[str, Any]) -> list[str]:
    """Список transition_id без привязки к base_sha в строках."""

    ids: list[str] = []
    states = contract["states"]
    for state_id in sorted(states.keys()):
        transitions = states[state_id].get("transitions") or []
        for ordinal in range(len(transitions)):
            ids.append(transition_id_for(state_id, ordinal))
    return sorted(ids)


def count_transitions(contract: dict[str, Any]) -> int:
    """Число declared transitions в контракте."""

    total = 0
    for state_id in contract["states"]:
        total += len(contract["states"][state_id].get("transitions") or [])
    return total
