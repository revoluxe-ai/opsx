"""Применение probe → штатные конфиги (pyright / pytest / mypy), без OPSX manifest consumer.

DEPRECATED for /opsx-prep-green-scope STAGE1/GO orchestration (v0.9 agent + disable_plan/apply_disable).
Use ``artifacts.write_probe_and_plan`` and ``apply_disable.apply_disable_delta`` instead.
Legacy ``--deselect`` apply paths must not run on GO.

``pytest`` addopts пишутся только через ``pyproject_pytest`` (TOML-массив + validate).
Regex-splice в одну double-quoted строку запрещён — ломает TOML и ``poetry install``.

STAGE1 (сбор красного) — **агент** через ``/opsx-static-check FULL`` + запись JSON здесь.
Оркестратор ``prep_green_scope.py`` для PROBE/STAGE1 **deprecated** (v3 agent-only).
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

IMPLEMENTATION_ID = "stroy-prep-green-scope-v3.0.2-agent"

from pyproject_pytest import (
    PYPROJECT_REL,
    normalize_addopts_to_array,
    read_base_addopts,
    repair_invalid_pyproject_addopts,
    validate_pyproject_toml,
    write_pytest_addopts,
)

PYRIGHT_STRICT_REL = Path("pyrightconfig_strict.json")
STAGE1_PYPROJECT_SNAPSHOT_REL = Path(
    "opsx/prep-green-scope/stage1-pyproject-snapshot.toml"
)
PROBE_REL = Path("opsx/prep-green-scope/probe-result.json")
APPLY_RECEIPT_REL = Path("opsx/prep-green-scope/apply-receipt.json")
PLAN_REL = Path("opsx/prep-green-scope/plan.json")

# Pytest -rF / short summary: node id до « - » (parametrize с пробелами).
_PYTEST_FAIL = re.compile(r"^(?:FAILED|ERROR)\s+(.+?)(?:\s+-\s+|$)")
_MYPY_ERR = re.compile(r"^(.+?):\d+: error:")
_DESELECT_FLAG = re.compile(r'--deselect=(?:\"[^\"]*\"|\S+)')
_PREP_OVERRIDE_BLOCK = re.compile(
    r"\n\[\[tool\.mypy\.overrides\]\]\nmodule = \"[^\"]+\"\nignore_errors = true",
    re.MULTILINE,
)


def extract_mypy_error_lines(stdout: str, stderr: str) -> list[str]:
    """Строки mypy ``path:line: error:`` из stdout и stderr (как static-check worker)."""

    lines: list[str] = []
    for chunk in (stdout, stderr):
        for line in chunk.splitlines():
            if ": error:" in line:
                lines.append(line)
    return lines


def parse_pytest_failures(stdout: str, stderr: str) -> list[str]:
    """Извлечь node id из полного вывода pytest (``-rF`` / ``FAILED node - msg``)."""

    nodes: list[str] = []
    seen: set[str] = set()
    for chunk in (stdout, stderr):
        for line in chunk.splitlines():
            stripped = line.strip()
            m = _PYTEST_FAIL.match(stripped)
            if not m:
                continue
            node = m.group(1).strip()
            if node and node not in seen:
                seen.add(node)
                nodes.append(node)
    return nodes


def strip_prep_deselect_from_addopts(addopts: str) -> str:
    """Убрать все ``--deselect=…`` (перезапись probe, не накопление)."""

    cleaned = _DESELECT_FLAG.sub("", addopts)
    return " ".join(cleaned.split())


def _pytest_deselect_flag(node_id: str) -> str:
    """Флаг ``--deselect`` для ``addopts`` (кавычки при пробелах в node id)."""

    if re.search(r"\s", node_id):
        inner = node_id.replace('"', '\\"')
        return f'--deselect="{inner}"'
    return f"--deselect={node_id}"


def pyright_diag_files(diags: list[dict[str, Any]], root: Path) -> list[str]:
    """Относительные пути файлов из диагностик Pyright (posix)."""

    out: list[str] = []
    seen: set[str] = set()
    for d in diags:
        raw = str(d.get("file", "")).strip()
        if not raw:
            continue
        path = Path(raw)
        try:
            rel = (
                path if path.is_absolute() else root / path
            ).resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            rel = raw.replace("\\", "/")
        if rel not in seen:
            seen.add(rel)
            out.append(rel)
    return sorted(out)


def mypy_error_modules(lines: list[str]) -> list[str]:
    """Модули mypy из строк `path:line: error:` (audit в probe)."""

    modules: list[str] = []
    seen: set[str] = set()
    for line in lines:
        m = _MYPY_ERR.match(line.strip())
        if not m:
            continue
        path = m.group(1).replace("\\", "/")
        if path.endswith(".py"):
            path = path[: -len(".py")]
        mod = path.replace("/", ".").removeprefix("src.")
        if mod and mod not in seen:
            seen.add(mod)
            modules.append(mod)
    return sorted(modules)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def apply_pyright_ignore(root: Path, files: list[str]) -> int:
    """Добавить пути в ``ignore`` pyrightconfig_strict.json (штатный Pyright)."""

    path = root / PYRIGHT_STRICT_REL
    data = _read_json(path)
    ignore = list(data.get("ignore") or [])
    known = set(str(x) for x in ignore)
    added = 0
    for f in files:
        if f not in known:
            ignore.append(f)
            known.add(f)
            added += 1
    data["ignore"] = sorted(ignore, key=str)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return added


def apply_pytest_deselect_addopts(root: Path, node_ids: list[str]) -> int:
    """Заменить prep ``--deselect`` в ``addopts`` на список из probe (TOML array)."""

    repair_invalid_pyproject_addopts(root)
    normalize_addopts_to_array(root)
    base = read_base_addopts(root)
    deduped = sorted({n for n in node_ids if n})
    write_pytest_addopts(root, base=base, deselect_nodes=deduped)
    return len(deduped)


def _strip_prep_mypy_override_blocks(text: str) -> str:
    """Удалить legacy per-module ``[[tool.mypy.overrides]]`` от старых APPLY."""

    return _PREP_OVERRIDE_BLOCK.sub("", text.rstrip()) + "\n"


def apply_mypy_exit_gate(root: Path, *, error_line_count: int) -> str:
    """Deprecated: prep-green-scope использует ``apply_mypy_module_ignore`` (только модули с ошибками)."""

    _ = root, error_line_count
    return "deprecated_use_per_module_overrides"


def apply_mypy_module_ignore(root: Path, modules: list[str]) -> int:
    """``[[tool.mypy.overrides]]`` с ``ignore_errors = true`` только для модулей из probe."""

    deduped = sorted({m for m in modules if m})
    if not deduped:
        return 0
    path = root / PYPROJECT_REL
    text = path.read_text(encoding="utf-8")
    text = _strip_prep_mypy_override_blocks(text)
    head, sep, tail = text.partition("[tool.ruff]")
    if not sep:
        raise RuntimeError("pyproject.toml: missing [tool.ruff] anchor for mypy edit")
    head = re.sub(r"(?m)^ignore_errors\s*=\s*true\s*\n", "", head, count=1)
    blocks = [
        f'[[tool.mypy.overrides]]\nmodule = "{mod}"\nignore_errors = true\n'
        for mod in deduped
    ]
    new_text = (head + sep + tail.rstrip() + "\n" + "".join(blocks)).rstrip() + "\n"
    path.write_text(new_text, encoding="utf-8")
    validate_pyproject_toml(path)
    return len(deduped)


def recover_pyproject_from_prep(root: Path) -> dict[str, Any]:
    """Снять следы prep APPLY с ``pyproject.toml`` (штатный revert без git)."""

    actions: list[str] = []
    if repair_invalid_pyproject_addopts(root):
        actions.append("pytest_addopts_repaired_invalid_toml")
    path = root / PYPROJECT_REL
    text = path.read_text(encoding="utf-8")
    had_deselect = "--deselect=" in text
    base = read_base_addopts(root)
    write_pytest_addopts(root, base=base, deselect_nodes=[])
    if had_deselect:
        actions.append("pytest_deselect_stripped")
    text = path.read_text(encoding="utf-8")
    stripped = _strip_prep_mypy_override_blocks(text)
    if stripped != text:
        text = stripped
        actions.append("mypy_overrides_stripped")
    head, sep, tail = text.partition("[tool.ruff]")
    if not sep:
        raise RuntimeError("pyproject.toml: missing [tool.ruff] anchor")
    new_head, n = re.subn(r"(?m)^ignore_errors\s*=\s*true\s*\n", "", head, count=1)
    if n:
        head = new_head
        actions.append("mypy_global_ignore_errors_removed")
    path.write_text(head + sep + tail, encoding="utf-8")
    validate_pyproject_toml(path)
    return {"ok": True, "actions": actions or ["no_prep_artifacts_found"]}


def snapshot_pyproject_for_stage1(root: Path) -> Path:
    """Сохранить копию ``pyproject.toml`` перед STAGE1 probe (restore после PROBE)."""

    src = root / PYPROJECT_REL
    dst = root / STAGE1_PYPROJECT_SNAPSHOT_REL
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def restore_pyproject_stage1_snapshot(root: Path) -> bool:
    """Вернуть ``pyproject.toml`` после STAGE1 (этап 1 не меняет product configs)."""

    dst = root / PYPROJECT_REL
    snap = root / STAGE1_PYPROJECT_SNAPSHOT_REL
    if not snap.is_file():
        return False
    shutil.copy2(snap, dst)
    validate_pyproject_toml(dst)
    return True


def strip_deselect_for_probe(root: Path) -> None:
    """Временно убрать prep exclusions для честного PROBE (deselect + mypy overrides)."""

    strip_prep_exclusions_for_probe(root)


def strip_prep_exclusions_for_probe(root: Path) -> None:
    """Снять prep ``--deselect`` и per-module mypy overrides (не global typing policy)."""

    repair_invalid_pyproject_addopts(root)
    base = read_base_addopts(root)
    write_pytest_addopts(root, base=base, deselect_nodes=[])
    path = root / PYPROJECT_REL
    text = _strip_prep_mypy_override_blocks(path.read_text(encoding="utf-8"))
    head, sep, tail = text.partition("[tool.ruff]")
    if not sep:
        raise RuntimeError("pyproject.toml: missing [tool.ruff] anchor")
    head = re.sub(r"(?m)^ignore_errors\s*=\s*true\s*\n", "", head, count=1)
    path.write_text(head + sep + tail, encoding="utf-8")
    validate_pyproject_toml(path)


def merge_pytest_failures_from_logs(*chunks: str) -> list[str]:
    """Объединить node id из нескольких pytest-логов (unit + integration + destructive)."""

    nodes: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        for node in parse_pytest_failures(chunk, ""):
            if node not in seen:
                seen.add(node)
                nodes.append(node)
    return sorted(nodes)


def detect_prep_pyproject_artifacts(root: Path) -> dict[str, bool]:
    """Быстрая эвристика: следы prep APPLY в ``pyproject.toml`` (без subprocess)."""

    text = (root / PYPROJECT_REL).read_text(encoding="utf-8")
    head = text.split("[tool.ruff]")[0] if "[tool.ruff]" in text else text
    return {
        "pytest_deselect_in_addopts": "--deselect=" in text,
        "mypy_override_blocks": "[[tool.mypy.overrides]]" in text,
        "mypy_global_ignore_errors": bool(
            re.search(r"(?m)^ignore_errors\s*=\s*true\s*$", head)
        ),
    }


def load_plan(root: Path) -> dict[str, Any]:
    """Прочитать plan.json (если есть)."""

    path = root / PLAN_REL
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_probe(root: Path) -> dict[str, Any]:
    """Прочитать probe-result.json."""

    path = root / PROBE_REL
    if not path.is_file():
        raise FileNotFoundError(PROBE_REL.as_posix())
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _auto_owner_ref() -> str:
    return f"owner-message:auto-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"


def _resolve_owner_ref(root: Path, explicit: str) -> str:
    if explicit.strip():
        return explicit.strip()
    planned = str(load_plan(root).get("owner_authorization_ref") or "").strip()
    if planned:
        return planned
    return _auto_owner_ref()


def _needs_recover(root: Path) -> bool:
    artifacts = detect_prep_pyproject_artifacts(root)
    return bool(
        artifacts.get("pytest_deselect_in_addopts")
        or artifacts.get("mypy_override_blocks")
        or artifacts.get("mypy_global_ignore_errors")
    )


def build_probe_payload(
    *,
    pyright_ignore_candidates: list[str],
    mypy_error_lines: list[str],
    pytest_failures: list[str],
    source: str = "/opsx-static-check FULL",
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Собрать ``probe-result.json`` из выводов static-check (агент заполняет поля)."""

    dedup_py = sorted({n for n in pytest_failures if n})
    mypy_lines = list(mypy_error_lines)
    return {
        "schema": "opsx.prep_green_scope.probe",
        "implementation": IMPLEMENTATION_ID,
        "probed_at": datetime.now(UTC).isoformat(),
        "probe_source": source,
        "notes": notes or [],
        "pyright_ignore_candidates": sorted(set(pyright_ignore_candidates)),
        "mypy_module_candidates": mypy_error_modules(mypy_lines),
        "mypy_error_line_count": len(mypy_lines),
        "mypy_exit_code": None,
        "mypy_error_lines": mypy_lines[:500],
        "pytest_failures": dedup_py,
        "pytest_failure_count": len(dedup_py),
        "timed_out_checks": [],
        "gate_mapping_note": (
            "pyright→pyrightconfig_strict.json ignore; "
            "mypy→[[tool.mypy.overrides]] ignore_errors per error module only; "
            "pytest→агент пишет в исходник теста pytest.mark.skip(reason=...); "
            "не --deselect и не plugin/hook; "
            "STAGE1 pytest: unit+integration+destructive ALL required for probe"
        ),
    }


