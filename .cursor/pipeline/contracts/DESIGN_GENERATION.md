# Design Generation Readiness (planning → design.md)

## Purpose

**Hard prerequisite (Owner-approved):** Architecture is a separate mandatory planning artifact before Technical Design. A READY `design.md` MUST declare `ARCHITECTURE_SHA256: <sha256 of accepted architecture.md>`; missing/mismatched digest blocks S2/tasks/apply even if the new Architecture revision is independently re-reviewed. Require `architecture.md` with `ARCHITECTURE_STATUS: READY` and independent review receipt accepted by `architecture_stage_gate.py check --root . --change <change>`. Apply also checks this gate. This precedes `DESIGN_READINESS`; the architecture author cannot self-review. See `ARCHITECTURE_STAGE_V1.md`. No design `READY` or tasks `READY` when Architecture is missing/stale, even if OpenSpec status reports the schema artifact complete. When an architecture choice arises while designing, return to Architecture review, do not choose it silently in Design.

Shared SoT for creating `design.md` from upstream planning artifacts
(owner-contract, proposal, specs, decisions) plus repository evidence.

Consumed by:

- `/opsx-continue` (when the ready artifact is `design`)
- `/opsx-ff` (when creating the `design` artifact)

Skill/prompt mirrors of those commands MUST follow this contract identically.

This contract is **not** a user-facing command. It does **not** authorize
Owner semantic decisions, product code edits, or rewriting reviewed
`owner-contract.md` semantics. It does authorize (and require) the planning
agent to translate **already reviewed architecture** into a concrete technical
implementation design. New structural choices require Architecture rereview.

## Authority invariant

```text
ARCHITECTURE MUST BE REVIEWED BEFORE TECHNICAL DESIGN
design.md = resolved technical plan for already-authorized product semantics
same upstream context → semantically equivalent design
  (/opsx-continue ≡ /opsx-ff; advancement mode differs only)
DESIGN GENERATION MUST PRESERVE UPSTREAM NORMATIVE MODALITY
MUST/MUST NOT/MAY modality preserved from authoritative upstream
```

Apply executes design. Apply MUST NOT receive tasks that choose:

- State Source of Truth
- durability boundary
- ownership
- write-path / write authority
- recovery / persistence contract

## When this gate runs

Two phases — both required before declaring
`openspec/changes/<change>/design.md` complete (before marking the design
artifact done / unlocking tasks):

```text
UPSTREAM_REQUIREMENTS
        ↓
TECHNOLOGY_ASSESSMENT  (when required — see §0; before design freeze)
        ↓
DRAFT_DESIGN  (in memory / scratch — not yet declared complete)
        ↓
NORMATIVE_PRESERVATION_CHECK  (post-generation)
        ↓
READY | BLOCKED
```

1. **Pre-draft / while drafting:** resolve technical choices; scan upstream
   for modality conflicts and implementation-critical open questions.
2. **Post-draft:** validate the **actually generated** draft design content
   for normative modality preservation.

`DESIGN_STATUS: READY` is allowed **only after** the post-generation check
passes. A clean upstream scan alone is **not** sufficient.

If either phase fails:

```text
DESIGN_STATUS: BLOCKED
FILES_CHANGED: none | partial-draft-only-if-explicitly-kept-unready
```

Do **not** treat design as ready. Downstream planning MUST NOT receive a
false «design complete». Prefer not writing a READY design file; if a draft
exists, it MUST carry `DESIGN_STATUS: BLOCKED` and MUST NOT be reported as
complete.

## DESIGN_READINESS

Emit (chat and/or durable note in the continue/ff progress output, and
prefer a short `DESIGN_READINESS` / `DESIGN_STATUS` block inside
`design.md` when READY) **after** draft construction and post-generation
validation:

```text
DESIGN_READINESS

PRODUCT_SEMANTICS_RESOLVED: yes
STATE_OWNERSHIP_RESOLVED: yes | not_applicable
DURABLE_SOT_RESOLVED: yes | not_applicable
WRITE_AUTHORITY_RESOLVED: yes | not_applicable
RECOVERY_SEMANTICS_RESOLVED: yes | not_applicable
NORMATIVE_CONFLICTS: none
NORMATIVE_MODALITY_PRESERVED: yes | no
IMPLEMENTATION_CRITICAL_OPEN_QUESTIONS: none

DESIGN_STATUS: READY | BLOCKED
REASON: <none | TECHNICAL_DESIGN_UNRESOLVED | UPSTREAM_NORMATIVE_CONFLICT |
  NORMATIVE_MODALITY_DRIFT | …>
```

