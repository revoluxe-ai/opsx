/**
 * OPSX Break Glass V1 — hardened process-defect exception spine (fail-closed).
 */
import { randomUUID } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  GRANT_SCHEMA,
  sha256Hex,
  signBreakGlassOwnerGrant,
  stableStringify,
} from "./opsx-break-glass-crypto.mjs";
import {
  bindGrantToRuntimeIdentity,
  loadOpsxChangeDesignBinding,
  normalizeExecutionContext,
  resolveActiveOpsxChangeContext,
  resolveAuthoritativeExecutionIdentity,
} from "./opsx-break-glass-execution-identity.mjs";
import {
  STATE_CLOSED_VERIFIED,
  STATE_CONTINUE_WITH_EXCEPTION,
  appendIncidentEvent,
  findOpenIncidents,
  incidentAlreadyApplied,
  incidentLedgerPath,
  readIncidentLedger,
} from "./opsx-break-glass-ledger.mjs";
import { validateRevalidationDurableReceipt } from "./opsx-break-glass-revalidation.mjs";
import {
  ISSUER_HMAC_ENV,
  VERIFY_HMAC_ENV,
  executorSelfSigningForbidden,
  issueGrantToTrustedStore,
  loadTrustedBreakGlassGrant,
  ownerAttestationPath,
  resolveVerifySecret,
  validateOwnerAttestationRecord,
  verifyGrantSignature,
} from "./opsx-break-glass-trusted-grant.mjs";
import { writeSetAllows } from "./opsx-break-glass-write-guard.mjs";
import { nativeGateRevalidationDeferredForV1 } from "./opsx-break-glass-capability-backlog.mjs";

export const BREAK_GLASS_MARKER = "OPSX_BREAK_GLASS_GO";
export { GRANT_SCHEMA };
export const INCIDENT_EVENT_SCHEMA = "opsx.break_glass_incident_event";
export const EXCEPTION_RECEIPT_SCHEMA = "opsx.break_glass_exception_receipt";

export const RECEIPT_PROCESS_GATE_BYPASSED = "PROCESS_GATE_BYPASSED";
export const RECEIPT_CONTINUE_WITH_EXCEPTION = "CONTINUE_WITH_EXCEPTION";
/** Process gate outcome when Owner invoked /opsx-break-glass (not a forged PASS). */
export const OWNER_OVERRIDDEN = "OWNER_OVERRIDDEN";
export const GRANT_PROVENANCE_OWNER_LOCAL = "owner_local_override";
export const RECEIPT_REVALIDATION_REQUIRED = "REVALIDATION_REQUIRED";

export const STATE_BLOCKED = "BLOCKED";
export const STATE_INCIDENT_RECORDED = "INCIDENT_RECORDED";
export const STATE_EXCEPTION_AUTHORIZED = "EXCEPTION_AUTHORIZED";
export { STATE_CONTINUE_WITH_EXCEPTION, STATE_CLOSED_VERIFIED };
export const STATE_REVALIDATION_REQUIRED = "REVALIDATION_REQUIRED";
export const STATE_DENIED = "DENIED";
export const STATE_PAUSED = "PAUSED";
export const STATE_EXPIRED = "EXPIRED";
export const STATE_REVOKED = "REVOKED";

export const OWNER_HMAC_ENV = VERIFY_HMAC_ENV;
export { ISSUER_HMAC_ENV, VERIFY_HMAC_ENV };
export { incidentLedgerPath, readIncidentLedger, findOpenIncidents };
export { writeSetAllows };
export {
  normalizeExecutionContext,
  resolveAuthoritativeExecutionIdentity,
  bindGrantToRuntimeIdentity,
  loadOpsxChangeDesignBinding,
  resolveActiveOpsxChangeContext,
};
export {
  loadTrustedBreakGlassGrant,
  issueGrantToTrustedStore,
  validateOwnerAttestationRecord,
};

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
export const DEFAULT_REPO_ROOT = defaultRepoRootFromToolFile(import.meta.url);

