import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Multi-slice post-IMPLEMENT recovery cohort: frozen manifest, per-slice receipts,
 * unaffected freshness proofs, and whole-change IMPL_OK convergence.
 */
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import {
  classifyGreenEvidenceFreshness,
  classifyPostImplementGreenRecovery,
  collectImplementProductionPaths,
  computeLiveFrozenTestContentIdentity,
  computeLiveProductSourceInputIdentity,
  fileSha256,
} from "./opsx-apply-pipeline-identity.mjs";
import { runFrozenRedCommand } from "./opsx-apply-green.mjs";
import { evaluateReviewFreshnessAgainstDesign } from "./opsx-apply-design-review.mjs";
import {
  APPLY_DESIGN_WRITER_RECORD_SCHEMA,
  AUTHORIZED_APPLY_DESIGN_COMMAND,
  assertApplyDesignWriterRecordForRecovery,
} from "./opsx-apply-design-writer-transition.mjs";
import { applyDesignWriterRecordPath } from "./opsx-apply-design-writer-record.mjs";
import {
  RECOVERY_PATH_B,
  FRESH_EVIDENCE_PASS,
  advancePostImplementRecovery,
  anchorRecoveryEvidenceBindings,
  buildBoundedVerifySuccessorMechanism,
  computeRecoveryExecutionRevision,
  computeRecoveryGenerationLineage,
  executeRecoveryFreshEvidence,
  persistRecoveryFreshEvidence,
  persistRecoveryLedger,
  persistRecoverySuccessorPlan,
  publishRecoveryGeneration,
  recomputeRecoveryImplOk,
  recoveryAttemptDir,
  assertRecoveryVerificationGateAccepted,
  validatePostImplementRecoveryOwnerDecision,
} from "./opsx-post-implement-test-contract-recovery.mjs";

export const RECOVERY_ADOPTION_MANIFEST_SCHEMA = "opsx.post_implement_recovery_adoption_contracts";
export { APPLY_DESIGN_WRITER_RECORD_SCHEMA, AUTHORIZED_APPLY_DESIGN_COMMAND };
export { applyDesignWriterRecordPath } from "./opsx-apply-design-writer-record.mjs";
export const RECOVERY_DESIGN_SUPERSESSION_SCHEMA = "opsx.apply_design_recovery_supersession";

export const RECOVERY_COHORT_MANIFEST_SCHEMA = "opsx.post_implement_recovery_cohort_manifest";
export const COHORT_PHASE_SLICE_RECOVERY = "COHORT_SLICE_RECOVERY";
export const COHORT_PHASE_UNAFFECTED_FRESHNESS = "COHORT_UNAFFECTED_FRESHNESS";
export const COHORT_PHASE_COHORT_VERIFIED = "COHORT_VERIFIED";
export const COHORT_PHASE_IMPL_OK = "COHORT_IMPL_OK";
export const COHORT_PHASE_SUCCESSOR_PLAN = "COHORT_SUCCESSOR_PLAN";

export const PILOT_AFFECTED_SLICE_IDS = [
  "SLICE-003",
  "SLICE-004",
  "SLICE-005",
  "SLICE-006",
  "SLICE-008",
  "SLICE-009",
  "SLICE-010",
];

export const PILOT_UNAFFECTED_SLICE_IDS = ["SLICE-001", "SLICE-002", "SLICE-007"];

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

function sha256Hex(input) {
  return createHash("sha256").update(input, "utf8").digest("hex");
}

function sha256File(repoRoot, relPath) {
  const abs = resourcePath(repoRoot, relPath);
  if (!existsSync(abs)) return null;
  return fileSha256(abs);
}

/**
 * Путь к adoption manifest (planning-approved verify commands per slice).
 */
export function recoveryAdoptionManifestPath(repoRoot, changeRoot) {
  return join(resourcePath(repoRoot, changeRoot), "recovery-adoption", "RECOVERY-ADOPTION-MANIFEST.json");
}

export function recoveryDesignSupersessionPath(repoRoot, changeRoot) {
  return join(
    resourcePath(repoRoot, changeRoot),
    "apply-design",
    "APPLY-DESIGN-RECOVERY-SUPERSESSION.json",
  );
}

/**
 * Загрузить adoption manifest с диска (обязателен для cohort verify authority).
 */
export function loadRecoveryAdoptionManifest({ repoRoot, changeRoot } = {}) {
  if (!changeRoot) {
    return { ok: false, code: "RECOVERY_ADOPTION_MANIFEST_MISSING" };
  }
  const path = recoveryAdoptionManifestPath(repoRoot, changeRoot);
  if (!existsSync(path)) {
    return { ok: false, code: "RECOVERY_ADOPTION_MANIFEST_MISSING", path };
  }
  const manifest = JSON.parse(readFileSync(path, "utf8"));
  if (manifest.schema !== RECOVERY_ADOPTION_MANIFEST_SCHEMA) {
    return { ok: false, code: "RECOVERY_ADOPTION_MANIFEST_SCHEMA_MISMATCH" };
  }
  return { ok: true, manifest, path };
}

/**
 * Резолв source revision для cohort (fail-closed при unknown).
 */
export function resolveCohortSourceRevision({
  repoRoot,
  changeRoot,
  redBySlice = {},
  explicit = null,
} = {}) {
  if (explicit) {
    return { ok: true, source_revision: String(explicit) };
  }
  if (changeRoot) {
    const hermeticPath = join(resourcePath(repoRoot, changeRoot), "HERMETIC-MANIFEST.json");
    if (existsSync(hermeticPath)) {
      const hermetic = JSON.parse(readFileSync(hermeticPath, "utf8"));
      if (hermetic.source_commit) {
        return { ok: true, source_revision: String(hermetic.source_commit) };
      }
    }
  }
  const adoption = changeRoot ? loadRecoveryAdoptionManifest({ repoRoot, changeRoot }) : { ok: false };
  if (adoption.ok && adoption.manifest?.source_revision) {
    return { ok: true, source_revision: String(adoption.manifest.source_revision) };
  }
  for (const red of Object.values(redBySlice)) {
    if (red?.source_revision) {
      return { ok: true, source_revision: String(red.source_revision) };
    }
  }
  return { ok: false, code: "RECOVERY_COHORT_SOURCE_REVISION_MISSING" };
}

/**
 * Derive mandatory verify command для slice из adoption manifest (не self-approve CLI).
 */
