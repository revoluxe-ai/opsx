import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * APPLY RED artifact validator, slice selection, and identity helpers.
 *
 * Does not author tests. Does not implement production. Agents produce RED.json;
 * this tool checks schema, RED_PROVEN semantics (including per-command
 * command_executions qualification), and deterministic slice pick.
 *
 * Usage:
 *   node opsx_tools/opsx-apply-red.mjs <RED.json>
 *   node opsx_tools/opsx-apply-red.mjs --schema <schema> <RED.json>
 *   node opsx_tools/opsx-apply-red.mjs --mode impact-expand --extra-seams <json> [--repo <root>] <RED.json>
 */
import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  assertManualWriterLeaseForMutation,
  finishManualWriterMutationGuard,
  inferChangeRootFromArtifactPath,
} from "./opsx-apply-change-writer.mjs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  identityDigest,
  freezePlanningIdentity,
  computeCurrentPlanningIdentity,
  classifyApplyDesignPlanningFreshness,
  evaluateApplyDesignFreshnessFromDisk,
} from "./opsx-apply-design.mjs";
import {
  APPLY_RED_SCHEMA_VERSION,
  RED_EVIDENCE_CONTRACT,
  ALREADY_SATISFIED,
  SCAFFOLD_NOT_IMPLEMENTED,
  classifyRedFailureAfterScaffold,
  isApplyDesignFresh,
  isDurableRedProven,
  isDurableAlreadySatisfiedObservation,
  isAlreadySatisfiedEvidenceComplete,
  isRedEvidenceComplete,
  selectNextEligibleSlice,
} from "./opsx-apply-pipeline-identity.mjs";
import {
  collectSlicePlannedTestArtifacts,
  redMissingPlannedTestArtifacts,
  validateCoverageReconcileArtifact,
  evaluateCoverageReconcileAuthority,
} from "./opsx-apply-verify-completion.mjs";
import {
  collectRedOwnedCompositionObligations,
} from "./opsx-apply-composition.mjs";
import {
  validateRedKnowledgeObligations,
  buildRedImpactExpandOverlay,
  loadLinkedDesign,
  artifactSha256,
} from "./opsx-bug-learning-apply.mjs";

export { validateRedKnowledgeObligations, buildRedImpactExpandOverlay } from "./opsx-bug-learning-apply.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);
const SCHEMA_REL = "opsx_tools/registry/apply_red.schema.json";

export const DEFAULT_MAX_RED_ATTEMPTS = 3;
export const GREEN_PROVEN = "GREEN_PROVEN";
export const RED_PROVEN = "RED_PROVEN";
export const ALREADY_SATISFIED_STATUS = ALREADY_SATISFIED;
export const RED_PROVEN_CLASSIFICATION = "INTENDED_BEHAVIOR_MISSING";
export { APPLY_RED_SCHEMA_VERSION, RED_EVIDENCE_CONTRACT, ALREADY_SATISFIED };

export {
  identityDigest,
  freezePlanningIdentity,
  computeCurrentPlanningIdentity,
  classifyApplyDesignPlanningFreshness,
  evaluateApplyDesignFreshnessFromDisk,
  isApplyDesignFresh,
  isDurableRedProven,
  isDurableAlreadySatisfiedObservation,
  isAlreadySatisfiedEvidenceComplete,
  isRedEvidenceComplete,
  selectNextEligibleSlice,
  classifyRedFailureAfterScaffold,
  collectSlicePlannedTestArtifacts,
  redMissingPlannedTestArtifacts,
  validateCoverageReconcileArtifact,
  collectRedOwnedCompositionObligations,
};

export {
  classifyCommandExecutionFromOutput,
  parseFrozenTestCommand,
} from "./opsx-apply-red-frozen-command.mjs";

function suiteFullyQualified(artifact) {
  const cmds = Array.isArray(artifact.test_commands) ? artifact.test_commands : [];
  const execs = Array.isArray(artifact.command_executions)
    ? artifact.command_executions
    : [];
  return (
    cmds.length > 0 &&
    execs.length === cmds.length &&
    execs.every((e) => e && e.red_qualified === true)
  );
}

