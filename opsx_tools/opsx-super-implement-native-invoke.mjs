import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Super Implement native primitive subprocess dispatch (machine entrypoint).
 *
 * Invoked with stage write-guard hook; records invocation evidence for dispatch proof.
 * Acceptance fixture writes use OPSX_SUPER_IMPLEMENT_NATIVE_ACCEPTANCE=1 only.
 */
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  APPLY_DESIGN_REVIEW_PASS,
  RED_PROVEN,
  SCAFFOLD_READY,
} from "./opsx-apply-pipeline-identity.mjs";
import { STAGE_PERMITTED_WRITE_SURFACES } from "./opsx-super-implement-stage-policy.mjs";
import { runProductionPrimitiveHop } from "./opsx-super-implement-primitive-router.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const HOOK = join(TOOL_DIR, "opsx-stage-write-guard-hook.mjs");
const SELF = join(TOOL_DIR, "opsx-super-implement-native-invoke.mjs");

function acceptanceModeEnabled() {
  return process.env.OPSX_SUPER_IMPLEMENT_NATIVE_ACCEPTANCE === "1";
}

function runAcceptanceStageHandler(repoRoot, envelope) {
  const { change_root: changeRoot, stage, slice_id: sliceId } = envelope;
  const base = resourcePath(repoRoot, changeRoot);
  if (stage === "APPLY_DESIGN_REVIEW") {
    mkdirSync(join(base, "apply-design-review"), { recursive: true });
    writeFileSync(
      join(base, "apply-design-review/APPLY-DESIGN-REVIEW.json"),
      JSON.stringify({
        status: APPLY_DESIGN_REVIEW_PASS,
        apply_design_identity: { planning_digest: envelope.planning_digest },
      }),
    );
    return { ok: true, code: "ACCEPTANCE_DESIGN_REVIEW_WRITTEN" };
  }
  if (stage === "APPLY_SCAFFOLD" && sliceId) {
    const dir = join(base, "apply-scaffold", sliceId);
    mkdirSync(dir, { recursive: true });
    writeFileSync(
      join(dir, "SCAFFOLD.json"),
      JSON.stringify({ status: SCAFFOLD_READY, slice_id: sliceId }),
    );
    return { ok: true, code: "ACCEPTANCE_SCAFFOLD_WRITTEN" };
  }
  if (stage === "APPLY_RED" && sliceId) {
    const dir = join(base, "apply-red", sliceId);
    mkdirSync(dir, { recursive: true });
    writeFileSync(
      join(dir, "RED.json"),
      JSON.stringify({
        status: RED_PROVEN,
        slice_id: sliceId,
        schema_version: 1,
        test_commands: ["poetry run pytest tests/unit/x.py::test_a"],
        command_executions: [
          {
            command: "poetry run pytest tests/unit/x.py::test_a",
            exit_code: 1,
            qualified: true,
          },
        ],
      }),
    );
    return { ok: true, code: "ACCEPTANCE_RED_WRITTEN" };
  }
  return { ok: false, code: "ACCEPTANCE_STAGE_UNSUPPORTED", stage };
}

/**
 * Child entry: perform one native stage hop under write guard (acceptance fixtures).
 */
export function runNativeDispatchInProcess(envelope) {
  if (acceptanceModeEnabled()) {
    return {
      ...runAcceptanceStageHandler(envelope.repo_root, envelope),
      dispatch_mode: "acceptance_fixture",
    };
  }
  const production = runProductionPrimitiveHop(envelope);
  return { ...production, dispatch_mode: "production_validator_cli" };
}

/**
 * Spawn guarded subprocess that runs this module as dispatch worker.
 */
export function invokeNativePrimitiveSubprocess(repoRoot, spec, { changeRoot } = {}) {
  const stage = spec.stage;
  const permitted = STAGE_PERMITTED_WRITE_SURFACES[stage] || [];
  const invocationId = spec.dispatch_id || spec.execution_id;
  const envelope = {
    repo_root: repoRoot,
    change_root: changeRoot,
    stage,
    slice_id: spec.slice_id ?? null,
    execution_id: spec.execution_id,
    dispatch_id: spec.dispatch_id,
    invocation_id: invocationId,
    primitive_command: spec.primitive_command,
    planning_digest: spec.input_revision?.planning_digest ?? null,
  };
  const tmpDir = resourcePath(repoRoot, "opsx", "tmp", "super-native-invoke");
  mkdirSync(tmpDir, { recursive: true });
  const specPath = join(tmpDir, `${invocationId}.json`);
  writeFileSync(specPath, `${JSON.stringify(envelope, null, 2)}\n`);
  const guardJson = JSON.stringify({
    repo_root: repoRoot,
    change_root: changeRoot,
    stage,
    permitted_globs: permitted,
    execution_id: spec.execution_id,
    invocation_id: invocationId,
  });
  const argv = [
    process.execPath,
    "--import",
    HOOK,
    SELF,
    "--dispatch-envelope",
    specPath,
  ];
  const proc = spawnSync(argv[0], argv.slice(1), {
    cwd: repoRoot,
    encoding: "utf8",
    env: {
      ...process.env,
      OPSX_STAGE_WRITE_GUARD_JSON: guardJson,
      OPSX_SUPER_IMPLEMENT_NATIVE_ACCEPTANCE:
        process.env.OPSX_SUPER_IMPLEMENT_NATIVE_ACCEPTANCE || "",
    },
  });
  let payload = null;
  try {
    payload = JSON.parse(proc.stdout || "{}");
  } catch {
    payload = { ok: false, code: "NATIVE_INVOKE_PARSE_FAILED", stderr: proc.stderr };
  }
  const stdoutSha = createHash("sha256")
    .update(proc.stdout || "", "utf8")
    .digest("hex");
  return {
    subprocess_invoked: true,
    transport: "native_subprocess",
    exit_code: proc.status,
    invocation_id: invocationId,
    execution_id: spec.execution_id,
    invoked_argv: argv,
    invoked_command: spec.primitive_command,
    stdout_sha256: stdoutSha,
    worker_payload: payload,
    ok: proc.status === 0 && payload?.ok === true,
    code: payload?.code || (proc.status === 0 ? "NATIVE_INVOKE_OK" : "NATIVE_INVOKE_FAILED"),
  };
}

function cliMain() {
  const args = process.argv.slice(2);
  const flag = args.indexOf("--dispatch-envelope");
  if (flag < 0) {
    process.stdout.write(JSON.stringify({ ok: false, code: "ENVELOPE_REQUIRED" }) + "\n");
    process.exit(1);
  }
  const path = resolve(args[flag + 1]);
  const envelope = JSON.parse(readFileSync(path, "utf8"));
  const result = runNativeDispatchInProcess(envelope);
  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exit(result.ok ? 0 : 1);
}

if (process.argv[1] && resolve(process.argv[1]) === SELF) {
  const isDispatchChild = process.argv.includes("--dispatch-envelope");
  if (isDispatchChild) {
    cliMain();
  }
}
