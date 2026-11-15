import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Native /opsx-apply-design writer transition: DESIGN review freshness → authorized
 * lease → APPLY-DESIGN-WRITER-RECORD.json (fail-closed, idempotent).
 *
 * Recovery cohort supersession consumes this record; manual JSON authoring is not
 * a supported write path.
 */
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  assertManualWriterLeaseForMutation,
  finishManualWriterMutationGuard,
  inferChangeRootFromArtifactPath,
} from "./opsx-apply-change-writer.mjs";
import { evaluateReviewFreshnessAgainstDesign } from "./opsx-apply-design-review.mjs";
import { fileSha256 } from "./opsx-apply-pipeline-identity.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);

export const AUTHORIZED_APPLY_DESIGN_COMMAND = "/opsx-apply-design";
export const APPLY_DESIGN_WRITER_RECORD_SCHEMA = "opsx.apply_design_writer_record";
export const WRITER_TRANSITION_PHASE_DESIGN_REVIEW_CURRENT = "DESIGN_REVIEW_CURRENT";
export const WRITER_TRANSITION_PHASE_AUTHORIZED = "WRITER_TRANSITION_AUTHORIZED";
export const WRITER_TRANSITION_PHASE_PROOF_PUBLISHED = "WRITER_PROOF_PUBLISHED";

