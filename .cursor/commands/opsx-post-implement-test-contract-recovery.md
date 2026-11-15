---
name: /opsx-post-implement-test-contract-recovery
id: opsx-post-implement-test-contract-recovery
category: Workflow
description: Advance post-IMPLEMENT test-contract recovery when operative re-RED is blocked but frozen tests pass
argument-hint: "change=<change-id> [mode=advance|replay|status] [slice=<id>]"
---

# /opsx-post-implement-test-contract-recovery

Post-IMPLEMENT **test-contract recovery** spine: rebaseline frozen-test identity,
refresh evidence, and unblock GREEN when `selectNextGreenableSlice` reports
`TEST_CONTRACT_REBASELINE_REQUIRED` or operative `/opsx-apply-red` is forbidden
(`ALREADY_SATISFIED` / durable pass observation) while digest lineage is stale.

This command is the chat entry for split-apply completion. It does **not**
weaken assertions, edit APPLY DESIGN, or skip Owner pause packets when the
runtime requires them.

**Runtime CLI:** `opsx_tools/opsx-post-implement-test-contract-recovery-cli.mjs`  
**Authority:** `.cursor/pipeline/contracts/SPLIT_APPLY_TARGET_RUNTIME.md`  
**Parent front door:** `/opsx-apply` (split-aware recovery routing)

## When to run

Run when split-aware `/opsx-apply` step 1 (or `/opsx-apply-green`) surfaces:

- `code: TEST_CONTRACT_REBASELINE_REQUIRED`
- `FROZEN_TEST_MUTATED_*` with **no** executable operative re-RED (frozen tests
  already pass; re-RED cannot repair lineage)
- `postImplementRecoveryBlocksOperativeRedRoute` semantics in pipeline identity

Do **not** use this command to bypass a healthy operative re-RED hop when
`next_command` is `/opsx-apply-red …` and RED is not `ALREADY_SATISFIED`.

## Input

```text
change=<change-id>
mode=advance|replay|status   # default: advance
slice=<SLICE-ID>             # optional hint when cohort names one slice
recovery-attempt-id=<id>     # optional for advance/advance-cohort (auto-minted)
```

Resolve `<changeRoot>` as `openspec/changes/<change-id>` (or store-aware root
from `openspec status --change "<id>" --json`).

## Steps

1. **Preflight**

   - Confirm `apply-design/APPLY-DESIGN.json` has `status: APPLY_DESIGN_READY`.
   - Read `.cursor/rules/testing-rules.mdc` — recovery must not edit production
     to “fix” tests; identity/evidence repair only per runtime.

2. **Invoke CLI (machine authority)**

   ```bash
   node opsx_tools/opsx-post-implement-test-contract-recovery-cli.mjs advance \
     --change-root openspec/changes/<change-id>
   ```

   `advance` / `advance-cohort` **mint** `--recovery-attempt-id` when omitted
   (`chat-<stamp>-<digest>`). `replay` / `invoke-successor` require an explicit id.

   Other modes:

   ```bash
   node opsx_tools/opsx-post-implement-test-contract-recovery-cli.mjs status \
     --change-root openspec/changes/<change-id>

   node opsx_tools/opsx-post-implement-test-contract-recovery-cli.mjs replay \
     --change-root openspec/changes/<change-id> \
     --recovery-attempt-id <id>
   ```

3. **Interpret result**

   - `ok: true` and phase advances toward `IMPL_OK` / successor GREEN plan →
     **PRIMARY** `NEXT_ACTION: /opsx-apply <change-id>` (split-aware; often
     `/opsx-apply-green` on named slice).
   - `pause: true` with Owner packet (`RECOVERY_AUTHORITY_PENDING`, classification,
     verify command adoption) → emit packet verbatim; stop without product edits.
   - `ok: false` hard block → report `code`; do not loop blindly.

4. **Completion coupling**

   Recovery success does **not** equal `APPLY_COMPLETE`. After advance succeeds,
   `/opsx-apply` must resume the split stage chain until plan reports
   `APPLY_COMPLETE`, then legacy tasks if still pending.

## Output

```text
STATUS: RECOVERY_ADVANCED | RECOVERY_PAUSED | RECOVERY_BLOCKED | FAIL_CLOSED
MODE: <advance|replay|status>
CHANGE: <change-id>
SLICE: <id or n/a>
CODE: <runtime code>
LEDGER_PHASE: <phase or n/a>

NEXT_ACTION:
  /opsx-apply <change-id>
```

When paused, replace `NEXT_ACTION` with the runtime Owner packet path only.

## Guardrails

- Never edit frozen RED test bodies from this command.
- Never mark tasks `[x]` or claim `/opsx-verify mode=archive` GO.
- Do not substitute `/opsx-verify` for missing GREEN after recovery.
- Cohort / multi-slice pilots follow CLI adoption gates; do not bypass HMAC /
  Owner decision directories when CLI requires them.
