---
component_id: opsx.commands.sherlock
component_version: 0.2.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-sherlock.md
required_adapters: ["opsx-lite", "project-generic"]
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-SHERLOCK-01
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


# /opsx-sherlock

## Purpose

Evidence-driven forensic investigation of an observed failure, anomalous
state, or unexplained execution result.

It answers one question:

```text
Given the observed symptom and admissible evidence, what is known, what is
still a hypothesis, and is the evidence sufficient to name a cause?
```

Core invariant:

```text
NO EVIDENCE -> NO ROOT-CAUSE CLAIM
```

Sherlock is an investigation command. It does not repair code, classify a bug
into Route A-D, own test success, or read production systems.

```text
IMPLEMENTATION_STATUS: IMPLEMENTED_UNVERIFIED
RUNTIME_STATUS: ACTIVE_WITH_LIMITS
CONTRACT_ID: OPSX-WB-SHERLOCK-01
PRODUCT_WRITER: NO
OPENSPEC_WRITER: NO
BUG_CASE_WRITER: NO
CLASSIFICATION_AUTHORITY: NO
TEST_VERDICT_AUTHORITY: NO
PRODUCTION_READ_AUTHORITY: NO
COMMIT_ALLOWED: NO
```

`IMPLEMENTED_UNVERIFIED` remains mandatory until a real invocation of this
command, including durable case creation or continuation, is independently
observed. Static checks and helper tests do not prove the end-to-end command.

## Neighbor authority

```text
/opsx-explore
  Open-ended product, architecture, or policy exploration. It may frame Owner
  choices but does not own Sherlock's forensic ledger.

/opsx-sherlock
  Resolves a FACT gap through cited evidence and competing hypotheses. It ends
  as CAUSE_SUPPORTED, EVIDENCE_REQUIRED, or INCONCLUSIVE.

/opsx-classify-bug
  Sole Route A-D/spec-vs-implementation classifier after facts are sufficient.
  Sherlock never writes ROUTE_STATE or ROUTE-TRACE.

/opsx-bug-team
  Owns the full durable bug lifecycle and worker orchestration. A linked
  Sherlock case is read-only correlation; Sherlock never writes below
  opsx/runs/bug-cases/.

/opsx-devops
  Owns its declared Kubernetes/manifest investigation surface and every
  cluster mutation. Sherlock may request a bounded INVESTIGATE hop but never
  runs kubectl or production queries itself.
```

Use Sherlock when classification or bug-team work is blocked on an unknown
fact, or when a CI/migration/state/concurrency symptom has no established
cause. Use `/opsx-explore` for a missing policy decision, `/opsx-classify-bug`
when the facts are already known, and `/opsx-fix-bug` when only repair remains.

## User input

```text
$ARGUMENTS
```

Supported forms:

```text
/opsx-sherlock <verbatim symptom>
/opsx-sherlock case=<BUGCASE-ID> finding=<FINDING-ID> <verbatim symptom>
/opsx-sherlock sherlock_case=<SHERLOCK-ID> mode=continue evidence="<text>" evidence_source="<source>" observed_at="<timestamp>" system_state_ref="<revision|unknown>"
```

Fields:

```text
case=                 optional read-only bug-case correlation
finding=              optional read-only finding correlation
sherlock_case=        required for mode=continue
mode=continue         resume from the durable ledger
evidence=             Owner-pasted evidence, not automatically a FACT
evidence_source=      citable source of pasted evidence
observed_at=          source observation timestamp
system_state_ref=     commit/deploy/revision identity, or unknown
```

On a first invocation, a non-empty symptom is required. A
`sherlock_case=` without a resolvable exact ledger returns
`BLOCKED_NEEDS_EXACT_SOURCE`. A durable `sherlock_case=` plus a new unrelated
symptom returns `BLOCKED_AMBIGUOUS_INVOCATION`.

## Projectization gate

Before any write, read `opsx/projectization.yaml`. Unless all of these are
true, write nothing:

```text
status: COMPLETE
normal_commands_enabled: true
adapters contain opsx-lite and poetry-python
```

Failure:

