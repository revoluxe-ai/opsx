---
component_id: opsx.commands.projectize
component_version: 1.1.0-portable
runtime_class: ACTIVE
ownership: TARGET_ONLY
contract_id: OPSX-PORTABLE-PROJECTIZE-01
canonical_source_body: none
required_adapters: ["opsx-lite"]
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
---

# /opsx-projectize

Projectize the installed OPSX control plane for the **current consumer repository**, using only verified project facts and explicit Owner decisions.

**Scope:** OPSX bootstrap/projectization only. This command does not create a product change, draft or edit an Owner Contract, choose tariffs, write product code, or authorize APPLY. It must never be proposed merely because a user answered a clarification, completed `/opsx-explore`, or wants to update an existing change.

## Routing: when to use this command

- Use `/opsx-projectize` only on an **explicit request to set up/projectize OPSX**, or when a verified current projectization gate reports that setup is required.
- For an existing product change or `owner-contract.md`, continue the existing authorized change workflow (normally `/opsx-continue`). Do **not** rerun projectization.
- To begin a new product change, use `/opsx-new`, not projectization. Implementation needs its own approved apply authority.
- For installation health use `opsx doctor`; for quality-gate/toolchain adoption use `/opsx-adopt`. Do not conflate these with projectization.
- Merely missing a conventional setup file is **not** proof that projectization is required; first determine the applicable current contract and machine-readable gate.

## Canonical inputs (read-only until explicit GO)

1. Current Git root and observed repository structure.
2. OPSX installation state (`opsx/install.json`, `opsx/core`) when present.
3. Project-owned OPSX capabilities (`opsx/project/capabilities.yaml`) and projectization state, **if present**.
4. Project declarations (`.project/PROJECT_INTENT.md`, `.project/PROJECT_PROFILE.yaml`, `.project/AI_PROJECT_CONTEXT.md`) **only when present** and established as project-owned truth; their absence is not permission to create substitutes or assume a greenfield project.
5. Applicable TARGET contracts and observed runtime/adapter configuration.

Do not infer product framework, technology stack, database, CI, deployment system, product requirements or authority from the OPSX donor or from this command. Treat missing/unresolved capabilities as unresolved, not as defaults. Existing product declarations must not be overwritten by OPSX setup.

## Default READ_ONLY mode

1. Determine whether projectization is actually required; if not, report `NOT_REQUIRED` and the appropriate normal workflow. STOP without writing.
2. If required, inspect inputs and separate FACT, OWNER_INPUT, INFERENCE, PROPOSAL and DECISION.
3. Identify the smallest missing capabilities/adapters and any real conflicts with installed OPSX contracts.
4. Propose a bounded plan and affected OPSX/process files. Do not write, initialize a change, install dependencies or change product code.
5. Where authority or project semantics cannot be resolved, stop with `OWNER_DECISION_REQUIRED` or `FAIL_CLOSED`; never report `COMPLETE` by inference.

## Explicit GO mode

Only an explicit `/opsx-projectize:GO` for the named projectization scope authorizes changes **to OPSX/projectization/process realization surfaces**. It does not authorize source/test changes, OpenSpec product artifacts, infrastructure deployment, product migrations, Git commit/push/PR, or changes to Owner Contract.

Set `COMPLETE` / enable normal mutation-capable commands only when the **applicable verified projectization contract** requires it and machine-checkable checks show:
- inputs and revision identity are current;
- selected adapter/capability configuration matches the actual consumer project;
- command contracts contain no foreign product assumptions;
- installed command activation identity matches the bytes and target paths;
- authority/write boundaries and projectization acceptance checks pass.

A `project-generic` adapter may leave product capabilities explicitly unresolved while generic operations remain usable, but unresolved product-specific operations must fail closed with `TARGET_CAPABILITY_UNRESOLVED`. Do not extrapolate this into blanket authority to execute all commands.

## Hard invariants

- Project-specific truth is owned by the **consumer**, never transplanted from the donor.
- No silent conversion of an unresolved field into an Owner decision.
- No writes in READ_ONLY; no product code, tests, product change artifacts, secrets, deployments or unrelated settings in GO.
- No claim of `COMPLETE` without identity/freshness-bound machine evidence.
- No dependency installation, commit, push, pull request, merge or deploy.
- Do not trigger this command as a prerequisite for ordinary `/opsx-new` / `/opsx-continue` work without an actual projectization gate.

## Output contract

STATUS: NOT_REQUIRED | PROJECTIZATION_PLAN | COMPLETE | FAIL_CLOSED | OWNER_DECISION_REQUIRED
PROJECT_ROOT:
PROJECTIZATION_GATE_EVIDENCE:
FACTS:
UNRESOLVED:
PROPOSED_ADAPTERS:
PROPOSED_CAPABILITIES:
FOREIGN_ASSUMPTIONS_FOUND:
CHANGES_MADE:
EVIDENCE:
OWNER_DECISION_REQUIRED:
NEXT_ACTION:
