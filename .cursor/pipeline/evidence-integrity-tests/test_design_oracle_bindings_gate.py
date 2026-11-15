"""REG: DESIGN technical contracts — oracle bindings and R3 freeze path conflicts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _node(script: str) -> dict[str, object]:
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_knowledge_obligations_scoped_to_red_slice() -> None:
    """Future-slice test targets must not block RED for current slice."""
    payload = _node(
        """
        const design = {
          knowledge_obligations: [{ seam_id: "x" }],
          slices: [
            { slice_id: "SLICE-001", red_test_targets_or_seams: ["tests/unit/a.py"] },
            { slice_id: "SLICE-002", red_test_targets_or_seams: ["tests/unit/b.py"] },
          ],
        };
        const red = {
          status: "RED_PROVEN",
          slice_id: "SLICE-001",
          test_paths: ["tests/unit/a.py"],
          test_commands: [],
        };
        const { validateRedKnowledgeObligations } = await import(
          "./opsx_tools/opsx-bug-learning-apply.mjs"
        );
        console.log(JSON.stringify(validateRedKnowledgeObligations(red, design)));
        """
    )
    assert payload["ok"] is True


def test_design_slice_bindings_required() -> None:
    design = {
        "slices": [
            {
                "slice_id": "SLICE-099",
                "red_test_targets_or_seams": ["tests/unit/new_seam.py"],
                "red_oracle_bindings": [],
            },
        ],
    }
    payload = _node(
        f"""
        const design = {json.dumps(design)};
        const {{ validateDesignSliceTechnicalContracts }} = await import(
          './opsx_tools/opsx-apply-red-oracle-authority.mjs'
        );
        console.log(JSON.stringify(validateDesignSliceTechnicalContracts(design)));
        """
    )
    assert payload["ok"] is False
    errors = payload["errors"]
    assert isinstance(errors, list)
    assert any(e.get("code") == "RED_ORACLE_BINDINGS_INCOMPLETE" for e in errors)
