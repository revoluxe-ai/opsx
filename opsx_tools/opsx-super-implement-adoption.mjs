/**
 * Mid-process adoption: join an in-flight manual split-APPLY without restarting
 * at APPLY DESIGN. Preserves MANUAL_PREDECESSOR provenance; never rewrites receipts.
 */
import { createHash } from "node:crypto";
import {
  APPLY_DESIGN_REVIEW_PASS,
  GREEN_PROVEN,
  classifyGreenEvidenceFreshness,
  evaluateGreenUpstreamIdentityChecks,
  graphValid,
  selectNextPipelineStage,
} from "./opsx-apply-pipeline-identity.mjs";
import {
  completeInFlightDispatch,
  evaluateApplyCompleteGate,
  evaluateOperativeReceiptForStage,
  loadSuperImplementContext,
  applyPersistedRun,
  RUN_STATUS_APPLY_COMPLETE,
  RUN_STATUS_PAUSED,
  RUN_STATUS_RUNNING,
  selectSuperImplementTransition,
  superImplementRunRelPath,
} from "./opsx-super-implement.mjs";
import {
  projectSplitApplyTaskCompletion,
  TASK_COMPLETE,
  TASK_INCOMPLETE,
  TASK_STALE,
} from "./opsx-apply-verify-completion.mjs";
import {
  acquireSuperChangeWriterLock,
  detectChangeWriterConflict,
  HOLDER_SUPER,
  manualApplyMarkerRelPath,
  superRunWriterLeaseId,
  writerLockRelPath,
  WRITER_HOLDER_MANUAL,
  WRITER_HOLDER_SUPER,
} from "./opsx-apply-change-writer.mjs";

export {
  manualApplyMarkerRelPath,
  writerLockRelPath,
  WRITER_HOLDER_MANUAL,
  WRITER_HOLDER_SUPER,
} from "./opsx-apply-change-writer.mjs";

export const ADOPTION_SCHEMA = "opsx.super_implement_mid_process_adoption";
export const ADOPTION_STATUS_ADOPTED = "ADOPTED";
export const ADOPTION_STATUS_PAUSED = "PAUSED";
export const ADOPTION_STATUS_FAILED = "FAILED";

export const PROVENANCE_MANUAL_PREDECESSOR = "MANUAL_PREDECESSOR";
export const PROVENANCE_SUPER_DISPATCH = "SUPER_DISPATCH";

const STAGE_ORDER = [
  "APPLY_DESIGN",
  "APPLY_DESIGN_REVIEW",
  "APPLY_SCAFFOLD",
  "APPLY_RED",
  "APPLY_IMPLEMENT",
  "APPLY_GREEN",
];

function nowIso() {
  return new Date().toISOString();
}

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function receiptFingerprint(receipt) {
  if (!receipt) return null;
  return createHash("sha256").update(JSON.stringify(receipt), "utf8").digest("hex");
}

function buildUpstreamBySlice(maps, repoRoot) {
  const binding = maps.boundedVerifyBinding ?? null;
  const bindingBySlice = maps.boundedVerifyBindingBySlice || {};
  const out = {};
  const ids = new Set([
    ...(maps.design?.slice_order?.nodes || []),
    ...Object.keys(maps.scaffoldBySlice || {}),
    ...Object.keys(maps.redBySlice || {}),
    ...Object.keys(maps.implementBySlice || {}),
    ...Object.keys(maps.greenBySlice || {}),
  ]);
  for (const id of ids) {
    out[id] = {
      design: maps.design,
      scaffold: maps.scaffoldBySlice?.[id],
      red: maps.redBySlice?.[id],
      implement: maps.implementBySlice?.[id],
      designArtifactSha256: maps.designArtifactSha256,
      scaffoldArtifactSha256: maps.scaffoldArtifactSha256BySlice?.[id],
      redArtifactSha256: maps.redArtifactSha256BySlice?.[id],
      implementArtifactSha256: maps.implementArtifactSha256BySlice?.[id],
      frozenTestContentIdentity: maps.frozenTestContentBySlice?.[id],
      liveFrozenTestContentIdentity: maps.liveFrozenTestContentBySlice?.[id],
      boundedVerifyBinding: bindingBySlice[id] ?? binding ?? undefined,
      repoRoot,
    };
  }
  return out;
}

