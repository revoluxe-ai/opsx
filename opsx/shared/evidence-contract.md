---
component_id: opsx.shared.evidence-contract
component_version: 0.3.0
ownership: EMBEDDED_SHARED
runtime_class: ACTIVE
---

# Evidence contract

Require as applicable: branch/HEAD; scope; changed files; commands+exit codes; gaps; verify verdict; archive evidence; unexecuted checks.
Do not invent substitute commands for undeclared quality slots.

## Committed identity binding

For committed transfer/activation evidence, content identity is:
`sha256(canonical Git blob bytes at the tested revision)`.

Working-tree bytes are not a valid committed identity source because checkout
filters and platform line endings (for example LF vs CRLF) may change physical
bytes without changing the committed object.

A committed evidence consumer MUST bind both:
- exact tested revision;
- canonical Git blob hash for each claimed tracked artifact.

Dirty/uncommitted execution evidence is a separate class and must explicitly
declare a working-tree identity mode; it cannot satisfy a committed-revision gate.
