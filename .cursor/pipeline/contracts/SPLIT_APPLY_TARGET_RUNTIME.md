# Split APPLY Target Runtime Contract

STATUS: TARGET_INTEGRATION_CONTRACT

## Purpose

Preserve the proven split-APPLY state/evidence semantics from the pinned SOURCE
while realizing execution against the adv-bot Python/Poetry project.

## Authority and write boundaries

The public stages are:

`/opsx-apply-design -> /opsx-apply-design-review -> /opsx-apply-scaffold ->
/opsx-apply-red -> /opsx-apply-implement -> /opsx-apply-green`.

- DESIGN / DESIGN_REVIEW write process receipts only.
- SCAFFOLD may create structural product seams but MUST NOT implement frozen
  business behavior.
- RED may write tests/fixtures and RED evidence; it MUST NOT modify production.
- IMPLEMENT may modify only the current slice's authorized product surfaces;
  it MUST NOT execute or modify frozen tests.
- GREEN may run the exact frozen tests, repair implementation-owned production
  failures, and write GREEN evidence.
- A worker self-report is never sufficient for RED_PROVEN or GREEN_PROVEN.

## Target realization

- Production surface: `app/**`
- Test surface: `tests/**`
- Process tools: `opsx_tools/opsx-apply-*.mjs`
- Schemas: `opsx/registry/apply_*.schema.json`
- Receipts: `openspec/changes/<change>/apply-*/**`
- Canonical test policy: `.cursor/rules/testing-rules.mdc`
- Runtime adapter: `opsx/adapters/python-poetry/README.md`

Bounded test execution accepts exact per-test commands only:

- `poetry run pytest <test-file-or-node-id> ...`
- legacy Node process fixtures may use exact `node --test <file>`

Whole-suite commands such as `poetry run pytest tests`, `npm test`, bugbot,
browser-check, or verify are not GREEN slice evidence.

## Identity and evidence

RED_PROVEN requires `per_command_v1`: every frozen `test_commands[]` entry
has its own qualified `command_executions[]` evidence.
GREEN_PROVEN is slice-local and freshness-bound to current DESIGN, SCAFFOLD,
RED, IMPLEMENT and frozen test content identities.

Retry convergence is progress-based: repeated identical failure signature with
unchanged production digest, sibling expansion, or regression stops with
FAIL/BLOCKED rather than exhausting retries into PASS.

## Compatibility with /opsx-apply

`/opsx-apply` remains independently operational and MUST NOT require
`APPLY_DESIGN_READY`, `RED_PROVEN`, `IMPLEMENTATION_WRITTEN`, or
`GREEN_PROVEN` as entry prerequisites.

**Interchangeability:** `/opsx-apply` and the explicit split stage commands
share one completion spine through `APPLY_COMPLETE`. Neither path may skip
authorized stages or claim completion from stale receipts alone.

When split receipts exist, `/opsx-apply` is **split-aware**: **first**
`selectNextGreenableSlice` recovery (`next_command`, operative re-RED when
authorized, **post-implement test-contract recovery** when re-RED is blocked
but frozen tests pass), **then** `opsx-super-implement.mjs plan` fallback for
the mapped sibling stage. That is not checkbox theater — it runs the same
primitives as `/super-implement` would dispatch when recovery does not override.

Post-implement recovery chat entry:
`/opsx-post-implement-test-contract-recovery` (CLI:
`opsx_tools/opsx-post-implement-test-contract-recovery-cli.mjs`).

When `FROZEN_TEST_MUTATED` but all frozen `test_commands[]` exit 0 on disk,
selectors MUST route to post-implement recovery (not operative re-RED), including
when RED remains `RED_PROVEN`.

Split APPLY MUST NOT fabricate legacy task-checkbox completion. Verification
may project split receipts into these explicit gap classes:

- PRODUCT_IMPLEMENTATION_GAP
- PROCESS_COMPLETION_PROJECTION_GAP
- TEST_COVERAGE_ARTIFACT_GAP
- STALE_EVIDENCE
- SPLIT_APPLY_CURRENT_GREEN

SOURCE feature run directories, run ids, HEAD values, and CASE_RUNTIME_ONLY
fixtures are not target authority and are never required for this contract.
