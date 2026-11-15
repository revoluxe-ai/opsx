import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * APPLY IMPLEMENT artifact validator, slice selection, and identity helpers.
 *
 * Does not write production. Does not run or modify tests. Agents produce
 * IMPLEMENTATION.json after one bounded production-write episode; this tool
 * checks schema, IMPLEMENTATION_WRITTEN semantics, deterministic slice pick,
 * frozen-RED identity, write-surface scope, and semantic slice scope
 * (current-slice responsibility / required_behavior / etc. — not path alone).
 *
 * Usage:
 *   node opsx_tools/opsx-apply-implement.mjs <IMPLEMENTATION.json>
 *   node opsx_tools/opsx-apply-implement.mjs --schema <schema> <IMPLEMENTATION.json>
 *   node opsx_tools/opsx-apply-implement.mjs --adopt-bundle <bundle.json>
 */
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  identityDigest,
  freezePlanningIdentity,
  computeCurrentPlanningIdentity,
  classifyApplyDesignPlanningFreshness,
  evaluateApplyDesignFreshnessFromDisk,
} from "./opsx-apply-design.mjs";
import {
  ALREADY_SATISFIED,
  AUTHORIZE_CURRENT_BYTE_ADOPTION,
  ADOPTION_EVIDENCE_GAP,
  CURRENT_BYTE_OWNER_ADOPTION,
  computeLiveFrozenTestContentIdentity,
  fileSha256,
  isAlreadySatisfiedEvidenceComplete,
  isApplyDesignFreshForImplement,
  isDurableAlreadySatisfiedObservation,
  isDurableRedProven,
  implementFrozenTestIdentityMatchesRedBytes,
  isRedEvidenceComplete,
  selectNextImplementableSlice,
} from "./opsx-apply-pipeline-identity.mjs";
import {
  detectImpactDiscoveryExpanded,
  IMPACT_DISCOVERY_EXPANDED,
  loadLinkedDesign,
  loadLinkedRed,
} from "./opsx-bug-learning-apply.mjs";
import {
  implementationAdoptedTransitionAuthorized,
  implementationAdoptedTransitionErrors,
  isAdoptionV2Claim,
  isLegacyAdoptionAuthority,
  runAdoptionAuthorityCheck,
} from "./opsx-adoption-transition-gate.mjs";
import {
  ADOPTION_CANDIDATE_DIGEST_ALGORITHM,
  sha256CanonicalJson,
} from "./opsx-adoption-canonical-json.mjs";

export {
  isAdoptionV2Claim,
  runAdoptionAuthorityCheck,
  implementationAdoptedTransitionAuthorized,
} from "./opsx-adoption-transition-gate.mjs";

export {
  detectImpactDiscoveryExpanded,
  IMPACT_DISCOVERY_EXPANDED,
} from "./opsx-bug-learning-apply.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);
const SCHEMA_REL = "opsx_tools/registry/apply_implement.schema.json";
const ADOPTION_CONTRACT_ID = "EVIDENCE_INTEGRITY_ADOPTION_V0";
const OWNER_ATTESTATION_PREFIX = /^(store:|cursor-stores\/|\/cursor\/stores\/|owner-message:)/;

export const IMPLEMENTATION_WRITTEN = "IMPLEMENTATION_WRITTEN";
export const IMPLEMENTATION_ADOPTED = "IMPLEMENTATION_ADOPTED";
export const GREEN_PROVEN = "GREEN_PROVEN";
export const RED_PROVEN = "RED_PROVEN";
export {
  ALREADY_SATISFIED,
  AUTHORIZE_CURRENT_BYTE_ADOPTION,
  ADOPTION_EVIDENCE_GAP,
  CURRENT_BYTE_OWNER_ADOPTION,
};
export const FORBIDDEN_SUCCESS_STATUSES = [
  "PASS",
  "GREEN",
  "GREEN_PROVEN",
  "VERIFIED",
  "COMPLETE_CHANGE",
];

export const FORBIDDEN_IMPLEMENT_SUBPROCESS_PATTERNS = [
  /\bnode\s+--test\b/,
  /\bnpm\s+test\b/,
  /\bnpm\s+run\s+test\b/,
  /\bpytest\b/,
  /\bpython\s+-m\s+unittest\b/,
  /\bpoetry\s+run\s+pytest\b/,
  /\bpoetry\s+run\s+python\s+-m\s+unittest\b/,
  /\bnpm\s+run\s+build\b/,
  /\bopsx-browser-check\b/,
  /\bopsx-run-tests\b/,
  /\bopsx-test\b/,
];

export {
  identityDigest,
  freezePlanningIdentity,
  computeCurrentPlanningIdentity,
  classifyApplyDesignPlanningFreshness,
  evaluateApplyDesignFreshnessFromDisk,
  isApplyDesignFreshForImplement,
  selectNextImplementableSlice,
  isDurableAlreadySatisfiedObservation,
  isDurableRedProven,
  computeLiveFrozenTestContentIdentity,
};

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function typeOk(value, schemaType) {
  if (schemaType === "object") return isObject(value);
  if (schemaType === "array") return Array.isArray(value);
  if (schemaType === "string") return typeof value === "string";
  if (schemaType === "integer") return Number.isInteger(value);
  if (schemaType === "boolean") return typeof value === "boolean";
  return true;
}

function resolveRef(rootSchema, ref) {
  if (!ref || !ref.startsWith("#/")) return null;
  const parts = ref.slice(2).split("/");
  let cur = rootSchema;
  for (const p of parts) {
    if (!isObject(cur) || !(p in cur)) return null;
    cur = cur[p];
  }
  return cur;
}