export function deriveRecoveryCohortVerifyCommandsForSlice({
  repoRoot,
  changeRoot,
  sliceId,
  red,
  adoptionManifest = null,
} = {}) {
  let manifest = adoptionManifest;
  if (!manifest && changeRoot) {
    const loaded = loadRecoveryAdoptionManifest({ repoRoot, changeRoot });
    if (!loaded.ok) {
      return loaded;
    }
    manifest = loaded.manifest;
  }
  if (!manifest?.slices?.[sliceId]) {
    return { ok: false, code: "RECOVERY_ADOPTION_SLICE_CONTRACT_MISSING", slice_id: sliceId };
  }
  const entry = manifest.slices[sliceId];
  const obligationDigest = red?.behavior_obligation_identity?.obligation_digest || null;
  if (
    obligationDigest &&
    entry.obligation_digest &&
    entry.obligation_digest !== obligationDigest
  ) {
    return { ok: false, code: "RECOVERY_ADOPTION_OBLIGATION_MISMATCH", slice_id: sliceId };
  }
  const cmd =
    entry.mandatory_verify_command || (entry.mandatory_verify_commands || [])[0] || null;
  if (!cmd) {
    return { ok: false, code: "RECOVERY_MANDATORY_VERIFY_COMMAND_MISSING", slice_id: sliceId };
  }
  return {
    ok: true,
    slice_id: sliceId,
    verify_commands: [cmd],
    obligation_digest: entry.obligation_digest || obligationDigest,
  };
}

/**
 * Derive verify commands для всех affected slices.
 */
export function deriveRecoveryCohortVerifyCommandsBySlice({
  repoRoot,
  changeRoot,
  affectedSliceIds,
  redBySlice = {},
} = {}) {
  const adoption = loadRecoveryAdoptionManifest({ repoRoot, changeRoot });
  if (!adoption.ok) {
    return adoption;
  }
  const verifyCommandsBySlice = {};
  const obligationsBySlice = {};
  for (const sliceId of affectedSliceIds || []) {
    const derived = deriveRecoveryCohortVerifyCommandsForSlice({
      repoRoot,
      changeRoot,
      sliceId,
      red: redBySlice[sliceId],
      adoptionManifest: adoption.manifest,
    });
    if (!derived.ok) {
      return derived;
    }
    verifyCommandsBySlice[sliceId] = derived.verify_commands;
    obligationsBySlice[sliceId] = derived.obligation_digest;
  }
  return {
    ok: true,
    verify_commands_by_slice: verifyCommandsBySlice,
    obligations_by_slice: obligationsBySlice,
    adoption_manifest: adoption.manifest,
  };
}

/**
 * Caller-supplied verify commands must match adoption-derived authority.
 */
export function assertCallerVerifyCommandsAuthorizedByAdoption({
  repoRoot,
  changeRoot,
  verifyCommandsBySlice = {},
  affectedSliceIds = [],
  redBySlice = {},
} = {}) {
  const derived = deriveRecoveryCohortVerifyCommandsBySlice({
    repoRoot,
    changeRoot,
    affectedSliceIds,
    redBySlice,
  });
  if (!derived.ok) {
    return derived;
  }
  for (const sliceId of affectedSliceIds) {
    const allowed = derived.verify_commands_by_slice[sliceId] || [];
    const caller = verifyCommandsBySlice[sliceId] || [];
    if (caller.length === 0) {
      continue;
    }
    for (const cmd of caller) {
      if (!allowed.includes(cmd)) {
        return {
          ok: false,
          code: "RECOVERY_VERIFY_COMMAND_UNAUTHORIZED",
          slice_id: sliceId,
        };
      }
    }
  }
  return { ok: true, derived };
}

/**
 * Disk proof: APPLY-DESIGN supersession via authorized /opsx-apply-design writer (not marker alone).
 */
export function assertRecoveryDesignSupersessionAuthorized({
  repoRoot,
  changeRoot,
  cohortManifestId,
  designApplyDesignSha256,
} = {}) {
  const writerPath = applyDesignWriterRecordPath(repoRoot, changeRoot);
  if (!existsSync(writerPath)) {
    return { ok: false, code: "RECOVERY_DESIGN_SUPERSESSION_UNPROVEN", detail: "writer_record_missing" };
  }
  const writer = JSON.parse(readFileSync(writerPath, "utf8"));
  if (
    writer.schema !== APPLY_DESIGN_WRITER_RECORD_SCHEMA ||
    writer.authorized_command !== AUTHORIZED_APPLY_DESIGN_COMMAND
  ) {
    return { ok: false, code: "RECOVERY_DESIGN_SUPERSESSION_UNPROVEN", detail: "writer_not_authorized" };
  }
  const writerProof = assertApplyDesignWriterRecordForRecovery({
    writer,
    cohortManifestId,
    designApplyDesignSha256,
  });
  if (!writerProof.ok) {
    return writerProof;
  }
  const superPath = recoveryDesignSupersessionPath(repoRoot, changeRoot);
  if (!existsSync(superPath)) {
    return { ok: false, code: "RECOVERY_DESIGN_SUPERSESSION_UNPROVEN", detail: "supersession_record_missing" };
  }
  const supersession = JSON.parse(readFileSync(superPath, "utf8"));
  if (
    supersession.schema !== RECOVERY_DESIGN_SUPERSESSION_SCHEMA ||
    supersession.authorized_writer_command !== AUTHORIZED_APPLY_DESIGN_COMMAND ||
    supersession.bound_cohort_manifest_id !== cohortManifestId
  ) {
    return { ok: false, code: "RECOVERY_DESIGN_SUPERSESSION_UNPROVEN", detail: "supersession_binding_invalid" };
  }
  return { ok: true, supersession };
}

/**
 * Persist supersession record after writer proof (idempotent per manifest digest).
 */
export function persistRecoveryDesignSupersession({
  repoRoot,
  changeRoot,
  cohortManifestId,
  designApplyDesignSha256,
  generationLineageId,
} = {}) {
  const writerPath = applyDesignWriterRecordPath(repoRoot, changeRoot);
  if (!existsSync(writerPath)) {
    return { ok: false, code: "RECOVERY_DESIGN_SUPERSESSION_UNPROVEN" };
  }
  const writer = JSON.parse(readFileSync(writerPath, "utf8"));
  if (writer.authorized_command !== AUTHORIZED_APPLY_DESIGN_COMMAND) {
    return { ok: false, code: "RECOVERY_DESIGN_SUPERSESSION_UNPROVEN" };
  }
  const writerProof = assertApplyDesignWriterRecordForRecovery({
    writer,
    cohortManifestId,
    designApplyDesignSha256,
  });
  if (!writerProof.ok) {
    return writerProof;
  }
  const path = recoveryDesignSupersessionPath(repoRoot, changeRoot);
  const body = {
    schema: RECOVERY_DESIGN_SUPERSESSION_SCHEMA,
    schema_version: 1,
    authorized_writer_command: AUTHORIZED_APPLY_DESIGN_COMMAND,
    bound_cohort_manifest_id: cohortManifestId,
    apply_design_sha256: designApplyDesignSha256 || writer.apply_design_sha256 || null,
    generation_lineage_id: generationLineageId || null,
    writer_record_sha256: sha256File(
      repoRoot,
      join(changeRoot, "apply-design/APPLY-DESIGN-WRITER-RECORD.json"),
    ),
    recorded_at: new Date().toISOString(),
  };
  if (existsSync(path)) {
    const existing = JSON.parse(readFileSync(path, "utf8"));
    if (existing.bound_cohort_manifest_id !== cohortManifestId) {
      return { ok: false, code: "RECOVERY_DESIGN_SUPERSESSION_COLLISION" };
    }
    return { ok: true, idempotent: true, path, supersession: existing };
  }
  atomicWriteManifest(path, body);
  return { ok: true, path, supersession: body };
}

