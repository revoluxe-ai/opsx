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
            execution_route="/opsx-apply-design",
        )
        self.assertEqual(fix.execution_route, "/opsx-apply-design")

    def test_apply_design_review_route(self) -> None:
        fix = FixEvidence(
            status="FIXED",
            summary="ok",
            files_changed=["openspec/changes/x/apply-design-review/APPLY-DESIGN-REVIEW.json"],
            execution_route="/opsx-apply-design-review",
        )
        self.assertEqual(fix.execution_route, "/opsx-apply-design-review")


if __name__ == "__main__":
    unittest.main()
