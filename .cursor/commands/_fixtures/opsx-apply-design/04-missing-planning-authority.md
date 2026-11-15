# Fixture 04 — missing required frozen planning authority

## Given

`change=fixture-missing`

`openspec status --change "fixture-missing" --json` resolves
`changeRoot=openspec/changes/fixture-missing`.

On disk:

- `owner-contract.md` may exist
- `tasks.md` is **missing**
- no `design.md`, `proposal.md`, or `specs/**/spec.md`

The executor must not invent tasks or architecture from chat.

## Expected

```text
STATUS: BLOCKED
BLOCK_REASON: MISSING_PLANNING_AUTHORITY
CHANGE: fixture-missing
NEXT_STAGE: NONE
CHAT_HISTORY_REQUIRED: false
WRITE_BOUNDARY.product_code: false
WRITE_BOUNDARY.tests: false
```

Do not guess slices. Do not emit `APPLY_DESIGN_READY`.
Do not emit `NEXT_ACTION: /opsx-apply-design-review` or `/opsx-apply-scaffold`.

Golden machine artifact: `04-missing-planning-authority.APPLY-DESIGN.json`

Variant (no write): unresolvable `change_id` → `INVALID_CHANGE_ID` and
`DESIGN_ARTIFACT: none`.
