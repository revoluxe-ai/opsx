import { defaultRepoRootFromToolFile as opsxDefaultProjectRoot } from "./opsx-repo-root.mjs";
import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Split-APPLY pipeline identity and freshness helpers (V2).
 *
 * Shared by /opsx-apply-design, -scaffold, -red, -implement, and -green.
 * Does not implement a cache, scheduler, or Temporal runtime.
 *
 * Freshness precedence:
 *   1. Frozen planning identity — strict
 *   2. Protected PRODUCT architectural/repository context — strict
 *      (OPSX process-contract paths are provenance, not this digest)
 *   3. Pipeline-mutable candidate surfaces — predecessor receipt chain
 *   4. Frozen RED tests after RED_PROVEN — immutable
 *   5. GREEN_PROVEN receipt — current only while upstream semantic identities
 *      match (DESIGN / SCAFFOLD / RED / IMPLEMENT / frozen tests). Receipt-file
 *      `artifact_sha256` envelope churn from rebind/reformat alone MUST NOT
 *      rewind GREEN (self-invalidating loop). Genuine semantic drift still
 *      yields STALE_GREEN. Callers MAY pass on-disk artifact sha256 for
 *      diagnostics; freshness authority is semantic-first.
 *   6. Unrelated worktree dirt — ignored
 *   7. Unreceipted drift on pipeline-owned surfaces — fail closed
 */
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { implementationAdoptedTransitionAuthorized } from "./opsx-adoption-transition-gate.mjs";
import { getRedOperativeTransitionAuthorized } from "./opsx-apply-red-bindings.mjs";
import { evaluateRedTransitionGate } from "./opsx-apply-red-transition-gate.mjs";
import { computeFrozenTestContentIdentity } from "./opsx-frozen-test-content-identity.mjs";

export const PIPELINE_VERSION = "design-review-scaffold-red-implement-green";
export const SCAFFOLD_READY = "SCAFFOLD_READY";
export const RED_PROVEN = "RED_PROVEN";
export const ALREADY_SATISFIED = "ALREADY_SATISFIED";
export const IMPLEMENTATION_WRITTEN = "IMPLEMENTATION_WRITTEN";
export const IMPLEMENTATION_ADOPTED = "IMPLEMENTATION_ADOPTED";
export const GREEN_PROVEN = "GREEN_PROVEN";
export const NORMAL_PIPELINE_BRANCH = "NORMAL";
export const ADOPTION_PIPELINE_BRANCH = "ADOPTION";
/** ALREADY_SATISFIED observation + реальная запись IMPLEMENTATION_WRITTEN (не adoption). */
export const ALREADY_SATISFIED_WRITE_PIPELINE_BRANCH = "ALREADY_SATISFIED_WRITE";
export const ADOPTION_EVIDENCE_GAP = "ADOPTION_EVIDENCE_GAP";
export const CURRENT_BYTE_OWNER_ADOPTION = "CURRENT_BYTE_OWNER_ADOPTION";
export const AUTHORIZE_CURRENT_BYTE_ADOPTION = "AUTHORIZE_CURRENT_BYTE_ADOPTION";
export const APPLY_SCAFFOLD = "APPLY_SCAFFOLD";
export const APPLY_DESIGN_REVIEW = "APPLY_DESIGN_REVIEW";
export const RECOVERY_PAUSED = "RECOVERY_PAUSED";
export const APPLY_DESIGN_REVIEW_PASS = "APPLY_DESIGN_REVIEW_PASS";
export const REPAIRS_REQUIRED = "REPAIRS_REQUIRED";
/** Review receipt no longer matches canonical APPLY-DESIGN identity on disk. */
export const STALE_APPLY_DESIGN_REVIEW = "STALE_APPLY_DESIGN_REVIEW";
export const SCAFFOLD_NOT_IMPLEMENTED = "OPSX_SCAFFOLD_NOT_IMPLEMENTED";
export const APPLY_RED_SCHEMA_VERSION = 2;
export const RED_EVIDENCE_CONTRACT = "per_command_v1";
export const RED_PROVEN_CLASSIFICATION = "INTENDED_BEHAVIOR_MISSING";
/** Live re-fail gate for APPLY_RED / pre-IMPLEMENT operative authority. */
export const RED_VALIDATION_PHASE_OPERATIVE = "operative_pre_implement";
/** Receipt-bound RED authority after IMPLEMENTATION_WRITTEN (no live re-fail). */
export const RED_VALIDATION_PHASE_HISTORICAL = "historical_bound";

export const FORBIDDEN_SCAFFOLD_BEHAVIOR = [
  "dedupe",
  "retry",
  "authorization",
  "session_policy",
  "persistence",
  "api_orchestration",
  "business_branching",
  "cache_merge",
  "error_recovery",
  "state_machine",
  "debounce",
  "user_visible_behavior",
  "cross_device_semantics",
];

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

export function posixPath(p) {
  return String(p || "").replace(/\\/g, "/");
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

/**
 * Shared digest encoding over `{path, sha256}` entries (sorted path + sha256
 * lines). NOT a file-content hasher.
 *
 * Forbidden misuse (incident cross-device-novel-save-refresh):
 *   identityDigest(readFileSync(path))  // Buffer → bogus planning_digest
 * Use fileSha256 for raw file bytes, then freezePlanningIdentity /
 * computeCurrentPlanningIdentity for planning digests.
 */
export function identityDigest(entries) {
  if (Buffer.isBuffer(entries) || ArrayBuffer.isView(entries)) {
    throw new TypeError(
      "identityDigest expects [{path, sha256}, ...]; hash file bytes with fileSha256, then freezePlanningIdentity / computeCurrentPlanningIdentity",
    );
  }
  if (entries != null && !Array.isArray(entries)) {
    throw new TypeError(
      "identityDigest expects an array of {path, sha256} entries",
    );
  }
  for (const e of entries || []) {
    if (typeof e === "number") {
      throw new TypeError(
        "identityDigest received numeric entries (likely a Buffer spread); use fileSha256 for content hashes",
      );
    }
  }
  const lines = [...(entries || [])]
    .map((e) => ({
      path: posixPath(e.path || e),
      sha256: String(e.sha256 || ""),
    }))
    .filter((e) => e.path)
    .sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0))
    .map((e) => `path=${e.path}\0sha256=${e.sha256}\n`)
    .join("");
  return createHash("sha256").update(lines, "utf8").digest("hex");
}

/**
 * Normalize planning input rows used by APPLY DESIGN freeze + freshness.
 * sha256 MUST be raw-byte file digests (fileSha256), never identityDigest(bytes).
 */
export function normalizePlanningInputEntries(inputs) {
  return [...(inputs || [])]
    .map((e) => {
      const path = posixPath(e?.path || "");
      if (!path) return null;
      const out = {
        path,
        sha256: String(e.sha256 || ""),
      };
      if (e.role != null) out.role = e.role;
      return out;
    })
    .filter(Boolean);
}

/**
 * Canonical freeze of planning identity (exactly what APPLY DESIGN writes).
 * freezePlanningIdentity(inputs).planning_digest === identityDigest(normalized inputs)
 * and MUST equal computeCurrentPlanningIdentity for identical on-disk bytes.
 */
export function freezePlanningIdentity({ change_root, inputs, ...rest } = {}) {
  const normalized = normalizePlanningInputEntries(inputs);
  const out = {
    ...rest,
    inputs: normalized,
    planning_digest: identityDigest(normalized),
  };
  if (change_root != null) out.change_root = posixPath(change_root);
  return out;
}

/**
 * Recompute current planning identity from disk with the SAME algorithm as
 * freezePlanningIdentity. Reads raw file bytes via fileSha256 only.
 */
export function computeCurrentPlanningIdentity({
  repoRoot,
  planningIdentity,
  inputs,
} = {}) {
  if (!repoRoot) {
    throw new TypeError("computeCurrentPlanningIdentity requires repoRoot");
  }
  const source = inputs || planningIdentity?.inputs || [];
  const live = source.map((e) => {
    const path = posixPath(e.path);
    const abs = resourcePath(repoRoot, path);
    const row = { path, sha256: "" };
    if (e.role != null) row.role = e.role;
    if (existsSync(abs)) row.sha256 = fileSha256(abs);
    return row;
  });
  return freezePlanningIdentity({
    change_root: planningIdentity?.change_root,
    inputs: live,
  });
}

/**
 * Compare frozen APPLY DESIGN planning_identity to current disk bytes.
 * This is the only supported way to derive a "live" planning_digest.
 */
export function classifyApplyDesignPlanningFreshness(design, { repoRoot } = {}) {
  const frozen = design?.planning_identity;
  if (!isObject(frozen) || !Array.isArray(frozen.inputs) || !frozen.planning_digest) {
    return {
      ok: false,
      code: "STALE_OR_INVALID_APPLY_DESIGN",
      frozen_digest: frozen?.planning_digest || null,
      current_digest: null,
      current: null,
    };
  }
  if (!repoRoot) {
    throw new TypeError("classifyApplyDesignPlanningFreshness requires repoRoot");
  }
  const current = computeCurrentPlanningIdentity({
    repoRoot,
    planningIdentity: frozen,
  });
  if (current.planning_digest === frozen.planning_digest) {
    return {
      ok: true,
      code: null,
      frozen_digest: frozen.planning_digest,
      current_digest: current.planning_digest,
      current,
    };
  }
  return {
    ok: false,
    code: "STALE_APPLY_DESIGN",
    frozen_digest: frozen.planning_digest,
    current_digest: current.planning_digest,
    current,
  };
}

/**
 * Disk-backed DESIGN authority check: planning via canonical API + optional
 * protected-context digest (caller-supplied or recomputed from declared paths).
 */
export function evaluateApplyDesignFreshnessFromDisk(design, {
  repoRoot,
  protectedContextDigest,
} = {}) {
  if (!repoRoot) {
    throw new TypeError("evaluateApplyDesignFreshnessFromDisk requires repoRoot");
  }
  let protectedDigest = protectedContextDigest;
  if (protectedDigest == null) {
    const paths = design?.protected_context_identity?.paths;
    if (Array.isArray(paths) && paths.length) {
      const map = hashRepoRelativePaths(repoRoot, paths);
      protectedDigest = identityDigest(
        paths.map((p) => ({ path: posixPath(p), sha256: map[posixPath(p)] || "" })),
      );
    }
  }
  return isApplyDesignAuthorityFresh(design, {
    repoRoot,
    protectedContextDigest: protectedDigest,
  });
}

export function isPathCoveredBy(path, surfaces) {
  const p = posixPath(path);
  if (!p) return false;
  return (surfaces || []).some((raw) => {
    const s = posixPath(raw);
    if (!s) return false;
    if (p === s) return true;
    if (s.endsWith("/")) return p.startsWith(s);
    return p.startsWith(`${s}/`);
  });
}

/**
 * Union of surfaces DESIGN authorizes later stages to mutate.
 */
export function collectPipelineMutablePaths(design) {
  const paths = new Set();
  for (const p of design?.pipeline_mutable_paths || []) {
    if (p) paths.add(posixPath(p));
  }
  for (const slice of design?.slices || []) {
    for (const p of slice.expected_product_write_surfaces || []) {
      if (p) paths.add(posixPath(p));
    }
    for (const p of slice.red_test_targets_or_seams || []) {
      if (p && looksLikeTestPath(p)) paths.add(posixPath(p));
    }
    const sc = slice.scaffold_contract;
    for (const surf of sc?.required_surfaces || []) {
      if (surf.path) paths.add(posixPath(surf.path));
      for (const p of surf.allowed_scaffold_writes || []) {
        if (p) paths.add(posixPath(p));
      }
    }
  }
  return [...paths].sort();
}

/**
 * Narrow classifier: OPSX split-APPLY pipeline process contracts.
 * Product architecture decisions (novel-save-sot-api, auth-client-sot-api, …)
 * are NOT process contracts. Do not treat all of openspec/decisions/** as process.
 */
export function isOpsxProcessContractPath(path) {
  const p = posixPath(path);
  if (!p) return false;
  if (/^openspec\/decisions\/opsx-apply-/.test(p)) return true;
  if (
    /^opsx\/registry\/apply_(design|scaffold|red|implement|green)\.schema\.json$/.test(
      p,
    )
  ) {
    return true;
  }
  if (/^opsx\/tools\/opsx-apply-/.test(p)) return true;
  if (/^\.cursor\/commands\/opsx-apply/.test(p)) return true;
  return false;
}

function productProtectedCandidate(path, mutable) {
  const p = posixPath(path);
  if (!p) return false;
  if (isOpsxProcessContractPath(p)) return false;
  if (isPathCoveredBy(p, mutable)) return false;
  return true;
}

/**
 * Inspected DESIGN paths that downstream stages are NOT authorized to mutate
 * AND whose byte drift can invalidate PRODUCT implementation design.
 * OPSX process-contract paths are excluded even if DESIGN inspected them.
 */
export function collectProtectedContextPaths(design) {
  const mutable = collectPipelineMutablePaths(design);
  const inspected = (design?.repository_identity?.inspected_paths || []).map(posixPath);
  const declared = (design?.protected_context_identity?.paths || []).map(posixPath);
  const fromInspected = inspected.filter((p) => productProtectedCandidate(p, mutable));
  const fromDeclared = declared.filter((p) => productProtectedCandidate(p, mutable));
  return [...new Set([...fromInspected, ...fromDeclared])].sort();
}

/**
 * Process-contract paths DESIGN consulted (command semantics / pipeline identity).
 * Provenance only — byte drift MUST NOT produce PROTECTED_CONTEXT_DRIFT.
 */
export function collectProcessContractPaths(design) {
  const inspected = (design?.repository_identity?.inspected_paths || []).map(posixPath);
  const declaredProtected = (design?.protected_context_identity?.paths || []).map(
    posixPath,
  );
  const declaredProcess = (design?.process_contract_identity?.paths || []).map(
    posixPath,
  );
  const merged = new Set(
    [...inspected, ...declaredProtected, ...declaredProcess].filter(
      (p) => p && isOpsxProcessContractPath(p),
    ),
  );
  return [...merged].sort();
}

export function deriveProtectedContextIdentity(design, pathSha256 = {}) {
  const paths = collectProtectedContextPaths(design);
  const entries = paths.map((path) => ({
    path,
    sha256: pathSha256[path] || "",
  }));
  return {
    paths,
    content_digest: identityDigest(entries),
  };
}

export function deriveProcessContractIdentity(design, pathSha256 = {}) {
  const paths = collectProcessContractPaths(design);
  const entries = paths.map((path) => ({
    path,
    sha256: pathSha256[path] || "",
  }));
  return {
    paths,
    content_digest: identityDigest(entries),
  };
}

/** Architectural slice identity (not process/provenance hashes). */
export function sliceSemanticDigest(design) {
  const slices = [...(design?.slices || [])]
    .map((s) => ({
      slice_id: s.slice_id,
      title: s.title,
      responsibility: s.responsibility,
      source_task_ids: s.source_task_ids,
      expected_product_write_surfaces: s.expected_product_write_surfaces,
      red_test_targets_or_seams: s.red_test_targets_or_seams,
      required_behavior: s.required_behavior,
      invariants: s.invariants,
      dependencies_on_prior_slices: s.dependencies_on_prior_slices,
      scaffold_contract: s.scaffold_contract,
    }))
    .sort((a, b) => String(a.slice_id).localeCompare(String(b.slice_id)));
  return createHash("sha256").update(JSON.stringify(slices), "utf8").digest("hex");
}

/**
 * Classify a DESIGN identity rewrite. PROCESS_CONTRACT_ONLY means planning,
 * product protected path set, and slice semantics are unchanged — downstream
 * GREEN evidence may be rebound rather than re-executed.
 */
