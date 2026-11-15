/**
 * Universal Break Glass V2 — any OPSX blocker, owner-bound grant, durable debt journal.
 *
 * Independent of selectNextPipelineStage, RED/GREEN chain, recovery selector, and
 * verification-plan gates. Does not mint fake GREEN / VERIFY_PASS evidence.
 */
import { randomUUID } from "node:crypto";
import { GRANT_SCHEMA, sha256Hex } from "./opsx-break-glass-crypto.mjs";
import {
  executionIdentityDigest,
  normalizeExecutionContext,
} from "./opsx-break-glass-execution-identity.mjs";
import {
  STATE_CONTINUE_WITH_EXCEPTION,
  appendIncidentEvent,
  findOpenIncidents,
  incidentAlreadyApplied,
} from "./opsx-break-glass-ledger.mjs";
import {
  ACTIVATION_STATE_DEBT_RECORDED,
  ACTIVATION_STATE_TRANSACTION_COMPLETE,
  DEBT_EVENT_SCHEMA,
  appendDebtEvent,
  readDebtLedger,
  universalActivationDebtRecorded,
  universalActivationTransactionComplete,
} from "./opsx-break-glass-debt-journal.mjs";
import {
  GRANT_PROVENANCE_OWNER_LOCAL,
  OWNER_OVERRIDDEN,
  assertReceiptDoesNotMintForbiddenEvidence,
  buildExceptionReceipt,
  buildIncidentEvent,
  transitionAllows,
} from "./opsx-break-glass.mjs";
import {
  executorSelfSigningForbidden,
  issueGrantToTrustedStore,
  loadTrustedBreakGlassGrant,
  resolveVerifySecret,
  validateOwnerAttestationRecord,
  verifyGrantSignature,
} from "./opsx-break-glass-trusted-grant.mjs";
import {
  EXCEPTION_CLOSEOUT_SCHEMA,
  PROOF_CURRENT_GREEN,
  PROOF_EXCEPTION_CLOSEOUT,
  PROOF_EXCEPTION_COMPLETION,
  PROOF_PROCESS_GATE_DEFERRAL,
  evaluateDevelopmentCompleteWithExceptionGate,
  evaluateSliceDevelopmentProof,
} from "./opsx-break-glass-exception-closeout.mjs";
import { EXCEPTION_COMPLETION_SCHEMA } from "./opsx-break-glass-exception-completion.mjs";

const ALLOWED_SLICE_PROOF_KINDS = new Set([
  PROOF_CURRENT_GREEN,
  PROOF_EXCEPTION_COMPLETION,
  PROOF_EXCEPTION_CLOSEOUT,
  PROOF_PROCESS_GATE_DEFERRAL,
]);
import { COMPLETION_DEVELOPMENT_WITH_EXCEPTION } from "./opsx-break-glass-exception-completion.mjs";
import {
  ensureProcessGateDeferralForChange,
  processGateMatchesOwnerOverride,
} from "./opsx-break-glass-process-gate-deferral.mjs";

export const BREAK_GLASS_MODE_UNIVERSAL_V2 = "universal_v2";
export const BLOCKER_SCOPE_ANY = "ANY_OPSX_BLOCKER";
export const FAILURE_UNKNOWN = "UNKNOWN";
export const GATE_ANY = "ANY";

export const STATE_BLOCKED = "BLOCKED";
export const STATE_EXCEPTION_REQUESTED = "EXCEPTION_REQUESTED";
export const STATE_OWNER_AUTHORIZED = "OWNER_AUTHORIZED";
export const STATE_DEBT_RECORDED = "DEBT_RECORDED";
export const STATE_DEVELOPMENT_COMPLETE_WITH_EXCEPTION = COMPLETION_DEVELOPMENT_WITH_EXCEPTION;
export { STATE_CONTINUE_WITH_EXCEPTION };
export const STATE_DENIED = "DENIED";
export const STATE_PAUSED = "PAUSED";
export const STATE_EXPIRED = "EXPIRED";
export const STATE_REVOKED = "REVOKED";

const FORBIDDEN_GRANT_TRANSITIONS = new Set([
  "ARCHIVE_READY",
  "VERIFY_PASS",
  "RED_PROVEN",
  "GREEN_PROVEN",
  "INTEGRATED",
  "IMPL_OK",
]);

export const PRODUCT_OUTCOME_DENY = new Set([
  "INTENDED_BEHAVIOR_MISSING",
  "PRODUCT_TEST_FAILED",
  "UNRESOLVED_PRODUCT_DEFECT",
]);

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

/**
 * Digest for bound_execution_identity_digest: ANY/UNKNOWN grants pin canonical
 * gate/code sentinels so activation may use arbitrary runtime blocker identity.
 */
export function executionIdentityDigestForUniversalV2Grant(grant, runtimeIdentity) {
  const grantGate = String(grant?.blocked_gate || "");
  const grantCode = String(grant?.original_failure_code || FAILURE_UNKNOWN);
  const identity = runtimeIdentity?.ok ? runtimeIdentity.identity : runtimeIdentity;
  return executionIdentityDigest({
    ...identity,
    blocked_gate: grantGate === GATE_ANY ? GATE_ANY : identity?.blocked_gate,
    failure_code: grantCode === FAILURE_UNKNOWN ? FAILURE_UNKNOWN : identity?.failure_code,
  });
}

/**
 * Universal eligibility: any OPSX blocker except explicit product-outcome failures.
 */
export function classifyUniversalBlockerEligibility({ blockedGate, failureCode } = {}) {
  const gate = String(blockedGate || "").trim();
  const code = String(failureCode || FAILURE_UNKNOWN).trim() || FAILURE_UNKNOWN;
  if (!gate) {
    return { eligible: false, disposition: STATE_DENIED, code: "MISSING_BLOCKED_GATE" };
  }
  if (PRODUCT_OUTCOME_DENY.has(code)) {
    return { eligible: false, disposition: STATE_DENIED, code: "PRODUCT_OUTCOME_NOT_ESCAPABLE" };
  }
  return {
    eligible: true,
    disposition: "UNIVERSAL_OPSX_BLOCKER",
    code: "UNIVERSAL_BLOCKER_ELIGIBLE",
    blocked_gate: gate,
    failure_code: code,
  };
}