const FORBIDDEN_EVIDENCE_MINT = new Set([
  "RED_PROVEN",
  "GREEN_PROVEN",
  "IMPL_OK",
  "VERIFY_PASS",
  "INTEGRATED",
  "ARCHIVE_READY",
]);

const PROCESS_ELIGIBLE_FAILURES = new Set([
  "SEMANTIC_ORACLE_UNPROVEN",
  "NO_ELIGIBLE_SLICE",
  "SELECTOR_NON_CONVERGENCE",
  "PROCESS_RECEIPT_INCOMPATIBLE",
  "ORCHESTRATION_DEADLOCK",
  "RECOVERY_MECHANISM_FAILURE",
  "PROCESS_ADAPTER_MISSING",
  "PROCESS_VALIDATION_FALSE_BLOCK",
  "IDENTITY_ALGORITHM_UNRESOLVED",
  "TEST_COVERAGE_ARTIFACT_GAP",
  "FROZEN_TEST_PATH_MISSING",
]);

const PRODUCT_FAILURE_DENY = new Set([
  "INTENDED_BEHAVIOR_MISSING",
  "PRODUCT_TEST_FAILED",
  "UNRESOLVED_PRODUCT_DEFECT",
]);

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

export function detectBreakGlassMarker(text) {
  return String(text || "").includes(BREAK_GLASS_MARKER);
}

export function classifyBreakGlassEligibility({
  blockedGate,
  failureCode,
  processDefectAttested = false,
  processDefectPredicate = null,
} = {}) {
  const gate = String(blockedGate || "");
  const code = String(failureCode || "");
  if (!gate || !code) {
    return { eligible: false, disposition: "AMBIGUOUS", code: "MISSING_BLOCK_CONTEXT" };
  }
  if (PRODUCT_FAILURE_DENY.has(code)) {
    return { eligible: false, disposition: "DENIED", code: "PRODUCT_FAILURE_NOT_ELIGIBLE" };
  }
  if (!PROCESS_ELIGIBLE_FAILURES.has(code)) {
    return { eligible: false, disposition: "AMBIGUOUS", code: "FAILURE_NOT_CLASSIFIED" };
  }
  if (code === "NO_ELIGIBLE_SLICE" && !processDefectAttested) {
    return { eligible: false, disposition: "AMBIGUOUS", code: "NO_ELIGIBLE_SLICE_UNATTESTED" };
  }
  if (code === "SEMANTIC_ORACLE_UNPROVEN") {
    if (
      !processDefectPredicate ||
      processDefectPredicate.class !== "OPSX_GATE_DEFECT" ||
      !processDefectPredicate.independent_witness_ref
    ) {
      return { eligible: false, disposition: "PAUSED", code: "PROCESS_DEFECT_UNPROVEN" };
    }
  }
  return {
    eligible: true,
    disposition: "PROCESS_DEFECT",
    code: "PROCESS_DEFECT_ELIGIBLE",
    category: code,
  };
}

/**
 * Validate grant against authoritative runtime identity (P0-1). Ignores grantSource claims.
 */