Declare design complete only when:

```text
PRODUCT_SEMANTICS_RESOLVED: yes
STATE_OWNERSHIP_RESOLVED: yes | not_applicable
DURABLE_SOT_RESOLVED: yes | not_applicable
WRITE_AUTHORITY_RESOLVED: yes | not_applicable
RECOVERY_SEMANTICS_RESOLVED: yes | not_applicable
NORMATIVE_CONFLICTS: none
NORMATIVE_MODALITY_PRESERVED: yes
IMPLEMENTATION_CRITICAL_OPEN_QUESTIONS: none
DESIGN_STATUS: READY
```

Use `not_applicable` only when the Change truly has no durable/stateful
write/recovery surface (document why briefly). Durable-state Changes MUST
NOT mark SoT/ownership/write/recovery as `not_applicable`.

Forbidden false readiness: declaring `NORMATIVE_CONFLICTS: none` +
`DESIGN_STATUS: READY` and then emitting draft text that weakens upstream
`MUST` / `MUST NOT`. Preflight that ignores draft content is a contract
defect.

---

## 0. TECHNOLOGY_AWARE_CONTEXT_V0

Minimal technology assessment **before** `design.md` receives
`DESIGN_STATUS: READY` (planning freeze). Technology choices MUST NOT be
deferred to `/opsx-apply-design` or implement-time discovery.

This section does **not** add commands, JSON schemas, mandatory MCP calls, or
orchestration. It extends the existing design generator obligations shared
by `/opsx-continue` and `/opsx-ff`.

### Capability

```text
TECHNOLOGY_AWARE_CONTEXT_V0
```

Responsibilities (evidence order):

1. **Repository** — inspect current implementation and patterns (`src/**`,
   adjacent modules, accepted `openspec/decisions/**`).
2. **Lockfile** — read `pyproject.toml` / `poetry.lock` for installed deps
   relevant to the question (no installs in planning).
3. **You.com `you-search`** (optional) — find alternatives **only when**
   repository + lockfile evidence is insufficient to choose REUSE vs ADOPT
   vs BUILD. Cap: **≤ 3 searches per Change** when used. Do **not** use
   `you-discover` for library/package discovery.
4. **Context7** (optional) — document a **specific API or behavior** for an
   **ADOPT** candidate. Resolve library ID, then `query-docs`. Not required
   for REUSE of in-repo code.
5. **Verdict** — record per surfaced choice: `REUSE` | `ADOPT` | `BUILD`
   with engineering rationale (durability, idempotency, retries/recovery,
   PostgreSQL fit, ops complexity, migration cost).
6. **Sources & version confidence (per choice)** — cite URLs for external
   evidence. Each surfaced **ADOPT** (or compared external) candidate MUST
   carry its **own** `version_confidence` and `version_evidence` (e.g.
   lockfile pin, Context7 `libraryId` + doc URL, release note). Values:
   `VERSION_CONFIRMED` | `VERSION_UNCONFIRMED` | `N/A` (in-repo **REUSE**).
   Candidates MUST NOT share one blanket `VERSION_CONFIRMED`. A candidate
   with `VERSION_UNCONFIRMED` MUST NOT be described as pin-compatible with
   `poetry.lock`.
7. **Owner Contract** — external recommendations are subordinate. Conflict
   → `DESIGN_STATUS: BLOCKED` / `REASON: OWNER_CONTRACT_CONFLICT` (or
   `UPSTREAM_NORMATIVE_CONFLICT` when modality clashes).

MCP tools are **evidence only**; they do not approve dependencies or edit
the repository.

### When assessment is required

Run §0 **before** finalizing `design.md` when **any** of:

- A new **library** or **broker/infra** is considered (ADOPT path).
- Competing persistence/queue/runtime options are compared (e.g. PG queue vs
  Redis/RabbitMQ/Kafka).
- Design prose would otherwise say «evaluate at apply» or leave stack TBD.

### When assessment is **not** required (do not force MCP)

- Change **only** extends existing modules with no new stack fork and design
  explicitly **REUSE**s documented patterns (cite repo paths).
- No durable/stateful surface (`not_applicable` on SoT fields with brief why).
- Trivial naming/docs-only Changes with no implementation technology fork.

Absence of MCP MUST NOT block READY when verdict is **REUSE** or **BUILD**
supported solely by repository + lockfile evidence.

### Where to record (Slice 0)

Record outcomes in `design.md`:

- **`## Decisions`** — each technology choice with verdict
  (`REUSE`/`ADOPT`/`BUILD`), rationale, repo paths and/or source URLs, and
  for each **ADOPT** (or compared external) candidate its own
  `version_confidence` + `version_evidence` (see below).
- Optional short block (when any external evidence was used):

```text
TECHNOLOGY_ASSESSMENT
REQUIRED: yes | no
PER_CHOICE:
- <choice-id>: verdict=REUSE|ADOPT|BUILD — <one line>
  version_confidence: VERSION_CONFIRMED | VERSION_UNCONFIRMED | N/A
  version_evidence: <lockfile@pin | context7:/org/proj + url | repo path | N/A>
  evidence_kinds: repo | lockfile | you-search | context7
MCP_USED: yes | no
OWNER_CONTRACT_CONFLICTS: none | <summary>
COMPATIBILITY_GAPS: none | <choice-id>: <unresolved gap>
```

Paste optional `TECHNOLOGY_EXPLORE_NOTES` from `/opsx-explore` into design
rationale when explore already performed repository-first research.

### ADOPT gates (planning freeze)

Distinguish three layers (do **not** collapse them):

| Layer | Meaning | READY impact |
|-------|---------|----------------|
| **A. Authorization** | Owner / Owner Contract allows introducing a new dependency or infra within product scope | Contradiction with reviewed `owner-contract.md` or accepted ADRs → `OWNER_CONTRACT_CONFLICT` / BLOCKED. Ordinary pure-technical forks (e.g. PG vs Redis when WHAT unchanged) still do **not** require a new Owner gate (see §1 Owner confirmation). |
| **B. Compatibility evidence** | Technical proof the chosen ADOPT candidate matches required API/behavior and, when claimed, `poetry.lock` pins | Per-choice `version_confidence`. `VERSION_UNCONFIRMED` is **not** upgraded to `VERSION_CONFIRMED` by Owner risk language alone. |
| **C. Residual risk acceptance** | Owner documents known residual risk after B is as strong as practicable | May appear in Owner Contract or design rationale; **does not** substitute for B on a **critical** compatibility gap. |

Rules:

- **ADOPT** that would add or materially change a `pyproject.toml` dependency
  MUST be visible in `design.md` (per-choice fields above).
- **Safety-critical ADOPT** (durable boundary, auth, persistence) with
  `version_confidence: VERSION_UNCONFIRMED` for the **selected** candidate →
  `DESIGN_STATUS: BLOCKED` / `REASON: TECHNICAL_DESIGN_UNRESOLVED` until
  sufficient compatibility evidence **or** the design changes to a safe
  alternative (**REUSE**, **BUILD**, or ADOPT with `VERSION_CONFIRMED` for
  that choice) per this contract. Owner residual-risk acceptance alone does
  **not** clear a critical unresolved compatibility gap.
- Non-critical ADOPT with `VERSION_UNCONFIRMED` may reach READY only when the
  gap is explicitly listed under `COMPATIBILITY_GAPS`, scoped, and not
  safety-critical; still never claim pin-compatibility without evidence.
- If external comparison was claimed but MCP failed and repo evidence is
  insufficient → `DESIGN_STATUS: BLOCKED` /
  `REASON: TECHNICAL_DESIGN_UNRESOLVED` with `EVIDENCE_MISSING:` set (do not
  invent alternatives).

Forbidden collapses (ADOPT):

```text
Owner approves library X        ≠ compatibility evidence for X
Owner accepts residual risk     ≠ VERSION_CONFIRMED
Residual risk acceptance (C)    ≠ clears critical COMPATIBILITY_GAPS without B or safe design change
```

### Apply boundary (unchanged, reinforced)

`/opsx-apply-design` maps approved design onto the codebase. It MUST NOT
introduce a library, store, or queue technology that is not already chosen
in frozen `design.md` §Decisions / `TECHNOLOGY_ASSESSMENT`. Silent stack
changes are a contract defect.

### Architecture evidence receipt (when §0 required)

When §0 TECHNOLOGY_AWARE_CONTEXT_V0 is **required**, the Change MUST also
materialize `architecture-evidence/ARCH-EVIDENCE.json` per
`.cursor/pipeline/contracts/ARCHITECTURE_DEPENDENCY_EVIDENCE_V1.md`.
Claims MUST reference `TECHNOLOGY_ASSESSMENT` / `## Decisions` anchors.
Design MUST include `ARCHITECTURE_COVERAGE_OBLIGATIONS` for each material
library, external API, or critical architecture invariant. Validator PASS
is evidence only — it does not replace `design.md` SoT.

