"""Append-only ledger and derived projection for ``/opsx-sherlock``.

The helper owns only ``opsx/runs/sherlock/<case>/LEDGER.jsonl`` and the
derived ``INVESTIGATION.md`` projection.  It validates structural evidence
references and cause-status preconditions; it does not decide whether a fact
is true or whether evidence is logically discriminating.
"""

from __future__ import annotations

import argparse
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, cast


JsonObject = dict[str, object]
CASE_ID_PATTERN = re.compile(r"^SHK-[A-Z0-9][A-Z0-9._-]{2,127}$")
SCHEMA_VERSION = 1
RESERVED_FIELDS = {
    "SCHEMA_VERSION",
    "EVENT_SEQUENCE",
    "EVENT_ID",
    "PREVIOUS_EVENT_ID",
    "RECORDED_AT",
}
ACCESS_MODES = {
    "presence_check",
    "count_only",
    "normalized_summary",
    "revision_id_only",
}
ACQUISITION_SOURCES = {"repo", "opsx-devops", "external_owner_manual"}
ACQUISITION_STATUSES = {
    "FULFILLED",
    "REJECTED",
    "STALE_SUSPECTED",
}
HYPOTHESIS_STATUSES = {"OPEN", "SUPPORTED", "REFUTED", "OUT_OF_SCOPE"}


def _empty_str_list() -> list[str]:
    return []


def _empty_json_map() -> dict[str, JsonObject]:
    return {}


def _empty_hypothesis_map() -> dict[str, Hypothesis]:
    return {}


def _empty_acquisition_map() -> dict[str, Acquisition]:
    return {}


class LedgerError(ValueError):
    """Raised when a ledger event violates the Sherlock structural contract."""


@dataclass
class Hypothesis:
    """Current projection of one hypothesis."""

    statement: str
    status: str = "OPEN"
    supported_by: list[str] = field(default_factory=_empty_str_list)
    contradicted_by: list[str] = field(default_factory=_empty_str_list)
    scope_reason: str | None = None


@dataclass
class Acquisition:
    """Current projection of one evidence-acquisition request."""

    gap_id: str
    source: str
    required_authority: str
    access_mode: str
    redaction_constraints: str
    requested_at: str
    system_state_ref_at_request: str
    status: str = "PENDING"
    result_reason: str | None = None


@dataclass
class LedgerState:
    """Reconstructed Sherlock state derived only from ledger events."""

    case_id: str | None = None
    symptom: str | None = None
    linked_bug_case: str = "none"
    linked_finding: str = "none"
    facts: dict[str, JsonObject] = field(default_factory=_empty_json_map)
    hypotheses: dict[str, Hypothesis] = field(
        default_factory=_empty_hypothesis_map
    )
    gaps: dict[str, JsonObject] = field(default_factory=_empty_json_map)
    acquisitions: dict[str, Acquisition] = field(
        default_factory=_empty_acquisition_map
    )
    conclusion: JsonObject | None = None
    inconclusive_reason: str | None = None

    @property
    def status(self) -> str:
        """Return the investigation status implied by terminal ledger events."""

        if self.conclusion is not None:
            return "CAUSE_SUPPORTED"
        if self.inconclusive_reason is not None:
            return "INCONCLUSIVE"
        return "EVIDENCE_REQUIRED"


def _fail(message: str) -> NoReturn:
    raise LedgerError(message)


def _required_str(event: JsonObject, key: str) -> str:
    value = event.get(key)
    if not isinstance(value, str) or not value.strip():
        _fail(f"{key} must be a non-empty string")
    return value.strip()


def _optional_str(event: JsonObject, key: str, default: str = "none") -> str:
    value = event.get(key, default)
    if not isinstance(value, str) or not value.strip():
        _fail(f"{key} must be a non-empty string")
    return value.strip()


def _required_str_list(event: JsonObject, key: str) -> list[str]:
    value = event.get(key)
    if not isinstance(value, list):
        _fail(f"{key} must be a list of non-empty strings")
    result: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str) or not item.strip():
            _fail(f"{key} must be a list of non-empty strings")
        result.append(item.strip())
    return result


def _require_known_ids(
    values: list[str], known: set[str], field_name: str
) -> None:
    unknown = sorted(set(values) - known)
    if unknown:
        _fail(f"{field_name} contains unknown ids: {unknown}")


