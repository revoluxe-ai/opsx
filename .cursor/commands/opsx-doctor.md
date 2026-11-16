---
component_id: opsx.commands.doctor
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-doctor.md
required_adapters: ["opsx-lite", "project-generic"]
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-DOCTOR-01
---


# TARGET OVERRIDE — HIGHEST PRIORITY

# PROJECT TARGET PROFILE — REQUIRED

Product-specific execution authority resolves only through:

  opsx/project/capabilities.yaml

Adapter-specific supplements apply only when that adapter is selected in
capabilities.yaml (see opsx/adapters/<adapter-id>/).

Donor literals in this command are historical compatibility text, not authority.
Before product-specific reads, writes, or commands, resolve the required capability.
If unresolved, fail closed with TARGET_CAPABILITY_UNRESOLVED.

Control-plane paths under tools/temporal and tools/cursor_executor are separate
from product runtime adoption.


# /opsx-doctor

Run a read-only hygiene clinic over OpenSpec active change trees. It combines
`openspec doctor` relationship evidence with bounded checks for corrupt active
leftovers. It never repairs planning, archives a change, or deletes a path.

## User input

```text
$ARGUMENTS
```

Supported forms:

```text
/opsx-doctor
/opsx-doctor CHECK
/opsx-doctor CHECK change=<name>
```

Legacy read-only aliases `scope=all`, `scope=active`, and
`repair=remove_corrupt_leftover` may be accepted but do not enable writes.

`GO`, acknowledgement, `ok`, or any repair wording does not authorize a write
in this TARGET adaptation. If exact `GO` is present, return `GO_UNSUPPORTED`
without deleting, unstaging, staging, or changing any file.

## Authority and safety

```text
IMPLEMENTATION_STATUS: IMPLEMENTED_WITH_LIMITS
RUNTIME_STATUS: ACTIVE_WITH_LIMITS
CONTRACT_ID: OPSX-WB-DOCTOR-01
MODE: CHECK
READ_ONLY: true
PRODUCT_WRITER: NO
ARCHIVE_AUTHORITY: NO
DELETE_AUTHORITY: NO
COMMIT_ALLOWED: NO
PRODUCTION_WRITES_ALLOWED: NO
```

- Default and explicit `CHECK` are strictly read-only.
- Do not create, edit, move, delete, unstage, stage, commit, merge, push,
  checkout, reset, clean, stash, sync specs, promote decisions, or archive.
- Do not run product code, project tests, WB/HTTP/PG/CH/Redis writes,
  Kubernetes commands, deployments, or network calls.
- Do not install dependencies or modify `pyproject.toml` / `poetry.lock`.
- Do not create or fill `proposal.md` or `design.md`; absence is evidence, not
  permission to invent planning.
- Never touch `openspec/specs/**`, `openspec/decisions/**`, an archive tree, or
  another active change.
- This command does not emit `/opsx-verify` GO/NO_GO and does not compete with
  `/opsx-archive`, whose archive move remains authoritative.

Follow `.cursor/commands/opsx-source-resolution.md` and
`.cursor/commands/opsx-execution-preflight.md`: exact paths first, bounded
reads, and no recursive body dump of all changes or archives.

## Store selection

If the Owner names a standalone store or the work lives in one, run
`openspec store list --json`, resolve the registered id, and pass
`--store <id>` to `openspec doctor`, `openspec list`, and every
`openspec status` call. Otherwise use the nearest local OpenSpec root. Keep one
resolved store/root for the entire report.

## Target path authority

1. Run `openspec list --json` to obtain active change names.
2. For every selected active name, run
   `openspec status --change "<name>" --json`.
3. Use only status JSON `changeRoot`, `planningHome.changesDir`, and
   `artifactPaths`. Never reconstruct an active path when status resolves it.
4. The only archive root for that planning home is
   `<planningHome.changesDir>/archive`. In this repository's local planning
   home it resolves to `openspec/changes/archive`.
5. An archive sibling is a directory matching
   `<planningHome.changesDir>/archive/*-<name>`. Do not use, create, or scan a
   competing archive root.
6. Inspect only sibling directory names and the selected active
   `proposal.md`/`design.md` evidence. Do not recursively read archive content.
7. If status cannot resolve a listed or focused active change, report
   `UNRESOLVED_ACTIVE_CHANGE`; do not guess paths.

## Leftover classification gate

Classify `CORRUPT_LEFTOVER_ACTIVE_CHANGE` only when all facts are evidenced:

1. the status-resolved active `changeRoot` exists;
2. at least one same-name archive sibling exists below the exact resolved
   `<planningHome.changesDir>/archive`;
3. active `proposal.md` or `design.md` is missing, zero bytes, or
   whitespace-only.

When required planning is missing/corrupt but no archive sibling exists,
classify `CORRUPT_OR_EMPTY_PLANNING_ARTIFACTS`; never classify it as safely
deletable. Healthy non-empty planning is never a leftover.

