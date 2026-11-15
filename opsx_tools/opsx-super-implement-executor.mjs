import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Super Implement → Cursor executor adapter contracts.
 *
 * Builds dispatch specs for native /opsx-apply-* primitives. Does not execute
 * product writes itself; the SDK worker runs the command contract.
 */
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  NATIVE_PRIMITIVE_BY_STAGE,
  computeSuperImplementExecutionIdentity,
  formatPrimitiveInvocation,
} from "./opsx-super-implement.mjs";
import { STAGE_PERMITTED_WRITE_SURFACES } from "./opsx-super-implement-stage-policy.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);

export const MODEL_MODE_INHERIT = "INHERIT";
export const MODEL_SELECTION_REASON = "OPSX_MODEL_DISPATCH_INHERIT_V1";

/**
 * V0 release: real Cursor SDK / cursor_executor subprocess transport is hard-off.
 * Recording adapter remains the acceptance path; no env bypass in V0.
 */
export const SUPER_IMPLEMENT_V0_LIVE_SDK_TRANSPORT_ENABLED = false;

/**
 * Fail-closed gate at the live transport boundary (not documentation-only).
 */
export function assertSuperImplementV0LiveSdkTransportAllowed() {
  if (SUPER_IMPLEMENT_V0_LIVE_SDK_TRANSPORT_ENABLED !== true) {
    return {
      ok: false,
      code: "LIVE_SDK_TRANSPORT_DISABLED_V0",
      live_sdk_hard_disabled: true,
      recovery_contract: {
        code: "LIVE_SDK_TRANSPORT_DISABLED_V0",
        next_action:
          "Super Implement V0 does not invoke cursor-sdk / cursor_executor live transport; use --adapter recording",
        resume_requires: ["v0_live_sdk_transport_unavailable"],
      },
    };
  }
  return { ok: true };
}

function blockedLiveSdkTransportResult(spec, { reconcile = false } = {}) {
  return {
    terminal_class: "BLOCKED",
    adapter_terminal: "BLOCKED",
    code: "LIVE_SDK_TRANSPORT_DISABLED_V0",
    message: reconcile
      ? "Super Implement V0 hard-disables live SDK reconcile transport"
      : "Super Implement V0 hard-disables real Cursor SDK subprocess dispatch",
    execution_id: spec?.execution_id || null,
    transport: "cursor-sdk",
    subprocess_invoked: false,
    live_sdk_hard_disabled: true,
  };
}

/** Process stages that mutate durable receipts (all split APPLY primitives). */
export const STAGE_WRITE_POLICY = Object.freeze({
  APPLY_DESIGN: "MUTATING",
  APPLY_DESIGN_REVIEW: "MUTATING",
  APPLY_SCAFFOLD: "MUTATING",
  APPLY_RED: "MUTATING",
  APPLY_IMPLEMENT: "MUTATING",
  APPLY_GREEN: "MUTATING",
});

/**
 * Map stage → slash command id (no leading slash).
 */
export function commandIdFromPrimitive(primitiveCommand) {
  const cmd = String(primitiveCommand || "").trim();
  if (!cmd.startsWith("/")) return null;
  return cmd.slice(1);
}

/**
 * Build Cursor executor request payload (JSON) for one primitive hop.
 */
export function buildCursorExecutorRequest({
  workspace = DEFAULT_ROOT,
  executionId,
  prompt,
  stage,
  primitiveCommand,
  changeId,
  sliceId = null,
  hopDigest = null,
  modelRoute = null,
  correlationId = null,
  timeoutSeconds = 900,
} = {}) {
  const commandId = commandIdFromPrimitive(primitiveCommand);
  if (!commandId) {
    throw new Error("primitiveCommand must be a /opsx-apply-* slash command");
  }
  const slashPrompt = prompt.startsWith("/") ? prompt : `/${prompt.replace(/^\//, "")}`;
  const route =
    modelRoute ||
    resolveInheritModelRouteFromEnv() ||
    defaultFixtureModelRoute();
  const effectiveModelId = route?.model_id || null;
  return {
    execution_id: executionId,
    correlation_id: correlationId || executionId,
    workspace: resolve(workspace),
    runtime: "LOCAL",
    model_route: route,
    prompt: slashPrompt,
    write_policy: STAGE_WRITE_POLICY[stage] || "MUTATING",
    timeout_seconds: timeoutSeconds,
    metadata: {
      super_implement: true,
      model_mode: MODEL_MODE_INHERIT,
      requested_model: "inherit",
      effective_model_id: effectiveModelId,
      effective_model_source: modelRoute ? "dispatch_spec" : "implicit_default",
      model_selection_reason: MODEL_SELECTION_REASON,
      stage,
      primitive_command: primitiveCommand,
      change_id: changeId,
      slice_id: sliceId,
      hop_digest: hopDigest,
      command_id: commandId,
      tier_substitution_forbidden: true,
      permitted_write_globs: STAGE_PERMITTED_WRITE_SURFACES[stage] || [],
      stage_write_boundary_enforcement: "metadata_only",
      slash_prompt_imitation: true,
    },
  };
}

export function defaultFixtureModelRoute() {
  return {
    model_id: "gpt-5.6-sol",
    params: { context: "272k", reasoning: "high", fast: "false" },
  };
}

export function resolveInheritModelRouteFromEnv() {
  const raw = process.env.SUPER_IMPLEMENT_RESOLVED_MODEL_ROUTE_JSON;
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Effective model route for dispatch (fail-closed for live when env unresolved).
 */
export function resolveEffectiveModelRoute({ allowFixtureFallback = true } = {}) {
  const fromEnv = resolveInheritModelRouteFromEnv();
  if (fromEnv) {
    return {
      ok: true,
      route: fromEnv,
      effective_source: "SUPER_IMPLEMENT_RESOLVED_MODEL_ROUTE_JSON",
    };
  }
  if (!allowFixtureFallback) {
    return {
      ok: false,
      code: "MODEL_ROUTE_UNRESOLVED_FOR_LIVE",
      tier_substitution_forbidden: true,
    };
  }
  return {
    ok: true,
    route: defaultFixtureModelRoute(),
    effective_source: "fixture_fallback_simulated_only",
  };
}

export function buildDispatchSpec({
  repoRoot = DEFAULT_ROOT,
  changeId,
  changeRoot = null,
  stage,
  sliceId,
  primitiveCommand,
  dispatchId,
  runSequence = 0,
  planningDigest,
  repositoryHead,
  modelRoute = null,
  effectiveModelSource = null,
} = {}) {
  const hop = computeSuperImplementExecutionIdentity({
    changeId,
    planningDigest,
    repositoryHead,
    stage,
    sliceId,
    runSequence,
  });
  const executionId = createHash("sha256")
    .update(
      `super-implement:${changeId}:${hop.hop_digest}:${dispatchId}`,
      "utf8",
    )
    .digest("hex")
    .slice(0, 40);
  const normalizedPrompt = formatPrimitiveInvocation(primitiveCommand, {
    changeId,
    sliceId,
  });
  const request = buildCursorExecutorRequest({
    workspace: repoRoot,
    executionId,
    prompt: normalizedPrompt,
    stage,
    primitiveCommand,
    changeId,
    sliceId,
    hopDigest: hop.hop_digest,
    correlationId: dispatchId,
    modelRoute,
  });
  if (effectiveModelSource) {
    request.metadata = {
      ...request.metadata,
      effective_model_source: effectiveModelSource,
    };
  }
  return {
    execution_id: executionId,
    dispatch_id: dispatchId,
    change_root: changeRoot,
    stage,
    slice_id: sliceId,
    primitive_command: primitiveCommand,
    prompt: normalizedPrompt,
    hop_identity: hop,
    input_revision: {
      planning_digest: planningDigest,
      repository_head: repositoryHead,
      hop_digest: hop.hop_digest,
    },
    cursor_request: request,
  };
}

/**
 * In-memory recording adapter for acceptance tests (not live SDK proof).
 */
export function createRecordingExecutorAdapter({ onExecute } = {}) {
  const calls = [];
  return {
    is_simulated: true,
    calls,
    async execute(spec) {
      calls.push({ ...spec, at: new Date().toISOString() });
      if (onExecute) {
        return await onExecute(spec);
      }
      return {
        terminal_class: "BLOCKED",
        adapter_terminal: "BLOCKED",
        code: "SIMULATED_NO_HANDLER",
        message: "recording adapter requires onExecute",
        execution_id: spec.execution_id,
      };
    },
    async reconcile(executionId) {
      const match = calls.find((c) => c.execution_id === executionId);
      if (!match) return null;
      return {
        terminal_class: "UNKNOWN",
        reconciliation_state: "INDETERMINATE",
        execution_id: executionId,
      };
    },
  };
}

/**
 * Subprocess adapter: invokes cursor_executor CLI (real transport; needs SDK auth for success).
 */
/**
 * V1 cursor-sdk adapter: subprocess transport only after V1 live readiness (caller must gate).
 */
export function createV1CursorSdkExecutorAdapter({
  repoRoot = DEFAULT_ROOT,
  pythonExecutable = process.env.SUPER_IMPLEMENT_PYTHON || "python3",
  readinessGate = null,
} = {}) {
  const inner = createCursorSdkExecutorAdapter({
    repoRoot,
    pythonExecutable,
    skipV0HardDisable: true,
  });
  return {
    is_simulated: false,
    transport: "cursor-sdk",
    v1_transport: true,
    live_sdk_hard_disabled: false,
    async execute(spec) {
      if (readinessGate) {
        const ready = readinessGate(spec);
        if (!ready.ok) {
          return {
            terminal_class: "BLOCKED",
            adapter_terminal: "BLOCKED",
            code: ready.code || "LIVE_EXECUTION_BLOCKED",
            blockers: ready.blockers,
            execution_id: spec?.execution_id,
            transport: "cursor-sdk",
            subprocess_invoked: false,
            live_ready: false,
          };
        }
      }
      return inner.execute(spec);
    },
    reconcile: inner.reconcile,
  };
}

export function createCursorSdkExecutorAdapter({
  repoRoot = DEFAULT_ROOT,
  pythonExecutable = process.env.SUPER_IMPLEMENT_PYTHON || "python3",
  ledgerRoot = null,
  skipV0HardDisable = false,
} = {}) {
  const executorPkg = resourcePath(repoRoot, "tools", "cursor_executor");
  return {
    is_simulated: false,
    transport: "cursor-sdk",
    live_sdk_hard_disabled:
      skipV0HardDisable !== true && SUPER_IMPLEMENT_V0_LIVE_SDK_TRANSPORT_ENABLED !== true,
    async execute(spec) {
      if (skipV0HardDisable !== true) {
        const transportGate = assertSuperImplementV0LiveSdkTransportAllowed();
        if (!transportGate.ok) {
          return blockedLiveSdkTransportResult(spec);
        }
      }
      const tmp = mkdtempSync(join(tmpdir(), "super-impl-exec-"));
      try {
        const reqPath = join(tmp, "request.json");
        writeFileSync(reqPath, `${JSON.stringify(spec.cursor_request, null, 2)}\n`);
        const proc = spawnSync(
          pythonExecutable,
          ["-m", "cursor_executor.cli", "execute", "--request", reqPath],
          {
            cwd: executorPkg,
            encoding: "utf8",
            env: { ...process.env, PYTHONPATH: executorPkg },
          },
        );
        let parsed = null;
        try {
          parsed = JSON.parse(proc.stdout || "{}");
        } catch {
          parsed = {
            terminal_class: "FAILED",
            errors: [{ message: proc.stderr || proc.stdout || "invalid json" }],
          };
        }
        return {
          ...parsed,
          exit_code: proc.status,
          execution_id: spec.execution_id,
          transport: "cursor_executor.cli",
        };
      } finally {
        try {
          rmSync(tmp, { recursive: true, force: true });
        } catch {
          /* ignore */
        }
      }
    },
    async reconcile(executionId) {
      if (skipV0HardDisable !== true) {
        const transportGate = assertSuperImplementV0LiveSdkTransportAllowed();
        if (!transportGate.ok) {
          return blockedLiveSdkTransportResult(
            { execution_id: executionId },
            { reconcile: true },
          );
        }
      }
      const proc = spawnSync(
        pythonExecutable,
        ["-m", "cursor_executor.cli", "reconcile", "--execution-id", executionId],
        {
          cwd: executorPkg,
          encoding: "utf8",
          env: { ...process.env, PYTHONPATH: executorPkg },
        },
      );
      try {
        return JSON.parse(proc.stdout || "null");
      } catch {
        return null;
      }
    },
  };
}

/**
 * Validate command contract file exists for planned primitive (local proof, no SDK).
 */
export function validateNativeCommandContractExists(repoRoot, primitiveCommand) {
  const id = commandIdFromPrimitive(primitiveCommand);
  if (!id) return { ok: false, code: "INVALID_PRIMITIVE" };
  const rel = join(".cursor", "commands", `${id}.md`);
  const abs = resourcePath(repoRoot, rel);
  if (!existsSync(abs)) {
    return { ok: false, code: "COMMAND_CONTRACT_MISSING", path: rel };
  }
  const bytes = readFileSync(abs);
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  return { ok: true, path: rel, contract_sha256: sha256 };
}
