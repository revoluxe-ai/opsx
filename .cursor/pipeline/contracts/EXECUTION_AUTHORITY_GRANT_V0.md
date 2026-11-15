# Execution Authority Grant V0

## Purpose

`MINIMAL_AUTHORITY_GRANT_V0` — минимальный Owner grant для узких execution lanes
(local commit, publication, model tier). Default deny: отсутствие grant = запрет.

Этот контракт **не** является live enforcement (нет pre-push hook в S0+S1).
`LIVE_AUTHORITY: NO` до отдельного Owner GO на S4+.

## Precedence (mandatory)

От высшего к низшему:

1. `BRANCH_PROTECTION_V0` (protected refs; см. `.cursor/pipeline/contracts/BRANCH_PROTECTION_V0.md`).
2. Стабильный OPSX command contract (`/opsx-supervisor` §19, §27; semantic-write;
   resolve-conflict hard stops).
3. Change freeze (`OPSX_CONTROL_READY`, executor packet).
4. `MINIMAL_AUTHORITY_GRANT_V0` + `checkpoint_publish` (см. `CHECKPOINT_POLICY_V0.md`;
   может **сужать**, не **расширять** против п.1–2).
5. Projectization capabilities (что может запускаться, не публикация).
6. Cloud Agent platform defaults (commit/push when done).

Grant из chat или workspace-файла **не** отменяет §27. Supervisor subagents
остаются под publication denylist даже при валидном grant.

См. также: `.cursor/pipeline/contracts/SEMANTIC_WRITE_TARGET.md`.

## Schema carrier

- JSON Schema: `.cursor/pipeline/execution-authority-grant/minimal_authority_grant_v0.schema.json`
- Validator: `.cursor/pipeline/execution-authority-grant/minimal_authority_grant.py`

## Model tier

- `model_tier` MUST be `inherit` only in V0.
- `model_tier: standard` is **undefined** → `GRANT_REJECTED_MODEL_TIER_UNDEFINED`.
- Fast slugs (`*-fast`, `auto`, omitted model) require explicit
  `owner_fast_allow: true` on an Owner-issued grant; default deny.
- `/opsx-static-check` `FAST` and `/opsx-risk` `STANDARD` are unrelated words.

## Grant minting

Workers MUST NOT mint grants. `owner_attestation_ref` MUST point to Owner-held
evidence (`store:…`, `cursor-stores/…`, `/cursor/stores/…`, `owner-message:…`).
Self-asserted workspace files satisfy **no** issuer proof.

## Actions vs forbids

`actions` and `forbids` MUST be disjoint. `local_commit` MUST NOT require
`tip_oid` before the commit exists. `push_ref` MUST bind `base_oid`, `tip_oid`,
and `ref`. `checkpoint_publish` MUST bind `base_oid` and `ref` only; `tip_oid` is
receipt evidence after commit (see `CHECKPOINT_POLICY_V0.md`).

## Command cross-links

| Command | Grant role |
|---|---|
| `/opsx-commit` | exact `GO` + optional grant attestation; no push |
| `/opsx-create-pr` | exact `GO` + publication grant fields when pushing |
| `/opsx-supervisor` | denylist dominates; grant cannot authorize subagent push |
| `/opsx-split-for-pr` | MUST NOT embed `GO`; publication via create-pr only |

## Status words

On conflict: `OWNER_ATTENTION`. After unauthorized publication reported:
`BLOCKED_POLICY_VIOLATION` (supervisor §19) remains distinct and is not replaced.
