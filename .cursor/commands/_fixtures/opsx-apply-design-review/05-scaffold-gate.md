# Fixture 05 — SCAFFOLD gate

## Theme

Split pipeline SCAFFOLD requires a current `APPLY_DESIGN_REVIEW_PASS`.

## Gate outcomes

| Review state | SCAFFOLD / selectNextScaffoldableSlice |
|--------------|----------------------------------------|
| missing | fail `DESIGN_REVIEW_MISSING` / `DESIGN_REVIEW_MISSING_OR_STALE` |
| `REPAIRS_REQUIRED` | fail `DESIGN_REVIEW_REPAIRS_REQUIRED` |
| PASS but wrong `artifact_sha256` | fail `DESIGN_REVIEW_STALE` |
| `APPLY_DESIGN_REVIEW_PASS` + identities match | unlock (eligible slice selection) |

Helpers:

- `evaluateApplyDesignReviewGate`
- `selectNextScaffoldableSlice({ design, designReview, … })`
- `selectNextPipelineStage` → `APPLY_DESIGN_REVIEW` when review not PASS

PASS unlocks `/opsx-apply-scaffold`; REPAIRS / missing / stale must not.
