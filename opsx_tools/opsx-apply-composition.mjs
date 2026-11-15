/**
 * Cross-slice composition contracts for split APPLY.
 *
 * Shared by APPLY DESIGN (author/freeze), DESIGN REVIEW (challenge),
 * RED (materialize RED-owned obligations), and GREEN (prefer GREEN-owned
 * composition adjacent checks over path-proximity heuristics).
 *
 * Dogfood basis: cross-device-novel-save-refresh interaction failures are
 * generalized into failure-class patterns — not product-specific logic.
 *
 * Does not invent Temporal, post-GREEN verification, or product writes.
 */

export const APPLY_DESIGN_REVIEW = "APPLY_DESIGN_REVIEW";
export const APPLY_DESIGN_REVIEW_PASS = "APPLY_DESIGN_REVIEW_PASS";
export const REPAIRS_REQUIRED = "REPAIRS_REQUIRED";

export const PROOF_PLACEMENT = Object.freeze({
  B_RED: "B_RED",
  B_GREEN_ADJACENT: "B_GREEN_ADJACENT",
  LATER_SLICE: "LATER_SLICE",
  CHANGE_LEVEL_VERIFY: "CHANGE_LEVEL_VERIFY",
});

export const INVARIANT_CLASS = Object.freeze({
  SLICE_LOCAL: "slice_local",
  CROSS_SLICE: "cross_slice",
  CHANGE_LEVEL: "change_level",
});

/**
 * Generic interaction failure classes proven by dogfood (novel-save refresh).
 * DESIGN/REVIEW fixtures must treat these as composition patterns when
 * applicable — never hard-code novel-save module names here.
 */
export const COMPOSITION_FAILURE_CLASSES = Object.freeze([
  {
    id: "IN_FLIGHT_JOIN_BY_BOOTSTRAP",
    summary:
      "Downstream bootstrap joins an in-flight upstream operation and must still complete startup",
  },
  {
    id: "CONTROL_RESULT_AS_FAILURE",
    summary:
      "Upstream control/race results (e.g. in-flight, stale generation) must not be interpreted as API/fail-closed failures downstream",
  },
  {
    id: "OVERLAPPING_WRITE_INVALIDATES_BOOLEAN_IN_FLIGHT",
    summary:
      "Overlapping writes invalidate naive boolean in-flight gating; composition must define generation/token semantics",
  },
  {
    id: "DEBOUNCED_WORK_AFTER_LIFECYCLE_CHANGE",
    summary:
      "Debounced or deferred work must not execute after route/lifecycle state has changed",
  },
  {
    id: "SEMANTIC_EQ_VS_BYTE_EQ",
    summary:
      "Semantic state equality must not be confused with byte/serialization equality at reload or payload boundaries",
  },
]);

export const COMPOSITION_FAILURE_CLASS_IDS = COMPOSITION_FAILURE_CLASSES.map(
  (c) => c.id,
);

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function edgeKey(from, to) {
  return `${from}->${to}`;
}

/**
 * Every dependency edge must be covered by a cross_slice_contract that
 * lists the upstream and names the dependent slice.
 */
export function compositionContractsCoverEdges(design) {
  const edges = design?.slice_order?.edges || [];
  const contracts = design?.cross_slice_contracts || [];
  const uncovered = [];
  const covered = new Set();
  for (const edge of edges) {
    if (!edge || !edge.from || !edge.to) continue;
    const key = edgeKey(edge.from, edge.to);
    const match = contracts.find(
      (c) =>
        c &&
        c.dependent_slice_id === edge.to &&
        Array.isArray(c.upstream_slice_ids) &&
        c.upstream_slice_ids.includes(edge.from),
    );
    if (!match) uncovered.push({ from: edge.from, to: edge.to, key });
    else covered.add(key);
  }
  return { ok: uncovered.length === 0, uncovered, covered: [...covered] };
}

/**
 * Structural completeness of one cross_slice_contract (WHY/WHAT compose).
 */