function validate(value, schema, rootSchema, path, errors) {
  if (!schema) return;
  if (schema.$ref) {
    validate(value, resolveRef(rootSchema, schema.$ref), rootSchema, path, errors);
    return;
  }
  if (schema.oneOf) {
    let matched = 0;
    let lastLocal = [];
    for (const branch of schema.oneOf) {
      const local = [];
      validate(value, branch, rootSchema, path, local);
      if (local.length === 0) matched += 1;
      else lastLocal = local;
    }
    if (matched !== 1) {
      errors.push({
        code: "oneOf_mismatch",
        message: `${path}: must match exactly one APPLY IMPLEMENT status branch`,
      });
      if (matched === 0) {
        for (const e of lastLocal) errors.push(e);
      }
    }
    return;
  }
  if (schema.type && !typeOk(value, schema.type)) {
    errors.push({ code: "type", message: `${path}: expected ${schema.type}` });
    return;
  }
  if (schema.const !== undefined && value !== schema.const) {
    errors.push({
      code: "const",
      message: `${path}: must be ${JSON.stringify(schema.const)}`,
    });
  }
  if (schema.enum && !schema.enum.includes(value)) {
    errors.push({ code: "enum", message: `${path}: invalid enum value` });
  }
  if (schema.type === "string") {
    if (schema.minLength && value.length < schema.minLength) {
      errors.push({ code: "minLength", message: `${path}: too short` });
    }
    if (schema.pattern) {
      const re = new RegExp(schema.pattern);
      if (!re.test(value)) {
        errors.push({ code: "pattern", message: `${path}: pattern failed` });
      }
    }
  }
  if (schema.type === "array") {
    if (schema.minItems !== undefined && value.length < schema.minItems) {
      errors.push({ code: "minItems", message: `${path}: too few items` });
    }
    if (schema.maxItems !== undefined && value.length > schema.maxItems) {
      errors.push({ code: "maxItems", message: `${path}: too many items` });
    }
    if (schema.items) {
      value.forEach((item, i) =>
        validate(item, schema.items, rootSchema, `${path}[${i}]`, errors),
      );
    }
  }
  if (
    isObject(value) &&
    (schema.type === "object" ||
      schema.properties ||
      schema.required ||
      schema.additionalProperties === false)
  ) {
    if (Array.isArray(schema.required)) {
      for (const key of schema.required) {
        if (!(key in value)) {
          errors.push({
            code: "required",
            message: `${path}: missing required field ${key}`,
          });
        }
      }
    }
    if (schema.additionalProperties === false) {
      const allowed = new Set(Object.keys(schema.properties || {}));
      for (const key of Object.keys(value)) {
        if (!allowed.has(key)) {
          errors.push({
            code: "additionalProperties",
            message: `${path}: unexpected field ${key}`,
          });
        }
      }
    }
    for (const [key, propSchema] of Object.entries(schema.properties || {})) {
      if (key in value) {
        validate(value[key], propSchema, rootSchema, `${path}.${key}`, errors);
      }
    }
  }
}

function posixPath(p) {
  return String(p || "").replace(/\\/g, "/");
}

export function isForbiddenImplementSubprocess(command) {
  const s = String(command || "");
  return FORBIDDEN_IMPLEMENT_SUBPROCESS_PATTERNS.some((re) => re.test(s));
}

export function looksLikeTestPath(path) {
  const p = posixPath(path);
  return (
    p.startsWith("tests/") ||
    p.startsWith("backend/tests/") ||
    /(^|\/)apply-red\//.test(p) ||
    /\.test\.(mjs|js|cjs|py)$/.test(p) ||
    /(^|\/)test_[^/]+\.py$/.test(p)
  );
}

export function isWithinExpectedSurface(path, surface) {
  const p = posixPath(path);
  const s = posixPath(surface);
  if (!p || !s) return false;
  if (p === s) return true;
  if (s.endsWith("/")) return p.startsWith(s);
  return p.startsWith(`${s}/`);
}

export function classifyImplementationScope({
  expectedSurfaces = [],
  writtenPaths = [],
  unexpectedButRequired = [],
} = {}) {
  const justified = new Set(
    (unexpectedButRequired || []).map((x) =>
      posixPath(typeof x === "string" ? x : x.path),
    ),
  );
  const extras = [];
  const unjustified = [];
  for (const raw of writtenPaths) {
    const p = posixPath(raw);
    if (looksLikeTestPath(p)) {
      unjustified.push(p);
      continue;
    }
    const expected = (expectedSurfaces || []).some((s) =>
      isWithinExpectedSurface(p, s),
    );
    if (expected) continue;
    extras.push(p);
    if (!justified.has(p)) unjustified.push(p);
  }
  if (unjustified.length) {
    return {
      ok: false,
      code: "BLOCKED_SCOPE_EXPANSION",
      paths: unjustified,
    };
  }
  return { ok: true, unexpected_but_required_paths: extras };
}

/** Identifier-like tokens that often mark owned behavior across slices. */
const SEMANTIC_ID_RE =
  /\b(?:putInFlight|putGeneration|getPutGate|applyHydrateSuccess|applyHydrateFailure|stale_generation|stale-generation|fail_closed|fail-closed|bootstrapColdStart|preserveInProgressPartyOnFailure|iframeSrc|cache-bust|requestCloudProgressHydrate)\b/g;

/**
 * Structural adapter / registration seams that may remain present without
 * implementing later-slice semantics (store adapter; do not interpret it).
 */
const STRUCTURAL_SEAM_PATTERNS = [
  /\bregisterNovelSaveHydrateHost\b/,
  /\bhostAdapter\s*=/,
  /\bexport function register/,
];

/**
 * Interpreting / filling a reserved host adapter seam with later-slice
 * semantics (gate, generation discard, apply/failure dispatch).
 */
