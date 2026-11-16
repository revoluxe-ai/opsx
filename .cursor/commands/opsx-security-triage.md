---
component_id: opsx.commands.security_triage
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-security-triage.md
required_adapters: ["opsx-lite", "project-generic"]
local_modification_note: "SOURCE vue-npm security_triage capability is not declared on this target and is not invented. Bounded local citation path only. IMPLEMENTED_UNVERIFIED."
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-SECURITY-TRIAGE-01
---


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


# /opsx-security-triage

# TARGET OVERRIDE — HIGHEST PRIORITY

**Contract:** `OPSX-WB-SECURITY-TRIAGE-01`
**Runtime class:** `ACTIVE_WITH_LIMITS`
**Capability parity:** partial. Declared `security_triage` adapter: absent.

```text
IMPLEMENTATION_STATUS: IMPLEMENTED_UNVERIFIED
RUNTIME_STATUS: ACTIVE_WITH_LIMITS
CONTRACT_ID: OPSX-WB-SECURITY-TRIAGE-01
CAPABILITY_PARITY: PARTIAL
SECURITY_TRIAGE_DECLARED: NO
LOCAL_EVIDENCE_PATH: bounded_workspace_citation
SOURCE_PARITY: PARTIAL
SECURITY_SIGNAL_AUTHORITY: YES
SECURITY_REVIEW_VERDICT_AUTHORITY: NO
SECURITY_REMEDIATION_WRITER: NO
GREEN_WRITER: NO
ARCHIVE_VERDICT_WRITER: NO
COMMIT_ALLOWED: NO
BUG_LANE_OWNER: NO
CONFIDENCE_IS_PROOF: NO
MODE: READ_ONLY_EXCEPT_SECURITY_EVIDENCE
```

`IMPLEMENTED_UNVERIFIED` stays until a real invocation of this command is
observed. Helper tests do not prove the live slash command.

## Purpose

Take **one** raw security signal, check the minimum cited workspace evidence,
and materialize a `SECURITY_FINDING_PACKET` only when that evidence confirms
the claim.

```text
SECURITY_SIGNAL ≠ SECURITY_FINDING ≠ SECURITY_REVIEW_VERDICT
```

This command owns signal → triage disposition → optional case-local packet.
It does not own archive security review, remediation, bug classification, or
learning PASS.

## Capability gate

Read `opsx/projectization.yaml` and `opsx/adapters/poetry-python/README.md`.

- Projectization `status` must be `COMPLETE` and `normal_commands_enabled`
  true. Otherwise `STATUS: BLOCKED`, `REASON: OPSX_PROJECTIZATION_REQUIRED`.
- SOURCE names a `vue-npm` `security_triage` capability. This target does not
  declare that capability. Do not add it and do not invent a `vue-npm` adapter.
- Usable path: the Poetry helper below, workspace file citations only.
  Record `CAPABILITY_PARITY: PARTIAL` and `SECURITY_TRIAGE_DECLARED: NO` on
  every result.
- No scanner, browser, production, or Kubernetes probe is proof.

## Invocation

```text
/opsx-security-triage
$ARGUMENTS
```

```text
/opsx-security-triage
  source=<source-kind>
  signal="<one raw security finding>"
  [source_ref=<ref>]
  [source_note=<note>]          # required when source=OTHER
  [cited_path=<workspace-relative path>]
  [symbol=<symbol>]
  [severity=<severity>]
  [change=<change-id>]
  [declared_plane=PRODUCT|CONTROL_PLANE|INFRASTRUCTURE]
  [case_id=SEC-CASE-...]
```

Exactly one signal. Missing `signal` → `BLOCKED` / `missing_signal`.

Source kinds: `CURSOR_AGENTIC_SECURITY_REVIEW`, `PR_SECURITY_REVIEW`,
`CI_SECURITY_SIGNAL`, `MANUAL_SECURITY_REVIEW`, `USER_SECURITY_SIGNAL`,
`OTHER`.

## Sole writer

The only writer of a case-local packet is:

```text
.cursor/pipeline/security-triage/security_triage.py
```

Schema:

```text
.cursor/pipeline/security-triage/SECURITY_FINDING_PACKET.schema.json
```

Focused tests:

```text
.cursor/pipeline/security-triage-tests/test_security_triage.py
```

Build one request JSON and run:

```powershell
python .cursor\pipeline\security-triage\security_triage.py --repo-root <workspace> --request-file <request.json>
```

