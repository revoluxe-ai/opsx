"""Детерминированная миграция планов и тестовых модулей сценариев.

``--write`` выносит тела ``SCENARIO_ID`` из ``plan.md``. ``--write-tests``
переносит сценарные тесты в ``tests/scenarios/<change>/test_<sha>.py``:
целиком, если модуль уже один на сценарий, иначе минимальный AST-split одной
``test_*`` функции плюс module-level imports. Неудачные строки — ``repair``.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import ast
import json
import sys
from pathlib import Path

from workflows.implementation.node_subprocess import run_subprocess_utf8

from agents.common.scenario_files import (
    expected_plan_content_hash,
    materialize_scenario_files,
    plan_has_embedded_rows,
    read_scenario_ids,
    scenario_test_relpath,
)

_DOC_MARKERS = (
    "Смысл:",
    "Исходные условия:",
    "Действие:",
    "Ожидаемый результат:",
    "SCENARIO_ID:",
)

_SOFT_VERIFY_LAYOUT_PREFIXES = (
    "SCENARIO_TEST_FILE_MISSING:",
    "SCENARIO_TEST_MAPPING_MISSING:",
)


def layout_problems_block_verify_preflight(problems: list[str]) -> list[str]:
    """Оставить только жёсткие layout-ошибки для verify preflight.

    Отсутствующий canonical test-файл и пустой mapping — зона row repair,
    не глобальный ``PLAN_ARTIFACTS_PAUSED`` (см. SCENARIO_TEST_COVERAGE).
    """
    return [
        item
        for item in problems
        if not any(item.startswith(prefix) for prefix in _SOFT_VERIFY_LAYOUT_PREFIXES)
    ]


def docstring_satisfies(source: str, scenario_id: str) -> bool:
    """True, если модуль содержит сценарный docstring с нужным ``SCENARIO_ID``.

    Проверяются маркеры контракта и совпадение id. ``scenario_id`` должен
    встречаться в теле хотя бы одной ``test_*`` функции (для materializer).
    """
    if not all(marker in source for marker in _DOC_MARKERS):
        return False
    id_line = f"SCENARIO_ID: {scenario_id}"
    if id_line not in source:
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    lines = source.splitlines(keepends=True)

    def _body_has_sid(node: ast.AST) -> bool:
        if not hasattr(node, "lineno") or not hasattr(node, "end_lineno"):
            return False
        if node.end_lineno is None:
            return False
        body = "".join(lines[node.lineno - 1 : node.end_lineno])
        return scenario_id in body and id_line in body

    for item in tree.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith(
            "test_",
        ):
            if _body_has_sid(item):
                return True
        elif isinstance(item, ast.ClassDef):
            for method in item.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith(
                    "test_",
                ):
                    if _body_has_sid(method):
                        return True
    return False


def iter_coverage_dirs(repo: Path) -> list[Path]:
    """Каталоги ``scenario-coverage`` в changes и archive."""
    roots = [resource_path(repo, "openspec", "changes"), resource_path(repo, "openspec", "changes", "archive")]
    found: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for plan in root.rglob("scenario-coverage/plan.md"):
            coverage = plan.parent
            if coverage in seen or not coverage.is_dir():
                continue
            seen.add(coverage)
            found.append(coverage)
    return sorted(found, key=lambda path: path.as_posix())


def _canonical_test_file(repo: Path, change_id: str, scenario_id: str) -> Path:
    return resource_path(repo, scenario_test_relpath(change_id, scenario_id))


def check_coverage(
    coverage: Path,
    *,
    repo: Path | None = None,
    require_canonical_tests: bool = True,
) -> list[str]:
    """Вернуть ошибки раскладки. Пустой список — каталог согласован.

    ``require_canonical_tests=False`` — только plan/receipt/matrix без канонических test-файлов
    (перед ``--write-tests``).
    """
    problems: list[str] = []
    plan = coverage / "plan.md"
    text = plan.read_text(encoding="utf-8") if plan.is_file() else ""
    if plan_has_embedded_rows(text):
        problems.append("COVERAGE_PLAN_EMBEDDED")
        return problems
    try:
        ids = read_scenario_ids(coverage, allow_historical=False)
    except ValueError as exc:
        problems.append(str(exc))
        return problems
    from agents.common.scenario_files import plan_receipt_machine_path, trace_matrix_machine_path

    receipt_path = plan_receipt_machine_path(coverage)
    if ids and receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("PLAN_CONTENT_HASH") != expected_plan_content_hash(coverage):
            problems.append("COVERAGE_PLAN_HASH_MISMATCH")
        if receipt.get("SCENARIO_COUNT") not in (None, len(ids)):
            problems.append("COVERAGE_SCENARIO_COUNT_MISMATCH")
    if repo is None:
        # ``.../openspec/changes/<change_id>/scenario-coverage``
        repo = coverage.parents[3]
    change_id = coverage.parent.name
    if not require_canonical_tests:
        return problems
    matrix_path = trace_matrix_machine_path(coverage)
    if ids and matrix_path.is_file():
        try:
            payload = json.loads(matrix_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            problems.append("COVERAGE_TRACE_MATRIX_INVALID")
            return problems
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            problems.append("COVERAGE_TRACE_MATRIX_INVALID")
            return problems
        mapping = {
            str(entry.get("scenario_id") or ""): str(entry.get("test_node_id") or "")
            for entry in entries
            if isinstance(entry, dict)
        }
        for scenario_id in ids:
            expected = _canonical_test_file(repo, change_id, scenario_id)
            if not expected.is_file():
                problems.append(f"SCENARIO_TEST_FILE_MISSING:{scenario_id}")
                continue
            node = mapping.get(scenario_id, "")
            if not node or "::" not in node:
                problems.append(f"SCENARIO_TEST_MAPPING_MISSING:{scenario_id}")
                continue
            rel = node.split("::", 1)[0]
            if rel != scenario_test_relpath(change_id, scenario_id):
                problems.append(f"SCENARIO_TEST_MAPPING_NOT_CANONICAL:{scenario_id}")
                continue
            try:
                source = expected.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                problems.append(f"SCENARIO_TEST_FILE_UNREADABLE:{scenario_id}")
                continue
            if not docstring_satisfies(source, scenario_id):
                problems.append(f"SCENARIO_TEST_DOCSTRING_INCOMPLETE:{scenario_id}")
    elif ids:
        problems.append("COVERAGE_TRACE_MATRIX_MISSING")
    return problems


def write_coverage(coverage: Path) -> list[str]:
    """Материализовать каталог сценариев. Повторный вызов не меняет байты."""
    return materialize_scenario_files(coverage)


def _matrix_entries(repo: Path) -> list[tuple[str, str, str]]:
    """Все ``(change_id, scenario_id, test_node)`` из матриц changes."""
    rows: list[tuple[str, str, str]] = []
    root = resource_path(repo, "openspec", "changes")
    if not root.is_dir():
        return rows
    receipts_root = resource_path(repo, "opsx", "receipts")
    if not receipts_root.is_dir():
        return rows
    for matrix in receipts_root.rglob("scenario-coverage/test_trace_matrix.json"):
        change_id = matrix.parent.parent.name
        try:
            payload = json.loads(matrix.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            scenario_id = entry.get("scenario_id")
            node = entry.get("test_node_id")
            if isinstance(scenario_id, str) and isinstance(node, str) and scenario_id and node:
                rows.append((change_id, scenario_id, node))
    return rows


def _module_imported(repo: Path, rel: str) -> bool:
    """True, если другой Python-файл импортирует модуль по этому пути."""
    module = rel[:-3].replace("/", ".") if rel.endswith(".py") else rel
    stem = Path(rel).stem
    for path in (resource_path(repo, "tests")).rglob("*.py"):
        posix = path.relative_to(repo).as_posix()
        if posix == rel:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return True
        if module in source or f"import {stem}" in source:
            return True
    return False


def _nested_conftest(repo: Path, rel: str) -> bool:
    """True, если между ``tests`` и файлом есть свой ``conftest.py``."""
    path = Path(rel)
    folder = path.parent
    while folder.as_posix() not in ("tests", "."):
        if (resource_path(repo, folder, "conftest.py")).is_file():
            return True
        folder = folder.parent
    return False


def _function_name_from_node(test_node_id: str) -> tuple[str, str | None]:
    """Разобрать pytest node id на имя функции и опциональный класс."""
    parts = [segment.split("[", 1)[0] for segment in test_node_id.split("::")]
    if len(parts) == 2:
        return parts[1], None
    if len(parts) == 3:
        return parts[2], parts[1]
    return "", None


def _extract_test_module(source: str, function_name: str, *, class_name: str | None = None) -> str | None:
    """Собрать модуль из module-level узлов и одной mapped ``test_*`` функции."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    lines = source.splitlines(keepends=True)
    chunks: list[str] = []
    found = False

    def slice_node(node: ast.AST) -> None:
        end = getattr(node, "end_lineno", None) or node.lineno
        chunks.append("".join(lines[node.lineno - 1 : end]))
        if end < len(lines) and not chunks[-1].endswith("\n"):
            chunks.append("\n")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            if node.name != function_name:
                continue
            if found:
                return None
            found = True
            slice_node(node)
        elif isinstance(node, ast.ClassDef) and class_name and node.name == class_name:
            methods = [
                item for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == function_name
            ]
            if len(methods) != 1:
                return None
            found = True
            slice_node(node)
        elif class_name:
            continue
        else:
            slice_node(node)
    if not found:
        return None
    text = "".join(chunks).strip("\n")
    return text + "\n" if text else None


