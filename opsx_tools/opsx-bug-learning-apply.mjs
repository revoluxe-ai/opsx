import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * APPLY consumer hooks for bug-learning V0 knowledge obligations.
 * Imported by opsx-apply-design/red/implement/green — never writes LEARNING_RECEIPT.json.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { collectPipelineMutablePaths } from "./opsx-apply-pipeline-identity.mjs";
import {
  matchSeamsForQuery,
  normalizePath,
  rebuildIndex,
  TOOL_VERSION,
} from "./opsx-bug-learning-match.mjs";
import { rawBytesSha256 } from "./opsx-bug-learning-hash.mjs";

export const IMPACT_DISCOVERY_EXPANDED = "IMPACT_DISCOVERY_EXPANDED";
export const MISSING_KNOWLEDGE_OBLIGATION = "MISSING_KNOWLEDGE_OBLIGATION";
export const KNOWLEDGE_OBLIGATION_IGNORED = "KNOWLEDGE_OBLIGATION_IGNORED";
export const UNRESOLVED_IMPACT_EXPAND = "UNRESOLVED_IMPACT_EXPAND";

const DEFAULT_SEAMS_JSONL = "opsx-knowledge/behavioral-seams.jsonl";
const DEFAULT_SEAMS_INDEX = "opsx-knowledge/behavioral-seams-index.json";

/**
 * Load matcher index from repo; empty records if files missing.
 * @param {string} repoRoot
 * @param {{ seamsJsonl?: string, seamsIndex?: string }} [opts]
 * @returns {{ records: object[], indexPath: string }}
 */
export function loadMatcherIndex(repoRoot, opts = {}) {
  const indexRel = opts.seamsIndex || DEFAULT_SEAMS_INDEX;
  const jsonlRel = opts.seamsJsonl || DEFAULT_SEAMS_JSONL;
  const indexAbs = resourcePath(repoRoot, indexRel);
  const jsonlAbs = resourcePath(repoRoot, jsonlRel);
  if (existsSync(indexAbs)) {
    const index = JSON.parse(readFileSync(indexAbs, "utf8"));
    return { records: index.records || [], indexPath: indexRel };
  }
  if (existsSync(jsonlAbs)) {
    const bytes = readFileSync(jsonlAbs);
    const index = rebuildIndex(bytes, TOOL_VERSION);
    return { records: index.records || [], indexPath: indexRel };
  }
  return { records: [], indexPath: indexRel };
}

/**
 * Collect planned surfaces from design artifact for matcher query.
 * @param {object} design
 * @returns {{ paths: string[], symbols: string[], concepts: string[] }}
 */
export function collectPlannedSurfaces(design) {
  const paths = new Set();
  for (const p of collectPipelineMutablePaths(design) || []) {
    paths.add(normalizePath(p));
  }
  for (const slice of design?.slices || []) {
    for (const p of slice.expected_product_write_surfaces || []) {
      paths.add(normalizePath(p));
    }
    for (const p of slice.relevant_existing_surfaces || []) {
      paths.add(normalizePath(p));
    }
  }
  return { paths: [...paths], symbols: [], concepts: [] };
}

/**
 * Inject knowledge obligations into APPLY-DESIGN slices (in-memory).
 * @param {object} design
 * @param {string} repoRoot
 * @param {{ seamsJsonl?: string, seamsIndex?: string }} [opts]
 * @returns {{ design: object, knowledge_obligations: object[], matched_seam_ids: string[] }}
 */
