import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * APPLY DESIGN REVIEW artifact validator and freshness helpers.
 *
 * Read-only semantic review receipt after APPLY_DESIGN_READY and before
 * SCAFFOLD. Does not rewrite APPLY DESIGN, planning, product, or tests.
 * Agents produce APPLY-DESIGN-REVIEW.json; this tool checks schema +
 * semantic gates used by command-contract tests.
 *
 * Usage:
 *   node opsx_tools/opsx-apply-design-review.mjs <APPLY-DESIGN-REVIEW.json>
 *   node opsx_tools/opsx-apply-design-review.mjs --schema <schema> <path>
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  computeCanonicalApplyDesignIdentity,
  evaluateApplyDesignReviewGate,
  fileSha256,
  freezePlanningIdentity,
  STALE_APPLY_DESIGN_REVIEW,
} from "./opsx-apply-pipeline-identity.mjs";
import {
  structuralDesignReviewFindings,
  APPLY_DESIGN_REVIEW_PASS,
  REPAIRS_REQUIRED,
} from "./opsx-apply-composition.mjs";
import {
  KNOWLEDGE_OBLIGATION_IGNORED,
  validateDesignKnowledgeObligations,
} from "./opsx-bug-learning-apply.mjs";
import { validateDesignSliceTechnicalContracts } from "./opsx-apply-red-oracle-authority.mjs";

export { KNOWLEDGE_OBLIGATION_IGNORED, validateDesignKnowledgeObligations };

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);
const SCHEMA_REL = "opsx_tools/registry/apply_design_review.schema.json";

