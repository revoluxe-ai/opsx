import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Super Implement bounded auto-loop and crash/resume reconciliation.
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import {
  RUN_STATUS_APPLY_COMPLETE,
  RUN_STATUS_FAILED,
  RUN_STATUS_PAUSED,
  RUN_STATUS_RUNNING,
  beginInFlightDispatch,
  completeInFlightDispatch,
  evaluateApplyCompleteGate,
  evaluateBranchPreflight,
  evaluateOperativeReceiptForStage,
  loadOrCreateRun,
  loadSuperImplementContext,
  applyPersistedRun,
  normalizeRunLedger,
  planSuperImplementStep,
  preventDuplicateDispatch,
  superImplementRunRelPath,
} from "./opsx-super-implement.mjs";
import {
  assertSuperImplementV0LiveSdkTransportAllowed,
  buildDispatchSpec,
} from "./opsx-super-implement-executor.mjs";
import {
  appendDispatchLedgerEntry,
  bindEvidenceIdentity,
  evaluateConvergenceStop,
  evaluateStageWriteIsolation,
  evaluateSuperImplementV1LiveReadiness,
  findDispatchLedgerEntry,
  finalizeDispatchLedgerEntry,
  verifyNativeDispatchProof,
} from "./opsx-super-implement-live-v1.mjs";
import {
  ADOPTION_STATUS_ADOPTED,
  ensureMidProcessAdoption,
  ensureSuperWriterLockForHop,
} from "./opsx-super-implement-adoption.mjs";
import { releaseSuperChangeWriterLock } from "./opsx-apply-change-writer.mjs";
import { resolveEffectiveModelRoute } from "./opsx-super-implement-executor.mjs";

export const DEFAULT_MAX_AUTO_HOPS = 64;

function saveRun(repoRoot, changeRoot, run) {
  return applyPersistedRun(repoRoot, changeRoot, run);
}

function loadRunLedgerFromDisk(repoRoot, changeRoot) {
  const abs = resourcePath(repoRoot, superImplementRunRelPath(changeRoot));
  if (!existsSync(abs)) return null;
  try {
    return normalizeRunLedger(JSON.parse(readFileSync(abs, "utf8")));
  } catch {
    return null;
  }
}

function maybeReleaseSuperWriterLock(
  repoRoot,
  changeRoot,
  run,
  terminalStatus,
  { persistOk = true } = {},
) {
  if (!persistOk) {
    return { ok: true, skipped: true, reason: "persist_failed" };
  }
  if (terminalStatus !== RUN_STATUS_APPLY_COMPLETE && terminalStatus !== RUN_STATUS_PAUSED) {
    return { ok: true, skipped: true };
  }
  const diskRun = loadRunLedgerFromDisk(repoRoot, changeRoot) || run;
  if (terminalStatus === RUN_STATUS_PAUSED && diskRun?.in_flight_dispatch) {
    return { ok: true, skipped: true, reason: "in_flight_present" };
  }
  return releaseSuperChangeWriterLock({ repoRoot, changeRoot, run: diskRun });
}

function revisionStillValid(run, ctx) {
  const designDigest = ctx.design?.planning_identity?.planning_digest;
  const bound = run.execution_identity?.planning_digest;
  if (bound && designDigest && bound !== designDigest) {
    return { ok: false, code: "PLANNING_DIGEST_DRIFT" };
  }
  if (run.in_flight_dispatch?.input_revision?.planning_digest) {
    const inflight = run.in_flight_dispatch.input_revision.planning_digest;
    if (designDigest && inflight !== designDigest) {
      return { ok: false, code: "INFLIGHT_INPUT_REVISION_STALE" };
    }
  }
  return { ok: true, planning_digest: designDigest };
}

/**
 * Reconcile in-flight dispatch without re-invoking worker when receipts prove completion.
 */