export function classifyDesignIdentityDelta(previous, next) {
  const prevPlan = previous?.planning_identity?.planning_digest;
  const nextPlan = next?.planning_identity?.planning_digest;
  if (prevPlan && nextPlan && prevPlan !== nextPlan) {
    return { kind: "PLANNING", rebind_safe: false };
  }
  if (sliceSemanticDigest(previous) !== sliceSemanticDigest(next)) {
    return { kind: "SLICE_SEMANTIC", rebind_safe: false };
  }
  const prevProduct = collectProtectedContextPaths(previous);
  const nextProduct = collectProtectedContextPaths(next);
  if (JSON.stringify(prevProduct) !== JSON.stringify(nextProduct)) {
    return { kind: "PRODUCT_ARCHITECTURE", rebind_safe: false };
  }
  return { kind: "PROCESS_CONTRACT_ONLY", rebind_safe: true };
}

export function refreshApplyDesignProtectedContext(artifact, pathSha256 = {}) {
  const previous = artifact;
  const next = {
    ...artifact,
    protected_context_identity: deriveProtectedContextIdentity(artifact, pathSha256),
    process_contract_identity: deriveProcessContractIdentity(artifact, pathSha256),
  };
  return { artifact: next, delta: classifyDesignIdentityDelta(previous, next) };
}

export function rebindReceiptApplyDesignIdentity(receipt, newDesign, newDesignSha256) {
  if (!isObject(receipt) || !isObject(receipt.apply_design_identity)) {
    return receipt;
  }
  return {
    ...receipt,
    apply_design_identity: {
      ...receipt.apply_design_identity,
      planning_digest: newDesign.planning_identity?.planning_digest,
      inspected_digest: newDesign.repository_identity?.inspected_digest,
      protected_context_digest: newDesign.protected_context_identity?.content_digest,
      artifact_sha256: newDesignSha256,
    },
  };
}

export function graphValid(design) {
  const slices = Array.isArray(design?.slices) ? design.slices : [];
  const order = design?.slice_order;
  if (!isObject(order) || !Array.isArray(order.nodes) || order.kind !== "dependency_graph") {
    return false;
  }
  const byId = new Map(slices.map((s) => [s.slice_id, s]));
  if (order.nodes.length !== slices.length) return false;
  for (const id of order.nodes) {
    if (!byId.has(id)) return false;
  }
  for (const edge of order.edges || []) {
    if (!byId.has(edge.from) || !byId.has(edge.to)) return false;
  }
  for (const s of slices) {
    for (const dep of s.dependencies_on_prior_slices || []) {
      if (!byId.has(dep)) return false;
    }
  }
  return true;
}

function statusIs(map, id, status) {
  const rec = map && map[id];
  return Boolean(rec && rec.status === status);
}

function staleGreen(mismatched, detail = undefined) {
  const out = { current: false, code: "STALE_GREEN", mismatched };
  if (detail !== undefined) out.detail = detail;
  return out;
}

/**
 * Semantic (not receipt-file envelope) match for GREEN-bound apply_design_identity.
 */
export function greenApplyDesignIdentitySemanticMatch(adi, design) {
  if (!isObject(adi)) return true;
  if (!isObject(design)) return true;
  if (design.status && adi.status && design.status !== adi.status) return false;
  const plan = design.planning_identity?.planning_digest;
  if (plan && adi.planning_digest && plan !== adi.planning_digest) return false;
  const pci = design.protected_context_identity?.content_digest;
  if (pci && adi.protected_context_digest && pci !== adi.protected_context_digest) {
    return false;
  }
  return true;
}

/** Semantic match for GREEN-bound scaffold_identity. */
export function greenScaffoldIdentitySemanticMatch(si, scaffold) {
  if (!isObject(si)) return true;
  if (!isObject(scaffold)) return true;
  if (scaffold.status && si.status && scaffold.status !== si.status) return false;
  if (
    scaffold.candidate_digest_after &&
    si.candidate_digest_after &&
    scaffold.candidate_digest_after !== si.candidate_digest_after
  ) {
    return false;
  }
  if (si.slice_id && scaffold.slice_id && si.slice_id !== scaffold.slice_id) {
    return false;
  }
  return true;
}

/**
 * Сопоставить declared frozen digest в GREEN/RED с live canonical: legacy concat
 * и path-sha эквивалентны, если текущие байты дают тот же canonical, что и live.
 * Поддельные digest и реальная мутация тестов по-прежнему fail-closed.
 */
export function greenBoundFrozenTestIdentityMatchesLive(
  declaredDigest,
  red,
  repoRoot,
  liveCanonicalDigest,
) {
  if (!declaredDigest) return true;
  if (!liveCanonicalDigest) return false;
  if (declaredDigest === liveCanonicalDigest) return true;
  if (!repoRoot || !isObject(red)) return false;
  const resolved = resolveDeclaredFrozenTestContentIdentity(red, repoRoot);
  if (!resolved.ok || !resolved.canonical) return false;
  if (liveCanonicalDigest !== resolved.canonical) return false;
  return (resolved.accepted_digests || []).includes(declaredDigest);
}

/** Semantic match for GREEN-bound red_identity (live frozen tests are authoritative). */
export function greenRedIdentitySemanticMatch(ri, red, {
  frozenTestContentIdentity,
  liveFrozenTestContentIdentity,
  implement,
  repoRoot = null,
} = {}) {
  if (!isObject(ri)) return true;
  if (!isObject(red)) return true;
  const liveFrozen =
    liveFrozenTestContentIdentity ||
    frozenTestContentIdentity ||
    red?.test_content_identity ||
    implement?.frozen_test_content_identity;
  if (red.status && ri.status && red.status !== ri.status) {
    // Поздний ALREADY_SATISFIED не отменяет GREEN, если байты исторического
    // набора по-прежнему дают записанный canonical/legacy digest.
    const historicalStillBound =
      implementLinksHistoricalRedProven(implement) &&
      ri.status === RED_PROVEN &&
      ri.test_content_identity &&
      implement?.frozen_test_content_identity === ri.test_content_identity &&
      greenBoundFrozenTestIdentityMatchesLive(
        ri.test_content_identity,
        red,
        repoRoot,
        liveFrozen,
      );
    if (!historicalStillBound) return false;
  }
  if (
    ri.test_content_identity &&
    liveFrozen &&
    !greenBoundFrozenTestIdentityMatchesLive(
      ri.test_content_identity,
      red,
      repoRoot,
      liveFrozen,
    )
  ) {
    return false;
  }
  if (ri.slice_id && red.slice_id && ri.slice_id !== red.slice_id) return false;
  return true;
}

/** Semantic match for GREEN-bound implementation_identity. */
export function greenImplementIdentitySemanticMatch(ii, implement) {
  if (!isObject(ii)) return true;
  if (!isObject(implement)) return true;
  if (implement.status && ii.status && implement.status !== ii.status) return false;
  if (ii.slice_id && implement.slice_id && ii.slice_id !== implement.slice_id) {
    return false;
  }
  if (
    implement.frozen_test_content_identity &&
    ii.frozen_test_content_identity &&
    implement.frozen_test_content_identity !== ii.frozen_test_content_identity
  ) {
    return false;
  }
  return true;
}

/**
 * Split upstream identity checks for diagnostics: semantic authority vs
 * receipt-file envelope (`artifact_sha256`). Envelope-only mismatch is
 * rebind-safe when semantic checks pass.
 */
export function evaluateGreenUpstreamIdentityChecks(green, upstream = {}) {
  const {
    design,
    scaffold,
    red,
    implement,
    designArtifactSha256,
    scaffoldArtifactSha256,
    redArtifactSha256,
    implementArtifactSha256,
    frozenTestContentIdentity,
    liveFrozenTestContentIdentity,
    repoRoot = null,
  } = upstream;
  const semanticChecks = {
    planning: greenApplyDesignIdentitySemanticMatch(green?.apply_design_identity, design),
    protected: greenApplyDesignIdentitySemanticMatch(green?.apply_design_identity, design),
    scaffold_candidate: greenScaffoldIdentitySemanticMatch(
      green?.scaffold_identity,
      scaffold,
    ),
    red_test: greenRedIdentitySemanticMatch(green?.red_identity, red, {
      frozenTestContentIdentity,
      liveFrozenTestContentIdentity,
      implement,
      repoRoot,
    }),
    impl_frozen: greenImplementIdentitySemanticMatch(
      green?.implementation_identity,
      implement,
    ),
  };
  const designSha = designArtifactSha256 || design?.artifact_sha256;
  const scaffoldSha = scaffoldArtifactSha256 || scaffold?.artifact_sha256;
  const redSha = redArtifactSha256 || red?.artifact_sha256;
  const implSha = implementArtifactSha256 || implement?.artifact_sha256;
  const fileChecks = {
    design_file:
      !green?.apply_design_identity?.artifact_sha256 ||
      !designSha ||
      green.apply_design_identity.artifact_sha256 === designSha,
    scaffold_file:
      !green?.scaffold_identity?.artifact_sha256 ||
      !scaffoldSha ||
      green.scaffold_identity.artifact_sha256 === scaffoldSha,
    red_file:
      !green?.red_identity?.artifact_sha256 ||
      !redSha ||
      green.red_identity.artifact_sha256 === redSha,
    impl_file:
      !green?.implementation_identity?.artifact_sha256 ||
      !implSha ||
      green.implementation_identity.artifact_sha256 === implSha,
  };
  return { semanticChecks, fileChecks };
}

/**
 * GREEN_PROVEN is durable status, not validity. A receipt is current only
 * when it still matches the upstream identities it proved.
 *
 * Status-only graph stubs (`{ status: "GREEN_PROVEN" }`) remain current for
 * unit selection fixtures. Real receipts with nested *_identity MUST be
 * checked against current DESIGN / SCAFFOLD / RED / IMPLEMENT / frozen tests.
 *
 * Classification: STALE_GREEN (primary). Callers may surface the mismatched
 * field via `mismatched`.
 */
export function classifyGreenEvidenceFreshness(green, {
  design,
  scaffold,
  red,
  implement,
  designArtifactSha256,
  scaffoldArtifactSha256,
  redArtifactSha256,
  implementArtifactSha256,
  frozenTestContentIdentity,
  liveFrozenTestContentIdentity,
  boundedVerifyBinding,
  repoRoot = DEFAULT_REPO_ROOT,
} = {}) {
  if (!isObject(green) || green.status !== GREEN_PROVEN) {
    return { current: false, code: "NOT_GREEN", mismatched: null };
  }
  const hasNestedIdentity =
    isObject(green.apply_design_identity) ||
    isObject(green.scaffold_identity) ||
    isObject(green.red_identity) ||
    isObject(green.implementation_identity) ||
    typeof green.frozen_test_content_identity === "string";
  if (!hasNestedIdentity) {
    const stubBinding = classifyGreenBoundedVerifyBinding(green, boundedVerifyBinding);
    if (!stubBinding.ok) {
      return {
        current: false,
        code: stubBinding.code || "STALE_GREEN",
        mismatched: stubBinding.mismatched || "bounded_verify_binding",
      };
    }
    return { current: true, code: null, mismatched: null, stub: true };
  }

  const frozenCtx = {
    frozenTestContentIdentity,
    liveFrozenTestContentIdentity,
    implement,
    repoRoot,
  };

  if (
    isObject(green.apply_design_identity) &&
    !greenApplyDesignIdentitySemanticMatch(green.apply_design_identity, design)
  ) {
    return staleGreen("apply_design_identity");
  }

  if (
    isObject(green.scaffold_identity) &&
    !greenScaffoldIdentitySemanticMatch(green.scaffold_identity, scaffold)
  ) {
    return staleGreen("scaffold_identity");
  }

  if (
    isObject(green.red_identity) &&
    !greenRedIdentitySemanticMatch(green.red_identity, red, frozenCtx)
  ) {
    return staleGreen("red_identity");
  }

  if (
    isObject(green.implementation_identity) &&
    !greenImplementIdentitySemanticMatch(green.implementation_identity, implement)
  ) {
    return staleGreen("implementation_identity");
  }

  // Receipt-file envelope: only when the live receipt object is absent and we
  // cannot evaluate semantic fields (fail closed on missing upstream).
  if (isObject(green.apply_design_identity) && !isObject(design)) {
    const designSha = designArtifactSha256;
    const adi = green.apply_design_identity;
    if (designSha && adi.artifact_sha256 && designSha !== adi.artifact_sha256) {
      return staleGreen("apply_design_identity");
    }
  }
  if (isObject(green.scaffold_identity) && !isObject(scaffold)) {
    const scaffoldSha = scaffoldArtifactSha256;
    const si = green.scaffold_identity;
    if (scaffoldSha && si.artifact_sha256 && scaffoldSha !== si.artifact_sha256) {
      return staleGreen("scaffold_identity");
    }
  }
  if (isObject(green.red_identity) && !isObject(red)) {
    const redSha = redArtifactSha256;
    const ri = green.red_identity;
    if (redSha && ri.artifact_sha256 && redSha !== ri.artifact_sha256) {
      return staleGreen("red_identity");
    }
  }
  if (isObject(green.implementation_identity) && !isObject(implement)) {
    const implSha = implementArtifactSha256;
    const ii = green.implementation_identity;
    if (ii.artifact_sha256) {
      if (!implSha) {
        return staleGreen(
          "implementation_identity",
          "missing_current_implementation_artifact_sha256",
        );
      }
      if (implSha !== ii.artifact_sha256) {
        return staleGreen("implementation_identity");
      }
    }
  }

  const frozen =
    liveFrozenTestContentIdentity ||
    frozenTestContentIdentity ||
    red?.test_content_identity ||
    implement?.frozen_test_content_identity;
  if (typeof green.frozen_test_content_identity === "string" && frozen) {
    if (
      !greenBoundFrozenTestIdentityMatchesLive(
        green.frozen_test_content_identity,
        red,
        repoRoot,
        frozen,
      )
    ) {
      return { current: false, code: "STALE_GREEN", mismatched: "frozen_test_content_identity" };
    }
  }

  if (typeof green.predecessor_branch === "string") {
    const pairing = classifyGreenPredecessorPairing({
      red,
      implement,
      green,
      repoRoot,
      greenFreshnessCheck: true,
    });
    if (!pairing.ok) {
      return { current: false, code: "STALE_GREEN", mismatched: "predecessor_pairing" };
    }
    if (green.predecessor_branch !== pairing.branch) {
      return { current: false, code: "STALE_GREEN", mismatched: "predecessor_branch" };
    }
  }

  const bindingVerdict = classifyGreenBoundedVerifyBinding(green, boundedVerifyBinding);
  if (!bindingVerdict.ok) {
    return {
      current: false,
      code: bindingVerdict.code || "STALE_GREEN",
      mismatched: bindingVerdict.mismatched || "bounded_verify_binding",
    };
  }

  return { current: true, code: null, mismatched: null };
}

/**
 * When GREEN carries bounded_verify_binding, fail closed on plan/generation/branch drift.
 * Absent binding is not_applicable (native slice freshness only).
 */
/**
 * Expected binding context from a frozen bounded-verify plan (terminal gate consumers).
 */
export function boundedVerifyBindingFromPlan(plan) {
  if (!isObject(plan)) return null;
  const out = {};
  for (const field of ["plan_id", "plan_fingerprint", "execution_id", "git_branch"]) {
    const v = plan[field];
    if (v != null && String(v) !== "") out[field] = String(v);
  }
  return Object.keys(out).length ? out : null;
}

/** Load plan.json binding for a change when bounded-verify is present. */
export function loadBoundedVerifyBindingFromDisk(repoRoot, changeRoot) {
  const cr = posixPath(changeRoot).replace(/\/$/, "");
  const planPath = resourcePath(repoRoot, cr, "bounded-verify/plan.json");
  if (!existsSync(planPath)) return null;
  try {
    const plan = JSON.parse(readFileSync(planPath, "utf8"));
    return boundedVerifyBindingFromPlan(plan);
  } catch {
    return null;
  }
}

export function classifyGreenBoundedVerifyBinding(green, expected = {}) {
  const binding = green?.bounded_verify_binding;
  if (!isObject(binding)) {
    return { ok: true, code: null, not_applicable: true };
  }
  if (!isObject(expected) || Object.keys(expected).length === 0) {
    return {
      ok: false,
      code: "BOUNDED_VERIFY_BINDING_UNRESOLVED",
      mismatched: "bounded_verify_binding",
    };
  }
  const fields = ["plan_id", "plan_fingerprint", "execution_id", "git_branch"];
  for (const field of fields) {
    const exp = expected[field];
    if (exp == null || exp === "") continue;
    const bound = binding[field];
    if (bound && String(bound) !== String(exp)) {
      return {
        ok: false,
        code: "BOUNDED_VERIFY_BINDING_STALE",
        mismatched: field,
      };
    }
  }
  return { ok: true, code: null };
}