/**
 * CONTINUE under universal V2 requires explicit owner risk acceptance for broad/UNKNOWN blockers.
 */
export function assertOwnerRiskAcceptanceForContinue(grant, runtimeIdentity) {
  const identity = runtimeIdentity?.ok ? runtimeIdentity.identity : runtimeIdentity;
  const runtimeCode = String(identity?.failure_code || FAILURE_UNKNOWN).trim() || FAILURE_UNKNOWN;
  const grantCode = String(grant?.original_failure_code || FAILURE_UNKNOWN).trim() || FAILURE_UNKNOWN;
  const grantGate = String(grant?.blocked_gate || "");
  const needsRisk =
    runtimeCode === FAILURE_UNKNOWN ||
    grantCode === FAILURE_UNKNOWN ||
    grantGate === GATE_ANY ||
    identity?.blocked_gate === GATE_ANY;
  if (!needsRisk) {
    return { ok: true };
  }
  const ra = grant?.owner_risk_acceptance;
  if (!isObject(ra) || ra.acknowledged !== true) {
    return { ok: false, code: STATE_DENIED, reason: "OWNER_RISK_ACCEPTANCE_REQUIRED" };
  }
  const next = String(ra.bounded_next_action || "").trim();
  if (!next || next.length > 512) {
    return { ok: false, code: STATE_DENIED, reason: "BOUNDED_NEXT_ACTION_REQUIRED" };
  }
  return { ok: true };
}

/**
 * Full activation postconditions: debt row, CONTINUE incident, OPEN incident, transaction marker.
 */
export function universalActivationComplete(repoRoot, grant) {
  const key = grant.idempotency_key;
  if (!universalActivationDebtRecorded(repoRoot, key)) {
    return { complete: false, reason: "DEBT_NOT_RECORDED" };
  }
  if (!incidentAlreadyApplied(repoRoot, key)) {
    return { complete: false, reason: "CONTINUE_INCIDENT_MISSING" };
  }
  const open = findOpenIncidents(repoRoot, {
    changeId: grant.change_id,
    executionId: grant.execution_id,
  });
  const hasOpen = open.some(
    (ev) => ev.idempotency_key === key && ev.incident_status === "OPEN",
  );
  if (!hasOpen) {
    return { complete: false, reason: "OPEN_INCIDENT_MISSING" };
  }
  if (!universalActivationTransactionComplete(repoRoot, key)) {
    return { complete: false, reason: "ACTIVATION_TRANSACTION_INCOMPLETE" };
  }
  return { complete: true };
}

/**
 * Bind V2 grant: repo/branch/revision/change/execution strict; gate/code wildcard when ANY/UNKNOWN.
 */
export function bindUniversalV2GrantToRuntimeIdentity(grant, runtimeIdentity) {
  if (!isObject(grant) || !isObject(runtimeIdentity)) {
    return { ok: false, code: STATE_DENIED, reason: "BINDING_CONTEXT_MISSING" };
  }
  const rid = grant.repository_identity || {};
  const strict = [
    ["GRANT_REPO_MISMATCH", rid.repo, runtimeIdentity.repo],
    ["GRANT_BRANCH_MISMATCH", rid.branch, runtimeIdentity.branch],
    ["GRANT_REVISION_MISMATCH", rid.revision, runtimeIdentity.revision],
    ["GRANT_CHANGE_MISMATCH", grant.change_id, runtimeIdentity.change_id],
    ["GRANT_EXECUTION_MISMATCH", grant.execution_id, runtimeIdentity.execution_id],
  ];
  for (const [reason, grantVal, runtimeVal] of strict) {
    if (grantVal == null || runtimeVal == null) {
      return { ok: false, code: STATE_DENIED, reason: "BINDING_FIELD_MISSING" };
    }
    if (String(grantVal) !== String(runtimeVal)) {
      const code = reason === "GRANT_REVISION_MISMATCH" ? STATE_PAUSED : STATE_DENIED;
      return { ok: false, code, reason };
    }
  }
  const grantGate = String(grant.blocked_gate || "");
  const grantCode = String(grant.original_failure_code || FAILURE_UNKNOWN);
  if (grantGate !== GATE_ANY && grantGate !== String(runtimeIdentity.blocked_gate)) {
    return { ok: false, code: STATE_DENIED, reason: "GRANT_GATE_MISMATCH" };
  }
  if (
    grantCode !== FAILURE_UNKNOWN &&
    grantCode !== String(runtimeIdentity.failure_code)
  ) {
    return { ok: false, code: STATE_DENIED, reason: "GRANT_FAILURE_CODE_MISMATCH" };
  }
  if (grant.bound_execution_identity_digest) {
    const digest = executionIdentityDigestForUniversalV2Grant(grant, runtimeIdentity);
    if (grant.bound_execution_identity_digest !== digest) {
      return { ok: false, code: STATE_DENIED, reason: "EXECUTION_IDENTITY_DIGEST_MISMATCH" };
    }
  }
  return { ok: true };
}