export function reconcileInFlightFromReceipts({ run, ctx }) {
  if (!run?.in_flight_dispatch) {
    return { action: "none", run };
  }
  const { stage, slice_id: sliceId } = run.in_flight_dispatch;
  const operative = evaluateOperativeReceiptForStage(stage, sliceId, ctx);
  if (operative.authorized) {
    const done = completeInFlightDispatch(run, ctx, {
      worker_claim: "success",
      reconciled_from_disk: true,
      exit_code_ignored: true,
    });
    return {
      action: "completed_from_receipts",
      run: done.run,
      ok: done.ok,
      operative,
    };
  }
  return { action: "still_in_flight", run, operative };
}

/**
 * Map cursor_executor terminal evidence to controller outcome.
 */
export function mapExecutorOutcomeToWorkerClaim(executorResult) {
  if (!executorResult) {
    return { uncertain: true, worker_claim: "unknown" };
  }
  const terminal = executorResult.terminal_class || executorResult.adapter_terminal;
  if (terminal === "SUCCEEDED") {
    return { worker_claim: "success", exit_code: executorResult.exit_code };
  }
  if (terminal === "BLOCKED" || terminal === "FAILED" || terminal === "CANCELLED") {
    return { worker_claim: "failure", exit_code: executorResult.exit_code, terminal };
  }
  if (executorResult.reconciliation_state === "INDETERMINATE") {
    return { uncertain: true, worker_claim: "unknown" };
  }
  return { uncertain: true, worker_claim: "unknown", terminal };
}

/**
 * Crash/resume decision before a new dispatch.
 */
export function evaluateResumeBeforeDispatch({ run, ctx, executorReconcile = null }) {
  if (!run.in_flight_dispatch) {
    return { ok: true, action: "proceed" };
  }
  const fromReceipts = reconcileInFlightFromReceipts({ run, ctx });
  if (fromReceipts.action === "completed_from_receipts") {
    return {
      ok: true,
      action: "reconciled_ack_lost",
      run: fromReceipts.run,
      completed: fromReceipts,
    };
  }
  if (executorReconcile?.terminal_class === "SUCCEEDED") {
    const operative = fromReceipts.operative;
    if (!operative?.authorized) {
      return {
        ok: false,
        action: "pause",
        code: "EXECUTOR_SUCCESS_OPERATIVE_MISMATCH",
        recovery_contract: {
          code: "EXECUTOR_SUCCESS_OPERATIVE_MISMATCH",
          next_action: "inspect receipts; do not retry write until operative gate passes",
          resume_requires: ["operative_receipt_valid"],
        },
      };
    }
  }
  if (executorReconcile?.terminal_class === "SUCCEEDED" && fromReceipts.operative?.authorized) {
    const done = completeInFlightDispatch(run, ctx, { worker_claim: "success" });
    return { ok: true, action: "reconciled_executor_and_receipts", run: done.run };
  }
  return {
    ok: false,
    action: "pause",
    code: "IN_FLIGHT_UNCERTAIN",
    recovery_contract: {
      code: "IN_FLIGHT_UNCERTAIN",
      next_action:
        "reconcile executor ledger or receipt files; clear in_flight only after evidence",
      resume_requires: ["in_flight_reconciled"],
    },
    in_flight: run.in_flight_dispatch,
  };
}

/**
 * Skip dispatch when stage is already operatively satisfied (idempotent advance).
 */
export function trySkipDispatchForOperativeStage({ run, ctx, plan }) {
  const operative = evaluateOperativeReceiptForStage(
    plan.next_stage,
    plan.next_slice,
    ctx,
  );
  if (!operative.authorized) {
    return { skipped: false, operative };
  }
  const seq = (run.execution_identity?.run_sequence ?? 0) + 1;
  const nextRun = {
    ...run,
    execution_identity: {
      ...run.execution_identity,
      run_sequence: seq,
    },
    history: [
      ...(run.history || []),
      {
        sequence: run.execution_identity?.run_sequence ?? 0,
        stage: plan.next_stage,
        primitive_command: plan.primitive_command,
        outcome: "SKIPPED_ALREADY_OPERATIVE",
        artifact_status: operative.artifact_status,
      },
    ],
    last_transition: {
      at: new Date().toISOString(),
      kind: "STAGE_SKIPPED_OPERATIVE",
      stage: plan.next_stage,
      slice_id: plan.next_slice,
      code: null,
    },
  };
  const transition = evaluateApplyCompleteGate(ctx, { requireTaskProjection: true });
  if (transition.ok) {
    nextRun.status = RUN_STATUS_APPLY_COMPLETE;
  }
  return { skipped: true, run: nextRun, operative };
}

