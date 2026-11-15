# PROJECT ENGINEERING ADOPTION V1

Contract id: `OPSX-PROJECT-ENGINEERING-ADOPTION-V1`

## Purpose

Before ordinary OPSX mutation-capable commands run, the target project MUST
complete `/opsx-adopt` and hold a verified Adoption Contract:

- `opsx/engineering-adoption.yaml` (lock)
- `opsx/engineering-adoption/adoption-contract.json`
- `opsx/engineering-adoption/verification-receipt.json`

Forged `ADOPTED` markers without hash binding MUST fail closed.

## State machine

`DISCOVER → ASSESS → POLICY_PROPOSED → OWNER_POLICY_GATE → CONFIGURE → VERIFY → ADOPTED`

Quarantine path: `ADOPTED_WITH_QUARANTINE` with `opsx/engineering-adoption/quarantined-tests.json`.

## Quality ratchet (Python)

- Pyright **standard** ratchet (`pyright --outputjson` vs baseline); pytest active suite.
- Ruff optional; **mypy not part of adoption VERIFY**.
- Legacy diagnostics identity-bound in `pyright-baseline.json`.
- **Not adoption:** repo-wide `pyright-strict`, `mypy`, strict-on-touch — those belong to
  `/opsx-static-check-coverage` (changed in-policy production Python ⊆ strict include)
  then `/opsx-static-check` on the implementation contour.
- No silent relax of baseline ratchet.

## Gate

`python -m tools.openspec.opsx_engineering_adoption_gate --check-mutation`

Bootstrap allowlist documented in `opsx-execution-preflight.md`.