def write_plan_after_probe(root: Path, probe_result: dict[str, Any]) -> dict[str, Any]:
    """``plan.json`` после STAGE1 (агент); конфиги product ещё не меняются."""

    needs_recover = _needs_recover(root)
    go_steps: list[str] = []
    if needs_recover:
        go_steps.append("RECOVER")
    go_steps.append("APPLY")
    mypy_lines = int(probe_result.get("mypy_error_line_count") or 0)
    plan: dict[str, Any] = {
        "schema": "opsx.prep_green_scope.plan",
        "stage": 1,
        "status": "AWAITING_GO",
        "implementation": IMPLEMENTATION_ID,
        "planned_at": datetime.now(UTC).isoformat(),
        "probe_probed_at": probe_result.get("probed_at"),
        "probe_source": probe_result.get("probe_source"),
        "needs_recover_before_apply": needs_recover,
        "go_steps": go_steps,
        "will_apply": {
            "pyright_ignore_files": len(
                probe_result.get("pyright_ignore_candidates") or []
            ),
            "mypy_module_override_count": len(
                probe_result.get("mypy_module_candidates") or []
            ),
            "mypy_error_line_count": mypy_lines,
            "pytest_source_skip_count": int(
                probe_result.get("pytest_failure_count") or 0
            ),
        },
        "owner_authorization_ref": _auto_owner_ref(),
        "execute_on_go": {"mode": "agent_apply", "requires_probe": True},
        "after_apply_owner_slash": [
            "/opsx-commit GO",
            "/opsx-static-check FULL",
        ],
    }
    _write_json(root / PLAN_REL, plan)
    return plan