const SEAM_FILL_PATTERNS = [
  /\.getPutGate\s*\(/,
  /\.putInFlight\b/,
  /\.putGeneration\b/,
  /\.applyHydrateSuccess\s*\(/,
  /\.applyHydrateFailure\s*\(/,
  /\breason:\s*['"]put_in_flight['"]/,
  /\breason:\s*['"]stale_generation['"]/,
];

function sliceAuthorityTexts(slice) {
  if (!isObject(slice)) return [];
  return [
    slice.responsibility,
    slice.required_behavior,
    slice.architectural_intent,
    slice.completion_contract,
    slice.implementation_notes,
    ...(Array.isArray(slice.invariants) ? slice.invariants : []),
    ...(Array.isArray(slice.source_task_ids)
      ? slice.source_task_ids.map((id) => `task:${id}`)
      : []),
  ]
    .filter((t) => typeof t === "string" && t.trim())
    .map((t) => String(t));
}

function extractSemanticMarkers(texts) {
  const out = new Set();
  for (const text of texts || []) {
    const s = String(text || "");
    const matches = s.match(SEMANTIC_ID_RE) || [];
    for (const m of matches) out.add(m.toLowerCase());
  }
  return [...out];
}

/**
 * Authority for the CURRENT slice only. Path authorization is insufficient;
 * behavior must match these fields.
 */
export function collectCurrentSliceSemanticAuthority(slice) {
  if (!isObject(slice)) {
    return {
      slice_id: null,
      source_task_ids: [],
      texts: [],
      markers: [],
    };
  }
  const texts = sliceAuthorityTexts(slice);
  return {
    slice_id: slice.slice_id || null,
    source_task_ids: [...(slice.source_task_ids || [])],
    responsibility: slice.responsibility || "",
    required_behavior: slice.required_behavior || "",
    architectural_intent: slice.architectural_intent || "",
    invariants: [...(slice.invariants || [])],
    completion_contract: slice.completion_contract || "",
    texts,
    markers: extractSemanticMarkers(texts),
  };
}

/**
 * Markers owned by other slices and not also authorized on the current slice.
 */
export function collectForeignSliceBehaviorMarkers(currentSlice, allSlices = []) {
  const current = collectCurrentSliceSemanticAuthority(currentSlice);
  const currentSet = new Set(current.markers);
  const foreign = [];
  for (const slice of allSlices || []) {
    if (!isObject(slice) || slice.slice_id === current.slice_id) continue;
    const markers = extractSemanticMarkers(sliceAuthorityTexts(slice));
    for (const m of markers) {
      if (currentSet.has(m)) continue;
      foreign.push({
        marker: m,
        owner_slice_id: slice.slice_id,
        source_task_ids: [...(slice.source_task_ids || [])],
      });
    }
  }
  return foreign;
}

function candidateHaystack(candidateTexts = [], claimedBehaviors = []) {
  return [...(candidateTexts || []), ...(claimedBehaviors || [])]
    .map((t) => String(t || ""))
    .join("\n");
}

/**
 * True when candidate only reserves a structural seam (adapter slot) without
 * filling later-slice semantics.
 */
export function isStructuralSeamReservationOnly(candidateText) {
  const text = String(candidateText || "");
  if (!text.trim()) return true;
  const hasStructural = STRUCTURAL_SEAM_PATTERNS.some((re) => re.test(text));
  const fillsSeam = SEAM_FILL_PATTERNS.some((re) => re.test(text));
  if (fillsSeam) return false;
  return hasStructural || !/\bhostAdapter\b/.test(text);
}

/**
 * Semantic slice scope: authorized path is not enough. Future-slice behavior
 * written into an expected surface is BLOCKED_SCOPE_EXPANSION.
 *
 * Structural scaffold seams reserved for later slices may remain present;
 * filling those seams with later-task semantics is rejected.
 */
export function classifySemanticSliceScope({
  currentSlice,
  allSlices = [],
  candidateTexts = [],
  claimedBehaviors = [],
} = {}) {
  if (!isObject(currentSlice)) {
    return {
      ok: false,
      code: "BLOCKED_SCOPE_EXPANSION",
      detail: "current slice contract missing for semantic scope check",
      hits: [],
    };
  }
  const authority = collectCurrentSliceSemanticAuthority(currentSlice);
  const foreign = collectForeignSliceBehaviorMarkers(currentSlice, allSlices);
  const haystack = candidateHaystack(candidateTexts, claimedBehaviors);
  const haystackLower = haystack.toLowerCase();
  const hits = [];

  for (const item of foreign) {
    const marker = item.marker;
    if (!marker) continue;
    // Word-boundary-ish match against candidate / claimed behavior text.
    const re = new RegExp(
      `(^|[^a-z0-9_])${marker.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^a-z0-9_]|$)`,
      "i",
    );
    if (re.test(haystackLower) || haystackLower.includes(marker)) {
      hits.push(item);
    }
  }

  const fillsSeam = SEAM_FILL_PATTERNS.some((re) => re.test(haystack));
  if (fillsSeam) {
    const seamOwners = foreign.filter((f) =>
      /putinflight|putgeneration|getputgate|applyhydratesuccess|applyhydratefailure|stale_generation|stale-generation/.test(
        f.marker,
      ),
    );
    for (const s of seamOwners) {
      if (!hits.some((h) => h.marker === s.marker && h.owner_slice_id === s.owner_slice_id)) {
        hits.push({ ...s, reason: "filled_reserved_seam" });
      }
    }
    if (!hits.length) {
      hits.push({
        marker: "host_adapter_semantic_fill",
        owner_slice_id: "FUTURE",
        reason: "filled_reserved_seam",
      });
    }
  }

  if (hits.length) {
    return {
      ok: false,
      code: "BLOCKED_SCOPE_EXPANSION",
      detail:
        "future-slice behavior in authorized path / reserved seam fill; " +
        "expected_product_write_surfaces is not sole semantic authority",
      hits,
      authority_slice_id: authority.slice_id,
      authority_source_task_ids: authority.source_task_ids,
    };
  }

  return {
    ok: true,
    authority_slice_id: authority.slice_id,
    structural_seam_ok: isStructuralSeamReservationOnly(haystack),
  };
}

function surfaceModuleStem(surface) {
  const p = posixPath(surface);
  const base = p.split("/").pop() || "";
  return base.replace(/\.[a-z0-9]+$/i, "");
}

/**
 * Frozen reuse_decision kind=reuse must not silently become a duplicated
 * local mechanism (e.g. local fetch clone of softProdNovelSaveGet).
 */
export function classifyReuseDecisionCompliance({
  sliceReuseIds = [],
  designReuseDecisions = [],
  candidateTexts = [],
  importSpecifiers = [],
} = {}) {
  const haystack = candidateHaystack(candidateTexts, []);
  const imports = (importSpecifiers || []).map((s) => String(s || ""));
  const violations = [];

  for (const rd of designReuseDecisions || []) {
    if (!isObject(rd) || rd.kind !== "reuse") continue;
    if (
      Array.isArray(sliceReuseIds) &&
      sliceReuseIds.length &&
      !sliceReuseIds.includes(rd.id)
    ) {
      continue;
    }
    const surface = posixPath(rd.surface || "");
    if (!surface) continue;
    const stem = surfaceModuleStem(surface);
    const imported = imports.some(
      (spec) =>
        spec.includes(surface) ||
        spec.includes(`/${stem}.js`) ||
        spec.includes(`/${stem}`) ||
        spec.endsWith(stem),
    );
    const mentionsExport =
      new RegExp(`\\b${stem}(?:Get|Put|Post|Delete)?\\b`, "i").test(haystack) &&
      /import\s*\{[^}]*\b/.test(haystack);

    // Silent duplication: local fetch to the same resource family without
    // importing the frozen reuse surface.
    const duplicatesHttpClient =
      /\bfetch\s*\(/.test(haystack) &&
      (/\/novel-saves\//.test(haystack) ||
        /novelSaveUrl\s*\(/.test(haystack) ||
        /novelSaveGet\s*\(/.test(haystack)) &&
      !imported &&
      !mentionsExport &&
      /softprodnovelsave/i.test(surface.replace(/\//g, ""));

    const surfaceIsNovelSave = /softProdNovelSave/i.test(surface);
    if (surfaceIsNovelSave && duplicatesHttpClient) {
      violations.push({
        id: rd.id,
        surface,
        detail:
          "reuse_decision=REUSE silently duplicated as local fetch/client; " +
          "must import frozen surface instead of cloning mechanism",
      });
      continue;
    }

    // Generic: REUSE surface never imported while candidate reimplements stem API.
    if (
      !imported &&
      new RegExp(`\\basync function ${stem}`, "i").test(haystack)
    ) {
      violations.push({
        id: rd.id,
        surface,
        detail: `reuse_decision=REUSE (${rd.id}) reimplemented locally without importing ${surface}`,
      });
    }
  }

  if (violations.length) {
    return {
      ok: false,
      code: "DESIGN_REALITY_DRIFT",
      violations,
      detail: violations.map((v) => v.detail).join("; "),
    };
  }
  return { ok: true, violations: [] };
}

/**
 * Extract static import specifiers from JS candidate text (best-effort).
 */
export function extractImportSpecifiers(candidateText) {
  const text = String(candidateText || "");
  const specs = [];
  const re =
    /(?:import\s+(?:[\s\S]*?\s+from\s+)?|export\s+[\s\S]*?\s+from\s+)['"]([^'"]+)['"]/g;
  let m;
  while ((m = re.exec(text))) {
    specs.push(m[1]);
  }
  return specs;
}

export function frozenTestIdentityMatches({
  frozen,
  before,
  after,
} = {}) {
  if (!frozen || !before) {
    return { ok: false, code: "STALE_RED" };
  }
  if (before !== frozen) {
    return { ok: false, code: "STALE_RED" };
  }
  if (after && after !== frozen) {
    return { ok: false, code: "FROZEN_RED_MUTATED" };
  }
  return { ok: true };
}

export function isRedFresh(red, { design, testContentIdentity, repoRoot } = {}) {
  const root = repoRoot || DEFAULT_ROOT;
  if (!isObject(red)) {
    return { ok: false, code: "IMPLEMENTATION_AUTHORITY_MISSING" };
  }
  if (isDurableAlreadySatisfiedObservation(red, root)) {
    return { ok: true, branch: "ADOPTION" };
  }
  if (red.status !== RED_PROVEN) {
    return { ok: false, code: "IMPLEMENTATION_AUTHORITY_MISSING" };
  }
  if (red.next_stage !== "APPLY_IMPLEMENT" || red.tests_frozen !== true) {
    return { ok: false, code: "STALE_RED" };
  }
  if (testContentIdentity && red.test_content_identity !== testContentIdentity) {
    return { ok: false, code: "STALE_RED" };
  }
  if (Array.isArray(red.test_commands) && !isRedEvidenceComplete(red)) {
    return { ok: false, code: "STALE_RED" };
  }
  if (!isDurableRedProven(red, root)) {
    return { ok: false, code: "STALE_RED" };
  }
  if (isObject(design)) {
    const id = red.apply_design_identity;
    const pid = design.planning_identity;
    const rid = design.repository_identity;
    if (
      !isObject(id) ||
      !isObject(pid) ||
      !isObject(rid) ||
      id.planning_digest !== pid.planning_digest ||
      id.inspected_digest !== rid.inspected_digest
    ) {
      return { ok: false, code: "STALE_RED" };
    }
  }
  return { ok: true };
}

/**
 * Build deterministic adoption candidate manifest binding current product
 * bytes, frozen tests, and planning/scaffold/protected-context identities.
 */
export function buildAdoptionCandidateManifest({
  changeRoot,
  sliceId,
  slice,
  design,
  scaffold,
  red,
  designReview,
  protectedContextDigest,
  productionManifest = [],
  repoRoot,
} = {}) {
  const cr = posixPath(changeRoot).replace(/\/$/, "");
  const liveTests = computeLiveFrozenTestContentIdentity(repoRoot, {
    test_paths: red?.test_paths,
    fixture_paths: red?.fixture_paths,
  });
  return {
    change_root: cr,
    slice_id: sliceId,
    slice_digest: slice?.slice_digest || red?.slice_identity?.slice_digest || null,
    planning_digest: design?.planning_identity?.planning_digest || null,
    protected_context_digest:
      protectedContextDigest ||
      design?.protected_context_identity?.content_digest ||
      null,
    design_review_status: designReview?.status || null,
    design_review_artifact_sha256: designReview?.apply_design_identity?.artifact_sha256 || null,
    scaffold_candidate_digest_after: scaffold?.candidate_digest_after || null,
    scaffold_artifact_sha256: scaffold?.artifact_sha256 || null,
    red_test_content_identity: red?.test_content_identity || null,
    live_frozen_test_content_identity: liveTests.identity,
    live_frozen_test_entries: liveTests.entries,
    production_manifest: [...productionManifest]
      .map((e) => ({
        path: posixPath(e.path),
        sha256: String(e.sha256 || ""),
      }))
      .sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0)),
    owner_what_digest: design?.owner_what_digest || design?.protected_context_identity?.owner_what_digest || null,
  };
}

export function computeAdoptionCandidateDigest(manifest) {
  return sha256CanonicalJson(manifest, createHash);
}

export { ADOPTION_CANDIDATE_DIGEST_ALGORITHM };

export function buildAdoptionGapPath(changeRoot, sliceId, candidateDigest) {
  const cr = posixPath(changeRoot).replace(/\/$/, "");
  return `${cr}/apply-implement/${sliceId}/ADOPTION-EVIDENCE-GAP-${candidateDigest}.json`;
}

export function buildAdoptionAuthorityPath(changeRoot, sliceId, candidateDigest) {
  const cr = posixPath(changeRoot).replace(/\/$/, "");
  return `${cr}/apply-implement/${sliceId}/CURRENT-BYTE-ADOPTION-AUTHORITY-${candidateDigest}.json`;
}

export function formatAuthorizeCurrentByteAdoptionInvocation({
  changeId,
  gapPath,
  candidateDigest,
}) {
  return `/opsx-apply-implement change=${changeId} ${AUTHORIZE_CURRENT_BYTE_ADOPTION} gap=${gapPath} candidate=${candidateDigest}`;
}

/**
 * Classify adoption evidence gap when current-byte Owner authority is missing.
 * Does not write product or test files.
 */
export function classifyAdoptionEvidenceGap({
  red,
  design,
  scaffold,
  designReview,
  slice,
  sliceId,
  changeRoot,
  repoRoot,
  productionManifest = [],
  protectedContextDigest,
  ownerToken = null,
  existingAuthority = null,
  adoptionCheck = null,
} = {}) {
  if (!isDurableAlreadySatisfiedObservation(red)) {
    return { ok: false, code: "IMPLEMENTATION_AUTHORITY_MISSING" };
  }
  if (!isAlreadySatisfiedEvidenceComplete(red)) {
    return { ok: false, code: "INCOMPLETE_RED_EVIDENCE" };
  }
  const manifest = buildAdoptionCandidateManifest({
    changeRoot,
    sliceId,
    slice,
    design,
    scaffold,
    red,
    designReview,
    protectedContextDigest,
    productionManifest,
    repoRoot,
  });
  const candidateDigest = computeAdoptionCandidateDigest(manifest);
  const gapPath = buildAdoptionGapPath(changeRoot, sliceId, candidateDigest);
  const authorityPath = buildAdoptionAuthorityPath(changeRoot, sliceId, candidateDigest);

  const liveTests = computeLiveFrozenTestContentIdentity(repoRoot, {
    test_paths: red.test_paths,
    fixture_paths: red.fixture_paths,
  });
  if (!liveTests.ok) {
    return {
      ok: false,
      code: "ADOPTION_EVIDENCE_GAP",
      candidate_digest: candidateDigest,
      gap_path: gapPath,
      detail: `live frozen test identity unavailable: ${liveTests.missing_paths.join(", ")}`,
    };
  }
  if (red.test_content_identity && red.test_content_identity !== liveTests.identity) {
    return {
      ok: false,
      code: "STALE_RED",
      candidate_digest: candidateDigest,
      detail: "frozen RED test_content_identity does not match live bytes",
    };
  }

  const decision = evaluateAdoptionAuthorization({
    ownerToken,
    check: adoptionCheck ?? null,
  });
  if (!decision.ok) {
    return {
      ok: false,
      code: decision.code,
      status: ADOPTION_EVIDENCE_GAP,
      candidate_digest: candidateDigest,
      gap_path: gapPath,
      authority_path: authorityPath,
      manifest,
      owner_invocation: formatAuthorizeCurrentByteAdoptionInvocation({
        changeId: design?.change_id || slice?.change_id || "unknown",
        gapPath,
        candidateDigest,
      }),
      detail: decision.detail,
    };
  }

  if (existingAuthority) {
    const valid = validateCurrentByteAdoptionAuthority(existingAuthority, {
      candidateDigest,
      gapPath,
      manifest,
      auditReceiptSha256: adoptionCheck?.audit_receipt_sha256,
    });
    if (!valid.ok || valid.legacy) {
      return {
        ok: false,
        code: valid.legacy ? "ADOPTION_AUTHORITY_LEGACY" : valid.code,
        detail: valid.detail,
      };
    }
  }

  return {
    ok: true,
    code: null,
    status: IMPLEMENTATION_ADOPTED,
    branch: "ADOPTION",
    candidate_digest: candidateDigest,
    gap_path: gapPath,
    authority_path: authorityPath,
    manifest,
    product_files_modified: false,
    tests_modified: false,
    tests_executed: false,
  };
}

/**
 * Literal AUTHORIZE_CURRENT_BYTE_ADOPTION is not adoption authority.
 * A passing S-EV2 checker result is required.
 */
export function evaluateAdoptionAuthorization({ ownerToken, check } = {}) {
  const tokenPresent =
    typeof ownerToken === "string" && ownerToken.trim() === AUTHORIZE_CURRENT_BYTE_ADOPTION;
  if (!check || check.implementation_adopted_authorized !== true) {
    const code =
      check?.findings?.find((item) => item && item.severity === "HARD")?.code ||
      (tokenPresent ? "ADOPTION_AUDIT_MISSING" : ADOPTION_EVIDENCE_GAP);
    return {
      ok: false,
      code,
      token_present: tokenPresent,
      detail: "AUTHORIZE_CURRENT_BYTE_ADOPTION is not sufficient without audit and authority v2",
    };
  }
  return { ok: true, token_present: tokenPresent, status: IMPLEMENTATION_ADOPTED };
}

/**
 * Validate CURRENT-BYTE-ADOPTION-AUTHORITY artifact semantics.
 */
export function validateCurrentByteAdoptionAuthority(authority, {
  candidateDigest,
  gapPath,
  manifest,
  repositoryHead,
  auditReceiptSha256,
} = {}) {
  if (!isObject(authority)) {
    return { ok: false, code: "ADOPTION_AUTHORITY_INVALID", detail: "missing authority" };
  }
  if (isLegacyAdoptionAuthority(authority)) {
    if (candidateDigest && authority.candidate_digest !== candidateDigest) {
      return { ok: false, code: "ADOPTION_AUTHORITY_INVALID", detail: "candidate_digest mismatch", legacy: true };
    }
    return {
      ok: true,
      legacy: true,
      code: "ADOPTION_AUTHORITY_LEGACY",
      detail: "v1 authority is readable and not adoptable",
    };
  }
  if (authority.schema_version !== 2) {
    return { ok: false, code: "ADOPTION_AUTHORITY_INVALID", detail: "authority schema_version must be 2" };
  }
  if (authority.authorization_class !== CURRENT_BYTE_OWNER_ADOPTION) {
    return { ok: false, code: "ADOPTION_AUTHORITY_INVALID", detail: "wrong authorization_class" };
  }
  if (!authority.audit_receipt_sha256 || !authority.owner_attestation_ref || !authority.bindings) {
    return { ok: false, code: "ADOPTION_AUTHORITY_INVALID", detail: "v2 binding fields missing" };
  }
  if (!OWNER_ATTESTATION_PREFIX.test(String(authority.owner_attestation_ref))) {
    return { ok: false, code: "ATTESTATION_REF_NOT_OWNER_HELD", detail: "owner_attestation_ref prefix" };
  }
  if (auditReceiptSha256 && authority.audit_receipt_sha256 !== auditReceiptSha256) {
    return { ok: false, code: "ADOPTION_AUTHORITY_INVALID", detail: "audit_receipt_sha256 mismatch" };
  }
  if (
    authority.bindings.candidate_digest !== authority.candidate_digest ||
    authority.bindings.audit_receipt_sha256 !== authority.audit_receipt_sha256 ||
    authority.bindings.owner_attestation_ref !== authority.owner_attestation_ref
  ) {
    return { ok: false, code: "ADOPTION_AUTHORITY_INVALID", detail: "bindings mismatch" };
  }
  if (candidateDigest && authority.candidate_digest !== candidateDigest) {
    return { ok: false, code: "ADOPTION_AUTHORITY_INVALID", detail: "candidate_digest mismatch" };
  }
  if (gapPath && authority.gap_path && authority.gap_path !== gapPath) {
    return { ok: false, code: "ADOPTION_AUTHORITY_INVALID", detail: "gap_path mismatch" };
  }
  if (manifest && authority.candidate_digest && authority.schema_version !== 2) {
    const expected = computeAdoptionCandidateDigest(manifest);
    if (authority.candidate_digest !== expected) {
      return { ok: false, code: "ADOPTION_AUTHORITY_INVALID", detail: "manifest drift" };
    }
  }
  if (repositoryHead && authority.repository_head && authority.repository_head !== repositoryHead) {
    return { ok: false, code: "ADOPTION_AUTHORITY_STALE", detail: "repository_head mismatch" };
  }
  const expectedContentHash = createHash("sha256")
    .update(JSON.stringify({ ...authority, content_hash: undefined }), "utf8")
    .digest("hex");
  if (authority.content_hash && authority.content_hash !== expectedContentHash) {
    return { ok: false, code: "ADOPTION_AUTHORITY_TAMPERED", detail: "content_hash mismatch" };
  }
  return { ok: true, legacy: false };
}

/**
 * Build immutable CURRENT-BYTE-ADOPTION-AUTHORITY artifact body.
 */
export function buildCurrentByteAdoptionAuthority({
  changeId,
  sliceId,
  gapPath,
  gapSha256,
  candidateDigest,
  manifest,
  repositoryHead,
  redIdentity,
  scaffoldIdentity,
  designIdentity,
  designReviewIdentity,
  auditReceiptSha256,
  ownerAttestationRef,
} = {}) {
  if (!auditReceiptSha256 || !ownerAttestationRef || !candidateDigest) {
    const error = new Error("ADOPTION_AUTHORITY_INVALID");
    error.code = "ADOPTION_AUTHORITY_INVALID";
    throw error;
  }
  if (!OWNER_ATTESTATION_PREFIX.test(String(ownerAttestationRef))) {
    const error = new Error("ATTESTATION_REF_NOT_OWNER_HELD");
    error.code = "ATTESTATION_REF_NOT_OWNER_HELD";
    throw error;
  }
  const body = {
    schema: "opsx.current_byte_adoption_authority",
    schema_version: 2,
    change_id: changeId,
    slice_id: sliceId,
    status: "CURRENT_BYTE_ADOPTION_AUTHORIZED",
    authorization_class: CURRENT_BYTE_OWNER_ADOPTION,
    candidate_digest: candidateDigest,
    audit_receipt_sha256: auditReceiptSha256,
    owner_attestation_ref: ownerAttestationRef,
    bindings: {
      candidate_digest: candidateDigest,
      audit_receipt_sha256: auditReceiptSha256,
      owner_attestation_ref: ownerAttestationRef,
    },
    gap_path: gapPath,
    gap_sha256: gapSha256,
    production_manifest: manifest?.production_manifest || [],
    live_frozen_test_content_identity: manifest?.live_frozen_test_content_identity || null,
    live_frozen_test_entries: manifest?.live_frozen_test_entries || [],
    planning_digest: manifest?.planning_digest || null,
    protected_context_digest: manifest?.protected_context_digest || null,
    owner_what_digest: manifest?.owner_what_digest || null,
    slice_digest: manifest?.slice_digest || null,
    design_review_status: manifest?.design_review_status || null,
    design_review_artifact_sha256: manifest?.design_review_artifact_sha256 || null,
    scaffold_candidate_digest_after: manifest?.scaffold_candidate_digest_after || null,
    scaffold_artifact_sha256: manifest?.scaffold_artifact_sha256 || null,
    red_identity: redIdentity || null,
    scaffold_identity: scaffoldIdentity || null,
    apply_design_identity: designIdentity || null,
    design_review_identity: designReviewIdentity || null,
    repository_head: repositoryHead || "unknown",
    generated_at: new Date().toISOString(),
  };
  body.content_hash = createHash("sha256")
    .update(JSON.stringify(body), "utf8")
    .digest("hex");
  return body;
}

/**
 * Build IMPLEMENTATION_ADOPTED receipt semantics (process contract only).
 */
export function buildImplementationAdoptedReceipt({
  changeId,
  sliceId,
  designIdentity,
  scaffoldIdentity,
  redIdentity,
  frozenTestContentIdentity,
  authorityPath,
  authorityDigest,
  auditReceiptSha256,
  ownerAttestationRef,
  otherEligibleSliceIds = [],
} = {}) {
  if (!auditReceiptSha256 || !ownerAttestationRef || !authorityDigest) {
    const error = new Error("ADOPTION_AUTHORITY_INVALID");
    error.code = "ADOPTION_AUTHORITY_INVALID";
    throw error;
  }
  return {
    schema: "opsx.apply_implement",
    schema_version: 1,
    change_id: changeId,
    slice_id: sliceId,
    status: IMPLEMENTATION_ADOPTED,
    branch: "ADOPTION",
    adoption_contract: ADOPTION_CONTRACT_ID,
    apply_design_identity: designIdentity,
    scaffold_identity: scaffoldIdentity,
    red_identity: redIdentity,
    current_byte_adoption_authority: {
      machine_path: authorityPath,
      candidate_digest: authorityDigest,
      audit_receipt_sha256: auditReceiptSha256,
      owner_attestation_ref: ownerAttestationRef,
      schema_version: 2,
    },
    frozen_test_content_identity: frozenTestContentIdentity,
    frozen_test_identity_before: frozenTestContentIdentity,
    frozen_test_identity_after: frozenTestContentIdentity,
    production_paths_modified: [],
    production_paths_created: [],
    product_files_modified: false,
    tests_modified: false,
    tests_executed: false,
    implementation_summary: "Current-byte adoption audit completed without product or test writes.",
    architectural_intent_satisfied: true,
    reuse_decisions_followed: [],
    invariants_preserved: [],
    chat_history_required: false,
    other_eligible_slice_ids: otherEligibleSliceIds,
    blockers: [],
    next_stage: "APPLY_GREEN",
    generated_at: new Date().toISOString(),
  };
}

function productionWriteCount(artifact) {
  const modified = Array.isArray(artifact.production_paths_modified)
    ? artifact.production_paths_modified
    : [];
  const created = Array.isArray(artifact.production_paths_created)
    ? artifact.production_paths_created
    : [];
  return modified.length + created.length;
}

/**
 * Reconcile IMPLEMENT freeze against on-disk RED frozen bytes (not receipt echo alone).
 */
function implementFrozenTestAuthorityErrors(artifact, repoRoot = DEFAULT_ROOT, design = null) {
  const errors = [];
  if (!isObject(artifact) || artifact.status !== IMPLEMENTATION_WRITTEN) {
    return errors;
  }
  let red = loadLinkedRed(repoRoot, artifact);
  if ((!red || !Array.isArray(red.test_paths)) && isObject(design) && artifact.slice_id) {
    const slice = (design.slices || []).find((s) => s.slice_id === artifact.slice_id);
    const test_paths = (slice?.red_test_targets_or_seams || []).filter(
      (p) => typeof p === "string" && /\.test\.(mjs|js|cjs|py)$/.test(p),
    );
    if (test_paths.length) {
      red = {
        ...(red || {}),
        ...(artifact.red_identity || {}),
        status: RED_PROVEN,
        slice_id: artifact.slice_id,
        tests_frozen: true,
        test_paths,
        fixture_paths: red?.fixture_paths || [],
      };
    }
  }
  if (!isObject(red) || red.status !== RED_PROVEN || !Array.isArray(red.test_paths)) {
    return errors;
  }
  const match = implementFrozenTestIdentityMatchesRedBytes(red, artifact, repoRoot);
  if (!match.ok) {
    errors.push({
      code: match.code || "FROZEN_BYTES_MISMATCH",
      message: "frozen_test_content_identity must match authoritative RED frozen bytes",
      detail: match,
    });
    return errors;
  }
  const legacy = match.legacy_digest;
  const before = artifact.frozen_test_identity_before;
  const after = artifact.frozen_test_identity_after;
  const accepted = new Set(
    [legacy, match.canonical_digest, artifact.frozen_test_content_identity].filter(Boolean),
  );
  if (before && !accepted.has(before)) {
    errors.push({
      code: "STALE_RED",
      message: "frozen_test_identity_before must match authoritative frozen bytes",
    });
  }
  if (after && !accepted.has(after)) {
    errors.push({
      code: "FROZEN_RED_MUTATED",
      message: "frozen_test_identity_after must match authoritative frozen bytes",
    });
  }
  return errors;
}

function semanticErrors(artifact) {
  const errors = [];
  if (!isObject(artifact)) return errors;
  if (FORBIDDEN_SUCCESS_STATUSES.includes(artifact.status)) {
    errors.push({
      code: "forbidden_status",
      message: "IMPLEMENT must not emit PASS/GREEN/VERIFIED terminals",
    });
  }
  if (artifact.chat_history_required !== false) {
    errors.push({
      code: "chat_history_required",
      message: "chat_history_required must be false",
    });
  }
  if (artifact.tests_executed !== false) {
    errors.push({
      code: "tests_executed",
      message: "tests_executed must be false (APPLY GREEN owns execution)",
    });
  }
  if (artifact.status === IMPLEMENTATION_WRITTEN) {
    const impactExpanded =
      artifact.impact_discovery_expanded?.status === IMPACT_DISCOVERY_EXPANDED;
    if (impactExpanded) {
      if (artifact.next_stage === "APPLY_GREEN") {
        errors.push({
          code: IMPACT_DISCOVERY_EXPANDED,
          message:
            "IMPACT_DISCOVERY_EXPANDED forbids next_stage=APPLY_GREEN until RED impact-expand overlay is proven",
        });
      }
    } else if (artifact.next_stage !== "APPLY_GREEN") {
      errors.push({
        code: "next_stage",
        message: "IMPLEMENTATION_WRITTEN requires next_stage=APPLY_GREEN",
      });
    }
    if (artifact.tests_modified !== false) {
      errors.push({
        code: "tests_modified",
        message: "IMPLEMENTATION_WRITTEN requires tests_modified=false",
      });
    }
    if (!artifact.scaffold_identity || artifact.scaffold_identity.status !== "SCAFFOLD_READY") {
      errors.push({
        code: "scaffold_identity",
        message: "IMPLEMENTATION_WRITTEN requires scaffold_identity with SCAFFOLD_READY",
      });
    }
    if (productionWriteCount(artifact) < 1) {
      errors.push({
        code: "production_paths",
        message: "IMPLEMENTATION_WRITTEN requires at least one production path",
      });
    }
    const frozen = artifact.frozen_test_content_identity;
    const before = artifact.frozen_test_identity_before;
    const after = artifact.frozen_test_identity_after;
    const match = frozenTestIdentityMatches({ frozen, before, after });
    if (!match.ok) {
      errors.push({
        code: match.code,
        message: "frozen RED test identity must match before and after writes",
      });
    }
    if (artifact.red_identity && frozen && artifact.red_identity.test_content_identity !== frozen) {
      errors.push({
        code: "STALE_RED",
        message: "frozen_test_content_identity must equal red_identity.test_content_identity",
      });
    }
    const written = [
      ...(artifact.production_paths_modified || []),
      ...(artifact.production_paths_created || []),
    ];
    if (written.some(looksLikeTestPath)) {
      errors.push({
        code: "tests_modified",
        message: "production path lists must not include test paths",
      });
    }
    const expected = artifact.expected_product_write_surfaces;
    if (Array.isArray(expected)) {
      const scope = classifyImplementationScope({
        expectedSurfaces: expected,
        writtenPaths: written,
        unexpectedButRequired: artifact.unexpected_but_required_paths,
      });
      if (!scope.ok) {
        errors.push({
          code: scope.code,
          message: `write surface expansion: ${(scope.paths || []).join(", ")}`,
        });
      }
    }
  }
  if (artifact.status === IMPLEMENTATION_ADOPTED) {
    if (artifact.branch !== "ADOPTION") {
      errors.push({
        code: "branch",
        message: "IMPLEMENTATION_ADOPTED requires branch=ADOPTION",
      });
    }
    if (artifact.next_stage !== "APPLY_GREEN") {
      errors.push({
        code: "next_stage",
        message: "IMPLEMENTATION_ADOPTED requires next_stage=APPLY_GREEN",
      });
    }
    if (artifact.product_files_modified !== false) {
      errors.push({
        code: "product_files_modified",
        message: "IMPLEMENTATION_ADOPTED requires product_files_modified=false",
      });
    }
    if (artifact.tests_modified !== false || artifact.tests_executed !== false) {
      errors.push({
        code: "tests_modified",
        message: "IMPLEMENTATION_ADOPTED must not modify or execute tests",
      });
    }
    if (productionWriteCount(artifact) > 0) {
      errors.push({
        code: "production_paths",
        message: "IMPLEMENTATION_ADOPTED must not record production writes",
      });
    }
    if (!artifact.current_byte_adoption_authority?.candidate_digest) {
      errors.push({
        code: "adoption_authority",
        message: "IMPLEMENTATION_ADOPTED requires current_byte_adoption_authority",
      });
    }
    if (isAdoptionV2Claim(artifact)) {
      const pointer = artifact.current_byte_adoption_authority || {};
      if (pointer.schema_version !== 2 || !pointer.audit_receipt_sha256 || !pointer.owner_attestation_ref) {
        errors.push({
          code: "ADOPTION_AUTHORITY_INVALID",
          message: "v2 IMPLEMENTATION_ADOPTED requires audit sha, owner ref, and schema_version 2",
        });
      } else if (!OWNER_ATTESTATION_PREFIX.test(String(pointer.owner_attestation_ref))) {
        errors.push({
          code: "ATTESTATION_REF_NOT_OWNER_HELD",
          message: "owner_attestation_ref prefix is not Owner-held",
        });
      }
    }
    if (
      artifact.red_identity?.status &&
      artifact.red_identity.status !== ALREADY_SATISFIED
    ) {
      errors.push({
        code: "red_identity",
        message: "IMPLEMENTATION_ADOPTED requires red_identity.status=ALREADY_SATISFIED",
      });
    }
  }
  if (artifact.status === "BLOCKED") {
    if (artifact.next_stage !== "NONE") {
      errors.push({
        code: "next_stage",
        message: "BLOCKED requires next_stage=NONE",
      });
    }
    const codes = (artifact.blockers || []).map((b) => b && b.code);
    if (codes.includes("FROZEN_RED_MUTATED") && artifact.tests_modified !== true) {
      errors.push({
        code: "tests_modified",
        message: "FROZEN_RED_MUTATED must record tests_modified=true",
      });
    }
    if (!codes.includes("FROZEN_RED_MUTATED") && artifact.tests_modified === true) {
      errors.push({
        code: "tests_modified",
        message: "tests_modified=true is only valid with FROZEN_RED_MUTATED",
      });
    }
  }
  return errors;
}

/**
 * Break Glass exception authority for IMPLEMENT without RED_PROVEN (fail-closed).
 */
export function evaluateBreakGlassImplementAuthority({
  exceptionReceipt,
  grant,
  redArtifact = null,
} = {}) {
  if (!exceptionReceipt || !grant) {
    return { ok: false, code: "BREAK_GLASS_AUTHORITY_MISSING" };
  }
  if (exceptionReceipt.grant_id !== grant.grant_id) {
    return { ok: false, code: "BREAK_GLASS_GRANT_MISMATCH" };
  }
  if (redArtifact?.status === RED_PROVEN) {
    return { ok: false, code: "FORBIDDEN_FAKE_RED", reason: "RED_PROVEN_PRESENT" };
  }
  if (!exceptionReceipt.red_debt_remains) {
    return { ok: false, code: "BREAK_GLASS_RED_DEBT_REQUIRED" };
  }
  return { ok: true, authority: "BREAK_GLASS_EXCEPTION", red_debt_remains: true };
}

export function validateApplyImplementArtifact({
  artifact,
  schema,
  design,
  repoRoot = DEFAULT_ROOT,
  artifactAbsPath = null,
}) {
  const errors = [];
  if (!isObject(artifact)) {
    return {
      ok: false,
      errors: [{ code: "type", message: "artifact must be an object" }],
    };
  }
  if (!isObject(schema)) {
    return {
      ok: false,
      errors: [{ code: "schema_missing", message: "schema missing" }],
    };
  }
  validate(artifact, schema, schema, "$", errors);
  for (const e of semanticErrors(artifact)) errors.push(e);
  for (const e of implementFrozenTestAuthorityErrors(artifact, repoRoot, design)) {
    errors.push(e);
  }
  if (artifact.status === IMPLEMENTATION_ADOPTED) {
    for (const e of implementationAdoptedTransitionErrors(artifact, repoRoot, {
      artifactAbsPath,
    })) {
      errors.push(e);
    }
  }
  if (isAdoptionV2Claim(artifact)) {
    for (const e of adoptionV2EnforcementErrors(artifact, repoRoot)) errors.push(e);
  }
  if (design && artifact.status === IMPLEMENTATION_WRITTEN) {
    const impact = detectImpactDiscoveryExpanded(artifact, design, repoRoot);
    if (impact.expanded && !artifact.impact_discovery_expanded) {
      errors.push({
        code: IMPACT_DISCOVERY_EXPANDED,
        message: `ACTUAL mutation matched extra seams: ${impact.extra_seam_ids.join(", ")}`,
      });
    }
  }
  const transitionAuthorized =
    artifact.status !== IMPLEMENTATION_ADOPTED ||
    implementationAdoptedTransitionAuthorized(artifact, repoRoot);
  return {
    ok: errors.length === 0,
    errors,
    status: artifact.status,
    transition_authorized: transitionAuthorized,
  };
}

export function loadApplyImplementSchema(repoRoot = DEFAULT_ROOT) {
  const schemaPath = resourcePath(repoRoot, SCHEMA_REL);
  if (!existsSync(schemaPath)) return null;
  return JSON.parse(readFileSync(schemaPath, "utf8"));
}

function adoptionV2EnforcementErrors(artifact, repoRoot) {
  const errors = [];
  const auditPath = artifact.audit_receipt_path;
  const authorityPath = artifact.current_byte_adoption_authority?.machine_path;
  if (!auditPath || !authorityPath) {
    errors.push({
      code: "ADOPTION_AUDIT_MISSING",
      message: "v2 adoption claim requires audit and authority paths",
    });
    return errors;
  }
  const auditAbs = resourcePath(repoRoot, auditPath);
  const authorityAbs = resourcePath(repoRoot, authorityPath);
  if (!existsSync(auditAbs) || !existsSync(authorityAbs)) {
    errors.push({
      code: "ADOPTION_AUDIT_MISSING",
      message: "audit or authority file is missing",
    });
    return errors;
  }
  const audit = JSON.parse(readFileSync(auditAbs, "utf8"));
  const authority = JSON.parse(readFileSync(authorityAbs, "utf8"));
  const check = runAdoptionAuthorityCheck({
    audit,
    authority,
    implementation: artifact,
    red_status: artifact.red_identity?.status || "ALREADY_SATISFIED",
  });
  if (!check.implementation_adopted_authorized) {
    const hard = (check.findings || []).find((item) => item.severity === "HARD");
    errors.push({
      code: hard?.code || "ADOPTION_AUTHORITY_INVALID",
      message: hard?.evidence || "adoption authority check failed",
    });
  }
  return errors;
}

/**
 * Build authority v2 and IMPLEMENTATION_ADOPTED only after the checker permits it.
 * Does not write files.
 */
export function adoptFromCheckedBundle(bundle) {
  const first = runAdoptionAuthorityCheck({
    ...bundle,
    authority: null,
    implementation: null,
  });
  if (!first.authority_permitted) {
    return { ok: false, check: first, authority: null, implementation: null };
  }
  let authority = null;
  let implementation = null;
  try {
    authority = buildCurrentByteAdoptionAuthority({
      changeId: bundle.change_id,
      sliceId: bundle.slice_id,
      gapPath: bundle.gap_path || null,
      candidateDigest: first.candidate_digest,
      auditReceiptSha256: first.audit_receipt_sha256,
      ownerAttestationRef: bundle.owner_attestation_ref,
      repositoryHead: bundle.repository_head,
    });
    implementation = buildImplementationAdoptedReceipt({
      changeId: bundle.change_id,
      sliceId: bundle.slice_id,
      redIdentity: { status: "ALREADY_SATISFIED" },
      authorityPath: bundle.authority_path || null,
      authorityDigest: first.candidate_digest,
      auditReceiptSha256: first.audit_receipt_sha256,
      ownerAttestationRef: bundle.owner_attestation_ref,
    });
  } catch (error) {
    return {
      ok: false,
      check: first,
      authority: null,
      implementation: null,
      error: error.code || "ADOPTION_AUTHORITY_INVALID",
    };
  }
  const second = runAdoptionAuthorityCheck({
    ...bundle,
    authority,
    implementation,
  });
  if (!second.implementation_adopted_authorized) {
    return { ok: false, check: second, authority: null, implementation: null };
  }
  return { ok: true, check: second, authority, implementation };
}

function main(argv) {
  const args = [...argv];
  let repoRoot = DEFAULT_ROOT;
  const adoptFlag = args.indexOf("--adopt-bundle");
  if (adoptFlag >= 0) {
    const bundlePath = args[adoptFlag + 1];
    if (!bundlePath || !existsSync(bundlePath)) {
      process.stdout.write(
        JSON.stringify({ ok: false, errors: [{ code: "missing_path" }] }) + "\n",
      );
      process.exit(1);
    }
    const bundle = JSON.parse(readFileSync(bundlePath, "utf8"));
    const result = adoptFromCheckedBundle(bundle);
    process.stdout.write(JSON.stringify(result) + "\n");
    process.exit(result.ok ? 0 : 1);
  }
  const repoFlag = args.indexOf("--repo");
  if (repoFlag >= 0) {
    repoRoot = resolve(args[repoFlag + 1]);
    args.splice(repoFlag, 2);
  }
  let schemaPath;
  const schemaFlag = args.indexOf("--schema");
  if (schemaFlag >= 0) {
    schemaPath = resolve(args[schemaFlag + 1]);
    args.splice(schemaFlag, 2);
  } else {
    schemaPath = resourcePath(repoRoot, SCHEMA_REL);
  }
  const artifactPath = args[0];
  if (!artifactPath || !existsSync(schemaPath) || !existsSync(artifactPath)) {
    process.stdout.write(
      JSON.stringify({ status: "FAIL", errors: [{ code: "missing_path" }] }) + "\n",
    );
    process.exit(1);
  }
  const schema = JSON.parse(readFileSync(schemaPath, "utf8"));
  const artifact = JSON.parse(readFileSync(artifactPath, "utf8"));
  const design = loadLinkedDesign(repoRoot, artifact);
  const result = validateApplyImplementArtifact({
    artifact,
    schema,
    design,
    repoRoot,
    artifactAbsPath: resolve(artifactPath),
  });
  process.stdout.write(
    JSON.stringify(
      {
        status: result.ok ? "PASS" : "FAIL",
        artifact_status: artifact.status,
        transition_authorized: result.transition_authorized === true,
        error_count: result.errors.length,
        errors: result.errors,
      },
      null,
      2,
    ) + "\n",
  );
  process.exit(result.ok ? 0 : 1);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2));
}