/**
 * Per-slice stage classification for adoption audit (read-only).
 */
export function classifySlicePipelineStates(ctx) {
  const { design, maps, designReview, repoRoot } = ctx;
  const upstreamBySlice = buildUpstreamBySlice(maps, repoRoot);
  const slices = [];
  for (const slice of design?.slices || []) {
    const id = slice.slice_id;
    const stages = {};
    for (const stage of STAGE_ORDER) {
      if (stage === "APPLY_DESIGN") {
        stages[stage] = design?.status === "APPLY_DESIGN_READY"
          ? "COMPLETE"
          : "INCOMPLETE";
        continue;
      }
      if (stage === "APPLY_DESIGN_REVIEW") {
        const pass = designReview?.status === APPLY_DESIGN_REVIEW_PASS;
        stages[stage] = pass ? "COMPLETE" : "INCOMPLETE";
        continue;
      }
      const operative = evaluateOperativeReceiptForStage(stage, id, {
        ...ctx,
        upstreamBySlice,
      });
      let state = "NOT_STARTED";
      if (operative.artifact_status) {
        state = operative.authorized ? "COMPLETE" : "STALE_OR_BLOCKED";
      }
      if (
        stage === "APPLY_GREEN" &&
        maps.greenBySlice?.[id]?.status === GREEN_PROVEN &&
        !operative.authorized
      ) {
        state = "STALE_OR_BLOCKED";
      }
      if (
        stage === "APPLY_IMPLEMENT" &&
        maps.implementBySlice?.[id]?.status === "BLOCKED"
      ) {
        state = "BLOCKED";
      }
      stages[stage] = state;
    }
    slices.push({ slice_id: id, stages });
  }
  return slices;
}

function collectAdoptedPredecessorEvidence(ctx) {
  const { maps, repoRoot } = ctx;
  const upstreamBySlice = buildUpstreamBySlice(maps, repoRoot);
  const adopted = [];
  for (const slice of ctx.design?.slices || []) {
    const id = slice.slice_id;
    for (const stage of STAGE_ORDER) {
      if (stage === "APPLY_DESIGN" || stage === "APPLY_DESIGN_REVIEW") continue;
      const operative = evaluateOperativeReceiptForStage(stage, id, {
        ...ctx,
        upstreamBySlice,
      });
      if (!operative.authorized) continue;
      let receipt = null;
      if (stage === "APPLY_SCAFFOLD") receipt = maps.scaffoldBySlice?.[id];
      if (stage === "APPLY_RED") receipt = maps.redBySlice?.[id];
      if (stage === "APPLY_IMPLEMENT") receipt = maps.implementBySlice?.[id];
      if (stage === "APPLY_GREEN") receipt = maps.greenBySlice?.[id];
      adopted.push({
        slice_id: id,
        stage,
        artifact_status: operative.artifact_status,
        provenance: PROVENANCE_MANUAL_PREDECESSOR,
        relabeled_as_super_execution: false,
        receipt_fingerprint: receiptFingerprint(receipt),
        adopted_at: null,
      });
    }
  }
  return adopted;
}

function checkPlanningRevisionBinding(ctx) {
  const digest = ctx.design?.planning_identity?.planning_digest;
  const blockers = [];
  for (const slice of ctx.design?.slices || []) {
    const id = slice.slice_id;
    const green = ctx.maps.greenBySlice?.[id];
    if (!green?.apply_design_identity?.planning_digest) continue;
    if (
      digest &&
      green.apply_design_identity.planning_digest !== digest
    ) {
      blockers.push({
        code: "ADOPTION_PLANNING_REVISION_MISMATCH",
        slice_id: id,
        field: "green.apply_design_identity.planning_digest",
      });
    }
    const red = ctx.maps.redBySlice?.[id];
    if (
      red?.apply_design_identity?.planning_digest &&
      digest &&
      red.apply_design_identity.planning_digest !== digest
    ) {
      blockers.push({
        code: "ADOPTION_PLANNING_REVISION_MISMATCH",
        slice_id: id,
        field: "red.apply_design_identity.planning_digest",
      });
    }
  }
  return blockers;
}

