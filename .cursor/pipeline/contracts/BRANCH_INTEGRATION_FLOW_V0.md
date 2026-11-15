# Branch integration flow V0 (Owner policy)

`LIVE_AUTHORITY: NO` — human and platform settings remain authoritative; this
contract records **approved** integration discipline for `stroy-system`.

## Long-lived branches

| Branch | Role |
|--------|------|
| `dev` | Integration branch for feature work |
| `stage` | Downstream promotion target from `dev` |
| Other long-lived targets | Reached **only** via `stage` (no direct `dev` → target promotion) |

## Rules

1. **Feature start:** New feature development branches from **current** `dev`.
2. **Feature finish:** Integrate back into **`dev`** (PR or equivalent review).
3. **Downstream promotion:** The only authorized route from `dev` is **`dev` → `stage`**.
4. **Further distribution:** Promotion to other long-lived target branches goes **through `stage`**.
5. **Prohibited:** Direct promotion from `dev` to any long-lived target other than `stage`.
6. **Sync, not promotion:** Temporary feature/PR branches may fetch/merge **current `dev`** to prepare integration; this is reconciliation, not downstream promotion.
7. **No automation:** No automatic merge or promotion between long-lived branches.

## Relation to other contracts

- Checkpoint and ref protection: `BRANCH_PROTECTION_V0.md` (protected refs, grants).
- Architecture evidence freeze: `opsx/TECH_DEBT_REGISTER.md` (orthogonal).

**Effective:** Owner decision 2026-10-24.