export function validateUniversalV2OwnerGrant(grant, { runtimeIdentity, nowMs = Date.now() } = {}) {
  const selfSign = executorSelfSigningForbidden();
  if (!selfSign.ok) return selfSign;
  const secretRes = resolveVerifySecret();
  if (!secretRes.ok) return secretRes;
  if (!isObject(grant)) {
    return { ok: false, code: STATE_DENIED, reason: "GRANT_CORRUPT" };
  }
  if (grant.schema !== GRANT_SCHEMA || grant.schema_version !== 2) {
    return { ok: false, code: STATE_DENIED, reason: "GRANT_SCHEMA_INVALID" };
  }
  if (grant.break_glass_mode !== BREAK_GLASS_MODE_UNIVERSAL_V2) {
    return { ok: false, code: STATE_DENIED, reason: "GRANT_NOT_UNIVERSAL_V2" };
  }
  if (grant.blocker_scope !== BLOCKER_SCOPE_ANY) {
    return { ok: false, code: STATE_DENIED, reason: "BLOCKER_SCOPE_INVALID" };
  }
  if (grant.grant_provenance !== "owner_trusted_store") {
    return { ok: false, code: STATE_DENIED, reason: "GRANT_NOT_FROM_TRUSTED_STORE" };
  }
  if (grant.issuer_role !== "owner_break_glass_issuer") {
    return { ok: false, code: STATE_DENIED, reason: "EXECUTOR_SELF_ISSUED_GRANT" };
  }
  const sig = verifyGrantSignature(grant, secretRes.secret);
  if (!sig.ok) return sig;
  if (grant.revoked_at) {
    return { ok: false, code: STATE_REVOKED, reason: "GRANT_REVOKED" };
  }
  const expiresAt = Date.parse(grant.expires_at || "");
  if (!Number.isFinite(expiresAt) || expiresAt <= nowMs) {
    return { ok: false, code: STATE_EXPIRED, reason: "GRANT_EXPIRED" };
  }
  const identity = runtimeIdentity?.ok ? runtimeIdentity.identity : runtimeIdentity;
  if (!identity?.change_id || !identity?.execution_id || !identity?.revision) {
    return { ok: false, code: STATE_DENIED, reason: "RUNTIME_IDENTITY_REQUIRED" };
  }
  const bind = bindUniversalV2GrantToRuntimeIdentity(grant, identity);
  if (!bind.ok) return bind;
  const eligibility = classifyUniversalBlockerEligibility({
    blockedGate: identity.blocked_gate,
    failureCode: identity.failure_code,
  });
  if (!eligibility.eligible) {
    return { ok: false, code: eligibility.disposition, reason: eligibility.code };
  }
  const risk = assertOwnerRiskAcceptanceForContinue(grant, runtimeIdentity);
  if (!risk.ok) return risk;
  for (const t of grant.allowed_transitions || []) {
    if (FORBIDDEN_GRANT_TRANSITIONS.has(t)) {
      return { ok: false, code: STATE_DENIED, reason: "FORBIDDEN_TRANSITION_IN_GRANT" };
    }
  }
  return { ok: true, grant_id: grant.grant_id, idempotency_key: grant.idempotency_key };
}

/**
 * Owner-side issuance with digest bound to authoritative runtime identity.
 */
export function issueUniversalV2GrantToTrustedStore(repoRoot, grant, issuerSecret, runtimeIdentity) {
  const identity = runtimeIdentity?.ok ? runtimeIdentity.identity : runtimeIdentity;
  const body = {
    ...grant,
    bound_execution_identity_digest: executionIdentityDigestForUniversalV2Grant(grant, identity),
  };
  return issueGrantToTrustedStore(repoRoot, body, issuerSecret);
}

/**
 * Owner `/opsx-break-glass` local grant — no HMAC, no trusted-store issuance.
 */
export function buildOwnerLocalOverrideGrant(runtimeIdentity, opts = {}) {
  const identity = runtimeIdentity?.ok ? runtimeIdentity.identity : runtimeIdentity;
  const blockedGate = opts.blockedGate || identity?.blocked_gate || GATE_ANY;
  const failureCode = opts.failureCode || identity?.failure_code || FAILURE_UNKNOWN;
  const grant = buildUniversalV2OwnerGrantTemplate({
    changeId: identity.change_id,
    executionId: identity.execution_id,
    revision: identity.revision,
    repo: identity.repo,
    branch: identity.branch,
    blockedGate,
    failureCode,
    boundedWriteSet:
      opts.boundedWriteSet || ["openspec/changes/", "app/", "tests/", "opsx/", "src/"],
    ownerAttestationRef: "owner-message:opsx-break-glass-local",
    ownerRiskAcceptance: {
      acknowledged: true,
      failure_class: "OPSX_PROCESS_GATE",
      bounded_next_action: "Continue bounded development; verification debt remains OPEN",
    },
  });
  grant.grant_provenance = GRANT_PROVENANCE_OWNER_LOCAL;
  grant.grant_id = randomUUID();
  grant.idempotency_key = sha256Hex(
    `${identity.execution_id}:${identity.change_id}:${identity.branch}:owner_local_override`,
  );
  grant.bound_execution_identity_digest = executionIdentityDigestForUniversalV2Grant(
    grant,
    identity,
  );
  return grant;
}

export function validateOwnerLocalOverrideGrant(grant, { runtimeIdentity } = {}) {
  if (!isObject(grant)) {
    return { ok: false, code: STATE_DENIED, reason: "GRANT_CORRUPT" };
  }
  if (grant.grant_provenance !== GRANT_PROVENANCE_OWNER_LOCAL) {
    return { ok: false, code: STATE_DENIED, reason: "GRANT_NOT_OWNER_LOCAL" };
  }
  if (grant.schema !== GRANT_SCHEMA || grant.schema_version !== 2) {
    return { ok: false, code: STATE_DENIED, reason: "GRANT_SCHEMA_INVALID" };
  }
  if (grant.break_glass_mode !== BREAK_GLASS_MODE_UNIVERSAL_V2) {
    return { ok: false, code: STATE_DENIED, reason: "GRANT_NOT_UNIVERSAL_V2" };
  }
  const identity = runtimeIdentity?.ok ? runtimeIdentity.identity : runtimeIdentity;
  if (!identity?.change_id || !identity?.execution_id || !identity?.revision) {
    return { ok: false, code: STATE_DENIED, reason: "RUNTIME_IDENTITY_REQUIRED" };
  }
  const bind = bindUniversalV2GrantToRuntimeIdentity(grant, identity);
  if (!bind.ok) return bind;
  const eligibility = classifyUniversalBlockerEligibility({
    blockedGate: identity.blocked_gate,
    failureCode: identity.failure_code,
  });
  if (!eligibility.eligible) {
    return { ok: false, code: eligibility.disposition, reason: eligibility.code };
  }
  const risk = assertOwnerRiskAcceptanceForContinue(grant, runtimeIdentity);
  if (!risk.ok) return risk;
  for (const t of grant.allowed_transitions || []) {
    if (FORBIDDEN_GRANT_TRANSITIONS.has(t)) {
      return { ok: false, code: STATE_DENIED, reason: "FORBIDDEN_TRANSITION_IN_GRANT" };
    }
  }
  return { ok: true, grant_id: grant.grant_id, idempotency_key: grant.idempotency_key };
}

