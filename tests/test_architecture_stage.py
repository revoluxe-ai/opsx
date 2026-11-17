"""Mandatory OPSX Architecture-stage gate: revision and upstream freshness."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.openspec.architecture_stage_gate import inspect, record_review, sha256

SECTIONS = [
    "Context and constraints", "Boundaries and ownership",
    "Decisions and rationale", "Data flow and interfaces",
    "Invariants and failure modes", "Compatibility and migration",
    "Technical design handoff",
]
DETAIL = "The existing component owns canonical state and keeps writes atomic. "


class MandatoryArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.change = self.root / "openspec/changes/my-change"
        self.change.mkdir(parents=True)
        (self.change / "owner-contract.md").write_text("Owner acceptance: no new product semantics.\n", encoding="utf-8")
        (self.change / "proposal.md").write_text("Change reason and scope.\n", encoding="utf-8")
        (self.change / "specs/cap").mkdir(parents=True)
        (self.change / "specs/cap/spec.md").write_text("Requirement: preserved behavior.\n", encoding="utf-8")
        self._architecture()

    def _architecture(self, status="READY"):
        body = "# Architecture\nARCHITECTURE_STATUS: " + status + "\n\n"
        body += "\n".join("## " + heading + "\n" + DETAIL + "\n" for heading in SECTIONS)
        (self.change / "architecture.md").write_text(body, encoding="utf-8")

    def _review(self):
        digest = sha256((self.change / "architecture.md").read_bytes())
        return record_review(self.root, "my-change", author_id="architect-A",
                             reviewer_id="reviewer-B", reviewed_sha256=digest,
                             review_execution_id="review-exec-001")

    def test_unreviewed_or_missing_architecture_blocks(self):
        self.assertIn("ARCHITECTURE_REVIEW_MISSING", inspect(self.root, "my-change")["findings"])
        (self.change / "architecture.md").unlink()
        self.assertIn("ARCHITECTURE_MISSING", inspect(self.root, "my-change")["findings"])

    def test_current_architecture_review_allows_design(self):
        self.assertEqual(self._review()["status"], "READY")
        self.assertEqual(inspect(self.root, "my-change")["status"], "READY")

    def test_unreviewed_or_stale_architecture_blocks_design_and_apply(self):
        self.assertEqual(self._review()["status"], "READY")
        (self.change / "architecture.md").write_text(
            (self.change / "architecture.md").read_text(encoding="utf-8") + "\nchanged architecture\n",
            encoding="utf-8"
        )
        result = inspect(self.root, "my-change")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("REVIEW_IDENTITY_OR_FRESHNESS_MISMATCH:architecture_sha256", result["findings"])

    def test_upstream_modification_invalidates_review(self):
        self.assertEqual(self._review()["status"], "READY")
        (self.change / "owner-contract.md").write_text("Owner changes semantics.\n", encoding="utf-8")
        self.assertIn("REVIEW_IDENTITY_OR_FRESHNESS_MISMATCH:upstream_sha256",
                      inspect(self.root, "my-change")["findings"])

    def test_missing_upstream_and_self_review_fail(self):
        self.assertEqual(record_review(self.root, "my-change",
            author_id="same", reviewer_id="same",
            reviewed_sha256=sha256((self.change / "architecture.md").read_bytes()),
            review_execution_id="review-exec-002")["status"], "BLOCKED")
        (self.change / "proposal.md").unlink()
        self.assertIn("UPSTREAM_MISSING:proposal.md", inspect(self.root, "my-change")["findings"])

    def test_empty_section_and_placeholder_block(self):
        self._architecture()
        s = (self.change / "architecture.md").read_text(encoding="utf-8")
        (self.change / "architecture.md").write_text(s.replace(
            "## Technical design handoff\n" + DETAIL,
            "## Technical design handoff\nTBD"
        ), encoding="utf-8")
        findings = inspect(self.root, "my-change")["findings"]
        self.assertIn("ARCHITECTURE_UNRESOLVED_PLACEHOLDER", findings)
        self.assertIn("ARCHITECTURE_SECTION_MISSING_OR_EMPTY:Technical design handoff", findings)

    def test_no_skip_even_when_design_already_exists(self):
        self.assertEqual(inspect(self.root, "my-change", only_if_downstream=True)["status"], "NOT_YET_DUE")
        (self.change / "design.md").write_text("DESIGN_STATUS: READY\n", encoding="utf-8")
        self.assertEqual(inspect(self.root, "my-change", only_if_downstream=True)["status"], "BLOCKED")

    def test_change_identity_and_review_identity_fail_closed(self):
        self.assertEqual(self._review()["status"], "READY")
        rpath = self.change / "architecture-evidence/ARCHITECTURE-REVIEW.json"
        d = json.loads(rpath.read_text(encoding="utf-8"))
        d["change_id"] = "another-change"
        rpath.write_text(json.dumps(d), encoding="utf-8")
        self.assertIn("REVIEW_IDENTITY_OR_FRESHNESS_MISMATCH:change_id",
                      inspect(self.root, "my-change")["findings"])
        with self.assertRaisesRegex(ValueError, "CHANGE_ID_INVALID"):
            inspect(self.root, "../other")

    def test_missing_review_execution_identity_blocks(self):
        self.assertEqual(self._review()["status"], "READY")
        path = self.change / "architecture-evidence/ARCHITECTURE-REVIEW.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        del doc["review_execution_id"]
        path.write_text(json.dumps(doc), encoding="utf-8")
        self.assertIn("REVIEW_EXECUTION_ID_MISSING",
                      inspect(self.root, "my-change")["findings"])

    def test_spec_revision_invalidates_review(self):
        self.assertEqual(self._review()["status"], "READY")
        path = self.change / "specs/cap/spec.md"
        path.write_text("Changed semantic requirement.\n", encoding="utf-8")
        self.assertIn("REVIEW_IDENTITY_OR_FRESHNESS_MISMATCH:upstream_sha256",
                      inspect(self.root, "my-change")["findings"])

    def test_direct_s2_s3_gates_cannot_bypass_missing_architecture(self):
        import importlib.util
        path = ROOT / '.cursor/pipeline/architecture-evidence/planning_gate.py'
        spec = importlib.util.spec_from_file_location('mandatory_stage_integration_gate', path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        (self.change / 'design.md').write_text('DESIGN_STATUS: READY\n', encoding='utf-8')
        (self.change / 'tasks.md').write_text('TASKS_STATUS: READY\n', encoding='utf-8')
        design = module.run_design_ready_gate('my-change', self.root)
        apply = module.run_apply_preflight_gate('my-change', self.root)
        self.assertIn('ARCHITECTURE_STAGE_BLOCKED', {x.code for x in design.findings})
        self.assertIn('ARCHITECTURE_STAGE_BLOCKED', {x.code for x in apply.findings})
        accepted_digest = sha256((self.change / 'architecture.md').read_bytes())
        (self.change / 'design.md').write_text(
            'DESIGN_STATUS: READY\nARCHITECTURE_SHA256: ' + accepted_digest + '\n', encoding='utf-8')
        self.assertEqual(self._review()['status'], 'READY')
        design_after = module.run_design_ready_gate('my-change', self.root)
        self.assertNotIn('ARCHITECTURE_STAGE_BLOCKED', {x.code for x in design_after.findings})

    def test_design_must_bind_current_architecture_revision(self):
        self.assertEqual(self._review()['status'], 'READY')
        design_path = self.change / 'design.md'
        original_digest = sha256((self.change / 'architecture.md').read_bytes())
        design_path.write_text('DESIGN_STATUS: READY\nARCHITECTURE_SHA256: ' + original_digest + '\n', encoding='utf-8')
        self.assertEqual(inspect(self.root, 'my-change')['status'], 'READY')
        self._architecture()
        path = self.change / 'architecture.md'
        path.write_text(path.read_text(encoding='utf-8') + '\nApproved architecture revision.\n', encoding='utf-8')
        self.assertEqual(self._review()['status'], 'READY')
        self.assertIn('DESIGN_ARCHITECTURE_STALE', inspect(self.root, 'my-change')['findings'])

    def test_consumer_workflow_wiring(self):
        files = [
            ".cursor/commands/opsx-new.md",
            ".cursor/commands/opsx-continue.md",
            ".cursor/commands/opsx-ff.md",
            ".cursor/commands/opsx-propose.md",
            ".cursor/commands/opsx-update.md",
            ".cursor/commands/opsx-apply.md",
            ".cursor/commands/opsx-verify.md",
            ".cursor/commands/opsx-archive.md",
            ".cursor/commands/opsx-planning-review.md",
            ".cursor/pipeline/contracts/DESIGN_GENERATION.md",
            ".cursor/pipeline/contracts/PLANNING_REVIEW.md",
        ]
        for rel in files:
            with self.subTest(file=rel):
                self.assertIn("architecture", (ROOT / rel).read_text(encoding="utf-8").lower())
        for rel in ("tools/openspec/openspec_guard.py",
                    ".cursor/pipeline/architecture-evidence/planning_gate.py"):
            self.assertIn("ARCHITECTURE_STAGE_BLOCKED",
                          (ROOT / rel).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