function greenIdentityCurrent(green, upstream) {
  if (!green || green.status !== GREEN_PROVEN) return false;
  const freshness = classifyGreenEvidenceFreshness(green, upstream);
  const checks = evaluateGreenUpstreamIdentityChecks(green, upstream);
  return (
    freshness.current &&
    Object.values(checks.semanticChecks).every(Boolean) &&
    Object.values(checks.fileChecks).every(Boolean)
  );
}

/**
 * Earliest slice whose on-disk GREEN is not current (stale predecessor evidence).
 */
export function findEarliestStaleGreenSlice(ctx) {
  const { design, maps, repoRoot } = ctx;
  const upstreamBySlice = buildUpstreamBySlice(maps, repoRoot);
  for (const id of design?.slice_order?.nodes || []) {
    const green = maps.greenBySlice?.[id];
    if (!green || green.status !== GREEN_PROVEN) continue;
    if (!greenIdentityCurrent(green, upstreamBySlice[id] || {})) {
      return id;
    }
  }
  return null;
}

function checkTasksConsistency(ctx) {
  const upstreamBySlice = buildUpstreamBySlice(ctx.maps, ctx.repoRoot);
  const projection = projectSplitApplyTaskCompletion({
    design: ctx.design,
    greenBySlice: ctx.maps.greenBySlice,
    upstreamBySlice,
    durableProjectionPresent: false,
  });
  const issues = [];
  for (const row of projection.tasks || []) {
    if (row.completion_status === TASK_STALE) {
      issues.push({
        code: "TASK_STALE_GREEN",
        task_id: row.task_id,
        evidence: row.current_green_evidence,
      });
    }
    const hasGreenSlice = (row.covering_slice_ids || []).some((sid) => {
      const g = ctx.maps.greenBySlice?.[sid];
      return g?.status === GREEN_PROVEN;
    });
    if (
      hasGreenSlice &&
      row.completion_status === TASK_INCOMPLETE
    ) {
      issues.push({
        code: "TASKS_INCONSISTENT_WITH_GREEN",
        task_id: row.task_id,
        completion_status: row.completion_status,
      });
    }
  }
  return issues;
}

export function detectConcurrentWriterConflict({
  repoRoot,
  changeRoot,
  run = null,
}) {
  const lease = run ? superRunWriterLeaseId(run) : null;
  const check = detectChangeWriterConflict({
    repoRoot,
    changeRoot,
    holder: HOLDER_SUPER,
    writerLeaseId: lease,
  });
  if (!check.ok && check.code === "MANUAL_WRITER_IN_FLIGHT") {
    return {
      ...check,
      recovery_contract: check.recovery_contract || {
        code: check.code,
        next_action:
          "wait for manual split-APPLY to finish or release WRITER_LOCK after reconcile",
        resume_requires: ["manual_writer_cleared"],
      },
    };
  }
  if (!check.ok && check.code === "SUPER_BLOCKED_BY_MANUAL") {
    return {
      ...check,
      code: "FOREIGN_WRITER_LOCK",
    };
  }
  return check;
}

export function acquireSuperWriterLock(repoRoot, changeRoot, run) {
  return acquireSuperChangeWriterLock({ repoRoot, changeRoot, run });
}

/**
 * Revalidate writer ownership and acquire Super lock when none exists (fail-closed).
 */
export function ensureSuperWriterLockForHop({ repoRoot, changeRoot, run }) {
  const pre = detectConcurrentWriterConflict({ repoRoot, changeRoot, run });
  if (!pre.ok) {
    return {
      ok: false,
      code: pre.code,
      recovery_contract: pre.recovery_contract,
      lock: pre.lock,
    };
  }
  return acquireSuperChangeWriterLock({ repoRoot, changeRoot, run });
}