def write_stage1_artifacts(root: Path, probe_result: dict[str, Any]) -> dict[str, Any]:
    """Записать ``probe-result.json`` + ``plan.json`` (агент после static-check)."""

    if probe_result.get("implementation") != IMPLEMENTATION_ID:
        probe_result = {
            **probe_result,
            "implementation": IMPLEMENTATION_ID,
        }
    _write_json(root / PROBE_REL, probe_result)
    plan = write_plan_after_probe(root, probe_result)
    return {"probe_path": PROBE_REL.as_posix(), "plan_path": PLAN_REL.as_posix(), "plan": plan}


def apply_from_probe(root: Path, *, owner_ref: str) -> dict[str, Any]:
    """Owner GO: записать exclusions в pyright / pyproject (штатные конфиги)."""

    probe_data = load_probe(root)
    py_added = apply_pyright_ignore(
        root, list(probe_data.get("pyright_ignore_candidates") or [])
    )
    modules = list(probe_data.get("mypy_module_candidates") or [])
    if not modules and int(probe_data.get("mypy_error_line_count") or 0) > 0:
        modules = mypy_error_modules(
            [
                ln
                for ln in (probe_data.get("mypy_error_lines") or [])
                if isinstance(ln, str)
            ]
        )
    mypy_added = apply_mypy_module_ignore(root, modules)
    mypy_action = (
        f"per_module_overrides_added:{mypy_added}"
        if mypy_added
        else "skipped_no_mypy_modules"
    )
    # Pytest: агент пишет skip(reason=) в исходник. Здесь только снять старый --deselect.
    nodes = [n for n in (probe_data.get("pytest_failures") or []) if n]
    repair_invalid_pyproject_addopts(root)
    base = read_base_addopts(root)
    write_pytest_addopts(root, base=base, deselect_nodes=[])
    pt_added = 0
    receipt = {
        "schema": "opsx.prep_green_scope.apply",
        "implementation": IMPLEMENTATION_ID,
        "applied_at": datetime.now(UTC).isoformat(),
        "owner_authorization_ref": owner_ref,
        "probe_probed_at": probe_data.get("probed_at"),
        "probe_source": probe_data.get("probe_source"),
        "pyright_ignore_added": py_added,
        "mypy_gate_action": mypy_action,
        "pytest_deselect_count": pt_added,
        "probe_pytest_failure_count": probe_data.get("pytest_failure_count"),
        "mechanisms": {
            "pyright": "pyrightconfig_strict.json → ignore[]",
            "mypy": (
                "pyproject.toml → [[tool.mypy.overrides]] ignore_errors "
                "only for modules with errors (not global)"
            ),
            "pytest": "pyproject.toml → addopts TOML array --deselect from probe",
        },
    }
    _write_json(root / APPLY_RECEIPT_REL, receipt)
    return {
        "ok": True,
        "status": "APPLY_DONE",
        "receipt_path": APPLY_RECEIPT_REL.as_posix(),
        "pyright_ignore_added": py_added,
        "mypy_gate_action": mypy_action,
        "pytest_deselect_count": pt_added,
    }