```text
STATUS: FAIL_CLOSED
REASON: OPSX_PROJECTIZATION_REQUIRED
NEXT_ACTION: /opsx-projectize
```

## Safety and authority

Sherlock MUST NOT:

- edit `app/**`, tests, OpenSpec artifacts, rules, commands, or product docs;
- write below `opsx/runs/bug-cases/**` or `.cursor/devops/**`;
- choose or persist Route A-D, `CURRENT_CLASSIFICATION`, or `ROUTE_STATE`;
- claim any test, build, archive, integration, or deployment success;
- run Kubernetes, WB, HTTP, PG, ClickHouse, Redis, or other production reads;
- mutate infrastructure, deploy, restart, migrate, commit, push, merge, rebase,
  stage, stash, reset, clean, or open a pull request;
- treat its own narrative, likelihood estimate, or prior conclusion as a FACT;
- turn an uncited observation into evidence;
- request raw dumps, credentials, tokens, secrets, PII, or row contents;
- repair the symptom even when the Owner includes `GO`.

If asked to fix, stop the investigation write after recording only admissible
forensic evidence and return `/opsx-fix-bug` as a handoff. `GO` grants no
additional Sherlock authority.

## Source resolution and preflight

Follow `.cursor/commands/opsx-source-resolution.md` and
`.cursor/commands/opsx-execution-preflight.md`.

Resolve exact sources first:

1. `mode=continue`:
   `opsx/runs/sherlock/<SHERLOCK-ID>/LEDGER.jsonl`.
2. `case=` plus `finding=`:
   `opsx/runs/bug-cases/<CASE-ID>/findings/<FINDING-ID>.md`.
3. If that exact finding is missing, read only
   `opsx/runs/bug-cases/<CASE-ID>/LEDGER.md` to resolve its packet path.
4. An unlinked first invocation uses only the supplied symptom and exact
   evidence paths it names.

Do not use repo-wide discovery to rediscover a supplied id. Do not use a
subagent to simulate evidence. Bounded `rg` is allowed only for symbols or
paths named by an admissible artifact and only when the evidence question
requires it.

Git branch/status/diff are skipped by default. Run a bounded `git rev-parse`
only when a commit identity is needed as `SYSTEM_STATE_REF`; run a scoped diff
only when the hypothesis explicitly concerns current uncommitted content.
Optional git metadata never blocks ledger reconstruction.

## Runtime write boundary

Durable writes:

```text
opsx/runs/sherlock/<SHERLOCK-ID>/LEDGER.jsonl
opsx/runs/sherlock/<SHERLOCK-ID>/INVESTIGATION.md
opsx/runs/sherlock/<SHERLOCK-ID>/ACQUISITION-<n>.md
opsx/runs/sherlock/INDEX.md
```

Optional transient payload:

```text
.tmp/opsx-sherlock/<SHERLOCK-ID>/event.json
```

Nothing else is writable. `SHERLOCK-ID` is
`SHK-<UTC-YYYYMMDDTHHMMSSZ>` or
`SHK-<BUGCASE-ID>-<UTC-YYYYMMDDTHHMMSSZ>`, normalized to uppercase
letters, digits, dot, underscore, and hyphen.

`LEDGER.jsonl` is append-only. Never replace, truncate, reorder, or edit a
prior line. `INVESTIGATION.md` is a disposable projection rebuilt only from
the full ledger. `INDEX.md` is a bounded navigation projection containing at
most the 50 most recently updated Sherlock cases; it is not evidence.

## Executable ledger helper

Use:

```text
.cursor/pipeline/sherlock/sherlock_ledger.py
```

For every ledger event:

1. Create one JSON object with a file-writing tool at the transient
   `event.json` path. Do not use a heredoc or quote a nested JSON payload into
   PowerShell.
2. Append and project from repository root:

```powershell
poetry run python .cursor\pipeline\sherlock\sherlock_ledger.py append --repo-root . --sherlock-case <SHERLOCK-ID> --event-file .tmp\opsx-sherlock\<SHERLOCK-ID>\event.json
```

3. Delete the transient payload after a successful append.
4. If interrupted after append, rebuild safely:

```powershell
poetry run python .cursor\pipeline\sherlock\sherlock_ledger.py project --repo-root . --sherlock-case <SHERLOCK-ID>
```

The helper machine-checks:

- owned path confinement, JSONL sequence, event identity, and previous-event
  chain;
- first-event initialization and unique FACT/HYPOTHESIS/GAP/REQUEST ids;
- required citable FACT fields and allowed evidence kinds;
- FACT references on supported/refuted hypotheses;
- least-privilege acquisition modes;
- stale system-state mismatch not being recorded as fulfilled;
- exactly one supported hypothesis, no open siblings, and at least one cited
  FACT before a root-cause event;
- projection reconstruction from the complete ledger.

The helper does **not** prove that an external statement is true, that a cited
path contains the claimed fact, or that evidence logically discriminates
between hypotheses. The investigating agent must inspect the cited evidence
and explain that reasoning. Do not describe those semantic judgments as
machine-enforced.

## Ledger event model

Every helper-authored line includes:

```text
SCHEMA_VERSION
EVENT_SEQUENCE
EVENT_ID
PREVIOUS_EVENT_ID
RECORDED_AT
EVENT_TYPE
```

Domain event types:

```text
INVESTIGATION_STARTED
FACT_RECORDED
HYPOTHESIS_RECORDED
HYPOTHESIS_STATUS_CHANGED
EVIDENCE_GAP_RECORDED
ACQUISITION_REQUESTED
ACQUISITION_STATUS_CHANGED
INVESTIGATION_ENDED_INCONCLUSIVE
CONCLUSION_RECORDED
```

Minimum domain fields:

```text
INVESTIGATION_STARTED:
  SHERLOCK_CASE_ID, SYMPTOM, LINKED_BUG_CASE, LINKED_FINDING,
  SYSTEM_STATE_REF

FACT_RECORDED:
  FACT_ID, STATEMENT,
  EVIDENCE_KIND=repo_path|durable_artifact|owner_external,
  EVIDENCE_REF, OBSERVED_AT, SYSTEM_STATE_REF

HYPOTHESIS_RECORDED:
  HYPOTHESIS_ID, STATEMENT

HYPOTHESIS_STATUS_CHANGED:
  HYPOTHESIS_ID,
  STATUS=SUPPORTED with SUPPORTED_BY=[FACT_ID...], or
  STATUS=REFUTED with CONTRADICTED_BY=[FACT_ID...], or
  STATUS=OUT_OF_SCOPE with SCOPE_REASON

EVIDENCE_GAP_RECORDED:
  GAP_ID, QUESTION, BLOCKS=[HYPOTHESIS_ID...]

ACQUISITION_REQUESTED:
  REQUEST_ID, EVIDENCE_GAP,
  SOURCE=repo|opsx-devops|external_owner_manual,
  REQUIRED_AUTHORITY,
  ACCESS_MODE=presence_check|count_only|normalized_summary|revision_id_only,
  REDACTION_CONSTRAINTS, REQUESTED_AT, SYSTEM_STATE_REF_AT_REQUEST

ACQUISITION_STATUS_CHANGED:
  REQUEST_ID,
  STATUS=FULFILLED|REJECTED|STALE_SUSPECTED,
  SYSTEM_STATE_REF_RETURNED,
  EVIDENCE_FACT_IDS for FULFILLED, or REASON otherwise

INVESTIGATION_ENDED_INCONCLUSIVE:
  REASON

CONCLUSION_RECORDED:
  ROOT_CAUSE, SUPPORTED_BY=[FACT_ID..., HYPOTHESIS_ID]
```

The latest status transition for a hypothesis is its current state; historical
events remain unchanged. A conclusion or inconclusive event is terminal.

## Evidence admissibility

A FACT requires a re-checkable reference:

- an exact repository path and line/range;
- a durable artifact produced by an authorized command;
- a focused CI/test log with stable location;
- Owner-pasted external evidence with explicit source, observation timestamp,
  and system-state reference.

An Owner paste lacking source or timestamp remains unadmitted evidence. Record
an `EVIDENCE_GAP`, not a FACT.