/**
 * Validate owner decisions for all affected slices before recovery side effects.
 */
export function preflightRecoveryCohortOwnerBindings({
  design,
  manifest,
  affectedSliceIds = [],
  ownerDecisionsBySlice = {},
  ownerDecisionSecret = null,
  redBySlice = {},
  implementBySlice = {},
  repoRoot,
} = {}) {
  if (!ownerDecisionSecret) {
    return { ok: false, code: "RECOVERY_OWNER_DECISION_SECRET_MISSING" };
  }
  for (const sliceId of affectedSliceIds) {
    const ownerDecision = ownerDecisionsBySlice[sliceId] || null;
    if (!ownerDecision) {
      return { ok: false, code: "RECOVERY_OWNER_DECISION_MISSING", slice_id: sliceId };
    }
    const red = redBySlice[sliceId];
    const implement = implementBySlice[sliceId];
    const recovery = classifyPostImplementGreenRecovery({ red, implement, repoRoot });
    const { execution_revision: executionRevision } = computeRecoveryExecutionRevision({
      red,
      implement,
      repoRoot,
    });
    const lineage = computeRecoveryGenerationLineage({
      changeId: design.change_id,
      sliceId,
      recoveryCode: recovery.code,
      ownerDecisionId: ownerDecision.decision_id,
      executionRevision,
      recoveryManifestDigest: manifest.manifest_id,
      designSupersessionId: `DESIGN_SUPERSEDED:${manifest.manifest_id}`,
      inputIdentities: {
        red_test_content_identity: red?.test_content_identity || null,
        implement_frozen_test_content_identity: implement?.frozen_test_content_identity || null,
      },
    });
    const validated = validatePostImplementRecoveryOwnerDecision(ownerDecision, {
      secret: ownerDecisionSecret,
      changeId: design.change_id,
      sliceId,
      recoveryCode: recovery.code,
      generationLineageId: lineage.generation_lineage_id,
    });
    if (!validated.ok) {
      return { ok: false, code: validated.code, slice_id: sliceId };
    }
  }
  return { ok: true };
}

/**
 * Ordered admission gates: adoption (caller) → design-review → owner binding →
 * writer record → supersession. No slice recovery effects before this passes.
 */
export function preflightRecoveryCohortAdmissionGates({
  repoRoot,
  changeRoot,
  design,
  manifest,
  affectedSliceIds = [],
  ownerDecisionsBySlice = {},
  ownerDecisionSecret = null,
  redBySlice = {},
  implementBySlice = {},
} = {}) {
  const adoption = loadRecoveryAdoptionManifest({ repoRoot, changeRoot });
  if (!adoption.ok) {
    return adoption;
  }
  const designRel =
    design?.apply_design_identity?.machine_path ||
    join(changeRoot, "apply-design/APPLY-DESIGN.json");
  const reviewRel = join(changeRoot, "apply-design-review/APPLY-DESIGN-REVIEW.json");
  const designAbs = resourcePath(repoRoot, designRel);
  const reviewAbs = resourcePath(repoRoot, reviewRel);
  if (!existsSync(designAbs) || !existsSync(reviewAbs)) {
    return {
      ok: false,
      code: "RECOVERY_COHORT_DESIGN_REVIEW_MISSING",
      detail: !existsSync(reviewAbs) ? "apply_design_review_missing" : "apply_design_missing",
    };
  }
  const review = JSON.parse(readFileSync(reviewAbs, "utf8"));
  const designSha = sha256File(repoRoot, designRel);
  const reviewGate = evaluateReviewFreshnessAgainstDesign({
    design,
    review,
    designArtifactSha256: designSha,
  });
  if (!reviewGate.ok) {
    return {
      ok: false,
      code: reviewGate.code || "RECOVERY_COHORT_DESIGN_REVIEW_STALE",
      pause: true,
    };
  }
  const ownerPreflight = preflightRecoveryCohortOwnerBindings({
    design,
    manifest,
    affectedSliceIds,
    ownerDecisionsBySlice,
    ownerDecisionSecret,
    redBySlice,
    implementBySlice,
    repoRoot,
  });
  if (!ownerPreflight.ok) {
    return { ...ownerPreflight, pause: true };
  }
  const writerPath = applyDesignWriterRecordPath(repoRoot, changeRoot);
  if (!existsSync(writerPath)) {
    return {
      ok: false,
      code: "RECOVERY_DESIGN_SUPERSESSION_UNPROVEN",
      detail: "writer_record_missing",
      pause: true,
    };
  }
  const writer = JSON.parse(readFileSync(writerPath, "utf8"));
  const writerProof = assertApplyDesignWriterRecordForRecovery({
    writer,
    cohortManifestId: manifest.manifest_id,
    designApplyDesignSha256: manifest.design_apply_design_sha256 || designSha,
  });
  if (!writerProof.ok) {
    return { ...writerProof, pause: true };
  }
  const supersessionPersist = persistRecoveryDesignSupersession({
    repoRoot,
    changeRoot,
    cohortManifestId: manifest.manifest_id,
    designApplyDesignSha256: manifest.design_apply_design_sha256 || designSha,
  });
  if (!supersessionPersist.ok) {
    return { ...supersessionPersist, pause: supersessionPersist.code === "RECOVERY_DESIGN_SUPERSESSION_UNPROVEN" };
  }
  const designProof = assertRecoveryDesignSupersessionAuthorized({
    repoRoot,
    changeRoot,
    cohortManifestId: manifest.manifest_id,
    designApplyDesignSha256: manifest.design_apply_design_sha256 || designSha,
  });
  if (!designProof.ok) {
    return { ...designProof, pause: true };
  }
  return {
    ok: true,
    adoption_manifest: adoption.manifest,
    supersession: designProof.supersession,
    design_review_status: reviewGate.status,
  };
}

/**
 * Post-publish drift: source/test bytes vs frozen cohort manifest.
 */
export function validateCohortManifestLiveBytes({
  manifest,
  redBySlice = {},
  implementBySlice = {},
  repoRoot,
} = {}) {
  if (!manifest?.source_revision) {
    return { ok: false, code: "RECOVERY_COHORT_SOURCE_REVISION_MISSING" };
  }
  for (const [sliceId, entry] of Object.entries(manifest.slices || {})) {
    const red = redBySlice[sliceId];
    const implement = implementBySlice[sliceId];
    if (!red) {
      continue;
    }
    const live = computeLiveFrozenTestContentIdentity(repoRoot, {
      test_paths: red.test_paths,
      fixture_paths: red.fixture_paths,
    });
    if (entry.test_content_identity && live.ok && live.identity !== entry.test_content_identity) {
      return { ok: false, code: "RECOVERY_COHORT_TEST_BYTE_DRIFT", slice_id: sliceId };
    }
    const freeze =
      implement?.frozen_test_content_identity || implement?.frozen_test_identity_after || null;
    if (entry.frozen_test_content_identity && freeze && entry.frozen_test_content_identity !== freeze) {
      return { ok: false, code: "RECOVERY_COHORT_IMPLEMENT_BYTE_DRIFT", slice_id: sliceId };
    }
    const productionPaths = collectImplementProductionPaths(implement);
    const liveProduct = computeLiveProductSourceInputIdentity(repoRoot, {
      production_paths: productionPaths,
    });
    if (
      entry.product_source_input_identity &&
      liveProduct.ok &&
      liveProduct.identity !== entry.product_source_input_identity
    ) {
      return {
        ok: false,
        code: "RECOVERY_COHORT_PRODUCT_SOURCE_BYTE_DRIFT",
        slice_id: sliceId,
      };
    }
    if (red.source_revision && String(red.source_revision) !== String(manifest.source_revision)) {
      return { ok: false, code: "RECOVERY_SOURCE_REVISION_DRIFT", slice_id: sliceId };
    }
  }
  return { ok: true };
}