The SOURCE destructive gate remains intentionally stricter than prose-only
authorization: TARGET has no deterministic executable pre-delete helper in
this batch. Therefore `TARGETS_FOR_GO` is evidence preview only and
`GO_SUPPORTED: false`.

## CHECK procedure

1. Run `openspec doctor --json`; preserve its `healthy`, `status`,
   relationship, root, and store evidence in the report.
2. Run `openspec list --json`.
3. Apply `change=<name>` only as an exact-name filter. Without it, inspect all
   listed active changes.
4. For each selected name, run status JSON and record:
   - resolved `changeRoot`;
   - resolved `planningHome.changesDir`;
   - resolved archive root;
   - proposal state: `meaningful | missing | zero_bytes | whitespace_only`;
   - design state: `meaningful | missing | zero_bytes | whitespace_only`;
   - exact matching archive sibling paths, or `none`.
5. Apply the classification gate mechanically. Do not infer corruption from
   task completion labels such as `complete` or `no-tasks`.
6. Emit every inspected path and fact needed to reproduce each finding.
7. Stop without writes.

No active changes is a valid CHECK result. Report the doctor evidence and an
empty scan; do not invent a planning home solely to search archives.

## GO behavior

Destructive GO is unavailable in this adaptation:

```text
STATUS: BLOCKED
MODE: GO
REASON: GO_UNSUPPORTED
GO_SUPPORTED: false
REPAIRED: none
OUTSIDE_ALLOWLIST: none
NEXT_ACTION: /opsx-doctor CHECK
```

Do not provide or execute a manual `Remove-Item`, `git rm`, Python deletion, or
equivalent workaround. A future transfer may enable GO only after a
deterministic executable pre-delete gate proves that each target is the exact
status-resolved active `changeRoot`, is directly below the resolved
`planningHome.changesDir`, has a same-name sibling below only
`<planningHome.changesDir>/archive`, has corrupt required planning, and is not
an archive tree, specs tree, decisions tree, or another active change. That
future enablement still requires a separate later exact `/opsx-doctor GO`
invocation; a prior CHECK never authorizes deletion.

## Finding kinds

| Kind | Meaning |
|---|---|
| `OPENSPEC_DOCTOR_UNHEALTHY` | `openspec doctor --json` reports unhealthy relationships. |
| `UNRESOLVED_ACTIVE_CHANGE` | List/focus named a change whose status paths could not be resolved. |
| `CORRUPT_OR_EMPTY_PLANNING_ARTIFACTS` | Required active planning is empty/missing without a matching archive sibling. |
| `CORRUPT_LEFTOVER_ACTIVE_CHANGE` | Active tree, matching archive sibling, and corrupt required planning are all evidenced; CHECK only. |

## Output

```text
STATUS: HEALTHY | FINDINGS | BLOCKED
MODE: CHECK | GO
GO_SUPPORTED: false

OPENSPEC_DOCTOR:
- healthy: yes | no | unknown
- root:
- store:
- status:

SCAN_SCOPE:
- requested_change: <name> | all
- active_names:

EVIDENCE:
- CHANGE:
  CHANGE_ROOT:
  CHANGES_DIR:
  ARCHIVE_ROOT:
  PROPOSAL_STATE:
  DESIGN_STATE:
  ARCHIVE_SIBLINGS: none | exact paths

FINDINGS:
- KIND:
  PATH:
  SEVERITY: warn | block
  EVIDENCE:
  NEXT: /opsx-doctor CHECK

TARGETS_FOR_GO: none | evidence-only list
REPAIRED: none
SKIPPED: none | name + reason
OUTSIDE_ALLOWLIST: none
STAGE_COMMIT_PUSH: not_performed
NEXT_ACTION: none | /opsx-doctor CHECK
```

`TARGETS_FOR_GO` must never be described as deletion authorization while
`GO_SUPPORTED: false`.

## Pipeline position

```text
ENTRY_STATE: anytime
COMMAND_ACTION: read-only hygiene evidence
EXIT_STATE: hygiene_ok | hygiene_findings | blocked_go_unsupported
NEXT_ALLOWED_STEP: none | /opsx-doctor CHECK
FAILURE_OR_RECOVERY_STEP: resolve OpenSpec relationship/path evidence, then CHECK
```

## Self-check

- [ ] Default and CHECK wrote or deleted nothing.
- [ ] Exact GO returned `GO_UNSUPPORTED` before any mutation.
- [ ] `openspec doctor --json` evidence is present.
- [ ] Every active path came from `openspec status --change ... --json`.
- [ ] Every archive sibling came only from
      `<planningHome.changesDir>/archive`.
- [ ] Findings distinguish corrupt planning from a proven archived leftover.
- [ ] No archive tree, main spec, decision, or active healthy change changed.