/**
 * Receipt-file envelope rules for historical GREEN: artifact_sha256 is diagnostic
 * when semantic upstream objects are present; mandatory fail-closed only when
 * semantic implement is absent but green.implementation_identity.artifact_sha256 is set.
 */
export function classifyGreenHistoricalEnvelopeAuthority(green, upstream = {}) {
  const { implement, implementArtifactSha256 } = upstream;
  const ii = green?.implementation_identity;
  if (!isObject(ii) || !ii.artifact_sha256) {
    return { ok: true, mandatory: false, code: null };
  }
  if (isObject(implement)) {
    return { ok: true, mandatory: false, code: null, semantic_authority: true };
  }
  const implSha = implementArtifactSha256;
  if (!implSha) {
    return {
      ok: false,
      mandatory: true,
      code: "MISSING_CURRENT_IMPLEMENTATION_ARTIFACT_SHA256",
    };
  }
  if (implSha !== ii.artifact_sha256) {
    return { ok: false, mandatory: true, code: "STALE_IMPLEMENTATION_ENVELOPE" };
  }
  return { ok: true, mandatory: true, code: null };
}

/**
 * True when every frozen RED test_commands[] exits 0 on disk (operative re-RED
 * cannot reproduce intentional failure).
 */
export function frozenRedTestCommandsAllPass(red, repoRoot = DEFAULT_REPO_ROOT) {
  if (!isObject(red)) return false;
  const cmds = red.test_commands;
  if (!Array.isArray(cmds) || cmds.length === 0) return false;
  for (const command of cmds) {
    if (typeof command !== "string" || !command.trim()) return false;
    const r = spawnSync("bash", ["-lc", command], {
      cwd: repoRoot,
      encoding: "utf8",
      timeout: 120_000,
    });
    if (r.status !== 0) return false;
  }
  return true;
}

/**
 * Frozen tests changed after IMPLEMENT while operative re-RED cannot repair lineage:
 * ALREADY_SATISFIED observation, or RED_PROVEN with live frozen suite already green.
 */
function requiresTestContractRebaselinePause(red, implement, recovery, repoRoot) {
  if (recovery?.disposition !== "FROZEN_TEST_MUTATED_REQUIRES_RE_RED") return false;
  if (!isObject(red) || !isObject(implement)) return false;
  if (implement.status !== IMPLEMENTATION_WRITTEN) return false;
  if (red.status === ALREADY_SATISFIED) {
    if (implementLinksHistoricalRedProven(implement)) return false;
    return true;
  }
  if (isDurableAlreadySatisfiedObservation(red, repoRoot)) return true;
  if (red.status === RED_PROVEN && isRedEvidenceComplete(red)) {
    return frozenRedTestCommandsAllPass(red, repoRoot);
  }
  return false;
}

function postImplementRecoveryNextCommand(design, sliceId) {
  return `/opsx-post-implement-test-contract-recovery change=${design?.change_id || ""} mode=advance slice=${sliceId}`;
}

/**
 * Post-IMPLEMENT recovery entries that must surface in GREEN / pipeline selectors.
 */
function shouldEnqueuePostImplementRecoveryBlock(recovery) {
  if (!recovery) return false;
  if (recovery.disposition === "FROZEN_TEST_MUTATED_REQUIRES_RE_RED") return true;
  if (recovery.disposition === "PAUSE") return true;
  if (recovery.disposition === "BLOCKED") {
    return (
      recovery.code === "FROZEN_TEST_PATH_MISSING" ||
      recovery.code === "IMPLEMENT_RED_LINEAGE_AMBIGUOUS" ||
      recovery.code === "IMPLEMENT_RED_LINKAGE_MISSING" ||
      recovery.code === "MISSING_FROZEN_TEST_IDENTITY"
    );
  }
  return false;
}

/**
 * True when stale post-IMPLEMENT recovery forbids routing to operative /opsx-apply-red.
 */
export function postImplementRecoveryBlocksOperativeRedRoute({
  red,
  implement,
  recovery,
  repoRoot = DEFAULT_REPO_ROOT,
} = {}) {
  if (!shouldEnqueuePostImplementRecoveryBlock(recovery)) return false;
  if (recovery.disposition === "FROZEN_TEST_MUTATED_REQUIRES_RE_RED") {
    return requiresTestContractRebaselinePause(red, implement, recovery, repoRoot);
  }
  return true;
}

/**
 * Collect recovery-blocked slices in slice_order (after freshness skip).
 */
export function collectPostImplementRecoveryBlocked({
  sliceIds = [],
  redBySlice = {},
  implementBySlice = {},
  greenBySlice = {},
  repoRoot = DEFAULT_REPO_ROOT,
} = {}) {
  const recoveryBlocked = [];
  for (const id of sliceIds) {
    const red = redBySlice[id];
    const implement = implementBySlice[id];
    if (!red || !implement) continue;
    if (
      red.status === ALREADY_SATISFIED &&
      implement.status === IMPLEMENTATION_WRITTEN &&
      !implementLinksHistoricalRedProven(implement)
    ) {
      const legacy = legacyFrozenTestContentIdentityFromRed(red, repoRoot);
      const implFrozen = String(implement.frozen_test_content_identity || "");
      if (legacy.ok && implFrozen && legacy.digest !== implFrozen) {
        recoveryBlocked.push({
          slice_id: id,
          recovery: {
            disposition: "PAUSE",
            code: "TEST_CONTRACT_REBASELINE_REQUIRED",
          },
        });
        continue;
      }
    }
    const recovery = classifyPostImplementGreenRecovery({
      red,
      implement,
      green: greenBySlice[id],
      repoRoot,
    });
    if (recovery.disposition === "GREEN_REFRESH_AUTHORIZED") {
      continue;
    }
    if (shouldEnqueuePostImplementRecoveryBlock(recovery)) {
      recoveryBlocked.push({ slice_id: id, recovery });
    }
  }
  return recoveryBlocked;
}

/**
 * Map first recovery-blocked slice to GREEN selector pick (shared with pipeline stage).
 */
export function resolveRecoveryBlockedGreenPick(
  recoveryBlocked,
  { design, redBySlice = {}, implementBySlice = {}, repoRoot = DEFAULT_REPO_ROOT } = {},
) {
  if (!recoveryBlocked.length) {
    return { ok: false, code: "NO_RECOVERY_BLOCKED", other_eligible: [] };
  }
  const first = recoveryBlocked[0];
  const red = redBySlice[first.slice_id];
  const implement = implementBySlice[first.slice_id];
  if (first.recovery?.code === "TEST_CONTRACT_REBASELINE_REQUIRED") {
    return {
      ok: false,
      code: "TEST_CONTRACT_REBASELINE_REQUIRED",
      slice_id: first.slice_id,
      recovery: first.recovery,
      other_blocked: recoveryBlocked.slice(1),
      other_eligible: [],
      disposition: "PAUSE",
      next_command: postImplementRecoveryNextCommand(design, first.slice_id),
    };
  }
  if (
    requiresTestContractRebaselinePause(
      red,
      implement,
      first.recovery,
      repoRoot,
    )
  ) {
    return {
      ok: false,
      code: "TEST_CONTRACT_REBASELINE_REQUIRED",
      slice_id: first.slice_id,
      recovery: first.recovery,
      other_blocked: recoveryBlocked.slice(1),
      other_eligible: [],
      disposition: "PAUSE",
      next_command: postImplementRecoveryNextCommand(design, first.slice_id),
    };
  }
  if (
    first.recovery.disposition === "PAUSE" &&
    first.recovery.code === "IDENTITY_ALGORITHM_UNRESOLVED"
  ) {
    return {
      ok: false,
      code: "IDENTITY_ALGORITHM_UNRESOLVED",
      slice_id: first.slice_id,
      recovery: first.recovery,
      other_blocked: recoveryBlocked.slice(1),
      other_eligible: [],
      disposition: "PAUSE",
    };
  }
  if (first.recovery.code === "FROZEN_TEST_PATH_MISSING") {
    return {
      ok: false,
      code: "FROZEN_TEST_PATH_MISSING",
      slice_id: first.slice_id,
      recovery: first.recovery,
      other_blocked: recoveryBlocked.slice(1),
      other_eligible: [],
      disposition: "PAUSE",
      next_command: undefined,
    };
  }
  const mutatedOnly =
    first.recovery.disposition === "FROZEN_TEST_MUTATED_REQUIRES_RE_RED";
  const lineageOrLinkage =
    first.recovery.code === "IMPLEMENT_RED_LINEAGE_AMBIGUOUS" ||
    first.recovery.code === "IMPLEMENT_RED_LINKAGE_MISSING" ||
    first.recovery.code === "MISSING_FROZEN_TEST_IDENTITY";
  if (lineageOrLinkage) {
    return {
      ok: false,
      code:
        first.recovery.code === "IMPLEMENT_RED_LINEAGE_AMBIGUOUS"
          ? "IMPLEMENT_RED_LINEAGE_BLOCKED"
          : first.recovery.code,
      slice_id: first.slice_id,
      recovery: first.recovery,
      other_blocked: recoveryBlocked.slice(1),
      other_eligible: [],
      disposition: "PAUSE",
    };
  }
  if (
    mutatedOnly &&
    requiresTestContractRebaselinePause(
      redBySlice[first.slice_id],
      implementBySlice[first.slice_id],
      first.recovery,
      repoRoot,
    )
  ) {
    return {
      ok: false,
      code: "TEST_CONTRACT_REBASELINE_REQUIRED",
      slice_id: first.slice_id,
      recovery: first.recovery,
      other_blocked: recoveryBlocked.slice(1),
      other_eligible: [],
      disposition: "PAUSE",
      next_command: postImplementRecoveryNextCommand(design, first.slice_id),
    };
  }
  return {
    ok: false,
    code: mutatedOnly ? "FROZEN_TEST_MUTATED_REQUIRES_RE_RED" : "RECOVERY_BLOCKED",
    slice_id: first.slice_id,
    recovery: first.recovery,
    other_blocked: recoveryBlocked.slice(1),
    other_eligible: [],
    next_command: mutatedOnly
      ? `/opsx-apply-red change=${design?.change_id || ""} slice=${first.slice_id}`
      : undefined,
  };
}

export function isCurrentGreenProven(green, upstream = {}) {
  return classifyGreenEvidenceFreshness(green, upstream).current === true;
}

function currentGreenStatusIs(map, id, upstream = {}) {
  return isCurrentGreenProven(map && map[id], upstream);
}