export function compositionContractStructuralGaps(contract) {
  const gaps = [];
  if (!contract || !isObject(contract)) {
    return ["contract_missing"];
  }
  const reqArrays = [
    "upstream_slice_ids",
    "consumed_semantics",
    "incoming_states",
    "incoming_failure_modes",
    "interaction_invariants",
    "required_composition_behavior",
    "proof_placement",
  ];
  for (const k of reqArrays) {
    if (!Array.isArray(contract[k]) || contract[k].length < 1) {
      gaps.push(`empty_or_missing:${k}`);
    }
  }
  if (!contract.dependent_slice_id) gaps.push("missing:dependent_slice_id");
  if (!Array.isArray(contract.shared_state)) gaps.push("missing:shared_state");
  if (!Array.isArray(contract.race_boundaries)) {
    gaps.push("missing:race_boundaries");
  }
  if (!Array.isArray(contract.lifecycle_boundaries)) {
    gaps.push("missing:lifecycle_boundaries");
  }
  for (const p of contract.proof_placement || []) {
    if (!p || !p.obligation || !p.placement) {
      gaps.push("proof_placement_incomplete");
      break;
    }
    if (!Object.values(PROOF_PLACEMENT).includes(p.placement)) {
      gaps.push(`invalid_proof_placement:${p.placement}`);
    }
  }
  // Bare "depends on" without behavior is rejected.
  const behaviors = contract.required_composition_behavior || [];
  if (
    behaviors.length === 1 &&
    /^depends on /i.test(String(behaviors[0])) &&
    String(behaviors[0]).length < 40
  ) {
    gaps.push("bare_depends_on");
  }
  return gaps;
}

/**
 * READY design composition semantic errors (also reusable by REVIEW).
 */
export function designCompositionSemanticErrors(design) {
  const errors = [];
  if (!design || design.status !== "APPLY_DESIGN_READY") return errors;

  const cover = compositionContractsCoverEdges(design);
  for (const u of cover.uncovered) {
    errors.push({
      code: "incomplete_composition",
      message: `dependency edge ${u.key} has no cross_slice_contract explaining WHY/WHAT must compose`,
    });
  }

  for (const c of design.cross_slice_contracts || []) {
    const gaps = compositionContractStructuralGaps(c);
    for (const g of gaps) {
      errors.push({
        code: "incomplete_composition",
        message: `cross_slice_contract ${c?.id || "?"} gap: ${g}`,
      });
    }
  }

  const completeness = design.composition_completeness;
  if (!isObject(completeness)) {
    errors.push({
      code: "incomplete_composition",
      message: "composition_completeness is required for APPLY_DESIGN_READY",
    });
    return errors;
  }

  const inv = completeness.invariant_classifications || [];
  if (!Array.isArray(inv) || inv.length < 1) {
    errors.push({
      code: "incomplete_composition",
      message: "composition_completeness.invariant_classifications must be non-empty",
    });
  } else {
    for (const row of inv) {
      if (!row?.invariant || !row?.class || !row?.proof_owner) {
        errors.push({
          code: "incomplete_composition",
          message: "every invariant classification needs invariant, class, proof_owner",
        });
        break;
      }
      if (!Object.values(INVARIANT_CLASS).includes(row.class)) {
        errors.push({
          code: "incomplete_composition",
          message: `invalid invariant class: ${row.class}`,
        });
      }
    }
  }

  // Shared mutable state: owner + consumers when present.
  for (const s of completeness.shared_mutable_state || []) {
    if (!s?.name || !s?.owner_slice_id || !Array.isArray(s.consumer_slice_ids)) {
      errors.push({
        code: "incomplete_composition",
        message: "shared_mutable_state entries need name, owner_slice_id, consumer_slice_ids",
      });
      break;
    }
  }

  // Special results crossing boundaries need interpretation.
  for (const r of completeness.special_result_interpretations || []) {
    if (!r?.result || !r?.producer_slice_id || !r?.interpretation) {
      errors.push({
        code: "incomplete_composition",
        message:
          "special_result_interpretations need result, producer_slice_id, interpretation",
      });
      break;
    }
  }

  // Race / lifecycle applicability must be explicit (relevant or not_applicable).
  const raceApp = completeness.race_applicability;
  const lifeApp = completeness.lifecycle_applicability;
  if (!isObject(raceApp) || typeof raceApp.applicable !== "boolean") {
    errors.push({
      code: "incomplete_composition",
      message: "race_applicability.applicable (boolean) is required",
    });
  } else if (raceApp.applicable === true) {
    const kinds = ["concurrent_invocation", "stale_result", "cancellation_skip", "retry_reentry"];
    for (const k of kinds) {
      if (!raceApp[k] || typeof raceApp[k] !== "string" || !raceApp[k].trim()) {
        errors.push({
          code: "incomplete_composition",
          message: `race_applicability.${k} required when applicable=true`,
        });
      }
    }
  }
  if (!isObject(lifeApp) || typeof lifeApp.applicable !== "boolean") {
    errors.push({
      code: "incomplete_composition",
      message: "lifecycle_applicability.applicable (boolean) is required",
    });
  } else if (lifeApp.applicable === true) {
    const kinds = [
      "bootstrap",
      "already_running",
      "route_enter_leave",
      "visibility_resume",
      "teardown_remount",
    ];
    for (const k of kinds) {
      if (!lifeApp[k] || typeof lifeApp[k] !== "string" || !lifeApp[k].trim()) {
        errors.push({
          code: "incomplete_composition",
          message: `lifecycle_applicability.${k} required when applicable=true`,
        });
      }
    }
  }

  return errors;
}