export function injectKnowledgeIntoDesign(design, repoRoot, opts = {}) {
  const { records } = loadMatcherIndex(repoRoot, opts);
  const query = collectPlannedSurfaces(design);
  const matched = matchSeamsForQuery(query, records);
  if (matched.length === 0) {
    return {
      design,
      knowledge_obligations: design.knowledge_obligations || [],
      matched_seam_ids: [],
    };
  }

  const next = JSON.parse(JSON.stringify(design));
  const obligations = [];
  const matchedIds = [];

  for (const seam of matched) {
    matchedIds.push(seam.seam_id);
    const injectedFields = [];
    for (const slice of next.slices || []) {
      const sliceQuery = {
        paths: [
          ...(slice.expected_product_write_surfaces || []).map(normalizePath),
          ...(slice.relevant_existing_surfaces || []).map(normalizePath),
        ],
        symbols: [],
        concepts: [],
      };
      if (!matchSeamsForQuery(sliceQuery, [seam]).length) continue;

      for (const reg of seam.regression_obligations || []) {
        const testSeam = reg.suggested_test_seam || reg.summary;
        if (testSeam && !slice.red_test_targets_or_seams.includes(testSeam)) {
          slice.red_test_targets_or_seams.push(testSeam);
          injectedFields.push(`slices.${slice.slice_id}.red_test_targets_or_seams`);
        }
      }
      if (!next.composition_completeness) {
        next.composition_completeness = { obligations: [] };
      }
      if (!next.composition_completeness.obligations) {
        next.composition_completeness.obligations = [];
      }
      const obligationEntry = {
        obligation: seam.obligation_summary || `seam:${seam.seam_id}`,
        placement: "B_RED",
      };
      const exists = next.composition_completeness.obligations.some(
        (o) => o.obligation === obligationEntry.obligation && o.placement === "B_RED",
      );
      if (!exists) {
        next.composition_completeness.obligations.push(obligationEntry);
        injectedFields.push("composition_completeness.obligations");
      }
    }
    obligations.push({
      seam_id: seam.seam_id,
      seam_content_sha256: seam.content_sha256,
      injected_fields: [...new Set(injectedFields)],
    });
  }

  next.knowledge_obligations = obligations;
  return {
    design: next,
    knowledge_obligations: obligations,
    matched_seam_ids: matchedIds,
  };
}

/**
 * Fail if design receipt lists obligations but slices omit injected targets.
 * @param {object} design
 * @returns {{ ok: boolean, errors: object[] }}
 */
export function validateDesignKnowledgeObligations(design) {
  const errors = [];
  const obligations = design.knowledge_obligations || [];
  if (obligations.length === 0) return { ok: true, errors };

  for (const ob of obligations) {
    const seamId = ob.seam_id;
    let found = false;
    for (const slice of design.slices || []) {
      for (const field of ob.injected_fields || []) {
        if (field.includes("red_test_targets_or_seams")) {
          found = slice.red_test_targets_or_seams?.length > 0;
        }
        if (field.includes("composition_completeness")) {
          found =
            found ||
            (design.composition_completeness?.obligations || []).length > 0;
        }
      }
    }
    if (!found && (ob.injected_fields || []).length > 0) {
      errors.push({
        code: KNOWLEDGE_OBLIGATION_IGNORED,
        message: `Matched seam ${seamId} obligations not reflected in slice fields`,
      });
    }
  }
  return { ok: errors.length === 0, errors };
}

/** Cross-slice contract paths explicitly assigned to a slice for RED obligations. */
function collectCrossSliceRedTestObligations(sliceId, design) {
  const targets = new Set();
  for (const csc of design?.cross_slice_contracts || []) {
    if (!csc || typeof csc !== "object") continue;
    const owner =
      csc.obligation_owner_slice_id || csc.assigned_slice_id || csc.dependent_slice_id;
    if (owner !== sliceId) continue;
    for (const list of [
      csc.red_test_targets_or_seams,
      csc.assigned_red_test_targets,
      csc.upstream_red_test_targets,
    ]) {
      if (!Array.isArray(list)) continue;
      for (const t of list) {
        if (t) targets.add(String(t));
      }
    }
  }
  return [...targets];
}

/**
 * RED freeze: missing knowledge-mandated tests block RED_PROVEN.
 * Scoped to the receipt slice plus explicit cross-slice contract assignments.
 */