export function validateBreakGlassOwnerGrant(grant, { runtimeIdentity, nowMs = Date.now() } = {}) {
  const selfSign = executorSelfSigningForbidden();
  if (!selfSign.ok) return selfSign;
  const secretRes = resolveVerifySecret();
  if (!secretRes.ok) return secretRes;
  if (!isObject(grant)) {
    return { ok: false, code: "DENIED", reason: "GRANT_CORRUPT" };
  }
  if (grant.schema !== GRANT_SCHEMA || grant.schema_version !== 1) {
    return { ok: false, code: "DENIED", reason: "GRANT_SCHEMA_INVALID" };
  }
  if (grant.grant_provenance !== "owner_trusted_store") {
    return { ok: false, code: "DENIED", reason: "GRANT_NOT_FROM_TRUSTED_STORE" };
  }
  if (grant.issuer_role !== "owner_break_glass_issuer") {
    return { ok: false, code: "DENIED", reason: "EXECUTOR_SELF_ISSUED_GRANT" };
  }
  const sig = verifyGrantSignature(grant, secretRes.secret);
  if (!sig.ok) return sig;
  if (grant.revoked_at) {
    return { ok: false, code: "REVOKED", reason: "GRANT_REVOKED" };
  }
  const expiresAt = Date.parse(grant.expires_at || "");
  if (!Number.isFinite(expiresAt) || expiresAt <= nowMs) {
    return { ok: false, code: "EXPIRED", reason: "GRANT_EXPIRED" };
  }
  const identity = runtimeIdentity?.ok ? runtimeIdentity.identity : runtimeIdentity;
  if (
    !identity?.change_id ||
    !identity?.execution_id ||
    !identity?.revision ||
    !identity?.blocked_gate ||
    !identity?.failure_code
  ) {
    return { ok: false, code: "DENIED", reason: "RUNTIME_IDENTITY_REQUIRED" };
  }
  const bind = bindGrantToRuntimeIdentity(grant, identity);
  if (!bind.ok) return bind;
  const eligibility = classifyBreakGlassEligibility({
    blockedGate: identity.blocked_gate,
    failureCode: identity.failure_code,
    processDefectAttested: Boolean(identity.process_defect_attested),
    processDefectPredicate: identity.process_defect_predicate || null,
  });
  if (!eligibility.eligible) {
    return { ok: false, code: eligibility.disposition, reason: eligibility.code };
  }
  return { ok: true, grant_id: grant.grant_id, idempotency_key: grant.idempotency_key };
}

export function evaluateBreakGlassActivation({
  markerPresent,
  grant,
  repoRoot,
  runtimeIdentity,
  attestationRepoRoot = null,
} = {}) {
  if (!markerPresent) {
    return { ok: false, code: "DENIED", reason: "MARKER_ABSENT" };
  }
  const attRoot = attestationRepoRoot || repoRoot;
  const att = validateOwnerAttestationRecord(attRoot, grant);
  if (!att.ok) return att;
  const validated = validateBreakGlassOwnerGrant(grant, { runtimeIdentity });
  if (!validated.ok) return validated;
  return {
    ok: true,
    code: STATE_EXCEPTION_AUTHORIZED,
    grant_id: validated.grant_id,
    idempotency_key: validated.idempotency_key,
  };
}

export function assertReceiptDoesNotMintForbiddenEvidence(receipt) {
  const blob = stableStringify(receipt || {});
  for (const forbidden of FORBIDDEN_EVIDENCE_MINT) {
    if (blob.includes(`"status":"${forbidden}"`) || blob.includes(`"status": "${forbidden}"`)) {
      throw new Error(`FORBIDDEN_FAKE_EVIDENCE:${forbidden}`);
    }
  }
}

export function buildExceptionReceipt({
  exceptionId,
  grant,
  transition,
  executionId,
  checksNotPerformed = [],
} = {}) {
  if (FORBIDDEN_EVIDENCE_MINT.has(transition)) {
    throw new Error(`FORBIDDEN_FAKE_EVIDENCE:${transition}`);
  }
  const ownerLocal = grant.grant_provenance === GRANT_PROVENANCE_OWNER_LOCAL;
  const receipt = {
    schema: EXCEPTION_RECEIPT_SCHEMA,
    schema_version: 1,
    exception_id: exceptionId,
    grant_id: grant.grant_id,
    execution_id: executionId || grant.execution_id,
    receipt_kind: RECEIPT_PROCESS_GATE_BYPASSED,
    transition,
    authority: ownerLocal ? "owner_local_override" : "break_glass_owner_grant",
    blocked_gate: grant.blocked_gate,
    original_failure_code: grant.original_failure_code,
    red_debt_remains: grant.blocked_gate.includes("RED"),
    checks_not_performed: checksNotPerformed,
    revalidation_required: true,
    issued_at: new Date().toISOString(),
  };
  if (ownerLocal) {
    receipt.gate_check_status = OWNER_OVERRIDDEN;
  }
  return receipt;
}

