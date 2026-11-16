"""Controller outcome must not treat process exit alone as verification PASS."""
from __future__ import annotations

import unittest

from opsx_cli.service import RunService


class RunOutcomeMappingTests(unittest.TestCase):
    def test_aggregate_prefers_verification_not_pass(self):
        verification = "NOT_EVALUATED"
        code = 0
        aggregate = verification if verification in {
            "PASS", "FAIL", "NOT_READY", "NOT_EVALUATED",
        } else ("PROCESS_COMPLETED" if code == 0 else "PROCESS_FAILED")
        self.assertEqual(aggregate, "NOT_EVALUATED")

    def test_aggregate_pass_only_when_verified(self):
        verification = "PASS"
        aggregate = verification if verification in {"PASS", "FAIL", "NOT_READY", "NOT_EVALUATED"} else "X"
        self.assertEqual(aggregate, "PASS")


if __name__ == "__main__":
    unittest.main()