/**
 * Load grant embedded in debt journal during owner-local activation.
 */
export function loadGrantSnapshotFromDebtJournal(repoRoot, grantId) {
  const events = readDebtLedger(repoRoot);
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i];
    if (ev.grant_id === grantId && ev.grant_snapshot) {
      return { ok: true, grant: ev.grant_snapshot };
    }
  }
  return { ok: false, code: STATE_DENIED, reason: "GRANT_SNAPSHOT_NOT_IN_DEBT_JOURNAL" };
}

/**
 * True when an open CONTINUE incident covers this process gate (post /opsx-break-glass).
 */
export function ownerOverrideAllowsProcessGate({
  repoRoot,
  changeId,
  executionId = null,
  branch = null,
  blockedGate,
  failureCode = null,
} = {}) {
  if (!changeId || !blockedGate) {
    return { ok: false, code: "NO_OWNER_OVERRIDE", reason: "BINDING_INCOMPLETE" };
  }
  const open = findOpenIncidents(repoRoot, { changeId, executionId });
  for (const inc of open) {
    if (inc.exception_state !== STATE_CONTINUE_WITH_EXCEPTION) continue;
    if (branch && inc.branch && String(inc.branch) !== String(branch)) continue;
    const loaded = loadGrantSnapshotFromDebtJournal(repoRoot, inc.grant_id);
    const grant = loaded.ok ? loaded.grant : null;
    if (
      !processGateMatchesOwnerOverride({
        incident: inc,
        grant,
        blockedGate,
        failureCode,
      })
    ) {
      continue;
    }
    return {
      ok: true,
      status: OWNER_OVERRIDDEN,
      gate_check_status: OWNER_OVERRIDDEN,
      incident_id: inc.incident_id,
      grant_id: inc.grant_id,
      grant,
      idempotency_key: inc.idempotency_key,
      original_failure_code: inc.failure_code,
      original_blocked_gate: inc.failed_gate,
    };
  }
  return { ok: false, code: "NO_OWNER_OVERRIDE" };
}

/**
 * Single-command owner break glass (no secrets, no grant issuance).
 */
export function activateBreakGlassOwnerOverride({
  repoRoot,
  runtimeIdentity,
  downstreamStage = "EXCEPTION_APPLY_CONTINUE",
  failedCommand = "/opsx-break-glass",
  expectedBehavior = "pipeline continues under owner exception",
  observedBehavior = null,
  whyProcessDefect = "owner_opsx_break_glass",
  checksNotPerformed = ["ORDINARY_PROCESS_GATES"],
  qualityObligation = null,
  boundedWriteSet = null,
} = {}) {
  const grant = buildOwnerLocalOverrideGrant(runtimeIdentity, { boundedWriteSet });
  const identity = runtimeIdentity?.ok ? runtimeIdentity.identity : runtimeIdentity;
  const observed =
    observedBehavior ||
    `OPSX process gate blocked (${identity.failure_code}); continuing under ${OWNER_OVERRIDDEN}`;
  return activateBreakGlassUniversalV2({
    repoRoot,
    grant,
    runtimeIdentity,
    downstreamStage,
    failedCommand,
    expectedBehavior,
    observedBehavior: observed,
    whyProcessDefect,
    checksNotPerformed,
    qualityObligation,
  });
}

export function buildUniversalV2OwnerGrantTemplate(opts = {}) {
  const {
    changeId,
    executionId,
    revision,
    repo = "revoluxe-ai/stroy-system",
    branch = null,
    blockedGate = GATE_ANY,
    failureCode = FAILURE_UNKNOWN,
    ownerIdentity = "owner@vkita",
    ownerAttestationRef = "owner-message:universal-v2",
    boundedWriteSet = ["openspec/changes/"],
    stage = "DEVELOPMENT",
    scope = "change_scoped",
    expiresInMs = 3600_000,
    ownerRiskAcceptance = null,
  } = opts;
  const effectiveGate = blockedGate || GATE_ANY;
  const effectiveCode = failureCode || FAILURE_UNKNOWN;
  return {
    schema: GRANT_SCHEMA,
    schema_version: 2,
    break_glass_mode: BREAK_GLASS_MODE_UNIVERSAL_V2,
    blocker_scope: BLOCKER_SCOPE_ANY,
    grant_id: randomUUID(),
    idempotency_key: sha256Hex(
      `${executionId}:${effectiveGate}:${effectiveCode}:universal_v2`,
    ),
    owner_authorization_identity: ownerIdentity,
    owner_attestation_ref: ownerAttestationRef,
    change_id: changeId,
    execution_id: executionId,
    repository_identity: { repo, branch, revision },
    blocked_gate: effectiveGate,
    original_failure_code: effectiveCode,
    pipeline_stage: stage,
    exception_scope: scope,
    allowed_transitions: [
      "EXCEPTION_APPLY_CONTINUE",
      "DEVELOPMENT_COMPLETE_WITH_EXCEPTION",
      "QUALITY_GATE_DEFERRED",
    ],
    forbidden_transitions: [...FORBIDDEN_GRANT_TRANSITIONS],
    bounded_write_set: boundedWriteSet,
    expires_at: new Date(Date.now() + expiresInMs).toISOString(),
    revalidation_obligations: [],
    obligation_notes: [],
    owner_risk_acceptance:
      ownerRiskAcceptance ||
      {
        acknowledged: true,
        failure_class: "UNKNOWN_OPSX_BLOCKER",
        bounded_next_action:
          "Continue bounded change work; record verification debt; no fake GREEN/VERIFY_PASS",
      },
  };
}

