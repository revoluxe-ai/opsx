import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Зеркало workflows/artifact_paths.py: канонические пути receipts и planning.
 *
 * Receipts: opsx/receipts/<change_id>/...
 * Planning: openspec/changes/<change_id>/...
 */
import { resolve, sep } from "node:path";

const CHANGE_SAFE = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

const APPLY_DESIGN_DIR = "apply-design";
const APPLY_DESIGN_REVIEW_DIR = "apply-design-review";
const APPLY_SCAFFOLD_DIR = "apply-scaffold";
const APPLY_RED_DIR = "apply-red";
const APPLY_IMPLEMENT_DIR = "apply-implement";
const APPLY_GREEN_DIR = "apply-green";
const SCENARIO_COVERAGE_RECEIPTS_DIR = "scenario-coverage";

const STAGE_BY_ROUTING = {
  APPLY_SCAFFOLD: [APPLY_SCAFFOLD_DIR, "SCAFFOLD.json"],
  APPLY_RED: [APPLY_RED_DIR, "RED.json"],
  APPLY_IMPLEMENT: [APPLY_IMPLEMENT_DIR, "IMPLEMENTATION.json"],
  APPLY_GREEN: [APPLY_GREEN_DIR, "GREEN.json"],
};

const STAGE_BY_FOLDER = {
  "apply-scaffold": ["SCAFFOLD.json", "SCAFFOLD.md"],
  "apply-red": ["RED.json", "RED.md"],
  "apply-implement": ["IMPLEMENTATION.json", "IMPLEMENTATION.md"],
  "apply-green": ["GREEN.json", "GREEN.md"],
};

function assertChangeId(changeId) {
  if (typeof changeId !== "string" || !CHANGE_SAFE.test(changeId) || changeId === "." || changeId === "..") {
    throw new Error("CHANGE_ID_UNSAFE");
  }
}

/**
 * @param {string} repoRoot
 * @param {string} changeId
 * @returns {string}
 */
export function planningChangeRel(repoRoot, changeId) {
  assertChangeId(changeId);
  return `openspec/changes/${changeId}`;
}

/**
 * @param {string} repoRoot
 * @param {string} changeId
 * @returns {string}
 */
export function receiptsRel(changeId) {
  assertChangeId(changeId);
  return `opsx/receipts/${changeId}`;
}

/**
 * @param {string} repoRoot
 * @param {string} changeId
 * @returns {string}
 */
export function receiptsAbs(repoRoot, changeId) {
  return resourcePath(repoRoot, receiptsRel(changeId));
}

/**
 * @param {string} repoRoot
 * @param {string} changeId
 * @returns {string}
 */
export function planningChangeAbs(repoRoot, changeId) {
  return resourcePath(repoRoot, planningChangeRel(repoRoot, changeId));
}

/**
 * @param {string} changeId
 * @param {string} stage routing или apply-* folder
 * @param {string} sliceId
 * @returns {string} relative POSIX path from repo root
 */
export function stageReceiptRel(changeId, stage, sliceId) {
  assertChangeId(changeId);
  const pair = STAGE_BY_ROUTING[stage];
  let folder;
  let filename;
  if (pair) {
    [folder, filename] = pair;
  } else if (STAGE_BY_FOLDER[stage]) {
    folder = stage;
    [filename] = STAGE_BY_FOLDER[stage];
  } else {
    throw new Error(`UNKNOWN_STAGE_RECEIPT: ${stage}`);
  }
  return `${receiptsRel(changeId)}/${folder}/${sliceId}/${filename}`;
}

/**
 * @param {string} repoRoot
 * @param {string} changeId
 * @param {string} stage
 * @param {string} sliceId
 * @returns {string}
 */
export function stageReceiptAbs(repoRoot, changeId, stage, sliceId) {
  return resourcePath(repoRoot, stageReceiptRel(changeId, stage, sliceId));
}

/**
 * @param {string} changeId
 * @returns {string}
 */
