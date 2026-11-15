import { defaultRepoRootFromToolFile as opsxDefaultProjectRoot } from "./opsx-repo-root.mjs";
import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * R1 authoritative oracle: frozen APPLY-DESIGN obligations vs worker test metadata.
 *
 * Worker `@opsx-red-spec-value` identifies an assertion claim only; specification
 * authority comes from `slice.red_oracle_bindings` tied to planning revision.
 */
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  classifyGreenEvidenceFreshness,
  IMPLEMENTATION_WRITTEN,
  isDurableRedProven,
  isHistoricalRedProvenBound,
  loadChangeReceiptMaps,
  resolveHistoricalLinkageFrozenTestPaths,
  upstreamContextForSlice,
} from "./opsx-apply-pipeline-identity.mjs";

const DEFAULT_REPO_ROOT = opsxDefaultProjectRoot(import.meta.url);

function stableObligationStringify(value) {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((entry) => stableObligationStringify(entry)).join(",")}]`;
  }
  const keys = Object.keys(value).sort();
  return `{${keys
    .map((key) => `${JSON.stringify(key)}:${stableObligationStringify(value[key])}`)
    .join(",")}}`;
}

function normalizeOracleToken(value) {
  if (value === "true") return true;
  if (value === "false") return false;
  if (value === "null") return null;
  if (/^-?\d+$/.test(String(value))) return Number(value);
  const s = String(value);
  if (
    (s.startsWith("'") && s.endsWith("'")) ||
    (s.startsWith('"') && s.endsWith('"'))
  ) {
    return s.slice(1, -1);
  }
  return value;
}

function gateError(code, message, detail = undefined) {
  return { code, message, ...(detail !== undefined ? { detail } : {}) };
}

/**
 * Deterministic digest of slice behavior obligation + oracle bindings.
 */
export function computeSliceBehaviorObligationDigest(design, slice) {
  if (!slice || typeof slice !== "object") {
    return { ok: false, code: "missing_slice" };
  }
  const bindings = Array.isArray(slice.red_oracle_bindings)
    ? slice.red_oracle_bindings
    : [];
  const payload = {
    slice_id: slice.slice_id || null,
    planning_digest: design?.planning_identity?.planning_digest || null,
    required_behavior: slice.required_behavior || "",
    invariants: [...(Array.isArray(slice.invariants) ? slice.invariants : [])].sort(),
    red_oracle_bindings: bindings
      .map((entry) => ({
        test_path: String(entry.test_path || ""),
        authoritative_expected: String(entry.authoritative_expected ?? ""),
      }))
      .sort((a, b) => a.test_path.localeCompare(b.test_path)),
  };
  const digest = createHash("sha256")
    .update(stableObligationStringify(payload), "utf8")
    .digest("hex");
  return { ok: true, digest, payload };
}

export function findRedOracleBinding(slice, testPath) {
  const bindings = Array.isArray(slice?.red_oracle_bindings)
    ? slice.red_oracle_bindings
    : [];
  const normalized = String(testPath || "").replace(/\\/g, "/");
  const direct =
    bindings.find((b) => String(b.test_path || "").replace(/\\/g, "/") === normalized) || null;
  if (direct) return direct;
  const aliases =
    slice?.red_oracle_seam_aliases && typeof slice.red_oracle_seam_aliases === "object"
      ? slice.red_oracle_seam_aliases
      : null;
  if (!aliases) return null;
  const aliasTarget = aliases[normalized] || aliases[testPath];
  if (!aliasTarget) return null;
  const aliasNorm = String(aliasTarget).replace(/\\/g, "/");
  return (
    bindings.find((b) => String(b.test_path || "").replace(/\\/g, "/") === aliasNorm) || null
  );
}

/**
 * Verify receipt `behavior_obligation_identity` matches linked APPLY-DESIGN slice.
 */
export function verifyBehaviorObligationIdentity({ artifact, design, slice } = {}) {
  const errors = [];
  const computed = computeSliceBehaviorObligationDigest(design, slice);
  if (!computed.ok) {
    errors.push(gateError("SEMANTIC_ORACLE_UNPROVEN", "slice missing for obligation identity"));
    return { ok: false, errors };
  }
  const declared = artifact?.behavior_obligation_identity;
  if (!declared || typeof declared !== "object") {
    errors.push(
      gateError(
        "R1_ORACLE",
        "behavior_obligation_identity required for authoritative oracle verification",
      ),
    );
    return { ok: false, errors };
  }
  if (declared.slice_id !== slice?.slice_id) {
    errors.push(
      gateError("R1_ORACLE", "behavior_obligation_identity.slice_id mismatch", {
        declared: declared.slice_id,
        slice: slice?.slice_id,
      }),
    );
  }
  const planning = design?.planning_identity?.planning_digest || null;
  if (declared.planning_digest !== planning) {
    errors.push(
      gateError("R1_ORACLE", "behavior_obligation_identity.planning_digest stale or wrong", {
        declared: declared.planning_digest,
        design: planning,
      }),
    );
  }
  if (declared.obligation_digest !== computed.digest) {
    errors.push(
      gateError(
        "R1_ORACLE",
        "behavior_obligation_identity.obligation_digest does not match APPLY-DESIGN slice",
        { declared: declared.obligation_digest, computed: computed.digest },
      ),
    );
  }
  return { ok: errors.length === 0, errors, computed };
}

/**
 * Authoritative R1 checks for one frozen test path (operative RED / ALREADY_SATISFIED).
 */
export function evaluateAuthoritativeTestOracle({
  artifact,
  design,
  slice,
  testPath,
  oracle,
  status,
} = {}) {
  const errors = [];
  if (!design || !slice) {
    errors.push(
      gateError(
        "SEMANTIC_ORACLE_UNPROVEN",
        "linked APPLY-DESIGN slice required for authoritative oracle",
      ),
    );
    return { ok: false, errors };
  }
  const binding = findRedOracleBinding(slice, testPath);
  if (!binding || binding.authoritative_expected === undefined) {
    errors.push(
      gateError(
        "SEMANTIC_ORACLE_UNPROVEN",
        `no red_oracle_bindings entry for frozen test: ${testPath}`,
      ),
    );
    return { ok: false, errors };
  }
  if (oracle.spec === null || oracle.behavior === null) {
    errors.push(
      gateError(
        "R1_ORACLE",
        `test must declare @opsx-red-spec-value and @opsx-red-behavior-value: ${testPath}`,
      ),
    );
    return { ok: false, errors };
  }

  const authoritative = normalizeOracleToken(String(binding.authoritative_expected));
  const specVal = normalizeOracleToken(oracle.spec);
  const behaviorVal = normalizeOracleToken(oracle.behavior);

  if (String(specVal) !== String(authoritative)) {
    errors.push(
      gateError(
        "R1_ORACLE",
        "worker @opsx-red-spec-value must equal authoritative_expected from APPLY-DESIGN binding",
        { authoritative, worker_spec: specVal },
      ),
    );
  }

  const assertionExpected = /assert\.equal\([^,]+,\s*([^)]+)\)/.exec(oracle.source);
  if (assertionExpected) {
    const expectedToken = assertionExpected[1].trim();
    const expectedNorm = normalizeOracleToken(
      expectedToken === "true" || expectedToken === "false"
        ? expectedToken
        : expectedToken.replace(/['"]/g, ""),
    );
    if (String(expectedNorm) !== String(authoritative)) {
      errors.push(
        gateError(
          "R1_ORACLE",
          "assertion expected value must equal authoritative_expected from APPLY-DESIGN",
          { authoritative, assertionExpected: expectedNorm },
        ),
      );
    }
  }

  if (status === "RED_PROVEN" && String(behaviorVal) === String(authoritative)) {
    errors.push(
      gateError(
        "R1_ORACLE",
        "current behavior already satisfies authoritative obligation; use ALREADY_SATISFIED not RED_PROVEN",
        { authoritative, behavior: behaviorVal },
      ),
    );
  }

  if (status === "ALREADY_SATISFIED" && String(behaviorVal) !== String(authoritative)) {
    errors.push(
      gateError(
        "R1_ORACLE",
        "ALREADY_SATISFIED requires behavior marker to match authoritative obligation",
        { authoritative, behavior: behaviorVal },
      ),
    );
  }

  return { ok: errors.length === 0, errors, authoritative };
}

function posixPath(p) {
  return String(p || "").replace(/\\/g, "/");
}

function obligationDigestToken(value) {
  if (value == null) return null;
  if (typeof value === "string") return value;
  if (typeof value === "object" && typeof value.digest === "string") return value.digest;
  return null;
}

/**
 * Classify slice as accepted (durable RED / IMPLEMENT+RED bound / current GREEN)
 * vs future using pipeline identity predicates (read-only).
 */
export function classifyDesignSliceContractPhase(sliceId, design, { maps, repoRoot } = {}) {
  if (!sliceId || !maps || !repoRoot) return "future";
  const red = maps.redBySlice?.[sliceId];
  const implement = maps.implementBySlice?.[sliceId];
  const green = maps.greenBySlice?.[sliceId];
  if (green) {
    const upstream = upstreamContextForSlice(sliceId, maps);
    const fresh = classifyGreenEvidenceFreshness(green, {
      design,
      scaffold: upstream.scaffold,
      red: upstream.red,
      implement: upstream.implement,
      designArtifactSha256: maps.designArtifactSha256,
      scaffoldArtifactSha256: maps.scaffoldArtifactSha256BySlice?.[sliceId],
      redArtifactSha256: maps.redArtifactSha256BySlice?.[sliceId],
      implementArtifactSha256: maps.implementArtifactSha256BySlice?.[sliceId],
      frozenTestContentIdentity: maps.frozenTestContentBySlice?.[sliceId],
      liveFrozenTestContentIdentity: maps.liveFrozenTestContentBySlice?.[sliceId],
      repoRoot,
    });
    if (fresh.current) return "accepted";
  }
  if (red && implement && isHistoricalRedProvenBound(red, implement, repoRoot)) {
    return "accepted";
  }
  if (red && isDurableRedProven(red, repoRoot)) return "accepted";
  if (
    red &&
    implement?.status === IMPLEMENTATION_WRITTEN &&
    isHistoricalRedProvenBound(red, implement, repoRoot)
  ) {
    return "accepted";
  }
  return "future";
}

function buildDesignSliceValidationContext(design, { repoRoot, maps: mapsIn } = {}) {
  const root = repoRoot || DEFAULT_REPO_ROOT;
  const sliceIds = (
    design?.slice_order?.nodes ||
    (design?.slices || []).map((s) => s && s.slice_id).filter(Boolean)
  ).filter(Boolean);
  const changeRoot = design?.planning_identity?.change_root;
  const maps =
    mapsIn ||
    (changeRoot && sliceIds.length
      ? loadChangeReceiptMaps(root, changeRoot, sliceIds)
      : null);
  const acceptedSliceIds = new Set();
  if (maps) {
    for (const id of sliceIds) {
      if (classifyDesignSliceContractPhase(id, design, { maps, repoRoot: root }) === "accepted") {
        acceptedSliceIds.add(id);
      }
    }
  }
  return { repoRoot: root, maps, acceptedSliceIds };
}

function acceptedSliceContractDriftErrors(slice, { maps, repoRoot, design }) {
  const errors = [];
  if (!slice?.slice_id || !maps) return errors;
  const red = maps.redBySlice?.[slice.slice_id];
  if (!red) return errors;
  const implement = maps.implementBySlice?.[slice.slice_id];
  const green = maps.greenBySlice?.[slice.slice_id];
  const historicalPaths = resolveHistoricalLinkageFrozenTestPaths(
    red,
    implement,
    green,
    repoRoot,
  );
  const frozenPaths = (historicalPaths || red.test_paths || [])
    .map(posixPath)
    .filter(Boolean);
  const designPaths = new Set(
    [...(slice.red_test_targets_or_seams || [])].map(posixPath).filter(Boolean),
  );
  for (const testPath of frozenPaths) {
    if (!designPaths.has(testPath)) {
      errors.push({
        code: "DESIGN_ACCEPTED_SLICE_CONTRACT_DRIFT",
        slice_id: slice.slice_id,
        message:
          `accepted slice ${slice.slice_id} must retain frozen test path ${testPath} in red_test_targets_or_seams`,
      });
    }
  }
  const redPlanning = red.behavior_obligation_identity?.planning_digest;
  const designForObligation =
    redPlanning &&
    design?.planning_identity?.planning_digest &&
    redPlanning !== design.planning_identity.planning_digest
      ? {
          ...design,
          planning_identity: {
            ...design.planning_identity,
            planning_digest: redPlanning,
          },
        }
      : design;
  const computed = computeSliceBehaviorObligationDigest(designForObligation, slice);
  const declaredDigest = obligationDigestToken(red.behavior_obligation_identity?.obligation_digest);
  if (computed.ok && declaredDigest && computed.digest !== declaredDigest) {
    errors.push({
      code: "DESIGN_ACCEPTED_SLICE_CONTRACT_DRIFT",
      slice_id: slice.slice_id,
      message: `accepted slice ${slice.slice_id} oracle bindings or required_behavior drift from durable RED obligation`,
    });
  }
  if (repoRoot) {
    for (const testPath of frozenPaths) {
      if (!existsSync(resourcePath(repoRoot, testPath))) {
        errors.push({
          code: "RED_TEST_TARGET_MISSING",
          slice_id: slice.slice_id,
          message: `accepted slice ${slice.slice_id} missing on-disk frozen test ${testPath}`,
        });
      }
    }
  }
  return errors;
}

/**
 * APPLY-DESIGN technical completeness: oracle bindings per RED test target and
 * no shared frozen test module across slices (R3 freeze boundary).
 *
 * Accepted slices own frozen paths; future slices with overlapping planned paths
 * must pick new modules. Future targets are not required to exist on disk yet.
 */
export function validateDesignSliceTechnicalContracts(design, opts = {}) {
  const errors = [];
  const slices = Array.isArray(design?.slices) ? design.slices : [];
  const ctx = buildDesignSliceValidationContext(design, opts);
  const { acceptedSliceIds, maps, repoRoot } = ctx;
  const frozenPathOwners = new Map();
  const futurePathOwners = new Map();

  for (const slice of slices) {
    if (!slice || !slice.slice_id) continue;
    if (acceptedSliceIds.has(slice.slice_id)) {
      const red = maps?.redBySlice?.[slice.slice_id];
      const implement = maps?.implementBySlice?.[slice.slice_id];
      const green = maps?.greenBySlice?.[slice.slice_id];
      const historicalPaths = red
        ? resolveHistoricalLinkageFrozenTestPaths(red, implement, green, repoRoot)
        : null;
      const redFrozenPaths = historicalPaths || red?.test_paths || [];
      const paths = new Set(
        [...redFrozenPaths, ...(slice.red_test_targets_or_seams || [])]
          .map(posixPath)
          .filter(Boolean),
      );
      for (const testPath of paths) {
        frozenPathOwners.set(testPath, slice.slice_id);
      }
      for (const e of acceptedSliceContractDriftErrors(slice, { maps, repoRoot, design })) {
        errors.push(e);
      }
    }
  }

  for (const slice of slices) {
    if (!slice || !slice.slice_id) continue;
    const isAccepted = acceptedSliceIds.has(slice.slice_id);
    const targets = [...(slice.red_test_targets_or_seams || [])].map(posixPath).filter(Boolean);
    for (const testPath of targets) {
      const frozenOwner = frozenPathOwners.get(testPath);
      if (frozenOwner && frozenOwner !== slice.slice_id) {
        errors.push({
          code: "R3_FREEZE_CONFLICT",
          slice_id: slice.slice_id,
          message: `test path ${testPath} frozen by accepted slice ${frozenOwner}; assign a new test path for ${slice.slice_id}`,
        });
        continue;
      }
      const priorFuture = futurePathOwners.get(testPath);
      if (priorFuture && priorFuture !== slice.slice_id) {
        errors.push({
          code: "R3_FREEZE_CONFLICT",
          slice_id: slice.slice_id,
          message: `test path ${testPath} shared by ${priorFuture} and ${slice.slice_id}; use separate test files per slice`,
        });
      } else if (!priorFuture && !isAccepted) {
        futurePathOwners.set(testPath, slice.slice_id);
      }
      const binding = findRedOracleBinding(slice, testPath);
      if (!binding || binding.authoritative_expected === undefined) {
        errors.push({
          code: "RED_ORACLE_BINDINGS_INCOMPLETE",
          slice_id: slice.slice_id,
          message: `slice ${slice.slice_id} missing red_oracle_bindings for ${testPath}`,
        });
      }
    }
  }
  return { ok: errors.length === 0, errors, accepted_slice_ids: [...acceptedSliceIds] };
}

export { normalizeOracleToken };
