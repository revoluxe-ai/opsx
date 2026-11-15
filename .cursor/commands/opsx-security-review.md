---
component_id: opsx.commands.security_review
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-security-review.md
required_adapters: ["opsx-lite", "project-generic"]
local_modification_note: "SOURCE vue-npm security_review capability and Vue/browser checklist are not declared on this target and are not invented. Report-only PRODUCT delta. No change-local review packet. IMPLEMENTED_UNVERIFIED."
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-SECURITY-REVIEW-01
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

# /opsx-security-review

# TARGET OVERRIDE — HIGHEST PRIORITY

**Contract:** `OPSX-WB-SECURITY-REVIEW-01`
**Runtime class:** `ACTIVE_WITH_LIMITS`
**Capability parity:** partial. Declared `security_review` adapter: absent.
S6 static-check registry does not contain check id `security_review`.

```text
IMPLEMENTATION_STATUS: IMPLEMENTED_UNVERIFIED
RUNTIME_STATUS: ACTIVE_WITH_LIMITS
CONTRACT_ID: OPSX-WB-SECURITY-REVIEW-01
CAPABILITY_PARITY: PARTIAL
SECURITY_REVIEW_DECLARED: NO
SOURCE_PARITY: PARTIAL
SECURITY_VERDICT_PLANE: PRODUCT
PRODUCT_SECURITY_VERDICT_AUTHORITY: NO
PRODUCT_SECURITY_VERDICT: not_issued
CONTROL_PLANE_SECURITY_VERDICT_AUTHORITY: NO
INFRA_SECURITY_VERDICT_AUTHORITY: NO
ARCHIVE_VERDICT: not_issued
ARCHIVE_VERDICT_WRITER: NO
GREEN_WRITER: NO
COMMIT_ALLOWED: NO
DURABLE_RECEIPT: NONE
VERIFY_CONSUMES_SECURITY_REVIEW: NO
PACKET_WRITER: NONE
SURFACE_COVERAGE: undeclared
MODE: READ_ONLY
```

`IMPLEMENTED_UNVERIFIED` stays until a real invocation of this command is
observed. Helper tests do not prove the live slash command. This report is
not whole-change `/opsx-verify` and not archive GO.

SOURCE contract `OPSX-GAME-SECURITY-REVIEW-01` writes
`openspec/changes/<change>/verify/SECURITY-REVIEW.md` and may return
`PASS` / `PASS_WITH_RISK` / `NOT_APPLICABLE` because game declares
`security_review`. That writer is not installed here.

```text
FORBIDDEN_ARCHIVE_STATUS: PASS | PASS_WITH_RISK | NOT_APPLICABLE
```

Those statuses are refused when review evidence is missing, surface coverage
is undeclared, or evidence is stale or cross-revision. `DATA_GAP` is a valid
terminal status when evidence is incomplete. `FAIL` is a delta-report status
for a PRODUCT finding that is still open. It is not an archive verdict.

## Purpose

Answer, for one current change revision: what happened to each change-bound
CONFIRMED PRODUCT triage packet when the cited file is read again now?

```text
SECURITY_SIGNAL ≠ SECURITY_FINDING ≠ SECURITY_REVIEW_VERDICT
```

Plane authority stays PRODUCT-only. `CONTROL_PLANE` and `INFRASTRUCTURE`
packets are `OUT_OF_SCOPE` / `wrong_plane`. Their `PRODUCT_VERDICT` is
`not_set`. This command does not issue a product verdict for them and does
not route them through an invented infra verifier.

Preserved triage packets under `opsx/runs/security-cases/**` are inputs.
This command does not mutate them.

## Capability gate

Read `opsx/projectization.yaml` and `opsx/adapters/poetry-python/README.md`.

- Projectization `status` must be `COMPLETE` and `normal_commands_enabled`
  true. Otherwise `STATUS: BLOCKED`, `REASON: OPSX_PROJECTIZATION_REQUIRED`.
- `security_review: AVAILABLE` is not present on the Poetry adapter. Do not
  add it and do not invent a vue-npm adapter.
- `.cursor/static-check/registry.yaml` has no `security_review` check id.
  Do not add one in this command.
- `openspec/policies/VERIFY_POLICY.md` archive blockers do not consume a
  security-review packet. `VERIFY_CONSUMES_SECURITY_REVIEW: NO`.
- No scanner, browser, production, or Kubernetes probe is proof.

Usable path: `mode=report` through the helper below. Record
`CAPABILITY_PARITY: PARTIAL` and `SECURITY_REVIEW_DECLARED: NO`.

## Invocation

```text
/opsx-security-review
$ARGUMENTS
```

```text
/opsx-security-review mode=report change=<change-id> revision=<40-hex-head>
/opsx-security-review mode=bounded
```

`revision` must be the current `git rev-parse HEAD` of this workspace.
A different revision is `BLOCKED` / `revision_not_current`. There is no
cross-revision disposition.

Optional request fields for the helper, not a second writer:

- `security_findings`: extra finding ids. They do not replace the index.
- `remediation_evidence`: map of finding id to quotes that must be present
  in the same cited file before `REMEDIATED`.
- `index_scan: false` is `BLOCKED` / `index_scan_required`.

Build one request JSON and run:

```powershell
python .cursor\pipeline\security-review\security_review.py --repo-root <workspace> --request-file <request.json>
```

Helper:

```text
.cursor/pipeline/security-review/security_review.py
```

Focused tests:

```text
.cursor/pipeline/security-review-tests/test_security_review.py
```

## mode=bounded

Authority for a future S6 check would be a registered `security_review`
check id consumed by `/bounded-executor`. That id is not registered.

