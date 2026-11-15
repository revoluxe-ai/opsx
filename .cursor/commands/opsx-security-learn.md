---
component_id: opsx.commands.security_learn
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-security-learn.md
required_adapters: ["opsx-lite", "project-generic"]
local_modification_note: "Class learning only from identity/freshness-bound reviewed evidence. Never from raw unreviewed findings."
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-SECURITY-LEARN-01
description: "Record a transferable security detection class after plane-correct REMEDIATED review."
argument-hint: "case=<SECURITY_CASE_ID> finding=<SECURITY_FINDING_ID>"
---

# STROY-SYSTEM TARGET OVERRIDE - HIGHEST PRIORITY

This command was transferred from a project-specific OPSX realization.
For stroy-system, product-specific realization MUST resolve through:

  opsx/project/capabilities.yaml
  opsx/shared/target-capability-resolution.md

Any inherited donor literal (WB Sky, adv-bot, Poetry, pyproject.toml, app/**,
tests/**, npm/package.json, src/**, public/**, deploy names) is historical or
compatibility text only and is NOT execution authority.

Before any product-specific read/write/command execution, resolve the
corresponding TARGET capability. If unresolved, fail closed:

STATUS: FAIL_CLOSED
REASON: TARGET_CAPABILITY_UNRESOLVED
CAPABILITY: <required capability>
NEXT_ACTION: define/discover capability in opsx/project/capabilities.yaml

Do not infer product stack from the donor repository.
Control-plane runtime paths under tools/temporal and tools/cursor_executor are
separate from product Temporal/runtime adoption.

# TARGET OVERRIDE — HIGHEST PRIORITY

**Contract:** `OPSX-WB-SECURITY-LEARN-01`
**Runtime class:** `ACTIVE`

```text
IMPLEMENTATION_STATUS: FUNCTIONAL_PROVEN
RUNTIME_STATUS: ACTIVE
CONTRACT_ID: OPSX-WB-SECURITY-LEARN-01
SECURITY_LEARNING_AUTHORITY: YES
SECURITY_SIGNAL_AUTHORITY: NO
SECURITY_REVIEW_VERDICT_AUTHORITY: NO
SECURITY_REMEDIATION_WRITER: NO
GREEN_WRITER: NO
ARCHIVE_VERDICT_WRITER: NO
COMMIT_ALLOWED: NO
```

# /opsx-security-learn

Extract a **class** lesson after confirmed triage plus independent
plane-correct `FINDING_DISPOSITION=REMEDIATED`. Never learn from raw signals.

Helper: `.cursor/pipeline/security-learn/security_learn.py`
Tests: `.cursor/pipeline/security-learn-tests/test_security_learn.py`

Write boundary:

```text
opsx/runs/security-learning/<case>/<finding>.md
```

## Invocation

```text
/opsx-security-learn
$ARGUMENTS
```

```text
/opsx-security-learn case=<SECURITY_CASE_ID> finding=<SECURITY_FINDING_ID>
```

Required helper fields: `lesson_class`, `lesson_text`. Optional:
`review_path=`, `finding_path=`, `revision=<40-hex-head>`.

## Eligibility

1. Durable `SECURITY_FINDING_PACKET` with `TRIAGE_STATUS=CONFIRMED`
2. Plane-correct reviewed evidence, identity-bound (`finding`, `SOURCE_SIGNAL_HASH`)
   and freshness-bound (`REVISION` = current HEAD):
   - CONTROL_PLANE → `/opsx-control-plane-security-review` packet
   - PRODUCT → current `/opsx-security-review` report JSON (`ARCHIVE_VERDICT: not_issued`)
   - INFRASTRUCTURE → `BLOCKED_AUTHORITY_GAP`
3. `FINDING_DISPOSITION=REMEDIATED`

Raw findings, FAIL reviews, chat narrative, and incident-specific lesson text
(`INCIDENT_SPECIFIC_NOT_TRANSFERABLE`) do not mint a lesson.

This command does not write product code, process contracts, bug-cases, or
archive security PASS.
