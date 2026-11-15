import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Native APPLY_IMPLEMENT continuation under Break Glass exception authority.
 */
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  evaluateBreakGlassImplementAuthority,
  loadApplyImplementSchema,
  validateApplyImplementArtifact,
} from "./opsx-apply-implement.mjs";
import { guardedWriteText } from "./opsx-break-glass-write-guard.mjs";
import { IMPLEMENTATION_WRITTEN } from "./opsx-apply-pipeline-identity.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const IMPLEMENT_CLI = join(TOOL_DIR, "opsx-apply-implement.mjs");

/**
 * Production write episode + native validator CLI + contract tests.
 */
export function executeNativeBreakGlassImplement({
  repoRoot,
  changeRoot,
  sliceId,
  grant,
  exceptionReceipt,
  productionRelPath,
  productionBody,
  contractTestRelPath,
  frozenTestIdentity = "frozen-test-identity-hermetic",
  redArtifact = null,
} = {}) {
  const authority = evaluateBreakGlassImplementAuthority({
    exceptionReceipt,
    grant,
    redArtifact,
  });
  if (!authority.ok) return authority;

  const w1 = guardedWriteText({
    grant,
    repoRoot,
    relPath: productionRelPath,
    content: productionBody,
  });
  if (!w1.ok) return w1;

  const relImplement = join(changeRoot, "apply-implement", sliceId, "IMPLEMENTATION.json");
  const artifact = {
    schema: "opsx.apply_implement",
    schema_version: 1,
    status: IMPLEMENTATION_WRITTEN,
    next_stage: "APPLY_GREEN",
    tests_modified: false,
    tests_executed: false,
    chat_history_required: false,
    production_paths_created: [productionRelPath],
    production_paths_modified: [],
    scaffold_identity: { status: "SCAFFOLD_READY" },
    frozen_test_content_identity: frozenTestIdentity,
    frozen_test_identity_before: frozenTestIdentity,
    frozen_test_identity_after: frozenTestIdentity,
    break_glass_exception: {
      exception_id: exceptionReceipt.exception_id,
      grant_id: exceptionReceipt.grant_id,
      authority: "CONTINUE_WITH_EXCEPTION",
      red_debt_remains: true,
      blocked_gate: grant.blocked_gate,
    },
  };
  const w2 = guardedWriteText({
    grant,
    repoRoot,
    relPath: relImplement,
    content: `${JSON.stringify(artifact, null, 2)}\n`,
  });
  if (!w2.ok) return w2;

  const artifactAbs = resourcePath(repoRoot, relImplement);
  const schema =
    loadApplyImplementSchema(repoRoot) || loadApplyImplementSchema(defaultRepoRootFromToolFile(import.meta.url));
  const inProcess = validateApplyImplementArtifact({
    artifact,
    schema,
    design: null,
    repoRoot,
    artifactAbsPath: artifactAbs,
  });
  if (!inProcess.ok) {
    return { ok: false, code: "NATIVE_IMPLEMENT_VALIDATION_FAILED", errors: inProcess.errors };
  }

  const cli = spawnSync(
    process.execPath,
    [IMPLEMENT_CLI, "--repo", repoRoot, artifactAbs],
    { encoding: "utf8", cwd: repoRoot },
  );
  let cliJson = null;
  try {
    cliJson = JSON.parse(cli.stdout || "{}");
  } catch {
    cliJson = { status: "FAIL", parse_error: true, stdout: cli.stdout, stderr: cli.stderr };
  }
  if (cli.status !== 0 || cliJson.status !== "PASS") {
    return {
      ok: false,
      code: "NATIVE_IMPLEMENT_CLI_REJECTED",
      cli_exit: cli.status,
      cli: cliJson,
    };
  }

  const workspaceRoot = defaultRepoRootFromToolFile(import.meta.url);
  const testAbs = existsSync(resourcePath(repoRoot, contractTestRelPath))
    ? resourcePath(repoRoot, contractTestRelPath)
    : resolve(workspaceRoot, contractTestRelPath);
  if (!existsSync(testAbs)) {
    return { ok: false, code: "CONTRACT_TEST_MISSING", path: contractTestRelPath };
  }
  const contractTestEnv = {
    ...process.env,
    BG_PILOT_PRODUCT_PATH: resourcePath(repoRoot, productionRelPath),
  };
  delete contractTestEnv.NODE_TEST_CONTEXT;
  const testRun = spawnSync(process.execPath, ["--test", testAbs], {
    encoding: "utf8",
    cwd: workspaceRoot,
    env: contractTestEnv,
  });
  const contract_test_receipt = {
    schema: "opsx.break_glass_contract_test_receipt",
    schema_version: 1,
    command: [process.execPath, "--test", contractTestRelPath],
    exit_code: testRun.status ?? 1,
    stdout_sha256: null,
    stderr_tail: String(testRun.stderr || "").slice(-500),
    minted_fake_red: false,
  };
  if (testRun.status !== 0) {
    return {
      ok: false,
      code: "CONTRACT_TEST_FAILED",
      contract_test_receipt,
      native_implement_cli: cliJson,
    };
  }

  return {
    ok: true,
    implementation_path: relImplement,
    production_path: productionRelPath,
    artifact,
    native_implement_cli: cliJson,
    contract_test_receipt,
    test_receipt: contract_test_receipt,
    red_debt_remains: true,
    authority: "BREAK_GLASS_NATIVE_IMPLEMENT",
  };
}
