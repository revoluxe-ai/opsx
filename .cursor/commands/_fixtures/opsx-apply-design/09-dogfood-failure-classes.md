# Fixture 09 — dogfood composition failure classes

## Theme

Five generic interaction failure classes from dogfood (novel-save refresh
composition bugs) are **contract patterns**, not product-specific novel-save
logic. APPLY DESIGN / DESIGN REVIEW treat them as reusable composition
checklist items when race or lifecycle applicability is true.

## Failure classes (contract patterns)

| ID | Pattern (generic) |
|----|-------------------|
| `IN_FLIGHT_JOIN_BY_BOOTSTRAP` | Downstream bootstrap joins an in-flight upstream operation and must still complete startup |
| `CONTROL_RESULT_AS_FAILURE` | Upstream control/race results (in-flight, stale generation) must not be interpreted as API/fail-closed failures downstream |
| `OVERLAPPING_WRITE_INVALIDATES_BOOLEAN_IN_FLIGHT` | Overlapping writes invalidate naive boolean in-flight gating; composition must define generation/token semantics |
| `DEBOUNCED_WORK_AFTER_LIFECYCLE_CHANGE` | Debounced or deferred work must not execute after route/lifecycle state has changed |
| `SEMANTIC_EQ_VS_BYTE_EQ` | Semantic state equality must not be confused with byte/serialization equality at reload or payload boundaries |

## Expected DESIGN usage

- `composition_completeness.failure_class_applicability` classifies **all five**
  as `applicable` | `not_applicable` when race and/or lifecycle are applicable.
- When a class is `applicable`, at least one `cross_slice_contract` should
  name it under `failure_classes` and freeze consumed semantics / composition
  behavior for that pattern.
- Fixture 01 marks `IN_FLIGHT_JOIN_BY_BOOTSTRAP` and `CONTROL_RESULT_AS_FAILURE`
  applicable; the other three `not_applicable` for that hydrate-resume graph.

## Forbidden

- Hard-coding novel-save module names as the only way to express these classes
- Skipping classification when `race_applicability.applicable` or
  `lifecycle_applicability.applicable` is true
- Claiming READY while edges exist and a dogfood class remains unclassified

Source of truth for ids/summaries: `opsx_tools/opsx-apply-composition.mjs`
(`COMPOSITION_FAILURE_CLASSES`).