/**
 * Read-only adoption evaluation (native validators + selectNextPipelineStage).
 */
export function evaluateMidProcessAdoption(ctx) {
  if (!ctx.ok) {
    return { ok: false, code: ctx.code || "CONTEXT_INVALID" };
  }
  if (!isObject(ctx.design) || !graphValid(ctx.design)) {
    return { ok: false, code: "INVALID_SLICE_GRAPH" };
  }
  const slice_states = classifySlicePipelineStates(ctx);
  const revisionBlockers = checkPlanningRevisionBinding(ctx);
  const taskIssues = checkTasksConsistency(ctx);
  const staleGreenSlice = findEarliestStaleGreenSlice(ctx);
  const pipelineStagePick = selectNextPipelineStage({
    design: ctx.design,
    scaffoldBySlice: ctx.maps.scaffoldBySlice,
    redBySlice: ctx.maps.redBySlice,
    implementBySlice: ctx.maps.implementBySlice,
    greenBySlice: ctx.maps.greenBySlice,
    designReview: ctx.designReview,
    repoRoot: ctx.repoRoot,
  });
  const transition = selectSuperImplementTransition(ctx);
  if (!transition.ok) {
    return {
      ok: false,
      code: transition.code,
      slice_states,
      revisionBlockers,
      taskIssues,
    };
  }
  const completeGate = evaluateApplyCompleteGate(ctx, { requireTaskProjection: true });
  const blockers = [...revisionBlockers, ...taskIssues];
  if (blockers.length) {
    return {
      ok: false,
      code: blockers[0].code,
      slice_states,
      blockers,
      transition,
      complete_gate: completeGate,
    };
  }
  const adopted_predecessor_evidence = collectAdoptedPredecessorEvidence(ctx).map(
    (row) => ({ ...row, adopted_at: nowIso() }),
  );
  let nextAfter = {
    next_stage: transition.next_stage,
    next_slice: transition.next_slice,
    primitive_command: transition.primitive_command,
  };
  if (staleGreenSlice) {
    nextAfter = {
      next_stage: "APPLY_GREEN",
      next_slice: staleGreenSlice,
      primitive_command: "/opsx-apply-green",
    };
  }
  return {
    ok: true,
    slice_states,
    adopted_predecessor_evidence,
    stale_green_slice: staleGreenSlice,
    next_after_adoption: nextAfter,
    pipeline_stage_pick: pipelineStagePick,
    pipeline_all_green: pipelineStagePick.next_stage === "APPLY_COMPLETE",
    already_complete: transition.next_stage === "APPLY_COMPLETE" && completeGate.ok,
    complete_gate: completeGate,
    transition,
  };
}

/**
 * Materialize RUN.json adoption section (does not modify slice receipts).
 */
