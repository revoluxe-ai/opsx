---
component_id: opsx.commands.meta_discover
component_version: 0.2.0
ownership: TARGET_ONLY
runtime_class: ACTIVE
canonical_source_body: none
required_adapters: ["opsx-lite"]
local_modification_note: "Bounded interesting-command recommendations; SURFACE STRUCTURE_CHANGED layout drift"
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-META-DISCOVER-01
newline_normalization: none
---

# PROJECT PROJECTIZATION GATE

Before any command-specific action, resolve adoption through:

  opsx/project/capabilities.yaml

If adoption is incomplete (`NEEDS_ADOPTION` or projectization incomplete), stop without
command-specific writes and emit OPSX_PROJECTIZATION_REQUIRED with NEXT_ACTION from the
project profile (`/opsx-adopt` or documented projectize flow).

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


# /opsx-meta-discover

READ_ONLY: what may help **this** repository from the central catalog index.

```text
IMPLEMENTATION_STATUS: IMPLEMENTED
RUNTIME_STATUS: ACTIVE
CONTRACT_ID: OPSX-WB-META-DISCOVER-01
WRITES: none (optional .tmp packet only)
```

## Steps

1. Require fresh-enough index (`INDEX_STATUS`). If stale and no candidates →
   `BLOCKED_NEEDS_FRESH_INDEX` → `/opsx-meta-update-index`.
2. Build cheap local profile: local `opsx-*.md` stems, lock rows, adapter ids.
3. Read `catalog/by-command/**` / `by-repo/**` (not full snapshot trees).
4. Exclude identical local hashes, deprecated/rejected, hard-incompatible adapters.
5. Rank absent / divergent / tagged matches; deep-read only top ≤ 3.
6. If recent catalog discoveries include `STRUCTURE_CHANGED` for a source,
   surface it as a study cue (added/removed paths may hide useful commands or
   unexpected removals). Do not auto-import from layout drift alone.
7. Emit recommendations; never import.

Schema: `opsx/shared/command-discovery.md` (`STRUCTURE_HASH` /
`STRUCTURE_CHANGED`).

## Owner actions per item

`IGNORE` | `WATCH` (future) | handoff:

```text
/opsx-meta-import-command OCCURRENCE=<id>
```

## Output

```text
STATUS: RECOMMENDATIONS_READY | NONE | BLOCKED_NEEDS_FRESH_INDEX | CENTRAL_UNAVAILABLE
RECOMMENDED_IMPORTS:
- OCCURRENCE_ID
  WHY_FOR_THIS_REPO
  SOURCE_REPO_BRANCH
  LOCAL_STATUS
  EXPECTED_MODE
  CONFIDENCE
STRUCTURE_SIGNALS: (optional STRUCTURE_CHANGED study cues)
NEXT_ACTION:
ORIENTATION_HINT:
  LANE: META
  NEXT_META: /opsx-meta
  NEXT_ACTION:
```
