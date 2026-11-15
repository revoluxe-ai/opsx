# Fixture 01 — APPLY_DESIGN_REVIEW_PASS

## Theme

Independent review of the READY design golden
`.cursor/commands/_fixtures/opsx-apply-design/01-ready-valid-change.APPLY-DESIGN.json`
passes: identities bind, Q1–Q12 pass, composed-behavior risk closed by
CSC-003 composition contracts.

## Bindings (from DESIGN golden)

| Field | Value |
|-------|-------|
| change_id | fixture-hydrate |
| planning_digest | from `design.planning_identity.planning_digest` |
| protected_context_digest | from `design.protected_context_identity.content_digest` |
| artifact_sha256 | sha256 of the DESIGN golden JSON bytes |
| machine_path | `openspec/changes/fixture-hydrate/apply-design/APPLY-DESIGN.json` |
| apply_design status | APPLY_DESIGN_READY |

## Expected

```text
STATUS: APPLY_DESIGN_REVIEW_PASS
NEXT_STAGE: APPLY_SCAFFOLD
BLOCKERS: none
chat_history_required: false
composed_behavior_risk.answered: true
all_slices_green_composed_wrong_possible: false
write_boundary: product/tests/apply_design/planning all false
```

Validate:

```bash
node opsx_tools/opsx-apply-design-review.mjs .cursor/commands/_fixtures/opsx-apply-design-review/01-pass.APPLY-DESIGN-REVIEW.json
```

## Machine artifact

`01-pass.APPLY-DESIGN-REVIEW.json`
