# Fixture 02 — REPAIRS uncovered critical interaction

## Theme

Dogfood pattern: slices look locally complete, dependency edges exist, but a
critical cross-slice interaction (in-flight join / control-result-as-failure)
is uncovered. Review MUST NOT self-repair DESIGN; emit `REPAIRS_REQUIRED`.

## Expected

```text
STATUS: REPAIRS_REQUIRED
NEXT_STAGE: APPLY_DESIGN
blockers: INCOMPLETE_COMPOSITION and/or COMPOSED_BEHAVIOR_GAP
composed_behavior_risk.answered: true
all_slices_green_composed_wrong_possible: true
repair_handoff.summary + required_design_repairs[] present
at least one blocking finding referencing question 8 or 12
```

Validate:

```bash
node opsx_tools/opsx-apply-design-review.mjs .cursor/commands/_fixtures/opsx-apply-design-review/02-repairs-uncovered-interaction.APPLY-DESIGN-REVIEW.json
```

Do **not** emit `/opsx-apply-scaffold` as NEXT_ACTION.

## Machine artifact

`02-repairs-uncovered-interaction.APPLY-DESIGN-REVIEW.json`
