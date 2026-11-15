# 08 — Current GREEN via on-disk IMPLEMENTATION.json hash

Theme: `selectNextScaffoldableSlice` must pass current file sha256 of
`IMPLEMENTATION.json` (and DESIGN/SCAFFOLD/RED) into
`classifyGreenEvidenceFreshness`. IMPLEMENTATION.json has no top-level
`artifact_sha256`.

## Expected

| Check | Result |
|-------|--------|
| Matching current IMPLEMENTATION file sha | GREEN current; SLICE-002 scaffoldable |
| Changed IMPLEMENTATION file sha | `STALE_GREEN`; `DEPENDENCY_NOT_GREEN` |
| Nested identity without current sha | `missing_current_implementation_artifact_sha256` |
| Unrelated worktree dirt | ignored |