/**
 * Independent per-slice receipt validation from persisted FRESH-EVIDENCE.json.
 */
export function validateCohortAffectedSliceReceiptFromDisk({
  repoRoot,
  changeRoot,
  recoveryAttemptId,
  sliceId,
  manifest,
  redBySlice = {},
  implementBySlice = {},
  expectedReceipt = null,
} = {}) {
  const freshPath = recoveryCohortSliceFreshEvidencePath(
    repoRoot,
    changeRoot,
    recoveryAttemptId,
    sliceId,
  );
  if (!existsSync(freshPath)) {
    return { ok: false, code: "RECOVERY_COHORT_SLICE_EVIDENCE_MISSING", slice_id: sliceId };
  }
  const fresh = JSON.parse(readFileSync(freshPath, "utf8"));
  if (fresh.status !== FRESH_EVIDENCE_PASS) {
    return { ok: false, code: "RECOVERY_COHORT_SLICE_EVIDENCE_NOT_PASS", slice_id: sliceId };
  }
  const gate = assertRecoveryVerificationGateAccepted(fresh);
  if (!gate.ok) {
    return { ...gate, slice_id: sliceId };
  }
  const sliceManifest = manifest?.slices?.[sliceId];
  if (!sliceManifest) {
    return { ok: false, code: "RECOVERY_COHORT_SLICE_UNKNOWN", slice_id: sliceId };
  }
  const allowed = sliceManifest.verify_commands || [];
  const cmdExec = (fresh.executions || []).find((e) => e.kind === "command_execution");
  if (!cmdExec || !allowed.includes(cmdExec.command)) {
    return { ok: false, code: "RECOVERY_VERIFY_COMMAND_MANIFEST_DRIFT", slice_id: sliceId };
  }
  const red = redBySlice[sliceId];
  const implement = implementBySlice[sliceId];
  const { execution_revision: liveRev } = computeRecoveryExecutionRevision({
    red,
    implement,
    repoRoot,
  });
  if (
    expectedReceipt?.execution_revision &&
    expectedReceipt.execution_revision !== fresh.execution_revision
  ) {
    return { ok: false, code: "RECOVERY_COHORT_RECEIPT_TAMPERED", slice_id: sliceId };
  }
  if (liveRev && fresh.execution_revision && liveRev !== fresh.execution_revision) {
    return { ok: false, code: "RECOVERY_COHORT_RECEIPT_EXECUTION_DRIFT", slice_id: sliceId };
  }
  if (
    fresh.bound_source_revision &&
    manifest.source_revision &&
    String(fresh.bound_source_revision) !== String(manifest.source_revision)
  ) {
    return { ok: false, code: "RECOVERY_SOURCE_REVISION_DRIFT", slice_id: sliceId };
  }
  const obligation = red?.behavior_obligation_identity?.obligation_digest || null;
  if (sliceManifest.obligation_digest && obligation && sliceManifest.obligation_digest !== obligation) {
    return { ok: false, code: "RECOVERY_ADOPTION_OBLIGATION_MISMATCH", slice_id: sliceId };
  }
  if (
    expectedReceipt?.generation_lineage_id &&
    expectedReceipt.generation_lineage_id !== fresh.generation_lineage_id
  ) {
    return { ok: false, code: "RECOVERY_COHORT_RECEIPT_TAMPERED", slice_id: sliceId };
  }
  return { ok: true, fresh_evidence: fresh, slice_id: sliceId };
}

/**
 * Re-read all cohort receipts + unaffected proofs before whole-change IMPL_OK.
 */
/**
 * Cohort recovery context for completion consumer (ledger + frozen manifest on disk).
 */
export function buildCohortRecoveryContextFromLedger({
  repoRoot,
  changeRoot,
  recoveryAttemptId,
  ledger,
} = {}) {
  if (ledger?.schema !== "opsx.post_implement_recovery_cohort_ledger") {
    return null;
  }
  const manifestPath = recoveryCohortManifestPath(repoRoot, changeRoot, recoveryAttemptId);
  if (!existsSync(manifestPath)) {
    return null;
  }
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  return {
    manifest_digest: manifest.manifest_id || ledger.cohort_manifest_id,
    affected_slice_ids: manifest.affected_slice_ids,
    unaffected_slice_ids: manifest.unaffected_slice_ids,
    slice_receipts: ledger.slice_receipts || {},
    unaffected_freshness: ledger.unaffected_freshness || {},
  };
}

/**
 * Fail-closed disk revalidation before completion consumer grants IMPL_OK.
 */
export function revalidatePersistedCohortRecoveryAttempt({
  repoRoot,
  changeRoot,
  recoveryAttemptId,
  ledger,
  redBySlice = {},
  implementBySlice = {},
  greenBySlice = {},
} = {}) {
  if (ledger?.schema !== "opsx.post_implement_recovery_cohort_ledger") {
    return { ok: true, skipped: true };
  }
  const manifestPath = recoveryCohortManifestPath(repoRoot, changeRoot, recoveryAttemptId);
  if (!existsSync(manifestPath)) {
    return { ok: false, code: "RECOVERY_COHORT_MANIFEST_MISSING" };
  }
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  return revalidateCohortDiskEvidenceBeforeImplOk({
    repoRoot,
    changeRoot,
    recoveryAttemptId,
    manifest,
    sliceReceipts: ledger.slice_receipts || {},
    unaffectedFreshness: ledger.unaffected_freshness || {},
    redBySlice,
    implementBySlice,
    greenBySlice,
  });
}

