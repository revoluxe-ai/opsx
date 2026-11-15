import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Production machine surface for Split APPLY primitives (native-tool transport).
 *
 * Maps each stage to the existing `opsx_tools/opsx-apply-*.mjs` validator CLI.
 * Does not simulate agent UX or fabricate receipts. Missing artifacts fail closed
 * with NATIVE_PRIMITIVE_AGENT_SURFACE_REQUIRED.
 */
import { existsSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { NATIVE_TOOL_SCRIPT_BY_COMMAND } from "./opsx-super-implement-stage-policy.mjs";

function sliceArtifact(prefix, sliceId, fileName) {
  return [prefix + sliceId + "/" + fileName];
}

/** Expected receipt paths relative to change root (validator input). */
export const MACHINE_ARTIFACT_REL_BY_STAGE = Object.freeze({
  APPLY_DESIGN: ["apply-design/APPLY-DESIGN.json"],
  APPLY_DESIGN_REVIEW: ["apply-design-review/APPLY-DESIGN-REVIEW.json"],
  APPLY_SCAFFOLD: (sliceId) => sliceArtifact("apply-scaffold/", sliceId, "SCAFFOLD.json"),
  APPLY_RED: (sliceId) => sliceArtifact("apply-red/", sliceId, "RED.json"),
  APPLY_IMPLEMENT: (sliceId) => sliceArtifact("apply-implement/", sliceId, "IMPLEMENT.json"),
  APPLY_GREEN: (sliceId) => sliceArtifact("apply-green/", sliceId, "GREEN.json"),
});

function artifactPathsForStage(changeRoot, stage, sliceId) {
  const rel = MACHINE_ARTIFACT_REL_BY_STAGE[stage];
  if (!rel) return [];
  const rels = typeof rel === "function" ? (sliceId ? rel(sliceId) : []) : rel;
  return rels.map((r) => join(changeRoot, r));
}

/**
 * Build argv for the native tool script (validator CLI — same as CI uses).
 */
export function buildProductionToolArgv(repoRoot, primitiveCommand, artifactAbs) {
  const rel = NATIVE_TOOL_SCRIPT_BY_COMMAND[primitiveCommand];
  if (!rel) return null;
  const script = resourcePath(repoRoot, rel);
  return { script, argv: [script, artifactAbs] };
}

/**
 * Run production machine hop: invoke native validator; no receipt synthesis.
 */
export function runProductionPrimitiveHop(envelope) {
  const {
    repo_root: repoRoot,
    change_root: changeRoot,
    stage,
    slice_id: sliceId,
    primitive_command: primitive,
  } = envelope;
  const paths = artifactPathsForStage(changeRoot, stage, sliceId);
  if (!paths.length) {
    return {
      ok: false,
      code: "NATIVE_PRIMITIVE_MACHINE_SURFACE_UNKNOWN",
      stage,
      recovery_contract: {
        code: "NATIVE_PRIMITIVE_MACHINE_SURFACE_UNKNOWN",
        next_action: "declare machine artifact mapping for stage",
        resume_requires: ["machine_surface_registry"],
      },
    };
  }
  const missing = paths.filter((rel) => !existsSync(resourcePath(repoRoot, rel)));
  if (missing.length) {
    return {
      ok: false,
      code: "NATIVE_PRIMITIVE_AGENT_SURFACE_REQUIRED",
      missing_artifacts: missing,
      recovery_contract: {
        code: "NATIVE_PRIMITIVE_AGENT_SURFACE_REQUIRED",
        next_action:
          "run the slash primitive in Cursor (or Owner machine) to materialize receipts; machine hop validates only",
        resume_requires: ["operative_receipt_present"],
      },
      machine_surface: "validator_cli",
      primitive_command: primitive,
    };
  }
  const artifactAbs = resourcePath(repoRoot, paths[0]);
  const built = buildProductionToolArgv(repoRoot, primitive, artifactAbs);
  if (!built) {
    return { ok: false, code: "NATIVE_TOOL_SCRIPT_UNKNOWN", primitive_command: primitive };
  }
  const proc = spawnSync(process.execPath, built.argv, {
    cwd: repoRoot,
    encoding: "utf8",
  });
  let parsed = null;
  try {
    parsed = JSON.parse(proc.stdout || "{}");
  } catch {
    parsed = { status: "FAIL", parse_error: true, stderr: proc.stderr };
  }
  const pass = proc.status === 0 && (parsed.status === "PASS" || parsed.ok === true);
  return {
    ok: pass,
    code: pass ? "NATIVE_PRIMITIVE_VALIDATOR_PASS" : "NATIVE_PRIMITIVE_VALIDATOR_FAIL",
    machine_surface: "validator_cli",
    primitive_command: primitive,
    tool_script: built.argv[0],
    artifact_path: paths[0],
    validator_exit_code: proc.status,
    validator_payload: parsed,
  };
}
