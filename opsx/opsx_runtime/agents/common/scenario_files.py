"""Раскладка scenario-coverage: один scenario_id — один файл.

Живой проход читает каталог ``scenarios/<sha256(scenario_id)>.md`` и receipt.
``plan.md`` остаётся change-level документом без тел ``- SCENARIO_ID:``.
Такие тела в ``plan.md`` — незавершённая или смешанная миграция.
Исторический монолит читает только recovery (``allow_historical=True``).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from check_workflow_receipt import CheckWorkflowReceipt

_ROW = re.compile(r"^(\s*)-\s+SCENARIO_ID:\s*(.*?)\s*$")
_SCENARIOS_HEADER = re.compile(r"^\s*SCENARIOS:\s*$")


def scenario_digest(scenario_id: str) -> str:
    """Вернуть sha256 идентификатора сценария в hex."""
    return hashlib.sha256(scenario_id.encode("utf-8")).hexdigest()


def scenario_filename(scenario_id: str) -> str:
    """Имя файла плана сценария: ``<sha256>.md``."""
    return scenario_digest(scenario_id) + ".md"


def scenario_test_relpath(change_id: str, scenario_id: str) -> str:
    """Путь теста, который pytest собирает по умолчанию (``test_*.py``)."""
    return f"tests/scenarios/{change_id}/test_{scenario_digest(scenario_id)}.py"


def plan_has_embedded_rows(text: str) -> bool:
    """True, если в тексте плана остались тела строк ``- SCENARIO_ID:``."""
    return any(_ROW.match(line) for line in text.splitlines())


def _blocks(text: str) -> list[tuple[int, int, str, str]]:
    """Вырезать блоки ``- SCENARIO_ID:`` вместе с вложенными строками."""
    lines = text.splitlines(keepends=True)
    found: list[tuple[int, int, str, str]] = []
    index = 0
    while index < len(lines):
        match = _ROW.match(lines[index])
        if match is None:
            index += 1
            continue
        indent = len(match.group(1))
        scenario_id = match.group(2)
        end = index + 1
        while end < len(lines):
            line = lines[end]
            if line.strip():
                next_indent = len(line) - len(line.lstrip(" "))
                if next_indent <= indent:
                    break
            end += 1
        found.append((index, end, scenario_id, "".join(lines[index:end])))
        index = end
    return found


def _scenarios_section_start(text: str) -> int | None:
    """Номер строки заголовка ``SCENARIOS:`` (последний в файле)."""
    start = None
    for index, line in enumerate(text.splitlines()):
        if _SCENARIOS_HEADER.match(line):
            start = index
    return start


def _without_marker(body: str) -> str:
    """Убрать первую строку ``- SCENARIO_ID:``, сохранив вложенные поля."""
    lines = body.splitlines()
    if lines and _ROW.match(lines[0]):
        lines = lines[1:]
    return "\n".join(lines).strip()


def split_plan_text(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Вынести тела сценариев из ``plan.md``.

    Порядок — строки секции ``SCENARIOS:``. Тела вне этой секции с тем же
    id дописываются в файл сценария, а не отбрасываются. Уникальные id
    только из инвентаря добавляются после секции ``SCENARIOS:``.

    Returns:
        Текст ``plan.md`` без блоков ``- SCENARIO_ID:`` и пары
        ``(scenario_id, тело файла)``.
    """
    blocks = _blocks(text)
    if not blocks:
        return text, []
    section_at = _scenarios_section_start(text)
    in_section: list[tuple[str, str]] = []
    outside: list[tuple[str, str]] = []
    lines = text.splitlines(keepends=True)
    for start, _end, scenario_id, body in blocks:
        if section_at is not None and start > section_at:
            in_section.append((scenario_id, body))
        else:
            outside.append((scenario_id, body))
    order: list[str] = []
    bodies: dict[str, list[str]] = {}
    for scenario_id, body in in_section + outside:
        if scenario_id not in bodies:
            order.append(scenario_id)
            bodies[scenario_id] = []
        if body not in bodies[scenario_id]:
            bodies[scenario_id].append(body)
    # Секция SCENARIOS задаёт порядок; id только из инвентаря идут следом.
    section_ids = []
    seen: set[str] = set()
    for scenario_id, _body in in_section:
        if scenario_id not in seen:
            seen.add(scenario_id)
            section_ids.append(scenario_id)
    trailing = [scenario_id for scenario_id in order if scenario_id not in seen]
    ordered = section_ids + trailing
    files: list[tuple[str, str]] = []
    for scenario_id in ordered:
        parts = bodies[scenario_id]
        primary = parts[0]
        extras = [_without_marker(part) for part in parts[1:]]
        extras = [part for part in extras if part]
        body = primary.rstrip()
        if extras:
            body += "\nINVENTORY:\n" + "\n".join(extras) + "\n"
        else:
            body += "\n"
        files.append((scenario_id, body))
    remove = {(start, end) for start, end, _sid, _body in blocks}
    kept: list[str] = []
    index = 0
    while index < len(lines):
        skipped = False
        for start, end in remove:
            if index == start:
                index = end
                skipped = True
                break
        if skipped:
            continue
        kept.append(lines[index])
        index += 1
    plan = "".join(kept)
    plan = re.sub(r"\n{3,}", "\n\n", plan)
    if not plan.endswith("\n"):
        plan += "\n"
    return plan, files