const TEST_MODULE_PATH_IN_COMMAND_RE = /(tests\/[^\s'":]+\.py)/gi;

function extractTestModulePathsFromCommands(commands) {
  const paths = new Set();
  for (const raw of commands || []) {
    const cmd = String(raw);
    let match;
    TEST_MODULE_PATH_IN_COMMAND_RE.lastIndex = 0;
    while ((match = TEST_MODULE_PATH_IN_COMMAND_RE.exec(cmd)) !== null) {
      paths.add(posixPath(match[1]));
    }
  }
  return [...paths];
}

/**
 * IMPLEMENT-bound RED_PROVEN freeze paths when operative RED rewrote test_paths
 * (e.g. ALREADY_SATISFIED adoption). Resolves by matching legacy_concat bytes to
 * implement.frozen_test_content_identity, preferring GREEN red_executions lineage.
 */
export function resolveHistoricalLinkageFrozenTestPaths(
  red,
  implement,
  green = null,
  repoRoot = DEFAULT_REPO_ROOT,
) {
  if (!isObject(red) || !implementLinksHistoricalRedProven(implement)) {
    return null;
  }
  const target = String(
    implement.frozen_test_content_identity ||
      implement.red_identity?.test_content_identity ||
      "",
  );
  if (!target) return null;

  const candidateSets = [];
  const pushSet = (paths) => {
    const sorted = [...new Set((paths || []).map(posixPath).filter(Boolean))].sort();
    if (sorted.length) candidateSets.push(sorted);
  };

  pushSet(red.test_paths);
  pushSet(extractTestModulePathsFromCommands(red.test_commands));
  pushSet(
    (red.command_executions || [])
      .map((entry) => entry?.test_path)
      .filter(Boolean),
  );
  if (isObject(green)) {
    pushSet(
      extractTestModulePathsFromCommands((green.red_executions || []).map((entry) => entry.command)),
    );
  }

  for (const paths of candidateSets) {
    const legacy = computeFrozenTestContentIdentity(repoRoot, paths);
    if (legacy.ok && legacy.digest === target) return paths;
    const canonical = computeLiveFrozenTestContentIdentity(repoRoot, {
      test_paths: paths,
      fixture_paths: [],
    });
    if (canonical.ok && canonical.identity === target) return paths;
  }
  return null;
}

function redReceiptForHistoricalFrozenIdentity(red, implement, green, repoRoot) {
  const historical = resolveHistoricalLinkageFrozenTestPaths(red, implement, green, repoRoot);
  if (!historical) return red;
  const receipt = {
    ...red,
    test_paths: historical,
    fixture_paths: [],
  };
  if (red.status !== RED_PROVEN) {
    const linked =
      implement?.red_identity?.test_content_identity ||
      implement?.frozen_test_content_identity;
    if (linked) receipt.test_content_identity = linked;
  }
  return receipt;
}

/**
 * Canonical frozen-test digest for GREEN freshness (per-path sha256 + identityDigest).
 * Includes fixture_paths. Differs from legacy concat when only the algorithm changed.
 */
export function canonicalFrozenTestContentIdentityFromRed(
  red,
  repoRoot = DEFAULT_REPO_ROOT,
) {
  if (!isObject(red)) {
    return { ok: false, code: "missing_red", identity: null };
  }
  return computeLiveFrozenTestContentIdentity(repoRoot, {
    test_paths: red.test_paths || [],
    fixture_paths: red.fixture_paths || [],
  });
}

/**
 * Legacy RED/IMPLEMENT freeze digest (concat raw bytes, sorted paths).
 * Used to detect genuine on-disk test mutation vs identity-algorithm drift.
 */
export function legacyFrozenTestContentIdentityFromRed(
  red,
  repoRoot = DEFAULT_REPO_ROOT,
) {
  if (!isObject(red)) {
    return { ok: false, code: "missing_red", identity: null };
  }
  const paths = [...(red.test_paths || []), ...(red.fixture_paths || [])]
    .map(posixPath)
    .filter(Boolean)
    .sort();
  return computeFrozenTestContentIdentity(repoRoot, paths);
}

/**
 * Historical RED receipts used test_paths-only concat; IMPLEMENT freeze uses
 * legacy_concat on test_paths+fixture_paths; GREEN uses canonical path-sha.
 *
 * @param {object} red — APPLY-RED artifact
 * @param {string} repoRoot
 * @returns {{
 *   ok: boolean,
 *   declared: string | null,
 *   canonical: string | null,
 *   legacy_all_paths: string | null,
 *   legacy_test_paths_only: string | null,
 *   matched_variant: string | null,
 *   accepted_digests: string[],
 * }}
 */
export function resolveDeclaredFrozenTestContentIdentity(red, repoRoot = DEFAULT_REPO_ROOT) {
  const declared = red?.test_content_identity ? String(red.test_content_identity) : null;
  const canonical = canonicalFrozenTestContentIdentityFromRed(red, repoRoot);
  const legacyAll = legacyFrozenTestContentIdentityFromRed(red, repoRoot);
  const testOnlyPaths = [...(red?.test_paths || [])].map(posixPath).filter(Boolean).sort();
  const legacyTestOnly = computeFrozenTestContentIdentity(repoRoot, testOnlyPaths);
  const accepted = [];
  if (canonical.ok && canonical.identity) accepted.push(canonical.identity);
  if (legacyAll.ok && legacyAll.digest) accepted.push(legacyAll.digest);
  if (legacyTestOnly.ok && legacyTestOnly.digest) accepted.push(legacyTestOnly.digest);
  const unique = [...new Set(accepted)];
  let matched_variant = null;
  if (declared) {
    if (canonical.ok && declared === canonical.identity) {
      matched_variant = "canonical_path_sha256_v1";
    } else if (legacyAll.ok && declared === legacyAll.digest) {
      matched_variant = "legacy_concat_all_paths_v1";
    } else if (legacyTestOnly.ok && declared === legacyTestOnly.digest) {
      matched_variant = "legacy_concat_test_paths_only_v1";
    }
  }
  const pathsOk =
    (canonical.ok || legacyAll.ok || legacyTestOnly.ok) && unique.length > 0;
  const ok = !declared || (pathsOk && matched_variant !== null);
  return {
    ok,
    declared,
    canonical: canonical.ok ? canonical.identity : null,
    legacy_all_paths: legacyAll.ok ? legacyAll.digest : null,
    legacy_test_paths_only: legacyTestOnly.ok ? legacyTestOnly.digest : null,
    matched_variant,
    accepted_digests: unique,
  };
}

/**
 * Authoritative digest for new RED operative decisions and GREEN freshness.
 */
export function authoritativeFrozenTestContentIdentityFromRed(
  red,
  repoRoot = DEFAULT_REPO_ROOT,
) {
  const canonical = canonicalFrozenTestContentIdentityFromRed(red, repoRoot);
  if (canonical.ok && canonical.identity) {
    return { ok: true, digest: canonical.identity, algorithm: "canonical_path_sha256_v1" };
  }
  const legacy = legacyFrozenTestContentIdentityFromRed(red, repoRoot);
  if (legacy.ok && legacy.digest) {
    return { ok: true, digest: legacy.digest, algorithm: "legacy_concat_all_paths_v1" };
  }
  return { ok: false, digest: null, algorithm: null };
}

/**
 * IMPLEMENT freeze aligns with on-disk RED frozen bytes (legacy or canonical algorithm drift).
 * Does not authorize arbitrary digests: impl must match legacy bytes or an accepted live variant.
 */
export function implementFrozenTestIdentityMatchesRedBytes(
  red,
  implement,
  repoRoot = DEFAULT_REPO_ROOT,
  green = null,
) {
  if (!isObject(red) || !isObject(implement)) {
    return { ok: false, code: "MISSING_PREDECESSORS" };
  }
  const implFrozen = String(implement.frozen_test_content_identity || "");
  if (!implFrozen) {
    return { ok: false, code: "MISSING_IMPL_FROZEN" };
  }
  const effectiveRed = redReceiptForHistoricalFrozenIdentity(red, implement, green, repoRoot);
  const legacy = legacyFrozenTestContentIdentityFromRed(effectiveRed, repoRoot);
  if (!legacy.ok || !legacy.digest) {
    return { ok: false, code: "LEGACY_UNAVAILABLE" };
  }
  if (implFrozen === legacy.digest) {
    return { ok: true, variant: "legacy_concat_all_paths_v1", legacy_digest: legacy.digest };
  }
  const resolved = resolveDeclaredFrozenTestContentIdentity(effectiveRed, repoRoot);
  const canonical = resolved.canonical;
  const operativeRaw = effectiveRed?.operative_red_transition?.test_content_identity;
  const operative =
    operativeRaw != null && String(operativeRaw).trim() ? String(operativeRaw).trim() : "";
  const acceptedNow = new Set(resolved.accepted_digests || []);
  if (operative && acceptedNow.has(operative) && implFrozen === operative) {
    return {
      ok: true,
      variant: "operative_red_transition",
      legacy_digest: legacy.digest,
      canonical_digest: canonical,
    };
  }
  if (
    resolved.legacy_test_paths_only &&
    implFrozen === resolved.legacy_test_paths_only &&
    (resolved.declared === resolved.legacy_test_paths_only ||
      resolved.matched_variant === "legacy_concat_test_paths_only_v1")
  ) {
    return {
      ok: true,
      variant: "legacy_concat_test_paths_only_v1",
      legacy_digest: legacy.digest,
      canonical_digest: canonical,
      legacy_test_paths_only: resolved.legacy_test_paths_only,
    };
  }
  if (
    canonical &&
    implFrozen === canonical &&
    (resolved.matched_variant === "canonical_path_sha256_v1" ||
      resolved.matched_variant === "legacy_concat_all_paths_v1")
  ) {
    return {
      ok: true,
      variant: "canonical_path_sha256_v1",
      legacy_digest: legacy.digest,
      canonical_digest: canonical,
    };
  }
  if (
    resolved.legacy_test_paths_only &&
    implFrozen === resolved.legacy_test_paths_only &&
    resolved.matched_variant === "legacy_concat_test_paths_only_v1"
  ) {
    return {
      ok: true,
      variant: "legacy_concat_test_paths_only_v1",
      legacy_digest: legacy.digest,
      canonical_digest: canonical,
    };
  }
  return {
    ok: false,
    code: "FROZEN_BYTES_MISMATCH",
    implement_frozen: implFrozen,
    legacy_digest: legacy.digest,
    canonical_digest: canonical,
  };
}

/**
 * Post-IMPLEMENT stale-GREEN recovery (does not re-run operative RED failure).
 *
 * GREEN_REFRESH_AUTHORIZED: legacy bytes match IMPLEMENT freeze + full predecessor
 * linkage (`isHistoricalRedProvenBound`) — forged `implement.red_identity` cannot
 * authorize refresh.
 * FROZEN_TEST_MUTATED_REQUIRES_RE_RED: live legacy digest ≠ implement freeze.
 */
export function classifyPostImplementGreenRecovery({
  red,
  implement,
  green = null,
  repoRoot = DEFAULT_REPO_ROOT,
} = {}) {
  if (!isObject(red) || !isObject(implement)) {
    return { disposition: "NOT_APPLICABLE", code: "MISSING_PREDECESSORS" };
  }
  if (implement.status !== IMPLEMENTATION_WRITTEN) {
    return { disposition: "NOT_APPLICABLE", code: "IMPLEMENT_NOT_WRITTEN" };
  }
  const historicalRedLink = implementLinksHistoricalRedProven(implement);
  if (red.status !== RED_PROVEN && !historicalRedLink) {
    return { disposition: "NOT_APPLICABLE", code: "RED_NOT_PROVEN" };
  }
  const effectiveRed = redReceiptForHistoricalFrozenIdentity(red, implement, green, repoRoot);
  const legacy = legacyFrozenTestContentIdentityFromRed(effectiveRed, repoRoot);
  const canonical = canonicalFrozenTestContentIdentityFromRed(effectiveRed, repoRoot);
  if (!legacy.ok || !legacy.digest) {
    return {
      disposition: "BLOCKED",
      code: "FROZEN_TEST_PATH_MISSING",
      missing_paths: legacy.path ? [legacy.path] : canonical.missing_paths || [],
    };
  }
  const implFrozen = String(implement.frozen_test_content_identity || "");
  let receiptTci = red.test_content_identity ? String(red.test_content_identity) : null;
  if (historicalRedLink && red.status !== RED_PROVEN) {
    receiptTci = implement.red_identity?.test_content_identity
      ? String(implement.red_identity.test_content_identity)
      : receiptTci;
  }
  const canonicalIdentity = canonical.ok ? canonical.identity : null;

  if (!implFrozen) {
    return {
      disposition: "BLOCKED",
      code: "MISSING_FROZEN_TEST_IDENTITY",
      first_failing_predicate: "implement.frozen_test_content_identity_missing",
      implement_frozen: null,
      legacy_on_disk_digest: legacy.digest,
      canonical_live_digest: canonicalIdentity,
      receipt_test_content_identity: receiptTci,
      identity_algorithm_drift_only: false,
    };
  }

  if (receiptTci) {
    const receiptAligns =
      receiptTci === implFrozen ||
      receiptTci === legacy.digest ||
      (canonicalIdentity && receiptTci === canonicalIdentity);
    if (!receiptAligns) {
      return {
        disposition: "PAUSE",
        code: "IDENTITY_ALGORITHM_UNRESOLVED",
        first_failing_predicate: "red.test_content_identity_unaligned_with_frozen_bytes",
        implement_frozen: implFrozen,
        legacy_on_disk_digest: legacy.digest,
        canonical_live_digest: canonicalIdentity,
        receipt_test_content_identity: receiptTci,
        identity_algorithm_drift_only: false,
      };
    }
  }

  const frozenMatch = implementFrozenTestIdentityMatchesRedBytes(
    red,
    implement,
    repoRoot,
    green,
  );
  if (!frozenMatch.ok) {
    return {
      disposition: "FROZEN_TEST_MUTATED_REQUIRES_RE_RED",
      code: "FROZEN_TEST_MUTATED",
      first_failing_predicate:
        "implement.frozen_test_content_identity !== legacy_on_disk_concat_digest",
      implement_frozen: implFrozen,
      legacy_on_disk_digest: legacy.digest,
      canonical_live_digest: canonicalIdentity,
      receipt_test_content_identity: receiptTci,
      identity_algorithm_drift_only: false,
    };
  }
  if (!implementRedLinkageComplete(implement)) {
    return {
      disposition: "BLOCKED",
      code: "IMPLEMENT_RED_LINKAGE_MISSING",
      first_failing_predicate: "implement.red_identity_or_frozen_digest_incomplete",
    };
  }
  const historicalBound =
    red.status === RED_PROVEN
      ? isHistoricalRedProvenBound(red, implement, repoRoot)
      : isImplementLinkedHistoricalRedBound(red, implement, repoRoot);
  if (!historicalBound) {
    return {
      disposition: "BLOCKED",
      code: "IMPLEMENT_RED_LINEAGE_AMBIGUOUS",
      first_failing_predicate: "historical_red_proven_bound",
      legacy_on_disk_digest: legacy.digest,
      canonical_live_digest: canonical.ok ? canonical.identity : null,
    };
  }
  if (green && isCurrentGreenProven(green, {})) {
    return { disposition: "NOT_APPLICABLE", code: "GREEN_ALREADY_CURRENT" };
  }
  return {
    disposition: "GREEN_REFRESH_AUTHORIZED",
    code: "GREEN_REFRESH_AUTHORIZED",
    legacy_on_disk_digest: legacy.digest,
    canonical_live_digest: canonical.ok ? canonical.identity : null,
    identity_algorithm_drift_only: Boolean(
      canonical.ok && canonical.identity && canonical.identity !== legacy.digest,
    ),
  };
}

/**
 * Live sha256 identity over frozen RED test_paths + fixture_paths.
 * Fail-closed when any path is missing or unreadable.
 */
/**
 * Live sha256 identity over IMPLEMENT production_paths_* (product source inputs).
 */
export function computeLiveProductSourceInputIdentity(repoRoot, {
  production_paths = [],
} = {}) {
  const paths = [...(production_paths || [])]
    .map(posixPath)
    .filter(Boolean)
    .sort();
  const unique = [...new Set(paths)];
  const entries = [];
  const missing_paths = [];
  for (const path of unique) {
    const abs = resourcePath(repoRoot, path);
    if (!existsSync(abs)) {
      missing_paths.push(path);
      entries.push({ path, sha256: "" });
      continue;
    }
    try {
      entries.push({ path, sha256: fileSha256(abs) });
    } catch {
      missing_paths.push(path);
      entries.push({ path, sha256: "" });
    }
  }
  return {
    ok: missing_paths.length === 0 && unique.length > 0,
    identity: unique.length ? identityDigest(entries) : null,
    entries,
    missing_paths,
  };
}

export function collectImplementProductionPaths(implement) {
  const paths = [
    ...(implement?.production_paths_created || []),
    ...(implement?.production_paths_modified || []),
  ]
    .map(posixPath)
    .filter(Boolean);
  return [...new Set(paths)].sort();
}

export function computeLiveFrozenTestContentIdentity(repoRoot, {
  test_paths = [],
  fixture_paths = [],
} = {}) {
  const paths = [...(test_paths || []), ...(fixture_paths || [])]
    .map(posixPath)
    .filter(Boolean)
    .sort();
  const entries = [];
  const missing_paths = [];
  for (const path of paths) {
    const abs = resourcePath(repoRoot, path);
    if (!existsSync(abs)) {
      missing_paths.push(path);
      entries.push({ path, sha256: "" });
      continue;
    }
    try {
      entries.push({ path, sha256: fileSha256(abs) });
    } catch {
      missing_paths.push(path);
      entries.push({ path, sha256: "" });
    }
  }
  return {
    ok: missing_paths.length === 0 && paths.length > 0,
    identity: paths.length ? identityDigest(entries) : null,
    entries,
    missing_paths,
  };
}

/**
 * Full-frozen-suite PASS evidence for ALREADY_SATISFIED observation.
 * Every test_commands[] entry executed with exit 0 and pass_qualified.
 */
export function isAlreadySatisfiedEvidenceComplete(red) {
  if (!isObject(red)) return false;
  const versionOk =
    red.schema_version === APPLY_RED_SCHEMA_VERSION ||
    red.evidence_contract === RED_EVIDENCE_CONTRACT;
  if (!versionOk) return false;
  const cmds = red.test_commands;
  const execs = red.command_executions;
  if (!Array.isArray(cmds) || cmds.length === 0) return false;
  if (!Array.isArray(execs) || execs.length !== cmds.length) return false;
  return execs.every((entry, i) => {
    if (!isObject(entry)) return false;
    if (entry.pass_qualified !== true && entry.red_qualified !== true) return false;
    if (entry.command !== cmds[i]) return false;
    if (
      entry.final_classification !== ALREADY_SATISFIED &&
      entry.final_classification !== "PASS"
    ) {
      return false;
    }
    if (entry.final_exit_code !== 0) return false;
    if (!Array.isArray(entry.attempts) || entry.attempts.length === 0) return false;
    return true;
  });
}

/**
 * Durable ALREADY_SATISFIED observation branch. Observation alone never
 * authorizes product writes; adoption audit is a separate IMPLEMENT hop.
 */
export function isDurableAlreadySatisfiedObservation(
  red,
  repoRoot = DEFAULT_REPO_ROOT,
) {
  if (!isObject(red) || red.status !== ALREADY_SATISFIED) return false;
  if (red.observation_only !== true) return false;
  if (red.implementation_write_authorized !== false) return false;
  if (red.adoption_audit_eligible !== true) return false;
  if (red.tests_frozen !== true) return false;
  if (red.next_stage !== "APPLY_IMPLEMENT") return false;
  const looksLikeReceipt =
    Array.isArray(red.test_commands) ||
    Array.isArray(red.command_executions) ||
    Number.isInteger(red.schema_version);
  if (!looksLikeReceipt) return false;
  if (!isAlreadySatisfiedEvidenceComplete(red)) return false;
  const authorize = getRedOperativeTransitionAuthorized();
  if (!authorize) return false;
  const validationPhase = isOperativeReceiptRecoveryEnvelopeValid(red)
    ? RED_VALIDATION_PHASE_HISTORICAL
    : RED_VALIDATION_PHASE_OPERATIVE;
  return authorize(red, repoRoot, { validationPhase });
}

/**
 * Either durable RED_PROVEN or durable ALREADY_SATISFIED observation.
 */
export function isDurableRedAuthority(red, repoRoot = DEFAULT_REPO_ROOT) {
  return (
    isDurableRedProven(red, repoRoot) ||
    isDurableAlreadySatisfiedObservation(red, repoRoot)
  );
}

/**
 * Resolve RED authority branch for pairing / selection.
 *
 * @param {object} [options]
 * @param {string} [options.validationPhase] — when `historical_bound`, skip operative live re-exec.
 */
export function redAuthorityBranch(
  red,
  repoRoot = DEFAULT_REPO_ROOT,
  implement = null,
  options = {},
) {
  const historicalOnly = options.validationPhase === RED_VALIDATION_PHASE_HISTORICAL;
  const authorize = getRedOperativeTransitionAuthorized();

  if (
    isObject(implement) &&
    implement.status === IMPLEMENTATION_WRITTEN &&
    red?.status === RED_PROVEN
  ) {
    if (isHistoricalRedProvenBound(red, implement, repoRoot)) {
      return NORMAL_PIPELINE_BRANCH;
    }
    if (implementRedLinkageComplete(implement)) {
      if (isHistoricalRedProvenBound(red, implement, repoRoot)) {
        return NORMAL_PIPELINE_BRANCH;
      }
      const recovery = classifyPostImplementGreenRecovery({
        red,
        implement,
        repoRoot,
      });
      if (recovery.disposition === "GREEN_REFRESH_AUTHORIZED") {
        return NORMAL_PIPELINE_BRANCH;
      }
      return null;
    }
    return null;
  }
  if (
    isObject(implement) &&
    implement.status === IMPLEMENTATION_WRITTEN &&
    red?.status === ALREADY_SATISFIED
  ) {
    if (isHistoricalAlreadySatisfiedWriteBound(red, implement, repoRoot)) {
      return ALREADY_SATISFIED_WRITE_PIPELINE_BRANCH;
    }
    if (
      implementLinksHistoricalRedProven(implement) &&
      isImplementLinkedHistoricalRedBound(red, implement, repoRoot, options.green)
    ) {
      return ALREADY_SATISFIED_WRITE_PIPELINE_BRANCH;
    }
  }
  if (
    isObject(implement) &&
    implement.status === IMPLEMENTATION_ADOPTED &&
    red?.status === ALREADY_SATISFIED
  ) {
    if (isHistoricalAlreadySatisfiedAdoptionBound(red, implement, repoRoot)) {
      return ADOPTION_PIPELINE_BRANCH;
    }
    if (historicalOnly) {
      if (
        authorize?.(red, repoRoot, {
          validationPhase: RED_VALIDATION_PHASE_HISTORICAL,
        })
      ) {
        return ADOPTION_PIPELINE_BRANCH;
      }
      return null;
    }
    if (isDurableAlreadySatisfiedObservation(red, repoRoot)) {
      return ADOPTION_PIPELINE_BRANCH;
    }
    return null;
  }
  if (historicalOnly) {
    return null;
  }
  if (isDurableRedProven(red, repoRoot)) return NORMAL_PIPELINE_BRANCH;
  if (isDurableAlreadySatisfiedObservation(red, repoRoot)) {
    return ADOPTION_PIPELINE_BRANCH;
  }
  if (isObject(red) && red.status === ALREADY_SATISFIED) {
    const looksLikeReceipt =
      Array.isArray(red.test_commands) ||
      Array.isArray(red.command_executions) ||
      Number.isInteger(red.schema_version);
    if (!looksLikeReceipt) return ADOPTION_PIPELINE_BRANCH;
  }
  return null;
}

export function implementAuthorityBranch(
  implement,
  red = null,
  repoRoot = DEFAULT_REPO_ROOT,
) {
  if (!isObject(implement)) return null;
  if (implement.status === IMPLEMENTATION_WRITTEN) {
    if (
      isObject(red) &&
      red.status === ALREADY_SATISFIED &&
      (isHistoricalAlreadySatisfiedWriteBound(red, implement, repoRoot) ||
        (implementLinksHistoricalRedProven(implement) &&
          isImplementLinkedHistoricalRedBound(red, implement, repoRoot)))
    ) {
      return ALREADY_SATISFIED_WRITE_PIPELINE_BRANCH;
    }
    return NORMAL_PIPELINE_BRANCH;
  }
  if (implement.status === IMPLEMENTATION_ADOPTED) return ADOPTION_PIPELINE_BRANCH;
  return null;
}

/**
 * Историческая связка ALREADY_SATISFIED + IMPLEMENTATION_WRITTEN после реальной
 * product-записи: замороженные тесты уже проходили, operative live re-failure не требуется.
 */
export function isHistoricalAlreadySatisfiedWriteBound(
  red,
  implement,
  repoRoot = DEFAULT_REPO_ROOT,
) {
  if (!isObject(red) || red.status !== ALREADY_SATISFIED) return false;
  if (!isObject(implement) || implement.status !== IMPLEMENTATION_WRITTEN) return false;
  if (red.slice_id && implement.slice_id && red.slice_id !== implement.slice_id) {
    return false;
  }
  if (red.tests_frozen !== true) return false;
  if (!isAlreadySatisfiedEvidenceComplete(red)) return false;
  if (implement.tests_modified === true || implement.tests_executed === true) {
    return false;
  }
  if (implement.next_stage && implement.next_stage !== "APPLY_GREEN") {
    return false;
  }
  const frozenMatch = implementFrozenTestIdentityMatchesRedBytes(red, implement, repoRoot);
  if (!frozenMatch.ok) {
    return false;
  }
  const written = [
    ...(implement.production_paths_modified || []),
    ...(implement.production_paths_created || []),
  ];
  if (written.length < 1) {
    return false;
  }
  const rid = implement.red_identity;
  if (!isObject(rid)) return false;
  if (rid.status && rid.status !== ALREADY_SATISFIED && rid.status !== RED_PROVEN) {
    return false;
  }
  if (rid.status === RED_PROVEN) {
    return isImplementLinkedHistoricalRedBound(red, implement, repoRoot);
  }
  if (rid.slice_id && red.slice_id && rid.slice_id !== red.slice_id) return false;
  if (
    rid.test_content_identity &&
    implement.frozen_test_content_identity &&
    rid.test_content_identity !== implement.frozen_test_content_identity
  ) {
    return false;
  }
  if (rid.tests_frozen === false) return false;
  const authorize = getRedOperativeTransitionAuthorized();
  if (authorize?.(red, repoRoot, { validationPhase: RED_VALIDATION_PHASE_HISTORICAL })) {
    return true;
  }
  const gate = evaluateRedTransitionGate({
    artifact: red,
    repoRoot,
    validationPhase: RED_VALIDATION_PHASE_HISTORICAL,
    historicalRead: true,
  });
  return gate.ok === true;
}

/**
 * Valid predecessor pairing for APPLY GREEN.
 * 1. RED_PROVEN + IMPLEMENTATION_WRITTEN (NORMAL)
 * 2. ALREADY_SATISFIED + IMPLEMENTATION_ADOPTED (ADOPTION)
 * 3. ALREADY_SATISFIED + IMPLEMENTATION_WRITTEN (ALREADY_SATISFIED_WRITE)
 */
export function classifyGreenPredecessorPairing({
  red,
  implement,
  green = null,
  repoRoot = DEFAULT_REPO_ROOT,
  greenFreshnessCheck = false,
} = {}) {
  const branchOpts = greenFreshnessCheck
    ? { validationPhase: RED_VALIDATION_PHASE_HISTORICAL }
    : {};
  const redBranch = redAuthorityBranch(red, repoRoot, implement, {
    ...branchOpts,
    green,
  });
  const implBranch = implementAuthorityBranch(implement, red, repoRoot);
  if (!redBranch || !implBranch) {
    return { ok: false, code: "IMPLEMENTATION_AUTHORITY_MISSING" };
  }
  if (redBranch !== implBranch) {
    return { ok: false, code: "CROSS_PAIRED_PREDECESSOR" };
  }
  if (redBranch === NORMAL_PIPELINE_BRANCH) {
    if (red?.status !== RED_PROVEN || implement?.status !== IMPLEMENTATION_WRITTEN) {
      return { ok: false, code: "CROSS_PAIRED_PREDECESSOR" };
    }
  } else if (redBranch === ADOPTION_PIPELINE_BRANCH) {
    if (red?.status !== ALREADY_SATISFIED || implement?.status !== IMPLEMENTATION_ADOPTED) {
      return { ok: false, code: "CROSS_PAIRED_PREDECESSOR" };
    }
  } else if (redBranch === ALREADY_SATISFIED_WRITE_PIPELINE_BRANCH) {
    if (red?.status !== ALREADY_SATISFIED || implement?.status !== IMPLEMENTATION_WRITTEN) {
      return { ok: false, code: "CROSS_PAIRED_PREDECESSOR" };
    }
    if (!isHistoricalAlreadySatisfiedWriteBound(red, implement, repoRoot)) {
      if (
        !(
          implementLinksHistoricalRedProven(implement) &&
          isImplementLinkedHistoricalRedBound(red, implement, repoRoot, green)
        )
      ) {
        return { ok: false, code: "CROSS_PAIRED_PREDECESSOR" };
      }
    }
  }
  return { ok: true, branch: redBranch };
}

function implementRedLinkageComplete(implement) {
  return (
    isObject(implement) &&
    isObject(implement.red_identity) &&
    typeof implement.frozen_test_content_identity === "string"
  );
}

/**
 * IMPLEMENT snapshot still names a durable RED_PROVEN lineage (operative RED
 * receipt may later be ALREADY_SATISFIED without erasing implement linkage).
 */
function implementLinksHistoricalRedProven(implement) {
  const rid = implement?.red_identity;
  return (
    isObject(implement) &&
    implement.status === IMPLEMENTATION_WRITTEN &&
    isObject(rid) &&
    rid.status === RED_PROVEN
  );
}

/**
 * Historical post-IMPLEMENT bound when live RED is not RED_PROVEN but IMPLEMENT
 * red_identity freeze still records RED_PROVEN + matching on-disk frozen bytes.
 */
function isImplementLinkedHistoricalRedBound(
  red,
  implement,
  repoRoot = DEFAULT_REPO_ROOT,
  green = null,
) {
  if (!implementLinksHistoricalRedProven(implement)) return false;
  if (!isObject(red)) return false;
  if (red.slice_id && implement.slice_id && red.slice_id !== implement.slice_id) {
    return false;
  }
  if (implement.tests_modified === true || implement.tests_executed === true) {
    return false;
  }
  if (implement.next_stage && implement.next_stage !== "APPLY_GREEN") {
    return false;
  }
  const frozenMatch = implementFrozenTestIdentityMatchesRedBytes(
    red,
    implement,
    repoRoot,
    green,
  );
  if (!frozenMatch.ok) {
    return false;
  }
  const rid = implement.red_identity;
  if (rid.slice_id && implement.slice_id && rid.slice_id !== implement.slice_id) {
    return false;
  }
  if (
    rid.test_content_identity &&
    implement.frozen_test_content_identity &&
    rid.test_content_identity !== implement.frozen_test_content_identity
  ) {
    return false;
  }
  if (rid.tests_frozen === false) return false;
  return true;
}

/**
 * Сохранённый RED_PROVEN envelope после позднего ALREADY_SATISFIED.
 * Пустой preserved digest не считается матчем (fail-closed).
 */
export function isPreservedRedProvenLineageCurrent(red, repoRoot = DEFAULT_REPO_ROOT) {
  const hist = red?.historical_red_proven_receipt;
  if (!isObject(hist)) return false;
  if (hist.preserved_from_status !== RED_PROVEN) return false;
  const preserved = String(hist.preserved_test_content_identity || "").trim();
  if (!preserved) return false;
  const legacy = legacyFrozenTestContentIdentityFromRed(red, repoRoot);
  if (!legacy.ok || !legacy.digest) return false;
  const canonical = canonicalFrozenTestContentIdentityFromRed(red, repoRoot);
  const liveCanonical = canonical.ok ? canonical.identity : null;
  if (preserved === legacy.digest || (liveCanonical && preserved === liveCanonical)) {
    return true;
  }
  return greenBoundFrozenTestIdentityMatchesLive(
    preserved,
    red,
    repoRoot,
    liveCanonical || legacy.digest,
  );
}

const OPERATIVE_RECEIPT_RECOVERY_SCHEMA = "opsx.red_operative_receipt_recovery";

/**
 * Валидный operative-receipt recovery envelope (без R5 re-exec).
 */
export function isOperativeReceiptRecoveryEnvelopeValid(red) {
  const rec = red?.operative_receipt_recovery;
  if (!isObject(rec)) return false;
  if (rec.schema !== OPERATIVE_RECEIPT_RECOVERY_SCHEMA) return false;
  if (rec.schema_version !== 1) return false;
  if (rec.mode !== "recover-operative-red") return false;
  if (rec.prior_operative_authority_stale !== true) return false;
  if (typeof rec.recovered_at !== "string" || !rec.recovered_at.trim()) return false;
  return true;
}

/**
 * Оперативный recovery envelope после ``recover-operative-red`` (visual closeout).
 * Stale pause обходится только при прохождении того же durable bar, что и у свежего RED.
 */
export function isOperativeReceiptRecoveryAuthoritative(
  red,
  repoRoot = DEFAULT_REPO_ROOT,
) {
  if (!isOperativeReceiptRecoveryEnvelopeValid(red)) return false;
  return isDurableAlreadySatisfiedObservation(red, repoRoot);
}

/**
 * Оперативный FAIL при полной receipt-форме: не принимать как RED history.
 * Исключения: operative durable authority, recovery envelope или historical_red_proven_receipt.
 *
 * RED_PROVEN с полной формой, но без durable authority — не stale: selector ведёт
 * в APPLY_RED для оперативного re-observe, а не в RECOVERY_PAUSED.
 */
export function isOperativeRedReceiptStale(red, repoRoot = DEFAULT_REPO_ROOT) {
  if (!isObject(red)) return false;
  if (isDurableRedAuthority(red, repoRoot)) return false;
  if (isOperativeReceiptRecoveryAuthoritative(red, repoRoot)) return false;
  if (isPreservedRedProvenLineageCurrent(red, repoRoot)) return false;
  const looksLikeReceipt =
    red.schema === "opsx.apply_red" ||
    Array.isArray(red.test_commands) ||
    Array.isArray(red.command_executions);
  if (!looksLikeReceipt) return false;
  if (red.status === ALREADY_SATISFIED && isAlreadySatisfiedEvidenceComplete(red)) {
    return true;
  }
  return false;
}

/**
 * Только preserved RED_PROVEN lineage блокирует ordinary APPLY_RED (без
 * подмены принятой истории stale ALREADY_SATISFIED).
 */
export function redFinishedReceiptBlocksOrdinaryOperativeRed(
  red,
  repoRoot = DEFAULT_REPO_ROOT,
) {
  if (!isObject(red) || red.tests_frozen !== true) return false;
  if (isDurableRedAuthority(red, repoRoot)) return false;
  return isPreservedRedProvenLineageCurrent(red, repoRoot);
}

/**
 * Routing authority: preserved lineage или operative gate PASS только.
 */
export function pipelineRedAuthoritySatisfied(red, repoRoot = DEFAULT_REPO_ROOT) {
  if (!isObject(red)) return false;
  if (isPreservedRedProvenLineageCurrent(red, repoRoot)) return true;
  return isDurableRedAuthority(red, repoRoot);
}

function durableRedAuthorityStatusIs(map, id, opts = {}) {
  const red = map && map[id];
  const repoRoot = opts.repoRoot || DEFAULT_REPO_ROOT;
  const implement = opts.implementBySlice?.[id];
  const linkedGreen = opts.green ?? null;
  if (!red) return false;
  if (isObject(implement) && implement.status === IMPLEMENTATION_WRITTEN) {
    if (red.status === RED_PROVEN) {
      if (isHistoricalRedProvenBound(red, implement, repoRoot)) {
        return true;
      }
      if (implementRedLinkageComplete(implement)) {
        if (isHistoricalRedProvenBound(red, implement, repoRoot)) {
          return true;
        }
        return (
          classifyPostImplementGreenRecovery({
            red,
            implement,
            green: linkedGreen,
            repoRoot,
          }).disposition === "GREEN_REFRESH_AUTHORIZED"
        );
      }
      return false;
    }
    if (red.status === ALREADY_SATISFIED) {
      if (isHistoricalAlreadySatisfiedWriteBound(red, implement, repoRoot)) {
        return true;
      }
      if (implementLinksHistoricalRedProven(implement)) {
        if (isImplementLinkedHistoricalRedBound(red, implement, repoRoot, linkedGreen)) {
          return true;
        }
        return (
          classifyPostImplementGreenRecovery({
            red,
            implement,
            green: linkedGreen,
            repoRoot,
          }).disposition === "GREEN_REFRESH_AUTHORIZED"
        );
      }
    }
  }
  return pipelineRedAuthoritySatisfied(red, repoRoot);
}

const DEFAULT_REPO_ROOT = opsxDefaultProjectRoot(import.meta.url);

function implementationStatusComplete(map, id) {
  const rec = map && map[id];
  if (!isObject(rec)) return false;
  return rec.status === IMPLEMENTATION_WRITTEN || rec.status === IMPLEMENTATION_ADOPTED;
}

/** APPLY GREEN: ADOPTION requires checker-authorized IMPLEMENTATION_ADOPTED (S-EV2). */
function implementationGreenAuthorityComplete(map, id, repoRoot = DEFAULT_REPO_ROOT) {
  const rec = map && map[id];
  if (!isObject(rec)) return false;
  if (rec.status === IMPLEMENTATION_WRITTEN) return true;
  if (rec.status === IMPLEMENTATION_ADOPTED) {
    return implementationAdoptedTransitionAuthorized(rec, repoRoot);
  }
  return false;
}

/**
 * Full-frozen-suite evidence: every test_commands[] entry has its own
 * qualified execution. Schema v1 / missing command_executions is incomplete.
 */
export function isRedEvidenceComplete(red) {
  if (!isObject(red)) return false;
  const versionOk =
    red.schema_version === APPLY_RED_SCHEMA_VERSION ||
    red.evidence_contract === RED_EVIDENCE_CONTRACT;
  if (!versionOk) return false;
  const cmds = red.test_commands;
  const execs = red.command_executions;
  if (!Array.isArray(cmds) || cmds.length === 0) return false;
  if (!Array.isArray(execs) || execs.length !== cmds.length) return false;
  return execs.every((entry, i) => {
    if (!isObject(entry)) return false;
    if (entry.red_qualified !== true) return false;
    if (entry.command !== cmds[i]) return false;
    if (entry.final_classification !== RED_PROVEN_CLASSIFICATION) return false;
    if (!Array.isArray(entry.attempts) || entry.attempts.length === 0) return false;
    return true;
  });
}

/**
 * Durable RED_PROVEN for slice selection and implement pairing.
 * Status-only stubs (`{ status: "RED_PROVEN" }`) are not operative authority.
 * Receipt-shaped artifacts require complete per-command evidence and R5 gate pass.
 */
export function isDurableRedProven(red, repoRoot = DEFAULT_REPO_ROOT) {
  if (!isObject(red) || red.status !== RED_PROVEN) return false;
  const looksLikeReceipt =
    Array.isArray(red.test_commands) ||
    Array.isArray(red.command_executions) ||
    Number.isInteger(red.schema_version) ||
    typeof red.evidence_contract === "string";
  if (!looksLikeReceipt) return false;
  if (red.tests_frozen !== true) return false;
  if (!isRedEvidenceComplete(red)) return false;
  const authorize = getRedOperativeTransitionAuthorized();
  if (!authorize) return false;
  return authorize(red, repoRoot, { validationPhase: RED_VALIDATION_PHASE_OPERATIVE });
}

/**
 * Post-IMPLEMENT NORMAL branch: historical RED_PROVEN bound to IMPLEMENTATION_WRITTEN.
 * Does not require frozen tests to still fail against current product code.
 */
export function isHistoricalRedProvenBound(red, implement, repoRoot = DEFAULT_REPO_ROOT) {
  if (!isObject(red) || red.status !== RED_PROVEN) return false;
  if (!isObject(implement) || implement.status !== IMPLEMENTATION_WRITTEN) return false;
  if (red.slice_id && implement.slice_id && red.slice_id !== implement.slice_id) {
    return false;
  }
  if (red.tests_frozen !== true) return false;
  if (!isRedEvidenceComplete(red)) return false;
  if (implement.tests_modified === true || implement.tests_executed === true) {
    return false;
  }
  if (implement.next_stage && implement.next_stage !== "APPLY_GREEN") {
    return false;
  }
  const frozenMatch = implementFrozenTestIdentityMatchesRedBytes(red, implement, repoRoot);
  if (!frozenMatch.ok) {
    return false;
  }
  const rid = implement.red_identity;
  if (!isObject(rid)) return false;
  if (rid.status && rid.status !== RED_PROVEN) return false;
  if (rid.slice_id && red.slice_id && rid.slice_id !== red.slice_id) return false;
  if (rid.test_content_identity && implement.frozen_test_content_identity) {
    const resolved = resolveDeclaredFrozenTestContentIdentity(red, repoRoot);
    const lineageDigests = new Set(
      [
        implement.frozen_test_content_identity,
        ...(resolved.accepted_digests || []),
      ].filter(Boolean),
    );
    if (!lineageDigests.has(rid.test_content_identity)) {
      return false;
    }
  }
  const authorize = getRedOperativeTransitionAuthorized();
  if (!authorize) return false;
  return authorize(red, repoRoot, { validationPhase: RED_VALIDATION_PHASE_HISTORICAL });
}

/**
 * Post-adoption ADOPTION branch: ALREADY_SATISFIED bound to IMPLEMENTATION_ADOPTED.
 * Operative re-run of passing frozen tests must not invalidate adoption pairing
 * once S-EV2 transition authority is proven (mirror isHistoricalRedProvenBound).
 */
export function isHistoricalAlreadySatisfiedAdoptionBound(
  red,
  implement,
  repoRoot = DEFAULT_REPO_ROOT,
) {
  if (!isObject(red) || red.status !== ALREADY_SATISFIED) return false;
  if (!isObject(implement) || implement.status !== IMPLEMENTATION_ADOPTED) return false;
  if (red.slice_id && implement.slice_id && red.slice_id !== implement.slice_id) {
    return false;
  }
  if (red.observation_only !== true) return false;
  if (red.implementation_write_authorized !== false) return false;
  if (red.adoption_audit_eligible !== true) return false;
  if (red.tests_frozen !== true) return false;
  if (!isAlreadySatisfiedEvidenceComplete(red)) return false;
  if (implement.tests_modified === true) return false;
  const frozenDigest =
    red.test_content_identity || implement.frozen_test_content_identity || null;
  if (!frozenDigest) {
    const legacy = legacyFrozenTestContentIdentityFromRed(red, repoRoot);
    if (!legacy.ok || !legacy.digest) return false;
    if (
      implement.frozen_test_content_identity &&
      implement.frozen_test_content_identity !== legacy.digest
    ) {
      return false;
    }
  } else if (
    implement.frozen_test_content_identity &&
    implement.frozen_test_content_identity !== frozenDigest
  ) {
    return false;
  }
  const rid = implement.red_identity;
  if (!isObject(rid)) return false;
  if (rid.status && rid.status !== ALREADY_SATISFIED) return false;
  if (rid.slice_id && red.slice_id && rid.slice_id !== red.slice_id) return false;
  if (
    rid.test_content_identity &&
    implement.frozen_test_content_identity &&
    rid.test_content_identity !== implement.frozen_test_content_identity
  ) {
    return false;
  }
  if (!implementationAdoptedTransitionAuthorized(implement, repoRoot)) return false;
  const authorize = getRedOperativeTransitionAuthorized();
  if (!authorize) return false;
  return authorize(red, repoRoot, { validationPhase: RED_VALIDATION_PHASE_HISTORICAL });
}

function durableRedStatusIs(map, id) {
  return isDurableRedProven(map && map[id]);
}

/**
 * DESIGN authority freshness. Original inspected_digest is provenance only.
 * Callers MUST NOT pass a current whole-inspected digest as a stale gate.
 *
 * Prefer `repoRoot`: planning freshness is computed only via
 * classifyApplyDesignPlanningFreshness (canonical freeze algorithm). When
 * repoRoot is set, caller-supplied `planningDigest` is ignored so a bogus
 * identityDigest(fileBytes) reconstruction cannot force STALE_APPLY_DESIGN.
 * Fixture-only callers may still pass planningDigest without repoRoot.
 */
export function isApplyDesignAuthorityFresh(design, {
  planningDigest,
  protectedContextDigest,
  repoRoot,
} = {}) {
  if (!isObject(design)) {
    return { ok: false, code: "STALE_OR_INVALID_APPLY_DESIGN" };
  }
  if (design.status !== "APPLY_DESIGN_READY") {
    return { ok: false, code: "STALE_OR_INVALID_APPLY_DESIGN" };
  }
  // DESIGN next_stage is APPLY_DESIGN_REVIEW after composition strengthening;
  // legacy APPLY_SCAFFOLD next_stage still accepted for in-flight receipts.
  if (
    design.next_stage !== APPLY_DESIGN_REVIEW &&
    design.next_stage !== APPLY_SCAFFOLD
  ) {
    return { ok: false, code: "STALE_OR_INVALID_APPLY_DESIGN" };
  }
  const pid = design.planning_identity;
  if (!isObject(pid)) {
    return { ok: false, code: "STALE_OR_INVALID_APPLY_DESIGN" };
  }
  if (repoRoot) {
    const plan = classifyApplyDesignPlanningFreshness(design, { repoRoot });
    if (!plan.ok) {
      return {
        ok: false,
        code: plan.code,
        frozen_digest: plan.frozen_digest,
        current_digest: plan.current_digest,
      };
    }
  } else if (planningDigest && pid.planning_digest !== planningDigest) {
    return { ok: false, code: "STALE_APPLY_DESIGN" };
  }
  const pci = design.protected_context_identity;
  if (protectedContextDigest) {
    if (!isObject(pci) || pci.content_digest !== protectedContextDigest) {
      return { ok: false, code: "PROTECTED_CONTEXT_DRIFT" };
    }
  }
  return { ok: true };
}

/**
 * Back-compat wrapper. Ignores inspectedDigest even if callers still pass it.
 * Planning mismatch is remapped to STALE_OR_INVALID_APPLY_DESIGN for RED.
 */
export function isApplyDesignFresh(design, opts = {}) {
  const { inspectedDigest: _ignored, ...rest } = opts || {};
  void _ignored;
  const r = isApplyDesignAuthorityFresh(design, rest);
  if (!r.ok && r.code === "STALE_APPLY_DESIGN") {
    return { ok: false, code: "STALE_OR_INVALID_APPLY_DESIGN" };
  }
  return r;
}

export function isApplyDesignFreshForImplement(design, opts = {}) {
  const { inspectedDigest: _ignored, ...rest } = opts || {};
  void _ignored;
  const r = isApplyDesignAuthorityFresh(design, rest);
  if (!r.ok && r.code === "STALE_OR_INVALID_APPLY_DESIGN") {
    return { ok: false, code: "STALE_APPLY_DESIGN" };
  }
  if (!r.ok && r.code === "PROTECTED_CONTEXT_DRIFT") {
    return r;
  }
  if (!r.ok) return { ok: false, code: "STALE_APPLY_DESIGN" };
  return r;
}

export function isScaffoldReadyRecord(scaffold) {
  return Boolean(
    isObject(scaffold) &&
      scaffold.status === SCAFFOLD_READY &&
      scaffold.next_stage === "APPLY_RED",
  );
}

export function isScaffoldFresh(scaffold, { sliceId, candidateDigestAfter } = {}) {
  if (!isObject(scaffold) || scaffold.status !== SCAFFOLD_READY) {
    return { ok: false, code: "SCAFFOLD_AUTHORITY_MISSING" };
  }
  if (scaffold.next_stage !== "APPLY_RED") {
    return { ok: false, code: "STALE_SCAFFOLD" };
  }
  if (sliceId && scaffold.slice_id !== sliceId) {
    return { ok: false, code: "STALE_SCAFFOLD" };
  }
  const after = scaffold.repository_identity_after;
  if (
    candidateDigestAfter &&
    isObject(after) &&
    after.inspected_digest &&
    after.inspected_digest !== candidateDigestAfter
  ) {
    return { ok: false, code: "PIPELINE_CANDIDATE_DRIFT" };
  }
  return { ok: true };
}

/**
 * Compare current scoped candidate bytes to predecessor after-state.
 * Authorized writes of the current stage are exempt.
 */
export function validateCandidateAgainstPredecessor({
  predecessorEntries = [],
  currentEntries = [],
  authorizedWritePaths = [],
} = {}) {
  const currentByPath = new Map(
    currentEntries.map((e) => [posixPath(e.path), String(e.sha256 || "")]),
  );
  const unauthorizedChanged = [];
  for (const e of predecessorEntries) {
    const path = posixPath(e.path);
    if (!path || isPathCoveredBy(path, authorizedWritePaths)) continue;
    const cur = currentByPath.get(path);
    if (cur !== undefined && cur !== String(e.sha256 || "")) {
      unauthorizedChanged.push(path);
    }
  }
  if (unauthorizedChanged.length) {
    return {
      ok: false,
      code: "PIPELINE_CANDIDATE_DRIFT",
      paths: unauthorizedChanged,
    };
  }
  return { ok: true };
}

/**
 * Path-level drift classifier used by contract tests and stage gates.
 */
export function classifyPathDrift({
  path,
  design,
  planningPaths = [],
  authorizedWritePaths = [],
  predecessorExplains = false,
} = {}) {
  const p = posixPath(path);
  const planning = (planningPaths.length
    ? planningPaths
    : (design?.planning_identity?.inputs || []).map((i) => i.path)
  ).map(posixPath);
  if (planning.includes(p) || isPathCoveredBy(p, planning)) {
    return { stale: true, code: "STALE_APPLY_DESIGN" };
  }
  if (isOpsxProcessContractPath(p)) {
    return { stale: false, code: "PROCESS_CONTRACT" };
  }
  const protectedPaths = collectProtectedContextPaths(design);
  if (isPathCoveredBy(p, protectedPaths) || protectedPaths.includes(p)) {
    return { stale: true, code: "PROTECTED_CONTEXT_DRIFT" };
  }
  const mutable = collectPipelineMutablePaths(design);
  if (isPathCoveredBy(p, mutable)) {
    if (predecessorExplains || isPathCoveredBy(p, authorizedWritePaths)) {
      return { stale: false, code: "AUTHORIZED_PIPELINE_MUTATION" };
    }
    return { stale: true, code: "PIPELINE_CANDIDATE_DRIFT" };
  }
  return { stale: false, code: "UNRELATED_WORKTREE" };
}

export function classifyRedFailureAfterScaffold({
  importError = false,
  missingSymbol = false,
  syntaxError = false,
  fixtureError = false,
  reachedIntendedSeam = false,
  assertionMismatch = false,
  sentinelReached = false,
  zeroIntendedTestsExecuted = false,
  wrongPattern = false,
} = {}) {
  if (syntaxError || fixtureError || zeroIntendedTestsExecuted || wrongPattern) {
    return {
      proven: false,
      classification: "TEST_SETUP_FAILURE",
      code: "TEST_SETUP_FAILURE",
      red_qualified: false,
    };
  }
  if (importError || missingSymbol) {
    return {
      proven: false,
      classification: "SCAFFOLD_DEFECT",
      code: "SCAFFOLD_DEFECT",
      red_qualified: false,
    };
  }
  if (reachedIntendedSeam && (assertionMismatch || sentinelReached)) {
    return {
      proven: true,
      classification: "INTENDED_BEHAVIOR_MISSING",
      code: null,
      red_qualified: true,
    };
  }
  return {
    proven: false,
    classification: "UNRELATED_FAILURE",
    code: "UNRELATED_FAILURE",
    red_qualified: false,
  };
}

function buildUpstreamMaps(opts = {}) {
  return {
    design: opts.design,
    repoRoot: opts.repoRoot,
    scaffoldBySlice: opts.scaffoldBySlice || {},
    redBySlice: opts.redBySlice || {},
    implementBySlice: opts.implementBySlice || {},
    greenBySlice: opts.greenBySlice || {},
    frozenTestContentBySlice: opts.frozenTestContentBySlice || {},
    liveFrozenTestContentBySlice: opts.liveFrozenTestContentBySlice || {},
    designArtifactSha256: opts.designArtifactSha256,
    scaffoldArtifactSha256BySlice: opts.scaffoldArtifactSha256BySlice || {},
    redArtifactSha256BySlice: opts.redArtifactSha256BySlice || {},
    implementArtifactSha256BySlice: opts.implementArtifactSha256BySlice || {},
    boundedVerifyBinding: opts.boundedVerifyBinding ?? null,
    boundedVerifyBindingBySlice: opts.boundedVerifyBindingBySlice || {},
  };
}

function upstreamForSlice(id, {
  design,
  repoRoot,
  scaffoldBySlice = {},
  redBySlice = {},
  implementBySlice = {},
  greenBySlice = {},
  frozenTestContentBySlice = {},
  liveFrozenTestContentBySlice = {},
  designArtifactSha256,
  scaffoldArtifactSha256BySlice = {},
  redArtifactSha256BySlice = {},
  implementArtifactSha256BySlice = {},
  boundedVerifyBinding = null,
  boundedVerifyBindingBySlice = {},
} = {}) {
  const red = redBySlice[id];
  const implement = implementBySlice[id];
  const green = greenBySlice[id];
  let liveFrozen = liveFrozenTestContentBySlice[id];
  if (!liveFrozen && repoRoot && isObject(red)) {
    const historical = resolveHistoricalLinkageFrozenTestPaths(
      red,
      implement,
      green,
      repoRoot,
    );
    const computed = computeLiveFrozenTestContentIdentity(repoRoot, {
      test_paths: historical || red.test_paths,
      fixture_paths: historical ? [] : red.fixture_paths,
    });
    if (computed.ok) liveFrozen = computed.identity;
  }
  return {
    design,
    scaffold: scaffoldBySlice[id],
    red,
    implement: implementBySlice[id],
    frozenTestContentIdentity: frozenTestContentBySlice[id],
    liveFrozenTestContentIdentity: liveFrozen,
    designArtifactSha256,
    scaffoldArtifactSha256: scaffoldArtifactSha256BySlice[id],
    redArtifactSha256: redArtifactSha256BySlice[id],
    implementArtifactSha256: implementArtifactSha256BySlice[id],
    boundedVerifyBinding:
      boundedVerifyBindingBySlice[id] ?? boundedVerifyBinding ?? undefined,
    repoRoot,
  };
}

function unmetGreenDeps(slice, greenBySlice, upstreamMaps = {}) {
  return (slice?.dependencies_on_prior_slices || []).filter(
    (d) => !currentGreenStatusIs(greenBySlice, d, upstreamForSlice(d, upstreamMaps)),
  );
}

export function selectNextScaffoldableSlice(opts = {}) {
  const {
    design,
    scaffoldBySlice = {},
    greenBySlice = {},
  } = opts;
  if (!isObject(design) || design.status !== "APPLY_DESIGN_READY") {
    return { ok: false, code: "STALE_APPLY_DESIGN", other_eligible: [] };
  }
  if (
    design.next_stage !== APPLY_DESIGN_REVIEW &&
    design.next_stage !== APPLY_SCAFFOLD
  ) {
    return { ok: false, code: "STALE_APPLY_DESIGN", other_eligible: [] };
  }
  if (!graphValid(design)) {
    return { ok: false, code: "INVALID_SLICE_GRAPH", other_eligible: [] };
  }
  // Review gate: SCAFFOLD requires APPLY_DESIGN_REVIEW_PASS (or legacy waiver
  // only when opts.designReviewPass === true is explicitly supplied by caller
  // after evaluateApplyDesignReviewGate).
  if (opts.requireDesignReview !== false) {
    const review = opts.designReview;
    if (!review || review.status !== APPLY_DESIGN_REVIEW_PASS) {
      return {
        ok: false,
        code: review?.status === REPAIRS_REQUIRED
          ? "DESIGN_REVIEW_REPAIRS_REQUIRED"
          : "DESIGN_REVIEW_MISSING_OR_STALE",
        other_eligible: [],
      };
    }
    const gate = evaluateApplyDesignReviewGate({
      design,
      review,
      repoRoot: opts.repoRoot,
      currentDesignArtifactSha256: opts.designArtifactSha256,
      currentPlanningDigest: design?.planning_identity?.planning_digest,
      currentProtectedContextDigest: design?.protected_context_identity?.content_digest,
    });
    if (!gate.ok) {
      return {
        ok: false,
        code: gate.code || STALE_APPLY_DESIGN_REVIEW,
        other_eligible: [],
        review_gate: gate,
      };
    }
  }
  const upstreamMaps = buildUpstreamMaps(opts);
  const byId = new Map(design.slices.map((s) => [s.slice_id, s]));
  const eligible = [];
  const waiting = [];
  for (const id of design.slice_order.nodes) {
    const slice = byId.get(id);
    if (!slice) continue;
    if (statusIs(scaffoldBySlice, id, SCAFFOLD_READY)) continue;
    const unmet = unmetGreenDeps(slice, greenBySlice, upstreamMaps);
    if (unmet.length) {
      waiting.push({ slice_id: id, unmet_dependencies: unmet });
      continue;
    }
    eligible.push(id);
  }
  if (eligible.length === 0) {
    if (waiting.length) {
      return {
        ok: false,
        code: "DEPENDENCY_NOT_GREEN",
        waiting,
        other_eligible: [],
        slice_id: waiting[0].slice_id,
        unmet_dependencies: waiting[0].unmet_dependencies,
      };
    }
    return { ok: false, code: "NO_ELIGIBLE_SLICE", other_eligible: [] };
  }
  return {
    ok: true,
    slice_id: eligible[0],
    other_eligible: eligible.slice(1),
  };
}

/**
 * RED eligibility: SCAFFOLD_READY, not RED_PROVEN, deps GREEN_PROVEN.
 */
export function selectNextEligibleSlice(opts = {}) {
  const {
    design,
    scaffoldBySlice = {},
    redBySlice = {},
    greenBySlice = {},
    implementBySlice = {},
    repoRoot = DEFAULT_REPO_ROOT,
  } = opts;
  if (!isObject(design) || design.status !== "APPLY_DESIGN_READY") {
    return { ok: false, code: "STALE_OR_INVALID_APPLY_DESIGN", other_eligible: [] };
  }
  if (
    design.next_stage !== APPLY_DESIGN_REVIEW &&
    design.next_stage !== APPLY_SCAFFOLD
  ) {
    return { ok: false, code: "STALE_OR_INVALID_APPLY_DESIGN", other_eligible: [] };
  }
  if (!graphValid(design)) {
    return { ok: false, code: "INVALID_SLICE_GRAPH", other_eligible: [] };
  }
  const upstreamMaps = buildUpstreamMaps(opts);
  const byId = new Map(design.slices.map((s) => [s.slice_id, s]));
  const eligible = [];
  const waiting = [];
  const missingScaffold = [];
  for (const id of design.slice_order.nodes) {
    const slice = byId.get(id);
    if (!slice) continue;
    if (durableRedAuthorityStatusIs(redBySlice, id, {
      implementBySlice,
      green: greenBySlice[id],
      repoRoot,
    })) {
      continue;
    }
    const unmet = unmetGreenDeps(slice, greenBySlice, upstreamMaps);
    if (unmet.length) {
      waiting.push({ slice_id: id, unmet_dependencies: unmet });
      continue;
    }
    if (!statusIs(scaffoldBySlice, id, SCAFFOLD_READY)) {
      missingScaffold.push(id);
      continue;
    }
    eligible.push(id);
  }
  if (eligible.length === 0) {
    // Deps-met slices missing scaffold are the next work; do not hide them
    // behind a later slice that is only waiting on GREEN.
    if (missingScaffold.length) {
      return {
        ok: false,
        code: "SCAFFOLD_AUTHORITY_MISSING",
        other_eligible: [],
        slice_id: missingScaffold[0],
      };
    }
    if (waiting.length) {
      return {
        ok: false,
        code: "BLOCKED_DEPENDENCY_NOT_READY",
        waiting,
        other_eligible: [],
        slice_id: waiting[0].slice_id,
        unmet_dependencies: waiting[0].unmet_dependencies,
      };
    }
    return { ok: false, code: "NO_ELIGIBLE_SLICE", other_eligible: [] };
  }
  return {
    ok: true,
    slice_id: eligible[0],
    other_eligible: eligible.slice(1),
  };
}

export function selectNextImplementableSlice(opts = {}) {
  const {
    design,
    scaffoldBySlice = {},
    redBySlice = {},
    implementBySlice = {},
    greenBySlice = {},
  } = opts;
  if (!isObject(design) || design.status !== "APPLY_DESIGN_READY") {
    return { ok: false, code: "STALE_APPLY_DESIGN", other_eligible: [] };
  }
  if (!graphValid(design)) {
    return { ok: false, code: "INVALID_SLICE_GRAPH", other_eligible: [] };
  }
  const upstreamMaps = buildUpstreamMaps(opts);
  const byId = new Map(design.slices.map((s) => [s.slice_id, s]));
  const eligible = [];
  const waitingGreen = [];
  const missingAuth = [];
  for (const id of design.slice_order.nodes) {
    const slice = byId.get(id);
    if (!slice) continue;
    if (implementationStatusComplete(implementBySlice, id)) continue;
    const unmet = unmetGreenDeps(slice, greenBySlice, upstreamMaps);
    const hasRed = durableRedAuthorityStatusIs(redBySlice, id, {
      implementBySlice,
      green: greenBySlice[id],
      repoRoot,
    });
    const hasScaffold = statusIs(scaffoldBySlice, id, SCAFFOLD_READY);
    if (!hasRed || !hasScaffold) {
      missingAuth.push({ slice_id: id, unmet_dependencies: unmet });
      continue;
    }
    if (unmet.length) {
      waitingGreen.push({ slice_id: id, unmet_dependencies: unmet });
      continue;
    }
    eligible.push(id);
  }
  if (eligible.length === 0) {
    if (waitingGreen.length) {
      return {
        ok: false,
        code: "DEPENDENCY_NOT_GREEN",
        waiting: waitingGreen,
        other_eligible: [],
        slice_id: waitingGreen[0].slice_id,
        unmet_dependencies: waitingGreen[0].unmet_dependencies,
      };
    }
    if (missingAuth.length) {
      return {
        ok: false,
        code: "IMPLEMENTATION_AUTHORITY_MISSING",
        waiting: missingAuth,
        other_eligible: [],
        slice_id: missingAuth[0].slice_id,
      };
    }
    return { ok: false, code: "NO_ELIGIBLE_SLICE", other_eligible: [] };
  }
  return {
    ok: true,
    slice_id: eligible[0],
    other_eligible: eligible.slice(1),
  };
}

export function selectNextGreenableSlice(opts = {}) {
  const {
    design,
    scaffoldBySlice = {},
    redBySlice = {},
    implementBySlice = {},
    greenBySlice = {},
    repoRoot = DEFAULT_REPO_ROOT,
  } = opts;
  if (!isObject(design) || design.status !== "APPLY_DESIGN_READY") {
    return { ok: false, code: "STALE_APPLY_DESIGN", other_eligible: [] };
  }
  if (!graphValid(design)) {
    return { ok: false, code: "INVALID_SLICE_GRAPH", other_eligible: [] };
  }
  const upstreamMaps = buildUpstreamMaps(opts);
  const byId = new Map(design.slices.map((s) => [s.slice_id, s]));
  const eligible = [];
  const waitingGreen = [];
  for (const id of design.slice_order.nodes) {
    const slice = byId.get(id);
    if (!slice) continue;
    // Freshness before completeness: stale GREEN_PROVEN does not skip the slice.
    if (currentGreenStatusIs(greenBySlice, id, upstreamForSlice(id, upstreamMaps))) {
      continue;
    }
    const ready =
      statusIs(scaffoldBySlice, id, SCAFFOLD_READY) &&
      durableRedAuthorityStatusIs(redBySlice, id, {
        implementBySlice,
        green: greenBySlice[id],
        repoRoot,
      }) &&
      implementationGreenAuthorityComplete(implementBySlice, id, repoRoot);
    if (!ready) continue;
    const unmet = unmetGreenDeps(slice, greenBySlice, upstreamMaps);
    if (unmet.length) {
      waitingGreen.push({ slice_id: id, unmet_dependencies: unmet });
      continue;
    }
    eligible.push(id);
  }
  if (eligible.length === 0) {
    if (waitingGreen.length) {
      return {
        ok: false,
        code: "DEPENDENCY_NOT_GREEN",
        waiting: waitingGreen,
        other_eligible: [],
        slice_id: waitingGreen[0].slice_id,
        unmet_dependencies: waitingGreen[0].unmet_dependencies,
      };
    }
    for (const id of design.slice_order.nodes) {
      const slice = byId.get(id);
      if (!slice) continue;
      if (currentGreenStatusIs(greenBySlice, id, upstreamForSlice(id, upstreamMaps))) {
        continue;
      }
      const red = redBySlice[id];
      const implement = implementBySlice[id];
      if (!red || !implement) continue;
      const recovery = classifyPostImplementGreenRecovery({
        red,
        implement,
        green: greenBySlice[id],
        repoRoot,
      });
      if (recovery.disposition === "GREEN_REFRESH_AUTHORIZED") {
        return {
          ok: false,
          code: "GREEN_REFRESH_REQUIRED",
          slice_id: id,
          recovery,
          other_eligible: [],
          next_command: `/opsx-apply-green change=${design.change_id || ""} slice=${id}`,
        };
      }
    }
    const staleSliceIds = design.slice_order.nodes.filter(
      (id) => !currentGreenStatusIs(greenBySlice, id, upstreamForSlice(id, upstreamMaps)),
    );
    const recoveryBlocked = collectPostImplementRecoveryBlocked({
      sliceIds: staleSliceIds,
      redBySlice,
      implementBySlice,
      repoRoot,
    });
    if (recoveryBlocked.length) {
      return resolveRecoveryBlockedGreenPick(recoveryBlocked, {
        design,
        redBySlice,
        implementBySlice,
        repoRoot,
      });
    }
    return { ok: false, code: "NO_ELIGIBLE_SLICE", other_eligible: [] };
  }
  return {
    ok: true,
    slice_id: eligible[0],
    other_eligible: eligible.slice(1),
  };
}

/**
 * Receipt stays valid across unrelated edits. A bound planning_digest that
 * differs from the current APPLY-DESIGN snapshot means this slice stage must
 * run again. Missing digest does not invent a mismatch.
 */
function sliceReceiptMatchesCurrentDesign(record, design) {
  const bound = record?.apply_design_identity?.planning_digest;
  const current = design?.planning_identity?.planning_digest;
  if (!bound || !current) return true;
  return bound === current;
}

/**
 * After GREEN_PROVEN, pick the earliest missing stage on the next ready slice.
 * When any slice still needs SCAFFOLD and review is not PASS, next stage is
 * APPLY_DESIGN_REVIEW. All-green still yields APPLY_COMPLETE without a review
 * argument (review already happened before the first scaffold).
 */
export function selectNextPipelineStage(opts = {}) {
  const {
    design,
    scaffoldBySlice = {},
    redBySlice = {},
    implementBySlice = {},
    greenBySlice = {},
    designReview = null,
    repoRoot = DEFAULT_REPO_ROOT,
  } = opts;
  if (!isObject(design) || !graphValid(design)) {
    return { ok: false, code: "INVALID_SLICE_GRAPH" };
  }
  const upstreamMaps = buildUpstreamMaps(opts);
  const byId = new Map(design.slices.map((s) => [s.slice_id, s]));
  const allGreen = design.slice_order.nodes.every((id) =>
    currentGreenStatusIs(greenBySlice, id, upstreamForSlice(id, upstreamMaps)),
  );
  if (allGreen) {
    return { ok: true, next_stage: "APPLY_COMPLETE", next_slice: null };
  }
  const needsScaffold = design.slice_order.nodes.some(
    (id) => !statusIs(scaffoldBySlice, id, SCAFFOLD_READY),
  );
  if (
    needsScaffold &&
    (!designReview || designReview.status !== APPLY_DESIGN_REVIEW_PASS)
  ) {
    return {
      ok: true,
      next_stage: APPLY_DESIGN_REVIEW,
      next_slice: null,
      review_status: designReview?.status || "MISSING",
    };
  }
  for (const id of design.slice_order.nodes) {
    if (currentGreenStatusIs(greenBySlice, id, upstreamForSlice(id, upstreamMaps))) {
      continue;
    }
    const slice = byId.get(id);
    const unmet = unmetGreenDeps(slice, greenBySlice, upstreamMaps);
    if (unmet.length) continue;
    if (!statusIs(scaffoldBySlice, id, SCAFFOLD_READY)) continue;
    if (!sliceReceiptMatchesCurrentDesign(scaffoldBySlice[id], design)) continue;
    const staleRed = redBySlice[id];
    if (isOperativeRedReceiptStale(staleRed, repoRoot)) {
      return {
        ok: true,
        next_stage: RECOVERY_PAUSED,
        next_slice: id,
        recovery_code: "RED_OPERATIVE_RECEIPT_STALE",
        disposition: "PAUSE",
        selector_parity: {
          code: "RED_OPERATIVE_RECEIPT_STALE",
          disposition: "PAUSE",
          missing_provenance:
            "historical_red_proven_receipt or durable operative RED validation",
        },
        authorized_dispatch: false,
      };
    }
  }
  for (const id of design.slice_order.nodes) {
    if (currentGreenStatusIs(greenBySlice, id, upstreamForSlice(id, upstreamMaps))) {
      continue;
    }
    const slice = byId.get(id);
    const unmet = unmetGreenDeps(slice, greenBySlice, upstreamMaps);
    if (unmet.length) continue;
    if (
      !statusIs(scaffoldBySlice, id, SCAFFOLD_READY) ||
      !sliceReceiptMatchesCurrentDesign(scaffoldBySlice[id], design)
    ) {
      return { ok: true, next_stage: APPLY_SCAFFOLD, next_slice: id };
    }
    if (!sliceReceiptMatchesCurrentDesign(redBySlice[id], design)) {
      return { ok: true, next_stage: "APPLY_RED", next_slice: id };
    }
    if (!durableRedAuthorityStatusIs(redBySlice, id, {
      implementBySlice,
      green: greenBySlice[id],
      repoRoot,
    })) {
      const red = redBySlice[id];
      const implement = implementBySlice[id];
      const blocked = collectPostImplementRecoveryBlocked({
        sliceIds: [id],
        redBySlice,
        implementBySlice,
        repoRoot,
      });
      const recoveryEntry = blocked[0];
      if (
        recoveryEntry &&
        postImplementRecoveryBlocksOperativeRedRoute({
          red,
          implement,
          recovery: recoveryEntry.recovery,
          repoRoot,
        })
      ) {
        const selectorPick = resolveRecoveryBlockedGreenPick(blocked, {
          design,
          redBySlice,
          implementBySlice,
          repoRoot,
        });
        return {
          ok: true,
          next_stage: RECOVERY_PAUSED,
          next_slice: id,
          recovery_code: selectorPick.code,
          disposition: selectorPick.disposition,
          recovery: selectorPick.recovery,
          selector_parity: selectorPick,
          authorized_dispatch: false,
        };
      }
      if (isOperativeRedReceiptStale(red, repoRoot)) {
        return {
          ok: true,
          next_stage: RECOVERY_PAUSED,
          next_slice: id,
          recovery_code: "RED_OPERATIVE_RECEIPT_STALE",
          disposition: "PAUSE",
          selector_parity: {
            code: "RED_OPERATIVE_RECEIPT_STALE",
            disposition: "PAUSE",
            missing_provenance:
              "historical_red_proven_receipt or durable operative RED validation",
          },
          authorized_dispatch: false,
        };
      }
      if (redFinishedReceiptBlocksOrdinaryOperativeRed(red, repoRoot)) {
        return {
          ok: true,
          next_stage: "APPLY_IMPLEMENT",
          next_slice: id,
          selector_parity: {
            code: "PRESERVED_RED_PROVEN_LINEAGE",
            disposition: "IMPLEMENT_AFTER_PRESERVED_RED",
          },
        };
      }
      return { ok: true, next_stage: "APPLY_RED", next_slice: id };
    }
    if (!sliceReceiptMatchesCurrentDesign(implementBySlice[id], design)) {
      return { ok: true, next_stage: "APPLY_IMPLEMENT", next_slice: id };
    }
    if (!implementationGreenAuthorityComplete(implementBySlice, id, repoRoot)) {
      return { ok: true, next_stage: "APPLY_IMPLEMENT", next_slice: id };
    }
    return { ok: true, next_stage: "APPLY_GREEN", next_slice: id };
  }
  const remaining = design.slice_order.nodes.filter(
    (id) => !currentGreenStatusIs(greenBySlice, id, upstreamForSlice(id, upstreamMaps)),
  );
  return {
    ok: true,
    next_stage: APPLY_SCAFFOLD,
    next_slice: remaining[0] || null,
  };
}

/**
 * Каноническая идентичность APPLY-DESIGN для привязки review (не git blob).
 *
 * Использует planning_digest, protected_context_digest, slice_semantic_digest
 * и fileSha256 байтов APPLY-DESIGN.json на диске. reviewed_head / git object
 * sha НЕ являются authority.
 *
 * @param {object} design — распарсенный APPLY-DESIGN.json
 * @param {{ repoRoot?: string, artifactRelPath?: string, artifactSha256?: string }} [opts]
 * @returns {{ ok: true, identity: object } | { ok: false, code: string }}
 */
export function computeCanonicalApplyDesignIdentity(design, {
  repoRoot = null,
  artifactRelPath = null,
  artifactSha256 = null,
} = {}) {
  if (!isObject(design) || design.status !== "APPLY_DESIGN_READY") {
    return { ok: false, code: "STALE_OR_INVALID_APPLY_DESIGN" };
  }
  const planning_digest = design.planning_identity?.planning_digest;
  const protected_context_digest = design.protected_context_identity?.content_digest;
  const slice_semantic_digest = sliceSemanticDigest(design);
  if (!planning_digest || !protected_context_digest || !slice_semantic_digest) {
    return { ok: false, code: "MISSING_APPLY_DESIGN_IDENTITY" };
  }
  let artifact_sha256 = artifactSha256 ? String(artifactSha256) : null;
  const rel = posixPath(
    artifactRelPath ||
      design.artifact_identity?.machine_path ||
      reviewApplyDesignArtifactRelPath(design),
  );
  if (!artifact_sha256 && repoRoot && rel) {
    const abs = resourcePath(repoRoot, rel);
    if (existsSync(abs)) {
      artifact_sha256 = fileSha256(abs);
    }
  }
  if (!artifact_sha256) {
    return { ok: false, code: "MISSING_APPLY_DESIGN_ARTIFACT_SHA256" };
  }
  const content_identity = identityDigest([
    { path: "planning_digest", sha256: planning_digest },
    { path: "protected_context_digest", sha256: protected_context_digest },
    { path: "slice_semantic_digest", sha256: slice_semantic_digest },
    { path: "artifact_sha256", sha256: artifact_sha256 },
  ]);
  return {
    ok: true,
    identity: {
      status: design.status,
      artifact_path: rel,
      planning_digest,
      protected_context_digest,
      slice_semantic_digest,
      artifact_sha256,
      content_identity,
    },
  };
}

function reviewApplyDesignArtifactRelPath(design) {
  const cr = design.planning_identity?.change_root;
  if (cr) return `${posixPath(cr).replace(/\/$/, "")}/apply-design/APPLY-DESIGN.json`;
  return null;
}

/**
 * SCAFFOLD gate: review must be PASS and bound to current canonical DESIGN identity.
 */
export function evaluateApplyDesignReviewGate({
  design,
  review,
  repoRoot = null,
  currentDesignArtifactSha256,
  currentPlanningDigest,
  currentProtectedContextDigest,
} = {}) {
  if (!isObject(design) || design.status !== "APPLY_DESIGN_READY") {
    return { ok: false, code: "STALE_OR_INVALID_APPLY_DESIGN" };
  }
  if (!isObject(review)) {
    return { ok: false, code: "DESIGN_REVIEW_MISSING" };
  }
  if (review.status === REPAIRS_REQUIRED) {
    return { ok: false, code: "DESIGN_REVIEW_REPAIRS_REQUIRED" };
  }
  if (review.status !== APPLY_DESIGN_REVIEW_PASS) {
    return { ok: false, code: "DESIGN_REVIEW_MISSING_OR_STALE" };
  }
  const bound = review.apply_design_identity;
  if (!isObject(bound)) {
    return { ok: false, code: STALE_APPLY_DESIGN_REVIEW, detail: "apply_design_identity_missing" };
  }
  const artifactRel =
    bound.artifact_path || bound.machine_path || reviewApplyDesignArtifactRelPath(design);
  const canonical = computeCanonicalApplyDesignIdentity(design, {
    repoRoot,
    artifactRelPath: artifactRel,
    artifactSha256: currentDesignArtifactSha256,
  });
  if (!canonical.ok) {
    return { ok: false, code: STALE_APPLY_DESIGN_REVIEW, detail: canonical.code };
  }
  const id = canonical.identity;
  const planning_digest = currentPlanningDigest || id.planning_digest;
  const protected_context_digest =
    currentProtectedContextDigest || id.protected_context_digest;
  const artifact_sha256 = currentDesignArtifactSha256 || id.artifact_sha256;

  if (bound.content_identity && bound.content_identity !== id.content_identity) {
    return {
      ok: false,
      code: STALE_APPLY_DESIGN_REVIEW,
      detail: "content_identity",
      canonical_content_identity: id.content_identity,
      bound_content_identity: bound.content_identity,
    };
  }
  for (const [field, live] of [
    ["planning_digest", planning_digest],
    ["protected_context_digest", protected_context_digest],
    ["artifact_sha256", artifact_sha256],
    ["slice_semantic_digest", id.slice_semantic_digest],
  ]) {
    const boundVal = bound[field];
    if (!boundVal) {
      if (field === "slice_semantic_digest") continue;
      return {
        ok: false,
        code: STALE_APPLY_DESIGN_REVIEW,
        detail: `${field}_missing`,
      };
    }
    if (boundVal !== live) {
      return {
        ok: false,
        code: STALE_APPLY_DESIGN_REVIEW,
        detail: field,
        mismatch: "mismatch",
      };
    }
  }
  if (bound.status && bound.status !== design.status) {
    return { ok: false, code: STALE_APPLY_DESIGN_REVIEW, detail: "status" };
  }
  return { ok: true, status: APPLY_DESIGN_REVIEW_PASS, canonical_identity: id };
}

export function candidateIdentityFromEntries(entries, { head = "unknown" } = {}) {
  const paths = [...new Set(entries.map((e) => posixPath(e.path)).filter(Boolean))].sort();
  return {
    head,
    inspected_paths: paths,
    inspected_digest: identityDigest(entries),
  };
}

/** SHA-256 of on-disk file bytes. */
export function fileSha256(absPath) {
  return createHash("sha256").update(readFileSync(absPath)).digest("hex");
}

export function hashRepoRelativePaths(repoRoot, paths) {
  const map = {};
  for (const rel of paths || []) {
    const p = posixPath(rel);
    if (!p) continue;
    const abs = resourcePath(repoRoot, p);
    if (existsSync(abs)) map[p] = fileSha256(abs);
  }
  return map;
}

export function applyReceiptRelPaths(changeRoot, sliceId) {
  const cr = posixPath(changeRoot).replace(/\/$/, "");
  return {
    design: `${cr}/apply-design/APPLY-DESIGN.json`,
    scaffold: `${cr}/apply-scaffold/${sliceId}/SCAFFOLD.json`,
    red: `${cr}/apply-red/${sliceId}/RED.json`,
    implement: `${cr}/apply-implement/${sliceId}/IMPLEMENTATION.json`,
    green: `${cr}/apply-green/${sliceId}/GREEN.json`,
  };
}

function readJsonAndHash(repoRoot, rel) {
  const abs = resourcePath(repoRoot, posixPath(rel));
  if (!existsSync(abs)) return { json: null, sha256: undefined };
  return {
    json: JSON.parse(readFileSync(abs, "utf8")),
    sha256: fileSha256(abs),
  };
}

/**
 * Current durable artifact identities from disk for one slice.
 * Hashes the receipt files themselves — do not infer from nested status.
 */
export function loadCurrentSliceArtifactIdentities(repoRoot, changeRoot, sliceId) {
  const rel = applyReceiptRelPaths(changeRoot, sliceId);
  const design = readJsonAndHash(repoRoot, rel.design);
  const scaffold = readJsonAndHash(repoRoot, rel.scaffold);
  const red = readJsonAndHash(repoRoot, rel.red);
  const implement = readJsonAndHash(repoRoot, rel.implement);
  const green = readJsonAndHash(repoRoot, rel.green);
  let liveFrozenTestContentIdentity = null;
  let liveFrozenTestContentOk = false;
  if (isObject(red.json)) {
    const historicalPaths = resolveHistoricalLinkageFrozenTestPaths(
      red.json,
      implement.json,
      green.json,
      repoRoot,
    );
    const frozenPaths = historicalPaths || red.json.test_paths;
    if (Array.isArray(frozenPaths)) {
      const live = computeLiveFrozenTestContentIdentity(repoRoot, {
        test_paths: frozenPaths,
        fixture_paths: historicalPaths ? [] : red.json.fixture_paths,
      });
      liveFrozenTestContentOk = live.ok;
      liveFrozenTestContentIdentity = live.identity;
    }
  }
  const historicalFrozen =
    red.json?.test_content_identity ||
    implement.json?.frozen_test_content_identity;
  return {
    design: design.json,
    scaffold: scaffold.json,
    red: red.json,
    implement: implement.json,
    green: green.json,
    designArtifactSha256: design.sha256,
    scaffoldArtifactSha256: scaffold.sha256,
    redArtifactSha256: red.sha256,
    implementArtifactSha256: implement.sha256,
    frozenTestContentIdentity: liveFrozenTestContentOk
      ? liveFrozenTestContentIdentity
      : historicalFrozen,
    liveFrozenTestContentIdentity,
    liveFrozenTestContentOk,
    historicalFrozenTestContentIdentity: historicalFrozen,
  };
}

/**
 * Load receipt maps + current file hashes for every slice in DESIGN order.
 */
export function loadChangeReceiptMaps(repoRoot, changeRoot, sliceIds) {
  const scaffoldBySlice = {};
  const redBySlice = {};
  const implementBySlice = {};
  const greenBySlice = {};
  const frozenTestContentBySlice = {};
  const scaffoldArtifactSha256BySlice = {};
  const redArtifactSha256BySlice = {};
  const implementArtifactSha256BySlice = {};
  const liveFrozenTestContentBySlice = {};
  let design = null;
  let designArtifactSha256;
  for (const id of sliceIds || []) {
    const loaded = loadCurrentSliceArtifactIdentities(repoRoot, changeRoot, id);
    if (loaded.design) {
      design = loaded.design;
      designArtifactSha256 = loaded.designArtifactSha256;
    }
    if (loaded.scaffold) {
      scaffoldBySlice[id] = loaded.scaffold;
      scaffoldArtifactSha256BySlice[id] = loaded.scaffoldArtifactSha256;
    }
    if (loaded.red) {
      redBySlice[id] = loaded.red;
      redArtifactSha256BySlice[id] = loaded.redArtifactSha256;
    }
    if (loaded.implement) {
      implementBySlice[id] = loaded.implement;
      implementArtifactSha256BySlice[id] = loaded.implementArtifactSha256;
    }
    if (loaded.green) greenBySlice[id] = loaded.green;
    if (loaded.frozenTestContentIdentity) {
      frozenTestContentBySlice[id] = loaded.frozenTestContentIdentity;
    }
    if (loaded.liveFrozenTestContentIdentity) {
      liveFrozenTestContentBySlice[id] = loaded.liveFrozenTestContentIdentity;
    }
  }
  return {
    design,
    designArtifactSha256,
    repoRoot,
    scaffoldBySlice,
    redBySlice,
    implementBySlice,
    greenBySlice,
    frozenTestContentBySlice,
    liveFrozenTestContentBySlice,
    scaffoldArtifactSha256BySlice,
    redArtifactSha256BySlice,
    implementArtifactSha256BySlice,
  };
}

/**
 * Upstream identity bundle for one slice (GREEN freshness / recovery parity).
 */
export function upstreamContextForSlice(sliceId, maps = {}) {
  return upstreamForSlice(sliceId, buildUpstreamMaps(maps));
}
