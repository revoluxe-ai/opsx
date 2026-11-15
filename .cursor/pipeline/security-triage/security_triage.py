"""Target-native security-signal triage for ``/opsx-security-triage``.

Единственный writer ``SECURITY_FINDING_PACKET`` под
``opsx/runs/security-cases/<case>/findings/``. Подтверждение требует
цитаты, найденной в containment-проверенном файле. Уверенность модели
доказательством не является. Объявленного adapter capability
``security_triage`` на target нет: локальный evidence path частичный
(``CAPABILITY_PARITY: PARTIAL``), adapter не изобретается.

Пакет не является archive security verdict и не пишет bug ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import NoReturn, cast


CONTRACT_ID = "OPSX-WB-SECURITY-TRIAGE-01"
SCHEMA_VERSION = "target-security-finding-packet-v1"
MAX_EVIDENCE_BYTES = 1024 * 1024
SOURCE_KINDS = {
    "CURSOR_AGENTIC_SECURITY_REVIEW",
    "PR_SECURITY_REVIEW",
    "CI_SECURITY_SIGNAL",
    "MANUAL_SECURITY_REVIEW",
    "USER_SECURITY_SIGNAL",
    "OTHER",
}
PLANES = {"PRODUCT", "CONTROL_PLANE", "INFRASTRUCTURE"}
ARCHIVE_VERDICTS = {"PASS", "PASS_WITH_RISK", "FAIL"}
CASE_ID_PATTERN = re.compile(r"^SEC-CASE-[A-Z0-9][A-Z0-9-]{2,80}$")
FINDING_ID_PATTERN = re.compile(r"^SEC-FIND-[A-Z0-9][A-Z0-9-]{2,80}$")
CHANGE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
PACKET_BEGIN = "BEGIN_SECURITY_FINDING_PACKET\n"
PACKET_END = "\nEND_SECURITY_FINDING_PACKET\n"
CONTRACT_FIELDS = (
    "security_class",
    "threat",
    "attack_precondition",
    "causal_path",
    "impact",
    "security_invariant",
    "remediation_requirement",
    "retest_requirement",
)
LOCAL_ORIGIN_QUOTES = ('"localhost" in', '"127.0.0.1" in')
LOCAL_ORIGIN_CONTRACT = {
    "security_class": "origin_trust",
    "severity": "MEDIUM",
    "threat": (
        "attacker/non-local hostname can satisfy a local-origin trust predicate"
    ),
    "attack_precondition": (
        "the configured/provided origin reaches this guard with a crafted or "
        "misconfigured non-local hostname"
    ),
    "causal_path": (
        "substring containment of localhost or 127.0.0.1 accepts a non-local "
        "hostname such as localhost.attacker.tld"
    ),
    "impact": (
        "local-only payment/sandbox behavior may be exposed outside the "
        "intended local boundary"
    ),
    "security_invariant": (
        "local-origin trust MUST be based on parsed hostname identity / "
        "explicit loopback semantics, never substring containment"
    ),
    "remediation_requirement": (
        "replace substring-based local-host recognition with strict "
        "parsed-host classification according to approved local-origin policy"
    ),
    "retest_requirement": (
        "negative cases including localhost.attacker.tld and "
        "127.0.0.1.attacker.tld MUST NOT classify as local; legitimate "
        "approved localhost/loopback forms must retain intended behavior"
    ),
}
DO_NOT_DO_YET = (
    "Do not treat triage as archive security PASS / FAIL; "
    "do not write verify/SECURITY-REVIEW.md; "
    "do not invent /opsx-security-fix; "
    "do not open opsx/runs/bug-cases; "
    "do not edit product or OpenSpec planning; "
    "do not probe production."
)
_PATH_EQ = re.compile(r"""path=("?)([^"\s]+)\1""")
_WIN_ABS = re.compile(r"[A-Za-z]:\\[^\s\"']+")
_DOTDOT = re.compile(r"(?:\.\./|\.\.\\)[^\s\"']+")
_REPOISH = re.compile(
    r"(?:app|tests|k8s|dockers|\.cursor|\opsx|openspec)/[A-Za-z0-9_./\\-]+"
)


class SecurityTriageError(ValueError):
    """Нарушение контракта triage, которое нельзя превратить в verdict."""


def disposition_if_packet_missing() -> NoReturn:
    """Отсутствующий packet не является disposition.

    Raises:
        SecurityTriageError: всегда, с кодом ``ABSENCE_IS_NOT_A_DISPOSITION``.
    """

    raise SecurityTriageError("ABSENCE_IS_NOT_A_DISPOSITION")


def normalize_signal(text: str) -> str:
    """Схлопнуть пробелы и переводы строк в стабильный сигнал."""

    return " ".join(text.replace("\r\n", "\n").split()).strip()


def source_signal_hash(
    *,
    source: str,
    source_ref: str,
    path: str,
    symbol: str,
    severity: str,
    signal: str,
) -> str:
    """Стабильный sha256 идентичности сигнала.

    Вход: ``source | source_ref | path | symbol | severity | normalized signal``.
    """

    payload = "|".join(
        (
            source.strip(),
            source_ref.strip(),
            path.strip().replace("\\", "/"),
            symbol.strip(),
            severity.strip(),
            normalize_signal(signal),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def finding_id_for(signal_hash: str) -> str:
    """Производный id finding; один hash — один id."""

    return f"SEC-FIND-{signal_hash[:24].upper()}"


def case_id_for(signal_hash: str, requested: str) -> str:
    """Вернуть запрошенный case id или стабильный id из hash."""

    if requested:
        if not CASE_ID_PATTERN.fullmatch(requested):
            raise SecurityTriageError("invalid_case_id")
        return requested
    return f"SEC-CASE-{signal_hash[:16].upper()}"


def canonical_contained(candidate: Path, approved_root: Path) -> Path | None:
    """Канонический путь, только если он внутри root с учётом separator.

    Совпадение голого строкового префикса соседнего каталога
    (``root`` vs ``root_evil``) отклоняется. Symlink/junction
    проверяется по resolved target.
    """

    root = approved_root.resolve()
    raw = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = raw.resolve()
    except OSError:
        return None
    root_text = os.path.normcase(str(root))
    resolved_text = os.path.normcase(str(resolved))
    if resolved_text == root_text:
        return resolved
    prefix = root_text + os.sep
    if resolved_text.startswith(prefix):
        return resolved
    return None


def triage(repo_root: Path, request: dict[str, object]) -> dict[str, object]:
    """Разобрать один сигнал и, только при CONFIRMED, материализовать packet.

    Args:
        repo_root: корень workspace; единственный approved root.
        request: один JSON-сигнал (source, signal, цитаты, контракт).

    Returns:
        Явный disposition. ``CONFIRMED`` только вместе с записанным packet.
    """

    try:
        return _triage(repo_root.resolve(), request)
    except SecurityTriageError as error:
        return _status(
            status="BLOCKED",
            reason=str(error),
            source="",
            signal_hash="",
            packet_state="absent",
        )


def _triage(repo_root: Path, request: dict[str, object]) -> dict[str, object]:
    project = _load_projectization(repo_root)
    if project is not None:
        return project

    from opsx_runtime_gates import adoption_block_for_pipeline

    def _adoption_blocked(reason: str, **extra: object) -> dict[str, object]:
        return _status(
            status="BLOCKED",
            reason=reason,
            source="",
            signal_hash="",
            packet_state="absent",
        )

    adoption = adoption_block_for_pipeline(
        repo_root,
        "python.pipeline.security_triage",
        blocked_factory=_adoption_blocked,
    )
    if adoption is not None:
        return adoption

    source = _required_token(request, "source", SOURCE_KINDS, "bad_source_kind")
    if isinstance(source, dict):
        return source
    signal = _optional_str(request, "signal")
    if not signal.strip():
        return _status(
            status="BLOCKED",
            reason="missing_signal",
            source=source,
            signal_hash="",
            packet_state="absent",
        )
    source_note = _optional_str(request, "source_note")
    if source == "OTHER" and not source_note:
        return _status(
            status="BLOCKED",
            reason="missing_source_note",
            source=source,
            signal_hash="",
            packet_state="absent",
        )

    source_ref = _optional_str(request, "source_ref")
    symbol = _optional_str(request, "symbol")
    severity = _optional_str(request, "severity")
    declared_plane = _optional_str(request, "declared_plane")
    if declared_plane and declared_plane not in PLANES:
        return _status(
            status="BLOCKED",
            reason="bad_plane",
            source=source,
            signal_hash="",
            packet_state="absent",
        )

    candidates = _candidate_paths(request, signal)
    rejected = _first_uncontained(repo_root, candidates)
    if rejected is not None:
        return _status(
            status="BLOCKED",
            reason="path_outside_workspace",
            source=source,
            signal_hash=source_signal_hash(
                source=source,
                source_ref=source_ref,
                path=rejected,
                symbol=symbol,
                severity=severity,
                signal=signal,
            ),
            packet_state="absent",
            extra={"REJECTED_PATH": rejected},
        )

    primary = _primary_cited(request)
    relative = _relative_posix(repo_root, primary) if primary else ""
    signal_hash = source_signal_hash(
        source=source,
        source_ref=source_ref,
        path=relative,
        symbol=symbol,
        severity=severity,
        signal=signal,
    )
    duplicate = _find_duplicate(repo_root, signal_hash)
    if duplicate is not None:
        return _status(
            status="DUPLICATE",
            reason="existing_finding",
            source=source,
            signal_hash=signal_hash,
            packet_state="reused_duplicate",
            case_id=str(duplicate["SECURITY_CASE_ID"]),
            finding_id=str(duplicate["SECURITY_FINDING_ID"]),
            evidence_path=str(duplicate["EVIDENCE_PATH"]),
            plane=str(duplicate.get("AFFECTED_PLANE", "n/a")),
        )

    if not primary:
        authority = _optional_str(request, "out_of_scope_authority")
        if authority:
            return _status(
                status="OUT_OF_SCOPE",
                reason="named_authority",
                source=source,
                signal_hash=signal_hash,
                packet_state="absent",
                extra={"AUTHORITY": authority},
            )
        return _status(
            status="NEEDS_CONTEXT",
            reason="missing_cited_path",
            source=source,
            signal_hash=signal_hash,
            packet_state="absent",
        )

    resolved = canonical_contained(Path(primary), repo_root)
    if resolved is None or not resolved.is_file():
        return _status(
            status="NEEDS_CONTEXT",
            reason="evidence_absent",
            source=source,
            signal_hash=signal_hash,
            packet_state="absent",
            extra={"PATH": relative},
        )

    plane = _classify_plane(relative)
    planes = _planes_of(repo_root, _explicit_paths(request))
    if len(planes) > 1 or (declared_plane and plane and declared_plane != plane):
        return _status(
            status="BLOCKED",
            reason="plane_misroute",
            source=source,
            signal_hash=signal_hash,
            packet_state="absent",
            plane=declared_plane or plane or "n/a",
            extra={"CLASSIFIED_PLANE": plane or "unclassified"},
        )
    if declared_plane and plane is None:
        return _status(
            status="BLOCKED",
            reason="plane_misroute",
            source=source,
            signal_hash=signal_hash,
            packet_state="absent",
            plane=declared_plane,
            extra={"CLASSIFIED_PLANE": "unclassified"},
        )

    text, truncated = _read_evidence(resolved)
    evidence_quotes = _str_list(request, "evidence_quotes")
    disproof_quotes = _str_list(request, "disproof_quotes")
    evidence_hit = [quote for quote in evidence_quotes if quote in text]
    evidence_miss = [quote for quote in evidence_quotes if quote not in text]
    disproof_hit = [quote for quote in disproof_quotes if quote in text]
    signal_mentions_local = (
        "localhost" in signal.lower() or "127.0.0.1" in signal
    )
    local_origin = signal_mentions_local and all(
        quote in text for quote in LOCAL_ORIGIN_QUOTES
    )

    if truncated and (evidence_miss or not evidence_quotes):
        return _status(
            status="NEEDS_CONTEXT",
            reason="evidence_read_budget",
            source=source,
            signal_hash=signal_hash,
            packet_state="absent",
            extra={"PATH": relative},
        )

    contract = _resolve_contract(
        request=request,
        severity=severity,
        local_origin=local_origin,
        evidence_hit=evidence_hit,
        evidence_requested=evidence_quotes,
    )
    if contract is None:
        if disproof_quotes and len(disproof_hit) == len(disproof_quotes) and not evidence_hit:
            return _status(
                status="FALSE_POSITIVE",
                reason="causal_path_disproven",
                source=source,
                signal_hash=signal_hash,
                packet_state="absent",
                extra={"PATH": relative},
            )
        authority = _optional_str(request, "out_of_scope_authority")
        if authority and not evidence_hit:
            return _status(
                status="OUT_OF_SCOPE",
                reason="named_authority",
                source=source,
                signal_hash=signal_hash,
                packet_state="absent",
                extra={"AUTHORITY": authority, "PATH": relative},
            )
        reason = "evidence_absent" if not evidence_hit else "incomplete_contract"
        if evidence_quotes and evidence_miss and not evidence_hit:
            reason = "evidence_not_in_cited_path"
        return _status(
            status="NEEDS_CONTEXT",
            reason=reason,
            source=source,
            signal_hash=signal_hash,
            packet_state="absent",
            extra={"PATH": relative, "CONFIDENCE": _optional_str(request, "confidence")},
        )

    if plane is None:
        return _status(
            status="NEEDS_CONTEXT",
            reason="plane_unclassified",
            source=source,
            signal_hash=signal_hash,
            packet_state="absent",
            extra={"PATH": relative},
        )

    quotes = evidence_hit or list(LOCAL_ORIGIN_QUOTES)
    try:
        case_id = case_id_for(signal_hash, _optional_str(request, "case_id"))
    except SecurityTriageError as error:
        return _status(
            status="BLOCKED",
            reason=str(error),
            source=source,
            signal_hash=signal_hash,
            packet_state="absent",
        )
    finding_id = finding_id_for(signal_hash)
    route = _route(
        plane=plane,
        repo_root=repo_root,
        change_id=_optional_str(request, "change_id"),
        surface=relative,
        finding_id=finding_id,
    )
    packet = _build_packet(
        case_id=case_id,
        finding_id=finding_id,
        source=source,
        source_ref=source_ref,
        signal_hash=signal_hash,
        plane=plane,
        surface=relative,
        symbol=symbol,
        text=text,
        quotes=quotes,
        contract=contract,
        confidence=_optional_str(request, "confidence"),
        route=route,
    )
    schema_error = _schema_gaps(packet)
    if schema_error:
        return _status(
            status="BLOCKED",
            reason=schema_error,
            source=source,
            signal_hash=signal_hash,
            packet_state="absent",
        )
    written = _materialize(repo_root, packet)
    if written.get("STATUS") != "CONFIRMED":
        return written
    return written


def _load_projectization(repo_root: Path) -> dict[str, object] | None:
    path = repo_root / "opsx" / "projectization.yaml"
    resolved = canonical_contained(path, repo_root)
    if resolved is None or not resolved.is_file():
        return _status(
            status="BLOCKED",
            reason="OPSX_PROJECTIZATION_REQUIRED",
            source="",
            signal_hash="",
            packet_state="absent",
        )
    text = resolved.read_text(encoding="utf-8")
    status = _yaml_scalar(text, "status")
    enabled = _yaml_scalar(text, "normal_commands_enabled")
    if status != "COMPLETE" or enabled not in {"true", "True", "yes"}:
        return _status(
            status="BLOCKED",
            reason="OPSX_PROJECTIZATION_REQUIRED",
            source="",
            signal_hash="",
            packet_state="absent",
        )
    return None


def _yaml_scalar(text: str, key: str) -> str:
    prefix = f"{key}:"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def _required_token(
    request: dict[str, object], key: str, allowed: set[str], reason: str
) -> str | dict[str, object]:
    value = _optional_str(request, key)
    if value not in allowed:
        return _status(
            status="BLOCKED",
            reason=reason,
            source=value,
            signal_hash="",
            packet_state="absent",
        )
    return value


def _optional_str(request: dict[str, object], key: str) -> str:
    value = request.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SecurityTriageError(f"bad_field:{key}")
    return value.strip()


def _str_list(request: dict[str, object], key: str) -> list[str]:
    value = request.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise SecurityTriageError(f"bad_field:{key}")
    quotes: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str) or not item:
            raise SecurityTriageError(f"bad_field:{key}")
        quotes.append(item)
    return quotes


def _candidate_paths(request: dict[str, object], signal: str) -> list[str]:
    found: list[str] = []

    def add(value: str) -> None:
        cleaned = value.strip().strip('"').strip("'")
        if cleaned and cleaned not in found:
            found.append(cleaned)

    primary = request.get("cited_path", "")
    if isinstance(primary, str):
        add(primary)
    extra = request.get("cited_paths", [])
    if isinstance(extra, list):
        for item in cast(list[object], extra):
            if isinstance(item, str):
                add(item)
    for match in _PATH_EQ.finditer(signal):
        add(match.group(2))
    for pattern in (_WIN_ABS, _DOTDOT, _REPOISH):
        for match in pattern.finditer(signal):
            add(match.group(0))
    return found


def _first_uncontained(repo_root: Path, candidates: list[str]) -> str | None:
    for candidate in candidates:
        if canonical_contained(Path(candidate), repo_root) is None:
            return candidate
    return None


def _explicit_paths(request: dict[str, object]) -> list[str]:
    """Пути, которые caller назвал поверхностью, а не упоминания в прозе."""

    found: list[str] = []
    primary = request.get("cited_path", "")
    if isinstance(primary, str) and primary.strip():
        found.append(primary.strip())
    extra = request.get("cited_paths", [])
    if isinstance(extra, list):
        for item in cast(list[object], extra):
            if isinstance(item, str) and item.strip() and item.strip() not in found:
                found.append(item.strip())
    return found


def _primary_cited(request: dict[str, object]) -> str:
    primary = request.get("cited_path", "")
    if isinstance(primary, str) and primary.strip():
        return primary.strip()
    extra = request.get("cited_paths", [])
    if isinstance(extra, list):
        for item in cast(list[object], extra):
            if isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def _relative_posix(repo_root: Path, candidate: str) -> str:
    resolved = canonical_contained(Path(candidate), repo_root)
    if resolved is None:
        return candidate.replace("\\", "/")
    try:
        relative = resolved.relative_to(repo_root.resolve())
    except ValueError:
        return candidate.replace("\\", "/")
    return relative.as_posix()


def _classify_plane(relative: str) -> str | None:
    norm = relative.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    norm = norm.lstrip("/")
    if norm.startswith(".cursor/") or norm.startswith("opsx/"):
        return "CONTROL_PLANE"
    if norm.startswith("app/"):
        return "PRODUCT"
    if norm.startswith("k8s/") or norm.startswith("dockers/"):
        return "INFRASTRUCTURE"
    if norm in {"docker-compose.yaml", "docker-compose.yml"}:
        return "INFRASTRUCTURE"
    return None


def _planes_of(repo_root: Path, candidates: list[str]) -> set[str]:
    planes: set[str] = set()
    for candidate in candidates:
        relative = _relative_posix(repo_root, candidate)
        plane = _classify_plane(relative)
        if plane is not None:
            planes.add(plane)
    return planes


def _read_evidence(path: Path) -> tuple[str, bool]:
    size = path.stat().st_size
    data = path.read_bytes()[:MAX_EVIDENCE_BYTES]
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
    return text, size > MAX_EVIDENCE_BYTES


def _resolve_contract(
    *,
    request: dict[str, object],
    severity: str,
    local_origin: bool,
    evidence_hit: list[str],
    evidence_requested: list[str],
) -> dict[str, str] | None:
    supplied = {key: _optional_str(request, key) for key in CONTRACT_FIELDS}
    filled = [value for value in supplied.values() if value]
    if filled and len(filled) != len(CONTRACT_FIELDS):
        return None
    if len(filled) == len(CONTRACT_FIELDS):
        if not severity:
            return None
        if evidence_requested and len(evidence_hit) != len(evidence_requested):
            return None
        if not evidence_hit:
            return None
        supplied["severity"] = severity
        return supplied
    if local_origin:
        contract = dict(LOCAL_ORIGIN_CONTRACT)
        if severity:
            contract["severity"] = severity
        return contract
    return None


def _route(
    *,
    plane: str,
    repo_root: Path,
    change_id: str,
    surface: str,
    finding_id: str,
) -> dict[str, str]:
    if plane == "PRODUCT":
        bound = _change_is_bound(repo_root, change_id)
        if bound:
            return {
                "REMEDIATION_AUTHORITY_CLASS": "PRODUCT_CHANGE",
                "BINDING_CLASS": "PRODUCT_CHANGE",
                "BINDING_ID": change_id,
                "WRITE_AUTHORITY": "/opsx-update",
                "REMEDIATION_ROUTE": "UPDATE",
                "NEXT_COMMAND": "/opsx-update",
                "NEXT_ARGS": f"change={change_id} security_finding={finding_id}",
                "EXPECTED_VERIFIER": "none",
                "VERIFIER_GAP": "opsx-security-review_not_runnable",
                "CHANGE_ID": change_id,
                "CHANGE_STATUS": "BOUND",
                "NEXT_ACTION": (
                    f"/opsx-update change={change_id} security_finding={finding_id}"
                ),
            }
        return {
            "REMEDIATION_AUTHORITY_CLASS": "OWNER_DECISION",
            "BINDING_CLASS": "NONE",
            "BINDING_ID": "none",
            "WRITE_AUTHORITY": "none",
            "REMEDIATION_ROUTE": "NEEDS_OWNER",
            "NEXT_COMMAND": "none",
            "NEXT_ARGS": "",
            "EXPECTED_VERIFIER": "none",
            "VERIFIER_GAP": "opsx-security-review_not_runnable",
            "CHANGE_ID": "UNBOUND",
            "CHANGE_STATUS": "NEEDS_CHANGE_BINDING",
            "NEXT_ACTION": "identify or create the owning Change; no product write",
        }
    if plane == "CONTROL_PLANE":
        return {
            "REMEDIATION_AUTHORITY_CLASS": "PROCESS_COMMAND_REPAIR",
            "BINDING_CLASS": "PROCESS_CHANGE",
            "BINDING_ID": surface,
            "WRITE_AUTHORITY": "/opsx-meta-fix-command-defects",
            "REMEDIATION_ROUTE": "PROCESS_REPAIR",
            "NEXT_COMMAND": "/opsx-meta-fix-command-defects",
            "NEXT_ARGS": "",
            "EXPECTED_VERIFIER": "/opsx-control-plane-security-review",
            "VERIFIER_GAP": "none",
            "CHANGE_ID": "n/a",
            "CHANGE_STATUS": "n/a",
            "NEXT_ACTION": (
                "/opsx-meta-fix-command-defects "
                "(Owner AUTHORIZE_COMMAND_FIX still required before writes)"
            ),
        }
    return {
        "REMEDIATION_AUTHORITY_CLASS": "BLOCKED_AUTHORITY_GAP",
        "BINDING_CLASS": "NONE",
        "BINDING_ID": "none",
        "WRITE_AUTHORITY": "none",
        "REMEDIATION_ROUTE": "BLOCKED",
        "NEXT_COMMAND": "none",
        "NEXT_ARGS": "",
        "EXPECTED_VERIFIER": "none",
        "VERIFIER_GAP": "infra_writer_not_proven",
        "CHANGE_ID": "n/a",
        "CHANGE_STATUS": "n/a",
        "NEXT_ACTION": "none",
    }


def _change_is_bound(repo_root: Path, change_id: str) -> bool:
    if not change_id or not CHANGE_ID_PATTERN.fullmatch(change_id):
        return False
    if ".." in change_id.split("."):
        return False
    candidate = repo_root / "openspec" / "changes" / change_id
    resolved = canonical_contained(candidate, repo_root)
    return resolved is not None and resolved.is_dir()


def _build_packet(
    *,
    case_id: str,
    finding_id: str,
    source: str,
    source_ref: str,
    signal_hash: str,
    plane: str,
    surface: str,
    symbol: str,
    text: str,
    quotes: list[str],
    contract: dict[str, str],
    confidence: str,
    route: dict[str, str],
) -> dict[str, object]:
    evidence: list[dict[str, object]] = []
    first_line = ""
    for quote in quotes:
        line_number = _line_of(text, quote)
        if first_line == "" and line_number:
            first_line = str(line_number)
        evidence.append(
            {"PATH": surface, "QUOTE": quote, "LINE": line_number or 0}
        )
    loc_link = f"{surface}:{first_line}" if first_line else surface
    unresolved = [route["VERIFIER_GAP"]] if route["VERIFIER_GAP"] != "none" else []
    return {
        "SECURITY_CASE_ID": case_id,
        "SECURITY_FINDING_ID": finding_id,
        "SOURCE": source,
        "SOURCE_REF": source_ref or "none",
        "SOURCE_SIGNAL_HASH": signal_hash,
        "TRIAGE_STATUS": "CONFIRMED",
        "SECURITY_CLASS": contract["security_class"],
        "SEVERITY": contract["severity"],
        "CONFIDENCE": confidence or "n/a",
        "CONFIDENCE_IS_PROOF": "NO",
        "AFFECTED_PLANE": plane,
        "AFFECTED_SURFACE": surface,
        "PATH": surface,
        "LOC_LINK": loc_link,
        "SYMBOL": symbol or "n/a",
        "THREAT": contract["threat"],
        "ATTACK_PRECONDITION": contract["attack_precondition"],
        "CAUSAL_PATH": contract["causal_path"],
        "IMPACT": contract["impact"],
        "SECURITY_INVARIANT": contract["security_invariant"],
        "REMEDIATION_REQUIREMENT": contract["remediation_requirement"],
        "RETEST_REQUIREMENT": contract["retest_requirement"],
        "REMEDIATION_AUTHORITY_CLASS": route["REMEDIATION_AUTHORITY_CLASS"],
        "SECURITY_BINDING": {
            "BINDING_CLASS": route["BINDING_CLASS"],
            "BINDING_ID": route["BINDING_ID"],
            "WRITE_AUTHORITY": route["WRITE_AUTHORITY"],
        },
        "REMEDIATION_ROUTE": route["REMEDIATION_ROUTE"],
        "NEXT_COMMAND": route["NEXT_COMMAND"],
        "EXPECTED_VERIFIER": route["EXPECTED_VERIFIER"],
        "VERIFIER_GAP": route["VERIFIER_GAP"],
        "CHANGE_BINDING": {
            "CHANGE_ID": route["CHANGE_ID"],
            "STATUS": route["CHANGE_STATUS"],
        },
        "EVIDENCE": evidence,
        "ASSUMPTIONS": [
            "CAPABILITY_PARITY PARTIAL",
            "target has no declared security_triage adapter",
        ],
        "UNRESOLVED": unresolved,
        "CAPABILITY_PARITY": "PARTIAL",
        "SECURITY_TRIAGE_DECLARED": "NO",
        "SECURITY_REVIEW_VERDICT": "not_issued",
        "NEXT_ACTION": route["NEXT_ACTION"],
        "DO_NOT_DO_YET": DO_NOT_DO_YET,
        "SCHEMA_VERSION": SCHEMA_VERSION,
    }


def _line_of(text: str, quote: str) -> int | None:
    for index, line in enumerate(text.split("\n"), start=1):
        if quote in line:
            return index
    return None


def _schema_gaps(packet: dict[str, object]) -> str:
    schema_path = Path(__file__).resolve().parent / "SECURITY_FINDING_PACKET.schema.json"
    if not schema_path.is_file():
        return "schema_missing"
    schema = json.loads(schema_path.read_text(encoding="utf-8-sig"))
    if not isinstance(schema, dict):
        return "schema_invalid"
    required = schema.get("required")
    if not isinstance(required, list):
        return "schema_invalid"
    for key in cast(list[object], required):
        if not isinstance(key, str) or key not in packet:
            return "schema_gap"
        if packet[key] in ("", None, []):
            if key in {"SOURCE_REF", "UNRESOLVED", "ASSUMPTIONS"}:
                continue
            return "schema_gap"
    if packet.get("TRIAGE_STATUS") != "CONFIRMED":
        return "schema_gap"
    if packet.get("SECURITY_REVIEW_VERDICT") in ARCHIVE_VERDICTS:
        return "archive_verdict_forbidden"
    if packet.get("CONFIDENCE_IS_PROOF") != "NO":
        return "confidence_not_proof"
    return ""


def _materialize(
    repo_root: Path, packet: dict[str, object]
) -> dict[str, object]:
    case_id = str(packet["SECURITY_CASE_ID"])
    finding_id = str(packet["SECURITY_FINDING_ID"])
    if not CASE_ID_PATTERN.fullmatch(case_id) or not FINDING_ID_PATTERN.fullmatch(
        finding_id
    ):
        return _status(
            status="BLOCKED",
            reason="invalid_case_id",
            source=str(packet.get("SOURCE", "")),
            signal_hash=str(packet.get("SOURCE_SIGNAL_HASH", "")),
            packet_state="absent",
        )
    findings_dir = (
        repo_root / "opsx" / "runs" / "security-cases" / case_id / "findings"
    )
    target = findings_dir / f"{finding_id}.md"
    if canonical_contained(target, repo_root) is None:
        return _status(
            status="BLOCKED",
            reason="path_outside_workspace",
            source=str(packet["SOURCE"]),
            signal_hash=str(packet["SOURCE_SIGNAL_HASH"]),
            packet_state="absent",
        )
    store_root = (repo_root / "opsx" / "runs" / "security-cases").resolve()
    if canonical_contained(target, store_root) is None:
        return _status(
            status="BLOCKED",
            reason="path_outside_workspace",
            source=str(packet["SOURCE"]),
            signal_hash=str(packet["SOURCE_SIGNAL_HASH"]),
            packet_state="absent",
        )
    if "bug-cases" in target.parts:
        return _status(
            status="BLOCKED",
            reason="bug_lane_forbidden",
            source=str(packet["SOURCE"]),
            signal_hash=str(packet["SOURCE_SIGNAL_HASH"]),
            packet_state="absent",
        )
    try:
        findings_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return _status(
            status="BLOCKED",
            reason="interrupted_evidence_write",
            source=str(packet["SOURCE"]),
            signal_hash=str(packet["SOURCE_SIGNAL_HASH"]),
            packet_state="absent",
        )
    body = _render(packet).encode("utf-8")
    try:
        _exclusive_write(target, body)
    except FileExistsError:
        return _collision(repo_root, target, packet)
    except OSError:
        return _status(
            status="BLOCKED",
            reason="interrupted_evidence_write",
            source=str(packet["SOURCE"]),
            signal_hash=str(packet["SOURCE_SIGNAL_HASH"]),
            packet_state="absent",
        )
    ledger = findings_dir.parent / "ledger.jsonl"
    record = {
        "event": "FINDING_MATERIALIZED",
        "SECURITY_CASE_ID": case_id,
        "SECURITY_FINDING_ID": finding_id,
        "SOURCE_SIGNAL_HASH": packet["SOURCE_SIGNAL_HASH"],
    }
    try:
        _append_ledger(ledger, record)
    except OSError:
        return _status(
            status="BLOCKED",
            reason="interrupted_evidence_write",
            source=str(packet["SOURCE"]),
            signal_hash=str(packet["SOURCE_SIGNAL_HASH"]),
            packet_state="absent",
            case_id=case_id,
            finding_id=finding_id,
        )
    relative = target.relative_to(repo_root).as_posix()
    return _status(
        status="CONFIRMED",
        reason="cited_evidence_verified",
        source=str(packet["SOURCE"]),
        signal_hash=str(packet["SOURCE_SIGNAL_HASH"]),
        packet_state="present",
        case_id=case_id,
        finding_id=finding_id,
        evidence_path=relative,
        plane=str(packet["AFFECTED_PLANE"]),
        extra={
            "SECURITY_CLASS": packet["SECURITY_CLASS"],
            "SECURITY_BINDING": packet["SECURITY_BINDING"],
            "REMEDIATION_AUTHORITY_CLASS": packet["REMEDIATION_AUTHORITY_CLASS"],
            "REMEDIATION_ROUTE": packet["REMEDIATION_ROUTE"],
            "NEXT_COMMAND": packet["NEXT_COMMAND"],
            "NEXT_ARGS": _route_next_args(packet),
            "EXPECTED_VERIFIER": packet["EXPECTED_VERIFIER"],
            "VERIFIER_GAP": packet["VERIFIER_GAP"],
            "CHANGE_BINDING": packet["CHANGE_BINDING"],
            "NEXT_ACTION": packet["NEXT_ACTION"],
            "AFFECTED_SURFACE": packet["AFFECTED_SURFACE"],
        },
    )


def _route_next_args(packet: dict[str, object]) -> str:
    binding = packet["CHANGE_BINDING"]
    if not isinstance(binding, dict):
        return ""
    typed = cast(dict[str, object], binding)
    if typed.get("STATUS") != "BOUND":
        return ""
    return (
        f"change={typed.get('CHANGE_ID')} "
        f"security_finding={packet.get('SECURITY_FINDING_ID')}"
    )


def _render(packet: dict[str, object]) -> str:
    payload = json.dumps(packet, ensure_ascii=True, indent=2, sort_keys=True)
    return (
        "# SECURITY_FINDING_PACKET\n\n"
        "Triage disposition only. This file is not a security review verdict.\n\n"
        f"{PACKET_BEGIN}{payload}{PACKET_END}"
    )


def _exclusive_write(path: Path, data: bytes) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    descriptor = os.open(path, flags)
    try:
        view = memoryview(data)
        written = 0
        while written < len(view):
            chunk = os.write(descriptor, view[written:])
            if chunk <= 0:
                raise OSError("short write")
            written += chunk
        os.fsync(descriptor)
    except OSError:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    os.close(descriptor)


def _append_ledger(path: Path, record: dict[str, object]) -> None:
    line = json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        _ = handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _collision(
    repo_root: Path, target: Path, packet: dict[str, object]
) -> dict[str, object]:
    existing = _parse_packet(target)
    source = str(packet.get("SOURCE", ""))
    signal_hash = str(packet.get("SOURCE_SIGNAL_HASH", ""))
    if existing is None:
        return _status(
            status="BLOCKED",
            reason="identity_collision",
            source=source,
            signal_hash=signal_hash,
            packet_state="absent",
        )
    if existing.get("SOURCE_SIGNAL_HASH") != signal_hash:
        return _status(
            status="BLOCKED",
            reason="identity_collision",
            source=source,
            signal_hash=signal_hash,
            packet_state="absent",
        )
    relative = target.relative_to(repo_root).as_posix()
    return _status(
        status="DUPLICATE",
        reason="existing_finding",
        source=source,
        signal_hash=signal_hash,
        packet_state="reused_duplicate",
        case_id=str(existing.get("SECURITY_CASE_ID", "")),
        finding_id=str(existing.get("SECURITY_FINDING_ID", "")),
        evidence_path=relative,
        plane=str(existing.get("AFFECTED_PLANE", "n/a")),
    )


def _find_duplicate(
    repo_root: Path, signal_hash: str
) -> dict[str, object] | None:
    root = repo_root / "opsx" / "runs" / "security-cases"
    if not root.is_dir():
        return None
    for path in sorted(root.glob("*/findings/*.md")):
        resolved = canonical_contained(path, root)
        if resolved is None or not resolved.is_file():
            continue
        packet = _parse_packet(resolved)
        if packet is None:
            continue
        if packet.get("SOURCE_SIGNAL_HASH") != signal_hash:
            continue
        if packet.get("TRIAGE_STATUS") != "CONFIRMED":
            continue
        relative = resolved.relative_to(repo_root.resolve()).as_posix()
        found = dict(packet)
        found["EVIDENCE_PATH"] = relative
        return found
    return None


def _parse_packet(path: Path) -> dict[str, object] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    start = text.find(PACKET_BEGIN)
    end = text.find(PACKET_END)
    if start < 0 or end < 0 or end <= start:
        return None
    raw = text[start + len(PACKET_BEGIN) : end]
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _status(
    *,
    status: str,
    reason: str,
    source: str,
    signal_hash: str,
    packet_state: str,
    case_id: str = "none",
    finding_id: str = "none",
    evidence_path: str = "none",
    plane: str = "n/a",
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    if status in ARCHIVE_VERDICTS:
        raise SecurityTriageError("archive_verdict_forbidden")
    body: dict[str, object] = {
        "STATUS": status,
        "REASON": reason,
        "MODE": "READ_ONLY_EXCEPT_SECURITY_EVIDENCE",
        "CONTRACT_ID": CONTRACT_ID,
        "CAPABILITY_PARITY": "PARTIAL",
        "SECURITY_TRIAGE_DECLARED": "NO",
        "LOCAL_EVIDENCE_PATH": "bounded_workspace_citation",
        "SOURCE_PARITY": "PARTIAL",
        "SOURCE": source or "none",
        "SOURCE_SIGNAL_HASH": signal_hash or "none",
        "SECURITY_CASE_ID": case_id,
        "SECURITY_FINDING_ID": finding_id,
        "EVIDENCE_PATH": evidence_path,
        "TRIAGE_STATUS": status,
        "AFFECTED_PLANE": plane,
        "SECURITY_FINDING_PACKET": packet_state,
        "SECURITY_REVIEW_VERDICT": "not_issued",
        "BUG_LANE_WRITE": "NO",
        "NEXT_COMMAND": "none",
        "DO_NOT_DO_YET": DO_NOT_DO_YET,
    }
    if extra:
        body.update(extra)
    return body


def build_parser() -> argparse.ArgumentParser:
    """CLI: один request JSON, результат disposition в stdout."""

    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--repo-root", type=Path, required=True)
    _ = parser.add_argument("--request-file", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Прочитать request и напечатать disposition JSON."""

    args = build_parser().parse_args(argv)
    try:
        raw = json.loads(args.request_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(json.dumps(_blocked_cli(f"invalid_request:{error}")))
        return 2
    if not isinstance(raw, dict):
        print(json.dumps(_blocked_cli("invalid_request")))
        return 2
    result = triage(args.repo_root, cast(dict[str, object], raw))
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


def _blocked_cli(reason: str) -> dict[str, object]:
    return _status(
        status="BLOCKED",
        reason=reason,
        source="",
        signal_hash="",
        packet_state="absent",
    )


if __name__ == "__main__":
    raise SystemExit(main())