A hypothesis is `SUPPORTED` only when at least one cited FACT discriminates
for it rather than being equally compatible with every still-open sibling.
It is `REFUTED` only when a cited FACT is logically incompatible with it.
`OUT_OF_SCOPE` is not a bug classification; it only removes a hypothesis from
this investigation with an explicit reason.

Status rules:

```text
CAUSE_SUPPORTED
  Exactly one hypothesis is SUPPORTED; every sibling is REFUTED with FACT
  evidence or OUT_OF_SCOPE with reason; the conclusion cites a FACT and the
  supported hypothesis.

EVIDENCE_REQUIRED
  At least one live hypothesis lacks discriminating evidence and a concrete
  evidence gap can be named.

INCONCLUSIVE
  At least two hypotheses remain evidence-supported and compatible, every
  feasible acquisition is exhausted/rejected/declined, and no pending
  request remains.
```

Plausibility, majority, or "best current guess" is never sufficient for
`CAUSE_SUPPORTED`.

## Constrained acquisition

Sherlock first uses bounded repository evidence already within its read
authority. If the missing fact is external, it records one minimal request:

### `/opsx-devops` handoff

Use only when the evidence falls inside the target command's declared
Kubernetes/manifest read surface:

```text
SOURCE: opsx-devops
REQUIRED_AUTHORITY: opsx-devops
ACCESS_MODE: presence_check|count_only|normalized_summary|revision_id_only
PASTEABLE_NEXT:
/opsx-devops INVESTIGATE ns=<namespace> focus=<pods|logs|events|resources|alerts|manifests> symptom="<exact evidence question; no secrets or raw dump>"
```

Sherlock emits that line but does not invoke it and never runs `kubectl`.
Do not invent SOURCE-only `host=`, Compose, browser, or generic production DB
capabilities absent from target `/opsx-devops`.

### Owner-manual handoff

For evidence outside declared OPSX read authority:

```text
SOURCE: external_owner_manual
REQUIRED_AUTHORITY: owner_manual
ACCESS_MODE: <least invasive allowlisted mode>
PASTEABLE_NEXT: <one exact read-only request>
```

Examples: table presence only, row count only, migration revision id only, or
job status only. Never ask for row contents, schema dumps, credentials, PII,
or secrets.

If returned material appears to contain secrets, credentials, or PII, do not
quote or persist it as a FACT. Record the acquisition as `REJECTED`, keep the
gap open, and ask for a redacted minimal replacement.

Returned evidence is usable only when
`SYSTEM_STATE_REF_RETURNED == SYSTEM_STATE_REF_AT_REQUEST` and both are known.
Otherwise record `STALE_SUSPECTED`; do not name a cause until reconfirmed.

## First invocation algorithm

1. Re-anchor to the current symptom and apply the projectization gate.
2. Resolve only exact linked finding/evidence paths.
3. Generate a unique Sherlock id and append `INVESTIGATION_STARTED`.
4. Record each observed item separately as `FACT_RECORDED`; leave inference
   out of FACT statements.
5. Record at least two materially competing hypotheses when the evidence
   permits alternatives. Do not manufacture a sibling merely to satisfy a
   count.
6. Evaluate each hypothesis against the accumulated FACT set.
7. If evidence does not discriminate, record a gap and the least-privilege
   acquisition request; status remains `EVIDENCE_REQUIRED`.
8. Append a conclusion only when the cause gate is satisfied. Otherwise finish
   as `EVIDENCE_REQUIRED` or, after acquisition exhaustion, `INCONCLUSIVE`.
9. Rebuild the projection and update the bounded `INDEX.md` row from projected
   fields only.
10. Return the required output and stop. Do not repair or classify.

## `mode=continue`

1. Read and validate the exact ledger in full:

```powershell
poetry run python .cursor\pipeline\sherlock\sherlock_ledger.py validate --repo-root . --sherlock-case <SHERLOCK-ID>
```

2. Reconstruct all facts, hypotheses, requests, and current statuses from the
   ledger. Never trust a chat summary over it.
3. Treat pasted evidence as a candidate only. Require its source, timestamp,
   and system-state reference; reject unsafe content.
