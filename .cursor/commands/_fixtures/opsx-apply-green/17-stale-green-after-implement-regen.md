# 17 — STALE GREEN after IMPLEMENT regeneration

Theme: durable `GREEN_PROVEN` is not validity.

## Setup

- SLICE-001 has historical `GREEN_PROVEN` bound to IMPLEMENTATION identity A.
- Current IMPLEMENTATION receipt identity is B (`artifact_sha256` differs).
- SCAFFOLD / RED still READY/PROVEN; frozen tests unchanged.
- Old `GREEN.json` remains on disk (no manual delete).

## Expected

| Check | Result |
|-------|--------|
| `classifyGreenEvidenceFreshness` | `current: false`, `code: STALE_GREEN`, mismatched `implementation_identity` |
| `selectNextGreenableSlice` | eligible `SLICE-001` |
| `selectNextPipelineStage` | `APPLY_GREEN` for `SLICE-001` (not `APPLY_SCAFFOLD` for next) |
| Downstream dep unlock | stale GREEN does **not** satisfy `dependencies_on_prior_slices` |
| Task completion | stale GREEN does **not** complete shared task ids |
| `APPLY_COMPLETE` | stale GREEN does **not** count |
| Re-run GREEN | executes frozen RED again; fresh receipt records identity B |

## Anti-pattern

Treating `status == GREEN_PROVEN` alone as complete → `NO_ELIGIBLE_SLICE` /
wrong next stage `APPLY_SCAFFOLD` for a dependent slice.