### S2 machine gate (design-ready)

When `design.md` carries `DESIGN_STATUS: READY`, integrated continue/ff/update
preflight (via `openspec_guard.py` + `--change`) MUST run:

```bash
python .cursor/pipeline/architecture-evidence/planning_gate.py design-freeze --change <id>
python .cursor/pipeline/architecture-evidence/planning_gate.py design-ready --change <id>
```

`openspec_guard.py` invokes `design-freeze` + `design-ready` when `--change` is set.
Draft `DESIGN_STATUS: BLOCKED` (or not READY) is not promoted. Declare
`ARCHITECTURE_AFFECTED_SURFACE` when material code integrations are in scope.

The gate performs bounded dependency/API **discovery** from Change upstream
artifacts, compares against `ARCHITECTURE_COVERAGE_OBLIGATIONS` /
`ARCHITECTURE_INTEGRATION_INVENTORY`, validates `architecture-evidence/ARCH-EVIDENCE.json`
with **mandatory** `change_id`, `design_digest`, and `inputs_digest` binding
(no optional bypass in integrated paths), and rejects URL/MCP-only compatibility
without inspected digests. `INDEPENDENT_REVIEW_AGENT` cannot establish
`GATE_ACCEPTED` in S2 (S3 dependency).

### S3 machine gate (planning-review / apply)

Before `/opsx-apply` on material Changes, run:

```bash
python .cursor/pipeline/architecture-evidence/planning_gate.py apply-preflight --change <id>
```

`openspec_guard.py --scenario opsx_apply --change <id> --strict` invokes this gate.
Trusted tool-execution receipts and `REVIEW-ATTESTATION.json` are required for
`ready_for_apply_preflight` (see `ARCHITECTURE_DEPENDENCY_EVIDENCE_V1.md` §S3).

---

## 1. TECHNICAL_CHOICE_RESOLUTION

When a technical architecture choice exists **in the mandatory Architecture stage**, for example:

- PG vs Redis (or other persistence)
- existing state vs new state
- which component owns canonical state
- which component may write
- how state is read / recovered after restart

the architecture author MUST:

0. Complete **§0 TECHNOLOGY_AWARE_CONTEXT_V0** when required (before
   declaring READY).
1. Study repository evidence (existing patterns, adjacent modules).
2. Study existing architecture decisions / patterns
   (context-first; bounded fallback — see architecture-decisions rule).
3. Choose the minimal technical option that satisfies upstream semantics.
4. Record the choice in `architecture.md`, then reference it in `design.md` (and propose a change decision artifact
   when reusable ownership/write-path rules are new).
5. Record rationale / trade-off.
6. Fix ownership and write boundary.

Record at least:

```text
WHAT is durable (if anything)
WHO owns canonical state
WHERE canonical state lives (SoT class / store)
WHO may write it
HOW it is read / recovered
```

Exact ORM field names or class names are **not** required for READY.

### Owner confirmation

If the technical choice does **not** change WHAT / SCOPE / ACCEPTANCE,
Owner confirmation is **not** required. This is planning/design-agent
responsibility.

### Forbidden READY shapes

```text
Точная схема PG/Redis — apply-time
PG vs Redis — выбрать в apply / при реализации
SoT TBD — decide during implementation
write ownership — resolve at apply
```

Forbidden combination:

```text
DESIGN_STATUS: READY
+ architecture choice delegated to apply
```

### Fail → BLOCKED when evidence is insufficient

Do **not** write «решить в apply». Instead:

```text
DESIGN_STATUS: BLOCKED
REASON: TECHNICAL_DESIGN_UNRESOLVED

UNRESOLVED:
<exact architecture choice>

EVIDENCE_MISSING:
<what prevents a safe decision>

NEXT_ACTION: gather evidence / propose decision / /opsx-update after facts exist
```

Do **not** auto-ask Owner solely for a pure technical PG/Redis fork when
WHAT/SCOPE/ACCEPTANCE are unchanged — either resolve from evidence or
BLOCK with `EVIDENCE_MISSING`.

---

## 2. NORMATIVE_MODALITY_PRESERVATION

When reflecting authoritative upstream semantics (reviewed owner-contract,
specs, accepted/proposed decisions) into design prose, preserve modality:

```text
MUST     → MUST
MUST NOT → MUST NOT
MAY      → MAY
```

Forbidden weakenings (examples):