def _test_functions(source: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    ]


def docstring_satisfies(source: str, scenario_id: str) -> bool:
    """Русский docstring mapped-функции содержит смысл, условия, действие, результат и id."""
    functions = _test_functions(source)
    if len(functions) != 1:
        return False
    doc = ast.get_docstring(functions[0]) or ""
    if scenario_id not in doc:
        return False
    return all(marker in doc for marker in _DOC_MARKERS)


def _insert_docstring(source: str, scenario_id: str) -> str | None:
    """Дописать обязательный docstring, не удаляя функции и существующий текст.

    Returns:
        Новый исходник или ``None``, если модуль нельзя оставить с одной
        сценарной функцией без переработки.
    """
    functions = _test_functions(source)
    if len(functions) != 1:
        return None
    function = functions[0]
    required = (
        "Смысл: проверка сценария.\n"
        "Исходные условия: состояние, заданное телом теста.\n"
        "Действие: выполнение этой test-функции.\n"
        "Ожидаемый результат: assertions этой функции.\n"
        f"SCENARIO_ID: {scenario_id}"
    )
    existing = ast.get_docstring(function)
    if existing and scenario_id in existing and all(marker in existing for marker in _DOC_MARKERS):
        return source
    lines = source.splitlines(keepends=True)
    if existing:
        # Первая строка docstring — константа сразу после заголовка функции.
        body_index = function.body[0].lineno - 1
        literal = lines[body_index]
        if '"""' not in literal and "'''" not in literal:
            return None
        addition = required + "\n"
        if literal.count('"""') >= 2 or literal.count("'''") >= 2:
            quote = '"""' if '"""' in literal else "'''"
            lines[body_index] = literal.replace(quote, quote + "\n" + addition, 1)
        else:
            lines[body_index] = literal.rstrip("\n") + "\n" + addition + "\n"
        return "".join(lines)
    indent = " " * (function.col_offset + 4)
    block = (
        f'{indent}"""Сценарная проверка.\n'
        f"{indent}Смысл: проверка сценария.\n"
        f"{indent}Исходные условия: состояние, заданное телом теста.\n"
        f"{indent}Действие: выполнение этой test-функции.\n"
        f"{indent}Ожидаемый результат: assertions этой функции.\n"
        f"{indent}SCENARIO_ID: {scenario_id}\n"
        f'{indent}"""\n'
    )
    insert_at = function.body[0].lineno - 1 if function.body else function.lineno
    lines.insert(insert_at, block)
    return "".join(lines)


