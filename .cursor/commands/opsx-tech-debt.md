---
component_id: opsx.commands.tech_debt
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE
contract_id: OPSX-PROCESS-TECH-DEBT-V0
---

# /opsx-tech-debt — Process Tech Debt Capture (V0)

**Capability:** `PROCESS_DEBT_CAPTURE` only.  
**Out of scope:** `WORKFLOW_SELF_GUIDANCE` (OPSX does not autonomously choose workflow transitions in V0).

## Purpose

One invocation during real work → **durable process debt** in the local worktree registry plus a **bounded repair handoff** toward an existing meta consumer.

## Persistence

| Artifact | Path | Policy |
|----------|------|--------|
| Schema | `opsx_tools/registry/process_tech_debt_v0.schema.json` | meta-commit |
| Runtime registry | `opsx/runs/process-tech-debt/registry.jsonl` | `RUNS_LOCAL_DURABLE` — same worktree only; **not** in process source snapshot |
| Lock | `opsx/runs/process-tech-debt/.registry.lock` | runtime |
| Quarantine | `opsx/runs/process-tech-debt/registry.quarantine.jsonl` | recovery only |

No cross-worktree or global durability is claimed.

## Authority

| Action | Allowed |
|--------|---------|
| Append registry events | this command / `opsx-process-tech-debt.mjs` |
| Process command + tool + schema | `/opsx-meta-commit` after Owner GO |
| Unit tests | `/opsx-commit` |
| GRAPH, producers, `sources.yaml` | **not** this command — separate fix/pipeline packets |

## Modes

### REGISTER (default)

1. Collect `cited_output` (FACT lines), optional `change_id`, `execution_identity`, `revision_identity`.
2. Classify FACT vs INFERENCE; Owner suspicion alone → `PAUSED_NEEDS_EVIDENCE` or `SUSPECTED`, not auto HIGH defect.
3. Dedupe by **root fingerprint** (excludes `change_id`); new change → **occurrence** on same `debt_id`.
4. If registry has **corrupt lines** → `REGISTRY_PAUSED` with recovery contract (**no silent delete**).
5. Emit handoff packet; validate consumer contract:
   - `READ_ONLY_ANALYSIS` vs `APPLY_AUTHORIZED` are distinct.
   - `EXECUTABLE_READY` only when required fields for the **actual** mode are present.
   - Missing APPLY evidence → `PAUSED_CONSUMER_GAP` + recovery contract.

### READ

Load `debt_id` from registry without chat history (same worktree).

### CLOSE (Owner / verifier only)

`CLOSED_VERIFIED` requires provenance, contract/revision identity, and regression results.  
`independent: true` alone is insufficient. Self-report `FIXED` and stale evidence are rejected.

### RECOVER

`OWNER_RECOVER_REGISTRY` moves corrupt lines to quarantine, then compact valid events.

## CLI (hermetic)

```bash
node opsx_tools/opsx-process-tech-debt.mjs register --json '{...}'
node opsx_tools/opsx-process-tech-debt.mjs read --debt-id ptd-...
node opsx_tools/opsx-process-tech-debt.mjs recover
```

## Output packet (conceptual)

```text
STATUS: REGISTERED | NO_DEBT_RECORDED | PAUSED_NEEDS_EVIDENCE | REGISTRY_PAUSED
debt_id: ptd-...
handoff.read_only: EXECUTABLE_READY | PAUSED_CONSUMER_GAP
handoff.executable_apply: true | false
persistence_policy: RUNS_LOCAL_DURABLE
recovery_contract: (when paused)
```

## DO NOT

- Claim WORKFLOW_SELF_GUIDANCE or full OPSX self-guidance.
- Run product/production workflows from this command.
- Commit registry runtime files via `/opsx-meta-commit`.