export function buildIncidentEvent(fields) {
  const {
    incidentId,
    grant,
    executionContext,
    failedCommand,
    expectedBehavior,
    observedBehavior,
    evidenceReferences = [],
    whyProcessDefect,
    actionsPerformed = [],
    actualVerificationResults = null,
    checksNotPerformed = [],
    remainingRisks = [],
    recoveryContract,
    incidentStatus,
    exceptionState,
    gateCheckStatus = null,
  } = fields;
  const ts = new Date().toISOString();
  return {
    schema: INCIDENT_EVENT_SCHEMA,
    schema_version: 1,
    event_id: randomUUID(),
    incident_id: incidentId,
    timestamp: ts,
    repo: grant.repository_identity?.repo,
    branch: grant.repository_identity?.branch,
    revision: grant.repository_identity?.revision,
    change_id: grant.change_id,
    execution_id: grant.execution_id,
    slice: executionContext?.slice_id || null,
    failed_command: failedCommand,
    failed_gate: grant.blocked_gate,
    failure_code: grant.original_failure_code,
    expected_behavior: expectedBehavior,
    observed_behavior: observedBehavior,
    evidence_references: evidenceReferences,
    why_process_defect: whyProcessDefect,
    authorized_bypass_scope: {
      allowed_transitions: grant.allowed_transitions,
      bounded_write_set: grant.bounded_write_set,
    },
    actions_performed: actionsPerformed,
    actual_verification_results: actualVerificationResults,
    checks_not_performed: checksNotPerformed,
    remaining_risks: remainingRisks,
    recovery_contract: recoveryContract,
    incident_status: incidentStatus,
    exception_state: exceptionState,
    grant_id: grant.grant_id,
    idempotency_key: grant.idempotency_key,
    ...(gateCheckStatus ? { gate_check_status: gateCheckStatus } : {}),
  };
}

export function transitionAllows(grant, transition) {
  const allowed = new Set(grant.allowed_transitions || []);
  const forbidden = new Set(grant.forbidden_transitions || []);
  if (forbidden.has(transition)) return false;
  return allowed.has(transition);
}

