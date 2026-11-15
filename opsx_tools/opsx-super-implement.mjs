import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Super Implement V0 — thin sequencer over native split-APPLY primitives.
 *
 * Owns deterministic stage selection (via selectNextPipelineStage), dispatch
 * planning, operative receipt verification, durable run ledger, resume/recovery,
 * and APPLY_COMPLETE gating. Does not replace /opsx-apply-* write authority.
 *
 * Live product Change execution is fail-closed unless live_execution_authorized
 * is explicitly true (Owner attestation outside this tool).
 *
 * Usage:
 *   node opsx_tools/opsx-super-implement.mjs plan --change-root <rel>
 *   node opsx_tools/opsx-super-implement.mjs status --change-root <rel>
 *   node opsx_tools/opsx-super-implement.mjs advance --change-root <rel>
 */
import { createHash, randomBytes } from "node:crypto";
import { renameSync } from "node:fs";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { evaluateBreakGlassArchiveDebt } from "./opsx-break-glass-archive-gate.mjs";
import { evaluateApplyCompleteWithExceptionGate } from "./opsx-break-glass-exception-completion.mjs";
import { OWNER_OVERRIDDEN } from "./opsx-break-glass.mjs";
import { ownerOverrideAllowsProcessGate } from "./opsx-break-glass-universal-v2.mjs";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  APPLY_DESIGN_REVIEW_PASS,
  GREEN_PROVEN,
  IMPLEMENTATION_ADOPTED,
  IMPLEMENTATION_WRITTEN,
  RED_PROVEN,
  SCAFFOLD_READY,
  ALREADY_SATISFIED,
  classifyGreenEvidenceFreshness,
  evaluateApplyDesignReviewGate,
  evaluateGreenUpstreamIdentityChecks,
  graphValid,
  isApplyDesignAuthorityFresh,
  isDurableRedAuthority,
  loadBoundedVerifyBindingFromDisk,
  loadChangeReceiptMaps,
  posixPath,
  selectNextPipelineStage,
  RECOVERY_PAUSED,
  classifyPostImplementGreenRecovery,
  postImplementRecoveryBlocksOperativeRedRoute,
} from "./opsx-apply-pipeline-identity.mjs";
import {
  buildOwnerDecisionPacket,
  classifyPostImplementRecoveryPath,
  evaluateRecoveryCompletionConsumer,
  evaluateSemanticEquivalenceGate,
  RECOVERY_PHASE_SUCCESSOR_PLAN,
} from "./opsx-post-implement-test-contract-recovery.mjs";
import {
  evaluateSplitApplyVerifyCompletionFromDisk,
  loadRecoveryCompletionContextFromDisk,
  projectSplitApplyTaskCompletion,
  TASK_COMPLETE,
  TASK_STALE,
} from "./opsx-apply-verify-completion.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);

export const SUPER_IMPLEMENT_SCHEMA = "opsx.super_implement_run";
export const SUPER_IMPLEMENT_SCHEMA_VERSION = 1;
export const LIVE_EXECUTION_DEFAULT_AUTHORIZED = false;

/** Native split-APPLY primitives the sequencer may plan (one per hop). */
export const NATIVE_PRIMITIVE_BY_STAGE = Object.freeze({
  APPLY_DESIGN: "/opsx-apply-design",
  APPLY_DESIGN_REVIEW: "/opsx-apply-design-review",
  APPLY_SCAFFOLD: "/opsx-apply-scaffold",
  APPLY_RED: "/opsx-apply-red",
  APPLY_IMPLEMENT: "/opsx-apply-implement",
  APPLY_GREEN: "/opsx-apply-green",
});

/** Commands that must never substitute for a split stage hop. */
export const FORBIDDEN_DISPATCH_COMMANDS = Object.freeze([
  "/opsx-apply",
  "/opsx-verify",
  "/opsx-archive",
  "/opsx-supervisor",
]);

export const RUN_STATUS_RUNNING = "RUNNING";
export const RUN_STATUS_PAUSED = "PAUSED";
export const RUN_STATUS_FAILED = "FAILED";
export const RUN_STATUS_APPLY_COMPLETE = "APPLY_COMPLETE";

const OPERATIVE_SUCCESS = Object.freeze({
  APPLY_DESIGN: new Set(["APPLY_DESIGN_READY"]),
  APPLY_DESIGN_REVIEW: new Set([APPLY_DESIGN_REVIEW_PASS]),
  APPLY_SCAFFOLD: new Set([SCAFFOLD_READY]),
  APPLY_RED: new Set([RED_PROVEN, ALREADY_SATISFIED]),
  APPLY_IMPLEMENT: new Set([IMPLEMENTATION_WRITTEN, IMPLEMENTATION_ADOPTED]),
  APPLY_GREEN: new Set([GREEN_PROVEN]),
});

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function nowIso() {
  return new Date().toISOString();
}

/**
 * Stable execution identity for one planned hop (planning digest + head + stage).
 */
import { computeWriterLeaseId } from "./opsx-apply-change-writer.mjs";

export { computeWriterLeaseId } from "./opsx-apply-change-writer.mjs";

export function computeSuperImplementExecutionIdentity({
  changeId,
  planningDigest,
  repositoryHead = "unknown",
  stage,
  sliceId = null,
  runSequence = 0,
} = {}) {
  const payload = stableStringify({
    change_id: changeId || "",
    planning_digest: planningDigest || "",
    repository_head: repositoryHead || "",
    stage: stage || "",
    slice_id: sliceId || "",
    run_sequence: runSequence,
  });
  return {
    planning_digest: planningDigest || null,
    repository_head: repositoryHead || null,
    run_sequence: runSequence,
    hop_digest: createHash("sha256").update(payload, "utf8").digest("hex"),
  };
}

