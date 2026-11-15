# Fixture — mixed Bugbot batch splits at S0 intake (product vs process)

## Context

```text
INVOCATION: /opsx-orch-bug-fix change=cross-device-wardrobe-refresh
IMMEDIATE_PRIOR_MESSAGE: Bugbot table (mixed product + process rows)
```

## Immediate prior (mixed)

```text
| Severity | Location | Finding |
| High | src/utils/cloudProgressHydrateCoordinator.js:16 | @/eventBus import breaks node --test direct imports |
| High | .cursor/commands/opsx-orch-bug-fix.md:252 | S0 admits process findings into product PP |
```

## Expected Orch behavior

```text
CURRENT_STAGE: S0_INTAKE_CLEAR
INTAKE_PASS_0_25: REPAIR_AUTHORITY_CLASSIFY applied
PROCESS_FINDINGS_ROUTED_COUNT: 1
CLEAN_FINDINGS_COUNT: 1

PROCESS_HANDOFF:
  COMMAND: /opsx-meta-fix-command-defects
  ROWS: .cursor/commands/opsx-orch-bug-fix.md:252

PRODUCT_BATCH:
  ADMITTED: src/utils/cloudProgressHydrateCoordinator.js:16
  CURRENT_STAGE after handoff: S1a_FAMILY_PLAN_READ_ONLY

ORCH_BUG_FIX_ON_PROCESS_ROW: FORBIDDEN
META_FIX_ON_PROCESS_ROW: REQUIRED
BUG_TEAM_ON_PROCESS_ROW: FORBIDDEN
```

## Forbidden

```text
family-plan / disposition / bug-team for .cursor/commands/** finding
dropping process row without meta-fix handoff
treating mixed batch as product-only CLEAN_FINDINGS=2
```