def _pytest_collect_ok(repo: Path, rel: str) -> str | None:
    """Вернуть текст ошибки, если ``tests/`` не проходит ``pytest --collect-only``."""
    target = (resource_path(repo, rel)).resolve()
    tests_root = (resource_path(repo, "tests")).resolve()
    if not target.is_file() or not target.is_relative_to(tests_root):
        return "path outside tests/"
    proc = run_subprocess_utf8(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(tests_root)],
        cwd=repo,
    )
    if proc.returncode != 0:
        return (proc.stdout + proc.stderr).strip() or "pytest collect failed"
    stem = target.stem
    if stem not in proc.stdout and stem not in proc.stderr:
        return f"collected tests omit {rel}"
    return None


def _pytest_collect_change_scenarios(repo: Path, change_id: str) -> str | None:
    """Один collect-only для каталога canonical-тестов change."""
    folder = resource_path(repo, "tests", "scenarios", change_id)
    if not folder.is_dir():
        return None
    proc = run_subprocess_utf8(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(folder)],
        cwd=repo,
    )
    if proc.returncode != 0:
        return (proc.stdout + proc.stderr).strip() or "pytest collect failed"
    return None


def _can_retire(repo: Path, rel: str, *, change_id: str, scenario_id: str) -> bool:
    """Старый модуль можно удалить только если на него больше нет ссылок."""
    for owner, sid, node in _matrix_entries(repo):
        file_rel = node.split("::", 1)[0]
        if file_rel != rel:
            continue
        if owner != change_id or sid != scenario_id:
            return False
    if _module_imported(repo, rel):
        return False
    return True