export function revalidateCohortDiskEvidenceBeforeImplOk({
  repoRoot,
  changeRoot,
  recoveryAttemptId,
  manifest,
  sliceReceipts = {},
  unaffectedFreshness = {},
  redBySlice = {},
  implementBySlice = {},
  greenBySlice = {},
} = {}) {
  const live = validateCohortManifestLiveBytes({
    manifest,
    redBySlice,
    implementBySlice,
    repoRoot,
  });
  if (!live.ok) {
    return live;
  }
  for (const sliceId of manifest?.affected_slice_ids || []) {
    const check = validateCohortAffectedSliceReceiptFromDisk({
      repoRoot,
      changeRoot,
      recoveryAttemptId,
      sliceId,
      manifest,
      redBySlice,
      implementBySlice,
      expectedReceipt: sliceReceipts[sliceId],
    });
    if (!check.ok) {
      return check;
    }
  }
  for (const sliceId of manifest?.unaffected_slice_ids || []) {
    if (!unaffectedFreshness[sliceId]?.ok) {
      return {
        ok: false,
        code: "IMPL_GAPS_REMAIN",
        gaps: { blocking: [{ class: "STALE_EVIDENCE", slice_id: sliceId }] },
      };
    }
    const reproved = proveUnaffectedSliceFreshness({
      sliceId,
      red: redBySlice[sliceId],
      implement: implementBySlice[sliceId],
      green: greenBySlice[sliceId],
      repoRoot,
    });
    if (!reproved.ok) {
      return {
        ok: false,
        code: "IMPL_GAPS_REMAIN",
        gaps: { blocking: [{ class: "STALE_EVIDENCE", slice_id: sliceId }] },
      };
    }
  }
  return { ok: true };
}

/**
 * Путь к замороженному cohort manifest на диске (до публикации generation).
 */
export function recoveryCohortManifestPath(repoRoot, changeRoot, recoveryAttemptId) {
  return join(recoveryAttemptDir(repoRoot, changeRoot, recoveryAttemptId), "COHORT-MANIFEST.json");
}

/**
 * Путь к per-slice fresh evidence внутри cohort attempt.
 */
export function recoveryCohortSliceFreshEvidencePath(
  repoRoot,
  changeRoot,
  recoveryAttemptId,
  sliceId,
) {
  return join(
    recoveryAttemptDir(repoRoot, changeRoot, recoveryAttemptId),
    "slices",
    sliceId,
    "FRESH-EVIDENCE.json",
  );
}

/**
 * Собрать тело cohort manifest (без manifest_id) из design/maps.
 */
export function buildRecoveryCohortManifestBody({
  changeId,
  changeRoot = null,
  sourceRevision,
  design,
  redBySlice = {},
  implementBySlice = {},
  repoRoot,
  affectedSliceIds = PILOT_AFFECTED_SLICE_IDS,
  unaffectedSliceIds = PILOT_UNAFFECTED_SLICE_IDS,
  verifyCommandsBySlice = {},
  executorIdentity = "opsx-post-implement-recovery-cohort",
} = {}) {
  const resolvedSource = sourceRevision
    ? { ok: true, source_revision: String(sourceRevision) }
    : resolveCohortSourceRevision({ repoRoot, changeRoot, redBySlice });
  if (!resolvedSource.ok) {
    return { ok: false, code: resolvedSource.code };
  }
  let effectiveVerifyBySlice = { ...verifyCommandsBySlice };
  if (changeRoot) {
    const derived = deriveRecoveryCohortVerifyCommandsBySlice({
      repoRoot,
      changeRoot,
      affectedSliceIds,
      redBySlice,
    });
    if (!derived.ok) {
      return { ok: false, code: derived.code };
    }
    for (const sliceId of affectedSliceIds) {
      effectiveVerifyBySlice[sliceId] = derived.verify_commands_by_slice[sliceId];
    }
    const auth = assertCallerVerifyCommandsAuthorizedByAdoption({
      repoRoot,
      changeRoot,
      verifyCommandsBySlice,
      affectedSliceIds,
      redBySlice,
    });
    if (!auth.ok) {
      return { ok: false, code: auth.code };
    }
  }
  const sliceEntries = {};
  for (const sliceId of [...affectedSliceIds, ...unaffectedSliceIds]) {
    const red = redBySlice[sliceId];
    const implement = implementBySlice[sliceId];
    const live = red
      ? computeLiveFrozenTestContentIdentity(repoRoot, {
          test_paths: red.test_paths,
          fixture_paths: red.fixture_paths,
        })
      : { ok: false, identity: null };
    const productionPaths = implement ? collectImplementProductionPaths(implement) : [];
    const liveProduct =
      productionPaths.length > 0
        ? computeLiveProductSourceInputIdentity(repoRoot, { production_paths: productionPaths })
        : { ok: false, identity: null };
    const { execution_revision: executionRevision } =
      red && implement
        ? computeRecoveryExecutionRevision({ red, implement, repoRoot })
        : { execution_revision: null };
    const recovery = red && implement
      ? classifyPostImplementGreenRecovery({ red, implement, repoRoot })
      : null;
    sliceEntries[sliceId] = {
      cohort_role: affectedSliceIds.includes(sliceId) ? "AFFECTED" : "UNAFFECTED",
      recovery_code: recovery?.code || null,
      apply_design_identity: red?.apply_design_identity || null,
      test_paths: [...(red?.test_paths || [])].sort(),
      fixture_paths: [...(red?.fixture_paths || [])].sort(),
      test_content_identity: live.ok ? live.identity : null,
      frozen_test_content_identity:
        implement?.frozen_test_content_identity ||
        implement?.frozen_test_identity_after ||
        null,
      product_source_input_identity: liveProduct.ok ? liveProduct.identity : null,
      production_paths: productionPaths,
      execution_revision: executionRevision,
      verify_commands: [...(effectiveVerifyBySlice[sliceId] || [])],
      obligation_digest: red?.behavior_obligation_identity?.obligation_digest || null,
      acceptance_predicate:
        affectedSliceIds.includes(sliceId)
          ? "GENERATION_BOUND_FRESH_EVIDENCE_PASS"
          : "GREEN_EVIDENCE_FRESH",
    };
  }
  return {
    schema: RECOVERY_COHORT_MANIFEST_SCHEMA,
    schema_version: 1,
    change_id: changeId || design?.change_id,
    source_revision: resolvedSource.source_revision,
    design_apply_design_sha256: sha256File(
      repoRoot,
      design?.apply_design_identity?.machine_path ||
        `openspec/changes/${changeId || design?.change_id}/apply-design/APPLY-DESIGN.json`,
    ),
    slice_ids: design?.slice_order?.nodes || [
      ...unaffectedSliceIds,
      ...affectedSliceIds,
    ],
    affected_slice_ids: [...affectedSliceIds],
    unaffected_slice_ids: [...unaffectedSliceIds],
    slices: sliceEntries,
    executor_identity: executorIdentity,
    frozen_at: null,
  };
}

/**
 * Вычислить digest manifest (каноническое тело без manifest_id/frozen_at).
 */
export function computeRecoveryCohortManifestDigest(manifestBody) {
  const { manifest_id, frozen_at, ...core } = manifestBody || {};
  return sha256Hex(stableStringify(core));
}

function atomicWriteManifest(path, manifest) {
  mkdirSync(dirname(path), { recursive: true });
  const tmp = `${path}.${process.pid}.tmp`;
  writeFileSync(tmp, `${JSON.stringify(manifest, null, 2)}\n`);
  renameSync(tmp, path);
}

/**
 * Заморозить manifest на диск (идемпотентно при полном совпадении digest).
 */
