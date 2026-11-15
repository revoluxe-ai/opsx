import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * APPLY GREEN artifact validator, slice selection, frozen-RED runner, and
 * repair-loop helpers.
 *
 * Agents diagnose implementation-owned failures and write production repairs.
 * This tool checks GREEN.json, runs exact frozen RED commands when asked,
 * and does not rewrite tests or APPLY DESIGN.
 *
 * Usage:
 *   node opsx_tools/opsx-apply-green.mjs <GREEN.json>
 *   node opsx_tools/opsx-apply-green.mjs --schema <schema> <GREEN.json>
 */
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, posix, resolve } from "node:path";
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
  classifyImplementationScope,
  frozenTestIdentityMatches,
  GREEN_PROVEN,
  IMPLEMENTATION_WRITTEN,
  IMPLEMENTATION_ADOPTED,
  isApplyDesignFreshForImplement,
  isRedFresh,
  looksLikeTestPath,
} from "./opsx-apply-implement.mjs";
import {
  ALREADY_SATISFIED,
  ADOPTION_PIPELINE_BRANCH,
  ALREADY_SATISFIED_WRITE_PIPELINE_BRANCH,
  NORMAL_PIPELINE_BRANCH,
  classifyGreenEvidenceFreshness,
  classifyGreenPredecessorPairing,
  isCurrentGreenProven,
  selectNextGreenableSlice,
  selectNextPipelineStage,
} from "./opsx-apply-pipeline-identity.mjs";
import {
  collectGreenOwnedCompositionObligations,
  collectGreenCompositionSeamHints,
} from "./opsx-apply-composition.mjs";
import {
  validateGreenKnowledgeUnion,
  loadLinkedRed,
  loadImpactExpandOverlay,
} from "./opsx-bug-learning-apply.mjs";
import {
  adoptionGreenTransitionErrors,
  implementationAdoptedTransitionAuthorized,
  VALIDATE_GREEN_TRANSITION,
} from "./opsx-adoption-transition-gate.mjs";

export { VALIDATE_GREEN_TRANSITION } from "./opsx-adoption-transition-gate.mjs";

export { validateGreenKnowledgeUnion } from "./opsx-bug-learning-apply.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);
const SCHEMA_REL = "opsx_tools/registry/apply_green.schema.json";

export const DEFAULT_MAX_GREEN_FIX_ITERATIONS = 5;
export const RED_PROVEN = "RED_PROVEN";
export const SOFT_PROD_ALIAS_LOADER_REL = "tests/unit/softProdAliasLoader.mjs";
export const ADJACENT_NOT_REQUIRED = "NOT_REQUIRED";
export const ADJACENT_REQUIRED = "REQUIRED";
export const REQUIRED_BUT_NOT_EXECUTABLE = "REQUIRED_BUT_NOT_EXECUTABLE";
export const EXECUTION_MECHANISM_NODE_TEST = "node_test";
export const EXECUTION_MECHANISM_NODE_TEST_ALIAS_LOADER =
  "node_test_with_alias_loader";
export const EXECUTION_MECHANISM_POETRY_PYTEST = "poetry_pytest";
export const EXECUTION_MECHANISM_PYTHON_PYTEST = "python_pytest";

export const IMPLEMENTATION_OWNED_CLASSIFICATIONS = [
  "IMPLEMENTATION_BEHAVIOR_MISSING",
  "IMPLEMENTATION_BUG",
  "INTEGRATION_MISMATCH",
  "REGRESSION_FROM_SLICE",
];

export const MUST_NOT_REPAIR_CLASSIFICATIONS = [
  "FROZEN_RED_INVALID_OR_DRIFTED",
  "ENVIRONMENT_FAILURE",
  "TEST_INFRA_FAILURE",
  "UNRELATED_FAILURE",
  "DESIGN_REALITY_CONFLICT",
  "PLANNING_CHANGE_REQUIRED",
  "UNKNOWN",
];

export const UNBOUNDED_VERIFICATION_PATTERNS = [
  /tests\/unit\/\*\*/,
  /node --test tests\/unit$/,
  /\bpoetry\s+run\s+pytest\s+tests(?:\s|$)/,
  /\bnpm test\b/,
  /opsx-project-bug-hunt/,
  /opsx-browser-check/,
  /bugbot/i,
  /opsx-verify/,
];

export {
  identityDigest,
  freezePlanningIdentity,
  computeCurrentPlanningIdentity,
  classifyApplyDesignPlanningFreshness,
  evaluateApplyDesignFreshnessFromDisk,
  classifyImplementationScope,
  frozenTestIdentityMatches,
  GREEN_PROVEN,
  IMPLEMENTATION_WRITTEN,
  IMPLEMENTATION_ADOPTED,
  isApplyDesignFreshForImplement,
  isRedFresh,
  looksLikeTestPath,
  selectNextGreenableSlice,
  selectNextPipelineStage,
  classifyGreenEvidenceFreshness,
  isCurrentGreenProven,
  collectGreenOwnedCompositionObligations,
  collectGreenCompositionSeamHints,
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
        message: `${path}: must match exactly one APPLY GREEN status branch`,
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

export function mayEnterGreenFixLoop(classification) {
  return IMPLEMENTATION_OWNED_CLASSIFICATIONS.includes(classification);
}

export function isUnboundedVerificationCommand(command) {
  const s = String(command || "");
  return UNBOUNDED_VERIFICATION_PATTERNS.some((re) => re.test(s));
}

function tokenizeCommand(command) {
  const tokens = [];
  const re = /"([^"\\]|\\.)*"|'([^'\\]|\\.)*'|[^\s]+/g;
  let m;
  while ((m = re.exec(String(command || "")))) {
    let t = m[0];
    if ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'"))) {
      t = t.slice(1, -1).replace(/\\"/g, '"').replace(/\\'/g, "'");
    }
    tokens.push(t);
  }
  return tokens;
}

