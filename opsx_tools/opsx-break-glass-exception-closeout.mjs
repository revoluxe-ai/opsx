import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Exception-route development closeout — reuse historical IMPLEMENTATION_WRITTEN evidence
 * without re-IMPLEMENT; record explicit verification debt under Owner grant.
 */
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import {
  GREEN_PROVEN,
  IMPLEMENTATION_WRITTEN,
  IMPLEMENTATION_ADOPTED,
  applyReceiptRelPaths,
  classifyGreenEvidenceFreshness,
  classifyPostImplementGreenRecovery,
  upstreamContextForSlice,
} from "./opsx-apply-pipeline-identity.mjs";
import {
  COMPLETION_APPLY_WITH_EXCEPTION,
  COMPLETION_DEVELOPMENT_WITH_EXCEPTION,
  EXCEPTION_COMPLETION_SCHEMA,
  exceptionCompletionRelPath,
} from "./opsx-break-glass-exception-completion.mjs";
import { BG_001_NATIVE_REVALIDATION_ID } from "./opsx-break-glass-capability-backlog.mjs";
import { executionIdentityDigest } from "./opsx-break-glass-execution-identity.mjs";
import { guardedWriteText } from "./opsx-break-glass-write-guard.mjs";
import { readProcessGateDeferral } from "./opsx-break-glass-process-gate-deferral.mjs";

export const EXCEPTION_CLOSEOUT_SCHEMA = "opsx.break_glass_exception_closeout";
export const CLOSEOUT_KIND_HISTORICAL_IMPL = "HISTORICAL_IMPLEMENTATION_REUSED";
export const CLOSEOUT_KIND_CURRENT_SLICE = "CURRENT_SLICE_ORDINARY_PROOF";
export const PROOF_CURRENT_GREEN = "CURRENT_SLICE_GREEN_PROVEN";
export const PROOF_EXCEPTION_COMPLETION = "EXCEPTION_COMPLETION_RECEIPT";
export const PROOF_EXCEPTION_CLOSEOUT = "EXCEPTION_CLOSEOUT_RECEIPT";
export const PROOF_PROCESS_GATE_DEFERRAL = "PROCESS_GATE_DEFERRAL_RECEIPT";

export const RECOVERY_CLOSEOUT_FAILURE_CODES = new Set([
  "IDENTITY_ALGORITHM_UNRESOLVED",
  "TEST_COVERAGE_ARTIFACT_GAP",
  "FROZEN_TEST_PATH_MISSING",
]);

export function exceptionCloseoutRelPath(changeRoot, sliceId) {
  return join(changeRoot, "apply-exception-closeout", sliceId, "CLOSEOUT.json");
}

function readJsonIfExists(absPath) {
  if (!existsSync(absPath)) return null;
  try {
    return JSON.parse(readFileSync(absPath, "utf8"));
  } catch {
    return null;
  }
}

export function closeoutIdempotencyKey({ grant, sliceId, incidentId }) {
  const base = [
    grant?.idempotency_key || grant?.grant_id,
    sliceId,
    incidentId || "",
    "exception_closeout_v0",
  ].join(":");
  return createHash("sha256").update(base, "utf8").digest("hex");
}

/**
 * Prove durable IMPLEMENTATION_WRITTEN exists under the change scope (no rewrite).
 */
export function proveImplementationExistsInScope(repoRoot, changeRoot, sliceId) {
  const rel = applyReceiptRelPaths(changeRoot, sliceId).implement;
  const abs = resourcePath(repoRoot, rel);
  const impl = readJsonIfExists(abs);
  if (
    !impl ||
    (impl.status !== IMPLEMENTATION_WRITTEN && impl.status !== IMPLEMENTATION_ADOPTED)
  ) {
    return { ok: false, code: "IMPLEMENTATION_MISSING", slice_id: sliceId, path: rel };
  }
  return { ok: true, implementation_path: rel, implementation: impl };
}

/**
 * Derive open verification obligations for a slice with stale / blocked recovery.
 */
export function deriveVerificationObligationsForSlice({
  red,
  implement,
  green,
  repoRoot,
  upstream = {},
} = {}) {
  const recovery = classifyPostImplementGreenRecovery({
    red,
    implement,
    green,
    repoRoot,
  });
  const obligations = [];
  if (recovery?.code && recovery.code !== "GREEN_REFRESH_AUTHORIZED") {
    obligations.push({
      code: recovery.code,
      disposition: recovery.disposition || "PAUSE",
      status: "OPEN",
      mandatory_revalidation: true,
      source: "post_implement_recovery_classifier",
    });
  }
  const freshness = classifyGreenEvidenceFreshness(green, upstream);
  if (green?.status === GREEN_PROVEN && !freshness.current) {
    obligations.push({
      code: freshness.code || "STALE_GREEN_EVIDENCE",
      disposition: "VERIFY_DEBT",
      status: "OPEN",
      mandatory_revalidation: true,
      source: "green_freshness",
    });
  }
  return obligations;
}

