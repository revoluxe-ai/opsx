# Fixture 03 — stale after DESIGN change

## Theme

A prior `APPLY_DESIGN_REVIEW_PASS` is bound to DESIGN via
`apply_design_identity.artifact_sha256` (and planning /
protected_context digests). Changing DESIGN bytes without re-review must
fail the SCAFFOLD gate.

## Gate

`evaluateApplyDesignReviewGate({ design, review, currentDesignArtifactSha256 })`

When `review.apply_design_identity.artifact_sha256 !== currentDesignArtifactSha256`:

```text
ok: false
code: DESIGN_REVIEW_STALE
```

Same for planning_digest or protected_context_digest mismatch.

## Expected executor behavior

- Do **not** treat the old PASS as current.
- Re-run `/opsx-apply-design-review` against the new DESIGN freeze.
- `/opsx-apply-scaffold` must not proceed on a stale review.