export function parseBoundedTestCommand(command) {
  const tokens = tokenizeCommand(command);
  if (tokens.length >= 2 && tokens[0] === "node" && tokens[1] === "--test") {
    return {
      ok: true,
      file: process.execPath,
      argv: tokens.slice(1),
      mechanism: EXECUTION_MECHANISM_NODE_TEST,
    };
  }
  if (
    tokens.length >= 4 &&
    tokens[0] === "poetry" &&
    tokens[1] === "run" &&
    tokens[2] === "pytest"
  ) {
    const pytestArgs = tokens.slice(3);
    const targets = pytestArgs.filter(
      (t) => !t.startsWith("-") && (t.includes("::") || /(?:^|\/)test[^/]*\.py$/.test(t)),
    );
    if (targets.length === 0 || pytestArgs.some((t) => t === "tests" || t === "tests/")) {
      return {
        ok: false,
        code: "TEST_INFRA_FAILURE",
        detail: "poetry pytest must name a bounded test file or node id",
      };
    }
    return {
      ok: true,
      file: "poetry",
      argv: ["run", "pytest", ...pytestArgs],
      mechanism: EXECUTION_MECHANISM_POETRY_PYTEST,
    };
  }
  const pythonPytest = parsePythonPytest(tokens);
  if (pythonPytest) return pythonPytest;
  return {
    ok: false,
    code: "TEST_INFRA_FAILURE",
    detail:
      "only bounded node --test, poetry run pytest, or python -m pytest is auto-runnable",
  };
}

/**
 * Target capability `quality.test` is `python -m pytest` (capabilities.yaml).
 * Poetry remains a donor compatibility path and is not the stroy-system runner.
 */
function parsePythonPytest(tokens) {
  if (tokens.length < 4) return null;
  const isPythonLauncher =
    tokens[0] === "python" ||
    tokens[0] === "python3" ||
    /(?:^|[/\\])python(?:3)?\.exe$/i.test(tokens[0]);
  if (!isPythonLauncher) return null;
  if (tokens[1] !== "-m" || tokens[2] !== "pytest") return null;
  const pytestArgs = tokens.slice(3);
  const targets = pytestArgs.filter(
    (t) => !t.startsWith("-") && (t.includes("::") || /(?:^|\/)test[^/]*\.py$/.test(t)),
  );
  if (targets.length === 0 || pytestArgs.some((t) => t === "tests" || t === "tests/")) {
    return {
      ok: false,
      code: "TEST_INFRA_FAILURE",
      detail: "python pytest must name a bounded test file or node id",
    };
  }
  return {
    ok: true,
    file: tokens[0],
    argv: ["-m", "pytest", ...pytestArgs],
    mechanism: EXECUTION_MECHANISM_PYTHON_PYTEST,
  };
}

function toPosix(p) {
  return String(p || "").replace(/\\/g, "/");
}

/**
 * Repo-relative test file from a frozen `node --test …` command string.
 */
export function extractTestPathFromNodeTestCommand(command) {
  const parsed = parseBoundedTestCommand(command);
  if (parsed.ok) {
    const files = parsed.argv.filter((t) => /\.test\.\w+$/.test(t) || /test_\S+\.py$/.test(t));
    if (files.length) return toPosix(files[files.length - 1]);
  }
  const m = String(command || "").match(/(\S+\.test\.\w+|\S+test_\S+\.py)\s*$/);
  return m ? toPosix(m[1]) : "";
}

export function aliasLoaderPath(cwd = DEFAULT_ROOT) {
  return resolve(cwd, SOFT_PROD_ALIAS_LOADER_REL);
}

export function aliasLoaderAvailable(cwd = DEFAULT_ROOT) {
  return existsSync(aliasLoaderPath(cwd));
}

/**
 * True when Node failed because production `@/` (Vue CLI) specifiers were unresolved.
 */