/**
 * Review structural findings against a READY design (no chat history).
 * Returns findings[]; empty means no structural repairs required by tool.
 * Agent review may still add semantic findings.
 */
export function structuralDesignReviewFindings(design, { planningScenarios = [] } = {}) {
  const findings = [];
  if (!design || design.status !== "APPLY_DESIGN_READY") {
    findings.push({
      id: "F-DESIGN-NOT-READY",
      question: 1,
      severity: "blocking",
      detail: "APPLY DESIGN is not APPLY_DESIGN_READY",
    });
    return findings;
  }

  for (const err of designCompositionSemanticErrors(design)) {
    findings.push({
      id: `F-COMP-${findings.length + 1}`,
      question: 8,
      severity: "blocking",
      detail: err.message,
      code: err.code,
    });
  }

  // Q6: every listed planning scenario / requirement_ref should have an owner slice.
  const ownedRefs = new Set();
  for (const s of design.slices || []) {
    for (const r of s.requirement_refs || []) ownedRefs.add(r);
    for (const t of s.source_task_ids || []) ownedRefs.add(t);
  }
  for (const sc of planningScenarios) {
    if (sc && !ownedRefs.has(sc)) {
      findings.push({
        id: `F-OWNER-${sc}`,
        question: 6,
        severity: "blocking",
        detail: `planning item ${sc} has no slice implementation owner`,
      });
    }
  }

  // Q7: every critical / cross_slice / change_level invariant has proof_owner.
  for (const row of design.composition_completeness?.invariant_classifications || []) {
    if (
      (row.class === INVARIANT_CLASS.CROSS_SLICE ||
        row.class === INVARIANT_CLASS.CHANGE_LEVEL) &&
      !row.proof_owner
    ) {
      findings.push({
        id: `F-PROOF-${row.invariant}`,
        question: 7,
        severity: "blocking",
        detail: `critical invariant lacks proof_owner: ${row.invariant}`,
      });
    }
  }

  // Q12 mandatory: composed-behavior risk must be answered on the review receipt
  // (checked by review validator). Here we only flag if DESIGN itself claims
  // zero interaction risk while having dependency edges.
  const edges = design.slice_order?.edges || [];
  if (edges.length > 0) {
    const contracts = design.cross_slice_contracts || [];
    const thin = contracts.filter(
      (c) => (c.required_composition_behavior || []).length < 1,
    );
    if (thin.length) {
      findings.push({
        id: "F-COMPOSED-THIN",
        question: 12,
        severity: "blocking",
        detail:
          "dependency edges exist but composition behavior is empty — slices could GREEN while composed behavior is wrong",
      });
    }
  }

  return findings;
}

/**
 * Detect dogfood-equivalent interaction patterns missing from DESIGN when
 * the design declares race/lifecycle applicable and has dependency edges.
 */
