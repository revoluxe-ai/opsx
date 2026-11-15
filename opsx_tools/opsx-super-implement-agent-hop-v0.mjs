/**
 * Super Implement Agent Hop V0 — one bounded Cursor SDK authoring hop in an isolated workspace.
 *
 * Controller flow: isolate → agent (SDK) → diff → promotion gate → canonical integrate → verify.
 * Does not attach to auto-loop yet; minimal executor for later sequencer integration.
 */
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { formatPrimitiveInvocation } from "./opsx-super-implement.mjs";
import { evaluateOperativeReceiptForStage, loadSuperImplementContext } from "./opsx-super-implement.mjs";
import { allowedPromotionPaths } from "./opsx-super-implement-dual-write-policy.mjs";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  buildGreenReceiptFromUpstream,
  buildImplementReceipt,
  buildOperativeRedReceipt,
  seedAgentHopFrozenTest,
  seedAgentHopProduct,
} from "./opsx-super-implement-agent-hop-fixture.mjs";
import {
  enforceAgentHopLiveRuntimeBoundary,
  scanWorkspaceEscapes,
  assertToolchainNotCanonicalSymlink,
} from "./opsx-super-implement-agent-hop-boundary.mjs";
import {
  acquireChangePromotionLock,
  releaseChangePromotionLock,
  resolveTransactionAtStart,
  snapshotCanonicalTargetRevision,
  executePromotionTransaction,
  buildPromotionOperations,
  evaluateTargetRevisionFresh,
} from "./opsx-super-implement-hop-transaction.mjs";
import { validateAgentHopOwnerGrant } from "./opsx-super-implement-agent-hop-owner.mjs";
import { spawnSdkInOsSandbox } from "./opsx-super-implement-agent-hop-os-enforcement.mjs";
import {
  assertAgentHopSubprocessEnvSecretSafe,
  buildAgentHopSdkSubprocessEnv,
} from "./opsx-super-implement-agent-hop-subprocess-env.mjs";
import {
  createIsolatedAgentWorkspace,
  snapshotWorkspaceContent,
} from "./opsx-super-implement-isolated-workspace.mjs";
import { diffIsolatedWorkspace, evaluatePromotionGate } from "./opsx-super-implement-hop-promotion.mjs";
import { STAGE_DUAL_WRITE_SURFACES } from "./opsx-super-implement-dual-write-policy.mjs";
import { resolveEffectiveModelRoute } from "./opsx-super-implement-executor.mjs";
import { computeFrozenTestContentIdentity } from "./opsx-frozen-test-content-identity.mjs";
import { AGENT_HOP_TEST_REL } from "./opsx-super-implement-agent-hop-fixture.mjs";
import { runFrozenRedCommand } from "./opsx-apply-green.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);

export const AGENT_HOP_SCHEMA = "opsx.super_implement_agent_hop_result";
export const AGENT_HOP_SCHEMA_VERSION = 1;

export function buildAgentHopContractPrompt(spec) {
  const invocation = formatPrimitiveInvocation(spec.primitive_command, {
    changeId: spec.change_id,
    sliceId: spec.slice_id,
  });
  return [
    "# OPSX Super Implement Agent Hop V0",
    "",
    "You are executing ONE bounded split-APPLY primitive inside an isolated workspace.",
    "Do not push, merge, deploy, or write outside the stage write surfaces.",
    "",
    `Primitive: ${spec.primitive_command}`,
    `Invocation line (semantic reference): ${invocation}`,
    `Stage: ${spec.stage}`,
    `Slice: ${spec.slice_id ?? "(none)"}`,
    `Change root: ${spec.change_root}`,
    `Execution id: ${spec.execution_id}`,
    "",
    "Author real artifacts and operative receipts required for this stage.",
    "Do not fabricate PASS statuses without qualified evidence.",
  ].join("\n");
}

