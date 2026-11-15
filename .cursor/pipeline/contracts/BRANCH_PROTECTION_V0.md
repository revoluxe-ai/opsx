# Branch Protection V0 (OPSX-2 S1.5)

`LIVE_AUTHORITY: NO`

## Default protected refs

Full ref match (case-sensitive, after `refs/heads/` normalization):

| Ref | Source |
|---|---|
| `refs/heads/main` | Owner default |
| `refs/heads/master` | Owner default |
| `refs/heads/stage` | Owner default |
| `refs/heads/prod` | Retained from `/opsx-commit` gate |
| `refs/heads/production` | Retained from `/opsx-commit` gate |

`refs/heads/cursor/staging-fix-…` is **not** `refs/heads/stage` (no substring rules).

## Owner-configurable prohibition

Grants MAY include `owner_prohibited_refs`: additional branch **names** or full
`refs/heads/…` entries merged into the protected set for that grant only.
No silent new **global** mandatory defaults beyond the table above.

## Checkpoint deny-list (S4a Owner policy)

Full ref match after `refs/heads/` normalization (in addition to protected table):

| Pattern | Rule |
|---|---|
| `dev` | denied for `checkpoint_publish` |
| `mvp` | denied |
| `deploy/*` | denied (prefix `deploy/`) |

`checkpoint_publish` grant refs MUST be under `refs/heads/cursor/` (exact push still
targets only `grant.ref`, not every `cursor/*` branch).

## Checkpoint rules

- `grant.ref` in the protected set → `GRANT_REJECTED_PROTECTED_REF`
- Runner MUST NOT commit while `HEAD` is a protected ref
- Runner MUST NOT push to any protected destination
- `GO_ON_PROTECTED_BRANCH` does **not** authorize checkpoint push
- Push MUST target exactly `grant.ref` (no alternate refspec)

## Precedence

`BRANCH_PROTECTION_V0` outranks grant `checkpoint.extra_go: false` and Cloud platform
“commit and push when done” defaults.