`mode=bounded` returns `BLOCKED` / `BINDING_GAP` and does not run the delta
report. no fallback to `change=`, DIFF, or index discovery.

| Registry fact | DETAIL |
|---|---|
| `security_review` absent | `S6_SECURITY_REVIEW_CAPABILITY_UNREGISTERED` |
| `security_review` present | `S6_SECURITY_REVIEW_RESULT_WRITER_NOT_INSTALLED` |

The second row still does not write `verify/check-results/**` or a
change-local review file. This command is not a bounded result writer.

## mode=report

1. Resolve `change` under `openspec/changes/<change-id>` with separator-aware
   containment. Missing change → `change_not_found`.
2. Require `revision` equal to current HEAD. Record `CHANGE`, `REVISION`,
   `HEAD`, `IDENTITY_MATCH`.
3. Mandatory index scan of
   `opsx/runs/security-cases/<case>/findings/*.md` for
   `TRIAGE_STATUS: CONFIRMED` and `CHANGE_BINDING` `STATUS: BOUND` to this
   change. Other changes and `NEEDS_CHANGE_BINDING` stay out of the set.
   An unreadable packet is `DATA_GAP` / `index_unreadable`, not an empty set.
4. Union finding ids cited in the change markdown (budget 20 files, 200 KB
   each) and ids passed as `security_findings`. An id with no packet is
   `DATA_GAP` / `finding_not_in_index`.
5. Disposition every indexed hit. A hit missing from the disposition set is
   `BLOCKED` / `skipped_finding`.
6. Same `SOURCE_SIGNAL_HASH` is one canonical disposition. The worst current
   evidence wins (`STILL_OPEN` before `REMEDIATED`). Other copies are
   `duplicate_signal_hash` and cannot hide an open quote.
7. Do not write a review file. Print the helper JSON.

### Path containment

Before any read of a packet `PATH`, resolve it and require separator-aware
containment in the workspace root. `..`, an absolute path outside the root,
a symlink or junction that leaves the root, and a sibling directory whose
name only shares the root prefix are `BLOCKED` with
`REASON: path_outside_workspace`. Do not open those paths. Refuse `.env`,
secret, pem, and key filenames with `DATA_GAP` / `secret_path_refused`
without reading them.

### Finding disposition

Re-read the cited file at the current revision. Do not re-infer
`AFFECTED_PLANE` from prose or from the path after triage classified it.

```text
FINDING_DISPOSITION: REMEDIATED | STILL_OPEN | NOT_REPRODUCED | DATA_GAP | OUT_OF_SCOPE
```

| Disposition | Rule |
|---|---|
| `OUT_OF_SCOPE` | `AFFECTED_PLANE` is `CONTROL_PLANE` or `INFRASTRUCTURE`. `PRODUCT_VERDICT: not_set`. |
| `DATA_GAP` | `stale_revision` when `OBSERVED_REVISION` or `CHANGE_BINDING.REVISION` differs from current HEAD; missing file; missing quotes; unreadable or oversize evidence; secret path. Stale evidence is not `REMEDIATED`. |
| `STILL_OPEN` | Every cited original quote that is still in the file is independent evidence, with path and line. |
| `REMEDIATED` | Every original quote is absent and every supplied remediation quote is present in that same file. |
| `NOT_REPRODUCED` | Original quotes are absent and no independent remediation quote is present. |

`SECURITY_LEARNING_ELIGIBILITY: ELIGIBLE` only for PRODUCT + `REMEDIATED` +
causal quotes. This command does not write a lesson and does not call
`/opsx-security-learn`.

### Report status

| STATUS | Rule |
|---|---|
| `BLOCKED` | Projectization, mode, identity, containment, bounded `BINDING_GAP`, or a skipped index hit. |
| `FAIL` | At least one PRODUCT finding is `STILL_OPEN`. `ARCHIVE_VERDICT` stays `not_issued`. |
| `DATA_GAP` | Evidence incomplete, stale, not reproduced, wrong-plane only, corrupt index, or no declared surface coverage. |

`SURFACE_COVERAGE: undeclared`. SOURCE Vue/browser checks (guest UI, CORS,
CSRF, XSS bio, bundle secrets, host-trust class ids) are not executed and
are not replaced with an invented WB scanner. A proven-empty index or a
fully remediated index remains `DATA_GAP` / `undeclared_surface_coverage`.
That is not `NOT_APPLICABLE`.

## Return

Print the helper JSON. Required fields include `STATUS`, `REASON`, `CHANGE`,
`REVISION`, `HEAD`, `INDEX_SCAN`, `INDEX_HITS`, `FINDINGS`,
`DUPLICATE_FINDINGS`, `FINDINGS_OPEN`, `SOURCE_PARITY`,
`SECURITY_REVIEW_DECLARED`, `ARCHIVE_VERDICT`, `PRODUCT_SECURITY_VERDICT`,
`DURABLE_RECEIPT`, and `VERIFY_CONSUMES_SECURITY_REVIEW`.

## Do not

- Do not write `verify/SECURITY-REVIEW.md`.
- Do not write `verify/check-results/**`.
- Do not mutate `opsx/runs/security-cases/**`.
- Do not emit archive `PASS`, `PASS_WITH_RISK`, or `NOT_APPLICABLE`.
- Do not treat `FAIL` or `DATA_GAP` as `/opsx-verify` GO or NO_GO.
- Do not add another bounded result writer.
- Do not edit product code, tests, or OpenSpec planning.
- Do not probe production, Kubernetes, or secrets.
- Do not stage, commit, or push.
- Do not invent `/opsx-security-fix`.
