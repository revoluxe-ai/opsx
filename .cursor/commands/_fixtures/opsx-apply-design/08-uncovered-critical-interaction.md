# Fixture 08 — uncovered critical interaction (review repairs)

## Theme

Slices may be locally complete (scaffold_contract, tasks owned, edges listed)
while still omitting critical **composed** behavior. That is not a DESIGN
schema pass alone — `/opsx-apply-design-review` must challenge interaction
treatment and may emit `REPAIRS_REQUIRED`.

## Given

A READY-looking APPLY DESIGN where:

- each slice has coherent responsibility + `scaffold_contract`
- `slice_order.edges` names dependencies (e.g. coordinator → resume)
- `cross_slice_contracts` exist and cover edges **structurally**
- but critical interaction treatment is missing or thin, for example:
  - no treatment of in-flight join when bootstrap/resume overlaps
  - control/race results left as fail-closed API failures
  - `required_composition_behavior` is bare "depends on SLICE-001"
  - applicable dogfood failure class marked `not_applicable` while race +
    lifecycle text clearly describes that class
  - cross_slice / change_level invariant without usable `proof_owner`

Planning and repository ownership are otherwise fine (not
`PLANNING_REALITY_CONFLICT`).

## Expected

```text
DESIGN validator: may PASS schema if contracts are structurally filled
DESIGN_REVIEW STATUS: REPAIRS_REQUIRED
NEXT_STAGE: none until DESIGN is repaired and review re-run
NEXT_ACTION: repair APPLY DESIGN composition, then /opsx-apply-design-review
```

Do **not** proceed to `/opsx-apply-scaffold` while review is
`REPAIRS_REQUIRED`.

Contrast with fixture 01: CSC-003 explicitly freezes in-flight join +
control-result interpretation with `B_RED` / `B_GREEN_ADJACENT` proof
placement.

Contrast with fixture 07: missing contracts fail DESIGN validate; this
fixture is about **semantic** composition gaps caught by review.
