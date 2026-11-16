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