Do not hand-write a packet. Do not write `opsx/runs/bug-cases/**`,
`openspec/changes/**/verify/SECURITY-REVIEW.md`, product code, or git state.

Packets live only at:

```text
opsx/runs/security-cases/<SECURITY_CASE_ID>/findings/<SECURITY_FINDING_ID>.md
```

Create is exclusive. A case ledger line is append-only after the packet is
durable. A changed signal hash is a different finding. Replay of the same
hash is `DUPLICATE` and reuses the existing case, including across case ids.

`ABSENCE_IS_NOT_A_DISPOSITION`: a missing packet is not `FALSE_POSITIVE` and
not an archive verdict.

## Path containment

Before any read of `cited_path`, `cited_paths`, or a path cited in the signal,
the helper resolves the path and requires separator-aware containment in the
workspace root. `..`, an absolute path outside the root, a symlink or junction
that leaves the root, and a sibling directory whose name only shares the root
prefix are `BLOCKED` with `REASON: path_outside_workspace`. Do not open those
paths.

## Disposition

```text
STATUS: CONFIRMED | FALSE_POSITIVE | NEEDS_CONTEXT | OUT_OF_SCOPE | DUPLICATE | BLOCKED
```

| Disposition | Rule |
|---|---|
| `CONFIRMED` | Every supplied evidence quote is present in the cited file, the contract fields are complete, and the plane is classified. The localhost substring fixture is confirmed only when both `"localhost" in` and `"127.0.0.1" in` are in the cited file and the signal mentions that host pattern. |
| `FALSE_POSITIVE` | Disproof quotes are in the file and the claimed quotes are not. |
| `NEEDS_CONTEXT` | Cited file or quote is missing, or the contract is incomplete. Never downgrade this to `FALSE_POSITIVE`. |
| `OUT_OF_SCOPE` | Caller names another authority and in-repo quotes do not confirm a finding. |
| `DUPLICATE` | Same `SOURCE_SIGNAL_HASH` already has a confirmed packet. |
| `BLOCKED` | Bad args, projectization, containment, `plane_misroute`, identity collision, or `interrupted_evidence_write`. |

`CONFIDENCE_IS_PROOF: NO`. A confidence value without a verified quote does
not confirm.

Plane is classified from the cited surface, not from prose:

```text
AFFECTED_PLANE: PRODUCT | CONTROL_PLANE | INFRASTRUCTURE
```

- `app/**` → `PRODUCT`
- `.cursor/**`, `opsx/**` → `CONTROL_PLANE`
- `k8s/**`, `dockers/**`, compose file → `INFRASTRUCTURE`

`declared_plane` that disagrees with that class, or explicit cited paths in
more than one class, is `BLOCKED` / `plane_misroute`. No packet is written.

## Routing when CONFIRMED

| Plane | Route | Next | Verifier on this target |
|---|---|---|---|
| `PRODUCT` and `openspec/changes/<id>` exists | `UPDATE` | `/opsx-update` only | `none` (`opsx-security-review` is not runnable) |
| `PRODUCT` without a bound Change | `NEEDS_OWNER` | `none` | `none` |
| `CONTROL_PLANE` | `PROCESS_REPAIR` | `/opsx-meta-fix-command-defects` | `none` (`opsx-control-plane-security-review` is not installed) |
| `INFRASTRUCTURE` | `BLOCKED_AUTHORITY_GAP` | `none` | `none` |

Do not invent an infra command. Do not invent a product Change id for a
control-plane finding. `CONFIRMED` does not authorize the remediation write.
Do not emit archive `PASS`, `PASS_WITH_RISK`, or `FAIL` as `STATUS`.

`NEEDS_CONTEXT` does not create a finding file.

## Return

Print the helper JSON. Required fields include `STATUS`, `REASON`,
`SOURCE_SIGNAL_HASH`, `SECURITY_CASE_ID`, `SECURITY_FINDING_ID`,
`EVIDENCE_PATH`, `SECURITY_FINDING_PACKET` (`present`, `absent`, or
`reused_duplicate`), `AFFECTED_PLANE`, and `SECURITY_REVIEW_VERDICT:
not_issued`.

## Do not

- Do not treat triage as archive security PASS / FAIL.
- Do not write `verify/SECURITY-REVIEW.md`.
- Do not open `opsx/runs/bug-cases/**` or call bug-team / classify-bug.
- Do not invent `/opsx-security-fix`.
- Do not edit product code or OpenSpec planning.
- Do not probe production, Kubernetes, or secrets.
- Do not stage, commit, or push.
