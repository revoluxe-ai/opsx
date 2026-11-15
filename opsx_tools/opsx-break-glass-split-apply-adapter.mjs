/**
 * V1 native consumer: Split-APPLY selector + Break Glass exception route (hardened).
 */
import {
  RECOVERY_PAUSED,
  selectNextPipelineStage,
} from "./opsx-apply-pipeline-identity.mjs";
import {
  RECOVERY_CLOSEOUT_FAILURE_CODES,
  materializeExceptionCloseoutReceipt,
} from "./opsx-break-glass-exception-closeout.mjs";
import { blockTerminalSuccessIfBreakGlassDebt } from "./opsx-break-glass-archive-gate.mjs";
import {
  BREAK_GLASS_MARKER,
  OWNER_OVERRIDDEN,
  RECEIPT_CONTINUE_WITH_EXCEPTION,
  STATE_BLOCKED,
  STATE_CONTINUE_WITH_EXCEPTION,
  applyBreakGlassException,
  classifyBreakGlassEligibility,
  evaluateBreakGlassActivation,
  loadTrustedBreakGlassGrant,
  resolveAuthoritativeExecutionIdentity,
} from "./opsx-break-glass.mjs";
import { ownerOverrideAllowsProcessGate } from "./opsx-break-glass-universal-v2.mjs";
import { executeExceptionApplyImplement } from "./opsx-break-glass-exception-implement.mjs";
import {
  COMPLETION_APPLY_WITH_EXCEPTION,
  COMPLETION_DEVELOPMENT_WITH_EXCEPTION,
  materializeExceptionCompletionEvidence,
} from "./opsx-break-glass-exception-completion.mjs";

export const PROCESS_BLOCKED_GATE = "APPLY_RED_OPERATIVE";
export const PILOT_FAILURE_CODE = "SEMANTIC_ORACLE_UNPROVEN";

export function classifySplitApplyProcessBlock({
  stagePick,
  operativeRedGateError,
  recoveryCloseoutAttested = false,
} = {}) {
  if (operativeRedGateError?.code === PILOT_FAILURE_CODE) {
    return {
      blocked: true,
      blocked_gate: PROCESS_BLOCKED_GATE,
      failure_code: PILOT_FAILURE_CODE,
      failed_command: "/opsx-apply-red",
    };
  }
  if (stagePick?.ok && stagePick.next_stage === RECOVERY_PAUSED) {
    const recoveryCode =
      stagePick.recovery_code || stagePick.selector_parity?.code || stagePick.code;
    if (RECOVERY_CLOSEOUT_FAILURE_CODES.has(recoveryCode)) {
      return {
        blocked: true,
        blocked_gate: "POST_IMPLEMENT_RECOVERY_SELECTOR",
        failure_code: recoveryCode,
        failed_command: "selectNextPipelineStage",
        requires_process_attestation: true,
        recovery_slice: stagePick.next_slice,
        recovery_closeout: true,
      };
    }
  }
  if (!stagePick?.ok && stagePick?.code === "NO_ELIGIBLE_SLICE") {
    return {
      blocked: true,
      blocked_gate: "PIPELINE_SELECTOR",
      failure_code: "NO_ELIGIBLE_SLICE",
      failed_command: "selectNextPipelineStage",
      requires_process_attestation: true,
    };
  }
  return { blocked: false };
}

