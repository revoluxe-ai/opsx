import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * APPLY SCAFFOLD artifact validator and slice selection.
 *
 * Does not author tests. Does not implement production behavior. Agents
 * produce SCAFFOLD.json after one bounded SHAPE write (or no-op); this tool
 * checks schema, SCAFFOLD_READY semantics, and deterministic slice pick.
 *
 * Usage:
 *   node opsx_tools/opsx-apply-scaffold.mjs <SCAFFOLD.json>
 *   node opsx_tools/opsx-apply-scaffold.mjs --schema <schema> <SCAFFOLD.json>
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  FORBIDDEN_SCAFFOLD_BEHAVIOR,
  SCAFFOLD_NOT_IMPLEMENTED,
  SCAFFOLD_READY,
  identityDigest,
  freezePlanningIdentity,
  computeCurrentPlanningIdentity,
  classifyApplyDesignPlanningFreshness,
  evaluateApplyDesignFreshnessFromDisk,
  isApplyDesignFreshForImplement,
  looksLikeTestPath,
  posixPath,
  selectNextScaffoldableSlice,
  loadChangeReceiptMaps,
  evaluateApplyDesignReviewGate,
  APPLY_DESIGN_REVIEW_PASS,
} from "./opsx-apply-pipeline-identity.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);
const SCHEMA_REL = "opsx_tools/registry/apply_scaffold.schema.json";

export {
  FORBIDDEN_SCAFFOLD_BEHAVIOR,
  SCAFFOLD_NOT_IMPLEMENTED,
  SCAFFOLD_READY,
  identityDigest,
  freezePlanningIdentity,
  computeCurrentPlanningIdentity,
  classifyApplyDesignPlanningFreshness,
  evaluateApplyDesignFreshnessFromDisk,
  selectNextScaffoldableSlice,
  loadChangeReceiptMaps,
  evaluateApplyDesignReviewGate,
  APPLY_DESIGN_REVIEW_PASS,
};

export const FORBIDDEN_SCAFFOLD_SUBPROCESS_PATTERNS = [
  /\bnode\s+--test\b/,
  /\bnpm\s+test\b/,
  /\bpytest\b/,
  /\bpython\s+-m\s+unittest\b/,
  /\bnpm\s+run\s+build\b/,
  /\bopsx-test\b/,
];

export function isForbiddenScaffoldSubprocess(command) {
  return FORBIDDEN_SCAFFOLD_SUBPROCESS_PATTERNS.some((re) =>
    re.test(String(command || "")),
  );
}

export function isApplyDesignFreshForScaffold(design, opts = {}) {
  return isApplyDesignFreshForImplement(design, opts);
}

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
        message: `${path}: must match exactly one APPLY SCAFFOLD status branch`,
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

export function classifyScaffoldScope({
  allowedWritePaths = [],
  writtenPaths = [],
} = {}) {
  const extras = [];
  for (const raw of writtenPaths) {
    const p = posixPath(raw);
    if (looksLikeTestPath(p)) {
      extras.push(p);
      continue;
    }
    const allowed = (allowedWritePaths || []).some((s) => {
      const surf = posixPath(s);
      return p === surf || p.startsWith(`${surf}/`);
    });
    if (!allowed) extras.push(p);
  }
  if (extras.length) {
    return { ok: false, code: "SCAFFOLD_SCOPE_EXPANSION", paths: extras };
  }
  return { ok: true };
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
  if (artifact.tests_modified !== false) {
    errors.push({
      code: "tests_modified",
      message: "SCAFFOLD must not modify tests",
    });
  }
  if (artifact.tests_executed !== false) {
    errors.push({
      code: "tests_executed",
      message: "SCAFFOLD must not run tests",
    });
  }
  if (artifact.behavior_implemented !== false) {
    errors.push({
      code: "behavior_implemented",
      message: "SCAFFOLD must not implement production behavior",
    });
  }
  if (artifact.status === SCAFFOLD_READY) {
    if (artifact.next_stage !== "APPLY_RED") {
      errors.push({
        code: "next_stage",
        message: "SCAFFOLD_READY requires next_stage=APPLY_RED",
      });
    }
    const created = artifact.created_paths || [];
    const extended = artifact.extended_paths || [];
    const written = [...created, ...extended];
    if (written.some(looksLikeTestPath)) {
      errors.push({
        code: "tests_modified",
        message: "SCAFFOLD production path lists must not include tests",
      });
    }
    if (artifact.no_op === true) {
      if (created.length) {
        errors.push({
          code: "no_op",
          message: "no_op SCAFFOLD_READY must not create paths",
        });
      }
      if (artifact.product_files_modified === true) {
        errors.push({
          code: "no_op",
          message: "no_op SCAFFOLD_READY requires product_files_modified=false",
        });
      }
    } else if (written.length < 1) {
      errors.push({
        code: "production_paths",
        message: "non-no-op SCAFFOLD_READY requires created or extended paths",
      });
    }
    const scope = classifyScaffoldScope({
      allowedWritePaths: artifact.authorized_write_paths || [],
      writtenPaths: written,
    });
    if (!scope.ok) {
      errors.push({
        code: scope.code,
        message: `scaffold write expansion: ${(scope.paths || []).join(", ")}`,
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
  }
  return errors;
}

export function validateApplyScaffoldArtifact({ artifact, schema }) {
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
  return { ok: errors.length === 0, errors, status: artifact.status };
}

export function loadApplyScaffoldSchema(repoRoot = DEFAULT_ROOT) {
  const schemaPath = resourcePath(repoRoot, SCHEMA_REL);
  if (!existsSync(schemaPath)) return null;
  return JSON.parse(readFileSync(schemaPath, "utf8"));
}

function main(argv) {
  let schemaPath = resolve(DEFAULT_ROOT, SCHEMA_REL);
  const args = [...argv];
  const schemaFlag = args.indexOf("--schema");
  if (schemaFlag >= 0) {
    schemaPath = resolve(args[schemaFlag + 1]);
    args.splice(schemaFlag, 2);
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
  const result = validateApplyScaffoldArtifact({ artifact, schema });
  process.stdout.write(
    JSON.stringify(
      {
        status: result.ok ? "PASS" : "FAIL",
        artifact_status: artifact.status,
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