export function freezeRecoveryCohortManifest({
  repoRoot,
  changeRoot,
  recoveryAttemptId,
  manifestBody,
} = {}) {
  if (!changeRoot || !recoveryAttemptId || !manifestBody) {
    return { ok: false, code: "RECOVERY_COHORT_MANIFEST_INCOMPLETE" };
  }
  const digest = computeRecoveryCohortManifestDigest(manifestBody);
  const manifest = {
    ...manifestBody,
    manifest_id: digest,
    frozen_at: new Date().toISOString(),
  };
  const path = recoveryCohortManifestPath(repoRoot, changeRoot, recoveryAttemptId);
  if (existsSync(path)) {
    const existing = JSON.parse(readFileSync(path, "utf8"));
    if (existing.manifest_id !== digest) {
      return { ok: false, code: "RECOVERY_COHORT_MANIFEST_DIGEST_COLLISION" };
    }
    return { ok: true, idempotent: true, manifest: existing, path, manifest_digest: digest };
  }
  atomicWriteManifest(path, manifest);
  return { ok: true, manifest, path, manifest_digest: digest };
}

/**
 * Проверить, что исполняемая verify-команда совпадает с замороженным manifest.
 */
export function assertVerifyCommandMatchesCohortManifest({
  manifest,
  sliceId,
  verifyCommand,
} = {}) {
  if (!verifyCommand) {
    return { ok: true, skipped: true };
  }
  if (!manifest?.slices?.[sliceId]) {
    return { ok: false, code: "RECOVERY_COHORT_SLICE_UNKNOWN" };
  }
  const allowed = manifest.slices[sliceId].verify_commands || [];
  if (!allowed.includes(verifyCommand)) {
    return { ok: false, code: "RECOVERY_VERIFY_COMMAND_MANIFEST_DRIFT" };
  }
  return { ok: true };
}

/**
 * Доказательство свежести unaffected slice (без recovery generation).
 */
export function proveUnaffectedSliceFreshness({
  sliceId,
  red,
  implement,
  green,
  repoRoot,
  verifyCommand = null,
  runCommand = null,
} = {}) {
  const live = computeLiveFrozenTestContentIdentity(repoRoot, {
    test_paths: red?.test_paths,
    fixture_paths: red?.fixture_paths,
  });
  const freeze =
    implement?.frozen_test_content_identity ||
    implement?.frozen_test_identity_after ||
    null;
  if (live.ok && freeze && live.identity === freeze) {
    if (verifyCommand && runCommand) {
      const run = runCommand(verifyCommand, { cwd: repoRoot, timeoutMs: 120000 });
      const exitCode = run.exit_code ?? (run.ok === false ? 1 : 0);
      if (exitCode !== 0) {
        return { ok: false, code: "RECOVERY_UNAFFECTED_VERIFY_FAILED", slice_id: sliceId };
      }
    }
    return {
      ok: true,
      slice_id: sliceId,
      proof: {
        kind: "unaffected_live_frozen_identity",
        status: "FRESH",
        test_content_identity: live.identity,
      },
    };
  }
  const freshness = classifyGreenEvidenceFreshness(green, { red, implement, repoRoot });
  if (!freshness.current) {
    return { ok: false, code: "RECOVERY_UNAFFECTED_STALE", slice_id: sliceId, freshness };
  }
  return {
    ok: true,
    slice_id: sliceId,
    proof: {
      kind: "unaffected_green_freshness",
      status: "FRESH",
      test_content_identity: freshness.test_content_identity || null,
    },
  };
}

function persistCohortSliceFreshEvidence({
  repoRoot,
  changeRoot,
  recoveryAttemptId,
  sliceId,
  freshEvidence,
}) {
  const path = recoveryCohortSliceFreshEvidencePath(
    repoRoot,
    changeRoot,
    recoveryAttemptId,
    sliceId,
  );
  atomicWriteManifest(path, {
    schema: "opsx.post_implement_recovery_cohort_slice_fresh_evidence",
    schema_version: 1,
    slice_id: sliceId,
    recovery_attempt_id: recoveryAttemptId,
    ...freshEvidence,
    recorded_at: new Date().toISOString(),
  });
  return path;
}

/**
 * Восстановить один affected slice внутри cohort (PATH_A/B через advancePostImplementRecovery).
 */
export function recoverCohortAffectedSlice({
  design,
  scaffoldBySlice,
  redBySlice,
  implementBySlice,
  greenBySlice,
  upstreamBySlice,
  repoRoot,
  changeRoot,
  recoveryAttemptId,
  sliceId,
  manifest,
  ownerDecision = null,
  ownerDecisionSecret = null,
  verifyCommand = null,
  runCommand,
} = {}) {
  const manifestCheck = assertVerifyCommandMatchesCohortManifest({
    manifest,
    sliceId,
    verifyCommand,
  });
  if (!manifestCheck.ok) {
    return manifestCheck;
  }
  const verificationCommandsOverride = verifyCommand ? [verifyCommand] : null;
  const advanced = advancePostImplementRecovery({
    design: {
      ...design,
      slice_order: { ...design.slice_order, nodes: [sliceId] },
      slices: (design.slices || []).filter((s) => s.slice_id === sliceId),
    },
    scaffoldBySlice,
    redBySlice,
    implementBySlice,
    greenBySlice,
    upstreamBySlice,
    repoRoot,
    changeRoot,
    recoveryAttemptId: `${recoveryAttemptId}::${sliceId}`,
    ownerDecision,
    ownerDecisionSecret,
    verificationCommandsOverride,
    runCommand,
    planFingerprint: manifest?.manifest_id || null,
    recoveryManifestDigest: manifest?.manifest_id || null,
    designSupersessionId: manifest?.manifest_id
      ? `DESIGN_SUPERSEDED:${manifest.manifest_id}`
      : null,
    publishSuccessor: false,
  });
  if (!advanced.ok && advanced.code !== "RECOVERY_AUTHORITY_PENDING") {
    return { ok: false, code: advanced.code, slice_id: sliceId, detail: advanced };
  }
  if (advanced.code === "RECOVERY_AUTHORITY_PENDING") {
    return {
      ok: false,
      code: "RECOVERY_AUTHORITY_PENDING",
      slice_id: sliceId,
      owner_decision_packet: advanced.owner_decision_packet,
    };
  }
  const freshPath = persistCohortSliceFreshEvidence({
    repoRoot,
    changeRoot,
    recoveryAttemptId,
    sliceId,
    freshEvidence: advanced.fresh_evidence || advanced.ledger?.fresh_evidence_record,
  });
  return {
    ok: true,
    slice_id: sliceId,
    receipt: {
      generation_lineage_id: advanced.ledger?.generation_lineage_id,
      execution_revision: advanced.ledger?.execution_revision,
      fresh_evidence_path: freshPath,
      fresh_evidence_status: FRESH_EVIDENCE_PASS,
      recovery_path: advanced.ledger?.recovery_path,
    },
  };
}

/**
 * Пересчитать whole-change IMPL_OK для cohort (per-slice gaps, без ложного clearing).
 */