/**
 * Bounded automatic loop until APPLY_COMPLETE, PAUSED, or FAILED.
 *
 * @param {object} opts
 * @param {import('./opsx-super-implement-executor.mjs').createRecordingExecutorAdapter} opts.executorAdapter
 */
export async function runSuperImplementAutoLoop({
  repoRoot,
  changeRoot,
  executorAdapter,
  liveExecutionAuthorized = false,
  maxHops = DEFAULT_MAX_AUTO_HOPS,
  currentHead = null,
  currentBranch = null,
} = {}) {
  if (!executorAdapter || typeof executorAdapter.execute !== "function") {
    return {
      status: RUN_STATUS_FAILED,
      code: "EXECUTOR_ADAPTER_REQUIRED",
    };
  }
  const hopLog = [];
  let bootstrapCtx = loadSuperImplementContext({ repoRoot, changeRoot });
  if (!bootstrapCtx.ok) {
    return { status: RUN_STATUS_FAILED, code: bootstrapCtx.code, hopLog };
  }
  let bootstrapRun = loadOrCreateRun(repoRoot, changeRoot, bootstrapCtx, {
    liveExecutionAuthorized,
    repositoryHead: currentHead,
  });
  bootstrapRun.live_execution_authorized = liveExecutionAuthorized === true;
  if (bootstrapRun.mid_process_adoption?.status !== ADOPTION_STATUS_ADOPTED) {
    const adopt = ensureMidProcessAdoption({
      repoRoot,
      changeRoot,
      run: bootstrapRun,
    });
    hopLog.push({
      hop: -1,
      action: "mid_process_adoption",
      ok: adopt.ok,
      code: adopt.code || null,
    });
    if (!adopt.ok) {
      return {
        status: RUN_STATUS_PAUSED,
        code: adopt.code,
        run: adopt.run,
        hopLog,
        recovery_contract: adopt.recovery_contract,
        adoption: adopt.evaluation,
      };
    }
    bootstrapRun = adopt.run;
    if (adopt.evaluation?.already_complete) {
      const savedBootstrap = saveRun(repoRoot, changeRoot, bootstrapRun);
      bootstrapRun = savedBootstrap.run;
      maybeReleaseSuperWriterLock(
        repoRoot,
        changeRoot,
        bootstrapRun,
        RUN_STATUS_APPLY_COMPLETE,
        { persistOk: savedBootstrap.ok },
      );
      return {
        status: savedBootstrap.ok ? RUN_STATUS_APPLY_COMPLETE : RUN_STATUS_PAUSED,
        run: bootstrapRun,
        hopLog,
        adoption: adopt.evaluation,
        persist_code: savedBootstrap.ok ? null : savedBootstrap.code,
      };
    }
  }
  for (let hop = 0; hop < maxHops; hop += 1) {
    const ctx = loadSuperImplementContext({ repoRoot, changeRoot });
    if (!ctx.ok) {
      return { status: RUN_STATUS_FAILED, code: ctx.code, hopLog };
    }
    let run = loadOrCreateRun(repoRoot, changeRoot, ctx, {
      liveExecutionAuthorized,
      repositoryHead: currentHead,
    });
    run.live_execution_authorized = liveExecutionAuthorized === true;

    const writerGate = ensureSuperWriterLockForHop({
      repoRoot,
      changeRoot,
      run,
    });
    if (!writerGate.ok) {
      run.status = RUN_STATUS_PAUSED;
      run.recovery_contract = writerGate.recovery_contract || {
        code: writerGate.code,
        next_action: "resolve writer conflict before continuing Super",
        resume_requires: ["writer_conflict_cleared"],
      };
      const savedWriter = saveRun(repoRoot, changeRoot, run);
      return {
        status: RUN_STATUS_PAUSED,
        code: writerGate.code,
        run: savedWriter.run,
        hopLog,
        writer: writerGate,
      };
    }

    const branch = evaluateBranchPreflight({
      currentHead: currentHead || ctx.design?.repository_identity?.head,
      expectedHead: run.expected_head,
      canonicalBranch: run.canonical_branch,
      currentBranch,
    });
    if (!branch.ok) {
      run.status = RUN_STATUS_PAUSED;
      run.recovery_contract = branch.recovery_contract;
      const savedBranch = saveRun(repoRoot, changeRoot, run);
      maybeReleaseSuperWriterLock(repoRoot, changeRoot, savedBranch.run, RUN_STATUS_PAUSED, {
        persistOk: savedBranch.ok,
      });
      return { status: RUN_STATUS_PAUSED, code: branch.code, run: savedBranch.run, hopLog };
    }

    const rev = revisionStillValid(run, ctx);
    if (!rev.ok) {
      run.status = RUN_STATUS_PAUSED;
      run.recovery_contract = {
        code: rev.code,
        next_action: "/opsx-update or refresh APPLY DESIGN planning identity",
        resume_requires: ["planning_digest_aligned"],
      };
      const savedRev = saveRun(repoRoot, changeRoot, run);
      maybeReleaseSuperWriterLock(repoRoot, changeRoot, savedRev.run, RUN_STATUS_PAUSED, {
        persistOk: savedRev.ok,
      });
      return { status: RUN_STATUS_PAUSED, code: rev.code, run: savedRev.run, hopLog };
    }

    let executorReconcile = null;
    if (run.in_flight_dispatch?.execution_id && executorAdapter.reconcile) {
      executorReconcile = await executorAdapter.reconcile(
        run.in_flight_dispatch.execution_id,
      );
    }
    const resume = evaluateResumeBeforeDispatch({ run, ctx, executorReconcile });
    if (resume.run) run = resume.run;
    if (!resume.ok) {
      run.status = RUN_STATUS_PAUSED;
      run.recovery_contract = resume.recovery_contract;
      const savedResume = saveRun(repoRoot, changeRoot, run);
      run = savedResume.run;
      maybeReleaseSuperWriterLock(repoRoot, changeRoot, run, RUN_STATUS_PAUSED, {
        persistOk: savedResume.ok,
      });
      return {
        status: RUN_STATUS_PAUSED,
        code: resume.code,
        run,
        hopLog,
        resume,
      };
    }
    if (resume.action === "reconciled_ack_lost" || resume.action === "reconciled_executor_and_receipts") {
      const savedReconcile = saveRun(repoRoot, changeRoot, run);
      run = savedReconcile.run;
      hopLog.push({ hop, action: resume.action });
      if (run.status === RUN_STATUS_APPLY_COMPLETE) {
        maybeReleaseSuperWriterLock(repoRoot, changeRoot, run, RUN_STATUS_APPLY_COMPLETE, {
          persistOk: savedReconcile.ok,
        });
        return { status: RUN_STATUS_APPLY_COMPLETE, run, hopLog };
      }
      continue;
    }

    const dup = preventDuplicateDispatch(run);
    if (!dup.ok) {
      const re = reconcileInFlightFromReceipts({ run, ctx });
      if (re.action === "completed_from_receipts") {
        run = re.run;
        const savedDup = saveRun(repoRoot, changeRoot, run);
        run = savedDup.run;
        hopLog.push({ hop, action: "duplicate_prevented_reconciled" });
        continue;
      }
      run.status = RUN_STATUS_PAUSED;
      run.recovery_contract = dup.recovery_contract;
      const savedDupPause = saveRun(repoRoot, changeRoot, run);
      maybeReleaseSuperWriterLock(repoRoot, changeRoot, savedDupPause.run, RUN_STATUS_PAUSED, {
        persistOk: savedDupPause.ok,
      });
      return { status: RUN_STATUS_PAUSED, code: dup.code, run: savedDupPause.run, hopLog };
    }

    const plan = planSuperImplementStep({
      repoRoot,
      changeRoot,
      run,
      currentHead,
      currentBranch,
      liveExecutionAuthorized: run.live_execution_authorized,
    });

    if (plan.status === RUN_STATUS_APPLY_COMPLETE) {
      const gate = evaluateApplyCompleteGate(ctx);
      if (!gate.ok) {
        run.status = RUN_STATUS_PAUSED;
        run.recovery_contract = {
          code: gate.code,
          next_action: "repair evidence before APPLY_COMPLETE",
          resume_requires: ["apply_complete_gate_pass"],
        };
        const savedGatePause = saveRun(repoRoot, changeRoot, run);
        maybeReleaseSuperWriterLock(repoRoot, changeRoot, savedGatePause.run, RUN_STATUS_PAUSED, {
          persistOk: savedGatePause.ok,
        });
        return { status: RUN_STATUS_PAUSED, code: gate.code, run: savedGatePause.run, hopLog, gate };
      }
      run.status = RUN_STATUS_APPLY_COMPLETE;
      const savedComplete = saveRun(repoRoot, changeRoot, run);
      run = savedComplete.run;
      maybeReleaseSuperWriterLock(repoRoot, changeRoot, run, RUN_STATUS_APPLY_COMPLETE, {
        persistOk: savedComplete.ok,
      });
      return {
        status: savedComplete.ok ? RUN_STATUS_APPLY_COMPLETE : RUN_STATUS_PAUSED,
        run,
        hopLog,
        gate,
        persist_code: savedComplete.ok ? null : savedComplete.code,
      };
    }

    if (plan.status === RUN_STATUS_FAILED) {
      return { status: RUN_STATUS_FAILED, code: plan.code, run, hopLog };
    }

    if (!run.live_execution_authorized) {
      return {
        status: RUN_STATUS_PAUSED,
        code: "LIVE_EXECUTION_NOT_AUTHORIZED",
        plan,
        run,
        hopLog,
        recovery_contract: {
          code: "LIVE_EXECUTION_NOT_AUTHORIZED",
          next_action: "Owner live-execution attestation required",
          resume_requires: ["owner_execution_attestation"],
        },
      };
    }

    if (executorAdapter.is_simulated !== true) {
      const adapterKind =
        executorAdapter.transport === "native-tool"
          ? "native-tool"
          : executorAdapter.transport === "cursor-sdk" || executorAdapter.v1_transport
            ? "cursor-sdk"
            : "cursor-sdk";
      const liveReady = evaluateSuperImplementV1LiveReadiness({
        repoRoot,
        changeRoot,
        run,
        ctx,
        adapterKind,
      });
      if (!liveReady.ok) {
        run.status = RUN_STATUS_PAUSED;
        run.recovery_contract = liveReady.recovery_contract;
        const savedTransport = saveRun(repoRoot, changeRoot, run);
        maybeReleaseSuperWriterLock(repoRoot, changeRoot, savedTransport.run, RUN_STATUS_PAUSED, {
          persistOk: savedTransport.ok,
        });
        return {
          status: RUN_STATUS_PAUSED,
          code: liveReady.code,
          run: savedTransport.run,
          hopLog,
          recovery_contract: liveReady.recovery_contract,
          live_execution_blocked: true,
          blockers: liveReady.blockers,
        };
      }
      if (executorAdapter.v1_transport !== true && executorAdapter.transport !== "native-tool") {
        const liveTransport = assertSuperImplementV0LiveSdkTransportAllowed();
        if (!liveTransport.ok) {
          run.status = RUN_STATUS_PAUSED;
          run.recovery_contract = liveTransport.recovery_contract;
          const savedTransport = saveRun(repoRoot, changeRoot, run);
          maybeReleaseSuperWriterLock(repoRoot, changeRoot, savedTransport.run, RUN_STATUS_PAUSED, {
            persistOk: savedTransport.ok,
          });
          return {
            status: RUN_STATUS_PAUSED,
            code: liveTransport.code,
            run: savedTransport.run,
            hopLog,
            recovery_contract: liveTransport.recovery_contract,
            live_sdk_hard_disabled: true,
          };
        }
      }
    }

    const skip = trySkipDispatchForOperativeStage({ run, ctx, plan });
    if (skip.skipped) {
      run = skip.run;
      const savedSkip = saveRun(repoRoot, changeRoot, run);
      run = savedSkip.run;
      hopLog.push({ hop, action: "skipped_operative", stage: plan.next_stage });
      if (run.status === RUN_STATUS_APPLY_COMPLETE) {
        maybeReleaseSuperWriterLock(repoRoot, changeRoot, run, RUN_STATUS_APPLY_COMPLETE, {
          persistOk: savedSkip.ok,
        });
        return { status: RUN_STATUS_APPLY_COMPLETE, run, hopLog };
      }
      continue;
    }

    const routePolicy = resolveEffectiveModelRoute({
      allowFixtureFallback: executorAdapter.is_simulated === true,
    });
    if (!routePolicy.ok) {
      run.status = RUN_STATUS_PAUSED;
      run.recovery_contract = {
        code: routePolicy.code,
        next_action:
          "export SUPER_IMPLEMENT_RESOLVED_MODEL_ROUTE_JSON before live Super dispatch",
        resume_requires: ["resolved_model_route"],
      };
      const savedRoute = saveRun(repoRoot, changeRoot, run);
      maybeReleaseSuperWriterLock(repoRoot, changeRoot, savedRoute.run, RUN_STATUS_PAUSED, {
        persistOk: savedRoute.ok,
      });
      return {
        status: RUN_STATUS_PAUSED,
        code: routePolicy.code,
        run: savedRoute.run,
        hopLog,
      };
    }

    const spec = buildDispatchSpec({
      repoRoot,
      changeId: ctx.changeId,
      changeRoot,
      stage: plan.next_stage,
      sliceId: plan.next_slice,
      primitiveCommand: plan.primitive_command,
      dispatchId: plan.dispatch_id,
      runSequence: run.execution_identity?.run_sequence ?? 0,
      planningDigest: ctx.design?.planning_identity?.planning_digest,
      repositoryHead: currentHead || ctx.design?.repository_identity?.head,
      modelRoute: routePolicy.route,
      effectiveModelSource: routePolicy.effective_source,
    });

    const isolation = evaluateStageWriteIsolation({
      stage: plan.next_stage,
      primitiveCommand: plan.primitive_command,
    });
    if (!isolation.ok) {
      run.status = RUN_STATUS_PAUSED;
      run.recovery_contract = {
        code: isolation.code,
        next_action: "forbidden primitive or stage write scope",
        resume_requires: ["authorized_primitive"],
      };
      const savedIso = saveRun(repoRoot, changeRoot, run);
      return { status: RUN_STATUS_PAUSED, code: isolation.code, run: savedIso.run, hopLog };
    }

    const evidenceBind = bindEvidenceIdentity({ run, spec, ctx });
    if (!evidenceBind.ok) {
      run.status = RUN_STATUS_PAUSED;
      run.recovery_contract = {
        code: evidenceBind.code,
        next_action: "align planning digest / revision before dispatch",
        resume_requires: ["evidence_identity_aligned"],
      };
      const savedBind = saveRun(repoRoot, changeRoot, run);
      return { status: RUN_STATUS_PAUSED, code: evidenceBind.code, run: savedBind.run, hopLog };
    }

    const priorLedger = findDispatchLedgerEntry(repoRoot, changeRoot, plan.dispatch_id);
    if (priorLedger?.status === "TERMINAL") {
      hopLog.push({ hop, action: "dispatch_ledger_idempotent_skip", dispatch_id: plan.dispatch_id });
      continue;
    }

    const begun = beginInFlightDispatch(run, {
      dispatchId: plan.dispatch_id,
      stage: plan.next_stage,
      sliceId: plan.next_slice,
      command: plan.primitive_command,
    });
    if (!begun.ok) {
      return { status: RUN_STATUS_PAUSED, code: begun.code, run, hopLog };
    }
    run = begun.run;
    run.in_flight_dispatch = {
      ...run.in_flight_dispatch,
      execution_id: spec.execution_id,
      input_revision: spec.input_revision,
      hop_digest: spec.hop_identity?.hop_digest,
    };
    const savedInflight = saveRun(repoRoot, changeRoot, run);
    if (!savedInflight.ok) {
      return {
        status: RUN_STATUS_PAUSED,
        code: savedInflight.code,
        run: savedInflight.run,
        hopLog,
      };
    }
    run = savedInflight.run;

    appendDispatchLedgerEntry(repoRoot, changeRoot, {
      changeId: ctx.changeId,
      dispatchId: plan.dispatch_id,
      executionId: spec.execution_id,
      stage: plan.next_stage,
      sliceId: plan.next_slice,
      status: "DISPATCHED",
      transport: executorAdapter.transport || "unknown",
    });

    let execResult = await executorAdapter.execute(spec);
    if (
      executorAdapter.transport === "native-tool" &&
      execResult?.terminal_class === "SUCCEEDED"
    ) {
      const proofCheck = verifyNativeDispatchProof(spec, execResult);
      if (!proofCheck.ok) {
        execResult = {
          ...execResult,
          terminal_class: "BLOCKED",
          code: proofCheck.code,
          dispatch_proof_rejected: true,
        };
      }
    }
    hopLog.push({
      hop,
      action: "dispatched",
      stage: plan.next_stage,
      slice: plan.next_slice,
      execution_id: spec.execution_id,
      simulated: executorAdapter.is_simulated === true,
      terminal: execResult?.terminal_class,
    });

    const freshCtx = loadSuperImplementContext({ repoRoot, changeRoot });
    const outcome = mapExecutorOutcomeToWorkerClaim(execResult);
    const done = completeInFlightDispatch(run, freshCtx.ok ? freshCtx : ctx, outcome);
    run = done.run;
    const savedDone = saveRun(repoRoot, changeRoot, run);
    run = savedDone.run;

    if (!savedDone.ok) {
      return {
        status: RUN_STATUS_PAUSED,
        code: savedDone.code,
        run,
        hopLog,
        persist_code: savedDone.code,
      };
    }

    if (!done.ok) {
      const convergence = evaluateConvergenceStop(run, done.operative);
      run = convergence.run;
      if (convergence.stop) {
        run.status = RUN_STATUS_PAUSED;
        run.recovery_contract = convergence.recovery_contract;
        const savedConv = saveRun(repoRoot, changeRoot, run);
        maybeReleaseSuperWriterLock(repoRoot, changeRoot, savedConv.run, RUN_STATUS_PAUSED, {
          persistOk: savedConv.ok,
        });
        return {
          status: RUN_STATUS_PAUSED,
          code: convergence.code,
          run: savedConv.run,
          hopLog,
          operative: done.operative,
          recovery_contract: convergence.recovery_contract,
        };
      }
      maybeReleaseSuperWriterLock(repoRoot, changeRoot, run, RUN_STATUS_PAUSED, {
        persistOk: savedDone.ok,
      });
      return {
        status: RUN_STATUS_PAUSED,
        code: done.recovery_contract?.code || "STAGE_REJECTED",
        run,
        hopLog,
        operative: done.operative,
        recovery_contract: done.recovery_contract,
        persist_code: savedDone.ok ? null : savedDone.code,
      };
    }

    finalizeDispatchLedgerEntry(repoRoot, changeRoot, {
      dispatchId: plan.dispatch_id,
      status: "TERMINAL",
      nativeDispatchProof: execResult?.native_dispatch_proof,
    });

    if (run.status === RUN_STATUS_APPLY_COMPLETE) {
      maybeReleaseSuperWriterLock(repoRoot, changeRoot, run, RUN_STATUS_APPLY_COMPLETE, {
        persistOk: savedDone.ok,
      });
      return { status: RUN_STATUS_APPLY_COMPLETE, run, hopLog };
    }
  }
  return {
    status: RUN_STATUS_PAUSED,
    code: "MAX_HOPS_EXCEEDED",
    hopLog,
    recovery_contract: {
      code: "MAX_HOPS_EXCEEDED",
      next_action: "inspect hopLog and resume with bounded maxHops",
      resume_requires: ["owner_review"],
    },
  };
}
