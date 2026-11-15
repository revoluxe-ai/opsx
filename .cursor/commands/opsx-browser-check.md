---
component_id: opsx.commands.browser_check
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-browser-check.md
required_adapters: ["opsx-lite", "project-generic"]
local_modification_note: "Target-native browser verification. Adv-bot has no Vue product UI; NOT_APPLICABLE is machine-checkable when no browser scenario/surface exists. Declared browser scenarios fail closed."
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-BROWSER-CHECK-01
description: "Prove declared browser/web scenarios on the actual target surface, or return machine-checkable NOT_APPLICABLE."
argument-hint: "change=<change-id> | case=<CASE_ID> finding=<FINDING_ID> | mode=bounded"
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

**Contract:** `OPSX-WB-BROWSER-CHECK-01`
**Runtime class:** `ACTIVE`
**Capability:** `smoke_acceptance` is **not** declared on poetry-python.

```text
IMPLEMENTATION_STATUS: FUNCTIONAL_PROVEN
RUNTIME_STATUS: ACTIVE
CONTRACT_ID: OPSX-WB-BROWSER-CHECK-01
SOURCE_PARITY: ADAPTED
BROWSER_ADAPTER_DECLARED: NO
GREEN_WRITER: NO
ARCHIVE_VERDICT_WRITER: NO
COMMIT_ALLOWED: NO
LIVE_BROWSER_RUN: NO
```

# /opsx-browser-check

Bounded **browser/web** reality check for one OpenSpec change or bug finding.
This is not Telegram-bot unit GREEN and not archive verify.

Target product surface is a Telegram/WB bot under `app/**`. There is no Vue
SPA, no `cursor-ide-browser` product adapter, and no localhost web app in
quality gates. Helper:

`.cursor/pipeline/browser-check/browser_check.py`

Tests: `.cursor/pipeline/browser-check-tests/test_browser_check.py`

## Invocation

```text
/opsx-browser-check
$ARGUMENTS
```

```text
/opsx-browser-check change=<change-id>
/opsx-browser-check case=<CASE_ID> finding=<FINDING_ID>
/opsx-browser-check mode=bounded
```

Run:

```text
poetry run python .cursor/pipeline/browser-check/browser_check.py --repo-root <workspace> --request-file <json>
```

`revision` if supplied must equal current `git rev-parse HEAD`. Stale or
cross-execution evidence is `BLOCKED` / `stale_revision`.

## Semantics

| Evidence | Status | PASS |
|---|---|---|
| No browser markers; optional Telegram scenarios | `NOT_APPLICABLE` / `NO_DECLARED_BROWSER_SURFACE_OR_SCENARIO` | NO |
| Browser/Vue/SPA/HTTP/CDP markers present | `BLOCKED` / `BROWSER_SURFACE_UNDECLARED` | NO |
| `mode=bounded` | `BLOCKED` / `BINDING_GAP` / `S6_SMOKE_ACCEPTANCE_UNREGISTERED` | NO |
| `base_url=` | `BLOCKED` / `undeclared_browser_base_url` | NO |

`NOT_APPLICABLE` is valid only with machine-checkable scan evidence
(scenario_heading_count, marker lists, capability_declared=false). It is not
a blanket waiver for declared browser work.

Never report PASS, GREEN, or archive readiness from this command while no
target browser adapter exists. Do not fake Vue parity.

## Authority

- Write: none (helper stdout only)
- Production / Kubernetes / secrets: forbidden
- `/opsx-test` remains the Telegram behavior route
- `/tempo-verification` Owner browser-GO UX stays on Temporal `owner_browser_check_gate`; this command does not replace it
