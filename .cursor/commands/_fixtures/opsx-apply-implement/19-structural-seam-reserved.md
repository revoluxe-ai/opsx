# 19 — Reserved scaffold seam may stay structural; fill is rejected

Theme: semantic scope vs scaffold seams.

Allowed (SLICE-001):

```text
registerNovelSaveHydrateHost(adapter) { hostAdapter = adapter || null }
```

Structural callable / storage seam already created by scaffold.

Not allowed (SLICE-001):

```text
hostAdapter.getPutGate().putInFlight
putGeneration capture / stale discard
applyHydrateSuccess / applyHydrateFailure dispatch
```

Those fill later-slice semantics → `BLOCKED_SCOPE_EXPANSION` via
`classifySemanticSliceScope` / seam-fill patterns.
