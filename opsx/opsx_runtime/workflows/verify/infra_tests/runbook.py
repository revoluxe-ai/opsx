"""Хранилище runbook: proposed становится verified только после успешного доказательства.

Писатель один. Revision сравнивается перед записью (CAS).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class RunbookConflict(RuntimeError):
    """Запись с чужой revision. Канон не перезаписывается молча."""


@dataclass(frozen=True)
class KnownIssue:
    """Одна запись известных особенностей среды."""

    id: str
    signature: str
    symptom: str
    remedy: str
    status: str
    evidence: str = ""


@dataclass
class RunbookDocument:
    """Документ known-issues плюс revision."""

    revision: int
    issues: list[KnownIssue]

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "issues": [
                {
                    "id": row.id,
                    "signature": row.signature,
                    "symptom": row.symptom,
                    "remedy": row.remedy,
                    "status": row.status,
                    "evidence": row.evidence,
                }
                for row in self.issues
            ],
        }


def load_runbook(path: Path) -> RunbookDocument:
    """Прочитать known-issues.yaml. Пустой список допустим."""
    if not path.is_file():
        return RunbookDocument(revision=0, issues=[])
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"runbook must be a mapping: {path}")
    rows = raw.get("issues") or []
    issues = [
        KnownIssue(
            id=str(row.get("id") or ""),
            signature=str(row.get("signature") or ""),
            symptom=str(row.get("symptom") or ""),
            remedy=str(row.get("remedy") or ""),
            status=str(row.get("status") or "proposed"),
            evidence=str(row.get("evidence") or ""),
        )
        for row in rows
        if isinstance(row, dict)
    ]
    return RunbookDocument(revision=int(raw.get("revision") or 0), issues=issues)


def save_runbook(path: Path, doc: RunbookDocument, *, expected_revision: int) -> RunbookDocument:
    """Записать следующий revision. Несовпадение expected — RunbookConflict."""
    current = load_runbook(path)
    if current.revision != expected_revision:
        raise RunbookConflict(
            f"runbook revision {current.revision} != expected {expected_revision}"
        )
    stored = RunbookDocument(revision=expected_revision + 1, issues=list(doc.issues))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(stored.to_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return stored


def propose_issue(doc: RunbookDocument, issue: KnownIssue) -> RunbookDocument:
    """Добавить запись только как proposed."""
    if issue.status != "proposed":
        raise ValueError("new runbook rows start as proposed")
    return RunbookDocument(revision=doc.revision, issues=[*doc.issues, issue])


def promote_verified(doc: RunbookDocument, issue_id: str, *, passed: bool, evidence: str) -> RunbookDocument:
    """verified только если следующий probe или run реально прошёл."""
    if not passed:
        raise ValueError("refusing to promote without a passing follow-up")
    updated: list[KnownIssue] = []
    found = False
    for row in doc.issues:
        if row.id == issue_id:
            found = True
            updated.append(
                KnownIssue(
                    id=row.id,
                    signature=row.signature,
                    symptom=row.symptom,
                    remedy=row.remedy,
                    status="verified",
                    evidence=evidence,
                )
            )
        else:
            updated.append(row)
    if not found:
        raise KeyError(issue_id)
    return RunbookDocument(revision=doc.revision, issues=updated)


def render_runbook_md(profile_name: str, doc: RunbookDocument) -> str:
    """Человекочитаемый runbook из данных. Агент этот файл руками не редактирует."""
    lines = [
        f"# Infra runbook ({profile_name})",
        "",
        f"Revision: {doc.revision}",
        "",
        "Этот файл собран из профиля и known-issues. Не править вручную.",
        "",
    ]
    if not doc.issues:
        lines.append("Известных особенностей пока нет.")
    for row in doc.issues:
        lines.extend(
            [
                f"## {row.id} ({row.status})",
                "",
                f"- signature: `{row.signature}`",
                f"- symptom: {row.symptom}",
                f"- remedy: {row.remedy}",
                f"- evidence: {row.evidence or '(none)'}",
                "",
            ]
        )
    return "\n".join(lines)
