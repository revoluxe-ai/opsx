# Fixture — terminal prior mission must not shadow immediate Bugbot findings

## Context

```text
PRIOR_MISSION: ORCH-bug-fix-20260826T220000Z
PRIOR_MISSION_STATUS: BUG_BATCH_CLEAR
IMMEDIATE_PRIOR_MESSAGE: Bugbot DIRECT_PARENT review (actionable rows present)
INVOCATION: /opsx-orch-bug-fix   (bare — no mission=/change=/findings=/GO)
```

## Immediate prior (supported)

```text
| Severity | Location | Finding |
| High | src/store/wardrobe.js:42 | Stale refresh after cross-device sync |
```

## Preconditions

```text
$ARGUMENTS: empty
R0 mission=: absent
R1 inline findings: absent
R2 immediate prior: actionable Bugbot table present
change resolution: SINGLE_ACTIVE → cross-device-wardrobe-refresh
```

## Expected Orch behavior

```text
SOURCE_PRECEDENCE: R2
TERMINAL_PREVIOUS_MISSION_MUST_NOT_SHADOW_FRESH_IMMEDIATE_FINDINGS: honored
NEW_MISSION_MINTED: YES
PRIOR_MISSION_RESUMED: NO
ADMITTED_FINDINGS: from immediate prior Bugbot table (1 row)
CURRENT_STAGE: S0_INTAKE_CLEAR
MISSION_STATUS: ACTIVE
INFORMATIONAL_ONLY_BUG_BATCH_CLEAR_REPORT: FORBIDDEN as sole outcome
```

## Forbidden

```text
Resume ORCH-bug-fix-20260826T220000Z as if no new batch
STATUS: BUG_BATCH_CLEAR with "no change=/findings=/GO/mission= → no new batch"
Discard immediate prior findings
Fuzzy-search older chat for findings
Route .cursor/commands/** defects through this command
```
