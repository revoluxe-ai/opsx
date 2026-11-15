"""Детерминированная техническая приёмка PNG."""

from __future__ import annotations

from opsx.project_context import resource_path

from pathlib import Path

from agents.agent_tools.image.revision import output_file_revision
from agents.agent_tools.image.schemas import ImageDesignTask, TechnicalGateVerdict


def run_technical_gate(
    *,
    repo_root: Path,
    task: ImageDesignTask,
) -> TechnicalGateVerdict:
    """IHDR, путь, формат."""
    errors: list[str] = []
    rel = task.output_path.replace("\\", "/")
    out = (resource_path(repo_root, rel)).resolve()
    root = repo_root.resolve()
    if not str(out).startswith(str(root)):
        errors.append("output_path outside repository")
    if not out.is_file():
        errors.append("output file missing")
        return TechnicalGateVerdict(ok=False, output_revision="", errors=errors)
    try:
        from agents.agent_tools.image.png_codec import read_png_size
    except ImportError:
        errors.append("read_png_size unavailable")
        return TechnicalGateVerdict(
            ok=False,
            output_revision=output_file_revision(out),
            errors=errors,
        )
    w, h = read_png_size(out)
    exp_w, exp_h = task.acceptance.ihdr[0], task.acceptance.ihdr[1]
    if (w, h) != (exp_w, exp_h):
        errors.append(f"IHDR mismatch: {w}x{h} expected {exp_w}x{exp_h}")
    rev = output_file_revision(out)
    return TechnicalGateVerdict(ok=not errors, output_revision=rev, errors=errors)
