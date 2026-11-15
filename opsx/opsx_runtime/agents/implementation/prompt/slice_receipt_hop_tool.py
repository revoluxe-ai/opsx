"""Текст промпта: runtime записывает slice receipt без участия модели."""

from __future__ import annotations


def slice_receipt_hop_tool_block() -> str:
    """Блок authority: квитанция пишется детерминированно после hop; руками JSON не редактировать."""
    return """\
## Slice receipt (runtime-owned)

Do **not** edit on-disk receipt JSON for this slice. Do **not** guess digests, identities,
or machine statuses.

After a successful hop the runtime materializes the stage receipt from the design,
the worktree diff, and live command results. If the hop ends ``BLOCKED``, the runtime
writes a blocked receipt from the hop outcome.

Focus on product and test work only. Hop answer JSON stays separate
(``APPLY_COMPLETE``, ``BLOCKED``, ``NO_CHANGE``, …).
"""