function buildDebtEvent({
  grant,
  runtimeIdentity,
  activationState,
  stateTrail,
  obligation = null,
  ownerLocalOverride = false,
  actualGateResult = null,
}) {
  const identity = runtimeIdentity?.ok ? runtimeIdentity.identity : runtimeIdentity;
  return {
    schema: DEBT_EVENT_SCHEMA,
    schema_version: 1,
    event_id: randomUUID(),
    timestamp: new Date().toISOString(),
    grant_id: grant.grant_id,
    idempotency_key: grant.idempotency_key,
    change_id: grant.change_id,
    execution_id: grant.execution_id,
    blocked_gate: identity.blocked_gate,
    failure_code: identity.failure_code || FAILURE_UNKNOWN,
    pipeline_stage: grant.pipeline_stage,
    exception_scope: grant.exception_scope,
    bounded_write_set: grant.bounded_write_set,
    activation_state: activationState,
    state_trail: stateTrail,
    obligation,
    ...(ownerLocalOverride
      ? {
          gate_check_status: OWNER_OVERRIDDEN,
          actual_gate_result: actualGateResult,
          grant_snapshot: grant,
        }
      : {}),
  };
}

/**
 * Independent universal activation entrypoint (no pipeline selector / marker required).
 */
export function activateBreakGlassUniversalV2({
  repoRoot,
  grant = null,
  grantId = null,
  runtimeIdentity,
  downstreamStage = "EXCEPTION_APPLY_CONTINUE",
  failedCommand = "opsx-break-glass activate",
  expectedBehavior = "pipeline continues under owner exception",
  observedBehavior = "blocked by OPSX gate",
  whyProcessDefect = "universal_owner_exception",
  checksNotPerformed = ["ORDINARY_PROCESS_GATES"],
  qualityObligation = null,
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

  const isOwnerLocal = effectiveGrant.grant_provenance === GRANT_PROVENANCE_OWNER_LOCAL;
  const identityEarly = runtimeIdentity?.ok ? runtimeIdentity.identity : runtimeIdentity;
  const actualGateResult = isOwnerLocal
    ? {
        status: "FAILED",
        gate_check_status: OWNER_OVERRIDDEN,
        blocked_gate: identityEarly?.blocked_gate,
        failure_code: identityEarly?.failure_code || FAILURE_UNKNOWN,
      }
    : null;

  if (!isOwnerLocal) {
    const att = validateOwnerAttestationRecord(repoRoot, effectiveGrant);
    if (!att.ok) return att;
  }

  const stateTrail = [STATE_BLOCKED, STATE_EXCEPTION_REQUESTED];

  const validated = isOwnerLocal
    ? validateOwnerLocalOverrideGrant(effectiveGrant, { runtimeIdentity })
    : validateUniversalV2OwnerGrant(effectiveGrant, { runtimeIdentity });
  if (!validated.ok) {
    return { ok: false, code: validated.code || STATE_DENIED, reason: validated.reason };
  }
  stateTrail.push(STATE_OWNER_AUTHORIZED);

  if (!transitionAllows(effectiveGrant, downstreamStage)) {
    return { ok: false, code: STATE_DENIED, reason: "TRANSITION_NOT_IN_GRANT" };
  }

  const activationStatus = universalActivationComplete(repoRoot, effectiveGrant);
  if (activationStatus.complete) {
    const deferralRepair = ensureProcessGateDeferralForChange({
      repoRoot,
      changeId: effectiveGrant.change_id,
    });
    return {
      ok: true,
      idempotent: true,
      code: STATE_CONTINUE_WITH_EXCEPTION,
      exception_state: STATE_CONTINUE_WITH_EXCEPTION,
      state_trail: [...stateTrail, STATE_DEBT_RECORDED, STATE_CONTINUE_WITH_EXCEPTION],
      process_gate_deferral: deferralRepair.ok ? deferralRepair : { ok: false, code: deferralRepair.code },
      ...(isOwnerLocal ? { gate_check_status: OWNER_OVERRIDDEN } : {}),
    };
  }
  const debtOnly =
    universalActivationDebtRecorded(repoRoot, effectiveGrant.idempotency_key) &&
    !incidentAlreadyApplied(repoRoot, effectiveGrant.idempotency_key);
  const repairedActivation =
    debtOnly || activationStatus.reason === "ACTIVATION_TRANSACTION_INCOMPLETE";
  const continueWithoutDebt =
    incidentAlreadyApplied(repoRoot, effectiveGrant.idempotency_key) &&
    !universalActivationDebtRecorded(repoRoot, effectiveGrant.idempotency_key);
  if (continueWithoutDebt) {
    return {
      ok: false,
      code: STATE_PAUSED,
      reason: "INCOMPLETE_ACTIVATION_TRANSACTION",
      incomplete_code: activationStatus.reason,
    };
  }
  const identity = runtimeIdentity?.ok ? runtimeIdentity.identity : runtimeIdentity;
  const executionContext = normalizeExecutionContext({}, identity);
  const incidentId = sha256Hex(
    `${effectiveGrant.grant_id}:${effectiveGrant.execution_id}:${identity.blocked_gate}`,
  ).slice(0, 16);

  const debtOverride = {
    ownerLocalOverride: isOwnerLocal,
    actualGateResult,
  };
  let debtEventId = null;
  if (!universalActivationDebtRecorded(repoRoot, effectiveGrant.idempotency_key)) {
    const debtBody = buildDebtEvent({
      grant: effectiveGrant,
      runtimeIdentity: identity,
      activationState: ACTIVATION_STATE_DEBT_RECORDED,
      stateTrail: [...stateTrail, STATE_DEBT_RECORDED],
      obligation: qualityObligation,
      ...debtOverride,
    });
    const debtWritten = appendDebtEvent(repoRoot, debtBody, {
      dedupeKey: `${effectiveGrant.idempotency_key}:debt_recorded`,
    });
    if (!debtWritten.ok) {
      return { ok: false, code: STATE_DENIED, reason: "ACTIVATION_NOT_DEBT_RECORDED" };
    }
    debtEventId = debtBody.event_id;
  }
  stateTrail.push(STATE_DEBT_RECORDED);

  const skipIncidentWrites =
    incidentAlreadyApplied(repoRoot, effectiveGrant.idempotency_key) &&
    activationStatus.reason === "ACTIVATION_TRANSACTION_INCOMPLETE";

  if (skipIncidentWrites) {
    const txBody = buildDebtEvent({
      grant: effectiveGrant,
      runtimeIdentity: identity,
      activationState: ACTIVATION_STATE_TRANSACTION_COMPLETE,
      stateTrail: [...stateTrail, ACTIVATION_STATE_TRANSACTION_COMPLETE],
      obligation: qualityObligation,
      ...debtOverride,
    });
    const txWritten = appendDebtEvent(repoRoot, txBody, {
      dedupeKey: `${effectiveGrant.idempotency_key}:activation_tx`,
    });
    if (!txWritten.ok) {
      return { ok: false, code: STATE_PAUSED, reason: "PARTIAL_ACTIVATION_TRANSACTION_MARKER_FAILED" };
    }
    stateTrail.push(STATE_CONTINUE_WITH_EXCEPTION);
    const deferralRepaired = ensureProcessGateDeferralForChange({
      repoRoot,
      changeId: effectiveGrant.change_id,
    });
    return {
      ok: true,
      code: STATE_CONTINUE_WITH_EXCEPTION,
      exception_state: STATE_CONTINUE_WITH_EXCEPTION,
      incident_id: incidentId,
      downstream_stage: downstreamStage,
      state_trail: stateTrail,
      debt_event_id: debtEventId,
      repaired: true,
      universal_v2: true,
      process_gate_deferral: deferralRepaired,
      ...(isOwnerLocal ? { gate_check_status: OWNER_OVERRIDDEN } : {}),
    };
  }

  const incidentOverride = isOwnerLocal
    ? {
        gateCheckStatus: OWNER_OVERRIDDEN,
        actualVerificationResults: actualGateResult,
      }
    : {};

  const incidentBlocked = buildIncidentEvent({
    incidentId,
    grant: effectiveGrant,
    executionContext,
    failedCommand,
    expectedBehavior,
    observedBehavior,
    whyProcessDefect,
    checksNotPerformed,
    incidentStatus: "OPEN",
    exceptionState: STATE_DEBT_RECORDED,
    recoveryContract: {
      close_via: "OWNER_REVALIDATION_OR_ORDINARY_PROOF",
      universal_v2: true,
      ...(isOwnerLocal ? { owner_local_override: true } : {}),
    },
    ...incidentOverride,
  });
  const recorded = appendIncidentEvent(repoRoot, incidentBlocked, {
    dedupeKey: `${effectiveGrant.idempotency_key}:incident_recorded`,
  });
  if (!recorded.ok) {
    return { ok: false, code: STATE_PAUSED, reason: "PARTIAL_ACTIVATION_INCIDENT_FAILED" };
  }

  const exceptionId = sha256Hex(`${incidentId}:${effectiveGrant.idempotency_key}`).slice(0, 16);
  const receipt = buildExceptionReceipt({
    exceptionId,
    grant: effectiveGrant,
    transition: downstreamStage,
    executionId: executionContext.execution_id,
    checksNotPerformed,
  });
  assertReceiptDoesNotMintForbiddenEvidence(receipt);

  const continueEvent = buildIncidentEvent({
    incidentId,
    grant: effectiveGrant,
    executionContext,
    failedCommand,
    expectedBehavior,
    observedBehavior,
    whyProcessDefect,
    actionsPerformed: isOwnerLocal
      ? ["OWNER_LOCAL_OVERRIDE", downstreamStage]
      : ["UNIVERSAL_V2_ACTIVATE", downstreamStage],
    checksNotPerformed,
    remainingRisks: ["Process verification debt remains OPEN"],
    incidentStatus: "OPEN",
    exceptionState: STATE_CONTINUE_WITH_EXCEPTION,
    recoveryContract: incidentBlocked.recovery_contract,
    ...incidentOverride,
  });
  const continued = appendIncidentEvent(repoRoot, continueEvent, {
    dedupeKey: `${effectiveGrant.idempotency_key}:continue`,
  });
  if (!continued.ok) {
    return { ok: false, code: STATE_PAUSED, reason: "PARTIAL_ACTIVATION_CONTINUE_FAILED" };
  }

  stateTrail.push(STATE_CONTINUE_WITH_EXCEPTION);

  const txBody = buildDebtEvent({
    grant: effectiveGrant,
    runtimeIdentity: identity,
    activationState: ACTIVATION_STATE_TRANSACTION_COMPLETE,
    stateTrail: [...stateTrail, ACTIVATION_STATE_TRANSACTION_COMPLETE],
    obligation: qualityObligation,
    ...debtOverride,
  });
  const txWritten = appendDebtEvent(repoRoot, txBody, {
    dedupeKey: `${effectiveGrant.idempotency_key}:activation_tx`,
  });
  if (!txWritten.ok) {
    return { ok: false, code: STATE_PAUSED, reason: "PARTIAL_ACTIVATION_TRANSACTION_MARKER_FAILED" };
  }

  const deferralWritten = ensureProcessGateDeferralForChange({
    repoRoot,
    changeId: effectiveGrant.change_id,
  });
  if (!deferralWritten.ok) {
    return {
      ok: false,
      code: STATE_PAUSED,
      reason: "PROCESS_GATE_DEFERRAL_MATERIALIZE_FAILED",
      detail: deferralWritten,
    };
  }

  return {
    ok: true,
    code: STATE_CONTINUE_WITH_EXCEPTION,
    exception_state: STATE_CONTINUE_WITH_EXCEPTION,
    exception_receipt: receipt,
    incident_id: incidentId,
    downstream_stage: downstreamStage,
    state_trail: stateTrail,
    debt_event_id: debtEventId,
    repaired: repairedActivation,
    universal_v2: true,
    process_gate_deferral: deferralWritten,
    ...(isOwnerLocal ? { gate_check_status: OWNER_OVERRIDDEN, owner_local_override: true } : {}),
  };
}

/**
 * Record repo-wide quality gate deferral (e.g. mypy) without falsifying gate results.
 */
export function recordQualityGateDeferralUnderException({
  repoRoot,
  grant,
  runtimeIdentity,
  gateName,
  gateResult,
  obligation,
}) {
  const validated =
    grant?.grant_provenance === GRANT_PROVENANCE_OWNER_LOCAL
      ? validateOwnerLocalOverrideGrant(grant, { runtimeIdentity })
      : validateUniversalV2OwnerGrant(grant, { runtimeIdentity });
  if (!validated.ok) return validated;
  if (!transitionAllows(grant, "QUALITY_GATE_DEFERRED")) {
    return { ok: false, code: STATE_DENIED, reason: "TRANSITION_NOT_IN_GRANT" };
  }
  if (gateResult?.minted_pass === true || gateResult?.status === "PASS_FORGED") {
    return { ok: false, code: STATE_DENIED, reason: "FAKE_GATE_PASS_FORBIDDEN" };
  }
  const identity = runtimeIdentity?.ok ? runtimeIdentity.identity : runtimeIdentity;
  const body = buildDebtEvent({
    grant,
    runtimeIdentity: identity,
    activationState: "QUALITY_GATE_DEFERRED",
    stateTrail: [STATE_DEBT_RECORDED, "QUALITY_GATE_DEFERRED"],
    obligation: {
      gate: gateName,
      actual_result: gateResult,
      deferred: true,
      ...obligation,
    },
  });
  const written = appendDebtEvent(repoRoot, body, {
    dedupeKey: `${grant.idempotency_key}:quality:${gateName}`,
  });
  if (!written.ok) return written;
  return {
    ok: true,
    code: "QUALITY_GATE_DEFERRED",
    gate: gateName,
    actual_result_preserved: gateResult,
    obligation_recorded: true,
  };
}

/**
 * @param {object} row — row returned by evaluateSliceDevelopmentProof (on-disk revalidated)
 */
export function isAuthoritativeSliceDevelopmentProofRow(row, grant = null) {
  if (!row || row.ok === false) return false;
  const sliceId = row.slice_id ?? row.sliceId;
  if (!sliceId) return false;
  if (!row.proof_kind || !ALLOWED_SLICE_PROOF_KINDS.has(row.proof_kind)) return false;
  const grantId = grant?.grant_id;
  if (row.proof_kind === PROOF_EXCEPTION_CLOSEOUT) {
    const closeout = row.closeout;
    if (!closeout || closeout.schema !== EXCEPTION_CLOSEOUT_SCHEMA) return false;
    if (grantId && closeout.grant_id !== grantId) return false;
  } else if (row.proof_kind === PROOF_EXCEPTION_COMPLETION) {
    const completion = row.completion;
    if (!completion || completion.schema !== EXCEPTION_COMPLETION_SCHEMA) return false;
    if (grantId && completion.grant_id !== grantId) return false;
  } else if (row.proof_kind === PROOF_CURRENT_GREEN) {
    if (!row.implementation_path) return false;
  }
  return true;
}

function implementationProofInvalid(hint, extra = {}) {
  return {
    ok: false,
    code: STATE_DENIED,
    reason: "IMPLEMENTATION_PROOF_INVALID",
    hint,
    ...extra,
  };
}

/**
 * Bind closeout/completion proof artifacts to grant and runtime execution identity.
 */
export function bindSliceProofToGrantAndIdentity(proof, grant, runtimeIdentity) {
  if (!isAuthoritativeSliceDevelopmentProofRow(proof, grant)) return false;
  const identity = runtimeIdentity?.ok ? runtimeIdentity.identity : runtimeIdentity;
  if (proof.proof_kind === PROOF_EXCEPTION_CLOSEOUT) {
    const closeout = proof.closeout;
    if (closeout.change_id !== grant.change_id) return false;
    if (closeout.execution_id !== grant.execution_id) return false;
    const gri = grant.repository_identity || {};
    const cri = closeout.repository_identity || {};
    if (gri.revision && cri.revision && String(gri.revision) !== String(cri.revision)) {
      return false;
    }
    if (identity?.revision && cri.revision && String(identity.revision) !== String(cri.revision)) {
      return false;
    }
    const digest = executionIdentityDigest(identity || {});
    if (
      closeout.execution_identity_digest &&
      digest &&
      closeout.execution_identity_digest !== digest
    ) {
      return false;
    }
  }
  if (proof.proof_kind === PROOF_EXCEPTION_COMPLETION) {
    if (proof.completion.grant_id !== grant.grant_id) return false;
  }
  if (proof.proof_kind === PROOF_PROCESS_GATE_DEFERRAL) {
    const deferral = proof.deferral;
    if (!deferral || deferral.grant_id !== grant.grant_id) return false;
    if (deferral.change_id !== grant.change_id) return false;
    if (deferral.execution_id !== grant.execution_id) return false;
  }
  return true;
}

/**
 * Re-load per-slice proof from disk; never trust hand-built sliceProofSummary rows.
 */
export function revalidateAuthoritativeSliceProofs({
  grant,
  runtimeIdentity,
  repoRoot,
  changeRoot,
  design = null,
  maps = null,
  sliceProofSummary = null,
  upstreamBySlice = null,
  redBySlice = null,
  implementBySlice = null,
  greenBySlice = null,
  attestedCurrentGreenSliceIds = [],
} = {}) {
  const grantId = grant?.grant_id;
  const mapsRed = maps?.redBySlice || redBySlice || {};
  const mapsImpl = maps?.implementBySlice || implementBySlice || {};
  const mapsGreen = maps?.greenBySlice || greenBySlice || {};
  const upstreamMaps = maps || {
    redBySlice: mapsRed,
    implementBySlice: mapsImpl,
    greenBySlice: mapsGreen,
  };

  if (design && repoRoot && changeRoot) {
    const gate = evaluateDevelopmentCompleteWithExceptionGate({
      design,
      repoRoot,
      changeRoot,
      maps: upstreamMaps,
      grantId,
      upstreamBySlice,
      attestedCurrentGreenSliceIds,
    });
    if (!gate.ok) {
      return implementationProofInvalid(
        "evaluateDevelopmentCompleteWithExceptionGate failed; closeout must match on-disk receipts",
        { gate_failure: gate },
      );
    }
    if (Array.isArray(sliceProofSummary) && sliceProofSummary.length > 0) {
      const expected = new Set(
        sliceProofSummary.map((row) => String(row.slice_id ?? row.sliceId ?? "")),
      );
      const fromGate = new Set(gate.slice_proofs.map((p) => String(p.slice_id)));
      if (expected.size !== fromGate.size || [...expected].some((id) => !fromGate.has(id))) {
        return implementationProofInvalid("sliceProofSummary does not match design slice_order");
      }
    }
    for (const proof of gate.slice_proofs) {
      if (!bindSliceProofToGrantAndIdentity(proof, grant, runtimeIdentity)) {
        return implementationProofInvalid("gate slice proof not bound to grant / execution identity");
      }
    }
    return { ok: true, slice_proofs: gate.slice_proofs };
  }

  if (!repoRoot || !changeRoot) {
    return implementationProofInvalid(
      "repoRoot and changeRoot required to revalidate slice proof (or pass design for full gate)",
    );
  }
  if (!Array.isArray(sliceProofSummary) || sliceProofSummary.length === 0) {
    return {
      ok: false,
      code: STATE_DENIED,
      reason: "IMPLEMENTATION_PROOF_REQUIRED",
      hint: "Provide slice ids or design for evaluateSliceDevelopmentProof revalidation",
    };
  }

  const authoritative = [];
  for (const row of sliceProofSummary) {
    const sliceId = row.slice_id ?? row.sliceId;
    if (!sliceId) {
      return implementationProofInvalid("sliceProofSummary row missing slice_id");
    }
    const proof = evaluateSliceDevelopmentProof({
      repoRoot,
      changeRoot,
      sliceId,
      red: mapsRed[sliceId],
      implement: mapsImpl[sliceId],
      green: mapsGreen[sliceId],
      upstream: upstreamBySlice?.[sliceId],
      grantId,
      attestedCurrentGreenSliceIds,
    });
    if (!proof.ok) {
      return implementationProofInvalid("evaluateSliceDevelopmentProof failed for slice", {
        slice_id: sliceId,
        eval_failure: proof,
      });
    }
    if (!bindSliceProofToGrantAndIdentity(proof, grant, runtimeIdentity)) {
      return implementationProofInvalid("slice proof not bound to grant / execution identity", {
        slice_id: sliceId,
      });
    }
    if (row.proof_kind && row.proof_kind !== proof.proof_kind) {
      return implementationProofInvalid("hand-built proof_kind does not match on-disk evaluation", {
        slice_id: sliceId,
      });
    }
    authoritative.push(proof);
  }
  return { ok: true, slice_proofs: authoritative };
}

/**
 * Slice proof summary from evaluateDevelopmentCompleteWithExceptionGate ( #175 parity ).
 */
export function sliceProofSummaryFromExceptionGate(gateResult) {
  if (!gateResult?.ok || !Array.isArray(gateResult.slice_proofs)) return null;
  return gateResult.slice_proofs;
}

/**
 * Mark development complete under exception without calling pipeline selector.
 */
export function assertDevelopmentCompleteWithExceptionV2({
  grant,
  runtimeIdentity,
  sliceProofSummary = null,
  repoRoot = null,
  changeRoot = null,
  design = null,
  maps = null,
  upstreamBySlice = null,
  redBySlice = null,
  implementBySlice = null,
  greenBySlice = null,
  attestedCurrentGreenSliceIds = [],
} = {}) {
  const validated =
    grant?.grant_provenance === GRANT_PROVENANCE_OWNER_LOCAL
      ? validateOwnerLocalOverrideGrant(grant, { runtimeIdentity })
      : validateUniversalV2OwnerGrant(grant, { runtimeIdentity });
  if (!validated.ok) return validated;
  if (!transitionAllows(grant, STATE_DEVELOPMENT_COMPLETE_WITH_EXCEPTION)) {
    return { ok: false, code: STATE_DENIED, reason: "TRANSITION_NOT_IN_GRANT" };
  }
  const hasSummary = Array.isArray(sliceProofSummary) && sliceProofSummary.length > 0;
  const hasGateContext = Boolean(design && repoRoot && changeRoot);
  if (!hasSummary && !hasGateContext) {
    return {
      ok: false,
      code: STATE_DENIED,
      reason: "IMPLEMENTATION_PROOF_REQUIRED",
      hint: "Provide per-slice closeout proof (evaluateSliceDevelopmentProof / exception closeout)",
    };
  }
  const revalidated = revalidateAuthoritativeSliceProofs({
    grant,
    runtimeIdentity,
    repoRoot,
    changeRoot,
    design,
    maps,
    sliceProofSummary,
    upstreamBySlice,
    redBySlice,
    implementBySlice,
    greenBySlice,
    attestedCurrentGreenSliceIds,
  });
  if (!revalidated.ok) {
    if (revalidated.reason === "IMPLEMENTATION_PROOF_REQUIRED") return revalidated;
    return revalidated.reason
      ? revalidated
      : implementationProofInvalid("slice proof revalidation failed");
  }
  const authoritativeSummary = revalidated.slice_proofs;
  if (!authoritativeSummary?.length) {
    return implementationProofInvalid("no authoritative slice proofs after revalidation");
  }
  return {
    ok: true,
    code: STATE_DEVELOPMENT_COMPLETE_WITH_EXCEPTION,
    development_stage: STATE_DEVELOPMENT_COMPLETE_WITH_EXCEPTION,
    verification_debt_open: true,
    auto_archive_forbidden: true,
    slice_proof_summary: authoritativeSummary,
    fake_green_minted: false,
    fake_verify_pass: false,
  };
}

export { loadTrustedBreakGlassGrant };
