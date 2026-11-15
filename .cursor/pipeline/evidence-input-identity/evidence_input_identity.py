"""Идентичность входов OPSX execution receipt.

Свежесть PASS привязана к точному набору байт материальных входов:
продуктовый код, тесты, спецификации, валидаторы и конфиги сертифицируемого
изменения. Сам receipt в набор не входит. HEAD записывается как
происхождение прогона и не участвует в дайджесте.

Оракул ничего не пишет в продуктовые хранилища. Предзапусковая сверка
``AUDITED_HEAD`` в ``/opsx-close-external-gap`` остаётся отдельным
launch-guard и этим модулем не заменяется.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


CONTRACT_ID = "OPSX_EVIDENCE_INPUT_IDENTITY_V0"
ORACLE_REPO_PATH = (
    ".cursor/pipeline/evidence-input-identity/evidence_input_identity.py"
)
ROLES: tuple[str, ...] = ("product", "tests", "specs", "validators", "configs")

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_RESULT_LINE = re.compile(r"^[ \t]*RESULT:\s*(\S+)\s*$", re.MULTILINE)
_IDENTITY_LINE = re.compile(r"^[ \t]*INPUT_IDENTITY:\s*(\S+)\s*$", re.MULTILINE)
_HEAD_LINE = re.compile(r"^[ \t]*PROVENANCE_HEAD:\s*(\S+)\s*$", re.MULTILINE)
_ROOT_LINE = re.compile(r"^[ \t]*INPUT_ROOT\s+([a-z]+):\s*(\S+)\s*$", re.MULTILINE)
_ROLE_DIGEST_LINE = re.compile(
    r"^[ \t]*ROLE_DIGEST\s+([a-z]+):\s*([0-9a-f]{64})\s*$",
    re.MULTILINE,
)
_RECEIPT_PATH_LINE = re.compile(r"^[ \t]*RECEIPT_PATH:\s*(\S+)\s*$", re.MULTILINE)


class InputIdentityError(ValueError):
    """Входной набор нельзя сертифицировать: роль пуста, путь опасен или это receipt."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class InputIdentity:
    """Снимок материальных входов.

    ``digest`` не зависит от ``provenance_head`` и от байт receipt.
    """

    digest: str
    role_digests: dict[str, str]
    files: dict[str, tuple[str, ...]]
    roots: dict[str, tuple[str, ...]]
    receipt_paths: tuple[str, ...]
    provenance_head: str | None


@dataclass(frozen=True)
class Freshness:
    """Вердикт свежести ранее выпущенного PASS."""

    status: str
    reason: str
    bound_identity: str | None
    current_identity: str | None
    provenance_head_recorded: str | None
    provenance_head_current: str | None
    changed_roles: tuple[str, ...]

    @property
    def fresh(self) -> bool:
        """True только для честного совпадения PASS и input identity."""

        return self.status == "FRESH"


def is_receipt_artifact(relative_posix: str) -> bool:
    """Путь receipt: ``opsx/runs/<...>/receipts/<file>``."""

    parts = tuple(Path(relative_posix).parts)
    if "opsx" not in parts or "runs" not in parts or "receipts" not in parts:
        return False
    return parts.index("opsx") < parts.index("runs") < parts.index("receipts")


def compute_input_identity(
    root: Path,
    layout: Mapping[str, Sequence[str]],
    *,
    provenance_head: str | None = None,
    receipt_paths: Sequence[str] = (),
    require_oracle: bool = False,
) -> InputIdentity:
    """Посчитать input identity по корням ролей.

    Args:
        root: Корень дерева, от которого заданы относительные пути.
        layout: Роль → файлы или каталоги. Все пять ролей обязательны.
        provenance_head: Текущий HEAD для записи. В дайджест не входит.
        receipt_paths: Явные пути receipt, которые нельзя хешировать.
        require_oracle: Требовать этот модуль в роли ``validators``.

    Returns:
        Снимок с ``digest``, пофайловым составом и записанным HEAD.

    Raises:
        InputIdentityError: Пустая роль, receipt в списке файлов, симлинк,
            путь вне корня или отсутствующий оракул при ``require_oracle``.
    """

    files = resolve_material_files(root, layout, receipt_paths=receipt_paths)
    if require_oracle and ORACLE_REPO_PATH not in files["validators"]:
        raise InputIdentityError("ORACLE_NOT_IN_VALIDATORS", ORACLE_REPO_PATH)
    role_digests = {
        role: _digest_role(root, role, files[role]) for role in ROLES
    }
    normalized_roots = {
        role: tuple(sorted(_normalize_rel(entry) for entry in layout[role]))
        for role in ROLES
    }
    normalized_receipts = tuple(sorted(_normalize_rel(path) for path in receipt_paths))
    return InputIdentity(
        digest=_aggregate(role_digests),
        role_digests=role_digests,
        files=files,
        roots=normalized_roots,
        receipt_paths=normalized_receipts,
        provenance_head=provenance_head,
    )