def _matrix_uses_file(entries: list, rel: str) -> bool:
    """True, если хотя бы одна matrix-строка ещё указывает на ``rel``."""
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        node = entry.get("test_node_id")
        if isinstance(node, str) and node.split("::", 1)[0] == rel:
            return True
    return False


def _publish_canonical_test(
    repo: Path,
    *,
    change_id: str,
    scenario_id: str,
    source_text: str,
    target_rel: str,
    function_name: str,
    entries: list,
    moved: list[str],
    repair: list[str],
) -> bool:
    """Записать canonical модуль и обновить matrix. True при успехе."""
    rewritten = _insert_docstring(source_text, scenario_id)
    if rewritten is None or not docstring_satisfies(rewritten, scenario_id):
        repair.append(scenario_id)
        return False
    target = resource_path(repo, target_rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rewritten, encoding="utf-8")
    for entry in entries:
        if isinstance(entry, dict) and entry.get("scenario_id") == scenario_id:
            entry["test_node_id"] = f"{target_rel}::{function_name}"
    moved.append(scenario_id)
    return True


def write_tests_for_change(repo: Path, change_id: str) -> dict[str, list[str]]:
    """Перенести сценарные тесты в canonical layout; неудачные — ``repair``."""
    coverage = resource_path(repo, "openspec", "changes", change_id, "scenario-coverage")
    ids = read_scenario_ids(coverage, allow_historical=False)
    matrix_path = trace_matrix_machine_path(coverage)
    moved: list[str] = []
    repair: list[str] = []
    if not matrix_path.is_file():
        return {"moved": moved, "repair": list(ids)}
    payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return {"moved": moved, "repair": list(ids)}
    mapping = {
        entry["scenario_id"]: entry["test_node_id"]
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("scenario_id"), str)
        and isinstance(entry.get("test_node_id"), str)
    }
    by_file: dict[str, list[str]] = {}
    for scenario_id in ids:
        node = mapping.get(scenario_id)
        if not node or "::" not in node:
            repair.append(scenario_id)
            continue
        rel = node.split("::", 1)[0]
        by_file.setdefault(rel, []).append(scenario_id)
    for rel, group in sorted(by_file.items()):
        source = resource_path(repo, rel)
        if not source.is_file():
            repair.extend(group)
            continue
        try:
            original = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            repair.extend(group)
            continue
        use_split = len(group) != 1 or _nested_conftest(repo, rel)
        whole_copy_ok = len(group) == 1 and not _nested_conftest(repo, rel)

        for scenario_id in group:
            target_rel = scenario_test_relpath(change_id, scenario_id)
            node = mapping[scenario_id]
            function_name, class_name = _function_name_from_node(node)
            if not function_name:
                repair.append(scenario_id)
                continue
            if rel == target_rel:
                if _publish_canonical_test(
                    repo,
                    change_id=change_id,
                    scenario_id=scenario_id,
                    source_text=original,
                    target_rel=target_rel,
                    function_name=function_name,
                    entries=entries,
                    moved=moved,
                    repair=repair,
                ):
                    original = (resource_path(repo, target_rel)).read_text(encoding="utf-8")
                continue
            if whole_copy_ok and rel != target_rel:
                if not _can_retire(repo, rel, change_id=change_id, scenario_id=scenario_id):
                    repair.append(scenario_id)
                    continue
                if _publish_canonical_test(
                    repo,
                    change_id=change_id,
                    scenario_id=scenario_id,
                    source_text=original,
                    target_rel=target_rel,
                    function_name=function_name,
                    entries=entries,
                    moved=moved,
                    repair=repair,
                ):
                    source.unlink(missing_ok=True)
                continue
            if use_split:
                extracted = _extract_test_module(
                    original, function_name, class_name=class_name,
                )
                if extracted is None:
                    repair.append(scenario_id)
                    continue
                _publish_canonical_test(
                    repo,
                    change_id=change_id,
                    scenario_id=scenario_id,
                    source_text=extracted,
                    target_rel=target_rel,
                    function_name=function_name,
                    entries=entries,
                    moved=moved,
                    repair=repair,
                )
            else:
                repair.append(scenario_id)

        if source.is_file() and not _matrix_uses_file(entries, rel):
            if not _module_imported(repo, rel):
                source.unlink(missing_ok=True)
    if moved:
        matrix_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return {"moved": moved, "repair": repair}