export function applyDesignArtifactRel(changeId) {
  assertChangeId(changeId);
  return `openspec/changes/${changeId}/${APPLY_DESIGN_DIR}/APPLY-DESIGN.json`;
}

export function applyDesignHopReceiptRel(changeId) {
  return `${receiptsRel(changeId)}/${APPLY_DESIGN_DIR}/hop.receipt.json`;
}

/** @deprecated Канон design больше не в receipts. Оставлено как alias на artifact. */
export function applyDesignReceiptRel(changeId) {
  return applyDesignArtifactRel(changeId);
}

/**
 * @param {string} repoRoot
 * @param {string} changeId
 * @returns {string}
 */
export function applyDesignArtifactAbs(repoRoot, changeId) {
  return resourcePath(repoRoot, applyDesignArtifactRel(changeId));
}

export function applyDesignHopReceiptAbs(repoRoot, changeId) {
  return resourcePath(repoRoot, applyDesignHopReceiptRel(changeId));
}

export function applyDesignReceiptAbs(repoRoot, changeId) {
  return applyDesignArtifactAbs(repoRoot, changeId);
}

/**
 * @param {string} changeId
 * @returns {string}
 */
export function applyDesignReviewReceiptRel(changeId) {
  return `${receiptsRel(changeId)}/${APPLY_DESIGN_REVIEW_DIR}/APPLY-DESIGN-REVIEW.json`;
}

/**
 * @param {string} repoRoot
 * @param {string} changeId
 * @returns {string}
 */
export function applyDesignReviewReceiptAbs(repoRoot, changeId) {
  return resourcePath(repoRoot, applyDesignReviewReceiptRel(changeId));
}

/**
 * @param {string} changeId
 * @param {"implement"|"green"} stage
 * @param {string} sliceId
 * @returns {string|null}
 */
export function adoptionStageReceiptRel(changeId, stage, sliceId) {
  if (stage === "implement") {
    return stageReceiptRel(changeId, "APPLY_IMPLEMENT", sliceId);
  }
  if (stage === "green") {
    return stageReceiptRel(changeId, "APPLY_GREEN", sliceId);
  }
  return null;
}

/**
 * @param {string} changeId
 * @param {string} filename
 * @returns {string}
 */
export function scenarioMachineReceiptRel(changeId, filename) {
  assertChangeId(changeId);
  const name = String(filename || "").replace(/\\/g, "/").replace(/^\//, "");
  if (name.includes("..") || name.includes("/")) {
    throw new Error("SCENARIO_RECEIPT_NAME_UNSAFE");
  }
  return `${receiptsRel(changeId)}/${SCENARIO_COVERAGE_RECEIPTS_DIR}/${name}`;
}

/**
 * @param {string} repoRoot
 * @param {string} changeId
 * @param {string} filename
 * @returns {string}
 */
export function scenarioMachineReceiptAbs(repoRoot, changeId, filename) {
  return resourcePath(repoRoot, scenarioMachineReceiptRel(changeId, filename));
}

/**
 * @param {string} repoRoot
 * @param {string} changeId
 * @returns {string}
 */
export function implVerifyReceiptAbs(repoRoot, changeId) {
  return resourcePath(repoRoot, `${receiptsRel(changeId)}/IMPL-VERIFY-RECEIPT.json`);
}

/**
 * @param {string} absPath
 * @param {string} repoRoot
 * @returns {string}
 */
export function relposFromRepo(absPath, repoRoot) {
  const root = resolve(repoRoot);
  const target = resolve(absPath);
  const rel = target.slice(root.length).replace(/\\/g, "/");
  return rel.startsWith("/") ? rel.slice(1) : rel;
}

export {
  APPLY_DESIGN_DIR,
  APPLY_DESIGN_REVIEW_DIR,
  APPLY_SCAFFOLD_DIR,
  APPLY_RED_DIR,
  APPLY_IMPLEMENT_DIR,
  APPLY_GREEN_DIR,
  SCENARIO_COVERAGE_RECEIPTS_DIR,
};
