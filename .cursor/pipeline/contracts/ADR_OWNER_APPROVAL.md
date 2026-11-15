# ADR Owner approval evidence

Machine-checkable Owner authorization for a change-local architecture
decision. The decision file stays `Proposed` until archive promotion.
Apply does not accept or promote it. Verify does not treat `Proposed`
as a placement violation when this evidence is `approved`. Archive does
not promote on verify `GO` alone.

Checker:

```text
python .cursor/pipeline/adr-owner-approval/check_adr_owner_approval.py
```

The checker never writes `owner_signature`.

## Change identity

Verify already checks these two fields on scenario evidence
(`.cursor/commands/opsx-verify.md`, `SCENARIO_TEST_COVERAGE_REPORT`):

```text
CHANGE_ID
WORKTREE_FINGERPRINT
```

`WORKTREE_FINGERPRINT` is `HEAD=<40 lowercase hex>` from
`git rev-parse HEAD` of the change worktree. This evidence uses that
same pair. It does not invent a second change identity.

A new commit changes `HEAD` and makes a previously matching approval
`STALE` even when the decision bytes are unchanged. The Owner re-signs
for the new fingerprint. Content binding is separate and still required.

## Content hash

`adr_content_hash` is sha256 of the decision file after newline
normalization (`CRLF`/`CR` → `LF`). No other rewrite. Algorithm token:
`sha256-lf-normalized`.

## Approval artifact

Path:

```text
openspec/changes/<change-id>/decisions/<decision-id>/owner-approval.json
```

Schema: `.cursor/pipeline/contracts/adr-owner-approval.schema.json`

| Field | Role |
| --- | --- |
| `schema_version` | `adr-owner-approval-v1` |
| `status` | `pending` or `approved` |
| `change_id` | OpenSpec change id |
| `decision_id` | directory name under `decisions/` |
| `decision_path` | repo-relative `decision.md` |
| `adr_content_hash` | hash of that file |
| `content_hash_algorithm` | `sha256-lf-normalized` |
| `worktree_fingerprint` | `HEAD=<40 hex>` |
| `owner_signature` | `null` while pending; Owner token when approved |
| `signed_canonical` | exact UTF-8 JSON the Owner signs |

`pending` is not approval. `owner_signature` stays JSON `null`.
Agents, apply, verify, and archive must not fill it.

### Bytes the Owner signs

Canonical JSON of the bound fields only, UTF-8, keys sorted,
separators `(",", ":")`, no trailing newline. Same function as
`canonical_bytes` in the checker (and bounded-verify canonical JSON).

Bound fields:

```text
adr_content_hash
change_id
content_hash_algorithm
decision_id
decision_path
schema_version
worktree_fingerprint
```

`status`, `owner_signature`, and `signed_canonical` are not inside the
signed payload. `signed_canonical` must equal those bytes as text.

An approved signature is a non-empty Owner token of at least 32
characters from `[A-Za-z0-9+/=_-]`. Placeholder words (`pending`,
`unsigned`, `approved`, `yes`, …) do not pass. There is no project key
and no mint function. The token is present only after the Owner writes
it. The checker binds the token to the hashes; it does not prove who
typed it beyond that shape.

## Verify

For each change-local decision used for placement, run:

```text
python .cursor/pipeline/adr-owner-approval/check_adr_owner_approval.py approval \
  --change-id <change-id> \
  --evidence openspec/changes/<change-id>/decisions/<decision-id>/owner-approval.json \
  --decision openspec/changes/<change-id>/decisions/<decision-id>/decision.md
```

Omit `--fingerprint` so the checker uses the current `HEAD`.

| Checker status | Verify |
| --- | --- |
| `APPROVED` | This Proposed decision is authorized. Do not require `## Status` `Accepted`. Do not emit a placement violation for `Proposed`. |
| `PENDING` / `MISSING` | Archive blocker `adr_owner_approval_pending`. Not `placement_violations`. Not `Architecture Decision Missing` when the proposed file exists. |
| `STALE` | Archive blocker `adr_owner_approval_stale`. |
| `PROMOTED_EARLY` | Archive blocker `adr_status_promoted_early`. Change-local `Accepted` is not authorization. |
| `INVALID` | Archive blocker `adr_owner_approval_pending`. |

Accepted registry files under `openspec/decisions/` stay the permanent
source after promotion. Until then, placement may follow the Proposed
file plus `APPROVED` evidence.

## Archive authorization

Separate artifact. Approval does not authorize promotion.

Path:

```text
openspec/changes/<change-id>/adr-archive-authorization.json
```

Schema: `.cursor/pipeline/contracts/adr-archive-authorization.schema.json`

Bound fields signed the same way:

```text
change_id
decisions
schema_version
worktree_fingerprint
```

Each `decisions[]` entry:

```text
decision_id
decision_path
approval_path
adr_content_hash
```

`status` is `pending` or `authorized`. Promotion requires `authorized`
and a passing Owner token. Verify `✅ GO` is not a field of this file
and is not implied by it.

Check:

```text
python .cursor/pipeline/adr-owner-approval/check_adr_owner_approval.py archive-authorization \
  --change-id <change-id> \
  --authorization openspec/changes/<change-id>/adr-archive-authorization.json
```

| Checker status | Archive |
| --- | --- |
| `AUTHORIZED` | Authorization gate is open for the listed decisions. |
| anything else | Do not promote. Do not move the change. |

## Promotion

Promote one listed decision only when all three are true:

1. `/opsx-verify mode=archive` for this change ended with `✅ GO`.
   `✅ IMPL_OK` does not count. `GO` alone does not promote.
2. `archive-authorization` checker status is `AUTHORIZED` for this
   `CHANGE_ID` and current `WORKTREE_FINGERPRINT`.
3. `approval` checker status is `APPROVED` for that decision (the
   archive checker already requires this).

Then copy `decision.md` to
`openspec/decisions/<decision-id>/decision.md` and, in that registry
copy only, change the `## Status` value from `Proposed` to `Accepted`.
Leave the change-local file `Proposed`. If `openspec/decisions/index.md`
exists, add the registry path there. Report the promoted path.

If no decision is both reusable and authorized, output
`Reusable architecture promotions: none` and do not invent a registry
file.

Apply must not perform this copy and must not edit `## Status` to
`Accepted`.