function stableStringify(value) {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((e) => stableStringify(e)).join(",")}]`;
  }
  const keys = Object.keys(value).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${stableStringify(value[k])}`).join(",")}}`;
}

function sha256Hex(input) {
  return createHash("sha256").update(input, "utf8").digest("hex");
}

function atomicWriteJson(path, value) {
  mkdirSync(dirname(path), { recursive: true });
  const tmp = `${path}.${process.pid}.tmp`;
  writeFileSync(tmp, `${JSON.stringify(value, null, 2)}\n`);
  renameSync(tmp, path);
}

export function applyDesignWriterRecordRelPath(changeRoot) {
  const cr = String(changeRoot).replace(/\\/g, "/").replace(/\/$/, "");
  return `${cr}/apply-design/APPLY-DESIGN-WRITER-RECORD.json`;
}

export function computeWriterTransitionDigest({
  changeId,
  applyDesignSha256,
  applyDesignReviewArtifactSha256,
  boundCohortManifestId,
  authorizedCommand = AUTHORIZED_APPLY_DESIGN_COMMAND,
  writerTransitionCommand = AUTHORIZED_APPLY_DESIGN_COMMAND,
} = {}) {
  const core = {
    change_id: changeId,
    apply_design_sha256: applyDesignSha256,
    apply_design_review_artifact_sha256: applyDesignReviewArtifactSha256,
    bound_cohort_manifest_id: boundCohortManifestId,
    authorized_command: authorizedCommand,
    writer_transition_command: writerTransitionCommand,
    transition_phase: WRITER_TRANSITION_PHASE_PROOF_PUBLISHED,
  };
  return sha256Hex(stableStringify(core));
}

/**
 * Fail-closed match of an on-disk writer record to cohort + live DESIGN bytes.
 */
export function assertApplyDesignWriterRecordForRecovery({
  writer,
  cohortManifestId,
  designApplyDesignSha256,
} = {}) {
  if (!writer || writer.schema !== APPLY_DESIGN_WRITER_RECORD_SCHEMA) {
    return { ok: false, code: "RECOVERY_DESIGN_SUPERSESSION_UNPROVEN", detail: "writer_not_authorized" };
  }
  if (writer.authorized_command !== AUTHORIZED_APPLY_DESIGN_COMMAND) {
    return { ok: false, code: "RECOVERY_DESIGN_SUPERSESSION_UNPROVEN", detail: "writer_not_authorized" };
  }
  if (writer.writer_transition_confirmed !== true) {
    return { ok: false, code: "RECOVERY_DESIGN_SUPERSESSION_UNPROVEN", detail: "writer_transition_unconfirmed" };
  }
  if (writer.writer_transition_command !== AUTHORIZED_APPLY_DESIGN_COMMAND) {
    return { ok: false, code: "RECOVERY_DESIGN_SUPERSESSION_UNPROVEN", detail: "writer_transition_command_invalid" };
  }
  if (!writer.writer_transition_digest) {
    return { ok: false, code: "RECOVERY_DESIGN_SUPERSESSION_UNPROVEN", detail: "writer_transition_digest_missing" };
  }
  if (cohortManifestId && writer.bound_cohort_manifest_id !== cohortManifestId) {
    return { ok: false, code: "RECOVERY_DESIGN_SUPERSESSION_UNPROVEN", detail: "writer_cohort_manifest_mismatch" };
  }
  if (
    designApplyDesignSha256 &&
    writer.apply_design_sha256 &&
    writer.apply_design_sha256 !== designApplyDesignSha256
  ) {
    return { ok: false, code: "RECOVERY_DESIGN_SUPERSESSION_UNPROVEN", detail: "writer_sha_mismatch" };
  }
  const expectedDigest = computeWriterTransitionDigest({
    changeId: writer.change_id,
    applyDesignSha256: writer.apply_design_sha256,
    applyDesignReviewArtifactSha256: writer.apply_design_review_identity?.artifact_sha256,
    boundCohortManifestId: writer.bound_cohort_manifest_id,
  });
  if (expectedDigest !== writer.writer_transition_digest) {
    return { ok: false, code: "RECOVERY_DESIGN_SUPERSESSION_UNPROVEN", detail: "writer_transition_digest_invalid" };
  }
  return { ok: true };
}

function resolveDesignAndReviewPaths({ repoRoot, changeRoot, designPath, reviewPath } = {}) {
  const cr = changeRoot || null;
  const designAbs = designPath
    ? resourcePath(repoRoot, designPath)
    : resourcePath(repoRoot, cr, "apply-design/APPLY-DESIGN.json");
  const reviewAbs = reviewPath
    ? resourcePath(repoRoot, reviewPath)
    : resourcePath(repoRoot, cr, "apply-design-review/APPLY-DESIGN-REVIEW.json");
  if (!existsSync(designAbs)) {
    return { ok: false, code: "APPLY_DESIGN_MISSING", path: designAbs };
  }
  if (!existsSync(reviewAbs)) {
    return { ok: false, code: "APPLY_DESIGN_REVIEW_MISSING", path: reviewAbs };
  }
  const design = JSON.parse(readFileSync(designAbs, "utf8"));
  const review = JSON.parse(readFileSync(reviewAbs, "utf8"));
  const changeRootResolved =
    cr || design.planning_identity?.change_root || inferChangeRootFromArtifactPath(designAbs);
  return {
    ok: true,
    designAbs,
    reviewAbs,
    design,
    review,
    changeRoot: changeRootResolved,
  };
}

function buildWriterRecordProofBody({
  design,
  review,
  designSha,
  reviewSha,
  changeRootResolved,
  cohortManifestId,
  writerLeaseId = null,
} = {}) {
  const digest = computeWriterTransitionDigest({
    changeId: design.change_id,
    applyDesignSha256: designSha,
    applyDesignReviewArtifactSha256: reviewSha,
    boundCohortManifestId: cohortManifestId,
  });
  return {
    schema: APPLY_DESIGN_WRITER_RECORD_SCHEMA,
    schema_version: 1,
    authorized_command: AUTHORIZED_APPLY_DESIGN_COMMAND,
    writer_transition_confirmed: true,
    writer_transition_command: AUTHORIZED_APPLY_DESIGN_COMMAND,
    writer_transition_digest: digest,
    transition_phase: WRITER_TRANSITION_PHASE_PROOF_PUBLISHED,
    change_id: design.change_id,
    change_root: changeRootResolved,
    apply_design_sha256: designSha,
    apply_design_review_identity: {
      machine_path: review.apply_design_identity?.machine_path || null,
      artifact_sha256: reviewSha,
      planning_digest: review.apply_design_identity?.planning_digest || null,
      protected_context_digest: review.apply_design_identity?.protected_context_digest || null,
    },
    bound_cohort_manifest_id: cohortManifestId,
    writer_lease_id: writerLeaseId,
    recorded_at: new Date().toISOString(),
  };
}

function persistWriterRecordProof({
  repoRoot,
  changeRootResolved,
  cohortManifestId,
  designSha,
  body,
} = {}) {
  const recordPath = resourcePath(repoRoot, applyDesignWriterRecordRelPath(changeRootResolved));
  if (existsSync(recordPath)) {
    const existing = JSON.parse(readFileSync(recordPath, "utf8"));
    const match = assertApplyDesignWriterRecordForRecovery({
      writer: existing,
      cohortManifestId,
      designApplyDesignSha256: designSha,
    });
    if (match.ok && existing.bound_cohort_manifest_id === cohortManifestId) {
      return {
        ok: true,
        idempotent: true,
        phase: WRITER_TRANSITION_PHASE_PROOF_PUBLISHED,
        path: applyDesignWriterRecordRelPath(changeRootResolved),
        writer_record: existing,
      };
    }
    return { ok: false, code: "WRITER_RECORD_COLLISION", detail: "existing_writer_record_mismatch" };
  }
  atomicWriteJson(recordPath, body);
  return {
    ok: true,
    phase: WRITER_TRANSITION_PHASE_PROOF_PUBLISHED,
    path: applyDesignWriterRecordRelPath(changeRootResolved),
    writer_record: body,
    transition_phase_authorized: WRITER_TRANSITION_PHASE_AUTHORIZED,
  };
}

/**
 * Hermetic/tests: same proof semantics as publish, without adoption/lease (fail-closed review gate only).
 */
export function materializeApplyDesignWriterRecordProof({
  repoRoot = DEFAULT_ROOT,
  changeRoot = null,
  designPath = null,
  reviewPath = null,
  cohortManifestId,
} = {}) {
  if (!cohortManifestId || typeof cohortManifestId !== "string") {
    return { ok: false, code: "WRITER_TRANSITION_COHORT_MANIFEST_REQUIRED" };
  }
  const loaded = resolveDesignAndReviewPaths({
    repoRoot,
    changeRoot,
    designPath,
    reviewPath,
  });
  if (!loaded.ok) {
    return loaded;
  }
  const { designAbs, reviewAbs, design, review, changeRoot: changeRootResolved } = loaded;
  const designSha = fileSha256(designAbs);
  const reviewSha = fileSha256(reviewAbs);
  const reviewFresh = evaluateReviewFreshnessAgainstDesign({
    design,
    review,
    designArtifactSha256: designSha,
  });
  if (!reviewFresh.ok) {
    return {
      ok: false,
      code: reviewFresh.code || "DESIGN_REVIEW_STALE",
      phase: WRITER_TRANSITION_PHASE_DESIGN_REVIEW_CURRENT,
      detail: "design_review_not_current_for_live_design",
    };
  }
  const body = buildWriterRecordProofBody({
    design,
    review,
    designSha,
    reviewSha,
    changeRootResolved,
    cohortManifestId,
    writerLeaseId: "hermetic-writer-proof",
  });
  return persistWriterRecordProof({
    repoRoot,
    changeRootResolved,
    cohortManifestId,
    designSha,
    body,
  });
}

/**
 * Publish APPLY-DESIGN-WRITER-RECORD.json via native transition only.
 */
export function publishApplyDesignWriterRecord({
  repoRoot = DEFAULT_ROOT,
  changeRoot = null,
  designPath = null,
  reviewPath = null,
  cohortManifestId,
  writerSessionId = null,
} = {}) {
  if (!cohortManifestId || typeof cohortManifestId !== "string") {
    return { ok: false, code: "WRITER_TRANSITION_COHORT_MANIFEST_REQUIRED" };
  }
  const loaded = resolveDesignAndReviewPaths({
    repoRoot,
    changeRoot,
    designPath,
    reviewPath,
  });
  if (!loaded.ok) {
    return loaded;
  }
  const { designAbs, reviewAbs, design, review, changeRoot: changeRootResolved } = loaded;
  const designSha = fileSha256(designAbs);
  const reviewSha = fileSha256(reviewAbs);
  const reviewFresh = evaluateReviewFreshnessAgainstDesign({
    design,
    review,
    designArtifactSha256: designSha,
  });
  if (!reviewFresh.ok) {
    return {
      ok: false,
      code: reviewFresh.code || "DESIGN_REVIEW_STALE",
      phase: WRITER_TRANSITION_PHASE_DESIGN_REVIEW_CURRENT,
      detail: "design_review_not_current_for_live_design",
    };
  }

  const writerGuard = assertManualWriterLeaseForMutation({
    repoRoot,
    changeRoot: changeRootResolved,
    writerSessionId,
    changeId: design.change_id,
    planningDigest: design.planning_identity?.planning_digest,
    primitive: "APPLY_DESIGN_WRITER_TRANSITION",
  });
  if (!writerGuard.ok) {
    return writerGuard;
  }

  const body = buildWriterRecordProofBody({
    design,
    review,
    designSha,
    reviewSha,
    changeRootResolved,
    cohortManifestId,
    writerLeaseId: writerGuard.lock?.writer_lease_id || writerGuard.writer_session_id || null,
  });

  try {
    return persistWriterRecordProof({
      repoRoot,
      changeRootResolved,
      cohortManifestId,
      designSha,
      body,
    });
  } finally {
    finishManualWriterMutationGuard({
      repoRoot,
      changeRoot: changeRootResolved,
      guard: writerGuard,
    });
  }
}