def _validate_envelope(
    event: JsonObject, expected_sequence: int, previous_event_id: str | None
) -> None:
    if event.get("SCHEMA_VERSION") != SCHEMA_VERSION:
        _fail("unsupported SCHEMA_VERSION")
    if event.get("EVENT_SEQUENCE") != expected_sequence:
        _fail(f"EVENT_SEQUENCE must be {expected_sequence}")
    event_id = _required_str(event, "EVENT_ID")
    if not event_id.startswith("SHK-EVT-"):
        _fail("EVENT_ID must start with SHK-EVT-")
    expected_previous = previous_event_id if previous_event_id is not None else "none"
    if event.get("PREVIOUS_EVENT_ID") != expected_previous:
        _fail("PREVIOUS_EVENT_ID breaks the append chain")
    _ = _required_str(event, "RECORDED_AT")


def _apply_event(state: LedgerState, event: JsonObject) -> None:
    event_type = _required_str(event, "EVENT_TYPE")
    if state.status in {"CAUSE_SUPPORTED", "INCONCLUSIVE"}:
        _fail("terminal investigation cannot accept more events")

    if event_type == "INVESTIGATION_STARTED":
        if state.case_id is not None:
            _fail("INVESTIGATION_STARTED may appear only once")
        case_id = _required_str(event, "SHERLOCK_CASE_ID")
        if CASE_ID_PATTERN.fullmatch(case_id) is None:
            _fail("invalid SHERLOCK_CASE_ID")
        state.case_id = case_id
        state.symptom = _required_str(event, "SYMPTOM")
        state.linked_bug_case = _optional_str(event, "LINKED_BUG_CASE")
        state.linked_finding = _optional_str(event, "LINKED_FINDING")
        _ = _required_str(event, "SYSTEM_STATE_REF")
        return

    if state.case_id is None:
        _fail("first ledger event must be INVESTIGATION_STARTED")

    if event_type == "FACT_RECORDED":
        fact_id = _required_str(event, "FACT_ID")
        if fact_id in state.facts:
            _fail(f"duplicate FACT_ID: {fact_id}")
        evidence_kind = _required_str(event, "EVIDENCE_KIND")
        if evidence_kind not in {"repo_path", "durable_artifact", "owner_external"}:
            _fail("EVIDENCE_KIND is not admissible")
        _ = _required_str(event, "STATEMENT")
        _ = _required_str(event, "EVIDENCE_REF")
        _ = _required_str(event, "OBSERVED_AT")
        _ = _required_str(event, "SYSTEM_STATE_REF")
        state.facts[fact_id] = event
        return

    if event_type == "HYPOTHESIS_RECORDED":
        hypothesis_id = _required_str(event, "HYPOTHESIS_ID")
        if hypothesis_id in state.hypotheses:
            _fail(f"duplicate HYPOTHESIS_ID: {hypothesis_id}")
        state.hypotheses[hypothesis_id] = Hypothesis(
            statement=_required_str(event, "STATEMENT")
        )
        return

    if event_type == "HYPOTHESIS_STATUS_CHANGED":
        hypothesis_id = _required_str(event, "HYPOTHESIS_ID")
        hypothesis = state.hypotheses.get(hypothesis_id)
        if hypothesis is None:
            _fail(f"unknown HYPOTHESIS_ID: {hypothesis_id}")
        status = _required_str(event, "STATUS")
        if status not in HYPOTHESIS_STATUSES - {"OPEN"}:
            _fail("hypothesis transition STATUS is invalid")
        if status == "SUPPORTED":
            supported_by = _required_str_list(event, "SUPPORTED_BY")
            if not supported_by:
                _fail("SUPPORTED hypothesis requires at least one FACT_ID")
            _require_known_ids(
                supported_by, set(state.facts), "SUPPORTED_BY"
            )
            hypothesis.supported_by = supported_by
            hypothesis.contradicted_by = []
            hypothesis.scope_reason = None
        elif status == "REFUTED":
            contradicted_by = _required_str_list(event, "CONTRADICTED_BY")
            if not contradicted_by:
                _fail("REFUTED hypothesis requires at least one FACT_ID")
            _require_known_ids(
                contradicted_by, set(state.facts), "CONTRADICTED_BY"
            )
            hypothesis.contradicted_by = contradicted_by
            hypothesis.scope_reason = None
        else:
            hypothesis.scope_reason = _required_str(event, "SCOPE_REASON")
        hypothesis.status = status
        return

    if event_type == "EVIDENCE_GAP_RECORDED":
        gap_id = _required_str(event, "GAP_ID")
        if gap_id in state.gaps:
            _fail(f"duplicate GAP_ID: {gap_id}")
        _ = _required_str(event, "QUESTION")
        blocks = _required_str_list(event, "BLOCKS")
        _require_known_ids(blocks, set(state.hypotheses), "BLOCKS")
        state.gaps[gap_id] = event
        return

    if event_type == "ACQUISITION_REQUESTED":
        request_id = _required_str(event, "REQUEST_ID")
        if request_id in state.acquisitions:
            _fail(f"duplicate REQUEST_ID: {request_id}")
        gap_id = _required_str(event, "EVIDENCE_GAP")
        if gap_id not in state.gaps:
            _fail(f"unknown EVIDENCE_GAP: {gap_id}")
        source = _required_str(event, "SOURCE")
        if source not in ACQUISITION_SOURCES:
            _fail("SOURCE is not allowed")
        access_mode = _required_str(event, "ACCESS_MODE")
        if access_mode not in ACCESS_MODES:
            _fail("ACCESS_MODE is not least-privilege allowlisted")
        state.acquisitions[request_id] = Acquisition(
            gap_id=gap_id,
            source=source,
            required_authority=_required_str(event, "REQUIRED_AUTHORITY"),
            access_mode=access_mode,
            redaction_constraints=_required_str(
                event, "REDACTION_CONSTRAINTS"
            ),
            requested_at=_required_str(event, "REQUESTED_AT"),
            system_state_ref_at_request=_required_str(
                event, "SYSTEM_STATE_REF_AT_REQUEST"
            ),
        )
        return

    if event_type == "ACQUISITION_STATUS_CHANGED":
        request_id = _required_str(event, "REQUEST_ID")
        acquisition = state.acquisitions.get(request_id)
        if acquisition is None:
            _fail(f"unknown REQUEST_ID: {request_id}")
        if acquisition.status != "PENDING":
            _fail(f"REQUEST_ID is already terminal: {request_id}")
        status = _required_str(event, "STATUS")
        if status not in ACQUISITION_STATUSES:
            _fail("acquisition STATUS is invalid")
        returned_state = _required_str(event, "SYSTEM_STATE_REF_RETURNED")
        requested_state = acquisition.system_state_ref_at_request
        if status == "FULFILLED":
            if (
                requested_state == "unknown"
                or returned_state == "unknown"
                or returned_state != requested_state
            ):
                _fail(
                    "unmatched system state must be STALE_SUSPECTED, "
                    "not FULFILLED"
                )
            fact_ids = _required_str_list(event, "EVIDENCE_FACT_IDS")
            if not fact_ids:
                _fail("FULFILLED acquisition requires EVIDENCE_FACT_IDS")
            _require_known_ids(
                fact_ids, set(state.facts), "EVIDENCE_FACT_IDS"
            )
        else:
            acquisition.result_reason = _required_str(event, "REASON")
        acquisition.status = status
        return

    if event_type == "INVESTIGATION_ENDED_INCONCLUSIVE":
        supported_count = sum(
            hypothesis.status == "SUPPORTED"
            for hypothesis in state.hypotheses.values()
        )
        if supported_count < 2:
            _fail("INCONCLUSIVE requires at least two SUPPORTED hypotheses")
        pending = [
            request_id
            for request_id, request in state.acquisitions.items()
            if request.status == "PENDING"
        ]
        if pending:
            _fail(f"INCONCLUSIVE cannot leave pending acquisitions: {pending}")
        state.inconclusive_reason = _required_str(event, "REASON")
        return

    if event_type == "CONCLUSION_RECORDED":
        supported = [
            hypothesis_id
            for hypothesis_id, hypothesis in state.hypotheses.items()
            if hypothesis.status == "SUPPORTED"
        ]
        unresolved = [
            hypothesis_id
            for hypothesis_id, hypothesis in state.hypotheses.items()
            if hypothesis.status not in {"SUPPORTED", "REFUTED", "OUT_OF_SCOPE"}
        ]
        if len(supported) != 1 or unresolved:
            _fail(
                "CAUSE_SUPPORTED requires exactly one SUPPORTED hypothesis "
                "and no OPEN siblings"
            )
        supported_by = _required_str_list(event, "SUPPORTED_BY")
        known_ids = set(state.facts) | set(state.hypotheses)
        _require_known_ids(supported_by, known_ids, "SUPPORTED_BY")
        if supported[0] not in supported_by:
            _fail("conclusion must cite the sole SUPPORTED hypothesis")
        if not set(supported_by).intersection(state.facts):
            _fail("conclusion must cite at least one FACT_ID")
        _ = _required_str(event, "ROOT_CAUSE")
        state.conclusion = event
        return

    _fail(f"unsupported EVENT_TYPE: {event_type}")


