# Шаблон durable plan.md (mode=plan)
#
# Скопируй структуру в
# openspec/changes/<CHANGE_ID>/scenario-coverage/plan.md
# Замени <CHANGE_ID> и заполни секции по INPUT CONTEXT.
# Не вставляй тела сценариев (- SCENARIO_ID: …) в plan.md — только в scenarios/*.md

CHANGE_ID: <CHANGE_ID>
MODE: plan
PLAN_STATUS: ready
PLAN_EVIDENCE_ID: <computed-after-write>
LIFECYCLE_STATUS: DERIVED
LIFECYCLE_SOURCE: openspec/changes/<CHANGE_ID>/design.md

STEADY_STATE_RECONCILE: not_applicable | required | covered
RATIONALE: <short plan-level rationale from design/spec, no invented product semantics>

LIFECYCLE_COVERAGE:
  L1:
    - openspec/changes/<CHANGE_ID>/specs/<spec-dir>/spec.md::<Requirement or Scenario title>
  L2:
    - <spec>::<scenario mapping>
  L3:
    - <optional — omit level if no mapping>
  L_cross:
    - <cross-cutting spec scenarios if any>

SCENARIO_INVENTORY:
  - openspec/changes/<CHANGE_ID>/scenario-coverage/scenarios/<sha256(scenario_id)>.md
  # one repo-relative path per authored scenario file (hash = sha256 of SCENARIO_ID utf-8)
  # content_hash (sha256 of file body) must NEVER appear as the filename stem

SCENARIO_COVERAGE_PLAN:
  SCHEMA_VERSION: 1
  CHANGE_ID: <CHANGE_ID>
  PLAN_STATUS: ready
  SCENARIOS_TOTAL: <integer>
  SCENARIOS:
  # SCENARIO rows live only under scenarios/*.md — keep this list empty or omit row bodies here

OPEN_QUESTIONS:
  - <OA-NNN: …> | (none)

UNRESOLVED_GAPS:
  (none)

GENERATED_FROM:
  DESIGN_REVISION: openspec/changes/<CHANGE_ID>/design.md
  OWNER_CONTRACT_REVISION: openspec/changes/<CHANGE_ID>/owner-contract.md
  SPEC: openspec/changes/<CHANGE_ID>/specs/.../spec.md
  PRODUCER: SCENARIO_COVERAGE_PLAN
