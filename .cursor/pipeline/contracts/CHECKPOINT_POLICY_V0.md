# Checkpoint Policy V0 (OPSX-2 S1.5)

`LIVE_AUTHORITY: NO` — contract and validator only; no pre-push hook in this slice.

## Action `checkpoint_publish`

Distinct from `push_ref`. One checkpoint = one non-empty commit on `grant.ref`, then one
fast-forward push of that oid to the **same** ref. No PR, merge, deploy, or second Owner `GO`.

| | `push_ref` | `checkpoint_publish` |
|---|---|---|
| `tip_oid` in grant | required before use | **forbidden** (filled in receipt after commit) |
| Extra `GO` at use | yes (create-pr path) | **no** (`checkpoint.extra_go: false`) |
| Invocation surface | publisher paths | `cloud_parent` only |

Grant with `checkpoint_publish` MUST NOT also list `local_commit`, `push_ref`, `create_pr`,
`merge`, or `update_pr_body`.

## Required grant fields

- `ref` — single authorized feature ref (`refs/heads/…`, not protected)
- `base_oid` — oid ref must contain at first use
- `execution_id` — binds grant to one Execution
- `grant_revision` — monotonic revision; stale revision rejected
- `checkpoint.mode` = `auto_commit_and_push`
- `checkpoint.extra_go` = `false`
- `checkpoint.idempotency` = `git_ff_oid`
- `subject.agent_id` — Cloud parent allowed to checkpoint

`owner_attestation_ref` MUST be Owner-held. Checkpoint receipts MUST NOT be presented as
attestation (grant issuance/storage remains outside worker write-authority; S4 gap).

## State machine (contract)

`NO_GRANT` → `GRANT_REJECTED` → `CHECKPOINT_ELIGIBLE` → `CHECKPOINT_LOCAL` →
`CHECKPOINT_PUSHING` → `CHECKPOINT_PUBLISHED` | `CHECKPOINT_IDEMPOTENT_NOOP` |
`CHECKPOINT_DIVERGED` | `DENIED_PROTECTED_REF` | `OWNER_ATTENTION` |
`BLOCKED_POLICY_VIOLATION`.

Checkpoint publication MUST NOT emit `GREEN` or `PASS` (not a verification verdict).

## Crash recovery

If local commit succeeded but remote push outcome is uncertain: enter `OWNER_ATTENTION`,
reconcile `remote_oid` against receipt `tip_oid` before retry. MUST NOT fabricate
`CHECKPOINT_PUBLISHED` without remote confirmation.

## Validator

- Grant: `.cursor/pipeline/execution-authority-grant/minimal_authority_grant.py`
- Attempt transitions: `.cursor/pipeline/execution-authority-grant/checkpoint_publication_v0.py`

See `BRANCH_PROTECTION_V0.md` and `EXECUTION_AUTHORITY_GRANT_V0.md` (precedence).
