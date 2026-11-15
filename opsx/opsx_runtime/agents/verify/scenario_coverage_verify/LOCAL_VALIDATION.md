# Local-only verification: scenario receipts / SDK salvage

This PR deliberately has **no GitHub Actions workflow**. Owner runs offline tests locally. No SDK/model request is needed for any command below.

Repository branch to test: `cursor/opsx-scenario-receipt-replay-fix-v1`.

## 1. Focused regression suite (Git Bash)

```bash
PYTHONPATH=opsx/opsx_runtime poetry run python -m pytest -q \
  opsx/opsx_runtime/workflows/verify/tests/test_scenario_row_recovery.py \
  opsx/opsx_runtime/workflows/verify/tests/test_scenario_row_checkpoints.py \
  opsx/opsx_runtime/workflows/verify/tests/test_scenario_saved_replay.py \
  opsx/opsx_runtime/agents/adapters/tests/test_scenario_row_status.py
```

Acceptance: exit code 0; no warnings silently converted to PASS. The suite covers single/multi GAP, 56-row serial checkpointing, corrupt/stale row proofs, immutable-source binding, verifier/model identity, exact stream-to-meta pairing, multiple findings, replay provenance, tamper/path/symlink resistance, no-side-effect dry-run, explicit partial recovery, and idempotence.

## 2. Real historical SDK answers (read-only; no SDK calls)

Requires the original Git commit `fb3f42089deaf2e04230333966e641232bc4a2ea` locally; shallow clones must fetch its history before trying this step.

```bash
PYTHONPATH=opsx/opsx_runtime poetry run python -m \
  agents.verify.scenario_coverage_verify.replay_saved \
  --repo . \
  --change invest-yalta-max-funnel \
  --index openspec/changes/invest-yalta-max-funnel/verify/scenario-recovery-8b46e104c657/INDEX.json
```

Expected bound inputs: 11 final SDK responses, 7 SDK-claimed SUPPORTED, 4 SDK-claimed GAP. Actual native-validated reusable PASS candidates can be fewer. All 8 original GAP findings remain in source candidate files. The output must report `DRY_RUN`, `sdk_calls=0`, `aggregate_verdict=NOT_ASSERTED`, and `written={observations: 0, supported_receipts: 0}`.

**Do not use `--write` until the report has been independently reviewed.** A model's SUPPORTED claim is not a PASS receipt. Existing output files from PR #300 must remain unchanged. Do not launch the 56-row audit just to test this repair.

## Merge gate

No merge based on code review text alone. Store local test output and dry-run JSON as machine-checkable artifacts in the execution contour, tied to the tested commit. Fail closed if tests fail, the original Git anchor is missing, preflight rejects a row, proofs are stale, or a purported PASS has no native receipt.
