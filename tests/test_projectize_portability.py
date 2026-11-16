"""Regression guards for portable OPSX projectization routing."""
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / ".cursor/commands/opsx-projectize.md"


class ProjectizePortabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = SOURCE.read_text(encoding="utf-8")

    def test_no_donor_product_or_greenfield_assumptions(self):
        for forbidden in ("stroy-system", "Telegram + Avito", "AmoCRM",
                          "This repository is greenfield", "OPSX-STROY-PROJECTIZE-01"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.body)

    def test_owner_contract_is_not_a_projectization_trigger(self):
        for required in ("owner-contract.md", "/opsx-new", "/opsx-continue",
                         "/opsx-projectize:GO", "NOT_REQUIRED",
                         "TARGET_CAPABILITY_UNRESOLVED", "machine-checkable"):
            with self.subTest(required=required):
                self.assertIn(required, self.body)
        self.assertIn("does not create a product change", self.body)
        self.assertIn("No writes in READ_ONLY", self.body)


if __name__ == "__main__":
    unittest.main()