function readExceptionCompletion(repoRoot, changeRoot, sliceId) {
  return readJsonIfExists(resourcePath(repoRoot, exceptionCompletionRelPath(changeRoot, sliceId)));
}

function readExceptionCloseout(repoRoot, changeRoot, sliceId) {
  return readJsonIfExists(resourcePath(repoRoot, exceptionCloseoutRelPath(changeRoot, sliceId)));
}

/**
 * Per-slice proof for DEVELOPMENT_COMPLETE_WITH_EXCEPTION (mixed historical + current).
 */
export function evaluateSliceDevelopmentProof({
  repoRoot,
  changeRoot,
  sliceId,
  red,
  implement,
  green,
  upstream = {},
  grantId = null,
  attestedCurrentGreenSliceIds = [],
} = {}) {
  const implProof = proveImplementationExistsInScope(repoRoot, changeRoot, sliceId);
  if (!implProof.ok) return implProof;

  if (
    attestedCurrentGreenSliceIds.includes(sliceId) &&
    green?.status === GREEN_PROVEN
  ) {
    return {
      ok: true,
      proof_kind: PROOF_CURRENT_GREEN,
      slice_id: sliceId,
      implementation_path: implProof.implementation_path,
      attested_current_green: true,
    };
  }

  const freshness = classifyGreenEvidenceFreshness(green, upstream);
  if (green?.status === GREEN_PROVEN && freshness.current) {
    return {
      ok: true,
      proof_kind: PROOF_CURRENT_GREEN,
      slice_id: sliceId,
      implementation_path: implProof.implementation_path,
    };
  }

  const completion = readExceptionCompletion(repoRoot, changeRoot, sliceId);
  if (completion?.schema === EXCEPTION_COMPLETION_SCHEMA) {
    const contract = completion.contract_test_receipt;
    if (contract?.exit_code === 0 && contract.minted_fake_red !== true) {
      if (!grantId || completion.grant_id === grantId) {
        return {
          ok: true,
          proof_kind: PROOF_EXCEPTION_COMPLETION,
          slice_id: sliceId,
          completion,
        };
      }
      return { ok: false, code: "GRANT_COMPLETION_MISMATCH", slice_id: sliceId };
    }
  }

  const deferralLoaded = readProcessGateDeferral(repoRoot, changeRoot);
  if (deferralLoaded.ok && deferralLoaded.deferral?.verification_debt_open === true) {
    if (!grantId || deferralLoaded.deferral.grant_id === grantId) {
      const staleRows =
        deferralLoaded.deferral.actual_gate_results?.split_apply_green_freshness?.slices || [];
      const listedStale = staleRows.some((row) => row.slice_id === sliceId);
      const processOnlyDebt =
        staleRows.length === 0 &&
        Boolean(deferralLoaded.deferral.actual_gate_results?.quality_typecheck);
      if (listedStale || processOnlyDebt) {
        return {
          ok: true,
          proof_kind: PROOF_PROCESS_GATE_DEFERRAL,
          slice_id: sliceId,
          deferral: deferralLoaded.deferral,
          implementation_path: implProof.implementation_path,
          ordinary_green_current: freshness.current,
        };
      }
    }
  }

  const closeout = readExceptionCloseout(repoRoot, changeRoot, sliceId);
  if (closeout?.schema !== EXCEPTION_CLOSEOUT_SCHEMA) {
    return { ok: false, code: "EXCEPTION_CLOSEOUT_MISSING", slice_id: sliceId };
  }
  if (grantId && closeout.grant_id !== grantId) {
    return { ok: false, code: "GRANT_CLOSEOUT_MISMATCH", slice_id: sliceId };
  }
  if (closeout.implementation_path !== implProof.implementation_path) {
    return { ok: false, code: "CLOSEOUT_IMPL_PATH_DRIFT", slice_id: sliceId };
  }
  if (!Array.isArray(closeout.verification_obligations) || !closeout.verification_obligations.length) {
    return { ok: false, code: "VERIFICATION_OBLIGATIONS_MISSING", slice_id: sliceId };
  }
  const openDebt = closeout.verification_obligations.some((o) => o.status === "OPEN");
  if (!openDebt) {
    return { ok: false, code: "CLOSEOUT_DEBT_NOT_OPEN", slice_id: sliceId };
  }
  return {
    ok: true,
    proof_kind: PROOF_EXCEPTION_CLOSEOUT,
    slice_id: sliceId,
    closeout,
    implementation_path: implProof.implementation_path,
  };
}

/**
 * Authorized writer: durable exception closeout receipt (idempotent).
 */
