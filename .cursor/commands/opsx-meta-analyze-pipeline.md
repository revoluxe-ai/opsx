---
component_id: opsx.commands.meta_analyze_pipeline
component_version: 0.5.0
ownership: TARGET_ONLY
runtime_class: ACTIVE
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-meta-analyze-pipeline.md
required_adapters: ["opsx-lite", "project-generic"]
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-META-ANALYZE-PIPELINE-01
---


# TARGET OVERRIDE — HIGHEST PRIORITY

This target-native command preserves the SOURCE name and user interface while
using WB's existing OPSX authorities. It is always read-only.

Authority:

- `.cursor/pipeline/GRAPH.md` and `.cursor/pipeline/META.md` are the durable
  operator map and remain writable only through `/opsx-pipeline UPDATE GO`.
- `opsx/shared/meta-orientation.md` owns create-versus-fix routing.
- `.cursor/pipeline/contracts/PRODUCT_ACCEPTANCE.md` owns product DONE meaning.
- `.cursor/commands/opsx-source-resolution.md` and
  `.cursor/commands/opsx-execution-preflight.md` own bounded evidence lookup.
- Target commands use `opsx-lite` and `poetry-python`; Vue/npm, browser,
  production, deploy, commit, and product-code execution are out of scope.

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


# /opsx-meta-analyze-pipeline

Analyze OPSX process design for human clarity, safety, ownership, and
predictability. Do not edit process or product files.

## User input

```text
$ARGUMENTS
```

Accepted optional forms:

- `EXAMPLE_CHAT:<id>`
- `CHAT_ID:<id>` (alias)
- one unambiguous UUID-looking transcript id
- free text describing the analysis focus

Empty input is valid. Resolve a supplied id only in the host-provided
agent-transcripts directory for this workspace. If it cannot be read, return
`STATUS: BLOCKED` and `REASON: EXAMPLE_CHAT_UNREADABLE`; do not search for a
different transcript store.

## Topic-intent gate

Resolve the current request before reading prior chat evidence:

```text
TOPIC_INTENT: PRODUCT_BRIEF | PROCESS_PIPELINE | AMBIGUOUS
```

- `PRODUCT_BRIEF`: return `WRONG_SURFACE` and route to `/opsx-evolution` or
  `/opsx-explore`; do not reframe it as process learning.
- `AMBIGUOUS`: ask one narrow product-versus-process question and stop.
- `PROCESS_PIPELINE`: continue below.

## Modes

### PIPELINE_ANALYSIS

Build an ephemeral `CURRENT_GRAPH` from command inventory, bounded command
reads, durable GRAPH/META when relevant, and optional transcript evidence.
For each evidenced command identify: purpose, input/output state, artifacts,
owner, must, must-not, and next step.

### IMPROVEMENT_PROPOSAL

Describe current behavior, defect, human impact, proposed behavior, risks,
and exactly one remedy class per option:

- `LOCAL_SYMPTOM`
- `CLASS_KERNEL`
- `AUTHORITY_ALREADY_EXISTS`

When an existing durable authority covers the class, prefer extending or
enforcing it over inventing a parallel gate.

### REGRESSION_PIPELINE_REVIEW

Check whether a process change reduced clarity, safety, recovery, or
predictability.

## Read and write boundary

Always begin with `READ_ONLY`.

Use inventory and search before bounded reads. Follow the read budgets in
`opsx-execution-preflight.md`: at most two parallel reads, section reads for
large files, and no full command-set or transcript dump. Mark unopened
evidence `NOT_OPENED_BUDGET`; never infer its contents.

Never edit files, create patches, run implementation, commit, push, deploy,
create a command identity, or apply a command-contract fix. Optional
transcript evidence is never the durable graph.

## Routing

- Existing command/contract defect:
  `/opsx-meta-fix-command-defects`
- New command design:
  `/opsx-meta-design-command`
- Unclear process ownership:
  `/opsx-meta-consult`
- Durable graph update:
  `/opsx-pipeline UPDATE`, then a separately authorized
  `/opsx-pipeline UPDATE GO`
- Transferable process lesson:
  `/opsx-meta-promote-process-lesson`

When create or fix is recommended, cite
`opsx/shared/meta-orientation.md`.

## Output contract

```text
READ_ONLY
STATUS: PIPELINE_ANALYSIS_COMPLETE | WRONG_SURFACE | BLOCKED
REASON:
MODE: PIPELINE_ANALYSIS | IMPROVEMENT_PROPOSAL | REGRESSION_PIPELINE_REVIEW
TOPIC_INTENT:
EXAMPLE_CHAT_ID:
PIPELINE_SUMMARY:
CURRENT_GRAPH:
GRAPH_DIFF_SUMMARY:
ROLE_MATRIX:
# command | does | must | must_not | next
HUMAN_USABILITY_SCORE:
PROBLEMS_FOUND:
IMPROVEMENT_OPTIONS:
# every option includes REMEDY_CLASS
RECOMMENDED_OPTION:
DURABLE_AUTHORITY_CITED:
REGRESSION_RISKS:
HANDOFF_CREATE:
HANDOFF_FIX:
HANDOFF_CONSULT:
HANDOFF_PIPELINE:
NEXT_ACTION:
```

Use `none — <reason>` for inapplicable handoffs. A handoff is advice, not an
authorization or a write.

## Self-review

- Current-message topic intent won over sticky prior context.
- Process analysis stayed separate from product work and implementation.
- No state, command behavior, or evidence was invented.
- Existing authority was preferred over duplicate soft gates.
- `ROLE_MATRIX`, typed handoffs, and one executable next action are present,
  or the exact evidence gap is reported.
