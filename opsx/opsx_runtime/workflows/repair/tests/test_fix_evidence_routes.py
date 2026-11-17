"""FixEvidence принимает маршруты split-apply hops."""

from __future__ import annotations

import unittest

from workflows.repair.schemas import FixEvidence


class FixEvidenceRouteTests(unittest.TestCase):
    def test_apply_design_route(self) -> None:
        fix = FixEvidence(
            status="FIXED",
            summary="ok",
            files_changed=["openspec/changes/x/a.json"],
            execution_route="APPLY_DESIGN",
        )
        self.assertEqual(fix.execution_route, "APPLY_DESIGN")

    def test_apply_design_review_route(self) -> None:
        fix = FixEvidence(
            status="FIXED",
            summary="ok",
            files_changed=["openspec/changes/x/apply-design-review/APPLY-DESIGN-REVIEW.json"],
            execution_route="APPLY_DESIGN_REVIEW",
        )
        self.assertEqual(fix.execution_route, "APPLY_DESIGN_REVIEW")


if __name__ == "__main__":
    unittest.main()