export function materializeExceptionCloseoutReceipt({
  repoRoot,
  changeRoot,
  sliceId,
  grant,
  exceptionReceipt,
  incidentId,
  runtimeIdentity,
  red,
  implement,
  green,
  upstream = {},
  verificationObligations = null,
  failedGates = null,
  remainingRisks = null,
} = {}) {
  const implProof = proveImplementationExistsInScope(repoRoot, changeRoot, sliceId);
  if (!implProof.ok) return implProof;

  const relPath = exceptionCloseoutRelPath(changeRoot, sliceId);
  const existing = readJsonIfExists(resourcePath(repoRoot, relPath));
  const idempotencyKey = closeoutIdempotencyKey({
    grant,
    sliceId,
    incidentId,
  });
  if (
    existing?.schema === EXCEPTION_CLOSEOUT_SCHEMA &&
    existing.idempotency_key === idempotencyKey
  ) {
    return { ok: true, idempotent: true, closeout_path: relPath, closeout: existing };
  }

  const obligations =
    verificationObligations ||
    deriveVerificationObligationsForSlice({
      red,
      implement,
      green,
      repoRoot,
      upstream,
    });
  if (!obligations.length) {
    return {
      ok: false,
      code: "NO_VERIFICATION_DEBT_TO_RECORD",
      slice_id: sliceId,
      hint: "Use ordinary green proof or exception completion for fully proven slices",
    };
  }

  const identity = runtimeIdentity?.ok ? runtimeIdentity.identity : runtimeIdentity;
  const body = {
    schema: EXCEPTION_CLOSEOUT_SCHEMA,
    schema_version: 1,
    closeout_kind: CLOSEOUT_KIND_HISTORICAL_IMPL,
    completion_stage: COMPLETION_APPLY_WITH_EXCEPTION,
    development_stage: COMPLETION_DEVELOPMENT_WITH_EXCEPTION,
    slice_id: sliceId,
    change_id: grant.change_id,
    execution_id: grant.execution_id,
    grant_id: grant.grant_id,
    exception_id: exceptionReceipt?.exception_id,
    incident_id: incidentId,
    implementation_path: implProof.implementation_path,
    implementation_evidence_kind: "PRE_EXISTING_IMPLEMENTATION_WRITTEN",
    verification_obligations: obligations,
    failed_gates: failedGates || [
      {
        gate: grant.blocked_gate,
        failure_code: grant.original_failure_code,
      },
    ],
    execution_identity_digest: executionIdentityDigest(identity || {}),
    repository_identity: grant.repository_identity,
    capability_debt_ref: BG_001_NATIVE_REVALIDATION_ID,
    incident_remains_open: true,
    revalidation_mandatory: true,
    idempotency_key: idempotencyKey,
    remaining_risks:
      remainingRisks || [
        "Verification debt OPEN until OPSX-BG-001 revalidation or ordinary GREEN refresh",
      ],
    created_at: new Date().toISOString(),
  };

  const written = guardedWriteText({
    grant,
    repoRoot,
    relPath,
    content: `${JSON.stringify(body, null, 2)}\n`,
  });
  if (!written.ok) return written;
  return { ok: true, closeout_path: relPath, closeout: body, idempotent: false };
}

/**
 * Gate: all slices satisfied via current proof, exception completion, or closeout debt.
 */
export function evaluateDevelopmentCompleteWithExceptionGate(ctx) {
  const { design, repoRoot, changeRoot, maps, grantId } = ctx;
  const changeId = design?.change_id;
  if (!changeId || !changeRoot) {
    return { ok: false, code: "INVALID_CONTEXT" };
  }
  const sliceIds = design.slice_order?.nodes || [];
  if (!sliceIds.length) {
    return { ok: false, code: "NO_SLICES" };
  }

  const upstreamBySlice = ctx.upstreamBySlice || maps?.upstreamBySlice || {};
  const redBySlice = maps?.redBySlice || ctx.redBySlice || {};
  const implementBySlice = maps?.implementBySlice || ctx.implementBySlice || {};
  const greenBySlice = maps?.greenBySlice || ctx.greenBySlice || {};
  const upstreamMaps = {
    design,
    repoRoot,
    ...maps,
    redBySlice,
    implementBySlice,
    greenBySlice,
  };

  const attestedCurrentGreenSliceIds = ctx.attestedCurrentGreenSliceIds || [];

  const sliceProofs = [];
  for (const id of sliceIds) {
    const upstream =
      upstreamBySlice[id] || upstreamContextForSlice(id, upstreamMaps);
    const proof = evaluateSliceDevelopmentProof({
      repoRoot,
      changeRoot,
      sliceId: id,
      red: redBySlice[id],
      implement: implementBySlice[id],
      green: greenBySlice[id],
      upstream,
      grantId,
      attestedCurrentGreenSliceIds,
    });
    if (!proof.ok) return proof;
    sliceProofs.push(proof);
  }

  return {
    ok: true,
    completion_stage: COMPLETION_APPLY_WITH_EXCEPTION,
    development_stage: COMPLETION_DEVELOPMENT_WITH_EXCEPTION,
    slice_proofs: sliceProofs,
    verification_debt_open: true,
    auto_archive_forbidden: true,
  };
}
