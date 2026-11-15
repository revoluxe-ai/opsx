"""Явный ``CANNOT_COMPLETE`` repair hop → ``VerifyStageStop``, не repair-loop BLOCKED."""

from __future__ import annotations

from workflows.stop_errors import VerifyStageStop

STOP_CODE = "REPAIR_HOP_CANNOT_COMPLETE"


def raise_if_repair_hop_cannot_complete(
    *,
    hop: str,
    finding_id: str,
    status: str,
    summary: str | None = None,
) -> None:
    """Поднять stage stop, если модель явно заявила, что hop не может выполнить задачу.

    Неудачный repair (FIXED/FAILED с реальным pytest) сюда не входит — только
    добровольный ``CANNOT_COMPLETE`` в JSON ответе модели.
    """
    if str(status or "").strip().upper() != "CANNOT_COMPLETE":
        return
    hop_label = str(hop or "").strip() or "(unknown)"
    fid = str(finding_id or "").strip() or "(unknown)"
    summary_text = str(summary or "").strip()
    evidence = (
        f"hop={hop_label} finding_id={fid} status=CANNOT_COMPLETE"
        + (f" summary={summary_text}" if summary_text else "")
    )
    raise VerifyStageStop(
        "repair hop reported CANNOT_COMPLETE",
        code=STOP_CODE,
        stage="dispatch_cycle",
        evidence=evidence,
        owner="repair",
        next_action=(
            "inspect hop prompt, tool surface (read/edit/run_pytest), and model blockers; "
            "do not start another silent repair round"
        ),
    )