4. Append new FACT events before any status transition that cites them.
5. Re-evaluate every non-terminal hypothesis. If new evidence disproves a
   previously supported hypothesis, append a new `REFUTED` transition; never
   edit its earlier event.
6. Apply the cause/evidence-required/inconclusive gate and rebuild projection.

Terminal investigations do not accept more events. A materially new symptom
or post-terminal contradiction starts a new Sherlock case linked to the old
case; it does not rewrite forensic history.

## Required output

Lead with the status and whether a cause is established:

```text
SHERLOCK_CASE_ID:
STATUS: CAUSE_SUPPORTED | EVIDENCE_REQUIRED | INCONCLUSIVE
ROOT_CAUSE_ESTABLISHED: yes | no

SYMPTOM:
LINKED_BUG_CASE:
LINKED_FINDING:
SYSTEM_STATE_REF:

FACTS:
- <FACT-ID>: <statement> (evidence: <ref>)

HYPOTHESES:
- <HYPOTHESIS-ID> [OPEN|SUPPORTED|REFUTED|OUT_OF_SCOPE]: <statement>
  supported_by: [...]
  contradicted_by: [...]

EVIDENCE_GAPS:
- <GAP-ID>: <question> -- blocks <HYPOTHESIS-ID>

ACQUISITION_REQUEST:
- source: repo | opsx-devops | external_owner_manual
- access_mode: presence_check | count_only | normalized_summary | revision_id_only
- redaction_constraints:
- pasteable_next:

CONCLUSION:
- root_cause: <only for CAUSE_SUPPORTED; otherwise not established>
- supported_by: [...]

LEDGER_PATH:
INVESTIGATION_PATH:
HELPER_VALIDATION: valid | blocked
EXECUTION_PREFLIGHT:
- git_branch:
- git_status:
- other_ops:
- blocked_on_preflight:

NEXT_ACTION:
DO_NOT_DO_YET:
- do not repair code
- do not classify Route A-D
- do not claim test or deployment success
```

Next action by status:

```text
CAUSE_SUPPORTED:
  If linked to a finding, return to:
  /opsx-classify-bug case=<actual-case-id> finding=<actual-finding-id>
  Otherwise return the cited conclusion to the invoking workflow.

EVIDENCE_REQUIRED:
  Run exactly the emitted acquisition action, then:
  /opsx-sherlock sherlock_case=<actual-id> mode=continue evidence="<redacted evidence>" evidence_source="<source>" observed_at="<timestamp>" system_state_ref="<matching revision>"

INCONCLUSIVE:
  Return the residual hypotheses to the invoking /opsx-bug-team or Owner.
  Use /opsx-explore only when the remaining question is a policy choice rather
  than a factual acquisition.
```

Never emit an invented placeholder as the primary pasteable action. Use actual
ids when known; otherwise state the exact missing field.

## Acceptance scenarios

1. No citable evidence: `EVIDENCE_REQUIRED`; no root cause.
2. Two hypotheses remain equally supported after acquisition exhaustion:
   `INCONCLUSIVE`; no arbitrary winner.
3. One hypothesis is supported and every sibling is factually refuted or
   explicitly removed from this investigation: `CAUSE_SUPPORTED` with ids.
4. Required fact exists only in production: no direct access; one bounded
   `/opsx-devops` or Owner-manual acquisition request.
5. "Just fix it": no repair; handoff to `/opsx-fix-bug`.
6. New evidence refutes the leading hypothesis: append a transition; preserve
   all prior ledger bytes.
7. Unsafe or stale returned evidence: rejected/stale request, open gap, no
   cause.
8. Linked bug case: read-only correlation; no bug-case artifact changed.
9. Interrupted projection: ledger validates and projection can be rebuilt.
10. No classification, production read, repair, or success-verdict authority
    is acquired through `GO`.

## Verification scope

Focused helper and contract checks may prove append semantics, structural
cause gating, path confinement, acquisition allowlists, registry wiring, and
projection regeneration. They do not prove a real Cursor command invocation,
the truth of future evidence, or the correctness of an investigation's
semantic reasoning. Until such an invocation is observed, report:

```text
IMPLEMENTED_UNVERIFIED
```
