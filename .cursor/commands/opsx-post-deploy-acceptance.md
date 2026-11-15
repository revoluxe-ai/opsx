---
component_id: opsx.commands.post_deploy_acceptance
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-post-deploy-acceptance.md
required_adapters: ["opsx-lite", "project-generic"]
local_modification_note: "Read-only acceptance bound to target devops receipts and declared probes. No game host, no production mutations."
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-POST-DEPLOY-ACCEPTANCE-01
description: "Read-only post-deploy acceptance from a contained devops deploy receipt."
argument-hint: "deploy_receipt=opsx/runs/devops/<ts>/DEPLOY_RESULT.md [catalog=smoke]"
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

**Contract:** `OPSX-WB-POST-DEPLOY-ACCEPTANCE-01`
**Runtime class:** `ACTIVE`

```text
IMPLEMENTATION_STATUS: FUNCTIONAL_PROVEN
RUNTIME_STATUS: ACTIVE
CONTRACT_ID: OPSX-WB-POST-DEPLOY-ACCEPTANCE-01
PRODUCTION_WRITES: NO
GREEN_WRITER: NO
ARCHIVE_VERDICT_WRITER: NO
COMMIT_ALLOWED: NO
PROD_UI_HOST: none
AUTHORITY: /opsx-devops
```

# /opsx-post-deploy-acceptance

Post-deploy **acceptance** for adv-bot: prove a deploy receipt is success and
identity-bound to declared probes. Side-lane. Does not deploy, does not
archive, does not replace `/opsx-browser-check`.

Helper: `.cursor/pipeline/post-deploy-acceptance/post_deploy_acceptance.py`
Tests: `.cursor/pipeline/post-deploy-acceptance-tests/test_post_deploy_acceptance.py`

## Invocation

```text
/opsx-post-deploy-acceptance
$ARGUMENTS
```

```text
/opsx-post-deploy-acceptance deploy_receipt=opsx/runs/devops/<ts>/DEPLOY_RESULT.md catalog=smoke
```

`deploy_receipt=` is required and must canonicalize under
`opsx/runs/devops/**`. Allowed names: `DEPLOY_RESULT.md`,
`DEPLOY_RESULT.json`, `LAST.json`.

`GO`, `mutating=true`, and `base_url=` (including any game host) are
`PRODUCTION_MUTATION_FORBIDDEN` or `URL_NOT_ALLOWLISTED`. This command does
not broaden `/opsx-devops` write authority.

Stale `execution_id` or non-current `revision` fail closed.
Missing probes → `acceptance_probes_missing`, never PASS-by-absence.

PASS means: receipt success markers plus at least one `PROBE:` /
`ACCEPTANCE_PROBE:` line. It is not archive GO and not Kubernetes mutate.