export function applyBreakGlassException({
  repoRoot,
  grant,
  markerPresent,
  runtimeIdentity,
  failedCommand,
  expectedBehavior,
  observedBehavior,
  whyProcessDefect,
  downstreamStage = "EXCEPTION_APPLY_CONTINUE",
  checksNotPerformed = ["RED_PROVEN"],
  verificationFn = null,
} = {}) {
  const activation = evaluateBreakGlassActivation({
    markerPresent,
    grant,
    repoRoot,
    runtimeIdentity,
  });
  if (!activation.ok) {
    return { ok: false, code: activation.code || STATE_DENIED, reason: activation.reason };
  }
  if (!transitionAllows(grant, downstreamStage)) {
    return { ok: false, code: STATE_DENIED, reason: "TRANSITION_NOT_IN_GRANT" };
  }
  if (incidentAlreadyApplied(repoRoot, grant.idempotency_key)) {
    return {
      ok: true,
      idempotent: true,
      code: STATE_CONTINUE_WITH_EXCEPTION,
      exception_state: STATE_CONTINUE_WITH_EXCEPTION,
      downstream_stage: downstreamStage,
    };
  }
  const incidentId = sha256Hex(
    `${grant.grant_id}:${grant.execution_id}:${grant.blocked_gate}`,
  ).slice(0, 16);
  const executionContext = normalizeExecutionContext({}, runtimeIdentity.identity || runtimeIdentity);
  const incidentBlocked = buildIncidentEvent({
    incidentId,
    grant,
    executionContext,
    failedCommand,
    expectedBehavior,
    observedBehavior,
    whyProcessDefect,
    checksNotPerformed,
    incidentStatus: "OPEN",
    exceptionState: STATE_INCIDENT_RECORDED,
    recoveryContract: {
      revalidate_gates: grant.revalidation_obligations,
      close_via: STATE_CLOSED_VERIFIED,
    },
  });
  const recorded = appendIncidentEvent(repoRoot, incidentBlocked, {
    dedupeKey: `${grant.idempotency_key}:incident_recorded`,
  });
  if (!recorded.ok) {
    return { ok: false, code: STATE_DENIED, reason: "NO_BYPASS_WITHOUT_INCIDENT" };
  }
  const actualVerificationResults =
    typeof verificationFn === "function" ? verificationFn() : null;
  const exceptionId = sha256Hex(`${incidentId}:${grant.idempotency_key}`).slice(0, 16);
  const receipt = buildExceptionReceipt({
    exceptionId,
    grant,
    transition: downstreamStage,
    executionId: executionContext.execution_id,
    checksNotPerformed,
  });
  const continueEvent = buildIncidentEvent({
    incidentId,
    grant,
    executionContext,
    failedCommand,
    expectedBehavior,
    observedBehavior,
    whyProcessDefect,
    actionsPerformed: ["PROCESS_GATE_BYPASSED", downstreamStage],
    actualVerificationResults,
    checksNotPerformed,
    remainingRisks: ["RED evidence debt remains until revalidation"],
    incidentStatus: "OPEN",
    exceptionState: STATE_CONTINUE_WITH_EXCEPTION,
    recoveryContract: incidentBlocked.recovery_contract,
  });
  const continued = appendIncidentEvent(repoRoot, continueEvent, {
    dedupeKey: `${grant.idempotency_key}:continue`,
  });
  if (!continued.ok) {
    return { ok: false, code: STATE_PAUSED, reason: "PARTIAL_INCIDENT_WRITE" };
  }
  assertReceiptDoesNotMintForbiddenEvidence(receipt);
  return {
    ok: true,
    code: STATE_CONTINUE_WITH_EXCEPTION,
    exception_state: STATE_CONTINUE_WITH_EXCEPTION,
    exception_receipt: receipt,
    incident_id: incidentId,
    downstream_stage: downstreamStage,
    receipt_kind: RECEIPT_PROCESS_GATE_BYPASSED,
    revalidation_required: true,
  };
}

/**
 * Close incident only from durable revalidation receipt on disk (P0-3).
 */
export function revalidateBreakGlassIncident({
  repoRoot,
  incidentId,
  grant = null,
  grantId = null,
  runtimeIdentity,
  revalidationEvidence = null,
  nowMs = Date.now(),
} = {}) {
  let effectiveGrant = grant;
  if (grantId) {
    const loaded = loadTrustedBreakGlassGrant(repoRoot, grantId);
    if (!loaded.ok) return loaded;
    effectiveGrant = loaded.grant;
  }
  if (!effectiveGrant) {
    return { ok: false, code: STATE_DENIED, reason: "GRANT_REQUIRED" };
  }
  grant = effectiveGrant;
  if (revalidationEvidence) {
    return {
      ok: false,
      code: STATE_REVALIDATION_REQUIRED,
      reason: "INLINE_REVALIDATION_EVIDENCE_REJECTED",
    };
  }
  const v1Defer = nativeGateRevalidationDeferredForV1(repoRoot);
  if (v1Defer.deferred) {
    return {
      ok: false,
      code: STATE_REVALIDATION_REQUIRED,
      reason: v1Defer.reason,
      capability_gap: v1Defer.entry?.id,
    };
  }
  const open = findOpenIncidents(repoRoot, { executionId: grant?.execution_id });
  const incident = open.find((ev) => ev.incident_id === incidentId);
  if (!incident) {
    return { ok: false, code: STATE_DENIED, reason: "INCIDENT_NOT_OPEN" };
  }
  const grantCheck = validateBreakGlassOwnerGrant(grant, { runtimeIdentity, nowMs });
  if (!grantCheck.ok && grantCheck.reason !== "GRANT_EXPIRED") {
    return { ok: false, code: grantCheck.code, reason: grantCheck.reason };
  }
  const identity = runtimeIdentity.ok ? runtimeIdentity.identity : runtimeIdentity;
  const durable = validateRevalidationDurableReceipt({
    repoRoot,
    incident,
    runtimeIdentity: identity,
    grant,
  });
  if (!durable.ok) return durable;
  const closeEvent = buildIncidentEvent({
    incidentId,
    grant,
    executionContext: { slice_id: incident.slice },
    failedCommand: incident.failed_command,
    expectedBehavior: incident.expected_behavior,
    observedBehavior: durable.receipt.observed_behavior,
    whyProcessDefect: incident.why_process_defect,
    actionsPerformed: ["REVALIDATION_RUN", "INCIDENT_CLOSE"],
    actualVerificationResults: durable.receipt,
    evidenceReferences: [durable.receipt_path],
    checksNotPerformed: [],
    remainingRisks: [],
    incidentStatus: STATE_CLOSED_VERIFIED,
    exceptionState: STATE_CLOSED_VERIFIED,
    recoveryContract: { closed_at: new Date(nowMs).toISOString() },
  });
  const closed = appendIncidentEvent(repoRoot, closeEvent, {
    dedupeKey: `${incidentId}:closed`,
  });
  if (!closed.ok) {
    return { ok: false, code: STATE_PAUSED, reason: "CLOSE_WRITE_FAILED" };
  }
  return { ok: true, code: STATE_CLOSED_VERIFIED, incident_id: incidentId };
}