export function looksLikeAliasResolutionFailure(output) {
  const text = String(output || "");
  return (
    /Cannot find package ['"]@\//i.test(text) ||
    /ERR_MODULE_NOT_FOUND[\s\S]*['"]@\//i.test(text) ||
    /Cannot find module ['"]@\//i.test(text)
  );
}

function resolveProductionSpecifier(fromRel, spec) {
  const from = toPosix(fromRel);
  if (spec.startsWith("@/")) {
    let rel = `src/${spec.slice(2)}`;
    if (!/\.[a-zA-Z0-9]+$/.test(rel)) rel += ".js";
    return toPosix(rel);
  }
  if (spec.startsWith(".")) {
    let rel = posix.normalize(`${posix.dirname(from)}/${spec}`);
    if (rel.startsWith("./")) rel = rel.slice(2);
    if (!/\.[a-zA-Z0-9]+$/.test(rel)) rel += ".js";
    return toPosix(rel);
  }
  return null;
}

function importSpecifiers(source) {
  const specs = [];
  const re = /(?:from|import)\s+['"]([^'"]+)['"]/g;
  let m;
  while ((m = re.exec(String(source || "")))) specs.push(m[1]);
  return specs;
}

function productionImportsFrom(relPath, { cwd = DEFAULT_ROOT } = {}) {
  const abs = resolve(cwd, relPath);
  if (!existsSync(abs)) return [];
  const specs = importSpecifiers(readFileSync(abs, "utf8"));
  const out = [];
  for (const spec of specs) {
    const resolved = resolveProductionSpecifier(relPath, spec);
    if (resolved && resolved.startsWith("src/")) out.push(resolved);
  }
  return out;
}

/**
 * Whether a unit test (or a bounded hop into `src/`) uses Vue `@/` imports.
 */
export function testFileNeedsProdAlias(testRel, { cwd = DEFAULT_ROOT, depth = 0, seen } = {}) {
  const rel = toPosix(testRel);
  if (!rel) return false;
  const vis = seen || new Set();
  if (vis.has(rel) || depth > 4) return false;
  vis.add(rel);
  const abs = resolve(cwd, rel);
  if (!existsSync(abs)) return false;
  const text = readFileSync(abs, "utf8");
  if (/from\s+['"]@\//.test(text) || /import\s+['"]@\//.test(text)) return true;
  for (const hop of productionImportsFrom(rel, { cwd })) {
    if (testFileNeedsProdAlias(hop, { cwd, depth: depth + 1, seen: vis })) return true;
  }
  return false;
}

function withAliasLoaderArgv(argv, { cwd = DEFAULT_ROOT } = {}) {
  if (!aliasLoaderAvailable(cwd)) return argv;
  // Prefer repo-relative loader path so Node ESM hooks stay on file:// URLs
  // under Windows (absolute Win paths without file:// break the loader chain).
  return [
    "--experimental-loader",
    `./${SOFT_PROD_ALIAS_LOADER_REL.replace(/\\/g, "/")}`,
    ...argv,
  ];
}

/**
 * Semantic slice surfaces for adjacent applicability (not execution).
 */
export function collectSliceSemanticSurfaces(slice = {}) {
  const paths = [];
  const push = (p) => {
    const n = toPosix(p);
    if (n && !n.startsWith("tests/") && !paths.includes(n)) paths.push(n);
  };
  for (const p of slice.expected_product_write_surfaces || []) push(p);
  for (const p of slice.relevant_existing_surfaces || []) push(p);
  for (const p of slice.red_test_targets_or_seams || []) push(p);
  return paths;
}

/**
 * One-hop production modules imported by slice write surfaces (`src/utils`, `src/api`).
 */
export function collectWriteSurfaceOneHopModules(slice = {}, { cwd = DEFAULT_ROOT } = {}) {
  const hops = [];
  for (const ws of slice.expected_product_write_surfaces || []) {
    for (const hop of productionImportsFrom(ws, { cwd })) {
      if (!hop.startsWith("src/utils/") && !hop.startsWith("src/api/")) continue;
      if (!hops.includes(hop)) hops.push(hop);
    }
  }
  return hops;
}

function candidateReferencesSurface(testText, surface) {
  const s = toPosix(surface);
  if (!s) return false;
  if (testText.includes(s)) return true;
  if (s.startsWith("src/") && testText.includes(`../../${s}`)) return true;
  if (s.startsWith("src/")) {
    const alias = `@/${s.slice(4).replace(/\.js$/, "")}`;
    if (testText.includes(alias)) return true;
  }
  return false;
}

/**
 * Test paths named as RED seams on slices after `sliceId` in `slice_order.nodes`.
 * Those are future-slice obligations, not this slice's adjacent checks.
 */
export function laterSliceRedTestPaths({ design, sliceId } = {}) {
  const nodes =
    (design && design.slice_order && Array.isArray(design.slice_order.nodes)
      ? design.slice_order.nodes
      : (design && design.slices ? design.slices.map((s) => s.slice_id) : [])) || [];
  const idx = nodes.indexOf(sliceId);
  if (idx < 0) return [];
  const later = new Set(nodes.slice(idx + 1));
  const paths = [];
  for (const s of (design && design.slices) || []) {
    if (!later.has(s.slice_id)) continue;
    for (const p of s.red_test_targets_or_seams || []) {
      const n = toPosix(p);
      if (n.startsWith("tests/") && !paths.includes(n)) paths.push(n);
    }
  }
  return paths;
}

/**
 * Semantic applicability of one candidate unit test to a slice.
 * Frozen RED paths are not adjacent. Missing files are not a launcher waiver.
 * Later-slice named RED seams are not this slice's adjacent obligation.
 */
export function classifyAdjacentApplicability({
  slice = {},
  candidatePath,
  frozenRedTestPaths = [],
  laterSliceTestPaths = [],
  compositionSeamHints = [],
  cwd = DEFAULT_ROOT,
} = {}) {
  const cand = toPosix(candidatePath);
  if (!cand) {
    return { applicability: ADJACENT_NOT_REQUIRED, reason: "NO_CANDIDATE" };
  }
  const frozen = (frozenRedTestPaths || []).map(toPosix);
  if (frozen.includes(cand)) {
    return { applicability: "NOT_ADJACENT", reason: "FROZEN_RED_PATH" };
  }
  if ((laterSliceTestPaths || []).map(toPosix).includes(cand)) {
    return { applicability: ADJACENT_NOT_REQUIRED, reason: "FUTURE_SLICE_RED_SEAM" };
  }
  const compositionSeams = (compositionSeamHints || []).map(toPosix);
  if (compositionSeams.includes(cand)) {
    return {
      applicability: ADJACENT_REQUIRED,
      reason: "COMPOSITION_OBLIGATION",
    };
  }
  const namedTestSeams = (slice.red_test_targets_or_seams || [])
    .map(toPosix)
    .filter((p) => p.startsWith("tests/"));
  const surfaces = [
    ...collectSliceSemanticSurfaces(slice),
    ...collectWriteSurfaceOneHopModules(slice, { cwd }),
  ];
  const abs = resolve(cwd, cand);
  if (!existsSync(abs)) {
    if (namedTestSeams.includes(cand) || compositionSeams.includes(cand)) {
      return { applicability: ADJACENT_NOT_REQUIRED, reason: "NAMED_SEAM_NOT_MATERIALIZED" };
    }
    return { applicability: ADJACENT_NOT_REQUIRED, reason: "CANDIDATE_NOT_MATERIALIZED" };
  }
  if (namedTestSeams.includes(cand)) {
    return { applicability: ADJACENT_REQUIRED, reason: "RED_TEST_TARGET_SEAM" };
  }
  const testText = readFileSync(abs, "utf8");
  for (const surface of surfaces) {
    if (candidateReferencesSurface(testText, surface)) {
      return { applicability: ADJACENT_REQUIRED, reason: "SURFACE_OR_ONE_HOP_OVERLAP" };
    }
  }
  return { applicability: ADJACENT_NOT_REQUIRED, reason: "NO_OVERLAPPING_SEAMS" };
}

/**
 * Execution capability for a semantically required bounded check.
 * Does not change applicability. Missing `@/` loader when needed is a blocker.
 */
export function resolveAdjacentExecutionMechanism(command, { cwd = DEFAULT_ROOT } = {}) {
  const parsed = parseBoundedTestCommand(command);
  if (!parsed.ok) {
    return {
      executable: false,
      status: REQUIRED_BUT_NOT_EXECUTABLE,
      code: parsed.code || "TEST_INFRA_FAILURE",
      mechanism: "none",
      alias_loader: false,
      detail: parsed.detail || "no supported execution mechanism",
    };
  }
  const testPath = extractTestPathFromNodeTestCommand(command);
  const needsAlias = testFileNeedsProdAlias(testPath, { cwd });
  const loader = aliasLoaderAvailable(cwd);
  if (needsAlias && !loader) {
    return {
      executable: false,
      status: REQUIRED_BUT_NOT_EXECUTABLE,
      code: "TEST_INFRA_FAILURE",
      mechanism: "none",
      alias_loader: false,
      detail: `${SOFT_PROD_ALIAS_LOADER_REL} required for @/ imports and is missing`,
    };
  }
  return {
    executable: true,
    status: "EXECUTABLE",
    code: null,
    mechanism: loader
      ? EXECUTION_MECHANISM_NODE_TEST_ALIAS_LOADER
      : EXECUTION_MECHANISM_NODE_TEST,
    alias_loader: loader,
    detail: loader
      ? `register ${SOFT_PROD_ALIAS_LOADER_REL}`
      : "node --test",
  };
}

/**
 * Combine semantic applicability with an execution result.
 * Plain-node `@/` failure of a REQUIRED check is never NOT_REQUIRED.
 */
export function finalizeAdjacentCheckDisposition({
  applicability,
  execution,
  output = "",
} = {}) {
  if (applicability === ADJACENT_NOT_REQUIRED || applicability === "NOT_ADJACENT") {
    return { adjacent_final_status: ADJACENT_NOT_REQUIRED, block: null };
  }
  if (!execution || execution.executable === false) {
    return {
      adjacent_final_status: REQUIRED_BUT_NOT_EXECUTABLE,
      block: {
        code: (execution && execution.code) || "TEST_INFRA_FAILURE",
        detail:
          (execution && execution.detail) ||
          "relevant adjacent check has no supported execution mechanism",
      },
    };
  }
  if (looksLikeAliasResolutionFailure(output) && !execution.alias_loader) {
    return {
      adjacent_final_status: REQUIRED_BUT_NOT_EXECUTABLE,
      block: {
        code: "TEST_INFRA_FAILURE",
        detail:
          "plain node --test @/ alias failure is not NOT_REQUIRED; use tests/unit/softProdAliasLoader.mjs",
      },
    };
  }
  if (looksLikeAliasResolutionFailure(output) && execution.alias_loader) {
    return {
      adjacent_final_status: REQUIRED_BUT_NOT_EXECUTABLE,
      block: {
        code: "TEST_INFRA_FAILURE",
        detail: "alias-dependent adjacent check still failed with the supported loader",
      },
    };
  }
  return { adjacent_final_status: "PASS", block: null };
}

/**
 * Execute one frozen RED or bounded focused/adjacent `node --test` command.
 * Deterministic subprocess, not an agent claim.
 *
 * Target vue-npm: when `tests/unit/softProdAliasLoader.mjs` exists, register it
 * so Soft-prod `@/` imports resolve under Node ESM. The frozen command string
 * stays exact (`node --test …`); only the subprocess argv gains the loader.
 */
function pythonLauncherImportsPytest(launcher, cwd) {
  const probe = spawnSync(launcher, ["-c", "import pytest"], {
    cwd,
    encoding: "utf8",
    timeout: 30000,
  });
  return probe.status === 0;
}

/**
 * Находит pytest-capable Python: env OPSX_PYTEST_PYTHON, repo venv, sibling stroy-system.
 */
function resolveRepoPytestLauncher(cwd) {
  const fromEnv = process.env.OPSX_PYTEST_PYTHON;
  if (fromEnv && existsSync(fromEnv) && pythonLauncherImportsPytest(fromEnv, cwd)) {
    return fromEnv;
  }
  const candidates = [
    resolve(cwd, ".venv_win/Scripts/python.exe"),
    resolve(cwd, ".venv/Scripts/python.exe"),
    resolve(cwd, ".venv-ae/Scripts/python.exe"),
    resolve(cwd, "../stroy-system/.venv/Scripts/python.exe"),
  ];
  for (const py of candidates) {
    if (existsSync(py) && pythonLauncherImportsPytest(py, cwd)) {
      return py;
    }
  }
  if (process.platform === "win32") {
    for (const launcher of [
      "C:\\Program Files\\Python312\\python.exe",
      "C:\\Program Files\\Python313\\python.exe",
    ]) {
      if (existsSync(launcher) && pythonLauncherImportsPytest(launcher, cwd)) {
        return launcher;
      }
    }
  }
  return null;
}

function poetryPytestHealthy(cwd) {
  const probe = spawnSync("poetry", ["run", "python", "-c", "import pytest"], {
    cwd,
    encoding: "utf8",
    timeout: 45000,
  });
  return probe.status === 0;
}

function preferPoetryPytestCommand(command, cwd) {
  const cmd = String(command || "");
  const launcher = resolveRepoPytestLauncher(cwd);
  if (launcher) {
    const normalized =
      process.platform === "win32" ? launcher.replace(/\\/g, "/") : launcher;
    if (/^poetry\s+run\s+pytest\b/.test(cmd)) {
      const pytestPart = cmd.replace(/^poetry\s+run\s+pytest\s*/, "");
      return `"${normalized}" -m pytest ${pytestPart}`;
    }
    if (/^python3? -m pytest\b/.test(cmd)) {
      return cmd.replace(/^python3? -m pytest/, `${normalized} -m pytest`);
    }
  }
  if (/^python3? -m pytest\b/.test(cmd)) {
    if (poetryPytestHealthy(cwd)) {
      return `poetry run ${cmd.replace(/^python3? -m pytest/, "pytest")}`;
    }
    return cmd;
  }
  if (/^poetry\s+run\s+pytest\b/.test(cmd) && poetryPytestHealthy(cwd)) {
    return cmd;
  }
  return cmd;
}

export function runFrozenRedCommand(command, { cwd = DEFAULT_ROOT, timeoutMs = 30000 } = {}) {
  const resolvedCommand = preferPoetryPytestCommand(command, cwd);
  const parsed = parseBoundedTestCommand(resolvedCommand);
  if (!parsed.ok) {
    return {
      ok: false,
      code: parsed.code,
      command: String(resolvedCommand || ""),
      exit_code: 1,
      relevant_output: parsed.detail || "unparseable command",
      alias_loader: false,
      execution_mechanism: "none",
    };
  }
  const env = { ...process.env, NODE_DISABLE_COLORS: "1", PY_COLORS: "0" };
  delete env.NODE_TEST_CONTEXT;
  delete env.NODE_CHANNEL_FD;
  delete env.NODE_UNIQUE_ID;
  delete env.PYTHONPATH;
  delete env.PYTHONHOME;
  const isNode = parsed.mechanism === EXECUTION_MECHANISM_NODE_TEST;
  const loader = isNode && aliasLoaderAvailable(cwd);
  const argv = loader ? withAliasLoaderArgv(parsed.argv, { cwd }) : parsed.argv;
  const r = spawnSync(parsed.file, argv, {
    cwd,
    encoding: "utf8",
    timeout: timeoutMs,
    env,
  });
  const out = `${r.stdout || ""}${r.stderr || ""}`.slice(0, 4000);
  const exit = typeof r.status === "number" ? r.status : 1;
  return {
    ok: exit === 0,
    command: String(resolvedCommand),
    exit_code: exit,
    relevant_output: out,
    timed_out: Boolean(r.error && r.error.code === "ETIMEDOUT"),
    alias_loader: loader,
    execution_mechanism:
      parsed.mechanism === EXECUTION_MECHANISM_POETRY_PYTEST
        ? EXECUTION_MECHANISM_POETRY_PYTEST
        : parsed.mechanism === EXECUTION_MECHANISM_PYTHON_PYTEST
          ? EXECUTION_MECHANISM_PYTHON_PYTEST
          : loader
            ? EXECUTION_MECHANISM_NODE_TEST_ALIAS_LOADER
            : EXECUTION_MECHANISM_NODE_TEST,
  };
}

/** Focused/adjacent checks use the same bounded runner as frozen RED. */
export const runBoundedGreenCheck = runFrozenRedCommand;

export function isImplementationFresh(impl, { red, repoRoot } = {}) {
  const root = repoRoot || DEFAULT_ROOT;
  if (!isObject(impl)) {
    return { ok: false, code: "STALE_IMPLEMENTATION" };
  }
  if (impl.status !== IMPLEMENTATION_WRITTEN && impl.status !== IMPLEMENTATION_ADOPTED) {
    return { ok: false, code: "STALE_IMPLEMENTATION" };
  }
  if (impl.next_stage !== "APPLY_GREEN") {
    return { ok: false, code: "STALE_IMPLEMENTATION" };
  }
  if (impl.tests_executed !== false || impl.tests_modified !== false) {
    return { ok: false, code: "STALE_IMPLEMENTATION" };
  }
  if (isObject(red)) {
    const pairing = classifyGreenPredecessorPairing({
      red,
      implement: impl,
      repoRoot: root,
    });
    if (!pairing.ok) {
      return { ok: false, code: "STALE_IMPLEMENTATION" };
    }
    if (
      pairing.branch === ADOPTION_PIPELINE_BRANCH &&
      impl.status === IMPLEMENTATION_ADOPTED &&
      !implementationAdoptedTransitionAuthorized(impl, root)
    ) {
      return { ok: false, code: "ADOPTION_AUDIT_MISSING" };
    }
    const frozen = red.test_content_identity;
    if (frozen && impl.frozen_test_content_identity !== frozen) {
      return { ok: false, code: "STALE_IMPLEMENTATION" };
    }
    const rid = impl.red_identity;
    if (isObject(rid) && rid.test_content_identity && rid.test_content_identity !== frozen) {
      return { ok: false, code: "STALE_IMPLEMENTATION" };
    }
  }
  return { ok: true };
}

export function detectGreenNoProgress(iterations = []) {
  if (!Array.isArray(iterations) || iterations.length < 2) {
    return { ok: true };
  }
  const last = iterations[iterations.length - 1];
  const prev = iterations[iterations.length - 2];
  if (
    last &&
    prev &&
    last.failure_signature &&
    last.failure_signature === prev.failure_signature &&
    last.production_digest &&
    last.production_digest === prev.production_digest &&
    last.rerun_exit_code !== 0
  ) {
    return { ok: false, code: "GREEN_NO_PROGRESS" };
  }
  return { ok: true };
}

function isGreen(greenBySlice, id, upstream = {}) {
  return isCurrentGreenProven(greenBySlice[id], upstream);
}

export function selectTasksCompletableAfterGreen({
  design,
  greenBySlice = {},
  newlyGreenSliceId,
  scaffoldBySlice = {},
  redBySlice = {},
  implementBySlice = {},
  frozenTestContentBySlice = {},
} = {}) {
  if (!isObject(design) || !newlyGreenSliceId) {
    return { completable: [], deferred: [] };
  }
  const slice = (design.slices || []).find((s) => s.slice_id === newlyGreenSliceId);
  if (!slice) return { completable: [], deferred: [] };
  const effectiveGreen = { ...greenBySlice, [newlyGreenSliceId]: { status: GREEN_PROVEN } };
  const completable = [];
  const deferred = [];
  for (const taskId of slice.source_task_ids || []) {
    const covering = (design.slices || []).filter((s) =>
      (s.source_task_ids || []).includes(taskId),
    );
    const waiting = covering
      .filter((s) => {
        const upstream = {
          design,
          scaffold: scaffoldBySlice[s.slice_id],
          red: redBySlice[s.slice_id],
          implement: implementBySlice[s.slice_id],
          frozenTestContentIdentity: frozenTestContentBySlice[s.slice_id],
        };
        // Newly proven slice is authorized by this invoke (status stub).
        if (s.slice_id === newlyGreenSliceId) return false;
        return !isGreen(effectiveGreen, s.slice_id, upstream);
      })
      .map((s) => s.slice_id);
    if (waiting.length) deferred.push({ task_id: taskId, waiting_slices: waiting });
    else completable.push(taskId);
  }
  return { completable, deferred };
}

function lastRedExecution(artifact) {
  const execs = Array.isArray(artifact.red_executions) ? artifact.red_executions : [];
  return execs[execs.length - 1] || null;
}

function semanticErrors(artifact) {
  const errors = [];
  if (!isObject(artifact)) return errors;
  if (artifact.chat_history_required !== false) {
    errors.push({
      code: "chat_history_required",
      message: "chat_history_required must be false",
    });
  }
  if (artifact.whole_change_verified === true) {
    errors.push({
      code: "whole_change_verified",
      message: "GREEN_PROVEN is slice-local; whole_change_verified must be false",
    });
  }
  const limit = artifact.iteration_limit || DEFAULT_MAX_GREEN_FIX_ITERATIONS;
  const count = artifact.iteration_count;
  const fixes = Array.isArray(artifact.green_fix_iterations)
    ? artifact.green_fix_iterations
    : [];
  if (Number.isInteger(count) && count !== fixes.length) {
    errors.push({
      code: "iteration_count",
      message: "iteration_count must equal green_fix_iterations.length",
    });
  }
  if (artifact.status === GREEN_PROVEN) {
    if (artifact.tests_modified !== false) {
      errors.push({
        code: "tests_modified",
        message: "GREEN_PROVEN requires tests_modified=false",
      });
    }
    if (!artifact.scaffold_identity || artifact.scaffold_identity.status !== "SCAFFOLD_READY") {
      errors.push({
        code: "scaffold_identity",
        message: "GREEN_PROVEN requires scaffold_identity with SCAFFOLD_READY",
      });
    }
    if (
      artifact.predecessor_branch &&
      artifact.predecessor_branch !== NORMAL_PIPELINE_BRANCH &&
      artifact.predecessor_branch !== ADOPTION_PIPELINE_BRANCH &&
      artifact.predecessor_branch !== ALREADY_SATISFIED_WRITE_PIPELINE_BRANCH
    ) {
      errors.push({
        code: "predecessor_branch",
        message:
          "GREEN_PROVEN predecessor_branch must be NORMAL, ADOPTION, or ALREADY_SATISFIED_WRITE",
      });
    }
    if (artifact.predecessor_branch === ADOPTION_PIPELINE_BRANCH) {
      if (artifact.red_identity?.status !== ALREADY_SATISFIED) {
        errors.push({
          code: "red_identity",
          message: "ADOPTION GREEN requires red_identity.status=ALREADY_SATISFIED",
        });
      }
      if (artifact.implementation_identity?.status !== IMPLEMENTATION_ADOPTED) {
        errors.push({
          code: "implementation_identity",
          message: "ADOPTION GREEN requires implementation_identity.status=IMPLEMENTATION_ADOPTED",
        });
      }
    } else if (artifact.predecessor_branch === NORMAL_PIPELINE_BRANCH) {
      if (artifact.red_identity?.status !== "RED_PROVEN") {
        errors.push({
          code: "red_identity",
          message: "NORMAL GREEN requires red_identity.status=RED_PROVEN",
        });
      }
      if (artifact.implementation_identity?.status !== IMPLEMENTATION_WRITTEN) {
        errors.push({
          code: "implementation_identity",
          message: "NORMAL GREEN requires implementation_identity.status=IMPLEMENTATION_WRITTEN",
        });
      }
    } else if (artifact.predecessor_branch === ALREADY_SATISFIED_WRITE_PIPELINE_BRANCH) {
      if (artifact.red_identity?.status !== ALREADY_SATISFIED) {
        errors.push({
          code: "red_identity",
          message:
            "ALREADY_SATISFIED_WRITE GREEN requires red_identity.status=ALREADY_SATISFIED",
        });
      }
      if (artifact.implementation_identity?.status !== IMPLEMENTATION_WRITTEN) {
        errors.push({
          code: "implementation_identity",
          message:
            "ALREADY_SATISFIED_WRITE GREEN requires implementation_identity.status=IMPLEMENTATION_WRITTEN",
        });
      }
    }
    if (artifact.red_final_status !== "PASS") {
      errors.push({
        code: "red_final_status",
        message: "GREEN_PROVEN requires red_final_status=PASS",
      });
    }
    const last = lastRedExecution(artifact);
    if (!last || last.exit_code !== 0) {
      errors.push({
        code: "red_executions",
        message: "GREEN_PROVEN requires last frozen RED execution exit_code=0",
      });
    }
    for (const fix of fixes) {
      if (!mayEnterGreenFixLoop(fix.failure_classification)) {
        errors.push({
          code: "failure_classification",
          message: `GREEN must not repair ${fix.failure_classification}`,
        });
      }
      if (looksLikeTestPath(String((fix.paths_modified || [])[0] || ""))) {
        errors.push({
          code: "tests_modified",
          message: "GREEN fix iterations must not modify test paths",
        });
      }
    }
    for (let i = 0; i < fixes.length; i += 1) {
      const fix = fixes[i];
      const rerun = (artifact.red_executions || []).find(
        (e) => e.phase === "post_fix" && e.iteration === fix.iteration,
      );
      if (!rerun && fix.rerun_exit_code === undefined) {
        errors.push({
          code: "red_rerun",
          message: `fix iteration ${fix.iteration} missing frozen RED rerun`,
        });
      }
    }
    if (fixes.length && fixes[fixes.length - 1].rerun_exit_code !== 0) {
      errors.push({
        code: "red_rerun",
        message: "last production fix must be followed by RED rerun exit 0",
      });
    }
    if (Number.isInteger(count) && count > limit) {
      errors.push({
        code: "GREEN_ITERATION_LIMIT",
        message: "GREEN_PROVEN cannot exceed iteration_limit",
      });
    }
    const match = frozenTestIdentityMatches({
      frozen: artifact.frozen_test_content_identity,
      before: artifact.frozen_test_identity_before,
      after: artifact.frozen_test_identity_after,
    });
    if (!match.ok) {
      errors.push({
        code: match.code,
        message: "frozen RED identity must match before and after GREEN",
      });
    }
    const checks = [
      ...(artifact.focused_checks || []),
      ...(artifact.adjacent_checks || []),
    ];
    for (const c of checks) {
      if (isUnboundedVerificationCommand(c.command)) {
        errors.push({
          code: "unbounded_check",
          message: `focused/adjacent check is not bounded: ${c.command}`,
        });
      }
    }
    if (artifact.adjacent_final_status === "PASS") {
      const adj = artifact.adjacent_checks || [];
      if (!adj.length || adj.some((c) => c.exit_code !== 0)) {
        errors.push({
          code: "adjacent_final_status",
          message: "adjacent_final_status=PASS requires all adjacent checks exit 0",
        });
      }
    }
    if (artifact.adjacent_final_status === "NOT_REQUIRED") {
      const adj = artifact.adjacent_checks || [];
      for (const c of adj) {
        if (looksLikeAliasResolutionFailure(c.relevant_output || "")) {
          errors.push({
            code: "adjacent_final_status",
            message:
              "plain node @/ alias failure cannot justify adjacent_final_status=NOT_REQUIRED",
          });
        }
        if (c.execution_capability === REQUIRED_BUT_NOT_EXECUTABLE) {
          errors.push({
            code: "adjacent_final_status",
            message:
              "REQUIRED_BUT_NOT_EXECUTABLE cannot be waived as NOT_REQUIRED on GREEN_PROVEN",
          });
        }
      }
    }
    if (artifact.adjacent_final_status === REQUIRED_BUT_NOT_EXECUTABLE) {
      errors.push({
        code: "TEST_INFRA_FAILURE",
        message: "REQUIRED_BUT_NOT_EXECUTABLE is a blocker, not GREEN_PROVEN",
      });
    }
    const progress = detectGreenNoProgress(fixes);
    if (!progress.ok && last && last.exit_code !== 0) {
      errors.push({
        code: "GREEN_NO_PROGRESS",
        message: "stagnant repair cannot be GREEN_PROVEN",
      });
    }
    if (["PASS", "VERIFIED", "COMPLETE_CHANGE"].includes(artifact.next_stage)) {
      errors.push({
        code: "next_stage",
        message: "GREEN must not claim whole-change verification",
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
    if (codes.includes("GREEN_ITERATION_LIMIT") && Number.isInteger(count) && count < limit) {
      errors.push({
        code: "GREEN_ITERATION_LIMIT",
        message: "GREEN_ITERATION_LIMIT requires iteration_count >= iteration_limit",
      });
    }
    for (const fix of fixes) {
      if (!mayEnterGreenFixLoop(fix.failure_classification) && !codes.length) {
        errors.push({
          code: "failure_classification",
          message: "non-owned classification must not appear as a repair iteration",
        });
      }
    }
  }
  return errors;
}

export function validateApplyGreenArtifact({
  artifact,
  schema,
  impactExpand = null,
  red = null,
  repoRoot = DEFAULT_ROOT,
  artifactAbsPath = null,
  greenAdoptionMode: greenAdoptionModeOverride = null,
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
  const greenAdoptionMode = greenAdoptionModeOverride ?? VALIDATE_GREEN_TRANSITION;
  for (const e of adoptionGreenTransitionErrors(artifact, repoRoot, {
    greenAbsPath: artifactAbsPath,
  })) {
    errors.push(e);
  }
  const ku = validateGreenKnowledgeUnion(artifact, red || {}, impactExpand);
  for (const e of ku.errors) errors.push(e);
  let greenTransitionAuthorized = true;
  if (artifact.predecessor_branch === "ADOPTION" && artifact.status === "GREEN_PROVEN") {
    const implPath = artifact.implementation_identity?.machine_path;
    let implement = null;
    if (typeof implPath === "string" && implPath && existsSync(resourcePath(repoRoot, implPath))) {
      implement = JSON.parse(readFileSync(resourcePath(repoRoot, implPath), "utf8"));
    }
    greenTransitionAuthorized = implement
      ? implementationAdoptedTransitionAuthorized(implement, repoRoot)
      : false;
  }
  return {
    ok: errors.length === 0,
    errors,
    status: artifact.status,
    green_adoption_mode: greenAdoptionMode,
    transition_authorized: greenTransitionAuthorized,
  };
}

export function loadApplyGreenSchema(repoRoot = DEFAULT_ROOT) {
  const schemaPath = resourcePath(repoRoot, SCHEMA_REL);
  if (!existsSync(schemaPath)) return null;
  return JSON.parse(readFileSync(schemaPath, "utf8"));
}

function main(argv) {
  const args = [...argv];
  let repoRoot = DEFAULT_ROOT;
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
  const red = loadLinkedRed(repoRoot, artifact) || {};
  const impactExpand = loadImpactExpandOverlay(
    repoRoot,
    artifact,
    artifact.red_identity?.machine_path,
  );
  const result = validateApplyGreenArtifact({
    artifact,
    schema,
    impactExpand,
    red,
    repoRoot,
    artifactAbsPath: resolve(artifactPath),
  });
  process.stdout.write(
    JSON.stringify(
      {
        status: result.ok ? "PASS" : "FAIL",
        artifact_status: artifact.status,
        green_adoption_mode: result.green_adoption_mode,
        transition_authorized: result.transition_authorized,
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
