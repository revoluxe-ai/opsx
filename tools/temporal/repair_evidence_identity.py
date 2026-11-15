"""Идентичность evidence ремонта: pre-materialize vs post-materialize.

Контур Bug Hunt считает агента не вредоносным, но способным ошибиться.
Успех доказывает наблюдаемый in-scope diff и фактический Git, а не проза,
самохешированный JSON или наличие грязного дерева. Receipt OPSX может
подтверждать hop, но сам по себе переход не авторизует. Криптоподпись и
изоляция ОС не требуются и не заявляются.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MATERIALIZE_DIR = Path("opsx/runs/bug-cases/materialize")
SCHEMA_VERSION = "temporal-repair-evidence-v1"
PHASE_PRE = "pre_materialize"
PHASE_POST = "post_materialize"
SUCCESS_RECEIPT_STATUSES = frozenset({"closed_fixed"})

TERMINAL_GAP = "BLOCKED_EVIDENCE_GAP"
TERMINAL_IDENTITY = "BLOCKED_EVIDENCE_IDENTITY"
TERMINAL_INTEGRITY = "BLOCKED_EVIDENCE_INTEGRITY"
TERMINAL_ORDERING = "BLOCKED_EVIDENCE_ORDERING"


@dataclass(frozen=True)
class RepairEvidenceContext:
    """Идентичность текущего repair hop, с которой сверяется receipt."""

    orchestration_execution_id: str
    workflow_id: str
    repair_number: int
    review_fingerprint: str
    base_sha: str
    head_sha: str
    workflow_run_id: str = ""

    def require_complete(self) -> None:
        missing = [
            name
            for name, value in (
                ("orchestration_execution_id", self.orchestration_execution_id),
                ("workflow_id", self.workflow_id),
                ("review_fingerprint", self.review_fingerprint),
                ("base_sha", self.base_sha),
                ("head_sha", self.head_sha),
            )
            if not str(value).strip()
        ]
        if self.repair_number < 1:
            missing.append("repair_number")
        if missing:
            raise ValueError("unresolved repair evidence identity: " + ",".join(missing))


@dataclass(frozen=True)
class EvidenceVerdict:
    """Вердикт гейта. ``authorized`` разрешает следующий шаг control plane.

    Для COMPLETE_NOOP это не успешный ремонт: caller оставляет
    ``candidate_transitioned`` ложным.
    """

    authorized: bool
    terminal: str | None
    reason: str
    content_sha256: str | None = None


def receipt_path(context: RepairEvidenceContext) -> Path:
    """Детерминированный путь receipt этого hop. Не glob по mtime."""

    name = (
        f"repair-{context.orchestration_execution_id}-r{context.repair_number}.json"
    )
    return MATERIALIZE_DIR / name


def canonical_content_sha256(payload: dict[str, Any]) -> str:
    """SHA-256 канонического JSON без поля ``content_sha256``."""

    body = {key: value for key, value in payload.items() if key != "content_sha256"}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def context_from_mapping(payload: dict[str, Any] | None) -> RepairEvidenceContext | None:
    """Собрать контекст из activity payload. Неполный mapping → None (fail-closed выше)."""

    if not payload:
        return None
    try:
        repair_number = int(payload.get("repair_number") or 0)
    except (TypeError, ValueError):
        return None
    context = RepairEvidenceContext(
        orchestration_execution_id=str(payload.get("orchestration_execution_id") or ""),
        workflow_id=str(payload.get("workflow_id") or ""),
        repair_number=repair_number,
        review_fingerprint=str(payload.get("review_fingerprint") or ""),
        base_sha=str(payload.get("base_sha") or payload.get("base") or ""),
        head_sha=str(payload.get("head_sha") or payload.get("head") or ""),
        workflow_run_id=str(payload.get("workflow_run_id") or ""),
    )
    try:
        context.require_complete()
    except ValueError:
        return None
    return context


def _verdict(authorized: bool, terminal: str | None, reason: str, digest: str | None = None) -> EvidenceVerdict:
    return EvidenceVerdict(authorized, terminal, reason, digest)


def _identity_matches(payload: dict[str, Any], context: RepairEvidenceContext) -> str | None:
    if str(payload.get("orchestration_execution_id") or "") != context.orchestration_execution_id:
        return "foreign orchestration_execution_id"
    if str(payload.get("workflow_id") or "") != context.workflow_id:
        return "foreign workflow_id"
    try:
        repair_number = int(payload.get("repair_number"))
    except (TypeError, ValueError):
        return "repair_number missing"
    if repair_number != context.repair_number:
        return "stale or replayed repair_number"
    if str(payload.get("review_fingerprint") or "") != context.review_fingerprint:
        return "review_fingerprint mismatch"
    revision = payload.get("revision_identity")
    if not isinstance(revision, dict):
        return "revision_identity missing"
    if str(revision.get("base_sha") or "") != context.base_sha:
        return "base_sha mismatch"
    if str(revision.get("head_sha") or "") != context.head_sha:
        return "head_sha mismatch"
    run_id = str(payload.get("workflow_run_id") or "")
    if context.workflow_run_id and run_id and run_id != context.workflow_run_id:
        return "workflow_run_id mismatch"
    return None


def _load_receipt(
    repo_path: Path, context: RepairEvidenceContext
) -> tuple[dict[str, Any] | None, EvidenceVerdict | None]:
    """Прочитать канонический receipt. Отсутствие файла — не ошибка.

    Returns:
        ``(payload, None)`` для согласованного JSON, ``(None, None)`` если файла
        нет, иначе ``(None, blocking verdict)``.
    """

    path = repo_path / receipt_path(context)
    if not path.is_file():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, _verdict(False, TERMINAL_INTEGRITY, f"receipt unreadable: {exc}")
    if not isinstance(payload, dict):
        return None, _verdict(False, TERMINAL_INTEGRITY, "receipt root must be an object")
    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        return None, _verdict(False, TERMINAL_INTEGRITY, "schema_version mismatch")
    expected = canonical_content_sha256(payload)
    actual = str(payload.get("content_sha256") or "")
    if actual != expected:
        return None, _verdict(False, TERMINAL_INTEGRITY, "content_sha256 mismatch", expected)
    return payload, None


def _supporting_receipt(repo_path: Path, context: RepairEvidenceContext) -> EvidenceVerdict | None:
    """Проверить receipt, если он есть. Отсутствие — не ошибка и не доказательство.

    Returns:
        None, если файла нет или он согласован с hop. Иначе блокирующий вердикт.
    """

    payload, blocked = _load_receipt(repo_path, context)
    if blocked is not None or payload is None:
        return blocked
    expected = str(payload.get("content_sha256") or "")
    phase = str(payload.get("evidence_phase") or "")
    if phase == PHASE_POST:
        return _verdict(
            False,
            TERMINAL_ORDERING,
            "post-materialize receipt cannot authorize materialize",
            expected,
        )
    if phase != PHASE_PRE:
        return _verdict(False, TERMINAL_ORDERING, f"evidence_phase {phase!r} is not pre_materialize", expected)
    mismatch = _identity_matches(payload, context)
    if mismatch:
        return _verdict(False, TERMINAL_IDENTITY, mismatch, expected)
    status = str(payload.get("terminal_status") or "")
    if status not in SUCCESS_RECEIPT_STATUSES:
        return _verdict(False, TERMINAL_GAP, f"receipt terminal_status {status!r} is not success", expected)
    return None


def _post_receipt_conflict(
    repo_path: Path,
    context: RepairEvidenceContext,
    *,
    head_after: str,
    commit_status: str,
) -> EvidenceVerdict | None:
    """Отклонить противоречивый post-receipt. Отсутствие и pre-файл доказательством не являются."""

    payload, blocked = _load_receipt(repo_path, context)
    if blocked is not None or payload is None:
        return blocked
    phase = str(payload.get("evidence_phase") or "")
    if phase == PHASE_PRE:
        return None
    expected = str(payload.get("content_sha256") or "")
    if phase != PHASE_POST:
        return _verdict(False, TERMINAL_ORDERING, f"evidence_phase {phase!r} is not post_materialize", expected)
    mismatch = _identity_matches(payload, context)
    if mismatch:
        return _verdict(False, TERMINAL_IDENTITY, mismatch, expected)
    claimed_after = str(payload.get("head_after") or "")
    if claimed_after and claimed_after != head_after:
        return _verdict(
            False,
            TERMINAL_IDENTITY,
            "post receipt head_after disagrees with observed HEAD",
            expected,
        )
    status = str(payload.get("terminal_status") or "")
    if commit_status == "COMPLETE_NOOP" and status in SUCCESS_RECEIPT_STATUSES:
        return _verdict(
            False,
            TERMINAL_GAP,
            "post receipt claims a repair while commit-all preserved HEAD",
            expected,
        )
    if commit_status == "COMPLETE" and status not in SUCCESS_RECEIPT_STATUSES:
        return _verdict(
            False,
            TERMINAL_GAP,
            f"post receipt terminal_status {status!r} disagrees with COMPLETE",
            expected,
        )
    return None


def _git_head(repo_path: Path) -> str | None:
    import subprocess

    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    sha = (proc.stdout or "").strip()
    return sha or None


def evaluate_pre_materialize(
    repo_path: str | Path,
    context: RepairEvidenceContext | None,
    *,
    baseline: Any | None = None,
    authorized_prefixes: tuple[str, ...] = (),
) -> EvidenceVerdict:
    """Авторизация по наблюдённому diff внутри scope и идентичности control plane.

    Receipt может подтверждать hop, но сам по себе, как и проза агента или
    просто грязное дерево без baseline, успех не доказывает. Post-receipt
    этот шаг не проходит. Криптографическая подпись не требуется.
    """

    if context is None:
        return _verdict(False, TERMINAL_GAP, "repair evidence identity is unresolved")
    try:
        context.require_complete()
    except ValueError as exc:
        return _verdict(False, TERMINAL_GAP, str(exc))

    repo = Path(repo_path)
    supporting = _supporting_receipt(repo, context)
    if supporting is not None:
        return supporting
    if baseline is None:
        return _verdict(False, TERMINAL_GAP, "repair baseline was not observed")
    from write_ownership import changed_paths_in_scope

    try:
        changed = changed_paths_in_scope(repo, baseline, authorized_prefixes)
    except OSError as exc:
        return _verdict(False, TERMINAL_GAP, f"worktree inspect failed: {exc}")
    if not changed:
        return _verdict(
            False,
            TERMINAL_GAP,
            "no observed in-scope repair change; receipt and prose are not sole proof",
        )
    head = _git_head(repo)
    if head != context.head_sha:
        return _verdict(False, TERMINAL_IDENTITY, "input revision does not match HEAD")
    return _verdict(True, None, "observed in-scope repair: " + ",".join(changed))


def _committed_paths(repo: Path, head_before: str, head_after: str) -> list[str] | None:
    import subprocess

    proc = subprocess.run(
        ["git", "diff", "--name-only", head_before, head_after],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return [line.strip().replace("\\", "/") for line in (proc.stdout or "").splitlines() if line.strip()]


def evaluate_post_materialize(
    repo_path: str | Path,
    context: RepairEvidenceContext | None,
    *,
    head_before: str,
    head_after: str,
    commit_status: str,
    tree_clean: bool,
    authorized_prefixes: tuple[str, ...] = (),
) -> EvidenceVerdict:
    """Сверить commit-all с наблюдаемым Git. Receipt не является доказательством.

    COMPLETE_NOOP сохраняет входной HEAD и не считается успешным ремонтом.
    PRE-файл не переписывается.
    """

    if context is None:
        return _verdict(False, TERMINAL_GAP, "post-materialize identity is unresolved")
    if not head_before or context.head_sha != head_before:
        return _verdict(False, TERMINAL_IDENTITY, "input revision was not the commit parent")
    repo = Path(repo_path)
    conflict = _post_receipt_conflict(
        repo,
        context,
        head_after=head_after,
        commit_status=commit_status,
    )
    if conflict is not None:
        return conflict
    observed = _git_head(repo)
    if observed != head_after:
        return _verdict(False, TERMINAL_IDENTITY, "observed HEAD does not match commit result")
    if commit_status == "COMPLETE_NOOP":
        if head_after != head_before or not tree_clean:
            return _verdict(False, TERMINAL_IDENTITY, "COMPLETE_NOOP did not preserve the input revision")
        return _verdict(True, None, "COMPLETE_NOOP preserves HEAD and is not a repair transition")
    if commit_status != "COMPLETE" or not tree_clean or not head_after or head_after == head_before:
        return _verdict(False, TERMINAL_GAP, "commit-all did not produce a clean new HEAD")
    import subprocess

    parent = subprocess.run(
        ["git", "rev-parse", f"{head_after}^"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if parent.returncode != 0 or (parent.stdout or "").strip() != head_before:
        return _verdict(False, TERMINAL_IDENTITY, "new HEAD is not a child of the input revision")
    committed = _committed_paths(repo, head_before, head_after)
    if committed is None:
        return _verdict(False, TERMINAL_GAP, "committed tree could not be listed")
    if not committed:
        return _verdict(False, TERMINAL_GAP, "COMPLETE commit has an empty tree diff")
    if authorized_prefixes:
        from write_ownership import _in_scope, is_runtime_artifact, path_escapes_write_scope

        outside = [
            path
            for path in committed
            if path_escapes_write_scope(repo, path, authorized_prefixes)
            or (not _in_scope(path, authorized_prefixes) and not is_runtime_artifact(path))
        ]
        if outside:
            return _verdict(
                False,
                TERMINAL_IDENTITY,
                "commit includes paths outside authorized write scope: " + ",".join(outside),
            )
    return _verdict(True, None, "observed git transition matches this execution")
