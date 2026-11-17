---
component_id: opsx.commands.control_plane_security_review
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-control-plane-security-review.md
required_adapters: ["opsx-lite", "project-generic"]
local_modification_note: "Independent CONTROL_PLANE verifier using target security-case packets. Durable reviews under security-cases/**/reviews/control-plane/. No product/archive verdict."
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-CONTROL-PLANE-SECURITY-REVIEW-01
description: "Independent control-plane security verdict for one CONFIRMED finding."
argument-hint: "case=<SECURITY_CASE_ID> finding=<SECURITY_FINDING_ID>"
---


# TARGET OVERRIDE — HIGHEST PRIORITY

**Contract:** `OPSX-WB-CONTROL-PLANE-SECURITY-REVIEW-01`
**Runtime class:** `ACTIVE`

```text
IMPLEMENTATION_STATUS: FUNCTIONAL_PROVEN
RUNTIME_STATUS: ACTIVE
CONTRACT_ID: OPSX-WB-CONTROL-PLANE-SECURITY-REVIEW-01
CONTROL_PLANE_SECURITY_VERDICT_AUTHORITY: YES
PRODUCT_SECURITY_VERDICT_AUTHORITY: NO
PROCESS_WRITE_AUTHORITY: NO
INFRA_SECURITY_VERDICT_AUTHORITY: NO
GREEN_WRITER: NO
ARCHIVE_VERDICT_WRITER: NO
COMMIT_ALLOWED: NO
MODE: READ_ONLY_EXCEPT_CONTROL_PLANE_REVIEW_EVIDENCE
```

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


# /opsx-control-plane-security-review

Independent verifier for one CONFIRMED `AFFECTED_PLANE: CONTROL_PLANE`
finding. Not product review, not triage, not remediation.

Helper: `.cursor/pipeline/control-plane-security-review/control_plane_security_review.py`
Tests: `.cursor/pipeline/control-plane-security-review-tests/test_control_plane_security_review.py`

Write boundary:

```text
opsx/runs/security-cases/<case>/reviews/control-plane/<finding>.md
```

## Invocation

```text
/opsx-control-plane-security-review
$ARGUMENTS
```

```text
/opsx-control-plane-security-review case=<SECURITY_CASE_ID> finding=<SECURITY_FINDING_ID>
```

Optional: `finding_path=`, `remediation_path=`, `remediating_quote=`,
`reviewer_id=`, `remediator_id=`, `revision=<40-hex-head>`.

```text
poetry run python .cursor/pipeline/control-plane-security-review/control_plane_security_review.py --repo-root <workspace> --request-file <json>
```

Path containment is separator-aware. Escape → `path_outside_workspace`.
Stale HEAD → `stale_revision`. PRODUCT plane → `NOT_APPLICABLE`.
INFRASTRUCTURE → `NOT_APPLICABLE` / `BLOCKED_AUTHORITY_GAP`.

## Verdicts

| Observation | STATUS | FINDING_DISPOSITION |
|---|---|---|
| Original quote still present, remediating quote absent | `FAIL` | `STILL_VULNERABLE` |
| Remediating quote present, original gone, independent reviewer | `PASS` | `REMEDIATED` |
| Same reviewer and remediator | `BLOCKED` / `self_certification_forbidden` | |
| Missing evidence | `BLOCKED` | |

`PASS` is a control-plane review verdict, not archive PASS / PASS_WITH_RISK /
NOT_APPLICABLE and not `PRODUCT_SECURITY_VERDICT`.

Remediation remains `/opsx-meta-fix-command-defects`. This command does not
write `.cursor/**` contracts.
