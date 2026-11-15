import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * APPLY_COMPLETE_WITH_EXCEPTION — evidence-backed completion while incident stays OPEN.
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { findOpenIncidents } from "./opsx-break-glass-ledger.mjs";
import {
  BG_001_NATIVE_REVALIDATION_ID,
  findCapabilityBacklogEntry,
  nativeGateRevalidationDeferredForV1,
} from "./opsx-break-glass-capability-backlog.mjs";
import { guardedWriteText } from "./opsx-break-glass-write-guard.mjs";
import { IMPLEMENTATION_WRITTEN } from "./opsx-apply-pipeline-identity.mjs";
import { evaluateBreakGlassArchiveDebt } from "./opsx-break-glass-archive-gate.mjs";
import { evaluateDevelopmentCompleteWithExceptionGate } from "./opsx-break-glass-exception-closeout.mjs";

export const COMPLETION_APPLY_WITH_EXCEPTION = "APPLY_COMPLETE_WITH_EXCEPTION";
export const COMPLETION_DEVELOPMENT_WITH_EXCEPTION = "DEVELOPMENT_COMPLETE_WITH_EXCEPTION";
export const EXCEPTION_COMPLETION_SCHEMA = "opsx.break_glass_exception_completion";

export function exceptionCompletionRelPath(changeRoot, sliceId) {
  return join(changeRoot, "apply-exception-complete", sliceId, "COMPLETION.json");
}

/**
 * Durable completion receipt after real implement + contract tests under grant.
 */
export function materializeExceptionCompletionEvidence({
  repoRoot,
  changeRoot,
  sliceId,
  grant,
  exceptionReceipt,
  incidentId,
  implementationPath,
  contractTestReceipt,
  nativeImplementCli,
} = {}) {
  const relPath = exceptionCompletionRelPath(changeRoot, sliceId);
  const body = {
    schema: EXCEPTION_COMPLETION_SCHEMA,
    schema_version: 1,
    completion_stage: COMPLETION_APPLY_WITH_EXCEPTION,
    development_stage: COMPLETION_DEVELOPMENT_WITH_EXCEPTION,
    slice_id: sliceId,
    change_id: grant.change_id,
    execution_id: grant.execution_id,
    grant_id: grant.grant_id,
    exception_id: exceptionReceipt?.exception_id,
    incident_id: incidentId,
    implementation_path: implementationPath,
    contract_test_receipt: contractTestReceipt,
    native_implement_cli: nativeImplementCli,
    capability_debt_ref: BG_001_NATIVE_REVALIDATION_ID,
    incident_remains_open: true,
    revalidation_deferred: true,
    created_at: new Date().toISOString(),
  };
  const written = guardedWriteText({
    grant,
    repoRoot,
    relPath,
    content: `${JSON.stringify(body, null, 2)}\n`,
  });
  if (!written.ok) return written;
  return { ok: true, completion_path: relPath, completion: body };
}

function readJsonIfExists(absPath) {
  if (!existsSync(absPath)) return null;
  try {
    return JSON.parse(readFileSync(absPath, "utf8"));
  } catch {
    return null;
  }
}

function sliceHasExceptionCompletion(repoRoot, changeRoot, sliceId) {
  const completion = readJsonIfExists(
    resourcePath(repoRoot, exceptionCompletionRelPath(changeRoot, sliceId)),
  );
  if (!completion || completion.schema !== EXCEPTION_COMPLETION_SCHEMA) {
    return { ok: false, code: "EXCEPTION_COMPLETION_MISSING", slice_id: sliceId };
  }
  if (completion.completion_stage !== COMPLETION_APPLY_WITH_EXCEPTION) {
    return { ok: false, code: "INVALID_COMPLETION_STAGE", slice_id: sliceId };
  }
  const contract = completion.contract_test_receipt;
  if (!contract || contract.exit_code !== 0 || contract.minted_fake_red === true) {
    return { ok: false, code: "CONTRACT_TEST_EVIDENCE_INVALID", slice_id: sliceId };
  }
  const impl = readJsonIfExists(resourcePath(repoRoot, completion.implementation_path));
  if (!impl || impl.status !== IMPLEMENTATION_WRITTEN || !impl.break_glass_exception) {
    return { ok: false, code: "IMPLEMENTATION_EVIDENCE_INVALID", slice_id: sliceId };
  }
  if (impl.break_glass_exception.grant_id !== completion.grant_id) {
    return { ok: false, code: "GRANT_IMPLEMENTATION_MISMATCH", slice_id: sliceId };
  }
  return { ok: true, completion, implementation: impl };
}

/**
 * Evidence gate: scoped owner exception path may complete while incident + BG-001 stay OPEN.
 */
export function evaluateApplyCompleteWithExceptionGate(ctx) {
  const { design, repoRoot, changeRoot } = ctx;
  const changeId = design?.change_id;
  if (!changeId || !changeRoot) {
    return { ok: false, code: "INVALID_CONTEXT" };
  }

  const debtAllow = evaluateBreakGlassArchiveDebt({
    repoRoot,
    changeId,
    targetStage: COMPLETION_APPLY_WITH_EXCEPTION,
  });
  if (!debtAllow.ok) {
    return debtAllow;
  }

  const bg001 = findCapabilityBacklogEntry(repoRoot, BG_001_NATIVE_REVALIDATION_ID);
  if (!bg001 || bg001.status !== "OPEN") {
    return { ok: false, code: "OPSX_BG_001_NOT_OPEN", reason: "EXCEPTION_DEBT_NOT_REGISTERED" };
  }

  const open = findOpenIncidents(repoRoot, { changeId });
  if (!open.length) {
    return { ok: false, code: "NO_OPEN_EXCEPTION_INCIDENT" };
  }

  const deferred = nativeGateRevalidationDeferredForV1(repoRoot);
  if (!deferred.deferred) {
    return { ok: false, code: "REVALIDATION_NOT_DEFERRED_FOR_V1" };
  }

  const devCloseout = evaluateDevelopmentCompleteWithExceptionGate({
    ...ctx,
    grantId: ctx.grantId,
  });
  if (!devCloseout.ok) {
    return devCloseout;
  }

  return {
    ok: true,
    completion_stage: COMPLETION_APPLY_WITH_EXCEPTION,
    development_stage: COMPLETION_DEVELOPMENT_WITH_EXCEPTION,
    open_incident_ids: open.map((i) => i.incident_id),
    capability_debt_ref: BG_001_NATIVE_REVALIDATION_ID,
    incident_remains_open: true,
    auto_archive_forbidden: true,
    slice_proofs: devCloseout.slice_proofs,
  };
}

export { evaluateDevelopmentCompleteWithExceptionGate };
