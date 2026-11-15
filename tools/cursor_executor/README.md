# cursor_executor (OPSX adapter V0.1)

Minimal generic boundary: OPSX caller → exact Cursor model route → async Python
SDK execution → terminal evidence and reconciliation.

- **Scope:** LOCAL async SDK on Windows; catalog preflight; route attestation;
  ledger keyed by `execution_id`; `reconcile()` for idempotent terminal reads.
- **Out of scope:** Temporal, OPSX lifecycle, model-tier policy, production features.
- **Secrets:** Never persisted in ledger or evidence; auth from process env or
  Windows USER registry only.

Run tests from repo root:

```bash
C:/Temp/cursor-sdk-312/Scripts/python.exe -m unittest discover -s tools/cursor_executor/tests -p "test_*.py" -v
```