export function missingDogfoodFailureClassCoverage(design) {
  const missing = [];
  if (!design || design.status !== "APPLY_DESIGN_READY") return missing;
  const edges = design.slice_order?.edges || [];
  if (!edges.length) return missing;
  const raceOn = design.composition_completeness?.race_applicability?.applicable;
  const lifeOn = design.composition_completeness?.lifecycle_applicability?.applicable;
  if (!raceOn && !lifeOn) return missing;

  const blob = JSON.stringify({
    contracts: design.cross_slice_contracts,
    completeness: design.composition_completeness,
  }).toLowerCase();

  const needles = {
    IN_FLIGHT_JOIN_BY_BOOTSTRAP: [/in-flight|inflight|join/, /bootstrap|startup/],
    CONTROL_RESULT_AS_FAILURE: [
      /control|stale_generation|put_in_flight|race/,
      /fail-closed|failure|not.*api/,
    ],
    OVERLAPPING_WRITE_INVALIDATES_BOOLEAN_IN_FLIGHT: [
      /overlap|concurrent|generation|token/,
      /in-flight|inflight|boolean/,
    ],
    DEBOUNCED_WORK_AFTER_LIFECYCLE_CHANGE: [
      /debounc|deferred/,
      /route|lifecycle|visibility|teardown/,
    ],
    SEMANTIC_EQ_VS_BYTE_EQ: [/semantic/, /byte|serializ|payload|reload/],
  };

  for (const cls of COMPOSITION_FAILURE_CLASS_IDS) {
    const listed =
      (design.composition_completeness?.applicable_failure_classes || []).includes(
        cls,
      ) ||
      (design.composition_completeness?.failure_class_applicability || {})[cls] ===
        "applicable" ||
      (design.composition_completeness?.failure_class_applicability || {})[cls] ===
        "not_applicable";
    if (listed) continue;
    // If DESIGN explicitly classifies failure classes, require every class
    // to be classified when race/lifecycle applicable.
    const hasMap = isObject(
      design.composition_completeness?.failure_class_applicability,
    );
    const hasList = Array.isArray(
      design.composition_completeness?.applicable_failure_classes,
    );
    if (hasMap || hasList) {
      missing.push(cls);
      continue;
    }
    // Heuristic: if text mentions neither pattern half, flag as uncovered.
    const pairs = needles[cls] || [];
    const hit = pairs.every((res) => res.some((re) => re.test(blob)));
    if (!hit) missing.push(cls);
  }
  return missing;
}

export function collectContractsForSlice(design, sliceId, { asDependent = true } = {}) {
  const out = [];
  for (const c of design?.cross_slice_contracts || []) {
    if (asDependent && c.dependent_slice_id === sliceId) out.push(c);
    if (
      !asDependent &&
      Array.isArray(c.upstream_slice_ids) &&
      c.upstream_slice_ids.includes(sliceId)
    ) {
      out.push(c);
    }
  }
  return out;
}

export function collectCompositionObligations(design, sliceId, placement) {
  const obligations = [];
  for (const c of collectContractsForSlice(design, sliceId, { asDependent: true })) {
    for (const p of c.proof_placement || []) {
      if (p.placement === placement) {
        obligations.push({
          contract_id: c.id,
          obligation: p.obligation,
          placement: p.placement,
          upstream_slice_ids: c.upstream_slice_ids,
          required_composition_behavior: c.required_composition_behavior,
          interaction_invariants: c.interaction_invariants,
        });
      }
    }
  }
  return obligations;
}

export function collectRedOwnedCompositionObligations(design, sliceId) {
  return collectCompositionObligations(design, sliceId, PROOF_PLACEMENT.B_RED);
}

export function collectGreenOwnedCompositionObligations(design, sliceId) {
  return collectCompositionObligations(
    design,
    sliceId,
    PROOF_PLACEMENT.B_GREEN_ADJACENT,
  );
}

/**
 * Named composition seams (test paths / symbols) from GREEN-owned obligations.
 * Prefer these over path-proximity heuristics when selecting adjacent checks.
 */
export function collectGreenCompositionSeamHints(design, sliceId) {
  const hints = [];
  for (const o of collectGreenOwnedCompositionObligations(design, sliceId)) {
    const text = `${o.obligation} ${(o.required_composition_behavior || []).join(" ")}`;
    const paths = text.match(/tests\/[A-Za-z0-9_./-]+\.test\.m?js/g) || [];
    for (const p of paths) {
      if (!hints.includes(p)) hints.push(p);
    }
  }
  // Also accept explicit seam fields on contracts.
  for (const c of collectContractsForSlice(design, sliceId)) {
    for (const s of c.green_adjacent_seams || []) {
      if (s && !hints.includes(s)) hints.push(s);
    }
  }
  return hints;
}