export function recomputeCohortRecoveryImplOk({
  design,
  greenBySlice,
  upstreamBySlice,
  redBySlice,
  implementBySlice,
  manifest,
  sliceReceipts = {},
  unaffectedFreshness = {},
  recoveryLedger,
  generation,
  repoRoot,
  changeRoot,
  recoveryAttemptId = null,
  freshEvidence,
} = {}) {
  for (const sliceId of manifest?.unaffected_slice_ids || []) {
    if (!unaffectedFreshness[sliceId]?.ok) {
      return {
        ok: false,
        code: "IMPL_GAPS_REMAIN",
        gaps: { blocking: [{ class: "STALE_EVIDENCE", slice_id: sliceId }] },
      };
    }
  }
  for (const sliceId of manifest?.affected_slice_ids || []) {
    const receipt = sliceReceipts[sliceId];
    if (!receipt || receipt.fresh_evidence_status !== FRESH_EVIDENCE_PASS) {
      return {
        ok: false,
        code: "IMPL_GAPS_REMAIN",
        gaps: {
          blocking: [{ class: "STALE_EVIDENCE", slice_id: sliceId, cohort: true }],
        },
      };
    }
  }
  if (changeRoot && recoveryAttemptId && manifest) {
    const disk = revalidateCohortDiskEvidenceBeforeImplOk({
      repoRoot,
      changeRoot,
      recoveryAttemptId,
      manifest,
      sliceReceipts,
      unaffectedFreshness,
      redBySlice,
      implementBySlice,
      greenBySlice,
    });
    if (!disk.ok) {
      return disk;
    }
  }
  const cohortRecovery = {
    manifest_digest: manifest?.manifest_id,
    affected_slice_ids: manifest?.affected_slice_ids,
    unaffected_slice_ids: manifest?.unaffected_slice_ids,
    slice_receipts: sliceReceipts,
    unaffected_freshness: unaffectedFreshness,
  };
  return recomputeRecoveryImplOk({
    design,
    greenBySlice,
    upstreamBySlice,
    redBySlice,
    implementBySlice,
    freshEvidence,
    recoveryLedger,
    generation,
    repoRoot,
    changeRoot,
    recoveredSliceId: null,
    cohortRecovery,
  });
}

/**
 * Главный cohort advance: manifest → unaffected proofs → 7 affected receipts → IMPL_OK → successor draft.
 */