def _lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def file_sha256(data: bytes) -> str:
    """sha256 LF-нормализованных байт файла."""
    return hashlib.sha256(_lf(data)).hexdigest()


def plan_content_hash(plan_bytes: bytes, pairs: list[tuple[str, str]]) -> str:
    """sha256 оставшегося ``plan.md`` и канонического списка ``(id, file_sha256)``.

    Args:
        plan_bytes: Байты change-level ``plan.md``.
        pairs: Пары ``(scenario_id, sha256 файла сценария)`` в порядке receipt.
    """
    digest = hashlib.sha256()
    digest.update(_lf(plan_bytes))
    digest.update(b"\0")
    for scenario_id, file_hash in pairs:
        digest.update(scenario_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _coverage_change_and_root(coverage: Path) -> tuple[str, Path]:
    """Идентификатор change и корень репозитория по каталогу ``scenario-coverage``."""
    change_id = coverage.parent.name
    repo_root = coverage.parents[3]
    return change_id, repo_root


def scenario_machine_path(coverage: Path, filename: str) -> Path:
    """Один machine-файл scenario-coverage под ``opsx/receipts/<change_id>/``."""
    from workflows.artifact_paths import scenario_machine_receipt_path

    change_id, repo_root = _coverage_change_and_root(coverage)
    return scenario_machine_receipt_path(repo_root, change_id, filename)


def plan_receipt_machine_path(coverage: Path) -> Path:
    """Канонический machine-путь ``opsx/receipts/<id>/scenario-coverage/plan.receipt.json``."""
    return scenario_machine_path(coverage, "plan.receipt.json")


def trace_matrix_machine_path(coverage: Path) -> Path:
    """Канонический ``test_trace_matrix.json`` в ``opsx/receipts``."""
    return scenario_machine_path(coverage, "test_trace_matrix.json")


def row_walk_journal_machine_path(coverage: Path) -> Path:
    """Канонический ``row_walk_journal.json`` в ``opsx/receipts``."""
    return scenario_machine_path(coverage, "row_walk_journal.json")


def _receipt(coverage: Path) -> dict:
    """Прочитать machine receipt плана из ``opsx/receipts/``."""
    receipt_path = plan_receipt_machine_path(coverage)
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("COVERAGE_RECEIPT_INVALID")
    return payload


def _catalog_ids(coverage: Path, receipt: dict) -> list[str]:
    files = receipt.get("SCENARIO_FILES")
    if not isinstance(files, list) or not files:
        return []
    ids: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("COVERAGE_RECEIPT_INVALID")
        scenario_id = item.get("scenario_id")
        rel = item.get("path")
        content_hash = item.get("content_hash")
        if not isinstance(scenario_id, str) or not scenario_id:
            raise ValueError("COVERAGE_RECEIPT_INVALID")
        if not isinstance(rel, str) or rel != f"scenarios/{scenario_filename(scenario_id)}":
            raise ValueError(f"COVERAGE_SCENARIO_PATH_INVALID: {scenario_id}")
        path = coverage / rel
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"COVERAGE_SCENARIO_FILE_MISSING: {rel}")
        text = path.read_text(encoding="utf-8")
        found = [match.group(2) for line in text.splitlines() if (match := _ROW.match(line))]
        if found != [scenario_id]:
            raise ValueError(f"COVERAGE_SCENARIO_FILE_IDENTITY: {scenario_id}")
        if not CheckWorkflowReceipt.matches_file(path, content_hash):
            raise ValueError(f"COVERAGE_SCENARIO_HASH_MISMATCH: {scenario_id}")
        if scenario_id in ids:
            raise ValueError(f"COVERAGE_SCENARIO_DUPLICATE: {scenario_id}")
        ids.append(scenario_id)
    return ids


