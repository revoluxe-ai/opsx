---
name: openspec-continue-change
description: Continue working on an OpenSpec change by creating the next artifact. Use when the user wants to progress their change, create the next artifact, or continue their workflow.
allowed-tools: Bash(opsx/tools/openspec-cli:*)
license: MIT
compatibility: Requires TARGET-local OpenSpec CLI at `opsx_tools/openspec-cli`.
metadata:
  author: openspec
  version: "1.0"
  generatedBy: "1.6.0"
---

# STROY-SYSTEM TARGET OVERRIDE - HIGHEST PRIORITY

This skill mirrors `/opsx-continue`. Product-specific realization MUST resolve
through:

  `opsx/project/capabilities.yaml`
  `opsx/shared/target-capability-resolution.md`

Any inherited donor literal (WB Sky, adv-bot, Poetry, pyproject.toml, app/**,
tests/**, npm/package.json, src/**, public/**, deploy names) is historical or
compatibility text only and is NOT execution authority.

Before any product-specific read/write/command execution, resolve the
corresponding TARGET capability. If unresolved, fail closed:

```text
STATUS: FAIL_CLOSED
REASON: TARGET_CAPABILITY_UNRESOLVED
CAPABILITY: <required capability>
NEXT_ACTION: define/discover capability in opsx/project/capabilities.yaml
```

Continue working on a change by creating the next artifact.

**Projectization gate:** Before artifact writes, read `opsx/projectization.yaml`
(same contract as `opsx-execution-preflight.md` / `opsx-source-resolution.md`).
If `status` is not `COMPLETE` or `normal_commands_enabled` is not `true`, stop
without artifact writes:

```text
STATUS: FAIL_CLOSED
REASON: OPSX_PROJECTIZATION_REQUIRED
NEXT_ACTION: /opsx-projectize
```

Authority is **only** `opsx/projectization.yaml` — not `opsx/transfer/*` receipts.

**Engineering adoption gate:** After projectization, before artifact writes, verify
`opsx/engineering-adoption.yaml` binds a verified adoption contract. Otherwise:

```text
STATUS: FAIL_CLOSED
REASON: OPSX_ADOPTION_REQUIRED
NEXT_ACTION: /opsx-adopt
```

**OpenSpec CLI (canonical, fail-closed):** Use the TARGET-local wrapper only —
no global `openspec` on PATH, no `npx`:

```bash
opsx/tools/openspec-cli list --json
opsx/tools/openspec-cli status --change "<name>" --json
opsx/tools/openspec-cli instructions <artifact-id> --change "<name>" --json
```

If the provider is missing or wrong version, stop and report `OPENSPEC_CLI_FAIL`
(run `opsx_tools/openspec-cli-node/setup.sh` once with network during bootstrap).

**Pre-write enforcement (wired contract):** Before this skill writes artifacts,
run:

```bash
python -m tools.openspec.opsx_continue_skill_contract --preflight-before-write
```

Stop on non-zero exit and report stderr. This entrypoint is the bound execution
path (not prose-only): it runs skill/command contract checks, projectization
gate, `python tools/openspec/openspec_guard.py --scenario opsx_continue --strict --change "<name>" --guard-stage early`,
and TARGET-local CLI provider validation. Do not use a parallel guard framework.

**Source resolution:** Follow `.cursor/commands/opsx-source-resolution.md` and
`.cursor/commands/opsx-execution-preflight.md`. When the change name is known,
use `opsx_tools/openspec-cli status/instructions --change "<name>"` and
canonical change paths; do not inventory unrelated changes; do not gate on
unconditional `git status`.

**Store selection:** If the user names a store (a store is a standalone OpenSpec repo registered on this machine) or the work lives in one, run `opsx_tools/openspec-cli store list --json` to discover registered store ids, then pass `--store <id>` on the commands that read or write specs and changes (`new change`, `status`, `instructions`, `list`, `show`, `validate`, `archive`, `doctor`, `context`). Other commands do not take the flag. Hints printed by commands already carry the flag; keep it on follow-ups. Without a store, commands act on the nearest local `openspec/` root.

**Input**: Optionally specify a change name. If omitted, check if it can be inferred from conversation context. If vague or ambiguous you MUST prompt for available changes.

**Steps**

1. **If no change name provided, prompt for selection**

   Run `opsx_tools/openspec-cli list --json` to get available changes sorted by most recently modified. Then use the **AskUserQuestion tool** to let the user select which change to work on.

   Present the top 3-4 most recently modified changes as options, showing:
   - Change name
   - Schema (from `schema` field if present, otherwise "spec-driven")
   - Status (e.g., "0/5 tasks", "complete", "no tasks")
   - How recently it was modified (from `lastModified` field)

   Mark the most recently modified change as "(Recommended)" since it's likely what the user wants to continue.

   **IMPORTANT**: Do NOT guess or auto-select a change. Always let the user choose.

2. **Check current status**
   ```bash
   opsx/tools/openspec-cli status --change "<name>" --json
   ```
   Parse the JSON to understand current state. The response includes:
   - `schemaName`: The workflow schema being used (e.g., "spec-driven")
   - `artifacts`: Array of artifacts with their status ("done", "ready", "blocked")
   - `isComplete`: Boolean indicating if all artifacts are complete
   - `planningHome`, `changeRoot`, `artifactPaths`, and `actionContext`: path and scope context. Use these instead of assuming repo-local paths.

2.5. **Owner Contract first (mandatory before OpenSpec schema artifacts)**

   Authority: `.cursor/pipeline/contracts/OWNER_CONTRACT_FIRST_ARTIFACT.md`

   - Resolve `<changeRoot>/owner-contract.md` from `changeRoot` in status JSON.
   - If the file **does not exist**:
     - Create **only** `owner-contract.md` in this invocation (including required
       `## Owner attention` with `OWNER_ATTENTION_STATUS` and `OA-00N` items).
     - Do **not** create `proposal`, `specs`, `design`, `tasks`, or `decisions` in the
       same invocation.
     - STOP after writing the contract; output `NEXT_ACTION`:
       `/opsx-owner-contract-review change="<name>"`.
   - If the file exists, proceed to step 3.

3. **Act based on status**:

   ---

   **If all artifacts are complete (`isComplete: true`)**:
   - Congratulate the user
   - Show final status including the schema used
   - Suggest: "All artifacts created! You can now implement this change with `/opsx:apply` or archive it with `/opsx:archive`."
   - STOP

   ---

   **If artifacts are ready to create** (status shows artifacts with `status: "ready"`):
   - Pick the FIRST artifact with `status: "ready"` from the status output
   - Get its instructions:
     ```bash
     opsx/tools/openspec-cli instructions <artifact-id> --change "<name>" --json
     ```
   - Parse the JSON. The key fields are:
     - `context`: Project background (constraints for you - do NOT include in output)
     - `rules`: Artifact-specific rules (constraints for you - do NOT include in output)
     - `template`: The structure to use for your output file
     - `instruction`: Schema-specific guidance
     - `resolvedOutputPath`: Resolved path or pattern to write the artifact
     - `dependencies`: Completed artifacts to read for context
   - **Create the artifact file**:
     - Read any completed dependency files for context
     - Use `template` as the structure - fill in its sections
     - Apply `context` and `rules` as constraints when writing - but do NOT copy them into the file
     - Write to the `resolvedOutputPath` specified in instructions. If it is a glob pattern, choose the concrete file path using the schema instruction and the change's context
   - Show what was created and what's now unlocked
   - STOP after creating ONE artifact

   ---

   **If no artifacts are ready (all blocked)**:
   - This shouldn't happen with a valid schema
   - Show status and suggest checking for issues

4. **After creating an artifact, show progress**
   ```bash
   opsx/tools/openspec-cli status --change "<name>"
   ```

**Output**

After each invocation, show:
- Which artifact was created
- Schema workflow being used
- Current progress (N/M complete)
- What artifacts are now unlocked
- Prompt: "Run `/opsx-continue` to create the next artifact"

**Artifact Creation Guidelines**

The artifact types and their purpose depend on the schema. Use the `instruction` field from the instructions output to understand what to create.

Common artifact patterns:

**spec-driven schema** (proposal → specs → design → tasks):
- **proposal.md**: Ask user about the change if not clear. Fill in Why, What Changes, Capabilities, Impact.
  - The Capabilities section is critical - each capability listed will need a spec file.
- **specs/<capability>/spec.md**: Create one spec per capability listed in the proposal's Capabilities section (use the capability name, not the change name).
- **design.md**: Document technical decisions, architecture, and implementation
  approach **only after** `DESIGN_READINESS` passes (see **Design Generation**
  below). Resolve SoT/ownership/write-path/recovery before READY; never defer
  those to apply. When the Change has temporal/stateful behavior, also emit a
  **Derived Lifecycle** section (see below) — planning representation only,
  not a new Owner authority. Preserve upstream MUST / MUST NOT / MAY.
- **tasks.md**: Break down implementation into checkboxed tasks **only after**
  `TASK_GENERATION_READINESS` passes (see **Task Generation** below).
  `tasks.md` implements already-resolved design — it MUST NOT complete
  unresolved architecture/SoT/ownership/write-path/semantic choices.
- After a Derived Lifecycle exists in design, each **specs/<capability>/spec.md**
  MUST cover every materially observable lifecycle transition with a requirement/
  scenario, or record an explicit proof that a separate scenario is not needed.
  Spec MUST NOT invent behavior absent from lifecycle / reviewed Owner Contract.

For other schemas, follow the `instruction` field from the CLI output.

### Design Generation (design.md — shared with `/opsx-ff`)

Follow `.cursor/pipeline/contracts/DESIGN_GENERATION.md` (SoT). Same rules as
command `/opsx-continue` / `/opsx-ff`:

- Build draft design, then run `DESIGN_READINESS` **after** draft
  (post-generation `NORMATIVE_PRESERVATION_CHECK`). On
  `TECHNICAL_DESIGN_UNRESOLVED` / `UPSTREAM_NORMATIVE_CONFLICT` /
  `NORMATIVE_MODALITY_DRIFT` → `DESIGN_STATUS: BLOCKED`, do not mark design
  ready.
- READY requires `NORMATIVE_MODALITY_PRESERVED: yes`.
- Resolve technical SoT/ownership/write/recovery from evidence + decisions
  (WHAT/WHO/WHERE/WHO-writes/HOW-recover + rationale). Forbidden: apply-time
  architecture / «выбрать в apply».
- Preserve `MUST` / `MUST NOT` / `MAY` (no weakening).
- Open Questions: harmless naming only after authority is fixed.
- continue ≡ ff for identical upstream context.

### Task Generation (tasks.md — shared with `/opsx-ff`)

Follow `.cursor/pipeline/contracts/TASK_GENERATION.md` (SoT). Same rules as
command `/opsx-continue` / `/opsx-ff`:

- Build draft tasks, then run readiness **after** draft (post-generation
  `NORMATIVE_PRESERVATION_CHECK`). On
  `TECHNICAL_DESIGN_UNRESOLVED` / `UPSTREAM_NORMATIVE_CONFLICT` /
  `NON_IMPL_TASK_IN_TRACK` / `NORMATIVE_MODALITY_DRIFT` /
  `CONDITIONAL_BRANCH_COLLAPSE` /
  `SCENARIO_PLAN_EVIDENCE_MISSING` / `SCENARIO_PLAN_EVIDENCE_STALE` /
  `SCENARIO_PLAN_EVIDENCE_INTEGRITY_FAIL` →
  `TASKS_STATUS: BLOCKED`, `FILES_CHANGED: none`.
- **IMPLEMENTATION_TRACK** (§0): every `- [ ]` closable by apply; Owner/ops/live
  → post-impl section without checkboxes.
- READY requires `NORMATIVE_MODALITY_PRESERVED: yes`,
  `CONDITIONAL_BRANCHES_PRESERVED: yes`, and `IMPLEMENTATION_TRACK_ONLY: yes`.
- When scenario coverage is required: `SCENARIO_REQUIREMENTS_CONSUMED: yes`
  only with CURRENT durable `scenario-coverage/plan.receipt.json` and
  matching `SCENARIO_PLAN_EVIDENCE_ID` (never chat-only). Else BLOCKED and
  `NEXT: /opsx-spec-scenario-coverage mode=plan`.
- Evidence roles: `LEGACY_BASELINE` | `TARGET_REGRESSION` | `FINAL_ACCEPTANCE`.
- Preserve `MUST` / `MUST NOT` / `MAY` (no weakening); preserve conditional
  branches (no collapse to MAY); epoch only after actual start when applicable.
- continue ≡ ff for identical upstream artifacts.

### Derived Lifecycle (design — temporal / stateful Changes)

Authority model:

```text
owner-contract.md (+ owner-contract-review PASS) = semantic authority
design Lifecycle                               = DERIVED planning representation
continue / ff                                  = no product authority
```

When creating `design.md` for a Change with temporal/stateful behavior:

1. Derive a minimal ordered Lifecycle (`L1..Ln`) from, in order:
   - reviewed `openspec/changes/<change>/owner-contract.md`;
   - accepted/proposed decisions for this change;
   - repository facts;
   - already-created planning artifacts (proposal/specs/decisions).
2. Step count is **not** fixed to 3–7 — use the shortest sequence sufficient
   for this Change. Non-temporal / simple Changes: do **not** invent a
   ceremonial lifecycle.
3. Each step MUST carry local provenance, e.g.:
   `SOURCE: owner-contract.md / decision D-003 / repository fact`.
4. Record status:
   - `LIFECYCLE_STATUS: DERIVED` when every material transition is uniquely
     determined by reviewed Owner semantics (do **not** ask Owner to
     re-confirm via `CONFIRM_LIFECYCLE` / parallel Owner gate).
5. If after A the Owner Contract leaves B|C unresolved → **stop** writing
   further planning that depends on that fork:

```text
LIFECYCLE_STATUS: UNRESOLVED
UNRESOLVED_TRANSITION: <exact fork>
SOURCE_GAP: <what Owner Contract / decision lacks>
NEXT_ACTION: /opsx-owner-contract-review change=<change>
```

Do **not** invent semantic transitions. Do **not** create `CONFIRM_LIFECYCLE`
or a second Owner confirmation contour inside continue.
`DERIVED` may restructure already-authorized semantics in time; it MUST NOT
add product semantics.

**Guardrails**
- **Owner Contract precedes schema artifacts:** never write `proposal`+ when
  `owner-contract.md` is missing (see step 2.5 and `OWNER_CONTRACT_FIRST_ARTIFACT.md`).
- Create ONE artifact per invocation
- Always read dependency artifacts before creating a new one
- Never skip artifacts or create out of order
- If context is unclear, ask the user before creating
- Verify the artifact file exists after writing before marking progress
- Use the schema's artifact sequence, don't assume specific artifact names
- For temporal Changes: design MUST include Derived Lifecycle with provenance;
  unresolved forks → owner-contract-review (not `CONFIRM_LIFECYCLE`)
- Before writing/completing `design.md`: follow
  `.cursor/pipeline/contracts/DESIGN_GENERATION.md`
  (draft → post-generation check; `NORMATIVE_MODALITY_PRESERVED` required)
- Before writing `tasks.md`: follow `.cursor/pipeline/contracts/TASK_GENERATION.md`
  (draft → post-generation check; modality + conditional branches required)
- **IMPORTANT**: `context` and `rules` are constraints for YOU, not content for the file
  - Do NOT copy `<context>`, `<rules>`, `<project_context>` blocks into the artifact
  - These guide what you write, but should never appear in the output