def resolve_material_files(
    root: Path,
    layout: Mapping[str, Sequence[str]],
    *,
    receipt_paths: Sequence[str] = (),
) -> dict[str, tuple[str, ...]]:
    """Собрать обычные файлы пяти ролей, пропустив receipt внутри каталогов.

    Явно переданный файл-receipt отклоняется. Пустая роль отклоняется.
    """

    excluded = _receipt_exclusions(receipt_paths)
    found: dict[str, tuple[str, ...]] = {}
    seen: dict[str, str] = {}
    for role in ROLES:
        if role not in layout or not layout[role]:
            raise InputIdentityError("MATERIAL_INPUT_MISSING", role)
        collected: list[str] = []
        for entry in layout[role]:
            relative = _normalize_rel(entry)
            path = _resolve_inside(root, relative)
            if not path.exists():
                raise InputIdentityError("MATERIAL_INPUT_MISSING", relative)
            if path.is_symlink():
                raise InputIdentityError("SYMLINK_REJECTED", relative)
            if path.is_file():
                collected.extend(
                    _accept_explicit_file(root, path, excluded)
                )
            elif path.is_dir():
                collected.extend(_walk_dir(root, path, excluded))
            else:
                raise InputIdentityError("NOT_A_REGULAR_FILE", relative)
        unique = tuple(sorted(set(collected)))
        if not unique:
            raise InputIdentityError("MATERIAL_INPUT_MISSING", role)
        for rel in unique:
            previous = seen.get(rel)
            if previous is not None:
                raise InputIdentityError("DUPLICATE_INPUT", f"{rel} in {previous} and {role}")
            seen[rel] = role
        found[role] = unique
    return found


def format_pass_receipt(identity: InputIdentity, *, result: str = "PASS") -> str:
    """Собрать текст receipt: identity, корни, дайджесты ролей и provenance HEAD."""

    lines = ["PROVIDER_EXECUTION_RECEIPT:"]

    def _field(text: str) -> None:
        lines.append(f"  {text}")

    _field("SCHEMA_VERSION: 1")
    _field(f"CONTRACT_ID: {CONTRACT_ID}")
    _field(f"RESULT: {result}")
    _field(f"INPUT_IDENTITY: {identity.digest}")
    _field(f"PROVENANCE_HEAD: {identity.provenance_head or 'unknown'}")
    for path in identity.receipt_paths:
        _field(f"RECEIPT_PATH: {path}")
    for role in ROLES:
        for root_entry in identity.roots[role]:
            _field(f"INPUT_ROOT {role}: {root_entry}")
        _field(f"ROLE_DIGEST {role}: {identity.role_digests[role]}")
    return "\n".join(lines) + "\n"