def read_scenario_ids(coverage: Path, *, allow_historical: bool) -> list[str]:
    """Прочитать порядок сценариев.

    Args:
        coverage: Каталог ``scenario-coverage``.
        allow_historical: Разрешить монолитный ``plan.md`` (только recovery).

    Raises:
        ValueError: Смешанный или немигрированный план на живом проходе,
            либо receipt не сходится с файлами.
    """
    plan_path = coverage / "plan.md"
    text = plan_path.read_text(encoding="utf-8") if plan_path.is_file() else ""
    embedded = plan_has_embedded_rows(text)
    if embedded:
        if not allow_historical:
            raise ValueError("COVERAGE_PLAN_EMBEDDED")
        return [scenario_id for _start, _end, scenario_id, _body in _blocks(text)]
    receipt_path = plan_receipt_machine_path(coverage)
    if not receipt_path.is_file():
        return []
    return _catalog_ids(coverage, _receipt(coverage))


def scenario_pairs(coverage: Path) -> list[tuple[str, str]]:
    """Пары ``(scenario_id, file_sha256)`` из receipt после проверки файлов."""
    receipt = _receipt(coverage)
    ids = _catalog_ids(coverage, receipt)
    pairs: list[tuple[str, str]] = []
    for scenario_id in ids:
        path = coverage / "scenarios" / scenario_filename(scenario_id)
        pairs.append((scenario_id, file_sha256(path.read_bytes())))
    return pairs


def materialize_scenario_files(coverage: Path) -> list[str]:
    """Вынести тела сценариев в каталог и обновить receipt. Повтор — no-op.

    Returns:
        Упорядоченные ``scenario_id``. ``PLAN_EVIDENCE_ID`` сохраняется.
    """
    plan_path = coverage / "plan.md"
    receipt_path = plan_receipt_machine_path(coverage)
    text = plan_path.read_text(encoding="utf-8") if plan_path.is_file() else ""
    receipt = _receipt(coverage) if receipt_path.is_file() else {}
    if not plan_has_embedded_rows(text):
        return read_scenario_ids(coverage, allow_historical=False)
    plan_text, files = split_plan_text(text)
    scenarios = coverage / "scenarios"
    scenarios.mkdir(exist_ok=True)
    entries = []
    for scenario_id, body in files:
        path = scenarios / scenario_filename(scenario_id)
        data = body.encode("utf-8")
        if not path.is_file() or path.read_bytes() != data:
            path.write_bytes(data)
        entries.append({
            "scenario_id": scenario_id,
            "path": f"scenarios/{scenario_filename(scenario_id)}",
            "content_hash": file_sha256(data),
        })
    plan_bytes = plan_text.encode("utf-8")
    if plan_path.read_bytes() != plan_bytes:
        plan_path.write_bytes(plan_bytes)
    pairs = [(item["scenario_id"], item["content_hash"]) for item in entries]
    receipt = dict(receipt)
    receipt["SCENARIO_FILES"] = entries
    receipt["SCENARIO_COUNT"] = len(entries)
    receipt["PLAN_CONTENT_HASH"] = plan_content_hash(plan_bytes, pairs)
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return [item["scenario_id"] for item in entries]


def expected_plan_content_hash(coverage: Path) -> str:
    """Пересчитать ``PLAN_CONTENT_HASH`` по диску."""
    plan_bytes = (coverage / "plan.md").read_bytes()
    return plan_content_hash(plan_bytes, scenario_pairs(coverage))