export function advancePostImplementRecoveryCohort({
  design,
  scaffoldBySlice = {},
  redBySlice = {},
  implementBySlice = {},
  greenBySlice = {},
  upstreamBySlice = {},
  repoRoot,
  changeRoot,
  recoveryAttemptId,
  sourceRevision = null,
  manifestBody = null,
  frozenManifestBody = null,
  ownerDecisionsBySlice = {},
  ownerDecisionSecret = null,
  verifyCommandsBySlice = {},
  runCommand = runFrozenRedCommand,
} = {}) {
  if (!changeRoot || !recoveryAttemptId) {
    return { ok: false, code: "RECOVERY_COHORT_PRECONDITIONS_MISSING" };
  }
  const sourceResolved = resolveCohortSourceRevision({
    repoRoot,
    changeRoot,
    redBySlice,
    explicit: sourceRevision,
  });
  if (!sourceResolved.ok) {
    return sourceResolved;
  }
  let mergedVerifyBySlice = { ...verifyCommandsBySlice };
  const derivedVerify = deriveRecoveryCohortVerifyCommandsBySlice({
    repoRoot,
    changeRoot,
    affectedSliceIds: PILOT_AFFECTED_SLICE_IDS.filter((id) =>
      (design?.slice_order?.nodes || []).includes(id),
    ),
    redBySlice,
  });
  if (!derivedVerify.ok) {
    return derivedVerify;
  }
  const affectedIds =
    derivedVerify.verify_commands_by_slice
      ? Object.keys(derivedVerify.verify_commands_by_slice)
      : PILOT_AFFECTED_SLICE_IDS;
  const authVerify = assertCallerVerifyCommandsAuthorizedByAdoption({
    repoRoot,
    changeRoot,
    verifyCommandsBySlice: mergedVerifyBySlice,
    affectedSliceIds: affectedIds,
    redBySlice,
  });
  if (!authVerify.ok) {
    return authVerify;
  }
  for (const sliceId of affectedIds) {
    mergedVerifyBySlice[sliceId] = derivedVerify.verify_commands_by_slice[sliceId];
  }
  const bodyBuilt =
    frozenManifestBody ||
    manifestBody ||
    buildRecoveryCohortManifestBody({
      changeId: design?.change_id,
      changeRoot,
      sourceRevision: sourceResolved.source_revision,
      design,
      redBySlice,
      implementBySlice,
      repoRoot,
      verifyCommandsBySlice: mergedVerifyBySlice,
    });
  if (bodyBuilt?.ok === false) {
    return bodyBuilt;
  }
  const body = bodyBuilt;
  const frozen = freezeRecoveryCohortManifest({
    repoRoot,
    changeRoot,
    recoveryAttemptId,
    manifestBody: body,
  });
  if (!frozen.ok) {
    return frozen;
  }
  const manifest = frozen.manifest;
  const admission = preflightRecoveryCohortAdmissionGates({
    repoRoot,
    changeRoot,
    design,
    manifest,
    affectedSliceIds: affectedIds,
    ownerDecisionsBySlice,
    ownerDecisionSecret,
    redBySlice,
    implementBySlice,
  });
  if (!admission.ok) {
    return admission;
  }
  const unaffectedFreshness = {};
  for (const sliceId of manifest.unaffected_slice_ids) {
    unaffectedFreshness[sliceId] = proveUnaffectedSliceFreshness({
      sliceId,
      red: redBySlice[sliceId],
      implement: implementBySlice[sliceId],
      green: greenBySlice[sliceId],
      repoRoot,
      verifyCommand: (mergedVerifyBySlice[sliceId] || [])[0] || null,
      runCommand,
    });
    if (!unaffectedFreshness[sliceId].ok) {
      return {
        ok: false,
        code: "RECOVERY_UNAFFECTED_FRESHNESS_FAILED",
        slice_id: sliceId,
        unaffected_freshness: unaffectedFreshness,
      };
    }
  }
  const sliceReceipts = {};
  for (const sliceId of manifest.affected_slice_ids) {
    const ownerDecision = ownerDecisionsBySlice[sliceId] || null;
    const sliceResult = recoverCohortAffectedSlice({
      design,
      scaffoldBySlice,
      redBySlice,
      implementBySlice,
      greenBySlice,
      upstreamBySlice,
      repoRoot,
      changeRoot,
      recoveryAttemptId,
      sliceId,
      manifest,
      ownerDecision,
      ownerDecisionSecret,
      verifyCommand: (mergedVerifyBySlice[sliceId] || [])[0] || null,
      runCommand,
    });
    if (!sliceResult.ok) {
      return {
        ok: false,
        code: sliceResult.code,
        slice_id: sliceId,
        slice_receipts: sliceReceipts,
        unaffected_freshness: unaffectedFreshness,
        manifest_id: manifest.manifest_id,
      };
    }
    sliceReceipts[sliceId] = sliceResult.receipt;
  }

  const cohortLedger = {
    schema: "opsx.post_implement_recovery_cohort_ledger",
    schema_version: 1,
    recovery_attempt_id: recoveryAttemptId,
    change_id: design.change_id,
    phase: COHORT_PHASE_COHORT_VERIFIED,
    cohort_manifest_id: manifest.manifest_id,
    slice_receipts: sliceReceipts,
    unaffected_freshness: unaffectedFreshness,
    terminal: false,
  };

  const { execution_revision: cohortExecutionRevision } = computeRecoveryExecutionRevision({
    red: redBySlice[manifest.affected_slice_ids[0]],
    implement: implementBySlice[manifest.affected_slice_ids[0]],
    repoRoot,
  });
  const cohortLineage = computeRecoveryGenerationLineage({
    changeId: design.change_id,
    sliceId: "COHORT",
    recoveryCode: "MULTI_SLICE_RECOVERY",
    ownerDecisionId: "OWNER_ACCEPTED_EQUIVALENCE",
    executionRevision: cohortExecutionRevision,
    recoveryManifestDigest: manifest.manifest_id,
    designSupersessionId: `DESIGN_SUPERSEDED:${manifest.manifest_id}`,
  });
  let publishedGeneration = null;
  const pub = publishRecoveryGeneration({
    repoRoot,
    changeRoot,
    recoveryAttemptId,
    generationLineageId: cohortLineage.generation_lineage_id,
    lineageBody: cohortLineage.body,
    ledger: cohortLedger,
  });
  if (!pub.ok) {
    return { ok: false, code: pub.code, cohort_ledger: cohortLedger };
  }
  publishedGeneration = pub.generation;

  const verifyCmd = (mergedVerifyBySlice[manifest.affected_slice_ids[0]] || [])[0];
  let freshEvidence = {
    status: FRESH_EVIDENCE_PASS,
    kind: "cohort_convergence",
    generation_lineage_id: cohortLineage.generation_lineage_id,
    execution_revision: cohortExecutionRevision,
    verification_gate_accepted: Boolean(verifyCmd),
    cohort_manifest_id: manifest.manifest_id,
    slice_receipt_count: Object.keys(sliceReceipts).length,
    unaffected_fresh_count: Object.keys(unaffectedFreshness).length,
    executions: verifyCmd
      ? [
          {
            kind: "command_execution",
            command: verifyCmd,
            exit_code: 0,
            execution_mechanism: "cohort_manifest_bound",
          },
        ]
      : [],
  };
  if (verifyCmd) {
    const red = redBySlice[manifest.affected_slice_ids[0]];
    const implement = implementBySlice[manifest.affected_slice_ids[0]];
    const executed = executeRecoveryFreshEvidence({
      recoveryPath: RECOVERY_PATH_B,
      red: {
        ...red,
        recovery_adoption_verification_commands: [verifyCmd],
        post_implement_verification_commands: [],
      },
      implement,
      generationLineageId: cohortLineage.generation_lineage_id,
      executionRevision: cohortExecutionRevision,
      boundSliceId: manifest.affected_slice_ids[0],
      repoRoot,
      ownerDecisionValidated: true,
      runCommand,
    });
    if (executed.status !== FRESH_EVIDENCE_PASS) {
      return { ok: false, code: "RECOVERY_EVIDENCE_FAILED", fresh_evidence: executed };
    }
    freshEvidence = anchorRecoveryEvidenceBindings(executed, {
      red,
      repoRoot,
      changeRoot,
      sliceId: manifest.affected_slice_ids[0],
    });
    freshEvidence.cohort_manifest_id = manifest.manifest_id;
  }

  cohortLedger.generation_lineage_id = cohortLineage.generation_lineage_id;
  cohortLedger.execution_revision = cohortExecutionRevision;
  cohortLedger.fresh_evidence_record = freshEvidence;

  const impl = recomputeCohortRecoveryImplOk({
    design,
    greenBySlice,
    upstreamBySlice,
    redBySlice,
    implementBySlice,
    manifest,
    sliceReceipts,
    unaffectedFreshness,
    recoveryLedger: cohortLedger,
    generation: publishedGeneration,
    repoRoot,
    changeRoot,
    recoveryAttemptId,
    freshEvidence,
  });
  if (!impl.ok) {
    cohortLedger.phase = COHORT_PHASE_SLICE_RECOVERY;
    return { ok: false, code: impl.code, cohort_ledger: cohortLedger, detail: impl };
  }
  cohortLedger.phase = COHORT_PHASE_IMPL_OK;
  cohortLedger.impl_ok = true;

  const cohortRecovery = {
    manifest_digest: manifest.manifest_id,
    affected_slice_ids: manifest.affected_slice_ids,
    unaffected_slice_ids: manifest.unaffected_slice_ids,
    slice_receipts: sliceReceipts,
    unaffected_freshness: unaffectedFreshness,
  };
  const successorDraft = buildBoundedVerifySuccessorMechanism({
    design,
    changeId: design.change_id,
    changeRoot,
    repoRoot,
    recoveryLedger: cohortLedger,
    freshEvidence,
    generation: publishedGeneration,
    redBySlice,
    implementBySlice,
    greenBySlice,
    upstreamBySlice,
    requirePublishedDiskProof: false,
    cohortRecovery,
  });
  if (!successorDraft.ok) {
    return { ok: false, code: successorDraft.code, cohort_ledger: cohortLedger };
  }
  const publishedSuccessor = persistRecoverySuccessorPlan({
    repoRoot,
    changeRoot,
    recoveryAttemptId,
    successorMechanism: successorDraft,
    design,
  });
  if (!publishedSuccessor.ok) {
    return { ok: false, code: publishedSuccessor.code, cohort_ledger: cohortLedger };
  }
  persistRecoveryFreshEvidence({
    repoRoot,
    changeRoot,
    recoveryAttemptId,
    freshEvidence,
    ledger: cohortLedger,
  });
  cohortLedger.phase = COHORT_PHASE_SUCCESSOR_PLAN;
  cohortLedger.terminal = true;
  cohortLedger.successor_plan_file = publishedSuccessor.path;
  persistRecoveryLedger({
    repoRoot,
    changeRoot,
    recoveryAttemptId,
    ledger: cohortLedger,
  });

  return {
    ok: true,
    code: "COHORT_VERIFIED",
    phase: COHORT_PHASE_SUCCESSOR_PLAN,
    manifest_id: manifest.manifest_id,
    generation_lineage_id: cohortLineage.generation_lineage_id,
    cohort_ledger: cohortLedger,
    slice_receipts: sliceReceipts,
    unaffected_freshness: unaffectedFreshness,
    fresh_evidence: freshEvidence,
    generation: publishedGeneration,
    design_supersession_id: `DESIGN_SUPERSEDED:${manifest.manifest_id}`,
    successor_plan_path: publishedSuccessor.path,
  };
}
