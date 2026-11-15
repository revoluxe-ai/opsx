# APPLY SCAFFOLD fixtures

| Id | File | Intent | Terminal |
|---|---|---|---|
| 01 | `01-select-first.md` | deterministic first slice | documented |
| 02 | `02-write-boundary.md` | no tests, shape-only | documented |
| 03 | `03-ready-existing-noop.SCAFFOLD.json` | EXISTING seam, no-op | SCAFFOLD_READY |
| 04 | `04-ready-introduce.SCAFFOLD.json` | INTRODUCE module + export | SCAFFOLD_READY |
| 05 | `05-behavior-forbidden.md` | no business behavior | documented |
| 06 | `06-apply-compat.md` | legacy apply independent | documented |
| 07 | `07-dependency-not-green.SCAFFOLD.json` | SLICE-003 waits | BLOCKED |
| 08 | `08-green-current-via-disk-hash.md` | pass on-disk IMPLEMENTATION sha | SLICE-002 eligible |

Schema: `opsx_tools/registry/apply_scaffold.schema.json`.