```text
MUST     → MAY
MUST     → SHOULD
MUST NOT → SHOULD NOT
MUST NOT → MAY NOT
```

Example (regression class):

```text
upstream: our block clear AND no other blockers → Auto MUST start
design:   Activity-control MUST start Auto …
FORBIDDEN: Activity-control MAY start Auto …
```

If two **authoritative** upstream artifacts conflict on modality for the
same observable:

```text
DESIGN_STATUS: BLOCKED
REASON: UPSTREAM_NORMATIVE_CONFLICT

SOURCE_A:
<artifact + modality>

SOURCE_B:
<artifact + modality>

NEXT_ACTION: reconcile upstream planning (not silent compromise wording)
```

Do **not** model-select the more convenient semantics. If a single
authoritative source is unambiguous, design MUST match it.

### Post-generation drift (mandatory)

After draft design exists, re-check draft wording against upstream
obligations.

If draft weakens an upstream obligation (e.g. upstream `MUST start`, draft
`MAY start`):

```text
NORMATIVE_MODALITY_PRESERVED: no
DESIGN_STATUS: BLOCKED
REASON: NORMATIVE_MODALITY_DRIFT
DRIFT: <upstream obligation> → <draft wording>
FILES_CHANGED: none | partial-draft-only-if-explicitly-kept-unready
```

Do **not** declare design complete. Fix the draft (preserve modality) or
stop.

This catch is required even when the pre-draft upstream scan reported
`NORMATIVE_CONFLICTS: none` — the generator itself can introduce drift.

---

## 3. OPEN_QUESTIONS_BOUNDARY

`## Open Questions` MAY exist, but only for items that:

- are **not** required for implementation readiness;
- do **not** define state authority;
- do **not** define product semantics;
- do **not** define write / recovery contract.

### Forbidden Open Questions (implementation-critical)

```text
- где SoT?
- PG или Redis?
- кто владеет состоянием?
- где начинается epoch? (when that is product/recovery semantics)
- какой component имеет write authority?
```

Any such question → readiness FAIL (`IMPLEMENTATION_CRITICAL_OPEN_QUESTIONS`
non-empty) → `DESIGN_STATUS: BLOCKED`.

### Allowed Open Questions (harmless naming)

```text
- точное имя enum / reason string
- точное имя поля
- локальное название helper
```

only when semantics and authority are already fully determined. Such
questions alone MAY leave `DESIGN_STATUS: READY`.

---

## 4. DERIVED_LIFECYCLE (temporal / stateful)

Unchanged authority model (see continue/ff command bodies):

```text
owner-contract.md (+ review PASS) = semantic authority
design Lifecycle                  = DERIVED planning representation
continue / ff                     = no product authority
```

When behavior is temporal/stateful, design MUST include Derived Lifecycle
with `LIFECYCLE_STATUS: DERIVED` and per-step `SOURCE:` provenance, or
`LIFECYCLE_STATUS: UNRESOLVED` → route
`/opsx-owner-contract-review` (not `CONFIRM_LIFECYCLE` inside continue/ff).

Lifecycle completeness does **not** waive TECHNICAL_CHOICE_RESOLUTION or
NORMATIVE_MODALITY_PRESERVATION.

---

## 5. CONTINUE_FF_EQUIVALENCE

```text
same upstream context + same repository evidence
  → semantically equivalent design.md
```

independent of advancement mode (`/opsx-continue` vs `/opsx-ff`).

If a shared helper/template is used, edit the **single** SoT (this contract
and any shared template it cites). Do **not** maintain divergent design
rules in continue-only vs ff-only prose.

---

## 6. Out of scope for this contract

- Manually «fixing» a product Change’s existing `design.md` as part of a
  command-contract apply (regenerate via continue/ff after this gate exists,
  or `/opsx-update`).
- Owner Contract semantic authorship.
- Production code, product tests, migrations, product tasks.
- `/opsx-new`, `/opsx-owner-contract-review`, `/opsx-spec-scenario-coverage`,
  apply / verify / archive command bodies (unless a separate authorized fix
  lists them).
- Task generation (see `TASK_GENERATION.md`).

## Process fixtures

Regression fixtures live under:

`.cursor/commands/_fixtures/design-generation-readiness/`

Real planning sample used only as a **generator** fixture pointer:

`openspec/changes/auto-shelf-exhausted-pause-retest/`

Do not edit that Change’s product `design.md` inside a command-contract
apply of this gate; regenerate via continue/ff (or update) after the gate
exists.