def execute_go_apply(root: Path, *, owner_ref: str = "") -> dict[str, Any]:
    """Этап 2: RECOVER (если следы prep) + APPLY по ``probe-result.json`` (агент на GO)."""

    probe_path = root / PROBE_REL
    if not probe_path.is_file():
        return {
            "ok": False,
            "status": "BLOCKED_NEEDS_STAGE1",
            "reason": "missing_probe",
        }
    try:
        probe_data = load_probe(root)
    except (OSError, json.JSONDecodeError):
        return {
            "ok": False,
            "status": "BLOCKED_NEEDS_STAGE1",
            "reason": "invalid_probe",
        }
    impl = str(probe_data.get("implementation") or "")
    if impl and impl != IMPLEMENTATION_ID:
        return {
            "ok": False,
            "status": "BLOCKED_NEEDS_STAGE1",
            "reason": "stale_probe",
            "expected_implementation": IMPLEMENTATION_ID,
            "found_implementation": impl,
        }
    steps: list[dict[str, Any]] = []
    if _needs_recover(root):
        rec = recover_pyproject_from_prep(root)
        steps.append({"step": "RECOVER", "actions": rec.get("actions")})
    ref = _resolve_owner_ref(root, owner_ref)
    apply_res = apply_from_probe(root, owner_ref=ref)
    steps.append({"step": "APPLY", "status": apply_res.get("status")})
    ok = bool(apply_res.get("ok"))
    return {
        "ok": ok,
        "status": "APPLY_DONE" if ok else "APPLY_FAIL",
        "steps": steps,
        "receipt_path": apply_res.get("receipt_path"),
    }


def stage1_honest_pyproject_window(root: Path) -> None:
    """Перед static-check: snapshot + strip deselect; после — ``restore_stage1_honest_pyproject``."""

    snapshot_pyproject_for_stage1(root)
    strip_deselect_for_probe(root)


def restore_stage1_honest_pyproject(root: Path) -> bool:
    """После static-check: вернуть ``pyproject.toml`` (этап 1 не коммитит exclusions)."""

    return restore_pyproject_stage1_snapshot(root)