def assess_pass_freshness(
    *,
    receipt_text: str,
    root: Path,
    provenance_head: str | None,
    layout: Mapping[str, Sequence[str]] | None = None,
) -> Freshness:
    """Пересчитать identity и решить, свеж ли записанный PASS.

    Совпадение дайджеста при ``RESULT=PASS`` даёт ``FRESH``, даже если
    текущий HEAD отличается от записанного. Любое изменение материальных
    байт даёт ``STALE``. Не-PASS и битая identity не считаются свежими.

    Args:
        receipt_text: Текст ранее выпущенного receipt.
        root: Текущее дерево.
        provenance_head: HEAD на момент проверки. Только для отчёта.
        layout: Если задан, должен совпасть с ``INPUT_ROOT`` receipt.

    Returns:
        Вердикт ``FRESH`` или ``STALE`` с причиной и изменившимися ролями.
    """

    parsed = _parse_receipt(receipt_text)
    recorded_head = parsed["provenance_head"]
    bound = parsed["input_identity"]
    if parsed["malformed"]:
        return _stale(
            "IDENTITY_MALFORMED",
            bound=None,
            current=None,
            recorded_head=recorded_head,
            current_head=provenance_head,
        )
    if bound is None or not parsed["roots"]:
        return _stale(
            "IDENTITY_ABSENT",
            bound=bound,
            current=None,
            recorded_head=recorded_head,
            current_head=provenance_head,
        )
    if layout is not None and _normalize_layout(layout) != parsed["roots"]:
        return _stale(
            "LAYOUT_MISMATCH",
            bound=bound,
            current=None,
            recorded_head=recorded_head,
            current_head=provenance_head,
        )
    try:
        current = compute_input_identity(
            root,
            parsed["roots"],
            provenance_head=provenance_head,
            receipt_paths=parsed["receipt_paths"],
        )
    except InputIdentityError as exc:
        return _stale(
            exc.code,
            bound=bound,
            current=None,
            recorded_head=recorded_head,
            current_head=provenance_head,
        )
    recorded_digests = parsed["role_digests"]
    changed = tuple(
        role
        for role in ROLES
        if recorded_digests.get(role) != current.role_digests[role]
    )
    if parsed["result"] != "PASS" or bound is None:
        reason = "RESULT_NOT_PASS" if parsed["result"] != "PASS" else "IDENTITY_ABSENT"
        return Freshness(
            status="STALE",
            reason=reason,
            bound_identity=bound,
            current_identity=current.digest,
            provenance_head_recorded=recorded_head,
            provenance_head_current=provenance_head,
            changed_roles=changed,
        )
    if bound != current.digest:
        return Freshness(
            status="STALE",
            reason="MATERIAL_INPUT_CHANGED",
            bound_identity=bound,
            current_identity=current.digest,
            provenance_head_recorded=recorded_head,
            provenance_head_current=provenance_head,
            changed_roles=changed,
        )
    return Freshness(
        status="FRESH",
        reason="INPUT_IDENTITY_MATCH",
        bound_identity=bound,
        current_identity=current.digest,
        provenance_head_recorded=recorded_head,
        provenance_head_current=provenance_head,
        changed_roles=(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: напечатать identity или вердикт свежести.

    ``0`` — identity посчитана или PASS свеж. ``2`` — PASS stale.
    ``1`` — набор отклонён или аргументы неверны.
    """

    parser = argparse.ArgumentParser(description=CONTRACT_ID)
    parser.add_argument("command", choices=("identity", "assess"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--layout", required=True, type=Path)
    parser.add_argument("--provenance-head", default="unknown")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--require-oracle", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        layout, receipt_paths = _load_layout(args.layout)
        if args.command == "identity":
            identity = compute_input_identity(
                args.root,
                layout,
                provenance_head=args.provenance_head,
                receipt_paths=receipt_paths,
                require_oracle=args.require_oracle,
            )
            print(f"INPUT_IDENTITY: {identity.digest}")
            print(f"PROVENANCE_HEAD: {identity.provenance_head}")
            return 0
        if args.receipt is None:
            print("STATUS: STALE", file=sys.stderr)
            print("REASON: RECEIPT_REQUIRED", file=sys.stderr)
            return 1
        verdict = assess_pass_freshness(
            receipt_text=args.receipt.read_text(encoding="utf-8"),
            root=args.root,
            provenance_head=args.provenance_head,
            layout=layout,
        )
    except (OSError, InputIdentityError, json.JSONDecodeError) as exc:
        print("STATUS: STALE", file=sys.stderr)
        print(f"REASON: {exc}", file=sys.stderr)
        return 1
    print(f"STATUS: {verdict.status}")
    print(f"REASON: {verdict.reason}")
    if verdict.current_identity is not None:
        print(f"INPUT_IDENTITY: {verdict.current_identity}")
    if verdict.changed_roles:
        print(f"CHANGED_ROLES: {','.join(verdict.changed_roles)}")
    return 0 if verdict.fresh else 2


def _stale(
    reason: str,
    *,
    bound: str | None,
    current: str | None,
    recorded_head: str | None,
    current_head: str | None,
) -> Freshness:
    return Freshness(
        status="STALE",
        reason=reason,
        bound_identity=bound,
        current_identity=current,
        provenance_head_recorded=recorded_head,
        provenance_head_current=current_head,
        changed_roles=(),
    )


def _parse_receipt(receipt_text: str) -> dict[str, object]:
    result_match = _RESULT_LINE.search(receipt_text)
    identity_match = _IDENTITY_LINE.search(receipt_text)
    head_match = _HEAD_LINE.search(receipt_text)
    roots: dict[str, list[str]] = {role: [] for role in ROLES}
    for role, entry in _ROOT_LINE.findall(receipt_text):
        if role in roots:
            roots[role].append(entry)
    role_digests = {role: digest for role, digest in _ROLE_DIGEST_LINE.findall(receipt_text)}
    receipt_paths = tuple(_RECEIPT_PATH_LINE.findall(receipt_text))
    identity = identity_match.group(1) if identity_match is not None else None
    malformed = identity is not None and _HEX64.fullmatch(identity) is None
    present_roots = {
        role: tuple(sorted(entries))
        for role, entries in roots.items()
        if entries
    }
    return {
        "result": result_match.group(1) if result_match is not None else "",
        "input_identity": None if malformed else identity,
        "malformed": malformed,
        "provenance_head": head_match.group(1) if head_match is not None else None,
        "roots": present_roots,
        "role_digests": role_digests,
        "receipt_paths": receipt_paths,
    }


def _receipt_exclusions(receipt_paths: Sequence[str]) -> set[str]:
    """Принять только настоящие receipt-пути. Материальный файл исключать нельзя."""

    excluded: set[str] = set()
    for path in receipt_paths:
        relative = _normalize_rel(path)
        if not is_receipt_artifact(relative):
            raise InputIdentityError("RECEIPT_PATH_NOT_ARTIFACT", relative)
        excluded.add(relative)
    return excluded


def _normalize_layout(layout: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, ...]]:
    return {
        role: tuple(sorted(_normalize_rel(entry) for entry in layout[role]))
        for role in ROLES
        if role in layout and layout[role]
    }


def _load_layout(path: Path) -> tuple[dict[str, list[str]], tuple[str, ...]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise InputIdentityError("LAYOUT_INVALID", str(path))
    layout: dict[str, list[str]] = {}
    for role in ROLES:
        entries = raw.get(role)
        if not isinstance(entries, list) or not entries:
            raise InputIdentityError("MATERIAL_INPUT_MISSING", role)
        layout[role] = [str(item) for item in entries]
    receipt_raw = raw.get("receipt_paths", [])
    if not isinstance(receipt_raw, list):
        raise InputIdentityError("LAYOUT_INVALID", "receipt_paths")
    receipt_paths = tuple(str(item) for item in receipt_raw)
    return layout, receipt_paths


def _digest_role(root: Path, role: str, paths: Sequence[str]) -> str:
    hasher = hashlib.sha256()
    for rel in paths:
        hasher.update(role.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update((root / rel).read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def _aggregate(role_digests: Mapping[str, str]) -> str:
    hasher = hashlib.sha256()
    for role in ROLES:
        hasher.update(role.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(role_digests[role].encode("utf-8"))
        hasher.update(b"\0")
    return hasher.hexdigest()


def _normalize_rel(entry: str) -> str:
    if not entry or "\\" in entry or entry.startswith("/"):
        raise InputIdentityError("PATH_ESCAPE", entry)
    pure = Path(entry)
    if pure.is_absolute() or ".." in pure.parts:
        raise InputIdentityError("PATH_ESCAPE", entry)
    normalized = Path(entry).as_posix().removeprefix("./")
    if not normalized or normalized == ".":
        raise InputIdentityError("PATH_ESCAPE", entry)
    return normalized


def _resolve_inside(root: Path, relative: str) -> Path:
    root_resolved = root.resolve()
    candidate = (root / relative).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise InputIdentityError("PATH_ESCAPE", relative)
    return candidate


def _accept_explicit_file(
    root: Path,
    path: Path,
    excluded: set[str],
) -> list[str]:
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    if relative in excluded or is_receipt_artifact(relative):
        raise InputIdentityError("RECEIPT_EXCLUDED", relative)
    if not path.is_file() or path.is_symlink():
        raise InputIdentityError("NOT_A_REGULAR_FILE", relative)
    return [relative]


def _walk_dir(root: Path, start: Path, excluded: set[str]) -> list[str]:
    found: list[str] = []
    root_resolved = root.resolve()
    for dirpath, dirnames, filenames in os.walk(start):
        current = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            child = current / name
            if name == ".git":
                continue
            if child.is_symlink():
                raise InputIdentityError("SYMLINK_REJECTED", child.relative_to(root).as_posix())
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            file_path = current / name
            relative = file_path.resolve().relative_to(root_resolved).as_posix()
            if file_path.is_symlink():
                raise InputIdentityError("SYMLINK_REJECTED", relative)
            if not file_path.is_file():
                raise InputIdentityError("NOT_A_REGULAR_FILE", relative)
            if relative in excluded or is_receipt_artifact(relative):
                continue
            found.append(relative)
    return found


if __name__ == "__main__":
    sys.exit(main())