export function validateRedKnowledgeObligations(red, design) {
  const errors = [];
  const obligations = design?.knowledge_obligations || [];
  if (obligations.length === 0 || red.status !== "RED_PROVEN") {
    return { ok: true, errors };
  }

  const frozen = new Set([
    ...(red.test_paths || []),
    ...(red.fixture_paths || []),
    ...(red.test_commands || []).map((c) => String(c)),
  ]);
  const sliceId = red.slice_id ? String(red.slice_id) : null;
  if (!sliceId) {
    return {
      ok: false,
      errors: [
        {
          code: MISSING_KNOWLEDGE_OBLIGATION,
          message: "RED_PROVEN requires slice_id to scope knowledge obligations",
        },
      ],
    };
  }
  const slice =
    Array.isArray(design?.slices) && design.slices.find((s) => s && s.slice_id === sliceId);
  if (!slice) {
    return {
      ok: false,
      errors: [
        {
          code: MISSING_KNOWLEDGE_OBLIGATION,
          message: `RED slice_id ${sliceId} not found in APPLY-DESIGN`,
          slice_id: sliceId,
        },
      ],
    };
  }
  const seamObligations = [
    ...(slice.red_test_targets_or_seams || []),
    ...collectCrossSliceRedTestObligations(sliceId, design),
  ];
  for (const target of seamObligations) {
    const covered = [...frozen].some((f) => f.includes(target) || target.includes(f));
    if (!covered) {
      errors.push({
        code: MISSING_KNOWLEDGE_OBLIGATION,
        message: `RED missing knowledge obligation test/seam: ${target}`,
        slice_id: sliceId,
      });
    }
  }
  return { ok: errors.length === 0, errors };
}

/**
 * Detect extra CURRENT seams matched by ACTUAL mutation paths vs DESIGN bind.
 * @param {object} implement
 * @param {object} design
 * @param {string} repoRoot
 * @returns {{ expanded: boolean, extra_seam_ids: string[], impact: object|null }}
 */
export function detectImpactDiscoveryExpanded(implement, design, repoRoot) {
  const written = [
    ...(implement.production_paths_modified || []),
    ...(implement.production_paths_created || []),
  ].map(normalizePath);

  const { records } = loadMatcherIndex(repoRoot);
  const actualMatched = matchSeamsForQuery({ paths: written, symbols: [], concepts: [] }, records);
  const designBound = new Set((design.knowledge_obligations || []).map((o) => o.seam_id));
  const extra = actualMatched.filter((s) => !designBound.has(s.seam_id));

  if (extra.length === 0) {
    return { expanded: false, extra_seam_ids: [], impact: null };
  }

  return {
    expanded: true,
    extra_seam_ids: extra.map((s) => s.seam_id),
    impact: {
      status: IMPACT_DISCOVERY_EXPANDED,
      extra_seam_ids: extra.map((s) => s.seam_id),
      actual_mutation_paths: written,
      next_hop: "APPLY_RED_IMPACT_EXPAND",
    },
  };
}

/**
 * Build additive RED-IMPACT-EXPAND overlay artifact (does not touch original RED.json).
 * @param {object} params
 * @returns {object}
 */
export function buildRedImpactExpandOverlay({
  changeId,
  sliceId,
  extraSeams,
  originalRed,
}) {
  const tests = [];
  for (const seam of extraSeams) {
    for (const reg of seam.regression_obligations || []) {
      if (reg.suggested_test_seam) {
        tests.push({
          obligation_id: reg.obligation_id,
          test_seam: reg.suggested_test_seam,
          seam_id: seam.seam_id,
        });
      }
    }
  }
  return {
    schema: "opsx.apply_red_impact_expand",
    schema_version: 1,
    change_id: changeId,
    slice_id: sliceId,
    status: "IMPACT_EXPAND_PENDING",
    parent_red_sha256: originalRed?.artifact_sha256 || null,
    additive_test_commands: tests.map(
      (t) => `node --test ${t.test_seam}`,
    ),
    extra_seam_ids: extraSeams.map((s) => s.seam_id),
    generated_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
  };
}

/**
 * GREEN blocked when impact-expand unresolved or overlay tests not proven.
 * @param {object} green
 * @param {object} red
 * @param {object|null} impactExpand
 * @returns {{ ok: boolean, errors: object[] }}
 */
export function validateGreenKnowledgeUnion(green, red, impactExpand) {
  const errors = [];
  if (green.status !== "GREEN_PROVEN") return { ok: true, errors };

  if (impactExpand && impactExpand.status === "IMPACT_EXPAND_PENDING") {
    errors.push({
      code: UNRESOLVED_IMPACT_EXPAND,
      message: "GREEN forbidden until RED-IMPACT-EXPAND overlay is proven",
    });
  }

  if (impactExpand?.additive_test_commands?.length) {
    const proven = impactExpand.proven === true || impactExpand.status === "IMPACT_EXPAND_PROVEN";
    if (!proven) {
      errors.push({
        code: UNRESOLVED_IMPACT_EXPAND,
        message: "GREEN requires union of original RED + impact-expand overlay tests",
      });
    }
  }

  return { ok: errors.length === 0, errors };
}

