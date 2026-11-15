# Owner vs agent execution boundary (OPSX)

**Contract:** `OPSX-OWNER-AGENT-EXECUTION-BOUNDARY-V1`  
**Scope:** All `/opsx-*` and `/tempo-*` command bodies and orchestrators.

## Owner interface

Owner interacts with OPSX through **Cursor chat only**:

- slash commands (`/opsx-…`, `/tempo-…`);
- short authorization tokens when a command requires them (`GO`, material semantic choices);
- answers to **one** narrow question when a command is blocked.

Owner does **not** run repository or pipeline tooling in a separate terminal as part of the normal contract.

## Agent interface

The **agent in the same chat turn** (or an orchestrator’s Task child with the named primary command) MUST:

- run `python`, `git` (read and commit when the named command allows), shell, Docker, Temporal operator, and bounded-verify CLIs **when the active command contract requires it**;
- complete multi-step hops (plan → execute, tempo `runtime.py run`, supervisor dispatch) **without** asking Owner to paste follow-up slash commands for each hop;
- treat `NEXT_ACTION: /opsx-…` in reports as **agent dispatch labels**, not Owner homework.

## Forbidden Owner handoffs (process defect class)

Command bodies MUST NOT treat these as the default end state:

- «Run in terminal: `python tools/…`»
- «Reply: `/opsx-commit-all GO`» when Owner already sent `GO` on the orchestrator
- «Reply: AUTHORIZE_COMMAND_FIX» when Owner already asked to fix in the same message as `/opsx-meta-fix-command-defects`
- `OWNER_RUN_COMMAND` blocks unless Owner explicitly asked for copy-paste / exact CLI for **their** machine (e.g. push from local laptop after Cloud Agent cannot auth)

## Allowed Owner-only actions (outside agent VM)

- GitHub auth / push from Owner credentials when the Cloud Agent token is invalid
- Production deploy, secrets, Kubernetes (explicitly banned from agents anyway)
- Browser steps marked **Owner-only** (e.g. game Chrome focus gate before smoke)

## Orchestrator rule

When Owner sends **`GO`** on an orchestrator that owns child GO (e.g. `/opsx-commit-all GO`, `/opsx-supervisor` continuation):

- the orchestrator MUST complete the authorized child work in **that turn**;
- MUST NOT stop after PLAN_ONLY and ask Owner to re-invoke the same slash with `GO`.

## Reporting

Terminal blocks use `STATUS:` / `OUTCOME:` / `ONE_NEXT_ACTION:` for Owner **decisions**, not console recipes.  
Internal agent steps may cite paths like `tools/temporal/runtime.py` in **agent-only** sections (clearly labeled), mirroring `/tempo-*` command split.