function flattenPermittedGlobs(changeRoot, stage) {
  const { change_local, repo_root } = allowedPromotionPaths(changeRoot, stage);
  return [...change_local, ...repo_root];
}

/**
 * Test-only authoring simulator (not production SDK). Writes stage-shaped artifacts with operative evidence.
 */
export async function runTestAuthoringSimulator(isolatedRoot, spec, options = {}) {
  const cr = spec.change_root;
  const base = join(isolatedRoot, cr);
  const slice = spec.slice_id;
  if (options.simulateUnauthorizedWrite) {
    mkdirSync(join(isolatedRoot, "app", "evil_hop"), { recursive: true });
    writeFileSync(join(isolatedRoot, "app", "evil_hop", "payload.mjs"), "export const x = 1;\n");
    return { ok: true, code: "TEST_AUTHOR_UNAUTHORIZED" };
  }
  if (spec.stage === "APPLY_RED" && slice) {
    const red = buildOperativeRedReceipt(isolatedRoot, slice, cr);
    const redDir = join(base, "apply-red", slice);
    mkdirSync(redDir, { recursive: true });
    writeFileSync(join(redDir, "RED.json"), `${JSON.stringify(red, null, 2)}\n`);
    return { ok: true, code: "TEST_AUTHOR_RED" };
  }
  if (spec.stage === "APPLY_IMPLEMENT" && slice) {
    const redPath = join(base, "apply-red", slice, "RED.json");
    let testPaths = [AGENT_HOP_TEST_REL];
    try {
      const red = JSON.parse(readFileSync(redPath, "utf8"));
      if (Array.isArray(red.test_paths) && red.test_paths.length) {
        testPaths = red.test_paths;
      }
    } catch {
      /* use default fixture path */
    }
    const identity = computeFrozenTestContentIdentity(isolatedRoot, testPaths);
    if (!identity.ok) {
      return { ok: false, code: "TEST_AUTHOR_IMPLEMENT_IDENTITY_FAILED", identity };
    }
    seedAgentHopProduct(isolatedRoot, { implemented: true });
    const impl = buildImplementReceipt(slice, identity.digest);
    const implDir = join(base, "apply-implement", slice);
    mkdirSync(implDir, { recursive: true });
    writeFileSync(join(implDir, "IMPLEMENTATION.json"), `${JSON.stringify(impl, null, 2)}\n`);
    return { ok: true, code: "TEST_AUTHOR_IMPLEMENT" };
  }
  if (spec.stage === "APPLY_GREEN" && slice) {
    const ctx = loadSuperImplementContext({ repoRoot: isolatedRoot, changeRoot: cr });
    if (!ctx.ok) return { ok: false, code: ctx.code };
    seedAgentHopProduct(isolatedRoot, { implemented: true });
    seedAgentHopFrozenTest(isolatedRoot);
    const cmd = `node --test ${AGENT_HOP_TEST_REL}`;
    const live = runFrozenRedCommand(cmd, { cwd: isolatedRoot, timeoutMs: 120000 });
    if (live.exit_code !== 0) {
      return { ok: false, code: "TEST_AUTHOR_GREEN_TEST_NOT_PASSING" };
    }
    const green = buildGreenReceiptFromUpstream(slice, ctx);
    const greenDir = join(base, "apply-green", slice);
    mkdirSync(greenDir, { recursive: true });
    writeFileSync(join(greenDir, "GREEN.json"), `${JSON.stringify(green, null, 2)}\n`);
    return { ok: true, code: "TEST_AUTHOR_GREEN" };
  }
  return { ok: false, code: "TEST_AUTHOR_STAGE_UNSUPPORTED" };
}

function invokeSdkAgentHop({ repoRoot, isolatedRoot, spec, modelRoute }) {
  const permitted = flattenPermittedGlobs(spec.change_root, spec.stage);
  const request = {
    execution_id: spec.execution_id,
    workspace: isolatedRoot,
    runtime: "LOCAL",
    model_route: modelRoute,
    prompt: buildAgentHopContractPrompt(spec),
    write_policy: "MUTATING",
    timeout_seconds: 900,
    metadata: {
      super_implement: true,
      super_implement_agent_hop_v0: true,
      isolated_workspace_root: isolatedRoot,
      slash_prompt_imitation: false,
      stage: spec.stage,
      primitive_command: spec.primitive_command,
      change_id: spec.change_id,
      slice_id: spec.slice_id,
      permitted_write_globs: permitted,
      stage_write_boundary_enforcement: "isolated_workspace_v0",
      tier_substitution_forbidden: true,
    },
  };
  const tmp = join(isolatedRoot, "opsx", "tmp", "agent-hop-request.json");
  mkdirSync(dirname(tmp), { recursive: true });
  writeFileSync(tmp, `${JSON.stringify(request, null, 2)}\n`);
  const subprocessEnv = buildAgentHopSdkSubprocessEnv({
    isolatedRoot,
    osSandboxEnforced: true,
  });
  const envSafe = assertAgentHopSubprocessEnvSecretSafe(subprocessEnv);
  if (!envSafe.ok) {
    return {
      sdk_invoked: false,
      terminal_class: "FAILED",
      code: envSafe.code,
      execution_id: spec.execution_id,
      os_sandbox_enforced: false,
      env_leak_key: envSafe.key,
    };
  }
  const sandbox = spawnSdkInOsSandbox({
    isolatedRoot,
    canonicalRepoRoot: repoRoot,
    command: process.env.SUPER_IMPLEMENT_PYTHON || "python3",
    commandArgs: ["-m", "cursor_executor.cli", "execute", "--request", tmp],
    env: subprocessEnv,
  });
  if (!sandbox.ok) {
    return {
      sdk_invoked: false,
      terminal_class: "FAILED",
      code: sandbox.code,
      execution_id: spec.execution_id,
      os_sandbox_enforced: false,
      proof: sandbox.proof,
    };
  }
  const proc = sandbox.proc;
  let parsed = null;
  try {
    parsed = JSON.parse(proc.stdout || "{}");
  } catch {
    parsed = { terminal_class: "FAILED", parse_error: true, stderr: proc.stderr };
  }
  return {
    sdk_invoked: true,
    terminal_class: parsed.terminal_class || "FAILED",
    exit_code: proc.status,
    execution_id: spec.execution_id,
    agent_hop_contract: true,
    os_sandbox_enforced: true,
    result: parsed,
  };
}

/**
 * Run one agent hop V0 with optional test simulator (CI) or SDK (Owner host).
 */
export async function runAgentHopV0({
  repoRoot = DEFAULT_ROOT,
  changeRoot,
  spec,
  modelRoute = null,
  useTestAuthoringSimulator = false,
  simulateUnauthorizedWrite = false,
  transactionTestHooks = null,
} = {}) {
  if (!spec?.stage || !spec?.primitive_command || !spec?.execution_id) {
    return { ok: false, code: "AGENT_HOP_SPEC_INVALID" };
  }
  const hooks =
    process.env.OPSX_AGENT_HOP_TEST_HOOKS === "1" ? transactionTestHooks : null;

  let routePolicy;
  if (useTestAuthoringSimulator) {
    routePolicy = modelRoute
      ? { ok: true, route: modelRoute, effective_source: "caller_pinned" }
      : resolveEffectiveModelRoute({ allowFixtureFallback: true });
    if (!routePolicy.ok) {
      return { ok: false, code: routePolicy.code || "MODEL_ROUTE_UNRESOLVED" };
    }
  }

  const txStart = resolveTransactionAtStart(repoRoot, changeRoot, spec.execution_id);
  if (!txStart.ok) {
    return { ok: false, code: txStart.code, ...txStart };
  }

  const targetRevision = snapshotCanonicalTargetRevision(repoRoot, changeRoot, spec.stage);

  const { isolatedRoot, hop_id: hopId } = createIsolatedAgentWorkspace({
    repoRoot,
    changeRoot,
    stage: spec.stage,
  });
  const toolchainIsolation = assertToolchainNotCanonicalSymlink(isolatedRoot, repoRoot);
  if (!toolchainIsolation.ok) {
    return { ok: false, code: toolchainIsolation.code, violations: toolchainIsolation.violations };
  }
  const beforeContent = snapshotWorkspaceContent(isolatedRoot, { changeRoot });

  let agentOutcome;
  if (!useTestAuthoringSimulator) {
    const boundary = enforceAgentHopLiveRuntimeBoundary({
      isolatedRoot,
      canonicalRepoRoot: repoRoot,
    });
    if (!boundary.ok) {
      return {
        ok: false,
        code: boundary.code,
        os_isolation: boundary.os_isolation,
        recovery_contract: boundary.recovery_contract,
      };
    }
    const permitted = flattenPermittedGlobs(changeRoot, spec.stage);
    const owner = validateAgentHopOwnerGrant({
      repoRoot,
      changeRoot,
      spec,
      permittedWriteGlobs: permitted,
      callerModelRoute: modelRoute,
    });
    if (!owner.ok) {
      return { ok: false, code: owner.code, owner_grant: owner };
    }
    routePolicy = {
      ok: true,
      route: owner.model_route,
      effective_source: owner.model_route_source,
      owner_grant_id: owner.grant_id,
    };
  }

  if (useTestAuthoringSimulator) {
    const sim = await runTestAuthoringSimulator(
      isolatedRoot,
      { ...spec, change_root: changeRoot },
      { simulateUnauthorizedWrite },
    );
    agentOutcome = {
      terminal_class: sim.ok ? "SUCCEEDED" : "FAILED",
      code: sim.code,
      test_simulator: true,
    };
    if (!sim.ok) {
      return {
        ok: false,
        code: sim.code,
        hop_id: hopId,
        isolated_root: isolatedRoot,
        agent: agentOutcome,
      };
    }
  } else {
    agentOutcome = invokeSdkAgentHop({
      repoRoot,
      isolatedRoot,
      spec: { ...spec, change_root: changeRoot },
      modelRoute: routePolicy.route,
    });
    if (agentOutcome.terminal_class !== "SUCCEEDED") {
      return {
        ok: false,
        code: "AGENT_HOP_SDK_FAILED",
        hop_id: hopId,
        isolated_root: isolatedRoot,
        agent: agentOutcome,
      };
    }
  }

  const escapeScan = scanWorkspaceEscapes(isolatedRoot, { changeRoot });
  if (!escapeScan.ok) {
    return {
      ok: false,
      code: escapeScan.code,
      hop_id: hopId,
      isolated_root: isolatedRoot,
      agent: agentOutcome,
      boundary: escapeScan,
    };
  }

  const diff = diffIsolatedWorkspace(isolatedRoot, beforeContent, changeRoot);
  const gate = evaluatePromotionGate({
    changeRoot,
    stage: spec.stage,
    changedRepoRelPaths: diff.changed_repo_rel,
    deletedRepoRelPaths: diff.deleted_repo_rel,
  });
  if (!gate.ok) {
    return {
      ok: false,
      code: gate.code,
      hop_id: hopId,
      isolated_root: isolatedRoot,
      agent: agentOutcome,
      promotion: gate,
      diff,
    };
  }

  const isolatedCtx = loadSuperImplementContext({ repoRoot: isolatedRoot, changeRoot });
  const isolatedOperative = isolatedCtx.ok
    ? evaluateOperativeReceiptForStage(spec.stage, spec.slice_id, isolatedCtx)
    : { authorized: false, code: isolatedCtx.code };
  if (!isolatedOperative.authorized) {
    return {
      ok: false,
      code: isolatedOperative.code || "ISOLATED_OPERATIVE_REJECTED",
      hop_id: hopId,
      isolated_root: isolatedRoot,
      agent: agentOutcome,
      diff,
      operative: isolatedOperative,
      pre_promotion: true,
    };
  }

  const changeLock = acquireChangePromotionLock(repoRoot, changeRoot);
  if (!changeLock.ok) {
    return {
      ok: false,
      code: changeLock.code,
      recovery_contract: changeLock.recovery_contract,
      hop_id: hopId,
      isolated_root: isolatedRoot,
    };
  }
  const revisionUnderLock = evaluateTargetRevisionFresh(
    repoRoot,
    changeRoot,
    spec.stage,
    targetRevision.digest,
  );
  if (!revisionUnderLock.ok) {
    releaseChangePromotionLock(repoRoot, changeRoot);
    return {
      ok: false,
      ...revisionUnderLock,
      hop_id: hopId,
      isolated_root: isolatedRoot,
    };
  }

  const operations = buildPromotionOperations(diff);
  let promotion;
  try {
    promotion = executePromotionTransaction({
      repoRoot,
      isolatedRoot,
      changeRoot,
      stage: spec.stage,
      executionId: spec.execution_id,
      hopId,
      operations,
      targetRevisionDigest: targetRevision.digest,
      prePromotionRevisionDigest: targetRevision.digest,
      modelRoute: routePolicy.route,
      sliceId: spec.slice_id,
      transactionTestHooks: hooks,
      canonicalOperativeVerify: () => {
        const ctx = loadSuperImplementContext({ repoRoot, changeRoot });
        return ctx.ok
          ? evaluateOperativeReceiptForStage(spec.stage, spec.slice_id, ctx)
          : { authorized: false, code: ctx.code };
      },
    });
  } finally {
    releaseChangePromotionLock(repoRoot, changeRoot);
  }

  if (!promotion.ok) {
    return {
      ok: false,
      code: promotion.code,
      hop_id: hopId,
      isolated_root: isolatedRoot,
      agent: agentOutcome,
      promotion,
      diff,
      operative: promotion.operative || isolatedOperative,
      target_revision: targetRevision,
    };
  }

  const operative = promotion.transaction?.canonical_operative || isolatedOperative;
  const verified = true;

  const hopResult = {
    schema: AGENT_HOP_SCHEMA,
    schema_version: AGENT_HOP_SCHEMA_VERSION,
    ok: verified,
    code: verified ? "AGENT_HOP_VERIFIED" : operative.code || "OPERATIVE_REJECTED",
    hop_id: hopId,
    execution_id: spec.execution_id,
    stage: spec.stage,
    slice_id: spec.slice_id,
    isolated_root: isolatedRoot,
    agent: agentOutcome,
    promotion,
    diff,
    operative,
    model_route: routePolicy.route,
    model_route_source: routePolicy.effective_source,
    write_surfaces: STAGE_DUAL_WRITE_SURFACES[spec.stage],
    target_revision: targetRevision,
    offline_simulator: useTestAuthoringSimulator,
    live_ready: false,
  };

  writeFileSync(
    join(isolatedRoot, "opsx-agent-hop-result.json"),
    `${JSON.stringify(hopResult, null, 2)}\n`,
  );

  return hopResult;
}

export function buildAgentHopSpec({
  changeId,
  changeRoot,
  stage,
  sliceId,
  primitiveCommand,
  dispatchId,
  planningDigest,
}) {
  const executionId = createHash("sha256")
    .update(`agent-hop:${changeId}:${dispatchId}:${stage}:${sliceId || ""}`, "utf8")
    .digest("hex")
    .slice(0, 40);
  return {
    execution_id: executionId,
    dispatch_id: dispatchId,
    change_id: changeId,
    change_root: changeRoot,
    stage,
    slice_id: sliceId,
    primitive_command: primitiveCommand,
    planning_digest: planningDigest,
  };
}