export function buildOwnerGrantTemplate(opts = {}) {
  const {
    changeId,
    executionId,
    revision,
    repo = "revoluxe-ai/stroy-system",
    branch = "feature/opsx-break-glass-v1",
    blockedGate = "APPLY_RED_OPERATIVE",
    failureCode = "SEMANTIC_ORACLE_UNPROVEN",
    ownerIdentity = "owner@vkita",
    ownerAttestationRef = "owner-message:break-glass-pilot",
    boundedWriteSet = ["openspec/changes/break-glass-pilot/"],
    expiresInMs = 3600_000,
  } = opts;
  return {
    schema: GRANT_SCHEMA,
    schema_version: 1,
    grant_id: randomUUID(),
    idempotency_key: sha256Hex(`${executionId}:${blockedGate}:${failureCode}`),
    owner_authorization_identity: ownerIdentity,
    owner_attestation_ref: ownerAttestationRef,
    change_id: changeId,
    execution_id: executionId,
    repository_identity: { repo, branch, revision },
    blocked_gate: blockedGate,
    original_failure_code: failureCode,
    eligible_category: failureCode,
    allowed_transitions: [
      "EXCEPTION_APPLY_CONTINUE",
      "APPLY_IMPLEMENT",
      "EXCEPTION_CLOSEOUT",
      "DEVELOPMENT_COMPLETE_WITH_EXCEPTION",
    ],
    forbidden_transitions: ["ARCHIVE_READY", "VERIFY_PASS", "RED_PROVEN"],
    bounded_write_set: boundedWriteSet,
    expires_at: new Date(Date.now() + expiresInMs).toISOString(),
    revalidation_obligations: [blockedGate, "APPLY_RED_OPERATIVE"],
  };
}

export function signGrantTemplate(grant, issuerSecret, repoRoot = DEFAULT_REPO_ROOT) {
  return issueGrantToTrustedStore(repoRoot, grant, issuerSecret);
}

export function writeOwnerAttestationRecord(repoRoot, ref, grantId) {
  const id = String(ref).replace(/^owner-message:/, "");
  const path = ownerAttestationPath(repoRoot, ref);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(
    path,
    `${JSON.stringify(
      {
        schema: "opsx.break_glass_owner_attestation",
        schema_version: 1,
        owner_attestation_ref: ref,
        bound_grant_id: grantId,
        holder: "owner",
      },
      null,
      2,
    )}\n`,
    "utf8",
  );
  return path;
}