function stableStringify(value) {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((e) => stableStringify(e)).join(",")}]`;
  }
  const keys = Object.keys(value).sort();
  return `{${keys
    .map((k) => `${JSON.stringify(k)}:${stableStringify(value[k])}`)
    .join(",")}}`;
}

export function superImplementRunRelPath(changeRoot) {
  const cr = posixPath(changeRoot).replace(/\/$/, "");
  return `${cr}/super-implement/RUN.json`;
}

export function designReviewRelPath(changeRoot) {
  const cr = posixPath(changeRoot).replace(/\/$/, "");
  return `${cr}/apply-design-review/APPLY-DESIGN-REVIEW.json`;
}

export function loadDesignReviewFromDisk(repoRoot, changeRoot) {
  const abs = resourcePath(repoRoot, designReviewRelPath(changeRoot));
  if (!existsSync(abs)) return null;
  return JSON.parse(readFileSync(abs, "utf8"));
}

function buildUpstreamBySlice(maps) {
  const binding = maps.boundedVerifyBinding ?? null;
  const bindingBySlice = maps.boundedVerifyBindingBySlice || {};
  const out = {};
  const sliceIds = new Set([
    ...Object.keys(maps.scaffoldBySlice || {}),
    ...Object.keys(maps.redBySlice || {}),
    ...Object.keys(maps.implementBySlice || {}),
    ...Object.keys(maps.greenBySlice || {}),
  ]);
  for (const id of maps.design?.slice_order?.nodes || []) {
    sliceIds.add(id);
  }
  for (const id of sliceIds) {
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
      repoRoot: maps.repoRoot || DEFAULT_ROOT,
    };
  }
  return out;
}

/**
 * Load DESIGN, review, and per-slice receipt maps from disk.
 */
export function loadSuperImplementContext({
  repoRoot = DEFAULT_ROOT,
  changeRoot,
} = {}) {
  const cr = posixPath(changeRoot);
  const designPath = resourcePath(repoRoot, cr, "apply-design/APPLY-DESIGN.json");
  if (!existsSync(designPath)) {
    return { ok: false, code: "APPLY_DESIGN_MISSING", change_root: cr };
  }
  const design = JSON.parse(readFileSync(designPath, "utf8"));
  const sliceIds =
    design.slice_order?.nodes || (design.slices || []).map((s) => s.slice_id);
  const maps = loadChangeReceiptMaps(repoRoot, cr, sliceIds);
  const boundedVerifyBinding = loadBoundedVerifyBindingFromDisk(repoRoot, cr);
  const designReview = loadDesignReviewFromDisk(repoRoot, cr);
  const upstreamBySlice = buildUpstreamBySlice({
    ...maps,
    repoRoot,
    boundedVerifyBinding,
  });
  return {
    ok: true,
    repoRoot,
    changeRoot: cr,
    changeId: design.change_id,
    design,
    designReview,
    sliceIds,
    maps,
    upstreamBySlice,
  };
}

/**
 * Preflight: canonical branch HEAD must match expected when both are supplied.
 */
export function evaluateBranchPreflight({
  currentHead,
  expectedHead,
  canonicalBranch = null,
  currentBranch = null,
} = {}) {
  if (expectedHead && currentHead && expectedHead !== currentHead) {
    return {
      ok: false,
      code: "BRANCH_HEAD_MISMATCH",
      recovery_contract: {
        code: "BRANCH_HEAD_MISMATCH",
        next_action: "checkout canonical branch and align HEAD before resume",
        resume_requires: ["expected_head_match"],
      },
      current_head: currentHead,
      expected_head: expectedHead,
      canonical_branch: canonicalBranch,
      current_branch: currentBranch,
    };
  }
  return { ok: true };
}

export function assertDispatchCommandAllowed(command) {
  const cmd = String(command || "").trim();
  if (FORBIDDEN_DISPATCH_COMMANDS.includes(cmd)) {
    return {
      ok: false,
      code: "FORBIDDEN_PRIMITIVE_SUBSTITUTION",
      command: cmd,
    };
  }
  const allowed = Object.values(NATIVE_PRIMITIVE_BY_STAGE);
  if (!allowed.includes(cmd)) {
    return { ok: false, code: "UNAUTHORIZED_PRIMITIVE", command: cmd };
  }
  return { ok: true, command: cmd };
}

/**
 * Status-only graph stubs must not satisfy completion or operative advance.
 */
export function isStatusOnlyGreenStub(green) {
  if (!isObject(green) || green.status !== GREEN_PROVEN) return false;
  const freshness = classifyGreenEvidenceFreshness(green, {});
  return freshness.current === true && freshness.stub === true;
}

export function isStatusOnlyRedStub(red) {
  if (!isObject(red)) return false;
  if (red.status !== RED_PROVEN && red.status !== ALREADY_SATISFIED) return false;
  const looksLikeReceipt =
    Array.isArray(red.test_commands) ||
    Array.isArray(red.command_executions) ||
    Number.isInteger(red.schema_version) ||
    typeof red.evidence_contract === "string";
  return !looksLikeReceipt;
}

/**
 * Operative receipt check for the stage that was just completed (or blocking).
 * Ignores process exit codes; uses artifact_status + native freshness helpers.
 */
export function evaluateOperativeReceiptForStage(stage, sliceId, ctx) {
  const { maps, design, designReview, repoRoot, upstreamBySlice } = ctx;
  if (stage === "APPLY_DESIGN") {
    const fresh = isApplyDesignAuthorityFresh(design, { repoRoot });
    const status = design?.status;
    const authorized =
      fresh.ok && OPERATIVE_SUCCESS.APPLY_DESIGN.has(status);
    return {
      authorized,
      artifact_status: status,
      code: authorized ? null : fresh.code || "STALE_OR_INVALID_APPLY_DESIGN",
    };
  }
  if (stage === "APPLY_DESIGN_REVIEW") {
    const gate = evaluateApplyDesignReviewGate({
      design,
      review: designReview,
      repoRoot,
      currentDesignArtifactSha256: maps.designArtifactSha256,
      currentPlanningDigest: design?.planning_identity?.planning_digest,
      currentProtectedContextDigest: design?.protected_context_identity?.content_digest,
    });
    const status = designReview?.status;
    return {
      authorized: gate.ok,
      artifact_status: status,
      code: gate.ok ? null : gate.code,
    };
  }
  if (stage === "APPLY_SCAFFOLD") {
    const rec = maps.scaffoldBySlice?.[sliceId];
    const status = rec?.status;
    const authorized = status === SCAFFOLD_READY;
    return {
      authorized,
      artifact_status: status,
      code: authorized ? null : "SCAFFOLD_NOT_READY",
    };
  }
  if (stage === "APPLY_RED") {
    const red = maps.redBySlice?.[sliceId];
    const status = red?.status;
    if (isStatusOnlyRedStub(red)) {
      return {
        authorized: false,
        artifact_status: status,
        code: "STATUS_ONLY_RED_STUB",
      };
    }
    const durable = isDurableRedAuthority(red, repoRoot);
    const allowedStatus = OPERATIVE_SUCCESS.APPLY_RED.has(status);
    const authorized = allowedStatus && durable;
    let code = null;
    if (!allowedStatus) code = "RED_NOT_PROVEN";
    else if (!durable) code = "RED_OPERATIVE_GATE_FAILED";
    return { authorized, artifact_status: status, code };
  }
  if (stage === "APPLY_IMPLEMENT") {
    const rec = maps.implementBySlice?.[sliceId];
    const status = rec?.status;
    const authorized = OPERATIVE_SUCCESS.APPLY_IMPLEMENT.has(status);
    return {
      authorized,
      artifact_status: status,
      code: authorized ? null : "IMPLEMENTATION_NOT_WRITTEN",
    };
  }
  if (stage === "APPLY_GREEN") {
    const green = maps.greenBySlice?.[sliceId];
    const upstream = upstreamBySlice?.[sliceId] || {};
    const freshness = classifyGreenEvidenceFreshness(green, upstream);
    if (isStatusOnlyGreenStub(green)) {
      return {
        authorized: false,
        artifact_status: green?.status,
        code: "STATUS_ONLY_GREEN_STUB",
      };
    }
    const upstreamChecks = evaluateGreenUpstreamIdentityChecks(green, upstream);
    const identityCurrent =
      freshness.current &&
      Object.values(upstreamChecks.semanticChecks).every(Boolean) &&
      Object.values(upstreamChecks.fileChecks).every(Boolean);
    const authorized = green?.status === GREEN_PROVEN && identityCurrent;
    return {
      authorized,
      artifact_status: green?.status,
      code: authorized ? null : freshness.code || "STALE_GREEN",
      freshness,
      upstream_checks: upstreamChecks,
    };
  }
  return { authorized: false, artifact_status: null, code: "UNKNOWN_STAGE" };
}

/**
 * Evidence-backed APPLY_COMPLETE gate (not archive / verify permission).
 */
export {
  evaluateApplyCompleteWithExceptionGate,
  evaluateDevelopmentCompleteWithExceptionGate,
} from "./opsx-break-glass-exception-completion.mjs";

export function evaluateApplyCompleteGate(ctx, { requireTaskProjection = true } = {}) {
  const { design, maps, repoRoot, changeRoot } = ctx;
  const breakGlassDebt = evaluateBreakGlassArchiveDebt({
    repoRoot,
    changeId: design?.change_id,
    targetStage: "APPLY_COMPLETE",
  });
  if (!breakGlassDebt.ok) {
    return breakGlassDebt;
  }
  const upstreamBySlice =
    ctx.upstreamBySlice && Object.keys(ctx.upstreamBySlice).length
      ? ctx.upstreamBySlice
      : buildUpstreamBySlice({ ...maps, repoRoot });
  if (!isObject(design) || !graphValid(design)) {
    return { ok: false, code: "INVALID_SLICE_GRAPH" };
  }
  const sliceIds = design.slice_order?.nodes || [];
  for (const id of sliceIds) {
    const impl = maps.implementBySlice?.[id];
    const green = maps.greenBySlice?.[id];
    if (
      impl?.status === "BLOCKED" &&
      green?.status === GREEN_PROVEN
    ) {
      return {
        ok: false,
        code: "BLOCKED_IMPLEMENT_CONTRADICTS_GREEN",
        slice_id: id,
      };
    }
    if (!green || green.status !== GREEN_PROVEN) {
      return { ok: false, code: "SLICE_NOT_GREEN", slice_id: id };
    }
    if (isStatusOnlyGreenStub(green)) {
      return { ok: false, code: "STATUS_ONLY_GREEN_STUB", slice_id: id };
    }
    const upstream = upstreamBySlice?.[id] || {};
    const freshness = classifyGreenEvidenceFreshness(green, upstream);
    const upstreamChecks = evaluateGreenUpstreamIdentityChecks(green, upstream);
    const identityCurrent =
      freshness.current &&
      Object.values(upstreamChecks.semanticChecks).every(Boolean) &&
      Object.values(upstreamChecks.fileChecks).every(Boolean);
    if (!identityCurrent) {
      return {
        ok: false,
        code: freshness.code || "STALE_GREEN",
        slice_id: id,
        mismatched: freshness.mismatched,
        upstream_checks: upstreamChecks,
      };
    }
  }
  const verify = evaluateSplitApplyVerifyCompletionFromDisk({
    repoRoot,
    changeRoot,
  });
  if (!verify.split_apply) {
    return { ok: false, code: "SPLIT_APPLY_NOT_MATERIALIZED" };
  }
  if (verify.marker === "IMPL_GAPS" && (verify.blocking || []).length > 0) {
    return {
      ok: false,
      code: "IMPL_GAPS_REMAIN",
      blocking: verify.blocking,
    };
  }
  if (requireTaskProjection) {
    const projection = projectSplitApplyTaskCompletion({
      design,
      greenBySlice: maps.greenBySlice,
      upstreamBySlice,
      durableProjectionPresent: existsSync(
        resourcePath(repoRoot, posixPath(changeRoot), "apply-complete/TASK-COMPLETION.json"),
      ),
    });
    const bad = (projection.tasks || []).filter(
      (t) => t.completion_status !== TASK_COMPLETE,
    );
    if (bad.length) {
      return {
        ok: false,
        code: "TASKS_NOT_RECONCILED",
        tasks: bad,
      };
    }
    if ((projection.tasks || []).some((t) => t.completion_status === TASK_STALE)) {
      return { ok: false, code: "TASK_STALE_GREEN", tasks: projection.tasks };
    }
  }
  return {
    ok: true,
    verify_marker: verify.marker,
    auto_verify_forbidden: true,
  };
}

export function primitiveForStage(stage) {
  return NATIVE_PRIMITIVE_BY_STAGE[stage] || null;
}

export function formatPrimitiveInvocation(command, { changeId, sliceId } = {}) {
  let line = `${command} change=${changeId}`;
  if (sliceId) line += ` slice=${sliceId}`;
  return line;
}

/**
 * recovery_successor only after completion consumer proves disk publication + IMPL_OK recompute.
 */
function recoverySuccessorTransitionIfConsumerReady({
  recoveryCtx,
  design,
  maps,
  repoRoot,
  changeRoot,
}) {
  if (!recoveryCtx?.ok || recoveryCtx.generation_current !== true) {
    return null;
  }
  const consumer = evaluateRecoveryCompletionConsumer({
    design,
    repoRoot,
    changeRoot,
    recoveryAttemptId: recoveryCtx.recovery_attempt_id,
    scaffoldBySlice: maps?.scaffoldBySlice,
    redBySlice: maps?.redBySlice,
    implementBySlice: maps?.implementBySlice,
    greenBySlice: maps?.greenBySlice,
    upstreamBySlice: maps?.upstreamBySlice,
  });
  if (!consumer.ok) {
    return null;
  }
  const successor = consumer.successor_plan;
  if (
    successor?.ok !== true ||
    successor.published_disk_supersession !== true ||
    successor.phase !== RECOVERY_PHASE_SUCCESSOR_PLAN ||
    successor.impl_ok_recomputed !== true
  ) {
    return null;
  }
  return {
    ok: true,
    next_stage: "RECOVERY_SUCCESSOR_BOUNDED_VERIFY",
    next_slice: recoveryCtx.recovered_slice_id,
    authorized_dispatch: false,
    recovery_successor: true,
    generation_lineage_id:
      successor.generation_lineage_id || recoveryCtx.generation_lineage_id,
  };
}

/**
 * Deterministic next hop from current receipts (reuses selectNextPipelineStage).
 */
export function selectSuperImplementTransition(ctx) {
  const { design, maps, designReview, repoRoot, changeRoot } = ctx;
  const recoveryCtx = loadRecoveryCompletionContextFromDisk({
    repoRoot,
    changeRoot,
    changeId: design?.change_id,
  });
  const recoverySuccessor = recoverySuccessorTransitionIfConsumerReady({
    recoveryCtx,
    design,
    maps,
    repoRoot,
    changeRoot,
  });
  if (recoverySuccessor) {
    return recoverySuccessor;
  }
  const stagePick = selectNextPipelineStage({
    design,
    scaffoldBySlice: maps.scaffoldBySlice,
    redBySlice: maps.redBySlice,
    implementBySlice: maps.implementBySlice,
    greenBySlice: maps.greenBySlice,
    designReview,
    repoRoot,
  });
  if (!stagePick.ok) {
    const pipelineOverride = ownerOverrideAllowsProcessGate({
      repoRoot,
      changeId: design?.change_id,
      blockedGate: "PIPELINE_SELECTOR",
      failureCode: stagePick.code || "NO_ELIGIBLE_SLICE",
    });
    if (pipelineOverride.ok) {
      return {
        ok: true,
        next_stage: "EXCEPTION_APPLY_CONTINUE",
        next_slice: stagePick.next_slice || null,
        authorized_dispatch: true,
        gate_check_status: OWNER_OVERRIDDEN,
        owner_override: pipelineOverride,
        selector_failure: stagePick,
      };
    }
    return { ok: false, code: stagePick.code || "STAGE_SELECT_FAILED" };
  }
  if (stagePick.next_stage === RECOVERY_PAUSED) {
    const recoveryOverride = ownerOverrideAllowsProcessGate({
      repoRoot,
      changeId: design?.change_id,
      blockedGate: "POST_IMPLEMENT_RECOVERY_SELECTOR",
      failureCode: stagePick.recovery_code || stagePick.selector_parity?.code || "UNKNOWN",
    });
    if (recoveryOverride.ok) {
      return {
        ok: true,
        next_stage: RECOVERY_PAUSED,
        next_slice: stagePick.next_slice,
        authorized_dispatch: true,
        gate_check_status: OWNER_OVERRIDDEN,
        owner_override: recoveryOverride,
        recovery_code: stagePick.recovery_code || stagePick.selector_parity?.code,
      };
    }
    const sliceId = stagePick.next_slice;
    const red = maps.redBySlice?.[sliceId];
    const implement = maps.implementBySlice?.[sliceId];
    const recovery =
      red && implement
        ? classifyPostImplementGreenRecovery({
            red,
            implement,
            green: maps.greenBySlice?.[sliceId],
            repoRoot,
          })
        : {
            code: stagePick.recovery_code || stagePick.selector_parity?.code,
            disposition: "PAUSE",
          };
    const semanticGate = evaluateSemanticEquivalenceGate({
      recovery,
      red,
      implement,
      repoRoot,
    });
    const recoveryCode =
      stagePick.recovery_code || recovery.code || stagePick.selector_parity?.code;
    if (!semanticGate.owner_decision_required) {
      const baseContract = {
        code: recoveryCode,
        disposition: stagePick.disposition || recovery.disposition || "PAUSE",
        semantic_outcome: semanticGate.outcome,
        semantic_gate_detail: semanticGate.detail,
        unauthorized_generation: true,
      };
      if (recovery.implement_frozen || implement?.frozen_test_content_identity) {
        baseContract.implement_frozen =
          recovery.implement_frozen || implement.frozen_test_content_identity;
      }
      if (recovery.legacy_on_disk_digest) {
        baseContract.legacy_on_disk_digest = recovery.legacy_on_disk_digest;
      }
      if (recovery.canonical_live_digest) {
        baseContract.canonical_live_digest = recovery.canonical_live_digest;
      }
      if (semanticGate.outcome === "AUTO_PROVEN") {
        let nextStage = "APPLY_GREEN";
        let nextSlice = sliceId;
        if (recovery.disposition === "GREEN_REFRESH_AUTHORIZED") {
          nextStage = "APPLY_GREEN";
        } else if (
          recovery.disposition === "FROZEN_TEST_MUTATED_REQUIRES_RE_RED" &&
          !postImplementRecoveryBlocksOperativeRedRoute({
            red,
            implement,
            recovery,
            repoRoot,
          })
        ) {
          nextStage = "APPLY_RED";
        }
        const primitive = primitiveForStage(nextStage);
        const allow = assertDispatchCommandAllowed(primitive);
        if (allow.ok) {
          return {
            ok: true,
            next_stage: nextStage,
            next_slice: nextSlice,
            recovery_code: recoveryCode,
            selector_parity: stagePick.selector_parity,
            authorized_dispatch: true,
            primitive_command: primitive,
            recovery_contract: {
              ...baseContract,
              next_action: semanticGate.detail,
            },
          };
        }
      }
      if (semanticGate.outcome === "DESIGN_FUTURE_SLICE_REPAIR") {
        const primitive = primitiveForStage("APPLY_DESIGN");
        const allow = assertDispatchCommandAllowed(primitive);
        if (allow.ok) {
          return {
            ok: true,
            next_stage: "APPLY_DESIGN",
            next_slice: null,
            recovery_code: recoveryCode,
            selector_parity: stagePick.selector_parity,
            authorized_dispatch: true,
            primitive_command: primitive,
            recovery_contract: {
              ...baseContract,
              next_action: semanticGate.detail,
            },
          };
        }
      }
      if (semanticGate.outcome === "OPERATIVE_RE_RED_REQUIRED") {
        if (
          !postImplementRecoveryBlocksOperativeRedRoute({
            red,
            implement,
            recovery,
            repoRoot,
          })
        ) {
          const primitive = primitiveForStage("APPLY_RED");
          const allow = assertDispatchCommandAllowed(primitive);
          if (allow.ok) {
            return {
              ok: true,
              next_stage: "APPLY_RED",
              next_slice: sliceId,
              recovery_code: recoveryCode,
              selector_parity: stagePick.selector_parity,
              authorized_dispatch: true,
              primitive_command: primitive,
              recovery_contract: {
                ...baseContract,
                next_action: semanticGate.detail,
              },
            };
          }
        }
      }
      return {
        ok: true,
        next_stage: RECOVERY_PAUSED,
        next_slice: stagePick.next_slice,
        recovery_code: recoveryCode,
        selector_parity: stagePick.selector_parity,
        authorized_dispatch: false,
        recovery_contract: {
          ...baseContract,
          next_action: semanticGate.detail || recoveryCode,
        },
      };
    }
    const ownerPacket = buildOwnerDecisionPacket({
      changeId: design?.change_id,
      sliceId,
      recovery,
      semanticGate,
      recoveryPath: classifyPostImplementRecoveryPath(recovery, {
        red,
        implement,
        repoRoot,
      }),
    });
    return {
      ok: true,
      next_stage: RECOVERY_PAUSED,
      next_slice: stagePick.next_slice,
      recovery_code: recoveryCode,
      selector_parity: stagePick.selector_parity,
      authorized_dispatch: false,
      recovery_contract: {
        code: recoveryCode,
        disposition: stagePick.disposition || "PAUSE",
        next_action: "Owner planning supersession or TEST_CONTRACT_REBASELINE",
        owner_decision_packet: ownerPacket,
        unauthorized_generation: true,
      },
    };
  }
  if (stagePick.next_stage === "APPLY_COMPLETE") {
    const complete = evaluateApplyCompleteGate(ctx);
    if (!complete.ok) {
      return {
        ok: true,
        next_stage: "APPLY_GREEN",
        next_slice: stagePick.next_slice,
        blocked_complete: complete,
      };
    }
    return {
      ok: true,
      next_stage: "APPLY_COMPLETE",
      next_slice: null,
      complete_gate: complete,
    };
  }
  const primitive = primitiveForStage(stagePick.next_stage);
  const allow = assertDispatchCommandAllowed(primitive);
  if (!allow.ok) {
    return { ok: false, code: allow.code, command: primitive };
  }
  return {
    ok: true,
    next_stage: stagePick.next_stage,
    next_slice: stagePick.next_slice,
    primitive_command: primitive,
  };
}

export function preventDuplicateDispatch(run) {
  if (run?.in_flight_dispatch) {
    return {
      ok: false,
      code: "DISPATCH_ALREADY_IN_FLIGHT",
      in_flight: run.in_flight_dispatch,
      recovery_contract: {
        code: "DISPATCH_ALREADY_IN_FLIGHT",
        next_action:
          "reconcile worker outcome or clear in_flight_dispatch after durable evidence",
        resume_requires: ["in_flight_cleared_or_reconciled"],
      },
    };
  }
  return { ok: true };
}

export function defaultRun(ctx, opts = {}) {
  const { design, changeRoot, changeId } = ctx;
  return {
    schema: SUPER_IMPLEMENT_SCHEMA,
    schema_version: SUPER_IMPLEMENT_SCHEMA_VERSION,
    change_id: changeId,
    change_root: changeRoot,
    status: RUN_STATUS_RUNNING,
    live_execution_authorized:
      opts.liveExecutionAuthorized === true
        ? true
        : LIVE_EXECUTION_DEFAULT_AUTHORIZED,
    canonical_branch: opts.canonicalBranch || null,
    expected_head: opts.expectedHead || null,
    execution_identity: computeSuperImplementExecutionIdentity({
      changeId,
      planningDigest: design?.planning_identity?.planning_digest,
      repositoryHead: opts.repositoryHead || design?.repository_identity?.head,
      runSequence: 0,
    }),
    run_ledger_id: randomBytes(16).toString("hex"),
    writer_lease_id: randomBytes(16).toString("hex"),
    ledger_revision: 0,
    in_flight_dispatch: null,
    last_transition: { at: nowIso(), kind: "RUN_CREATED", stage: null, slice_id: null, code: null },
    recovery_contract: null,
    history: [],
  };
}

/**
 * Ensure durable ledger identity on legacy RUN.json (per-run writer lease).
 */
export function normalizeRunLedger(run) {
  if (!run || run.schema !== SUPER_IMPLEMENT_SCHEMA) return run;
  let next = run;
  if (!next.run_ledger_id) {
    next = { ...next, run_ledger_id: randomBytes(16).toString("hex") };
  }
  if (!next.writer_lease_id) {
    next = { ...next, writer_lease_id: randomBytes(16).toString("hex") };
  }
  if (next.ledger_revision == null) {
    next = { ...next, ledger_revision: 0 };
  }
  return next;
}

export function loadOrCreateRun(repoRoot, changeRoot, ctx, opts = {}) {
  const rel = superImplementRunRelPath(changeRoot);
  const abs = resourcePath(repoRoot, rel);
  if (existsSync(abs)) {
    return normalizeRunLedger(JSON.parse(readFileSync(abs, "utf8")));
  }
  return defaultRun(ctx, opts);
}

/**
 * Atomic RUN.json replace with ledger revision + run_ledger_id conflict detection.
 */
export function persistRun(repoRoot, changeRoot, run) {
  const rel = superImplementRunRelPath(changeRoot);
  const abs = resourcePath(repoRoot, rel);
  mkdirSync(dirname(abs), { recursive: true });
  let disk = null;
  if (existsSync(abs)) {
    try {
      disk = JSON.parse(readFileSync(abs, "utf8"));
    } catch {
      return { ok: false, code: "RUN_LEDGER_CORRUPT", path: rel };
    }
    const diskId = disk.run_ledger_id;
    const runId = run.run_ledger_id;
    if (diskId && runId && diskId !== runId) {
      return {
        ok: false,
        code: "RUN_LEDGER_ID_CONFLICT",
        disk_ledger_id: diskId,
        run_ledger_id: runId,
      };
    }
    const diskRev = disk.ledger_revision ?? 0;
    if (run.ledger_revision == null) {
      run = { ...run, ledger_revision: diskRev };
    }
    if (run.ledger_revision !== diskRev) {
      return {
        ok: false,
        code: "RUN_LEDGER_REVISION_CONFLICT",
        disk_revision: diskRev,
        run_revision: run.ledger_revision,
      };
    }
    run = {
      ...run,
      run_ledger_id: diskId || runId || randomBytes(16).toString("hex"),
      ledger_revision: diskRev + 1,
    };
  } else {
    run = {
      ...run,
      run_ledger_id: run.run_ledger_id || randomBytes(16).toString("hex"),
      writer_lease_id: run.writer_lease_id || randomBytes(16).toString("hex"),
      ledger_revision: (run.ledger_revision ?? 0) + 1,
    };
  }
  const body = `${JSON.stringify(run, null, 2)}\n`;
  const tmp = `${abs}.${randomBytes(4).toString("hex")}.tmp`;
  writeFileSync(tmp, body, "utf8");
  renameSync(tmp, abs);
  return { ok: true, path: abs, run };
}

/**
 * Persist RUN.json or fail-closed with PAUSED-shaped run on ledger conflict.
 */
export function applyPersistedRun(repoRoot, changeRoot, run) {
  const persisted = persistRun(repoRoot, changeRoot, run);
  if (!persisted.ok) {
    return {
      ok: false,
      code: persisted.code,
      run: {
        ...run,
        status: RUN_STATUS_PAUSED,
        recovery_contract: {
          code: persisted.code,
          next_action:
            "reload RUN.json and reconcile concurrent Super writers before continuing",
          resume_requires: ["run_ledger_reconciled"],
        },
      },
      persist: persisted,
    };
  }
  return { ok: true, run: persisted.run };
}

/**
 * Plan one sequencer hop (no live primitive execution).
 */
export function planSuperImplementStep({
  repoRoot = DEFAULT_ROOT,
  changeRoot,
  run = null,
  currentHead = null,
  currentBranch = null,
  liveExecutionAuthorized = LIVE_EXECUTION_DEFAULT_AUTHORIZED,
} = {}) {
  const ctx = loadSuperImplementContext({ repoRoot, changeRoot });
  if (!ctx.ok) {
    if (ctx.code === "APPLY_DESIGN_MISSING") {
      const primitive = NATIVE_PRIMITIVE_BY_STAGE.APPLY_DESIGN;
      const invocation = formatPrimitiveInvocation(primitive, {
        changeId: changeRoot.split("/").filter(Boolean).at(-1) || null,
        sliceId: null,
      });
      return {
        status: RUN_STATUS_RUNNING,
        plan_only: true,
        next_stage: "APPLY_DESIGN",
        next_slice: null,
        primitive_command: primitive,
        primitive_invocation: invocation,
        code: ctx.code,
        live_execution_authorized: liveExecutionAuthorized === true,
        live_dispatch_would_run: liveExecutionAuthorized === true ? invocation : null,
      };
    }
    return { status: "FAILED", code: ctx.code, live_execution_authorized: false };
  }
  const branch = evaluateBranchPreflight({
    currentHead: currentHead || ctx.design?.repository_identity?.head,
    expectedHead: run?.expected_head,
    canonicalBranch: run?.canonical_branch,
    currentBranch,
  });
  if (!branch.ok) {
    return {
      status: RUN_STATUS_PAUSED,
      code: branch.code,
      recovery_contract: branch.recovery_contract,
      live_execution_authorized: false,
    };
  }
  const ledger = run || defaultRun(ctx, {
    liveExecutionAuthorized,
    expectedHead: run?.expected_head,
    canonicalBranch: run?.canonical_branch,
    repositoryHead: currentHead,
  });
  const dup = preventDuplicateDispatch(ledger);
  if (!dup.ok) {
    return {
      status: RUN_STATUS_PAUSED,
      code: dup.code,
      recovery_contract: dup.recovery_contract,
      in_flight: dup.in_flight,
      live_execution_authorized: ledger.live_execution_authorized,
    };
  }
  const transition = selectSuperImplementTransition(ctx);
  if (!transition.ok) {
    return {
      status: RUN_STATUS_FAILED,
      code: transition.code,
      live_execution_authorized: ledger.live_execution_authorized,
    };
  }
  if (transition.next_stage === "APPLY_COMPLETE") {
    return {
      status: RUN_STATUS_APPLY_COMPLETE,
      next_stage: "APPLY_COMPLETE",
      complete_gate: transition.complete_gate,
      dispatch: null,
      live_execution_authorized: ledger.live_execution_authorized,
      auto_verify_forbidden: true,
      auto_archive_forbidden: true,
    };
  }
  if (
    transition.next_stage === RECOVERY_PAUSED &&
    transition.authorized_dispatch !== true
  ) {
    const recoveryCode =
      transition.recovery_code ||
      transition.recovery_contract?.code ||
      transition.selector_parity?.code ||
      "RECOVERY_PAUSED";
    return {
      status: RUN_STATUS_PAUSED,
      plan_only: true,
      code: recoveryCode,
      recovery_code: recoveryCode,
      next_stage: RECOVERY_PAUSED,
      next_slice: transition.next_slice ?? null,
      recovery_contract: transition.recovery_contract ?? null,
      selector_parity: transition.selector_parity ?? null,
      authorized_dispatch: false,
      live_execution_authorized: ledger.live_execution_authorized,
      live_dispatch_would_run: null,
      tier_substitution_forbidden: true,
    };
  }
  const operative = evaluateOperativeReceiptForStage(
    transition.next_stage,
    transition.next_slice,
    ctx,
  );
  const dispatchId = createHash("sha256")
    .update(
      stableStringify({
        stage: transition.next_stage,
        slice: transition.next_slice,
        seq: ledger.execution_identity?.run_sequence ?? 0,
      }),
      "utf8",
    )
    .digest("hex")
    .slice(0, 16);
  const invocation = formatPrimitiveInvocation(transition.primitive_command, {
    changeId: ctx.changeId,
    sliceId: transition.next_slice,
  });
  return {
    status: RUN_STATUS_RUNNING,
    plan_only: true,
    next_stage: transition.next_stage,
    next_slice: transition.next_slice,
    primitive_command: transition.primitive_command,
    primitive_invocation: invocation,
    dispatch_id: dispatchId,
    prior_stage_operative: operative,
    blocked_complete: transition.blocked_complete || null,
    live_execution_authorized: ledger.live_execution_authorized,
    live_dispatch_would_run:
      ledger.live_execution_authorized === true ? invocation : null,
    tier_substitution_forbidden: true,
  };
}

/**
 * Record in-flight dispatch (atomic resume policy: never double-book).
 */
export function beginInFlightDispatch(run, { dispatchId, stage, sliceId, command }) {
  const allow = assertDispatchCommandAllowed(command);
  if (!allow.ok) {
    return { ok: false, code: allow.code };
  }
  const dup = preventDuplicateDispatch(run);
  if (!dup.ok) return dup;
  const next = {
    ...run,
    in_flight_dispatch: {
      dispatch_id: dispatchId,
      stage,
      slice_id: sliceId,
      primitive_command: command,
      started_at: nowIso(),
    },
    last_transition: {
      at: nowIso(),
      kind: "DISPATCH_STARTED",
      stage,
      slice_id: sliceId,
      code: null,
    },
  };
  return { ok: true, run: next };
}

/**
 * Complete or fail a hop after worker evidence is reconciled.
 */
export function completeInFlightDispatch(run, ctx, outcome) {
  const inflight = run.in_flight_dispatch;
  if (!inflight) {
    return {
      ok: false,
      code: "NO_IN_FLIGHT_DISPATCH",
      recovery_contract: {
        code: "NO_IN_FLIGHT_DISPATCH",
        next_action: "plan next hop or resume from RUN.json",
        resume_requires: ["reconcile_run_ledger"],
      },
    };
  }
  const operative = evaluateOperativeReceiptForStage(
    inflight.stage,
    inflight.slice_id,
    ctx,
  );
  let authorized = false;
  if (outcome?.uncertain === true) {
    return {
      ok: false,
      uncertain: true,
      operative,
      run: {
        ...run,
        status: RUN_STATUS_PAUSED,
        in_flight_dispatch: inflight,
        recovery_contract: {
          code: "IN_FLIGHT_UNCERTAIN",
          next_action:
            "reconcile executor ledger or receipt files before any new dispatch",
          resume_requires: ["in_flight_reconciled"],
        },
      },
      recovery_contract: {
        code: "IN_FLIGHT_UNCERTAIN",
        next_action:
          "reconcile executor ledger or receipt files before any new dispatch",
        resume_requires: ["in_flight_reconciled"],
      },
    };
  }
  if (outcome?.worker_claim === "success") {
    authorized = operative.authorized;
  } else {
    authorized = false;
  }
  const historyEntry = {
    sequence: run.execution_identity?.run_sequence ?? 0,
    stage: inflight.stage,
    primitive_command: inflight.primitive_command,
    outcome: authorized ? "AUTHORIZED" : "REJECTED",
    artifact_status: operative.artifact_status,
    code: operative.code,
    exit_code_ignored: outcome?.exit_code != null,
  };
  const seq = (run.execution_identity?.run_sequence ?? 0) + 1;
  const nextRun = {
    ...run,
    in_flight_dispatch: null,
    execution_identity: {
      ...run.execution_identity,
      run_sequence: seq,
    },
    history: [...(run.history || []), historyEntry],
    last_transition: {
      at: nowIso(),
      kind: authorized ? "STAGE_AUTHORIZED" : "STAGE_REJECTED",
      stage: inflight.stage,
      slice_id: inflight.slice_id,
      code: operative.code,
    },
  };
  if (!authorized) {
    nextRun.status = RUN_STATUS_PAUSED;
    nextRun.recovery_contract = {
      code: operative.code || "OPERATIVE_RECEIPT_REJECTED",
      next_action: `re-run ${inflight.primitive_command} or repair receipt`,
      resume_requires: ["operative_receipt_valid"],
    };
    return { ok: false, run: nextRun, operative, recovery_contract: nextRun.recovery_contract };
  }
  const transition = selectSuperImplementTransition(ctx);
  if (transition.next_stage === "APPLY_COMPLETE" && transition.complete_gate?.ok) {
    nextRun.status = RUN_STATUS_APPLY_COMPLETE;
    nextRun.recovery_contract = null;
  } else {
    nextRun.status = RUN_STATUS_RUNNING;
    nextRun.recovery_contract = null;
  }
  return { ok: true, run: nextRun, operative, transition };
}

export function advanceSuperImplement({
  repoRoot = DEFAULT_ROOT,
  changeRoot,
  outcome = null,
  liveExecutionAuthorized = LIVE_EXECUTION_DEFAULT_AUTHORIZED,
} = {}) {
  const ctx = loadSuperImplementContext({ repoRoot, changeRoot });
  if (!ctx.ok) {
    return { status: RUN_STATUS_FAILED, code: ctx.code };
  }
  let run = loadOrCreateRun(repoRoot, changeRoot, ctx, { liveExecutionAuthorized });
  if (run.status === RUN_STATUS_APPLY_COMPLETE) {
    return { status: RUN_STATUS_APPLY_COMPLETE, run, idempotent: true };
  }
  if (outcome && run.in_flight_dispatch) {
    const done = completeInFlightDispatch(run, ctx, outcome);
    run = done.run;
    const saved = applyPersistedRun(repoRoot, changeRoot, run);
    run = saved.run;
    return {
      status: run.status,
      ...done,
      persisted: saved.ok,
      persist_code: saved.ok ? null : saved.code,
    };
  }
  const plan = planSuperImplementStep({
    repoRoot,
    changeRoot,
    run,
    liveExecutionAuthorized: run.live_execution_authorized,
  });
  if (plan.status === RUN_STATUS_APPLY_COMPLETE) {
    run.status = RUN_STATUS_APPLY_COMPLETE;
    run.last_transition = {
      at: nowIso(),
      kind: "APPLY_COMPLETE",
      stage: "APPLY_COMPLETE",
      slice_id: null,
      code: null,
    };
    const saved = applyPersistedRun(repoRoot, changeRoot, run);
    run = saved.run;
    return {
      status: saved.ok ? RUN_STATUS_APPLY_COMPLETE : RUN_STATUS_PAUSED,
      run,
      plan,
      persist_code: saved.ok ? null : saved.code,
    };
  }
  if (!run.live_execution_authorized) {
    return {
      status: RUN_STATUS_PAUSED,
      code: "LIVE_EXECUTION_NOT_AUTHORIZED",
      plan,
      run,
      recovery_contract: {
        code: "LIVE_EXECUTION_NOT_AUTHORIZED",
        next_action: "set live_execution_authorized after Owner attestation",
        resume_requires: ["owner_execution_attestation"],
      },
    };
  }
  return { status: RUN_STATUS_PAUSED, code: "LIVE_DISPATCH_NOT_IMPLEMENTED", plan, run };
}

function parseArgs(argv) {
  const out = { command: null, changeRoot: null, live: false, adapter: "none", maxHops: 64 };
  const cmd = argv[0];
  if (["plan", "status", "advance", "adopt", "run"].includes(cmd)) out.command = cmd;
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--change-root" && argv[i + 1]) out.changeRoot = argv[i + 1];
    if (argv[i] === "--live-execution") out.live = argv[i + 1] === "true";
    if (argv[i] === "--adapter" && argv[i + 1]) out.adapter = argv[i + 1];
    if (argv[i] === "--max-hops" && argv[i + 1]) out.maxHops = Number(argv[i + 1]);
  }
  return out;
}

async function main(argv) {
  const args = parseArgs(argv);
  if (!args.command || !args.changeRoot) {
    process.stdout.write(
      JSON.stringify({
        status: "FAIL",
        errors: [{ code: "usage", message: "plan|status|advance --change-root <rel>" }],
      }) + "\n",
    );
    process.exit(1);
  }
  if (args.command === "plan") {
    const result = planSuperImplementStep({
      changeRoot: args.changeRoot,
      liveExecutionAuthorized: args.live,
    });
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    process.exit(result.status === RUN_STATUS_FAILED ? 1 : 0);
  }
  if (args.command === "status") {
    const ctx = loadSuperImplementContext({ changeRoot: args.changeRoot });
    const run = existsSync(
      join(DEFAULT_ROOT, superImplementRunRelPath(args.changeRoot)),
    )
      ? JSON.parse(
          readFileSync(join(DEFAULT_ROOT, superImplementRunRelPath(args.changeRoot)), "utf8"),
        )
      : null;
    const transition = ctx.ok ? selectSuperImplementTransition(ctx) : null;
    process.stdout.write(
      JSON.stringify(
        {
          context_ok: ctx.ok,
          run,
          transition,
          live_execution_authorized: run?.live_execution_authorized ?? false,
        },
        null,
        2,
      ) + "\n",
    );
    process.exit(0);
  }
  if (args.command === "advance") {
    const adv = advanceSuperImplement({
      changeRoot: args.changeRoot,
      liveExecutionAuthorized: args.live,
    });
    process.stdout.write(`${JSON.stringify(adv, null, 2)}\n`);
    process.exit(adv.status === RUN_STATUS_FAILED ? 1 : 0);
  }
  if (args.command === "adopt") {
    const { ensureMidProcessAdoption, evaluateMidProcessAdoption } = await import(
      "./opsx-super-implement-adoption.mjs"
    );
    const ctx = loadSuperImplementContext({ changeRoot: args.changeRoot });
    if (!ctx.ok) {
      process.stdout.write(JSON.stringify({ status: "FAILED", code: ctx.code }) + "\n");
      process.exit(1);
    }
    const run = loadOrCreateRun(DEFAULT_ROOT, args.changeRoot, ctx, {
      liveExecutionAuthorized: args.live,
    });
    const dry = evaluateMidProcessAdoption(ctx);
    const materialized = ensureMidProcessAdoption({
      repoRoot: DEFAULT_ROOT,
      changeRoot: args.changeRoot,
      run,
    });
    process.stdout.write(
      JSON.stringify({ evaluation: dry, materialized }, null, 2) + "\n",
    );
    process.exit(materialized.ok ? 0 : 1);
  }
  const { runSuperImplementAutoLoop } = await import("./opsx-super-implement-loop.mjs");
  const {
    createV1CursorSdkExecutorAdapter,
    createRecordingExecutorAdapter,
  } = await import("./opsx-super-implement-executor.mjs");
  const { createNativeToolExecutorAdapter } = await import("./opsx-super-implement-live-v1.mjs");
  let executorAdapter = null;
  if (args.adapter === "cursor-sdk") {
    executorAdapter = createV1CursorSdkExecutorAdapter({
      repoRoot: DEFAULT_ROOT,
      readinessGate: () => ({ ok: true }),
    });
  } else if (args.adapter === "native-tool") {
    executorAdapter = createNativeToolExecutorAdapter({
      repoRoot: DEFAULT_ROOT,
      changeRoot: args.changeRoot,
    });
  } else if (args.adapter === "recording") {
    executorAdapter = createRecordingExecutorAdapter();
  } else {
    process.stdout.write(
      JSON.stringify({
        status: "FAIL",
        code: "ADAPTER_REQUIRED",
        message:
          "run requires --adapter recording|native-tool|cursor-sdk and --live-execution true",
      }) + "\n",
    );
    process.exit(1);
  }
  const loopResult = await runSuperImplementAutoLoop({
    repoRoot: DEFAULT_ROOT,
    changeRoot: args.changeRoot,
    executorAdapter,
    liveExecutionAuthorized: args.live,
    maxHops: args.maxHops,
  });
  process.stdout.write(`${JSON.stringify(loopResult, null, 2)}\n`);
  process.exit(loopResult.status === RUN_STATUS_FAILED ? 1 : 0);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((err) => {
    process.stdout.write(
      JSON.stringify({ status: "FAIL", code: "UNHANDLED", message: String(err) }) + "\n",
    );
    process.exit(1);
  });
}