export function ensureMidProcessAdoption({
  repoRoot,
  changeRoot,
  run,
  force = false,
} = {}) {
  const ctx = loadSuperImplementContext({ repoRoot, changeRoot });
  if (!ctx.ok) {
    return { ok: false, code: ctx.code, status: ADOPTION_STATUS_FAILED };
  }
  const writer = detectConcurrentWriterConflict({ repoRoot, changeRoot, run });
  if (!writer.ok) {
    return {
      ok: false,
      status: ADOPTION_STATUS_PAUSED,
      code: writer.code,
      recovery_contract: writer.recovery_contract,
    };
  }
  if (run?.in_flight_dispatch) {
    const inflight = run.in_flight_dispatch;
    const operative = evaluateOperativeReceiptForStage(
      inflight.stage,
      inflight.slice_id,
      ctx,
    );
    if (operative.authorized) {
      const done = completeInFlightDispatch(run, ctx, {
        worker_claim: "success",
        reconciled_from_disk: true,
        exit_code_ignored: true,
      });
      run = done.run;
    } else if (!force) {
      return {
        ok: false,
        status: ADOPTION_STATUS_PAUSED,
        code: "ADOPTION_DEFERRED_IN_FLIGHT",
        recovery_contract: {
          code: "ADOPTION_DEFERRED_IN_FLIGHT",
          next_action: "reconcile in_flight_dispatch before adoption refresh",
          resume_requires: ["in_flight_reconciled"],
        },
      };
    }
  }
  if (
    !force &&
    run?.mid_process_adoption?.status === ADOPTION_STATUS_ADOPTED &&
    run?.adopted_predecessor_evidence?.length
  ) {
    const writerStill = detectConcurrentWriterConflict({ repoRoot, changeRoot, run });
    if (!writerStill.ok) {
      return {
        ok: false,
        status: ADOPTION_STATUS_PAUSED,
        code: writerStill.code,
        recovery_contract: writerStill.recovery_contract,
      };
    }
    if (run?.in_flight_dispatch) {
      return {
        ok: false,
        status: ADOPTION_STATUS_PAUSED,
        code: "ADOPTION_DEFERRED_IN_FLIGHT",
        recovery_contract: {
          code: "ADOPTION_DEFERRED_IN_FLIGHT",
          next_action: "reconcile in_flight_dispatch before idempotent adoption",
          resume_requires: ["in_flight_reconciled"],
        },
      };
    }
    return {
      ok: true,
      status: ADOPTION_STATUS_ADOPTED,
      run,
      idempotent: true,
    };
  }
  const evaluation = evaluateMidProcessAdoption(ctx);
  if (!evaluation.ok) {
    return {
      ok: false,
      status: ADOPTION_STATUS_PAUSED,
      code: evaluation.code,
      evaluation,
      recovery_contract: {
        code: evaluation.code,
        next_action: "repair stale/blocking evidence before Super adoption",
        resume_requires: ["adoption_evaluation_pass"],
      },
    };
  }
  const adoptedAt = nowIso();
  const nextRun = {
    ...run,
    status: evaluation.already_complete ? RUN_STATUS_APPLY_COMPLETE : RUN_STATUS_RUNNING,
    mid_process_adoption: {
      schema: ADOPTION_SCHEMA,
      status: ADOPTION_STATUS_ADOPTED,
      adopted_at: adoptedAt,
      predecessor_provenance: PROVENANCE_MANUAL_PREDECESSOR,
      planning_digest: ctx.design?.planning_identity?.planning_digest,
      next_after_adoption: evaluation.next_after_adoption,
      slice_states: evaluation.slice_states,
      relabeled_predecessor_evidence: false,
    },
    adopted_predecessor_evidence: evaluation.adopted_predecessor_evidence,
    history: [
      ...(run.history || []),
      {
        sequence: run.execution_identity?.run_sequence ?? 0,
        kind: "MID_PROCESS_ADOPTION",
        outcome: "ADOPTED",
        next_stage: evaluation.next_after_adoption?.next_stage,
        next_slice: evaluation.next_after_adoption?.next_slice,
      },
    ],
    last_transition: {
      at: adoptedAt,
      kind: "MID_PROCESS_ADOPTION",
      stage: evaluation.next_after_adoption?.next_stage,
      slice_id: evaluation.next_after_adoption?.next_slice,
      code: null,
    },
  };
  const lock = acquireSuperWriterLock(repoRoot, changeRoot, nextRun);
  if (!lock.ok) {
    return {
      ok: false,
      status: ADOPTION_STATUS_PAUSED,
      code: lock.code,
    };
  }
  const saved = applyPersistedRun(repoRoot, changeRoot, nextRun);
  if (!saved.ok) {
    return {
      ok: false,
      status: ADOPTION_STATUS_PAUSED,
      code: saved.code,
      run: saved.run,
      recovery_contract: saved.run.recovery_contract,
    };
  }
  return {
    ok: true,
    status: ADOPTION_STATUS_ADOPTED,
    run: saved.run,
    evaluation,
    writer_lock: lock.lock,
  };
}