function suiteFullyPassQualified(artifact) {
  const cmds = Array.isArray(artifact.test_commands) ? artifact.test_commands : [];
  const execs = Array.isArray(artifact.command_executions)
    ? artifact.command_executions
    : [];
  return (
    cmds.length > 0 &&
    execs.length === cmds.length &&
    execs.every(
      (e) =>
        e &&
        (e.pass_qualified === true || e.red_qualified === true) &&
        e.final_exit_code === 0,
    )
  );
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
        message: `${path}: must match exactly one APPLY RED status branch`,
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

export function sliceDigest(slice) {
  return createHash("sha256")
    .update(JSON.stringify(slice), "utf8")
    .digest("hex");
}

export function bytesSha256(text) {
  return createHash("sha256").update(String(text), "utf8").digest("hex");
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
  if (artifact.product_files_modified !== false) {
    errors.push({
      code: "product_files_modified",
      message: "product_files_modified must be false",
    });
  }
  if (artifact.status === ALREADY_SATISFIED) {
    if (artifact.next_stage !== "APPLY_IMPLEMENT") {
      errors.push({
        code: "next_stage",
        message: "ALREADY_SATISFIED observation requires next_stage=APPLY_IMPLEMENT",
      });
    }
    if (artifact.observation_only !== true) {
      errors.push({
        code: "observation_only",
        message: "ALREADY_SATISFIED requires observation_only=true",
      });
    }
    if (artifact.implementation_write_authorized !== false) {
      errors.push({
        code: "implementation_write_authorized",
        message: "ALREADY_SATISFIED requires implementation_write_authorized=false",
      });
    }
    if (artifact.adoption_audit_eligible !== true) {
      errors.push({
        code: "adoption_audit_eligible",
        message: "ALREADY_SATISFIED requires adoption_audit_eligible=true",
      });
    }
    if (artifact.tests_frozen !== true) {
      errors.push({
        code: "tests_frozen",
        message: "ALREADY_SATISFIED observation requires tests_frozen=true",
      });
    }
    if (!isAlreadySatisfiedEvidenceComplete(artifact) || !suiteFullyPassQualified(artifact)) {
      errors.push({
        code: "command_executions",
        message:
          "ALREADY_SATISFIED requires complete mapped PASS evidence for every frozen command",
      });
    }
    if (!artifact.scaffold_identity || artifact.scaffold_identity.status !== "SCAFFOLD_READY") {
      errors.push({
        code: "scaffold_identity",
        message: "ALREADY_SATISFIED requires scaffold_identity with SCAFFOLD_READY",
      });
    }
    const paths = [
      ...(artifact.test_paths || []),
      ...(artifact.fixture_paths || []),
    ];
    if (paths.length === 0) {
      errors.push({ code: "test_paths", message: "ALREADY_SATISFIED requires test_paths" });
    }
  }
  if (artifact.status === RED_PROVEN) {
    if (artifact.next_stage !== "APPLY_IMPLEMENT") {
      errors.push({
        code: "next_stage",
        message: "RED_PROVEN requires next_stage=APPLY_IMPLEMENT",
      });
    }
    if (artifact.tests_frozen !== true) {
      errors.push({
        code: "tests_frozen",
        message: "RED_PROVEN requires tests_frozen=true",
      });
    }
    const cls = artifact.final_execution && artifact.final_execution.failure_classification;
    if (cls !== RED_PROVEN_CLASSIFICATION) {
      errors.push({
        code: "failure_classification",
        message: "RED_PROVEN requires INTENDED_BEHAVIOR_MISSING",
      });
    }
    if (artifact.schema_version !== APPLY_RED_SCHEMA_VERSION) {
      errors.push({
        code: "schema_version",
        message: `RED_PROVEN requires schema_version=${APPLY_RED_SCHEMA_VERSION}`,
      });
    }
    if (artifact.evidence_contract !== RED_EVIDENCE_CONTRACT) {
      errors.push({
        code: "evidence_contract",
        message: `RED_PROVEN requires evidence_contract=${RED_EVIDENCE_CONTRACT}`,
      });
    }
    if (!isRedEvidenceComplete(artifact) || !suiteFullyQualified(artifact)) {
      errors.push({
        code: "command_executions",
        message:
          "RED_PROVEN requires command_executions.length == test_commands.length and every red_qualified===true; do not infer sibling executions",
      });
    }
    if (!artifact.scaffold_identity || artifact.scaffold_identity.status !== "SCAFFOLD_READY") {
      errors.push({
        code: "scaffold_identity",
        message: "RED_PROVEN requires scaffold_identity with SCAFFOLD_READY",
      });
    }
    const limit = artifact.attempt_limit || DEFAULT_MAX_RED_ATTEMPTS;
    const commandExecs = Array.isArray(artifact.command_executions)
      ? artifact.command_executions
      : [];
    for (const exec of commandExecs) {
      const n = Array.isArray(exec.attempts) ? exec.attempts.length : 0;
      if (n > limit) {
        errors.push({
          code: "attempt_limit",
          message: `command ${exec.command || "?"} attempts ${n} exceeds attempt_limit ${limit}`,
        });
      }
    }
    const paths = [
      ...(artifact.test_paths || []),
      ...(artifact.fixture_paths || []),
    ].map((path) => ({ path, sha256: "pending" }));
    if (paths.length === 0) {
      errors.push({ code: "test_paths", message: "RED_PROVEN requires test_paths" });
    }
  }
  if (artifact.status === "BLOCKED") {
    if (artifact.next_stage !== "NONE") {
      errors.push({
        code: "next_stage",
        message: "BLOCKED requires next_stage=NONE",
      });
    }
    if (artifact.tests_frozen !== false) {
      errors.push({
        code: "tests_frozen",
        message: "BLOCKED must not freeze tests as RED",
      });
    }
  }
  const cmds = Array.isArray(artifact.test_commands) ? artifact.test_commands : [];
  const execs = Array.isArray(artifact.command_executions)
    ? artifact.command_executions
    : [];
  if (execs.length && cmds.length && execs.length !== cmds.length) {
    errors.push({
      code: "command_executions",
      message: "command_executions.length must equal test_commands.length",
    });
  }
  if (artifact.tests_frozen === true) {
    const passOk =
      artifact.status === ALREADY_SATISFIED
        ? suiteFullyPassQualified(artifact)
        : suiteFullyQualified(artifact);
    if (!passOk) {
      errors.push({
        code: "tests_frozen",
        message:
          "tests_frozen=true is allowed only when every frozen command is qualified",
      });
    }
  }
  return errors;
}

export function validateApplyRedArtifact({
  artifact,
  schema,
  design,
  slice,
  scenarioPlanText = "",
} = {}) {
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
  if (slice && artifact.status === RED_PROVEN) {
    const missing = redMissingPlannedTestArtifacts({
      test_paths: artifact.test_paths,
      design,
      slice,
      scenarioPlanText,
    });
    if (missing.length) {
      errors.push({
        code: "MISSING_PLANNED_TEST_ARTIFACT",
        message: `RED_PROVEN omitted planned test artifacts: ${missing.join(", ")}`,
        missing,
      });
    }
    if (design) {
      const ko = validateRedKnowledgeObligations(artifact, design);
      for (const e of ko.errors) errors.push(e);
    }
  }
  return { ok: errors.length === 0, errors, status: artifact.status };
}

export function loadApplyRedSchema(repoRoot = DEFAULT_ROOT) {
  const schemaPath = resourcePath(repoRoot, SCHEMA_REL);
  if (!existsSync(schemaPath)) return null;
  return JSON.parse(readFileSync(schemaPath, "utf8"));
}

function posixRel(repoRoot, abs) {
  return String(abs)
    .replace(/\\/g, "/")
    .replace(String(repoRoot).replace(/\\/g, "/").replace(/\/$/, "") + "/", "");
}

/**
 * Write additive RED-IMPACT-EXPAND overlay next to RED.json (never rewrites RED).
 */
export function writeRedImpactExpandOverlay({
  repoRoot = DEFAULT_ROOT,
  redArtifactPath,
  extraSeams,
  writerSessionId = null,
} = {}) {
  const redAbs = resolve(redArtifactPath);
  const originalRed = JSON.parse(readFileSync(redAbs, "utf8"));
  const changeRoot =
    originalRed.apply_design_identity?.change_root ||
    inferChangeRootFromArtifactPath(redAbs);
  let writerGuard = null;
  if (changeRoot) {
    writerGuard = assertManualWriterLeaseForMutation({
      repoRoot,
      changeRoot,
      writerSessionId,
      changeId: originalRed.change_id,
      planningDigest: originalRed.apply_design_identity?.planning_digest,
      primitive: "APPLY_RED_IMPACT_EXPAND",
    });
    if (!writerGuard.ok) return writerGuard;
  }
  try {
  const overlay = buildRedImpactExpandOverlay({
    changeId: originalRed.change_id,
    sliceId: originalRed.slice_id,
    extraSeams,
    originalRed: {
      ...originalRed,
      artifact_sha256: artifactSha256(repoRoot, posixRel(repoRoot, redAbs)),
    },
  });
  const dir = dirname(redAbs);
  const jsonAbs = resolve(dir, "RED-IMPACT-EXPAND.json");
  const mdAbs = resolve(dir, "RED-IMPACT-EXPAND.md");
  writeFileSync(jsonAbs, `${JSON.stringify(overlay, null, 2)}\n`);
  const md = [
    "# RED-IMPACT-EXPAND",
    "",
    `Additive overlay for slice \`${originalRed.slice_id}\`. Original \`RED.json\` is unchanged.`,
    "",
    `- status: ${overlay.status}`,
    `- extra_seam_ids: ${(overlay.extra_seam_ids || []).join(", ")}`,
    `- additive_test_commands: ${(overlay.additive_test_commands || []).length}`,
    "",
  ].join("\n");
  writeFileSync(mdAbs, md);
  return {
    ok: true,
    json_path: posixRel(repoRoot, jsonAbs),
    md_path: posixRel(repoRoot, mdAbs),
    overlay,
  };
  } finally {
    if (changeRoot) {
      finishManualWriterMutationGuard({
        repoRoot,
        changeRoot,
        guard: writerGuard,
      });
    }
  }
}

async function main(argv) {
  const args = [...argv];
  let repoRoot = DEFAULT_ROOT;
  const repoFlag = args.indexOf("--repo");
  if (repoFlag >= 0) {
    repoRoot = resolve(args[repoFlag + 1]);
    args.splice(repoFlag, 2);
  }
  let historicalRead = false;
  const historicalFlag = args.indexOf("--historical-read");
  if (historicalFlag >= 0) {
    historicalRead = true;
    args.splice(historicalFlag, 1);
  }
  const modeFlag = args.indexOf("--mode");
  if (modeFlag >= 0) {
    const mode = args[modeFlag + 1];
    args.splice(modeFlag, 2);
    if (mode === "coverage-reconcile") {
      const artifactPath = args[0];
      if (!artifactPath || !existsSync(artifactPath)) {
        process.stdout.write(
          JSON.stringify({ status: "FAIL", errors: [{ code: "missing_path" }] }) + "\n",
        );
        process.exit(1);
      }
      const artifact = JSON.parse(readFileSync(artifactPath, "utf8"));
      const auth = evaluateCoverageReconcileAuthority({ artifact, repoRoot });
      process.stdout.write(
        JSON.stringify(
          {
            status: auth.ok ? "PASS" : "FAIL",
            mode: "coverage-reconcile",
            error_count: auth.errors?.length || 0,
            errors: auth.errors || [],
          },
          null,
          2,
        ) + "\n",
      );
      process.exit(auth.ok ? 0 : 1);
    }
    if (mode === "impact-expand") {
      const extraFlag = args.indexOf("--extra-seams");
      if (extraFlag < 0) {
        process.stdout.write(
          JSON.stringify({ status: "FAIL", errors: [{ code: "missing_extra_seams" }] }) + "\n",
        );
        process.exit(1);
      }
      const extraSeams = JSON.parse(args[extraFlag + 1]);
      args.splice(extraFlag, 2);
      const artifactPath = args[0];
      if (!artifactPath || !existsSync(artifactPath)) {
        process.stdout.write(
          JSON.stringify({ status: "FAIL", errors: [{ code: "missing_path" }] }) + "\n",
        );
        process.exit(1);
      }
      const result = writeRedImpactExpandOverlay({
        repoRoot,
        redArtifactPath: artifactPath,
        extraSeams,
      });
      process.stdout.write(JSON.stringify({ status: "PASS", mode: "impact-expand", ...result }, null, 2) + "\n");
      process.exit(0);
    }
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
  const slice =
    (design?.slices || []).find((s) => s.slice_id === artifact.slice_id) || null;
  const result = validateApplyRedArtifact({
    artifact,
    schema,
    design,
    slice,
  });
  const errors = [...result.errors];
  let transitionDecision = null;
  let operativeGateResult = null;
  if (result.ok && !historicalRead) {
    const {
      buildRedOperativeTransitionDecision,
      computeRedOperativeDecisionDigest,
      evaluateRedTransitionGate,
    } = await import("./opsx-apply-red-transition-gate.mjs");
    const gate = evaluateRedTransitionGate({
      artifact,
      repoRoot,
      historicalRead: false,
      design,
      slice,
    });
    operativeGateResult = gate;
    if (!gate.ok) {
      for (const e of gate.errors || []) {
        errors.push({
          code: e.code || "red_transition_gate",
          message: e.message || String(e),
        });
      }
    } else if (
      artifact.status === "RED_PROVEN" ||
      artifact.status === "ALREADY_SATISFIED"
    ) {
      const decision = buildRedOperativeTransitionDecision({
        artifact,
        gate,
        repoRoot,
      });
      transitionDecision = {
        ...decision,
        decision_digest: computeRedOperativeDecisionDigest(decision),
      };
    }
  }
  const ok = result.ok && errors.length === result.errors.length;
  process.stdout.write(
    JSON.stringify(
      {
        status: ok ? "PASS" : "FAIL",
        artifact_status: artifact.status,
        historical_read: historicalRead,
        error_count: errors.length,
        errors,
        live_pytest_skipped: operativeGateResult?.live_pytest_skipped === true,
        live_pytest_skip_reason: operativeGateResult?.live_pytest_skip_reason ?? null,
        ...(transitionDecision ? { transition_decision: transitionDecision } : {}),
      },
      null,
      2,
    ) + "\n",
  );
  process.exit(ok ? 0 : 1);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((err) => {
    process.stdout.write(
      JSON.stringify({ status: "FAIL", errors: [{ code: "unhandled", message: String(err) }] }) + "\n",
    );
    process.exit(1);
  });
}