def read_ledger(ledger_path: Path) -> tuple[list[JsonObject], LedgerState]:
    """Read and fully validate an existing ledger."""

    events: list[JsonObject] = []
    state = LedgerState()
    if not ledger_path.exists():
        return events, state
    previous_event_id: str | None = None
    for line_number, raw_line in enumerate(
        ledger_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            _fail(f"blank line in ledger at {line_number}")
        try:
            parsed: object = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise LedgerError(
                f"invalid JSON at ledger line {line_number}: {error}"
            ) from error
        if not isinstance(parsed, dict):
            _fail(f"ledger line {line_number} is not an object")
        parsed_object = cast(dict[object, object], parsed)
        event: JsonObject = {
            str(key): value for key, value in parsed_object.items()
        }
        _validate_envelope(event, line_number, previous_event_id)
        _apply_event(state, event)
        previous_event_id = _required_str(event, "EVENT_ID")
        events.append(event)
    return events, state


def _render_list(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def render_projection(events: list[JsonObject], state: LedgerState) -> str:
    """Render the human-readable investigation from reconstructed state."""

    if state.case_id is None or state.symptom is None:
        _fail("cannot project an uninitialized ledger")
    lines = [
        "# Sherlock Investigation",
        "",
        "GENERATED_FROM: LEDGER.jsonl",
        f"EVENT_COUNT: {len(events)}",
        f"SHERLOCK_CASE_ID: {state.case_id}",
        f"STATUS: {state.status}",
        f"SYMPTOM: {state.symptom}",
        f"LINKED_BUG_CASE: {state.linked_bug_case}",
        f"LINKED_FINDING: {state.linked_finding}",
        "",
        "## FACTS",
    ]
    if state.facts:
        for fact_id, fact in state.facts.items():
            lines.append(
                f"- {fact_id}: {_required_str(fact, 'STATEMENT')} "
                f"(evidence: {_required_str(fact, 'EVIDENCE_REF')})"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## HYPOTHESES"])
    if state.hypotheses:
        for hypothesis_id, hypothesis in state.hypotheses.items():
            lines.extend(
                [
                    f"- {hypothesis_id} [{hypothesis.status}]: "
                    f"{hypothesis.statement}",
                    f"  - supported_by: "
                    f"{_render_list(hypothesis.supported_by)}",
                    f"  - contradicted_by: "
                    f"{_render_list(hypothesis.contradicted_by)}",
                ]
            )
            if hypothesis.scope_reason is not None:
                lines.append(f"  - scope_reason: {hypothesis.scope_reason}")
    else:
        lines.append("- none")
    lines.extend(["", "## EVIDENCE_GAPS"])
    if state.gaps:
        for gap_id, gap in state.gaps.items():
            lines.append(
                f"- {gap_id}: {_required_str(gap, 'QUESTION')} "
                f"(blocks: {_render_list(_required_str_list(gap, 'BLOCKS'))})"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## ACQUISITION_REQUESTS"])
    if state.acquisitions:
        for request_id, request in state.acquisitions.items():
            lines.append(
                f"- {request_id} [{request.status}]: "
                f"{request.source}/{request.access_mode}; gap={request.gap_id}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## CONCLUSION"])
    if state.conclusion is not None:
        lines.extend(
            [
                f"- root_cause: "
                f"{_required_str(state.conclusion, 'ROOT_CAUSE')}",
                f"- supported_by: "
                f"{_render_list(_required_str_list(state.conclusion, 'SUPPORTED_BY'))}",
            ]
        )
    elif state.inconclusive_reason is not None:
        lines.append(f"- inconclusive: {state.inconclusive_reason}")
    else:
        lines.append("- root_cause: not established")
    return "\n".join(lines) + "\n"


def write_projection(case_dir: Path) -> LedgerState:
    """Rebuild ``INVESTIGATION.md`` exclusively from ``LEDGER.jsonl``."""

    ledger_path = case_dir / "LEDGER.jsonl"
    events, state = read_ledger(ledger_path)
    body = render_projection(events, state)
    projection_path = case_dir / "INVESTIGATION.md"
    temporary_path = case_dir / ".INVESTIGATION.md.tmp"
    _ = temporary_path.write_text(body, encoding="utf-8", newline="\n")
    _ = temporary_path.replace(projection_path)
    return state


def append_event(case_dir: Path, event_payload: JsonObject) -> JsonObject:
    """Validate and append one event, then rebuild the derived projection."""

    case_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = case_dir / "LEDGER.jsonl"
    events, state = read_ledger(ledger_path)
    conflicting = RESERVED_FIELDS.intersection(event_payload)
    if conflicting:
        _fail(f"payload contains reserved fields: {sorted(conflicting)}")
    event: JsonObject = dict(event_payload)
    event["SCHEMA_VERSION"] = SCHEMA_VERSION
    event["EVENT_SEQUENCE"] = len(events) + 1
    event["EVENT_ID"] = f"SHK-EVT-{uuid.uuid4().hex}"
    event["PREVIOUS_EVENT_ID"] = (
        _required_str(events[-1], "EVENT_ID") if events else "none"
    )
    event["RECORDED_AT"] = datetime.now(UTC).isoformat()
    _validate_envelope(
        event,
        len(events) + 1,
        _required_str(events[-1], "EVENT_ID") if events else None,
    )
    _apply_event(state, event)
    serialized = json.dumps(event, ensure_ascii=True, separators=(",", ":"))
    with ledger_path.open("a", encoding="utf-8", newline="\n") as ledger:
        _ = ledger.write(serialized + "\n")
    _ = write_projection(case_dir)
    return event


def resolve_case_dir(repo_root: Path, sherlock_case: str) -> Path:
    """Resolve and constrain a Sherlock case directory below the owned root."""

    if CASE_ID_PATTERN.fullmatch(sherlock_case) is None:
        _fail("invalid SHERLOCK_CASE_ID")
    root = (repo_root / "opsx" / "runs" / "sherlock").resolve()
    case_dir = (root / sherlock_case).resolve()
    if case_dir.parent != root:
        _fail("Sherlock case path escapes the owned root")
    return case_dir


def _read_event_file(path: Path) -> JsonObject:
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise LedgerError(f"invalid event JSON: {error}") from error
    if not isinstance(parsed, dict):
        _fail("event payload must be a JSON object")
    parsed_object = cast(dict[object, object], parsed)
    return {str(key): value for key, value in parsed_object.items()}


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for append, project, and validate."""

    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "action", choices=("append", "project", "validate")
    )
    _ = parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    _ = parser.add_argument("--sherlock-case", required=True)
    _ = parser.add_argument("--event-file", type=Path)
    return parser


def main() -> int:
    """Run one bounded ledger operation and report only non-sensitive metadata."""

    args = build_parser().parse_args()
    case_dir = resolve_case_dir(args.repo_root, args.sherlock_case)
    if args.action == "append":
        if args.event_file is None:
            _fail("--event-file is required for append")
        event = append_event(case_dir, _read_event_file(args.event_file))
        print(
            f"APPENDED sequence={event['EVENT_SEQUENCE']} "
            f"status={write_projection(case_dir).status}"
        )
    elif args.action == "project":
        print(f"PROJECTED status={write_projection(case_dir).status}")
    else:
        _, state = read_ledger(case_dir / "LEDGER.jsonl")
        print(f"VALID status={state.status}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LedgerError as error:
        raise SystemExit(f"SHERLOCK_LEDGER_ERROR: {error}") from error