/**
 * Hash APPLY artifact raw bytes for bind-consumer.
 * @param {string} repoRoot
 * @param {string} relPath
 * @returns {string}
 */
export function artifactSha256(repoRoot, relPath) {
  const abs = resourcePath(repoRoot, relPath);
  return rawBytesSha256(readFileSync(abs));
}

/**
 * Extract knowledge obligations from APPLY-DESIGN artifact for bind-consumer validation.
 * @param {string} repoRoot
 * @param {string} designRelPath
 * @returns {object[]}
 */
export function readDesignKnowledgeObligations(repoRoot, designRelPath) {
  const abs = resourcePath(repoRoot, designRelPath);
  if (!existsSync(abs)) return [];
  const design = JSON.parse(readFileSync(abs, "utf8"));
  return design.knowledge_obligations || [];
}

/**
 * Resolve machine_path or artifact_path from a pipeline identity block.
 * @param {object|null|undefined} identity
 * @returns {string|null}
 */
export function resolveIdentityRelPath(identity) {
  if (!identity || typeof identity !== "object") return null;
  return identity.machine_path || identity.artifact_path || null;
}

/**
 * Load a repo-relative JSON artifact when present.
 * @param {string} repoRoot
 * @param {string|null|undefined} relPath
 * @returns {object|null}
 */
export function loadLinkedArtifactJson(repoRoot, relPath) {
  if (!relPath) return null;
  const abs = resourcePath(repoRoot, String(relPath).replace(/\\/g, "/"));
  if (!existsSync(abs)) return null;
  return JSON.parse(readFileSync(abs, "utf8"));
}

/**
 * Load APPLY-DESIGN linked from any receipt carrying apply_design_identity.
 * @param {string} repoRoot
 * @param {object} artifact
 * @returns {object|null}
 */
export function loadLinkedDesign(repoRoot, artifact) {
  return loadLinkedArtifactJson(repoRoot, resolveIdentityRelPath(artifact?.apply_design_identity));
}

/**
 * Load RED.json linked from implement/green red_identity.
 * @param {string} repoRoot
 * @param {object} artifact
 * @returns {object|null}
 */
export function loadLinkedRed(repoRoot, artifact) {
  return loadLinkedArtifactJson(repoRoot, resolveIdentityRelPath(artifact?.red_identity));
}

/**
 * Resolve RED-IMPACT-EXPAND overlay path adjacent to a RED receipt.
 * @param {string} repoRoot
 * @param {string} redArtifactPath absolute or repo-relative RED.json path
 * @returns {string|null} repo-relative overlay path when file exists
 */
export function discoverImpactExpandOverlay(repoRoot, redArtifactPath) {
  if (!redArtifactPath) return null;
  const redAbs = resourcePath(repoRoot, String(redArtifactPath).replace(/\\/g, "/"));
  const overlayAbs = resolve(dirname(redAbs), "RED-IMPACT-EXPAND.json");
  if (!existsSync(overlayAbs)) return null;
  const rel = overlayAbs
    .replace(/\\/g, "/")
    .replace(String(repoRoot).replace(/\\/g, "/").replace(/\/$/, "") + "/", "");
  return rel;
}

/**
 * Load RED-IMPACT-EXPAND overlay for a GREEN/RED artifact when present.
 * @param {string} repoRoot
 * @param {object} artifact
 * @param {string} [artifactPath] path used to validate RED artifact
 * @returns {object|null}
 */
export function loadImpactExpandOverlay(repoRoot, artifact, artifactPath = "") {
  const redRel =
    resolveIdentityRelPath(artifact?.red_identity) ||
    (artifactPath ? String(artifactPath).replace(/\\/g, "/") : null);
  const overlayRel = discoverImpactExpandOverlay(repoRoot, redRel || artifactPath);
  return overlayRel ? loadLinkedArtifactJson(repoRoot, overlayRel) : null;
}