export {
  structuralDesignReviewFindings,
  APPLY_DESIGN_REVIEW_PASS,
  REPAIRS_REQUIRED,
  computeCanonicalApplyDesignIdentity,
  evaluateApplyDesignReviewGate,
  fileSha256,
  freezePlanningIdentity,
  STALE_APPLY_DESIGN_REVIEW,
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
  if (schemaType === "number") return typeof value === "number";
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
        message: `${path}: must match exactly one APPLY DESIGN REVIEW status branch`,
      });
      if (matched === 0) {
        for (const e of lastLocal) errors.push(e);
      }
    }
    return;
  }
  if (schema.type && !typeOk(value, schema.type)) {
    errors.push({
      code: "type",
      message: `${path}: expected ${schema.type}`,
    });
    return;
  }
  if (schema.const !== undefined && value !== schema.const) {
    errors.push({
      code: "const",
      message: `${path}: must be ${JSON.stringify(schema.const)}`,
    });
  }
  if (schema.enum && !schema.enum.includes(value)) {
    errors.push({
      code: "enum",
      message: `${path}: invalid enum value`,
    });
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
  if (schema.type === "integer" || schema.type === "number") {
    if (schema.minimum !== undefined && value < schema.minimum) {
      errors.push({ code: "minimum", message: `${path}: below minimum` });
    }
    if (schema.maximum !== undefined && value > schema.maximum) {
      errors.push({ code: "maximum", message: `${path}: above maximum` });
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

function hasQuestion12(reviewQuestions) {
  if (!Array.isArray(reviewQuestions)) return false;
  return reviewQuestions.some(
    (q) =>
      q &&
      q.id === 12 &&
      typeof q.verdict === "string" &&
      q.verdict.length > 0,
  );
}

function semanticErrors(artifact) {
  const errors = [];
  if (!isObject(artifact)) return errors;

  if (artifact.chat_history_required !== false) {
    errors.push({
      code: "chat_history_required",
      message: "chat_history_required must be false (no chat-history authority)",
    });
  }

  const wb = artifact.write_boundary;
  if (isObject(wb)) {
    if (wb.product_code !== false) {
      errors.push({
        code: "write_boundary",
        message: "write_boundary.product_code must be false",
      });
    }
    if (wb.tests !== false) {
      errors.push({
        code: "write_boundary",
        message: "write_boundary.tests must be false",
      });
    }
    if (wb.apply_design !== false) {
      errors.push({
        code: "write_boundary",
        message: "write_boundary.apply_design must be false",
      });
    }
    if (wb.planning !== false) {
      errors.push({
        code: "write_boundary",
        message: "write_boundary.planning must be false",
      });
    }
  }

  const findings = Array.isArray(artifact.findings) ? artifact.findings : [];
  const blocking = findings.filter((f) => f && f.severity === "blocking");
  const risk = artifact.composed_behavior_risk;

  if (artifact.status === APPLY_DESIGN_REVIEW_PASS) {
    if (artifact.next_stage !== "APPLY_SCAFFOLD") {
      errors.push({
        code: "next_stage",
        message: "APPLY_DESIGN_REVIEW_PASS requires next_stage=APPLY_SCAFFOLD",
      });
    }
    if (!isObject(risk) || risk.answered !== true) {
      errors.push({
        code: "composed_behavior_risk",
        message:
          "APPLY_DESIGN_REVIEW_PASS requires composed_behavior_risk.answered=true",
      });
    }
    if (!hasQuestion12(artifact.review_questions)) {
      errors.push({
        code: "review_questions",
        message:
          "APPLY_DESIGN_REVIEW_PASS requires review question 12 with a verdict",
      });
    }
    if (blocking.length > 0) {
      errors.push({
        code: "findings",
        message: "APPLY_DESIGN_REVIEW_PASS forbids blocking findings",
      });
    }
    const adi = artifact.apply_design_identity;
    if (isObject(adi) && adi.status !== "APPLY_DESIGN_READY") {
      errors.push({
        code: "apply_design_identity",
        message:
          "APPLY_DESIGN_REVIEW_PASS requires apply_design_identity.status=APPLY_DESIGN_READY",
      });
    }
  }

  if (artifact.status === REPAIRS_REQUIRED) {
    if (artifact.next_stage !== "APPLY_DESIGN") {
      errors.push({
        code: "next_stage",
        message: "REPAIRS_REQUIRED requires next_stage=APPLY_DESIGN",
      });
    }
    if (blocking.length < 1) {
      errors.push({
        code: "findings",
        message: "REPAIRS_REQUIRED requires at least one blocking finding",
      });
    }
    if (!isObject(artifact.repair_handoff)) {
      errors.push({
        code: "repair_handoff",
        message: "REPAIRS_REQUIRED requires repair_handoff",
      });
    }
    const blockers = Array.isArray(artifact.blockers) ? artifact.blockers : [];
    if (blockers.length < 1) {
      errors.push({
        code: "blockers",
        message: "REPAIRS_REQUIRED requires blockers[]",
      });
    }
  }

  return errors;
}

export function validateApplyDesignReviewArtifact({ artifact, schema, repoRoot = DEFAULT_ROOT }) {
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

  const designRel =
    artifact.apply_design_identity?.artifact_path ||
    artifact.apply_design_identity?.machine_path;
  let design = null;
  let canonicalIdentity = null;
  if (designRel && existsSync(resourcePath(repoRoot, designRel))) {
    const designAbs = resourcePath(repoRoot, designRel);
    design = JSON.parse(readFileSync(designAbs, "utf8"));
    const ko = validateDesignKnowledgeObligations(design);
    for (const e of ko.errors) errors.push(e);
    const tech = validateDesignSliceTechnicalContracts(design, { repoRoot });
    for (const e of tech.errors) errors.push(e);
    const canonical = computeCanonicalApplyDesignIdentity(design, {
      repoRoot,
      artifactRelPath: designRel,
    });
    if (!canonical.ok) {
      errors.push({
        code: STALE_APPLY_DESIGN_REVIEW,
        message: `canonical APPLY-DESIGN identity unavailable (${canonical.code})`,
      });
    } else {
      canonicalIdentity = canonical.identity;
    }
  } else if (artifact.status === APPLY_DESIGN_REVIEW_PASS) {
    errors.push({
      code: STALE_APPLY_DESIGN_REVIEW,
      message: "APPLY_DESIGN_REVIEW_PASS requires linked APPLY-DESIGN.json on disk",
    });
  }

  if (artifact.status === APPLY_DESIGN_REVIEW_PASS && design && canonicalIdentity) {
    const gate = evaluateApplyDesignReviewGate({
      design,
      review: artifact,
      repoRoot,
      currentDesignArtifactSha256: canonicalIdentity.artifact_sha256,
      currentPlanningDigest: canonicalIdentity.planning_digest,
      currentProtectedContextDigest: canonicalIdentity.protected_context_digest,
    });
    if (!gate.ok) {
      errors.push({
        code: gate.code || STALE_APPLY_DESIGN_REVIEW,
        message: `review stale vs canonical APPLY-DESIGN identity (${gate.detail || "mismatch"})`,
      });
    }
  }

  return {
    ok: errors.length === 0,
    errors,
    status: artifact.status,
    canonical_apply_design_identity: canonicalIdentity,
  };
}

export function loadApplyDesignReviewSchema(repoRoot = DEFAULT_ROOT) {
  const schemaPath = resourcePath(repoRoot, SCHEMA_REL);
  if (!existsSync(schemaPath)) return null;
  return JSON.parse(readFileSync(schemaPath, "utf8"));
}

/**
 * Freshness of a review receipt against a READY DESIGN + current artifact hash.
 */
export function evaluateReviewFreshnessAgainstDesign({
  design,
  review,
  designArtifactSha256,
} = {}) {
  return evaluateApplyDesignReviewGate({
    design,
    review,
    currentDesignArtifactSha256: designArtifactSha256,
    currentPlanningDigest: design?.planning_identity?.planning_digest,
    currentProtectedContextDigest:
      design?.protected_context_identity?.content_digest,
  });
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
  if (!artifactPath) {
    process.stdout.write(
      JSON.stringify({ status: "FAIL", errors: [{ code: "missing_path" }] }) +
        "\n",
    );
    process.exit(1);
  }
  if (!existsSync(schemaPath)) {
    process.stdout.write(
      JSON.stringify({ status: "FAIL", errors: [{ code: "schema_missing" }] }) +
        "\n",
    );
    process.exit(1);
  }
  if (!existsSync(artifactPath)) {
    process.stdout.write(
      JSON.stringify({ status: "FAIL", errors: [{ code: "artifact_missing" }] }) +
        "\n",
    );
    process.exit(1);
  }
  const schema = JSON.parse(readFileSync(schemaPath, "utf8"));
  const artifact = JSON.parse(readFileSync(artifactPath, "utf8"));
  const result = validateApplyDesignReviewArtifact({ artifact, schema, repoRoot });
  const out = {
    status: result.ok ? "PASS" : "FAIL",
    artifact_status: artifact.status,
    error_count: result.errors.length,
    errors: result.errors,
    canonical_apply_design_identity: result.canonical_apply_design_identity,
  };
  process.stdout.write(JSON.stringify(out, null, 2) + "\n");
  process.exit(result.ok ? 0 : 1);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2));
}