export function selectSplitApplyStageWithBreakGlass(opts = {}) {
  const {
    repoRoot,
    grantId = null,
    grant = null,
    markerPresent = false,
    changeId,
    executionId,
    operativeRedGateError = null,
    processDefectAttested = false,
    processDefectPredicate = null,
    recoveryCloseoutAttested = false,
    changeRoot = "openspec/changes/break-glass-pilot",
    sliceId = null,
    runImplement = false,
    runCloseout = false,
    closeoutTargets = null,
    upstreamBySlice = {},
    redBySlice = {},
    implementBySlice = {},
    greenBySlice = {},
  } = opts;

  const stagePick = selectNextPipelineStage(opts);
  const block = classifySplitApplyProcessBlock({
    stagePick,
    operativeRedGateError,
    recoveryCloseoutAttested,
  });

  if (!block.blocked) {
    const debt = blockTerminalSuccessIfBreakGlassDebt({
      repoRoot,
      changeId,
      nextStage: stagePick?.next_stage,
    });
    if (!debt.ok) {
      return { ok: false, ...debt, route: "NORMAL", stage: stagePick };
    }
    return { ok: true, route: "NORMAL", stage: stagePick };
  }

  const ownerOverride = ownerOverrideAllowsProcessGate({
    repoRoot,
    changeId,
    executionId,
    branch: opts.branch,
    blockedGate: block.blocked_gate,
    failureCode: block.failure_code,
  });
  if (ownerOverride.ok) {
    return {
      ok: true,
      route: "OWNER_OVERRIDE",
      gate_check_status: OWNER_OVERRIDDEN,
      exception_state: STATE_CONTINUE_WITH_EXCEPTION,
      incident_id: ownerOverride.incident_id,
      grant: ownerOverride.grant,
      next_stage: "EXCEPTION_APPLY_CONTINUE",
      allowed_follow_on: ["APPLY_IMPLEMENT"],
      red_debt_remains: true,
      stage: stagePick,
      blocked: block,
      original_gate_result: {
        status: "FAILED",
        blocked_gate: block.blocked_gate,
        failure_code: block.failure_code,
      },
    };
  }

  const eligibility = classifyBreakGlassEligibility({
    blockedGate: block.blocked_gate,
    failureCode: block.failure_code,
    processDefectAttested:
      processDefectAttested ||
      recoveryCloseoutAttested ||
      !block.requires_process_attestation,
    processDefectPredicate,
  });
  if (!eligibility.eligible) {
    return {
      ok: false,
      code: eligibility.disposition,
      reason: eligibility.code,
      exception_state: STATE_BLOCKED,
      stage: stagePick,
      blocked: block,
    };
  }

  const runtimeIdentity = resolveAuthoritativeExecutionIdentity({
    repoRoot,
    changeId,
    executionId,
    blockedGate: block.blocked_gate,
    failureCode: block.failure_code,
    branch: opts.branch,
    revision: opts.revision,
    repo: opts.repo,
  });
  if (!runtimeIdentity.ok) {
    return { ok: false, code: STATE_BLOCKED, reason: runtimeIdentity.code, stage: stagePick };
  }
  runtimeIdentity.identity.process_defect_attested = processDefectAttested;
  runtimeIdentity.identity.process_defect_predicate = processDefectPredicate;

  let loadedGrant = grant;
  if (grantId) {
    const loaded = loadTrustedBreakGlassGrant(repoRoot, grantId);
    if (!loaded.ok) return { ok: false, ...loaded, stage: stagePick, blocked: block };
    loadedGrant = loaded.grant;
  }
  if (!loadedGrant) {
    return {
      ok: false,
      code: STATE_BLOCKED,
      reason: "BREAK_GLASS_GRANT_REQUIRED",
      marker: BREAK_GLASS_MARKER,
      stage: stagePick,
      blocked: block,
    };
  }

  const activationOnly = evaluateBreakGlassActivation({
    markerPresent,
    grant: loadedGrant,
    repoRoot,
    runtimeIdentity,
  });
  if (!activationOnly.ok) {
    return { ok: false, ...activationOnly, stage: stagePick, blocked: block };
  }

  const applied = applyBreakGlassException({
    repoRoot,
    grant: loadedGrant,
    markerPresent,
    runtimeIdentity,
    failedCommand: block.failed_command,
    expectedBehavior: "operative RED gate evaluates frozen tests",
    observedBehavior: `process gate failed: ${block.failure_code}`,
    whyProcessDefect: processDefectPredicate?.independent_witness_ref || "attested process defect",
  });
  if (!applied.ok) {
    return { ok: false, ...applied, stage: stagePick, blocked: block };
  }

  let implementResult = null;
  let closeoutResults = null;
  const effectiveSlice = sliceId || stagePick?.next_slice || block.recovery_slice || "SLICE-001";
  if (runCloseout) {
    const targets =
      closeoutTargets ||
      (block.recovery_slice ? [block.recovery_slice] : [effectiveSlice]);
    closeoutResults = [];
    for (const sid of targets) {
      const closeout = materializeExceptionCloseoutReceipt({
        repoRoot,
        changeRoot,
        sliceId: sid,
        grant: loadedGrant,
        exceptionReceipt: applied.exception_receipt,
        incidentId: applied.incident_id,
        runtimeIdentity,
        red: redBySlice[sid] || opts.redBySlice?.[sid],
        implement: implementBySlice[sid] || opts.implementBySlice?.[sid],
        green: greenBySlice[sid] || opts.greenBySlice?.[sid],
        upstream: upstreamBySlice[sid] || opts.upstreamBySlice?.[sid] || {},
      });
      if (!closeout.ok) {
        return { ok: false, ...closeout, stage: stagePick, incident_id: applied.incident_id };
      }
      closeoutResults.push(closeout);
    }
  }
  if (runImplement) {
    const productionBody =
      opts.productionBody ||
      `export function add(a, b) {\n  return a + b;\n}\n`;
    implementResult = executeExceptionApplyImplement({
      repoRoot,
      changeRoot,
      sliceId: effectiveSlice,
      grant: loadedGrant,
      exceptionReceipt: applied.exception_receipt,
      productionRelPath: opts.productionRelPath || `${changeRoot}/src/pilot_product.mjs`,
      productionBody,
      contractTestRelPath:
        opts.contractTestRelPath ||
        "tests/unit/fixtures/breakGlassNativePilot/contract_pilot_product.test.mjs",
    });
    if (!implementResult.ok) {
      return { ok: false, ...implementResult, stage: stagePick, incident_id: applied.incident_id };
    }
    const completion = materializeExceptionCompletionEvidence({
      repoRoot,
      changeRoot,
      sliceId: effectiveSlice,
      grant: loadedGrant,
      exceptionReceipt: applied.exception_receipt,
      incidentId: applied.incident_id,
      implementationPath: implementResult.implementation_path,
      contractTestReceipt: implementResult.contract_test_receipt,
      nativeImplementCli: implementResult.native_implement_cli,
    });
    if (!completion.ok) {
      return { ok: false, ...completion, stage: stagePick, incident_id: applied.incident_id };
    }
    implementResult.exception_completion = completion;
  }

  return {
    ok: true,
    route: "EXCEPTION",
    exception_state: STATE_CONTINUE_WITH_EXCEPTION,
    next_stage: "EXCEPTION_APPLY_CONTINUE",
    completion_stage: COMPLETION_APPLY_WITH_EXCEPTION,
    development_stage: COMPLETION_DEVELOPMENT_WITH_EXCEPTION,
    next_slice: effectiveSlice,
    allowed_follow_on: ["APPLY_IMPLEMENT"],
    red_debt_remains: true,
    exception_receipt: applied.exception_receipt,
    incident_id: applied.incident_id,
    receipt_kind: RECEIPT_CONTINUE_WITH_EXCEPTION,
    implement_result: implementResult,
    closeout_results: closeoutResults,
    recovery_closeout: Boolean(block.recovery_closeout),
    stage: stagePick,
    idempotent: applied.idempotent || false,
  };
}

export { evaluateBreakGlassArchiveDebt } from "./opsx-break-glass-archive-gate.mjs";

export function normalGateAcceptsExceptionDebt({ openIncidents = [], stage = "ARCHIVE" } = {}) {
  if (!openIncidents.length) return { ok: true };
  return {
    ok: false,
    code: "REVALIDATION_REQUIRED",
    reason: "OPEN_BREAK_GLASS_INCIDENT",
    blocked_stage: stage,
    open_incident_ids: openIncidents.map((i) => i.incident_id),
  };
}
